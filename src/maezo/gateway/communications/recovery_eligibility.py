"""Elegibilidade de recuperacao de abandono (E-SF-16 / GTM-E-51): quem PODE receber um lembrete, e quando.

Politica pura, sem envio. So' acrescenta recusas; voltar `Elegivel` nao autoriza nada: o despacho re-checa no
envio e o `OutboundGate` continua decidindo (permissao, supressao, modelo).

Assinatura FECHADA (so' estes argumentos, nomeados): tipo de titular, etapa da jornada, se ha' aceite
registrado e o estado do relogio-sombra canonico. Nenhuma idade, faixa, plano, valor ou dado de saude
entra aqui.

Regras (nesta ordem):
  * tipo de titular: so' lead e prospect, por tipo exato. Beneficiario nunca recebe recuperacao.
  * relogio: so' o estado do relogio canonico (`exigir_relogio_canonico`); outro relogio e' recusado.
  * etapa: classe fechada de cinco. Desconhecida (inclusive sem leitor da fonte) recusa.
  * aceite registrado, "aceite sem pagamento" e "contrato emitido pendente de declaracao de saude": nunca.
    A etapa pendente de declaracao de saude fica fora por construcao.
  * sem evento de inicio no relogio: recusa (ancora nao resolvida; nunca inventada).
  * relogio com a recuperacao bloqueada (a partir do dia em que o relogio-sombra a bloqueia): recusa.

Nada montado: nenhum despachante, worker ou raiz de composicao importa este modulo.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from maezo.domain.rn413_contracting_clock import exigir_relogio_canonico

__all__ = [
    "ETAPAS",
    "ETAPAS_CANDIDATAS",
    "ETAPAS_NUNCA",
    "TITULARES_DA_RECUPERACAO",
    "Elegivel",
    "MotivoRecuperacao",
    "RecuperacaoRecusada",
    "avaliar_elegibilidade",
]

#: Etapas em que um lembrete PODE ser considerado (sempre dentro do relogio-sombra).
ETAPAS_CANDIDATAS: Final[frozenset[str]] = frozenset(
    {"cotacao_sem_decisao", "proposta_incompleta", "termos_sem_aceite"}
)
#: Etapas em que a recuperacao NUNCA ocorre.
ETAPAS_NUNCA: Final[frozenset[str]] = frozenset(
    {"aceite_sem_pagamento", "contrato_pendente_declaracao_de_saude"}
)
ETAPAS: Final[frozenset[str]] = ETAPAS_CANDIDATAS | ETAPAS_NUNCA

#: Lead e prospect, iguais. Beneficiario nunca.
TITULARES_DA_RECUPERACAO: Final[frozenset[str]] = frozenset({"lead", "prospect"})


class MotivoRecuperacao(StrEnum):
    RECOVERY_SUBJECT_KIND_NOT_ALLOWED = "RECOVERY_SUBJECT_KIND_NOT_ALLOWED"
    RECOVERY_STAGE_UNKNOWN = "RECOVERY_STAGE_UNKNOWN"
    RECOVERY_AFTER_ACCEPTANCE = "RECOVERY_AFTER_ACCEPTANCE"
    CLOCK_ANCHOR_UNRESOLVED = "CLOCK_ANCHOR_UNRESOLVED"
    RECOVERY_CLOCK_BLOCKED = "RECOVERY_CLOCK_BLOCKED"


class RecuperacaoRecusada(PermissionError):  # noqa: N818
    """Recusa tipada; a mensagem e' so' o codigo, nunca o valor recebido."""

    def __init__(self, motivo: MotivoRecuperacao) -> None:
        self.motivo = motivo
        super().__init__(motivo.value)


@dataclass(frozen=True, slots=True)
class Elegivel:
    """Elegivel AGORA. Nao e' autorizacao de envio."""

    proponente_digest: str
    etapa: str


def avaliar_elegibilidade(
    *, tipo_titular: str, etapa: str, aceite_registrado: bool, relogio: object
) -> Elegivel:
    """Devolve `Elegivel` ou levanta `RecuperacaoRecusada` (`RelogioRecusado` para outro relogio)."""
    if type(tipo_titular) is not str or tipo_titular not in TITULARES_DA_RECUPERACAO:
        raise RecuperacaoRecusada(MotivoRecuperacao.RECOVERY_SUBJECT_KIND_NOT_ALLOWED)
    estado = exigir_relogio_canonico(relogio)
    if type(etapa) is not str or etapa not in ETAPAS:
        raise RecuperacaoRecusada(MotivoRecuperacao.RECOVERY_STAGE_UNKNOWN)
    # Qualquer coisa que nao seja exatamente False conta como aceite registrado (desconhecido nunca libera).
    if aceite_registrado is not False or etapa in ETAPAS_NUNCA:
        raise RecuperacaoRecusada(MotivoRecuperacao.RECOVERY_AFTER_ACCEPTANCE)
    if estado.inicio is None:
        raise RecuperacaoRecusada(MotivoRecuperacao.CLOCK_ANCHOR_UNRESOLVED)
    if estado.recuperacao_bloqueada:
        raise RecuperacaoRecusada(MotivoRecuperacao.RECOVERY_CLOCK_BLOCKED)
    return Elegivel(proponente_digest=estado.proponente_digest, etapa=etapa)
