"""Billing-status 0.3.0 (manifest v1.6): o adaptador sobre os bytes REAIS vendorizados.

Decisao do dono de 14/10/2026. A revisao 0.3.0 acrescenta, SO' com `incluir_contratacao=true`, tres campos:
`resumo.tipo_contratacao`, `resumo.beneficiario_e_pagador` e `competencias[].valor_boleto_pagador`. O 0.2.0
(manifest v1.4) segue pinado e intocado, e nao pede nem le os campos novos.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from maezo.adapters.amh import billing_status as bs
from maezo.adapters.amh.contract import (
    CONTRACT_PIN_RELATIVE_PATH,
    V1_4_LOCK_KEY,
    V1_6_LOCK_KEY,
    load_contract_pin,
)
from maezo.ports.errors import PortFailureReason as Reason
from tests.unit.adapters.amh.test_billing_status import Executor, _corpo

REPO_ROOT = Path(__file__).resolve().parents[4]
OPENAPI = REPO_ROOT / "config/integrations/amh/openapi"
REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
_ARGS = {"purpose_of_use": "atendimento_whatsapp", "consent_decision_ref": "base-legal:execucao-de-contrato"}


def _bytes(nome: str) -> bytes:
    return (OPENAPI / nome).read_bytes()


def _novo(executor: Executor) -> bs.AmhBillingStatusAdapter:
    return bs.AmhBillingStatusAdapter(
        _bytes("billing-status-contratacao.openapi.yaml"), executor=executor, artifact=bs.ARTIFACT_CONTRATACAO
    )


def _corpo_real() -> dict[str, Any]:
    """O corpo sintetico dos testes do adaptador, ajustado ao schema REAL (ref no padrao e `moeda`)."""
    corpo = copy.deepcopy(_corpo())
    corpo["portable_subject_ref"] = REF
    corpo["resumo"]["moeda"] = "BRL"
    return corpo


def _corpo_novo(**resumo: Any) -> dict[str, Any]:
    corpo = _corpo_real()
    corpo["resumo"].update({"tipo_contratacao": "individual_familiar", "beneficiario_e_pagador": True})
    corpo["resumo"].update(resumo)
    for competencia in corpo["competencias"]:
        competencia["valor_boleto_pagador"] = "1280.40"
    return corpo


def test_os_tres_artefatos_sao_aceitos_cada_um_pelo_proprio_digest() -> None:
    novo = _novo(Executor())
    atendimento = bs.AmhBillingStatusAdapter(
        _bytes("billing-status-atendimento.openapi.yaml"),
        executor=Executor(),
        artifact=bs.ARTIFACT_ATENDIMENTO,
    )
    antigo = bs.AmhBillingStatusAdapter(_bytes("billing-status.openapi.yaml"), executor=Executor())
    assert (novo.artifact, atendimento.artifact, antigo.artifact) == (
        bs.ARTIFACT_CONTRATACAO,
        bs.ARTIFACT_ATENDIMENTO,
        bs.ARTIFACT,
    )
    assert bs.ARTIFACT_CONTRATACAO in bs.ARTIFACTS


@pytest.mark.parametrize(
    ("arquivo", "artifact"),
    [
        (
            "billing-status-atendimento.openapi.yaml",
            bs.ARTIFACT_CONTRATACAO,
        ),  # bytes 0.2.0 sob o caminho do 0.3.0
        (
            "billing-status-contratacao.openapi.yaml",
            bs.ARTIFACT_ATENDIMENTO,
        ),  # bytes 0.3.0 sob o caminho do 0.2.0
        ("billing-status-contratacao.openapi.yaml", bs.ARTIFACT),
    ],
)
def test_bytes_trocados_recusam(arquivo: str, artifact: str) -> None:
    with pytest.raises(bs.BillingStatusContractError):
        bs.AmhBillingStatusAdapter(_bytes(arquivo), executor=Executor(), artifact=artifact)


async def test_o_0_3_0_pede_incluir_contratacao_e_os_antigos_nao() -> None:
    executor_novo = Executor()
    executor_novo.body = _corpo_novo()
    await _novo(executor_novo).get_billing_status(REF, **_ARGS)
    [pedido] = executor_novo.calls
    assert dict(pedido.query) == {
        "purpose_of_use": "atendimento_whatsapp",
        "janela_meses": 12,
        "incluir_contratacao": "true",
    }

    executor_atendimento = Executor()
    atendimento = bs.AmhBillingStatusAdapter(
        _bytes("billing-status-atendimento.openapi.yaml"),
        executor=executor_atendimento,
        artifact=bs.ARTIFACT_ATENDIMENTO,
    )
    await atendimento.get_billing_status(REF, **_ARGS)
    assert "incluir_contratacao" not in dict(executor_atendimento.calls[0].query)


async def test_resposta_do_0_3_0_traz_os_tres_campos_na_visao() -> None:
    executor = Executor()
    executor.body = _corpo_novo()
    resultado = await _novo(executor).get_billing_status(REF, **_ARGS)
    assert resultado.succeeded, resultado.failure
    visao = resultado.value
    assert visao is not None
    assert visao.resumo.tipo_contratacao == "individual_familiar"
    assert visao.resumo.eh_o_pagador is True
    assert [c.valor_boleto_pagador for c in visao.competencias] == ["1280.40"]


async def test_adesao_com_campos_nulos_e_valida() -> None:
    executor = Executor()
    executor.body = _corpo_novo(
        tipo_contratacao="coletivo_adesao", beneficiario_e_pagador=None, pagador_tipo="desconhecido"
    )
    for competencia in executor.body["competencias"]:
        competencia["valor_boleto_pagador"] = None
    resultado = await _novo(executor).get_billing_status(REF, **_ARGS)
    assert resultado.succeeded, resultado.failure
    assert resultado.value is not None
    assert resultado.value.resumo.tipo_contratacao == "coletivo_adesao"
    assert resultado.value.resumo.eh_o_pagador is None
    assert resultado.value.competencias[0].valor_boleto_pagador is None


async def test_resposta_sem_os_campos_novos_tambem_e_valida_e_fica_none() -> None:
    """Os campos sao opcionais no schema: uma resposta sem eles (servidor antigo) segue valida."""
    executor = Executor()
    executor.body = _corpo_real()
    resultado = await _novo(executor).get_billing_status(REF, **_ARGS)
    assert resultado.succeeded, resultado.failure
    assert resultado.value is not None
    assert resultado.value.resumo.tipo_contratacao is None and resultado.value.resumo.eh_o_pagador is None
    assert resultado.value.competencias[0].valor_boleto_pagador is None


@pytest.mark.parametrize(
    "mutar",
    [
        lambda c: c["resumo"].update(tipo_contratacao="Coletivo"),  # fora do enum
        lambda c: c["resumo"].update(beneficiario_e_pagador="sim"),
        lambda c: c["competencias"][0].update(valor_boleto_pagador="1280.4"),  # fora da forma 0.00
        lambda c: c["competencias"][0].update(valor_boleto_pagador=1280.4),
        lambda c: c["resumo"].update(cpf="00000000000"),  # campo fora do contrato fechado
        lambda c: c["competencias"][0].update(nosso_numero="123"),
    ],
)
async def test_resposta_fora_do_contrato_e_recusada(mutar: Any) -> None:
    executor = Executor()
    executor.body = _corpo_novo()
    mutar(executor.body)
    resultado = await _novo(executor).get_billing_status(REF, **_ARGS)
    assert resultado.failure is not None and resultado.failure.reason is Reason.CONTRACT_VIOLATION


async def test_o_0_2_0_continua_rejeitando_campo_novo_na_resposta() -> None:
    """Quem pina o 0.2.0 valida `additionalProperties:false`: o servidor so' manda com o parametro."""
    executor = Executor()
    executor.body = _corpo_novo()
    atendimento = bs.AmhBillingStatusAdapter(
        _bytes("billing-status-atendimento.openapi.yaml"), executor=executor, artifact=bs.ARTIFACT_ATENDIMENTO
    )
    resultado = await atendimento.get_billing_status(REF, **_ARGS)
    assert resultado.failure is not None and resultado.failure.reason is Reason.CONTRACT_VIOLATION


def test_lock_sem_o_bloco_v1_6_o_0_3_0_recusa(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lock = json.loads((REPO_ROOT / CONTRACT_PIN_RELATIVE_PATH).read_text(encoding="utf-8"))
    lock.pop(V1_6_LOCK_KEY)
    sem_v1_6 = tmp_path / "contracts.lock.json"
    sem_v1_6.write_text(json.dumps(lock), encoding="utf-8")
    pin = load_contract_pin(sem_v1_6)
    assert bs.ARTIFACT_CONTRATACAO not in pin.artifact_digests and pin.manifest_v1_6_digest is None
    monkeypatch.setattr(bs, "load_contract_pin", lambda path=None: pin)
    with pytest.raises(bs.BillingStatusContractError):
        _novo(Executor())
    # o 0.2.0 (bloco v1.4) segue de pe'
    bs.AmhBillingStatusAdapter(
        _bytes("billing-status-atendimento.openapi.yaml"),
        executor=Executor(),
        artifact=bs.ARTIFACT_ATENDIMENTO,
    )


def test_v1_6_sem_o_v1_4_no_lock_e_recusado_pelo_loader(tmp_path: Path) -> None:
    lock = json.loads((REPO_ROOT / CONTRACT_PIN_RELATIVE_PATH).read_text(encoding="utf-8"))
    lock.pop(V1_4_LOCK_KEY)
    caminho = tmp_path / "contracts.lock.json"
    caminho.write_text(json.dumps(lock), encoding="utf-8")
    from maezo.adapters.amh.contract import AmhContractPinError

    with pytest.raises(AmhContractPinError):
        load_contract_pin(caminho)
