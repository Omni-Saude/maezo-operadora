#!/usr/bin/env python3
"""CI gate: boundary-proof allowlist for worker BPMN errors (ADR-0030 §2, Tier-0).

Purpose
-------
A worker signals a modeled BPMN error by raising ``WorkerBpmnError(code)``; the harness
reports it to the engine as a real ``bpmnError`` **only** when ``code`` is in the runtime
``bpmn_error_allowlist`` (``harness.py`` §9). An unmodeled ``bpmnError`` does not open an
incident on CIB Seven 2.1.0 — it silently **ends the process scope** (live-verified hazard).
This gate is the CI-side static proof ADR-0026 §5 / T1.1 §9 committed to and ADR-0030 promotes
to a hard precondition: it mechanically computes, from ``spec/**`` BPMN, the set of
``(topic, errorCode)`` error-boundary declarations on **external** tasks, and verifies every
``WorkerBpmnError(code)`` a worker raises is **consumption-covered** for that worker's topic.

The three clauses (ADR-0030 §2)
-------------------------------
- **(a) compute the allowlist from spec.** The legal code set is derived from
  ``bpmn:error@errorCode`` on error boundary events attached to ``camunda:type="external"``
  service tasks — never a hand-maintained list.
- **(b) FAIL on an unproven raise.** A worker that raises ``WorkerBpmnError(code)`` whose
  ``code`` is not consumption-covered (below) is a hard failure — including a raise for a code
  with **no** spec boundary at all (the ``ERR_PUBLISH_MISSING_TOPIC`` deliberate-demote case).
- **(c) WARN on a dead model** (phased per ADR-0030 F5). A spec-declared boundary code that no
  worker raises is a dead-model regression, but during the Tier-0..2 migration window this is
  **warn-only** against a shrinking baseline; ``--strict-dead-models`` hardens it to FAIL at
  Tier-3 close. Without phasing the gate is permanently red until every tier lands, which
  teaches people to ignore it.

Consumption-covered (clause (b) semantics — ADR-0030 §2, resolves R1 F2)
------------------------------------------------------------------------
For a raised ``code`` declared on topic ``t``:

- **Simple rule** — ``t`` is boundary-declared with ``code`` in **every** process that consumes
  ``t`` (has an external service task on ``t``). Holds for 14 of the 15 distinct spec codes.
- **Dispatch-filter escape** — the one admitted exception is ``operadora.events.publish``:
  consumed by all 16 processes but ``ERR_EVENT_PUBLISH_FAILED`` is boundary-declared only in
  SP-OP-ESCALATION-001. The as-built safety is a **worker-side dispatch condition** (the raise
  is gated on the event topic being in ``events._ESCALATION_PUBLISH_BPMN_ERROR_TOPICS``). This
  gate admits that pattern *only* with the two mechanical checks ADR-0030 §2 requires:
    - **(b1)** the worker filter set **equals** the set of ``event_topic`` input-mapping values
      on the boundary-carrying activities in the declaring process(es); and
    - **(b2)** none of those values appears in any **other** process's ``event_topic`` mappings.
  A drift in either direction (a new process reusing an escalation domain topic; the filter
  diverging from the spec) fails CI. The escape's *values* are read from the worker source (not
  hardcoded); only the ``topic -> filter-constant`` linkage is declared here
  (``_DISPATCH_FILTER_REGISTRY``), deliberately and under review.

T-E gating (reporting only — ADR-0030 §4)
-----------------------------------------
Being consumption-covered makes a code *gate-proven*, not *production-safe*. Business-outcome
codes — every ``*_NOT_HUMAN`` guard plus the denial-block ``ERR_AUTH_DENIAL_INCOMPLETE`` — are
**hard-gated on T-E** (audited-refusal): activating one pre-T-E trades a human-visible incident
for a silent clean end at a neutral terminal. The gate therefore splits its proven set into
``tier0_enabled`` (enable in ``service.py`` now) and ``te_deferred`` (do NOT enable until T-E),
so "populate the runtime allowlist from the gate's output" is a literal, reviewable instruction.
The gate never *fails* on a T-E code that is correctly raised + covered + left out of production;
that assertion lives in the unit tests that import the wired ``service.py`` allowlist.

Design
------
A pure, dependency-light core (``build_spec_model``, ``build_worker_model``, ``evaluate``) plus a
thin CLI wrapper (``main``). The core takes already-parsed models, so it is fully unit-testable
against synthetic fixtures without touching the filesystem — mirroring
``scripts/ci/check_evidence_ledger.py``.

Fail-closed contract
--------------------
- Any ``raise WorkerBpmnError(<x>)`` whose ``<x>`` cannot be statically resolved to a string is a
  **violation** (a raise the gate cannot prove must never pass).
- A dispatch filter named in the registry that cannot be fully resolved is a **violation**.
- No "warn and pass" path for clause (b): ambiguity is failure, never skip.

Usage (CI / local)
-------------------
    python3 scripts/ci/check_bpmn_error_allowlist.py
    python3 scripts/ci/check_bpmn_error_allowlist.py \
        --bpmn-dir spec/processes/bpmn --workers-dir src/maezo/tools/workers
    python3 scripts/ci/check_bpmn_error_allowlist.py --strict-dead-models   # Tier-3 hardening
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------------------
# BPMN / Camunda namespaces (mirrors src/maezo/platform/validation/bpmn.py)
# ---------------------------------------------------------------------------

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
CAMUNDA_NS = "http://camunda.org/schema/1.0/bpmn"

#: The external-task type marker on a `bpmn:serviceTask` (`camunda:type="external"`).
_EXTERNAL_TASK_TYPE = "external"


def _q(tag: str) -> str:
    return f"{{{BPMN_NS}}}{tag}"


def _camunda(attr: str) -> str:
    return f"{{{CAMUNDA_NS}}}{attr}"


# ---------------------------------------------------------------------------
# ADR-0030 §2 dispatch-filter escape + §4 T-E gating (declared, reviewed constants)
# ---------------------------------------------------------------------------

#: The ONE consumption-covered escape admitted into clause (b) (ADR-0030 §2). Maps the topic whose
#: worker legitimately raises a boundary code declared in only a SUBSET of consuming processes to
#: the worker-side dispatch-filter constant that makes it safe. VALUES are read from the worker
#: source (never hardcoded); only this topic<->filter linkage is declared here — extending it is a
#: deliberate, reviewed act, not an accident.
_DISPATCH_FILTER_REGISTRY: dict[str, str] = {
    "operadora.events.publish": "_ESCALATION_PUBLISH_BPMN_ERROR_TOPICS",
}

#: ADR-0030 §4: business-outcome codes hard-gated on T-E (audited-refusal) before they may be
#: enabled in the production allowlist — the denial-block codes. The ``*_NOT_HUMAN`` guard family
#: is matched by suffix (see ``is_te_gated``) so a new guard code is gated automatically.
_DENIAL_BLOCK_CODES: frozenset[str] = frozenset({"ERR_AUTH_DENIAL_INCOMPLETE"})

#: The exception type name workers raise to signal a modeled BPMN error (``harness.py``).
_BPMN_ERROR_EXC = "WorkerBpmnError"


def is_te_gated(code: str) -> bool:
    """True iff ``code`` is a business-outcome code hard-gated on T-E (ADR-0030 §4).

    Pattern, not a hand-list: ALL ``*_NOT_HUMAN`` guard codes (blocking an adverse L0 action and
    routing to a neutral terminal) plus the denial-block ``ERR_AUTH_DENIAL_INCOMPLETE``. Enabling
    any of these pre-T-E converts a guaranteed-human-visible incident into a silent clean end.
    """
    return code.endswith("_NOT_HUMAN") or code in _DENIAL_BLOCK_CODES


# ---------------------------------------------------------------------------
# Spec model (parsed from spec/processes/bpmn/**)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundaryDecl:
    """One error-boundary catch on an **external** service task (ADR-0030 census unit)."""

    process_id: str
    topic: str
    error_code: str
    activity_id: str
    event_topic: str | None  # the `event_topic` inputParameter on the attached activity, if any


@dataclass(frozen=True)
class SpecModel:
    """Everything the gate needs from ``spec/**`` BPMN, aggregated across every process."""

    boundary_decls: tuple[BoundaryDecl, ...]
    #: topic -> process_ids that have >=1 external service task on that topic (its consumers).
    topic_consumers: dict[str, frozenset[str]]
    #: process_id -> every `event_topic` inputParameter value on its external tasks.
    process_event_topics: dict[str, frozenset[str]]
    #: parse errors keyed by file — a malformed spec file is a hard failure, never skipped.
    parse_errors: tuple[str, ...] = ()

    # -- derived indices ---------------------------------------------------------------------

    def spec_codes(self) -> frozenset[str]:
        """Distinct boundary error codes on external tasks."""
        return frozenset(d.error_code for d in self.boundary_decls)

    def code_to_topics(self) -> dict[str, frozenset[str]]:
        out: dict[str, set[str]] = {}
        for d in self.boundary_decls:
            out.setdefault(d.error_code, set()).add(d.topic)
        return {c: frozenset(ts) for c, ts in out.items()}

    def pair_declarers(self) -> dict[tuple[str, str], frozenset[str]]:
        """(topic, code) -> process_ids that declare that boundary on an external task."""
        out: dict[tuple[str, str], set[str]] = {}
        for d in self.boundary_decls:
            out.setdefault((d.topic, d.error_code), set()).add(d.process_id)
        return {k: frozenset(v) for k, v in out.items()}

    def boundary_event_topics(self) -> dict[tuple[str, str], frozenset[str]]:
        """(topic, code) -> the `event_topic` values on the boundary-carrying activities (b1)."""
        out: dict[tuple[str, str], set[str]] = {}
        for d in self.boundary_decls:
            if d.event_topic is not None:
                out.setdefault((d.topic, d.error_code), set()).add(d.event_topic)
        return {k: frozenset(v) for k, v in out.items()}


def _external_service_tasks(process: ET.Element) -> dict[str, tuple[str, str | None]]:
    """Map every external service task id -> (topic, event_topic value) in one process.

    Iterates recursively (``iter``) so tasks nested in a ``subProcess`` are included. Only
    ``camunda:type="external"`` tasks are returned — the boundary census is external-task only
    (ADR-0030), which is why fraude's single boundary event, attached to a non-external task,
    correctly does not count.
    """
    tasks: dict[str, tuple[str, str | None]] = {}
    for st in process.iter(_q("serviceTask")):
        if st.get(_camunda("type")) != _EXTERNAL_TASK_TYPE:
            continue
        tid = st.get("id")
        topic = st.get(_camunda("topic"))
        if not tid or not topic:
            continue
        tasks[tid] = (topic, _event_topic_of(st))
    return tasks


def _event_topic_of(service_task: ET.Element) -> str | None:
    """Return the literal ``event_topic`` ``camunda:inputParameter`` value on a task, or None.

    Only a literal string value is meaningful for the b1/b2 checks; a task whose ``event_topic``
    is absent (or a non-literal expression) yields None and simply does not contribute a value.
    """
    for inp in service_task.iter(_camunda("inputParameter")):
        if inp.get("name") == "event_topic":
            text = (inp.text or "").strip()
            return text or None
    return None


def _parse_bpmn_file(path: Path) -> tuple[list[BoundaryDecl], dict[str, set[str]], dict[str, set[str]]]:
    """Parse one ``.bpmn`` file.

    Returns ``(boundary_decls, topic_consumers, process_event_topics)`` where the two dicts are
    keyed by topic / process_id respectively. Raises ``ET.ParseError``/``OSError`` to the caller,
    which turns it into a hard, per-file parse-error finding (never a silent skip).
    """
    root = ET.parse(path).getroot()  # noqa: S314 — trusted in-repo spec artifact (repo posture)
    # `bpmn:error` catalog is defined at `definitions` (root) level; errorRef resolves against it.
    error_catalog = {
        e.get("id"): e.get("errorCode") for e in root.iter(_q("error")) if e.get("id") and e.get("errorCode")
    }

    decls: list[BoundaryDecl] = []
    topic_consumers: dict[str, set[str]] = {}
    process_event_topics: dict[str, set[str]] = {}

    for process in root.iter(_q("process")):
        process_id = process.get("id")
        if not process_id:
            continue
        service_tasks = _external_service_tasks(process)
        for topic, event_topic in service_tasks.values():
            topic_consumers.setdefault(topic, set()).add(process_id)
            if event_topic is not None:
                process_event_topics.setdefault(process_id, set()).add(event_topic)

        for boundary in process.iter(_q("boundaryEvent")):
            error_def = boundary.find(_q("errorEventDefinition"))
            if error_def is None:
                continue
            attached = boundary.get("attachedToRef")
            if attached not in service_tasks:
                continue  # boundary on a non-external task (userTask/subProcess/...) — out of scope
            error_ref = error_def.get("errorRef")
            error_code = error_catalog.get(error_ref) if error_ref else None
            if not error_code:
                continue
            topic, event_topic = service_tasks[attached]
            decls.append(
                BoundaryDecl(
                    process_id=process_id,
                    topic=topic,
                    error_code=error_code,
                    activity_id=attached,
                    event_topic=event_topic,
                )
            )

    return decls, topic_consumers, process_event_topics


def build_spec_model(bpmn_dir: Path) -> SpecModel:
    """Parse every ``*.bpmn`` under ``bpmn_dir`` into a :class:`SpecModel` (fail-closed on parse)."""
    all_decls: list[BoundaryDecl] = []
    topic_consumers: dict[str, set[str]] = {}
    process_event_topics: dict[str, set[str]] = {}
    parse_errors: list[str] = []

    for path in sorted(bpmn_dir.glob("*.bpmn")):
        try:
            decls, consumers, event_topics = _parse_bpmn_file(path)
        except (ET.ParseError, OSError) as exc:
            parse_errors.append(f"{path.name}: {exc}")
            continue
        all_decls.extend(decls)
        for topic, procs in consumers.items():
            topic_consumers.setdefault(topic, set()).update(procs)
        for proc, topics in event_topics.items():
            process_event_topics.setdefault(proc, set()).update(topics)

    return SpecModel(
        boundary_decls=tuple(all_decls),
        topic_consumers={t: frozenset(p) for t, p in topic_consumers.items()},
        process_event_topics={p: frozenset(t) for p, t in process_event_topics.items()},
        parse_errors=tuple(parse_errors),
    )


# ---------------------------------------------------------------------------
# Worker model (parsed from src/maezo/tools/workers/** via AST — no import side effects)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerRaise:
    """One ``raise WorkerBpmnError(code)`` site whose code resolved to a string."""

    code: str
    module: str  # file stem, e.g. "auth" / "cancel" / "events"
    lineno: int


@dataclass(frozen=True)
class WorkerModel:
    """Everything the gate needs from the worker source tree."""

    raises: tuple[WorkerRaise, ...]
    #: topic -> resolved worker-side dispatch filter set (from ``_DISPATCH_FILTER_REGISTRY``).
    dispatch_filters: dict[str, frozenset[str]]
    #: (module, lineno) of raises whose code argument could not be statically resolved.
    unresolved_raises: tuple[tuple[str, int], ...] = ()
    #: registry filter constants that could not be fully resolved (name -> reason).
    unresolved_filters: tuple[str, ...] = ()

    def raised_codes(self) -> frozenset[str]:
        return frozenset(r.code for r in self.raises)

    def sites_for(self, code: str) -> tuple[str, ...]:
        return tuple(f"{r.module}.py:{r.lineno}" for r in self.raises if r.code == code)


def _collect_str_constants(tree: ast.Module) -> dict[str, set[str]]:
    """Module-level ``NAME = "literal"`` / ``NAME: T = "literal"`` string constants."""
    consts: dict[str, set[str]] = {}
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant) and isinstance(value.value, str):
            consts.setdefault(target.id, set()).add(value.value)
    return consts


def _resolve_name(name: str, consts: dict[str, set[str]]) -> str | None:
    """Resolve a constant NAME to its single string value, or None if unknown/ambiguous."""
    values = consts.get(name)
    if values and len(values) == 1:
        return next(iter(values))
    return None


def _call_func_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _raise_code_arg(call: ast.Call) -> ast.expr | None:
    """The first positional (or ``error_code=``) argument of a ``WorkerBpmnError(...)`` call."""
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg == "error_code":
            return kw.value
    return None


def _resolve_set_literal(value: ast.expr, consts: dict[str, set[str]]) -> frozenset[str] | None:
    """Resolve a ``frozenset({...})`` / ``{...}`` / ``[...]`` of str literals or NAME constants."""
    elts: list[ast.expr]
    if (
        isinstance(value, ast.Call)
        and _call_func_name(value.func) in {"frozenset", "set"}
        and len(value.args) == 1
    ):
        inner = value.args[0]
        if not isinstance(inner, ast.Set | ast.List | ast.Tuple):
            return None
        elts = list(inner.elts)
    elif isinstance(value, ast.Set | ast.List | ast.Tuple):
        elts = list(value.elts)
    else:
        return None

    resolved: set[str] = set()
    for elt in elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            resolved.add(elt.value)
        elif isinstance(elt, ast.Name):
            member = _resolve_name(elt.id, consts)
            if member is None:
                return None  # unresolved member -> whole filter is unproven (fail-closed)
            resolved.add(member)
        else:
            return None
    return frozenset(resolved)


def _find_filter_value(tree: ast.Module, const_name: str) -> ast.expr | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id == const_name:
                return node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name) and node.target.id == const_name:
                return node.value
    return None


def build_worker_model(workers_dir: Path) -> WorkerModel:
    """AST-scan ``workers_dir`` for ``WorkerBpmnError`` raises + the registry dispatch filters."""
    trees: dict[Path, ast.Module] = {}
    global_consts: dict[str, set[str]] = {}
    for path in sorted(workers_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, OSError):
            continue
        trees[path] = tree
        for name, values in _collect_str_constants(tree).items():
            global_consts.setdefault(name, set()).update(values)

    raises: list[WorkerRaise] = []
    unresolved: list[tuple[str, int]] = []
    for path, tree in trees.items():
        module = path.stem
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            if _call_func_name(node.exc.func) != _BPMN_ERROR_EXC:
                continue
            arg = _raise_code_arg(node.exc)
            code: str | None = None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                code = arg.value
            elif isinstance(arg, ast.Name):
                code = _resolve_name(arg.id, global_consts)
            if code is None:
                unresolved.append((module, node.lineno))
            else:
                raises.append(WorkerRaise(code=code, module=module, lineno=node.lineno))

    dispatch_filters: dict[str, frozenset[str]] = {}
    unresolved_filters: list[str] = []
    for topic, const_name in _DISPATCH_FILTER_REGISTRY.items():
        value_node: ast.expr | None = None
        for tree in trees.values():
            value_node = _find_filter_value(tree, const_name)
            if value_node is not None:
                break
        if value_node is None:
            unresolved_filters.append(f"{const_name} (not found in worker source)")
            continue
        resolved = _resolve_set_literal(value_node, global_consts)
        if resolved is None:
            unresolved_filters.append(f"{const_name} (could not statically resolve members)")
            continue
        dispatch_filters[topic] = resolved

    return WorkerModel(
        raises=tuple(raises),
        dispatch_filters=dispatch_filters,
        unresolved_raises=tuple(unresolved),
        unresolved_filters=tuple(unresolved_filters),
    )


# ---------------------------------------------------------------------------
# Pure evaluation core
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    """Outcome of the boundary-proof gate, independent of how the models were obtained."""

    ok: bool
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    consumption_covered: frozenset[str] = frozenset()  # gate-proven codes (raised + covered)
    tier0_enabled: frozenset[str] = frozenset()  # proven AND not T-E-gated -> wire in service.py
    te_deferred: frozenset[str] = frozenset()  # proven AND T-E-gated -> do NOT enable pre-T-E
    dead_models: frozenset[str] = frozenset()  # spec codes with no raiser (clause (c))

    def render(self) -> str:
        lines: list[str] = []
        status = "PASS" if self.ok else "FAIL"
        lines.append(f"[bpmn-error-allowlist] {status}")
        if self.violations:
            lines.append(f"  violations ({len(self.violations)}):")
            lines.extend(f"    - {v}" for v in self.violations)
        if self.warnings:
            lines.append(f"  warnings ({len(self.warnings)}, non-blocking per ADR-0030 F5):")
            lines.extend(f"    - {w}" for w in self.warnings)
        lines.append(f"  consumption-covered (gate-proven): {sorted(self.consumption_covered)}")
        lines.append(f"  Tier-0 enable now (service.py allowlist): {sorted(self.tier0_enabled)}")
        lines.append(f"  T-E deferred (do NOT enable pre-T-E, ADR-0030 §4): {sorted(self.te_deferred)}")
        if self.dead_models:
            lines.append(f"  dead models (declared boundary, no raiser): {sorted(self.dead_models)}")
        return "\n".join(lines)


def _coverage_violation(
    code: str,
    topic: str,
    spec: SpecModel,
    workers: WorkerModel,
) -> str | None:
    """Check consumption-coverage of ``code`` on ``topic``. Returns a violation string or None.

    Simple rule first (boundary declared in every consuming process); then the ADR-0030 §2
    dispatch-filter escape (b1/b2) for a registry topic. Anything else is a violation.
    """
    consumers = spec.topic_consumers.get(topic, frozenset())
    declarers = spec.pair_declarers().get((topic, code), frozenset())
    sites = ", ".join(workers.sites_for(code)) or "?"

    if consumers and consumers <= declarers:
        return None  # simple rule satisfied

    # Simple rule failed -> the only admitted escape is a worker-side dispatch filter.
    filt = workers.dispatch_filters.get(topic)
    if filt is None:
        uncovered = sorted(consumers - declarers)
        return (
            f"worker raises {_BPMN_ERROR_EXC}({code}) [{sites}] on topic {topic!r}, but that "
            f"boundary is NOT declared in every consuming process (missing in: {uncovered}) and "
            f"no worker-side dispatch filter proves consumption-coverage — not consumption-covered "
            f"(ADR-0030 §2 clause (b))."
        )

    # (b1) filter set must equal the event_topic values on the boundary-carrying activities.
    b1_expected = spec.boundary_event_topics().get((topic, code), frozenset())
    if filt != b1_expected:
        return (
            f"dispatch-filter check b1 FAILED for {code} on {topic!r}: worker filter "
            f"{sorted(filt)} != boundary-activity event_topic set {sorted(b1_expected)} "
            f"(ADR-0030 §2 (b1) — filter/spec drift)."
        )

    # (b2) no filter value may appear in any OTHER process's event_topic mappings.
    leaks: dict[str, list[str]] = {}
    for proc, topics in spec.process_event_topics.items():
        if proc in declarers:
            continue
        overlap = sorted(filt & topics)
        if overlap:
            leaks[proc] = overlap
    if leaks:
        return (
            f"dispatch-filter check b2 FAILED for {code} on {topic!r}: escalation domain "
            f"topic(s) reused by other process(es) {leaks} — a publish failure there would "
            f"wrongly route as {code} (ADR-0030 §2 (b2))."
        )

    return None


def evaluate(spec: SpecModel, workers: WorkerModel, *, strict_dead_models: bool = False) -> GateResult:
    """Run the three-clause boundary-proof gate over parsed models (pure).

    ``strict_dead_models`` hardens clause (c) from warn-only to a hard failure (Tier-3 close,
    ADR-0030 F5). Left False during the Tier-0..2 migration window.
    """
    violations: list[str] = []
    warnings: list[str] = []

    # Structural fail-closed guards first: a spec we could not fully parse, or a raise/filter we
    # could not statically resolve, cannot be reasoned about — never pass on ambiguity.
    for err in spec.parse_errors:
        violations.append(f"BPMN parse error (cannot compute allowlist): {err}")
    for module, lineno in workers.unresolved_raises:
        violations.append(
            f"{module}.py:{lineno}: {_BPMN_ERROR_EXC}(...) raised with a non-literal, "
            f"unresolvable code argument — cannot prove it against spec (fail-closed)."
        )
    for filt in workers.unresolved_filters:
        violations.append(f"dispatch filter unresolved (fail-closed): {filt}")

    spec_codes = spec.spec_codes()
    code_to_topics = spec.code_to_topics()
    raised_codes = workers.raised_codes()

    # Clause (b): every raised code must be consumption-covered on every topic it is declared on.
    consumption_covered: set[str] = set()
    for code in sorted(raised_codes):
        sites = ", ".join(workers.sites_for(code)) or "?"
        if code not in spec_codes:
            violations.append(
                f"worker raises {_BPMN_ERROR_EXC}({code}) [{sites}] but NO external-task "
                f"bpmn:error@errorCode={code} boundary exists anywhere in spec/** — an "
                f"uncatalogued raise relying on demote-to-incident (ADR-0030 §2 clause (b); the "
                f"ERR_PUBLISH_MISSING_TOPIC case). Reclassify to ValueError or declare a boundary."
            )
            continue
        code_violations = [
            v
            for topic in sorted(code_to_topics.get(code, frozenset()))
            if (v := _coverage_violation(code, topic, spec, workers)) is not None
        ]
        if code_violations:
            violations.extend(code_violations)
        else:
            consumption_covered.add(code)

    # Clause (c): a spec boundary code with no raiser is a dead model (warn-only unless strict).
    dead_models = sorted(spec_codes - raised_codes)
    for code in dead_models:
        topics = sorted(code_to_topics.get(code, frozenset()))
        detail = (
            f"spec declares external-task boundary bpmn:error@errorCode={code} (topics {topics}) "
            f"but NO worker raises it — dead model (ADR-0030 §2 clause (c))."
        )
        if strict_dead_models:
            violations.append(detail + " [--strict-dead-models: Tier-3 hard failure]")
        else:
            warnings.append(detail + " Warn-only during the Tier-0..2 migration window (F5).")

    tier0_enabled = frozenset(c for c in consumption_covered if not is_te_gated(c))
    te_deferred = frozenset(c for c in consumption_covered if is_te_gated(c))

    return GateResult(
        ok=not violations,
        violations=tuple(violations),
        warnings=tuple(warnings),
        consumption_covered=frozenset(consumption_covered),
        tier0_enabled=tier0_enabled,
        te_deferred=te_deferred,
        dead_models=frozenset(dead_models),
    )


# ---------------------------------------------------------------------------
# CLI wrapper (I/O boundary)
# ---------------------------------------------------------------------------


def run_gate(bpmn_dir: Path, workers_dir: Path, *, strict_dead_models: bool = False) -> GateResult:
    """Parse the spec + worker trees and evaluate the gate. The one I/O entry point for tests."""
    spec = build_spec_model(bpmn_dir)
    workers = build_worker_model(workers_dir)
    return evaluate(spec, workers, strict_dead_models=strict_dead_models)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_bpmn_error_allowlist",
        description=(
            "Boundary-proof CI gate (ADR-0030 §2): every WorkerBpmnError a worker raises must be a "
            "consumption-covered spec-declared boundary code on its external task."
        ),
    )
    parser.add_argument(
        "--bpmn-dir",
        default="spec/processes/bpmn",
        help="Directory of .bpmn process files (default: spec/processes/bpmn).",
    )
    parser.add_argument(
        "--workers-dir",
        default="src/maezo/tools/workers",
        help="Worker source tree to AST-scan (default: src/maezo/tools/workers).",
    )
    parser.add_argument(
        "--strict-dead-models",
        action="store_true",
        help="Harden clause (c) (dead models) from warn-only to a hard failure (Tier-3 close).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns 0 (pass) or 1 (fail); never raises on ordinary input."""
    args = build_arg_parser().parse_args(argv)
    bpmn_dir = Path(args.bpmn_dir)
    workers_dir = Path(args.workers_dir)

    if not bpmn_dir.is_dir():
        print(f"[bpmn-error-allowlist] FAIL: BPMN dir not found: {bpmn_dir}", file=sys.stderr)
        return 1
    if not workers_dir.is_dir():
        print(f"[bpmn-error-allowlist] FAIL: workers dir not found: {workers_dir}", file=sys.stderr)
        return 1

    result = run_gate(bpmn_dir, workers_dir, strict_dead_models=args.strict_dead_models)
    print(result.render())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
