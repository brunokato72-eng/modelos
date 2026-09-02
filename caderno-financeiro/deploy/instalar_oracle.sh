#!/usr/bin/env bash
# Setup do Caderno Financeiro numa VPS sempre ligada (testado pensando no
# Always Free da Oracle Cloud, mas serve pra qualquer VM Ubuntu/Debian ou
# Oracle Linux/RHEL com systemd).
#
# O que este script faz:
#   1. Detecta a distro e instala dependências de sistema (Python, build tools)
#   2. Instala Node.js via nvm (não depende do gerenciador de pacote da distro)
#   3. Instala o CLI `claude` (npm) — o login com a assinatura é manual, feito
#      por você logo depois, porque é um fluxo interativo (abre uma URL)
#   4. Cria um virtualenv e instala o caderno-financeiro nele
#   5. Instala e ativa o Tailscale
#   6. Gera e habilita um serviço systemd que sobe o `caderno servir`
#      automaticamente no boot e reinicia sozinho se cair
#
# O que este script NÃO faz (você faz na mão, uma vez, com instruções na tela):
#   - Login do `claude` com sua assinatura (`claude` -> `/login`)
#   - Definir o PIN de acesso (`caderno definir-pin`)
#   - `tailscale up` (autenticar a VM na sua tailnet)
#
# Uso:
#   git clone https://github.com/brunokato72-eng/modelos.git
#   cd modelos/caderno-financeiro/deploy
#   chmod +x instalar_oracle.sh
#   ./instalar_oracle.sh
#
# Rodar de novo depois é seguro (idempotente na maior parte) — útil pra
# atualizar após um `git pull`.

set -euo pipefail

RAIZ_PROJETO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$RAIZ_PROJETO/.venv"
PORTA_PADRAO=8420

verde() { printf '\033[32m%s\033[0m\n' "$1"; }
amarelo() { printf '\033[33m%s\033[0m\n' "$1"; }
vermelho() { printf '\033[31m%s\033[0m\n' "$1"; }

if [ "$(id -u)" -eq 0 ]; then
  vermelho "Não rode como root — rode como o seu usuário normal (o script usa sudo onde precisa)."
  exit 1
fi

echo "Projeto detectado em: $RAIZ_PROJETO"

# ---------------------------------------------------------------------------
# 1) distro + dependências de sistema
# ---------------------------------------------------------------------------

if [ -f /etc/os-release ]; then
  . /etc/os-release
  DISTRO="$ID"
else
  vermelho "não consegui detectar a distro (/etc/os-release ausente)."
  exit 1
fi

echo "Distro detectada: $DISTRO"

case "$DISTRO" in
  ubuntu|debian)
    sudo apt-get update -y
    sudo apt-get install -y python3 python3-venv python3-pip git curl build-essential
    ;;
  ol|rhel|centos|fedora|rocky|almalinux)
    if command -v dnf >/dev/null 2>&1; then GERENCIADOR=dnf; else GERENCIADOR=yum; fi
    sudo "$GERENCIADOR" install -y python3 python3-pip git curl gcc make policycoreutils-python-utils
    ;;
  *)
    amarelo "distro '$DISTRO' não reconhecida — segue tentando, mas instale manualmente"
    amarelo "python3, python3-venv, python3-pip, git e curl se algo falhar."
    ;;
esac

# ---------------------------------------------------------------------------
# 2) Node.js via nvm (funciona igual em qualquer distro)
# ---------------------------------------------------------------------------

export NVM_DIR="$HOME/.nvm"
if [ ! -s "$NVM_DIR/nvm.sh" ]; then
  echo "instalando nvm..."
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
fi
# shellcheck disable=SC1091
. "$NVM_DIR/nvm.sh"

if ! command -v node >/dev/null 2>&1 || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 20 ]; then
  nvm install --lts
fi
NODE_BIN_DIR="$(dirname "$(nvm which node)")"
verde "node: $(node -v)  (em $NODE_BIN_DIR)"

# ---------------------------------------------------------------------------
# 3) CLI claude
# ---------------------------------------------------------------------------

if ! command -v claude >/dev/null 2>&1; then
  echo "instalando o CLI claude..."
  npm install -g @anthropic-ai/claude-code
fi
verde "claude: $(claude --version 2>&1 | head -1)"

# ---------------------------------------------------------------------------
# 4) virtualenv + pacote
# ---------------------------------------------------------------------------

if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$RAIZ_PROJETO[web,xlsx]"
verde "pacote instalado no virtualenv ($VENV)"

# Em distros com SELinux (Oracle Linux, RHEL...), um executável dentro da
# pasta pessoal do usuário fica marcado como "user_home_t" — o systemd não
# tem permissão de rodar nada com esse rótulo. Sem isso, o serviço sobe e
# morre na hora com "203/EXEC", sem mensagem clara do motivo.
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = "Enforcing" ] && command -v semanage >/dev/null 2>&1; then
  echo "SELinux enforcing detectado — liberando execução em $VENV/bin ..."
  sudo semanage fcontext -a -t bin_t "$VENV/bin(/.*)?" 2>/dev/null || true
  sudo restorecon -Rv "$VENV/bin" > /dev/null
  verde "contexto SELinux ajustado"
fi

# ---------------------------------------------------------------------------
# 5) Tailscale
# ---------------------------------------------------------------------------

if ! command -v tailscale >/dev/null 2>&1; then
  echo "instalando o Tailscale..."
  curl -fsSL https://tailscale.com/install.sh | sh
fi
verde "tailscale instalado — falta autenticar (instrução no fim do script)"

# ---------------------------------------------------------------------------
# 6) serviço systemd
# ---------------------------------------------------------------------------

UNIDADE="/etc/systemd/system/caderno-financeiro.service"
echo "criando serviço systemd em $UNIDADE ..."

sudo tee "$UNIDADE" > /dev/null <<EOF
[Unit]
Description=Caderno Financeiro (servidor web)
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$RAIZ_PROJETO
Environment=PATH=$NODE_BIN_DIR:$VENV/bin:/usr/local/bin:/usr/bin:/bin
Environment=CADERNO_HOME=$HOME/.caderno-financeiro
ExecStart=$VENV/bin/caderno servir --host 0.0.0.0 --porta $PORTA_PADRAO
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
verde "serviço criado (ainda não ativado — falta o PIN, ver abaixo)."

# ---------------------------------------------------------------------------
# 7) auto-atualização (confere o git a cada 5 min e reinicia sozinho)
# ---------------------------------------------------------------------------

UNIDADE_ATUALIZAR="/etc/systemd/system/caderno-auto-atualizar.service"
TIMER_ATUALIZAR="/etc/systemd/system/caderno-auto-atualizar.timer"
echo "criando auto-atualização (timer systemd, a cada 5 min) ..."

sudo tee "$UNIDADE_ATUALIZAR" > /dev/null <<EOF
[Unit]
Description=Verifica e aplica atualizações do Caderno Financeiro

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$RAIZ_PROJETO
Environment=PATH=$NODE_BIN_DIR:$VENV/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$RAIZ_PROJETO/deploy/auto-atualizar.sh
EOF

sudo tee "$TIMER_ATUALIZAR" > /dev/null <<EOF
[Unit]
Description=Roda a checagem de atualização do Caderno Financeiro a cada 5 min

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
EOF

chmod +x "$RAIZ_PROJETO/deploy/auto-atualizar.sh"
sudo systemctl daemon-reload
sudo systemctl enable --now caderno-auto-atualizar.timer
verde "auto-atualização ativada — a VPS confere o GitHub sozinha a cada 5 min."

# ---------------------------------------------------------------------------
# passos manuais que faltam (só na primeira instalação)
# ---------------------------------------------------------------------------

JA_CONFIGURADO=false
if "$VENV/bin/python3" - <<PYEOF 2>/dev/null
import sys
sys.path.insert(0, "$RAIZ_PROJETO")
from caderno_financeiro import auth, db
with db.banco() as conexao:
    sys.exit(0 if auth.pin_configurado(conexao) else 1)
PYEOF
then
  JA_CONFIGURADO=true
fi

echo
if [ "$JA_CONFIGURADO" = true ]; then
  verde "=== atualização concluída — já estava tudo configurado, nada mais a fazer ==="
  echo "auto-atualização ativa: a VPS confere o GitHub sozinha a cada 5 min a partir de agora."
  if systemctl is-active --quiet caderno-financeiro; then
    sudo systemctl restart caderno-financeiro
    echo "serviço reiniciado pra pegar qualquer mudança de código mais recente."
  fi
else
  verde "=== instalação de dependências concluída ==="
  echo
  amarelo "faltam 3 passos manuais, nessa ordem:"
  echo
  echo "1) Login do claude com sua assinatura (é interativo, abre uma URL):"
  echo "     claude"
  echo "     (dentro da sessão, rode /login e siga a URL que aparecer)"
  echo
  echo "2) Definir o PIN de acesso ao servidor:"
  echo "     $VENV/bin/caderno definir-pin"
  echo
  echo "3) Autenticar o Tailscale nesta VM (mesma conta do seu celular):"
  echo "     sudo tailscale up"
  echo "     (vai imprimir uma URL — abra em qualquer navegador logado na sua conta Tailscale)"
  echo
  echo "Depois dos 3 passos, ative o serviço:"
  echo "     sudo systemctl enable --now caderno-financeiro"
  echo "     sudo systemctl status caderno-financeiro"
  echo
  echo "E confira o endereço da tailnet desta VM:"
  echo "     tailscale ip -4"
  echo
  echo "No celular (com Tailscale ativo), acesse http://<esse-ip-ou-nome>:$PORTA_PADRAO"
fi
