"""Interface de linha de comando do caderno financeiro."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import config, consulta, db, estatisticas, exportador, ia, importador, registro
from .calculadora import OPERACOES, executar_calculo
from .datas import mes_atual, validar_mes
from .valores import formatar

VERDE = "\033[32m"
VERMELHO = "\033[31m"
AMARELO = "\033[33m"
CINZA = "\033[90m"
NEGRITO = "\033[1m"
FIM = "\033[0m"


def _colorido() -> bool:
    return sys.stdout.isatty()


def pintar(texto: str, cor: str) -> str:
    return f"{cor}{texto}{FIM}" if _colorido() else texto


def imprimir_json(dados: Any) -> None:
    print(json.dumps(dados, ensure_ascii=False, indent=2, default=str))


def _linha_lancamento(lanc: Dict[str, Any]) -> str:
    parcela = f" [{lanc['parcelaAtual']}/{lanc['totalParcelas']}]" if (lanc.get("totalParcelas") or 1) > 1 else ""
    sinal = "-" if lanc.get("tipo") == config.TIPO_DESPESA else "+"
    pagamento = lanc.get("formaPagamento") or ""
    if lanc.get("conta"):
        pagamento = f"{pagamento} · {lanc['conta']}"
    corpo = (
        f"{lanc['data']}  {sinal}{formatar(lanc['valor']):>14}  "
        f"{lanc['categoria']:<14}{parcela}  {lanc.get('descricao', '')}"
    )
    return corpo + pintar(f"  ({pagamento})", CINZA)


# --------------------------------------------------------------------------
# comandos
# --------------------------------------------------------------------------

def cmd_init(args) -> int:
    with db.banco(args.banco) as conexao:
        total = db.contar(conexao)
    print(f"banco pronto em {pintar(str(config.caminho_banco() if not args.banco else args.banco), NEGRITO)}")
    print(f"lançamentos existentes: {total}")
    return 0


def cmd_auth(args) -> int:
    info = ia.diagnostico()
    if args.json:
        imprimir_json(info)
        return 0 if info["ok"] else 1

    print(pintar("Autenticação", NEGRITO))
    print(f"  CLI claude: {info['binario'] or pintar('não encontrado', VERMELHO)}")
    if info.get("versao"):
        print(f"  versão:     {info['versao']}")
    if info["apiKeyNoAmbiente"]:
        print(
            pintar(
                "  ATENÇÃO: ANTHROPIC_API_KEY está setada no ambiente. Com ela, o CLI "
                "cobra por token em vez de usar a assinatura.\n"
                "  Rode `unset ANTHROPIC_API_KEY` antes de usar o caderno.",
                AMARELO,
            )
        )
    else:
        print(f"  chave de API no ambiente: {pintar('nenhuma (usa a assinatura)', VERDE)}")
    if info["ok"]:
        print(pintar(f"  chamada de teste: OK ({info.get('respostaTeste','')!r})", VERDE))
        return 0
    print(pintar(f"  chamada de teste falhou:\n{info['erro']}", VERMELHO))
    return 1


def cmd_registrar(args) -> int:
    texto = " ".join(args.texto).strip()
    if args.audio:
        print(pintar("transcrevendo áudio (local)...", CINZA))
        transcrito = registro.transcrever(Path(args.audio))
        print(f"  ouvi: {transcrito!r}")
        texto = f"{texto} {transcrito}".strip()
    if not texto:
        print(pintar("nada pra registrar (passe um texto ou --audio)", VERMELHO))
        return 1

    with db.banco(args.banco) as conexao:
        preparado = registro.preparar(conexao, texto, data_forcada=args.data)
        if not preparado["grupos"]:
            print(pintar("não identifiquei nenhum lançamento nessa mensagem.", AMARELO))
            if preparado.get("observacao"):
                print(f"  {preparado['observacao']}")
            return 1

        for grupo in preparado["grupos"]:
            linhas = grupo["linhas"]
            primeira = linhas[0]
            print(pintar(f"\n{primeira['tipo']}: {primeira['descricao'] or '(sem descrição)'}", NEGRITO))
            print(f"  categoria:  {primeira['categoria']}")
            print(f"  valor:      {formatar(primeira['valorTotal'])}"
                  + (f" em {primeira['totalParcelas']}x de {formatar(primeira['valor'])}"
                     if primeira["totalParcelas"] > 1 else ""))
            print(f"  pagamento:  {primeira['formaPagamento']}"
                  + (f" · {primeira['conta']}" if primeira["conta"] else ""))
            print(f"  data:       {primeira['data']}")
            if len(linhas) > 1:
                ultimas = ", ".join(f"{l['data']} {formatar(l['valor'])}" for l in linhas[:3])
                print(f"  parcelas:   {ultimas}, ... até {linhas[-1]['data']} "
                      f"({formatar(linhas[-1]['valor'])})")

        if args.simular:
            print(pintar("\n(simulação — nada foi salvo)", CINZA))
            return 0

        if not args.sim and sys.stdin.isatty():
            resposta = input(pintar(f"\nsalvar {preparado['totalLinhas']} lançamento(s)? [S/n] ", NEGRITO))
            if resposta.strip().lower() in ("n", "nao", "não"):
                print("cancelado.")
                return 1

        total = registro.salvar(conexao, preparado)
        print(pintar(f"\n{total} lançamento(s) salvo(s).", VERDE))
    return 0


def cmd_resumo(args) -> int:
    mes = validar_mes(args.mes) if args.mes else mes_atual()
    with db.banco(args.banco) as conexao:
        lancamentos = db.listar(conexao)
    resumo = estatisticas.resumo_mensal(lancamentos, mes)

    if args.json:
        imprimir_json(resumo)
        return 0

    print(pintar(f"\nResumo de {mes}", NEGRITO))
    print(f"  despesas: {formatar(resumo['totalDespesas'])}   "
          f"receitas: {formatar(resumo['totalReceitas'])}   "
          f"saldo: {pintar(formatar(resumo['saldo']), VERDE if resumo['saldo'] >= 0 else VERMELHO)}")
    print(f"  {resumo['quantidadeLancamentos']} lançamentos · "
          f"ticket médio de despesa {formatar(resumo['ticketMedioDespesa'])} · "
          f"parcelas no mês {formatar(resumo['comprometidoParcelas'])}")

    anterior = resumo.get("mesAnterior") or {}
    variacao = anterior.get("variacaoPercentual")
    if anterior.get("totalDespesas") and variacao is not None:
        seta = "↑" if variacao > 0 else "↓"
        cor = VERMELHO if variacao > 0 else VERDE
        print(f"  vs {anterior['mes']}: {formatar(anterior['totalDespesas'])} "
              f"({pintar(f'{seta} {abs(variacao)}%', cor)})")

    def _bloco(titulo: str, grupos: Sequence[Dict[str, Any]], total: float) -> None:
        if not grupos:
            return
        print(pintar(f"\n  {titulo}", NEGRITO))
        for grupo in grupos:
            valor = grupo["resultado"] or 0
            fatia = (valor / total * 100) if total else 0
            barra = "█" * max(0, int(round(fatia / 4)))
            print(f"    {grupo['grupo']:<18} {formatar(valor):>13}  {fatia:5.1f}%  "
                  f"{pintar(barra, CINZA)}  ({grupo['quantidade']})")

    _bloco("Por categoria", resumo["porCategoria"], resumo["totalDespesas"])
    _bloco("Por forma de pagamento", resumo["porFormaPagamento"], resumo["totalDespesas"])
    _bloco("Por conta", resumo["porConta"], resumo["totalDespesas"])
    _bloco("Receitas", resumo["receitasPorCategoria"], resumo["totalReceitas"])

    futuras = [p for p in estatisticas.parcelas_futuras(lancamentos, mes) if p["totalParcelas"]]
    if futuras:
        print(pintar("\n  Parcelas já comprometidas nos próximos meses", NEGRITO))
        for parcela in futuras:
            print(f"    {parcela['mes']}  {formatar(parcela['totalParcelas']):>13}")
    print()
    return 0


def cmd_listar(args) -> int:
    with db.banco(args.banco) as conexao:
        lancamentos = db.listar(
            conexao,
            mes_inicio=validar_mes(args.mes) if args.mes else None,
            mes_fim=validar_mes(args.mes) if args.mes else None,
            tipo=args.tipo,
            categoria=args.categoria,
            forma_pagamento=args.forma,
            conta=args.conta,
            limite=args.limite,
        )
    if args.json:
        imprimir_json(lancamentos)
        return 0
    if not lancamentos:
        print("nenhum lançamento com esses filtros.")
        return 0
    for lanc in lancamentos:
        print(_linha_lancamento(lanc))
        if args.ids:
            print(pintar(f"    id {lanc['id']}", CINZA))
    print(pintar(f"\n{len(lancamentos)} lançamento(s)", CINZA))
    return 0


def cmd_calcular(args) -> int:
    filtro = {
        "operacao": args.operacao,
        "tipo": args.tipo,
        "categoria": args.categoria,
        "formaPagamento": args.forma,
        "conta": args.conta,
        "mesInicio": validar_mes(args.de) if args.de else None,
        "mesFim": validar_mes(args.ate) if args.ate else None,
        "descricaoContem": args.descricao,
        "agruparPor": args.agrupar,
    }
    with db.banco(args.banco) as conexao:
        lancamentos = db.listar(conexao)
    resultado = executar_calculo({k: v for k, v in filtro.items() if v}, lancamentos)
    if args.json:
        imprimir_json(resultado)
        return 0
    print(pintar(f"{resultado['operacao']} · {resultado['quantidade']} lançamento(s)", NEGRITO))
    if "resultado" in resultado:
        valor = resultado["resultado"]
        print(f"  {formatar(valor) if isinstance(valor, float) else valor}")
    for grupo in resultado.get("grupos", []):
        print(f"    {grupo['grupo']:<20} {formatar(grupo['resultado'] or 0):>13}  ({grupo['quantidade']})")
    for item in resultado.get("itens", []):
        print(f"    {item['data']}  {formatar(item['valor']):>13}  {item['categoria']:<14} {item['descricao']}")
    return 0


def _mostrar_resposta(resultado: Dict[str, Any], detalhes: bool) -> None:
    if resultado.get("avisoFallback"):
        print(pintar(f"[{resultado['avisoFallback']}]", AMARELO))
    print(f"\n{resultado['resposta']}\n")
    if not detalhes:
        return
    print(pintar("— cálculos executados —", CINZA))
    if resultado["modo"] == "toolcall":
        for chamada in resultado.get("chamadas", []):
            print(pintar(f"  calcular({json.dumps(chamada['argumentos'], ensure_ascii=False)})"
                         f" -> {json.dumps(chamada['resultado'].get('resultado'), ensure_ascii=False)}", CINZA))
    else:
        for item in resultado.get("resultados", []):
            resumo = item.get("resultado", item.get("itens"))
            print(pintar(f"  {item.get('rotulo')}: {json.dumps(resumo, ensure_ascii=False)[:160]}"
                         f"  (n={item.get('quantidade')})", CINZA))
    print()


def cmd_perguntar(args) -> int:
    pergunta = " ".join(args.pergunta).strip()
    if not pergunta:
        print(pintar("faltou a pergunta", VERMELHO))
        return 1
    with db.banco(args.banco) as conexao:
        if db.contar(conexao) == 0:
            print(pintar("o caderno está vazio — registre ou importe algo antes.", AMARELO))
            return 1
        resultado = consulta.responder(conexao, pergunta, modo=args.modo)
    if args.json:
        imprimir_json(resultado)
        return 0
    _mostrar_resposta(resultado, args.detalhes)
    return 0


def cmd_chat(args) -> int:
    historico: List[Dict[str, str]] = []
    print(pintar("Caderno financeiro — modo conversa", NEGRITO))
    print(pintar("perguntas em linguagem natural; :sair pra terminar, :resumo pro mês atual\n", CINZA))
    with db.banco(args.banco) as conexao:
        modo = args.modo
        while True:
            try:
                pergunta = input(pintar("você> ", NEGRITO)).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not pergunta:
                continue
            if pergunta in (":sair", ":q", "sair"):
                break
            if pergunta == ":resumo":
                cmd_resumo(argparse.Namespace(banco=args.banco, mes=None, json=False))
                continue
            try:
                resultado = consulta.responder(conexao, pergunta, modo=modo, historico=historico)
            except ia.ErroIA as erro:
                print(pintar(f"erro: {erro}", VERMELHO))
                continue
            _mostrar_resposta(resultado, args.detalhes)
            historico.append({"pergunta": pergunta, "resposta": resultado["resposta"]})
    return 0


def cmd_importar(args) -> int:
    with db.banco(args.banco) as conexao:
        relatorio = importador.importar(
            conexao,
            Path(args.arquivo),
            simular=args.simular,
            checar_duplicatas=not args.sem_dedup,
            tolerancia_dias=args.tolerancia,
        )
    if args.json:
        imprimir_json(relatorio)
        return 0

    print(pintar(f"\n{relatorio['arquivo']}", NEGRITO))
    print("  colunas detectadas:")
    for campo, coluna in relatorio["colunasDetectadas"].items():
        print(f"    {campo:<15} <- {coluna!r}")
    if relatorio["colunasIgnoradas"]:
        print(pintar(f"    (ignoradas: {', '.join(relatorio['colunasIgnoradas'])})", CINZA))
    print(f"  linhas lidas:  {relatorio['linhasLidas']}")
    print(f"  {'a importar' if relatorio['simulado'] else 'importados'}: "
          f"{pintar(str(relatorio['aImportar']), VERDE)}")
    print(f"  duplicados:    {len(relatorio['duplicados'])}")
    print(f"  com erro:      {len(relatorio['erros'])}")

    for duplicado in relatorio["duplicados"][:10]:
        lanc = duplicado["lancamento"]
        print(pintar(f"    linha {duplicado['linha']}: {lanc['data']} {formatar(lanc['valor'])} "
                     f"{lanc['descricao'][:30]} — já existe", CINZA))
    if len(relatorio["duplicados"]) > 10:
        print(pintar(f"    ... e mais {len(relatorio['duplicados']) - 10}", CINZA))
    for erro in relatorio["erros"][:10]:
        print(pintar(f"    linha {erro['linha']}: {erro['motivo']}", VERMELHO))
    if relatorio["simulado"]:
        print(pintar("\n  (simulação — nada foi gravado; rode sem --simular pra valer)", AMARELO))
    print()
    return 0


def cmd_exportar(args) -> int:
    with db.banco(args.banco) as conexao:
        lancamentos = db.listar(conexao)
    destino = args.saida or str(exportador.nome_padrao())
    total = exportador.escrever(lancamentos, destino)
    if destino != "-":
        print(pintar(f"{total} lançamento(s) exportado(s) para {destino}", VERDE))
    return 0


def cmd_remover(args) -> int:
    with db.banco(args.banco) as conexao:
        alvo = db.buscar(conexao, args.id)
        if not alvo:
            print(pintar(f"não achei lançamento com id {args.id}", VERMELHO))
            return 1
        if args.grupo and alvo.get("grupoParcelamento"):
            removidos = db.remover_grupo(conexao, alvo["grupoParcelamento"])
        else:
            removidos = db.remover(conexao, args.id)
    print(pintar(f"{removidos} lançamento(s) removido(s).", VERDE))
    return 0


def cmd_testar_toolcall(args) -> int:
    print(pintar("testando function calling nativo (servidor MCP local)...", CINZA))
    with db.banco(args.banco) as conexao:
        diagnostico = consulta.testar_toolcall(conexao)
    if args.json:
        imprimir_json(diagnostico)
        return 0 if diagnostico["ok"] else 1

    print(f"  ferramenta executada de verdade: "
          f"{pintar('sim', VERDE) if diagnostico['ferramentaExecutada'] else pintar('NÃO', VERMELHO)}")
    print(f"  chamadas registradas: {len(diagnostico.get('chamadas', []))}")
    for chamada in diagnostico.get("chamadas", []):
        print(pintar(f"    {json.dumps(chamada['argumentos'], ensure_ascii=False)}", CINZA))
    print(f"  resposta do modelo: {diagnostico.get('resposta','')[:200]}")
    print(f"  valor esperado: {formatar(diagnostico['esperado'])}")
    if diagnostico["ok"]:
        print(pintar("\n  OK — o modo toolcall vai ser usado por padrão.", VERDE))
        return 0
    print(pintar(f"\n  FALHOU: {diagnostico.get('motivo')}", VERMELHO))
    print(pintar("  o modo manual (3 passos) continua sendo o padrão — nada quebra.", AMARELO))
    return 1


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="caderno",
        description="Caderno financeiro pessoal — registro por texto, estatísticas locais "
                    "e perguntas com cálculo determinístico.",
    )
    parser.add_argument("--banco", help="caminho do arquivo SQLite (padrão: ~/.caderno-financeiro/caderno.db)")
    subcomandos = parser.add_subparsers(dest="comando", required=True)

    p = subcomandos.add_parser("init", help="cria/abre o banco")
    p.set_defaults(funcao=cmd_init)

    p = subcomandos.add_parser("auth", help="confere se dá pra chamar o Claude pela assinatura")
    p.add_argument("--json", action="store_true")
    p.set_defaults(funcao=cmd_auth)

    p = subcomandos.add_parser("registrar", help="registra gasto/entrada a partir de texto livre")
    p.add_argument("texto", nargs="*", help='ex: "45,90 no mercado ontem no pix"')
    p.add_argument("-d", "--data", help="força a data (AAAA-MM-DD)")
    p.add_argument("--audio", help="arquivo de áudio pra transcrever localmente")
    p.add_argument("-s", "--sim", action="store_true", help="não pede confirmação")
    p.add_argument("--simular", action="store_true", help="mostra o que faria, sem salvar")
    p.set_defaults(funcao=cmd_registrar)

    p = subcomandos.add_parser("resumo", help="estatísticas do mês (sem IA)")
    p.add_argument("-m", "--mes", help="AAAA-MM (padrão: mês atual)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(funcao=cmd_resumo)

    p = subcomandos.add_parser("listar", help="lista lançamentos (sem IA)")
    p.add_argument("-m", "--mes")
    p.add_argument("-t", "--tipo", choices=list(config.TIPOS))
    p.add_argument("-c", "--categoria")
    p.add_argument("-f", "--forma", choices=list(config.FORMAS_PAGAMENTO))
    p.add_argument("--conta")
    p.add_argument("-n", "--limite", type=int, default=50)
    p.add_argument("--ids", action="store_true", help="mostra o id de cada lançamento")
    p.add_argument("--json", action="store_true")
    p.set_defaults(funcao=cmd_listar)

    p = subcomandos.add_parser("calcular", help="cálculo determinístico direto (sem IA)")
    p.add_argument("operacao", choices=list(OPERACOES))
    p.add_argument("--de", help="mês inicial AAAA-MM")
    p.add_argument("--ate", help="mês final AAAA-MM")
    p.add_argument("-t", "--tipo", choices=list(config.TIPOS))
    p.add_argument("-c", "--categoria")
    p.add_argument("-f", "--forma", choices=list(config.FORMAS_PAGAMENTO))
    p.add_argument("--conta")
    p.add_argument("--descricao", help="filtra por trecho da descrição")
    p.add_argument("--agrupar", choices=["categoria", "formaPagamento", "conta", "tipo", "mes"])
    p.add_argument("--json", action="store_true")
    p.set_defaults(funcao=cmd_calcular)

    p = subcomandos.add_parser("perguntar", help="pergunta em linguagem natural")
    p.add_argument("pergunta", nargs="*")
    p.add_argument("--modo", choices=["auto", "manual", "toolcall"], default="auto")
    p.add_argument("--detalhes", action="store_true", help="mostra os cálculos por trás da resposta")
    p.add_argument("--json", action="store_true")
    p.set_defaults(funcao=cmd_perguntar)

    p = subcomandos.add_parser("chat", help="conversa mantendo contexto entre perguntas")
    p.add_argument("--modo", choices=["auto", "manual", "toolcall"], default="auto")
    p.add_argument("--detalhes", action="store_true")
    p.set_defaults(funcao=cmd_chat)

    p = subcomandos.add_parser("importar", help="importa planilha antiga (CSV/XLSX)")
    p.add_argument("arquivo")
    p.add_argument("--simular", action="store_true", help="só mostra o que entraria")
    p.add_argument("--sem-dedup", action="store_true", help="não checa duplicatas")
    p.add_argument("--tolerancia", type=int, default=1, help="dias de tolerância na duplicata (padrão 1)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(funcao=cmd_importar)

    p = subcomandos.add_parser("exportar", help="backup em CSV")
    p.add_argument("saida", nargs="?", help="arquivo de saída ('-' para stdout)")
    p.set_defaults(funcao=cmd_exportar)

    p = subcomandos.add_parser("remover", help="remove um lançamento pelo id")
    p.add_argument("id")
    p.add_argument("--grupo", action="store_true", help="remove todas as parcelas da compra")
    p.set_defaults(funcao=cmd_remover)

    p = subcomandos.add_parser("testar-toolcall", help="verifica se o function calling nativo funciona")
    p.add_argument("--json", action="store_true")
    p.set_defaults(funcao=cmd_testar_toolcall)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = construir_parser()
    args = parser.parse_args(argv)
    banco = getattr(args, "banco", None)
    args.banco = Path(banco) if banco else None
    try:
        return args.funcao(args)
    except (ia.ErroIA, importador.ErroImportacao, ValueError, RuntimeError) as erro:
        print(pintar(f"erro: {erro}", VERMELHO), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
