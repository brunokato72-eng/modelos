import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from caderno_financeiro import db


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


class TestMigracaoDeEsquemaAntigo(unittest.TestCase):
    def test_banco_sem_as_colunas_novas_e_migrado_sozinho(self):
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        caminho = Path(pasta.name) / "antigo.db"

        # simula um banco criado antes de origem/origem_id/revisao_pendente existirem
        conexao_crua = sqlite3.connect(str(caminho))
        conexao_crua.execute(
            """CREATE TABLE lancamentos (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, tipo TEXT NOT NULL,
                categoria TEXT NOT NULL, valor REAL NOT NULL, valor_total REAL NOT NULL,
                parcela_atual INTEGER NOT NULL DEFAULT 1, total_parcelas INTEGER NOT NULL DEFAULT 1,
                forma_pagamento TEXT NOT NULL DEFAULT '', conta TEXT NOT NULL DEFAULT '',
                descricao TEXT NOT NULL DEFAULT '', criado_em TEXT NOT NULL,
                grupo_parcelamento TEXT
            )"""
        )
        conexao_crua.execute(
            "INSERT INTO lancamentos VALUES ('x1','2026-08-01','Despesa','Mercado',10,10,1,1,'Pix','Nubank','antigo','2026-08-01T00:00:00',NULL)"
        )
        conexao_crua.commit()
        conexao_crua.close()

        with db.banco(caminho) as conexao:
            colunas = {l["name"] for l in conexao.execute("PRAGMA table_info(lancamentos)")}
            self.assertIn("origem", colunas)
            self.assertIn("origem_id", colunas)
            self.assertIn("revisao_pendente", colunas)
            registro = db.listar(conexao)[0]
            self.assertEqual(registro["descricao"], "antigo")
            self.assertEqual(registro["origem"], "manual")
            self.assertEqual(registro["revisaoPendente"], 0)


class TestFilaDeRevisao(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_lancamento_pendente_aparece_na_fila(self):
        with db.banco(self.banco) as conexao:
            db.inserir(conexao, [
                lancamento(id="a", categoria="Outros", revisaoPendente=1, origem="pluggy"),
                lancamento(id="b", categoria="Mercado", revisaoPendente=0),
            ])
            pendentes = db.listar_pendentes_revisao(conexao)
            self.assertEqual(len(pendentes), 1)
            self.assertEqual(pendentes[0]["id"], "a")
            self.assertEqual(db.contar_pendentes_revisao(conexao), 1)

    def test_resolver_revisao_atualiza_categoria_e_tira_da_fila(self):
        with db.banco(self.banco) as conexao:
            db.inserir(conexao, [lancamento(id="a", categoria="Outros", revisaoPendente=1, origem="pluggy")])
            self.assertTrue(db.resolver_revisao(conexao, "a", "Besteiras"))
            self.assertEqual(db.contar_pendentes_revisao(conexao), 0)
            atualizado = db.buscar(conexao, "a")
            self.assertEqual(atualizado["categoria"], "Besteiras")
            self.assertEqual(atualizado["revisaoPendente"], 0)

    def test_resolver_revisao_id_inexistente(self):
        with db.banco(self.banco) as conexao:
            self.assertFalse(db.resolver_revisao(conexao, "nao-existe", "Outros"))

    def test_lancamento_manual_nao_precisa_informar_origem(self):
        """registro.py e importador.py não sabem desses campos novos —
        inserir() tem que preencher os padrões sozinho."""
        with db.banco(self.banco) as conexao:
            sem_campos_novos = lancamento(id="c")
            del sem_campos_novos  # lancamento() já não inclui os campos novos por padrão
            db.inserir(conexao, [lancamento(id="c")])
            registro = db.buscar(conexao, "c")
            self.assertEqual(registro["origem"], "manual")
            self.assertEqual(registro["revisaoPendente"], 0)
            self.assertIsNone(registro["origemId"])


class TestDedupPorOrigem(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_origem_ja_importada(self):
        with db.banco(self.banco) as conexao:
            self.assertFalse(db.origem_ja_importada(conexao, "pluggy", "tx-123"))
            db.inserir(conexao, [lancamento(id="a", origem="pluggy", origemId="tx-123")])
            self.assertTrue(db.origem_ja_importada(conexao, "pluggy", "tx-123"))
            self.assertFalse(db.origem_ja_importada(conexao, "pluggy", "tx-999"))
            self.assertFalse(db.origem_ja_importada(conexao, "manual", "tx-123"))

    def test_inserir_a_mesma_origem_id_duas_vezes_da_erro_de_integridade(self):
        with db.banco(self.banco) as conexao:
            db.inserir(conexao, [lancamento(id="a", origem="pluggy", origemId="tx-123")])
            with self.assertRaises(sqlite3.IntegrityError):
                db.inserir(conexao, [lancamento(id="b", origem="pluggy", origemId="tx-123")])

    def test_origem_id_nulo_permite_varios_lancamentos_manuais(self):
        """A constraint única é (origem, origem_id) só quando origem_id não é
        nulo — lançamentos manuais (origem_id sempre NULL) não podem colidir
        entre si por causa disso."""
        with db.banco(self.banco) as conexao:
            db.inserir(conexao, [lancamento(id="a"), lancamento(id="b")])
            self.assertEqual(db.contar(conexao), 2)


class TestConexoesPluggy(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_registrar_e_listar_conexao(self):
        with db.banco(self.banco) as conexao:
            db.registrar_conexao_pluggy(conexao, "item-1", "Nubank")
            conexoes = db.listar_conexoes_pluggy(conexao)
            self.assertEqual(len(conexoes), 1)
            self.assertEqual(conexoes[0]["instituicao"], "Nubank")
            self.assertIsNone(conexoes[0]["ultima_sincronizacao"])

    def test_registrar_de_novo_atualiza_em_vez_de_duplicar(self):
        with db.banco(self.banco) as conexao:
            db.registrar_conexao_pluggy(conexao, "item-1", "Nubank")
            db.registrar_conexao_pluggy(conexao, "item-1", "Nubank Atualizado")
            conexoes = db.listar_conexoes_pluggy(conexao)
            self.assertEqual(len(conexoes), 1)
            self.assertEqual(conexoes[0]["instituicao"], "Nubank Atualizado")

    def test_atualizar_sincronizacao(self):
        with db.banco(self.banco) as conexao:
            db.registrar_conexao_pluggy(conexao, "item-1", "Nubank")
            db.atualizar_sincronizacao_pluggy(conexao, "item-1")
            conexoes = db.listar_conexoes_pluggy(conexao)
            self.assertIsNotNone(conexoes[0]["ultima_sincronizacao"])


class TestInvestimentos(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_inserir_e_listar_posicoes(self):
        with db.banco(self.banco) as conexao:
            db.inserir_posicoes_investimento(conexao, [
                {"id": "p1", "data": "2026-08-01", "itemId": "item-1", "conta": "XP",
                 "tipo": "Renda Fixa", "nome": "CDB Banco X", "valor": 1000.0, "quantidade": None},
                {"id": "p2", "data": "2026-08-01", "itemId": "item-1", "conta": "XP",
                 "tipo": "Ações", "nome": "PETR4", "valor": 500.0, "quantidade": 20},
            ])
            posicoes = db.listar_posicoes_investimento(conexao)
            self.assertEqual(len(posicoes), 2)

    def test_listar_sem_data_pega_o_snapshot_mais_recente_por_ativo(self):
        with db.banco(self.banco) as conexao:
            db.inserir_posicoes_investimento(conexao, [
                {"id": "p1", "data": "2026-08-01", "itemId": "item-1", "conta": "XP",
                 "tipo": "Ações", "nome": "PETR4", "valor": 480.0},
                {"id": "p2", "data": "2026-08-15", "itemId": "item-1", "conta": "XP",
                 "tipo": "Ações", "nome": "PETR4", "valor": 500.0},
            ])
            posicoes = db.listar_posicoes_investimento(conexao)
            self.assertEqual(len(posicoes), 1)
            self.assertEqual(posicoes[0]["valor"], 500.0)
            self.assertEqual(posicoes[0]["data"], "2026-08-15")

    def test_listar_por_data_especifica(self):
        with db.banco(self.banco) as conexao:
            db.inserir_posicoes_investimento(conexao, [
                {"id": "p1", "data": "2026-08-01", "itemId": "item-1", "conta": "XP",
                 "tipo": "Ações", "nome": "PETR4", "valor": 480.0},
                {"id": "p2", "data": "2026-08-15", "itemId": "item-1", "conta": "XP",
                 "tipo": "Ações", "nome": "PETR4", "valor": 500.0},
            ])
            posicoes = db.listar_posicoes_investimento(conexao, data="2026-08-01")
            self.assertEqual(len(posicoes), 1)
            self.assertEqual(posicoes[0]["valor"], 480.0)


if __name__ == "__main__":
    unittest.main()
