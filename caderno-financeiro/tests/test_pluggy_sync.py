import tempfile
import unittest
from pathlib import Path
from unittest import mock

from caderno_financeiro import config, db, ia, pluggy_sync
from caderno_financeiro import pluggy_cliente as pc


ITEM_NUBANK = {"id": "item-1", "connector": {"name": "Nubank"}}
CONTA_CORRENTE = {"id": "conta-1", "type": "BANK", "name": "Conta Corrente"}
CARTAO = {"id": "conta-2", "type": "CREDIT", "name": "Cartão Nubank"}


class TestSincronizar(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def _rodar_com_dubles(self, transacoes_por_conta, classificacoes):
        with db.banco(self.banco) as conexao, \
             mock.patch.object(pc, "obter_api_key", return_value="chave-falsa"), \
             mock.patch.object(pc, "listar_items", return_value=[ITEM_NUBANK]), \
             mock.patch.object(pc, "listar_contas", return_value=[CONTA_CORRENTE, CARTAO]), \
             mock.patch.object(pc, "listar_transacoes", side_effect=lambda k, conta_id, **kw: transacoes_por_conta.get(conta_id, [])), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=classificacoes):
            resultado = pluggy_sync.sincronizar(conexao)
            lancamentos = db.listar(conexao)
        return resultado, lancamentos

    def test_transacao_com_confianca_alta_e_gravada_direto(self):
        transacoes = {
            "conta-1": [{"id": "tx-1", "date": "2026-08-10", "description": "UBER TRIP", "amount": -24.9}],
        }
        resultado, lancamentos = self._rodar_com_dubles(
            transacoes, [{"categoria": "Transporte", "confianca": 0.95}]
        )
        self.assertEqual(resultado["transacoesNovas"], 1)
        self.assertEqual(resultado["paraRevisao"], 0)
        self.assertEqual(len(lancamentos), 1)
        lanc = lancamentos[0]
        self.assertEqual(lanc["categoria"], "Transporte")
        self.assertEqual(lanc["valor"], 24.9)
        self.assertEqual(lanc["tipo"], "Despesa")
        self.assertEqual(lanc["origem"], "pluggy")
        self.assertEqual(lanc["origemId"], "tx-1")
        self.assertEqual(lanc["revisaoPendente"], 0)
        self.assertEqual(lanc["conta"], "Conta Corrente")

    def test_transacao_com_confianca_baixa_vai_pra_fila_de_revisao(self):
        transacoes = {"conta-1": [{"id": "tx-2", "date": "2026-08-10", "description": "PAG*ALGUEM", "amount": -50.0}]}
        resultado, lancamentos = self._rodar_com_dubles(
            transacoes, [{"categoria": "Pessoal", "confianca": 0.2}]
        )
        self.assertEqual(resultado["paraRevisao"], 1)
        self.assertEqual(lancamentos[0]["revisaoPendente"], 1)

    def test_valor_positivo_e_receita(self):
        transacoes = {"conta-1": [{"id": "tx-3", "date": "2026-08-10", "description": "SALARIO", "amount": 5000.0}]}
        _, lancamentos = self._rodar_com_dubles(transacoes, [{"categoria": "Salário", "confianca": 0.99}])
        self.assertEqual(lancamentos[0]["tipo"], "Receita")
        self.assertEqual(lancamentos[0]["valor"], 5000.0)

    def test_conta_credito_vira_cartao_de_credito(self):
        transacoes = {"conta-2": [{"id": "tx-4", "date": "2026-08-10", "description": "NETFLIX", "amount": -39.9}]}
        _, lancamentos = self._rodar_com_dubles(transacoes, [{"categoria": "Assinaturas", "confianca": 0.9}])
        self.assertEqual(lancamentos[0]["formaPagamento"], config.FORMA_PADRAO)
        self.assertEqual(lancamentos[0]["conta"], "Cartão Nubank")

    def test_descricao_com_pix_mapeia_forma_pix(self):
        transacoes = {"conta-1": [{"id": "tx-5", "date": "2026-08-10", "description": "PIX ENVIADO JOAO", "amount": -30.0}]}
        _, lancamentos = self._rodar_com_dubles(transacoes, [{"categoria": "Outros", "confianca": 0.4}])
        self.assertEqual(lancamentos[0]["formaPagamento"], "Pix")

    def test_transacao_ja_importada_nao_e_reimportada(self):
        with db.banco(self.banco) as conexao:
            db.definir_config(conexao, "marcador", "1")  # garante schema criado
        transacoes = {"conta-1": [{"id": "tx-6", "date": "2026-08-10", "description": "UBER", "amount": -10.0}]}

        # primeira sincronização: grava
        self._rodar_com_dubles(transacoes, [{"categoria": "Transporte", "confianca": 0.9}])
        # segunda sincronização com a MESMA transação: não deve duplicar nem chamar a IA
        with db.banco(self.banco) as conexao, \
             mock.patch.object(pc, "obter_api_key", return_value="chave-falsa"), \
             mock.patch.object(pc, "listar_items", return_value=[ITEM_NUBANK]), \
             mock.patch.object(pc, "listar_contas", return_value=[CONTA_CORRENTE, CARTAO]), \
             mock.patch.object(pc, "listar_transacoes", side_effect=lambda k, conta_id, **kw: transacoes.get(conta_id, [])), \
             mock.patch.object(ia, "categorizar_transacoes") as ia_mock:
            resultado = pluggy_sync.sincronizar(conexao)
            total = db.contar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 0)
        self.assertEqual(total, 1)
        ia_mock.assert_not_called()

    def test_erro_numa_conexao_nao_trava_a_sincronizacao_das_outras(self):
        item_com_erro = {"id": "item-2", "connector": {"name": "BancoQuebrado"}}
        with db.banco(self.banco) as conexao, \
             mock.patch.object(pc, "obter_api_key", return_value="chave-falsa"), \
             mock.patch.object(pc, "listar_items", return_value=[ITEM_NUBANK, item_com_erro]), \
             mock.patch.object(pc, "listar_contas", side_effect=[
                 [CONTA_CORRENTE],
                 pc.ErroPluggy("timeout"),
             ]), \
             mock.patch.object(pc, "listar_transacoes", return_value=[]):
            resultado = pluggy_sync.sincronizar(conexao)

        self.assertEqual(resultado["itemsProcessados"], 1)
        self.assertEqual(len(resultado["erros"]), 1)
        self.assertIn("BancoQuebrado", resultado["erros"][0])

    def test_registra_conexao_e_atualiza_sincronizacao(self):
        with db.banco(self.banco) as conexao, \
             mock.patch.object(pc, "obter_api_key", return_value="chave-falsa"), \
             mock.patch.object(pc, "listar_items", return_value=[ITEM_NUBANK]), \
             mock.patch.object(pc, "listar_contas", return_value=[]):
            pluggy_sync.sincronizar(conexao)
            conexoes = db.listar_conexoes_pluggy(conexao)

        self.assertEqual(len(conexoes), 1)
        self.assertEqual(conexoes[0]["instituicao"], "Nubank")
        self.assertIsNotNone(conexoes[0]["ultima_sincronizacao"])


class TestSincronizarInvestimentos(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_grava_snapshot_das_posicoes(self):
        investimentos = [
            {"id": "inv-1", "name": "CDB Banco X", "type": "FIXED_INCOME", "balance": 1000.0},
            {"id": "inv-2", "name": "PETR4", "type": "EQUITY", "value": 500.0, "quantity": 20},
        ]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(pc, "obter_api_key", return_value="chave-falsa"), \
             mock.patch.object(pc, "listar_items", return_value=[ITEM_NUBANK]), \
             mock.patch.object(pc, "listar_investimentos", return_value=investimentos):
            total = pluggy_sync.sincronizar_investimentos(conexao)
            posicoes = db.listar_posicoes_investimento(conexao)

        self.assertEqual(total, 2)
        self.assertEqual(len(posicoes), 2)
        valores = {p["nome"]: p["valor"] for p in posicoes}
        self.assertEqual(valores["CDB Banco X"], 1000.0)
        self.assertEqual(valores["PETR4"], 500.0)

    def test_erro_numa_conexao_pula_pra_proxima(self):
        with db.banco(self.banco) as conexao, \
             mock.patch.object(pc, "obter_api_key", return_value="chave-falsa"), \
             mock.patch.object(pc, "listar_items", return_value=[ITEM_NUBANK]), \
             mock.patch.object(pc, "listar_investimentos", side_effect=pc.ErroPluggy("indisponível")):
            total = pluggy_sync.sincronizar_investimentos(conexao)

        self.assertEqual(total, 0)


if __name__ == "__main__":
    unittest.main()
