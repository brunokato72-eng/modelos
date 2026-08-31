import unittest

from caderno_financeiro.calculadora import executar_calculo

LANCAMENTOS = [
    {"data": "2026-07-05", "tipo": "Despesa", "categoria": "Mercado", "valor": 100.00,
     "formaPagamento": "Pix", "conta": "Nubank", "descricao": "feira", "totalParcelas": 1},
    {"data": "2026-08-01", "tipo": "Despesa", "categoria": "Mercado", "valor": 200.00,
     "formaPagamento": "Cartão de crédito", "conta": "Nubank", "descricao": "mercado do mês",
     "totalParcelas": 1},
    {"data": "2026-08-10", "tipo": "Despesa", "categoria": "Transporte", "valor": 33.33,
     "formaPagamento": "Pix", "conta": "Nubank", "descricao": "uber pro trabalho",
     "totalParcelas": 1},
    {"data": "2026-08-15", "tipo": "Despesa", "categoria": "Alimentação", "valor": 66.67,
     "formaPagamento": "VR", "conta": "Ifood", "descricao": "almoço", "totalParcelas": 1},
    {"data": "2026-08-20", "tipo": "Receita", "categoria": "Salário", "valor": 5000.00,
     "formaPagamento": "Pix", "conta": "Itaú", "descricao": "salário", "totalParcelas": 1},
]


class TestExecutarCalculo(unittest.TestCase):
    def test_soma_do_mes(self):
        resultado = executar_calculo(
            {"operacao": "soma", "tipo": "Despesa", "mesInicio": "2026-08", "mesFim": "2026-08"},
            LANCAMENTOS,
        )
        self.assertEqual(resultado["resultado"], 300.00)
        self.assertEqual(resultado["quantidade"], 3)

    def test_soma_sem_filtro_de_periodo_pega_tudo(self):
        resultado = executar_calculo({"operacao": "soma", "tipo": "Despesa"}, LANCAMENTOS)
        self.assertEqual(resultado["resultado"], 400.00)

    def test_soma_de_centavos_nao_acumula_erro(self):
        centavos = [{"data": "2026-08-01", "tipo": "Despesa", "categoria": "Outros",
                     "valor": 0.10, "totalParcelas": 1} for _ in range(10)]
        resultado = executar_calculo({"operacao": "soma"}, centavos)
        self.assertEqual(resultado["resultado"], 1.00)

    def test_media_mediana_e_desvio(self):
        base = {"tipo": "Despesa", "mesInicio": "2026-08", "mesFim": "2026-08"}
        self.assertEqual(executar_calculo({**base, "operacao": "media"}, LANCAMENTOS)["resultado"], 100.00)
        self.assertEqual(executar_calculo({**base, "operacao": "mediana"}, LANCAMENTOS)["resultado"], 66.67)
        self.assertAlmostEqual(
            executar_calculo({**base, "operacao": "desviopadrao"}, LANCAMENTOS)["resultado"],
            72.01, places=2,  # desvio populacional de 200,00 / 33,33 / 66,67
        )

    def test_mediana_com_quantidade_par(self):
        resultado = executar_calculo({"operacao": "mediana", "categoria": "Mercado"}, LANCAMENTOS)
        self.assertEqual(resultado["resultado"], 150.00)

    def test_minimo_maximo_contagem(self):
        base = {"tipo": "Despesa"}
        self.assertEqual(executar_calculo({**base, "operacao": "minimo"}, LANCAMENTOS)["resultado"], 33.33)
        self.assertEqual(executar_calculo({**base, "operacao": "maximo"}, LANCAMENTOS)["resultado"], 200.00)
        self.assertEqual(executar_calculo({**base, "operacao": "contagem"}, LANCAMENTOS)["resultado"], 4)

    def test_filtros_por_forma_conta_e_descricao(self):
        self.assertEqual(
            executar_calculo({"operacao": "soma", "formaPagamento": "Pix", "tipo": "Despesa"},
                             LANCAMENTOS)["resultado"], 133.33)
        self.assertEqual(
            executar_calculo({"operacao": "soma", "conta": "ifood"}, LANCAMENTOS)["resultado"], 66.67)
        self.assertEqual(
            executar_calculo({"operacao": "soma", "descricaoContem": "uber"},
                             LANCAMENTOS)["resultado"], 33.33)

    def test_agrupamento_ordenado_por_valor(self):
        resultado = executar_calculo(
            {"operacao": "soma", "tipo": "Despesa", "mesInicio": "2026-08",
             "mesFim": "2026-08", "agruparPor": "categoria"},
            LANCAMENTOS,
        )
        self.assertEqual([g["grupo"] for g in resultado["grupos"]],
                         ["Mercado", "Alimentação", "Transporte"])
        self.assertEqual(resultado["grupos"][0]["resultado"], 200.00)

    def test_listar_ordena_por_valor_e_traz_total(self):
        resultado = executar_calculo({"operacao": "listar", "tipo": "Despesa"}, LANCAMENTOS)
        self.assertEqual(resultado["itens"][0]["valor"], 200.00)
        self.assertEqual(resultado["totalPeriodo"], 400.00)
        self.assertFalse(resultado["truncado"])

    def test_recorte_vazio_devolve_zero_e_quantidade_zero(self):
        resultado = executar_calculo(
            {"operacao": "soma", "categoria": "Educação", "mesInicio": "2026-08"}, LANCAMENTOS)
        self.assertEqual(resultado["resultado"], 0)
        self.assertEqual(resultado["quantidade"], 0)

    def test_operacao_invalida_da_erro(self):
        with self.assertRaises(ValueError):
            executar_calculo({"operacao": "regressao_linear"}, LANCAMENTOS)

    def test_agrupamento_invalido_da_erro(self):
        with self.assertRaises(ValueError):
            executar_calculo({"operacao": "soma", "agruparPor": "signo"}, LANCAMENTOS)


if __name__ == "__main__":
    unittest.main()
