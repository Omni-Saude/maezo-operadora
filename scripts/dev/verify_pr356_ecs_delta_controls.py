"""Run maintained PR356 ECS propagation and counterfactual controls on an explicit checkout."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from scripts.dev.verification_consumer_paths import (
    VerificationRefusedError,
    validate_inputs,
    verify_source,
    write_json_exclusive,
)

HEAD = "f38ec6ad2639ad288611dae240e227057754f93d"


def _load_permanent(source: Path) -> Any:
    path = source / "tests/unit/ci/test_webhook_ecs_runtime_contract.py"
    specification = importlib.util.spec_from_file_location("maintained_pr356_ecs_permanent", path)
    if specification is None or specification.loader is None:
        raise VerificationRefusedError("permanent ECS control cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


async def _propagation_probe(server_type: type[Any], task_source: str) -> dict[str, Any]:
    monkeypatch = pytest.MonkeyPatch()
    try:
        for name in tuple(os.environ):
            if name.startswith("WHATSAPP_"):
                monkeypatch.delenv(name)
        mappings = re.findall(
            r'\{\s*name\s*=\s*"(WHATSAPP_[A-Z_]+)",\s*valueFrom\s*=\s*'
            r'"\$\{aws_secretsmanager_secret\.whatsapp_meta\.arn\}:([a-z_]+)::"\s*\}',
            task_source,
        )
        fields = {
            "app_secret": "offline-app",
            "verify_token": "offline-verify",
            "phone_number_id": "offline-phone",
            "waba_token": "offline-waba",
        }
        if len(mappings) != 4:
            raise AssertionError("exact ECS secret mapping count changed")
        for name, field in mappings:
            monkeypatch.setenv(name, fields[field])
        client = AsyncMock()
        client.__aenter__.return_value = client
        response = MagicMock()
        response.json.return_value = {"messages": [{"id": "offline-message-id"}]}
        client.post.return_value = response
        monkeypatch.setattr("httpx.AsyncClient", lambda **_: client)
        result = await server_type().send_message("offline-recipient", "offline-reply")
        if result["messages"][0]["id"] != "offline-message-id":
            raise AssertionError("sender response changed")
        call = client.post.await_args
        if not call.args[0].endswith("/offline-phone/messages"):
            raise AssertionError("phone-number field did not reach sender URL")
        if call.kwargs["headers"]["Authorization"] != "Bearer offline-waba":
            raise AssertionError("WABA token did not reach authorization header")
        return {"case": "exact-field-propagation", "status": "PASS"}
    finally:
        monkeypatch.undo()


def _mutate(original: str, mutation: str) -> str:
    if mutation == "missing-phone":
        return re.sub(r'^.*\{ name = "WHATSAPP_PHONE_NUMBER_ID".*\n', "", original, flags=re.M)
    if mutation == "missing-token":
        return re.sub(r'^.*\{ name = "WHATSAPP_TOKEN".*\n', "", original, flags=re.M)
    if mutation == "wrong-field":
        return original.replace(":waba_token::", ":verify_token::")
    if mutation == "exec-on":
        return original.replace("enable_execute_command = false", "enable_execute_command = true")
    if mutation == "readonly-off":
        return original.replace("readonlyRootFilesystem = true", "readonlyRootFilesystem = false")
    raise AssertionError("unknown mutation")


def verify(source: Path) -> list[dict[str, Any]]:
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(source / "src"), str(source)]
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer  # noqa: PLC0415

    imported = Path(sys.modules[WhatsAppServer.__module__].__file__).resolve(strict=True)
    if not imported.is_relative_to(source):
        raise VerificationRefusedError("WhatsApp source imported from a different checkout")
    permanent = _load_permanent(source)
    task = source / "deploy/aws-ecs/envs/dev-sa-east-1/service-webhook-receiver.tf"
    original = task.read_text()
    records = [asyncio.run(_propagation_probe(WhatsAppServer, original))]
    with tempfile.TemporaryDirectory(prefix="pr356-maintained-mutants-") as directory:
        for mutation in ("missing-phone", "missing-token", "wrong-field", "exec-on", "readonly-off"):
            mutated = _mutate(original, mutation)
            if mutated == original:
                raise AssertionError(f"mutation {mutation} did not change the fixture")
            path = Path(directory) / f"{mutation}.tf"
            path.write_text(mutated)
            monkeypatch = pytest.MonkeyPatch()
            try:
                monkeypatch.setattr(permanent, "_TASK", path)
                try:
                    if mutation in {"exec-on", "readonly-off"}:
                        permanent.test_ecs_receiver_retains_readonly_root_without_unsupported_exec()
                    else:
                        permanent.test_ecs_receiver_supplies_sender_settings(monkeypatch)
                except AssertionError:
                    pass
                else:
                    raise AssertionError(f"permanent controls accepted {mutation}")
            finally:
                monkeypatch.undo()
            records.append({"case": mutation, "status": "REJECTED_AS_EXPECTED"})
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        inputs = validate_inputs(
            source=args.source, evidence=args.evidence, output=args.output, expected_head=HEAD
        )
        records = verify(inputs.source)
        verify_source(inputs.source, HEAD)
        value = {
            "status": "PASS",
            "head": HEAD,
            "source_tree": inputs.tree,
            "evidence": str(inputs.evidence),
            "checks": records,
        }
        write_json_exclusive(inputs.output, value)
    except (AssertionError, OSError, ValueError, VerificationRefusedError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "PASS", "checks": len(records), "output": str(inputs.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
