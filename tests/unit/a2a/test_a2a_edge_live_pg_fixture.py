"""Fast fences for the live A2A fixture's engine contract (EIR-CI-A2A-01).

The live assertions remain in ``test_a2a_edge_live_pg.py`` and require real Postgres/CIB Seven.
These tests exercise only the fixture composition, with no service or network access, so a future
edit cannot silently point the positive path back at the RAF-02 negative endpoint or hide its
engine dependency from the integration runner.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import TracebackType

import pytest

from . import test_a2a_edge_live_pg as live


class _RecordingDeployClient:
    instances: list[_RecordingDeployClient] = []

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.deploy_calls: list[tuple[tuple[Path, ...], str]] = []
        self.instances.append(self)

    def __enter__(self) -> _RecordingDeployClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def deploy(self, paths: tuple[Path, ...], *, name: str) -> None:
        self.deploy_calls.append((paths, name))


def test_positive_fixture_resolves_live_engine_and_deploys_auth_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_url = "http://127.0.0.1:18080/engine-rest"
    _RecordingDeployClient.instances.clear()
    monkeypatch.setattr(live, "resolve_engine_rest_url", lambda: resolved_url)
    monkeypatch.setattr(live, "EngineDeployClient", _RecordingDeployClient)

    assert live._deploy_auth_process_to_live_engine() == resolved_url
    assert len(_RecordingDeployClient.instances) == 1
    client = _RecordingDeployClient.instances[0]
    assert client.base_url == resolved_url
    assert client.deploy_calls == [(live._AUTH_ARTIFACTS, "a2a-live-pg-auth-fixture")]
    assert [path.relative_to(live._REPO_ROOT).as_posix() for path in live._AUTH_ARTIFACTS] == [
        "spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn",
        "spec/processes/dmn/auth_admissibility.dmn",
        "spec/processes/dmn/auth_auto_approval.dmn",
        "spec/processes/dmn/auth_sla.dmn",
    ]
    assert all(path.is_file() for path in live._AUTH_ARTIFACTS)


def test_positive_settings_take_the_resolved_url_and_raf02_is_an_explicit_negative() -> None:
    resolved_url = "http://127.0.0.1:18080/engine-rest"
    settings = live._settings(
        tenant="tenantfence", database_url="postgresql://db", engine_rest_url=resolved_url
    )
    assert settings.cibseven_base_url == resolved_url
    assert settings.cibseven_base_url != live._UNREACHABLE_CIBSEVEN_URL

    source = inspect.getsource(live.test_live_unreachable_engine_propagates_without_false_completed_or_seal)
    assert "pytest.raises(StartProcessFailedError)" in source
    assert 'idem_row["status"] == "processing"' in source
    assert 'idem_row["result"] is None' in source
    assert '"COMPLETED" not in' in source
    assert 'fact["kind"] != "completed"' in source


def test_runner_dependency_signal_stays_executable_in_the_live_module() -> None:
    source = inspect.getsource(live._deploy_auth_process_to_live_engine)
    assert "resolve_engine_rest_url()" in source
