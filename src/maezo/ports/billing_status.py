"""`BillingStatusPort` — situacao de cobranca de um beneficiario, lida por contrato publicado pela AMH.

ADR-0037: o Maezo NAO le o lago nem tabela crua; consome so' projecao canonica da AMH. Este port e'
o lado do Maezo do contrato `billing-status` (proposto em `Omni-Saude/amh-data-platform`, branch
`proposta/maezo-cobranca-e-identidade-v1`, AINDA NAO PUBLICADO). Os campos abaixo espelham o TIPO do
contrato, nao o schema: o schema e' da AMH (proibicao imutavel #4) e o adaptador so' o usa depois de
conferir o digest contra o pin.

CAMPO AUSENTE E' ESTADO VALIDO. Todo fato que a fonte nao tem vem `None` e o nome entra em
`campos_ausentes`. Quem consome nunca completa um fato ausente: o Lucas escala a humano.

Sem identificador cru: o sujeito e' o `portable_subject_ref` opaco; o boleto e' so' o rotulo mascarado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortResult


@dataclass(frozen=True, slots=True)
class CompetenciaBilling:
    """Uma competencia da janela. Valores sao decimais em texto (`"320.25"`), nunca float."""

    competencia: str  # AAAA-MM
    parcela: int | None
    vencimento: str | None  # AAAA-MM-DD
    situacao: str  # paga | em_aberto | vencida | cancelada | sem_titulo
    valor_total: str | None
    valor_coparticipacao: str | None
    valor_saldo: str | None
    liquidado_em: str | None
    boleto_numero_mascarado: str | None  # ****1234
    boleto_disponivel_online: bool | None


@dataclass(frozen=True, slots=True)
class BillingSummary:
    status_conciliado: bool | None
    ciclos_sem_conciliacao: int | None
    valor_em_aberto: str | None
    dias_atraso_max: int | None
    pagador_tipo: str  # pessoa_fisica | pessoa_juridica | desconhecido
    #: Como `status_conciliado` foi obtido: `situacao_paga_ou_liquidada` ou `retorno_bancario`.
    criterio_conciliacao: str


@dataclass(frozen=True, slots=True)
class BillingStatusView:
    portable_subject_ref: str
    as_of: str
    #: Ultima atualizacao da fonte. Quem consome decide se uma fonte velha demais vale.
    fonte_atualizada_em: str
    resumo: BillingSummary
    competencias: tuple[CompetenciaBilling, ...]
    campos_ausentes: frozenset[str]


@runtime_checkable
class BillingStatusPort(Protocol):
    """Leitura read-only da situacao de cobranca. Sucesso ou recusa, nunca excecao de politica."""

    async def get_billing_status(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        competencia: str | None = None,
        janela_meses: int = 12,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[BillingStatusView]: ...
