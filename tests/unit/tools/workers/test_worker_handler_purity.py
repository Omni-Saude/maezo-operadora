"""Architecture test: worker handler PURITY (P1) — the property that makes the harness the single
audited chokepoint (T-C, T1.10; design MUST-FIX 1 / §4.2, ADR-0007 L0).

**Precondition P1 (handler purity).** A worker handler's *only* externally-visible effect is the
harness's own terminal `complete`/`bpmnError`/`failure` call. The handler body is a pure,
deterministic transform of process variables (`fn(variables) -> dict`) that performs **no**
non-idempotent external side-effect and mints **no** non-deterministic persisted identifier before
returning.

**Why P1 is load-bearing.** Emit-before-complete's fail-closed guarantee — *an emit failure ->
re-delivery -> re-run leaves no duplicate effect* — holds ONLY under P1. If a handler performs a
mid-body external effect `E` before returning, then an emit failure after `E` ran re-delivers and
re-runs `E`; the §4.3 dedup suppresses only the second audit ROW, never the second `E`. A P1
violation silently converts a fail-closed audit into a double-effect (or an audit/effect
misalignment) hazard.

This test enforces P1 statically (the design's "blunt import/call fence", MUST-FIX 1a — a robust
fence, not value-flow taint analysis) in two layers:

  (i)  NETWORK / ENGINE-EFFECT FENCE (strict): no domain worker module may import a network/effect
       client (httpx, aiokafka, kafka, requests, urllib, socket, ...). Handlers reach the engine
       ONLY through the harness's terminal call and the injected `dmn=` seam — never directly.
       This is the "handlers doing engine effects directly" property the harness exists to own.

  (ii) NON-DETERMINISM BASELINE (tracked): detect non-deterministic identifier sources
       (`time.time_ns`, `time.time`, `uuid.uuid4/uuid1`, `random.*`, `secrets.*`, `os.urandom`) in
       domain worker modules and assert they are confined to a DOCUMENTED baseline. A NEW module
       adopting one fails CI (the regression the fence exists to catch). This is a lint baseline
       with a tracking note per entry (the ADR-0024/T-H "grandfather with a ticket" pattern), NOT a
       disabled validation — it actively fails on new violations and on unexpected baseline drift.

FINDING (recorded for the R1 verifier): the design's MUST-FIX 1 named ONLY `ans_submit`'s
`protocolo_ans = sha256(time_ns())` as the P1 (ii) latent violation. The tree actually has SEVEN
domain worker modules minting non-deterministic identifiers (auth/contas/ans_submit/inadimplencia/
lgpd/recurso/reembolso) — the design UNDERCOUNTED, exactly as the R1 re-review caught the 5-vs-9
`start_process_idempotent` undercount. All seven are latent today (the minted values are output
variables of stub/no-network handlers, so no real external `E` re-runs); each becomes a genuine
double-effect / audit-misalignment hazard the moment its handler performs a real external effect
keyed on that identifier. `ans_submit` is the T-H (T2.6-owned) named co-requisite; the other six are
recorded here so they cannot silently become real effects without tripping this fence.
"""

from __future__ import annotations

import ast
from pathlib import Path

import maezo.tools.workers as workers_pkg

# Infra modules — NOT worker handlers, legitimately import network clients / use jitter. Excluded
# from the domain-handler fences below (each with the reason it is infra, not a BPMN handler).
_INFRA_MODULES: frozenset[str] = frozenset(
    {
        "__init__",
        "harness",  # the dispatch loop itself: owns the httpx transport + the audit emit seam
        "dmn_transport",  # the DMN transport seam (httpx) workers reach ONLY via the dmn= param
        "base",  # WorkerBase/FunctionWorker/registry scaffolding
        "bootstrap",  # composition root (register_all_workers)
        "ceilings",  # governance-ceiling resolver (policy loader, no engine/PHI)
        "phi_vars",  # one-way PHI redaction helper
        "_audit_ctx",  # per-task DMN-version collector (T-B)
        # T-D (merged in the T1.10 wave alongside this arch-test): the fresh-client-per-call
        # CibSevenTransport SEAM (GAP-INAD-1) — an engine transport like dmn_transport, reached
        # by workers ONLY via the engine= param; registers no workers, legitimately owns httpx.
        "cibseven_engine",
    }
)

# Network / engine-effect clients a domain handler must NEVER import directly (fence i). A handler's
# only engine touch is the harness terminal call + the injected dmn= seam.
_FORBIDDEN_IMPORT_ROOTS: frozenset[str] = frozenset(
    {"httpx", "aiokafka", "kafka", "requests", "urllib", "aiohttp", "socket", "websockets"}
)

# Non-deterministic identifier sources (fence ii). Dotted attribute access forms; `random`/
# `secrets`/`os.urandom` are matched by their module root (any attribute).
_NONDET_DOTTED: frozenset[str] = frozenset(
    {"time.time_ns", "time.time", "uuid.uuid4", "uuid.uuid1", "uuid.uuid3", "uuid.uuid5", "os.urandom"}
)
_NONDET_ROOTS: frozenset[str] = frozenset({"random", "secrets"})

# DOCUMENTED baseline of domain worker modules that currently mint non-deterministic identifiers.
# Value = the tracking note. NEW entries (a module not here) FAIL the fence. See module FINDING.
_NONDETERMINISM_BASELINE: dict[str, str] = {
    "ans_submit": "protocolo_ans = sha256(time_ns()) — T-H / T2.6 NAMED co-requisite (design "
    "MUST-FIX 1); MUST derive deterministically before transmit_to_ans becomes a real audited "
    "external effect.",
    "auth": "dossier_ref / auth_number = uuid4 — latent (output vars of a no-network handler); "
    "must become deterministic before any real keyed external effect (P1).",
    "contas": "time_ns()-derived id — latent; same P1 caveat.",
    "inadimplencia": "dossier_ref = uuid4 — latent; same P1 caveat.",
    "lgpd": "package_ref = uuid4 — latent; same P1 caveat.",
    "recurso": "protocolo = sha256(time_ns()) — latent; same P1 caveat.",
    "reembolso": "comprovante_ref = sha256(time_ns()) — latent; same P1 caveat.",
}


def _domain_worker_modules() -> dict[str, Path]:
    pkg_dir = Path(workers_pkg.__file__).parent
    return {p.stem: p for p in sorted(pkg_dir.glob("*.py")) if p.stem not in _INFRA_MODULES}


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _nondeterministic_calls(tree: ast.AST) -> set[str]:
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            dotted = f"{node.value.id}.{node.attr}"
            if dotted in _NONDET_DOTTED or node.value.id in _NONDET_ROOTS:
                hits.add(dotted)
    return hits


def test_domain_worker_modules_discovered() -> None:
    """Guard: the discovery actually found the 17 registered worker modules (so the fences below
    are not silently scanning an empty set)."""
    mods = _domain_worker_modules()
    # 17 SP-OP-* modules (bootstrap.ALL_WORKER_BOOTSTRAPS = 17: 16 + events).
    assert len(mods) == 17, f"expected 17 domain worker modules, found {sorted(mods)}"


def test_no_domain_worker_imports_a_network_or_engine_client() -> None:
    """Fence (i): a handler reaching the engine/Kafka DIRECTLY would bypass the harness chokepoint
    and break emit-before-complete. NONE may import a network client (strict — TRUE today)."""
    offenders: dict[str, set[str]] = {}
    for name, path in _domain_worker_modules().items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        bad = _imported_roots(tree) & _FORBIDDEN_IMPORT_ROOTS
        if bad:
            offenders[name] = bad
    assert not offenders, (
        "P1 violation — worker handler modules import a network/engine-effect client directly "
        f"(must go through the harness / dmn= seam): {offenders}"
    )


def test_nondeterministic_identifier_minting_confined_to_documented_baseline() -> None:
    """Fence (ii): non-deterministic identifier minting must stay confined to the documented
    baseline. A NEW module adopting it fails here (the P1 regression this fence catches)."""
    actual: dict[str, set[str]] = {}
    for name, path in _domain_worker_modules().items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _nondeterministic_calls(tree)
        if hits:
            actual[name] = hits

    actual_modules = set(actual)
    baseline_modules = set(_NONDETERMINISM_BASELINE)

    new_violations = actual_modules - baseline_modules
    assert not new_violations, (
        "NEW P1 (ii) violation — module(s) started minting non-deterministic identifiers outside "
        f"the documented baseline: { {m: sorted(actual[m]) for m in new_violations} }. Either make "
        "the minted value deterministic (P1) or add an explicit tracking entry to "
        "_NONDETERMINISM_BASELINE with the co-requisite that will fix it."
    )
    # If a baseline entry is FIXED (no longer mints), require the stale entry to be removed — keeps
    # the baseline honest and shrinking, never a rubber stamp.
    resolved = baseline_modules - actual_modules
    assert not resolved, (
        f"baseline modules no longer mint non-deterministic identifiers: {sorted(resolved)} — "
        "remove them from _NONDETERMINISM_BASELINE (the P1 hazard is closed there)."
    )


def test_ans_submit_protocolo_is_the_named_th_corequisite() -> None:
    """Pin the design's NAMED T-H co-requisite so it cannot silently disappear from the baseline
    without the deterministic fix landing (MUST-FIX 1 co-requisite)."""
    assert "ans_submit" in _NONDETERMINISM_BASELINE
    path = _domain_worker_modules()["ans_submit"]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert "time.time_ns" in _nondeterministic_calls(tree), (
        "ans_submit no longer mints protocolo_ans from time_ns — if T-H landed the deterministic "
        "derivation, remove ans_submit from _NONDETERMINISM_BASELINE."
    )
