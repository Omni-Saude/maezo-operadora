"""The daemon's settings must refuse by name, and must never be satisfiable by a default.

Each test asserts BOTH that composition is refused and that the refusal names the variable an
operator has to set — a daemon that starts on defaults and drains nothing is the failure mode this
whole module exists to make impossible.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from maezo.runtime.intake_dispatch.settings import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_POLL_INTERVAL_S,
    IntakeDispatchRefusalError,
    IntakeDispatchSettings,
)

DIGEST = "b" * 64

DEFINITION = {
    "process_key": "SP-OP-AUTH-001",
    "definition_id": "SP-OP-AUTH-001:3:abc",
    "definition_digest": DIGEST,
    "deployment_id": "deployment-1",
    "input_profile": "portal-auth-intake.v1",
    "profile_digest": DIGEST,
}


def definition_file(tmp_path) -> tuple[str, str]:
    """An owner-installed pin file: absolute path, regular file, owner-only mode."""
    path = tmp_path / "definition.json"
    raw = json.dumps(DEFINITION).encode()
    path.write_bytes(raw)
    os.chmod(path, 0o600)
    return str(path), hashlib.sha256(raw).hexdigest()


def complete(tmp_path, **overrides) -> IntakeDispatchSettings:
    path, digest = definition_file(tmp_path)
    values = {
        "tenant": "amh",
        "lifecycle_path": "/etc/maezo/auth-lifecycle.json",
        "identity_writer_url": "postgresql+asyncpg://identity/db",
        "definition_path": path,
        "definition_digest": digest,
    }
    values.update(overrides)
    return IntakeDispatchSettings(**values)


def test_a_daemon_with_no_configuration_at_all_refuses_naming_the_tenant():
    with pytest.raises(IntakeDispatchRefusalError, match="MAEZO_INTAKE_DISPATCH_TENANT"):
        IntakeDispatchSettings(
            tenant=None,
            lifecycle_path=None,
            identity_writer_url=None,
            definition_path=None,
            definition_digest=None,
        ).materials()


@pytest.mark.parametrize(
    ("missing", "named"),
    [
        ("lifecycle_path", "MAEZO_INTAKE_DISPATCH_LIFECYCLE_PATH"),
        ("identity_writer_url", "MAEZO_INTAKE_DISPATCH_IDENTITY_WRITER_URL"),
        ("definition_path", "MAEZO_INTAKE_DISPATCH_DEFINITION_PATH"),
        ("definition_digest", "MAEZO_INTAKE_DISPATCH_DEFINITION_DIGEST"),
    ],
)
def test_every_effect_bearing_input_is_required_and_named(tmp_path, missing, named):
    with pytest.raises(IntakeDispatchRefusalError, match=named):
        complete(tmp_path, **{missing: None}).materials()


def test_a_wrong_definition_digest_refuses_the_pin(tmp_path):
    from maezo.gateway.human.auth_transport import AuthUnavailableError

    with pytest.raises(AuthUnavailableError):
        complete(tmp_path, definition_digest="c" * 64).materials()


def test_a_relative_definition_path_is_refused(tmp_path):
    from maezo.gateway.human.auth_transport import AuthUnavailableError

    with pytest.raises(AuthUnavailableError):
        complete(tmp_path, definition_path="definition.json").materials()


def test_a_world_readable_definition_pin_is_refused(tmp_path):
    from maezo.gateway.human.auth_transport import AuthUnavailableError

    settings = complete(tmp_path)
    # After the settings exist, so `complete`'s own file write cannot restore the mode.
    assert settings.definition_path is not None
    os.chmod(settings.definition_path, 0o644)
    with pytest.raises(AuthUnavailableError):
        settings.materials()


@pytest.mark.parametrize(("field", "value"), [("batch_size", 0), ("poll_interval_s", 0.0)])
def test_a_loop_that_could_never_sweep_is_refused(tmp_path, field, value):
    with pytest.raises(IntakeDispatchRefusalError, match="never sweeps"):
        complete(tmp_path, **{field: value}).materials()


def test_a_complete_configuration_yields_the_pinned_definition(tmp_path):
    materials = complete(tmp_path).materials()
    assert materials.tenant == "amh"
    assert materials.definition.process_key == "SP-OP-AUTH-001"
    assert materials.definition.definition_id == "SP-OP-AUTH-001:3:abc"
    # The secret never appears in a repr an operator might paste into a ticket.
    assert "postgresql" not in repr(materials)


def test_the_defaults_that_are_allowed_to_exist_are_only_the_harmless_ones(tmp_path):
    settings = complete(tmp_path)
    assert (settings.batch_size, settings.poll_interval_s) == (DEFAULT_BATCH_SIZE, DEFAULT_POLL_INTERVAL_S)
    assert settings.heartbeat_path.endswith("maezo-intake-dispatch.heartbeat")
