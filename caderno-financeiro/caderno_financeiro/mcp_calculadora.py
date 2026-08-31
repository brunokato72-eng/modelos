"""Servidor MCP (stdio) que expõe o calculador determinístico como ferramenta.

Isso existe para o caminho de *function calling nativo*: em vez de a IA responder
com um JSON de plano que o nosso código executa, o próprio modelo chama a
ferramenta `calcular` e recebe de volta o número que ESTE processo calculou.

O ponto que não muda entre os dois caminhos: a conta continua sendo feita em
Python, sobre o banco real. O modelo nunca soma nada — no máximo ele decide quais
filtros pedir.

Protocolo MCP falado na mão (JSON-RPC 2.0 em stdio), sem dependência externa.

Uso: python3 -m caderno_financeiro.mcp_calculadora
Env: CADERNO_DB (banco a consultar), CADERNO_MCP_LOG (arquivo de auditoria das
chamadas — é o que o `testar-toolcall` usa pra provar que a ferramenta rodou
de verdade, em vez de o modelo ter simulado a chamada em texto).
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Dict, Optional

from . import db
from .calculadora import OPERACOES, executar_calculo

VERSAO_PROTOCOLO_PADRAO = "2025-06-18"
VERSOES_CONHECIDAS = {"2024-11-05", "2025-03-26", "2025-06-18"}

ESQUEMA_ENTRADA = {
    "type": "object",
    "properties": {
        "operacao": {
            "type": "string",
            "enum": list(OPERACOES),
            "description": "Agregação a executar sobre os lançamentos filtrados.",
        },
        "tipo": {"type": "string", "enum": ["Despesa", "Receita"]},
        "categoria": {"type": "string"},
        "formaPagamento": {"type": "string"},
        "conta": {"type": "string"},
        "mesInicio": {"type": "string", "description": "AAAA-MM (inclusive)"},
        "mesFim": {"type": "string", "description": "AAAA-MM (inclusive)"},
        "descricaoContem": {"type": "string"},
        "agruparPor": {
            "type": "string",
            "enum": ["categoria", "formaPagamento", "conta", "tipo", "mes"],
        },
    },
    "required": ["operacao"],
}

DESCRICAO_FERRAMENTA = (
    "Calcula estatísticas sobre os lançamentos financeiros reais do usuário "
    "(banco SQLite local). Use SEMPRE esta ferramenta para qualquer número: "
    "nunca some, estime ou aproxime valores por conta própria. Devolve o "
    "resultado já calculado e a quantidade de lançamentos considerados."
)


def _registrar_chamada(argumentos: Dict[str, Any], resultado: Dict[str, Any]) -> None:
    caminho = os.environ.get("CADERNO_MCP_LOG")
    if not caminho:
        return
    try:
        with open(caminho, "a", encoding="utf-8") as arquivo:
            arquivo.write(
                json.dumps({"argumentos": argumentos, "resultado": resultado}, ensure_ascii=False)
                + "\n"
            )
    except OSError:
        pass


def _calcular(argumentos: Dict[str, Any]) -> Dict[str, Any]:
    filtro = {k: v for k, v in (argumentos or {}).items() if v not in (None, "")}
    with db.banco() as conexao:
        lancamentos = db.listar(conexao)
    resultado = executar_calculo(filtro, lancamentos)
    _registrar_chamada(filtro, resultado)
    return resultado


def _resposta(id_requisicao: Any, resultado: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_requisicao, "result": resultado}


def _erro(id_requisicao: Any, codigo: int, mensagem: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_requisicao, "error": {"code": codigo, "message": mensagem}}


def tratar(mensagem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    metodo = mensagem.get("method")
    id_requisicao = mensagem.get("id")
    if id_requisicao is None:  # notificação: não se responde
        return None

    if metodo == "initialize":
        pedida = (mensagem.get("params") or {}).get("protocolVersion")
        versao = pedida if pedida in VERSOES_CONHECIDAS else VERSAO_PROTOCOLO_PADRAO
        return _resposta(
            id_requisicao,
            {
                "protocolVersion": versao,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "caderno-financeiro", "version": "1.0.0"},
            },
        )

    if metodo == "ping":
        return _resposta(id_requisicao, {})

    if metodo == "tools/list":
        return _resposta(
            id_requisicao,
            {
                "tools": [
                    {
                        "name": "calcular",
                        "title": "Calcular sobre os lançamentos",
                        "description": DESCRICAO_FERRAMENTA,
                        "inputSchema": ESQUEMA_ENTRADA,
                    }
                ]
            },
        )

    if metodo in ("resources/list", "prompts/list"):
        chave = "resources" if metodo.startswith("resources") else "prompts"
        return _resposta(id_requisicao, {chave: []})

    if metodo == "tools/call":
        parametros = mensagem.get("params") or {}
        if parametros.get("name") != "calcular":
            return _erro(id_requisicao, -32602, f"ferramenta desconhecida: {parametros.get('name')}")
        try:
            resultado = _calcular(parametros.get("arguments") or {})
        except Exception as erro:  # devolvido como erro de ferramenta, não do protocolo
            return _resposta(
                id_requisicao,
                {
                    "content": [{"type": "text", "text": f"erro ao calcular: {erro}"}],
                    "isError": True,
                },
            )
        texto = json.dumps(resultado, ensure_ascii=False)
        return _resposta(
            id_requisicao,
            {"content": [{"type": "text", "text": texto}], "structuredContent": resultado},
        )

    return _erro(id_requisicao, -32601, f"método não suportado: {metodo}")


def main() -> int:
    entrada = sys.stdin
    for linha in entrada:
        linha = linha.strip()
        if not linha:
            continue
        try:
            mensagem = json.loads(linha)
        except json.JSONDecodeError:
            continue
        try:
            resposta = tratar(mensagem)
        except Exception:  # pragma: no cover
            traceback.print_exc(file=sys.stderr)
            resposta = _erro(mensagem.get("id"), -32603, "erro interno no servidor")
        if resposta is not None:
            sys.stdout.write(json.dumps(resposta, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
