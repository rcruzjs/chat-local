"""Backend do Chat LLM Local — Sprint 1 (login e multiusuário).

Públicas:      GET /api/health · POST /api/auth/login
Usuário:       GET /api/auth/me · POST /api/auth/trocar-senha · GET /api/models
               POST /api/chat/stream · GET/PATCH /api/conversations · GET /api/conversations/{id}/messages
               POST /api/feedback · GET /api/history · GET /api/history.csv · GET /api/metrics
Admin:         GET/POST/PATCH /api/admin/users · GET/PATCH /api/admin/models · POST /api/embed · POST /api/rerank
Usuário comum vê só os próprios dados; admin vê tudo e pode filtrar por usuário.
"""
import asyncio
import csv
import io
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

import asyncpg
import httpx
import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .auth import criar_token, hash_senha, senha_confere, somente_admin, usuario_atual
from .config import settings

log = logging.getLogger("chat-local")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def _seed_usuarios(con) -> None:
    """Cria usuario1..usuario10 (senha = login) se ainda não existirem."""
    existentes = {r["email"] for r in await con.fetch("SELECT email FROM users WHERE email LIKE 'usuario%'")}
    for i in range(1, 11):
        login = f"usuario{i}"
        if login not in existentes:
            await con.execute(
                "INSERT INTO users (nome, email, senha_hash, papel) VALUES ($1, $2, $3, 'usuario') ON CONFLICT DO NOTHING",
                f"Usuário {i}", login, hash_senha(login),
            )
            log.info("usuário de teste criado: %s", login)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
    app.state.redis = aioredis.from_url(settings.redis_url)
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=10.0))
    if settings.seed_usuarios:
        async with app.state.db.acquire() as con:
            await _seed_usuarios(con)
    yield
    await app.state.http.aclose()
    await app.state.redis.aclose()
    await app.state.db.close()


app = FastAPI(title="Chat LLM Local", version="0.3.0", lifespan=lifespan)


def _gw_headers() -> dict:
    return {"Authorization": f"Bearer {settings.litellm_master_key}"}


def _baixado(nome: str, local: bool) -> bool:
    """Modelo local só conta como disponível se o arquivo GGUF existir na pasta de modelos."""
    return (not local) or os.path.exists(os.path.join(settings.models_dir, f"{nome}.gguf"))


# ---------------------------------------------------------------- health (público)
async def _check(name: str, coro) -> dict:
    t0 = time.perf_counter()
    try:
        detail = await coro
        return {"servico": name, "ok": True, "ms": round((time.perf_counter() - t0) * 1000), "detalhe": detail}
    except Exception as exc:  # noqa: BLE001
        return {"servico": name, "ok": False, "ms": round((time.perf_counter() - t0) * 1000), "detalhe": str(exc)[:300]}


async def _http_ok(url: str, headers: dict | None = None) -> str:
    r = await app.state.http.get(url, headers=headers, timeout=10.0)
    r.raise_for_status()
    return f"HTTP {r.status_code}"


async def _db_ok() -> str:
    async with app.state.db.acquire() as con:
        n = await con.fetchval("SELECT count(*) FROM users WHERE ativo")
        ext = await con.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    return f"{n} usuários ativos; pgvector {ext}"


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


# ---------------------------------------------------------------- autenticação
class LoginRequest(BaseModel):
    login: str
    senha: str


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    async with app.state.db.acquire() as con:
        u = await con.fetchrow(
            "SELECT id, nome, email AS login, papel, ativo, senha_hash FROM users WHERE lower(email) = lower($1)",
            req.login.strip(),
        )
    if not u or not u["ativo"] or not senha_confere(u["senha_hash"], req.senha):
        await asyncio.sleep(0.5)  # atrasa tentativas de adivinhar senha
        raise HTTPException(401, "Usuário ou senha inválidos.")
    usuario = {"id": str(u["id"]), "nome": u["nome"], "login": u["login"], "papel": u["papel"]}
    return {"token": criar_token(u["id"], u["papel"]), "usuario": usuario}


@app.get("/api/auth/me")
async def me(u: dict = Depends(usuario_atual)):
    return {"id": str(u["id"]), "nome": u["nome"], "login": u["login"], "papel": u["papel"]}


class TrocarSenha(BaseModel):
    senha_atual: str
    senha_nova: str = Field(min_length=6)


@app.post("/api/auth/trocar-senha")
async def trocar_senha(req: TrocarSenha, u: dict = Depends(usuario_atual)):
    async with app.state.db.acquire() as con:
        h = await con.fetchval("SELECT senha_hash FROM users WHERE id = $1", u["id"])
        if not senha_confere(h, req.senha_atual):
            raise HTTPException(400, "Senha atual incorreta.")
        await con.execute("UPDATE users SET senha_hash = $1 WHERE id = $2", hash_senha(req.senha_nova), u["id"])
    return {"ok": True}


# ---------------------------------------------------------------- modelos
@app.get("/api/models")
async def models(u: dict = Depends(usuario_atual)):
    """Modelos de chat que o usuário pode escolher: ativos e com arquivo baixado."""
    async with app.state.db.acquire() as con:
        rows = await con.fetch(
            "SELECT nome, papel, local, contexto_max FROM llm_models WHERE ativo AND papel = 'chat' ORDER BY nome"
        )
    return [dict(r) for r in rows if _baixado(r["nome"], r["local"])]


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


async def _limite_ok(user_id) -> bool:
    """Limite simples de perguntas por usuário por minuto (Redis)."""
    try:
        chave = f"rl:{user_id}:{int(time.time() // 60)}"
        n = await app.state.redis.incr(chave)
        if n == 1:
            await app.state.redis.expire(chave, 70)
        return n <= settings.rate_limit_por_minuto
    except Exception:  # noqa: BLE001 - sem Redis, não bloqueia
        return True


async def _save_answer(msg_id, conv_id, model_id, content, usage, total_ms, ttft_ms, req, erro, interrompido):
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
                """INSERT INTO messages (id, conversation_id, papel, conteudo, modelo_id, executado_em,
                                         params, tokens_in, tokens_out, latencia_ms)
                   VALUES ($1, $2, 'assistant', $3, $4, 'local', $5::jsonb, $6, $7, $8)""",
                msg_id, conv_id, content, model_id, json.dumps(params),
                (usage or {}).get("prompt_tokens"), (usage or {}).get("completion_tokens"), total_ms,
            )
    except Exception:  # noqa: BLE001 - o log nunca pode derrubar o chat
        log.exception("falha ao gravar resposta no histórico")


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, u: dict = Depends(usuario_atual)):
    model = req.model or settings.default_chat_model
    pergunta = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    if not pergunta.strip():
        raise HTTPException(400, "Mensagem vazia.")
    if not await _limite_ok(u["id"]):
        raise HTTPException(429, f"Limite de {settings.rate_limit_por_minuto} perguntas por minuto atingido. Aguarde um pouco.")

    async with app.state.db.acquire() as con:
        m = await con.fetchrow("SELECT id, local, ativo FROM llm_models WHERE nome = $1 AND papel = 'chat'", model)
        if not m or not m["ativo"] or not _baixado(model, m["local"]):
            raise HTTPException(400, f"O modelo '{model}' não está disponível. Escolha outro.")
        model_id = m["id"]
        conv_id = None
        if req.conversation_id:
            conv_id = await con.fetchval(
                "SELECT id FROM conversations WHERE id = $1::uuid AND user_id = $2 AND excluida_em IS NULL",
                req.conversation_id, u["id"],
            )
        if conv_id is None:
            conv_id = await con.fetchval(
                "INSERT INTO conversations (user_id, titulo, modelo_id) VALUES ($1, $2, $3) RETURNING id",
                u["id"], pergunta[:80], model_id,
            )
        await con.execute(
            "INSERT INTO messages (conversation_id, papel, conteudo, modelo_id) VALUES ($1, 'user', $2, $3)",
            conv_id, pergunta, model_id,
        )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + [m.model_dump() for m in req.messages]
    payload = {
        "model": model,
        "messages": messages,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    msg_id = uuid.uuid4()

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
                    terminou = True
                    yield f"data: {json.dumps({'error': erro})}\n\n"
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
                msg_id, conv_id, model_id, "".join(parts), usage, total_ms, ttft_ms, req, erro, not terminou
            ))
        stats = {"modelo": model, "latencia_ms": total_ms, "primeiro_token_ms": ttft_ms, "usage": usage}
        log.info("chat %s %s", u["login"], stats)
        yield f"data: {json.dumps({'done': True, 'message_id': str(msg_id), 'stats': stats})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- conversas
@app.get("/api/conversations")
async def conversations(u: dict = Depends(usuario_atual)):
    async with app.state.db.acquire() as con:
        rows = await con.fetch(
            """SELECT c.id, c.titulo, c.criada_em,
                      COALESCE(max(m.criada_em), c.criada_em) AS atualizada_em,
                      count(m.id) FILTER (WHERE m.papel = 'user') AS perguntas
               FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id
               WHERE c.user_id = $1 AND c.excluida_em IS NULL
               GROUP BY c.id ORDER BY atualizada_em DESC LIMIT 200""",
            u["id"],
        )
    return [dict(r) for r in rows]


async def _conversa_do_usuario(con, conv_id: str, u: dict):
    dono = await con.fetchval("SELECT user_id FROM conversations WHERE id = $1::uuid", conv_id)
    if dono is None or (dono != u["id"] and u["papel"] != "admin"):
        raise HTTPException(404, "Conversa não encontrada.")


@app.get("/api/conversations/{conv_id}/messages")
async def conversation_messages(conv_id: str, u: dict = Depends(usuario_atual)):
    async with app.state.db.acquire() as con:
        await _conversa_do_usuario(con, conv_id, u)
        rows = await con.fetch(
            """SELECT m.id, m.papel, m.conteudo, m.criada_em, l.nome AS modelo,
                      m.tokens_in, m.tokens_out, m.latencia_ms,
                      (m.params->>'primeiro_token_ms')::int AS primeiro_token_ms,
                      f.nota AS feedback
               FROM messages m
               LEFT JOIN llm_models l ON l.id = m.modelo_id
               LEFT JOIN feedback f ON f.message_id = m.id AND f.user_id = $2
               WHERE m.conversation_id = $1::uuid
               ORDER BY m.criada_em""",
            conv_id, u["id"],
        )
    return [dict(r) for r in rows]


class ConversaPatch(BaseModel):
    titulo: str | None = None
    arquivar: bool | None = None


@app.patch("/api/conversations/{conv_id}")
async def conversation_patch(conv_id: str, req: ConversaPatch, u: dict = Depends(usuario_atual)):
    async with app.state.db.acquire() as con:
        await _conversa_do_usuario(con, conv_id, u)
        if req.titulo is not None:
            await con.execute("UPDATE conversations SET titulo = $1 WHERE id = $2::uuid", req.titulo[:120], conv_id)
        if req.arquivar:
            await con.execute("UPDATE conversations SET excluida_em = now() WHERE id = $1::uuid", conv_id)
    return {"ok": True}


# ---------------------------------------------------------------- avaliação (👍/👎)
class FeedbackRequest(BaseModel):
    message_id: str
    nota: int = Field(ge=-1, le=1)  # 1 = útil, -1 = não útil, 0 = remover
    comentario: str | None = None


@app.post("/api/feedback")
async def feedback(req: FeedbackRequest, u: dict = Depends(usuario_atual)):
    async with app.state.db.acquire() as con:
        conv = await con.fetchval(
            "SELECT conversation_id FROM messages WHERE id = $1::uuid AND papel = 'assistant'", req.message_id
        )
        if conv is None:
            raise HTTPException(404, "Resposta não encontrada.")
        await _conversa_do_usuario(con, str(conv), u)
        await con.execute("DELETE FROM feedback WHERE message_id = $1::uuid AND user_id = $2", req.message_id, u["id"])
        if req.nota != 0:
            await con.execute(
                "INSERT INTO feedback (message_id, user_id, nota, comentario) VALUES ($1::uuid, $2, $3, $4)",
                req.message_id, u["id"], req.nota, (req.comentario or None),
            )
    return {"ok": True}


# ---------------------------------------------------------------- histórico e métricas
def _escopo(u: dict, usuario: str | None):
    """Usuário comum: só os próprios dados. Admin: todos, ou um usuário específico."""
    if u["papel"] == "admin":
        return None, (usuario or None)
    return u["id"], None


HISTORY_SQL = """
SELECT a.id, a.criada_em, c.id AS conversation_id, us.email AS usuario, m.nome AS modelo,
       q.conteudo AS pergunta, a.conteudo AS resposta,
       a.tokens_in, a.tokens_out, a.latencia_ms,
       (a.params->>'primeiro_token_ms')::int AS primeiro_token_ms,
       CASE WHEN a.tokens_out > 0 AND a.latencia_ms > COALESCE((a.params->>'primeiro_token_ms')::int, 0)
            THEN round(a.tokens_out * 1000.0 / (a.latencia_ms - COALESCE((a.params->>'primeiro_token_ms')::int, 0)), 1)
       END AS tokens_por_s,
       a.params->>'erro' AS erro,
       COALESCE((a.params->>'interrompido')::boolean, false) AS interrompido,
       (SELECT f.nota FROM feedback f WHERE f.message_id = a.id ORDER BY f.criada_em DESC LIMIT 1) AS feedback,
       (SELECT f.comentario FROM feedback f WHERE f.message_id = a.id ORDER BY f.criada_em DESC LIMIT 1) AS feedback_comentario
FROM messages a
JOIN conversations c ON c.id = a.conversation_id
JOIN users us ON us.id = c.user_id
LEFT JOIN llm_models m ON m.id = a.modelo_id
LEFT JOIN LATERAL (
    SELECT conteudo FROM messages q
    WHERE q.conversation_id = a.conversation_id AND q.papel = 'user' AND q.criada_em <= a.criada_em
    ORDER BY q.criada_em DESC LIMIT 1
) q ON true
WHERE a.papel = 'assistant'
  AND ($1::text IS NULL OR m.nome = $1)
  AND ($2::text IS NULL OR q.conteudo ILIKE '%' || $2 || '%' OR a.conteudo ILIKE '%' || $2 || '%')
  AND ($3::uuid IS NULL OR c.user_id = $3)
  AND ($4::text IS NULL OR us.email = $4)
ORDER BY a.criada_em DESC
"""


@app.get("/api/history")
async def history(limit: int = 50, offset: int = 0, modelo: str | None = None, q: str | None = None,
                  usuario: str | None = None, u: dict = Depends(usuario_atual)):
    limit = max(1, min(limit, 200))
    uid, login = _escopo(u, usuario)
    async with app.state.db.acquire() as con:
        rows = await con.fetch(HISTORY_SQL + " LIMIT $5 OFFSET $6", modelo or None, q or None, uid, login, limit, offset)
    return [dict(r) for r in rows]


@app.get("/api/history.csv")
async def history_csv(modelo: str | None = None, q: str | None = None, usuario: str | None = None,
                      u: dict = Depends(usuario_atual)):
    uid, login = _escopo(u, usuario)
    async with app.state.db.acquire() as con:
        rows = await con.fetch(HISTORY_SQL, modelo or None, q or None, uid, login)
    buf = io.StringIO()
    buf.write("﻿")  # BOM: acentos corretos no Excel
    w = csv.writer(buf, delimiter=";")
    cols = ["criada_em", "usuario", "modelo", "pergunta", "resposta", "tokens_in", "tokens_out",
            "latencia_ms", "primeiro_token_ms", "tokens_por_s", "feedback", "feedback_comentario",
            "erro", "interrompido"]
    w.writerow(cols)
    for r in rows:
        linha = []
        for c in cols:
            v = r[c]
            if v is None:
                linha.append("")
            elif c == "criada_em":
                linha.append(v.isoformat())
            elif c == "tokens_por_s":
                linha.append(str(v).replace(".", ","))
            else:
                linha.append(v)
        w.writerow(linha)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=historico-chat.csv"},
    )


METRICS_BASE = """
WITH a AS (
    SELECT a.*, m.nome AS modelo, (a.params->>'primeiro_token_ms')::int AS ttft
    FROM messages a
    JOIN conversations c ON c.id = a.conversation_id
    JOIN users us ON us.id = c.user_id
    LEFT JOIN llm_models m ON m.id = a.modelo_id
    WHERE a.papel = 'assistant' AND a.criada_em >= now() - make_interval(days => $1)
      AND ($2::uuid IS NULL OR c.user_id = $2)
      AND ($3::text IS NULL OR us.email = $3)
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

OK = " WHERE params->>'erro' IS NULL "


@app.get("/api/metrics")
async def metrics(dias: int = 30, usuario: str | None = None, u: dict = Depends(usuario_atual)):
    dias = max(1, min(dias, 365))
    uid, login = _escopo(u, usuario)
    args = (dias, uid, login)
    async with app.state.db.acquire() as con:
        totais = await con.fetchrow(METRICS_BASE + "SELECT" + METRICS_AGG + "FROM a" + OK, *args)
        por_modelo = await con.fetch(
            METRICS_BASE + "SELECT COALESCE(modelo, '?') AS modelo," + METRICS_AGG + "FROM a" + OK
            + "GROUP BY 1 ORDER BY perguntas DESC", *args)
        por_dia = await con.fetch(
            METRICS_BASE + """SELECT (criada_em AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
                   count(*) AS perguntas, COALESCE(sum(tokens_in), 0) AS tokens_in,
                   COALESCE(sum(tokens_out), 0) AS tokens_out
                   FROM a""" + OK + "GROUP BY 1 ORDER BY 1", *args)
        por_hora = await con.fetch(
            METRICS_BASE + """SELECT extract(hour FROM criada_em AT TIME ZONE 'America/Sao_Paulo')::int AS hora,
                   count(*) AS perguntas FROM a""" + OK + "GROUP BY 1 ORDER BY 1", *args)
        extras = await con.fetchrow(
            METRICS_BASE + """SELECT count(*) FILTER (WHERE params->>'erro' IS NOT NULL) AS erros,
                   (SELECT count(*) FROM feedback f JOIN a ON a.id = f.message_id WHERE f.nota = 1) AS uteis,
                   (SELECT count(*) FROM feedback f JOIN a ON a.id = f.message_id WHERE f.nota = -1) AS nao_uteis,
                   count(DISTINCT conversation_id) AS conversas
                   FROM a""", *args)
        por_usuario = []
        if u["papel"] == "admin":
            por_usuario = await con.fetch(
                METRICS_BASE + """SELECT us.email AS usuario, count(*) AS perguntas,
                       COALESCE(sum(a.tokens_in), 0) AS tokens_in, COALESCE(sum(a.tokens_out), 0) AS tokens_out,
                       round(avg(a.latencia_ms)) AS latencia_media_ms, max(a.criada_em) AS ultimo_uso
                       FROM a JOIN conversations c ON c.id = a.conversation_id JOIN users us ON us.id = c.user_id
                       GROUP BY 1 ORDER BY perguntas DESC""", *args)
    return {
        "dias": dias,
        "totais": {**dict(totais), **dict(extras)},
        "por_modelo": [dict(r) for r in por_modelo],
        "por_dia": [dict(r) for r in por_dia],
        "por_hora": [dict(r) for r in por_hora],
        "por_usuario": [dict(r) for r in por_usuario],
    }


# ---------------------------------------------------------------- administração
class NovoUsuario(BaseModel):
    login: str = Field(min_length=3, max_length=60, pattern=r"^[A-Za-z0-9._@-]+$")
    nome: str = Field(min_length=1, max_length=120)
    papel: str = Field(default="usuario", pattern="^(admin|curador|usuario)$")
    senha: str = Field(min_length=6)


class EditaUsuario(BaseModel):
    nome: str | None = None
    papel: str | None = Field(default=None, pattern="^(admin|curador|usuario)$")
    ativo: bool | None = None
    senha: str | None = Field(default=None, min_length=6)


@app.get("/api/admin/users")
async def admin_users(_: dict = Depends(somente_admin)):
    async with app.state.db.acquire() as con:
        rows = await con.fetch(
            """SELECT us.id, us.email AS login, us.nome, us.papel, us.ativo, us.criada_em,
                      count(m.id) AS perguntas, max(m.criada_em) AS ultimo_uso
               FROM users us
               LEFT JOIN conversations c ON c.user_id = us.id
               LEFT JOIN messages m ON m.conversation_id = c.id AND m.papel = 'user'
               WHERE us.email <> 'admin@local'
               GROUP BY us.id ORDER BY us.papel, us.email"""
        )
    return [dict(r) for r in rows]


@app.post("/api/admin/users")
async def admin_create_user(req: NovoUsuario, _: dict = Depends(somente_admin)):
    async with app.state.db.acquire() as con:
        try:
            uid = await con.fetchval(
                "INSERT INTO users (nome, email, senha_hash, papel) VALUES ($1, $2, $3, $4) RETURNING id",
                req.nome, req.login.strip(), hash_senha(req.senha), req.papel,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, f"Já existe o usuário '{req.login}'.")
    return {"id": str(uid)}


@app.patch("/api/admin/users/{user_id}")
async def admin_edit_user(user_id: str, req: EditaUsuario, eu: dict = Depends(somente_admin)):
    if str(eu["id"]) == user_id and (req.ativo is False or (req.papel and req.papel != "admin")):
        raise HTTPException(400, "Você não pode desativar nem tirar o perfil de admin da própria conta.")
    sets, vals = [], []
    for campo, valor in (("nome", req.nome), ("papel", req.papel), ("ativo", req.ativo)):
        if valor is not None:
            vals.append(valor)
            sets.append(f"{campo} = ${len(vals)}")
    if req.senha:
        vals.append(hash_senha(req.senha))
        sets.append(f"senha_hash = ${len(vals)}")
    if not sets:
        return {"ok": True}
    vals.append(user_id)
    async with app.state.db.acquire() as con:
        r = await con.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ${len(vals)}::uuid", *vals)
    if r.endswith("0"):
        raise HTTPException(404, "Usuário não encontrado.")
    return {"ok": True}


@app.get("/api/admin/models")
async def admin_models(_: dict = Depends(somente_admin)):
    async with app.state.db.acquire() as con:
        rows = await con.fetch("SELECT nome, papel, local, contexto_max, ativo FROM llm_models ORDER BY papel, nome")
    return [{**dict(r), "baixado": _baixado(r["nome"], r["local"])} for r in rows]


class EditaModelo(BaseModel):
    ativo: bool


@app.patch("/api/admin/models/{nome}")
async def admin_edit_model(nome: str, req: EditaModelo, _: dict = Depends(somente_admin)):
    async with app.state.db.acquire() as con:
        r = await con.execute("UPDATE llm_models SET ativo = $1 WHERE nome = $2", req.ativo, nome)
    if r.endswith("0"):
        raise HTTPException(404, "Modelo não encontrado.")
    return {"ok": True}


# ---------------------------------------------------------------- testes de embeddings e rerank (admin)
class EmbedRequest(BaseModel):
    textos: list[str]


@app.post("/api/embed")
async def embed(req: EmbedRequest, _: dict = Depends(somente_admin)):
    r = await app.state.http.post(f"{settings.embed_url}/v1/embeddings", json={"input": req.textos})
    if r.status_code != 200:
        raise HTTPException(502, f"embed HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()["data"]
    return {"quantidade": len(data), "dimensao": len(data[0]["embedding"]) if data else 0}


class RerankRequest(BaseModel):
    pergunta: str
    documentos: list[str]


@app.post("/api/rerank")
async def rerank(req: RerankRequest, _: dict = Depends(somente_admin)):
    r = await app.state.http.post(
        f"{settings.rerank_url}/v1/rerank",
        json={"query": req.pergunta, "documents": req.documentos, "top_n": len(req.documentos)},
    )
    if r.status_code != 200:
        raise HTTPException(502, f"rerank HTTP {r.status_code}: {r.text[:300]}")
    res = r.json().get("results", [])
    return [{"indice": x["index"], "score": x["relevance_score"], "texto": req.documentos[x["index"]]} for x in res]
