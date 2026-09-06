"""R-023 / SC-02 (OWNER-DECISIONS-REGISTER, APROVADO-APOS-REVISAO-HUMANA): every `Deployment`
template in this chart declares `capacity.declaredCeilingPerReplica` +
`capacity.measurementStatus` in `values.yaml` — documentation-as-data, never a built HPA
(`resposta_sugerida` verbatim: "declarar os tetos estruturais deriváveis hoje e carimbar
explicitamente como NÃO MEDIDO o que só carga real responde"; `deployer_agent_prep_task`: "nenhum
template HPA neste PR").

WHY THIS FENCE EXISTS. Before this PR, `grep -n capacity deploy/helm/maezo-tenant/values.yaml` was
0 hits — SC-02's audit found zero autoscaling AND zero declared structural ceiling anywhere in the
chart, so nobody could say what a single replica of any Deployment was expected to hold before it
falls over. This module is TREE-DERIVED (`glob("deployment-*.yaml")`), not a hardcoded count, so a
future new `deployment-*.yaml` template that omits its `values.yaml` capacity block fails here by
construction, the same way `test_sc06_throughput_ceilings.py` re-derives its fan-in counts from the
tree every run rather than trusting a frozen number.

`declaredCeilingPerReplica` is `None` (YAML `null`) for Deployments with no derivable concurrency
knob (module doesn't exist yet, or no concurrency-limiting code exists) — this fence checks that
the KEY is present with an explicit value, never that the value is a positive integer, because
inventing a number where none is derivable would be exactly the "capacidade especulativa" the
owner's decision rejected. `measurementStatus` must be the literal `"NAO_MEDIDO"` everywhere: none
of these ceilings — derived or not — have ever been validated against real load.

RED proof (reproduced this session, reverted before commit): deleting `workerDaemon.capacity` from
`values.yaml` makes `test_every_deployment_template_has_a_capacity_block` fail, naming
`deployment-worker-daemon.yaml` as the offending template.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_CHART_DIR: Final[Path] = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant"
_TEMPLATES_DIR: Final[Path] = _CHART_DIR / "templates"
_VALUES: Final[Path] = _CHART_DIR / "values.yaml"

#: Every `deployment-*.yaml` template file -> the `values.yaml` top-level key(s) whose `capacity`
#: block governs its replica(s). `deployment-agent-runtime.yaml` renders ONE Deployment per
#: `.Values.agents` entry (a `range`, not a 1:1 file<->Deployment mapping) — its capacity lives on
#: the SHARED `agentDefaults` block, the same fallback `resources` already uses
#: (`$agent.resources | default $.Values.agentDefaults.resources`).
_TEMPLATE_TO_VALUES_KEY: Final[dict[str, str]] = {
    "deployment-a2a-outbox-relay.yaml": "a2aOutboxRelay",
    "deployment-agent-runtime.yaml": "agentDefaults",
    "deployment-bridge-consent-revocation.yaml": "consentRevocationBridge",
    "deployment-bridge-netchange.yaml": "networkChangeBridge",
    "deployment-bridge.yaml": "notificationsBridge",
    "deployment-fhir-sync.yaml": "fhirSync",
    "deployment-gateway.yaml": "gateway",
    "deployment-webhook-receiver.yaml": "webhookReceiver",
    "deployment-worker-daemon.yaml": "workerDaemon",
}


def _deployment_template_files() -> list[Path]:
    files = sorted(_TEMPLATES_DIR.glob("deployment-*.yaml"))
    assert files, f"no deployment-*.yaml templates found under {_TEMPLATES_DIR}"
    return files


def _values() -> dict[str, Any]:
    return yaml.safe_load(_VALUES.read_text(encoding="utf-8"))


def test_the_map_covers_exactly_the_deployment_templates_on_disk() -> None:
    """A claims sweep: `_TEMPLATE_TO_VALUES_KEY` must name EVERY `deployment-*.yaml` template that
    actually exists — tree-derived, so a new template file that forgets to extend this map fails
    here rather than silently being skipped by the loop below."""
    on_disk = {path.name for path in _deployment_template_files()}
    mapped = set(_TEMPLATE_TO_VALUES_KEY)
    assert mapped == on_disk, (
        f"mismatch between mapped templates and templates on disk: "
        f"missing from map={sorted(on_disk - mapped)}, stale in map={sorted(mapped - on_disk)}"
    )


def test_every_deployment_template_has_a_capacity_block() -> None:
    """The R-023 invariant itself: every Deployment template's governing `values.yaml` key carries
    a `capacity` mapping with BOTH `declaredCeilingPerReplica` and `measurementStatus` keys."""
    values = _values()
    missing: list[str] = []
    for template_name, values_key in _TEMPLATE_TO_VALUES_KEY.items():
        block = values.get(values_key)
        assert block is not None, f"values.yaml has no top-level key {values_key!r}"
        capacity = block.get("capacity")
        if capacity is None:
            missing.append(f"{template_name} (values.yaml:{values_key}.capacity)")
            continue
        if "declaredCeilingPerReplica" not in capacity:
            missing.append(f"{template_name} (values.yaml:{values_key}.capacity.declaredCeilingPerReplica)")
        if "measurementStatus" not in capacity:
            missing.append(f"{template_name} (values.yaml:{values_key}.capacity.measurementStatus)")
    assert not missing, "Deployment template(s) missing a capacity key:\n" + "\n".join(missing)


def test_measurement_status_is_the_literal_nao_medido_everywhere() -> None:
    """None of these ceilings — derived from a real knob or not — were ever validated against real
    load. A future edit that flips one to some other status without an actual load test would be
    exactly the self-certification the owner's decision (`floor_note`) forbids."""
    values = _values()
    wrong: list[str] = []
    for values_key in set(_TEMPLATE_TO_VALUES_KEY.values()):
        status = values[values_key]["capacity"]["measurementStatus"]
        if status != "NAO_MEDIDO":
            wrong.append(f"{values_key}.capacity.measurementStatus={status!r}")
    assert not wrong, "capacity.measurementStatus must stay the literal 'NAO_MEDIDO':\n" + "\n".join(wrong)


def test_declared_ceilings_that_exist_are_positive_integers_or_explicitly_null() -> None:
    """Where a ceiling IS declared (not null), it must be a real positive integer — never a zero,
    a float, or a string standing in for "I don't know" (that's what `null` is for)."""
    values = _values()
    bad: list[str] = []
    for values_key in set(_TEMPLATE_TO_VALUES_KEY.values()):
        ceiling = values[values_key]["capacity"]["declaredCeilingPerReplica"]
        if ceiling is None:
            continue
        if not (isinstance(ceiling, int) and not isinstance(ceiling, bool) and ceiling > 0):
            bad.append(f"{values_key}.capacity.declaredCeilingPerReplica={ceiling!r}")
    assert not bad, "declaredCeilingPerReplica must be a positive int or null:\n" + "\n".join(bad)


def test_worker_daemon_ceiling_matches_the_code_derived_max_tasks_per_poll() -> None:
    """Pins the SPECIFIC derivation this fence's docstring claims: `workerDaemon`'s ceiling must
    equal `WorkerRuntimeSettings.max_tasks_per_poll`'s own default, re-read from the settings
    module (not copied by hand) — so a future change to that default, without updating
    `values.yaml`, is caught here rather than silently drifting."""
    settings_path = _REPO_ROOT / "src" / "maezo" / "runtime" / "worker_runtime" / "settings.py"
    settings_text = settings_path.read_text(encoding="utf-8")
    expected_field = 'max_tasks_per_poll: int = Field(default=10, alias="WORKER_MAX_TASKS_PER_POLL")'
    assert expected_field in settings_text, (
        "WorkerRuntimeSettings.max_tasks_per_poll's default/alias changed — re-derive "
        "workerDaemon.capacity.declaredCeilingPerReplica in values.yaml from the new value"
    )
    values = _values()
    assert values["workerDaemon"]["capacity"]["declaredCeilingPerReplica"] == 10


def test_a2a_outbox_relay_ceiling_matches_its_own_declared_batch_size() -> None:
    """`declaredCeilingPerReplica` must track `batchSize` structurally, not as two independent
    numbers that can drift apart — re-read `batchSize` from the same file rather than hardcoding
    100 twice."""
    values = _values()
    relay = values["a2aOutboxRelay"]
    assert relay["capacity"]["declaredCeilingPerReplica"] == relay["batchSize"]
