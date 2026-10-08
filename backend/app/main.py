"""Backend do Chat LLM Local — Sprint 0.

Rotas:
  GET  /api/health        estado de todos os serviços
  GET  /api/models        modelos ativos (tabela llm_models)
  POST /api/chat/stream   chat com streaming (SSE) via gateway LiteLLM
  POST /api/embed         teste do serviço de embeddings
  POST /api/rerank        teste do reranker
"""
import json
import logging
import time
from contextlib import asynccontextmanager

import asyncpg
import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import settings

log = logging.getLogger("chat-local")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
    app.state.redis = aioredis.from_url(settings.redis_url)
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=10.0))
    yield
    await app.state.http.aclose()
    await app.state.redis.aclose()
    await app.state.db.close()


app = FastAPI(title="Chat LLM Local", version="0.1.0", lifespan=lifespan)


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
    temperature: float = 0.3
    max_tokens: int = 1024


SYSTEM_PROMPT = (
    "Você é um assistente que responde em português do Brasil, de forma direta. "
    "Se não souber, diga que não sabe."
)


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    model = req.model or settings.default_chat_model
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + [m.model_dump() for m in req.messages]
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
        try:
            async with app.state.http.stream(
                "POST", f"{settings.litellm_url}/v1/chat/completions", json=payload, headers=_gw_headers()
            ) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="ignore")[:500]
                    yield f"data: {json.dumps({'error': f'gateway HTTP {r.status_code}: {body}'})}\n\n"
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
                        yield f"data: {json.dumps({'delta': delta})}\n\n"
        except httpx.HTTPError as exc:
            yield f"data: {json.dumps({'error': f'falha no gateway: {exc}'})}\n\n"
            return
        total_ms = round((time.perf_counter() - t0) * 1000)
        ttft_ms = round((first - t0) * 1000) if first else None
        stats = {"modelo": model, "latencia_ms": total_ms, "primeiro_token_ms": ttft_ms, "usage": usage}
        log.info("chat %s", stats)  # Sprint 1: gravar em messages
        yield f"data: {json.dumps({'done': True, 'stats': stats})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
