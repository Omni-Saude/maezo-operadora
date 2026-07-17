# ADR-0028: Avaliacao de DMN em runtime — engine-side (CIB Seven REST), fail-closed

**Status:** Proposed (2026-07-17) · **Data:** 2026-07-17 · **Area:** Regras de negocio / Orquestracao (runtime, B5)

> **Zero-trust note (ground rule 1 — no self-certification).** Every count, path, and line cited below was
> verified this session against HEAD `ab7708d` (v2) and the READ-ONLY v1 donor `Maezo-Healthcare-Plan`.
> This ADR is authored by process-engine-specialist (R1, design phase); it decides strategy and gates
> **T1.5**, but writes **no** `src/` or `spec/` code. An independent R1 verification and orchestrator
> ratification follow — the acceptance in the plan (T1.4: *"Every DMN table evaluated by conformant engine
> in tests; no-match raises, never `{}`"*) is the contract. DMN drives money-path decisions; **Blocked ≠
> done**.

## Contexto

**The defect (B5, plan §29).** The DMN layer is **dead at runtime**. Three independent, verified facts:

1. **`DmnServer` is instantiated nowhere in production.** The v2 in-process XML evaluator
   `src/maezo/tools/mcp_dmn/server.py` is only *imported* by its own `__init__.py` and *referenced* by a
   single unit test (`tests/unit/tools/test_mcp_dmn.py`). Grep for consumers across `src/` and `tests/`
   returns exactly three files: the package (`__init__.py`, `server.py`) and that one test. **Zero
   production consumers.**
2. **The local evaluator fails OPEN.** On no matching rule it logs a warning and `return {}`
   (`server.py:204-205`) — the plan's "fails open (`{}`)". For a decision that gates a money movement or an
   adverse action, an empty dict is a silent non-decision.
3. **The local evaluator lacks comparison operators.** `_match_condition` implements only boolean and
   string/number **equality** (`server.py:218-240`); the numeric branch does `float(actual) == cond_num`
   (`server.py:233-235`) with an explicit *"future: >=, <=, >, <"* placeholder comment (`server.py:232`).
   FEEL comparison (`>=`,`<=`,`>`,`<`), ranges (`[a..b]`), lists (`"A","B"`) and `not(...)` are unsupported.

Because the DMN layer is dead, **workers re-implement DMN rules in Python** — restoring the very
duplication ADR-0012 ("DMN e a fonte unica de regra de negocio deterministica") forbids. The most
dangerous instance is on the **money path**: `pagto_alcada` (the payment authority ladder) is hand-coded in
`src/maezo/tools/workers/pagto.py:119-152` with the exact `<=` thresholds
(`valor_cents <= 10_000_000 … <= 1_000_000_000`) that the local evaluator **cannot** evaluate. The dead DMN
layer *forced* a Python fork precisely where the table needs comparison operators.

**Engine facts (verified — T1.3, live).** All artifacts already deploy to the real engine:
`docker-compose.yml` runs `cibseven/cibseven:2.1.0` (Camunda-7-compatible REST under `/engine-rest`,
T1.1-runtime-spine §2). T1.3 proved a full-tree deploy of **20/20 process defs (16 BPMN) + 55/55 decision
defs (54 DMN files)** succeed, idempotent, `0` rejected (ledger T1.3 @ `638ded3` / artifact-fix
`21e6cfd`). CIB Seven exposes `POST /engine-rest/decision-definition/key/{key}/evaluate` and, for
provenance, `GET /engine-rest/decision-definition/key/{key}` → `{id, version, deploymentId}`.

**The v1 donor already solved this, engine-side (READ-ONLY).** v1's runtime evaluated DMN **through the
engine**, not a local parser. `src/maezo/tools/mcp_dmn/server.py` in the donor defines a
`DmnTransport` Protocol, a real `CibSevenDmnTransport` posting to
`/decision-definition/key/{key}/evaluate` and *raising* `DmnEvaluationError` on non-2xx / unreachable
(donor `server.py:43-134`), a labeled `FakeDmnTransport` that **fails closed** on an unregistered key
(donor `server.py:140-161`), and a thin `DmnServer(transport)` tool wrapper returning a `DecisionResult`
with `decision_version` provenance (donor `server.py:167-205`). The daemon builds it once and injects it as
a `dmn=` seam into workers (donor `runtime/worker_runtime/service.py:56,118,232,514`); fraude's scoring path shows
the fail-closed contract — *"uma tabela indisponivel … levanta `DmnEvaluationError`"* (donor
`fraude.py:335,344` `rows, _version = await dmn.evaluate(...)`). The test harness resolves the authoritative
version via `GET /decision-definition/key/{key}` and evaluates via
`POST /decision-definition/key/{key}/evaluate` (donor `tests/integration/processes/engine_rest.py:336-361`).

**Why this ADR now.** The plan mandates *prefer engine-side evaluation via CIB Seven*; local `mcp_dmn`
demoted to a dev tool that, if kept, must implement `>=/<=/>/<` and fail **CLOSED** on no-match. This ADR
ratifies that direction, specifies the client seam (consistent with T1.1's transport shape), the
versioning/tenant/error/audit semantics, and gates **T1.5** (deleting the Python re-implementations).

**Alternatives considered.**

- **(A) Keep the local XML evaluator, harden it.** Implement full FEEL (`>=/<=/>/<`, ranges, lists, `not`,
  date/duration) and make no-match raise. **Rejected as the runtime path:** this maintains a *second FEEL
  engine* alongside CIB Seven's — the exact drift/dead-code class B5 is. It would need its own conformance
  suite against the engine forever, and it duplicates a real, already-deployed dependency (ADR-0011). It
  survives only as an optional **dev/test** artifact under strict conditions (Decisao §2).
- **(B) Engine-side via REST (RECOMMENDED).** Port the donor's `DmnTransport`/`CibSevenDmnTransport` seam;
  workers call `dmn.evaluate(key, vars)`; the engine — the single source of DMN truth — evaluates the
  deployed table exactly as authored, including all comparison/range/list operators. Fail-closed on every
  error class; version provenance flows to the audit record.
- **(C) Hybrid — engine primary, local fallback when engine unreachable.** **Rejected:** a fallback that
  silently substitutes a *different* evaluator on a money-path decision is a fail-open in disguise; an
  unreachable engine must **raise / route to human**, never be masked by a parallel implementation.

## Decisao

**Adopt (B): DMN is evaluated engine-side, at runtime, via CIB Seven REST. The local XML `DmnServer` is
deprecated. No worker decides what a deployed DMN table decides.**

### 1. Runtime evaluation = engine-side, via a `DmnTransport` seam (consistent with T1.1)

We add a DMN client seam that mirrors T1.1's `WorkerTransport` triple (Protocol + real + Fake), ported from
the v1 donor:

- **`DmnTransport` Protocol** — `async def evaluate(decision_key: str, variables: dict[str, Any]) ->
  tuple[list[dict[str, Any]], DmnVersion]`. One method, injectable for unit tests without faking the engine
  in integration tests (ADR-0011). (Donor `server.py:59-69`.)
- **`CibSevenDmnTransport(base_url, *, auth_token=None)`** — real `httpx.AsyncClient`, `POST
  /decision-definition/key/{key}/evaluate` under `/engine-rest`. Reuses the donor's `_to_camunda_vars`
  typing — **`Long` for ints outside int32** (donor `server.py:96-107`). This is **load-bearing** for
  `pagto_alcada`, whose top alcada band is unbounded; a payment above int32-max cents (R$21.47M) —
  verified `3_000_000_000` as Integer → HTTP 400 overflow — requires `Long`. (ADR-0018 money typing;
  identical rationale to T1.1 §5.)
- **`FakeDmnTransport`** — labeled, import-lint-fenced test double; `evaluate` **raises**
  `DmnEvaluationError` on an unregistered key (donor `server.py:140-161`). Never imported by production.

**Wiring.** The DMN transport is built **once at daemon boot** (worker_runtime `service.py`, T1.1 §3 step B)
and injected as the **`dmn=` seam** through the per-module bootstraps
`register_<domain>_workers(harness, kafka, dmn=..., **seams)` — a seam ADR-0026 §2 already reserves
("Other seams (`audit=`, `dmn=`, `dispatcher=`, `erasure=`) pass the same way"). Workers receive `dmn` by
`functools.partial` closure at wrap time; a worker with no DMN dependency ignores it. Lifecycle
(construct/close) matches the worker transport.

### 2. Decision-versioning + tenant semantics

- **Evaluate LATEST deployed version by default.** `/decision-definition/key/{key}/evaluate` targets the
  latest version of the key. Deployments are versioned + idempotent (T1.3), so "latest" is deterministic
  per deploy. Pin-by-version (`/decision-definition/{id}/evaluate`) is available but **not** the default.
- **Authoritative version provenance (fixes a v1 fail-open).** The concrete version is resolved via `GET
  /decision-definition/key/{key}[/tenant-id/{tenant}]` → `{id, version, deploymentId}` (donor
  `engine_rest.py:336-347`), cached per key/tenant/deployment. We do **not** copy the donor's weak fallback
  of reading header `X-Decision-Definition-Id` and defaulting to `"unknown"` (donor `server.py:132-133`) —
  an unresolvable version **raises** (fail-closed provenance: no `"unknown"` on the money path).
- **Tenant scoping.** CIB Seven decision definitions are tenant-scoped, and `contract_extraction` generates
  per-tenant DMN (ADR-0012) under federated tenancy (ADR-0004). When a tenant-specific table exists, evaluate
  `/decision-definition/key/{key}/tenant-id/{tenant}/evaluate`. **Fail-closed:** if a required tenant table
  is absent, **raise** — never silently fall back to a global table on a coverage/money decision.

### 3. Error semantics — NEVER `{}` (the fail-closed core)

`evaluate` maps every non-happy path to a raise; the worker maps a raise to an engine outcome per T1.1 §9 /
ADR-0026 §5 (guard/validation → `failure(retries=0)` → engine incident → human; transient → engine retry):

| Condition | `DmnTransport` behavior | Worker consequence |
|---|---|---|
| Engine 4xx/5xx | raise `DmnEvaluationError` (donor `server.py:119-122`) | classify → incident / retry (T1.1 §9) |
| Engine unreachable / timeout | raise `DmnEvaluationError` (donor `server.py:123-124`) | transient → engine-side retry → incident at 0 |
| **Empty result `[]`** (no rule matched, no default output) | return `[]` (honestly) | **worker maps empty → `ANALISE_HUMANA` (raise/route to human), NEVER `{}`** — the direct fix for v2 `server.py:204-205` |
| Version unresolvable | raise `DmnEvaluationError` | fail-closed provenance |

Well-formed tables carry a conservative catch-all row (→ `ANALISE_HUMANA`), so empty should not occur; if it
does, the caller treats "no decision" as deny-and-escalate. **No path returns an empty dict as if it were a
decision.**

### 4. Audit integration — populate `dmn_versions` (closes the T1.5 TODO for T1.10)

Every DMN consulted during a decision contributes to the audit record:
`dmn_versions[decision_key] = {"version": N, "id": ..., "deploymentId": ...}` (from §2's authoritative
lookup). The worker/agent passes this into `AuditRecord(dmn_versions=...)`
(`src/maezo/gateway/audit.py:174`), persisted as the `NOT NULL … jsonb` column
(`audit_postgres.py:121,236`). This **closes the explicit `TODO(T1.5)` at `audit.py:148-157`** ("no caller
populates this yet — workers still re-implement DMN-like logic in Python … Once T1.5 lands, wire the real
versions through here"). After this ADR's implementation, every money-path decision's audit row cites the
exact DMN version that produced it (ADR-0007 non-repudiation), which is impossible today because no DMN is
evaluated at all. *Schema note:* v2 keeps `dmn_versions` on the `AuditRecord`; the v1 donor had removed it
in favor of `decision_basis.dmn_refs` (donor `server.py:18-19`, GAP-XOBS-7) — we follow **v2's** schema
(the `dmn_versions` column), not v1's.

## Fate of `mcp_dmn` (Decisao §2 continued — the demotion)

**Consumers today:** production = **0** (`DmnServer` never instantiated); tests = **1**
(`tests/unit/tools/test_mcp_dmn.py`).

**Decision: deprecate the local XML `DmnServer` / `_match_condition` evaluator.** Replace the package
contents with the engine-backed seam (§1). The only sanctioned non-engine artifact is `FakeDmnTransport`
(a labeled, fail-closed test double), **not** a real local FEEL reimplementation — hardening the XML parser
to full FEEL means maintaining a second decision engine, the drift class B5 embodies.

**IF an offline evaluator is nonetheless retained (dev/test only), it MUST (hard requirements):**
(a) implement `>=`,`<=`,`>`,`<`, ranges `[a..b]`/`(a..b]`, lists `"A","B"`, and `not(...)`;
(b) no-match → **raise** (never `{}`);
(c) be import-lint-fenced from production;
(d) be validated against the same golden-parity corpus as the engine (§ migration).
We **recommend against (a)** and deprecate. *(Note: the plan's T1.9 flags a `dentro_teto` echo-through at
`mcp_dmn/server.py:51`; the deprecation must not re-introduce it — `dentro_teto` is an input fact computed
by the policy layer (ADR-0025), never echoed by the evaluator.)*

## Migration plan for T1.5 (per-worker → decision-key map)

Each Python re-implementation maps to a **deployed** decision key (all present in the 55/55 deploy, T1.3):

| Worker fn (path:line) | Decision key(s) | Deployed | Comparison ops? | Path sensitivity |
|---|---|---|---|---|
| `pagto.py:119-152` classify alcada | `pagto_alcada` | ✅ | **YES `<=`** | **money (authority ladder)** |
| `pagto.py:81` | `pagto_admissibility` | ✅ | no | money |
| `inadimplencia.py:77-116` `assess_status` | `inadimplencia_status` | ✅ | no (enum/list) | contract termination (adverse-adjacent) |
| `contas.py:261-299` `prepare_triage_dossier` | `glosa_triage` | ✅ | no (list) | glosa (money) |
| `contas.py:470` normalize reason | `glosa_reason_normalization` | ✅ | no (list) | glosa |
| `recurso.py:162-189` `assess_eligibility` | `recurso_admissibility` + `recurso_eligibility` | ✅ | no (list) | glosa appeal |
| `ans_submit.py:261-305` `retry_submission` | `ans_retry_policy` | ✅ | no | regulatory submission |
| `ans_cron.py:116` | `ans_calendar` | ✅ | no | ANS calendar |
| `adequacao.py:73` | `adequacao_gap` + `adequacao_remediation_routing` | ✅ | **`adequacao_gap` YES** | network adequacy |
| `credenciamento.py:69` | `cred_admissibility` + `cred_route` | ✅ | no | provider credentialing |
| `lgpd.py:102` | `lgpd_dsr_routing` | ✅ | no (list) | LGPD/PHI |

**Scope warning (T1.5 must not under-scope).** The plan's explicit deletion list
(`inadimplencia.py:80`, `contas.py:271`, `recurso.py:168`, `ans_submit.py:280`) and the acceptance grep
`"DMN-like" → 0` are **different, only partially-overlapping** subsets. `grep "DMN-like"` today returns
**4** hits — `adequacao.py:73`, `credenciamento.py:69`, `inadimplencia.py:80`, `pagto.py:81` — of which only
`inadimplencia.py:80` is on the explicit list. The full re-implementation set is the **~11 functions across
9 modules** above. T1.5 must use this inventory, and the acceptance grep should be broadened beyond
`"DMN-like"` (also `"Apply DMN"`, `"DMN <key> \("`, `"Baseado na DMN"`, `"regras da .* DMN"`).

**Golden-output parity test strategy (risk mitigation — run both paths before deleting the Python).**

1. For each (worker fn, decision key), assemble a recorded input corpus covering **every threshold boundary**
   (e.g. `pagto_alcada` at `10_000_000 ± 1`, `50_000_000 ± 1`, …; `carencia_check` at `730/300/180/1`; each
   list branch and the catch-all).
2. Run **both** the Python fn **and** `dmn.evaluate(key, inputs)` against the **live compose engine**
   (ADR-0011, `docker compose --profile core`) on every corpus row; assert outputs equal.
3. Only after **100% parity** in CI: delete the Python re-implementation, repoint the worker to the seam,
   keep the corpus as an **engine-only regression** test.
4. **spec/ is the single source of truth (constraint 5).** On any Python/DMN divergence, the **DMN wins**;
   the Python was authoritative-by-accident. If the DMN itself is wrong, fix forward **in spec**, never
   patch the Python back.
5. **Order:** cut over non-adverse routing tables first (`ans_retry_policy`, `ans_calendar`, `adequacao`)
   to exercise the seam; then the **money/adverse** tables (`pagto_alcada`, `glosa_*`, `recurso_*`,
   `lgpd_dsr_routing`) under **policy-guardian review** (R3-forbidden territory). The `dentro_teto`
   invariant (ADR-0025 / T1.9) must hold across the cutover.

## The comparison-operator inventory (the forcing argument) — VERIFIED

The local evaluator only does boolean + exact equality. An authoritative parse of `spec/processes/dmn/*.dmn`
(inputEntry `<text>` per `dmn:decision`) finds **13** of the 55 decisions (13 of 54 files) use FEEL
**comparison/range** operators that the local evaluator cannot evaluate:

| # | DMN file / decision | Operators (examples) |
|---|---|---|
| 1 | `pagto_alcada` | `<= 10000000`, `(10000000..50000000]`, `(50000000..200000000]`, `(200000000..1000000000]` |
| 2 | `carencia_check` | `< 730`, `>= 730`, `< 300`, `>= 300`, `< 180`, `>= 180`, `< 1`, `>= 1` |
| 3 | `adequacao_gap` | `> 30`, `<= 60`, `<= 30`, `> 30.0`, `<= 50.0`, `<= 30.0`, `> 0` |
| 4 | `dut_criteria_bariatrica` | `imc < 35`, `tentativas >= 1`, `<= 0` |
| 5 | `dut_criteria_terapias_especiais` | `<= 20`, `> 20`, `<= 8` |
| 6 | `fraude_indicadores` | `score >= 50`, `>= 20`, `< 20` |
| 7 | `glosa_classification` | `denial_ratio >= 1.0`, `(0.0..1.0)` |
| 8 | `contas_sla` | `valor_apresentado_brl > 50000.0` |
| 9 | `recurso_sla` | `valor_glosado_brl > 50000.0` |
| 10 | `lucas_billing_admissibility` | `ciclos_sem_conciliacao >= 1` |
| 11 | `triage_redflag_adult` | `idade_anos >= 65` |
| 12 | `triage_redflag_gestante` | `>= 20`, `>= 26`, `< 37` |
| 13 | `triage_redflag_pediatric` | `idade_meses < 3` |

**Correction (zero-trust, constraint 3 — no fabricated results).** The plan's B5 estimate is *"14/54
tables"*; the authoritative parse of the spec (single source of truth) yields **13**. We report **13** and
list them; the estimate was one high.

**The argument is actually stronger.** Beyond the 13, another **13** tables use **list-disjunction** unary
tests (`"A","B"`) the same local evaluator mishandles (e.g. `auth_sla`, `cancel_admissibility`,
`glosa_reason_normalization`, `inadimplencia_status`, `lgpd_dsr_routing`, `nip_classification`,
`recurso_eligibility`, `reembolso_sla`, `triage_redflag_mental_health`, …). Worse than fail-open: where such
a table has a trailing catch-all `-` row, the local evaluator silently returns the **wrong** (catch-all)
output non-empty. Union: **26 of 55 decisions** are mis-evaluated or fail-open under the local parser; only
~29 pure boolean/equality tables happen to work. **These 13 comparison tables run correctly ONLY
engine-side today — which is exactly why the money-path `pagto_alcada` was hand-forked into Python
(`pagto.py:119-152`). That is the forcing argument for engine-side evaluation.**

## Consequencias

**Positivas:**
- ADR-0012 restored: DMN is the single source of deterministic rule; workers stop re-implementing it.
- All 55 decisions (incl. the 13 comparison tables) evaluate exactly as authored — the engine is a real,
  deployed dependency (ADR-0011), not a parallel FEEL reimplementation.
- Fail-closed everywhere: no-decision → raise/human, never `{}`; unreachable engine → raise, never masked.
- `dmn_versions` audit provenance becomes real (closes `audit.py:148-157`; ADR-0007 non-repudiation; feeds
  T1.10 records).
- The client seam matches T1.1's transport shape and ADR-0026's `dmn=` seam → cheap T3.1 fixture port
  (donor `FakeDmnTransport` / `DmnTransport` names preserved).

**Negativas (aceitas):**
- Runtime DMN evaluation now requires the engine to be reachable — but the engine is already a hard
  dependency for external-task dispatch (T1.1); a DMN call is on the same failure/retry/incident plane.
- A network round-trip per decision vs an in-process parse — acceptable for correctness + auditability;
  version metadata is cached per key/tenant/deployment.
- Two DMN-adjacent code artifacts remain during migration (Python re-impls + engine seam) until T1.5's
  golden-parity cutover deletes the Python; mitigated by the parity gate.

## Open questions (need orchestrator / R1)

1. **Q-1 (empty-result policy per hit policy).** Confirm the universal rule "empty result → `ANALISE_HUMANA`
   / raise" for FIRST/UNIQUE tables, and that COLLECT tables that *may* legitimately return `[]` are
   enumerated so their workers treat empty as a valid (non-escalating) outcome. Recommend: default escalate;
   enumerate the exceptions.
2. **Q-2 (version pinning).** Ratify "evaluate latest, record concrete version" as default vs pin-by-version
   for regulated tables (e.g. `pagto_alcada`, DUT). Recommend latest+record; revisit pinning if an auditor
   requires replay against a fixed version.
3. **Q-3 (tenant fallback).** Confirm fail-closed "no global fallback when a tenant table is expected" vs a
   documented allow-list of tables where a global default is safe. Recommend fail-closed.
4. **Q-4 (mcp_dmn deprecation depth).** Ratify full deprecation of the local XML `DmnServer` (recommended)
   vs retaining a hardened offline evaluator under the four §2 conditions. Recommend deprecate; keep only
   `FakeDmnTransport`.
5. **Q-5 (T1.5 grep breadth).** Approve broadening the T1.5 acceptance grep beyond `"DMN-like"` (which
   catches 4 of ~11) to the full inventory here.

## Supersedes

—  (Restores and operationalizes **ADR-0012**; consumes **T1.1** transport-seam shape and **ADR-0026**
`dmn=` bootstrap seam; closes the `dmn_versions` TODO for **T1.10** audit records; gates **T1.5**.)
