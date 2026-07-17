# ADR-0026: Worker standardization — adapter over subclassing

**Status:** Proposed (2026-07-16) · **Data:** 2026-07-16 · **Area:** Orquestracao (runtime spine, B13)

> Draft for the orchestrator's ratification. Ground rule 2 (author ≠ verifier): this ADR is authored by
> runtime-spine-engineer (R1) and must be R1-verified before it moves to Accepted. Every count and
> signature below was verified at HEAD `a0427d2`.

## Contexto

**The defect (B13, plan §1).** The runtime spine (T1.1) dispatches BPMN external tasks by topic through
`WorkerRegistry`, and every worker gets retry + per-worker metrics from `WorkerBase.run()`
(`src/maezo/tools/workers/base.py:107-185`). But **13 of 16 worker modules are not `WorkerBase`
subclasses** — the registry is typed `dict[str, WorkerBase]` (`base.py:228,231`) and **cannot accept
them**, so those 13 modules have no retry, no metrics, and no route from the engine. This ADR decides how
to make all 16 registrable.

**The evidence (verified this session).**

- **Two worker shapes coexist:**
  - *Class-based* (3 modules, 15 subclasses): `auth.py` (6), `escalation.py` (3), `lgpd.py` (6). Each is
    `class XWorker(WorkerBase)` with `def execute(self, process_vars: dict[str, Any]) -> dict[str,
    Any]` and a topic set in `__init__` (e.g. `escalation.py:39-56,94-107,138-150`). These already flow
    through `WorkerBase.run()` (retry + metrics). Count of `execute(self, process_vars…)`: **16** (15
    subclasses + the abstract in `base.py:93`).
  - *Function-based* (13 modules): `adequacao, ans_cron, ans_submit, cancel, contas, credenciamento,
    fraude, inadimplencia, nip, pagto, programa, recurso, reembolso`. They expose **module-level free
    functions**, **~84** of them, with a **uniform signature** `def fn(variables: dict[str, Any]) ->
    dict[str, Any]` (e.g. `adequacao.py:31,70,124,143,165`; the signature is invariant across the corpus,
    see the count in "Signature census" below). No topic, no registry, no `WorkerBase`.
- **Signature census.** The only two call shapes in the corpus are `execute(self, process_vars: dict[str,
  Any]) -> dict[str, Any]` (×16, class path) and `fn(variables: dict[str, Any]) -> dict[str, Any]` (×~84,
  function path). The only difference is the parameter **name** (`process_vars` vs `variables`) — both are
  the same BPMN process-variable dict. This uniformity is the central fact: **one adapter can wrap every
  function.**
- **Heterogeneous error classes (20 across the modules).** Guard/authorization errors subclass
  `PermissionError` (`AnsSubmitNotHumanError`, `CancellationNotHumanError`, `NipNegativaNotHumanError`,
  `ReembolsoDenialNotHumanError`, `DesistenciaNotHumanError`, `GlosaAcceptNotHumanError`,
  `CancelManterNotHumanError` — e.g. `ans_submit.py:24`, `nip.py:24`); validation errors subclass
  `ValueError` (`AnsDatasetIncompletoError`, `NipProtocoloInvalidoError`, `ContasLoteInvalidoError`,
  `CancelContratoInvalidoError`, `RecursoGlosaInvalidaError`, … `nip.py:40`, `contas.py:42`); a few subclass
  `RuntimeError` (`AnsRetryEsgotadoError`, `ans_submit.py:47`) or plain `Exception` with a `.code`/`.message`
  (`AdequacaoError` `adequacao.py:214-220`, `FraudeError`, `CredError`, `PagtoError`,
  `InadimplenciaError`, `ProgramaError`). Plus the module-level `ERR_*_NOT_HUMAN` guard **constants**
  (`base.py:25-32`). Any standardization must map these classes to an engine outcome (§Decisao).
- **How `WorkerBase.run` wraps execution.** `run(process_vars)` loops `1..max_retries`, calls
  `execute()`, on success records `record_worker_execution(worker_name, topic, duration)` and returns; on
  each failure records `record_worker_error(worker_name, topic, error_type)` and `time.sleep(backoff*n)`;
  after exhaustion re-raises the last error (`base.py:107-185`). Metrics are best-effort (`base.py:151-152,
  174-175`). `execute()` is the single abstract method (`base.py:93-105`).
- **Tests bind to the *functions*, not classes.** Function-module tests import and call the free functions
  directly — e.g. `from maezo.tools.workers.ans_cron import check_calendar, trigger_submissions`
  (`tests/unit/tools/workers/test_ans_cron.py`) — and import typed **result dataclasses**
  (`RecursoValidationResult`, `CancelValidationResult`, `ReembolsoCalculoResult`, `NipClassificationResult`,
  `GlosaIdentified`, `NipRoutingResult`). Class-module tests call `worker.run(process_vars)` (≈39 call
  sites). So the *tested unit* of the 13 modules is the free function; the *tested unit* of the 3 is
  `run()`.

**Honesty note — v2 has already diverged from v1.** The v1 donor has **no** `WorkerBase` class at all:
its workers are **async closures** `async def handler(task: ExternalTask) -> Mapping|None`, produced by
`make_*_handler(...)` factories and registered by topic via `register_<name>_workers(harness, kafka)`;
retry lived in the harness (tenacity) and metrics in the harness (`_emit_worker_task_outcome`), **not** in
the worker (v1 `tools/workers/harness.py`, `service.py`). v2, by contrast, invented `WorkerBase` +
`WorkerRegistry` and wrote its workers as **sync** units — 15 `WorkerBase` subclasses and ~84 sync
`fn(variables)->dict` functions. So there are effectively **three** shapes in play (v1 async closures; v2
sync classes; v2 sync functions), and any decision must be honest that "port v1" and "keep v2" pull in
different directions. This ADR decides the **v2 worker→registry** binding; the **runtime-spine** surface
(`WorkerHarness`/`CibSevenWorkerTransport`/`register_*_workers` names) is preserved separately by the T1.1
design §16 so T3.1 fixtures port regardless of which option below is chosen.

**The tension.** We need all 16 registrable (retry/metrics, dead registry removed) **without** rewriting
the ~84 functions and their tests, while (a) not discarding v2's 15 `WorkerBase` subclasses and their ~39
passing `worker.run()` tests, and (b) preserving the fixture-port path for T3.1 (v1's `register_<name>_workers`
bootstrap names — T1.1 §16).

**Alternatives considered (four real options).**

- **(A) Subclassing** — turn each free function into a `WorkerBase` subclass. ~84 new trivial classes
  (or fewer classes multiplexing several topics through one `execute`, which breaks WorkerBase's
  one-topic/one-execute contract). Deletes/duplicates the free functions → rewrites ~13 function-test
  modules that import and call them, and orphans the result dataclasses' call sites. Maximum churn on the
  money/PHI paths (R3-forbidden territory), maximum risk.
- **(B) Adapter (RECOMMENDED)** — one `FunctionWorker(WorkerBase)` that wraps `(topic, callable)`; each
  module gains a small `register_<domain>_workers(registry)` bootstrap. The free functions and their tests
  are **untouched**; the 15 subclasses and their tests are **untouched**; registry/retry/metrics are added
  *on top*.
- **(C) Adopt v1's async-closure + `register_*` model wholesale** — rewrite all 16 v2 modules into async
  `handler(task)` closures, register via `harness.register(topic, handler)`, and get retry/metrics from the
  **harness** (as v1 does) rather than `WorkerBase`. **Maximizes T3.1 fixture fidelity at the *worker
  internals* level**, but: (i) requires rewriting **all 16** v2 modules (the ~84 sync functions **and** the
  15 subclasses) into async closures — the exact mass churn on money/PHI paths this task exists to avoid;
  (ii) **breaks** the v2 function tests (they call `fn(variables)` sync, not `await handler(task)`) **and**
  the ~39 class tests (they call `worker.run()`); (iii) discards v2's `WorkerBase`/`WorkerRegistry`
  investment. Net: the fixture *portability that matters for T3.1 is the **spine** surface* (harness +
  `register_*_workers` names), which option (B) also preserves — so (C) pays the full rewrite cost to buy
  fidelity T3.1 does not actually require. Rejected, but explicitly, per orchestrator challenge.
- **(D) Hybrid** — FunctionWorker-wrap the pure sync functions (the majority), but keep/allow genuinely
  **async** workers (those doing engine/DMN/Kafka I/O) as `run_async` overrides or a
  `register_function_async(topic, coro)` path. This is option (B) plus an async escape hatch, not a
  separate model; adopted as the **provision inside (B)** (see Decisao §1 note on `run_async`), not a
  standalone choice.
- **(E) Do nothing / duck-type the registry** — loosen `WorkerRegistry` to accept bare callables. Loses
  the retry/metrics `run()` wrapper (the whole point of B13) and the type safety; rejected.

**Retry/metrics home (reconciled).** Under (B), "retry" in the plan's "registered with retry/metrics"
acceptance is delivered by the **engine** (T1.1 §9 makes the engine the single retry system of record; the
dispatcher reports `failure(retries=task.retries-1)`), while "metrics" come from **both**
`WorkerBase.run()` (exec-time/error per worker, `base.py:141-175`) **and** the dispatcher's
dispatch-outcome counter (T1.1 §13). `WorkerBase`'s own in-process retry defaults **OFF** on the runtime
path — it is not a third retry layer. This is coherent and matches how v1 actually behaved (retry at the
spine), while keeping v2's per-worker metric labels.

## Decisao

**Adopt the adapter (B).** All 16 modules register in `WorkerRegistry` and run through
`WorkerBase.run()`'s retry/metrics.

1. **`FunctionWorker(WorkerBase)`** — a single new class in `src/maezo/tools/workers/base.py`:
   ```python
   class FunctionWorker(WorkerBase):
       def __init__(self, topic: str, fn: Callable[[dict[str, Any]], dict[str, Any]],
                    *, max_retries: int = 1, retry_backoff: float = 1.0) -> None:
           super().__init__(topic=topic, max_retries=max_retries, retry_backoff=retry_backoff)
           self._fn = fn
       def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
           return self._fn(process_vars)   # variables == process_vars (same dict)
   ```
   It inherits `run()`, so every wrapped function now emits
   `maezo_worker_execution_time_seconds{worker,topic}` / `maezo_worker_error_count_total{…}`
   (`observability.py:148-188`) and is routable by topic. Default `max_retries=1` — the **engine owns
   durable retry** (T1.1 design §9); in-process retry is opt-in per worker for provably-idempotent
   transient faults only.

2. **Per-module bootstrap** — each of the 13 function modules gains
   `def register_<domain>_workers(registry: WorkerRegistry) -> None` that maps its BPMN external-task
   topics → `FunctionWorker(topic, fn)`:
   ```python
   # adequacao.py
   def register_adequacao_workers(registry: WorkerRegistry) -> None:
       registry.register("operadora.adequacao.measure_gap", FunctionWorker("operadora.adequacao.measure_gap", measure_gap))
       registry.register("operadora.adequacao.route_remediation", FunctionWorker(..., route_remediation))
       ...  # register_fallback_commitment is the L0-guarded topic (adequacao.py:165-206)
   ```
   Topics come from `spec/` (BPMN external-task `topicName`), **not** a hand-list — the registry is
   validated against the spec at readiness (T1.1 §12, fail-closed: a spec topic with no worker → not
   ready). Helper functions (non-external-task funcs) are simply not registered.

3. **Class modules** register directly: `register_<domain>_workers(registry)` calls
   `registry.register(worker.topic, worker())` for each of the 15 subclasses (they already are
   `WorkerBase`).

4. **`register_all_workers(registry)`** composes all 16 module bootstraps (mirrors the donor's
   `_register_all_workers`, T1.1 §16). The runtime daemon calls it in boot step B; `fetchAndLock` topics =
   `registry.list_topics()`.

5. **Error → engine-outcome classifier** (lives once, in the dispatcher, consumed via a small
   `classify_worker_error(exc)` helper next to `FunctionWorker`): guard errors (`ERR_*_NOT_HUMAN`
   constants; `*NotHumanError(PermissionError)`) → **`bpmnError`, never retried** (retrying an L0 guard
   could drive an adverse action — forbidden, ADR-0008); `ValueError`-family → `bpmnError` or
   `failure(retries=0)` per topic (T1.1 open Q-3); transient (`RuntimeError`/IO/`*RetryEsgotado*`) →
   `failure(retries=task.retries-1, retryTimeout=backoff)` (engine-side retry → incident at 0). This keeps
   the mapping in **one** place instead of scattered across 13 modules.

**Why adapter wins (grounded):** one new class + 16 tiny bootstraps vs ~84 new subclasses; the ~84
functions and their unit tests stay **untouched** (they keep importing and calling the function — logic
coverage preserved) while the adapter adds registry/retry/metrics coverage on top; result dataclasses keep
their call sites; the guard-error mapping is centralized and fail-closed; and the `register_*_workers`
bootstrap names line up with the v1 donor so T3.1 fixtures port with adaptation only (T1.1 §16).

## Consequencias

**Positivas:**
- All 16 modules register in `WorkerRegistry` with retry + per-worker metrics → B13 closed; no dead
  registry (plan T1.2 acceptance: "All 16 modules registered; metrics emitted per worker; no dead
  registry").
- Minimal, mechanical, low-risk change on money/PHI worker code — the business logic (the functions) is
  not edited, only wrapped.
- Single, centralized, fail-closed error→outcome mapping; L0 guards can never be auto-retried into an
  adverse action.
- Bootstrap names align with the donor → cheaper T3.1 port.

**Negativas (aceitas):**
- Two coexisting worker *shapes* remain (classes + wrapped functions). Accepted: the `FunctionWorker`
  seam hides the difference from the registry and the dispatcher; a future normalization is optional, not
  required for B13.
- `FunctionWorker` loses per-topic customization of `max_retries`/`execute` semantics unless passed
  explicitly — acceptable because the engine owns retry (T1.1 §9), so per-worker in-process tuning is
  rarely needed.
- The topic list must be kept truthful against `spec/`; mitigated by the fail-closed readiness check
  (T1.1 §12) and the registry-coverage test below (a spec topic with no worker fails CI).

## Test strategy

- **Unchanged:** the 13 function modules' existing unit tests keep importing and calling the free
  functions directly — proves business logic, untouched by this ADR.
- **New — adapter:** `FunctionWorker` wrapping test — assert `run()` calls the wrapped `fn`, returns its
  dict, and emits `record_worker_execution`/`record_worker_error` with the right `{worker,topic}` labels.
- **New — registry coverage (fail-closed):** `register_all_workers(registry)` then assert
  `registry.count()` == expected and `set(registry.list_topics())` ⊇ the spec's external-task topics; a
  spec topic with **no** worker → test **fails** (kills "dead registry" regressions).
- **New — error classifier:** guard error → `bpmnError` outcome, asserted **never retried**; `ValueError`
  → configured outcome; transient → `failure` with `retries=task.retries-1`.
- **Integration (T3.1):** one real-engine task per topic → worker executes → complete/failure/incident.

## Supersedes

—  (Extends ADR-0008 autonomy guards and ADR-0010 observability; consumed by T1.1 runtime spine and T1.2.)
