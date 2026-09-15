import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from caderno_financeiro import cli, ia, upx_sync


def _capturar(funcao, args) -> str:
    saida = io.StringIO()
    with redirect_stdout(saida):
        funcao(args)
    return saida.getvalue()


class TestCmdUpxSincronizar(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_json_reporta_resumo_da_sincronizacao(self):
        resumo = {"transacoesNovas": 2, "paraRevisao": 1}
        args = argparse.Namespace(banco=self.banco, investimentos=False, desde=None, ate=None, json=True)
        with mock.patch.object(upx_sync, "sincronizar", return_value=resumo):
            saida = _capturar(cli.cmd_upx_sincronizar, args)
        self.assertEqual(json.loads(saida), resumo)

    def test_erro_ia_reporta_e_retorna_1(self):
        args = argparse.Namespace(banco=self.banco, investimentos=False, desde=None, ate=None, json=False)
        with mock.patch.object(upx_sync, "sincronizar", side_effect=ia.ErroIA("sem login")):
            saida = io.StringIO()
            with redirect_stdout(saida):
                codigo = cli.cmd_upx_sincronizar(args)
        self.assertEqual(codigo, 1)
        self.assertIn("sem login", saida.getvalue())

    def test_flag_investimentos_tambem_sincroniza(self):
        resumo = {"transacoesNovas": 0, "paraRevisao": 0}
        args = argparse.Namespace(banco=self.banco, investimentos=True, desde=None, ate=None, json=True)
        with mock.patch.object(upx_sync, "sincronizar", return_value=resumo), \
             mock.patch.object(upx_sync, "sincronizar_investimentos", return_value=3) as inv_mock:
            saida = _capturar(cli.cmd_upx_sincronizar, args)
        inv_mock.assert_called_once()
        self.assertEqual(json.loads(saida)["investimentosSincronizados"], 3)

    def test_desde_e_ate_repassados_pra_sincronizar(self):
        resumo = {"transacoesNovas": 0, "paraRevisao": 0}
        args = argparse.Namespace(
            banco=self.banco, investimentos=False, desde="2020-01-01", ate="2027-12-31", json=True
        )
        with mock.patch.object(upx_sync, "sincronizar", return_value=resumo) as sinc_mock:
            _capturar(cli.cmd_upx_sincronizar, args)
        sinc_mock.assert_called_once_with(mock.ANY, desde="2020-01-01", ate="2027-12-31")


if __name__ == "__main__":
    unittest.main()
