"""Constantes de domínio e localização do banco.

Tudo aqui vem do briefing e é tratado como contrato: as listas de categorias e
formas de pagamento são as únicas aceitas pelo sistema (a IA recebe essas listas
e o importador normaliza variações de grafia para elas).
"""

from __future__ import annotations

import os
from pathlib import Path

TIPO_DESPESA = "Despesa"
TIPO_RECEITA = "Receita"
TIPOS = (TIPO_DESPESA, TIPO_RECEITA)

CATEGORIAS_DESPESA = (
    "Mercado",
    "Alimentação",
    "Transporte",
    "Moradia",
    "Saúde",
    "Lazer",
    "Compras",
    "Besteiras",
    "Dívidas",
    "Educação",
    "Pessoal",
    "Assinaturas",
    "Outros",
)

CATEGORIAS_RECEITA = (
    "Salário",
    "Reembolso",
    "Outras entradas",
)

CATEGORIAS = CATEGORIAS_DESPESA + CATEGORIAS_RECEITA

FORMAS_PAGAMENTO = (
    "Pix",
    "Cartão de crédito",
    "Cartão de débito",
    "Dinheiro",
    "VR",
)

# Regras determinísticas (ver regras.py)
FORMA_PADRAO = "Cartão de crédito"
CONTA_PADRAO = "Nubank"
CONTA_VR = "Ifood"
FORMA_DA_CONTA_VR = "VR"

# Modelos usados em cada tipo de chamada. Extração e planejamento de consulta são
# tarefas curtas e estruturadas (haiku dá conta); a redação da análise final é a
# parte que se beneficia de um modelo maior.
MODELO_EXTRACAO = os.environ.get("CADERNO_MODELO_EXTRACAO", "haiku")
MODELO_ANALISE = os.environ.get("CADERNO_MODELO_ANALISE", "sonnet")


def diretorio_dados() -> Path:
    """Diretório onde vivem banco, backups e estado. Sobrescrevível por env."""
    base = os.environ.get("CADERNO_HOME")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".caderno-financeiro"


def caminho_banco() -> Path:
    explicito = os.environ.get("CADERNO_DB")
    if explicito:
        return Path(explicito).expanduser()
    return diretorio_dados() / "caderno.db"
