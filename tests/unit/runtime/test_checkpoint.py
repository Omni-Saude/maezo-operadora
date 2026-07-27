"""Unit tests for maezo.runtime.checkpoint (DL-0017; T3.4/F4 prod wiring)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from maezo.runtime.checkpoint import (
    Checkpointer,
    assert_phi_safe_thread_id,
    checkpoint_thread_config,
)


@pytest.mark.asyncio
async def test_checkpoint_save_calls_aput() -> None:
    """Checkpointer.save should delegate to the underlying PostgresSaver.aput."""
    mock_saver = MagicMock()
    mock_saver.aput = AsyncMock(return_value={"configurable": {"thread_id": "t1"}})

    checkpointer = Checkpointer(saver=mock_saver)
    config = {"configurable": {"thread_id": "t1"}}
    checkpoint_data = {"state": {"key": "value"}}
    metadata = {"source": "test"}

    result = await checkpointer.save(config, checkpoint_data, metadata)

    mock_saver.aput.assert_awaited_once()
    assert result == {"configurable": {"thread_id": "t1"}}


@pytest.mark.asyncio
async def test_checkpoint_load_calls_aget_tuple() -> None:
    """Checkpointer.load should delegate to the underlying PostgresSaver.aget_tuple."""
    mock_saver = MagicMock()
    expected = MagicMock()
    mock_saver.aget_tuple = AsyncMock(return_value=expected)

    checkpointer = Checkpointer(saver=mock_saver)
    config = {"configurable": {"thread_id": "t1"}}

    result = await checkpointer.load(config)

    mock_saver.aget_tuple.assert_awaited_once_with(config)
    assert result is expected


def test_checkpointer_accepts_conn_string() -> None:
    """Checkpointer should accept a conn_string for pool creation (DL-0017 setup=)."""
    checkpointer = Checkpointer(conn_string="postgresql://test:test@localhost/test")

    assert checkpointer.conn_string == "postgresql://test:test@localhost/test"


def test_saver_property_exposes_underlying_saver() -> None:
    """`.saver` returns the wrapped saver (what `StateGraph.compile(checkpointer=...)` needs)."""
    mock_saver = MagicMock()
    assert Checkpointer(saver=mock_saver).saver is mock_saver
    assert Checkpointer(conn_string="postgresql://x/y").saver is None


# --- PHI-safe thread-id discipline (T3.4/F4) --------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "+5511999998888",  # E.164 phone
        "5511999998888",  # bare phone
        "12345678901",  # bare CPF (11 digits)
        "  0000000000  ",  # padded, still all-digits
    ],
)
def test_assert_phi_safe_thread_id_rejects_raw_numeric(raw: str) -> None:
    """A raw phone/CPF-like id is refused — checkpoint tables are PHI-bearing, keyed by thread_id."""
    with pytest.raises(ValueError, match="raw phone/CPF-like"):
        assert_phi_safe_thread_id(raw)


def test_assert_phi_safe_thread_id_rejects_empty() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        assert_phi_safe_thread_id("   ")


#: A valid 64-char HMAC-SHA256 hex digest (all-hex, contains a-f), tagged keyed with `hk1_`.
_KEYED = "hk1_" + "deadbeef" * 8  # `hk1_` + exactly 64 hex chars


@pytest.mark.parametrize(
    "safe",
    [
        # KEYED hashed conversation id (dispatch.py convention).
        f"wa:amh:{_KEYED}",
        # process business key (helena/graph.py convention) wrapping a keyed conversation id.
        f"ESC-amh-wa:amh:{_KEYED}",
    ],
)
def test_assert_phi_safe_thread_id_accepts_keyed_conventional_ids(safe: str) -> None:
    """The platform's KEYED conversation ids / process business keys pass unchanged."""
    assert assert_phi_safe_thread_id(safe) == safe.strip()


@pytest.mark.parametrize(
    "unkeyed",
    [
        # The LEGACY reversible scheme: `wa:{tenant}:{bare-sha256}` with NO `hk1_` marker. This is
        # the exact identity the fix must now REJECT (it is indistinguishable in shape from a keyed
        # HMAC, so "not raw-numeric" alone used to let it through — the reversibility bug).
        "wa:amh:deadbeefcafe0123456789abcdef0123456789abcdef0123456789abcdef01",
        "ESC-amh-wa:amh:feedfacecafe00112233445566778899aabbccddeeff",
        # An arbitrary non-keyed id (a bare process-instance handle carries no keyed pseudonym).
        "amh:proc-instance-42",
    ],
)
def test_assert_phi_safe_thread_id_rejects_unkeyed_hash(unkeyed: str) -> None:
    """An UNKEYED (reversible) hash — even in the `wa:`/`ESC-` conventional shape — is refused: the
    guard now REQUIRES the `hk1_` keyed-pseudonym marker (ADR-0035 extension, t9-phi-conversation-id)."""
    with pytest.raises(ValueError, match="KEYED pseudonym token"):
        assert_phi_safe_thread_id(unkeyed)


def test_checkpoint_thread_config_shape() -> None:
    """`checkpoint_thread_config` builds a langgraph RunnableConfig with the validated thread id."""
    tid = f"wa:amh:{_KEYED}"
    cfg = checkpoint_thread_config(tid, checkpoint_ns="sub")
    assert cfg["configurable"]["thread_id"] == tid
    assert cfg["configurable"]["checkpoint_ns"] == "sub"


def test_checkpoint_thread_config_rejects_raw_phi() -> None:
    """The builder fail-closes on a raw-numeric id, before any config is produced."""
    with pytest.raises(ValueError, match="raw phone/CPF-like"):
        checkpoint_thread_config("+5511999998888")


def test_checkpoint_thread_config_rejects_unkeyed_hash() -> None:
    """The builder fail-closes on the legacy unkeyed `wa:{tenant}:{sha256}` id (reversibility fix)."""
    with pytest.raises(ValueError, match="KEYED pseudonym token"):
        checkpoint_thread_config("wa:amh:deadbeefcafe0123456789abcdef0123456789abcdef0123456789abcdef01")


# --- DSN normalization (prod-critical: Helm Aurora secret is `postgresql+asyncpg://`) ----------


@pytest.mark.asyncio
async def test_connect_and_setup_normalizes_sqlalchemy_asyncpg_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The platform `DATABASE_URL` convention is a SQLAlchemy `postgresql+asyncpg://...` DSN (Helm's
    Aurora ExternalSecret). psycopg — which `AsyncPostgresSaver` connects with — cannot parse the
    `+asyncpg` token, so `connect_and_setup` MUST strip it (same `normalize_dsn` as the audit sink)
    BEFORE handing it to the saver — otherwise the checkpointer ProgrammingErrors and fails closed
    on every prod boot. Proven without a real Postgres by capturing the DSN the saver receives."""
    captured: dict[str, str] = {}

    class _FakeSaver:
        async def setup(self) -> None:
            return None

    class _FakePoolCM:
        async def __aenter__(self) -> _FakeSaver:
            return _FakeSaver()

        async def __aexit__(self, *_: object) -> None:
            return None

    class _FakeAsyncPostgresSaver:
        @staticmethod
        def from_conn_string(dsn: str) -> _FakePoolCM:
            captured["dsn"] = dsn
            return _FakePoolCM()

    # `connect_and_setup` imports the saver lazily from this module — patch the attribute there.
    monkeypatch.setattr(
        "langgraph.checkpoint.postgres.aio.AsyncPostgresSaver", _FakeAsyncPostgresSaver, raising=True
    )

    ck = await Checkpointer.connect_and_setup("postgresql+asyncpg://u:p@aurora:5432/db")
    assert captured["dsn"] == "postgresql://u:p@aurora:5432/db"  # +asyncpg token stripped
    assert ck.conn_string == "postgresql://u:p@aurora:5432/db"  # stored normalized too

    # A plain `postgresql://` DSN passes through unchanged (normalization is a no-op).
    await Checkpointer.connect_and_setup("postgresql://u:p@host:5432/db")
    assert captured["dsn"] == "postgresql://u:p@host:5432/db"
