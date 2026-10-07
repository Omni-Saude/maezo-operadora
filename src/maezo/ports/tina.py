"""`TinaPort` — fatos do plano do beneficiario (vinculos, carencias, requisicoes) pelo contrato TINA da AMH.

ADR-0037: o Maezo NAO le o lago nem tabela crua; consome so' projecao canonica da AMH. Este port e' o
lado do Maezo do contrato `tina` (`schemas/openapi/maezo/v1/tina.openapi.yaml` em
`Omni-Saude/amh-data-platform`, manifest aditivo v1.2 — AINDA DRAFT em 07/10/2026). Os campos abaixo
espelham o TIPO do contrato, nao o schema: o schema e' da AMH (proibicao imutavel #4) e o adaptador so'
o usa depois de conferir o digest contra o pin.

CAMPO AUSENTE E' ESTADO VALIDO (`None`). Quem consome nunca completa um fato ausente.

Sem identificador cru: o sujeito e' o `portable_subject_ref` opaco; carteirinha e senha de autorizacao
vem SO' mascaradas. Carencia e' o que consta no cadastro, nunca promessa de cobertura.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortResult


@dataclass(frozen=True, slots=True)
class VinculoPlano:
    """Um vinculo do beneficiario com o plano. Datas em `AAAA-MM-DD`."""

    carteirinha_mascarada: str | None
    plano: str | None
    registro_ans_plano: str | None
    segmentacao: str | None
    acomodacao: str | None
    tipo_contratacao: str | None
    vigencia_inicio: str | None
    cancelamento: str | None
    atendimento_liberado: bool | None
    situacao_vinculo: str | None  # `situacao_beneficiario` no contrato
    situacao_contrato: str | None
    titular: bool | None
    relacao_dependencia: str | None
    titular_ref: str | None


@dataclass(frozen=True, slots=True)
class ElegibilidadeView:
    portable_subject_ref: str
    ativo: bool
    vinculos: tuple[VinculoPlano, ...]
    fonte_atualizada_em: str
    campos_ausentes: frozenset[str]


@dataclass(frozen=True, slots=True)
class CarenciaPlano:
    """`carencia` e' a descricao do cadastro do Tasy, sem interpretacao."""

    carencia: str | None
    inicio: str | None
    dias: int | None
    validade: str | None
    cumprida: bool | None


@dataclass(frozen=True, slots=True)
class CarenciasView:
    portable_subject_ref: str
    pendentes: int
    carencias: tuple[CarenciaPlano, ...]
    fonte_atualizada_em: str


@dataclass(frozen=True, slots=True)
class RequisicaoPlano:
    """Uma solicitacao de autorizacao. A senha vem SO' mascarada; o medico solicitante e' do contrato,
    mas quem consome decide se o expoe (a Helena nao o expoe)."""

    solicitacao: int | None
    solicitada_em: str | None
    status: str | None
    medico_solicitante: str | None
    senha_mascarada: str | None
    senha_validade: str | None
    senha_vigente: bool | None
    sla_dias: int | None
    liberacao_prevista: str | None


@dataclass(frozen=True, slots=True)
class RequisicoesView:
    portable_subject_ref: str
    requisicoes: tuple[RequisicaoPlano, ...]
    fonte_atualizada_em: str


@runtime_checkable
class TinaPort(Protocol):
    """Leituras read-only das funcoes de LEITURA do bot TINA. Sucesso ou recusa, nunca excecao."""

    async def get_elegibilidade(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[ElegibilidadeView]: ...

    async def get_carencias(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        limite: int = 20,
        pendentes: bool | None = None,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CarenciasView]: ...

    async def get_requisicoes(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        limite: int = 20,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[RequisicoesView]: ...


__all__ = [
    "CarenciaPlano",
    "CarenciasView",
    "ElegibilidadeView",
    "RequisicaoPlano",
    "RequisicoesView",
    "TinaPort",
    "VinculoPlano",
]
