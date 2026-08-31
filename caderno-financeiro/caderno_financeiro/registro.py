"""Registro de lançamento a partir de texto livre (ou voz).

Fluxo, nessa ordem e sem atalho:
  1. IA extrai o que a mensagem diz (e só isso) -> JSON.
  2. Código aplica as regras determinísticas (forma/conta padrão, Ifood -> VR).
  3. Código expande o parcelamento (uma linha por mês, última parcela ajustada).
  4. Salva.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db, ia, regras
from .datas import hoje_iso, validar_iso


def preparar(
    conexao,
    texto: str,
    *,
    hoje: Optional[str] = None,
    data_forcada: Optional[str] = None,
) -> Dict[str, Any]:
    """Passos 1-3: devolve o que *seria* salvo, sem salvar (permite confirmar antes)."""
    hoje = hoje or hoje_iso()
    contas = db.valores_distintos(conexao, "conta")
    extraido = ia.extrair_lancamentos(texto, hoje, contas)
    criado_em = db.agora()

    grupos: List[Dict[str, Any]] = []
    for bruto in extraido.get("lancamentos", []):
        data = validar_iso(data_forcada or bruto.get("data") or hoje)
        linhas = regras.montar_lancamentos(
            data=data,
            tipo=bruto.get("tipo"),
            categoria=bruto.get("categoria"),
            valor_total=bruto.get("valorTotal") or 0,
            total_parcelas=bruto.get("totalParcelas") or 1,
            forma_pagamento=bruto.get("formaPagamento"),
            conta=bruto.get("conta"),
            descricao=bruto.get("descricao") or "",
            criado_em=criado_em,
        )
        grupos.append({"extraido": bruto, "linhas": linhas})

    return {
        "texto": texto,
        "observacao": extraido.get("observacao", ""),
        "grupos": grupos,
        "totalLinhas": sum(len(g["linhas"]) for g in grupos),
    }


def salvar(conexao, preparado: Dict[str, Any]) -> int:
    linhas = [linha for grupo in preparado["grupos"] for linha in grupo["linhas"]]
    if not linhas:
        return 0
    return db.inserir(conexao, linhas)


def transcrever(caminho_audio: Path) -> str:
    """Voz -> texto, rodando local (nenhum áudio sai da máquina, custo zero).

    Usa faster-whisper ou openai-whisper se estiverem instalados. Nenhum dos dois
    é dependência do projeto: sem eles, o registro por voz fica indisponível e o
    de texto continua funcionando normalmente.
    """
    caminho_audio = Path(caminho_audio)
    if not caminho_audio.exists():
        raise FileNotFoundError(f"áudio não encontrado: {caminho_audio}")

    try:
        from faster_whisper import WhisperModel  # type: ignore

        modelo = WhisperModel("small", device="cpu", compute_type="int8")
        segmentos, _ = modelo.transcribe(str(caminho_audio), language="pt")
        return " ".join(s.text.strip() for s in segmentos).strip()
    except ImportError:
        pass

    try:
        import whisper  # type: ignore

        modelo = whisper.load_model("small")
        return str(modelo.transcribe(str(caminho_audio), language="pt")["text"]).strip()
    except ImportError as erro:
        raise RuntimeError(
            "registro por voz precisa de um transcritor local.\n"
            "Instale `pip install faster-whisper` (recomendado) ou "
            "`pip install openai-whisper` e tente de novo.\n"
            "Nada é enviado pra fora: a transcrição roda na sua máquina."
        ) from erro
