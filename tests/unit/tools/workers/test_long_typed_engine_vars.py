"""R-173 — `total_glosado_candidato_centavos` is a Camunda `Long` UNCONDITIONALLY.

Owner decision R-173 of 2026-09-04 (SP-OP-CONTAS-001, WP-CONTRATOS-SYNC): *"Remover a pergunta do
pacote de finanças e declarar `total_glosado_candidato_centavos` como `Long` incondicionalmente no
contrato e na variável de engine, fixando a tipagem por teste que falha se o literal voltar a 32
bits."*

The defect these tests pin: every CIB Seven variable mapper typed a Python ``int`` by MAGNITUDE,
so the wire type of a financial fact depended on how big the lote happened to be — ``Integer`` for
a small conta, ``Long`` only above R$ 21.470.000,00 (``2**31 - 1`` cents). `identify_glosa` sums in
Python's arbitrary-precision integers, so the serialization boundary was the only 32-bit surface
left. **This is a typing decision, not a financial one**: no value, ceiling or glosa rule is
asserted here.

No engine: the wire format is captured through `httpx.MockTransport`, the same seam
`test_harness.py` uses for `CibSevenWorkerTransport`.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.tools.mcp_cibseven.transport import _to_camunda_vars as _process_vars
from maezo.tools.workers.contas import GlosaIdentified, calculate_impact_entry, identify_glosa_entry
from maezo.tools.workers.dmn_transport import _to_camunda_vars as _dmn_vars
from maezo.tools.workers.engine_var_types import (
    LONG_TYPED_ENGINE_VARS,
    camunda_int_type,
)
from maezo.tools.workers.harness import CibSevenWorkerTransport, _to_camunda_var

_VARIAVEL = "total_glosado_candidato_centavos"

# R$ 200,00 — a lote so small it sat comfortably inside int32 and was therefore typed `Integer`
# before this fix. It is the value the whole decision turns on: the SAME variable must not change
# wire type with the size of the batch.
_LOTE_PEQUENO_CENTAVOS = 20_000

_INT32_MAX = 2**31 - 1  # R$ 21.474.836,47 — the landmine named in the contract


# ---------------------------------------------------------------------------
# 1. The declaration itself
# ---------------------------------------------------------------------------


def test_a_variavel_do_contrato_esta_declarada_long() -> None:
    """The declaration names the SP-OP-CONTAS-001 variable (R-173)."""
    assert _VARIAVEL in LONG_TYPED_ENGINE_VARS


def test_toda_variavel_declarada_long_e_um_fato_computado_real_do_worker() -> None:
    """The declaration may not name a variable that no worker produces — every entry must be a
    real field of the computed-facts dataclass, derived from the dataclass itself."""
    campos = {f.name for f in dataclasses.fields(GlosaIdentified)}
    assert campos >= LONG_TYPED_ENGINE_VARS, LONG_TYPED_ENGINE_VARS - campos


# ---------------------------------------------------------------------------
# 2. The 32-bit regression — one test per production mapper
# ---------------------------------------------------------------------------


def test_harness_tipa_long_mesmo_num_lote_pequeno() -> None:
    """FAILS if the literal ever returns to a 32-bit type on the worker-completion mapper."""
    assert _to_camunda_var(_LOTE_PEQUENO_CENTAVOS, name=_VARIAVEL) == {
        "value": _LOTE_PEQUENO_CENTAVOS,
        "type": "Long",
    }


def test_mapper_de_variaveis_de_processo_tipa_long_mesmo_num_lote_pequeno() -> None:
    assert _process_vars({_VARIAVEL: _LOTE_PEQUENO_CENTAVOS}) == {
        _VARIAVEL: {"value": _LOTE_PEQUENO_CENTAVOS, "type": "Long"}
    }


def test_mapper_de_entradas_dmn_tipa_long_mesmo_num_lote_pequeno() -> None:
    assert _dmn_vars({_VARIAVEL: _LOTE_PEQUENO_CENTAVOS}) == {
        _VARIAVEL: {"value": _LOTE_PEQUENO_CENTAVOS, "type": "Long"}
    }


@pytest.mark.parametrize("centavos", [0, 1, 100, _LOTE_PEQUENO_CENTAVOS, _INT32_MAX - 1, _INT32_MAX])
def test_a_tipagem_nao_depende_da_magnitude_em_nenhum_ponto_da_faixa_int32(centavos: int) -> None:
    """Every value that USED to be typed `Integer` (the whole int32 range) is now `Long`. The
    magnitude no longer participates in the decision for this variable."""
    assert camunda_int_type(_VARIAVEL, centavos) == "Long"


# ---------------------------------------------------------------------------
# 3. The declaration changes NOTHING for any other variable
# ---------------------------------------------------------------------------


def test_as_demais_variaveis_mantem_a_regra_de_magnitude() -> None:
    """`glosa_count` and friends keep `Integer`/`Long` by int32 bounds (ADR-0018 part 2)."""
    assert camunda_int_type("glosa_count", 42) == "Integer"
    assert camunda_int_type("glosa_count", _INT32_MAX) == "Integer"
    assert camunda_int_type("glosa_count", _INT32_MAX + 1) == "Long"
    assert camunda_int_type("glosa_count", -(2**31)) == "Integer"
    assert camunda_int_type("glosa_count", -(2**31) - 1) == "Long"


def test_sem_nome_a_regra_e_a_de_magnitude() -> None:
    """A bare value conversion (no variable name) can only be typed by magnitude."""
    assert camunda_int_type(None, _LOTE_PEQUENO_CENTAVOS) == "Integer"
    assert _to_camunda_var(_LOTE_PEQUENO_CENTAVOS) == {"value": _LOTE_PEQUENO_CENTAVOS, "type": "Integer"}


# ---------------------------------------------------------------------------
# 4. End to end: the value the worker really computes, on the real complete payload
# ---------------------------------------------------------------------------


def _lote_com_uma_glosa() -> dict[str, Any]:
    """A conta whose glosa total is R$ 200,00 — inside int32, the pre-fix `Integer` case."""
    return {
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-1",
        "prestador_id": "PREST-1",
        "valor_apresentado_brl": 400.0,
        "data_recebimento_lote": "2026-09-01",
        "data_vencimento": "2026-10-01",
        "linhas_conta_refs": [
            {"linha_id": "L1", "valor_apresentado_centavos": 40_000, "valor_glosado_centavos": 20_000}
        ],
        "reason_codes_tiss": [],
    }


def test_o_worker_calcula_o_valor_pequeno_que_o_defeito_tipava_integer() -> None:
    """Guard on the fixture: the lote really produces a value inside int32 (otherwise the
    end-to-end test below would be green for the wrong reason)."""
    out = identify_glosa_entry(_lote_com_uma_glosa())
    assert out[_VARIAVEL] == _LOTE_PEQUENO_CENTAVOS
    assert out[_VARIAVEL] <= _INT32_MAX


async def test_o_complete_do_worker_escreve_long_no_engine() -> None:
    """The REAL `complete` payload — this is what fails if the harness stops passing the variable
    NAME to the mapper (the name is what carries the declaration)."""
    capturado: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["json"] = request.content
        return httpx.Response(204)

    transport = CibSevenWorkerTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )
    out = identify_glosa_entry(_lote_com_uma_glosa())
    await transport.complete("task-1", "w", out)
    await transport.close()

    import json as _json

    enviado = _json.loads(capturado["json"])["variables"]
    assert enviado[_VARIAVEL] == {"value": _LOTE_PEQUENO_CENTAVOS, "type": "Long"}
    # ...and the neighbouring computed facts keep their own wire types untouched by this change.
    assert enviado["glosa_count"]["type"] == "Integer"


async def test_o_bpmn_error_do_worker_tambem_escreve_long() -> None:
    """`handle_bpmn_error` carries variables too — same mapper, same declaration."""
    capturado: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["json"] = request.content
        return httpx.Response(204)

    transport = CibSevenWorkerTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )
    await transport.handle_bpmn_error(
        "task-1", "w", error_code="ERR_X", variables={_VARIAVEL: _LOTE_PEQUENO_CENTAVOS}
    )
    await transport.close()

    import json as _json

    enviado = _json.loads(capturado["json"])["variables"]
    assert enviado[_VARIAVEL] == {"value": _LOTE_PEQUENO_CENTAVOS, "type": "Long"}


def test_calculate_impact_tambem_reemite_a_variavel_e_ela_e_long() -> None:
    """`calculate_impact` re-emits the same computed fact; it must not re-enter as `Integer`."""
    identificado = GlosaIdentified(
        has_glosas=True,
        denial_ratio=0.5,
        divergencia_valor=True,
        glosa_count=1,
        total_glosado_candidato_centavos=_LOTE_PEQUENO_CENTAVOS,
        linhas_glosadas_candidatas=[],
    )
    out = calculate_impact_entry({**dataclasses.asdict(identificado), "valor_apresentado_brl": 400.0})
    assert out[_VARIAVEL] == _LOTE_PEQUENO_CENTAVOS
    assert _to_camunda_var(out[_VARIAVEL], name=_VARIAVEL)["type"] == "Long"


# ---------------------------------------------------------------------------
# 5. Tree-derived fence, scoped to `src/`: no mapper in `src/` may reintroduce the
#    magnitude-only rule. A fourth mapper already exists OUTSIDE that scope and does exactly
#    that — `tests/integration/processes/engine_rest.py::EngineRest._to_camunda_vars`, test infra
#    with zero `maezo` imports by design; it is deliberately not fenced here.
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


_DECLARACAO = "maezo/tools/workers/engine_var_types.py"


def _mappers_de_variavel_camunda() -> dict[str, ast.Module]:
    """Every `src/` module that DEFINES a Camunda variable mapper, derived from the tree by AST
    (a function named `_to_camunda_var`/`_to_camunda_vars`) — never from a hand-kept list."""
    raiz = _repo_root()
    achados: dict[str, ast.Module] = {}
    for caminho in sorted((raiz / "src").rglob("*.py")):
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        nomes = {
            node.name for node in ast.walk(arvore) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        if nomes & {"_to_camunda_var", "_to_camunda_vars"}:
            achados[str(caminho.relative_to(raiz / "src"))] = arvore
    return achados


def test_a_varredura_da_arvore_realmente_acha_os_mappers() -> None:
    """Guard: the fence below is not scanning an empty set. Three production mappers exist today —
    the worker-completion mapper, the DMN-input mapper and the process-variable mapper."""
    achados = _mappers_de_variavel_camunda()
    assert set(achados) == {
        "maezo/tools/workers/harness.py",
        "maezo/tools/workers/dmn_transport.py",
        "maezo/tools/mcp_cibseven/transport.py",
    }, sorted(achados)


def test_todo_mapper_de_variavel_camunda_consulta_a_declaracao() -> None:
    """A new mapper that types ints by magnitude alone would silently reopen R-173 on its own call
    path. Every Camunda variable mapper in `src/` must route its int branch through
    `camunda_int_type`."""
    infratores = [
        rel
        for rel, arvore in _mappers_de_variavel_camunda().items()
        if not any(isinstance(node, ast.Name) and node.id == "camunda_int_type" for node in ast.walk(arvore))
    ]
    assert not infratores, (
        f"mappers de variavel Camunda que nao consultam a declaracao R-173: {infratores}. "
        "Use engine_var_types.camunda_int_type."
    )


def test_o_literal_da_faixa_int32_vive_num_unico_lugar() -> None:
    """The int32 bounds ARE the magnitude rule R-173 removes for this variable. One copy in
    `src/` — drifting copies are how a mapper silently keeps typing money by lote size. The scope
    is `src/` and only `src/`: `tests/integration/processes/engine_rest.py` holds a fourth copy of
    the same constants, EXCLUDED on purpose because that mapper imports nothing from `maezo`, so
    it cannot consume the declaration without losing that property. That zero-import property is
    true today (measured) but only DECLARED, in the module docstring of
    `tests/unit/integration_support/test_engine_rest_ausencia_e_proveniencia.py`; no test fences
    it, which is why the exclusion is documented here rather than left to be inferred."""
    raiz = _repo_root()
    portadores = sorted(
        str(caminho.relative_to(raiz / "src"))
        for caminho in (raiz / "src").rglob("*.py")
        if "_JAVA_INT32_MAX" in caminho.read_text(encoding="utf-8")
    )
    assert portadores == [_DECLARACAO], portadores


def test_a_declaracao_nao_importa_nada_do_projeto() -> None:
    """The declaration is a LEAF (zero project imports) — that is what lets the three mappers
    share it without any of them depending on the others."""
    caminho = _repo_root() / "src" / _DECLARACAO
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    importados = {
        (node.module or "").split(".")[0] for node in ast.walk(arvore) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(arvore)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert importados <= {"__future__"}, importados


# R173 applies before pre-shaped passthrough too. These are representation checks,
# not financial limits: signed zero/negative Long values are valid on this wire.
_BAD_DECLARED_LONG = [
    True,
    False,
    1.0,
    "1",
    None,
    2**63,
    -(2**63) - 1,
    {"value": True, "type": "Long"},
    {"value": 1.5, "type": "Long"},
    {"value": "1000", "type": "Long"},
    {"value": 100, "type": "Integer"},
    {"value": 100, "type": "Double"},
    {"value": 100, "type": "String"},
    {"value": 100},
    {"value": 2**63, "type": "Long"},
    {"value": -(2**63) - 1, "type": "Long"},
]


def _production_maps(value):
    return (
        lambda: _to_camunda_var(value, name=_VARIAVEL),
        lambda: _dmn_vars({_VARIAVEL: value})[_VARIAVEL],
        lambda: _process_vars({_VARIAVEL: value})[_VARIAVEL],
    )


@pytest.mark.parametrize("value", _BAD_DECLARED_LONG)
@pytest.mark.parametrize("mapper", range(3))
def test_declared_long_cannot_bypass_representation_policy(value, mapper):
    with pytest.raises(ValueError, match="invalid declared Long engine variable"):
        _production_maps(value)[mapper]()


@pytest.mark.parametrize("value", [-(2**63), -1, 0, 100, 2**31, 2**63 - 1])
@pytest.mark.parametrize("mapper", range(3))
def test_declared_long_preserves_exact_signed_values_and_valid_shape(value, mapper):
    assert _production_maps(value)[mapper]() == {"value": value, "type": "Long"}
    shaped = {"value": value, "type": "Long", "valueInfo": {}}
    assert _production_maps(shaped)[mapper]() is shaped


@pytest.mark.parametrize(
    "shaped",
    [
        {"value": True, "type": "Boolean"},
        {"value": 1.25, "type": "Double"},
        {"value": "100", "type": "String", "valueInfo": {}},
        {"value": '[{"ref":"synthetic"}]', "type": "Json"},
    ],
)
def test_non_declared_variables_keep_shaped_passthrough(shaped):
    assert _to_camunda_var(shaped, name="unrelated_variable") is shaped
    assert _dmn_vars({"unrelated_variable": shaped})["unrelated_variable"] is shaped
    assert _process_vars({"unrelated_variable": shaped})["unrelated_variable"] is shaped


async def _exercise_declared_long_transport(path, value, posts):
    """Only HTTP is replaced. Invoke real public APIs and sanctioned process-start producer."""
    import json

    from maezo.tools.mcp_cibseven.transport import (
        AgentDecisionProvenance,
        CibSevenHttpTransport,
        start_process_idempotent,
    )
    from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
    from tests.support.audit_fakes import FakeStartAuditSink

    def capture(request):
        if request.method == "POST":
            posts.append(json.loads(request.content))
            return httpx.Response(200, json=[] if path == "dmn" else {"id": "synthetic-instance"})
        if path == "dmn":
            return httpx.Response(
                200, json={"id": "synthetic-dmn", "version": 1, "deploymentId": "synthetic"}
            )
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        base_url="https://engine.invalid", transport=httpx.MockTransport(capture)
    ) as client:
        variables = {_VARIAVEL: value}
        if path == "dmn":
            transport = CibSevenDmnTransport("https://engine.invalid")
            transport._new_client = lambda: httpx.AsyncClient(
                base_url="https://engine.invalid", transport=httpx.MockTransport(capture)
            )
            await transport.evaluate("synthetic-definition", variables)
        elif path in ("complete", "bpmn_error"):
            transport = CibSevenWorkerTransport("https://engine.invalid")
            await transport._client.aclose()
            transport._client = client
            if path == "complete":
                await transport.complete("synthetic-task", "synthetic-worker", variables)
            else:
                await transport.handle_bpmn_error(
                    "synthetic-task", "synthetic-worker", error_code="ERR_SYNTHETIC", variables=variables
                )
        else:
            transport = CibSevenHttpTransport("https://engine.invalid")
            await transport._client.aclose()
            transport._client = client
            if path == "start":
                # R173 fact is computed, not a legitimate start input for CONTAS.
                # This tests a generic serializer boundary, never domain authorization.
                await start_process_idempotent(
                    transport,
                    process_key="SP-OP-ESCALATION-001",
                    business_key="synthetic-business",
                    variables=variables,
                    audit_sink=FakeStartAuditSink(),
                    provenance=AgentDecisionProvenance(
                        agent_id="synthetic", agent_version="v1", tenant_id="amh", decision_basis={}
                    ),
                )
            elif path == "correlation_keys":
                await transport.correlate_message("synthetic", "", {}, correlation_keys=variables)
            else:
                await transport.correlate_message("synthetic", "synthetic-business", variables)


@pytest.mark.parametrize(
    "path", ["complete", "bpmn_error", "dmn", "start", "correlation", "correlation_keys"]
)
@pytest.mark.parametrize(
    "value",
    [
        {"value": True, "type": "Long"},
        {"value": 100, "type": "Integer"},
        {"value": 2**63, "type": "Long"},
    ],
)
async def test_public_transports_refuse_declared_long_before_effect_post(path, value):
    posts = []
    with pytest.raises(ValueError, match="invalid declared Long engine variable"):
        await _exercise_declared_long_transport(path, value, posts)
    assert posts == []


@pytest.mark.parametrize(
    "path", ["complete", "bpmn_error", "dmn", "start", "correlation", "correlation_keys"]
)
async def test_public_transports_keep_exact_valid_shaped_long(path):
    posts = []
    value = {"value": 2**63 - 1, "type": "Long", "valueInfo": {}}
    await _exercise_declared_long_transport(path, value, posts)
    assert len(posts) == 1
    field = (
        "correlationKeys"
        if path == "correlation_keys"
        else "processVariables"
        if path == "correlation"
        else "variables"
    )
    assert posts[0][field][_VARIAVEL] == value


def test_engine_rest_keeps_explicit_name_blind_exemption():
    from tests.integration.processes.engine_rest import EngineRest

    # Test-only start helper is not a producer of CONTAS's computed R173 fact.
    # Its independent name-blind behavior remains explicit; production fencing is above.
    assert EngineRest._to_camunda_vars({_VARIAVEL: 100})[_VARIAVEL]["type"] == "Integer"


@pytest.mark.parametrize("valid", [False, True])
async def test_computed_worker_output_cannot_be_retyped_before_harness_effect(valid):
    import json

    from maezo.tools.workers.harness import ExternalTask, FakeAuditSink, WorkerHarness

    posts = []

    def capture(request):
        posts.append((request.url.path, json.loads(request.content)))
        return httpx.Response(204)

    transport = CibSevenWorkerTransport("https://engine.invalid")
    await transport._client.aclose()
    transport._client = httpx.AsyncClient(
        base_url="https://engine.invalid", transport=httpx.MockTransport(capture)
    )
    harness = WorkerHarness(transport, worker_id="synthetic-worker", audit_sink=FakeAuditSink())

    async def producer(task):
        result = identify_glosa_entry(_lote_com_uma_glosa())
        result[_VARIAVEL] = {"value": result[_VARIAVEL], "type": "Long" if valid else "Integer"}
        return result

    harness.register("operadora.synthetic.topic", producer)
    task = ExternalTask(
        task_id="synthetic-task",
        topic="operadora.synthetic.topic",
        process_instance_id="synthetic-process",
        business_key="synthetic-business",
        worker_id="synthetic-worker",
        variables={},
    )
    try:
        await harness._handle(task)
    finally:
        await transport.close()
    assert len(posts) == 1
    path, payload = posts[0]
    if valid:
        assert path.endswith("/complete")
        assert payload["variables"][_VARIAVEL] == {"value": _LOTE_PEQUENO_CENTAVOS, "type": "Long"}
    else:
        assert path.endswith("/failure") and payload["retries"] == 0
        assert "variables" not in payload


@pytest.mark.parametrize("mapper", range(3))
def test_declared_long_rejects_integer_subclass_without_coercion(mapper):
    class DerivedInt(int):
        def __int__(self):
            pytest.fail("transport must not coerce")

    for value in (DerivedInt(100), {"value": DerivedInt(100), "type": "Long"}):
        with pytest.raises(ValueError, match="invalid declared Long engine variable"):
            _production_maps(value)[mapper]()
