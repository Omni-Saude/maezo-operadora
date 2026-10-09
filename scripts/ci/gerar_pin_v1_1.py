#!/usr/bin/env python3
"""Gera o bloco de um manifest ADITIVO (`manifest_v1_1`, `manifest_v1_2`) do pin `contracts.lock.json`.

GENERALIZADO EM 07/10/2026 para o v1.2 (TINA: `schemas/contracts/maezo/v1.2/contract-manifest.yaml`, 1
artefato OpenAPI `schemas/openapi/maezo/v1/tina.openapi.yaml`): `--manifest-version v1.2` escolhe o
bloco, o caminho do manifest, o `manifest_version` exigido e a lista de artefatos. O padrao continua
v1.1, entao toda invocacao antiga faz exatamente o que fazia. O rigor e' o MESMO para os dois: o bloco
so' e' gravado se o verificador (`check_manifest_additive`) aceitar, e um bloco ja' existente nunca e'
sobrescrito (pin imutavel). O nome do arquivo ficou por compatibilidade com runbooks e testes.
`--manifest-version` aceita todo manifest de `ADDITIVE_BY_VERSION` (v1.1 a v1.4; o v1.4, de 09/10/2026, e'
o billing-status 0.2.0 com a finalidade `atendimento_whatsapp`).

Quando o manifest ADITIVO v1.1 da AMH (`schemas/contracts/maezo/v1.1/contract-manifest.yaml`) for
PUBLICADO, o pin vira um passo mecanico: este script le os bytes publicados, calcula os digests com
o MESMO esquema do pin v1 (sha256 do arquivo inteiro, git blob sha1, tamanho em bytes) e monta o
bloco v1.1 com as 2 entradas OpenAPI (billing-status e subject-resolution). So OpenAPI: o bloco NAO
leva ids Glue nem `schema_version_status`.

Fontes do manifest (escolha uma):
  --manifest PATH            arquivo local com os bytes PUBLICADOS (precisa de --manifest-commit-sha).
  --gh-ref SHA_OU_REF        baixa os bytes crus do GitHub nesse commit via `gh api`
                             (repo padrao Omni-Saude/amh-data-platform); o commit e' resolvido e o
                             blob sha e' conferido contra o que o GitHub informa.

Saida: imprime o bloco (JSON) em stdout. Com `--write`, grava no lock (so se ainda nao existir
`manifest_v1_1`: o pin e' imutavel) e so se o resultado passar na validacao do verificador.

Recusa: manifest com status != PUBLISHED, com placeholder `<SET-AT-PUBLICATION...>`, com chaves
duplicadas, ou cujo digest dos 2 OpenAPI nao exista. Nunca usa rede, exceto `--gh-ref` (via `gh`).

Nunca editar o lock a mao (ver `lock_format._comment`). Este script e' a unica via suportada.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # execucao direta: `python scripts/ci/gerar_pin_v1_1.py`
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci.verify_amh_contract_pin import (  # noqa: E402
    ADDITIVE_BY_VERSION,
    DEFAULT_LOCK_PATH,
    FROZEN_STATUS,
    V1_1,
    V1_1_ARTIFACT_PATHS,
    V1_1_LOCK_KEY,
    V1_1_MANIFEST_PATH,
    AdditiveManifest,
    check_manifest_additive,
    scan_manifest_path_digests,
    scan_manifest_scalars,
)

# Reexportados: os nomes do v1.1 continuam importaveis daqui como antes da generalizacao.
__all__ = ["V1_1_ARTIFACT_PATHS", "V1_1_LOCK_KEY", "V1_1_MANIFEST_PATH"]

DEFAULT_AMH_REPO = "Omni-Saude/amh-data-platform"
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RUN_TAIL_RE = re.compile(r"(\d+)$")


class GerarPinError(Exception):
    """Erro de uso ou de conteudo do manifest. Mensagem pronta para o operador."""


def git_blob_sha(raw: bytes) -> str:
    """sha1 do objeto blob do git (`blob <tamanho>\\0<bytes>`), igual a `git hash-object`."""
    return hashlib.sha1(b"blob %d\0" % len(raw) + raw, usedforsecurity=False).hexdigest()


def _unico(text: str, key: str) -> str:
    valores = scan_manifest_scalars(text, key)
    if not valores:
        raise GerarPinError(f"manifest: chave `{key}:` ausente")
    if len(set(valores)) != 1:
        raise GerarPinError(f"manifest: chave `{key}:` ambigua {sorted(set(valores))}")
    return valores[0]


def montar_bloco(
    raw: bytes,
    *,
    manifest_commit_sha: str,
    verified_by: str,
    verified_at_utc: str,
    spec: AdditiveManifest = V1_1,
) -> dict[str, Any]:
    """Monta o bloco `spec.lock_key` a partir dos bytes publicados. Nao toca disco nem rede."""
    if _GIT_SHA_RE.match(manifest_commit_sha) is None:
        raise GerarPinError(f"commit do manifest invalido (40 hex esperados): {manifest_commit_sha!r}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GerarPinError(f"manifest nao e' UTF-8: {exc}") from exc
    if "SET-AT-PUBLICATION" in text:
        raise GerarPinError("manifest ainda tem placeholder <SET-AT-PUBLICATION...>: nao foi publicado")

    status = _unico(text, "status")
    if status != FROZEN_STATUS:
        raise GerarPinError(f"manifest status={status!r}: so se pina manifest {FROZEN_STATUS}")
    if _unico(text, "manifest_version") != spec.manifest_version:
        raise GerarPinError(f"manifest_version precisa ser {spec.manifest_version}")

    dry_run_id = _unico(text, "dry_run_id")
    tail = _RUN_TAIL_RE.search(dry_run_id)
    if tail is None:
        raise GerarPinError(f"dry_run_id sem id numerico no fim: {dry_run_id!r}")

    pares = scan_manifest_path_digests(text)
    artefatos: list[dict[str, str]] = []
    for caminho in spec.artifact_paths:
        digest = pares.get(caminho)
        if digest is None:
            raise GerarPinError(f"manifest sem digest para {caminho}")
        artefatos.append({"path": caminho, "sha256": digest})

    return {
        "provenance": {
            "amh_commit_sha": _unico(text, "amh_commit_sha"),
            "amh_manifest_commit_sha": manifest_commit_sha,
            "evidence_id": _unico(text, "evidence_id"),
            "status": status,
        },
        "manifest_pin": {
            "byte_size": len(raw),
            "git_blob_sha": git_blob_sha(raw),
            "path": spec.manifest_path,
            "prepublication_sha256": _unico(text, "prepublication_manifest_sha256"),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "compatibility_report": {
            "dry_run_id": dry_run_id,
            "dry_run_run_id": tail.group(1),
            "evidence_artifact_id": _unico(text, "evidence_artifact_id"),
            "provider_contract_tests": _unico(text, "provider_contract_tests"),
            "result": _unico(text, "result"),
        },
        "publication": {
            "environment": _unico(text, "environment"),
            "publication_run_id": _unico(text, "publication_run_id"),
            "published_at_utc": _unico(text, "published_at_utc"),
        },
        "artifacts": artefatos,
        "xrg3_verification": {
            "verification_method": [
                f"manifest {spec.version} + digests recalculados pelo script scripts/ci/gerar_pin_v1_1.py",
                "sha256, git blob sha e tamanho calculados sobre os bytes publicados",
                f"{len(spec.artifact_paths)} digests OpenAPI conferidos contra o manifest publicado",
            ],
            "verified_at_utc": verified_at_utc,
            "verified_by": verified_by,
        },
    }


def _gh(args: Sequence[str], runner: Callable[..., Any] = subprocess.run) -> bytes:
    try:
        done = runner(["gh", "api", *args], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detalhe = getattr(exc, "stderr", b"") or b""
        raise GerarPinError(
            f"`gh api {' '.join(args)}` falhou: {detalhe.decode('utf-8', 'replace') or exc}"
        ) from exc
    return bytes(done.stdout)


def baixar_do_github(
    repo: str, ref: str, runner: Callable[..., Any] = subprocess.run, *, spec: AdditiveManifest = V1_1
) -> tuple[bytes, str]:
    """Bytes crus do manifest `spec` no commit `ref` + o commit resolvido (40 hex)."""
    commit = _gh([f"repos/{repo}/commits/{ref}", "--jq", ".sha"], runner).decode().strip()
    raw = _gh(
        [
            f"repos/{repo}/contents/{spec.manifest_path}?ref={commit}",
            "-H",
            "Accept: application/vnd.github.raw+json",
        ],
        runner,
    )
    meta = json.loads(_gh([f"repos/{repo}/contents/{spec.manifest_path}?ref={commit}"], runner))
    if meta.get("sha") != git_blob_sha(raw):
        raise GerarPinError(
            f"blob sha informado pelo GitHub ({meta.get('sha')}) difere do calculado ({git_blob_sha(raw)})"
        )
    return raw, commit


def _detectar_eol(raw: bytes) -> str:
    return "\r\n" if b"\r\n" in raw else "\n"


def gravar_no_lock(lock_path: Path, bloco: dict[str, Any], *, spec: AdditiveManifest = V1_1) -> None:
    """Insere `spec.lock_key` no lock (so se ausente) e so grava se o verificador aceitar."""
    raw = lock_path.read_bytes()
    lock = json.loads(raw.decode("utf-8"))
    if spec.lock_key in lock:
        raise GerarPinError(f"o lock ja tem `{spec.lock_key}`: pin imutavel, nao sobrescrevo")
    lock[spec.lock_key] = bloco
    violacoes = check_manifest_additive(lock, spec)
    if violacoes:
        raise GerarPinError(
            "bloco recusado pelo verificador:\n" + "\n".join(f"  - {v.render()}" for v in violacoes)
        )
    texto = json.dumps(lock, indent=2, ensure_ascii=False) + "\n"
    eol = _detectar_eol(raw)
    lock_path.write_bytes(texto.replace("\n", eol).encode("utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gerar_pin_v1_1", description=__doc__.split("\n\n")[0])
    origem = parser.add_mutually_exclusive_group(required=True)
    origem.add_argument("--manifest", help="Arquivo local com os bytes publicados do manifest v1.1.")
    origem.add_argument("--gh-ref", help="Commit/ref da AMH de onde baixar o manifest (via `gh api`).")
    parser.add_argument(
        "--manifest-commit-sha", help="Commit AMH que contem o manifest (40 hex). Obrigatorio com --manifest."
    )
    parser.add_argument("--amh-repo", default=DEFAULT_AMH_REPO)
    parser.add_argument("--verified-by", required=True, help="Quem fez a verificacao XRG-3 independente.")
    parser.add_argument("--verified-at-utc", default=None, help="ISO-8601 UTC (padrao: agora).")
    parser.add_argument("--lock", default=DEFAULT_LOCK_PATH)
    parser.add_argument("--write", action="store_true", help="Grava o bloco no lock (padrao: so imprime).")
    parser.add_argument(
        "--manifest-version",
        choices=tuple(ADDITIVE_BY_VERSION),
        default=V1_1.version,
        help="Qual manifest aditivo pinar (padrao: v1.1).",
    )
    args = parser.parse_args(argv)
    spec = ADDITIVE_BY_VERSION[args.manifest_version]

    try:
        if args.manifest is not None:
            if not args.manifest_commit_sha:
                raise GerarPinError("--manifest exige --manifest-commit-sha")
            raw = Path(args.manifest).read_bytes()
            commit = args.manifest_commit_sha
        else:
            raw, commit = baixar_do_github(args.amh_repo, args.gh_ref, spec=spec)
        verified_at = args.verified_at_utc or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        bloco = montar_bloco(
            raw,
            manifest_commit_sha=commit,
            verified_by=args.verified_by,
            verified_at_utc=verified_at,
            spec=spec,
        )
        if args.write:
            lock_path = Path(args.lock)
            if not lock_path.is_absolute():
                lock_path = REPO_ROOT / lock_path
            gravar_no_lock(lock_path, bloco, spec=spec)
            print(f"[gerar-pin-v1-1] bloco gravado em {lock_path}", file=sys.stderr)
        print(json.dumps({spec.lock_key: bloco}, indent=2, ensure_ascii=False))
    except (GerarPinError, OSError) as exc:
        print(f"[gerar-pin-v1-1] ERRO: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
