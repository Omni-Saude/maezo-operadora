"""SC-06 / R-109 (OWNER-DECISIONS-REGISTER, APROVADO-APOS-REVISAO-HUMANA, opcao A): the two
declared "fan-in" ceilings in `deploy/helm/maezo-tenant/values.yaml` (`throughputCeilings.
transportFanIn` / `.harnessFanIn`) must be RECONFIRMED against the real import graph, not merely
copied from `docs/audits/maezo-deep-audit/recon/as-built-map.md` (which the R-109 agent_prep_task
explicitly distrusted — "nao localizadas na revisao da linha" — and which this test's own
recount shows over-counted both: 30/32 by textual mention vs 29/19 by actual `import`).

This test performs the SAME reconfirmation the values.yaml comment cites (AST-based, counting
only real `import`/`from...import` statements resolved against the two target modules) and pins
it against both the declared chart values AND the two runtime `BaseSettings` classes that
surface them. It goes RED the moment either drifts: a new import of either target module changes
the real fan-in, or the chart/settings/log wiring stops matching.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_DIR = _REPO_ROOT / "src" / "maezo"
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant"
_VALUES_AMH = _CHART_DIR / "values-amh.yaml"
_VALUES = _CHART_DIR / "values.yaml"

_TRANSPORT_MODULE = "maezo.tools.mcp_cibseven.transport"
_HARNESS_MODULE = "maezo.tools.workers.harness"


def _resolve_relative_import(module: str | None, level: int, file_path: Path) -> str:
    if level <= 0:
        return module or ""
    parts = file_path.relative_to(_SRC_DIR).parts[:-1]
    package = ["maezo", *parts]
    up = level - 1
    if up:
        package = package[: len(package) - up] if up <= len(package) else []
    base = ".".join(package)
    if module:
        return f"{base}.{module}" if base else module
    return base


def _real_import_fan_in(target_module: str) -> set[str]:
    """Every file under `src/maezo` that genuinely IMPORTS `target_module` (an `import` or
    `from ... import ...` statement resolving to it — never a bare string mention in prose,
    which is exactly the gap between the audit report's 30/32 and this scan's 29/19)."""
    prefix, _, attr = target_module.rpartition(".")
    importers: set[str] = set()
    for path in sorted(_SRC_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == target_module for alias in node.names):
                    importers.add(str(path))
            elif isinstance(node, ast.ImportFrom):
                resolved = _resolve_relative_import(node.module, node.level, path)
                if resolved == target_module or (
                    resolved == prefix and any(alias.name == attr for alias in node.names)
                ):
                    importers.add(str(path))
    return importers


def _values() -> dict[str, Any]:
    return yaml.safe_load(_VALUES.read_text(encoding="utf-8"))


def test_transport_fan_in_reconfirms_to_29_not_the_audit_reports_30() -> None:
    importers = _real_import_fan_in(_TRANSPORT_MODULE)
    assert len(importers) == 29, (
        f"transport fan-in drifted to {len(importers)} — recompute values.yaml's "
        "throughputCeilings.transportFanIn and its comment"
    )


def test_harness_fan_in_reconfirms_to_19_not_the_audit_reports_32() -> None:
    importers = _real_import_fan_in(_HARNESS_MODULE)
    assert len(importers) == 19, (
        f"harness fan-in drifted to {len(importers)} — recompute values.yaml's "
        "throughputCeilings.harnessFanIn and its comment"
    )


def test_values_yaml_declares_the_reconfirmed_numbers() -> None:
    ceilings = _values()["throughputCeilings"]
    assert ceilings["transportFanIn"] == 29
    assert ceilings["harnessFanIn"] == 19


def _helm_template(*extra_args: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["helm", "template", "test-release", str(_CHART_DIR), "-f", str(_VALUES_AMH), *extra_args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"helm template failed: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc is not None]


def _container(docs: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") == "Deployment" and doc.get("metadata", {}).get("name") == name:
            return doc["spec"]["template"]["spec"]["containers"][0]
    raise AssertionError(f"Deployment/{name} not found in rendered output")


def test_worker_daemon_gets_both_ceilings_from_the_declared_values() -> None:
    """RED proof: the keys are not dead — the rendered worker-daemon Deployment must carry BOTH
    envs with the exact values `values.yaml` declares."""
    docs = _helm_template()
    container = _container(docs, "worker-daemon")
    env_by_name = {e["name"]: e["value"] for e in container["env"] if "value" in e}
    assert env_by_name["MAEZO_TRANSPORT_FAN_IN_CEILING"] == "29"
    assert env_by_name["MAEZO_HARNESS_FAN_IN_CEILING"] == "19"


def test_agent_runtime_gets_the_transport_ceiling_but_not_harness() -> None:
    """Agent-runtime never runs the WorkerHarness — it should get `transportFanIn` only."""
    docs = _helm_template()
    for agent_name in ("helena", "rafael", "marina"):
        container = _container(docs, f"agent-{agent_name}")
        env_by_name = {e["name"]: e.get("value") for e in container["env"]}
        assert env_by_name.get("MAEZO_TRANSPORT_FAN_IN_CEILING") == "29"
        assert "MAEZO_HARNESS_FAN_IN_CEILING" not in env_by_name


def test_both_settings_classes_genuinely_read_the_env_names() -> None:
    """The env vars the chart declares are actually READ by the two `BaseSettings` classes that
    surface them in the start-up log — not merely declared in the chart with no consumer."""
    from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    worker_settings = WorkerRuntimeSettings(
        MAEZO_TRANSPORT_FAN_IN_CEILING="29", MAEZO_HARNESS_FAN_IN_CEILING="19"
    )
    assert worker_settings.transport_fan_in_ceiling == 29
    assert worker_settings.harness_fan_in_ceiling == 19

    agent_settings = AgentRuntimeSettings(MAEZO_TRANSPORT_FAN_IN_CEILING="29")
    assert agent_settings.transport_fan_in_ceiling == 29


def test_both_settings_default_to_none_never_fail_closed_when_absent() -> None:
    """Informational metadata must never gate boot — absent envs resolve to `None`, not a
    validation error."""
    from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    worker_settings = WorkerRuntimeSettings()
    assert worker_settings.transport_fan_in_ceiling is None
    assert worker_settings.harness_fan_in_ceiling is None

    agent_settings = AgentRuntimeSettings()
    assert agent_settings.transport_fan_in_ceiling is None
