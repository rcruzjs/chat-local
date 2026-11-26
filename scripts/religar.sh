#!/usr/bin/env bash
# Religa os containers que o Docker NÃO conseguiu iniciar no boot do Windows.
#
# Por que existe: o Docker Desktop sobe antes de o disco do WSL ficar acessível.
# Ao tentar montar arquivos de configuração (ex.: config/litellm.yaml), ele não os
# encontra, a criação do container falha ("mount a directory onto a file") e a
# política "restart: unless-stopped" não tenta de novo. Foi assim que o gateway
# ficou parado por 5 semanas sem ninguém perceber.
#
# O que faz: espera o Docker responder e recria SÓ os containers que têm erro de
# inicialização (State.Error preenchido). Containers que você parou de propósito
# (docker compose stop) não são tocados.
#
# Uso:  bash scripts/religar.sh [pasta-do-projeto ...]
#       Sem argumentos: chat-local e nlp-sql (nessa ordem, pois o nlp-sql usa a rede do chat-local).
# Instalar para rodar sozinho a cada boot do WSL: bash scripts/instalar_religar.sh
set -uo pipefail

ESPERA_MAX="${ESPERA_MAX:-300}"   # segundos aguardando o Docker Desktop subir
if [ "$#" -gt 0 ]; then
    PROJETOS=("$@")
else
    PROJETOS=("$HOME/projetos/chat-local" "$HOME/projetos/nlp-sql")
fi

log() { echo "[$(date '+%F %T')] $*"; }

# 1) Esperar o Docker responder
espera=0
until docker info >/dev/null 2>&1; do
    if [ "$espera" -ge "$ESPERA_MAX" ]; then
        log "Docker não respondeu em ${ESPERA_MAX}s. Abortando."
        exit 1
    fi
    sleep 5
    espera=$((espera + 5))
done
log "Docker respondendo (aguardou ${espera}s)."

# 2) Em cada projeto, recriar os serviços que falharam ao iniciar
falhas_gerais=0
for dir in "${PROJETOS[@]}"; do
    nome="$(basename "$dir")"
    if [ ! -f "$dir/docker-compose.yml" ]; then
        log "$nome: sem docker-compose.yml, pulando."
        continue
    fi
    cd "$dir" || continue

    quebrados=()
    for id in $(docker compose ps -a -q); do
        erro="$(docker inspect -f '{{.State.Error}}' "$id")"
        if [ -n "$erro" ]; then
            servico="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$id")"
            quebrados+=("$servico")
            log "$nome/$servico: falhou ao iniciar -> ${erro:0:120}..."
        fi
    done

    if [ "${#quebrados[@]}" -eq 0 ]; then
        log "$nome: nenhum container com erro de inicialização."
        continue
    fi

    if docker compose up -d --force-recreate --no-deps "${quebrados[@]}"; then
        log "$nome: recriados ${quebrados[*]}."
    else
        log "$nome: FALHA ao recriar ${quebrados[*]}."
        falhas_gerais=1
    fi
done

exit "$falhas_gerais"
