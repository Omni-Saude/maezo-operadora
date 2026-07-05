"""Unit tests for maezo.tools.workers.inadimplencia (SP-OP-INADIMPLENCIA-001).

TDD London School: tests verify the guard contracts from the SP-OP contract.
"""

import pytest

from maezo.tools.workers.inadimplencia import (
    DECISAO_ENCAMINHAR_RESCISAO,
    DECISAO_MANTER,
    DECISAO_SUSPENDER,
    ERR_CONTRACT_SUSPENSION_NOT_HUMAN,
    InadimplenciaError,
    assess_status,
    calculate_purge,
    handoff_rescisao,
    notify_beneficiario,
    register_contract_suspension,
    register_suspension,
    resolve_facts,
)

# ---------------------------------------------------------------
# resolve_facts
# ---------------------------------------------------------------


def test_resolve_facts_computes_meses_from_competencias() -> None:
    result = resolve_facts(
        {
            "competencias_em_aberto": ["2024-01", "2024-02", "2024-03"],
            "valor_total_devido_cents": 150000,
            "notificacao_previa_feita": False,
        }
    )
    assert result["meses_inadimplencia"] == 3
    assert result["valor_total_devido_cents"] == 150000


def test_resolve_facts_empty_competencias() -> None:
    result = resolve_facts(
        {
            "competencias_em_aberto": [],
            "valor_total_devido_cents": 0,
        }
    )
    assert result["meses_inadimplencia"] == 0


# ---------------------------------------------------------------
# assess_status
# ---------------------------------------------------------------


def test_assess_status_coletivo_to_human() -> None:
    result = assess_status(
        {
            "tipo_plano": "coletivo_empresarial",
            "meses_inadimplencia": 6,
            "dentro_periodo_minimo": True,
            "notificacao_previa_feita": True,
            "dentro_janela_purga": False,
        }
    )
    assert result["roteamento"] == "ANALISE_HUMANA"


def test_assess_status_pendente_notificacao() -> None:
    result = assess_status(
        {
            "tipo_plano": "individual",
            "meses_inadimplencia": 3,
            "dentro_periodo_minimo": True,
            "notificacao_previa_feita": False,
            "dentro_janela_purga": False,
        }
    )
    assert result["roteamento"] == "PENDENTE_NOTIFICACAO"


def test_assess_status_aguarda_purga() -> None:
    result = assess_status(
        {
            "tipo_plano": "individual",
            "meses_inadimplencia": 3,
            "dentro_periodo_minimo": True,
            "notificacao_previa_feita": True,
            "dentro_janela_purga": True,
        }
    )
    assert result["roteamento"] == "AGUARDA_PURGA"


def test_assess_status_segue_analise() -> None:
    result = assess_status(
        {
            "tipo_plano": "individual",
            "meses_inadimplencia": 3,
            "dentro_periodo_minimo": True,
            "notificacao_previa_feita": True,
            "dentro_janela_purga": False,
        }
    )
    assert result["roteamento"] == "SEGUE_ANALISE"


def test_assess_status_catch_all_to_human() -> None:
    """Catch-all conservador: tudo que nao casa cai em ANALISE_HUMANA."""
    result = assess_status(
        {
            "tipo_plano": "individual",
            "meses_inadimplencia": 0,
            "dentro_periodo_minimo": False,
            "notificacao_previa_feita": True,
            "dentro_janela_purga": False,
        }
    )
    assert result["roteamento"] == "ANALISE_HUMANA"


# ---------------------------------------------------------------
# calculate_purge
# ---------------------------------------------------------------


def test_calculate_purge_individual() -> None:
    result = calculate_purge({"tipo_plano": "individual"})
    assert result["prazo_purga"] == "P10D"
    assert result["periodo_minimo"] == "P60D"


def test_calculate_purge_coletivo() -> None:
    result = calculate_purge({"tipo_plano": "coletivo_empresarial"})
    assert result["prazo_purga"] == "P30D"
    assert result["periodo_minimo"] == "P90D"


# ---------------------------------------------------------------
# notify_beneficiario
# ---------------------------------------------------------------


def test_notify_beneficiario_sets_flag() -> None:
    result = notify_beneficiario(
        {
            "numero_contrato": "C-123",
            "matricula_beneficiario": "pseudo-abc",
        }
    )
    assert result["notificacao_previa_feita"] is True


# ---------------------------------------------------------------
# register_suspension / register_contract_suspension — GUARD
# ---------------------------------------------------------------


def test_inadimplencia_guard_suspension_happy_path() -> None:
    result = register_contract_suspension(
        {
            "decisao_inadimplencia": DECISAO_SUSPENDER,
            "responsavel_id": "juridico-001",
            "fundamentacao_contratual": "Art. 13 Lei 9656",
            "referencia_regulatoria": "RN 593",
            "comprovacao_notificacao_previa": "ref-notif-001",
            "comprovacao_periodo_minimo": "ref-periodo-001",
            "ja_em_rescisao_cancel": False,
            "numero_contrato": "C-123",
            "data_efeito_iso": "2024-06-01",
        }
    )
    assert result["suspensao_registrada"] is True


def test_inadimplencia_guard_rejects_wrong_decisao() -> None:
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(
            {
                "decisao_inadimplencia": DECISAO_MANTER,
                "responsavel_id": "juridico-001",
                "fundamentacao_contratual": "x",
                "referencia_regulatoria": "x",
                "comprovacao_notificacao_previa": "x",
                "comprovacao_periodo_minimo": "x",
                "ja_em_rescisao_cancel": False,
            }
        )
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert "MANTER" in excinfo.value.message


def test_inadimplencia_guard_rejects_missing_responsavel() -> None:
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(
            {
                "decisao_inadimplencia": DECISAO_SUSPENDER,
                "responsavel_id": "",
                "fundamentacao_contratual": "x",
                "referencia_regulatoria": "x",
                "comprovacao_notificacao_previa": "x",
                "comprovacao_periodo_minimo": "x",
                "ja_em_rescisao_cancel": False,
            }
        )
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert "responsavel_id" in excinfo.value.message


def test_inadimplencia_guard_rejects_missing_fundamentacao() -> None:
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(
            {
                "decisao_inadimplencia": DECISAO_SUSPENDER,
                "responsavel_id": "j-001",
                "fundamentacao_contratual": "",
                "referencia_regulatoria": "RN 593",
                "comprovacao_notificacao_previa": "x",
                "comprovacao_periodo_minimo": "x",
                "ja_em_rescisao_cancel": False,
            }
        )
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN


def test_inadimplencia_guard_rejects_ja_em_rescisao() -> None:
    """Anti-double-termination: se ja_em_rescisao_cancel, recusa suspensao."""
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(
            {
                "decisao_inadimplencia": DECISAO_SUSPENDER,
                "responsavel_id": "j-001",
                "fundamentacao_contratual": "Art. 13",
                "referencia_regulatoria": "RN 593",
                "comprovacao_notificacao_previa": "ref-001",
                "comprovacao_periodo_minimo": "ref-002",
                "ja_em_rescisao_cancel": True,
            }
        )
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert "ja_em_rescisao_cancel" in excinfo.value.message


def test_inadimplencia_register_suspension_alias() -> None:
    """register_suspension is an alias for register_contract_suspension."""
    result = register_suspension(
        {
            "decisao_inadimplencia": DECISAO_SUSPENDER,
            "responsavel_id": "j-001",
            "fundamentacao_contratual": "x",
            "referencia_regulatoria": "x",
            "comprovacao_notificacao_previa": "x",
            "comprovacao_periodo_minimo": "x",
            "ja_em_rescisao_cancel": False,
        }
    )
    assert result["suspensao_registrada"] is True


# ---------------------------------------------------------------
# handoff_rescisao — NEUTRO
# ---------------------------------------------------------------


def test_handoff_rescisao_executes_for_encaminhar() -> None:
    result = handoff_rescisao(
        {
            "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
            "numero_contrato": "C-456",
        }
    )
    assert result["handoff_executado"] is True
    assert result["processo_destino"] == "SP-OP-CANCEL-001"


def test_handoff_rescisao_noop_for_other_decisao() -> None:
    result = handoff_rescisao(
        {
            "decisao_inadimplencia": DECISAO_MANTER,
        }
    )
    assert result["handoff_executado"] is False
