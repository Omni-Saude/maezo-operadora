"""Entrada de mensagens no Lucas: UM turno do Lucas a partir do handoff tipado da Helena.

Onda (d) de `docs/plans/lucas-numero-unico.md` (§2.5, §3, §5; ADR-0062). Fecha o GAP 11.7 de
`spec/agents/lucas/agent.yaml` do unico jeito que o plano admite: o Lucas ganha entrada pelo
webhook unico, mas **nao recebe texto do beneficiario**.

O QUE ENTRA. Tres coisas, nenhuma com texto:
  - `handoff`: a saida tipada da Helena (`HandoffCobranca`, §2.4: `para`, `cobranca_subtipo`,
    `competencia`, `message_ref`). Dominio fechado, conferido aqui chave a chave; qualquer chave a
    mais (um `message_body` plantado, por exemplo) e' recusada. A Helena so' passa a emiti-lo na
    onda (e).
  - `conversa`: os identificadores do turno que o despachante ja' tem (`conversation_id` keyed,
    pseudonimo do beneficiario, hash do destino e o `message_id` de entrada, usado SO' para a chave
    de idempotencia de saida).
  - `sender`: o remetente do turno, que fecha sobre o numero cru como o `_ScopedWhatsAppSender` do
    despachante. O `LucasTurno` o embrulha com o gate de efeito do principal `lucas`, nunca o da
    Helena, e e' o `LucasTurno` quem escolhe a chave de cada envio.

A TABELA DE §2.5 (`ENTRADA_POR_SUBTIPO`) traduz o subtipo para a entrada estrita do Lucas
(`new_lucas_state`). `tipo_solicitacao` usa o vocabulario da DMN `lucas_billing_admissibility`
(`boleto`/`2a_via`/`vencimento`/`status_pagamento`, ou `""`), o MESMO que o corpus da onda (a)
(`tests/evals/lucas/casos.json`, campo `subtipo_handoff`) ja' fixa caso a caso. Passar o subtipo
cru (`boleto_2via`) mandaria toda 2a via para o catch-all da DMN, que escala: o teste
`test_lucas_turno.py::test_tabela_do_subtipo_e_a_do_corpus_da_onda_a` prende as duas pontas.

OS FATOS DE COBRANCA vem da porta `FonteCobranca`. Ate' existir a fonte real, e' a
`FonteCobrancaSimulada` (onda a), e so' ela e' aceita pelas settings (`MAEZO_LUCAS_FONTE_COBRANCA`).
`Indisponivel` nao inventa fato: os campos de conciliacao ficam ausentes e o catch-all da DMN
escala.

O GRAFO compila SEM checkpointer (§2.1): o Lucas e' de um turno so', e a continuidade dele sao as
colunas tipadas de `conversa_agente_ativo`, nunca um thread de checkpoint (dois grafos no mesmo
thread se sobrescrevem).

A CHAVE DE SAIDA e' `WhatsAppDedupGuard.outbound_key(f"lucas:{message_id}", occurrence=n)`: por
ENTREGA de entrada, como a da Helena, num espaco proprio (`lucas:`) para que um envio do Lucas e um
da Helena causados pela mesma mensagem nunca se suprimam. Uma reentrega do mesmo `message_id`
reclama a mesma chave, e o store duravel suprime o envio. A chave por impressao digital que o grafo
calcula (`_idempotency_key`) e' DESCARTADA aqui de proposito: ela existe porque o Lucas nao tinha
id de entrega, e agora tem.

ZONA (§3, ADR-0006). O Lucas e' `security_zone: general` e roda no processo do receptor, que e' zona
geral. `exigir_zona_geral` recusa construir o turno de um agente cuja definicao nao seja `general`
(ou nao carregue). O Lucas nao ve texto, e `test_lucas_turno.py` prova isso por varredura de chaves.

NESTA ONDA ninguem chama `executar` em producao: o despachante so' passa a chamar na onda (e), e a
cerca `scripts/ci/check_roteador_lucas.py` (item 5) ja' exige que `LucasTurno` so' seja construido
dentro do `if settings.roteador_lucas_enabled`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

import structlog

from maezo.agents.lucas.fonte_cobranca import FatosCobranca, FonteCobranca
from maezo.agents.lucas.graph import LucasState, build, new_lucas_state
from maezo.gateway.effect_pep import PHI_ZONE_GENERAL
from maezo.gateway.seams import SeamContext
from maezo.gateway.seams.whatsapp import gate_whatsapp
from maezo.platform.observability import record_agent_first_response
from maezo.runtime.competencia import competencia_valida
from maezo.runtime.inference import InferenceProvider
from maezo.runtime.metrics import classify_agent_error_type
from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
from maezo.tools.workers.dmn_transport import DmnTransport

from .dedup import WhatsAppDedupGuard
from .roteamento import COBRANCA_SUBTIPOS

logger = structlog.get_logger(__name__)

#: O principal deste turno. Literal unico: as metricas, o gate e a zona falam deste id.
AGENT_ID: Final[str] = "lucas"
#: Prefixo do espaco de chaves de saida do Lucas (ver docstring do modulo).
PREFIXO_CHAVE_SAIDA: Final[str] = "lucas:"

#: As chaves do `HandoffCobranca` (§2.4), nem uma a mais, nem uma a menos.
CHAVES_DO_HANDOFF: Final[frozenset[str]] = frozenset(
    {"para", "cobranca_subtipo", "competencia", "message_ref"}
)

_CONVERSATION_ID: Final[re.Pattern[str]] = re.compile(r"^wa:[^:]+:hk1_[0-9a-f]+$")

#: §2.5: subtipo do handoff -> entrada do Lucas. J1 = `cobranca_info` sem flag, J2 =
#: `confirmacao_pagamento`, J3 = contestacao/inadimplencia/cancelamento (sempre humano).
ENTRADA_POR_SUBTIPO: Final[Mapping[str, Mapping[str, Any]]] = {
    "boleto_2via": {"intencao": "cobranca_info", "tipo_solicitacao": "2a_via"},
    "vencimento": {"intencao": "cobranca_info", "tipo_solicitacao": "vencimento"},
    "outro": {"intencao": "cobranca_info", "tipo_solicitacao": ""},
    "confirmacao_pagamento": {"intencao": "confirmacao_pagamento", "tipo_solicitacao": "status_pagamento"},
    "contestacao": {"intencao": "cobranca_info", "tipo_solicitacao": "", "contesta_cobranca": True},
    "cobranca_recebida": {"intencao": "inadimplencia", "tipo_solicitacao": ""},
    "cancelamento": {"intencao": "cancelamento", "tipo_solicitacao": "", "pedido_cancelamento": True},
}
if frozenset(ENTRADA_POR_SUBTIPO) != frozenset(COBRANCA_SUBTIPOS):
    raise RuntimeError(
        "lucas_turno: a tabela de §2.5 nao cobre exatamente o dominio de `cobranca_subtipo` "
        f"(faltam={sorted(frozenset(COBRANCA_SUBTIPOS) - frozenset(ENTRADA_POR_SUBTIPO))}, "
        f"sobram={sorted(frozenset(ENTRADA_POR_SUBTIPO) - frozenset(COBRANCA_SUBTIPOS))})"
    )


class HandoffInvalidoError(ValueError):
    """O handoff nao tem a forma de §2.4. A mensagem e' token de classe, nunca valor ecoado."""


class ZonaDeSegurancaError(RuntimeError):
    """O agente nao e' `security_zone: general` e nao pode rodar neste processo (§3)."""


@dataclass(frozen=True, slots=True)
class HandoffValidado:
    cobranca_subtipo: str
    competencia: str | None
    message_ref: str


@dataclass(frozen=True, slots=True)
class ConversaDoTurno:
    """Os identificadores do turno. Nenhum campo carrega texto nem telefone em claro.

    `message_id` e' o id de entrega de ENTRADA; ele so' alimenta a chave de idempotencia de saida
    (que o pseudonimiza) e nunca vai para log nem para o estado do Lucas.
    """

    conversation_id: str
    beneficiario_pseudo_id: str
    to_hash: str
    message_id: str


class RemetenteDoTurno(Protocol):
    """O remetente de UM turno, sobre o numero cru (como o `_ScopedWhatsAppSender`). A chave e'
    obrigatoria: quem decide qual e' o `LucasTurno`, nunca o remetente."""

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]: ...


def validar_handoff(handoff: Mapping[str, Any]) -> HandoffValidado:
    """Confere o `HandoffCobranca` chave a chave. Fail-closed: forma errada nao vira turno."""
    if not isinstance(handoff, Mapping):
        raise HandoffInvalidoError("lucas_turno: handoff nao e' um mapeamento")
    if frozenset(handoff) != CHAVES_DO_HANDOFF:
        # So' os NOMES das chaves, e so' os que sobram ou faltam: um valor plantado nunca ecoa.
        raise HandoffInvalidoError(
            "lucas_turno: chaves do handoff fora de §2.4 "
            f"(sobram={sorted(frozenset(handoff) - CHAVES_DO_HANDOFF)}, "
            f"faltam={sorted(CHAVES_DO_HANDOFF - frozenset(handoff))})"
        )
    if handoff["para"] != AGENT_ID:
        raise HandoffInvalidoError("lucas_turno: handoff nao e' para o lucas")
    subtipo = handoff["cobranca_subtipo"]
    if not isinstance(subtipo, str) or subtipo not in ENTRADA_POR_SUBTIPO:
        raise HandoffInvalidoError("lucas_turno: cobranca_subtipo fora do dominio")
    competencia = handoff["competencia"]
    if competencia is not None and not competencia_valida(competencia):
        raise HandoffInvalidoError("lucas_turno: competencia fora do formato YYYY-MM")
    message_ref = handoff["message_ref"]
    if not isinstance(message_ref, str) or not message_ref:
        raise HandoffInvalidoError("lucas_turno: message_ref ausente")
    return HandoffValidado(cobranca_subtipo=subtipo, competencia=competencia, message_ref=message_ref)


def exigir_zona_geral(agent_id: str) -> None:
    """§3: so' um agente `security_zone: general` roda neste processo. Recusa no BOOT.

    Le' a definicao do proprio `agent.yaml`, e nao o `SeamContext`: este cai em `general` quando a
    definicao nao carrega (`build_agent_seam_context` e' nao-fatal por desenho), e aqui a ausencia
    tem de RECUSAR, nunca passar por geral.
    """
    from maezo.agents import AgentLoader

    try:
        zona = AgentLoader().load_by_id(agent_id).security_zone
    except Exception as exc:
        raise ZonaDeSegurancaError(
            f"lucas_turno: definicao de {agent_id!r} nao carregou ({type(exc).__name__}) — sem zona "
            "declarada, o agente nao entra no processo do receptor"
        ) from exc
    if zona != PHI_ZONE_GENERAL:
        raise ZonaDeSegurancaError(
            f"lucas_turno: {agent_id!r} declara security_zone={zona!r}; o receptor e' zona geral e "
            "so' admite agente `general` (ADR-0006, plano §3)"
        )


def entrada_do_lucas(
    handoff: HandoffValidado, conversa: ConversaDoTurno, *, tenant_id: str, fatos: FatosCobranca | None
) -> LucasState:
    """A entrada ESTRITA do turno (`new_lucas_state`): identificadores, a linha de §2.5 e os fatos
    da fonte. Nenhuma chave de texto existe para ser preenchida."""
    entrada: dict[str, Any] = {
        "tenant_id": tenant_id,
        "conversation_id": conversa.conversation_id,
        "canal": "whatsapp",
        "beneficiario_pseudo_id": conversa.beneficiario_pseudo_id,
        "to_hash": conversa.to_hash,
        **ENTRADA_POR_SUBTIPO[handoff.cobranca_subtipo],
    }
    if handoff.competencia is not None:
        entrada["competencia"] = handoff.competencia
    if fatos is not None:
        entrada.update(fatos.como_entrada_lucas())
    return new_lucas_state(entrada)


class _RemetenteDoLucas:
    """O `WhatsAppSender` que o grafo do Lucas recebe: troca a chave do grafo pela chave por
    entrega (`lucas:{message_id}`, ordinal n) e entrega ao remetente do turno."""

    def __init__(self, *, inner: RemetenteDoTurno, dedup: WhatsAppDedupGuard, message_id: str) -> None:
        self._inner = inner
        self._dedup = dedup
        self._message_id = message_id
        self._envios = 0

    def chave(self, ordinal: int) -> str:
        return self._dedup.outbound_key(f"{PREFIXO_CHAVE_SAIDA}{self._message_id}", occurrence=ordinal)

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del idempotency_key  # a do grafo (impressao digital); ver docstring do modulo
        self._envios += 1
        return await self._inner.send(to_hash, text, idempotency_key=self.chave(self._envios))


@dataclass
class LucasTurno:
    """Construido UMA vez no boot do receptor, so' com o roteador ligado (`service.py`)."""

    tenant_id: str
    inference: InferenceProvider
    dmn: DmnTransport
    cibseven: CibSevenTransport
    audit_sink: AuditStartSink
    #: O `SeamContext` do principal `lucas`: o gate do envio decide e registra sob o Lucas.
    seam_context: SeamContext
    #: O MESMO guard da Helena (uma chave derivada de um pseudonimizador so').
    dedup: WhatsAppDedupGuard
    fonte: FonteCobranca
    agent_version: str = "lucas@v0"

    def __post_init__(self) -> None:
        if self.seam_context.principal != AGENT_ID or self.seam_context.tenant != self.tenant_id:
            raise ValueError("lucas_turno: o SeamContext nao e' do principal lucas neste tenant")
        if self.seam_context.phi_zone != PHI_ZONE_GENERAL:
            raise ZonaDeSegurancaError("lucas_turno: SeamContext fora da zona geral")
        exigir_zona_geral(AGENT_ID)

    def _compilar(self, sender: Any) -> Any:
        grafo = build(
            {
                "inference": self.inference,
                "dmn": self.dmn,
                "cibseven": self.cibseven,
                "whatsapp": sender,
                "audit_sink": self.audit_sink,
                "agent_version": self.agent_version,
            }
        )
        # SEM checkpointer, explicito (§2.1): o Lucas e' de um turno so'.
        return grafo.compile(checkpointer=None)

    def _conferir_conversa(self, conversa: ConversaDoTurno) -> None:
        if not _CONVERSATION_ID.match(conversa.conversation_id) or not conversa.conversation_id.startswith(
            f"wa:{self.tenant_id}:"
        ):
            raise ValueError("lucas_turno: conversation_id fora do formato keyed deste tenant")
        if not conversa.message_id:
            raise ValueError("lucas_turno: message_id ausente — sem ele nao ha' chave de saida")
        if not conversa.to_hash or not conversa.beneficiario_pseudo_id:
            raise ValueError("lucas_turno: identificadores do beneficiario ausentes")

    async def _fatos(self, conversa: ConversaDoTurno, handoff: HandoffValidado) -> FatosCobranca | None:
        resultado = await self.fonte.fatos(conversa.beneficiario_pseudo_id, handoff.competencia)
        if isinstance(resultado, FatosCobranca):
            return resultado
        logger.warning(
            "lucas_turno_fonte_indisponivel",
            tenant_id=self.tenant_id,
            conversation_id=conversa.conversation_id,
            motivo=resultado.motivo,
        )
        return None

    async def executar(
        self, handoff: Mapping[str, Any], conversa: ConversaDoTurno, sender: RemetenteDoTurno
    ) -> dict[str, Any]:
        """UM turno do Lucas. Devolve o estado final do grafo.

        Levanta `HandoffInvalidoError`/`ValueError` ANTES de qualquer efeito quando a entrada nao
        tem a forma do contrato: um handoff malformado nunca vira turno do Lucas.
        """
        inicio = time.monotonic()
        try:
            validado = validar_handoff(handoff)
            self._conferir_conversa(conversa)
            estado = entrada_do_lucas(
                validado, conversa, tenant_id=self.tenant_id, fatos=await self._fatos(conversa, validado)
            )
        except Exception as exc:
            # A falha ANTES do grafo (forma do handoff/conversa, fonte) tambem e' erro do agente
            # `lucas` (review do #589); a do grafo e' contada no `ainvoke` abaixo. Um ponto por
            # falha, e o despachante, que trata a falha, nao conta de novo.
            from maezo.platform.observability import record_agent_error

            record_agent_error(agent=AGENT_ID, error_type=classify_agent_error_type(exc))
            raise
        remetente = gate_whatsapp(
            _RemetenteDoLucas(inner=sender, dedup=self.dedup, message_id=conversa.message_id),
            self.seam_context,
        )
        compilado = self._compilar(remetente)
        logger.info(
            "lucas_turno_iniciado",
            tenant_id=self.tenant_id,
            conversation_id=conversa.conversation_id,
            cobranca_subtipo=validado.cobranca_subtipo,
            intencao=estado.get("intencao"),
            competencia_presente=validado.competencia is not None,
        )
        try:
            final = await compilado.ainvoke(estado)
        except Exception as exc:
            from maezo.platform.observability import record_agent_error

            record_agent_error(agent=AGENT_ID, error_type=classify_agent_error_type(exc))
            raise
        record_agent_first_response(agent_id=AGENT_ID, seconds=time.monotonic() - inicio)
        logger.info(
            "lucas_turno_concluido",
            tenant_id=self.tenant_id,
            conversation_id=conversa.conversation_id,
            route=final.get("route"),
            desfecho=final.get("desfecho"),
            process_started=final.get("process_started"),
            mensagem_enviada=final.get("mensagem_enviada"),
        )
        return dict(final)

    async def aclose(self) -> None:
        """Drenagem: fecha o transporte do motor do Lucas (o da Helena e' fechado a parte)."""
        close = getattr(self.cibseven, "close", None)
        if close is not None:
            await close()


__all__ = [
    "AGENT_ID",
    "CHAVES_DO_HANDOFF",
    "ENTRADA_POR_SUBTIPO",
    "PREFIXO_CHAVE_SAIDA",
    "ConversaDoTurno",
    "HandoffInvalidoError",
    "HandoffValidado",
    "LucasTurno",
    "RemetenteDoTurno",
    "ZonaDeSegurancaError",
    "entrada_do_lucas",
    "exigir_zona_geral",
    "validar_handoff",
]
