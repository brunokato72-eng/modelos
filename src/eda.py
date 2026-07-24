"""Análise Exploratória de Dados (EDA) genérica.

Gera um resumo textual e salva gráficos em disco para qualquer dataset.
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")  # backend sem interface gráfica (funciona em servidor)
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


def _savefig(fig, figures_dir: str, name: str) -> None:
    os.makedirs(figures_dir, exist_ok=True)
    path = os.path.join(figures_dir, name)
    fig.savefig(path, bbox_inches="tight", dpi=120)
    plt.close(fig)
    print(f"[eda] Figura salva: {path}")


def run_eda(df: pd.DataFrame, target: str, figures_dir: str) -> dict:
    """Executa a análise exploratória completa.

    Args:
        df: DataFrame com os dados.
        target: nome da coluna alvo.
        figures_dir: diretório onde salvar os gráficos.

    Returns:
        Dicionário com um resumo dos achados (útil para logs/testes).
    """
    print("\n" + "=" * 70)
    print("ANÁLISE EXPLORATÓRIA DE DADOS (EDA)")
    print("=" * 70)

    # ---- 1. Visão geral -------------------------------------------------
    print(f"\nDimensões: {df.shape[0]} linhas x {df.shape[1]} colunas\n")
    print("Tipos de dados:")
    print(df.dtypes)

    print("\nPrimeiras linhas:")
    print(df.head())

    # ---- 2. Estatísticas descritivas -----------------------------------
    print("\nEstatísticas descritivas (numéricas):")
    with pd.option_context("display.max_columns", None):
        print(df.describe(include=[np.number]).T)

    cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
    if cat_cols:
        print("\nEstatísticas descritivas (categóricas):")
        print(df[cat_cols].describe().T)

    # ---- 3. Valores faltantes ------------------------------------------
    missing = df.isnull().sum()
    missing = missing[missing > 0].sort_values(ascending=False)
    print("\nValores faltantes por coluna:")
    if missing.empty:
        print("  Nenhum valor faltante.")
    else:
        pct = (missing / len(df) * 100).round(2)
        print(pd.DataFrame({"faltantes": missing, "%": pct}))
        _plot_missing(df, figures_dir)

    # ---- 4. Duplicatas -------------------------------------------------
    n_dup = df.duplicated().sum()
    print(f"\nLinhas duplicadas: {n_dup}")

    # ---- 5. Distribuição do alvo ---------------------------------------
    _plot_target_distribution(df, target, figures_dir)

    # ---- 6. Distribuições das features numéricas -----------------------
    num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if target in num_cols:
        num_cols.remove(target)
    if num_cols:
        _plot_numeric_distributions(df, num_cols, figures_dir)

    # ---- 7. Matriz de correlação ---------------------------------------
    corr_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if len(corr_cols) >= 2:
        _plot_correlation(df[corr_cols], figures_dir)

    print("\n[eda] EDA concluída. Veja os gráficos em:", figures_dir)

    return {
        "shape": df.shape,
        "missing_total": int(missing.sum()) if not missing.empty else 0,
        "duplicates": int(n_dup),
        "n_numeric": len(num_cols),
        "n_categorical": len(cat_cols),
    }


def _plot_missing(df: pd.DataFrame, figures_dir: str) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    sns.heatmap(df.isnull(), cbar=False, ax=ax)
    ax.set_title("Mapa de valores faltantes")
    _savefig(fig, figures_dir, "01_valores_faltantes.png")


def _plot_target_distribution(df: pd.DataFrame, target: str, figures_dir: str) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    serie = df[target]

    # Poucos valores únicos -> tratamos como classes (gráfico de contagem)
    if serie.dtype == object or serie.nunique() <= 20:
        order = serie.value_counts().index
        sns.countplot(x=serie, order=order, ax=ax)
        ax.set_title(f"Distribuição do alvo: {target}")
        ax.tick_params(axis="x", rotation=45)
        print("\nContagem por classe do alvo:")
        print(serie.value_counts())
    else:
        sns.histplot(serie.dropna(), kde=True, ax=ax)
        ax.set_title(f"Distribuição do alvo (contínuo): {target}")

    _savefig(fig, figures_dir, "02_distribuicao_alvo.png")


def _plot_numeric_distributions(df: pd.DataFrame, num_cols: list, figures_dir: str) -> None:
    cols = num_cols[:16]  # limita para não gerar figuras gigantes
    n = len(cols)
    ncols = 4
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 3 * nrows))
    axes = np.array(axes).reshape(-1)

    for i, col in enumerate(cols):
        sns.histplot(df[col].dropna(), kde=True, ax=axes[i])
        axes[i].set_title(col, fontsize=9)
    for j in range(n, len(axes)):
        axes[j].axis("off")

    fig.suptitle("Distribuições das features numéricas", y=1.02)
    fig.tight_layout()
    _savefig(fig, figures_dir, "03_distribuicoes_numericas.png")


def _plot_correlation(df_num: pd.DataFrame, figures_dir: str) -> None:
    fig, ax = plt.subplots(figsize=(10, 8))
    corr = df_num.corr(numeric_only=True)
    sns.heatmap(corr, annot=len(corr) <= 15, fmt=".2f", cmap="coolwarm",
                center=0, ax=ax, square=False)
    ax.set_title("Matriz de correlação (features numéricas)")
    _savefig(fig, figures_dir, "04_correlacao.png")
