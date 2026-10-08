#!/usr/bin/env bash
# Baixa os modelos GGUF e cria os nomes fixos que o llama-swap e o compose esperam:
#   models/chat-4b.gguf, chat-8b.gguf, chat-moe-30b.gguf, vision-3b.gguf, vision-3b-mmproj.gguf,
#   embed-bge-m3.gguf, rerank-bge-v2-m3.gguf
#
# Uso:
#   bash scripts/baixar_modelos.sh            # todos (~30 GB)
#   SEM_MOE=1 bash scripts/baixar_modelos.sh  # pula o MoE 30B (~18 GB a menos)
#   SEM_8B=1  bash scripts/baixar_modelos.sh  # pula o 8B (~5 GB a menos)
#
# Para trocar um modelo, mude o repositório/padrão abaixo e rode de novo.
set -euo pipefail
cd "$(dirname "$0")/.."

C4_REPO="${C4_REPO:-unsloth/Qwen3-4B-Instruct-2507-GGUF}";       C4_PAT="${C4_PAT:-*-Q4_K_M.gguf}"
CHAT_REPO="${CHAT_REPO:-Qwen/Qwen3-8B-GGUF}";                       CHAT_PAT="${CHAT_PAT:-*Q4_K_M.gguf}"
MOE_REPO="${MOE_REPO:-Qwen/Qwen3-30B-A3B-GGUF}";                   MOE_PAT="${MOE_PAT:-*Q4_K_M.gguf}"
VIS_REPO="${VIS_REPO:-ggml-org/Qwen2.5-VL-3B-Instruct-GGUF}";      VIS_PAT="${VIS_PAT:-*Q4_K_M.gguf}"
EMB_REPO="${EMB_REPO:-gpustack/bge-m3-GGUF}";                       EMB_PAT="${EMB_PAT:-*Q8_0.gguf}"
RRK_REPO="${RRK_REPO:-gpustack/bge-reranker-v2-m3-GGUF}";           RRK_PAT="${RRK_PAT:-*Q8_0.gguf}"

HF="$HOME/.venvs/hf/bin/hf"
[ -x "$HF" ] || HF="$HOME/.venvs/hf/bin/huggingface-cli"
[ -x "$HF" ] || { echo "CLI do Hugging Face não encontrada. Rode scripts/setup_ubuntu.sh antes."; exit 1; }

mkdir -p models/src

# baixar <nome> <repo> <padrão>  -> baixa para models/src/<nome>/
baixar() {
  local nome="$1" repo="$2" pat="$3"
  echo -e "\n==> $nome  ($repo, $pat)"
  "$HF" download "$repo" --include "$pat" --local-dir "models/src/$nome"
}

# ligar <nome-fixo> <nome> <padrão-local>  -> link relativo (funciona dentro do container)
ligar() {
  local alvo="$1" nome="$2" pat="$3" arq
  arq="$(find "models/src/$nome" -name "$pat" ! -name 'mmproj*' | sort | head -n1)"
  [ -n "$arq" ] || { echo "ERRO: nenhum arquivo '$pat' em models/src/$nome"; exit 1; }
  ln -sfn "${arq#models/}" "models/$alvo"
  echo "    models/$alvo -> ${arq#models/}"
}

baixar chat-4b "$C4_REPO" "$C4_PAT";      ligar chat-4b.gguf chat-4b "$C4_PAT"

if [ "${SEM_8B:-0}" != "1" ]; then
  baixar chat-8b "$CHAT_REPO" "$CHAT_PAT";  ligar chat-8b.gguf chat-8b "$CHAT_PAT"
fi

if [ "${SEM_MOE:-0}" != "1" ]; then
  baixar chat-moe-30b "$MOE_REPO" "$MOE_PAT";  ligar chat-moe-30b.gguf chat-moe-30b "$MOE_PAT"
fi

baixar vision-3b "$VIS_REPO" "$VIS_PAT"
"$HF" download "$VIS_REPO" --include "mmproj*" --local-dir "models/src/vision-3b"
ligar vision-3b.gguf vision-3b "$VIS_PAT"
mm="$(find models/src/vision-3b -name 'mmproj*.gguf' | sort | head -n1)"
[ -n "$mm" ] || { echo "ERRO: mmproj não encontrado para o modelo de visão"; exit 1; }
ln -sfn "${mm#models/}" models/vision-3b-mmproj.gguf
echo "    models/vision-3b-mmproj.gguf -> ${mm#models/}"

baixar embed "$EMB_REPO" "$EMB_PAT";      ligar embed-bge-m3.gguf embed "$EMB_PAT"
baixar rerank "$RRK_REPO" "$RRK_PAT";     ligar rerank-bge-v2-m3.gguf rerank "$RRK_PAT"

echo -e "\nModelos prontos:"
ls -l models/*.gguf
du -sh models/src
