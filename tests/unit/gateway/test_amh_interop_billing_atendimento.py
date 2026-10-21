"""Composicao da cobranca com a finalidade do atendimento do WhatsApp (billing-status 0.2.0, manifest v1.4).

`billing_atendimento=True` (fonte de cobranca do Lucas com o acesso ligado): so' a leitura de cobranca troca
de artefato e de finalidade; o resto da composicao fica como sempre. Sobre os bytes REAIS vendorizados e o
pin real, sem rede.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from maezo.adapters.amh import billing_status as bs
from maezo.gateway.amh_interop import (
    CONSENT_SCOPE,
    PURPOSE_ATENDIMENTO_WHATSAPP,
    AmhInteropCompositionError,
)
from maezo.gateway.tool_registry import build_amh_interop
from tests.unit.gateway.test_amh_interop import PURPOSE, SECRET, Audit, _seam, _settings

REPO_ROOT = Path(__file__).resolve().parents[3]
OPENAPI = REPO_ROOT / "config/integrations/amh/openapi"


def _reais(tmp_path: Any, **sobre: Any) -> Any:
    reais: dict[str, Any] = {
        "amh_billing_status_openapi_path": str(OPENAPI / "billing-status.openapi.yaml"),
        "amh_subject_resolution_openapi_path": str(OPENAPI / "subject-resolution.openapi.yaml"),
    }
    return _settings(tmp_path, **{**reais, **sobre})


def test_a_finalidade_nova_e_o_escopo_do_consentimento() -> None:
    assert PURPOSE_ATENDIMENTO_WHATSAPP == CONSENT_SCOPE == "atendimento_whatsapp"


def test_composicao_de_sempre_le_o_0_1_0_com_a_finalidade_configurada(tmp_path: Path) -> None:
    interop = build_amh_interop(
        settings=_reais(tmp_path), seam=_seam(), audit=Audit(), agent_version="lucas@v0"
    )
    assert interop.billing.artifact == bs.ARTIFACT
    assert interop.billing_purpose_of_use == interop.purpose_of_use == PURPOSE
    assert interop._executors[0]._purpose == PURPOSE


def test_atendimento_le_o_0_2_0_e_so_a_cobranca_declara_atendimento_whatsapp(tmp_path: Path) -> None:
    settings = _reais(
        tmp_path,
        amh_billing_status_openapi_path=None,  # o 0.1.0 nao e' lido neste caminho
        amh_billing_status_atendimento_openapi_path=str(OPENAPI / "billing-status-atendimento.openapi.yaml"),
    )
    interop = build_amh_interop(
        settings=settings, seam=_seam(), audit=Audit(), agent_version="lucas@v0", billing_atendimento=True
    )
    assert interop.billing.artifact == bs.ARTIFACT_ATENDIMENTO
    assert interop.billing_purpose_of_use == "atendimento_whatsapp"
    billing_executor, subjects_executor = interop._executors
    assert billing_executor._purpose == "atendimento_whatsapp"
    # resolucao (e o resto) seguem com a finalidade configurada
    assert subjects_executor._purpose == PURPOSE == interop.purpose_of_use


def test_atendimento_sem_o_openapi_novo_recusa_com_o_nome(tmp_path: Path) -> None:
    with pytest.raises(AmhInteropCompositionError) as exc:
        build_amh_interop(
            settings=_reais(tmp_path),
            seam=_seam(),
            audit=Audit(),
            agent_version="lucas@v0",
            billing_atendimento=True,
        )
    assert "amh_billing_status_atendimento_openapi_path" in str(exc.value)
    assert SECRET not in str(exc.value)


def test_atendimento_com_os_bytes_do_0_1_0_recusa_pelo_contrato(tmp_path: Path) -> None:
    settings = _reais(
        tmp_path, amh_billing_status_atendimento_openapi_path=str(OPENAPI / "billing-status.openapi.yaml")
    )
    with pytest.raises(bs.BillingStatusContractError):
        build_amh_interop(
            settings=settings, seam=_seam(), audit=Audit(), agent_version="lucas@v0", billing_atendimento=True
        )
