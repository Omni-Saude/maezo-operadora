"""Unit tests for maezo.tools.workers.fraude (SP-OP-FRAUDE-001).

TDD London School: tests verify custody sealing (Merkle), accusation guard (L0-hard),
PHI detection, and inverted scoring (NEVER auto-accusation).
"""

import pytest

from maezo.gateway.custody import CustodyBundle
from maezo.tools.workers.fraude import (
    ERR_CUSTODY_NOT_SEALED,
    ERR_FRAUD_ACCUSATION_NOT_HUMAN,
    ERR_PHI_IN_CUSTODY,
    FraudeError,
    assemble_dossier,
    gather_evidence,
    intake,
    publish_completed,
    refer_to_legal,
    register_fraud_accusation,
    score_indicators,
    seal_custody_bundle,
    start_contratual,
    start_credenciamento,
)

# ---------------------------------------------------------------
# intake — neutral start
# ---------------------------------------------------------------


def test_intake_registers_case() -> None:
    result = intake(
        {
            "numero_caso": "FRAUDE-001",
            "origem_encaminhamento": "contas",
            "encaminhado_por_id": "auditor-001",
        }
    )
    assert result["caso_registrado"] is True


# ---------------------------------------------------------------
# gather_evidence
# ---------------------------------------------------------------


def test_gather_evidence_collects_refs() -> None:
    result = gather_evidence(
        {
            "numero_caso": "FRAUDE-001",
            "entidade_tipo": "prestador",
            "entidade_pseudo_id": "pseudo-p-001",
            "evidencia_refs": ["ref-001", "ref-002"],
        }
    )
    assert result["evidencia_refs"] == ["ref-001", "ref-002"]


def test_gather_evidence_empty_refs() -> None:
    result = gather_evidence(
        {
            "numero_caso": "FRAUDE-001",
            "entidade_tipo": "prestador",
            "entidade_pseudo_id": "p-p-001",
            "evidencia_refs": None,
        }
    )
    assert result["evidencia_refs"] == []


# ---------------------------------------------------------------
# score_indicators — INVERTED: NEVER auto-accusation
# ---------------------------------------------------------------


def test_score_indicators_empty_evidence() -> None:
    result = score_indicators({"evidencia_refs": []})
    assert result["score_indicadores"] == 0
    assert result["indicadores_presentes"] == []
    assert result["intensidade_investigacao"] == "LEVE"


def test_score_indicators_with_evidence() -> None:
    refs = [f"ref-{i}" for i in range(8)]  # 8 refs = score 80
    result = score_indicators({"evidencia_refs": refs})
    assert result["score_indicadores"] == 80
    assert "evidencia_presente" in result["indicadores_presentes"]
    assert "score_elevado" in result["indicadores_presentes"]
    assert result["intensidade_investigacao"] == "APROFUNDADA"


def test_score_indicators_prioritario() -> None:
    refs = [f"ref-{i}" for i in range(15)]  # 15 refs = score 150
    result = score_indicators({"evidencia_refs": refs})
    assert result["score_indicadores"] == 150
    assert "multiplos_indicadores" in result["indicadores_presentes"]
    assert result["intensidade_investigacao"] == "PRIORITARIA"


# ---------------------------------------------------------------
# assemble_dossier
# ---------------------------------------------------------------


def test_assemble_dossier() -> None:
    result = assemble_dossier(
        {
            "numero_caso": "F-001",
            "evidencia_refs": ["ref-a", "ref-b"],
            "indicadores_presentes": ["evidencia_presente"],
        }
    )
    assert result["dossie_montado"] is True
    assert result["dossie_items"] == 2


# ---------------------------------------------------------------
# seal_custody_bundle — Merkle seal
# ---------------------------------------------------------------


def test_fraud_seal_custody() -> None:
    """Seal evidence bundle produces deterministic Merkle root."""
    evidencia_refs = ["ref-001", "ref-002", "ref-003"]
    result = seal_custody_bundle(
        {
            "numero_caso": "F-001",
            "evidencia_refs": evidencia_refs,
        }
    )
    assert result["custody_sealed"] is True
    assert result["bundle_root"] is not None
    assert len(result["bundle_root"]) == 64
    assert result["record_count"] == 3

    # Verify it's a valid Merkle root
    expected = CustodyBundle.seal_bundle(evidencia_refs)
    assert result["bundle_root"] == expected


def test_fraud_seal_custody_tamper_evident() -> None:
    """Different evidence produces different Merkle roots."""
    refs_a = ["ref-a-1", "ref-a-2"]
    refs_b = ["ref-b-1", "ref-b-2"]

    root_a = seal_custody_bundle({"evidencia_refs": refs_a})["bundle_root"]
    root_b = seal_custody_bundle({"evidencia_refs": refs_b})["bundle_root"]

    assert root_a != root_b


def test_fraud_seal_custody_rejects_phi() -> None:
    """Seal must reject evidence references containing PHI markers."""
    with pytest.raises(FraudeError) as excinfo:
        seal_custody_bundle(
            {
                "evidencia_refs": ["ref-ok", "ref-com-cpf-123"],
            }
        )
    assert excinfo.value.code == ERR_PHI_IN_CUSTODY


def test_fraud_seal_custody_empty() -> None:
    result = seal_custody_bundle({"evidencia_refs": []})
    assert result["custody_sealed"] is True
    assert len(result["bundle_root"]) == 64


# ---------------------------------------------------------------
# register_fraud_accusation — L0-hard GUARD
# ---------------------------------------------------------------


def test_fraud_accusation_guard_happy_path() -> None:
    refs = ["ref-001", "ref-002"]
    bundle_root = CustodyBundle.seal_bundle(refs)

    result = register_fraud_accusation(
        {
            "decisao_fraude": "ACUSAR_FRAUDE",
            "investigator_id": "inv-001",
            "tier": "senior",
            "fundamentacao_investigacao": "Evidencia de upcoding sistematico",
            "indicadores_fundamentantes": ["upcoding_pattern", "frequency_deviation"],
            "referencia_normativa": "RN 593, Lei 9656 art. 13",
            "destino_referral": {"juridico": True, "ans": True},
            "bundle_root": bundle_root,
            "evidencia_refs": refs,
        }
    )
    assert result["acusacao_registrada"] is True
    assert result["bundle_root_verificado"] == bundle_root


def test_fraud_accusation_guard_rejects_arquivar() -> None:
    """Arquivar should NOT pass the accusation guard."""
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(
            {
                "decisao_fraude": "ARQUIVAR",
                "investigator_id": "inv-001",
                "tier": "senior",
                "fundamentacao_investigacao": "x",
                "indicadores_fundamentantes": ["x"],
                "referencia_normativa": "x",
                "destino_referral": {"juridico": True},
                "bundle_root": "fake",
                "evidencia_refs": [],
            }
        )
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN


def test_no_auto_accusation_monitorar() -> None:
    """Monitorar should NOT trigger accusation — strictly human-gated."""
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(
            {
                "decisao_fraude": "MONITORAR",
                "investigator_id": "inv-001",
                "tier": "x",
                "fundamentacao_investigacao": "x",
                "indicadores_fundamentantes": ["x"],
                "referencia_normativa": "x",
                "destino_referral": {"ans": True},
                "bundle_root": "fake",
                "evidencia_refs": [],
            }
        )
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN


def test_fraud_accusation_guard_custody_not_sealed() -> None:
    """Missing bundle_root should trigger ERR_CUSTODY_NOT_SEALED (pure custody failure)."""
    refs = ["ref-001"]
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(
            {
                "decisao_fraude": "ACUSAR_FRAUDE",
                "investigator_id": "inv-001",
                "tier": "senior",
                "fundamentacao_investigacao": "x",
                "indicadores_fundamentantes": ["x"],
                "referencia_normativa": "x",
                "destino_referral": {"juridico": True},
                "bundle_root": "",
                "evidencia_refs": refs,
            }
        )
    assert excinfo.value.code == ERR_CUSTODY_NOT_SEALED


def test_fraud_accusation_guard_tampered_bundle() -> None:
    """Tampered evidence (bundle_root mismatch) triggers custody failure."""
    original_refs = ["ref-001", "ref-002"]
    bundle_root = CustodyBundle.seal_bundle(original_refs)
    tampered_refs = ["ref-001", "ref-002", "ref-003"]  # added ref

    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(
            {
                "decisao_fraude": "ACUSAR_FRAUDE",
                "investigator_id": "inv-001",
                "tier": "senior",
                "fundamentacao_investigacao": "x",
                "indicadores_fundamentantes": ["x"],
                "referencia_normativa": "x",
                "destino_referral": {"juridico": True},
                "bundle_root": bundle_root,
                "evidencia_refs": tampered_refs,
            }
        )
    assert excinfo.value.code == ERR_CUSTODY_NOT_SEALED


def test_fraud_accusation_guard_missing_investigator() -> None:
    """Missing investigator_id should be human-gate failure."""
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(
            {
                "decisao_fraude": "ACUSAR_FRAUDE",
                "investigator_id": "",
                "tier": "senior",
                "fundamentacao_investigacao": "x",
                "indicadores_fundamentantes": ["x"],
                "referencia_normativa": "x",
                "destino_referral": {"juridico": True},
                "bundle_root": "fake",
                "evidencia_refs": [],
            }
        )
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN


# ---------------------------------------------------------------
# refer_to_legal
# ---------------------------------------------------------------


def test_refer_to_legal() -> None:
    result = refer_to_legal(
        {
            "numero_caso": "F-001",
            "destino_referral": {"juridico": True, "ans": True},
        }
    )
    assert result["referral_executado"] is True


# ---------------------------------------------------------------
# start_credenciamento
# ---------------------------------------------------------------


def test_start_credenciamento() -> None:
    result = start_credenciamento({"prestador_id": "P-001"})
    assert result["handoff_credenciamento"] is True
    assert result["processo_destino"] == "SP-OP-CRED-001"


# ---------------------------------------------------------------
# start_contratual
# ---------------------------------------------------------------


def test_start_contratual() -> None:
    result = start_contratual({"numero_contrato": "C-001"})
    assert result["handoff_contratual"] is True
    assert "CANCEL-001" in result["processo_destino"]


# ---------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------


def test_publish_completed_arquivado() -> None:
    result = publish_completed({"decisao_fraude": "ARQUIVAR"})
    assert result["desfecho"] == "arquivado_sem_indicio"


def test_publish_completed_acusado() -> None:
    result = publish_completed({"decisao_fraude": "ACUSAR_FRAUDE"})
    assert result["desfecho"] == "fraude_confirmada_humano"
