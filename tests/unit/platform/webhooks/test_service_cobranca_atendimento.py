"""Fonte de cobranca do Lucas com a finalidade do atendimento do WhatsApp (billing-status 0.2.0, v1.4).

Decisao do DPO de 08/10/2026: com o ACESSO ligado (refs verificadas), a leitura de cobranca declara
`purpose_of_use=atendimento_whatsapp` sobre o OpenAPI 0.2.0, e a fonte de consentimento devolve o
`consent_ref` que a AMH deu ao registro do consentimento do WhatsApp (escopo `atendimento_whatsapp`). Sem o
acesso, o comportamento de antes (0.1.0 + `amh_interop_purpose_of_use`). Pin e bytes REAIS, sem rede.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import maezo.platform.webhooks.service as svc
from maezo.adapters.amh import billing_status as bs
from maezo.agents.lucas.fonte_cobranca_amh import FonteCobrancaAmh
from maezo.agents.lucas.identidade_amh import BASE_LEGAL_EXECUCAO_DE_CONTRATO
from maezo.gateway.amh_interop import AmhInteropCompositionError
from maezo.platform.webhooks.whatsapp.acesso_ponte import ConsentimentoDoAcesso, RefsVerificadas
from tests.unit.platform.webhooks.test_service_roteador import _amh, _settings

REPO_ROOT = Path(__file__).resolve().parents[4]
OPENAPI = REPO_ROOT / "config/integrations/amh/openapi"
REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"


def _valores(tmp_path: Any, **sobre: Any) -> dict[str, Any]:
    return _amh(
        tmp_path,
        amh_billing_status_openapi_path=str(OPENAPI / "billing-status.openapi.yaml"),
        amh_subject_resolution_openapi_path=str(OPENAPI / "subject-resolution.openapi.yaml"),
        **sobre,
    )


def _turno(settings: Any, *, acesso: bool) -> Any:
    dispatcher, _ = svc._build_dispatcher(settings)
    if acesso:
        dispatcher.refs_verificadas = RefsVerificadas()
    return svc._build_lucas_turno(settings, dispatcher), dispatcher


async def test_acesso_ligado_a_cobranca_declara_atendimento_whatsapp_sobre_o_0_2_0(tmp_path: Path) -> None:
    settings = _settings(
        **_valores(
            tmp_path,
            acesso_beneficiario=True,
            amh_billing_status_atendimento_openapi_path=str(
                OPENAPI / "billing-status-atendimento.openapi.yaml"
            ),
        )
    )
    turno, dispatcher = _turno(settings, acesso=True)
    fonte = turno.fonte
    assert isinstance(fonte, FonteCobrancaAmh)
    assert fonte._purpose == "atendimento_whatsapp"
    assert fonte._billing.artifact == bs.ARTIFACT_ATENDIMENTO
    assert isinstance(fonte._consentimento, ConsentimentoDoAcesso)

    # A fonte de consentimento, com a finalidade nova, acha o consentimento do WhatsApp do turno verificado.
    with dispatcher.refs_verificadas.do_turno("pseudo-1", REF, "consent-ref-do-whatsapp"):
        assert await fonte._consentimento.decisao(REF, "atendimento_whatsapp") == "consent-ref-do-whatsapp"
    # Gravacao do consentimento ainda pendente: a base legal de execucao de contrato de sempre (DL-0083).
    with dispatcher.refs_verificadas.do_turno("pseudo-1", REF, None):
        assert (
            await fonte._consentimento.decisao(REF, "atendimento_whatsapp") == BASE_LEGAL_EXECUCAO_DE_CONTRATO
        )
    await turno.aclose()


async def test_acesso_desligado_mantem_o_0_1_0_e_a_finalidade_configurada(tmp_path: Path) -> None:
    settings = _settings(**_valores(tmp_path))
    turno, _ = _turno(settings, acesso=False)
    assert turno.fonte._purpose == settings.amh_interop_purpose_of_use == "sharing_amh_internal"
    assert turno.fonte._billing.artifact == bs.ARTIFACT
    await turno.aclose()


def test_acesso_ligado_sem_o_openapi_novo_recusa_compor(tmp_path: Path) -> None:
    """Config incoerente (acesso ligado, fonte AMH, sem o caminho do 0.2.0): a composicao recusa, e o
    receptor recusa servir (`_bring_up_dependencies` trata a recusa do turno do Lucas como recusa de
    servir)."""
    settings = _settings(**_valores(tmp_path, acesso_beneficiario=True))
    with pytest.raises(AmhInteropCompositionError, match="amh_billing_status_atendimento_openapi_path"):
        _turno(settings, acesso=True)


def test_a_variavel_le_o_nome_canonico(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_AMH_BILLING_STATUS_ATENDIMENTO_OPENAPI_PATH", "/app/x.yaml")
    assert _settings().amh_billing_status_atendimento_openapi_path == "/app/x.yaml"
    monkeypatch.delenv("MAEZO_AMH_BILLING_STATUS_ATENDIMENTO_OPENAPI_PATH")
    assert _settings().amh_billing_status_atendimento_openapi_path is None
