#!/usr/bin/env bash
# Calcula o score do dia e manda notificação via ntfy.sh se houver sinal —
# independente de sessão do Claude Code estar aberta/conectada. Pensado pra
# rodar via timer systemd (caderno-notificar.timer), depois do timer de
# sincronização (caderno-upx-sincronizar.timer).
#
# Existe porque a rotina na nuvem (RemoteTrigger) só enfileira a mensagem na
# sessão persistente do Claude — se ninguém estiver "ouvindo" (remote-control
# desconectado, sessão fechada), ela não sai. Esse script não depende disso:
# é só `caderno score` + um POST HTTP.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

VENV="$RAIZ/.venv"
echo "$(date -Iseconds) checando score do dia"
"$VENV/bin/caderno" notificar
echo "$(date -Iseconds) notificação verificada"
