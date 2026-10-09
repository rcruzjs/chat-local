"""Autenticação: senhas com Argon2, sessão com token JWT, dependências de acesso."""
import time

import jwt
from argon2 import PasswordHasher
from fastapi import Depends, HTTPException, Request

from .config import settings

_ph = PasswordHasher()


def hash_senha(senha: str) -> str:
    return _ph.hash(senha)


def senha_confere(hash_: str, senha: str) -> bool:
    try:
        return _ph.verify(hash_, senha)
    except Exception:  # noqa: BLE001 - hash inválido ("!") ou senha errada
        return False


def criar_token(user_id: str, papel: str) -> str:
    payload = {"sub": str(user_id), "papel": papel, "exp": int(time.time()) + settings.jwt_horas * 3600}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


async def usuario_atual(request: Request) -> dict:
    cab = request.headers.get("authorization", "")
    if not cab.lower().startswith("bearer "):
        raise HTTPException(401, "Faça login para continuar.")
    try:
        dados = jwt.decode(cab[7:], settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Sessão expirada. Faça login novamente.")
    async with request.app.state.db.acquire() as con:
        u = await con.fetchrow(
            "SELECT id, nome, email AS login, papel, ativo FROM users WHERE id = $1::uuid", dados["sub"]
        )
    if not u or not u["ativo"]:
        raise HTTPException(401, "Usuário inativo. Fale com o administrador.")
    return dict(u)


async def somente_admin(u: dict = Depends(usuario_atual)) -> dict:
    if u["papel"] != "admin":
        raise HTTPException(403, "Apenas administradores.")
    return u
