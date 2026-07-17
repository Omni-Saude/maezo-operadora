"""Unit tests for maezo.platform.deploy.cli — deploy-artifacts CLI (T1.3).

Engine interaction is MOCKED (httpx.MockTransport, or a hand-rolled fake
implementing the same surface as EngineDeployClient) — these tests assert
exit codes and output shape, not real engine behavior. See the T1.3 PR body
for the live-verification run against a real `cibseven` container.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.platform.deploy.cli import (
    DEFAULT_DEPLOYMENT_NAME,
    build_parser,
    main,
    run_deploy,
    run_list,
)
from maezo.platform.deploy.engine_deploy import EngineDeployClient, EngineDeployError

# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


class TestCliParser:
    def test_help_flag(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0

    def test_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        assert args.list_mode is False
        assert args.deployment_name == DEFAULT_DEPLOYMENT_NAME
        assert args.engine_url is None
        assert args.spec_dir is None

    def test_list_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--list"])
        assert args.list_mode is True

    def test_overrides(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "--deployment-name",
                "custom-name",
                "--engine-url",
                "http://x:1234/engine-rest",
                "--spec-dir",
                "/tmp/spec",
            ]
        )
        assert args.deployment_name == "custom-name"
        assert args.engine_url == "http://x:1234/engine-rest"
        assert args.spec_dir == "/tmp/spec"


# ---------------------------------------------------------------------------
# run_deploy / run_list — direct calls (mocked transport)
# ---------------------------------------------------------------------------


def _fake_spec_dir(tmp_path: Path, *, bpmn: int = 2, dmn: int = 3) -> Path:
    spec_dir = tmp_path / "spec"
    processes_dir = spec_dir / "processes"
    bpmn_dir = processes_dir / "bpmn"
    dmn_dir = processes_dir / "dmn"
    bpmn_dir.mkdir(parents=True)
    dmn_dir.mkdir(parents=True)
    for i in range(bpmn):
        (bpmn_dir / f"p{i}.bpmn").write_text("<x/>")
    for i in range(dmn):
        (dmn_dir / f"d{i}.dmn").write_text("<x/>")
    return spec_dir


def _client_with_handler(handler: Any) -> EngineDeployClient:
    transport = httpx.MockTransport(handler)
    return EngineDeployClient(
        base_url="http://engine.example/engine-rest", client=httpx.Client(transport=transport)
    )


class TestRunDeploy:
    def test_success_returns_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        spec_dir = _fake_spec_dir(tmp_path, bpmn=2, dmn=3)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "dep-1",
                    "name": "n",
                    "deployedProcessDefinitions": {"a": {"resource": "p0.bpmn"}},
                    "deployedDecisionDefinitions": {},
                },
            )

        client = _client_with_handler(handler)
        rc = run_deploy(client, spec_dir=spec_dir, deployment_name="n")
        out = capsys.readouterr().out

        assert rc == 0
        assert "5 artifact(s) (2 BPMN, 3 DMN)" in out
        assert "deployed (new/changed): 1" in out
        assert "skipped (duplicate, unchanged): 4" in out

    def test_missing_spec_dir_returns_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("engine must not be called when artifact resolution fails")

        client = _client_with_handler(handler)
        rc = run_deploy(client, spec_dir=tmp_path / "does-not-exist", deployment_name="n")
        err = capsys.readouterr().err

        assert rc == 1
        assert "FAILED to resolve artifacts" in err

    def test_engine_rejection_returns_nonzero_and_prints_engine_body(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        spec_dir = _fake_spec_dir(tmp_path, bpmn=1, dmn=0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="ENGINE-09008 malformed BPMN")

        client = _client_with_handler(handler)
        rc = run_deploy(client, spec_dir=spec_dir, deployment_name="n")
        err = capsys.readouterr().err

        assert rc == 1
        assert "ENGINE-09008 malformed BPMN" in err

    def test_idempotent_rerun_reports_all_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        spec_dir = _fake_spec_dir(tmp_path, bpmn=1, dmn=1)

        def handler(request: httpx.Request) -> httpx.Response:
            # duplicate-filtered response: nothing new deployed
            return httpx.Response(
                200,
                json={
                    "id": "dep-1",
                    "name": "n",
                    "deployedProcessDefinitions": {},
                    "deployedDecisionDefinitions": {},
                },
            )

        client = _client_with_handler(handler)
        rc = run_deploy(client, spec_dir=spec_dir, deployment_name="n")
        out = capsys.readouterr().out

        assert rc == 0
        assert "deployed (new/changed): 0" in out
        assert "skipped (duplicate, unchanged): 2" in out


class TestRunList:
    def test_success_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=[{"id": "d1", "name": "maezo-spec-processes", "deploymentTime": "t1"}]
            )

        client = _client_with_handler(handler)
        rc = run_list(client)
        out = capsys.readouterr().out

        assert rc == 0
        assert "1 deployment(s) on engine" in out
        assert "id=d1" in out
        assert "maezo-spec-processes" in out

    def test_engine_failure_returns_nonzero(self, capsys: pytest.CaptureFixture[str]) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        client = _client_with_handler(handler)
        rc = run_list(client)
        err = capsys.readouterr().err

        assert rc == 1
        assert "boom" in err


# ---------------------------------------------------------------------------
# main() — end-to-end argv -> exit code, with EngineDeployClient monkeypatched
# ---------------------------------------------------------------------------


class _FakeClient:
    """Drop-in EngineDeployClient replacement for main()-level tests."""

    def __init__(
        self, list_result: list[dict[str, Any]] | None = None, deploy_error: EngineDeployError | None = None
    ) -> None:
        self.base_url = "http://fake/engine-rest"
        self._list_result = list_result or []
        self._deploy_error = deploy_error
        self.deploy_calls: list[tuple[Any, str]] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def list_deployments(self) -> list[dict[str, Any]]:
        return self._list_result

    def deploy(self, paths: Any, *, name: str) -> Any:
        self.deploy_calls.append((paths, name))
        if self._deploy_error is not None:
            raise self._deploy_error
        from maezo.platform.deploy.engine_deploy import DeploymentOutcome

        return DeploymentOutcome.from_response(
            {"id": "dep-1", "name": name, "deployedProcessDefinitions": {}},
            submitted=[p.name for p in paths],
        )


class TestMainEndToEnd:
    def test_list_mode_dispatches_to_run_list(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _FakeClient(list_result=[{"id": "d1", "name": "n", "deploymentTime": "t"}])
        monkeypatch.setattr("maezo.platform.deploy.cli.EngineDeployClient", lambda base_url=None: fake)

        rc = main(["--list"])
        out = capsys.readouterr().out

        assert rc == 0
        assert "1 deployment(s)" in out

    def test_deploy_mode_uses_real_spec_tree_by_default(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _FakeClient()
        monkeypatch.setattr("maezo.platform.deploy.cli.EngineDeployClient", lambda base_url=None: fake)

        rc = main([])
        out = capsys.readouterr().out

        assert rc == 0
        assert len(fake.deploy_calls) == 1
        _, name = fake.deploy_calls[0]
        assert name == DEFAULT_DEPLOYMENT_NAME
        assert "16 BPMN, 54 DMN" in out

    def test_deploy_mode_engine_rejection_is_nonzero_exit(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _FakeClient(
            deploy_error=EngineDeployError(
                "deployment 'n' rejected by engine [400]: bad bpmn", status_code=400, body="bad bpmn"
            )
        )
        monkeypatch.setattr("maezo.platform.deploy.cli.EngineDeployClient", lambda base_url=None: fake)

        rc = main([])
        err = capsys.readouterr().err

        assert rc == 1
        assert "bad bpmn" in err
