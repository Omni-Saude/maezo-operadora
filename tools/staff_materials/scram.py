"""Senha de login gerada na estacao e verificador SCRAM-SHA-256 calculado no cliente.

E a mesma tecnica do `portal_bff_amh` (plano, Onda 2): o dono do banco recebe so o verificador
(`CREATE ROLE ... PASSWORD 'SCRAM-SHA-256$...'`), e a senha em claro existe apenas no DSN que vai
para o segredo. Formato do PostgreSQL (`src/backend/libpq/auth-scram.c`):
``SCRAM-SHA-256$<iteracoes>:<sal b64>$<StoredKey b64>:<ServerKey b64>``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ITERATIONS = 4096


def new_password() -> str:
    # URL-safe: entra no DSN sem escape. 32 bytes de entropia.
    return secrets.token_urlsafe(32)


def verifier(password: str, *, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    if not password.isascii() or not password.isprintable():
        # SASLprep de ASCII imprimivel e a identidade; fora disso nao calculamos por conta.
        raise ValueError("senha fora do subconjunto ASCII imprimivel")
    salt = secrets.token_bytes(16) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def b64(raw: bytes) -> str:
        return base64.b64encode(raw).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"
