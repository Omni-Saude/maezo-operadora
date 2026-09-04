"""Unit tests for the chart/TF <-> src/ env-name reconciliation fence (DU-02 / R-002).

Layers:

1. **Synthetic** (`reconcile` directly): an injected declared-but-unread name — including the
   EXACT shape of the original DU-02 defect (`job-migrations.yaml` declaring `MAEZO_TENANT`) —
   proves the gate goes RED without needing `helm`/`src/` on disk at all.
2. **Real chart + TF + src/**: the full extraction pipeline against the real tree proves GREEN
   today (the rename landed) and proves it is green FOR THE RIGHT REASON — `MAEZO_TENANT_ID` is
   declared and read, `MAEZO_TENANT` is declared nowhere.
3. **Allowlist non-vacuity**: every `INFRA_OWNED_DECLARED` entry that the real chart/TF actually
   declares is exempted (not silently dropped from the count) and is genuinely absent from `src/`'s
   read set — proving the allowlist is load-bearing, not accidentally exempting nothing.
"""

from __future__ import annotations

from pathlib import Path

from scripts.ci.check_chart_env_reconciliation import (
    INFRA_OWNED_DECLARED,
    EnvNameRef,
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


# ---------------------------------------------------------------------------
# 2. Real chart + TF + src/ — GREEN today, for the right reason
# ---------------------------------------------------------------------------


def test_real_tree_is_green() -> None:
    rendered = render_chart()
    declared = extract_declared_from_helm(rendered) + extract_declared_from_terraform(_TF_ROOT)
    literal_names = extract_literal_env_names(_SRC_DIR)
    settings_all, settings_required = extract_settings_env_names(_SRC_DIR)
    read_names = literal_names | settings_all
    required_refs = [
        EnvNameRef(name=n, source="src/ BaseSettings (no default)") for n in sorted(settings_required)
    ]
    result = reconcile(declared, read_names, required_refs)
    assert result.ok, result.render()


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


# ---------------------------------------------------------------------------
# 3. Allowlist non-vacuity
# ---------------------------------------------------------------------------


def test_allowlist_entries_that_are_declared_today_are_genuinely_unread_by_src() -> None:
    """Every allowlisted name the real TF tree actually declares must be ABSENT from src/'s read
    set — otherwise the allowlist entry is dead weight (or worse, hiding a real defect)."""
    declared_names = {ref.name for ref in extract_declared_from_terraform(_TF_ROOT)}
    declared_names |= {ref.name for ref in extract_declared_from_helm(render_chart())}
    literal_names = extract_literal_env_names(_SRC_DIR)
    settings_all, _ = extract_settings_env_names(_SRC_DIR)
    read_names = literal_names | settings_all

    declared_and_allowlisted = declared_names & set(INFRA_OWNED_DECLARED)
    assert len(declared_and_allowlisted) >= 13, (
        "sanity: the Kafka-broker allowlist entries must actually surface in the real TF tree"
    )
    for name in declared_and_allowlisted:
        assert name not in read_names, (
            f"{name} is allowlisted as infra-owned/non-Python but src/ DOES read it — "
            "remove the allowlist entry instead of leaving a stale exemption"
        )


def test_allowlist_has_no_unused_entries_against_the_real_tree() -> None:
    """Every `INFRA_OWNED_DECLARED` entry either surfaces in today's default render/TF sweep, or is
    explicitly documented (its own dict comment) as gated off by default — both real entries in
    this module are covered by one of the two."""
    declared_names = {ref.name for ref in extract_declared_from_terraform(_TF_ROOT)}
    declared_names |= {ref.name for ref in extract_declared_from_helm(render_chart())}
    undeclared_allowlist_entries = set(INFRA_OWNED_DECLARED) - declared_names
    # CIBSEVEN_DATABASE_URL is pinned defensively for a template gated off by default
    # (cibseven.inCluster.enabled=false) — the one legitimate "not declared today" entry.
    assert undeclared_allowlist_entries == {"CIBSEVEN_DATABASE_URL"}
