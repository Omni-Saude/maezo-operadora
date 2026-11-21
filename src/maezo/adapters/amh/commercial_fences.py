"""E-SF-04 — "zero saúde" wire fences and closed vocabularies on the commercial plane.

Conservative fallback of the plan row (state A of REG-02; fallback of REG-10/TB-03/TB-24):
nothing health-derived enters the commercial wire. No DPO field-class taxonomy is decided
here: every marker below is a term the spec text names, and every open taxonomy point is a
GAP that only TIGHTENS when it lands (the purpose allowlist is injected, never defaulted).

- REG-02a: `contract_health_slots` applies the acceptance regex of EP-REG-02a verbatim to
  every property name and description under `$defs` of a C6-4 document.
- REG-10a / TB-03-a: `validate_intake_descriptor` is the canonical pre-DS descriptor
  validator (stages `prospect|contact|party`, contract :1858-1872): denylist over
  `field_ref`/`label`/`choices`/`purpose_ref`, purpose allowlist (an UNKNOWN purpose
  collapses into `PHI_IN_COMMERCIAL_INPUT`, the `vendor_notice.screen_commercial_input`
  mold), no `value_type: text` and at most five fields in `prospect`.
- TB-24-a / TB-12-b: `screen_commercial_input` refuses an undeclared or health-marked key
  and a health-marked `IntakeValue.field_ref` before any composition; `screen_journey_labels`
  refuses a health-marked `JourneyPendingItem`/`JourneyNextAction` label;
  `offer_quote_slot_violations` / `result_score_slots` read a C6-4 document (pinned or
  successor) for score/segment/health slots.
- TB-21-c (anti-benchmark) lives with the D-36 draft structures it fences, under
  `maezo.portal.contracts` (the A8 src vocabulary pin keeps that vocabulary there).
- E-SF-06 (EP-REG-01a): `sla_claim_marker` flags a numeric deadline promise ("responderemos
  em 7 dias úteis": marker + number + unit) in a source-provided support label; no prazo is
  shown before Q1/DEC-18 are ratified, so the support disclosure gate refuses it (`unratified_sla_claim`).
  The refusal-vocabulary (EP-REG-29a / TB-E-08-a) and personalization (EP-REG-02b) lints are
  CI checks over contract documents and live with their tests (`tests/unit/gateway/
  test_esf06_*.py`): this gate path carries no age/disability identifier (E-SF-04 pin).

The refusal token travels on the C6-4 wire as the codec's closed `invalid_request` /
`source_unavailable`: `phi_in_commercial_input` is not in the frozen `reason_code` enum and
its wire form is successor-schema negotiation (`commercial_legitimacy_guard.py:122-133`).
The legitimacy guard itself stays body-opaque by design; these fences run in the codec.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, Final, NoReturn, final

__all__ = [
    "DS_CONCEPTS",
    "KNOWN_FALSE_POSITIVES",
    "OFFER_QUOTE_KEYS",
    "PRE_DS_STAGES",
    "PROSPECT_MAX_FIELDS",
    "REG02A_HEALTH_PATTERN",
    "RESULT_SCORE_PATTERN",
    "CommercialFenceError",
    "CommercialFenceRefusal",
    "contract_health_slots",
    "health_marker",
    "offer_quote_slot_violations",
    "result_score_slots",
    "screen_commercial_input",
    "screen_journey_labels",
    "sla_claim_marker",
    "validate_intake_descriptor",
]

#: EP-REG-02a acceptance (1), verbatim: `health|saude|ds_|declaration|cpt|agravo|clinical`.
REG02A_HEALTH_PATTERN: Final = re.compile(r"health|saude|ds_|declaration|cpt|agravo|clinical", re.IGNORECASE)

#: TB-E-24-a acceptance (3): no commercial result exposes `score|propensity|risk|segment`.
RESULT_SCORE_PATTERN: Final = re.compile(r"score|propensity|risk|segment", re.IGNORECASE)

#: TB-E-24-a test: the only `OfferQuotePayload` keys any C6-4 successor may carry.
OFFER_QUOTE_KEYS: Final = frozenset({"catalog_ref", "catalog_version", "product_ref", "profile_ref"})

#: The pre-DS intake stages of C6-4 (`IntakeDescriptor.stage`, contract :650-656).
PRE_DS_STAGES: Final = frozenset({"prospect", "contact", "party"})

#: TB-03 condition (1): at most five fields in `prospect`.
PROSPECT_MAX_FIELDS: Final = 5

# Health markers, matched on accent-folded lowercase tokens (split on non-alphanumerics).
# Exact tokens: the REG-02a `ds_`/`cpt` prefixes as identifier segments, and IMC (TB-03 (2)).
_EXACT_TOKENS: Final = frozenset({"ds", "cpt", "imc"})
# Token prefixes: REG-02a terms (`clinical` folded to its stem so pt-BR `clinico` also
# matches) and the TB-03 condition (2) denylist categories — saúde, doença, sintoma,
# medicação, tratamento, hábitos de vida, gestação, deficiência, internação, cirurgia,
# peso/altura/IMC (`internacao`/`internad`, not `internac`: `internacional` is no health term).
_TOKEN_PREFIXES: Final = (
    "health",
    "saude",
    "declaration",
    "agravo",
    "clinic",
    "doenc",
    "sintom",
    "medicac",
    "medicament",
    "tratament",
    "habito",
    "gestac",
    "gestant",
    "gravid",
    "deficien",
    "internacao",
    "internad",
    "cirurg",
    "peso",
    "altura",
)
#: KNOWN FALSE POSITIVES (accepted over-refusal of the conservative fallback; pinned by
#: `test_known_false_positives_are_pinned` so any loosening is a deliberate act):
#: "Declaração de Saúde pendente", "Modalidade: Saúde PME", "Forma de tratamento",
#: "Clínica credenciada", "Tratamento de dados pessoais", "Peso do documento" — the markers
#: are lexical, never semantic, and a non-clinical use of a named term still refuses.
KNOWN_FALSE_POSITIVES: Final = (
    "Declaração de Saúde pendente",
    "Modalidade: Saúde PME",
    "Forma de tratamento",
    "Clínica credenciada",
    "Tratamento de dados pessoais",
    "Peso do documento",
)
#: The DS concepts EP-REG-10 names from the source vocabulary (`health-onboarding.ts:9`):
#: identifiers carrying one of them are health-class whatever the stage.
DS_CONCEPTS: Final = (
    ("known", "condition"),
    ("care", "priority"),
    ("history",),
    ("treatment",),
    ("daily", "life"),
    ("screening",),
)


# EP-REG-01a: a prazo PROMISE is a deadline marker ("em", "até", "dentro de", "prazo",
# "retorno", "resposta"), optional qualifiers ("no máximo", "de", "cerca de"), then a number
# (digits or pt-BR cardinals, "setenta e duas"; "2 (dois)"; a range "2 a 3") and a time unit.
# A bare number + unit ("Atendimento 24 horas", "Carência de 180 dias") is a product fact.
_SLA_NUMBER: Final = (
    r"(?<![a-z0-9])(?:\d+|um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|quinze"
    r"|vinte|trinta|quarenta|cinquenta|sessenta|setenta|oitenta|noventa|cem)"
)
_SLA_MARKER: Final = (
    r"(?:\bem\b|\bate\b|dentro de|\bprazo\b|\bretorno\b|\bresposta\b)[^0-9a-z]*"
    r"(?:(?:no\s+)?(?:maximo|minimo|ate|de|cerca de|aproximadamente)[^0-9a-z]*)*"
)
_SLA_CLAIM_PATTERN: Final = re.compile(
    _SLA_MARKER
    + _SLA_NUMBER
    + r"(?:\s+e\s+"
    + _SLA_NUMBER
    + r")*(?:\s*\([a-z ]+\))?(?:\s*(?:a|ou|-)\s*"
    + _SLA_NUMBER
    + r")?\s*(?:dias?|horas?|hs?|min|minutos?|semanas?|mes|meses|days?|hours?|weeks?|months?)\b"
)


@final
class CommercialFenceRefusal(StrEnum):
    """Closed refusal vocabulary of the E-SF-04 fences — never diagnostic text."""

    PHI_IN_COMMERCIAL_INPUT = "PHI_IN_COMMERCIAL_INPUT"
    PROSPECT_FREE_TEXT = "PROSPECT_FREE_TEXT"
    PROSPECT_FIELD_LIMIT = "PROSPECT_FIELD_LIMIT"
    #: EP-REG-01a: a numeric deadline claim reached a support disclosure before Q1/DEC-18.
    UNRATIFIED_SLA_CLAIM = "unratified_sla_claim"


@final
class CommercialFenceError(ValueError):
    """Bounded typed refusal; never carries the refused key, label or value."""

    def __init__(self, reason: CommercialFenceRefusal) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _tokens(text: str) -> tuple[str, ...]:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").casefold()
    return tuple(token for token in re.split(r"[^a-z0-9]+", folded) if token)


def health_marker(text: str) -> bool:
    """True when `text` (key, ref, label or choice) carries a spec-named health marker."""
    tokens = _tokens(text)
    if any(token in _EXACT_TOKENS or token.startswith(_TOKEN_PREFIXES) for token in tokens):
        return True
    return any(
        tokens[index : index + len(concept)] == concept
        for concept in DS_CONCEPTS
        for index in range(len(tokens) - len(concept) + 1)
    )


def sla_claim_marker(text: str) -> bool:
    """True when `text` promises a numeric deadline (EP-REG-01a: "em 7 dias úteis", "até 72 horas").

    This is a marker HEURISTIC (deadline marker + number + unit), not a complete detector: a
    prazo phrased without one of the markers passes. The full guarantee of "no prazo before
    Q1" is the closed state — no wait clock or prazo value exists anywhere until Q1/DEC-18
    are ratified; this fence only catches a source label that states one anyway.
    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").casefold()
    return _SLA_CLAIM_PATTERN.search(folded) is not None


def _refuse(reason: CommercialFenceRefusal = CommercialFenceRefusal.PHI_IN_COMMERCIAL_INPUT) -> NoReturn:
    raise CommercialFenceError(reason)


def _keys(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > 32:
        _refuse()
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _keys(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item, depth + 1)


def screen_commercial_input(
    payload: Mapping[str, Any],
    *,
    declared_keys: frozenset[str],
    allowed_field_refs: frozenset[str] | None = None,
) -> None:
    """Refuse a commercial command payload carrying health or UNKNOWN-class input.

    A top-level key outside the command's declared payload keys is UNKNOWN class and, until
    the DPO taxonomy exists, collapses into `PHI_IN_COMMERCIAL_INPUT` (vendor_notice mold);
    a health-marked key at any depth, or an `IntakeValue.field_ref` with a health marker,
    refuses the same way — before any composition, so nothing reaches a source. With
    `allowed_field_refs` (the field refs of the admitted descriptor) every `field_ref`
    outside that closed set is UNKNOWN class and refuses too; `None` means the caller holds
    no descriptor binding (the stateless codec gates intake values by the purpose list).
    """
    if set(payload) - declared_keys:
        _refuse()
    if any(health_marker(key) for key in _keys(payload)):
        _refuse()
    values = payload.get("values")
    if isinstance(values, list):
        for value in values:
            if not isinstance(value, Mapping):
                _refuse()
            field_ref = str(value.get("field_ref", ""))
            if health_marker(field_ref) or (
                allowed_field_refs is not None and field_ref not in allowed_field_refs
            ):
                _refuse()


def validate_intake_descriptor(descriptor: Mapping[str, Any], *, purpose_allowlist: frozenset[str]) -> None:
    """The pre-DS descriptor fence (TB-03 condition (1)-(3), REG-10 (i)).

    `purpose_allowlist` is the closed, versioned purpose set of the stage (região,
    modalidade, composição do grupo, contato com permissão — TB-03 (1)); its tokens are
    source/DPO-published (GAP), so it is a required argument with no default and an empty
    set refuses every field. Order: health denylist, prospect shape, purpose allowlist.

    E-SF-37r (b): no `prospect` stage there is no document/CPF entry (E1), so a field with
    `value_type: "document"` refuses on its own, before and independently of the purpose
    allowlist (the collapsed `PHI_IN_COMMERCIAL_INPUT` code: no new refusal member).
    """
    stage = descriptor.get("stage")
    fields = descriptor.get("fields")
    if stage not in PRE_DS_STAGES or not isinstance(fields, list):
        _refuse()
    for field in fields:
        if not isinstance(field, Mapping):
            _refuse()
        texts = [
            field.get("field_ref"),
            field.get("label"),
            field.get("purpose_ref"),
            *(field.get("choices") or []),
        ]
        if any(not isinstance(text, str) or health_marker(text) for text in texts):
            _refuse()
    if stage == "prospect":
        if len(fields) > PROSPECT_MAX_FIELDS:
            _refuse(CommercialFenceRefusal.PROSPECT_FIELD_LIMIT)
        if any(field.get("value_type") == "text" for field in fields):
            _refuse(CommercialFenceRefusal.PROSPECT_FREE_TEXT)
        if any(field.get("value_type") == "document" for field in fields):
            _refuse()
    if any(field.get("purpose_ref") not in purpose_allowlist for field in fields):
        _refuse()


def screen_journey_labels(journeys: Iterable[Mapping[str, Any]], *, vocabulary: frozenset[str]) -> None:
    """TB-12-b: pending-item / next-action labels come from a CLOSED non-clinical vocabulary.

    Applied to every label, not only to DS `requirement_ref`s: the DS requirement marker and
    the closed label set are not published (GAP), so `vocabulary` is injected and an empty
    set refuses every label. A label outside the set — health-marked or not ("hipertensão",
    "Tabagista") — refuses `PHI_IN_COMMERCIAL_INPUT`; a health-marked label refuses even if
    a vocabulary were ever to list it (defense in depth).
    """
    for journey in journeys:
        for item in (*journey.get("pending_items", ()), *journey.get("next_actions", ())):
            label = str(item.get("label", ""))
            if label not in vocabulary or health_marker(label):
                _refuse()


def contract_health_slots(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Every `$defs` property (name or description) the REG-02a regex matches, as paths."""
    hits: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            properties = node.get("properties")
            if isinstance(properties, Mapping):
                for name, spec in properties.items():
                    description = spec.get("description", "") if isinstance(spec, Mapping) else ""
                    if REG02A_HEALTH_PATTERN.search(name) or REG02A_HEALTH_PATTERN.search(str(description)):
                        hits.append(f"{path}.{name}")
            for key, item in node.items():
                walk(item, f"{path}/{key}")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    for name, definition in document["$defs"].items():
        walk(definition, name)
    return tuple(sorted(hits))


def offer_quote_slot_violations(document: Mapping[str, Any]) -> tuple[str, ...]:
    """TB-24-a (1): `OfferQuotePayload` closed and limited to the four selector keys."""
    payload = document["$defs"]["OfferQuotePayload"]
    violations = sorted(set(payload.get("properties", {})) - OFFER_QUOTE_KEYS)
    if payload.get("additionalProperties") is not False:
        violations.append("additionalProperties")
    return tuple(violations)


def result_score_slots(document: Mapping[str, Any], roots: Iterable[str]) -> tuple[str, ...]:
    """TB-24-a (3): property names under the result defs (following `$ref`) that match."""
    definitions = document["$defs"]
    seen: set[str] = set()
    hits: set[str] = set()

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = ref.rsplit("/", 1)[1]
                if target not in seen:
                    seen.add(target)
                    walk(definitions[target], target)
            for key, item in node.items():
                if key == "properties" and isinstance(item, Mapping):
                    for name, spec in item.items():
                        if RESULT_SCORE_PATTERN.search(name):
                            hits.add(f"{path}.{name}")
                        walk(spec, f"{path}.{name}")
                else:
                    walk(item, path)
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    for root in roots:
        seen.add(root)
        walk(definitions[root], root)
    return tuple(sorted(hits))
