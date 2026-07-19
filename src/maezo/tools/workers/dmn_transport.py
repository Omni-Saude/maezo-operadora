"""DMN evaluation transport — engine-side, via CIB Seven REST (ADR-0028, T1.5).

The local in-process XML evaluator (`maezo.tools.mcp_dmn.server.DmnServer`) is DEAD at runtime
(zero production consumers) and fails OPEN on no-match (`return {}`) — the exact defect (B5)
this seam fixes. Every DMN table is now evaluated by the REAL, already-deployed engine
(ADR-0011: CIB Seven `2.1.0`, `docker-compose.yml`), which supports the full FEEL comparison/
range/list grammar the local parser cannot (`>=`,`<=`,`>`,`<`, ranges, lists, `not(...)`).

Mirrors T1.1's `WorkerTransport` triple shape (Protocol + real + Fake) exactly, ported from the
v1 donor (`Maezo-Healthcare-Plan src/maezo/tools/mcp_dmn/server.py:43-161`, READ-ONLY reference)
with two fixes the ADR mandates over the donor:

1. **Authoritative version provenance.** The donor read `X-Decision-Definition-Id` off the
   `evaluate` response and defaulted to the string `"unknown"` when absent (donor
   `server.py:132-133`) — a fail-OPEN provenance gap. This transport resolves the concrete
   version via `GET /decision-definition/key/{key}` (ADR-0028 §2) and RAISES
   `DmnEvaluationError` when it cannot — no `"unknown"` ever reaches an audit record.
2. **`Long` typing for cents.** `valor_pagamento_cents` (and any BRL-cents field) must be typed
   `Long`, never `Integer`, once it exceeds Java's `int32` (ADR-0018 part 2) — ported verbatim
   from the donor's `_to_camunda_vars` (`server.py:88-107`).

Error semantics (ADR-0028 §3 — the fail-closed core, NEVER `{}`):

- Engine 4xx/5xx / unreachable / timeout -> raises `DmnEvaluationError` (`RuntimeError`) ->
  transient -> engine-side retry -> incident at 0.
- Version unresolvable -> raises `DmnEvaluationError` -> same (fail-closed provenance).
- Empty result `[]` (no rule matched) -> `DmnNoResultError` from `first_row()` -> immediate
  incident, NEVER retried (deterministic — retrying changes nothing).
- A `Json`-typed result variable that fails to decode -> `DmnVariableDecodeError` -> immediate
  incident, NEVER retried (deterministic, same reasoning as `DmnNoResultError` — see T1.5 note
  below).

`DmnEvaluationError` is deliberately a bare `RuntimeError` (no `.code`/`.message`) so
`tools/workers/base.py`'s `_HARNESS_CLASSIFIED` tuple and `harness.py`'s `_handle` transient
classification pick it up unchanged -> engine-computed retries. `DmnNoResultError` and
`DmnVariableDecodeError` deliberately carry `.code`/`.message` (this package's "coded exception"
convention, e.g. `PagtoError`, `AdequacaoError`) so `FunctionWorker.execute()`
(`base.py:272-282`) reclassifies them to `ValueError` -> `failure(retries=0)` -> an
engine-guaranteed, human-visible incident.

T1.5 (Json-typed result-variable decode, sibling of `harness.py::_from_camunda_var` PR #75 and
`mcp_cibseven/transport.py::_from_camunda_var` PR #89): `evaluate()`'s result-row entries are
decoded the same one-line rule those two seams already apply (`type == "Json"` and a `str`
`value` -> `json.loads`). **Live-probed** against an isolated CIB Seven `2.1.0` engine (capped
JVM heap, throwaway `postgres`+`cibseven` pair, no production/shared instance touched) to answer
whether the DMN evaluate endpoint needed the SAME `deserializeValues=false` treatment PR #89
found for `GET /process-instance/{id}/variables`: it does NOT. `POST /decision-definition/key/
{key}/evaluate` silently ignores an unrecognized `deserializeValues` query param (confirmed —
identical response with or without it) and, for a non-primitive (context/list) output value,
returns it **already deserialized** — `{"type": null, "value": <native dict/list>, "valueInfo":
null}` — never `type: "Json"` with a re-encoded string. This is a THIRD, previously-undocumented
serialization behavior, distinct from both `fetchAndLock` (PR #75: `type: "Json"`, string value,
no flag) and `GET .../variables`'s default (PR #89: `deserializeValues=false` required to avoid
a Jackson `JsonNode` bean reflection). The `type == "Json"` decode below is therefore added
DEFENSIVELY, for wire-format symmetry with the two sibling seams and in case a differently-
configured engine or a custom DMN `itemDefinition` ever does emit `type: "Json"` here — not
because any currently-deployed decision table (`spec/processes/dmn/*.dmn`, every output typeRef
restricted to `{string,boolean,integer,long,double,date}` — see `pagto_alcada.dmn`'s
description) exercises it today. Separately: an output typeRef of `"string"` applied to a
context/list literal is a DMN-modeling error, not a transport defect — the DMN engine coerces
the value via Java `Map.toString()`/`List.toString()` BEFORE it ever reaches the wire (confirmed
live: `{"a": 1, "b": [1, 2, 3]}` -> `"{a=1, b=[1, 2, 3]}"`), destroying the structure upstream of
anything this transport could decode; this is exactly why every real table here restricts output
typeRef to scalar FEEL types and routes structured results through process variables instead.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

from maezo.tools.workers._audit_ctx import record_dmn_version

logger = structlog.get_logger(__name__)

# Java `int32` (java.lang.Integer) bounds — identical to `harness.py`'s constants (ADR-0018
# part 2). Duplicated (not imported) so this module has zero dependency on `harness.py`.
_JAVA_INT32_MIN = -(2**31)
_JAVA_INT32_MAX = 2**31 - 1


class DmnEvaluationError(RuntimeError):
    """Engine unreachable, non-2xx, or version-unresolvable — TRANSIENT (ADR-0028 §3).

    Deliberately a bare `RuntimeError`: an unreachable/erroring engine is transient
    infrastructure, not a bad business decision. `tools/workers/base.py::_HARNESS_CLASSIFIED`
    and `harness.py::_handle`'s `_transient_types` already treat `RuntimeError` as
    engine-retryable — this raises unchanged through `FunctionWorker.execute()` (no
    `.code`/`.message` duck-typing, so it is NOT reclassified into an immediate-incident
    `ValueError`).
    """


class DmnNoResultError(Exception):
    """No DMN rule matched (empty result) — fail-closed per ADR-0028 §3: NEVER `{}`.

    Deliberately NOT a `RuntimeError`: an empty result is a DETERMINISTIC function of the given
    inputs — retrying changes nothing, so this must reach a human immediately, the same as a
    guard failure, never be engine-retried. Carries `.code`/`.message` (this package's coded-
    exception convention — mirrors `PagtoError`/`AdequacaoError`/`CredError`/`InadimplenciaError`)
    so `FunctionWorker.execute()` (`tools/workers/base.py:272-282`) reclassifies it into
    `ValueError`, which the harness maps to `failure(retries=0)` — an engine-guaranteed,
    human-visible incident (T1.1 design §9).
    """

    def __init__(self, decision_key: str, variables: dict[str, Any] | None = None) -> None:
        self.code = "ERR_DMN_NO_RESULT"
        self.decision_key = decision_key
        self.variables = dict(variables) if variables else {}
        self.message = (
            f"DMN '{decision_key}' produced no matching rule for the given inputs (empty "
            "result) — routing to human review, NEVER treated as an implicit decision"
        )
        super().__init__(f"{self.code}: {self.message}")


class DmnVariableDecodeError(Exception):
    """A `Json`-typed DMN result variable's `value` was not valid JSON — fail-closed (T1.5,
    sibling of `tools/workers/harness.py::fetch_and_lock`'s decode defect, PR #75, and
    `mcp_cibseven/transport.py::get_process_status`'s copy, PR #89).

    Deliberately NOT a `RuntimeError`/`DmnEvaluationError`: a malformed `Json`-typed result
    variable on an otherwise-successful 200 `evaluate` response is a DETERMINISTIC function of
    the decision table's own rule definitions — retrying the identical `evaluate` call
    reproduces the identical malformed value, so this must reach a human immediately and NEVER
    be engine-retried (same reasoning as `DmnNoResultError` above — deliberately NOT mirroring
    `mcp_cibseven.transport.CibSevenVariableDecodeError`'s choice to subclass a `RuntimeError`;
    see module docstring's T1.5 note for the live-probe context). Carries `.code`/`.message`
    (this package's coded-exception convention) so `FunctionWorker.execute()`
    (`tools/workers/base.py:272-282`) reclassifies it into `ValueError` -> `failure(retries=0)`
    — an engine-guaranteed, human-visible incident, NEVER a silent raw-string passthrough.
    """

    def __init__(self, decision_key: str, variable_name: str, error: Exception) -> None:
        self.code = "ERR_DMN_VARIABLE_DECODE"
        self.decision_key = decision_key
        self.variable_name = variable_name
        self.message = (
            f"DMN '{decision_key}' returned a malformed Json-typed result variable `{variable_name}`: {error}"
        )
        super().__init__(f"{self.code}: {self.message}")


@dataclass(frozen=True, slots=True)
class DmnVersion:
    """Authoritative DMN decision-definition version provenance (ADR-0028 §2/§5).

    Resolved via `GET /decision-definition/key/{key}` — NEVER the donor's weak
    `X-Decision-Definition-Id` header fallback (which defaults to the string `"unknown"`, donor
    `server.py:132-133`). An unresolvable version raises `DmnEvaluationError` instead.
    """

    key: str
    id: str
    version: int
    deployment_id: str

    def to_audit_dict(self) -> dict[str, Any]:
        """Shape for `AuditRecord.dmn_versions[key]` (ADR-0028 §5, `gateway/audit.py`)."""
        return {"version": self.version, "id": self.id, "deploymentId": self.deployment_id}


@runtime_checkable
class DmnTransport(Protocol):
    """DMN evaluation seam — mirrors T1.1's `WorkerTransport` triple shape (ADR-0028 §1).

    One method, injectable for unit tests (`FakeDmnTransport`) without faking the engine in
    integration tests (ADR-0011). `tenant`, when given, targets a tenant-scoped decision
    definition (`/decision-definition/key/{key}/tenant-id/{tenant}/evaluate`); `None` (the
    default for every T1.5-migrated table — none of the ~11 are tenant-scoped) evaluates the
    global definition.
    """

    async def evaluate(
        self,
        decision_key: str,
        variables: dict[str, Any],
        *,
        tenant: str | None = None,
    ) -> tuple[list[dict[str, Any]], DmnVersion]: ...

    async def close(self) -> None: ...


def _to_camunda_vars(variables: dict[str, Any]) -> dict[str, Any]:
    """Type DMN inputs for the engine wire format — `Long` for ints outside int32 (ADR-0028 §2).

    Ported verbatim from the v1 donor (`mcp_dmn/server.py:88-107`, READ-ONLY reference) — same
    rule as `harness.py::_to_camunda_var` (T1.1 §5), duplicated here (not imported) so this
    module carries no dependency on `harness.py`. **Load-bearing**: `pagto_alcada`'s
    `valor_pagamento_cents` is unbounded on its top band — a payment above int32-max cents
    (R$21.47M) typed as `Integer` gets HTTP 400 "Cannot convert value ... to java type
    java.lang.Integer" from the engine (verified — ADR-0028 §1).

    T1.5 (dict|list -> Json parity fix): a bare `dict`/`list` value (no pre-existing `"value"`
    key) now types as a Camunda `Json` variable, exactly mirroring `harness.py::_to_camunda_var`'s
    `dict|list` branch byte-for-byte (`json.dumps(v, ensure_ascii=False, default=str)`). Before
    this fix such a value fell through to the catch-all `else` and was typed `String` via Python
    `str(v)` (e.g. `"{'a': 1}"`, `repr()`-like and not valid JSON) — the engine would store it as
    an opaque string, not a structured object/array.

    **This mapper types by the Python RUNTIME type of each value ONLY — it does NOT validate against
    the DMN input's declared `typeRef`.** A wrong-SHAPE source value is silently typed by whatever
    Python type it happens to be: a `list` bound to a `string`-declared input is encoded as `Json`
    (not the `String` FEEL expects); an LLM-emitted JSON STRING (`"85"`) bound to an `integer`-
    declared input is typed `String` (not `Integer`); a `float` bound to an `integer` input is
    typed `Double`. None of these raise here — the engine may FEEL-coerce, no-match (fail-closed),
    or, worst case, match a rule on the wrong-typed value. Ensuring each value's Python type matches
    the DMN's declared `typeRef` is the CALLER's responsibility (e.g. `fraude._collect_scoring_inputs`
    coerces-or-drops its numeric signals before calling in). This function only guarantees each
    Python type maps to the correct Camunda wire type.
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
    """Decode ONE outbound DMN result-row variable entry (`{"value": ..., "type": ...}`) into
    the Python value a worker actually consumes. Duplicated (not imported — this module's
    zero-dependency stance, see `_to_camunda_vars`'s docstring) from `tools/workers/harness.py`'s
    `_from_camunda_var` (T1.1 fix, PR #75) / `mcp_cibseven/transport.py`'s copy (PR #89): same
    rule, same fail-closed posture.

    **Defensive, not currently live-exercised** (T1.5 investigation — see module docstring):
    every wire type this endpoint has been observed to actually emit already arrives value-ready
    (`Integer`/`Long`/`Double`/`Boolean`/`String`/a `null` value, OR an untyped `type: null`
    context/list already deserialized) — `.get("value")` alone is correct for all of them. This
    decode is applied anyway for wire-format symmetry with the two sibling seams and as a guard
    against a differently-configured engine that DOES emit `type: "Json"` with a re-encoded
    JSON string here, exactly as `fetchAndLock` already does for process variables.

    Raises `json.JSONDecodeError` (a `ValueError` subclass) on malformed JSON content, and
    `TypeError`/`AttributeError` on a malformed entry shape — both left for the caller
    (`evaluate`) to wrap into `DmnVariableDecodeError` (fail-closed, never a silent raw-string
    passthrough). A `Json`-typed entry with `value: null` decodes to `None`, never attempted
    through `json.loads` (which would raise `TypeError` on a non-str/bytes argument).
    """
    value = entry.get("value")
    if entry.get("type") == "Json" and isinstance(value, str):
        return json.loads(value)
    return value


class CibSevenDmnTransport:
    """Real transport: CIB Seven REST `/decision-definition/key/{key}/evaluate` (ADR-0028 §1/§2).

    Resolves + caches the authoritative version (`GET /decision-definition/key/{key}[/tenant-id/
    {tenant}]`) per `(decision_key, tenant)` before evaluating — a redeploy during process
    lifetime is out of scope (mirrors T1.1's engine-facts caching stance; a new daemon
    instance/restart picks up the new version). NEVER swallows a transport error into a
    fake-empty success (mirrors `CibSevenWorkerTransport`'s fail-closed rule, `harness.py:269`).

    **No persistent `httpx.AsyncClient`** (unlike `CibSevenWorkerTransport`) — deliberately: this
    transport is called from worker functions via `evaluate_sync` (`asyncio.run(...)`, T1.1
    design §7 sync/async bridge), and the harness dispatches EVERY sync worker call through
    `asyncio.to_thread`, i.e. a FRESH event loop per call (`asyncio.run` creates and closes one
    each time). An `httpx.AsyncClient` built once in `__init__` binds its connection pool to
    whichever loop first uses it; a second call from a *different* fresh loop then fails with
    `RuntimeError: Event loop is closed` (reproduced against the live compose engine while
    building this transport's parity harness — a real defect, not a hypothetical one). Building
    a client fresh inside each `evaluate`/`_resolve_version` call keeps every call self-contained
    within whatever loop invoked it — the cost is no cross-call connection pooling, which is
    consistent with (and dominated by) the per-task fresh-OS-thread cost `asyncio.to_thread`
    already pays.
    """

    def __init__(self, base_url: str, *, auth_token: str | None = None, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth_token:
            self._headers["Authorization"] = f"Bearer {auth_token}"
        self._timeout = timeout
        self._version_cache: dict[tuple[str, str | None], DmnVersion] = {}

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, headers=self._headers, timeout=self._timeout)

    @staticmethod
    def _definition_path(decision_key: str, tenant: str | None) -> str:
        if tenant:
            return f"/decision-definition/key/{decision_key}/tenant-id/{tenant}"
        return f"/decision-definition/key/{decision_key}"

    async def _resolve_version(
        self, client: httpx.AsyncClient, decision_key: str, tenant: str | None
    ) -> DmnVersion:
        cache_key = (decision_key, tenant)
        cached = self._version_cache.get(cache_key)
        if cached is not None:
            return cached

        url = self._definition_path(decision_key, tenant)
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DmnEvaluationError(
                f"CIB Seven decision-definition lookup failed [{exc.response.status_code}] for "
                f"`{decision_key}`" + (f" (tenant `{tenant}`)" if tenant else "")
            ) from exc
        except httpx.RequestError as exc:
            raise DmnEvaluationError(
                f"CIB Seven unreachable resolving decision-definition `{decision_key}`: {exc}"
            ) from exc

        body = resp.json()
        try:
            version = DmnVersion(
                key=decision_key,
                id=str(body["id"]),
                version=int(body["version"]),
                deployment_id=str(body["deploymentId"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            # Fail-closed provenance (ADR-0028 §2) — NEVER default to "unknown" (the donor's gap).
            raise DmnEvaluationError(
                f"CIB Seven decision-definition response for `{decision_key}` missing "
                f"id/version/deploymentId: {body!r}"
            ) from exc
        self._version_cache[cache_key] = version
        return version

    async def evaluate(
        self,
        decision_key: str,
        variables: dict[str, Any],
        *,
        tenant: str | None = None,
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        async with self._new_client() as client:
            version = await self._resolve_version(client, decision_key, tenant)

            eval_path = (
                f"/decision-definition/key/{decision_key}/tenant-id/{tenant}/evaluate"
                if tenant
                else f"/decision-definition/key/{decision_key}/evaluate"
            )
            payload = {"variables": _to_camunda_vars(variables)}
            try:
                resp = await client.post(eval_path, json=payload)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise DmnEvaluationError(
                    f"CIB Seven DMN evaluate failed [{exc.response.status_code}] for "
                    f"`{decision_key}`: {exc.response.text[:300]}"
                ) from exc
            except httpx.RequestError as exc:
                raise DmnEvaluationError(f"CIB Seven DMN unreachable for `{decision_key}`: {exc}") from exc

            body = resp.json()

        # T1.5 fix (sibling of `harness.py::fetch_and_lock` PR #75 / `mcp_cibseven/transport.py`
        # PR #89): decode `type == "Json"` result-variable entries via `_from_camunda_var`
        # instead of a bare `.get("value")` — see module docstring for the live-probe verdict.
        # A malformed `Json`-typed entry NEVER reaches the worker as the raw undecoded string;
        # it raises `DmnVariableDecodeError` (fail-closed) instead.
        result_rows: list[dict[str, Any]] = []
        for row in body:
            decoded_row: dict[str, Any] = {}
            for k, v in row.items():
                if not isinstance(v, dict):
                    decoded_row[k] = v
                    continue
                try:
                    decoded_row[k] = _from_camunda_var(v)
                except (ValueError, TypeError, AttributeError) as exc:
                    raise DmnVariableDecodeError(decision_key, k, exc) from exc
            result_rows.append(decoded_row)
        return result_rows, version

    async def close(self) -> None:
        """No-op: no persistent client to close (see class docstring). Kept for `DmnTransport`
        Protocol conformance / lifecycle symmetry with `CibSevenWorkerTransport.close()`."""
        return None


class FakeDmnTransport:
    """Labeled, fail-closed test double. NEVER imported by production code (import-lint-fenced,
    mirrors `FakeWorkerTransport`/`FakeKafkaPublisher`).

    `.evaluate` RAISES `DmnEvaluationError` on an unregistered key (donor `server.py:140-161`) —
    a test that forgets to `.register(...)` a decision key gets a loud failure, never a silent
    empty/default result.

    Post-fix contract (T1.5, mirrors `FakeCibSevenTransport.get_process_status`'s comment,
    `mcp_cibseven/transport.py`): callers of `evaluate()` see decoded Python objects (dict/list),
    never a raw JSON string. This fake never wire-encodes/decodes at all — `register` stores
    exactly the Python objects a test passes in — so it already matches
    `CibSevenDmnTransport.evaluate()`'s post-fix, already-decoded contract by construction; a
    test that needs to prove the wire-level `Json` decode uses `CibSevenDmnTransport` against a
    mocked `httpx` client instead, per `test_dmn_transport.py`.
    """

    def __init__(self) -> None:
        self._responses: dict[str, tuple[list[dict[str, Any]], DmnVersion]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def register(
        self,
        decision_key: str,
        rows: list[dict[str, Any]],
        *,
        version: int = 1,
        definition_id: str | None = None,
        deployment_id: str = "test-deployment",
    ) -> None:
        """Register the rows + version `evaluate(decision_key, ...)` returns."""
        self._responses[decision_key] = (
            rows,
            DmnVersion(
                key=decision_key,
                id=definition_id or f"{decision_key}:{version}:test",
                version=version,
                deployment_id=deployment_id,
            ),
        )

    async def evaluate(
        self,
        decision_key: str,
        variables: dict[str, Any],
        *,
        tenant: str | None = None,
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        del tenant  # unused — no T1.5-migrated table is tenant-scoped
        self.calls.append((decision_key, dict(variables)))
        if decision_key not in self._responses:
            raise DmnEvaluationError(f"FakeDmnTransport: `{decision_key}` not registered")
        rows, version = self._responses[decision_key]
        return list(rows), version

    async def close(self) -> None:
        self.closed = True


def evaluate_sync(
    dmn: DmnTransport,
    decision_key: str,
    variables: dict[str, Any],
    *,
    tenant: str | None = None,
) -> tuple[list[dict[str, Any]], DmnVersion]:
    """Bridge `dmn.evaluate(...)` into the SYNC worker entry-function boundary.

    Workers are synchronous by design (`FunctionWorker.execute`, `tools/workers/base.py`) and
    the harness always dispatches them via `asyncio.to_thread` — never directly on the event
    loop (T1.1 design §7 sync/async bridge) — so a fresh `asyncio.run()` here never collides
    with a running loop in production. Direct synchronous unit tests call it the same way.

    T-B (T1.10, ADR-0007 non-repudiation): on every successful evaluation this records the
    authoritative `DmnVersion` into the current task's audit collector (`_audit_ctx`), so the
    harness can populate `AuditRecord.dmn_versions` at emit time WITHOUT changing the
    `fn(variables) -> dict` worker boundary (design §3.2). `record_dmn_version` is a no-op
    outside an audited dispatch (tests/tooling), so this is transparent to every existing caller.
    This is the ONE funnel every worker DMN eval passes through, so a single hook covers all
    ~11 migrated tables.
    """
    rows, version = asyncio.run(dmn.evaluate(decision_key, variables, tenant=tenant))
    record_dmn_version(version)
    return rows, version


def first_row(
    rows: list[dict[str, Any]],
    decision_key: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return `rows[0]` (FIRST/UNIQUE hit policy -> exactly one authoritative result row).

    Raises `DmnNoResultError` on empty `rows` (ADR-0028 §3 — NEVER `{}`; every T1.5-migrated
    table is FIRST hit policy with a conservative catch-all, so this should not occur in
    practice — if it does, the caller escalates to a human rather than silently deciding).
    """
    if not rows:
        raise DmnNoResultError(decision_key, variables)
    return rows[0]


def require_dmn(dmn: DmnTransport | None, topic: str) -> DmnTransport:
    """Fail-closed guard for the `dmn=` seam at a worker entry point (ADR-0028 §1).

    Raises `DmnEvaluationError` (transient — engine-side retry, T1.1 §9) when the seam was not
    wired (e.g. `CibSevenDmnTransport` construction failed at daemon boot, `service.py`'s
    `_bring_up_dependencies`) — the daemon's readiness check independently surfaces this via
    `workers_registered`/`engine_reachable`; this is the defense-in-depth guard at the call
    site itself, so a missing seam fails an actual task loudly rather than crashing with an
    unclassified `AttributeError` on `None`.
    """
    if dmn is None:
        raise DmnEvaluationError(f"dmn transport seam not wired for `{topic}` (ADR-0028)")
    return dmn
