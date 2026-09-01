"""Aritmética de datas do parcelamento.

`somar_meses` é o porte fiel do `addMonths` já validado no protótipo, inclusive o
clamp pro último dia do mês (compra em 31/01 → parcela de fevereiro em 28/02, não
estoura pra março).
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config

RE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def somar_meses(data_iso: str, n: int) -> str:
    ano, mes, dia = (int(p) for p in data_iso.split("-"))
    indice_alvo = (mes - 1) + n
    # // em Python é floor division, igual ao Math.floor do JS (vale pra n negativo).
    ano_alvo = ano + indice_alvo // 12
    mes_alvo = indice_alvo % 12  # 0-based, sempre não-negativo
    ultimo_dia = calendar.monthrange(ano_alvo, mes_alvo + 1)[1]
    dia_final = min(dia, ultimo_dia)
    return f"{ano_alvo:04d}-{mes_alvo + 1:02d}-{dia_final:02d}"


def hoje_iso() -> str:
    """Data de hoje no fuso do usuário (`config.FUSO_HORARIO`), não no fuso do
    processo — o servidor pode estar numa VPS em UTC, o usuário não está."""
    return datetime.now(ZoneInfo(config.FUSO_HORARIO)).date().isoformat()


def mes_de(data_iso: str) -> str:
    """AAAA-MM de uma data AAAA-MM-DD."""
    return data_iso[:7]


def mes_atual() -> str:
    return hoje_iso()[:7]


def mes_anterior(mes: str) -> str:
    ano, m = (int(p) for p in mes.split("-"))
    return f"{ano - 1}-12" if m == 1 else f"{ano}-{m - 1:02d}"


def validar_iso(data_iso: str) -> str:
    if not RE_ISO.match(str(data_iso or "")):
        raise ValueError(f"data inválida (esperado AAAA-MM-DD): {data_iso!r}")
    datetime.strptime(data_iso, "%Y-%m-%d")
    return data_iso


def validar_mes(mes: str) -> str:
    if not re.match(r"^\d{4}-\d{2}$", str(mes or "")):
        raise ValueError(f"mês inválido (esperado AAAA-MM): {mes!r}")
    if not 1 <= int(mes[5:7]) <= 12:
        raise ValueError(f"mês inválido: {mes!r}")
    return mes


def dias_entre(a_iso: str, b_iso: str) -> int:
    a = datetime.strptime(a_iso, "%Y-%m-%d").date()
    b = datetime.strptime(b_iso, "%Y-%m-%d").date()
    return abs((a - b).days)
