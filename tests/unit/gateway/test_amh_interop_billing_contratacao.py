"""Composicao da cobranca com o billing-status 0.3.0 (manifest v1.6, decisao do dono de 14/10/2026).

Com `amh_billing_status_contratacao_openapi_path` configurado e `billing_atendimento=True`, a leitura de
cobranca passa a ler o 0.3.0 (que pede `incluir_contratacao=true`); sem o caminho, e' o 0.2.0 de sempre.
Sobre os bytes REAIS vendorizados e o pin real, sem rede. O executor deixa passar so' o parametro novo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from maezo.adapters.amh import billing_status as bs
from maezo.gateway.amh_interop import AmhBillingStatusExecutor
from maezo.gateway.tool_registry import build_amh_interop
from maezo.ports.errors import PortFailureReason as Reason
from tests.unit.gateway.test_amh_interop import (
    ORIGIN,
    PURPOSE,
    Audit,
    Servidor,
    _comum,
    _seam,
    _settings,
)
from tests.unit.gateway.test_amh_interop import relogio as relogio  # noqa: F401  (fixture)
from tests.unit.gateway.test_amh_interop import servidor as servidor  # noqa: F401  (fixture)
from tests.unit.gateway.test_amh_interop import tokens as tokens  # noqa: F401  (fixture)

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
OPENAPI = REPO_ROOT / "config/integrations/amh/openapi"
REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
PATH = f"/interop/billing-status/v1/subjects/{REF}/billing/status"  # como o servidor a ve
PATH_NO_FIO = (
    f"/interop/billing-status/v1/subjects/{quote(REF, safe='')}/billing/status"  # como o executor a recebe
)


def _atendimento(tmp_path: Path, **sobre: Any) -> Any:
    base = {
        "amh_billing_status_openapi_path": None,
        "amh_subject_resolution_openapi_path": str(OPENAPI / "subject-resolution.openapi.yaml"),
        "amh_billing_status_atendimento_openapi_path": str(
            OPENAPI / "billing-status-atendimento.openapi.yaml"
        ),
    }
    return _settings(tmp_path, **{**base, **sobre})


def _corpo() -> dict[str, Any]:
    return {
        "portable_subject_ref": REF,
        "as_of": "2026-10-14T12:00:00Z",
        "fonte_atualizada_em": "2026-10-13T09:00:00Z",
        "resumo": {
            "status_conciliado": False,
            "ciclos_sem_conciliacao": 1,
            "valor_em_aberto": "320.25",
            "moeda": "BRL",
            "dias_atraso_max": 5,
            "pagador_tipo": "pessoa_fisica",
            "criterio_conciliacao": "situacao_paga_ou_liquidada",
            "tipo_contratacao": "individual_familiar",
            "beneficiario_e_pagador": True,
        },
        "competencias": [
            {
                "competencia": "2026-10",
                "parcela": 10,
                "vencimento": "2026-10-10",
                "situacao": "vencida",
                "valor_total": "320.25",
                "valor_coparticipacao": "0.00",
                "valor_saldo": "320.25",
                "liquidado_em": None,
                "boleto": {"numero_mascarado": "****4821", "disponivel_online": None},
                "valor_boleto_pagador": "1280.40",
            }
        ],
        "qualidade": {"campos_ausentes": []},
    }


def test_sem_o_caminho_novo_a_cobranca_do_atendimento_segue_no_0_2_0(tmp_path: Path) -> None:
    interop = build_amh_interop(
        settings=_atendimento(tmp_path),
        seam=_seam(),
        audit=Audit(),
        agent_version="lucas@v0",
        billing_atendimento=True,
    )
    assert interop.billing.artifact == bs.ARTIFACT_ATENDIMENTO


def test_com_o_caminho_novo_a_cobranca_do_atendimento_le_o_0_3_0(tmp_path: Path) -> None:
    settings = _atendimento(
        tmp_path,
        amh_billing_status_contratacao_openapi_path=str(OPENAPI / "billing-status-contratacao.openapi.yaml"),
        amh_billing_status_atendimento_openapi_path=None,  # o 0.2.0 nao e' lido neste caminho
    )
    interop = build_amh_interop(
        settings=settings, seam=_seam(), audit=Audit(), agent_version="lucas@v0", billing_atendimento=True
    )
    assert interop.billing.artifact == bs.ARTIFACT_CONTRATACAO
    assert interop.billing_purpose_of_use == "atendimento_whatsapp"


def test_o_caminho_novo_so_vale_na_cobranca_do_atendimento(tmp_path: Path) -> None:
    """Sem `billing_atendimento` (acesso desligado) o caminho novo e' ignorado: a cobranca e' a de sempre."""
    settings = _atendimento(
        tmp_path,
        amh_billing_status_openapi_path=str(OPENAPI / "billing-status.openapi.yaml"),
        amh_billing_status_contratacao_openapi_path=str(OPENAPI / "billing-status-contratacao.openapi.yaml"),
    )
    interop = build_amh_interop(settings=settings, seam=_seam(), audit=Audit(), agent_version="lucas@v0")
    assert interop.billing.artifact == bs.ARTIFACT


def test_caminho_novo_com_bytes_do_0_2_0_recusa_pelo_contrato(tmp_path: Path) -> None:
    settings = _atendimento(
        tmp_path,
        amh_billing_status_contratacao_openapi_path=str(OPENAPI / "billing-status-atendimento.openapi.yaml"),
    )
    with pytest.raises(bs.BillingStatusContractError):
        build_amh_interop(
            settings=settings, seam=_seam(), audit=Audit(), agent_version="lucas@v0", billing_atendimento=True
        )


async def test_ponta_a_ponta_pede_incluir_contratacao_e_le_os_tres_campos(
    tmp_path: Path,
    servidor: Servidor,  # noqa: F811
) -> None:
    servidor.respostas[PATH] = httpx.Response(200, json=_corpo())
    settings = _atendimento(
        tmp_path,
        amh_billing_status_contratacao_openapi_path=str(OPENAPI / "billing-status-contratacao.openapi.yaml"),
        amh_billing_status_atendimento_openapi_path=None,
    )
    interop = build_amh_interop(
        settings=settings,
        seam=_seam(),
        audit=Audit(servidor),
        agent_version="lucas@v0",
        transport_factory=servidor.factory,
        billing_atendimento=True,
    )
    resultado = await interop.billing.get_billing_status(
        REF,
        purpose_of_use="atendimento_whatsapp",
        consent_decision_ref="base-legal:execucao-de-contrato",
    )
    assert resultado.succeeded, resultado.failure
    visao = resultado.value
    assert visao is not None
    assert visao.resumo.tipo_contratacao == "individual_familiar" and visao.resumo.eh_o_pagador is True
    assert visao.competencias[0].valor_boleto_pagador == "1280.40"
    [pedido] = servidor.ao_servico()
    assert str(pedido.url).startswith(ORIGIN + PATH_NO_FIO + "?")
    assert "incluir_contratacao=true" in str(pedido.url)


@pytest.mark.parametrize(
    ("query", "aceita"),
    [
        ((("purpose_of_use", PURPOSE), ("incluir_contratacao", "true")), True),
        ((("purpose_of_use", PURPOSE), ("incluir_contratacao", "false")), True),
        ((("purpose_of_use", PURPOSE), ("incluir_contratacao", "sim")), False),
        ((("purpose_of_use", PURPOSE), ("incluir_contratacao", 1)), False),
        ((("purpose_of_use", PURPOSE), ("outro_parametro", "true")), False),
    ],
)
async def test_executor_deixa_passar_so_o_parametro_novo_valido(
    servidor: Servidor,
    tokens: Any,
    query: tuple[tuple[str, Any], ...],
    aceita: bool,  # noqa: F811
) -> None:
    from tests.unit.gateway.test_amh_interop import _pedido_billing

    executor = AmhBillingStatusExecutor(**_comum(servidor, tokens, Audit()))
    resultado = await executor.execute(_pedido_billing(path=PATH_NO_FIO, query=query))
    if aceita:
        assert resultado.succeeded, resultado.failure
    else:
        assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
        assert servidor.ao_servico() == []
