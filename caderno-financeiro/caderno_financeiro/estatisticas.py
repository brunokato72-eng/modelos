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
