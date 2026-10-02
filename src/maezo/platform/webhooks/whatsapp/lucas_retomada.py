"""Retomada do Lucas — o caminho de VOLTA ao WhatsApp quando o atendente devolve um caso de cobranca.

O PROBLEMA (medido em dev em 02/10/2026). O portal oferece "Devolvo ao agente, com instrucao" para
qualquer caso de SP-OP-ESCALATION-001, e o `agent-resume` so' registrava a Helena. Um caso aberto pelo
Lucas devolvido pelo atendente acabava em `agent_resume.no_resumer_for_agent` ("o caso foi devolvido e
NINGUEM retomou a conversa"): a instrucao do humano nunca chegava a quem perguntou.

O QUE ESTA PORTA FAZ. Recebe o que o `ResumeHandler` ja' validou (conversa deste tenant, numero que
bate com o hash keyed, janela de 24h aberta, instrucao lida do HISTORICO do motor) e envia UMA mensagem:
a instrucao do humano, entre aspas e atribuida a "um profissional da nossa equipe", num modelo fixo.

O QUE ELA NAO FAZ, e e' proposital:
  * NAO chama modelo, DMN nem motor. A retomada e' deterministica: a instrucao chega COMO ESCRITA
    (mesma escolha da Helena, opcao B aprovada pelo dono em 25/09/2026). Nao ha' rascunho para o modelo
    errar e nao ha' processo para abrir.
  * NAO decide nada de cobranca. Nenhuma das proibicoes do Lucas e' afrouxada: o texto final passa
    pelas MESMAS cercas de saida do grafo (`lucas.prompts.motivo_de_recusa`, rota `retomada`, sem fatos:
    desfecho adverso, capacidade, valor sem fato e promessa de humano). Texto recusado vira a constante
    `RETOMADA_RECUSADA_TEXTO`, e o desfecho conta a recusa.
  * NAO usa o numero fora desta chamada. O destino e' o `_ScopedWhatsAppSender` (numero cru so' aqui,
    recusado se `hash_phone(raw_to)` nao bater com a conversa), embrulhado com o gate do principal
    `lucas` — o envio decide e registra sob o Lucas, nunca sob a Helena.

CHAVE DE SAIDA. `lucas-resume:{resume_ref}` (a instancia do escalonamento), num espaco proprio: nao
colide com `lucas:{message_id}` (turno do beneficiario) nem com `resume:{resume_ref}` (retomada da
Helena), e uma entrega repetida do evento nao reenvia a instrucao.

REDACAO. O modelo abaixo e' PROPOSTA minha para o Lucas — o da Helena foi aprovado pelo dono. Ate' o
dono aprovar a frase, ela e' o texto de dev; a troca e' uma constante.
"""

from __future__ import annotations

import time
from typing import Any, Final

import structlog

from maezo.agents.helena.graph import motivo_de_recusa_da_retomada
from maezo.agents.lucas.prompts import motivo_de_recusa
from maezo.gateway.effect_pep import PHI_ZONE_GENERAL
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.seams import SeamContext
from maezo.gateway.seams.whatsapp import gate_whatsapp
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.metrics import classify_agent_error_type
from maezo.runtime.turn_telemetry import emit_turn_desfecho

from .dedup import WhatsAppDedupGuard
from .lucas_turno import AGENT_ID, RemetenteDoTurno, exigir_zona_geral
from .security import hash_phone

logger = structlog.get_logger(__name__)

#: A nota do portal tem 1..2000 caracteres (`portal/contracts/completions.py`) e e' depurada para 500
#: antes de ir ao motor; acima do teto aqui e' estado que nao devia existir, e nada e' enviado.
RETOMADA_MAX_INSTRUCOES: Final[int] = 2000

#: `response_kind` da retomada nas cercas do Lucas: NAO esta em `_ROTAS_QUE_PODEM_PROMETER_HUMANO`,
#: entao uma nota que prometa "um atendente vai ligar" e' barrada — o caso acabou de ser devolvido e
#: nenhum humano esta' com ele.
RESPONSE_KIND_RETOMADA: Final[str] = "retomada"

RETOMADA_TEMPLATE: Final[str] = (
    "Olá, aqui é o atendimento de cobrança do seu plano. Um profissional da nossa equipe "
    'revisou o seu caso e pediu que repassássemos: "{instrucoes}". '
    "Se precisar de algo mais, é só responder esta mensagem."
)

#: O texto que sai quando as cercas barram a instrucao do atendente. Medido no teste do Filipe em
#: 02/10/2026: a nota "Entraremos em contato." foi barrada (promessa de humano) e a pessoa, que tinha
#: pedido CANCELAMENTO, recebeu `RESPOSTA_INFORMATIVA_RECUSADA`, que manda "consultar os dados do seu
#: boleto" — texto de outra jornada. Este e' neutro: nao cita boleto, nao promete retorno e nao repete
#: a nota barrada.
RETOMADA_RECUSADA_TEXTO: Final[str] = (
    "Recebemos o retorno do seu atendimento, mas não foi possível repassar o texto por aqui. "
    "Para saber o andamento, fale com a central de atendimento do plano."
)

DESFECHO_RETOMADA_ENVIADA: Final[str] = "retomada_enviada"
DESFECHO_RETOMADA_RECUSADA: Final[str] = "retomada_recusada"
DESFECHO_RETOMADA_SEM_INSTRUCOES: Final[str] = "retomada_sem_instrucoes"
DESFECHO_RETOMADA_FALHA_ENVIO: Final[str] = "retomada_falha_envio"


class RetomadaDoLucasEnvioFalhouError(RuntimeError):
    """O envio da retomada falhou. PROPAGA para o consumidor, que nao confirma o offset: engolir a
    falha confirmaria uma retomada que nunca chegou e a instrucao do humano se perderia sem rastro.
    A chave de saida e' por instancia de escalonamento, entao a nova tentativa nao duplica."""


def compor_mensagem_de_retomada(instrucoes: str) -> str:
    """A mensagem ao beneficiario a partir das instrucoes humanas. PURA e deterministica."""
    limpo = " ".join(instrucoes.split()).replace('"', "'")
    return RETOMADA_TEMPLATE.format(instrucoes=limpo)


class _RemetenteDaRetomada:
    """O `WhatsAppSender` que o gate do Lucas embrulha: usa a chave da RETOMADA, nunca a do chamador."""

    def __init__(self, *, inner: RemetenteDoTurno, chave: str) -> None:
        self._inner = inner
        self._chave = chave

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del idempotency_key
        return await self._inner.send(to_hash, text, idempotency_key=self._chave)


class LucasRetomada:
    """A `AgentResumer` do Lucas (`platform/integrations/agent_resume.py::AgentResumer`)."""

    def __init__(
        self,
        *,
        tenant_id: str,
        pseudonymizer: Pseudonymizer,
        whatsapp_client: Any,
        dedup: WhatsAppDedupGuard | None,
        seam_context: SeamContext,
    ) -> None:
        if dedup is None:
            raise ValueError("lucas_retomada: sem guard de dedup nao ha' chave de saida duravel")
        if seam_context.principal != AGENT_ID or seam_context.tenant != tenant_id:
            raise ValueError("lucas_retomada: o SeamContext nao e' do principal lucas neste tenant")
        if seam_context.phi_zone != PHI_ZONE_GENERAL:
            raise ValueError("lucas_retomada: SeamContext fora da zona geral")
        exigir_zona_geral(AGENT_ID)
        self.tenant_id = tenant_id
        self._pseudonymizer = pseudonymizer
        self._client = whatsapp_client
        self._dedup = dedup
        self._seam_context = seam_context

    def _conferir_destino(self, conversation_id: str, raw_to: str) -> str:
        prefixo = f"wa:{self.tenant_id}:"
        phone_hash = conversation_id[len(prefixo) :] if conversation_id.startswith(prefixo) else ""
        if not phone_hash or ":" in phone_hash:
            raise ValueError(
                "lucas resume: conversation_id nao e' uma conversa de WhatsApp deste tenant — "
                "recusando retomar uma conversa que este processo nao possui"
            )
        if hash_phone(raw_to, self.tenant_id, self._pseudonymizer) != phone_hash:
            raise ValueError(
                "lucas resume: o destinatario nao bate com o hash keyed da conversa — recusando "
                "enviar a instrucao de um humano a um destino nao verificado"
            )
        return phone_hash

    def _emitir(self, desfecho: str, *, enviada: bool) -> None:
        emit_turn_desfecho(
            {},
            agent_id=AGENT_ID,
            desfecho=desfecho,
            route=RESPONSE_KIND_RETOMADA,
            motivo_categoria=None,
            enviada=enviada,
        )

    async def resume(
        self, *, conversation_id: str, instrucoes: str, raw_to: str, resume_ref: str
    ) -> dict[str, Any]:
        """UMA retomada. Devolve `{"desfecho": ...}`.

        Levanta `ValueError` (conversa alheia ou destinatario que nao bate) antes de qualquer efeito e
        `RetomadaDoLucasEnvioFalhouError` quando o envio falha (o consumidor NAO confirma o offset).
        """
        phone_hash = self._conferir_destino(conversation_id, raw_to)
        inicio = time.monotonic()
        nota = " ".join((instrucoes or "").split())
        if not nota or len(nota) > RETOMADA_MAX_INSTRUCOES:
            # Nada a repassar (ou nota fora do contrato): nao se inventa texto em nome do humano.
            logger.warning(
                "lucas_retomada_sem_instrucoes",
                tenant_id=self.tenant_id,
                conversation_id=conversation_id,
                vazia=not nota,
            )
            self._emitir(DESFECHO_RETOMADA_SEM_INSTRUCOES, enviada=False)
            return {"desfecho": DESFECHO_RETOMADA_SEM_INSTRUCOES}

        texto = compor_mensagem_de_retomada(nota)
        # DUAS camadas: as cercas do Lucas (desfecho adverso, capacidade, valor sem fato) e as da
        # retomada da Helena (canal nao confirmado e QUALQUER promessa/mencao de encaminhamento: o caso
        # acabou de ser devolvido e nenhum humano esta com ele). A frase "um atendente vai te ligar"
        # passa pela primeira e e' barrada pela segunda (medido no teste de 02/10/2026).
        recusa = motivo_de_recusa(texto, RESPONSE_KIND_RETOMADA, {}) or motivo_de_recusa_da_retomada(texto)
        desfecho = DESFECHO_RETOMADA_ENVIADA
        if recusa is not None:
            # Grupo e padrao vao para o log; o TEXTO recusado nunca (saida sobre a cobranca de alguem).
            logger.warning(
                "lucas_retomada_recusada",
                tenant_id=self.tenant_id,
                conversation_id=conversation_id,
                grupo=recusa[0],
            )
            texto = RETOMADA_RECUSADA_TEXTO
            desfecho = DESFECHO_RETOMADA_RECUSADA

        from .dispatch import _ScopedWhatsAppSender

        chave = self._dedup.outbound_key(f"lucas-resume:{resume_ref}", occurrence=1)
        remetente = gate_whatsapp(
            _RemetenteDaRetomada(
                inner=_ScopedWhatsAppSender(raw_to=raw_to, expected_hash=phone_hash, client=self._client),
                chave=chave,
            ),
            self._seam_context,
        )
        try:
            await remetente.send(phone_hash, texto, idempotency_key=chave)
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES as exc:
            from maezo.platform.observability import record_agent_error

            record_agent_error(agent=AGENT_ID, error_type=classify_agent_error_type(exc))
            self._emitir(DESFECHO_RETOMADA_FALHA_ENVIO, enviada=False)
            raise RetomadaDoLucasEnvioFalhouError(
                f"lucas resume: falha ao enviar a retomada ({type(exc).__name__})"
            ) from exc
        logger.info(
            "lucas_retomada_enviada",
            tenant_id=self.tenant_id,
            conversation_id=conversation_id,
            desfecho=desfecho,
            ms=int((time.monotonic() - inicio) * 1000),
        )
        self._emitir(desfecho, enviada=True)
        return {"desfecho": desfecho}
