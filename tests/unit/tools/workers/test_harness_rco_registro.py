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
from maezo.platform.privacy.key_scrubber import reset_egress_pseudonymizer_cache
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
    monkeypatch.setenv("RUNTIME_MODE", "local")
    monkeypatch.setenv("PHI_HMAC_KEY", "chave-de-teste")
    monkeypatch.delenv(at.CHAVE_EFEITO_K1, raising=False)
    reset_egress_pseudonymizer_cache()
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


async def test_registra_uma_vez_depois_da_auditoria_com_a_chave_pseudonimizada() -> None:
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
    assert contexto.chave_negocio_pseudonimizada is not None
    assert contexto.chave_negocio_pseudonimizada.startswith("AUTH-hk1_")
    assert "guia-1" not in contexto.chave_negocio_pseudonimizada
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


async def test_registro_lento_nao_segura_a_tarefa(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.tools.workers import harness as h

    monkeypatch.setattr(h, "_RCO_REGISTRO_TIMEOUT_S", 0.05)
    sink = FakeAuditSink()
    registro = _Registro(sink, demora=5)
    harness, transport, _ = _montar(ROW_NEGAR, registro=registro, sink=sink)
    await harness._handle(_task())
    assert len(transport.completed) == 1


async def test_producao_sem_chave_phi_nao_grava_e_a_tarefa_completa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUNTIME_MODE", raising=False)
    monkeypatch.delenv("PHI_HMAC_KEY", raising=False)
    reset_egress_pseudonymizer_cache()
    sink = FakeAuditSink()
    registro = _Registro(sink)
    harness, transport, _ = _montar(ROW_NEGAR, registro=registro, sink=sink)
    await harness._handle(_task())
    assert registro.chamadas == [] and len(transport.completed) == 1
    reset_egress_pseudonymizer_cache()


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


def test_manifesto_major1_nao_tem_as_chaves_nem_avaliacao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raiz = tmp_path / "m1"
    sint.montar(raiz, major=1)
    pin = rco_pin.load_rco_rules_pin(raiz / sint.LOCK_REL, rules_dir=raiz / sint.RULES_DIR)
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: pin)
    entry, avaliacao = _version().audit_pair(ROW_NEGAR)
    assert avaliacao is None
    assert {"estadoManual", "ativacaoEfetiva", "modo", "ativacaoPacote"}.isdisjoint(entry)
    assert entry["versaoRegra"] == "1.0.1"
