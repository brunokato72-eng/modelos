import unittest
from unittest import mock

from caderno_financeiro import estatisticas


def _lanc(data, valor, categoria, tipo="Despesa", descricao="", forma_pagamento=""):
    return {
        "data": data,
        "valor": valor,
        "categoria": categoria,
        "tipo": tipo,
        "descricao": descricao,
        "formaPagamento": forma_pagamento,
    }


# A partir da conferência contra holerite real (ver conversa), o "mês" de um
# lançamento não é mais um ciclo fixo de 27 dias — é `estatisticas.mes_referencia`:
#   - Despesa fora do cartão de crédito / Receita que não é Salário: mês
#     calendário da própria data (1 a 28/30/31), sem deslocar nada.
#   - Receita "Salário": sempre o mês SEGUINTE ao do pagamento.
#   - Despesa no cartão de crédito: o mês em que a fatura é PAGA (mesmo
#     critério do salário — ver `TestMesReferencia`).
# Os testes abaixo usam janeiro/2026 (31 dias) como mês calendário de
# referência pros lançamentos "fora do cartão", que é o caso comum.


class TestMesReferencia(unittest.TestCase):
    def test_despesa_fora_do_cartao_usa_mes_calendario_da_data(self):
        lanc = _lanc("2026-01-15", 100, "Mercado")
        self.assertEqual(estatisticas.mes_referencia(lanc), "2026-01")

    def test_receita_que_nao_e_salario_usa_mes_calendario_da_data(self):
        lanc = _lanc("2026-01-15", 100, "Reembolso", tipo="Receita")
        self.assertEqual(estatisticas.mes_referencia(lanc), "2026-01")

    def test_salario_desloca_pro_mes_seguinte(self):
        # achado real, conferido contra holerite: adiantamento/saldo pagos em
        # julho são "o salário de agosto" (o dinheiro que financia agosto).
        adiantamento = _lanc("2026-07-15", 3636.0, "Salário", tipo="Receita")
        saldo = _lanc("2026-07-31", 2317.24, "Salário", tipo="Receita")
        self.assertEqual(estatisticas.mes_referencia(adiantamento), "2026-08")
        self.assertEqual(estatisticas.mes_referencia(saldo), "2026-08")

    def test_salario_em_dezembro_desloca_pra_janeiro_do_ano_seguinte(self):
        lanc = _lanc("2025-12-15", 3636.0, "Salário", tipo="Receita")
        self.assertEqual(estatisticas.mes_referencia(lanc), "2026-01")

    def test_excecao_manual_sobrepoe_a_regra_automatica(self):
        # 13º/férias não deslocam — ficam no mês em que foram pagos de
        # verdade, mas o banco não diferencia isso automaticamente (mesma
        # descrição genérica de sempre), então só dá pra marcar à mão.
        decimo_terceiro = _lanc("2025-11-28", 2000.0, "Salário", tipo="Receita")
        decimo_terceiro["mesReferenciaManual"] = "2025-11"
        self.assertEqual(estatisticas.mes_referencia(decimo_terceiro), "2025-11")

    def test_despesa_no_cartao_usa_mes_de_pagamento_da_fatura_nao_de_fechamento(self):
        # fatura fecha dia 27 — uma compra feita 10/07 cai na fatura que
        # fecha 27/07, paga em agosto (mesmo critério do salário: o mês cujo
        # dinheiro cobriu aquilo).
        compra = _lanc("2026-07-10", 50.0, "Compras", forma_pagamento="Cartão de crédito")
        self.assertEqual(estatisticas.mes_referencia(compra), "2026-08")

    def test_despesa_no_cartao_apos_fechamento_tambem_vai_pro_mes_seguinte(self):
        # compra em 28/07 já cai na fatura que fecha 27/08, paga em setembro.
        compra = _lanc("2026-07-28", 50.0, "Compras", forma_pagamento="Cartão de crédito")
        self.assertEqual(estatisticas.mes_referencia(compra), "2026-09")


class TestProjecaoCategoria(unittest.TestCase):
    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-10")
    def test_projeta_pelo_ritmo_de_gasto_ate_agora_sem_historico(self, _hoje):
        lancamentos = [_lanc("2026-01-05", 100, "Mercado")]
        # dia 10 de janeiro (31 dias): 100 em 10 dias -> ritmo 310; sem
        # histórico, shrinkage não entra, é ritmo puro.
        resultado = estatisticas.projecao_categoria(lancamentos, "Mercado", 400, "2026-01")
        self.assertEqual(resultado["gasto"], 100.0)
        self.assertEqual(resultado["projecao"], 310.0)
        self.assertFalse(resultado["vaiEstourar"])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-10")
    def test_sem_limite_nao_marca_estouro(self, _hoje):
        lancamentos = [_lanc("2026-01-05", 100, "Mercado")]
        resultado = estatisticas.projecao_categoria(lancamentos, "Mercado", None, "2026-01")
        self.assertFalse(resultado["vaiEstourar"])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-02-01")
    def test_mes_fechado_usa_o_mes_inteiro_sem_projetar(self, _hoje):
        lancamentos = [_lanc("2026-01-15", 500, "Mercado")]
        resultado = estatisticas.projecao_categoria(lancamentos, "Mercado", 400, "2026-01")
        self.assertEqual(resultado["projecao"], 500.0)
        self.assertTrue(resultado["vaiEstourar"])


class TestProjecaoComPoucosDias(unittest.TestCase):
    """Dia 1 do mês não pode projetar via ritmo puro — gasto/1*31 explode
    qualquer transação isolada. Tem que puxar pra média histórica."""

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-01")
    def test_dia_1_nao_explode_projecao_sem_historico(self, _hoje):
        lancamentos = [_lanc("2026-01-01", 1000, "Compras")]
        resultado = estatisticas.projecao_categoria(lancamentos, "Compras", 500, "2026-01")
        # sem histórico pra puxar, cai no ritmo puro mesmo (não tem o que fazer)
        self.assertEqual(resultado["projecao"], 31000.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-01")
    def test_dia_1_com_historico_fica_proximo_da_media(self, _hoje):
        lancamentos = [
            _lanc("2025-10-15", 500, "Compras"),
            _lanc("2025-11-15", 500, "Compras"),
            _lanc("2025-12-15", 500, "Compras"),
            _lanc("2026-01-01", 1000, "Compras"),
        ]
        resultado = estatisticas.projecao_categoria(lancamentos, "Compras", 500, "2026-01")
        # ritmo puro seria 31000; com peso baixo no dia 1 (1/31), a média
        # histórica (500) domina e a projeção fica bem mais baixa que isso
        self.assertAlmostEqual(resultado["projecao"], 1483.87, delta=1.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-01")
    def test_sem_gasto_real_no_mes_nao_marca_estouro_so_pela_media_historica(self, _hoje):
        lancamentos = [
            _lanc("2025-10-15", 2000, "Saúde"),
            _lanc("2025-11-15", 2000, "Saúde"),
            _lanc("2025-12-15", 2000, "Saúde"),
        ]
        resultado = estatisticas.projecao_categoria(lancamentos, "Saúde", 1100, "2026-01")
        self.assertEqual(resultado["gasto"], 0.0)
        self.assertFalse(resultado["vaiEstourar"])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-31")
    def test_fim_do_mes_ritmo_e_total_real_coincidem(self, _hoje):
        lancamentos = [
            _lanc("2025-12-15", 500, "Compras"),
            _lanc("2026-01-31", 3100, "Compras"),
        ]
        resultado = estatisticas.projecao_categoria(lancamentos, "Compras", 500, "2026-01")
        # último dia do mês: peso do ritmo é 100%, projeção = total real do mês
        self.assertEqual(resultado["projecao"], 3100.0)


class TestProjecaoDeMesFuturo(unittest.TestCase):
    """Mês que ainda nem começou: sem ritmo nenhum, a projeção por categoria
    tem que vir 100% da média histórica (mais o piso do que já é certo), não
    do que já está cadastrado nesse mês específico (que seria só o que for
    certo, subestimando o resto)."""

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-10")
    def test_mes_futuro_usa_media_historica_sem_ritmo(self, _hoje):
        lancamentos = [
            _lanc("2025-10-15", 900, "Compras"),
            _lanc("2025-11-15", 900, "Compras"),
            _lanc("2025-12-15", 900, "Compras"),
        ]
        resultado = estatisticas.projecao_categoria(lancamentos, "Compras", 500, "2026-03")
        self.assertEqual(resultado["gasto"], 0.0)
        self.assertEqual(resultado["projecao"], 900.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-10")
    def test_mes_futuro_respeita_piso_do_que_ja_e_certo(self, _hoje):
        lancamentos = [
            _lanc("2025-10-15", 100, "Compras"),
            _lanc("2025-11-15", 100, "Compras"),
            _lanc("2025-12-15", 100, "Compras"),
            # parcela já cadastrada pro mês futuro, maior que a média histórica
            _lanc("2026-03-05", 5000, "Compras"),
        ]
        resultado = estatisticas.projecao_categoria(lancamentos, "Compras", 500, "2026-03")
        self.assertEqual(resultado["gasto"], 5000.0)
        self.assertEqual(resultado["projecao"], 5000.0)


class TestProjecaoComParcelasFuturas(unittest.TestCase):
    """Parcela futura já cadastrada no mês é gasto CERTO, não estimativa — não
    pode entrar no "até agora" e ser multiplicada de novo pelo ritmo."""

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-05")
    def test_parcela_futura_nao_e_contada_duas_vezes_na_projecao(self, _hoje):
        lancamentos = [
            _lanc("2026-01-01", 100, "Compras"),
            _lanc("2026-01-03", 100, "Compras"),
            _lanc("2026-01-10", 400, "Compras"),  # parcela futura já cadastrada
        ]
        resultado = estatisticas.projecao_categoria(lancamentos, "Compras", 300, "2026-01")
        self.assertEqual(resultado["gasto"], 600.0)
        # ritmo: 200 até dia 5 -> 200/5*31 = 1240; piso é 600 (já conhecido) -> fica 1240
        self.assertEqual(resultado["projecao"], 1240.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-05")
    def test_projecao_nunca_fica_abaixo_do_total_ja_conhecido(self, _hoje):
        lancamentos = [_lanc("2026-01-20", 5000, "Compras")]  # só parcela futura, nada "até agora"
        resultado = estatisticas.projecao_categoria(lancamentos, "Compras", 300, "2026-01")
        self.assertEqual(resultado["gasto"], 5000.0)
        self.assertEqual(resultado["projecao"], 5000.0)


class TestProjecaoPoupanca(unittest.TestCase):
    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_usa_mediana_de_receita_dos_meses_anteriores_fechados(self, _hoje):
        # pagamento em julho = "salário de agosto", em junho = "de julho" etc.
        lancamentos = [
            _lanc("2026-05-05", 5000, "Salário", tipo="Receita"),  # -> junho
            _lanc("2026-06-05", 6000, "Salário", tipo="Receita"),  # -> julho
            _lanc("2026-07-05", 5500, "Salário", tipo="Receita"),  # -> agosto
            _lanc("2026-09-01", 1000, "Mercado"),
        ]
        resultado = estatisticas.projecao_poupanca(lancamentos, "2026-09")
        self.assertEqual(resultado["mes"], "2026-09")
        self.assertEqual(resultado["receitaEsperada"], 5500.0)
        self.assertEqual(resultado["poupancaProjetada"], round(resultado["receitaEsperada"] - resultado["despesaProjetada"], 2))

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_com_meta_calcula_aderencia_e_caminho(self, _hoje):
        lancamentos = [
            _lanc("2026-07-05", 5000, "Salário", tipo="Receita"),  # -> agosto
            _lanc("2026-09-01", 1000, "Mercado"),
        ]
        resultado = estatisticas.projecao_poupanca(lancamentos, "2026-09", meta_valor=1000.0)
        self.assertIn("aderenciaPercentual", resultado)
        self.assertTrue(resultado["noCaminho"])

        resultado_apertado = estatisticas.projecao_poupanca(lancamentos, "2026-09", meta_valor=4000.0)
        self.assertFalse(resultado_apertado["noCaminho"])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_ruido_de_corretora_nao_entra_na_despesa_nem_na_receita(self, _hoje):
        # "Investimento" aparece tanto como Despesa (tarifa/retirada de margem)
        # quanto como Receita (devolução de margem) — nenhum dos dois é gasto
        # ou renda de verdade, então nenhum pode mexer na poupança projetada.
        com_ruido = [
            _lanc("2026-07-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-09-01", 1000, "Mercado"),
            _lanc("2026-09-02", 300, "Investimento", tipo="Despesa", descricao="TARIFA POR COBERTURA DE MARGEM"),
            _lanc("2026-09-02", 280, "Investimento", tipo="Receita", descricao="DEVOLUÇÃO DE COBERTURA DE MARGEM"),
        ]
        sem_ruido = [
            _lanc("2026-07-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-09-01", 1000, "Mercado"),
        ]
        resultado_com_ruido = estatisticas.projecao_poupanca(com_ruido, "2026-09")
        resultado_sem_ruido = estatisticas.projecao_poupanca(sem_ruido, "2026-09")
        self.assertEqual(resultado_com_ruido["receitaEsperada"], resultado_sem_ruido["receitaEsperada"])
        self.assertEqual(resultado_com_ruido["despesaProjetada"], resultado_sem_ruido["despesaProjetada"])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_produto_de_venda_de_ativo_nao_conta_como_receita_mesmo_sem_palavra_de_margem(self, _hoje):
        # achado real: TED de R$8.571,37 era produto de venda de ações usado
        # pra pagar uma despesa — dinheiro que ele já tinha mudando de forma,
        # não renda nova. Diferente do lado Despesa, aqui a categoria inteira
        # "Investimento" é ignorada, mesmo sem nenhuma palavra de margem.
        com_venda = [
            _lanc("2026-07-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-09-01", 1000, "Mercado"),
            _lanc("2026-09-02", 8571.37, "Investimento", tipo="Receita", descricao="TED recebida de BRUNO NONATO KATO"),
        ]
        sem_venda = [
            _lanc("2026-07-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-09-01", 1000, "Mercado"),
        ]
        resultado_com_venda = estatisticas.projecao_poupanca(com_venda, "2026-09")
        resultado_sem_venda = estatisticas.projecao_poupanca(sem_venda, "2026-09")
        self.assertEqual(resultado_com_venda["receitaEsperada"], resultado_sem_venda["receitaEsperada"])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_recompra_de_acao_vendida_a_descoberto_conta_como_gasto_de_verdade(self, _hoje):
        # achado real: o usuário vendeu mais ações do que tinha e precisou
        # recomprar — isso é dinheiro saindo de verdade (~R$1.600), diferente
        # da tarifa/cobertura de margem que se autocancela todo dia. Só o
        # ruído de margem é ignorado; uma compra de ação de verdade continua
        # contando, mesmo vindo da mesma categoria "Investimento".
        lancamentos = [
            _lanc("2026-07-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-09-01", 1000, "Mercado"),
            _lanc("2026-09-02", 1600, "Investimento", tipo="Despesa", descricao="COMPRA DE AÇÕES VISC11"),
        ]
        resultado = estatisticas.projecao_poupanca(lancamentos, "2026-09")
        # piso é sempre o total já conhecido no mês (sem histórico, a
        # projeção pelo ritmo pode ficar até mais alta que isso)
        self.assertGreaterEqual(resultado["despesaProjetada"], 2600.0)


class TestDre(unittest.TestCase):
    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_realizado_soma_por_categoria_e_calcula_margem(self, _hoje):
        orcamentos = []
        lancamentos = [
            _lanc("2026-08-01", 5000, "Salário", tipo="Receita"),  # -> setembro
            _lanc("2026-09-05", 1000, "Mercado"),
            _lanc("2026-09-06", 500, "Lazer"),
        ]
        resultado = estatisticas.dre(lancamentos, orcamentos, "2026-09")
        self.assertEqual(resultado["mes"], "2026-09")
        self.assertEqual(resultado["realizado"]["receitaBruta"], 5000.0)
        self.assertEqual(resultado["realizado"]["totalDespesas"], 1500.0)
        self.assertEqual(resultado["realizado"]["resultadoLiquido"], 3500.0)
        self.assertEqual(resultado["realizado"]["margemPercentual"], 70.0)
        categorias = {c["categoria"]: c["valor"] for c in resultado["realizado"]["despesasPorCategoria"]}
        self.assertEqual(categorias, {"Mercado": 1000.0, "Lazer": 500.0})

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_capex_fica_de_fora_do_resultado_operacional(self, _hoje):
        orcamentos = []
        lancamentos = [
            _lanc("2026-08-01", 5000, "Salário", tipo="Receita"),
            _lanc("2026-09-05", 1000, "Mercado"),
            _lanc("2026-09-06", 10000, "Negócio"),
        ]
        resultado = estatisticas.dre(lancamentos, orcamentos, "2026-09")
        self.assertEqual(resultado["capex"], 10000.0)
        self.assertEqual(resultado["realizado"]["totalDespesas"], 1000.0)
        self.assertNotIn("Negócio", [c["categoria"] for c in resultado["realizado"]["despesasPorCategoria"]])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_sem_receita_margem_fica_none(self, _hoje):
        resultado = estatisticas.dre([_lanc("2026-09-05", 100, "Mercado")], [], "2026-09")
        self.assertIsNone(resultado["realizado"]["margemPercentual"])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_salario_fica_separado_de_outras_entradas(self, _hoje):
        # pedido explícito do usuário: "fonte de receita é só o salário" — Pix
        # da esposa, reembolso etc. entram, mas não contam como receita
        # principal, ficam numa linha separada no DRE.
        orcamentos = []
        lancamentos = [
            _lanc("2026-08-01", 5000, "Salário", tipo="Receita"),  # -> setembro
            _lanc("2026-09-02", 300, "Reembolso", tipo="Receita"),
            _lanc("2026-09-03", 200, "Outras entradas", tipo="Receita"),
            _lanc("2026-09-05", 1000, "Mercado"),
        ]
        resultado = estatisticas.dre(lancamentos, orcamentos, "2026-09")
        self.assertEqual(resultado["realizado"]["receitaSalario"], 5000.0)
        self.assertEqual(resultado["realizado"]["receitaBruta"], 5500.0)
        outras = {c["categoria"]: c["valor"] for c in resultado["realizado"]["outrasEntradas"]}
        self.assertEqual(outras, {"Reembolso": 300.0, "Outras entradas": 200.0})

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_devolucao_de_margem_nao_conta_como_receita_bruta(self, _hoje):
        orcamentos = []
        lancamentos = [
            _lanc("2026-08-01", 5000, "Salário", tipo="Receita"),
            _lanc("2026-09-05", 1000, "Mercado"),
            _lanc("2026-09-06", 280, "Investimento", tipo="Receita", descricao="DEVOLUÇÃO DE COBERTURA DE MARGEM"),
        ]
        resultado = estatisticas.dre(lancamentos, orcamentos, "2026-09")
        self.assertEqual(resultado["realizado"]["receitaBruta"], 5000.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_compra_no_cartao_conta_no_mes_em_que_a_fatura_e_paga(self, _hoje):
        # compra feita em julho, na fatura que fecha 27/07 — paga em agosto,
        # então é despesa de agosto, não de julho.
        lancamentos = [
            _lanc("2026-07-10", 200, "Compras", forma_pagamento="Cartão de crédito"),
        ]
        resultado_julho = estatisticas.dre(lancamentos, [], "2026-07")
        resultado_agosto = estatisticas.dre(lancamentos, [], "2026-08")
        self.assertEqual(resultado_julho["realizado"]["totalDespesas"], 0.0)
        self.assertEqual(resultado_agosto["realizado"]["totalDespesas"], 200.0)


class TestProjecaoCompromissosFuturos(unittest.TestCase):
    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_usa_parcelas_certas_e_receita_esperada_sem_assumir_orcamento_inteiro(self, _hoje):
        lancamentos = [
            _lanc("2026-05-05", 5000, "Salário", tipo="Receita"),  # -> junho
            _lanc("2026-06-05", 5000, "Salário", tipo="Receita"),  # -> julho
            _lanc("2026-07-05", 5000, "Salário", tipo="Receita"),  # -> agosto
            # parcela 2/3 cai no próximo mês (outubro)
            _lanc("2026-10-01", 300, "Compras", tipo="Despesa"),
        ]
        lancamentos[-1]["totalParcelas"] = 3
        resultado = estatisticas.projecao_compromissos_futuros(lancamentos, "2026-09", meses=2)
        self.assertEqual(len(resultado), 2)
        primeiro = resultado[0]
        self.assertEqual(primeiro["mes"], "2026-10")
        self.assertEqual(primeiro["receitaEsperada"], 5000.0)
        self.assertEqual(primeiro["compromissosCertos"], 300.0)
        self.assertEqual(primeiro["saldoEsperado"], 4700.0)
        self.assertEqual(primeiro["acumulado"], 4700.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_desconta_gasto_recorrente_tipico_alem_das_parcelas(self, _hoje):
        lancamentos = [
            _lanc("2026-05-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-06-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-07-05", 5000, "Salário", tipo="Receita"),
            # gasto recorrente (não-parcelado) repetido nos 3 meses anteriores
            _lanc("2026-07-10", 800, "Mercado"),
            _lanc("2026-08-10", 800, "Mercado"),
            _lanc("2026-09-10", 800, "Mercado"),
        ]
        resultado = estatisticas.projecao_compromissos_futuros(lancamentos, "2026-09", meses=1)
        self.assertEqual(resultado[0]["gastoRecorrenteEsperado"], 800.0)
        self.assertEqual(resultado[0]["saldoEsperado"], 5000.0 - 800.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_parcela_nao_entra_duas_vezes_no_gasto_recorrente(self, _hoje):
        lancamentos = [
            _lanc("2026-05-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-06-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-07-05", 5000, "Salário", tipo="Receita"),
        ]
        # mesma parcela recorrendo nos 3 meses anteriores E no próximo mês
        for data in ("2026-07-10", "2026-08-10", "2026-09-10", "2026-10-01"):
            parcela = _lanc(data, 300, "Compras")
            parcela["totalParcelas"] = 4
            lancamentos.append(parcela)
        resultado = estatisticas.projecao_compromissos_futuros(lancamentos, "2026-09", meses=1)
        # se contasse nos dois (recorrente E parcela certa), o desconto seria 600
        self.assertEqual(resultado[0]["gastoRecorrenteEsperado"], 0.0)
        self.assertEqual(resultado[0]["compromissosCertos"], 300.0)
        self.assertEqual(resultado[0]["saldoEsperado"], 5000.0 - 300.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_acumulado_soma_mes_a_mes(self, _hoje):
        lancamentos = [_lanc("2026-07-05", 1000, "Salário", tipo="Receita")]
        resultado = estatisticas.projecao_compromissos_futuros(lancamentos, "2026-09", meses=3)
        self.assertEqual([r["acumulado"] for r in resultado], [1000.0, 2000.0, 3000.0])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_receita_esperada_nao_zera_mesmo_em_meses_distantes(self, _hoje):
        """Encadeando vários meses futuros, os mais próximos não têm
        lançamento real nenhum — a mediana de receita precisa olhar além dos
        3 meses imediatamente anteriores pra não cair pra zero."""
        lancamentos = [_lanc("2026-07-05", 1000, "Salário", tipo="Receita")]
        resultado = estatisticas.projecao_compromissos_futuros(lancamentos, "2026-09", meses=6)
        self.assertTrue(all(r["receitaEsperada"] == 1000.0 for r in resultado))


class TestScoreDia(unittest.TestCase):
    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_transacao_grande_desconta_pontuacao_e_gera_alerta(self, _hoje):
        orcamentos = [{"categoria": "Compras", "limite": 100.0}]
        lancamentos = [_lanc("2026-09-10", 90, "Compras")]
        resultado = estatisticas.score_dia(lancamentos, orcamentos, dia_iso="2026-09-10")
        self.assertEqual(resultado["pontuacaoComportamento"], 75)
        self.assertTrue(any("Compras" in a for a in resultado["alertasComportamento"]))

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_frequencia_alta_no_mesmo_dia_desconta_pontuacao(self, _hoje):
        orcamentos = []
        lancamentos = [_lanc("2026-09-10", 10, "Lazer") for _ in range(3)]
        resultado = estatisticas.score_dia(lancamentos, orcamentos, dia_iso="2026-09-10")
        # sem histórico em Lazer, 3 lançamentos no dia disparam dois sinais: frequência
        # no dia (-15) e mudança de padrão, já que a média histórica é 0 (-20).
        self.assertEqual(resultado["pontuacaoComportamento"], 65)
        self.assertTrue(any("hoje" in a for a in resultado["alertasComportamento"]))

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_gatilho_de_impulso_conhecido_desconta_pontuacao(self, _hoje):
        orcamentos = []
        lancamentos = [_lanc("2026-09-10", 30, "Compras", descricao="APPLE.COM/BILL")]
        resultado = estatisticas.score_dia(lancamentos, orcamentos, dia_iso="2026-09-10")
        self.assertEqual(resultado["pontuacaoComportamento"], 80)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_score_final_combina_comportamento_e_aderencia_a_meta(self, _hoje):
        orcamentos = []
        lancamentos = [
            _lanc("2026-08-01", 5000, "Salário", tipo="Receita"),  # -> setembro
            _lanc("2026-09-01", 4000, "Mercado"),
        ]
        resultado = estatisticas.score_dia(lancamentos, orcamentos, meta_valor=1000.0, dia_iso="2026-09-10")
        # comportamento fica 100 (sem sinais), mas aderência à meta é ruim -> score final cai
        self.assertEqual(resultado["pontuacaoComportamento"], 100)
        self.assertLess(resultado["pontuacaoFinal"], 100)
        self.assertTrue(len(resultado["categoriasEmRiscoDeEstourar"]) == 0)  # sem orçamento definido

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_frequencia_alta_em_investimento_nao_gera_alerta(self, _hoje):
        # achado real: cobertura de margem da corretora gera várias transações
        # "Investimento" por dia — isso é ruído de liquidação, não comportamento
        # do usuário, então não pode disparar o sinal de mudança de padrão nem
        # o de frequência no dia.
        orcamentos = []
        lancamentos = [
            _lanc("2026-09-10", 10, "Investimento", descricao="RETIRADA DE COBERTURA DE MARGEM")
            for _ in range(4)
        ]
        resultado = estatisticas.score_dia(lancamentos, orcamentos, dia_iso="2026-09-10")
        self.assertEqual(resultado["pontuacaoComportamento"], 100)
        self.assertEqual(resultado["alertasComportamento"], [])


if __name__ == "__main__":
    unittest.main()
