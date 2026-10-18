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

CARTEIRINHA COMPLETA (decisao do dono de 17/10/2026, so' dev): `consultar_carteirinha` le' o numero completo
da carteirinha do PROPRIO sujeito pela rota `tina-carteirinha` (manifest v1.12), com a finalidade FIXA
`atendimento_whatsapp` e o id OPACO da solicitacao. Implementa `FonteDeCarteirinhaCompleta`; o grafo so' a
chama com a chave `MAEZO_HELENA_CARTEIRINHA_COMPLETA` ligada e a identidade verificada. O numero NUNCA vai a
log: so' o motivo (token fechado) e quantos itens vieram.

COBERTURA POR PROCEDIMENTO (decisao do dono D1 de 18/10/2026): `consultar_cobertura` le' os fatos do cadastro
(Rol, segmentacao, carencias) de um procedimento para o plano do PROPRIO sujeito pela rota `tina-cobertura`
(manifest v1.9), com a finalidade FIXA `atendimento_whatsapp`. Implementa `FonteDeCobertura`. O termo pedido
NUNCA vai a log: so' o motivo e quantos itens vieram.
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
    CarteirinhasView,
    CoberturaView,
    DependentesView,
    ElegibilidadeView,
    RequisicoesView,
    TinaCarteirinhaPort,
    TinaCoberturaPort,
    TinaDependentesPort,
    TinaPort,
)

logger = structlog.get_logger(__name__)

#: Prazo de CADA leitura da AMH (o `timeout_seconds` do port). A consulta Athena do interop leva de 1 a
#: 10 s (o prazo dela e' 10 s); com os 5 s do padrao do port, o roteiro `dono_v1` reprovou em 16/10/2026 por
#: `timeout` em dependentes e em carencia com a AMH respondendo.
PRAZO_LEITURA_S: Final[float] = 9.0
#: Teto de uma consulta: a base legal e ate DUAS leituras em sequencia ("estou em carencia?" sem linha de
#: carencia confirma o sujeito pela elegibilidade, `_sem_carencias`): duas de `PRAZO_LEITURA_S` mais folga.
PRAZO_TOTAL_S: Final[float] = 20.0

_View = ElegibilidadeView | CarenciasView | RequisicoesView | DependentesView | FonteIndisponivel
#: A UNICA finalidade da rota da carteirinha completa (enum do contrato `tina-carteirinha`).
PURPOSE_CARTEIRINHA: Final[str] = "atendimento_whatsapp"
#: A UNICA finalidade da rota da cobertura por procedimento (enum do contrato `tina-cobertura`).
PURPOSE_COBERTURA: Final[str] = "atendimento_whatsapp"
#: Quantos procedimentos a cobertura pede (contrato: 1..10, padrao 5). Termo generico volta `truncada`.
LIMITE_COBERTURA: Final[int] = 5


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
                ref,
                purpose_of_use=proposito,
                consent_decision_ref=base_legal,
                timeout_seconds=PRAZO_LEITURA_S,
            )
        if subtipo == "carencia":
            return await self.port.get_carencias(
                ref,
                purpose_of_use=proposito,
                consent_decision_ref=base_legal,
                limite=LIMITE_CARENCIAS,
                timeout_seconds=PRAZO_LEITURA_S,
            )
        if subtipo == "autorizacao":
            return await self.port.get_requisicoes(
                ref,
                purpose_of_use=proposito,
                consent_decision_ref=base_legal,
                limite=LIMITE_REQUISICOES,
                timeout_seconds=PRAZO_LEITURA_S,
            )
        if subtipo == "dependentes":
            # O mesmo adaptador TINA implementa o port de dependentes; um port sem ele = sem a fonte.
            if not isinstance(self.port, TinaDependentesPort):
                return PortResult.refused(PortFailureReason.SCOPE_NOT_SUPPORTED)
            return await self.port.get_dependentes(
                ref,
                purpose_of_use=proposito,
                consent_decision_ref=base_legal,
                timeout_seconds=PRAZO_LEITURA_S,
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
            ref,
            purpose_of_use=self.purpose_of_use,
            consent_decision_ref=base_legal,
            timeout_seconds=PRAZO_LEITURA_S,
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

    async def consultar_carteirinha(
        self, portable_subject_ref: str, request_ref: str
    ) -> CarteirinhasView | None:
        """O numero COMPLETO da carteirinha dos vinculos ATIVOS do proprio sujeito, ou `None`. NUNCA levanta.

        `None` = a rota nao esta' carregada (o port nao e' `TinaCarteirinhaPort` ou recusa
        `scope_not_supported`), 404 (sem vinculo ativo / rota desligada), qualquer outra recusa, prazo, ou
        resposta de OUTRO sujeito."""
        motivo = "ok"
        view: CarteirinhasView | None = None
        try:
            async with asyncio.timeout(PRAZO_TOTAL_S):
                if not isinstance(self.port, TinaCarteirinhaPort):
                    motivo = "rota_nao_carregada"
                else:
                    base_legal = await self.consentimento.decisao(portable_subject_ref, PURPOSE_CARTEIRINHA)
                    if base_legal is None:
                        motivo = "sem_base_legal"
                    else:
                        resultado = await self.port.get_carteirinha(
                            portable_subject_ref,
                            purpose_of_use=PURPOSE_CARTEIRINHA,
                            consent_decision_ref=base_legal,
                            request_ref=request_ref,
                            timeout_seconds=PRAZO_LEITURA_S,
                        )
                        if not resultado.succeeded or resultado.value is None:
                            falha = resultado.failure
                            motivo = falha.reason.value if falha is not None else "recusa"
                        elif getattr(resultado.value, "portable_subject_ref", None) != portable_subject_ref:
                            motivo = "fatos_de_outro_sujeito"
                        else:
                            view = resultado.value
        except TimeoutError:
            motivo = "prazo_estourado"
        except Exception as exc:
            logger.warning("helena_carteirinha_falhou", error_type=type(exc).__name__)
            return None
        logger.info(
            "helena_carteirinha_lida",
            motivo=motivo,
            itens=len(view.carteirinhas) if view is not None else 0,
        )
        return view

    async def consultar_cobertura(self, portable_subject_ref: str, procedimento: str) -> CoberturaView | None:
        """Os fatos de cobertura do procedimento (codigo ou termo) para o plano do proprio sujeito, ou
        `None`. NUNCA levanta.

        `None` = a rota nao esta' carregada (o port nao e' `TinaCoberturaPort` ou recusa
        `scope_not_supported`), termo fora do padrao do contrato, 404 (rota desligada / sujeito sem plano),
        qualquer outra recusa, prazo, ou resposta de OUTRO sujeito. O termo NUNCA vai a log: so' o motivo
        (token fechado) e quantos itens vieram."""
        motivo = "ok"
        view: CoberturaView | None = None
        try:
            async with asyncio.timeout(PRAZO_TOTAL_S):
                if not isinstance(self.port, TinaCoberturaPort):
                    motivo = "rota_nao_carregada"
                else:
                    base_legal = await self.consentimento.decisao(portable_subject_ref, PURPOSE_COBERTURA)
                    if base_legal is None:
                        motivo = "sem_base_legal"
                    else:
                        resultado = await self.port.get_cobertura(
                            portable_subject_ref,
                            purpose_of_use=PURPOSE_COBERTURA,
                            consent_decision_ref=base_legal,
                            procedimento=procedimento,
                            limite=LIMITE_COBERTURA,
                            timeout_seconds=PRAZO_LEITURA_S,
                        )
                        if not resultado.succeeded or resultado.value is None:
                            falha = resultado.failure
                            motivo = falha.reason.value if falha is not None else "recusa"
                        elif getattr(resultado.value, "portable_subject_ref", None) != portable_subject_ref:
                            motivo = "fatos_de_outro_sujeito"
                        else:
                            view = resultado.value
        except TimeoutError:
            motivo = "prazo_estourado"
        except Exception as exc:
            logger.warning("helena_cobertura_falhou", error_type=type(exc).__name__)
            return None
        logger.info(
            "helena_cobertura_lida",
            motivo=motivo,
            itens=len(view.itens) if view is not None else 0,
        )
        return view

    async def aclose(self) -> None:
        if self.aclose_fn is not None:
            await self.aclose_fn()


__all__ = [
    "LIMITE_COBERTURA",
    "PRAZO_LEITURA_S",
    "PRAZO_TOTAL_S",
    "PURPOSE_CARTEIRINHA",
    "PURPOSE_COBERTURA",
    "ConsultasPlanoAmh",
]
