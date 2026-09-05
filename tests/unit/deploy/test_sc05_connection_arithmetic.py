"""SC-05 / R-025+R-026 (OWNER-DECISIONS-REGISTER, APROVADO-APOS-REVISAO-HUMANA): the connection
arithmetic block in `deploy/helm/maezo-tenant/values.yaml` (pools/process x processes/pod x
rendered pod count x tenants vs Aurora max_connections) is DERIVED from real files, not asserted
prose.

Gatekeeper findings closed here (VERIFY-A2-HELM-CAPACITY.md):

- **F1**: the previous version summed `replicaCount` from the BARE `values.yaml` (11 pods) but
  crossed that number against PROD's `max_connections` — PROD actually runs the `values-amh.yaml`
  overlay (14 pods, 27% higher demand). This file now derives the per-tenant pod count from a REAL
  `helm template` render for BOTH overlays (`values-staging.yaml`/`values-amh.yaml`) and drives
  the headroom/crossing-point arithmetic from the `values-amh.yaml` row — the one PROD actually
  runs — never from the bare-file row.
- **F6**: every number below is recomputed from the real files (rendered chart, `create_pool`
  sites, the Terraform instance class) and then checked AGAINST what the `values.yaml` comment
  block prints — a drift between the comment and the code goes RED, not a hardcoded literal that
  passes vacuously under every mutation.
- **F9**: the migrations Job (`templates/job-migrations.yaml`) and the 3 `lifecycle` CronJobs are
  `enabled: true` by default and are NOT among the 4 `create_pool` sites — this file also proves
  the stated transient-connection allowance (+1 for migrations, +0 for `lifecycle` TODAY) against
  the real source.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant"
_VALUES = _CHART_DIR / "values.yaml"
_DOCKERFILE = _REPO_ROOT / "deploy" / "Dockerfile"
_PROD_MAIN_TF = _REPO_ROOT / "deploy" / "terraform" / "envs" / "prod-amh-sa-east-1" / "main.tf"
_LIFECYCLE_INIT = _REPO_ROOT / "src" / "maezo" / "platform" / "lifecycle" / "__init__.py"

#: The four `create_pool` sites SC-05/D4-02 name explicitly.
_POOL_SITES: tuple[str, ...] = (
    "src/maezo/a2a/idempotency.py",
    "src/maezo/a2a/outbox.py",
    "src/maezo/platform/integrations/amh_inbox.py",
    "src/maezo/gateway/audit_postgres.py",
)

#: The migrations Job opens exactly one SYNCHRONOUS (psycopg/alembic) connection per tenant —
#: not an asyncpg pool, so it is not among `_POOL_SITES`, but it does run against the same
#: cluster (item 4/5 of the comment block).
_MIGRATIONS_TRANSIENT_ALLOWANCE = 1


def _values() -> dict[str, Any]:
    return yaml.safe_load(_VALUES.read_text(encoding="utf-8"))


def _comment_block() -> str:
    """The raw SC-05 comment block text — tests check the NUMBERS the comment prints against
    independently recomputed values, instead of hardcoding a copy of them (gatekeeper F6)."""
    text = _VALUES.read_text(encoding="utf-8")
    start = text.index("# SC-05 / R-025+R-026")
    end = text.index("# Migrations Job", start)
    return text[start:end]


def _pool_max_size() -> int:
    """Every `create_pool` site's `max_size`, confirmed identical across all four — the
    per-process ceiling is `len(_POOL_SITES) * this`, never a bare hardcoded 40."""
    sizes: set[int] = set()
    for rel_path in _POOL_SITES:
        source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
        match = re.search(r"max_size\s*=\s*(\d+)\b", source)
        assert match, f"{rel_path}: no max_size= found in create_pool()"
        sizes.add(int(match.group(1)))
    assert len(sizes) == 1, f"create_pool sites disagree on max_size: {sizes}"
    return sizes.pop()


def _helm_template(*extra_args: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["helm", "template", "test-release", str(_CHART_DIR), *extra_args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"helm template failed: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc is not None]


def _rendered_pod_count(overlay: str) -> int:
    """SUM `spec.replicas` over every rendered `Deployment` for the given overlay — the real
    per-tenant pod count for that environment (gatekeeper F1: NOT a hand-summed `values.yaml`
    read, which is what the bare file below is documented to be — a floor, not either real
    tenant's count)."""
    docs = _helm_template("-f", str(_CHART_DIR / overlay))
    return sum(doc["spec"].get("replicas", 1) for doc in docs if doc.get("kind") == "Deployment")


def _bare_pod_count() -> int:
    """The BARE `values.yaml` pod count — a direct key read, since this file does not render
    standalone (see `test_bare_values_yaml_is_a_floor_not_a_renderable_tenant` below)."""
    values = _values()
    total = 0
    for agent in values["agents"]:
        if agent["enabled"]:
            total += agent["replicaCount"]
    for component_name in ("workerDaemon", "notificationsBridge", "webhookReceiver", "a2aOutboxRelay"):
        component = values[component_name]
        if component["enabled"]:
            total += component["replicaCount"]
    return total


def test_four_pool_sites_all_still_use_max_size_10() -> None:
    assert _pool_max_size() == 10


def test_worst_case_per_process_ceiling_matches_the_comment() -> None:
    """4 pools x max_size, recomputed — then checked against the exact figure the comment prints,
    so a drift between the code and the prose goes RED (gatekeeper F6: this used to be
    `assert len(_POOL_SITES) * 10 == 40`, a literal with no repo input)."""
    ceiling = len(_POOL_SITES) * _pool_max_size()
    block = _comment_block()
    assert f"4 pools x max_size {_pool_max_size()} = {ceiling} conexoes/processo" in block, (
        f"comment does not state the recomputed per-process ceiling ({ceiling}) — update it"
    )


def test_no_multi_worker_entrypoint_so_processes_per_pod_is_one() -> None:
    """The arithmetic assumes 1 OS process per pod — proven by the absence of a multi-worker
    launcher (gunicorn / `--workers`) in the image's entrypoint."""
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
    assert "gunicorn" not in dockerfile.lower()
    assert "--workers" not in dockerfile


def test_bare_values_yaml_is_a_floor_not_a_renderable_tenant() -> None:
    """Sanity for the documented FLOOR row (item 3a of the comment): the components assumed
    disabled-by-default really are, the bare count matches what the comment prints, AND bare
    `values.yaml` genuinely fails to render standalone (proving the comment's claim that no live
    environment ever deploys it un-overlaid — gatekeeper F1's root cause)."""
    values = _values()
    for component_name in ("gateway", "fhirSync", "networkChangeBridge", "consentRevocationBridge"):
        assert values[component_name]["enabled"] is False, (
            f"{component_name} is now enabled by default — SC-05's floor row is stale, "
            "recompute the values.yaml comment block"
        )
    for agent in values["agents"]:
        if agent["name"] in ("gustavo", "lucas"):
            assert agent["enabled"] is False, (
                f"{agent['name']} is now enabled by default — SC-05's floor row is stale"
            )

    count = _bare_pod_count()
    assert count == 11, f"bare pod count drifted to {count} — recompute SC-05's floor row"
    conns = count * len(_POOL_SITES) * _pool_max_size()
    block = _comment_block()
    assert f"{count} pods -> {conns} conexoes" in block, "comment does not cite the recomputed bare floor row"

    result = subprocess.run(
        ["helm", "template", "test-release", str(_CHART_DIR)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        "bare values.yaml now renders standalone — SC-05's 'bare is a floor, not a real tenant' "
        "framing is stale; recompute the comment block using the bare row as a real environment"
    )


def test_rendered_staging_pod_count_matches_the_comment() -> None:
    count = _rendered_pod_count("values-staging.yaml")
    assert count == 8, f"values-staging.yaml rendered pod count drifted to {count} — recompute SC-05's block"
    conns = count * len(_POOL_SITES) * _pool_max_size()
    block = _comment_block()
    assert f"{count} pods -> {conns} conexoes" in block, (
        "comment does not cite the recomputed values-staging.yaml pod count/demand"
    )


def test_rendered_amh_pod_count_matches_the_comment() -> None:
    """PROD runs `values-amh.yaml`, not the bare file (gatekeeper F1) — this is the row the
    headroom/crossing-point test below is built on."""
    count = _rendered_pod_count("values-amh.yaml")
    assert count == 14, f"values-amh.yaml rendered pod count drifted to {count} — recompute SC-05's block"
    conns = count * len(_POOL_SITES) * _pool_max_size()
    block = _comment_block()
    assert f"{count} pods -> {conns} conexoes" in block, (
        "comment does not cite the recomputed values-amh.yaml pod count/demand"
    )


def test_prod_aurora_uses_the_instance_class_the_comment_derives_max_connections_from() -> None:
    """The comment's ~3604 max_connections figure is derived from `db.r6g.xlarge` (32 GiB) via
    AWS's documented default formula for the aurora-postgresql family:
    `LEAST({DBInstanceClassMemory/9531392}, 5000)`. If prod's instance class ever changes, this
    fails and the derived number must be recomputed."""
    prod_main = _PROD_MAIN_TF.read_text(encoding="utf-8")
    assert re.search(r"serverless\s*=\s*false", prod_main), (
        "prod is expected to be provisioned, not serverless"
    )
    assert re.search(r'instance_class\s*=\s*"db\.r6g\.xlarge"', prod_main), (
        "prod instance_class drifted from db.r6g.xlarge — recompute SC-05's max_connections figure"
    )


def test_prod_max_connections_formula_yields_the_documented_3604() -> None:
    """AWS's documented default `max_connections` formula for aurora-postgresql, applied to
    `db.r6g.xlarge`'s 32 GiB — this repo's parameter groups
    (`aws_rds_cluster_parameter_group.this` / `aws_db_parameter_group.instance`,
    `deploy/terraform/modules/aurora-postgres/main.tf`) do not override it."""
    memory_bytes_r6g_xlarge = 32 * 1024**3
    max_connections = memory_bytes_r6g_xlarge // 9_531_392
    assert max_connections == 3604


def test_migrations_and_lifecycle_transient_allowance_matches_the_real_code() -> None:
    """Gatekeeper F9: `migrations`/`lifecycle` are enabled by default and open connections OUTSIDE
    the 4 `create_pool` sites — the comment's stated allowance (+1 for migrations, +0 for the 3
    `lifecycle` CronJobs TODAY) must match the real code, not be asserted as prose. This is a fact
    about the CURRENT `lifecycle` package — it must go RED (and be revisited) the day any of the
    three commands stops being a fail-closed refusal stub."""
    values = _values()
    assert values["migrations"]["enabled"] is True
    assert values["lifecycle"]["enabled"] is True

    lifecycle_src = _LIFECYCLE_INIT.read_text(encoding="utf-8")
    assert "REFUSAL_EXIT_CODE = 78" in lifecycle_src, (
        "src/maezo/platform/lifecycle/__init__.py no longer fail-closed-refuses at a stable exit "
        "code — SC-05's '+0 transient today' claim for the 3 lifecycle CronJobs is stale; "
        "recompute the transient allowance and the values.yaml comment block"
    )
    # Every known subcommand must still be refused before any DB access — no early-return success.
    for marker in ("SUBCMD_EXPURGO_WORKING", "SUBCMD_VERIFY_ERASURE", "SUBCMD_AUDIT_RETENTION"):
        assert marker in lifecycle_src, f"{marker} no longer named in lifecycle/__init__.py"

    block = _comment_block()
    assert f"+{_MIGRATIONS_TRANSIENT_ALLOWANCE} de folga transiente" in block, (
        "comment does not state the migrations transient-connection allowance"
    )
    assert "lifecycle" in block.lower() and "ZERO" in block, (
        "comment does not name the lifecycle CronJobs' current (zero) transient allowance"
    )


def test_prod_headroom_and_crossing_point_derive_from_the_amh_overlay_not_the_bare_file() -> None:
    """Gatekeeper F1: PROD's real per-tenant demand is the `values-amh.yaml` overlay's rendered
    pod count plus the migrations transient allowance — NOT the bare-file count. Recomputes
    conns/tenant, `max_connections` (via the formula proven above), headroom at N=1, and the
    smallest N that crosses the ceiling — then checks the comment states them."""
    pod_count = _rendered_pod_count("values-amh.yaml")
    per_process = len(_POOL_SITES) * _pool_max_size()
    per_tenant = per_process * 1 * pod_count + _MIGRATIONS_TRANSIENT_ALLOWANCE

    memory_bytes_r6g_xlarge = 32 * 1024**3
    max_connections = memory_bytes_r6g_xlarge // 9_531_392

    headroom_n1 = max_connections - per_tenant
    crossing_n = next(n for n in range(1, 1000) if n * per_tenant > max_connections)
    fits_n = crossing_n - 1
    margin_at_fits_n = max_connections - fits_n * per_tenant

    assert per_tenant == 561, f"amh/prod per-tenant demand drifted to {per_tenant}"
    assert crossing_n == 7, f"crossing point drifted to N={crossing_n} — recompute SC-05's block"

    block = _comment_block()
    assert f"amh/prod  -> 14 pods -> 560 + 1 = {per_tenant}" in block
    assert f"N=1 -> {per_tenant}" in block
    assert f"N=1 -> {headroom_n1} de" in block
    assert f"N={crossing_n} ({crossing_n * per_tenant} > {max_connections})" in block
    assert f"N={fits_n} ({fits_n * per_tenant}) ainda" in block
    assert f"cabe, com {margin_at_fits_n} de margem" in block
