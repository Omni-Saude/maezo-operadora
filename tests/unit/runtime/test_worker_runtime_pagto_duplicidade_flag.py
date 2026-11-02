"""Fiacao da flag dark `MAEZO_PAGTO_DETECT_DUPLICIDADE` (pacote PAGTO PR-E), ponta a ponta:

(a) alias de env + default OFF; (b) `_bring_up_dependencies` repassa a setting e so' monta a razao
Postgres com a flag E DATABASE_URL; (c) `register_default_workers` REAL repassa ao registro de
PAGTO — provado pela EXECUCAO dos handlers registrados (validacao e as duas liberacoes), nao por
inspecao de kwargs: ligada, a validacao ganha os fatos e as liberacoes gravam a `LIBERADA` atraves
da razao repassada; desligada (ou omitida, ou valor-lixo), saida e escopo de leitura sao os de sempre
e a razao nunca e' tocada.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from maezo.platform.pagto_obrigacao.ledger import (
    NIVEL1_SEM_OUTRA_ORDEM,
    EntradaObrigacao,
    IndiciosDuplicidade,
    PostgresRazaoObrigacoesPagamento,
)
from maezo.runtime.worker_runtime.service import WorkerState, register_default_workers
from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness
from maezo.tools.workers.pagto import register_pagto_workers
from maezo.tools.workers.pagto_duplicidade import DUPLICIDADE_SAIDAS, VALIDATE_COM_DUPLICIDADE_VARIABLES

_VALIDAR = "operadora.pagto.validate_payment_data"
_BAIXO = "operadora.pagto.release_low_value_payment"
_ALTO = "operadora.pagto.release_high_value_payment"
_ALIAS = "MAEZO_PAGTO_DETECT_DUPLICIDADE"

_ORDEM: dict[str, Any] = {
    "tenant_id": "amh",
    "ordem_pagamento_id": "ORDEM-W",
    "numero_lote_tiss": "LOTE-W",
    "prestador_id": "prov:w",
    "instrumento_pagamento": "ted",
    "valor_pagamento_cents": 50000,
    "competencia": "2026-06",
    "lastro_origem": "contas_adjudicacao_humana",
    "faixa_valor": "DENTRO_TETO_L2",
}


class _Razao:
    tenant = "amh"

    def __init__(self) -> None:
        self.registradas: list[EntradaObrigacao] = []
        self.liberadas: list[EntradaObrigacao] = []

    async def registrar_e_ler(self, entrada: EntradaObrigacao) -> IndiciosDuplicidade:
        self.registradas.append(entrada)
        return IndiciosDuplicidade(NIVEL1_SEM_OUTRA_ORDEM, "", None, True, None, "", True)

    async def registrar_liberacao(self, entrada: EntradaObrigacao) -> bool:
        self.liberadas.append(entrada)
        return True


def _dmn() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register(
        "pagto_duplicidade",
        [{"duplicidade_status": "INDETERMINADA", "duplicidade_motivo_codigo": "NIVEL_NAO_AVALIADO"}],
    )
    return dmn


def _harness() -> WorkerHarness:
    return WorkerHarness(FakeWorkerTransport(), worker_id="unit-test-pagto-duplicidade-flag")


def _executa(harness: WorkerHarness, topic: str) -> dict[str, Any]:
    worker = harness.registry.get(topic)
    assert worker is not None
    result: dict[str, Any] = worker.execute(dict(_ORDEM))
    return result


# (a) ----------------------------------------------------------------------------------------


def test_settings_alias_e_default_desligado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ALIAS, raising=False)
    assert WorkerRuntimeSettings().pagto_detect_duplicidade is False
    monkeypatch.setenv(_ALIAS, "true")
    assert WorkerRuntimeSettings().pagto_detect_duplicidade is True


# (b) ----------------------------------------------------------------------------------------


class _SpyHarness:
    def __init__(self, transport: Any, *, worker_id: str, audit_sink: Any = None, **kwargs: Any) -> None:
        del transport, worker_id, audit_sink, kwargs

    def register_worker(self, worker: Any, **_kw: Any) -> None:
        pass

    def register(self, topic: str, handler: Any, *, variables: Any = None) -> None:
        pass

    @property
    def registered_topics(self) -> list[str]:
        return []

    async def run(self) -> None:
        await asyncio.Event().wait()


@pytest.mark.parametrize(("flag", "dsn"), [(False, True), (True, True), (True, False)])
async def test_bring_up_repassa_a_flag_e_so_monta_a_razao_com_flag_e_dsn(
    monkeypatch: pytest.MonkeyPatch, flag: bool, dsn: bool
) -> None:
    import maezo.runtime.worker_runtime.service as svc

    captured: dict[str, Any] = {}

    def _spy_register(_harness: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    async def _probe_red(_sink: Any, _timeout: float) -> bool:
        return False

    monkeypatch.setenv(_ALIAS, "true" if flag else "false")
    monkeypatch.setattr(svc, "WorkerHarness", _SpyHarness)
    monkeypatch.setattr(svc, "register_default_workers", _spy_register)
    monkeypatch.setattr(svc, "_expected_worker_topics", lambda installed=False: frozenset({"t"}))
    monkeypatch.setattr(svc, "_probe_audit_sink", _probe_red)

    url = "postgresql://maezo@localhost:5432/maezo"
    state = WorkerState(settings=WorkerRuntimeSettings(DATABASE_URL=url if dsn else ""))
    try:
        await svc._bring_up_dependencies(state)
        if not dsn:
            # Sem DATABASE_URL o daemon nem chega a registrar (sem sink); quando chega, sem razao.
            assert captured.get("pagto_obrigacao_ledger") is None
            return
        assert captured["pagto_detect_duplicidade"] is flag
        razao = captured["pagto_obrigacao_ledger"]
        if flag:
            assert isinstance(razao, PostgresRazaoObrigacoesPagamento)
            assert razao.tenant == state.settings.tenant_id
        else:
            assert razao is None
    finally:
        if state.transport is not None:
            await state.transport.close()
        if state.audit_sink is not None:
            await state.audit_sink.aclose()


# (c) ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("kwargs", [{}, {"pagto_detect_duplicidade": False}])
def test_desligada_saida_de_sempre_e_razao_intocada(kwargs: dict[str, Any]) -> None:
    harness, razao = _harness(), _Razao()
    register_default_workers(harness, dmn=_dmn(), pagto_obrigacao_ledger=razao, **kwargs)  # type: ignore[arg-type]
    assert not (set(_executa(harness, _VALIDAR)) & DUPLICIDADE_SAIDAS)
    assert harness._topic_variables[_VALIDAR] is None
    assert _executa(harness, _BAIXO) == {"pagamento_executado": True, "tipo_liberacao": "auto_L2"}
    assert razao.registradas == [] and razao.liberadas == []


def test_ligada_detecta_pela_razao_e_dmn_repassadas_e_grava_a_liberacao() -> None:
    harness, razao, dmn = _harness(), _Razao(), _dmn()
    register_default_workers(
        harness,
        dmn=dmn,
        pagto_detect_duplicidade=True,
        pagto_obrigacao_ledger=razao,  # type: ignore[arg-type]
    )
    out = _executa(harness, _VALIDAR)
    assert set(out) >= DUPLICIDADE_SAIDAS
    assert out["duplicidade_status"] == "INDETERMINADA"
    assert out["duplicidade_suspeita"] is False  # rota inalterada
    assert len(razao.registradas) == 1 and dmn.calls[0][0] == "pagto_duplicidade"
    assert harness._topic_variables[_VALIDAR] == VALIDATE_COM_DUPLICIDADE_VARIABLES
    assert _executa(harness, _BAIXO) == {"pagamento_executado": True, "tipo_liberacao": "auto_L2"}
    assert [e.evento for e in razao.liberadas] == ["LIBERADA"]


def test_ligada_o_guard_de_alto_valor_continua_recusando_e_nada_e_gravado() -> None:
    harness, razao = _harness(), _Razao()
    register_default_workers(
        harness,
        dmn=_dmn(),
        pagto_detect_duplicidade=True,
        pagto_obrigacao_ledger=razao,  # type: ignore[arg-type]
    )
    with pytest.raises(Exception):  # noqa: B017 - o codigo do guard e' coberto em test_pagto_duplicidade
        _executa(harness, _ALTO)  # sem decisao humana: o guard de sempre recusa
    assert razao.liberadas == []


def test_ligada_com_o_lastro_combina_e_o_escopo_inclui_os_dois() -> None:
    harness, razao = _harness(), _Razao()
    register_default_workers(
        harness,
        dmn=_dmn(),
        pagto_resolve_lastro=True,
        pagto_detect_duplicidade=True,
        pagto_obrigacao_ledger=razao,  # type: ignore[arg-type]
    )
    out = _executa(harness, _VALIDAR)
    assert out["lastro_status"] == "INDISPONIVEL"  # sem motor: PR-D falha fechado
    assert out["duplicidade_nivel2_situacao"] == "NAO_AVALIAVEL"
    assert harness._topic_variables[_VALIDAR] == VALIDATE_COM_DUPLICIDADE_VARIABLES


def test_ligada_sem_razao_falha_fechado() -> None:
    harness = _harness()
    register_default_workers(harness, dmn=_dmn(), pagto_detect_duplicidade=True)
    out = _executa(harness, _VALIDAR)
    assert out["duplicidade_status"] == "INDETERMINADA"
    assert out["duplicidade_motivo_codigo"] == "RAZAO_INDISPONIVEL"


@pytest.mark.parametrize("lixo", ["true", 1, "1", object()])
def test_registro_de_pagto_so_liga_com_true(lixo: Any) -> None:
    harness, razao = _harness(), _Razao()
    register_pagto_workers(harness, dmn=_dmn(), pagto_detect_duplicidade=lixo, pagto_obrigacao_ledger=razao)
    assert not (set(_executa(harness, _VALIDAR)) & DUPLICIDADE_SAIDAS)
    _executa(harness, _BAIXO)
    assert razao.liberadas == []


def test_topicos_esperados_nao_mudam_com_a_flag() -> None:
    desligada, ligada = _harness(), _harness()
    register_default_workers(desligada)
    register_default_workers(ligada, pagto_detect_duplicidade=True)
    assert desligada.registered_topics == ligada.registered_topics


def test_ligada_a_liberacao_de_alto_valor_aprovada_grava_a_liberada_pela_razao_repassada() -> None:
    harness, razao = _harness(), _Razao()
    register_default_workers(
        harness,
        dmn=_dmn(),
        pagto_detect_duplicidade=True,
        pagto_obrigacao_ledger=razao,  # type: ignore[arg-type]
    )
    worker = harness.registry.get(_ALTO)
    assert worker is not None
    aprovada = {
        **_ORDEM,
        "decisao_pagamento": "APROVAR",
        "aprovador_id": "aprovador-1",
        "aprovador_tier": 4,
        "justificativa_aprovacao": "ok",
        "valor_aprovado_cents": 50000,
        "valor_maximo_aprovavel_cents": 50000,
        "tier_minimo": 2,
        "faixa_valor": "ALCADA_L2",
    }
    out = worker.execute(dict(aprovada))
    assert out == {"pagamento_liberado": True, "tipo_liberacao": "humano_alcada"}
    assert [e.evento for e in razao.liberadas] == ["LIBERADA"]
