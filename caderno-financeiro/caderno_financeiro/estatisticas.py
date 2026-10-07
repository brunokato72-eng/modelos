"""Estatísticas locais — nenhuma chamada de IA acontece aqui.

Total do mês, quebra por categoria / forma de pagamento / conta, saldo e
comparação com o mês anterior. É o que aparece no `resumo` e o que alimenta o
contexto da conversa (sem custo).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import config
from .calculadora import executar_calculo
from .datas import (
    ciclo_anterior,
    ciclo_atual,
    ciclo_de,
    ciclo_por_rotulo,
    ciclo_seguinte,
    dias_entre,
    hoje_iso,
    mes_anterior,
    mes_atual,
    rotulo_ciclo,
    somar_dias,
)
from .valores import para_centavos, para_reais


def _quebra(lancamentos: Sequence[Dict[str, Any]], campo: str, tipo: str) -> List[Dict[str, Any]]:
    resultado = executar_calculo(
        {"operacao": "soma", "tipo": tipo, "agruparPor": campo}, lancamentos
    )
    return resultado.get("grupos", [])


def resumo_mensal(
    lancamentos: Sequence[Dict[str, Any]],
    rotulo: Optional[str] = None,
    *,
    incluir_comparacao: bool = True,
) -> Dict[str, Any]:
    """Resumo do ciclo de fatura (fecha dia 27) cujo rótulo (AAAA-MM do
    fechamento) é `rotulo` — padrão é o ciclo corrente."""
    inicio, fim = ciclo_por_rotulo(rotulo) if rotulo else ciclo_atual()
    mes = rotulo_ciclo(fim)
    do_ciclo = [e for e in lancamentos if inicio <= (e.get("data") or "") <= fim]

    despesas = [e for e in do_ciclo if e.get("tipo") == config.TIPO_DESPESA]
    receitas = [e for e in do_ciclo if e.get("tipo") == config.TIPO_RECEITA]
    total_despesas = sum(para_centavos(e.get("valor") or 0) for e in despesas)
    total_receitas = sum(para_centavos(e.get("valor") or 0) for e in receitas)

    resumo: Dict[str, Any] = {
        "mes": mes,
        "totalDespesas": para_reais(total_despesas),
        "totalReceitas": para_reais(total_receitas),
        "saldo": para_reais(total_receitas - total_despesas),
        "quantidadeLancamentos": len(do_ciclo),
        "quantidadeDespesas": len(despesas),
        "ticketMedioDespesa": para_reais(round(total_despesas / len(despesas))) if despesas else 0.0,
        "porCategoria": _quebra(do_ciclo, "categoria", config.TIPO_DESPESA),
        "porFormaPagamento": _quebra(do_ciclo, "formapagamento", config.TIPO_DESPESA),
        "porConta": _quebra(do_ciclo, "conta", config.TIPO_DESPESA),
        "receitasPorCategoria": _quebra(do_ciclo, "categoria", config.TIPO_RECEITA),
        "comprometidoParcelas": para_reais(
            sum(
                para_centavos(e.get("valor") or 0)
                for e in despesas
                if (e.get("totalParcelas") or 1) > 1
            )
        ),
    }

    if incluir_comparacao:
        inicio_ant, fim_ant = ciclo_anterior(inicio)
        despesas_anteriores = sum(
            para_centavos(e.get("valor") or 0)
            for e in lancamentos
            if inicio_ant <= (e.get("data") or "") <= fim_ant and e.get("tipo") == config.TIPO_DESPESA
        )
        variacao = None
        if despesas_anteriores:
            variacao = round((total_despesas - despesas_anteriores) / despesas_anteriores * 100, 1)
        resumo["mesAnterior"] = {
            "mes": rotulo_ciclo(fim_ant),
            "totalDespesas": para_reais(despesas_anteriores),
            "variacaoPercentual": variacao,
            "diferenca": para_reais(total_despesas - despesas_anteriores),
        }

    return resumo


def parcelas_futuras(lancamentos: Sequence[Dict[str, Any]], rotulo_referencia: Optional[str] = None,
                     meses: int = 6) -> List[Dict[str, Any]]:
    """Quanto já está comprometido nos próximos ciclos por conta de parcelamentos."""
    _, fim = ciclo_por_rotulo(rotulo_referencia) if rotulo_referencia else ciclo_atual()
    saida = []
    for _ in range(meses):
        inicio_alvo, fim = ciclo_seguinte(fim)
        total = sum(
            para_centavos(e.get("valor") or 0)
            for e in lancamentos
            if inicio_alvo <= (e.get("data") or "") <= fim
            and e.get("tipo") == config.TIPO_DESPESA
            and (e.get("totalParcelas") or 1) > 1
        )
        saida.append({"mes": rotulo_ciclo(fim), "totalParcelas": para_reais(total)})
    return saida


def progresso_orcamentos(
    lancamentos: Sequence[Dict[str, Any]],
    orcamentos: Sequence[Dict[str, Any]],
    rotulo: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Quanto já foi gasto em cada categoria com orçamento, no ciclo de fatura
    (fecha dia 27 — ver `datas.ciclo_de`), vs o limite."""
    inicio, fim = ciclo_por_rotulo(rotulo) if rotulo else ciclo_atual()
    gasto_por_categoria: Dict[str, int] = {}
    for lanc in lancamentos:
        data = lanc.get("data") or ""
        if lanc.get("tipo") != config.TIPO_DESPESA or not (inicio <= data <= fim):
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
def saude_financeira(lancamentos: Sequence[Dict[str, Any]], rotulo: Optional[str] = None) -> Dict[str, Any]:
    inicio, fim = ciclo_por_rotulo(rotulo) if rotulo else ciclo_atual()
    mes = rotulo_ciclo(fim)
    resumo = resumo_mensal(lancamentos, mes, incluir_comparacao=False)
    receitas, despesas = resumo["totalReceitas"], resumo["totalDespesas"]

    taxa_poupanca = round(resumo["saldo"] / receitas * 100, 1) if receitas else None

    totais_meses_anteriores = []
    inicio_alvo = inicio
    for _ in range(3):
        inicio_alvo, fim_alvo = ciclo_anterior(inicio_alvo)
        total = sum(
            para_centavos(e.get("valor") or 0)
            for e in lancamentos
            if inicio_alvo <= (e.get("data") or "") <= fim_alvo and e.get("tipo") == config.TIPO_DESPESA
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

def _media_historica_despesa_centavos(
    lancamentos: Sequence[Dict[str, Any]], inicio: str, categoria: Optional[str] = None,
    *, excluir_compromissos_certos: bool = False,
) -> Optional[int]:
    """Média dos últimos 3 ciclos de fatura ANTERIORES a `inicio` que tiveram
    gasto de verdade — pula ciclos vazios (relevante ao encadear vários ciclos
    futuros, onde os mais próximos ainda não têm nenhum lançamento real) em
    vez de parar nos 3 primeiros e arriscar não achar nenhum dado.

    `excluir_compromissos_certos=True` tira parcelas e "Dívidas" da média —
    usado em `projecao_compromissos_futuros`, que já soma esses compromissos
    certos de cada ciclo futuro à parte; sem isso, um valor entraria na média
    E de novo como "certo" do ciclo futuro, contando duas vezes."""
    totais = []
    inicio_alvo = inicio
    for _ in range(12):
        if len(totais) >= 3:
            break
        inicio_alvo, fim_alvo = ciclo_anterior(inicio_alvo)
        total = sum(
            para_centavos(e.get("valor") or 0)
            for e in lancamentos
            if inicio_alvo <= (e.get("data") or "") <= fim_alvo
            and e.get("tipo") == config.TIPO_DESPESA
            and e.get("categoria") != CATEGORIA_NEGOCIO
            and (categoria is None or e.get("categoria") == categoria)
            and not (excluir_compromissos_certos and _eh_compromisso_certo(e))
        )
        if total:
            totais.append(total)
    return round(sum(totais) / len(totais)) if totais else None


# gasto/dia_atual*dias_totais assume ritmo constante — funciona pra gasto do
# dia a dia (mercado, transporte), mas explode qualquer categoria concentrada
# num pagamento só (aluguel pago 1x, parcela de data fixa): no dia 5, pagar o
# aluguel inteiro e extrapolar como se fosse 1/5 de um gasto diário recorrente
# virava "vai gastar aluguel 6x no mês". Por isso o peso do ritmo sobe
# gradualmente com a fração do mês já decorrida (dia 5 de 31 = 16% ritmo, 84%
# média histórica); só no fim do mês o ritmo pesa 100% — ponto em que ritmo e
# total real já coincidem de qualquer forma. Shrinkage simples, não regressão.
def _projetar_com_shrinkage(
    ritmo_centavos: int, dia_atual: int, dias_totais: int, media_historica_centavos: Optional[int]
) -> int:
    if media_historica_centavos is None:
        return ritmo_centavos
    peso = dia_atual / dias_totais
    return round(peso * ritmo_centavos + (1 - peso) * media_historica_centavos)


# Capex de negócio (ex.: entrada de franquia) não é gasto de estilo de vida —
# some do cálculo de poupança projetada e dos sinais comportamentais, senão um
# aporte pontual de milhares de reais faz o score ficar sempre crítico até o
# capex terminar, o que não diz nada sobre o comportamento do usuário.
CATEGORIA_NEGOCIO = "Negócio"

# "Dívidas" aqui costuma ser financiamento/fatura com cronograma já conhecido
# (achado real: pagamentos decrescentes de R$1.139 a R$429 já cadastrados pros
# próximos meses, sem usar o mecanismo de parcela do app) — pra efeito de
# "o que já é certo" conta junto com parcela de verdade, não como gasto
# recorrente estimado (senão esse valor entra duas vezes: uma na média
# histórica, outra como compromisso certo do ciclo futuro específico).
CATEGORIA_DIVIDAS = "Dívidas"


def _eh_compromisso_certo(lancamento: Dict[str, Any]) -> bool:
    return (lancamento.get("totalParcelas") or 1) > 1 or lancamento.get("categoria") == CATEGORIA_DIVIDAS


def _dia_de_referencia(inicio: str, fim: str) -> tuple[str, int, int]:
    """(data de referência, dia do ciclo já decorrido, dias totais do ciclo).

    Pro ciclo corrente usa hoje; pro ciclo já fechado considera ele inteiro
    decorrido (não faz sentido "projetar" um ciclo que já acabou); pro ciclo
    que ainda nem começou, dia_atual=0 — sem ritmo nenhum pra usar, a
    projeção (via `_projetar_com_shrinkage`) parte 100% da média histórica,
    só respeitando o piso do que já estiver cadastrado como certo."""
    hoje = hoje_iso()
    dias_totais = dias_entre(inicio, fim) + 1
    if inicio <= hoje <= fim:
        return hoje, dias_entre(inicio, hoje) + 1, dias_totais
    if hoje > fim:
        return fim, dias_totais, dias_totais
    return somar_dias(inicio, -1), 0, dias_totais


def _receita_esperada_ciclo(lancamentos: Sequence[Dict[str, Any]], inicio: str) -> float:
    """Mediana das receitas totais dos últimos 3 ciclos de fatura ANTERIORES a
    `inicio` que tiveram receita de verdade — pula ciclos vazios, igual
    `_media_historica_despesa_centavos` (necessário ao encadear vários ciclos
    futuros em `projecao_compromissos_futuros`). Salário chega de uma vez só,
    então "projetar pelo ritmo" não funciona pra receita como funciona pra
    despesa."""
    totais = []
    inicio_alvo = inicio
    for _ in range(12):
        if len(totais) >= 3:
            break
        inicio_alvo, fim_alvo = ciclo_anterior(inicio_alvo)
        total = sum(
            para_centavos(e.get("valor") or 0)
            for e in lancamentos
            if inicio_alvo <= (e.get("data") or "") <= fim_alvo and e.get("tipo") == config.TIPO_RECEITA
        )
        if total:
            totais.append(total)
    if totais:
        totais.sort()
        meio = len(totais) // 2
        mediana = totais[meio] if len(totais) % 2 else (totais[meio - 1] + totais[meio]) / 2
        return para_reais(round(mediana))
    return 0.0


def projecao_categoria(
    lancamentos: Sequence[Dict[str, Any]],
    categoria: str,
    limite: Optional[float],
    inicio: str,
    fim: str,
) -> Dict[str, Any]:
    """Projeta o total do ciclo de fatura numa categoria pelo ritmo de gasto até
    agora (regra de três simples, não regressão de verdade — não precisa de mais).

    Parcelas futuras já cadastradas no ciclo (datadas depois de hoje) são gasto
    CERTO, não estimativa — contá-las dentro do "até agora" e multiplicar de
    novo pelo ritmo inflava a projeção em cima do que já era sabido."""
    data_referencia, dia_atual, dias_totais = _dia_de_referencia(inicio, fim)
    despesas_categoria_ciclo = [
        e for e in lancamentos
        if e.get("tipo") == config.TIPO_DESPESA
        and e.get("categoria") == categoria
        and inicio <= (e.get("data") or "") <= fim
    ]
    gasto_ate_hoje_centavos = sum(
        para_centavos(e.get("valor") or 0)
        for e in despesas_categoria_ciclo
        if (e.get("data") or "") <= data_referencia
    )
    gasto_total_conhecido_centavos = sum(
        para_centavos(e.get("valor") or 0) for e in despesas_categoria_ciclo
    )
    ritmo_centavos = (
        round(gasto_ate_hoje_centavos / dia_atual * dias_totais) if dia_atual else gasto_ate_hoje_centavos
    )
    media_historica_centavos = _media_historica_despesa_centavos(lancamentos, inicio, categoria)
    projecao_ritmo_centavos = _projetar_com_shrinkage(ritmo_centavos, dia_atual, dias_totais, media_historica_centavos)
    # a projeção nunca fica abaixo do que já é certo (parcelas futuras já cadastradas)
    projecao_centavos = max(projecao_ritmo_centavos, gasto_total_conhecido_centavos)
    limite_centavos = para_centavos(limite) if limite else None
    return {
        "categoria": categoria,
        "gasto": para_reais(gasto_total_conhecido_centavos),
        "projecao": para_reais(projecao_centavos),
        "limite": limite,
        # gasto > 0 exige pelo menos 1 lançamento real no ciclo — sem isso a
        # "projeção" é só a média histórica puxando sozinha, não comportamento de hoje.
        "vaiEstourar": (
            limite_centavos is not None
            and gasto_total_conhecido_centavos > 0
            and projecao_centavos > limite_centavos
        ),
    }


def projecao_orcamentos(
    lancamentos: Sequence[Dict[str, Any]],
    orcamentos: Sequence[Dict[str, Any]],
    rotulo: Optional[str] = None,
) -> List[Dict[str, Any]]:
    inicio, fim = ciclo_por_rotulo(rotulo) if rotulo else ciclo_atual()
    projecoes = [
        projecao_categoria(lancamentos, o["categoria"], o["limite"], inicio, fim) for o in orcamentos
    ]
    return sorted(projecoes, key=lambda p: (not p["vaiEstourar"], -p["projecao"]))


def projecao_poupanca(
    lancamentos: Sequence[Dict[str, Any]],
    rotulo: Optional[str] = None,
    meta_valor: Optional[float] = None,
) -> Dict[str, Any]:
    """Poupança projetada até o fim do ciclo de fatura vs a meta definida — é o
    elo entre o score do dia e "quanto eu quero economizar"."""
    inicio, fim = ciclo_por_rotulo(rotulo) if rotulo else ciclo_atual()
    data_referencia, dia_atual, dias_totais = _dia_de_referencia(inicio, fim)

    despesas_ciclo = [
        e for e in lancamentos
        if e.get("tipo") == config.TIPO_DESPESA
        and inicio <= (e.get("data") or "") <= fim
        and e.get("categoria") != CATEGORIA_NEGOCIO
    ]
    despesas_ate_agora_centavos = sum(
        para_centavos(e.get("valor") or 0) for e in despesas_ciclo if (e.get("data") or "") <= data_referencia
    )
    despesas_total_conhecido_centavos = sum(para_centavos(e.get("valor") or 0) for e in despesas_ciclo)
    ritmo_despesa_centavos = (
        round(despesas_ate_agora_centavos / dia_atual * dias_totais) if dia_atual else despesas_ate_agora_centavos
    )
    media_historica_centavos = _media_historica_despesa_centavos(lancamentos, inicio)
    despesa_projetada_ritmo_centavos = _projetar_com_shrinkage(
        ritmo_despesa_centavos, dia_atual, dias_totais, media_historica_centavos
    )
    # nunca abaixo do que já é certo (parcelas futuras já cadastradas no ciclo)
    despesa_projetada_centavos = max(despesa_projetada_ritmo_centavos, despesas_total_conhecido_centavos)
    receita_esperada = _receita_esperada_ciclo(lancamentos, inicio)
    poupanca_projetada = receita_esperada - para_reais(despesa_projetada_centavos)

    resultado: Dict[str, Any] = {
        "ciclo": rotulo_ciclo(fim),
        "inicio": inicio,
        "fim": fim,
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


def dre(
    lancamentos: Sequence[Dict[str, Any]],
    orcamentos: Sequence[Dict[str, Any]],
    rotulo: Optional[str] = None,
) -> Dict[str, Any]:
    """DRE pessoal do ciclo de fatura: receita bruta, despesas por categoria,
    resultado líquido e margem (resultado/receita) — "realizado" com o que já
    aconteceu, "projetado" usando a mesma projeção por categoria do score/meta.
    Capex de negócio sai à parte (não é despesa operacional)."""
    inicio, fim = ciclo_por_rotulo(rotulo) if rotulo else ciclo_atual()
    limite_por_categoria = {o["categoria"]: o["limite"] for o in orcamentos}

    despesas_ciclo = [
        e for e in lancamentos
        if e.get("tipo") == config.TIPO_DESPESA and inicio <= (e.get("data") or "") <= fim
    ]
    receitas_ciclo = [
        e for e in lancamentos
        if e.get("tipo") == config.TIPO_RECEITA and inicio <= (e.get("data") or "") <= fim
    ]
    capex_centavos = sum(
        para_centavos(e.get("valor") or 0) for e in despesas_ciclo if e.get("categoria") == CATEGORIA_NEGOCIO
    )
    receita_bruta_centavos = sum(para_centavos(e.get("valor") or 0) for e in receitas_ciclo)

    categorias_operacionais = [c for c in config.CATEGORIAS_DESPESA if c != CATEGORIA_NEGOCIO]

    despesas_realizado = []
    total_despesas_centavos = 0
    for categoria in categorias_operacionais:
        valor_centavos = sum(
            para_centavos(e.get("valor") or 0) for e in despesas_ciclo if e.get("categoria") == categoria
        )
        if valor_centavos:
            despesas_realizado.append({"categoria": categoria, "valor": para_reais(valor_centavos)})
            total_despesas_centavos += valor_centavos
    despesas_realizado.sort(key=lambda c: -c["valor"])

    resultado_liquido_centavos = receita_bruta_centavos - total_despesas_centavos
    margem = (
        round(resultado_liquido_centavos / receita_bruta_centavos * 100, 1) if receita_bruta_centavos else None
    )
    realizado = {
        "receitaBruta": para_reais(receita_bruta_centavos),
        "despesasPorCategoria": despesas_realizado,
        "totalDespesas": para_reais(total_despesas_centavos),
        "resultadoLiquido": para_reais(resultado_liquido_centavos),
        "margemPercentual": margem,
    }

    receita_esperada = _receita_esperada_ciclo(lancamentos, inicio)
    receita_esperada_centavos = para_centavos(receita_esperada)
    despesas_projetado = []
    total_projetado_centavos = 0
    for categoria in categorias_operacionais:
        projecao = projecao_categoria(lancamentos, categoria, limite_por_categoria.get(categoria), inicio, fim)
        if projecao["projecao"]:
            despesas_projetado.append({"categoria": categoria, "valor": projecao["projecao"]})
            total_projetado_centavos += para_centavos(projecao["projecao"])
    despesas_projetado.sort(key=lambda c: -c["valor"])

    resultado_liquido_projetado_centavos = receita_esperada_centavos - total_projetado_centavos
    margem_projetada = (
        round(resultado_liquido_projetado_centavos / receita_esperada_centavos * 100, 1)
        if receita_esperada_centavos else None
    )
    projetado = {
        "receitaBruta": receita_esperada,
        "despesasPorCategoria": despesas_projetado,
        "totalDespesas": para_reais(total_projetado_centavos),
        "resultadoLiquido": para_reais(resultado_liquido_projetado_centavos),
        "margemPercentual": margem_projetada,
    }

    return {
        "ciclo": rotulo_ciclo(fim),
        "inicio": inicio,
        "fim": fim,
        "realizado": realizado,
        "projetado": projetado,
        "capex": para_reais(capex_centavos),
    }


def projecao_compromissos_futuros(
    lancamentos: Sequence[Dict[str, Any]],
    rotulo_referencia: Optional[str] = None,
    meses: int = 6,
) -> List[Dict[str, Any]]:
    """Quanto deve sobrar em cada um dos próximos ciclos: receita esperada menos
    o que já é CERTO (parcelas cadastradas + "Dívidas", que na prática é
    financiamento com cronograma já conhecido) menos o gasto recorrente típico
    (média histórica do que não é compromisso certo — mercado, transporte,
    etc.) — não é nem só os compromissos certos (ignora o resto do gasto
    normal) nem o orçamento cheio (assume disciplina perfeita), é o que você
    realmente costuma gastar. `acumulado` é a reserva esperada somando os
    ciclos um a um."""
    _, fim = ciclo_por_rotulo(rotulo_referencia) if rotulo_referencia else ciclo_atual()
    saida = []
    acumulado = 0.0
    for _ in range(meses):
        inicio_alvo, fim = ciclo_seguinte(fim)
        compromissos_certos_centavos = sum(
            para_centavos(e.get("valor") or 0)
            for e in lancamentos
            if inicio_alvo <= (e.get("data") or "") <= fim
            and e.get("tipo") == config.TIPO_DESPESA
            and _eh_compromisso_certo(e)
        )
        compromissos_certos = para_reais(compromissos_certos_centavos)
        gasto_recorrente_centavos = _media_historica_despesa_centavos(
            lancamentos, inicio_alvo, excluir_compromissos_certos=True
        ) or 0
        gasto_recorrente = para_reais(gasto_recorrente_centavos)
        receita_esperada = _receita_esperada_ciclo(lancamentos, inicio_alvo)
        saldo_esperado = round(receita_esperada - compromissos_certos - gasto_recorrente, 2)
        acumulado = round(acumulado + saldo_esperado, 2)
        saida.append({
            "ciclo": rotulo_ciclo(fim),
            "inicio": inicio_alvo,
            "fim": fim,
            "receitaEsperada": receita_esperada,
            "gastoRecorrenteEsperado": gasto_recorrente,
            "compromissosCertos": compromissos_certos,
            "saldoEsperado": saldo_esperado,
            "acumulado": acumulado,
        })
    return saida


def _sinais_comportamentais(
    lancamentos: Sequence[Dict[str, Any]],
    orcamentos: Sequence[Dict[str, Any]],
    dia_iso: str,
    inicio_ciclo: str,
    fim_ciclo: str,
) -> Dict[str, Any]:
    """Os 4 sinais que definimos: transação grande, gatilho de impulso conhecido,
    mudança de padrão numa categoria historicamente baixa, e frequência alta no
    mesmo dia. Cada desconto vem com o alerta que explica de onde saiu."""
    mes = dia_iso[:7]
    limite_por_categoria = {o["categoria"]: para_centavos(o["limite"]) for o in orcamentos}

    gasto_ciclo_por_categoria: Dict[str, int] = {}
    for e in lancamentos:
        data = e.get("data") or ""
        if e.get("tipo") != config.TIPO_DESPESA or not (inicio_ciclo <= data <= fim_ciclo):
            continue
        gasto_ciclo_por_categoria[e["categoria"]] = gasto_ciclo_por_categoria.get(
            e["categoria"], 0
        ) + para_centavos(e.get("valor") or 0)

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
        restante = max(0, limite_centavos - gasto_ciclo_por_categoria.get(categoria, 0))
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
    meta de poupança do ciclo de fatura — é o que conecta o alerta do dia a dia
    com "quanto eu quero economizar", não só com limites de categoria isolados."""
    dia_iso = dia_iso or hoje_iso()
    inicio, fim = ciclo_de(dia_iso)
    rotulo = rotulo_ciclo(fim)

    comportamento = _sinais_comportamentais(lancamentos, orcamentos, dia_iso, inicio, fim)
    poupanca = projecao_poupanca(lancamentos, rotulo, meta_valor)
    categorias_em_risco = [c for c in projecao_orcamentos(lancamentos, orcamentos, rotulo) if c["vaiEstourar"]]

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
