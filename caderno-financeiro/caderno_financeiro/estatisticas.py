"""Estatísticas locais — nenhuma chamada de IA acontece aqui.

Total do mês, quebra por categoria / forma de pagamento / conta, saldo e
comparação com o mês anterior. É o que aparece no `resumo` e o que alimenta o
contexto da conversa (sem custo).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import config
from .calculadora import executar_calculo
from .datas import dias_no_mes, hoje_iso, mes_anterior, mes_atual, somar_dias
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


# Contrapartes conhecidas por puxar compra por impulso (achado no histórico real:
# skins de jogo, assinaturas via checkout de terceiro cobradas na fatura da Apple).
GATILHOS_IMPULSO = ("apple.com/bill", "lastlink")

# Nos primeiros dias do mês, gasto/dia_atual*dias_totais explode qualquer
# transação isolada (R$959 num único dia 1 vira "projeção de R$29.739"). Até
# completar essa quantidade de dias, a projeção pelo ritmo é misturada com a
# média histórica da categoria (quanto menos dias, mais peso pra média) — é um
# shrinkage simples, não regressão de verdade, só pra não disparar alerta
# bobo por falta de amostra.
MIN_DIAS_PROJECAO = 5


def _media_historica_despesa_centavos(
    lancamentos: Sequence[Dict[str, Any]], mes: str, categoria: Optional[str] = None
) -> Optional[int]:
    totais = []
    alvo = mes
    for _ in range(3):
        alvo = mes_anterior(alvo)
        total = sum(
            para_centavos(e.get("valor") or 0)
            for e in lancamentos
            if (e.get("data") or "")[:7] == alvo
            and e.get("tipo") == config.TIPO_DESPESA
            and e.get("categoria") != CATEGORIA_NEGOCIO
            and (categoria is None or e.get("categoria") == categoria)
        )
        if total:
            totais.append(total)
    return round(sum(totais) / len(totais)) if totais else None


def _projetar_com_shrinkage(ritmo_centavos: int, dia_atual: int, media_historica_centavos: Optional[int]) -> int:
    if dia_atual >= MIN_DIAS_PROJECAO or media_historica_centavos is None:
        return ritmo_centavos
    peso = dia_atual / MIN_DIAS_PROJECAO
    return round(peso * ritmo_centavos + (1 - peso) * media_historica_centavos)


# Capex de negócio (ex.: entrada de franquia) não é gasto de estilo de vida —
# some do cálculo de poupança projetada e dos sinais comportamentais, senão um
# aporte pontual de milhares de reais faz o score ficar sempre crítico até o
# capex terminar, o que não diz nada sobre o comportamento do usuário.
CATEGORIA_NEGOCIO = "Negócio"


def _dia_de_referencia(mes: str) -> tuple[str, int, int]:
    """(data de referência, dia do mês já decorrido, dias totais do mês).

    Pra mês corrente usa hoje; pra mês fechado considera o mês inteiro decorrido
    (não faz sentido "projetar" um mês que já acabou)."""
    hoje = hoje_iso()
    dias_totais = dias_no_mes(mes)
    if hoje[:7] == mes:
        return hoje, int(hoje[8:10]), dias_totais
    return f"{mes}-{dias_totais:02d}", dias_totais, dias_totais


def _receita_esperada_mes(lancamentos: Sequence[Dict[str, Any]], mes: str) -> float:
    """Mediana das receitas totais dos últimos meses fechados — salário chega de
    uma vez só, então "projetar pelo ritmo" não funciona pra receita como funciona
    pra despesa."""
    totais = []
    alvo = mes
    for _ in range(3):
        alvo = mes_anterior(alvo)
        total = sum(
            para_centavos(e.get("valor") or 0)
            for e in lancamentos
            if (e.get("data") or "")[:7] == alvo and e.get("tipo") == config.TIPO_RECEITA
        )
        if total:
            totais.append(total)
    if totais:
        totais.sort()
        meio = len(totais) // 2
        mediana = totais[meio] if len(totais) % 2 else (totais[meio - 1] + totais[meio]) / 2
        return para_reais(round(mediana))
    receitas_do_mes = sum(
        para_centavos(e.get("valor") or 0)
        for e in lancamentos
        if (e.get("data") or "")[:7] == mes and e.get("tipo") == config.TIPO_RECEITA
    )
    return para_reais(receitas_do_mes)


def projecao_categoria(
    lancamentos: Sequence[Dict[str, Any]],
    categoria: str,
    limite: Optional[float],
    mes: str,
) -> Dict[str, Any]:
    """Projeta o total do mês numa categoria pelo ritmo de gasto até agora
    (regra de três simples, não regressão de verdade — não precisa de mais).

    Parcelas futuras já cadastradas no mês (datadas depois de hoje) são gasto
    CERTO, não estimativa — contá-las dentro do "até agora" e multiplicar de
    novo pelo ritmo inflava a projeção em cima do que já era sabido."""
    data_referencia, dia_atual, dias_totais = _dia_de_referencia(mes)
    despesas_categoria_mes = [
        e for e in lancamentos
        if e.get("tipo") == config.TIPO_DESPESA
        and e.get("categoria") == categoria
        and (e.get("data") or "")[:7] == mes
    ]
    gasto_ate_hoje_centavos = sum(
        para_centavos(e.get("valor") or 0) for e in despesas_categoria_mes if (e.get("data") or "") <= data_referencia
    )
    gasto_total_conhecido_centavos = sum(
        para_centavos(e.get("valor") or 0) for e in despesas_categoria_mes
    )
    ritmo_centavos = (
        round(gasto_ate_hoje_centavos / dia_atual * dias_totais) if dia_atual else gasto_ate_hoje_centavos
    )
    media_historica_centavos = _media_historica_despesa_centavos(lancamentos, mes, categoria)
    projecao_ritmo_centavos = _projetar_com_shrinkage(ritmo_centavos, dia_atual, media_historica_centavos)
    # a projeção nunca fica abaixo do que já é certo (parcelas futuras já cadastradas)
    projecao_centavos = max(projecao_ritmo_centavos, gasto_total_conhecido_centavos)
    limite_centavos = para_centavos(limite) if limite else None
    return {
        "categoria": categoria,
        "gasto": para_reais(gasto_total_conhecido_centavos),
        "projecao": para_reais(projecao_centavos),
        "limite": limite,
        # gasto > 0 exige pelo menos 1 lançamento real no mês — sem isso a "projeção"
        # é só a média histórica puxando sozinha, não comportamento de hoje.
        "vaiEstourar": (
            limite_centavos is not None
            and gasto_total_conhecido_centavos > 0
            and projecao_centavos > limite_centavos
        ),
    }


def projecao_orcamentos(
    lancamentos: Sequence[Dict[str, Any]],
    orcamentos: Sequence[Dict[str, Any]],
    mes: Optional[str] = None,
) -> List[Dict[str, Any]]:
    mes = mes or mes_atual()
    projecoes = [
        projecao_categoria(lancamentos, o["categoria"], o["limite"], mes) for o in orcamentos
    ]
    return sorted(projecoes, key=lambda p: (not p["vaiEstourar"], -p["projecao"]))


def projecao_poupanca(
    lancamentos: Sequence[Dict[str, Any]],
    mes: Optional[str] = None,
    meta_valor: Optional[float] = None,
) -> Dict[str, Any]:
    """Poupança projetada até o fim do mês vs a meta definida — é o elo entre o
    score do dia e "quanto eu quero economizar"."""
    mes = mes or mes_atual()
    data_referencia, dia_atual, dias_totais = _dia_de_referencia(mes)

    despesas_mes = [
        e for e in lancamentos
        if e.get("tipo") == config.TIPO_DESPESA
        and (e.get("data") or "")[:7] == mes
        and e.get("categoria") != CATEGORIA_NEGOCIO
    ]
    despesas_ate_agora_centavos = sum(
        para_centavos(e.get("valor") or 0) for e in despesas_mes if (e.get("data") or "") <= data_referencia
    )
    despesas_total_conhecido_centavos = sum(para_centavos(e.get("valor") or 0) for e in despesas_mes)
    ritmo_despesa_centavos = (
        round(despesas_ate_agora_centavos / dia_atual * dias_totais) if dia_atual else despesas_ate_agora_centavos
    )
    media_historica_centavos = _media_historica_despesa_centavos(lancamentos, mes)
    despesa_projetada_ritmo_centavos = _projetar_com_shrinkage(
        ritmo_despesa_centavos, dia_atual, media_historica_centavos
    )
    # nunca abaixo do que já é certo (parcelas futuras já cadastradas no mês)
    despesa_projetada_centavos = max(despesa_projetada_ritmo_centavos, despesas_total_conhecido_centavos)
    receita_esperada = _receita_esperada_mes(lancamentos, mes)
    poupanca_projetada = receita_esperada - para_reais(despesa_projetada_centavos)

    resultado: Dict[str, Any] = {
        "mes": mes,
        "receitaEsperada": receita_esperada,
        "despesaProjetada": para_reais(despesa_projetada_centavos),
        "poupancaProjetada": round(poupanca_projetada, 2),
    }
    if meta_valor:
        resultado["metaPoupanca"] = meta_valor
        resultado["diferencaParaMeta"] = round(poupanca_projetada - meta_valor, 2)
        resultado["aderenciaPercentual"] = round(poupanca_projetada / meta_valor * 100, 1)
        resultado["noCaminho"] = poupanca_projetada >= meta_valor
    return resultado


def _sinais_comportamentais(
    lancamentos: Sequence[Dict[str, Any]],
    orcamentos: Sequence[Dict[str, Any]],
    dia_iso: str,
) -> Dict[str, Any]:
    """Os 4 sinais que definimos: transação grande, gatilho de impulso conhecido,
    mudança de padrão numa categoria historicamente baixa, e frequência alta no
    mesmo dia. Cada desconto vem com o alerta que explica de onde saiu."""
    mes = dia_iso[:7]
    limite_por_categoria = {o["categoria"]: para_centavos(o["limite"]) for o in orcamentos}

    gasto_mes_por_categoria: Dict[str, int] = {}
    for e in lancamentos:
        if e.get("tipo") != config.TIPO_DESPESA or (e.get("data") or "")[:7] != mes:
            continue
        gasto_mes_por_categoria[e["categoria"]] = gasto_mes_por_categoria.get(e["categoria"], 0) + para_centavos(
            e.get("valor") or 0
        )

    despesas_do_dia = [
        e for e in lancamentos if e.get("tipo") == config.TIPO_DESPESA and e.get("data") == dia_iso
    ]

    pontuacao = 100
    alertas: List[str] = []

    # 1. transação grande: consumiu mais de 40% do que restava no orçamento da categoria
    for e in despesas_do_dia:
        categoria = e.get("categoria")
        limite_centavos = limite_por_categoria.get(categoria)
        if not limite_centavos:
            continue
        restante = max(0, limite_centavos - gasto_mes_por_categoria.get(categoria, 0))
        valor_centavos = para_centavos(e.get("valor") or 0)
        if restante > 0 and valor_centavos > 0.4 * restante:
            pontuacao -= 25
            alertas.append(
                f"gasto de {para_reais(valor_centavos):.2f} em {categoria} consumiu mais de 40% "
                f"do que restava no orçamento"
            )

    # 2. gatilhos de impulso conhecidos
    for e in despesas_do_dia:
        descricao = (e.get("descricao") or "").lower()
        if any(gatilho in descricao for gatilho in GATILHOS_IMPULSO):
            pontuacao -= 20
            alertas.append(f"gatilho de impulso conhecido: {e.get('descricao')}")
    ifood_hoje = sum(1 for e in despesas_do_dia if "ifood" in (e.get("descricao") or "").lower())
    if ifood_hoje >= 2:
        pontuacao -= 20
        alertas.append(f"{ifood_hoje}x iFood hoje")

    # 3. mudança de padrão: categoria historicamente pouco usada com 3+ lançamentos em <7 dias
    janela_inicio = somar_dias(dia_iso, -6)
    contagem_recente: Dict[str, int] = {}
    for e in lancamentos:
        if e.get("tipo") != config.TIPO_DESPESA or e.get("categoria") == CATEGORIA_NEGOCIO:
            continue
        data = e.get("data") or ""
        if janela_inicio <= data <= dia_iso:
            contagem_recente[e["categoria"]] = contagem_recente.get(e["categoria"], 0) + 1
    totais_por_mes: Dict[str, Dict[str, int]] = {}
    for e in lancamentos:
        if e.get("tipo") != config.TIPO_DESPESA or e.get("categoria") == CATEGORIA_NEGOCIO:
            continue
        m = (e.get("data") or "")[:7]
        if m >= mes:
            continue
        totais_por_mes.setdefault(m, {})
        totais_por_mes[m][e["categoria"]] = totais_por_mes[m].get(e["categoria"], 0) + 1
    for categoria, qtd_recente in contagem_recente.items():
        if qtd_recente < 3:
            continue
        historico = [totais.get(categoria, 0) for totais in totais_por_mes.values()]
        media_mensal = sum(historico) / len(historico) if historico else 0
        if media_mensal < qtd_recente:
            pontuacao -= 20
            alertas.append(
                f"{categoria} teve {qtd_recente} lançamentos nos últimos 7 dias — "
                f"média histórica é {media_mensal:.1f} por mês inteiro"
            )

    # 4. frequência alta no mesmo dia
    contagem_dia: Dict[str, int] = {}
    for e in despesas_do_dia:
        contagem_dia[e["categoria"]] = contagem_dia.get(e["categoria"], 0) + 1
    for categoria, qtd in contagem_dia.items():
        if qtd >= 3:
            pontuacao -= 15
            alertas.append(f"{qtd} transações em {categoria} hoje")

    return {"pontuacaoComportamento": max(0, pontuacao), "alertas": alertas}


def score_dia(
    lancamentos: Sequence[Dict[str, Any]],
    orcamentos: Sequence[Dict[str, Any]],
    meta_valor: Optional[float] = None,
    dia_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Score do dia (0-100): 60% comportamento (os 4 sinais), 40% aderência à
    meta de poupança do mês — é o que conecta o alerta do dia a dia com
    "quanto eu quero economizar", não só com limites de categoria isolados."""
    dia_iso = dia_iso or hoje_iso()
    mes = dia_iso[:7]

    comportamento = _sinais_comportamentais(lancamentos, orcamentos, dia_iso)
    poupanca = projecao_poupanca(lancamentos, mes, meta_valor)
    categorias_em_risco = [c for c in projecao_orcamentos(lancamentos, orcamentos, mes) if c["vaiEstourar"]]

    pontuacao_comportamento = comportamento["pontuacaoComportamento"]
    aderencia = poupanca.get("aderenciaPercentual")
    if aderencia is not None:
        pontuacao_meta = max(0, min(100, aderencia))
        pontuacao_final = round(0.6 * pontuacao_comportamento + 0.4 * pontuacao_meta)
    else:
        pontuacao_final = pontuacao_comportamento

    pontuacao_final = max(0, min(100, pontuacao_final))
    classificacao = (
        "tranquilo" if pontuacao_final >= 70 else "atenção" if pontuacao_final >= 40 else "crítico"
    )

    return {
        "data": dia_iso,
        "pontuacaoFinal": pontuacao_final,
        "classificacao": classificacao,
        "pontuacaoComportamento": pontuacao_comportamento,
        "alertasComportamento": comportamento["alertas"],
        "poupanca": poupanca,
        "categoriasEmRiscoDeEstourar": categorias_em_risco,
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
