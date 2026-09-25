"""Utilitarios comuns: segredo por GetSecretValue, arquivos 0400 sem symlink, falha sem detalhe."""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
from typing import Any


class OpsError(Exception):
    """Recusa com mensagem PUBLICA (nunca carrega valor de segredo)."""


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise OpsError(f"variavel {name} ausente")
    return value


def secrets_client() -> Any:
    import boto3  # type: ignore[import-untyped]

    return boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "sa-east-1"))


def secret_json(client: Any, arn: str) -> dict[str, Any]:
    value = client.get_secret_value(SecretId=arn).get("SecretString")
    if not isinstance(value, str):
        raise OpsError("segredo sem SecretString")
    return parse_json_object(value, "segredo")


def parse_json_object(raw: str, where: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value = dict(pairs)
        if len(value) != len(pairs):
            raise OpsError(f"{where}: chave repetida")
        return value

    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except ValueError:
        raise OpsError(f"{where}: JSON invalido") from None
    if not isinstance(value, dict):
        raise OpsError(f"{where}: esperado objeto JSON")
    return value


def b64(value: object, where: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise OpsError(f"{where}: base64 ausente")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        raise OpsError(f"{where}: base64 invalido") from None
    if base64.b64encode(raw).decode("ascii") != value:
        raise OpsError(f"{where}: base64 nao canonico")
    return raw


def write_private(path: Path, data: bytes, *, owner: int | None = None, mode: int = 0o400) -> None:
    """Arquivo NOVO (O_EXCL, O_NOFOLLOW), `mode`, dono opcional; nunca sobrescreve."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, data)
        if owner is not None:
            os.fchown(fd, owner, owner)
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)
