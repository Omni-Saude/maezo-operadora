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

FONTES DA CAMADA "SEU PLANO" (DL-0167, HEL-3; pedidos (a) e (c) da T-MZO-B a T-MZO-A). A cobertura assistida
antiga (DL-0128/DL-0130) saiu com o DL-0154 e NAO volta: nada aqui decide, redige, escolhe procedimento ou
monta fluxo de conversa. Esta fonte so' LE, pelas rotas ja' pinadas, e devolve a view do contrato:

- `consultar_cobertura` (`FonteDeCobertura`): os fatos do cadastro de um procedimento JA' RESOLVIDO para o
  plano do PROPRIO sujeito, rota `tina-cobertura` (manifest v1.9), finalidade FIXA `atendimento_whatsapp`;
- `consultar_cpt` (`FonteDeCpt`): a CPT vigente do proprio sujeito, rota `tina-cpt` (manifest v1.15, DL-0130);
- `consultar_catalogo_procedimentos` (`FonteDoCatalogoDeProcedimentos`): o catalogo inteiro de UMA foto, rota
  `tina-procedimentos` (manifest v1.14), entregue como `ProcedimentoCatalogo` do port para a resolucao
  deterministica da HEL-2 (`habilidades/cobertura/procedimento.resolver`). Dado de referencia, sem sujeito.

O adaptador TINA ja' carrega essas rotas quando os caminhos dos OpenAPI estao configurados
(`tool_registry.build_amh_interop`); sem elas o port recusa `scope_not_supported` (ou nem as implementa) e a
fonte devolve `None`. O procedimento pedido e o conteudo da CPT NUNCA vao a log: so' o motivo (token fechado)
e contagens. O comportamento vivo nao muda: so' a camada 2, atras da chave `cobertura_ampla` (DL-0167), as
chama.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

import structlog

from maezo.agents.lucas.fonte_cobranca_amh import FonteDeConsentimento
from maezo.contratos.fatos_do_plano import (
    LIMITE_CARENCIAS,
    LIMITE_REQUISICOES,
    CatalogoDeProcedimentos,
    FonteIndisponivel,
)
from maezo.ports.errors import PortFailureReason, PortResult
from maezo.ports.tina import (
    CarenciasView,
    CarteirinhasView,
    CoberturaView,
    CptVigenteView,
    DependentesNominaisView,
    DependentesView,
    ElegibilidadeView,
    ProcedimentoCatalogo,
    RequisicoesView,
    TinaCarteirinhaPort,
    TinaCoberturaPort,
    TinaCptPort,
    TinaDependentesNomePort,
    TinaDependentesPort,
    TinaPort,
    TinaProcedimentosPort,
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
#: A UNICA finalidade da rota da CPT vigente (enum do contrato `tina-cpt`, manifest v1.15; DL-0130).
PURPOSE_CPT: Final[str] = "atendimento_whatsapp"
#: A UNICA finalidade do catalogo de procedimentos (enum do contrato `tina-procedimentos`, manifest v1.14).
PURPOSE_PROCEDIMENTOS: Final[str] = "atendimento_whatsapp"
#: A busca de cobertura da AMH (termo x Rol x plano) leva 7 a 9 s em dev (medido em 18/10/2026).
PRAZO_LEITURA_COBERTURA_S: Final[float] = 16.0
#: Teto da leitura da CPT (base legal + uma leitura curta): sem resposta a tempo, `None`.
PRAZO_TOTAL_CPT_S: Final[float] = 11.0
#: Quantos procedimentos a cobertura pede (contrato: 1..10, padrao 5). Mais que isso volta `truncada`.
LIMITE_COBERTURA: Final[int] = 5
#: A base legal do CATALOGO (dado de referencia, sem sujeito): so' o hash entra na auditoria.
BASE_LEGAL_DO_CATALOGO: Final[str] = "referencia:catalogo-de-procedimentos"
#: Paginacao do catalogo: o tamanho de pagina pedido e o teto de paginas lidas de UMA foto.
TAMANHO_PAGINA_CATALOGO: Final[int] = 2000
MAX_PAGINAS_CATALOGO: Final[int] = 50
#: Teto da leitura do catalogo inteiro (todas as paginas, incluindo o recomeco unico da pagina 1).
PRAZO_TOTAL_CATALOGO_S: Final[float] = 120.0
#: Por quanto tempo uma foto NAO VAZIA do catalogo lida com sucesso e' reaproveitada: o `max-age=300` do
#: `Cache-Control` do contrato `tina-procedimentos` (a `versao_catalogo` muda quando o conteudo muda).
#: Catalogo vazio nunca fica guardado.
VALIDADE_DO_CATALOGO_S: Final[float] = 300.0
#: Depois de uma leitura que falhou, por quanto tempo a fonte responde `None` sem ler de novo (quem esperava
#: na trava nao refaz a leitura inteira, um atras do outro).
ESPERA_APOS_FALHA_DO_CATALOGO_S: Final[float] = 45.0
#: Quanto tempo uma chamada espera a trava do catalogo (outra leitura em curso) antes de devolver `None`.
PRAZO_DA_TRAVA_DO_CATALOGO_S: Final[float] = PRAZO_TOTAL_CATALOGO_S + 5.0
#: Quantas leituras inteiras uma consulta faz: a `versao_catalogo` mudou no meio, o contrato manda recomecar
#: da pagina 1 (uma vez so'; mudou de novo = `None`).
TENTATIVAS_DO_CATALOGO: Final[int] = 2


@dataclass
class ConsultasPlanoAmh:
    """Construida UMA vez no boot do receptor, so' com `helena_consultas_amh` ligada (`service.py`)."""

    port: TinaPort
    #: Base legal fixa de execucao de contrato (decisao do dono; NAO e' consentimento).
    consentimento: FonteDeConsentimento
    purpose_of_use: str
    #: Drenagem dos executores do gateway (a composicao e' a da identidade; fecha uma vez so').
    aclose_fn: Callable[[], Awaitable[None]] | None = field(default=None, repr=False)
    #: A ultima foto do catalogo lida com sucesso e quando (relogio monotonico).
    _catalogo: tuple[float, CatalogoDeProcedimentos] | None = field(default=None, init=False, repr=False)
    #: Quando a ultima leitura do catalogo falhou (relogio monotonico), ou `None`.
    _falha_do_catalogo_em: float | None = field(default=None, init=False, repr=False)
    _trava_catalogo: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

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

    async def consultar_dependentes_nominais(
        self, portable_subject_ref: str, request_ref: str
    ) -> DependentesNominaisView | None:
        """Os dependentes ATIVOS do proprio titular COM o nome completo (rota `tina-dependentes-nome`,
        manifest
        v1.13; decisao do dono e DPO de 19/10/2026, DL-0127), ou `None`. NUNCA levanta.

        `None` = a rota nao esta' carregada, 404, qualquer outra recusa, prazo, ou resposta de OUTRO sujeito.
        Nenhum nome vai a log: so' o motivo e quantos itens vieram."""
        motivo = "ok"
        view: DependentesNominaisView | None = None
        try:
            async with asyncio.timeout(PRAZO_TOTAL_S):
                if not isinstance(self.port, TinaDependentesNomePort):
                    motivo = "rota_nao_carregada"
                else:
                    base_legal = await self.consentimento.decisao(portable_subject_ref, PURPOSE_CARTEIRINHA)
                    if base_legal is None:
                        motivo = "sem_base_legal"
                    else:
                        resultado = await self.port.get_dependentes_nominais(
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
            logger.warning("helena_dependentes_nome_falhou", error_type=type(exc).__name__)
            return None
        logger.info(
            "helena_dependentes_nome_lidos",
            motivo=motivo,
            itens=len(view.dependentes) if view is not None else 0,
        )
        return view

    async def consultar_cobertura(self, portable_subject_ref: str, procedimento: str) -> CoberturaView | None:
        """Os fatos de cobertura do procedimento (o CODIGO resolvido pela HEL-2, ou o termo no padrao do
        contrato) para o plano do PROPRIO sujeito, ou `None`. NUNCA levanta.

        `None` = a rota nao esta' carregada (o port nao e' `TinaCoberturaPort` ou recusa
        `scope_not_supported`), sem base legal, termo fora do padrao do contrato, 404 (rota desligada /
        sujeito sem plano), qualquer outra recusa, prazo, ou resposta de OUTRO sujeito. O procedimento NUNCA
        vai a log: so' o motivo (token fechado) e quantos itens vieram."""
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
                            timeout_seconds=PRAZO_LEITURA_COBERTURA_S,
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

    async def consultar_cpt(self, portable_subject_ref: str) -> CptVigenteView | None:
        """A CPT vigente do PROPRIO sujeito (rota `tina-cpt`, manifest v1.15; caso C1 do dono e DPO de
        20/10/2026, DL-0130), ou `None`. NUNCA levanta.

        `None` = a rota nao esta' carregada, sem base legal, 404 (rota desligada / sujeito sem plano), 429,
        qualquer outra recusa, prazo, ou resposta de OUTRO sujeito. Nada da CPT vai a log: so' o motivo."""
        motivo = "ok"
        view: CptVigenteView | None = None
        try:
            async with asyncio.timeout(PRAZO_TOTAL_CPT_S):
                if not isinstance(self.port, TinaCptPort):
                    motivo = "rota_nao_carregada"
                else:
                    base_legal = await self.consentimento.decisao(portable_subject_ref, PURPOSE_CPT)
                    if base_legal is None:
                        motivo = "sem_base_legal"
                    else:
                        resultado = await self.port.get_cpt_vigente(
                            portable_subject_ref,
                            purpose_of_use=PURPOSE_CPT,
                            consent_decision_ref=base_legal,
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
            logger.warning("helena_cpt_falhou", error_type=type(exc).__name__)
            return None
        logger.info("helena_cpt_lida", motivo=motivo)
        return view

    async def consultar_catalogo_procedimentos(self) -> CatalogoDeProcedimentos | None:
        """O catalogo de procedimentos INTEIRO de uma foto (todas as paginas com a MESMA `versao_catalogo`),
        ou `None`. NUNCA levanta (nem pela trava).

        Dado de referencia (sem sujeito): base legal fixa `BASE_LEGAL_DO_CATALOGO`. Uma foto NAO VAZIA lida
        com sucesso e' reaproveitada por `VALIDADE_DO_CATALOGO_S` (o `max-age` do contrato); catalogo vazio
        nunca fica guardado. Depois de uma falha, `None` sem ler de novo por
        `ESPERA_APOS_FALHA_DO_CATALOGO_S` (quem esperava na trava nao refaz a leitura). `None` = a rota nao
        esta' carregada, qualquer recusa ou excecao, prazo (da leitura ou da trava), paginas demais, pagina ou
        contagem incoerente, ou a versao mudou de novo depois do recomeco (catalogo parcial nunca e'
        entregue: a resolucao deixaria procedimentos de fora sem saber)."""
        try:
            achado, guardado = self._catalogo_guardado()
            if achado:
                return guardado
            async with asyncio.timeout(PRAZO_DA_TRAVA_DO_CATALOGO_S):
                await self._trava_catalogo.acquire()
            try:
                achado, guardado = self._catalogo_guardado()
                if achado:
                    return guardado
                catalogo = await self._ler_catalogo()
                agora = time.monotonic()
                if catalogo is None:
                    self._falha_do_catalogo_em = agora
                else:
                    self._falha_do_catalogo_em = None
                    if catalogo.itens:
                        self._catalogo = (agora, catalogo)
                return catalogo
            finally:
                self._trava_catalogo.release()
        except TimeoutError:
            logger.info("helena_catalogo_lido", motivo="trava_ocupada", itens=0)
            return None
        except Exception as exc:
            logger.warning("helena_catalogo_falhou", error_type=type(exc).__name__)
            return None

    def _catalogo_guardado(self) -> tuple[bool, CatalogoDeProcedimentos | None]:
        """`(True, foto)` com foto boa dentro da validade; `(True, None)` dentro da espera depois de uma
        falha; `(False, None)` = precisa ler."""
        agora = time.monotonic()
        guardado = self._catalogo
        if guardado is not None and agora - guardado[0] < VALIDADE_DO_CATALOGO_S:
            return True, guardado[1]
        falha_em = self._falha_do_catalogo_em
        if falha_em is not None and agora - falha_em < ESPERA_APOS_FALHA_DO_CATALOGO_S:
            return True, None
        return False, None

    async def _ler_catalogo(self) -> CatalogoDeProcedimentos | None:
        if not isinstance(self.port, TinaProcedimentosPort):
            logger.info("helena_catalogo_lido", motivo="rota_nao_carregada", itens=0)
            return None
        motivo = "ok"
        catalogo: CatalogoDeProcedimentos | None = None
        try:
            async with asyncio.timeout(PRAZO_TOTAL_CATALOGO_S):
                for _ in range(TENTATIVAS_DO_CATALOGO):
                    # Versao que muda no meio: o contrato manda recomecar da pagina 1 (uma vez so').
                    motivo, catalogo = await self._ler_uma_foto(self.port)
                    if motivo != "versao_mudou":
                        break
        except TimeoutError:
            motivo, catalogo = "prazo_estourado", None
        except Exception as exc:
            logger.warning("helena_catalogo_falhou", error_type=type(exc).__name__)
            return None
        logger.info(
            "helena_catalogo_lido", motivo=motivo, itens=len(catalogo.itens) if catalogo is not None else 0
        )
        return catalogo

    @staticmethod
    async def _ler_uma_foto(port: TinaProcedimentosPort) -> tuple[str, CatalogoDeProcedimentos | None]:
        """Le' as paginas de UMA foto a partir da 1. `("ok", catalogo)` ou `(motivo, None)`."""
        itens: list[ProcedimentoCatalogo] = []
        versao = ""
        atualizada_em = ""
        pagina, total, total_itens = 1, 1, 0
        while pagina <= total:
            resultado = await port.get_catalogo_procedimentos(
                purpose_of_use=PURPOSE_PROCEDIMENTOS,
                consent_decision_ref=BASE_LEGAL_DO_CATALOGO,
                pagina=pagina,
                tamanho=TAMANHO_PAGINA_CATALOGO,
                timeout_seconds=PRAZO_LEITURA_S * 2,
            )
            if not resultado.succeeded or resultado.value is None:
                falha = resultado.failure
                return (falha.reason.value if falha is not None else "recusa"), None
            view = resultado.value
            if pagina == 1:
                if view.total_paginas > MAX_PAGINAS_CATALOGO:
                    return "paginas_demais", None
                versao = view.versao_catalogo
                atualizada_em = view.fonte_atualizada_em
                total = view.total_paginas
                total_itens = view.total_itens
            elif view.versao_catalogo != versao:
                return "versao_mudou", None
            if view.pagina != pagina or view.total_paginas != total:
                return "catalogo_inconsistente", None
            itens.extend(view.itens)
            pagina += 1
        # O contrato: uma linha por codigo distinto, `total_itens` itens no catalogo inteiro.
        if len(itens) != total_itens or len({i.codigo for i in itens}) != len(itens):
            return "catalogo_inconsistente", None
        return "ok", CatalogoDeProcedimentos(
            versao_catalogo=versao, itens=tuple(itens), fonte_atualizada_em=atualizada_em
        )

    async def aclose(self) -> None:
        if self.aclose_fn is not None:
            await self.aclose_fn()


__all__ = [
    "BASE_LEGAL_DO_CATALOGO",
    "LIMITE_COBERTURA",
    "PRAZO_LEITURA_S",
    "PRAZO_TOTAL_S",
    "PURPOSE_CARTEIRINHA",
    "PURPOSE_COBERTURA",
    "PURPOSE_CPT",
    "PURPOSE_PROCEDIMENTOS",
    "ESPERA_APOS_FALHA_DO_CATALOGO_S",
    "VALIDADE_DO_CATALOGO_S",
    "ConsultasPlanoAmh",
]
