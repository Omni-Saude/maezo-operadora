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
* ``sign-human-admission --record F --root-key K [--confirm-digest H] --out P``  assina o registro
  `ReadAdmission` do pacote humano e grava `human-read-admission.json`
  (`portal-human-read-admission.v1`: Ed25519 sobre o JCS do `record`, SEM dominio, exatamente o
  que `production_materials.verify_read_admission` confere), que o `human-bundle package` recebe.
  Sem `--passphrase-file`, a senha da raiz cifrada e pedida por `getpass`.
* ``sign-assignment-owner --record F --root-key K [--confirm-digest H] --out P``  assina o documento
  de dono do plano de atribuicao `staff-assignment-owner.v1` (D-O) no dominio separado
  ``"maezo/staff-assignment-owner/v1\\0" || JCS(documento)`` e grava o
  `staff-assignment-owner-proof.v1`; o SHA-256 desse arquivo e o digest do `owner_receipt`.

Este modulo pode importar o de engenharia; o contrario e proibido e testado.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
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
# H1 (D-N): `resource` so junto com o bloco `human` (e o bloco so junto com o publicador `resource`).
ADMISSION_SOURCE_KINDS = frozenset({"membership", "catalog-designate", "resource"})
HUMAN_CLASSIFICATION_KEYS = frozenset(
    {
        "classification_ref",
        "classification_digest",
        "policy_ref",
        "policy_digest",
        "projection",
        "fields_digest",
    }
)
HUMAN_ENTRY_KEYS = frozenset(
    {
        "process_definition_id",
        "task_definition_key",
        "classification",
        "identity_policy",
        "task_id_format",
        "candidate_groups",
        "user_candidates",
    }
)
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
    # `human` e a unica chave opcional (H1): o registro staff-only guarda o shape exato da T1.7a.
    optional = type(value) is dict and "human" in value
    record = _closed(value, ADMISSION_KEYS | {"human"} if optional else ADMISSION_KEYS)
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
    if ("human" in record) != ("resource" in kinds):
        raise ValueError
    if "human" in record:
        _human_shape(record["human"])
    _decimal(record["statement_timeout_seconds"], 1, 10)
    _decimal(record["observation_seconds"], 60, 900)
    start, end = _instant(record["not_before"]), _instant(record["valid_until"])
    if not start < end or end - start > MAX_WINDOW:
        raise ValueError


def _human_shape(value: Any) -> None:
    """Espelho de `HumanAdmission.java` (H1): o que o aprovador admite de cada tarefa humana."""
    entries = _closed(value, {"entries"})["entries"]
    if type(entries) is not list or not entries or len(entries) > 256:
        raise ValueError
    seen = set()
    for item in entries:
        entry = _closed(item, HUMAN_ENTRY_KEYS)
        key = (_ref(entry["process_definition_id"]), _ref(entry["task_definition_key"]))
        if key in seen:
            raise ValueError
        seen.add(key)
        classification = _closed(entry["classification"], HUMAN_CLASSIFICATION_KEYS)
        _ref(classification["classification_ref"])
        _hash(classification["classification_digest"])
        _ref(classification["policy_ref"])
        _hash(classification["policy_digest"])
        _hash(classification["fields_digest"])
        if classification["projection"] != "full_task_detail.v1":
            raise ValueError
        policy = _closed(entry["identity_policy"], {"artifact_ref", "digest"})
        _ref(policy["artifact_ref"])
        _hash(policy["digest"])
        if entry["task_id_format"] not in ("uuid", "decimal"):
            raise ValueError
        if entry["user_candidates"] not in ("refused", "native_principal"):
            raise ValueError
        groups = entry["candidate_groups"]
        if type(groups) is not list or not groups or len(groups) > 64 or len(set(groups)) != len(groups):
            raise ValueError
        for group in groups:
            _ref(group)


def review_catalog(record: dict[str, Any], catalog_raw: bytes) -> list[str]:
    """H4 (D-N): o catalogo COM tarefas humanas que a admissao nomeia, conferido entrada a entrada.

    O digest do registro so pina bytes; sem ler o catalogo o aprovador assinaria tarefas que nao
    viu. Exige: SHA-256 do artefato = `catalog.catalog_digest`; `catalog_ref`/`publisher_ref`
    iguais aos admitidos; cada entrada do catalogo com exatamente uma entrada `human` (e vice-versa)
    pela chave (`process_definition_id`, `task_definition_key`); `identity_policy` = a
    `opaque_task_id_policy` da entrada; a politica da classificacao = a `disclosure_policy`; os
    `candidate_groups` dentro do dominio de grupos da entrada. Um catalogo com tarefas sem o bloco
    `human` e recusado: as tarefas ficariam publicadas e nunca legiveis.
    """
    from maezo.gateway.human.read_profile import ReadCatalogArtifact, parse_model

    try:
        artifact = parse_model(ReadCatalogArtifact, strict_loads(catalog_raw))
    except Exception:
        raise MaterialError("catalogo fora do perfil fechado portal-read-catalog.v1") from None
    admitted = record["catalog"]
    if hashlib.sha256(catalog_raw).hexdigest() != admitted["catalog_digest"]:
        raise MaterialError("o catalogo nao e o que a admissao pina (catalog_digest)")
    if (artifact.catalog_ref, artifact.publisher_ref) != (admitted["catalog_ref"], admitted["publisher_ref"]):
        raise MaterialError("catalog_ref/publisher_ref do catalogo diferem dos admitidos")
    human = {
        (e["process_definition_id"], e["task_definition_key"]): e
        for e in record.get("human", {}).get("entries", [])
    }
    entries = {(e.process_definition_id, e.task_definition_key): e for e in artifact.entries}
    if set(human) != set(entries):
        raise MaterialError("cada tarefa do catalogo precisa de exatamente uma entrada `human` na admissao")
    lines = [f"catalogo={artifact.catalog_ref} entradas={len(entries)}"]
    for key in sorted(entries):
        entry, admitted_entry = entries[key], human[key]
        identity = admitted_entry["identity_policy"]
        classification = admitted_entry["classification"]
        if (identity["artifact_ref"], identity["digest"]) != (
            entry.opaque_task_id_policy.artifact_ref,
            entry.opaque_task_id_policy.digest,
        ):
            raise MaterialError(f"identity_policy de {key[1]} nao e a opaque_task_id_policy do catalogo")
        if (classification["policy_ref"], classification["policy_digest"]) != (
            entry.disclosure_policy.artifact_ref,
            entry.disclosure_policy.digest,
        ):
            raise MaterialError(
                f"a politica da classificacao de {key[1]} nao e a disclosure_policy do catalogo"
            )
        if not set(admitted_entry["candidate_groups"]) <= set(entry.group_domain.groups):
            raise MaterialError(f"candidate_groups de {key[1]} fora do dominio de grupos do catalogo")
        groups = list(entry.group_domain.groups)
        lines.append(
            f"- {key[0]}/{key[1]} form={entry.form_key}@{entry.form_version} grupos={groups}"
            f" admitidos={admitted_entry['candidate_groups']}"
            f" task_id_format={admitted_entry['task_id_format']}"
        )
    return lines


def review_admission(raw: bytes, catalog: bytes | None = None) -> tuple[dict[str, Any], str, list[str]]:
    """Confere o registro `portal-read-admission.v1` contra o shape fechado da T1.7a.

    O que passa e EXIBIDO campo a campo para o aprovador conferir contra as fontes da §4. Com o
    bloco `human` (tarefas, H1) o catalogo e obrigatorio e conferido por `review_catalog`.
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
    if "human" in value:
        if catalog is None:
            raise MaterialError("admissao com tarefas humanas: informe o catalogo (--catalog) para conferir")
        lines += review_catalog(value, catalog)
    elif catalog is not None:
        review_catalog(value, catalog)  # staff-only: o catalogo informado tambem tem de ser o pinado
    return value, digest(value), lines


def sign_admission(
    raw: bytes, root: Ed25519PrivateKey, *, confirm_digest: str | None, catalog: bytes | None = None
) -> bytes:
    _, record_digest, _ = review_admission(raw, catalog)
    _confirm(record_digest, confirm_digest)
    signature = root.sign(ADMISSION_DOMAIN + raw)
    root.public_key().verify(signature, ADMISSION_DOMAIN + raw)
    return base64.b64encode(signature)


HUMAN_ADMISSION_SCHEMA = "portal-human-read-admission.v1"


def review_human_admission(raw: bytes) -> tuple[dict[str, Any], str, list[str]]:
    """Confere o `ReadAdmission` (plano humano) no perfil do loader e o exibe campo a campo."""
    from maezo.gateway.human.read_credentials import ReadAdmission
    from maezo.gateway.human.read_profile import parse_model, wire

    try:
        value = strict_loads(raw)
        record = wire(parse_model(ReadAdmission, value))
    except Exception:
        raise MaterialError("registro fora do perfil fechado ReadAdmission (plano humano)") from None
    if canonicalize(record) != raw:
        raise MaterialError("o registro de admissao humana precisa estar em JCS canonico")
    start, end = _instant(value["observed_at"]), _instant(value["valid_until"])
    if not start < end or end - start > MAX_WINDOW:
        raise MaterialError("janela da admissao humana invalida ou maior que 14 dias")
    lines = [f"{key}={canonicalize(value[key]).decode()}" for key in sorted(value)]
    return record, digest(record), lines


def sign_human_admission(raw: bytes, root: Ed25519PrivateKey, *, confirm_digest: str | None) -> bytes:
    """Devolve o `human-read-admission.json` completo (documento, nao so a assinatura)."""
    record, record_digest, _ = review_human_admission(raw)
    _confirm(record_digest, confirm_digest)
    signature = root.sign(raw)
    root.public_key().verify(signature, raw)
    return canonicalize(
        {
            "schema": HUMAN_ADMISSION_SCHEMA,
            "record": record,
            "signature": base64.b64encode(signature).decode(),
        }
    )


def review_assignment_owner(raw: bytes) -> tuple[dict[str, Any], str, list[str]]:
    """Confere o `staff-assignment-owner.v1` (formato fechado, JCS) e o exibe campo a campo."""
    from .assignment_trust import parse_owner

    return parse_owner(raw)


def sign_assignment_owner(raw: bytes, root: Ed25519PrivateKey, *, confirm_digest: str | None) -> bytes:
    """Devolve o `staff-assignment-owner-proof.v1` completo; seu SHA-256 e o `owner_receipt` (D-O)."""
    from .assignment_trust import OWNER_DOMAIN, owner_proof

    document, document_digest, _ = review_assignment_owner(raw)
    _confirm(document_digest, confirm_digest)
    signature = root.sign(OWNER_DOMAIN + raw)
    root.public_key().verify(signature, OWNER_DOMAIN + raw)
    return owner_proof(document, signature)


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
    for name in ("sign-designation", "sign-admission", "sign-human-admission", "sign-assignment-owner"):
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
        if name == "sign-admission":
            command.add_argument(
                "--catalog", type=Path, help="portal-read-catalog.v1 (JCS) que a admissao pina"
            )
    args = parser.parse_args(argv)
    try:
        if args.command == "root-keygen":
            secret = None if args.no_encrypt else getpass.getpass("senha da raiz: ").encode()
            if secret is not None and getpass.getpass("repita a senha: ").encode() != secret:
                raise MaterialError("as senhas nao conferem")
            print(f"root_key_sha256={root_keygen(args.out, passphrase=secret, plaintext=args.no_encrypt)}")
            return 0
        secret = _passphrase(args.passphrase_file)
        if secret is None and args.command == "sign-human-admission":
            try:
                root = load_root(args.root_key)
            except MaterialError:
                root = load_root(args.root_key, getpass.getpass("senha da raiz: ").encode())
        else:
            root = load_root(args.root_key, secret)
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
        elif args.command == "sign-human-admission":
            raw = args.record.read_bytes()
            _, shown, lines = review_human_admission(raw)
            print("\n".join(lines))
            print(f"human_admission_sha256={shown}")
            signed = sign_human_admission(raw, root, confirm_digest=args.confirm_digest)
        elif args.command == "sign-assignment-owner":
            raw = args.record.read_bytes()
            _, shown, lines = review_assignment_owner(raw)
            print("\n".join(lines))
            print(f"assignment_owner_sha256={shown}")
            signed = sign_assignment_owner(raw, root, confirm_digest=args.confirm_digest)
            print(f"owner_receipt_digest={hashlib.sha256(signed).hexdigest()} (SHA-256 do arquivo assinado)")
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
