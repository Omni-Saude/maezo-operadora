"""Estado do estorno (EP-REG-19a item 4): `ESTORNO_CONCLUIDO` so' com os dois receipts de fonte.

Fail-closed. Sem valores monetarios (o estorno nao decide dinheiro). O fio C6-4 atual nao leva o ack Tasy
do estorno; por isso o estado nunca chega a "concluido" pelo fio (campo novo = sucessor do C6-4, AMH).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

EstadoFonteFinanceira = Literal["requested", "pending", "completed", "refused", "uncertain"]
EstadoEstorno = Literal[
    "ESTORNO_NAO_SOLICITADO", "ESTORNO_PENDENTE", "ESTORNO_CONCLUIDO", "ESTORNO_EM_ANALISE_HUMANA"
]


@dataclass(frozen=True, slots=True)
class ReceiptDeFonte:
    ref: str
    tipo: Literal["restituicao", "tasy_ack"]
    origem: Literal["fonte", "webhook_nao_autenticado"]


def _de_fonte(receipts: Sequence[ReceiptDeFonte], tipo: str) -> list[str]:
    return [r.ref.strip() for r in receipts if r.tipo == tipo and r.origem == "fonte" and r.ref.strip()]


def estado_do_estorno(
    *, estado_fonte: EstadoFonteFinanceira | None, receipts: Sequence[ReceiptDeFonte]
) -> EstadoEstorno:
    if estado_fonte is None:
        return "ESTORNO_NAO_SOLICITADO"
    if estado_fonte in ("refused", "uncertain"):
        return "ESTORNO_EM_ANALISE_HUMANA"
    if estado_fonte == "completed":
        restituicoes = _de_fonte(receipts, "restituicao")
        acks = _de_fonte(receipts, "tasy_ack")
        if restituicoes and acks and set(restituicoes).isdisjoint(acks):
            return "ESTORNO_CONCLUIDO"
    return "ESTORNO_PENDENTE"


def receipts_do_fio_c6_4(financial_action: Mapping[str, object]) -> tuple[ReceiptDeFonte, ...]:
    """Le a projecao `FinancialAction` atual: `source_receipt_ref` nao nulo vira receipt `restituicao`.

    NUNCA produz `tasy_ack`: o fio nao tem esse campo (GAP-09-8)."""
    ref = financial_action.get("source_receipt_ref")
    if isinstance(ref, str) and ref.strip():
        return (ReceiptDeFonte(ref=ref.strip(), tipo="restituicao", origem="fonte"),)
    return ()
