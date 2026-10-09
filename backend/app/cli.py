"""Comandos de administração pelo terminal.

Criar (ou redefinir) a conta de administrador:
    docker compose exec api python -m app.cli criar-admin <login> "<Nome completo>"

Redefinir a senha de qualquer usuário:
    docker compose exec api python -m app.cli senha <login>
"""
import asyncio
import getpass
import sys

import asyncpg

from .auth import hash_senha
from .config import settings


def _pedir_senha() -> str:
    senha = getpass.getpass("Nova senha: ")
    if senha != getpass.getpass("Repita a senha: "):
        sys.exit("As senhas não conferem.")
    if len(senha) < 6:
        sys.exit("A senha precisa ter pelo menos 6 caracteres.")
    return senha


async def criar_admin(login: str, nome: str) -> None:
    senha = _pedir_senha()
    con = await asyncpg.connect(settings.database_url)
    try:
        uid = await con.fetchval(
            """INSERT INTO users (nome, email, senha_hash, papel, ativo)
               VALUES ($1, $2, $3, 'admin', true)
               ON CONFLICT (email) DO UPDATE
                 SET nome = EXCLUDED.nome, senha_hash = EXCLUDED.senha_hash, papel = 'admin', ativo = true
               RETURNING id""",
            nome, login, hash_senha(senha),
        )
        # O histórico gravado antes do login (usuário provisório) passa para o admin
        r = await con.execute(
            "UPDATE conversations SET user_id = $1 WHERE user_id IN (SELECT id FROM users WHERE email = 'admin@local')",
            uid,
        )
        await con.execute(
            "UPDATE feedback SET user_id = $1 WHERE user_id IN (SELECT id FROM users WHERE email = 'admin@local')", uid
        )
        await con.execute("UPDATE users SET ativo = false WHERE email = 'admin@local'")
        print(f"Administrador '{login}' pronto. Conversas antigas transferidas: {r.split()[-1]}.")
    finally:
        await con.close()


async def trocar_senha(login: str) -> None:
    senha = _pedir_senha()
    con = await asyncpg.connect(settings.database_url)
    try:
        r = await con.execute("UPDATE users SET senha_hash = $1 WHERE email = $2", hash_senha(senha), login)
        print("Senha alterada." if r.endswith("1") else f"Usuário '{login}' não encontrado.")
    finally:
        await con.close()


def main() -> None:
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "criar-admin":
        nome = args[2] if len(args) > 2 else args[1]
        asyncio.run(criar_admin(args[1], nome))
    elif len(args) == 2 and args[0] == "senha":
        asyncio.run(trocar_senha(args[1]))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
