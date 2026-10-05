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


def somar_dias(data_iso: str, n: int) -> str:
    from datetime import timedelta

    data = datetime.strptime(data_iso, "%Y-%m-%d").date() + timedelta(days=n)
    return data.isoformat()


def dias_no_mes(mes: str) -> int:
    ano, m = (int(p) for p in mes.split("-"))
    return calendar.monthrange(ano, m)[1]


# Ciclo de fatura: fecha todo dia `config.DIA_FECHAMENTO_CICLO` (27 por padrão,
# alinhado ao fechamento do cartão/salário) — orçamento, meta de poupança e
# score usam esse ciclo em vez do mês calendário (1-31). Todo mês tem pelo
# menos 27 dias, então o dia de fechamento nunca precisa de clamp. O ciclo é
# identificado pelo AAAA-MM do seu dia de FECHAMENTO (ex.: ciclo que fecha
# 27/10 é "o ciclo de outubro", mesmo cobrindo a maior parte de setembro).
def ciclo_de(data_iso: str) -> tuple[str, str]:
    """(início, fim) do ciclo que contém `data_iso`."""
    ano, mes, dia = (int(p) for p in data_iso.split("-"))
    fechamento_deste_mes = f"{ano:04d}-{mes:02d}-{config.DIA_FECHAMENTO_CICLO:02d}"
    fim = fechamento_deste_mes if dia <= config.DIA_FECHAMENTO_CICLO else somar_meses(fechamento_deste_mes, 1)
    inicio = somar_dias(somar_meses(fim, -1), 1)
    return inicio, fim


def ciclo_atual() -> tuple[str, str]:
    return ciclo_de(hoje_iso())


def ciclo_anterior(inicio_iso: str) -> tuple[str, str]:
    return ciclo_de(somar_dias(inicio_iso, -1))


def ciclo_seguinte(fim_iso: str) -> tuple[str, str]:
    return ciclo_de(somar_dias(fim_iso, 1))


def rotulo_ciclo(fim_iso: str) -> str:
    """AAAA-MM do fechamento — como o usuário chamaria o ciclo (\"ciclo de outubro\")."""
    return fim_iso[:7]


def ciclo_por_rotulo(rotulo: str) -> tuple[str, str]:
    """(início, fim) do ciclo cujo fechamento cai no mês `rotulo` (AAAA-MM)."""
    fim = f"{rotulo}-{config.DIA_FECHAMENTO_CICLO:02d}"
    inicio = somar_dias(somar_meses(fim, -1), 1)
    return inicio, fim


def rotulo_ciclo_atual() -> str:
    """Atalho pro rótulo do ciclo corrente — o "mês" padrão em todo o app."""
    return rotulo_ciclo(ciclo_atual()[1])
