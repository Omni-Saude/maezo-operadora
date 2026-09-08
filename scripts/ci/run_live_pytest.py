"""Executa uma lane pytest live com evidência verificável e falha fechada.

O subcomando ``collect`` fixa a coleção, as identidades JUnit, os marcadores e os
hashes das fontes antes de subir os serviços. O subcomando ``run`` executa a
mesma seleção com prova das fases e de entrada no corpo, confronta o resultado
com o manifesto fixado e grava um relatório de validação legível por máquina.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from scripts.ci.pytest_execution_evidence import (
        SCHEMA_VERSION,
        EvidencePlugin,
        redact,
        validate_execution,
    )
else:
    from pytest_execution_evidence import SCHEMA_VERSION, EvidencePlugin, redact, validate_execution


def _pytest_args(raw: Sequence[str]) -> list[str]:
    args = list(raw)
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        raise ValueError("argumentos do pytest ausentes depois de `--`")
    forbidden = ("--collect-only", "--junitxml", "--junit-xml")
    if any(arg == flag or arg.startswith(f"{flag}=") for arg in args for flag in forbidden):
        raise ValueError("o wrapper controla --collect-only e --junitxml")
    return args


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON deve ser objeto: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _remove_stale(*paths: Path) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def _publish_sanitized_junit(private_path: Path, public_path: Path) -> None:
    """Publica XML válido sem texto sensível e sem mudar a identidade dos casos."""
    tree = ET.parse(private_path)
    for element in tree.getroot().iter():
        for name, value in tuple(element.attrib.items()):
            if name not in {"classname", "name"}:
                element.set(name, redact(value))
        if element.text:
            element.text = redact(element.text)
        if element.tail:
            element.tail = redact(element.tail)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(public_path, encoding="utf-8", xml_declaration=True)


def collect(output: Path, root: Path, raw_pytest_args: Sequence[str]) -> int:
    args = _pytest_args(raw_pytest_args)
    _remove_stale(output)
    plugin = EvidencePlugin(output=output, root=root)
    pytest_rc = int(pytest.main([*args, "--collect-only"], plugins=[plugin]))
    if pytest_rc != 0:
        return pytest_rc
    try:
        evidence = _read_json(output)
        items = evidence["collection"]
        nodeids = [item["nodeid"] for item in items]
        if (
            evidence.get("schema") != SCHEMA_VERSION
            or evidence.get("finished") is not True
            or evidence.get("pytest_exitstatus") != pytest_rc
            or evidence.get("reports") != []
            or not nodeids
            or len(nodeids) != len(set(nodeids))
        ):
            raise ValueError("manifesto de coleta vazio, duplicado, incompleto ou inconsistente")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[live-pytest] FAIL coleta: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    print(f"[live-pytest] coleta fixada: {len(nodeids)} casos em {output}")
    return 0


def run(
    expected_path: Path,
    evidence_path: Path,
    junit_path: Path,
    validation_path: Path,
    root: Path,
    raw_pytest_args: Sequence[str],
) -> int:
    args = _pytest_args(raw_pytest_args)
    private_junit_path = junit_path.with_name(f".{junit_path.name}.private")
    _remove_stale(evidence_path, private_junit_path, junit_path, validation_path)
    try:
        expected = _read_json(expected_path)
        expected_items = expected["collection"]
        if (
            expected.get("schema") != SCHEMA_VERSION
            or expected.get("finished") is not True
            or expected.get("pytest_exitstatus") != 0
            or expected.get("reports") != []
            or not isinstance(expected_items, list)
            or not expected_items
        ):
            raise ValueError("manifesto de coleta não é uma coleta completa e não vazia")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[live-pytest] FAIL manifesto: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    plugin = EvidencePlugin(output=evidence_path, root=root)
    pytest_rc = int(pytest.main([*args, f"--junitxml={private_junit_path}"], plugins=[plugin]))
    try:
        evidence = _read_json(evidence_path)
        report = validate_execution(private_junit_path, evidence, expected_items, pytest_rc)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "return_code": pytest_rc or 3,
            "errors": [f"validação interrompida: {type(exc).__name__}: {exc}"],
        }
    try:
        _publish_sanitized_junit(private_junit_path, junit_path)
    except (OSError, ET.ParseError, ValueError) as exc:
        report.setdefault("errors", []).append(
            f"JUnit privado não pôde ser publicado com redação: {type(exc).__name__}: {exc}"
        )
        report["return_code"] = pytest_rc or 3
        junit_path.unlink(missing_ok=True)
    finally:
        private_junit_path.unlink(missing_ok=True)
    _write_json(validation_path, report)

    return_code = int(report.get("return_code", 3))
    if return_code:
        for error in report.get("errors", ["relatório sem lista de erros"]):
            print(f"[live-pytest] FAIL: {error}", file=sys.stderr)
        print(f"[live-pytest] relatório: {validation_path}", file=sys.stderr)
        return return_code
    counts = report["case_counts"]
    print(
        "[live-pytest] PASS: "
        f"passed={counts.get('passed', 0)}, "
        f"xfailed_executed={counts.get('xfailed_executed', 0)}, "
        f"inactive_companion={counts.get('inactive_companion', 0)}; "
        f"relatório={validation_path}"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument("pytest_args", nargs=argparse.REMAINDER)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--expected", type=Path, required=True)
    run_parser.add_argument("--evidence", type=Path, required=True)
    run_parser.add_argument("--junit", type=Path, required=True)
    run_parser.add_argument("--validation", type=Path, required=True)
    run_parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "collect":
            return collect(args.output, args.root, args.pytest_args)
        return run(
            args.expected,
            args.evidence,
            args.junit,
            args.validation,
            args.root,
            args.pytest_args,
        )
    except ValueError as exc:
        print(f"[live-pytest] FAIL argumentos: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
