"""Constantes e utilitarios do C1. Tudo que e segredo vive no volume `c1private` (/c1), nunca no repo."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from maezo.portal.engine.profile import canonicalize

ROOT = Path("/c1")
ADMIN = ROOT / "admin"  # senhas dos logins de teste que nao tem gerador (admin, cibseven_app, maezo_app)
PGTLS = ROOT / "pgtls"  # certificado do PostgreSQL descartavel (dono uid 70, o postgres do alpine)
ENGINE_RUN = ROOT / "engine-run"  # montado read-only em /run/maezo/c1 do engine
ENGINE_NATIVE = ROOT / "engine-native"  # montado read-only em /run/maezo/native do engine
MATERIALS = ROOT / "materials"  # saida do `generate` (T1.3)
#: A raiz de TESTE do C1. Descartavel: nasce aqui, morre com o volume. NUNCA a do aprovador.
TEST_ROOT = ROOT / "TEST-ROOT-DESCARTAVEL-C1"
APPROVER_OUT = ROOT / "approver-out"  # installation-root.der + installation-proof.json da raiz de TESTE
ASSEMBLED = ROOT / "assembled"
STATE = ROOT / "state"  # fatos medidos (OIDs, digests) que um passo passa ao seguinte

TENANT = "amh"
ENVIRONMENT = "dev"
ENGINE_NAME = "default"
INCARNATION = "c1-incarnation-1"
NATIVE_HOSTNAME = "engine-native.c1.internal"
PG_HOST = "postgres"
DATABASE = "maezo"
NATIVE_SCHEMA = "maezo_native"
ENGINE_SCHEMA = "cibseven"
#: Workload do portal no Q2 (scope.workload_ref do catalogo e das memberships).
PORTAL_WORKLOAD = "portal-staff-amh"
CATALOG_REF = "staff-catalog-amh"
SESSION_LOCK_LOGIN = "portal_staff_lock_amh"
WITNESS_LOGIN = "portal_staff_witness_amh"
ISSUER_LOGIN = "maezo_native_case_issuer"
ISSUER_WITNESS_LOGIN = "maezo_native_issuer_witness"
# Prefixo da membership publicada pelo job T1.5: admissao T1.7a E entrada `identity_verifier` (F9).
MEMBERSHIP_PREFIX = f"portal-identity:{TENANT}:"
OBSERVER_LOGIN = "portal_read_source_amh"
OWNER_LOGIN = "maezo_native_schema_owner"
NATIVE_LOGINS = (OWNER_LOGIN, ISSUER_LOGIN, ISSUER_WITNESS_LOGIN, OBSERVER_LOGIN)


def now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    """O formato de instante do perfil fechado (microssegundos, `Z`)."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def later(**delta: float) -> str:
    return iso(now() + timedelta(**delta))


def earlier(**delta: float) -> str:
    return iso(now() - timedelta(**delta))


def jcs(value: Any) -> bytes:
    return canonicalize(value)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def password() -> str:
    return secrets.token_urlsafe(24)


def write(path: Path, data: bytes | str, mode: int = 0o400, *, uid: int | None = None) -> None:
    """Grava (substituindo) com modo explicito. So para o que o harness monta; o generate usa a ferramenta."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        path.unlink()
    raw = data.encode() if isinstance(data, str) else data
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    if uid is not None:
        os.chown(path, uid, uid)
    os.chmod(path, mode)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def state(name: str) -> Any:
    return json.loads((STATE / f"{name}.json").read_text(encoding="utf-8"))


def save_state(name: str, value: Any) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def admin_dsn(user: str = "postgres", password_file: str = "postgres-password", database: str = DATABASE) -> str:
    secret = read_text(ADMIN / password_file)
    return f"postgresql://{user}:{secret}@{PG_HOST}:5432/{database}"


def tls_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(PGTLS / "ca.pem"))


def step(name: str, ok: bool, detail: str) -> None:
    """Uma linha medida por passo, o formato que o run.sh agrega no resumo."""
    print(f"C1 {name}: {'PASS' if ok else 'FAIL'} {detail}", flush=True)
