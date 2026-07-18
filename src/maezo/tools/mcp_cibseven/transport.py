"""CIB Seven process-instance transport — agent-directed start/correlate (ADR-0001, T1.11).

ADR-0001: agents start/advance BPMN processes via REST (agent -> engine, the residual
direction `tools/workers/harness.py` does NOT cover — that module is exclusively the
engine -> worker leg, fetch-and-lock). Before T1.11 nothing in `src/` let an agent graph
start a process instance idempotently by business key; this module closes that gap so
Helena/Rafael can call `SP-OP-ESCALATION-001`/`SP-OP-AUTH-001` for real.

Mirrors `maezo.tools.workers.dmn_transport`'s triple shape (Protocol + real + Fake) exactly,
ported from the v1 donor (`Maezo-Healthcare-Plan src/maezo/tools/mcp_cibseven/server.py:89-398`,
READ-ONLY reference) with the higher-level `CibSevenServer` (tool-registration/allowlist/metrics
wrapper) left behind on purpose — v2 has no `ToolRegistry`/PEP-gateway wiring for agent tool
calls yet (T2.4 gap), so this module ships only the transport primitives an agent graph needs
directly: idempotent start (business-key dedup, ADR contract convention `{PREFIX}-{tenant}-{id}`),
message correlation, and status lookup.

Idempotency (the reason `start_process_instance` alone is not enough): every SP-OP-* contract
this repo ships declares "one active instance per business key" (SP-OP-ESCALATION-001 §Business
key, SP-OP-AUTH-001 §Business key). `CibSevenServer.start_process` below is the enforcement
point — it ALWAYS calls `find_active_instance` before `start_process_instance`, returning the
existing instance untouched (`already_existed=True`) rather than risking a duplicate escalation/
authorization for the same conversation/guia on retry or redelivery.

Error semantics: `CibSevenError` (bare `RuntimeError`) for unreachable/non-2xx — transient,
mirrors `DmnEvaluationError`'s classification story so a caller wrapping this in a worker-style
boundary gets the same engine-retried treatment `tools/workers/harness.py`'s `_handle` already
gives every unclassified `RuntimeError`. `ProcessNotFoundError` (a `CibSevenError` subclass) is
raised by `get_process_status`/`correlate` when no instance matches — never a silent `None`
masquerading as "nothing to do".
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

# T-C2 provenance-chokepoint fence (ADR-0007, docs/design/audit-emit-path-wiring.md SHOULD-FIX 3).
# Deliberate, load-bearing coupling — the fence makes a durable, PHI-safe ADR-0007 provenance
# record a STRUCTURAL precondition of every process start, so `start_process_idempotent` must
# reach the audit-record type + the one-way PHI redactor. This is the ONLY dependency this module
# takes beyond its self-contained transport primitives; both imports are cycle-free (neither
# `maezo.gateway` nor `maezo.tools.workers.phi_vars` imports `mcp_cibseven` at load time) and
# neither pulls `asyncpg` (the durable sink is duck-typed via the `AuditStartSink` Protocol below,
# concrete-typed only under TYPE_CHECKING) — so a consumer of the read/transport primitives pays
# no new heavyweight cost.
from maezo.gateway.audit import AuditRecord, hash_input
from maezo.tools.workers.phi_vars import redact_phi_vars

logger = structlog.get_logger(__name__)

# Java `int32` (java.lang.Integer) bounds — identical rationale/constants as
# `dmn_transport.py`/`tools/workers/harness.py` (ADR-0018 part 2). Duplicated (not imported) so
# this module has zero dependency on those modules, matching their own stated convention.
_JAVA_INT32_MIN = -(2**31)
_JAVA_INT32_MAX = 2**31 - 1


class CibSevenError(RuntimeError):
    """Engine unreachable or non-2xx — TRANSIENT (mirrors `DmnEvaluationError`'s classification)."""


class ProcessNotFoundError(CibSevenError):
    """No process instance (active or historic) matches the given business key."""


class CibSevenVariableDecodeError(CibSevenError):
    """A `Json`-typed process variable's `value` was not valid JSON — FAIL-CLOSED.

    T1.1 sibling of the `tools/workers/harness.py` `fetch_and_lock` decode defect (PR #75):
    same wire shape, same fail-closed posture, adapted to `get_process_status`'s single-
    instance read (there is no engine task to report `failure(retries=0)` against here, unlike
    the harness's fetch-and-lock batch loop — this call simply raises instead of silently
    returning the raw JSON string or dropping the offending variable)."""


@dataclass(frozen=True, slots=True)
class ProcessInstance:
    """A process instance as returned by `start_process_instance`/`find_active_instance`."""

    instance_id: str
    process_key: str
    business_key: str
    state: str  # "ACTIVE" | "COMPLETED" | "SUSPENDED" | "EXTERNALLY_TERMINATED"
    already_existed: bool = False  # True on an idempotent hit (pre-existing active instance)


@dataclass(frozen=True, slots=True)
class ProcessStatus:
    """Status of a process instance, resolved by business key."""

    instance_id: str
    process_key: str
    business_key: str
    state: str
    variables: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CibSevenTransport(Protocol):
    """Agent-directed engine transport seam — injectable for unit tests (`FakeCibSevenTransport`)
    without faking the engine in integration tests (ADR-0011)."""

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None: ...

    async def start_process_instance(
        self,
        process_key: str,
        business_key: str,
        variables: dict[str, Any],
    ) -> ProcessInstance: ...

    async def correlate_message(
        self,
        message_name: str,
        business_key: str,
        variables: dict[str, Any],
        *,
        correlation_keys: dict[str, Any] | None = None,
        all_matching: bool = False,
    ) -> None: ...

    async def get_process_status(self, business_key: str) -> ProcessStatus: ...

    async def close(self) -> None: ...


def _to_camunda_vars(variables: dict[str, Any]) -> dict[str, Any]:
    """Type process variables for the engine wire format — `Long` for ints outside int32.

    Ported verbatim from the v1 donor's `_to_camunda_vars` (`mcp_cibseven/server.py:161-177`) —
    same rule as `dmn_transport.py`'s helper, duplicated (not imported) per this module's
    zero-dependency stance.
    """
    camunda_vars: dict[str, Any] = {}
    for k, v in variables.items():
        if isinstance(v, dict) and "value" in v:
            camunda_vars[k] = v  # already engine-shaped — pass through unchanged
        elif isinstance(v, bool):
            camunda_vars[k] = {"value": v, "type": "Boolean"}
        elif isinstance(v, int):
            fits_int32 = _JAVA_INT32_MIN <= v <= _JAVA_INT32_MAX
            camunda_vars[k] = {"value": v, "type": "Integer" if fits_int32 else "Long"}
        elif isinstance(v, float):
            camunda_vars[k] = {"value": v, "type": "Double"}
        elif isinstance(v, dict | list):
            camunda_vars[k] = {"value": json.dumps(v, ensure_ascii=False, default=str), "type": "Json"}
        elif v is None:
            camunda_vars[k] = {"value": None, "type": "String"}
        else:
            camunda_vars[k] = {"value": str(v), "type": "String"}
    return camunda_vars


def _from_camunda_var(entry: Mapping[str, Any]) -> Any:
    """Decode ONE inbound CIB Seven / Camunda variable entry (`{"value": ..., "type": ...}`)
    into the Python value a caller actually consumes. Symmetric read-side counterpart to
    `_to_camunda_vars` above.

    Ported verbatim (posture, not import — this module's zero-dependency stance, see
    `_to_camunda_vars`'s docstring) from `tools/workers/harness.py`'s `_from_camunda_var`
    (T1.1 harness fix, PR #75). **Load-bearing**: every wire type except `Json` already
    arrives value-ready — `Integer`/`Long`/`Double`/`Boolean`/`String`/a `null` value
    deserialize straight off the REST response body, so `.get("value")` alone was correct for
    them. `Json` is the ONE type that needs a second decode: CIB Seven/Camunda 7's REST
    contract returns a `Json`-typed variable's `value` as a JSON STRING (the structured
    content re-encoded — the exact mirror of what `_to_camunda_vars` WRITES on
    `start_process_instance`/`correlate_message`), never as an already-parsed object/array.
    Without this, list/dict process variables (e.g. SP-OP-CONTAS-001's `linhas_conta_refs`)
    read via `get_process_status` arrive as a raw string instead of a Python `list`/`dict`.

    Raises `json.JSONDecodeError` (a `ValueError` subclass) on malformed JSON content, and
    `TypeError`/`AttributeError` on a malformed entry shape — both left for the caller
    (`get_process_status`) to fail-closed (module docstring: `CibSevenVariableDecodeError`,
    never a silent raw-string passthrough). A `Json`-typed variable with `value: null` (unset)
    decodes to `None`, never attempted through `json.loads` (which would raise `TypeError` on
    a non-str/bytes argument).
    """
    value = entry.get("value")
    if entry.get("type") == "Json" and isinstance(value, str):
        return json.loads(value)
    return value


class CibSevenHttpTransport:
    """Real transport: CIB Seven REST `/process-instance`, `/process-definition/key/{key}/start`,
    `/message`, `/history/process-instance` (ADR-0001). Ported from the v1 donor's
    `CibSevenHttpTransport` (`mcp_cibseven/server.py:148-317`).

    A fresh `httpx.AsyncClient` is held for the lifetime of this instance (unlike
    `CibSevenDmnTransport`'s per-call client) — callers that construct this once per process
    (daemon-lifetime) must call `close()` on shutdown; short-lived callers (a single request
    handler) may also just let the instance be garbage-collected after `close()`.
    """

    def __init__(self, base_url: str, *, auth_token: str | None = None, timeout: float = 15.0) -> None:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        """Query for an ACTIVE instance with this business key (idempotency check)."""
        try:
            resp = await self._client.get(
                "/process-instance", params={"businessKey": business_key, "active": "true"}
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CibSevenError(
                f"CIB Seven process-instance query failed [{exc.response.status_code}] for "
                f"business_key `{business_key}`"
            ) from exc
        except httpx.RequestError as exc:
            raise CibSevenError(
                f"CIB Seven unreachable querying business_key `{business_key}`: {exc}"
            ) from exc

        items = resp.json()
        if not items:
            return None
        item = items[0]
        return ProcessInstance(
            instance_id=str(item["id"]),
            process_key=str(item.get("processDefinitionKey", "")),
            business_key=business_key,
            state="ACTIVE",
            already_existed=True,
        )

    async def start_process_instance(
        self,
        process_key: str,
        business_key: str,
        variables: dict[str, Any],
    ) -> ProcessInstance:
        """Unconditionally POST a new instance start. Callers wanting idempotency call
        `find_active_instance` first (`CibSevenServer.start_process` below does this)."""
        payload = {"businessKey": business_key, "variables": _to_camunda_vars(variables)}
        try:
            resp = await self._client.post(f"/process-definition/key/{process_key}/start", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CibSevenError(
                f"CIB Seven start_process_instance failed [{exc.response.status_code}] for "
                f"`{process_key}` (business_key `{business_key}`): {exc.response.text[:300]}"
            ) from exc
        except httpx.RequestError as exc:
            raise CibSevenError(f"CIB Seven unreachable starting `{process_key}`: {exc}") from exc

        data = resp.json()
        return ProcessInstance(
            instance_id=str(data["id"]),
            process_key=process_key,
            business_key=business_key,
            state=str(data.get("state", "ACTIVE")),
            already_existed=False,
        )

    async def correlate_message(
        self,
        message_name: str,
        business_key: str,
        variables: dict[str, Any],
        *,
        correlation_keys: dict[str, Any] | None = None,
        all_matching: bool = False,
    ) -> None:
        """Correlate `message_name` by `business_key` (single-instance, agent-directed default)
        or by `correlation_keys` (process-variable correlation, potentially fan-out to multiple
        instances — `all_matching=True` delegates the fan-out to the engine's own `POST /message`
        `all` flag rather than the caller enumerating instances)."""
        payload: dict[str, Any] = {
            "messageName": message_name,
            "processVariables": _to_camunda_vars(variables),
        }
        if business_key:
            payload["businessKey"] = business_key
        if correlation_keys:
            payload["correlationKeys"] = _to_camunda_vars(correlation_keys)
        if all_matching:
            payload["all"] = True
        try:
            resp = await self._client.post("/message", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CibSevenError(
                f"CIB Seven correlate_message failed [{exc.response.status_code}] for `{message_name}`"
            ) from exc
        except httpx.RequestError as exc:
            raise CibSevenError(f"CIB Seven unreachable correlating `{message_name}`: {exc}") from exc

    async def get_process_status(self, business_key: str) -> ProcessStatus:
        instance = await self.find_active_instance(business_key)
        if instance is None:
            try:
                resp = await self._client.get(
                    "/history/process-instance", params={"processInstanceBusinessKey": business_key}
                )
                resp.raise_for_status()
                history = resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                raise ProcessNotFoundError(f"business_key `{business_key}` not found") from exc

            if not history:
                raise ProcessNotFoundError(f"business_key `{business_key}` not found")
            item = history[0]
            return ProcessStatus(
                instance_id=str(item["id"]),
                process_key=str(item.get("processDefinitionKey", "")),
                business_key=business_key,
                state=str(item.get("state", "UNKNOWN")),
                variables={},
            )

        try:
            # `deserializeValues=false` is LOAD-BEARING (live-caught, T1.1): CIB Seven's
            # `GET /process-instance/{id}/variables` defaults to SERVER-SIDE deserializing a
            # `Json`-typed variable's value — the response's `value` is then a Jackson
            # `JsonNode` bean reflection (`{"array": bool, "nodeType": ..., "object": bool,
            # ...}`), NOT the actual JSON content and NOT a string `_from_camunda_var` can
            # decode. This is a DIFFERENT default than `POST /external-task/fetchAndLock`
            # (`tools/workers/harness.py`, PR #75), which already returns a `Json` variable's
            # `value` as the raw JSON string with no query param needed — the two REST
            # resources have different serialization defaults for the same `Json` type.
            # `deserializeValues=false` makes THIS endpoint match that shape (confirmed live
            # against cibseven:2.1.0: `String`/`Integer`/other scalar types are unaffected by
            # the flag — only `Json`/`Object`-family types change).
            resp_vars = await self._client.get(
                f"/process-instance/{instance.instance_id}/variables",
                params={"deserializeValues": "false"},
            )
            resp_vars.raise_for_status()
            raw_vars: dict[str, Any] = resp_vars.json()
        except (httpx.HTTPStatusError, httpx.RequestError):
            variables_out = {}
        else:
            try:
                variables_out = {k: _from_camunda_var(v) for k, v in raw_vars.items()}
            except (ValueError, TypeError, AttributeError) as exc:
                # Fail-closed (mirrors `tools/workers/harness.py`'s ValueError-family decode
                # convention, PR #75): a malformed `Json`-typed variable is bad/immutable
                # input from the engine — NEVER hand it to the caller as the raw undecoded
                # string (silent corruption) and NEVER silently drop it into `{}` either.
                raise CibSevenVariableDecodeError(
                    f"CIB Seven returned a malformed Json-typed variable for business_key "
                    f"`{business_key}` (instance `{instance.instance_id}`): {exc}"
                ) from exc

        return ProcessStatus(
            instance_id=instance.instance_id,
            process_key=instance.process_key,
            business_key=business_key,
            state=instance.state,
            variables=variables_out,
        )

    async def close(self) -> None:
        await self._client.aclose()


class FakeCibSevenTransport:
    """Labeled test double. NEVER imported by production code (mirrors `FakeDmnTransport`)."""

    def __init__(self) -> None:
        self._instances: dict[str, ProcessInstance] = {}
        self._variables: dict[str, dict[str, Any]] = {}
        self._correlate_calls: list[dict[str, Any]] = []

    def seed_instance(self, instance: ProcessInstance, *, variables: dict[str, Any] | None = None) -> None:
        """Pre-load an instance (simulates a pre-existing active instance for idempotency
        tests). `variables`, if given, is what `get_process_status` returns for it —
        already-decoded Python objects (this fake never wire-encodes/decodes, mirroring
        `FakeWorkerTransport`/`FakeDmnTransport`'s pure-Python-double posture; a caller that
        needs to prove the wire-level `Json` decode uses `CibSevenHttpTransport` against a
        mocked `httpx` client instead, per `test_mcp_cibseven_transport.py`)."""
        self._instances[instance.business_key] = instance
        self._variables[instance.business_key] = dict(variables) if variables else {}

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        inst = self._instances.get(business_key)
        if inst and inst.state == "ACTIVE":
            return inst
        return None

    async def start_process_instance(
        self,
        process_key: str,
        business_key: str,
        variables: dict[str, Any],
    ) -> ProcessInstance:
        inst = ProcessInstance(
            instance_id=f"fake-{business_key}",
            process_key=process_key,
            business_key=business_key,
            state="ACTIVE",
            already_existed=False,
        )
        self._instances[business_key] = inst
        self._variables[business_key] = dict(variables)
        return inst

    async def correlate_message(
        self,
        message_name: str,
        business_key: str,
        variables: dict[str, Any],
        *,
        correlation_keys: dict[str, Any] | None = None,
        all_matching: bool = False,
    ) -> None:
        self._correlate_calls.append(
            {
                "message_name": message_name,
                "business_key": business_key,
                "variables": dict(variables),
                "correlation_keys": dict(correlation_keys) if correlation_keys else {},
                "all_matching": all_matching,
            }
        )

    async def get_process_status(self, business_key: str) -> ProcessStatus:
        inst = self._instances.get(business_key)
        if inst is None:
            raise ProcessNotFoundError(f"business_key `{business_key}` not found")
        return ProcessStatus(
            instance_id=inst.instance_id,
            process_key=inst.process_key,
            business_key=business_key,
            state=inst.state,
            # Post-fix contract (T1.1): callers see decoded Python objects, matching
            # `CibSevenHttpTransport.get_process_status`'s now-decoded `Json` variables — this
            # fake stores them decoded already (it never wire-encodes), never `{}`.
            variables=dict(self._variables.get(business_key, {})),
        )

    @property
    def correlate_calls(self) -> list[dict[str, Any]]:
        return list(self._correlate_calls)

    async def close(self) -> None:
        return None


@runtime_checkable
class AuditStartSink(Protocol):
    """The exactly-once emit seam `start_process_idempotent` requires (T-C2 fence).

    The production implementation is `maezo.gateway.audit_postgres.PostgresAuditSink`, which
    satisfies this Protocol structurally (durable, fail-closed, per-tenant advisory-locked, atomic
    dedup-claim + chain-insert — T-A). Typed as a Protocol rather than the concrete class so the
    chokepoint takes no `asyncpg` import and unit tests can inject a recording fake, while a real
    agent daemon injects the real durable sink.
    """

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str: ...


@dataclass(frozen=True, slots=True)
class AgentDecisionProvenance:
    """The ADR-0007 decision-provenance an agent MUST carry to start a process (T-C2 fence).

    This is the LLM-decision provenance the deterministic worker chokepoint (T-C) structurally
    leaves null: `model_id`/`prompt_version`/`decision_basis` for the autonomous agent decision
    that led to the process start. It is a REQUIRED argument of `start_process_idempotent`, so an
    agent physically cannot start a process without supplying it (a missing tuple is a call-time
    error, never a silent audit gap — the drift-proof property the fence buys over enumerating the
    call sites).

    PHI discipline (docs/design/audit-emit-path-wiring.md §3.3): `decision_basis` MUST be a curated
    allowlist of bounded routing/enum tokens ONLY (`route`, `desfecho`, `faixa_valor`,
    `grupo_aprovador`, `motivo_*`, `severidade`, ...) — NEVER raw clinical free text, and never a
    passthrough of `state`/`variables`. The chokepoint additionally runs it through the one-way
    `redact_phi_vars` backstop and binds the raw start `variables` via a one-way `input_sha256`
    (never persisting them), so a resolvable business key / clinical value never reaches the durable
    chain even if a caller's allowlist slips.

    Attributes:
        agent_id: Stable agent identity (ADR-0007 `agent_id`), e.g. ``"andre"`` — NOT an ephemeral
            per-replica id.
        agent_version: Emitting agent version (ADR-0007 "sob-qual-versao").
        tenant_id: Owning tenant — also the schema the durable sink writes to; used to build the
            per-tenant dedup key.
        decision_basis: Curated, PHI-safe routing/enum tokens (§3.3). REQUIRED and explicit.
        model_id: LLM model id that drove the decision, when one did (else None — honest, mirrors
            the worker path).
        prompt_version: Prompt-template version that drove the decision, when applicable.
        dmn_versions: DMN decision-definition version tokens consulted, ``{key: {...}}`` (class
            tokens only, never clinical values). Defaults to empty.
    """

    agent_id: str
    agent_version: str
    tenant_id: str
    decision_basis: dict[str, Any]
    model_id: str | None = None
    prompt_version: str | None = None
    dmn_versions: dict[str, Any] = field(default_factory=dict)


# ADR-0007 "tool"/"decision" values for the agent process-start effect. `action` names the effect
# and its target process definition (a class token); `decision` is a fixed honest label — PEP is
# unwired at runtime (design §2.1 site 3), so no ALLOW/DENY verdict is captured here; this record
# attests "the agent committed to starting this process instance". The agent's own routing verdict
# lives in `decision_basis`.
_START_ACTION_PREFIX = "start_process"
_START_DECISION = "START_PROCESS"


def build_start_audit_record(
    provenance: AgentDecisionProvenance,
    *,
    process_key: str,
    business_key: str,
    variables: dict[str, Any],
) -> AuditRecord:
    """Build the PHI-safe ADR-0007 process-start record from `provenance` + the start inputs.

    PHI discipline (design §3.3): the caller-curated `decision_basis` passes through the one-way
    `redact_phi_vars` backstop; the raw start `variables` (which legitimately carry resolvable
    business identifiers — e.g. a `numero_guia_tiss`-derived business key or payment order id) are
    bound by a one-way `input_sha256` and NEVER stored in the clear. `business_key` itself is NOT
    placed in the durable chain (§3.3 "no resolvable business identifiers"); it lives only in the
    dedup key (a sibling table), mirroring how the engine already stores its own business key.
    `process_key` is a decision-definition class token, safe in the clear (it is also the `action`).
    """
    details: dict[str, Any] = {
        **redact_phi_vars(provenance.decision_basis),
        "process_key": process_key,
        "input_sha256": hash_input(variables),
    }
    return AuditRecord(
        agent_id=provenance.agent_id,
        tenant_id=provenance.tenant_id,
        agent_version=provenance.agent_version,
        action=f"{_START_ACTION_PREFIX}:{process_key}",
        decision=_START_DECISION,
        details=details,
        dmn_versions=dict(provenance.dmn_versions),
        model_id=provenance.model_id,
        prompt_version=provenance.prompt_version,
    )


def start_dedup_key(tenant_id: str, process_key: str, business_key: str) -> str:
    """The exactly-once dedup key for an agent process start (design SHOULD-FIX 3 / §4.3).

    ``f"{tenant}:start:{process_key}:{business_key}"`` — matches each SP-OP-* contract's "one
    active instance per business key" invariant, so a re-run dedupes BOTH the start effect (via
    `find_active_instance`) AND the audit row (via `emit_once`). Stable across re-delivery/retry
    because it is derived from the business key, not a per-call id.
    """
    return f"{tenant_id}:start:{process_key}:{business_key}"


async def start_process_idempotent(
    transport: CibSevenTransport,
    *,
    process_key: str,
    business_key: str,
    variables: dict[str, Any],
    audit_sink: AuditStartSink,
    provenance: AgentDecisionProvenance,
) -> ProcessInstance:
    """Idempotent, ADR-0007-audited process start — the SINGLE agent-side effect chokepoint (T-C2).

    This is the ONE call site agent graphs use to start a BPMN process — never
    `start_process_instance` directly. `audit_sink` and `provenance` are REQUIRED (no defaults):
    the fence is structural, so a caller that omits either fails LOUDLY at call time (a
    `TypeError`), never silently starting an un-audited process. Because every present and future
    agent start funnels through here, the ADR-0007 provenance coverage cannot drift the way an
    enumerated list of call sites did (design SHOULD-FIX 3).

    Fail-closed ordering (design §4.2 — audit BEFORE effect):
      1. Emit the durable, PHI-safe provenance record via `emit_once` (idempotent on
         `start_dedup_key`). This RAISES `AuditPersistenceError` on any durability failure — and,
         being neither a `CibSevenError` nor caught by the agents' `except CibSevenError`, it
         propagates and fails the turn. No process is ever started without a preceding durable
         audit row.
      2. `find_active_instance` — an active hit is returned unchanged (`already_existed=True`),
         never re-started (the "one active instance per business key" invariant).
      3. Otherwise start the instance.

    Idempotency & no-double: on re-delivery the same business key yields `emit_once` →
    ``ALREADY_AUDITED`` (no second chain link) AND `find_active_instance` → the existing instance
    (no second start). The process start is itself business-key-idempotent, so it is P1-safe (no
    double-effect); the emit dedup closes the double-audit window. Emitting slightly ahead of the
    (idempotent) start is the fail-closed choice: the record attests the agent's decision to start;
    the start is the mechanical realization the idempotency guard makes safe to repeat.
    """
    # 1. FAIL-CLOSED durable provenance BEFORE any engine effect (ADR-0007 invariant).
    record = build_start_audit_record(
        provenance, process_key=process_key, business_key=business_key, variables=variables
    )
    await audit_sink.emit_once(
        record, dedup_key=start_dedup_key(provenance.tenant_id, process_key, business_key)
    )

    # 2. Idempotency: an active instance is returned unchanged, never double-started.
    existing = await transport.find_active_instance(business_key)
    if existing is not None:
        logger.info(
            "cibseven_start_idempotent_hit",
            process_key=process_key,
            business_key=business_key,
            instance_id=existing.instance_id,
        )
        return existing

    # 3. Start the instance (the effect — gated behind the durable audit above).
    instance = await transport.start_process_instance(process_key, business_key, variables)
    logger.info(
        "cibseven_process_started",
        process_key=process_key,
        business_key=business_key,
        instance_id=instance.instance_id,
    )
    return instance
