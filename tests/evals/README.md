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
                                    # assert_live_score, mutation-check helpers
                                    # (OWNED BY B0 — do not edit from a family wave)
  README.md                        # this file (OWNED BY B0)
  test_classifier_evals.py         # helena (B0 reference) + fernando/lucas (B1 extends this file)
  test_dossier_adverse_evals.py    # rafael/valentina/marina/andre (B2)
  test_dossier_admin_evals.py      # carolina/beatriz/gustavo (B3)
  golden/
    helena/EVL-HELENA-01.json ... EVL-HELENA-08.json
    fernando/EVL-FERNANDO-01.json ...
    lucas/ ... rafael/ ... valentina/ ... marina/ ... carolina/ ... andre/ ... beatriz/ ...
    gustavo/ ...
```

Each family builder wave owns its own test module + its own `golden/<agent>/` subdirectories —
disjoint files, so B1/B2/B3 can run in parallel with zero collisions. Nobody but B0 edits
`conftest.py` / `_harness.py` / this README.

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
  (guard-compliance), `DD` (dmn-input-discipline). `tier` is `["A"]` or `["A", "B"]` — Tier B
  alone (no A) is not a supported shape; every eval has a deterministic baseline.
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

## Markers

No new pytest markers are registered by this wave — `eval` and `llm_live` already exist in
`pyproject.toml`'s `[tool.pytest.ini_options] markers`. Tier B evals carry BOTH `eval` (so `-m
eval` collects them — they still show up, just skipped without a key) and `llm_live` (so `-m
llm_live` alone also finds them, consistent with the existing live-Anthropic-call convention).
