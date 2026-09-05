"""SC-06 / R-109 (OWNER-DECISIONS-REGISTER, APROVADO-APOS-REVISAO-HUMANA, opcao A): the two
declared "fan-in" ceilings in `deploy/helm/maezo-tenant/values.yaml` (`throughputCeilings.
transportFanIn` / `.harnessFanIn`) must stay RECONFIRMED against the real import graph, not merely
copied from `docs/audits/maezo-deep-audit/recon/as-built-map.md` (which the R-109 agent_prep_task
explicitly distrusted — "nao localizadas na revisao da linha").

TREE-DERIVED IN BOTH DIRECTIONS (REANCHOR-A2-HELM-CAPACITY, 2026-09-05): the first version of this
test pinned the reconfirmed counts as literals (`== 29`, `== 19`). That is exactly the failure mode
it was meant to prevent — main grew three new agent delegation modules
(`agents/{gustavo,marina,valentina}/delegation.py`) that genuinely `import
maezo.tools.mcp_cibseven.transport`, moving the real transport fan-in from 29 to 32, and the
literal-pinned test went RED for the right reason but demanded a hand-edited number instead of
re-deriving it. The two tests below instead compare TWO independently derived quantities —
`values.yaml`'s declared value and this module's own AST re-scan of `src/` — so they go RED in
EITHER direction: `values.yaml` drifts stale (declared value != tree), or the reverse (someone
hand-edits `values.yaml` ahead of the tree). Neither test embeds a count of its own.
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
    which is exactly the gap between the audit report's original 30/32 and this scan's real
    counts)."""
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


def test_transport_fan_in_in_values_matches_the_tree() -> None:
    """Goes RED whether `values.yaml` falls behind the tree OR gets hand-edited ahead of it —
    neither side of the comparison is a literal."""
    tree_count = len(_real_import_fan_in(_TRANSPORT_MODULE))
    declared = _values()["throughputCeilings"]["transportFanIn"]
    assert declared == tree_count, (
        f"values.yaml throughputCeilings.transportFanIn={declared} but the real import graph "
        f"has {tree_count} importer(s) of {_TRANSPORT_MODULE} — recompute the value AND the "
        "comment block above it (deploy/helm/maezo-tenant/values.yaml)"
    )


def test_harness_fan_in_in_values_matches_the_tree() -> None:
    """Same both-directions comparison as above, for the harness fan-in."""
    tree_count = len(_real_import_fan_in(_HARNESS_MODULE))
    declared = _values()["throughputCeilings"]["harnessFanIn"]
    assert declared == tree_count, (
        f"values.yaml throughputCeilings.harnessFanIn={declared} but the real import graph "
        f"has {tree_count} importer(s) of {_HARNESS_MODULE} — recompute the value AND the "
        "comment block above it (deploy/helm/maezo-tenant/values.yaml)"
    )


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
    envs with the exact values `values.yaml` declares (read from `values.yaml`, never a literal,
    so this stays correct as the declared numbers move)."""
    ceilings = _values()["throughputCeilings"]
    docs = _helm_template()
    container = _container(docs, "worker-daemon")
    env_by_name = {e["name"]: e["value"] for e in container["env"] if "value" in e}
    assert env_by_name["MAEZO_TRANSPORT_FAN_IN_CEILING"] == str(ceilings["transportFanIn"])
    assert env_by_name["MAEZO_HARNESS_FAN_IN_CEILING"] == str(ceilings["harnessFanIn"])


def test_agent_runtime_gets_the_transport_ceiling_but_not_harness() -> None:
    """Agent-runtime never runs the WorkerHarness — it should get `transportFanIn` only."""
    ceilings = _values()["throughputCeilings"]
    docs = _helm_template()
    for agent_name in ("helena", "rafael", "marina"):
        container = _container(docs, f"agent-{agent_name}")
        env_by_name = {e["name"]: e.get("value") for e in container["env"]}
        assert env_by_name.get("MAEZO_TRANSPORT_FAN_IN_CEILING") == str(ceilings["transportFanIn"])
        assert "MAEZO_HARNESS_FAN_IN_CEILING" not in env_by_name


def test_both_settings_classes_genuinely_read_the_env_names() -> None:
    """The env vars the chart declares are actually READ by the two `BaseSettings` classes that
    surface them in the start-up log — not merely declared in the chart with no consumer. Uses
    arbitrary probe values (settings parsing is generic over any int); the real declared/tree
    values are covered by the two tests above."""
    from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    worker_settings = WorkerRuntimeSettings(
        MAEZO_TRANSPORT_FAN_IN_CEILING="32", MAEZO_HARNESS_FAN_IN_CEILING="19"
    )
    assert worker_settings.transport_fan_in_ceiling == 32
    assert worker_settings.harness_fan_in_ceiling == 19

    agent_settings = AgentRuntimeSettings(MAEZO_TRANSPORT_FAN_IN_CEILING="32")
    assert agent_settings.transport_fan_in_ceiling == 32


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
