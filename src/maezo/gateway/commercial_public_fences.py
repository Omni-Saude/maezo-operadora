"""E-SF-05r (parte a) — cercas da vitrine pública: só a 1ª perna da vitrine anônima.

Linha E-SF-05r, variante decidida pela coordenação (CD-159): a vitrine pública anônima entra só
na primeira perna — catálogo cujo preço pode ser desconhecido; o preço público numérico
(segunda perna) fica FECHADO até o ato do comercial e do jurídico. O canal presencial é fato do
dono ainda não dado: é um parâmetro que nasce vazio, e vazio fecha a vitrine. Filhos atendidos
aqui: TB-E-01-a (registro ANS conhecido é condição de `usable`), EP-REG-G1a (sem fato presencial,
nenhum plano na vitrine on-line, fail-closed) e EP-REG-06a (nenhum filtro local de produto).

Três cercas, todas antes da divulgação e todas sobre o wire já validado pelo codec congelado:

1. Produto `usable` só é exibido como contratável com `ansRegistry` em `dataState: known` e com
   `productRegistryCode` e `operatorRegistryCode` preenchidos (o wire de fonte v1, v2 e v3 carrega
   os dois códigos dentro de `ansRegistry`).
2. Produto com `price` em `dataState: known` (valor numérico, em qualquer das três versões do
   wire) não é exibido enquanto a segunda perna estiver fechada.
3. Sem `PresentialChannelFact` (o padrão), nenhum catálogo é exibido.

Por que o catálogo inteiro é recusado e não filtrado. É o mesmo motivo de
`CollectiveHoldCatalogCodec`: a divulgação só acontece depois que `result_digest` bate com o
sha256 do corpo exato da fonte, e a composição de leitura devolve "o mesmo wire da fonte". Um
corpo filtrado ou reescrito (preço trocado por `unknown`) quebraria as duas amarras e seria, ele
mesmo, um filtro local de produto — o que a EP-REG-06a proíbe: tirar a marcação na fonte é o
único jeito de tirar um produto da vitrine. Por isso não há aqui lista, conjunto ou constante de
produto: as cercas leem só campos do próprio wire.

O fato presencial não é lista de produtos. É um ato único do dono (referência do ato), sem
nenhum `optionRef`; nenhum código em `src/` constrói um. Abrir a vitrine é ato do dono e
parecer do jurídico, nunca flag nem variável de ambiente.

Forma da recusa: o par congelado `denied/purpose_denied`, o mesmo da espera do coletivo; a rota
pública renderiza toda recusa com o próprio corpo fixo (503), então nenhum detalhe sai do
processo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Final, Literal

from maezo.ports.commercial import (
    CommercialCodec,
    CommercialCommand,
    CommercialRefusalError,
    CommercialStatus,
    OperationalResult,
)

__all__ = [
    "PUBLIC_FENCE_REFUSAL",
    "PresentialChannelFact",
    "PublicOfferFenceCodec",
    "public_offer_fence_violations",
]

#: Par de recusa das cercas; os dois valores pertencem ao vocabulário congelado do C6-4.
PUBLIC_FENCE_REFUSAL: Final[tuple[Literal["denied"], Literal["purpose_denied"]]] = (
    "denied",
    "purpose_denied",
)

_UNKNOWN: Final = "unknown"
_KNOWN: Final = "known"
_USABLE: Final = "usable"
_REGISTRY_CODES: Final = ("productRegistryCode", "operatorRegistryCode")
_ACT_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


@dataclass(frozen=True, slots=True, kw_only=True)
class PresentialChannelFact:
    """Ato do dono que comprova a oferta presencial dos planos vendidos on-line.

    Só a referência do ato, sem produto: o fato vale para a vitrine, não para uma lista de
    produtos (que seria filtro local). Nenhum código de produção constrói este valor hoje.
    """

    owner_act_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.owner_act_ref, str) or not _ACT_REF.fullmatch(self.owner_act_ref):
            raise ValueError("presential_channel_fact_invalid")


def _data_state(value: object) -> str:
    if not isinstance(value, dict):
        raise ValueError("public_fence_catalog_unreadable")
    state = value.get("dataState")
    if state not in (_UNKNOWN, _KNOWN):
        raise ValueError("public_fence_catalog_unreadable")
    return str(state)


def _registry_known(registry: object) -> bool:
    if _data_state(registry) != _KNOWN or not isinstance(registry, dict):
        return False
    return all(isinstance(registry.get(code), str) and registry.get(code) for code in _REGISTRY_CODES)


def public_offer_fence_violations(catalog: object) -> tuple[str, ...]:
    """Códigos das cercas violadas por um catálogo já validado pelo wire.

    Leitura pura. Qualquer coisa fora da forma validada conta como violação (fail-closed),
    nunca como resposta vazia.
    """
    if not isinstance(catalog, dict) or not isinstance(catalog.get("products"), list):
        raise ValueError("public_fence_catalog_unreadable")
    found: set[str] = set()
    for product in catalog["products"]:
        if not isinstance(product, dict):
            raise ValueError("public_fence_catalog_unreadable")
        offer_state = product.get("offerState")
        if not isinstance(offer_state, str):
            raise ValueError("public_fence_catalog_unreadable")
        if offer_state == _USABLE and not _registry_known(product.get("ansRegistry")):
            found.add("ans_registry_unknown")
        if _data_state(product.get("price")) == _KNOWN:
            found.add("public_price_numeric")
    return tuple(sorted(found))


class PublicOfferFenceCodec(CommercialCodec):
    """Codec do catálogo público com as cercas da 1ª perna.

    O codec interno valida o wire primeiro; depois as cercas recusam, antes da divulgação, o
    catálogo inteiro. Todo outro método delega sem mudança, então o caminho de comando de
    vendas fica idêntico ao entregue.
    """

    def __init__(self, inner: CommercialCodec, *, presential_fact: PresentialChannelFact | None) -> None:
        if not isinstance(inner, CommercialCodec):
            raise ValueError("commercial_composition_invalid")
        if presential_fact is not None and not isinstance(presential_fact, PresentialChannelFact):
            raise ValueError("commercial_composition_invalid")
        self._inner = inner
        self._presential_fact = presential_fact

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
        if self._presential_fact is None:
            raise CommercialRefusalError(*PUBLIC_FENCE_REFUSAL)
        try:
            violations = public_offer_fence_violations(json.loads(disclosed))
        except ValueError:
            raise CommercialRefusalError(*PUBLIC_FENCE_REFUSAL) from None
        if violations:
            raise CommercialRefusalError(*PUBLIC_FENCE_REFUSAL)
        return disclosed
