"""A porta das consultas do plano na AMH (contrato TINA), em comum entre a Helena e o adaptador da plataforma
(`platform/webhooks/whatsapp/helena_consultas.py`) — ADR-0067.

A Helena CONSOME (`agents/helena/consultas_plano.py`, que continua exportando os mesmos nomes); a plataforma
IMPLEMENTA. Nenhum dos dois importa o outro.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from maezo.ports.tina import (
    CarenciasView,
    CarteirinhasView,
    CoberturaView,
    CptVigenteView,
    DependentesNominaisView,
    DependentesView,
    ElegibilidadeView,
    RequisicoesView,
)

#: Quantas requisicoes/carencias vao ao texto. O agente conversa, nao exporta (contrato: 1..50).
LIMITE_REQUISICOES: Final[int] = 5
LIMITE_CARENCIAS: Final[int] = 20


@dataclass(frozen=True, slots=True)
class FonteIndisponivel:
    """A fonte NAO esta' carregada neste ambiente (o artefato da rota nao foi configurado): diferente de uma
    falha da consulta. So' `dependentes` a usa — com ela o texto provisorio da rodada 10 continua valendo."""

    subtipo: str


@runtime_checkable
class FonteDeFatosDoPlano(Protocol):
    """Consulta a AMH para UM sujeito e UM subtipo. Nunca levanta; falha = `None`."""

    async def consultar(
        self, portable_subject_ref: str, subtipo: str
    ) -> ElegibilidadeView | CarenciasView | RequisicoesView | DependentesView | FonteIndisponivel | None: ...


@runtime_checkable
class FonteDeCarteirinhaCompleta(Protocol):
    """A fonte que TAMBEM le' o numero completo da carteirinha (rota `tina-carteirinha`, manifest v1.12).

    Protocolo SEPARADO: a fonte de sempre nao muda, e o grafo so' pede o numero quando a fonte injetada o
    implementa E a chave `MAEZO_HELENA_CARTEIRINHA_COMPLETA` esta' ligada. Nunca levanta; falha = `None`
    (o grafo cai no mascarado). `request_ref` e' o id OPACO da solicitacao (`X-Amh-Request-Ref`)."""

    async def consultar_carteirinha(
        self, portable_subject_ref: str, request_ref: str
    ) -> CarteirinhasView | None: ...


@runtime_checkable
class FonteDeNomesDosDependentes(Protocol):
    """A fonte que TAMBEM le' os NOMES dos dependentes do proprio titular (rota `tina-dependentes-nome`,
    manifest v1.13; decisao do dono e DPO de 19/10/2026, DL-0127).

    Protocolo SEPARADO: a fonte de sempre nao muda, e o grafo so' pede os nomes quando a fonte injetada o
    implementa E a chave `MAEZO_HELENA_DEPENDENTES_NOME` esta' ligada. Nunca levanta; falha = `None` (o grafo
    cai em "dependente N"). `request_ref` e' o id OPACO da solicitacao (`X-Amh-Request-Ref`)."""

    async def consultar_dependentes_nominais(
        self, portable_subject_ref: str, request_ref: str
    ) -> DependentesNominaisView | None: ...


@runtime_checkable
class FonteDeCobertura(Protocol):
    """A fonte que TAMBEM le' os fatos de cobertura de um procedimento (rota `tina-cobertura`, manifest v1.9;
    decisao do dono D1 de 18/10/2026).

    Protocolo SEPARADO: a fonte de sempre nao muda. Nunca levanta; falha = `None`. `procedimento` e' o
    codigo ou o termo no padrao do contrato e nunca vai a log."""

    async def consultar_cobertura(
        self, portable_subject_ref: str, procedimento: str
    ) -> CoberturaView | None: ...


@runtime_checkable
class FonteDeCpt(Protocol):
    """A fonte que TAMBEM le' a CPT vigente do proprio sujeito (rota `tina-cpt`, manifest v1.15; caso C1 do
    dono e DPO de 20/10/2026, DL-0130).

    Protocolo SEPARADO: a fonte de sempre nao muda, e o grafo so' pede a CPT na cobertura assistida (allowlist
    da saude gerativa), para o TITULAR. Nunca levanta; falha = `None` (nenhum aviso de CPT)."""

    async def consultar_cpt(self, portable_subject_ref: str) -> CptVigenteView | None: ...
