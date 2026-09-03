"""Unit tests for maezo.platform.validation.phi_completeness — the PHI-completeness fence.

The fence answers audit gap DU-07 (report D8, gateway `gvr-d08`): the platform's
PHI redaction is name-anchored on twelve frozen names inherited from the donor,
and nothing proved those names cover the process variables the live BPMN/DMN
actually declare. Four groups of tests carry the load:

1. **Non-vacuity.** The sweep must find real names in real files. If the
   extractor silently broke, every bucket would empty and a fence over an empty
   universe is green by construction — the failure mode this file exists to
   make impossible. Every declared surface must fire against the live `spec/`,
   and the per-file `VARIAVEIS DE ENTRADA` rolls are pinned name-by-name.
2. **The three buckets, pinned.** `LISTED`/`SHAPE_SUSPECT`/`CLEAN` counts, the
   occurrence count and the full `SHAPE_SUSPECT` membership are pinned against
   `spec/`, so a new PHI-shaped variable in any artifact is a red build.
3. **The fence bites.** A synthetic BPMN introducing a `cns` variable turns the
   fence red; the disposition table cannot acquire a ratified-looking row; and
   the `zona="PHI"` annotation rule is independently exercised.
4. **The unwired pin.** Like PR-1's `perspective.py`, the module is measured
   before it is enforced. `test_fence_is_not_wired_into_the_cli_yet` records
   that as a deliberate state, not an oversight.

Two groups were added after the first adversarial review, because the two things
it broke were exactly the two this file had asserted without measuring:

5. **`TestClassificationRecall`** runs the battery the classifier FAILED —
   `dt_nasc`, `cartao_sus` and `logradouro` passed CLEAN end to end, as did the
   plural evasions `laudos`/`resumos`/`justificativas` — against a copy of the
   LIVE `SP-OP-AUTH-001` BPMN, and pins the five names that must stay clean and
   the named residue that is still uncaught.
6. **`TestStructuralFreeTextSignal`** covers the classification arm that never
   reads the name, and `TestDispositionTable` now pins the `DRAFT/verify (DPO)`
   invariant by LITERAL: the two guards that carried it before both compared a
   status against the `DRAFT_VERIFY` symbol, so a one-line edit to that constant
   rendered every row as ratified with all 68 tests green.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest

from maezo.gateway.pseudonymizer import PHI_FIELDS
from maezo.platform.validation import phi_completeness as fence
from maezo.platform.validation.phi_completeness import (
    ATTESTED_NOWHERE,
    CLEAN,
    DISPOSITIONS,
    LISTED,
    PHI_LISTED_NAMES,
    SHAPE_SUSPECT,
    SHAPE_TOKENS,
    SURFACES,
    Disposition,
    Sweep,
    VarRef,
    annotated_phi_fields,
    blank_entities,
    classify,
    collect_xml_refs,
    collect_xml_refs_from_source,
    collect_yaml_refs,
    declared_input_free_text_names,
    declared_input_names,
    free_text_form_field_offsets,
    juel_names,
    matches_phi_shape,
    render_buckets,
    segments,
    sweep_processes_root,
)
from maezo.platform.validation.result import Report
from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS

_REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/unit/platform -> repo root
_SPEC_PROCESSES = _REPO_ROOT / "spec" / "processes"
_BPMN_DIR = _SPEC_PROCESSES / "bpmn"

_BPMN_WRAPPER = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" \
xmlns:camunda="http://camunda.org/schema/1.0/bpmn" id="Definitions_Fixture">
  <bpmn:process id="Process_Fixture" isExecutable="true">
    {fragment}
  </bpmn:process>
</bpmn:definitions>
"""

_DMN_WRAPPER = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="Definitions_Fixture" \
name="fixture" namespace="http://maezo.health/dmn/fixture">
  <decision id="d_fixture" name="Fixture">
    <decisionTable id="dt_fixture" hitPolicy="FIRST">
      {fragment}
    </decisionTable>
  </decision>
</definitions>
"""


@pytest.fixture(scope="module")
def live_sweep() -> Sweep:
    """The sweep of the real `spec/processes` tree, shared by the pinning tests."""
    report = Report()
    sweep = sweep_processes_root(_SPEC_PROCESSES, report)
    assert report.ok, [finding.render() for finding in report.findings]
    return sweep


def _bpmn(tmp_path: Path, fragment: str, name: str = "fixture.bpmn") -> Path:
    path = tmp_path / name
    path.write_text(_BPMN_WRAPPER.format(fragment=fragment), encoding="utf-8")
    return path


def _dmn(tmp_path: Path, fragment: str, name: str = "fixture.dmn") -> Path:
    path = tmp_path / name
    path.write_text(_DMN_WRAPPER.format(fragment=fragment), encoding="utf-8")
    return path


def _names(refs: list[VarRef], surface: str) -> set[str]:
    return {ref.name for ref in refs if ref.surface == surface}


# ---------------------------------------------------------------------------
# 1. Non-vacuity — the sweep must find real things in real files
# ---------------------------------------------------------------------------


class TestNonVacuity:
    def test_the_sweep_reads_the_whole_corpus(self, live_sweep: Sweep) -> None:
        """16 BPMN + 62 DMN + 8 YAML must all have been opened."""
        touched = {ref.path for ref in live_sweep.refs}
        assert len({p for p in touched if p.suffix == ".bpmn"}) == 16
        assert len({p for p in touched if p.suffix == ".dmn"}) == 62

    def test_every_declared_surface_fires_on_the_live_spec(self, live_sweep: Sweep) -> None:
        """A dead extraction path must not masquerade as coverage."""
        fired = {ref.surface for ref in live_sweep.refs}
        assert fired == set(SURFACES), f"declared but never fired: {set(SURFACES) - fired}"

    def test_the_universe_is_large_enough_to_be_a_universe(self, live_sweep: Sweep) -> None:
        """A sweep that collapsed to a handful of names would make the fence vacuous."""
        assert len(live_sweep.names) >= 300
        assert len(live_sweep.refs) >= 1500

    def test_at_least_one_shape_suspect_exists(self, live_sweep: Sweep) -> None:
        """The whole point of DU-07: the twelve listed names are NOT the whole story."""
        assert live_sweep.by_bucket()[SHAPE_SUSPECT], "no shape-suspect names — heuristic is inert"

    def test_six_of_the_eight_listed_process_vars_are_found_in_the_spec(self, live_sweep: Sweep) -> None:
        """DU-07's other half, measured: the frozen set is WIDER than the specs.

        `laudo` and `diagnostico` (phi_vars.py:60-61) are declared by NO spec
        artifact — they are donor inheritance the 16 BPMN never use — and none
        of `PHI_FIELDS` (`cpf`/`nome`/`telefone`/`email`) appears either, which
        is the gateway boundary working as designed (a process variable carries
        `beneficiario_pseudo_id`, never a CPF). This is pinned rather than
        asserted as `>= 8` because forcing that number would mean inventing
        coverage the corpus does not have.

        Unused is NOT dead, and this test is not an argument for pruning them.
        `phi_vars.py:48-51` says the set is "kept aligned with the donor's
        `PHI_PROCESS_VARS` so the invariant's coverage does not drift between
        codebases", and `:24` names `laudo`/`diagnostico` among the clinical
        content it exists to cover: removing them would CREATE the drift that
        docstring forbids. They cost nothing (redacting a key that never appears
        is a no-op) and they are pre-positioned — the day a BPMN declares
        `laudo`, it is born covered.
        """
        found = PHI_LISTED_NAMES & set(live_sweep.names)
        assert found == {
            "cid10_referencia",
            "fundamentacao_dut",
            "justificativa_clinica",
            "matricula_beneficiario",
            "notas_resolucao",
            "resumo_contexto",
        }
        assert set(PHI_PROCESS_VARS) - found == {"laudo", "diagnostico"}
        assert not (PHI_FIELDS & set(live_sweep.names))

    def test_the_declared_input_roll_is_pinned_per_file(self, live_sweep: Sweep) -> None:
        """The prose roll can only UNDER-collect, so its yield is pinned per file.

        These are the variables the CALLER sets at `start_process_idempotent`
        time — the highest-PHI-risk surface in the corpus, and the only one that
        names `matricula_beneficiario`, `resumo_contexto` and the bare `cid10`.
        A prose edit that shortens a roll changes these numbers.
        """
        per_file: dict[str, int] = {}
        for ref in live_sweep.refs:
            if ref.surface == "bpmn_declared_input":
                per_file[ref.path.name] = per_file.get(ref.path.name, 0) + 1
        assert per_file == {
            "SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn": 14,
            "SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn": 10,
            "SP-OP-AUTH-001_Autorizacao_Previa.bpmn": 10,
            "SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn": 14,
            "SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn": 19,
            "SP-OP-CRED-001_Descredenciamento.bpmn": 18,
            "SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn": 10,
            "SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn": 15,
            "SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn": 14,
            "SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn": 6,
            "SP-OP-NIP-001_Resposta_NIP.bpmn": 12,
            "SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn": 15,
            "SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn": 11,
            "SP-OP-RECURSO-001_Recurso_Glosa.bpmn": 19,
            "SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn": 21,
        }
        # SP-OP-ANS-CRON-001 is timer-started and declares no roll — its absence
        # from the table above is the fact, not an omission.
        assert "SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn" not in per_file

    def test_the_auth_roll_names_the_boundary_case(self, live_sweep: Sweep) -> None:
        """`cid10` at AUTH-001:59 is OQ-11's boundary case, found by the fence."""
        auth = [
            ref
            for ref in live_sweep.refs
            if ref.name == "cid10" and ref.path.name.startswith("SP-OP-AUTH-001")
        ]
        assert [(ref.line, ref.surface) for ref in auth] == [(59, "bpmn_declared_input")]


# ---------------------------------------------------------------------------
# 2. The three buckets, pinned
# ---------------------------------------------------------------------------


class CorpusDelta(NamedTuple):
    """One explained move of the corpus-wide name/occurrence pins below.

    `name_delta` is +1/-1 when NAME entered or left `live_sweep.names` entirely
    (zero occurrences left anywhere in `spec/`), 0 when the name stays but its
    occurrence count shifts. `occurrence_delta` is the net change in
    `live_sweep.refs` attributable to this name. The two pinned-count tests
    below SUM this log against the baseline this fence measured when GAP-DU-07
    first landed (commit `26724a8`: 329 names / 1616 occurrences) and assert
    the arithmetic lands exactly on the literal pins — so a future pin edit
    that is not accompanied by a matching logged entry (or whose deltas do not
    add up) fails loudly here instead of landing as a silent two-line diff.
    """

    date: str
    pr: str
    name: str
    name_delta: int
    occurrence_delta: int
    reason: str


#: Baseline this log's deltas are summed against — the counts `26724a8` measured,
#: before main's onda-0 train (#270/#275/#271/#272/#276/#277) merged into this branch.
_BASELINE_NAMES = 329
_BASELINE_OCCURRENCES = 1616

CORPUS_DELTA_LOG: tuple[CorpusDelta, ...] = (
    CorpusDelta(
        date="2026-09-03",
        pr="#276",
        name="tuss_codes",
        name_delta=-1,
        occurrence_delta=-1,
        reason=(
            "fix/dmn-higiene-inputs-mortos (GAP-PERSP-DMN-DEAD-INPUTS, docs/review-queue.md:636) "
            "removed the `tuss_codes` input column from unbundling_partial_bundles.dmn: dead on "
            "BOTH ends — every rule read it as `-` and the worker stopped sending it at T1.5 "
            "(`_SCORING_INPUT_KEYS`, src/maezo/tools/workers/fraude.py). The declaration was the "
            "name's only occurrence anywhere in spec/, so it leaves the corpus entirely."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#276",
        name="encounter_class",
        name_delta=0,
        occurrence_delta=-1,
        reason=(
            "same PR, docs/review-queue.md:635 — removed the equally-dead `encounter_class` "
            "input column from frequency_zscore_threshold.dmn (also `-` on every rule, read by "
            "no output expression). The name stays in the corpus: "
            "upcoding_complexity_ceiling.dmn still declares and genuinely reads it "
            "(docs/review-queue.md:637). Only the one dead declaration's occurrence is gone."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#271",
        name="calculo",
        name_delta=0,
        occurrence_delta=3,
        reason=(
            "fix/reembolso-consome-dmn (GAP F-1 / ADR-0012) added camunda:inputParameter "
            "bindings on ST_CalculateAmount in SP-OP-REEMBOLSO-001 that read "
            "`${calculo.valor_calculado_tabela_cents}` etc. off the DMN result map — three new "
            "occurrences of an already-corpus name, no new PHI-shaped surface."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#271",
        name="valor_calculado_tabela_cents",
        name_delta=0,
        occurrence_delta=2,
        reason="same PR/task as `calculo` above — the inputParameter's own name, already a DMN output.",
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#271",
        name="multiplo_tabela_aplicado",
        name_delta=0,
        occurrence_delta=2,
        reason="same PR/task as `calculo` above — flattened from the same DMN result map.",
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#271",
        name="fonte_tabela",
        name_delta=0,
        occurrence_delta=2,
        reason="same PR/task as `calculo` above — flattened from the same DMN result map.",
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#295",
        name="prestador_id",
        name_delta=0,
        occurrence_delta=1,
        reason=(
            "fix/coreografia-xproc-handoff-adequacao-cred (PERSP-ADEQ-CRED-HANDOFF) added "
            "`prestador_id` to SP-OP-ADEQUACAO-001's `VARIAVEIS DE ENTRADA` declared-input roll "
            "(the candidate provider ST_StartCredenciamentoL3 requires to hand off to "
            "SP-OP-CRED-001; fail-closed without it). Already a corpus name via "
            "SP-OP-CRED-001_Descredenciamento.bpmn's own roll, so the name does not enter — one "
            "new occurrence in ADEQUACAO's roll (12->14 per-file). Provider cadastral identifier, "
            "not beneficiary PHI: absent from PHI_PROCESS_VARS (ADR-0006 'PHI em duas zonas', "
            "phi_vars.py:52-63 — clinical/beneficiary names only) and classified CLEAN by the "
            "fence's own shape heuristic, so no LISTED/SHAPE_SUSPECT membership moves."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#295",
        name="tipo_prestador",
        name_delta=0,
        occurrence_delta=1,
        reason=(
            "same PR/task as `prestador_id` above — `tipo_prestador` added to the same "
            "ADEQUACAO roll (carried in the handoff payload when known). Already a corpus name "
            "via SP-OP-CRED-001's own roll; one new occurrence, provider cadastral data (not in "
            "PHI_PROCESS_VARS, ADR-0006), CLEAN bucket, no LISTED/SHAPE_SUSPECT movement."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#288",
        name="data_ciencia_glosa",
        name_delta=-1,
        occurrence_delta=-1,
        reason=(
            "feat/perspectiva-operadora-recurso (ADR-0040) removed the appellant-side fallback "
            "anchor from SP-OP-RECURSO-001's SLA computation: the prestador-declared date was a "
            "recorrente-perspective clock and the payer's SLA anchor is exclusively "
            "`data_recebimento_recurso_iso` (when the OPERADORA received the appeal). Its only "
            "occurrence anywhere in spec/ was this one declaration, so the name leaves the corpus."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#288",
        name="loop_counter",
        name_delta=-1,
        occurrence_delta=-4,
        reason=(
            "same PR — `loop_counter` tracked iterations of `ICE_AguardarResposta`/"
            "`GW_LoopLimite`, the recorrente's wait-for-the-operadora's-answer loop. That whole "
            "16-element appellant branch was deleted with no shim (ADR-0040 §2), so all 4 "
            "occurrences of the loop's own counter variable leave the corpus with it."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#288",
        name="resposta_operadora",
        name_delta=-1,
        occurrence_delta=-2,
        reason=(
            "same PR — `resposta_operadora` was the recorrente-side variable naming the payer's "
            "answer as an inbound fact the appellant tracks (`ST_TrackStatus`/`msg.recurso."
            "resposta_recebida`, also deleted). The payer-perspective rewrite emits its own "
            "answer via `ST_Comunicar*` instead of receiving/tracking one; both occurrences leave "
            "the corpus with the deleted branch."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#288",
        name="data_ciencia_alegada_prestador",
        name_delta=1,
        occurrence_delta=1,
        reason=(
            "same PR — replaces `data_ciencia_glosa` as the (optional, non-authoritative) record "
            "of the date the prestador ALLEGES in the appeal, named so it cannot be mistaken for "
            "the payer's own SLA anchor (BPMN documentation at ST_ValidarRecurso: 'aquele relogio "
            "e do recorrente, e o da operadora comeca no recebimento'). Provider/appeal metadata, "
            "not beneficiary PHI — absent from PHI_PROCESS_VARS (ADR-0006) and CLEAN by the "
            "fence's shape heuristic; one declaration, no LISTED/SHAPE_SUSPECT movement."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#288",
        name="fonte_valor",
        name_delta=1,
        occurrence_delta=2,
        reason=(
            "same PR — new `camunda:inputParameter` on the two PAGTO handoff service tasks "
            "(`ST_HandoffPagamentoRecurso`, `ST_HandoffPagamentoParcial`), both pinned to the "
            "literal `deferido` so SP-OP-PAGTO-001 always sees where the reverted value came "
            "from. Two declarations, a financial-flow tag (not PHI), CLEAN bucket."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#288",
        name="tipo_comunicacao",
        name_delta=1,
        occurrence_delta=5,
        reason=(
            "same PR — new `camunda:inputParameter` on the five `ST_Comunicar*` service tasks "
            "(deferimento, deferimento_parcial, indeferimento x2, inadmissibilidade), each pinned "
            "to a literal tagging what is being communicated to the prestador. Five declarations, "
            "a communication-type tag (not PHI), CLEAN bucket."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#288",
        name="decisao_recurso",
        name_delta=0,
        occurrence_delta=3,
        reason=(
            "same PR — `GW_DecisaoRecurso`'s branching widened from the recorrente's {RECORRER, "
            "NAO_RECORRER x2, SOLICITAR_INFO, ESCALAR_AUDITOR} (4 conditionExpressions) to the "
            "payer's {DEFERIR, DEFERIR_PARCIAL, INDEFERIR x2 (incl. inadmissivel), "
            "SOLICITAR_INFO, ESCALAR_AUDITOR} (6 conditionExpressions), plus one new "
            '`camunda:outputParameter name="decisao_recurso">${""}` fail-closed initializer '
            "(bpmn:145) so an absent human decision reaches the dedicated error terminal "
            "`End_ErrRecursoDecisaoInvalida` instead of stalling the User Task on an engine "
            "'Unknown property' HTTP 500. 4 -> 7 occurrences, name stays in the corpus."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#288",
        name="decisao_auditor_recurso",
        name_delta=0,
        occurrence_delta=3,
        reason=(
            "same PR — same fail-closed-initializer mechanism on `GW_MeritoAuditor` "
            "(bpmn:146): one new `camunda:outputParameter` plus 3 conditionExpressions "
            "(DEFERIR/DEFERIR_PARCIAL/INDEFERIR) replacing the recorrente's single "
            "`ACEITAR_GLOSA` condition. 1 -> 4 occurrences, name stays in the corpus."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#288",
        name="event_desfecho",
        name_delta=0,
        occurrence_delta=-1,
        reason=(
            "same PR — the 6 recorrente-perspective outcome tasks (`deferido`, "
            "`parcialmente_deferido`, `indeferido`, `nao_interposto_humano` x2, `inadmissivel`) "
            "became the 5 payer-perspective `ST_Comunicar*` tasks (`deferido_humano`, "
            "`deferido_parcial_humano`, `indeferido_humano` x2, `inadmissivel_humano`) — one "
            "fewer terminal-communication task in the rebuilt flow. 6 -> 5 in RECURSO; corpus-wide "
            "60 -> 59, name stays (still declared by every other process's own event tasks)."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#288",
        name="event_fase",
        name_delta=0,
        occurrence_delta=-1,
        reason=(
            "same PR — RECURSO declared this on 2 tasks before (the SLA-risk notifier's "
            "`analise` and the P30D-ceiling escalator's `prazo_max`); the rebuilt escalator's "
            "input parameter for the prazo_max path was renamed to the more specific "
            "`event_topic_breach` (`recurso.py::make_escalate_ans_timeout_handler`, "
            "`_ESCALATE_ANS_TIMEOUT_FASE` is now a Python-side constant, not a BPMN declaration), "
            "so only the `analise` declaration still uses the generic `event_fase` name. RECURSO "
            "2 -> 1; corpus-wide 4 -> 3 (ESCALATION-001's own 2 untouched), name stays."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#288",
        name="event_payload_vars",
        name_delta=0,
        occurrence_delta=-2,
        reason=(
            "same PR — same rename as `event_fase` above: the prazo_max escalation task's "
            "`event_payload_vars`/`event_topic` input parameters were dropped from the BPMN in "
            "favour of the worker building the sla_breached payload itself from "
            "`EscalateAnsTimeoutInput` and reading `event_topic_breach` (a distinct variable "
            "name, not counted here). RECURSO 10 -> 8 for each of the two names; corpus-wide "
            "126 -> 124 for each, names stay (declared by every other process's own event tasks)."
        ),
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#288",
        name="event_topic",
        name_delta=0,
        occurrence_delta=-2,
        reason="same PR/task/mechanism as `event_payload_vars` immediately above.",
    ),
    CorpusDelta(
        date="2026-09-03",
        pr="#288",
        name="data_vencimento",
        name_delta=0,
        occurrence_delta=1,
        reason=(
            "same PR — RECURSO's own `VARIAVEIS DE ENTRADA` roll gains this name "
            "(ST_ValidarRecurso documentation, bpmn:90): the new PAGTO handoff (invariant "
            "I-PAGTO-1) requires the origin conta's due date and the worker refuses a blank "
            "value (`RecursoHandoffPagamentoInvalidoError`, see probe D in the gatekeeper "
            "report). Already a corpus name via SP-OP-PAGTO-001's own declared-input roll "
            "(`PAGTO-001:69`), so the name does not enter — one new occurrence, CLEAN bucket, "
            "no LISTED/SHAPE_SUSPECT movement."
        ),
    ),
)


class TestBuckets:
    def test_bucket_counts_are_pinned(self, live_sweep: Sweep) -> None:
        buckets = live_sweep.by_bucket()
        expected_names = _BASELINE_NAMES + sum(delta.name_delta for delta in CORPUS_DELTA_LOG)
        # LISTED and SHAPE_SUSPECT are pinned independently below (exact membership, with
        # provenance); CLEAN is everything else, cross-checked against CORPUS_DELTA_LOG.
        expected_clean = expected_names - 6 - 9
        assert expected_clean == 313
        assert {key: len(value) for key, value in buckets.items()} == {
            LISTED: 6,
            SHAPE_SUSPECT: 9,
            CLEAN: expected_clean,
        }
        assert sum(len(value) for value in buckets.values()) == len(live_sweep.names)

    def test_the_occurrence_count_is_pinned(self, live_sweep: Sweep) -> None:
        """328 names over 1627 occurrences — the number the ledger row quotes.

        Pinned because the first ledger draft quoted 1637, a figure no state of
        this branch produced. A number reported to a reader and reproducible by
        nobody is worse than no number. The two literals are re-derived from
        `_BASELINE_NAMES`/`_BASELINE_OCCURRENCES` plus `CORPUS_DELTA_LOG` so a
        future move must come with a logged, reasoned entry: an unexplained
        edit to the bare literal fails the cross-check chain below.
        """
        expected_names = _BASELINE_NAMES + sum(delta.name_delta for delta in CORPUS_DELTA_LOG)
        expected_refs = _BASELINE_OCCURRENCES + sum(delta.occurrence_delta for delta in CORPUS_DELTA_LOG)
        assert len(live_sweep.names) == expected_names == 328
        assert len(live_sweep.refs) == expected_refs == 1627

    def test_the_corpus_delta_log_names_only_names_the_live_sweep_actually_moved(
        self, live_sweep: Sweep
    ) -> None:
        """The log is evidence, not narration: every entry must name a real, current move.

        Guards the log itself against rotting the way the pins it explains
        once did. Three directions, named so a future thinning of this check
        reads as a deliberate choice, not an oversight:
          - `name_delta >= 0` (added, or present with a shifted occurrence
            count) must still be a name the live sweep actually has — an
            entry for a name that has since left the corpus a SECOND time,
            or that was never in it, would be undetectable prose.
          - `name_delta < 0` (claimed to have LEFT the corpus) is checked the
            other way: the name must be ABSENT — an entry that claims a
            removal for a name still present would let a future edit that
            never happened stand unaudited.
          - every entry must move at least one of the two counters — an
            inert `name_delta=0, occurrence_delta=0` row would pass no
            matter what PR or reason it named, since it contributes nothing
            either pinned-count test can observe.
        (A rename — one name leaves as another, same-PR, replacement enters —
        nets to `name_delta=0` in aggregate with no per-entry way to tell it
        apart from "no move at all"; CORPUS_DELTA_LOG does not model that
        case today, a documented limitation, not a bug this test papers over.)
        """
        for delta in CORPUS_DELTA_LOG:
            if delta.name_delta >= 0:
                assert delta.name in live_sweep.names, (
                    f"CORPUS_DELTA_LOG: {delta.name!r} ({delta.pr}) is not in the live corpus"
                )
            else:
                assert delta.name not in live_sweep.names, (
                    f"CORPUS_DELTA_LOG: {delta.name!r} ({delta.pr}) is logged as having LEFT the "
                    "corpus (name_delta < 0) but is still present in the live sweep"
                )
        pr_pattern = re.compile(r"^#\d+$")
        for delta in CORPUS_DELTA_LOG:
            assert pr_pattern.match(delta.pr), f"CORPUS_DELTA_LOG: {delta.pr!r} is not a `#NNN` PR reference"
            assert len(delta.reason) > 40, f"CORPUS_DELTA_LOG: {delta.name!r} reason is too thin to audit"
            assert delta.name_delta != 0 or delta.occurrence_delta != 0, (
                f"CORPUS_DELTA_LOG: {delta.name!r} ({delta.pr}) moves neither counter — an inert "
                "entry is unfalsifiable narration, not an audited delta"
            )

    def test_the_shape_suspect_list_is_pinned_exactly(self, live_sweep: Sweep) -> None:
        """The DPO questions. Each one has a `DISPOSITIONS` entry with evidence."""
        assert live_sweep.by_bucket()[SHAPE_SUSPECT] == (
            "auditor_id",
            "cid10",
            "detalhes_requisicao",
            "diagnostico_oncologico_confirmado",
            "diagnostico_tea_ou_neurodesenvolvimento",
            "exames_convencionais_inconclusivos",
            "fundamentacao_legal",
            "has_cid10_codes",
            "sintoma_codigo",
        )

    def test_every_shape_suspect_provenance_is_pinned(self, live_sweep: Sweep) -> None:
        """`file:line` for every DPO question — the deliverable DU-07 asks for."""
        actual = {
            name: sorted(f"{ref.path.name}:{ref.line} ({ref.surface})" for ref in live_sweep.provenance(name))
            for name in live_sweep.by_bucket()[SHAPE_SUSPECT]
        }
        assert actual == {
            "auditor_id": [
                "SP-OP-AUTH-001_Autorizacao_Previa.bpmn:324 (bpmn_form_field)",
                "SP-OP-AUTH-001_Autorizacao_Previa.bpmn:409 (bpmn_form_field)",
                "SP-OP-AUTH-001_Autorizacao_Previa.bpmn:547 (bpmn_form_field)",
            ],
            "cid10": [
                "SP-OP-AUTH-001_Autorizacao_Previa.bpmn:59 (bpmn_declared_input)",
                "SP-OP-RECURSO-001_Recurso_Glosa.bpmn:86 (bpmn_declared_input)",
                "SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn:61 (bpmn_declared_input)",
            ],
            "detalhes_requisicao": [
                "SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn:55 (bpmn_declared_input)",
            ],
            "diagnostico_oncologico_confirmado": [
                "dut_criteria_oncologia_pet_ct.dmn:56 (dmn_input_expression)",
            ],
            "diagnostico_tea_ou_neurodesenvolvimento": [
                "dut_criteria_terapias_especiais.dmn:62 (dmn_input_expression)",
            ],
            "exames_convencionais_inconclusivos": [
                "dut_criteria_oncologia_pet_ct.dmn:74 (dmn_input_expression)",
            ],
            "fundamentacao_legal": [
                "SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn:378 (juel_root)",
                "SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn:78 (bpmn_output_parameter)",
            ],
            "has_cid10_codes": [
                "phantom_no_diagnosis.dmn:30 (dmn_input_expression)",
            ],
            "sintoma_codigo": [
                "triage_redflag_adult.dmn:26 (dmn_input_expression)",
                "triage_redflag_gestante.dmn:19 (dmn_input_expression)",
                "triage_redflag_mental_health.dmn:22 (dmn_input_expression)",
                "triage_redflag_pediatric.dmn:19 (dmn_input_expression)",
            ],
        }

    def test_every_pinned_provenance_line_really_carries_the_name(self, live_sweep: Sweep) -> None:
        """Re-read each cited line from disk: a fabricated line number fails here."""
        for name in live_sweep.by_bucket()[SHAPE_SUSPECT]:
            for ref in live_sweep.provenance(name):
                line = ref.path.read_text(encoding="utf-8").splitlines()[ref.line - 1]
                assert name in line, f"{ref.render()} does not contain {name!r}: {line!r}"

    def test_the_live_spec_passes_the_fence(self, live_sweep: Sweep) -> None:
        """Every shape-suspect is dispositioned today, so the gate is green."""
        report = Report()
        fence.check_sweep(live_sweep, report)
        assert report.ok, [finding.render() for finding in report.findings]

    def test_render_buckets_shows_all_three_with_provenance(self, live_sweep: Sweep) -> None:
        rendered = render_buckets(live_sweep)
        assert f"## {LISTED} (6)" in rendered
        assert f"## {SHAPE_SUSPECT} (9)" in rendered
        assert f"## {CLEAN} (313)" in rendered  # see CORPUS_DELTA_LOG — tuss_codes left the corpus (#276)
        assert "SP-OP-AUTH-001_Autorizacao_Previa.bpmn:59" in rendered
        # The LITERAL, not the symbol: counting occurrences of `DRAFT_VERIFY`
        # would stay green after an edit that renamed the constant's VALUE to
        # "RATIFICADO pelo DPO", which is exactly the laundering this asserts
        # against.
        assert rendered.count("DRAFT/verify (DPO)") == 9


# ---------------------------------------------------------------------------
# 3. The fence bites
# ---------------------------------------------------------------------------


class TestTheFenceBites:
    def test_a_new_cns_variable_in_a_bpmn_turns_the_fence_red(self, tmp_path: Path) -> None:
        """The regression this module exists for: a PHI-shaped name added quietly."""
        _bpmn(
            tmp_path,
            '<bpmn:userTask id="UT_A"><bpmn:extensionElements><camunda:formData>'
            '<camunda:formField id="cns_beneficiario" label="Cartao Nacional de Saude" '
            'type="string"/></camunda:formData></bpmn:extensionElements></bpmn:userTask>',
        )
        report = Report()
        sweep = sweep_processes_root(tmp_path, report)
        assert "cns_beneficiario" in sweep.names
        assert sweep.bucket("cns_beneficiario") == SHAPE_SUSPECT

        fence.check_sweep(sweep, report)
        assert not report.ok
        message = "\n".join(finding.message for finding in report.findings)
        assert "cns_beneficiario" in message
        assert "DISPOSITIONS" in message
        assert "never add the name to a PHI set" in message

    def test_the_same_variable_in_a_dmn_input_expression_also_turns_it_red(self, tmp_path: Path) -> None:
        _dmn(
            tmp_path,
            '<input id="in_x" label="CNS"><inputExpression id="ie_x" typeRef="string">'
            "<text>cns_titular</text></inputExpression></input>",
        )
        report = Report()
        sweep = sweep_processes_root(tmp_path, report)
        fence.check_sweep(sweep, report)
        assert not report.ok
        assert any("cns_titular" in finding.message for finding in report.findings)

    def test_and_in_a_yaml_manifest_too(self, tmp_path: Path) -> None:
        (tmp_path / "candidate.yaml").write_text(
            "regras_candidatas:\n  - entradas:\n      nome_mae: '-'\n", encoding="utf-8"
        )
        report = Report()
        sweep = sweep_processes_root(tmp_path, report)
        assert "nome_mae" in sweep.names
        fence.check_sweep(sweep, report)
        assert not report.ok

    def test_a_clean_variable_does_not_turn_it_red(self, tmp_path: Path) -> None:
        _bpmn(tmp_path, '<bpmn:serviceTask id="ST_A" camunda:resultVariable="roteamento"/>')
        report = Report()
        sweep = sweep_processes_root(tmp_path, report)
        assert sweep.bucket("roteamento") == CLEAN
        fence.check_sweep(sweep, report)
        assert report.ok, [finding.render() for finding in report.findings]

    def test_a_dispositioned_name_does_not_turn_it_red(self, tmp_path: Path) -> None:
        """A disposition silences the FENCE, and changes nothing else."""
        _bpmn(tmp_path, '<bpmn:serviceTask id="ST_A" camunda:resultVariable="cid10"/>')
        report = Report()
        sweep = sweep_processes_root(tmp_path, report)
        assert sweep.bucket("cid10") == SHAPE_SUSPECT
        fence.check_sweep(sweep, report)
        assert report.ok
        # ...and the name is still NOT in any PHI set: the disposition records a
        # question, it does not answer it.
        assert "cid10" not in PHI_LISTED_NAMES

    def test_an_unparseable_artifact_is_an_error_not_a_silent_skip(self, tmp_path: Path) -> None:
        (tmp_path / "broken.bpmn").write_text("<bpmn:definitions><oops>", encoding="utf-8")
        report = Report()
        sweep_processes_root(tmp_path, report)
        assert not report.ok
        assert "malformed XML" in report.findings[0].message

    def test_a_malformed_manifest_is_an_error_not_a_silent_skip(self, tmp_path: Path) -> None:
        (tmp_path / "broken.yaml").write_text("entradas:\n  - [unclosed\n", encoding="utf-8")
        report = Report()
        sweep_processes_root(tmp_path, report)
        assert not report.ok
        assert "malformed YAML" in report.findings[0].message


class TestZonaAnnotationRule:
    """The artifact's own `zona="PHI"` declaration, cross-checked independently."""

    def test_the_live_spec_annotations_are_found_and_all_listed(self, live_sweep: Sweep) -> None:
        assert [(ref.path.name, ref.line, ref.name) for ref in live_sweep.annotated] == [
            ("SP-OP-AUTH-001_Autorizacao_Previa.bpmn", 305, "justificativa_clinica"),
            ("SP-OP-AUTH-001_Autorizacao_Previa.bpmn", 390, "justificativa_clinica"),
            ("SP-OP-AUTH-001_Autorizacao_Previa.bpmn", 528, "justificativa_clinica"),
            ("SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn", 159, "notas_resolucao"),
            ("SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn", 272, "notas_resolucao"),
        ]
        assert all(ref.name in PHI_LISTED_NAMES for ref in live_sweep.annotated)

    def test_an_annotated_but_unlisted_field_is_an_error(self, tmp_path: Path) -> None:
        path = _bpmn(
            tmp_path,
            '<bpmn:userTask id="UT_A"><bpmn:extensionElements><camunda:formData>'
            '<camunda:formField id="observacao_do_analista" type="string">'
            '<camunda:validation><camunda:constraint name="required"/></camunda:validation>'
            "<camunda:properties>"
            '<camunda:property name="zona" value="PHI — texto livre humano"/>'
            "</camunda:properties></camunda:formField>"
            "</camunda:formData></bpmn:extensionElements></bpmn:userTask>",
        )
        annotated = annotated_phi_fields(path, path.read_text(encoding="utf-8"))
        assert [ref.name for ref in annotated] == ["observacao_do_analista"]

        report = Report()
        sweep = sweep_processes_root(tmp_path, report)
        fence.check_sweep(sweep, report)
        assert not report.ok
        assert any("contradict each other" in finding.message for finding in report.findings)

    def test_the_self_closing_children_do_not_truncate_the_scan(self, tmp_path: Path) -> None:
        """Regression: a block regex stops at the first `/>` and finds nothing.

        `camunda:value`/`camunda:constraint` are self-closing children that sit
        BEFORE the `zona` property in the real AUTH-001 form, so a naive
        `formField ... /formField` regex reports zero hits on a corpus with five.
        """
        path = _bpmn(
            tmp_path,
            '<bpmn:userTask id="UT_A"><bpmn:extensionElements><camunda:formData>'
            '<camunda:formField id="campo_x" type="enum">'
            '<camunda:value id="A" name="a"/><camunda:value id="B" name="b"/>'
            "<camunda:properties>"
            '<camunda:property name="requiredIf" value="outra == X"/>'
            '<camunda:property name="zona" value="PHI — clinico"/>'
            "</camunda:properties></camunda:formField>"
            "</camunda:formData></bpmn:extensionElements></bpmn:userTask>",
        )
        assert [ref.name for ref in annotated_phi_fields(path, path.read_text(encoding="utf-8"))] == [
            "campo_x"
        ]

    def test_a_non_phi_zona_value_is_not_an_annotation(self, tmp_path: Path) -> None:
        path = _bpmn(
            tmp_path,
            '<bpmn:userTask id="UT_A"><bpmn:extensionElements><camunda:formData>'
            '<camunda:formField id="campo_y" type="string"><camunda:properties>'
            '<camunda:property name="zona" value="Zona Geral — sem PHI"/>'
            "</camunda:properties></camunda:formField>"
            "</camunda:formData></bpmn:extensionElements></bpmn:userTask>",
        )
        assert annotated_phi_fields(path, path.read_text(encoding="utf-8")) == []


class TestDispositionTable:
    def test_every_shape_suspect_is_listed_or_dispositioned(self, live_sweep: Sweep) -> None:
        """The invariant the fence enforces, asserted directly."""
        for name in live_sweep.by_bucket()[SHAPE_SUSPECT]:
            assert name in DISPOSITIONS, f"{name} is a DPO question with no written disposition"

    def test_the_draft_status_literals_are_pinned(self) -> None:
        """The anchor the rest of this class rests on, pinned BY LITERAL.

        The first version of this fence guarded its central promise with two
        assertions that compared a status against the `DRAFT_VERIFY` SYMBOL
        (`item.status == DRAFT_VERIFY`, `rendered.count(DRAFT_VERIFY)`). Both are
        tautologies: editing that one constant to `"RATIFICADO pelo DPO"` made
        all six rows render as ratified with the entire suite green. Nothing
        below is worth anything unless these two strings are what they say.
        """
        assert fence.DRAFT_VERIFY == "DRAFT/verify (DPO)"
        assert fence.DRAFT_STATUS_PREFIX == "DRAFT/verify"
        assert fence.DRAFT_VERIFY.startswith(fence.DRAFT_STATUS_PREFIX)

    def test_every_disposition_is_draft_verify(self) -> None:
        assert all(item.status == "DRAFT/verify (DPO)" for item in DISPOSITIONS.values())

    def test_a_disposition_cannot_claim_ratification(self) -> None:
        """The table has no vocabulary for a decision that belongs to the DPO."""
        with pytest.raises(ValueError, match="DPO act"):
            Disposition(name="x", evidence="e", recommendation="r", status="RATIFICADO")

    @pytest.mark.parametrize(
        "status",
        [
            "RATIFICADO",
            "RATIFICADO pelo DPO",
            "APROVADO (DPO)",
            "draft/verify (DPO)",  # case matters: the literal is the literal
            "verify (DPO)",
            "",
        ],
    )
    def test_a_status_that_is_not_the_draft_literal_is_refused(self, status: str) -> None:
        """Refusal does not depend on `DRAFT_VERIFY` still holding a draft value.

        `__post_init__` checks BOTH `== DRAFT_VERIFY` and
        `startswith(DRAFT_STATUS_PREFIX)`. The second check is the one that
        survives an edit to the first constant, and the next test proves it does.
        """
        with pytest.raises(ValueError, match="DPO act"):
            Disposition(name="x", evidence="e", recommendation="r", status=status)

    def test_a_ratified_status_is_refused_even_if_the_constant_is_edited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exact attack: one line edited, `DRAFT_VERIFY = "RATIFICADO pelo DPO"`.

        With the old single check (`status != DRAFT_VERIFY`) this construction
        succeeded and the table rendered as ratified. The hard-coded prefix does
        not consult the constant, so it still refuses.
        """
        monkeypatch.setattr(fence, "DRAFT_VERIFY", "RATIFICADO pelo DPO")
        with pytest.raises(ValueError, match="DPO act"):
            Disposition(name="x", evidence="e", recommendation="r", status="RATIFICADO pelo DPO")

    def test_the_table_itself_cannot_be_mutated(self) -> None:
        """`DISPOSITIONS` was annotated `Mapping` while being a mutable `dict`."""
        with pytest.raises(TypeError):
            DISPOSITIONS["invented"] = DISPOSITIONS["cid10"]  # type: ignore[index]
        with pytest.raises((TypeError, AttributeError)):
            DISPOSITIONS.pop("cid10")  # type: ignore[attr-defined]

    def test_a_disposition_cannot_be_subclassed_into_compliance(self) -> None:
        """A subclass overriding `__post_init__` would construct any status it liked."""
        with pytest.raises(TypeError, match="may not be subclassed"):

            class Laundered(Disposition):  # pragma: no cover - the class body never runs
                pass

    def test_a_slot_written_behind_the_frozen_dataclass_still_cannot_render(self) -> None:
        """The residual hole, named and then closed at the point of USE.

        `object.__setattr__` writes the slot of a frozen dataclass — CPython
        leaves that door open and no class-level guard shuts it. So the claim is
        not "a row cannot be mutated"; it is "a mutated row cannot RENDER as
        ratified", and `assert_draft_status` is what makes that true.
        """
        item = Disposition(name="x", evidence="e", recommendation="r")
        fence.assert_draft_status(item)  # the control case: a genuine row passes

        object.__setattr__(item, "status", "RATIFICADO pelo DPO")
        assert item.status == "RATIFICADO pelo DPO"  # the write really did land
        with pytest.raises(ValueError, match="records questions, never answers"):
            fence.assert_draft_status(item)

    def test_render_buckets_refuses_to_print_a_ratified_looking_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end: the mutated row reaches the renderer and the renderer stops."""
        laundered = Disposition(name="cid10", evidence="e", recommendation="r")
        object.__setattr__(laundered, "status", "RATIFICADO pelo DPO")
        monkeypatch.setattr(fence, "DISPOSITIONS", {**DISPOSITIONS, "cid10": laundered})

        _bpmn(tmp_path, '<bpmn:serviceTask id="ST_A" camunda:resultVariable="cid10"/>')
        sweep = sweep_processes_root(tmp_path, Report())
        with pytest.raises(ValueError, match="records questions, never answers"):
            render_buckets(sweep)

    def test_the_review_queue_carries_the_draft_literal_on_every_row(self) -> None:
        """The document a DPO actually reads must not drift from the table."""
        queue = (_REPO_ROOT / "docs" / "review-queue.md").read_text(encoding="utf-8")
        rows = [
            line
            for line in queue.splitlines()
            if line.startswith("| `") and any(f"`{name}` (" in line for name in DISPOSITIONS)
        ]
        assert len(rows) == len(DISPOSITIONS), rows
        for row in rows:
            assert "DRAFT/verify (DPO) — pergunta aberta" in row, row

    def test_every_disposition_carries_evidence_and_a_recommendation(self) -> None:
        for item in DISPOSITIONS.values():
            assert re.search(r"\.(bpmn|dmn|py|md|yaml):\d+", item.evidence), item.evidence
            assert len(item.recommendation) > 120, item.name
            assert "RECOMMEND" in item.recommendation or "SAME reading" in item.recommendation

    def test_the_table_has_no_entry_for_a_name_the_corpus_never_declares(self, live_sweep: Sweep) -> None:
        """A disposition for a variable that does not exist is dead paperwork."""
        assert set(DISPOSITIONS) <= set(live_sweep.names)

    def test_the_disposition_table_does_not_live_in_the_codeowned_policy_path(self) -> None:
        """It must not be laundered into the DPO's directory before ratification."""
        privacy = _REPO_ROOT / "spec" / "policies" / "privacy"
        assert not (privacy / "phi-process-vars-disposition.yaml").exists()
        assert sorted(p.name for p in privacy.glob("*.yaml")) == ["phi-business-key-remediation.yaml"]

    def test_the_review_queue_records_the_migration_and_the_questions(self) -> None:
        queue = (_REPO_ROOT / "docs" / "review-queue.md").read_text(encoding="utf-8")
        assert "GAP-DU-07" in queue
        assert "phi_completeness.DISPOSITIONS" in queue
        for name in DISPOSITIONS:
            assert name in queue, f"{name} has no row in docs/review-queue.md"


# ---------------------------------------------------------------------------
# 4. The heuristic and the extraction paths, unit by unit
# ---------------------------------------------------------------------------


class TestShapeHeuristic:
    def test_every_shape_token_cites_a_live_source(self) -> None:
        """Re-read each citation: a rotted quote fails the build.

        Content-anchored, not line-anchored (the ADR-0041 reconciliation fence,
        `tests/unit/docs/test_adr_amendments.py`, applied the same lesson): the
        assertion is that `quote` still appears SOMEWHERE in `source`, not that
        it sits on a specific line. A citation pinned to a line number breaks
        the instant anything ELSE in that file gains or loses a line above the
        cited text — the exact failure `docs/review-queue.md` produced when an
        unrelated edit reflowed rows above :436 and pushed `data_nascimento`
        and `cep` down to :438 without the citation going stale in any sense a
        reader cares about.
        """
        for token in SHAPE_TOKENS:
            if token.source == ATTESTED_NOWHERE:
                assert token.quote == ""
                continue
            path = _REPO_ROOT / token.source
            assert path.is_file(), (
                f"{token.token}: source {token.source!r} is not a file — SHAPE_TOKENS "
                "sources are file paths only, no trailing `:line`"
            )
            text = path.read_text(encoding="utf-8")
            assert token.quote in text, f"{token.token}: {token.source} no longer contains {token.quote!r}"

    def test_the_unattested_tokens_are_named_not_hidden(self) -> None:
        """Pinned exactly, because growing this set quietly is the failure mode.

        Every entry here is a DECLARED extrapolation: the repo's own vocabulary
        does not attest it. They exist because the adversarial battery in
        `TestClassificationRecall` walked straight through the attested tokens —
        `dt_nasc`, `cartao_sus`, `logradouro`, `sintomas_relatados` and the rest
        were all CLEAN. Each carries an inline comment in `SHAPE_TOKENS` giving
        the basis; none is dressed up with a plausible-looking citation.
        """
        assert {t.token for t in SHAPE_TOKENS if t.source == ATTESTED_NOWHERE} == {
            "alergia",
            "anamnese",
            "bairro",
            "biopsia",
            "cartao",
            "carteirinha",
            "etnia",
            "exame",
            "hipotese",
            "historic",
            "logradouro",
            "mae",
            "medicament",
            "municipio",
            "observac",
            "raca",
            "relato",
            "sintoma",
            "sus",
        }

    def test_every_token_declares_a_comparison_rule(self) -> None:
        for token in SHAPE_TOKENS:
            assert token.match in {fence.SEGMENT_MATCH, fence.STEM_MATCH}, token
        assert fence.SEGMENT_TOKENS and fence.STEM_TOKENS

    def test_the_collision_prone_tokens_stay_segment_matched(self) -> None:
        """Named, because as stems they would fire on words the corpus really uses."""
        assert {"sus", "cid", "rg", "cpf", "nome", "cep", "relato"} <= fence.SEGMENT_TOKENS
        # `sus` as a stem would swallow `suspeita`, live in the CLEAN bucket today.
        assert not matches_phi_shape("duplicidade_suspeita")
        assert not matches_phi_shape("indicio_fraude_sinalizado")
        # `cid` as a stem would swallow `cidade`/`cidadao`.
        assert not matches_phi_shape("decidir_rota")
        # `relato` as a stem would swallow `relatorio`.
        assert not matches_phi_shape("relatorio_mensal")

    def test_stem_matching_folds_plural_and_gendered_forms(self) -> None:
        """The one-character evasions the first version of this fence let through."""
        for evasion in ("laudos", "resumos", "justificativas", "diagnosticas", "notas_extras"):
            assert matches_phi_shape(evasion), evasion

    def test_matching_is_on_segments_not_substrings(self) -> None:
        assert matches_phi_shape("nome_mae")
        assert matches_phi_shape("nomeMae")
        assert matches_phi_shape("cpf_titular")
        assert not matches_phi_shape("sobrenome_fantasia")
        assert not matches_phi_shape("decidir_rota")
        assert not matches_phi_shape("renomear")

    def test_trailing_digits_are_stripped_so_cid10_folds_to_cid(self) -> None:
        assert segments("cid10_referencia") == ("cid", "referencia")
        assert matches_phi_shape("cid10")

    def test_camel_case_boundaries_split(self) -> None:
        assert segments("dataNascimentoTitular") == ("data", "nascimento", "titular")
        assert segments("CPFDoTitular") == ("cpf", "do", "titular")

    def test_classify_prefers_listed_over_shape(self) -> None:
        assert classify("justificativa_clinica") == LISTED
        assert classify("justificativa_do_gestor") == SHAPE_SUSPECT
        assert classify("decisao_auditor") == CLEAN

    def test_the_brief_candidates_all_match_the_heuristic(self) -> None:
        """`data_nascimento`, `cns`, `nome_mae`, `endereco`, `cpf_titular`, `cid10`.

        None of them is declared by any artifact today (see
        `test_the_brief_candidates_are_absent_from_the_live_corpus`) — but the
        day one is, the heuristic must catch it. That is the mechanism DU-07
        asked for, tested independently of the corpus.
        """
        for candidate in (
            "data_nascimento",
            "cns",
            "nome_mae",
            "endereco",
            "cpf_titular",
            "cid10",
            "fundamentacao_legal",
        ):
            assert matches_phi_shape(candidate), candidate

    def test_the_brief_candidates_are_absent_from_the_live_corpus(self, live_sweep: Sweep) -> None:
        present = set(live_sweep.names)
        for candidate in ("data_nascimento", "cns", "nome_mae", "endereco", "cpf_titular"):
            assert candidate not in present, f"{candidate} appeared — re-run the bucket pins"


_AUTH_BPMN = _BPMN_DIR / "SP-OP-AUTH-001_Autorizacao_Previa.bpmn"
_ROLL_TAIL = "cid10 (opcional), documentos_refs."

#: The adversarial battery this fence FAILED the first time it was reviewed.
#: `dt_nasc`, `cartao_sus` and `logradouro` were the three named misses; the rest
#: are the extra probes and the plural / one-letter evasions found alongside them.
#: Every one of these is PHI-shaped and must be caught.
ATTACK_MUST_FLAG: tuple[str, ...] = (
    # the 11-name attack table
    "nome_completo_titular",
    "laudo_medico_texto",
    "cns_beneficiario",
    "dt_nasc",
    "cartao_sus",
    "logradouro",
    # the extra probes
    "observacao_clinica",
    "historico_medico",
    "relato_do_paciente",
    "texto_livre_auditor",
    "sintomas_relatados",
    "medicamento_prescrito",
    "alergia_declarada",
    "hipotese_diagnostica",
    "exame_resultado",
    "biopsia",
    "numero_carteirinha",
    "raca_cor",
    "e_mail",
    # plural / one-letter / gendered evasions of names the runtime control keys on
    "diagnostica_confirmado",
    "justificativas",
    "laudos",
    "resumos",
    # the audit report's own candidate list
    "data_nascimento",
    "cns",
    "nome_mae",
    "endereco",
    "cpf_titular",
)

#: The other half of the same battery: names that must NOT move. A heuristic that
#: catches everything catches nothing.
ATTACK_MUST_STAY_CLEAN: tuple[str, ...] = (
    "numero_lote_tiss",
    "prazo_dias",
    "codigo_tuss",
    "valor_cents",
    "cnpj_prestador",
)

#: Measured residue, declared rather than quietly absent: still CLEAN by NAME.
#: `idade_anos` is left out deliberately — see the module's declared limits for
#: the measured cost of adding it.
DECLARED_RESIDUE: tuple[str, ...] = ("descricao_procedimento", "sexo", "gestante", "idade_anos")


def _auth_copy_with_roll_extras(tmp_path: Path, extras: tuple[str, ...]) -> Path:
    """A copy of the LIVE AUTH-001 BPMN with `extras` spliced into its start roll."""
    source = _AUTH_BPMN.read_text(encoding="utf-8")
    assert source.count(_ROLL_TAIL) == 1
    if extras:
        source = source.replace(
            _ROLL_TAIL, "cid10 (opcional), documentos_refs, " + ", ".join(extras) + ".", 1
        )
    path = tmp_path / _AUTH_BPMN.name
    path.write_text(source, encoding="utf-8")
    return path


class TestClassificationRecall:
    """The MEASURED recall of the classifier, asserted name by name.

    "Shape, not content" describes what the heuristic reads; it says nothing
    about how much of the shape it catches. These tests are that number, and
    they run the battery END TO END against a copy of the live AUTH-001 BPMN —
    the same file, the same `VARIAVEIS DE ENTRADA` surface, the same
    `sweep_processes_root` + `check_sweep` path a build takes — not against a
    synthetic fixture that could agree with the parser by accident.
    """

    def test_the_untouched_live_file_is_the_green_control(self, tmp_path: Path) -> None:
        """Without an injection the copy passes: a red control would prove nothing."""
        _auth_copy_with_roll_extras(tmp_path, ())
        report = Report()
        sweep = sweep_processes_root(tmp_path, report)
        fence.check_sweep(sweep, report)
        assert report.ok, [finding.render() for finding in report.findings]

    @pytest.mark.parametrize("name", ATTACK_MUST_FLAG)
    def test_every_phi_shaped_attack_name_is_classified_suspect(self, name: str) -> None:
        assert classify(name) == SHAPE_SUSPECT, f"{name} -> {classify(name)}"

    @pytest.mark.parametrize("name", ATTACK_MUST_STAY_CLEAN)
    def test_every_deliberately_clean_name_stays_clean(self, name: str) -> None:
        assert classify(name) == CLEAN, f"{name} -> {classify(name)}"

    def test_the_whole_attack_battery_reddens_the_live_file(self, tmp_path: Path) -> None:
        """Every should-flag name, injected at once, named in the findings."""
        _auth_copy_with_roll_extras(tmp_path, ATTACK_MUST_FLAG)
        report = Report()
        sweep = sweep_processes_root(tmp_path, report)
        collected = set(sweep.names)
        assert set(ATTACK_MUST_FLAG) <= collected, set(ATTACK_MUST_FLAG) - collected

        fence.check_sweep(sweep, report)
        assert not report.ok
        flagged = {name for name in ATTACK_MUST_FLAG for f in report.findings if f"'{name}'" in f.message}
        assert flagged == set(ATTACK_MUST_FLAG), set(ATTACK_MUST_FLAG) - flagged

    def test_the_clean_battery_leaves_the_live_file_green(self, tmp_path: Path) -> None:
        """The false-positive half, end to end on the same surface."""
        _auth_copy_with_roll_extras(tmp_path, ATTACK_MUST_STAY_CLEAN)
        report = Report()
        sweep = sweep_processes_root(tmp_path, report)
        assert set(ATTACK_MUST_STAY_CLEAN) <= set(sweep.names)
        fence.check_sweep(sweep, report)
        assert report.ok, [finding.render() for finding in report.findings]

    def test_the_named_residue_is_still_uncaught_and_says_so(self, tmp_path: Path) -> None:
        """Declared limit, pinned: these pass CLEAN today and the module says why.

        This is not an aspiration test. It records the measured edge of the
        vocabulary so a reader of `render_buckets` knows what the green means —
        and it goes RED the day someone adds one of these tokens without
        updating the module's declared limits.
        """
        for name in DECLARED_RESIDUE:
            assert classify(name) == CLEAN, name
        _auth_copy_with_roll_extras(tmp_path, DECLARED_RESIDUE)
        report = Report()
        sweep = sweep_processes_root(tmp_path, report)
        fence.check_sweep(sweep, report)
        assert report.ok, [finding.render() for finding in report.findings]


class TestStructuralFreeTextSignal:
    """The one classification input that never reads the name."""

    def test_an_unbounded_string_field_is_suspect_under_any_name(self, tmp_path: Path) -> None:
        """The root fix for a name-anchored heuristic: a rename does not evade it."""
        assert classify("campo_totalmente_neutro") == CLEAN  # no token fires
        _bpmn(
            tmp_path,
            '<bpmn:userTask id="UT_A"><bpmn:extensionElements><camunda:formData>'
            '<camunda:formField id="campo_totalmente_neutro" label="Observacoes" type="string"/>'
            "</camunda:formData></bpmn:extensionElements></bpmn:userTask>",
        )
        report = Report()
        sweep = sweep_processes_root(tmp_path, report)
        assert sweep.free_text_names == {"campo_totalmente_neutro"}
        assert sweep.bucket("campo_totalmente_neutro") == SHAPE_SUSPECT

        fence.check_sweep(sweep, report)
        assert not report.ok
        assert any("declared FREE TEXT by the artifact itself" in f.message for f in report.findings)

    def test_a_closed_value_domain_is_not_free_text(self, tmp_path: Path) -> None:
        _bpmn(
            tmp_path,
            '<bpmn:userTask id="UT_A"><bpmn:extensionElements><camunda:formData>'
            '<camunda:formField id="campo_com_dominio" type="string">'
            '<camunda:value id="A" name="a"/><camunda:value id="B" name="b"/>'
            "</camunda:formField>"
            "</camunda:formData></bpmn:extensionElements></bpmn:userTask>",
        )
        report = Report()
        sweep = sweep_processes_root(tmp_path, report)
        assert sweep.free_text_names == frozenset()
        assert sweep.bucket("campo_com_dominio") == CLEAN
        fence.check_sweep(sweep, report)
        assert report.ok, [finding.render() for finding in report.findings]

    @pytest.mark.parametrize("field_type", ["boolean", "long", "date", "enum"])
    def test_a_type_bounded_field_is_not_free_text(self, tmp_path: Path, field_type: str) -> None:
        path = _bpmn(
            tmp_path,
            '<bpmn:userTask id="UT_A"><bpmn:extensionElements><camunda:formData>'
            f'<camunda:formField id="campo_tipado" type="{field_type}"/>'
            "</camunda:formData></bpmn:extensionElements></bpmn:userTask>",
        )
        assert free_text_form_field_offsets(path.read_text(encoding="utf-8")) == frozenset()

    def test_a_field_with_no_type_at_all_is_read_fail_closed(self, tmp_path: Path) -> None:
        """An undeclared type is Camunda's `string`; guessing the other way would hide it."""
        path = _bpmn(
            tmp_path,
            '<bpmn:userTask id="UT_A"><bpmn:extensionElements><camunda:formData>'
            '<camunda:formField id="campo_sem_tipo"/>'
            "</camunda:formData></bpmn:extensionElements></bpmn:userTask>",
        )
        assert len(free_text_form_field_offsets(path.read_text(encoding="utf-8"))) == 1

    def test_the_live_free_text_fields_are_pinned(self, live_sweep: Sweep) -> None:
        """The whole measured cost of the structural signal on today's corpus."""
        occurrences = [ref for ref in live_sweep.refs if ref.free_text]
        assert len(occurrences) == 14
        assert live_sweep.free_text_names == {
            "auditor_id",
            "cid10_referencia",
            "fundamentacao_dut",
            "justificativa_clinica",
            "notas_resolucao",
        }
        # Four of the five are already covered by the runtime control; `auditor_id`
        # is the single new DPO question the signal costs, and it is dispositioned.
        assert live_sweep.free_text_names - PHI_LISTED_NAMES == {"auditor_id"}
        assert "auditor_id" in DISPOSITIONS

    def test_a_roll_entry_that_annotates_itself_free_text_is_suspect(self) -> None:
        found = declared_input_free_text_names(
            "VARIAVEIS DE ENTRADA: tenant_id, parecer_do_gestor (texto livre, pseudonimizado),\n"
            "canal (whatsapp | web). Contrato completo: docs/x.md."
        )
        assert found == {"parecer_do_gestor"}
        assert classify("parecer_do_gestor") == CLEAN  # no token fires on the name
        assert classify("parecer_do_gestor", free_text=True) == SHAPE_SUSPECT

    @pytest.mark.parametrize("marker", ["texto livre", "TEXTO-LIVRE", "free-text", "narrativa"])
    def test_every_declared_marker_fires(self, marker: str) -> None:
        found = declared_input_free_text_names(f"VARIAVEIS DE ENTRADA: campo_x ({marker}).")
        assert found == {"campo_x"}

    def test_an_ordinary_annotation_is_not_a_free_text_declaration(self) -> None:
        assert (
            declared_input_free_text_names(
                "VARIAVEIS DE ENTRADA: tenant_id, dentro_prazo (bool, pre-resolvido)."
            )
            == frozenset()
        )

    def test_no_roll_entry_declares_itself_free_text_today(self) -> None:
        """A measured ZERO, pinned so this arm cannot rot into a silent truth.

        Same discipline as `test_no_structured_message_payload_surface_exists`:
        the day a roll reads `foo (texto livre)`, the fence sees it, and this
        test is what proves the arm was live while the count was zero.
        """
        declared = {ref.name for ref in _bpmn_declared_refs() if ref.free_text}
        assert declared == set()


class TestMeasuredZeroExtractionLimits:
    """Two fail-OPEN extraction paths, each pinned at its measured zero."""

    def test_no_non_identifier_input_expression_exists_today(self) -> None:
        """A FEEL `<text>paciente.cpf</text>` outside `${...}` would leave the universe."""
        offenders: list[str] = []
        for path in sorted(_SPEC_PROCESSES.rglob("*")):
            if path.suffix not in {".bpmn", ".dmn"}:
                continue
            for event in fence.scan_xml(path.read_text(encoding="utf-8")):
                if not isinstance(event, fence._Text):
                    continue
                if event.parents[-1:] == ("text",) and "inputExpression" in event.parents:
                    candidate = event.text.strip()
                    if candidate and not fence._IDENTIFIER.match(candidate):
                        offenders.append(f"{path.name}: {candidate!r}")
        assert offenders == []

    def test_no_dmn_variable_element_exists_today(self) -> None:
        """`<dmn:variable name="...">` is not a surface, and there is none to miss."""
        found: list[str] = []
        for path in sorted(_SPEC_PROCESSES.rglob("*")):
            if path.suffix not in {".bpmn", ".dmn"}:
                continue
            found += [
                f"{path.name}: {element}"
                for element in re.findall(r"<(?:\w+:)?variable\s[^>]*>", path.read_text(encoding="utf-8"))
            ]
        assert found == []


def _bpmn_declared_refs() -> list[VarRef]:
    refs: list[VarRef] = []
    for path in sorted(_BPMN_DIR.glob("*.bpmn")):
        refs += [ref for ref in collect_xml_refs(path, Report()) if ref.surface == "bpmn_declared_input"]
    return refs


class TestJuelExtraction:
    def test_a_bare_variable(self) -> None:
        assert juel_names("${decisao_auditor}")[0][:2] == ("decisao_auditor", "juel_root")

    def test_a_dotted_path_yields_root_and_segment(self) -> None:
        found = [(name, surface) for name, surface, _ in juel_names("${admissibilidade.roteamento}")]
        assert found == [("admissibilidade", "juel_root"), ("roteamento", "juel_segment")]

    def test_string_literals_never_become_variables(self) -> None:
        found = {name for name, _, _ in juel_names("${decisao_auditor == 'nome'}")}
        assert found == {"decisao_auditor"}

    def test_entities_never_become_variables(self) -> None:
        """`&amp;&amp;` must not yield a phantom `amp` (regression)."""
        found = {name for name, _, _ in juel_names("${a != null &amp;&amp; b != ''}")}
        assert found == {"a", "b"}

    def test_el_literals_and_context_objects_are_not_variables(self) -> None:
        assert juel_names("${true}") == []
        assert juel_names("${empty}") == []
        found = {name for name, _, _ in juel_names("${execution.getVariable('x')}")}
        assert found == set()

    def test_offsets_pin_each_name_to_its_own_line(self, tmp_path: Path) -> None:
        path = _bpmn(
            tmp_path,
            '<bpmn:sequenceFlow id="F"><bpmn:conditionExpression>${primeira_var\n'
            "!= null and segunda_var != null}</bpmn:conditionExpression></bpmn:sequenceFlow>",
        )
        refs = collect_xml_refs(path, Report())
        lines = {ref.name: ref.line for ref in refs if ref.surface == "juel_root"}
        assert lines["primeira_var"] + 1 == lines["segunda_var"]

    def test_blank_entities_preserves_length(self) -> None:
        text = "a &amp; b &lt; c"
        assert len(blank_entities(text)) == len(text)
        assert blank_entities(text) == "a       b      c"

    def test_only_three_entities_are_used_in_the_corpus(self) -> None:
        """The blanking-not-decoding shortcut is exact only while this holds."""
        found: set[str] = set()
        for path in sorted(_SPEC_PROCESSES.rglob("*")):
            if path.suffix in {".bpmn", ".dmn"}:
                found |= set(re.findall(r"&[a-zA-Z#0-9]+;", path.read_text(encoding="utf-8")))
        assert found == {"&amp;", "&gt;", "&lt;"}


class TestXmlSurfaces:
    def test_form_field_id(self, tmp_path: Path) -> None:
        path = _bpmn(
            tmp_path,
            '<bpmn:userTask id="UT"><bpmn:extensionElements><camunda:formData>'
            '<camunda:formField id="campo_um" type="string"/>'
            "</camunda:formData></bpmn:extensionElements></bpmn:userTask>",
        )
        assert _names(collect_xml_refs(path, Report()), "bpmn_form_field") == {"campo_um"}

    def test_input_and_output_parameters(self, tmp_path: Path) -> None:
        path = _bpmn(
            tmp_path,
            '<bpmn:serviceTask id="ST"><bpmn:extensionElements><camunda:inputOutput>'
            '<camunda:inputParameter name="entrada_um">valor</camunda:inputParameter>'
            '<camunda:outputParameter name="saida_um">${x}</camunda:outputParameter>'
            "</camunda:inputOutput></bpmn:extensionElements></bpmn:serviceTask>",
        )
        refs = collect_xml_refs(path, Report())
        assert _names(refs, "bpmn_input_parameter") == {"entrada_um"}
        assert _names(refs, "bpmn_output_parameter") == {"saida_um"}
        assert "x" in _names(refs, "juel_root")

    def test_result_variable(self, tmp_path: Path) -> None:
        path = _bpmn(
            tmp_path,
            '<bpmn:businessRuleTask id="BRT" camunda:decisionRef="d" '
            'camunda:resultVariable="mapa_resultado"/>',
        )
        assert _names(collect_xml_refs(path, Report()), "bpmn_result_variable") == {"mapa_resultado"}

    def test_dmn_input_expression_and_output_name(self, tmp_path: Path) -> None:
        path = _dmn(
            tmp_path,
            '<input id="i"><inputExpression id="ie" typeRef="string">'
            "<text>variavel_lida</text></inputExpression></input>"
            '<output id="o" label="rotulo" name="variavel_escrita" typeRef="string"/>',
        )
        refs = collect_xml_refs(path, Report())
        assert _names(refs, "dmn_input_expression") == {"variavel_lida"}
        assert _names(refs, "dmn_output_name") == {"variavel_escrita"}

    def test_a_non_identifier_input_expression_is_not_taken_as_a_variable(self, tmp_path: Path) -> None:
        path = _dmn(
            tmp_path,
            '<input id="i"><inputExpression id="ie" typeRef="string">'
            "<text>date and time(x)</text></inputExpression></input>",
        )
        assert _names(collect_xml_refs(path, Report()), "dmn_input_expression") == set()

    def test_an_xml_comment_is_not_a_declaration(self, tmp_path: Path) -> None:
        """A comment is prose ABOUT variables, never a declaration OF one."""
        path = _bpmn(tmp_path, "<!-- cpf_do_titular fica fora do escopo -->")
        assert collect_xml_refs(path, Report()) == []

    def test_a_cdata_documentation_roll_is_read(self, tmp_path: Path) -> None:
        path = _bpmn(
            tmp_path,
            "<bpmn:documentation><![CDATA[\nVARIAVEIS DE ENTRADA: tenant_id, campo_dois.\n"
            "Contrato completo: docs/x.md.\n]]></bpmn:documentation>",
        )
        assert _names(collect_xml_refs(path, Report()), "bpmn_declared_input") == {
            "tenant_id",
            "campo_dois",
        }

    def test_the_dmn_output_name_surface_does_not_leak_into_bpmn_output_parameter(
        self, tmp_path: Path
    ) -> None:
        """`output name` and `outputParameter name` are different elements."""
        source = _DMN_WRAPPER.format(fragment='<output id="o" name="saida_dmn" typeRef="string"/>')
        refs = collect_xml_refs_from_source(Path("x.dmn"), source)
        assert _names(refs, "bpmn_output_parameter") == set()
        assert _names(refs, "dmn_output_name") == {"saida_dmn"}


class TestDeclaredInputRoll:
    def test_parenthesised_annotations_are_not_variables(self) -> None:
        found = declared_input_names(
            "VARIAVEIS DE ENTRADA: tenant_id, direcao (credenciamento | descredenciamento),\n"
            "licenca_valida (bool, pre-resolvido).\nContrato completo: docs/x.md."
        )
        assert [name for name, _ in found] == ["tenant_id", "direcao", "licenca_valida"]

    def test_a_multiline_annotation_does_not_truncate_the_roll(self) -> None:
        """Regression: flattening a span's newline welds the next name to prose."""
        found = declared_input_names(
            "VARIAVEIS DE ENTRADA: regiao_saude\n(regiao de saude — cadastral), especialidade "
            "(taxonomia\nTUSS/CBO — cadastral). Contrato completo: docs/x.md."
        )
        assert [name for name, _ in found] == ["regiao_saude", "especialidade"]

    def test_the_last_name_welded_to_the_following_sentence_is_still_taken(self) -> None:
        found = declared_input_names(
            "VARIAVEIS DE ENTRADA: tenant_id, duplicidade_suspeita (bool). Contrato completo: x."
        )
        assert [name for name, _ in found] == ["tenant_id", "duplicidade_suspeita"]

    def test_the_parenthesised_header_form_is_handled(self) -> None:
        """`VARIAVEIS DE ENTRADA (contrato completo: ...):` — the colon inside first."""
        found = declared_input_names(
            "VARIAVEIS DE ENTRADA (contrato completo: docs/x.md):\n  tenant_id, canal.\nSAIDA: y."
        )
        assert [name for name, _ in found] == ["tenant_id", "canal"]

    def test_no_marker_yields_nothing(self) -> None:
        assert declared_input_names("Um texto qualquer com tenant_id dentro.") == []

    def test_the_roll_never_walks_into_a_documentation_path(self) -> None:
        """`docs/processes/...` starts lowercase; the roll must stop before it."""
        found = declared_input_names(
            "VARIAVEIS DE ENTRADA: tenant_id.\nContrato completo:\ndocs/processes/x.md."
        )
        assert [name for name, _ in found] == ["tenant_id"]


class TestYamlManifests:
    def test_entradas_and_saidas_keys_are_variables(self, tmp_path: Path) -> None:
        path = tmp_path / "candidate.yaml"
        path.write_text(
            "regras_candidatas:\n"
            "  - id: r1\n"
            "    entradas:\n"
            "      tipo_procedimento: '\"x\"'\n"
            "      dias_desde_adesao: '< 1'\n"
            "    saidas:\n"
            "      carencia_cumprida: false\n",
            encoding="utf-8",
        )
        refs = collect_yaml_refs(path, Report())
        assert _names(refs, "yaml_manifest_variable") == {
            "tipo_procedimento",
            "dias_desde_adesao",
            "carencia_cumprida",
        }

    def test_keys_outside_the_blocks_are_not_variables(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.yaml"
        path.write_text("status: DRAFT\nrevisor: null\nalvo: x.dmn\n", encoding="utf-8")
        assert collect_yaml_refs(path, Report()) == []

    def test_the_live_manifest_yield_is_pinned_per_file(self, live_sweep: Sweep) -> None:
        """The pin the MODULE deliberately does not carry.

        `phi_completeness.py` never names a shadow-candidate manifest, because
        `tests/unit/spec/test_shadow_candidates_common.py::test_no_src_consumer_of_the_candidate_manifests`
        proves the W4 wave is unwired by asserting that nothing under `src/`
        names one. That proof is worth more than an example filename in a
        comment, so the file-by-file evidence lives here instead.
        """
        per_file: dict[str, int] = {}
        for ref in live_sweep.refs:
            if ref.surface == "yaml_manifest_variable":
                per_file[ref.path.name] = per_file.get(ref.path.name, 0) + 1
        assert per_file == {
            "adequacao-gap-shadow-candidate.yaml": 63,
            "carencia-check-shadow-candidate.yaml": 72,
            "glosa-triage-shadow-candidate.yaml": 35,
            "triage-redflag-gestante-shadow-candidate.yaml": 32,
            "triage-redflag-pediatric-shadow-candidate.yaml": 32,
            "upcoding-complexity-ceiling-shadow-candidate.yaml": 43,
        }
        # `orphans-allowlist.yaml` and `auth-criteria-ratification.yaml` declare
        # no `entradas:`/`saidas:` block, so they contribute nothing — the fact,
        # not an omission.
        assert "orphans-allowlist.yaml" not in per_file
        assert "auth-criteria-ratification.yaml" not in per_file

    def test_line_provenance_is_the_line_the_key_is_on(self, tmp_path: Path) -> None:
        path = tmp_path / "candidate.yaml"
        path.write_text("entradas:\n  primeira: 1\n  segunda: 2\n", encoding="utf-8")
        refs = collect_yaml_refs(path, Report())
        assert {(ref.name, ref.line) for ref in refs} == {("primeira", 2), ("segunda", 3)}


# ---------------------------------------------------------------------------
# 5. The unwired pin
# ---------------------------------------------------------------------------


class TestNotWiredYet:
    def test_fence_is_not_wired_into_the_cli_yet(self) -> None:
        """Deliberate, like PR-1's `perspective.py`: measured before enforced.

        `cli.py` is being edited by another PR, so the wiring is a separate,
        visible commit rather than a conflict. Until then `make
        validate-artifacts` does not call this module.
        """
        cli = (_REPO_ROOT / "src" / "maezo" / "platform" / "validation" / "cli.py").read_text(
            encoding="utf-8"
        )
        assert "phi_completeness" not in cli

    def test_validate_artifacts_stays_green(self) -> None:
        from maezo.platform.validation.cli import validate_artifacts

        assert validate_artifacts(["spec/processes", "spec/policies", "spec/agents"]) == 0

    def test_no_structured_message_payload_surface_exists(self) -> None:
        """The declared limit, measured — so the day it stops being true it fails.

        `bpmn:message` elements carry only `id`/`name`; there are no
        `bpmn:signal` elements and no `camunda:in`/`camunda:out` call-activity
        variable mappings anywhere in the corpus. If one appears, this test goes
        red and the surface has to be added rather than silently missed.
        """
        messages = 0
        for path in sorted(_BPMN_DIR.glob("*.bpmn")):
            source = path.read_text(encoding="utf-8")
            messages += len(re.findall(r"<bpmn:message\s", source))
            assert not re.search(r"<bpmn:signal\b", source), path.name
            assert not re.search(r"<camunda:(in|out)\s", source), path.name
            for element in re.findall(r"<bpmn:message\s[^>]*>", source):
                assert set(re.findall(r"([\w:]+)=", element)) <= {"id", "name"}, element
        assert messages == 21
