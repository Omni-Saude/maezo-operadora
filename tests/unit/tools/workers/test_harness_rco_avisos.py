"""DL-0185 (unidade 2a): captura da composicao na tarefa, `sinaisAuditoria` na auditoria e o enfileiramento
dos avisos no harness DEPOIS de completar — FAIL-OPEN, idempotente, sem ler o indicador de efeito.

Sem engine, Postgres nem broker: `FakeWorkerTransport` + `FakeAuditSink` + o outbox em memoria e o produtor
falso de `tests/unit/platform/test_rco_avisos.py`. O pin e o sintetico 2.3.0 (`rco_k1_sintetico`, grupo
`mais_restritiva` `grupo_restritivo` = [rco_sint_d, rco_sint_c], sinal `aviso_sintetico` ->
`auditoria_medica` e `cadastro`); os destinos do pacote REAL (recorte 2.3.0) sao provados em
`test_destinos_do_recorte_real_no_harness`.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import structlog

from maezo.platform import observability, rco_avisos_relay, rco_composicao
from maezo.platform import rco_ativacao as at
from maezo.platform.rco_avisos import montar_payload
from maezo.tools.workers import _audit_ctx, rco_pin
from maezo.tools.workers._audit_ctx import (
    collect_dmn_versions,
    collect_rco_composicoes,
    record_dmn_version,
    registrar_composicao,
)
from maezo.tools.workers.dmn_transport import DmnVersion
from maezo.tools.workers.harness import ExternalTask, FakeAuditSink, FakeWorkerTransport, WorkerHarness
from tests.unit.platform import rco_k1_2_3_recorte as rec
from tests.unit.platform import rco_k1_sintetico as sint
from tests.unit.platform.test_rco_avisos import (
    SINAL_REAL,
    OutboxEmMemoria,
    ProdutorFalso,
    avisos_do_recorte,
)

TOPICO = "operadora.test.rco_aviso"
LINHA_D = {
    "decisao": "liberar",
    "idRegra": "SINT-D-0001",
    "motivoTab38": "",
    "fila": "",
    "classeAdverso": "",
    "sinalAuditoria": sint.SINAL,
}
LINHA_D_SEM_SINAL = {**LINHA_D, "sinalAuditoria": ""}
LINHA_C = {
    "decisao": "glosar",
    "idRegra": "SINT-C-0001",
    "motivoTab38": "1801",
    "fila": "cadastro",
    "classeAdverso": "administrativo",
}


def _proibido(*_a: Any, **_k: Any) -> bool:
    raise AssertionError("o caminho do aviso leu o indicador efeito_k1_habilitado")


@pytest.fixture
def pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> rco_pin.RcoRulesPin:
    sint.com_formato_2_3(tmp_path, sint.montar(tmp_path))
    carregado = rco_pin.load_rco_rules_pin(tmp_path / sint.LOCK_REL, rules_dir=tmp_path / sint.RULES_DIR)
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: carregado)
    monkeypatch.delenv(at.CHAVE_EFEITO_K1, raising=False)
    # DL-0181 (4)(b): nada neste caminho le o indicador; se ler, o teste quebra.
    monkeypatch.setattr(at, "efeito_k1_habilitado", _proibido)
    monkeypatch.setattr(rco_pin, "efeito_k1_habilitado", _proibido)
    at.limpar_cache_estados()
    return carregado


def _grupo(pin: rco_pin.RcoRulesPin) -> Any:
    return next(c for c in pin.composicoes if c.nome == "grupo_restritivo")


def _task(task_id: str = "task-1") -> ExternalTask:
    return ExternalTask(
        task_id=task_id,
        topic=TOPICO,
        process_instance_id="proc-1",
        business_key="bk-1",
        worker_id="w-1",
        variables={},
    )


def _montar(
    pin: rco_pin.RcoRulesPin,
    *,
    outbox: Any | None,
    respostas: Mapping[str, list[dict[str, Any]]] | None = None,
    registrar: bool = True,
    modo: str = "sombra",
    avisos_do_pin: Any | None = None,
) -> tuple[WorkerHarness, FakeWorkerTransport, FakeAuditSink]:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(
        transport,
        worker_id="w",
        tenant="amh",
        audit_sink=sink,
        rco_avisos=outbox,
        rco_avisos_do_pin=avisos_do_pin,
    )
    resp = respostas or {"rco_sint_d": [LINHA_D], "rco_sint_c": [LINHA_C]}

    def avaliador(decisao: str, entradas: Mapping[str, Any]) -> Any:
        linhas = copy.deepcopy(resp[decisao])
        # A sombra do montador: grava a entrada de `dmn_versions` sem ler o indicador.
        record_dmn_version(
            DmnVersion(key=decisao, id="x", version=1, deployment_id="d"), row=linhas[0], sombra=True
        )
        return linhas

    async def handler(task: ExternalTask) -> dict[str, Any]:
        resultado = rco_composicao.compor(
            _grupo(pin),
            avaliador,
            {"x": 1},
            ordem_restricao=pin.ordem_restricao,
            sinais_auditoria=pin.sinais_auditoria,
        )
        if registrar:
            registrar_composicao(resultado, modo=modo)
        return {"decisaoK1": resultado.linhas[0]["decisao"] if resultado.linhas else "revisao_humana"}

    harness.register(TOPICO, handler)
    return harness, transport, sink


@pytest.fixture
def contados(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    registro: list[tuple[str, int]] = []

    def gravar(desfecho: str, quantidade: int = 1) -> None:
        if quantidade > 0:
            registro.append((desfecho, quantidade))

    from maezo.tools.workers import harness as h

    monkeypatch.setattr(h, "record_rco_aviso", gravar)
    monkeypatch.setattr(observability, "record_rco_aviso", gravar)
    return registro


# --- captura ----------------------------------------------------------------------------------------


def test_registrar_fora_de_despacho_e_no_op(pin: rco_pin.RcoRulesPin) -> None:
    resultado = rco_composicao.ResultadoComposicao(composicao="g", desfecho="escolhido", sinais=(sint.SINAL,))
    registrar_composicao(resultado, modo="sombra")  # sem coletor: nada, sem erro


def test_registrar_acrescenta_sinais_so_as_entradas_do_grupo(pin: rco_pin.RcoRulesPin) -> None:
    with collect_dmn_versions() as versoes, collect_rco_composicoes() as composicoes:
        record_dmn_version(
            DmnVersion(key="rco_sint_d", id="x", version=1, deployment_id="d"), row=LINHA_D, sombra=True
        )
        record_dmn_version(DmnVersion(key="auth_sla", id="y", version=2, deployment_id="d"))
        antes = copy.deepcopy(versoes)
        resultado = rco_composicao.ResultadoComposicao(
            composicao="grupo_restritivo",
            desfecho="escolhido",
            avaliadas=("rco_sint_d", "rco_sint_c"),
            sinais=(sint.SINAL,),
        )
        registrar_composicao(resultado, modo="efeito")
    assert versoes["rco_sint_d"]["sinaisAuditoria"] == [sint.SINAL]
    assert versoes["auth_sla"] == antes["auth_sla"]  # tabela fora do grupo intacta
    sem_chave = {k: v for k, v in versoes["rco_sint_d"].items() if k != "sinaisAuditoria"}
    assert sem_chave == antes["rco_sint_d"]  # chave aditiva, nada mais muda
    assert composicoes["grupo_restritivo"] == _audit_ctx.ComposicaoRegistrada(
        "grupo_restritivo", (sint.SINAL,), "efeito", ("rco_sint_d", "rco_sint_c")
    )


def test_sem_sinal_a_entrada_fica_igual(pin: rco_pin.RcoRulesPin) -> None:
    with collect_dmn_versions() as versoes, collect_rco_composicoes() as composicoes:
        record_dmn_version(
            DmnVersion(key="rco_sint_d", id="x", version=1, deployment_id="d"), row=LINHA_D, sombra=True
        )
        antes = copy.deepcopy(versoes)
        registrar_composicao(
            rco_composicao.ResultadoComposicao(
                composicao="g", desfecho="escolhido", avaliadas=("rco_sint_d",)
            ),
            modo="sombra",
        )
    assert versoes == antes
    assert composicoes["g"].sinais == ()


def test_modo_invalido_nunca_levanta(pin: rco_pin.RcoRulesPin, contados: list[tuple[str, int]]) -> None:
    with collect_rco_composicoes() as composicoes:
        registrar_composicao(
            rco_composicao.ResultadoComposicao(composicao="g", desfecho="escolhido", sinais=(sint.SINAL,)),
            modo="ativa",
        )
    assert composicoes == {} and contados == [("invalido", 1)]


# --- harness ----------------------------------------------------------------------------------------


async def test_sombra_enfileira_uma_linha_por_fila_depois_de_completar(
    pin: rco_pin.RcoRulesPin, contados: list[tuple[str, int]]
) -> None:
    outbox = OutboxEmMemoria()
    ordem: list[str] = []
    original = outbox.enfileirar

    async def enfileirar(linhas: Any) -> int:
        ordem.append(f"enfileirar:{len(transport.completed)}")
        return await original(linhas)

    outbox.enfileirar = enfileirar  # type: ignore[method-assign]
    harness, transport, sink = _montar(pin, outbox=outbox)
    await harness._handle(_task())
    assert transport.completed == [("task-1", {"decisaoK1": "glosar"})]
    assert ordem == ["enfileirar:1"]  # so depois de `complete`
    assert [(r["linha"].sinal, r["linha"].fila) for r in outbox.linhas.values()] == [
        (sint.SINAL, "auditoria_medica"),
        (sint.SINAL, "cadastro"),
    ]
    assert {r["linha"].composicao for r in outbox.linhas.values()} == {"grupo_restritivo"}
    # a auditoria (gravada ANTES) leva a uniao dos sinais em cada decisao avaliada do grupo
    dmn = sink.emitted[0][0].dmn_versions
    assert dmn["rco_sint_d"]["sinaisAuditoria"] == [sint.SINAL]
    assert dmn["rco_sint_c"]["sinaisAuditoria"] == [sint.SINAL]
    assert contados == [("enfileirado", 2)]


async def test_sinal_vazio_nao_enfileira_nem_toca_a_auditoria(pin: rco_pin.RcoRulesPin) -> None:
    outbox = OutboxEmMemoria()
    harness, transport, sink = _montar(
        pin, outbox=outbox, respostas={"rco_sint_d": [LINHA_D_SEM_SINAL], "rco_sint_c": [LINHA_C]}
    )
    await harness._handle(_task())
    assert len(transport.completed) == 1 and outbox.linhas == {}
    assert all("sinaisAuditoria" not in e for e in sink.emitted[0][0].dmn_versions.values())


async def test_reentrega_da_tarefa_nao_duplica(
    pin: rco_pin.RcoRulesPin, contados: list[tuple[str, int]]
) -> None:
    outbox = OutboxEmMemoria()
    harness, transport, _ = _montar(pin, outbox=outbox)
    await harness._handle(_task())
    await harness._handle(_task())
    assert len(transport.completed) == 2
    assert len(outbox.linhas) == 2  # uma por fila, nao quatro
    assert contados == [("enfileirado", 2), ("repetido", 2)]
    await harness._handle(_task("task-2"))
    assert len(outbox.linhas) == 4  # outra tarefa, outros avisos


def _saida(transport: FakeWorkerTransport, sink: FakeAuditSink) -> tuple[Any, ...]:
    record = sink.emitted[0][0]
    return (transport.completed, transport.failures, record.dmn_versions, record.details, sink.emitted[0][1])


@pytest.mark.parametrize(
    "falha",
    [
        RuntimeError("banco fora: Failing row contains x"),
        ConnectionError("x"),
        TimeoutError(),
        KeyError("bug"),
    ],
)
async def test_falha_do_outbox_loga_conta_e_nao_muda_a_decisao(
    pin: rco_pin.RcoRulesPin, contados: list[tuple[str, int]], falha: BaseException
) -> None:
    bom, t_bom, s_bom = _montar(pin, outbox=OutboxEmMemoria())
    await bom._handle(_task())

    ruim_outbox = OutboxEmMemoria()
    ruim_outbox.falhar_enfileirar = falha
    contados.clear()
    ruim, t_ruim, s_ruim = _montar(pin, outbox=ruim_outbox)
    with structlog.testing.capture_logs() as logs:
        await ruim._handle(_task())
    assert _saida(t_ruim, s_ruim) == _saida(t_bom, s_bom)  # mesmas variaveis de saida e mesma auditoria
    assert t_ruim.failures == []
    erros = [e for e in logs if e["event"] == "rco_aviso_enfileirar_falhou"]
    assert len(erros) == 1 and erros[0]["log_level"] == "error"
    assert "Failing row" not in str(erros) and "bk-1" not in str(erros)
    assert contados == [("falha_enfileirar", 2)]


async def test_sem_outbox_a_decisao_e_a_mesma(
    pin: rco_pin.RcoRulesPin, contados: list[tuple[str, int]]
) -> None:
    bom, t_bom, s_bom = _montar(pin, outbox=OutboxEmMemoria())
    await bom._handle(_task())
    contados.clear()
    nenhum, t_nenhum, s_nenhum = _montar(pin, outbox=None)
    await nenhum._handle(_task())
    assert _saida(t_nenhum, s_nenhum) == _saida(t_bom, s_bom)
    assert contados == [("nao_configurado", 1)]


async def test_outbox_lento_nao_segura_alem_do_teto(
    pin: rco_pin.RcoRulesPin, monkeypatch: pytest.MonkeyPatch, contados: list[tuple[str, int]]
) -> None:
    from maezo.tools.workers import harness as h

    monkeypatch.setattr(h, "_RCO_AVISO_TIMEOUT_S", 0.05)

    class Lento(OutboxEmMemoria):
        async def enfileirar(self, linhas: Any) -> int:
            await asyncio.sleep(5)
            return 0

    harness, transport, _ = _montar(pin, outbox=Lento())
    await asyncio.wait_for(harness._handle(_task()), timeout=2)
    assert len(transport.completed) == 1 and contados == [("teto_estourado", 1)]


async def test_pin_ilegivel_ou_sinal_sem_destino_nao_muda_a_decisao(
    pin: rco_pin.RcoRulesPin, contados: list[tuple[str, int]]
) -> None:
    def quebra() -> Mapping[str, Any]:
        raise rco_pin.RcoRulesPinError("pin fora")

    harness, transport, _ = _montar(pin, outbox=OutboxEmMemoria(), avisos_do_pin=quebra)
    await harness._handle(_task())
    assert len(transport.completed) == 1 and contados == [("falha_enfileirar", 1)]

    contados.clear()
    sem_destino, t2, _ = _montar(pin, outbox=OutboxEmMemoria(), avisos_do_pin=lambda: {})
    with structlog.testing.capture_logs() as logs:
        await sem_destino._handle(_task())
    assert len(t2.completed) == 1 and contados == [("invalido", 1)]
    assert any(e.get("erro") == "erro_de_programacao" for e in logs)


async def test_tarefa_que_falha_nao_enfileira(pin: rco_pin.RcoRulesPin) -> None:
    outbox = OutboxEmMemoria()
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    sink.always_fail = RuntimeError("audit fora")
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink, rco_avisos=outbox)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        registrar_composicao(
            rco_composicao.ResultadoComposicao(
                composicao="grupo_restritivo", desfecho="escolhido", sinais=(sint.SINAL,)
            ),
            modo="sombra",
        )
        return {}

    harness.register(TOPICO, handler)
    await harness._handle(_task())
    assert transport.completed == [] and outbox.linhas == {}


async def test_destinos_do_recorte_real_no_harness(pin: rco_pin.RcoRulesPin) -> None:
    """Os destinos sao DADO do pin: com os `avisos` do recorte real 2.3.0, o sinal real vai as duas filas."""
    outbox = OutboxEmMemoria()
    transport = FakeWorkerTransport()
    harness = WorkerHarness(
        transport,
        worker_id="w",
        tenant="amh",
        audit_sink=FakeAuditSink(),
        rco_avisos=outbox,
        rco_avisos_do_pin=avisos_do_recorte,
    )

    async def handler(task: ExternalTask) -> dict[str, Any]:
        registrar_composicao(
            rco_composicao.ResultadoComposicao(
                composicao=rec.COMPOSICAO, desfecho="escolhido", sinais=(SINAL_REAL,)
            ),
            modo="sombra",
        )
        return {}

    harness.register(TOPICO, handler)
    await harness._handle(_task())
    assert sorted(r["linha"].fila for r in outbox.linhas.values()) == ["auditoria_medica", "cadastro"]


async def test_ponta_a_ponta_sintetico_ate_o_relay(pin: rco_pin.RcoRulesPin) -> None:
    """Composicao simulada registrada na tarefa -> harness completa -> linhas no outbox -> o relay publica o
    payload exato (produtor falso). O consumidor (2b) fica fora."""
    outbox = OutboxEmMemoria()
    harness, transport, _ = _montar(pin, outbox=outbox)
    await harness._handle(_task())
    assert len(transport.completed) == 1
    produtor = ProdutorFalso()
    rel = await rco_avisos_relay.drain_once(
        outbox,
        produtor,
        reivindicada_por="relay-1",
        grupos_do_pin=lambda: {"auditoria_medica": "grupo-sint-a", "cadastro": "grupo-sint-b"},  # DL-0186
    )
    assert rel.entregues == 2
    payloads = [p[1] for p in produtor.publicadas]
    assert payloads == [montar_payload(r["linha"]) for r in outbox.linhas.values()]
    for payload, fila in zip(payloads, ("auditoria_medica", "cadastro"), strict=True):
        assert payload == {
            "type": "rco.aviso_auditoria",
            "tenant_id": "amh",
            "task_id": "task-1",
            "composicao": "grupo_restritivo",
            "sinal": sint.SINAL,
            "fila": fila,
            "prazo_parametro": sint.AVISOS[sint.SINAL]["prazo_parametro"],
            "chave_idempotencia": payload["chave_idempotencia"],
        }
    assert "bk-1" not in str(payloads)


# --- revisao: contrato de ligacao, conclusao incerta, destinos e "ultima vence" ------------------------


def _fonte_da_composicao() -> str:
    return Path(rco_composicao.__file__).read_text(encoding="utf-8")


async def test_compor_e_puro_e_nao_registra_nada(pin: rco_pin.RcoRulesPin) -> None:
    """Contrato: `compor`/`compor_async` nao chamam `registrar_composicao`. Quem liga o aviso e o WORKER que
    compoe; sem essa chamada, nada vai ao coletor nem a auditoria (a ligacao nunca e presumida)."""
    respostas = {"rco_sint_d": [LINHA_D], "rco_sint_c": [LINHA_C]}

    async def assincrono(decisao: str, entradas: Mapping[str, Any]) -> Any:
        return copy.deepcopy(respostas[decisao])

    with collect_dmn_versions() as versoes, collect_rco_composicoes() as composicoes:
        sinc = rco_composicao.compor(
            _grupo(pin),
            lambda d, e: copy.deepcopy(respostas[d]),
            {},
            sinais_auditoria=pin.sinais_auditoria,
            ordem_restricao=pin.ordem_restricao,
        )
        assinc = await rco_composicao.compor_async(
            _grupo(pin),
            assincrono,
            {},
            sinais_auditoria=pin.sinais_auditoria,
            ordem_restricao=pin.ordem_restricao,
        )
    assert sinc.sinais == assinc.sinais == (sint.SINAL,)
    assert composicoes == {} and versoes == {}
    fonte = _fonte_da_composicao()
    assert "registrar_composicao" not in fonte and "_audit_ctx" not in fonte


async def test_harness_sem_registrar_nao_enfileira(pin: rco_pin.RcoRulesPin) -> None:
    outbox = OutboxEmMemoria()
    harness, transport, sink = _montar(pin, outbox=outbox, registrar=False)
    await harness._handle(_task())
    assert len(transport.completed) == 1 and outbox.linhas == {}
    assert all("sinaisAuditoria" not in e for e in sink.emitted[0][0].dmn_versions.values())


class _CompleteQueFalha(FakeWorkerTransport):
    async def complete(self, task_id: str, worker_id: str, variables: dict[str, Any]) -> None:
        raise TimeoutError("resposta perdida")


async def test_complete_que_levanta_nao_enfileira_e_torna_observavel(
    pin: rco_pin.RcoRulesPin, contados: list[tuple[str, int]]
) -> None:
    outbox = OutboxEmMemoria()
    harness, transport, sink = _montar(pin, outbox=outbox)
    harness._transport = _CompleteQueFalha()
    with structlog.testing.capture_logs() as logs:
        await harness._handle(_task())
    assert outbox.linhas == {}  # nada enfileirado: a tarefa pode ser re-entregue
    perdidos = [e for e in logs if e["event"] == "rco_aviso_nao_enfileirado"]
    assert [(e["composicao"], e["sinal"], e["motivo"]) for e in perdidos] == [
        ("grupo_restritivo", sint.SINAL, "conclusao_incerta")
    ]
    assert contados == [("conclusao_incerta", 1)]
    assert len(harness._transport.failures) == 1  # o caminho de falha de sempre seguiu
    assert sink.emitted[0][0].dmn_versions["rco_sint_d"]["sinaisAuditoria"] == [sint.SINAL]


async def test_destinos_diferentes_do_pin_sao_os_gravados(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin sintetico com o aviso apontando para OUTRAS filas: as linhas sao exatamente essas (nada fixo)."""
    sint.com_formato_2_3(
        tmp_path,
        sint.montar(tmp_path),
        k6=sint.k6_2_3(
            filas={"fila_x": {"par": "sim"}, "fila_y": {"par": "nao"}},
            avisos={sint.SINAL: {"filas": ["fila_y", "fila_x"], "prazo_parametro": "SINT-9:prazos.1"}},
        ),
    )
    carregado = rco_pin.load_rco_rules_pin(tmp_path / sint.LOCK_REL, rules_dir=tmp_path / sint.RULES_DIR)
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: carregado)
    monkeypatch.setattr(at, "efeito_k1_habilitado", _proibido)
    monkeypatch.setattr(rco_pin, "efeito_k1_habilitado", _proibido)
    at.limpar_cache_estados()
    outbox = OutboxEmMemoria()
    harness, transport, _ = _montar(carregado, outbox=outbox)  # destinos pelo provedor PADRAO (o pin)
    await harness._handle(_task())
    assert len(transport.completed) == 1
    assert [(r["linha"].fila, r["linha"].prazo_parametro) for r in outbox.linhas.values()] == [
        ("fila_y", "SINT-9:prazos.1"),
        ("fila_x", "SINT-9:prazos.1"),
    ]


async def test_reavaliacao_depois_de_registrar_nao_tira_a_chave_da_auditoria(
    pin: rco_pin.RcoRulesPin,
) -> None:
    outbox = OutboxEmMemoria()
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink, rco_avisos=outbox)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        versao = DmnVersion(key="rco_sint_d", id="x", version=1, deployment_id="d")
        record_dmn_version(versao, row=LINHA_D, sombra=True)
        registrar_composicao(
            rco_composicao.ResultadoComposicao(
                composicao="grupo_restritivo",
                desfecho="escolhido",
                avaliadas=("rco_sint_d",),
                sinais=(sint.SINAL,),
            ),
            modo="sombra",
        )
        record_dmn_version(versao, row=LINHA_D, sombra=True)  # reescreve a entrada ("ultima vence")
        return {}

    harness.register(TOPICO, handler)
    await harness._handle(_task())
    assert sink.emitted[0][0].dmn_versions["rco_sint_d"]["sinaisAuditoria"] == [sint.SINAL]
