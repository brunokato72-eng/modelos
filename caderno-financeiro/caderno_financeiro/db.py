"""Persistência em SQLite.

Um arquivo só (~/.caderno-financeiro/caderno.db por padrão), sem servidor e sem
dependência externa. As colunas seguem o modelo de dados do briefing; a coluna
extra `grupo_parcelamento` existe só pra dar pra remover uma compra parcelada
inteira de uma vez.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from . import config
from .datas import dias_entre
from .valores import para_centavos

ESQUEMA = """
CREATE TABLE IF NOT EXISTS lancamentos (
    id                 TEXT PRIMARY KEY,
    data               TEXT NOT NULL,
    tipo               TEXT NOT NULL,
    categoria          TEXT NOT NULL,
    valor              REAL NOT NULL,
    valor_total        REAL NOT NULL,
    parcela_atual      INTEGER NOT NULL DEFAULT 1,
    total_parcelas     INTEGER NOT NULL DEFAULT 1,
    forma_pagamento    TEXT NOT NULL DEFAULT '',
    conta              TEXT NOT NULL DEFAULT '',
    descricao          TEXT NOT NULL DEFAULT '',
    criado_em          TEXT NOT NULL,
    grupo_parcelamento TEXT
);
CREATE INDEX IF NOT EXISTS idx_lanc_data ON lancamentos(data);
CREATE INDEX IF NOT EXISTS idx_lanc_tipo ON lancamentos(tipo);
CREATE INDEX IF NOT EXISTS idx_lanc_categoria ON lancamentos(categoria);
CREATE INDEX IF NOT EXISTS idx_lanc_grupo ON lancamentos(grupo_parcelamento);

CREATE TABLE IF NOT EXISTS configuracao (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""

COLUNAS = (
    "id",
    "data",
    "tipo",
    "categoria",
    "valor",
    "valor_total",
    "parcela_atual",
    "total_parcelas",
    "forma_pagamento",
    "conta",
    "descricao",
    "criado_em",
    "grupo_parcelamento",
)

# ligação entre o dicionário em camelCase (usado no código/IA) e as colunas
_DE_DICT = {
    "id": "id",
    "data": "data",
    "tipo": "tipo",
    "categoria": "categoria",
    "valor": "valor",
    "valorTotal": "valor_total",
    "parcelaAtual": "parcela_atual",
    "totalParcelas": "total_parcelas",
    "formaPagamento": "forma_pagamento",
    "conta": "conta",
    "descricao": "descricao",
    "criadoEm": "criado_em",
    "grupoParcelamento": "grupo_parcelamento",
}
_PARA_DICT = {v: k for k, v in _DE_DICT.items()}


def linha_para_dict(linha: sqlite3.Row) -> Dict[str, Any]:
    return {_PARA_DICT[c]: linha[c] for c in linha.keys()}


def conectar(caminho: Optional[Path] = None, *, criar_esquema: bool = True) -> sqlite3.Connection:
    """Abre uma conexão. `criar_esquema=False` pula o DDL (CREATE TABLE IF NOT
    EXISTS) — usado pelo servidor web, que já garante o schema uma vez no start
    e abre uma conexão por request; repetir DDL a cada request é desnecessário
    e, sob concorrência, é o que causava "database is locked" nas rotas de
    leitura enquanto uma escrita estava em andamento."""
    destino = Path(caminho) if caminho else config.caminho_banco()
    destino.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(str(destino), timeout=10)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA journal_mode=WAL")
    conexao.execute("PRAGMA foreign_keys=ON")
    conexao.execute("PRAGMA busy_timeout=10000")
    if criar_esquema:
        conexao.executescript(ESQUEMA)
    return conexao


@contextmanager
def banco(caminho: Optional[Path] = None, *, criar_esquema: bool = True):
    conexao = conectar(caminho, criar_esquema=criar_esquema)
    try:
        yield conexao
        conexao.commit()
    finally:
        conexao.close()


def agora() -> str:
    """Timestamp de bookkeeping (`criadoEm`) no fuso do usuário, mesma razão
    de `datas.hoje_iso()` — não usa o fuso do processo/servidor."""
    return datetime.now(ZoneInfo(config.FUSO_HORARIO)).isoformat(timespec="seconds")


def inserir(conexao: sqlite3.Connection, lancamentos: Iterable[Dict[str, Any]]) -> int:
    linhas = []
    for lanc in lancamentos:
        linhas.append(tuple(lanc.get(_PARA_DICT[coluna]) for coluna in COLUNAS))
    conexao.executemany(
        f"INSERT INTO lancamentos ({', '.join(COLUNAS)}) "
        f"VALUES ({', '.join('?' * len(COLUNAS))})",
        linhas,
    )
    return len(linhas)


def listar(
    conexao: sqlite3.Connection,
    *,
    mes_inicio: Optional[str] = None,
    mes_fim: Optional[str] = None,
    tipo: Optional[str] = None,
    categoria: Optional[str] = None,
    forma_pagamento: Optional[str] = None,
    conta: Optional[str] = None,
    limite: Optional[int] = None,
) -> List[Dict[str, Any]]:
    clausulas, parametros = [], []
    if mes_inicio:
        clausulas.append("substr(data, 1, 7) >= ?")
        parametros.append(mes_inicio)
    if mes_fim:
        clausulas.append("substr(data, 1, 7) <= ?")
        parametros.append(mes_fim)
    if tipo:
        clausulas.append("tipo = ?")
        parametros.append(tipo)
    if categoria:
        clausulas.append("categoria = ?")
        parametros.append(categoria)
    if forma_pagamento:
        clausulas.append("forma_pagamento = ?")
        parametros.append(forma_pagamento)
    if conta:
        clausulas.append("lower(conta) = lower(?)")
        parametros.append(conta)

    sql = f"SELECT {', '.join(COLUNAS)} FROM lancamentos"
    if clausulas:
        sql += " WHERE " + " AND ".join(clausulas)
    sql += " ORDER BY data DESC, criado_em DESC"
    if limite:
        sql += f" LIMIT {int(limite)}"
    return [linha_para_dict(l) for l in conexao.execute(sql, parametros)]


def buscar(conexao: sqlite3.Connection, id_lancamento: str) -> Optional[Dict[str, Any]]:
    linha = conexao.execute(
        f"SELECT {', '.join(COLUNAS)} FROM lancamentos WHERE id = ?", (id_lancamento,)
    ).fetchone()
    return linha_para_dict(linha) if linha else None


def remover(conexao: sqlite3.Connection, id_lancamento: str) -> int:
    cursor = conexao.execute("DELETE FROM lancamentos WHERE id = ?", (id_lancamento,))
    return cursor.rowcount


def remover_grupo(conexao: sqlite3.Connection, grupo: str) -> int:
    cursor = conexao.execute("DELETE FROM lancamentos WHERE grupo_parcelamento = ?", (grupo,))
    return cursor.rowcount


def contar(conexao: sqlite3.Connection) -> int:
    return conexao.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0]


def periodo_registrado(conexao: sqlite3.Connection):
    linha = conexao.execute("SELECT MIN(data), MAX(data) FROM lancamentos").fetchone()
    return (linha[0], linha[1])


def valores_distintos(conexao: sqlite3.Connection, coluna: str) -> List[str]:
    if coluna not in {"categoria", "forma_pagamento", "conta", "tipo"}:
        raise ValueError(f"coluna não permitida: {coluna}")
    return [
        l[0]
        for l in conexao.execute(
            f"SELECT DISTINCT {coluna} FROM lancamentos WHERE {coluna} <> '' ORDER BY 1"
        )
    ]


def existe_duplicata(
    conexao: sqlite3.Connection, data: str, valor: float, *, tolerancia_dias: int = 1
) -> Optional[Dict[str, Any]]:
    """Duplicata = mesmo valor + data a até `tolerancia_dias` de distância.

    Regra do briefing para o import de histórico (mesma data ±1 dia + mesmo valor).
    Comparação de valor em centavos pra não depender de float.
    """
    centavos = para_centavos(valor)
    candidatos = conexao.execute(
        "SELECT " + ", ".join(COLUNAS) + " FROM lancamentos "
        "WHERE date(data) BETWEEN date(?, ?) AND date(?, ?)",
        (data, f"-{tolerancia_dias} day", data, f"+{tolerancia_dias} day"),
    )
    for linha in candidatos:
        if para_centavos(linha["valor"]) == centavos and dias_entre(linha["data"], data) <= tolerancia_dias:
            return linha_para_dict(linha)
    return None


def definir_config(conexao: sqlite3.Connection, chave: str, valor: str) -> None:
    conexao.execute(
        "INSERT INTO configuracao (chave, valor) VALUES (?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (chave, str(valor)),
    )


def ler_config(conexao: sqlite3.Connection, chave: str, padrao=None):
    linha = conexao.execute("SELECT valor FROM configuracao WHERE chave = ?", (chave,)).fetchone()
    return linha[0] if linha else padrao
