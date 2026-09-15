"""Sincronização automática via UPX Financial (Open Finance/Plaid).

Diferente da Pluggy: aqui não existe client_id/secret salvo no projeto. A
autorização é feita 1x pelo usuário direto em claude.ai (Configurações >
Conectores > adicionar "UPX Financial"), fica presa à conta da assinatura, e
qualquer `claude -p` rodado por esse usuário já enxerga as contas conectadas
— sem nenhuma credencial pra configurar na VPS.

O acesso aos dados passa pelo Claude (as ferramentas são MCP, não uma API
REST que dá pra chamar direto), mas o Claude aqui só tem UMA função: chamar a
ferramenta pedida e devolver os dados exatos que ela retornou, no formato do
schema. Ele nunca resume, nunca soma, nunca decide categoria — quem decide
categoria e se algo precisa de revisão continua sendo código determinístico
(`ia.categorizar_transacoes` + o limiar de confiança), exatamente como na
sincronização da Pluggy.

Passo a passo (mesmo padrão da Pluggy):
  1. busca transações novas de todas as contas conectadas (1 chamada, o
     Claude decide sozinho quantas vezes precisa chamar a ferramenta de
     transação por trás)
  2. filtra o que já foi importado antes (`db.origem_ja_importada`)
  3. categoriza em lote
  4. confiança alta -> grava direto; confiança baixa -> fila de revisão

Investimentos são sincronizados à parte (`sincronizar_investimentos`) — é
saldo/posição, não gasto, não faz sentido misturar com os lançamentos.

Usa MODELO_ANALISE (não MODELO_EXTRACAO/haiku) pra chamar as ferramentas
MCP: o passo tem várias etapas (listar contas, paginar transações, cruzar
account_id com a conta certa) e o haiku vinha devolvendo `{"transacoes":[]}`
sem de fato tentar — provavelmente não seguia o roteiro de múltiplas
chamadas de ferramenta.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any, Dict, List, Optional

from . import config, db, ia
from .datas import hoje_iso, somar_meses

# O conector é adicionado em claude.ai como "UPX Financial", mas o Claude
# Code expõe as ferramentas com o nome do servidor MCP tal como aparece em
# `claude mcp list` (ali: "claude.ai UPX Financial"), sanitizado — daí o
# prefixo `claude_ai_` aqui. Sem isso o nome não bate com --allowed-tools e a
# ferramenta nunca é chamada de fato (o Claude fica pedindo permissão, que
# nunca é respondida em modo headless, e a sincronização sempre volta vazia).
# Chamada lenta (várias idas e vindas: conexões, contas, transações
# paginadas por conexão) — o timeout padrão de 180s de ia.py é curto demais.
# Um teste real levou 831s só na etapa de transações (3 contas x 90 dias,
# várias páginas cada), por isso a folga generosa aqui.
TIMEOUT_SINCRONIZACAO = 1500

FERRAMENTA_CONEXOES = "mcp__claude_ai_UPX_Financial__finance_connections_list"
FERRAMENTA_CONTAS = "mcp__claude_ai_UPX_Financial__finance_accounts_list"
FERRAMENTA_TRANSACOES = "mcp__claude_ai_UPX_Financial__finance_transactions_list"
FERRAMENTA_INVESTIMENTOS = "mcp__claude_ai_UPX_Financial__finance_investments_list"

SISTEMA_UPX = (
    "Você tem acesso a ferramentas do UPX Financial (extrato bancário via Open "
    "Finance/Plaid). Sua única tarefa é chamar a ferramenta pedida — quantas "
    "vezes for preciso, uma por conta/conexão — e devolver os dados no formato "
    "pedido. Nunca resuma, nunca calcule total, nunca arredonde, nunca invente "
    "um campo que a ferramenta não devolveu (use null). O campo `valor` é "
    "sempre positivo (o `tipo` já diz se é despesa ou receita)."
)

_SCHEMA_TRANSACOES = {
    "type": "object",
    "properties": {
        "transacoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "transactionId": {"type": "string"},
                    "instituicao": {"type": "string"},
                    "contaCategoria": {
                        "type": "string",
                        "enum": ["checking", "savings", "credit_card", "investment", "loan", "other"],
                    },
                    "contaNome": {"type": "string"},
                    "data": {"type": "string"},
                    "tipo": {"type": "string", "enum": list(config.TIPOS)},
                    "valor": {"type": "number"},
                    "descricao": {"type": "string"},
                },
                "required": [
                    "transactionId", "instituicao", "contaCategoria", "contaNome",
                    "data", "tipo", "valor", "descricao",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["transacoes"],
    "additionalProperties": False,
}

_SCHEMA_INVESTIMENTOS = {
    "type": "object",
    "properties": {
        "posicoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "holdingId": {"type": "string"},
                    "instituicao": {"type": "string"},
                    "nome": {"type": "string"},
                    "tipo": {"type": "string"},
                    "valor": {"type": "number"},
                    "quantidade": {"type": ["number", "null"]},
                },
                "required": ["holdingId", "instituicao", "nome", "tipo", "valor", "quantidade"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["posicoes"],
    "additionalProperties": False,
}


def _mapear_forma_pagamento(conta_categoria: str, descricao: str) -> str:
    if conta_categoria == "credit_card":
        return config.FORMA_PADRAO  # "Cartão de crédito"
    descricao_maiuscula = descricao.upper()
    if "PIX" in descricao_maiuscula:
        return "Pix"
    if any(chave in descricao_maiuscula for chave in ("TED", "DOC", "TRANSFER")):
        return "Pix"
    return "Cartão de débito"


def buscar_transacoes_novas(desde: str, ate: str) -> List[Dict[str, Any]]:
    """Pede ao Claude pra puxar (via ferramenta MCP) as transações de todas as
    contas conectadas na UPX Financial, no intervalo [desde, ate]."""
    prompt = (
        f"1) Chame a ferramenta de listar contas da UPX Financial pra saber o "
        f"nome da instituição, a categoria (checking/savings/credit_card/"
        f"investment/loan/other) e um nome curto de cada conta (account_id).\n"
        f"2) Chame a ferramenta de listar transações pra TODAS as "
        f"contas/conexões ativas, com `from`/data inicial = {desde} e "
        f"`to`/data final = {ate} (AAAA-MM-DD, ambos inclusive — é IMPORTANTE "
        f"passar `to`, senão a ferramenta também devolve parcelas futuras de "
        f"cartão de crédito ainda não vencidas, inflando muito a paginação "
        f"sem necessidade, já que elas aparecerão por conta própria no mês "
        f"em que vencerem). A resposta pagina (campo `has_more`/`next_cursor`) "
        f"— chame de novo com o cursor até `has_more` ser falso, juntando "
        f"TODAS as páginas antes de responder.\n"
        f"3) Cruze o `account_id` de cada transação com a lista de contas do "
        f"passo 1 pra saber a categoria da conta dela.\n"
        f"4) Filtre: descarte transações com `is_pending: true`, EXCETO quando "
        f"a conta for `credit_card` — nessas, mantenha mesmo pendente (fatura "
        f"de cartão demora a fechar, mas o valor da compra já é definitivo). "
        f"Ou seja: conta que não é cartão de crédito + pendente = descarta; "
        f"qualquer outra combinação = mantém.\n"
        f"5) Pra cada transação que sobrou, devolva: `transactionId` (campo `transaction_id`), "
        f"`instituicao` (nome da instituição da conta), `contaCategoria` "
        f"(categoria da conta), `contaNome` (nome curto da conta), `data` "
        f"(campo `posted_date`, AAAA-MM-DD), `tipo` (Despesa se `direction` for "
        f"outflow, Receita se for inflow), `valor` (campo `amount.amount`, "
        f"sempre positivo) e `descricao` (campo `description`)."
    )
    resposta = ia.chamar(
        prompt,
        sistema=SISTEMA_UPX,
        modelo=config.MODELO_ANALISE,
        schema=_SCHEMA_TRANSACOES,
        ferramentas=[FERRAMENTA_CONEXOES, FERRAMENTA_CONTAS, FERRAMENTA_TRANSACOES],
        mcp_da_conta=True,
        timeout=TIMEOUT_SINCRONIZACAO,
    )
    print(f"[upx] resposta bruta do Claude: {ia.texto_da_resposta(resposta)[:500]!r}", file=sys.stderr)
    dados = ia.json_da_resposta(resposta)
    transacoes = dados.get("transacoes") if isinstance(dados, dict) else None
    return transacoes if isinstance(transacoes, list) else []


JANELA_MESES = 3  # ~90 dias — suficiente pra pegar qualquer transação pendente
# que só assentou depois; a deduplicação por origem_id garante que reimportar
# a mesma janela todo dia não gera lançamento repetido.


def sincronizar(conexao, *, desde: Optional[str] = None, ate: Optional[str] = None) -> Dict[str, Any]:
    """Roda a sincronização completa. Por padrão busca uma janela fixa de
    `JANELA_MESES` até hoje (não avança um "desde a última sincronização",
    porque uma transação pendente numa sincronização pode assentar (e só
    aparecer) dias depois — se a janela tivesse avançado, ela ficaria pra
    trás e nunca mais seria vista). Quem evita duplicar em cima disso é o
    `origem_ja_importada` por transação.

    `desde`/`ate` (AAAA-MM-DD) permitem sobrescrever a janela padrão — útil
    pra uma carga única de histórico mais antigo ou parcelas futuras já
    conhecidas, sem alterar o comportamento da sincronização diária."""
    hoje = hoje_iso()
    if desde is None:
        desde = somar_meses(hoje, -JANELA_MESES)
    if ate is None:
        ate = hoje

    brutas = buscar_transacoes_novas(desde, ate)
    print(f"[upx] {len(brutas)} transação(ões) bruta(s) recebida(s) entre {desde} e {ate}", file=sys.stderr)
    pendentes = []
    duplicadas = 0
    for bruta in brutas:
        try:
            id_origem = str(bruta["transactionId"])
        except (KeyError, TypeError):
            continue
        if db.origem_ja_importada(conexao, "upx", id_origem):
            continue
        data = str(bruta.get("data") or hoje_iso())[:10]
        valor = round(abs(float(bruta.get("valor") or 0)), 2)
        if db.existe_duplicata(conexao, data, valor):
            # já existe um lançamento (provavelmente digitado manualmente antes
            # de conectar o banco) com o mesmo valor e data — não duplica.
            duplicadas += 1
            continue
        descricao = str(bruta.get("descricao") or "").strip() or "(sem descrição)"
        pendentes.append({
            "origemId": id_origem,
            "data": data,
            "tipo": bruta.get("tipo") if bruta.get("tipo") in config.TIPOS else config.TIPO_DESPESA,
            "valor": valor,
            "descricao": descricao,
            "formaPagamento": _mapear_forma_pagamento(str(bruta.get("contaCategoria") or ""), descricao),
            "conta": str(bruta.get("contaNome") or bruta.get("instituicao") or "UPX"),
        })

    resultado: Dict[str, Any] = {"transacoesNovas": 0, "paraRevisao": 0, "duplicadas": duplicadas}
    if pendentes:
        classificacoes = ia.categorizar_transacoes(
            [{"descricao": t["descricao"], "valor": t["valor"], "tipo": t["tipo"]} for t in pendentes]
        )
        criado_em = db.agora()
        linhas = []
        for transacao, classificacao in zip(pendentes, classificacoes):
            confianca = classificacao.get("confianca", 0.0)
            incerto = confianca < config.PLUGGY_CONFIANCA_MINIMA
            linhas.append({
                "id": uuid.uuid4().hex,
                "data": transacao["data"],
                "tipo": transacao["tipo"],
                "categoria": classificacao.get("categoria", "Outros"),
                "valor": transacao["valor"],
                "valorTotal": transacao["valor"],
                "parcelaAtual": 1,
                "totalParcelas": 1,
                "formaPagamento": transacao["formaPagamento"],
                "conta": transacao["conta"],
                "descricao": transacao["descricao"],
                "criadoEm": criado_em,
                "grupoParcelamento": None,
                "origem": "upx",
                "origemId": transacao["origemId"],
                "revisaoPendente": 1 if incerto else 0,
            })
        db.inserir(conexao, linhas)
        resultado["transacoesNovas"] = len(linhas)
        resultado["paraRevisao"] = sum(1 for l in linhas if l["revisaoPendente"])

    return resultado


def sincronizar_investimentos(conexao) -> int:
    """Salva um snapshot do dia de cada posição de investimento. Devolve
    quantas posições foram gravadas."""
    prompt = (
        "Chame a ferramenta de listar investimentos da UPX Financial pra TODAS "
        "as contas/conexões de investimento ativas. Se a resposta paginar "
        "(`has_more`/`next_cursor`), busque todas as páginas antes de "
        "responder. Se precisar do nome da instituição de cada posição e ele "
        "não vier direto na ferramenta de investimentos, cruze com a "
        "ferramenta de listar contas pelo id da conta/conexão. Pra cada "
        "posição, devolva: um id da posição/holding, o nome da instituição, o "
        "nome do ativo, o tipo (ação, fundo, renda fixa, etc.), o valor atual "
        "e a quantidade (null se não se aplicar, ex.: renda fixa)."
    )
    resposta = ia.chamar(
        prompt,
        sistema=SISTEMA_UPX,
        modelo=config.MODELO_ANALISE,
        schema=_SCHEMA_INVESTIMENTOS,
        ferramentas=[FERRAMENTA_CONEXOES, FERRAMENTA_CONTAS, FERRAMENTA_INVESTIMENTOS],
        mcp_da_conta=True,
        timeout=TIMEOUT_SINCRONIZACAO,
    )
    dados = ia.json_da_resposta(resposta)
    brutas = dados.get("posicoes") if isinstance(dados, dict) else None
    if not isinstance(brutas, list) or not brutas:
        return 0

    hoje = hoje_iso()
    posicoes = []
    for bruta in brutas:
        try:
            holding_id = str(bruta["holdingId"])
        except (KeyError, TypeError):
            continue
        posicoes.append({
            "id": f"upx:{holding_id}:{hoje}",
            "data": hoje,
            "itemId": str(bruta.get("instituicao") or "upx"),
            "conta": str(bruta.get("instituicao") or "UPX"),
            "tipo": str(bruta.get("tipo") or ""),
            "nome": str(bruta.get("nome") or "Ativo sem nome"),
            "valor": float(bruta.get("valor") or 0),
            "quantidade": bruta.get("quantidade"),
        })
    if not posicoes:
        return 0
    db.inserir_posicoes_investimento(conexao, posicoes)
    return len(posicoes)
