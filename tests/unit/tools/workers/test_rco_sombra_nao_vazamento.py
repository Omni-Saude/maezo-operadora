"""Sombra do K1 com o pin REAL: nenhum VALOR de entrada vaza, e nenhuma falha dela muda a tarefa.

Pin: o do repositorio (`rco_pin.rco_rules_pin()`, K1 b8444f52, `fixado`), sem nada substituido — as tabelas
`rco_geral_prazos_conta` (intake de contas), `rco_geral_carencia` + `rco_geral_cpt` (composicao
`composicao_carencia_cpt`) e `rco_geral_elegibilidade` (AUTH), o dicionario 1.6.0 do pin e a versao do K3 do
pin AMH real. O motor e o `FakeDmnTransport` (teste de unidade; ele registra o que recebeu, o que prova que os
valores-sentinela CHEGARAM as entradas da tabela).

(1) NAO VAZAMENTO. Valores-sentinela entram nas entradas das tabelas pinadas (a data de atendimento, os dias
ate a apresentacao) e em todas as variaveis de processo do caso (`FICT-SENTINELA-*`). Nenhum deles aparece nas
linhas do registro K1 (`montar_linha(...).params()`, as colunas de `rco_decisao_regra`/`_desfecho`), na
entrada `rco_` de `dmn_versions` (no fato `SHADOW` do AUTH e na auditoria do intake de contas), em log
(`structlog.testing.capture_logs`) ou em rotulo de metrica `maezo_rco*`. So nomes de regra e de decisao, ids,
codigos fechados e hashes.

ACHADO REGISTRADO (nao corrigido aqui, por instrucao): a auditoria do montador grava `derivacoes` com
`diasBrutos` e `valorEntregue` — o VALOR da entrada derivada `diasAlemDoPrazoContratual` — na entrada `rco_`
de `dmn_versions` (`src/maezo/tools/workers/rco_sombra.py:184` `auditoria_das_entradas`, mesclada em
`src/maezo/tools/workers/_audit_ctx.py:184`, origem `src/maezo/tools/workers/rco_montador.py:1370`). Hoje
nenhum caminho real o preenche (o intake de contas entrega os termos sem produtor), mas um produtor futuro o
faria. `test_derivacoes_gravam_o_valor_da_entrada_derivada` fixa o achado como `xfail(strict=True)`: no dia em
que a auditoria deixar de levar o valor, o teste passa e fica VERMELHO, e quem corrigir o remove.

(2) FALHA ENGOLIDA. Erro do motor, erro inesperado, prazo esgotado e falha de escrita do registro dentro da
sombra: no AUTH (depois do complete) a tarefa completa uma vez, com as mesmas variaveis, sem falha, sem
incidente e com o desfecho contado; no intake de contas (sincrono, dentro de `PRAZO_SOMBRA_S`) a saida da
tarefa e a mesma.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import json
from collections.abc import Iterator
from datetime import date, timedelta
from types import MappingProxyType
from typing import Any

import pytest
import structlog

from maezo.platform import observability
from maezo.platform import rco_ativacao as at
from maezo.platform.rco_registro import AvaliacaoRegra, ContextoDecisao, contexto_da_tarefa, montar_linha
from maezo.tools.workers import auth_rco, contas, rco_pin, rco_sombra
from maezo.tools.workers import harness as harness_mod
from maezo.tools.workers import rco_montador as mt
from maezo.tools.workers._audit_ctx import collect_dmn_versions, collect_rco_avaliacoes
from maezo.tools.workers.auth import ValidateAutoCriteriaWorker
from maezo.tools.workers.auth_criteria import CriteriaSources
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import ExternalTask, FakeAuditSink, FakeWorkerTransport, WorkerHarness

PRAZOS = contas.DECISAO_RCO_PRAZOS_CONTA
CARENCIA, CPT = "rco_geral_carencia", "rco_geral_cpt"
ELEGIBILIDADE = auth_rco.DECISAO_ELEGIBILIDADE
RCO_AUTH = (CARENCIA, CPT, ELEGIBILIDADE)
TOPICO_AUTH = "operadora.auth.validate_auto_criteria"
TOPICO_CONTAS = "operadora.contas.identify_glosa"

#: Sentinelas que ENTRAM nas tabelas: precisam passar tipo e dominio do dicionario para chegar ao motor.
DATA_SENTINELA = "1987-06-05"
DIAS_SENTINELA = 7919
RECEBIMENTO_SENTINELA = (date.fromisoformat(DATA_SENTINELA) + timedelta(days=DIAS_SENTINELA)).isoformat()
#: Sentinelas das variaveis de processo que nao sao entrada de tabela (identificadores do caso).
FICT = {
    "numero_guia_tiss": "FICT-SENTINELA-GUIA-0001",
    "beneficiario_id": "FICT-SENTINELA-BEN-0002",
    "prestador_id": "FICT-SENTINELA-PREST-0003",
    "numero_lote_tiss": "FICT-SENTINELA-LOTE-0004",
}
CHAVE_NEGOCIO = "FICT-SENTINELA-CHAVE-0005"
TEXTOS = (
    DATA_SENTINELA,
    DATA_SENTINELA.replace("-", ""),
    RECEBIMENTO_SENTINELA,
    CHAVE_NEGOCIO,
    *FICT.values(),
)
INTEIROS = (DIAS_SENTINELA,)

ROW_PRAZOS = {
    "decisao": "glosar",
    "motivoTab38": "1702",
    "idRegra": "OMNI-GERAL-GLO-0002",
    "fila": "faturamento",
    "classeAdverso": "contas",
}
ROW_CARENCIA = {
    "decisao": "negar",
    "motivoTab38": "1001",
    "idRegra": "OMNI-GERAL-CAR-0002",
    "fila": "cadastro",
    "classeAdverso": "administrativo",
    "sinalAuditoria": "aviso_urgencia_carencia",
}
ROW_CPT = {
    "decisao": "recomendar_negativa",
    "motivoTab38": "1002",
    "idRegra": "OMNI-GERAL-CAR-0004",
    "fila": "auditoria_medica",
    "classeAdverso": "clinico",
}
ROW_ELEGIBILIDADE = {
    "decisao": "negar",
    "motivoTab38": "1003",
    "idRegra": "OMNI-GERAL-ELG-0001",
    "fila": "cadastro",
    "classeAdverso": "administrativo",
}
_FONTES = ("dut_rol_coverage", "carencia_check", "auth_criteria_contratual")
SEM_PACOTE = rco_pin.RcoRulesPin(estado=rco_pin.ESTADO_SEM_PACOTE, tables=MappingProxyType({}))


@pytest.fixture(autouse=True)
def _pin_real(monkeypatch: pytest.MonkeyPatch) -> rco_pin.RcoRulesPin:
    monkeypatch.delenv(at.CHAVE_EFEITO_K1, raising=False)
    at.limpar_cache_estados()
    pin = rco_pin.rco_rules_pin()
    assert pin.estado == "fixado"
    for chave in (PRAZOS, *RCO_AUTH):
        assert chave in pin.tables
    return pin


# --- a busca: valor estruturado, nunca substring de hash ----------------------------------------------------


def _vazamentos(objeto: Any) -> list[str]:
    """Onde um sentinela aparece num objeto (dict/list/str/int, recursivo). Inteiros por IGUALDADE (um
    `7919` dentro de um hash hex nao e vazamento), textos por substring."""
    achados: list[str] = []

    def andar(valor: Any, caminho: str) -> None:
        if isinstance(valor, bool):
            return
        if isinstance(valor, int):
            if valor in INTEIROS:
                achados.append(f"{caminho}={valor}")
        elif isinstance(valor, str):
            achados.extend(f"{caminho}~{t}" for t in TEXTOS if t in valor)
        elif isinstance(valor, dict):
            for chave, sub in valor.items():
                andar(str(chave), f"{caminho}.<chave>")
                andar(sub, f"{caminho}.{chave}")
        elif isinstance(valor, (list, tuple, set, frozenset)):
            for i, sub in enumerate(valor):
                andar(sub, f"{caminho}[{i}]")
        elif valor is not None:
            andar(repr(valor), f"{caminho}<repr>")

    andar(objeto, "$")
    return achados


def _metricas_rco() -> Iterator[tuple[str, dict[str, str]]]:
    registro = observability._get_metrics_collector().registry
    for familia in registro.collect():
        if familia.name.startswith("maezo_rco"):
            for amostra in familia.samples:
                yield amostra.name, dict(amostra.labels)


def _assert_metricas_limpas() -> None:
    for nome, rotulos in _metricas_rco():
        assert not _vazamentos(rotulos), (nome, rotulos)


def _assert_registro_limpo(chamadas: list[tuple[AvaliacaoRegra, ContextoDecisao]]) -> None:
    assert chamadas, "nenhuma linha de registro: o teste nao provaria nada"
    for avaliacao, contexto in chamadas:
        assert not _vazamentos(montar_linha(avaliacao, contexto, None).params())
        assert not _vazamentos(repr(avaliacao))


class _Registro:
    def __init__(self, *, falha: BaseException | None = None) -> None:
        self.tenant = "amh"
        self.falha = falha
        self.chamadas: list[tuple[AvaliacaoRegra, ContextoDecisao]] = []

    async def registrar(self, avaliacao: AvaliacaoRegra, contexto: ContextoDecisao) -> bool:
        if self.falha is not None:
            raise self.falha
        self.chamadas.append((avaliacao, contexto))
        return True


# --- motores -----------------------------------------------------------------------------------------------


def _registrar(dmn: FakeDmnTransport, *, auth: bool) -> FakeDmnTransport:
    dmn.register(PRAZOS, [dict(ROW_PRAZOS)])
    dmn.register(CARENCIA, [dict(ROW_CARENCIA)])
    dmn.register(CPT, [dict(ROW_CPT)])
    dmn.register(ELEGIBILIDADE, [dict(ROW_ELEGIBILIDADE)])
    if auth:
        dmn.register("dut_rol_coverage", [{"no_rol": False, "requer_dut": False, "dut_ref": "NAO_APLICA"}])
        dmn.register("carencia_check", [{"carencia_cumprida": True, "prazo_restante_dias": 0}])
        dmn.register(
            "auth_criteria_contratual",
            [{"criterio_contratual_ok": False, "motivo": "SEM_REGRA_RATIFICADA"}],
        )
    return dmn


class _MotorQueFalha(FakeDmnTransport):
    """Levanta `erro` em toda tabela `rco_`; as tabelas do Maezo seguem normais."""

    def __init__(self, erro: BaseException) -> None:
        super().__init__()
        self.erro = erro

    async def evaluate(self, decision_key: str, variables: dict[str, Any], **kwargs: Any) -> Any:
        if decision_key.startswith("rco_"):
            self.calls.append((decision_key, dict(variables)))
            raise self.erro
        return await super().evaluate(decision_key, variables, **kwargs)


def _motor(falha: BaseException | None, *, auth: bool) -> FakeDmnTransport:
    return _registrar(FakeDmnTransport() if falha is None else _MotorQueFalha(falha), auth=auth)


# --- (1) nao vazamento: intake de contas -------------------------------------------------------------------


def _fatos_contas_com_sentinelas(versao_k3: tuple[int, int]) -> mt.FatosDoCaso:
    """Fatos com valores-sentinela nas entradas de `rco_geral_prazos_conta`, sem o prazo do contrato (que
    alimenta `derivacoes`: o achado de `test_derivacoes_gravam_o_valor_da_entrada_derivada`)."""
    return mt.FatosDoCaso(
        guia={"data_atendimento": DATA_SENTINELA, "tipo_transacao": "ENVIO_LOTE_GUIAS"},
        versao_k3_pinada=versao_k3,
        tiss={"data_final_faturamento": DATA_SENTINELA},
        fora_tiss={"data_recebimento_lote": RECEBIMENTO_SENTINELA},
        regime_guia=mt.REGIME_INTERNACAO,
    )


def test_contas_sentinelas_chegam_a_tabela_e_nao_saem_da_sombra() -> None:
    dmn = _motor(None, auth=False)
    with (
        structlog.testing.capture_logs() as logs,
        collect_dmn_versions() as versoes,
        collect_rco_avaliacoes() as avaliacoes,
    ):
        rco_sombra.avaliar_em_sombra(dmn, PRAZOS, _fatos_contas_com_sentinelas, topico=TOPICO_CONTAS)
    # nao vacuo: os sentinelas sao entradas da tabela pinada que o motor recebeu
    [(chave, enviadas)] = dmn.calls
    assert chave == PRAZOS
    assert enviadas["dataAtendimento"] == DATA_SENTINELA
    assert enviadas["diasAteApresentacao"] == DIAS_SENTINELA
    # ...e nao saem: auditoria, registro, log e metrica
    assert versoes[PRAZOS]["shaDmn"].startswith("sha256:")
    # sem o prazo do contrato a derivada sai nula: `derivacoes` leva so `None` e o codigo (o caso com valor e
    # o achado de `test_derivacoes_gravam_o_valor_da_entrada_derivada`)
    for registro in versoes[PRAZOS].get("derivacoes", {}).values():
        assert registro["valorEntregue"] is None and registro["diasBrutos"] is None
    assert not _vazamentos(versoes), _vazamentos(versoes)
    assert set(avaliacoes) == {PRAZOS}
    contexto = contexto_da_tarefa(tenant="amh", task_id="t-1", decision_key=PRAZOS, business_key="CONTAS-x")
    _assert_registro_limpo([(avaliacoes[PRAZOS], contexto)])
    assert logs and not _vazamentos(logs), _vazamentos(logs)
    _assert_metricas_limpas()


def _variaveis_contas() -> dict[str, Any]:
    return {
        "tenant_id": "amh",
        "numero_lote_tiss": FICT["numero_lote_tiss"],
        "valor_apresentado_brl": 1.0,
        "linhas_conta_refs": [{"valor_apresentado_centavos": 100}],
        "data_recebimento_lote": RECEBIMENTO_SENTINELA,
        "data_vencimento": "2026-09-30",
        "tipo_lote": "internacao",
        "numero_guia_tiss": FICT["numero_guia_tiss"],
        "beneficiario_id": FICT["beneficiario_id"],
        "prestador_id": FICT["prestador_id"],
    }


def _harness_contas(
    dmn: FakeDmnTransport, registro: _Registro
) -> tuple[WorkerHarness, FakeWorkerTransport, FakeAuditSink]:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    h = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink, rco_decisoes=registro)
    h.register_worker(
        FunctionWorker(TOPICO_CONTAS, functools.partial(contas.identify_glosa_entry, dmn=dmn)),
        variables=tuple(_variaveis_contas()),
    )
    return h, transport, sink


def _task(topico: str, variaveis: dict[str, Any]) -> ExternalTask:
    return ExternalTask(
        task_id="task-sentinela-1",
        topic=topico,
        process_instance_id="proc-sentinela-1",
        business_key=CHAVE_NEGOCIO,
        worker_id="w-1",
        variables=copy.deepcopy(variaveis),
    )


def test_contas_pelo_harness_nada_do_caso_no_registro_da_sombra() -> None:
    dmn = _motor(None, auth=False)
    registro = _Registro()
    h, transport, sink = _harness_contas(dmn, registro)
    with structlog.testing.capture_logs() as logs:
        asyncio.run(h._handle(_task(TOPICO_CONTAS, _variaveis_contas())))
    assert transport.failures == [] and len(transport.completed) == 1
    assert [chave for chave, _ in dmn.calls] == [PRAZOS]
    entrada = sink.emitted[0][0].dmn_versions[PRAZOS]
    assert not _vazamentos(entrada), _vazamentos(entrada)
    _assert_registro_limpo(registro.chamadas)
    da_sombra = [e for e in logs if str(e["event"]).startswith("rco_")]
    assert da_sombra and not _vazamentos(da_sombra), _vazamentos(da_sombra)
    _assert_metricas_limpas()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "ACHADO (nao corrigido): rco_sombra.py:184 auditoria_das_entradas grava derivacoes.valorEntregue e "
        "diasBrutos (o valor de diasAlemDoPrazoContratual) na entrada rco_ de dmn_versions"
    ),
)
def test_derivacoes_gravam_o_valor_da_entrada_derivada() -> None:
    """Com o prazo do contrato informado, o valor ENTREGUE a tabela vai a auditoria. Fica `xfail` estrito."""
    entregue: dict[str, Any] = {}

    def fatos(versao_k3: tuple[int, int]) -> mt.FatosDoCaso:
        base = _fatos_contas_com_sentinelas(versao_k3)
        return mt.FatosDoCaso(
            guia=base.guia,
            versao_k3_pinada=versao_k3,
            cadastro={"prazo_apresentacao_contratual_dias": 7},
            tiss=base.tiss,
            fora_tiss=base.fora_tiss,
            regime_guia=base.regime_guia,
        )

    dmn = _motor(None, auth=False)
    with collect_dmn_versions() as versoes, collect_rco_avaliacoes():
        rco_sombra.avaliar_em_sombra(dmn, PRAZOS, fatos, topico=TOPICO_CONTAS)
    [(_, enviadas)] = dmn.calls
    entregue["valor"] = enviadas["diasAlemDoPrazoContratual"]
    assert isinstance(entregue["valor"], int)
    gravados = versoes[PRAZOS].get("derivacoes", {})
    assert entregue["valor"] not in json.loads(json.dumps(_inteiros(gravados)))


def _inteiros(objeto: Any) -> list[int]:
    if isinstance(objeto, bool):
        return []
    if isinstance(objeto, int):
        return [objeto]
    if isinstance(objeto, dict):
        return [i for v in objeto.values() for i in _inteiros(v)]
    if isinstance(objeto, (list, tuple)):
        return [i for v in objeto for i in _inteiros(v)]
    return []


# --- (1) nao vazamento: AUTH (depois do complete) ----------------------------------------------------------


class _Resolver:
    def within_l2_ceiling(self, **_: Any) -> Any:
        return True


def _variaveis_auth() -> dict[str, Any]:
    return {
        "tenant_id": "amh",
        "numero_guia_tiss": FICT["numero_guia_tiss"],
        "beneficiario_id": FICT["beneficiario_id"],
        "prestador_id": FICT["prestador_id"],
        "valor_estimado_brl": 180.0,
        "codigo_procedimento_tuss": "10101012",
        "categoria_procedimento": "consulta",
        "tipo_procedimento": "eletivo",
        "dias_desde_adesao": 400,
        "cpt_declarada": False,
        "carater_atendimento": "eletivo",
        "data_atendimento": DATA_SENTINELA,
    }


def _harness_auth(
    dmn: FakeDmnTransport, registro: _Registro
) -> tuple[WorkerHarness, FakeWorkerTransport, FakeAuditSink]:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    worker = ValidateAutoCriteriaWorker(
        resolver=_Resolver(),  # type: ignore[arg-type]
        dmn=dmn,
        sources=CriteriaSources(ratified=frozenset(_FONTES), dut_criteria_by_ref={}),
    )
    h = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink, rco_decisoes=registro)
    h.register_worker(worker, variables=tuple(_variaveis_auth()))
    return h, transport, sink


def test_auth_sentinela_chega_a_tabela_e_nao_sai_da_sombra() -> None:
    """Pelo harness: o fato `SHADOW` (detalhes e `dmn_versions`), o registro K1, os logs `rco_*` e as
    metricas `maezo_rco*` nao levam a data de atendimento que entrou nas tres tabelas, nem os ids do caso."""
    dmn = _motor(None, auth=True)
    registro = _Registro()
    h, transport, sink = _harness_auth(dmn, registro)
    with structlog.testing.capture_logs() as logs:
        asyncio.run(h._handle(_task(TOPICO_AUTH, _variaveis_auth())))
    assert transport.failures == [] and len(transport.completed) == 1
    rco = [(k, v) for k, v in dmn.calls if k.startswith("rco_")]
    assert [k for k, _ in rco] == [CARENCIA, CPT, ELEGIBILIDADE]
    assert all(v["dataAtendimento"] == DATA_SENTINELA for _, v in rco)
    [(fato, dedup)] = [(r, k) for r, k in sink.emitted if r.decision == harness_mod.AUDIT_DECISION_SOMBRA]
    assert dedup == "amh:sombra:task-sentinela-1"
    assert set(fato.dmn_versions) == set(RCO_AUTH)
    assert not _vazamentos(fato.dmn_versions), _vazamentos(fato.dmn_versions)
    assert not _vazamentos(fato.details), _vazamentos(fato.details)
    assert sorted(a.decision_key for a, _ in registro.chamadas) == sorted(RCO_AUTH)
    _assert_registro_limpo(registro.chamadas)
    da_sombra = [e for e in logs if str(e["event"]).startswith("rco_")]
    assert da_sombra and not _vazamentos(da_sombra), _vazamentos(da_sombra)
    _assert_metricas_limpas()


def test_auth_sombra_direta_nenhum_log_leva_o_valor() -> None:
    """A sombra do AUTH chamada direto: TODO log emitido durante ela (nao so os `rco_*`) fica limpo."""
    dmn = _motor(None, auth=False)
    with (
        structlog.testing.capture_logs() as logs,
        collect_dmn_versions() as versoes,
        collect_rco_avaliacoes() as avaliacoes,
    ):
        auth_rco.avaliar_em_sombra_auth(dmn, _variaveis_auth(), topico=TOPICO_AUTH)
    assert [k for k, _ in dmn.calls] == [CARENCIA, CPT, ELEGIBILIDADE]
    assert logs and not _vazamentos(logs), _vazamentos(logs)
    assert not _vazamentos(versoes)
    assert set(avaliacoes) == set(RCO_AUTH)
    _assert_metricas_limpas()


# --- (2) falha engolida: AUTH ------------------------------------------------------------------------------

FALHAS_MOTOR: dict[str, BaseException] = {
    "erro_do_motor": DmnEvaluationError("motor fora"),
    "erro_inesperado_do_motor": RuntimeError("bug no transporte"),
    "prazo_esgotado": TimeoutError(),
}
#: O desfecho de `maezo_rco_sombra_total` que cada falha produz (por porta de `rco_sombra`).
DESFECHO_FALHA = {
    "erro_do_motor": rco_sombra.AVALIACAO_FALHOU,
    "erro_inesperado_do_motor": rco_sombra.ERRO_DE_PROGRAMACAO,
    "prazo_esgotado": rco_sombra.PRAZO_ESGOTADO,
}


@pytest.fixture
def contadores(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Capta as metricas que contam a sombra e o desfecho da tarefa (incidente = `incident`/`failed`)."""
    capt: dict[str, list[Any]] = {"sombra": [], "pos": [], "tarefa": []}
    monkeypatch.setattr(rco_sombra, "record_rco_sombra", capt["sombra"].append)
    monkeypatch.setattr(harness_mod, "record_rco_sombra_pos_conclusao", capt["pos"].append)

    def outcome(**kw: Any) -> None:
        capt["tarefa"].append(kw["outcome"])

    monkeypatch.setattr(observability, "record_worker_task_outcome", outcome)
    return capt


def _baseline_auth(monkeypatch: pytest.MonkeyPatch, pin: rco_pin.RcoRulesPin) -> list[dict[str, Any]]:
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: SEM_PACOTE)
    h, transport, _ = _harness_auth(_motor(None, auth=True), _Registro())
    asyncio.run(h._handle(_task(TOPICO_AUTH, _variaveis_auth())))
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: pin)
    return [v for _, v in transport.completed]


def _assert_sem_incidente(transport: FakeWorkerTransport, capt: dict[str, list[Any]]) -> None:
    assert transport.failures == []
    assert transport.bpmn_errors == []
    assert len(transport.completed) == 1
    assert "incident" not in capt["tarefa"] and "failed" not in capt["tarefa"]
    assert capt["tarefa"] == ["completed"]


@pytest.mark.parametrize("falha", sorted(FALHAS_MOTOR))
def test_auth_falha_do_motor_na_sombra_e_engolida_contada_e_sem_incidente(
    _pin_real: rco_pin.RcoRulesPin,
    monkeypatch: pytest.MonkeyPatch,
    contadores: dict[str, list[Any]],
    falha: str,
) -> None:
    baseline = _baseline_auth(monkeypatch, _pin_real)
    for lista in contadores.values():
        lista.clear()
    dmn = _motor(FALHAS_MOTOR[falha], auth=True)
    registro = _Registro()
    h, transport, _ = _harness_auth(dmn, registro)
    asyncio.run(h._handle(_task(TOPICO_AUTH, _variaveis_auth())))
    # a falha foi exercitada: as tabelas rco_ foram chamadas e levantaram
    assert [k for k, _ in dmn.calls if k.startswith("rco_")]
    _assert_sem_incidente(transport, contadores)
    assert [v for _, v in transport.completed] == baseline
    assert DESFECHO_FALHA[falha] in contadores["sombra"], contadores["sombra"]
    assert contadores["pos"] == ["executada"]  # o trabalho pos-conclusao rodou e terminou
    assert registro.chamadas == []  # nenhuma regra casada: nada a registrar


def test_auth_falha_de_escrita_do_registro_e_engolida_sem_incidente(
    _pin_real: rco_pin.RcoRulesPin, monkeypatch: pytest.MonkeyPatch, contadores: dict[str, list[Any]]
) -> None:
    """A escrita do registro K1 falha (banco fora): fail-open em `modo=sombra`. A tarefa completa igual; o que
    conta a falha e o log `rco_decisao_registro_falhou_fail_open` (so tipo e codigo) — nao ha metrica propria
    da escrita do registro (o desfecho pos-conclusao segue `executada`)."""
    baseline = _baseline_auth(monkeypatch, _pin_real)
    for lista in contadores.values():
        lista.clear()
    registro = _Registro(falha=ConnectionError("FICT-SENTINELA-DRIVER Failing row contains ..."))
    h, transport, sink = _harness_auth(_motor(None, auth=True), registro)
    with structlog.testing.capture_logs() as logs:
        asyncio.run(h._handle(_task(TOPICO_AUTH, _variaveis_auth())))
    _assert_sem_incidente(transport, contadores)
    assert [v for _, v in transport.completed] == baseline
    falhas = [e for e in logs if e["event"] == "rco_decisao_registro_falhou_fail_open"]
    assert len(falhas) == len(RCO_AUTH)
    assert {e["erro"] for e in falhas} == {"ConnectionError"}
    assert not any("FICT-SENTINELA-DRIVER" in repr(e) for e in logs)
    assert contadores["pos"] == ["executada"]
    assert any(r.decision == harness_mod.AUDIT_DECISION_SOMBRA for r, _ in sink.emitted)


def test_auth_excecao_na_auditoria_da_sombra_e_engolida_sem_incidente(
    _pin_real: rco_pin.RcoRulesPin, monkeypatch: pytest.MonkeyPatch, contadores: dict[str, list[Any]]
) -> None:
    h, transport, sink = _harness_auth(_motor(None, auth=True), _Registro())
    emit_once = sink.emit_once

    async def falha(record: Any, *, dedup_key: str) -> str:
        if ":sombra:" in dedup_key:
            raise RuntimeError("cadeia fora")
        return await emit_once(record, dedup_key=dedup_key)

    sink.emit_once = falha  # type: ignore[method-assign]
    asyncio.run(h._handle(_task(TOPICO_AUTH, _variaveis_auth())))
    _assert_sem_incidente(transport, contadores)
    assert contadores["pos"] == ["auditoria_falhou"]


# --- (2) falha engolida: intake de contas (sincrono, dentro de PRAZO_SOMBRA_S) -----------------------------


@pytest.mark.parametrize("falha", sorted(FALHAS_MOTOR))
def test_contas_falha_do_motor_na_sombra_nao_muda_a_saida(
    contadores: dict[str, list[Any]], falha: str
) -> None:
    esperado = contas.identify_glosa_entry(_variaveis_contas())  # sem transporte: sombra inerte
    contadores["sombra"].clear()
    dmn = _motor(FALHAS_MOTOR[falha], auth=False)
    assert contas.identify_glosa_entry(_variaveis_contas(), dmn=dmn) == esperado
    assert [k for k, _ in dmn.calls] == [PRAZOS]
    assert contadores["sombra"] == [DESFECHO_FALHA[falha]]


def test_contas_prazo_da_sombra_e_o_teto_da_chamada_ao_motor(
    monkeypatch: pytest.MonkeyPatch, contadores: dict[str, list[Any]]
) -> None:
    """Motor que nao responde: a chamada e cortada em `PRAZO_SOMBRA_S` (aqui reduzido); a saida nao muda."""
    monkeypatch.setattr(rco_sombra, "PRAZO_SOMBRA_S", 0.05)

    class _Lento(FakeDmnTransport):
        async def evaluate(self, decision_key: str, variables: dict[str, Any], **kwargs: Any) -> Any:
            self.calls.append((decision_key, dict(variables)))
            await asyncio.sleep(30)
            raise AssertionError("nunca chega")

    esperado = contas.identify_glosa_entry(_variaveis_contas())
    contadores["sombra"].clear()
    dmn = _Lento()
    assert contas.identify_glosa_entry(_variaveis_contas(), dmn=dmn) == esperado
    assert contadores["sombra"] == [rco_sombra.PRAZO_ESGOTADO]


@pytest.mark.parametrize("falha", ["erro_do_motor", "escrita_do_registro"])
def test_contas_pelo_harness_falha_da_sombra_completa_igual_sem_incidente(
    _pin_real: rco_pin.RcoRulesPin,
    monkeypatch: pytest.MonkeyPatch,
    contadores: dict[str, list[Any]],
    falha: str,
) -> None:
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: SEM_PACOTE)
    h0, t0, _ = _harness_contas(_motor(None, auth=False), _Registro())
    asyncio.run(h0._handle(_task(TOPICO_CONTAS, _variaveis_contas())))
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: _pin_real)
    for lista in contadores.values():
        lista.clear()
    if falha == "erro_do_motor":
        dmn, registro = _motor(DmnEvaluationError("motor fora"), auth=False), _Registro()
    else:
        dmn, registro = _motor(None, auth=False), _Registro(falha=ConnectionError("banco fora"))
    h, transport, _ = _harness_contas(dmn, registro)
    with structlog.testing.capture_logs() as logs:
        asyncio.run(h._handle(_task(TOPICO_CONTAS, _variaveis_contas())))
    _assert_sem_incidente(transport, contadores)
    assert [v for _, v in transport.completed] == [v for _, v in t0.completed]
    if falha == "erro_do_motor":
        assert contadores["sombra"] == [rco_sombra.AVALIACAO_FALHOU]
    else:
        assert contadores["sombra"] == [rco_sombra.AVALIADA]
        assert [e["erro"] for e in logs if e["event"] == "rco_decisao_registro_falhou_fail_open"] == [
            "ConnectionError"
        ]
