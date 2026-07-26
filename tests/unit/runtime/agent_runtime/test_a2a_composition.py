"""F2 — the A2A composition root must FAIL-CLOSED when no Card-signing key is present.

The defect: `build_auth_delegation_dispatcher` resolved the signing key and, when it was absent,
SILENTLY produced `signer=None` -> unsigned Cards -> `build_dispatcher(verifier=None)` ->
`A2ARegistry(verifier=None)`, which admits ANY (unsigned/forged) Card. In production that is a
signature-enforcement fail-open with no prod-mode guard.

The fix (`_require_signer_or_fail_closed`): an absent key RAISES at composition in production
runtime mode (`agent_runtime_mode != "local"`, un-bypassable — the dev opt-out is IGNORED there),
and in a non-production runtime proceeds unsigned ONLY behind the EXPLICIT
`MAEZO_A2A_ALLOW_UNSIGNED_CARDS` opt-out, never as a silent default.

Prod/dev discriminator: `agent_runtime_mode` — Helm injects "kubernetes"
(`deployment-agent-runtime.yaml`); the settings default is "local". ANY value other than "local"
is treated as production (an unrecognized/misconfigured mode fails closed). This mirrors the
fail-closed startup precedents `AnthropicInferenceProvider` (absent credential -> raise, "no silent
fallback to noop") and `RefusingAnsGatewayTransport` (production default refuses).
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.a2a import CardSigner
from maezo.runtime.agent_runtime import a2a_composition
from maezo.runtime.agent_runtime.a2a_composition import (
    ALLOW_UNSIGNED_CARDS_ENV_VAR,
    _require_signer_or_fail_closed,
    build_auth_delegation_dispatcher,
)
from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings

_SIGNING_KEY_ENV = "MAEZO_A2A_CARD_SIGNING_KEY"
_VALID_KEY = "unit-test-card-signing-key-0123456789abcdef"


@pytest.fixture(autouse=True)
def _clean_signing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never depend on ambient signing-key / opt-out state; each test sets exactly what it needs."""
    monkeypatch.delenv(_SIGNING_KEY_ENV, raising=False)
    monkeypatch.delenv(ALLOW_UNSIGNED_CARDS_ENV_VAR, raising=False)


def _settings(*, mode: str) -> AgentRuntimeSettings:
    return AgentRuntimeSettings(agent_id="rafael", agent_runtime_mode=mode)


# --- The F2 logic, isolated on `_require_signer_or_fail_closed` --------------------------------


def test_prod_mode_absent_key_refuses_to_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRODUCTION (kubernetes) + no key -> RAISE. The core fail-closed guard."""
    with pytest.raises(RuntimeError, match="no Agent Card signing key"):
        _require_signer_or_fail_closed(_settings(mode="kubernetes"))


def test_prod_mode_absent_key_ignores_dev_optout(monkeypatch: pytest.MonkeyPatch) -> None:
    """UN-BYPASSABLE: in production the explicit dev opt-out is IGNORED — an absent key STILL
    raises. Revert-RED guard for the `is_production or ...` clause: dropping `is_production` would
    let the opt-out bypass production enforcement, and this test would go green when it must not."""
    monkeypatch.setenv(ALLOW_UNSIGNED_CARDS_ENV_VAR, "1")
    with pytest.raises(RuntimeError, match="no Agent Card signing key"):
        _require_signer_or_fail_closed(_settings(mode="kubernetes"))


def test_unrecognized_mode_treated_as_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown/misconfigured `agent_runtime_mode` (anything != 'local') fails CLOSED, even with
    the opt-out set — only the literal dev default 'local' may run unsigned."""
    monkeypatch.setenv(ALLOW_UNSIGNED_CARDS_ENV_VAR, "1")
    with pytest.raises(RuntimeError, match="no Agent Card signing key"):
        _require_signer_or_fail_closed(_settings(mode="docker-compose"))


def test_local_mode_absent_key_without_optout_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even in dev, an absent key is NEVER a silent default to unsigned — without the explicit
    opt-out it raises. Revert-RED guard for the `not _unsigned_cards_opt_out()` clause."""
    with pytest.raises(RuntimeError, match=ALLOW_UNSIGNED_CARDS_ENV_VAR):
        _require_signer_or_fail_closed(_settings(mode="local"))


def test_local_mode_absent_key_with_explicit_optout_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev/test ergonomics: local mode + the EXPLICIT opt-out -> unsigned Cards (signer=None)."""
    monkeypatch.setenv(ALLOW_UNSIGNED_CARDS_ENV_VAR, "true")
    assert _require_signer_or_fail_closed(_settings(mode="local")) is None


@pytest.mark.parametrize("mode", ["local", "kubernetes", "anything"])
def test_present_key_always_returns_a_signer(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """A present, well-formed key yields a real `CardSigner` regardless of runtime mode."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    signer = _require_signer_or_fail_closed(_settings(mode=mode))
    assert isinstance(signer, CardSigner)


# --- The guard is wired into the composition ROOT (un-bypassable) ------------------------------


def test_build_auth_delegation_dispatcher_prod_absent_key_never_returns_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end at the composition root: in production runtime mode with no key,
    `build_auth_delegation_dispatcher` RAISES — no unsigned dispatcher object is ever returned, so
    there is nothing to `.delegate()` a forged Card against. `_build_tool_deps` is faked so the test
    is hermetic (no Postgres / no transports) and isolates the F2 guard as the thing that fires."""

    def _fake_tool_deps(_settings_obj: AgentRuntimeSettings) -> dict[str, Any]:
        return {"dmn": object(), "cibseven": object(), "audit_sink": object()}

    monkeypatch.setattr(a2a_composition, "_build_tool_deps", _fake_tool_deps)

    with pytest.raises(RuntimeError, match="Refusing to compose an unsigned dispatcher"):
        build_auth_delegation_dispatcher(_settings(mode="kubernetes"))
