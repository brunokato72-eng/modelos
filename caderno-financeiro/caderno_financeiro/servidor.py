"""Servidor web local: API JSON + arquivos do PWA.

Pensado pra rodar na sua máquina, atrás de uma tailnet (Tailscale) — o celular
acessa pelo endereço da tailnet, nunca pela internet aberta. O servidor não
guarda a credencial da assinatura em lugar nenhum próprio: quem fala com o
Claude continua sendo o CLI `claude`, exatamente como no uso por linha de
comando (`ia.py`). O PIN aqui protege *o acesso ao servidor*, não a IA.

Todas as rotas de dado (tudo abaixo de /api, exceto /api/auth/login) exigem
`Authorization: Bearer <token>` de uma sessão válida.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, Response, g, jsonify, request, send_from_directory

from . import auth, config, consulta, db, estatisticas, exportador, ia, registro
from .calculadora import executar_calculo
from .datas import mes_atual, validar_mes

DIRETORIO_PWA = Path(__file__).resolve().parent.parent / "web"

_caminho_banco: Path | None = None  # setado por rodar(); None = usa config.caminho_banco()


def _conexao():
    if "conexao" not in g:
        g.conexao = db.conectar(_caminho_banco, criar_esquema=False)
    return g.conexao


def _erro(mensagem: str, codigo: int = 400):
    return jsonify({"erro": mensagem}), codigo


def _token_do_request() -> str:
    cabecalho = request.headers.get("Authorization", "")
    return cabecalho[7:] if cabecalho.startswith("Bearer ") else ""


def criar_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.teardown_appcontext
    def _fechar_conexao(excecao=None):
        conexao = g.pop("conexao", None)
        if conexao is not None:
            if excecao is None:
                conexao.commit()
            conexao.close()

    @app.before_request
    def _exigir_sessao():
        if request.path in ("/api/auth/login", "/api/auth/status") or not request.path.startswith("/api/"):
            return None
        if not auth.sessao_valida(_conexao(), _token_do_request()):
            return _erro("sessão inválida ou expirada — faça login de novo", 401)
        return None

    # -- PWA (arquivos estáticos) -----------------------------------------

    @app.get("/")
    def _raiz():
        return send_from_directory(DIRETORIO_PWA, "index.html")

    @app.get("/<path:caminho>")
    def _estatico(caminho: str):
        if caminho.startswith("api/"):
            return _erro("não encontrado", 404)
        return send_from_directory(DIRETORIO_PWA, caminho)

    # -- autenticação -------------------------------------------------------

    @app.post("/api/auth/login")
    def _login():
        conexao = _conexao()
        if not auth.pin_configurado(conexao):
            return _erro("nenhum PIN configurado — rode `caderno definir-pin` na máquina do servidor", 412)
        corpo = request.get_json(silent=True) or {}
        if not auth.conferir_pin(conexao, str(corpo.get("pin", ""))):
            return _erro("PIN incorreto", 401)
        return jsonify({"token": auth.criar_sessao(conexao)})

    @app.get("/api/auth/status")
    def _status():
        conexao = _conexao()
        return jsonify({
            "pinConfigurado": auth.pin_configurado(conexao),
            "sessaoValida": auth.sessao_valida(conexao, _token_do_request()),
        })

    @app.post("/api/auth/logout")
    def _logout():
        auth.revogar_sessao(_conexao(), _token_do_request())
        return jsonify({"ok": True})

    # -- dados: resumo / listar / calcular (sem IA) -------------------------

    @app.get("/api/resumo")
    def _resumo():
        mes = request.args.get("mes")
        mes = validar_mes(mes) if mes else mes_atual()
        lancamentos = db.listar(_conexao())
        return jsonify(estatisticas.resumo_mensal(lancamentos, mes))

    @app.get("/api/listar")
    def _listar():
        conexao = _conexao()
        mes = request.args.get("mes")
        lancamentos = db.listar(
            conexao,
            mes_inicio=validar_mes(mes) if mes else None,
            mes_fim=validar_mes(mes) if mes else None,
            tipo=request.args.get("tipo") or None,
            categoria=request.args.get("categoria") or None,
            forma_pagamento=request.args.get("forma") or None,
            conta=request.args.get("conta") or None,
            limite=int(request.args.get("limite", 100)),
        )
        return jsonify(lancamentos)

    @app.get("/api/calcular")
    def _calcular():
        filtro = {
            "operacao": request.args.get("operacao", "soma"),
            "tipo": request.args.get("tipo") or None,
            "categoria": request.args.get("categoria") or None,
            "formaPagamento": request.args.get("forma") or None,
            "conta": request.args.get("conta") or None,
            "mesInicio": request.args.get("mesInicio") or None,
            "mesFim": request.args.get("mesFim") or None,
            "descricaoContem": request.args.get("descricaoContem") or None,
            "agruparPor": request.args.get("agruparPor") or None,
        }
        try:
            resultado = executar_calculo({k: v for k, v in filtro.items() if v}, db.listar(_conexao()))
        except ValueError as erro:
            return _erro(str(erro))
        return jsonify(resultado)

    @app.get("/api/panorama")
    def _panorama():
        return jsonify({
            "categoriasDespesa": list(config.CATEGORIAS_DESPESA),
            "categoriasReceita": list(config.CATEGORIAS_RECEITA),
            "formasPagamento": list(config.FORMAS_PAGAMENTO),
            **estatisticas.visao_geral(db.listar(_conexao())),
        })

    @app.delete("/api/lancamentos/<id_lancamento>")
    def _remover(id_lancamento: str):
        conexao = _conexao()
        alvo = db.buscar(conexao, id_lancamento)
        if not alvo:
            return _erro("lançamento não encontrado", 404)
        remover_grupo = request.args.get("grupo") == "1" and alvo.get("grupoParcelamento")
        removidos = db.remover_grupo(conexao, alvo["grupoParcelamento"]) if remover_grupo else db.remover(conexao, id_lancamento)
        return jsonify({"removidos": removidos})

    # -- dados: exportar (sem IA) --------------------------------------------

    @app.get("/api/exportar")
    def _exportar():
        texto = exportador.para_texto(db.listar(_conexao()))
        resposta = Response(texto, mimetype="text/csv")
        resposta.headers["Content-Disposition"] = "attachment; filename=caderno-backup.csv"
        return resposta

    # -- IA: registrar / perguntar -------------------------------------------

    @app.post("/api/registrar")
    def _registrar():
        conexao = _conexao()
        corpo = request.get_json(silent=True) or {}
        texto = str(corpo.get("texto", "")).strip()
        if not texto:
            return _erro("faltou o texto do lançamento")
        try:
            preparado = registro.preparar(conexao, texto, data_forcada=corpo.get("data") or None)
        except ia.ErroIA as erro:
            return _erro(str(erro), 502)
        if not preparado["grupos"]:
            return jsonify({"salvo": False, "observacao": preparado.get("observacao", ""), "grupos": []})
        if corpo.get("confirmar", True):
            registro.salvar(conexao, preparado)
        return jsonify({
            "salvo": bool(corpo.get("confirmar", True)),
            "observacao": preparado.get("observacao", ""),
            "grupos": [
                {"extraido": grupo["extraido"], "linhas": grupo["linhas"]}
                for grupo in preparado["grupos"]
            ],
        })

    @app.post("/api/perguntar")
    def _perguntar():
        conexao = _conexao()
        corpo = request.get_json(silent=True) or {}
        pergunta = str(corpo.get("pergunta", "")).strip()
        if not pergunta:
            return _erro("faltou a pergunta")
        if db.contar(conexao) == 0:
            return _erro("o caderno está vazio — registre ou importe algo antes", 409)
        historico = corpo.get("historico") or []
        try:
            resultado = consulta.responder(
                conexao, pergunta, modo=corpo.get("modo", "auto"), historico=historico
            )
        except ia.ErroIA as erro:
            return _erro(str(erro), 502)
        return jsonify(resultado)

    return app


def rodar(host: str = "0.0.0.0", porta: int = 8420, debug: bool = False,
          banco: "Path | None" = None) -> None:
    global _caminho_banco
    _caminho_banco = Path(banco) if banco else None
    with db.banco(_caminho_banco) as conexao:  # garante que o schema existe antes de servir
        pin_ok = auth.pin_configurado(conexao)
    if not pin_ok:
        raise RuntimeError(
            "nenhum PIN configurado — rode `caderno definir-pin` antes de `caderno servir`"
        )
    app = criar_app()
    app.run(host=host, port=porta, debug=debug)
