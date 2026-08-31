"""Regras de negócio determinísticas.

Nada aqui é decidido pela IA. A IA extrai o que foi *dito* (e deixa null o que não
foi); estas funções preenchem o resto sempre do mesmo jeito, em código puro.

Regras do briefing:
  1. Não mencionou forma de pagamento nem conta -> "Cartão de crédito" + "Nubank".
  2. Conta é "Ifood" -> forma de pagamento é "VR" (tem prioridade sobre a 1).
  3. O que foi dito explicitamente é respeitado; as regras acima só preenchem branco.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

from . import config
from .datas import somar_meses, validar_iso
from .texto import normalizar
from .valores import para_centavos, para_reais

# Sinônimos aceitos ao normalizar o que veio da IA ou de uma planilha.
_ALIAS_FORMA = {
    "pix": "Pix",
    "cartao de credito": "Cartão de crédito",
    "cartao credito": "Cartão de crédito",
    "credito": "Cartão de crédito",
    "cartao": "Cartão de crédito",
    "cred": "Cartão de crédito",
    "cartao de debito": "Cartão de débito",
    "cartao debito": "Cartão de débito",
    "debito": "Cartão de débito",
    "deb": "Cartão de débito",
    "dinheiro": "Dinheiro",
    "especie": "Dinheiro",
    "cash": "Dinheiro",
    "vr": "VR",
    "va": "VR",
    "vale refeicao": "VR",
    "vale alimentacao": "VR",
    "voucher": "VR",
}

_ALIAS_TIPO = {
    "despesa": config.TIPO_DESPESA,
    "despesas": config.TIPO_DESPESA,
    "gasto": config.TIPO_DESPESA,
    "gastos": config.TIPO_DESPESA,
    "saida": config.TIPO_DESPESA,
    "debito": config.TIPO_DESPESA,
    "receita": config.TIPO_RECEITA,
    "receitas": config.TIPO_RECEITA,
    "entrada": config.TIPO_RECEITA,
    "entradas": config.TIPO_RECEITA,
    "ganho": config.TIPO_RECEITA,
    "credito": config.TIPO_RECEITA,
}

_ALIAS_CATEGORIA = {
    "outros receita": "Outras entradas",
    "outros receitas": "Outras entradas",
    "outras receitas": "Outras entradas",
    "outra entrada": "Outras entradas",
    "outros entradas": "Outras entradas",
    "salario": "Salário",
    "salarios": "Salário",
    "restituicao": "Outras entradas",
    "rendimento": "Outras entradas",
    "rendimentos": "Outras entradas",
    "reembolsos": "Reembolso",
    "supermercado": "Mercado",
    "mercados": "Mercado",
    "comida": "Alimentação",
    "restaurante": "Alimentação",
    "delivery": "Alimentação",
    "ifood": "Alimentação",
    "uber": "Transporte",
    "combustivel": "Transporte",
    "transportes": "Transporte",
    "casa": "Moradia",
    "aluguel": "Moradia",
    "contas": "Moradia",
    "saude": "Saúde",
    "farmacia": "Saúde",
    "lazer e entretenimento": "Lazer",
    "entretenimento": "Lazer",
    "compra": "Compras",
    "vestuario": "Compras",
    "besteira": "Besteiras",
    "divida": "Dívidas",
    "dividas": "Dívidas",
    "emprestimo": "Dívidas",
    "educacao": "Educação",
    "cursos": "Educação",
    "curso": "Educação",
    "pessoais": "Pessoal",
    "assinatura": "Assinaturas",
    "streaming": "Assinaturas",
    "outro": "Outros",
}

_CATEGORIA_POR_NORMAL = {normalizar(c): c for c in config.CATEGORIAS}
_FORMA_POR_NORMAL = {normalizar(f): f for f in config.FORMAS_PAGAMENTO}


def normalizar_tipo(bruto, padrao: str = config.TIPO_DESPESA) -> str:
    chave = normalizar(bruto)
    if not chave:
        return padrao
    if chave in {normalizar(t) for t in config.TIPOS}:
        return config.TIPO_DESPESA if chave == "despesa" else config.TIPO_RECEITA
    return _ALIAS_TIPO.get(chave, padrao)


def categoria_canonica_estrita(bruto):
    """Devolve a categoria canônica só quando realmente casou (exato ou alias).

    Diferente de `normalizar_categoria`, não tem fallback: serve pra decidir se
    uma categoria de planilha é de receita ou de despesa sem chutar.
    """
    chave = normalizar(bruto).replace("_", " ").replace("-", " ")
    chave = " ".join(chave.split())
    if not chave:
        return None
    if chave in _CATEGORIA_POR_NORMAL:
        return _CATEGORIA_POR_NORMAL[chave]
    return _ALIAS_CATEGORIA.get(chave)


def normalizar_categoria(bruto, tipo: str = config.TIPO_DESPESA) -> str:
    """Casa a categoria ignorando acento/caixa; cai no 'Outros' do tipo se não casar."""
    chave = normalizar(bruto).replace("_", " ").replace("-", " ")
    chave = " ".join(chave.split())
    if chave in _CATEGORIA_POR_NORMAL:
        return _CATEGORIA_POR_NORMAL[chave]
    if chave in _ALIAS_CATEGORIA:
        return _ALIAS_CATEGORIA[chave]
    return "Outras entradas" if tipo == config.TIPO_RECEITA else "Outros"


def normalizar_forma(bruto) -> str:
    """Devolve a forma canônica, ou '' se não foi mencionada/reconhecida."""
    chave = normalizar(bruto).replace("_", " ").replace("-", " ")
    chave = " ".join(chave.split())
    if not chave:
        return ""
    if chave in _FORMA_POR_NORMAL:
        return _FORMA_POR_NORMAL[chave]
    return _ALIAS_FORMA.get(chave, "")


def eh_conta_vr(conta) -> bool:
    return normalizar(conta) == normalizar(config.CONTA_VR)


def aplicar_regras(forma_bruta, conta_bruta) -> tuple:
    """Aplica as regras 1-3 e devolve (forma_pagamento, conta) já preenchidos.

    Ordem: o que foi dito manda (regra 3); Ifood preenche VR (regra 2, com
    prioridade); os dois em branco caem no padrão (regra 1).
    """
    conta = str(conta_bruta or "").strip()
    forma = normalizar_forma(forma_bruta)
    conta_mencionada = bool(conta)
    forma_mencionada = bool(forma)

    if eh_conta_vr(conta):
        conta = config.CONTA_VR  # grafia canônica
        if not forma_mencionada:
            forma = config.FORMA_DA_CONTA_VR
        return forma, conta

    if not forma_mencionada and not conta_mencionada:
        return config.FORMA_PADRAO, config.CONTA_PADRAO

    if not forma_mencionada:
        # Conta citada mas forma não: o briefing não cobre esse caso explicitamente;
        # completamos com a mesma forma padrão da regra 1 pra nunca gravar em branco.
        forma = config.FORMA_PADRAO

    return forma, conta


def dividir_parcelas(valor_total: float, total_parcelas: int) -> List[float]:
    """Divide em centavos e joga a sobra na última parcela (soma exata garantida)."""
    if total_parcelas < 1:
        raise ValueError("total de parcelas precisa ser >= 1")
    total_centavos = para_centavos(valor_total)
    base = total_centavos // total_parcelas
    parcelas = [base] * total_parcelas
    parcelas[-1] = total_centavos - base * (total_parcelas - 1)
    return [para_reais(c) for c in parcelas]


def montar_lancamentos(
    *,
    data: str,
    tipo: str,
    categoria: str,
    valor_total: float,
    total_parcelas: int = 1,
    forma_pagamento=None,
    conta=None,
    descricao: str = "",
    criado_em: str,
) -> List[Dict[str, Any]]:
    """Transforma um lançamento extraído em uma ou N linhas prontas pro banco.

    Uma linha por parcela, com a data de cada mês seguinte (com clamp de dia) e o
    valor da última ajustado pra fechar a soma exata.
    """
    validar_iso(data)
    tipo = normalizar_tipo(tipo)
    categoria = normalizar_categoria(categoria, tipo)
    forma, conta_final = aplicar_regras(forma_pagamento, conta)
    total_parcelas = max(1, int(total_parcelas or 1))
    valores = dividir_parcelas(valor_total, total_parcelas)
    grupo = uuid.uuid4().hex if total_parcelas > 1 else None

    linhas = []
    for indice, valor_parcela in enumerate(valores):
        linhas.append(
            {
                "id": uuid.uuid4().hex,
                "data": data if indice == 0 else somar_meses(data, indice),
                "tipo": tipo,
                "categoria": categoria,
                "valor": valor_parcela,
                "valorTotal": round(float(valor_total), 2),
                "parcelaAtual": indice + 1,
                "totalParcelas": total_parcelas,
                "formaPagamento": forma,
                "conta": conta_final,
                "descricao": (descricao or "").strip(),
                "criadoEm": criado_em,
                "grupoParcelamento": grupo,
            }
        )
    return linhas
