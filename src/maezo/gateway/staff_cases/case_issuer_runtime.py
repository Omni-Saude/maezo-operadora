"""Composicao do emissor de casos staff (T1.6): uma rodada por execucao, fail-closed.

`python -m maezo.gateway.staff_cases` le `MAEZO_STAFF_CASE_ISSUER_FILE`
(`staff-case-issuer-composition.v1`, JSON fechado) e os arquivos irmaos que ele nomeia (chaves,
designacao, DSNs). Nao ha default de nada: sem arquivo, campo desconhecido, arquivo irmao ausente,
tenant ausente ou tenant diferente do escopo, o processo sai com codigo 2 sem tocar banco nem engine.

Decisoes do plano `portal-autoridade-nativa-dev.md`:

* D-H.5: o tenant (`amh` em dev) e declarado explicitamente na composicao; o codigo nao tem default;
* D-H.6: `policy_ref = staff-escalation-routing@d{designation_revision}`, derivado da designacao
  instalada; o ledger guarda uma linha por `policy_ref` (designacao nova = revisao recomecando);
* D-H.1/2/3: `AuthClaimAnchor`, `IssuerWitness` e `PostgresIssuerLedger` (`case_issuer_sources`).

O log e uma linha JSON por rodada, so com contadores e o digest da composicao: nunca guia, sujeito,
DSN ou chave.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import uuid
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from maezo.gateway.external_cases.models import Scope, now_utc
from maezo.portal.engine.profile import strict_loads

from .authority import InstalledStaffAuthority, fingerprint
from .case_issuer import StaffCaseIssuer, StaffCaseIssuerJob, StaffCasePolicy, policy_ref_for
from .case_issuer_sources import (
    AuthClaimAnchor,
    EngineEscalationSource,
    IssuerWitness,
    PostgresIssuerLedger,
    PostgresStaffGranteeSource,
)
from .models import Proof
from .postgres import NativeMembershipSource, RelationPin
from .production_config import is_native_schema
from .publisher import StaffNativeClient, StaffSigner, StaffWitnessSource

ENV = "MAEZO_STAFF_CASE_ISSUER_FILE"
ISSUER_LOGIN = "maezo_native_case_issuer"
WITNESS_LOGIN = "maezo_native_issuer_witness"
Sibling = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
Name = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z_][a-z0-9_]{0,62}$")]


class IssuerCompositionError(RuntimeError):
    """Recusa de composicao. Nunca carrega valor de configuracao."""

    def __init__(self) -> None:
        super().__init__("staff_case_issuer_composition_refused")


class _Closed(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class _Pin(_Closed):
    oid: Annotated[int, Field(ge=1)]
    owner: Name


class _Native(_Closed):
    origin: Annotated[str, StringConstraints(strict=True, pattern=r"^https://[^\s/?#@]+$")]
    ca_file: Sibling
    client_certificate_file: Sibling
    client_key_file: Sibling
    importer_key_file: Sibling
    server_spki_sha256: Digest
    configuration_digest: Digest


class IssuerComposition(_Closed):
    schema_: Literal["staff-case-issuer-composition.v1"] = Field(alias="schema")
    #: D-H.5: explicito, sem default. `amh` em dev.
    tenant: Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_-]{0,62}$")]
    environment: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64)]
    engine_name: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64)]
    database_incarnation: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]
    native_schema: Name
    membership_schema: Name
    designation_file: Sibling
    installation_proof_file: Sibling
    root_public_key_file: Sibling
    designation_digest: Digest
    revoked_fingerprints: tuple[Digest, ...]
    case_issuer_key_file: Sibling
    witness_key_file: Sibling
    issuer_dsn_file: Sibling
    witness_dsn_file: Sibling
    relation_pins: dict[Literal["mzo_portal_read_membership", "mzo_human_principal"], _Pin]
    engine_rest_url: Annotated[str, StringConstraints(strict=True, pattern=r"^https?://[^\s?#@]+$")]
    native: _Native
    seconds: Annotated[int, Field(ge=1, le=10)]

    @property
    def scope(self) -> Scope:
        return Scope(
            tenant=self.tenant,
            environment=self.environment,
            engine_name=self.engine_name,
            database_incarnation=self.database_incarnation,
        )


@dataclass(frozen=True)
class LoadedComposition:
    composition: IssuerComposition
    digest: str
    directory: Path

    def read(self, name: str) -> bytes:
        path = self.directory / name
        if path.parent != self.directory or not path.is_file():
            raise IssuerCompositionError()
        return path.read_bytes()


def load_composition(environ: dict[str, str] | None = None) -> LoadedComposition:
    env = os.environ if environ is None else environ
    try:
        raw_path = env[ENV]
        path = Path(raw_path)
        if not path.is_absolute() or not path.is_file():
            raise IssuerCompositionError()
        raw = path.read_bytes()
        if not 0 < len(raw) <= 65536:
            raise IssuerCompositionError()
        json.loads(raw, object_pairs_hook=_unique)  # chave duplicada recusa antes do modelo
        composition = IssuerComposition.model_validate_json(raw)
        if (
            set(composition.relation_pins) != set(NativeMembershipSource.TABLES)
            or not is_native_schema(composition.native_schema)
            or composition.membership_schema != composition.tenant
        ):
            raise IssuerCompositionError()
        loaded = LoadedComposition(composition, hashlib.sha256(raw).hexdigest(), path.parent.resolve())
        for name in _siblings(composition):
            loaded.read(name)
        return loaded
    except IssuerCompositionError:
        raise
    except (KeyError, OSError, ValueError, ValidationError):
        raise IssuerCompositionError() from None


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate key")
    return value


def _siblings(c: IssuerComposition) -> tuple[str, ...]:
    n = c.native
    return (
        c.designation_file,
        c.installation_proof_file,
        c.root_public_key_file,
        c.case_issuer_key_file,
        c.witness_key_file,
        c.issuer_dsn_file,
        c.witness_dsn_file,
        n.ca_file,
        n.client_certificate_file,
        n.client_key_file,
        n.importer_key_file,
    )


def _private(raw: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(raw, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise IssuerCompositionError()
    return key


def _url(raw: bytes, login: str) -> Any:
    url = make_url(raw.decode("ascii").strip())
    if url.drivername != "postgresql+asyncpg" or url.username != login or url.query:
        raise IssuerCompositionError()
    return url


def _engine(url: Any, seconds: int) -> AsyncEngine:
    return create_async_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=seconds,
        connect_args={"timeout": seconds, "command_timeout": seconds},
    )


@dataclass
class RoundReport:
    composition_digest: str
    policy_ref: str
    live: int
    anchored: int
    unanchored: int
    refused: int
    foreign: int
    reasons: dict[str, int]
    published: int
    grants: int
    revokes: int
    checkpoints: int


async def run_round(loaded: LoadedComposition) -> RoundReport:
    c = loaded.composition
    scope = c.scope
    try:
        root = serialization.load_der_public_key(loaded.read(c.root_public_key_file))
        if not isinstance(root, Ed25519PublicKey):
            raise IssuerCompositionError()
        designation_bytes = loaded.read(c.designation_file)
        authority = InstalledStaffAuthority.verify(
            designation_bytes=designation_bytes,
            installation_proof=Proof.model_validate(strict_loads(loaded.read(c.installation_proof_file))),
            expected_digest=c.designation_digest,
            expected_scope=scope,
            root=root,
            revoked_fingerprints=frozenset(c.revoked_fingerprints),
            now=now_utc(),
        )
        policy_ref = policy_ref_for(authority.designation.designation_revision)
        issuer_signer = StaffSigner(authority, _private(loaded.read(c.case_issuer_key_file)), "case_issuer")
        entry = authority.entries.get(fingerprint(issuer_signer.key.public_key()))
        if entry is None:
            raise IssuerCompositionError()
        issuer = StaffCaseIssuer(
            signer=issuer_signer,
            scope=scope,
            source_ref=entry.source_ref,
            policy=StaffCasePolicy(policy_ref=policy_ref, policy_revision=1),
        )
        witness_signer = StaffSigner(
            authority, _private(loaded.read(c.witness_key_file)), "identity_verifier"
        )
        importer = StaffSigner(
            authority, _private(loaded.read(c.native.importer_key_file)), "publication_importer"
        )
        issuer_url = _url(loaded.read(c.issuer_dsn_file), ISSUER_LOGIN)
        witness_url = _url(loaded.read(c.witness_dsn_file), WITNESS_LOGIN)
    except IssuerCompositionError:
        raise
    except Exception:
        raise IssuerCompositionError() from None

    async with AsyncExitStack() as stack:
        issuer_engine = _engine(issuer_url, c.seconds)
        stack.push_async_callback(issuer_engine.dispose)
        witness_engine = _engine(witness_url, c.seconds)
        stack.push_async_callback(witness_engine.dispose)
        rest = httpx.AsyncClient(
            base_url=c.engine_rest_url, timeout=c.seconds, trust_env=False, follow_redirects=False
        )
        stack.push_async_callback(rest.aclose)
        native = StaffNativeClient(
            origin=c.native.origin,
            ca_file=loaded.directory / c.native.ca_file,
            certificate_file=loaded.directory / c.native.client_certificate_file,
            private_key_file=loaded.directory / c.native.client_key_file,
            server_spki_sha256=c.native.server_spki_sha256,
            signer=importer,
            result_authority=authority,
            configuration_digest=c.native.configuration_digest,
            seconds=c.seconds,
        )
        stack.push_async_callback(native.close)
        membership = NativeMembershipSource(
            witness_engine,
            scope,
            WITNESS_LOGIN,
            {name: RelationPin(pin.oid, pin.owner) for name, pin in c.relation_pins.items()},
            native_schema=c.native_schema,
            seconds=c.seconds,
        )
        anchor = AuthClaimAnchor(
            rest, issuer_engine, tenant=c.tenant, native_schema=c.native_schema, seconds=c.seconds
        )
        escalations = EngineEscalationSource(rest, tenant=c.tenant, anchor=anchor)
        job = StaffCaseIssuerJob(
            issuer=issuer,
            escalations=escalations,
            grantees=PostgresStaffGranteeSource(
                issuer_engine, tenant=c.tenant, schema=c.membership_schema, seconds=c.seconds
            ),
            witness=IssuerWitness(StaffWitnessSource(membership, witness_signer), run_id=uuid.uuid4().hex),
            ledger=PostgresIssuerLedger(
                issuer_engine,
                scope=scope,
                policy_ref=policy_ref,
                login=ISSUER_LOGIN,
                native_schema=c.native_schema,
                seconds=c.seconds,
            ),
            publish=native.publish,
        )
        result = await job.run_once()
        report = escalations.report
        return RoundReport(
            composition_digest=loaded.digest,
            policy_ref=policy_ref,
            live=report.live,
            anchored=report.anchored,
            unanchored=report.unanchored,
            refused=report.refused,
            foreign=report.foreign,
            reasons=dict(sorted(report.reasons.items())),
            **asdict(result),
        )


_REASON = re.compile(r"^staff_[a-z_]+$")


def main(environ: dict[str, str] | None = None) -> int:
    """0 = rodada concluida; 2 = composicao recusada (nada tocado); 1 = rodada falhou (fail-closed)."""
    try:
        loaded = load_composition(environ)
    except IssuerCompositionError:
        print(json.dumps({"event": "staff_case_issuer_refused", "reason": "composition"}), flush=True)
        return 2
    try:
        report = asyncio.run(run_round(loaded))
    except IssuerCompositionError:
        print(
            json.dumps(
                {"event": "staff_case_issuer_refused", "reason": "composition", "digest": loaded.digest}
            ),
            flush=True,
        )
        return 2
    except Exception as exc:  # a mensagem das excecoes do emissor e um codigo fechado, sem dado
        message = str(exc)
        reason = message if _REASON.fullmatch(message) else type(exc).__name__
        print(
            json.dumps({"event": "staff_case_issuer_failed", "reason": reason, "digest": loaded.digest}),
            flush=True,
        )
        return 1
    print(json.dumps({"event": "staff_case_issuer_round", **asdict(report)}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
