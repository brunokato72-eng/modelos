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

# Só as tabelas — os índices vêm depois de rodar a migração aditiva (ver
# `_migrar_esquema`), porque um banco antigo pode não ter ainda as colunas
# novas (origem/origem_id/revisao_pendente) que alguns índices referenciam:
# criar o índice antes da migração dá "no such column" num banco existente.
ESQUEMA_TABELAS = """
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
    grupo_parcelamento TEXT,
    origem             TEXT NOT NULL DEFAULT 'manual',
    origem_id          TEXT,
    revisao_pendente   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS configuracao (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pluggy_conexoes (
    item_id             TEXT PRIMARY KEY,
    instituicao         TEXT NOT NULL DEFAULT '',
    ultima_sincronizacao TEXT,
    status              TEXT NOT NULL DEFAULT 'ativa',
    criado_em           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS investimentos_posicoes (
    id           TEXT PRIMARY KEY,
    data         TEXT NOT NULL,
    item_id      TEXT NOT NULL,
    conta        TEXT NOT NULL DEFAULT '',
    tipo         TEXT NOT NULL DEFAULT '',
    nome         TEXT NOT NULL DEFAULT '',
    valor        REAL NOT NULL,
    quantidade   REAL,
    criado_em    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orcamentos (
    categoria     TEXT PRIMARY KEY,
    limite        REAL NOT NULL,
    criado_em     TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
"""

ESQUEMA_INDICES = """
CREATE INDEX IF NOT EXISTS idx_lanc_data ON lancamentos(data);
CREATE INDEX IF NOT EXISTS idx_lanc_tipo ON lancamentos(tipo);
CREATE INDEX IF NOT EXISTS idx_lanc_categoria ON lancamentos(categoria);
CREATE INDEX IF NOT EXISTS idx_lanc_grupo ON lancamentos(grupo_parcelamento);
CREATE INDEX IF NOT EXISTS idx_lanc_revisao ON lancamentos(revisao_pendente);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lanc_origem_id ON lancamentos(origem, origem_id)
    WHERE origem_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_invest_data ON investimentos_posicoes(data);
CREATE INDEX IF NOT EXISTS idx_invest_item ON investimentos_posicoes(item_id);
"""

# mantido pelo nome antigo pra quem importar `ESQUEMA` de fora (nenhum uso
# interno depende mais dele — `conectar()` roda tabelas, migração e índices
# nessa ordem, separadamente).
ESQUEMA = ESQUEMA_TABELAS + ESQUEMA_INDICES

# Migração aditiva pra bancos criados antes dessas colunas existirem — SQLite
# não tem "ADD COLUMN IF NOT EXISTS", então checamos via PRAGMA antes de somar.
_MIGRACOES_LANCAMENTOS = (
    ("origem", "TEXT NOT NULL DEFAULT 'manual'"),
    ("origem_id", "TEXT"),
    ("revisao_pendente", "INTEGER NOT NULL DEFAULT 0"),
)


def _migrar_esquema(conexao: sqlite3.Connection) -> None:
    colunas_existentes = {l["name"] for l in conexao.execute("PRAGMA table_info(lancamentos)")}
    for nome, definicao in _MIGRACOES_LANCAMENTOS:
        if nome not in colunas_existentes:
            conexao.execute(f"ALTER TABLE lancamentos ADD COLUMN {nome} {definicao}")


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
    "origem",
    "origem_id",
    "revisao_pendente",
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
    "origem": "origem",
    "origemId": "origem_id",
    "revisaoPendente": "revisao_pendente",
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
        conexao.executescript(ESQUEMA_TABELAS)
        _migrar_esquema(conexao)
        conexao.executescript(ESQUEMA_INDICES)
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


_PADROES_LANCAMENTO = {"origem": "manual", "revisaoPendente": 0}


def inserir(conexao: sqlite3.Connection, lancamentos: Iterable[Dict[str, Any]]) -> int:
    linhas = []
    for lanc in lancamentos:
        linhas.append(
            tuple(
                lanc.get(_PARA_DICT[coluna], _PADROES_LANCAMENTO.get(_PARA_DICT[coluna]))
                for coluna in COLUNAS
            )
        )
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


# --------------------------------------------------------------------------
# fila de revisão (categorização incerta na sincronização automática)
# --------------------------------------------------------------------------

def listar_pendentes_revisao(conexao: sqlite3.Connection) -> List[Dict[str, Any]]:
    linhas = conexao.execute(
        f"SELECT {', '.join(COLUNAS)} FROM lancamentos "
        "WHERE revisao_pendente = 1 ORDER BY data DESC"
    )
    return [linha_para_dict(l) for l in linhas]


def resolver_revisao(conexao: sqlite3.Connection, id_lancamento: str, categoria: str) -> bool:
    """Confirma a categoria de um lançamento pendente e tira ele da fila."""
    cursor = conexao.execute(
        "UPDATE lancamentos SET categoria = ?, revisao_pendente = 0 WHERE id = ?",
        (categoria, id_lancamento),
    )
    return cursor.rowcount > 0


def contar_pendentes_revisao(conexao: sqlite3.Connection) -> int:
    return conexao.execute(
        "SELECT COUNT(*) FROM lancamentos WHERE revisao_pendente = 1"
    ).fetchone()[0]


def origem_ja_importada(conexao: sqlite3.Connection, origem: str, origem_id: str) -> bool:
    """Dedup real pra fontes externas (Pluggy): por id da transação, não por
    valor+data — evita reimportar a cada sincronização diária."""
    linha = conexao.execute(
        "SELECT 1 FROM lancamentos WHERE origem = ? AND origem_id = ?", (origem, origem_id)
    ).fetchone()
    return linha is not None


# --------------------------------------------------------------------------
# conexões Pluggy (controle de sincronização)
# --------------------------------------------------------------------------

def registrar_conexao_pluggy(conexao: sqlite3.Connection, item_id: str, instituicao: str) -> None:
    conexao.execute(
        "INSERT INTO pluggy_conexoes (item_id, instituicao, status, criado_em) "
        "VALUES (?, ?, 'ativa', ?) "
        "ON CONFLICT(item_id) DO UPDATE SET instituicao = excluded.instituicao",
        (item_id, instituicao, agora()),
    )


def atualizar_sincronizacao_pluggy(conexao: sqlite3.Connection, item_id: str) -> None:
    conexao.execute(
        "UPDATE pluggy_conexoes SET ultima_sincronizacao = ? WHERE item_id = ?",
        (agora(), item_id),
    )


def listar_conexoes_pluggy(conexao: sqlite3.Connection) -> List[Dict[str, Any]]:
    linhas = conexao.execute(
        "SELECT item_id, instituicao, ultima_sincronizacao, status, criado_em "
        "FROM pluggy_conexoes ORDER BY criado_em"
    )
    return [dict(l) for l in linhas]


# --------------------------------------------------------------------------
# investimentos (posições — modelo diferente de lançamento: é saldo, não gasto)
# --------------------------------------------------------------------------

def inserir_posicoes_investimento(
    conexao: sqlite3.Connection, posicoes: Iterable[Dict[str, Any]]
) -> int:
    linhas = [
        (
            p["id"], p["data"], p["itemId"], p.get("conta", ""), p.get("tipo", ""),
            p.get("nome", ""), p["valor"], p.get("quantidade"), p.get("criadoEm") or agora(),
        )
        for p in posicoes
    ]
    conexao.executemany(
        "INSERT OR REPLACE INTO investimentos_posicoes "
        "(id, data, item_id, conta, tipo, nome, valor, quantidade, criado_em) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        linhas,
    )
    return len(linhas)


def listar_posicoes_investimento(
    conexao: sqlite3.Connection, data: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Sem `data`, devolve a posição mais recente de cada ativo (último snapshot)."""
    if data:
        linhas = conexao.execute(
            "SELECT * FROM investimentos_posicoes WHERE data = ? ORDER BY conta, nome", (data,)
        )
        return [dict(l) for l in linhas]

    linhas = conexao.execute(
        """
        SELECT ip.* FROM investimentos_posicoes ip
        INNER JOIN (
            SELECT item_id, nome, MAX(data) AS data_max
            FROM investimentos_posicoes GROUP BY item_id, nome
        ) atual ON ip.item_id = atual.item_id AND ip.nome = atual.nome AND ip.data = atual.data_max
        ORDER BY ip.conta, ip.nome
        """
    )
    return [dict(l) for l in linhas]


# --------------------------------------------------------------------------
# orçamentos (limite mensal por categoria — vale todo mês até ser redefinido)
# --------------------------------------------------------------------------

def definir_orcamento(conexao: sqlite3.Connection, categoria: str, limite: float) -> None:
    if categoria not in config.CATEGORIAS_DESPESA:
        raise ValueError(f"categoria inválida pra orçamento: {categoria!r}")
    if limite <= 0:
        raise ValueError("o limite do orçamento precisa ser maior que zero")
    agora_str = agora()
    conexao.execute(
        "INSERT INTO orcamentos (categoria, limite, criado_em, atualizado_em) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(categoria) DO UPDATE SET limite = excluded.limite, atualizado_em = excluded.atualizado_em",
        (categoria, limite, agora_str, agora_str),
    )


def remover_orcamento(conexao: sqlite3.Connection, categoria: str) -> bool:
    cursor = conexao.execute("DELETE FROM orcamentos WHERE categoria = ?", (categoria,))
    return cursor.rowcount > 0


def listar_orcamentos(conexao: sqlite3.Connection) -> List[Dict[str, Any]]:
    linhas = conexao.execute(
        "SELECT categoria, limite, criado_em, atualizado_em FROM orcamentos ORDER BY categoria"
    )
    return [dict(l) for l in linhas]
