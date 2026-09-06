# ADR-0026: Worker standardization — adapter over subclassing

**Status:** Accepted — ratified by orchestrator (Fable 5) 2026-07-27, recorded as DL-0036 · **Data:** 2026-07-16 · **Area:** Orquestracao (runtime spine, B13)

> Draft for the orchestrator's ratification. Ground rule 2 (author ≠ verifier): this ADR is authored by
> runtime-spine-engineer (R1) and must be R1-verified before it moves to Accepted. Every count and
> signature below was verified at HEAD `a0427d2`.

> **Ratification amendment note (2026-07-27):** implementation is R1-verified on main
> (`docs/evidence-ledger.md:53`, "verified — B13 closed, 16/16 registered 99 topics (merged #52)").
> **Factual correction, ground-truth count as of ratification:** the original "16 modulos" text below
> (Decisao §1/§4, and the README row) reflected the T1.2-day snapshot. A **later**, separately-merged
> task (T3.1 "events.publish fix", `docs/evidence-ledger.md:66`) added a **17th** bootstrap —
> `register_events_workers` (`src/maezo/tools/workers/events.py`, `bootstrap.py:1-19,45-46`) — a **raw**
> `harness.register()` handler, not `FunctionWorker`-wrapped, because it needs `task.business_key`/
> `process_instance_id`, which the dict-first `FunctionWorker` boundary (Decisao §1) does not expose.
> This is disclosed here as an amendment, not a silent rewrite of Decisao §1/§4's original "16" text:
> the *adapter* decision (Decisao §1, `FunctionWorker`) still holds for 16 of the 17 modules; the 17th
> is the escape hatch Decisao's "(D) Hybrid" note on `run_async` already permits in spirit, now named
> explicitly. `register_all_workers`/`ALL_WORKER_BOOTSTRAPS` (Decisao §4) compose all **17** bootstraps
> today. Ratifying this ADR's Decisao (adapter over subclassing) does not require re-litigating this
> count; it is recorded so the "16" in the body/README is understood as historical, not current.

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
    functions**. No topic, no registry, no `WorkerBase`.
- **Signature census (AST-verified — corrects this ADR's first draft, which claimed "~84 uniform
  `fn(variables: dict)` functions"; that was refuted by R1 verification and re-measured here).**
  Across the 13 function modules there are **97 module-level `def`s**, of which only **42** take the
  dict-first shape `fn(variables: dict[str, Any]) -> dict[str, Any]`. The dict-first modules are
  `adequacao` (5/5), `credenciamento` (6/6), `fraude` (10/10), `inadimplencia` (7/7), `pagto` (6/6),
  `programa` (6/6), `ans_cron` (2/3). **Six modules have ZERO dict-first functions** — `ans_submit` (0/6),
  `cancel` (0/9), `contas` (0/12), `nip` (0/10), `recurso` (0/8), `reembolso` (0/9): they use **typed
  dataclass I/O**, e.g. `validate_recurso(input_data: RecursoInput) -> RecursoValidationResult`
  (`recurso.py:122`), with no existing from-dict/to-dict marshalling helpers. The class path is uniform:
  `execute(self, process_vars: dict[str, Any]) -> dict[str, Any]` ×16 (15 subclasses + the abstract,
  `base.py:93`). Consequence: **one adapter class can front every module, but it cannot *directly* wrap
  the six typed-I/O modules** — those need explicit dict-boundary entry functions (Decisao §2b).
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

**Adendo 2026-09-06 (R-089 / gap `ADR-0026-0028-STALE-ANSCRON`, owner-decision, `docs/adr/` CODEOWNED,
disclosed in `scripts/ci/check_doc_symbol_citations.py::_DISCLOSED_ROT`).** O exemplo acima (nao
editado — `docs/adr/README.md:8` proibe reescrita in-loco de ADR `Accepted`) cita `check_calendar`
como um dos dois nomes importados de `ans_cron.py` por `tests/unit/tools/workers/test_ans_cron.py`.
`check_calendar` foi **REMOVIDA** desse modulo (nao renomeada, nao desativada) apos a reconciliacao
de taxonomia GAP-ANS-1/ANS-CRON-DEAD-CODE: era uma reimplementacao Python de `ans_calendar.dmn` que
a propria docstring do modulo ja declarava bloqueada pela divergencia de taxonomia; com a taxonomia
reconciliada, o portao do **ADR-0028 §7** ("only after 100% parity in CI: delete the Python
re-implementation") passou a valer, e a tabela e avaliada hoje ENGINE-SIDE pelo businessRuleTask
`BRT_Calendario` (`camunda:decisionRef="ans_calendar"`) em
`spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn`. O import real hoje (mesmo
arquivo de teste) e `from maezo.tools.workers.ans_cron import (..., parse_competencia_referencia_iso,
register_ans_cron_workers, trigger_submissions, ...)` — o ponto arquitetural do bullet acima ("tests
bind to functions, not classes") continua verdadeiro com esses nomes; so o exemplo literal apodreceu.
Ver **ADR-0028**'s proprio adendo 2026-09-06 para a re-ancoragem do lado da tabela de decision-keys.

**Honesty note — v2 has already diverged from v1.** The v1 donor has **no** `WorkerBase` class at all:
its workers are **async closures** `async def handler(task: ExternalTask) -> Mapping|None`, produced by
`make_*_handler(...)` factories and registered by topic via `register_<name>_workers(harness, kafka)`;
retry lived in the harness (tenacity) and metrics in the harness (`_emit_worker_task_outcome`), **not** in
the worker (v1 `tools/workers/harness.py`, `service.py`). v2, by contrast, invented `WorkerBase` +
`WorkerRegistry` and wrote its workers as **sync** units — 15 `WorkerBase` subclasses and 97 sync
module-level functions (42 dict-first, the rest typed-dataclass I/O — see census). So there are
effectively **three** shapes in play (v1 async closures; v2
sync classes; v2 sync functions), and any decision must be honest that "port v1" and "keep v2" pull in
different directions. This ADR decides the **v2 worker→registry** binding; the **runtime-spine** surface
(`WorkerHarness`/`CibSevenWorkerTransport`/`register_*_workers` names) is preserved separately by the T1.1
design §16 so T3.1 fixtures port regardless of which option below is chosen.

**The tension.** We need all 16 registrable (retry/metrics, dead registry removed) **without** rewriting
the 97 functions and their tests, while (a) not discarding v2's 15 `WorkerBase` subclasses and their ~39
passing `worker.run()` tests, and (b) preserving the fixture-port path for T3.1 (v1's `register_<name>_workers`
bootstrap names — T1.1 §16).

**Alternatives considered (four real options).**

- **(A) Subclassing** — turn each external-task-backing free function into a `WorkerBase` subclass —
  up to ~97 new trivial classes
  (or fewer classes multiplexing several topics through one `execute`, which breaks WorkerBase's
  one-topic/one-execute contract). Deletes/duplicates the free functions → rewrites ~13 function-test
  modules that import and call them, and orphans the result dataclasses' call sites. Maximum churn on the
  money/PHI paths (R3-forbidden territory), maximum risk.
- **(B) Adapter (RECOMMENDED)** — one `FunctionWorker(WorkerBase)` that wraps `(topic, dict-boundary
  callable)`; each module gains a small `register_<domain>_workers(harness, kafka)` bootstrap (donor
  contract, see Decisao §2/§4). The 42 dict-first functions wrap directly; the six typed-I/O modules get
  thin **dict-boundary entry functions** (Decisao §2b) so their typed internals and tests stay untouched.
  The 15 subclasses and their tests are **untouched**; registry/retry/metrics are added *on top*.
- **(C) Adopt v1's async-closure + `register_*` model wholesale** — rewrite all 16 v2 modules into async
  `handler(task)` closures, register via `harness.register(topic, handler)`, and get retry/metrics from the
  **harness** (as v1 does) rather than `WorkerBase`. **Maximizes T3.1 fixture fidelity at the *worker
  internals* level**, but: (i) requires rewriting **all 16** v2 modules (the 97 sync functions **and** the
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

2. **Per-module bootstrap — ONE contract, the donor's** (this reconciles the R3-flagged contradiction
   with T1.1 §16.1; the earlier `(registry)`-only spelling is superseded):
   ```python
   def register_<domain>_workers(harness: WorkerHarness,
                                 kafka: KafkaPublisher | None = None,
                                 **seams: Any) -> None
   ```
   The harness **owns** a `WorkerRegistry` and exposes `register_worker(worker: WorkerBase) -> None`,
   which delegates to `registry.register(worker.topic, worker)`; `fetchAndLock` topics =
   `registry.list_topics()`. **Kafka wiring is explicit:** workers never reach for a global producer —
   the bootstrap closes the `KafkaPublisher` (or `FakeKafkaPublisher` in tests) over the wrapped callable
   at wrap time (`functools.partial(entry_fn, kafka=kafka)`); a module with no domain events ignores the
   parameter. Other seams (`audit=`, `dmn=`, `dispatcher=`, `erasure=` — mirroring the donor's bootstrap
   kwargs) pass the same way.

   **2a. Dict-first modules (7 — 42 functions):** wrap directly.
   ```python
   # adequacao.py
   def register_adequacao_workers(harness: WorkerHarness, kafka: KafkaPublisher | None = None) -> None:
       harness.register_worker(FunctionWorker("operadora.adequacao.measure_gap", measure_gap))
       harness.register_worker(FunctionWorker("operadora.adequacao.route_remediation", route_remediation))
       ...  # register_fallback_commitment is the L0-guarded topic (adequacao.py:165-206)
   ```

   **2b. Typed-I/O modules (6 — `ans_submit, cancel, contas, nip, recurso, reembolso`; zero dict-first
   functions):** each external-task topic gets a thin, module-local **dict-boundary entry function** that
   does the marshalling — the typed function itself is **not modified**:
   ```python
   # recurso.py — NEW entry fn per external-task topic (typed internals untouched)
   def validate_recurso_entry(variables: dict[str, Any], *,
                              kafka: KafkaPublisher | None = None) -> dict[str, Any]:
       input_data = RecursoInput(**_pick(variables, RecursoInput))   # fail-closed: unknown/missing
       result = validate_recurso(input_data)                          # -> RecursoValidationResult
       return dataclasses.asdict(result)                              # dict back to process vars
   ```
   Marshalling rules (fail-closed): inbound `variables` → typed input dataclass with **explicit** field
   selection; a missing/invalid required field raises the module's own `*Invalido*Error` (never a silent
   default on a guarded path); outbound typed result → `dataclasses.asdict` (flat dicts; nested
   dataclasses serialize to `Json` vars via the transport's `_to_camunda_var`, T1.1 §5). One entry
   function per external-task topic in those modules — bounded by the spec topic list, not by the 54
   internal functions. The bootstrap then wraps: `harness.register_worker(FunctionWorker(topic,
   functools.partial(validate_recurso_entry, kafka=kafka)))`.

   Topics come from `spec/` (BPMN external-task `topicName`), **not** a hand-list — the registry is
   validated against the spec at readiness (T1.1 §12, fail-closed: a spec topic with no worker → not
   ready). Helper functions (non-external-task funcs) are simply not registered.

3. **Class modules** register directly: `register_<domain>_workers(harness, kafka=None)` calls
   `harness.register_worker(worker())` for each of the 15 subclasses (they already are `WorkerBase`).

4. **`register_all_workers(harness, kafka, **seams)`** composes all 16 module bootstraps (the donor's
   `_register_all_workers` shape, T1.1 §16). The runtime daemon calls it in boot step B.

5. **Error → engine-outcome classifier** (lives once, in the dispatcher, consumed via a small
   `classify_worker_error(exc)` helper next to `FunctionWorker`): guard errors (`ERR_*_NOT_HUMAN`
   constants; `*NotHumanError(PermissionError)`) → **`failure(retries=0)` → engine incident, never
   retried** (engine-guaranteed fail-closed; retrying an L0 guard could drive an adverse action —
   forbidden, ADR-0008). `bpmnError` is a **per-code opt-in** allowed only where the T1.1 §9
   boundary-proof gate shows a matching error boundary in every consuming BPMN — an *unmodeled*
   `bpmnError` silently **ends the process with no incident** (live-proven on CIB Seven 2.1.0), which is
   exactly the silent-drop failure mode L0 forbids. `ValueError`-family → `failure(retries=0)` by
   default, `bpmnError` only if gate-proven (T1.1 open Q-3); transient (`RuntimeError`/IO/
   `*RetryEsgotado*`) → `failure(retries=task.retries-1, retryTimeout=backoff)` (engine-side retry →
   incident at 0). This keeps the mapping in **one** place instead of scattered across 13 modules.

**Why adapter wins (grounded):** one new class + 16 tiny bootstraps + ~a-dozen-per-module entry functions
for the six typed-I/O modules, vs ~97 new subclasses; the 97 functions and their unit tests stay
**untouched** (they keep importing and calling the function — logic coverage preserved) while the adapter
adds registry/retry/metrics coverage on top; result dataclasses keep their call sites (the entry functions
*consume* them rather than replace them); the guard-error mapping is centralized and fail-closed; and the
`register_*_workers(harness, kafka)` bootstrap contract is the donor's own, so T3.1 fixtures port with
adaptation only (T1.1 §16).

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
- **New — dict-boundary entry functions (typed-I/O modules):** per entry fn, assert round-trip
  `variables → typed input → typed result → dict` equals calling the typed function directly; assert a
  missing/invalid required field raises the module's `*Invalido*Error` (fail-closed marshalling, never a
  silent default on a guarded path).
- **New — registry coverage (fail-closed):** `register_all_workers(harness, kafka=FakeKafkaPublisher())`
  then assert `registry.count()` == expected and `set(registry.list_topics())` ⊇ the spec's external-task
  topics; a spec topic with **no** worker → test **fails** (kills "dead registry" regressions).
- **New — error classifier:** guard error → **`failure(retries=0)`** outcome (engine incident), asserted
  **never retried** and **never** an unproven `bpmnError`; a gate-proven code → `bpmnError`; an unproven
  `WorkerBpmnError` code demotes to `failure(retries=0)` (and fails the CI boundary-proof gate, T1.1 §9);
  `ValueError` → configured outcome; transient → `failure` with `retries=task.retries-1`.
- **Integration (T3.1):** one real-engine task per topic → worker executes → complete/failure/incident.

## Supersedes

—  (Extends ADR-0008 autonomy guards and ADR-0010 observability; consumed by T1.1 runtime spine and T1.2.)
