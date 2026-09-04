"""Unit tests for the Helm-entrypoint fence (AF-01 / R-001).

Three layers:

1. **Synthetic-tree** (`extract_entrypoints`/`resolve_entrypoints` directly): a throwaway rendered-
   YAML fixture with an injected phantom module proves the gate goes RED without needing `helm` on
   PATH at all — the injected-violation self-check the round's non-negotiables require.
2. **Real-chart, default values**: `render_chart` + the full pipeline against the actual chart
   proves GREEN today (both `networkChangeBridge`/`consentRevocationBridge` now `enabled: false`).
3. **Real-chart, forced-on** (the mutation proof): the SAME real chart, rendered with the two
   flags forced back to `true` via `--set`, proves the gate goes RED — i.e. reverting the
   `values.yaml` half of the AF-01 fix (without also building the two phantom modules) is caught
   mechanically, not just by review.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from scripts.ci.check_helm_entrypoints import (
    EntrypointRef,
    extract_entrypoints,
    render_chart,
    resolve_entrypoints,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_VALUES_PATH = _REPO_ROOT / "deploy/helm/maezo-tenant/values.yaml"


# ---------------------------------------------------------------------------
# 1. Synthetic-tree: the parser + resolver catch a phantom module, in isolation
# ---------------------------------------------------------------------------


def test_extract_entrypoints_finds_a_command_and_attributes_its_source() -> None:
    rendered = (
        "---\n"
        "# Source: maezo-tenant/templates/deployment-bridge.yaml\n"
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: notifications-bridge\n"
        '          command: ["python", "-m", "maezo.platform.integrations.notifications_bridge"]\n'
    )
    refs = extract_entrypoints(rendered)
    assert refs == [
        EntrypointRef(
            module="maezo.platform.integrations.notifications_bridge",
            source_template="maezo-tenant/templates/deployment-bridge.yaml",
        )
    ]


def test_extract_entrypoints_handles_multiple_manifests_and_extra_args() -> None:
    """cronjob-lifecycle.yaml's shape: a trailing subcommand arg after the module."""
    rendered = (
        "---\n"
        "# Source: maezo-tenant/templates/deployment-bridge-netchange.yaml\n"
        'command: ["python", "-m", "maezo.platform.integrations.network_change_bridge"]\n'
        "---\n"
        "# Source: maezo-tenant/templates/cronjob-lifecycle.yaml\n"
        'command: ["python", "-m", "maezo.platform.lifecycle", "expurgo-working"]\n'
    )
    refs = extract_entrypoints(rendered)
    assert [r.module for r in refs] == [
        "maezo.platform.integrations.network_change_bridge",
        "maezo.platform.lifecycle",
    ]
    assert refs[0].source_template == "maezo-tenant/templates/deployment-bridge-netchange.yaml"
    assert refs[1].source_template == "maezo-tenant/templates/cronjob-lifecycle.yaml"


def test_resolve_entrypoints_goes_red_on_a_phantom_module() -> None:
    """(a) — the injected-violation self-check: a module that does not exist under `maezo.` fails,
    naming the offending template."""
    refs = [
        EntrypointRef(
            module="maezo.platform.integrations.this_module_does_not_exist_af01",
            source_template="maezo-tenant/templates/deployment-bridge-phantom.yaml",
        )
    ]
    result = resolve_entrypoints(refs)
    assert not result.ok
    assert len(result.missing) == 1
    assert result.missing[0].module == "maezo.platform.integrations.this_module_does_not_exist_af01"
    assert "PHANTOM MODULE" in result.render()
    assert "deployment-bridge-phantom.yaml" in result.render()


def test_resolve_entrypoints_goes_green_on_real_modules() -> None:
    refs = [
        EntrypointRef(module="maezo.platform.integrations.notifications_bridge", source_template=None),
        EntrypointRef(module="maezo.runtime.worker_runtime", source_template=None),
        EntrypointRef(module="maezo.a2a.outbox_relay", source_template=None),
    ]
    result = resolve_entrypoints(refs)
    assert result.ok, result.render()
    assert len(result.missing) == 0
    assert len(result.errored) == 0


def test_resolve_entrypoints_errors_on_a_nonexistent_parent_package() -> None:
    """A phantom PARENT segment (not just leaf module) is a hard failure too (`errored`, not a
    silent skip) — `find_spec` raises `ModuleNotFoundError` importing the missing parent."""
    refs = [EntrypointRef(module="maezo.this_package_does_not_exist_af01.submodule", source_template=None)]
    result = resolve_entrypoints(refs)
    assert not result.ok
    assert len(result.errored) == 1


# ---------------------------------------------------------------------------
# 2. Real chart, default values — GREEN today (b)
# ---------------------------------------------------------------------------


def test_real_chart_default_values_is_green() -> None:
    rendered = render_chart()
    refs = extract_entrypoints(rendered)
    assert len(refs) > 0, "sanity: the render must contain at least one python -m maezo.* command"
    result = resolve_entrypoints(refs)
    assert result.ok, result.render()


def test_real_chart_default_render_does_not_include_the_two_disabled_bridges() -> None:
    """Non-vacuity for the GREEN result above: prove it is green BECAUSE the two bridges are
    disabled by default, not because the scan silently found nothing relevant."""
    rendered = render_chart()
    refs = extract_entrypoints(rendered)
    modules = {r.module for r in refs}
    assert "maezo.platform.integrations.network_change_bridge" not in modules
    assert "maezo.platform.integrations.consent_revocation_bridge" not in modules
    # But the chart DOES still render other real entrypoints (sanity the render isn't broken).
    assert "maezo.platform.integrations.notifications_bridge" in modules


# ---------------------------------------------------------------------------
# 3. Real chart, forced on — RED (the mutation / revert proof)
# ---------------------------------------------------------------------------


def test_real_chart_forced_bridges_on_is_red_the_revert_proof() -> None:
    """If someone reverts AF-01's `values.yaml` half (flips the two bridges back to `enabled:
    true`) without also building the two modules, this is the test that catches it: render the
    REAL chart with the flags forced on and confirm the gate goes RED naming BOTH phantom modules."""
    rendered = render_chart(
        set_overrides=[
            "networkChangeBridge.enabled=true",
            "consentRevocationBridge.enabled=true",
        ]
    )
    refs = extract_entrypoints(rendered)
    result = resolve_entrypoints(refs)
    assert not result.ok
    missing_modules = {r.module for r in result.missing}
    assert missing_modules == {
        "maezo.platform.integrations.network_change_bridge",
        "maezo.platform.integrations.consent_revocation_bridge",
    }


# ---------------------------------------------------------------------------
# (c) the two bridges are `enabled: false` in values.yaml
# ---------------------------------------------------------------------------


def test_bridges_are_disabled_by_default_in_values_yaml() -> None:
    values = yaml.safe_load(_VALUES_PATH.read_text(encoding="utf-8"))
    assert values["networkChangeBridge"]["enabled"] is False
    assert values["consentRevocationBridge"]["enabled"] is False
