"""Rota do despachante para o agente `comercial` (GTM-E-12b): so' a DECISAO de rota, fechada.

Politica pura, sem efeito. Nao envia, nao chama sender, nao carrega grafo, nao le ambiente nem
settings, nao registra log. Devolve, quando (e se) a rota um dia estiver ligada, o id do agente e a
acao de autonomia PRETENDIDA; quem executa e' o despachante, que nao monta este modulo.

Chave: `ROTA_COMERCIAL_LIGADA` e' constante `False`. Nao ha' leitor de ambiente, de settings ou de
argumento que a ligue: ligar e' edicao de codigo revisada, em linha propria.

Assinatura FECHADA (so' estes argumentos, nomeados): tipo de titular, se a sessao foi iniciada pelo
proprio titular e se a mensagem esta' dentro da janela de atendimento aberta por ele. Nenhuma idade,
faixa, plano, valor, regiao ou dado de saude entra aqui.

Regras (nesta ordem):
  * chave desligada: recusa, para qualquer entrada.
  * guarda do fluxo de acesso ausente: recusa. Com o acesso de beneficiario ligado, o despachante leva
    numero fora do cadastro ao fluxo de consentimento e identificacao do beneficiario; isso nao serve
    a prospect. Enquanto o despachante nao tiver a guarda que tira o prospect desse fluxo,
    `GUARDA_DE_PROSPECT_NO_ACESSO` fica `False` e a rota nao liga, mesmo com a chave `True`.
  * tipo de titular: so' `prospect`, por tipo exato. Beneficiario, lead e desconhecido recusam;
    desconhecido nunca vira prospect.
  * sessao: so' a iniciada pelo proprio prospect (exatamente `True`). Nada proativo.
  * janela: so' dentro da janela de atendimento aberta pelo prospect (exatamente `True`).

Nada montado: nenhum modulo de `src` importa este.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "ACAO_PRETENDIDA",
    "AGENTE_ID",
    "GUARDA_DE_PROSPECT_NO_ACESSO",
    "ROTA_COMERCIAL_LIGADA",
    "TITULARES_DA_ROTA",
    "DecisaoDeRota",
    "MotivoRota",
    "avaliar_rota",
]

#: Desligada. Sem leitor que a ligue.
ROTA_COMERCIAL_LIGADA: Final[bool] = False

#: Bloqueio da ligacao futura: so' vira `True` na linha que der ao despachante a guarda que tira o
#: prospect do fluxo de acesso do beneficiario. Hoje essa guarda nao existe.
GUARDA_DE_PROSPECT_NO_ACESSO: Final[bool] = False

#: O grafo de destino, so' por id (a plataforma nao importa o agente).
AGENTE_ID: Final[str] = "comercial"

#: A acao de autonomia que o envio ao prospect exerceria. So' devolvida; nunca executada aqui.
ACAO_PRETENDIDA: Final[str] = "send_prospect_message"

#: So' prospect. Beneficiario segue com a entrada atual; lead e desconhecido nao entram.
TITULARES_DA_ROTA: Final[frozenset[str]] = frozenset({"prospect"})


class MotivoRota(StrEnum):
    ROUTE_SWITCH_OFF = "ROUTE_SWITCH_OFF"
    ROUTE_ACCESS_FLOW_GUARD_MISSING = "ROUTE_ACCESS_FLOW_GUARD_MISSING"
    ROUTE_SUBJECT_KIND_NOT_ALLOWED = "ROUTE_SUBJECT_KIND_NOT_ALLOWED"
    ROUTE_SESSION_NOT_SUBJECT_INITIATED = "ROUTE_SESSION_NOT_SUBJECT_INITIATED"
    ROUTE_OUTSIDE_INBOUND_WINDOW = "ROUTE_OUTSIDE_INBOUND_WINDOW"


@dataclass(frozen=True, slots=True)
class DecisaoDeRota:
    """Resultado da decisao. `permitido` nao e' autorizacao de envio: o chokepoint decide depois."""

    permitido: bool
    motivo: MotivoRota | None
    agente_id: str | None
    acao_pretendida: str | None


def _recusa(motivo: MotivoRota) -> DecisaoDeRota:
    return DecisaoDeRota(permitido=False, motivo=motivo, agente_id=None, acao_pretendida=None)


def avaliar_rota(
    *, tipo_titular: object, sessao_iniciada_pelo_titular: object, dentro_da_janela_de_entrada: object
) -> DecisaoDeRota:
    """Decide se a mensagem iria ao agente `comercial`. Hoje sempre recusa (`ROUTE_SWITCH_OFF`)."""
    if ROTA_COMERCIAL_LIGADA is not True:
        return _recusa(MotivoRota.ROUTE_SWITCH_OFF)
    if GUARDA_DE_PROSPECT_NO_ACESSO is not True:
        return _recusa(MotivoRota.ROUTE_ACCESS_FLOW_GUARD_MISSING)
    if type(tipo_titular) is not str or tipo_titular not in TITULARES_DA_ROTA:
        return _recusa(MotivoRota.ROUTE_SUBJECT_KIND_NOT_ALLOWED)
    if sessao_iniciada_pelo_titular is not True:
        return _recusa(MotivoRota.ROUTE_SESSION_NOT_SUBJECT_INITIATED)
    if dentro_da_janela_de_entrada is not True:
        return _recusa(MotivoRota.ROUTE_OUTSIDE_INBOUND_WINDOW)
    return DecisaoDeRota(permitido=True, motivo=None, agente_id=AGENTE_ID, acao_pretendida=ACAO_PRETENDIDA)
