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


def _suite_loads_explicit_fixture(path: Path, contract: ExplicitFixtureContract) -> bool:
    """Prove the category names the fixture the live pytest fixture actually loads.

    Importing a loader into the module is insufficient: it must be called from a function decorated
    as a pytest fixture. This prevents an unused resolver-shaped symbol from satisfying the fence.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports_loader = any(
        isinstance(node, ast.ImportFrom)
        and node.module == contract.loader_module
        and any(alias.name == contract.loader_name and alias.asname is None for alias in node.names)
        for node in tree.body
    )
    integration_marked = any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
        and node.value.attr == "mark"
        and node.attr == "integration"
        for node in ast.walk(tree)
    )
    if not imports_loader or not integration_marked:
        return False
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_fixture = any(
            isinstance(decorator, ast.Attribute)
            and isinstance(decorator.value, ast.Name)
            and decorator.value.id == "pytest"
            and decorator.attr == "fixture"
            for decorator in node.decorator_list
        )
        calls_loader = any(
            isinstance(candidate, ast.Call)
            and isinstance(candidate.func, ast.Attribute)
            and isinstance(candidate.func.value, ast.Name)
            and candidate.func.value.id == contract.loader_name
            and candidate.func.attr == "load"
            and not candidate.args
            and not candidate.keywords
            for candidate in ast.walk(node)
        )
        if is_fixture and calls_loader:
            return True
    return False


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
