"""P4 (16/10/2026): ONE number for the attendant's note — wire limit = effective limit = generated maxLength.

`MAX_NOTES` is what `pseudonymize_notes` enforces AFTER the scrub (P3); the browser counts against
the `maxLength` of the generated OpenAPI artifact the web reads (`completionClient.ts::maxNotes`).
If the two drift, the counter promises what the server refuses (or the reverse). And the note at
that limit must fit the Helena resume path: `RETOMADA_MAX_INSTRUCOES` (her own refusal ceiling) and
the 4096-character body of the WhatsApp Cloud API, once composed with the approved template.
"""

import json
from pathlib import Path

from maezo.agents.helena.graph import RETOMADA_MAX_INSTRUCOES, compor_mensagem_de_retomada
from maezo.gateway.human.completion import MAX_NOTES as GATEWAY_MAX_NOTES
from maezo.gateway.human.completion import pseudonymize_notes
from maezo.portal.contracts.completions import MAX_NOTES, TaskCompletionSubmission

OPENAPI = Path(__file__).resolve().parents[3] / "src" / "maezo" / "portal" / "web" / "openapi.json"
#: WhatsApp Cloud API: maximum length of a text message body.
WHATSAPP_TEXT_BODY_MAX = 4096


def _wire_max_length() -> int:
    schema = json.loads(OPENAPI.read_text(encoding="utf-8"))
    submission = schema["components"]["schemas"]["TaskCompletionSubmission"]
    return submission["properties"]["notas_resolucao"]["maxLength"]


def test_the_wire_limit_the_effective_limit_and_the_generated_max_length_are_one_number():
    model_max = TaskCompletionSubmission.model_json_schema()["properties"]["notas_resolucao"]["maxLength"]
    assert MAX_NOTES == GATEWAY_MAX_NOTES == model_max == _wire_max_length() == 2000


def test_a_note_at_the_limit_fits_the_helena_resume_ceiling_and_the_whatsapp_body():
    note = pseudonymize_notes("x" * MAX_NOTES).text  # the longest note the portal lets through
    assert len(note) == MAX_NOTES <= RETOMADA_MAX_INSTRUCOES
    assert len(compor_mensagem_de_retomada(note)) <= WHATSAPP_TEXT_BODY_MAX
