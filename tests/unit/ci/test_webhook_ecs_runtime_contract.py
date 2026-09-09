"""PR356 ECS receiver wiring: ADR-0035 and the webhook/sender settings contract."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from maezo.tools.mcp_whatsapp.server import WhatsAppSettings

_TASK = Path(__file__).resolve().parents[3] / "deploy/aws-ecs/envs/dev-sa-east-1/service-webhook-receiver.tf"


def test_ecs_receiver_supplies_sender_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _TASK.read_text()
    for name in tuple(os.environ):
        if name.startswith("WHATSAPP_"):
            monkeypatch.delenv(name)
    mappings = dict(
        re.findall(
            r'\{\s*name\s*=\s*"(WHATSAPP_[A-Z_]+)",\s*valueFrom\s*=\s*'
            r'"\$\{aws_secretsmanager_secret\.whatsapp_meta\.arn\}:([a-z_]+)::"\s*\}',
            source,
        )
    )
    assert mappings == {
        "WHATSAPP_APP_SECRET": "app_secret",
        "WHATSAPP_VERIFY_TOKEN": "verify_token",
        "WHATSAPP_PHONE_NUMBER_ID": "phone_number_id",
        "WHATSAPP_TOKEN": "waba_token",
    }
    for name, field in mappings.items():
        monkeypatch.setenv(name, "offline-fixture-" + field)
    settings = WhatsAppSettings(_env_file=None)
    assert settings.phone_number_id == "offline-fixture-phone_number_id"
    assert settings.whatsapp_token == "offline-fixture-waba_token"


def test_ecs_receiver_retains_readonly_root_without_unsupported_exec() -> None:
    source = _TASK.read_text()
    readonly = re.search(r"readonlyRootFilesystem\s*=\s*(true|false)", source)
    execute = re.search(r"enable_execute_command\s*=\s*(true|false)", source)
    assert readonly and readonly.group(1) == "true"
    assert execute and execute.group(1) == "false"
