"""R-061 / F-069 + F-149 — NetworkPolicy default-deny com egress OTel/engine e liveness das pontes.

Renderiza o chart REAL (`helm template`, binario real, mesmo padrao de
`test_notifications_bridge_singleton.py`) para cada overlay que renderiza sozinho hoje
(`values-staging.yaml`, `values-amh.yaml` — o `values.yaml` nu nao renderiza: `aurora.endpoint` e
os CIDRs ficam vazios de proposito) e prova:

- `default-deny-egress` continua renderizado (podSelector vazio, egress vazio);
- `allow-otel-egress` abre SO o collector in-cluster (namespace derivado do host de
  `observability.otel.endpoint`, porta derivada da URL), para todos os pods do namespace, sem
  ipBlock — nunca 0.0.0.0/0;
- o toggle `networkPolicy.otelEgress.enabled=false` remove a policy; `defaultDenyEgress=false`
  nao renderiza nenhuma allow-policy de `podSelector: {}` (uma policy de Egress isolaria todos os
  pods por efeito colateral);
- endpoint OTel fora do DNS de Service sem `namespace` explicito FALHA o render (fail-closed);
- engine in-cluster (`cibseven.inCluster.enabled`) abre egress por seletor so para os consumidores
  do `CIBSEVEN_BASE_URL`;
- nenhuma probe usa `pgrep` (imagem sem procps) e a probe de heartbeat das pontes funciona de fato
  (executada aqui contra arquivo novo/velho/ausente).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant"
_OVERLAYS: tuple[str, ...] = ("values-staging.yaml", "values-amh.yaml")
_BRIDGES: tuple[str, ...] = ("notifications-bridge", "network-change-bridge", "consent-revocation-bridge")
_ALL_BRIDGES_ON: tuple[str, ...] = (
    "--set",
    "networkChangeBridge.enabled=true",
    "--set",
    "consentRevocationBridge.enabled=true",
)


def _run_helm(overlay: str, *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", "test-release", str(_CHART_DIR), "-f", str(_CHART_DIR / overlay), *extra_args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _render(overlay: str, *extra_args: str) -> list[dict[str, Any]]:
    result = _run_helm(overlay, *extra_args)
    if result.returncode != 0:
        raise RuntimeError(f"helm template failed: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc is not None]


def _policies(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {d["metadata"]["name"]: d for d in docs if d.get("kind") == "NetworkPolicy"}


def _deployment(docs: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for d in docs:
        if d.get("kind") == "Deployment" and d["metadata"]["name"] == name:
            return d
    raise AssertionError(f"Deployment/{name} not rendered")


@pytest.mark.parametrize("overlay", _OVERLAYS)
def test_default_deny_egress_is_rendered(overlay: str) -> None:
    deny = _policies(_render(overlay))["default-deny-egress"]
    assert deny["spec"]["podSelector"] == {}
    assert deny["spec"]["policyTypes"] == ["Egress"]
    assert deny["spec"]["egress"] == []


@pytest.mark.parametrize("overlay", _OVERLAYS)
def test_otel_egress_opens_only_the_in_cluster_collector(overlay: str) -> None:
    otel = _policies(_render(overlay))["allow-otel-egress"]
    spec = otel["spec"]
    assert spec["podSelector"] == {}, "every pod (PHI zone included) exports telemetry to the collector"
    assert spec["policyTypes"] == ["Egress"]
    assert spec["egress"] == [
        {
            "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "observability"}}}],
            "ports": [{"port": 4317, "protocol": "TCP"}],
        }
    ]


def test_otel_egress_derives_namespace_and_port_from_the_endpoint() -> None:
    spec = _policies(
        _render(
            "values-amh.yaml",
            "--set",
            "observability.otel.endpoint=http://collector.mon.svc:4318",
            "--set",
            "networkPolicy.otelEgress.podLabels.app=otel",
        )
    )["allow-otel-egress"]["spec"]
    assert spec["egress"] == [
        {
            "to": [
                {
                    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "mon"}},
                    "podSelector": {"matchLabels": {"app": "otel"}},
                }
            ],
            "ports": [{"port": 4318, "protocol": "TCP"}],
        }
    ]


def test_otel_endpoint_outside_service_dns_fails_closed() -> None:
    result = _run_helm("values-amh.yaml", "--set", "observability.otel.endpoint=http://10.0.0.5:4317")
    assert result.returncode != 0
    assert "networkPolicy.otelEgress" in result.stderr


def test_otel_endpoint_without_port_fails_closed() -> None:
    result = _run_helm(
        "values-amh.yaml", "--set", "observability.otel.endpoint=http://otel-collector.observability.svc"
    )
    assert result.returncode != 0
    assert "networkPolicy.otelEgress" in result.stderr


def test_otel_egress_toggle_off_removes_the_policy() -> None:
    policies = _policies(_render("values-amh.yaml", "--set", "networkPolicy.otelEgress.enabled=false"))
    assert "allow-otel-egress" not in policies
    assert "default-deny-egress" in policies


def test_default_deny_off_renders_no_namespace_wide_allow_policy() -> None:
    policies = _policies(
        _render(
            "values-amh.yaml",
            "--set",
            "networkPolicy.defaultDenyEgress=false",
            "--set",
            "cibseven.inCluster.enabled=true",
            "--set",
            "cibseven.inCluster.db.url=jdbc:postgresql://db.example:5432/x",
        )
    )
    for name in ("default-deny-egress", "allow-otel-egress", "allow-cibseven-egress"):
        assert name not in policies


@pytest.mark.parametrize("overlay", _OVERLAYS)
def test_no_egress_rule_is_open_to_the_internet(overlay: str) -> None:
    docs = _render(overlay, *_ALL_BRIDGES_ON)
    for name, policy in _policies(docs).items():
        for rule in policy["spec"].get("egress") or []:
            for peer in rule.get("to") or []:
                cidr = (peer.get("ipBlock") or {}).get("cidr")
                assert cidr not in ("0.0.0.0/0", "::/0"), f"{name} opens {cidr}"


def test_cibseven_egress_absent_while_engine_is_external() -> None:
    assert "allow-cibseven-egress" not in _policies(_render("values-amh.yaml"))


def test_in_cluster_engine_egress_by_selector_for_engine_consumers_only() -> None:
    docs = _render(
        "values-amh.yaml",
        "--set",
        "cibseven.inCluster.enabled=true",
        "--set",
        "cibseven.inCluster.db.url=jdbc:postgresql://db.example:5432/x",
        *_ALL_BRIDGES_ON,
    )
    spec = _policies(docs)["allow-cibseven-egress"]["spec"]
    (expr,) = spec["podSelector"]["matchExpressions"]
    assert expr["key"] == "app.kubernetes.io/component"
    assert expr["operator"] == "In"
    agents = sorted(
        d["metadata"]["labels"]["app.kubernetes.io/component"]
        for d in docs
        if d.get("kind") == "Deployment" and "maezo.io/agent" in d["metadata"].get("labels", {})
    )
    assert agents, "expected agent Deployments in the render"
    assert sorted(expr["values"]) == sorted(["worker-daemon", *_BRIDGES, *agents])
    assert spec["egress"] == [
        {
            "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/component": "cibseven"}}}],
            "ports": [{"port": 8080, "protocol": "TCP"}],
        }
    ]


def test_wr2_daemon_egress_covers_the_kafka_bridges_when_enabled() -> None:
    wr2 = _policies(_render("values-amh.yaml", *_ALL_BRIDGES_ON))["wr2-daemon-egress"]
    (expr,) = wr2["spec"]["podSelector"]["matchExpressions"]
    assert set(_BRIDGES) <= set(expr["values"])


@pytest.mark.parametrize("overlay", _OVERLAYS)
def test_no_probe_uses_pgrep(overlay: str) -> None:
    probes = 0
    for doc in _render(overlay, *_ALL_BRIDGES_ON):
        pod = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
        for container in pod.get("containers") or []:
            for kind in ("livenessProbe", "readinessProbe", "startupProbe"):
                command = ((container.get(kind) or {}).get("exec") or {}).get("command") or []
                probes += bool(command)
                assert not any("pgrep" in part for part in command), (doc["metadata"]["name"], kind)
    assert probes >= len(_BRIDGES)


def _bridge_container(docs: list[dict[str, Any]], name: str) -> dict[str, Any]:
    (container,) = _deployment(docs, name)["spec"]["template"]["spec"]["containers"]
    return container


@pytest.mark.parametrize("bridge", _BRIDGES)
def test_bridge_heartbeat_probe_really_detects_staleness(bridge: str, tmp_path: Path) -> None:
    container = _bridge_container(_render("values-amh.yaml", *_ALL_BRIDGES_ON), bridge)
    env = {e["name"]: e["value"] for e in container["env"] if "value" in e}
    assert env["BRIDGE_HEARTBEAT_PATH"] == f"/tmp/maezo-{bridge}.heartbeat"
    interval = float(env["BRIDGE_HEARTBEAT_INTERVAL_S"])
    command = container["livenessProbe"]["exec"]["command"]
    assert command[:2] == ["python", "-c"]

    heartbeat = tmp_path / "hb"
    probe_env = {
        **os.environ,
        "BRIDGE_HEARTBEAT_PATH": str(heartbeat),
        "BRIDGE_HEARTBEAT_INTERVAL_S": str(interval),
    }

    def probe() -> int:
        return subprocess.run([sys.executable, "-c", command[2]], env=probe_env, timeout=30).returncode

    assert probe() == 1, "missing heartbeat file must fail liveness"
    heartbeat.touch()
    assert probe() == 0, "fresh heartbeat must pass liveness"
    stale = time.time() - max(3.0 * interval, 5.0) - 1
    os.utime(heartbeat, (stale, stale))
    assert probe() == 1, "stale heartbeat must fail liveness"
