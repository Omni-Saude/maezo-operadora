"""Repo-wide fence: every live suite's DEFAULT coordinate must name infrastructure this repo
actually serves. A default nobody serves is a silent skip, not a proof.

Root cause this closes (gap LIVE-SUITES-SILENT-SKIP-AUDIT, 2026-09-04; same defect class as
PRODUCER-LIVE-19092-DEFAULT, which #306 fixed one file at a time). Six live suites shipped with a
hardcoded fallback DSN pointing at a Postgres NOTHING in this repository ever brings up — no
`docker-compose.yml` service, no CI service container, no documented bring-up command:

    tests/integration/platform/test_notifications_bridge_live_pg.py   localhost:5647   ( 5 tests)
    tests/unit/a2a/test_a2a_edge_live_pg.py                           localhost:5643   ( 7 tests)
    tests/unit/gateway/test_audit_anchor_drills_live_pg.py            localhost:5466   ( 9 tests)
    tests/unit/platform/integrations/test_amh_inbox_live_pg.py        localhost:5647   (22 tests)
    tests/unit/platform/webhooks/whatsapp/test_dispatch_live_pg.py    ckpt@:5663/ckpt  ( 1 test )
    tests/unit/runtime/agent_runtime/test_checkpoint_live_pg.py       ckpt@:5658/ckpt  ( 2 tests)

Each one justified its port in prose as ISOLATION ("a FREE, dedicated port", "deliberately NOT the
compose stack's 5433/5432"). The prose was sincere and the effect was the opposite of a proof: with
the project's own stack up and no environment variable exported, all 46 of those tests reported
`COULD NOT VERIFY` and the run still printed a green summary. Isolation was never coming from the
port anyway — every one of those suites already works inside its OWN per-run tenant schema (or its
own per-run thread ids), which is what actually keeps concurrent suites off each other's rows.

This fence makes the class structurally impossible to reintroduce: it EXECUTES each suite's own DSN
resolver under a cleared environment and compares the result against the coordinates parsed out of
`docker-compose.yml` itself, so it stays correct when the compose port changes and it cannot be
satisfied by a comment. It also pins the `MAEZO_PG_HOST_PORT` override every suite must honour —
the one CI's `integration` and `chaos` jobs pin to 5432 — so a future CI job that adds the Postgres
service does not have to special-case any file.

Note what this fence deliberately does NOT assert: that the suites are COLLECTED by a CI lane. On
`main` @ 2e46145 the seven `tests/unit/**/*_live_pg.py` suites are collected only by the `quality`
job (`uv run pytest tests/ -q --cov...`, no marker filter — so they are collected and skipped, 649
skips in run 33853408226), never by the `integration` job (path-scoped to `tests/integration`).
Serving them a Postgres is a `.github/workflows/ci.yml` change and therefore owner-gated; this
fence closes the half that lives in `tests/`.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final
from unittest import mock

import pytest
import yaml

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_TESTS_ROOT: Final[Path] = _REPO_ROOT / "tests"
_COMPOSE: Final[Path] = _REPO_ROOT / "docker-compose.yml"

#: Names a live suite may give its zero-argument DSN resolver. The set is closed on purpose: a new
#: suite that invents a name is reported by `test_every_live_suite_exposes_a_resolver_or_is_
#: explicitly_excluded` instead of quietly escaping the fence.
_PG_RESOLVER_NAMES: Final[tuple[str, ...]] = ("_default_test_dsn", "_pg_dsn", "_audit_dsn", "_dsn")
_KAFKA_RESOLVER_NAME: Final[str] = "_kafka_bootstrap_servers"


@dataclass(frozen=True)
class ExplicitFixtureContract:
    """A live suite whose coordinates come only from a private, ROOT-provided fixture."""

    loader_module: str
    loader_name: str
    environment_variable: str
    manifest_name: str
    schema: str
    database_host: str
    database_port: int


_EXPLICIT_FIXTURES: Final[dict[str, ExplicitFixtureContract]] = {
    "tests/integration/gateway/test_human_relay_live_cib.py": ExplicitFixtureContract(
        loader_module="tests.support.human_relay_live",
        loader_name="RelayConfig",
        environment_variable="MAEZO_HUMAN_RELAY_PRIVATE_DIR",
        manifest_name="relay-fixture.json",
        schema="human-relay-fixture.v1",
        database_host="127.0.0.1",
        database_port=15433,
    )
}

#: Modules whose filename matches the live-suite glob but which need no infrastructure at all.
#: Each entry is a claim this file's own tests re-check (they must define NO resolver) AND a claim
#: `test_name_only_entries_are_actually_discovered` re-checks: the module must actually be produced
#: by `_iter_live_suites()`, or the exclusion can never be exercised by
#: `test_every_live_suite_exposes_a_resolver_or_is_explicitly_excluded` and is dead on arrival —
#: exactly the bug `test_live_dispatch_wiring.py` was listed here as until this fix (see
#: `_iter_live_suites`'s docstring for why that module was never in scope to begin with).
_NAME_ONLY: Final[dict[str, str]] = {
    "tests/integration/gateway/test_decision_binding_live_pg.py": (
        "ADR-0049 D5 decision-binding suite against a ROOT-supplied DISPOSABLE CIB PostgreSQL with "
        "mTLS identities: its only coordinate is the JSON manifest at "
        "MAEZO_DECISION_BINDING_TEST_CONFIG (host/port/database/roles/certificate files inside the "
        "manifest; no caller DSN, no repo-served default), and it skips loudly when unset — the "
        "same class as test_inference_live.py below. The relay grammar of _EXPLICIT_FIXTURES "
        "(loader class + pinned compose host/port) does not describe it. PR-A landing record."
    ),
    "tests/unit/a2a/test_a2a_edge_live_pg_fixture.py": (
        "four pure unit fences for the companion A2A live-engine fixture: they inspect composition "
        "and replace the engine resolver/client in-process, with no Postgres, broker, engine, "
        "network access, or integration marker; only the filename matches the live-suite glob."
    ),
    "tests/unit/runtime/test_inference_live.py": (
        "live Anthropic API call (T1.7), `pytestmark = pytest.mark.llm_live`; its coordinate is an "
        "API KEY, not a repo-served port, and it skips loudly via `skipif` when no key is set — "
        "by design, and outside this fence's subject."
    ),
}


def _iter_live_suites() -> list[Path]:
    """Every `tests/**/test_*_live*.py` module, sorted. Measured today: 12 modules.

    The `_live` prefix in the glob matters: it excludes files that merely contain the letters
    (`..._rede-live-ry...`, `..._de-live-ry...`) — `test_t33_a1_cancel_handoff_redelivery_
    idempotency.py` and `test_duplicate_fact_delivery_attack.py` are NOT live suites and must not
    be dragged in by a lazier pattern.

    The SAME exclusion, less obviously, also drops `tests/unit/gateway/seams/test_live_dispatch_
    wiring.py` and this file itself (`test_live_suite_defaults_are_served.py`): `fnmatch` requires
    the literal `_live` substring to occur strictly AFTER the `test_` prefix the pattern already
    consumes, and both filenames start `test_live_...` — `live` immediately follows `test_` with
    no separating underscore, so no `_live` substring exists anywhere in either name
    (`fnmatch('test_live_dispatch_wiring.py', 'test_*_live*.py')` is `False`). This is NOT a
    loophole to patch: `test_live_dispatch_wiring.py`'s "live" names the live AGENT PATH (design
    §5.5), not live infrastructure — pure introspection over the composition root, `pytestmark =
    pytest.mark.anyio`, no DSN and no broker — so it needs no entry in `_NAME_ONLY` at all, and
    listing it there anyway (as an earlier revision of this fence did) created an exclusion
    `test_every_live_suite_exposes_a_resolver_or_is_explicitly_excluded` could never reach, because
    the loop it guards only visits paths this function yields.
    """
    return sorted(p for p in _TESTS_ROOT.rglob("test_*_live*.py") if "__pycache__" not in p.parts)


def _dotted(path: Path) -> str:
    return ".".join(path.relative_to(_REPO_ROOT).with_suffix("").parts)


def _import(path: Path) -> ModuleType:
    """Import a live suite module. Importing never runs a test and never opens a connection —
    every suite in scope resolves its DSN inside a function, not at module import time (which is
    itself part of what this fence pins)."""
    return importlib.import_module(_dotted(path))


def _resolver(module: ModuleType, names: tuple[str, ...]) -> tuple[str, Callable[[], str]] | None:
    found = [n for n in names if callable(getattr(module, n, None))]
    assert len(found) <= 1, (
        f"{module.__name__} defines more than one DSN resolver ({found}) — this fence would not "
        "know which one the suite's fixtures actually call"
    )
    if not found:
        return None
    fn: Callable[[], str] = getattr(module, found[0])
    return found[0], fn


def _explicit_fixture_loader(contract: ExplicitFixtureContract) -> type:
    module = importlib.import_module(contract.loader_module)
    loader = getattr(module, contract.loader_name)
    assert isinstance(loader, type), f"{contract.loader_module}.{contract.loader_name} is not a class"
    return loader


def _is_pytest_mark(node: ast.expr, name: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
        and node.value.attr == "mark"
        and node.attr == name
    )


def _pytest_parametrize_names(node: ast.expr) -> set[str] | None:
    """Return finite literal argnames; None means this is not a supported parametrization."""
    if not (isinstance(node, ast.Call) and _is_pytest_mark(node.func, "parametrize")):
        return set()
    argnames = (
        node.args[0]
        if node.args
        else next((keyword.value for keyword in node.keywords if keyword.arg == "argnames"), None)
    )
    if isinstance(argnames, ast.Constant) and isinstance(argnames.value, str):
        return {name.strip() for name in argnames.value.split(",") if name.strip()}
    if isinstance(argnames, (ast.List, ast.Tuple)) and all(
        isinstance(item, ast.Constant) and isinstance(item.value, str) for item in argnames.elts
    ):
        return {item.value for item in argnames.elts}
    return None


def _parametrization_may_override(node: ast.expr, fixture_name: str) -> bool:
    names = _pytest_parametrize_names(node)
    return names is None or fixture_name in names


def _is_qualified_pytest_mark(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
        and node.value.attr == "mark"
    )


def _supported_test_decorator(node: ast.expr, fixture_name: str) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    if not _is_qualified_pytest_mark(target):
        return False
    if target.attr != "parametrize":
        return True
    return isinstance(node, ast.Call) and not _parametrization_may_override(node, fixture_name)


#: Module-level marks this grammar tolerates on an explicit-fixture suite. The set stays CLOSED
#: (an unknown mark means the suite's shape drifted and the fence must re-read it), but a mark that
#: can only change SELECTION — never the `live` fixture's lifecycle, its parametrization, or which
#: tests request it — is safe to admit. `root_fixture` is exactly that: it removes the suite from
#: the global `-m integration` lane, which cannot hold its private ROOT fixture, and it is itself
#: fenced by `tests/unit/ci/test_root_fixture_deselection.py` (registered marker, reviewed
#: allowlist, deselection proved to fire). `integration`/`asyncio` remain REQUIRED separately by
#: `required_module_marks`, so admitting this one cannot let a suite drop either of them.
_SELECTION_ONLY_MODULE_MARKS: Final[tuple[str, ...]] = ("root_fixture",)


def _supported_module_mark(node: ast.expr, fixture_name: str) -> bool:
    if _is_pytest_mark(node, "integration") or _is_pytest_mark(node, "asyncio"):
        return True
    if any(_is_pytest_mark(node, name) for name in _SELECTION_ONLY_MODULE_MARKS):
        return True
    return (
        isinstance(node, ast.Call)
        and _is_pytest_mark(node.func, "parametrize")
        and not _parametrization_may_override(node, fixture_name)
    )


def _is_call(node: ast.expr, owner: str, method: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == owner
        and node.func.attr == method
        and not node.args
        and not node.keywords
    )


def _assigns_call(statement: ast.stmt, target: str, owner: str, method: str) -> bool:
    return (
        isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and statement.targets[0].id == target
        and _is_call(statement.value, owner, method)
    )


def _awaits_call(statement: ast.stmt, owner: str, method: str) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Await)
        and _is_call(statement.value.value, owner, method)
    )


def _canonical_live_fixture(node: ast.AsyncFunctionDef) -> bool:
    """Match the one supported relay fixture lifecycle, without inferring Python data flow."""
    if len(node.body) != 4:
        return False
    load, artifacts, construct, lifecycle = node.body
    if not _assigns_call(load, "config", "RelayConfig", "load"):
        return False
    if not (
        isinstance(artifacts, ast.Assign)
        and len(artifacts.targets) == 1
        and isinstance(artifacts.targets[0], ast.Name)
        and artifacts.targets[0].id == "artifacts"
        and isinstance(artifacts.value, ast.Call)
        and isinstance(artifacts.value.func, ast.Name)
        and artifacts.value.func.id == "artifact_directory"
        and len(artifacts.value.args) == 2
        and isinstance(artifacts.value.args[0], ast.Name)
        and artifacts.value.args[0].id == "config"
        and isinstance(artifacts.value.args[1], ast.Attribute)
        and artifacts.value.args[1].attr == "name"
        and isinstance(artifacts.value.args[1].value, ast.Attribute)
        and artifacts.value.args[1].value.attr == "node"
        and isinstance(artifacts.value.args[1].value.value, ast.Name)
        and artifacts.value.args[1].value.value.id == "request"
        and not artifacts.value.keywords
    ):
        return False
    if not (
        isinstance(construct, ast.Assign)
        and len(construct.targets) == 1
        and isinstance(construct.targets[0], ast.Name)
        and construct.targets[0].id == "fixture"
        and isinstance(construct.value, ast.Call)
        and isinstance(construct.value.func, ast.Name)
        and construct.value.func.id == "LiveRelayFixture"
        and [argument.id for argument in construct.value.args if isinstance(argument, ast.Name)]
        == ["config", "artifacts"]
        and len(construct.value.args) == 2
        and not construct.value.keywords
    ):
        return False
    return (
        isinstance(lifecycle, ast.Try)
        and len(lifecycle.body) == 2
        and _awaits_call(lifecycle.body[0], "fixture", "open")
        and isinstance(lifecycle.body[1], ast.Expr)
        and isinstance(lifecycle.body[1].value, ast.Yield)
        and isinstance(lifecycle.body[1].value.value, ast.Name)
        and lifecycle.body[1].value.value.id == "fixture"
        and not lifecycle.handlers
        and not lifecycle.orelse
        and len(lifecycle.finalbody) == 1
        and _awaits_call(lifecycle.finalbody[0], "fixture", "close")
    )


def _bound_target_names(node: ast.expr) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Starred):
        return _bound_target_names(node.value)
    if isinstance(node, (ast.List, ast.Tuple)):
        return set().union(*(_bound_target_names(item) for item in node.elts))
    return set()


def _module_scope_bindings(tree: ast.Module) -> dict[str, list[ast.stmt]]:
    """Inventory lexical module bindings without evaluating branches or function bodies."""
    result: dict[str, list[ast.stmt]] = {}

    def record(name: str, statement: ast.stmt) -> None:
        result.setdefault(name, []).append(statement)

    def visit(statement: ast.stmt) -> None:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            record(statement.name, statement)
            return
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            for alias in statement.names:
                record(alias.asname or alias.name.split(".")[0], statement)
            return
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            targets.extend(statement.targets)
        elif (
            isinstance(statement, ast.AnnAssign)
            and statement.value is not None
            or isinstance(statement, (ast.AugAssign, ast.For, ast.AsyncFor))
        ):
            targets.append(statement.target)
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            targets.extend(item.optional_vars for item in statement.items if item.optional_vars is not None)
        elif isinstance(statement, ast.Delete):
            targets.extend(statement.targets)
        for target in targets:
            for name in _bound_target_names(target):
                record(name, statement)
        if isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While)):
            for child in [*statement.body, *statement.orelse]:
                visit(child)
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            for child in statement.body:
                visit(child)
        elif isinstance(statement, (ast.Try, ast.TryStar)):
            for child in [*statement.body, *statement.orelse, *statement.finalbody]:
                visit(child)
            for handler in statement.handlers:
                if handler.name:
                    record(handler.name, statement)
                for child in handler.body:
                    visit(child)

    for statement in tree.body:
        visit(statement)
    return result


def _fixture_registration_name(node: ast.AsyncFunctionDef) -> str | None:
    if len(node.decorator_list) != 1:
        return None
    decorator = node.decorator_list[0]
    return (
        node.name
        if isinstance(decorator, ast.Attribute)
        and isinstance(decorator.value, ast.Name)
        and decorator.value.id == "pytest"
        and decorator.attr == "fixture"
        else None
    )


def _suite_loads_explicit_fixture(path: Path, contract: ExplicitFixtureContract) -> bool:
    """Recognize the finite, fail-closed grammar of the actual relay fixture lifecycle."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    supported_top_level = all(
        isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef))
        or (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        or (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "pytestmark"
        )
        for node in tree.body
    )
    supported_imports = not any(
        isinstance(node, ast.ImportFrom) and node.module == "pytest" for node in tree.body
    )
    protected_names_are_not_assigned = not any(
        isinstance(node, ast.Name)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and node.id in {contract.loader_name, "LiveRelayFixture", "pytest"}
        for node in ast.walk(tree)
    )
    loader_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == contract.loader_module
        and {alias.name for alias in node.names if alias.asname is None}
        >= {contract.loader_name, "LiveRelayFixture"}
    ]
    pytest_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.Import)
        and any(alias.name == "pytest" and alias.asname is None for alias in node.names)
    ]
    bindings = _module_scope_bindings(tree)
    imports_fixture = len(loader_imports) == 1 and all(
        bindings.get(name) == loader_imports for name in (contract.loader_name, "LiveRelayFixture")
    )
    imports_pytest = len(pytest_imports) == 1 and bindings.get("pytest") == pytest_imports
    pytestmark_values = [
        node.value
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and (
            any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets)
            if isinstance(node, ast.Assign)
            else isinstance(node.target, ast.Name) and node.target.id == "pytestmark"
        )
    ]
    module_marks = [
        candidate
        for value in pytestmark_values
        for candidate in (value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value])
    ]
    supported_module_marks = bool(module_marks) and all(
        _supported_module_mark(candidate, "live") for candidate in module_marks
    )
    required_module_marks = all(
        any(_is_pytest_mark(candidate, name) for candidate in module_marks)
        for name in ("integration", "asyncio")
    )
    integration_marked = len(pytestmark_values) == 1 and (
        _is_pytest_mark(pytestmark_values[0], "integration")
        or (
            isinstance(pytestmark_values[0], (ast.List, ast.Tuple))
            and any(_is_pytest_mark(value, "integration") for value in pytestmark_values[0].elts)
        )
    )
    fixture_definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and _fixture_registration_name(node) == "live"
    ]
    test_functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    ]
    tests_request_live = bool(test_functions) and all(
        any(argument.arg == "live" for argument in [*node.args.posonlyargs, *node.args.args])
        and all(_supported_test_decorator(decorator, "live") for decorator in node.decorator_list)
        and not any(
            isinstance(candidate, ast.Name)
            and isinstance(candidate.ctx, ast.Store)
            and candidate.id == "live"
            for statement in node.body
            for candidate in ast.walk(statement)
        )
        for node in test_functions
    )
    if (
        not imports_fixture
        or not imports_pytest
        or not supported_top_level
        or not supported_imports
        or not protected_names_are_not_assigned
        or not integration_marked
        or not supported_module_marks
        or not required_module_marks
        or len(fixture_definitions) != 1
        or bindings.get("live") != fixture_definitions
        or not tests_request_live
    ):
        return False
    return _canonical_live_fixture(fixture_definitions[0])


# ---------------------------------------------------------------------------
# What the repo actually serves, read from docker-compose.yml (never hardcoded)
# ---------------------------------------------------------------------------


def _compose() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    return data


#: D6-03: `docker-compose.yml`'s postgres credentials moved from a bare literal to
#: `${VAR:-default}` (a normalized "password in repo" pattern, same fix as the port mapping
#: below always used). This extracts the DEFAULT half of that syntax — the value docker compose
#: actually serves when no override is exported, which is exactly what a bare `pytest` run (no
#: env vars set) gets — so the fence keeps enforcing the real served coordinate, never a template
#: string, and stays correct if the default changes.
_VAR_DEFAULT_RE: Final = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}")


def _resolve_var_default(value: str) -> str:
    """`${VAR:-default}` -> `default`; a plain literal (no such token) passes through unchanged."""
    match = _VAR_DEFAULT_RE.fullmatch(value)
    return match.group(1) if match else value


def _compose_pg_coordinates() -> tuple[str, str, str, str]:
    """(user, password, database, default host port) of the compose `postgres` service."""
    service = _compose()["services"]["postgres"]
    env = service["environment"]
    published: str = service["ports"][0]  # "${MAEZO_PG_HOST_PORT:-5433}:5432"
    match = re.fullmatch(r"\$\{MAEZO_PG_HOST_PORT:-(\d+)\}:\d+", published)
    assert match, (
        f"docker-compose.yml's postgres port mapping is {published!r}, which this fence cannot "
        "parse — update the fence together with the compose file"
    )
    return (
        _resolve_var_default(env["POSTGRES_USER"]),
        _resolve_var_default(env["POSTGRES_PASSWORD"]),
        _resolve_var_default(env["POSTGRES_DB"]),
        match.group(1),
    )


def _compose_kafka_external_port() -> str:
    """Host port of the compose Kafka service's EXTERNAL listener (advertised as localhost)."""
    for published in _compose()["services"]["kafka"]["ports"]:
        host, _, container = str(published).partition(":")
        if host == container:  # "9092:9092" — the EXTERNAL listener, not the 29092 internal one
            return host
    raise AssertionError("docker-compose.yml's kafka service publishes no host==container port")


def _expected_default_dsn() -> str:
    user, password, database, port = _compose_pg_coordinates()
    return f"postgresql://{user}:{password}@localhost:{port}/{database}"


# ---------------------------------------------------------------------------
# The fence itself
# ---------------------------------------------------------------------------


def test_every_live_suite_exposes_a_resolver_or_is_explicitly_excluded() -> None:
    """No live suite may be outside this fence by accident. A module either resolves a Postgres
    DSN, resolves a Kafka bootstrap, loads a verified explicit fixture, or appears in `_NAME_ONLY`
    with a written reason. Explicit fixtures are infrastructure-bearing, not exclusions."""
    unclassified: list[str] = []
    for path in _iter_live_suites():
        rel = str(path.relative_to(_REPO_ROOT))
        module = _import(path)
        has_pg = _resolver(module, _PG_RESOLVER_NAMES) is not None
        has_kafka = _resolver(module, (_KAFKA_RESOLVER_NAME,)) is not None
        explicit = _EXPLICIT_FIXTURES.get(rel)
        assert not (explicit and rel in _NAME_ONLY), (
            f"{rel} cannot be both an infrastructure-bearing explicit fixture and NAME_ONLY"
        )
        if explicit:
            assert not has_pg and not has_kafka, (
                f"{rel} has an explicit private fixture and a shared resolver; keep one real path"
            )
            assert getattr(module, explicit.loader_name, None) is _explicit_fixture_loader(explicit)
            assert _suite_loads_explicit_fixture(path, explicit), (
                f"{rel} no longer calls {explicit.loader_name}.load() from a pytest fixture"
            )
            continue
        if rel in _NAME_ONLY:
            assert not has_pg and not has_kafka, (
                f"{rel} is listed in _NAME_ONLY as needing no infrastructure, but it defines an "
                "infrastructure resolver — remove the exclusion instead of keeping a stale one"
            )
            continue
        if not (has_pg or has_kafka):
            unclassified.append(rel)
    assert not unclassified, (
        "these live suites expose no resolver this fence recognises, so their default coordinate "
        f"is unchecked — name it one of {_PG_RESOLVER_NAMES} / {_KAFKA_RESOLVER_NAME}, register a "
        "verified explicit fixture, or add a genuinely infrastructure-free suite to _NAME_ONLY "
        "with a reason:\n  " + "\n  ".join(unclassified)
    )


def test_every_live_pg_suite_defaults_to_the_compose_postgres() -> None:
    """With NO environment at all, every live-PG suite must resolve the DSN the compose stack
    actually publishes. This is the gap itself: 5647/5643/5466/5663/5658 were all unserved."""
    expected = _expected_default_dsn()
    wrong: list[str] = []
    for path in _iter_live_suites():
        rel = str(path.relative_to(_REPO_ROOT))
        if rel in _NAME_ONLY:
            continue
        resolved = _resolver(_import(path), _PG_RESOLVER_NAMES)
        if resolved is None:
            continue
        name, fn = resolved
        with mock.patch.dict(os.environ, {}, clear=True):
            actual = fn()
        if actual != expected:
            wrong.append(f"{rel}::{name}() -> {actual!r}")
    assert not wrong, (
        f"these live-PG suites do not default to the Postgres docker-compose.yml serves "
        f"({expected}) — a default nobody serves is a silent skip, not a proof:\n  " + "\n  ".join(wrong)
    )


def test_every_live_pg_suite_honours_the_ci_host_port_pin() -> None:
    """`MAEZO_PG_HOST_PORT` must move the default, because that is the ONLY knob CI's integration
    and chaos jobs set (both pin it to 5432 against the same compose Postgres). A suite that
    hardcodes 5433 would skip on a runner that publishes 5432."""
    user, password, database, default_port = _compose_pg_coordinates()
    pinned = "5432" if default_port != "5432" else "5433"
    expected = f"postgresql://{user}:{password}@localhost:{pinned}/{database}"
    wrong: list[str] = []
    for path in _iter_live_suites():
        rel = str(path.relative_to(_REPO_ROOT))
        if rel in _NAME_ONLY:
            continue
        resolved = _resolver(_import(path), _PG_RESOLVER_NAMES)
        if resolved is None:
            continue
        name, fn = resolved
        with mock.patch.dict(os.environ, {"MAEZO_PG_HOST_PORT": pinned}, clear=True):
            actual = fn()
        if actual != expected:
            wrong.append(f"{rel}::{name}() -> {actual!r}")
    assert not wrong, (
        f"these live-PG suites ignore MAEZO_PG_HOST_PORT={pinned} (expected {expected}) — a CI job "
        "that serves the compose Postgres on its own port would still skip them:\n  " + "\n  ".join(wrong)
    )


def test_every_live_pg_suite_lets_its_own_env_var_win() -> None:
    """The escape hatch must survive the fix: an explicitly exported DSN always wins over the
    compose default, so a throwaway server is still one `export` away."""
    sentinel = "postgresql://sentinel:sentinel@127.0.0.1:1/sentinel"
    # Every DSN env var any live suite reads. Setting them all at once is safe: each suite reads
    # exactly one, and MAEZO_PG_HOST_PORT is deliberately ALSO set to prove the explicit DSN wins
    # over it too.
    env = {
        "MAEZO_TEST_DATABASE_URL": sentinel,
        "MAEZO_TEST_A2A_EDGE_DATABASE_URL": sentinel,
        "MAEZO_TEST_AMH_INBOX_DATABASE_URL": sentinel,
        "MAEZO_TEST_AUDIT_ANCHOR_DRILL_DATABASE_URL": sentinel,
        "MAEZO_TEST_CHECKPOINT_DATABASE_URL": sentinel,
        "MAEZO_PG_HOST_PORT": "5999",
    }
    ignored: list[str] = []
    for path in _iter_live_suites():
        rel = str(path.relative_to(_REPO_ROOT))
        if rel in _NAME_ONLY:
            continue
        resolved = _resolver(_import(path), _PG_RESOLVER_NAMES)
        if resolved is None:
            continue
        name, fn = resolved
        with mock.patch.dict(os.environ, env, clear=True):
            actual = fn()
        if actual != sentinel:
            ignored.append(f"{rel}::{name}() -> {actual!r}")
    assert not ignored, (
        "these live-PG suites ignore their own explicit DSN override, so a throwaway server can no "
        "longer be pointed at:\n  " + "\n  ".join(ignored)
    )


def test_every_live_kafka_suite_defaults_to_the_compose_broker() -> None:
    """Same rule for the broker: the default must be the EXTERNAL listener compose advertises as
    `localhost`. This is the shape gap PRODUCER-LIVE-19092-DEFAULT fixed by hand in #306; the
    fence is what keeps it fixed."""
    expected = f"localhost:{_compose_kafka_external_port()}"
    wrong: list[str] = []
    for path in _iter_live_suites():
        rel = str(path.relative_to(_REPO_ROOT))
        if rel in _NAME_ONLY:
            continue
        resolved = _resolver(_import(path), (_KAFKA_RESOLVER_NAME,))
        if resolved is None:
            continue
        name, fn = resolved
        with mock.patch.dict(os.environ, {}, clear=True):
            actual = fn()
        if actual != expected:
            wrong.append(f"{rel}::{name}() -> {actual!r}")
    assert not wrong, (
        f"these live-Kafka suites do not default to the broker docker-compose.yml serves "
        f"({expected}):\n  " + "\n  ".join(wrong)
    )


def test_scan_is_not_vacuous() -> None:
    """A fence that scanned zero suites would be worthless. Measured today: 12 live suites, 10
    resolving Postgres, 2 resolving Kafka (`test_events_kafka_producer_live.py` resolves both), 1
    name-only. The floors below are NOT uniformly "just below" that count: `>= 12` (suites) and
    `>= 2` (kafka) sit AT today's measured count — deleting a single live suite, or the last Kafka
    suite, fails here immediately — while `>= 8` (pg) sits two below today's 10, leaving headroom
    for one legitimate removal without having to edit this floor in the same PR (see advisory A6,
    gap LIVE-SUITES-SILENT-SKIP-AUDIT's gatekeeper review, 2026-09-04)."""
    suites = _iter_live_suites()
    assert len(suites) >= 12, (
        f"expected at least 12 test_*_live*.py modules under {_TESTS_ROOT}, found {len(suites)}"
    )
    pg = [p for p in suites if _resolver(_import(p), _PG_RESOLVER_NAMES) is not None]
    kafka = [p for p in suites if _resolver(_import(p), (_KAFKA_RESOLVER_NAME,)) is not None]
    assert len(pg) >= 8, f"expected at least 8 live-PG suites, found {len(pg)}"
    assert len(kafka) >= 2, f"expected at least 2 live-Kafka suites, found {len(kafka)}"
    for rel in _NAME_ONLY:
        assert (_REPO_ROOT / rel).is_file(), (
            f"{rel} is excluded by name but no longer exists — drop the stale _NAME_ONLY entry"
        )


def test_name_only_entries_are_actually_discovered() -> None:
    """A `_NAME_ONLY` entry only means something if `_iter_live_suites()` actually yields that
    path — otherwise `test_every_live_suite_exposes_a_resolver_or_is_explicitly_excluded`'s guard
    for it can never fire, and the module's real default coordinate (if it has infrastructure
    after all) goes unchecked by every other test in this file: a dead exclusion, not a live one.

    This is exactly the defect gap LIVE-SUITES-SILENT-SKIP-AUDIT's own fence shipped with:
    `test_live_dispatch_wiring.py` was listed in `_NAME_ONLY` before this fix, but `test_*_live*.py`
    never matched it (no `_live` substring occurs after `test_` — see `_iter_live_suites`'s
    docstring), so the exclusion it carried had been inert since the fence's first commit."""
    discovered = {str(p.relative_to(_REPO_ROOT)) for p in _iter_live_suites()}
    dead = sorted(set(_NAME_ONLY) - discovered)
    assert not dead, (
        "these _NAME_ONLY entries are never produced by _iter_live_suites(), so their exclusion "
        "guard can never fire — either the entry is stale (drop it) or the glob needs widening to "
        "reach it:\n  " + "\n  ".join(dead)
    )


def test_explicit_fixture_entries_are_discovered_and_actually_loaded() -> None:
    """An explicit-fixture category is executable classification, never a name-only pardon."""
    discovered = {str(path.relative_to(_REPO_ROOT)): path for path in _iter_live_suites()}
    dead = sorted(set(_EXPLICIT_FIXTURES) - set(discovered))
    assert not dead, "explicit fixture entries outside live-suite discovery:\n  " + "\n  ".join(dead)
    assert not set(_EXPLICIT_FIXTURES).intersection(_NAME_ONLY)
    for rel, contract in _EXPLICIT_FIXTURES.items():
        path = discovered[rel]
        module = _import(path)
        assert getattr(module, contract.loader_name, None) is _explicit_fixture_loader(contract)
        assert _suite_loads_explicit_fixture(path, contract)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "def helper():\n    return RelayConfig.load()\n",
        "@pytest.fixture\ndef live():\n    return object()\n",
    ],
)
def test_explicit_fixture_category_rejects_an_unused_loader(tmp_path: Path, body: str) -> None:
    candidate = tmp_path / "test_unused_live_fixture.py"
    candidate.write_text(
        "import pytest\n"
        "from tests.support.human_relay_live import RelayConfig\n"
        "pytestmark = pytest.mark.integration\n" + body,
        encoding="utf-8",
    )
    assert not _suite_loads_explicit_fixture(candidate, _relay_contract())


def _actual_relay_source() -> str:
    return (_REPO_ROOT / "tests/integration/gateway/test_human_relay_live_cib.py").read_text(encoding="utf-8")


def _actual_pytestmark_elements(source: str) -> str:
    """The real suite's module marks, as source text — DERIVED, never hardcoded.

    The mutation tests below rewrite the suite's `pytestmark` to prove the grammar rejects the
    mutation. A hardcoded needle makes them VACUOUS the moment the real mark list legitimately
    changes: `str.replace` no-ops, the "mutated" file is the pristine one, and `assert not
    _suite_loads_explicit_fixture(...)` fails (which is how PR-A's `root_fixture` mark surfaced
    this) — or, worse, would silently pass for any assertion written the other way round.
    """
    lines = [line for line in source.splitlines() if line.startswith("pytestmark = ")]
    assert len(lines) == 1, f"expected exactly one module-level pytestmark; got {lines}"
    inner = lines[0].split("=", 1)[1].strip()
    assert inner.startswith("[") and inner.endswith("]"), inner
    return inner[1:-1].strip()


def _mutate_pytestmark(source: str, replacement: str) -> str:
    lines = [line for line in source.splitlines() if line.startswith("pytestmark = ")]
    assert len(lines) == 1, lines
    mutated = source.replace(lines[0], replacement, 1)
    assert mutated != source, "pytestmark mutation did not apply — the test would be vacuous"
    return mutated


def _relay_suite_source(fixture_body: str, *, marker: str = "pytestmark") -> str:
    return (
        "import pytest\n"
        "from tests.support.human_relay_live import LiveRelayFixture, RelayConfig\n"
        f"{marker} = [pytest.mark.integration, pytest.mark.asyncio]\n"
        "def artifact_directory(config, node_name):\n"
        "    return object()\n"
        "@pytest.fixture\n"
        "async def live(request):\n" + fixture_body + "async def test_uses_live(live):\n"
        "    pass\n"
    )


_CANONICAL_RELAY_FIXTURE_BODY = (
    "    config = RelayConfig.load()\n"
    "    artifacts = artifact_directory(config, request.node.name)\n"
    "    fixture = LiveRelayFixture(config, artifacts)\n"
    "    try:\n"
    "        await fixture.open()\n"
    "        yield fixture\n"
    "    finally:\n"
    "        await fixture.close()\n"
)


def test_explicit_fixture_category_accepts_canonical_relay_lifecycle(tmp_path: Path) -> None:
    candidate = tmp_path / "test_canonical_relay_fixture.py"
    candidate.write_text(_relay_suite_source(_CANONICAL_RELAY_FIXTURE_BODY), encoding="utf-8")
    assert _suite_loads_explicit_fixture(candidate, _relay_contract())


@pytest.mark.parametrize(
    ("name", "source"),
    [
        (
            "unreachable_load_unused_result",
            _relay_suite_source("    if False:\n        RelayConfig.load()\n    return object()\n"),
        ),
        (
            "nested_unused_helper",
            _relay_suite_source(
                "    def unused():\n        return RelayConfig.load()\n    return object()\n"
            ),
        ),
        (
            "unused_loader_result",
            _relay_suite_source("    RelayConfig.load()\n    return object()\n"),
        ),
        (
            "unused_integration_marker",
            _relay_suite_source(_CANONICAL_RELAY_FIXTURE_BODY, marker="unused"),
        ),
        (
            "fixture_local_fallback",
            _relay_suite_source(
                _CANONICAL_RELAY_FIXTURE_BODY.replace(
                    "RelayConfig.load()", "RelayConfig.load() if request else object()", 1
                )
            ),
        ),
        (
            "fixture_local_reassignment",
            _relay_suite_source(
                _CANONICAL_RELAY_FIXTURE_BODY.replace(
                    "    artifacts =", "    config = object()\n    artifacts =", 1
                )
            ),
        ),
    ],
)
def test_explicit_fixture_category_rejects_noncanonical_lifecycle(
    tmp_path: Path, name: str, source: str
) -> None:
    candidate = tmp_path / f"test_{name}.py"
    candidate.write_text(source, encoding="utf-8")
    assert not _suite_loads_explicit_fixture(candidate, _relay_contract())


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        (
            "unused_canonical_fixture",
            lambda source: (
                source.replace("async def live(request):", "async def unused_live(request):", 1)
                + "\n@pytest.fixture\nasync def live():\n    yield object()\n"
            ),
        ),
        (
            "overridden_canonical_fixture",
            lambda source: source + "\n@pytest.fixture\nasync def live():\n    yield object()\n",
        ),
        (
            "rebound_loader",
            lambda source: source.replace(
                "@pytest.fixture\nasync def live(request):",
                "class RelayConfig:\n"
                "    @classmethod\n"
                "    def load(cls):\n"
                "        return object()\n\n"
                "@pytest.fixture\n"
                "async def live(request):",
                1,
            ),
        ),
        (
            "rebound_constructor",
            lambda source: source.replace(
                "@pytest.fixture\nasync def live(request):",
                "class LiveRelayFixture:\n"
                "    def __init__(self, config, artifacts):\n"
                "        pass\n\n"
                "@pytest.fixture\n"
                "async def live(request):",
                1,
            ),
        ),
        (
            "rebound_pytest",
            lambda source: source.replace("pytestmark =", "pytest = object()\npytestmark =", 1),
        ),
        (
            "reassigned_requested_fixture",
            lambda source: source.replace(
                "    command = await live.command()",
                "    live = object()\n    command = await live.command()",
                1,
            ),
        ),
        (
            "tuple_rebound_loader",
            lambda source: source.replace(
                "@pytest.fixture\nasync def live(request):",
                "RelayConfig, unused = (object, None)\n\n@pytest.fixture\nasync def live(request):",
                1,
            ),
        ),
        (
            "globals_subscript_rebound_loader",
            lambda source: source.replace(
                "@pytest.fixture\nasync def live(request):",
                'globals()["RelayConfig"] = object\n\n@pytest.fixture\nasync def live(request):',
                1,
            ),
        ),
        (
            "aliased_constructor_import",
            lambda source: source.replace(
                "@pytest.fixture\nasync def live(request):",
                "from builtins import object as LiveRelayFixture\n\n"
                "@pytest.fixture\n"
                "async def live(request):",
                1,
            ),
        ),
    ],
)
def test_actual_relay_suite_rejects_fixture_resolution_bypasses(
    tmp_path: Path, name: str, mutate: Callable[[str], str]
) -> None:
    source = _actual_relay_source()
    candidate = tmp_path / f"test_{name}.py"
    candidate.write_text(mutate(source), encoding="utf-8")
    assert not _suite_loads_explicit_fixture(candidate, _relay_contract())


@pytest.mark.parametrize(
    "decorator",
    [
        '@pytest.mark.parametrize("live", [object()])',
        '@pytest.mark.parametrize("live", [object()], indirect=True)',
        '@pytest.mark.parametrize(("live", "fault"), [(object(), "x")])',
        '@pytest.mark.parametrize(["live", "fault"], [(object(), "x")])',
    ],
)
def test_actual_relay_suite_rejects_direct_live_parametrization(tmp_path: Path, decorator: str) -> None:
    source = _actual_relay_source()
    first_test = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_")
    )
    needle = f"async def {first_test.name}("
    candidate = tmp_path / "test_direct_live_parameter.py"
    candidate.write_text(source.replace(needle, decorator + "\n" + needle, 1), encoding="utf-8")
    assert not _suite_loads_explicit_fixture(candidate, _relay_contract())


@pytest.mark.parametrize("container", ["list", "tuple"])
def test_actual_relay_suite_rejects_module_live_parametrization(tmp_path: Path, container: str) -> None:
    source = _actual_relay_source()
    opening, closing = ("[", "]") if container == "list" else ("(", ")")
    replacement = (
        f"pytestmark = {opening}{_actual_pytestmark_elements(source)}, "
        f'pytest.mark.parametrize("live", [object()]){closing}'
    )
    candidate = tmp_path / "test_module_live_parameter.py"
    candidate.write_text(_mutate_pytestmark(source, replacement), encoding="utf-8")
    assert not _suite_loads_explicit_fixture(candidate, _relay_contract())


def test_actual_relay_suite_keeps_other_name_parametrization(tmp_path: Path) -> None:
    source = _relay_suite_source(_CANONICAL_RELAY_FIXTURE_BODY).replace(
        "async def test_uses_live(live):\n    pass",
        '@pytest.mark.parametrize("fault", ["x"])\nasync def test_uses_live(live, fault):\n    assert fault',
        1,
    )
    candidate = tmp_path / "test_other_parameter.py"
    candidate.write_text(source, encoding="utf-8")
    assert _suite_loads_explicit_fixture(candidate, _relay_contract())


@pytest.mark.parametrize("boundary", ["imported_mark", "fixture_wrapper", "test_wrapper", "module_wrapper"])
def test_actual_relay_suite_rejects_unknown_fixture_selection_decorators(
    tmp_path: Path, boundary: str
) -> None:
    source = _actual_relay_source()
    first_test = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_")
    )
    mutations = {
        "imported_mark": lambda value: value.replace(
            "import pytest", "import pytest\nfrom pytest import mark", 1
        ).replace(
            f"async def {first_test.name}(",
            f'@mark.parametrize("live", [object()])\nasync def {first_test.name}(',
            1,
        ),
        "fixture_wrapper": lambda value: value.replace(
            "@pytest.fixture\nasync def live(request):",
            "def wrapper(function):\n    return function\n\n"
            "@wrapper\n@pytest.fixture\nasync def live(request):",
            1,
        ),
        "test_wrapper": lambda value: value.replace(
            f"async def {first_test.name}(",
            f"def wrapper(function):\n    return function\n\n@wrapper\nasync def {first_test.name}(",
            1,
        ),
        "module_wrapper": lambda value: _mutate_pytestmark(
            value,
            "def wrapper(function):\n    return function\n\n"
            f"pytestmark = [{_actual_pytestmark_elements(value)}, wrapper]",
        ),
    }
    mutated = mutations[boundary](source)
    assert mutated != source, f"mutation {boundary!r} did not apply — the test would be vacuous"
    candidate = tmp_path / f"test_{boundary}.py"
    candidate.write_text(mutated, encoding="utf-8")
    assert not _suite_loads_explicit_fixture(candidate, _relay_contract())


def test_canonical_module_parametrization_of_other_name_is_supported(tmp_path: Path) -> None:
    source = (
        _relay_suite_source(_CANONICAL_RELAY_FIXTURE_BODY)
        .replace(
            "pytestmark = [pytest.mark.integration, pytest.mark.asyncio]",
            "pytestmark = [pytest.mark.integration, pytest.mark.asyncio, "
            'pytest.mark.parametrize("fault", ["x"])]',
            1,
        )
        .replace(
            "async def test_uses_live(live):\n    pass",
            "async def test_uses_live(live, fault):\n    assert fault",
            1,
        )
    )
    candidate = tmp_path / "test_module_other_parameter.py"
    candidate.write_text(source, encoding="utf-8")
    assert _suite_loads_explicit_fixture(candidate, _relay_contract())


def test_the_expected_coordinates_come_from_compose_not_from_this_file() -> None:
    """Non-vacuity for the comparison itself: the expected DSN/bootstrap this fence checks against
    must be READ from `docker-compose.yml`, so changing the compose port changes what is enforced.
    Proven by pointing the parser at a mutated copy of the real file."""
    user, password, database, port = _compose_pg_coordinates()
    assert (user, password, database, port) == ("maezo", "maezo", "maezo", "5433")
    assert _compose_kafka_external_port() == "9092"

    mutated = _COMPOSE.read_text(encoding="utf-8").replace(
        "${MAEZO_PG_HOST_PORT:-5433}:5432", "${MAEZO_PG_HOST_PORT:-5544}:5432", 1
    )
    with mock.patch.object(Path, "read_text", autospec=True) as read_text:
        read_text.return_value = mutated
        assert _compose_pg_coordinates()[3] == "5544", (
            "the expected port is hardcoded in this file rather than read from docker-compose.yml"
        )


def test_module_import_does_not_open_a_connection() -> None:
    """The resolvers this fence calls must be pure: importing a live suite (which this fence does,
    and which `pytest --collect-only` does for the whole repo) must never touch a server. Pinned by
    importing every live suite with an environment whose DSNs point at a port that instantly
    refuses — if any module connected at import time, this would raise rather than pass."""
    refusing = "postgresql://maezo:maezo@127.0.0.1:1/maezo"
    env = {
        "MAEZO_TEST_DATABASE_URL": refusing,
        "MAEZO_TEST_A2A_EDGE_DATABASE_URL": refusing,
        "MAEZO_TEST_AMH_INBOX_DATABASE_URL": refusing,
        "MAEZO_TEST_AUDIT_ANCHOR_DRILL_DATABASE_URL": refusing,
        "MAEZO_TEST_CHECKPOINT_DATABASE_URL": refusing,
        "KAFKA_BOOTSTRAP_SERVERS": "127.0.0.1:1",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        for path in _iter_live_suites():
            _import(path)


def _relay_contract() -> ExplicitFixtureContract:
    return _EXPLICIT_FIXTURES["tests/integration/gateway/test_human_relay_live_cib.py"]


def _write_private(path: Path, value: object) -> None:
    text = value if isinstance(value, str) else json.dumps(value)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _write_valid_relay_fixture(directory: Path) -> dict[str, dict[str, Any]]:
    directory.mkdir()
    directory.chmod(0o700)
    source_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, text=True).strip()
    tenant = "relay_" + "a" * 24
    password_file = directory / "postgres-password"
    data: dict[str, Any] = {
        "schema": "human-relay-fixture.v1",
        "synthetic_opt_in": True,
        "tenant": tenant,
        "source_sha": source_sha,
        "rest_url": "http://127.0.0.1:18080/engine-rest",
        "human_url": "https://127.0.0.1:18443",
        "database": {
            "host": "127.0.0.1",
            "port": 15433,
            "user": "maezo",
            "database": "maezo",
            "password_file": str(password_file),
        },
    }
    trust: dict[str, Any] = {"enable_synthetic_fixture": True, "tenant": tenant}
    public: dict[str, Any] = {"relay_synthetic": True, "source_sha": source_sha}
    _write_private(directory / "relay-fixture.json", data)
    _write_private(directory / "trust.json", trust)
    _write_private(directory / "public-receipt.json", public)
    _write_private(password_file, "test-password")
    return {"data": data, "trust": trust, "public": public}


def test_explicit_relay_fixture_has_no_missing_configuration_fallback() -> None:
    contract = _relay_contract()
    loader = _explicit_fixture_loader(contract)
    with (
        mock.patch.dict(os.environ, {}, clear=True),
        pytest.raises(AssertionError, match="no skip/default"),
    ):
        loader.load()


def test_explicit_relay_fixture_binds_private_manifest_source_and_coordinates(tmp_path: Path) -> None:
    contract = _relay_contract()
    directory = tmp_path / "relay-private"
    values = _write_valid_relay_fixture(directory)
    with mock.patch.dict(os.environ, {contract.environment_variable: str(directory)}, clear=True):
        config = _explicit_fixture_loader(contract).load()
    assert config.directory == directory
    assert config.data["schema"] == contract.schema
    assert config.data["source_sha"] == values["public"]["source_sha"]
    assert config.data["database"]["host"] == contract.database_host
    assert config.data["database"]["port"] == contract.database_port


@pytest.mark.parametrize(
    "filename",
    ["directory", "relay-fixture.json", "trust.json", "public-receipt.json", "postgres-password"],
)
def test_explicit_relay_fixture_refuses_nonprivate_inputs(tmp_path: Path, filename: str) -> None:
    contract = _relay_contract()
    directory = tmp_path / "relay-private"
    _write_valid_relay_fixture(directory)
    (directory if filename == "directory" else directory / filename).chmod(
        0o755 if filename == "directory" else 0o644
    )
    with (
        mock.patch.dict(os.environ, {contract.environment_variable: str(directory)}, clear=True),
        pytest.raises(AssertionError),
    ):
        _explicit_fixture_loader(contract).load()


@pytest.mark.parametrize("malformation", ["missing_manifest", "invalid_json"])
def test_explicit_relay_fixture_refuses_malformed_configuration(tmp_path: Path, malformation: str) -> None:
    contract = _relay_contract()
    directory = tmp_path / "relay-private"
    _write_valid_relay_fixture(directory)
    manifest = directory / contract.manifest_name
    if malformation == "missing_manifest":
        manifest.unlink()
    else:
        _write_private(manifest, "{")
    expected = FileNotFoundError if malformation == "missing_manifest" else json.JSONDecodeError
    with (
        mock.patch.dict(os.environ, {contract.environment_variable: str(directory)}, clear=True),
        pytest.raises(expected),
    ):
        _explicit_fixture_loader(contract).load()


@pytest.mark.parametrize(
    "mutation",
    ["source", "public_source", "database_host", "database_port", "rest_url", "human_url"],
)
def test_explicit_relay_fixture_refuses_source_or_coordinate_mismatch(tmp_path: Path, mutation: str) -> None:
    contract = _relay_contract()
    directory = tmp_path / "relay-private"
    values = _write_valid_relay_fixture(directory)
    data, public = values["data"], values["public"]
    if mutation == "source":
        data["source_sha"] = "0" * 40
    elif mutation == "public_source":
        public["source_sha"] = "0" * 40
    elif mutation == "database_host":
        data["database"]["host"] = "database.example.invalid"
    elif mutation == "database_port":
        data["database"]["port"] = 5433
    elif mutation == "rest_url":
        data["rest_url"] = "http://cib.example.invalid:18080/engine-rest"
    else:
        data["human_url"] = "http://127.0.0.1:18443"
    _write_private(directory / "relay-fixture.json", data)
    _write_private(directory / "public-receipt.json", public)
    with (
        mock.patch.dict(os.environ, {contract.environment_variable: str(directory)}, clear=True),
        pytest.raises(AssertionError),
    ):
        _explicit_fixture_loader(contract).load()


@pytest.mark.parametrize("relative_path", sorted(_NAME_ONLY))
def test_name_only_exclusions_carry_a_reason(relative_path: str) -> None:
    """Each exclusion is a claim, not a silence: it must state why the module needs no served
    infrastructure, and the module must still exist."""
    reason = _NAME_ONLY[relative_path]
    assert len(reason) > 40, f"{relative_path}'s exclusion reason is too thin to audit: {reason!r}"
    assert (_REPO_ROOT / relative_path).is_file()
