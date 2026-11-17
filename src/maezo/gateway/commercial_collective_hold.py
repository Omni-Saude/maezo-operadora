"""E-SF-20 (EP-REG-08a, maezo side) — waiting posture: no collective self-service offer.

Plan row E-SF-20 / record REG-08, decided inside the record (engineering, no legal judgement):
"(i) enquanto não houver ratificação, o funil omni NÃO oferece caminho coletivo self-service —
coletivo só por rota humana interna" and "(iii) a recusa ao comprador usa linguagem neutra sem
caracterizar o canal". Only the fallback variant is built ("coletivo self-service bloqueado;
tela dormente (API-only); PF-only permanente"). The OP21 enforcing posture, the
`legitimacy_unproven` token and the enforcing posture of the vendor surface are E-SF-20r
(EP-REG-08b, GTM-E-36) and stay OUT: nothing here reads the legitimacy store, mounts a guard
or decides legitimacy. Coordinator decision CD-04: only the maezo side of EP-REG-08a; the
buyer-surface rendering (`customer-effects.ts`) belongs to the consumer repository.

Where the contract type exists. The frozen C6-4 wire carries the contract type in exactly one
place: `modality` of a public catalog product (`individual_familiar | empresarial | adesao`,
with `offerState` `usable | unavailable | review_required`). `OfferQuotePayload`
(catalog_ref/catalog_version/product_ref/profile_ref), `OfferTermsPayload`
(quote_ref/source_revision/terms_digest), the quote result and the trusted scope carry none,
so the admission cannot tell a collective `offer.terms` from a PF one (GAP, successor
contract). The posture therefore holds at the one boundary where maezo sees the type: the
self-service public catalog read, which is where every quote selector of the funnel comes
from. A product of a collective modality may be disclosed as information (public price), but
never as a self-service contractable offer: `offerState: "usable"` on a collective product is
refused before disclosure. `unavailable` and `review_required` (the human route) pass
unchanged; `individual_familiar` products are untouched. Lifting the hold is ratification of
REG-08 plus a successor contract, never a flag or an environment variable.

GAP-ESF20-TERMS: a admissão não impede `offer.quote`/`offer.terms` com `product_ref`
coletivo; o critério 'nenhuma oferta coletiva chega a offer.terms' depende do sucessor C6-4
(E-04) e do AMH honrar `offerState`.

Why the whole catalog is refused instead of filtered. Removing the collective products and
serving the rest would disclose bytes the source never authorized: the gateway discloses only
after `PublicCatalogProof.result_digest` matches the sha256 of the exact source body, and the
C6-4 `x-public-catalog.read_composition` says "Maezo validates admitted source projection and
returns the same source wire to Sales". A filtered body would break both bindings.

Refusal shape. The hold raises the frozen C6-4 pair `denied/purpose_denied`: the funnel's
self-service purpose does not include collective contracting while REG-08 is unratified. The
public catalog route renders every refusal with its own fixed body, so no hold detail leaves
the process.

Buyer-facing text (ADDENDUM-01 §6: "texto de recusa de EP-REG-08a nomeia o critério e oferece
caminho PF (SC-08)"). `COLLECTIVE_HOLD_REFUSAL_TEXT` names the criterion with the rubric of
RN 489/2022 art. 23-A as pinned verbatim in NORMATIVE-BASELINE-v2 ("Legitimidade da pessoa
jurídica contratante de beneficiário em plano coletivo") and offers the two SC-08 paths
verbatim ("Contratar como individual/familiar", "Falar com especialista"). It says nothing
about who sells: no sales-channel figure is named or characterized (REG-08 (iii); parecer §3
as transcribed in REG-08). It cites no article text of RN 557/2022, which the baseline does
not hold verbatim. The C6-4 refusal carries no text slot, so this constant is the reviewed
copy the consumer renders (E-SF-ADD01-06); nothing here sends it to anyone.
"""

from __future__ import annotations

import json
from typing import Final, Literal

from maezo.ports.commercial import (
    CommercialCodec,
    CommercialCommand,
    CommercialRefusalError,
    CommercialStatus,
    OperationalResult,
)

__all__ = [
    "COLLECTIVE_HOLD_REFUSAL",
    "COLLECTIVE_HOLD_REFUSAL_TEXT",
    "COLLECTIVE_MODALITIES",
    "SELF_SERVICE_OFFER_STATE",
    "CollectiveHoldCatalogCodec",
    "collective_self_service_offers",
]

#: The C6-4 catalog `modality` values of a collective contract (contractType
#: `coletivo_empresarial` / `coletivo_adesao` in the plan row). `individual_familiar` is the
#: only self-service modality while REG-08 is unratified.
COLLECTIVE_MODALITIES: Final = frozenset({"empresarial", "adesao"})

#: The C6-4 `offerState` that makes a catalog product a self-service contractable offer.
SELF_SERVICE_OFFER_STATE: Final = "usable"

#: The refusal pair of the hold — both values belong to the frozen C6-4 refusal vocabulary.
COLLECTIVE_HOLD_REFUSAL: Final[tuple[Literal["denied"], Literal["purpose_denied"]]] = (
    "denied",
    "purpose_denied",
)

#: The reviewed buyer-facing refusal copy: names the criterion, offers the PF path and the
#: human route, and names no sales-channel figure. Keys are closed; the consumer renders them.
COLLECTIVE_HOLD_REFUSAL_TEXT: Final[dict[str, str]] = {
    "titulo": "Plano coletivo indisponível na contratação on-line",
    "criterio": "Legitimidade da pessoa jurídica contratante de beneficiário em plano coletivo",
    "mensagem": (
        "A contratação de plano coletivo depende da comprovação da legitimidade da pessoa "
        "jurídica contratante, e essa comprovação ainda não é feita na contratação on-line. "
        "Você pode contratar um plano individual ou familiar agora, ou falar com um "
        "especialista sobre o plano coletivo."
    ),
    "caminho_pf": "Contratar como individual/familiar",
    "rota_humana": "Falar com especialista",
}


def collective_self_service_offers(catalog: object) -> tuple[str, ...]:
    """The `optionRef` of every collective product the catalog offers as self-service.

    A pure reading of an already wire-validated catalog body: a product whose `modality` is
    collective AND whose `offerState` is `usable`. Anything that is not the validated shape
    counts as a violation (fail-closed), never as an empty answer.
    """
    if not isinstance(catalog, dict) or not isinstance(catalog.get("products"), list):
        raise ValueError("collective_hold_catalog_unreadable")
    found: list[str] = []
    for product in catalog["products"]:
        if not isinstance(product, dict):
            raise ValueError("collective_hold_catalog_unreadable")
        modality = product.get("modality")
        offer_state = product.get("offerState")
        if not isinstance(modality, str) or not isinstance(offer_state, str):
            raise ValueError("collective_hold_catalog_unreadable")
        if modality in COLLECTIVE_MODALITIES and offer_state == SELF_SERVICE_OFFER_STATE:
            found.append(str(product.get("optionRef")))
    return tuple(found)


class CollectiveHoldCatalogCodec(CommercialCodec):
    """Public catalog codec under the REG-08 waiting posture.

    The inner (frozen C6-4) codec validates the wire first; then a catalog that offers any
    collective product as `usable` is refused `denied/purpose_denied` before disclosure. Every
    other method delegates unchanged, so the Sales command path and a PF-only catalog are
    byte-identical to the shipped state.
    """

    def __init__(self, inner: CommercialCodec) -> None:
        if not isinstance(inner, CommercialCodec):
            raise ValueError("commercial_composition_invalid")
        self._inner = inner

    @property
    def schema_digest(self) -> str:
        return self._inner.schema_digest

    def parse_command(self, body: bytes) -> CommercialCommand:
        return self._inner.parse_command(body)

    def parse_result(self, body: bytes, command: CommercialCommand) -> OperationalResult:
        return self._inner.parse_result(body, command)

    def refusal(
        self, status: CommercialStatus, reason_code: str, operation_ref: str | None = None
    ) -> OperationalResult:
        return self._inner.refusal(status, reason_code, operation_ref)

    def parse_public_result(self, body: bytes) -> bytes:
        disclosed = self._inner.parse_public_result(body)
        try:
            offers = collective_self_service_offers(json.loads(disclosed))
        except ValueError:
            raise CommercialRefusalError(*COLLECTIVE_HOLD_REFUSAL) from None
        if offers:
            raise CommercialRefusalError(*COLLECTIVE_HOLD_REFUSAL)
        return disclosed
