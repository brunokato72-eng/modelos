import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from caderno_financeiro import cli, db


def _capturar(funcao, args) -> str:
    saida = io.StringIO()
    with redirect_stdout(saida):
        funcao(args)
    return saida.getvalue()


class TestCmdOrcamento(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_definir_com_json_atualiza_o_banco(self):
        args = argparse.Namespace(banco=self.banco, definir=["Mercado", "800"], remover=None, mes=None, json=False)
        codigo = cli.cmd_orcamento(args)
        self.assertEqual(codigo, 0)
        with db.banco(self.banco) as conexao:
            orcamentos = db.listar_orcamentos(conexao)
        self.assertEqual(orcamentos[0]["categoria"], "Mercado")
        self.assertEqual(orcamentos[0]["limite"], 800.0)

    def test_definir_aceita_virgula_decimal(self):
        args = argparse.Namespace(banco=self.banco, definir=["Mercado", "800,50"], remover=None, mes=None, json=False)
        cli.cmd_orcamento(args)
        with db.banco(self.banco) as conexao:
            orcamentos = db.listar_orcamentos(conexao)
        self.assertEqual(orcamentos[0]["limite"], 800.5)

    def test_definir_categoria_invalida_retorna_erro(self):
        args = argparse.Namespace(banco=self.banco, definir=["Não Existe", "800"], remover=None, mes=None, json=False)
        codigo = cli.cmd_orcamento(args)
        self.assertEqual(codigo, 1)

    def test_remover_categoria_inexistente_retorna_erro(self):
        args = argparse.Namespace(banco=self.banco, definir=None, remover="Mercado", mes=None, json=False)
        codigo = cli.cmd_orcamento(args)
        self.assertEqual(codigo, 1)

    def test_json_mostra_progresso(self):
        with db.banco(self.banco) as conexao:
            db.definir_orcamento(conexao, "Mercado", 800.0)
            db.inserir(conexao, [{
                "id": "a", "data": "2026-08-05", "tipo": "Despesa", "categoria": "Mercado",
                "valor": 300.0, "valorTotal": 300.0, "parcelaAtual": 1, "totalParcelas": 1,
                "formaPagamento": "Pix", "conta": "Nubank", "descricao": "compras",
                "criadoEm": "2026-08-05T09:00:00", "grupoParcelamento": None,
            }])
        args = argparse.Namespace(banco=self.banco, definir=None, remover=None, mes="2026-08", json=True)
        saida = _capturar(cli.cmd_orcamento, args)
        progresso = json.loads(saida)
        self.assertEqual(progresso[0]["gasto"], 300.0)


class TestCmdSaude(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_json_reporta_pontuacao(self):
        with db.banco(self.banco) as conexao:
            db.inserir(conexao, [{
                "id": "a", "data": "2026-08-01", "tipo": "Receita", "categoria": "Salário",
                "valor": 5000.0, "valorTotal": 5000.0, "parcelaAtual": 1, "totalParcelas": 1,
                "formaPagamento": "Pix", "conta": "Nubank", "descricao": "salário",
                "criadoEm": "2026-08-01T09:00:00", "grupoParcelamento": None,
            }])
        args = argparse.Namespace(banco=self.banco, mes="2026-08", json=True)
        saida = _capturar(cli.cmd_saude, args)
        dados = json.loads(saida)
        self.assertEqual(dados["pontuacao"], 100)
        self.assertEqual(dados["classificacao"], "boa")


class TestCmdInvestimentos(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_sem_posicoes(self):
        args = argparse.Namespace(banco=self.banco, json=False)
        saida = _capturar(cli.cmd_investimentos, args)
        self.assertIn("nenhuma posição", saida)

    def test_lista_e_soma_total(self):
        with db.banco(self.banco) as conexao:
            db.inserir_posicoes_investimento(conexao, [
                {"id": "p1", "data": "2026-08-01", "itemId": "item-1", "conta": "XP",
                 "tipo": "Renda Fixa", "nome": "CDB Banco X", "valor": 1000.0},
                {"id": "p2", "data": "2026-08-01", "itemId": "item-1", "conta": "XP",
                 "tipo": "Ações", "nome": "PETR4", "valor": 500.0},
            ])
        args = argparse.Namespace(banco=self.banco, json=True)
        saida = _capturar(cli.cmd_investimentos, args)
        posicoes = json.loads(saida)
        self.assertEqual(len(posicoes), 2)
        self.assertEqual(sum(p["valor"] for p in posicoes), 1500.0)


if __name__ == "__main__":
    unittest.main()
