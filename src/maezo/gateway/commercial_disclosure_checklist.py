"""E-SF-36 (EP-REG-34) — RN 413 art. 4º pre-contract disclosure checklist, fallback variant.

Plan row E-SF-36 / record REG-34 (NORM-GAP-10): a disclosure checklist with a PENDING source
slot. Only the fallback is built here: "checklist com slot pendente; inciso VII = só o fato
público de preço". E-SF-36r (EP-REG-07a, price formation on screen × CADE, Q7) is out.

What this module is:

- `RN413_ART4_CHECKLIST` maps RN 413 art. 4º incisos I–IX and §1º 1:1 to a candidate terms
  field each (`terms.rn413_art4_<n>`). The C6-4 `offer.terms` wire carries no per-inciso
  field (`OfferTermsPayload` = quote_ref/source_revision/terms_digest; `HostedTermsDocument` =
  url/expires_at), so these names are CANDIDATE slots for a successor contract, never wire.
- `GUIA_ART18_SLOT` is the pending source slot: art. 4º §2º says the art. 18 Guia de
  Orientação "especificará outras informações que deverão ser destacadas"; the Guia is not
  located (GAP-10 open), so `GUIA_ART18_SOURCE` is `None` and the slot cannot be filled.
- `claim_status` is the only way to state a checklist status. While the Guia slot is empty the
  only representable status is `incompleto_fonte_pendente`; any other claim refuses with
  `fonte_normativa_pendente_rn413_art18`.
- `require_disclosure_before_decision` is the server-side precondition of the buyer decision
  (the C6-4 `acceptance.record` `decision: accept`; maezo has no buyer UI button — the hosted
  terms surface belongs to the issuer). Missing or invalid content for any of I–IX or §1º
  refuses with `disclosure_incompleto_rn413_art4`. A decline is never blocked. It is wired
  into the bound Sales admission (`maezo.gateway.commercial_admission`), which reads the
  `DISCLOSURE_TERMS_FIELDS` slots of the decision payload. The pinned C6-4
  `AcceptanceRecordPayload` is closed and carries none of them (GAP), so on that wire every
  `accept` refuses before any source step, even with the A3 record admitted; it opens only
  when a successor contract carries the inciso content.

Inciso VII ("formação do preço") holds ONLY the public price fact as integer cents. A mapping,
a string, a breakdown or any intermediary-remuneration/component content is not representable
and refuses as an incomplete inciso VII; the shape is closed, so no other price-formation key
exists. No remuneration value and no cross-operator comparison exists here (Q7 open). This
module never names the remuneration vocabulary the A8 lock pins to its own loader
(`tests/unit/test_commercial_admission_a8.py`), so the D-36 anti-comparison fence is not
imported here: a text-level comparator check belongs to E-SF-36r (EP-REG-07a).

Normative text is quoted verbatim from `docs/compliance/legal/textos-base/RN-413-2016.md`
(RN 413/2016, DOU 14/11/2016 S1 p. 109; art. 4º caput and §1º). Art. 4º §2º and art. 18 come
only from the official SEI 31143600 copy, not conferred with the DOU and outside that excerpt
(caveat GAP-2). Incisos V and VI are elided ("[...]") in that excerpt: their verbatim is a GAP
(`None` below), never written from memory; their terms slots still count for presence.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal, final

from maezo.gateway.required_text import is_blank
from maezo.ports.commercial import CommercialCommand, CommercialRefusalError

__all__ = [
    "DISCLOSURE_GATED_COMMANDS",
    "DISCLOSURE_STATUS_PENDING",
    "DISCLOSURE_TERMS_FIELDS",
    "GUIA_ART18_SLOT",
    "GUIA_ART18_SOURCE",
    "PUBLIC_PRICE_FIELD",
    "RN413_ART4_CHECKLIST",
    "DisclosureChecklistError",
    "DisclosureItem",
    "DisclosureRefusal",
    "DisclosureStatus",
    "claim_status",
    "require_disclosure_before_decision",
    "require_precontract_disclosure",
]

#: The only checklist status while GAP-10 is open (REG-34 acceptance).
DISCLOSURE_STATUS_PENDING: Final = "incompleto_fonte_pendente"
DisclosureStatus = Literal["incompleto_fonte_pendente"]

_TEXTO_BASE: Final = "docs/compliance/legal/textos-base/RN-413-2016.md"
_SOURCE_DOU: Final = f"RN 413/2016, DOU 14/11/2016 S1 p. 109 ({_TEXTO_BASE})"
_SOURCE_SEI: Final = "RN 413/2016, cópia oficial SEI 31143600 (não conferida no DOU; caveat GAP-2)"
_GAP_ELIDED: Final = f"GAP: verbatim elidido '[...]' em {_TEXTO_BASE}"


@final
class DisclosureRefusal(StrEnum):
    """Closed refusal vocabulary of EP-REG-34 (tokens verbatim from REG-34)."""

    FONTE_NORMATIVA_PENDENTE = "fonte_normativa_pendente_rn413_art18"
    DISCLOSURE_INCOMPLETO = "disclosure_incompleto_rn413_art4"


@final
class DisclosureChecklistError(ValueError):
    """Typed refusal; never carries the refused field or value."""

    def __init__(self, reason: DisclosureRefusal) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class DisclosureItem:
    """One checklist row: the dispositivo, its candidate terms slot and its verbatim (or GAP)."""

    dispositivo: str
    terms_field: str
    verbatim: str | None
    source: str


#: RN 413 art. 4º I–IX + §1º, 1:1 to candidate terms slots (caput: "Durante o processo de
#: contratação, anteriormente à sua finalização, as operadoras devem apresentar as informações
#: inerentes ao contrato do plano de saúde, dentre as quais, destacam-se:").
RN413_ART4_CHECKLIST: Final[tuple[DisclosureItem, ...]] = (
    DisclosureItem(
        "art. 4º, I",
        "terms.rn413_art4_i",
        "nome comercial e nº de registro do plano na ANS",
        _SOURCE_DOU,
    ),
    DisclosureItem(
        "art. 4º, II", "terms.rn413_art4_ii", "tipo de contratação e suas peculiaridades", _SOURCE_DOU
    ),
    DisclosureItem(
        "art. 4º, III", "terms.rn413_art4_iii", "segmentação assistencial do plano de saúde", _SOURCE_DOU
    ),
    DisclosureItem(
        "art. 4º, IV",
        "terms.rn413_art4_iv",
        "área geográfica de abrangência do plano de saúde",
        _SOURCE_DOU,
    ),
    DisclosureItem("art. 4º, V", "terms.rn413_art4_v", None, _GAP_ELIDED),
    DisclosureItem("art. 4º, VI", "terms.rn413_art4_vi", None, _GAP_ELIDED),
    DisclosureItem("art. 4º, VII", "terms.rn413_art4_vii", "formação do preço", _SOURCE_DOU),
    DisclosureItem("art. 4º, VIII", "terms.rn413_art4_viii", "serviços e coberturas adicionais", _SOURCE_DOU),
    DisclosureItem(
        "art. 4º, IX",
        "terms.rn413_art4_ix",
        "mecanismos de regulação da utilização dos serviços",
        _SOURCE_DOU,
    ),
    DisclosureItem(
        "art. 4º, §1º",
        "terms.rn413_art4_par1",
        "Além dessas informações, todos os Guias e Manuais que sejam obrigatórios na contratação "
        "presencial devem estar disponíveis para impressão ou download e assinatura pelo interessado.",
        _SOURCE_DOU,
    ),
)

#: The pending source slot (art. 4º §2º + art. 18). Not part of the presence gate: its items
#: are unknown until the Guia is located (GAP-10).
GUIA_ART18_SLOT: Final = DisclosureItem(
    "art. 4º, §2º + art. 18",
    "terms.rn413_art18_guia",
    "O Guia de Orientação previsto no artigo 18 desta Resolução especificará outras informações "
    "que deverão ser destacadas.",
    _SOURCE_SEI,
)

#: The located Guia de Orientação. `None` while GAP-10 is open — never defaulted, never inferred.
GUIA_ART18_SOURCE: Final[str | None] = None

#: Inciso VII slot: only the public price fact, integer cents.
PUBLIC_PRICE_FIELD: Final = "terms.rn413_art4_vii"

_ART4_FIELDS: Final = frozenset(item.terms_field for item in RN413_ART4_CHECKLIST)

#: Every slot the gate reads from a decision payload: I–IX/§1º plus the pending Guia slot (so a
#: filled Guia slot reaches the gate and refuses `fonte_normativa_pendente_rn413_art18`).
DISCLOSURE_TERMS_FIELDS: Final = _ART4_FIELDS | {GUIA_ART18_SLOT.terms_field}


def _refuse(reason: DisclosureRefusal) -> DisclosureChecklistError:
    return DisclosureChecklistError(reason)


def claim_status(requested: str) -> DisclosureStatus:
    """State the checklist status. With the Guia slot empty only the pending status exists.

    Any other claim (`completo`, `complete`, ...) refuses with
    `fonte_normativa_pendente_rn413_art18`. There is no branch that returns another status:
    a located Guia adds items to the checklist (REG-34 "o que mudaria a resposta"), which is a
    new epic, not a flag here.
    """
    if requested != DISCLOSURE_STATUS_PENDING:
        raise _refuse(DisclosureRefusal.FONTE_NORMATIVA_PENDENTE)
    return DISCLOSURE_STATUS_PENDING


def _public_price(value: object) -> bool:
    return type(value) is int and value > 0


def require_disclosure_before_decision(
    terms: Mapping[str, object], *, decision: Literal["accept", "decline"]
) -> DisclosureStatus:
    """Precondition of the buyer decision: I–IX and §1º present before `accept`.

    Refuses `disclosure_incompleto_rn413_art4` when a slot of I–IX/§1º is missing or blank,
    when inciso VII is anything but a positive integer-cents public price, or when the content
    carries a key outside the checklist (the shape is closed: extra price-formation content has
    no slot). A filled Guia slot refuses `fonte_normativa_pendente_rn413_art18` (no source can
    fill it). The returned status stays `incompleto_fonte_pendente`: presence of I–IX/§1º
    never upgrades the checklist while the Guia is pending.
    """
    if decision == "decline":
        return DISCLOSURE_STATUS_PENDING
    if decision != "accept" or not isinstance(terms, Mapping):
        raise _refuse(DisclosureRefusal.DISCLOSURE_INCOMPLETO)
    if GUIA_ART18_SLOT.terms_field in terms:
        raise _refuse(DisclosureRefusal.FONTE_NORMATIVA_PENDENTE)
    if set(terms) != _ART4_FIELDS:
        raise _refuse(DisclosureRefusal.DISCLOSURE_INCOMPLETO)
    for item in RN413_ART4_CHECKLIST:
        value = terms[item.terms_field]
        if item.terms_field == PUBLIC_PRICE_FIELD:
            if not _public_price(value):
                raise _refuse(DisclosureRefusal.DISCLOSURE_INCOMPLETO)
        elif is_blank(value):
            raise _refuse(DisclosureRefusal.DISCLOSURE_INCOMPLETO)
    return DISCLOSURE_STATUS_PENDING


# --- the wiring (E-SF-36) ------------------------------------------------------------------
#
# An `acceptance.record` that is not an explicit `decline` must carry, in its own payload, the
# I–IX/§1º content of the checklist before it can reach the source; a decline is never
# blocked. The pinned C6-4 `AcceptanceRecordPayload` is closed (`additionalProperties:
# false`) and has none of these slots, so on that wire every `accept` refuses — with or
# without the A3 record. The internal checklist token stays the `__cause__`; the wire answer
# is the C6-4 `denied/terms_review_required` (the terms review that the decision needs is not
# complete). Nothing here states the disclosure as complete: the checklist status stays
# `incompleto_fonte_pendente` while the art. 18 Guia is pending. Called by the bound Sales
# admission (`commercial_admission`); `CommercialGateway` keeps the payload opaque, so the
# gate is a property of the bound composition (`compose_commercial_gateway`, the only one in
# `src/`).

DISCLOSURE_GATED_COMMANDS: Final = frozenset({"acceptance.record"})

#: Every `terms.*` key of a decision payload reaches the checklist, so content outside the
#: closed slots (e.g. a price-formation breakdown) refuses instead of being dropped.
_TERMS_PREFIX: Final = "terms."


def require_precontract_disclosure(command: CommercialCommand) -> None:
    """Refuse an `acceptance.record` that is not a decline and lacks the art. 4º disclosure.

    The decline exemption needs the payload AND the parsed selection to say `decline`; any
    divergence, a missing selection or an unreadable payload is treated as an accept.
    """
    if command.command not in DISCLOSURE_GATED_COMMANDS:
        return
    try:
        envelope = json.loads(command.wire_bytes)
        payload = envelope["payload"]
        if not isinstance(payload, dict):
            raise TypeError("payload")
        selection = command.terms_selection
        if payload.get("decision") == "decline" and selection is not None and selection.decision == "decline":
            return
        presented = {
            key: value
            for key, value in payload.items()
            if key in DISCLOSURE_TERMS_FIELDS or (isinstance(key, str) and key.startswith(_TERMS_PREFIX))
        }
        require_disclosure_before_decision(presented, decision="accept")
    except (DisclosureChecklistError, ValueError, KeyError, TypeError) as exc:
        raise CommercialRefusalError("denied", "terms_review_required") from exc
