"""Exact existing v2 zero-output completion; catalog/authority are installed inputs."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from maezo.gateway.native_fetch.models import (
    closed,
    command,
    decode,
    encode,
    identity,
    integer,
    ref,
    refusal,
    sha,
    token,
)
from maezo.gateway.native_fetch.transport import NativeTLS

from .models import require
from .transport import NativeChannel, ProducerAcquisition


@dataclass(frozen=True, slots=True, repr=False)
class CompletionLease:
    purpose: str
    selection_digest: str
    binding: bytes = field(repr=False)
    capability_digest: str
    activation_ref: str
    database_incarnation: str
    not_before: datetime
    valid_until: datetime
    current: Callable[[], None] = field(repr=False)

    def guard(self, clock: Callable[[], datetime], selection: str, binding: bytes, purpose: str) -> None:
        self.current()
        require(
            self.purpose == purpose
            and self.selection_digest == selection
            and self.binding == binding
            and self.not_before <= clock() < self.valid_until
        )


class CompletionAuthority(ABC):
    @abstractmethod
    async def acquire(self, selection_digest: str, binding: bytes, purpose: str) -> CompletionLease:
        """Actual current D/native admission and separate outcome designation, never fetch authority."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True, repr=False)
class PreparedCompletion:
    body: bytes
    binding: bytes


@dataclass(frozen=True, slots=True, repr=False)
class CompletionOutcome:
    status: str
    receipt: bytes | None


class CompletionClient:
    def __init__(
        self,
        tls: NativeTLS,
        authority: CompletionAuthority,
        capability: bytes,
        catalog: bytes,
        catalog_digest: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        require(isinstance(authority, CompletionAuthority), "unavailable")
        cap = closed(
            decode(capability), "protocol identity target schema worker_id source_target acquisition_policy"
        )
        require(encode(cap) == capability and cap["protocol"] == "maezo.engine-capability.v2")
        require(identity(cap["identity"]) == identity(decode(tls.identity_document)))
        target = closed(cap["target"], "process_key process_version definition_id topic message")
        require(
            target["process_key"] == "SP-OP-AUTH-001"
            and target["topic"] == "operadora.auth.request_documents"
            and target["message"] == ""
            and cap["source_target"] is None
        )
        integer(target["process_version"], 1)
        token(target["definition_id"])
        token(cap["worker_id"])
        require(
            cap["acquisition_policy"]
            == {
                "resource_requirement": "required",
                "source_requirement": "none",
                "source_owner_binding_digest": None,
            }
        )
        schema = closed(
            cap["schema"],
            "schema_id operation process_key workload fields sources topic message "
            "correlation_fields all_matching error_codes source_process_key source_topic audit_actor "
            "read_projection",
        )
        require(
            schema["operation"] == "external_complete"
            and schema["workload"] == cap["identity"]["workload"]
            and all(schema[k] == target[k] for k in ("process_key", "topic", "message"))
            and schema["fields"]
            == schema["correlation_fields"]
            == schema["error_codes"]
            == schema["read_projection"]
            == []
            and schema["source_process_key"] == schema["source_topic"] == ""
            and schema["all_matching"] is False
        )
        token(schema["schema_id"])
        require(
            type(schema["audit_actor"]) is str and type(schema["sources"]) is list and bool(schema["sources"])
        )
        for declared_source in schema["sources"]:
            token(declared_source)
        require(len(set(schema["sources"])) == len(schema["sources"]))
        selected = closed(decode(catalog), "protocol schemas")
        require(
            sha(catalog) == catalog_digest
            and selected["protocol"] == "maezo.engine-schemas.v2"
            and selected["schemas"].count(schema) == 1,
            "unavailable",
        )
        self.capability, self.cap, self.authority = capability, cap, authority
        selection = encode(
            dict(
                schema="maezo.auth-document-completion-selection.v1",
                tls_selection_digest=tls.selection_digest,
                capability_digest=sha(capability),
                catalog_digest=catalog_digest,
            )
        )
        self.channel = NativeChannel(tls, selection, ((capability, sha(capability)),), clock)

    def prepare(self, acquired: ProducerAcquisition, command_id: str) -> PreparedCompletion:
        acquired.current()
        ref(command_id)
        original, row, snapshot = (
            decode(acquired.expected.binding),
            decode(acquired.row),
            decode(acquired.snapshot),
        )
        require(
            self.cap["identity"] == original["identity"]
            and self.cap["target"] == acquired.expected.profile.value()["target"]
            and self.cap["worker_id"] == row["worker_id"]
        )
        body = encode(
            dict(
                protocol="maezo.engine-operation.v2",
                capability_digest=sha(self.capability),
                operation="external_complete",
                process_key=self.cap["target"]["process_key"],
                resource_ref=row["id"],
                variables={},
                correlation={},
                all_matching=False,
                error_code="",
                topic=self.cap["target"]["topic"],
                message="",
                worker_id=self.cap["worker_id"],
                parameters={},
                source_ref="",
                command_id=command_id,
                activation_ref=original["activation_ref"],
                resource_acquisition={
                    "acquisition_ref": snapshot["acquisition_ref"],
                    "lease_revision": snapshot["lease_revision"],
                },
                source_acquisition=None,
            )
        )
        binding = encode(
            dict(
                original,
                operation="external_complete",
                capability_digest=sha(self.capability),
                command_id=command_id,
                request_digest=sha(body),
            )
        )
        acquired.current()
        return PreparedCompletion(body, binding)

    def validate(self, prepared: PreparedCompletion) -> None:
        b = command(decode(prepared.binding))
        q = closed(
            decode(prepared.body),
            "protocol capability_digest operation process_key resource_ref variables correlation "
            "all_matching error_code "
            "topic message worker_id parameters source_ref command_id activation_ref "
            "resource_acquisition source_acquisition",
        )
        require(
            q["protocol"] == "maezo.engine-operation.v2"
            and q["operation"] == b["operation"] == "external_complete"
            and q["capability_digest"] == b["capability_digest"] == sha(self.capability)
            and b["identity"] == self.cap["identity"]
            and b["request_digest"] == sha(prepared.body)
            and b["command_id"] == q["command_id"]
            and b["activation_ref"] == q["activation_ref"]
            and q["worker_id"] == self.cap["worker_id"]
            and all(q[k] == self.cap["target"][k] for k in ("process_key", "topic", "message"))
            and q["variables"] == q["correlation"] == q["parameters"] == {}
            and q["all_matching"] is False
            and q["error_code"] == q["source_ref"] == ""
            and q["source_acquisition"] is None
        )
        token(q["resource_ref"])
        ref(q["command_id"])
        a = closed(q["resource_acquisition"], "acquisition_ref lease_revision")
        ref(a["acquisition_ref"])
        integer(a["lease_revision"], 1)

    def _receipt(self, value: Any, prepared: PreparedCompletion) -> bytes:
        r = closed(
            value,
            "receipt_ref state command resource_ref_digest source_ref_digest resource_acquisition "
            "source_acquisition acquisitions",
        )
        q = decode(prepared.body)
        ref(r["receipt_ref"])
        require(
            r["state"] == "committed"
            and command(r["command"]) == decode(prepared.binding)
            and r["resource_ref_digest"] == sha(q["resource_ref"].encode())
            and r["source_ref_digest"] == sha(b"")
            and r["resource_acquisition"] == q["resource_acquisition"]
            and r["source_acquisition"] is None
            and type(r["acquisitions"]) is list
            and len(r["acquisitions"]) == 1
        )
        a = closed(r["acquisitions"][0], "role task_ref acquisition_ref lease_revision lock_expires_at state")
        require(
            a["role"] == "resource"
            and a["state"] == "closed"
            and a["task_ref"] == q["resource_ref"]
            and a["acquisition_ref"] == q["resource_acquisition"]["acquisition_ref"]
            and a["lease_revision"] == q["resource_acquisition"]["lease_revision"]
        )
        ref(a["acquisition_ref"])
        token(a["task_ref"])
        integer(a["lease_revision"], 1)
        integer(a["lock_expires_at"], 0)
        return encode(r)

    async def exchange(
        self, prepared: PreparedCompletion, *, recovery: bool, before_send: Callable[[], None]
    ) -> CompletionOutcome:
        self.validate(prepared)
        purpose = "outcome" if recovery else "complete"

        async def perform() -> tuple[CompletionOutcome, Callable[[], None]]:
            lease = await self.authority.acquire(self.channel.selection_digest, prepared.binding, purpose)

            def guard() -> None:
                self.channel.borrower.check()
                lease.guard(self.channel.clock, self.channel.selection_digest, prepared.binding, purpose)
                if not recovery:
                    original = decode(prepared.binding)
                    require(
                        lease.capability_digest == sha(self.capability)
                        and lease.activation_ref == original["activation_ref"]
                        and lease.database_incarnation == original["database_incarnation"]
                    )

            guard()
            if recovery:
                query = encode(
                    dict(
                        protocol="maezo.engine-outcome-query.v2",
                        recovery_capability_digest=lease.capability_digest,
                        reader_activation_ref=lease.activation_ref,
                        command=decode(prepared.binding),
                    )
                )
            else:
                query = prepared.body
            before_send()
            guard()
            status, raw = await self.channel.exchange(
                "/maezo-workload/v2/outcomes" if recovery else "/maezo-workload/v2/operations", query, guard
            )
            guard()
            value = decode(raw)
            if refusal(value, status):
                result = CompletionOutcome("unavailable", None)
            elif recovery:
                closed(
                    value,
                    "protocol recovery_capability_digest reader_activation_ref query_digest command "
                    "status receipt",
                )
                require(
                    value["protocol"] == "maezo.engine-outcome.v2"
                    and value["query_digest"] == sha(query)
                    and value["recovery_capability_digest"] == lease.capability_digest
                    and value["reader_activation_ref"] == lease.activation_ref
                    and value["command"] == decode(prepared.binding)
                )
                require(
                    (status, value["status"])
                    in {(200, "committed"), (200, "not_observed"), (409, "conflict"), (503, "unavailable")}
                )
                receipt = (
                    self._receipt(value["receipt"], prepared) if value["status"] == "committed" else None
                )
                require(receipt is not None or value["receipt"] is None)
                result = CompletionOutcome(value["status"], receipt)
            else:
                closed(value, "protocol command status receipt result")
                require(
                    value["protocol"] == "maezo.engine-result.v2"
                    and value["command"] == decode(prepared.binding)
                )
                require(
                    (status, value["status"])
                    in {(200, "executed"), (200, "duplicate"), (409, "conflict"), (503, "unavailable")}
                )
                receipt = (
                    self._receipt(value["receipt"], prepared)
                    if value["status"] in {"executed", "duplicate"}
                    else None
                )
                if value["status"] == "executed":
                    require(
                        value["result"]
                        == {"value": {"applied": True}, "acquisitions": value["receipt"]["acquisitions"]}
                    )
                else:
                    require(value["result"] is None)
                require(receipt is not None or value["receipt"] is None)
                result = CompletionOutcome(value["status"], receipt)
            guard()
            return result, guard

        result, guard = await self.channel.borrower.run_async(perform)
        guard()
        return result
