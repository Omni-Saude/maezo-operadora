"""Fila NÃO assistencial "Relacionamento e renovação" e o pedido de rascunho do copiloto (E-SF-RT-03).

Módulo puro, sem I/O próprio e NÃO montado: nenhum despachante, worker, rota, composição nem o
cancelamento a pedido o importa. A fila é humana: a pessoa do papel "Relacionamento e renovação"
atende, decide e responde. O copiloto só PREPARA um rascunho para essa pessoa.

O que existe aqui:

- ``abrir_item_de_relacionamento``: abre o item da fila com o protocolo como primeira ação. O
  protocolo vem SOMENTE da fonte de custódia (``FonteDeProtocolo``, no mesmo formato da do
  GTM-E-63, sem importá-la: aquele módulo é cercado contra importadores); nunca é gerado aqui. Sem
  protocolo, nada é aberto.
- ``pedir_rascunho``: devolve, como DADO, a ação de autonomia que o rascunho exerceria
  (``retention_staff_assist_draft``, L1 em ``spec/policies/autonomy/L0-core.yaml``: o agente só
  prepara, o humano revisa e decide). Quem avalia a ação é o PEP do gateway (L1 = REQUIRE_HUMAN),
  na unidade que um dia montar o copiloto; este módulo não avalia nem executa nada.

Limites deliberados:

- PRAZO: o vencimento é ``None``. O prazo de 7 dias úteis do plano não tem linha no catálogo de
  prazos (``spec/processes/dmn/funnel_deadline_catalog.dmn``) e regra de prazo não nasce em Python:
  fica GAP, sem número aqui. Nenhum prazo é exibido.
- CAMPOS: os fatos que um rascunho poderia usar passam por uma lista FECHADA,
  ``CAMPOS_DO_RASCUNHO_RETENCAO``, hoje VAZIA (cédula E36: campos com default vazio, sem CPT, sem
  inadimplência, só sintético; RT-D3 do DPO sem resposta). O recorte usa
  ``runtime.prompt_format.render_fatos_para_prompt(permitidos=..., agente=...)``: nenhum fato chega
  a um modelo, e texto livre de terceiro não é campo da lista.
- SEM MODELO: nenhum LLM, nenhum provedor, nenhum texto gerado. ``texto`` do rascunho é sempre vazio.
- SEM OFERTA: nenhuma oferta, desconto, prazo, promessa ou condição comercial é produzida aqui.
  Nenhum texto deste módulo vai ao beneficiário nem ao Lucas.
- SEM DECISÃO: nada aqui decide valor, elegibilidade, rescisão, contestação ou cancelamento. O
  cancelamento a pedido segue sem fricção e sem passar por aqui (``platform/saida_a_pedido``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol

from maezo.runtime.prompt_format import render_fatos_para_prompt

__all__ = [
    "ACAO_RASCUNHO_RETENCAO",
    "AGENTE_DO_RECORTE",
    "CAMPOS_DO_RASCUNHO_RETENCAO",
    "FILA_RELACIONAMENTO",
    "FonteDeProtocolo",
    "ItemDeRelacionamento",
    "PedidoDeRascunho",
    "ProtocoloRegistrado",
    "RetencaoRelacionamentoError",
    "abrir_item_de_relacionamento",
    "pedir_rascunho",
]

PROCESS_KEYS: Final[tuple[str, ...]] = ()  # nenhum processo BPMN

#: A fila humana não assistencial (papel "Relacionamento e renovação").
FILA_RELACIONAMENTO: Final[str] = "relacionamento_renovacao"

#: A ação de autonomia do rascunho (L1, sem parâmetros, sem tool, não `hard`; #1349).
ACAO_RASCUNHO_RETENCAO: Final[str] = "retention_staff_assist_draft"

#: Lista FECHADA dos fatos que podem chegar a um rascunho. VAZIA por default (cédula E36).
CAMPOS_DO_RASCUNHO_RETENCAO: Final[frozenset[str]] = frozenset()

#: Rótulo do recorte de fatos (vocabulário fechado do contador: fora dele, conta como `outro`).
AGENTE_DO_RECORTE: Final[str] = "retencao_relacionamento"

_PROTOCOLO_MAX: Final = 64
_REF_MAX: Final = 256


class RetencaoRelacionamentoError(ValueError):
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
            raise RetencaoRelacionamentoError("ERR_PROTOCOLO_AUSENTE")
        if not isinstance(self.registrado_em, datetime) or self.registrado_em.utcoffset() is None:
            raise RetencaoRelacionamentoError("ERR_PROTOCOLO_AUSENTE")


class FonteDeProtocolo(Protocol):
    # Nenhuma implementação existe (GAP: custódia de protocolo de suporte da fonte).
    async def registrar(
        self, *, tenant_ref: str, titular_ref: str, fila_ref: str
    ) -> ProtocoloRegistrado | None: ...


@dataclass(frozen=True, slots=True)
class ItemDeRelacionamento:
    protocolo_ref: str
    registrado_em: datetime
    fila_ref: str
    vencimento: None  # GAP: prazo sem linha no catálogo; nunca calculado aqui


@dataclass(frozen=True, slots=True)
class PedidoDeRascunho:
    """O que a fila pediria ao copiloto: a ação L1 como dado e o recorte fechado dos fatos."""

    protocolo_ref: str
    acao: str
    fatos: str
    texto: str  # sempre vazio: nenhum texto gerado


async def abrir_item_de_relacionamento(
    fonte: FonteDeProtocolo, *, tenant_ref: str, titular_ref: str
) -> ItemDeRelacionamento:
    """Protocolo como primeira ação, vindo da custódia; sem protocolo, nada é aberto."""
    for ref in (tenant_ref, titular_ref):
        if not _ascii_token(ref, _REF_MAX):
            raise RetencaoRelacionamentoError("ERR_RELACIONAMENTO_ENTRADA_INVALIDA")
    try:
        registro = await fonte.registrar(
            tenant_ref=tenant_ref, titular_ref=titular_ref, fila_ref=FILA_RELACIONAMENTO
        )
    except Exception:
        raise RetencaoRelacionamentoError("ERR_PROTOCOLO_AUSENTE") from None
    if not isinstance(registro, ProtocoloRegistrado):
        raise RetencaoRelacionamentoError("ERR_PROTOCOLO_AUSENTE")
    if not _ascii_token(registro.protocolo_ref, _PROTOCOLO_MAX) or (
        not isinstance(registro.registrado_em, datetime) or registro.registrado_em.utcoffset() is None
    ):
        raise RetencaoRelacionamentoError("ERR_PROTOCOLO_AUSENTE")
    return ItemDeRelacionamento(
        protocolo_ref=registro.protocolo_ref,
        registrado_em=registro.registrado_em,
        fila_ref=FILA_RELACIONAMENTO,
        vencimento=None,
    )


def pedir_rascunho(item: ItemDeRelacionamento, fatos: Mapping[str, Any]) -> PedidoDeRascunho:
    """Monta o pedido de rascunho: a ação L1 como dado e só os fatos da lista fechada (hoje nenhum).

    Não chama modelo, não gera texto, não envia nada e não avalia a ação: isso é da unidade que um
    dia montar o copiloto, pelo PEP do gateway (L1 = REQUIRE_HUMAN).
    """
    if not isinstance(item, ItemDeRelacionamento):
        raise RetencaoRelacionamentoError("ERR_RELACIONAMENTO_ENTRADA_INVALIDA")
    if not isinstance(fatos, Mapping):
        raise RetencaoRelacionamentoError("ERR_RELACIONAMENTO_ENTRADA_INVALIDA")
    recorte = render_fatos_para_prompt(
        fatos, booleanos={}, permitidos=CAMPOS_DO_RASCUNHO_RETENCAO, agente=AGENTE_DO_RECORTE
    )
    return PedidoDeRascunho(
        protocolo_ref=item.protocolo_ref, acao=ACAO_RASCUNHO_RETENCAO, fatos=recorte, texto=""
    )
