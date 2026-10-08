#!/usr/bin/env bash
# Teste de fumaça do Sprint 0: todos os serviços respondem e o chat gera texto.
# Uso:  bash scripts/smoke_test.sh [modelo]     (padrão: chat-4b)
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

MODEL="${1:-chat-4b}"
API="http://127.0.0.1:8000"
GW="http://127.0.0.1:4000"
ok=0; falhas=0
pass() { printf "  \033[32mOK\033[0m   %s\n" "$*"; ok=$((ok+1)); }
bad()  { printf "  \033[31mFALHA\033[0m %s\n" "$*"; falhas=$((falhas+1)); }

echo "1) Estado dos serviços (/api/health)"
if h="$(curl -fsS "$API/api/health")"; then
  echo "$h" | jq -r '.servicos[] | "     \(if .ok then "ok   " else "FALHA" end) \(.servico) (\(.ms) ms) \(.detalhe)"'
  [ "$(echo "$h" | jq -r .ok)" = "true" ] && pass "todos os serviços" || bad "algum serviço falhou (veja acima)"
else
  bad "backend não respondeu em $API"
fi

echo "2) Embeddings"
r="$(curl -fsS -X POST "$API/api/embed" -H 'Content-Type: application/json' \
     -d '{"textos":["Estatuto do clube","Ata da reunião do conselho"]}')" \
  && [ "$(echo "$r" | jq .dimensao)" = "1024" ] && pass "2 textos, dimensão 1024" || bad "embeddings: $r"

echo "3) Reranker"
r="$(curl -fsS -X POST "$API/api/rerank" -H 'Content-Type: application/json' \
     -d '{"pergunta":"Quem convoca a assembleia geral?","documentos":["O presidente do conselho convoca a assembleia geral.","A piscina abre às 8h."]}')" \
  && [ "$(echo "$r" | jq '.[0].indice')" = "0" ] && pass "documento certo em 1º lugar" || bad "rerank: $r"

echo "4) Chat via gateway ($MODEL) — a 1ª chamada carrega o modelo na GPU"
t0=$(date +%s.%N)
r="$(curl -fsS "$GW/v1/chat/completions" -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
     -H 'Content-Type: application/json' \
     -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Em uma frase: o que é RAG?\"}],\"max_tokens\":120}")"
t1=$(date +%s.%N)
if [ -n "$r" ] && echo "$r" | jq -e '.choices[0].message.content' >/dev/null; then
  toks="$(echo "$r" | jq '.usage.completion_tokens // 0')"
  seg="$(echo "$t1 - $t0" | bc -l 2>/dev/null || echo 0)"
  pass "resposta: $(echo "$r" | jq -r '.choices[0].message.content' | head -c 160)"
  echo "     $toks tokens em ${seg%.*}s (inclui carga do modelo se for a 1ª vez)"
else
  bad "chat: $r"
fi

echo
echo "Resultado: $ok ok, $falhas falha(s)."
[ "$falhas" -eq 0 ]
