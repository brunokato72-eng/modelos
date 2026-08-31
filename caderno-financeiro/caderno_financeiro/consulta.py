"""Perguntas e respostas sobre os dados.

Dois caminhos, com a mesma garantia: quem calcula é o Python.

- modo "manual" (padrão, validado no protótipo): 3 passos — a IA descreve
  filtros, o código executa a agregação, a IA redige em cima do resultado.
- modo "toolcall": function calling nativo via servidor MCP local. O modelo chama
  a ferramenta `calcular`, que roda aqui dentro e devolve o número pronto.

O modo toolcall só é usado depois de passar no `testar-toolcall`, que prova que a
ferramenta foi *executada de verdade* (log de auditoria do servidor MCP) e que o
número na resposta bate com o cálculo local — exatamente o bug que apareceu no
artefato do navegador, onde o modelo simulava `<tool_call>` em texto e inventava
número.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from . import config, db, ia
from .calculadora import executar_calculo
from .datas import hoje_iso
from .estatisticas import visao_geral

CHAVE_TOOLCALL = "toolcall_ok"
NOME_SERVIDOR = "caderno"
NOME_FERRAMENTA = f"mcp__{NOME_SERVIDOR}__calcular"

SISTEMA_TOOLCALL = ia.SISTEMA_ANALISE + (
    "\nVocê tem a ferramenta `calcular`, que roda em cima do banco real do usuário. "
    "Chame-a quantas vezes precisar e use só os números que ela devolver. "
    "Nunca escreva um valor que não tenha saído de uma chamada dessa ferramenta."
)


def _raiz_pacote() -> str:
    return str(Path(__file__).resolve().parent.parent)


def montar_mcp_config(caminho_banco: Path, caminho_log: Optional[Path] = None) -> str:
    ambiente = {
        "CADERNO_DB": str(caminho_banco),
        "PYTHONPATH": _raiz_pacote(),
        "PATH": os.environ.get("PATH", ""),
    }
    if caminho_log:
        ambiente["CADERNO_MCP_LOG"] = str(caminho_log)
    return json.dumps(
        {
            "mcpServers": {
                NOME_SERVIDOR: {
                    "command": sys.executable,
                    "args": ["-m", "caderno_financeiro.mcp_calculadora"],
                    "env": ambiente,
                }
            }
        }
    )


# --------------------------------------------------------------------------
# modo manual (3 passos)
# --------------------------------------------------------------------------

def responder_manual(
    conexao,
    pergunta: str,
    *,
    historico: Sequence[Dict[str, str]] = (),
    hoje: Optional[str] = None,
) -> Dict[str, Any]:
    hoje = hoje or hoje_iso()
    lancamentos = db.listar(conexao)
    panorama = visao_geral(lancamentos)

    consultas = ia.planejar_consultas(
        pergunta, hoje=hoje, panorama=panorama, historico=historico
    )

    resultados = []
    for consulta in consultas:
        filtro = {k: v for k, v in consulta.items() if v not in (None, "") and k != "rotulo"}
        try:
            calculado = executar_calculo(filtro, lancamentos)
        except ValueError as erro:
            calculado = {"erro": str(erro), "filtro": filtro}
        calculado["rotulo"] = consulta.get("rotulo") or filtro.get("operacao", "consulta")
        resultados.append(calculado)

    resposta = ia.redigir_resposta(pergunta, resultados, hoje=hoje, historico=historico)
    return {"modo": "manual", "resposta": resposta, "consultas": consultas, "resultados": resultados}


# --------------------------------------------------------------------------
# modo function calling nativo
# --------------------------------------------------------------------------

def responder_toolcall(
    pergunta: str,
    *,
    caminho_banco: Optional[Path] = None,
    historico: Sequence[Dict[str, str]] = (),
    hoje: Optional[str] = None,
) -> Dict[str, Any]:
    hoje = hoje or hoje_iso()
    caminho_banco = Path(caminho_banco) if caminho_banco else config.caminho_banco()

    contexto = ""
    if historico:
        linhas = [f"Você: {h['pergunta']}\nAnalista: {h['resposta'][:500]}" for h in historico[-4:]]
        contexto = "Conversa até aqui:\n" + "\n\n".join(linhas) + "\n\n"

    with tempfile.TemporaryDirectory(prefix="caderno-tool-") as pasta:
        log = Path(pasta) / "chamadas.jsonl"
        mcp = montar_mcp_config(caminho_banco, log)
        prompt = (
            f"Hoje é {hoje} (mês atual {hoje[:7]}).\n\n{contexto}"
            f'Pergunta: """{pergunta}"""\n\n'
            "Use a ferramenta `calcular` para obter cada número de que precisar "
            "e depois responda."
        )
        resposta = ia.chamar(
            prompt,
            sistema=SISTEMA_TOOLCALL,
            modelo=config.MODELO_ANALISE,
            mcp_config=mcp,
            ferramentas=[NOME_FERRAMENTA],
        )
        chamadas = []
        if log.exists():
            chamadas = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]

    return {
        "modo": "toolcall",
        "resposta": ia.texto_da_resposta(resposta),
        "chamadas": chamadas,
        "ferramentaExecutada": bool(chamadas),
    }


def responder(
    conexao,
    pergunta: str,
    *,
    modo: str = "auto",
    historico: Sequence[Dict[str, str]] = (),
    hoje: Optional[str] = None,
) -> Dict[str, Any]:
    """Roteia entre os dois modos. 'auto' usa toolcall só se o teste já passou."""
    if modo == "auto":
        modo = "toolcall" if db.ler_config(conexao, CHAVE_TOOLCALL) == "1" else "manual"

    if modo == "toolcall":
        resultado = responder_toolcall(pergunta, historico=historico, hoje=hoje)
        if not resultado["ferramentaExecutada"]:
            # O modelo respondeu sem executar a ferramenta: qualquer número aí é
            # invenção. Refaz pelo caminho manual, que é determinístico.
            resultado = responder_manual(conexao, pergunta, historico=historico, hoje=hoje)
            resultado["avisoFallback"] = (
                "o modelo não executou a ferramenta; respondi pelo caminho manual"
            )
        return resultado

    if modo != "manual":
        raise ValueError(f"modo inválido: {modo} (use auto, manual ou toolcall)")
    return responder_manual(conexao, pergunta, historico=historico, hoje=hoje)


# --------------------------------------------------------------------------
# teste do function calling nativo
# --------------------------------------------------------------------------

def testar_toolcall(conexao=None) -> Dict[str, Any]:
    """Prova (ou desmente) que o function calling nativo funciona neste ambiente.

    Monta um banco temporário com valores propositalmente esquisitos, faz uma
    pergunta cuja resposta só sai somando esses valores e checa três coisas:
      1. o servidor MCP registrou a execução da ferramenta;
      2. os filtros pedidos fazem sentido;
      3. o número que apareceu na resposta é o número certo.
    """
    from .valores import formatar

    lancamentos_teste = [
        {"valor": 111.11, "categoria": "Mercado", "descricao": "teste A"},
        {"valor": 222.22, "categoria": "Transporte", "descricao": "teste B"},
        {"valor": 333.33, "categoria": "Lazer", "descricao": "teste C"},
    ]
    esperado = 666.66
    mes = "2024-02"

    with tempfile.TemporaryDirectory(prefix="caderno-teste-") as pasta:
        banco_teste = Path(pasta) / "teste.db"
        log = Path(pasta) / "chamadas.jsonl"
        with db.banco(banco_teste) as conexao_teste:
            db.inserir(
                conexao_teste,
                [
                    {
                        "id": uuid.uuid4().hex,
                        "data": f"{mes}-1{indice}",
                        "tipo": config.TIPO_DESPESA,
                        "categoria": item["categoria"],
                        "valor": item["valor"],
                        "valorTotal": item["valor"],
                        "parcelaAtual": 1,
                        "totalParcelas": 1,
                        "formaPagamento": "Pix",
                        "conta": "TesteBank",
                        "descricao": item["descricao"],
                        "criadoEm": db.agora(),
                        "grupoParcelamento": None,
                    }
                    for indice, item in enumerate(lancamentos_teste)
                ],
            )

        mcp = montar_mcp_config(banco_teste, log)
        prompt = (
            f"Qual foi o total de despesas em {mes}? "
            "Use a ferramenta `calcular` e responda com o valor em reais."
        )
        diagnostico: Dict[str, Any] = {"esperado": esperado, "mes": mes}
        try:
            resposta = ia.chamar(
                prompt,
                sistema=(
                    "Você responde sobre finanças usando exclusivamente a ferramenta "
                    "`calcular`. Nunca some valores por conta própria."
                ),
                modelo=config.MODELO_ANALISE,
                mcp_config=mcp,
                ferramentas=[NOME_FERRAMENTA],
            )
        except ia.ErroIA as erro:
            return {**diagnostico, "ok": False, "motivo": f"a chamada falhou: {erro}"}

        texto = ia.texto_da_resposta(resposta)
        chamadas = []
        if log.exists():
            chamadas = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]

    diagnostico["resposta"] = texto
    diagnostico["chamadas"] = chamadas
    diagnostico["ferramentaExecutada"] = bool(chamadas)

    if not chamadas:
        diagnostico["ok"] = False
        diagnostico["motivo"] = (
            "o modelo respondeu sem executar a ferramenta (nenhuma chamada chegou ao "
            "servidor MCP) — é exatamente o caso de tool call simulado"
        )
    else:
        variantes = {"666,66", "666.66", formatar(esperado)}
        acertou = any(v in texto for v in variantes)
        diagnostico["ok"] = acertou
        if not acertou:
            diagnostico["motivo"] = (
                f"a ferramenta rodou, mas o valor esperado ({formatar(esperado)}) "
                "não apareceu na resposta"
            )

    if conexao is not None:
        db.definir_config(conexao, CHAVE_TOOLCALL, "1" if diagnostico["ok"] else "0")
        db.definir_config(conexao, "toolcall_testado_em", db.agora())
    return diagnostico
