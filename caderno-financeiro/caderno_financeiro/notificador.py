"""Notificação diária independente de sessão do Claude Code.

O `PushNotification` do harness só existe dentro de uma conversa ativa — se a
rotina na nuvem dispara mas ninguém está "ouvindo" (remote-control
desconectado, sessão fechada), a mensagem fica na fila sem sair (foi
exatamente o que aconteceu entre 15/10 e 25/10: a rotina rodou todo dia com
sucesso, mas a entrega até a sessão não aconteceu). Pra não depender disso,
esse módulo roda via `systemd timer` direto na VPS e manda a notificação por
um canal simples (ntfy.sh) que não passa pelo Claude Code nenhuma vez — é só
um POST HTTP.

A mensagem é montada de forma determinística a partir do JSON de
`estatisticas.score_dia` (mesma regra de prioridade que já usávamos ao
escrever a notificação manualmente): sinal comportamental primeiro, depois
categoria estourada nova, depois aderência à meta, por último o score puro.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any, Dict

from .valores import formatar


def montar_mensagem(resultado: Dict[str, Any]) -> str:
    partes = []

    if resultado.get("alertasComportamento"):
        partes.append(resultado["alertasComportamento"][0])

    novas = [c for c in resultado.get("categoriasEmRiscoDeEstourar", []) if c.get("notificarAgora")]
    if novas:
        c = novas[0]
        partes.append(f"{c['categoria']} projetada em {formatar(c['projecao'])} (limite {formatar(c['limite'])})")

    if not partes:
        aderencia = (resultado.get("poupanca") or {}).get("aderenciaPercentual")
        if aderencia is not None:
            partes.append(f"aderência à meta de poupança: {aderencia}%")

    if not partes:
        partes.append("sem sinal específico")

    return f"{' · '.join(partes)} (score {resultado['pontuacaoFinal']}/100, {resultado['classificacao']})"


def deve_notificar(resultado: Dict[str, Any]) -> bool:
    if resultado["pontuacaoFinal"] < 70:
        return True
    return any(c.get("notificarAgora") for c in resultado.get("categoriasEmRiscoDeEstourar", []))


class ErroNotificacao(Exception):
    pass


def enviar_ntfy(mensagem: str, topico: str, *, titulo: str = "Caderno Financeiro",
                prioridade: str = "default", timeout: int = 15) -> None:
    if not topico:
        raise ErroNotificacao("nenhum tópico ntfy configurado (CADERNO_NTFY_TOPICO)")
    requisicao = urllib.request.Request(
        f"https://ntfy.sh/{topico}",
        data=mensagem.encode("utf-8"),
        headers={
            "Title": titulo,
            "Priority": prioridade,
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
            if resposta.status >= 300:
                raise ErroNotificacao(f"ntfy.sh respondeu {resposta.status}")
    except urllib.error.URLError as erro:
        raise ErroNotificacao(f"falha ao enviar pro ntfy.sh: {erro}") from erro
