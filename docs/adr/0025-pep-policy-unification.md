# ADR-0025: PEP ↔ Policy Unification (autonomy-matrix YAML loading + single action vocabulary) [T1.8]

**Status:** Accepted — ratified by orchestrator (Fable 5) 2026-07-27, recorded as DL-0036 · **Data:** 2026-07-16 · **Area:** Seguranca / Governanca de Autonomia
Owner: policy-guardian. Defects: B3 (PEP does not load YAML) / B4 (dual vocabulary; hard-set incomplete).
Scope: T1.8 acceptance criteria only. Implementation lands after T0.3.
**Amended by ADR-0034:** the D6/Q4 "flagged follow-up" for async audit + L2 review sampling is
converted from *deferred* to *descoped* — L2 review sampling is intentionally not in v2 (gap #24).
Root cause: v2's PEP is never on the request path (`PEP.evaluate` has no runtime callers; `build_pep()`
is a startup readiness probe only), so autonomy is enforced structurally via BPMN (ADR-0018), not by a
per-tool-call PEP; an injected sampler would sample nothing. Revisit only if a runtime per-tool-call PEP
chokepoint is later introduced. ADR-0034 amends, does not supersede.
**Ratification amendment note (2026-07-27):** implementation is R1-verified on main (`pep.py:71`
`HARD_ACTIONS` frozen 5, `:287` `load_matrix`, `:317` `_load_matrix_cached`; evidence-ledger T1.8
`:43` "verified — B4 closed (merged #36)"). Ratifying D1-D6 does **not** resolve Q2 (§6): the
`analise_recurso` orphan action remains absent from `spec/policies/autonomy/L0-core.yaml`
(`grep -rn "analise_recurso\|recurso_analysis"` over `pep.py` + `spec/policies/autonomy/` still
returns 0 hits) — this residual stays open, blocked(external: compliance), tracked separately from
this ratification.

Zero-trust note: every claim below carries a path:line citation into actual code, verified this session.
Do not trust docstrings/comments — where a comment asserts behavior, it is flagged as comment-only.

---

## 1. Problem statement (verified, not asserted)

### 1.1 The v2 PEP does not read policy at all
`src/maezo/gateway/pep.py` hard-codes a Python dict `_AUTONOMY_MATRIX` (pep.py:51-74) and a frozenset
`HARD_ACTIONS` (pep.py:22-28). There is **no** YAML load anywhere in the module: `grep load_matrix
src/maezo` returns zero hits in gateway (only a comment reference at `spec/policies/autonomy/L0-core.yaml:28`).
`PEP.evaluate` (pep.py:76-124) resolves against the in-memory dict; `spec/policies/autonomy/*.yaml` is
dead config as far as the PEP is concerned.

### 1.2 Two disjoint vocabularies
- `pep.py` uses **Portuguese** action names: `negativa_cobertura`, `decisao_clinica`, `acusacao_fraude`,
  `cancelamento_contrato`, `pagamento_alcada`, `reembolso_calculo`, `triagem_whatsapp`, etc.
  (pep.py:22-28, 51-74).
- `spec/policies/autonomy/L0-core.yaml` uses **English** action names: `clinical_decision`,
  `authorization_denial`, `authorization_approval`, `reembolso_auto_approval`, `nip_manter_negativa`,
  `contract_termination`, etc. (L0-core.yaml:6-48).
- Every `agent.yaml` `autonomy_actions:` list uses the **English** vocabulary
  (e.g. `src/maezo/agents/gustavo/agent.yaml:27-...` lists `ans_official_submission`, `nip_response`,
  `read_phi_data`, `query_decision_engine`; `rafael/agent.yaml`, `helena/agent.yaml`, etc. all English).

Occurrence count (this session, `grep -rl` over `spec/` + `src/`): English canonical names appear in
3–27 files each (`authorization_denial` 25, `fraud_accusation` 27, `clinical_decision` 20,
`contract_termination` 14, `high_value_payment` 12, `provider_decredentialing` 9). The Portuguese
pep.py names appear in **1–2 files** each (essentially pep.py + its test + a docstring in base.py).
→ Decision is not close: **English is the canonical vocabulary.**

### 1.3 The hard-deny set is incomplete
`_hard_frozen.yaml:17-31` freezes **5** hard items: `clinical_decision`, `authorization_denial`,
`nip_manter_negativa`, `fraud_accusation`, `contract_termination`. `L0-core.yaml` marks the same 5
with `hard: true` (L0-core.yaml:6,7,11,12,13). But `pep.py:22-28` HARD_ACTIONS carries only **3**
(negativa_cobertura, decisao_clinica, acusacao_fraude) and is missing the equivalents of
`nip_manter_negativa` and `contract_termination`. Worse, `cancelamento_contrato` — the PT name closest
to `contract_termination` — is coded at **L0 non-hard** (pep.py:56, level 0 → REQUIRE_HUMAN), which is a
**downgrade** of a frozen hard item. Test `tests/unit/gateway/test_pep.py:59-64` only asserts the 3 PT
items are present, so CI currently ratifies the incomplete set.

### 1.4 The mature design already exists in v1 (donor)
The v1 donor `/Users/familia/code/Maezo-Healthcare-Plan/src/maezo/gateway/pep.py` **already** implements
the full target: `load_matrix(core_path, tenant, overlay_path)` (v1 pep.py:277-294), `AutonomyMatrix`/
`ActionPolicy` dataclasses (v1 pep.py:101-119), `_parse_core` with defense-in-depth hard checks
(v1 pep.py:197-218), `_apply_overlay` that refuses to touch hard items (v1 pep.py:221-274), and
`HARD_ACTIONS` = the correct **5 English** items (v1 pep.py:65-76). v1's `L0-core.yaml`,
`_hard_frozen.yaml`, `tenants-amh.yaml` are byte-identical to v2's (diff clean this session).
**T1.8 is a port**, not a green-field design. The only reason v2 diverged is that v2 gateway/pep.py was
re-stubbed in Portuguese; the spec YAML was copied over but never wired.

---

## 2. Decisions

### D1 — Canonical action vocabulary: **English**, as defined by `spec/policies/autonomy/L0-core.yaml`
Rationale (§1.2 counts): the YAML matrix (spec = single source of truth, constraint 5), all `agent.yaml`
allowlists, `_hard_frozen.yaml`, and the v1 donor PEP already speak English. The PT dict in pep.py is the
sole outlier. Adopt English; retire the PT dict. No PT↔EN alias layer at runtime (an alias map is a
second vocabulary and a fail-open surface — a typo'd alias would silently resolve). PT names become
**unknown → DENY** (fail-closed) after migration; this is desirable (any caller still emitting PT is a bug
that must surface as a denial, not be papered over).

### D2 — PEP loads `spec/policies/autonomy/*.yaml` via ported `load_matrix`
Port v1 `load_matrix` + `_load_yaml` + `_parse_core` + `_apply_overlay` + `AutonomyMatrix`/`ActionPolicy`
(v1 pep.py:190-294) into v2 `src/maezo/gateway/pep.py`, adapting only the default path.
- **When**: matrix is loaded once at PEP construction (eager), not per-`evaluate`. `PEP.__init__` takes an
  already-resolved `AutonomyMatrix` (constructor-injected, as v1's `PolicyEnforcementPoint.__init__`
  does, v1 pep.py:307-322). A module-level/DI factory calls `load_matrix` and hands the matrix in.
- **Where the files live** (RATIFIED by orchestrator 2026-07-16: option (b) — `spec/` stays canonical,
  consistent with constraint 5; the earlier option-(a) recommendation in this draft is superseded).
  Policies remain at `spec/policies/autonomy/`. Three consequences the implementation MUST handle,
  because the wheel currently ships **only** `src/maezo` (`pyproject.toml:61-62`
  `[tool.hatch.build.targets.wheel] packages = ["src/maezo"]` — `spec/` is NOT packaged today):
  1. **Packaging**: add a hatch `force-include` (or `artifacts`) entry so `spec/policies/autonomy/*.yaml`
     ships inside the wheel; otherwise D5's refuse-to-start semantics would brick every packaged
     deployment (matrix file absent at runtime → PEP factory refuses → nothing boots).
  2. **Path resolution**: T0.3 introduces `MAEZO_SPEC_DIR` + `resolve_spec_dir()` in
     `src/maezo/agents/__init__.py` (verified NOT yet on main this session — grep zero hits; T0.3 merges
     before implementation). The PEP factory and the T1.9 `CeilingResolver` MUST resolve the policy dir
     via that same mechanism (`resolve_spec_dir() / "policies" / "autonomy" / "L0-core.yaml"`) — do NOT
     invent a second resolution scheme. `AUTONOMY_CORE_PATH` may survive only as a narrow test-pinning
     override, if kept at all.
  3. **agent.yaml fix-up**: all **11** `agent.yaml` files declare `autonomy_policy: ../../policies/autonomy`,
     which resolves to the nonexistent `src/maezo/policies/autonomy` (verified: andre:25, _template:26,
     carolina:22, gustavo:26, beatriz:30, fernando:31, lucas:24, rafael:26, marina:26, helena:29,
     valentina:30). All 11 must be repointed to the spec-resolved location (or to a symbolic value the
     loader resolves through `resolve_spec_dir()`) in the same change — a dangling path here would make
     per-agent policy resolution silently diverge from the PEP's.
- **Caching**: `load_matrix` reads static config; cache by resolved (core_path, overlay_path, tenant) —
  reuse v1 ceilings.py `_load_matrix_cached` `@lru_cache(maxsize=64)` (v1 ceilings.py:45-63) pattern for the
  matrix. The PEP itself holds one resolved matrix; agents/tenants that need a different overlay get their
  own matrix instance from the cached loader.
- **Schema validation**: `_parse_core` (v1 pep.py:197-218) enforces: every `actions` entry has a `level`
  in {L0,L1,L2,L3} (raises `PolicyError` otherwise); every code-frozen HARD_ACTIONS member must be present
  AND L0 (defense-in-depth, v1 pep.py:211-217). Overlay validation in `_apply_overlay` (v1 pep.py:221-274).

### D3 — Hard-deny set composition = the frozen 5, English, code-frozen
`HARD_ACTIONS` in v2 pep.py becomes exactly (port v1 pep.py:65-76):
`{clinical_decision, authorization_denial, nip_manter_negativa, fraud_accusation, contract_termination}`.
- `nip_manter_negativa` is hard because MANTER_NEGATIVA embeds a coverage denial (authorization_denial-class);
  it is frozen L0 in `_hard_frozen.yaml:26-27` and `L0-core.yaml:11`. It must be split out of the generic
  `nip_response` (L1) — see migration table row.
- `contract_termination` is hard per `_hard_frozen.yaml:30-31` and `L0-core.yaml:13`. This **hardens**
  today's pep.py `cancelamento_contrato` (L0 non-hard, pep.py:56).
- Hardness is **code-frozen**: `HARD_ACTIONS` in Python is the source of truth for "untouchable"
  (v1 pep.py:62-64 doctrine). Even if a YAML `hard:true` is dropped, the code re-imposes it
  (`_parse_core` `hard = ... or name in HARD_ACTIONS`, v1 pep.py:208) and refuses a core missing a hard
  item (v1 pep.py:211-217). Overlays can never touch a hard item (v1 pep.py:243-247).

### D4 — Tenant overlay mechanism (`tenants-amh.yaml`)
Port v1 `_apply_overlay` (v1 pep.py:221-274). Semantics enforced **in code** (never trust YAML):
- An overlay may only **raise restriction** on a **non-hard** action (move to a lower/more-restrictive
  level rank; v1 pep.py:249-257 rejects loosening) and **refine `params`** (v1 pep.py:262-266).
- An overlay may **not** touch a hard item at all — any `level`/`hard`/`params` key on a hard action →
  `PolicyError` (v1 pep.py:243-247). **L0 hard is not overridable, full stop.**
- Overlay `tenant:` field must match the requested tenant or → `PolicyError` (v1 pep.py:290-292).
- `tenants-amh.yaml` today only refines `authorization_approval.params.max_value_brl` and
  `reembolso_auto_approval.params.max_value_brl` (tenants-amh.yaml:5-9) — both currently 0 (D-07 open).
  This is the legitimate use: **params refinement of non-hard L2 ceilings**, never a level change.
- Overlay auto-discovery: `<core-dir>/tenants-<tenant>.yaml` (v1 ceilings.py:86-91 convention). Same file
  the CeilingResolver (T1.9) reads — one merge path, shared by PEP and ceilings.

### D5 — Fail-closed semantics (non-negotiable, constraint 2)
- **Unknown action → DENY.** Port v1 `_evaluate_inner`: `policy is None → Decision.DENY` with reason
  "acao ausente da matriz (fail-closed)" and an audit record (v1 pep.py:346-355). v2 already denies unknown
  (pep.py:98-105) but only against the hard-coded dict; after D2 it denies against the YAML matrix.
- **Unparseable/missing YAML → refuse to start** — with precise exception semantics. v1 `_load_yaml`
  (v1 pep.py:190-194) has **three** distinct failure classes, not one:
  (i) **missing file** → `FileNotFoundError` (raised by `path.read_text`, v1 pep.py:191 — `load_matrix`
  does not catch it); (ii) **syntactically invalid YAML** → `yaml.YAMLError` (Scanner/ParserError from
  `yaml.safe_load`, v1 pep.py:191 — also not caught; note this is NOT a `PolicyError`); (iii) **YAML that
  parses but is structurally invalid** (non-dict root, v1 pep.py:192-193; missing `level`, v1 pep.py:203-205;
  missing/non-L0 hard item, v1 pep.py:213-217; bad overlay, v1 pep.py:221-274) → `PolicyError`.
  The PEP factory MUST catch and normalize **all three** classes into refuse-to-start (re-raise as a single
  startup failure, e.g. wrap (i)/(ii) in `PolicyError` at the factory boundary) — never swallow any of them.
  A PEP with no valid matrix must fail construction so the process does not boot in a state where it cannot
  decide. (Contrast: the T1.9 CeilingResolver deliberately swallows-to-0 for *ceiling* resolution because a
  missing ceiling → fail-closed to human review; but the *PEP itself* must hard-fail, because a PEP that
  cannot load its matrix cannot gate anything.)
- **Level decision fail-closed order** (port v1 `_decide`, v1 pep.py:415-450): L0-hard → DENY;
  L0-non-hard/L1 → REQUIRE_HUMAN; L2/L3 → ALLOW. No warn-and-pass anywhere.

### D6 — Sync vs async adaptation
v1 `PolicyEnforcementPoint.evaluate` is `async` and audits refusals + does L2 sampling (v1 pep.py:324-372).
v2 `PEP.evaluate` is sync and does neither (pep.py:76-124). **Recommendation**: port the matrix-loading and
vocabulary/hard-set correctness now (the T1.8 surface); keep evaluate sync for this task to minimize blast
radius, but preserve the level→decision mapping exactly. Porting async audit + L2 sampling is a larger
change (depends on `audit.py`, `l2_sampling.py`) — recommend a follow-up task, flagged, not silently
dropped. T1.8 acceptance does not require audit/sampling; it requires YAML loading + vocabulary + hard set.

---

## 3. Complete migration table (every action in today's pep.py AND in the YAML)

Legend: pep.py level integer → 0=L0,1=L1,2=L2,3=L3. "Δ" flags a semantic change the migration introduces.

| pep.py (PT, current)            | pep.py loc      | pep level | → canonical (EN) YAML action | YAML loc         | YAML level        | Notes / Δ |
|---------------------------------|-----------------|-----------|------------------------------|------------------|-------------------|-----------|
| negativa_cobertura (HARD)       | pep.py:24,53    | L0 hard   | authorization_denial         | L0-core.yaml:7   | L0 hard           | direct rename |
| decisao_clinica (HARD)          | pep.py:25,54    | L0 hard   | clinical_decision            | L0-core.yaml:6   | L0 hard           | direct rename |
| acusacao_fraude (HARD)          | pep.py:26,55    | L0 hard   | fraud_accusation             | L0-core.yaml:12  | L0 hard           | direct rename |
| cancelamento_contrato           | pep.py:56       | L0 **non-hard** | contract_termination   | L0-core.yaml:13  | **L0 hard**       | **Δ HARDEN**: non-hard→hard; DENY not REQUIRE_HUMAN. Frozen `_hard_frozen.yaml:30`. |
| descredenciamento               | pep.py:57       | L0 non-hard | provider_decredentialing   | L0-core.yaml:19  | L1                | Δ level L0→L1 (both REQUIRE_HUMAN; decision-equivalent). Collapses with descredenciamento_formal. |
| pagamento_alcada                | pep.py:59       | L1        | high_value_payment           | L0-core.yaml:21  | L1 (threshold_brl:100000) | direct; ceiling param carried |
| resposta_nip                    | pep.py:60       | L1        | nip_response **+** nip_manter_negativa | L0-core.yaml:18 **+** :11 | L1 **+** L0 hard | **Δ SPLIT**: MANTER_NEGATIVA outcome → `nip_manter_negativa` (L0 hard); CONCEDER/RESPONDER_NAO_ASSISTENCIAL → `nip_response` (L1). pep's single L1 action conflates a hard denial. |
| envio_ans                       | pep.py:61       | L1        | ans_official_submission      | L0-core.yaml:14  | L1                | direct rename |
| descredenciamento_formal        | pep.py:62       | L1        | provider_decredentialing     | L0-core.yaml:19  | L1                | merges with `descredenciamento` above |
| aprovacao_auth_dmn_favoravel    | pep.py:64       | L2        | authorization_approval       | L0-core.yaml:22  | L2 (requires dmn_favorable, max_value_brl) | direct; ceiling param carried (T1.9) |
| glosa_padrao                    | pep.py:65       | L2        | standard_glosa_processing    | L0-core.yaml:29  | L2                | direct rename |
| reembolso_calculo               | pep.py:66       | L2        | reembolso_auto_approval      | L0-core.yaml:24  | L2 (requires dmn_favorable, max_value_brl) | Δ NAME: "calculo" = pure arithmetic (not a gated effect); the gated autonomy action is auto-approval. Map to `reembolso_auto_approval`. (Arithmetic itself is not a matrix action.) |
| analise_recurso                 | pep.py:67       | L2        | **(no YAML action)**         | —                | —                 | **Δ ORPHAN**: no `analise_recurso`/appeal-analysis action in L0-core.yaml. Either add it (compliance sign-off) or confirm recurso analysis is L2 dossier-prep under an existing action. BLOCKED — needs ratification. |
| triagem_whatsapp                | pep.py:69       | L3        | triage_and_routing           | L0-core.yaml:30  | L3                | direct rename |
| agendamento                     | pep.py:70       | L3        | scheduling                   | L0-core.yaml:31  | L3                | direct rename |
| respostas_informativas          | pep.py:71       | L3        | informational_response       | L0-core.yaml:32  | L3                | direct rename |
| lembretes                       | pep.py:72       | L3        | reminders_nudges             | L0-core.yaml:33  | L3                | direct rename |
| autorizacao_automatica          | pep.py:73       | **L3**    | authorization_approval       | L0-core.yaml:22  | **L2**            | **Δ CONTRADICTION**: pep codes auto-authorization at L3 (ALLOW autonomous, no ceiling), but auto-authorization is L2 gated by DMN+ceiling. This is a latent bypass. Drop `autorizacao_automatica`; the only auto-auth action is `authorization_approval` (L2). Confirm no caller depends on the L3 name. |

### 3.1 Canonical actions with NO pep.py equivalent (adopt wholesale from YAML)
These already exist in `L0-core.yaml` + every `agent.yaml` allowlist and must be in the resolved matrix
(they are absent from pep.py's dict today, so any call to them currently DENYs as "unknown" — a live gap):
`adequacao_fallback_commitment` (L1, L0-core.yaml:20), `query_decision_engine` (L3, :36),
`start_compliance_process` (L2, :38), `correlate_process_message` (L2, :39), `query_process_status` (L3, :40),
`read_phi_data` (L3, :42), `send_beneficiary_message` (L3, :44), `send_beneficiary_template` (L2, :45),
`read_write_memory` (L3, :47), `erase_patient_memory` (L1, :48). After D2 the PEP resolves all of these
directly from YAML — no dict edit needed; this is the payoff of loading the matrix.

---

## 4. Property-test plan (concrete names + assertions)

Target file: `tests/unit/gateway/test_pep_policy_unification.py` (new) + updates to existing
`tests/unit/gateway/test_pep.py` (which today asserts the PT vocabulary, test_pep.py:9-73 — those tests
must be rewritten to the English vocabulary or they will pin the regression).

1. `test_every_yaml_action_resolvable_by_pep` — load matrix from real `spec/policies/autonomy/L0-core.yaml`;
   for **every** `action` in `matrix.actions`, assert `PEP.evaluate(action)` returns a `Decision` in
   {ALLOW, DENY, REQUIRE_HUMAN} and never raises / never returns None. (T1.8 criterion: every YAML action
   resolvable by PEP.)
2. `test_hard_frozen_set_equals_yaml_frozen_set_equals_code` — three-way equality:
   (a) `pep.HARD_ACTIONS`, (b) `{e.action for e in _hard_frozen.yaml.hard_items}`, (c)
   `{name for name,spec in L0-core.yaml.actions if spec.hard}`. Assert all three sets equal
   `{clinical_decision, authorization_denial, nip_manter_negativa, fraud_accusation, contract_termination}`.
   (T1.8 criterion: hard-frozen set == YAML frozen set.)
3. `test_unknown_action_denies_fail_closed` — `PEP.evaluate("acao_inexistente")` → `Decision.DENY`.
   (T1.8 criterion: unknown action → DENY.)
4. `test_retired_pt_names_now_deny` — each retired PT name (`negativa_cobertura`, `reembolso_calculo`,
   `triagem_whatsapp`, …) → `Decision.DENY` (unknown, fail-closed) — proves the vocabulary actually migrated,
   not aliased.
5. `test_every_hard_action_denies` — for each of the 5 English hard actions, `PEP.evaluate(action)` → DENY
   regardless of `agent_context` (port test_pep.py:9-16 to English + full 5).
6a. `test_missing_core_file_refuses_to_start` — `load_matrix(tmp_path/"nao-existe.yaml")` raises
   `FileNotFoundError` (v1 pep.py:191 semantics); the PEP factory normalizes it into refuse-to-start
   (assert factory raises, not swallows). (T1.8 criterion: missing YAML → refuse to start.)
6b. `test_malformed_core_refuses_to_start` — two sub-cases asserted separately: a core whose YAML parses
   to a non-dict (e.g. bare string) → `PolicyError` (v1 pep.py:192-193); a core with a YAML **syntax**
   error (e.g. unbalanced bracket) → `yaml.YAMLError`, which the factory must also normalize into
   refuse-to-start. (T1.8 criterion: unparseable YAML → refuse to start.)
7. `test_core_missing_hard_item_refuses` — a core that omits `contract_termination` (or marks it non-L0)
   → `PolicyError` (v1 pep.py:213-217 behavior).
8. `test_overlay_cannot_touch_hard_item` — an overlay with `authorization_denial: {level: L2}` (or params)
   → `PolicyError` (v1 pep.py:243-247). Parametrize over all 5 hard actions.
9. `test_overlay_may_refine_nonhard_params_only` — `tenants-amh.yaml` overlay refining
   `authorization_approval.max_value_brl` succeeds; an overlay loosening a non-hard level (L2→L3) →
   `PolicyError` (v1 pep.py:249-257).
10. `test_real_policies_reconcile_and_freeze_nip_manter_negativa` — port v1 test_autonomy.py:79-94: load the
    real `spec/policies/autonomy/` dir; assert `nip_manter_negativa` and `authorization_denial` are frozen L0
    and the whole dir reconciles (frozen ↔ core-hard) with no error.
11. `test_agent_allowlist_actions_all_in_matrix` — for each `agent.yaml` `autonomy_actions` string entry,
    assert it exists in the resolved matrix (catches drift between agent declarations and the matrix).
12. `test_overlay_unknown_action_raises_policy_error` — an overlay whose `overrides` references an action
    absent from the core (e.g. `acao_fantasma: {params: {max_value_brl: 1}}`) → `PolicyError`
    (v1 pep.py:238-239 "overlay refere acao desconhecida"). Guards against a typo'd overlay silently
    configuring nothing. (Companion fail-closed behavior on the T1.9 resolver path is tested there —
    see docs/design/T1.9-ceiling-enforcement.md §5.8.)
13. `test_agent_yaml_autonomy_policy_paths_resolve` — for each of the 11 `agent.yaml` files, assert the
    declared `autonomy_policy` location resolves (via the T0.3 `resolve_spec_dir()` mechanism) to an
    existing directory containing `L0-core.yaml` — pins the D2 fix-up and prevents path regression.

CI gate (bonus, supports criterion "unparseable → refuse to start" at build time): v2's
`platform/validation/` is a **stub** — it has only `cli.py` + `__init__.py` (verified `ls`), whereas v1 has
`autonomy.py`, `_loaders.py`, `result.py`, `agents.py`, `bpmn.py`, `dmn.py`, `signoff.py`, `topics.py`.
Porting v1 `validation/autonomy.py` (+ `_loaders.py`, `result.py`) gives `make validate-artifacts` the
frozen-set reconciliation gate that `_hard_frozen.yaml:5-6` claims exists but currently does not run in v2.
Recommend as part of T1.8 or an immediate follow-up (flagged; the property tests above cover correctness
even without the CI gate).

---

## 5. Implementation checklist (design-ready, for the later implementer)
1. Port v1 `gateway/pep.py:100-294` (dataclasses + `_load_yaml`/`_parse_core`/`_apply_overlay`/`load_matrix`)
   into v2 `src/maezo/gateway/pep.py`. Keep v2's `Decision` enum values or align to v1's — pick one (v2 uses
   UPPER `ALLOW`, v1 uses lower `allow`; callers of v2 `Decision` must be checked — grep shows only the test
   imports it today).
2. Replace v2 `_AUTONOMY_MATRIX` dict + PT `HARD_ACTIONS` with: English `HARD_ACTIONS` (5) +
   constructor-injected `AutonomyMatrix`.
3. Add a factory (`build_pep()` / DI) that calls `load_matrix(default_core, tenant, overlay)` and fails
   loud on `PolicyError`.
4. Policy file location (RATIFIED, §D2): policies stay in `spec/policies/autonomy/`. Implement the three
   D2 consequences: (a) hatch `force-include`/`artifacts` for `spec/policies/autonomy/*.yaml` in the wheel
   (`pyproject.toml:61-62` currently ships only `src/maezo`); (b) resolve the policy dir through T0.3's
   `resolve_spec_dir()`/`MAEZO_SPEC_DIR` — no second resolution mechanism; (c) repoint all 11 `agent.yaml`
   `autonomy_policy` paths (currently dangling at `../../policies/autonomy`).
5. Rewrite `tests/unit/gateway/test_pep.py` to English vocabulary; add
   `test_pep_policy_unification.py` (§4).
6. Resolve `analise_recurso` orphan (§6-Q2) and `autorizacao_automatica` contradiction before implementation.

## 6. Open questions needing human/orchestrator ratification
- Q1 (policy location): **RESOLVED — RATIFIED 2026-07-16**: `spec/` stays canonical (option (b)); see D2
  for the three implementation consequences (packaging force-include, `resolve_spec_dir()` reuse,
  11× agent.yaml repoint).
- Q2 (analise_recurso orphan): no matrix action exists for appeal analysis. Add `recurso_analysis` (L2?)
  with compliance sign-off, or confirm it maps to an existing dossier-prep L2 action. **blocked(external:
  compliance)**.
- Q3 (autorizacao_automatica): confirm no caller relies on the L3 name before dropping it; it is a latent
  ceiling bypass (L3 = no ceiling). Recommend removal in favor of `authorization_approval` (L2).
- Q4 (Decision enum casing / async): ratify keeping `evaluate` sync for T1.8 and deferring async audit +
  L2 sampling (v1 pep.py:324-413) to a follow-up.

---

## 7. Verification & revisions
Verified by adversarial-verifier (R1) on 2026-07-16 — all 10 checks CONFIRMED (incl. byte-identical policy
YAMLs, English-canonical occurrence counts, and all 4 migration-table semantic deltas judged
strengthen-or-fail-closed). Verdict: REVISE (minor) → revised same day. Revisions landed in this doc:
- Rev 1 (policy location, LOAD-BEARING): D2 "Where the files live" rewritten to the ratified option (b)
  — spec/ canonical + hatch force-include (`pyproject.toml:61-62` evidence) + 11× agent.yaml repoint +
  mandatory reuse of T0.3 `MAEZO_SPEC_DIR`/`resolve_spec_dir()`. Checklist #4 and Q1 updated to match;
  property test 13 added to pin the path fix-up.
- Rev 2 (loader exception semantics): D5 second bullet rewritten — three failure classes
  (`FileNotFoundError` / `yaml.YAMLError` / `PolicyError`, v1 pep.py:190-194) all normalized by the factory
  into refuse-to-start; property test 6 split into 6a (missing file) and 6b (malformed, both sub-cases).
  Note: this revision goes one step beyond the coordinator's wording — v1 raises `yaml.YAMLError` (not
  `PolicyError`) on YAML *syntax* errors, so the factory must normalize three classes, not two.
- Rev 4(b) (overlay-unknown-action): property test 12 added (`PolicyError` per v1 pep.py:238-239), with a
  companion fail-closed resolver test cross-referenced in docs/design/T1.9-ceiling-enforcement.md §5.8.
(Rev 3 and Rev 4(a) apply to docs/design/T1.9-ceiling-enforcement.md — see its Verification & revisions section.)

---

RECOVERY NOTE (2026-07-16): this file was re-emitted from agent context after the session scratchpad was
wiped by another agent's cleanup. Content is the full post-revision version (all revisions + appendix).
