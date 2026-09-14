import tempfile
import unittest
import uuid
from pathlib import Path

from caderno_financeiro import config, db
from caderno_financeiro.estatisticas import progresso_orcamentos, saude_financeira


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


class TestOrcamentosNoBanco(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_definir_e_listar(self):
        with db.banco(self.banco) as conexao:
            db.definir_orcamento(conexao, "Mercado", 800.0)
            orcamentos = db.listar_orcamentos(conexao)
        self.assertEqual(len(orcamentos), 1)
        self.assertEqual(orcamentos[0]["categoria"], "Mercado")
        self.assertEqual(orcamentos[0]["limite"], 800.0)

    def test_definir_de_novo_atualiza_em_vez_de_duplicar(self):
        with db.banco(self.banco) as conexao:
            db.definir_orcamento(conexao, "Mercado", 800.0)
            db.definir_orcamento(conexao, "Mercado", 1000.0)
            orcamentos = db.listar_orcamentos(conexao)
        self.assertEqual(len(orcamentos), 1)
        self.assertEqual(orcamentos[0]["limite"], 1000.0)

    def test_categoria_invalida_da_erro(self):
        with db.banco(self.banco) as conexao:
            with self.assertRaises(ValueError):
                db.definir_orcamento(conexao, "Categoria Inventada", 100.0)

    def test_limite_zero_ou_negativo_da_erro(self):
        with db.banco(self.banco) as conexao:
            with self.assertRaises(ValueError):
                db.definir_orcamento(conexao, "Mercado", 0)
            with self.assertRaises(ValueError):
                db.definir_orcamento(conexao, "Mercado", -50)

    def test_remover(self):
        with db.banco(self.banco) as conexao:
            db.definir_orcamento(conexao, "Mercado", 800.0)
            self.assertTrue(db.remover_orcamento(conexao, "Mercado"))
            self.assertEqual(db.listar_orcamentos(conexao), [])
            self.assertFalse(db.remover_orcamento(conexao, "Mercado"))


class TestProgressoOrcamentos(unittest.TestCase):
    def test_gasto_dentro_do_limite(self):
        lancamentos = [lancamento(categoria="Mercado", valor=300.0, data="2026-08-05")]
        orcamentos = [{"categoria": "Mercado", "limite": 800.0}]
        progresso = progresso_orcamentos(lancamentos, orcamentos, mes="2026-08")
        self.assertEqual(len(progresso), 1)
        item = progresso[0]
        self.assertEqual(item["gasto"], 300.0)
        self.assertEqual(item["restante"], 500.0)
        self.assertEqual(item["percentual"], 37.5)
        self.assertFalse(item["estourado"])

    def test_gasto_estourado(self):
        lancamentos = [lancamento(categoria="Mercado", valor=900.0, data="2026-08-05")]
        orcamentos = [{"categoria": "Mercado", "limite": 800.0}]
        progresso = progresso_orcamentos(lancamentos, orcamentos, mes="2026-08")
        item = progresso[0]
        self.assertTrue(item["estourado"])
        self.assertEqual(item["restante"], 0.0)

    def test_ignora_gastos_de_outro_mes_e_de_outra_categoria(self):
        lancamentos = [
            lancamento(categoria="Mercado", valor=300.0, data="2026-07-05"),
            lancamento(categoria="Transporte", valor=100.0, data="2026-08-05"),
        ]
        orcamentos = [{"categoria": "Mercado", "limite": 800.0}]
        progresso = progresso_orcamentos(lancamentos, orcamentos, mes="2026-08")
        self.assertEqual(progresso[0]["gasto"], 0.0)

    def test_sem_gasto_no_mes(self):
        orcamentos = [{"categoria": "Mercado", "limite": 800.0}]
        progresso = progresso_orcamentos([], orcamentos, mes="2026-08")
        self.assertEqual(progresso[0]["gasto"], 0.0)
        self.assertEqual(progresso[0]["percentual"], 0.0)


class TestSaudeFinanceira(unittest.TestCase):
    def test_sem_receita_penaliza_e_avisa(self):
        lancamentos = [lancamento(data="2026-08-05", valor=100.0)]
        resultado = saude_financeira(lancamentos, mes="2026-08")
        self.assertIsNone(resultado["taxaPoupanca"])
        self.assertLess(resultado["pontuacao"], 100)
        self.assertTrue(any("receita" in a for a in resultado["alertas"]))

    def test_saldo_negativo_penaliza_mais_que_saldo_positivo_baixo(self):
        gastando_mais_que_ganha = [
            lancamento(tipo="Receita", categoria="Salário", valor=1000.0, data="2026-08-01"),
            lancamento(tipo="Despesa", valor=1500.0, data="2026-08-05"),
        ]
        poupando_pouco = [
            lancamento(tipo="Receita", categoria="Salário", valor=1000.0, data="2026-08-01"),
            lancamento(tipo="Despesa", valor=950.0, data="2026-08-05"),
        ]
        resultado_negativo = saude_financeira(gastando_mais_que_ganha, mes="2026-08")
        resultado_positivo = saude_financeira(poupando_pouco, mes="2026-08")
        self.assertLess(resultado_negativo["pontuacao"], resultado_positivo["pontuacao"])
        self.assertEqual(resultado_negativo["pontuacao"], 60)
        self.assertEqual(resultado_negativo["classificacao"], "atenção")

    def test_boa_taxa_de_poupanca_sem_alertas_da_pontuacao_maxima(self):
        lancamentos = [
            lancamento(tipo="Receita", categoria="Salário", valor=5000.0, data="2026-08-01"),
            lancamento(tipo="Despesa", valor=1000.0, data="2026-08-05"),
        ]
        resultado = saude_financeira(lancamentos, mes="2026-08")
        self.assertEqual(resultado["pontuacao"], 100)
        self.assertEqual(resultado["classificacao"], "boa")
        self.assertEqual(resultado["alertas"], [])

    def test_despesas_subindo_em_relacao_a_media_anterior_gera_alerta(self):
        lancamentos = [
            lancamento(tipo="Receita", categoria="Salário", valor=5000.0, data="2026-08-01"),
            lancamento(tipo="Despesa", valor=3000.0, data="2026-08-05"),
            lancamento(tipo="Despesa", valor=1000.0, data="2026-07-05"),
            lancamento(tipo="Despesa", valor=1000.0, data="2026-06-05"),
        ]
        resultado = saude_financeira(lancamentos, mes="2026-08")
        self.assertEqual(resultado["tendenciaDespesas"], "subindo")
        self.assertTrue(any("subindo" in a for a in resultado["alertas"]))

    def test_parcelas_futuras_pesadas_geram_alerta(self):
        lancamentos = [
            lancamento(tipo="Receita", categoria="Salário", valor=1000.0, data="2026-08-01"),
            lancamento(tipo="Despesa", valor=500.0, data="2026-09-05", totalParcelas=6, parcelaAtual=2),
            lancamento(tipo="Despesa", valor=500.0, data="2026-10-05", totalParcelas=6, parcelaAtual=3),
        ]
        resultado = saude_financeira(lancamentos, mes="2026-08")
        self.assertIsNotNone(resultado["comprometimentoFuturoPercentual"])
        self.assertGreater(resultado["comprometimentoFuturoPercentual"], 30)
        self.assertTrue(any("comprometem" in a for a in resultado["alertas"]))


if __name__ == "__main__":
    unittest.main()
