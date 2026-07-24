"""
Inferência: usa o modelo já treinado para prever em dados NOVOS.

Reaproveita o mesmo pré-processador e label encoder salvos pelo pipeline,
garantindo que os dados novos passem exatamente pela mesma transformação
do treino.

Uso:
    python predict.py --input dados_novos.csv --output predicoes.csv
"""

from __future__ import annotations

import argparse
import os

import joblib
import numpy as np
import pandas as pd
import yaml
from tensorflow import keras


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inferência com o modelo treinado")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--input", required=True, help="CSV/Excel/Parquet com dados novos")
    parser.add_argument("--output", default="predicoes.csv", help="Arquivo de saída")
    args = parser.parse_args()

    cfg = load_config(args.config)
    models_dir = cfg["output"]["models_dir"]

    # Carrega artefatos do treino
    model = keras.models.load_model(os.path.join(models_dir, "modelo_final.keras"))
    preprocessor = joblib.load(os.path.join(models_dir, "preprocessor.joblib"))
    le_path = os.path.join(models_dir, "label_encoder.joblib")
    label_encoder = joblib.load(le_path) if os.path.exists(le_path) else None

    # Carrega dados novos
    ext = os.path.splitext(args.input)[1].lower()
    if ext == ".csv":
        df = pd.read_csv(args.input, sep=cfg["data"].get("csv_sep", ","))
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(args.input)
    else:
        df = pd.read_parquet(args.input)

    # Remove a coluna alvo caso ela venha nos dados novos
    target = cfg["data"]["target"]
    if target in df.columns:
        df = df.drop(columns=[target])
    drop_cols = [c for c in (cfg["data"].get("drop_columns") or []) if c in df.columns]
    if drop_cols:
        df = df.drop(columns=drop_cols)

    X = preprocessor.transform(df)
    X = np.asarray(X, dtype=np.float32)
    raw = model.predict(X, verbose=0)

    if label_encoder is not None:
        if raw.shape[1] == 1:  # binário sigmoide
            proba = raw.ravel()
            pred_idx = (proba >= 0.5).astype(int)
            df["probabilidade"] = proba
        else:                  # multiclasse softmax
            pred_idx = np.argmax(raw, axis=1)
            df["probabilidade"] = raw.max(axis=1)
        df["predicao"] = label_encoder.inverse_transform(pred_idx)
    else:                      # regressão
        df["predicao"] = raw.ravel()

    df.to_csv(args.output, index=False)
    print(f"[predict] Predições salvas em: {args.output}")


if __name__ == "__main__":
    main()
