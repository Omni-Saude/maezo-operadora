"""WP-J1-04 custody controls: quarantine never satisfies, wrong case never passes,
completion never duplicates. Providers here are offline doubles of PostgreSQL only;
the authority, the provider and the PHI service under test are the production classes.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from maezo.gateway.documents.authority import PublishedDocumentAuthority
from maezo.gateway.documents.postgres import PostgresProtectedDocumentProvider
from maezo.gateway.documents.service import DocumentAccess, DocumentService
from maezo.gateway.documents.storage import DocumentScope, PhiDocumentKeys, content_digest
from maezo.gateway.human.auth_profile import (
    Actor,
    DocumentCustody,
    DocumentPolicy,
    DocumentRef,
    ResourceAuthority,
    Scope,
)
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import ArtifactPin, SourceProvenance, digest
from maezo.gateway.intake.models import IntakeError
from maezo.gateway.intake.native_authority import NativeAuthReader, NativeDatabaseBinding
from maezo.gateway.intake.native_source_lifecycle import RelationPin
from maezo.portal.api.document_phi import PhiDocumentService
from maezo.portal.contracts.documents import (
    DocumentResponse,
    UploadCompletion,
    UploadInitiation,
)
from maezo.portal.contracts.models import HumanPrincipal, SubjectBinding

TENANT = "synthetic-tenant"
CASE = "a" * 32
OTHER_CASE = "b" * 32
REQUEST = "c" * 32
POLICY = "d" * 32
DOCTYPE = "e" * 32
DOCUMENT = "f" * 32
OTHER_DOCUMENT = "10" + "f" * 30


#: Deadlines are computed at CALL time, never at import time: this suite runs inside a
#: full-suite job that may reach these tests many minutes after collection, and a stale
#: window would read as an expired session instead of a test defect.
def _until() -> datetime:
    return datetime.now(UTC) + timedelta(minutes=5)


def _deadline() -> datetime:
    return datetime.now(UTC) + timedelta(minutes=10)


PHRASE = b"PRIVATE_CLINICAL_CANARY"

pytestmark = pytest.mark.asyncio


def principal(ref: str = "human-internal-1", subject: str = "synthetic-subject") -> HumanPrincipal:
    return HumanPrincipal(
        schema_version=1,
        principal_ref=ref,
        issuer="https://idp.example.test",
        subject=subject,
        tenant=TENANT,
        membership_revision=1,
        memberships=(),
        session_ref="session-ref-1",
        authenticated_at=datetime.now(UTC),
        subject_bindings=(SubjectBinding(kind="provider", resource_ref=CASE),),
    )


def actor(principal_value: HumanPrincipal, audience: str = "provider") -> Actor:
    return Actor.from_principal(principal_value, audience)  # type: ignore[arg-type]


def source(ref: str) -> SourceProvenance:
    payload = {"ref": ref, "kind": "synthetic"}
    return SourceProvenance(
        publisher_ref="synthetic-publisher",
        source_ref=ref,
        source_revision=1,
        source_digest=digest(payload),
        receipt_ref="receipt-1",
        observed_at=datetime.now(UTC),
        valid_until=_deadline(),
    )


def policy_head(
    principal_value: HumanPrincipal,
    *,
    case_ref: str = CASE,
    request_ref: str | None = REQUEST,
    complete: bool = False,
) -> DocumentPolicy:
    documents: tuple[DocumentRef, ...] = ()
    return DocumentPolicy(
        assessment_ref="assessment-1",
        resource_kind="case",
        resource_ref=case_ref,
        request_ref=request_ref,
        request_revision=3,
        policy=ArtifactPin(artifact_ref="document-policy-artifact", digest="5" * 64),
        policy_revision=1,
        recipient_principal_refs=(principal_value.principal_ref,),
        required_codes=(DOCTYPE,),
        missing_codes=(DOCTYPE,),
        submitted_response_digest=None,
        effective_document_refs=documents,
        document_set_digest=digest(documents),
        complete=complete,
        source=source("policy-source"),
        valid_until=_until(),
    )


def custody_head(
    document_ref: str,
    principal_value: HumanPrincipal,
    *,
    case_ref: str = CASE,
    screening_result: str = "clean",
    custody_state: str = "available",
) -> DocumentCustody:
    return DocumentCustody(
        document=DocumentRef(
            document_ref=document_ref,
            custody_revision=1,
            content_sha256="1" * 64,
            storage_version_ref=document_ref,
            policy_digest="2" * 64,
        ),
        resource_kind="case",
        resource_ref=case_ref,
        creator_principal_ref=principal_value.principal_ref,
        screening_ref="screening-1",
        screening_revision=1,
        screening_result=screening_result,  # type: ignore[arg-type]
        custody_state=custody_state,  # type: ignore[arg-type]
        key_custody_ref="key-custody-1",
        key_custody_revision=1,
        valid_until=_until(),
    )


def respond_authority(
    principal_value: HumanPrincipal, *, case_ref: str = CASE, request_ref: str = REQUEST
) -> ResourceAuthority:
    return ResourceAuthority(
        authority_ref="authority-1",
        actor=actor(principal_value),
        beneficiary_ref="9" * 32,
        provider_ref=principal_value.principal_ref,
        resource_kind="case",
        resource_ref=case_ref,
        action="auth.documents.respond",
        request_ref=request_ref,
        relationship_revision=1,
        consent_revision=1,
        grant_ref="grant-1",
        basis_ref="basis-1",
        legal_basis="consent",
        consent_state="valid",
        state="active",
        valid_from=datetime.now(UTC) - timedelta(minutes=1),
        valid_until=_until(),
        source=source("authority-source"),
    )


def context_authority(principal_value: HumanPrincipal, case_ref: str = CASE) -> ResourceAuthority:
    granted = respond_authority(principal_value, case_ref=case_ref)
    return granted.model_copy(update={"action": "auth.document_context.read", "request_ref": None})


class NativeHeads:
    """A real `NativeAuthReader` whose published heads come from a dict.

    Only the SQL boundary is substituted: `NativeAuthReader` itself is constructed and
    every payload flows through the production authority/provider code paths.
    """

    def __init__(self, heads: dict[tuple[str, str], object]) -> None:
        binding = NativeDatabaseBinding(
            scope=Scope(
                tenant=TENANT,
                environment="synthetic-env-0001",
                engine_name="synthetic-engine",
                database_incarnation="incarnation-1",
                installation_ref="installation-1",
                installation_revision=1,
            ),
            database_name="synthetic",
            database_oid=1,
            schema_name="mzo_auth",
            schema_oid=2,
            owner_role="mzo_auth_owner",
            reader_role="mzo_auth_reader",
            relations=tuple(
                RelationPin(schema_name="mzo_auth", name=name, oid=oid, owner="mzo_auth_owner")
                for oid, name in enumerate(
                    (
                        "mzo_auth_installation",
                        "mzo_auth_trust",
                        "mzo_auth_revoked_key",
                        "mzo_auth_input_head",
                        "mzo_auth_input_version",
                    ),
                    start=10,
                )
            ),
            installed_binding_digest="3" * 64,
            installed_qualification_digest="4" * 64,
            valid_until=_deadline(),
        )
        self.heads = heads
        self.reader = NativeAuthReader(SimpleNamespace(dialect=SimpleNamespace(name="postgresql")), binding)

        async def qualified(db: object) -> tuple[dict[str, object], datetime]:
            return {"installation": "synthetic"}, _deadline()

        async def head(db: object, kind: str, ref: str) -> tuple[object, dict[str, object], datetime]:
            try:
                return self.heads[(kind, ref)], {"resource_": ref}, _deadline()
            except KeyError:
                raise AuthUnavailableError() from None

        self.reader.qualified = qualified  # type: ignore[method-assign]
        self.reader.head = head  # type: ignore[method-assign]
        # The authority opens its own reader connection; this object IS the engine seam.
        self.reader.engine = self

    @asynccontextmanager
    async def connect(self):  # type: ignore[no-untyped-def]
        connection = SimpleNamespace(execute=self._execute)
        yield connection

    async def _execute(self, sql: object, values: object | None = None) -> object:
        # The candidate SELECT is a FILTER only in production; here it stays generous and
        # the payload checks after `head` do the real refusing, which is exactly the
        # property the authority documents. Every other statement is a refusal.
        query = str(sql)
        if query.startswith("SELECT resource_"):
            kind = dict(values or {}).get("kind")
            refs = sorted({ref for (k, ref) in self.heads if k == kind})
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: refs))
        if "SET TRANSACTION ISOLATION LEVEL" in query:
            return None
        raise AssertionError(f"fake reader cannot answer: {query[:100]}")


class DocumentDatabase:
    """Offline `portal_document` store: the SQL surface the production provider speaks."""

    def __init__(self) -> None:
        self.upload: dict[str, dict[str, object]] = {}
        self.object: dict[str, dict[str, object]] = {}
        self.document: dict[str, dict[str, object]] = {}
        self.custody: dict[str, dict[str, object]] = {}
        self.response: dict[str, dict[str, object]] = {}
        self.locks: list[str] = []
        # `external_cases.postgres._transaction` refuses anything that is not a
        # PostgreSQL engine, so the offline store presents the same face.
        self.dialect = SimpleNamespace(name="postgresql")
        self.echo = False
        self.sync_engine = SimpleNamespace(hide_parameters=True)

    def connect(self) -> _PendingConnection:
        # SQLAlchemy's async engine awaits `connect()`; `_transaction` awaits it too.
        return _PendingConnection(self)

    @staticmethod
    def _result(row: dict[str, object] | None = None, rows: list[dict[str, object]] | None = None):  # type: ignore[no-untyped-def]
        found = rows if rows is not None else ([] if row is None else [row])
        mappings = SimpleNamespace(
            one_or_none=lambda: row,
            one=lambda: (_ for _ in ()).throw(AssertionError("one() not expected offline")),
            all=lambda: found,
        )
        return SimpleNamespace(
            mappings=lambda: mappings,
            scalars=lambda: SimpleNamespace(all=lambda: [item["document_ref"] for item in found]),
        )

    async def execute(self, sql: object, values: dict[str, object]):  # type: ignore[no-untyped-def]
        query, v = str(sql), dict(values)
        if "set_config" in query:
            return self._result()
        if "pg_advisory" in query:
            self.locks.append(str(v["lock"]))
            return self._result()
        if query.startswith("INSERT INTO portal_document.upload"):
            self.upload[str(v["upload"])] = {
                "upload_ref": v["upload"],
                "resource_kind": v["rkind"],
                "resource_ref": v["rref"],
                "policy_ref": v["policy"],
                "policy_digest": v["pdigest"],
                "document_type_ref": v["dtype"],
                "creator_identity_digest": v["creator"],
                "command_id": v["command"],
                "admitted_digest": v["admitted"],
                "state": "awaiting_content",
                "document_ref": None,
                "key_id": v["key"],
                "valid_until": v["until"],
            }
        elif query.startswith("INSERT INTO portal_document.object"):
            self.object[str(v["upload"])] = {
                "key_id": v["key"],
                "nonce": v["nonce"],
                "ciphertext": v["ciphertext"],
                "content_sha256": v["digest"],
            }
        elif query.startswith("INSERT INTO portal_document.document"):
            self.document[str(v["document"])] = {
                "document_ref": v["document"],
                "document_type_ref": v["dtype"],
                "upload_ref": v["upload"],
                "resource_kind": v["rkind"],
                "resource_ref": v["rref"],
                "content_sha256": v["content"],
                "creator_identity_digest": v["creator"],
            }
        elif query.startswith("INSERT INTO portal_document.custody"):
            # screening_result/custody_state are SQL literals here: `pending`/`available`
            # is the only state this plane may write on its own.
            assert "'pending'" in query and "'available'" in query
            self.custody[str(v["document"])] = {
                "screening_result": "pending",
                "custody_state": "available",
                "resource_ref": v["rref"],
            }
        elif query.startswith("INSERT INTO portal_document.response"):
            self.response[str(v["command"])] = {
                "response_ref": v["response"],
                "command_id": v["command"],
                "case_ref": v["case"],
                "request_ref": v["request"],
                "principal_ref": v["principal"],
                "admitted_digest": v["admitted"],
            }
        elif query.startswith("UPDATE portal_document.upload"):
            row = self.upload[str(v["upload"])]
            row["state"], row["document_ref"] = "screening", v["document"]
        elif "FROM portal_document.upload" in query and "creator_identity_digest=:creator" in query:
            return self._result(
                next(
                    (
                        dict(item)
                        for item in self.upload.values()
                        if item["creator_identity_digest"] == v["creator"]
                        and item["command_id"] == v["command"]
                    ),
                    None,
                )
            )
        elif "FROM portal_document.upload" in query:
            key = str(v["upload"])
            return self._result(None if key not in self.upload else dict(self.upload[key]))
        elif "FROM portal_document.object" in query:
            key = str(v["upload"])
            return self._result(None if key not in self.object else dict(self.object[key]))
        elif "FROM portal_document.document d JOIN" in query:
            row = self.document.get(str(v["document"]))
            stored = self.object.get(str(row["upload_ref"])) if row else None
            upload = self.upload.get(str(row["upload_ref"])) if row else None
            if row is None or stored is None or upload is None:
                return self._result()
            return self._result(
                {
                    "upload_ref": row["upload_ref"],
                    "content_sha256": row["content_sha256"],
                    "resource_kind": row["resource_kind"],
                    "resource_ref": row["resource_ref"],
                    "document_type_ref": row["document_type_ref"],
                    "command_id": upload["command_id"],
                    "key_id": stored["key_id"],
                    "nonce": stored["nonce"],
                    "ciphertext": stored["ciphertext"],
                }
            )
        elif "FROM portal_document.document" in query:
            rows = [
                dict(item)
                for item in self.document.values()
                if item["resource_kind"] == "case" and item["resource_ref"] == v["case"]
            ]
            return self._result(rows=rows)
        elif "FROM portal_document.response" in query:
            key = str(v["command"])
            return self._result(None if key not in self.response else dict(self.response[key]))
        else:
            raise AssertionError(f"offline store does not speak this query: {query[:120]}")
        return self._result()


class _PendingConnection:
    def __init__(self, database: DocumentDatabase) -> None:
        self.database = database

    def __await__(self):  # type: ignore[no-untyped-def]
        database = self.database

        async def _resume() -> _Connection:
            return _Connection(database)

        return _resume().__await__()


class _Transaction:
    def __init__(self) -> None:
        self.active = True

    async def commit(self) -> None:
        self.active = False

    async def rollback(self) -> None:
        self.active = False


class _Connection:
    """The small connection face `_transaction` actually drives, offline."""

    def __init__(self, database: DocumentDatabase) -> None:
        self.database = database
        self.transaction = _Transaction()

    def begin(self):  # type: ignore[no-untyped-def]
        transaction = self.transaction

        async def _begin() -> _Transaction:
            return transaction

        return _begin()

    def in_transaction(self) -> bool:
        return self.transaction.active

    async def execute(self, sql: object, values: dict[str, object] | None = None):  # type: ignore[no-untyped-def]
        if values is None:
            return None
        return await self.database.execute(sql, values)

    async def close(self) -> None:
        self.transaction.active = False

    async def invalidate(self) -> None:
        self.transaction.active = False


class Resolver:
    """Session seam: the same shape `DocumentService` and the PHI service consume."""

    def __init__(self, principal_value: HumanPrincipal) -> None:
        self.settings = SimpleNamespace(
            tenant=TENANT,
            issuer="https://idp.example.test",
            public_origin="https://portal.example.test",
        )
        self.principal = principal_value
        self.record = SimpleNamespace(expires_at=_until(), csrf_token="synthetic-csrf")
        self.membership = SimpleNamespace(audience="provider", reviewed_until=_until())

    async def resolve(self, secret: str) -> SimpleNamespace:
        return SimpleNamespace(principal=self.principal, record=self.record, membership=self.membership)


def keys() -> PhiDocumentKeys:
    return PhiDocumentKeys(
        scope=DocumentScope(tenant=TENANT, environment="synthetic-env-0001"),
        active_key_id="doc-key-1",
        keys={"doc-key-1": os.urandom(32)},
        valid_until=_deadline(),
    )


class Harness:
    """One composed plane: production authority + provider + PHI service over doubles."""

    def __init__(self, principal_value: HumanPrincipal | None = None) -> None:
        self.principal = principal_value or principal()
        self.native = NativeHeads(
            {
                ("document_policy", POLICY): policy_head(self.principal),
                ("resource_authority", "respond-1"): respond_authority(self.principal),
                ("resource_authority", "context-1"): context_authority(self.principal),
            }
        )
        self.database = DocumentDatabase()
        self.keys = keys()
        self.provider = PostgresProtectedDocumentProvider(
            self.database,
            scope=DocumentScope(tenant=TENANT, environment="synthetic-env-0001"),
            keys=self.keys,
            heads=self.native.reader,
        )
        self.authority = PublishedDocumentAuthority(self.native.reader)
        self.resolver = Resolver(self.principal)
        self.service = DocumentService(self.resolver, self.authority, self.provider)
        self.phi = PhiDocumentService(self.resolver, self.authority, self.provider)

    # -- flows -------------------------------------------------------------------
    async def initiate(self, command_id: str = "1" * 32) -> str:
        receipt = await self.service.execute(
            "synthetic",
            operation="initiate_upload",
            resource_kind="case",
            resource_ref=CASE,
            body=UploadInitiation(command_id=command_id, policy_ref=POLICY, document_type_ref=DOCTYPE),
            csrf="synthetic-csrf",
            origin="https://portal.example.test",
        )
        assert receipt.disposition == "awaiting_content"
        return str(receipt.upload_ref)

    async def deliver(self, upload_ref: str, raw: bytes = PHRASE) -> str:
        return await self.provider.store(upload_ref, self.principal, raw)

    async def complete(self, upload_ref: str, command_id: str = "1" * 32):
        return await self.service.execute(
            "synthetic",
            operation="complete_upload",
            resource_kind="upload",
            resource_ref=upload_ref,
            body=UploadCompletion(command_id=command_id),
            csrf="synthetic-csrf",
            origin="https://portal.example.test",
        )

    async def upload(self) -> tuple[str, str]:
        """One full upload; returns (upload_ref, document_ref) with the document still
        unscreened, which is exactly the state a browser can reach on its own."""
        upload_ref = await self.initiate()
        await self.deliver(upload_ref)
        receipt = await self.complete(upload_ref)
        assert receipt.disposition == "screening" and receipt.document_ref is None
        documents = list(self.database.document)
        assert len(documents) == 1
        return upload_ref, documents[0]

    def publish(self, document_ref: str, **changes: object) -> None:
        """Simulate the engine-side custody publication of a verified screening."""
        self.native.heads[("document_custody", document_ref)] = custody_head(
            document_ref, self.principal, **changes
        )

    async def respond(self, document_refs: tuple[str, ...], *, case_ref: str = CASE):
        return await self.service.execute(
            "synthetic",
            operation="respond",
            resource_kind="request",
            resource_ref=REQUEST,
            case_ref=case_ref,
            body=DocumentResponse(command_id="2" * 32, expected_revision="3", document_refs=document_refs),
            csrf="synthetic-csrf",
            origin="https://portal.example.test",
        )


async def test_pending_completion_reports_screening_and_never_a_document() -> None:
    harness = Harness()
    upload_ref, document_ref = await harness.upload()
    assert harness.database.custody[document_ref]["screening_result"] == "pending"
    listing = await harness.service.execute(
        "synthetic",
        operation="list_documents",
        resource_kind="case",
        resource_ref=CASE,
    )
    # A pending document is not a verified document: the case lists nothing.
    assert listing.documents == ()
    with pytest.raises(IntakeError):
        await harness.respond((document_ref,))


async def test_quarantined_screening_never_satisfies_a_request_or_a_download() -> None:
    harness = Harness()
    upload_ref, document_ref = await harness.upload()
    harness.publish(document_ref, screening_result="quarantined")
    with pytest.raises(IntakeError):
        await harness.respond((document_ref,))
    assert harness.database.response == {}
    # The download authority refuses a quarantined document outright, so the PHI byte
    # route can never be reached for it through any path.
    with pytest.raises(IntakeError):
        await harness.authority.authorize(
            DocumentAccess(
                principal=harness.principal,
                operation="download",
                resource_kind="document",
                resource_ref=document_ref,
            )
        )
    listing = await harness.service.execute(
        "synthetic", operation="list_documents", resource_kind="case", resource_ref=CASE
    )
    assert listing.documents == ()
    # And a completion replay reports the quarantine truthfully, still without a ref.
    replay = await harness.complete(upload_ref)
    assert replay.disposition == "quarantined" and replay.document_ref is None


async def test_rejected_screening_is_also_unusable_but_reports_itself() -> None:
    harness = Harness()
    upload_ref, document_ref = await harness.upload()
    harness.publish(document_ref, screening_result="rejected")
    receipt = await harness.complete(upload_ref)
    assert receipt.disposition == "rejected" and receipt.document_ref is None
    with pytest.raises(IntakeError):
        await harness.respond((document_ref,))


async def test_verified_document_satisfies_its_request_exactly_once() -> None:
    harness = Harness()
    upload_ref, document_ref = await harness.upload()
    harness.publish(document_ref)
    receipt = await harness.complete(upload_ref)
    assert receipt.disposition == "verified" and receipt.document_ref == document_ref
    response = await harness.respond((document_ref,))
    assert response.disposition == "admitted" and response.correlation_receipt_ref is None
    assert response.request_ref == REQUEST
    replay = await harness.respond((document_ref,))
    assert replay.disposition == "admitted"
    assert len(harness.database.response) == 1
    listing = await harness.service.execute(
        "synthetic", operation="list_documents", resource_kind="case", resource_ref=CASE
    )
    assert [summary.document_ref for summary in listing.documents] == [document_ref]


async def test_wrong_case_document_and_request_are_refused() -> None:
    harness = Harness()
    _upload_ref, document_ref = await harness.upload()
    harness.publish(document_ref)
    # The document's custody names another case: refuse even though the request is ours.
    harness.native.heads[("document_custody", document_ref)] = custody_head(
        document_ref, harness.principal, case_ref=OTHER_CASE
    )
    with pytest.raises(IntakeError):
        await harness.respond((document_ref,), case_ref=CASE)
    # And a respond authority published for another case never carries our request.
    other = Harness(principal())
    other.native.heads[("resource_authority", "respond-1")] = respond_authority(
        other.principal, case_ref=OTHER_CASE
    )
    with pytest.raises(IntakeError):
        await other.authority.authorize(
            DocumentAccess(
                principal=other.principal,
                operation="respond",
                resource_kind="request",
                resource_ref=REQUEST,
                case_ref=CASE,
            )
        )
    with pytest.raises(IntakeError):
        await other.respond((document_ref,), case_ref=CASE)


async def test_complete_is_idempotent_on_retry() -> None:
    harness = Harness()
    upload_ref = await harness.initiate()
    await harness.deliver(upload_ref)
    first = await harness.complete(upload_ref)
    second = await harness.complete(upload_ref)
    assert str(first.upload_ref) == str(second.upload_ref)
    document_ref = list(harness.database.document)[0]
    assert list(harness.database.document) == [document_ref]
    assert len(harness.database.custody) == 1
    assert len(harness.database.locks) >= 2  # both completions took the same lock
    # A third completion after verification still reports the same single document.
    harness.publish(document_ref)
    third = await harness.complete(upload_ref)
    assert third.disposition == "verified" and third.document_ref == document_ref
    assert list(harness.database.document) == [document_ref]


async def test_initiate_is_idempotent_per_command_and_conflicts_otherwise() -> None:
    harness = Harness()
    first = await harness.initiate("3" * 32)
    replay = await harness.initiate("3" * 32)
    assert first == replay
    assert len(harness.database.upload) == 1
    other_case = Harness()
    other_case.native.heads[("document_policy", POLICY)] = policy_head(
        other_case.principal, case_ref=OTHER_CASE
    )
    receipt = await other_case.service.execute(
        "synthetic",
        operation="initiate_upload",
        resource_kind="case",
        resource_ref=OTHER_CASE,
        body=UploadInitiation(command_id="3" * 32, policy_ref=POLICY, document_type_ref=DOCTYPE),
        csrf="synthetic-csrf",
        origin="https://portal.example.test",
    )
    assert receipt.disposition == "awaiting_content"


async def test_stored_bytes_cannot_be_replaced_and_are_sealed_per_document() -> None:
    harness = Harness()
    upload_ref = await harness.initiate()
    await harness.deliver(upload_ref)
    with pytest.raises(IntakeError):
        await harness.deliver(upload_ref, b"PRIVATE_REPLACEMENT_CANARY")
    stored = harness.database.object[upload_ref]
    assert stored["content_sha256"] == content_digest(PHRASE)
    assert PHRASE not in bytes(stored["ciphertext"])  # type: ignore[operator]
    assert stored["nonce"] != harness.database.object.get("other", {}).get("nonce")


async def test_phi_route_serves_verified_bytes_and_never_a_quarantined_document() -> None:
    harness = Harness()
    _upload_ref, document_ref = await harness.upload()
    harness.publish(document_ref)
    raw = await harness.provider.open(document_ref)
    assert raw == PHRASE
    served = await harness.phi.read_content(
        "synthetic", document_ref=document_ref, freeze=lambda value: value
    )
    assert served == PHRASE
    harness.publish(document_ref, screening_result="quarantined")
    with pytest.raises(IntakeError):
        await harness.phi.read_content("synthetic", document_ref=document_ref, freeze=lambda value: value)


async def test_phi_service_refuses_a_foreign_or_unwired_plane() -> None:
    harness = Harness()
    stranger = Harness(principal(subject="other-subject"))
    stranger.resolver.settings.tenant = "other-tenant"
    with pytest.raises(IntakeError):
        PhiDocumentService(stranger.resolver, harness.authority, harness.provider)
    # A store of another plane is refused on the scope it declares, not on type alone.
    harness.provider.scope = DocumentScope(
        tenant="other-tenant-0000001", environment=harness.provider.scope.environment
    )
    with pytest.raises(IntakeError):
        PhiDocumentService(harness.resolver, harness.authority, harness.provider)


async def test_general_document_service_never_returns_bytes() -> None:
    harness = Harness()
    _upload_ref, document_ref = await harness.upload()
    harness.publish(document_ref)
    result = await harness.service.execute(
        "synthetic", operation="download", resource_kind="document", resource_ref=document_ref
    )
    assert result == document_ref
    if isinstance(result, bytes):  # a byte leak would be a custody breach
        raise AssertionError("bytes crossed the General-zone service")
