"""Estatísticas locais — nenhuma chamada de IA acontece aqui.

Total do mês, quebra por categoria / forma de pagamento / conta, saldo e
comparação com o mês anterior. É o que aparece no `resumo` e o que alimenta o
contexto da conversa (sem custo).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import config
from .calculadora import executar_calculo
from .datas import mes_anterior, mes_atual
from .valores import para_centavos, para_reais


def _quebra(lancamentos: Sequence[Dict[str, Any]], campo: str, tipo: str) -> List[Dict[str, Any]]:
    resultado = executar_calculo(
        {"operacao": "soma", "tipo": tipo, "agruparPor": campo}, lancamentos
    )
    return resultado.get("grupos", [])


def resumo_mensal(
    lancamentos: Sequence[Dict[str, Any]],
    mes: Optional[str] = None,
    *,
    incluir_comparacao: bool = True,
) -> Dict[str, Any]:
    mes = mes or mes_atual()
    do_mes = [e for e in lancamentos if (e.get("data") or "")[:7] == mes]

    despesas = [e for e in do_mes if e.get("tipo") == config.TIPO_DESPESA]
    receitas = [e for e in do_mes if e.get("tipo") == config.TIPO_RECEITA]
    total_despesas = sum(para_centavos(e.get("valor") or 0) for e in despesas)
    total_receitas = sum(para_centavos(e.get("valor") or 0) for e in receitas)

    resumo: Dict[str, Any] = {
        "mes": mes,
        "totalDespesas": para_reais(total_despesas),
        "totalReceitas": para_reais(total_receitas),
        "saldo": para_reais(total_receitas - total_despesas),
        "quantidadeLancamentos": len(do_mes),
        "quantidadeDespesas": len(despesas),
        "ticketMedioDespesa": para_reais(round(total_despesas / len(despesas))) if despesas else 0.0,
        "porCategoria": _quebra(do_mes, "categoria", config.TIPO_DESPESA),
        "porFormaPagamento": _quebra(do_mes, "formapagamento", config.TIPO_DESPESA),
        "porConta": _quebra(do_mes, "conta", config.TIPO_DESPESA),
        "receitasPorCategoria": _quebra(do_mes, "categoria", config.TIPO_RECEITA),
        "comprometidoParcelas": para_reais(
            sum(
                para_centavos(e.get("valor") or 0)
                for e in despesas
                if (e.get("totalParcelas") or 1) > 1
            )
        ),
    }

    if incluir_comparacao:
        anterior = mes_anterior(mes)
        despesas_anteriores = sum(
            para_centavos(e.get("valor") or 0)
            for e in lancamentos
            if (e.get("data") or "")[:7] == anterior and e.get("tipo") == config.TIPO_DESPESA
        )
        variacao = None
        if despesas_anteriores:
            variacao = round((total_despesas - despesas_anteriores) / despesas_anteriores * 100, 1)
        resumo["mesAnterior"] = {
            "mes": anterior,
            "totalDespesas": para_reais(despesas_anteriores),
            "variacaoPercentual": variacao,
            "diferenca": para_reais(total_despesas - despesas_anteriores),
        }

    return resumo


def parcelas_futuras(lancamentos: Sequence[Dict[str, Any]], mes_referencia: Optional[str] = None,
                     meses: int = 6) -> List[Dict[str, Any]]:
    """Quanto já está comprometido nos próximos meses por conta de parcelamentos."""
    from .datas import somar_meses

    mes_referencia = mes_referencia or mes_atual()
    saida = []
    for passo in range(1, meses + 1):
        alvo = somar_meses(f"{mes_referencia}-01", passo)[:7]
        total = sum(
            para_centavos(e.get("valor") or 0)
            for e in lancamentos
            if (e.get("data") or "")[:7] == alvo
            and e.get("tipo") == config.TIPO_DESPESA
            and (e.get("totalParcelas") or 1) > 1
        )
        saida.append({"mes": alvo, "totalParcelas": para_reais(total)})
    return saida


def progresso_orcamentos(
    lancamentos: Sequence[Dict[str, Any]],
    orcamentos: Sequence[Dict[str, Any]],
    mes: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Quanto já foi gasto em cada categoria com orçamento, no mês, vs o limite."""
    mes = mes or mes_atual()
    gasto_por_categoria: Dict[str, int] = {}
    for lanc in lancamentos:
        if lanc.get("tipo") != config.TIPO_DESPESA or (lanc.get("data") or "")[:7] != mes:
            continue
        categoria = lanc.get("categoria")
        gasto_por_categoria[categoria] = gasto_por_categoria.get(categoria, 0) + para_centavos(lanc.get("valor") or 0)

    progresso = []
    for orcamento in orcamentos:
        limite_centavos = para_centavos(orcamento["limite"])
        gasto_centavos = gasto_por_categoria.get(orcamento["categoria"], 0)
        progresso.append({
            "categoria": orcamento["categoria"],
            "limite": para_reais(limite_centavos),
            "gasto": para_reais(gasto_centavos),
            "restante": para_reais(max(0, limite_centavos - gasto_centavos)),
            "percentual": round(gasto_centavos / limite_centavos * 100, 1) if limite_centavos else 0.0,
            "estourado": gasto_centavos > limite_centavos,
        })
    return sorted(progresso, key=lambda p: p["percentual"], reverse=True)


# Pontuação determinística de 0 a 100 — nenhuma chamada de IA. Começa em 100 e
# desconta por sinais concretos (poupança baixa/negativa, despesas subindo,
# parcelas futuras pesando demais na receita). É uma régua simples, não um
# veredito: os `alertas` explicam exatamente de onde veio cada desconto.
def saude_financeira(lancamentos: Sequence[Dict[str, Any]], mes: Optional[str] = None) -> Dict[str, Any]:
    from .datas import somar_meses

    mes = mes or mes_atual()
    resumo = resumo_mensal(lancamentos, mes, incluir_comparacao=False)
    receitas, despesas = resumo["totalReceitas"], resumo["totalDespesas"]

    taxa_poupanca = round(resumo["saldo"] / receitas * 100, 1) if receitas else None

    totais_meses_anteriores = []
    for passo in range(1, 4):
        alvo = somar_meses(f"{mes}-01", -passo)[:7]
        total = sum(
            para_centavos(e.get("valor") or 0)
            for e in lancamentos
            if (e.get("data") or "")[:7] == alvo and e.get("tipo") == config.TIPO_DESPESA
        )
        if total:
            totais_meses_anteriores.append(total)
    media_despesas_anteriores = (
        para_reais(round(sum(totais_meses_anteriores) / len(totais_meses_anteriores)))
        if totais_meses_anteriores else None
    )

    tendencia_despesas = None
    if media_despesas_anteriores:
        variacao = round((despesas - media_despesas_anteriores) / media_despesas_anteriores * 100, 1)
        tendencia_despesas = "subindo" if variacao > 5 else "caindo" if variacao < -5 else "estável"

    total_comprometido = sum(p["totalParcelas"] for p in parcelas_futuras(lancamentos, mes, meses=3))
    comprometimento_percentual = round(total_comprometido / (receitas * 3) * 100, 1) if receitas else None

    pontuacao = 100
    alertas: List[str] = []
    if taxa_poupanca is None:
        pontuacao -= 20
        alertas.append("sem receita registrada no mês — não dá pra calcular taxa de poupança")
    elif taxa_poupanca < 0:
        pontuacao -= 40
        alertas.append("gastando mais do que ganha esse mês")
    elif taxa_poupanca < 10:
        pontuacao -= 20
        alertas.append("taxa de poupança abaixo de 10%")

    if tendencia_despesas == "subindo":
        pontuacao -= 15
        alertas.append("despesas subindo em relação à média dos últimos meses")

    if comprometimento_percentual is not None and comprometimento_percentual > 30:
        pontuacao -= 15
        alertas.append("parcelas dos próximos meses comprometem mais de 30% da receita média")

    pontuacao = max(0, min(100, pontuacao))
    classificacao = "boa" if pontuacao >= 70 else "atenção" if pontuacao >= 40 else "crítica"

    return {
        "mes": mes,
        "pontuacao": pontuacao,
        "classificacao": classificacao,
        "taxaPoupanca": taxa_poupanca,
        "tendenciaDespesas": tendencia_despesas,
        "mediaDespesas3MesesAnteriores": media_despesas_anteriores,
        "comprometimentoFuturoPercentual": comprometimento_percentual,
        "alertas": alertas,
    }


def visao_geral(lancamentos: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Panorama curto (usado como contexto barato nas perguntas)."""
    meses = sorted({(e.get("data") or "")[:7] for e in lancamentos if e.get("data")})
    return {
        "totalLancamentos": len(lancamentos),
        "primeiroMes": meses[0] if meses else None,
        "ultimoMes": meses[-1] if meses else None,
        "categoriasUsadas": sorted({e.get("categoria") for e in lancamentos if e.get("categoria")}),
        "contasUsadas": sorted({e.get("conta") for e in lancamentos if e.get("conta")}),
        "formasUsadas": sorted(
            {e.get("formaPagamento") for e in lancamentos if e.get("formaPagamento")}
        ),
    }
