"""Fatos do plano na Helena: a fonte que consulta a AMH pelo contrato TINA (DL de 07/10/2026).

Decisao do dono de 07/10/2026: com a identidade do beneficiario resolvida (DL-0077), a Helena responde
fatos do PROPRIO plano — vinculos/carteirinha, carencias e requisicoes de autorizacao — vindos do lago
pelas rotas TINA do servico interop da AMH (`ports/tina.py`, `adapters/amh/tina.py`). DESLIGADA por
padrao (`MAEZO_HELENA_CONSULTAS_AMH`); ligada, exige a identidade ligada e o interop configurado, e o
adaptador TINA so' sobe com o manifest aditivo v1.2 pinado — sem isso o receptor RECUSA servir.

O QUE E'. A implementacao de `agents/helena/consultas_plano.py::FonteDeFatosDoPlano`: dado o
`portable_subject_ref` (que o grafo so' tem com candidato UNICO) e o subtipo, chama UMA leitura do port
com o proposito e a base legal fixa de execucao de contrato (a MESMA da identidade; NAO e'
consentimento). Devolve a view do contrato ou `None`. NUNCA levanta e nunca passa de `PRAZO_TOTAL_S`.

LOG. So' o subtipo, o motivo (token fechado) e booleanos. Nunca a referencia, os fatos nem o telefone —
este modulo nem recebe o telefone ou o wamid.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

import structlog

from maezo.agents.helena.consultas_plano import LIMITE_CARENCIAS, LIMITE_REQUISICOES, FonteIndisponivel
from maezo.agents.lucas.fonte_cobranca_amh import FonteDeConsentimento
from maezo.ports.errors import PortFailureReason, PortResult
from maezo.ports.tina import (
    CarenciasView,
    DependentesView,
    ElegibilidadeView,
    RequisicoesView,
    TinaDependentesPort,
    TinaPort,
)

logger = structlog.get_logger(__name__)

#: Teto de uma consulta: a base legal e ate DUAS leituras em sequencia (cada uma limitada a 5 s pelo port). Duas porque
#: "estou em carencia?" sem linha de carencia (`not_found`) confirma o sujeito pela elegibilidade (`_sem_carencias`).
#: Eram 6 s: com cada consulta Athena da AMH levando de 1 a 5 s, o roteiro `dono_v1` reprovou 1 em 3 rodadas
#: (16/10/2026, `prazo_estourado` aos 6 s exatos) mesmo com as duas leituras respondendo.
PRAZO_TOTAL_S: Final[float] = 12.0

_View = ElegibilidadeView | CarenciasView | RequisicoesView | DependentesView | FonteIndisponivel


@dataclass
class ConsultasPlanoAmh:
    """Construida UMA vez no boot do receptor, so' com `helena_consultas_amh` ligada (`service.py`)."""

    port: TinaPort
    #: Base legal fixa de execucao de contrato (decisao do dono; NAO e' consentimento).
    consentimento: FonteDeConsentimento
    purpose_of_use: str
    #: Drenagem dos executores do gateway (a composicao e' a da identidade; fecha uma vez so').
    aclose_fn: Callable[[], Awaitable[None]] | None = field(default=None, repr=False)

    async def _ler(self, ref: str, subtipo: str, base_legal: str) -> PortResult[Any] | None:
        proposito = self.purpose_of_use
        if subtipo in ("elegibilidade", "carteirinha"):
            return await self.port.get_elegibilidade(
                ref, purpose_of_use=proposito, consent_decision_ref=base_legal
            )
        if subtipo == "carencia":
            return await self.port.get_carencias(
                ref, purpose_of_use=proposito, consent_decision_ref=base_legal, limite=LIMITE_CARENCIAS
            )
        if subtipo == "autorizacao":
            return await self.port.get_requisicoes(
                ref, purpose_of_use=proposito, consent_decision_ref=base_legal, limite=LIMITE_REQUISICOES
            )
        if subtipo == "dependentes":
            # O mesmo adaptador TINA implementa o port de dependentes; um port sem ele = sem a fonte.
            if not isinstance(self.port, TinaDependentesPort):
                return PortResult.refused(PortFailureReason.SCOPE_NOT_SUPPORTED)
            return await self.port.get_dependentes(
                ref, purpose_of_use=proposito, consent_decision_ref=base_legal
            )
        return None

    async def _sem_carencias(self, ref: str, base_legal: str) -> CarenciasView | None:
        """RODADA 8 (teste real do dono, 13/10/2026): `not_found` em carencias = a pessoa NAO TEM linhas de
        carencia. O dono perguntou "Estou em carencia?" e leu "ainda nao estao disponiveis por este chat",
        com a elegibilidade da MESMA pessoa funcionando um turno antes.

        So' vira "nenhuma carencia" depois de CONFIRMAR o sujeito: a elegibilidade dele responde agora. Sem
        essa confirmacao (ela tambem falhou, ou voltou de outro sujeito) continua falha — nunca se afirma
        "nenhuma carencia" sobre um sujeito que a AMH nao reconheceu. A data da fonte e' a da
        elegibilidade."""
        confirmada = await self.port.get_elegibilidade(
            ref, purpose_of_use=self.purpose_of_use, consent_decision_ref=base_legal
        )
        if not confirmada.succeeded or confirmada.value is None:
            return None
        if getattr(confirmada.value, "portable_subject_ref", None) != ref:
            return None
        return CarenciasView(
            portable_subject_ref=ref,
            pendentes=0,
            carencias=(),
            fonte_atualizada_em=confirmada.value.fonte_atualizada_em,
        )

    async def consultar(self, portable_subject_ref: str, subtipo: str) -> _View | None:
        """A view do contrato para o subtipo, ou `None`. NUNCA levanta."""
        motivo = "ok"
        view: _View | None = None
        try:
            async with asyncio.timeout(PRAZO_TOTAL_S):
                base_legal = await self.consentimento.decisao(portable_subject_ref, self.purpose_of_use)
                if base_legal is None:
                    motivo = "sem_base_legal"
                else:
                    resultado = await self._ler(portable_subject_ref, subtipo, base_legal)
                    if resultado is None:
                        motivo = "subtipo_desconhecido"
                    elif not resultado.succeeded or resultado.value is None:
                        falha = resultado.failure
                        motivo = falha.reason.value if falha is not None else "recusa"
                        if (
                            subtipo == "dependentes"
                            and falha is not None
                            and falha.reason == PortFailureReason.SCOPE_NOT_SUPPORTED
                        ):
                            # A rota de dependentes nao esta' carregada: nao e' falha da consulta.
                            view = FonteIndisponivel(subtipo)
                        if (
                            subtipo == "carencia"
                            and falha is not None
                            and falha.reason == PortFailureReason.NOT_FOUND
                        ):
                            view = await self._sem_carencias(portable_subject_ref, base_legal)
                            if view is not None:
                                motivo = "sem_carencias"
                    elif getattr(resultado.value, "portable_subject_ref", None) != portable_subject_ref:
                        motivo = "fatos_de_outro_sujeito"
                    else:
                        view = resultado.value
        except TimeoutError:
            motivo = "prazo_estourado"
        except Exception as exc:
            logger.warning("helena_consultas_falhou", subtipo=subtipo, error_type=type(exc).__name__)
            return None
        logger.info("helena_consultas_lidas", subtipo=subtipo, motivo=motivo, fatos=view is not None)
        return view

    async def aclose(self) -> None:
        if self.aclose_fn is not None:
            await self.aclose_fn()


__all__ = ["PRAZO_TOTAL_S", "ConsultasPlanoAmh"]
