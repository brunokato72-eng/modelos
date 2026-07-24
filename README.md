# Pipeline genérico de Redes Neurais

Pipeline completo e **reutilizável** de rede neural (MLP com Keras/TensorFlow),
da **análise exploratória** até o **treino, validação e teste**. Feito para você
aplicar em **qualquer base de dados** apenas editando o `config.yaml` — sem mexer
no código.

O pipeline detecta sozinho se o problema é de **classificação** ou **regressão**
e adapta arquitetura, função de perda, métricas e gráficos automaticamente.

## O que ele faz (etapas)

1. **Carregamento** — lê `.csv`, `.xlsx/.xls` ou `.parquet` (`src/data_loader.py`)
2. **Análise Exploratória (EDA)** — estatísticas, valores faltantes, duplicatas,
   distribuição do alvo, distribuições numéricas e matriz de correlação, salvando
   gráficos em `outputs/figures/` (`src/eda.py`)
3. **Pré-processamento + split** — imputação de faltantes, encoding de categóricas,
   padronização, e divisão em **treino / validação / teste**. O pré-processador é
   ajustado **só no treino** para evitar vazamento de dados (`src/preprocessing.py`)
4. **Modelo** — MLP com camadas, dropout, batch-norm e regularização
   configuráveis (`src/model.py`)
5. **Treino** — usa treino + validação, com *early stopping*, *checkpoint* do
   melhor modelo, redução de learning rate e balanceamento de classes
   (`src/train.py`)
6. **Avaliação no teste** — métricas + gráficos (matriz de confusão / ROC AUC para
   classificação; MAE, RMSE, R², predito-vs-real e resíduos para regressão)
   (`src/evaluate.py`)
7. **Salvamento** — modelo, pré-processador, label encoder e métricas em `outputs/`

## Instalação

```bash
pip install -r requirements.txt
```

> Se preferir a versão só-CPU do TensorFlow: `pip install tensorflow-cpu`.

## Uso rápido (com dados de exemplo)

```bash
# 1. Gera uma base de exemplo (classificação)
python gerar_dados_exemplo.py
#    ou de regressão:  python gerar_dados_exemplo.py --tipo regressao

# 2. Roda o pipeline completo
python pipeline.py

# 3. (opcional) Prevê em dados novos com o modelo já treinado
python predict.py --input dados_novos.csv --output predicoes.csv
```

## Como aplicar na SUA base

Basta editar o `config.yaml`:

```yaml
data:
  path: "data/minha_base.csv"   # caminho do seu arquivo
  target: "coluna_alvo"         # nome da coluna que você quer prever
  csv_sep: ","                  # ";" se o seu CSV usar ponto e vírgula
  drop_columns: ["id"]          # colunas a ignorar (ids, etc.)

problem:
  type: "auto"                  # auto | classification | regression
```

Depois:

```bash
python pipeline.py
```

Pronto. O pipeline cuida do resto (detecção do tipo, encoding, split, treino,
avaliação). Todos os hiperparâmetros — camadas, dropout, épocas, learning rate,
tamanho dos splits — também estão no `config.yaml`.

## Estrutura do projeto

```
.
├── config.yaml              # única coisa que você precisa editar
├── pipeline.py              # roda tudo de ponta a ponta
├── predict.py               # inferência em dados novos
├── gerar_dados_exemplo.py   # cria uma base de teste
├── requirements.txt
├── src/
│   ├── data_loader.py       # carregamento
│   ├── eda.py               # análise exploratória
│   ├── preprocessing.py     # limpeza, encoding, scaling, split treino/val/teste
│   ├── model.py             # construção da rede neural
│   ├── train.py             # treino com validação
│   └── evaluate.py          # avaliação no teste
├── data/                    # coloque sua base aqui
└── outputs/
    ├── figures/             # gráficos (EDA, curva de treino, avaliação)
    ├── models/              # modelo + pré-processador salvos
    └── metricas.json        # métricas finais no teste
```

## Divisão treino / validação / teste

O split é feito em dois passos (definidos em `split` no config):

- `test_size` separa o conjunto de **teste** (dados nunca vistos, usados só no fim)
- `validation_size` separa a **validação** do que sobrou (usada durante o treino
  para *early stopping* e ajuste)
- o restante é o **treino**

Ex.: com `test_size: 0.20` e `validation_size: 0.20`, o resultado é
aproximadamente **64% treino / 16% validação / 20% teste**. Em classificação, o
split é **estratificado** por padrão (mantém a proporção das classes).

## Linha de comando

```bash
python pipeline.py --config config.yaml   # config alternativo
python pipeline.py --skip-eda             # pula a EDA
```
