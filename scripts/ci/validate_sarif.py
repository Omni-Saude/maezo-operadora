#!/usr/bin/env python3
"""Validate SARIF files produced by the house pipeline — fail-closed, before anything consumes them.

WHY THIS SCRIPT EXISTS
----------------------
The SARIF path on this repository ("GHAS-substitute", W7 7.1) has three legs:

  1. PRODUCE  — ``.github/workflows/security.yml`` runs CodeQL (python, java-kotlin) and Trivy
     (IaC), each emitting a SARIF file (``sarif-results/*.sarif``, ``trivy-iac.sarif``).
  2. INGEST   — the SARIF is POSTed to the real code-scanning API
     (``POST /repos/{repo}/code-scanning/sarifs``, gzip+base64) inside a try-and-classify step:
     accepted -> results live in the Security tab; rejected with the exact "Code Security must be
     enabled" gate message -> visible skip; any other failure -> job fails.
  3. REVIEW   — independently of the upload, the SARIF is published as a workflow artifact and
     rendered into the step summary by ``scripts/ci/summarize_sarif.py``.

Legs 2 and 3 both consume the file, but nothing checked the artifact itself. ``summarize_sarif``
only fails on un-parseable JSON; a SARIF that parses yet violates the format (missing ``version``,
``runs`` not a list, a result without ``ruleId``, a location without ``artifactLocation.uri``) would
render a silently wrong summary and, worse, be rejected by the code-scanning API for a reason that
does NOT match the documented GHAS gate message — i.e. it would fail the job via the fail-closed
``else`` branch with a confusing diagnosis instead of a clear "the SARIF is malformed" one. This
script closes that gap: validate BEFORE ingest/publish.

CONTRACT (fail-closed). Exit 0 only when EVERY file passes; any violation — including a file that
does not exist or does not parse — is printed and makes the exit 1. Like ``summarize_sarif.py``,
this NEVER looks at severities to decide anything about the code being scanned: validity of the
SARIF container is the only concern; gating on findings is the caller's job.

STRUCTURAL CHECKS are the invariants the two house consumers actually rely on:
  - top-level ``version`` == "2.1.0" (both producers emit it; the code-scanning API requires it);
  - ``$schema``, when present, references the SARIF 2.1.0 schema;
  - ``runs`` is a non-empty array of objects, each with ``tool.driver.name``;
  - every result has a non-empty string ``ruleId`` and, when present, a ``level`` in the SARIF
    enum {"none", "note", "warning", "error"};
  - every result location resolves to ``physicalLocation.artifactLocation.uri`` (what both the
    Security tab and the step-summary renderers need to name a finding).

``--schema FILE`` additionally runs the FULL official JSON-Schema (the OASIS SARIF 2.1.0 schema is
draft-04; the validator class is picked via ``jsonschema.validators.validator_for``) on top of the
structural checks, for callers that want byte-level conformance (e.g. a CI wiring decision, which
is owner-gated and therefore NOT made here).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: SARIF 2.1.0 is the only version any producer in this pipeline emits and the only one the
#: code-scanning ingestion accepts, so anything else is a defect, not a compatibility question.
SARIF_VERSION = "2.1.0"

#: ``result.level`` enum from the SARIF 2.1.0 spec (§3.27.10).
LEVELS = ("none", "note", "warning", "error")


def _structural_errors(doc: Any) -> list[str]:
    """Return every structural violation of one parsed SARIF document (empty list = valid)."""
    errors: list[str] = []

    if not isinstance(doc, dict):
        return [f"top level must be an object, got {type(doc).__name__}"]

    version = doc.get("version")
    if version != SARIF_VERSION:
        errors.append(f"$version must be {SARIF_VERSION!r}, got {version!r}")

    schema_ref = doc.get("$schema")
    if schema_ref is not None and "sarif" not in str(schema_ref).lower():
        errors.append(f"$schema must reference the SARIF schema, got {schema_ref!r}")

    runs = doc.get("runs")
    if not isinstance(runs, list) or not runs:
        errors.append(f"runs must be a non-empty array, got {runs!r}")
        return errors

    for i, run in enumerate(runs):
        where = f"runs[{i}]"
        if not isinstance(run, dict):
            errors.append(f"{where} must be an object, got {type(run).__name__}")
            continue

        tool = run.get("tool")
        if not isinstance(tool, dict):
            errors.append(f"{where}.tool must be an object, got {tool!r}")
            continue
        driver = tool.get("driver")
        if not isinstance(driver, dict):
            errors.append(f"{where}.tool.driver must be an object, got {driver!r}")
        elif not isinstance(driver.get("name"), str) or not driver["name"]:
            errors.append(f"{where}.tool.driver.name must be a non-empty string, got {driver.get('name')!r}")

        results = run.get("results", [])
        if not isinstance(results, list):
            errors.append(f"{where}.results must be an array when present, got {results!r}")
            continue
        for j, result in enumerate(results):
            rwhere = f"{where}.results[{j}]"
            if not isinstance(result, dict):
                errors.append(f"{rwhere} must be an object, got {type(result).__name__}")
                continue
            rule_id = result.get("ruleId")
            if not isinstance(rule_id, str) or not rule_id:
                errors.append(f"{rwhere}.ruleId must be a non-empty string, got {rule_id!r}")
            level = result.get("level")
            if level is not None and level not in LEVELS:
                errors.append(f"{rwhere}.level must be one of {LEVELS}, got {level!r}")

            locations = result.get("locations", [])
            if not isinstance(locations, list):
                errors.append(f"{rwhere}.locations must be an array when present, got {locations!r}")
                continue
            for k, location in enumerate(locations):
                lwhere = f"{rwhere}.locations[{k}]"
                if not isinstance(location, dict):
                    errors.append(f"{lwhere} must be an object, got {type(location).__name__}")
                    continue
                physical = location.get("physicalLocation")
                if not isinstance(physical, dict):
                    errors.append(f"{lwhere}.physicalLocation must be an object, got {physical!r}")
                    continue
                artifact = physical.get("artifactLocation")
                artifact_where = f"{lwhere}.physicalLocation.artifactLocation"
                if not isinstance(artifact, dict):
                    errors.append(f"{artifact_where} must be an object, got {artifact!r}")
                elif not isinstance(artifact.get("uri"), str) or not artifact["uri"]:
                    errors.append(
                        f"{artifact_where}.uri must be a non-empty string, got {artifact.get('uri')!r}"
                    )

    return errors


def _schema_errors(doc: Any, schema: dict[str, Any]) -> list[str]:
    """Validate ``doc`` against the full JSON-Schema; empty list = conformant."""
    from jsonschema.exceptions import SchemaError
    from jsonschema.validators import validator_for

    validator_class = validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except SchemaError as exc:  # a broken schema is a caller defect, not a SARIF defect
        raise SystemExit(f"--schema {exc.message[:200]}") from exc
    return [
        f"schema: {list(error.path)}: {error.message}" for error in validator_class(schema).iter_errors(doc)
    ]


def validate_file(path: Path, schema_path: Path | None) -> list[str]:
    """Return every violation of one SARIF file (empty list = valid). Missing/unparseable = invalid."""
    if not path.is_file():
        return [
            f"{path}: file not found (fail-closed: a missing scan result must never read as a valid scan)"
        ]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return [f"{path}: not valid JSON: {exc}"]

    errors = _structural_errors(doc)
    if schema_path is not None and not errors:
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SystemExit(f"--schema: {schema_path} is not valid JSON: {exc}") from exc
        errors.extend(_schema_errors(doc, schema))
    return [f"{path}: {error}" for error in errors]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate SARIF file(s) produced by the scan (fail-closed; see --help for the contract).",
    )
    parser.add_argument(
        "--sarif",
        required=True,
        action="append",
        help="path to a SARIF file to validate (repeat for several lanes' files)",
    )
    parser.add_argument(
        "--schema",
        default=None,
        help="optional JSON-Schema file (e.g. the official OASIS SARIF 2.1.0 schema) for full "
        "conformance checking on top of the structural invariants",
    )
    args = parser.parse_args(argv)

    schema_path = Path(args.schema) if args.schema else None
    all_errors: list[str] = []
    for raw_path in args.sarif:
        all_errors.extend(validate_file(Path(raw_path), schema_path))

    if all_errors:
        for error in all_errors:
            print(f"INVALID SARIF: {error}", file=sys.stderr)
        print(
            f"{len(all_errors)} SARIF violation(s) across {len(args.sarif)} file(s) — failing closed.",
            file=sys.stderr,
        )
        return 1

    print(f"{len(args.sarif)} SARIF file(s) valid" + (f" against {schema_path}" if schema_path else "") + ".")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
