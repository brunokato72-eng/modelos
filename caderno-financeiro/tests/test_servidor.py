"""Testes da API HTTP. Registrar/perguntar são dublados (não chamam o modelo)."""

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from caderno_financeiro import auth, config, db, ia
from caderno_financeiro.servidor import criar_app


def lancamento(**campos):
    base = {
        "id": uuid.uuid4().hex, "data": "2026-08-10", "tipo": "Despesa",
        "categoria": "Mercado", "valor": 50.0, "valorTotal": 50.0,
        "parcelaAtual": 1, "totalParcelas": 1, "formaPagamento": "Pix",
        "conta": "Nubank", "descricao": "teste", "criadoEm": "2026-08-10T09:00:00",
        "grupoParcelamento": None,
    }
    base.update(campos)
    return base


class TestServidor(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"
        self.patcher = mock.patch.object(config, "caminho_banco", return_value=self.banco)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

        with db.banco(self.banco) as conexao:
            auth.definir_pin(conexao, "1234")
            db.inserir(conexao, [lancamento()])

        self.app = criar_app()
        self.cliente = self.app.test_client()
        self.token = self.cliente.post("/api/auth/login", json={"pin": "1234"}).get_json()["token"]
        self.auth = {"Authorization": f"Bearer {self.token}"}

    def test_rota_de_dados_sem_token_da_401(self):
        r = self.cliente.get("/api/resumo")
        self.assertEqual(r.status_code, 401)

    def test_login_com_pin_errado(self):
        r = self.cliente.post("/api/auth/login", json={"pin": "0000"})
        self.assertEqual(r.status_code, 401)

    def test_login_certo_devolve_token_utilizavel(self):
        r = self.cliente.get("/api/resumo?mes=2026-08", headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["totalDespesas"], 50.0)

    def test_token_revogado_apos_logout(self):
        self.cliente.post("/api/auth/logout", headers=self.auth)
        r = self.cliente.get("/api/resumo", headers=self.auth)
        self.assertEqual(r.status_code, 401)

    def test_listar_e_calcular(self):
        r = self.cliente.get("/api/listar?mes=2026-08", headers=self.auth)
        self.assertEqual(len(r.get_json()), 1)

        r = self.cliente.get("/api/calcular?operacao=soma&tipo=Despesa", headers=self.auth)
        self.assertEqual(r.get_json()["resultado"], 50.0)

    def test_exportar_devolve_csv_com_cabecalho(self):
        r = self.cliente.get("/api/exportar", headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data.decode("utf-8").startswith("data,tipo,categoria"))

    def test_panorama(self):
        r = self.cliente.get("/api/panorama", headers=self.auth)
        corpo = r.get_json()
        self.assertIn("Mercado", corpo["categoriasDespesa"])
        self.assertEqual(corpo["totalLancamentos"], 1)

    def test_remover_lancamento(self):
        r = self.cliente.delete("/api/lancamentos/x-nao-existe", headers=self.auth)
        self.assertEqual(r.status_code, 404)

        with db.banco(self.banco) as conexao:
            existente = db.listar(conexao)[0]["id"]
        r = self.cliente.delete(f"/api/lancamentos/{existente}", headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["removidos"], 1)

    def test_registrar_usa_extracao_dublada_e_aplica_regras(self):
        extraido = {"lancamentos": [{
            "tipo": "Despesa", "categoria": "Alimentação", "valorTotal": 30.0,
            "totalParcelas": 1, "data": "2026-08-31",
            "formaPagamento": None, "conta": "Ifood", "descricao": "almoço",
        }], "observacao": ""}
        with mock.patch.object(ia, "extrair_lancamentos", return_value=extraido):
            r = self.cliente.post("/api/registrar", json={"texto": "almoço 30 no ifood"}, headers=self.auth)
        self.assertEqual(r.status_code, 200)
        corpo = r.get_json()
        self.assertTrue(corpo["salvo"])
        self.assertEqual(corpo["grupos"][0]["linhas"][0]["formaPagamento"], "VR")

    def test_registrar_sem_confirmar_nao_grava(self):
        extraido = {"lancamentos": [{
            "tipo": "Despesa", "categoria": "Mercado", "valorTotal": 10.0,
            "totalParcelas": 1, "data": "2026-08-31",
            "formaPagamento": None, "conta": None, "descricao": "x",
        }], "observacao": ""}
        with mock.patch.object(ia, "extrair_lancamentos", return_value=extraido):
            r = self.cliente.post(
                "/api/registrar", json={"texto": "x", "confirmar": False}, headers=self.auth
            )
        self.assertFalse(r.get_json()["salvo"])
        with db.banco(self.banco) as conexao:
            self.assertEqual(db.contar(conexao), 1)  # só o lançamento do setUp

    def test_perguntar_usa_modo_manual_dublado(self):
        plano = [{"rotulo": "total", "operacao": "soma", "tipo": "Despesa",
                  "mesInicio": "2026-08", "mesFim": "2026-08", "categoria": None,
                  "formaPagamento": None, "conta": None, "descricaoContem": None,
                  "agruparPor": None}]
        with mock.patch.object(ia, "planejar_consultas", return_value=plano), \
             mock.patch.object(ia, "redigir_resposta", return_value="você gastou R$ 50,00"):
            r = self.cliente.post(
                "/api/perguntar", json={"pergunta": "quanto gastei?", "modo": "manual"},
                headers=self.auth,
            )
        self.assertEqual(r.status_code, 200)
        self.assertIn("R$ 50,00", r.get_json()["resposta"])

    def test_perguntar_com_caderno_vazio_da_409(self):
        with db.banco(self.banco) as conexao:
            db.remover(conexao, db.listar(conexao)[0]["id"])
        r = self.cliente.post("/api/perguntar", json={"pergunta": "e ai?"}, headers=self.auth)
        self.assertEqual(r.status_code, 409)




class TestSessaoNaoSeguraTransacao(unittest.TestCase):
    """Regressão: sessao_valida() é chamada no before_request de TODO
    request, inclusive os lentos (registrar/perguntar, que ficam segundos
    esperando o CLI `claude`). Se ela deixa uma transação aberta, essa
    transação fica pendurada pela duração inteira do request lento — e
    qualquer outro request concorrente que também precise escrever
    (a própria checagem de sessão dele) trava com "database is locked"
    esperando ela liberar. O teste checa a causa raiz diretamente:
    `conexao.in_transaction` tem que ser False assim que a função retorna,
    não só quando o request inteiro termina."""

    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_nao_deixa_transacao_aberta_apos_validar(self):
        with db.banco(self.banco) as conexao:
            auth.definir_pin(conexao, "1234")
            token = auth.criar_sessao(conexao)
            conexao.commit()

            self.assertTrue(auth.sessao_valida(conexao, token))
            self.assertFalse(
                conexao.in_transaction,
                "sessao_valida() deixou uma transação (o UPDATE de ultimo_uso) "
                "aberta — isso trava qualquer request concorrente que também "
                "precise escrever, pela duração inteira de um request lento.",
            )

    def test_nao_deixa_transacao_aberta_em_token_expirado(self):
        with db.banco(self.banco) as conexao:
            auth.definir_pin(conexao, "1234")
            token = auth.criar_sessao(conexao)
            conexao.execute(
                "UPDATE sessoes SET expira_em = '2000-01-01T00:00:00' WHERE token = ?", (token,)
            )
            conexao.commit()

            self.assertFalse(auth.sessao_valida(conexao, token))
            self.assertFalse(conexao.in_transaction)

    def test_segunda_conexao_nao_bloqueia_enquanto_a_primeira_so_valida_sessao(self):
        """Prova de ponta a ponta com duas conexões de verdade (não só uma):
        depois de validar a sessão na conexão A, uma escrita concorrente na
        conexão B (simulando outro request) não pode travar esperando A."""
        with db.banco(self.banco) as conexao_setup:
            auth.definir_pin(conexao_setup, "1234")
            token = auth.criar_sessao(conexao_setup)

        conexao_a = db.conectar(self.banco, criar_esquema=False)
        conexao_b = db.conectar(self.banco, criar_esquema=False)
        try:
            self.assertTrue(auth.sessao_valida(conexao_a, token))
            # se sessao_valida ainda segurasse a transação, esta escrita na
            # conexão B travaria (ou estouraria o busy_timeout)
            conexao_b.execute(
                "INSERT INTO configuracao (chave, valor) VALUES ('teste', '1')"
            )
            conexao_b.commit()
        finally:
            conexao_a.close()
            conexao_b.close()


if __name__ == "__main__":
    unittest.main()
