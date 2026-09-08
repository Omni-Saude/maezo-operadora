"""Evidência de execução pytest compartilhada pelo runner local e pela CI.

A coleção fixada no SHA é a autoridade de identidade; JUnit e texto de terminal
não provam entrada no corpo. Companions inativos são omissões declaradas, nunca
prova RED. Não há integração com engine neste módulo.

Schema 2: nodeid/classname/name são rótulos públicos, nunca chaves de comparação
ou seletores parametrizados. SHA-256 de identidades completas (JSON array compacto
UTF-8, ensure_ascii=True, domínio versionado) preserva correlação sem publicar os
parâmetros. JUnit cru deve permanecer privado até validate_execution; somente
depois public_junit_identity projeta seus atributos para publicação. Não validar
essa projeção como se fosse o XML cru. O consumidor autentica a coleção esperada
independentemente no checkout/SHA e mantém stdout/JUnit privados sob seu controle.
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

SCHEMA_VERSION = 2
_URI_PASSWORD = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s/@]+(@)")
_NAMED_SECRET = re.compile(
    r"""(?ix)
    (\b(?:password|passwd|secret|token|api[_-]?key)\b["']?\s*[:=]\s*)
    ("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)
    """
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMPANION_ROOT = Path(__file__).resolve().parents[2]
_GUARD_SHA256 = "8a5b703081314182953a0cf12b844976e2f3908cbf4d5a701b70e3999efa3cc9"

# Catálogo de declarações, não heurística de nomes/reasons. População autorizada
# em docs/design/T3.3-chaos-resilience.md §4 (B1b-posture: GAP-D3-02/DL-0046),
# conferida na fonte eb6b7355 (ancestral de 14eeb930). Cada pin é SHA-256 do texto UTF-8 da função
# (dedent/getsource, newlines LF), incluindo guarda E corpo. Assim, substituir o uso
# real por referência inerte, trocar token/callable ou copiar outro teste não
# cria uma nova exceção. Não existe auto-registro, arquivo de entrada ou env para
# ampliar esta população. Expansão/refatoração deliberada exige revisar a fonte,
# atualizar seu pin e provar os controles positivo/negativo e o RED separado.
_COMPANIONS: dict[str, tuple[str, str, str]] = {
    "tests/integration/chaos/test_crash_between_seams.py::"
    "test_b1a_mutation_check_chain_insert_outside_lock_turns_suite_red": (
        "b1a",
        "broken_emit_once_chain_insert_outside_advisory_lock",
        "26476c68e57ee290da8551c937849805867b16390f5b927dbba2d7a869293077",
    ),
    "tests/integration/chaos/test_crash_between_seams.py::"
    "test_b1b_posture_mutation_check_restart_on_missing_instance_turns_suite_red": (
        "b1b_posture",
        "broken_start_process_restart_on_claim_without_instance",
        "1fb6c03845b1fdf13521521dfacb7e65dd491a2a938650b9cbca6234541ace83",
    ),
    "tests/integration/chaos/test_crash_between_seams.py::"
    "test_b1b_mutation_check_effect_before_emit_turns_suite_red": (
        "b1b",
        "broken_start_process_effect_before_emit",
        "70fc2f615f2f5b1ce84ccc74e56b5adf19b9e7f7db797c011881999d2153a8d7",
    ),
    "tests/integration/chaos/test_sink_down_failclosed.py::"
    "test_c1_down_mutation_check_fail_open_swallow_turns_suite_red": (
        "c1_down",
        "broken_emit_once_fail_open_swallow",
        "3162a66d5d8d9b7c156926fbafc0cc27c1b39604b3048ea990c6ce28c3f7051b",
    ),
    "tests/integration/processes/test_t33_a1_cancel_handoff_redelivery_idempotency.py::"
    "test_a1_mutation_check_broken_idempotency_creates_a_second_instance": (
        "a1_a2",
        "broken_start_process_always_start",
        "241d589a05d08567720ccc1b90316c1c4b4aae0a453ac40e66beccdba81bc8be",
    ),
    "tests/integration/processes/test_t33_a2_agent_start_idempotency_matrix.py::"
    "test_a2_mutation_check_broken_idempotency_creates_a_second_instance": (
        "a1_a2",
        "broken_start_process_always_start",
        "260c453e743137eaf571f04786c98cd7949a568e9ed5b038ec560dc7705e5543",
    ),
}


# PEC-D1: 63ca2a84 removeu somente comentários noqa; fonte conferida em
# a3471496 (R6). Pares exatos, sem normalização AST/textual nem combinação
# cartesiana: C1 antigo com helper R6 (ou o inverso) não está autorizado.
# Os outros cinco corpos são bytewise idênticos nas duas origens. Uma terceira
# fonte continua exigindo revisão explícita; não há catálogo configurável.
_R6_GUARD_SHA256 = "3e84f392e8157d3056036e3f6bcbef94de116199caa3ad55858e33d56aac2d99"
_R6_COMPANIONS = {
    **_COMPANIONS,
    "tests/integration/chaos/test_sink_down_failclosed.py::"
    "test_c1_down_mutation_check_fail_open_swallow_turns_suite_red": (
        "c1_down",
        "broken_emit_once_fail_open_swallow",
        "9b4f076a9dbbe066b22156105aa748aa2ea5172f4c317760587eced421267e21",
    ),
}


def _companion_catalog(guard_hash: str) -> dict[str, tuple[str, str, str]] | None:
    """Seleciona apenas pares de fontes revisados; nunca deriva pins da entrada."""
    if guard_hash == _GUARD_SHA256:
        return _COMPANIONS
    if guard_hash == _R6_GUARD_SHA256:
        return _R6_COMPANIONS
    return None


def redact(value: str) -> str:
    """Redige texto, inclusive chaves citadas em JSON/repr e valores com espaços.

    Objetos estruturados devem chegar como texto completo, preservando o contexto
    das chaves. Redigir apenas folhas isoladas perde esse contexto. Não substitui
    a projeção opaca dos parâmetros/identidades ou a custódia do stdout cru.
    """

    def named(match: re.Match[str]) -> str:
        quote = match[2][0] if match[2].startswith(('"', "'")) else ""
        return f"{match[1]}{quote}<redacted>{quote}"

    return _NAMED_SECRET.sub(named, _URI_PASSWORD.sub(r"\1<redacted>\2", value))


def _fingerprint(domain: str, *parts: str) -> str:
    payload = json.dumps([domain, *parts], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _identity_label(value: str) -> str:
    """Parâmetros nunca são conteúdo publicável, mesmo sem padrão de segredo."""
    prefix, marker, _ = value.partition("[")
    if marker:
        return f"{redact(prefix)}[parameters:{_fingerprint('pytest-label-v2', value)}]"
    safe = redact(value)
    if safe != value:
        return f"{safe}[identity:{_fingerprint('pytest-label-v2', value)}]"
    return safe


def public_junit_identity(classname: str, name: str) -> dict[str, str]:
    """Projeta o par COMPLETO, sem ambiguidade/colapso, após validação do XML cru.

    Consumidores publicam classname/name e preservam junit_identity_sha256 como
    correlação; não recalculam o fingerprint a partir dos rótulos projetados.
    """
    return {
        "classname": _identity_label(classname),
        "name": _identity_label(name),
        "junit_identity_sha256": _fingerprint("pytest-junit-v2", classname, name),
    }


def _resolve(node: ast.expr, namespace: dict[str, Any]) -> Any:
    if isinstance(node, ast.Name):
        return namespace.get(node.id)
    if isinstance(node, ast.Attribute):
        return getattr(_resolve(node.value, namespace), node.attr, None)
    return None


def _companion(item: Any, root: Path) -> dict[str, str] | None:
    """Autentica uma declaração catalogada; não atesta execução da mutação."""
    declaration = _COMPANIONS.get(item.nodeid)
    if declaration is None or root.resolve() != _COMPANION_ROOT.resolve():
        return None
    module_path = root / "tests/integration/chaos/mutations.py"
    if not module_path.is_file():
        return None
    guard_hash = hashlib.sha256(module_path.read_bytes()).hexdigest()
    catalog = _companion_catalog(guard_hash)
    if catalog is None:
        return None
    expected_token, expected_broken, expected_source_hash = catalog[item.nodeid]
    source = root / item.nodeid.split("::", 1)[0]
    if Path(item.path).resolve() != source.resolve():
        return None
    function = inspect.unwrap(item.obj)
    try:
        if Path(inspect.getfile(function)).resolve() != source.resolve():
            return None
        declaration_source = textwrap.dedent(inspect.getsource(function))
        tree = ast.parse(declaration_source)
    except (OSError, TypeError, SyntaxError):
        return None
    definition = tree.body[0]
    if not isinstance(definition, ast.FunctionDef | ast.AsyncFunctionDef):
        return None
    declaration_hash = hashlib.sha256(declaration_source.encode("utf-8")).hexdigest()
    if declaration_hash != expected_source_hash:
        return None
    namespace = function.__globals__
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
            and token.value == expected_token
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
    if broken != {expected_broken} or len(markers) != 1 or markers[0].args != (True,):
        return None
    if markers[0].kwargs.get("reason") != canonical_reason:
        return None
    return {
        "mutation": mutation_id,
        "reason": redact(canonical_reason),
        "reason_sha256": _fingerprint("pytest-reason-v2", canonical_reason),
        "declaration_sha256": declaration_hash,
        "guard_source": module_path.relative_to(root).as_posix(),
        "guard_sha256": guard_hash,
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
    junit = public_junit_identity(".".join(address[:-1]), address[-1])
    return {
        "nodeid": _identity_label(item.nodeid),
        "nodeid_sha256": _fingerprint("pytest-nodeid-v2", item.nodeid),
        "junit_classname": junit["classname"],
        "junit_name": junit["name"],
        "junit_identity_sha256": junit["junit_identity_sha256"],
        "source": _identity_label(relative),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "xfail": None
        if xfail is None
        else {
            "strict": xfail.strict,
            "run": xfail.run,
            "reason": redact(xfail.reason),
            "reason_sha256": _fingerprint("pytest-reason-v2", xfail.reason),
        },
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
                "nodeid": _identity_label(report.nodeid),
                "nodeid_sha256": _fingerprint("pytest-nodeid-v2", report.nodeid),
                "phase": report.when,
                "outcome": report.outcome,
                "body_entered": report.nodeid in self.entered,
                "wasxfail": redact(str(report.wasxfail)) if hasattr(report, "wasxfail") else None,
                "wasxfail_sha256": _fingerprint("pytest-reason-v2", str(report.wasxfail))
                if hasattr(report, "wasxfail")
                else None,
                "skip_reason": redact(reason),
                "skip_reason_sha256": _fingerprint("pytest-reason-v2", reason),
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
                identity = public_junit_identity(case.get("classname", ""), case.get("name", ""))
                skip_reason = skipped.get("message", "") if skipped is not None else ""
                skip_type = skipped.get("type", "") if skipped is not None else ""
                if skip_type not in {"", "pytest.skip", "pytest.xfail"}:
                    raise ValueError("tipo de skip JUnit não suportado")
                cases.append(
                    {
                        **identity,
                        "status": status,
                        "skip_type": skip_type,
                        "skip_reason": redact(skip_reason),
                        "skip_reason_sha256": _fingerprint("pytest-reason-v2", skip_reason),
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
    except (OSError, ET.ParseError, ValueError, KeyError, TypeError) as exc:
        # Exception text can contain the private pathname or XML attribute value.
        errors.append(f"JUnit inválido: {type(exc).__name__}")
    if not isinstance(expected_items, list) or any(
        not isinstance(item, dict)
        or any(
            not isinstance(item.get(key), str) or _SHA256.fullmatch(item[key]) is None
            for key in ("nodeid_sha256", "junit_identity_sha256")
        )
        for item in expected_items
    ):
        errors.append("manifest de identidade inválido ou de schema anterior")
        expected_items = []
    expected_ids = [item["nodeid_sha256"] for item in expected_items]
    if not expected_ids or len(set(expected_ids)) != len(expected_ids):
        errors.append("manifest vazio ou com nodeids duplicados")
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema") != SCHEMA_VERSION
        or evidence.get("finished") is not True
        or evidence.get("pytest_exitstatus") != pytest_rc
    ):
        errors.append("evidência ausente, interrompida ou exitstatus divergente")
    if not isinstance(evidence, dict):
        evidence = {}
    if evidence.get("collection") != expected_items:
        errors.append("coleção/identidade/fonte/marcadores divergem do manifest")
    expected_keys = [item["junit_identity_sha256"] for item in expected_items]
    case_keys = [case["junit_identity_sha256"] for case in cases]
    if Counter(case_keys) != Counter(expected_keys) or len(set(case_keys)) != len(case_keys):
        errors.append("identidade/conjunto/duplicação JUnit diverge do manifest")
    reports = evidence.get("reports", [])
    if not isinstance(reports, list) or any(
        not isinstance(report, dict)
        or report.get("nodeid_sha256") not in expected_ids
        or not isinstance(report.get("phase"), str)
        or report["phase"] not in {"setup", "call", "teardown"}
        for report in reports
    ):
        errors.append("relatórios de execução fora do manifest")
        reports = []
    outcomes: Counter[str] = Counter()
    for item in expected_items:
        nodeid = item["nodeid_sha256"]
        rows = [r for r in reports if r.get("nodeid_sha256") == nodeid]
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
                and setup.get("skip_reason_sha256") == companion["reason_sha256"]
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
                    and call.get("wasxfail_sha256") == xfail["reason_sha256"]
                ):
                    status = "xfailed_executed"
            if status == "unverified":
                errors.append(f"corpo não verificado, skip/XPASS/falha inesperado: {nodeid}")
        outcomes[status] += 1
        key = item["junit_identity_sha256"]
        matching = [case for case in cases if case["junit_identity_sha256"] == key]
        if len(matching) == 1 and status != "unverified":
            matched_case = matching[0]
            expected_status = "passed" if status == "passed" else "skipped"
            if matched_case["status"] != expected_status or (
                (matched_case["skip_type"] == "pytest.xfail") != (status == "xfailed_executed")
            ):
                errors.append(f"estado JUnit diverge das fases: {nodeid}")
            if status in {"inactive_companion", "xfailed_executed"}:
                canonical = item["inactive_companion"] if status == "inactive_companion" else item["xfail"]
                if matched_case["skip_reason_sha256"] != canonical["reason_sha256"]:
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
