import unittest

from caderno_financeiro.datas import (dias_entre, mes_anterior, somar_meses,
                                      validar_iso, validar_mes)


class TestSomarMeses(unittest.TestCase):
    def test_mes_seguinte_simples(self):
        self.assertEqual(somar_meses("2026-03-10", 1), "2026-04-10")

    def test_clampa_dia_em_mes_curto(self):
        # o caso do briefing: comprou dia 31/01, parcela de fevereiro cai em 28
        self.assertEqual(somar_meses("2026-01-31", 1), "2026-02-28")
        self.assertEqual(somar_meses("2026-01-31", 3), "2026-04-30")

    def test_ano_bissexto(self):
        self.assertEqual(somar_meses("2024-01-31", 1), "2024-02-29")

    def test_virada_de_ano(self):
        self.assertEqual(somar_meses("2026-12-15", 1), "2027-01-15")
        self.assertEqual(somar_meses("2026-11-30", 14), "2028-01-30")

    def test_meses_negativos(self):
        self.assertEqual(somar_meses("2026-03-31", -1), "2026-02-28")
        self.assertEqual(somar_meses("2026-01-15", -1), "2025-12-15")

    def test_dia_nunca_estoura_para_o_mes_seguinte(self):
        for passo in range(0, 25):
            data = somar_meses("2026-01-31", passo)
            self.assertLessEqual(int(data[8:10]), 31)
            validar_iso(data)


class TestAuxiliares(unittest.TestCase):
    def test_mes_anterior(self):
        self.assertEqual(mes_anterior("2026-01"), "2025-12")
        self.assertEqual(mes_anterior("2026-08"), "2026-07")

    def test_dias_entre(self):
        self.assertEqual(dias_entre("2026-08-01", "2026-08-02"), 1)
        self.assertEqual(dias_entre("2026-08-02", "2026-08-01"), 1)

    def test_validacoes(self):
        with self.assertRaises(ValueError):
            validar_iso("31/01/2026")
        with self.assertRaises(ValueError):
            validar_mes("2026-13")
        self.assertEqual(validar_mes("2026-02"), "2026-02")


if __name__ == "__main__":
    unittest.main()
