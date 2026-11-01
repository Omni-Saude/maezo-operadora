"""F-124 — fiacao da flag dark `MAEZO_INAD_CHAVE_EXIGE_CONTRATO` (decisao do DPO, #1082 opcao a).

Cada elo falha sob mutacao da fiacao:
(a) alias de env, default OFF;
(b) `_bring_up_dependencies` repassa a setting ao bootstrap E a raiz do dossie A2A;
(c) `register_default_workers` repassa ao `prepare_dossier` (sem contrato -> lacuna, nunca delega);
(d) `build_dossier_delegation_dispatcher` repassa ao handler do Fernando.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest
from tests.unit.runtime.agent_runtime.test_a2a_composition import (
    _SIGNING_KEY_ENV,
    _VALID_KEY,
    RecordingProducer,
    _dossier_deps,
)
from tests.unit.runtime.test_worker_runtime_service import _SpyHarness

from maezo.a2a import DelegationResult
from maezo.runtime.agent_runtime import a2a_composition
from maezo.runtime.worker_runtime.service import WorkerState, register_default_workers
from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings
from maezo.tools.workers.harness import ExternalTask, FakeWorkerTransport, WorkerHarness
from maezo.tools.workers.inadimplencia import make_prepare_dossier_handler

_TOPIC = "operadora.inadimplencia.prepare_dossier"
_MATRICULA = "MAT-SINTETICA-7731"
_DSN = "postgresql://maezo@localhost:5432/maezo"


class _Dispatcher:
    def __init__(self) -> None:
        self.envelopes: list[Any] = []

    async def delegate(self, envelope: Any) -> DelegationResult:
        self.envelopes.append(envelope)
        return DelegationResult.ok(envelope.task_id, f"process://{envelope.task_id}")


def _task(**overrides: Any) -> ExternalTask:
    variables: dict[str, Any] = {
        "tenant_id": "amh",
        "numero_contrato": "",
        "matricula_beneficiario": _MATRICULA,
        "tipo_plano": "individual",
        "meses_inadimplencia": 2,
    }
    variables.update(overrides)
    return ExternalTask(
        task_id="et-f124",
        topic=_TOPIC,
        process_instance_id="pi-f124",
        business_key="INAD-amh-x",
        worker_id="w",
        variables=variables,
    )


# (a) -------------------------------------------------------------------------------------------


def test_settings_alias_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAEZO_INAD_CHAVE_EXIGE_CONTRATO", raising=False)
    assert WorkerRuntimeSettings().inad_chave_exige_contrato is False
    monkeypatch.setenv("MAEZO_INAD_CHAVE_EXIGE_CONTRATO", "true")
    assert WorkerRuntimeSettings().inad_chave_exige_contrato is True


# (b) -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("flag", [False, True])
async def test_bring_up_repassa_a_setting_ao_bootstrap_e_a_raiz_do_dossie(
    monkeypatch: pytest.MonkeyPatch, flag: bool
) -> None:
    import maezo.runtime.worker_runtime.service as svc

    registrado: dict[str, Any] = {}
    raiz: dict[str, Any] = {}

    def _spy_register(_harness: Any, **kwargs: Any) -> None:
        registrado.update(kwargs)

    def _spy_raiz(**kwargs: Any) -> Any:
        raiz.update(kwargs)
        return object()

    async def _probe_ok(_sink: Any, _timeout: float) -> bool:
        return True

    monkeypatch.setattr(a2a_composition, "build_dossier_delegation_dispatcher", _spy_raiz)
    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_ok)

    settings = WorkerRuntimeSettings(DATABASE_URL=_DSN, MAEZO_INAD_CHAVE_EXIGE_CONTRATO=flag)
    state = WorkerState(settings=settings)
    try:
        await svc._bring_up_dependencies(state)
        assert registrado["inad_chave_exige_contrato"] is flag
        assert raiz["inad_chave_exige_contrato"] is flag
    finally:
        if state.harness_task is not None:
            state.harness_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.harness_task
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()


# (c) -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("flag", [False, True])
async def test_register_default_workers_repassa_ao_prepare_dossier(flag: bool) -> None:
    dispatcher = _Dispatcher()
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_default_workers(harness, dossier_dispatcher=dispatcher, inad_chave_exige_contrato=flag)  # type: ignore[arg-type]

    result = await harness._handlers[_TOPIC](_task())

    if flag:
        assert result["arrears_followup_delegated"] is False
        assert result["arrears_followup_gap"] == "missing_business_identifiers"
        assert dispatcher.envelopes == []
    else:  # OFF: comportamento de hoje — delega com o `task_id` sobre a matricula.
        assert result["arrears_followup_delegated"] is True
        (envelope,) = dispatcher.envelopes
        assert envelope.task_id == f"INAD-amh-{_MATRICULA}"


async def test_prepare_dossier_sob_a_flag_delega_com_contrato() -> None:
    dispatcher = _Dispatcher()
    handler = make_prepare_dossier_handler(dispatcher, chave_exige_contrato=True)  # type: ignore[arg-type]
    result = await handler(_task(numero_contrato="000000042"))
    assert result["arrears_followup_delegated"] is True
    (envelope,) = dispatcher.envelopes
    assert envelope.task_id == "INAD-amh-000000042"


def test_prepare_dossier_flag_exige_bool() -> None:
    with pytest.raises(TypeError, match="bool"):
        make_prepare_dossier_handler(None, chave_exige_contrato="true")  # type: ignore[arg-type]


# (d) -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("flag", [False, True])
def test_raiz_do_dossie_repassa_ao_handler_do_fernando(monkeypatch: pytest.MonkeyPatch, flag: bool) -> None:
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    capturado: dict[str, Any] = {}
    original = a2a_composition.make_fernando_handler

    def _spy(*args: Any, **kwargs: Any) -> Any:
        capturado.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(a2a_composition, "make_fernando_handler", _spy)
    a2a_composition.build_dossier_delegation_dispatcher(
        tenant="amh",
        runtime_mode="local",
        kafka_producer=RecordingProducer(),
        inad_chave_exige_contrato=flag,
        **_dossier_deps(),
    )
    assert capturado["inad_chave_exige_contrato"] is flag
