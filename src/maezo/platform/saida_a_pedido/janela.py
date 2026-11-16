"""Avaliacao da janela de arrependimento (EP-REG-03a). Nunca recusa: todo resultado e' `route_to_human`.

A duracao vem da politica (YAML); nao ha literal de prazo aqui. Ancora so' vale se vier de fonte
(receipt autenticado); ancora do navegador e' recusada na construcao.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from maezo.platform.saida_a_pedido.politica import Ancoragem, NomeAncora, PoliticaJanela

OrigemReceipt = Literal["fonte", "sessao_autenticada", "navegador"]
SituacaoJanela = Literal["DENTRO", "FORA", "INDETERMINADA"]


class AncoraNaoAutenticada(ValueError):  # noqa: N818 - nome fixado pelo desenho
    code: Final = "anchor_unauthenticated"


@dataclass(frozen=True, slots=True)
class AncoraDeFonte:
    nome: NomeAncora
    instante: datetime  # timezone-aware obrigatorio
    receipt_ref: str  # nao vazio
    origem: OrigemReceipt

    def __post_init__(self) -> None:
        if self.origem != "fonte":
            raise AncoraNaoAutenticada(f"{self.nome}: origem {self.origem!r} nao e' fonte")
        if not self.receipt_ref.strip():
            raise AncoraNaoAutenticada(f"{self.nome}: receipt_ref vazio")
        if self.instante.tzinfo is None or self.instante.utcoffset() is None:
            raise AncoraNaoAutenticada(f"{self.nome}: instante sem fuso")


@dataclass(frozen=True, slots=True)
class JanelaAvaliada:
    situacao: SituacaoJanela
    variante: str
    termos_finais: Mapping[str, datetime]  # por ancora usada
    regimes_abertos: frozenset[str]  # dispositivo_ref das janelas abertas
    encaminhamento: Literal["route_to_human"]  # sempre; nao ha outro valor no tipo


def avaliar_janela(
    politica: PoliticaJanela, ancoras: Sequence[AncoraDeFonte], pedido_em: datetime
) -> JanelaAvaliada:
    if pedido_em.tzinfo is None or pedido_em.utcoffset() is None:
        raise ValueError("pedido_em deve ter fuso")
    por_nome = {a.nome: a for a in ancoras}
    termos: dict[str, datetime] = {}
    abertos: set[str] = set()
    faltando = False
    inicios: list[datetime] = []
    for ancoragem in politica.ancoras_da_variante:
        presente = por_nome.get(ancoragem.nome)
        if presente is None:
            faltando = True
            continue
        fim = presente.instante + ancoragem.duracao
        termos[ancoragem.nome] = fim
        inicios.append(presente.instante)
        if _dentro(presente.instante, fim, pedido_em):
            abertos.add(ancoragem.dispositivo_ref)

    dentro = bool(abertos)
    if politica.combinacao == "assinatura_ate_ultimo_termo_final" and termos and not dentro:
        # uniao contigua: da primeira ancora ate o ultimo termo final (cobre o intervalo entre os dois)
        dentro = _dentro(min(inicios), max(termos.values()), pedido_em)
    if dentro:
        situacao: SituacaoJanela = "DENTRO"
    elif faltando or not termos:
        situacao = "INDETERMINADA"  # nunca FORA com ancora exigida faltando
    else:
        situacao = "FORA"
    return JanelaAvaliada(
        situacao=situacao,
        variante=politica.variante_ativa,
        termos_finais=termos,
        regimes_abertos=frozenset(abertos),
        encaminhamento="route_to_human",
    )


def _dentro(inicio: datetime, fim: datetime, instante: datetime) -> bool:
    # Contagem (corrida/util, inclusiva) = Q3; inofensivo porque nada se recusa por prazo.
    return inicio <= instante <= fim


__all__ = ["Ancoragem", "AncoraDeFonte", "AncoraNaoAutenticada", "JanelaAvaliada", "avaliar_janela"]
