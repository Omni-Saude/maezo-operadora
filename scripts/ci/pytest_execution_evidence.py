"""Evidência de execução pytest compartilhada pelo runner local e pela CI.

A coleção fixada no SHA é a autoridade de identidade; JUnit e texto de terminal
não provam entrada no corpo. Companions inativos são omissões declaradas, nunca
prova RED. Não há integração com engine neste módulo.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import re
import sys
import textwrap
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from types import FrameType
from typing import Any

SCHEMA_VERSION = 1
_URI_PASSWORD = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s/@]+(@)")
_NAMED_SECRET = re.compile(r"(?i)(\b(?:password|passwd|secret|token|api[_-]?key)\b\s*[:=]\s*)([^\s,;]+)")


def redact(value: str) -> str:
    return _NAMED_SECRET.sub(r"\1<redacted>", _URI_PASSWORD.sub(r"\1<redacted>\2", value))


def _resolve(node: ast.expr, namespace: dict[str, Any]) -> Any:
    if isinstance(node, ast.Name):
        return namespace.get(node.id)
    if isinstance(node, ast.Attribute):
        return getattr(_resolve(node.value, namespace), node.attr, None)
    return None


def _companion(item: Any, root: Path) -> dict[str, str] | None:
    """Deriva a exceção da guarda e do callable autenticados, nunca do reason.

    O contrato existente usa `not mutation_active(id)` de mutations.py e chama
    uma broken_* daquele mesmo módulo. Ambas as fontes pertencem à coleção do
    SHA e seus hashes integram a identidade comparada antes/depois da execução.
    """
    function = inspect.unwrap(item.obj)
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    except (OSError, TypeError, SyntaxError):
        return None
    definition = tree.body[0]
    if not isinstance(definition, ast.FunctionDef | ast.AsyncFunctionDef):
        return None
    namespace = function.__globals__
    module_path = root / "tests/integration/chaos/mutations.py"
    if not module_path.is_file():
        return None
    guards: list[tuple[str, str]] = []
    for decorator in definition.decorator_list:
        if not isinstance(decorator, ast.Call) or not decorator.args:
            continue
        marker = decorator.func
        if not isinstance(marker, ast.Attribute) or marker.attr != "skipif":
            continue
        condition = decorator.args[0]
        if not isinstance(condition, ast.UnaryOp) or not isinstance(condition.op, ast.Not):
            continue
        call = condition.operand
        if not isinstance(call, ast.Call) or len(call.args) != 1 or call.keywords:
            continue
        token = call.args[0]
        helper = _resolve(call.func, namespace)
        if not (
            isinstance(token, ast.Constant)
            and isinstance(token.value, str)
            and inspect.isfunction(helper)
            and helper.__name__ == "mutation_active"
            and Path(inspect.getfile(helper)).resolve() == module_path.resolve()
        ):
            continue
        reason = next((kw.value for kw in decorator.keywords if kw.arg == "reason"), None)
        if not isinstance(reason, ast.Constant) or not isinstance(reason.value, str):
            continue
        guards.append((token.value, reason.value))
    if len(guards) != 1:
        return None
    broken = {
        value.__name__
        for node in ast.walk(definition)
        if isinstance(node, ast.Name | ast.Attribute)
        and inspect.isfunction(value := _resolve(node, namespace))
        and value.__name__.startswith("broken_")
        and Path(inspect.getfile(value)).resolve() == module_path.resolve()
    }
    mutation_id, canonical_reason = guards[0]
    markers = list(item.iter_markers("skipif"))
    if not broken or len(markers) != 1 or markers[0].args != (True,):
        return None
    if markers[0].kwargs.get("reason") != canonical_reason:
        return None
    return {
        "mutation": mutation_id,
        "reason": redact(canonical_reason),
        "guard_source": module_path.relative_to(root).as_posix(),
        "guard_sha256": hashlib.sha256(module_path.read_bytes()).hexdigest(),
        "broken_callables": ",".join(sorted(broken)),
        "obligation": "separate_opt_in_RED_required",
    }


def item_identity(item: Any, root: Path) -> dict[str, Any]:
    from _pytest.junitxml import mangle_test_address
    from _pytest.skipping import evaluate_xfail_marks

    source = Path(item.path).resolve()
    relative = source.relative_to(root.resolve()).as_posix()
    address = mangle_test_address(item.nodeid)
    xfail = evaluate_xfail_marks(item)
    return {
        "nodeid": item.nodeid,
        "junit_classname": ".".join(address[:-1]),
        "junit_name": address[-1],
        "source": relative,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "xfail": None
        if xfail is None
        else {"strict": xfail.strict, "run": xfail.run, "reason": redact(xfail.reason)},
        "inactive_companion": _companion(item, root),
    }


class EvidencePlugin:
    def __init__(self, output: Path, root: Path) -> None:
        import pytest

        pytest.hookimpl(wrapper=True, tryfirst=True)(type(self).pytest_runtest_call)
        self.output = output
        self.root = root.resolve()
        self.items: list[dict[str, Any]] = []
        self.reports: list[dict[str, Any]] = []
        self.entered: set[str] = set()
        self.code: dict[str, Any] = {}

    def pytest_collection_finish(self, session: Any) -> None:
        self.items = [item_identity(item, self.root) for item in session.items]
        self.code = {item.nodeid: inspect.unwrap(item.obj).__code__ for item in session.items}

    def pytest_runtest_call(self, item: Any) -> Any:
        previous = sys.getprofile()
        target = self.code.get(item.nodeid)

        def observe(frame: FrameType, event: str, arg: Any) -> None:
            if event == "call" and frame.f_code is target:
                self.entered.add(item.nodeid)
            if previous is not None:
                previous(frame, event, arg)

        sys.setprofile(observe)
        try:
            return (yield)
        finally:
            sys.setprofile(previous)

    def pytest_runtest_logreport(self, report: Any) -> None:
        reason = ""
        if isinstance(report.longrepr, tuple):
            reason = str(report.longrepr[-1]).removeprefix("Skipped: ")
        self.reports.append(
            {
                "nodeid": report.nodeid,
                "phase": report.when,
                "outcome": report.outcome,
                "body_entered": report.nodeid in self.entered,
                "wasxfail": redact(str(report.wasxfail)) if hasattr(report, "wasxfail") else None,
                "skip_reason": redact(reason),
            }
        )

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(
            json.dumps(
                {
                    "schema": SCHEMA_VERSION,
                    "collection": self.items,
                    "reports": self.reports,
                    "finished": True,
                    "pytest_exitstatus": int(exitstatus),
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )


def validate_execution(
    junit_path: Path,
    evidence: dict[str, Any],
    expected_items: list[dict[str, Any]],
    pytest_rc: int,
) -> dict[str, Any]:
    """Confronta identidades, fases, entrada no corpo, XML e totais; falha fechado."""
    errors: list[str] = []
    cases: list[dict[str, Any]] = []
    totals = dict.fromkeys(("tests", "failures", "errors", "skipped"), 0)
    try:
        root = ET.parse(junit_path).getroot()
        if root.tag not in {"testsuite", "testsuites"}:
            raise ValueError("raiz JUnit inválida")
        suites = [root] if root.tag == "testsuite" else list(root)
        if not suites or any(s.tag != "testsuite" for s in suites):
            raise ValueError("estrutura JUnit inválida")
        for suite in suites:
            suite_cases = list(suite.findall("testcase"))
            if any(
                child.tag not in {"testcase", "properties", "system-out", "system-err"} for child in suite
            ):
                raise ValueError("estrutura de suite não suportada")
            observed = {"tests": len(suite_cases), "failures": 0, "errors": 0, "skipped": 0}
            for case in suite_cases:
                states = [child.tag for child in case if child.tag in {"failure", "error", "skipped"}]
                if len(states) > 1:
                    raise ValueError("caso JUnit com estados incompatíveis")
                status = states[0] if states else "passed"
                if status != "passed":
                    observed[{"failure": "failures", "error": "errors", "skipped": "skipped"}[status]] += 1
                skipped = case.find("skipped")
                cases.append(
                    {
                        "classname": case.get("classname", ""),
                        "name": case.get("name", ""),
                        "status": status,
                        "skip_type": skipped.get("type", "") if skipped is not None else "",
                        "skip_reason": redact(skipped.get("message", "")) if skipped is not None else "",
                    }
                )
            for name, count in observed.items():
                if int(suite.attrib[name]) != count:
                    raise ValueError("totais JUnit divergem dos casos reais")
                totals[name] += count
        if root.tag == "testsuites":
            for name, count in totals.items():
                if name in root.attrib and int(root.attrib[name]) != count:
                    raise ValueError("totais agregados JUnit divergem das suites")
    except (OSError, ET.ParseError, ValueError, KeyError) as exc:
        errors.append(f"JUnit inválido: {type(exc).__name__}: {redact(str(exc))}")
    expected_ids = [item["nodeid"] for item in expected_items]
    if not expected_ids or len(set(expected_ids)) != len(expected_ids):
        errors.append("manifest vazio ou com nodeids duplicados")
    if (
        evidence.get("schema") != SCHEMA_VERSION
        or evidence.get("finished") is not True
        or evidence.get("pytest_exitstatus") != pytest_rc
    ):
        errors.append("evidência ausente, interrompida ou exitstatus divergente")
    if evidence.get("collection") != expected_items:
        errors.append("coleção/identidade/fonte/marcadores divergem do manifest")
    expected_keys = [(i["junit_classname"], i["junit_name"]) for i in expected_items]
    case_keys = [(c["classname"], c["name"]) for c in cases]
    if Counter(case_keys) != Counter(expected_keys) or len(set(case_keys)) != len(case_keys):
        errors.append("identidade/conjunto/duplicação JUnit diverge do manifest")
    reports = evidence.get("reports", [])
    if not isinstance(reports, list) or any(r.get("nodeid") not in expected_ids for r in reports):
        errors.append("relatórios de execução fora do manifest")
        reports = []
    outcomes: Counter[str] = Counter()
    for item in expected_items:
        nodeid = item["nodeid"]
        rows = [r for r in reports if r.get("nodeid") == nodeid]
        phases = {r.get("phase"): r for r in rows}
        status = "unverified"
        if len(phases) != len(rows) or set(phases) not in (
            {"setup", "teardown"},
            {"setup", "call", "teardown"},
        ):
            errors.append(f"fases incompletas/duplicadas: {nodeid}")
        else:
            setup, teardown, call = phases["setup"], phases["teardown"], phases.get("call")
            companion = item.get("inactive_companion")
            xfail = item.get("xfail")
            if (
                companion
                and setup["outcome"] == "skipped"
                and call is None
                and setup.get("skip_reason") == companion["reason"]
                and setup.get("wasxfail") is None
                and teardown["outcome"] == "passed"
            ):
                status = "inactive_companion"
            elif (
                setup["outcome"] == "passed"
                and teardown["outcome"] == "passed"
                and call
                and call.get("body_entered") is True
            ):
                if call["outcome"] == "passed" and call.get("wasxfail") is None:
                    status = "passed"
                elif (
                    call["outcome"] == "skipped"
                    and xfail
                    and xfail["strict"] is True
                    and xfail["run"] is True
                    and call.get("wasxfail") == xfail["reason"]
                ):
                    status = "xfailed_executed"
            if status == "unverified":
                errors.append(f"corpo não verificado, skip/XPASS/falha inesperado: {nodeid}")
        outcomes[status] += 1
        key = (item["junit_classname"], item["junit_name"])
        matching = [case for case in cases if (case["classname"], case["name"]) == key]
        if len(matching) == 1 and status != "unverified":
            matched_case = matching[0]
            expected_status = "passed" if status == "passed" else "skipped"
            if matched_case["status"] != expected_status or (
                (matched_case["skip_type"] == "pytest.xfail") != (status == "xfailed_executed")
            ):
                errors.append(f"estado JUnit diverge das fases: {nodeid}")
            if status in {"inactive_companion", "xfailed_executed"}:
                canonical = item["inactive_companion"] if status == "inactive_companion" else item["xfail"]
                if matched_case["skip_reason"] != canonical["reason"]:
                    errors.append(f"razão JUnit diverge do estado canônico: {nodeid}")
    if not outcomes["passed"] + outcomes["xfailed_executed"]:
        errors.append("nenhum corpo de teste foi verificado")
    return {
        "return_code": pytest_rc or int(bool(errors)),
        "errors": errors,
        "junit_counts": totals,
        "case_counts": dict(outcomes),
        "cases": cases,
        "inactive_companions_require_separate_RED": outcomes["inactive_companion"],
    }
