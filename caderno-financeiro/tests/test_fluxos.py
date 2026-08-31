"""Fluxos de ponta a ponta com a IA dublada.

O objetivo é provar que nenhuma conta depende do modelo: com a extração e a
redação substituídas por dublês, o valor gravado e o valor calculado continuam
sendo os corretos.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from caderno_financeiro import consulta, db, ia, registro


class TestRegistroPorTexto(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def _preparar(self, extraido, texto="qualquer coisa"):
        with mock.patch.object(ia, "extrair_lancamentos", return_value=extraido) as dublê:
            with db.banco(self.banco) as conexao:
                preparado = registro.preparar(conexao, texto, hoje="2026-08-31")
                registro.salvar(conexao, preparado)
                lancamentos = db.listar(conexao)
        return preparado, lancamentos, dublê

    def test_parcelamento_expandido_com_soma_exata(self):
        extraido = {"lancamentos": [{
            "tipo": "Despesa", "categoria": "Compras", "valorTotal": 3600.0,
            "totalParcelas": 12, "data": "2026-01-31",
            "formaPagamento": None, "conta": None, "descricao": "geladeira",
        }], "observacao": ""}
        _, lancamentos, _ = self._preparar(extraido)
        self.assertEqual(len(lancamentos), 12)
        self.assertEqual(round(sum(l["valor"] for l in lancamentos), 2), 3600.00)
        datas = sorted(l["data"] for l in lancamentos)
        self.assertEqual(datas[0], "2026-01-31")
        self.assertEqual(datas[1], "2026-02-28")
        self.assertTrue(all(l["formaPagamento"] == "Cartão de crédito" for l in lancamentos))
        self.assertTrue(all(l["conta"] == "Nubank" for l in lancamentos))

    def test_regra_do_ifood_aplicada_apos_a_extracao(self):
        extraido = {"lancamentos": [{
            "tipo": "Despesa", "categoria": "Alimentação", "valorTotal": 38.9,
            "totalParcelas": 1, "data": "2026-08-30",
            "formaPagamento": None, "conta": "Ifood", "descricao": "almoço",
        }], "observacao": ""}
        _, lancamentos, _ = self._preparar(extraido)
        self.assertEqual(lancamentos[0]["formaPagamento"], "VR")
        self.assertEqual(lancamentos[0]["conta"], "Ifood")

    def test_receita_tambem_e_registrada(self):
        extraido = {"lancamentos": [{
            "tipo": "Receita", "categoria": "Salário", "valorTotal": 5200.0,
            "totalParcelas": 1, "data": "2026-08-05",
            "formaPagamento": "Pix", "conta": "Itaú", "descricao": "salário",
        }], "observacao": ""}
        _, lancamentos, _ = self._preparar(extraido)
        self.assertEqual(lancamentos[0]["tipo"], "Receita")
        self.assertEqual(lancamentos[0]["valor"], 5200.0)

    def test_dois_lancamentos_na_mesma_mensagem(self):
        extraido = {"lancamentos": [
            {"tipo": "Despesa", "categoria": "Transporte", "valorTotal": 22.0, "totalParcelas": 1,
             "data": "2026-08-31", "formaPagamento": "Pix", "conta": None, "descricao": "uber"},
            {"tipo": "Despesa", "categoria": "Mercado", "valorTotal": 50.0, "totalParcelas": 1,
             "data": "2026-08-31", "formaPagamento": None, "conta": None, "descricao": "feira"},
        ], "observacao": ""}
        _, lancamentos, _ = self._preparar(extraido)
        self.assertEqual(len(lancamentos), 2)


class TestPerguntaModoManual(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"
        with db.banco(self.banco) as conexao:
            db.inserir(conexao, [
                {"id": f"id{n}", "data": f"2026-08-1{n}", "tipo": "Despesa",
                 "categoria": "Mercado", "valor": valor, "valorTotal": valor,
                 "parcelaAtual": 1, "totalParcelas": 1, "formaPagamento": "Pix",
                 "conta": "Nubank", "descricao": "feira", "criadoEm": "2026-08-01T00:00:00",
                 "grupoParcelamento": None}
                for n, valor in enumerate([111.11, 222.22, 333.33])
            ])

    def test_o_numero_vem_do_codigo_e_nao_do_modelo(self):
        plano = [{"rotulo": "gasto do mês", "operacao": "soma", "tipo": "Despesa",
                  "mesInicio": "2026-08", "mesFim": "2026-08", "categoria": None,
                  "formaPagamento": None, "conta": None, "descricaoContem": None,
                  "agruparPor": None}]
        capturado = {}

        def redigir(pergunta, resultados, **kwargs):
            capturado["resultados"] = list(resultados)
            return "resposta escrita pelo modelo"

        with mock.patch.object(ia, "planejar_consultas", return_value=plano), \
             mock.patch.object(ia, "redigir_resposta", side_effect=redigir):
            with db.banco(self.banco) as conexao:
                resultado = consulta.responder(conexao, "quanto gastei?", modo="manual")

        self.assertEqual(resultado["modo"], "manual")
        self.assertEqual(capturado["resultados"][0]["resultado"], 666.66)
        self.assertEqual(capturado["resultados"][0]["quantidade"], 3)
        self.assertEqual(capturado["resultados"][0]["rotulo"], "gasto do mês")

    def test_filtro_invalido_do_modelo_nao_derruba_a_pergunta(self):
        plano = [{"rotulo": "x", "operacao": "prever_o_futuro", "tipo": None, "categoria": None,
                  "formaPagamento": None, "conta": None, "mesInicio": None, "mesFim": None,
                  "descricaoContem": None, "agruparPor": None}]
        with mock.patch.object(ia, "planejar_consultas", return_value=plano), \
             mock.patch.object(ia, "redigir_resposta", return_value="ok") as redigir:
            with db.banco(self.banco) as conexao:
                consulta.responder(conexao, "e aí?", modo="manual")
        resultados = redigir.call_args[0][1]
        self.assertIn("erro", resultados[0])

    def test_modo_auto_usa_manual_enquanto_o_toolcall_nao_foi_validado(self):
        with mock.patch.object(consulta, "responder_manual", return_value={"modo": "manual"}) as manual, \
             mock.patch.object(consulta, "responder_toolcall") as toolcall:
            with db.banco(self.banco) as conexao:
                consulta.responder(conexao, "quanto gastei?", modo="auto")
        manual.assert_called_once()
        toolcall.assert_not_called()

    def test_toolcall_sem_execucao_de_ferramenta_cai_no_manual(self):
        """O bug do artefato: o modelo 'simula' a ferramenta e inventa número.

        Se nenhuma chamada chegou ao servidor MCP, a resposta é descartada e a
        pergunta é refeita pelo caminho determinístico.
        """
        with db.banco(self.banco) as conexao:
            db.definir_config(conexao, consulta.CHAVE_TOOLCALL, "1")

        falso = {"modo": "toolcall", "resposta": "gastei uns R$ 700", "chamadas": [],
                 "ferramentaExecutada": False}
        with mock.patch.object(consulta, "responder_toolcall", return_value=falso), \
             mock.patch.object(consulta, "responder_manual",
                               return_value={"modo": "manual", "resposta": "R$ 666,66"}) as manual:
            with db.banco(self.banco) as conexao:
                resultado = consulta.responder(conexao, "quanto gastei?", modo="auto")
        manual.assert_called_once()
        self.assertEqual(resultado["modo"], "manual")
        self.assertIn("avisoFallback", resultado)


if __name__ == "__main__":
    unittest.main()
