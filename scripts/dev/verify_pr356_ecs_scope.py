"""Re-run the PR356 ECS exact-scope proof against an explicit clean source checkout."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

from scripts.dev.verification_consumer_paths import (
    VerificationRefusedError,
    git,
    validate_inputs,
    verify_source,
    write_json_exclusive,
)

BASE = "18a90959187318cc92727652e57cacfe4b6c96aa"
HEAD = "f38ec6ad2639ad288611dae240e227057754f93d"
PARENT = "2d9682f3fc32042770e0a72d047aca1af65f4ed3"
EXPECTED_PATHS = {
    "deploy/aws-ecs/envs/dev-sa-east-1/secrets.tf",
    "deploy/aws-ecs/envs/dev-sa-east-1/service-webhook-receiver.tf",
    "tests/unit/ci/test_check_chart_env_reconciliation.py",
    "tests/unit/ci/test_webhook_ecs_runtime_contract.py",
}


def verify(source: Path) -> dict[str, Any]:
    paths = git(source, "diff", "--name-only", BASE, HEAD).decode().splitlines()
    if set(paths) != EXPECTED_PATHS:
        raise AssertionError("ECS source delta path set changed")
    file = "tests/unit/ci/test_check_chart_env_reconciliation.py"
    old = ast.parse(git(source, "show", f"{BASE}:{file}"))
    new = ast.parse((source / file).read_text())
    functions = {node.name: node for node in old.body if isinstance(node, ast.FunctionDef)}
    changed: list[str] = []
    for function in new.body:
        if not isinstance(function, ast.FunctionDef):
            continue
        previous = functions[function.name]
        if ast.dump(previous) == ast.dump(function):
            continue
        changed.append(function.name)
        assignments = [
            node
            for node in function.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "declared" for target in node.targets)
        ]
        prior = [
            node
            for node in previous.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "declared" for target in node.targets)
        ]
        if len(assignments) != 1 or len(prior) != 1:
            raise AssertionError("changed reconciliation test has unexpected assignment shape")
        right = assignments[0].value
        if not isinstance(right, ast.BinOp) or not isinstance(right.op, ast.Add):
            raise AssertionError("changed declaration is not the reviewed additive filter")
        if not isinstance(right.right, ast.ListComp):
            raise AssertionError("changed declaration has no reviewed list comprehension")
        comprehension = right.right
        if ast.unparse(comprehension.elt) != "ref" or len(comprehension.generators) != 1:
            raise AssertionError("changed declaration comprehension shape changed")
        generator = comprehension.generators[0]
        if ast.unparse(generator.iter) != "extract_declared_from_terraform(_TF_ROOT)":
            raise AssertionError("changed declaration reads an unexpected source")
        if len(generator.ifs) != 1:
            raise AssertionError("changed declaration filter count changed")
        target = "WHATSAPP_VERIFY_TOKEN" if "verify_token" in function.name else "WHATSAPP_TOKEN"
        if ast.unparse(generator.ifs[0]) != f"ref.name != '{target}'":
            raise AssertionError("changed declaration filters the wrong setting")
        assignments[0].value = prior[0].value
        if ast.dump(function) != ast.dump(previous):
            raise AssertionError("changed function differs beyond the reviewed declaration filter")
    if len(changed) != 2 or ast.dump(old) != ast.dump(new):
        raise AssertionError("reconciliation module differs beyond the two reviewed functions")
    file = "tests/unit/ci/test_webhook_ecs_runtime_contract.py"
    parent = git(source, "show", f"{PARENT}:{file}")
    current = (source / file).read_bytes()
    if parent.replace(b"WhatsAppSettings(_env_file=None)", b"WhatsAppSettings()") != current:
        raise AssertionError("typed successor test differs beyond the reviewed constructor edit")
    if git(source, "diff", "--name-only", PARENT, HEAD).decode().splitlines() != [file]:
        raise AssertionError("typed successor parent delta changed")
    return {
        "status": "PASS_EXACT_SCOPE",
        "head": HEAD,
        "base": BASE,
        "paths": paths,
        "helm_test_function_count": len(functions),
        "changed_only_declared_assignment_in": changed,
        "all_other_helm_test_asts_unchanged": True,
        "typed_successor_only_change": (
            "Remove redundant _env_file=None from test constructor; no model env_file is configured"
        ),
        "global_reconciliation_cli_unchanged": True,
        "limitation": (
            "Global CLI remains a declared-somewhere inventory; target-scoped mutation tests are not "
            "a new per-deployment CLI gate"
        ),
    }


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
        result = verify(inputs.source)
        verify_source(inputs.source, HEAD)
        result.update(source_tree=inputs.tree, evidence=str(inputs.evidence))
        write_json_exclusive(inputs.output, result)
    except (AssertionError, OSError, ValueError, VerificationRefusedError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "PASS_EXACT_SCOPE", "output": str(inputs.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
