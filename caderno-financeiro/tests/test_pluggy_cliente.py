import json
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from caderno_financeiro import pluggy_cliente as pc


def _resposta_falsa(corpo: dict):
    class RespostaFalsa:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(corpo).encode("utf-8")

    return RespostaFalsa()


class TestAutenticar(unittest.TestCase):
    def test_devolve_api_key(self):
        with mock.patch.object(pc.urllib.request, "urlopen", return_value=_resposta_falsa({"apiKey": "abc123"})):
            self.assertEqual(pc.autenticar("id", "segredo"), "abc123")

    def test_requisicao_manda_credenciais_no_corpo(self):
        capturada = {}

        def urlopen_falso(requisicao, timeout=None):
            capturada["url"] = requisicao.full_url
            capturada["corpo"] = json.loads(requisicao.data)
            capturada["metodo"] = requisicao.get_method()
            return _resposta_falsa({"apiKey": "xyz"})

        with mock.patch.object(pc.urllib.request, "urlopen", side_effect=urlopen_falso):
            pc.autenticar("meu-id", "meu-segredo")

        self.assertEqual(capturada["metodo"], "POST")
        self.assertTrue(capturada["url"].endswith("/auth"))
        self.assertEqual(capturada["corpo"], {"clientId": "meu-id", "clientSecret": "meu-segredo"})

    def test_sem_api_key_na_resposta_da_erro(self):
        with mock.patch.object(pc.urllib.request, "urlopen", return_value=_resposta_falsa({})):
            with self.assertRaises(pc.ErroPluggy):
                pc.autenticar("id", "segredo")

    def test_erro_http_vira_erro_pluggy(self):
        erro = HTTPError("url", 401, "unauthorized", {}, None)
        erro.read = lambda: b'{"message": "credenciais invalidas"}'
        with mock.patch.object(pc.urllib.request, "urlopen", side_effect=erro):
            with self.assertRaises(pc.ErroPluggy):
                pc.autenticar("id", "errado")

    def test_erro_de_rede_vira_erro_pluggy(self):
        with mock.patch.object(pc.urllib.request, "urlopen", side_effect=URLError("sem rede")):
            with self.assertRaises(pc.ErroPluggy):
                pc.autenticar("id", "segredo")


class TestListagens(unittest.TestCase):
    def test_listar_items_e_contas(self):
        with mock.patch.object(pc.urllib.request, "urlopen",
                               return_value=_resposta_falsa({"results": [{"id": "item1"}]})):
            self.assertEqual(pc.listar_items("k")[0]["id"], "item1")

        with mock.patch.object(pc.urllib.request, "urlopen",
                               return_value=_resposta_falsa({"results": [{"id": "conta1"}]})):
            self.assertEqual(pc.listar_contas("k", "item1")[0]["id"], "conta1")

    def test_transacoes_pagina_sozinho_via_totalpages(self):
        chamadas = []

        def urlopen_falso(requisicao, timeout=None):
            chamadas.append(requisicao.full_url)
            pagina = len(chamadas)
            if pagina == 1:
                return _resposta_falsa({"results": [{"id": "t1"}, {"id": "t2"}], "totalPages": 2})
            return _resposta_falsa({"results": [{"id": "t3"}], "totalPages": 2})

        with mock.patch.object(pc.urllib.request, "urlopen", side_effect=urlopen_falso):
            transacoes = pc.listar_transacoes("k", "conta1", tamanho_pagina=2)

        self.assertEqual(len(chamadas), 2)
        self.assertEqual([t["id"] for t in transacoes], ["t1", "t2", "t3"])

    def test_transacoes_pagina_sozinho_sem_totalpages(self):
        """API sem `totalPages`: para quando a página vem menor que o pedido."""
        chamadas = []

        def urlopen_falso(requisicao, timeout=None):
            chamadas.append(requisicao.full_url)
            if len(chamadas) == 1:
                return _resposta_falsa({"results": [{"id": "t1"}, {"id": "t2"}]})
            return _resposta_falsa({"results": [{"id": "t3"}]})

        with mock.patch.object(pc.urllib.request, "urlopen", side_effect=urlopen_falso):
            transacoes = pc.listar_transacoes("k", "conta1", tamanho_pagina=2)

        self.assertEqual(len(chamadas), 2)
        self.assertEqual(len(transacoes), 3)

    def test_transacoes_filtra_por_data_inicial(self):
        capturada = {}

        def urlopen_falso(requisicao, timeout=None):
            capturada["url"] = requisicao.full_url
            return _resposta_falsa({"results": []})

        with mock.patch.object(pc.urllib.request, "urlopen", side_effect=urlopen_falso):
            pc.listar_transacoes("k", "conta1", desde="2026-08-01")

        self.assertIn("from=2026-08-01", capturada["url"])

    def test_investimentos(self):
        with mock.patch.object(pc.urllib.request, "urlopen",
                               return_value=_resposta_falsa({"results": [{"id": "inv1"}]})):
            self.assertEqual(pc.listar_investimentos("k", "item1")[0]["id"], "inv1")


class TestObterApiKey(unittest.TestCase):
    def test_erro_claro_sem_credenciais_configuradas(self):
        with mock.patch.object(pc.config, "PLUGGY_CLIENT_ID", None), \
             mock.patch.object(pc.config, "PLUGGY_CLIENT_SECRET", None):
            with self.assertRaises(pc.ErroPluggy):
                pc.obter_api_key()


if __name__ == "__main__":
    unittest.main()
