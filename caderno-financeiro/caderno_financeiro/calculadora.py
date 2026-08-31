"""Calculador determinístico.

Este módulo é o único lugar onde número vira número. A IA nunca soma, nunca
estima, nunca "lê a lista e arredonda": ela no máximo descreve um filtro, e é
`executar_calculo` que roda a agregação sobre os dados reais.

Porte da função de referência `executarCalculo` do protótipo, com três extensões
que não mudam a semântica original: filtro por intervalo de datas exato, filtro
por texto da descrição e agrupamento (`agruparPor`).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from .texto import normalizar
from .valores import para_centavos, para_reais

OPERACOES = (
    "soma",
    "media",
    "mediana",
    "desviopadrao",
    "minimo",
    "maximo",
    "contagem",
    "listar",
)

CAMPOS_AGRUPAMENTO = {
    "categoria": lambda e: e.get("categoria") or "(sem categoria)",
    "formapagamento": lambda e: e.get("formaPagamento") or "(sem forma)",
    "conta": lambda e: e.get("conta") or "(sem conta)",
    "tipo": lambda e: e.get("tipo") or "(sem tipo)",
    "mes": lambda e: (e.get("data") or "")[:7],
    "descricao": lambda e: e.get("descricao") or "(sem descrição)",
}

LIMITE_LISTAGEM = 20


def _norm_op(bruto) -> str:
    chave = normalizar(bruto).replace("_", "").replace("-", "").replace(" ", "")
    equivalentes = {
        "media": "media",
        "medias": "media",
        "avg": "media",
        "total": "soma",
        "somatorio": "soma",
        "sum": "soma",
        "desvio": "desviopadrao",
        "desviopadrao": "desviopadrao",
        "std": "desviopadrao",
        "min": "minimo",
        "max": "maximo",
        "count": "contagem",
        "quantidade": "contagem",
        "list": "listar",
        "lista": "listar",
    }
    chave = equivalentes.get(chave, chave)
    if chave not in OPERACOES:
        raise ValueError(f"operação não suportada: {bruto!r} (use uma de {', '.join(OPERACOES)})")
    return chave


def filtrar(filtro: Optional[Dict[str, Any]], lancamentos: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtro = filtro or {}
    tipo = filtro.get("tipo")
    categoria = filtro.get("categoria")
    forma = filtro.get("formaPagamento")
    conta = filtro.get("conta")
    mes_inicio = filtro.get("mesInicio")
    mes_fim = filtro.get("mesFim")
    data_inicio = filtro.get("dataInicio")
    data_fim = filtro.get("dataFim")
    descricao_contem = filtro.get("descricaoContem")
    alvo_descricao = normalizar(descricao_contem) if descricao_contem else None

    resultado = []
    for e in lancamentos:
        data = e.get("data") or ""
        if tipo and e.get("tipo") != tipo:
            continue
        if categoria and e.get("categoria") != categoria:
            continue
        if forma and e.get("formaPagamento") != forma:
            continue
        if conta and normalizar(e.get("conta")) != normalizar(conta):
            continue
        if mes_inicio and data[:7] < mes_inicio:
            continue
        if mes_fim and data[:7] > mes_fim:
            continue
        if data_inicio and data < data_inicio:
            continue
        if data_fim and data > data_fim:
            continue
        if alvo_descricao and alvo_descricao not in normalizar(e.get("descricao")):
            continue
        resultado.append(e)
    return resultado


def _agregar(operacao: str, filtrados: Sequence[Dict[str, Any]]):
    """Roda a operação sobre os valores. Somas em centavos inteiros, sem drift."""
    if operacao == "contagem":
        return len(filtrados)

    centavos = [para_centavos(e.get("valor") or 0) for e in filtrados]
    if not centavos:
        return 0 if operacao == "soma" else None

    if operacao == "soma":
        return para_reais(sum(centavos))
    if operacao == "media":
        return para_reais(round(sum(centavos) / len(centavos)))
    if operacao == "mediana":
        ordenados = sorted(centavos)
        meio = len(ordenados) // 2
        if len(ordenados) % 2:
            return para_reais(ordenados[meio])
        return para_reais(round((ordenados[meio - 1] + ordenados[meio]) / 2))
    if operacao == "desviopadrao":
        media = sum(centavos) / len(centavos)
        variancia = sum((c - media) ** 2 for c in centavos) / len(centavos)  # populacional
        return para_reais(round(math.sqrt(variancia)))
    if operacao == "minimo":
        return para_reais(min(centavos))
    if operacao == "maximo":
        return para_reais(max(centavos))
    raise ValueError(f"operação sem agregação definida: {operacao}")


def executar_calculo(
    filtro: Optional[Dict[str, Any]], lancamentos: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    """Aplica os filtros e devolve o resultado já calculado, pronto pra IA redigir.

    Devolve sempre `quantidade` (quantos lançamentos entraram na conta) pra que a
    resposta possa dizer em cima de quantos registros o número foi feito.
    """
    filtro = dict(filtro or {})
    operacao = _norm_op(filtro.get("operacao") or "soma")
    filtrados = filtrar(filtro, lancamentos)

    saida: Dict[str, Any] = {
        "operacao": operacao,
        "filtro": {k: v for k, v in filtro.items() if k != "operacao" and v not in (None, "")},
        "quantidade": len(filtrados),
    }

    if operacao == "listar":
        ordenados = sorted(filtrados, key=lambda e: para_centavos(e.get("valor") or 0), reverse=True)
        saida["itens"] = [
            {
                "data": e.get("data"),
                "tipo": e.get("tipo"),
                "categoria": e.get("categoria"),
                "valor": round(float(e.get("valor") or 0), 2),
                "formaPagamento": e.get("formaPagamento"),
                "conta": e.get("conta"),
                "descricao": e.get("descricao"),
                "parcela": (
                    f"{e.get('parcelaAtual')}/{e.get('totalParcelas')}"
                    if (e.get("totalParcelas") or 1) > 1
                    else None
                ),
            }
            for e in ordenados[:LIMITE_LISTAGEM]
        ]
        saida["truncado"] = len(ordenados) > LIMITE_LISTAGEM
        saida["totalPeriodo"] = para_reais(sum(para_centavos(e.get("valor") or 0) for e in filtrados))
        return saida

    saida["resultado"] = _agregar(operacao, filtrados)

    agrupar_por = filtro.get("agruparPor")
    if agrupar_por:
        chave = normalizar(agrupar_por).replace("_", "").replace(" ", "")
        if chave not in CAMPOS_AGRUPAMENTO:
            raise ValueError(
                f"agruparPor inválido: {agrupar_por!r} "
                f"(use {', '.join(sorted(CAMPOS_AGRUPAMENTO))})"
            )
        extrair = CAMPOS_AGRUPAMENTO[chave]
        baldes: Dict[str, List[Dict[str, Any]]] = {}
        for e in filtrados:
            baldes.setdefault(extrair(e), []).append(e)
        grupos = [
            {
                "grupo": nome,
                "quantidade": len(itens),
                "resultado": _agregar(operacao, itens),
            }
            for nome, itens in baldes.items()
        ]
        grupos.sort(key=lambda g: (g["resultado"] is None, -(g["resultado"] or 0)))
        saida["grupos"] = grupos
    return saida
