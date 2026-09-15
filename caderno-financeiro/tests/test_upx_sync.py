import tempfile
import unittest
from pathlib import Path
from unittest import mock

from caderno_financeiro import config, db, ia, upx_sync


def resposta_upx(dados):
    """Simula o formato de retorno de ia.chamar() (o dict cru do CLI) —
    upx_sync usa ia.json_da_resposta() por cima, então só precisamos que o
    texto do 'result' seja o JSON esperado."""
    import json
    return {"result": json.dumps(dados)}


class TestSincronizar(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_transacao_com_confianca_alta_e_gravada_direto(self):
        transacoes = [{
            "transactionId": "tx-1", "instituicao": "Nubank", "contaCategoria": "checking",
            "contaNome": "Conta Corrente", "data": "2026-09-10", "tipo": "Despesa",
            "valor": 24.9, "descricao": "UBER TRIP",
        }]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar", return_value=resposta_upx({"transacoes": transacoes})), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Transporte", "confianca": 0.95}]):
            resultado = upx_sync.sincronizar(conexao)
            lancamentos = db.listar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 1)
        self.assertEqual(resultado["paraRevisao"], 0)
        self.assertEqual(len(lancamentos), 1)
        lanc = lancamentos[0]
        self.assertEqual(lanc["categoria"], "Transporte")
        self.assertEqual(lanc["valor"], 24.9)
        self.assertEqual(lanc["tipo"], "Despesa")
        self.assertEqual(lanc["origem"], "upx")
        self.assertEqual(lanc["origemId"], "tx-1")
        self.assertEqual(lanc["revisaoPendente"], 0)
        self.assertEqual(lanc["conta"], "Conta Corrente")

    def test_transacao_com_confianca_baixa_vai_pra_fila_de_revisao(self):
        transacoes = [{
            "transactionId": "tx-2", "instituicao": "Nubank", "contaCategoria": "checking",
            "contaNome": "Conta Corrente", "data": "2026-09-10", "tipo": "Despesa",
            "valor": 50.0, "descricao": "PAG*ALGUEM",
        }]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar", return_value=resposta_upx({"transacoes": transacoes})), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Pessoal", "confianca": 0.2}]):
            resultado = upx_sync.sincronizar(conexao)
            lancamentos = db.listar(conexao)

        self.assertEqual(resultado["paraRevisao"], 1)
        self.assertEqual(lancamentos[0]["revisaoPendente"], 1)

    def test_conta_credito_vira_cartao_de_credito(self):
        transacoes = [{
            "transactionId": "tx-3", "instituicao": "Nubank", "contaCategoria": "credit_card",
            "contaNome": "platinum", "data": "2026-09-10", "tipo": "Despesa",
            "valor": 39.9, "descricao": "NETFLIX",
        }]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar", return_value=resposta_upx({"transacoes": transacoes})), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Assinaturas", "confianca": 0.9}]):
            resultado = upx_sync.sincronizar(conexao)
            lancamentos = db.listar(conexao)

        self.assertEqual(lancamentos[0]["formaPagamento"], config.FORMA_PADRAO)
        self.assertEqual(lancamentos[0]["conta"], "platinum")

    def test_descricao_com_pix_mapeia_forma_pix(self):
        transacoes = [{
            "transactionId": "tx-4", "instituicao": "Nubank", "contaCategoria": "checking",
            "contaNome": "Conta Corrente", "data": "2026-09-10", "tipo": "Despesa",
            "valor": 30.0, "descricao": "PIX ENVIADO JOAO",
        }]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar", return_value=resposta_upx({"transacoes": transacoes})), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Outros", "confianca": 0.4}]):
            resultado = upx_sync.sincronizar(conexao)
            lancamentos = db.listar(conexao)

        self.assertEqual(lancamentos[0]["formaPagamento"], "Pix")

    def test_transacao_ja_importada_nao_e_reimportada(self):
        transacoes = [{
            "transactionId": "tx-5", "instituicao": "Nubank", "contaCategoria": "checking",
            "contaNome": "Conta Corrente", "data": "2026-09-10", "tipo": "Despesa",
            "valor": 10.0, "descricao": "UBER",
        }]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar", return_value=resposta_upx({"transacoes": transacoes})), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Transporte", "confianca": 0.9}]):
            upx_sync.sincronizar(conexao)

        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar", return_value=resposta_upx({"transacoes": transacoes})), \
             mock.patch.object(ia, "categorizar_transacoes") as ia_mock:
            resultado = upx_sync.sincronizar(conexao)
            total = db.contar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 0)
        self.assertEqual(total, 1)
        ia_mock.assert_not_called()

    def test_transacao_sem_id_e_ignorada_sem_quebrar(self):
        transacoes = [
            {"instituicao": "Nubank", "contaCategoria": "checking", "contaNome": "Conta",
             "data": "2026-09-10", "tipo": "Despesa", "valor": 10.0, "descricao": "sem id"},
            {"transactionId": "tx-6", "instituicao": "Nubank", "contaCategoria": "checking",
             "contaNome": "Conta", "data": "2026-09-10", "tipo": "Despesa", "valor": 20.0, "descricao": "com id"},
        ]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar", return_value=resposta_upx({"transacoes": transacoes})), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Outros", "confianca": 0.9}]):
            resultado = upx_sync.sincronizar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 1)

    def test_atualiza_data_da_ultima_sincronizacao(self):
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar", return_value=resposta_upx({"transacoes": []})):
            upx_sync.sincronizar(conexao)
            self.assertIsNotNone(db.ler_config(conexao, "upx_ultima_sincronizacao"))

    def test_usa_data_da_ultima_sincronizacao_no_prompt(self):
        with db.banco(self.banco) as conexao:
            db.definir_config(conexao, "upx_ultima_sincronizacao", "2026-08-01")
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar", return_value=resposta_upx({"transacoes": []})) as chamar_mock:
            upx_sync.sincronizar(conexao)
        prompt_usado = chamar_mock.call_args[0][0]
        self.assertIn("2026-08-01", prompt_usado)


class TestSincronizarInvestimentos(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_grava_snapshot_das_posicoes(self):
        posicoes = [
            {"holdingId": "h1", "instituicao": "Clear", "nome": "PETR4", "tipo": "Ações",
             "valor": 500.0, "quantidade": 20},
            {"holdingId": "h2", "instituicao": "Clear", "nome": "Tesouro Selic", "tipo": "Renda Fixa",
             "valor": 1000.0, "quantidade": None},
        ]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar", return_value=resposta_upx({"posicoes": posicoes})):
            total = upx_sync.sincronizar_investimentos(conexao)
            gravadas = db.listar_posicoes_investimento(conexao)

        self.assertEqual(total, 2)
        self.assertEqual(len(gravadas), 2)
        valores = {p["nome"]: p["valor"] for p in gravadas}
        self.assertEqual(valores["PETR4"], 500.0)
        self.assertEqual(valores["Tesouro Selic"], 1000.0)

    def test_sem_posicoes_devolve_zero(self):
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar", return_value=resposta_upx({"posicoes": []})):
            total = upx_sync.sincronizar_investimentos(conexao)
        self.assertEqual(total, 0)

    def test_posicao_sem_id_e_ignorada_sem_quebrar(self):
        posicoes = [
            {"instituicao": "Clear", "nome": "sem id", "tipo": "Ações", "valor": 100.0, "quantidade": 1},
            {"holdingId": "h3", "instituicao": "Clear", "nome": "com id", "tipo": "Ações",
             "valor": 200.0, "quantidade": 2},
        ]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar", return_value=resposta_upx({"posicoes": posicoes})):
            total = upx_sync.sincronizar_investimentos(conexao)
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
