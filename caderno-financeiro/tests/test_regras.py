import unittest

from caderno_financeiro import config
from caderno_financeiro.regras import (aplicar_regras, dividir_parcelas,
                                       montar_lancamentos, normalizar_categoria,
                                       normalizar_forma, normalizar_tipo)
from caderno_financeiro.valores import para_centavos, parsear_valor


class TestRegrasDeterministicas(unittest.TestCase):
    def test_regra_1_sem_forma_e_sem_conta(self):
        self.assertEqual(aplicar_regras(None, None), ("Cartão de crédito", "Nubank"))
        self.assertEqual(aplicar_regras("", "  "), ("Cartão de crédito", "Nubank"))

    def test_regra_2_ifood_vira_vr(self):
        self.assertEqual(aplicar_regras(None, "Ifood"), ("VR", "Ifood"))
        # tem prioridade sobre a regra 1: não cai no cartão de crédito/Nubank
        self.assertEqual(aplicar_regras(None, "ifood"), ("VR", "Ifood"))
        self.assertEqual(aplicar_regras(None, "IFOOD"), ("VR", "Ifood"))

    def test_regra_3_explicito_ganha(self):
        self.assertEqual(aplicar_regras("Pix", "Itaú"), ("Pix", "Itaú"))
        self.assertEqual(aplicar_regras("pix", None), ("Pix", config.CONTA_PADRAO))
        # forma dita explicitamente numa compra do Ifood não é sobrescrita
        self.assertEqual(aplicar_regras("Pix", "Ifood"), ("Pix", "Ifood"))

    def test_conta_citada_sem_forma_nao_fica_em_branco(self):
        forma, conta = aplicar_regras(None, "Inter")
        self.assertEqual(forma, config.FORMA_PADRAO)
        self.assertEqual(conta, "Inter")

    def test_forma_citada_sem_conta_tambem_nao_fica_em_branco(self):
        # espelho do caso acima: "no pix" sem dizer o banco vira Pix + Nubank,
        # não Pix + conta em branco — cada campo em branco pega seu próprio padrão
        forma, conta = aplicar_regras("Pix", None)
        self.assertEqual(forma, "Pix")
        self.assertEqual(conta, config.CONTA_PADRAO)


class TestNormalizacoes(unittest.TestCase):
    def test_categoria_ignora_acento_e_caixa(self):
        self.assertEqual(normalizar_categoria("Alimentacao"), "Alimentação")
        self.assertEqual(normalizar_categoria("ALIMENTAÇÃO"), "Alimentação")
        self.assertEqual(normalizar_categoria("saude"), "Saúde")

    def test_categoria_de_receita_por_alias(self):
        self.assertEqual(normalizar_categoria("Outros_Receita", "Receita"), "Outras entradas")
        self.assertEqual(normalizar_categoria("salario", "Receita"), "Salário")

    def test_categoria_desconhecida_cai_no_outros_do_tipo(self):
        self.assertEqual(normalizar_categoria("xpto", "Despesa"), "Outros")
        self.assertEqual(normalizar_categoria("xpto", "Receita"), "Outras entradas")

    def test_forma_pagamento(self):
        self.assertEqual(normalizar_forma("credito"), "Cartão de crédito")
        self.assertEqual(normalizar_forma("CARTÃO DE DÉBITO"), "Cartão de débito")
        self.assertEqual(normalizar_forma("vale refeicao"), "VR")
        self.assertEqual(normalizar_forma("qualquer coisa"), "")

    def test_tipo(self):
        self.assertEqual(normalizar_tipo("despesa"), "Despesa")
        self.assertEqual(normalizar_tipo("Entrada"), "Receita")
        self.assertEqual(normalizar_tipo(None), "Despesa")

    def test_parsear_valor(self):
        self.assertEqual(parsear_valor("R$ 1.234,56"), 1234.56)
        self.assertEqual(parsear_valor("1234.56"), 1234.56)
        self.assertEqual(parsear_valor("45,90"), 45.9)
        self.assertEqual(parsear_valor("1.234"), 1234.0)
        self.assertEqual(parsear_valor("-20"), -20.0)
        self.assertEqual(parsear_valor(30), 30.0)


class TestParcelamento(unittest.TestCase):
    def test_soma_das_parcelas_bate_exatamente(self):
        for total, n in ((100.0, 3), (3600.0, 12), (0.05, 4), (999.99, 7), (1234.56, 10)):
            parcelas = dividir_parcelas(total, n)
            self.assertEqual(len(parcelas), n)
            self.assertEqual(sum(para_centavos(p) for p in parcelas), para_centavos(total))

    def test_ajuste_fica_na_ultima_parcela(self):
        parcelas = dividir_parcelas(100.0, 3)
        self.assertEqual(parcelas[:2], [33.33, 33.33])
        self.assertEqual(parcelas[-1], 33.34)

    def test_uma_parcela(self):
        self.assertEqual(dividir_parcelas(45.9, 1), [45.9])

    def test_gera_uma_linha_por_mes_com_clamp(self):
        linhas = montar_lancamentos(
            data="2026-01-31",
            tipo="Despesa",
            categoria="Compras",
            valor_total=3600.0,
            total_parcelas=12,
            forma_pagamento=None,
            conta=None,
            descricao="geladeira",
            criado_em="2026-01-31T10:00:00",
        )
        self.assertEqual(len(linhas), 12)
        self.assertEqual(linhas[0]["data"], "2026-01-31")
        self.assertEqual(linhas[1]["data"], "2026-02-28")
        self.assertEqual(linhas[-1]["data"], "2026-12-31")
        self.assertEqual(sum(para_centavos(l["valor"]) for l in linhas), 360000)
        # regras determinísticas aplicadas depois da extração
        self.assertEqual(linhas[0]["formaPagamento"], "Cartão de crédito")
        self.assertEqual(linhas[0]["conta"], "Nubank")
        # todas as parcelas do mesmo grupo
        self.assertEqual(len({l["grupoParcelamento"] for l in linhas}), 1)
        self.assertEqual([l["parcelaAtual"] for l in linhas[:3]], [1, 2, 3])

    def test_compra_a_vista_nao_tem_grupo(self):
        linhas = montar_lancamentos(
            data="2026-08-30", tipo="Despesa", categoria="Mercado", valor_total=45.9,
            forma_pagamento=None, conta="Ifood", descricao="almoço",
            criado_em="2026-08-30T10:00:00",
        )
        self.assertEqual(len(linhas), 1)
        self.assertIsNone(linhas[0]["grupoParcelamento"])
        self.assertEqual(linhas[0]["formaPagamento"], "VR")


if __name__ == "__main__":
    unittest.main()
