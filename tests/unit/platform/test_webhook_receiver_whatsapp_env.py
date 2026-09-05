"""WHATSAPP-ENV-PREFIX-b / R-061 (OWNER-DECISIONS-REGISTER): `deployment-webhook-receiver.yaml`
injects `WHATSAPP_PHONE_NUMBER_ID` from the non-secret `values.yaml` key `whatsapp.phoneNumberId`
— proven against the REAL rendered chart, not a template-string inspection.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant"
_VALUES_AMH = _CHART_DIR / "values-amh.yaml"


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


def _webhook_receiver_container(docs: list[dict[str, Any]]) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") == "Deployment" and doc.get("metadata", {}).get("name") == "webhook-receiver":
            return doc["spec"]["template"]["spec"]["containers"][0]
    raise AssertionError("Deployment/webhook-receiver not found in rendered output")


def test_default_render_injects_an_empty_phone_number_id() -> None:
    """Default `values.yaml` (`whatsapp.phoneNumberId: ""`) renders a literal, present env var —
    never omitted — so `WhatsAppSettings`'s own fail-closed refusal is what governs behavior, not
    an absent key that could silently fall back to something else."""
    docs = _helm_template()
    container = _webhook_receiver_container(docs)
    env_by_name = {e["name"]: e for e in container["env"]}
    assert "WHATSAPP_PHONE_NUMBER_ID" in env_by_name
    entry = env_by_name["WHATSAPP_PHONE_NUMBER_ID"]
    assert "valueFrom" not in entry, "phoneNumberId is NOT a secret — must be a plain `value:`"
    assert entry["value"] == ""


def test_a_per_tenant_override_flows_through_to_the_env_var(tmp_path: Path) -> None:
    """RED proof: if the template stopped reading `.Values.whatsapp.phoneNumberId` (e.g. reverted
    to a hardcoded `""`), this override would not show up in the rendered env — it would."""
    override = tmp_path / "override.yaml"
    override.write_text('whatsapp:\n  phoneNumberId: "1234567890"\n', encoding="utf-8")
    docs = _helm_template("-f", str(override))
    container = _webhook_receiver_container(docs)
    env_by_name = {e["name"]: e for e in container["env"]}
    assert env_by_name["WHATSAPP_PHONE_NUMBER_ID"]["value"] == "1234567890"


def test_the_three_credential_envs_still_come_from_the_secret_not_a_plain_value() -> None:
    """Non-regression: the new plain-value env must not have blurred the line between the
    non-secret id and the three real WABA credentials, which stay `valueFrom.secretKeyRef`."""
    docs = _helm_template()
    container = _webhook_receiver_container(docs)
    env_by_name = {e["name"]: e for e in container["env"]}
    for name in ("WHATSAPP_TOKEN", "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN"):
        assert "valueFrom" in env_by_name[name], f"{name} must stay a secretKeyRef"
        assert env_by_name[name]["valueFrom"]["secretKeyRef"]["name"] == "maezo-whatsapp-config"
