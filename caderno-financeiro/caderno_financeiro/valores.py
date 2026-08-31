"""Dinheiro em centavos.

Toda conta interna (divisão de parcela, soma, média) é feita em centavos inteiros
e só volta pra reais na hora de mostrar. Isso é o que garante que a soma das
parcelas bate exatamente com o valor total, sem erro de arredondamento acumulado.
"""

from __future__ import annotations

import re


def para_centavos(valor) -> int:
    return int(round(float(valor) * 100))


def para_reais(centavos: int) -> float:
    return round(int(centavos) / 100, 2)


def formatar(valor) -> str:
    """1234.5 -> 'R$ 1.234,50'"""
    texto = f"{float(valor):,.2f}"
    texto = texto.replace(",", "#").replace(".", ",").replace("#", ".")
    return f"R$ {texto}"


def parsear_valor(bruto) -> float:
    """Aceita '1.234,56', '1234.56', 'R$ 45,90', '-20', 1234.5 e devolve float.

    Regra de separador: se tem vírgula e ponto, o que vier por último é o decimal.
    Se só tem vírgula, ela é decimal. Se só tem ponto, é decimal — a não ser que
    pareça separador de milhar ("1.234", "1.234.567").
    """
    if bruto is None:
        raise ValueError("valor vazio")
    if isinstance(bruto, (int, float)):
        return round(float(bruto), 2)

    texto = str(bruto).strip()
    if not texto:
        raise ValueError("valor vazio")

    negativo = texto.lstrip().startswith("-") or (texto.startswith("(") and texto.endswith(")"))
    limpo = re.sub(r"[^0-9,.]", "", texto)
    if not limpo:
        raise ValueError(f"valor não numérico: {bruto!r}")

    tem_virgula = "," in limpo
    tem_ponto = "." in limpo
    if tem_virgula and tem_ponto:
        if limpo.rfind(",") > limpo.rfind("."):
            limpo = limpo.replace(".", "").replace(",", ".")
        else:
            limpo = limpo.replace(",", "")
    elif tem_virgula:
        limpo = limpo.replace(",", ".")
    elif tem_ponto:
        # "1.234" / "1.234.567" = milhar; "1.23" / "1.2" = decimal
        partes = limpo.split(".")
        if len(partes) > 2 or len(partes[-1]) == 3:
            limpo = limpo.replace(".", "")

    valor = round(abs(float(limpo)), 2)
    return -valor if negativo else valor
