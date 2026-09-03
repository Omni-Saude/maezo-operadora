"""CIB Seven process-instance transport — agent-directed start/correlate (ADR-0001, T1.11).

ADR-0001: agents start/advance BPMN processes via REST (agent -> engine, the residual
direction `tools/workers/harness.py` does NOT cover — that module is exclusively the
engine -> worker leg, fetch-and-lock). Before T1.11 nothing in `src/` let an agent graph
start a process instance idempotently by business key; this module closes that gap so
Helena/Rafael can call `SP-OP-ESCALATION-001`/`SP-OP-AUTH-001` for real.

Mirrors `maezo.tools.workers.dmn_transport`'s triple shape (Protocol + real + Fake) exactly,
ported from the v1 donor (`Maezo-Healthcare-Plan src/maezo/tools/mcp_cibseven/server.py:89-398`,
READ-ONLY reference) with the donor's higher-level `CibSevenServer` (tool-registration/allowlist/
metrics wrapper) left behind ON PURPOSE and NEVER PORTED — it does not exist in v2 and must not be
looked for here (`tests/unit/tools/test_mcp_cibseven.py:46-48` asserts it is neither importable
nor in `__all__`). This module ships only the transport primitives an agent graph needs directly:
idempotent start (business-key dedup, ADR contract convention `{PREFIX}-{tenant}-{id}`), message
correlation, and status lookup.

WHERE THE ENFORCEMENT ACTUALLY LIVES (design finding R-6 — this docstring used to advertise the
deleted class as the enforcement point, which is how an auditor reading the effect plane from
here would conclude a tool allowlist existed when none did). Two real layers, both in this tree:
  1. `start_process_idempotent` (:1052) — the ONE sanctioned start chokepoint: audit-before-effect
     (ADR-0007/T-C2), business-key idempotency, and the strict-family dedup gate. It is enforced
     repo-wide by the AST fence `scripts/ci/check_start_process_fence.py`, which fails the build on
     any direct `start_process_instance(...)` call outside a pinned allowlist.
  2. The Onda-1 seam layer (`maezo.gateway.seams.cibseven.GatedCibSevenTransport`, built only by
     `maezo.gateway.tool_registry`) — the per-call effect chokepoint for `correlate_message`,
     `get_process_status` and the two `find_*` reads. It ships INERT (`action-approvals.yaml`
     is `status: DRAFT` / `modo: shadow`), so today it decides and records without ever blocking.

Idempotency (the reason `start_process_instance` alone is not enough): every SP-OP-* contract
this repo ships declares "one active instance per business key" (SP-OP-ESCALATION-001 §Business
key, SP-OP-AUTH-001 §Business key). `start_process_idempotent` (:1052) is the enforcement
point — it ALWAYS calls `find_active_instance` before `start_process_instance`, returning the
existing instance untouched (`StartOutcome.ALREADY_ACTIVE`) rather than risking a duplicate
escalation/authorization for the same conversation/guia on retry or redelivery.

Error semantics: `CibSevenError` (bare `RuntimeError`) for unreachable/non-2xx — transient,
mirrors `DmnEvaluationError`'s classification story so a caller wrapping this in a worker-style
boundary gets the same engine-retried treatment `tools/workers/harness.py`'s `_handle` already
gives every unclassified `RuntimeError`. `ProcessNotFoundError` (a `CibSevenError` subclass) is
raised by `get_process_status`/`correlate` when no instance matches — never a silent `None`
masquerading as "nothing to do".
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
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
from maezo.gateway.audit import AuditRecord, EmitOnceOutcome, hash_input
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


class StartOutcome(StrEnum):
    """WHAT `start_process_idempotent` DID — the typed field a consumer branches on (F3 MAJOR-2).

    `already_existed` is a single bool AND it is transport-reported, so it cannot express the one
    distinction that matters on a money key: "a live instance exists" vs "an instance already ran
    to completion and I REFUSED to start a second one". Reporting the latter as a success (or as a
    bare log line the caller cannot see) is the fail-silent-success class this enum closes.

    Only `start_process_idempotent` stamps a value other than `UNREPORTED`, and it ASSERTS it from
    its own control flow — never copies it from the transport. A raw
    `find_active_instance`/`start_process_instance` result therefore stays `UNREPORTED`, which is
    honest: no gate verdict was computed for it.
    """

    #: THIS call performed the engine start. The only value that means "a new instance exists
    #: because of me" — the one a caller reporting a caused effect may treat as success.
    STARTED = "STARTED"
    #: A LIVE instance for this business key already existed; nothing was started. The instance id
    #: is the live one.
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    #: STRICT gate hit: the durable claim exists AND the engine's HISTORY proves an instance for
    #: this key already ran and is no longer active. Nothing was started, and nothing ever will be
    #: for this key. The instance id is the HISTORIC one (real, never blank).
    ALREADY_COMPLETED = "ALREADY_COMPLETED"
    #: Not produced by the chokepoint — the default for an instance built directly by a transport
    #: method, where no gate verdict exists.
    UNREPORTED = "UNREPORTED"


@dataclass(frozen=True, slots=True)
class ProcessInstance:
    """A process instance as returned by `start_process_instance`/`find_active_instance`."""

    instance_id: str
    process_key: str
    business_key: str
    state: str  # "ACTIVE" | "COMPLETED" | "SUSPENDED" | "EXTERNALLY_TERMINATED"
    already_existed: bool = False  # True on an idempotent hit (pre-existing active instance)
    #: F3 MAJOR-2 — the chokepoint's own verdict (see `StartOutcome`). Transport-built instances
    #: leave it `UNREPORTED`; `start_process_idempotent` always stamps a real value.
    start_outcome: StartOutcome = StartOutcome.UNREPORTED


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


@runtime_checkable
class HistoryQueryingTransport(Protocol):
    """A `CibSevenTransport` that can also answer "did an instance for this key EVER exist?".

    `find_active_instance` is `active=true` only, so it is BLIND to a COMPLETED/terminated
    instance. That blindness is what made the strict start gate dishonest: a durable dedup claim
    with no active instance was reported as "already started" with a BLANK instance id, and a
    caller could not tell that from a real start (F3 BLOCKER-1).

    The engine's own history IS the durable "the start was committed" record — written by the
    engine, in the same transaction as the instance creation. Querying it turns the strict gate's
    ambiguous case into two decidable ones (see `_resolve_strict_dedup_hit`), which is why this is
    a required seam for a strict family rather than a nice-to-have.

    A SEPARATE Protocol (not a new method on `CibSevenTransport`) for the same reason
    `DedupReportingAuditSink` is separate from `AuditStartSink`: every existing implementer and
    test double keeps type-checking, and `start_process_idempotent` probes with `isinstance` and
    FAILS CLOSED for strict families when the seam is absent.
    """

    async def find_any_instance(
        self, business_key: str, *, process_key: str = ""
    ) -> ProcessInstance | None: ...


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

    async def find_any_instance(self, business_key: str, *, process_key: str = "") -> ProcessInstance | None:
        """Query the engine's HISTORY for ANY instance with this business key — active OR finished.

        `HistoryQueryingTransport` implementation (F3 BLOCKER-1). Same endpoint
        `get_process_status` already falls back to (`:406-413`); unlike `find_active_instance` it
        carries NO `active=true`, so a COMPLETED / EXTERNALLY_TERMINATED / SUSPENDED instance is
        visible. The returned `state` is the engine's own historic state token, verbatim.

        `process_key` (optional) is applied CLIENT-SIDE against each item's `processDefinitionKey`
        rather than as a query parameter: it makes the answer match the dedup key's
        `(process_key, business_key)` tuple exactly, with zero risk of an engine build rejecting or
        silently ignoring an unrecognised query param. Items whose `processDefinitionKey` the
        engine did not report are NOT filtered out (absence of evidence is not a mismatch) —
        conservative on purpose: over-matching here REFUSES a start, under-matching would permit a
        duplicate one.

        Raises `CibSevenError` on an unreachable/non-2xx engine, exactly like its siblings —
        NEVER `None`, which the strict gate would read as "no instance ever existed".
        """
        try:
            resp = await self._client.get(
                "/history/process-instance", params={"processInstanceBusinessKey": business_key}
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CibSevenError(
                f"CIB Seven history query failed [{exc.response.status_code}] for business_key "
                f"`{business_key}`"
            ) from exc
        except httpx.RequestError as exc:
            raise CibSevenError(
                f"CIB Seven unreachable querying history for business_key `{business_key}`: {exc}"
            ) from exc

        for item in resp.json() or []:
            item_key = str(item.get("processDefinitionKey", ""))
            if process_key and item_key and item_key != process_key:
                continue
            return ProcessInstance(
                instance_id=str(item["id"]),
                process_key=item_key or process_key,
                business_key=business_key,
                state=str(item.get("state", "UNKNOWN")),
                already_existed=True,
            )
        return None

    async def start_process_instance(
        self,
        process_key: str,
        business_key: str,
        variables: dict[str, Any],
    ) -> ProcessInstance:
        """Unconditionally POST a new instance start. NEVER call this directly.

        `start_process_idempotent` (:1052) is the only sanctioned caller — it does the
        `find_active_instance` probe, the durable audit-before-effect claim and the strict dedup
        gate around this POST, and the AST fence `scripts/ci/check_start_process_fence.py` fails
        the build on any other call site outside its pinned allowlist."""
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

    async def find_any_instance(self, business_key: str, *, process_key: str = "") -> ProcessInstance | None:
        """`HistoryQueryingTransport` double: this fake's `_instances` map IS its history — an
        instance seeded/started here is never forgotten, so a COMPLETED one stays visible exactly
        as the engine's `/history/process-instance` keeps it. Mirrors `CibSevenHttpTransport`'s
        client-side `process_key` filter (an instance whose `process_key` is blank is not filtered
        out — absence of evidence is not a mismatch)."""
        inst = self._instances.get(business_key)
        if inst is None:
            return None
        if process_key and inst.process_key and inst.process_key != process_key:
            return None
        return inst

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


@runtime_checkable
class DedupReportingAuditSink(Protocol):
    """An `AuditStartSink` that also REPORTS its durable dedup-claim outcome (B-3 effect gate).

    `emit_once` returns a bare hash on BOTH paths (fresh insert / prior claim), so a caller cannot
    tell them apart — the bit that turns the durable `audit_emit_dedup` claim from a double-AUDIT
    guard into a double-EFFECT gate. `emit_once_status` returns it explicitly.

    `PostgresAuditSink` (the production sink) satisfies this structurally. It is a SEPARATE
    Protocol rather than a new required method on `AuditStartSink` so that every existing
    emit-only sink/fake keeps type-checking; `start_process_idempotent` probes for it with
    `isinstance` and FAILS CLOSED for GATED process families when it is absent (see
    `_START_DEDUP_POLICY` / `StartDedupPosture`; the name `_STRICT_START_DEDUP_PROCESS_KEYS` cited
    by an earlier revision has not existed since B-3 landed) — a sink that cannot report dedup
    status must never silently degrade a payment (or contract-termination) gate back to the
    TOCTOU-only behaviour.
    """

    async def emit_once_status(self, record: AuditRecord, *, dedup_key: str) -> EmitOnceOutcome: ...


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


class StartDedupGateUnavailableError(RuntimeError):
    """A STRICT-family start was asked for behind a seam that cannot evaluate the gate.

    Either the audit sink cannot report its dedup claim (`DedupReportingAuditSink`) or the
    transport cannot query engine history (`HistoryQueryingTransport`). Both are gate inputs; a
    strict start with either missing is UN-GATEABLE, so it must not run at all.

    Deliberately NOT a `CibSevenError`: the agent graphs all catch `CibSevenError` and degrade to
    ``{"process_started": False, "error": "start_process indisponivel"}``, which would report an
    ENGINE outage for what is actually a mis-wired durable gate — and, worse, would look like a
    routine transient. This raise propagates and fails the turn, exactly like
    `AuditPersistenceError` does for the emit itself (fail-closed: no un-gated payment start).

    Raised BEFORE the durable claim is written, so a mis-wired call never leaves a claim behind.
    """


class StartClaimWithoutInstanceError(RuntimeError):
    """STRICT family: the durable claim exists, but the engine has NO instance for this key — at
    all, not even in history. The gate cannot decide, so it refuses, LOUDLY (F3 BLOCKER-1).

    WHAT THIS STATE MEANS. The claim is written before the engine POST (ADR-0007 emit-before-
    effect), so exactly two histories produce it, and NOTHING durable distinguishes them:

      A. The POST never took effect (engine down / 4xx / lost request). The intended start never
         happened, and retrying is correct.
      B. A concurrent racer holds the claim and its POST is STILL IN FLIGHT. Retrying would create
         a SECOND instance — for `SP-OP-PAGTO-001`, a second payment release.

    Guessing "A" (auto-restart) is the duplicate-payment defect wearing a recovery costume, and
    guessing "B" (report a phantom success) is the fail-silent-success this whole gate exists to
    kill. So the honest third answer is: STOP and make it visible. A human reads the engine and
    either starts the instance manually or clears the one named claim row — both decisions this
    module has no evidence to make. The message therefore carries the exact `dedup_key`.

    Deliberately NOT a `CibSevenError` (same reasoning as `StartDedupGateUnavailableError`): this
    is not an engine outage and must never be swallowed into a routine transient. It is also NOT
    retryable-by-shrugging — every retry lands here again until a human resolves it, which is the
    point: a wedged payment order that SAYS it is wedged, never one that quietly reports success.

    Closing this residual for real needs the cross-system atomicity the MZO-060 inbox (XRD-10)
    buys; it cannot be closed inside this module without an outbox.
    """

    def __init__(self, *, process_key: str, business_key: str, dedup_key: str) -> None:
        super().__init__(
            f"process_key={process_key!r} business_key={business_key!r}: a durable start claim "
            f"EXISTS (dedup_key={dedup_key!r}) but the engine reports no instance for this key — "
            "neither active nor in history. Either the intended start never took effect or a "
            "concurrent start is still in flight; nothing durable distinguishes the two, so this "
            "STRICT family refuses to start (a wrong guess here is a duplicate money effect). "
            "OPERATOR: confirm against the engine, then either start the instance manually or "
            "delete that single audit_emit_dedup row to re-arm the gate"
        )
        self.process_key = process_key
        self.business_key = business_key
        self.dedup_key = dedup_key


class StartDedupPosture(StrEnum):
    """WHAT THE DURABLE START CLAIM GATES for a process family (B-3, extended by GAP-D3-02).

    The original B-3 policy was a BOOLEAN, and that shape is the root cause GAP-D3-02 reports.
    It welds together two guarantees that are separable, and that a real process key needs
    independently:

      (a) MUTUAL EXCLUSION between CONCURRENT starts — closing the TOCTOU window that
          `find_active_instance` (a plain GET, `:265-271`) structurally cannot close. Safe for
          every family: it can only make a re-delivery converge on the instance a racer is
          creating, never swallow a case.
      (b) A PERMANENT CROSS-TIME GATE — a key that has EVER been started is never started again,
          not even after its instance finished. Correct ONLY for a one-shot key; on a key that
          legitimately recurs it silently swallows the second, real case.

    With only `True`/`False` on offer, a key that needs (a) but must not have (b) had no honest
    classification — which is exactly why `SP-OP-CANCEL-001` sat in the map's "AMBIGUOUS / human
    call" bucket rather than being decided. This enum gives it one.
    """

    #: The claim deduplicates the AUDIT row only. The effect is gated solely by
    #: `find_active_instance` — today's pre-B-3 behaviour, TOCTOU window included.
    NON_STRICT = "NON_STRICT"
    #: (a) WITHOUT (b). The claim is a mutual-exclusion token for the CURRENT generation: a dedup
    #: hit is resolved against ENGINE EVIDENCE — a live instance is returned, an instance PROVEN
    #: FINISHED means this recurrent key may legitimately start its next case, and "no instance
    #: anywhere" stays UNDECIDABLE-AND-LOUD (`StartClaimWithoutInstanceError`). Never a permanent
    #: gate, so a legitimate second case is never swallowed.
    EXCLUSIVE = "EXCLUSIVE"
    #: (a) AND (b). Everything `EXCLUSIVE` does, plus: an instance proven FINISHED REFUSES the
    #: start (`StartOutcome.ALREADY_COMPLETED`). For a one-shot key only — restarting it is the
    #: duplicate effect the gate exists to prevent.
    PERMANENT = "PERMANENT"


#: B-3 per-process START-DEDUP POSTURE (three-valued since GAP-D3-02 — see `StartDedupPosture`
#: for why the original boolean could not classify `SP-OP-CANCEL-001` honestly).
#:
#: WHY THIS IS A MAP AND NOT A BLANKET RULE: a permanent dedup CHANGES RESTART SEMANTICS. Business
#: keys that embed a cycle/competencia component are re-startable BY CONSTRUCTION (the next cycle
#: mints a new key), but several keys here do NOT carry one and a legitimate re-run across time is
#: plausible — turning `PERMANENT` on for those would SILENTLY SWALLOW a legitimate second case.
#: Each key below is classified from its composer, with evidence; unknown keys default to
#: `NON_STRICT` (never a surprise gate) and log a warning so the omission is visible.
#:
#: THE CRITERION (unchanged, only now with three answers instead of two). Ask, in order:
#:   1. Can a re-delivery/racer start a SECOND instance while the first is still being created,
#:      and would that second instance cause harm? -> at least `EXCLUSIVE`.
#:   2. Is the key ONE-SHOT — is a second case under the same key impossible by construction, so
#:      that a restart could only ever be a duplicate of an effect already produced?
#:      -> `PERMANENT`. If a legitimate second case exists, `PERMANENT` is WRONG for that key,
#:      however adverse its effect: the gate would deny the second case with no human in the loop.
#:
#: ┌ PERMANENT ─────────────────────────────────────────────────────────────────────────────────
#: `SP-OP-PAGTO-001` — MONEY RELEASE. Key `PAGTO-{tenant}-{ordem_pagamento_id}` or
#:   `PAGTO-{tenant}-{numero_lote_tiss}-{prestador_id}`, composed by the single shared composer
#:   `agents/andre/keys.py:pagto_business_key` (`keys.py:115`, M-8) through its two call sites
#:   `agents/andre/graph.py:480-486` and `agents/andre/delegation.py:269-275`. NO cycle component,
#:   and none is possible: a payment order is settled ONCE. Restarting a COMPLETED key is a
#:   RE-RELEASE of an already-paid order — the duplicate-payment defect this gate exists for.
#:   THE ONE START SITE TODAY: `agents/andre/graph.py:986` (`PROCESS_KEY_PAGTO`, `graph.py:258`).
#:   `platform/notification_bridge.py` does NOT start PAGTO — its rule set is exactly 7
#:   (`_register_default_handoffs`, `:544`): CONTAS→RECURSO `:566`, CONTAS→FRAUDE `:595`,
#:   FRAUDE→CRED `:618`, FRAUDE→CANCEL `:639`, FRAUDE→INADIMPLENCIA `:668`, and the two
#:   ANS-SUBMIT rules `:692`/`:711`. A FUTURE rule targeting PAGTO would route through
#:   `build_cibseven_process_starter` (`notification_bridge.py:983`) and hence through this gate;
#:   that is a capability of the fenced starter, not a live start site (F3 MAJOR-3 — the earlier
#:   revision cited a bridge line as if it were one). [Line numbers re-pinned at GAP-D3-02; the
#:   previous revision's `:513/:535/…/:952` had drifted.]
#: └────────────────────────────────────────────────────────────────────────────────────────────
#:
#: ┌ EXCLUSIVE (concurrent starts mutually excluded; NEVER a permanent gate) ────────────────────
#: `SP-OP-CANCEL-001` — CONTRACT TERMINATION REVIEW. Key `CANCEL-{tenant}-{numero_contrato}`,
#:   matricula fallback for individual/familiar plans — three composers, all delegating to the
#:   one shared minter `tools/workers/base.py:mint_contract_business_key` (`base.py:500`):
#:   `tools/workers/inadimplencia.py:56` (`_cancel_business_key`, WITH the matricula fallback),
#:   `tools/workers/fraude.py:828` and `platform/notification_bridge.py:417` (both WITHOUT it —
#:   the B-2 asymmetry, swept by `base.contract_business_key_forms`).
#:   THREE START SITES TODAY: `tools/workers/inadimplencia.py:722` (`handoff_rescisao`, the
#:   ENCAMINHAR_RESCISAO handoff), `tools/workers/fraude.py:918` (`start_contratual` via
#:   `_fenced_start`, `fraude.py:741`), and the bridge's FRAUDE→CANCEL rule
#:   (`notification_bridge.py:639`) through `build_cibseven_process_starter` (`:983` -> `:1034`).
#:   WHY `EXCLUSIVE` (criterion step 1 — YES): two of those three are RE-DELIVERABLE by
#:   construction (a CIB Seven external-task lock expiry re-dispatches the same handoff; a Kafka
#:   re-delivery re-fires the bridge rule), and a second CONCURRENT instance is the BLOCKING L0
#:   double-termination risk `docs/processes/harmonization-inadimplencia-cancel.md` §1 is written
#:   to prevent — two parallel `UT_AnaliseRescisao` reviews for one contract. Before GAP-D3-02
#:   the only thing standing between a racer and that outcome was `find_active_instance`, whose
#:   TOCTOU window is documented at `:1131-1136` and is exactly what the durable claim closes.
#:   WHY NOT `PERMANENT` (criterion step 2 — NO, and this is load-bearing): this key is NOT
#:   one-shot, and the evidence is in the process's own terminals. THREE of CANCEL-001's end
#:   events leave the contract ALIVE — `End_ContratoMantido`
#:   (`spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn:467`),
#:   `End_PedidoCancelamentoNegado` (`:443`) and `End_ManterNaoConfirmado` (`:419`) — and ONE key
#:   serves all four `tipo_solicitacao` triggers (`pedido_beneficiario` | `inadimplencia` |
#:   `for_cause_operadora` | `fraude_referida`, `docs/processes/contracts/SP-OP-CANCEL-001.md`
#:   § Variaveis de entrada). So a titular whose cancellation request was DENIED today, or a
#:   contract kept alive by a human MANTER decision, legitimately produces a SECOND case under
#:   the SAME key later. Under `PERMANENT` that second case would be refused with
#:   `ALREADY_COMPLETED` — an adverse outcome against a beneficiary, produced by an idempotency
#:   gate, with NO human in the loop. That is a worse defect than the one being closed, and it is
#:   the same recurrence argument that keeps `SP-OP-INADIMPLENCIA-001` non-permanent below (RN 593
#:   delinquency recurs, and INADIMPLENCIA's own handoff is what mints this CANCEL key).
#:   WHY THE IRREVERSIBLE EFFECT DOES NOT ARGUE FOR `PERMANENT`. Rescisao IS irreversible — but it
#:   is NOT produced by the start. `contract_termination` is L0 hard and every adverse terminal is
#:   reachable ONLY through the human `UT_AnaliseRescisao` (contract § Terminais humano-gated);
#:   a duplicate START buys a duplicate human REVIEW, not a duplicate rescisao. That is the exact
#:   asymmetry with `SP-OP-PAGTO-001`, where the effect FOLLOWS from the start.
#:   RESIDUAL, disclosed: `EXCLUSIVE` excludes concurrent starts within the claimed GENERATION.
#:   Once the claimed instance is proven finished the key becomes re-startable, and exclusivity
#:   for the NEXT generation rests on `find_active_instance` again — closing that needs a
#:   generation-scoped durable token, i.e. the same XRD-10/MZO-060 outbox this module cannot build.
#:   The dominant re-delivery burst (lock expiry / broker retry, seconds after the handoff) lands
#:   inside the claimed generation, which is the window this posture actually closes.
#: └────────────────────────────────────────────────────────────────────────────────────────────
#:
#: ┌ NON_STRICT (classified — legitimate re-run across time, or genuinely ambiguous) ────────────
#: `SP-OP-INADIMPLENCIA-001` — key `INAD-{tenant}-{numero_contrato}` (`agents/fernando/graph.py:
#:   290-298`), started at `agents/fernando/graph.py:548`. A contract that cured a delinquency can
#:   go delinquent AGAIN; the key repeats by design. Gating would block the second, real case.
#: `SP-OP-ESCALATION-001` — key `ESC-{tenant}-{conversation_id}` (`agents/helena/graph.py:304-306`,
#:   `agents/lucas/graph.py:313-316`), started at `agents/helena/graph.py:673` /
#:   `agents/lucas/graph.py:639`. A conversation legitimately escalates again after an earlier
#:   escalation closed.
#: `SP-OP-CRED-001` — key `CRED-{tenant}-{prestador}` or `CRED-{tenant}-{prestador}-{protocolo}`
#:   (`agents/carolina/graph.py:286-297`), started at `agents/carolina/graph.py:626` and, for the
#:   fraude handoff, `tools/workers/fraude.py:735` via `_cred_business_key` (`fraude.py:754`).
#:   AMBIGUOUS: the `-{protocolo}` variant IS per-request, but the bare variant repeats across
#:   RECREDENCIAMENTO cycles — gating it would block a periodic re-accreditation. Human call.
#: `SP-OP-FRAUDE-001` — key `FRAUDE-{tenant}-{numero_caso|prestador_id}` (contract, quoted at
#:   `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:260`), started at
#:   `tools/workers/contas.py:750` and `tools/workers/fraude.py:735`. AMBIGUOUS: keyed by
#:   `numero_caso` it is one-shot, but the `prestador_id` fallback repeats — a genuinely NEW fraud
#:   case against the same provider must still open. Human call.
#: `SP-OP-CONTAS-001` — key `CONTAS-{tenant}-{lote}` / `CONTAS-{tenant}-{guia}-{conta}`
#:   (`agents/marina/graph.py:311-332`), started at `agents/marina/graph.py:704`. AMBIGUOUS: the
#:   contract says a re-sent lote must NOT create a new instance
#:   (`SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:32-34`), which argues STRICT — but that
#:   text is about the ACTIVE instance, and corrections are modelled as a boundary message on the
#:   running instance (`BME_LinhasAtualizadas`, same file `:190`), not as a restart. Human call.
#: `SP-OP-RECURSO-001` — key `RECURSO-{tenant}-{numero_guia_tiss}-{glosa_id}`
#:   (`tools/workers/contas.py:475-481`, `platform/notification_bridge.py:370-374`,
#:   `agents/marina/graph.py:325`), started at `tools/workers/contas.py:596` and
#:   `agents/marina/graph.py:704`. AMBIGUOUS and CLOSE TO STRICT: one recurso per glosa per guia,
#:   and a duplicate filing is a real harm — but `glosa_id` is externally supplied on this path
#:   (see M-9 note in `tools/workers/contas.py`), so its stability is not this module's to assert.
#:   Human call.
#: `SP-OP-AUTH-001` — key `AUTH-{tenant}-{numero_guia_tiss}` (`agents/rafael/graph.py:167-169`),
#:   started at `agents/rafael/graph.py:509`. AMBIGUOUS: a TISS guia is normally one-shot, but
#:   whether a re-submitted/reopened guia may reuse its number is a MEDICAL/ANS semantics question,
#:   explicitly out of an engineering agent's authority. Human call.
#: `SP-OP-ANS-SUBMIT-001` — key `ANSSUB-{tenant}-{report_type}-{competencia}`
#:   (`agents/gustavo/graph.py:303-311`), started at `agents/gustavo/graph.py:697`. CYCLE-SCOPED
#:   (competencia), so a new period already mints a new key — but a RETIFICACAO for an already
#:   submitted competencia is a real ANS workflow; gating it would block a regulatory correction.
#: `SP-OP-NIP-001` — key `NIP-{tenant}-{numero_nip_ans}` (`agents/gustavo/graph.py:311`), started
#:   at `agents/gustavo/graph.py:697`. AMBIGUOUS (ANS protocol-number reuse semantics). Human call.
#: `SP-OP-PROGRAMA-001` — key `PROG-{tenant}-{programa}-{benef}-{ciclo}`
#:   (`agents/valentina/graph.py:277-288`), started at `agents/valentina/graph.py:648`.
#:   CYCLE-SCOPED (`ciclo`): a new enrolment cycle already mints a new key, so the gate would be
#:   harmless — left off only because it buys nothing today.
#: └────────────────────────────────────────────────────────────────────────────────────────────
#:
#: OPERATIONAL CO-REQUISITES (must be settled before a GATED posture — `EXCLUSIVE` or
#: `PERMANENT` — can be trusted in production). NEITHER is enforced in code here.
#:
#: 1. DBA — `audit_emit_dedup` RETENTION. The table is documented as SWEEPABLE, its claims needing
#:    only to "outlive the engine's re-delivery window"
#:    (`platform/migrations/versions/0005_audit_emit_dedup.py:30-36`). A `PERMANENT` key's claim is
#:    the permanent record that a start was COMMITTED TO — if the retention sweep deletes it, the
#:    gate silently reverts to the TOCTOU-only behaviour and a COMPLETED payment key becomes
#:    re-startable again. `PERMANENT` claims MUST be excluded from the by-age sweep (or retained
#:    for the full audit-chain horizon). Direction of failure: SILENT, toward a duplicate payment.
#:    An `EXCLUSIVE` key is NOT exposed to this: its claim is only ever a mutual-exclusion token
#:    for an in-flight generation, so a sweep older than the re-delivery window costs it nothing —
#:    the documented sweep horizon is already the right one for `SP-OP-CANCEL-001`.
#: 2. DBA/ENGINE — CIB Seven HISTORY retention. `_resolve_strict_dedup_hit` reads the engine's
#:    history as the proof that the claimed start actually took effect. If the engine's history
#:    cleanup removes a finished `SP-OP-PAGTO-001` instance while its claim survives, the gate can
#:    no longer tell "already paid" from "never started" and raises
#:    `StartClaimWithoutInstanceError`. Direction of failure: LOUD, toward an operator ticket —
#:    the safe direction, but PAGTO history SHOULD outlive the claim to avoid the noise. (The two
#:    horizons are ordered: claim retention <= history retention keeps the gate quiet AND safe.)
#:    The same ordering keeps `SP-OP-CANCEL-001` quiet, for the same reason and with the same
#:    LOUD-not-silent failure direction.
_START_DEDUP_POLICY: Mapping[str, StartDedupPosture] = {
    "SP-OP-PAGTO-001": StartDedupPosture.PERMANENT,
    "SP-OP-CANCEL-001": StartDedupPosture.EXCLUSIVE,
    "SP-OP-INADIMPLENCIA-001": StartDedupPosture.NON_STRICT,
    "SP-OP-ESCALATION-001": StartDedupPosture.NON_STRICT,
    "SP-OP-CRED-001": StartDedupPosture.NON_STRICT,
    "SP-OP-FRAUDE-001": StartDedupPosture.NON_STRICT,
    "SP-OP-CONTAS-001": StartDedupPosture.NON_STRICT,
    "SP-OP-RECURSO-001": StartDedupPosture.NON_STRICT,
    "SP-OP-AUTH-001": StartDedupPosture.NON_STRICT,
    "SP-OP-ANS-SUBMIT-001": StartDedupPosture.NON_STRICT,
    "SP-OP-NIP-001": StartDedupPosture.NON_STRICT,
    "SP-OP-PROGRAMA-001": StartDedupPosture.NON_STRICT,
}


def start_dedup_posture(process_key: str) -> StartDedupPosture:
    """`process_key`'s classified `StartDedupPosture` (`_START_DEDUP_POLICY`).

    An UNCLASSIFIED key defaults to `NON_STRICT` — today's behaviour — and logs a warning rather
    than guessing: a surprise gate on a flow that legitimately re-runs across cycles would
    silently swallow real cases, which is the worse failure of the two.
    """
    posture = _START_DEDUP_POLICY.get(process_key)
    if posture is None:
        logger.warning(
            "cibseven_start_dedup_policy_unclassified",
            process_key=process_key,
            defaulted_to=StartDedupPosture.NON_STRICT.value,
        )
        return StartDedupPosture.NON_STRICT
    return posture


def is_strict_start_dedup(process_key: str) -> bool:
    """True iff the durable start claim GATES THE EFFECT for `process_key` — i.e. its posture is
    `EXCLUSIVE` or `PERMANENT`, not `NON_STRICT`.

    This is the predicate `start_process_idempotent` branches on to decide whether the two gate
    SEAMS are mandatory (`_require_strict_gate_seams`) and whether a dedup hit must be resolved
    against engine evidence. It deliberately does NOT distinguish `EXCLUSIVE` from `PERMANENT`:
    both need exactly the same seams and the same resolution: only the FINISHED-instance verdict
    differs, and that branch reads the posture itself.
    """
    return start_dedup_posture(process_key) is not StartDedupPosture.NON_STRICT


#: Bounded re-poll of the engine when a STRICT dedup hit finds no instance yet (see
#: `_resolve_strict_dedup_hit`). Deliberately SMALL: the window being absorbed is one concurrent
#: racer's in-flight POST, not an engine outage (which raises `CibSevenError` from the query
#: itself). Worst case adds `(attempts - 1) * delay` to a path that performs NO effect. Raising
#: these buys quieter false alarms, never more safety — the safety comes from never starting.
_STRICT_GATE_RESOLVE_ATTEMPTS = 3
_STRICT_GATE_RESOLVE_DELAY_S = 0.2


def _require_strict_gate_seams(
    transport: CibSevenTransport,
    audit_sink: AuditStartSink,
    *,
    process_key: str,
) -> None:
    """Both STRICT-gate inputs must be present, checked BEFORE anything durable is written.

    The gate needs two facts a non-strict start does not: whether the durable claim was already
    held (`DedupReportingAuditSink.emit_once_status`) and whether the engine EVER ran an instance
    for this key (`HistoryQueryingTransport.find_any_instance`). Missing either makes a strict
    start un-gateable, and a silent degradation to the TOCTOU-only path is exactly the
    duplicate-payment behaviour this module exists to prevent.

    Checked up front so a mis-wired composition root fails BEFORE the claim is written — a raise
    after the claim would leave an orphan claim that wedges the key
    (`StartClaimWithoutInstanceError`) on every later, correctly-wired retry.
    """
    if not isinstance(audit_sink, DedupReportingAuditSink):
        raise StartDedupGateUnavailableError(
            f"process_key={process_key!r} is a STRICT start-dedup family but the injected audit "
            f"sink {type(audit_sink).__name__!r} does not implement `emit_once_status` — the "
            "durable dedup claim cannot be observed, so the start CANNOT be gated. Refusing to "
            "start (fail-closed: an un-gated start of a strict family risks a duplicate effect)"
        )
    if not isinstance(transport, HistoryQueryingTransport):
        raise StartDedupGateUnavailableError(
            f"process_key={process_key!r} is a STRICT start-dedup family but the injected "
            f"transport {type(transport).__name__!r} does not implement `find_any_instance` — a "
            "dedup hit could then only be resolved against `active=true`, which cannot see a "
            "COMPLETED instance, so a claim with no live instance would be indistinguishable from "
            "a start that never happened. Refusing to start (fail-closed)"
        )


async def _emit_start_record_once(
    audit_sink: AuditStartSink,
    record: AuditRecord,
    *,
    dedup_key: str,
) -> EmitOnceOutcome | None:
    """Emit the durable start record exactly-once; return the dedup OUTCOME when observable.

    Returns `None` when the injected sink can only report a hash (`emit_once`) — legal for
    NON-strict families, where the claim remains a double-audit guard only. A STRICT family can
    never reach that branch: `_require_strict_gate_seams` has already refused such a sink.
    """
    if isinstance(audit_sink, DedupReportingAuditSink):
        return await audit_sink.emit_once_status(record, dedup_key=dedup_key)
    await audit_sink.emit_once(record, dedup_key=dedup_key)
    return None


async def _resolve_strict_dedup_hit(
    transport: CibSevenTransport,
    *,
    process_key: str,
    business_key: str,
    dedup_key: str,
    record_hash: str,
    posture: StartDedupPosture,
) -> ProcessInstance | None:
    """A GATED family's durable claim already existed — decide what that PROVES, honestly.

    THE DEFECT THIS REPLACES (F3 BLOCKER-1). The claim is written BEFORE the engine POST, so "a
    claim exists" is evidence that a start was *committed to*, NOT that one *happened*. The
    previous revision treated the two as the same thing and, finding no ACTIVE instance, returned
    a synthetic instance with `instance_id=""` and `already_existed=True` — which
    `agents/andre/graph.py` then reported as `process_started: True` and `delegation.py` shipped
    over A2A. If the engine POST had failed, EVERY retry took that branch: the payment order was
    never started and the system reported success on every attempt, forever. Fail-silent-success.

    THE FIX: ask the engine's HISTORY, which is the record of what actually happened — written by
    the engine itself, transactionally with the instance, so (unlike a Maezo-side "start
    completed" row) there is no crash window between the effect and its record.

      1. ACTIVE instance      -> it is live. Report it, `ALREADY_ACTIVE`, real id. (Both postures.)
      2. FINISHED instance    -> the start DID happen and has ended. THE ONE PLACE THE TWO GATED
                                 POSTURES DIVERGE:
                                 * `PERMANENT` — this is the gate doing its job on an already-paid
                                   order: refuse, `ALREADY_COMPLETED`, real (historic) id and the
                                   engine's own state token.
                                 * `EXCLUSIVE` — the claimed generation is OVER, so it excludes
                                   nothing any more. Return `None`, which tells the caller to fall
                                   through and start this recurrent key's next legitimate case
                                   (GAP-D3-02: refusing here would deny a beneficiary's second
                                   cancellation request with no human in the loop).
      3. NOTHING, anywhere    -> undecidable (see `StartClaimWithoutInstanceError`). RAISE — for
                                 BOTH gated postures, and for the same reason.

    Case 3 is why this does NOT auto-restart on "no instance found", tempting as it looks: a
    concurrent racer that already won the claim may simply not have POSTed yet, and restarting
    into that window is a second payment (`PERMANENT`) or a second in-flight contract-termination
    review (`EXCLUSIVE`). The engine query cannot distinguish it; nothing durable here can. So the
    residual is a LOUD wedge, never a silent one and never a false success. Note the asymmetry
    with case 2 that makes `EXCLUSIVE` sound: a FINISHED instance is positive evidence that the
    claimed start took effect and is over; "nothing anywhere" is the ABSENCE of evidence, and this
    posture never treats absence as permission.

    BOUNDED RE-POLL before case 3. The single most likely producer of case 3 is benign: a
    near-simultaneous re-delivery whose WINNER is mid-POST, microseconds from making the answer
    obvious. So the lookups are retried `_STRICT_GATE_RESOLVE_ATTEMPTS` times spaced
    `_STRICT_GATE_RESOLVE_DELAY_S` apart. This DECIDES NOTHING — it only waits for evidence to
    arrive, and it never starts anything on any attempt, so it cannot trade safety for quiet. Its
    only purpose is to keep a routine duplicate delivery from raising an operator-facing error
    that would train on-call to ignore the one that matters. A winner slower than the whole window
    (or one that died) still lands in case 3, correctly.
    """
    for attempt in range(_STRICT_GATE_RESOLVE_ATTEMPTS):
        if attempt:
            await asyncio.sleep(_STRICT_GATE_RESOLVE_DELAY_S)

        active = await transport.find_active_instance(business_key)
        if active is not None:
            logger.info(
                "cibseven_start_dedup_gate_hit",
                process_key=process_key,
                business_key=business_key,
                instance_id=active.instance_id,
                engine_state="ACTIVE",
                attempt=attempt,
            )
            # `already_existed`/`start_outcome` are asserted HERE rather than trusted from the
            # transport: the durable claim is what proves this instance pre-existed, and a caller
            # reading either to decide whether it just caused a payment must not depend on a
            # transport implementation remembering to set them.
            return replace(active, already_existed=True, start_outcome=StartOutcome.ALREADY_ACTIVE)

        # `_require_strict_gate_seams` already guaranteed this for every strict caller — re-checked
        # (never `assert`ed: `python -O` strips asserts, and this one gates money) so a future
        # direct caller of this helper fails closed instead of `AttributeError`-ing mid-gate.
        if not isinstance(transport, HistoryQueryingTransport):
            raise StartDedupGateUnavailableError(
                f"process_key={process_key!r}: strict dedup hit cannot be resolved — transport "
                f"{type(transport).__name__!r} does not implement `find_any_instance` (fail-closed)"
            )
        historic = await transport.find_any_instance(business_key, process_key=process_key)
        if historic is not None:
            if historic.state == "ACTIVE":
                # The history endpoint also reports RUNNING instances; `find_active_instance` just
                # missed it (replication lag / a racer's POST landing between the two reads). Live
                # is live, under BOTH postures — never start a second one.
                logger.info(
                    "cibseven_start_dedup_gate_hit",
                    process_key=process_key,
                    business_key=business_key,
                    instance_id=historic.instance_id,
                    engine_state=historic.state,
                    attempt=attempt,
                    source="history",
                )
                return replace(historic, already_existed=True, start_outcome=StartOutcome.ALREADY_ACTIVE)
            if posture is StartDedupPosture.EXCLUSIVE:
                # The claimed generation is PROVEN OVER, so the claim excludes nothing any more.
                # `None` = "no verdict — proceed to the normal start path" (GAP-D3-02). This is the
                # ONLY branch that differs from `PERMANENT`, and it is what keeps a recurrent key's
                # legitimate second case from being denied by an idempotency gate.
                logger.info(
                    "cibseven_start_dedup_claim_generation_closed",
                    process_key=process_key,
                    business_key=business_key,
                    instance_id=historic.instance_id,
                    engine_state=historic.state,
                    dedup_record_hash=record_hash,
                    attempt=attempt,
                    reason="EXCLUSIVE posture: the claimed instance has ENDED — this recurrent key "
                    "may start its next case (no permanent gate)",
                )
                return None
            logger.warning(
                "cibseven_start_dedup_gate_blocked_completed",
                process_key=process_key,
                business_key=business_key,
                instance_id=historic.instance_id,
                engine_state=historic.state,
                dedup_record_hash=record_hash,
                attempt=attempt,
                reason="durable claim + engine history PROVE this key already started — refusing to restart",
            )
            return replace(historic, already_existed=True, start_outcome=StartOutcome.ALREADY_COMPLETED)

    logger.error(
        "cibseven_start_claim_without_instance",
        process_key=process_key,
        business_key=business_key,
        dedup_key=dedup_key,
        dedup_record_hash=record_hash,
        attempts=_STRICT_GATE_RESOLVE_ATTEMPTS,
        reason="claim exists but engine has NO instance (active or historic) — undecidable, wedged loudly",
    )
    raise StartClaimWithoutInstanceError(
        process_key=process_key, business_key=business_key, dedup_key=dedup_key
    )


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
      0. GATED POSTURES ONLY (`EXCLUSIVE`/`PERMANENT`) — verify both gate seams exist
         (`_require_strict_gate_seams`) BEFORE writing anything durable, so a mis-wired root never
         leaves an orphan claim. There is NO fallback to the un-gated path: a gated family whose
         root cannot supply the seams refuses to start at all.
      1. Emit the durable, PHI-safe provenance record via `emit_once_status` (idempotent on
         `start_dedup_key`). This RAISES `AuditPersistenceError` on any durability failure — and,
         being neither a `CibSevenError` nor caught by the agents' `except CibSevenError`, it
         propagates and fails the turn. No process is ever started without a preceding durable
         audit row.
      2. GATED POSTURES (B-3 + GAP-D3-02, `_START_DEDUP_POLICY`) — a `deduped=True` outcome means a
         start for this `(tenant, process_key, business_key)` was ALREADY COMMITTED TO, so this
         call must not blindly start another. `_resolve_strict_dedup_hit` asks the ENGINE what
         actually happened: live instance -> report it (`ALREADY_ACTIVE`); FINISHED instance ->
         `PERMANENT` refuses (`ALREADY_COMPLETED`) while `EXCLUSIVE` returns `None` and falls
         through to steps 3-4 (the claimed generation is over — this recurrent key may start its
         next legitimate case); no instance at all -> `StartClaimWithoutInstanceError`
         (undecidable, wedged LOUDLY, both postures).
      3. `find_active_instance` — an active hit is returned unchanged, never re-started (the "one
         active instance per business key" invariant).
      4. Otherwise start the instance.

    Every return carries a `StartOutcome` this function ASSERTS from its own control flow (F3
    MAJOR-2) — the typed field a caller branches on. `already_existed` alone cannot express
    "refused because it already ran", and a log line is not a return value.

    B-3 — WHY THE ENGINE ACTIVE QUERY IS NOT ENOUGH (the duplicate-payment class this closes). The
    pre-B-3 code discarded the `emit_once` result and relied SOLELY on step 3, which has two
    holes:
      * TOCTOU. `find_active_instance` is a plain GET (`:265-271`); two concurrent callers with
        the same business key can BOTH read "no active instance" and BOTH start. The durable
        claim has no such window — the lookup, the claim and the chain insert share one
        per-tenant advisory-locked transaction (`PostgresAuditSink.emit_once_status`), so exactly
        one racer can observe `deduped=False`.
      * `active=true`. A COMPLETED instance is invisible to that query, so a re-delivered start
        for an already-FINISHED business key looked brand new. For `SP-OP-PAGTO-001` that is a
        re-release of an already-paid order.
    Both engine queries are RETAINED as secondary checks — they are the only source of the
    instance id, which the claim (which stores only a record hash) cannot supply.

    F3 BLOCKER-1 — WHY THE CLAIM IS NOT ENOUGH EITHER. A claim proves an agent COMMITTED to a
    start, not that the engine performed one: it is written at step 1, the POST happens at step 4.
    Reading it as proof of a start is what made a failed engine POST report success forever (see
    `_resolve_strict_dedup_hit`). The claim is the MUTUAL-EXCLUSION token; the engine's history is
    the RECORD OF THE EFFECT. Neither substitutes for the other, and this function uses each for
    exactly what it proves.

    RESIDUAL, recorded not hidden (XRD-10 / MZO-060): claim-write and engine-start are two
    systems with no shared transaction, so the window between them is real. It is not closed here
    — no outbox is built — it is made UNDECIDABLE-AND-LOUD (`StartClaimWithoutInstanceError`)
    instead of decidable-and-wrong. A crash inside that window wedges exactly one business key,
    visibly, for a human; it never releases a payment twice and never claims one was released.

    SEMANTIC CAUTION. Making the claim a GATE at all — and above all making it PERMANENT — CHANGES
    RESTART SEMANTICS, so it is applied per process key via `_START_DEDUP_POLICY`, whose comment
    block carries the full per-caller classification with file:line evidence and the two-step
    criterion. Unclassified keys keep today's behaviour. Read that block — and its two operational
    co-requisites (`audit_emit_dedup` retention AND engine history retention) — before adding a key.

    Idempotency & no-double: on re-delivery the same business key yields a dedup outcome (no
    second chain link) AND, for `NON_STRICT` families, `find_active_instance` → the existing
    instance (no second start). Emitting slightly ahead of the start is the fail-closed choice:
    the record attests the agent's decision to start; for gated families that record is also the
    exclusive right to perform it — permanently under `PERMANENT`, for the duration of the claimed
    generation under `EXCLUSIVE`.
    """
    posture = start_dedup_posture(process_key)
    gated = posture is not StartDedupPosture.NON_STRICT
    # 0. Both gate seams, checked BEFORE the claim exists (see `_require_strict_gate_seams`).
    if gated:
        _require_strict_gate_seams(transport, audit_sink, process_key=process_key)

    # 1. FAIL-CLOSED durable provenance BEFORE any engine effect (ADR-0007 invariant).
    record = build_start_audit_record(
        provenance, process_key=process_key, business_key=business_key, variables=variables
    )
    dedup_key = start_dedup_key(provenance.tenant_id, process_key, business_key)
    outcome = await _emit_start_record_once(audit_sink, record, dedup_key=dedup_key)

    # 2. B-3 ATOMIC GATE: the durable claim already existed -> a start was already committed to for
    #    this key. Under `PERMANENT` that is FINAL — never start again, not even if the engine
    #    reports nothing active (which is precisely the COMPLETED-instance hole). Under `EXCLUSIVE`
    #    it is final only while the claimed generation is unfinished or unproven; a `None` verdict
    #    means the engine PROVED that generation ended, and this call proceeds as a fresh start.
    #: True only on the `EXCLUSIVE` fall-through — the claim pre-existed AND the engine proved its
    #: generation ended. Read once, at the failure log below, where "orphan claim" would be a lie.
    claim_generation_closed = False
    if gated and outcome is not None and outcome.deduped:
        resolved = await _resolve_strict_dedup_hit(
            transport,
            process_key=process_key,
            business_key=business_key,
            dedup_key=dedup_key,
            record_hash=outcome.record_hash,
            posture=posture,
        )
        if resolved is not None:
            return resolved
        claim_generation_closed = True
    elif outcome is not None and outcome.deduped:
        # `NON_STRICT` family: the claim deduped the AUDIT row only. Recorded so the observation is
        # never invisible again (the B-3 defect was exactly this value being discarded).
        logger.info(
            "cibseven_start_audit_deduped_not_gated",
            process_key=process_key,
            business_key=business_key,
            dedup_record_hash=outcome.record_hash,
        )

    # 3. Idempotency: an active instance is returned unchanged, never double-started.
    existing = await transport.find_active_instance(business_key)
    if existing is not None:
        logger.info(
            "cibseven_start_idempotent_hit",
            process_key=process_key,
            business_key=business_key,
            instance_id=existing.instance_id,
        )
        return replace(existing, start_outcome=StartOutcome.ALREADY_ACTIVE)

    # 4. Start the instance (the effect — gated behind the durable audit above).
    try:
        instance = await transport.start_process_instance(process_key, business_key, variables)
    except CibSevenError:
        if gated and not claim_generation_closed:
            # The claim is now an ORPHAN: held by this (failed) attempt, with no engine instance
            # behind it. Announced HERE, at the moment it is created, rather than only on the next
            # retry — the operator gets the exact key to resolve without waiting for a re-delivery.
            # The claim is deliberately NOT released: a POST that timed out may still have taken
            # effect, and releasing on that guess is a duplicate payment. `CibSevenError` is
            # re-raised unchanged so the caller's existing engine-outage handling is untouched
            # (an honest `process_started: False` — the process really was not started).
            logger.error(
                "cibseven_start_claim_orphaned",
                process_key=process_key,
                business_key=business_key,
                dedup_key=dedup_key,
                reason="engine start FAILED after the durable claim was written — this key is now "
                "wedged until an operator resolves it (see StartClaimWithoutInstanceError)",
            )
        elif gated:
            # `EXCLUSIVE` fall-through: the claim is NOT orphaned — the engine's own history still
            # holds the finished instance that proves the claimed generation ended, so the next
            # retry resolves the same way and re-attempts the start. Announcing a wedge here would
            # send an operator to unwedge a key that is not wedged (a false alarm is how a real
            # `cibseven_start_claim_orphaned` gets ignored).
            logger.warning(
                "cibseven_start_failed_after_closed_generation",
                process_key=process_key,
                business_key=business_key,
                dedup_key=dedup_key,
                reason="engine start FAILED on an EXCLUSIVE key whose claimed generation is proven "
                "closed — retryable, NOT wedged (the historic instance keeps the verdict decidable)",
            )
        raise
    logger.info(
        "cibseven_process_started",
        process_key=process_key,
        business_key=business_key,
        instance_id=instance.instance_id,
    )
    return replace(instance, start_outcome=StartOutcome.STARTED)
