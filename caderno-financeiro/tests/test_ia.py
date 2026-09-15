import json
import unittest
from unittest import mock

from caderno_financeiro import ia


def _processo_falso(saida):
    return mock.Mock(returncode=0, stdout=json.dumps({"result": saida}), stderr="")


class TestChamarConstroiComandoCerto(unittest.TestCase):
    """`chamar()` nunca dá acesso a ferramenta nenhuma por padrão — a exceção
    (MCP autorizado na conta, tipo UPX Financial) tem que ser explícita via
    `mcp_da_conta=True`, senão a chamada continua isolada como sempre foi."""

    def setUp(self):
        patch_bin = mock.patch.object(ia, "binario_claude", return_value="claude")
        patch_bin.start()
        self.addCleanup(patch_bin.stop)

    def test_chamada_padrao_leva_strict_mcp_config_e_sem_ferramentas(self):
        with mock.patch("subprocess.run", return_value=_processo_falso("ok")) as run_mock:
            ia.chamar("oi", sistema="sistema", modelo="haiku")
        comando = run_mock.call_args[0][0]
        self.assertIn("--strict-mcp-config", comando)
        self.assertIn("--tools", comando)
        self.assertNotIn("--allowed-tools", comando)

    def test_mcp_da_conta_omite_strict_mcp_config_e_libera_ferramenta(self):
        with mock.patch("subprocess.run", return_value=_processo_falso("ok")) as run_mock:
            ia.chamar(
                "oi", sistema="sistema", modelo="haiku",
                ferramentas=["mcp__UPX_Financial__finance_transactions_list"],
                mcp_da_conta=True,
            )
        comando = run_mock.call_args[0][0]
        self.assertNotIn("--strict-mcp-config", comando)
        self.assertIn("--allowed-tools", comando)
        indice = comando.index("--allowed-tools")
        self.assertEqual(comando[indice + 1], "mcp__UPX_Financial__finance_transactions_list")

    def test_ferramentas_sem_mcp_da_conta_ainda_leva_strict_mcp_config(self):
        """Passar `ferramentas` sozinho (sem `mcp_da_conta`) não muda o
        isolamento padrão — é o padrão já usado por outras chamadas do
        projeto (ex.: consulta.testar_toolcall, que usa MCP local, não de
        conta)."""
        with mock.patch("subprocess.run", return_value=_processo_falso("ok")) as run_mock:
            ia.chamar("oi", sistema="sistema", modelo="haiku", ferramentas=["calcular"])
        comando = run_mock.call_args[0][0]
        self.assertIn("--strict-mcp-config", comando)


if __name__ == "__main__":
    unittest.main()
