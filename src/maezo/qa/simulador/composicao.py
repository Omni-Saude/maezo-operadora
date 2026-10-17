"""Composicao do simulador: o despachante REAL do receptor, com so' as bordas de saida trocadas.

O despachante nasce de `platform.webhooks.service.build_live_dispatcher` — a MESMA funcao que o
receptor implantado chama no boot (nenhuma copia da composicao aqui). Depois, e so' depois, cinco
trocas, todas no lado de fora dos agentes (nenhum comportamento de Helena ou Lucas muda):

  1. `whatsapp_client` -> `CapturaDeEnvios`. O remetente por turno (`_ScopedWhatsAppSender`) e o
     gate de efeito (`gate_whatsapp`) continuam no caminho: a captura fica no lugar do cliente HTTP
     da Meta, recusa destino fora da faixa sintetica e marca cada envio como `helena`, `lucas` ou
     `acesso` (o Lucas usa o espaco de chaves `lucas:{message_id}`; o acesso, textos fixos).
  2. `cibseven` (do despachante e do Lucas) -> `gate_cibseven(InterceptorDeEscalonamento(...))`.
     O interceptador finge SO' SP-OP-ESCALATION-001 (procura e inicio, por chave `ESC-...`) e grava
     "teria escalado" com motivo, severidade e resumo; leitura de outras chaves passa ao motor real.
     Iniciar qualquer outro processo e' RECUSADO: nenhum caso real nasce de uma conversa simulada.
     O gate de efeito do principal continua por fora, como no receptor.
  3. `lucas_turno.fonte` -> `FonteGravada`: guarda os `FatosCobranca` EXATOS de cada turno (o
     gabarito da checagem D2) e devolve o mesmo objeto.
  4. `identidade` -> `IdentidadeGravada` (so' registra o desfecho fechado de cada resolucao).
  5. `recipient_vault = None` (nenhum telefone sintetico vai para a custodia) e o gravador de
     consentimento do acesso -> `ConsentimentoControlado`: no modo `somente-leitura` (padrao) a
     gravacao na AMH so' acontece para o dono; para terceiros `record()` devolve recusa SEM chamar a
     AMH, e o acesso segue pelo caminho ja' previsto de "gravacao pendente" — a leitura de cobranca
     cai na base legal de execucao de contrato (`acesso_ponte.ConsentimentoDoAcesso`).

O que liga turno e captura e' uma `ContextVar`: cada conversa roda na sua task, entao o que um
turno grava nunca se mistura com o de outra conversa concorrente.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import structlog

from maezo.gateway.seams import GatedSeam
from maezo.gateway.seams.cibseven import gate_cibseven
from maezo.gateway.tool_registry import effect_seams_gated
from maezo.platform.webhooks.service import WebhookState, build_live_dispatcher
from maezo.platform.webhooks.whatsapp import acesso as acesso_mod
from maezo.ports.errors import PortFailureReason, PortResult
from maezo.runtime.caso_clinico import chave_do_escalonamento
from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    ProcessInstance,
    ProcessNotFoundError,
    ProcessStatus,
)

from .registro import EscalonamentoSimulado, MensagemEnviada, Sujeito, Turno

if TYPE_CHECKING:
    from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

logger = structlog.get_logger(__name__)

PROCESSO_ESCALONAMENTO = "SP-OP-ESCALATION-001"
#: Rodada 12: o pedido de cancelamento confirmado pelo beneficiario PF (Lucas) tambem e' interceptado.
PROCESSO_CANCELAMENTO = "SP-OP-CANCEL-001"
#: DL-0111: o marcador gravado nas instancias de cancelamento fingidas (`instancia_de_teste=True`).
MARCADOR_DE_TESTE = "SIMULADOR-QA"
#: A faixa sintetica (a mesma de `mcp_whatsapp.server.FAIXA_SINTETICA`; repetida porque este pacote
#: nao importa o cliente da Meta). A captura recusa qualquer destino fora dela.
FAIXA_SINTETICA = re.compile(r"^5511900000\d{3}$")
#: Quantos envios do Lucas por mensagem de entrada a captura reconhece pela chave.
MAX_ENVIOS_POR_TURNO = 8

ModoConsentimento = Literal["somente-leitura", "completo"]
MODOS_CONSENTIMENTO: tuple[ModoConsentimento, ...] = ("somente-leitura", "completo")


@dataclass
class ContextoDoTurno:
    """O turno em curso NESTA task (conversa). `message_id` e' o id de entrada sintetico."""

    turno: Turno
    message_id: str
    conversation_id: str


_CONTEXTO: ContextVar[ContextoDoTurno | None] = ContextVar("simulador_turno", default=None)


@contextmanager
def no_turno(contexto: ContextoDoTurno) -> Iterator[ContextoDoTurno]:
    token = _CONTEXTO.set(contexto)
    try:
        yield contexto
    finally:
        _CONTEXTO.reset(token)


def turno_atual() -> ContextoDoTurno | None:
    return _CONTEXTO.get()


# ================================================================================================
# 1. WhatsApp
# ================================================================================================
def _textos_do_acesso() -> frozenset[str]:
    textos = set(acesso_mod.textos_fixos().values())
    textos.add(acesso_mod.REVOGACAO_EM_PROCESSAMENTO)
    return frozenset(textos)


class DestinoForaDaFaixaError(RuntimeError):
    """A captura recebeu um destino que nao e' sintetico. Nunca acontece por desenho; se acontecer, para."""


class CapturaDeEnvios:
    """Fica no lugar do cliente HTTP da Meta (`WhatsAppServer`) dentro do remetente por turno."""

    def __init__(self, *, chave_de_saida: Callable[[str, int], str] | None) -> None:
        self._chave_de_saida = chave_de_saida
        self._textos_acesso = _textos_do_acesso()
        self._sequencia = itertools.count(1)
        #: Envios sem turno em curso (nao deveria haver; contados e logados).
        self.fora_de_turno = 0

    def _agente(self, texto: str, idempotency_key: str | None, contexto: ContextoDoTurno) -> str:
        if idempotency_key is not None and self._chave_de_saida is not None:
            chaves_do_lucas = {
                self._chave_de_saida(f"lucas:{contexto.message_id}", n)
                for n in range(1, MAX_ENVIOS_POR_TURNO + 1)
            }
            if idempotency_key in chaves_do_lucas:
                return "lucas"
        if texto in self._textos_acesso:
            return "acesso"
        return "helena"

    async def send_message(self, to: str, text: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        if not FAIXA_SINTETICA.match(re.sub(r"\D", "", to or "")):
            raise DestinoForaDaFaixaError("simulador: destino fora da faixa sintetica — envio recusado")
        contexto = _CONTEXTO.get()
        if contexto is None:
            self.fora_de_turno += 1
            logger.error("simulador_envio_fora_de_turno")
        else:
            agente = self._agente(text, idempotency_key, contexto)
            contexto.turno.mensagens.append(MensagemEnviada(agente=agente, texto=text))  # type: ignore[arg-type]
        return {"messages": [{"id": f"wamid.simulador.{next(self._sequencia)}"}], "simulado": True}


# ================================================================================================
# 2. Motor: SP-OP-ESCALATION-001 interceptado
# ================================================================================================
@dataclass
class _InstanciaSimulada:
    instancia: ProcessInstance
    variaveis: dict[str, Any]


@dataclass
class RegistroDeEscalonamentos:
    """As instancias que o interceptador fingiu ter aberto, por chave de negocio."""

    _por_chave: dict[str, _InstanciaSimulada] = field(default_factory=dict)
    _sequencia: Iterator[int] = field(default_factory=lambda: itertools.count(1))
    total: int = 0
    #: DL-0111: TODA chave `CANCEL-...` que o interceptador fingiu abrir nesta execucao (nao some com
    #: `esquecer`): a limpeza pergunta por elas ao motor REAL no fim — tem de ser zero instancia viva.
    cancelamentos_registrados: set[str] = field(default_factory=set)

    def ativa(self, business_key: str) -> ProcessInstance | None:
        achada = self._por_chave.get(business_key)
        return achada.instancia if achada else None

    def status(self, business_key: str) -> ProcessStatus:
        achada = self._por_chave.get(business_key)
        if achada is None:
            raise ProcessNotFoundError(f"simulador: nenhuma instancia simulada para {business_key!r}")
        i = achada.instancia
        return ProcessStatus(
            instance_id=i.instance_id,
            process_key=i.process_key,
            business_key=i.business_key,
            state=i.state,
            variables=dict(achada.variaveis),
        )

    def registrar(
        self, business_key: str, variaveis: dict[str, Any], process_key: str = PROCESSO_ESCALONAMENTO
    ) -> ProcessInstance:
        instancia = ProcessInstance(
            instance_id=f"simulador-{next(self._sequencia)}",
            process_key=process_key,
            business_key=business_key,
            state="ACTIVE",
        )
        self._por_chave[business_key] = _InstanciaSimulada(instancia, dict(variaveis))
        self.total += 1
        if process_key == PROCESSO_CANCELAMENTO:
            self.cancelamentos_registrados.add(business_key)
        return instancia

    def encerrar(self, business_key: str) -> bool:
        """ADR-0064/DL-0118: a instancia viva da chave TERMINA (o motor real a encerra pelo event subprocess
        de encerramento a pedido do SP-OP-ESCALATION-001 v1.1.0). `False` = nao havia instancia viva."""
        return self._por_chave.pop(business_key, None) is not None

    def esquecer(self, tenant_id: str, conversation_id: str) -> None:
        """Conversa nova no mesmo telefone: os casos simulados da anterior deixam de existir."""
        for clinico in (False, True):
            self._por_chave.pop(chave_do_escalonamento(tenant_id, conversation_id, clinico=clinico), None)
        # Pedidos de cancelamento simulados (chave ancorada na identidade pseudonima, nao na conversa).
        for chave in [k for k in self._por_chave if k.startswith(f"CANCEL-{tenant_id}-")]:
            del self._por_chave[chave]


#: ADR-0064/DL-0118: a mensagem de encerramento a pedido do SP-OP-ESCALATION-001 v1.1.0 (a do grafo da Helena).
MENSAGEM_ENCERRAMENTO_A_PEDIDO = "msg.escalation.withdrawn"


def _e_chave_de_escalonamento(business_key: str) -> bool:
    return business_key.startswith(("ESC-", "CANCEL-"))


class InterceptorDeEscalonamento:
    """`CibSevenTransport` que finge SP-OP-ESCALATION-001 e passa o resto ao transporte real."""

    def __init__(self, inner: Any, registro: RegistroDeEscalonamentos) -> None:
        self._inner = inner
        self._registro = registro

    @property
    def engine_tenant_id(self) -> str | None:
        tenant: str | None = self._inner.engine_tenant_id
        return tenant

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        if _e_chave_de_escalonamento(business_key):
            return self._registro.ativa(business_key)
        achada: ProcessInstance | None = await self._inner.find_active_instance(business_key)
        return achada

    async def find_any_instance(self, business_key: str, *, process_key: str = "") -> ProcessInstance | None:
        if business_key.startswith("CANCEL-") and self._registro.ativa(business_key) is None:
            # DL-0111: SP-OP-CANCEL-001 e' EXCLUSIVE no portao de dedup (`transport.py`). A reivindicacao
            # duravel de uma execucao ANTERIOR do simulador continua no Postgres, mas o caso fingido dela
            # sumiu com o registro: sem esta resposta o portao veria "reivindicacao sem instancia" e
            # travaria o turno. Para o simulador, a geracao anterior ENCERROU (nenhuma instancia real
            # jamais existiu) — o portao segue para o start, que este interceptador tambem finge.
            return ProcessInstance(
                instance_id="simulador-geracao-encerrada",
                process_key=PROCESSO_CANCELAMENTO,
                business_key=business_key,
                state="COMPLETED",
            )
        if _e_chave_de_escalonamento(business_key):
            return self._registro.ativa(business_key)
        achada: ProcessInstance | None = await self._inner.find_any_instance(
            business_key, process_key=process_key
        )
        return achada

    async def read_historic_variables(self, process_instance_id: str, names: tuple[str, ...]) -> Any:
        if process_instance_id.startswith("simulador-"):
            return None
        return await self._inner.read_historic_variables(process_instance_id, names)

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        e_cancelamento = process_key == PROCESSO_CANCELAMENTO and business_key.startswith("CANCEL-")
        e_escalonamento = process_key == PROCESSO_ESCALONAMENTO and business_key.startswith("ESC-")
        if not (e_cancelamento or e_escalonamento):
            # Nenhum processo real nasce de conversa simulada: a recusa vira o caminho de falha do agente.
            raise CibSevenError("simulador: inicio de processo real recusado")
        if e_cancelamento:
            # DL-0111: o pedido de cancelamento simulado leva o MARCADOR DE TESTE (nunca chega ao motor: o
            # interceptador e' a unica porta; o marcador e' para o registro e o relatorio dizerem o que e').
            variables = {**variables, "instancia_de_teste": True, "marcador_teste": MARCADOR_DE_TESTE}
        instancia = self._registro.registrar(business_key, variables, process_key)
        contexto = _CONTEXTO.get()
        if contexto is not None:
            contexto.turno.escalonamentos.append(
                EscalonamentoSimulado(
                    agente=str(variables.get("source_agent_id") or "desconhecido"),
                    motivo="cancelamento"
                    if e_cancelamento
                    else _texto_ou_none(variables.get("motivo_categoria")),
                    severidade=_texto_ou_none(variables.get("severidade")),
                    business_key=business_key,
                    resumo=str(variables.get("resumo_contexto") or ""),
                )
            )
        logger.info(
            "simulador_escalonamento_interceptado",
            agente=variables.get("source_agent_id"),
            motivo=variables.get("motivo_categoria"),
        )
        return instancia

    async def correlate_message(
        self,
        message_name: str,
        business_key: str,
        variables: dict[str, Any],
        *,
        correlation_keys: dict[str, Any] | None = None,
        all_matching: bool = False,
    ) -> None:
        if _e_chave_de_escalonamento(business_key):
            logger.info("simulador_correlacao_interceptada", mensagem=message_name)
            if message_name == MENSAGEM_ENCERRAMENTO_A_PEDIDO and business_key.startswith("ESC-"):
                # ADR-0064/DL-0118: como o motor com o BPMN v1.1.0 — o encerramento a pedido ENCERRA a instancia;
                # sem instancia viva o motor recusa (400), e o agente ouve a falha.
                if not self._registro.encerrar(business_key):
                    raise CibSevenError("simulador: nenhuma instancia viva para o encerramento a pedido")
                contexto = _CONTEXTO.get()
                if contexto is not None and self._registro.ativa(business_key) is None:
                    contexto.turno.encerramentos.append(business_key)
            return
        await self._inner.correlate_message(
            message_name,
            business_key,
            variables,
            correlation_keys=correlation_keys,
            all_matching=all_matching,
        )

    async def get_process_status(self, business_key: str) -> ProcessStatus:
        if _e_chave_de_escalonamento(business_key):
            return self._registro.status(business_key)
        status: ProcessStatus = await self._inner.get_process_status(business_key)
        return status

    async def close(self) -> None:
        await self._inner.close()


def _texto_ou_none(valor: Any) -> str | None:
    return None if valor is None else str(valor)


# ================================================================================================
# 3. Fonte de cobranca do Lucas (gabarito do D2)
# ================================================================================================
def fatos_como_dict(fatos: Any) -> dict[str, Any]:
    bruto = asdict(fatos)
    bruto["competencias"] = [dict(c) for c in bruto.get("competencias", ())]
    return bruto


class FonteGravada:
    """`FonteCobranca` que devolve EXATAMENTE o que a fonte real devolveu, guardando uma copia."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def fatos(self, pseudo_id: str, competencia: str | None, *, phone_hash: str | None = None) -> Any:
        from maezo.agents.lucas.fonte_cobranca import FatosCobranca

        resultado = await self._inner.fatos(pseudo_id, competencia, phone_hash=phone_hash)
        contexto = _CONTEXTO.get()
        if contexto is not None:
            if isinstance(resultado, FatosCobranca):
                gravado = fatos_como_dict(resultado)
                gravado["competencia_pedida"] = competencia
                contexto.turno.fatos.append(gravado)
            else:
                contexto.turno.fonte_indisponivel.append(str(getattr(resultado, "motivo", "desconhecido")))
        return resultado

    def __getattr__(self, nome: str) -> Any:
        return getattr(self._inner, nome)


class FonteV4Gravada:
    """`FonteV4` (DL-0114) que devolve EXATAMENTE o que a fonte devolveu e guarda, no turno, os fatos que ela
    entrega ao Lucas (gabarito do D2: total e detalhamento do pagador, valor do titulo da 2a via). A
    CREDENCIAL (codigo de barras) nunca e' gravada: `FatosV4.como_entrada_lucas` nao a tem, e o objeto
    `CredenciaisDoTurno` so' passa adiante."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @staticmethod
    def _gravar(fatos: Any) -> None:
        contexto = _CONTEXTO.get()
        como_entrada = getattr(fatos, "como_entrada_lucas", None)
        if contexto is not None and como_entrada is not None:
            gravado = como_entrada()
            if gravado:
                contexto.turno.fatos.append(gravado)

    async def detalhamento(self, *args: Any, **kwargs: Any) -> Any:
        resultado = await self._inner.detalhamento(*args, **kwargs)
        self._gravar(resultado)
        return resultado

    async def segunda_via(self, *args: Any, **kwargs: Any) -> Any:
        fatos, credenciais = await self._inner.segunda_via(*args, **kwargs)
        self._gravar(fatos)
        return fatos, credenciais

    def __getattr__(self, nome: str) -> Any:
        return getattr(self._inner, nome)


# ================================================================================================
# 4. Identidade da Helena
# ================================================================================================
class IdentidadeGravada:
    """Proxy da `IdentidadeHelena`: delega tudo e registra o desfecho fechado de cada resolucao."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def resolver_com_desfecho(self, numero: str, *, conversation_id: str, pseudo_id: str) -> Any:
        resultado = await self._inner.resolver_com_desfecho(
            numero, conversation_id=conversation_id, pseudo_id=pseudo_id
        )
        contexto = _CONTEXTO.get()
        if contexto is not None:
            contexto.turno.identidade.append(str(getattr(resultado, "desfecho", "")))
        return resultado

    def invalidar(self, conversation_id: str) -> None:
        self._inner.invalidar(conversation_id)

    async def aclose(self) -> None:
        await self._inner.aclose()

    def __getattr__(self, nome: str) -> Any:
        return getattr(self._inner, nome)


# ================================================================================================
# 5. Consentimento
# ================================================================================================
@dataclass
class ContagemDeConsentimento:
    gravados: int = 0
    nao_gravados: int = 0


class ConsentimentoControlado:
    """`ConsentRecordPort` que so' deixa gravar na AMH quem o modo permite (ver docstring do modulo)."""

    def __init__(self, inner: Any, *, modo: ModoConsentimento, refs_completas: frozenset[str]) -> None:
        if modo not in MODOS_CONSENTIMENTO:
            raise ValueError("consentimento: modo desconhecido")
        self._inner = inner
        self._modo = modo
        self._refs_completas = refs_completas
        self.contagem = ContagemDeConsentimento()

    def permite(self, portable_subject_ref: str) -> bool:
        return self._modo == "completo" or portable_subject_ref in self._refs_completas

    async def record(self, portable_subject_ref: str, **kwargs: Any) -> Any:
        if not self.permite(portable_subject_ref):
            self.contagem.nao_gravados += 1
            logger.info(
                "simulador_consentimento_nao_gravado", modo=self._modo, decisao=kwargs.get("decision")
            )
            return PortResult.refused(
                PortFailureReason.UPSTREAM_UNAVAILABLE, detail="simulador-somente-leitura"
            )
        self.contagem.gravados += 1
        return await self._inner.record(portable_subject_ref, **kwargs)


# ================================================================================================
# A composicao
# ================================================================================================
class ComposicaoInvalidaError(RuntimeError):
    """O despachante real nao subiu, ou uma troca nao encontrou a peca que esperava."""


@dataclass
class Composicao:
    estado: WebhookState
    dispatcher: HelenaDispatcher
    captura: CapturaDeEnvios
    escalonamentos: RegistroDeEscalonamentos
    #: O transporte do motor ORIGINAL (gated) do despachante — so' para conferir, na limpeza, que o
    #: motor nao tem caso das conversas simuladas.
    motor_real: Any
    consentimento: ConsentimentoControlado | None
    #: Resolve uma pessoa do roster na AMH (preflight).
    resolver: Callable[[Sujeito], Awaitable[str | None]] | None

    @property
    def tenant_id(self) -> str:
        return self.dispatcher.tenant_id

    @property
    def saver(self) -> Any:
        return self.dispatcher.checkpointer.saver if self.dispatcher.checkpointer is not None else None

    async def aclose(self) -> None:
        """Mesma drenagem do `service.run` (motor, Lucas, identidade, acesso, checkpointer, dedup)."""
        estado = self.estado
        fechamentos: list[Any] = [
            estado.cibseven_transport.close() if estado.cibseven_transport is not None else None,
            estado.lucas_turno.aclose() if estado.lucas_turno is not None else None,
            estado.helena_identidade.aclose() if estado.helena_identidade is not None else None,
            estado.acesso.aclose() if estado.acesso is not None else None,
            estado.checkpointer.aclose() if estado.checkpointer is not None else None,
            self.dispatcher.dedup.aclose() if self.dispatcher.dedup is not None else None,
        ]
        for fechamento in fechamentos:
            if fechamento is None:
                continue
            try:
                await fechamento
            except Exception:
                logger.warning("simulador_drenagem_falhou", exc_info=True)


def _reembrulhar_motor(transporte: Any, registro: RegistroDeEscalonamentos) -> Any:
    if not isinstance(transporte, GatedSeam):
        raise ComposicaoInvalidaError("simulador: o motor do agente nao e' um seam cercado")
    return gate_cibseven(InterceptorDeEscalonamento(transporte.inner, registro), transporte.seam_context)


def resolvedor_de_documento(acesso: Any) -> Callable[[Sujeito], Awaitable[str | None]]:
    """O preflight: resolve a pessoa pelo MESMO contrato do fluxo de acesso (`resolve-by-document`),
    com o hasher, o esquema e a finalidade que o proprio acesso usa. Nunca registra o CPF."""
    documentos = getattr(acesso, "_documentos", None)
    hasher = getattr(acesso, "_hasher", None)
    if documentos is None or hasher is None:
        raise ComposicaoInvalidaError("simulador: o acesso nao tem a porta de documento (DL-0084)")

    async def resolver(sujeito: Sujeito) -> str | None:
        hash_cpf = hasher.hash_cpf(sujeito.cpf)
        hash_cpfdob = hasher.hash_cpf_nascimento(sujeito.cpf, sujeito.nascimento.strftime("%Y%m%d"))
        if not hash_cpf or not hash_cpfdob:
            return None
        resultado = await documentos.resolve_by_document(
            document_hash=hash_cpf,
            document_dob_hash=hash_cpfdob,
            hash_scheme=acesso._hash_scheme,
            purpose_of_use=acesso._purpose,
            amh_tenant=acesso._tenant_amh,
            timeout_seconds=acesso._prazo,
        )
        if not resultado.succeeded or resultado.value is None:
            return None
        valor = resultado.value
        return valor.portable_subject_ref if valor.resultado == "unico" else None

    return resolver


def trocar_bordas(
    estado: WebhookState,
    *,
    modo_consentimento: ModoConsentimento,
    refs_completas: frozenset[str] = frozenset(),
) -> Composicao:
    """As cinco trocas sobre um `WebhookState` ja' composto (separado de `compor` para teste)."""
    dispatcher = estado.dispatcher
    if dispatcher is None:
        raise ComposicaoInvalidaError(f"simulador: o despachante real nao subiu: {estado.dispatcher_error}")
    dedup = dispatcher.dedup
    captura = CapturaDeEnvios(
        chave_de_saida=(lambda mid, n: dedup.outbound_key(mid, occurrence=n)) if dedup is not None else None
    )
    dispatcher.whatsapp_client = captura  # type: ignore[assignment]
    dispatcher.recipient_vault = None

    registro = RegistroDeEscalonamentos()
    motor_real = dispatcher.cibseven
    dispatcher.cibseven = _reembrulhar_motor(dispatcher.cibseven, registro)
    lucas = estado.lucas_turno
    if lucas is not None:
        lucas.cibseven = _reembrulhar_motor(lucas.cibseven, registro)
        lucas.fonte = FonteGravada(lucas.fonte)
        if getattr(lucas, "fonte_v4", None) is not None:
            lucas.fonte_v4 = FonteV4Gravada(lucas.fonte_v4)  # type: ignore[assignment]
    if dispatcher.identidade is not None:
        dispatcher.identidade = IdentidadeGravada(dispatcher.identidade)  # type: ignore[assignment]

    consentimento: ConsentimentoControlado | None = None
    resolver: Callable[[Sujeito], Awaitable[str | None]] | None = None
    acesso = dispatcher.acesso
    if acesso is not None:
        consentimento = ConsentimentoControlado(
            acesso._consents, modo=modo_consentimento, refs_completas=refs_completas
        )
        acesso._consents = consentimento
        resolver = resolvedor_de_documento(acesso)

    # A mesma assercao de boot do receptor, DEPOIS das trocas: nenhuma costura de efeito sem gate.
    mapas = [{"dmn": dispatcher.dmn, "cibseven": dispatcher.cibseven, "inference": dispatcher.inference}]
    if lucas is not None:
        mapas.append({"dmn": lucas.dmn, "cibseven": lucas.cibseven, "inference": lucas.inference})
    for costuras in mapas:
        cercadas, detalhe = effect_seams_gated(costuras)
        if not cercadas:
            raise ComposicaoInvalidaError(f"simulador: costura sem gate depois das trocas: {detalhe}")
    return Composicao(
        estado=estado,
        dispatcher=dispatcher,
        captura=captura,
        escalonamentos=registro,
        motor_real=motor_real,
        consentimento=consentimento,
        resolver=resolver,
    )


async def compor(
    settings: WhatsAppWebhookSettings,
    *,
    modo_consentimento: ModoConsentimento,
    refs_completas: frozenset[str] = frozenset(),
) -> Composicao:
    """O despachante real (`build_live_dispatcher`) + as trocas. Recusa subir pela metade."""
    estado = await build_live_dispatcher(settings)
    if estado.dispatcher is None:
        raise ComposicaoInvalidaError(f"simulador: o despachante real nao subiu: {estado.dispatcher_error}")
    if estado.dispatcher.checkpointer is None:
        raise ComposicaoInvalidaError("simulador: sem checkpointer duravel nao ha' conversa de varios turnos")
    return trocar_bordas(estado, modo_consentimento=modo_consentimento, refs_completas=refs_completas)


def autorizar_consentimento(composicao: Composicao, refs: frozenset[str]) -> None:
    """Depois do preflight: as refs (do dono) que podem gravar consentimento no modo somente-leitura."""
    if composicao.consentimento is not None:
        composicao.consentimento._refs_completas = refs


def inferencia_crua(composicao: Composicao) -> Any:
    """O provedor de inferencia REAL dos agentes (mesmo modelo, mesma regiao), sem o gate do
    principal `helena`: a persona e o juiz nao sao a Helena e nao devem aparecer como chamadas dela.
    Nao constroi provedor nenhum (o registro e' o unico que constroi)."""
    inferencia = composicao.dispatcher.inference
    return inferencia.inner if isinstance(inferencia, GatedSeam) else inferencia


__all__ = [
    "MODOS_CONSENTIMENTO",
    "PROCESSO_ESCALONAMENTO",
    "CapturaDeEnvios",
    "Composicao",
    "ComposicaoInvalidaError",
    "ConsentimentoControlado",
    "ContextoDoTurno",
    "DestinoForaDaFaixaError",
    "FonteGravada",
    "FonteV4Gravada",
    "IdentidadeGravada",
    "InterceptorDeEscalonamento",
    "ModoConsentimento",
    "RegistroDeEscalonamentos",
    "autorizar_consentimento",
    "compor",
    "fatos_como_dict",
    "inferencia_crua",
    "no_turno",
    "resolvedor_de_documento",
    "trocar_bordas",
    "turno_atual",
]
