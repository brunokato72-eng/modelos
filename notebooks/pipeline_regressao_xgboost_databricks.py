# Databricks notebook source
# MAGIC %md
# MAGIC # Pipeline de Gradient Boosting (XGBoost) — Regressão (Databricks)
# MAGIC
# MAGIC Notebook autocontido, da **análise exploratória** ao **treino, validação e teste**
# MAGIC de um modelo de **Gradient Boosting (XGBoost)** para um problema de **regressão**.
# MAGIC
# MAGIC **Como usar:**
# MAGIC 1. Ajuste a célula de **Configuração** (nome da tabela/arquivo, coluna alvo, colunas a remover).
# MAGIC 2. Rode as células de cima para baixo (`Run All` ou `Shift+Enter` em cada uma).
# MAGIC 3. As orientações de **ajuste de hiperparâmetros** estão na última seção.
# MAGIC
# MAGIC **Divisão dos dados:** treino / validação / teste. O pré-processador é ajustado
# MAGIC **somente no treino** para evitar vazamento de dados (*data leakage*). A base de
# MAGIC **validação** é usada pelo XGBoost para *early stopping* (parar no nº ideal de árvores).
# MAGIC
# MAGIC > **Por que XGBoost?** Para dados tabulares costuma superar redes neurais, treina
# MAGIC > rápido, lida com valores faltantes nativamente e não exige padronização das features.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Instalação das dependências
# MAGIC
# MAGIC Clusters do Databricks ML já trazem `xgboost`, `scikit-learn`, `pandas`,
# MAGIC `matplotlib` e `seaborn`. Se algo faltar, descomente a linha abaixo.

# COMMAND ----------

# MAGIC %pip install --quiet xgboost scikit-learn seaborn
# dbutils.library.restartPython()   # descomente se rodou o %pip acima

# COMMAND ----------

import os

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

import xgboost as xgb
from xgboost import XGBRegressor

from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

print("XGBoost:", xgb.__version__)

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
    # XGBoost trata NaN numérico nativamente, então NÃO imputamos numéricas.
    # Categóricas recebem imputação + one-hot.
    "categorical_impute": "most_frequent",
    "max_cardinality": 30,            # categóricas com mais valores únicos são ignoradas

    # ---- Hiperparâmetros do XGBoost ---------------------------------
    "n_estimators": 2000,             # nº máximo de árvores (o early stopping corta o excesso)
    "learning_rate": 0.05,            # "eta": passo de cada árvore
    "max_depth": 6,                   # profundidade de cada árvore
    "min_child_weight": 1,            # peso mínimo por folha (↑ = mais conservador)
    "subsample": 0.8,                 # fração de linhas por árvore
    "colsample_bytree": 0.8,          # fração de colunas por árvore
    "gamma": 0.0,                     # ganho mínimo para dividir um nó
    "reg_alpha": 0.0,                 # regularização L1
    "reg_lambda": 1.0,                # regularização L2
    "early_stopping_rounds": 50,      # para se a validação não melhorar por N rodadas

    "n_jobs": -1,                     # usa todos os núcleos
    "seed": 42,
}

np.random.seed(CONFIG["seed"])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Carregamento dos dados
# MAGIC
# MAGIC Lê de uma **tabela** (Spark → pandas) ou de um **arquivo** no DBFS.
# MAGIC
# MAGIC > **Base muito grande?** Amostre antes de trazer para o driver, ex.:
# MAGIC > `spark.table(...).sample(0.1).toPandas()`. Para treino distribuído de verdade,
# MAGIC > veja a nota sobre `SparkXGBRegressor` no fim do notebook.

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
# MAGIC - Codifica categóricas (one-hot). Numéricas passam direto — o XGBoost lida com NaN.
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

try:
    ohe = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
except TypeError:  # sklearn < 1.2
    ohe = OneHotEncoder(handle_unknown="ignore", sparse=False)

transformers = []
if numeric_cols:
    # Numéricas passam direto: preservamos NaN para o XGBoost tratar nativamente.
    transformers.append(("num", "passthrough", numeric_cols))
if categorical_cols:
    transformers.append(("cat", Pipeline([
        ("imputer", SimpleImputer(strategy=CONFIG["categorical_impute"])),
        ("encoder", ohe),
    ]), categorical_cols))

preprocessor = ColumnTransformer(transformers=transformers, remainder="drop")

X_train_p = np.asarray(preprocessor.fit_transform(X_train), dtype=np.float32)
X_val_p = np.asarray(preprocessor.transform(X_val), dtype=np.float32)
X_test_p = np.asarray(preprocessor.transform(X_test), dtype=np.float32)

# Nomes das features (úteis para o gráfico de importância)
try:
    feature_names = list(preprocessor.get_feature_names_out())
except Exception:
    feature_names = [f"f{i}" for i in range(X_train_p.shape[1])]

print(f"Nº de features após encoding: {X_train_p.shape[1]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Treinamento do XGBoost (treino + validação com early stopping)
# MAGIC
# MAGIC O modelo treina no **treino** e monitora o erro na **validação** a cada árvore.
# MAGIC Quando a validação para de melhorar por `early_stopping_rounds` rodadas, o treino
# MAGIC para e guarda o melhor nº de árvores (`best_iteration`).

# COMMAND ----------

model = XGBRegressor(
    n_estimators=CONFIG["n_estimators"],
    learning_rate=CONFIG["learning_rate"],
    max_depth=CONFIG["max_depth"],
    min_child_weight=CONFIG["min_child_weight"],
    subsample=CONFIG["subsample"],
    colsample_bytree=CONFIG["colsample_bytree"],
    gamma=CONFIG["gamma"],
    reg_alpha=CONFIG["reg_alpha"],
    reg_lambda=CONFIG["reg_lambda"],
    objective="reg:squarederror",
    eval_metric="rmse",
    early_stopping_rounds=CONFIG["early_stopping_rounds"],
    n_jobs=CONFIG["n_jobs"],
    random_state=CONFIG["seed"],
)

model.fit(
    X_train_p, y_train,
    eval_set=[(X_train_p, y_train), (X_val_p, y_val)],
    verbose=False,
)

print(f"Melhor iteração (nº de árvores): {model.best_iteration + 1}")
print(f"Melhor RMSE na validação: {model.best_score:.4f}")

# COMMAND ----------

# Curva de aprendizado — RMSE por árvore (treino vs validação)
resultados = model.evals_result()
epochs = range(len(resultados["validation_0"]["rmse"]))
fig, ax = plt.subplots(figsize=(9, 5))
ax.plot(epochs, resultados["validation_0"]["rmse"], label="treino")
ax.plot(epochs, resultados["validation_1"]["rmse"], label="validação")
ax.axvline(model.best_iteration, color="gray", linestyle="--", label="best_iteration")
ax.set_xlabel("nº de árvores"); ax.set_ylabel("RMSE")
ax.set_title("Curva de aprendizado (XGBoost)"); ax.legend()
plt.show()

# MAGIC %md
# Leitura da curva:
# - treino e validação caindo juntas          -> ok
# - treino cai muito e validação estabiliza/sobe -> OVERFITTING (↓ max_depth, ↑ regularização, ↓ subsample/colsample)
# - as duas ficam altas                        -> UNDERFITTING (↑ max_depth, ↑ n_estimators, ↑ learning_rate)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Avaliação no conjunto de TESTE (dados nunca vistos)

# COMMAND ----------

y_pred = model.predict(X_test_p)

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
# MAGIC ## 7. Importância das features
# MAGIC
# MAGIC Quais variáveis mais pesaram no modelo. Um diferencial do XGBoost: ajuda a
# MAGIC entender o problema e a decidir o que remover/engenheirar.

# COMMAND ----------

importancias = (
    pd.DataFrame({"feature": feature_names, "importancia": model.feature_importances_})
    .sort_values("importancia", ascending=False)
    .head(20)
)

fig, ax = plt.subplots(figsize=(8, 7))
sns.barplot(data=importancias, y="feature", x="importancia", ax=ax)
ax.set_title("Top 20 features mais importantes")
plt.show()

display(importancias)   # noqa: F821

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Salvar o modelo e o pré-processador
# MAGIC
# MAGIC Salva em DBFS para reuso. Opcionalmente, registre no **MLflow** (última célula).

# COMMAND ----------

import joblib

save_dir = "/dbfs/FileStore/modelo_xgboost_regressao"
os.makedirs(save_dir, exist_ok=True)
model.save_model(f"{save_dir}/modelo_final.json")     # formato nativo do XGBoost
joblib.dump(preprocessor, f"{save_dir}/preprocessor.joblib")
print("Modelo e pré-processador salvos em:", save_dir)

# --- Inferência em dados novos (exemplo) ---
# novos = spark.table("catalogo.schema.dados_novos").toPandas()
# Xn = np.asarray(preprocessor.transform(novos), dtype=np.float32)
# novos["predicao"] = model.predict(Xn)
# display(novos)

# --- Recarregar depois ---
# from xgboost import XGBRegressor
# m = XGBRegressor(); m.load_model(f"{save_dir}/modelo_final.json")

# COMMAND ----------

# MAGIC %md
# MAGIC ### (Opcional) Registrar no MLflow

# COMMAND ----------

# import mlflow
# import mlflow.xgboost
# with mlflow.start_run(run_name="xgboost_regressao"):
#     mlflow.log_params({k: CONFIG[k] for k in
#                        ["n_estimators", "learning_rate", "max_depth",
#                         "min_child_weight", "subsample", "colsample_bytree",
#                         "gamma", "reg_alpha", "reg_lambda"]})
#     mlflow.log_metric("best_iteration", model.best_iteration + 1)
#     mlflow.log_metrics({"mae": mae, "rmse": rmse, "r2": r2})
#     mlflow.xgboost.log_model(model, "modelo")

# COMMAND ----------

# MAGIC %md
# MAGIC # 📘 Orientações para ajuste dos hiperparâmetros (XGBoost)
# MAGIC
# MAGIC Ajuste **um grupo de cada vez** e observe a métrica de **validação** (RMSE), não a
# MAGIC de treino. A meta é RMSE de validação baixo e curvas de treino/validação próximas.
# MAGIC
# MAGIC ### Diagnóstico rápido pela curva de aprendizado
# MAGIC | O que você vê | Significado | O que fazer |
# MAGIC |---|---|---|
# MAGIC | Treino cai muito, validação estabiliza/sobe | **Overfitting** | ↓ `max_depth`, ↑ `min_child_weight`, ↑ `gamma`, ↑ `reg_lambda`/`reg_alpha`, ↓ `subsample`/`colsample_bytree` |
# MAGIC | Treino e validação altos e parados | **Underfitting** | ↑ `max_depth`, ↑ `n_estimators`, ↑ `learning_rate` |
# MAGIC | Early stopping para muito cedo | LR alto demais | ↓ `learning_rate` e ↑ `n_estimators` |
# MAGIC
# MAGIC ### Guia por hiperparâmetro
# MAGIC
# MAGIC **`learning_rate`** (eta) — o mais importante. Menor = mais preciso, porém precisa
# MAGIC de mais árvores. Faixa típica: `0.01`–`0.3`. **Padrão de ouro:** use um LR baixo
# MAGIC (`0.03`–`0.05`), deixe `n_estimators` alto e confie no *early stopping*.
# MAGIC
# MAGIC **`n_estimators`** — nº máximo de árvores. Deixe **alto** (ex.: `2000`–`5000`); o
# MAGIC early stopping escolhe o nº ideal (`best_iteration`). Não precisa "acertar" na mão.
# MAGIC
# MAGIC **`max_depth`** — profundidade de cada árvore; controla a complexidade. Faixa:
# MAGIC `3`–`10`. Valor alto captura interações complexas mas causa overfitting rápido.
# MAGIC Comece em `6`. Base pequena/simples → `3`–`4`.
# MAGIC
# MAGIC **`min_child_weight`** — peso mínimo de amostras por folha. ↑ deixa o modelo mais
# MAGIC conservador (menos overfitting). Faixa: `1`–`10`.
# MAGIC
# MAGIC **`subsample`** — fração de **linhas** amostradas por árvore. `0.7`–`1.0`. Menor
# MAGIC que 1 adiciona aleatoriedade e reduz overfitting.
# MAGIC
# MAGIC **`colsample_bytree`** — fração de **colunas** por árvore. `0.7`–`1.0`. Mesma ideia
# MAGIC do subsample, mas nas features. Útil quando há muitas colunas.
# MAGIC
# MAGIC **`gamma`** (min_split_loss) — ganho mínimo para dividir um nó. `0` a `5`. ↑ torna
# MAGIC o modelo mais conservador (poda divisões pouco úteis).
# MAGIC
# MAGIC **`reg_lambda`** (L2) e **`reg_alpha`** (L1) — regularização dos pesos das folhas.
# MAGIC `reg_lambda` começa em `1`; aumente (`5`, `10`) contra overfitting. `reg_alpha`
# MAGIC começa em `0`; ↑ ajuda a zerar features irrelevantes (seleção esparsa).
# MAGIC
# MAGIC **`early_stopping_rounds`** — quantas árvores sem melhora antes de parar. `30`–`100`.
# MAGIC Precisa de `eval_set` com a validação (já configurado).
# MAGIC
# MAGIC ### Específico de regressão
# MAGIC - **Alvo assimétrico / cauda longa** (ex.: preço, renda): treine em `log`. Use
# MAGIC   `y = np.log1p(y)` antes do split e reverta com `np.expm1(pred)` na avaliação.
# MAGIC - **Outliers no alvo:** troque o objetivo para `objective="reg:pseudohubererror"`
# MAGIC   (Huber), menos sensível a outliers que o erro quadrático.
# MAGIC - **Métrica de negócio:** RMSE pune erros grandes mais que o MAE. Se preferir MAE,
# MAGIC   use `objective="reg:absoluteerror"` e `eval_metric="mae"`.
# MAGIC
# MAGIC ### Roteiro sugerido de tuning (nesta ordem)
# MAGIC 1. Fixe `learning_rate=0.05` e `n_estimators` alto; use early stopping. Veja a curva.
# MAGIC 2. Ajuste a **complexidade da árvore**: `max_depth` e `min_child_weight`.
# MAGIC 3. Ajuste a **aleatoriedade**: `subsample` e `colsample_bytree`.
# MAGIC 4. Se ainda houver overfitting, aumente **`gamma`**, **`reg_lambda`** e **`reg_alpha`**.
# MAGIC 5. No fim, **reduza o `learning_rate`** (ex.: `0.01`–`0.02`) e deixe o early stopping
# MAGIC    achar mais árvores — costuma ganhar o último ponto de performance.
# MAGIC 6. Só então avalie no **teste** — não ajuste olhando o teste (isso vaza informação).
# MAGIC
# MAGIC ### (Opcional) Busca automática e treino distribuído
# MAGIC - **Hyperopt** (nativo no Databricks): `from hyperopt import fmin, tpe, hp` para
# MAGIC   procurar combinações minimizando o RMSE de validação de forma sistemática.
# MAGIC - **Base gigante que não cabe no driver?** Use o `SparkXGBRegressor`
# MAGIC   (`from xgboost.spark import SparkXGBRegressor`), que treina distribuído sobre um
# MAGIC   Spark DataFrame, sem precisar do `.toPandas()`.
