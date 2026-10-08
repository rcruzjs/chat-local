"""Backend do Chat LLM Local — Sprint 0.

Rotas:
  GET  /api/health        estado de todos os serviços
  GET  /api/models        modelos ativos (tabela llm_models)
  POST /api/chat/stream   chat com streaming (SSE) via gateway LiteLLM, gravando no histórico
  GET  /api/history       histórico de perguntas e respostas com métricas
  GET  /api/history.csv   exportação do histórico (Excel)
  GET  /api/metrics       métricas agregadas: totais, por modelo, por dia, por hora
  POST /api/embed         teste do serviço de embeddings
  POST /api/rerank        teste do reranker
"""
import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

import asyncpg
import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .config import settings

log = logging.getLogger("chat-local")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
    app.state.redis = aioredis.from_url(settings.redis_url)
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=10.0))
    # Usuário padrão até o Sprint 1 (login). Todas as conversas ficam no nome dele.
    async with app.state.db.acquire() as con:
        app.state.default_user = await con.fetchval(
            """INSERT INTO users (nome, email, senha_hash, papel)
               VALUES ('Administrador', 'admin@local', '!', 'admin')
               ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
               RETURNING id"""
        )
    yield
    await app.state.http.aclose()
    await app.state.redis.aclose()
    await app.state.db.close()


app = FastAPI(title="Chat LLM Local", version="0.2.0", lifespan=lifespan)


def _gw_headers() -> dict:
    return {"Authorization": f"Bearer {settings.litellm_master_key}"}


# ---------------------------------------------------------------- health
async def _check(name: str, coro) -> dict:
    t0 = time.perf_counter()
    try:
        detail = await coro
        return {"servico": name, "ok": True, "ms": round((time.perf_counter() - t0) * 1000), "detalhe": detail}
    except Exception as exc:  # noqa: BLE001 - queremos o erro textual no painel
        return {"servico": name, "ok": False, "ms": round((time.perf_counter() - t0) * 1000), "detalhe": str(exc)[:300]}


async def _http_ok(url: str, headers: dict | None = None) -> str:
    r = await app.state.http.get(url, headers=headers, timeout=10.0)
    r.raise_for_status()
    return f"HTTP {r.status_code}"


async def _db_ok() -> str:
    async with app.state.db.acquire() as con:
        n = await con.fetchval("SELECT count(*) FROM llm_models WHERE ativo")
        ext = await con.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    return f"{n} modelos ativos; pgvector {ext}"


async def _redis_ok() -> str:
    return "PONG" if await app.state.redis.ping() else "sem resposta"


@app.get("/api/health")
async def health():
    checks = [
        await _check("postgres", _db_ok()),
        await _check("redis", _redis_ok()),
        await _check("gateway", _http_ok(f"{settings.litellm_url}/health/liveliness")),
        await _check("llm-swap", _http_ok(f"{settings.llm_swap_url}/health")),
        await _check("embed", _http_ok(f"{settings.embed_url}/health")),
        await _check("rerank", _http_ok(f"{settings.rerank_url}/health")),
    ]
    return {"ok": all(c["ok"] for c in checks), "servicos": checks}


# ---------------------------------------------------------------- modelos
@app.get("/api/models")
async def models():
    async with app.state.db.acquire() as con:
        rows = await con.fetch(
            "SELECT nome, papel, local, contexto_max, params FROM llm_models WHERE ativo ORDER BY papel, nome"
        )
    return [
        {**dict(r), "params": json.loads(r["params"]) if isinstance(r["params"], str) else r["params"]}
        for r in rows
    ]


# ---------------------------------------------------------------- chat
class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    conversation_id: str | None = None
    temperature: float = 0.3
    max_tokens: int = 1024


SYSTEM_PROMPT = (
    "Você é um assistente que responde em português do Brasil, de forma direta. "
    "Se não souber, diga que não sabe."
)


async def _save_answer(conv_id, model_id, content, usage, total_ms, ttft_ms, req, erro, interrompido):
    """Grava a resposta (mesmo parcial ou com erro) e as métricas da troca."""
    try:
        params = {
            "primeiro_token_ms": ttft_ms,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
            "erro": erro,
            "interrompido": interrompido,
        }
        async with app.state.db.acquire() as con:
            await con.execute(
                """INSERT INTO messages (conversation_id, papel, conteudo, modelo_id, executado_em,
                                         params, tokens_in, tokens_out, latencia_ms)
                   VALUES ($1, 'assistant', $2, $3, 'local', $4::jsonb, $5, $6, $7)""",
                conv_id, content, model_id, json.dumps(params),
                (usage or {}).get("prompt_tokens"), (usage or {}).get("completion_tokens"), total_ms,
            )
    except Exception:  # noqa: BLE001 - o log nunca pode derrubar o chat
        log.exception("falha ao gravar resposta no histórico")


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    model = req.model or settings.default_chat_model
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + [m.model_dump() for m in req.messages]
    pergunta = next((m.content for m in reversed(req.messages) if m.role == "user"), "")

    # Conversa e pergunta gravadas antes de chamar o modelo
    async with app.state.db.acquire() as con:
        model_id = await con.fetchval("SELECT id FROM llm_models WHERE nome = $1", model)
        conv_id = None
        if req.conversation_id:
            conv_id = await con.fetchval("SELECT id FROM conversations WHERE id = $1::uuid", req.conversation_id)
        if conv_id is None:
            conv_id = await con.fetchval(
                "INSERT INTO conversations (user_id, titulo, modelo_id) VALUES ($1, $2, $3) RETURNING id",
                app.state.default_user, pergunta[:80], model_id,
            )
        await con.execute(
            "INSERT INTO messages (conversation_id, papel, conteudo, modelo_id) VALUES ($1, 'user', $2, $3)",
            conv_id, pergunta, model_id,
        )

    payload = {
        "model": model,
        "messages": messages,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    async def gen():
        t0 = time.perf_counter()
        first = None
        usage = None
        parts: list[str] = []
        erro = None
        terminou = False
        yield f"data: {json.dumps({'conversation_id': str(conv_id)})}\n\n"
        try:
            async with app.state.http.stream(
                "POST", f"{settings.litellm_url}/v1/chat/completions", json=payload, headers=_gw_headers()
            ) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="ignore")[:500]
                    erro = f"gateway HTTP {r.status_code}: {body}"
                    yield f"data: {json.dumps({'error': erro})}\n\n"
                    terminou = True
                    return
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    delta = (choices[0].get("delta") or {}).get("content") if choices else None
                    if delta:
                        if first is None:
                            first = time.perf_counter()
                        parts.append(delta)
                        yield f"data: {json.dumps({'delta': delta})}\n\n"
            terminou = True
        except httpx.HTTPError as exc:
            erro = f"falha no gateway: {exc}"
            terminou = True
            yield f"data: {json.dumps({'error': erro})}\n\n"
            return
        finally:
            total_ms = round((time.perf_counter() - t0) * 1000)
            ttft_ms = round((first - t0) * 1000) if first else None
            # create_task: grava mesmo se o usuário clicar em "Parar" e a conexão cair
            asyncio.create_task(_save_answer(
                conv_id, model_id, "".join(parts), usage, total_ms, ttft_ms, req, erro, not terminou
            ))
        stats = {"modelo": model, "latencia_ms": total_ms, "primeiro_token_ms": ttft_ms, "usage": usage}
        log.info("chat %s", stats)
        yield f"data: {json.dumps({'done': True, 'stats': stats})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- histórico e métricas
HISTORY_SQL = """
SELECT a.id, a.criada_em, c.id AS conversation_id, m.nome AS modelo,
       u.conteudo AS pergunta, a.conteudo AS resposta,
       a.tokens_in, a.tokens_out, a.latencia_ms,
       (a.params->>'primeiro_token_ms')::int AS primeiro_token_ms,
       CASE WHEN a.tokens_out > 0 AND a.latencia_ms > COALESCE((a.params->>'primeiro_token_ms')::int, 0)
            THEN round(a.tokens_out * 1000.0 / (a.latencia_ms - COALESCE((a.params->>'primeiro_token_ms')::int, 0)), 1)
       END AS tokens_por_s,
       a.params->>'erro' AS erro,
       COALESCE((a.params->>'interrompido')::boolean, false) AS interrompido
FROM messages a
JOIN conversations c ON c.id = a.conversation_id
LEFT JOIN llm_models m ON m.id = a.modelo_id
LEFT JOIN LATERAL (
    SELECT conteudo FROM messages u
    WHERE u.conversation_id = a.conversation_id AND u.papel = 'user' AND u.criada_em <= a.criada_em
    ORDER BY u.criada_em DESC LIMIT 1
) u ON true
WHERE a.papel = 'assistant'
  AND ($1::text IS NULL OR m.nome = $1)
  AND ($2::text IS NULL OR u.conteudo ILIKE '%' || $2 || '%' OR a.conteudo ILIKE '%' || $2 || '%')
ORDER BY a.criada_em DESC
"""


@app.get("/api/history")
async def history(limit: int = 50, offset: int = 0, modelo: str | None = None, q: str | None = None):
    limit = max(1, min(limit, 200))
    async with app.state.db.acquire() as con:
        rows = await con.fetch(HISTORY_SQL + " LIMIT $3 OFFSET $4", modelo or None, q or None, limit, offset)
    return [dict(r) for r in rows]


@app.get("/api/history.csv")
async def history_csv(modelo: str | None = None, q: str | None = None):
    import csv
    import io

    async with app.state.db.acquire() as con:
        rows = await con.fetch(HISTORY_SQL, modelo or None, q or None)
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM: acentos corretos no Excel
    w = csv.writer(buf, delimiter=";")
    cols = ["criada_em", "modelo", "pergunta", "resposta", "tokens_in", "tokens_out",
            "latencia_ms", "primeiro_token_ms", "tokens_por_s", "erro", "interrompido"]
    w.writerow(cols)
    for r in rows:
        w.writerow([
            str(r[c]).replace(".", ",") if c == "tokens_por_s" and r[c] is not None else
            (r[c].isoformat() if c == "criada_em" else ("" if r[c] is None else r[c]))
            for c in cols
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=historico-chat.csv"},
    )


METRICS_BASE = """
WITH a AS (
    SELECT a.*, m.nome AS modelo, (a.params->>'primeiro_token_ms')::int AS ttft
    FROM messages a LEFT JOIN llm_models m ON m.id = a.modelo_id
    WHERE a.papel = 'assistant' AND a.criada_em >= now() - make_interval(days => $1)
      AND a.params->>'erro' IS NULL
)
"""

METRICS_AGG = """
    count(*) AS perguntas,
    COALESCE(sum(tokens_in), 0) AS tokens_in,
    COALESCE(sum(tokens_out), 0) AS tokens_out,
    round(avg(latencia_ms)) AS latencia_media_ms,
    round(percentile_cont(0.95) WITHIN GROUP (ORDER BY latencia_ms)::numeric) AS latencia_p95_ms,
    round(avg(ttft)) AS primeiro_token_medio_ms,
    round(avg(CASE WHEN tokens_out > 0 AND latencia_ms > COALESCE(ttft, 0)
                   THEN tokens_out * 1000.0 / (latencia_ms - COALESCE(ttft, 0)) END), 1) AS tokens_por_s
"""


@app.get("/api/metrics")
async def metrics(dias: int = 30):
    dias = max(1, min(dias, 365))
    async with app.state.db.acquire() as con:
        totais = await con.fetchrow(METRICS_BASE + "SELECT" + METRICS_AGG + "FROM a", dias)
        por_modelo = await con.fetch(
            METRICS_BASE + "SELECT COALESCE(modelo, '?') AS modelo," + METRICS_AGG
            + "FROM a GROUP BY 1 ORDER BY perguntas DESC", dias)
        por_dia = await con.fetch(
            METRICS_BASE + """SELECT (criada_em AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
                   count(*) AS perguntas, COALESCE(sum(tokens_in), 0) AS tokens_in,
                   COALESCE(sum(tokens_out), 0) AS tokens_out
                   FROM a GROUP BY 1 ORDER BY 1""", dias)
        por_hora = await con.fetch(
            METRICS_BASE + """SELECT extract(hour FROM criada_em AT TIME ZONE 'America/Sao_Paulo')::int AS hora,
                   count(*) AS perguntas FROM a GROUP BY 1 ORDER BY 1""", dias)
        erros = await con.fetchval(
            """SELECT count(*) FROM messages WHERE papel = 'assistant' AND params->>'erro' IS NOT NULL
               AND criada_em >= now() - make_interval(days => $1)""", dias)
    return {
        "dias": dias,
        "totais": {**dict(totais), "erros": erros},
        "por_modelo": [dict(r) for r in por_modelo],
        "por_dia": [dict(r) for r in por_dia],
        "por_hora": [dict(r) for r in por_hora],
    }


# ---------------------------------------------------------------- testes de embeddings e rerank
class EmbedRequest(BaseModel):
    textos: list[str]


@app.post("/api/embed")
async def embed(req: EmbedRequest):
    r = await app.state.http.post(f"{settings.embed_url}/v1/embeddings", json={"input": req.textos})
    if r.status_code != 200:
        raise HTTPException(502, f"embed HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()["data"]
    return {"quantidade": len(data), "dimensao": len(data[0]["embedding"]) if data else 0}


class RerankRequest(BaseModel):
    pergunta: str
    documentos: list[str]


@app.post("/api/rerank")
async def rerank(req: RerankRequest):
    r = await app.state.http.post(
        f"{settings.rerank_url}/v1/rerank",
        json={"query": req.pergunta, "documents": req.documentos, "top_n": len(req.documentos)},
    )
    if r.status_code != 200:
        raise HTTPException(502, f"rerank HTTP {r.status_code}: {r.text[:300]}")
    res = r.json().get("results", [])
    return [{"indice": x["index"], "score": x["relevance_score"], "texto": req.documentos[x["index"]]} for x in res]
