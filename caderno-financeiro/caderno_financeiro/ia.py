"""Ponte com o Claude usando a assinatura — sem chave de API paga.

Toda chamada é feita pelo binário `claude` em modo headless (`claude -p`), que usa
a sessão já autenticada da assinatura (o mesmo login do `claude` interativo). Não
existe nenhuma leitura de ANTHROPIC_API_KEY neste projeto; se essa variável
estiver setada no ambiente, o `diagnostico()` avisa, porque aí a cobrança sairia
por token em vez de sair da assinatura.

Cada chamada roda isolada de propósito:
  --system-prompt          troca o prompt gigante do Claude Code por 3 linhas
  --tools ""               nenhuma ferramenta embutida (sem ler/escrever arquivo)
  --strict-mcp-config      ignora MCPs configurados na máquina
  --no-session-persistence não deixa rastro de sessão
  cwd em diretório vazio   não carrega CLAUDE.md de onde o comando foi chamado
Resultado prático: ~1k tokens de entrada por chamada em vez de ~38k.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Sequence

from . import config

TIMEOUT_PADRAO = int(os.environ.get("CADERNO_TIMEOUT", "180"))


class ErroIA(RuntimeError):
    pass


def binario_claude() -> str:
    caminho = os.environ.get("CADERNO_CLAUDE_BIN") or shutil.which("claude")
    if not caminho:
        raise ErroIA(
            "não encontrei o CLI `claude` no PATH.\n"
            "Instale com `npm install -g @anthropic-ai/claude-code` e faça login "
            "com a sua assinatura (`claude` e depois `/login`)."
        )
    return caminho


def chamar(
    prompt: str,
    *,
    sistema: str,
    modelo: str,
    schema: Optional[Dict[str, Any]] = None,
    ferramentas: Optional[Sequence[str]] = None,
    mcp_config: Optional[str] = None,
    timeout: int = TIMEOUT_PADRAO,
) -> Dict[str, Any]:
    """Executa uma chamada headless e devolve o JSON completo do CLI."""
    comando = [
        binario_claude(),
        "-p",
        "--output-format",
        "json",
        "--system-prompt",
        sistema,
        "--model",
        modelo,
        "--no-session-persistence",
        "--strict-mcp-config",
    ]
    if schema is not None:
        comando += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    if mcp_config:
        comando += ["--mcp-config", mcp_config]
    if ferramentas:
        comando += ["--allowed-tools", ",".join(ferramentas)]
        comando += ["--permission-mode", "acceptEdits"]
    else:
        comando += ["--tools", ""]

    with tempfile.TemporaryDirectory(prefix="caderno-ia-") as vazio:
        try:
            processo = subprocess.run(
                comando,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=vazio,
            )
        except subprocess.TimeoutExpired as erro:
            raise ErroIA(f"o Claude não respondeu em {timeout}s") from erro

    if processo.returncode != 0:
        raise ErroIA(_mensagem_de_falha(processo.stdout, processo.stderr))

    try:
        resposta = json.loads(processo.stdout)
    except json.JSONDecodeError as erro:
        raise ErroIA(f"resposta do CLI não é JSON:\n{processo.stdout[:800]}") from erro

    if resposta.get("is_error"):
        raise ErroIA(_mensagem_de_falha(processo.stdout, processo.stderr))
    return resposta


def _mensagem_de_falha(saida: str, erro: str) -> str:
    bruto = f"{saida}\n{erro}".strip()
    minusculo = bruto.lower()
    if "login" in minusculo or "authentic" in minusculo or "unauthorized" in minusculo or "invalid api key" in minusculo:
        return (
            "o Claude recusou a chamada por autenticação.\n"
            "Rode `claude` no terminal, use `/login` e entre com a conta da sua "
            "assinatura. Depois tente de novo.\n\n" + bruto[:800]
        )
    return f"falha ao chamar o Claude:\n{bruto[:1200]}"


def texto_da_resposta(resposta: Dict[str, Any]) -> str:
    return (resposta.get("result") or "").strip()


def json_da_resposta(resposta: Dict[str, Any]) -> Any:
    """Lê o JSON da resposta, tolerando cerca de ``` ou texto em volta."""
    bruto = texto_da_resposta(resposta)
    try:
        return json.loads(bruto)
    except json.JSONDecodeError:
        pass
    limpo = bruto.strip()
    if limpo.startswith("```"):
        limpo = limpo.split("\n", 1)[-1]
        if limpo.rstrip().endswith("```"):
            limpo = limpo.rstrip()[:-3]
    inicio = min(
        [p for p in (limpo.find("{"), limpo.find("[")) if p >= 0] or [-1]
    )
    fim = max(limpo.rfind("}"), limpo.rfind("]"))
    if inicio >= 0 and fim > inicio:
        try:
            return json.loads(limpo[inicio : fim + 1])
        except json.JSONDecodeError:
            pass
    raise ErroIA(f"não consegui ler JSON da resposta do modelo:\n{bruto[:800]}")


def diagnostico() -> Dict[str, Any]:
    """Confirma o requisito não-negociável: dá pra chamar o Claude sem chave paga."""
    info: Dict[str, Any] = {
        "binario": None,
        "versao": None,
        "apiKeyNoAmbiente": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "baseUrlCustomizada": os.environ.get("ANTHROPIC_BASE_URL"),
        "ok": False,
        "erro": None,
    }
    try:
        info["binario"] = binario_claude()
    except ErroIA as erro:
        info["erro"] = str(erro)
        return info

    try:
        versao = subprocess.run(
            [info["binario"], "--version"], capture_output=True, text=True, timeout=60
        )
        info["versao"] = versao.stdout.strip()
    except Exception as erro:  # pragma: no cover - ambiente sem binário utilizável
        info["erro"] = f"não consegui executar o CLI: {erro}"
        return info

    try:
        resposta = chamar(
            "Responda exatamente: pronto",
            sistema="Responda com uma palavra, sem explicação.",
            modelo=config.MODELO_EXTRACAO,
            timeout=120,
        )
        info["ok"] = True
        info["respostaTeste"] = texto_da_resposta(resposta)[:100]
        info["custoDaChamadaUsd"] = resposta.get("total_cost_usd")
    except ErroIA as erro:
        info["erro"] = str(erro)
    return info


# --------------------------------------------------------------------------
# 1) Extração de texto livre -> JSON estruturado
# --------------------------------------------------------------------------

SISTEMA_EXTRACAO = (
    "Você extrai lançamentos financeiros de mensagens curtas em português do Brasil. "
    "Devolve apenas os dados que a mensagem realmente diz. "
    "Você NUNCA inventa forma de pagamento ou conta: se a mensagem não disser, "
    "devolva null nesses campos (existe uma regra determinística fora daqui que "
    "preenche o que ficou em branco). Você também não faz contas de parcela: "
    "devolve o valor TOTAL da compra e o número de parcelas."
)

_SCHEMA_EXTRACAO = {
    "type": "object",
    "properties": {
        "lancamentos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": list(config.TIPOS)},
                    "categoria": {"type": "string", "enum": list(config.CATEGORIAS)},
                    "valorTotal": {"type": "number"},
                    "totalParcelas": {"type": "integer", "minimum": 1},
                    "data": {"type": "string"},
                    "formaPagamento": {"type": ["string", "null"]},
                    "conta": {"type": ["string", "null"]},
                    "descricao": {"type": "string"},
                },
                "required": [
                    "tipo",
                    "categoria",
                    "valorTotal",
                    "totalParcelas",
                    "data",
                    "formaPagamento",
                    "conta",
                    "descricao",
                ],
                "additionalProperties": False,
            },
        },
        "observacao": {"type": "string"},
    },
    "required": ["lancamentos", "observacao"],
    "additionalProperties": False,
}


def extrair_lancamentos(texto: str, hoje: str, contas_conhecidas: Sequence[str] = ()) -> Dict[str, Any]:
    contas = ", ".join(sorted({c for c in contas_conhecidas if c})) or "(nenhuma ainda)"
    prompt = f"""Hoje é {hoje} (AAAA-MM-DD).

Mensagem do usuário:
\"\"\"{texto}\"\"\"

Extraia os lançamentos citados. Regras:
- `valorTotal` é o valor TOTAL da compra, não o da parcela. Se disser "10x de 50",
  o total é 500 e totalParcelas é 10. Se não houver parcelamento, totalParcelas = 1.
- `data` em AAAA-MM-DD. Resolva datas relativas a partir de hoje ({hoje}):
  "ontem", "anteontem", "sexta passada", "dia 12", "semana passada". Sem data
  mencionada, use hoje.
- `tipo`: "Despesa" para gasto, "Receita" para entrada de dinheiro
  (salário, reembolso, restituição, venda, rendimento).
- `categoria`: escolha uma da lista permitida, coerente com o tipo.
- `formaPagamento`: só preencha se a mensagem disser (pix, crédito, débito,
  dinheiro, VR/vale). Caso contrário, null.
- `conta`: só preencha se a mensagem citar o nome do cartão/conta/carteira
  (ex.: Nubank, Itaú, Inter, Ifood). Caso contrário, null. Contas já usadas
  antes: {contas}.
- `descricao`: curta, o que foi comprado/recebido, sem repetir valor nem data.
- Se a mensagem tiver mais de um gasto, devolva um item por gasto.
- Se não der pra identificar nenhum lançamento, devolva a lista vazia e explique
  em `observacao`."""
    resposta = chamar(
        prompt,
        sistema=SISTEMA_EXTRACAO,
        modelo=config.MODELO_EXTRACAO,
        schema=_SCHEMA_EXTRACAO,
    )
    dados = json_da_resposta(resposta)
    if not isinstance(dados, dict) or not isinstance(dados.get("lancamentos"), list):
        raise ErroIA(f"formato inesperado na extração: {dados!r}")
    return dados


# --------------------------------------------------------------------------
# 2) Pergunta -> plano de consultas (a IA descreve filtros, não calcula)
# --------------------------------------------------------------------------

SISTEMA_PLANO = (
    "Você traduz perguntas sobre finanças pessoais em consultas estruturadas. "
    "Você NÃO faz nenhuma conta e NÃO estima valores: apenas descreve quais "
    "filtros e operações precisam ser executados. Um programa roda as contas "
    "depois, em cima dos dados reais."
)

_SCHEMA_PLANO = {
    "type": "object",
    "properties": {
        "consultas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rotulo": {"type": "string"},
                    "operacao": {"type": "string", "enum": ["soma", "media", "mediana",
                                                            "desviopadrao", "minimo",
                                                            "maximo", "contagem", "listar"]},
                    "tipo": {"type": ["string", "null"], "enum": [*config.TIPOS, None]},
                    "categoria": {"type": ["string", "null"]},
                    "formaPagamento": {"type": ["string", "null"]},
                    "conta": {"type": ["string", "null"]},
                    "mesInicio": {"type": ["string", "null"]},
                    "mesFim": {"type": ["string", "null"]},
                    "descricaoContem": {"type": ["string", "null"]},
                    "agruparPor": {"type": ["string", "null"],
                                   "enum": ["categoria", "formaPagamento", "conta",
                                            "tipo", "mes", None]},
                },
                "required": ["rotulo", "operacao", "tipo", "categoria", "formaPagamento",
                             "conta", "mesInicio", "mesFim", "descricaoContem", "agruparPor"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["consultas"],
    "additionalProperties": False,
}

LIMITE_CONSULTAS = 8


def planejar_consultas(
    pergunta: str,
    *,
    hoje: str,
    panorama: Dict[str, Any],
    historico: Sequence[Dict[str, str]] = (),
) -> List[Dict[str, Any]]:
    contexto_historico = ""
    if historico:
        linhas = [f"- Pergunta anterior: {h['pergunta']}\n  Resposta dada: {h['resposta'][:400]}"
                  for h in historico[-4:]]
        contexto_historico = "Contexto da conversa até aqui:\n" + "\n".join(linhas) + "\n\n"

    prompt = f"""Hoje é {hoje}. Mês atual: {hoje[:7]}.

{contexto_historico}Pergunta: \"\"\"{pergunta}\"\"\"

Panorama dos dados (sem valores, só pra você saber o que existe):
{json.dumps(panorama, ensure_ascii=False, indent=2)}

Monte até {LIMITE_CONSULTAS} consultas que, executadas, dão tudo que você precisa
pra responder. Regras:
- Períodos em AAAA-MM (mesInicio/mesFim). Um mês só: repita o mesmo valor nos dois.
- Sem período citado na pergunta, use o mês atual ({hoje[:7]}) — a não ser que a
  pergunta seja claramente sobre o histórico todo, aí deixe os dois null.
- `categoria`, `formaPagamento` e `conta` precisam ser exatamente um dos valores
  do panorama; se a pergunta for sobre algo que não é categoria (ex.: "uber",
  "farmácia"), use `descricaoContem` em vez de inventar categoria.
- Use `agruparPor` quando a pergunta pedir quebra ("por categoria", "por mês",
  "onde gastei mais").
- Use `operacao: "listar"` quando a pergunta pedir exemplos/maiores gastos.
- Campos que não se aplicam vão como null.
- `rotulo`: nome curto do que essa consulta responde.
- Vale pedir uma consulta de comparação (mês anterior, média histórica) quando
  ajudar a dar contexto à resposta."""

    resposta = chamar(prompt, sistema=SISTEMA_PLANO, modelo=config.MODELO_EXTRACAO,
                      schema=_SCHEMA_PLANO)
    dados = json_da_resposta(resposta)
    consultas = dados.get("consultas") if isinstance(dados, dict) else None
    if not isinstance(consultas, list) or not consultas:
        raise ErroIA("o modelo não devolveu nenhuma consulta pra executar")
    return consultas[:LIMITE_CONSULTAS]


# --------------------------------------------------------------------------
# 3) Resultados calculados -> resposta escrita
# --------------------------------------------------------------------------

SISTEMA_ANALISE = (
    "Você é o analista financeiro pessoal do Bruno. Fala português do Brasil, "
    "direto, sem enrolação e sem moralismo.\n"
    "REGRA ABSOLUTA: todo número que você escrever tem que vir dos resultados já "
    "calculados que te entregam. Você não soma, não estima, não arredonda por "
    "conta própria e não inventa valor que não esteja ali. Se o dado necessário "
    "não estiver nos resultados, diga que não dá pra afirmar.\n"
    "Formate dinheiro como R$ 1.234,56. Responda a pergunta primeiro, em uma ou "
    "duas frases. Depois, quando fizer sentido, acrescente uma leitura crítica "
    "curta (comparação, concentração de gasto, tendência) e no máximo uma "
    "recomendação prática. Pergunta pontual pode ter resposta curta."
)


def redigir_resposta(
    pergunta: str,
    resultados: Sequence[Dict[str, Any]],
    *,
    hoje: str,
    historico: Sequence[Dict[str, str]] = (),
) -> str:
    contexto_historico = ""
    if historico:
        linhas = [f"Você: {h['pergunta']}\nAnalista: {h['resposta'][:500]}" for h in historico[-4:]]
        contexto_historico = "Conversa até aqui:\n" + "\n\n".join(linhas) + "\n\n"

    prompt = f"""Hoje é {hoje}.

{contexto_historico}Pergunta: \"\"\"{pergunta}\"\"\"

Resultados JÁ CALCULADOS pelo sistema em cima dos dados reais (use só estes números):
{json.dumps(list(resultados), ensure_ascii=False, indent=2)}

Escreva a resposta. Se algum resultado veio com quantidade 0, diga que não há
lançamento no recorte em vez de dizer que o gasto foi zero."""

    resposta = chamar(prompt, sistema=SISTEMA_ANALISE, modelo=config.MODELO_ANALISE)
    return texto_da_resposta(resposta)
