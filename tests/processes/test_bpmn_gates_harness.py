"""Cerca do harness de gates BPMN (EP-PROC-04 / E-SF-31) — gates 1-3 locais e o pin da engine.

Registro de origem: DEC-PROC PROC-04, "Testes de guarda: caso negativo por gate em fixture (...) o job
de CI tem de sair com codigo != 0 em cada um; e falha se `deploy/cibseven/Dockerfile:28` perder o
digest `@sha256:`."

O que esta cerca prende (sem rede, sem engine, sem node):

1. gates 1, 2 e 3 (`scripts/bpmn-gates/bpmn_gates.py`) SAEM 1 na fixture negativa de cada um, com o
   codigo do achado que a fixture encena — e nunca 2 (erro de ferramenta nao e' "reprovou");
2. os mesmos gates saem 0 nos arquivos reais dos dois perfis, com a baseline versionada;
3. a baseline so' encolhe: teto por gate/perfil abaixo; entrada sem `origem` reprova;
4. o XSD vendorizado bate com o sha256 registrado em `xsd/PROVENANCE.json`;
5. `deploy/cibseven/Dockerfile` (FROM), `docker-compose.yml` (servico `cibseven`) e o workflow
   `.github/workflows/bpmn-gates.yml` carregam O MESMO digest; o workflow so' usa action pinada por SHA.

Gates 4 e 5 (node) sao provados no passo de autoteste do workflow; aqui nao ha node.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATES_DIR = _REPO_ROOT / "scripts" / "bpmn-gates"
_HARNESS = _GATES_DIR / "bpmn_gates.py"
_FIXTURES = _GATES_DIR / "fixtures"
_BASELINE = _GATES_DIR / "baseline.json"
_DOCKERFILE = _REPO_ROOT / "deploy" / "cibseven" / "Dockerfile"
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "bpmn-gates.yml"

#: O pin da engine (DEC-PROC PROC-04, fatos verificados; Dockerfile desde 19/09/2026).
ENGINE_IMAGE = (
    "cibseven/cibseven:2.1.0@sha256:20a2135bbc634924838b6a067ac27172bb95d68ddec035e01b40d2b9e95a1169"
)

#: Teto da baseline por (gate, perfil), medido em 16/11/2026 no head 211c6ae. So' DESCE: corrigir
#: um achado obriga a remover a entrada (o harness reprova entrada obsoleta) e a baixar o numero.
#: Subir o teto e' a unica forma de aceitar achado novo, e fica visivel no diff desta linha.
BASELINE_CEILING = {
    ("2", "sp-op"): 1,
    ("4", "sp-op"): 14,
    ("3", "corpus-doc"): 1,
    ("2", "corpus-doc"): 11,  # G2-IMPL: corpus CA sem binding (revisao F1; ENGINE-09005 no motor)
}

NEGATIVE_CASES = [
    ("1", "neg_g1_xsd.bpmn", "G1-XSD"),
    ("2", "neg_g2_static.bpmn", "G2-REF"),
    ("3", "neg_g3_idname.bpmn", "G3-IDNAME"),
]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_HARNESS), *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("profile", ["sp-op", "corpus-doc"])
@pytest.mark.parametrize(("gate", "fixture", "code"), NEGATIVE_CASES)
def test_gate_rejects_its_negative_fixture(gate: str, fixture: str, code: str, profile: str) -> None:
    result = _run("--gate", gate, "--profile", profile, str(_FIXTURES / fixture))
    output = result.stdout + result.stderr
    assert result.returncode == 1, (
        f"gate {gate} na {fixture} saiu {result.returncode} (esperado 1):\n{output}"
    )
    assert f"NOVO     [G{gate} {code}]" in output, f"gate {gate} nao reportou {code} em {fixture}:\n{output}"


@pytest.mark.parametrize("profile", ["sp-op", "corpus-doc"])
@pytest.mark.parametrize("gate", ["1", "2", "3"])
def test_gate_passes_the_real_files_against_the_baseline(gate: str, profile: str) -> None:
    result = _run("--gate", gate, "--profile", profile, "--baseline", str(_BASELINE))
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"gate {gate} perfil {profile} saiu {result.returncode}:\n{output}"
    assert "novos=0" in output and "obsoletos=0" in output, output


def test_gates_without_baseline_still_see_the_known_findings() -> None:
    """A baseline nao esconde: sem ela, o achado pre-existente de G2 (ANS-CRON) reprova."""
    result = _run("--gate", "2", "--profile", "sp-op")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn" in result.stdout


def test_touched_file_is_not_exempted_by_the_baseline(tmp_path: Path) -> None:
    """Criterio 2 do PROC-04 ("0 findings nos arquivos tocados"): arquivo tocado perde a isencao."""
    touched = tmp_path / "touched.txt"
    touched.write_text("spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn\n", encoding="utf-8")
    result = _run(
        "--gate", "2", "--profile", "sp-op", "--baseline", str(_BASELINE), "--touched", str(touched)
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "NOVO     [G2 G2-CONV] spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn" in output


def test_baseline_only_shrinks() -> None:
    data = json.loads(_BASELINE.read_text(encoding="utf-8"))
    entries = data["entries"]
    for entry in entries:
        assert entry.get("origem", "").strip(), f"entrada sem origem: {entry}"
    counts = Counter((e["gate"], e["profile"]) for e in entries)
    for key, count in counts.items():
        assert key in BASELINE_CEILING, f"baseline ganhou (gate, perfil) novo {key}: {count} entrada(s)"
        assert count == BASELINE_CEILING[key], (
            f"baseline {key}: {count} entradas != teto {BASELINE_CEILING[key]} — ao remover uma entrada, "
            "baixe o teto no mesmo commit"
        )
    assert set(counts) == set(BASELINE_CEILING)
    keys = [tuple(e[k] for k in ("gate", "profile", "file", "code", "element", "message")) for e in entries]
    assert len(keys) == len(set(keys)), "entrada duplicada na baseline"


def test_vendored_xsd_matches_recorded_sha256() -> None:
    provenance = json.loads((_GATES_DIR / "xsd" / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert provenance["entry"] == "BPMN20.xsd"
    assert set(provenance["files"]) == {"BPMN20.xsd", "Semantic.xsd", "BPMNDI.xsd", "DI.xsd", "DC.xsd"}
    for name, meta in provenance["files"].items():
        assert meta["url"].startswith("https://www.omg.org/spec/BPMN/20100501/"), meta["url"]
        actual = hashlib.sha256((_GATES_DIR / "xsd" / name).read_bytes()).hexdigest()
        assert actual == meta["sha256"], f"{name}: {actual} != {meta['sha256']}"


def test_toolchain_manifest_pins_exact_versions_with_a_lockfile() -> None:
    manifest = json.loads((_GATES_DIR / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((_GATES_DIR / "package-lock.json").read_text(encoding="utf-8"))
    deps = manifest["dependencies"]
    assert set(deps) >= {"bpmnlint", "bpmn-moddle", "bpmn-js"}, deps
    for name, version in deps.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"{name} sem versao exata: {version}"
        locked = lock["packages"][f"node_modules/{name}"]
        assert locked["version"] == version and locked["integrity"].startswith("sha512-"), (name, locked)


def test_dockerfile_from_keeps_the_engine_digest() -> None:
    froms = [
        line.split(None, 1)[1].strip()
        for line in _DOCKERFILE.read_text().splitlines()
        if line.startswith("FROM ")
    ]
    assert froms == [ENGINE_IMAGE], f"FROM de {_DOCKERFILE.relative_to(_REPO_ROOT)}: {froms}"
    assert re.search(r"@sha256:[0-9a-f]{64}$", froms[0])


def test_compose_cibseven_uses_the_same_digest() -> None:
    compose = yaml.safe_load(_COMPOSE.read_text())
    assert compose["services"]["cibseven"]["image"] == ENGINE_IMAGE


def test_workflow_runs_the_pinned_engine_and_pinned_actions() -> None:
    workflow = yaml.safe_load(_WORKFLOW.read_text())
    job = workflow["jobs"]["gates"]
    assert job["services"]["cibseven"]["image"] == ENGINE_IMAGE
    assert workflow["env"]["BPMN_GATES_ENGINE_IMAGE"] == ENGINE_IMAGE
    triggers = workflow.get("on", workflow.get(True))  # PyYAML le `on:` como True
    assert "push" not in triggers
    paths = set(triggers["pull_request"]["paths"])
    assert paths == {
        "spec/processes/**",
        "docs/processes/New_BPMN/**",
        "scripts/bpmn-gates/**",
        ".bpmnlintrc",
        ".github/workflows/bpmn-gates.yml",
    }
    assert workflow["permissions"] == {"contents": "read"}
    for step in job["steps"]:
        assert "continue-on-error" not in step, step
        uses = step.get("uses")
        if uses:
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", uses), f"action nao pinada por SHA: {uses}"
    selftest = next(s for s in job["steps"] if str(s.get("name", "")).startswith("Self-test"))
    for gate in "12345":
        assert f"check {gate} neg_g{gate}_" in selftest["run"], f"autoteste sem o gate {gate}"
    assert '"$rc" -ne 1' in selftest["run"]
