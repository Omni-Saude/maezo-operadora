"""TestOnly AUTH material custody and real protected lifecycle loading.

Material generation is not installation, qualification, a source grant, or a
professional act. The owner installer must designate the returned public records
through AuthInstallation and provide measured E04/native bindings separately.
No HTTP substitute, native-v2 workload profile, receipt seed, or DSN fallback.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import os
import re
import secrets
import ssl
import stat
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.gateway.human.auth_profile import (
    Actor,
    InputPublication,
    Pin,
    PublicationLookup,
    PublicationQuery,
    Purpose,
    Scope,
)
from maezo.gateway.human.auth_transport import AuthUnavailableError, bind_result
from maezo.gateway.human.read_profile import SourceProvenance, digest, parse_model, wire
from maezo.gateway.human.read_publisher import membership_projection
from maezo.gateway.intake.native_authority import (
    AuthLifecycleConfiguration,
    AuthProductionComposition,
    KeyDesignation,
    NativeClientBinding,
    PostgresAuthCredentialProvider,
    SigningMaterial,
    SourceGrant,
    load_auth_lifecycle,
    protected_bytes,
)
from maezo.gateway.intake.native_source_lifecycle import (
    IdentitySourceBinding,
    MembershipDependencyBinding,
)
from maezo.portal.api.records import MembershipRecord
from maezo.portal.engine.profile import canonicalize, strict_loads
from tests.support.provider_tls_pg import OwnedTlsPostgres

CONTRACT = "provider-owned-auth-native-materials.testonly.v1"
RESULT_ALIAS = "provider-native-result"
PUBLISH_PURPOSE: Literal["human-auth-input-publication"] = "human-auth-input-publication"
READ_PURPOSE: Literal["human-auth-publication-read"] = "human-auth-publication-read"


class NativeFixtureError(RuntimeError):
    """A TestOnly prerequisite is absent or changed; no secret-bearing diagnostic."""


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _der(public: ec.EllipticCurvePublicKey | Ed25519PrivateKey) -> bytes:
    key = public.public_key() if isinstance(public, Ed25519PrivateKey) else public
    return key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def _write(directory: Path, name: str, value: bytes) -> Path:
    path = directory / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
    except BaseException:
        # Retain partially written material for custody; do not delete unknown bytes.
        raise NativeFixtureError("TestOnly material write failed") from None
    return path


@dataclass(frozen=True, repr=False)
class NativeTestMaterials:
    """Owned files and uninstalled designations; private values excluded from repr."""

    directory: Path
    owner: str
    files: tuple[tuple[str, str], ...]
    proposed_designations: tuple[KeyDesignation, ...]
    client_certificate: Path
    client_private_key: Path
    ca_certificate: Path
    server_certificate: Path
    server_private_key: Path
    result_pkcs12: Path
    result_password: Path
    journal_key: Path
    intake_key: Path
    signing: tuple[SigningMaterial, ...]

    def guard(self) -> None:
        metadata = self.directory.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or {p.name for p in self.directory.iterdir()} != {name for name, _ in self.files}
        ):
            raise NativeFixtureError("TestOnly material directory changed")
        for name, expected in self.files:
            protected_bytes(self.directory / name, expected)
        identifiers = {d.key_id for d in self.proposed_designations}
        public_keys = {d.public_key_base64 for d in self.proposed_designations}
        if len(identifiers) != 3 or len(public_keys) != 3:
            raise NativeFixtureError("TestOnly AUTH purpose keys are not distinct")

    def client_binding(self, *, origin: str, audience: str) -> NativeClientBinding:
        self.guard()
        target = urlsplit(origin)
        if (
            target.scheme != "https"
            or target.hostname not in ("localhost", "127.0.0.1")
            or target.port is None
            or target.path not in ("", "/")
            or target.query
            or target.fragment
            or target.username is not None
            or target.password is not None
        ):
            raise NativeFixtureError("TestOnly receiver must be a measured loopback HTTPS origin")
        result = next(d for d in self.proposed_designations if d.purpose == "human-auth-result")
        return NativeClientBinding(
            origin=origin,
            audience=audience,
            client_certificate_path=self.client_certificate,
            client_private_key_path=self.client_private_key,
            ca_path=self.ca_certificate,
            client_certificate_digest=sha256(protected_bytes(self.client_certificate)),
            client_private_key_digest=sha256(protected_bytes(self.client_private_key)),
            ca_digest=sha256(protected_bytes(self.ca_certificate)),
            native_key_id=result.key_id,
            native_designation_digest=digest(result),
            signing=self.signing,
            max_envelope_seconds=30,
        )

    def tls_context(self) -> ssl.SSLContext:
        self.guard()
        context = ssl.create_default_context(cafile=str(self.ca_certificate))
        context.load_cert_chain(str(self.client_certificate), str(self.client_private_key))
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise NativeFixtureError("TestOnly TLS verification absent")
        return context

    def remove_owned_files(self) -> None:
        """Only exact unchanged files created here; unknown/changed data is preserved."""
        self.guard()
        for name, _ in self.files:
            (self.directory / name).unlink()
        self.directory.rmdir()


def generate_native_materials(
    parent: Path, *, owner: str, workload_ref: str, source_grants: tuple[SourceGrant, ...]
) -> NativeTestMaterials:
    """Generate isolated two-hour TestOnly CA, mTLS, three Ed25519 keys and AEAD keys.

    Result signer and HTTPS server intentionally share the result Ed25519 SPKI:
    AuthResultSigner.load derives peerSpki from its signing certificate, and the
    real AuthNativeClient checks that pin against the observed TLS certificate.
    Publication and publication-read use separate signing keys and no result grant.
    """
    if (
        not parent.is_absolute()
        or parent.resolve() != parent
        or not parent.is_dir()
        or parent.stat().st_uid != os.geteuid()
        or stat.S_IMODE(parent.stat().st_mode) & 0o077
    ):
        raise NativeFixtureError("TestOnly material parent must be an absolute directory")
    if not owner.startswith("provider-") or not workload_ref.startswith("TestOnly-") or not source_grants:
        raise NativeFixtureError("TestOnly owner and exact source grants required")
    if len({digest(g) for g in source_grants}) != len(source_grants):
        raise NativeFixtureError("TestOnly source grant repeated")
    directory = parent / ("provider-auth-native-" + secrets.token_hex(12))
    directory.mkdir(mode=0o700)
    start, end = datetime.now(UTC) - timedelta(minutes=1), datetime.now(UTC) + timedelta(hours=2)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TestOnly-provider-native-ca")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True
        )
        .sign(ca_key, hashes.SHA256())
    )
    publish_key, read_key, result_key = (Ed25519PrivateKey.generate() for _ in range(3))
    client_key = ec.generate_private_key(ec.SECP256R1())

    def certificate(
        public: ec.EllipticCurvePublicKey | Ed25519PublicKey, *, server: bool
    ) -> x509.Certificate:
        builder = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost" if server else owner)])
            )
            .issuer_name(ca.subject)
            .public_key(public)
            .serial_number(x509.random_serial_number())
            .not_valid_before(start)
            .not_valid_after(end)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.ExtendedKeyUsage(
                    [ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]
                ),
                critical=True,
            )
        )
        if server:
            builder = builder.add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
        return builder.sign(ca_key, hashes.SHA256())

    server_cert, client_cert = (
        certificate(result_key.public_key(), server=True),
        certificate(client_key.public_key(), server=False),
    )
    values: dict[str, bytes] = {
        "ca.pem": ca.public_bytes(serialization.Encoding.PEM),
        "client.pem": client_cert.public_bytes(serialization.Encoding.PEM),
        "server.pem": server_cert.public_bytes(serialization.Encoding.PEM),
        "client-key.pem": client_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        "server-key.pem": result_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        "publication-key.pem": publish_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        "publication-read-key.pem": read_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        "journal-key.bin": secrets.token_bytes(32),
        "intake-key.bin": secrets.token_bytes(32),
        "result-password.txt": secrets.token_hex(24).encode("ascii"),
    }
    while values["journal-key.bin"] == values["intake-key.bin"]:
        values["intake-key.bin"] = secrets.token_bytes(32)
    values["result.p12"] = pkcs12.serialize_key_and_certificates(
        RESULT_ALIAS.encode(),
        result_key,
        server_cert,
        [ca],
        serialization.BestAvailableEncryption(values["result-password.txt"]),
    )
    paths = {name: _write(directory, name, value) for name, value in values.items()}
    designations = []
    signing = []
    prefix = "TestOnly-" + secrets.token_hex(12)
    purpose_keys: tuple[tuple[Purpose, Ed25519PrivateKey, str], ...] = (
        (PUBLISH_PURPOSE, publish_key, "publication-key.pem"),
        (READ_PURPOSE, read_key, "publication-read-key.pem"),
        ("human-auth-result", result_key, "server-key.pem"),
    )
    for purpose, key, filename in purpose_keys:
        designation = KeyDesignation(
            schema="human-auth-key-designation.v1",
            key_id=prefix + "-" + purpose,
            issuer=workload_ref if purpose != "human-auth-result" else prefix + "-receiver",
            purpose=purpose,
            peer_spki_sha256=sha256(
                _der(result_key) if purpose == "human-auth-result" else _der(client_key.public_key())
            ),
            public_key_base64=base64.b64encode(_der(key)).decode("ascii"),
            not_before=start,
            not_after=end,
            source_grants=() if purpose == "human-auth-result" else source_grants,
        )
        designations.append(designation)
        if purpose != "human-auth-result":
            signing.append(
                SigningMaterial(
                    purpose=purpose,
                    key_id=designation.key_id,
                    issuer=designation.issuer,
                    private_key_path=paths[filename],
                    private_key_digest=sha256(values[filename]),
                    designation_digest=digest(designation),
                )
            )
    materials = NativeTestMaterials(
        directory,
        owner,
        tuple((name, sha256(value)) for name, value in values.items()),
        tuple(designations),
        paths["client.pem"],
        paths["client-key.pem"],
        paths["ca.pem"],
        paths["server.pem"],
        paths["server-key.pem"],
        paths["result.p12"],
        paths["result-password.txt"],
        paths["journal-key.bin"],
        paths["intake-key.bin"],
        tuple(signing),
    )
    materials.guard()
    return materials


@asynccontextmanager
async def loaded_native_lifecycle(
    path: Path, *, expected_digest: str, identity_writer: AsyncEngine, materials: NativeTestMaterials
) -> AsyncIterator[AuthProductionComposition]:
    """Use the actual loader and every SQL qualifier after an owner installation.

    This consumes an installed protected configuration; it cannot create one or
    infer native admission from generated materials. ROOT must arrange the PG CA
    trust in the isolated test process because the existing loader uses system CA.
    """
    materials.guard()
    config = AuthLifecycleConfiguration.model_validate_json(
        protected_bytes(path, expected_digest), strict=True
    )
    if config.client != materials.client_binding(
        origin=config.client.origin, audience=config.client.audience
    ):
        raise NativeFixtureError("TestOnly installed client differs from generated material")
    if not config.native.scope.environment.startswith("TestOnly-"):
        raise NativeFixtureError("TestOnly AUTH realm marker absent")
    composition = load_auth_lifecycle(path, tenant=config.identity.tenant, identity_writer=identity_writer)
    try:
        await composition.qualify()
        credentials = PostgresAuthCredentialProvider(composition.native, config.client)
        for purpose in (PUBLISH_PURPOSE, READ_PURPOSE):
            await credentials.acquire(config.native.scope, purpose)
        await credentials.native_trust(config.native.scope)
        yield composition
    except AuthUnavailableError:
        raise NativeFixtureError("TestOnly real AUTH installation unavailable") from None
    finally:
        await composition.close()


def _require_current_actor_membership(
    member: MembershipRecord,
    *,
    binding: IdentitySourceBinding,
    scope: Scope,
    actor: Actor,
    head: Mapping[str, Any],
    expected_record_digest: str,
    now: datetime,
) -> None:
    """Bind actual typed payload bytes to the installed source and requested actor.

    SQL lookup columns do not prove identity fields inside the independent payload.
    This is rechecked at every source observation, before publication and owner SQL.
    """
    if (
        type(member) is not MembershipRecord
        or type(binding) is not IdentitySourceBinding
        or type(scope) is not Scope
        or type(actor) is not Actor
        or scope not in binding.scopes
        or binding.tenant != scope.tenant
        or actor.audience != "provider"
        or (
            member.tenant,
            member.issuer,
            member.subject,
            member.principal_ref,
            member.revision,
            member.audience,
            member.revoked,
        )
        != (
            binding.tenant,
            actor.issuer,
            actor.subject,
            actor.principal_ref,
            actor.membership_revision,
            "provider",
            False,
        )
        or (head["tenant"], head["category"], head["identity_ref"])
        != (binding.tenant, "membership", actor.principal_ref)
        or type(head["revision"]) is not int
        or head["revision"] < 1
        or head["state"] != "active"
        or head["pending_change"] is not None
        or head["record_digest"] != expected_record_digest
        or now >= min(binding.valid_until, member.reviewed_until)
    ):
        raise NativeFixtureError("TestOnly actor source is not current")


async def publish_and_register_actor_dependency(
    composition: AuthProductionComposition,
    pg: OwnedTlsPostgres,
    *,
    actor: Actor,
    dependency_ref: str,
) -> MembershipDependencyBinding:
    """TestOnly owner registration after genuine API publication and reconciliation.

    The kernel explicitly allocates membership_dependency registration to the
    owner. This fixture operation is not a runtime grant/API. It cannot accept a
    caller-supplied Pin, receipt or acknowledgement: both are obtained through the
    installed client and persisted by the real journal before this owner insert.
    It writes no issuance ACK, source_change, witness, or signed result.
    """
    source = composition.source
    b = source.binding
    scope = composition.config.native.scope
    if (
        type(composition) is not AuthProductionComposition
        or type(pg) is not OwnedTlsPostgres
        or not scope.environment.startswith("TestOnly-")
        or scope not in b.scopes
        or b.tenant != scope.tenant
        or actor.audience != "provider"
        or not dependency_ref.startswith("TestOnly-")
        or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", b.owner_role) is None
    ):
        raise NativeFixtureError("TestOnly owner registration scope refused")

    async def observe_member() -> tuple[MembershipRecord, dict[str, Any]]:
        async with source.reader.connect() as db:
            await source.qualified(db, b.reader_role)
            row = (
                await db.execute(
                    text(
                        f'SELECT payload FROM "{b.source_schema_name}".portal_memberships '
                        "WHERE tenant=:tenant AND issuer=:issuer AND subject=:subject"
                    ),
                    dict(tenant=b.tenant, issuer=actor.issuer, subject=actor.subject),
                )
            ).scalar_one()
            member = MembershipRecord.model_validate_json(row)
            head = (
                (
                    await db.execute(
                        text(
                            "SELECT * FROM portal_auth.source_head WHERE tenant=:tenant "
                            "AND category='membership' AND identity_ref=:principal"
                        ),
                        dict(tenant=b.tenant, principal=actor.principal_ref),
                    )
                )
                .mappings()
                .one()
            )
        _require_current_actor_membership(
            member,
            binding=b,
            scope=scope,
            actor=actor,
            head=dict(head),
            expected_record_digest=source.record_digest("membership", member),
            now=datetime.now(UTC),
        )
        return member, dict(head)

    member, head = await observe_member()
    until = min(
        b.valid_until, member.reviewed_until, datetime.now(UTC) + timedelta(seconds=b.control_seconds)
    )
    provenance = SourceProvenance(
        publisher_ref=b.publisher_ref,
        source_ref=b.source_ref,
        source_revision=head["revision"],
        source_digest=head["record_digest"],
        receipt_ref=b.installation_ref,
        observed_at=datetime.now(UTC),
        valid_until=until,
    )
    workload = next(m.issuer for m in composition.config.client.signing if m.purpose == PUBLISH_PURPOSE)
    payload = membership_projection(member)
    publication_id = "TestOnly-initial-" + sha256(dependency_ref.encode())
    publication = InputPublication(
        schema="human-auth-input-publication.v1",
        scope=scope,
        workload_ref=workload,
        publication_id=publication_id,
        kind="actor",
        resource_ref=actor.principal_ref,
        expected_generation=0,
        source=provenance,
        state="active",
        payload=payload,
        payload_digest=digest(payload),
        valid_until=until,
    )
    store = source.journal.store
    async with store.engine.connect() as db:
        await store.qualify(db)
        pending = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM portal_intake.native_publication "
                        "WHERE tenant=:tenant AND publication_id=:publication"
                    ),
                    dict(tenant=b.tenant, publication=publication_id),
                )
            )
            .mappings()
            .one_or_none()
        )
    if pending is not None:
        # A lost response must retry the exact journalled command, including its
        # original timestamps and CAS generation. Never mint renewed source facts.
        original = parse_model(
            InputPublication,
            store.unseal(
                "publication", publication_id, pending["key_id"], pending["nonce"], pending["ciphertext"]
            ),
        )
        if (
            pending["request_digest"] != digest(original)
            or original.scope != publication.scope
            or original.workload_ref != publication.workload_ref
            or original.kind != "actor"
            or original.resource_ref != actor.principal_ref
            or original.state != "active"
            or original.expected_generation != 0
            or wire(original.payload) != wire(payload)
            or original.source.publisher_ref != b.publisher_ref
            or original.source.source_ref != b.source_ref
            or original.source.source_revision != head["revision"]
            or original.source.source_digest != head["record_digest"]
            or original.source.receipt_ref != b.installation_ref
            or datetime.now(UTC) >= min(original.valid_until, until)
        ):
            raise NativeFixtureError("TestOnly original publication journal differs or expired")
        publication = original
        provenance = original.source
        until = min(until, original.valid_until)

    async def checkpoint() -> None:
        observed, current = await observe_member()
        if observed != member or current != head or datetime.now(UTC) >= until:
            raise NativeFixtureError("TestOnly publication source changed")

    receipt = await source._native_publication_receipt(publication, checkpoint)
    bind_result(receipt, publication, datetime.now(UTC))
    query = PublicationQuery(
        schema="human-auth-publication-query.v1",
        scope=scope,
        workload_ref=workload,
        query_id="TestOnly-query-" + secrets.token_hex(16),
        publication_id=publication.publication_id,
        expected_digest=digest(publication),
    )
    lookup = await composition.client.execute(query, checkpoint=checkpoint)
    if not isinstance(lookup, PublicationLookup) or lookup.status != "committed" or lookup.receipt != receipt:
        raise NativeFixtureError("TestOnly native publication is not reconciled")
    binding = MembershipDependencyBinding(
        dependency_ref=dependency_ref,
        principal_ref=actor.principal_ref,
        scope=scope,
        kind="actor",
        resource_ref=actor.principal_ref,
        source_ref=b.source_ref,
        publisher_ref=b.publisher_ref,
        workload_ref=workload,
        original_pin=Pin(
            kind="actor",
            resource_ref=actor.principal_ref,
            head_generation=receipt.head_generation,
            source=provenance,
            payload_digest=digest(payload),
        ),
    )
    # The fixture's own bootstrap connection is intentionally distinct from all
    # runtime roles. SET LOCAL is confined to this owner installation transaction.
    async with asyncio.timeout(5), pg.admin.transaction():
        await pg.admin.execute(
            "SET LOCAL statement_timeout='5s';SET LOCAL lock_timeout='3s';"
            "SET LOCAL idle_in_transaction_session_timeout='5s'"
        )
        before = await pg.admin.fetchrow(
            "SELECT current_user AS role,session_user AS session_role,current_database() AS database_name,"
            "(SELECT oid FROM pg_database WHERE datname=current_database()) AS database_oid,"
            "(SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()) AS tls"
        )
        if tuple(before.values()) != ("postgres", "postgres", b.database_name, b.database_oid, True):
            raise NativeFixtureError("TestOnly owner installation connection differs")
        await pg.admin.execute(f'SET LOCAL ROLE "{b.owner_role}"')
        installed = await pg.admin.fetchrow(
            "SELECT binding_digest,valid_until FROM portal_auth.installation WHERE tenant=$1 FOR UPDATE",
            b.tenant,
        )
        if (
            installed is None
            or installed["binding_digest"] != digest(b)
            or datetime.now(UTC) >= installed["valid_until"]
        ):
            raise NativeFixtureError("TestOnly identity owner binding changed")
        locked = await pg.admin.fetchrow(
            "SELECT * FROM portal_auth.source_head WHERE tenant=$1 "
            "AND category='membership' AND identity_ref=$2 FOR UPDATE",
            b.tenant,
            actor.principal_ref,
        )
        if locked is None or dict(locked) != head:
            raise NativeFixtureError("TestOnly owner census source changed")
        previous = await pg.admin.fetch(
            "SELECT binding FROM portal_auth.membership_dependency WHERE tenant=$1 "
            "AND principal_ref=$2 ORDER BY dependency_ref FOR UPDATE",
            b.tenant,
            actor.principal_ref,
        )
        previous_census = [strict_loads(row["binding"]) for row in previous]
        for entry in previous_census:
            prior = MembershipDependencyBinding.model_validate_json(canonicalize(entry))
            if prior.principal_ref != actor.principal_ref or prior.scope not in b.scopes:
                raise NativeFixtureError("TestOnly existing owner census has a different principal or scope")
        if head["dependency_set_digest"] != digest(previous_census):
            raise NativeFixtureError("TestOnly existing owner census is incomplete or changed")
        await checkpoint()
        existing = await pg.admin.fetchrow(
            "SELECT binding,last_publication,last_receipt,pending_publication,pending_change "
            "FROM portal_auth.membership_dependency WHERE tenant=$1 AND dependency_ref=$2 FOR UPDATE",
            b.tenant,
            dependency_ref,
        )
        if existing is None:
            await pg.admin.execute(
                "INSERT INTO portal_auth.membership_dependency(tenant,principal_ref,dependency_ref,"
                "binding,last_publication,last_receipt) "
                "VALUES($1,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb)",
                b.tenant,
                actor.principal_ref,
                dependency_ref,
                canonicalize(wire(binding)).decode(),
                canonicalize(wire(publication)).decode(),
                canonicalize(wire(receipt)).decode(),
            )
        elif (
            strict_loads(existing["binding"]) != wire(binding)
            or strict_loads(existing["last_publication"]) != wire(publication)
            or strict_loads(existing["last_receipt"]) != wire(receipt)
            or existing["pending_publication"] is not None
            or existing["pending_change"] is not None
        ):
            raise NativeFixtureError("TestOnly repeated owner registration differs")
        rows = await pg.admin.fetch(
            "SELECT binding FROM portal_auth.membership_dependency WHERE tenant=$1 "
            "AND principal_ref=$2 ORDER BY dependency_ref",
            b.tenant,
            actor.principal_ref,
        )
        # asyncpg JSON values are strings; decode via the production strict parser.
        census = [strict_loads(row["binding"]) for row in rows]
        await pg.admin.execute(
            "UPDATE portal_auth.source_head SET dependency_set_digest=$3 WHERE tenant=$1 "
            "AND category='membership' AND identity_ref=$2",
            b.tenant,
            actor.principal_ref,
            digest(census),
        )
        written = await pg.admin.fetchrow(
            "SELECT binding,last_publication,last_receipt,pending_publication,pending_change "
            "FROM portal_auth.membership_dependency WHERE tenant=$1 AND dependency_ref=$2",
            b.tenant,
            dependency_ref,
        )
        if (
            strict_loads(written["binding"]) != wire(binding)
            or strict_loads(written["last_publication"]) != wire(publication)
            or strict_loads(written["last_receipt"]) != wire(receipt)
            or written["pending_publication"] is not None
            or written["pending_change"] is not None
            or datetime.now(UTC) >= until
        ):
            raise NativeFixtureError("TestOnly owner dependency readback differs")
    head["dependency_set_digest"] = digest(census)
    await checkpoint()
    return binding
