"""Worker assíncrono (Arq). No Sprint 0 só tem um job de teste.

Sprints seguintes: ingest_document (Docling), run_ocr, extract_memory.
Enfileirar no backend:  await arq_pool.enqueue_job("ping", "olá")
"""
import logging

from arq.connections import RedisSettings

from .config import settings

log = logging.getLogger("worker")


async def ping(ctx, mensagem: str = "ping") -> str:
    log.info("job ping recebido: %s", mensagem)
    return f"pong: {mensagem}"


class WorkerSettings:
    functions = [ping]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 4
    job_timeout = 1800  # OCR em lote pode demorar
