"""Architecture test: worker handler PURITY (P1) — the property that makes the harness the single
audited chokepoint (T-C, T1.10; design MUST-FIX 1 / §4.2, ADR-0007 L0).

**Precondition P1 (handler purity).** A worker handler's *only* externally-visible effect is the
harness's own terminal `complete`/`bpmnError`/`failure` call. The handler body is a pure,
deterministic transform of process variables (`fn(variables) -> dict`) that performs **no**
non-idempotent external side-effect and mints **no** non-deterministic persisted identifier before
returning.

**Why P1 is load-bearing.** Emit-before-complete's fail-closed guarantee — *an emit failure ->
re-delivery -> re-run leaves no duplicate effect* — holds ONLY under P1. If a handler performs a
mid-body external effect `E` before returning, then an emit failure after `E` ran re-delivers and
re-runs `E`; the §4.3 dedup suppresses only the second audit ROW, never the second `E`. A P1
violation silently converts a fail-closed audit into a double-effect (or an audit/effect
misalignment) hazard.

This test enforces P1 statically (the design's "blunt import/call fence", MUST-FIX 1a — a robust
fence, not value-flow taint analysis) in two layers:

  (i)  NETWORK / ENGINE-EFFECT FENCE (strict): no domain worker module may import a network/effect
       client (httpx, aiokafka, kafka, requests, urllib, socket, ...). Handlers reach the engine
       ONLY through the harness's terminal call and the injected `dmn=` seam — never directly.
       This is the "handlers doing engine effects directly" property the harness exists to own.

  (ii) NON-DETERMINISM BASELINE (tracked): detect non-deterministic identifier sources
       (`time.time_ns`, `time.time`, `uuid.uuid4/uuid1`, `random.*`, `secrets.*`, `os.urandom`) in
       domain worker modules and assert they are confined to a DOCUMENTED baseline. A NEW module
       adopting one fails CI (the regression the fence exists to catch). This is a lint baseline
       with a tracking note per entry (the ADR-0024/T-H "grandfather with a ticket" pattern), NOT a
       disabled validation — it actively fails on new violations and on unexpected baseline drift.

FINDING (recorded for the R1 verifier): the design's MUST-FIX 1 named ONLY `ans_submit`'s
`protocolo_ans = sha256(time_ns())` as the P1 (ii) latent violation. The tree actually has SEVEN
domain worker modules minting non-deterministic identifiers (auth/contas/ans_submit/inadimplencia/
lgpd/recurso/reembolso) — the design UNDERCOUNTED, exactly as the R1 re-review caught the 5-vs-9
`start_process_idempotent` undercount. All seven are latent today (the minted values are output
variables of stub/no-network handlers, so no real external `E` re-runs); each becomes a genuine
double-effect / audit-misalignment hazard the moment its handler performs a real external effect
keyed on that identifier. `ans_submit` WAS the T-H (T2.6-owned) named co-requisite; the other six are
recorded here so they cannot silently become real effects without tripping this fence.

UPDATE (FAB-SLA-RISK-NOTIFIED-SLICE4): the fabricated-fact fence below now also catches the
`sla_risk_notified`/`deadline_risk_notified` keys and the `"status": "risk_notified"` STRING form
(`auth.NotifySlaRiskWorker`, which had no Kafka seam at all and still asserted both a notification
status and an event topic). Ten domain modules were fixed in the same commit as the widening, so
`_FABRICATED_FACT_BASELINE` stays EMPTY.

UPDATE (T2.6-1, design §2.A): `ans_submit` has since been FIXED and REMOVED from the baseline — its
fabricated `protocolo_ans = sha256(time_ns())` was replaced by the explicit `AnsGatewayTransport`
triple (`ans_gateway.py`: Refusing prod-default / LabeledMock deterministic-by-business-key / Real
creds-blocked stub). The baseline now tracks SIX modules; the pin
(`test_ans_submit_protocolo_th_corequisite_landed_deterministic`) guards the fixed state.
"""

from __future__ import annotations

import ast
from pathlib import Path

import maezo.tools.workers as workers_pkg

# Infra modules — NOT worker handlers, legitimately import network clients / use jitter. Excluded
# from the domain-handler fences below (each with the reason it is infra, not a BPMN handler).
_INFRA_MODULES: frozenset[str] = frozenset(
    {
        "__init__",
        "harness",  # the dispatch loop itself: owns the httpx transport + the audit emit seam
        "dmn_transport",  # the DMN transport seam (httpx) workers reach ONLY via the dmn= param
        "base",  # WorkerBase/FunctionWorker/registry scaffolding
        "bootstrap",  # composition root (register_all_workers)
        "ceilings",  # governance-ceiling resolver (policy loader, no engine/PHI)
        # GAP-AUTH-4 criteria gate: the RATIFICATION-manifest loader
        # (`spec/processes/dmn/auth-criteria-ratification.yaml`). Structurally identical to
        # `ceilings` above — a local, deterministic YAML policy loader with no engine client, no
        # PHI and no worker registration; consumed by `auth.ValidateAutoCriteriaWorker` only.
        "auth_criteria",
        # RN 259 `adequacao_gap` CANDIDATE shadow: the ratification-manifest loader
        # (`spec/processes/dmn/adequacao-gap-shadow-candidate.yaml`) + the pure evaluation of the
        # candidate rule set. Structurally identical to `auth_criteria` directly above — a local,
        # deterministic YAML loader with no engine client, no PHI and no worker registration;
        # consumed by `adequacao.route_remediation` only, and there ONLY as an observation seam
        # that returns None and cannot influence the verdict or the routing
        # (`adequacao._record_gap_shadow`; proved by test_adequacao_shadow.py section 6).
        "adequacao_shadow",
        "phi_vars",  # one-way PHI redaction helper
        "_audit_ctx",  # per-task DMN-version collector (T-B)
        # T-D (merged in the T1.10 wave alongside this arch-test): the fresh-client-per-call
        # CibSevenTransport SEAM (GAP-INAD-1) — an engine transport like dmn_transport, reached
        # by workers ONLY via the engine= param; registers no workers, legitimately owns httpx.
        "cibseven_engine",
        # T2.6-1 (design §2.A): the ANS protocol-issuance SEAM (AnsGatewayTransport triple —
        # Refusing prod-default / LabeledMock / Real creds-blocked stub), reached by ans_submit
        # ONLY via the ans_gateway= param; registers no workers. It replaced ans_submit's fabricated
        # sha256(time_ns) protocol, so ans_submit itself drops out of the non-determinism baseline
        # below.
        "ans_gateway",
        # T2.6-2 (design §2.B): the TISS/XSD schema-validation SEAM (`TissSchemaValidator` — a
        # local, deterministic resolver like `ceilings.py`'s `CeilingResolver`, not a networked
        # transport), reached by ans_submit ONLY via the `tiss_validator=` param; registers no
        # workers. Replaced ans_submit's `validate_data` echo-stub with real
        # `lxml.etree.XMLSchema` validation.
        "tiss_schema",
        # T2.6-2 TISS-schema-pin gate — DARK BUILD, NOT wired this wave (see that module's
        # docstring "SCOPING DECISION"). Structurally identical to `auth_criteria`/`tiss_schema`
        # directly above: a local, deterministic YAML ratification-manifest loader + XSD
        # validator, no network client, no PHI, no worker registration. `ans_submit.py` does not
        # import it (pinned by
        # test_tiss_schema_pin.py::test_tiss_schema_pin_not_imported_or_referenced_by_ans_submit);
        # its one demonstration consumption function (`tiss_schema_pin_gate_entry`) is exercised
        # only by that module's own tests, never registered on any harness/topic.
        "tiss_schema_pin",
    }
)

# Network / engine-effect clients a domain handler must NEVER import directly (fence i). A handler's
# only engine touch is the harness terminal call + the injected dmn= seam.
_FORBIDDEN_IMPORT_ROOTS: frozenset[str] = frozenset(
    {"httpx", "aiokafka", "kafka", "requests", "urllib", "aiohttp", "socket", "websockets"}
)

# Non-deterministic identifier sources (fence ii). Dotted attribute access forms; `random`/
# `secrets`/`os.urandom` are matched by their module root (any attribute).
_NONDET_DOTTED: frozenset[str] = frozenset(
    {"time.time_ns", "time.time", "uuid.uuid4", "uuid.uuid1", "uuid.uuid3", "uuid.uuid5", "os.urandom"}
)
_NONDET_ROOTS: frozenset[str] = frozenset({"random", "secrets"})

# DOCUMENTED baseline of domain worker modules that currently mint non-deterministic identifiers.
# Value = the tracking note. NEW entries (a module not here) FAIL the fence. See module FINDING.
_NONDETERMINISM_BASELINE: dict[str, str] = {
    # NOTE: `ans_submit` was REMOVED from this baseline by T2.6-1 (the T-H co-requisite landed). Its
    # fabricated `protocolo_ans = sha256(time_ns())` was replaced by the explicit
    # `AnsGatewayTransport` triple (Refusing prod-default / LabeledMock deterministic-by-business-key
    # / Real creds-blocked) — see `test_ans_submit_protocolo_th_corequisite_landed_deterministic`.
    "auth": "dossier_ref / auth_number = uuid4 — latent (output vars of a no-network handler); "
    "must become deterministic before any real keyed external effect (P1).",
    # `contas` REMOVED (M-9): `glosa_id = GLOSA-{analista}-sha256(time_ns())` is now
    # `_glosa_id(input_data)` — a sha256 over the acceptance's own contract facts + process-
    # instance anchors. F3 MINOR-5: it was LATENT, exactly like its baseline neighbours — the
    # minted value never reaches the `RECURSO-{tenant}-{guia}-{glosa_id}` anchor (ACEITAR is
    # terminal, its published `event_payload_vars` exclude `glosa_id`, and RECORRER's `glosa_id`
    # is externally supplied; full derivation in `contas._glosa_id`'s docstring). Fixed because a
    # latent non-deterministic identifier is worth removing, not because a live path consumed it.
    # Pinned by `test_contas_glosa_id_m9_landed_deterministic` below.
    "inadimplencia": "dossier_ref = uuid4 — latent; same P1 caveat.",
    "lgpd": "package_ref = uuid4 — latent; same P1 caveat.",
    # `recurso` REMOVIDO (ADR-0040): o unico mint de relogio do modulo era
    # `register_desistencia`'s `RECDESIST-{sha256(time_ns())}`, e ele morreu com a reescrita de
    # perspectiva. Os dois protocolos que sobraram sao DETERMINISTICOS por business key —
    # `RECIND-{business_key}` (`registrar_indeferimento`) e `RECRESP-{business_key}`
    # (`_mint_protocolo_resposta`) — entao uma re-entrega da mesma task cunha o MESMO protocolo em
    # vez de uma identidade nova para uma unica decisao. Pinado por
    # `test_recurso_protocolos_sao_deterministicos_por_business_key` abaixo.
    "reembolso": "comprovante_ref = sha256(time_ns()) — latent; same P1 caveat.",
}


def _domain_worker_modules() -> dict[str, Path]:
    pkg_dir = Path(workers_pkg.__file__).parent
    return {p.stem: p for p in sorted(pkg_dir.glob("*.py")) if p.stem not in _INFRA_MODULES}


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _nondeterministic_calls(tree: ast.AST) -> set[str]:
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            dotted = f"{node.value.id}.{node.attr}"
            if dotted in _NONDET_DOTTED or node.value.id in _NONDET_ROOTS:
                hits.add(dotted)
    return hits


# GAP-FAB-NOTIF (no-fabricated-facts fence). Keys a domain worker handler once returned as an
# unconditional `True` constant — a regulatory/notification "fact" with no real channel and (in
# both confirmed cases) ZERO downstream consumers anywhere in BPMN/DMN:
#   - `credenciamento.notify_prestador` (renamed `dispatch_prior_notice`) ->
#     `{"notificacao_previa_feita": True}` — contract claimed RN 567 prior notice "comprovada";
#     no conditionExpression/resultVariable/DMN input ever read it (D-N1).
#   - `adequacao.notify_coordenacao` -> `{"notificacao_enviada": True}` — one line in the whole
#     repo, zero consumers (D-N2).
# GAP-INAD-8 (WP-FATOS-FABRICADOS slice 2) closed the two REMAINING instances, both on the RN-593
# prior-notice step and both now returning `{}`:
#   - `inadimplencia.notify_beneficiario` (renamed `dispatch_prior_notice`) ->
#     `{"notificacao_previa_feita": True, "notificacao_previa_registrada_em": "now"}`. UNLIKE the
#     two above this one had REAL consumers — `inadimplencia_status.dmn:37-38` and, via the
#     CANCEL-001 handoff, `cancel_admissibility.dmn:63-64` + `cancel.assess_admissibility` — so
#     the constant made their `PENDENTE_NOTIFICACAO` branch unreachable for every
#     inadimplencia-originated case.
#   - `cancel.notify_beneficiario` (renamed `dispatch_prior_notice`) -> `{"notified": True, ...}`,
#     the same step on the consumer process, zero consumers; fixed with it because the slice-2 fix
#     is what first routes real traffic into that branch.
# `notified` joins the key set with them: the fence is only as wide as the shapes it has seen, and
# leaving it out would let the identical fabrication return under a name already used once.
# FAB-NOTIFIED-TRIO closed the THREE instances that widening surfaced, all now returning `{}`:
#   - `recurso.notify_prestador` (renamed `request_documents`, matching its own BPMN topic) ->
#     `{"notified": True, "prestador_id": ..., "glosa_id": ..., "message_type": ...}` on
#     `ST_SolicitarDocumentos`; the pended event is published by the BPMN's own
#     `ST_PublishRecursoPended`, and `test_sp_op_recurso_001.py:1250` pins that this task emits no
#     notification at all.
#   - `reembolso.request_documents` -> `{"notified": True, "status": "pended", ...}`, whose own
#     docstring disclosed it never publishes and returned the claim anyway.
#   - `contas.notify_sla_risk` -> `{"notified": True, "grupo": ..., ...}` on the non-interruptive
#     SLA alert.
# All three were zero-consumer (no conditionExpression, no DMN inputExpression, no downstream
# worker, no contract line), so each was the mechanical `{}` fix, not an INADIMPLENCIA-style
# two-consumer problem.
#
# All SEVEN now return `{}` and `_FABRICATED_FACT_BASELINE` below is EMPTY. This static fence keeps
# them fixed and catches the same shape (a `return {...}` mapping one of these keys straight to
# the literal `True`, no computation, no input dependency) anywhere else in the domain worker tree.
#
# FAB-SLA-RISK-NOTIFIED-SLICE4 widened the set with `sla_risk_notified` and
# `deadline_risk_notified`. Those are the SAME species under a different name, and the reason the
# earlier slices did not catch them: `notified` matched only the bare word, so NINE `notify_sla_
# risk` handlers (recurso/reembolso/cancel/credenciamento/fraude/inadimplencia/pagto/adequacao/
# programa) plus `nip.notify_deadline_risk` fabricated the identical fact under a compound name.
# All ten are fixed IN THE SAME COMMIT as this widening, so the baseline below stays EMPTY.
_FABRICATED_FACT_KEYS: frozenset[str] = frozenset(
    {
        "notificacao_previa_feita",
        "notificacao_enviada",
        "notified",
        "sla_risk_notified",
        "deadline_risk_notified",
    }
)

# SAME SPECIES, ESCAPING VIA A STRING (FAB-SLA-RISK-NOTIFIED-SLICE4). `auth.NotifySlaRiskWorker.
# execute` fabricated the fact TWICE OVER without ever using the literal `True`: it returned
# `{"status": "risk_notified", ..., "event": "agents.events.auth.sla_breached"}` from a SYNC
# `WorkerBase.execute` with no Kafka seam at all — a notification status AND an event topic, as
# though it had published. (The event name was wrong too: `agents.events.auth.sla_breached` is
# published by `ST_PublishSlaBreach` on the INTERRUPTIVE `BT_SlaAnalise` branch, which this
# non-interruptive alert never reaches.) So the fence also flags a `return {...}` mapping the
# generic key `status` to one of these notification-claim literals.
#
# DELIBERATELY NARROW. It does NOT fence every `"status": "<literal>"` — the tree returns many
# honest ones (`blocked_by_guard`, `pended`, `authorized`, `filed`, `published`, ...), and it does
# NOT fence the `"event": "agents.events.*"` idiom, which appears in 13 other places whose events
# ARE published by a sibling `ST_Publish*` task; widening into either without a per-instance
# consumer map would be a guess. `auth`'s `"status": "notice_sent"` (`send_denial_notice`) is a
# DISCLOSED, UNFIXED neighbour of this species — an L0 denial-transmission worker whose `status`
# has real guard consumers, out of this slice's scope (tracked in `docs/review-queue.md`).
_FABRICATED_STATUS_LITERALS: frozenset[str] = frozenset({"risk_notified", "notified", "notificado"})

# DOCUMENTED baseline (same "grandfather with a ticket" pattern as `_NONDETERMINISM_BASELINE`
# above) — modules with a pre-existing, STRUCTURALLY IDENTICAL instance of this shape that a given
# fix deliberately left alone. A NEW module adopting the pattern still fails the fence below.
#
# GAP-INAD-8 (WP-FATOS-FABRICADOS slice 2) removed the one entry slice 1 had grandfathered
# (`"inadimplencia": "notify_beneficiario -> {'notificacao_previa_feita': True}"`) — its R1
# package landed, so the ratchet below required the entry gone, as it should.
#
# The SAME commit widened `_FABRICATED_FACT_KEYS` with `notified`, which surfaced THREE more
# instances of the identical shape in three OTHER process families (recurso/reembolso/contas). It
# grandfathered them as out-of-scope-for-slice-2 and tracked each in `docs/review-queue.md`
# (GAP-RECURSO-5 / GAP-REEMBOLSO-8 / GAP-CONTAS-7).
#
# FAB-NOTIFIED-TRIO fixed all three and REMOVED all three entries, so this baseline is now EMPTY —
# the ratchet below (`resolved = baseline_modules - actual_modules; assert not resolved`) required
# exactly that, in the same commit as the fix. It stays declared (typed, empty) so a future
# genuinely-out-of-scope instance can be grandfathered WITH a tracked gap row, never as a bare code
# comment.
_FABRICATED_FACT_BASELINE: dict[str, str] = {}


def _fabricated_fact_hits(tree: ast.AST) -> set[str]:
    """Static detector for the fabricated-fact shape, in its TWO known forms:

      (a) a `return {...}` dict literal mapping one of `_FABRICATED_FACT_KEYS` directly to the
          constant `True` — reported as the key name;
      (b) a `return {...}` dict literal mapping the generic key `status` to one of
          `_FABRICATED_STATUS_LITERALS` — reported as `status='<literal>'`
          (FAB-SLA-RISK-NOTIFIED-SLICE4: the `auth.NotifySlaRiskWorker` escape, which claimed the
          notification in a STRING and so slipped past form (a) entirely).

    Both forms are produced with no conditional logic and no dependency on `variables`,
    unconditionally on every call. Mirrors this module's other AST-based fences: a blunt
    structural check, not value-flow taint analysis (a fabrication assembled key-by-key into a
    local — `out = {}; out["notified"] = True` — is documented as OUT of this detector's reach and
    is caught instead by the per-handler domain tests asserting `== {}`)."""
    hits: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        value = node.value
        if not isinstance(value, ast.Dict):
            continue
        for key_node, val_node in zip(value.keys, value.values, strict=True):
            if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
                continue
            if not isinstance(val_node, ast.Constant):
                continue
            if key_node.value in _FABRICATED_FACT_KEYS and val_node.value is True:
                hits.add(key_node.value)
            elif key_node.value == "status" and val_node.value in _FABRICATED_STATUS_LITERALS:
                hits.add(f"status={val_node.value!r}")
    return hits


def test_domain_worker_modules_discovered() -> None:
    """Guard: the discovery actually found the 17 registered worker modules (so the fences below
    are not silently scanning an empty set)."""
    mods = _domain_worker_modules()
    # 17 SP-OP-* modules (bootstrap.ALL_WORKER_BOOTSTRAPS = 17: 16 + events).
    assert len(mods) == 17, f"expected 17 domain worker modules, found {sorted(mods)}"


def test_no_domain_worker_imports_a_network_or_engine_client() -> None:
    """Fence (i): a handler reaching the engine/Kafka DIRECTLY would bypass the harness chokepoint
    and break emit-before-complete. NONE may import a network client (strict — TRUE today)."""
    offenders: dict[str, set[str]] = {}
    for name, path in _domain_worker_modules().items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        bad = _imported_roots(tree) & _FORBIDDEN_IMPORT_ROOTS
        if bad:
            offenders[name] = bad
    assert not offenders, (
        "P1 violation — worker handler modules import a network/engine-effect client directly "
        f"(must go through the harness / dmn= seam): {offenders}"
    )


def test_nondeterministic_identifier_minting_confined_to_documented_baseline() -> None:
    """Fence (ii): non-deterministic identifier minting must stay confined to the documented
    baseline. A NEW module adopting it fails here (the P1 regression this fence catches)."""
    actual: dict[str, set[str]] = {}
    for name, path in _domain_worker_modules().items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _nondeterministic_calls(tree)
        if hits:
            actual[name] = hits

    actual_modules = set(actual)
    baseline_modules = set(_NONDETERMINISM_BASELINE)

    new_violations = actual_modules - baseline_modules
    assert not new_violations, (
        "NEW P1 (ii) violation — module(s) started minting non-deterministic identifiers outside "
        f"the documented baseline: { {m: sorted(actual[m]) for m in new_violations} }. Either make "
        "the minted value deterministic (P1) or add an explicit tracking entry to "
        "_NONDETERMINISM_BASELINE with the co-requisite that will fix it."
    )
    # If a baseline entry is FIXED (no longer mints), require the stale entry to be removed — keeps
    # the baseline honest and shrinking, never a rubber stamp.
    resolved = baseline_modules - actual_modules
    assert not resolved, (
        f"baseline modules no longer mint non-deterministic identifiers: {sorted(resolved)} — "
        "remove them from _NONDETERMINISM_BASELINE (the P1 hazard is closed there)."
    )


def test_ans_submit_protocolo_th_corequisite_landed_deterministic() -> None:
    """T-H (T2.6-1, the design's NAMED co-requisite / MUST-FIX 1) LANDED: `ans_submit` no longer
    mints a non-deterministic protocol.

    The fabricated `protocolo_ans = sha256(time.time_ns())` path was removed and replaced by the
    explicit `AnsGatewayTransport` triple (`ans_gateway.py`): Refusing (prod default — refuses,
    fabricates nothing), LabeledMock (deterministic `MOCK-ANS-NAO-VINCULATIVO-{business_key}`), Real
    (creds-blocked stub). Pin the FIXED state so a regression (re-introducing `time_ns`/any
    non-deterministic identifier into `ans_submit`) trips BOTH this pin and the baseline fence
    (`test_nondeterministic_identifier_minting_confined_to_documented_baseline` would then flag
    `ans_submit` as a NEW violation)."""
    assert "ans_submit" not in _NONDETERMINISM_BASELINE
    path = _domain_worker_modules()["ans_submit"]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert "time.time_ns" not in _nondeterministic_calls(tree), (
        "ans_submit still mints a non-deterministic identifier — T2.6-1 removed the fabricated "
        "sha256(time_ns) protocol; nothing in this module may re-introduce time_ns/uuid/random."
    )


def test_contas_glosa_id_m9_landed_deterministic() -> None:
    """M-9 LANDED: `contas` no longer mints `glosa_id` from the wall clock.

    `glosa_id = GLOSA-{analista}-sha256(time.time_ns())[:12]` became `_glosa_id(input_data)` — a
    sha256 over the acceptance's OWN contract facts plus the process-instance anchors, so an
    engine re-delivery of the same `UT_AnalistaContas` decision reproduces the SAME id.

    LIKE its baseline neighbours, this one was LATENT (F3 MINOR-5 — an earlier revision claimed
    the opposite). `glosa_id` does anchor `RECURSO-{tenant}-{numero_guia_tiss}-{glosa_id}`, but
    the value minted HERE never reaches that key: the ACEITAR branch that mints it is terminal,
    its published `event_payload_vars` exclude `glosa_id`, and the RECORRER branch's `glosa_id`
    comes from outside this worker (derivation with BPMN/bridge line cites in
    `contas._glosa_id`'s docstring; `start_recurso`'s M-9 note says the same). Mirrors the
    `ans_submit` pin above: a regression trips BOTH this pin and the baseline fence (which would
    then flag `contas` as NEW).
    """
    assert "contas" not in _NONDETERMINISM_BASELINE
    path = _domain_worker_modules()["contas"]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert not _nondeterministic_calls(tree), (
        "contas re-introduced a non-deterministic identifier source — M-9 made `glosa_id` a pure "
        "function of the contract facts; nothing in this module may use time_ns/uuid/random."
    )


def test_no_domain_worker_returns_unconditional_true_for_fabricated_fact_keys() -> None:
    """GAP-FAB-NOTIF regression fence: no domain worker handler OUTSIDE the documented baseline may
    return a dict literal fabricating a notification/regulatory fact — neither as a bare `True`
    under a `_FABRICATED_FACT_KEYS` key nor as a `_FABRICATED_STATUS_LITERALS` string under
    `status`. `credenciamento.dispatch_prior_notice` (was `notify_prestador`) and `adequacao.
    notify_coordenacao` were the two confirmed, IN-SCOPE instances (D-N1/D-N2, part-B verification
    report); both now return `{}`. GAP-INAD-8 added `inadimplencia`/`cancel`,
    FAB-NOTIFIED-TRIO added `recurso`/`reembolso`/`contas`, and FAB-SLA-RISK-NOTIFIED-SLICE4 added
    the `sla_risk_notified`/`deadline_risk_notified`/`status='risk_notified'` forms across ELEVEN
    handlers in ten modules — the baseline is EMPTY, so ANY hit in ANY domain worker module fails
    here."""
    actual: dict[str, set[str]] = {}
    for name, path in _domain_worker_modules().items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _fabricated_fact_hits(tree)
        if hits:
            actual[name] = hits

    actual_modules = set(actual)
    baseline_modules = set(_FABRICATED_FACT_BASELINE)

    new_violations = actual_modules - baseline_modules
    assert not new_violations, (
        "fabricated regulatory/notification fact(s) reintroduced as an unconditional `True` "
        f"literal: { {m: sorted(actual[m]) for m in new_violations} } — GAP-FAB-NOTIF requires "
        "computing an honest value from real inputs, or returning {} with a documented neutral "
        "reason (never an unconditional fact); or, if genuinely out of this fix's scope, add a "
        "tracking entry to _FABRICATED_FACT_BASELINE."
    )
    # If a baseline entry is FIXED (no longer fabricates), require the stale entry to be removed —
    # keeps the baseline honest and shrinking, never a rubber stamp (mirrors
    # test_nondeterministic_identifier_minting_confined_to_documented_baseline above).
    resolved = baseline_modules - actual_modules
    assert not resolved, (
        f"baseline modules no longer return the fabricated-fact literal: {sorted(resolved)} — "
        "remove them from _FABRICATED_FACT_BASELINE (the fabrication is fixed there)."
    )
    # Confirm the fixed instances are ACTUALLY fixed (not merely absent from `actual` because the
    # module failed to parse, etc.): GAP-FAB-NOTIF's two, GAP-INAD-8's two, FAB-NOTIFIED-TRIO's
    # three, then FAB-SLA-RISK-NOTIFIED-SLICE4's ten modules (five of which are the SAME modules
    # under a second key, so this list grows by `auth`, `fraude`, `pagto`, `programa`, `nip`).
    for fixed in (
        "credenciamento",
        "adequacao",
        "inadimplencia",
        "cancel",
        "recurso",
        "reembolso",
        "contas",
        "auth",
        "fraude",
        "pagto",
        "programa",
        "nip",
    ):
        assert fixed not in actual, f"{fixed} re-introduced a fabricated fact: {sorted(actual[fixed])}"
    # Every grandfathered entry is GONE — the ratchet shrank to empty, never rubber-stamped.
    assert _FABRICATED_FACT_BASELINE == {}


def test_recurso_protocolos_sao_deterministicos_por_business_key() -> None:
    """ADR-0040 LANDED: `recurso` no longer mints anything from the wall clock.

    `register_desistencia`'s `RECDESIST-{sha256(time.time_ns())}` died with the appellant branch.
    The two protocols that remain are pure functions of the instance's own business identity:
    `RECIND-{business_key}` (`registrar_indeferimento`) and `RECRESP-{business_key}`
    (`_mint_protocolo_resposta`) — so an engine re-delivery of the SAME human decision reproduces
    the SAME protocol instead of minting a second identity for one act. Mirrors the `contas` pin
    above: a regression trips BOTH this pin and the baseline fence (which would then flag
    `recurso` as NEW).
    """
    assert "recurso" not in _NONDETERMINISM_BASELINE
    path = _domain_worker_modules()["recurso"]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert not _nondeterministic_calls(tree), (
        "recurso re-introduced a non-deterministic identifier source — both protocolos are pure "
        "functions of the business key; nothing in this module may use time_ns/uuid/random."
    )
