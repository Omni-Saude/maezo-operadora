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
        rc = run_deploy(client, spec_dir=spec_dir, deployment_name="n", tenant="amh", shared=None)
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
        rc = run_deploy(
            client, spec_dir=tmp_path / "does-not-exist", deployment_name="n", tenant="amh", shared=None
        )
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
        rc = run_deploy(client, spec_dir=spec_dir, deployment_name="n", tenant="amh", shared=None)
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
        rc = run_deploy(client, spec_dir=spec_dir, deployment_name="n", tenant="amh", shared=None)
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
        self.deploy_tenants: list[str | None] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def list_deployments(self) -> list[dict[str, Any]]:
        return self._list_result

    def deploy(self, paths: Any, *, name: str, tenant_id: str | None) -> Any:
        self.deploy_calls.append((paths, name))
        self.deploy_tenants.append(tenant_id)
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

        rc = main(["--tenant", "amh"])
        out = capsys.readouterr().out

        assert rc == 0
        assert len(fake.deploy_calls) == 1
        _, name = fake.deploy_calls[0]
        assert name == DEFAULT_DEPLOYMENT_NAME
        # 54 DMN (T1.3) + 7 fraude_scoring (T2.7) + auth_criteria_contratual (GAP-AUTH-4) = 62
        assert "17 BPMN, 64 DMN" in out  # VW4/GP11

    def test_deploy_mode_engine_rejection_is_nonzero_exit(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _FakeClient(
            deploy_error=EngineDeployError(
                "deployment 'n' rejected by engine [400]: bad bpmn", status_code=400, body="bad bpmn"
            )
        )
        monkeypatch.setattr("maezo.platform.deploy.cli.EngineDeployClient", lambda base_url=None: fake)

        rc = main(["--tenant", "amh"])
        err = capsys.readouterr().err

        assert rc == 1
        assert "bad bpmn" in err


# ---------------------------------------------------------------------------
# Deploy target is explicit (27/09/2026): tenant-owned definitions, shared copies, or REFUSE
# ---------------------------------------------------------------------------


class TestDeployTarget:
    """A tenant-bound start (`/process-definition/key/{k}/tenant-id/{t}/start`) resolves ONLY
    definitions of its tenant, and a tenant's business rule task resolves decisions ONLY in the
    same tenant — so the deploy must say where the definitions live, and never default."""

    @staticmethod
    def _recording_client(tmp_path: Path) -> tuple[EngineDeployClient, list[dict[str, Any]]]:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.read().decode("utf-8", errors="replace")
            tenant = None
            marker = 'name="tenant-id"\r\n\r\n'
            if marker in body:
                tenant = body.split(marker, 1)[1].split("\r\n", 1)[0]
            files = sorted(
                part.split('filename="', 1)[1].split('"', 1)[0]
                for part in body.split("--")
                if 'filename="' in part
            )
            seen.append({"tenant": tenant, "files": files})
            return httpx.Response(200, json={"id": "dep", "name": "n"})

        return _client_with_handler(handler), seen

    def test_no_target_refuses_without_calling_the_engine(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        client, seen = self._recording_client(tmp_path)
        rc = run_deploy(
            client, spec_dir=_fake_spec_dir(tmp_path), deployment_name="n", tenant=None, shared=None
        )
        assert rc == 2
        assert seen == []
        assert "REFUSED" in capsys.readouterr().err

    def test_main_without_target_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeClient()
        monkeypatch.setattr("maezo.platform.deploy.cli.EngineDeployClient", lambda base_url=None: fake)
        assert main([]) == 2
        assert fake.deploy_calls == []

    def test_tenant_deploys_every_bpmn_and_dmn_owned_by_the_tenant(self, tmp_path: Path) -> None:
        client, seen = self._recording_client(tmp_path)
        rc = run_deploy(
            client, spec_dir=_fake_spec_dir(tmp_path), deployment_name="n", tenant="amh", shared=None
        )
        assert rc == 0
        assert seen == [{"tenant": "amh", "files": ["d0.dmn", "d1.dmn", "d2.dmn", "p0.bpmn", "p1.bpmn"]}]

    def test_tenant_plus_shared_dmn_adds_a_decisions_only_shared_copy(self, tmp_path: Path) -> None:
        client, seen = self._recording_client(tmp_path)
        rc = run_deploy(
            client, spec_dir=_fake_spec_dir(tmp_path), deployment_name="n", tenant="amh", shared="dmn"
        )
        assert rc == 0
        assert seen[0]["tenant"] == "amh"
        # The shared copy carries NO BPMN: a second shared BPMN would duplicate timer starts.
        assert seen[1] == {"tenant": None, "files": ["d0.dmn", "d1.dmn", "d2.dmn"]}

    def test_shared_all_is_the_explicit_tenantless_deploy(self, tmp_path: Path) -> None:
        client, seen = self._recording_client(tmp_path)
        rc = run_deploy(
            client, spec_dir=_fake_spec_dir(tmp_path), deployment_name="n", tenant=None, shared="all"
        )
        assert rc == 0
        assert seen == [{"tenant": None, "files": ["d0.dmn", "d1.dmn", "d2.dmn", "p0.bpmn", "p1.bpmn"]}]

    @pytest.mark.parametrize("ruim", ["", "a/b", "amh?x", "../amh", "amh-x"])
    def test_invalid_tenant_is_rejected_before_the_engine(self, tmp_path: Path, ruim: str) -> None:
        client, seen = self._recording_client(tmp_path)
        rc = run_deploy(
            client, spec_dir=_fake_spec_dir(tmp_path), deployment_name="n", tenant=ruim, shared=None
        )
        assert rc == 1
        assert seen == []

    def test_main_passes_tenant_and_shared_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeClient()
        monkeypatch.setattr("maezo.platform.deploy.cli.EngineDeployClient", lambda base_url=None: fake)
        assert main(["--tenant", "amh", "--shared", "dmn"]) == 0
        assert fake.deploy_tenants == ["amh", None]
        assert all(p.suffix == ".dmn" for p in fake.deploy_calls[1][0])
