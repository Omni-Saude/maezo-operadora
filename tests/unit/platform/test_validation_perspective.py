"""Unit tests for maezo.platform.validation.perspective — the perspective fence.

The fence is specified by
`docs/audits/maezo-deep-audit/remediation/REDESIGN-SP-OP-CONTAS-001.md` §6 and
ADR-0040 D7. Three groups of tests carry the load:

1. **The adversarial corpus.** The design gatekeeper attacked the fence with
   25 crafted lines — 8 payer-legitimate lines the first lexicon wrongly
   rejected, 7 provider-perspective lines it wrongly accepted, and 10 more
   (5 + 5) written against the *rebuilt* lexicon. Each of the 25 has its own
   named test here, so a regression names the sentence that broke.
2. **Non-vacuity and inventory.** Every R1 family and every R2 rule has a
   positive and a negative fixture, and the rule counts are pinned — because
   the lexicon lives in a file that is not CODEOWNED, the cheap way to make a
   red build green would otherwise be to delete a rule.
3. **The descending pin.** `test_two_chains_hit_count_is_pinned` records what
   the fence measures on the live tree today. It descends in PR-3 and reaches
   zero in PR-4, which is also when the fence is wired into `cli.py`.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from maezo.platform.validation import perspective
from maezo.platform.validation.perspective import (
    R1_FAMILIES,
    R1_TOKENS,
    R2_CORE_RULES,
    R2_CTX_RULES,
    TIER_A,
    TIER_B,
    Hit,
    scan_line,
    scan_xml_file,
    scan_yaml_file,
)
from maezo.platform.validation.result import Report

_REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/unit/platform -> repo root
_SPEC = _REPO_ROOT / "spec"
_MODULE_PATH = _REPO_ROOT / "src" / "maezo" / "platform" / "validation" / "perspective.py"
_MODULE_SOURCE = _MODULE_PATH.read_text(encoding="utf-8")

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
name="fixture" namespace="http://maezo.health/fixture">
  <decision id="fixture_decision" name="fixture">
    <decisionTable id="dt_fixture" hitPolicy="FIRST">
      <input id="in1"><inputExpression id="ie1" typeRef="string"><text>x</text></inputExpression></input>
      <output id="out1" typeRef="string"/>
      <rule id="r1"><inputEntry id="ien1"><text>-</text></inputEntry>{fragment}</rule>
    </decisionTable>
  </decision>
</definitions>
"""


def _scan_bpmn(tmp_path: Path, fragment: str) -> list[Hit]:
    """Scan one BPMN fragment inside a minimal well-formed definitions document."""
    path = tmp_path / "fixture.bpmn"
    path.write_text(_BPMN_WRAPPER.format(fragment=fragment), encoding="utf-8")
    report = Report()
    hits = scan_xml_file(path, report)
    assert report.ok, [f.message for f in report.findings]
    return hits


def _scan_dmn(tmp_path: Path, fragment: str) -> list[Hit]:
    """Scan one DMN rule fragment inside a minimal well-formed decision table."""
    path = tmp_path / "fixture.dmn"
    path.write_text(_DMN_WRAPPER.format(fragment=fragment), encoding="utf-8")
    report = Report()
    hits = scan_xml_file(path, report)
    assert report.ok, [f.message for f in report.findings]
    return hits


def _scan_yaml(tmp_path: Path, source: str) -> list[Hit]:
    path = tmp_path / "fixture.yaml"
    path.write_text(source, encoding="utf-8")
    report = Report()
    hits = scan_yaml_file(path, report)
    assert report.ok, [f.message for f in report.findings]
    return hits


def _rules(hits: list[Hit]) -> set[str]:
    return {f"{hit.rule_class}/{hit.rule_id}" for hit in hits}


# ---------------------------------------------------------------------------
# 1. The adversarial corpus — the 8 original false positives (must PASS)
# ---------------------------------------------------------------------------


class TestOriginalFalsePositivesNowPass:
    """Payer-legitimate lines the first lexicon rejected. All 8 must be clean.

    A fence that rejects correct prose has exactly two escapes — reword the
    prose or delete the rule — and §6.2 forbids both by refusing a per-file
    exception mechanism. So these eight are load-bearing, not decoration.
    """

    def test_allows_prestador_deadline_prose(self, tmp_path: Path) -> None:
        """FP1 — X1 `interpor` is absolved by the actor cue `prestador`."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_Prazo" name="Prazo do prestador para interpor recurso '
            '(nao cronometrado pela operadora)"/>',
        )
        assert hits == [], _rules(hits)

    def test_allows_recurso_interposto_recebido_do_prestador(self, tmp_path: Path) -> None:
        """FP2 — the actor cue works at a distance; R0's exact-bigram layer is gone."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_Doc"><bpmn:documentation>Documentacao do recurso '
            "interposto, recebida do prestador em D+2</bpmn:documentation></bpmn:task>",
        )
        assert hits == [], _rules(hits)

    def test_allows_prestador_confirms_receipt_of_demonstrativo(self, tmp_path: Path) -> None:
        """FP3 — X2 fires on `recebimento`+`demonstrativo` but `prestador` absolves it."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_Dem"><bpmn:documentation>A operadora emite o demonstrativo; '
            "o prestador confirma o recebimento do demonstrativo</bpmn:documentation></bpmn:task>",
        )
        assert hits == [], _rules(hits)

    def test_allows_beneficiario_nip_right_to_appeal_to_ans(self, tmp_path: Path) -> None:
        """FP4 — RN 483 gives the beneficiary the right to appeal to the ANS."""
        hits = _scan_dmn(
            tmp_path,
            '<outputEntry id="oe1"><text>"Beneficiario pode recorrer a ANS via NIP '
            '(RN 483) — direito informado na negativa"</text></outputEntry>',
        )
        assert hits == [], _rules(hits)

    def test_allows_prestador_may_resubmit_corrected_conta(self, tmp_path: Path) -> None:
        """FP5 — CONTAS' own inbound resubmission event."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_Reenvio" name="Mensagem: o prestador pode reenviar a conta '
            'corrigida (a operadora recebe)"/>',
        )
        assert hits == [], _rules(hits)

    def test_allows_pagamento_da_conta_integral_ao_prestador(self, tmp_path: Path) -> None:
        """FP6 — the `conta integral` rule was deleted; the inverted part was `Sem glosa`."""
        hits = _scan_dmn(
            tmp_path,
            '<outputEntry id="oe1"><text>"Pagamento da conta integral ao prestador '
            '— sem divergencias"</text></outputEntry>',
        )
        assert hits == [], _rules(hits)

    def test_allows_demonstrativo_de_elegibilidade_do_beneficiario(self, tmp_path: Path) -> None:
        """FP7 — `\\bno demonstrativo\\b` was replaced by X2, which needs a reception verb."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_Eleg"><bpmn:documentation>Valor autorizado nao aparece no '
            "demonstrativo de elegibilidade do beneficiario</bpmn:documentation></bpmn:task>",
        )
        assert hits == [], _rules(hits)

    def test_allows_resposta_recebida_ans_variable(self, tmp_path: Path) -> None:
        """FP8 — `tail` boundary: an answer from the **ANS** is not an answer from the payer.

        The true positive is asserted in the same test, so the boundary cannot
        be "fixed" by deleting the token.
        """
        clean = _scan_dmn(tmp_path, '<outputEntry id="oe1"><text>resposta_recebida_ans</text></outputEntry>')
        assert clean == [], _rules(clean)

        flagged = _scan_dmn(
            tmp_path, '<outputEntry id="oe1"><text>msg.recurso.resposta_recebida</text></outputEntry>'
        )
        assert "R1/espera-resposta:resposta_recebida" in _rules(flagged)


# ---------------------------------------------------------------------------
# 1b. The adversarial corpus — the 7 original false negatives (must be REJECTED)
# ---------------------------------------------------------------------------


class TestOriginalFalseNegativesNowRejected:
    """Provider-perspective lines the first lexicon accepted. All 7 must fail."""

    def test_flags_first_person_in_a_spec_artifact(self, tmp_path: Path) -> None:
        """FN1 — `nosso` in a spec artifact plus the wait for someone else's decision."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_Ag"><bpmn:documentation>Aguardar a decisao da operadora sobre '
            "o nosso pleito de faturamento</bpmn:documentation></bpmn:task>",
        )
        assert {"R2-CORE/C1", "R2-CORE/C5", "R2-CORE/C6", "R2-CTX/X4"} <= _rules(hits)

    def test_flags_protocolar_recurso_junto_a_operadora(self, tmp_path: Path) -> None:
        """FN2 — filing an appeal *with* the operadora puts the payer outside itself."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:serviceTask id="ST_ProtocolarRecurso" name="Protocolar o recurso '
            'junto a operadora (TISS)"/>',
        )
        assert {"R2-CORE/C7", "R2-CORE/C8"} <= _rules(hits)

    def test_flags_appellant_synonym_decision_values(self, tmp_path: Path) -> None:
        """FN3 — the decisive one: the same inversion, expressed with synonyms.

        R1 is organized in *families* precisely so that `CONTESTAR`,
        `ACATAR_GLOSA` and `REAPRESENTAR` cannot walk past a blocklist built
        from the strings that happen to be in the tree today.
        """
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_S"><bpmn:documentation>Saidas: decisao_contas in '
            "{CONTESTAR, ACATAR_GLOSA, REAPRESENTAR}</bpmn:documentation></bpmn:task>",
        )
        assert {
            "R1/verbo-recorrente:CONTESTAR",
            "R1/aquiescencia:ACATAR_GLOSA",
            "R1/reapresentacao:REAPRESENTAR",
        } <= _rules(hits)

    def test_flags_conciliating_received_money(self, tmp_path: Path) -> None:
        """FN4 — reconciling money *received* is the creditor's act, not the payer's."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:serviceTask id="ST_ConciliarCredito" name="Conciliar o credito recebido '
            'da fonte pagadora"/>',
        )
        assert {"R2-CORE/C4", "R2-CORE/C10"} <= _rules(hits)

    def test_flags_faturamento_tracking_pleito_junto_a_fonte_pagadora(self, tmp_path: Path) -> None:
        """FN5 — `fonte pagadora` names the operadora as somebody else."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_F"><bpmn:documentation>O faturamento acompanha o andamento '
            "do pleito junto a fonte pagadora</bpmn:documentation></bpmn:task>",
        )
        assert {"R2-CORE/C4", "R2-CTX/X4"} <= _rules(hits)

    def test_flags_contestavel_decision_value(self, tmp_path: Path) -> None:
        """FN6 — `CONTESTAVEL` is `RECORRIVEL` with a different root."""
        hits = _scan_dmn(tmp_path, '<outputEntry id="oe1"><text>"CONTESTAVEL"</text></outputEntry>')
        assert "R1/recorrivel:CONTESTAVEL" in _rules(hits)

    def test_flags_fonte_pagadora_framing(self, tmp_path: Path) -> None:
        """FN7 — `decisao alheia` about the payer's own decision."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:endEvent id="End_PleitoIndeferidoPelaFonte" name="Pleito indeferido pela '
            'fonte pagadora (decisao alheia)"/>',
        )
        assert {"R2-CORE/C2", "R2-CORE/C4", "R2-CTX/X4"} <= _rules(hits)


# ---------------------------------------------------------------------------
# 1c. The 5 new payer-legitimate sentences (must PASS)
# ---------------------------------------------------------------------------


class TestNewPayerLegitimateSentencesPass:
    """Written by the gatekeeper against the *rebuilt* lexicon. 5/5 must pass."""

    def test_allows_emitting_a_demonstrativo(self, tmp_path: Path) -> None:
        """NFP1 — emission is the payer's direction of travel; X2 needs reception."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_Emit"><bpmn:documentation>A operadora emite o demonstrativo '
            "de analise da conta ao prestador (TISS)</bpmn:documentation></bpmn:task>",
        )
        assert hits == [], _rules(hits)

    def test_allows_recurso_de_glosa_recebido_do_prestador(self, tmp_path: Path) -> None:
        """NFP2 — receiving an appeal is exactly what RECURSO-001 exists to do."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:startEvent id="Start_RecursoRecebido" name="Recurso de glosa recebido '
            'do prestador (TISS)"/>',
        )
        assert hits == [], _rules(hits)

    def test_allows_business_key_reenvio_boilerplate(self, tmp_path: Path) -> None:
        """NFP3 — the idempotency boilerplate of 6 correct BPMNs.

        It needs no permission layer at all: `reenvio` is a noun (an inbound
        event), and X1 only lists the verbs.
        """
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_BK"><bpmn:documentation>Reenvio da mesma guia NAO cria nova '
            "instancia (idempotencia por business key)</bpmn:documentation></bpmn:task>",
        )
        assert hits == [], _rules(hits)

    def test_allows_prestador_deadline_to_reapresentar(self, tmp_path: Path) -> None:
        """NFP4 — the payer may state the prestador's deadline without owning it."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_Prazo2"><bpmn:documentation>Prazo de que o prestador dispoe '
            "para reapresentar a conta corrigida — nao cronometrado aqui"
            "</bpmn:documentation></bpmn:task>",
        )
        assert hits == [], _rules(hits)

    def test_allows_junta_medica_rn_424_prose(self, tmp_path: Path) -> None:
        """NFP5 — RN 424/2017 junta médica language is payer prose."""
        hits = _scan_dmn(
            tmp_path,
            '<outputEntry id="oe1"><text>"Junta medica instaurada para dirimir divergencia '
            'tecnico-assistencial (RN 424/2017)"</text></outputEntry>',
        )
        assert hits == [], _rules(hits)


# ---------------------------------------------------------------------------
# 1d. The 5 new provider-perspective sentences — 2 caught, 3 declared limit
# ---------------------------------------------------------------------------


class TestNewProviderSentences:
    """The gatekeeper's third register. Two are caught by an R1 extension; three
    are the **declared limit** of ADR-0040 D7 / §6.8 and are pinned as such.

    Pinning the limit as a passing-today assertion is deliberate: a limit that
    is only prose drifts, while a limit that is a test forces whoever narrows
    it to come back and say so.
    """

    def test_flags_reclamar_absorver_perda_refazer_guia_decision_values(self, tmp_path: Path) -> None:
        """NFN2 — caught by extending three existing R1 families (see `[PR-1]`)."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_S2"><bpmn:documentation>Saidas: decisao_conta in '
            "{RECLAMAR, ABSORVER_PERDA, REFAZER_GUIA}</bpmn:documentation></bpmn:task>",
        )
        assert {
            "R1/verbo-recorrente:RECLAMAR",
            "R1/aquiescencia:ABSORVER_PERDA",
            "R1/reapresentacao:REFAZER_GUIA",
        } <= _rules(hits)

    def test_flags_perda_aceita_decision_value(self, tmp_path: Path) -> None:
        """NFN5 — `PERDA_ACEITA` is acquiescence with an unfamiliar noun."""
        hits = _scan_dmn(tmp_path, '<outputEntry id="oe1"><text>"PERDA_ACEITA"</text></outputEntry>')
        assert "R1/aquiescencia:PERDA_ACEITA" in _rules(hits)

    def test_declared_limit_defesa_ao_convenio_is_not_caught(self, tmp_path: Path) -> None:
        """NFN1 — DECLARED LIMIT (ADR-0040 D7).

        `convenio` and `plano de saude` are what the operadora *is*; a rule on
        the bare nouns would reject correct RN prose ("prazo de resposta do
        plano de saude"). The register is left to the human perspective test
        (D2) rather than bought with false positives.
        """
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:serviceTask id="ST_EnviarDefesa" name="Enviar a defesa da conta ao convenio (TISS)"/>',
        )
        assert hits == [], _rules(hits)

    def test_declared_limit_repasse_do_plano_de_saude_is_not_caught(self, tmp_path: Path) -> None:
        """NFN3 — DECLARED LIMIT: `repasse` framed as inbound money."""
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:endEvent id="End_CreditoBaixado" name="Credito baixado apos o repasse '
            'do plano de saude"/>',
        )
        assert hits == [], _rules(hits)

    def test_declared_limit_parecer_do_convenio_is_not_caught(self, tmp_path: Path) -> None:
        """NFN4 — DECLARED LIMIT: waiting on a third party named `convenio`.

        Note C1 already forbids `resposta/decisao/retorno da operadora`; the
        same construction with `convenio` is the next synonym pair, and §6.8
        predicted in writing that it would pass.
        """
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_Mon"><bpmn:documentation>Monitorar o parecer do convenio '
            "sobre a defesa enviada pelo faturamento</bpmn:documentation></bpmn:task>",
        )
        assert hits == [], _rules(hits)


# ---------------------------------------------------------------------------
# 2. Rule tables — non-vacuity and inventory
# ---------------------------------------------------------------------------

#: One positive and one negative line per R1 family. The positive must fire a
#: token of that family; the negative must fire none of it.
R1_FAMILY_FIXTURES: dict[str, tuple[str, str]] = {
    "verbo-recorrente": ("${decisao_contas == 'RECORRER'}", "${decisao_contas == 'DEFERIR'}"),
    "aquiescencia": ("${decisao_contas == 'ACEITAR_GLOSA'}", "${decisao_contas == 'GLOSAR'}"),
    "reapresentacao": ("${decisao_contas == 'REENVIAR'}", "${decisao_contas == 'SOLICITAR_AJUSTE'}"),
    "recorrivel": ('"NAO_RECORRIVEL"', '"NAO_PROVIDO"'),
    "manutencao": ("MANTER_RECURSO", "MANTER_DECISAO"),
    "resposta-terceiro": ("resposta_operadora", "resposta_ao_recurso"),
    "ciclo-recorrente": ("operadora.recurso.submit_appeal", "operadora.recurso.receive_appeal"),
    "ancora-kpi": ("data_ciencia_glosa", "data_emissao_glosa"),
    "espera-resposta": ("msg.recurso.resposta_recebida", "resposta_recebida_ans"),
}

#: One positive and one negative line per R2 rule (both classes).
PROSE_FIXTURES: dict[str, tuple[str, str]] = {
    "C1": ("Aguardando a resposta da operadora", "Resposta ao recurso emitida pela operadora"),
    "C2": ("Encerrado por decisao alheia", "Encerrado por decisao da junta medica"),
    "C3": ("Encaminhado a operadora externa", "Encaminhado a auditoria interna"),
    "C4": ("Credito a receber da fonte pagadora", "Credito a pagar ao prestador"),
    "C5": ("Registrar o nosso protocolo", "Registrar o protocolo do prestador"),
    "C6": ("Aguardar a resposta da junta medica", "Aguardar o vencimento do prazo regulatorio"),
    "C7": ("Peca dirigida a operadora", "Peca recebida pela operadora"),
    "C8": ("Protocolar o recurso no sistema", "Registrar o recurso no sistema"),
    "C9": ("Aceite de glosa registrado", "Glosa aplicada na linha da conta"),
    "C10": ("Conciliar o pagamento recebido", "Conciliar o cadastro de credenciados"),
    "C11": ("Peticao de recurso anexada ao dossie", "Recurso anexado ao dossie"),
    "C12": ("Acompanhar o status do processo", "Registrar o status do processo"),
    "X1": ("Interpor recurso dentro do prazo", "Interpor dentro do prazo"),
    "X2": ("Conferir o demonstrativo recebido", "Emitir o demonstrativo de analise"),
    "X3": ("Registrar reenvio na trilha de auditoria", "Registrar lote na trilha de auditoria"),
    "X4": ("Pleito em analise pela area tecnica", "Encaminhamento em analise pela area tecnica"),
}

_ALL_PROSE_RULES = {rule.rule_id: rule for rule in (*R2_CORE_RULES, *R2_CTX_RULES)}


class TestRuleTablesAreNonVacuous:
    """A rule that no fixture can make fire is dead code pretending to be a control.

    Non-vacuity is measured **only** against this synthetic corpus. Against the
    live tree the target is zero (that is AC-5), so "fires against the tree"
    would be a self-contradictory criterion (design MINOR m1).
    """

    def test_every_r1_family_has_a_fixture(self) -> None:
        assert set(R1_FAMILY_FIXTURES) == set(R1_FAMILIES)

    def test_every_prose_rule_has_a_fixture(self) -> None:
        assert set(PROSE_FIXTURES) == set(_ALL_PROSE_RULES)

    @pytest.mark.parametrize("family", sorted(R1_FAMILY_FIXTURES))
    def test_r1_family_fires_on_its_positive_fixture(self, family: str) -> None:
        positive, _ = R1_FAMILY_FIXTURES[family]
        families = {
            rule_id.split(":", 1)[0] for cls, rule_id, _ in scan_line(positive, tier=TIER_A) if cls == "R1"
        }
        assert family in families

    @pytest.mark.parametrize("family", sorted(R1_FAMILY_FIXTURES))
    def test_r1_family_is_silent_on_its_negative_fixture(self, family: str) -> None:
        _, negative = R1_FAMILY_FIXTURES[family]
        families = {
            rule_id.split(":", 1)[0] for cls, rule_id, _ in scan_line(negative, tier=TIER_A) if cls == "R1"
        }
        assert family not in families

    @pytest.mark.parametrize("rule_id", sorted(PROSE_FIXTURES))
    def test_prose_rule_fires_on_its_positive_fixture(self, rule_id: str) -> None:
        positive, _ = PROSE_FIXTURES[rule_id]
        assert rule_id in {r for _, r, _ in scan_line(positive, tier=TIER_A)}

    @pytest.mark.parametrize("rule_id", sorted(PROSE_FIXTURES))
    def test_prose_rule_is_silent_on_its_negative_fixture(self, rule_id: str) -> None:
        _, negative = PROSE_FIXTURES[rule_id]
        assert rule_id not in {r for _, r, _ in scan_line(negative, tier=TIER_A)}


class TestRuleInventoryIsPinned:
    """Deleting a rule to make a build green must itself break the build.

    `src/maezo/platform/validation/` is deliberately **not** CODEOWNED (§6.1),
    so the pressure valve on a red fence would be to edit the lexicon. These
    assertions close it.
    """

    def test_rule_counts_are_pinned(self) -> None:
        assert len(R1_FAMILIES) == 9
        assert len(R1_TOKENS) == 79
        assert len(R2_CORE_RULES) == 12
        assert len(R2_CTX_RULES) == 4

    def test_r1_token_set_is_pinned_exactly(self) -> None:
        assert {rule.token for rule in R1_TOKENS} == {
            # verbo-recorrente
            "RECORRER",
            "Recorrer",
            "NAO_RECORRER",
            "NaoRecorrer",
            "CONTESTAR",
            "Contestar",
            "IMPUGNAR",
            "PROTESTAR",
            "PLEITEAR",
            "RECURSAR",
            "RECLAMAR",
            "Reclamar",
            # aquiescencia
            "ACEITAR_GLOSA",
            "AceitarGlosa",
            "ACATAR_GLOSA",
            "AcatarGlosa",
            "ACATAR",
            "CONFORMAR_GLOSA",
            "DESISTIR",
            "Desistir",
            "DESISTENCIA",
            "RENUNCIAR_RECURSO",
            "register_desistencia",
            "RegisterDesistencia",
            "justificativa_desistencia",
            "valor_glosa_aceito",
            "register_glosa_accept",
            "RegisterGlosaAccept",
            "GlosaAcceptNotHuman",
            "DesistenciaNotHuman",
            "ERR_GLOSA_ACCEPT_NOT_HUMAN",
            "ERR_DESISTENCIA_NOT_HUMAN",
            "ABSORVER_PERDA",
            "AbsorverPerda",
            "PERDA_ACEITA",
            "PerdaAceita",
            # reapresentacao
            "REENVIAR",
            "Reenviar",
            "REAPRESENTAR",
            "Reapresentar",
            "RETRANSMITIR",
            "REFATURAR",
            "REFAZER_GUIA",
            "RefazerGuia",
            # recorrivel
            "RECORRIVEL",
            "NAO_RECORRIVEL",
            "CONTESTAVEL",
            "NAO_CONTESTAVEL",
            "IMPUGNAVEL",
            "PASSIVEL_DE_RECURSO",
            # manutencao
            "MANTER_RECURSO",
            "ManterRecurso",
            "RECURSO_PARCIAL",
            "NAO_INTERPOSTO",
            "nao_interposto",
            "NaoInterposto",
            # resposta-terceiro
            "resposta_operadora",
            "decisao_operadora",
            "retorno_operadora",
            "resposta_fonte_pagadora",
            # ciclo-recorrente
            "submit_appeal",
            "SubmitAppeal",
            "track_status",
            "TrackStatus",
            "RECAPPEAL",
            "AguardarResposta",
            "reconcile_payment",
            "ReconcilePayment",
            "protocolo_recurso",
            "start_recurso",
            "StartRecurso",
            "acompanhar_recurso",
            "AcompanharRecurso",
            "acompanhar_status",
            "AcompanharStatus",
            # ancora-kpi
            "data_ciencia_glosa",
            "recurso_recovery_rate",
            # espera-resposta
            "resposta_recebida",
            "RespostaRecebida",
        }

    def test_prose_rule_ids_are_pinned_exactly(self) -> None:
        assert [rule.rule_id for rule in R2_CORE_RULES] == [f"C{n}" for n in range(1, 13)]
        assert [rule.rule_id for rule in R2_CTX_RULES] == ["X1", "X2", "X3", "X4"]


class TestBoundaryDiscipline:
    """`whole` / `segment` / `tail` / `group` — the four boundary modes."""

    def test_whole_boundary_does_not_match_a_longer_identifier(self) -> None:
        assert {r for _, r, _ in scan_line("recurso_recovery_rate", tier=TIER_A)} == {
            "ancora-kpi:recurso_recovery_rate"
        }
        assert scan_line("recurso_recovery_rate_v2", tier=TIER_A) == []

    def test_segment_boundary_matches_across_camel_case_but_not_inside_a_word(self) -> None:
        assert "ciclo-recorrente:ReconcilePayment" in {
            r for _, r, _ in scan_line("ST_ReconcilePaymentDeferido", tier=TIER_A)
        }
        assert scan_line("Reconciliacao", tier=TIER_A) == []

    def test_tail_boundary_requires_the_token_to_end_the_identifier(self) -> None:
        assert "espera-resposta:resposta_recebida" in {
            r for _, r, _ in scan_line("msg.recurso.resposta_recebida", tier=TIER_A)
        }
        assert scan_line("resposta_recebida_ans", tier=TIER_A) == []

    def test_start_recurso_group_boundary_catches_the_payer_action(self) -> None:
        """`ST_StartRecurso` / `operadora.contas.start_recurso` are the payer
        filing an appeal against its own glosa (D-m3) — they must stay caught.

        `StartRecurso` (CamelCase) uses `group`; `start_recurso` (snake_case)
        uses the plain `segment` default (m1, verifier gate review: a bare
        `tail` bound on both, PR-1's first cut, over-corrected — see the two
        tests below).
        """
        assert "ciclo-recorrente:StartRecurso" in {r for _, r, _ in scan_line("ST_StartRecurso", tier=TIER_A)}
        assert "ciclo-recorrente:start_recurso" in {
            r for _, r, _ in scan_line("operadora.contas.start_recurso", tier=TIER_A)
        }

    def test_start_recurso_group_boundary_releases_the_inbound_bpmn_id(self) -> None:
        """`Start_RecursoRecebido` is the payer *receiving* an appeal — the
        redesign renames `Start_RecursoSolicitado` (live today at
        `SP-OP-RECURSO-001_Recurso_Glosa.bpmn:86`, the inbound start event) to
        exactly this. Both must stay clean.
        """
        assert scan_line("Start_RecursoRecebido", tier=TIER_A) == []
        assert scan_line("Start_RecursoSolicitado", tier=TIER_A) == []

    def test_start_recurso_group_boundary_catches_the_suffixed_evasions(self) -> None:
        """m1, verifier gate review (EV-1/2/3): the `tail` bound PR-1 shipped
        first let every *suffixed* provider-perspective id in this family
        walk past — `ST_StartRecursoGlosa`, `ST_StartRecursoDeGlosa`,
        `operadora.contas.start_recurso_glosa` — while the `segment`-bound
        siblings of the same family (`SubmitAppeal`, `TrackStatus`) caught
        their own suffixed forms (`ST_SubmitAppealGlosa`,
        `ST_TrackStatusRecurso`, asserted below as the contrast). `group`
        closes the hole: it only releases a `Start`/`Recurso` pair that the
        identifier's *own* delimiter splits apart, which none of these do.
        """
        assert "ciclo-recorrente:StartRecurso" in {
            r for _, r, _ in scan_line("ST_StartRecursoGlosa", tier=TIER_A)
        }
        assert "ciclo-recorrente:StartRecurso" in {
            r for _, r, _ in scan_line("ST_StartRecursoDeGlosa", tier=TIER_A)
        }
        assert "ciclo-recorrente:start_recurso" in {
            r for _, r, _ in scan_line("operadora.contas.start_recurso_glosa", tier=TIER_A)
        }
        # The contrast: this family's other, already-`segment`-bound tokens
        # were never fooled by a suffix. Not hypothetical regressions — the
        # gate review's own EV-8/EV-9 attack sentences.
        assert "ciclo-recorrente:TrackStatus" in {
            r for _, r, _ in scan_line("ST_TrackStatusRecurso", tier=TIER_A)
        }
        assert "ciclo-recorrente:SubmitAppeal" in {
            r for _, r, _ in scan_line("ST_SubmitAppealGlosa", tier=TIER_A)
        }

    def test_start_recurso_group_boundary_declared_residual(self) -> None:
        """Declared residual (m1, verifier gate review) — not silently accepted.

        An id of the *exact* shape `Start_Recurso<Capitalized>` is structurally
        indistinguishable from the legitimate rename
        (`Start_RecursoSolicitado`/`Start_RecursoRecebido`): both put the
        identifier's own `_` between `Start` and `Recurso`. Closing this would
        require reading what the suffix *means*, which is a perspective
        judgement, not a vocabulary one (ADR-0040 D7) — so it stays open, and
        this test is the record that it is known, not missed.
        """
        assert scan_line("Start_RecursoGlosa", tier=TIER_A) == []

    def test_r1_is_case_sensitive(self) -> None:
        """`r_valor_recorrer` (a lowercase DMN rule id) is not the decision value."""
        assert not [r for c, r, _ in scan_line("r_valor_recorrer", tier=TIER_A) if c == "R1"]
        assert "verbo-recorrente:RECORRER" in {r for _, r, _ in scan_line("RECORRER", tier=TIER_A)}


class TestDisambiguationRules:
    """The three lexical disambiguations, each stated as both directions."""

    def test_actor_cue_absolves_only_r2_ctx_never_r1(self) -> None:
        """R1 has no exculpation at all: a decision value is a decision value."""
        hits = scan_line("Saida do prestador: RECORRER", tier=TIER_A)
        assert "verbo-recorrente:RECORRER" in {r for c, r, _ in hits if c == "R1"}

    def test_actor_cue_absolves_only_within_the_window(self) -> None:
        near = "o prestador pode interpor o recurso"
        far = "interpor o recurso; o pagamento sera avaliado conforme a tabela de precos do prestador"
        assert "X1" not in {r for _, r, _ in scan_line(near, tier=TIER_A)}
        assert "X1" in {r for _, r, _ in scan_line(far, tier=TIER_A)}

    def test_r2_ctx_needs_its_object(self) -> None:
        assert "X1" in {r for _, r, _ in scan_line("Reapresentar a conta", tier=TIER_A)}
        assert "X1" not in {r for _, r, _ in scan_line("Reapresentar o formulario", tier=TIER_A)}

    def test_an_immediately_preceding_negation_absolves_r2_ctx(self) -> None:
        """`sem contestacao de negativa` states an absence, not an act.

        Adjacency, not a window: the negation must sit immediately before the
        match, so it cannot launder an inversion elsewhere on the line.
        """
        assert "X1" not in {r for _, r, _ in scan_line("sem contestacao de negativa", tier=TIER_A)}
        assert "X1" in {r for _, r, _ in scan_line("com contestacao de negativa", tier=TIER_A)}
        assert "X1" in {
            r for _, r, _ in scan_line("sem anexos; contestacao de negativa protocolada", tier=TIER_A)
        }

    def test_nip_is_no_longer_an_actor_cue_because_it_names_an_instrument_not_a_party(self) -> None:
        """m2/m3 (verifier gate review): `nip` used to sit in `PISTA_DE_ATOR`,
        but it names an instrument (the NIP protocol), not a party — as an
        actor cue it absolved *all four* R2-CTX rules near any mention of
        NIP regardless of who the sentence's subject was, which laundered
        real inversions. Proof: a provider-perspective sentence that merely
        mentions NIP is now caught (it used to escape — the gate review's
        EV-6/EV-7).
        """
        hits = scan_line("Registrar o protocolo NIP e reapresentar a conta glosada", tier=TIER_A)
        assert "X1" in {r for _, r, _ in hits}
        hits = scan_line("Fluxo NIP: interpor o recurso e aguardar 30 dias", tier=TIER_A)
        assert "X1" in {r for _, r, _ in hits}

    def test_decisao_pagador_absolves_x4_for_the_nip_grant_line_not_nip_itself(self) -> None:
        """`SP-OP-NIP-001_Resposta_NIP.bpmn:482`'s actual text is the payer
        *conceding* a request — cleared by naming the payer's own decision
        verb (`DECISAO_PAGADOR`: conceder/deferir/indeferir/julgar/responder),
        not by naming the NIP instrument (m2/m3). This generalises correctly:
        any sentence stating the payer's own decision on a `pleito` is
        payer-legitimate, with or without `nip` in it.
        """
        assert scan_line("Submeter resposta NIP (conceder o pleito)", tier=TIER_A) == []
        assert scan_line("Conceder o pleito", tier=TIER_A) == []
        # X4 still fires with no decision verb nearby...
        assert "X4" in {r for _, r, _ in scan_line("Pleito em analise pela area tecnica", tier=TIER_A)}
        # ...and a bare participle (not the infinitive `DECISAO_PAGADOR` lists)
        # is *not* absolved — `test_flags_fonte_pagadora_framing` depends on
        # this staying caught.
        assert "X4" in {r for _, r, _ in scan_line("Pleito indeferido pela fonte pagadora", tier=TIER_A)}


# ---------------------------------------------------------------------------
# 1e. Verifier gate review (VERIFY-PR1-FENCE.md §3) — 18 attack sentences on
#     the rebuilt lexicon, none reused from the design gate's own corpus above
# ---------------------------------------------------------------------------


class TestVerifierGateReviewAttackSentences:
    """`VERIFY-PR1-FENCE.md` §3: the fence-gatekeeper's own adversarial pass.

    Three groups: 8 payer-legitimate lines that must stay clean (§3.1), 7
    provider-perspective lines that must be caught (§3.2), and 10 evasion
    probes hunting specifically for gaps (§3.3). The review found 5 of the 10
    probes escaping: EV-1/EV-2/EV-3 via the `start_recurso`/`StartRecurso`
    `tail` boundary (m1), and EV-6/EV-7 via the `nip` actor cue (m3). Both
    holes are closed in this PR — see `TestBoundaryDiscipline` and
    `test_nip_is_no_longer_an_actor_cue_...` above for the mechanism — so all
    10 probes below are now asserted **caught**, with the live-tree pin
    (`test_two_chains_hit_count_is_pinned`) unchanged at 295/7: no new false
    positive was introduced to close them. The one gap that remains open
    after this PR — the `Start_Recurso<Capitalized>` shape — is not one of
    these 18; it is disclosed separately by
    `test_start_recurso_group_boundary_declared_residual`.
    """

    # -- §3.1: payer-legitimate, must stay clean (8/8) -----------------------

    def test_nfp_a_prestador_may_resend_within_30_days(self) -> None:
        hits = scan_line(
            "O prestador pode reenviar a conta corrigida dentro do prazo de 30 dias", tier=TIER_A
        )
        assert hits == [], hits

    def test_nfp_b_beneficiario_may_appeal_to_ans_via_nip(self) -> None:
        hits = scan_line("Beneficiario pode recorrer a ANS via NIP (RN 483) apos a negativa", tier=TIER_A)
        assert hits == [], hits

    def test_nfp_c_escalation_naming_the_answer_to_an_appeal(self) -> None:
        """ "resposta ao recurso" is exactly the construction C1 forces the
        redesign to adopt — an ESCALATION line naming it must not be punished.
        """
        hits = scan_line(
            "Escalar para a coordenacao quando o SLA regulatorio da resposta ao recurso vencer",
            tier=TIER_A,
        )
        assert hits == [], hits

    def test_nfp_d_authorization_granted_and_denial_communicated(self) -> None:
        hits = scan_line(
            "Autorizacao concedida; negativa comunicada ao beneficiario com o codigo TISS", tier=TIER_A
        )
        assert hits == [], hits

    def test_nfp_e_start_recursosolicitado_is_the_live_inbound_id(self) -> None:
        """Same claim as `TestBoundaryDiscipline`, named for §3's own id (NFP-E)."""
        assert scan_line("Start_RecursoSolicitado", tier=TIER_A) == []

    def test_nfp_f_resposta_recebida_ans_is_the_ans_not_the_payer(self) -> None:
        """Same claim as FP8, named for §3's own id (NFP-F)."""
        assert scan_line("resposta_recebida_ans", tier=TIER_A) == []

    def test_nfp_g_st_registrarnegativaautorizacao_is_clean(self) -> None:
        assert scan_line("ST_RegistrarNegativaAutorizacao", tier=TIER_A) == []

    def test_nfp_h_emitting_a_demonstrativo_to_the_prestador_is_clean(self) -> None:
        """Emission is the payer's direction of travel; X2 needs *reception*."""
        hits = scan_line("Emitir o demonstrativo de analise da conta ao prestador", tier=TIER_A)
        assert hits == [], hits

    # -- §3.2: provider-perspective, must be caught (7/7) --------------------

    def test_nfn_a_appellant_synonyms_as_decision_values(self) -> None:
        hits = [
            *scan_line('${decisao_contas == "IMPUGNAR"}', tier=TIER_A),
            *scan_line('${decisao_contas == "CONFORMAR_GLOSA"}', tier=TIER_A),
            *scan_line('${decisao_contas == "RETRANSMITIR"}', tier=TIER_A),
        ]
        assert {
            "verbo-recorrente:IMPUGNAR",
            "aquiescencia:CONFORMAR_GLOSA",
            "reapresentacao:RETRANSMITIR",
        } <= {r for _, r, _ in hits}

    def test_nfn_b_first_person_appeal_against_a_glosa(self) -> None:
        hits = scan_line("Registrar o nosso recurso contra a glosa aplicada", tier=TIER_A)
        assert "C5" in {r for _, r, _ in hits}

    def test_nfn_c_petition_addressed_to_the_operadora(self) -> None:
        hits = scan_line("Peticao dirigida a operadora para revisao da glosa", tier=TIER_A)
        assert "C7" in {r for _, r, _ in hits}

    def test_nfn_d_tracking_the_appeal_status(self) -> None:
        hits = scan_line("Acompanhar o status do recurso ate a decisao final", tier=TIER_A)
        assert "C12" in {r for _, r, _ in hits}

    def test_nfn_e_st_acompanharrecurso_is_now_caught(self) -> None:
        """NFN-E: the review found this escaping (pt-BR gap, m5) — fixed by
        extending `ciclo-recorrente` with `AcompanharRecurso`/`acompanhar_recurso`
        (and the `AcompanharStatus`/`acompanhar_status` analogues).
        """
        assert "ciclo-recorrente:AcompanharRecurso" in {
            r for _, r, _ in scan_line("ST_AcompanharRecurso", tier=TIER_A)
        }

    def test_nfn_f_recursar_and_refaturar_as_requests(self) -> None:
        hits = scan_line("Solicitar o RECURSAR da glosa e o REFATURAR da guia", tier=TIER_A)
        assert {"verbo-recorrente:RECURSAR", "reapresentacao:REFATURAR"} <= {r for _, r, _ in hits}

    def test_nfn_g_waiting_for_the_operadoras_decision_on_the_glosa(self) -> None:
        hits = scan_line("Aguardar a decisao da operadora sobre a glosa apresentada", tier=TIER_A)
        assert {"C1", "C6"} <= {r for _, r, _ in hits}

    # -- §3.3: evasion probes — all 10 caught, 0 residual from this set -----

    def test_ev1_lowercase_start_recurso_glosa_topic_is_now_caught(self) -> None:
        """EV-1: escaped under `tail` (m1); `start_recurso` is now `segment`."""
        assert "ciclo-recorrente:start_recurso" in {
            r for _, r, _ in scan_line("operadora.contas.start_recurso_glosa", tier=TIER_A)
        }

    def test_ev2_st_startrecursoglosa_is_now_caught(self) -> None:
        """EV-2: escaped under `tail` (m1); `StartRecurso` is now `group`."""
        assert "ciclo-recorrente:StartRecurso" in {
            r for _, r, _ in scan_line("ST_StartRecursoGlosa", tier=TIER_A)
        }

    def test_ev3_st_startrecursodeglosa_is_now_caught(self) -> None:
        """EV-3: escaped under `tail` (m1); `StartRecurso` is now `group`."""
        assert "ciclo-recorrente:StartRecurso" in {
            r for _, r, _ in scan_line("ST_StartRecursoDeGlosa", tier=TIER_A)
        }

    def test_ev4_sem_duvida_abuse_does_not_reach_the_negation_anchor(self) -> None:
        """The abuse the negation rule's `$` anchor is built to refuse: "sem
        duvida" is not *adjacent* to the verb, so X1 still fires.
        """
        hits = scan_line("sem duvida, contestar a glosa junto ao setor de faturamento", tier=TIER_A)
        assert "X1" in {r for _, r, _ in hits}

    def test_ev5_sem_contestar_is_correctly_absolved(self) -> None:
        """States the *absence* of the act, not the act — correctly clean."""
        assert scan_line("sem contestar a glosa", tier=TIER_A) == []

    def test_ev6_nip_protocol_and_reapresentar_is_now_caught(self) -> None:
        """EV-6: escaped because `nip` absolved X1 as an actor cue (m3);
        `nip` is no longer in `PISTA_DE_ATOR`.
        """
        hits = scan_line("Registrar o protocolo NIP e reapresentar a conta glosada", tier=TIER_A)
        assert "X1" in {r for _, r, _ in hits}

    def test_ev7_nip_flow_and_interpor_is_now_caught(self) -> None:
        """EV-7: the same laundering vector as EV-6, via `interpor` (m3)."""
        hits = scan_line("Fluxo NIP: interpor o recurso e aguardar 30 dias", tier=TIER_A)
        assert "X1" in {r for _, r, _ in hits}

    def test_ev8_st_trackstatusrecurso_is_the_segment_contrast(self) -> None:
        """The family's already-`segment`-bound sibling was never fooled by a
        suffix — the contrast that shows the pre-m1 `tail` bound was
        `start_recurso`'s own defect, not a property the family needed.
        """
        assert "ciclo-recorrente:TrackStatus" in {
            r for _, r, _ in scan_line("ST_TrackStatusRecurso", tier=TIER_A)
        }

    def test_ev9_st_submitappealglosa_is_the_segment_contrast(self) -> None:
        assert "ciclo-recorrente:SubmitAppeal" in {
            r for _, r, _ in scan_line("ST_SubmitAppealGlosa", tier=TIER_A)
        }

    def test_ev10_acompanhar_o_andamento_do_recurso(self) -> None:
        hits = scan_line("Acompanhar o andamento do recurso", tier=TIER_A)
        assert "C12" in {r for _, r, _ in hits}


# ---------------------------------------------------------------------------
# 3. Surfaces
# ---------------------------------------------------------------------------


class TestXmlSurface:
    def test_xml_surface_includes_comments(self, tmp_path: Path) -> None:
        """A BPMN comment is design documentation inside the process definition."""
        hits = _scan_bpmn(tmp_path, "<!-- Ramo RECORRER: interpoe o recurso junto a operadora -->")
        assert "R1/verbo-recorrente:RECORRER" in _rules(hits)

    def test_condition_expression_text_is_a_surface(self, tmp_path: Path) -> None:
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:sequenceFlow id="Flow_1" sourceRef="A" targetRef="B">'
            "<bpmn:conditionExpression>${decisao_contas == 'REENVIAR'}</bpmn:conditionExpression>"
            "</bpmn:sequenceFlow>",
        )
        assert "R1/reapresentacao:REENVIAR" in _rules(hits)

    def test_camunda_topic_and_result_variable_are_surfaces(self, tmp_path: Path) -> None:
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:serviceTask id="ST_A" camunda:type="external" '
            'camunda:topic="operadora.recurso.submit_appeal" '
            'camunda:resultVariable="resposta_operadora"/>',
        )
        assert {
            "R1/ciclo-recorrente:submit_appeal",
            "R1/resposta-terceiro:resposta_operadora",
        } <= _rules(hits)

    def test_cdata_inside_documentation_is_a_surface(self, tmp_path: Path) -> None:
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_C"><bpmn:documentation><![CDATA[Saidas: RECORRER | '
            "ACEITAR_GLOSA]]></bpmn:documentation></bpmn:task>",
        )
        assert {
            "R1/verbo-recorrente:RECORRER",
            "R1/aquiescencia:ACEITAR_GLOSA",
        } <= _rules(hits)

    def test_namespace_declarations_and_xsi_type_are_not_surfaces(self, tmp_path: Path) -> None:
        """Structural XML is not spec vocabulary; only the declared surfaces are."""
        hits = _scan_bpmn(tmp_path, '<bpmn:task id="T_Clean" name="Analisar a conta recebida do prestador"/>')
        assert hits == [], _rules(hits)

    def test_sequence_flow_refs_are_not_a_surface_because_the_id_itself_is(self, tmp_path: Path) -> None:
        """`sourceRef`/`targetRef` repeat an id that is inspected where declared.

        Excluding them loses no coverage — the declaration still fires — and
        avoids counting one inverted name once per arrow that points at it.
        """
        referencing = _scan_bpmn(
            tmp_path,
            '<bpmn:sequenceFlow id="Flow_2" sourceRef="GW_A" targetRef="ST_ReconcilePayment"/>',
        )
        assert referencing == [], _rules(referencing)

        declaring = _scan_bpmn(tmp_path, '<bpmn:serviceTask id="ST_ReconcilePayment"/>')
        assert "R1/ciclo-recorrente:ReconcilePayment" in _rules(declaring)

    def test_dmn_input_label_is_a_surface(self, tmp_path: Path) -> None:
        """`label` is DMN's `name`; `recurso_sla.dmn:52` depends on it being read."""
        path = tmp_path / "labelled.dmn"
        path.write_text(
            _DMN_WRAPPER.format(fragment="").replace(
                '<input id="in1">',
                '<input id="in1" label="fail-safe data_ciencia_glosa">',
            ),
            encoding="utf-8",
        )
        report = Report()
        hits = scan_xml_file(path, report)
        assert report.ok, [f.message for f in report.findings]
        assert "R1/ancora-kpi:data_ciencia_glosa" in _rules(hits)

    def test_malformed_xml_is_an_error_not_a_skip(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.bpmn"
        path.write_text("<bpmn:definitions><unclosed>", encoding="utf-8")
        report = Report()
        hits = scan_xml_file(path, report)
        assert hits == []
        assert not report.ok
        assert "malformed XML" in report.findings[0].message


class TestYamlSurface:
    def test_yaml_surface_reads_parsed_nodes_not_comments(self, tmp_path: Path) -> None:
        """A YAML comment in a spec manifest is the audit narrative of a finding.

        It exists to record what an artifact *said*; a fence that forbade that
        would turn the shadow-candidate manifests into non-records. What
        governs behaviour is keys and values, and only those are read.
        """
        commented = _scan_yaml(
            tmp_path, "# achado: a tabela viva emitia RECORRER nesta linha\nregra: GLOSAR\n"
        )
        assert commented == [], _rules(commented)

        valued = _scan_yaml(tmp_path, "regra: RECORRER\n")
        assert "R1/verbo-recorrente:RECORRER" in _rules(valued)

    def test_yaml_keys_are_a_surface(self, tmp_path: Path) -> None:
        hits = _scan_yaml(
            tmp_path,
            "mapeamento_topicos:\n  operadora.recurso.submit_appeal: submissao_regulatoria_ans\n",
        )
        assert "R1/ciclo-recorrente:submit_appeal" in _rules(hits)

    def test_yaml_block_scalars_are_a_surface(self, tmp_path: Path) -> None:
        hits = _scan_yaml(tmp_path, "motivo: >-\n  linha candidata a recurso do prestador\n")
        assert "R2-CORE/C11" in _rules(hits)

    def test_tier_b_does_not_run_r2_ctx(self, tmp_path: Path) -> None:
        """§6.3: a ±40-character window is meaningless inside a block scalar."""
        hits = _scan_yaml(tmp_path, "nota: interpor o recurso no prazo\n")
        assert hits == [], _rules(hits)
        assert "X1" in {r for _, r, _ in scan_line("interpor o recurso no prazo", tier=TIER_A)}
        assert "X1" not in {r for _, r, _ in scan_line("interpor o recurso no prazo", tier=TIER_B)}

    def test_malformed_yaml_is_an_error_not_a_skip(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.yaml"
        path.write_text("a: [1, 2\nb: }{\n", encoding="utf-8")
        report = Report()
        hits = scan_yaml_file(path, report)
        assert hits == []
        assert not report.ok
        assert "malformed YAML" in report.findings[0].message


class TestNoExceptionMechanism:
    """§6.2: no allowlist file, no inline waiver, no `historico:` block."""

    def test_no_per_file_exception_mechanism_exists(self) -> None:
        """Read the module's AST, not its prose — docstrings *discuss* exceptions.

        Two properties, both structural: no identifier names an exception
        mechanism, and no string literal names a configuration file the fence
        could be taught to read.
        """
        tree = ast.parse(_MODULE_SOURCE)

        docstrings = {
            doc
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            for doc in [ast.get_docstring(node, clean=False)]
            if doc is not None
        }
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        } - docstrings
        named_file = re.compile(r"^[\w.-]+\.(ya?ml|json|toml|ini|txt)$")
        for literal in literals:
            assert not named_file.match(literal), literal

        identifiers = (
            {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            | {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            }
            | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        )
        for forbidden in ("allowlist", "historico", "excecao", "waiver", "bypass", "ignore"):
            assert not [n for n in identifiers if forbidden in n.lower()], forbidden

    def test_the_fence_reads_only_the_artifact_it_was_given(self) -> None:
        """No configuration is loaded: every `read_text` is on the scanned `path`."""
        tree = ast.parse(_MODULE_SOURCE)
        readers = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in {"read_text", "read_bytes", "open"}
        ]
        assert readers
        assert all(isinstance(node.value, ast.Name) and node.value.id == "path" for node in readers), [
            ast.dump(n) for n in readers
        ]

    def test_a_historico_block_in_yaml_is_still_read(self, tmp_path: Path) -> None:
        """The hypothesis was evaluated and refused: a `historico:` key is a node."""
        hits = _scan_yaml(tmp_path, "historico:\n  roteamento_anterior: RECORRER\n")
        assert "R1/verbo-recorrente:RECORRER" in _rules(hits)

    def test_an_inline_waiver_comment_does_not_silence_a_bpmn_hit(self, tmp_path: Path) -> None:
        hits = _scan_bpmn(
            tmp_path,
            '<bpmn:task id="T_W" name="RECORRER"/> <!-- perspective: skip, ja revisado -->',
        )
        assert "R1/verbo-recorrente:RECORRER" in _rules(hits)


# ---------------------------------------------------------------------------
# 4. The live tree — the descending pin and the clean-outside proof
# ---------------------------------------------------------------------------

#: Tier A, measured on `main 35cffd3`. Every one of these is an artifact of the
#: CONTAS or RECURSO chain, and every one is rewritten by the redesign.
#: **This pin descends**: PR-3 drops the RECURSO rows, PR-4 drops the rest and
#: the mapping becomes empty — which is when `cli.py` starts calling the fence.
TIER_A_PIN: dict[str, int] = {
    "spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn": 196,
    "spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn": 52,
    "spec/processes/dmn/recurso_eligibility.dmn": 18,
    "spec/processes/dmn/recurso_admissibility.dmn": 11,
    "spec/processes/dmn/glosa_triage.dmn": 10,
    "spec/processes/dmn/recurso_sla.dmn": 7,
    "spec/processes/dmn/contas_sla.dmn": 1,
}

#: Tier B, measured on `main 35cffd3`. `action-approvals.yaml`'s four hits are
#: exactly the hanging policy surfaces of finding M5; `marina/agent.yaml:34` is the
#: `recurso_recovery_rate` KPI; the shadow-candidate's two are the field §2.4
#: already rewrites plus its `motivo`.
TIER_B_PIN: dict[str, int] = {
    "spec/policies/autonomy/action-approvals.yaml": 4,
    "spec/processes/dmn/glosa-triage-shadow-candidate.yaml": 2,
    "spec/agents/marina/agent.yaml": 1,
}

_CHAIN_FILES = frozenset(TIER_A_PIN) | frozenset(TIER_B_PIN)


def _tier_a_paths() -> list[Path]:
    return sorted((_SPEC / "processes" / "bpmn").glob("*.bpmn")) + sorted(
        (_SPEC / "processes" / "dmn").glob("*.dmn")
    )


def _tier_b_paths() -> list[Path]:
    return (
        sorted((_SPEC / "processes" / "dmn").glob("*.yaml"))
        + sorted(_SPEC.glob("agents/*/agent.yaml"))
        + sorted((_SPEC / "policies" / "autonomy").glob("*.yaml"))
    )


def _measure(paths: list[Path], scan: Callable[[Path, Report], list[Hit]]) -> dict[str, int]:
    report = Report()
    counts: dict[str, int] = {}
    for path in paths:
        hits = scan(path, report)
        if hits:
            counts[path.relative_to(_REPO_ROOT).as_posix()] = len(hits)
    assert report.ok, [f.message for f in report.findings]
    return counts


class TestLiveTree:
    def test_two_chains_hit_count_is_pinned(self) -> None:
        """The migration pin: what the fence measures on `main` today.

        The number is evidence, not a target — it exists so that the two
        chains cannot grow *more* provider vocabulary during the window
        between PR-1 (the fence exists, unwired) and PR-4 (the fence gates).
        Between those two PRs `make validate-artifacts` does not block new
        vocabulary; this test, which runs in `make test`, is what does.
        """
        assert _measure(_tier_a_paths(), scan_xml_file) == TIER_A_PIN
        assert _measure(_tier_b_paths(), scan_yaml_file) == TIER_B_PIN
        assert sum(TIER_A_PIN.values()) == 295
        assert sum(TIER_B_PIN.values()) == 7

    def test_non_chain_artifacts_are_clean(self) -> None:
        """0 hits in the 71 BPMN/DMN outside the two chains, and in every other YAML.

        This is what makes the pin trustworthy: any file appearing here would
        be a defect of the **lexicon**, not of the artifact (§6.7), and would
        have to be fixed in the lexicon before merge. It covers AUTH, PAGTO,
        REEMBOLSO, NIP, CANCEL, CRED, FRAUDE, LGPD, ANS, ADEQUACAO,
        INADIMPLENCIA, PROGRAMA and ESCALATION.
        """
        dirty_a = {name for name in _measure(_tier_a_paths(), scan_xml_file) if name not in TIER_A_PIN}
        dirty_b = {name for name in _measure(_tier_b_paths(), scan_yaml_file) if name not in TIER_B_PIN}
        assert dirty_a == set()
        assert dirty_b == set()

    def test_the_pinned_files_are_exactly_the_two_chains(self) -> None:
        expected = {
            "spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn",
            "spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn",
            "spec/processes/dmn/contas_sla.dmn",
            "spec/processes/dmn/glosa_triage.dmn",
            "spec/processes/dmn/recurso_admissibility.dmn",
            "spec/processes/dmn/recurso_eligibility.dmn",
            "spec/processes/dmn/recurso_sla.dmn",
            "spec/processes/dmn/glosa-triage-shadow-candidate.yaml",
            "spec/agents/marina/agent.yaml",
            "spec/policies/autonomy/action-approvals.yaml",
        }
        assert expected == _CHAIN_FILES

    def test_the_non_chain_corpus_is_the_size_it_should_be(self) -> None:
        """Guards the clean-outside proof against a silently shrinking corpus."""
        assert len(_tier_a_paths()) == 78
        assert len([p for p in _tier_a_paths() if p.suffix == ".bpmn"]) == 16
        assert len(_tier_a_paths()) - len(TIER_A_PIN) == 71

    def test_tier_b_hit_lines_are_pinned(self) -> None:
        """The gate's own Tier-B re-measurement, line by line."""
        report = Report()
        located = {
            (path.name, hit.line, hit.rule_id)
            for path in _tier_b_paths()
            for hit in scan_yaml_file(path, report)
        }
        assert report.ok, [f.message for f in report.findings]
        assert located == {
            ("action-approvals.yaml", 259, "ciclo-recorrente:submit_appeal"),
            ("action-approvals.yaml", 368, "ciclo-recorrente:start_recurso"),
            ("action-approvals.yaml", 649, "ciclo-recorrente:submit_appeal"),
            ("action-approvals.yaml", 667, "ciclo-recorrente:start_recurso"),
            ("glosa-triage-shadow-candidate.yaml", 337, "verbo-recorrente:RECORRER"),
            ("glosa-triage-shadow-candidate.yaml", 338, "C11"),
            ("agent.yaml", 34, "ancora-kpi:recurso_recovery_rate"),
        }


# ---------------------------------------------------------------------------
# 5. The fence is measured but NOT wired — the deliberate, visible flip
# ---------------------------------------------------------------------------


class TestFenceIsNotWiredYet:
    """PR-1 ships the fence **measured but unwired**, by design (§6.6).

    The fence cannot be switched on while the tree is dirty, and it must not
    be born with a bypass flag — a flag is the per-file allowlist of §6.2 under
    another name. So `existing` is separated from `gating`: this PR adds the
    module and its tests; **PR-4** adds the three lines of
    `src/maezo/platform/validation/cli.py` that call it, in the same PR that
    takes the pin above to zero. When that happens, these two tests are
    *expected* to fail and must be replaced by their inverse — that is what
    makes the flip deliberate and visible instead of silent.
    """

    def test_fence_is_not_wired_into_the_cli_yet(self) -> None:
        cli = (_REPO_ROOT / "src" / "maezo" / "platform" / "validation" / "cli.py").read_text(
            encoding="utf-8"
        )
        assert "perspective" not in cli

    def test_validate_artifacts_stays_green_on_the_dirty_tree(self) -> None:
        """`make validate-artifacts` must still pass at PR-1, with 302 latent hits.

        302 = `sum(TIER_A_PIN.values())` (295) + `sum(TIER_B_PIN.values())` (7)
        — the same two pinned tables `test_two_chains_hit_count_is_pinned`
        asserts against, not a number restated independently (m4, verifier
        gate review: the prior "291" here had drifted from the pin).
        """
        from maezo.platform.validation.cli import validate_artifacts

        assert sum(TIER_A_PIN.values()) + sum(TIER_B_PIN.values()) == 302
        assert validate_artifacts(["spec/processes", "spec/policies", "spec/agents"]) == 0


# ---------------------------------------------------------------------------
# 6. Entry points
# ---------------------------------------------------------------------------


class TestEntryPoints:
    def test_check_processes_root_reports_every_hit_as_a_blocking_error(self, tmp_path: Path) -> None:
        bpmn_dir = tmp_path / "bpmn"
        dmn_dir = tmp_path / "dmn"
        bpmn_dir.mkdir()
        dmn_dir.mkdir()
        (bpmn_dir / "a.bpmn").write_text(
            _BPMN_WRAPPER.format(fragment='<bpmn:task id="T" name="RECORRER"/>'), encoding="utf-8"
        )
        (dmn_dir / "b.yaml").write_text("roteamento: ACEITAR_GLOSA\n", encoding="utf-8")

        report = Report()
        perspective.check_processes_root(tmp_path, report)
        assert not report.ok
        assert len(report.findings) == 2
        assert all("provider-perspective vocabulary" in f.message for f in report.findings)

    def test_check_yaml_defs_covers_agents_and_policies(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "marina"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text("kpis:\n  - name: recurso_recovery_rate\n", encoding="utf-8")
        report = Report()
        perspective.check_agents_root(tmp_path, report)
        assert not report.ok
        assert "recurso_recovery_rate" in report.findings[0].message

    def test_a_hit_carries_file_line_rule_and_matched_text(self, tmp_path: Path) -> None:
        hits = _scan_bpmn(tmp_path, '<bpmn:task id="ST_SubmitAppeal" name="Submeter recurso"/>')
        assert len(hits) == 1
        hit = hits[0]
        assert hit.path.name == "fixture.bpmn"
        assert hit.line == 4
        assert hit.tier == TIER_A
        assert hit.rule_class == "R1"
        assert hit.rule_id == "ciclo-recorrente:SubmitAppeal"
        assert hit.matched == "ST_SubmitAppeal"
        assert "line 4" in hit.render()
