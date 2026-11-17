"""Contestação de reajuste como solicitação NÃO assistencial (GTM-E-63).

Módulo puro, sem I/O próprio. Quando o titular contesta um reajuste, o protocolo
é a primeira ação e o caso é humano. Nunca passa pelo processo de escalação
clínica: reajuste não é solicitação assistencial.

Base normativa (somente referência, sem cópia de texto), no arquivo
``docs/compliance/legal/textos-base/RN-623-2024.md``:

- art. 10: número de protocolo como primeira ação;
- art. 11: solicitações não assistenciais;
- art. 2º, II: reajuste é solicitação não assistencial.

Limites deliberados:

- O prazo do art. 11 NÃO é calculado aqui. Não há linha no catálogo de prazos
  (``spec/processes/dmn/funnel_deadline_catalog.dmn``) e regra de prazo não nasce
  em Python: fica GAP. Nenhum prazo é exibido ao cliente.
- O protocolo vem somente da fonte de custódia ("nunca minta protocolo"); nunca é
  gerado aqui. Sem protocolo, nenhum texto é produzido.
- Nada é montado: nenhum receptor importa este módulo.
- O agente nunca decide a contestação; a decisão é humana e opaca para este módulo.
- Não há data da notificação de reajuste nem janela: a contestação nunca é
  recusada por janela ou timer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol

PROCESS_KEYS: Final[tuple[str, ...]] = ()  # nenhum processo BPMN

# PROVISÓRIO: redação a validar com o jurídico. Sem prazo, sem "em análise",
# sem dispositivo normativo.
TEXTO_AO_CLIENTE: Final[str] = (
    "Recebemos sua contestação do reajuste. Seu protocolo é {protocolo}. "
    "Uma pessoa da nossa equipe vai cuidar do seu pedido e responder por este canal."
)

_PROTOCOLO_MAX: Final = 64
_REF_MAX: Final = 256


class ContestacaoReajusteError(ValueError):
    """Falha técnica estática; nunca ecoa valor recebido."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _ascii_token(value: object, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and all(0x21 <= ord(c) <= 0x7E for c in value)


@dataclass(frozen=True, slots=True)
class ProtocoloRegistrado:
    protocolo_ref: str
    registrado_em: datetime

    def __post_init__(self) -> None:
        if not _ascii_token(self.protocolo_ref, _PROTOCOLO_MAX):
            raise ContestacaoReajusteError("ERR_PROTOCOLO_AUSENTE")
        if not isinstance(self.registrado_em, datetime) or self.registrado_em.tzinfo is None:
            raise ContestacaoReajusteError("ERR_PROTOCOLO_AUSENTE")
        if self.registrado_em.utcoffset() is None:
            raise ContestacaoReajusteError("ERR_PROTOCOLO_AUSENTE")


class FonteDeProtocolo(Protocol):
    # Nenhuma implementação existe (GAP: custódia de protocolo de suporte da fonte).
    async def registrar(
        self, *, tenant_ref: str, titular_ref: str, fila_ref: str
    ) -> ProtocoloRegistrado | None: ...


@dataclass(frozen=True, slots=True)
class ContestacaoAberta:
    protocolo_ref: str
    registrado_em: datetime
    fila_ref: str
    prazo_interno: None  # GAP: prazo do art. 11 sem linha no catálogo; nunca calculado aqui
    texto_ao_cliente: str


async def abrir_contestacao_reajuste(
    fonte: FonteDeProtocolo, *, tenant_ref: str, titular_ref: str, fila_ref: str
) -> ContestacaoAberta:
    for ref in (tenant_ref, titular_ref, fila_ref):
        if not _ascii_token(ref, _REF_MAX):
            raise ContestacaoReajusteError("ERR_CONTESTACAO_ENTRADA_INVALIDA")
    try:
        registro = await fonte.registrar(tenant_ref=tenant_ref, titular_ref=titular_ref, fila_ref=fila_ref)
    except Exception:
        raise ContestacaoReajusteError("ERR_PROTOCOLO_AUSENTE") from None
    if not isinstance(registro, ProtocoloRegistrado):
        raise ContestacaoReajusteError("ERR_PROTOCOLO_AUSENTE")
    if not _ascii_token(registro.protocolo_ref, _PROTOCOLO_MAX) or (
        not isinstance(registro.registrado_em, datetime) or registro.registrado_em.utcoffset() is None
    ):
        raise ContestacaoReajusteError("ERR_PROTOCOLO_AUSENTE")
    return ContestacaoAberta(
        protocolo_ref=registro.protocolo_ref,
        registrado_em=registro.registrado_em,
        fila_ref=fila_ref,
        prazo_interno=None,
        texto_ao_cliente=TEXTO_AO_CLIENTE.format(protocolo=registro.protocolo_ref),
    )


@dataclass(frozen=True, slots=True)
class DecisaoDaContestacao:
    protocolo_ref: str
    decisor_ref: str
    decisao_ref: str


def registrar_decisao(
    caso: ContestacaoAberta, *, decisor_tipo: str, decisor_ref: str, decisao_ref: str
) -> DecisaoDaContestacao:
    if not isinstance(decisor_tipo, str) or decisor_tipo != "humano":
        raise ContestacaoReajusteError("ERR_DECISAO_CONTESTACAO_HUMANA")
    if not _ascii_token(decisor_ref, _REF_MAX) or not _ascii_token(decisao_ref, _REF_MAX):
        raise ContestacaoReajusteError("ERR_CONTESTACAO_ENTRADA_INVALIDA")
    return DecisaoDaContestacao(
        protocolo_ref=caso.protocolo_ref, decisor_ref=decisor_ref, decisao_ref=decisao_ref
    )
