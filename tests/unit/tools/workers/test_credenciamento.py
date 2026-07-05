"""Unit tests for maezo.tools.workers.credenciamento (SP-OP-CRED-001).

TDD London School: tests verify both adverse directions (decred + denial).
"""

import pytest

from maezo.tools.workers.credenciamento import (
    ERR_CRED_DENIAL_NOT_HUMAN,
    ERR_DECRED_NOT_HUMAN,
    CredError,
    assess_admissibility,
    notify_prestador,
    register_cred_denial,
    register_decred,
    register_descredenciamento,
    validate_cred,
)

# ---------------------------------------------------------------
# validate_cred
# ---------------------------------------------------------------


def test_validate_cred_returns_facts() -> None:
    result = validate_cred(
        {
            "prestador_id": "P-001",
            "tipo_prestador": "hospital",
            "documentos_refs": {"licenca": "ref-lic-001"},
        }
    )
    assert result["licenca_valida"] is True
    assert result["documentacao_completa"] is True


def test_validate_cred_empty_docs() -> None:
    result = validate_cred(
        {
            "prestador_id": "P-002",
            "documentos_refs": {},
        }
    )
    assert result["documentacao_completa"] is False


# ---------------------------------------------------------------
# assess_admissibility
# ---------------------------------------------------------------


def test_assess_admissibility_pendente_docs() -> None:
    result = assess_admissibility(
        {
            "direcao": "credenciamento",
            "documentacao_completa": False,
            "licenca_valida": True,
            "dentro_criterios_rede": True,
            "indicio_irregularidade_sinalizado": False,
        }
    )
    assert result["roteamento"] == "PENDENTE_DOCUMENTACAO"


def test_assess_admissibility_analise_humana_licenca() -> None:
    result = assess_admissibility(
        {
            "direcao": "credenciamento",
            "documentacao_completa": True,
            "licenca_valida": False,
            "dentro_criterios_rede": True,
            "indicio_irregularidade_sinalizado": False,
        }
    )
    assert result["roteamento"] == "ANALISE_HUMANA"


def test_assess_admissibility_descredenciamento() -> None:
    result = assess_admissibility(
        {
            "direcao": "descredenciamento",
            "documentacao_completa": True,
            "licenca_valida": True,
            "dentro_criterios_rede": True,
            "indicio_irregularidade_sinalizado": False,
        }
    )
    assert result["roteamento"] == "ANALISE_DESCREDENCIAMENTO"


def test_assess_admissibility_irregularidade_route() -> None:
    result = assess_admissibility(
        {
            "direcao": "credenciamento",
            "documentacao_completa": True,
            "licenca_valida": True,
            "dentro_criterios_rede": True,
            "indicio_irregularidade_sinalizado": True,
        }
    )
    assert result["roteamento"] == "ANALISE_DESCREDENCIAMENTO"


def test_assess_admissibility_catch_all() -> None:
    result = assess_admissibility(
        {
            "direcao": "invalida",
            "documentacao_completa": True,
            "licenca_valida": True,
            "dentro_criterios_rede": True,
            "indicio_irregularidade_sinalizado": False,
        }
    )
    assert result["roteamento"] == "ANALISE_HUMANA"


# ---------------------------------------------------------------
# notify_prestador
# ---------------------------------------------------------------


def test_notify_prestador() -> None:
    result = notify_prestador({"prestador_id": "P-001"})
    assert result["notificacao_previa_feita"] is True


# ---------------------------------------------------------------
# register_decred — GUARD (direcao A: descredenciamento)
# ---------------------------------------------------------------


def test_cred_guard_decred_happy_path() -> None:
    result = register_descredenciamento(
        {
            "decisao_cred": "DESCREDENCIAR",
            "responsavel_id": "gestor-001",
            "fundamentacao": "Violacao contratual",
            "referencia_regulatoria": "RN 567",
            "comprovacao_notificacao_previa": "ref-notif-001",
            "tem_beneficiarios_vinculados": False,
            "prestador_id": "P-001",
        }
    )
    assert result["descredenciamento_registrado"] is True


def test_cred_guard_rejects_wrong_decisao() -> None:
    with pytest.raises(CredError) as excinfo:
        register_descredenciamento(
            {
                "decisao_cred": "MANTER",
                "responsavel_id": "x",
                "fundamentacao": "x",
                "referencia_regulatoria": "x",
                "comprovacao_notificacao_previa": "x",
            }
        )
    assert excinfo.value.code == ERR_DECRED_NOT_HUMAN


def test_cred_guard_rejects_missing_plano_substituicao() -> None:
    """Se ha beneficiarios vinculados, plano_substituicao e obrigatorio (RN 567)."""
    with pytest.raises(CredError) as excinfo:
        register_descredenciamento(
            {
                "decisao_cred": "DESCREDENCIAR",
                "responsavel_id": "gestor-001",
                "fundamentacao": "x",
                "referencia_regulatoria": "RN 567",
                "comprovacao_notificacao_previa": "ref-001",
                "tem_beneficiarios_vinculados": True,
                "plano_substituicao": "",
            }
        )
    assert excinfo.value.code == ERR_DECRED_NOT_HUMAN
    assert "plano_substituicao" in excinfo.value.message


def test_cred_guard_register_decred_alias() -> None:
    result = register_decred(
        {
            "decisao_cred": "DESCREDENCIAR",
            "responsavel_id": "g-001",
            "fundamentacao": "x",
            "referencia_regulatoria": "x",
            "comprovacao_notificacao_previa": "x",
        }
    )
    assert result["descredenciamento_registrado"] is True


# ---------------------------------------------------------------
# register_cred_denial — GUARD (direcao B: negativa de credenciamento)
# ---------------------------------------------------------------


def test_cred_denial_happy_path() -> None:
    result = register_cred_denial(
        {
            "decisao_cred": "NEGAR_CREDENCIAMENTO",
            "responsavel_id": "gestor-002",
            "fundamentacao": "Fora dos criterios RN 566",
            "referencia_regulatoria": "RN 566",
        }
    )
    assert result["credenciamento_negado"] is True


def test_cred_denial_rejects_missing_decisao() -> None:
    with pytest.raises(CredError) as excinfo:
        register_cred_denial(
            {
                "decisao_cred": "APROVAR_CREDENCIAMENTO",
                "responsavel_id": "x",
                "fundamentacao": "x",
                "referencia_regulatoria": "x",
            }
        )
    assert excinfo.value.code == ERR_CRED_DENIAL_NOT_HUMAN


def test_cred_denial_rejects_missing_regulatoria() -> None:
    with pytest.raises(CredError) as excinfo:
        register_cred_denial(
            {
                "decisao_cred": "NEGAR_CREDENCIAMENTO",
                "responsavel_id": "g-002",
                "fundamentacao": "x",
                "referencia_regulatoria": "",
            }
        )
    assert excinfo.value.code == ERR_CRED_DENIAL_NOT_HUMAN
