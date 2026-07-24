"""Carregamento genérico de dados a partir de CSV, Excel ou Parquet."""

from __future__ import annotations

import os

import pandas as pd


def load_data(path: str, csv_sep: str = ",") -> pd.DataFrame:
    """Carrega um dataset detectando o formato pela extensão do arquivo.

    Args:
        path: caminho do arquivo (.csv, .xlsx/.xls ou .parquet).
        csv_sep: separador usado quando o arquivo é CSV.

    Returns:
        DataFrame com os dados carregados.

    Raises:
        FileNotFoundError: se o arquivo não existir.
        ValueError: se a extensão não for suportada.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Arquivo não encontrado: '{path}'. "
            "Ajuste 'data.path' no config.yaml para apontar para a sua base."
        )

    ext = os.path.splitext(path)[1].lower()

    if ext == ".csv":
        df = pd.read_csv(path, sep=csv_sep)
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(path)
    elif ext == ".parquet":
        df = pd.read_parquet(path)
    else:
        raise ValueError(
            f"Extensão não suportada: '{ext}'. "
            "Use .csv, .xlsx, .xls ou .parquet."
        )

    print(f"[data_loader] Dados carregados: {df.shape[0]} linhas x {df.shape[1]} colunas")
    return df


def validate_target(df: pd.DataFrame, target: str) -> None:
    """Garante que a coluna alvo existe no DataFrame."""
    if target not in df.columns:
        raise KeyError(
            f"Coluna alvo '{target}' não encontrada. "
            f"Colunas disponíveis: {list(df.columns)}"
        )
