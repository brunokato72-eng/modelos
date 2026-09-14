import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from caderno_financeiro import cli, db, pluggy_sync
from caderno_financeiro import pluggy_cliente as pc


def lancamento(**campos):
    base = {
        "id": "a", "data": "2026-08-10", "tipo": "Despesa",
        "categoria": "Outros", "valor": 50.0, "valorTotal": 50.0,
        "parcelaAtual": 1, "totalParcelas": 1, "formaPagamento": "Pix",
        "conta": "Nubank", "descricao": "teste", "criadoEm": "2026-08-10T09:00:00",
        "grupoParcelamento": None, "origem": "pluggy", "origemId": "tx-1",
        "revisaoPendente": 1,
    }
    base.update(campos)
    return base


def _capturar(funcao, args) -> str:
    saida = io.StringIO()
    with redirect_stdout(saida):
        funcao(args)
    return saida.getvalue()


class TestCmdPluggySincronizar(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_json_reporta_resumo_da_sincronizacao(self):
        resumo = {"itemsProcessados": 1, "transacoesNovas": 2, "paraRevisao": 1, "erros": []}
        args = argparse.Namespace(banco=self.banco, investimentos=False, json=True)
        with mock.patch.object(pluggy_sync, "sincronizar", return_value=resumo):
            saida = _capturar(cli.cmd_pluggy_sincronizar, args)
        self.assertEqual(json.loads(saida), resumo)

    def test_erro_pluggy_reporta_e_retorna_1(self):
        args = argparse.Namespace(banco=self.banco, investimentos=False, json=False)
        with mock.patch.object(pluggy_sync, "sincronizar", side_effect=pc.ErroPluggy("sem credenciais")):
            saida = io.StringIO()
            with redirect_stdout(saida):
                codigo = cli.cmd_pluggy_sincronizar(args)
        self.assertEqual(codigo, 1)
        self.assertIn("sem credenciais", saida.getvalue())

    def test_flag_investimentos_tambem_sincroniza(self):
        resumo = {"itemsProcessados": 1, "transacoesNovas": 0, "paraRevisao": 0, "erros": []}
        args = argparse.Namespace(banco=self.banco, investimentos=True, json=True)
        with mock.patch.object(pluggy_sync, "sincronizar", return_value=resumo), \
             mock.patch.object(pluggy_sync, "sincronizar_investimentos", return_value=3) as inv_mock:
            saida = _capturar(cli.cmd_pluggy_sincronizar, args)
        inv_mock.assert_called_once()
        self.assertEqual(json.loads(saida)["investimentosSincronizados"], 3)


class TestCmdPluggyStatus(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_json_mostra_conexoes_e_pendentes(self):
        with db.banco(self.banco) as conexao:
            db.registrar_conexao_pluggy(conexao, "item-1", "Nubank")
            db.inserir(conexao, [lancamento()])
        args = argparse.Namespace(banco=self.banco, json=True)
        saida = _capturar(cli.cmd_pluggy_status, args)
        dados = json.loads(saida)
        self.assertEqual(len(dados["conexoes"]), 1)
        self.assertEqual(dados["conexoes"][0]["instituicao"], "Nubank")
        self.assertEqual(dados["pendentesRevisao"], 1)


class TestCmdRevisar(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_resolve_por_id_e_categoria(self):
        with db.banco(self.banco) as conexao:
            db.inserir(conexao, [lancamento(id="a")])
        args = argparse.Namespace(banco=self.banco, id="a", categoria="Besteiras", json=False)
        codigo = cli.cmd_revisar(args)
        self.assertEqual(codigo, 0)
        with db.banco(self.banco) as conexao:
            atualizado = db.buscar(conexao, "a")
        self.assertEqual(atualizado["categoria"], "Besteiras")
        self.assertEqual(atualizado["revisaoPendente"], 0)

    def test_resolve_id_inexistente_retorna_erro(self):
        args = argparse.Namespace(banco=self.banco, id="nao-existe", categoria="Outros", json=False)
        codigo = cli.cmd_revisar(args)
        self.assertEqual(codigo, 1)

    def test_id_sem_categoria_retorna_erro(self):
        args = argparse.Namespace(banco=self.banco, id="a", categoria=None, json=False)
        codigo = cli.cmd_revisar(args)
        self.assertEqual(codigo, 1)

    def test_json_lista_pendentes_sem_interacao(self):
        with db.banco(self.banco) as conexao:
            db.inserir(conexao, [lancamento(id="a")])
        args = argparse.Namespace(banco=self.banco, id=None, categoria=None, json=True)
        saida = _capturar(cli.cmd_revisar, args)
        dados = json.loads(saida)
        self.assertEqual(len(dados), 1)
        self.assertEqual(dados[0]["id"], "a")

    def test_sem_pendencias_nao_ha_nada_pra_revisar(self):
        args = argparse.Namespace(banco=self.banco, id=None, categoria=None, json=False)
        saida = _capturar(cli.cmd_revisar, args)
        self.assertIn("nada pendente", saida)


if __name__ == "__main__":
    unittest.main()
