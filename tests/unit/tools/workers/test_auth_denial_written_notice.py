"""R-004 (F-016) — entrega da negativa por escrito (RN 623/2024 art. 14), DARK.

O que estes testes fixam, e por que sao negativos em sua maioria:

* Nao existe canal de entrega ao beneficiario neste build. Com a flag
  `MAEZO_AUTH_DENIAL_WRITTEN_NOTICE` (default OFF) ligada, o worker registra o fato
  honesto — a entrega e' MANUAL, pendente, responsabilidade do medico auditor — e NUNCA
  afirma que a negativa foi entregue, registrada ou enviada.
* Desligada (o padrao), a saida do worker e' identica a de antes.
* A DECISAO de negar continua exclusivamente humana (item `hard`): os dois guards rodam
  ANTES de qualquer estado de entrega e, quando recusam, nenhum estado de entrega e' escrito.

Fixtures sinteticas; nenhum dado real.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.tools.workers.auth import SendDenialNoticeWorker, register_auth_workers
from maezo.tools.workers.base import ERR_AUTH_DENIAL_INCOMPLETE, ERR_DENIAL_NOT_HUMAN
from maezo.tools.workers.harness import FakeWorkerTransport, WorkerBpmnError, WorkerHarness
from maezo.tools.workers.phi_vars import REDACTED_PHI

TOPIC = "operadora.auth.send_denial_notice"

_ENTREGA_KEYS = frozenset({"entrega_negativa_status", "entrega_negativa_responsavel"})

#: Qualquer token que afirme que a negativa chegou ao beneficiario. Nenhum pode aparecer em
#: valor nem em chave da saida — o canal nao existe.
_CLAIMS_OF_DELIVERY = ("entregue", "enviad", "delivered", "registered", "notice_sent", "sent")


def _negar(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenant_id": "tenant_sintetico",
        "decisao_auditor": "NEGAR",
        "auditor_id": "auditor-sintetico-01",
        "justificativa_clinica": "Motivo sintetico para teste",
        "cid10_referencia": "Z00.0",
        "fundamentacao_dut": "Clausula sintetica 9.9 — teste",
    }
    payload.update(overrides)
    return payload


def _assert_no_delivery_claim(result: dict[str, Any]) -> None:
    for key, value in result.items():
        lowered_key = key.lower()
        assert not any(token in lowered_key for token in ("delivered", "registered", "sent")), key
        if isinstance(value, str):
            lowered = value.lower()
            assert not any(token in lowered for token in _CLAIMS_OF_DELIVERY), (key, value)
        assert value is not True or key == "human_approved", (key, value)
    assert "status" not in result


# --- dark by default ---------------------------------------------------------------------------


def test_default_worker_output_is_unchanged() -> None:
    """Flag OFF (o padrao): nenhuma chave nova, saida identica a de antes do R-004."""
    result = SendDenialNoticeWorker().execute(_negar())
    assert set(result) == {
        "notice_type",
        "error_code",
        "event",
        "human_approved",
        "justificativa_clinica",
        "cid10_referencia",
        "fundamentacao_dut",
    }
    assert not _ENTREGA_KEYS & set(result)


def test_settings_flag_is_dark_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    monkeypatch.delenv("MAEZO_AUTH_DENIAL_WRITTEN_NOTICE", raising=False)
    assert WorkerRuntimeSettings().auth_denial_written_notice is False
    monkeypatch.setenv("MAEZO_AUTH_DENIAL_WRITTEN_NOTICE", "true")
    assert WorkerRuntimeSettings().auth_denial_written_notice is True


# --- flag ON: manual delivery, never "delivered" ------------------------------------------------


def test_without_a_channel_the_delivery_is_manual_and_pending() -> None:
    result = SendDenialNoticeWorker(written_notice=True).execute(_negar())

    assert result["entrega_negativa_status"] == "entrega_manual_pendente"
    assert result["entrega_negativa_responsavel"] == "medico_auditor"
    assert result["notice_type"] == "denial"
    assert result["human_approved"] is True
    _assert_no_delivery_claim(result)


def test_the_clinical_grounding_stays_redacted_with_the_flag_on() -> None:
    """ADR-0006: o estado de entrega nao abre caminho para texto clinico no engine."""
    result = SendDenialNoticeWorker(written_notice=True).execute(_negar())
    for field in ("justificativa_clinica", "cid10_referencia", "fundamentacao_dut"):
        assert result[field] == REDACTED_PHI
    assert "Motivo sintetico" not in repr(result)


@pytest.mark.parametrize("missing", ["justificativa_clinica", "cid10_referencia", "fundamentacao_dut"])
def test_an_incomplete_denial_gets_no_delivery_state(missing: str) -> None:
    """Sem motivo/dispositivo nao ha termo a entregar: o boundary modelado continua o desfecho."""
    with pytest.raises(WorkerBpmnError) as raised:
        SendDenialNoticeWorker(written_notice=True).execute(_negar(**{missing: "   "}))
    assert raised.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE


@pytest.mark.parametrize("auditor", [None, "", "   ", 7])
def test_a_denial_without_a_human_gets_no_delivery_state(auditor: object) -> None:
    """Item `hard`: sem decisao humana nao existe negativa — logo nada a entregar."""
    result = SendDenialNoticeWorker(written_notice=True).execute(_negar(auditor_id=auditor))
    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN
    assert not _ENTREGA_KEYS & set(result)


def test_an_approval_gets_no_denial_delivery_state() -> None:
    result = SendDenialNoticeWorker(written_notice=True).execute(_negar(decisao_auditor="APROVAR"))
    assert result["notice_type"] == "approval"
    assert not _ENTREGA_KEYS & set(result)


def test_the_flag_can_not_be_switched_on_or_faked_by_a_process_variable() -> None:
    """A flag e' da implantacao, nunca do payload: variavel forjada nao muda nada."""
    forged = _negar(
        written_notice=True,
        auth_denial_written_notice=True,
        entrega_negativa_status="entregue",
    )
    off = SendDenialNoticeWorker().execute(forged)
    assert not _ENTREGA_KEYS & set(off)
    on = SendDenialNoticeWorker(written_notice=True).execute(forged)
    assert on["entrega_negativa_status"] == "entrega_manual_pendente"
    _assert_no_delivery_claim(on)


# --- composition: the flag reaches the one owner, and only through the deployment ---------------


def _harness() -> WorkerHarness:
    return WorkerHarness(FakeWorkerTransport(), worker_id="r004-probe")


@pytest.mark.parametrize("enabled", [False, True])
def test_register_auth_workers_threads_the_flag_into_the_single_owner(enabled: bool) -> None:
    harness = _harness()
    register_auth_workers(harness, denial_written_notice=enabled)
    owner = harness.registry.get(TOPIC)
    assert type(owner) is SendDenialNoticeWorker
    assert harness.registered_topics.count(TOPIC) == 1
    assert ("entrega_negativa_status" in owner.execute(_negar())) is enabled


@pytest.mark.parametrize("junk", ["true", "false", 1, "1"])
def test_a_junk_seam_value_does_not_switch_the_flag_on(junk: object) -> None:
    """N1: so o booleano `True` liga a bandeira (`is True`, nunca truthiness)."""
    harness = _harness()
    register_auth_workers(harness, denial_written_notice=junk)
    owner = harness.registry.get(TOPIC)
    assert type(owner) is SendDenialNoticeWorker
    assert not _ENTREGA_KEYS & set(owner.execute(_negar()))


@pytest.mark.parametrize("enabled", [False, True])
def test_the_daemon_bootstrap_threads_the_flag(enabled: bool) -> None:
    from maezo.runtime.worker_runtime.service import register_default_workers

    harness = _harness()
    register_default_workers(harness, denial_written_notice=enabled)
    owner = harness.registry.get(TOPIC)
    assert type(owner) is SendDenialNoticeWorker
    assert ("entrega_negativa_status" in owner.execute(_negar())) is enabled
