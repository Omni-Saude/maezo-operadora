"""R-090 / F-099: queue labels are derived from BPMN names, never hand-written.

The portal queue shows the `name` the process designer gave each bound
process/userTask instead of the technical key. These tests pin that the checked-in
browser artifact is exactly the generator output and covers every Q1 binding.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from maezo.portal.contracts import models

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "src/maezo/portal/web"
SCRIPT = WEB / "scripts/generate-task-labels.py"
ARTIFACT = WEB / "src/taskLabels.ts"


def test_artifact_matches_generator() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_every_binding_has_a_label() -> None:
    text = ARTIFACT.read_text(encoding="utf-8")
    for process, task in models._BINDINGS:
        assert f'"process": "{process}",\n    "task": "{task}",' in text, (process, task)


def test_labels_come_from_bpmn_names() -> None:
    text = ARTIFACT.read_text(encoding="utf-8")
    bpmn = (ROOT / "spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn").read_text(encoding="utf-8")
    assert 'name="Analise do medico auditor"' in bpmn
    assert '"taskLabel": "Analise do medico auditor"' in text
    assert '"processLabel": "Autorizacao Previa"' in text
    # Same task id in two processes keeps each process's own name.
    assert '"taskLabel": "Decidir destino da guia com pendencia expirada"' in text
    assert '"taskLabel": "Decidir destino da solicitacao com pendencia expirada"' in text
