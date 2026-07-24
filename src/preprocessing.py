"""Pré-processamento genérico e split treino/validação/teste.

Fluxo:
    1. Detecta o tipo de problema (classificação x regressão).
    2. Separa em TREINO, VALIDAÇÃO e TESTE.
    3. Ajusta o pré-processador SOMENTE no treino (evita vazamento de dados).
    4. Aplica a transformação nos três conjuntos.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import (
    LabelEncoder,
    MinMaxScaler,
    OneHotEncoder,
    OrdinalEncoder,
    StandardScaler,
)


@dataclass
class Dataset:
    """Conjuntos já processados e prontos para o modelo."""

    X_train: np.ndarray
    X_val: np.ndarray
    X_test: np.ndarray
    y_train: np.ndarray
    y_val: np.ndarray
    y_test: np.ndarray
    problem_type: str          # "classification" | "regression"
    n_classes: int             # 1 para regressão ou binário-sigmoide
    n_features: int
    feature_names: list
    label_encoder: LabelEncoder | None
    preprocessor: ColumnTransformer


def detect_problem_type(y: pd.Series, forced: str = "auto") -> str:
    """Detecta se o alvo é de classificação ou regressão."""
    if forced in ("classification", "regression"):
        return forced

    # Não numérico -> classificação
    if y.dtype == object or str(y.dtype).startswith("category"):
        return "classification"

    # Numérico: heurística por número de valores únicos
    nunique = y.nunique()
    if nunique <= 20 and np.array_equal(y.dropna(), y.dropna().astype(int)):
        return "classification"
    return "regression"


def _build_scaler(name: str):
    if name == "standard":
        return StandardScaler()
    if name == "minmax":
        return MinMaxScaler()
    if name == "none":
        return "passthrough"
    raise ValueError(f"scaler inválido: {name}")


def _build_preprocessor(
    X: pd.DataFrame, cfg: dict
) -> tuple[ColumnTransformer, list, list]:
    """Monta o ColumnTransformer para colunas numéricas e categóricas."""
    max_card = cfg.get("max_cardinality", 30)

    numeric_cols = X.select_dtypes(include=[np.number]).columns.tolist()
    categorical_cols = X.select_dtypes(include=["object", "category"]).columns.tolist()

    # Descarta categóricas de altíssima cardinalidade (ex.: ids textuais)
    high_card = [c for c in categorical_cols if X[c].nunique() > max_card]
    if high_card:
        print(f"[preprocessing] Ignorando categóricas de alta cardinalidade: {high_card}")
        categorical_cols = [c for c in categorical_cols if c not in high_card]

    # --- pipeline numérico ---
    num_imputer = SimpleImputer(strategy=cfg.get("numeric_impute", "median"))
    scaler = _build_scaler(cfg.get("scaler", "standard"))
    num_steps = [("imputer", num_imputer)]
    if scaler != "passthrough":
        num_steps.append(("scaler", scaler))
    numeric_pipe = Pipeline(num_steps)

    # --- pipeline categórico ---
    cat_imputer = SimpleImputer(strategy=cfg.get("categorical_impute", "most_frequent"))
    if cfg.get("categorical_encoding", "onehot") == "onehot":
        try:
            encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
        except TypeError:  # sklearn < 1.2 usa 'sparse'
            encoder = OneHotEncoder(handle_unknown="ignore", sparse=False)
    else:
        encoder = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
    categorical_pipe = Pipeline([("imputer", cat_imputer), ("encoder", encoder)])

    transformers = []
    if numeric_cols:
        transformers.append(("num", numeric_pipe, numeric_cols))
    if categorical_cols:
        transformers.append(("cat", categorical_pipe, categorical_cols))

    preprocessor = ColumnTransformer(transformers=transformers, remainder="drop")
    return preprocessor, numeric_cols, categorical_cols


def _get_feature_names(preprocessor: ColumnTransformer,
                       numeric_cols: list, categorical_cols: list) -> list:
    """Recupera os nomes das colunas após o pré-processamento."""
    try:
        return list(preprocessor.get_feature_names_out())
    except Exception:
        names = list(numeric_cols)
        names += list(categorical_cols)
        return names


def prepare_data(df: pd.DataFrame, target: str, cfg: dict) -> Dataset:
    """Executa todo o pré-processamento e o split treino/val/teste.

    Args:
        df: DataFrame completo (features + alvo).
        target: nome da coluna alvo.
        cfg: dicionário de configuração completo (config.yaml).

    Returns:
        Objeto Dataset com os conjuntos prontos para treino.
    """
    split_cfg = cfg["split"]
    prep_cfg = cfg["preprocessing"]

    # 1) Remover colunas indicadas pelo usuário
    drop_cols = cfg["data"].get("drop_columns") or []
    drop_cols = [c for c in drop_cols if c in df.columns]
    if drop_cols:
        df = df.drop(columns=drop_cols)
        print(f"[preprocessing] Colunas removidas: {drop_cols}")

    # 2) Remover linhas onde o alvo é nulo (não dá pra treinar sem rótulo)
    before = len(df)
    df = df.dropna(subset=[target])
    if len(df) < before:
        print(f"[preprocessing] Removidas {before - len(df)} linhas com alvo nulo")

    X = df.drop(columns=[target])
    y = df[target]

    # 3) Detectar tipo de problema
    problem_type = detect_problem_type(y, cfg["problem"].get("type", "auto"))
    print(f"[preprocessing] Tipo de problema detectado: {problem_type}")

    # 4) Codificar o alvo (classificação) ou manter contínuo (regressão)
    label_encoder = None
    if problem_type == "classification":
        label_encoder = LabelEncoder()
        y_enc = label_encoder.fit_transform(y.astype(str))
        n_classes = len(label_encoder.classes_)
        print(f"[preprocessing] Classes ({n_classes}): {list(label_encoder.classes_)}")
    else:
        y_enc = y.astype(float).values
        n_classes = 1

    # 5) Split treino+val / teste, depois treino / val
    stratify_test = y_enc if (problem_type == "classification"
                              and split_cfg.get("stratify", True)) else None

    X_trainval, X_test, y_trainval, y_test = train_test_split(
        X, y_enc,
        test_size=split_cfg["test_size"],
        random_state=split_cfg["random_state"],
        stratify=stratify_test,
    )

    stratify_val = y_trainval if (problem_type == "classification"
                                  and split_cfg.get("stratify", True)) else None

    X_train, X_val, y_train, y_val = train_test_split(
        X_trainval, y_trainval,
        test_size=split_cfg["validation_size"],
        random_state=split_cfg["random_state"],
        stratify=stratify_val,
    )

    print(f"[preprocessing] Treino: {len(X_train)} | "
          f"Validação: {len(X_val)} | Teste: {len(X_test)}")

    # 6) Ajustar o pré-processador SÓ no treino e transformar os três
    preprocessor, num_cols, cat_cols = _build_preprocessor(X_train, prep_cfg)
    X_train_p = preprocessor.fit_transform(X_train)
    X_val_p = preprocessor.transform(X_val)
    X_test_p = preprocessor.transform(X_test)

    X_train_p = np.asarray(X_train_p, dtype=np.float32)
    X_val_p = np.asarray(X_val_p, dtype=np.float32)
    X_test_p = np.asarray(X_test_p, dtype=np.float32)

    feature_names = _get_feature_names(preprocessor, num_cols, cat_cols)

    print(f"[preprocessing] Nº de features após encoding: {X_train_p.shape[1]}")

    return Dataset(
        X_train=X_train_p,
        X_val=X_val_p,
        X_test=X_test_p,
        y_train=np.asarray(y_train),
        y_val=np.asarray(y_val),
        y_test=np.asarray(y_test),
        problem_type=problem_type,
        n_classes=n_classes,
        n_features=X_train_p.shape[1],
        feature_names=feature_names,
        label_encoder=label_encoder,
        preprocessor=preprocessor,
    )
