import unittest
from unittest import mock

from caderno_financeiro import estatisticas


def _lanc(data, valor, categoria, tipo="Despesa", descricao=""):
    return {"data": data, "valor": valor, "categoria": categoria, "tipo": tipo, "descricao": descricao}


class TestProjecaoCategoria(unittest.TestCase):
    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_projeta_pelo_ritmo_de_gasto_ate_agora(self, _hoje):
        lancamentos = [_lanc("2026-09-05", 100, "Mercado")]
        # dia 10 de setembro, mês tem 30 dias: 100 em 10 dias -> ritmo 10/dia -> 300 no mês
        resultado = estatisticas.projecao_categoria(lancamentos, "Mercado", 250, "2026-09")
        self.assertEqual(resultado["gasto"], 100.0)
        self.assertEqual(resultado["projecao"], 300.0)
        self.assertTrue(resultado["vaiEstourar"])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_sem_limite_nao_marca_estouro(self, _hoje):
        lancamentos = [_lanc("2026-09-05", 100, "Mercado")]
        resultado = estatisticas.projecao_categoria(lancamentos, "Mercado", None, "2026-09")
        self.assertFalse(resultado["vaiEstourar"])

    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-10-01")
    def test_mes_fechado_usa_o_mes_inteiro_sem_projetar(self, _hoje):
        lancamentos = [_lanc("2026-09-15", 500, "Mercado")]
        resultado = estatisticas.projecao_categoria(lancamentos, "Mercado", 400, "2026-09")
        self.assertEqual(resultado["projecao"], 500.0)
        self.assertTrue(resultado["vaiEstourar"])


class TestProjecaoPoupanca(unittest.TestCase):
    @mock.patch.object(estatisticas, "hoje_iso", return_value="2026-09-10")
    def test_usa_mediana_de_receita_dos_meses_anteriores_fechados(self, _hoje):
        lancamentos = [
            _lanc("2026-06-05", 5000, "Salário", tipo="Receita"),
            _lanc("2026-07-05", 6000, "Salário", tipo="Receita"),
            _lanc("2026-08-05", 5500, "Salário", tipo="Receita"),
            _lanc("2026-09-01", 1000, "Mercado"),
        ]
        resultado = estatisticas.projecao_poupanca(lancamentos, "2026-09")
        self.assertEqual(resultado["receitaEsperada"], 5500.0)
        # 1000 em 10 dias -> ritmo 100/dia -> 3000 no mês (30 dias)
        self.assertEqual(resultado["despesaProjetada"], 3000.0)
        self.assertEqual(resultado["poupancaProjetada"], 2500.0)

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
