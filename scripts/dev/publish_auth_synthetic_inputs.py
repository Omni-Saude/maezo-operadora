#!/usr/bin/env python3
"""Publish LABELLED SYNTHETIC AUTH input heads through the real publication path.

WHAT THIS IS. A development seeder. It publishes the five `MZO_AUTH_INPUT_HEAD` kinds
(`actor`, `resource_authority`, `guide`, `start_facts`, `document_policy`) that the AUTH
journey reads, using the production publisher, the production transport and the
production journal — `AuthInputPublisher.publish` → `/v1/auth-input-publication`. Only
the *source* is synthetic: the rows come from a fixture file on disk, not from a real
operadora record, and every one of them carries
`SourceProvenance.publisher_ref == SYNTHETIC_PUBLISHER_REF`, which is the sole value the
portal will ever label `provenance_kind="synthetic"`.

WHAT THIS IS NOT. It is not, and must never become, a path to real AMH data. AMH
publishes no `resource_authority` today (AMH-FACT-MAP §3.1); obtaining real delegations
needs the cross-repo delegation/representation contract, which is an owner item
(`AMH-DELEGATION-CONTRACT-REQUEST.md`). Pointing this script at an AMH extract would
launder unattested facts into a head row that the gateway then treats as authority.

REFUSAL. The script refuses to run without an explicit synthetic-environment marker:
a dedicated material file named by `MAEZO_SYNTHETIC_AUTH_INPUTS` that declares the
marker string, the tenant and an explicit acknowledgement. The marker is the ONLY gate.
Nothing here reads `PortalSettings.mode`, so no deployment mode — `local-test` included —
can ever enable publication on its own, and `production` cannot silently disable a
misconfigured run either: absent marker, absent publication.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT / "src"))

from maezo.gateway.human.auth_profile import (  # noqa: E402
    PAYLOAD_TYPES,
    InputKind,
    InputPublication,
    PublicationReceipt,
    Scope,
)
from maezo.gateway.human.auth_publisher import (  # noqa: E402
    AuthInputPublisher,
    AuthPublicationSnapshot,
    PostgresAuthPublicationJournal,
)
from maezo.gateway.human.auth_transport import AuthUnavailableError  # noqa: E402
from maezo.gateway.human.read_profile import SourceProvenance, digest, parse_model  # noqa: E402
from maezo.gateway.human.read_publisher import SourceFreezeLease, SourceSnapshot  # noqa: E402
from maezo.gateway.intake.links import SYNTHETIC_PUBLISHER_REF  # noqa: E402
from maezo.portal.engine.profile import canonicalize  # noqa: E402

MARKER_VARIABLE = "MAEZO_SYNTHETIC_AUTH_INPUTS"
MARKER_STRING = "maezo-synthetic-auth-inputs"
# Publication order is fixed: an actor and its authority must exist before the facts that
# cite them, so a partially-failed run leaves a prefix that is still internally consistent.
KINDS: tuple[InputKind, ...] = ("actor", "resource_authority", "guide", "start_facts", "document_policy")


class SyntheticRefusalError(RuntimeError):
    """The synthetic seeder refused. No publication was attempted."""


@dataclass(frozen=True, slots=True)
class SyntheticMarker:
    """The dedicated material that authorises synthetic publication for one tenant."""

    path: Path
    tenant: str
    source_ref: str
    digest: str


def _readable_regular_file(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise SyntheticRefusalError(f"not a regular file: {path}")
    return path.read_bytes()


def require_marker(tenant: str, environ: dict[str, str] | None = None) -> SyntheticMarker:
    """Load the dedicated synthetic marker material, or refuse.

    Every condition is explicit and fail-closed. There is deliberately no inference from
    hostname, database name, deployment mode or the absence of production credentials.
    """
    env = os.environ if environ is None else environ
    location = env.get(MARKER_VARIABLE, "").strip()
    if not location:
        raise SyntheticRefusalError(
            f"{MARKER_VARIABLE} is unset: synthetic publication requires an explicit "
            "synthetic-environment marker file and is never inferred from the deployment mode"
        )
    path = Path(location)
    raw = _readable_regular_file(path)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise SyntheticRefusalError(f"marker is not valid JSON: {path}") from None
    if type(document) is not dict or document.get("marker") != MARKER_STRING:
        raise SyntheticRefusalError(f"marker string {MARKER_STRING!r} absent from {path}")
    if document.get("acknowledged") is not True:
        raise SyntheticRefusalError("marker does not carry an explicit acknowledgement")
    declared = document.get("tenant")
    if type(declared) is not str or declared != tenant:
        raise SyntheticRefusalError(f"marker authorises tenant {declared!r}, not {tenant!r}")
    source_ref = document.get("source_ref")
    if type(source_ref) is not str or not source_ref:
        raise SyntheticRefusalError("marker declares no synthetic source reference")
    return SyntheticMarker(path, tenant, source_ref, hashlib.sha256(raw).hexdigest())


@dataclass(frozen=True, slots=True)
class Fixture:
    """The labelled synthetic fixture: the declared source of every published row."""

    path: Path
    digest: str
    scope: Scope
    workload_ref: str
    revision: int
    valid_until: datetime
    inputs: dict[InputKind, tuple[str, int, dict[str, Any]]]


def load_fixture(path: Path, marker: SyntheticMarker) -> Fixture:
    raw = _readable_regular_file(path)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise SyntheticRefusalError(f"fixture is not valid JSON: {path}") from None
    if type(document) is not dict or document.get("synthetic") is not True:
        raise SyntheticRefusalError(f"fixture is not declared synthetic: {path}")
    try:
        scope = parse_model(Scope, document["scope"])
        valid_until = datetime.fromisoformat(document["valid_until"])
        revision = document["source_revision"]
        workload_ref = document["workload_ref"]
        entries = document["inputs"]
    except (KeyError, ValueError, AuthUnavailableError):
        raise SyntheticRefusalError(f"fixture is incomplete or malformed: {path}") from None
    if scope.tenant != marker.tenant:
        raise SyntheticRefusalError(f"fixture tenant {scope.tenant!r} is not the marked tenant")
    if valid_until.tzinfo is None or type(revision) is not int or type(workload_ref) is not str:
        raise SyntheticRefusalError("fixture header is malformed")
    if type(entries) is not list or [e.get("kind") for e in entries] != list(KINDS):
        raise SyntheticRefusalError(f"fixture must declare exactly, and in order, the kinds {list(KINDS)}")
    inputs: dict[InputKind, tuple[str, int, dict[str, Any]]] = {}
    for entry in entries:
        kind = entry["kind"]
        resource_ref, generation, payload = (
            entry.get("resource_ref"),
            entry.get("expected_generation", 0),
            entry.get("payload"),
        )
        if type(resource_ref) is not str or type(generation) is not int or type(payload) is not dict:
            raise SyntheticRefusalError(f"fixture entry {kind!r} is malformed")
        inputs[kind] = (resource_ref, generation, payload)
    return Fixture(path, hashlib.sha256(raw).hexdigest(), scope, workload_ref, revision, valid_until, inputs)


def publication_id(fixture: Fixture, kind: InputKind, resource_ref: str, payload_digest: str) -> str:
    """Deterministic per (tenant, kind, resource, payload, fixture): reruns are idempotent."""
    key = canonicalize(
        [fixture.scope.tenant, kind, resource_ref, payload_digest, fixture.digest, SYNTHETIC_PUBLISHER_REF]
    )
    return "synthetic-" + hashlib.sha256(key).hexdigest()


def build_snapshots(
    fixture: Fixture, marker: SyntheticMarker, now: datetime, live: Any = None
) -> tuple[AuthPublicationSnapshot, ...]:
    """Build one publication per kind. Payloads are parsed into their real closed models."""
    if now >= fixture.valid_until:
        raise SyntheticRefusalError("fixture validity window has already closed")
    snapshots: list[AuthPublicationSnapshot] = []
    for kind in KINDS:
        resource_ref, generation, raw_payload = fixture.inputs[kind]
        try:
            payload = parse_model(PAYLOAD_TYPES[kind], raw_payload)
        except Exception:
            raise SyntheticRefusalError(f"fixture payload for {kind!r} is not a valid {kind} model") from None
        payload_digest = digest(payload)
        source = SourceProvenance(
            publisher_ref=SYNTHETIC_PUBLISHER_REF,
            source_ref=marker.source_ref,
            source_revision=fixture.revision,
            source_digest=fixture.digest,
            receipt_ref=publication_id(fixture, kind, resource_ref, payload_digest),
            observed_at=now,
            valid_until=fixture.valid_until,
        )
        publication = InputPublication(
            schema="human-auth-input-publication.v1",
            scope=fixture.scope,
            workload_ref=fixture.workload_ref,
            publication_id=source.receipt_ref,
            kind=kind,
            resource_ref=resource_ref,
            expected_generation=generation,
            source=source,
            state="active",
            payload=payload,
            payload_digest=payload_digest,
            valid_until=fixture.valid_until,
        )
        body = canonicalize({"kind": kind, "resource_ref": resource_ref, "payload": raw_payload})

        def verify(raw: bytes, expected: bytes = body) -> None:
            if raw != expected:
                raise AuthUnavailableError()

        lease = SourceFreezeLease(
            provenance=source,
            verify=verify,
            live=_liveness(fixture, marker) if live is None else live,
            committed=lambda receipt: None,
            uncertain=lambda: None,
        )
        verify(body)
        snapshots.append(AuthPublicationSnapshot(publication, SourceSnapshot(source, payload, lease), _ack))
    return tuple(snapshots)


def _ack(receipt: PublicationReceipt) -> None:
    """The synthetic source has no issuance barrier of its own to release."""


def _liveness(fixture: Fixture, marker: SyntheticMarker) -> Any:
    """Re-read the marker and the fixture on every guard: removing either stops the run."""

    def live() -> None:
        try:
            current_marker = hashlib.sha256(_readable_regular_file(marker.path)).hexdigest()
            current_fixture = hashlib.sha256(_readable_regular_file(fixture.path)).hexdigest()
        except SyntheticRefusalError:
            raise AuthUnavailableError() from None
        if current_marker != marker.digest or current_fixture != fixture.digest:
            raise AuthUnavailableError()

    return live


async def publish_all(
    publisher: AuthInputPublisher, snapshots: Sequence[AuthPublicationSnapshot]
) -> tuple[PublicationReceipt, ...]:
    return tuple([await publisher.publish(snapshot) for snapshot in snapshots])


async def run(arguments: argparse.Namespace, environ: dict[str, str] | None = None) -> int:
    marker = require_marker(arguments.tenant, environ)
    fixture = load_fixture(Path(arguments.fixture), marker)
    snapshots = build_snapshots(fixture, marker, datetime.now(UTC))
    if arguments.dry_run:
        for snapshot in snapshots:
            print(f"{snapshot.publication.kind}\t{snapshot.publication.publication_id}\tSYNTHETIC")
        return 0
    composition, identity = _compose(arguments.tenant)
    try:
        # Real qualification of the installed native and protected relations first: an
        # unqualified store must never be written to, synthetic payload or not.
        await composition.qualify()
        publisher = AuthInputPublisher(
            client=composition.client,
            journal=PostgresAuthPublicationJournal(composition.source.protected),
        )
        for receipt in await publish_all(publisher, snapshots):
            print(
                f"{receipt.kind}\t{receipt.publication_id}\tgeneration={receipt.head_generation}\tSYNTHETIC"
            )
    finally:
        await composition.close()
        await identity.dispose()
    return 0


def _compose(tenant: str) -> tuple[Any, Any]:
    """Compose exactly what production composes — same binding, engine and qualification.

    The deployment's own installed binding is used; no binding path is accepted on the
    command line, so this script cannot be aimed at another installation's materials.
    """
    import ssl

    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    from maezo.gateway.intake.native_authority import load_auth_lifecycle
    from maezo.portal.api.config import PortalSettings

    config = PortalSettings()  # type: ignore[call-arg]
    if config.tenant != tenant:
        raise SyntheticRefusalError(f"deployment tenant is {config.tenant!r}, not {tenant!r}")
    if config.auth_lifecycle_binding_path is None or config.database_url is None:
        raise SyntheticRefusalError("no installed AUTH lifecycle binding and identity database")
    url = make_url(config.database_url.get_secret_value())
    if url.drivername != "postgresql+asyncpg" or not url.host:
        raise SyntheticRefusalError("dedicated PostgreSQL asyncpg connection required")
    engine = create_async_engine(
        url, hide_parameters=True, echo=False, connect_args={"ssl": ssl.create_default_context()}
    )
    composition = load_auth_lifecycle(
        config.auth_lifecycle_binding_path, tenant=config.tenant, identity_writer=engine
    )
    return composition, engine


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--fixture", required=True, help="labelled synthetic fixture (JSON)")
    parser.add_argument("--dry-run", action="store_true", help="build and label, publish nothing")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, environ: dict[str, str] | None = None) -> int:
    try:
        return asyncio.run(run(parse_arguments(argv), environ))
    except SyntheticRefusalError as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 2
    except AuthUnavailableError:
        print("REFUSED: the AUTH publication path is unavailable; nothing was published", file=sys.stderr)
        return 3


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
