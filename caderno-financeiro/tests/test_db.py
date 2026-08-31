import tempfile
import unittest
import uuid
from pathlib import Path

from caderno_financeiro import db, exportador
from caderno_financeiro.estatisticas import resumo_mensal


def lancamento(**campos):
    base = {
        "id": uuid.uuid4().hex,
        "data": "2026-08-10",
        "tipo": "Despesa",
        "categoria": "Mercado",
        "valor": 50.0,
        "valorTotal": 50.0,
        "parcelaAtual": 1,
        "totalParcelas": 1,
        "formaPagamento": "Pix",
        "conta": "Nubank",
        "descricao": "teste",
        "criadoEm": "2026-08-10T09:00:00",
        "grupoParcelamento": None,
    }
    base.update(campos)
    return base


class TestBanco(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.caminho = Path(self.pasta.name) / "teste.db"

    def tearDown(self):
        self.pasta.cleanup()

    def test_inserir_e_listar(self):
        with db.banco(self.caminho) as conexao:
            db.inserir(conexao, [lancamento(), lancamento(data="2026-07-01", valor=10.0)])
            self.assertEqual(db.contar(conexao), 2)
            self.assertEqual(len(db.listar(conexao, mes_inicio="2026-08")), 1)
            self.assertEqual(len(db.listar(conexao, categoria="Mercado")), 2)
            self.assertEqual(len(db.listar(conexao, conta="nubank")), 2)

    def test_persistencia_entre_conexoes(self):
        with db.banco(self.caminho) as conexao:
            db.inserir(conexao, [lancamento()])
        with db.banco(self.caminho) as conexao:
            self.assertEqual(db.contar(conexao), 1)
            self.assertEqual(db.listar(conexao)[0]["descricao"], "teste")

    def test_duplicata_mesmo_valor_ate_um_dia_de_distancia(self):
        with db.banco(self.caminho) as conexao:
            db.inserir(conexao, [lancamento(data="2026-08-10", valor=45.9)])
            self.assertIsNotNone(db.existe_duplicata(conexao, "2026-08-10", 45.9))
            self.assertIsNotNone(db.existe_duplicata(conexao, "2026-08-11", 45.9))
            self.assertIsNotNone(db.existe_duplicata(conexao, "2026-08-09", 45.9))
            self.assertIsNone(db.existe_duplicata(conexao, "2026-08-12", 45.9))
            self.assertIsNone(db.existe_duplicata(conexao, "2026-08-10", 45.91))

    def test_remover_grupo_apaga_todas_as_parcelas(self):
        grupo = uuid.uuid4().hex
        with db.banco(self.caminho) as conexao:
            db.inserir(conexao, [lancamento(grupoParcelamento=grupo, data=f"2026-0{m}-05")
                                 for m in range(1, 5)])
            alvo = db.listar(conexao)[0]
            self.assertEqual(db.remover_grupo(conexao, alvo["grupoParcelamento"]), 4)
            self.assertEqual(db.contar(conexao), 0)

    def test_configuracao_chave_valor(self):
        with db.banco(self.caminho) as conexao:
            self.assertIsNone(db.ler_config(conexao, "toolcall_ok"))
            db.definir_config(conexao, "toolcall_ok", "1")
            db.definir_config(conexao, "toolcall_ok", "0")
            self.assertEqual(db.ler_config(conexao, "toolcall_ok"), "0")


class TestExportacao(unittest.TestCase):
    def test_export_gera_csv_relegivel_pelo_importador(self):
        from caderno_financeiro import importador

        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        origem = Path(pasta.name) / "origem.db"
        destino_csv = Path(pasta.name) / "backup.csv"
        outro = Path(pasta.name) / "outro.db"

        with db.banco(origem) as conexao:
            db.inserir(conexao, [
                lancamento(valor=45.9, data="2026-08-01"),
                lancamento(valor=1200.0, data="2026-08-05", tipo="Receita",
                           categoria="Salário", conta="Itaú"),
            ])
            lancamentos = db.listar(conexao)

        self.assertEqual(exportador.escrever(lancamentos, destino_csv), 2)

        with db.banco(outro) as conexao:
            relatorio = importador.importar(conexao, destino_csv)
            self.assertEqual(relatorio["importados"], 2)
            reimportados = {l["valor"]: l for l in db.listar(conexao)}
        self.assertEqual(reimportados[45.9]["categoria"], "Mercado")
        self.assertEqual(reimportados[1200.0]["tipo"], "Receita")
        self.assertEqual(reimportados[1200.0]["conta"], "Itaú")


class TestEstatisticas(unittest.TestCase):
    def test_resumo_mensal(self):
        lancamentos = [
            lancamento(data="2026-08-01", valor=200.0),
            lancamento(data="2026-08-02", valor=100.0, categoria="Lazer", formaPagamento="VR",
                       conta="Ifood"),
            lancamento(data="2026-08-03", valor=3000.0, tipo="Receita", categoria="Salário"),
            lancamento(data="2026-07-15", valor=150.0),
        ]
        resumo = resumo_mensal(lancamentos, "2026-08")
        self.assertEqual(resumo["totalDespesas"], 300.0)
        self.assertEqual(resumo["totalReceitas"], 3000.0)
        self.assertEqual(resumo["saldo"], 2700.0)
        self.assertEqual(resumo["porCategoria"][0]["grupo"], "Mercado")
        self.assertEqual(resumo["mesAnterior"]["totalDespesas"], 150.0)
        self.assertEqual(resumo["mesAnterior"]["variacaoPercentual"], 100.0)


if __name__ == "__main__":
    unittest.main()
