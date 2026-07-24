"""Construção genérica da rede neural (MLP) com Keras.

A arquitetura se adapta automaticamente ao tipo de problema:
    - regressão            -> 1 neurônio linear na saída
    - classificação binária-> 1 neurônio sigmoide
    - classificação multi  -> N neurônios softmax
"""

from __future__ import annotations

import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers, regularizers


def _output_config(problem_type: str, n_classes: int):
    """Define neurônios de saída, ativação, loss e métricas."""
    if problem_type == "regression":
        return 1, "linear", "mse", ["mae", "mse"]

    if n_classes == 2:
        # Classificação binária -> sigmoide + binary_crossentropy
        return 1, "sigmoid", "binary_crossentropy", ["accuracy"]

    # Classificação multiclasse -> softmax + sparse_categorical_crossentropy
    return n_classes, "softmax", "sparse_categorical_crossentropy", ["accuracy"]


def _build_optimizer(name: str, lr: float):
    name = name.lower()
    if name == "adam":
        return keras.optimizers.Adam(learning_rate=lr)
    if name == "rmsprop":
        return keras.optimizers.RMSprop(learning_rate=lr)
    if name == "sgd":
        return keras.optimizers.SGD(learning_rate=lr, momentum=0.9)
    raise ValueError(f"optimizer inválido: {name}")


def build_model(
    n_features: int,
    problem_type: str,
    n_classes: int,
    model_cfg: dict,
    train_cfg: dict,
) -> keras.Model:
    """Constrói e compila a rede neural.

    Args:
        n_features: número de features de entrada.
        problem_type: "classification" ou "regression".
        n_classes: número de classes (1 para regressão).
        model_cfg: seção 'model' do config.
        train_cfg: seção 'train' do config.

    Returns:
        Modelo Keras compilado.
    """
    l2 = model_cfg.get("l2", 0.0)
    reg = regularizers.l2(l2) if l2 and l2 > 0 else None
    dropout = model_cfg.get("dropout", 0.0)
    use_bn = model_cfg.get("batch_normalization", False)
    activation = model_cfg.get("activation", "relu")

    model = keras.Sequential(name="mlp")
    model.add(keras.Input(shape=(n_features,)))

    for units in model_cfg.get("hidden_layers", [64, 32]):
        model.add(layers.Dense(units, kernel_regularizer=reg))
        if use_bn:
            model.add(layers.BatchNormalization())
        model.add(layers.Activation(activation))
        if dropout and dropout > 0:
            model.add(layers.Dropout(dropout))

    out_units, out_act, loss, metrics = _output_config(problem_type, n_classes)
    model.add(layers.Dense(out_units, activation=out_act, name="saida"))

    optimizer = _build_optimizer(
        train_cfg.get("optimizer", "adam"),
        train_cfg.get("learning_rate", 1e-3),
    )
    model.compile(optimizer=optimizer, loss=loss, metrics=metrics)

    model.summary()
    return model
