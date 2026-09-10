"""Run one explicit PR356 ECS verification command with fresh, private output receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from scripts.dev.verification_consumer_paths import (
    VerificationRefusedError,
    create_output_directory,
    validate_inputs,
    verify_source,
    write_json_exclusive,
)

HEAD = "f38ec6ad2639ad288611dae240e227057754f93d"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Fresh output directory")
    parser.add_argument("--label", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        inputs = validate_inputs(
            source=args.source, evidence=args.evidence, output=args.output, expected_head=HEAD
        )
        valid_label = args.label and all(
            character in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in args.label
        )
        if not valid_label:
            raise VerificationRefusedError("label must use lowercase letters, digits, dash or underscore")
        if not 0 < args.timeout <= 180 or not args.command:
            raise VerificationRefusedError("command and bounded timeout are required")
        executable = Path(args.command[0]).expanduser().resolve(strict=True)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise VerificationRefusedError("command executable is unavailable")
        terraform_config = (inputs.evidence / "terraform-cli.tfrc").resolve(strict=True)
        if not terraform_config.is_file() or not terraform_config.is_relative_to(inputs.evidence):
            raise VerificationRefusedError("evidence Terraform CLI configuration is unavailable")
        output = create_output_directory(inputs.output)
        environment = {
            "PATH": "/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_CONFIG_FILE": os.devnull,
            "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
            "CHECKPOINT_DISABLE": "1",
            "TF_CLI_CONFIG_FILE": str(terraform_config),
            "TF_DATA_DIR": str(output / "terraform-runtime"),
        }
        old_umask = os.umask(0o077)
        started = time.time()
        stdout_path = output / f"{args.label}.stdout"
        stderr_path = output / f"{args.label}.stderr"
        try:
            with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
                result = subprocess.run(
                    [str(executable), *args.command[1:]],
                    cwd=inputs.source,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=args.timeout,
                    check=False,
                )
        finally:
            os.umask(old_umask)
        finished = time.time()
        verify_source(inputs.source, HEAD)
        receipt = {
            "argv": [str(executable), *args.command[1:]],
            "cwd": str(inputs.source),
            "environment": environment,
            "started_epoch": started,
            "finished_epoch": finished,
            "returncode": result.returncode,
            "source_sha": HEAD,
            "source_tree": inputs.tree,
            "evidence": str(inputs.evidence),
            "tool": {"path": str(executable), "sha256": hashlib.sha256(executable.read_bytes()).hexdigest()},
        }
        write_json_exclusive(output / f"{args.label}.json", receipt)
    except (OSError, ValueError, subprocess.TimeoutExpired, VerificationRefusedError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"label": args.label, "returncode": result.returncode, "output": str(output)}))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
