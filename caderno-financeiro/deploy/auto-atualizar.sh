#!/usr/bin/env bash
# Verifica se há atualização no branch em uso e, se houver, baixa e reinicia
# o serviço sozinho. Pensado pra rodar via timer systemd
# (caderno-auto-atualizar.timer), não executado à mão — embora rodar à mão
# também funcione (idempotente: sem novidade, só sai sem fazer nada).

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git fetch origin "$BRANCH" --quiet

LOCAL="$(git rev-parse HEAD)"
REMOTO="$(git rev-parse "origin/$BRANCH")"

if [ "$LOCAL" = "$REMOTO" ]; then
  exit 0  # nada novo
fi

echo "$(date -Iseconds) atualizando de ${LOCAL:0:7} para ${REMOTO:0:7}"
git pull --ff-only origin "$BRANCH"

VENV="$RAIZ/.venv"
"$VENV/bin/pip" install --quiet -e "$RAIZ[web,xlsx]"

sudo systemctl restart caderno-financeiro
echo "$(date -Iseconds) atualizado e reiniciado"
