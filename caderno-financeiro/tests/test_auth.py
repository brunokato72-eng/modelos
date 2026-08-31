import tempfile
import unittest
from pathlib import Path

from caderno_financeiro import auth, db


class TestAutenticacao(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_pin_curto_e_rejeitado(self):
        with db.banco(self.banco) as conexao:
            with self.assertRaises(ValueError):
                auth.definir_pin(conexao, "12")

    def test_fluxo_completo_definir_conferir_sessao(self):
        with db.banco(self.banco) as conexao:
            self.assertFalse(auth.pin_configurado(conexao))
            auth.definir_pin(conexao, "2468")
            self.assertTrue(auth.pin_configurado(conexao))
            self.assertTrue(auth.conferir_pin(conexao, "2468"))
            self.assertFalse(auth.conferir_pin(conexao, "0000"))

            token = auth.criar_sessao(conexao)
            self.assertTrue(auth.sessao_valida(conexao, token))
            self.assertFalse(auth.sessao_valida(conexao, "token-que-nao-existe"))
            self.assertFalse(auth.sessao_valida(conexao, None))

    def test_redefinir_pin_revoga_sessoes_antigas(self):
        with db.banco(self.banco) as conexao:
            auth.definir_pin(conexao, "1111")
            token = auth.criar_sessao(conexao)
            self.assertTrue(auth.sessao_valida(conexao, token))
            auth.definir_pin(conexao, "2222")
            self.assertFalse(auth.sessao_valida(conexao, token))

    def test_revogar_todas_as_sessoes(self):
        with db.banco(self.banco) as conexao:
            auth.definir_pin(conexao, "1234")
            t1 = auth.criar_sessao(conexao)
            t2 = auth.criar_sessao(conexao)
            self.assertEqual(auth.revogar_todas_as_sessoes(conexao), 2)
            self.assertFalse(auth.sessao_valida(conexao, t1))
            self.assertFalse(auth.sessao_valida(conexao, t2))

    def test_pin_nao_fica_em_texto_puro_no_banco(self):
        with db.banco(self.banco) as conexao:
            auth.definir_pin(conexao, "9999")
        conteudo = self.banco.read_bytes()
        self.assertNotIn(b"9999", conteudo)


if __name__ == "__main__":
    unittest.main()
