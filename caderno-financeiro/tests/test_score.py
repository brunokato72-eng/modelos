import unittest
from unittest import mock

from caderno_financeiro import estatisticas


def _lanc(data, valor, categoria, tipo="Despesa", descricao=""):
    return {"data": data, "valor": valor, "categoria": categoria, "tipo": tipo, "descricao": descricao}


# Ciclo real de fatura (fecha dia 27): o ciclo "2026-01" é [2025-12-28, 2026-01-27].
# `_media_historica_despesa_centavos` deriva os 3 ciclos anteriores a partir do
# início real (via `datas.ciclo_anterior`), então os testes usam esse início de
# verdade — um início arbitrário faria o "ciclo anterior" calculado por engano
# se sobrepor ao próprio ciclo do teste.
INICIO, FIM = "2025-12-28", "2026-01-27"


class TestProjecaoCategoria(unittest.TestCase):
    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-10")
    def test_projeta_pelo_ritmo_de_gasto_ate_agora_sem_historico(self, _hoje):
        lancamentos = [_lanc("2026-01-05", 100, "Mercado")]
        # dia 14 de um ciclo de 31 dias (início 28/12): 100 em 14 dias -> ritmo 221.43
        # no ciclo; sem histórico, shrinkage não entra, é ritmo puro.
        resultado = estatisticas.projecao_categoria(lancamentos, "Mercado", 250, INICIO, FIM)
        self.assertEqual(resultado["gasto"], 100.0)
        self.assertEqual(resultado["projecao"], 221.43)
        self.assertFalse(resultado["vaiEstourar"])  # 221.43 < 250

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-10")
    def test_sem_limite_nao_marca_estouro(self, _hoje):
        lancamentos = [_lanc("2026-01-05", 100, "Mercado")]
        resultado = estatisticas.projecao_categoria(lancamentos, "Mercado", None, INICIO, FIM)
        self.assertFalse(resultado["vaiEstourar"])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-02-01")
    def test_ciclo_fechado_usa_o_ciclo_inteiro_sem_projetar(self, _hoje):
        lancamentos = [_lanc("2026-01-15", 500, "Mercado")]
        resultado = estatisticas.projecao_categoria(lancamentos, "Mercado", 400, INICIO, FIM)
        self.assertEqual(resultado["projecao"], 500.0)
        self.assertTrue(resultado["vaiEstourar"])


class TestProjecaoComPoucosDias(unittest.TestCase):
    """Dia 1 do ciclo não pode projetar via ritmo puro — gasto/1*31 explode
    qualquer transação isolada. Tem que puxar pra média histórica."""

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2025-12-28")
    def test_dia_1_nao_explode_projecao_sem_historico(self, _hoje):
        lancamentos = [_lanc("2025-12-28", 1000, "Compras")]
        resultado = estatisticas.projecao_categoria(lancamentos, "Compras", 500, INICIO, FIM)
        # sem histórico pra puxar, cai no ritmo puro mesmo (não tem o que fazer)
        self.assertEqual(resultado["projecao"], 31000.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2025-12-28")
    def test_dia_1_com_historico_fica_proximo_da_media(self, _hoje):
        lancamentos = [
            _lanc("2025-09-28", 500, "Compras"),
            _lanc("2025-10-28", 500, "Compras"),
            _lanc("2025-11-28", 500, "Compras"),
            _lanc("2025-12-28", 1000, "Compras"),
        ]
        resultado = estatisticas.projecao_categoria(lancamentos, "Compras", 500, INICIO, FIM)
        # ritmo puro seria 31000; com peso baixo no dia 1 (1/31), a média
        # histórica (500) domina e a projeção fica bem mais baixa que isso
        self.assertAlmostEqual(resultado["projecao"], 1483.87, delta=1.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2025-12-28")
    def test_sem_gasto_real_no_ciclo_nao_marca_estouro_so_pela_media_historica(self, _hoje):
        lancamentos = [
            _lanc("2025-09-28", 2000, "Saúde"),
            _lanc("2025-10-28", 2000, "Saúde"),
            _lanc("2025-11-28", 2000, "Saúde"),
        ]
        resultado = estatisticas.projecao_categoria(lancamentos, "Saúde", 1100, INICIO, FIM)
        self.assertEqual(resultado["gasto"], 0.0)
        self.assertFalse(resultado["vaiEstourar"])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-01")
    def test_peso_do_ritmo_cresce_com_a_fracao_do_ciclo_decorrida(self, _hoje):
        lancamentos = [
            _lanc("2025-11-28", 500, "Compras"),
            _lanc("2026-01-01", 500, "Compras"),
        ]
        resultado = estatisticas.projecao_categoria(lancamentos, "Compras", 500, INICIO, FIM)
        self.assertAlmostEqual(resultado["projecao"], 919.35, delta=1.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-27")
    def test_fim_do_ciclo_ritmo_e_total_real_coincidem(self, _hoje):
        lancamentos = [
            _lanc("2025-11-28", 500, "Compras"),
            _lanc("2026-01-27", 3100, "Compras"),
        ]
        resultado = estatisticas.projecao_categoria(lancamentos, "Compras", 500, INICIO, FIM)
        # último dia do ciclo: peso do ritmo é 100%, projeção = total real do ciclo
        self.assertEqual(resultado["projecao"], 3100.0)


class TestProjecaoComParcelasFuturas(unittest.TestCase):
    """Parcela futura já cadastrada no ciclo é gasto CERTO, não estimativa — não
    pode entrar no "até agora" e ser multiplicada de novo pelo ritmo."""

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-05")
    def test_parcela_futura_nao_e_contada_duas_vezes_na_projecao(self, _hoje):
        lancamentos = [
            _lanc("2026-01-01", 100, "Compras"),
            _lanc("2026-01-03", 100, "Compras"),
            _lanc("2026-01-10", 400, "Compras"),  # parcela futura já cadastrada
        ]
        resultado = estatisticas.projecao_categoria(lancamentos, "Compras", 300, INICIO, FIM)
        self.assertEqual(resultado["gasto"], 600.0)
        self.assertEqual(resultado["projecao"], 688.89)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-01-05")
    def test_projecao_nunca_fica_abaixo_do_total_ja_conhecido(self, _hoje):
        lancamentos = [_lanc("2026-01-20", 5000, "Compras")]  # só parcela futura, nada "até agora"
        resultado = estatisticas.projecao_categoria(lancamentos, "Compras", 300, INICIO, FIM)
        self.assertEqual(resultado["gasto"], 5000.0)
        self.assertEqual(resultado["projecao"], 5000.0)


# `projecao_poupanca` recebe um `rotulo` (AAAA-MM do fechamento) e resolve
# internamente o ciclo real via `datas.ciclo_por_rotulo` — o ciclo "2026-09" é
# [2026-08-28, 2026-09-27].
class TestProjecaoPoupanca(unittest.TestCase):
    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_usa_mediana_de_receita_dos_ciclos_anteriores_fechados(self, _hoje):
        lancamentos = [
            _lanc("2026-06-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-07-05", 6000, "Salário", tipo="Receita"),
            _lanc("2026-08-05", 5500, "Salário", tipo="Receita"),
            _lanc("2026-09-01", 1000, "Mercado"),
        ]
        resultado = estatisticas.projecao_poupanca(lancamentos, "2026-09")
        self.assertEqual(resultado["ciclo"], "2026-09")
        self.assertEqual(resultado["receitaEsperada"], 5500.0)
        self.assertEqual(resultado["poupancaProjetada"], round(resultado["receitaEsperada"] - resultado["despesaProjetada"], 2))

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_com_meta_calcula_aderencia_e_caminho(self, _hoje):
        lancamentos = [
            _lanc("2026-08-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-09-01", 1000, "Mercado"),
        ]
        resultado = estatisticas.projecao_poupanca(lancamentos, "2026-09", meta_valor=1000.0)
        self.assertIn("aderenciaPercentual", resultado)
        self.assertTrue(resultado["noCaminho"])

        resultado_apertado = estatisticas.projecao_poupanca(lancamentos, "2026-09", meta_valor=4000.0)
        self.assertFalse(resultado_apertado["noCaminho"])


class TestDre(unittest.TestCase):
    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_realizado_soma_por_categoria_e_calcula_margem(self, _hoje):
        orcamentos = []
        lancamentos = [
            _lanc("2026-09-01", 5000, "Salário", tipo="Receita"),
            _lanc("2026-09-05", 1000, "Mercado"),
            _lanc("2026-09-06", 500, "Lazer"),
        ]
        resultado = estatisticas.dre(lancamentos, orcamentos, "2026-09")
        self.assertEqual(resultado["ciclo"], "2026-09")
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
            _lanc("2026-09-01", 5000, "Salário", tipo="Receita"),
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


class TestProjecaoCompromissosFuturos(unittest.TestCase):
    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_usa_parcelas_certas_e_receita_esperada_sem_assumir_orcamento_inteiro(self, _hoje):
        lancamentos = [
            _lanc("2026-06-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-07-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-08-05", 5000, "Salário", tipo="Receita"),
            # parcela 2/3 cai no próximo ciclo (28/09 a 27/10)
            _lanc("2026-10-01", 300, "Compras", tipo="Despesa"),
        ]
        lancamentos[-1]["totalParcelas"] = 3
        resultado = estatisticas.projecao_compromissos_futuros(lancamentos, "2026-09", meses=2)
        self.assertEqual(len(resultado), 2)
        primeiro = resultado[0]
        self.assertEqual(primeiro["ciclo"], "2026-10")
        self.assertEqual(primeiro["receitaEsperada"], 5000.0)
        self.assertEqual(primeiro["compromissosCertos"], 300.0)
        self.assertEqual(primeiro["saldoEsperado"], 4700.0)
        self.assertEqual(primeiro["acumulado"], 4700.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_desconta_gasto_recorrente_tipico_alem_das_parcelas(self, _hoje):
        lancamentos = [
            _lanc("2026-06-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-07-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-08-05", 5000, "Salário", tipo="Receita"),
            # gasto recorrente (não-parcelado) repetido nos 3 ciclos anteriores
            _lanc("2026-06-10", 800, "Mercado"),
            _lanc("2026-07-10", 800, "Mercado"),
            _lanc("2026-08-10", 800, "Mercado"),
        ]
        resultado = estatisticas.projecao_compromissos_futuros(lancamentos, "2026-09", meses=1)
        self.assertEqual(resultado[0]["gastoRecorrenteEsperado"], 800.0)
        self.assertEqual(resultado[0]["saldoEsperado"], 5000.0 - 800.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_parcela_nao_entra_duas_vezes_no_gasto_recorrente(self, _hoje):
        lancamentos = [
            _lanc("2026-06-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-07-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-08-05", 5000, "Salário", tipo="Receita"),
        ]
        # mesma parcela recorrendo nos 3 ciclos anteriores E no próximo ciclo
        for data in ("2026-06-10", "2026-07-10", "2026-08-10", "2026-10-01"):
            parcela = _lanc(data, 300, "Compras")
            parcela["totalParcelas"] = 4
            lancamentos.append(parcela)
        resultado = estatisticas.projecao_compromissos_futuros(lancamentos, "2026-09", meses=1)
        # se contasse nos dois (recorrente E parcela certa), o desconto seria 600
        self.assertEqual(resultado[0]["gastoRecorrenteEsperado"], 0.0)
        self.assertEqual(resultado[0]["compromissosCertos"], 300.0)
        self.assertEqual(resultado[0]["saldoEsperado"], 5000.0 - 300.0)

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_acumulado_soma_ciclo_a_ciclo(self, _hoje):
        lancamentos = [_lanc("2026-08-05", 1000, "Salário", tipo="Receita")]
        resultado = estatisticas.projecao_compromissos_futuros(lancamentos, "2026-09", meses=3)
        self.assertEqual([r["acumulado"] for r in resultado], [1000.0, 2000.0, 3000.0])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_receita_esperada_nao_zera_mesmo_em_ciclos_distantes(self, _hoje):
        """Encadeando vários ciclos futuros, os mais próximos não têm lançamento
        real nenhum — a mediana de receita precisa olhar além dos 3 ciclos
        imediatamente anteriores pra não cair pra zero."""
        lancamentos = [_lanc("2026-08-05", 1000, "Salário", tipo="Receita")]
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
            _lanc("2026-08-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-09-01", 4000, "Mercado"),
        ]
        resultado = estatisticas.score_dia(lancamentos, orcamentos, meta_valor=1000.0, dia_iso="2026-09-10")
        # comportamento fica 100 (sem sinais), mas aderência à meta é ruim -> score final cai
        self.assertEqual(resultado["pontuacaoComportamento"], 100)
        self.assertLess(resultado["pontuacaoFinal"], 100)
        self.assertTrue(len(resultado["categoriasEmRiscoDeEstourar"]) == 0)  # sem orçamento definido


if __name__ == "__main__":
    unittest.main()
