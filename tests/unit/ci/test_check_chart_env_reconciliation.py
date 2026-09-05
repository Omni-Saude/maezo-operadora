"""Unit tests for the chart/TF <-> src/ env-name reconciliation fence (DU-02 / R-002).

Layers:

1. **Synthetic** (`reconcile` directly): an injected declared-but-unread name — including the
   EXACT shape of the original DU-02 defect (`job-migrations.yaml` declaring `MAEZO_TENANT`) —
   proves the gate goes RED without needing `helm`/`src/` on disk at all.
2. **Real chart + TF + src/**: the full extraction pipeline against the real tree proves GREEN
   today (the rename landed) and proves it is green FOR THE RIGHT REASON — `MAEZO_TENANT_ID` is
   declared and read, `MAEZO_TENANT` is declared nowhere.
3. **Allowlist non-vacuity**: every `INFRA_OWNED_DECLARED`/`DEFERRED_UNRECONCILED_DECLARED` entry
   that the real chart/TF actually declares is exempted (not silently dropped from the count) and
   is genuinely absent from `src/`'s read set — proving the allowlist is load-bearing, not
   accidentally exempting nothing.
4. **Widened scope** (gatekeeper finding F2): the fence is no longer restricted to four hardcoded
   prefixes — an unlisted declared name outside all four legacy prefixes must still go RED when
   unread; and every entry in both exemption tables must carry a non-empty reason, enforced by
   `_require_reasons` at import time and proven directly here.
5. **Context-scoped reads** (gatekeeper finding G3, §Delta): widening the declared-name pattern
   also widened the READ-side bare literal sweep to 700 names, 636 of them plain non-env constants
   (`RATIFICADO`, `PASSED`, `FAILED`, ...) — a chart declaring `- name: PASSED` passed GREEN with
   nothing reading it. `extract_literal_env_names` now counts a literal only when it is the KEY
   argument of a real env-read call, directly or via a same-named module-level constant.
6. **Marker breadth + honest messaging** (gatekeeper findings F3/F4, §Delta): R-101 originally
   marked ONLY `WhatsAppSettings.phone_number_id` `chart_required`, even though `send_message`
   ALSO fails closed on `whatsapp_token` and `verify_webhook` fails closed on
   `whatsapp_verify_token` — the fence stayed green if either of THOSE was ever dropped from the
   chart. Both now carry the marker too (declared inline, not via the opaque `_secret()` factory —
   see `mcp_whatsapp/server.py`). Separately, the RED message used to hardcode "has no default —
   fails closed at boot" for every required name, which is false for a marker-derived one (it DOES
   have a pydantic default and fails closed at CALL time instead); `_required_env_refs` now gives
   each required name an accurate, source-specific reason.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest
from scripts.ci.check_chart_env_reconciliation import (
    _MARKER_REASON,
    _NO_DEFAULT_REASON,
    DEFERRED_UNRECONCILED_DECLARED,
    INFRA_OWNED_DECLARED,
    EnvNameRef,
    _has_chart_required_marker,
    _module_level_string_constants,
    _require_reasons,
    _required_env_refs,
    extract_declared_from_helm,
    extract_declared_from_terraform,
    extract_literal_env_names,
    extract_settings_env_names,
    reconcile,
    render_chart,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_DIR = _REPO_ROOT / "src" / "maezo"
_TF_ROOT = _REPO_ROOT / "deploy"


# ---------------------------------------------------------------------------
# 1. Synthetic: the injected-violation self-check
# ---------------------------------------------------------------------------


def test_reconcile_flags_a_declared_name_nothing_in_src_reads() -> None:
    declared = [EnvNameRef(name="MAEZO_TENANT", source="job-migrations.yaml (Job/x)")]
    result = reconcile(declared, read_names=set(), required=[])
    assert not result.ok
    assert len(result.declared_unread) == 1
    assert result.declared_unread[0].name == "MAEZO_TENANT"
    assert "DECLARED BUT NEVER READ" in result.render()


def test_reconcile_the_exact_original_du02_defect_shape() -> None:
    """`job-migrations.yaml` declared `MAEZO_TENANT`; `env.py` only ever read `MAEZO_TENANT_ID`.
    Reconciling that declared set against that read set must go RED on `MAEZO_TENANT` — this is
    the test that fails if the DU-02 rename (or this gate) is ever reverted."""
    declared = [
        EnvNameRef(name="MAEZO_TENANT", source="job-migrations.yaml (Job/x-migrate)"),
        EnvNameRef(name="KAFKA_BOOTSTRAP_SERVERS", source="deployment-bridge.yaml (Deployment/y)"),
    ]
    read_names = {"MAEZO_TENANT_ID", "KAFKA_BOOTSTRAP_SERVERS"}  # env.py's real read set (renamed)
    result = reconcile(declared, read_names, required=[])
    assert not result.ok
    assert {r.name for r in result.declared_unread} == {"MAEZO_TENANT"}


def test_reconcile_flags_a_required_name_declared_nowhere() -> None:
    required = [EnvNameRef(name="WHATSAPP_APP_SECRET", source="src/ BaseSettings (no default)")]
    result = reconcile(declared=[], read_names=set(), required=required)
    assert not result.ok
    assert len(result.required_undeclared) == 1
    assert "REQUIRED BUT NEVER DECLARED" in result.render()


def test_reconcile_is_green_when_declared_read_and_required_all_line_up() -> None:
    declared = [EnvNameRef(name="MAEZO_TENANT_ID", source="job-migrations.yaml (Job/x)")]
    result = reconcile(declared, read_names={"MAEZO_TENANT_ID"}, required=declared)
    assert result.ok, result.render()


def test_reconcile_exempts_an_allowlisted_infra_owned_name() -> None:
    assert "KAFKA_NODE_ID" in INFRA_OWNED_DECLARED  # sanity: fixture references a real entry
    declared = [EnvNameRef(name="KAFKA_NODE_ID", source="service-kafka.tf")]
    result = reconcile(declared, read_names=set(), required=[])
    assert result.ok, result.render()
    assert len(result.allowlisted) == 1
    assert result.allowlisted[0].name == "KAFKA_NODE_ID"


def test_reconcile_exempts_a_deferred_name_but_keeps_it_visible() -> None:
    """A deferred (not-infra-owned, tracked-follow-up-gap) name does not fail the gate, but is
    reported in its OWN bucket — never silently merged into `allowlisted`, which would misrepresent
    it as infra-owned (the exact "false provenance" shape gatekeeper finding F2 flagged)."""
    assert "ENVIRONMENT" in DEFERRED_UNRECONCILED_DECLARED  # sanity
    declared = [EnvNameRef(name="ENVIRONMENT", source="values-amh.yaml (agents[].env)")]
    result = reconcile(declared, read_names=set(), required=[])
    assert result.ok, result.render()
    assert result.allowlisted == []
    assert len(result.deferred) == 1
    assert result.deferred[0].name == "ENVIRONMENT"


# ---------------------------------------------------------------------------
# 2. Real chart + TF + src/ — GREEN today, for the right reason
# ---------------------------------------------------------------------------


def _reconcile_real_tree():
    rendered = render_chart()
    declared = extract_declared_from_helm(rendered) + extract_declared_from_terraform(_TF_ROOT)
    literal_names = extract_literal_env_names(_SRC_DIR)
    settings_all, settings_required, marker_required, no_default = extract_settings_env_names(_SRC_DIR)
    read_names = literal_names | settings_all
    required_refs = _required_env_refs(settings_required, marker_required, no_default)
    return reconcile(declared, read_names, required_refs)


def test_real_tree_is_green() -> None:
    result = _reconcile_real_tree()
    assert result.ok, result.render()


def test_real_tree_covers_every_declared_name_not_a_four_prefix_subset() -> None:
    """Gatekeeper finding F2: the fence used to reconcile only `MAEZO_`/`WHATSAPP_`/`CIBSEVEN_`/
    `KAFKA_` names — 28 of the real tree's 90 declared names (31%). It must now cover all of them,
    with the rest either read, allowlisted, or deferred (never silently dropped)."""
    result = _reconcile_real_tree()
    assert result.declared_total >= 90, (
        f"real tree suddenly declares fewer names ({result.declared_total}) than the last full "
        "sweep (90) — re-verify the extraction wasn't narrowed"
    )
    accounted = (
        (result.declared_total - len(result.declared_unread))
        if result.declared_unread
        else result.declared_total
    )
    assert accounted == result.declared_total


def test_real_chart_declares_the_renamed_var_and_not_the_old_one() -> None:
    """DU-02's own fix, asserted directly against the real render: `MAEZO_TENANT_ID` (correct) is
    declared; the old `MAEZO_TENANT` (never read anywhere in src/) is declared nowhere."""
    rendered = render_chart()
    declared_names = {ref.name for ref in extract_declared_from_helm(rendered)}
    assert "MAEZO_TENANT_ID" in declared_names
    assert "MAEZO_TENANT" not in declared_names


def test_real_src_required_whatsapp_fields_are_declared_in_the_chart() -> None:
    """Non-vacuity for the required-direction check: `WhatsAppWebhookSettings.app_secret`/
    `.verify_token` really are modeled as required (no default) AND really are declared in
    `deployment-webhook-receiver.yaml` today — proves the reverse direction isn't vacuously green."""
    _, required, _marker, _no_default = extract_settings_env_names(_SRC_DIR)
    assert {"WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN"} <= required
    rendered = render_chart()
    declared_names = {ref.name for ref in extract_declared_from_helm(rendered)}
    assert "WHATSAPP_APP_SECRET" in declared_names
    assert "WHATSAPP_VERIFY_TOKEN" in declared_names


def test_real_src_functionally_required_phone_number_id_is_declared_in_chart() -> None:
    """WHATSAPP-ENV-PREFIX-b / R-101: `WhatsAppSettings.phone_number_id` carries the
    `chart_required` marker (pydantic default `""`, but `send_message` fails closed at call time
    without it) — the marker must make it show up in `required`, AND the chart must actually
    declare `WHATSAPP_PHONE_NUMBER_ID` today. RED proof: revert either half (drop the marker from
    `mcp_whatsapp/server.py`, or drop the env from `deployment-webhook-receiver.yaml`) and this
    test — or, for the chart half, `test_real_tree_is_green` — fails."""
    _, required, marker_required, _no_default = extract_settings_env_names(_SRC_DIR)
    assert "WHATSAPP_PHONE_NUMBER_ID" in required
    assert "WHATSAPP_PHONE_NUMBER_ID" in marker_required
    rendered = render_chart()
    declared_names = {ref.name for ref in extract_declared_from_helm(rendered)}
    assert "WHATSAPP_PHONE_NUMBER_ID" in declared_names


def test_has_chart_required_marker_recognizes_the_marker_generically(tmp_path: Path) -> None:
    """The marker mechanism is data on the field, not a hardcoded field-name allowlist — proven
    against a SYNTHETIC field, independent of `WhatsAppSettings`."""
    source = (
        "from pydantic import Field\nx: str = Field(default='', json_schema_extra={'chart_required': True})\n"
    )
    call_node = ast.parse(source).body[1].value  # the AnnAssign's Field(...) call
    assert isinstance(call_node, ast.Call)
    assert _has_chart_required_marker(call_node) is True


def test_has_chart_required_marker_ignores_a_field_without_it(tmp_path: Path) -> None:
    source = "from pydantic import Field\nx: str = Field(default='', description='not a marker')\n"
    call_node = ast.parse(source).body[1].value
    assert isinstance(call_node, ast.Call)
    assert _has_chart_required_marker(call_node) is False


def test_has_chart_required_marker_ignores_json_schema_extra_without_the_true_flag(
    tmp_path: Path,
) -> None:
    """`json_schema_extra` used for something else entirely (or `chart_required: False`) must not
    be mistaken for the marker."""
    source = (
        "from pydantic import Field\n"
        "x: str = Field(default='', json_schema_extra={'chart_required': False})\n"
    )
    call_node = ast.parse(source).body[1].value
    assert isinstance(call_node, ast.Call)
    assert _has_chart_required_marker(call_node) is False


def test_a_synthetic_chart_required_field_surfaces_as_required(tmp_path: Path) -> None:
    """End-to-end (not just the marker helper): a synthetic `BaseSettings` subclass with a
    `chart_required`-marked field must appear in `extract_settings_env_names`'s required set."""
    (tmp_path / "mod.py").write_text(
        "from pydantic import Field\n"
        "from pydantic_settings import BaseSettings, SettingsConfigDict\n"
        "class SyntheticSettings(BaseSettings):\n"
        "    model_config = SettingsConfigDict(env_prefix='SYNTH_')\n"
        "    something: str = Field(default='', json_schema_extra={'chart_required': True})\n",
        encoding="utf-8",
    )
    _, required, marker_required, _no_default = extract_settings_env_names(tmp_path)
    assert "SYNTH_SOMETHING" in required
    assert "SYNTH_SOMETHING" in marker_required


def test_real_chart_covers_both_new_a2a_outbox_relay_env_names() -> None:
    """SC-01's new Deployment declares two new env names — both must be genuinely read by
    `src/maezo/a2a/outbox_relay.py`'s `OutboxRelaySettings`, not merely declared."""
    rendered = render_chart()
    declared_names = {ref.name for ref in extract_declared_from_helm(rendered)}
    literal_names = extract_literal_env_names(_SRC_DIR)
    settings_all, _, _, _ = extract_settings_env_names(_SRC_DIR)
    read_names = literal_names | settings_all
    for name in ("A2A_OUTBOX_RELAY_BATCH_SIZE", "A2A_OUTBOX_RELAY_POLL_INTERVAL_S"):
        assert name in declared_names, f"{name} not declared by the real chart"
        assert name in read_names, f"{name} declared but not read by src/ — exactly the DU-02 shape"


# ---------------------------------------------------------------------------
# 3. Allowlist non-vacuity
# ---------------------------------------------------------------------------


def _real_declared_names() -> set[str]:
    declared_names = {ref.name for ref in extract_declared_from_terraform(_TF_ROOT)}
    declared_names |= {ref.name for ref in extract_declared_from_helm(render_chart())}
    return declared_names


def test_allowlist_entries_that_are_declared_today_are_genuinely_unread_by_src() -> None:
    """Every allowlisted name the real TF tree actually declares must be ABSENT from src/'s read
    set — otherwise the allowlist entry is dead weight (or worse, hiding a real defect)."""
    declared_names = _real_declared_names()
    literal_names = extract_literal_env_names(_SRC_DIR)
    settings_all, _, _, _ = extract_settings_env_names(_SRC_DIR)
    read_names = literal_names | settings_all

    declared_and_allowlisted = declared_names & set(INFRA_OWNED_DECLARED)
    assert len(declared_and_allowlisted) >= 40, (
        "sanity: the widened allowlist entries must actually surface in the real tree"
    )
    for name in declared_and_allowlisted:
        assert name not in read_names, (
            f"{name} is allowlisted as infra-owned/non-Python but src/ DOES read it — "
            "remove the allowlist entry instead of leaving a stale exemption"
        )


def test_deferred_entries_that_are_declared_today_are_genuinely_unread_by_src() -> None:
    """Same non-vacuity property for `DEFERRED_UNRECONCILED_DECLARED`: both entries must actually
    surface in the real chart AND be genuinely unread — otherwise they are stale or hiding a
    defect that should instead be a real (non-deferred) failure."""
    declared_names = _real_declared_names()
    literal_names = extract_literal_env_names(_SRC_DIR)
    settings_all, _, _, _ = extract_settings_env_names(_SRC_DIR)
    read_names = literal_names | settings_all

    declared_and_deferred = declared_names & set(DEFERRED_UNRECONCILED_DECLARED)
    assert declared_and_deferred == set(DEFERRED_UNRECONCILED_DECLARED)
    for name in declared_and_deferred:
        assert name not in read_names, f"{name} is deferred as unread but src/ DOES read it"


def test_allowlist_has_no_unused_entries_against_the_real_tree() -> None:
    """Every `INFRA_OWNED_DECLARED` entry either surfaces in today's default render/TF sweep, or is
    explicitly documented (its own dict comment) as gated off by default."""
    declared_names = _real_declared_names()
    undeclared_allowlist_entries = set(INFRA_OWNED_DECLARED) - declared_names
    # CIBSEVEN_DATABASE_URL is pinned defensively for a template gated off by default
    # (cibseven.inCluster.enabled=false) — the one legitimate "not declared today" entry.
    assert undeclared_allowlist_entries == {"CIBSEVEN_DATABASE_URL"}


# ---------------------------------------------------------------------------
# 4. Widened scope (gatekeeper finding F2) — no prefix restriction, reasoned exemptions only
# ---------------------------------------------------------------------------


def test_an_unlisted_declared_name_outside_every_legacy_prefix_still_goes_red() -> None:
    """Before F2's fix, a declared name outside `MAEZO_`/`WHATSAPP_`/`CIBSEVEN_`/`KAFKA_` was
    invisible to `_NAME_PATTERN` and could never be flagged, allowlisted, or deferred — it was
    silently excluded from the scan entirely. This is the RED proof that the widened
    `_NAME_PATTERN` now catches such a name — a synthetic one, reconciled against an empty read
    set, so it must be flagged rather than silently passing (it is not in either exemption table)."""
    assert "TOTALLY_UNLISTED_VAR" not in INFRA_OWNED_DECLARED
    assert "TOTALLY_UNLISTED_VAR" not in DEFERRED_UNRECONCILED_DECLARED
    declared = [EnvNameRef(name="TOTALLY_UNLISTED_VAR", source="synthetic")]
    result = reconcile(declared, read_names=set(), required=[])
    assert not result.ok
    assert {r.name for r in result.declared_unread} == {"TOTALLY_UNLISTED_VAR"}


def test_infra_owned_allowlist_has_a_reason_for_every_entry() -> None:
    assert INFRA_OWNED_DECLARED  # sanity: not accidentally emptied
    for name, reason in INFRA_OWNED_DECLARED.items():
        assert reason and reason.strip(), f"{name} has no reason"


def test_deferred_allowlist_has_a_reason_for_every_entry() -> None:
    assert DEFERRED_UNRECONCILED_DECLARED  # sanity: not accidentally emptied
    for name, reason in DEFERRED_UNRECONCILED_DECLARED.items():
        assert reason and reason.strip(), f"{name} has no reason"


def test_require_reasons_rejects_an_empty_reason() -> None:
    """The mutation proof: an allowlist entry with no reason must be rejected loudly (import-time
    `ValueError`), not silently accepted — proven directly against `_require_reasons` rather than
    by mutating the real module-level dicts (which would break every other test in this file)."""
    with pytest.raises(ValueError, match="has no reason"):
        _require_reasons({"SOME_VAR": ""}, label="test-allowlist")


def test_require_reasons_rejects_a_whitespace_only_reason() -> None:
    with pytest.raises(ValueError, match="has no reason"):
        _require_reasons({"SOME_VAR": "   "}, label="test-allowlist")


def test_require_reasons_accepts_a_real_reason() -> None:
    _require_reasons({"SOME_VAR": "a real one-line reason"}, label="test-allowlist")  # no raise


# ---------------------------------------------------------------------------
# 5. Context-scoped reads (gatekeeper finding G3, §Delta)
# ---------------------------------------------------------------------------


def test_a_plain_non_env_constant_is_not_counted_as_read() -> None:
    """The exact §Delta repro: `RATIFICADO`/`PASSED`/`FAILED` are valid UPPER_SNAKE_CASE strings
    that appear as plain status/enum literals somewhere in `src/`, never as the key of a real
    env-read call. Before G3's fix, the bare literal sweep counted them as "read" regardless."""
    read = extract_literal_env_names(_SRC_DIR)
    for name in ("RATIFICADO", "PASSED", "FAILED", "FINAL", "INFO", "WARNING", "ERROR", "POST"):
        assert name not in read, f"{name} is a plain constant, not an env-read key — must not count"


def test_injecting_ratificado_and_passed_as_declared_names_now_goes_red() -> None:
    """End-to-end proof against the real render: declaring two fictitious env names that happen to
    look like ordinary constants must be flagged, not silently pass — this is the exact scenario
    the gatekeeper demonstrated was GREEN before this fix."""
    result = _reconcile_real_tree()
    declared = [
        EnvNameRef(name="RATIFICADO", source="synthetic (gatekeeper G3 repro)"),
        EnvNameRef(name="PASSED", source="synthetic (gatekeeper G3 repro)"),
    ]
    rendered = render_chart()
    real_declared = extract_declared_from_helm(rendered) + extract_declared_from_terraform(_TF_ROOT)
    literal_names = extract_literal_env_names(_SRC_DIR)
    settings_all, settings_required, marker_required, no_default = extract_settings_env_names(_SRC_DIR)
    read_names = literal_names | settings_all
    required_refs = _required_env_refs(settings_required, marker_required, no_default)
    injected = reconcile(real_declared + declared, read_names, required_refs)
    assert not injected.ok
    assert {r.name for r in injected.declared_unread} == {"RATIFICADO", "PASSED"}
    assert result.ok, "sanity: the un-injected real tree must still be green"


def test_every_genuine_declared_name_on_the_real_chart_still_resolves() -> None:
    """The other half of G3's trade-off: tightening recall must not reintroduce false positives on
    the real tree — every one of the 91 genuinely declared names must still be read, allowlisted,
    or deferred (this is `test_real_tree_is_green` restated as an explicit non-regression check
    tied to G3 by name, so a future reviewer sees why it matters)."""
    result = _reconcile_real_tree()
    assert result.ok, result.render()
    assert result.declared_total >= 90


def test_extract_literal_env_names_counts_a_direct_call_argument(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        'import os\nVALUE = os.environ.get("MAEZO_DIRECT_LITERAL_TEST", "x")\n', encoding="utf-8"
    )
    assert "MAEZO_DIRECT_LITERAL_TEST" in extract_literal_env_names(tmp_path)


def test_extract_literal_env_names_counts_a_constant_resolved_by_name(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        'import os\nENV_NAME: str = "MAEZO_VIA_CONSTANT_TEST"\nVALUE = os.environ.get(ENV_NAME, None)\n',
        encoding="utf-8",
    )
    assert "MAEZO_VIA_CONSTANT_TEST" in extract_literal_env_names(tmp_path)


def test_extract_literal_env_names_ignores_a_constant_never_used_as_an_env_key(tmp_path: Path) -> None:
    """The exact G3 shape: a module-level UPPER_SNAKE_CASE constant that is never the key argument
    of any env-read call must NOT be counted, even though it satisfies `_NAME_PATTERN`."""
    (tmp_path / "mod.py").write_text(
        'STATUS_RATIFICADO = "RATIFICADO"\nprint(STATUS_RATIFICADO)\n', encoding="utf-8"
    )
    assert "RATIFICADO" not in extract_literal_env_names(tmp_path)


def test_extract_literal_env_names_covers_os_getenv_and_subscript_forms(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        'import os\nA = os.getenv("MAEZO_GETENV_TEST")\nB = os.environ["MAEZO_SUBSCRIPT_TEST"]\n',
        encoding="utf-8",
    )
    read = extract_literal_env_names(tmp_path)
    assert "MAEZO_GETENV_TEST" in read
    assert "MAEZO_SUBSCRIPT_TEST" in read


def test_module_level_string_constants_resolves_cross_file_by_name() -> None:
    """`ENV_PHI_ENDPOINT_URL: Final[str] = "MAEZO_PHI_ENDPOINT_URL"` really is defined in one file
    (`br_regional.py`) and read via `os.environ.get(ENV_PHI_ENDPOINT_URL, "")` in ANOTHER
    (`br_resident_provider.py`) — the cross-file case this heuristic exists to bridge."""
    constants = _module_level_string_constants(_SRC_DIR)
    assert "MAEZO_PHI_ENDPOINT_URL" in constants.get("ENV_PHI_ENDPOINT_URL", set())
    assert "MAEZO_PHI_ENDPOINT_URL" in extract_literal_env_names(_SRC_DIR)


# ---------------------------------------------------------------------------
# 6. Marker breadth (gatekeeper F3) + honest RED messaging (gatekeeper F4)
# ---------------------------------------------------------------------------


def test_whatsapp_token_and_verify_token_are_marker_required_not_just_phone_number_id() -> None:
    """Gatekeeper F3: R-101's marker used to cover only `phone_number_id`. `send_message` ALSO
    fails closed on `whatsapp_token`, and `verify_webhook` on `whatsapp_verify_token` — both must
    now surface as marker-required too, or a chart that stops injecting either stays green."""
    _, required, marker_required, _no_default = extract_settings_env_names(_SRC_DIR)
    for name in ("WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_TOKEN", "WHATSAPP_VERIFY_TOKEN"):
        assert name in required, f"{name} missing from required"
        assert name in marker_required, f"{name} missing from marker_required"


def test_whatsapp_token_is_genuinely_declared_in_the_real_chart_today() -> None:
    """Non-vacuity: `WHATSAPP_TOKEN` really is injected today — proves the RED proof below is
    testing a real removal, not a name that was never declared in the first place."""
    rendered = render_chart()
    declared_names = {ref.name for ref in extract_declared_from_helm(rendered)}
    assert "WHATSAPP_TOKEN" in declared_names


def test_removing_whatsapp_token_from_the_webhook_receiver_deployment_goes_red(tmp_path: Path) -> None:
    """Gatekeeper F3 RED proof: R-101's approved text asks for a fence that fails red when "qualquer
    env exigida" in `mcp_whatsapp/server.py` is missing from the chart — not only
    `WHATSAPP_PHONE_NUMBER_ID`. Mutates a COPY of the chart (never the real tree — `tmp_path`,
    `shutil.copytree`) to strip the `WHATSAPP_TOKEN` secretKeyRef block from
    `deployment-webhook-receiver.yaml`, then runs the REAL reconciliation pipeline against it."""
    chart_copy = tmp_path / "maezo-tenant"
    shutil.copytree(_REPO_ROOT / "deploy" / "helm" / "maezo-tenant", chart_copy)
    deployment = chart_copy / "templates" / "deployment-webhook-receiver.yaml"
    original = deployment.read_text(encoding="utf-8")
    token_block = (
        "            - name: WHATSAPP_TOKEN\n"
        "              valueFrom:\n"
        "                secretKeyRef:\n"
        "                  name: maezo-whatsapp-config\n"
        "                  key: waba-token\n"
    )
    assert token_block in original, "WHATSAPP_TOKEN block shape drifted — update this test's literal"
    mutated = original.replace(token_block, "", 1)
    deployment.write_text(mutated, encoding="utf-8")

    rendered = render_chart(
        chart=str(chart_copy),
        value_files=[str(_REPO_ROOT / "deploy" / "helm" / "maezo-tenant" / "values-amh.yaml")],
    )
    declared = extract_declared_from_helm(rendered) + extract_declared_from_terraform(_TF_ROOT)
    literal_names = extract_literal_env_names(_SRC_DIR)
    settings_all, settings_required, marker_required, no_default = extract_settings_env_names(_SRC_DIR)
    read_names = literal_names | settings_all
    required_refs = _required_env_refs(settings_required, marker_required, no_default)
    result = reconcile(declared, read_names, required_refs)

    assert not result.ok
    assert "WHATSAPP_TOKEN" in {r.name for r in result.required_undeclared}
    message = result.render()
    assert "REQUIRED BUT NEVER DECLARED: `WHATSAPP_TOKEN`" in message
    # Gatekeeper F4: the message must state the TRUTH for this marker-derived name — a pydantic
    # default exists, the refusal happens at CALL time, never "fails closed at boot".
    token_line = next(line for line in message.splitlines() if "WHATSAPP_TOKEN" in line)
    assert "chart_required" in token_line
    assert "CALL time" in token_line
    assert "fails closed at boot" not in token_line


def test_required_undeclared_message_states_the_truth_for_a_marker_derived_name() -> None:
    """Gatekeeper F4, isolated from the chart-mutation plumbing above: a marker-derived required
    name's RED message must say it has a pydantic default and fails at call time — never the
    boot-failure wording that is only true for a genuinely no-default field."""
    marker_ref = EnvNameRef(name="SYNTHETIC_MARKER_REQUIRED", source=_MARKER_REASON)
    result = reconcile(declared=[], read_names=set(), required=[marker_ref])
    message = result.render()
    assert "SYNTHETIC_MARKER_REQUIRED" in message
    assert "pydantic default" in message
    assert "CALL time" in message
    assert "has no default" not in message


def test_required_undeclared_message_still_states_a_boot_failure_for_a_no_default_name() -> None:
    """Non-regression: a genuinely no-default field's message must still say what it always said —
    only the marker-derived case's wording changed (gatekeeper F4)."""
    no_default_ref = EnvNameRef(name="SYNTHETIC_NO_DEFAULT_REQUIRED", source=_NO_DEFAULT_REASON)
    result = reconcile(declared=[], read_names=set(), required=[no_default_ref])
    message = result.render()
    assert "SYNTHETIC_NO_DEFAULT_REQUIRED" in message
    assert "has no default" in message
    assert "fails closed at boot" in message


# ---------------------------------------------------------------------------
# 7. No-default precedence over the marker for an overlapping name (gatekeeper F10, §Delta)
# ---------------------------------------------------------------------------


def test_whatsapp_verify_token_is_required_via_both_routes_today() -> None:
    """Sanity: the overlap this finding is about must actually exist in the real tree —
    `WHATSAPP_VERIFY_TOKEN` is genuinely no-default on `WhatsAppWebhookSettings.verify_token`
    (`min_length=1`, no `default=`) AND marker-carrying on
    `WhatsAppSettings.whatsapp_verify_token` (default `""`, `chart_required`). `WHATSAPP_TOKEN`
    and `WHATSAPP_PHONE_NUMBER_ID` are marker-ONLY; `WHATSAPP_APP_SECRET` is no-default-ONLY."""
    _, _required, marker_required, no_default = extract_settings_env_names(_SRC_DIR)
    assert "WHATSAPP_VERIFY_TOKEN" in marker_required
    assert "WHATSAPP_VERIFY_TOKEN" in no_default
    assert "WHATSAPP_TOKEN" in marker_required
    assert "WHATSAPP_TOKEN" not in no_default
    assert "WHATSAPP_PHONE_NUMBER_ID" in marker_required
    assert "WHATSAPP_PHONE_NUMBER_ID" not in no_default
    assert "WHATSAPP_APP_SECRET" in no_default
    assert "WHATSAPP_APP_SECRET" not in marker_required


def test_required_env_refs_reason_for_all_four_whatsapp_names() -> None:
    """Gatekeeper F10: a name required via BOTH routes must get the NO-DEFAULT (boot) reason —
    it fails at boot on `WhatsAppWebhookSettings.verify_token` regardless of what the marker on
    the sibling `WhatsAppSettings.whatsapp_verify_token` claims. Before this fix, `_required_env_refs`
    checked `marker_required_names` first and printed the false "fails at CALL time" reason for
    `WHATSAPP_VERIFY_TOKEN` — the exact defect F4 was supposed to have fixed, still present for any
    name required by both routes."""
    _, required, marker_required, no_default = extract_settings_env_names(_SRC_DIR)
    refs = {ref.name: ref.source for ref in _required_env_refs(required, marker_required, no_default)}

    assert refs["WHATSAPP_VERIFY_TOKEN"] == _NO_DEFAULT_REASON
    assert refs["WHATSAPP_TOKEN"] == _MARKER_REASON
    assert refs["WHATSAPP_PHONE_NUMBER_ID"] == _MARKER_REASON
    assert refs["WHATSAPP_APP_SECRET"] == _NO_DEFAULT_REASON


def test_required_env_refs_prefers_no_default_reason_when_both_routes_apply() -> None:
    """Isolated, synthetic RED proof of the precedence itself (independent of the real WhatsApp
    coincidence above): a name in BOTH `marker_required_names` and `no_default_names` must get the
    boot reason. Swapping `_required_env_refs`'s `if`/`elif` order (checking `marker_required_names`
    before `no_default_names`) makes this test FAIL — reproduced and reverted this session."""
    refs = _required_env_refs(
        required_names={"SYNTHETIC_BOTH_ROUTES"},
        marker_required_names={"SYNTHETIC_BOTH_ROUTES"},
        no_default_names={"SYNTHETIC_BOTH_ROUTES"},
    )
    assert len(refs) == 1
    assert refs[0].name == "SYNTHETIC_BOTH_ROUTES"
    assert refs[0].source == _NO_DEFAULT_REASON


def test_removing_whatsapp_verify_token_from_the_deployment_states_the_boot_reason(
    tmp_path: Path,
) -> None:
    """End-to-end RED proof against the real pipeline (chart mutated in a COPY, never the real
    tree): removing `WHATSAPP_VERIFY_TOKEN` must go RED with the boot reason, not the call-time
    marker reason — the live-CLI probe this session (`check_chart_env_reconciliation.py --chart
    <mutated-copy>`) reproduced this exact message."""
    chart_copy = tmp_path / "maezo-tenant"
    shutil.copytree(_REPO_ROOT / "deploy" / "helm" / "maezo-tenant", chart_copy)
    deployment = chart_copy / "templates" / "deployment-webhook-receiver.yaml"
    original = deployment.read_text(encoding="utf-8")
    verify_token_block = (
        "            - name: WHATSAPP_VERIFY_TOKEN\n"
        "              valueFrom:\n"
        "                secretKeyRef:\n"
        "                  name: maezo-whatsapp-config\n"
        "                  key: verify-token\n"
    )
    assert verify_token_block in original, (
        "WHATSAPP_VERIFY_TOKEN block shape drifted — update this test's literal"
    )
    deployment.write_text(original.replace(verify_token_block, "", 1), encoding="utf-8")

    rendered = render_chart(
        chart=str(chart_copy),
        value_files=[str(_REPO_ROOT / "deploy" / "helm" / "maezo-tenant" / "values-amh.yaml")],
    )
    declared = extract_declared_from_helm(rendered) + extract_declared_from_terraform(_TF_ROOT)
    literal_names = extract_literal_env_names(_SRC_DIR)
    settings_all, settings_required, marker_required, no_default = extract_settings_env_names(_SRC_DIR)
    read_names = literal_names | settings_all
    required_refs = _required_env_refs(settings_required, marker_required, no_default)
    result = reconcile(declared, read_names, required_refs)

    assert not result.ok
    assert "WHATSAPP_VERIFY_TOKEN" in {r.name for r in result.required_undeclared}
    message = result.render()
    token_line = next(line for line in message.splitlines() if "WHATSAPP_VERIFY_TOKEN" in line)
    assert "has no default" in token_line
    assert "fails closed at boot" in token_line
    assert "CALL time" not in token_line
