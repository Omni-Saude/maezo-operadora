"""Comandos do APROVADOR HUMANO. Rodam na maquina dele, com a raiz dele. Nenhum agente os roda.

Regra D-F (plano `portal-autoridade-nativa-dev`, §2; ADR-0060): o par Ed25519
`installation-root` nasce aqui e a chave privada nunca sai da maquina do aprovador. E ele quem
assina a designacao que LEU — este modulo mostra cada entrada e so assina quando o aprovador
digita de volta (`--confirm-digest`) o digest do conteudo exibido. Sem esse digest ele imprime a
revisao e sai sem assinar.

Comandos (``python -m tools.staff_materials.approver <comando>``):

* ``root-keygen --out DIR``  gera a raiz num diretorio NOVO 0700, fora do repositorio:
  `installation-root-key.pem` (0400, CIFRADA por senha por padrao; em claro so com
  `--no-encrypt`) e `installation-root.der` (a publica, que vai para o pacote). Imprime
  `root_key_sha256`.
* ``sign-designation --designation F --root-key K [--confirm-digest H] --expires-at T --out P``
  assina a designacao e grava `installation-proof.json`; em seguida reverifica com o mesmo
  verificador do portal (`InstalledStaffAuthority.verify`).
* ``sign-admission --record F --root-key K [--confirm-digest H] --out P``  assina o registro
  `portal-read-admission.v1` da T1.7 no dominio separado
  ``"maezo/portal-read-admission/v1\\0" || JCS(registro)`` (plano §3.1).

Este modulo pode importar o de engenharia; o contrario e proibido e testado.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import re
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from maezo.gateway.external_cases.models import digest, instant, now_utc, parse, timestamp
from maezo.gateway.staff_cases.authority import InstalledStaffAuthority, fingerprint
from maezo.gateway.staff_cases.models import Designation, Proof
from maezo.portal.engine.profile import canonicalize, strict_loads

from .generate import REPO
from .secure_io import PRIVATE, PUBLIC, MaterialError, new_private_directory, write_new

ADMISSION_DOMAIN = b"maezo/portal-read-admission/v1\x00"
MAX_WINDOW = timedelta(days=14)  # N2 (dono, 23/09/2026): vale para a designacao e para a admissao Q2


class ReviewRequiredError(MaterialError):
    """O aprovador ainda nao confirmou o digest do que leu. Nada foi assinado."""


def root_keygen(out: Path, *, passphrase: bytes | None, plaintext: bool = False) -> str:
    """Cifra a chave raiz por padrao. Em claro so com `plaintext=True` explicito (`--no-encrypt`)."""
    if passphrase is not None and plaintext:
        raise MaterialError("senha e --no-encrypt sao excludentes")
    if passphrase is None and not plaintext:
        raise MaterialError("a raiz sai cifrada por padrao: informe a senha ou use --no-encrypt")
    if passphrase is not None and len(passphrase) < 12:
        raise MaterialError("senha da raiz curta demais (minimo 12 bytes)")
    directory = new_private_directory(out, forbidden=(REPO,))
    key = Ed25519PrivateKey.generate()
    encryption: serialization.KeySerializationEncryption = (
        serialization.NoEncryption()
        if passphrase is None
        else serialization.BestAvailableEncryption(passphrase)
    )
    write_new(
        directory / "installation-root-key.pem",
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption),
        PRIVATE,
    )
    write_new(
        directory / "installation-root.der",
        key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
        PUBLIC,
    )
    return fingerprint(key.public_key())


def load_root(path: Path, passphrase: bytes | None = None) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=passphrase)
    except (TypeError, ValueError):
        raise MaterialError("nao foi possivel abrir a chave raiz (senha ausente ou errada)") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise MaterialError("a raiz de instalacao e Ed25519")
    return key


ESCALATION = "staff_escalation.v1"


def review_designation(raw: bytes) -> tuple[Designation, str, list[str]]:
    """O texto que o aprovador le antes de assinar. Nenhum campo de chave privada existe aqui."""
    try:
        designation = parse(Designation, raw)
    except ValueError:
        raise MaterialError("designacao fora do perfil fechado staff-case-designation.v1") from None
    # D-M.2/.4: o emissor e o leitor do portal sao designados para `staff_escalation.v1`. Sem ela o
    # engine nunca aceitaria a decisao nova, e a fila sairia sem guia/motivo/prazo: recusar aqui.
    for entry in designation.entries:
        if entry.role in {"case_issuer", "read_requester"} and ESCALATION not in entry.projections:
            raise MaterialError(f"a entrada {entry.role} nao autoriza a projecao {ESCALATION} (D-M)")
    lines = [
        f"designation_ref={designation.designation_ref} revision={designation.designation_revision}"
        f" (anterior {designation.expected_previous_revision}) state={designation.state}",
        f"scope={canonicalize(designation.scope.wire()).decode()}",
        f"janela={designation.issued_at} .. {designation.valid_until}",
    ]
    for entry in designation.entries:
        lines.append(
            f"- {entry.role}: key={entry.key_fingerprint} login={entry.login_role}"
            f" source={entry.source_namespace}/{entry.source_ref} tls_spki={entry.certificate_spki}"
            f" purposes={list(entry.purposes)} projections={list(entry.projections)}"
            f" operations={list(entry.operations)} janela={entry.not_before} .. {entry.valid_until}"
        )
    return designation, digest(designation.wire()), lines


def _confirm(expected: str, confirmed: str | None) -> None:
    if confirmed is None:
        raise ReviewRequiredError("revise o conteudo acima e repita com --confirm-digest <digest exibido>")
    if confirmed != expected:
        raise MaterialError("o digest confirmado nao e o do conteudo exibido; nada foi assinado")


def sign_designation(
    raw: bytes,
    root: Ed25519PrivateKey,
    *,
    confirm_digest: str | None,
    expires_at: datetime,
    now: datetime | None = None,
) -> bytes:
    designation, designation_digest, _ = review_designation(raw)
    _confirm(designation_digest, confirm_digest)
    now = now_utc() if now is None else now
    issued = timestamp(instant(now))
    if (
        not issued < expires_at
        or expires_at > timestamp(designation.valid_until)
        or expires_at - issued > MAX_WINDOW
        or designation.state != "active"
    ):
        raise MaterialError("validade da prova fora da designacao ou maior que 14 dias")
    proof: dict[str, Any] = dict(
        schema="staff-case-proof.v1",
        purpose="installation",
        algorithm="Ed25519",
        key_fingerprint=fingerprint(root.public_key()),
        issued_at=instant(issued),
        expires_at=instant(expires_at),
        statement_digest=designation_digest,
    )
    proof["signature"] = base64.b64encode(root.sign(canonicalize(proof))).decode("ascii")
    signed = canonicalize(proof)
    # Reverifica com o verificador do portal. Se a designacao so vale no futuro, confere no
    # primeiro instante em que ela vale (a prova nao pode nascer antes dele de outro jeito).
    try:
        InstalledStaffAuthority.verify(
            designation_bytes=raw,
            installation_proof=parse(Proof, signed),
            expected_digest=designation_digest,
            expected_scope=designation.scope,
            root=root.public_key(),
            revoked_fingerprints=frozenset(),
            now=max(issued, timestamp(designation.issued_at)),
        )
    except ValueError:
        raise MaterialError("a prova assinada nao passa no verificador do portal") from None
    return signed


ADMISSION_KEYS = frozenset(
    {
        "schema",
        "admission_ref",
        "admission_revision",
        "scope",
        "engine_name",
        "database_incarnation",
        "read_deployment_ref",
        "read_deployment_digest",
        "trust_configuration_digest",
        "purposes",
        "code_digests",
        "continuity_keys",
        "catalog",
        "publishers",
        "statement_timeout_seconds",
        "observation_seconds",
        "not_before",
        "valid_until",
    }
)
ADMISSION_PURPOSES = frozenset({"portal-task-read", "portal-read-publication"})
ADMISSION_SOURCE_KINDS = frozenset({"membership", "catalog-designate"})
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}")
_HASH = re.compile(r"[0-9a-f]{64}")
_DECIMAL = re.compile(r"0|[1-9][0-9]{0,17}")
_INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z")


def _closed(value: Any, keys: set[str] | frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError
    return value


def _ref(value: Any) -> str:
    if type(value) is not str or not _REF.fullmatch(value):
        raise ValueError
    return value


def _hash(value: Any) -> str:
    if type(value) is not str or not _HASH.fullmatch(value):
        raise ValueError
    return value


def _decimal(value: Any, low: int, high: int) -> int:
    if type(value) is not str or not _DECIMAL.fullmatch(value) or not low <= int(value) <= high:
        raise ValueError
    return int(value)


def _instant(value: Any) -> datetime:
    if type(value) is not str or not _INSTANT.fullmatch(value) or instant(timestamp(value)) != value:
        raise ValueError
    return timestamp(value)


def _admission_shape(value: Any) -> None:
    """O shape FECHADO do `portal-read-admission.v1`, espelho de `AdmissionRecord.java` (T1.7a).

    Qualquer registro que passe aqui o provedor le; qualquer um que o provedor recusaria e
    recusado ANTES de o aprovador assinar (F6 do C1: a versao anterior exigia o Scope de 4 campos
    e o provedor exige ``{tenant, environment, workload_ref}``).
    """
    record = _closed(value, ADMISSION_KEYS)
    if record["schema"] != "portal-read-admission.v1":
        raise ValueError
    _ref(record["admission_ref"])
    _decimal(record["admission_revision"], 1, 2**63 - 2)
    for key in ("tenant", "environment", "workload_ref"):
        _ref(_closed(record["scope"], {"tenant", "environment", "workload_ref"})[key])
    for key in ("engine_name", "database_incarnation", "read_deployment_ref"):
        _ref(record[key])
    _hash(record["read_deployment_digest"])
    _hash(record["trust_configuration_digest"])
    purposes = record["purposes"]
    if (
        type(purposes) is not list
        or not purposes
        or len(set(purposes)) != len(purposes)
        or not all(type(p) is str and p in ADMISSION_PURPOSES for p in purposes)
    ):
        raise ValueError
    code = _closed(record["code_digests"], {"engine", "provider"})
    _hash(code["engine"])
    _hash(code["provider"])
    keys = record["continuity_keys"]
    if type(keys) is not list or not keys:
        raise ValueError
    key_ids, commitments = set(), set()
    for item in keys:
        key = _closed(item, {"key_id", "generation", "commitment", "not_before", "not_after"})
        key_ids.add(_ref(key["key_id"]))
        _decimal(key["generation"], 0, 2**63 - 2)
        commitments.add(_hash(key["commitment"]))
        if not _instant(key["not_before"]) < _instant(key["not_after"]):
            raise ValueError
    if len(key_ids) != len(keys) or len(commitments) != len(keys):
        raise ValueError
    catalog = _closed(record["catalog"], {"catalog_ref", "publisher_ref", "catalog_digest"})
    _ref(catalog["catalog_ref"])
    _ref(catalog["publisher_ref"])
    _hash(catalog["catalog_digest"])
    publishers = record["publishers"]
    if type(publishers) is not list:
        raise ValueError
    kinds = set()
    for item in publishers:
        publisher = _closed(item, {"kind", "publisher_ref", "source_ref_prefix"})
        if publisher["kind"] not in ADMISSION_SOURCE_KINDS or publisher["kind"] in kinds:
            raise ValueError
        kinds.add(publisher["kind"])
        _ref(publisher["publisher_ref"])
        # O prefixo termina num separador: `...:amh:` nunca admite `...:amhx:...`.
        if not _ref(publisher["source_ref_prefix"]).endswith((":", "/")):
            raise ValueError
    _decimal(record["statement_timeout_seconds"], 1, 10)
    _decimal(record["observation_seconds"], 60, 900)
    start, end = _instant(record["not_before"]), _instant(record["valid_until"])
    if not start < end or end - start > MAX_WINDOW:
        raise ValueError


def review_admission(raw: bytes) -> tuple[dict[str, Any], str, list[str]]:
    """Confere o registro `portal-read-admission.v1` contra o shape fechado da T1.7a.

    O que passa e EXIBIDO campo a campo para o aprovador conferir contra as fontes da §4.
    """
    try:
        value = strict_loads(raw)
    except ValueError:
        raise MaterialError("o registro de admissao nao e JSON do perfil sem numeros") from None
    if type(value) is not dict or canonicalize(value) != raw:
        raise MaterialError("o registro de admissao precisa estar em JCS canonico")
    try:
        _admission_shape(value)
    except (KeyError, TypeError, ValueError):
        raise MaterialError("registro de admissao fora do shape fechado da T1.7a (AdmissionRecord)") from None
    lines = [f"{key}={canonicalize(value[key]).decode()}" for key in sorted(value)]
    return value, digest(value), lines


def sign_admission(raw: bytes, root: Ed25519PrivateKey, *, confirm_digest: str | None) -> bytes:
    _, record_digest, _ = review_admission(raw)
    _confirm(record_digest, confirm_digest)
    signature = root.sign(ADMISSION_DOMAIN + raw)
    root.public_key().verify(signature, ADMISSION_DOMAIN + raw)
    return base64.b64encode(signature)


def _passphrase(path: Path | None) -> bytes | None:
    return path.read_bytes().rstrip(b"\r\n") if path is not None else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.staff_materials.approver", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("root-keygen")
    keygen.add_argument("--out", type=Path, required=True)
    keygen.add_argument(
        "--no-encrypt", action="store_true", help="grava a chave raiz EM CLARO (so com decisao explicita)"
    )
    for name in ("sign-designation", "sign-admission"):
        command = commands.add_parser(name)
        command.add_argument("--root-key", type=Path, required=True)
        command.add_argument("--passphrase-file", type=Path)
        command.add_argument("--confirm-digest")
        command.add_argument("--out", type=Path, required=True)
        if name == "sign-designation":
            command.add_argument("--designation", type=Path, required=True)
            command.add_argument("--expires-at", required=True, help="AAAA-MM-DDTHH:MM:SS.ffffffZ")
        else:
            command.add_argument("--record", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "root-keygen":
            secret = None if args.no_encrypt else getpass.getpass("senha da raiz: ").encode()
            if secret is not None and getpass.getpass("repita a senha: ").encode() != secret:
                raise MaterialError("as senhas nao conferem")
            print(f"root_key_sha256={root_keygen(args.out, passphrase=secret, plaintext=args.no_encrypt)}")
            return 0
        root = load_root(args.root_key, _passphrase(args.passphrase_file))
        if args.out.exists():
            raise MaterialError("o arquivo de saida precisa ser NOVO")
        print(f"root_key_sha256={fingerprint(root.public_key())}")
        if args.command == "sign-designation":
            raw = args.designation.read_bytes()
            _, shown, lines = review_designation(raw)
            print("\n".join(lines))
            print(f"designation_sha256={shown}")
            signed = sign_designation(
                raw, root, confirm_digest=args.confirm_digest, expires_at=timestamp(args.expires_at)
            )
        else:
            raw = args.record.read_bytes()
            _, shown, lines = review_admission(raw)
            print("\n".join(lines))
            print(f"admission_sha256={shown}")
            signed = sign_admission(raw, root, confirm_digest=args.confirm_digest)
        write_new(args.out, signed, PUBLIC)
        print(f"assinado: {args.out}")
        return 0
    except ReviewRequiredError as review:
        print(str(review), file=sys.stderr)
        return 2
    except MaterialError as failure:
        print(f"recusado: {failure}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
