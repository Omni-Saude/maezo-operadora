"""Evaluate shipped HCL locals and model expressions, without AWS or a Terraform plan.

PR358 F2: Helena follows DPA admission independently of the narrative zone. These
are configuration proofs; synthetic ratification here never changes the real policy.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit.deploy._hcl_probe import _match_bracket

ROOT = Path(__file__).resolve().parents[3]
TF = ROOT / "deploy/aws-ecs/envs/dev-sa-east-1"


def _model_expression(source: str) -> str:
    matches = re.findall(r'name = "MAEZO_INFERENCE_MODEL", value = (.*?) \}', source)
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("zone", ["phi", "synthetic", "ratified"])
@pytest.mark.parametrize("dpa", ["", " \t ", "test-only-operator-reference"])
@pytest.mark.parametrize("model", ["", "test-only-explicit-model"])
@pytest.mark.parametrize("provider", ["bedrock_br", "br_resident", "phi_zone_mock"])
def test_actual_hcl_provider_model_and_receiver_parity(
    tmp_path: Path, zone: str, dpa: str, provider: str, model: str
) -> None:
    terraform = shutil.which("terraform")
    if terraform is None:
        pytest.skip("Terraform CLI required for real HCL evaluation (no substitute evaluator)")
    agent = (TF / "service-agents.tf").read_text()
    receiver = (TF / "service-webhook-receiver.tf").read_text()
    blocks = []
    for match in re.finditer(r"^locals\s*\{", agent, re.MULTILINE):
        opening = agent.index("{", match.start())
        blocks.append(agent[match.start() : _match_bracket(agent, opening) + 1])
    graph = ROOT / "src/maezo/agents/rafael/graph.py"
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "ratificado": zone == "ratified",
                "zona_declarada": "GERAL",
                "dpo_review": "APPROVED",
                "medico_auditor_review": "APPROVED",
                "graph_sha256": hashlib.sha256(graph.read_bytes()).hexdigest(),
            }
        )
    )
    config = "\n".join(blocks)
    config = config.replace(
        '"${path.module}/../../../../spec/policies/phi/dossier-narrative-zone.yaml"', json.dumps(str(policy))
    )
    config = config.replace(
        '"${path.module}/../../../../src/maezo/agents/rafael/graph.py"', json.dumps(str(graph))
    )
    values = {
        "caso_sintetico_zona_geral": zone == "synthetic",
        "inference_provider": "bedrock",
        "phi_zone_provider": provider,
        "phi_vendor_dpa_ref": dpa,
        "phi_model_id": model,
    }
    for name, value in values.items():
        config += f'\nvariable "{name}" {{ default = {json.dumps(value)} }}\n'
    fields = []
    for name in ("helena", "rafael", "marina"):
        expr = _model_expression(agent).replace("each.value.provider", f"local.agentes.{name}.provider")
        fields.append(
            f"{name} = {{ provider = local.agentes.{name}.provider, model = {expr}, "
            f"required = local.agentes.{name}.phi_zone_required }}"
        )
    fields.append(f"receiver_model = {_model_expression(receiver)}")
    config += "\nlocals { probe = { " + ", ".join(fields) + " } }\n"
    (tmp_path / "main.tf").write_text(config)
    result = subprocess.run(
        [terraform, f"-chdir={tmp_path}", "console", "-no-color"],
        input="jsonencode(local.probe)\n",
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(json.loads(result.stdout))
    helena_provider = provider if dpa.strip() else "bedrock"
    assert actual["helena"]["provider"] == helena_provider
    assert actual["helena"]["required"] == bool(dpa.strip())
    expected_model = model if helena_provider in {"bedrock_br", "br_resident"} else ""
    assert actual["helena"]["model"] == expected_model
    assert actual["receiver_model"] == expected_model
    for name in ("rafael", "marina"):
        expected_provider = provider if zone == "phi" else "bedrock"
        assert actual[name]["provider"] == expected_provider
        assert actual[name]["required"] == (zone == "phi")
        assert actual[name]["model"] == (model if expected_provider in {"bedrock_br", "br_resident"} else "")
