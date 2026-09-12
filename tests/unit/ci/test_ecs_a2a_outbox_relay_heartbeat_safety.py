"""Guard: the ECS `a2a-outbox-relay` task must never end up silently writing (or trying to write)
its heartbeat to a read-only `/tmp` (gatekeeper finding G2, VERIFY-A1-HELM.md secao Delta).

Why this exists
----------------
`service-a2a-outbox-relay.tf`'s container definition has `readonlyRootFilesystem = true` and
declares NO `volume`/`mountPoints` of its own, and no task in this environment mounts anything at
`/tmp` — `/tmp` for the relay is therefore read-only. (Until the portal service landed, no `.tf`
file under `deploy/aws-ecs/envs/dev-sa-east-1/` used either keyword at all, and this guard swept
for that. `service-portal.tf` now mounts `staff-materials`/`staff-scratch` at `/run/maezo-staff-*`
for a different task family, and redirects that task's `TMPDIR` to `/run/maezo-staff-scratch`
rather than `/tmp`. The premise below is re-aimed at the property that actually protects the
relay, so the guard stays non-vacuous instead of being deleted or broadened away.) If
`A2A_OUTBOX_RELAY_HEARTBEAT_PATH` were left unset, `OutboxRelaySettings` would default to
`DEFAULT_HEARTBEAT_PATH` ("/tmp/maezo-a2a-outbox-relay.heartbeat") and `run_relay_loop` would call
`Path(heartbeat_path).touch()` on EVERY sweep, always raising `OSError` (Read-only file system) —
the exact failure `dsn.tf:16-19` already records happening for real in this environment. The gate
here is that the env var is explicitly emptied, so `_touch_heartbeat`'s `if not path: return`
disables the write cleanly rather than attempting and failing it forever.

This mirrors the repo's own convention for reading Terraform structure (a plain-text regex sweep,
the same technique `scripts/ci/check_helm_entrypoints.py`'s `extract_entrypoints_from_terraform`
and `scripts/ci/check_chart_env_reconciliation.py`'s `extract_declared_from_terraform` already use)
rather than requiring a live `terraform show -json`, which would need AWS credentials this sandbox
does not have.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TF_PATH = _REPO_ROOT / "deploy" / "aws-ecs" / "envs" / "dev-sa-east-1" / "service-a2a-outbox-relay.tf"
_TF_ROOT = _REPO_ROOT / "deploy" / "aws-ecs" / "envs" / "dev-sa-east-1"

_HEARTBEAT_ENV_ENTRY_RE = re.compile(
    r'\{\s*name\s*=\s*"A2A_OUTBOX_RELAY_HEARTBEAT_PATH"\s*,\s*value\s*=\s*"([^"]*)"\s*\}'
)


def test_the_task_definition_file_exists() -> None:
    assert _TF_PATH.is_file(), f"expected file missing: {_TF_PATH}"


def _code(tf_path: Path) -> str:
    return "\n".join(
        line for line in tf_path.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("#")
    )


def test_the_relay_task_declares_no_volume_or_mount_of_its_own() -> None:
    """Non-vacuity, half 1: the relay container gets no writable mount at all. If a future PR
    gives it one, this fails, forcing a deliberate decision about whether the heartbeat override
    below is still correct rather than letting it silently go stale."""
    code_text = _code(_TF_PATH)
    assert not re.search(r"\bmountPoints\s*=", code_text), (
        f"{_TF_PATH.name} declares mountPoints — re-check G2"
    )
    assert not re.search(r"\bvolume\s*\{", code_text), f"{_TF_PATH.name} declares a volume — re-check G2"


def test_the_task_has_no_writable_mount_for_tmp() -> None:
    """Non-vacuity, half 2: no task in this environment mounts anything at `/tmp`, so nothing can
    hand the relay (or any sibling) a writable `/tmp` through a shared volume. Other tasks may
    mount elsewhere — `service-portal.tf` mounts `/run/maezo-staff-*` — which cannot change the
    relay's read-only `/tmp`."""
    for tf_path in sorted(_TF_ROOT.glob("*.tf")):
        code_text = _code(tf_path)
        assert not re.search(r'containerPath\s*=\s*"/tmp(/|")', code_text), (
            f"{tf_path.name} mounts a path under /tmp — re-check G2"
        )


def test_readonly_root_filesystem_is_still_true() -> None:
    text = _TF_PATH.read_text(encoding="utf-8")
    assert re.search(r"readonlyRootFilesystem\s*=\s*true", text)


def test_heartbeat_path_env_var_is_explicitly_empty_given_readonly_rootfs_and_no_mount() -> None:
    """The actual guard: given `readonlyRootFilesystem = true` and no writable mount (proven by the
    two tests above), `A2A_OUTBOX_RELAY_HEARTBEAT_PATH` MUST be declared with an explicit empty
    value — never left unset (which would silently fall back to `DEFAULT_HEARTBEAT_PATH` and fail
    every sweep) and never a non-empty path (which would fail every sweep just as surely, only
    louder). Mutation proof: delete this env entry, or change its value to anything non-empty, and
    this test goes RED.
    """
    text = _TF_PATH.read_text(encoding="utf-8")
    match = _HEARTBEAT_ENV_ENTRY_RE.search(text)
    assert match is not None, (
        "A2A_OUTBOX_RELAY_HEARTBEAT_PATH is not declared in this task's `environment` block — "
        "it would fall back to DEFAULT_HEARTBEAT_PATH and fail every sweep against a read-only /tmp"
    )
    assert match.group(1) == "", (
        f"A2A_OUTBOX_RELAY_HEARTBEAT_PATH is set to {match.group(1)!r}, not empty — with "
        "readonlyRootFilesystem=true and no writable mount, every sweep would OSError"
    )


def test_the_detector_would_catch_a_reintroduced_default_path(tmp_path: Path) -> None:
    """Negative control: prove `_HEARTBEAT_ENV_ENTRY_RE` actually matches a non-empty value (so the
    assertion above is a real check, not a vacuous one that always passes because the regex never
    matches anything)."""
    synthetic = tmp_path / "synthetic.tf"
    synthetic.write_text(
        '{ name = "A2A_OUTBOX_RELAY_HEARTBEAT_PATH", value = "/tmp/should-not-be-here" },\n',
        encoding="utf-8",
    )
    match = _HEARTBEAT_ENV_ENTRY_RE.search(synthetic.read_text(encoding="utf-8"))
    assert match is not None
    assert match.group(1) == "/tmp/should-not-be-here"
