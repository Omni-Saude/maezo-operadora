"""Unit tests for the Helm-entrypoint fence (AF-01 / R-001).

Four layers:

1. **Synthetic-tree** (`extract_entrypoints`/`resolve_entrypoints` directly): a throwaway rendered-
   YAML fixture with an injected phantom module proves the gate goes RED without needing `helm` on
   PATH at all — the injected-violation self-check the round's non-negotiables require.
2. **Real-chart, default values**: `render_chart` + the full pipeline against the actual chart
   proves GREEN today (both `networkChangeBridge`/`consentRevocationBridge` now `enabled: false`).
3. **Real-chart, forced-on** (the mutation proof): the SAME real chart, rendered with the two
   flags forced back to `true` via `--set`, proves the gate goes RED — i.e. reverting the
   `values.yaml` half of the AF-01 fix (without also building the two phantom modules) is caught
   mechanically, not just by review.
4. **Widened coverage** (gatekeeper finding F4): one test per previously-uncovered shape — `sh -c`,
   `bash -c`, `python3`, bare `-m` under `args:`, and `deploy/**/*.tf` container definitions —
   each proving RED on an injected phantom AND GREEN on the corresponding real module, plus the
   vacuity test (zero entrypoints found must fail, never pass).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from scripts.ci.check_helm_entrypoints import (
    EntrypointRef,
    extract_entrypoints,
    extract_entrypoints_from_terraform,
    render_chart,
    resolve_entrypoints,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_VALUES_PATH = _REPO_ROOT / "deploy/helm/maezo-tenant/values.yaml"
_TF_ROOT = _REPO_ROOT / "deploy"


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


def test_real_chart_accepts_the_new_a2a_outbox_relay_entrypoint_sc01() -> None:
    """SC-01/R-004: `deployment-a2a-outbox-relay.yaml` renders by default (`a2aOutboxRelay.enabled:
    true`) invoking `maezo.a2a.outbox_relay` — a REAL module — and the fence must accept it as part
    of the same GREEN result, not merely tolerate it as an untested addition."""
    rendered = render_chart()
    refs = extract_entrypoints(rendered)
    modules = {r.module for r in refs}
    assert "maezo.a2a.outbox_relay" in modules
    result = resolve_entrypoints(refs)
    assert result.ok, result.render()


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


# ---------------------------------------------------------------------------
# 4. Widened coverage (gatekeeper finding F4) — one test per previously-uncovered shape
# ---------------------------------------------------------------------------

# The exact three probes from VERIFY-A1-HELM's F4 finding: before this fix, all three extracted
# ZERO entrypoints (`_ENTRYPOINT_RE` required the literal quoted `"python", "-m", "maezo…"` triple).


def test_extracts_sh_c_shell_form_and_catches_a_phantom_there() -> None:
    rendered = (
        "---\n"
        "# Source: maezo-tenant/templates/deployment-phantom-shellform.yaml\n"
        "command: [\"sh\", \"-c\", \"set -eu\\nexec python -m "
        'maezo.platform.integrations.totally_phantom_shellform"]\n'
    )
    refs = extract_entrypoints(rendered)
    assert [r.module for r in refs] == ["maezo.platform.integrations.totally_phantom_shellform"]
    result = resolve_entrypoints(refs)
    assert not result.ok
    assert result.missing[0].module == "maezo.platform.integrations.totally_phantom_shellform"


def test_extracts_bash_c_shell_form() -> None:
    rendered = 'command: ["bash", "-c", "exec python3 -m maezo.a2a.outbox_relay"]\n'
    refs = extract_entrypoints(rendered)
    assert [r.module for r in refs] == ["maezo.a2a.outbox_relay"]
    result = resolve_entrypoints(refs)
    assert result.ok, result.render()


def test_extracts_python3_quoted_list_form_and_catches_a_phantom_there() -> None:
    rendered = 'command: ["python3", "-m", "maezo.platform.integrations.phantom_python3"]\n'
    refs = extract_entrypoints(rendered)
    assert [r.module for r in refs] == ["maezo.platform.integrations.phantom_python3"]
    result = resolve_entrypoints(refs)
    assert not result.ok
    assert result.missing[0].module == "maezo.platform.integrations.phantom_python3"


def test_extracts_bare_dash_m_under_args_and_catches_a_phantom_there() -> None:
    """`args:` with no interpreter token (an image `ENTRYPOINT python3` supplies it) — a real shape
    a Dockerfile-driven chart can use, distinct from `command:`."""
    rendered = 'args:    ["-m", "maezo.platform.integrations.phantom_args"]\n'
    refs = extract_entrypoints(rendered)
    assert [r.module for r in refs] == ["maezo.platform.integrations.phantom_args"]
    result = resolve_entrypoints(refs)
    assert not result.ok
    assert result.missing[0].module == "maezo.platform.integrations.phantom_args"


def test_the_gatekeepers_exact_f4_probe_all_three_shapes_at_once() -> None:
    """VERIFY-A1-HELM's F4 probe, verbatim: before this fix, all three extracted ZERO entrypoints
    (fence GREEN on three simultaneous phantoms). Now all three are found and all three are RED."""
    rendered = (
        'command: ["sh", "-c", "set -eu\\nexec python -m '
        'maezo.platform.integrations.totally_phantom_shellform"]\n'
        'command: ["python3", "-m", "maezo.platform.integrations.phantom_python3"]\n'
        'args:    ["-m", "maezo.platform.integrations.phantom_args"]\n'
    )
    refs = extract_entrypoints(rendered)
    assert len(refs) == 3
    result = resolve_entrypoints(refs)
    assert not result.ok
    assert len(result.missing) == 3


def test_extracts_from_deploy_tf_and_catches_a_phantom_there(tmp_path: Path) -> None:
    """`deploy/**/*.tf` container definitions (e.g. ECS task definitions) are scanned too — the
    shape THIS SAME PR's own `service-a2a-outbox-relay.tf` uses (`sh -c` + `exec python -m
    maezo.<real module>`), proven here against a synthetic phantom so it doesn't depend on which
    real .tf files exist."""
    tf_dir = tmp_path / "aws-ecs"
    tf_dir.mkdir()
    (tf_dir / "service-phantom.tf").write_text(
        'command = ["sh", "-c", "set -eu\\nexec python -m '
        'maezo.platform.integrations.totally_phantom_terraform"]\n',
        encoding="utf-8",
    )
    refs = extract_entrypoints_from_terraform(tmp_path)
    assert [r.module for r in refs] == ["maezo.platform.integrations.totally_phantom_terraform"]
    assert refs[0].source_template is not None and refs[0].source_template.endswith(
        "service-phantom.tf"
    )
    result = resolve_entrypoints(refs)
    assert not result.ok


def test_real_deploy_tf_tree_has_real_entrypoints_that_resolve() -> None:
    """Non-vacuity: the real `deploy/**/*.tf` sweep genuinely finds entrypoints (SC-01's
    `service-a2a-outbox-relay.tf` among them, in its `sh -c` form) and every one resolves — this is
    what would have caught the original F4 gap, since `deploy/**/*.tf` was not scanned at all."""
    refs = extract_entrypoints_from_terraform(_TF_ROOT)
    modules = {r.module for r in refs}
    assert "maezo.a2a.outbox_relay" in modules, "SC-01's ECS relay entrypoint must be found"
    result = resolve_entrypoints(refs)
    assert result.ok, result.render()


def test_real_chart_and_tf_combined_default_render_is_still_green() -> None:
    """The CLI's own combination (Helm render + `.tf` sweep, as `main()` does it) stays green on
    the real tree — proves the two extraction sources don't conflict or double-fail anything."""
    rendered = render_chart()
    refs = extract_entrypoints(rendered) + extract_entrypoints_from_terraform(_TF_ROOT)
    assert len(refs) > 10, "sanity: both sources contributed entrypoints"
    result = resolve_entrypoints(refs)
    assert result.ok, result.render()


def test_vacuity_zero_entrypoints_found_is_not_ok() -> None:
    """A manifest/TF sweep that extracts ZERO entrypoints must FAIL, never pass vacuously (the
    fence would rather fail loudly on broken extraction than silently stop checking anything)."""
    result = resolve_entrypoints([])
    assert not result.ok
    assert "NO ENTRYPOINTS FOUND" in result.render()


def test_vacuity_does_not_trigger_when_entrypoints_are_found_and_resolved() -> None:
    refs = [EntrypointRef(module="maezo.a2a.outbox_relay", source_template=None)]
    result = resolve_entrypoints(refs)
    assert result.ok, result.render()
    assert "NO ENTRYPOINTS FOUND" not in result.render()
