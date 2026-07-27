"""LIVE-Postgres proof: the Helena WEBHOOK DISPATCH PATH persists + resumes durable conversation
state across invocations and receiver restarts (T4b — closing the T3.4/F4 boundary #165 left in
`dispatch.py`).

Tier 2 (`@pytest.mark.integration`), placed HERE (not under `tests/integration/`) for the SAME
reason as `tests/unit/runtime/agent_runtime/test_checkpoint_live_pg.py`: it needs a REAL Postgres
but explicitly NOT the CIB Seven engine. The LLM + DMN + engine are Fakes (deterministic, no
network); the CHECKPOINTER is a REAL `AsyncPostgresSaver` — so this proves the dispatch path's
durable-persistence wiring, not Helena's business logic (that is covered by the mock-only suite).

Proves what the in-memory unit suite cannot:
  1. Two sequential `HelenaDispatcher.dispatch(...)` calls with the SAME conversation identity
     accumulate checkpoint history on ONE thread in real Postgres (turn 2 resumes turn 1).
  2. RESTART-RESUME: after `aclose()` (simulated receiver death) a FRESH checkpointer on the same
     DB still recovers the persisted turn-2 state.
  3. A DIFFERENT identity gets its own independent thread.
  4. PHI (LGPD): every `thread_id` in the checkpoint tables is a hashed `wa:{tenant}:{phone_hash}`
     id — never a raw phone number (SELECT proof).

Skips cleanly (never errors) when no Postgres is reachable.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage
from maezo.runtime.checkpoint import Checkpointer, checkpoint_thread_config
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

pytestmark = pytest.mark.integration

# A FREE, dedicated Postgres (deliberately not the compose stack's 5432/5433). Override with
# MAEZO_TEST_CHECKPOINT_DATABASE_URL. The T4b live proof used port 5663.
_DEFAULT_DSN = "postgresql://ckpt:ckpt@localhost:5663/ckpt"

_PHONE_A = "5511999990001"
_PHONE_B = "5511999990002"


def _dsn() -> str:
    return os.environ.get("MAEZO_TEST_CHECKPOINT_DATABASE_URL", _DEFAULT_DSN)


async def _postgres_reachable(dsn: str) -> bool:
    import asyncpg  # type: ignore[import-untyped]

    from maezo.gateway.audit_postgres import normalize_dsn

    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # noqa: BLE001 — any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


@pytest.fixture(scope="module")
def pg_dsn() -> str:
    dsn = _dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"COULD NOT VERIFY: Postgres not reachable at {dsn!r} (override with "
            "MAEZO_TEST_CHECKPOINT_DATABASE_URL). The dispatch path provisions the checkpoint "
            "tables via the saver's own awaited setup() — no migrations needed."
        )
    return dsn


class _FakeInference:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        return self._responses.pop(0) if self._responses else ""


class _FakeWhatsAppClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str) -> dict[str, Any]:
        self.sent.append((to, text))
        return {"messages": [{"id": "wamid.reply"}]}


def _dispatcher(checkpointer: Checkpointer, *, turns: int) -> HelenaDispatcher:
    responses: list[str] = []
    for _ in range(turns):
        responses.append('{"intent": "information", "population": "none", "psychosocial_risk": false}')
        responses.append("resposta")
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", [{"red_flag": False, "conduta": "CONTINUE"}] * turns)
    return HelenaDispatcher(
        tenant_id="amh",
        inference=_FakeInference(responses),  # type: ignore[arg-type]
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=_FakeWhatsAppClient(),  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
        audit_sink=FakeStartAuditSink(),
        checkpointer=checkpointer,
    )


async def test_dispatch_multi_turn_persist_resume_restart_and_phi(pg_dsn: str) -> None:
    ck = await Checkpointer.connect_and_setup(pg_dsn)
    conv_a: str | None = None
    conv_b: str | None = None
    try:
        dispatcher = _dispatcher(ck, turns=6)

        # TURN 1 (identity A) — persists a checkpoint on a hashed thread.
        r1 = await dispatcher.dispatch(InboundMessage(from_number=_PHONE_A, text="oi", message_id="m1"))
        conv_a = r1["conversation_id"]
        cfg_a = checkpoint_thread_config(conv_a)
        assert conv_a.startswith("wa:amh:")
        assert _PHONE_A not in conv_a  # hashed, never raw
        assert await ck.saver.aget_tuple(cfg_a) is not None  # type: ignore[union-attr]
        hist1 = [c async for c in ck.saver.alist(cfg_a)]  # type: ignore[union-attr]

        # TURN 2 (identity A) — SAME thread, resumes; checkpoint history grows.
        r2 = await dispatcher.dispatch(InboundMessage(from_number=_PHONE_A, text="de novo", message_id="m2"))
        assert r2["conversation_id"] == conv_a
        hist2 = [c async for c in ck.saver.alist(cfg_a)]  # type: ignore[union-attr]
        assert len(hist2) > len(hist1), "second turn did not accumulate durable checkpoint history"

        # TURN 3 (identity B) — independent, fresh thread.
        r3 = await dispatcher.dispatch(InboundMessage(from_number=_PHONE_B, text="oi", message_id="m3"))
        conv_b = r3["conversation_id"]
        assert conv_b != conv_a
        hist_b = [c async for c in ck.saver.alist(checkpoint_thread_config(conv_b))]  # type: ignore[union-attr]
        assert len(hist_b) < len(hist2)  # fresh identity, not A's 2-turn history

        # PHI (LGPD) SELECT proof: no raw phone number appears in any thread_id column.
        await _assert_no_raw_phone_in_checkpoint_tables(pg_dsn)
    finally:
        await ck.aclose()  # simulate receiver death — pool torn down

    # RESTART-RESUME: a FRESH checkpointer on the SAME DB recovers turn-2 state.
    ck2 = await Checkpointer.connect_and_setup(pg_dsn)
    try:
        assert conv_a is not None
        cfg_a = checkpoint_thread_config(conv_a)
        recovered = await ck2.saver.aget_tuple(cfg_a)  # type: ignore[union-attr]
        assert recovered is not None, "conversation state did not survive receiver restart"
        assert recovered.config["configurable"]["thread_id"] == conv_a
    finally:
        # Clean up both threads so re-runs stay isolated.
        for conv in (conv_a, conv_b):
            if conv is not None:
                await ck2.saver.adelete_thread(conv)  # type: ignore[union-attr]
        await ck2.aclose()


async def _assert_no_raw_phone_in_checkpoint_tables(dsn: str) -> None:
    import asyncpg  # type: ignore[import-untyped]

    from maezo.gateway.audit_postgres import normalize_dsn

    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        rows = await conn.fetch("SELECT DISTINCT thread_id FROM checkpoints")
    finally:
        await conn.close()
    thread_ids = [r["thread_id"] for r in rows]
    assert thread_ids, "expected at least one checkpoint thread"
    for tid in thread_ids:
        assert tid.startswith("wa:amh:"), f"non-convention thread_id leaked: {tid!r}"
        assert _PHONE_A not in tid and _PHONE_B not in tid, f"RAW PHONE in checkpoint thread_id: {tid!r}"
