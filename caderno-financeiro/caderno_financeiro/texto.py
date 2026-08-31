"""Normalização de texto — porte direto do `normalizeStr` já validado no protótipo.

    function normalizeStr(s) {
      return String(s || '').normalize('NFD')
        .replace(/[̀-ͯ]/g, '').toLowerCase().trim();
    }
"""

from __future__ import annotations

import re
import unicodedata

_COMBINANTES = re.compile(r"[̀-ͯ]")


def normalizar(s) -> str:
    """Remove acentos, baixa a caixa e apara espaços."""
    texto = "" if s is None else str(s)
    decomposto = unicodedata.normalize("NFD", texto)
    return _COMBINANTES.sub("", decomposto).lower().strip()


def normalizar_chave(s) -> str:
    """Como `normalizar`, mas colapsa separadores — usado pra casar nomes de coluna
    ("Forma de Pagamento", "forma_pagamento", "FORMA-PAGAMENTO" viram o mesmo)."""
    base = normalizar(s)
    base = re.sub(r"[^a-z0-9]+", " ", base)
    return re.sub(r"\s+", " ", base).strip()
