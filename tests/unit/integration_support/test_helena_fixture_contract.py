"""Cerca da configuracao da prova Helena; ADR-0030 e contrato ESC HEL-04.

Inspeciona o wiring do teste, sem substituir transporte ou motor. A execucao
real permanece obrigatoria na lane integration.
"""

from __future__ import annotations

import ast
from pathlib import Path

from maezo.tools.workers.escalation import ESCALATION_BPMN_ERROR_ALLOWLIST

_ROOT = Path(__file__).resolve().parents[3]


def test_helena_probe_uses_production_escalation_boundary_allowlist() -> None:
    tree = ast.parse((_ROOT / "tests/integration/agents/test_helena_escalation.py").read_text())
    fixture = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_escalation_worker_probe"
    )
    calls = [
        node
        for node in ast.walk(fixture)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "WorkerHarness"
    ]
    assert len(calls) == 1
    allowlist = next((kw.value for kw in calls[0].keywords if kw.arg == "bpmn_error_allowlist"), None)
    assert isinstance(allowlist, ast.Name)
    assert allowlist.id == "ESCALATION_BPMN_ERROR_ALLOWLIST"
    assert frozenset({"ERR_ESC_NOTIFY_FAILED"}) == ESCALATION_BPMN_ERROR_ALLOWLIST
