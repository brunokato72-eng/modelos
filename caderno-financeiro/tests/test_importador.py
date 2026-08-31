import tempfile
import unittest
from pathlib import Path

from caderno_financeiro import db, importador
from caderno_financeiro.importador import detectar_colunas, parsear_data


class TestDeteccaoDeColunas(unittest.TestCase):
    def test_formato_do_csv_novo(self):
        mapa = detectar_colunas(
            ["data", "tipo", "categoria", "valor", "forma_pagamento", "conta", "descricao"]
        )
        self.assertEqual(mapa["data"], 0)
        self.assertEqual(mapa["tipo"], 1)
        self.assertEqual(mapa["categoria"], 2)
        self.assertEqual(mapa["valor"], 3)
        self.assertEqual(mapa["formaPagamento"], 4)
        self.assertEqual(mapa["conta"], 5)
        self.assertEqual(mapa["descricao"], 6)

    def test_cabecalho_com_acento_maiuscula_e_espaco(self):
        mapa = detectar_colunas(
            ["Data da Compra", "Descrição", "Valor (R$)", "Forma de Pagamento", "Cartão/Conta"]
        )
        self.assertEqual(mapa["data"], 0)
        self.assertEqual(mapa["descricao"], 1)
        self.assertEqual(mapa["valor"], 2)
        self.assertEqual(mapa["formaPagamento"], 3)
        self.assertEqual(mapa["conta"], 4)

    def test_valor_total_nao_rouba_a_coluna_valor(self):
        mapa = detectar_colunas(["Data", "Valor Total", "Valor", "Parcelas"])
        self.assertEqual(mapa["valorTotal"], 1)
        self.assertEqual(mapa["valor"], 2)
        self.assertEqual(mapa["totalParcelas"], 3)


class TestParsearData(unittest.TestCase):
    def test_formatos_comuns(self):
        self.assertEqual(parsear_data("2026-08-31"), "2026-08-31")
        self.assertEqual(parsear_data("31/08/2026"), "2026-08-31")
        self.assertEqual(parsear_data("31-08-2026"), "2026-08-31")
        self.assertEqual(parsear_data("31/08/2026 14:30"), "2026-08-31")

    def test_numero_de_serie_do_excel(self):
        self.assertEqual(parsear_data(45000), "2023-03-15")

    def test_data_ilegivel_da_erro(self):
        with self.assertRaises(ValueError):
            parsear_data("mês passado")


class TestImportacao(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.raiz = Path(self.pasta.name)
        self.banco = self.raiz / "teste.db"

    def _csv(self, conteudo: str, nome: str = "planilha.csv") -> Path:
        caminho = self.raiz / nome
        caminho.write_text(conteudo, encoding="utf-8")
        return caminho

    def test_importa_formato_do_briefing(self):
        arquivo = self._csv(
            "data,tipo,categoria,valor,forma_pagamento,conta,descricao\n"
            "21/07/2026,Despesa,Mercado,187.45,Cartão de crédito,Nubank,compra do mês\n"
            "22/07/2026,Receita,Salário,5200.00,Pix,Itaú,salário julho\n"
            "23/07/2026,Despesa,Alimentacao,38.90,,Ifood,almoço\n"
        )
        with db.banco(self.banco) as conexao:
            relatorio = importador.importar(conexao, arquivo)
            lancamentos = {l["descricao"]: l for l in db.listar(conexao)}

        self.assertEqual(relatorio["importados"], 3)
        self.assertEqual(lancamentos["salário julho"]["tipo"], "Receita")
        self.assertEqual(lancamentos["compra do mês"]["data"], "2026-07-21")
        # acento normalizado e regra do Ifood aplicada no que veio em branco
        self.assertEqual(lancamentos["almoço"]["categoria"], "Alimentação")
        self.assertEqual(lancamentos["almoço"]["formaPagamento"], "VR")

    def test_regra_padrao_quando_planilha_nao_diz_forma_nem_conta(self):
        arquivo = self._csv("data,categoria,valor,descricao\n01/08/2026,Lazer,80,cinema\n")
        with db.banco(self.banco) as conexao:
            importador.importar(conexao, arquivo)
            lanc = db.listar(conexao)[0]
        self.assertEqual(lanc["formaPagamento"], "Cartão de crédito")
        self.assertEqual(lanc["conta"], "Nubank")

    def test_delimitador_ponto_e_virgula_e_valor_brasileiro(self):
        arquivo = self._csv(
            "Data;Descrição;Valor (R$);Categoria\n"
            "05/08/2026;TV nova;R$ 1.899,90;Compras\n"
        )
        with db.banco(self.banco) as conexao:
            relatorio = importador.importar(conexao, arquivo)
            lanc = db.listar(conexao)[0]
        self.assertEqual(relatorio["importados"], 1)
        self.assertEqual(lanc["valor"], 1899.90)

    def test_categoria_de_receita_sem_coluna_tipo(self):
        arquivo = self._csv("data,categoria,valor,descricao\n02/08/2026,Outros_Receita,120,venda\n")
        with db.banco(self.banco) as conexao:
            importador.importar(conexao, arquivo)
            lanc = db.listar(conexao)[0]
        self.assertEqual(lanc["tipo"], "Receita")
        self.assertEqual(lanc["categoria"], "Outras entradas")

    def test_valor_negativo_vira_despesa_positiva(self):
        arquivo = self._csv("data,categoria,valor,descricao\n02/08/2026,Mercado,-99.90,feira\n")
        with db.banco(self.banco) as conexao:
            importador.importar(conexao, arquivo)
            lanc = db.listar(conexao)[0]
        self.assertEqual(lanc["tipo"], "Despesa")
        self.assertEqual(lanc["valor"], 99.90)

    def test_duplicatas_dentro_do_arquivo_e_contra_o_banco(self):
        primeiro = self._csv(
            "data,categoria,valor,descricao\n"
            "10/08/2026,Mercado,45.90,feira\n", "a.csv")
        segundo = self._csv(
            "data,categoria,valor,descricao\n"
            "11/08/2026,Mercado,45.90,feira de novo\n"   # ±1 dia + mesmo valor = duplicata
            "13/08/2026,Mercado,45.90,outra feira\n"      # fora da janela, entra
            "13/08/2026,Mercado,45.90,repetida no arquivo\n",  # duplicata dentro do lote
            "b.csv")
        with db.banco(self.banco) as conexao:
            importador.importar(conexao, primeiro)
            relatorio = importador.importar(conexao, segundo)
            total = db.contar(conexao)
        self.assertEqual(relatorio["importados"], 1)
        self.assertEqual(len(relatorio["duplicados"]), 2)
        self.assertEqual(total, 2)

    def test_sem_dedup_importa_tudo(self):
        arquivo = self._csv(
            "data,categoria,valor,descricao\n"
            "10/08/2026,Mercado,45.90,feira\n"
            "10/08/2026,Mercado,45.90,feira\n")
        with db.banco(self.banco) as conexao:
            relatorio = importador.importar(conexao, arquivo, checar_duplicatas=False)
        self.assertEqual(relatorio["importados"], 2)

    def test_simular_nao_grava(self):
        arquivo = self._csv("data,categoria,valor,descricao\n10/08/2026,Mercado,45.90,feira\n")
        with db.banco(self.banco) as conexao:
            relatorio = importador.importar(conexao, arquivo, simular=True)
            self.assertEqual(db.contar(conexao), 0)
        self.assertEqual(relatorio["aImportar"], 1)
        self.assertEqual(relatorio["importados"], 0)

    def test_linha_ruim_vira_erro_e_o_resto_entra(self):
        arquivo = self._csv(
            "data,categoria,valor,descricao\n"
            "10/08/2026,Mercado,45.90,ok\n"
            "sem data,Mercado,10,quebrada\n"
            "12/08/2026,Mercado,,sem valor\n")
        with db.banco(self.banco) as conexao:
            relatorio = importador.importar(conexao, arquivo)
        self.assertEqual(relatorio["importados"], 1)
        self.assertEqual(len(relatorio["erros"]), 2)
        self.assertEqual(relatorio["erros"][0]["linha"], 3)

    def test_planilha_sem_colunas_obrigatorias(self):
        arquivo = self._csv("coluna a,coluna b\n1,2\n")
        with db.banco(self.banco) as conexao:
            with self.assertRaises(importador.ErroImportacao):
                importador.importar(conexao, arquivo)

    def test_coluna_de_parcela_no_formato_3_de_10(self):
        arquivo = self._csv(
            "data,categoria,valor,parcelas,descricao\n03/08/2026,Compras,300,3/10,geladeira\n")
        with db.banco(self.banco) as conexao:
            importador.importar(conexao, arquivo)
            lanc = db.listar(conexao)[0]
        self.assertEqual(lanc["parcelaAtual"], 3)
        self.assertEqual(lanc["totalParcelas"], 10)
        self.assertEqual(lanc["valorTotal"], 3000.0)


if __name__ == "__main__":
    unittest.main()
