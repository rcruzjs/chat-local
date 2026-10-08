#!/usr/bin/env bash
# Registra o uso de GPU a cada 2 s enquanto você usa o chat. Ctrl+C para parar.
# Saída: logs/vram-AAAAMMDD-HHMM.csv  (use para preencher a tabela "Orçamento de VRAM")
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
out="logs/vram-$(date +%Y%m%d-%H%M).csv"
echo "Gravando em $out (Ctrl+C para parar)"
nvidia-smi --query-gpu=timestamp,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw \
  --format=csv -l 2 | tee "$out"
