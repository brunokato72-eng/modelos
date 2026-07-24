"""Avaliação do modelo no conjunto de TESTE (dados nunca vistos)."""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    accuracy_score,
    classification_report,
    confusion_matrix,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)


def _predict(model, X, problem_type: str, n_classes: int):
    """Gera predições de classe/valor a partir das probabilidades do modelo."""
    raw = model.predict(X, verbose=0)

    if problem_type == "regression":
        return raw.ravel(), None

    if n_classes == 2:
        proba = raw.ravel()
        pred = (proba >= 0.5).astype(int)
        return pred, proba

    proba = raw
    pred = np.argmax(raw, axis=1)
    return pred, proba


def evaluate_model(model, data, figures_dir: str) -> dict:
    """Avalia o modelo no conjunto de teste e salva gráficos de diagnóstico.

    Args:
        model: modelo Keras treinado.
        data: objeto Dataset.
        figures_dir: onde salvar os gráficos.

    Returns:
        Dicionário com as métricas calculadas.
    """
    print("\n" + "=" * 70)
    print("AVALIAÇÃO NO CONJUNTO DE TESTE")
    print("=" * 70)

    os.makedirs(figures_dir, exist_ok=True)
    y_true = data.y_test
    pred, proba = _predict(model, data.X_test, data.problem_type, data.n_classes)

    if data.problem_type == "classification":
        return _evaluate_classification(y_true, pred, proba, data, figures_dir)
    return _evaluate_regression(y_true, pred, figures_dir)


def _evaluate_classification(y_true, pred, proba, data, figures_dir: str) -> dict:
    label_names = [str(c) for c in data.label_encoder.classes_]

    acc = accuracy_score(y_true, pred)
    print(f"\nAcurácia: {acc:.4f}\n")
    print("Relatório de classificação:")
    print(classification_report(y_true, pred, target_names=label_names, zero_division=0))

    metrics = {"accuracy": float(acc)}

    # AUC quando aplicável
    try:
        if data.n_classes == 2:
            auc = roc_auc_score(y_true, proba)
        else:
            auc = roc_auc_score(y_true, proba, multi_class="ovr")
        metrics["roc_auc"] = float(auc)
        print(f"ROC AUC: {auc:.4f}")
    except Exception as exc:  # nº de classes no teste pode não bater
        print(f"[evaluate] AUC não calculado: {exc}")

    # Matriz de confusão
    cm = confusion_matrix(y_true, pred)
    fig, ax = plt.subplots(figsize=(7, 6))
    ConfusionMatrixDisplay(cm, display_labels=label_names).plot(
        ax=ax, cmap="Blues", colorbar=False
    )
    ax.set_title("Matriz de confusão (teste)")
    plt.xticks(rotation=45)
    path = os.path.join(figures_dir, "06_matriz_confusao.png")
    fig.savefig(path, bbox_inches="tight", dpi=120)
    plt.close(fig)
    print(f"[evaluate] Matriz de confusão salva: {path}")

    return metrics


def _evaluate_regression(y_true, pred, figures_dir: str) -> dict:
    mae = mean_absolute_error(y_true, pred)
    rmse = np.sqrt(mean_squared_error(y_true, pred))
    r2 = r2_score(y_true, pred)

    print(f"\nMAE : {mae:.4f}")
    print(f"RMSE: {rmse:.4f}")
    print(f"R²  : {r2:.4f}")

    # Predito vs Real
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.scatter(y_true, pred, alpha=0.5, edgecolor="k", linewidth=0.3)
    lims = [min(y_true.min(), pred.min()), max(y_true.max(), pred.max())]
    ax.plot(lims, lims, "r--", label="ideal (y = ŷ)")
    ax.set_xlabel("Valor real")
    ax.set_ylabel("Valor predito")
    ax.set_title("Predito vs Real (teste)")
    ax.legend()
    path = os.path.join(figures_dir, "06_predito_vs_real.png")
    fig.savefig(path, bbox_inches="tight", dpi=120)
    plt.close(fig)
    print(f"[evaluate] Gráfico predito vs real salvo: {path}")

    # Distribuição dos resíduos
    residuos = y_true - pred
    fig, ax = plt.subplots(figsize=(7, 5))
    sns.histplot(residuos, kde=True, ax=ax)
    ax.set_title("Distribuição dos resíduos (teste)")
    ax.set_xlabel("resíduo (real - predito)")
    path = os.path.join(figures_dir, "07_residuos.png")
    fig.savefig(path, bbox_inches="tight", dpi=120)
    plt.close(fig)
    print(f"[evaluate] Gráfico de resíduos salvo: {path}")

    return {"mae": float(mae), "rmse": float(rmse), "r2": float(r2)}
