"""ADR-0054 genuine initial owner operation and same-cursor D consumer.

Composition requires the concrete frozen-source native readback and existing
qualified D OwnerInstaller/OwnerAuthority. No default authority, connections,
AWS metadata, admission or native registration is created here.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Protocol

from maezo.gateway import native_owner_store as store
from maezo.platform.engine_bootstrap import native_catalog_transition as transition
from maezo.platform.engine_bootstrap import native_owner_contracts as contract
from maezo.platform.engine_bootstrap.controller_storage import (
    Document,
    Refusal,
    canonical,
    decode64,
    digest,
    encode64,
    parse_wire,
    require,
    scalar,
    validate_native_qualification,
)


@dataclass(frozen=True, slots=True)
class NativeObservation:
    installed_wire: bytes
    native_catalog_bytes: bytes
    source_allocation: tuple[dict[str, Any], ...]


class NativeSourceReadback(Protocol):
    def validate_request(self, input_wire: bytes) -> None: ...
    def observe(self, cursor: store.Cursor) -> NativeObservation: ...


@dataclass(frozen=True, slots=True)
class QualificationResult:
    kind: str
    operation_id: str
    qualification_wire: bytes | None = None
    code: str | None = None

    def __post_init__(self) -> None:
        scalar("Uuid", self.operation_id)
        require(self.kind in {"COMMITTED", "HISTORICAL_COMMITTED", "REFUSED", "UNKNOWN"})
        if self.kind in {"COMMITTED", "HISTORICAL_COMMITTED"}:
            validate_native_qualification(parse_wire(self.qualification_wire))
            require(self.code is None)
        else:
            require(self.qualification_wire is None)
            scalar("RefusalCode", self.code)


class NativeQualificationOwner:
    def __init__(
        self, connection: Any, pins: store.StorePins, source: NativeSourceReadback, d_owner: Any
    ) -> None:
        require(connection is d_owner.connection, "PRECONDITION_MISMATCH")
        self.connection, self.pins, self.source, self.d_owner = connection, pins, source, d_owner

    def _birth(self, cursor: store.Cursor, q: dict[str, Any]) -> tuple[bytes, bytes]:
        row = store.one(
            cursor,
            """SELECT request_bytes,request_sha256,result_bytes,result_sha256
         FROM maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT" WHERE preparation_id=%s::uuid
         AND event='PREPARED' FOR UPDATE""",
            (q["d_preparation_id"],),
        )
        raw, birth = bytes(row[0]), bytes(row[2])
        require(digest(raw) == row[1] and digest(birth) == row[3], "PRECONDITION_MISMATCH")
        value = Document("OwnerPreparationResult", birth).value()
        require(
            value["installation_id"] == q["d_installation_id"] and value["scope"] == q["scope"],
            "PRECONDITION_MISMATCH",
        )
        require(value["request_sha256"] == digest(raw), "PRECONDITION_MISMATCH")
        return raw, birth

    def _catalog(self, cursor: store.Cursor, version: dict[str, Any]) -> tuple[bytes, bytes]:
        before = transition.retained_manifest(cursor, version)
        manifest = parse_wire(before)
        roles = [{key: r[key] for key in ("role_class", "role_name")} for r in manifest["roles"]]
        after = canonical(
            self.d_owner._catalog(
                cursor, roles, manifest["operational_principals"], version["installation_id"]
            )
        )
        return before, after

    def _observe(
        self,
        cursor: store.Cursor,
        q: dict[str, Any],
        session: tuple[Any, ...],
        fence: dict[str, Any],
        version: dict[str, Any],
        birth_wire: bytes,
        before: bytes,
        after: bytes,
    ) -> tuple[NativeObservation, bytes]:
        # This externally qualified port performs actual resource/strong-read
        # checks. Missing AWS owner inputs refuse; local vectors cannot replace it.
        self.source.validate_request(canonical(q))
        require(q["owner_installation_id"] == self.pins.installation_id, "PRECONDITION_MISMATCH")
        prepared_request, actual_birth = self._birth(cursor, q)
        require(actual_birth == birth_wire, "PRECONDITION_MISMATCH")
        self.d_owner.authority.replay(
            input_wire=prepared_request,
            retained_result_wire=birth_wire,
            fence_wire=canonical(fence),
            session=session,
        )
        observation = self.source.observe(cursor)
        installed = contract.installed_record(parse_wire(observation.installed_wire))
        require(
            installed == observation.installed_wire
            and digest(installed) == q["native_installation_receipt_sha256"],
            "PRECONDITION_MISMATCH",
        )
        require(
            decode64(parse_wire(installed)["preparation_receipt_bytes"]) == birth_wire,
            "PRECONDITION_MISMATCH",
        )
        require(
            digest(observation.native_catalog_bytes) == q["before_catalog_sha256"], "PRECONDITION_MISMATCH"
        )
        # Existing catalog_check refuses malicious expected manifests as well as
        # observed routes; never replace its role-specific allowlists.
        self.d_owner.check_native_catalog(before, after, session)
        previous = transition.verify_non_grant_invariants(cursor, version, before, after)
        transition.exact_initial_grant_union(
            previous, parse_wire(after)["object_grants"], list(observation.source_allocation)
        )
        store_wire = store.read_installation(cursor, self.pins)
        return observation, store_wire

    def qualify(self, input_wire: bytes) -> QualificationResult:
        q = contract.request(input_wire)
        operation = q["native_owner_operation_id"]
        self.source.validate_request(input_wire)
        require(q["owner_installation_id"] == self.pins.installation_id, "PRECONDITION_MISMATCH")
        cursor = None
        committing = False
        try:
            require(
                self.connection.autocommit is False and self.connection.info.transaction_status == 0,
                "PRECONDITION_MISMATCH",
            )
            cursor = self.connection.cursor()
            cursor.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
            cursor.execute("SET LOCAL search_path=pg_catalog,pg_temp")
            cursor.execute("SET LOCAL lock_timeout='1000ms'")
            cursor.execute("SET LOCAL statement_timeout='5000ms'")
            session = self.d_owner._session(cursor)
            context = {"scope": q["scope"], "installation_id": q["d_installation_id"]}
            # Lock D F -> G -> V before original PREPARED -> owner-store lock.
            fence, version, generation = self.d_owner._owner_context(
                cursor, context, session, q["generation_id"], q["login_oid"], native_transition=True
            )
            require(generation is not None, "PRECONDITION_MISMATCH")
            _, birth = self._birth(cursor, q)
            before, after = self._catalog(cursor, version)
            observation, store_wire = self._observe(cursor, q, session, fence, version, birth, before, after)
            retained = store.read_operation(cursor, operation)
            if retained is not None:
                require(retained[0] == input_wire, "REQUEST_CONFLICT")
                existing = contract.receipt(retained[1])
                require(
                    existing["installed_record_sha256"] == digest(observation.installed_wire)
                    and decode64(existing["native_catalog_bytes"]) == observation.native_catalog_bytes
                    and existing["owner_store_catalog_sha256"] == digest(store_wire),
                    "PRECONDITION_MISMATCH",
                )
                binding = retained[2]
            else:
                now = store.one(cursor, "SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint")[0]
                value = dict(
                    protocol="maezo.native-owner-initial-qualification-receipt.v1",
                    request_bytes=encode64(input_wire),
                    request_sha256=digest(input_wire),
                    installed_record=parse_wire(observation.installed_wire),
                    installed_record_sha256=digest(observation.installed_wire),
                    native_catalog_bytes=encode64(observation.native_catalog_bytes),
                    native_catalog_sha256=digest(observation.native_catalog_bytes),
                    d_before_catalog_bytes=encode64(before),
                    d_before_catalog_sha256=digest(before),
                    d_after_catalog_bytes=encode64(after),
                    d_after_catalog_sha256=digest(after),
                    owner_store_catalog_sha256=digest(store_wire),
                    owner_session_user=session[0],
                    owner_login_oid=session[2],
                    owner_effective_user=session[1],
                    owner_effective_oid=session[2],
                    recorded_before_commit_at_ms=now,
                )
                binding = store.append_operation(cursor, self.pins, canonical(value))
            # Revalidate same native custody and complete D catalogue before any
            # commit; this operation changes no D/native object, ACL or admission.
            final = self.source.observe(cursor)
            require(
                final == observation and self._catalog(cursor, version) == (before, after),
                "PRECONDITION_MISMATCH",
            )
            require(store.read_installation(cursor, self.pins) == store_wire, "PRECONDITION_MISMATCH")
            committing = True
            self.connection.commit()
            return QualificationResult("COMMITTED", operation, binding)
        except Exception as error:
            rollback_ok = cursor is None
            if cursor is not None:
                try:
                    self.connection.rollback()
                    rollback_ok = True
                except Exception:
                    pass
            unknown = committing or not rollback_ok
            return QualificationResult(
                "UNKNOWN" if unknown else "REFUSED",
                operation,
                code="PENDING_UNKNOWN"
                if unknown
                else error.code
                if isinstance(error, Refusal)
                else "UNAVAILABLE",
            )
        finally:
            if cursor is not None:
                with contextlib.suppress(Exception):
                    cursor.close()

    def verify(
        self,
        *,
        cursor: store.Cursor,
        qualification_wire: bytes,
        birth_wire: bytes,
        generation_wire: bytes,
        fence_wire: bytes,
        version_wire: bytes,
        before_catalog_wire: bytes,
        actual_catalog_wire: bytes,
        session: tuple[Any, ...],
    ) -> bytes:
        """D supplies its actual enlisted cursor; never connect, begin or commit."""
        require(getattr(cursor, "connection", None) is self.connection, "AUTH_REFUSED")
        outer = parse_wire(qualification_wire)
        validate_native_qualification(outer)
        supplied = decode64(outer["native_receipt_bytes"])
        value = contract.receipt(supplied)
        require(contract.qualification_from_receipt(supplied) == qualification_wire, "PRECONDITION_MISMATCH")
        q = contract.request(decode64(value["request_bytes"]))
        observation, store_wire = self._observe(
            cursor,
            q,
            session,
            parse_wire(fence_wire),
            parse_wire(version_wire),
            birth_wire,
            before_catalog_wire,
            actual_catalog_wire,
        )
        retained = store.read_operation(cursor, q["native_owner_operation_id"])
        require(
            retained == (decode64(value["request_bytes"]), supplied, qualification_wire),
            "PRECONDITION_MISMATCH",
        )
        require(
            observation.installed_wire == canonical(value["installed_record"])
            and observation.native_catalog_bytes == decode64(value["native_catalog_bytes"])
            and digest(store_wire) == value["owner_store_catalog_sha256"],
            "PRECONDITION_MISMATCH",
        )
        generation = parse_wire(generation_wire)
        require(
            generation["generation_id"] == q["generation_id"] and generation["login_oid"] == q["login_oid"],
            "PRECONDITION_MISMATCH",
        )
        return actual_catalog_wire

    def read_committed_operation(self, operation_id: str) -> QualificationResult:
        """Authenticated historical reconciliation only, never current admission."""
        scalar("Uuid", operation_id)
        cursor = None
        try:
            require(
                self.connection.autocommit is False and self.connection.info.transaction_status == 0,
                "PRECONDITION_MISMATCH",
            )
            cursor = self.connection.cursor()
            cursor.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
            cursor.execute("SET LOCAL search_path=pg_catalog,pg_temp")
            cursor.execute("SET LOCAL lock_timeout='1000ms'")
            cursor.execute("SET LOCAL statement_timeout='5000ms'")
            # Historical lookup does not acquire any D lock or validate current
            # admission. It cannot later escalate into a qualification transaction.
            store.read_installation(cursor, self.pins)
            retained = store.read_operation(cursor, operation_id)
            require(retained is not None, "UNAVAILABLE")
            assert retained is not None
            self.connection.rollback()
            return QualificationResult("HISTORICAL_COMMITTED", operation_id, retained[2])
        except Exception as error:
            if cursor is not None:
                with contextlib.suppress(Exception):
                    self.connection.rollback()
            return QualificationResult(
                "REFUSED", operation_id, code=error.code if isinstance(error, Refusal) else "UNAVAILABLE"
            )
        finally:
            if cursor is not None:
                with contextlib.suppress(Exception):
                    cursor.close()
