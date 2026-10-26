import unittest
from unittest import mock

from caderno_financeiro import notificador


def _score(pontuacao_final=60, classificacao="atenção", alertas=None, categorias=None, aderencia=None):
    return {
        "pontuacaoFinal": pontuacao_final,
        "classificacao": classificacao,
        "alertasComportamento": alertas or [],
        "categoriasEmRiscoDeEstourar": categorias or [],
        "poupanca": {"aderenciaPercentual": aderencia},
    }


class TestDeveNotificar(unittest.TestCase):
    def test_score_baixo_notifica(self):
        self.assertTrue(notificador.deve_notificar(_score(pontuacao_final=69)))

    def test_score_alto_sem_categoria_nao_notifica(self):
        self.assertFalse(notificador.deve_notificar(_score(pontuacao_final=70)))

    def test_score_alto_com_categoria_notificaragora_notifica(self):
        resultado = _score(pontuacao_final=90, categorias=[{"categoria": "Compras", "notificarAgora": True}])
        self.assertTrue(notificador.deve_notificar(resultado))

    def test_score_alto_com_categoria_sem_notificaragora_nao_notifica(self):
        resultado = _score(pontuacao_final=90, categorias=[{"categoria": "Compras", "notificarAgora": False}])
        self.assertFalse(notificador.deve_notificar(resultado))


class TestMontarMensagem(unittest.TestCase):
    def test_prioriza_alerta_comportamental(self):
        resultado = _score(
            alertas=["Educação teve 3 lançamentos"],
            categorias=[{"categoria": "Compras", "notificarAgora": True, "projecao": 1000.0, "limite": 500.0}],
            aderencia=-50.0,
        )
        msg = notificador.montar_mensagem(resultado)
        self.assertIn("Educação teve 3 lançamentos", msg)
        self.assertIn("score 60/100", msg)

    def test_sem_alerta_comportamental_usa_categoria_notificaragora(self):
        resultado = _score(categorias=[{"categoria": "Compras", "notificarAgora": True, "projecao": 1000.0, "limite": 500.0}])
        msg = notificador.montar_mensagem(resultado)
        self.assertIn("Compras", msg)
        self.assertIn("R$ 1.000,00", msg)
        self.assertIn("R$ 500,00", msg)

    def test_sem_sinais_usa_aderencia_a_meta(self):
        resultado = _score(aderencia=-30.5)
        msg = notificador.montar_mensagem(resultado)
        self.assertIn("aderência à meta de poupança: -30.5%", msg)

    def test_sem_nada_cai_no_generico(self):
        resultado = _score()
        msg = notificador.montar_mensagem(resultado)
        self.assertIn("sem sinal específico", msg)


class TestEnviarNtfy(unittest.TestCase):
    def test_sem_topico_da_erro(self):
        with self.assertRaises(notificador.ErroNotificacao):
            notificador.enviar_ntfy("oi", "")

    def test_sucesso_nao_levanta_erro(self):
        resposta_falsa = mock.MagicMock()
        resposta_falsa.status = 200
        resposta_falsa.__enter__.return_value = resposta_falsa
        with mock.patch("urllib.request.urlopen", return_value=resposta_falsa):
            notificador.enviar_ntfy("oi", "topico-teste")

    def test_falha_de_rede_vira_erro_notificacao(self):
        import urllib.error
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("sem conexão")):
            with self.assertRaises(notificador.ErroNotificacao):
                notificador.enviar_ntfy("oi", "topico-teste")


if __name__ == "__main__":
    unittest.main()
