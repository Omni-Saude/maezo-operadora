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
  refuses with `disclosure_incompleto_rn413_art4`. A decline is never blocked. The gate is not
  wired into `CommercialGateway` because the pinned wire carries no inciso content (GAP).

Inciso VII ("formação do preço") holds ONLY the public price fact as integer cents. A mapping,
a string, a breakdown or any intermediary-remuneration/component content is not representable
and refuses as an incomplete inciso VII; the shape is closed, so no other price-formation key
exists. No remuneration value and no cross-operator comparison exists here (Q7 open). This
module never names the remuneration vocabulary the A8 lock pins to its own loader
(`tests/unit/test_commercial_admission_a8.py`), so the D-36 anti-comparison fence is not
imported here: a text-level comparator check belongs to E-SF-36r (EP-REG-07a).

Normative text is quoted verbatim from NORMATIVE-BASELINE-v2 §2.3 (RN 413/2016, DOU 14/11/2016
S1 p. 109; art. 4º §2º and art. 18 from the official SEI 31143600 copy, caveat GAP-2). Incisos
V and VI are elided ("[...]") in that baseline: their verbatim is a GAP (`None` below), never
written from memory; their terms slots still count for presence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal, final

from maezo.gateway.required_text import is_blank

__all__ = [
    "DISCLOSURE_STATUS_PENDING",
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
]

#: The only checklist status while GAP-10 is open (REG-34 acceptance).
DISCLOSURE_STATUS_PENDING: Final = "incompleto_fonte_pendente"
DisclosureStatus = Literal["incompleto_fonte_pendente"]

_SOURCE_DOU: Final = "RN 413/2016, DOU 14/11/2016 S1 p. 109 (NORMATIVE-BASELINE-v2 §2.3)"
_SOURCE_SEI: Final = "RN 413/2016, cópia oficial SEI 31143600 (NORMATIVE-BASELINE-v2 §2.3; caveat GAP-2)"
_GAP_ELIDED: Final = "GAP: verbatim elidido '[...]' em NORMATIVE-BASELINE-v2 §2.3"


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
