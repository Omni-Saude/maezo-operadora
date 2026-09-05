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
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from scripts.ci.check_chart_env_reconciliation import (
    DEFERRED_UNRECONCILED_DECLARED,
    INFRA_OWNED_DECLARED,
    EnvNameRef,
    _has_chart_required_marker,
    _module_level_string_constants,
    _require_reasons,
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
    settings_all, settings_required = extract_settings_env_names(_SRC_DIR)
    read_names = literal_names | settings_all
    required_refs = [
        EnvNameRef(name=n, source="src/ BaseSettings (no default)") for n in sorted(settings_required)
    ]
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
    _, required = extract_settings_env_names(_SRC_DIR)
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
    _, required = extract_settings_env_names(_SRC_DIR)
    assert "WHATSAPP_PHONE_NUMBER_ID" in required
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
    _, required = extract_settings_env_names(tmp_path)
    assert "SYNTH_SOMETHING" in required


def test_real_chart_covers_both_new_a2a_outbox_relay_env_names() -> None:
    """SC-01's new Deployment declares two new env names — both must be genuinely read by
    `src/maezo/a2a/outbox_relay.py`'s `OutboxRelaySettings`, not merely declared."""
    rendered = render_chart()
    declared_names = {ref.name for ref in extract_declared_from_helm(rendered)}
    literal_names = extract_literal_env_names(_SRC_DIR)
    settings_all, _ = extract_settings_env_names(_SRC_DIR)
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
    settings_all, _ = extract_settings_env_names(_SRC_DIR)
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
    settings_all, _ = extract_settings_env_names(_SRC_DIR)
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
    settings_all, settings_required = extract_settings_env_names(_SRC_DIR)
    read_names = literal_names | settings_all
    required_refs = [
        EnvNameRef(name=n, source="src/ BaseSettings (no default)") for n in sorted(settings_required)
    ]
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
