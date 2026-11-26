#!/usr/bin/env bash
# Instala o scripts/religar.sh como serviço do systemd do Ubuntu (WSL),
# para rodar sozinho toda vez que o WSL iniciar. Pede a senha do sudo uma vez.
#
# Desinstalar:
#   sudo systemctl disable --now chat-local-religar && sudo rm /etc/systemd/system/chat-local-religar.service
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")" && pwd)/religar.sh"
USUARIO="$(id -un)"
UNIDADE=/etc/systemd/system/chat-local-religar.service

if ! id -nG "$USUARIO" | grep -qw docker; then
    echo "Aviso: $USUARIO não está no grupo docker; o serviço não conseguirá usar o Docker." >&2
fi

sudo tee "$UNIDADE" >/dev/null <<EOF
[Unit]
Description=Religa containers do chat-local que falharam ao iniciar no boot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${USUARIO}
Environment=HOME=${HOME}
ExecStart=/bin/bash ${SCRIPT}
# O script espera até 5 min pelo Docker Desktop; damos folga além disso.
TimeoutStartSec=420

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable chat-local-religar.service
echo "Instalado. Rode agora para testar:  sudo systemctl start chat-local-religar && journalctl -u chat-local-religar -n 20 --no-pager"
