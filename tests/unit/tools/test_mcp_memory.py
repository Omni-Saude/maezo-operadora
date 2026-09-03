"""Unit tests for maezo.tools.mcp_memory (ADR-0002, ADR-0022).

GAP-DU-01-a (2026-09): `store_episodic` and `recall_semantic` used to fabricate success —
inserting into / selecting from tables (`episodic_events`, `semantic_memory`) that no Alembic
migration creates, and `recall_semantic` additionally hard-coded `similarity: 1.0` on every
row regardless of query. `test_memory_store_episodic` and `test_memory_recall_semantic` below
used to assert exactly that fabricated behaviour (mocking the pool and asserting a fake
success return); they have been REWRITTEN (not merely renamed) to assert the fail-closed
refusal instead — see `test_store_episodic_refuses_fail_closed` and
`test_recall_semantic_refuses_fail_closed`. `test_memory_tools_registered`,
`test_memory_settings_defaults`, and `test_memory_store_episodic_validation` asserted true
facts (tool listing shape, settings shape, input validation) and are unchanged.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Memory Server: tool listing / settings (unchanged behaviour)
# ---------------------------------------------------------------------------


def test_memory_tools_registered() -> None:
    """Memory server should expose exactly 2 tools: store_episodic, recall_semantic.

    The GAP-DU-01-a fix changes what these tools DO (refuse instead of fabricate), not the
    catalogue/fence-visible surface — this proves the surface is unchanged.
    """
    from maezo.tools.mcp_memory import MemoryServer

    server = MemoryServer()

    tools = server.list_tools()

    assert len(tools) == 2, f"Expected 2 tools, got {len(tools)}: {tools}"
    tool_names = {t["name"] for t in tools}
    assert tool_names == {"store_episodic", "recall_semantic"}


def test_memory_settings_defaults() -> None:
    """MemorySettings should default to localhost:5432 for PostgreSQL/pgvector.

    NOTE: these settings are currently informational only — neither tool opens a connection
    (both refuse before needing one). This test asserts the shape, not that anything connects.
    """
    from maezo.tools.mcp_memory.server import MemorySettings

    settings = MemorySettings()

    assert settings.database_url is not None
    assert "postgresql" in settings.database_url
    assert settings.memory_embedding_dim == 1536


# ---------------------------------------------------------------------------
# store_episodic: input validation still real, then unconditional fail-closed refusal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_store_episodic_validation() -> None:
    """store_episodic should raise ValueError for empty agent_id (checked before the refusal)."""
    from maezo.tools.mcp_memory.server import MemoryServer

    server = MemoryServer()

    with pytest.raises(ValueError, match="agent_id"):
        await server.store_episodic("", {"type": "test"})


@pytest.mark.asyncio
async def test_store_episodic_refuses_fail_closed() -> None:
    """store_episodic(agent_id, event) must NEVER fabricate a stored event.

    Rewritten from `test_memory_store_episodic`, which used to mock an asyncpg pool and
    assert a successful insert into `episodic_events` — a table no migration creates. It now
    asserts the documented, typed refusal instead: no pool is touched (there is none left to
    mock), and the error carries `reason == REASON_EPISODIC_SCHEMA_DRIFT`.
    """
    from maezo.tools.mcp_memory.server import (
        REASON_EPISODIC_SCHEMA_DRIFT,
        EpisodicMemoryUnavailableError,
        MemoryServer,
    )

    server = MemoryServer()

    with pytest.raises(EpisodicMemoryUnavailableError) as exc_info:
        await server.store_episodic(
            agent_id="agent-auth-1",
            event={
                "type": "decision",
                "action": "auto_approval",
                "payload": {"process_id": "proc-123"},
            },
        )

    err = exc_info.value
    assert err.reason == REASON_EPISODIC_SCHEMA_DRIFT
    assert "agent_memory" in err.detail
    assert "tenant_id" in err.detail


@pytest.mark.asyncio
async def test_store_episodic_refuses_for_every_valid_event_shape() -> None:
    """The refusal is unconditional — no event payload shape makes store_episodic succeed."""
    from maezo.tools.mcp_memory.server import EpisodicMemoryUnavailableError, MemoryServer

    server = MemoryServer()

    for event in ({}, {"type": "x"}, {"type": "decision", "payload": {"a": 1}}):
        with pytest.raises(EpisodicMemoryUnavailableError):
            await server.store_episodic(agent_id="agent-1", event=event)


# ---------------------------------------------------------------------------
# recall_semantic: unconditional fail-closed refusal, structurally proven never to return
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_semantic_refuses_fail_closed() -> None:
    """recall_semantic(query) must NEVER fabricate a similarity score.

    Rewritten from `test_memory_recall_semantic`, which used to mock an asyncpg pool and
    assert a returned `similarity: 0.95` — a constant the donor code hard-coded regardless of
    query, against a `semantic_memory` table no migration creates. It now asserts the
    documented, typed refusal: no pool is touched (there is none left to mock), and the error
    carries `reason == REASON_SEMANTIC_SEARCH_NOT_WIRED`.
    """
    from maezo.tools.mcp_memory.server import (
        REASON_SEMANTIC_SEARCH_NOT_WIRED,
        MemoryServer,
        SemanticMemoryUnavailableError,
    )

    server = MemoryServer()

    with pytest.raises(SemanticMemoryUnavailableError) as exc_info:
        await server.recall_semantic("autorizacao procedimento similar")

    err = exc_info.value
    assert err.reason == REASON_SEMANTIC_SEARCH_NOT_WIRED
    assert "embedding" in err.detail


@pytest.mark.asyncio
async def test_recall_semantic_refuses_regardless_of_query_or_limit() -> None:
    """The refusal is unconditional — no query string or limit makes recall_semantic succeed."""
    from maezo.tools.mcp_memory.server import MemoryServer, SemanticMemoryUnavailableError

    server = MemoryServer()

    for query, limit in (("", 1), ("autorizacao", 10), ("x" * 500, 100)):
        with pytest.raises(SemanticMemoryUnavailableError):
            await server.recall_semantic(query, limit=limit)


def test_recall_semantic_has_no_return_statement() -> None:
    """Structural proof: `recall_semantic`'s body contains no `return` statement at all —
    AST-verified, so no code path can EVER produce a `similarity` value, real or fabricated,
    now or after an unnoticed future edit re-adds a partial success path.
    """
    from maezo.tools.mcp_memory.server import MemoryServer

    source = textwrap.dedent(inspect.getsource(MemoryServer.recall_semantic))
    tree = ast.parse(source)
    func_def = tree.body[0]
    assert isinstance(func_def, ast.AsyncFunctionDef), func_def
    returns = [node for node in ast.walk(func_def) if isinstance(node, ast.Return)]
    assert not returns, (
        f"recall_semantic must never `return` — found {len(returns)} return statement(s), "
        "meaning a code path could produce a result (fabricated or otherwise)"
    )


def test_store_episodic_has_no_success_return_statement() -> None:
    """Structural proof: `store_episodic`'s only `return` is unreachable dead code — the only
    statement that terminates normally is the `raise` (mirrors the recall_semantic proof).
    A stray success-shaped return would be a regression back toward fabricated behaviour.
    """
    from maezo.tools.mcp_memory.server import MemoryServer

    source = textwrap.dedent(inspect.getsource(MemoryServer.store_episodic))
    tree = ast.parse(source)
    func_def = tree.body[0]
    assert isinstance(func_def, ast.AsyncFunctionDef), func_def
    returns = [node for node in ast.walk(func_def) if isinstance(node, ast.Return)]
    assert not returns, (
        f"store_episodic must never `return` a stored-event dict — found {len(returns)} return statement(s)"
    )


# ---------------------------------------------------------------------------
# Repo-wide structural proof: the fabricated table names live in exactly one place
# ---------------------------------------------------------------------------


def test_fabricated_table_names_referenced_only_inside_mcp_memory_module() -> None:
    """`episodic_events` / `semantic_memory` were never-migrated table names this module used
    to query (GAP-DU-01-a). They may still appear as historical citations inside
    `mcp_memory/server.py`'s own refusal docstrings/detail strings, but nowhere else in
    `src/maezo` — a real writer/reader targeting either name anywhere else would be exactly
    the class of defect this fix closes.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    src_root = repo_root / "src" / "maezo"
    assert src_root.is_dir(), f"expected src root at {src_root}"

    allowed = {src_root / "tools" / "mcp_memory" / "server.py"}
    offenders: list[str] = []
    for path in src_root.rglob("*.py"):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if "episodic_events" in text or "semantic_memory" in text:
            offenders.append(str(path.relative_to(repo_root)))

    assert not offenders, (
        "fabricated/never-migrated table names referenced outside "
        f"src/maezo/tools/mcp_memory/server.py: {offenders}"
    )


def test_memory_server_has_no_connection_pool() -> None:
    """The bare, hygiene-defective asyncpg pool (no search_path per DL-0017, wrong default DB
    name) has been removed rather than patched dead code — both tools refuse before ever
    needing a connection. This pins that removal so it cannot silently regress back to an
    unreachable-but-present pool.
    """
    from maezo.tools.mcp_memory.server import MemoryServer

    server = MemoryServer()
    assert not hasattr(server, "_get_pool")
    assert not hasattr(server, "_pool")
