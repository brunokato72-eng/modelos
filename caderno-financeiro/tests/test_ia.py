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


def _resposta_categorizacao(classificacoes):
    return _processo_falso(json.dumps({"classificacoes": classificacoes}))


class TestCategorizarTransacoesEmLotes(unittest.TestCase):
    """Uma carga de histórico grande pode gerar centenas de transações
    pendentes numa sincronização só — categorizar tudo numa chamada arrisca
    estourar timeout (ou, em volumes maiores, o teto de tokens de saída).
    `categorizar_transacoes` precisa dividir em lotes sozinho, sem que quem
    chama (upx_sync, pluggy_sync) precise saber disso."""

    def setUp(self):
        patch_bin = mock.patch.object(ia, "binario_claude", return_value="claude")
        patch_bin.start()
        self.addCleanup(patch_bin.stop)

    def _transacao(self, i):
        return {"descricao": f"loja {i}", "valor": 10.0 + i, "tipo": "Despesa"}

    def test_lote_pequeno_faz_uma_unica_chamada(self):
        transacoes = [self._transacao(i) for i in range(5)]
        classificacoes = [{"indice": i, "categoria": "Outros", "confianca": 0.5} for i in range(5)]
        with mock.patch("subprocess.run", return_value=_resposta_categorizacao(classificacoes)) as run_mock:
            resultado = ia.categorizar_transacoes(transacoes)
        self.assertEqual(run_mock.call_count, 1)
        self.assertEqual(len(resultado), 5)

    def test_lote_grande_e_dividido_em_varias_chamadas(self):
        total = ia._LOTE_CATEGORIZACAO + 15  # força duas chamadas
        transacoes = [self._transacao(i) for i in range(total)]

        def _side_effect(comando, input, **kwargs):
            # cada chamada só vê seu próprio pedaço — os índices no prompt
            # sempre começam do 0 dentro do lote.
            n_neste_lote = input.count(". [")
            classificacoes = [
                {"indice": i, "categoria": "Outros", "confianca": 0.5} for i in range(n_neste_lote)
            ]
            return _resposta_categorizacao(classificacoes)

        with mock.patch("subprocess.run", side_effect=_side_effect) as run_mock:
            resultado = ia.categorizar_transacoes(transacoes)

        self.assertEqual(run_mock.call_count, 2)
        self.assertEqual(len(resultado), total)
        self.assertTrue(all(c["categoria"] == "Outros" for c in resultado))


if __name__ == "__main__":
    unittest.main()
