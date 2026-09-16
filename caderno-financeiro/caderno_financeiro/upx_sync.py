"""Sincronização automática via UPX Financial (Open Finance/Plaid).

Diferente da Pluggy: aqui não existe client_id/secret salvo no projeto. A
autorização é feita 1x pelo usuário direto em claude.ai (Configurações >
Conectores > adicionar "UPX Financial"), fica presa à conta da assinatura, e
qualquer `claude -p` rodado por esse usuário já enxerga as contas conectadas
— sem nenhuma credencial pra configurar na VPS.

O acesso aos dados passa pelo Claude (as ferramentas são MCP, não uma API
REST que dá pra chamar direto), mas cada chamada pede UMA ferramenta com
argumentos exatos e o resultado bruto é lido direto do stream de eventos do
Claude Code (`ia.chamar_ferramenta_unica`) — o Claude nunca resume, nunca
cruza conta com transação, nunca decide categoria. Isso é código
determinístico aqui embaixo (`_listar_contas`, o loop de paginação, o filtro
de pendências) — a única exceção é `ia.categorizar_transacoes`, que já é assim
também na sincronização da Pluggy.

Isso substitui um design anterior em que um único prompt pedia ao Claude pra
"buscar todas as páginas, cruzar com as contas e devolver tudo agregado no
final": funcionava para poucas transações, mas numa janela de 90 dias já
levava 831s (a IA reprocessando, via cache, o histórico de tool-results
acumulado a cada nova página) e em janelas maiores estourava o teto de
tokens de saída do modelo antes de terminar de escrever o JSON final —
sincronização inteira falhava mesmo tendo os dados em mãos. Paginando pelo
lado do Python, cada chamada é isolada (sem histórico acumulado) e nunca
depende do modelo reescrever dados grandes como texto.

Investimentos são sincronizados à parte (`sincronizar_investimentos`) — é
saldo/posição, não gasto, não faz sentido misturar com os lançamentos.
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
# ferramenta nunca é chamada de fato.
FERRAMENTA_CONTAS = "mcp__claude_ai_UPX_Financial__finance_accounts_list"
FERRAMENTA_TRANSACOES = "mcp__claude_ai_UPX_Financial__finance_transactions_list"
FERRAMENTA_INVESTIMENTOS = "mcp__claude_ai_UPX_Financial__finance_investments_list"

# Cada chamada agora busca no máximo UMA página (ou a lista de contas/
# investimentos, que não pagina na prática) — bem mais rápido e previsível
# que o design antigo, então a folga generosa de antes não é mais necessária.
TIMEOUT_FERRAMENTA = 180

# Máximo permitido pela ferramenta é 500, mas o Claude Code trunca (persiste
# em arquivo à parte, fora do alcance do subprocess isolado) qualquer
# tool_result grande demais pra caber direto na resposta — testado na prática:
# page_size=100 já estoura (~54KB), page_size=50 cabe com folga (~25KB).
PAGE_SIZE = 50


def _listar_contas() -> Dict[str, Dict[str, str]]:
    """account_id -> {categoria, nome, instituicao}, pra cruzar com as
    transações depois (código puro, sem IA nenhuma nesse cruzamento)."""
    bruto = ia.chamar_ferramenta_unica(FERRAMENTA_CONTAS, {}, timeout=TIMEOUT_FERRAMENTA)
    contas = bruto.get("accounts") if isinstance(bruto, dict) else None
    mapa: Dict[str, Dict[str, str]] = {}
    for conta in contas or []:
        conta_id = conta.get("account_id")
        if not conta_id:
            continue
        mapa[conta_id] = {
            "categoria": conta.get("category") or "other",
            "nome": conta.get("display_label") or conta.get("name") or "Conta",
            "instituicao": conta.get("institution_name") or "UPX",
        }
    return mapa


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
    """Busca as transações de todas as contas/conexões no intervalo [desde,
    ate]. A paginação é controlada aqui (uma chamada isolada e rápida por
    página, via cursor) — não pedida ao modelo dentro de um único prompt."""
    mapa_contas = _listar_contas()

    brutas: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    pagina_num = 0
    while True:
        pagina_num += 1
        argumentos: Dict[str, Any] = {"from": desde, "to": ate, "page_size": PAGE_SIZE}
        if cursor:
            argumentos["cursor"] = cursor
        pagina = ia.chamar_ferramenta_unica(FERRAMENTA_TRANSACOES, argumentos, timeout=TIMEOUT_FERRAMENTA)
        transacoes_pagina = pagina.get("transactions") if isinstance(pagina, dict) else None
        if isinstance(transacoes_pagina, list):
            brutas.extend(transacoes_pagina)
        print(
            f"[upx] página {pagina_num}: {len(transacoes_pagina or [])} transação(ões)",
            file=sys.stderr,
        )

        paginacao = (pagina or {}).get("pagination") or {}
        if not paginacao.get("has_more"):
            break
        cursor = paginacao.get("next_cursor")
        if not cursor:
            break

    resultado = []
    for bruta in brutas:
        transaction_id = bruta.get("transaction_id")
        if not transaction_id:
            continue

        conta_id = bruta.get("account_id")
        info_conta = mapa_contas.get(conta_id, {})
        categoria_conta = info_conta.get("categoria", "other")

        # conta que não é cartão de crédito + pendente = descarta; qualquer
        # outra combinação = mantém (fatura de cartão demora a fechar, mas o
        # valor da compra já é definitivo mesmo pendente).
        if bruta.get("is_pending") and categoria_conta != "credit_card":
            continue

        descricao_bruta = str(bruta.get("description") or "").strip()
        # "Pagamento de fatura" (saída da conta corrente) e "Pagamento
        # recebido" na conta do próprio cartão de crédito (entrada que quita
        # o saldo devedor) são as duas pernas da MESMA transferência interna
        # entre contas do usuário — confirmado comparando valor e data:
        # sempre batem exatos. As compras que geraram a fatura já foram
        # importadas individualmente; contar isso de novo duplica a despesa
        # e ainda infla a receita do lado do cartão. Sem esse filtro, cada
        # fatura paga (todo mês) reaparece como pendência nova, porque o
        # transaction_id muda a cada ocorrência.
        if descricao_bruta == "Pagamento de fatura":
            continue
        if descricao_bruta == "Pagamento recebido" and categoria_conta == "credit_card":
            continue

        valor_bruto = bruta.get("amount")
        valor = valor_bruto.get("amount") if isinstance(valor_bruto, dict) else valor_bruto
        descricao = str(bruta.get("description") or "").strip() or "(sem descrição)"

        resultado.append({
            "transactionId": str(transaction_id),
            "instituicao": info_conta.get("instituicao", "UPX"),
            "contaCategoria": categoria_conta,
            "contaNome": info_conta.get("nome", "UPX"),
            "data": str(bruta.get("posted_date") or "")[:10],
            "tipo": config.TIPO_RECEITA if bruta.get("direction") == "inflow" else config.TIPO_DESPESA,
            "valor": abs(float(valor or 0)),
            "descricao": descricao,
        })
    return resultado


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
    quantas posições foram gravadas. Não pagina: a ferramenta de
    investimentos devolve a lista completa numa chamada só (sem
    `pagination`/`has_more` no retorno)."""
    bruto = ia.chamar_ferramenta_unica(FERRAMENTA_INVESTIMENTOS, {}, timeout=TIMEOUT_FERRAMENTA)
    investimentos_brutos = bruto.get("investments") if isinstance(bruto, dict) else None
    if not isinstance(investimentos_brutos, list) or not investimentos_brutos:
        return 0

    hoje = hoje_iso()
    posicoes = []
    for bruta in investimentos_brutos:
        holding_id = bruta.get("holding_id")
        if not holding_id:
            continue
        valor_bruto = bruta.get("value")
        valor = valor_bruto.get("amount") if isinstance(valor_bruto, dict) else valor_bruto
        instituicao = str(bruta.get("institution_name") or "UPX")
        posicoes.append({
            "id": f"upx:{holding_id}:{hoje}",
            "data": hoje,
            "itemId": instituicao,
            "conta": instituicao,
            "tipo": str(bruta.get("category") or bruta.get("asset_class") or ""),
            "nome": str(bruta.get("name") or "Ativo sem nome"),
            "valor": float(valor or 0),
            "quantidade": bruta.get("quantity"),
        })
    if not posicoes:
        return 0
    db.inserir_posicoes_investimento(conexao, posicoes)
    return len(posicoes)
