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

(1b) ENTRADA LIST. As tabelas do AUTH leem entradas LIST na cerca PHI (`grupoCarencia`, `cptVigente`, ...).
Hoje nenhum adaptador do K3 v1.16 as produz (entram nulas); o teste
`test_auth_entrada_list_com_valor_chega_a_tabela_e_nao_sai_da_sombra` as preenche com valor valido e
distintivo (`grupoCarencia = parto_a_termo`, `cptVigente = true`) e prova que o valor chega ao motor e nao
aparece em nenhuma saida da sombra: nem como texto, nem como par nome-da-entrada/valor. Booleano nao escapa
da busca: so pode aparecer sob uma chave da lista fechada `CHAVES_BOOLEANAS`. As chaves da entrada `rco_` de
`dmn_versions` e as colunas do registro K1 sao listas FECHADAS: chave nova reprova ate ser revista aqui.
O intake de contas nao le entrada LIST (`rco_geral_prazos_conta`: datas, dias, tipo de transacao).

DERIVACOES. A auditoria do montador (MZO-TOL) grava em `derivacoes` o valor entregue a tabela e os dias
brutos de toda formula com ajuste (`rco_montador.FORMULAS[...].ajuste`; hoje so
`diasAlemDoPrazoContratual`, `rco_sombra.py` `auditoria_das_entradas`). E calendario do processo, CLEAN na
cerca PHI, e a regra e sobre variavel LIST. O teste
`test_derivacoes_so_levam_valor_de_entrada_que_nao_e_dado_de_saude` reprova se uma formula com ajuste for de
nome que o sweep vivo da cerca PHI nao conta CLEAN, ou com disposicao PHI.

(2) FALHA ENGOLIDA. Erro do motor, erro inesperado, prazo esgotado e falha de escrita do registro dentro da
sombra: no AUTH (depois do complete) a tarefa completa uma vez, com as mesmas variaveis, sem falha, sem
incidente e com o desfecho contado; no intake de contas (sincrono, dentro de `PRAZO_SOMBRA_S`) a saida da
tarefa e a mesma.
"""

from __future__ import annotations

import asyncio
import copy
import functools
from collections.abc import Iterator
from dataclasses import fields
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import structlog

from maezo.platform import observability
from maezo.platform import rco_ativacao as at
from maezo.platform.rco_registro import (
    COLUNAS,
    AvaliacaoRegra,
    ContextoDecisao,
    LinhaDecisao,
    contexto_da_tarefa,
    montar_linha,
)
from maezo.platform.validation import phi_completeness as phi
from maezo.platform.validation.result import Report
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
#: Valores das entradas LIST do AUTH (K3 v1.16): validos no dominio do dicionario 1.6.0 para chegar ao motor
#: e distintivos (nenhuma saida da sombra os escreve por desenho). `parto_a_termo` revela gestacao.
GRUPO_CARENCIA_SENTINELA = "parto_a_termo"
CPT_VIGENTE_SENTINELA = True
TEXTOS = (
    DATA_SENTINELA,
    DATA_SENTINELA.replace("-", ""),
    RECEBIMENTO_SENTINELA,
    CHAVE_NEGOCIO,
    GRUPO_CARENCIA_SENTINELA,
    *FICT.values(),
)
INTEIROS = (DIAS_SENTINELA,)
#: Lista FECHADA das chaves que podem levar booleano em qualquer saida da sombra (codigo fechado do proprio
#: registro, nunca valor de entrada). Booleano sob outra chave (ou solto numa lista) e achado.
CHAVES_BOOLEANAS = frozenset(
    {
        "amostrada",  # coluna do registro K1: a linha entrou na amostra (sombra: sempre false)
        "exige_aditivo",  # AvaliacaoRegra: atributo da regra pinada, nao do caso
        "sombra",  # AvaliacaoRegra: avaliacao feita pela sombra
        "peloPadrao",  # composicaoK1: o vencedor saiu da ordem padrao da composicao
    }
)
#: Os motivos de nulo do montador: unico valor que uma entrada da tabela pode ter como valor de chave numa
#: saida (`entradasNulas`). Qualquer outro valor sob o nome de uma entrada LIST e vazamento.
MOTIVOS_NULO = frozenset(m.value for m in mt.MotivoNulo)
#: Lista FECHADA das chaves da entrada `rco_` de `dmn_versions` (e das sub-chaves); chave nova reprova ate
#: ser revista aqui (ela poderia levar valor de entrada).
CHAVES_DMN_VERSIONS = frozenset(
    {
        "version",
        "id",
        "deploymentId",
        "shaDmn",
        "versaoRegra",
        "ativacaoPacote",
        "exigeAditivo",
        "idRegra",
        "estadoManual",
        "ativacaoEfetiva",
        "modo",
        "derivacoes",
        "entradasNulas",
        "versaoDicionarioEntradas",
        "origemAvaliacao",
        "composicaoK1",
        "sinaisAuditoria",
    }
)
CHAVES_COMPOSICAO_K1 = frozenset({"nome", "modo", "desfecho", "decisaoVencedora", "motivo", "peloPadrao"})
CHAVES_DERIVACAO = frozenset({"diasBrutos", "toleranciaAplicada", "valorEntregue", "motivoTolerancia"})
#: As colunas do registro K1 (`rco_decisao_regra`), lista FECHADA e na ordem do INSERT.
COLUNAS_REGISTRO = (
    "tenant",
    "chave_idempotencia",
    "id_regra",
    "versao_regra",
    "sha_dmn",
    "decision_key",
    "decisao",
    "classe_adverso",
    "motivo_tab38",
    "fila",
    "estado_manual",
    "ativacao_pacote",
    "ativacao_efetiva",
    "modo",
    "acao",
    "amostrada",
    "motivo_amostra",
    "id_chave_amostra",
    "ordinal",
)
CAMPOS_AVALIACAO = (
    "decision_key",
    "id_regra",
    "versao_regra",
    "sha_dmn",
    "decisao",
    "classe_adverso",
    "motivo_tab38",
    "fila",
    "estado_manual",
    "ativacao_pacote",
    "ativacao_efetiva",
    "modo",
    "exige_aditivo",
    "revisao_inicial_decisoes",
    "amostra_posterior_pct",
    "conferencia_negativa_admin_decisoes",
    "alertas",
    "sombra",
)

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


@functools.cache
def _sweep_vivo() -> phi.Sweep:
    """O sweep da cerca PHI sobre a arvore real `spec/processes` (o mesmo dos testes da cerca)."""
    relatorio = Report()
    sweep = phi.sweep_processes_root(Path(__file__).resolve().parents[4] / "spec" / "processes", relatorio)
    assert relatorio.ok, [achado.render() for achado in relatorio.findings]
    return sweep


@functools.cache
def _nomes_list() -> frozenset[str]:
    """As entradas das tabelas pinadas deste arquivo que a cerca PHI NAO conta CLEAN (sweep vivo, que ja
    inclui as disposicoes que elevam um nome)."""
    pin = rco_pin.rco_rules_pin()
    entradas: set[str] = set()
    for chave in (PRAZOS, *RCO_AUTH):
        decisao = pin.tables[chave].decisao
        assert decisao is not None, chave
        entradas.update(decisao.entradas)
    sweep = _sweep_vivo()
    return frozenset(n for n in entradas if sweep.bucket(n) != phi.CLEAN)


def _vazamentos(objeto: Any) -> list[str]:
    """Onde um sentinela aparece num objeto (dict/list/str/int/bool, recursivo). Inteiros por IGUALDADE (um
    `7919` dentro de um hash hex nao e vazamento), textos por substring, booleanos so sob chave de
    `CHAVES_BOOLEANAS`, e o nome de uma entrada LIST como chave so com um motivo de nulo como valor."""
    achados: list[str] = []

    def andar(valor: Any, caminho: str, chave: str | None = None) -> None:
        if isinstance(valor, bool):
            if chave not in CHAVES_BOOLEANAS:
                achados.append(f"{caminho}={valor} (booleano fora de CHAVES_BOOLEANAS)")
            return
        if isinstance(valor, int):
            if valor in INTEIROS:
                achados.append(f"{caminho}={valor}")
        elif isinstance(valor, str):
            achados.extend(f"{caminho}~{t}" for t in TEXTOS if t in valor)
        elif isinstance(valor, dict):
            for k, sub in valor.items():
                andar(str(k), f"{caminho}.<chave>")
                if k in _nomes_list() and sub not in MOTIVOS_NULO:
                    achados.append(f"{caminho}.{k}={sub!r} (par entrada LIST/valor)")
                andar(sub, f"{caminho}.{k}", str(k))
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
    """Linha do registro K1 e avaliacao: listas FECHADAS de colunas e de campos, e nenhum sentinela neles
    (booleano so sob `CHAVES_BOOLEANAS`)."""
    assert chamadas, "nenhuma linha de registro: o teste nao provaria nada"
    assert COLUNAS == COLUNAS_REGISTRO
    assert tuple(f.name for f in fields(LinhaDecisao)) == COLUNAS_REGISTRO
    assert tuple(f.name for f in fields(AvaliacaoRegra)) == CAMPOS_AVALIACAO
    for avaliacao, contexto in chamadas:
        linha = dict(zip(COLUNAS, montar_linha(avaliacao, contexto, None).params(), strict=True))
        assert not _vazamentos(linha), _vazamentos(linha)
        campos = {f.name: getattr(avaliacao, f.name) for f in fields(AvaliacaoRegra)}
        assert not _vazamentos(campos), _vazamentos(campos)
        assert not _vazamentos(repr(avaliacao))


def _assert_dmn_versions_fechado(versoes: Any, decisoes: Any) -> None:
    """A entrada `rco_` de `dmn_versions`: chaves em lista FECHADA; `entradasNulas` so nome de entrada da
    tabela com motivo de nulo; `derivacoes` so formula com ajuste, com as chaves do registro MZO-TOL."""
    assert set(versoes) == set(decisoes)
    for chave in decisoes:
        entrada = versoes[chave]
        assert set(entrada) <= CHAVES_DMN_VERSIONS, set(entrada) - CHAVES_DMN_VERSIONS
        assert set(entrada.get("composicaoK1", {})) <= CHAVES_COMPOSICAO_K1
        assert set(entrada.get("entradasNulas", {}).values()) <= MOTIVOS_NULO
        com_ajuste = {n for n, f in mt.FORMULAS.items() if f.ajuste}
        for nome, registro in entrada.get("derivacoes", {}).items():
            assert nome in com_ajuste, nome
            assert set(registro) == CHAVES_DERIVACAO, set(registro)


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
    _assert_dmn_versions_fechado(versoes, {PRAZOS})
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
    _assert_dmn_versions_fechado({PRAZOS: entrada}, {PRAZOS})
    _assert_registro_limpo(registro.chamadas)
    da_sombra = [e for e in logs if str(e["event"]).startswith("rco_")]
    assert da_sombra and not _vazamentos(da_sombra), _vazamentos(da_sombra)
    _assert_metricas_limpas()


def test_derivacoes_so_levam_valor_de_entrada_que_nao_e_dado_de_saude() -> None:
    """A auditoria do montador (MZO-TOL) grava em `derivacoes` o valor ENTREGUE a tabela e os dias brutos.

    Regra da coordenacao (PR do pin): o registro da sombra nao persiste valor de variavel LIST fora da
    zona PHI. So formula com `ajuste` grava derivacao (`rco_montador._montar_uma`); hoje so
    `diasAlemDoPrazoContratual`, calendario do processo (CLEAN na cerca PHI, sem disposicao). O teste fixa
    as duas coisas: o valor vai (desenho do MZO-TOL, conferivel pelo auditor) e TODA formula com ajuste
    (nao so a do prazo da conta) e um nome que o sweep vivo da cerca conta CLEAN, sem disposicao. Formula
    com ajuste nova de nome LIST ou com disposicao PHI reprova aqui, e quem a criar tem de tirar o valor da
    auditoria antes.
    """
    com_ajuste = {n for n, f in mt.FORMULAS.items() if f.ajuste}
    assert com_ajuste, "nenhuma formula com ajuste: o invariante nao provaria nada"
    sweep = _sweep_vivo()
    for nome in com_ajuste:
        assert nome not in phi.DISPOSITIONS, nome
        assert sweep.bucket(nome) == phi.CLEAN, (nome, sweep.bucket(nome))

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
    entregue = enviadas["diasAlemDoPrazoContratual"]
    assert isinstance(entregue, int)
    gravados = versoes[PRAZOS].get("derivacoes", {})
    assert set(gravados) == {"diasAlemDoPrazoContratual"}
    assert set(gravados) <= com_ajuste
    assert entregue in _inteiros(gravados)
    _assert_dmn_versions_fechado(versoes, {PRAZOS})


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
    _assert_dmn_versions_fechado(fato.dmn_versions, RCO_AUTH)
    assert not _vazamentos(fato.details), _vazamentos(fato.details)
    assert sorted(a.decision_key for a, _ in registro.chamadas) == sorted(RCO_AUTH)
    _assert_registro_limpo(registro.chamadas)
    da_sombra = [e for e in logs if str(e["event"]).startswith("rco_")]
    assert da_sombra and not _vazamentos(da_sombra), _vazamentos(da_sombra)
    _assert_metricas_limpas()


def _com_entradas_list(original: Any) -> Any:
    """`auth_rco.fatos_carencia_cpt_auth` com os fatos K3 v1.16 que nenhum adaptador produz ainda: o grupo de
    carencia e a CPT vigente do beneficiario na data do atendimento (entradas LIST das tabelas do AUTH)."""

    def fatos(process_vars: Any) -> Any:
        montar = original(process_vars)

        def com_k3(versao_k3: tuple[int, int]) -> mt.FatosDoCaso:
            base = montar(versao_k3)
            return mt.FatosDoCaso(
                guia=base.guia,
                versao_k3_pinada=max(versao_k3, mt.VERSAO_K3_ENTRADAS),
                k3=mt.FatosK3V116(
                    data_atendimento=DATA_SENTINELA,
                    grupo=GRUPO_CARENCIA_SENTINELA,
                    tem_cpt_vigente=CPT_VIGENTE_SENTINELA,
                ),
                carater_atendimento_guia=base.carater_atendimento_guia,
                tiss=base.tiss,
                tipo_guia=base.tipo_guia,
                termos_sem_produtor=base.termos_sem_produtor,
            )

        return com_k3

    return fatos


def test_auth_entrada_list_com_valor_chega_a_tabela_e_nao_sai_da_sombra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entradas LIST com VALOR (nao nulo) nas tabelas pinadas de carencia e CPT, pelo harness do AUTH: o
    motor as recebe e nenhuma saida da sombra as leva (fato `SHADOW`, `dmn_versions`, registro K1, logs,
    metricas), nem como texto, nem como booleano, nem como par nome-da-entrada/valor."""
    nomes_list = _nomes_list()
    assert {"grupoCarencia", "cptVigente"} <= nomes_list, nomes_list
    monkeypatch.setattr(
        auth_rco, "fatos_carencia_cpt_auth", _com_entradas_list(auth_rco.fatos_carencia_cpt_auth)
    )
    monkeypatch.setattr(mt, "lacuna_saldo_fechada", lambda: True)
    dmn = _motor(None, auth=True)
    registro = _Registro()
    h, transport, sink = _harness_auth(dmn, registro)
    with structlog.testing.capture_logs() as logs:
        asyncio.run(h._handle(_task(TOPICO_AUTH, _variaveis_auth())))
    assert transport.failures == [] and len(transport.completed) == 1
    rco = dict((k, v) for k, v in dmn.calls if k.startswith("rco_"))
    # nao vacuo: as entradas LIST chegaram com o valor as duas tabelas da composicao
    for chave in (CARENCIA, CPT):
        assert rco[chave]["grupoCarencia"] == GRUPO_CARENCIA_SENTINELA, (chave, rco[chave])
        assert rco[chave]["cptVigente"] is CPT_VIGENTE_SENTINELA, (chave, rco[chave])
    # ...e nao saem
    [fato] = [r for r, _ in sink.emitted if r.decision == harness_mod.AUDIT_DECISION_SOMBRA]
    assert set(fato.dmn_versions) == set(RCO_AUTH)
    _assert_dmn_versions_fechado(fato.dmn_versions, RCO_AUTH)
    for chave in (CARENCIA, CPT):
        nulas = fato.dmn_versions[chave].get("entradasNulas", {})
        assert "grupoCarencia" not in nulas and "cptVigente" not in nulas, nulas
    assert not _vazamentos(fato.dmn_versions), _vazamentos(fato.dmn_versions)
    assert not _vazamentos(fato.details), _vazamentos(fato.details)
    assert not _vazamentos(repr(fato)), _vazamentos(repr(fato))
    assert sorted(a.decision_key for a, _ in registro.chamadas) == sorted(RCO_AUTH)
    _assert_registro_limpo(registro.chamadas)
    da_sombra = [e for e in logs if str(e["event"]).startswith("rco_")]
    assert da_sombra and not _vazamentos(da_sombra), _vazamentos(da_sombra)
    _assert_metricas_limpas()
    # a sombra chamada direto, com os mesmos fatos: TODO log emitido durante ela fica limpo
    dmn = _motor(None, auth=False)
    with (
        structlog.testing.capture_logs() as logs,
        collect_dmn_versions() as versoes,
        collect_rco_avaliacoes() as avaliacoes,
    ):
        auth_rco.avaliar_em_sombra_auth(dmn, _variaveis_auth(), topico=TOPICO_AUTH)
    assert dict(dmn.calls)[CPT]["cptVigente"] is CPT_VIGENTE_SENTINELA
    assert logs and not _vazamentos(logs), _vazamentos(logs)
    assert not _vazamentos(versoes), _vazamentos(versoes)
    _assert_dmn_versions_fechado(versoes, RCO_AUTH)
    contexto = contexto_da_tarefa(tenant="amh", task_id="t-1", decision_key=CPT, business_key=CHAVE_NEGOCIO)
    _assert_registro_limpo([(avaliacoes[k], contexto) for k in RCO_AUTH])
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
