"""Drift-proof contract test: EVERY agent that starts a BPMN process audits through the T-C2 fence.

Two layers, both durable (design §6 — "a parametrized contract test over the callers OR an arch-test
that greps/imports — durable, not brittle"), covering all present AND future agent start sites:

  1. BEHAVIORAL — build each of the 9 agent graphs with a recording `FakeStartAuditSink`, drive its
     process-start node, and assert it emitted exactly one well-formed, PHI-safe ADR-0007 record
     BEFORE the engine effect, with the design's dedup-key shape.
  2. STRUCTURAL (AST) — every `start_process_idempotent(...)` call in `maezo.agents.*.graph` passes
     BOTH `audit_sink=` and `provenance=`; and every agent `build(config)` fail-closes without a
     sink. A new agent that lands a start site without the fence args breaks this test.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any

import pytest

from maezo.gateway.audit import EmitOnceOutcome
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import DmnVersion
from maezo.tools.workers.phi_vars import REDACTED_PHI
from tests.support.audit_fakes import FakeStartAuditSink


class _FakeInference:
    model_id = "claude-contract-probe"

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        return "sintetico"


class _FakeDmn:
    # P-17 (PROTOCOL-FAKE-FENCES, REG-04): parametros renomeados para os do Protocol real
    # `tools/workers/dmn_transport.py::DmnTransport.evaluate` -- so' o NOME mudou (chamadas
    # reais sao posicionais), nenhum comportamento. Este era o QUINTO modulo com o mesmo
    # defeito de REG-04, achado pela cerca generalizada (a evidencia original citava 4).
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


# (agent_id, GraphClass attr suffix, ctor extra kwargs, start-node method, minimal start-triggering
# state, expected process_key). States were derived empirically to reach the start (no no-op guard).
_CASES: list[tuple[str, str, dict[str, Any], str, dict[str, Any], str]] = [
    (
        "andre",
        "AndreGraph",
        {"fhir": None, "population": None},
        "start_process",
        {"tenant_id": "amh", "business_key": "PAGTO-amh-1", "route": "auto_route", "flow": "pagto_dossier"},
        "SP-OP-PAGTO-001",
    ),
    (
        "rafael",
        "RafaelGraph",
        {"fhir": None},
        "start_process",
        {"tenant_id": "amh", "business_key": "AUTH-amh-1", "route": "auto_approve", "guia_id": "G1"},
        "SP-OP-AUTH-001",
    ),
    (
        "helena",
        "HelenaGraph",
        {"whatsapp": _FakeWhatsApp()},
        "escalate",
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hash",
            "beneficiario_pseudo_id": "p1",
            "canal": "whatsapp",
            "escalation_motivo": "duvida_geral",
            "escalation_severidade": "leve",
        },
        "SP-OP-ESCALATION-001",
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
    ),
    (
        "carolina",
        "CarolinaGraph",
        {"fhir": None},
        "start_process",
        {"tenant_id": "amh", "business_key": "CRED-amh-1", "route": "auto_route"},
        "SP-OP-CRED-001",
    ),
    (
        "fernando",
        "FernandoGraph",
        {"whatsapp": _FakeWhatsApp()},
        "start_process",
        {"tenant_id": "amh", "business_key": "INAD-amh-1", "route": "escalate"},
        "SP-OP-INADIMPLENCIA-001",
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
    ),
    (
        "lucas",
        "LucasGraph",
        {"whatsapp": _FakeWhatsApp()},
        "start_process",
        {"tenant_id": "amh", "business_key": "ESC-amh-1", "route": "escalate_human"},
        "SP-OP-ESCALATION-001",
    ),
    (
        "valentina",
        "ValentinaGraph",
        {"fhir": None},
        "start_process",
        {"tenant_id": "amh", "business_key": "PROG-amh-1", "route": "auto_route", "programa": "cronicos"},
        "SP-OP-PROGRAMA-001",
    ),
]


def _graph_class(agent_id: str, class_name: str) -> Any:
    return getattr(importlib.import_module(f"maezo.agents.{agent_id}.graph"), class_name)


@pytest.mark.parametrize(("agent_id", "class_name", "extra", "node", "state", "process_key"), _CASES)
async def test_every_agent_start_emits_phi_safe_provenance_before_effect(
    agent_id: str, class_name: str, extra: dict[str, Any], node: str, state: dict[str, Any], process_key: str
) -> None:
    events: list[str] = []

    class _OrderingTransport(FakeCibSevenTransport):
        async def start_process_instance(self, *a: Any, **k: Any) -> ProcessInstance:
            events.append("start")
            return await super().start_process_instance(*a, **k)

    class _OrderingSink(FakeStartAuditSink):
        # B-3: spy on `emit_once_status` — the method the chokepoint actually calls now.
        async def emit_once_status(self, record: Any, *, dedup_key: str) -> EmitOnceOutcome:
            events.append("emit")
            return await super().emit_once_status(record, dedup_key=dedup_key)

    sink = _OrderingSink()
    graph = _graph_class(agent_id, class_name)(
        inference=_FakeInference(), dmn=_FakeDmn(), cibseven=_OrderingTransport(), audit_sink=sink, **extra
    )

    await getattr(graph, node)(dict(state))

    # Emitted exactly once, BEFORE the engine start effect.
    assert len(sink.calls) == 1, f"{agent_id}: expected exactly one audit emit"
    assert events == ["emit", "start"], f"{agent_id}: audit must precede the engine effect"

    record, dedup_key = sink.calls[0]
    assert record.agent_id == agent_id
    assert record.action == f"start_process:{process_key}"
    assert record.decision == "START_PROCESS"
    assert record.tenant_id == "amh"
    assert record.agent_version == f"{agent_id}@v0"
    assert record.model_id == "claude-contract-probe"  # threaded from InferenceProvider.model_id
    assert record.prompt_version  # each agent supplies its SYSTEM_PROMPT_VERSION
    # Design dedup-key shape: f"{tenant}:start:{process_key}:{business_key}".
    assert dedup_key.startswith(f"amh:start:{process_key}:")
    # PHI discipline: raw inputs bound only by a one-way hash; no PHI class token leaks in the clear.
    assert "input_sha256" in record.details
    assert REDACTED_PHI not in {k for k in record.details}  # redaction produces values, not keys


# ---------------------------------------------------------------------------
# Structural (AST) — drift-proof over ALL present + future start sites
# ---------------------------------------------------------------------------

_AGENT_IDS = [c[0] for c in _CASES]


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_every_start_call_passes_the_fence_args(agent_id: str) -> None:
    """Every `start_process_idempotent(...)` in the agent graph passes `audit_sink=` + `provenance=`."""
    src = Path(importlib.import_module(f"maezo.agents.{agent_id}.graph").__file__).read_text()
    tree = ast.parse(src)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "start_process_idempotent")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "start_process_idempotent")
        )
    ]
    assert calls, f"{agent_id}: expected at least one start_process_idempotent call"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "audit_sink" in kwargs, f"{agent_id}: a start call omits audit_sink (fence bypass!)"
        assert "provenance" in kwargs, f"{agent_id}: a start call omits provenance (fence bypass!)"


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_build_fails_closed_without_audit_sink(agent_id: str) -> None:
    build = importlib.import_module(f"maezo.agents.{agent_id}.graph").build
    with pytest.raises(ValueError, match="audit_sink"):
        build(
            {
                "inference": _FakeInference(),
                "dmn": _FakeDmn(),
                "cibseven": FakeCibSevenTransport(),
                "whatsapp": _FakeWhatsApp(),
            }
        )
