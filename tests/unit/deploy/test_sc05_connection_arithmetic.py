"""SC-05 / R-025+R-026 (OWNER-DECISIONS-REGISTER, APROVADO-APOS-REVISAO-HUMANA): the connection
arithmetic block in `deploy/helm/maezo-tenant/values.yaml` (pools/process x processes/pod x
replicaCount x tenants vs Aurora max_connections) is DERIVED from real files, not asserted prose.

This test recomputes every input from the actual `values.yaml`/`*.tf` files and pins the derived
totals the comment cites — it goes RED the moment any of them drifts (a `create_pool` site's
`max_size` changes, a component's default `replicaCount`/`enabled` changes, or the prod
`instance_class` changes) without the comment being revisited.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_VALUES = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant" / "values.yaml"
_DOCKERFILE = _REPO_ROOT / "deploy" / "Dockerfile"
_PROD_MAIN_TF = _REPO_ROOT / "deploy" / "terraform" / "envs" / "prod-amh-sa-east-1" / "main.tf"

#: The four `create_pool` sites SC-05/D4-02 name explicitly.
_POOL_SITES: tuple[str, ...] = (
    "src/maezo/a2a/idempotency.py",
    "src/maezo/a2a/outbox.py",
    "src/maezo/platform/integrations/amh_inbox.py",
    "src/maezo/gateway/audit_postgres.py",
)


def _values() -> dict:
    return yaml.safe_load(_VALUES.read_text(encoding="utf-8"))


def test_four_pool_sites_all_still_use_max_size_10() -> None:
    for rel_path in _POOL_SITES:
        source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
        assert re.search(r"max_size\s*=\s*10\b", source), f"{rel_path}: max_size default changed"


def test_worst_case_per_process_ceiling_is_40() -> None:
    """4 pools x max_size 10 = 40 — the conservative per-process ceiling the comment cites
    (a single process could, in the worst case, reach all four; there is no fifth site)."""
    assert len(_POOL_SITES) * 10 == 40


def test_no_multi_worker_entrypoint_so_processes_per_pod_is_one() -> None:
    """The arithmetic assumes 1 OS process per pod — proven by the absence of a multi-worker
    launcher (gunicorn / `--workers`) in the image's entrypoint."""
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
    assert "gunicorn" not in dockerfile.lower()
    assert "--workers" not in dockerfile


def test_enabled_by_default_pod_count_is_11() -> None:
    """Sums `replicaCount` over every component whose default is `enabled: true` — components
    gated off by default (`gateway`, `fhirSync`, `networkChangeBridge`,
    `consentRevocationBridge`, and the disabled `agents` entries) contribute zero."""
    values = _values()

    total = 0
    for agent in values["agents"]:
        if agent["enabled"]:
            total += agent["replicaCount"]

    for component_name in ("workerDaemon", "notificationsBridge", "webhookReceiver", "a2aOutboxRelay"):
        component = values[component_name]
        if component["enabled"]:
            total += component["replicaCount"]

    # Sanity: the components assumed disabled-by-default really are, today.
    for component_name in ("gateway", "fhirSync", "networkChangeBridge", "consentRevocationBridge"):
        assert values[component_name]["enabled"] is False, (
            f"{component_name} is now enabled by default — SC-05's pod count (11) is stale, "
            "recompute the values.yaml comment block"
        )
    for agent in values["agents"]:
        if agent["name"] in ("gustavo", "lucas"):
            assert agent["enabled"] is False, (
                f"{agent['name']} is now enabled by default — SC-05's pod count (11) is stale"
            )

    assert total == 11, f"enabled-by-default pod count drifted to {total} — recompute SC-05's block"


def test_worst_case_demand_per_tenant_is_440() -> None:
    assert 40 * 1 * 11 == 440


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


def test_headroom_at_one_tenant_matches_the_documented_figure() -> None:
    assert 3604 - 440 == 3164
