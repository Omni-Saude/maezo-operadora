"""Rescisao por arrependimento (TB-E-17-a / EP-REG-19a): intake validado, protocolo e encaminhamento humano.

Nenhuma funcao daqui produz desfecho: tudo sai `route_to_human`. Nao ha recusa por prazo/janela.
`contract_termination` e' item hard-frozen (L0): o unico ponto de recusa da efetivacao por agente e'
`recusar_efetivacao_por_agente`. Contrato: `SP-OP-ARREPENDIMENTO-001`.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, NoReturn, cast

from maezo.gateway.pep import PEP, Decision
from maezo.platform.saida_a_pedido.janela import (
    AncoraDeFonte,
    OrigemReceipt,
    SituacaoJanela,
    avaliar_janela,
)
from maezo.platform.saida_a_pedido.politica import PoliticaInvalida, PoliticaJanela, carregar_politica_janela

CODIGOS_DE_RECUSA_DO_FORMULARIO: Final[frozenset[str]] = frozenset(
    {
        "declaration_required",  # ausente, ou renderizada marcada
        "DECLARATION_FUSED_WITH_CLICK",  # ato == clique de abertura ou == envio
        "CANCEL_CONFIRMATION_NOT_WITHDRAWAL_DECLARATION",  # "Sim" do CANCEL-001
        "anchor_unauthenticated",  # ancora no payload
        "unknown_field",  # chave fora do conjunto fechado (inclui proxies de saude)
    }
)
# Invariante testado: nenhum codigo de recusa fala de prazo/janela.

CHAVES_DO_PAYLOAD: Final[frozenset[str]] = frozenset(
    {"pedido_ref", "contrato_ref", "abertura_ref", "envio_ref", "assinatura_ref", "declaracao"}
)
CHAVES_DA_DECLARACAO: Final[frozenset[str]] = frozenset(
    {"tipo_do_ato", "ato_ref", "estado_inicial_renderizado", "marcada_em", "texto_digest"}
)
_CHAVES_DE_ANCORA: Final[frozenset[str]] = frozenset(
    {"signed_at", "vigencia_start", "assinado_em", "vigencia", "data_vigencia", "data_assinatura"}
)

_SEMENTE_PROTOCOLO: Final[str] = "maezo-arrependimento-protocolo/v1"
ACAO_AUTONOMIA_DA_EFETIVACAO: Final[str] = "contract_termination"


class PedidoRecusado(ValueError):  # noqa: N818 - nome fixado pelo desenho
    code: str

    def __init__(self, code: str, detalhe: str = "") -> None:
        if code not in CODIGOS_DE_RECUSA_DO_FORMULARIO:
            raise ValueError(f"codigo de recusa fora do conjunto fechado: {code}")
        super().__init__(f"{code}: {detalhe}" if detalhe else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DeclaracaoDestacada:
    tipo_do_ato: Literal["declaracao_art8_par1", "confirmacao_cancelamento"]
    ato_ref: str
    estado_inicial_renderizado: Literal["desmarcada", "marcada"]
    marcada_em: datetime  # aware
    texto_digest: str
    origem: OrigemReceipt  # "sessao_autenticada" ou "fonte"; "navegador" -> declaration_required


@dataclass(frozen=True, slots=True)
class PedidoArrependimento:
    pedido_ref: str  # opaco, nao vazio
    contrato_ref: str  # opaco; nunca numero de contrato/matricula crus
    abertura_ref: str  # id do clique que abriu o formulario
    envio_ref: str  # id do envio
    assinatura_ref: str  # forma do art. 7º = Q3 (c); aqui so' nao vazio
    declaracao: DeclaracaoDestacada | None


@dataclass(frozen=True, slots=True)
class Recebimento:
    protocolo: str
    recebido_em: datetime
    situacao_janela: SituacaoJanela
    regimes_abertos: frozenset[str]
    texto_declaracao_ratificado: bool
    encaminhamento: Literal["route_to_human"]
    desfecho: None = None  # o teste afirma que o modulo nunca o preenche


def _obrigatorio(payload: Mapping[str, object], chave: str) -> str:
    valor = payload.get(chave)
    if not isinstance(valor, str) or not valor.strip():
        raise ValueError(f"campo obrigatorio ausente ou vazio: {chave}")
    return valor


def _declaracao_de_payload(bruta: object) -> DeclaracaoDestacada | None:
    if bruta is None:
        return None
    if not isinstance(bruta, Mapping):
        raise PedidoRecusado("declaration_required", "declaracao nao e' mapping")
    if _CHAVES_DE_ANCORA & set(bruta):
        raise PedidoRecusado("anchor_unauthenticated", "ancora na declaracao")
    if set(bruta) - CHAVES_DA_DECLARACAO:
        raise PedidoRecusado("unknown_field", "declaracao")
    tipo = _obrigatorio(bruta, "tipo_do_ato")
    estado = _obrigatorio(bruta, "estado_inicial_renderizado")
    if tipo not in ("declaracao_art8_par1", "confirmacao_cancelamento"):
        raise ValueError(f"tipo_do_ato invalido: {tipo}")
    if estado not in ("desmarcada", "marcada"):
        raise ValueError(f"estado_inicial_renderizado invalido: {estado}")
    marcada = bruta.get("marcada_em")
    instante = datetime.fromisoformat(marcada) if isinstance(marcada, str) else marcada
    if not isinstance(instante, datetime) or instante.tzinfo is None:
        raise ValueError("marcada_em deve ser instante com fuso")
    return DeclaracaoDestacada(
        tipo_do_ato=cast("Literal['declaracao_art8_par1', 'confirmacao_cancelamento']", tipo),
        ato_ref=_obrigatorio(bruta, "ato_ref"),
        estado_inicial_renderizado=cast("Literal['desmarcada', 'marcada']", estado),
        marcada_em=instante,
        texto_digest=str(bruta.get("texto_digest") or ""),
        # a borda autenticada garante a sessao; o modulo nao aceita `origem` do payload
        origem="sessao_autenticada",
    )


def pedido_de_payload(payload: Mapping[str, object]) -> PedidoArrependimento:
    # ancora no payload vem ANTES da checagem de chave desconhecida
    if _CHAVES_DE_ANCORA & set(payload):
        raise PedidoRecusado("anchor_unauthenticated", "ancora no payload")
    if set(payload) - CHAVES_DO_PAYLOAD:
        raise PedidoRecusado("unknown_field", "payload")
    return PedidoArrependimento(
        pedido_ref=_obrigatorio(payload, "pedido_ref"),
        contrato_ref=_obrigatorio(payload, "contrato_ref"),
        abertura_ref=_obrigatorio(payload, "abertura_ref"),
        envio_ref=_obrigatorio(payload, "envio_ref"),
        assinatura_ref=_obrigatorio(payload, "assinatura_ref"),
        declaracao=_declaracao_de_payload(payload.get("declaracao")),
    )


def validar_declaracao(pedido: PedidoArrependimento, *, politica: PoliticaJanela | None = None) -> None:
    declaracao = pedido.declaracao
    if declaracao is None:
        raise PedidoRecusado("declaration_required", "ausente")
    if declaracao.tipo_do_ato == "confirmacao_cancelamento":
        raise PedidoRecusado("CANCEL_CONFIRMATION_NOT_WITHDRAWAL_DECLARATION")
    if declaracao.estado_inicial_renderizado != "desmarcada" or declaracao.origem == "navegador":
        raise PedidoRecusado("declaration_required", "pre-marcada ou sem origem autenticada")
    if declaracao.ato_ref in {pedido.abertura_ref, pedido.envio_ref}:
        raise PedidoRecusado("DECLARATION_FUSED_WITH_CLICK")
    if not declaracao.texto_digest.strip():
        raise PedidoRecusado("declaration_required", "texto_digest vazio")
    if (
        politica is not None
        and politica.declaracao_texto_ratificado
        and politica.declaracao_texto_digest is not None
        and politica.declaracao_texto_digest != declaracao.texto_digest
    ):
        raise PedidoRecusado("declaration_required", "texto_digest diferente do ratificado")


def protocolo_de_arrependimento(pedido_ref: str) -> str:
    """`ARR-XXXX-XXXX`, estavel para o mesmo `pedido_ref` e sem PII (hash do id opaco)."""
    if not isinstance(pedido_ref, str) or not pedido_ref.strip():
        raise ValueError("protocolo_de_arrependimento: pedido_ref ausente")
    digest = hashlib.sha256(f"{_SEMENTE_PROTOCOLO}:{pedido_ref.strip()}".encode()).hexdigest().upper()
    return f"ARR-{digest[:4]}-{digest[4:8]}"


def receber_pedido(
    pedido: PedidoArrependimento,
    *,
    ancoras: Sequence[AncoraDeFonte],
    recebido_em: datetime,
    politica: PoliticaJanela | None = None,
) -> Recebimento:
    """Valida a declaracao, avalia a janela (sem recusar) e devolve o recebimento `route_to_human`.

    `recebido_em` vem do chamador (relogio de servidor), nunca do payload (GAP-09-4)."""
    if politica is None:
        try:
            politica = carregar_politica_janela()
        except PoliticaInvalida:
            politica = None  # fail-closed: a janela fica INDETERMINADA, sem recusar
    validar_declaracao(pedido, politica=politica)
    protocolo = protocolo_de_arrependimento(pedido.pedido_ref)
    if politica is None:
        situacao: SituacaoJanela = "INDETERMINADA"
        regimes: frozenset[str] = frozenset()
    else:
        avaliada = avaliar_janela(politica, ancoras, recebido_em)
        situacao, regimes = avaliada.situacao, avaliada.regimes_abertos
    return Recebimento(
        protocolo=protocolo,
        recebido_em=recebido_em,
        situacao_janela=situacao,
        regimes_abertos=regimes,
        texto_declaracao_ratificado=politica.declaracao_texto_ratificado if politica is not None else False,
        encaminhamento="route_to_human",
    )


class EfetivacaoAutonomaRecusada(PermissionError):  # noqa: N818 - nome fixado pelo desenho
    code: str

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def recusar_efetivacao_por_agente(pep: PEP, *, agent_id: str, nivel: str) -> NoReturn:
    """SEMPRE levanta. Unico ponto que uma ferramenta futura de "efetivar arrependimento" deve chamar."""
    decisao = pep.evaluate(ACAO_AUTONOMIA_DA_EFETIVACAO, {"agent_id": agent_id, "level": nivel})
    if decisao is Decision.DENY:
        raise EfetivacaoAutonomaRecusada("CONTRACT_TERMINATION_L0_HARD")
    if decisao is Decision.REQUIRE_HUMAN:
        raise EfetivacaoAutonomaRecusada("CONTRACT_TERMINATION_REQUIRES_HUMAN")
    raise EfetivacaoAutonomaRecusada("PEP_INCONSISTENTE")
