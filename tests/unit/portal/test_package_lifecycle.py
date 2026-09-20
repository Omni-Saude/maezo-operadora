"""Offline refusal and collection accounting checks; no engine emulation."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[3]


def module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


lifecycle = module("package_lifecycle", "deploy/cibseven/package-test/lifecycle.py")
secured = module("secured_prepare", "deploy/cibseven/secured/prepare_fixture.py")


def owned_stop_packet(runner, monkeypatch):
    """Docker protocol double only: no container, engine or runtime acceptance."""
    identity = "a" * 64
    runner.state.update(owner="b" * 32, phase="d7")
    packet = {
        "present": True,
        "events": [],
        "records": {
            identity: {
                "Id": identity,
                "Image": "sha256:" + "c" * 64,
                "Config": {
                    "Labels": {
                        lifecycle.OWNER_LABEL: runner.state["owner"],
                        "com.docker.compose.project": runner.project,
                        "com.docker.compose.service": "engine",
                    }
                },
            }
        },
    }

    monkeypatch.setattr(
        runner, "resources", lambda project: [("container", identity)] if packet["present"] else []
    )

    def command(argv, **kwargs):
        assert argv == ["docker", "container", "inspect", identity]
        return json.dumps([packet["records"][identity]])

    def compose(*args, **kwargs):
        assert args == ("bootstrap", "down", "-v")
        packet["events"].append("down")
        packet["present"] = False

    def logs(argv, *, stdout, stderr, **kwargs):
        assert argv == ["docker", "logs", "--timestamps", identity]
        assert kwargs["timeout"] == 30 and kwargs["check"] is False
        packet["events"].append("logs")
        stdout.write(b"engine-private-canary\n")
        stderr.write(b"engine-stderr-canary\n")
        if packet.get("interrupt") is not None:
            raise packet["interrupt"]
        if packet.get("drift_after_capture"):
            packet["records"][identity]["Config"]["Labels"][lifecycle.OWNER_LABEL] = "foreign"
        return SimpleNamespace(returncode=packet.get("returncode", 0))

    monkeypatch.setattr(runner, "run", command)
    monkeypatch.setattr(runner, "compose", compose)
    monkeypatch.setattr(lifecycle.subprocess, "run", logs)
    return packet


def capture_report(runner):
    paths = list(runner.root.glob("engine-logs-*/capture.json"))
    assert len(paths) == 1
    return paths[0], json.loads(paths[0].read_text())


def test_stop_captures_actual_streams_before_down_with_private_custody(runner, monkeypatch):
    packet = owned_stop_packet(runner, monkeypatch)
    runner.stop()
    assert packet["events"] == ["logs", "down"]
    path, report = capture_report(runner)
    assert report["outcome"] == "CAPTURED" and report["engine_present"] is True
    assert report["owner_sha256"] == hashlib.sha256(runner.state["owner"].encode()).hexdigest()
    assert report["records"][0]["returncode"] == 0
    assert "canary" not in path.read_text()
    assert path.parent.stat().st_mode & 0o777 == 0o700
    for stream in ("stdout", "stderr"):
        entry = report["records"][0][stream]
        raw = path.parent / entry["path"]
        assert raw.stat().st_mode & 0o777 == 0o600
        assert entry["sha256"] == hashlib.sha256(raw.read_bytes()).hexdigest()
        assert entry["bytes"] == len(raw.read_bytes())
    assert path.stat().st_mode & 0o777 == 0o600
    assert runner.state["phase"] == "stopped"


def test_log_command_failure_remains_failed_while_owned_cleanup_runs(runner, monkeypatch):
    packet = owned_stop_packet(runner, monkeypatch)
    packet["returncode"] = 23
    with pytest.raises(RuntimeError, match="log capture failed"):
        runner.stop()
    assert packet["events"] == ["logs", "down"]
    _, report = capture_report(runner)
    assert report["outcome"] == "FAILED"
    assert report["records"][0]["returncode"] == 23
    assert runner.state["phase"] == "stopped"


def test_interrupted_capture_keeps_partial_streams_and_still_cleans(runner, monkeypatch):
    packet = owned_stop_packet(runner, monkeypatch)
    packet["interrupt"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as caught:
        runner.stop()
    assert caught.value is packet["interrupt"]
    assert packet["events"] == ["logs", "down"]
    _, report = capture_report(runner)
    assert report["records"][0]["error_type"] == "KeyboardInterrupt"
    assert report["records"][0]["returncode"] is None
    assert report["records"][0]["stdout"]["bytes"] > 0


@pytest.mark.parametrize("drift", ["nonce", "project", "service", "identity"])
def test_foreign_or_ambiguous_identity_grants_neither_capture_nor_teardown(runner, monkeypatch, drift):
    packet = owned_stop_packet(runner, monkeypatch)
    record = next(iter(packet["records"].values()))
    if drift == "identity":
        record["Id"] = "f" * 64
    else:
        field = {
            "nonce": lifecycle.OWNER_LABEL,
            "project": "com.docker.compose.project",
            "service": "com.docker.compose.service",
        }[drift]
        record["Config"]["Labels"][field] = "foreign"
    with pytest.raises(lifecycle.OwnershipError):
        runner.stop()
    assert packet["events"] == [] and packet["present"] is True
    _, report = capture_report(runner)
    assert report["outcome"] == "FAILED" and report["records"] == []


def test_already_missing_engine_is_not_claimed_as_captured(runner, monkeypatch):
    packet = owned_stop_packet(runner, monkeypatch)
    packet["present"] = False
    with pytest.raises(RuntimeError, match="already absent"):
        runner.stop()
    assert packet["events"] == []
    _, report = capture_report(runner)
    assert report["engine_present"] is False and report["records"] == []
    assert report["outcome"] == "FAILED"


def test_ownership_drift_after_capture_refuses_teardown(runner, monkeypatch):
    packet = owned_stop_packet(runner, monkeypatch)
    packet["drift_after_capture"] = True
    with pytest.raises(lifecycle.OwnershipError):
        runner.stop()
    assert packet["events"] == ["logs"] and packet["present"] is True


def test_outer_wrapper_retains_original_failure_and_capture_failure(runner, monkeypatch):
    packet = owned_stop_packet(runner, monkeypatch)
    packet["returncode"] = 23
    primary = AssertionError("private-primary-canary")

    def operation():
        raise primary

    with pytest.raises(ExceptionGroup) as caught:
        runner.run_with_cleanup(operation)
    assert caught.value.exceptions[0] is primary
    assert len(caught.value.exceptions) == 2
    assert packet["events"] == ["logs", "down"]
    summary = next(runner.root.glob("operation-stop-*.json"))
    assert "canary" not in summary.read_text()
    assert json.loads(summary.read_text())["operation"]["error_type"] == "AssertionError"


def test_outer_interruption_runs_stop_and_preserves_interrupt(runner, monkeypatch):
    packet = owned_stop_packet(runner, monkeypatch)
    interrupt = KeyboardInterrupt()

    def operation():
        raise interrupt

    with pytest.raises(KeyboardInterrupt) as caught:
        runner.run_with_cleanup(operation)
    assert caught.value is interrupt and packet["events"] == ["logs", "down"]


def test_auxiliary_cleanup_error_does_not_omit_main_capture_or_cleanup(runner, monkeypatch):
    packet = owned_stop_packet(runner, monkeypatch)

    def cleanup():
        packet["events"].append("extract-error")
        raise RuntimeError("auxiliary cleanup failed")

    monkeypatch.setattr(runner, "cleanup_extracts", cleanup)
    with pytest.raises(RuntimeError, match="auxiliary"):
        runner.stop()
    assert packet["events"] == ["logs", "extract-error", "down"]


def test_primary_capture_and_teardown_failures_all_survive(runner, monkeypatch):
    packet = owned_stop_packet(runner, monkeypatch)
    packet["returncode"] = 23
    primary = AssertionError("original test failure")
    cleanup_failure = RuntimeError("owned removal failed")

    def operation():
        raise primary

    def compose(*args, **kwargs):
        packet["events"].append("down-failed")
        raise cleanup_failure

    monkeypatch.setattr(runner, "compose", compose)
    with pytest.raises(ExceptionGroup) as caught:
        runner.run_with_cleanup(operation)
    assert caught.value.exceptions[0] is primary
    stop_failures = caught.value.exceptions[1]
    assert isinstance(stop_failures, ExceptionGroup)
    assert stop_failures.exceptions[1] is cleanup_failure
    assert packet["events"] == ["logs", "down-failed"]
    assert packet["present"] is True and runner.state["phase"] == "d7"


def test_cutover_capture_failure_prevents_engine_replacement(runner, monkeypatch):
    runner.state["phase"] = "human-qualified"
    events = []
    monkeypatch.setattr(runner, "verify_source", lambda: None)
    monkeypatch.setattr(runner, "verify_project_ownership", lambda project: None)
    monkeypatch.setattr(runner, "verify_running_identity", lambda phase: None)
    monkeypatch.setattr(runner, "ready", lambda *a, **k: None)
    monkeypatch.setattr(runner, "configuration_hashes", lambda phase: {})
    monkeypatch.setattr(lifecycle, "load_module", lambda *a: SimpleNamespace(seed=lambda root: None))

    def capture(reason):
        assert reason == "human-to-d7-cutover"
        events.append("capture-failed")
        raise RuntimeError("custody failure")

    monkeypatch.setattr(runner, "capture_engine_logs", capture)
    monkeypatch.setattr(runner, "compose", lambda *a, **k: pytest.fail("engine removed without capture"))
    with pytest.raises(RuntimeError, match="custody failure"):
        runner.start_d7()
    assert events == ["capture-failed"]


def test_inventory_preserves_all_collected_identities_and_reports_actual_counts() -> None:
    paths = [row[0] for row in lifecycle.FAMILIES.values()]
    nodeids = [path + "::test_example[case]" for path in paths]
    nodeids.append("tests/integration/test_other.py::test_case")
    result = lifecycle.inventory(nodeids)
    owned = [node for family in result["families"].values() for node in family["nodeids"]]
    assert set(owned + result["other_nodeids"]) == set(nodeids)
    assert all(family["count"] == 1 for family in result["families"].values())
    assert result["families"]["d7"]["historical_count"] == 1119
    assert result["families"]["d7"]["count"] != 1119


@pytest.mark.parametrize("values", [["a::b", "a::b"], ["5 tests collected"], {"not": "nodeids"}])
def test_inventory_refuses_ambiguous_input(values) -> None:
    with pytest.raises(ValueError):
        lifecycle.inventory(values)


@pytest.mark.parametrize("drift", ["source_sha", "bootstrap_image", "secured_image", "bootstrap_jar_sha256"])
def test_candidate_receipt_drift_refused_before_generation(tmp_path: Path, drift: str) -> None:
    receipt = {
        "source_sha": "a" * 40,
        "bootstrap_image": "sha256:" + "b" * 64,
        "secured_image": "sha256:" + "c" * 64,
        "bootstrap_jar_sha256": "d" * 64,
        "secured_jar_sha256": "e" * 64,
    }
    receipt[drift] = "invalid"
    file = tmp_path / "image-receipt.json"
    file.write_text(json.dumps(receipt))
    args = SimpleNamespace(
        sha="a" * 40,
        bootstrap_image="sha256:" + "b" * 64,
        secured_image="sha256:" + "c" * 64,
        image_receipt=file,
    )
    with pytest.raises(ValueError, match="receipt|source"):
        secured.prepare(args)
    assert list(tmp_path.iterdir()) == [file]


def test_cutover_refuses_without_human_phase_before_any_command(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    root.chmod(0o700)
    lifecycle.dump(root / "d7-fixture.json", {"fixture_only": True, "checkout": str(ROOT)})
    lifecycle.dump(root / "lifecycle.json", {"project": "d7-unit-12345678", "phase": "prepared"})
    runner = lifecycle.Lifecycle(root)
    with pytest.raises(ValueError, match="human bootstrap"):
        runner.start_d7()
    assert not runner.log.exists()


def test_q2_missing_authority_is_refused_before_network(tmp_path: Path) -> None:
    environment = tmp_path / "q2-env.json"
    environment.write_text("{}")
    with pytest.raises(ValueError, match="independently admitted provider"):
        lifecycle.preflight_q2(ROOT, environment)


def test_private_dump_replaces_symlink_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "original"
    target.write_text("preserve")
    output = tmp_path / "environment.json"
    output.symlink_to(target)
    lifecycle.dump(output, {"private": True})
    assert target.read_text() == "preserve"
    assert not output.is_symlink()
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.fixture
def runner(tmp_path):
    root = tmp_path.resolve()
    root.chmod(0o700)
    lifecycle.dump(root / "d7-fixture.json", {"fixture_only": True, "checkout": str(ROOT)})
    lifecycle.dump(root / "lifecycle.json", {"project": "d7-unit-12345678", "phase": "prepared"})
    return lifecycle.Lifecycle(root)


def test_stop_after_refused_adoption_never_invokes_docker(runner, monkeypatch):
    monkeypatch.setattr(runner, "verify_source", lambda: None)
    monkeypatch.setattr(runner, "resources", lambda project: [("container", "foreign")])
    with pytest.raises(ValueError, match="adoption"):
        runner.start_human()
    monkeypatch.setattr(runner, "run", lambda *a, **k: pytest.fail("unowned Docker mutation"))
    runner.stop()
    assert runner.state["phase"] == "prepared"


@pytest.mark.parametrize(
    "child",
    [
        "lifecycle-private.log",
        "public-mount-marker",
        "bootstrap-compose.json",
        "ca.crt",
        "bootstrap-installed.jar",
    ],
)
def test_child_symlinks_refused_before_command_or_output(runner, tmp_path, child):
    outside = tmp_path.parent / (tmp_path.name + "-preserved")
    outside.write_text("preserve")
    (runner.root / child).symlink_to(outside)
    with pytest.raises(ValueError, match="regular"):
        runner.run(["python", "-c", "raise AssertionError('must not execute')"])
    assert outside.read_text() == "preserve"
    with pytest.raises(ValueError, match="regular"):
        lifecycle.Lifecycle(runner.root)


def test_existing_permissive_log_refused(runner):
    runner.log.write_text("preserve")
    runner.log.chmod(0o644)
    with pytest.raises(ValueError, match="private regular log"):
        runner.run(["python", "-c", "raise AssertionError('must not execute')"])
    assert runner.log.read_text() == "preserve"


def test_cleanup_refuses_foreign_resource_even_with_claimed_state(runner, monkeypatch):
    runner.state.update(owner="a" * 32, phase="starting-human")
    monkeypatch.setattr(runner, "resources", lambda project: [("container", "foreign")])
    commands = []

    def command(argv, **kwargs):
        commands.append(argv)
        assert argv == ["docker", "container", "inspect", "foreign"]
        return json.dumps([{"Config": {"Labels": {lifecycle.OWNER_LABEL: "someone-else"}}}])

    monkeypatch.setattr(runner, "run", command)
    with pytest.raises(ValueError, match="unowned"):
        runner.stop()
    assert len(commands) == 1


@pytest.mark.parametrize(
    "reason", ["entrypoint failed", "daemon unavailable", "bind source path does not exist: /wrong/path"]
)
def test_missing_mount_refuses_unrelated_command_failure(runner, monkeypatch, reason):
    runner.state["owner"] = "owned"
    runner.metadata["bootstrap_image"] = "sha256:test"
    monkeypatch.setattr(runner, "resources", lambda project: [])

    def command(argv, **kwargs):
        if argv[:3] == ["docker", "container", "ls"]:
            return ""
        if argv[:2] == ["docker", "run"]:
            return (runner.root / "public-mount-marker").read_text()
        raise lifecycle.CommandError(reason)

    monkeypatch.setattr(runner, "run", command)
    with pytest.raises(lifecycle.CommandError):
        runner.mount_preflight()
    assert "probe_project" not in runner.state


def test_missing_mount_accepts_only_exact_source_refusal(runner, monkeypatch):
    runner.state["owner"] = "owned"
    runner.metadata["bootstrap_image"] = "sha256:test"
    monkeypatch.setattr(runner, "resources", lambda project: [])

    def command(argv, **kwargs):
        if argv[:3] == ["docker", "container", "ls"]:
            return ""
        if argv[:2] == ["docker", "run"]:
            return (runner.root / "public-mount-marker").read_text()
        source = json.loads((runner.root / "negative-mount-compose.json").read_text())["services"]["probe"][
            "volumes"
        ][0]["source"]
        raise lifecycle.CommandError("invalid mount config: bind source path does not exist: " + source)

    monkeypatch.setattr(runner, "run", command)
    runner.mount_preflight()
    assert "probe_project" not in runner.state


@pytest.mark.parametrize("drift", ["image", "jar", "stopped"])
def test_running_artifact_drift_refused_before_admission(runner, monkeypatch, drift):
    runner.state["owner"] = "owned"
    runner.metadata.update(
        bootstrap_image="sha256:engine", image_receipt={"bootstrap_jar_sha256": "expected"}
    )
    lifecycle.dump(
        runner.root / "bootstrap-compose.json",
        {"services": {"engine": {"image": "sha256:engine"}, "postgres": {"image": "sha256:postgres"}}},
    )
    monkeypatch.setattr(runner, "configuration_hashes", lambda *a: {})
    runner.state["config_hashes"] = {"bootstrap": {}}
    monkeypatch.setattr(runner, "compose", lambda *a, **k: "engine-id")
    monkeypatch.setattr(
        runner,
        "assert_owned",
        lambda *a: {
            "Image": "wrong" if drift == "image" else "sha256:engine",
            "State": {"Running": drift != "stopped"},
        },
    )
    monkeypatch.setattr(runner, "run", lambda *a, **k: "sha256:engine")
    monkeypatch.setattr(runner, "jar_hash", lambda *a: "changed" if drift == "jar" else "expected")
    with pytest.raises(ValueError, match="identity|JAR"):
        runner.verify_running_identity("bootstrap")
    assert not (runner.root / "preflight-receipt.json").exists()


def test_configuration_drift_fails_before_docker(runner, monkeypatch):
    lifecycle.dump(runner.root / "bootstrap-compose.json", {"services": {}})
    runner.state["config_hashes"] = {"bootstrap": {"old": "digest"}}
    monkeypatch.setattr(runner, "run", lambda *a, **k: pytest.fail("must fail before Docker"))
    with pytest.raises(ValueError, match="configuration changed"):
        runner.verify_running_identity("bootstrap")


def test_interrupted_temporary_container_cleanup_checks_ownership(runner, monkeypatch):
    runner.state.update(owner="owned", extract_containers=["d7-unit-12345678-extract-test"])
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["container", "ls"]:
            return "container-id"
        if argv[1:3] == ["container", "inspect"]:
            return json.dumps([{"Config": {"Labels": {lifecycle.OWNER_LABEL: "foreign"}}}])
        pytest.fail("foreign resource mutation")

    monkeypatch.setattr(runner, "run", command)
    with pytest.raises(ValueError, match="unowned"):
        runner.cleanup_extracts()
    assert len(calls) == 2
    assert runner.state["extract_containers"]


def test_running_identities_include_configuration_and_database_volume(runner, monkeypatch):
    engine = "sha256:" + "a" * 64
    database = "sha256:" + "b" * 64
    source = runner.root / "trust.json"
    lifecycle.dump(source, {"synthetic": True})
    document = {
        "services": {
            "engine": {
                "image": engine,
                "environment": {"FIXTURE": "true"},
                "volumes": [
                    {
                        "source": str(source),
                        "target": "/trust",
                        "read_only": True,
                    }
                ],
            },
            "postgres": {"image": database, "volumes": ["postgres-data:/var/lib/postgresql/data"]},
        }
    }
    lifecycle.dump(runner.root / "bootstrap-compose.json", document)
    runner.state.update(owner="owned", config_hashes={"bootstrap": runner.configuration_hashes("bootstrap")})
    runner.metadata.update(bootstrap_image=engine, image_receipt={"bootstrap_jar_sha256": "jar"})
    monkeypatch.setattr(runner, "compose", lambda phase, *args, **kwargs: args[-1] + "-id")
    monkeypatch.setattr(runner, "run", lambda args, **kwargs: args[-1])
    monkeypatch.setattr(runner, "jar_hash", lambda identity: "jar")
    observed = []

    def inspect(kind, identity):
        observed.append((kind, identity))
        if kind == "volume":
            return {}
        return {
            "Image": engine if identity == "engine-id" else database,
            "State": {"Running": True},
            "Config": {"Env": ["FIXTURE=true"]},
            "Mounts": [
                {"Destination": "/trust", "Source": str(source), "RW": False},
                {"Destination": "/var/lib/postgresql/data", "Type": "volume", "Name": "owned-volume"},
            ],
        }

    monkeypatch.setattr(runner, "assert_owned", inspect)
    receipt = runner.verify_running_identity("bootstrap")
    assert receipt["jar_sha256"] == "jar"
    assert receipt["database_volume"] == "owned-volume"
    assert receipt["configuration_sha256"]["trust.json"]
    assert ("volume", "owned-volume") in observed
