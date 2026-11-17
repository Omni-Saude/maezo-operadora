"""5c-2 (ADR-0077 §4): chaves de auditoria `estadoManual`/`ativacaoEfetiva`/`modo` e o registro de
decisoes no harness — o ponto unico, a ordem em relacao a auditoria e o FAIL-OPEN.

Sem engine nem Postgres: `FakeWorkerTransport` + `FakeAuditSink` + um registro falso. O pin e o pacote K1
sao os sinteticos de `tests/unit/platform/rco_k1_sintetico.py`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import structlog

from maezo.gateway.audit import AuditRecord
from maezo.platform import rco_ativacao as at
from maezo.platform.rco_registro import AvaliacaoRegra, ContextoDecisao
from maezo.tools.workers import rco_pin
from maezo.tools.workers._audit_ctx import collect_dmn_versions, collect_rco_avaliacoes, record_dmn_version
from maezo.tools.workers.dmn_transport import DmnVersion
from maezo.tools.workers.harness import ExternalTask, FakeAuditSink, FakeWorkerTransport, WorkerHarness
from tests.unit.platform import rco_k1_sintetico as sint

SINT = "rco_sint_a"
ROW_NEGAR = {
    "decisao": "negar",
    "classeAdverso": "administrativo",
    "motivoTab38": "3171",
    "fila": "cadastro",
    "idRegra": "SINT-A-0002",
}
ROW_LIBERAR = {"decisao": "liberar", "idRegra": "SINT-A-0001"}


@pytest.fixture(autouse=True)
def _pin_e_ambiente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sint.montar(tmp_path)
    pin = rco_pin.load_rco_rules_pin(tmp_path / sint.LOCK_REL, rules_dir=tmp_path / sint.RULES_DIR)
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: pin)
    monkeypatch.delenv(at.CHAVE_EFEITO_K1, raising=False)
    at.limpar_cache_estados()


def _version(key: str = SINT) -> DmnVersion:
    return DmnVersion(key=key, id="x", version=1, deployment_id="d")


class _Registro:
    """Registro falso: guarda o que recebeu e a ordem em relacao a auditoria."""

    def __init__(self, sink: FakeAuditSink, *, falha: BaseException | None = None, demora: float = 0) -> None:
        self.tenant = "amh"
        self.chamadas: list[tuple[AvaliacaoRegra, ContextoDecisao, int]] = []
        self._sink = sink
        self._falha = falha
        self._demora = demora

    async def registrar(self, avaliacao: AvaliacaoRegra, contexto: ContextoDecisao) -> bool:
        self.chamadas.append((avaliacao, contexto, len(self._sink.emitted)))
        if self._demora:
            await asyncio.sleep(self._demora)
        if self._falha is not None:
            raise self._falha
        return True


def _task(business_key: str = "AUTH-amh-guia-1") -> ExternalTask:
    return ExternalTask(
        task_id="task-1",
        topic="operadora.test.rco",
        process_instance_id="proc-1",
        business_key=business_key,
        worker_id="w-1",
        variables={},
    )


def _montar(
    handler_row: dict[str, Any] | None,
    *,
    key: str = SINT,
    registro: Any | None = None,
    sink: FakeAuditSink | None = None,
    erro: BaseException | None = None,
) -> tuple[WorkerHarness, FakeWorkerTransport, FakeAuditSink]:
    transport = FakeWorkerTransport()
    sink = sink or FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink, rco_decisoes=registro)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        if erro is not None:
            raise erro
        record_dmn_version(_version(key), row=handler_row)
        return {"ok": True}

    harness.register("operadora.test.rco", handler)
    return harness, transport, sink


# --- chaves de auditoria ------------------------------------------------------------------------------


def test_chaves_de_auditoria_so_com_regra_casada_major2() -> None:
    cheio = _version().to_audit_dict(ROW_NEGAR)
    assert (cheio["estadoManual"], cheio["ativacaoEfetiva"], cheio["modo"]) == ("sombra", "plena", "sombra")
    for chave in ("estadoManual", "ativacaoEfetiva", "modo"):
        assert isinstance(cheio[chave], str)
        # `idRegra` ausente ou vazio (linha nao adversa sem regra): so `shaDmn`, como `ativacaoPacote`.
        assert chave not in _version().to_audit_dict(None)
        assert chave not in _version().to_audit_dict({"idRegra": "", "decisao": "liberar"})
        # tabela Maezo nunca as carrega
        assert chave not in _version("auth_sla").to_audit_dict({"idRegra": "X"})


def test_chaves_acompanham_o_estado_e_o_indicador(monkeypatch: pytest.MonkeyPatch) -> None:
    estados = at.EstadosManuais({("SINT-A-0002", "1.0.1"): "ativa_l2"})
    monkeypatch.setattr(rco_pin, "estados_em_cache", lambda: estados)
    sem_indicador = _version().to_audit_dict(ROW_NEGAR)
    assert (sem_indicador["estadoManual"], sem_indicador["modo"]) == ("ativa_l2", "sombra")
    monkeypatch.setattr(rco_pin, "efeito_k1_habilitado", lambda: True)
    com_indicador = _version().to_audit_dict(ROW_NEGAR)
    assert (com_indicador["estadoManual"], com_indicador["modo"]) == ("ativa_l2", "ativa")
    # a outra regra do mesmo pacote nao herda o estado (chave = idRegra + versaoRegra)
    outra = _version().to_audit_dict(ROW_LIBERAR)
    assert (outra["estadoManual"], outra["modo"]) == ("sombra", "sombra")


def test_arquivo_de_estado_ilegivel_aparece_na_auditoria(monkeypatch: pytest.MonkeyPatch) -> None:
    ruim = at.EstadosManuais({}, ilegivel=True, motivo="x")
    monkeypatch.setattr(rco_pin, "estados_em_cache", lambda: ruim)
    assert _version().to_audit_dict(ROW_NEGAR)["estadoManual"] == "desligada"


# --- o seam: registro depois da auditoria, fail-open ---------------------------------------------------


async def test_registra_uma_vez_depois_da_auditoria_sem_gravar_a_chave_de_negocio() -> None:
    sink = FakeAuditSink()
    registro = _Registro(sink)
    harness, transport, _ = _montar(ROW_NEGAR, registro=registro, sink=sink)
    await harness._handle(_task())

    assert len(transport.completed) == 1
    assert len(registro.chamadas) == 1
    avaliacao, contexto, emitidos_antes = registro.chamadas[0]
    assert emitidos_antes == 1  # a linha de auditoria ja existia quando o registro foi gravado
    assert (avaliacao.id_regra, avaliacao.versao_regra, avaliacao.decisao) == (
        "SINT-A-0002",
        "1.0.1",
        "negar",
    )
    assert (avaliacao.estado_manual, avaliacao.modo) == ("sombra", "sombra")
    assert contexto.tenant == "amh"
    assert contexto.chave_negocio == "AUTH-amh-guia-1"  # so em memoria, para a amostragem
    assert "guia-1" not in repr(contexto)
    # a auditoria e o registro dizem a mesma coisa
    entry = sink.emitted[0][0].dmn_versions[SINT]
    assert entry["estadoManual"] == avaliacao.estado_manual and entry["modo"] == avaliacao.modo


async def test_reentrega_usa_a_mesma_chave_de_idempotencia() -> None:
    sink = FakeAuditSink()
    registro = _Registro(sink)
    harness, _, _ = _montar(ROW_NEGAR, registro=registro, sink=sink)
    await harness._handle(_task())
    await harness._handle(_task())
    assert len(registro.chamadas) == 2
    assert registro.chamadas[0][1].chave_idempotencia == registro.chamadas[1][1].chave_idempotencia


@pytest.mark.parametrize(
    "falha",
    [RuntimeError("banco fora"), ConnectionError("x"), ValueError("bug"), TimeoutError()],
)
async def test_falha_do_registro_nunca_muda_o_desfecho_da_tarefa(falha: BaseException) -> None:
    """FAIL-OPEN: o registro e observacao (sombra); a auditoria ja esta gravada e a tarefa completa."""
    sink = FakeAuditSink()
    registro = _Registro(sink, falha=falha)
    harness, transport, _ = _montar(ROW_NEGAR, registro=registro, sink=sink)
    with structlog.testing.capture_logs() as logs:
        await harness._handle(_task())
    assert len(transport.completed) == 1 and transport.failures == []
    assert len(sink.emitted) == 1
    erros = [e for e in logs if e["event"] == "rco_decisao_registro_falhou_fail_open"]
    assert len(erros) == 1
    assert erros[0]["log_level"] == "error" and erros[0]["id_regra"] == "SINT-A-0002"
    assert "business_key" not in erros[0] and "AUTH-amh-guia-1" not in str(erros[0])
    assert "exc_info" not in erros[0]  # so o tipo: a mensagem do driver pode trazer a linha


async def test_registro_lento_nao_segura_a_tarefa(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.tools.workers import harness as h

    monkeypatch.setattr(h, "_RCO_REGISTRO_TIMEOUT_S", 0.05)
    sink = FakeAuditSink()
    registro = _Registro(sink, demora=5)
    harness, transport, _ = _montar(ROW_NEGAR, registro=registro, sink=sink)
    await harness._handle(_task())
    assert len(transport.completed) == 1


async def test_sem_registro_configurado_nada_muda() -> None:
    harness, transport, sink = _montar(ROW_NEGAR, registro=None)
    await harness._handle(_task())
    assert len(transport.completed) == 1 and len(sink.emitted) == 1


@pytest.mark.parametrize(
    ("row", "key"),
    [
        (None, SINT),  # sem linha casada unica
        ({"idRegra": "", "decisao": "liberar"}, SINT),  # vazio canonico do K1
        ({"idRegra": "QUALQUER"}, "auth_sla"),  # tabela Maezo
    ],
)
async def test_so_decisao_rco_com_id_regra_e_registrada(row: dict[str, Any] | None, key: str) -> None:
    sink = FakeAuditSink()
    registro = _Registro(sink)
    harness, transport, _ = _montar(row, key=key, registro=registro, sink=sink)
    await harness._handle(_task())
    assert registro.chamadas == [] and len(transport.completed) == 1


async def test_tarefa_que_falha_ou_e_recusada_nao_registra() -> None:
    sink = FakeAuditSink()
    registro = _Registro(sink)
    harness, transport, _ = _montar(ROW_NEGAR, registro=registro, sink=sink, erro=RuntimeError("falhou"))
    await harness._handle(_task())
    assert registro.chamadas == [] and transport.completed == []


async def test_auditoria_que_falha_nao_deixa_registro() -> None:
    sink = FakeAuditSink()
    sink.always_fail = RuntimeError("audit fora")
    registro = _Registro(sink)
    harness, transport, _ = _montar(ROW_NEGAR, registro=registro, sink=sink)
    await harness._handle(_task())
    assert registro.chamadas == [] and transport.completed == []  # o registro nunca precede a auditoria


async def test_avaliacao_anterior_sai_quando_a_mesma_tabela_nao_casa_depois() -> None:
    """Mesma regra de `dmn_versions` (ultima vence): a avaliacao nao sobrevive a uma avaliacao sem regra."""
    with collect_dmn_versions() as versoes, collect_rco_avaliacoes() as avaliacoes:
        record_dmn_version(_version(), row=ROW_NEGAR)
        assert SINT in avaliacoes
        record_dmn_version(_version(), row=None)
        assert SINT not in avaliacoes and "idRegra" not in versoes[SINT]


def test_audit_record_traz_as_chaves_como_texto() -> None:
    with collect_dmn_versions() as versoes:
        record_dmn_version(_version(), row=ROW_NEGAR)
    record = AuditRecord(
        agent_id="a",
        tenant_id="amh",
        agent_version="1",
        action="x",
        decision="COMPLETE",
        details={},
        dmn_versions=dict(versoes),
    )
    assert record.dmn_versions[SINT]["modo"] == "sombra"


def test_manifesto_major1_nao_e_pinado(tmp_path: Path) -> None:
    # Decisao da ROOT (16/11/2026): sem `provas[]` (MAJOR 1) o pin e recusado; nenhuma auditoria K1 roda.
    raiz = tmp_path / "m1"
    sint.montar(raiz, major=1)
    with pytest.raises(rco_pin.RcoRulesPinError, match="sem provas do motor"):
        rco_pin.load_rco_rules_pin(raiz / sint.LOCK_REL, rules_dir=raiz / sint.RULES_DIR)


async def test_falha_de_driver_loga_so_o_tipo_sem_a_mensagem() -> None:
    sink = FakeAuditSink()
    segredo = "Failing row contains (AUTH-amh-guia-1, 123.456.789-00)"
    registro = _Registro(sink, falha=RuntimeError(segredo))
    harness, transport, _ = _montar(ROW_NEGAR, registro=registro, sink=sink)
    with structlog.testing.capture_logs() as logs:
        await harness._handle(_task())
    erros = [e for e in logs if e["event"] == "rco_decisao_registro_falhou_fail_open"]
    assert erros[0]["erro"] == "RuntimeError" and "exc_info" not in erros[0]
    assert segredo not in str(logs) and len(transport.completed) == 1


async def test_erro_de_programacao_loga_com_pilha_e_a_tarefa_completa() -> None:
    sink = FakeAuditSink()
    registro = _Registro(sink, falha=TypeError("bug"))
    harness, transport, _ = _montar(ROW_NEGAR, registro=registro, sink=sink)
    with structlog.testing.capture_logs() as logs:
        await harness._handle(_task())
    erro = next(e for e in logs if e["event"] == "rco_decisao_registro_falhou_fail_open")
    assert erro["erro"] == "erro_de_programacao" and erro["exc_info"] is True
    assert len(transport.completed) == 1


class _RegistroQueIgnoraCancelamento:
    """Pior caso: dorme para sempre e engole o primeiro cancelamento."""

    tenant = "amh"

    def __init__(self) -> None:
        self.cancelamentos = 0

    async def registrar(self, avaliacao: AvaliacaoRegra, contexto: ContextoDecisao) -> bool:
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.cancelamentos += 1
                if self.cancelamentos > 1:
                    raise


async def test_store_pendurado_nao_atrasa_a_tarefa_alem_do_teto_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maezo.tools.workers import harness as h

    monkeypatch.setattr(h, "_RCO_REGISTRO_TIMEOUT_S", 0.2)
    registro = _RegistroQueIgnoraCancelamento()
    harness, transport, _ = _montar(ROW_NEGAR, registro=registro)
    inicio = asyncio.get_running_loop().time()
    with structlog.testing.capture_logs() as logs:
        await harness._handle(_task())
    assert asyncio.get_running_loop().time() - inicio < 2.0
    assert len(transport.completed) == 1
    assert any(e.get("erro") == "timeout_total" for e in logs)
    await asyncio.sleep(0)  # deixa a coroutine orfa receber o cancelamento


async def test_o_teto_e_unico_para_todas_as_decisoes_da_tarefa(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.tools.workers import harness as h

    monkeypatch.setattr(h, "_RCO_REGISTRO_TIMEOUT_S", 0.3)
    sink = FakeAuditSink()
    registro = _Registro(sink, demora=0.2)  # cada decisao cabe no teto; duas juntas nao
    harness = WorkerHarness(
        FakeWorkerTransport(), worker_id="w", tenant="amh", audit_sink=sink, rco_decisoes=registro
    )
    av = _avaliacao_sintetica()
    inicio = asyncio.get_running_loop().time()
    await harness._registrar_decisoes_rco(_task(), {"rco_sint_a": av, "rco_sint_b": av})
    assert asyncio.get_running_loop().time() - inicio < 0.45
    assert len(registro.chamadas) == 2  # a segunda foi iniciada; o teto total a corta


def _avaliacao_sintetica() -> AvaliacaoRegra:
    with collect_dmn_versions(), collect_rco_avaliacoes() as avs:
        record_dmn_version(_version(), row=ROW_NEGAR)
    return avs[SINT]


# --- DL-0182: com efeito possivel, o registro e FAIL-CLOSED ---------------------------------------------


@pytest.fixture
def efeito_ativo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simula o indicador ligado e a regra em `ativa_l2` SO neste processo de teste (config real intocada)."""
    estados = at.EstadosManuais({("SINT-A-0002", "1.0.1"): "ativa_l2"})
    monkeypatch.setattr(rco_pin, "estados_em_cache", lambda: estados)
    monkeypatch.setattr(rco_pin, "efeito_k1_habilitado", lambda: True)


def _task_id(task_id: str) -> ExternalTask:
    return ExternalTask(
        task_id=task_id,
        topic="operadora.test.rco",
        process_instance_id=f"proc-{task_id}",
        business_key=f"AUTH-amh-{task_id}",
        worker_id="w-1",
        variables={},
    )


@pytest.mark.usefixtures("efeito_ativo")
@pytest.mark.parametrize("falha", [RuntimeError("banco fora"), ConnectionError("x"), TypeError("bug")])
async def test_com_efeito_possivel_falha_do_registro_nao_completa_a_tarefa(falha: BaseException) -> None:
    sink = FakeAuditSink()
    registro = _Registro(sink, falha=falha)
    harness, transport, _ = _montar(ROW_NEGAR, registro=registro, sink=sink)
    with structlog.testing.capture_logs() as logs:
        await harness._handle(_task())
    assert registro.chamadas[0][0].modo == "ativa"
    assert transport.completed == [] and len(transport.failures) == 1  # re-entrega pelo motor
    assert len(sink.emitted) == 1  # a auditoria ja estava gravada (a re-entrega deduplica)
    erros = [e for e in logs if e["event"] == "rco_decisao_registro_falhou_fail_closed"]
    assert len(erros) == 1 and "AUTH-amh-guia-1" not in str(logs)
    assert not any(e["event"] == "rco_decisao_registro_falhou_fail_open" for e in logs)


@pytest.mark.usefixtures("efeito_ativo")
async def test_com_efeito_possivel_teto_estourado_nao_completa(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.tools.workers import harness as h

    monkeypatch.setattr(h, "_RCO_REGISTRO_TIMEOUT_S", 0.05)
    sink = FakeAuditSink()
    harness, transport, _ = _montar(ROW_NEGAR, registro=_Registro(sink, demora=5), sink=sink)
    await harness._handle(_task())
    assert transport.completed == [] and len(transport.failures) == 1


@pytest.mark.usefixtures("efeito_ativo")
async def test_com_efeito_possivel_sem_registro_configurado_nao_completa() -> None:
    harness, transport, _ = _montar(ROW_NEGAR, registro=None)
    await harness._handle(_task())
    assert transport.completed == [] and len(transport.failures) == 1


@pytest.mark.usefixtures("efeito_ativo")
async def test_com_efeito_possivel_e_registro_gravado_completa() -> None:
    sink = FakeAuditSink()
    registro = _Registro(sink)
    harness, transport, _ = _montar(ROW_NEGAR, registro=registro, sink=sink)
    await harness._handle(_task())
    assert len(transport.completed) == 1 and transport.failures == []


class _RegistroComOrdinal:
    """Registro em memoria com a MESMA semantica do Postgres: ordinal = MAX+1 das linhas gravadas,
    idempotente pela `chave_idempotencia`. `falhar_uma_vez` derruba a primeira gravacao destas tarefas."""

    tenant = "amh"

    def __init__(self, falhar_uma_vez: set[str]) -> None:
        self.linhas: dict[str, int] = {}  # chave_idempotencia -> ordinal
        self.por_tarefa: dict[str, str] = {}  # task_id -> chave_idempotencia
        self._falhar = set(falhar_uma_vez)

    async def registrar(self, avaliacao: AvaliacaoRegra, contexto: ContextoDecisao) -> bool:
        task_id = contexto.chave_negocio.removeprefix("AUTH-amh-") if contexto.chave_negocio else ""
        if task_id in self._falhar:
            self._falhar.discard(task_id)
            raise ConnectionError("banco fora")
        self.por_tarefa[task_id] = contexto.chave_idempotencia
        if contexto.chave_idempotencia in self.linhas:
            return False
        self.linhas[contexto.chave_idempotencia] = max(self.linhas.values(), default=0) + 1
        return True


@pytest.mark.usefixtures("efeito_ativo")
async def test_linha_perdida_nao_tira_decisao_com_efeito_da_contagem_de_ordinais() -> None:
    """Reproducao do defeito do 5c-2 (`rco_registro`, nota "linha perdida desloca os ordinais"): antes, a
    tarefa A completava (fail-open) SEM linha, e B herdava o ordinal 1 que seria de A: uma decisao com
    efeito saia fora da contagem. Agora A nao completa sem a linha; a re-entrega a grava."""
    sink = FakeAuditSink()
    registro = _RegistroComOrdinal(falhar_uma_vez={"A"})
    harness, transport, _ = _montar(ROW_NEGAR, registro=registro, sink=sink)
    fila = ["A", "B", "C"]
    while fila:  # o motor: o que nao completou volta para a fila (re-entrega)
        task_id = fila.pop(0)
        await harness._handle(_task_id(task_id))
        if task_id not in {t for t, _ in transport.completed}:
            fila.append(task_id)
    completadas = [t for t, _ in transport.completed]
    assert sorted(completadas) == ["A", "B", "C"]
    # toda decisao com efeito que completou tem a sua linha e o seu ordinal
    assert set(registro.por_tarefa) == set(completadas)
    ordinais = sorted(registro.linhas[registro.por_tarefa[t]] for t in completadas)
    assert ordinais == [1, 2, 3]  # sem buraco nem ordinal herdado de linha perdida
    assert len(transport.failures) == 1  # A falhou uma vez e foi re-entregue
