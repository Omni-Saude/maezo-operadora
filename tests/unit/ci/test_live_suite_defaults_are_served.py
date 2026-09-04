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

import importlib
import os
import re
from collections.abc import Callable
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

#: Modules whose filename matches the live-suite glob but which need no infrastructure at all.
#: Each entry is a claim this file's own tests re-check (they must define NO resolver).
_NAME_ONLY: Final[dict[str, str]] = {
    "tests/unit/gateway/seams/test_live_dispatch_wiring.py": (
        "'live' names the live AGENT PATH (design §5.5), not live infrastructure: pure "
        "introspection over the composition root, `pytestmark = pytest.mark.anyio`, no DSN and no "
        "broker."
    ),
    "tests/unit/runtime/test_inference_live.py": (
        "live Anthropic API call (T1.7), `pytestmark = pytest.mark.llm_live`; its coordinate is an "
        "API KEY, not a repo-served port, and it skips loudly via `skipif` when no key is set — "
        "by design, and outside this fence's subject."
    ),
}


def _iter_live_suites() -> list[Path]:
    """Every `tests/**/test_*_live*.py` module, sorted.

    The `_live` prefix in the glob matters: it excludes files that merely contain the letters
    (`..._rede-live-ry...`, `..._de-live-ry...`) — `test_t33_a1_cancel_handoff_redelivery_
    idempotency.py` and `test_duplicate_fact_delivery_attack.py` are NOT live suites and must not
    be dragged in by a lazier pattern.
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


# ---------------------------------------------------------------------------
# What the repo actually serves, read from docker-compose.yml (never hardcoded)
# ---------------------------------------------------------------------------


def _compose() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    return data


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
    return env["POSTGRES_USER"], env["POSTGRES_PASSWORD"], env["POSTGRES_DB"], match.group(1)


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
    DSN, or a Kafka bootstrap, or appears in `_NAME_ONLY` with a written reason — and a module in
    `_NAME_ONLY` must genuinely define no resolver, so the exclusion cannot rot into a loophole."""
    unclassified: list[str] = []
    for path in _iter_live_suites():
        rel = str(path.relative_to(_REPO_ROOT))
        module = _import(path)
        has_pg = _resolver(module, _PG_RESOLVER_NAMES) is not None
        has_kafka = _resolver(module, (_KAFKA_RESOLVER_NAME,)) is not None
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
        f"is unchecked — name it one of {_PG_RESOLVER_NAMES} / {_KAFKA_RESOLVER_NAME}, or add it "
        "to _NAME_ONLY with a reason:\n  " + "\n  ".join(unclassified)
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
    """A fence that scanned zero suites would be worthless. Floors sit just below today's real
    counts (13 live suites: 8 resolving Postgres, 2 resolving Kafka, 2 name-only) so a future
    refactor that silently narrows the glob fails here instead of going quietly green."""
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


@pytest.mark.parametrize("relative_path", sorted(_NAME_ONLY))
def test_name_only_exclusions_carry_a_reason(relative_path: str) -> None:
    """Each exclusion is a claim, not a silence: it must state why the module needs no served
    infrastructure, and the module must still exist."""
    reason = _NAME_ONLY[relative_path]
    assert len(reason) > 40, f"{relative_path}'s exclusion reason is too thin to audit: {reason!r}"
    assert (_REPO_ROOT / relative_path).is_file()
