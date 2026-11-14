"""R-061 (decisao do dono 2026-10-31, #1084: "autorizado PR para injetar DATABASE_URL no bridge").

`notifications_bridge.build_bridge` recusa subir sem `DATABASE_URL` (sink de auditoria duravel,
fail-closed ADR-0007), mas `templates/deployment-bridge.yaml` nao injetava a variavel — o pod
entraria em CrashLoop no cluster independentemente de rede (achado fora-de-escopo do PR #1098).

Prova, no chart REAL (`helm template`, overlays staging e amh):
- `Deployment/notifications-bridge` recebe `DATABASE_URL` por `secretKeyRef` — nunca `value`
  literal — apontando para o MESMO ExternalSecret Aurora (`aurora.secretName` do overlay) e a
  MESMA chave (`database-url`) que o a2a-outbox-relay ja' usa (espelho exato);
- o codigo da ponte de fato le essa variavel (alias `DATABASE_URL` nas settings) e falha fechado
  sem ela — a injecao nao e' cosmetica.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant"
_OVERLAYS: tuple[str, ...] = ("values-staging.yaml", "values-amh.yaml")
_BRIDGE_SOURCE = _REPO_ROOT / "src" / "maezo" / "platform" / "integrations" / "notifications_bridge.py"


def _render(overlay: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["helm", "template", "test-release", str(_CHART_DIR), "-f", str(_CHART_DIR / overlay)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"helm template failed: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc is not None]


def _env(docs: list[dict[str, Any]], deployment: str) -> dict[str, dict[str, Any]]:
    for d in docs:
        if d.get("kind") == "Deployment" and d["metadata"]["name"] == deployment:
            (container,) = d["spec"]["template"]["spec"]["containers"]
            return {e["name"]: e for e in container.get("env", [])}
    raise AssertionError(f"Deployment/{deployment} not rendered")


@pytest.mark.parametrize("overlay", _OVERLAYS)
def test_bridge_gets_database_url_from_the_aurora_secret_never_a_literal(overlay: str) -> None:
    overlay_values = yaml.safe_load((_CHART_DIR / overlay).read_text(encoding="utf-8"))
    env = _env(_render(overlay), "notifications-bridge")
    assert "DATABASE_URL" in env, "notifications-bridge sem DATABASE_URL: build_bridge recusa subir"
    entry = env["DATABASE_URL"]
    assert "value" not in entry, "DATABASE_URL literal no chart (segredo em texto claro)"
    ref = entry["valueFrom"]["secretKeyRef"]
    assert ref == {"name": overlay_values["aurora"]["secretName"], "key": "database-url"}


@pytest.mark.parametrize("overlay", _OVERLAYS)
def test_bridge_database_url_mirrors_the_outbox_relay_exactly(overlay: str) -> None:
    docs = _render(overlay)
    bridge = _env(docs, "notifications-bridge")["DATABASE_URL"]
    relay = _env(docs, "a2a-outbox-relay")["DATABASE_URL"]
    assert bridge == relay


def test_bridge_settings_read_database_url_and_fail_closed_without_it() -> None:
    source = _BRIDGE_SOURCE.read_text(encoding="utf-8")
    assert 'alias="DATABASE_URL"' in source
    assert "DATABASE_URL is required" in source
