"""Backup em CSV.

Exporta o banco inteiro (ou um recorte) num CSV que o próprio importador
consegue reler — os nomes das colunas são os que a detecção automática reconhece.
Não depende de IA nem de rede: funciona mesmo que todo o resto esteja quebrado.
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

CABECALHO = (
    "data",
    "tipo",
    "categoria",
    "valor",
    "valor_total",
    "parcela_atual",
    "total_parcelas",
    "forma_pagamento",
    "conta",
    "descricao",
    "criado_em",
    "id",
)

_DE_DICT = {
    "data": "data",
    "tipo": "tipo",
    "categoria": "categoria",
    "valor": "valor",
    "valor_total": "valorTotal",
    "parcela_atual": "parcelaAtual",
    "total_parcelas": "totalParcelas",
    "forma_pagamento": "formaPagamento",
    "conta": "conta",
    "descricao": "descricao",
    "criado_em": "criadoEm",
    "id": "id",
}


def nome_padrao(diretorio: Optional[Path] = None) -> Path:
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(diretorio) if diretorio else Path.cwd()
    return base / f"caderno-backup-{carimbo}.csv"


def _despejar(lancamentos: Sequence[Dict[str, Any]], arquivo) -> int:
    linhas = sorted(lancamentos, key=lambda e: (e.get("data") or "", e.get("criadoEm") or ""))
    escritor = csv.writer(arquivo)
    escritor.writerow(CABECALHO)
    for lancamento in linhas:
        escritor.writerow([lancamento.get(_DE_DICT[coluna], "") for coluna in CABECALHO])
    return len(linhas)


def para_texto(lancamentos: Sequence[Dict[str, Any]]) -> str:
    """CSV completo como string — usado pelo servidor web (download direto)."""
    buffer = io.StringIO()
    _despejar(lancamentos, buffer)
    return buffer.getvalue()


def escrever(lancamentos: Sequence[Dict[str, Any]], destino) -> int:
    """`destino` pode ser um caminho ou '-' para stdout."""
    if str(destino) == "-":
        return _despejar(lancamentos, sys.stdout)
    caminho = Path(destino)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        return _despejar(lancamentos, arquivo)
