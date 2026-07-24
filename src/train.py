"""Treino da rede neural usando as bases de TREINO e VALIDAÇÃO."""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.utils.class_weight import compute_class_weight
from tensorflow import keras


def _build_callbacks(train_cfg: dict, models_dir: str) -> list:
    os.makedirs(models_dir, exist_ok=True)
    ckpt_path = os.path.join(models_dir, "melhor_modelo.keras")

    callbacks = [
        keras.callbacks.EarlyStopping(
            monitor="val_loss",
            patience=train_cfg.get("early_stopping_patience", 10),
            restore_best_weights=True,
            verbose=1,
        ),
        keras.callbacks.ModelCheckpoint(
            ckpt_path, monitor="val_loss", save_best_only=True, verbose=0
        ),
    ]

    reduce_patience = train_cfg.get("reduce_lr_patience", 0)
    if reduce_patience and reduce_patience > 0:
        callbacks.append(
            keras.callbacks.ReduceLROnPlateau(
                monitor="val_loss",
                factor=0.5,
                patience=reduce_patience,
                min_lr=1e-6,
                verbose=1,
            )
        )
    return callbacks


def _compute_class_weight(y_train: np.ndarray) -> dict | None:
    classes = np.unique(y_train)
    weights = compute_class_weight("balanced", classes=classes, y=y_train)
    return {int(c): float(w) for c, w in zip(classes, weights)}


def train_model(model: keras.Model, data, train_cfg: dict,
                models_dir: str, figures_dir: str, verbose: int = 1):
    """Treina o modelo com early stopping e validação.

    Args:
        model: modelo Keras compilado.
        data: objeto Dataset (de preprocessing.prepare_data).
        train_cfg: seção 'train' do config.
        models_dir: onde salvar o melhor modelo.
        figures_dir: onde salvar a curva de aprendizado.
        verbose: nível de log do Keras.

    Returns:
        (modelo treinado, objeto history do Keras).
    """
    print("\n" + "=" * 70)
    print("TREINAMENTO (base de treino + validação)")
    print("=" * 70)

    class_weight = None
    if data.problem_type == "classification" and train_cfg.get("class_weight", False):
        class_weight = _compute_class_weight(data.y_train)
        print(f"[train] Pesos de classe (balanceamento): {class_weight}")

    callbacks = _build_callbacks(train_cfg, models_dir)

    history = model.fit(
        data.X_train, data.y_train,
        validation_data=(data.X_val, data.y_val),
        epochs=train_cfg.get("epochs", 100),
        batch_size=train_cfg.get("batch_size", 32),
        class_weight=class_weight,
        callbacks=callbacks,
        verbose=verbose,
    )

    _plot_history(history, data.problem_type, figures_dir)
    print(f"[train] Melhor modelo salvo em: "
          f"{os.path.join(models_dir, 'melhor_modelo.keras')}")

    return model, history


def _plot_history(history, problem_type: str, figures_dir: str) -> None:
    os.makedirs(figures_dir, exist_ok=True)
    hist = history.history

    # Métrica de acurácia (classificação) ou MAE (regressão)
    metric_key = "accuracy" if "accuracy" in hist else (
        "mae" if "mae" in hist else None
    )

    ncols = 2 if metric_key else 1
    fig, axes = plt.subplots(1, ncols, figsize=(6 * ncols, 4.5))
    if ncols == 1:
        axes = [axes]

    axes[0].plot(hist["loss"], label="treino")
    axes[0].plot(hist["val_loss"], label="validação")
    axes[0].set_title("Curva de perda (loss)")
    axes[0].set_xlabel("época")
    axes[0].set_ylabel("loss")
    axes[0].legend()

    if metric_key:
        axes[1].plot(hist[metric_key], label="treino")
        axes[1].plot(hist[f"val_{metric_key}"], label="validação")
        axes[1].set_title(f"Curva de {metric_key}")
        axes[1].set_xlabel("época")
        axes[1].set_ylabel(metric_key)
        axes[1].legend()

    fig.tight_layout()
    path = os.path.join(figures_dir, "05_curva_aprendizado.png")
    fig.savefig(path, bbox_inches="tight", dpi=120)
    plt.close(fig)
    print(f"[train] Curva de aprendizado salva: {path}")
