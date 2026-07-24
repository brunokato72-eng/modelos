"""
Pipeline genérico de Rede Neural — ponto de entrada.

Executa, de ponta a ponta:
    1. Carregamento dos dados
    2. Análise Exploratória (EDA)
    3. Pré-processamento + split treino/validação/teste
    4. Construção da rede neural
    5. Treino (com validação)
    6. Avaliação no teste
    7. Salvamento do modelo e artefatos

Uso:
    python pipeline.py                 # usa config.yaml
    python pipeline.py --config outro.yaml
    python pipeline.py --skip-eda      # pula a EDA
"""

from __future__ import annotations

import argparse
import json
import os
import random

import joblib
import numpy as np
import yaml

from src.data_loader import load_data, validate_target
from src.eda import run_eda
from src.evaluate import evaluate_model
from src.model import build_model
from src.preprocessing import prepare_data
from src.train import train_model


def set_seed(seed: int) -> None:
    """Fixa as sementes para tornar os resultados reprodutíveis."""
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    try:
        import tensorflow as tf

        tf.random.set_seed(seed)
    except Exception:
        pass


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline genérico de rede neural")
    parser.add_argument("--config", default="config.yaml", help="Caminho do config.yaml")
    parser.add_argument("--skip-eda", action="store_true", help="Pula a etapa de EDA")
    args = parser.parse_args()

    cfg = load_config(args.config)
    set_seed(cfg.get("seed", 42))

    out_dir = cfg["output"]["dir"]
    figures_dir = cfg["output"]["figures_dir"]
    models_dir = cfg["output"]["models_dir"]
    for d in (out_dir, figures_dir, models_dir):
        os.makedirs(d, exist_ok=True)

    # 1) Dados -----------------------------------------------------------
    df = load_data(cfg["data"]["path"], cfg["data"].get("csv_sep", ","))
    validate_target(df, cfg["data"]["target"])

    # 2) EDA -------------------------------------------------------------
    if not args.skip_eda:
        run_eda(df, cfg["data"]["target"], figures_dir)

    # 3) Pré-processamento + split --------------------------------------
    data = prepare_data(df, cfg["data"]["target"], cfg)

    # 4) Modelo ----------------------------------------------------------
    print("\n" + "=" * 70)
    print("CONSTRUÇÃO DO MODELO")
    print("=" * 70)
    model = build_model(
        n_features=data.n_features,
        problem_type=data.problem_type,
        n_classes=data.n_classes,
        model_cfg=cfg["model"],
        train_cfg=cfg["train"],
    )

    # 5) Treino ----------------------------------------------------------
    model, _ = train_model(
        model, data, cfg["train"],
        models_dir=models_dir,
        figures_dir=figures_dir,
        verbose=cfg["output"].get("verbose", 1),
    )

    # 6) Avaliação -------------------------------------------------------
    metrics = evaluate_model(model, data, figures_dir)

    # 7) Salvar artefatos ------------------------------------------------
    model_path = os.path.join(models_dir, "modelo_final.keras")
    model.save(model_path)
    joblib.dump(data.preprocessor, os.path.join(models_dir, "preprocessor.joblib"))
    if data.label_encoder is not None:
        joblib.dump(data.label_encoder, os.path.join(models_dir, "label_encoder.joblib"))

    with open(os.path.join(out_dir, "metricas.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 70)
    print("PIPELINE CONCLUÍDO")
    print("=" * 70)
    print(f"Modelo final:   {model_path}")
    print(f"Pré-processador:{os.path.join(models_dir, 'preprocessor.joblib')}")
    print(f"Métricas:       {os.path.join(out_dir, 'metricas.json')}")
    print(f"Gráficos:       {figures_dir}")
    print(f"\nMétricas finais (teste): {json.dumps(metrics, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
