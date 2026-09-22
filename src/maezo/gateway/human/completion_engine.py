"""Concrete INTERIM completion adapters (DL-0049): engine REST client + audit chain link.

Both classes here are the shortcut made explicit, not a new architecture:

* `EngineRestTaskCompletion` posts `POST {engine}/task/{id}/complete` — the SAME route the
  declared-demo test page (`platform/testchannel/paginas/escalonamento.html`) already calls.
  It is NOT the D5 signed envelope: no Ed25519 purpose, no receipt, no outbox, no optimistic
  fence in the engine. That is precisely the debt DL-0049 registers.
* `PostgresCompletionAudit` writes the completion link into the tenant's EXISTING hash chain
  through `PostgresAuditSink.emit_once_status` — the same sink, lock, table and algorithm
  `PostgresHumanOutbox` uses for `human_command.intent` / `.result`. No second chain.

Neither is constructed by `create_app`. Only an explicit composition installs them, and the
interim gate is off by default in both `PortalSettings` and the read composition.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import asyncpg  # type: ignore[import-untyped]
import httpx

from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import PostgresAuditSink

from .completion import (
    COMPLETION_OUTCOMES,
    CompletionAck,
    CompletionAuditEntry,
    CompletionAuditSink,
    CompletionOutcome,
    DirectTaskCompletion,
    PseudonymizedNotes,
)
from .errors import GatewayRefusalError
from .models import Scope

#: Bounded transport allocation for the engine's answer, mirroring `read_transport.py`.
#: A technical wire limit, not a business bound.
MAX_RESPONSE = 65536

#: CIB SEVEN answers a successful task completion with `204 No Content`; `200` is accepted for
#: the `withVariablesInReturn` shape this client never asks for.
_COMPLETED = frozenset({200, 204})

#: The engine PROVED the task is gone or moved: the caller's read snapshot is stale.
_CONFLICT = frozenset({404, 409})

#: Plaintext is admitted ONLY to a private name. `engine-rest` has no authentication in this
#: distribution (`platform/engine_bootstrap/bootstrap.py` says so in a comment that has been
#: there since the bootstrap was written), so the network is the only boundary this call has:
#: sending the note over http to a public FQDN would publish it. `.internal`/`.local` and the
#: loopback/single-label service names are what ECS service discovery hands out.
_PRIVATE_SUFFIXES = (".internal", ".local")
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True, slots=True)
class InterimCompletionConfig:
    """The deployment's two explicit inputs for the interim path. No defaults.

    Naming this config is the whole activation, exactly like `decision_directory` for the
    decision plane: absent, the read composition installs no completion capability and
    `complete_task` refuses through the base port. Present, both values must be explicit —
    an invented timeout would be an invented operational interval.
    """

    engine_origin: str
    timeout_seconds: int


def _fixed_engine_origin(value: str) -> str:
    """The deployment's engine REST base. Never browser-selectable, never a redirect target."""
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme not in ("https", "http")
        or not host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/engine-rest")
        or parsed.path != urlsplit(value.rstrip("/")).path
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
        or any(c in value for c in "?#\\")
    ):
        raise GatewayRefusalError("production_capabilities_unavailable")
    if parsed.scheme == "http" and not (
        host in _LOOPBACK or "." not in host or host.endswith(_PRIVATE_SUFFIXES)
    ):
        raise GatewayRefusalError("production_capabilities_unavailable")
    return value


class EngineRestTaskCompletion(DirectTaskCompletion):
    """INTERIM: complete an ESCALATION user task straight over the engine's REST surface.

    The route and the two variable names are fixed here, in server-owned code. Nothing the
    browser sends chooses a URL, a variable name, a type or a second variable: the request body
    this client builds is exactly the BPMN contract's two mandatory outputs.

    Error posture: only `204`/`200` is completion and only `404`/`409` is a proven conflict.
    Every other answer — including a timeout, a `500` or an unreadable body — is UNCERTAIN and
    surfaces as a refusal, never as "rolled back" (`portal/engine/README.md`: "A transport
    timeout is uncertain, not evidence of rollback or completion"). The engine's response text
    is never read into an error: upstream strings must not become PHI-bearing API errors
    (`gateway/human/errors.py`).
    """

    def __init__(
        self,
        *,
        scope: Scope,
        origin: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise GatewayRefusalError("production_capabilities_unavailable")
        self.scope = Scope.model_validate(scope)
        self._origin = _fixed_engine_origin(origin).rstrip("/")
        # Registered byte-exactly in `scripts/ci/check_effect_chokepoint_fence.py`
        # `_HTTPX_SCOPED_SEAMS`. `verify=True` pins the system trust store for the `https`
        # case; `trust_env=False` keeps a proxy env var out of the path; redirects are off so
        # a completion cannot be forwarded elsewhere.
        self._http = httpx.AsyncClient(
            verify=True,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=timeout_seconds,
        )
        self._closed = False

    async def complete(
        self,
        *,
        task_id: str,
        resultado: CompletionOutcome,
        notes: PseudonymizedNotes,
        expected_task_revision: int,
    ) -> CompletionAck:
        if (
            self._closed
            or resultado not in COMPLETION_OUTCOMES
            or type(notes) is not PseudonymizedNotes
            or not notes.text.strip()
            or type(expected_task_revision) is not int
            or expected_task_revision < 0
            or not task_id
            or any(c in task_id for c in "/?#\\%")
        ):
            raise GatewayRefusalError("task_unavailable")
        body = json.dumps(
            {
                "variables": {
                    "resultado": {"value": resultado, "type": "String"},
                    "notas_resolucao": {"value": notes.text, "type": "String"},
                },
                "withVariablesInReturn": False,
            },
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            async with self._http.stream(
                "POST",
                f"{self._origin}/task/{task_id}/complete",
                content=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            ) as response:
                status = response.status_code
                read = 0
                async for chunk in response.aiter_bytes():
                    read += len(chunk)
                    if read > MAX_RESPONSE:
                        raise GatewayRefusalError("admission_unavailable")
        except GatewayRefusalError:
            raise
        except Exception:
            # Uncertain: the completion may already have committed. Never claim rollback.
            raise GatewayRefusalError("admission_unavailable") from None
        if status in _CONFLICT:
            raise GatewayRefusalError("revision_conflict")
        if status not in _COMPLETED:
            raise GatewayRefusalError("admission_unavailable")
        return CompletionAck(
            task_id=task_id,
            resultado=resultado,
            consumed_task_revision=expected_task_revision,
            completed_at=datetime.now(UTC),
        )

    async def close(self) -> None:
        self._closed = True
        await self._http.aclose()


class PostgresCompletionAudit(CompletionAuditSink):
    """The completion link, written by the sink that already owns this tenant's chain.

    Same construction as `PostgresHumanOutbox.__init__`: an empty DSN plus the composition's
    tenant-bound pool, so no connection string is read here and no pool is created. The
    `intent` link commits BEFORE the engine call and its dedup claim is the mutual-exclusion
    gate; the `result` link records the outcome afterwards. A failure to write the result does
    NOT unmake a committed completion — it leaves the reconciliation pending, exactly as the
    durable relay does (`gateway/human/README.md`).
    """

    def __init__(self, *, scope: Scope, pool: asyncpg.Pool) -> None:
        self.scope = Scope.model_validate(scope)
        self._sink = PostgresAuditSink("", self.scope.tenant, pool=pool)

    async def record(self, entry: CompletionAuditEntry) -> tuple[str, bool]:
        if type(entry) is not CompletionAuditEntry or entry.scope != self.scope:
            raise GatewayRefusalError("admission_unavailable")
        outcome = await self._sink.emit_once_status(
            completion_audit_record(entry, workload_ref=self.scope.workload_ref),
            dedup_key=entry.dedup_key,
        )
        return outcome.record_hash, outcome.deduped


def completion_audit_record(entry: CompletionAuditEntry, *, workload_ref: str) -> AuditRecord:
    """The ADR-0007 tuple for one interim completion link.

    `action` names the interim path explicitly (`portal_direct_completion.<phase>`) so an
    auditor can separate these rows from the `human_command.<phase>` rows the durable relay
    writes; `decision` is `REQUIRE_HUMAN` because a human closed this case, which is the same
    value `PostgresHumanOutbox._audit` records for every human command.

    `details` carries the three facts the mandate asks for and nothing else: WHO (pseudonymous
    principal/session refs + membership revision), on WHAT BASIS (the outcome, the digest of
    the pseudonymized note, the process/form pins and the candidate groups the authority
    matched) and AT WHICH REVISION (task/authority/evidence). No narrative, no subject, no
    beneficiary reference.
    """
    snap = entry.snapshot
    details: dict[str, object] = {
        "interim_decision_ref": "DL-0049",
        "principal_kind": "human",
        "principal_ref": entry.principal_ref,
        "session_ref": entry.session_ref,
        "membership_revision": entry.membership_revision,
        "workload_ref": workload_ref,
        "task_id": snap.task_id,
        "process_definition_key": snap.process_definition_key,
        "process_definition_id": snap.process_definition_id,
        "task_definition_key": snap.task_definition_key,
        "form_key": snap.form_key,
        "form_digest": snap.form_digest,
        "eligible_candidate_groups": list(snap.eligible_candidate_groups),
        "task_revision": snap.task_revision,
        "authority_revision": entry.authority_revision,
        "evidence_revision": snap.evidence_revision,
        "evidence_digest": snap.evidence_digest,
        "operation": "completion",
        "resultado": entry.resultado,
        "notas_resolucao_digest": entry.notes_digest,
    }
    if entry.outcome is not None:
        details["outcome"] = entry.outcome
    return AuditRecord(
        agent_id=workload_ref,
        tenant_id=entry.scope.tenant,
        agent_version="portal-direct-completion.v1",
        action=f"portal_direct_completion.{entry.phase}",
        decision="REQUIRE_HUMAN",
        details=details,
    )
