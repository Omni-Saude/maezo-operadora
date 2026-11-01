"""Contrato tipado e versionado da projecao terminal CRED/FRAUDE -> delta do ledger (MPTT P3).

**O que e'.** Uma tabela FECHADA que diz, para cada evento final de nivel de processo de
SP-OP-CRED-001 e SP-OP-FRAUDE-001, qual fato o ledger `prestador_status_ledger` registra. Nada
alem disso: sem limiar, sem pontuacao, sem janela de tempo, sem escolha entre alternativas.

**Por que e' projecao (enum fechado) e NAO uma DMN.** AGENTS.md regra 5 manda regra de negocio
DETERMINISTICA para DMN. Esta tabela nao decide nada: cada linha re-rotula um terminal que o
proprio contrato do processo ja define semanticamente — a decisao (credenciar, descredenciar,
acusar fraude, monitorar, arquivar) foi tomada ANTES, por um humano numa User Task do processo
(ADR-0018), e o terminal e' o registro dela. As fontes de cada linha:

* `SP-OP-CRED-001.md` §"Terminais humano-gated": `End_PrestadorCredenciado` = credenciamento
  aprovado; `End_PrestadorDescredenciado` e `End_SubstituicaoRegistrada` = descredenciado
  ("Ambos os terminais sao ADVERSOS (o prestador foi descredenciado nos dois)", linha
  `tem_plano_substituicao`); `End_VinculoMantido` = descredenciamento nao prosseguiu;
  `End_CredenciamentoNegado` = candidato nao entrou na rede.
* `SP-OP-FRAUDE-001.md` §"Terminais humano-gated": `End_FraudeConfirmadaHumano` e os tres
  referrals a jusante dela = acusacao humana; `End_MonitorarSemAcao` = monitorar;
  `End_ArquivadoSemIndicio` = arquivar.

Os terminais de guarda (erro de prestador invalido, descredenciamento/negativa barrados por
falta de decisao humana) nao mudam status: `SEM_DELTA`. O erro de custodia do FRAUDE
(`End_CustodiaNaoSelada`) encerra o caso SEM decisao humana: registra `ENCERRADA_SEM_DECISAO`
(fato, nao veredito — o consumidor P6 o trata como caso nao resolvido). O alerta de SLA
(`End_RiscoSlaNotificado`) nao encerra instancia (ramo de borda nao-interruptivo): se a costura o
devolver como evento final, a leitura e' recusada (`NAO_TERMINAL` -> INDISPONIVEL).

Instancia que NAO terminou por evento final (cancelada — `EXTERNALLY_TERMINATED`/
`INTERNALLY_TERMINATED`) nao produziu terminal: `SEM_DELTA`. Instancia ativa/suspensa: o
reconciliador devolve `EM_CURSO` sem gravar — "descredenciamento em curso" e "fraude aberta" sao
leituras vivas do consumidor (P6), nunca linhas deste ledger.

**L0 hard.** Nada aqui acusa fraude nem descredencia: o ledger registra o desfecho que um humano
JA produziu no processo. `fraud_accusation`/`provider_decredentialing` continuam so' na UT.

**Versao.** `VERSAO_PROJECAO` vai em cada linha gravada. Mudar qualquer linha desta tabela exige
subir a versao (o teste de censo amarra a tabela aos eventos finais dos dois BPMN).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

VERSAO_PROJECAO: Final = "mptt-p3-projecao/v1"

PROCESSO_CRED: Final = "SP-OP-CRED-001"
PROCESSO_FRAUDE: Final = "SP-OP-FRAUDE-001"


class Origem(StrEnum):
    """Quem produziu a linha do ledger (CHECK `origem` da migracao 0032)."""

    BASELINE_AMH = "BASELINE_AMH"
    CRED = "CRED"
    FRAUDE = "FRAUDE"


class Dimensao(StrEnum):
    """Qual fato a linha registra (CHECK `dimensao`)."""

    SITUACAO = "situacao"
    FRAUDE = "fraude"


class ValorSituacao(StrEnum):
    """Situacao legal GRAVAVEL. `DESCONHECIDO`/`INDISPONIVEL` sao estados de leitura (ver
    `ledger.SituacaoLida`), nunca gravados. `SUSPENSO` nao existe: CRED nao tem decisao de
    suspensao (analise §1) — nao ha fonte."""

    CREDENCIADO = "CREDENCIADO"
    DESCREDENCIADO = "DESCREDENCIADO"


class ValorFraude(StrEnum):
    """Desfecho GRAVAVEL de um caso FRAUDE que referencia o prestador. A janela de 12 meses do
    monitoramento (analise §2/§4) e' regra do consumidor (DMN do P5/P6), nao deste ledger."""

    CONFIRMADA = "CONFIRMADA"
    MONITORAR = "MONITORAR"
    ARQUIVADA = "ARQUIVADA"
    ENCERRADA_SEM_DECISAO = "ENCERRADA_SEM_DECISAO"


class ClasseTerminal(StrEnum):
    DELTA = "DELTA"
    SEM_DELTA = "SEM_DELTA"
    NAO_TERMINAL = "NAO_TERMINAL"


@dataclass(frozen=True, slots=True)
class Projecao:
    """O que um evento final projeta. `dimensao`/`valor` so' existem quando `classe == DELTA`."""

    classe: ClasseTerminal
    dimensao: Dimensao | None = None
    valor: str | None = None

    def __post_init__(self) -> None:
        tem_delta = self.dimensao is not None and self.valor is not None
        if (self.classe is ClasseTerminal.DELTA) != tem_delta:
            raise ValueError("Projecao: DELTA exige dimensao+valor; as outras classes, nenhum")


def _delta(dimensao: Dimensao, valor: StrEnum) -> Projecao:
    return Projecao(ClasseTerminal.DELTA, dimensao, str(valor))


_SEM_DELTA: Final = Projecao(ClasseTerminal.SEM_DELTA)
_NAO_TERMINAL: Final = Projecao(ClasseTerminal.NAO_TERMINAL)

#: SP-OP-CRED-001 — TODOS os `endEvent` do BPMN, classificados (censo no teste unitario).
PROJECAO_CRED: Final[Mapping[str, Projecao]] = MappingProxyType(
    {
        "End_PrestadorCredenciado": _delta(Dimensao.SITUACAO, ValorSituacao.CREDENCIADO),
        "End_PrestadorDescredenciado": _delta(Dimensao.SITUACAO, ValorSituacao.DESCREDENCIADO),
        "End_SubstituicaoRegistrada": _delta(Dimensao.SITUACAO, ValorSituacao.DESCREDENCIADO),
        "End_VinculoMantido": _SEM_DELTA,
        "End_CredenciamentoNegado": _SEM_DELTA,
        "End_CredPrestadorInvalido": _SEM_DELTA,
        "End_DecredBloqueadoNaoHumano": _SEM_DELTA,
        "End_CredGuardBloqueadoNaoHumano": _SEM_DELTA,
        "End_RiscoSlaNotificado": _NAO_TERMINAL,
    }
)

#: SP-OP-FRAUDE-001 — TODOS os `endEvent` do BPMN, classificados (censo no teste unitario).
#: So' se aplica quando o caso referencia um prestador (`entidade_tipo == 'prestador'`).
PROJECAO_FRAUDE: Final[Mapping[str, Projecao]] = MappingProxyType(
    {
        "End_FraudeConfirmadaHumano": _delta(Dimensao.FRAUDE, ValorFraude.CONFIRMADA),
        "End_EncaminhadoCredenciamento": _delta(Dimensao.FRAUDE, ValorFraude.CONFIRMADA),
        "End_EncaminhadoJuridico": _delta(Dimensao.FRAUDE, ValorFraude.CONFIRMADA),
        "End_EncaminhadoContratual": _delta(Dimensao.FRAUDE, ValorFraude.CONFIRMADA),
        "End_MonitorarSemAcao": _delta(Dimensao.FRAUDE, ValorFraude.MONITORAR),
        "End_ArquivadoSemIndicio": _delta(Dimensao.FRAUDE, ValorFraude.ARQUIVADA),
        "End_CustodiaNaoSelada": _delta(Dimensao.FRAUDE, ValorFraude.ENCERRADA_SEM_DECISAO),
        "End_RiscoSlaNotificado": _NAO_TERMINAL,
    }
)

PROJECOES: Final[Mapping[Origem, Mapping[str, Projecao]]] = MappingProxyType(
    {Origem.CRED: PROJECAO_CRED, Origem.FRAUDE: PROJECAO_FRAUDE}
)

#: Estados do motor em que a instancia acabou SEM evento final (cancelada).
ESTADOS_ENCERRADOS_SEM_TERMINAL: Final = frozenset({"EXTERNALLY_TERMINATED", "INTERNALLY_TERMINATED"})
#: Estados do motor de instancia viva.
ESTADOS_EM_CURSO: Final = frozenset({"ACTIVE", "SUSPENDED"})
ESTADO_CONCLUIDO: Final = "COMPLETED"


def projetar(origem: Origem, end_activity_id: str) -> Projecao | None:
    """A projecao de um evento final, ou `None` se o evento nao consta do contrato v1 (o
    reconciliador trata `None` como leitura recusada — nunca adivinha)."""
    return PROJECOES[origem].get(end_activity_id)


__all__ = [
    "ESTADOS_EM_CURSO",
    "ESTADOS_ENCERRADOS_SEM_TERMINAL",
    "ESTADO_CONCLUIDO",
    "PROCESSO_CRED",
    "PROCESSO_FRAUDE",
    "PROJECAO_CRED",
    "PROJECAO_FRAUDE",
    "VERSAO_PROJECAO",
    "ClasseTerminal",
    "Dimensao",
    "Origem",
    "Projecao",
    "ValorFraude",
    "ValorSituacao",
    "projetar",
]
