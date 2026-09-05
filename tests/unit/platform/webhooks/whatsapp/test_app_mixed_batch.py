"""The MIXED batch — gap `WHATSAPP-MIXED-BATCH-RETRY-TRADEOFF`, owner decision R-100 (2026-09-04).

A batch where something succeeded and something failed used to be indistinguishable, in the only
signal operators have, from a batch where everything worked: a lost non-text acknowledgement
disappeared behind a clean `ok`. R-100 approves a label of its own AND writes the criterion for
flipping the HTTP code, so the `200` does not become permanent by inertia:

> "Manter o `200` com o rotulo `status=\"partial_failure\"` e escrever no mesmo PR o criterio de
> virada automatica: quando a guarda de dedup por `wamid` estiver ativa no caminho de entrada, o
> lote misto passa a devolver `500`, sem nova decisao do dono."

Both sides of that criterion are exercised here. Helpers come from `test_app_dedup.py` — the same
app factory, the same fake dispatcher, the same signature — so the two suites cannot drift on what
"a mixed batch" means.
"""

from __future__ import annotations

from maezo.platform.webhooks.whatsapp.app import create_app
from tests.support.dedup_fakes import FakeDedupRegistry

from .test_app_dedup import (
    _counter,
    _envelope,
    _FakeDispatcher,
    _guard,
    _non_text,
    _post,
    _settings,
    _text,
)


async def test_a_mixed_batch_carries_its_own_label_instead_of_looking_like_a_clean_ok() -> None:
    """R-100's core: one dispatched text + one failed ack used to be indistinguishable from a
    fully successful request in the ONLY signal operators have."""
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher(fail_ack=True)
    app = create_app(_settings(), dispatcher=dispatcher, dedup=_guard(registry))  # type: ignore[arg-type]
    before_partial, before_ok = _counter("partial_failure"), _counter("ok")

    response = await _post(app, _envelope(_text(), _non_text()))

    assert _counter("partial_failure") == before_partial + 1
    assert _counter("ok") == before_ok, "a mixed batch is NOT an ok request"
    assert response.json()["failed"] == 1


async def test_an_all_success_batch_is_still_labelled_ok() -> None:
    """Negative control for the label above — otherwise `partial_failure` could be emitted for
    every batch and the test above would still pass."""
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher()
    app = create_app(_settings(), dispatcher=dispatcher, dedup=_guard(registry))  # type: ignore[arg-type]
    before_partial, before_ok = _counter("partial_failure"), _counter("ok")

    response = await _post(app, _envelope(_text(), _non_text()))

    assert response.status_code == 200
    assert _counter("ok") == before_ok + 1
    assert _counter("partial_failure") == before_partial


async def test_the_mixed_batch_returns_500_once_the_dedup_guard_is_active() -> None:
    """The owner's flip criterion, evaluated in code rather than deferred to a second decision:
    "quando a guarda de dedup por `wamid` estiver ativa no caminho de entrada, o lote misto passa
    a devolver `500`". With the guard, 500 is strictly better — Meta re-delivers the batch, the
    part that already succeeded is suppressed as a duplicate, and only the failed message runs
    again."""
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher(fail_ack=True)
    app = create_app(_settings(), dispatcher=dispatcher, dedup=_guard(registry))  # type: ignore[arg-type]

    response = await _post(app, _envelope(_text(), _non_text()))

    assert response.status_code == 500
    assert response.json() == {
        "status": "partial_failure",
        "dispatched": 1,
        "failed": 1,
        "acked": 0,
    }


async def test_the_mixed_batch_keeps_the_200_trade_off_while_no_guard_is_wired() -> None:
    """The other half of the same criterion: with no guard, a 500 would re-send what already
    succeeded, so the disclosed trade-off (200, one ack lost, nothing re-sent) still holds."""
    dispatcher = _FakeDispatcher(fail_ack=True)
    app = create_app(_settings(), dispatcher=dispatcher)  # type: ignore[arg-type]

    response = await _post(app, _envelope(_text(), _non_text()))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "dispatched": 1, "failed": 1, "acked": 0}


async def test_the_retry_of_a_flipped_mixed_batch_answers_nobody_twice() -> None:
    """The flip is only safe because of the dedup — proven end to end: the redelivered batch
    re-runs ONLY the message that failed."""
    registry = FakeDedupRegistry()
    dispatcher = _FakeDispatcher(fail_ack=True)
    app = create_app(_settings(), dispatcher=dispatcher, dedup=_guard(registry))  # type: ignore[arg-type]
    payload = _envelope(_text(), _non_text())

    first = await _post(app, payload)
    second = await _post(app, payload)

    assert first.status_code == 500
    assert len(dispatcher.dispatched) == 1, "the text turn must not run twice"
    assert second.json()["duplicates"] == 1
    assert second.json()["failed"] == 1
