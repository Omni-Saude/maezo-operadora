"""P3 (16/10/2026): the attendant's note is scrubbed WHOLE and refused (never cut) over `MAX_NOTES`.

Before: `pseudonymize_notes` used the 500-character cap of `redact_free_text` (meant for error
messages), so a 1500-character note reached the engine — and the beneficiary — as 500 characters
plus `...[TRUNCATED]`. Now: no cut; the limit is checked on the SCRUBBED text, because the
`[REDACTED_*]` tokens are longer than what they replace (a note under the wire limit can go over
it), and the refusal comes before the audit `intent` link and without touching the engine.
"""

import pytest
from tests.unit.gateway.human.test_task_completion_gateway import RecordingCompletion, close, harness
from tests.unit.portal.test_task_completion_api import BODY, PATH, assert_safe, client

from maezo.gateway.human.completion import MAX_NOTES, NotesTooLongError, pseudonymize_notes
from maezo.tools.workers.phi_vars import REDACTED_PHONE

pytestmark = pytest.mark.asyncio

#: A BR mobile with DDD: 13 characters that the scrub turns into `[REDACTED_PHONE]` (16).
PHONE = "11 91234-5678"


def phones(n: int, pad: int = 0) -> str:
    return " ".join([PHONE] * n) + ("x" * pad)


#: (raw note, accepted?) — raw and scrubbed lengths are asserted in the test, not assumed.
CASES = [
    pytest.param("a" * MAX_NOTES, True, id="cru-no-limite-sem-identificador"),
    pytest.param("b" * 1500, True, id="1500-chega-inteira-antes-cortava-em-500"),
    pytest.param(phones(100, 301), True, id="redigida-exatamente-no-limite"),
    pytest.param(phones(100, 302), False, id="redigida-um-acima-do-limite"),
    pytest.param(phones(140), False, id="abaixo-do-limite-cru-acima-depois-da-redacao"),
]


@pytest.mark.parametrize(("raw", "accepted"), CASES)
async def test_pseudonymize_never_cuts_and_refuses_over_the_limit_after_the_scrub(raw, accepted):
    assert len(raw) <= MAX_NOTES  # every case fits the WIRE limit
    if accepted:
        notes = pseudonymize_notes(raw)
        assert len(notes.text) <= MAX_NOTES
        assert "[TRUNCATED]" not in notes.text
        assert notes.text.count(REDACTED_PHONE) == raw.count(PHONE)
        assert notes.text.replace(REDACTED_PHONE, PHONE) == raw  # whole: nothing cut, nothing else changed
    else:
        with pytest.raises(NotesTooLongError) as refusal:
            pseudonymize_notes(raw)
        assert str(refusal.value) == "notes_too_long"  # closed: no text, no length


@pytest.mark.parametrize(("raw", "accepted"), CASES)
async def test_the_gateway_refuses_before_the_intent_link_and_the_engine(raw, accepted):
    completion = RecordingCompletion()
    g, _, audit = await harness(completion=completion)
    if accepted:
        await close(g, notas_resolucao=raw)
        (_, _, notes, _) = completion.calls[0]
        assert notes.text == pseudonymize_notes(raw).text and "[TRUNCATED]" not in notes.text
        assert [e.phase for e in audit.entries] == ["intent", "result"]
    else:
        with pytest.raises(NotesTooLongError):
            await close(g, notas_resolucao=raw)
        assert completion.calls == []  # the engine transport was never called
        assert audit.entries == []  # and no `intent` link was written


@pytest.mark.parametrize(("raw", "accepted"), CASES)
async def test_the_route_answers_422_notes_too_long(raw, accepted):
    completion = RecordingCompletion()
    connection, g, _ = await client(completion=completion)
    async with connection:
        response = await connection.post(PATH, json={**BODY, "notas_resolucao": raw})
    if accepted:
        assert response.status_code == 200
        assert len(completion.calls) == 1
    else:
        assert_safe(response, 422, "notes_too_long")
        assert completion.calls == [] and g._completion_audit.entries == []
        assert PHONE not in response.text
