"""CONTAS->PAGTO: pagamento perdido da 2a conta do mesmo lote (decisao do dono de 2026-11-01, opcao A).

O defeito (confirmado por verificador cego): `contas._pagto_business_key` e'
`PAGTO-{t}-{lote}-{prestador}`, sem guia/conta; CONTAS admite uma instancia por guia/conta dividindo
o lote; PAGTO e' `PERMANENT` sem comparar payload. A 2a conta batia no claim da 1a, o handoff
registrava `pagto_already_existed=True` como sucesso e a conta ficava SEM ordem de pagamento.

O que este arquivo fixa:

* `escrita_alvo` (a chave por conta, dentro da janela do ADR-0068): duas contas do mesmo prestador
  no mesmo lote -> DUAS ordens; reenvio da MESMA conta -> UMA ordem (dedup estrito preservado);
  forma lote inalterada; guia com `-` recusada antes de qualquer efeito; leitura dupla da chave
  legada (ordem legada desta conta devolvida sem start; de outra conta destrava; indeterminada recusa).
* `legado` (padrao): a chave nao muda — o defeito persiste ATE a janela —, mas a 2a conta deixa de
  ser sucesso silencioso e vira recusa (incidente auditado); a mesma conta segue deduplicada.
* `leitura_dupla`: escreve a legada, mas antes procura a ordem desta conta na chave por conta.
* a postura `PERMANENT` de PAGTO nao muda; fase fora do dominio e' recusada no boot.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from maezo.tools.mcp_cibseven.transport import (
    _START_DEDUP_POLICY,
    FakeCibSevenTransport,
    HistoricInstanceRecord,
    HistoryReadUnavailableError,
    ProcessInstance,
    StartDedupPosture,
)
from maezo.tools.workers import contas
from maezo.tools.workers.contas import (
    FASE_CHAVE_ESCRITA_ALVO,
    FASE_CHAVE_LEGADO,
    FASE_CHAVE_LEITURA_DUPLA,
    HANDOFF_PAGTO_SEEDED_KEYS,
    PAGTO_CHAVE_CONTA_FASE_SEAM,
    PAGTO_PROCESS_KEY,
    ContasHandoffPagamentoInvalidoError,
    ContasHandoffPagamentoOrdemDeOutraContaError,
    handoff_pagamento,
    register_contas_workers,
)
from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness
from tests.support.audit_fakes import FakeStartAuditSink

_LOTE = "LOTE-9"
_PRESTADOR = "prov:teste-0001"
_CHAVE_LEGADA = f"PAGTO-amh-{_LOTE}-{_PRESTADOR}"


class _MotorComHistorico(FakeCibSevenTransport):
    """O dublê do motor com a propriedade do motor real que este pacote usa: toda instancia
    iniciada aparece no historico (`/history/process-instance` lista as ATIVAS tambem), com as
    variaveis que ela recebeu. `starts` conta os starts efetivos."""

    def __init__(self) -> None:
        super().__init__()
        self.starts: list[tuple[str, dict[str, Any]]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        inst = await super().start_process_instance(process_key, business_key, variables)
        self.starts.append((business_key, dict(variables)))
        self.seed_historic_instance_record(
            HistoricInstanceRecord(
                instance_id=inst.instance_id,
                business_key=business_key,
                process_key=process_key,
                state="ACTIVE",
                end_activity_id=None,
                variables=dict(variables),
            )
        )
        return inst

    def semear_ordem(self, business_key: str, variables: dict[str, Any], *, instance_id: str) -> None:
        """Uma ordem PRE-EXISTENTE (ex.: criada antes deste pacote, sob a chave legada)."""
        self.seed_instance(
            ProcessInstance(
                instance_id=instance_id,
                process_key=PAGTO_PROCESS_KEY,
                business_key=business_key,
                state="ACTIVE",
            ),
            variables=variables,
        )
        self.seed_historic_instance_record(
            HistoricInstanceRecord(
                instance_id=instance_id,
                business_key=business_key,
                process_key=PAGTO_PROCESS_KEY,
                state="ACTIVE",
                end_activity_id=None,
                variables=dict(variables),
            )
        )


def _conta(guia: str = "G1", conta: str = "C1", **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tenant_id": "amh",
        "numero_lote_tiss": _LOTE,
        "numero_guia_tiss": guia,
        "numero_conta": conta,
        "prestador_id": _PRESTADOR,
        "competencia": "2026-05",
        "data_vencimento": "2026-07-10",
        "valor_apresentado_brl": 150.0,
        "conta_origem_ref": "conta:1",
        "instrumento_pagamento": "ted",
    }
    base.update(over)
    return base


def _handoff(
    motor: FakeCibSevenTransport, sink: FakeStartAuditSink, fase: str, **conta: Any
) -> dict[str, Any]:
    return handoff_pagamento(
        _conta(**conta), fonte_valor="apresentado", engine=motor, audit_sink=sink, fase_chave_conta=fase
    )


def _ordens_ativas(motor: FakeCibSevenTransport, chave: str) -> ProcessInstance | None:
    return asyncio.run(motor.find_active_instance(chave))


# ---------------------------------------------------------------------------
# escrita_alvo — a correcao (chave por conta)
# ---------------------------------------------------------------------------


def test_duas_contas_mesmo_prestador_mesmo_lote_geram_duas_ordens_distintas() -> None:
    """A REGRESSAO do defeito: com a chave por conta ativa, a 2a conta ganha a SUA ordem."""
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()

    primeira = _handoff(motor, sink, FASE_CHAVE_ESCRITA_ALVO, guia="G1", conta="C1")
    segunda = _handoff(motor, sink, FASE_CHAVE_ESCRITA_ALVO, guia="G1", conta="C2")

    assert primeira["pagto_business_key"] == "PAGTO-amh-C-G1-C1"
    assert segunda["pagto_business_key"] == "PAGTO-amh-C-G1-C2"
    assert primeira["pagto_already_existed"] is False
    assert segunda["pagto_already_existed"] is False
    assert primeira["pagto_instance_id"] != segunda["pagto_instance_id"]
    assert [chave for chave, _ in motor.starts] == ["PAGTO-amh-C-G1-C1", "PAGTO-amh-C-G1-C2"]
    # Duas contas nunca dividem `ordem_pagamento_id` na forma por conta.
    assert primeira["ordem_pagamento_id"] == "ORDEM-CONTA-amh-C-G1-C1"
    assert segunda["ordem_pagamento_id"] == "ORDEM-CONTA-amh-C-G1-C2"
    assert _ordens_ativas(motor, _CHAVE_LEGADA) is None, "nenhuma ordem nasce na chave legada"


def test_reenvio_da_mesma_conta_continua_deduplicado_numa_ordem_so() -> None:
    """Dedup ESTRITO preservado: a mesma conta reenviada converge na MESMA ordem."""
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()

    primeira = _handoff(motor, sink, FASE_CHAVE_ESCRITA_ALVO, guia="G1", conta="C1")
    reenvio = _handoff(motor, sink, FASE_CHAVE_ESCRITA_ALVO, guia="G1", conta="C1")

    assert len(motor.starts) == 1, "um unico start para a mesma conta"
    assert reenvio["pagto_already_existed"] is True
    assert reenvio["pagto_instance_id"] == primeira["pagto_instance_id"]
    assert reenvio["pagto_business_key"] == "PAGTO-amh-C-G1-C1"


def test_reenvio_de_conta_ja_paga_concluida_e_recusado_pelo_gate_permanente() -> None:
    """PERMANENT intocado: a ordem desta conta ja' terminou -> nada e' reiniciado."""
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()
    primeira = _handoff(motor, sink, FASE_CHAVE_ESCRITA_ALVO, guia="G1", conta="C1")
    concluida = ProcessInstance(
        instance_id=primeira["pagto_instance_id"],
        process_key=PAGTO_PROCESS_KEY,
        business_key="PAGTO-amh-C-G1-C1",
        state="COMPLETED",
    )
    motor.seed_instance(concluida)

    reenvio = _handoff(motor, sink, FASE_CHAVE_ESCRITA_ALVO, guia="G1", conta="C1")

    assert len(motor.starts) == 1
    assert reenvio["pagto_already_existed"] is True
    assert reenvio["pagto_instance_id"] == primeira["pagto_instance_id"]


def test_forma_lote_nao_muda_de_chave_em_escrita_alvo() -> None:
    """Sem guia+conta, CONTAS rodou na forma lote: a chave fica lote-escopada, como hoje."""
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()

    resultado = _handoff(motor, sink, FASE_CHAVE_ESCRITA_ALVO, guia="G1", conta="")

    assert resultado["pagto_business_key"] == _CHAVE_LEGADA
    assert resultado["ordem_pagamento_id"] == f"ORDEM-CONTA-amh-{_LOTE}-{_PRESTADOR}"


@pytest.mark.parametrize("fase", [FASE_CHAVE_ESCRITA_ALVO, FASE_CHAVE_LEITURA_DUPLA])
def test_guia_com_hifen_e_recusada_antes_de_qualquer_efeito(fase: str) -> None:
    """Decisao do dono R-101, nas duas fases da janela: nenhum claim, nenhuma leitura, nenhum start."""
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()

    with pytest.raises(ContasHandoffPagamentoInvalidoError, match="numero_guia_tiss"):
        _handoff(motor, sink, fase, guia="G-1", conta="C1")

    assert sink.dedup_keys == []
    assert motor.starts == []


def test_guia_com_hifen_segue_aceita_no_legado() -> None:
    """O padrao e' byte-identico na chave: a recusa R-101 so' vale dentro da janela."""
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()
    resultado = _handoff(motor, sink, FASE_CHAVE_LEGADO, guia="G-1", conta="C1")
    assert resultado["pagto_business_key"] == _CHAVE_LEGADA


def test_escrita_alvo_devolve_a_ordem_legada_desta_conta_sem_novo_start() -> None:
    """Leitura dupla: a ordem desta conta nasceu sob a chave legada (com a conta semeada) -> e' ela,
    nunca uma segunda ordem atravessando a virada."""
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()
    motor.semear_ordem(
        _CHAVE_LEGADA, {"numero_guia_tiss": "G1", "numero_conta": "C1"}, instance_id="pagto-legado-c1"
    )

    resultado = _handoff(motor, sink, FASE_CHAVE_ESCRITA_ALVO, guia="G1", conta="C1")

    assert motor.starts == []
    assert sink.dedup_keys == [], "nenhum claim novo: a ordem existente foi devolvida antes do gate"
    assert resultado["pagto_already_existed"] is True
    assert resultado["pagto_instance_id"] == "pagto-legado-c1"
    assert resultado["pagto_business_key"] == _CHAVE_LEGADA
    assert resultado["ordem_pagamento_id"] == f"ORDEM-CONTA-amh-{_LOTE}-{_PRESTADOR}"


def test_escrita_alvo_destrava_a_conta_cuja_chave_legada_e_de_outra_conta() -> None:
    """A conta que hoje ficaria sem ordem: a legada e' PROVADAMENTE de outra conta -> ordem propria."""
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()
    motor.semear_ordem(
        _CHAVE_LEGADA, {"numero_guia_tiss": "G1", "numero_conta": "C1"}, instance_id="pagto-legado-c1"
    )

    resultado = _handoff(motor, sink, FASE_CHAVE_ESCRITA_ALVO, guia="G1", conta="C2")

    assert [chave for chave, _ in motor.starts] == ["PAGTO-amh-C-G1-C2"]
    assert resultado["pagto_already_existed"] is False


def test_escrita_alvo_recusa_quando_a_ordem_legada_nao_tem_conta_semeada_e_a_guia_e_a_mesma() -> None:
    """Ordem anterior a este pacote (sem `numero_conta`) e mesma guia: pode ser DESTA conta. Seguir
    para a chave por conta seria arriscar pagamento em dobro -> recusa (humano reconcilia)."""
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()
    motor.semear_ordem(_CHAVE_LEGADA, {"numero_guia_tiss": "G1"}, instance_id="pagto-pre-pacote")

    with pytest.raises(ContasHandoffPagamentoOrdemDeOutraContaError, match="indeterminada"):
        _handoff(motor, sink, FASE_CHAVE_ESCRITA_ALVO, guia="G1", conta="C1")

    assert motor.starts == []


def test_escrita_alvo_ordem_legada_sem_conta_de_outra_guia_destrava() -> None:
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()
    motor.semear_ordem(_CHAVE_LEGADA, {"numero_guia_tiss": "G9"}, instance_id="pagto-pre-pacote")

    resultado = _handoff(motor, sink, FASE_CHAVE_ESCRITA_ALVO, guia="G1", conta="C1")

    assert resultado["pagto_business_key"] == "PAGTO-amh-C-G1-C1"
    assert len(motor.starts) == 1


def test_leitura_do_historico_indisponivel_falha_fechado_sem_start() -> None:
    """ "Nao sei" nunca e' "nao ha": a leitura dupla que nao se completa nao libera start."""

    class _HistoricoFora(_MotorComHistorico):
        async def resolve_historic_instance(self, *args: Any, **kwargs: Any) -> HistoricInstanceRecord:
            raise HistoryReadUnavailableError("motor fora (sintetico)")

    motor, sink = _HistoricoFora(), FakeStartAuditSink()
    with pytest.raises(HistoryReadUnavailableError):
        _handoff(motor, sink, FASE_CHAVE_ESCRITA_ALVO, guia="G1", conta="C1")
    assert motor.starts == []
    assert sink.dedup_keys == []


# ---------------------------------------------------------------------------
# legado (padrao) — chave inalterada; o sucesso silencioso vira recusa visivel
# ---------------------------------------------------------------------------


def test_legado_segunda_conta_do_lote_e_recusada_em_vez_de_sucesso_silencioso() -> None:
    """Com o padrao o DEFEITO PERSISTE (a chave nao carrega a conta) — mas nao em silencio: a 2a
    conta vira incidente auditado, e continua existindo UMA ordem so' (a da 1a conta)."""
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()

    primeira = _handoff(motor, sink, FASE_CHAVE_LEGADO, guia="G1", conta="C1")
    with pytest.raises(ContasHandoffPagamentoOrdemDeOutraContaError, match="outra"):
        _handoff(motor, sink, FASE_CHAVE_LEGADO, guia="G1", conta="C2")

    assert primeira["pagto_business_key"] == _CHAVE_LEGADA
    assert len(motor.starts) == 1, "PERMANENT intocado: a 2a conta nao abriu uma 2a ordem"


def test_legado_recusa_e_um_value_error_da_familia_incidente_nunca_bpmn_error() -> None:
    assert issubclass(ContasHandoffPagamentoOrdemDeOutraContaError, ContasHandoffPagamentoInvalidoError)
    assert issubclass(ContasHandoffPagamentoOrdemDeOutraContaError, ValueError)
    assert str(ContasHandoffPagamentoOrdemDeOutraContaError("x")).startswith(
        "ERR_CONTAS_HANDOFF_PAGAMENTO_INVALIDO"
    )


def test_legado_reenvio_da_mesma_conta_segue_sucesso_idempotente() -> None:
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()

    primeira = _handoff(motor, sink, FASE_CHAVE_LEGADO, guia="G1", conta="C1")
    reenvio = _handoff(motor, sink, FASE_CHAVE_LEGADO, guia="G1", conta="C1")

    assert len(motor.starts) == 1
    assert reenvio["pagto_already_existed"] is True
    assert reenvio["pagto_instance_id"] == primeira["pagto_instance_id"]


def test_legado_chave_e_payload_byte_identicos_a_hoje_salvo_a_conta_semeada() -> None:
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()

    resultado = _handoff(motor, sink, FASE_CHAVE_LEGADO, guia="G1", conta="C1")

    assert resultado["pagto_business_key"] == _CHAVE_LEGADA
    assert resultado["ordem_pagamento_id"] == f"ORDEM-CONTA-amh-{_LOTE}-{_PRESTADOR}"
    [(_, payload)] = motor.starts
    assert set(payload) == HANDOFF_PAGTO_SEEDED_KEYS
    assert payload["numero_conta"] == "C1"
    assert payload["numero_guia_tiss"] == "G1"


def test_legado_nao_faz_leitura_extra_antes_do_start() -> None:
    """Padrao: nenhuma leitura de historico no caminho feliz (so' o start, como hoje)."""

    class _ContaLeituras(_MotorComHistorico):
        def __init__(self) -> None:
            super().__init__()
            self.leituras = 0

        async def resolve_historic_instance(self, *args: Any, **kwargs: Any) -> HistoricInstanceRecord:
            self.leituras += 1
            return await super().resolve_historic_instance(*args, **kwargs)

    motor, sink = _ContaLeituras(), FakeStartAuditSink()
    _handoff(motor, sink, FASE_CHAVE_LEGADO, guia="G1", conta="C1")
    assert motor.leituras == 0


def test_legado_ordem_pre_pacote_forma_lote_mesma_guia_segue_sucesso() -> None:
    """Forma lote: a chave ja' prova a identidade (lote+prestador); ordem sem conta semeada e'
    `mesma` quando o pedido tambem nao tem conta."""
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()
    motor.semear_ordem(_CHAVE_LEGADA, {"numero_guia_tiss": "G1"}, instance_id="pagto-pre-pacote")

    resultado = _handoff(motor, sink, FASE_CHAVE_LEGADO, guia="G1", conta="")

    assert resultado["pagto_already_existed"] is True
    assert resultado["pagto_instance_id"] == "pagto-pre-pacote"


def test_legado_ordem_existente_que_o_historico_nao_encontra_recusa() -> None:
    """O chokepoint diz que a ordem existe, o historico nao a acha: contradicao nao prova nada."""
    motor, sink = FakeCibSevenTransport(), FakeStartAuditSink()
    motor.seed_instance(
        ProcessInstance(
            instance_id="pagto-sem-historico",
            process_key=PAGTO_PROCESS_KEY,
            business_key=_CHAVE_LEGADA,
            state="ACTIVE",
        )
    )
    with pytest.raises(ContasHandoffPagamentoOrdemDeOutraContaError, match="indeterminada"):
        _handoff(motor, sink, FASE_CHAVE_LEGADO, guia="G1", conta="C1")


def test_legado_motor_sem_leitura_de_historico_falha_fechado_no_ja_existe() -> None:
    """Seam sem `resolve_historic_instance`: a identidade nao pode ser conferida -> RuntimeError
    (retry/incidente), nunca sucesso presumido."""

    class _SemCostura:
        def __init__(self, inner: FakeCibSevenTransport) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> Any:
            if name == "resolve_historic_instance":
                raise AttributeError(name)
            return getattr(self._inner, name)

        async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
            return await self._inner.find_active_instance(business_key)

        async def find_any_instance(
            self, business_key: str, *, process_key: str = ""
        ) -> ProcessInstance | None:
            return await self._inner.find_any_instance(business_key, process_key=process_key)

        async def start_process_instance(
            self, process_key: str, business_key: str, variables: dict[str, Any]
        ) -> ProcessInstance:
            return await self._inner.start_process_instance(process_key, business_key, variables)

        async def correlate_message(self, *args: Any, **kwargs: Any) -> None:
            await self._inner.correlate_message(*args, **kwargs)

        async def get_process_status(self, business_key: str) -> Any:
            return await self._inner.get_process_status(business_key)

        async def close(self) -> None:
            return None

    inner = FakeCibSevenTransport()
    inner.seed_instance(
        ProcessInstance(
            instance_id="pagto-x", process_key=PAGTO_PROCESS_KEY, business_key=_CHAVE_LEGADA, state="ACTIVE"
        )
    )
    with pytest.raises(RuntimeError, match="resolve_historic_instance"):
        handoff_pagamento(
            _conta(),
            fonte_valor="apresentado",
            engine=_SemCostura(inner),  # type: ignore[arg-type]
            audit_sink=FakeStartAuditSink(),
        )


# ---------------------------------------------------------------------------
# leitura_dupla — escreve a legada, le antes a por conta
# ---------------------------------------------------------------------------


def test_leitura_dupla_escreve_a_chave_legada() -> None:
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()
    resultado = _handoff(motor, sink, FASE_CHAVE_LEITURA_DUPLA, guia="G1", conta="C1")
    assert resultado["pagto_business_key"] == _CHAVE_LEGADA
    assert [chave for chave, _ in motor.starts] == [_CHAVE_LEGADA]


def test_leitura_dupla_ve_a_ordem_por_conta_de_uma_replica_em_escrita_alvo() -> None:
    """Leitor antes de escritor: uma replica ainda em `leitura_dupla` enxerga a ordem que outra,
    ja' em `escrita_alvo`, criou na chave por conta — e NAO abre uma segunda na legada."""
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()
    criada = _handoff(motor, sink, FASE_CHAVE_ESCRITA_ALVO, guia="G1", conta="C1")

    reenvio = _handoff(motor, sink, FASE_CHAVE_LEITURA_DUPLA, guia="G1", conta="C1")

    assert len(motor.starts) == 1
    assert reenvio["pagto_already_existed"] is True
    assert reenvio["pagto_instance_id"] == criada["pagto_instance_id"]
    assert reenvio["pagto_business_key"] == "PAGTO-amh-C-G1-C1"
    assert reenvio["ordem_pagamento_id"] == "ORDEM-CONTA-amh-C-G1-C1"


def test_leitura_dupla_recusa_ordem_por_conta_com_identidade_divergente() -> None:
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()
    motor.semear_ordem(
        "PAGTO-amh-C-G1-C1", {"numero_guia_tiss": "G1", "numero_conta": "OUTRA"}, instance_id="pagto-colisao"
    )
    with pytest.raises(ContasHandoffPagamentoOrdemDeOutraContaError):
        _handoff(motor, sink, FASE_CHAVE_LEITURA_DUPLA, guia="G1", conta="C1")
    assert motor.starts == []


# ---------------------------------------------------------------------------
# Postura, dominio da fase e fiacao
# ---------------------------------------------------------------------------


def test_postura_de_pagto_continua_permanent() -> None:
    assert _START_DEDUP_POLICY[PAGTO_PROCESS_KEY] is StartDedupPosture.PERMANENT


def test_fase_padrao_e_legado() -> None:
    import inspect

    assert inspect.signature(handoff_pagamento).parameters["fase_chave_conta"].default == FASE_CHAVE_LEGADO
    assert PAGTO_CHAVE_CONTA_FASE_SEAM == "pagto_chave_conta_fase"


def test_fase_fora_do_dominio_e_recusada_no_handoff() -> None:
    with pytest.raises(ValueError, match="fora do dominio"):
        _handoff(_MotorComHistorico(), FakeStartAuditSink(), "so_alvo")


def test_fase_fora_do_dominio_e_recusada_no_registro() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="pagto-chave-probe")
    with pytest.raises(ValueError, match="fora do dominio"):
        register_contas_workers(harness, None, **{PAGTO_CHAVE_CONTA_FASE_SEAM: "escrita"})


@pytest.mark.parametrize(
    ("seams", "chave_esperada"),
    [
        ({}, _CHAVE_LEGADA),
        ({PAGTO_CHAVE_CONTA_FASE_SEAM: FASE_CHAVE_LEGADO}, _CHAVE_LEGADA),
        ({PAGTO_CHAVE_CONTA_FASE_SEAM: FASE_CHAVE_LEITURA_DUPLA}, _CHAVE_LEGADA),
        ({PAGTO_CHAVE_CONTA_FASE_SEAM: FASE_CHAVE_ESCRITA_ALVO}, "PAGTO-amh-C-G1-C1"),
    ],
)
def test_register_contas_workers_repassa_a_fase_ao_handoff(
    seams: dict[str, str], chave_esperada: str
) -> None:
    """Fiacao (falha sob mutacao): o worker REGISTRADO usa a fase recebida pelo seam."""
    motor, sink = _MotorComHistorico(), FakeStartAuditSink()
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="pagto-chave-probe")
    register_contas_workers(harness, None, engine=motor, audit_sink=sink, **seams)
    worker = harness.registry.get("operadora.contas.handoff_pagamento")
    assert worker is not None

    resultado = worker.execute({**_conta(), "fonte_valor": "apresentado"})

    assert resultado["pagto_business_key"] == chave_esperada


def test_fetch_scope_do_handoff_traz_a_identidade_da_conta() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="pagto-chave-probe")
    register_contas_workers(harness, None)
    escopo = set(harness._topic_variables["operadora.contas.handoff_pagamento"] or ())
    assert {"numero_guia_tiss", "numero_conta", "numero_lote_tiss", "prestador_id"} <= escopo


def test_numero_conta_nao_e_variavel_phi_lida_pela_costura() -> None:
    """A costura de #1167 recusa nomes PHI antes da rede; a identidade da conta tem de passar."""
    from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS

    assert not ({"numero_guia_tiss", "numero_conta"} & PHI_PROCESS_VARS)
    assert contas._IDENTIDADE_DA_CONTA == ("numero_guia_tiss", "numero_conta")


def test_historico_variavel_conta_vazia_e_forma_lote() -> None:
    """Ordem semeada por este pacote na forma lote carrega `numero_conta=""` (presente, vazio)."""
    veredito = contas._comparar_identidade_da_conta(
        {"numero_guia_tiss": "G1", "numero_conta": ""}, numero_guia_tiss="G1", numero_conta=""
    )
    assert veredito == "mesma"
    assert (
        contas._comparar_identidade_da_conta(
            {"numero_guia_tiss": "G1", "numero_conta": ""}, numero_guia_tiss="G1", numero_conta="C1"
        )
        == "outra"
    )
    record = HistoricInstanceRecord(
        instance_id="i", business_key="k", process_key=PAGTO_PROCESS_KEY, state="ACTIVE", end_activity_id=None
    )
    assert replace(record, variables={}).variables == {}
