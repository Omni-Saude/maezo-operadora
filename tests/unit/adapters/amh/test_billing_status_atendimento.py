"""Billing-status 0.2.0 (manifest v1.4): o adaptador sobre os bytes REAIS vendorizados das duas revisoes.

Decisao do DPO de 08/10/2026: a consulta de cobranca do atendimento do WhatsApp declara
`purpose_of_use=atendimento_whatsapp`. So' a revisao 0.2.0 aceita esse valor; a 0.1.0 (manifest v1.1)
continua pinada e intocada e recusa o valor novo ANTES de despachar (o schema do parametro e' o pinado).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maezo.adapters.amh import billing_status as bs
from maezo.adapters.amh.contract import CONTRACT_PIN_RELATIVE_PATH, V1_4_LOCK_KEY, load_contract_pin
from maezo.ports.errors import PortFailureReason as Reason
from tests.unit.adapters.amh.test_billing_status import Executor

REPO_ROOT = Path(__file__).resolve().parents[4]
OPENAPI = REPO_ROOT / "config/integrations/amh/openapi"
REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"


def _bytes(nome: str) -> bytes:
    return (OPENAPI / nome).read_bytes()


def test_os_dois_artefatos_sao_aceitos_cada_um_pelo_proprio_digest() -> None:
    novo = bs.AmhBillingStatusAdapter(
        _bytes("billing-status-atendimento.openapi.yaml"),
        executor=Executor(),
        artifact=bs.ARTIFACT_ATENDIMENTO,
    )
    antigo = bs.AmhBillingStatusAdapter(_bytes("billing-status.openapi.yaml"), executor=Executor())
    assert novo.artifact == bs.ARTIFACT_ATENDIMENTO and antigo.artifact == bs.ARTIFACT


@pytest.mark.parametrize(
    ("arquivo", "artifact"),
    [
        ("billing-status.openapi.yaml", bs.ARTIFACT_ATENDIMENTO),  # bytes 0.1.0 sob o caminho do 0.2.0
        ("billing-status-atendimento.openapi.yaml", bs.ARTIFACT),  # bytes 0.2.0 sob o caminho do 0.1.0
        ("billing-status-atendimento.openapi.yaml", "schemas/openapi/maezo/v1/qualquer.openapi.yaml"),
    ],
)
def test_bytes_trocados_ou_artefato_desconhecido_recusam(arquivo: str, artifact: str) -> None:
    with pytest.raises(bs.BillingStatusContractError):
        bs.AmhBillingStatusAdapter(_bytes(arquivo), executor=Executor(), artifact=artifact)


async def test_finalidade_atendimento_whatsapp_so_e_despachada_pelo_0_2_0() -> None:
    executor_novo = Executor()
    novo = bs.AmhBillingStatusAdapter(
        _bytes("billing-status-atendimento.openapi.yaml"),
        executor=executor_novo,
        artifact=bs.ARTIFACT_ATENDIMENTO,
    )
    await novo.get_billing_status(REF, purpose_of_use="atendimento_whatsapp", consent_decision_ref="c1")
    [pedido] = executor_novo.calls
    assert dict(pedido.query)["purpose_of_use"] == "atendimento_whatsapp"

    executor_antigo = Executor()
    antigo = bs.AmhBillingStatusAdapter(_bytes("billing-status.openapi.yaml"), executor=executor_antigo)
    recusa = await antigo.get_billing_status(
        REF, purpose_of_use="atendimento_whatsapp", consent_decision_ref="c1"
    )
    assert recusa.failure is not None and recusa.failure.reason is Reason.INVALID_REQUEST
    assert executor_antigo.calls == []  # fail-closed: nunca chega ao servidor


async def test_o_0_2_0_continua_aceitando_a_finalidade_antiga() -> None:
    executor = Executor()
    novo = bs.AmhBillingStatusAdapter(
        _bytes("billing-status-atendimento.openapi.yaml"), executor=executor, artifact=bs.ARTIFACT_ATENDIMENTO
    )
    await novo.get_billing_status(REF, purpose_of_use="sharing_amh_internal", consent_decision_ref="c1")
    assert len(executor.calls) == 1


def test_lock_sem_o_bloco_v1_4_o_0_2_0_recusa(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lock = json.loads((REPO_ROOT / CONTRACT_PIN_RELATIVE_PATH).read_text(encoding="utf-8"))
    lock.pop(V1_4_LOCK_KEY)
    sem_v1_4 = tmp_path / "contracts.lock.json"
    sem_v1_4.write_text(json.dumps(lock), encoding="utf-8")
    pin = load_contract_pin(sem_v1_4)
    assert bs.ARTIFACT_ATENDIMENTO not in pin.artifact_digests and pin.manifest_v1_4_digest is None
    monkeypatch.setattr(bs, "load_contract_pin", lambda path=None: pin)
    with pytest.raises(bs.BillingStatusContractError):
        bs.AmhBillingStatusAdapter(
            _bytes("billing-status-atendimento.openapi.yaml"),
            executor=Executor(),
            artifact=bs.ARTIFACT_ATENDIMENTO,
        )
    # o 0.1.0 (bloco v1.1) segue de pe'
    bs.AmhBillingStatusAdapter(_bytes("billing-status.openapi.yaml"), executor=Executor())
