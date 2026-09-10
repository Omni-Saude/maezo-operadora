"""PLAN-W3-A2A: deterministic unit doubles; no engine or database claims."""

import pytest

from maezo.a2a.dispatcher import HandlerOutput
from maezo.a2a.facts import TOPIC_COMPLETED, TOPIC_REQUESTED

from .fakes import build_test_dispatcher, make_card
from .test_idempotency import _envelope


async def test_retry_has_one_requested_and_no_terminal():
    calls = 0

    async def handler(envelope):
        nonlocal calls
        calls += 1
        raise RuntimeError("retry")

    dispatcher, _, producer = build_test_dispatcher(cards=[make_card("rafael")], handlers={"rafael": handler})
    envelope = _envelope()
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await dispatcher.delegate(envelope)
    assert calls == 2
    assert producer.topics() == [TOPIC_REQUESTED]


async def test_requested_failure_is_retried_before_handler():
    async def handler(envelope):
        return HandlerOutput(output_ref="fhir://Task/result")

    dispatcher, _, producer = build_test_dispatcher(cards=[make_card("rafael")], handlers={"rafael": handler})
    send = producer.send
    first = True

    async def fail_once(topic, value, *, key=None):
        nonlocal first
        if first:
            first = False
            raise RuntimeError("enqueue failed")
        await send(topic, value, key=key)

    producer.send = fail_once
    envelope = _envelope()
    with pytest.raises(RuntimeError):
        await dispatcher.delegate(envelope)
    assert (await dispatcher.delegate(envelope)).success
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_COMPLETED]


async def test_outbox_child_task_cannot_use_parent_lease():
    import asyncio

    from maezo.a2a.outbox import current_outbox_connection, outbox_transaction

    conn = object()

    async def child():
        with pytest.raises(RuntimeError, match="another task"):
            current_outbox_connection()

    async with outbox_transaction(conn):
        assert current_outbox_connection() is conn
        await asyncio.create_task(child())


async def test_outbox_inherited_context_is_revoked_after_exit():
    import contextvars

    from maezo.a2a.outbox import current_outbox_connection, outbox_transaction

    async with outbox_transaction(object()):
        inherited = contextvars.copy_context()
    with pytest.raises(RuntimeError, match="inactive"):
        inherited.run(current_outbox_connection)


@pytest.mark.parametrize("bad", ["audit", "producer", "store", "tenant", "dsn"])
def test_production_rejects_non_enlisted_injection(bad):
    from maezo.a2a.dispatcher import FactProducer
    from maezo.a2a.idempotency import PostgresIdempotencyStore
    from maezo.a2a.outbox import PostgresFactOutbox, PostgresOutboxFactProducer
    from maezo.gateway.audit_postgres import PostgresAuditSink
    from maezo.runtime.agent_runtime.a2a_composition import _require_transactions_or_fail_closed

    dsn = "postgresql://localhost/unit"
    audit = PostgresAuditSink(dsn, "amh")
    store = PostgresIdempotencyStore(dsn=dsn, tenant="amh")
    producer = PostgresOutboxFactProducer(PostgresFactOutbox(dsn=dsn, tenant="amh"))
    if bad == "audit":
        audit = object()
    elif bad == "producer":
        producer = object()
    elif bad == "store":
        store = None
    elif bad == "tenant":
        audit = PostgresAuditSink(dsn, "other")
    else:
        audit = PostgresAuditSink("postgresql://localhost/other", "amh")
    with pytest.raises((ValueError, RuntimeError)):
        _require_transactions_or_fail_closed(
            runtime_mode="production",
            tenant="amh",
            audit=audit,
            facts=FactProducer(producer),
            idempotency=store,
        )


async def test_broker_exception_text_never_enters_report_or_log(capsys):
    from maezo.a2a.outbox_relay import drain_once

    from .test_outbox_relay import _seeded

    class PoisonedPublisher:
        async def send(self, *args, **kwargs):
            raise ConnectionError("SYNTHETIC-PHI-98765432100-PRIVATE-NARRATIVE")

    outbox = _seeded(1)
    report = await drain_once(outbox, PoisonedPublisher(), claimed_by="w")
    captured = capsys.readouterr()
    assert report.publish_error == "broker_publish_failed"
    assert outbox.rows[0].last_error == "broker_publish_failed"
    assert "SYNTHETIC-PHI" not in captured.out + captured.err
    assert "PRIVATE-NARRATIVE" not in captured.out + captured.err
