"""
Gera um dataset de exemplo para você testar o pipeline sem ter sua base ainda.

Cria 'data/dataset.csv' com features numéricas, categóricas, valores
faltantes e uma coluna alvo. Serve tanto para classificação quanto,
mudando --tipo, para regressão.

Uso:
    python gerar_dados_exemplo.py               # classificação
    python gerar_dados_exemplo.py --tipo regressao
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tipo", choices=["classificacao", "regressao"],
                        default="classificacao")
    parser.add_argument("--n", type=int, default=2000, help="Número de linhas")
    parser.add_argument("--out", default="data/dataset.csv")
    args = parser.parse_args()

    rng = np.random.default_rng(42)
    n = args.n

    idade = rng.integers(18, 80, n)
    renda = rng.normal(5000, 2000, n).clip(500)
    score = rng.normal(600, 120, n).clip(0, 1000)
    tempo_cliente = rng.integers(0, 240, n)
    regiao = rng.choice(["Norte", "Sul", "Leste", "Oeste"], n)
    plano = rng.choice(["basico", "premium", "enterprise"], n, p=[0.5, 0.35, 0.15])

    # Sinal que liga as features ao alvo
    sinal = (
        0.02 * (renda / 1000)
        + 0.01 * (score / 100)
        - 0.015 * idade
        + 0.02 * tempo_cliente
        + np.where(plano == "premium", 1.0, 0.0)
        + np.where(plano == "enterprise", 2.0, 0.0)
    )

    df = pd.DataFrame({
        "idade": idade,
        "renda": renda.round(2),
        "score_credito": score.round(1),
        "tempo_cliente_meses": tempo_cliente,
        "regiao": regiao,
        "plano": plano,
    })

    if args.tipo == "classificacao":
        prob = 1 / (1 + np.exp(-(sinal - sinal.mean()) / sinal.std()))
        df["target"] = (rng.random(n) < prob).astype(int)
    else:
        df["target"] = (sinal * 1000 + rng.normal(0, 500, n)).round(2)

    # Introduz alguns valores faltantes para exercitar a imputação
    for col in ["renda", "score_credito", "regiao"]:
        mask = rng.random(n) < 0.05
        df.loc[mask, col] = np.nan

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"Dataset de exemplo ({args.tipo}) salvo em: {args.out}")
    print(df.head())


if __name__ == "__main__":
    main()
