#!/usr/bin/env bash
# Roda a sincronização diária via Meu Pluggy (Open Finance) e loga o
# resultado. Pensado pra rodar via timer systemd
# (caderno-pluggy-sincronizar.timer), 1x por dia — mas rodar à mão também
# funciona. Se as credenciais (PLUGGY_CLIENT_ID/SECRET) ainda não foram
# configuradas, sai sem erro: nem todo mundo usa essa parte do app.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

if [ -z "${PLUGGY_CLIENT_ID:-}" ]; then
  exit 0  # Pluggy ainda não configurado — nada a fazer
fi

VENV="$RAIZ/.venv"
echo "$(date -Iseconds) iniciando sincronização Pluggy"
"$VENV/bin/caderno" pluggy-sincronizar --investimentos --json
echo "$(date -Iseconds) sincronização concluída"
