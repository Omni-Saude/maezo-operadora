"""A sombra do K1 no portao de criterios do AUTH nao produz efeito (DL-0187).

Molde: `test_rco_sombra_efeito.py`. O pin e `fixado`, carregado pelo leitor de PRODUCAO
(`rco_pin.load_rco_rules_pin`) de um lock em `tmp_path`, com o recorte REAL do K1 2.3.0
(`tests/fixtures/rco/k1-2.3.0-recorte/`, aed187d4): os DMN e as entradas de manifesto REAIS de
`rco_geral_carencia` e `rco_geral_cpt`, a composicao real `composicao_carencia_cpt` (`mais_restritiva`), o
`vocabulario.json` e o `k6-filas.json` reais e o dicionario de entradas 1.5.0 (recorte).
`rco_geral_elegibilidade` entra com a entrada REAL do manifesto (nao e membro de grupo) e um DMN sintetico
(o recorte nao traz o arquivo dela). Os demais itens do pacote (prova do motor, exemplo) sao os do pacote
sintetico (`rco_k1_sintetico`). Nada de `_dicionario`/`_versao_k3_pinada` substituido: o dicionario e o do
pin e a versao do K3 e a do pin AMH real do repositorio.

O motor e o `FakeDmnTransport` com o PIOR caso: carencia `negar` (OMNI-GERAL-CAR-0002, com o sinal
`aviso_urgencia_carencia`), CPT `recomendar_negativa`, elegibilidade `negar`. "Neutralizado" e o pin
`sem_pacote` (o de producao hoje). Prova:

(a) `execute` devolve o MESMO dicionario com a sombra ligada e neutralizada, numa matriz de variaveis;
(b) pelo harness: mesmas variaveis completas, sem falha, nada no outbox (`suprimido_sombra`), auditoria com
    `composicaoK1` em `modo=sombra`, registro de decisoes K1 so em sombra;
(c) com `MAEZO_RCO_EFEITO_K1` ligado e `efeito_k1_habilitado` levantando: `modo=sombra` sem ordinal; AST:
    `auth.py` e `auth_rco.py` nao citam o indicador;
(d) falhas injetadas nao mudam as variaveis nem falham a tarefa;
(e) lista de permissao: guia, beneficiario, prestador e chave de negocio nunca vao a log, auditoria,
    registro ou metrica; e a metrica de cobertura das entradas.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import structlog

from maezo.adapters.amh.contract import AmhContractPinError
from maezo.platform import observability
from maezo.platform import rco_ativacao as at
from maezo.platform import rco_composicao as cmp
from maezo.platform.rco_manifesto import ManifestoK1InvalidoError
from maezo.platform.rco_registro import AvaliacaoRegra, ContextoDecisao, montar_linha
from maezo.tools.workers import auth, auth_rco, rco_montador, rco_pin, rco_sombra
from maezo.tools.workers import harness as harness_mod
from maezo.tools.workers._audit_ctx import collect_dmn_versions, collect_rco_avaliacoes
from maezo.tools.workers.auth import ValidateAutoCriteriaWorker
from maezo.tools.workers.auth_criteria import CriteriaSources
from maezo.tools.workers.dmn_transport import DmnVersion, FakeDmnTransport
from maezo.tools.workers.harness import ExternalTask, FakeAuditSink, FakeWorkerTransport, WorkerHarness
from tests.unit.platform import rco_k1_2_3_recorte as rec
from tests.unit.platform import rco_k1_sintetico as sint
from tests.unit.platform.test_rco_avisos import OutboxEmMemoria

CARENCIA, CPT = rec.ORDEM
ELEGIBILIDADE = auth_rco.DECISAO_ELEGIBILIDADE
GRUPO = auth_rco.COMPOSICAO_CARENCIA_CPT
TOPICO = "operadora.auth.validate_auto_criteria"
SINAL = "aviso_urgencia_carencia"
ROW_CARENCIA = {
    "decisao": "negar",
    "motivoTab38": "1001",
    "idRegra": "OMNI-GERAL-CAR-0002",
    "fila": "cadastro",
    "classeAdverso": "administrativo",
    "sinalAuditoria": SINAL,
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
RCO = (CARENCIA, CPT, ELEGIBILIDADE)
#: Identificadores sinteticos do caso: nenhum pode chegar a log, auditoria, registro ou metrica.
GUIA = "GUIA-SINT-AUTH-3131"
BENEFICIARIO = "BEN-SINT-AUTH-4242"
PRESTADOR = "PREST-SINT-AUTH-5353"
CHAVE_NEGOCIO = f"AUTH-amh-{GUIA}"
DATA = "2026-11-03"
PROIBIDOS = (GUIA, BENEFICIARIO, PRESTADOR, CHAVE_NEGOCIO, DATA)
_FONTES = ("dut_rol_coverage", "carencia_check", "auth_criteria_contratual")


# --- o pacote: recorte REAL do K1 2.3.0 pinado por um lock -------------------------------------------------


def _montar_pacote(raiz: Path) -> None:
    manifesto = sint.montar(raiz)
    rco = raiz / sint.RULES_DIR
    k1 = raiz / sint.K1_DIR
    for arquivo in rco.glob("*.dmn"):
        arquivo.unlink()
    real = json.loads((rec.RECORTE / "manifesto.json").read_bytes())
    decisoes = []
    for d in real["decisoes"]:
        if d["id"] in (CARENCIA, CPT):
            raw = (rec.RECORTE / d["arquivo"]).read_bytes()
            assert sint.sha(raw) == d["sha256"]  # os bytes reais, sem alteracao
        elif d["id"] == ELEGIBILIDADE:
            saidas = [s["nome"] for s in d["saidas"]]
            raw = sint.dmn_bytes(
                ELEGIBILIDADE,
                saidas,
                [
                    ['"liberar"', '""', '"OMNI-GERAL-ELG-0001"', '""', '""'],
                    ['"negar"', '"1003"', '"OMNI-GERAL-ELG-0001"', '"cadastro"', '"administrativo"'],
                ],
            )
            d = dict(d, sha256=sint.sha(raw))
        else:
            continue
        (rco / f"{d['id_original']}.dmn").write_bytes(raw)
        decisoes.append(d)
    manifesto["decisoes"] = sorted(decisoes, key=lambda d: d["id"])
    manifesto["formato_manifesto"] = "2.3.0"
    manifesto["composicoes"] = [c for c in real["composicoes"] if c["nome"] == GRUPO]
    for nome in ("vocabulario.json", "k6-filas.json"):
        (k1 / nome).write_bytes((rec.RECORTE / nome).read_bytes())
    manifesto["vocabulario"] = real["vocabulario"]
    manifesto["k6"] = real["k6"]
    raw_entradas = (rec.RECORTE / "entradas-recorte.json").read_bytes()
    dic = json.loads(raw_entradas)
    (k1 / "entradas.json").write_bytes(raw_entradas)
    manifesto["dicionario_entradas"] = {
        "path": "entradas.json",
        "sha256": dic["sha256_conteudo"],
        "sha256_arquivo": sint.sha(raw_entradas),
        "versao": dic["versao"],
    }
    manifesto["exemplos_sinteticos"][0]["decisao"] = CARENCIA
    lock = sint.lock(raiz)
    lock["artifacts"] = [
        {"path": d["caminho_consumidor"], "sha256": d["sha256"]} for d in manifesto["decisoes"]
    ]
    sint.gravar_lock(raiz, lock)
    sint.regravar(raiz, manifesto)


SEM_PACOTE = rco_pin.RcoRulesPin(estado=rco_pin.ESTADO_SEM_PACOTE, tables=MappingProxyType({}))


@pytest.fixture
def pin_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> rco_pin.RcoRulesPin:
    _montar_pacote(tmp_path)
    pin = rco_pin.load_rco_rules_pin(tmp_path / sint.LOCK_REL, rules_dir=tmp_path / sint.RULES_DIR)
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: pin)
    monkeypatch.delenv(at.CHAVE_EFEITO_K1, raising=False)
    at.limpar_cache_estados()
    return pin


def _pinar(monkeypatch: pytest.MonkeyPatch, pin: rco_pin.RcoRulesPin) -> None:
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: pin)


# --- o worker e as variaveis -------------------------------------------------------------------------------


class _Resolver:
    def __init__(self, within: Any = True) -> None:
        self._within = within

    def within_l2_ceiling(self, **_: Any) -> Any:
        return self._within


class _Degradado(ValidateAutoCriteriaWorker):
    """Forca a degradacao (design §6): o calculo dos criterios levanta."""

    def _evaluate_criteria(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("calculo indisponivel")


def _dmn(*, tabelas_auth: bool = True) -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register(CARENCIA, [dict(ROW_CARENCIA)])
    dmn.register(CPT, [dict(ROW_CPT)])
    dmn.register(ELEGIBILIDADE, [dict(ROW_ELEGIBILIDADE)])
    if tabelas_auth:
        dmn.register("dut_rol_coverage", [{"no_rol": False, "requer_dut": False, "dut_ref": "NAO_APLICA"}])
        dmn.register("carencia_check", [{"carencia_cumprida": True, "prazo_restante_dias": 0}])
        dmn.register(
            "auth_criteria_contratual",
            [{"criterio_contratual_ok": False, "motivo": "SEM_REGRA_RATIFICADA"}],
        )
    return dmn


def _vars(**trocas: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "numero_guia_tiss": GUIA,
        "beneficiario_id": BENEFICIARIO,
        "prestador_id": PRESTADOR,
        "valor_estimado_brl": 180.0,
        "codigo_procedimento_tuss": "10101012",
        "categoria_procedimento": "consulta",
        "tipo_procedimento": "eletivo",
        "dias_desde_adesao": 400,
        "cpt_declarada": False,
        "carater_atendimento": "eletivo",
        "data_atendimento": DATA,
    }
    for chave, valor in trocas.items():
        if valor is _REMOVER:
            base.pop(chave, None)
        else:
            base[chave] = valor
    return base


_REMOVER = object()

#: (nome, variaveis, tabelas do AUTH registradas, worker degradado, fontes todas ratificadas)
CASOS: list[tuple[str, dict[str, Any], bool, bool, bool]] = [
    ("financeiro_aprova_tudo_ratificado", _vars(), True, False, True),
    ("fontes_reais_do_manifesto", _vars(), True, False, False),
    ("tabela_indisponivel", _vars(), False, False, True),
    ("degradacao_forcada", _vars(), True, True, True),
    ("carater_urgencia", _vars(carater_atendimento="urgencia"), True, False, True),
    ("carater_eletivo_sem_data", _vars(data_atendimento=_REMOVER), True, False, True),
    ("data_fora_do_padrao", _vars(data_atendimento="03/11/2026"), True, False, True),
]


def _worker(dmn: FakeDmnTransport, *, degradado: bool, ratificadas: bool) -> ValidateAutoCriteriaWorker:
    classe = _Degradado if degradado else ValidateAutoCriteriaWorker
    fontes = CriteriaSources(ratified=frozenset(_FONTES), dut_criteria_by_ref={}) if ratificadas else None
    return classe(resolver=_Resolver(True), dmn=dmn, sources=fontes)  # type: ignore[arg-type]


def _rco_chamadas(dmn: FakeDmnTransport) -> list[str]:
    return [chave for chave, _ in dmn.calls if chave.startswith("rco_")]


# --- (a) mesma saida com a sombra ligada e neutralizada ---------------------------------------------------


def test_pin_fixado_real_expoe_o_grupo_e_a_elegibilidade_fora_dele(pin_real: rco_pin.RcoRulesPin) -> None:
    assert pin_real.estado == rco_pin.ESTADO_FIXADO
    assert [(c.nome, c.decisoes, c.regra) for c in pin_real.composicoes] == [
        (GRUPO, rec.ORDEM, "mais_restritiva")
    ]
    assert not cmp.membro_de_composicao(pin_real, ELEGIBILIDADE)
    assert dict(pin_real.avisos)[SINAL].filas == ("auditoria_medica", "cadastro")
    # a elegibilidade tambem e "sozinha" no manifesto REAL inteiro (nao so no pacote deste teste)
    assert not cmp.membro_de_composicao(rec.manifesto(), ELEGIBILIDADE)


@pytest.mark.parametrize(("nome", "variaveis", "tabelas", "degradado", "ratificadas"), CASOS)
def test_execute_identico_com_sombra_ligada_e_neutralizada(
    pin_real: rco_pin.RcoRulesPin,
    monkeypatch: pytest.MonkeyPatch,
    nome: str,
    variaveis: dict[str, Any],
    tabelas: bool,
    degradado: bool,
    ratificadas: bool,
) -> None:
    dmn_ligada = _dmn(tabelas_auth=tabelas)
    entrada = copy.deepcopy(variaveis)
    with collect_dmn_versions() as versoes, collect_rco_avaliacoes() as avaliacoes:
        ligada = _worker(dmn_ligada, degradado=degradado, ratificadas=ratificadas).execute(entrada)
    assert entrada == variaveis  # a sombra nao muda as variaveis de entrada
    # a sombra rodou de verdade, com o pior caso: as tres tabelas `rco_` avaliadas, em sombra
    assert sorted(_rco_chamadas(dmn_ligada)) == sorted(RCO), nome
    assert {versoes[k]["modo"] for k in RCO} == {"sombra"}
    assert {(avaliacoes[k].sombra, avaliacoes[k].tem_ordinal) for k in RCO} == {(True, False)}

    _pinar(monkeypatch, SEM_PACOTE)
    dmn_neutra = _dmn(tabelas_auth=tabelas)
    neutra = _worker(dmn_neutra, degradado=degradado, ratificadas=ratificadas).execute(
        copy.deepcopy(variaveis)
    )
    assert _rco_chamadas(dmn_neutra) == []
    assert ligada == neutra
    for chave in (
        "auto_criteria_shadow",
        "auto_criteria_falhas",
        "motivo_bloqueio_criterios",
        "auto_criteria_verificado",
        "criterio_tecnico_ok",
        "criterio_financeiro_ok",
        "criterio_regulatorio_ok",
        "criterio_contratual_ok",
    ):
        assert ligada[chave] == neutra[chave], chave
    assert not any(isinstance(v, str) and v.startswith("rco_") for v in ligada.values())


def test_financeiro_aprova_e_a_sombra_adversa_nao_muda_nada(pin_real: rco_pin.RcoRulesPin) -> None:
    """Controle do caso mais sensivel: o criterio financeiro aprova, a sombra diz `negar`."""
    saida = _worker(_dmn(), degradado=False, ratificadas=True).execute(_vars())
    assert saida["criterio_financeiro_ok"] is True
    assert "negar" not in json.dumps(saida) and "recomendar_negativa" not in json.dumps(saida)


# --- (b) pelo harness ------------------------------------------------------------------------------------


class _Registro:
    def __init__(self) -> None:
        self.tenant = "amh"
        self.chamadas: list[tuple[AvaliacaoRegra, ContextoDecisao]] = []

    async def registrar(self, avaliacao: AvaliacaoRegra, contexto: ContextoDecisao) -> bool:
        self.chamadas.append((avaliacao, contexto))
        return True


def _harness(
    dmn: FakeDmnTransport, *, degradado: bool = False, worker: ValidateAutoCriteriaWorker | None = None
) -> tuple[WorkerHarness, FakeWorkerTransport, FakeAuditSink, _Registro, OutboxEmMemoria]:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    registro = _Registro()
    outbox = OutboxEmMemoria()
    h = WorkerHarness(
        transport,
        worker_id="w",
        tenant="amh",
        audit_sink=sink,
        rco_decisoes=registro,
        rco_avisos=outbox,
    )
    h.register_worker(worker or _worker(dmn, degradado=degradado, ratificadas=True), variables=tuple(_vars()))
    return h, transport, sink, registro, outbox


def _task(variaveis: dict[str, Any] | None = None) -> ExternalTask:
    return ExternalTask(
        task_id="task-auth-1",
        topic=TOPICO,
        process_instance_id="proc-auth-1",
        business_key=CHAVE_NEGOCIO,
        worker_id="w-1",
        variables=copy.deepcopy(variaveis if variaveis is not None else _vars()),
    )


def _rodar(h: WorkerHarness, task: ExternalTask) -> None:
    asyncio.run(h._handle(task))


@pytest.fixture
def avisos_contados(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    contados: list[tuple[str, int]] = []

    def gravar(desfecho: str, quantidade: int = 1) -> None:
        if quantidade > 0:
            contados.append((desfecho, quantidade))

    monkeypatch.setattr(harness_mod, "record_rco_aviso", gravar)
    return contados


@pytest.mark.parametrize("degradado", [False, True])
def test_harness_completa_igual_sem_aviso_e_registra_so_sombra(
    pin_real: rco_pin.RcoRulesPin,
    monkeypatch: pytest.MonkeyPatch,
    avisos_contados: list[tuple[str, int]],
    degradado: bool,
) -> None:
    com, t_com, sink, registro, outbox = _harness(_dmn(), degradado=degradado)
    with structlog.testing.capture_logs() as logs:
        _rodar(com, _task())
    _pinar(monkeypatch, SEM_PACOTE)
    sem, t_sem, _, registro_sem, _ = _harness(_dmn(), degradado=degradado)
    _rodar(sem, _task())

    assert t_com.failures == [] and t_sem.failures == []
    assert len(t_com.completed) == 1
    assert [v for _, v in t_com.completed] == [v for _, v in t_sem.completed]
    # sombra nao notifica: nada no outbox, o sinal contado como suprimido (DL-0187)
    assert outbox.linhas == {}
    assert avisos_contados == [("suprimido_sombra", 1)]
    # a auditoria duravel leva o modo e o sinal nas entradas do grupo
    dmn_versions = sink.emitted[0][0].dmn_versions
    for membro in rec.ORDEM:
        assert dmn_versions[membro]["composicaoK1"] == {
            "nome": GRUPO,
            "modo": "sombra",
            "desfecho": cmp.DESFECHO_ESCOLHIDO,
            "decisaoVencedora": CARENCIA,
            "motivo": None,
            "peloPadrao": False,
        }
        assert dmn_versions[membro]["sinaisAuditoria"] == [SINAL]
        assert dmn_versions[membro]["modo"] == "sombra"
    assert "composicaoK1" not in dmn_versions[ELEGIBILIDADE]
    # o registro de decisoes K1: so sombra, sem ordinal
    assert sorted(a.decision_key for a, _ in registro.chamadas) == sorted(RCO)
    assert {(a.modo, a.sombra, a.tem_ordinal) for a, _ in registro.chamadas} == {("sombra", True, False)}
    assert registro_sem.chamadas == []
    _assert_permitidos(logs, dmn_versions, registro)


# --- (c) indicador do K1 ligado: continua sombra -----------------------------------------------------------


def test_indicador_ligado_e_levantando_segue_sombra_sem_ordinal(
    pin_real: rco_pin.RcoRulesPin, monkeypatch: pytest.MonkeyPatch, avisos_contados: list[tuple[str, int]]
) -> None:
    def nao_leia(*_: Any, **__: Any) -> bool:
        raise AssertionError("a sombra do AUTH leu o indicador do K1")

    monkeypatch.setenv(at.CHAVE_EFEITO_K1, "true")
    monkeypatch.setattr(rco_pin, "efeito_k1_habilitado", nao_leia)
    monkeypatch.setattr(at, "efeito_k1_habilitado", nao_leia)
    estados = at.EstadosManuais({("OMNI-GERAL-CAR-0002", "2.0.1"): "ativa_l2"})
    monkeypatch.setattr(rco_pin, "estados_em_cache", lambda: estados)
    h, transport, sink, registro, outbox = _harness(_dmn())
    _rodar(h, _task())
    assert transport.failures == [] and len(transport.completed) == 1
    dmn_versions = sink.emitted[0][0].dmn_versions
    assert {dmn_versions[k]["modo"] for k in RCO} == {"sombra"}
    assert {dmn_versions[m]["composicaoK1"]["modo"] for m in rec.ORDEM} == {"sombra"}
    assert {(a.modo, a.tem_ordinal) for a, _ in registro.chamadas} == {("sombra", False)}
    for avaliacao, contexto in registro.chamadas:
        linha = montar_linha(avaliacao, contexto, None)
        assert (linha.modo, linha.ordinal, linha.amostrada) == ("sombra", None, False)
    assert outbox.linhas == {} and avisos_contados == [("suprimido_sombra", 1)]


@pytest.mark.parametrize("modulo", [auth, auth_rco])
def test_auth_e_auth_rco_nao_citam_o_indicador(modulo: Any) -> None:
    arvore = ast.parse(Path(modulo.__file__).read_text(encoding="utf-8"))
    nomes = {
        n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else n.name
        for n in ast.walk(arvore)
        if isinstance(n, ast.Name | ast.Attribute | ast.alias)
    }
    assert "efeito_k1_habilitado" not in nomes
    textos = [n.value for n in ast.walk(arvore) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any("MAEZO_RCO_EFEITO_K1" in t for t in textos)
    assert not any(t == "efeito" for t in textos)  # nem pede o modo de efeito


# --- (d) falhas injetadas ----------------------------------------------------------------------------------


def _levanta(exc: BaseException) -> Callable[..., Any]:
    def f(*_: Any, **__: Any) -> Any:
        raise exc

    return f


class _MotorQueEsgota(FakeDmnTransport):
    async def evaluate(self, decision_key: str, variables: dict[str, Any], **kwargs: Any) -> Any:
        if decision_key.startswith("rco_"):
            raise TimeoutError
        return await super().evaluate(decision_key, variables, **kwargs)


def _falha_pin(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(rco_pin, "rco_rules_pin", _levanta(rco_pin.RcoRulesPinError("pin ilegivel")))


def _falha_manifesto(mp: pytest.MonkeyPatch) -> None:
    rco_sombra._dicionarios.clear()
    mp.setattr(rco_sombra, "carregar_dicionario_entradas", _levanta(ManifestoK1InvalidoError(["x"])))


def _falha_amh(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(rco_sombra, "_versao_k3_pinada", _levanta(AmhContractPinError("amh")))


def _falha_montador(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(rco_montador, "montar_entradas", _levanta(rco_montador.MontadorEntradasError("x")))


def _falha_compor(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(cmp, "compor", _levanta(RuntimeError("bug em compor")))


FALHAS: dict[str, tuple[Callable[[pytest.MonkeyPatch], None] | None, bool, set[str]]] = {
    "pin_rco_ilegivel": (_falha_pin, False, {rco_sombra.PIN_INDISPONIVEL}),
    "manifesto_invalido": (_falha_manifesto, False, {rco_sombra.DICIONARIO_RECUSADO}),
    "pin_amh_ilegivel": (_falha_amh, False, {rco_sombra.PIN_AMH_INDISPONIVEL}),
    "montador_recusa": (_falha_montador, False, {rco_sombra.MONTADOR_RECUSOU}),
    "motor_esgota_o_prazo": (None, True, {rco_sombra.PRAZO_ESGOTADO}),
    "erro_em_compor": (_falha_compor, False, {rco_sombra.ERRO_DE_PROGRAMACAO, rco_sombra.AVALIADA}),
}


def _motor(esgota: bool) -> FakeDmnTransport:
    if not esgota:
        return _dmn()
    dmn = _MotorQueEsgota()
    for chave, (linhas, _versao) in _dmn()._responses.items():
        dmn.register(chave, linhas)
    return dmn


@pytest.mark.parametrize("falha", sorted(FALHAS))
def test_falhas_injetadas_nao_mudam_variaveis_nem_falham_a_tarefa(
    pin_real: rco_pin.RcoRulesPin, monkeypatch: pytest.MonkeyPatch, falha: str
) -> None:
    injetar, esgota, desfechos_esperados = FALHAS[falha]
    baseline_pin = pin_real
    desfechos: list[str] = []
    monkeypatch.setattr(rco_sombra, "record_rco_sombra", desfechos.append)

    _pinar(monkeypatch, SEM_PACOTE)
    neutra = _worker(_dmn(), degradado=False, ratificadas=True).execute(_vars())
    h_sem, t_sem, _, _, _ = _harness(_dmn())
    _rodar(h_sem, _task())
    _pinar(monkeypatch, baseline_pin)
    desfechos.clear()

    if injetar is not None:
        injetar(monkeypatch)
    ligada = _worker(_motor(esgota), degradado=False, ratificadas=True).execute(_vars())
    assert ligada == neutra
    # a falha foi de fato exercitada e virou desfecho fechado
    assert desfechos and set(desfechos) <= desfechos_esperados | {rco_sombra.AVALIADA}
    assert set(desfechos) & desfechos_esperados

    h, t, _, _, outbox = _harness(_motor(esgota))
    _rodar(h, _task())
    assert t.failures == [] and t_sem.failures == []
    assert [v for _, v in t.completed] == [v for _, v in t_sem.completed]
    assert outbox.linhas == {}


# --- (e) lista de permissao e a metrica de cobertura -------------------------------------------------------

#: Campos permitidos nos eventos de log `rco_*` (sombra, composicao, aviso suprimido).
CAMPOS_LOG = frozenset(
    {
        "event",
        "log_level",
        "decision_key",
        "composicao",
        "desfecho",
        "topico",
        "erro",
        "decisoes",
        "codigo",
        "modo",
        "task_id",
        "topic",
        "sinal",
        "unidade",
    }
)
CHAVES_AUDITORIA = frozenset(
    {
        "version",
        "id",
        "deploymentId",
        "shaDmn",
        "idRegra",
        "versaoRegra",
        "ativacaoPacote",
        "exigeAditivo",
        "estadoManual",
        "ativacaoEfetiva",
        "modo",
        "derivacoes",
        "entradasNulas",
        "versaoDicionarioEntradas",
        "origemAvaliacao",
        "sinaisAuditoria",
        "composicaoK1",
    }
)
CHAVES_COMPOSICAO = frozenset({"nome", "modo", "desfecho", "decisaoVencedora", "motivo", "peloPadrao"})


def _assert_permitidos(
    logs: list[dict[str, Any]], dmn_versions: dict[str, Any], registro: _Registro | None
) -> None:
    da_sombra = [e for e in logs if str(e["event"]).startswith("rco_")]
    assert da_sombra
    for evento in da_sombra:
        assert set(evento) <= CAMPOS_LOG, set(evento) - CAMPOS_LOG
        assert not any(p in repr(evento) for p in PROIBIDOS), evento["event"]
    for chave in RCO:
        entrada = dmn_versions[chave]
        assert set(entrada) <= CHAVES_AUDITORIA, set(entrada) - CHAVES_AUDITORIA
        if "composicaoK1" in entrada:
            assert set(entrada["composicaoK1"]) == CHAVES_COMPOSICAO
        assert all(isinstance(v, str) for v in entrada["entradasNulas"].values())
        assert not any(p in json.dumps(entrada) for p in PROIBIDOS)
    if registro is not None:
        for avaliacao, contexto in registro.chamadas:
            linha = montar_linha(avaliacao, contexto, None)
            assert not any(p in repr(linha.params()) for p in PROIBIDOS)
            assert not any(p in repr(avaliacao) for p in PROIBIDOS)


def _metricas_rco() -> Iterator[tuple[str, dict[str, str]]]:
    registro = observability._get_metrics_collector().registry
    for familia in registro.collect():
        if familia.name.startswith("maezo_rco"):
            for amostra in familia.samples:
                yield amostra.name, dict(amostra.labels)


def test_nada_do_caso_em_log_auditoria_registro_metrica_ou_motor(pin_real: rco_pin.RcoRulesPin) -> None:
    dmn = _dmn()
    h, transport, sink, registro, _ = _harness(dmn)
    with structlog.testing.capture_logs() as logs:
        _rodar(h, _task())
    assert transport.failures == []
    _assert_permitidos(logs, sink.emitted[0][0].dmn_versions, registro)
    for nome, rotulos in _metricas_rco():
        assert not any(p in v for v in rotulos.values() for p in PROIBIDOS), nome
    # o motor recebe so as entradas do dicionario: nenhum identificador do caso nem variavel de start crua
    manifesto = rec.manifesto()
    for chave, enviadas in [(k, v) for k, v in dmn.calls if k.startswith("rco_")]:
        if chave in rec.ORDEM:
            assert set(enviadas) == set(manifesto.decisoes[CARENCIA].entradas) | set(
                manifesto.decisoes[CPT].entradas
            )
        assert not any(p in repr(enviadas) for p in PROIBIDOS if p != DATA)
        assert "carater_atendimento" not in enviadas and "numero_guia_tiss" not in enviadas


@pytest.fixture
def cobertura(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    contados: list[dict[str, str]] = []
    monkeypatch.setattr(rco_sombra, "record_rco_entrada_cobertura", lambda **k: contados.append(k))
    return contados


def test_cobertura_das_entradas_com_o_start_de_hoje(
    pin_real: rco_pin.RcoRulesPin, cobertura: list[dict[str, str]]
) -> None:
    """Com o start de hoje so `dataAtendimento` vem preenchida (origem do start, sem procedencia declarada):
    a primeira medida da sombra e a cobertura, nao a concordancia."""
    _worker(_dmn(), degradado=False, ratificadas=True).execute(_vars())
    por_unidade: dict[str, dict[str, tuple[str, str, str]]] = {}
    for c in cobertura:
        unidade = por_unidade.setdefault(c["unidade"], {})
        assert c["entrada"] not in unidade  # uma vez por entrada distinta da avaliacao
        unidade[c["entrada"]] = (c["origem"], c["estado"], c["motivo"])
    manifesto = rec.manifesto()
    esperadas = set(manifesto.decisoes[CARENCIA].entradas) | set(manifesto.decisoes[CPT].entradas)
    assert set(por_unidade) == {GRUPO, ELEGIBILIDADE}
    assert set(por_unidade[GRUPO]) == esperadas
    assert set(por_unidade[ELEGIBILIDADE]) == set(manifesto.decisoes[ELEGIBILIDADE].entradas)
    grupo = por_unidade[GRUPO]
    assert grupo["dataAtendimento"] == ("start_sem_procedencia", "preenchida", "nenhum")
    assert grupo["caraterAtendimento"] == ("tiss", "nula", "ausente_na_fonte")  # regra 6: nunca do start
    assert grupo["cptVigente"] == ("cadastro", "nula", "k3_v1_16_nao_pinado")
    assert grupo["motivoIsencaoCarencia"] == ("derivado", "nula", "formula_publicada_nao_implementada")
    assert grupo["urgenciaAposVinteQuatroHorasCobertura"][:2] == ("derivado", "nula")
    preenchidas = {n for n, (_, estado, _) in grupo.items() if estado == "preenchida"}
    assert preenchidas == {"dataAtendimento"}
    for unidade in por_unidade.values():
        for origem, estado, motivo in unidade.values():
            assert origem in rco_sombra.ORIGENS_COBERTURA
            assert (estado == "preenchida") == (motivo == "nenhum")
            assert motivo == "nenhum" or motivo in {m.value for m in rco_montador.MotivoNulo}


def test_cobertura_sem_data_atendimento(
    pin_real: rco_pin.RcoRulesPin, cobertura: list[dict[str, str]]
) -> None:
    _worker(_dmn(), degradado=False, ratificadas=True).execute(_vars(data_atendimento=_REMOVER))
    grupo = {c["entrada"]: c for c in cobertura if c["unidade"] == GRUPO}
    assert (grupo["dataAtendimento"]["estado"], grupo["dataAtendimento"]["motivo"]) == (
        "nula",
        "ausente_na_fonte",
    )
    assert all(c["estado"] == "nula" for c in cobertura)


def test_metrica_de_cobertura_com_rotulos_fechados(pin_real: rco_pin.RcoRulesPin) -> None:
    _worker(_dmn(), degradado=False, ratificadas=True).execute(_vars())
    amostras = [r for n, r in _metricas_rco() if n == "maezo_rco_entrada_cobertura_total"]
    assert amostras
    for rotulos in amostras:
        assert set(rotulos) == {"unidade", "entrada", "origem", "estado", "motivo"}
        assert rotulos["unidade"] in observability.RCO_COBERTURA_UNIDADES | {"outro"}
        assert rotulos["entrada"] in observability.RCO_COBERTURA_ENTRADAS | {"outro"}
        assert rotulos["origem"] in observability.RCO_COBERTURA_ORIGENS | {"outro"}
        assert rotulos["estado"] in observability.RCO_COBERTURA_ESTADOS | {"outro"}
        assert rotulos["motivo"] in observability.RCO_COBERTURA_MOTIVOS | {"outro"}


def test_valor_desconhecido_vira_outro(monkeypatch: pytest.MonkeyPatch) -> None:
    vistos: list[dict[str, str]] = []

    class _Contador:
        def labels(self, **rotulos: str) -> Any:
            vistos.append(rotulos)
            return self

        def inc(self) -> None:
            return None

    class _Coletor:
        rco_entrada_cobertura_total = _Contador()

    monkeypatch.setattr(observability, "_get_metrics_collector", lambda: _Coletor())
    observability.record_rco_entrada_cobertura(
        unidade=GUIA, entrada="numeroGuia", origem="planilha", estado="talvez", motivo=BENEFICIARIO
    )
    assert vistos == [
        {"unidade": "outro", "entrada": "outro", "origem": "outro", "estado": "outro", "motivo": "outro"}
    ]


# --- paridade dos vocabularios fechados --------------------------------------------------------------------


def test_paridade_dos_rotulos_de_cobertura() -> None:
    entradas: set[str] = set()
    for mapa in rco_montador.MAPEAMENTOS.values():
        entradas |= set(mapa)
    assert entradas == observability.RCO_COBERTURA_ENTRADAS
    assert {m.value for m in rco_montador.MotivoNulo} | {
        rco_sombra.MOTIVO_NENHUM
    } == observability.RCO_COBERTURA_MOTIVOS
    assert observability.RCO_COBERTURA_ORIGENS == rco_sombra.ORIGENS_COBERTURA
    assert {rco_sombra.ESTADO_PREENCHIDA, rco_sombra.ESTADO_NULA} == observability.RCO_COBERTURA_ESTADOS
    assert observability.RCO_COBERTURA_UNIDADES == rco_sombra.UNIDADES_COBERTURA
    # toda unidade ligada a um worker esta no vocabulario
    from maezo.tools.workers import contas

    assert {GRUPO, ELEGIBILIDADE, contas.DECISAO_RCO_PRAZOS_CONTA} == rco_sombra.UNIDADES_COBERTURA
    # as origens K1 do dicionario estao todas no vocabulario
    assert {e.origem for e in rec.dicionario().entradas.values()} <= rco_sombra.ORIGENS_COBERTURA


def test_paridade_do_vocabulario_da_composicao_registrada() -> None:
    from maezo.tools.workers import _audit_ctx

    assert {cmp.DESFECHO_ESCOLHIDO, cmp.DESFECHO_REVISAO_HUMANA} == _audit_ctx.DESFECHOS_COMPOSICAO
    assert _audit_ctx.MOTIVOS_COMPOSICAO == cmp.MOTIVOS
    assert _audit_ctx.MODOS_COMPOSICAO == cmp.MODOS == observability.RCO_COMPOSICAO_MODOS
    assert "suprimido_sombra" in observability.RCO_AVISO_DESFECHOS


# --- os fatos do AUTH: so o que o montador permite ----------------------------------------------------------


def test_fatos_do_auth_seguem_as_regras_do_montador() -> None:
    variaveis = _vars(cadastro_qualquer="x", tipo_guia="consulta")
    fatos = auth_rco.fatos_carencia_cpt_auth(variaveis)((1, 15))
    assert dict(fatos.guia) == {"data_atendimento": DATA, "codigo_procedimento_tuss": "10101012"}
    assert fatos.versao_k3_pinada == (1, 15)
    assert (fatos.carater_atendimento_guia, fatos.tipo_guia, fatos.k3) == (None, None, None)
    assert dict(fatos.tiss) == {} and dict(fatos.cadastro) == {} and dict(fatos.fora_tiss) == {}
    assert fatos.termos_sem_produtor == auth_rco.TERMOS_SEM_PRODUTOR_AUTH
    # os termos sem produtor sao termos REAIS da formula de urgencia, e o parametro do pacote tem produtor
    termos = dict(rco_montador.FORMULAS["urgenciaAposVinteQuatroHorasCobertura"].termos)
    assert set(termos) >= auth_rco.TERMOS_SEM_PRODUTOR_AUTH
    assert {t for t, origem in termos.items() if origem == "parametro"}.isdisjoint(
        auth_rco.TERMOS_SEM_PRODUTOR_AUTH
    )


def test_sombra_do_auth_sem_transporte_e_sem_pacote_sao_inertes(monkeypatch: pytest.MonkeyPatch) -> None:
    desfechos: list[str] = []
    monkeypatch.setattr(rco_sombra, "record_rco_sombra", desfechos.append)
    auth_rco.avaliar_em_sombra_auth(None, _vars(), topico=TOPICO)
    assert desfechos == [rco_sombra.SEM_TRANSPORTE, rco_sombra.SEM_TRANSPORTE]
    desfechos.clear()
    _pinar(monkeypatch, SEM_PACOTE)
    dmn = _dmn()
    auth_rco.avaliar_em_sombra_auth(dmn, _vars(), topico=TOPICO)
    assert desfechos == [rco_sombra.SEM_PACOTE, rco_sombra.SEM_PACOTE] and dmn.calls == []


def test_versao_registrada_da_avaliacao_e_a_do_motor(pin_real: rco_pin.RcoRulesPin) -> None:
    """Controle: `record_dmn_version` sem a auditoria do montador nao marca `origemAvaliacao`."""
    versao = DmnVersion(key=CARENCIA, id="x", version=1, deployment_id="d")
    from maezo.tools.workers._audit_ctx import record_dmn_version

    with collect_dmn_versions() as versoes:
        record_dmn_version(versao, row=ROW_CARENCIA, sombra=True)
    assert versoes[CARENCIA]["modo"] == "sombra" and "origemAvaliacao" not in versoes[CARENCIA]
