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


def test_construct_by_field_name_populate_by_name() -> None:
    settings = WhatsAppWebhookSettings(app_secret="s", verify_token="v", tenant_id="omni")
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
# Env-var NAMES (gatekeeper MAJOR-1). The sibling MCP client
# (`tools/mcp_whatsapp/server.py`) read `WHATSAPP_WHATSAPP_TOKEN` because
# `env_prefix="WHATSAPP_"` was applied to fields already named `whatsapp_*`.
# This class was already correct — it uses explicit aliases and no prefix —
# but "already correct" was an assertion nobody had pinned. These two tests
# pin it, so the two WhatsApp settings classes cannot drift apart again.
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
