"""Fence: every compose service the `integration` job STARTS is also WAITED FOR before the tests run.

WHY THIS FENCE EXISTS (gap CI-KAFKA-HEALTH-WAIT; owner decisions R-074 and R-095, 2026-09-04)
----------------------------------------------------------------------------------------------
`.github/workflows/ci.yml`'s `integration` job brought up `postgres kafka cibseven` and then waited
for postgres and cibseven only. Kafka's readiness was INCIDENTAL — it happened to be up by the time
a Kafka-touching test ran, until it was not: CI run 33740368677 died on a `TimeoutError` in
`test_notifications_bridge_live_kafka.py` against a cold broker, and the mitigation at the time was
applied on the TEST side only, leaving the workflow's own gap open.

The asymmetry is what makes this worth a fence rather than a one-line fix: adding a service to the
`up -d` line is a natural, low-ceremony edit, while remembering to extend a shell loop three lines
below is not. So this test DERIVES the started set from the workflow itself and demands a wait for
each member. It cannot be satisfied by adding a service to an allowlist here — the only way to make
it green is to wait for the service, or to stop starting it.

WHY THE BROKER'S OWN PROBE AND NOT THE COMPOSE HEALTHCHECK. `docker-compose.yml` does declare a
Kafka healthcheck, and `docker compose up --wait` would appear simpler. It was rejected on measured
grounds: this job runs two JVMs (cibseven, kafka) plus Postgres on one GitHub-hosted runner, and the
compose healthcheck has already been observed reporting unhealthy under that memory pressure while
the broker was in fact serving. `kafka-broker-api-versions` asks the broker the one question the
tests care about — "will you answer a client?" — and is the pattern this same file already uses for
the Tasy simulator job, so the two lanes cannot drift apart in what "ready" means.

WHAT THIS FENCE DOES NOT COVER. Whether `integration tests (real engine)` is a REQUIRED status check
on the `main-protection` ruleset is server-side state that no file in this tree can assert; it is an
owner act (R-095) and it is deliberately not faked here. Measured 2026-09-04, the ruleset required
exactly four contexts and this job was not among them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_COMPOSE = _REPO_ROOT / "docker-compose.yml"

_INTEGRATION_JOB = "integration"
_START_STEP = "Start core stack"
_WAIT_STEP = "Wait for stack to be healthy"

#: How each started service proves it is ready. The value is a fragment that must appear inside the
#: wait step's `run:` block. Each is the service's OWN readiness question, never a compose-level
#: proxy — see the module docstring for why the distinction is load-bearing for kafka.
_READINESS_PROBE: dict[str, str] = {
    "postgres": "pg_isready",
    "cibseven": "engine-rest/version",
    "kafka": "kafka-broker-api-versions",
}


def _steps(job_id: str) -> list[dict[str, Any]]:
    workflow = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    return list(workflow["jobs"][job_id]["steps"])


def _step_starting_with(job_id: str, prefix: str) -> dict[str, Any]:
    for step in _steps(job_id):
        name = step.get("name") or ""
        if name.startswith(prefix):
            return step
    raise AssertionError(f"no step named {prefix!r} in job {job_id!r} — has the workflow been restructured?")


def _started_services() -> list[str]:
    """Derive the services the job brings up from its own `docker compose ... up -d` command."""
    run = _step_starting_with(_INTEGRATION_JOB, _START_STEP)["run"]
    match = re.search(r"docker compose[^\n]*\bup -d\b(?P<services>[^\n]*)", run)
    assert match, f"could not find a `docker compose ... up -d` command in:\n{run}"
    services = [token for token in match.group("services").split() if not token.startswith("-")]
    assert services, "the `up -d` command names no services — the derivation would be vacuous"
    return services


def test_the_derivation_finds_the_services_this_job_really_starts() -> None:
    """Non-vacuity: if the regex above ever stops matching, every assertion below goes quiet."""
    assert set(_started_services()) == {"postgres", "kafka", "cibseven"}, (
        "the integration job's service set changed. That is allowed — but update "
        "`_READINESS_PROBE` with how the new service proves it is ready, and update this test's "
        "expectation deliberately, rather than letting the fence drift."
    )


@pytest.mark.parametrize("service", sorted(_READINESS_PROBE))
def test_every_started_service_is_waited_for_before_the_tests_run(service: str) -> None:
    started = _started_services()
    assert service in started, f"{service!r} has a readiness probe declared but is no longer started"
    run = _step_starting_with(_INTEGRATION_JOB, _WAIT_STEP)["run"]
    probe = _READINESS_PROBE[service]
    assert probe in run, (
        f"the integration job starts {service!r} but the wait step never probes it "
        f"(expected the fragment {probe!r}). A service that is started but not waited for is ready "
        "only by luck — CI-KAFKA-HEALTH-WAIT is exactly that failure, already observed live in run "
        "33740368677."
    )


def test_no_started_service_is_left_without_a_declared_probe() -> None:
    """The other direction: adding a service to `up -d` must not silently escape this fence."""
    undeclared = [s for s in _started_services() if s not in _READINESS_PROBE]
    assert undeclared == [], (
        f"{undeclared} are started by the integration job but have no readiness probe declared in "
        "`_READINESS_PROBE`. Declare how each proves it is ready and wait for it in the workflow."
    )


def test_the_kafka_probe_targets_the_internal_listener_the_compose_file_advertises() -> None:
    """The probe runs INSIDE the kafka container, so it must use the listener advertised for
    container-to-container traffic. Pinning against `docker-compose.yml` rather than a literal keeps
    the two files from disagreeing after a listener change."""
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    advertised = compose["services"]["kafka"]["environment"]["KAFKA_ADVERTISED_LISTENERS"]
    internal = dict(entry.split("://", 1) for entry in advertised.split(","))["INTERNAL"]
    run = _step_starting_with(_INTEGRATION_JOB, _WAIT_STEP)["run"]
    assert f"--bootstrap-server {internal}" in run, (
        f"the wait step must probe kafka on its INTERNAL advertised listener ({internal}); the "
        "command runs inside the container, where that is the address Docker DNS resolves."
    )


def test_the_wait_step_fails_loudly_rather_than_falling_through_on_timeout() -> None:
    """A `timeout` that is not checked lets a cold broker through as a green wait step. The kafka
    line carries an explicit `|| { ...; exit 1; }`, the same shape the Tasy simulator job uses."""
    run = _step_starting_with(_INTEGRATION_JOB, _WAIT_STEP)["run"]
    kafka_line = next(line for line in run.splitlines() if "kafka-broker-api-versions" in line)
    tail = run[run.index(kafka_line) + len(kafka_line) :]
    assert "exit 1" in tail.split('echo "All services healthy."')[0], (
        "the kafka wait must exit non-zero on timeout; without it, `timeout` returning 124 is "
        "swallowed and the job proceeds against a broker that never answered."
    )
