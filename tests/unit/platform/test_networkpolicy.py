"""Unit tests for PHI egress NetworkPolicy enforcement (ADR-0017).

TDD London School: verifies that the rendered helm chart never allows
wildcard egress (0.0.0.0/0) for PHI-zone agents — fail-closed per ADR-0017.

Tests use `helm template` to render the chart and validate the output.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/unit/platform -> repo root
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant"
_VALUES_AMH = _CHART_DIR / "values-amh.yaml"


def _parse_helm_template_output(output: str) -> list[dict[str, Any]]:
    """Parse multi-document YAML output from helm template."""
    docs: list[dict[str, Any]] = []
    for doc in yaml.safe_load_all(output):
        if doc is not None:
            docs.append(doc)
    return docs


def _helm_template(values_file: Path) -> str:
    """Run helm template and return stdout."""
    # Use a temp directory so helm doesn't write anything to the chart dir
    result = subprocess.run(
        [
            "helm",
            "template",
            "test-release",
            str(_CHART_DIR),
            "-f",
            str(values_file),
            "--set",
            "networkPolicy.defaultDenyEgress=true",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"helm template failed: {result.stderr}")
    return result.stdout


def _get_phi_egress_cidrs(docs: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """Extract (doc_name, agent_or_component, cidr) tuples for all PHI egress rules.

    Only examines NetworkPolicy documents with maezo.io/security-zone: phi annotation.
    """
    phi_cidrs: list[tuple[str, str, str]] = []
    for doc in docs:
        if doc.get("kind") != "NetworkPolicy":
            continue

        metadata = doc.get("metadata", {})
        annotations = metadata.get("annotations", {})
        labels = metadata.get("labels", {})

        # Check if this is a PHI-zone NetworkPolicy
        security_zone = annotations.get("maezo.io/security-zone", "")
        if security_zone != "phi":
            # Also check fhir-sync-egress which is PHI-zone but uses a different annotation key
            zone = annotations.get("maezo.io/zone", "")
            if zone != "phi":
                continue

        doc_name = metadata.get("name", "unknown")
        agent = labels.get("maezo.io/agent", "unknown")

        # Examine egress rules
        for egress_rule in doc.get("spec", {}).get("egress", []):
            for to_entry in egress_rule.get("to", []):
                ip_block = to_entry.get("ipBlock", {})
                cidr = str(ip_block.get("cidr", ""))
                if cidr:
                    phi_cidrs.append((doc_name, agent, cidr))

    return phi_cidrs


def test_networkpolicy_no_wildcard_egress_for_phi() -> None:
    """PHI-zone NetworkPolicies must NEVER contain 0.0.0.0/0 cidr (ADR-0017).

    Uses helm template to render the chart with values-amh.yaml and validates
    that no rendered PHI-zone NetworkPolicy has a wildcard egress rule.

    This test may be skipped if helm is not available.
    """
    import shutil

    if not shutil.which("helm"):
        import pytest

        pytest.skip("helm not available")

    if not _VALUES_AMH.exists():
        import pytest

        pytest.skip(f"values file not found: {_VALUES_AMH}")

    output = _helm_template(_VALUES_AMH)
    docs = _parse_helm_template_output(output)

    phi_cidrs = _get_phi_egress_cidrs(docs)

    # Verify we found at least some PHI egress rules
    assert len(phi_cidrs) > 0, (
        "No PHI-zone egress rules found in rendered chart. "
        "Ensure PHI agents (rafael, marina) are enabled and have brResidentEndpoints configured."
    )

    # Verify NO wildcard CIDR exists
    wildcards_found = [
        (name, agent, cidr) for name, agent, cidr in phi_cidrs if cidr in ("0.0.0.0/0", "::/0")
    ]
    assert len(wildcards_found) == 0, (
        f"PHI-zone NetworkPolicies contain wildcard egress (ADR-0017 fail-closed): {wildcards_found}"
    )

    # Verify all PHI egress CIDRs are concrete (non-empty, non-wildcard)
    for doc_name, agent, cidr in phi_cidrs:
        assert cidr, f"Empty CIDR in {doc_name} (agent={agent})"
        assert cidr not in ("0.0.0.0/0", "::/0"), (
            f"Wildcard CIDR {cidr} in {doc_name} (agent={agent}) — ADR-0017 violation"
        )


def test_networkpolicy_phi_agents_have_cidrs() -> None:
    """Verify that enabled PHI agents have at least one concrete egress CIDR
    when brResidentEndpoints are configured.

    Uses helm template to render and validate.
    """
    import shutil

    if not shutil.which("helm"):
        import pytest

        pytest.skip("helm not available")

    if not _VALUES_AMH.exists():
        import pytest

        pytest.skip(f"values file not found: {_VALUES_AMH}")

    output = _helm_template(_VALUES_AMH)
    docs = _parse_helm_template_output(output)

    # Find all PHI agent NetworkPolicies
    phi_agent_cidrs: dict[str, list[str]] = {}
    for doc in docs:
        if doc.get("kind") != "NetworkPolicy":
            continue
        metadata = doc.get("metadata", {})
        annotations = metadata.get("annotations", {})
        labels = metadata.get("labels", {})
        security_zone = annotations.get("maezo.io/security-zone", "")
        if security_zone != "phi":
            continue

        agent = labels.get("maezo.io/agent", "unknown")
        cidrs: list[str] = []
        for egress_rule in doc.get("spec", {}).get("egress", []):
            for to_entry in egress_rule.get("to", []):
                ip_block = to_entry.get("ipBlock", {})
                cidr = str(ip_block.get("cidr", ""))
                if cidr:
                    cidrs.append(cidr)
        phi_agent_cidrs[agent] = cidrs

    # We should have PHI agent NetworkPolicies
    # With values-amh.yaml, rafael and marina are PHI agents and have brResidentEndpoints
    if phi_agent_cidrs:
        for agent, cidrs in phi_agent_cidrs.items():
            assert len(cidrs) > 0, f"PHI agent {agent} has no egress CIDRs configured"


def test_networkpolicy_no_wildcard_egress_direct_parse() -> None:
    """Fast unit test: directly parse the networkpolicy.yaml template and verify
    that the template logic prohibits 0.0.0.0/0 for PHI agents via requirePhiCidr.

    This test doesn't require helm — it just validates the template file itself
    references requirePhiCidr (which fails on 0.0.0.0/0).
    """
    template_path = _CHART_DIR / "templates" / "networkpolicy.yaml"

    if not template_path.exists():
        import pytest

        pytest.skip(f"Template not found: {template_path}")

    content = template_path.read_text()

    # The template must reference the requirePhiCidr helper for PHI endpoints
    assert "requirePhiCidr" in content, (
        "networkpolicy.yaml must use requirePhiCidr helper to validate PHI CIDRs (ADR-0017 fail-closed)"
    )

    # The template must NEVER have an actual egress rule with cidr: 0.0.0.0/0
    # (the string may appear in comments explaining why it's forbidden, which is fine)
    # Check for the dangerous pattern: cidr followed by 0.0.0.0/0 on the same line
    import re

    dangerous_pattern = re.compile(r"cidr:\s*[\"']?0\.0\.0\.0/0[\"']?", re.MULTILINE)
    assert not dangerous_pattern.search(content), (
        "networkpolicy.yaml must not contain cidr: 0.0.0.0/0 as an actual rule (ADR-0017 fail-closed)"
    )

    # The template must document the fail-closed requirement
    assert "fail-closed" in content or "FIX 1" in content, (
        "networkpolicy.yaml must document the fail-closed CIDR policy"
    )
