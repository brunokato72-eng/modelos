# Databricks notebook source
# MAGIC %md
# MAGIC # Pipeline de Rede Neural — Regressão (Databricks)
# MAGIC
# MAGIC Notebook autocontido, da **análise exploratória** ao **treino, validação e teste**
# MAGIC de uma rede neural (MLP com Keras/TensorFlow) para um problema de **regressão**.
# MAGIC
# MAGIC **Como usar:**
# MAGIC 1. Ajuste a célula de **Configuração** (nome da tabela/arquivo, coluna alvo, colunas a remover).
# MAGIC 2. Rode as células de cima para baixo (`Run All` ou `Shift+Enter` em cada uma).
# MAGIC 3. As orientações de **ajuste de hiperparâmetros** estão na última seção.
# MAGIC
# MAGIC **Divisão dos dados:** treino / validação / teste. O pré-processador é ajustado
# MAGIC **somente no treino** para evitar vazamento de dados (*data leakage*).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Instalação das dependências
# MAGIC
# MAGIC A maioria dos clusters do Databricks ML já vem com `tensorflow`, `scikit-learn`,
# MAGIC `pandas`, `matplotlib` e `seaborn`. Se algo faltar, descomente a linha abaixo.
# MAGIC Após instalar via `%pip`, o Databricks reinicia o Python automaticamente.

# COMMAND ----------

# MAGIC %pip install --quiet tensorflow scikit-learn seaborn
# dbutils.library.restartPython()   # descomente se rodou o %pip acima

# COMMAND ----------

import os
import random

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers, regularizers

from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler, MinMaxScaler, OneHotEncoder
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

print("TensorFlow:", tf.__version__)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Configuração  ⬅️  (edite só esta célula para aplicar na sua base)

# COMMAND ----------

CONFIG = {
    # ---- Dados -------------------------------------------------------
    # Escolha a FONTE dos dados: "tabela" (Unity Catalog / Hive) ou "arquivo".
    "fonte": "tabela",                     # "tabela" | "arquivo"
    "tabela": "catalogo.schema.minha_tabela",   # usado se fonte == "tabela"
    "arquivo": "/dbfs/FileStore/minha_base.csv",  # usado se fonte == "arquivo"
    "arquivo_sep": ",",                    # separador do CSV ("," ou ";")

    "target": "coluna_alvo",               # coluna numérica que você quer prever
    "drop_columns": [],                    # colunas a ignorar (ids, datas, etc.)

    # ---- Split treino / validação / teste ----------------------------
    "test_size": 0.20,        # fração para TESTE
    "validation_size": 0.20,  # fração (do restante) para VALIDAÇÃO
    "random_state": 42,

    # ---- Pré-processamento ------------------------------------------
    "numeric_impute": "median",       # "mean" | "median" | "most_frequent"
    "categorical_impute": "most_frequent",
    "scaler": "standard",             # "standard" | "minmax" | "none"
    "max_cardinality": 30,            # categóricas com mais valores únicos são ignoradas

    # ---- Arquitetura da rede ----------------------------------------
    "hidden_layers": [128, 64, 32],   # neurônios por camada oculta
    "activation": "relu",
    "dropout": 0.2,                   # 0 desliga
    "batch_normalization": True,
    "l2": 0.0,                        # regularização L2 (0 desliga)

    # ---- Treino ------------------------------------------------------
    "epochs": 200,
    "batch_size": 32,
    "learning_rate": 0.001,
    "optimizer": "adam",              # "adam" | "rmsprop" | "sgd"
    "early_stopping_patience": 15,
    "reduce_lr_patience": 7,

    "seed": 42,
}

# Reprodutibilidade
os.environ["PYTHONHASHSEED"] = str(CONFIG["seed"])
random.seed(CONFIG["seed"])
np.random.seed(CONFIG["seed"])
tf.random.set_seed(CONFIG["seed"])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Carregamento dos dados
# MAGIC
# MAGIC Lê de uma **tabela** (Spark → pandas) ou de um **arquivo** no DBFS.
# MAGIC A rede neural treina com pandas/NumPy, então convertemos o Spark DataFrame
# MAGIC para pandas com `.toPandas()`.
# MAGIC
# MAGIC > **Base muito grande?** Amostre antes de trazer para o driver, ex.:
# MAGIC > `spark.table(...).sample(0.1).toPandas()`.

# COMMAND ----------

if CONFIG["fonte"] == "tabela":
    sdf = spark.table(CONFIG["tabela"])          # noqa: F821 (spark existe no Databricks)
    df = sdf.toPandas()
else:
    caminho = CONFIG["arquivo"]
    ext = os.path.splitext(caminho)[1].lower()
    if ext == ".csv":
        df = pd.read_csv(caminho, sep=CONFIG["arquivo_sep"])
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(caminho)
    elif ext == ".parquet":
        df = pd.read_parquet(caminho)
    else:
        raise ValueError(f"Extensão não suportada: {ext}")

assert CONFIG["target"] in df.columns, (
    f"Coluna alvo '{CONFIG['target']}' não existe. Colunas: {list(df.columns)}"
)

print(f"Dados: {df.shape[0]} linhas x {df.shape[1]} colunas")
display(df.head())   # noqa: F821 (display é do Databricks)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Análise Exploratória de Dados (EDA)

# COMMAND ----------

target = CONFIG["target"]

print("Tipos de dados:")
print(df.dtypes)

print("\nEstatísticas descritivas (numéricas):")
display(df.describe().T)   # noqa: F821

# Valores faltantes
missing = df.isnull().sum()
missing = missing[missing > 0].sort_values(ascending=False)
print("\nValores faltantes por coluna:")
if missing.empty:
    print("  Nenhum valor faltante.")
else:
    print(pd.DataFrame({"faltantes": missing, "%": (missing / len(df) * 100).round(2)}))

print(f"\nLinhas duplicadas: {df.duplicated().sum()}")

# COMMAND ----------

# Distribuição do alvo (contínuo)
fig, ax = plt.subplots(figsize=(8, 5))
sns.histplot(df[target].dropna(), kde=True, ax=ax)
ax.set_title(f"Distribuição do alvo: {target}")
plt.show()

# Dica: se o alvo for muito assimétrico (cauda longa), considere prever log(alvo).
# Veja a orientação na seção de hiperparâmetros.

# COMMAND ----------

# Distribuições das features numéricas
num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
if target in num_cols:
    num_cols.remove(target)

cols = num_cols[:16]
if cols:
    ncols = 4
    nrows = int(np.ceil(len(cols) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 3 * nrows))
    axes = np.array(axes).reshape(-1)
    for i, c in enumerate(cols):
        sns.histplot(df[c].dropna(), kde=True, ax=axes[i])
        axes[i].set_title(c, fontsize=9)
    for j in range(len(cols), len(axes)):
        axes[j].axis("off")
    fig.tight_layout()
    plt.show()

# COMMAND ----------

# Matriz de correlação (features numéricas + alvo)
corr_cols = df.select_dtypes(include=[np.number]).columns.tolist()
if len(corr_cols) >= 2:
    fig, ax = plt.subplots(figsize=(10, 8))
    corr = df[corr_cols].corr(numeric_only=True)
    sns.heatmap(corr, annot=len(corr) <= 15, fmt=".2f", cmap="coolwarm",
                center=0, ax=ax)
    ax.set_title("Matriz de correlação")
    plt.show()

    print("\nCorrelação de cada feature com o alvo (ordenada):")
    print(corr[target].drop(target).sort_values(ascending=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Pré-processamento + split treino / validação / teste
# MAGIC
# MAGIC - Remove colunas indicadas e linhas com alvo nulo.
# MAGIC - Imputa faltantes, codifica categóricas (one-hot) e padroniza numéricas.
# MAGIC - O `ColumnTransformer` é ajustado **só no treino** (`fit` no treino, `transform` em val/teste).

# COMMAND ----------

# Remove colunas indicadas
drop_cols = [c for c in CONFIG["drop_columns"] if c in df.columns]
if drop_cols:
    df = df.drop(columns=drop_cols)
    print("Colunas removidas:", drop_cols)

# Remove linhas sem alvo
antes = len(df)
df = df.dropna(subset=[target])
if len(df) < antes:
    print(f"Removidas {antes - len(df)} linhas com alvo nulo")

X = df.drop(columns=[target])
y = df[target].astype(float).values

# --- Split: primeiro separa TESTE, depois VALIDAÇÃO do restante ---
X_trainval, X_test, y_trainval, y_test = train_test_split(
    X, y, test_size=CONFIG["test_size"], random_state=CONFIG["random_state"]
)
X_train, X_val, y_train, y_val = train_test_split(
    X_trainval, y_trainval,
    test_size=CONFIG["validation_size"], random_state=CONFIG["random_state"]
)
print(f"Treino: {len(X_train)} | Validação: {len(X_val)} | Teste: {len(X_test)}")

# --- Monta o pré-processador ---
numeric_cols = X_train.select_dtypes(include=[np.number]).columns.tolist()
categorical_cols = X_train.select_dtypes(include=["object", "category"]).columns.tolist()

# Descarta categóricas de altíssima cardinalidade
high_card = [c for c in categorical_cols if X_train[c].nunique() > CONFIG["max_cardinality"]]
if high_card:
    print("Ignorando categóricas de alta cardinalidade:", high_card)
    categorical_cols = [c for c in categorical_cols if c not in high_card]

scaler = {"standard": StandardScaler(), "minmax": MinMaxScaler(),
          "none": "passthrough"}[CONFIG["scaler"]]
num_steps = [("imputer", SimpleImputer(strategy=CONFIG["numeric_impute"]))]
if scaler != "passthrough":
    num_steps.append(("scaler", scaler))

try:
    ohe = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
except TypeError:  # sklearn < 1.2
    ohe = OneHotEncoder(handle_unknown="ignore", sparse=False)

transformers = []
if numeric_cols:
    transformers.append(("num", Pipeline(num_steps), numeric_cols))
if categorical_cols:
    transformers.append(("cat", Pipeline([
        ("imputer", SimpleImputer(strategy=CONFIG["categorical_impute"])),
        ("encoder", ohe),
    ]), categorical_cols))

preprocessor = ColumnTransformer(transformers=transformers, remainder="drop")

X_train_p = np.asarray(preprocessor.fit_transform(X_train), dtype=np.float32)
X_val_p = np.asarray(preprocessor.transform(X_val), dtype=np.float32)
X_test_p = np.asarray(preprocessor.transform(X_test), dtype=np.float32)

n_features = X_train_p.shape[1]
print(f"Nº de features após encoding: {n_features}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Construção da rede neural (regressão)
# MAGIC
# MAGIC MLP com saída de **1 neurônio linear**, perda **MSE** e métrica **MAE** —
# MAGIC a configuração padrão para regressão.

# COMMAND ----------

def build_model(n_features, cfg):
    reg = regularizers.l2(cfg["l2"]) if cfg["l2"] > 0 else None
    model = keras.Sequential(name="mlp_regressao")
    model.add(keras.Input(shape=(n_features,)))

    for units in cfg["hidden_layers"]:
        model.add(layers.Dense(units, kernel_regularizer=reg))
        if cfg["batch_normalization"]:
            model.add(layers.BatchNormalization())
        model.add(layers.Activation(cfg["activation"]))
        if cfg["dropout"] > 0:
            model.add(layers.Dropout(cfg["dropout"]))

    model.add(layers.Dense(1, activation="linear", name="saida"))

    opt = {
        "adam": keras.optimizers.Adam,
        "rmsprop": keras.optimizers.RMSprop,
        "sgd": lambda learning_rate: keras.optimizers.SGD(learning_rate, momentum=0.9),
    }[cfg["optimizer"]](learning_rate=cfg["learning_rate"])

    model.compile(optimizer=opt, loss="mse", metrics=["mae", "mse"])
    return model


model = build_model(n_features, CONFIG)
model.summary()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Treinamento (base de treino + validação)
# MAGIC
# MAGIC Usa *early stopping* (para quando a validação para de melhorar e restaura os
# MAGIC melhores pesos) e redução automática do learning rate.

# COMMAND ----------

callbacks = [
    keras.callbacks.EarlyStopping(
        monitor="val_loss", patience=CONFIG["early_stopping_patience"],
        restore_best_weights=True, verbose=1,
    ),
    keras.callbacks.ReduceLROnPlateau(
        monitor="val_loss", factor=0.5,
        patience=CONFIG["reduce_lr_patience"], min_lr=1e-6, verbose=1,
    ),
]

history = model.fit(
    X_train_p, y_train,
    validation_data=(X_val_p, y_val),
    epochs=CONFIG["epochs"],
    batch_size=CONFIG["batch_size"],
    callbacks=callbacks,
    verbose=1,
)

# COMMAND ----------

# Curvas de aprendizado — treino vs validação
hist = history.history
fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
axes[0].plot(hist["loss"], label="treino")
axes[0].plot(hist["val_loss"], label="validação")
axes[0].set_title("Perda (MSE)"); axes[0].set_xlabel("época"); axes[0].legend()
axes[1].plot(hist["mae"], label="treino")
axes[1].plot(hist["val_mae"], label="validação")
axes[1].set_title("MAE"); axes[1].set_xlabel("época"); axes[1].legend()
plt.show()

# MAGIC %md
# Leitura das curvas:
# - treino e validação caindo juntas  -> ok
# - treino cai e validação sobe        -> OVERFITTING (aumente dropout/L2, reduza a rede)
# - as duas ficam altas e paradas      -> UNDERFITTING (aumente a rede/épocas ou o LR)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Avaliação no conjunto de TESTE (dados nunca vistos)

# COMMAND ----------

y_pred = model.predict(X_test_p, verbose=0).ravel()

mae = mean_absolute_error(y_test, y_pred)
rmse = np.sqrt(mean_squared_error(y_test, y_pred))
r2 = r2_score(y_test, y_pred)

print(f"MAE : {mae:.4f}")
print(f"RMSE: {rmse:.4f}")
print(f"R²  : {r2:.4f}")
print("\nGuia rápido do R²: 1.0 = perfeito | 0 = igual a prever a média | < 0 = pior que a média")

# COMMAND ----------

# Predito vs Real
fig, ax = plt.subplots(figsize=(7, 6))
ax.scatter(y_test, y_pred, alpha=0.5, edgecolor="k", linewidth=0.3)
lims = [min(y_test.min(), y_pred.min()), max(y_test.max(), y_pred.max())]
ax.plot(lims, lims, "r--", label="ideal (y = ŷ)")
ax.set_xlabel("Valor real"); ax.set_ylabel("Valor predito")
ax.set_title("Predito vs Real (teste)"); ax.legend()
plt.show()

# Resíduos
residuos = y_test - y_pred
fig, ax = plt.subplots(figsize=(7, 5))
sns.histplot(residuos, kde=True, ax=ax)
ax.set_title("Distribuição dos resíduos (teste)")
ax.set_xlabel("resíduo (real - predito)")
plt.show()
# Resíduos centrados em zero e simétricos = bom sinal.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Salvar o modelo e o pré-processador
# MAGIC
# MAGIC Salva em DBFS para reuso. Opcionalmente, registre no **MLflow** (última célula).

# COMMAND ----------

import joblib

save_dir = "/dbfs/FileStore/modelo_regressao"
os.makedirs(save_dir, exist_ok=True)
model.save(f"{save_dir}/modelo_final.keras")
joblib.dump(preprocessor, f"{save_dir}/preprocessor.joblib")
print("Modelo e pré-processador salvos em:", save_dir)

# --- Inferência em dados novos (exemplo) ---
# novos = spark.table("catalogo.schema.dados_novos").toPandas()
# Xn = np.asarray(preprocessor.transform(novos), dtype=np.float32)
# novos["predicao"] = model.predict(Xn, verbose=0).ravel()
# display(novos)

# COMMAND ----------

# MAGIC %md
# MAGIC ### (Opcional) Registrar no MLflow

# COMMAND ----------

# import mlflow
# import mlflow.tensorflow
# with mlflow.start_run(run_name="mlp_regressao"):
#     mlflow.log_params({k: CONFIG[k] for k in
#                        ["hidden_layers", "dropout", "l2", "learning_rate",
#                         "batch_size", "optimizer"]})
#     mlflow.log_metrics({"mae": mae, "rmse": rmse, "r2": r2})
#     mlflow.tensorflow.log_model(model, "modelo")

# COMMAND ----------

# MAGIC %md
# MAGIC # 📘 Orientações para ajuste dos hiperparâmetros
# MAGIC
# MAGIC Ajuste **um grupo de cada vez** e observe a métrica de **validação** (não a de treino).
# MAGIC A meta é `val_loss` baixa e curvas de treino e validação próximas.
# MAGIC
# MAGIC ### Diagnóstico rápido pelas curvas de aprendizado
# MAGIC | O que você vê | Significado | O que fazer |
# MAGIC |---|---|---|
# MAGIC | Treino baixo, validação alta e subindo | **Overfitting** | ↑ `dropout`, ↑ `l2`, ↓ camadas/neurônios, mais dados |
# MAGIC | Treino e validação altos e parados | **Underfitting** | ↑ camadas/neurônios, ↑ `epochs`, ↑ `learning_rate` |
# MAGIC | Perda oscilando muito (serrilhada) | LR alto ou batch pequeno | ↓ `learning_rate`, ↑ `batch_size` |
# MAGIC | Perda cai devagar demais | LR baixo | ↑ `learning_rate` |
# MAGIC
# MAGIC ### Guia por hiperparâmetro
# MAGIC
# MAGIC **`learning_rate`** — o mais importante. Comece em `0.001`. Se a perda explode ou
# MAGIC oscila muito, reduza (`0.0005`, `0.0001`). Se cai lento demais, aumente (`0.003`).
# MAGIC O `ReduceLROnPlateau` já reduz o LR sozinho quando a validação estagna.
# MAGIC
# MAGIC **`hidden_layers`** (tamanho/profundidade da rede) — mais neurônios/camadas =
# MAGIC mais capacidade, mas mais risco de overfitting. Sugestões por tamanho de base:
# MAGIC - poucos milhares de linhas: `[64, 32]`
# MAGIC - dezenas de milhares: `[128, 64, 32]`
# MAGIC - centenas de milhares ou mais: `[256, 128, 64]`
# MAGIC Uma boa prática é ir **afunilando** (cada camada com menos neurônios que a anterior).
# MAGIC
# MAGIC **`dropout`** — principal freio contra overfitting. Faixa útil: `0.1`–`0.5`.
# MAGIC Overfitting? aumente. Underfitting? reduza (ou `0`).
# MAGIC
# MAGIC **`l2`** — regularização alternativa/complementar ao dropout. Comece em `0`;
# MAGIC se houver overfitting, teste `1e-4` a `1e-2`.
# MAGIC
# MAGIC **`batch_normalization`** — deixe `True`. Estabiliza e acelera o treino; costuma
# MAGIC permitir learning rates um pouco maiores.
# MAGIC
# MAGIC **`batch_size`** — `32` é um bom padrão. Maior (`64`, `128`, `256`) treina mais
# MAGIC rápido e suaviza a curva, mas às vezes generaliza um pouco pior. Menor (`16`) dá
# MAGIC mais ruído (pode ajudar a escapar de mínimos locais). Base grande → batch maior.
# MAGIC
# MAGIC **`epochs`** — deixe **alto** (ex.: `200`) e confie no `early_stopping` para parar
# MAGIC na hora certa. Não custa deixar folgado.
# MAGIC
# MAGIC **`early_stopping_patience`** — quantas épocas sem melhora antes de parar.
# MAGIC `10`–`20`. Muito baixo pode parar cedo demais; muito alto desperdiça tempo.
# MAGIC
# MAGIC **`optimizer`** — `adam` resolve quase tudo. `rmsprop` é alternativa; `sgd`
# MAGIC (com momentum) pode generalizar melhor, mas exige LR mais ajustado.
# MAGIC
# MAGIC **`scaler`** — mantenha `standard` para redes neurais. Padronizar as features é
# MAGIC **essencial** para o treino convergir bem.
# MAGIC
# MAGIC ### Específico de regressão
# MAGIC - **Alvo assimétrico / com cauda longa** (ex.: preço, renda): treine em
# MAGIC   `log`. Use `y = np.log1p(y)` antes do split e reverta com `np.expm1(pred)` na
# MAGIC   avaliação. Costuma melhorar bastante MAE/RMSE.
# MAGIC - **Outliers no alvo:** o MSE penaliza muito outliers. Se atrapalharem, troque a
# MAGIC   loss para `"huber"` no `model.compile` (`loss=keras.losses.Huber()`).
# MAGIC - **Métrica de negócio:** RMSE pune erros grandes mais que o MAE. Escolha a que
# MAGIC   representa o custo real do seu problema para guiar os ajustes.
# MAGIC
# MAGIC ### Roteiro sugerido de tuning (nesta ordem)
# MAGIC 1. Rode com os padrões e olhe as **curvas de aprendizado**.
# MAGIC 2. Acerte o **`learning_rate`** (a curva deve descer de forma estável).
# MAGIC 3. Ajuste o **tamanho da rede** (`hidden_layers`) para sair do underfitting.
# MAGIC 4. Se aparecer overfitting, aumente **`dropout`** e/ou **`l2`**.
# MAGIC 5. Faça o ajuste fino de **`batch_size`** e **`epochs`/`patience`**.
# MAGIC 6. Só então avalie no **teste** — e evite ajustar olhando o teste (isso vaza
# MAGIC    informação e infla a métrica).
# MAGIC
# MAGIC ### (Opcional) Busca automática de hiperparâmetros
# MAGIC Para automatizar, o Databricks tem o **Hyperopt** (`from hyperopt import fmin, tpe, hp`),
# MAGIC que testa combinações minimizando a `val_loss`. Vale quando o ajuste manual
# MAGIC estagna e você quer explorar o espaço de forma sistemática.
