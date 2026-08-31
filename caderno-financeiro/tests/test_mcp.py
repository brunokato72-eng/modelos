"""Testa o servidor MCP local (o caminho de function calling nativo).

Aqui não há chamada de modelo: o que se verifica é que o servidor fala o
protocolo direito e que quem calcula é o Python — a ferramenta devolve o número
já pronto, com log de auditoria da execução.
"""

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from caderno_financeiro import db
from caderno_financeiro import mcp_calculadora as servidor


class TestServidorMCP(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"
        self.log = Path(self.pasta.name) / "chamadas.jsonl"
        self.antigo_db = os.environ.get("CADERNO_DB")
        self.antigo_log = os.environ.get("CADERNO_MCP_LOG")
        os.environ["CADERNO_DB"] = str(self.banco)
        os.environ["CADERNO_MCP_LOG"] = str(self.log)
        self.addCleanup(self._restaurar)

        with db.banco(self.banco) as conexao:
            db.inserir(conexao, [
                {
                    "id": uuid.uuid4().hex, "data": f"2026-08-0{n}", "tipo": "Despesa",
                    "categoria": "Mercado", "valor": valor, "valorTotal": valor,
                    "parcelaAtual": 1, "totalParcelas": 1, "formaPagamento": "Pix",
                    "conta": "Nubank", "descricao": f"item {n}",
                    "criadoEm": "2026-08-01T00:00:00", "grupoParcelamento": None,
                }
                for n, valor in enumerate([111.11, 222.22, 333.33], start=1)
            ])

    def _restaurar(self):
        for chave, valor in (("CADERNO_DB", self.antigo_db), ("CADERNO_MCP_LOG", self.antigo_log)):
            if valor is None:
                os.environ.pop(chave, None)
            else:
                os.environ[chave] = valor

    def test_initialize_devolve_versao_do_protocolo(self):
        resposta = servidor.tratar({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
        })
        self.assertEqual(resposta["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("tools", resposta["result"]["capabilities"])

    def test_notificacao_nao_gera_resposta(self):
        self.assertIsNone(servidor.tratar({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_tools_list_expoe_calcular(self):
        resposta = servidor.tratar({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        ferramentas = resposta["result"]["tools"]
        self.assertEqual(len(ferramentas), 1)
        self.assertEqual(ferramentas[0]["name"], "calcular")
        self.assertIn("operacao", ferramentas[0]["inputSchema"]["properties"])

    def test_tools_call_calcula_de_verdade_e_registra_no_log(self):
        resposta = servidor.tratar({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "calcular", "arguments": {
                "operacao": "soma", "tipo": "Despesa", "mesInicio": "2026-08", "mesFim": "2026-08"}},
        })
        conteudo = json.loads(resposta["result"]["content"][0]["text"])
        self.assertEqual(conteudo["resultado"], 666.66)
        self.assertEqual(conteudo["quantidade"], 3)
        self.assertEqual(resposta["result"]["structuredContent"]["resultado"], 666.66)

        registros = [json.loads(l) for l in self.log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(registros), 1)
        self.assertEqual(registros[0]["argumentos"]["operacao"], "soma")

    def test_argumento_invalido_vira_erro_de_ferramenta(self):
        resposta = servidor.tratar({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "calcular", "arguments": {"operacao": "adivinhar"}},
        })
        self.assertTrue(resposta["result"]["isError"])

    def test_ferramenta_desconhecida(self):
        resposta = servidor.tratar({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "rm_rf", "arguments": {}},
        })
        self.assertEqual(resposta["error"]["code"], -32602)

    def test_metodo_desconhecido(self):
        resposta = servidor.tratar({"jsonrpc": "2.0", "id": 6, "method": "voce/nao/existe"})
        self.assertEqual(resposta["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
