"""Offline preparation controls; these are not CIB/HTTP/PG execution evidence."""

import json
import re
import stat

import pytest
from tests.support import human_relay_live
from tests.unit.portal.test_package_fixture_mounts import _git_checkout, _server_xml, prepare_module


@pytest.mark.parametrize("enabled", [False, True])
def test_relay_synthetic_is_explicit_and_default_package_is_inert(tmp_path, enabled):
    root = tmp_path.resolve()
    checkout, sha = _git_checkout(root)
    original = _server_xml(root)
    private = root / "private"
    private.mkdir(mode=0o700)
    evidence = root / "evidence"
    evidence.mkdir()
    output = private / "fixture"
    prepare_module.prepare(
        checkout, sha, original, private, evidence, output, "relay-fixture:test", relay_synthetic=enabled
    )
    trust = json.loads((output / "trust.json").read_text())
    public = json.loads((output / "public-receipt.json").read_text())
    assert trust["enable_synthetic_fixture"] is enabled
    assert public["relay_synthetic"] is enabled
    if enabled:
        assert re.fullmatch(r"relay_[0-9a-f]{24}", trust["tenant"])
        assert len(trust["tenant"] + "_alembic_version") <= 63
        config = json.loads((output / "relay-fixture.json").read_text())
        assert config["tenant"] == trust["tenant"]
        assert config["schema"] == "human-relay-fixture.v1"
        assert config["synthetic_opt_in"] is True
        assert config["source_sha"] == sha
        assert config["database"]["host"] == "127.0.0.1"
        assert config["database"]["password_file"] == str(output / "postgres-password")
    else:
        assert trust["tenant"] == "package-test"
        assert not (output / "relay-fixture.json").exists()
    assert (output / "02-tenant.sql").read_text() == (
        "INSERT INTO MZO_HUMAN_TENANT(TENANT_,REV_) VALUES ('" + trust["tenant"] + "',0);\n"
    )
    compose = json.loads((output / "compose.json").read_text())
    mounts = [m for s in compose["services"].values() for m in s.get("volumes", [])]
    assert len(mounts) == 8
    assert all(m["bind"] == {"create_host_path": False} and m["read_only"] for m in mounts)
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in output.iterdir() if p.suffix != ".sql")


def test_live_fixture_refuses_missing_explicit_configuration(monkeypatch):
    monkeypatch.delenv("MAEZO_HUMAN_RELAY_PRIVATE_DIR", raising=False)
    with pytest.raises(AssertionError, match="no skip/default"):
        human_relay_live.RelayConfig.load()


@pytest.mark.parametrize("mutation", ["none", "trust_inactive", "source_mismatch", "remote_database"])
def test_generated_relay_config_checks_opt_in_source_and_locality(tmp_path, monkeypatch, mutation):
    root = tmp_path.resolve()
    checkout, sha = _git_checkout(root)
    original = _server_xml(root)
    private = root / "private"
    private.mkdir(mode=0o700)
    evidence = root / "evidence"
    evidence.mkdir()
    output = private / "fixture"
    prepare_module.prepare(
        checkout, sha, original, private, evidence, output, "relay-fixture:test", relay_synthetic=True
    )
    target = output / ("trust.json" if mutation == "trust_inactive" else "relay-fixture.json")
    data = json.loads(target.read_text())
    if mutation == "trust_inactive":
        data["enable_synthetic_fixture"] = False
    elif mutation == "source_mismatch":
        data["source_sha"] = "0" * 40
    elif mutation == "remote_database":
        data["database"]["host"] = "database.example.invalid"
    target.write_text(json.dumps(data))
    monkeypatch.setattr(human_relay_live, "ROOT", checkout)
    monkeypatch.setenv("MAEZO_HUMAN_RELAY_PRIVATE_DIR", str(output))
    if mutation != "none":
        with pytest.raises(AssertionError):
            human_relay_live.RelayConfig.load()
    else:
        config = human_relay_live.RelayConfig.load()
        assert config.scope.tenant.startswith("relay_")
        # Real dedicated signer and SSLContext construction, still zero networking.
        transport = config.transport()
        assert transport.scope == config.scope
