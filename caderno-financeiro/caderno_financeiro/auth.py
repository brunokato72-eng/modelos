"""PIN + token de sessão para o servidor web.

Pensado pra ficar atrás de uma tailnet (só dispositivos que você autorizou
alcançam o servidor). O PIN é uma segunda camada, barata: mesmo que algo saia
errado na rede, ninguém entra sem ele.

PIN nunca é guardado em texto puro — só hash PBKDF2 com salt aleatório. O
token de sessão devolvido no login é o que o PWA guarda (localStorage) e manda
em todo request; fica em uma tabela própria pra poder revogar tudo de uma vez
(`caderno revogar-sessoes`, útil se você perder o celular).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from . import db

ITERACOES_PBKDF2 = 200_000
VALIDADE_TOKEN_DIAS = 90


def _hash_pin(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, ITERACOES_PBKDF2).hex()


def definir_pin(conexao, pin: str) -> None:
    pin = str(pin or "").strip()
    if len(pin) < 4:
        raise ValueError("o PIN precisa ter pelo menos 4 dígitos/caracteres")
    salt = secrets.token_bytes(16)
    db.definir_config(conexao, "pin_salt", salt.hex())
    db.definir_config(conexao, "pin_hash", _hash_pin(pin, salt))
    revogar_todas_as_sessoes(conexao)


def pin_configurado(conexao) -> bool:
    return db.ler_config(conexao, "pin_hash") is not None


def conferir_pin(conexao, pin: str) -> bool:
    salt_hex = db.ler_config(conexao, "pin_salt")
    hash_salvo = db.ler_config(conexao, "pin_hash")
    if not salt_hex or not hash_salvo:
        return False
    calculado = _hash_pin(str(pin or ""), bytes.fromhex(salt_hex))
    return hmac.compare_digest(calculado, hash_salvo)


def _esquema_sessoes(conexao) -> None:
    conexao.execute(
        "CREATE TABLE IF NOT EXISTS sessoes ("
        "token TEXT PRIMARY KEY, criado_em TEXT NOT NULL, "
        "expira_em TEXT NOT NULL, ultimo_uso TEXT NOT NULL)"
    )


def criar_sessao(conexao) -> str:
    _esquema_sessoes(conexao)
    token = secrets.token_urlsafe(32)
    agora = datetime.now()
    expira = (agora + timedelta(days=VALIDADE_TOKEN_DIAS)).isoformat(timespec="seconds")
    conexao.execute(
        "INSERT INTO sessoes (token, criado_em, expira_em, ultimo_uso) VALUES (?, ?, ?, ?)",
        (token, agora.isoformat(timespec="seconds"), expira, agora.isoformat(timespec="seconds")),
    )
    return token


def sessao_valida(conexao, token: Optional[str]) -> bool:
    """Confere o token e atualiza `ultimo_uso`, comitando na hora.

    O commit imediato importa: quem chama isso é o `before_request` do
    servidor, no início de TODO request — inclusive os que ficam minutos
    esperando o CLI `claude` responder (registrar/perguntar). Sem comitar
    aqui, essa escrita ficaria pendurada numa transação aberta pela duração
    inteira desses requests lentos, e qualquer outro request concorrente
    (ex: abrir a aba de histórico enquanto uma pergunta ainda está sendo
    respondida) travava com "database is locked" esperando essa transação
    liberar.
    """
    if not token:
        return False
    _esquema_sessoes(conexao)
    linha = conexao.execute(
        "SELECT expira_em FROM sessoes WHERE token = ?", (token,)
    ).fetchone()
    if not linha:
        return False
    if linha["expira_em"] < datetime.now().isoformat(timespec="seconds"):
        conexao.execute("DELETE FROM sessoes WHERE token = ?", (token,))
        conexao.commit()
        return False
    conexao.execute(
        "UPDATE sessoes SET ultimo_uso = ? WHERE token = ?",
        (datetime.now().isoformat(timespec="seconds"), token),
    )
    conexao.commit()
    return True


def revogar_todas_as_sessoes(conexao) -> int:
    _esquema_sessoes(conexao)
    cursor = conexao.execute("DELETE FROM sessoes")
    return cursor.rowcount


def revogar_sessao(conexao, token: str) -> int:
    _esquema_sessoes(conexao)
    cursor = conexao.execute("DELETE FROM sessoes WHERE token = ?", (token,))
    return cursor.rowcount
