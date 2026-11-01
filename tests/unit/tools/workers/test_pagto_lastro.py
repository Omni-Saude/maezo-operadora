"""Pacote PAGTO PR-D — `pagto_lastro.resolve_lastro` (analise PAGTO §2 L1-L4) e o handler dark.

Unitario sobre o duble `FakeCibSevenTransport` (a costura de PR-C ja tem seus testes de fio HTTP e
a prova contra o motor real; aqui o que se prova e' a CLASSIFICACAO dos fatos e a falha fechada).
A prova contra o motor real deste worker esta em
`tests/integration/test_pagto_lastro_history_resolution.py`.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.xml_safe import parse_xml_without_dtd
from maezo.tools.mcp_cibseven.transport import (
    FakeCibSevenTransport,
    HistoricInstanceAmbiguousError,
    HistoricInstanceRecord,
    HistoryPayloadMalformedError,
    HistoryReadUnavailableError,
)
from maezo.tools.workers import pagto, pagto_lastro
from maezo.tools.workers.harness import WorkerBpmnError
from maezo.tools.workers.pagto_lastro import (
    FONTE_CONTAS,
    FONTE_POR_EVIDENCIA,
    FONTE_RECURSO,
    LASTRO_ORIGEM_TIPOS,
    LASTRO_SAIDAS,
    LASTRO_STATUS,
    VALIDATE_COM_LASTRO_VARIABLES,
    resolve_lastro,
    validar_com_lastro,
)

_REPO = Path(__file__).resolve().parents[4]
_BPMN_CONTAS = _REPO / "spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn"
_BPMN_RECURSO = _REPO / "spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn"
_BPMN_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_CAMUNDA_NS = "{http://camunda.org/schema/1.0/bpmn}"

_LOTE = "LOTE-SINT-0001"
_PRESTADOR = "prov:teste-0001"
_GUIA = "GUIA-SINT-0001"
_GLOSA = "GLOSA-SINT-0001"


class _SpyFake(FakeCibSevenTransport):
    """O duble da costura, contando as chamadas e o seletor usado."""

    def __init__(self) -> None:
        super().__init__()
        self.chamadas: list[dict[str, Any]] = []

    async def resolve_historic_instance(
        self,
        process_key: str,
        *,
        business_key: str = "",
        variable_equals: Any = None,
        names: tuple[str, ...] = (),
    ) -> HistoricInstanceRecord:
        self.chamadas.append(
            {
                "process_key": process_key,
                "business_key": business_key,
                "variable_equals": dict(variable_equals or {}),
                "names": names,
            }
        )
        return await super().resolve_historic_instance(
            process_key, business_key=business_key, variable_equals=variable_equals, names=names
        )


class _Quebrado(FakeCibSevenTransport):
    def __init__(self, erro: BaseException) -> None:
        super().__init__()
        self._erro = erro

    async def resolve_historic_instance(self, process_key: str, **_kw: Any) -> HistoricInstanceRecord:
        raise self._erro


def _ordem_contas(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "ordem_pagamento_id": "ORDEM-CONTA-amh-LOTE-SINT-0001-prov:teste-0001",
        "numero_lote_tiss": _LOTE,
        "prestador_id": _PRESTADOR,
        "instrumento_pagamento": "ted",
        "valor_pagamento_cents": 150000,
        "lastro_origem": "contas_adjudicacao_humana",
    }
    base.update(extra)
    return base


def _ordem_recurso(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "ordem_pagamento_id": "ORDEM-GLOSAREV-amh-GUIA-SINT-0001-GLOSA-SINT-0001",
        "numero_guia_tiss": _GUIA,
        "glosa_id": _GLOSA,
        "prestador_id": _PRESTADOR,
        "instrumento_pagamento": "pix",
        "valor_pagamento_cents": 20000,
        "lastro_origem": "recurso_deferimento_humano",
    }
    base.update(extra)
    return base


def _contas(
    engine: FakeCibSevenTransport,
    *,
    terminal: str | None = "End_ContaAprovadaHumano",
    state: str = "COMPLETED",
    instance_id: str = "pi-contas-1",
    **variaveis: Any,
) -> None:
    variables: dict[str, Any] = {
        "numero_lote_tiss": _LOTE,
        "prestador_id": _PRESTADOR,
        "has_glosas": True,
        "valor_liberado_brl": "1500.00",
        "valor_apresentado_brl": 1800.0,
    }
    variables.update(variaveis)
    engine.seed_historic_instance_record(
        HistoricInstanceRecord(
            instance_id=instance_id,
            business_key=f"CONTAS-amh-{_LOTE}",
            process_key="SP-OP-CONTAS-001",
            state=state,
            end_activity_id=terminal,
            variables=variables,
        )
    )


def _recurso(
    engine: FakeCibSevenTransport,
    *,
    terminal: str | None = "End_RecursoDeferido",
    state: str = "COMPLETED",
    **variaveis: Any,
) -> None:
    variables: dict[str, Any] = {"prestador_id": _PRESTADOR, "valor_deferido_brl": "200.00"}
    variables.update(variaveis)
    engine.seed_historic_instance_record(
        HistoricInstanceRecord(
            instance_id="pi-recurso-1",
            business_key=f"RECURSO-amh-{_GUIA}-{_GLOSA}",
            process_key="SP-OP-RECURSO-001",
            state=state,
            end_activity_id=terminal,
            variables=variables,
        )
    )


def _assert_forma(out: dict[str, Any]) -> None:
    assert set(out) == LASTRO_SAIDAS
    assert out["lastro_status"] in LASTRO_STATUS
    assert out["lastro_origem_tipo"] in LASTRO_ORIGEM_TIPOS
    assert "lastro_confirmado" not in out


# ---------------------------------------------------------------------------------------------
# CONTAS
# ---------------------------------------------------------------------------------------------


def test_contas_humana_confirmada_com_o_valor_da_origem_como_evidencia() -> None:
    engine = _SpyFake()
    _contas(engine)
    out = resolve_lastro(_ordem_contas(), engine=engine)
    _assert_forma(out)
    assert out == {
        "lastro_status": "CONFIRMADO",
        "lastro_origem_tipo": "contas_humana",
        "lastro_origem_instancia_id": "pi-contas-1",
        "lastro_origem_terminal": "End_ContaAprovadaHumano",
        "lastro_valor_origem_cents": 150000,
    }
    # O seletor e' a identidade da PROPRIA ordem (lote + credor), nunca uma chave semeada.
    assert engine.chamadas == [
        {
            "process_key": "SP-OP-CONTAS-001",
            "business_key": "",
            "variable_equals": {"numero_lote_tiss": _LOTE, "prestador_id": _PRESTADOR},
            "names": ("has_glosas", "valor_apresentado_brl", "valor_liberado_brl"),
        }
    ]


def test_tolerancia_do_dono_abaixo_da_origem_segue() -> None:
    engine = _SpyFake()
    _contas(engine)
    out = resolve_lastro(_ordem_contas(valor_pagamento_cents=149999), engine=engine)
    assert out["lastro_status"] == "CONFIRMADO"
    assert out["lastro_valor_origem_cents"] == 150000


def test_tolerancia_do_dono_acima_da_origem_diverge() -> None:
    engine = _SpyFake()
    _contas(engine)
    out = resolve_lastro(_ordem_contas(valor_pagamento_cents=150001), engine=engine)
    assert out["lastro_status"] == "DIVERGENTE_VALOR"
    assert out["lastro_origem_tipo"] == "contas_humana"
    assert out["lastro_valor_origem_cents"] == 150000


def test_pagamento_parcial_humano_le_o_valor_liberado() -> None:
    engine = _SpyFake()
    _contas(engine, terminal="End_PagamentoParcialHumano", valor_liberado_brl=900.5)
    out = resolve_lastro(_ordem_contas(valor_pagamento_cents=90050), engine=engine)
    assert (out["lastro_status"], out["lastro_origem_tipo"]) == ("CONFIRMADO", "contas_humana")
    assert out["lastro_valor_origem_cents"] == 90050


@pytest.mark.parametrize(
    ("has_glosas", "tipo"),
    [(False, "contas_auto_sem_glosa"), (True, "contas_auto_triagem"), (None, "outro"), ("false", "outro")],
    ids=["sem-glosa", "triagem-pagar", "ausente", "string-nao-e-booleano"],
)
def test_perna_automatica_distinguida_por_has_glosas_historico(has_glosas: Any, tipo: str) -> None:
    """As duas pernas automaticas terminam em `End_ContaAprovadaIntegral`; o fato que as separa e'
    o `has_glosas` que o motor gravou. Sem ele (ou fora de tipo) o worker nao rotula — `outro`, que
    a DMN de PR-F nao trata como origem elegivel."""
    engine = _SpyFake()
    _contas(
        engine,
        terminal="End_ContaAprovadaIntegral",
        has_glosas=has_glosas,
        valor_apresentado_brl="1500.00",
    )
    out = resolve_lastro(_ordem_contas(lastro_origem="contas_adjudicacao_automatica"), engine=engine)
    assert out["lastro_origem_tipo"] == tipo
    # Perna automatica: o valor da origem e' o APRESENTADO (`fonte_valor=apresentado`).
    assert out["lastro_valor_origem_cents"] == 150000
    assert out["lastro_status"] == "CONFIRMADO"


@pytest.mark.parametrize(
    "terminal",
    ["End_GlosaAplicadaHumano", "End_ContaDevolvidaPrestador", "End_EncaminhadaFraude"],
)
def test_terminal_sem_obrigacao_nao_e_lastro(terminal: str) -> None:
    engine = _SpyFake()
    _contas(engine, terminal=terminal)
    out = resolve_lastro(_ordem_contas(), engine=engine)
    assert out["lastro_status"] == "NAO_ENCONTRADO"
    assert out["lastro_origem_tipo"] == "outro"
    assert out["lastro_origem_terminal"] == terminal  # evidencia para o humano
    assert out["lastro_valor_origem_cents"] is None


@pytest.mark.parametrize("state", ["ACTIVE", "SUSPENDED"])
def test_origem_em_curso_nao_concluida(state: str) -> None:
    engine = _SpyFake()
    _contas(engine, terminal=None, state=state)
    out = resolve_lastro(_ordem_contas(), engine=engine)
    assert out["lastro_status"] == "NAO_CONCLUIDO"
    assert out["lastro_origem_instancia_id"] == "pi-contas-1"


@pytest.mark.parametrize("state", ["EXTERNALLY_TERMINATED", "INTERNALLY_TERMINATED"])
def test_origem_cancelada_nao_e_lastro(state: str) -> None:
    engine = _SpyFake()
    _contas(engine, terminal=None, state=state)
    out = resolve_lastro(_ordem_contas(), engine=engine)
    assert out["lastro_status"] == "NAO_ENCONTRADO"


@pytest.mark.parametrize(
    ("state", "terminal"),
    [("COMPLETED", None), ("ESTADO_NOVO", "End_ContaAprovadaHumano"), ("", "End_ContaAprovadaHumano")],
    ids=["completed-sem-terminal", "estado-desconhecido", "estado-vazio"],
)
def test_estado_nao_provado_e_indisponivel_nunca_confirmado(state: str, terminal: str | None) -> None:
    engine = _SpyFake()
    _contas(engine, terminal=terminal, state=state)
    out = resolve_lastro(_ordem_contas(), engine=engine)
    assert out["lastro_status"] == "INDISPONIVEL"


def test_origem_inexistente_nao_encontrada() -> None:
    engine = _SpyFake()
    _contas(engine, prestador_id="prov:outro-credor")
    out = resolve_lastro(_ordem_contas(), engine=engine)
    assert out["lastro_status"] == "NAO_ENCONTRADO"
    assert len(engine.chamadas) == 1


def test_duas_origens_para_a_mesma_ordem_e_indisponivel() -> None:
    engine = _SpyFake()
    _contas(engine, instance_id="pi-a")
    _contas(engine, instance_id="pi-b")
    out = resolve_lastro(_ordem_contas(), engine=engine)
    assert out["lastro_status"] == "INDISPONIVEL"
    assert out["lastro_origem_instancia_id"] == ""


@pytest.mark.parametrize(
    "valor",
    [None, "", "abc", 0, -10.0, True],
    ids=["ausente", "vazio", "nao-numerico", "zero", "negativo", "booleano"],
)
def test_valor_da_origem_sem_confianca_diverge(valor: Any) -> None:
    engine = _SpyFake()
    _contas(engine, valor_liberado_brl=valor)
    out = resolve_lastro(_ordem_contas(), engine=engine)
    assert out["lastro_status"] == "DIVERGENTE_VALOR"
    assert out["lastro_valor_origem_cents"] is None


@pytest.mark.parametrize(
    "valor", [None, 1500.0, "150000", 0, True], ids=["ausente", "float", "str", "zero", "bool"]
)
def test_valor_da_ordem_sem_confianca_diverge(valor: Any) -> None:
    engine = _SpyFake()
    _contas(engine)
    out = resolve_lastro(_ordem_contas(valor_pagamento_cents=valor), engine=engine)
    assert out["lastro_status"] == "DIVERGENTE_VALOR"


@pytest.mark.parametrize("campo", ["numero_lote_tiss", "prestador_id"])
def test_ordem_sem_identidade_nem_consulta(campo: str) -> None:
    engine = _SpyFake()
    _contas(engine)
    out = resolve_lastro(_ordem_contas(**{campo: "  "}), engine=engine)
    assert out["lastro_status"] == "NAO_ENCONTRADO"
    assert engine.chamadas == []


# ---------------------------------------------------------------------------------------------
# RECURSO
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("terminal", ["End_RecursoDeferido", "End_RecursoDeferidoParcial"])
def test_recurso_deferido_confirmado_pela_chave_contratual(terminal: str) -> None:
    engine = _SpyFake()
    _recurso(engine, terminal=terminal)
    out = resolve_lastro(_ordem_recurso(), engine=engine)
    assert out == {
        "lastro_status": "CONFIRMADO",
        "lastro_origem_tipo": "recurso_humano",
        "lastro_origem_instancia_id": "pi-recurso-1",
        "lastro_origem_terminal": terminal,
        "lastro_valor_origem_cents": 20000,
    }
    assert engine.chamadas == [
        {
            "process_key": "SP-OP-RECURSO-001",
            "business_key": f"RECURSO-amh-{_GUIA}-{_GLOSA}",
            "variable_equals": {},
            "names": ("prestador_id", "valor_deferido_brl"),
        }
    ]


def test_recurso_acima_do_deferido_diverge() -> None:
    engine = _SpyFake()
    _recurso(engine)
    out = resolve_lastro(_ordem_recurso(valor_pagamento_cents=20001), engine=engine)
    assert out["lastro_status"] == "DIVERGENTE_VALOR"


@pytest.mark.parametrize("prestador", ["prov:outro-credor", None, ""])
def test_recurso_de_outro_credor_nao_e_lastro(prestador: Any) -> None:
    """L3: a chave casa (guia+glosa), mas a origem registrou outro credor — ou nenhum."""
    engine = _SpyFake()
    _recurso(engine, prestador_id=prestador)
    out = resolve_lastro(_ordem_recurso(), engine=engine)
    assert out["lastro_status"] == "NAO_ENCONTRADO"


@pytest.mark.parametrize(
    "terminal", ["End_RecursoIndeferido", "End_RecursoIndeferidoAuditor", "End_RecursoInadmissivel"]
)
def test_recurso_indeferido_nao_e_lastro(terminal: str) -> None:
    engine = _SpyFake()
    _recurso(engine, terminal=terminal)
    out = resolve_lastro(_ordem_recurso(), engine=engine)
    assert out["lastro_status"] == "NAO_ENCONTRADO"


@pytest.mark.parametrize("campo", ["numero_guia_tiss", "glosa_id", "tenant_id"])
def test_recurso_sem_identidade_nem_consulta(campo: str) -> None:
    engine = _SpyFake()
    _recurso(engine)
    out = resolve_lastro(_ordem_recurso(**{campo: ""}), engine=engine)
    assert out["lastro_status"] == "NAO_ENCONTRADO"
    assert engine.chamadas == []


# ---------------------------------------------------------------------------------------------
# Falha fechada
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("lastro_origem", [None, "", "manual", "contas_adjudicacao_qualquer"])
def test_sem_evidencia_conferivel_nao_consulta_e_nao_confirma(lastro_origem: Any) -> None:
    engine = _SpyFake()
    _contas(engine)
    out = resolve_lastro(_ordem_contas(lastro_origem=lastro_origem), engine=engine)
    _assert_forma(out)
    assert out["lastro_status"] == "NAO_ENCONTRADO"
    assert engine.chamadas == []


@pytest.mark.parametrize("engine", [None, object(), "http://engine"], ids=["none", "sem-costura", "str"])
def test_costura_ausente_e_indisponivel(engine: Any) -> None:
    out = resolve_lastro(_ordem_contas(), engine=engine)
    _assert_forma(out)
    assert out["lastro_status"] == "INDISPONIVEL"


@pytest.mark.parametrize(
    "erro",
    [
        HistoryReadUnavailableError("motor fora"),
        HistoricInstanceAmbiguousError("duas"),
        HistoryPayloadMalformedError("forma"),
        PermissionError("negado pelo gate"),
        RuntimeError("qualquer coisa"),
        ConnectionError("rede crua"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_erro_da_costura_e_indisponivel_nunca_excecao(erro: BaseException) -> None:
    out = resolve_lastro(_ordem_contas(), engine=_Quebrado(erro))
    _assert_forma(out)
    assert out["lastro_status"] == "INDISPONIVEL"


@pytest.mark.parametrize(
    "erro",
    [TypeError("bug"), KeyError("bug"), AttributeError("bug"), NotImplementedError("bug"), ValueError("bug")],
    ids=lambda e: type(e).__name__,
)
def test_erro_de_programacao_nao_vira_indisponivel_silencioso(erro: BaseException) -> None:
    """Licao NEW-12: um bug nao pode se passar por "motor indisponivel" para sempre — re-levanta
    (o harness o transforma em incidente visivel)."""
    with pytest.raises(type(erro)):
        resolve_lastro(_ordem_contas(), engine=_Quebrado(erro))


# ---------------------------------------------------------------------------------------------
# Handler composto (o que o topico serve com a flag ligada)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("estrito", [False, True])
def test_handler_composto_acrescenta_fatos_e_nao_toca_lastro_confirmado(estrito: bool) -> None:
    """Mesmo com a origem CONFIRMADA, `lastro_confirmado` e' o que `validate_pagto` ja dava: a
    rota da DMN viva (v0.1) NAO muda com a flag — so a sombra/PR-F leem os fatos novos."""
    engine = _SpyFake()
    _contas(engine)
    variaveis = _ordem_contas(lastro_confirmado=False)
    validar = pagto.functools.partial(pagto.validate_pagto, fatos_estritos=estrito)
    so_validar = validar(dict(variaveis))
    out = validar_com_lastro(dict(variaveis), validar=validar, engine=engine)
    assert {k: out[k] for k in so_validar} == so_validar
    assert out["lastro_confirmado"] is False
    assert out["lastro_status"] == "CONFIRMADO"
    assert set(out) == set(so_validar) | LASTRO_SAIDAS


def test_handler_composto_sobrescreve_fato_semeado() -> None:
    """I-PAGTO-1: um `lastro_status=CONFIRMADO` semeado no start nao sobrevive — o worker escreve o
    fato que ELE resolveu (aqui: sem origem conferivel)."""
    engine = _SpyFake()
    variaveis = _ordem_contas(lastro_status="CONFIRMADO", lastro_origem_tipo="contas_humana")
    out = validar_com_lastro(dict(variaveis), validar=pagto.validate_pagto, engine=engine)
    assert out["lastro_status"] == "NAO_ENCONTRADO"
    assert out["lastro_origem_tipo"] == "outro"


def test_handler_composto_ordem_invalida_levanta_antes_de_ler_o_motor() -> None:
    engine = _SpyFake()
    _contas(engine)
    with pytest.raises(WorkerBpmnError):
        validar_com_lastro(_ordem_contas(ordem_pagamento_id=""), validar=pagto.validate_pagto, engine=engine)
    assert engine.chamadas == []


# ---------------------------------------------------------------------------------------------
# Cercas: terminais = BPMN; escopo de leitura = o que as funcoes leem
# ---------------------------------------------------------------------------------------------


def _terminais_apos_handoff(bpmn: Path, topic: str) -> set[str]:
    """Eventos finais alcancaveis a partir de toda tarefa de handoff a PAGTO do processo."""
    root = parse_xml_without_dtd(bpmn.read_bytes())
    processo = root.find(f"{_BPMN_NS}process")
    assert processo is not None
    saidas: dict[str, list[str]] = {}
    for fluxo in processo.iter(f"{_BPMN_NS}sequenceFlow"):
        saidas.setdefault(fluxo.get("sourceRef", ""), []).append(fluxo.get("targetRef", ""))
    finais = {e.get("id", "") for e in processo.iter(f"{_BPMN_NS}endEvent")}
    inicios = [
        t.get("id", "")
        for t in processo.iter(f"{_BPMN_NS}serviceTask")
        if t.get(f"{_CAMUNDA_NS}topic") == topic
    ]
    assert inicios, f"nenhuma tarefa {topic} em {bpmn.name}"
    vistos: set[str] = set()
    pilha = list(inicios)
    alcancados: set[str] = set()
    while pilha:
        no = pilha.pop()
        if no in vistos:
            continue
        vistos.add(no)
        if no in finais:
            alcancados.add(no)
        pilha.extend(saidas.get(no, []))
    return alcancados


def test_terminais_de_contas_sao_exatamente_os_alcancados_pelos_handoffs() -> None:
    assert set(FONTE_CONTAS.terminais) == _terminais_apos_handoff(
        _BPMN_CONTAS, "operadora.contas.handoff_pagamento"
    )
    assert set(FONTE_CONTAS.variavel_valor) == set(FONTE_CONTAS.terminais)


def test_terminais_de_recurso_sao_exatamente_os_alcancados_pelos_handoffs() -> None:
    assert set(FONTE_RECURSO.terminais) == _terminais_apos_handoff(
        _BPMN_RECURSO, "operadora.recurso.handoff_pagamento"
    )
    assert set(FONTE_RECURSO.variavel_valor) == set(FONTE_RECURSO.terminais)


def test_evidencia_cobre_exatamente_os_rotulos_que_os_handoffs_semeiam() -> None:
    from maezo.tools.workers import contas, recurso

    assert set(FONTE_POR_EVIDENCIA) == contas.LASTROS_ORIGEM_PERMITIDOS | {recurso.LASTRO_ORIGEM_RECURSO}


def test_fatos_do_lastro_seguem_proibidos_na_semente() -> None:
    assert {"lastro_status", "lastro_origem_tipo"} <= pagto.PAGTO_FATOS_PROIBIDOS_NA_SEMENTE


def _nomes_lidos(fn: Any) -> set[str]:
    arvore = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if (
            isinstance(no, ast.Call)
            and isinstance(no.func, ast.Attribute)
            and no.func.attr == "get"
            and isinstance(no.func.value, ast.Name)
            and no.func.value.id == "variables"
            and no.args
            and isinstance(no.args[0], ast.Constant)
            and isinstance(no.args[0].value, str)
        ):
            nomes.add(no.args[0].value)
    return nomes


def test_escopo_de_leitura_explicito_cobre_tudo_que_o_handler_le() -> None:
    """Menor privilegio sem cegueira: se `validate_pagto` (ou o resolvedor) passar a ler uma variavel
    nova, o escopo explicito do topico com a flag ligada tem de crescer junto — senao o caminho
    ligado leria `None` onde o desligado le o valor."""
    lidos = (
        _nomes_lidos(pagto.validate_pagto)
        | _nomes_lidos(pagto_lastro.resolve_lastro)
        | _nomes_lidos(pagto_lastro._seletor)
        | _nomes_lidos(pagto_lastro._classificar)
    )
    assert len(lidos) >= 10, lidos  # o parser nao pode passar vacuamente
    assert lidos <= set(VALIDATE_COM_LASTRO_VARIABLES), sorted(lidos - set(VALIDATE_COM_LASTRO_VARIABLES))
    assert len(VALIDATE_COM_LASTRO_VARIABLES) == len(set(VALIDATE_COM_LASTRO_VARIABLES))
