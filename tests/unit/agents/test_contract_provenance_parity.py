"""CC-13 (Agent Fleet Audit §1, cross-cutting, P2) — spec-first parity: every ADDITIVE process
variable an agent's `_contract_variables`/`_escalation_variables`/`_inadimplencia_variables`
grounds into the engine must be declared in its own contract's markdown — either the base
"Variaveis de entrada"/"Variaveis de saida" tables, or an agent-specific
"Variaveis de proveniencia do agente" section (the pattern `andre`/`docs/processes/contracts/
SP-OP-PAGTO-001.md` established, and that `helena`/`SP-OP-ESCALATION-001.md` already satisfies
with ZERO additive keys — helena's `_start_escalation` writes exactly the contract's declared
input set, nothing more).

Before this fix, 7 agents grounded additive provenance/routing variables
(`dossie_<agente>`, `<agente>_route`/`<agente>_flow`, `motivo_encaminhamento`,
`grupo_destino`/`grupo_humano_sugerido`, `dmn_decision_refs`/`dmn_decision_ref`) into 9
agent/contract flows that declared none of them:
rafael->SP-OP-AUTH-001, marina->SP-OP-CONTAS-001, marina->SP-OP-RECURSO-001,
carolina->SP-OP-CRED-001, lucas->SP-OP-ESCALATION-001 (Lucas's OWN additive annotations — Helena,
who starts the SAME shared contract, adds none), gustavo->SP-OP-NIP-001,
gustavo->SP-OP-ANS-SUBMIT-001, valentina->SP-OP-PROGRAMA-001, fernando->SP-OP-INADIMPLENCIA-001.

`marina`'s THIRD flow, `reembolso` (SP-OP-REEMBOLSO-001), is DELIBERATELY excluded: her
`start_process` no-ops for that flow (SP-OP-REEMBOLSO-001 is already running when
`reembolso.analyze` arrives — `MarinaGraph.start_process`'s own docstring/guard), and the real
runtime worker for `ST_PrepararDossie` (`src/maezo/tools/workers/reembolso.py::analyze_request`)
is a SEPARATE, independent implementation that never calls `MarinaGraph` at all
(`grep -rln MarinaGraph src/maezo/` matches only `marina/graph.py` itself) — so none of
`MarinaGraph`'s reembolso-branch additive keys (`dossie_marina`/`marina_flow`/`marina_route` for
that flow) ever reach a real SP-OP-REEMBOLSO-001 engine instance. There is nothing to declare
there for CC-13's purposes.

SCOPE (deliberate, disclosed): this checks parity for the SPECIFIC additive keys named above —
NOT a fully generic "every engine variable must appear in a Variavel table" fence. Several BASE
input facts have PRE-EXISTING declaration gaps unrelated to CC-13 and reproducible with the same
harness (e.g. `divergencia_valor` in SP-OP-CONTAS-001 is only in prose/DMN-io tables, not its
Entrada table; `beneficiario_pseudo_id`/`canal`/`motivo_categoria`/`resumo_contexto` in
SP-OP-INADIMPLENCIA-001 likewise; `prazo_resposta_iso` in SP-OP-NIP-001 and `due_date` in
SP-OP-ANS-SUBMIT-001 are DMN `out` rows, not Entrada/Saida rows) — a DIFFERENT, wider finding,
NOT fixed here (fixing them edits BASE input-fact tables, a materially bigger change than
declaring agent-provenance additions; CC-13's own audit scope is the additive/provenance class
only). Left exactly as found.

Two layers per (agent, contract) case:

  1. BEHAVIORAL, real graphs (mirrors `test_start_process_provenance_contract.py`'s harness):
     drives the agent's start node with a `FakeCibSevenTransport` + a minimal start-triggering
     state, reads back the REAL variables dict the engine fake received
     (`get_process_status(business_key).variables`), and asserts the additive keys the agent's
     builder UNCONDITIONALLY emits for that state are actually present — a documentation-
     regression guard: this fails loudly (not silently passes) if the graph ever stops emitting
     a key this test exists to police.
  2. STATIC: every additive key in the flow's full set (including the ones conditional on
     `route=="human_review"`/similar, not exercised by this minimal auto-route state) must be
     declared in the corresponding contract's markdown.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

import pytest

from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import DmnVersion
from tests.support.audit_fakes import FakeStartAuditSink

_REPO_ROOT = Path(__file__).parents[3]
_CONTRACTS_DIR = _REPO_ROOT / "docs" / "processes" / "contracts"

_SECTION_HEADERS = (
    "## Variaveis de entrada",
    "## Variaveis de saida",
    "## Variaveis de proveniencia",
)
_ROW_RE = re.compile(r"^\|\s*`([A-Za-z0-9_]+)`\s*\|")


def _declared_variables(contract_id: str) -> set[str]:
    """Every backtick-quoted variable name in the first column of a table row under the
    contract's entrada/saida/proveniencia sections (mirrors the andre/SP-OP-PAGTO-001.md
    precedent's table shape: `| `var` | tipo | obrigatoria | descricao |`)."""
    text = (_CONTRACTS_DIR / f"{contract_id}.md").read_text(encoding="utf-8")
    declared: set[str] = set()
    active = False
    for line in text.splitlines():
        if line.startswith("## "):
            active = any(line.startswith(h) for h in _SECTION_HEADERS)
            continue
        if active:
            m = _ROW_RE.match(line)
            if m:
                declared.add(m.group(1))
    return declared


class _FakeInference:
    model_id = "cc13-contract-provenance-probe"

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        # CC-12 x integracao lote3 (LOTE3-INTEGRATION-FAKES-TASK-KIND): assinatura acompanha o
        # Protocol real (`runtime/inference::InferenceProvider.generate`), mesma especie do
        # defeito f1bc87f.
        task_kind: str | None = None,
    ) -> str:
        return "sintetico"


class _FakeDmn:
    # P-17 (PROTOCOL-FAKE-FENCES, REG-04): parametros renomeados para os do Protocol real
    # `tools/workers/dmn_transport.py::DmnTransport.evaluate` -- so' o NOME mudou (chamadas
    # reais sao posicionais), nenhum comportamento.
    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        return (
            [
                {
                    "roteamento": "SEGUE_ROTEAMENTO",
                    "recomendacao": "AUTO_APROVAR",
                    "faixa_valor": "DENTRO_TETO_L2",
                }
            ],
            DmnVersion("t", "id1", 1, "d1"),
        )


class _FakeWhatsApp:
    # P-17 (REG-04): `to` -> `to_hash`, o nome do Protocol real `WhatsAppSender.send`.
    # Trem train-b (LUC-08 x P-17): este falso e COMPARTILHADO por helena/fernando/lucas
    # (o grafo vem de `importlib.import_module(f"maezo.agents.{agent_id}.graph")`), e depois
    # de LUC-08 os tres Protocols DIVERGEM: so lucas declara `*, idempotency_key: str` (sem
    # default). Nenhuma assinatura EXPLICITA satisfaz os tres — medido: com o kwonly exigido a
    # cerca aponta "keyword-only extra" contra helena/fernando; com default, aponta tambem
    # "Protocol nao tem default, falso tem" contra lucas. `**_kwargs` e a saida que a PROPRIA
    # cerca declara (`if not tem_varkw_fake:` em `_ofensas_de_assinatura`): um falso que aceita
    # qualquer keyword nao pode quebrar chamador nenhum. Nada foi enfraquecido nem alargado nos
    # Protocols de helena/fernando.
    async def send(self, to_hash: str, text: str, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}


# (agent_id, GraphClass name, ctor extra kwargs, start-node method, minimal start-triggering
# state, contract id, keys UNCONDITIONALLY emitted by this state (behavioral guard), FULL
# additive key set for this flow including route-conditional ones (static declaration check
# only)). States mirror `test_start_process_provenance_contract.py::_CASES` (proven to reach
# `start_process` without a no-op guard); the two new flows (marina/recurso, gustavo/ans_submit)
# were derived the same way and verified to reach a real engine start.
_CASES: list[tuple[str, str, dict[str, Any], str, dict[str, Any], str, frozenset[str], frozenset[str]]] = [
    (
        "rafael",
        "RafaelGraph",
        {"fhir": None},
        "start_process",
        {"tenant_id": "amh", "business_key": "AUTH-amh-1", "route": "auto_approve", "guia_id": "G1"},
        "SP-OP-AUTH-001",
        frozenset({"source_agent_id", "source_agent_version", "dossie_rafael", "rafael_route"}),
        frozenset(
            {
                "source_agent_id",
                "source_agent_version",
                "dossie_rafael",
                "rafael_route",
                "motivo_encaminhamento",
                "dmn_decision_refs",
            }
        ),
    ),
    (
        "marina",
        "MarinaGraph",
        {"fhir": None},
        "start_process",
        {
            "tenant_id": "amh",
            "business_key": "CONTAS-amh-1",
            "route": "auto_route",
            "flow": "contas",
            "fluxo": "contas",
        },
        "SP-OP-CONTAS-001",
        frozenset(
            {"source_agent_id", "source_agent_version", "dossie_marina", "marina_flow", "marina_route"}
        ),
        frozenset(
            {
                "source_agent_id",
                "source_agent_version",
                "dossie_marina",
                "marina_flow",
                "marina_route",
                "motivo_encaminhamento",
                "grupo_destino",
                "dmn_decision_refs",
            }
        ),
    ),
    (
        "marina",
        "MarinaGraph",
        {"fhir": None},
        "start_process",
        {
            "tenant_id": "amh",
            "business_key": "RECURSO-amh-1",
            "route": "auto_route",
            "flow": "recurso",
            "numero_guia_tiss": "G1",
            "glosa_id": "GL1",
        },
        "SP-OP-RECURSO-001",
        frozenset(
            {"source_agent_id", "source_agent_version", "dossie_marina", "marina_flow", "marina_route"}
        ),
        frozenset(
            {
                "source_agent_id",
                "source_agent_version",
                "dossie_marina",
                "marina_flow",
                "marina_route",
                "motivo_encaminhamento",
                "grupo_destino",
                "dmn_decision_refs",
            }
        ),
    ),
    (
        "carolina",
        "CarolinaGraph",
        {"fhir": None},
        "start_process",
        {"tenant_id": "amh", "business_key": "CRED-amh-1", "route": "auto_route"},
        "SP-OP-CRED-001",
        frozenset({"source_agent_id", "source_agent_version", "dossie_carolina", "carolina_route"}),
        frozenset(
            {
                "source_agent_id",
                "source_agent_version",
                "dossie_carolina",
                "carolina_route",
                "motivo_encaminhamento",
                "grupo_destino",
                "dmn_decision_refs",
            }
        ),
    ),
    (
        "lucas",
        "LucasGraph",
        {"whatsapp": _FakeWhatsApp()},
        "start_process",
        {"tenant_id": "amh", "business_key": "ESC-amh-1", "route": "escalate_human"},
        "SP-OP-ESCALATION-001",
        frozenset({"dossie_lucas", "lucas_route", "motivo_encaminhamento", "grupo_humano_sugerido"}),
        frozenset(
            {
                "dossie_lucas",
                "lucas_route",
                "motivo_encaminhamento",
                "grupo_humano_sugerido",
                "dmn_decision_refs",
            }
        ),
    ),
    (
        "gustavo",
        "GustavoGraph",
        {"fhir": None},
        "start_process",
        {
            "tenant_id": "amh",
            "business_key": "NIP-amh-1",
            "process_key": "SP-OP-NIP-001",
            "route": "instruct_nip",
            "fluxo": "nip",
        },
        "SP-OP-NIP-001",
        frozenset(
            {
                "source_agent_id",
                "source_agent_version",
                "dossie_gustavo",
                "gustavo_route",
                "motivo_encaminhamento",
            }
        ),
        frozenset(
            {
                "source_agent_id",
                "source_agent_version",
                "dossie_gustavo",
                "gustavo_route",
                "motivo_encaminhamento",
                "dmn_decision_refs",
            }
        ),
    ),
    (
        "gustavo",
        "GustavoGraph",
        {"fhir": None},
        "start_process",
        {
            "tenant_id": "amh",
            "business_key": "ANSSUB-amh-1",
            "route": "review_submission",
            "fluxo": "ans_submit",
        },
        "SP-OP-ANS-SUBMIT-001",
        frozenset(
            {
                "source_agent_id",
                "source_agent_version",
                "dossie_gustavo",
                "gustavo_route",
                "motivo_encaminhamento",
            }
        ),
        frozenset(
            {
                "source_agent_id",
                "source_agent_version",
                "dossie_gustavo",
                "gustavo_route",
                "motivo_encaminhamento",
                "dmn_decision_refs",
            }
        ),
    ),
    (
        "valentina",
        "ValentinaGraph",
        {"fhir": None},
        "start_process",
        {"tenant_id": "amh", "business_key": "PROG-amh-1", "route": "auto_route", "programa": "cronicos"},
        "SP-OP-PROGRAMA-001",
        frozenset(
            {
                "source_agent_id",
                "source_agent_version",
                "dossie_valentina",
                "valentina_task",
                "valentina_route",
            }
        ),
        frozenset(
            {
                "source_agent_id",
                "source_agent_version",
                "dossie_valentina",
                "valentina_task",
                "valentina_route",
                "motivo_encaminhamento",
                "grupo_destino",
                "dmn_decision_refs",
            }
        ),
    ),
    (
        "fernando",
        "FernandoGraph",
        {"whatsapp": _FakeWhatsApp()},
        "start_process",
        {"tenant_id": "amh", "business_key": "INAD-amh-1", "route": "escalate"},
        "SP-OP-INADIMPLENCIA-001",
        frozenset(
            {
                "source_agent_id",
                "source_agent_version",
                "dossie_fernando",
                "fernando_route",
                "motivo_encaminhamento",
            }
        ),
        frozenset(
            {
                "source_agent_id",
                "source_agent_version",
                "dossie_fernando",
                "fernando_route",
                "motivo_encaminhamento",
                "dmn_decision_refs",
                "dmn_decision_ref",
            }
        ),
    ),
]


def _graph_class(agent_id: str, class_name: str) -> Any:
    return getattr(importlib.import_module(f"maezo.agents.{agent_id}.graph"), class_name)


@pytest.mark.parametrize(
    ("agent_id", "class_name", "extra", "node", "state", "contract_id", "always_emitted", "full_additive"),
    _CASES,
    ids=[f"{c[0]}->{c[5]}" for c in _CASES],
)
async def test_agent_additive_provenance_variables_are_declared_in_contract(
    agent_id: str,
    class_name: str,
    extra: dict[str, Any],
    node: str,
    state: dict[str, Any],
    contract_id: str,
    always_emitted: frozenset[str],
    full_additive: frozenset[str],
) -> None:
    transport = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    graph = _graph_class(agent_id, class_name)(
        inference=_FakeInference(), dmn=_FakeDmn(), cibseven=transport, audit_sink=sink, **extra
    )

    result = await getattr(graph, node)(dict(state))
    business_key = result.get("business_key") or state.get("business_key")
    status = await transport.get_process_status(business_key)
    actual_variables = set(status.variables.keys())

    # Layer 1 — behavioral: this minimal state's UNCONDITIONAL additive keys must actually reach
    # the engine (documentation-regression guard: a graph change that stops emitting one of these
    # must fail this test loudly, not pass it vacuously).
    missing_from_engine = always_emitted - actual_variables
    assert not missing_from_engine, (
        f"{agent_id}/{contract_id}: expected additive key(s) {sorted(missing_from_engine)} in the "
        f"engine variables for this state, but the graph did not emit them — update this test's "
        f"expectation if the graph's contract-variable builder intentionally changed."
    )

    # Layer 2 — static: EVERY additive key this flow can emit (including route-conditional ones
    # this minimal state does not exercise) must be declared in the contract's own markdown.
    declared = _declared_variables(contract_id)
    undeclared = sorted(full_additive - declared)
    assert not undeclared, (
        f"{agent_id} writes additive provenance/routing variable(s) {undeclared} into "
        f"{contract_id}'s engine scope, but {contract_id}.md declares none of them under "
        f"{_SECTION_HEADERS} (CC-13 — Agent Fleet Audit §1). Add a "
        f"'## Variaveis de proveniencia do agente' section mirroring "
        f"docs/processes/contracts/SP-OP-PAGTO-001.md's (andre)."
    )
