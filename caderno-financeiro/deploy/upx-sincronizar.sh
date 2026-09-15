#!/usr/bin/env bash
# Roda a sincronização diária via UPX Financial e loga o resultado. Pensado
# pra rodar via timer systemd (caderno-upx-sincronizar.timer), 1x por dia —
# mas rodar à mão também funciona.
#
# Diferente do Pluggy, não tem credencial nenhuma pra configurar aqui: a
# autorização foi feita 1x em claude.ai (Configurações > Conectores >
# UPX Financial) e fica presa à conta da assinatura — qualquer `claude -p`
# rodado por esse usuário já enxerga as contas conectadas.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

VENV="$RAIZ/.venv"
echo "$(date -Iseconds) iniciando sincronização UPX Financial"
"$VENV/bin/caderno" upx-sincronizar --investimentos --json
echo "$(date -Iseconds) sincronização concluída"
