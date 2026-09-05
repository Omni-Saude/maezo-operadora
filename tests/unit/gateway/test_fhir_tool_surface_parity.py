"""Cerca de PARIDADE da superficie de tools FHIR (WP FHIR-TOOL-SURFACE-PARITY).

POR QUE ESTE ARQUIVO EXISTE. Existem TRES declaracoes independentes da mesma superficie de
leitura de PHI clinico (classe de acao `leitura_phi_clinica`, C2), e nada as amarrava uma na
outra:

  (1) `spec/agents/<a>/agent.yaml` -> `tools:` lista ids `mcp-fhir.<op>` — a allowlist que o
      L1 do PEP (`gateway/effect_pep.py::AgentCapabilities.allows_tool`) consulta;
  (2) `src/maezo/agents/<a>/{graph,adapters,delegation}.py` -> o metodo que o no REALMENTE
      chama no seam injetado (`self._fhir.<op>(...)`), que e o que
      `gateway/seams/fhir.py::GatedFhirReader` traduz em `gate(seam, "fhir.<op>")`;
  (3) `gateway/tool_registry.py::_FHIR_ADAPTER_BY_AGENT` -> qual adaptador concreto o UNICO
      construtor sancionado embrulha para aquele agente (e, por ausencia, quais agentes nunca
      recebem seam FHIR nenhum).

Quando (1) e (2) divergem o efeito nao e cosmetico: `decide_effect()` decide sobre a operacao
que (2) invoca, casa contra a allowlist de (1) e NEGA com `TOOL_NAO_DECLARADA` na camada
`L1_CAPACIDADE`. Hoje isso nao derruba producao apenas porque `leitura_phi_clinica` esta em
`enforcement: shadow` (`spec/policies/autonomy/action-approvals.yaml`, desvio disclosed com
`review_by` 2027-02-09) — ou seja, a operadora ja roda uma leitura de PHI por um id que o
proprio contrato do agente nunca declarou, e o unico motivo de nao aparecer e o modo sombra.
Achados NEW-04 (carolina, P1) e BEA-13/NEW-04-irmao (beatriz) sao exatamente esse par.

Quando (1) declara o que (2) nunca chama, o defeito e o oposto e igualmente real: uma concessao
de autonomia + superficie de ataque documentadas que nenhum no exercita (NEW-09, helena).

O VOCABULARIO NAO E ESCRITO A MAO AQUI. `_gated_fhir_operations()` extrai por AST, do proprio
`gateway/seams/fhir.py`, cada metodo de `GatedFhirReader` e a constante `_OP_*` que ele passa
para `gate(...)`. Uma cerca cuja lista de operacoes fosse um literal deste arquivo pararia de
provar o que afirma no dia em que o seam ganhasse um quinto metodo — e ninguem ficaria sabendo.
`test_gated_fhir_vocabulary_matches_the_effect_catalogue` fecha o circuito contra
`gateway/effect_classes.py::OPERATIONS`.

RELACAO COM `tests/unit/platform/test_agent_yaml_tools_wired.py` (CC-05). Aquela cerca pergunta
"o id declarado aparece EM ALGUM LUGAR do texto-fonte do agente?" (substring sobre o arquivo
inteiro, docstrings inclusive) e por isso classificava carolina/beatriz/helena como "declarado e
nao wireado" sem conseguir ver a DIVERGENCIA DE NOME. Esta cerca pergunta a pergunta que falta:
"o conjunto declarado e IGUAL ao conjunto chamado?" — por AST, sobre nos `Call` reais, e
cruzando com o mapa de adaptadores. As duas sao complementares; nenhuma substitui a outra.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from maezo.gateway import effect_classes
from maezo.gateway.effect_pep import decide_effect
from maezo.gateway.seams import GatedFhirReader
from maezo.gateway.tool_registry import (
    _FHIR_ADAPTER_BY_AGENT,
    build_agent_seam_context,
    build_fhir_seam,
)

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_AGENTS_SPEC_ROOT: Final[Path] = _REPO_ROOT / "spec" / "agents"
_AGENTS_SRC_ROOT: Final[Path] = _REPO_ROOT / "src" / "maezo" / "agents"
_SEAM_MODULE: Final[Path] = _REPO_ROOT / "src" / "maezo" / "gateway" / "seams" / "fhir.py"
_TEMPLATE_DIR_NAME: Final[str] = "_template"

#: Prefixo do id de tool no `agent.yaml`. `mcp-fhir.read_patient` -> operacao `fhir.read_patient`.
_TOOL_PREFIX: Final[str] = "mcp-fhir."

#: Camada/motivo que o PEP devolve quando o agente invoca um tool que nao declarou. E ESTE o
#: sintoma que o achado NEW-04 descreve, e e sobre ele que a prova comportamental abaixo incide.
_LAYER_CAPACIDADE: Final[str] = "L1_CAPACIDADE"
_REASON_TOOL_NAO_DECLARADA: Final[str] = "TOOL_NAO_DECLARADA"

# =================================================================================================
# Inventarios FECHADOS (ambas as direcoes sao verificadas — nao sao allowlists que crescem calado)
# =================================================================================================

#: `(agente, operacao)` declarado no `agent.yaml` que NENHUM no chama, com o porque. Uma entrada
#: aqui e uma DIVIDA nomeada, nao um perdao: `test_declared_without_call_site_inventory_is_exact`
#: reprova tanto a entrada que virou wireada quanto a que sumiu do yaml.
_DECLARED_WITHOUT_CALL_SITE: Final[dict[tuple[str, str], str]] = {
    ("helena", "read_coverage"): (
        "OWNER-GATED (nao removivel por este WP): helena e o UNICO agent.yaml que declara "
        "`mcp-fhir.read_coverage`, e `tests/unit/gateway/test_effect_enforcement.py::"
        "test_every_catalogued_tool_id_is_declared_by_some_agent` exige que todo id de "
        "`effect_classes.CATALOGUED_TOOL_IDS` seja declarado por >=1 agente. Tirar o id do "
        "catalogo exigiria editar `spec/policies/autonomy/action-approvals.yaml` "
        "(`mapeamento_acoes` tem round-trip EXATO com `OPERATIONS` — "
        "`scripts/ci/check_effect_chokepoint_fence.py` §8.5 item 3), arquivo CODEOWNED. "
        "Mesma especie da excecao de gustavo em test_agent_yaml_tools_wired.py."
    ),
}

#: Agentes cujo grafo INVOCA o seam FHIR mas que `_FHIR_ADAPTER_BY_AGENT` nao nomeia — logo
#: nenhuma raiz de composicao lhes constroi seam algum e a leitura simplesmente NAO ACONTECE
#: (`self._fhir is None` -> nota de lacuna declarada). Nao e "PEP cego": e leitura inexistente.
#: Ligar qualquer um deles ACENDERIA uma leitura de PHI que hoje nao ocorre — ALARGAMENTO de
#: superficie, decisao do dono, nunca de um implementador. Registrado aqui para que a escolha
#: seja visivel na cerca em vez de invisivel na ausencia de uma chave de dicionario.
_INVOKED_WITHOUT_GATED_SEAM: Final[dict[str, str]] = {
    "beatriz": (
        "OWNER-GATED: `gather` chama `read_patient_summary` mas nenhuma raiz injeta `fhir` para "
        "beatriz (ausente de `_FHIR_ADAPTER_BY_AGENT` e de `_DOSSIER_FHIR_AGENT_IDS`), entao o "
        "ramo vive so na nota `NOTE_FHIR_READER_NAO_CONFIGURADO`. Ligar = acender uma leitura de "
        "PHI inexistente hoje (BEA-13)."
    ),
    "marina": (
        "OWNER-GATED: mesma forma de beatriz — `gather` chama `read_patient_summary`, marina esta "
        "ausente de `_FHIR_ADAPTER_BY_AGENT`, e a leitura nunca ocorre em producao."
    ),
}


# =================================================================================================
# Extracao (AST, nunca grep) das tres declaracoes
# =================================================================================================


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """`_OP_READ_PATIENT = "fhir.read_patient"` -> `{"_OP_READ_PATIENT": "fhir.read_patient"}`."""
    constants: dict[str, str] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if value is None or not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return constants


def _gated_fhir_operations() -> dict[str, str]:
    """`{metodo de GatedFhirReader: token de operacao que ele passa a gate()}`, lido por AST.

    Derivado do modulo do seam e nao escrito a mao para que um quinto metodo (ou um metodo que
    deixe de chamar `gate`) mude o que esta cerca cobre AUTOMATICAMENTE.
    """
    tree = ast.parse(_SEAM_MODULE.read_text(encoding="utf-8"))
    constants = _module_string_constants(tree)
    operations: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != GatedFhirReader.__name__:
            continue
        for member in node.body:
            if not isinstance(member, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            for call in ast.walk(member):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                    continue
                if call.func.id != "gate" or len(call.args) < 2:
                    continue
                token = call.args[1]
                if isinstance(token, ast.Name) and token.id in constants:
                    operations[member.name] = constants[token.id]
                elif isinstance(token, ast.Constant) and isinstance(token.value, str):
                    operations[member.name] = token.value
    assert operations, (
        f"nenhum metodo gated encontrado em {GatedFhirReader.__name__} — a extracao AST quebrou "
        "(cerca vacua), nao o seam"
    )
    return operations


_FHIR_OPERATIONS: Final[dict[str, str]] = _gated_fhir_operations()


def _real_agent_ids() -> list[str]:
    ids = sorted(
        p.name
        for p in _AGENTS_SPEC_ROOT.iterdir()
        if p.is_dir() and p.name != _TEMPLATE_DIR_NAME and (p / "agent.yaml").exists()
    )
    assert ids, f"nenhum agent.yaml sob {_AGENTS_SPEC_ROOT} — o glob quebrou, nao a arvore"
    return ids


def _agent_yaml(agent_id: str) -> dict[str, Any]:
    data = yaml.safe_load((_AGENTS_SPEC_ROOT / agent_id / "agent.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _declared_operations(agent_id: str) -> set[str]:
    """Operacoes FHIR que o `agent.yaml` de `agent_id` declara (sufixo de `mcp-fhir.<op>`)."""
    return {
        tool[len(_TOOL_PREFIX) :]
        for tool in (_agent_yaml(agent_id).get("tools") or [])
        if tool.startswith(_TOOL_PREFIX)
    }


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return "<expr>"


def _invoked_operations(agent_id: str) -> dict[str, list[str]]:
    """`{operacao: [call sites]}` que os modulos do PROPRIO agente chamam no seam FHIR.

    Varre por AST cada `*.py` de `src/maezo/agents/<id>/` (menos `__init__.py`, que so
    re-exporta) e coleta os nos `Call` cujo `func` e um `Attribute` com nome de operacao gated.
    Uma `async def read_patient(...)` de adaptador e uma DEFINICAO, nao um `Call`, e por isso
    nao conta — que e exatamente a distincao que a cerca por substring de CC-05 nao consegue
    fazer.
    """
    src_dir = _AGENTS_SRC_ROOT / agent_id
    assert src_dir.is_dir(), f"sem src/maezo/agents/{agent_id}/ — id de spec sem codigo irmao"
    found: dict[str, list[str]] = {}
    for py_file in sorted(src_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in _FHIR_OPERATIONS:
                continue
            found.setdefault(node.func.attr, []).append(
                f"{py_file.name}:{node.lineno} ({_dotted(node.func.value)}.{node.func.attr})"
            )
    return found


def _adapter_inner(adapter: str) -> Any:
    """O adaptador concreto que `build_fhir_seam` embrulha para `adapter`.

    Construido pelo construtor REAL (a construcao e pura — `FhirServer` so abre cliente HTTP
    quando um metodo roda), nunca por uma tabela paralela neste arquivo: uma segunda copia da
    escolha adaptador->classe seria a propria deriva que a cerca existe para pegar.
    """
    seam = build_fhir_seam(
        seam=build_agent_seam_context(tenant="amh", agent_id="rafael"),
        base_url="http://fhir.invalid/fhir",
        adapter=adapter,
    )
    return seam.inner


_AGENT_IDS: Final[list[str]] = _real_agent_ids()
_AGENTS_THAT_READ_FHIR: Final[list[str]] = [a for a in _AGENT_IDS if _invoked_operations(a)]


# =================================================================================================
# 1) paridade declarado <-> invocado
# =================================================================================================


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_every_invoked_fhir_operation_is_declared_by_the_agent(agent_id: str) -> None:
    """SEM inventario e sem excecao: chamar uma leitura de PHI que o contrato nao declara e o
    defeito NEW-04 em si. Um `agent.yaml` que precise de outra operacao muda o YAML (ato de
    ALARGAMENTO, visivel em diff e em review), nunca uma linha desta cerca."""
    declared = _declared_operations(agent_id)
    invoked = _invoked_operations(agent_id)
    undeclared = {op: sites for op, sites in invoked.items() if op not in declared}
    assert not undeclared, (
        f"{agent_id}: o grafo invoca operacao(oes) FHIR que o agent.yaml NAO declara -> o PEP "
        f"nega com {_REASON_TOOL_NAO_DECLARADA} em {_LAYER_CAPACIDADE} (mascarado hoje so pelo "
        f"enforcement=shadow de leitura_phi_clinica). Declarado={sorted(declared)}; "
        f"nao declarado e chamado={ {op: sites for op, sites in sorted(undeclared.items())} }"
    )


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_every_declared_fhir_tool_has_a_call_site_or_is_classified(agent_id: str) -> None:
    """A direcao inversa (NEW-09): um `mcp-fhir.*` declarado que nenhum no chama e concessao de
    autonomia + superficie de ataque sem contrapartida. Ou some do YAML, ou entra no inventario
    fechado com o motivo."""
    invoked = set(_invoked_operations(agent_id))
    unexercised = sorted(
        op
        for op in _declared_operations(agent_id)
        if op not in invoked and (agent_id, op) not in _DECLARED_WITHOUT_CALL_SITE
    )
    assert not unexercised, (
        f"{agent_id}: agent.yaml declara mcp-fhir.{{{','.join(unexercised)}}} que nenhum no "
        "chama e que nao esta em _DECLARED_WITHOUT_CALL_SITE — remova a declaracao, ligue o no, "
        "ou classifique com o motivo"
    )


def test_declared_without_call_site_inventory_is_exact() -> None:
    """O inventario nao pode envelhecer: toda chave precisa continuar DECLARADA e SEM call site."""
    stale: list[str] = []
    for (agent_id, operation), _reason in sorted(_DECLARED_WITHOUT_CALL_SITE.items()):
        if agent_id not in _AGENT_IDS:
            stale.append(f"{agent_id}: agente nao existe mais")
            continue
        if operation not in _declared_operations(agent_id):
            stale.append(f"{agent_id}: mcp-fhir.{operation} nao e mais declarado — remova a entrada")
            continue
        if operation in _invoked_operations(agent_id):
            stale.append(f"{agent_id}: mcp-fhir.{operation} agora tem call site — remova a entrada")
    assert not stale, "entradas obsoletas em _DECLARED_WITHOUT_CALL_SITE:\n" + "\n".join(stale)


# =================================================================================================
# 2) paridade invocado <-> seam gated construido (`_FHIR_ADAPTER_BY_AGENT`)
# =================================================================================================


@pytest.mark.parametrize("agent_id", _AGENTS_THAT_READ_FHIR)
def test_every_agent_that_reads_fhir_has_a_gated_seam_or_is_classified(agent_id: str) -> None:
    """Quem le PHI recebe o seam GATED do unico construtor sancionado — ou esta no inventario
    fechado dizendo que nao recebe seam algum (e portanto nao le)."""
    assert agent_id in _FHIR_ADAPTER_BY_AGENT or agent_id in _INVOKED_WITHOUT_GATED_SEAM, (
        f"{agent_id}: o grafo chama {sorted(_invoked_operations(agent_id))} mas o agente nao esta "
        "em _FHIR_ADAPTER_BY_AGENT nem em _INVOKED_WITHOUT_GATED_SEAM — classifique"
    )


def test_invoked_without_gated_seam_inventory_is_exact() -> None:
    """Mesma regra de exatidao do outro inventario, nas duas direcoes."""
    stale: list[str] = []
    for agent_id, _reason in sorted(_INVOKED_WITHOUT_GATED_SEAM.items()):
        if agent_id not in _AGENT_IDS:
            stale.append(f"{agent_id}: agente nao existe mais")
            continue
        if not _invoked_operations(agent_id):
            stale.append(f"{agent_id}: nenhum no chama FHIR — remova a entrada")
        if agent_id in _FHIR_ADAPTER_BY_AGENT:
            stale.append(f"{agent_id}: agora tem seam gated em _FHIR_ADAPTER_BY_AGENT — remova a entrada")
    assert not stale, "entradas obsoletas em _INVOKED_WITHOUT_GATED_SEAM:\n" + "\n".join(stale)


@pytest.mark.parametrize("agent_id", sorted(_FHIR_ADAPTER_BY_AGENT))
def test_gated_seam_is_only_built_for_an_agent_that_actually_reads(agent_id: str) -> None:
    """Menor privilegio na direcao da construcao: montar (e portanto injetar) um leitor FHIR
    para um agente cujo grafo nunca le e entregar uma capacidade que nada exercita."""
    assert _invoked_operations(agent_id), (
        f"{agent_id}: esta em _FHIR_ADAPTER_BY_AGENT mas nenhum no do seu grafo chama uma "
        "operacao FHIR — remova a chave (o seam deixa de ser construido) ou ligue o no"
    )


@pytest.mark.parametrize("agent_id", sorted(_FHIR_ADAPTER_BY_AGENT))
def test_selected_adapter_implements_every_operation_the_agent_invokes(agent_id: str) -> None:
    """O adaptador escolhido tem de implementar TODAS as operacoes que o grafo chama.

    `GatedFhirReader` delega (`self._inner.<op>`), entao um par (mapa, grafo) desalinhado nao da
    erro de tipo: da `AttributeError` no meio de um turno vivo, ja depois do `gate`.
    """
    inner = _adapter_inner(_FHIR_ADAPTER_BY_AGENT[agent_id])
    missing = sorted(op for op in _invoked_operations(agent_id) if not hasattr(inner, op))
    assert not missing, (
        f"{agent_id}: adaptador {_FHIR_ADAPTER_BY_AGENT[agent_id]!r} -> "
        f"{type(inner).__module__}.{type(inner).__name__} nao implementa {missing}, que o grafo "
        "chama — o mapa e o grafo divergiram"
    )


# =================================================================================================
# 3) integridade do vocabulario e prova COMPORTAMENTAL no PEP real
# =================================================================================================


def test_gated_fhir_vocabulary_matches_the_effect_catalogue() -> None:
    """Os quatro pontos da traducao (`metodo` -> `operacao` -> `tool_id`) tem de fechar em ciclo
    com `effect_classes.OPERATIONS`, senao a extracao AST acima mede uma coisa e o PEP outra."""
    catalogued = {
        op: spec.tool_id for op, spec in effect_classes.OPERATIONS.items() if op.startswith("fhir.")
    }
    assert set(_FHIR_OPERATIONS.values()) == set(catalogued), (
        "operacoes gated em GatedFhirReader != operacoes fhir.* do catalogo: "
        f"seam={sorted(_FHIR_OPERATIONS.values())} catalogo={sorted(catalogued)}"
    )
    for method, operation in sorted(_FHIR_OPERATIONS.items()):
        assert catalogued[operation] == f"{_TOOL_PREFIX}{method}", (
            f"o metodo {method!r} decide sobre {operation!r}, cujo tool_id catalogado e "
            f"{catalogued[operation]!r} — o sufixo do id e o nome do metodo tem de coincidir, "
            "senao a comparacao declarado<->invocado deste arquivo compara nomes de mundos "
            "diferentes"
        )


@pytest.mark.parametrize("agent_id", _AGENTS_THAT_READ_FHIR)
def test_pep_never_denies_an_agents_own_fhir_read_for_an_undeclared_tool(agent_id: str) -> None:
    """PROVA COMPORTAMENTAL (NEW-04/BEA-13), pelo PEP de verdade e pelos manifestos embarcados.

    Nao afirma ALLOW: hoje toda leitura de PHI cai em `MANIFESTO_NAO_RATIFICADO`/`L5_RATIFICACAO`
    (o manifesto embarcado e `status: DRAFT`, `modo: shadow`) — negacao universal, owner-gated,
    que nao e desta cerca. O que ela afirma e que a negacao NAO pode ser
    `TOOL_NAO_DECLARADA`/`L1_CAPACIDADE`: essa e a que sinaliza que o proprio contrato do agente
    nao conhece a operacao que o codigo dele executa.
    """
    context = build_agent_seam_context(tenant="amh", agent_id=agent_id)
    offenders: list[str] = []
    for method in sorted(_invoked_operations(agent_id)):
        decision = decide_effect(
            tenant="amh",
            principal=agent_id,
            operation=_FHIR_OPERATIONS[method],
            ctx=context.decision,
        )
        if decision.reason == _REASON_TOOL_NAO_DECLARADA or decision.layer == _LAYER_CAPACIDADE:
            offenders.append(f"{_FHIR_OPERATIONS[method]} -> reason={decision.reason} layer={decision.layer}")
    assert not offenders, (
        f"{agent_id}: o PEP nega em {_LAYER_CAPACIDADE} a leitura que o proprio grafo faz "
        f"(declarado={sorted(_declared_operations(agent_id))}): {offenders}"
    )


# =================================================================================================
# 4) provas COMPORTAMENTAIS por agente (o que a paridade estrutural acima nao alcanca)
# =================================================================================================


class _RecordingSummaryReader:
    """Leitor de resumo FHIR falso — expoe SO `read_patient_summary`, que e a operacao declarada.

    Deliberadamente sem `read_patient`: se o grafo voltar a chamar a operacao nao declarada, o
    `except Exception` best-effort do `gather` engole o `AttributeError` e `summary_facts` fica
    vazio — e a asserceao sobre `calls` reprova. E o duplo que recusa o que o contrato recusa.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        self.calls.append(patient_id)
        return {"resourceType": "Patient", "id": patient_id}


class _RecordingFhirServer:
    """Duplo do cliente generico `tools/mcp_fhir/server.py::FhirServer` (sem rede)."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.reads: list[tuple[str, str]] = []
        self.searches: list[tuple[str, dict[str, str] | None]] = []

    async def read_resource(self, resource_type: str, resource_id: str) -> dict[str, Any]:
        self.reads.append((resource_type, resource_id))
        return self._payload

    async def search_resources(
        self, resource_type: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self.searches.append((resource_type, params))
        return self._payload


async def test_carolina_gather_reads_the_declared_summary_through_the_real_gated_seam() -> None:
    """NEW-04, fim a fim: o `gather` de Carolina, embrulhado pelo `GatedFhirReader` REAL sob o
    `SeamContext` dela, exerce a operacao que o `agent.yaml` dela declara — e so essa."""
    from maezo.agents.carolina.graph import CarolinaGraph, CarolinaState
    from maezo.gateway.seams.fhir import gate_fhir
    from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
    from maezo.tools.workers.dmn_transport import FakeDmnTransport
    from tests.support.audit_fakes import FakeStartAuditSink

    inner = _RecordingSummaryReader()
    seam = gate_fhir(inner, build_agent_seam_context(tenant="amh", agent_id="carolina"))
    graph = CarolinaGraph(
        inference=_NullInference(),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        fhir=seam,
    )
    state: CarolinaState = {"patient_summary_ref": "pseudo-cred-1"}  # type: ignore[typeddict-item]

    result = await graph.gather(state)

    assert inner.calls == ["pseudo-cred-1"], (
        "o gather de Carolina nao exerceu `read_patient_summary` no seam gated — ou chamou outra "
        f"operacao (notas: {result.get('gather_notes')})"
    )
    assert result["summary_facts"] == {"resourceType": "Patient", "id": "pseudo-cred-1"}
    assert result["gather_notes"] == []
    assert seam.seam_context.principal == "carolina", (
        "o seam decide sob o principal de Carolina — um seam compartilhado decidiria a leitura "
        "dela sob a capacidade declarada de outro agente"
    )


class _NullInference:
    """Provedor de inferencia inerte — `gather` nao chama LLM; existe so para montar o grafo."""

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        raise AssertionError("gather nao deve chamar inferencia")


@pytest.mark.parametrize(
    "module_name",
    ["maezo.agents.valentina.adapters", "maezo.agents.marina.adapters"],
)
async def test_summary_adapter_refuses_a_resource_that_is_not_a_patient(module_name: str) -> None:
    """VAL-02, FAIL-CLOSED. O `agent.yaml` declara uma leitura de RESUMO DE PACIENTE; o cliente
    por baixo e um HAPI R4 generico. Se o servidor devolver outro `resourceType` (particao/tenant
    trocados, proxy mal configurado, `OperationOutcome` com 200), o adaptador RECUSA em vez de
    entregar PHI de forma desconhecida para `summary_facts` -> prompt -> dossie selado."""
    import importlib

    reader = importlib.import_module(module_name).FhirServerReader(
        _RecordingFhirServer({"resourceType": "Observation", "id": "obs-1"})
    )
    with pytest.raises(RuntimeError):
        await reader.read_patient_summary("pseudo-1")


async def test_rafael_patient_adapter_refuses_a_resource_that_is_not_a_patient() -> None:
    """Mesma forma, no adaptador que serve rafael/carolina/gustavo/andre (`read_patient`)."""
    from maezo.agents.rafael.adapters import FhirServerReader

    reader = FhirServerReader(_RecordingFhirServer({"resourceType": "Practitioner", "id": "prac-1"}))
    with pytest.raises(RuntimeError):
        await reader.read_patient("pseudo-1")


async def test_rafael_coverage_adapter_refuses_a_payload_that_is_not_a_bundle() -> None:
    """`search_coverage` promete um Bundle; qualquer outra coisa vira lista vazia SILENCIOSA hoje
    (`bundle.get("entry", [])`) — indistinguivel de "beneficiario sem cobertura", que e um fato
    clinico-administrativo. Recusar e a unica leitura honesta."""
    from maezo.agents.rafael.adapters import FhirServerReader

    reader = FhirServerReader(_RecordingFhirServer({"resourceType": "OperationOutcome"}))
    with pytest.raises(RuntimeError):
        await reader.search_coverage("pseudo-1")


@pytest.mark.parametrize("agent_id", sorted(_FHIR_ADAPTER_BY_AGENT))
def test_registry_builds_a_gated_reader_bound_to_the_agents_own_principal(agent_id: str) -> None:
    """RAF-03(c)/BEA-13, metade VERIFICADA: para todo agente do mapa o UNICO construtor
    sancionado devolve um `GatedFhirReader` fechado sobre o principal DAQUELE agente — e por isso
    o PEP decide a leitura dele sob a capacidade declarada dele."""
    from maezo.gateway.seams import is_gated_seam
    from maezo.gateway.tool_registry import build_agent_fhir_seam

    class _Settings:
        tenant_id = "amh"
        fhir_base_url = "http://fhir.invalid/fhir"

    seam = build_agent_fhir_seam(settings=_Settings(), agent_id=agent_id)
    assert seam is not None
    assert is_gated_seam(seam)
    assert isinstance(seam, GatedFhirReader)
    assert seam.seam_context.principal == agent_id
