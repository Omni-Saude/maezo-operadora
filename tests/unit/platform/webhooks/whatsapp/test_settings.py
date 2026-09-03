"""Unit tests for `maezo.platform.webhooks.whatsapp.settings.WhatsAppWebhookSettings` (T1.6)."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings


def _clear_whatsapp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("WHATSAPP_"):
            monkeypatch.delenv(name, raising=False)


def test_fail_closed_missing_app_secret_and_verify_token() -> None:
    """Constraint 2 — boot fails on unloadable config. No default: a webhook receiver that
    silently accepted requests with an unverifiable signature would be a real security defect."""
    with pytest.raises(ValidationError):
        WhatsAppWebhookSettings()


def test_fail_closed_missing_verify_token_only() -> None:
    with pytest.raises(ValidationError):
        WhatsAppWebhookSettings(WHATSAPP_APP_SECRET="secret")


def test_construct_with_required_fields() -> None:
    settings = WhatsAppWebhookSettings(WHATSAPP_APP_SECRET="secret", WHATSAPP_VERIFY_TOKEN="verify-token")
    assert settings.app_secret == "secret"
    assert settings.verify_token == "verify-token"
    assert settings.tenant_id == "amh"
    assert settings.health_port == 8080


def test_construct_by_field_name() -> None:
    """By-field-name CONSTRUCTION (no `populate_by_name` config flag involved — see the
    class-level comment in `settings.py`) is what `test_app.py` and `test_service.py` rely on
    for every field, `app_secret`/`verify_token` included; assert the round-trip, not just that
    construction succeeds."""
    settings = WhatsAppWebhookSettings(app_secret="s", verify_token="v", tenant_id="omni")
    assert settings.app_secret == "s"
    assert settings.verify_token == "v"
    assert settings.tenant_id == "omni"


def test_fail_closed_empty_app_secret() -> None:
    """An EMPTY secret is not a configured secret: `WHATSAPP_APP_SECRET=""` used to pass
    validation and key `verify_hub_signature`'s HMAC with b"" — a signature anyone can forge."""
    with pytest.raises(ValidationError):
        WhatsAppWebhookSettings(WHATSAPP_APP_SECRET="", WHATSAPP_VERIFY_TOKEN="verify-token")


def test_fail_closed_empty_verify_token() -> None:
    """Same, on the handshake side: an empty configured token made `?hub.verify_token=`
    (or a missing param) a VALID registration handshake for any caller."""
    with pytest.raises(ValidationError):
        WhatsAppWebhookSettings(WHATSAPP_APP_SECRET="secret", WHATSAPP_VERIFY_TOKEN="")


# ---------------------------------------------------------------------------
# Env-var NAMES (gatekeeper MAJOR-1, then residual D-2). The sibling MCP client
# (`tools/mcp_whatsapp/server.py`) read `WHATSAPP_WHATSAPP_TOKEN` because
# `env_prefix="WHATSAPP_"` was applied to fields already named `whatsapp_*`.
# This class never had THAT defect — it uses explicit aliases and no prefix —
# but a follow-up gate pass found and reproduced the sibling defect in this
# class: `populate_by_name=True` (no `env_prefix`) let the BARE, un-prefixed
# `APP_SECRET`/`VERIFY_TOKEN` bind too, because those two fields' Python names
# do not case-fold to their canonical `WHATSAPP_`-prefixed env name (unlike
# `tenant_id`/`phi_hmac_key`/`whatsapp_token`, whose names already do — an
# earlier claim that the whole class was "already correct" did not hold for
# these two, and was itself never pinned by a test). The four tests below pin
# canonical-binds, doubled-inert, bare-inert and by-name-construction, so the
# two WhatsApp settings classes cannot drift apart again in either direction.
# ---------------------------------------------------------------------------


def test_reads_the_canonical_whatsapp_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact names Helm injects (`deployment-webhook-receiver.yaml:38,47,52`)."""
    _clear_whatsapp_env(monkeypatch)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "canonical-secret")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "canonical-verify")
    monkeypatch.setenv("WHATSAPP_TOKEN", "canonical-waba-token")

    settings = WhatsAppWebhookSettings()

    assert settings.app_secret == "canonical-secret"
    assert settings.verify_token == "canonical-verify"
    assert settings.whatsapp_token == "canonical-waba-token"


def test_does_not_read_the_doubled_whatsapp_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """The doubled spelling must leave the REQUIRED fields unset — i.e. fail closed at
    boot (CrashLoopBackOff), never silently accept an unverifiable signature."""
    _clear_whatsapp_env(monkeypatch)
    monkeypatch.setenv("WHATSAPP_WHATSAPP_APP_SECRET", "doubled-secret")
    monkeypatch.setenv("WHATSAPP_WHATSAPP_VERIFY_TOKEN", "doubled-verify")

    with pytest.raises(ValidationError):
        WhatsAppWebhookSettings()


def test_does_not_read_bare_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reproduces the residual gate finding (D-2): with only the BARE, un-prefixed
    `APP_SECRET`/`VERIFY_TOKEN` exported (no `WHATSAPP_` prefix anywhere), the class must
    fail closed — never silently accept a colliding var from some unrelated sidecar/base-image
    as the real credential. `TENANT_ID` is deliberately NOT part of this reproduction: that bare
    name *is* the field's actual canonical alias (there is no `WHATSAPP_TENANT_ID`), so it is
    meant to bind."""
    _clear_whatsapp_env(monkeypatch)
    monkeypatch.delenv("APP_SECRET", raising=False)
    monkeypatch.delenv("VERIFY_TOKEN", raising=False)
    monkeypatch.setenv("APP_SECRET", "bare-secret")
    monkeypatch.setenv("VERIFY_TOKEN", "bare-verify")

    with pytest.raises(ValidationError):
        WhatsAppWebhookSettings()


def test_settings_never_render_the_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """`repr`/`str`/`model_dump`/`model_dump_json` used to print all four secrets
    (`app_secret`, `verify_token`, `whatsapp_token`, `phi_hmac_key`) verbatim (plain `str`
    fields, `repr=False` never applied). `phi_hmac_key` is the ADR-0035 vault key that makes
    every beneficiary pseudonym irreversible — the most sensitive value in this class. Mirrors
    `tests/unit/tools/test_mcp_whatsapp.py::test_settings_never_render_the_credentials` on the
    sibling class."""
    _clear_whatsapp_env(monkeypatch)
    secret = "seg-" + "z" * 24
    settings = WhatsAppWebhookSettings(
        app_secret=secret,
        verify_token=secret,
        whatsapp_token=secret,
        phi_hmac_key=secret,
        tenant_id="bare-tenant",
    )

    renderings = {
        "repr": repr(settings),
        "str": str(settings),
        "model_dump": repr(settings.model_dump()),
        "model_dump_json": settings.model_dump_json(),
    }

    for label, rendered in renderings.items():
        assert secret not in rendered, f"{label} leaks the credential: {rendered}"
        leaked = [
            secret[start : start + size]
            for size in range(4, len(secret) + 1)
            for start in range(0, len(secret) - size + 1)
            if secret[start : start + size] in rendered
        ]
        assert leaked == [], f"{label} leaks credential substrings: {leaked}"
    # Non-secret fields stay legible — this is redaction, not blindness.
    assert "bare-tenant" in renderings["repr"]
