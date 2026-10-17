"""`TinaPort` — fatos do plano do beneficiario (vinculos, carencias, requisicoes) pelo contrato TINA da AMH.

ADR-0037: o Maezo NAO le o lago nem tabela crua; consome so' projecao canonica da AMH. Este port e' o
lado do Maezo do contrato `tina` (`schemas/openapi/maezo/v1/tina.openapi.yaml` em
`Omni-Saude/amh-data-platform`, manifest aditivo v1.2, publicado em 13/10/2026) e da rota de dependentes
(`tina-dependentes.openapi.yaml`, manifest aditivo v1.5, publicado em 13/10/2026). Os campos abaixo
espelham o TIPO do contrato, nao o schema: o schema e' da AMH (proibicao imutavel #4) e o adaptador so'
o usa depois de conferir o digest contra o pin.

CAMPO AUSENTE E' ESTADO VALIDO (`None`). Quem consome nunca completa um fato ausente.

Sem identificador cru: o sujeito e' o `portable_subject_ref` opaco; carteirinha e senha de autorizacao
vem SO' mascaradas. Carencia e' o que consta no cadastro, nunca promessa de cobertura.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    """Uma solicitacao de autorizacao. A senha vem SO' mascarada. Sem nome de terceiro: o medico
    solicitante saiu do contrato (revisao do #690)."""

    solicitacao: int | None
    solicitada_em: str | None
    status: str | None
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


@dataclass(frozen=True, slots=True)
class DependenteAnonimo:
    """Um dependente ATIVO do titular, anonimo: so' a relacao, a situacao e se e' recem-nascido no plano.

    Sem nome, CPF, referencia nem nascimento: o contrato nao os carrega e este tipo nao tem onde guardalos.
    """

    relacao_dependencia: str | None  # ex.: "Filho(a) até 23 anos"
    situacao_vinculo: str | None  # `situacao_beneficiario` no contrato; ex.: "Apto"
    rn_no_plano: bool | None  # `recem_nascido_no_plano` no contrato (nomes PHI-shaped nao entram nos ports)


@dataclass(frozen=True, slots=True)
class DependentesView:
    """Dependentes do titular. `total_ativos` e' exato (pessoas distintas com vinculo ativo); `dependentes`
    traz no maximo `MAX_DEPENDENTES` (20) itens e `lista_truncada` avisa quando ha' mais do que isso.

    Quem nao e' titular recebe `eh_titular=False`, `total_ativos=0` e lista vazia.
    """

    portable_subject_ref: str
    eh_titular: bool
    total_ativos: int
    lista_truncada: bool
    dependentes: tuple[DependenteAnonimo, ...]
    fonte_atualizada_em: str


#: Teto da lista de dependentes no contrato (`maxItems`).
MAX_DEPENDENTES: int = 20


@dataclass(frozen=True, slots=True)
class CarteirinhaCompleta:
    """O numero COMPLETO da carteirinha de UM vinculo ATIVO do proprio sujeito (contrato `tina-carteirinha`,
    manifest v1.12; decisao do dono de 17/10/2026, so' dev).

    O numero e' IDENTIFICADOR DE USO do plano: fora do `repr` (nunca vai a log por acidente) e o consumidor
    so' o entrega por TEXTO FIXO ao proprio beneficiario verificado. `carteirinha_mascarada` e' a MESMA
    mascara da rota `elegibilidade`: e' por ela que o consumidor casa o numero com o vinculo de la'.
    """

    carteirinha_numero: str = field(repr=False)
    carteirinha_mascarada: str
    vinculo_principal: bool


@dataclass(frozen=True, slots=True)
class CarteirinhasView:
    """As carteirinhas dos vinculos ATIVOS do proprio sujeito (1..10). Sem vinculo ativo, a rota responde
    404."""

    portable_subject_ref: str
    carteirinhas: tuple[CarteirinhaCompleta, ...]
    fonte_atualizada_em: str


#: Teto da lista de carteirinhas no contrato (`maxItems`).
MAX_CARTEIRINHAS: int = 10


@runtime_checkable
class TinaCarteirinhaPort(Protocol):
    """Leitura read-only do numero completo da carteirinha (contrato `tina-carteirinha`). Sucesso ou recusa,
    nunca excecao. Port separado: quem so' implementa os fatos do plano nao muda.

    `request_ref` e' o `X-Amh-Request-Ref`: identificador OPACO da solicitacao (auditoria da AMH), nunca CPF,
    telefone ou nome."""

    async def get_carteirinha(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        request_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CarteirinhasView]: ...


@runtime_checkable
class TinaDependentesPort(Protocol):
    """Leitura read-only dos dependentes do titular (contrato `tina-dependentes`). Sucesso ou recusa, nunca
    excecao. Port separado do `TinaPort` de proposito: quem so' implementa os fatos do plano nao muda."""

    async def get_dependentes(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[DependentesView]: ...


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
    "MAX_CARTEIRINHAS",
    "MAX_DEPENDENTES",
    "CarenciaPlano",
    "CarteirinhaCompleta",
    "CarteirinhasView",
    "CarenciasView",
    "DependenteAnonimo",
    "DependentesView",
    "ElegibilidadeView",
    "RequisicaoPlano",
    "RequisicoesView",
    "TinaCarteirinhaPort",
    "TinaDependentesPort",
    "TinaPort",
    "VinculoPlano",
]
