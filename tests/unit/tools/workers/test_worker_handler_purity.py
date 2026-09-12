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

UPDATE (AUTH-SEND-DENIAL-NOTICE-STATUS-LITERAL): the same fence now catches
`"status": "notice_sent"` AND — form (c) — the shape that hid the worst instance of the species:
a dict literal bound to a LOCAL that the function returns through a call
(`notice = {...}; return redact_phi_vars(notice)`). That is `auth.SendDenialNoticeWorker`'s L0
RN-395 denial branch; the only branch the old detector could see there was the defensive,
model-unreachable approval one. Both branches were fixed in the same commit as the widening, so
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
        # R-173 (owner decision 2026-09-04): the engine-variable TYPING declaration
        # (`LONG_TYPED_ENGINE_VARS` + `camunda_int_type`). A leaf module with ZERO imports — no
        # engine client, no PHI, no policy file, no worker registration and no handler; it is the
        # single copy of the Java int32 bounds shared by `harness`/`dmn_transport`/
        # `mcp_cibseven.transport`, all three of which are already infra above.
        "engine_var_types",
        # E04 mechanical amendment v1 (portal AUTH intake): the `portal-auth-intake.v1` exact
        # money CODEC (`AuthExactAmount` + `hydrate_auth_amount`). A leaf module like
        # `engine_var_types` directly above — stdlib `re`/`dataclasses` only, no engine client, no
        # PHI, no policy file, no worker registration and no handler; consumed by
        # `auth._ceiling_valor_cents` only, and only when the native acquisition adapter already
        # selected that hydration type (a caller/browser cannot select a profile).
        "auth_exact_amount",
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
    # `lgpd` REMOVIDO (R-181): o unico mint de relogio/aleatorio do modulo era o
    # `package_ref = f"lgpd-export-{uuid.uuid4().hex[:12]}"` de `ExecuteExportWorker`, e ele morreu
    # com o colapso dos tres `Execute*Worker` no topico modelado `operadora.lgpd.execute_request`.
    # O worker unico (`ExecuteRequestWorker`) nao cunha identificador algum: TODO caminho levanta
    # `WorkerFailureError(retries_left=0)`. O modulo nao importa mais `uuid` — pinado por
    # `test_lgpd_erasure.py::test_execute_request_exportacao_nao_fabrica_pacote`.
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
#
# BEA-09 widened the set with `dossie_montado`. Same species, and the first instance of it that is
# NOT a notification: `fraude.assemble_dossier` returned `{"dossie_montado": True, "dossie_items":
# len(evidencia_refs)}` from a function whose own body carried the comment "Placeholder: real
# implementation calls Beatriz via A2A" — no A2A call, no dossier, no narrative, on a process whose
# next steps are `seal_custody_bundle` and the L0-hard human User Task `UT_DecisaoInvestigador`.
# The claim went into process scope through the harness `complete` (`harness.py:1779-1783`) exactly
# like the notification family, so the audit trail of a fraud investigation recorded a dossier that
# was never assembled. Fixed IN THE SAME COMMIT as this widening (the fix commit; the widening
# lands one commit earlier as the RED proof), so the baseline below stays EMPTY.
#
# The sibling fabrication in the SAME function pair — `gather_evidence`'s
# `{"evidencia_coletada_em": "now"}` — is NOT added here, deliberately and not by oversight: this
# detector matches `<key>: True` and `status: <literal>`, so a placeholder-timestamp STRING under
# an arbitrary key is outside its two shapes. Adding the key would be an INERT entry that reads
# like coverage. The `"now"` placeholder-timestamp shape has three more live instances in the
# worker tree (`fraude.intake::intake_ts`, `programa::consent_verified_at`,
# `programa::data_enrollment` — `grep -rn '": "now"' src/maezo/tools/workers/`), so widening into
# it is its own slice with its own consumer map, not a free rider on this one. Reported by BEA-09,
# not fixed by it.
#
# FAB-PROGRAMA-NOW-TIMESTAMPS is that slice for the two `programa` instances (`fraude.intake_ts`
# was already fixed and REMOVED — no longer live — by FAB-INTAKE-CASO-REGISTRADO; see that
# widening's own comment above). It adds detector FORM (d): a `return {...}` dict literal (or a
# dict literal bound to a local the function then returns — same forms (a)-(c) reuse) mapping ANY
# key to the string constant `"now"`, reported as `f"{key}='now'"`. Deliberately keyed on the
# VALUE, not an allowlisted key name (unlike `_FABRICATED_FACT_KEYS`/`_FABRICATED_STATUS_LITERALS`
# above): `"now"` is never a valid ISO-8601 instant under ANY key, so matching the literal value is
# an explicit, narrow shape — not a silent allowlist, and it does not need a per-key entry to grow.
# Consumer map (re-derived for this slice, `grep -rn 'consent_verified_at\|data_enrollment' spec/
# src/ tests/ docs/processes/`): **zero** hits besides the two `programa.py` return sites and this
# comment/the fence's own baseline docstrings — no `conditionExpression`, `inputExpression`, BPMN
# `outputParameter`, downstream worker, golden or contract line reads either key
# (`docs/processes/contracts/SP-OP-PROGRAMA-001.md` never declared them in "Variaveis de saida").
# `check_consent` returned `{"consentimento_ativo": True, "consent_verified_at": "now"}` and
# `enroll_beneficiario` returned `{"enrollment_realizado": True, "data_enrollment": "now"}` — both
# real, gating/logging actions (unlike `intake`'s single-statement no-op body), but the wall-clock
# instant of each is ALREADY a fact of the engine (`activity-instance` `endTime` of `ST_CheckConsent`
# / `ST_BuildCarePlan`), so the in-scope copy was redundant, not honest, before it was even
# considered fabricated. Re-verified with the widened detector BEFORE the fix (`ast.parse` over
# `src/maezo/tools/workers/*.py`, same walk this fence runs): `programa` is the ONLY module that
# trips form (d) — `{'data_enrollment', 'consent_verified_at'}` — confirming the widening has zero
# collateral hits (the two prose mentions of `": "now"` inside `fraude.py`'s OWN docstrings, and the
# one inside `inadimplencia.py`'s docstring, are plain text inside a `Constant` docstring string,
# never a `Return`/`Assign` dict literal, so this AST-based detector cannot and does not see them).
# Fixed IN THE SAME COMMIT as this widening lands one commit later (the fix commit; this widening
# lands first as the RED proof), so the baseline below stays EMPTY.
#
# FAB-REFER-TO-LEGAL / FAB-INTAKE-CASO-REGISTRADO widen the set with `referral_executado` and
# `caso_registrado` — the two instances BEA-09's verification surfaced in the SAME module, on the
# two ends of the same process:
#   - `fraude.refer_to_legal` returned `{"referral_executado": True, "destinos": <echo of the
#     input>}` from a body whose only statement was `logger.info`. It is the ADVERSE path: the
#     engine reaches it only DOWNSTREAM of the human accusation (`UT_DecisaoInvestigador`, L0
#     hard), the sealed `bundle_root` and the SECOND human gate `UT_RevisaoReferral`
#     (juridico/compliance approving the referral destinations), and its only outgoing flow ends
#     at `End_EncaminhadoJuridico` ("Encaminhado a juridico/ANS/civel/penal"). No referral is
#     performed anywhere: `fraude.py` has zero `kafka.publish(` call sites
#     (`register_fraude_workers` does `del kafka  # unused`) and no legal/ANS transport exists —
#     the contract itself lists the referral obligations (prazo/forma/autoridade) as DRAFT/verify,
#     "nao estao pinadas em nenhum repo". So the non-repudiable trail of a fraud referral recorded
#     `referral_executado=true` for an act that never left the process.
#   - `fraude.intake` returned `{"caso_registrado": True, "intake_ts": "now"}` from a body whose
#     only statement was `logger.info`. Both fixed IN THE SAME COMMIT as this widening, so the
#     baseline below stays EMPTY.
#
# ENROLL-BENEFICIARIO-SEM-EFEITO-REAL (achado do verificador de FAB-PROGRAMA-NOW, VERIFY-FABPROG.md
# §4(ii)/achado 1) widens the set with `enrollment_realizado` — the SAME species as
# `dossie_montado`/`referral_executado` (BEA-09/FAB-REFER-TO-LEGAL) that this fence was already
# blind to: `programa.enroll_beneficiario` returned `{"enrollment_realizado": True}` unconditionally
# from a body whose only statement was `logger.info`, while the contract
# (`docs/processes/contracts/SP-OP-PROGRAMA-001.md`, "Topicos" row for
# `operadora.programa.build_care_plan`) and the BPMN (`programa.py:687`'s own disclosure comment,
# "spec match: task name says 'care.enroll'") declare an A2A delegation `care.enroll` to Valentina
# that the code never calls. NOT the desfecho STRING `enrollment_realizado` that
# `ST_ProactiveContact`/the `programa.completed` fallback fix via BPMN `outputParameter`/ternary
# literal (`spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn:157,334`) — that is a
# fixed vocabulary value on an unrelated variable (`desfecho`), never read by this AST detector,
# which only ever matches a `return {...}` dict-literal KEY. Fixed IN THE SAME COMMIT as this
# widening, so `_FABRICATED_FACT_BASELINE` stays EMPTY.
#
# FAB-PUBLISH-CONTACT (NEW-A2-1/NEW-A2-2) widens the set with `evento_publicado` and
# `contato_realizado` — the two shapes the fence was still blind to, in the two directions the
# family can take:
#   - `evento_publicado` (2 sites: `fraude.publish_completed`, `pagto.publish_completed`) claimed a
#     domain-event PUBLISH from a body whose only statement was `logger.info`, in modules that have
#     ZERO `kafka.publish(` call sites (`register_fraude_workers` does `del kafka  # unused`). The
#     honest sibling is next door and predates it: `events.py` returns `event_published` from the
#     producer's REAL delivery bool and adds `event_publish_best_effort_failure` on a swallowed
#     failure. Neither site was reachable by the engine either — `operadora.{fraude,pagto}.
#     publish_completed` are ORPHAN CODE TOPICS carried by ZERO BPMN service tasks (every
#     `ST_Publish*` routes through the generic `operadora.events.publish`), so both functions and
#     both registrations were RETIRED, mirroring `operadora.lgpd.publish_completed`
#     (LGPD-PUBLISH-COMPLETED-ORPHAN-TOPIC, R-103/R-H) and `operadora.programa.monitor_programa`
#     (PERSP-C5-MONITOR-PROGRAMA). Retiring them removes the only two sites, so widening here has
#     zero collateral hits and the baseline stays EMPTY.
#   - `contato_realizado` (1 site: `programa.proactive_contact`) claimed a CONTACT with the
#     beneficiary. `ST_ProactiveContact` IS a real BPMN task, but no contact channel is wired to
#     it: the raw handler publishes an OBSERVABILITY notification onto
#     `operadora.notifications.internal` (`_PROACTIVE_CONTACT_NOTIFICATION_TYPE`), which reaches no
#     beneficiary, and on the `kafka is None` path it returned the claim having done nothing at
#     all. So this one is a REAL lacuna and takes the `GAP_*` class-token treatment
#     (`programa.GAP_CONTATO_BENEFICIARIO_NAO_LIGADO` -> `{"contato_gap": ...}`), not `{}`.
# The `published: True` sibling family (`{ans_submit,cancel,contas,nip,recurso,reembolso}.
# publish_completed`/`publish`, 6 further sites of the identical shape found by the AST census this
# WP ran over `src/maezo/tools/workers/*.py`) is deliberately NOT added here: fencing it without
# fixing it would need six grandfathered baseline entries, i.e. an entry that reads like coverage
# while nothing changed. Reported as an adjacency, not free-ridden on this slice.
_FABRICATED_FACT_KEYS: frozenset[str] = frozenset(
    {
        "notificacao_previa_feita",
        "notificacao_enviada",
        "notified",
        "sla_risk_notified",
        "deadline_risk_notified",
        "dossie_montado",
        "referral_executado",
        "caso_registrado",
        "enrollment_realizado",
        "evento_publicado",
        "contato_realizado",
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
# NOT fence the `"event": "agents.events.*"` idiom; widening into the latter without a
# per-instance consumer map would be a guess.
#
# AUTH-SEND-DENIAL-NOTICE-STATUS-LITERAL (slice 5) closed the neighbour the slice-4 comment
# disclosed here as UNFIXED: `auth.SendDenialNoticeWorker.execute` returned `"status":
# "notice_sent"` on BOTH success paths from a SYNC `WorkerBase.execute` with no Kafka seam and no
# channel of any kind — its own docstring already said the real secure channel is Phase 1. So
# `notice_sent` joins the set and both sites now omit `status` entirely; the guard record
# (`status="blocked_by_guard"` + `ERR_DENIAL_NOT_HUMAN`) is untouched, being a true fact about
# the refusal itself.
#
# The `"event": "agents.events.*"` exclusion was RE-DERIVED for this slice rather than inherited.
# Corrected count: the idiom had **13 sites in the whole worker tree** (7 `auth`, 2 `escalation`,
# 4 `lgpd`), TWO of which were inside `send_denial_notice` itself — so slice 4's "13 OTHER places"
# over-counted the neighbours by two; the honest figure is 11 others. Forward/backward
# reachability over the BPMN graph (not prose) was RE-DERIVED for **9 of the 13** (7 `auth` + 2
# `escalation`): each of those 9 has an `operadora.events.publish` task carrying the SAME
# `event_topic` on its own token path, `send_denial_notice`'s two included:
# `ST_EnviarNegativaFormal`'s only outgoing flow is `Flow_Negativa_Pub` -> `ST_PublishNegada`
# (`event_topic=agents.events.auth.completed`). The remaining **4 `lgpd` sites had NO token path
# at all**: `operadora.lgpd.execute_export`/`execute_rectification`/`execute_erasure`/
# `publish_completed` are ORPHAN CODE TOPICS carried by ZERO BPMN service tasks
# (`docs/compliance/lgpd-topic-reconciliation.md:38-39`, rows O2-O5 at `:61-64`).
#
# COUNT UPDATED (gap `LGPD-PUBLISH-COMPLETED-ORPHAN-TOPIC`, owner decision R-103, remedy R-H):
# the `publish_completed` site is GONE — `PublishCompletedWorker` was retired with its
# registration and its three tests, because it was the one orphan whose fabrication was a
# `"status": "published"` out of a publisher-less sync `execute`. The tree now carries **12
# sites** (7 `auth`, 2 `escalation`, **3** `lgpd`), and the orphan trio that remains is
# `execute_export`/`execute_rectification`/`execute_erasure` (rows O2-O4) — still out of scope
# here: their fate is R-D (`execute_request` decomposition), which is DPO/SME-sign-off-gated, not
# mechanical. That is why no `event` literal is fenced here — including the two in the worker
# slice 5 fixes.
_FABRICATED_STATUS_LITERALS: frozenset[str] = frozenset(
    {"risk_notified", "notified", "notificado", "notice_sent"}
)

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


def _dict_literal_fabrications(value: ast.Dict) -> set[str]:
    """The THREE fabricated SHAPES inside one dict literal (see `_fabricated_fact_hits`).

    FAB-PROGRAMA-NOW-TIMESTAMPS adds form (d): ANY key mapped to the string constant `"now"` — a
    placeholder-timestamp value, never a valid ISO-8601 instant under any key name. Reported as
    `f"{key}='now'"`, so it never collides with form (a)'s bare key-name hits.
    """
    hits: set[str] = set()
    for key_node, val_node in zip(value.keys, value.values, strict=True):
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            continue
        if not isinstance(val_node, ast.Constant):
            continue
        if key_node.value in _FABRICATED_FACT_KEYS and val_node.value is True:
            hits.add(key_node.value)
        elif key_node.value == "status" and val_node.value in _FABRICATED_STATUS_LITERALS:
            hits.add(f"status={val_node.value!r}")
        elif val_node.value == "now":
            hits.add(f"{key_node.value}='now'")
    return hits


def _returned_names(func: ast.AST) -> set[str]:
    """Every NAME that appears anywhere inside a `return` expression of `func`.

    Deliberately blunt (it does not care WHERE in the expression the name sits), so both
    `return notice` and `return redact_phi_vars(notice)` count. Over-approximates for nested
    functions — `ast.walk` on a factory also visits its inner handler's returns — which can only
    make the fence FIRE MORE, never less; it reports zero extra modules on the tree today.
    """
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and node.value is not None:
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
    return names


def _fabricated_fact_hits(tree: ast.AST) -> set[str]:
    """Static detector for the fabricated-fact shape, in its FOUR known forms:

      (a) a `return {...}` dict literal mapping one of `_FABRICATED_FACT_KEYS` directly to the
          constant `True` — reported as the key name;
      (b) a `return {...}` dict literal mapping the generic key `status` to one of
          `_FABRICATED_STATUS_LITERALS` — reported as `status='<literal>'`
          (FAB-SLA-RISK-NOTIFIED-SLICE4: the `auth.NotifySlaRiskWorker` escape, which claimed the
          notification in a STRING and so slipped past form (a) entirely);
      (c) the SAME shapes in a dict literal BOUND TO A LOCAL that the enclosing function then
          returns — `notice = {...}; return redact_phi_vars(notice)`;
      (d) a `return {...}` dict literal (direct or via (c)'s local-binding) mapping ANY key to the
          string constant `"now"` — reported as `f"{key}='now'"` (FAB-PROGRAMA-NOW-TIMESTAMPS:
          `programa.check_consent`/`enroll_beneficiario` claimed a real ISO-8601 instant with the
          literal placeholder string `"now"` instead of computing or omitting one).

    Form (c) is not a refinement: without it this fence is blind exactly where it matters most.
    `auth.SendDenialNoticeWorker.execute` has both success paths, and the one that forms (a)/(b)
    can see is the DEFENSIVE, model-unreachable approval branch; the L0 denial branch — the one
    that carries the RN 395 negativa — builds `notice` as a local and returns it through
    `redact_phi_vars(...)`. Fencing only the returned literal would have left the regulated path
    unfenced while looking green (AUTH-SEND-DENIAL-NOTICE-STATUS-LITERAL; proved by mutating each
    site independently).

    All three forms are produced with no conditional logic and no dependency on `variables`,
    unconditionally on every call. Mirrors this module's other AST-based fences: a blunt
    structural check, not value-flow taint analysis (a fabrication assembled key-by-key into a
    local — `out = {}; out["notified"] = True` — remains OUT of this detector's reach and is
    caught instead by the per-handler domain tests asserting `== {}`)."""
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            hits |= _dict_literal_fabrications(node.value)
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        returned = _returned_names(func)
        for node in ast.walk(func):
            if isinstance(node, ast.Assign):
                targets: list[ast.expr] = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            if any(isinstance(t, ast.Name) and t.id in returned for t in targets):
                hits |= _dict_literal_fabrications(node.value)
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
    handlers in ten modules. AUTH-SEND-DENIAL-NOTICE-STATUS-LITERAL added
    `status='notice_sent'` plus detector form (c), closing `auth.SendDenialNoticeWorker`'s two
    success paths — the L0 RN-395 denial one included. FAB-PROGRAMA-NOW-TIMESTAMPS added detector
    form (d) — ANY key mapped to the placeholder string `"now"` — closing `programa.check_consent`'s
    `consent_verified_at` and `programa.enroll_beneficiario`'s `data_enrollment`. The baseline is
    EMPTY, so ANY hit in ANY domain worker module fails here."""
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


def test_fabricated_fact_detector_sees_a_local_bound_dict_returned_through_a_call() -> None:
    """Form (c) SELF-TEST (AUTH-SEND-DENIAL-NOTICE-STATUS-LITERAL): the widening is real.

    `_fabricated_fact_hits` used to look only at `return {<literal>}`. That is exactly the shape
    `auth.SendDenialNoticeWorker.execute` does NOT use on its L0 denial branch, which builds a
    local `notice` dict and returns it through `redact_phi_vars(...)`. This pins the detector's
    new reach on a synthetic module, so the capability cannot rot silently even if the production
    code is later restructured: forms (a)/(b) alone must MISS it, and the shipped detector must
    catch it.
    """
    source = (
        "def handler(process_vars):\n"
        "    notice = {'status': 'notice_sent', 'notice_type': 'denial'}\n"
        "    return redact_phi_vars(notice)\n"
    )
    tree = ast.parse(source)

    returned_literals = {
        hit
        for node in ast.walk(tree)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
        for hit in _dict_literal_fabrications(node.value)
    }
    assert returned_literals == set(), "forms (a)/(b) are supposed to be blind to this shape"

    assert _fabricated_fact_hits(tree) == {"status='notice_sent'"}, (
        "detector form (c) must catch a fabricated status in a dict literal bound to a local that "
        "the function returns (directly or through a call) — without it the RN-395 denial path is "
        "unfenced while the fence looks green"
    )


def test_auth_send_denial_notice_omits_status_on_both_success_paths() -> None:
    """AUTH-SEND-DENIAL-NOTICE-STATUS-LITERAL LANDED: the transmission claim is gone.

    `SendDenialNoticeWorker` is a SYNC `WorkerBase.execute` with no Kafka seam and no channel of
    any kind — the real secure channel to the prestador is Phase 1, as its own docstring says. It
    nevertheless returned `"status": "notice_sent"` on BOTH success paths. Neither path writes
    `status` any more; the ONLY `status` this worker still emits is the guard record
    `blocked_by_guard`, a true fact about its own refusal.

    Pinned structurally (AST over the shipped module), so it also fails if someone re-introduces
    the literal in the local-bound shape the fence learned to see.
    """
    path = _domain_worker_modules()["auth"]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert "status='notice_sent'" not in _fabricated_fact_hits(tree)

    worker_class = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "SendDenialNoticeWorker"
    )
    status_values: set[object] = set()
    for node in ast.walk(worker_class):
        if not isinstance(node, ast.Dict):
            continue
        for key_node, val_node in zip(node.keys, node.values, strict=True):
            if (
                isinstance(key_node, ast.Constant)
                and key_node.value == "status"
                and isinstance(val_node, ast.Constant)
            ):
                status_values.add(val_node.value)
    assert status_values == {"blocked_by_guard"}, (
        "SendDenialNoticeWorker may emit exactly ONE status literal — the guard refusal record. "
        f"Found: {sorted(map(str, status_values))}"
    )
