"""Ligacao do montador ao caminho que avalia as decisoes K1 do rco, so em SOMBRA (MZO-3).

Prova, sem motor (o `FakeDmnTransport` registra o que recebeu) e sem Postgres:

- as entradas do montador chegam a avaliacao da tabela `rco_geral_prazos_conta` e a auditoria do montador
  (`derivacoes` com `diasBrutos`/`toleranciaAplicada`/`valorEntregue`/`motivoTolerancia`, e
  `entradasNulas`) entra na entrada `rco_` de `dmn_versions` — no coletor e no `AuditRecord` do harness;
- NADA produz efeito: as variaveis de saida do intake de SP-OP-CONTAS-001 sao identicas com e sem a
  sombra, a sombra devolve `None` e engole toda falha, e o indicador do K1 segue desligado;
- inerte hoje: com o pin real (`sem_pacote`) nada e avaliado;
- a data de hoje que o intake poe em `data_recebimento_lote` quando o lote nao a traz nunca chega ao
  montador.

O pin e montado no teste (`RcoRulesPin` com a decisao do recorte real do K1) e o dicionario e o recorte
1.2.0 (`rco_recorte.py`); nenhum byte disso vai para `spec/processes/dmn/rco/`.
"""

from __future__ import annotations

import asyncio
import functools
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import pytest
import structlog

from maezo.adapters.amh.contract import AmhContractPinError
from maezo.platform import rco_ativacao as at
from maezo.platform.rco_manifesto import ManifestoK1InvalidoError
from maezo.tools.workers import contas, rco_pin, rco_sombra
from maezo.tools.workers import rco_montador as mt
from maezo.tools.workers._audit_ctx import collect_dmn_versions, collect_rco_avaliacoes
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.dmn_transport import (
    DmnDeterministicEvaluationError,
    DmnEvaluationError,
    DmnNoResultError,
    DmnVariableDecodeError,
    DmnVersion,
    FakeDmnTransport,
)
from maezo.tools.workers.harness import ExternalTask, FakeAuditSink, FakeWorkerTransport, WorkerHarness
from tests.unit.tools.workers import rco_recorte

CHAVE = "rco_geral_prazos_conta"
SHA = "sha256:" + "a" * 64
ROW_SEM_REGRA = {"decisao": "liberar", "idRegra": "", "motivoTab38": "", "fila": "", "classeAdverso": ""}
TOPICO = "operadora.contas.identify_glosa"


@pytest.fixture
def pin_com_prazos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> rco_pin.RcoRulesPin:
    """Pin `fixado` com a tabela de prazos de conta do recorte real (regras sem `ativacao`: so `shaDmn`)."""
    decisao = rco_recorte.decisoes()[CHAVE]
    tabela = rco_pin.RcoPinnedTable(
        CHAVE, "spec/processes/dmn/rco/geral_prazos_conta.dmn", SHA, decisao=decisao
    )
    # `composicoes=()`: a guarda de DL-0173 (`exigir_avaliacao_isolada`) le o grupo do manifesto pinado.
    manifesto = cast(Any, SimpleNamespace(dicionario_entradas_sha256="sha256:" + "b" * 64, composicoes=()))
    pin = rco_pin.RcoRulesPin(
        estado="fixado", tables=MappingProxyType({CHAVE: tabela}), manifesto=manifesto, k1_dir=tmp_path
    )
    dicionario = rco_recorte.dicionario_1_2_0()
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: pin)
    monkeypatch.setattr(rco_sombra, "_dicionario", lambda _pin: dicionario)
    monkeypatch.setattr(rco_sombra, "_versao_k3_pinada", lambda: (1, 15))
    monkeypatch.delenv(at.CHAVE_EFEITO_K1, raising=False)
    return pin


def _dmn() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register(CHAVE, [ROW_SEM_REGRA])
    return dmn


def _fatos_com_datas(dias_corridos: int) -> rco_sombra.MontarFatos:
    """Fatos completos, como um produtor futuro os entregaria (hoje nenhum worker os tem)."""

    def montar(versao_k3: tuple[int, int]) -> mt.FatosDoCaso:
        return mt.FatosDoCaso(
            guia={"data_atendimento": "2026-10-01", "tipo_transacao": "ENVIO_LOTE_GUIAS"},
            versao_k3_pinada=versao_k3,
            cadastro={"prazo_apresentacao_contratual_dias": 30},
            tiss={"data_final_faturamento": "2026-10-01"},
            fora_tiss={"data_recebimento_lote": _mais("2026-10-01", dias_corridos)},
            regime_guia=mt.REGIME_INTERNACAO,
        )

    return montar


def _mais(dia: str, dias: int) -> str:
    return (date.fromisoformat(dia) + timedelta(days=dias)).isoformat()


def _variaveis_contas(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": "synthetic-tenant",
        "numero_lote_tiss": "synthetic-lote",
        "valor_apresentado_brl": 1.0,
        "linhas_conta_refs": [{"valor_apresentado_centavos": 100}],
        "data_recebimento_lote": "2026-09-01",
        "data_vencimento": "2026-09-30",
        "tipo_lote": "internacao",
    }
    base.update(extra)
    return base


# --- as entradas chegam a avaliacao e a auditoria ------------------------------------------------------


@pytest.mark.parametrize(("dias", "entregue", "bruto"), [(30, 0, 0), (31, 0, 1), (60, 0, 30), (61, 1, 31)])
def test_entradas_do_montador_chegam_a_tabela_e_a_auditoria(
    pin_com_prazos: rco_pin.RcoRulesPin, dias: int, entregue: int, bruto: int
) -> None:
    dmn = _dmn()
    with collect_dmn_versions() as versoes, collect_rco_avaliacoes():
        desfecho = rco_sombra.avaliar(dmn, CHAVE, _fatos_com_datas(dias))
    assert desfecho == rco_sombra.AVALIADA
    [(chave, variaveis)] = dmn.calls
    assert chave == CHAVE
    assert set(variaveis) == set(rco_recorte.decisoes()[CHAVE].entradas)
    assert variaveis["diasAteApresentacao"] == dias
    assert variaveis["diasAlemDoPrazoContratual"] == entregue
    entrada = versoes[CHAVE]
    assert entrada["shaDmn"] == SHA
    assert entrada["derivacoes"] == {
        "diasAlemDoPrazoContratual": {
            "diasBrutos": bruto,
            "toleranciaAplicada": 30 if bruto > 0 else 0,
            "valorEntregue": entregue,
            "motivoTolerancia": "aplicada" if bruto > 0 else "dentro_do_prazo_contratual",
        }
    }
    assert entrada["versaoDicionarioEntradas"] == "1.2.0"
    assert "diasAteApresentacao" not in entrada["entradasNulas"]
    assert entrada["entradasNulas"]["diasAteRecurso"] == "termo_da_formula_ausente"


def test_tabela_maezo_nunca_recebe_as_chaves_do_montador() -> None:
    from maezo.tools.workers._audit_ctx import record_dmn_version
    from maezo.tools.workers.dmn_transport import DmnVersion

    with collect_dmn_versions() as versoes:
        record_dmn_version(
            DmnVersion(key="glosa_triage", id="x", version=1, deployment_id="d"),
            entradas_rco={"derivacoes": {"x": {}}},
        )
    assert versoes["glosa_triage"] == {"version": 1, "id": "x", "deploymentId": "d"}


# --- inerte hoje e fail-open ---------------------------------------------------------------------------


def test_pin_real_sem_pacote_nao_avalia() -> None:
    dmn = _dmn()
    assert rco_pin.rco_rules_pin().estado == "sem_pacote"
    assert rco_sombra.avaliar(dmn, CHAVE, _fatos_com_datas(45)) == rco_sombra.SEM_PACOTE
    assert dmn.calls == []


def test_sem_transporte_nao_avalia(pin_com_prazos: rco_pin.RcoRulesPin) -> None:
    assert rco_sombra.avaliar(None, CHAVE, _fatos_com_datas(45)) == rco_sombra.SEM_TRANSPORTE


def test_dominio_sem_mapeamento(pin_com_prazos: rco_pin.RcoRulesPin, monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    tabela = pin_com_prazos.tables[CHAVE]
    assert tabela.decisao is not None
    outra = dataclasses.replace(tabela, decisao=dataclasses.replace(tabela.decisao, dominio="ortopedia-opme"))
    pin = dataclasses.replace(pin_com_prazos, tables=MappingProxyType({CHAVE: outra}))
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: pin)
    dmn = _dmn()
    assert rco_sombra.avaliar(dmn, CHAVE, _fatos_com_datas(45)) == rco_sombra.DOMINIO_NAO_SUPORTADO
    assert dmn.calls == []


class _MotorQueLevanta:
    """Transporte DMN que levanta `exc` (ou dorme `dorme` segundos) ao avaliar."""

    def __init__(self, exc: BaseException | None = None, dorme: float = 0.0) -> None:
        self._exc = exc
        self._dorme = dorme
        self.calls = 0

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        self.calls += 1
        if self._dorme:
            await asyncio.sleep(self._dorme)
        if self._exc is not None:
            raise self._exc
        return [dict(ROW_SEM_REGRA)], DmnVersion(key=decision_key, id="x", version=1, deployment_id="d")

    async def close(self) -> None:
        return None


def _levanta(exc: BaseException) -> Any:
    def _f(*_a: Any, **_k: Any) -> Any:
        raise exc

    return _f


@pytest.fixture
def metrica(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    contados: list[str] = []
    monkeypatch.setattr(rco_sombra, "record_rco_sombra", contados.append)
    return contados


def _logs_da_sombra(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in logs if str(e["event"]).startswith("rco_sombra")]


@pytest.mark.parametrize(
    ("onde", "exc", "desfecho"),
    [
        ("pin", rco_pin.RcoRulesPinError("pin ilegivel"), rco_sombra.PIN_INDISPONIVEL),
        ("dicionario", ManifestoK1InvalidoError(["entradas.json invalido"]), rco_sombra.DICIONARIO_RECUSADO),
        ("amh", AmhContractPinError(["pin AMH ilegivel"]), rco_sombra.PIN_AMH_INDISPONIVEL),
        ("montador", mt.MontadorEntradasError("divergente"), rco_sombra.MONTADOR_RECUSOU),
        ("montador", mt.DominioNaoSuportadoError("outro"), rco_sombra.DOMINIO_NAO_SUPORTADO),
        ("motor", DmnEvaluationError("motor fora"), rco_sombra.AVALIACAO_FALHOU),
        ("motor", DmnNoResultError(CHAVE), rco_sombra.AVALIACAO_FALHOU),
        ("motor", DmnDeterministicEvaluationError(CHAVE), rco_sombra.AVALIACAO_FALHOU),
        ("motor", DmnVariableDecodeError(CHAVE, "decisao", ValueError("x")), rco_sombra.AVALIACAO_FALHOU),
        ("motor", rco_pin.RcoRulesPinError("idRegra fora do padrao"), rco_sombra.AVALIACAO_FALHOU),
    ],
)
def test_falha_esperada_vira_desfecho(
    pin_com_prazos: rco_pin.RcoRulesPin,
    monkeypatch: pytest.MonkeyPatch,
    metrica: list[str],
    onde: str,
    exc: Exception,
    desfecho: str,
) -> None:
    dmn: Any = _dmn()
    if onde == "pin":
        monkeypatch.setattr(rco_pin, "rco_rules_pin", _levanta(exc))
    elif onde == "dicionario":
        monkeypatch.setattr(rco_sombra, "_dicionario", _levanta(exc))
    elif onde == "amh":
        monkeypatch.setattr(rco_sombra, "_versao_k3_pinada", _levanta(exc))
    elif onde == "montador":
        monkeypatch.setattr(mt, "montar_entradas", _levanta(exc))
    else:
        dmn = _MotorQueLevanta(exc)
    with structlog.testing.capture_logs() as logs:
        assert rco_sombra.avaliar_em_sombra(dmn, CHAVE, _fatos_com_datas(45), topico=TOPICO) is None
    [log] = _logs_da_sombra(logs)
    assert (log["event"], log["log_level"], log["desfecho"]) == ("rco_sombra", "info", desfecho)
    assert metrica == [desfecho]


def test_prazo_total_esgotado_vira_desfecho(
    pin_com_prazos: rco_pin.RcoRulesPin, monkeypatch: pytest.MonkeyPatch, metrica: list[str]
) -> None:
    monkeypatch.setattr(rco_sombra, "PRAZO_SOMBRA_S", 0.05)
    lento = _MotorQueLevanta(dorme=5.0)
    with collect_dmn_versions() as versoes, structlog.testing.capture_logs() as logs:
        rco_sombra.avaliar_em_sombra(lento, CHAVE, _fatos_com_datas(45))
    assert lento.calls == 1
    assert metrica == [rco_sombra.PRAZO_ESGOTADO]
    assert _logs_da_sombra(logs)[0]["desfecho"] == rco_sombra.PRAZO_ESGOTADO
    assert versoes == {}  # nada auditado de uma avaliacao que nao terminou


def test_prazo_padrao_e_5_segundos() -> None:
    assert rco_sombra.PRAZO_SOMBRA_S == 5.0


@pytest.mark.parametrize(
    ("onde", "exc"),
    [
        ("fatos", TypeError("dado de caso que nao pode ir ao log")),
        ("fatos", KeyError("guia-123")),
        ("motor", RuntimeError("dado de caso que nao pode ir ao log")),  # RuntimeError cru nao e do motor
        ("motor", AttributeError("x")),
        ("montador", ValueError("bug")),  # ValueError que nao e `MontadorEntradasError`
    ],
)
def test_erro_de_programacao_conta_e_loga_so_o_tipo(
    pin_com_prazos: rco_pin.RcoRulesPin,
    monkeypatch: pytest.MonkeyPatch,
    metrica: list[str],
    onde: str,
    exc: Exception,
) -> None:
    """Nao e engolido em silencio (metrica + ERROR), mas tambem nao sobe: subir faria o worker falhar."""
    dmn: Any = _dmn()
    montar: Any = _fatos_com_datas(45)
    if onde == "fatos":
        montar = _levanta(exc)
    elif onde == "motor":
        dmn = _MotorQueLevanta(exc)
    else:
        monkeypatch.setattr(mt, "montar_entradas", _levanta(exc))
    with structlog.testing.capture_logs() as logs:
        assert rco_sombra.avaliar_em_sombra(dmn, CHAVE, montar, topico=TOPICO) is None
    [log] = _logs_da_sombra(logs)
    assert log == {
        "event": "rco_sombra_erro_de_programacao",
        "log_level": "error",
        "decision_key": CHAVE,
        "erro": type(exc).__name__,
        "topico": TOPICO,
    }
    assert metrica == [rco_sombra.ERRO_DE_PROGRAMACAO]


def test_avaliar_deixa_o_erro_de_programacao_subir(pin_com_prazos: rco_pin.RcoRulesPin) -> None:
    with pytest.raises(TypeError):
        rco_sombra.avaliar(_dmn(), CHAVE, _levanta(TypeError("bug")))


def test_vocabulario_da_metrica_e_o_dos_desfechos() -> None:
    from maezo.platform.observability import RCO_SOMBRA_DESFECHOS

    assert rco_sombra.DESFECHOS == RCO_SOMBRA_DESFECHOS


# --- o intake de SP-OP-CONTAS-001: ligado, sem efeito --------------------------------------------------


def test_intake_avalia_em_sombra_sem_mudar_a_saida(pin_com_prazos: rco_pin.RcoRulesPin) -> None:
    variaveis = _variaveis_contas()
    sem_sombra = contas.identify_glosa_entry(dict(variaveis))
    dmn = _dmn()
    with collect_dmn_versions() as versoes, collect_rco_avaliacoes():
        com_sombra = contas.identify_glosa_entry(dict(variaveis), dmn=dmn)
    assert com_sombra == sem_sombra  # nenhuma variavel de processo nasce nem muda
    assert not any(k.startswith("rco") or k in versoes[CHAVE] for k in com_sombra)
    [(chave, enviadas)] = dmn.calls
    assert chave == CHAVE
    # hoje nenhum termo tem produtor no Maezo: tudo nulo, com o motivo; nulo nunca vira 0
    assert enviadas["diasAteApresentacao"] is None
    assert enviadas["diasAlemDoPrazoContratual"] is None
    nulas = versoes[CHAVE]["entradasNulas"]
    assert nulas["diasAteApresentacao"] == "termo_sem_produtor_no_maezo"
    assert nulas["diasAteRecurso"] == "termo_sem_produtor_no_maezo"
    assert nulas["diasAlemDoPrazoContratual"] == "termo_sem_produtor_no_maezo"
    assert versoes[CHAVE]["derivacoes"]["diasAlemDoPrazoContratual"]["motivoTolerancia"] == "termo_nulo"
    assert at.efeito_k1_habilitado() is False


@pytest.mark.parametrize(
    ("tipo_lote", "regime"),
    [
        ("internacao", mt.REGIME_INTERNACAO),
        ("sadt", mt.REGIME_AMBULATORIAL),
        ("consulta", None),
        ("misto", None),
        ("", None),
    ],
)
def test_regime_vem_do_tipo_de_lote(tipo_lote: str, regime: str | None) -> None:
    entrada = contas.GlosaInput(tenant_id="t", numero_lote_tiss="l", tipo_lote=tipo_lote)
    fatos = contas.fatos_prazo_conta_rco(entrada)((1, 15))
    assert fatos.regime_guia == regime
    assert fatos.termos_sem_produtor == contas.TERMOS_SEM_PRODUTOR_CONTAS
    assert (fatos.guia, fatos.tiss, fatos.fora_tiss, fatos.cadastro) == ({}, {}, {}, {})


def test_data_de_hoje_do_intake_nunca_chega_ao_montador(pin_com_prazos: rco_pin.RcoRulesPin) -> None:
    """Lote sem recebimento: o intake segue pondo HOJE em `data_recebimento_lote` (comportamento de SLA,
    GAP-CONTAS-4), mas o montador nao le essa variavel: o termo e nulo por falta de produtor."""
    hoje = datetime.now(UTC).strftime("%Y-%m-%d")
    dmn = _dmn()
    with collect_dmn_versions() as versoes, collect_rco_avaliacoes():
        out = contas.identify_glosa_entry(_variaveis_contas(data_recebimento_lote=""), dmn=dmn)
    assert out["data_recebimento_lote"] == hoje  # o padrao do SLA continua, so para o SLA
    [(_, enviadas)] = dmn.calls
    assert hoje not in repr(enviadas)
    assert enviadas["diasAteApresentacao"] is None
    assert versoes[CHAVE]["entradasNulas"]["diasAteApresentacao"] == "termo_sem_produtor_no_maezo"
    entrada = contas.GlosaInput(tenant_id="t", numero_lote_tiss="l", data_recebimento_lote=hoje)
    assert hoje not in repr(contas.fatos_prazo_conta_rco(entrada)((1, 15)))


def test_intake_com_pin_real_nao_chama_o_motor() -> None:
    dmn = _dmn()
    out = contas.identify_glosa_entry(_variaveis_contas(), dmn=dmn)
    assert dmn.calls == []
    assert out == contas.identify_glosa_entry(_variaveis_contas())


async def test_harness_grava_a_auditoria_do_montador_e_completa_igual(
    pin_com_prazos: rco_pin.RcoRulesPin,
) -> None:
    """Pelo harness real: a entrada `rco_` do `AuditRecord` traz a auditoria do montador e a tarefa
    completa com exatamente as variaveis do intake sem sombra."""
    dmn = _dmn()
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)
    harness.register_worker(
        FunctionWorker(TOPICO, functools.partial(contas.identify_glosa_entry, dmn=dmn)),
        variables=tuple(_variaveis_contas()),
    )
    task = ExternalTask(
        task_id="task-1",
        topic=TOPICO,
        process_instance_id="proc-1",
        business_key="CONTAS-synthetic-tenant-synthetic-lote",
        worker_id="w-1",
        variables=_variaveis_contas(),
    )
    await harness._handle(task)
    assert transport.failures == []
    [(_, saida)] = transport.completed
    assert saida == contas.identify_glosa_entry(_variaveis_contas())
    entrada = sink.emitted[0][0].dmn_versions[CHAVE]
    assert entrada["shaDmn"] == SHA
    assert set(entrada["derivacoes"]["diasAlemDoPrazoContratual"]) == {
        "diasBrutos",
        "toleranciaAplicada",
        "valorEntregue",
        "motivoTolerancia",
    }
    assert entrada["entradasNulas"]["diasAteApresentacao"] == "termo_sem_produtor_no_maezo"
