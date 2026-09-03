"""Unit tests for `maezo.platform.webhooks.whatsapp.settings.WhatsAppWebhookSettings` (T1.6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings


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
