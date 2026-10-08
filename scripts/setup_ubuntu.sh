#!/usr/bin/env bash
# Prepara o Ubuntu (WSL2) para o Chat LLM Local:
#   Docker Engine + Compose, NVIDIA Container Toolkit, utilitários e CLI do Hugging Face.
# Uso:  bash scripts/setup_ubuntu.sh
set -euo pipefail

say()  { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }
fail() { printf "\n\033[1;31mERRO: %s\033[0m\n" "$*"; exit 1; }

# 1) Ambiente
say "Verificando ambiente"
grep -qi microsoft /proc/version || echo "Aviso: não parece WSL. O script segue, mas foi feito para WSL2."
. /etc/os-release
echo "Distribuição: $PRETTY_NAME"

if [ "$(ps -p 1 -o comm=)" != "systemd" ]; then
  fail "systemd não está ativo. Rode:
  printf '[boot]\nsystemd=true\n' | sudo tee /etc/wsl.conf
depois, no PowerShell do Windows:  wsl --shutdown
e abra o Ubuntu de novo. Em seguida rode este script outra vez."
fi

if ! command -v nvidia-smi >/dev/null 2>&1; then
  fail "nvidia-smi não encontrado. Atualize o driver NVIDIA no Windows (não instale driver dentro do WSL)."
fi
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

VRAM_MB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | tr -d ' ')"
if [ "${VRAM_MB:-0}" -lt 7000 ]; then
  echo "GPU com ${VRAM_MB} MiB: use o perfil de 6 GB (chat-4b como padrão). Veja o README."
fi

if command -v docker >/dev/null 2>&1 && docker info 2>/dev/null | grep -qi "docker desktop"; then
  fail "Este Ubuntu está usando o Docker Desktop. Desative a integração WSL dele para esta distro
(Docker Desktop > Settings > Resources > WSL integration > desmarque Ubuntu > Apply & restart)
ou feche o Docker Desktop. Depois, no PowerShell: wsl --shutdown, abra o Ubuntu e rode de novo."
fi

# Restos do Docker Desktop (links para binários do Windows) impedem a instalação do Docker Engine
for b in docker docker-compose docker-credential-desktop.exe docker-credential-wincred.exe; do
  p="$(command -v "$b" 2>/dev/null || true)"
  [ -n "$p" ] || continue
  real="$(readlink -f "$p" 2>/dev/null || echo "$p")"
  if echo "$real" | grep -qiE "docker-desktop|/mnt/wsl|/mnt/c/"; then
    say "Removendo link do Docker Desktop: $p -> $real"
    sudo rm -f "$p"
  fi
done
hash -r

# 2) Utilitários
say "Instalando utilitários"
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg jq bc git python3-venv python3-pip

# Config do Docker Desktop com "credsStore": "desktop.exe" quebra o docker pull no Linux
if [ -f "$HOME/.docker/config.json" ] && grep -q '"credsStore"' "$HOME/.docker/config.json"; then
  say "Removendo credsStore do Docker Desktop em ~/.docker/config.json"
  cp "$HOME/.docker/config.json" "$HOME/.docker/config.json.bak"
  jq 'del(.credsStore)' "$HOME/.docker/config.json.bak" > "$HOME/.docker/config.json"
fi

# 3) Docker Engine (repositório oficial)
if ! command -v docker >/dev/null 2>&1; then
  say "Instalando Docker Engine"
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
  say "Docker já instalado: $(docker --version)"
fi
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" || true

# 4) NVIDIA Container Toolkit
if ! command -v nvidia-ctk >/dev/null 2>&1; then
  say "Instalando NVIDIA Container Toolkit"
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y nvidia-container-toolkit
fi
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 5) Teste da GPU dentro de um container
say "Testando GPU no Docker"
sudo docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi \
  || fail "O container não enxergou a GPU. Veja a seção 'Problemas comuns' do README."

# 6) CLI do Hugging Face (para baixar os modelos)
say "Instalando CLI do Hugging Face em ~/.venvs/hf"
python3 -m venv "$HOME/.venvs/hf"
"$HOME/.venvs/hf/bin/pip" install -q -U "huggingface_hub[cli]"

say "Pronto."
echo "Feche e abra o terminal do Ubuntu (ou rode 'newgrp docker') para usar o docker sem sudo."
echo "Próximo passo:  bash scripts/baixar_modelos.sh"
