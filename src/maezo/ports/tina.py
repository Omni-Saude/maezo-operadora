"""`TinaPort` — fatos do plano do beneficiario (vinculos, carencias, requisicoes) pelo contrato TINA da AMH.

ADR-0037: o Maezo NAO le o lago nem tabela crua; consome so' projecao canonica da AMH. Este port e' o
lado do Maezo do contrato `tina` (`schemas/openapi/maezo/v1/tina.openapi.yaml` em
`Omni-Saude/amh-data-platform`, manifest aditivo v1.2, publicado em 13/10/2026) e da rota de dependentes
(`tina-dependentes.openapi.yaml`, manifest aditivo v1.5, publicado em 13/10/2026), da carteirinha completa
(`tina-carteirinha`, v1.12) e da cobertura por procedimento (`tina-cobertura`, v1.9). Os campos abaixo
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


@dataclass(frozen=True, slots=True)
class CarenciaDoItem:
    """Uma carencia do BENEFICIARIO que se aplica a um procedimento (contrato `tina-cobertura`). Data em
    `AAAA-MM-DD`. E' o que consta no cadastro, nunca promessa de cobertura."""

    cumprida: bool | None
    dias: int | None
    termina_em: str | None


@dataclass(frozen=True, slots=True)
class CoberturaItemView:
    """Um procedimento que casou com o termo/codigo pedido, com os FATOS do cadastro do plano.

    `situacao` e' o enum fechado do contrato (`coberto_rol`, `coberto_extra`, `fora_da_segmentacao`,
    `fora_do_rol`, `indeterminado`). `coberto` e' "o cadastro do plano PREVE cobertura", nunca autorizacao
    nem promessa de pagamento; `None` (ex.: `indeterminado`) quer dizer que a fonte NAO sabe. Sem codigo
    nem origem: o consumidor fala da descricao, e o termo digitado nunca volta na resposta.
    """

    descricao: str | None
    situacao: str
    coberto: bool | None
    no_rol: bool | None
    na_segmentacao: bool | None
    rol_edicao: str | None
    tem_diretriz: bool  # `diretriz_de_utilizacao is True` no contrato (DUT no Rol)
    carencias: tuple[CarenciaDoItem, ...]


@dataclass(frozen=True, slots=True)
class CoberturaView:
    """Os fatos de cobertura do plano do proprio sujeito (contrato `tina-cobertura`, manifest v1.9).

    `itens` vazio = nenhum procedimento casou, o que NAO quer dizer "nao cobre". `truncada` = casaram mais
    do que os devolvidos (o consumidor pede um termo mais especifico, nunca escolhe um). `avisos` sao os
    codigos fechados do contrato que o consumidor traduz em ressalva no texto."""

    portable_subject_ref: str
    segmentacao: str | None
    regulamentado: bool | None
    itens: tuple[CoberturaItemView, ...]
    truncada: bool
    avisos: tuple[str, ...]
    fonte_atualizada_em: str | None


#: Teto da lista de procedimentos no contrato (`maxItems` e `limite` maximo).
MAX_COBERTURA_ITENS: int = 10


@runtime_checkable
class TinaCoberturaPort(Protocol):
    """Leitura read-only dos fatos de cobertura de um procedimento (contrato `tina-cobertura`). Sucesso ou
    recusa, nunca excecao. Port separado: quem so' implementa os fatos do plano nao muda.

    `procedimento` e' o codigo (so' digitos) ou o termo (letras, numeros e espaco) no padrao do contrato;
    nunca vai a log. `limite` vai de 1 a `MAX_COBERTURA_ITENS`."""

    async def get_cobertura(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        procedimento: str,
        limite: int = 5,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CoberturaView]: ...


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
    "MAX_COBERTURA_ITENS",
    "MAX_DEPENDENTES",
    "CarenciaDoItem",
    "CarenciaPlano",
    "CarteirinhaCompleta",
    "CarteirinhasView",
    "CarenciasView",
    "CoberturaItemView",
    "CoberturaView",
    "DependenteAnonimo",
    "DependentesView",
    "ElegibilidadeView",
    "RequisicaoPlano",
    "RequisicoesView",
    "TinaCarteirinhaPort",
    "TinaCoberturaPort",
    "TinaDependentesPort",
    "TinaPort",
    "VinculoPlano",
]
