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
2. **The three buckets, pinned.** `LISTED`/`SHAPE_SUSPECT`/`CLEAN` counts and
   the full `SHAPE_SUSPECT` membership are pinned against `spec/`, so a new
   PHI-shaped variable in any artifact is a red build.
3. **The fence bites.** A synthetic BPMN introducing a `cns` variable turns the
   fence red; the disposition table cannot acquire a ratified-looking row; and
   the `zona="PHI"` annotation rule is independently exercised.
4. **The unwired pin.** Like PR-1's `perspective.py`, the module is measured
   before it is enforced. `test_fence_is_not_wired_into_the_cli_yet` records
   that as a deliberate state, not an oversight.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from maezo.gateway.pseudonymizer import PHI_FIELDS
from maezo.platform.validation import phi_completeness as fence
from maezo.platform.validation.phi_completeness import (
    ATTESTED_NOWHERE,
    CLEAN,
    DISPOSITIONS,
    DRAFT_VERIFY,
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
    declared_input_names,
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
            "SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn": 12,
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
            "SP-OP-RECURSO-001_Recurso_Glosa.bpmn": 18,
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


class TestBuckets:
    def test_bucket_counts_are_pinned(self, live_sweep: Sweep) -> None:
        buckets = live_sweep.by_bucket()
        assert {key: len(value) for key, value in buckets.items()} == {
            LISTED: 6,
            SHAPE_SUSPECT: 6,
            CLEAN: 317,
        }
        assert sum(len(value) for value in buckets.values()) == len(live_sweep.names)

    def test_the_shape_suspect_list_is_pinned_exactly(self, live_sweep: Sweep) -> None:
        """The DPO questions. Each one has a `DISPOSITIONS` entry with evidence."""
        assert live_sweep.by_bucket()[SHAPE_SUSPECT] == (
            "cid10",
            "detalhes_requisicao",
            "diagnostico_oncologico_confirmado",
            "diagnostico_tea_ou_neurodesenvolvimento",
            "fundamentacao_legal",
            "has_cid10_codes",
        )

    def test_every_shape_suspect_provenance_is_pinned(self, live_sweep: Sweep) -> None:
        """`file:line` for every DPO question — the deliverable DU-07 asks for."""
        actual = {
            name: sorted(f"{ref.path.name}:{ref.line} ({ref.surface})" for ref in live_sweep.provenance(name))
            for name in live_sweep.by_bucket()[SHAPE_SUSPECT]
        }
        assert actual == {
            "cid10": [
                "SP-OP-AUTH-001_Autorizacao_Previa.bpmn:59 (bpmn_declared_input)",
                "SP-OP-RECURSO-001_Recurso_Glosa.bpmn:69 (bpmn_declared_input)",
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
            "fundamentacao_legal": [
                "SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn:378 (juel_root)",
                "SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn:78 (bpmn_output_parameter)",
            ],
            "has_cid10_codes": [
                "phantom_no_diagnosis.dmn:30 (dmn_input_expression)",
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
        assert f"## {SHAPE_SUSPECT} (6)" in rendered
        assert f"## {CLEAN} (317)" in rendered
        assert "SP-OP-AUTH-001_Autorizacao_Previa.bpmn:59" in rendered
        assert rendered.count(DRAFT_VERIFY) == 6


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

    def test_every_disposition_is_draft_verify(self) -> None:
        assert all(item.status == DRAFT_VERIFY for item in DISPOSITIONS.values())

    def test_a_disposition_cannot_claim_ratification(self) -> None:
        """The table has no vocabulary for a decision that belongs to the DPO."""
        with pytest.raises(ValueError, match="DPO act"):
            Disposition(name="x", evidence="e", recommendation="r", status="RATIFICADO")

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
        """Re-read each citation: a rotted `file:line` fails the build."""
        for token in SHAPE_TOKENS:
            if token.source == ATTESTED_NOWHERE:
                assert token.quote == ""
                continue
            path_text, _, lineno = token.source.rpartition(":")
            lines = (_REPO_ROOT / path_text).read_text(encoding="utf-8").splitlines()
            assert token.quote in lines[int(lineno) - 1], (
                f"{token.token}: {token.source} no longer contains {token.quote!r}"
            )

    def test_the_unattested_tokens_are_named_not_hidden(self) -> None:
        assert {t.token for t in SHAPE_TOKENS if t.source == ATTESTED_NOWHERE} == {"mae", "anamnese"}

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
        assert messages == 22
