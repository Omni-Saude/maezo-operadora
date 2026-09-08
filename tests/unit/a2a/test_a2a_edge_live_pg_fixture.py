"""Fast fences for the live A2A fixture's engine contract (EIR-CI-A2A-01).

The live assertions remain in ``test_a2a_edge_live_pg.py`` and require real Postgres/CIB Seven.
These tests exercise only the fixture composition, with no service or network access, so a future
edit cannot silently point the positive path back at the RAF-02 negative endpoint or hide its
engine dependency from the integration runner.
"""

from __future__ import annotations

import inspect

import pytest

from . import test_a2a_edge_live_pg as live


class _RecordingEngineRest:
    instances: list[_RecordingEngineRest] = []

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.definition_lookups: list[str] = []
        self.closed = False
        self.instances.append(self)

    async def latest_definition_xml(self, process_definition_key: str) -> str:
        self.definition_lookups.append(process_definition_key)
        return live._AUTH_BPMN.read_text()

    async def aclose(self) -> None:
        self.closed = True


def test_positive_fixture_resolves_live_engine_and_verifies_auth_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_url = "http://127.0.0.1:18080/engine-rest"
    _RecordingEngineRest.instances.clear()
    monkeypatch.setattr(live, "resolve_engine_rest_url", lambda: resolved_url)
    monkeypatch.setattr(live, "EngineRest", _RecordingEngineRest)

    assert live._resolve_verified_live_auth_engine() == resolved_url
    assert len(_RecordingEngineRest.instances) == 1
    engine = _RecordingEngineRest.instances[0]
    assert engine.base_url == resolved_url
    assert engine.definition_lookups == ["SP-OP-AUTH-001"]
    assert engine.closed is True
    assert live._AUTH_BPMN.relative_to(live._REPO_ROOT).as_posix() == (
        "spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn"
    )
    assert live._AUTH_BPMN.is_file()


def test_positive_fixture_fails_closed_on_foreign_auth_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ForeignEngineRest(_RecordingEngineRest):
        async def latest_definition_xml(self, process_definition_key: str) -> str:
            self.definition_lookups.append(process_definition_key)
            return "<definitions><process id='SP-OP-AUTH-001'/></definitions>"

    _ForeignEngineRest.instances.clear()
    monkeypatch.setattr(live, "resolve_engine_rest_url", lambda: "http://127.0.0.1:18080/engine-rest")
    monkeypatch.setattr(live, "EngineRest", _ForeignEngineRest)

    with pytest.raises(AssertionError, match="does not match this checkout"):
        live._resolve_verified_live_auth_engine()
    assert _ForeignEngineRest.instances[0].closed is True


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
    source = inspect.getsource(live._resolve_verified_live_auth_engine)
    assert "resolve_engine_rest_url()" in source
