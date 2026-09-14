"""Cliente HTTP mínimo pra API do Meu Pluggy (Open Finance).

Usa só a biblioteca padrão (urllib) — não vale adicionar uma dependência nova
só pra um punhado de chamadas GET/POST. Cobre exatamente o que a
sincronização precisa: autenticar, listar conexões, contas, transações e
investimentos.

Os nomes de campo (`apiKey`, `results`, `clientId`...) seguem a documentação
pública da Pluggy no momento em que isso foi escrito; se a API mudar algo,
este é o único arquivo que deveria precisar de ajuste — todo o resto do
projeto fala com `pluggy_sync.py`, nunca direto com HTTP.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from . import config

TIMEOUT_PADRAO = 30


class ErroPluggy(RuntimeError):
    pass


def _requisitar(
    metodo: str,
    caminho: str,
    *,
    api_key: Optional[str] = None,
    corpo: Optional[Dict[str, Any]] = None,
    parametros: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    url = f"{config.PLUGGY_API_URL}{caminho}"
    if parametros:
        limpo = {k: v for k, v in parametros.items() if v is not None}
        if limpo:
            url += f"?{urllib.parse.urlencode(limpo)}"

    cabecalhos = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        cabecalhos["X-API-KEY"] = api_key

    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    requisicao = urllib.request.Request(url, data=dados, headers=cabecalhos, method=metodo)
    try:
        with urllib.request.urlopen(requisicao, timeout=TIMEOUT_PADRAO) as resposta:
            bruto = resposta.read()
            return json.loads(bruto) if bruto else {}
    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode("utf-8", errors="replace")
        raise ErroPluggy(f"Pluggy respondeu {erro.code} em {caminho}: {detalhe[:300]}") from erro
    except urllib.error.URLError as erro:
        raise ErroPluggy(f"não consegui falar com a Pluggy: {erro.reason}") from erro
    except json.JSONDecodeError as erro:
        raise ErroPluggy(f"resposta da Pluggy não é JSON válido em {caminho}") from erro


def autenticar(client_id: str, client_secret: str) -> str:
    """Troca client_id/secret por uma API key (válida por ~2h)."""
    resposta = _requisitar(
        "POST", "/auth", corpo={"clientId": client_id, "clientSecret": client_secret}
    )
    chave = resposta.get("apiKey")
    if not chave:
        raise ErroPluggy(f"resposta de autenticação sem apiKey: {resposta}")
    return chave


def listar_items(api_key: str) -> List[Dict[str, Any]]:
    """Todas as conexões (bancos) já consentidas pelo usuário no Meu Pluggy."""
    resposta = _requisitar("GET", "/items", api_key=api_key)
    return resposta.get("results", [])


def listar_contas(api_key: str, item_id: str) -> List[Dict[str, Any]]:
    resposta = _requisitar("GET", "/accounts", api_key=api_key, parametros={"itemId": item_id})
    return resposta.get("results", [])


def _paginar(
    api_key: str, caminho: str, parametros_base: Dict[str, Any], tamanho_pagina: int
) -> List[Dict[str, Any]]:
    todos: List[Dict[str, Any]] = []
    pagina = 1
    while True:
        parametros = {**parametros_base, "page": pagina, "pageSize": tamanho_pagina}
        resposta = _requisitar("GET", caminho, api_key=api_key, parametros=parametros)
        lote = resposta.get("results", [])
        todos.extend(lote)

        total_paginas = resposta.get("totalPages")
        if total_paginas is not None:
            if pagina >= total_paginas:
                break
        elif len(lote) < tamanho_pagina:
            break
        pagina += 1
    return todos


def listar_transacoes(
    api_key: str, account_id: str, *, desde: Optional[str] = None, tamanho_pagina: int = 500
) -> List[Dict[str, Any]]:
    """Todas as transações de uma conta, paginando sozinho. `desde` é AAAA-MM-DD."""
    parametros: Dict[str, Any] = {"accountId": account_id}
    if desde:
        parametros["from"] = desde
    return _paginar(api_key, "/transactions", parametros, tamanho_pagina)


def listar_investimentos(api_key: str, item_id: str) -> List[Dict[str, Any]]:
    resposta = _requisitar("GET", "/investments", api_key=api_key, parametros={"itemId": item_id})
    return resposta.get("results", [])


def obter_api_key() -> str:
    if not config.PLUGGY_CLIENT_ID or not config.PLUGGY_CLIENT_SECRET:
        raise ErroPluggy(
            "PLUGGY_CLIENT_ID / PLUGGY_CLIENT_SECRET não configurados. "
            "Pegue as credenciais no Dashboard da Pluggy (app demo do Meu Pluggy) "
            "e defina essas variáveis de ambiente."
        )
    return autenticar(config.PLUGGY_CLIENT_ID, config.PLUGGY_CLIENT_SECRET)
