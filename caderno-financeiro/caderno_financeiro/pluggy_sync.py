"""Sincronização automática via Meu Pluggy (Open Finance).

Pensado pra rodar 1x por dia sozinho (`caderno pluggy-sincronizar`, agendado
por timer systemd — mesmo padrão do `auto-atualizar.sh`). Passo a passo:

  1. autentica na Pluggy (client_id/secret -> api key)
  2. pra cada conexão (item) já consentida no Meu Pluggy, busca as contas
  3. pra cada conta, busca transações que ainda não foram importadas
     (`db.origem_ja_importada`, por id da transação — não por valor/data,
     que não segura sincronização diária recorrente)
  4. categoriza tudo em lote (`ia.categorizar_transacoes`)
  5. confiança alta -> grava direto; confiança baixa -> fila de revisão
     (nunca adivinha uma categoria que o usuário teria que corrigir sem notar)

Investimentos são sincronizados à parte (`sincronizar_investimentos`) — é
saldo/posição, não gasto, não faz sentido misturar com os lançamentos.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

from . import config, db, ia
from . import pluggy_cliente as pc
from .datas import hoje_iso

TIPOS_CONTA_CARTAO = {"CREDIT"}


def _mapear_forma_pagamento(conta_pluggy: Dict[str, Any], descricao: str) -> str:
    tipo_conta = str(conta_pluggy.get("type") or "").upper()
    if tipo_conta in TIPOS_CONTA_CARTAO:
        return config.FORMA_PADRAO  # "Cartão de crédito"
    descricao_maiuscula = descricao.upper()
    if "PIX" in descricao_maiuscula:
        return "Pix"
    if any(chave in descricao_maiuscula for chave in ("TED", "DOC", "TRANSFER")):
        return "Pix"
    return "Cartão de débito"


def _nome_conta(conta_pluggy: Dict[str, Any], item: Dict[str, Any]) -> str:
    nome = conta_pluggy.get("name") or conta_pluggy.get("marketingName")
    if nome:
        return str(nome)
    conector = item.get("connector") or {}
    return str(conector.get("name") or "Open Finance")


def _mapear_transacao(transacao: Dict[str, Any], conta_pluggy: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    valor = float(transacao.get("amount") or 0)
    descricao = str(transacao.get("description") or "").strip() or "(sem descrição)"
    return {
        "origemId": str(transacao["id"]),
        "data": str(transacao.get("date") or hoje_iso())[:10],
        "tipo": config.TIPO_RECEITA if valor > 0 else config.TIPO_DESPESA,
        "valor": round(abs(valor), 2),
        "descricao": descricao,
        "formaPagamento": _mapear_forma_pagamento(conta_pluggy, descricao),
        "conta": _nome_conta(conta_pluggy, item),
    }


def _nome_instituicao(item: Dict[str, Any]) -> str:
    conector = item.get("connector") or {}
    return str(conector.get("name") or item.get("id") or "desconhecida")


def sincronizar(conexao) -> Dict[str, Any]:
    """Roda a sincronização completa. Devolve um resumo (contadores + erros
    por conexão) — nunca levanta exceção por causa de UMA conexão com
    problema, pra não travar a sincronização das outras."""
    api_key = pc.obter_api_key()
    items = pc.listar_items(api_key)

    resultado: Dict[str, Any] = {
        "itemsProcessados": 0,
        "transacoesNovas": 0,
        "paraRevisao": 0,
        "erros": [],
    }

    for item in items:
        item_id = str(item["id"])
        instituicao = _nome_instituicao(item)
        db.registrar_conexao_pluggy(conexao, item_id, instituicao)

        try:
            _sincronizar_item(conexao, api_key, item, resultado)
        except pc.ErroPluggy as erro:
            resultado["erros"].append(f"{instituicao}: {erro}")
            continue

        db.atualizar_sincronizacao_pluggy(conexao, item_id)
        resultado["itemsProcessados"] += 1

    return resultado


def _sincronizar_item(conexao, api_key: str, item: Dict[str, Any], resultado: Dict[str, Any]) -> None:
    contas = pc.listar_contas(api_key, str(item["id"]))
    pendentes: List[Dict[str, Any]] = []

    for conta in contas:
        transacoes = pc.listar_transacoes(api_key, str(conta["id"]))
        for transacao in transacoes:
            if db.origem_ja_importada(conexao, "pluggy", str(transacao["id"])):
                continue
            pendentes.append(_mapear_transacao(transacao, conta, item))

    if not pendentes:
        return

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
            "origem": "pluggy",
            "origemId": transacao["origemId"],
            "revisaoPendente": 1 if incerto else 0,
        })

    db.inserir(conexao, linhas)
    resultado["transacoesNovas"] += len(linhas)
    resultado["paraRevisao"] += sum(1 for l in linhas if l["revisaoPendente"])


def sincronizar_investimentos(conexao) -> int:
    """Salva um snapshot do dia de cada posição de investimento. Devolve
    quantas posições foram gravadas."""
    api_key = pc.obter_api_key()
    items = pc.listar_items(api_key)
    hoje = hoje_iso()
    total = 0

    for item in items:
        item_id = str(item["id"])
        try:
            investimentos = pc.listar_investimentos(api_key, item_id)
        except pc.ErroPluggy:
            continue
        if not investimentos:
            continue

        instituicao = _nome_instituicao(item)
        posicoes = [
            {
                "id": f"{item_id}:{inv.get('id') or inv.get('name')}:{hoje}",
                "data": hoje,
                "itemId": item_id,
                "conta": instituicao,
                "tipo": str(inv.get("type") or ""),
                "nome": str(inv.get("name") or "Ativo sem nome"),
                "valor": float(inv.get("balance") if inv.get("balance") is not None else (inv.get("value") or 0)),
                "quantidade": inv.get("quantity"),
            }
            for inv in investimentos
        ]
        db.inserir_posicoes_investimento(conexao, posicoes)
        total += len(posicoes)

    return total
