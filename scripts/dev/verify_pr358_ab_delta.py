"""Re-run the PR358 AB exact-delta controls against an explicit clean source checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from scripts.dev.verification_consumer_paths import (
    VerificationRefusedError,
    git,
    validate_inputs,
    verify_source,
    write_json_exclusive,
)

BASE = "3ba689781d442b48626cebed4171665018580e4f"
MAIN = "dd5caca20cf00d40efa570a4c96d70730cb1e947"
HEAD = "852d70588e732545471d052ebc2fa7fbc5840d5c"
EXPECTED_AUTHOR_FILES = 72


def _verified_evidence_files(evidence: Path) -> int:
    manifest = json.loads((evidence / "MANIFEST.json").read_text())
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != EXPECTED_AUTHOR_FILES:
        raise VerificationRefusedError("author evidence manifest membership changed")
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise VerificationRefusedError("author evidence manifest entry is invalid")
        candidate = (evidence / item["path"]).resolve(strict=True)
        if not candidate.is_relative_to(evidence) or not candidate.is_file() or candidate.is_symlink():
            raise VerificationRefusedError("author evidence member is unsafe")
        if hashlib.sha256(candidate.read_bytes()).hexdigest() != item.get("sha256"):
            raise VerificationRefusedError("author evidence member digest changed")
    return len(files)


def _run_shell_controls(source: Path, groups: set[str]) -> list[dict[str, Any]]:
    script = source / "deploy/cibseven/configure-group-whitelist.sh"
    valid = """<bpm-platform>
<job-executor><properties><property name="unchanged">x</property></properties></job-executor>
<process-engine name="default">
<properties>
<property name="generalResourceWhitelistPattern">[a-z]+</property>
<property name="userResourceWhitelistPattern">user[0-9]+</property>
<property name="tenantResourceWhitelistPattern">tenant[0-9]+</property>
<property name="authorizationEnabled">true</property>
</properties>
</process-engine>
</bpm-platform>
"""
    variants = {
        "valid": valid,
        "existing_narrow_group": valid.replace(
            "<properties>\n",
            '<properties>\n<property name="groupResourceWhitelistPattern">old</property>\n',
        ),
        "second_engine": valid.replace(
            "</bpm-platform>",
            '<process-engine name="other">\n<properties>\n</properties>\n</process-engine>\n</bpm-platform>',
        ),
        "no_properties": valid.replace("<properties>\n", "<missing-properties>\n"),
        "non_default": valid.replace('<process-engine name="default">', '<process-engine name="nondefault">'),
        "unclosed_engine": valid.replace("</process-engine>", ""),
    }
    records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="pr358-maintained-xml-") as directory:
        for label, text in variants.items():
            path = Path(directory) / "bpm-platform.xml"
            path.write_text(text)
            path.chmod(0o640)
            before = path.read_bytes()
            info = path.stat()
            run = subprocess.run(
                ["/bin/sh", str(script), str(path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=15,
                env={"PATH": "/usr/bin:/bin"},
            )
            if label == "valid":
                if run.returncode:
                    raise AssertionError("valid whitelist transformation refused")
                xml = ET.fromstring(path.read_bytes())
                properties = xml.findall("./process-engine/properties/property")
                added = [item for item in properties if item.get("name") == "groupResourceWhitelistPattern"]
                if len(added) != 1 or added[0].text is None:
                    raise AssertionError("group whitelist was not added exactly once")
                pattern = added[0].text
                stripped = re.sub(
                    rb'^.*<property name="groupResourceWhitelistPattern">.*</property>\n',
                    b"",
                    path.read_bytes(),
                    flags=re.M,
                )
                if stripped != before:
                    raise AssertionError("whitelist transformation changed adjacent XML")
                after_info = path.stat()
                if after_info.st_mode != info.st_mode or after_info.st_uid != info.st_uid:
                    raise AssertionError("whitelist transformation changed file custody")
                accepted = groups | {
                    "plantao-clinico",
                    "enfermagem-triagem",
                    "atendimento-humano",
                    "juridico-privacidade",
                    "aprovador-N1",
                    "dpo",
                    "camunda-admin",
                }
                if any(re.fullmatch(pattern, group) is None for group in accepted):
                    raise AssertionError("canonical group rejected by generated whitelist")
                rejected = ["-a", "a-", "a--b", "a_b", "a.b", "a/b", "a b", "a,b", "a\n", "${a}", "á", ""]
                if any(re.fullmatch(pattern, group) is not None for group in rejected):
                    raise AssertionError("invalid group accepted by generated whitelist")
                retained = {
                    item.get("name"): item.text
                    for item in properties
                    if item.get("name") != "groupResourceWhitelistPattern"
                }
                original = {
                    item.get("name"): item.text
                    for item in ET.fromstring(before).findall("./process-engine/properties/property")
                }
                if retained != original:
                    raise AssertionError("existing XML properties changed")
                after = path.read_bytes()
                again = subprocess.run(
                    ["/bin/sh", str(script), str(path)],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=15,
                    env={"PATH": "/usr/bin:/bin"},
                )
                if not again.returncode or path.read_bytes() != after:
                    raise AssertionError("repeat whitelist transformation did not fail closed")
            elif not run.returncode or path.read_bytes() != before:
                raise AssertionError(f"invalid XML case {label} did not fail closed")
            records.append({"case": label, "exit": run.returncode, "verified": True})
    return records


def verify(source: Path, evidence: Path) -> dict[str, Any]:
    verified_files = _verified_evidence_files(evidence)
    changed = git(source, "diff", "--name-only", BASE, HEAD).decode().splitlines()
    owned = (evidence / "OWNED-PATHS.txt").read_text().splitlines()
    if set(changed) != set(owned):
        raise AssertionError("source delta differs from reviewed ownership")
    restored = [
        path
        for path in changed
        if git(source, "ls-tree", MAIN, "--", path)
        and git(source, "show", f"{HEAD}:{path}") == git(source, "show", f"{MAIN}:{path}")
    ]
    if len(restored) != 32:
        raise AssertionError("exact main restoration count changed")
    process_paths = git(source, "ls-tree", "-r", "--name-only", MAIN, "spec/processes").decode().splitlines()
    if not all(
        git(source, "show", f"{MAIN}:{path}") == git(source, "show", f"{HEAD}:{path}")
        for path in process_paths
    ):
        raise AssertionError("process artifacts differ from reviewed main")
    groups: set[str] = set()
    expressions: set[str] = set()
    for path in process_paths:
        if not path.endswith(".bpmn"):
            continue
        for element in ET.fromstring(git(source, "show", f"{HEAD}:{path}")).iter():
            value = element.get("{http://camunda.org/schema/1.0/bpmn}candidateGroups", "")
            if "${" in value:
                expressions.add(value)
            elif value:
                groups.update(item.strip() for item in value.split(","))
    if len(groups) != 27 or len(expressions) != 4:
        raise AssertionError("candidate-group inventory changed")
    bootstrap_delta = git(
        source,
        "diff",
        "--ignore-space-at-eol",
        MAIN,
        HEAD,
        "--",
        "src/maezo/platform/engine_bootstrap/bootstrap.py",
    ).decode()
    for path in ("deploy/aws-ecs/envs/dev-sa-east-1/variables.tf", ".github/CODEOWNERS"):
        if git(source, "show", f"{HEAD}:{path}") != git(source, "show", f"{BASE}:{path}"):
            raise AssertionError(f"unowned path changed: {path}")
    dockerfile = source / "deploy/cibseven/Dockerfile"
    if not dockerfile.read_bytes().startswith(git(source, "show", f"{BASE}:deploy/cibseven/Dockerfile")):
        raise AssertionError("Dockerfile no longer retains the reviewed base")
    shell_controls = _run_shell_controls(source, groups)
    return {
        "status": "PASS",
        "head": HEAD,
        "base": BASE,
        "main": MAIN,
        "author_manifest_verified_files": verified_files,
        "owned_paths": len(changed),
        "exact_main_restorations": restored,
        "all_process_artifacts_main_byte_equal": len(process_paths),
        "static_groups": sorted(groups),
        "dynamic_expressions": sorted(expressions),
        "bootstrap_delta": bootstrap_delta,
        "unowned_variables_codeowners_unchanged": True,
        "independent_shell_controls": shell_controls,
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
        result = verify(inputs.source, inputs.evidence)
        verify_source(inputs.source, HEAD)
        result["source_tree"] = inputs.tree
        result["evidence"] = str(inputs.evidence)
        write_json_exclusive(inputs.output, result)
    except (AssertionError, OSError, ValueError, ET.ParseError, VerificationRefusedError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "PASS", "output": str(inputs.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
