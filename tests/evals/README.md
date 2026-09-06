# `tests/evals/` — behavioral evals (T3.2, ADR-0009 golden-dataset promotion gate)

This is the golden-dataset regression baseline ADR-0009 requires ("Golden dataset por agente
(`tests/evals/golden/`) rodado a cada mudanca de prompt/grafo/modelo — gate de CI obrigatorio
para promover"). It exists alongside `tests/unit/agents/` — unit tests already cover the
deterministic graph-handling fail-closed paths with inline scripted inference; this suite is the
**versioned, data-driven golden matrix** (JSON files, not inline Python) that CI re-runs on every
prompt/graph/model change, plus the live-LLM extraction-quality delta unit tests structurally
cannot see.

Full design record: see the T3.2 design doc handed to this build wave (ratified decomposition,
CI-lane ground truth, the 44-eval taxonomy). This file covers only the practical "how do I add
one" / "how does CI consume this" mechanics.

## Two-tier model

| | Tier A (deterministic replay) | Tier B (live LLM) |
|---|---|---|
| Provider | `ReplayInferenceProvider` — scripted `recorded_llm` responses, no network, no key | the real `anthropic` provider (`InferenceProvider(settings=InferenceSettings(provider="anthropic"))`) |
| Marker | `@pytest.mark.eval` | `@pytest.mark.eval` + `@pytest.mark.llm_live` + `@live_key_skip` |
| Pass criterion | exact route / structured-field match / canary-absent (RT/SF/ABS) | threshold score >= `live.threshold` against the Tier-A baseline (TH) |
| Runs when | always, keyless, no network — **the PR `evals` lane's merge-blocking gate** | only when `MAEZO_ANTHROPIC_API_KEY`/`ANTHROPIC_API_KEY` is set — nightly-only in CI, non-blocking; **loudly skips** otherwise |
| Cost/flakiness | zero (pure Python, deterministic) | one short LLM call per case; never gates a PR |

A single golden JSON case can carry both tiers (`"tier": ["A", "B"]`) — Tier A always runs
(deterministic replay is the source of truth for routing/structured-field correctness); Tier B,
when a key is present, re-runs the SAME node against the real model and scores it against the
Tier-A baseline (`recorded_llm[0]`) rather than against a second hand-maintained dataset. This
means a Tier-B failure is a **prompt/model drift signal**, never a routing-logic bug — those are
already caught, merge-blocking, by Tier A.

**Never put a live-LLM assertion on the PR-blocking path.** Tier B is nightly-only and
non-blocking by construction (loud `skipif`, not a marker-based deselect) — a keyless/fork PR run
always sees Tier B skip, never fail.

## Directory layout (exact — B1/B2/B3 mirror this)

```
tests/evals/
  __init__.py                      # package init
  conftest.py                      # ReplayInferenceProvider, FakeWhatsAppSender, load_golden,
                                    # live_key_skip  (OWNED BY B0 — do not edit from a family wave)
  _harness.py                      # run_case, assert_expect, assert_no_leak, score_live,
                                    # assert_live_score, score_clarity, assert_clarity,
                                    # mutation-check helpers
                                    # (OWNED BY B0 — do not edit from a family wave; extended by
                                    # WP-EVALS for gap 10.3's clarity/legibility check, see below)
  README.md                        # this file (OWNED BY B0)
  test_classifier_evals.py         # helena (B0 reference) + fernando/lucas (B1 extends this file)
  test_dossier_adverse_evals.py    # rafael/valentina/marina/andre (B2)
  test_dossier_admin_evals.py      # carolina/beatriz/gustavo (B3)
  test_helena_clarity_evals.py     # helena CLAREZA-* clarity/legibility evals (WP-EVALS, gap 10.3)
  test_helena_journey_evals.py     # helena JOURNEY-* end-to-end journey eval (WP-EVALS, gap 11.5)
  test_andre_journey_evals.py      # andre JOURNEY-* end-to-end journey eval (WP-EVALS, gap 11.5)
  test_beatriz_journey_evals.py    # beatriz JOURNEY-* end-to-end journey eval (WP-EVALS, gap 11.5)
  test_carolina_journey_evals.py   # carolina JOURNEY-* end-to-end journey eval (WP-EVALS, gap 11.5)
  test_fernando_journey_evals.py   # fernando JOURNEY-* end-to-end journey eval (WP-EVALS, gap 11.5)
  test_gustavo_journey_evals.py    # gustavo JOURNEY-* end-to-end journey eval (WP-EVALS, gap 11.5)
  test_lucas_journey_evals.py      # lucas JOURNEY-* end-to-end journey eval (WP-EVALS, gap 11.5)
  test_marina_journey_evals.py     # marina JOURNEY-* end-to-end journey eval (WP-EVALS, gap 11.5)
  test_rafael_journey_evals.py     # rafael JOURNEY-* end-to-end journey eval (WP-EVALS, gap 11.5)
  test_valentina_journey_evals.py  # valentina JOURNEY-* end-to-end journey eval (WP-EVALS, gap 11.5)
  golden/
    helena/EVL-HELENA-01.json ... EVL-HELENA-08.json,
           EVL-HELENA-CLAREZA-01..03.json (gap 10.3), EVL-HELENA-JOURNEY-01.json (gap 11.5)
    fernando/EVL-FERNANDO-01.json ...
    lucas/ ... rafael/ ... valentina/ ... marina/ ... carolina/ ... andre/ ... beatriz/ ...
    gustavo/ ...
```

Each family builder wave owns its own test module + its own `golden/<agent>/` subdirectories —
disjoint files, so B1/B2/B3 can run in parallel with zero collisions. Nobody but B0 edits
`conftest.py` / `_harness.py` / this README — WP-EVALS (gaps 10.3/11.5, 2026-09), RAF-06
(RAF-01, 2026-09-04) and CC-01/CC-08 (2026-09-04) are the three documented exceptions. WP-EVALS ADDS (never edits existing
lines in) `_harness.py`'s clarity helpers and this README's own documentation of them, per its
brief ("if the runner cannot express it, extend the runner at the root"). See "Clarity/legibility
checks" and "Journey evals" below. RAF-06's scope is honest, not purely additive like WP-EVALS':
it ADDS `RuleAwareFakeDmnTransport` and the `__rules__` conditional-fixture branch of
`register_dmn_fixture` (see "dmn_fixture" below), and separately EDITS the one existing line in
`run_case` that instantiates the fake DMN transport (`FakeDmnTransport()` ->
`RuleAwareFakeDmnTransport()`) — a conditional fixture cannot be served by the base fake, so
`run_case` must hand every case the rule-aware transport, not only the ones whose `dmn_fixture`
uses `__rules__`. No existing eval changes behavior: all but 2 of the golden cases carry no
`__rules__` key and take the unchanged static-row path (`RuleAwareFakeDmnTransport.evaluate`
falls through to `FakeDmnTransport.evaluate` verbatim whenever `decision_key` has no registered
rules) — reproduce the count with `grep -l '__rules__' tests/evals/golden/*/*.json | wc -l` (2)
against `ls tests/evals/golden/*/*.json | wc -l` (59 today; it was 49 when RAF-06 landed —
CC-01/CC-08 added nine fail-start cases, none of them carrying `__rules__`).

CC-01/CC-08's scope is purely ADDITIVE (like WP-EVALS', unlike RAF-06's): it ADDS
`FailingStartCibSevenTransport` and the `_cibseven_for(case)` selector to `_harness.py`, and
this paragraph plus the `cibseven` row of the schema below to this README. It EDITS no existing
line of `run_case` beyond swapping the literal `FakeCibSevenTransport()` construction for the
selector call, which returns exactly that same double for every case that does not opt in.
Reproduce the blast radius with `grep -c '"cibseven"' tests/evals/golden/*/*.json` — only the
nine `EVL-*` fail-start cases carry the key; every other golden takes the unchanged path.

WHY THE RUNNER HAD TO BE EXTENDED AT ALL (the README's own "if the runner cannot express it,
extend the runner at the root" rule): `run_case` hard-wired a CibSeven double whose start ALWAYS
succeeds, so the engine-unavailable branch of every agent graph was unreachable from the golden
dataset. CC-01 is precisely a defect ON that branch (a fabricated success desfecho on a start
that never happened), so without this extension the fix would have shipped with no golden able
to regress it.

CC-08 (2026-09-04, same additive contract, second and final harness change of this wave) ADDS
`_phi_capable_for(case)` to `_harness.py` — see that function's own docstring for the why — and
EDITS the single `run_case` line that constructs `inference` to pass
`phi_capable=_phi_capable_for(case)` instead of the implicit default. `ReplayInferenceProvider`
itself is NOT edited (it already accepted a `phi_capable` constructor kwarg — see its own
docstring above, "a case that wants to exercise the fail-closed PHI-routing path itself
constructs `ReplayInferenceProvider(responses, phi_capable=False)`" — nothing there was ever
reachable from a golden JSON file before this change); only `run_case`'s hard-wired construction
call changes, and it returns the exact same `phi_capable=True` behaviour for every case that
omits the new `inference` block (every golden before this wave, and every golden after it that
does not opt in). CC-08's DMN-unavailable goldens need NO harness change at all — `run_case`
already builds a case's `dmn_fixture` from exactly the `decision_key`s the case supplies
(`register_dmn_fixture`), so simply OMITTING a `decision_key` a graph's turn will evaluate is
already enough to make `FakeDmnTransport.evaluate` raise `DmnEvaluationError` for it — this is
why CC-08's DMN-down goldens carry no new top-level block at all, unlike its PhiZoneRoutingError
goldens (`inference`, new) and CC-01's start-failure goldens (`cibseven`, pre-existing).

## Golden case JSON schema

One JSON file per eval id, named `EVL-<AGENT>-<NN>.json`, under `golden/<agent>/`:

```json
{
  "id": "EVL-HELENA-01",
  "agent": "helena",
  "class": "CE",
  "tier": ["A", "B"],
  "description": "human-readable one-liner",
  "input": {
    "state": { "tenant_id": "amh", "conversation_id": "wa:amh:evl-helena-01",
               "canal": "whatsapp", "beneficiario_pseudo_id": "PSEUDO-TESTE-001",
               "message_body": "..." }
  },
  "recorded_llm": ["<scripted response 1>", "<scripted response 2>", "..."],
  "dmn_fixture": { "triage_redflag_adult": { "red_flag": true, "conduta": "ESCALATE_URGENTE", "prioridade": "P1" } },
  "expect": { "next_kind": "escalate", "fields": { "escalation_motivo": "red_flag_clinico" } },
  "leak_canaries": [],
  "live": { "assert": "fields", "match": ["intent", "population", "psychosocial_risk"], "threshold": 0.9 }
}
```

Field notes:

- `id` / `agent` / `class` / `tier`: bookkeeping. `class` is one of `CE` (correct_extraction),
  `FC` (fail-closed-on-garbage), `EU` (escalation-on-uncertainty), `PL` (no-PHI-leak), `GC`
  (guard-compliance), `DD` (dmn-input-discipline), `CL` (clarity_legibility — WP-EVALS gap
  10.3, see below), `JN` (journey_end_to_end — WP-EVALS gap 11.5, see below). `tier` is `["A"]`
  or `["A", "B"]` — Tier B alone (no A) is not a supported shape; every eval has a deterministic
  baseline.
- `input.state`: the plain dict passed to the compiled graph's `.ainvoke(...)` (or to a single
  node method directly, for a Tier-B live variant) — this is NOT validated against the agent's
  `TypedDict` at load time; an agent-specific field typo will surface as a graph-behavior
  difference, which is itself useful signal.
- `recorded_llm`: an ORDERED list of scripted provider outputs, popped front-to-back by
  `ReplayInferenceProvider` — **one entry per actual `inference.generate(...)` call the turn
  makes**, not per "meaningful" call. For Helena's escalate path that's 3: the `classify` JSON,
  the `_resumo_contexto` text, and the `_respond_llm` text (see `HelenaGraph.escalate`). Getting
  this count wrong raises a loud `ReplayExhaustedError` naming the exhausting prompt — it never
  silently continues on `""`.
- `dmn_fixture`: `{decision_key: row_or_rows}` registered onto a `FakeDmnTransport` via
  `register_dmn_fixture` — a bare dict is treated as a single row (wrapped in `[row]`); a list is
  used as-is (multiple candidate rows, first hit policy).
  A value carrying `__rules__` is a **conditional fixture** (first-hit rules, `when`/`then`),
  registered onto the `RuleAwareFakeDmnTransport` that `run_case` builds. Use it whenever the
  question the eval asks is *"can this table say that, given what the graph actually sends it?"*
  — a static row answers only *"does the graph relay whatever the fixture says?"*, which is
  vacuous for a routing assertion. `EVL-RAFAEL-02`/`-03` are the worked example: their
  `auth_auto_approval` fixture mirrors rule r1 of the real v0.2.0 table (AUTO_APROVAR only when
  all five booleans arrive `true`, catch-all `ANALISE_HUMANA`), which is what makes them prove
  Rafael's `auto_approve` route is unreachable from every typed seam instead of faking it
  reachable. A `when` matches on `==` per key; a missing input key does NOT match; no rule
  matching and no catch-all is a loud `AssertionError` (fixture bug), never a silent fail-safe.
  A conditional fixture handed to a plain `FakeDmnTransport` raises `TypeError` — a Tier-B live
  variant that builds its own transport must build a `RuleAwareFakeDmnTransport`.
- `cibseven` (OPTIONAL, CC-01/CC-08): `{"start_fails": true}` swaps the case's CibSeven double
  for `_harness.FailingStartCibSevenTransport`, whose `start_process_instance` raises
  `CibSevenError` — the engine-unavailable branch. Use it to prove an agent's start-failure
  routing (`desfecho == "erro_inicio_processo"`, no fabricated success, no message that promises
  a human who was never summoned). Absent (every pre-CC-01 golden) = the unchanged
  always-succeeds `FakeCibSevenTransport`.
- `inference` (OPTIONAL, CC-08): `{"phi_capable": false}` makes `ReplayInferenceProvider`
  raise `PhiZoneRoutingError` on every `generate(phi=True, ...)` call this turn (`_harness.
  _phi_capable_for`) — the PHI-zone routing fail-closed branch (invariant I-6). Use it to prove
  a graph never fails OPEN when its inference seam is PHI-zone-blocked (e.g. Helena's classify
  call failing this way must still escalate `falha_tecnica`, exactly like a classify-LLM
  exception or a DMN-down turn — never a silent `inform`). Absent (every pre-CC-08 golden) = the
  unchanged `phi_capable=True` default. Every `generate()` call this turn will raise once set —
  a case that opts in typically needs an EMPTY `recorded_llm` (`[]`), since no call ever
  successfully returns a scripted response to consume.
- `expect`: `next_kind` (RT) and/or `fields` (SF), checked by `assert_expect`.
- `leak_canaries`: synthetic strings (e.g. a synthetic CPF `123.456.789-09`, NEVER a real one)
  that must be absent from the emitted output (`assert_no_leak`, ABS). Empty list = no PL
  assertion for this case.
- `live`: only meaningful for a `tier: ["A", "B"]` case. `match` names the fields
  `score_live`/`assert_live_score` diff between the live run and `recorded_llm[0]`'s parsed JSON;
  `threshold` is the pass bar (design default `0.9`).

## How to add an eval

1. Pick the agent and copy an existing `golden/<agent>/EVL-<AGENT>-<NN>.json` as a template.
2. Fill in `input.state` with **synthetic-only data** — tenant `amh`, a `pseudo-*`/`PSEUDO-*`
   beneficiary id, a `wa:amh:*` conversation id. Never a real name/CPF/phone number. If the case
   needs to prove a leak-canary assertion, use the repo's existing synthetic CPF
   `123.456.789-09` (or another synthetic value matching this shape) as the value that must be
   absent, never present, in real data.
3. Fill in `recorded_llm` with the exact JSON/text the classify/narrative/response LLM call(s)
   would return for this scenario — count the calls the path you're targeting actually makes
   (read the graph's node methods; `_FakeInference`-based unit tests for the same agent are the
   fastest way to find the right call count/shape).
4. Fill in `dmn_fixture` if the path evaluates a DMN table.
5. Fill in `expect` (and `leak_canaries` if this is a PL-class eval).
6. Add a `test_<agent>_eval_tier_a` parametrized test in the owning family's test module if one
   doesn't exist yet for that agent (mirror `test_helena_eval_tier_a` in
   `test_classifier_evals.py` exactly — same `run_case`/`assert_expect`/`assert_no_leak` shape,
   swap the `build_fn` import and `extra_config`).
7. Run `uv run pytest tests/evals -q -m eval -k <your-new-id>` locally — the golden must PASS.
8. **Prove it's non-vacuous** (T3.2 design §7.1, mutation-check gate): perturb your own golden's
   `expect`/`leak_canaries` (via `mutate_expected_route`/`mutate_plant_canary`, or write a new
   `mutate_*` helper in `_harness.py` if neither fits your case's shape) and confirm
   `run_mutation_check` reports the corrupted golden FAILS. An eval that still passes under a
   deliberately-wrong expectation is rejected — it would never catch a real prompt/graph/model
   regression. See `test_evl_helena_01_mutation_check_route_is_non_vacuous` for the pattern.

## Recording a Tier-B live baseline

Tier B never invents its own dataset — it re-runs the same node against the real model and
diffs against the Tier-A `recorded_llm[0]` baseline you already wrote by hand in step 3 above.
There is nothing extra to "record" to add Tier B to a case: set `"tier": ["A", "B"]` and add a
`"live"` block naming which fields to compare and the pass threshold, then add a
`test_<agent>_eval_tier_b_live` parametrized case mirroring `test_helena_eval_tier_b_live`.

To exercise it locally against a real key (never commit one):

```
MAEZO_ANTHROPIC_API_KEY=sk-ant-... uv run pytest tests/evals -m "eval and llm_live" -v
```

Without a key, every Tier-B case SKIPS loudly (`live_key_skip`, mirrors
`tests/unit/runtime/test_inference_live.py`) — this is intentional, not a bug: Tier B requires a
reviewed `ci.yml` wiring change to ever run in CI (the pipeline currently passes
`LLM_GENERAL_API_KEY` to the eval gate step, but `maezo.runtime.inference` reads
`MAEZO_ANTHROPIC_API_KEY`/`ANTHROPIC_API_KEY` and defaults to the `noop` provider — see the T3.2
design doc §1.2/§4-R2). That wiring edit is explicitly its own, separately-reviewed change, not
bundled into any golden-dataset PR.

## How CI consumes this (no workflow edit needed)

`.github/workflows/ci.yml`'s `evals` (PR, agent-touching paths only) and `evals-nightly`
(`schedule` only) jobs both run the same three-bucket guard before anything else:

```
uv run pytest tests/evals --collect-only -q -m eval
  0        -> run=true   -> `make evals` (== `uv run pytest tests/evals -q -m eval`)
  4|5      -> run=false  -> LOUD visible skip (4 = tests/evals/ missing, 5 = nothing collected)
  else(2)  -> job FAILS  ("COLLECTION BROKEN" — an import error must never masquerade as
                           "no datasets yet")
```

Landing this wave's `EVL-HELENA-01` (a real, collectable, `@pytest.mark.eval` test under
`tests/evals/`) is what flips that guard from "4 = dir missing" to "0 = run the real gate" — no
`ci.yml` edit is required. `make evals`'s local-dev `|| [ $? -eq 5 ]` fallback (tolerating "not
collected yet") is now moot for this file (it always collects >= 1 case) but stays harmless for
any brand-new agent's `golden/<agent>/` directory that doesn't exist yet.

Both CI jobs pass `LLM_GENERAL_API_KEY` as an env var today — as noted above, that does **not**
currently reach `maezo.runtime.inference` (it reads `MAEZO_ANTHROPIC_API_KEY`/
`ANTHROPIC_API_KEY`), so Tier B skips loudly in CI until the separate, reviewed wiring change
lands. Tier A is completely unaffected by that gap: it injects its own provider and needs no key,
ever.

## Clarity/legibility checks (gap 10.3, WP-EVALS)

`tests/evals/golden/helena/EVL-HELENA-CLAREZA-{01,02,03}.json` + `test_helena_clarity_evals.py`
close `docs/audits/maezo-deep-audit/reports/domain-10-accessibility.md:32` ("vocabulario de
triagem nao e auditado para clareza linguistica ... sem eval de legibilidade nos golden
datasets"). This is a NEW pass-criterion type, `CL`, alongside RT/SF/ABS/TH — added at the root
(`_harness.py`'s `score_clarity`/`assert_clarity`, `mutate_extend_last_sentence`/
`mutate_replace_last_response`) rather than in a family test module, since it is generic,
agent-agnostic text scoring on whatever field a golden names.

It judges ONLY the CLARITY of the wording a graph drafts for a beneficiary (Helena's
`response_text`) — four independent, deterministic, reproducible checks, no LLM-as-judge, no
learned/constant score:

0. **Minimum length** — `text` must contain at least `min_words` words (default 3, overridable
   per-golden via an optional `clarity.min_words` key). An empty or near-empty reply is never
   clear, no matter what the other checks say — this closes a vacuous pass a golden with
   `required_disclaimers: []` would otherwise allow on an empty string. Surfaced as
   `ClarityReport.word_count`/`.min_words`.
1. **Sentence length** — any sentence, split by a proper sentence splitter that protects a
   documented PT-BR abbreviation list ("Dr.", "Dra.", "Sr.", "Sra.", "Srta.", "etc.", "p. ex.")
   and digit-dot-digit decimals ("37.5") from being misread as sentence boundaries, whose word
   count exceeds `clarity.max_words_per_sentence` is flagged. (A naive `.`-split would let a
   too-long utterance hide under the cap by fragmenting at a title abbreviation — e.g. "...a Dra.
   Fernanda..." — this splitter does not.)
2. **Forbidden jargon** — any `clarity.forbidden_jargon` term found in the text
   (case-insensitive substring) is flagged: internal/engine vocabulary (DMN table names, raw
   `sintoma_codigo` values, `motivo_categoria` tokens, severity codes like `P1`) must never leak
   verbatim into a beneficiary-facing message. (Deliberately still substring-based — a leaked
   engine token is a real leak wherever it appears, even mid-word.)
3. **Mandatory disclaimers** — `clarity.required_disclaimers` is a list of alternative-phrase
   GROUPS (e.g. a human-handoff group, an emergency-escalation group); each group needs >=1
   alternative present as a standalone word/phrase (case-insensitive, hyphen-aware word-boundary
   match — so "humano" matches "um atendente humano" but NOT "esforco sobre-humano") or it is
   flagged as missing. Accent-folding is deliberately NOT applied: goldens that need both
   spellings list each accented/unaccented form as its own alternative (e.g.
   `["emergencia", "emergência"]`).

A golden case opts in with an OPTIONAL top-level `"clarity"` block (`load_golden`'s required-key
check does not require it, so every pre-existing golden that omits it is unaffected):

```json
"clarity": {
  "field": "response_text",
  "max_words_per_sentence": 20,
  "forbidden_jargon": ["red_flag", "DMN", "P1", "sintoma_codigo"],
  "required_disclaimers": [["profissional", "humano", "atendente"], ["emergencia"]]
}
```

This checks ONLY the wording Helena emits to the beneficiary — never the clinical CONTENT of
the SME-gated `triage_redflag_*` DMN tables themselves (their `red_flag`/`conduta`/`prioridade`/
`motivo` vocabulary is DRAFT/verify content owned by a clinical reviewer, out of scope by
design). Non-vacuousness (§7.1) is proven per check kind in `test_helena_clarity_evals.py`
(`mutate_plant_canary` for the jargon check, `mutate_extend_last_sentence` for the
sentence-length check, `mutate_replace_last_response` for the disclaimer check) plus pure-logic
unit tests in `tests/unit/evals/test_harness_clarity.py` that exercise `score_clarity`/
`assert_clarity` directly, with no golden file or agent graph involved.

## Journey evals (gap 11.5, WP-EVALS)

`tests/evals/golden/helena/EVL-HELENA-JOURNEY-01.json` + `test_helena_journey_evals.py` closed
the first half of `docs/audits/maezo-deep-audit/reports/domain-11-product-fit.md:32` ("os 44
golden evals ... sao finos ... nenhum eval de jornada ponta a ponta") for Helena only (2026-09,
commit `187c6b1`). The residual half of gap 11.5 (WAVE0-RECON round-5 PARTIAL note) extended the
SAME pattern to the other nine agents: every agent under `golden/<agent>/` now has exactly one
`EVL-<AGENT>-JOURNEY-01.json` (class `JN`) + its own dedicated `test_<agent>_journey_evals.py`
module. Every pre-existing (non-journey) golden (via `test_classifier_evals.py`/
`test_dossier_adverse_evals.py`/`test_dossier_admin_evals.py`) asserts only a turn's FINAL
route/fields (`assert_expect(result.state, case["expect"])`). Class `JN` is a new ASSERTION
SHAPE, not a new harness path: every journey module drives the exact same `run_case`/`build()`
mechanism as every other eval, but asserts an OBSERVABLE checkpoint at EACH stage of one
beneficiary/case turn, named explicitly per agent (never a generic "final state" check) —
derived from that agent's compiled graph nodes/edges (`src/maezo/agents/<agent>/graph.py`),
`spec/agents/<agent>/agent.yaml`, and the process contract it starts/feeds
(`docs/processes/contracts/*.md`):

| Agent | Stages (module docstring names them precisely) | Hand-off checkpoint |
|---|---|---|
| helena | intake -> triage -> hand-off | `SP-OP-ESCALATION-001` start + WhatsApp delivery |
| andre | intake -> triage -> hand-off | `SP-OP-PAGTO-001` start (`pagto_dossier`/`auto_route`) |
| beatriz | intake -> gather -> hand-off | delegation envelope (the dossier itself — Beatriz never starts a process; L0 `decisao_fraude`/`bundle_root`/`destino_referral` always `None`) |
| carolina | intake -> triage -> hand-off | `SP-OP-CRED-001` start (`credenciamento`/`auto_route`) |
| fernando | intake -> triage -> hand-off | `SP-OP-INADIMPLENCIA-001` start (J3 `escalate`; asserts NO WhatsApp was sent on this branch — `notify` is the only sending node and is unreachable once `escalate` is taken) |
| gustavo | intake -> triage -> hand-off | `SP-OP-NIP-001` start (J2 `instruct_nip`; `process_key`/`business_key` are already observable at INTAKE — `receive` derives them before any DMN runs) |
| lucas | intake -> triage -> hand-off | `SP-OP-ESCALATION-001` start + WhatsApp ACK (LUC-05: the ACK fires only AFTER the engine start succeeds) |
| marina | intake -> triage -> hand-off | `SP-OP-CONTAS-001` start (`contas`/`auto_route`) |
| rafael | intake -> triage -> hand-off | `SP-OP-AUTH-001` start (`human_auditor` — the only branch RAF-01/RAF-06 proved is honestly reachable from this agent's typed seam; `auto_approve`'s real criteria are not) |
| valentina | consent -> triage -> hand-off | `SP-OP-PROGRAMA-001` start (`enroll`/`auto_route`, behind the LGPD consent chokepoint) |

Each journey golden is possible from ONE `run_case`/`ainvoke` call (not one call per stage)
because every agent's compiled `StateGraph` merges each node's return dict into one cumulative
state with a plain dict-update reducer — a downstream node's return never clears the keys an
upstream node set, so the FINAL `RunResult.state` already carries a checkpoint from every stage
the turn passed through; each journey test module's job is to assert against all of them, not
just the last one. A golden case names what to check at each stage via an OPTIONAL top-level
`"journey"` block — the exact keys vary slightly by agent shape (a process-starting agent uses
`handoff.business_key`/`handoff.process_key`/`handoff.engine_variables`, optionally
`handoff.whatsapp_delivered`/`whatsapp_sent`; Beatriz's delegation-envelope shape uses
`handoff.dossier_fields` instead of engine variables; Gustavo's `intake.fields` and Valentina's
`consent.fields` are agent-specific additions) — see each `EVL-<AGENT>-JOURNEY-01.json` for its
own shape and its `test_<agent>_journey_evals.py` module docstring for how each block is
interpreted. Every journey eval's non-vacuousness (T3.2 design §7.1) is proven by at least two
tests: a `run_mutation_check`-style flip of a turn-level `expect.fields` value, AND a dedicated
mutation of the journey's OWN hand-off-stage expectation (mirroring
`test_evl_helena_journey_01_mutation_check_handoff_stage_is_non_vacuous`) — proving the
PER-STAGE checks each module adds are actually exercised, not vacuously green.

## Markers

No new pytest markers are registered by this wave — `eval` and `llm_live` already exist in
`pyproject.toml`'s `[tool.pytest.ini_options] markers`. Tier B evals carry BOTH `eval` (so `-m
eval` collects them — they still show up, just skipped without a key) and `llm_live` (so `-m
llm_live` alone also finds them, consistent with the existing live-Anthropic-call convention).
