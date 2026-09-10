"""Run the maintained offline PR356 ECS interface probes against an explicit source checkout."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
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


def _declared_env(task_source: str, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    for name in tuple(os.environ):
        if name.startswith("WHATSAPP_"):
            monkeypatch.delenv(name)
    names = re.findall(r'\{\s*name\s*=\s*"(WHATSAPP_[A-Z_]+)"\s*,', task_source)
    for name in names:
        monkeypatch.setenv(name, "synthetic-offline-value")
    return set(names)


async def _sender_probe(server_type: type[Any], task_source: str, *, explicit_sender: bool) -> dict[str, Any]:
    monkeypatch = pytest.MonkeyPatch()
    try:
        names = _declared_env(task_source, monkeypatch)
        if not {"WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN"} <= names:
            raise AssertionError("task omits documented receiver settings")
        if explicit_sender:
            monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "offline-number-id")
            monkeypatch.setenv("WHATSAPP_TOKEN", "offline-not-a-real-credential")
        client = AsyncMock()
        response = MagicMock()
        response.json.return_value = {"messages": [{"id": "offline-result"}]}
        client.__aenter__.return_value = client
        client.post.return_value = response
        monkeypatch.setattr("httpx.AsyncClient", lambda **_: client)
        result = await server_type().send_message("synthetic-recipient", "synthetic-reply")
        if result != {"messages": [{"id": "offline-result"}]}:
            raise AssertionError("sender result shape changed")
        client.post.assert_awaited_once()
        if explicit_sender and not client.post.await_args.args[0].endswith("/offline-number-id/messages"):
            raise AssertionError("sender URL does not use the explicit phone-number binding")
        return {"case": "sender-explicit" if explicit_sender else "sender-documented", "status": "PASS"}
    finally:
        monkeypatch.undo()


def verify(source: Path) -> list[dict[str, Any]]:
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(source / "src"), str(source)]
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer  # noqa: PLC0415

    imported = Path(sys.modules[WhatsAppServer.__module__].__file__).resolve(strict=True)
    if not imported.is_relative_to(source):
        raise VerificationRefusedError("WhatsApp source imported from a different checkout")
    task = source / "deploy/aws-ecs/envs/dev-sa-east-1/service-webhook-receiver.tf"
    task_source = task.read_text()
    records = [
        asyncio.run(_sender_probe(WhatsAppServer, task_source, explicit_sender=False)),
        asyncio.run(_sender_probe(WhatsAppServer, task_source, explicit_sender=True)),
    ]
    enabled = re.search(r"enable_execute_command\s*=\s*(true|false)", task_source)
    readonly = re.search(r"readonlyRootFilesystem\s*=\s*(true|false)", task_source)
    if enabled is None or readonly is None or (enabled.group(1) == "true" and readonly.group(1) == "true"):
        raise AssertionError("ECS Exec and readonly filesystem remain incompatible")
    records.append({"case": "ecs-filesystem", "status": "PASS"})
    dockerfile = (source / "deploy/cibseven/Dockerfile").read_text()
    expression_match = re.search(r"RUN sed -i '([^']+)'", dockerfile)
    if expression_match is None:
        raise AssertionError("reviewed engine substitution is absent")
    expression = expression_match.group(1)
    for initial, accepted in (("true", True), ("false", True), (" true ", False), ("missing", False)):
        xml = "<bpm-platform><process-engine><properties>"
        if initial != "missing":
            xml += f'<property name="jobExecutorDeploymentAware">{initial}</property>'
        xml += "</properties></process-engine></bpm-platform>"
        transformed = subprocess.run(
            ["/usr/bin/sed", expression],
            input=xml,
            text=True,
            capture_output=True,
            timeout=15,
            check=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        with tempfile.TemporaryDirectory(prefix="pr356-maintained-engine-") as directory:
            after = Path(directory) / "bpm-platform.xml"
            after.write_text(transformed.stdout)
            guard = dockerfile[
                dockerfile.index('RUN grep -q \'<property name="jobExecutorDeploymentAware"') :
            ].split("\n\n")[0]
            guard = guard.removeprefix("RUN ").replace("/camunda/conf/bpm-platform.xml", str(after))
            checked = subprocess.run(
                ["/bin/sh", "-c", guard],
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
                env={"PATH": "/usr/bin:/bin"},
            )
        if (checked.returncode == 0) is not accepted:
            raise AssertionError(f"engine guard result changed for {initial!r}")
        disabled_property = '<property name="jobExecutorDeploymentAware">false</property>'
        if accepted and disabled_property not in transformed.stdout:
            raise AssertionError("engine substitution did not disable deployment-aware execution")
        records.append({"case": f"engine-{initial}", "status": "PASS"})
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
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError, VerificationRefusedError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "PASS", "checks": len(records), "output": str(inputs.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
