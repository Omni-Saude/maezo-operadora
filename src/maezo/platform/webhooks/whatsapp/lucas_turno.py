"""Entrada de mensagens no Lucas: UM turno do Lucas a partir do handoff tipado da Helena.

Onda (d) de `docs/plans/lucas-numero-unico.md` (§2.5, §3, §5; ADR-0062). Fecha o GAP 11.7 de
`spec/agents/lucas/agent.yaml` do unico jeito que o plano admite: o Lucas ganha entrada pelo
webhook unico, mas **nao recebe texto do beneficiario**.

O QUE ENTRA. Tres coisas, nenhuma com texto:
  - `handoff`: a saida tipada da Helena (`HandoffCobranca`, §2.4: `para`, `cobranca_subtipo`,
    `competencia`, `cobranca_foco` (DL-0087), `message_ref` e, opcional, `cobranca_lista` — o pedido de
    varios meses, 12/10/2026). Dominio fechado, conferido aqui chave a
    chave; qualquer chave a mais (um `message_body` plantado, por exemplo) e' recusada. A Helena
    so' passa a emiti-lo na onda (e).
  - `conversa`: os identificadores do turno que o despachante ja' tem (`conversation_id` keyed,
    pseudonimo do beneficiario, hash do destino e o `message_id` de entrada, usado SO' para a chave
    de idempotencia de saida).
  - `sender`: o remetente do turno, que fecha sobre o numero cru como o `_ScopedWhatsAppSender` do
    despachante. O `LucasTurno` o embrulha com o gate de efeito do principal `lucas`, nunca o da
    Helena, e e' o `LucasTurno` quem escolhe a chave de cada envio.

A TABELA DE §2.5 (`ENTRADA_POR_SUBTIPO`) traduz o subtipo para a entrada estrita do Lucas
(`new_lucas_state`). `tipo_solicitacao` usa o vocabulario da DMN `lucas_billing_admissibility`
(`boleto`/`2a_via`/`vencimento`/`status_pagamento`/`consulta_valores`, ou `""`), o MESMO que o corpus
da onda (a) (`tests/evals/lucas/casos.json`, campo `subtipo_handoff`) ja' fixa caso a caso. Passar o subtipo
cru (`boleto_2via`) mandaria toda 2a via para o catch-all da DMN, que escala: o teste
`test_lucas_turno.py::test_tabela_do_subtipo_e_a_do_corpus_da_onda_a` prende as duas pontas.

OS FATOS DE COBRANCA vem da porta `FonteCobranca`: a `FonteCobrancaSimulada` (onda a, o default)
ou, com `MAEZO_LUCAS_FONTE_COBRANCA=amh`, a `FonteCobrancaAmh` sobre os contratos da AMH (decisao do
dono de 06/10/2026; so' sobe com os contratos publicados e pinados). A fonte AMH resolve o sujeito
pelo hash `amh-phone-lookup-v1` do numero, que o despachante calcula no recebimento
(`hash_telefone_para_amh`) e entrega em `ConversaDoTurno.phone_hash_amh`.
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

QUEM CHAMA `executar`: so' `HelenaDispatcher._turno_do_lucas` (onda e), depois dos lexicos e do
`ainvoke` da Helena. A cerca `scripts/ci/check_roteador_lucas.py` prova isso estaticamente (item 7),
alem de exigir que `LucasTurno` so' seja construido dentro do `if settings.roteador_lucas_enabled`
(item 5) e que o handoff so' nasca no no' `handoff_cobranca` da Helena (item 6). O HANDOFF E' DESTA
MENSAGEM (onda f): `executar` recusa um `message_ref` que nao seja o pseudonimo keyed do id de
entrega deste turno (`HandoffDeOutraMensagemError`).
"""

from __future__ import annotations

import hmac
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import structlog

from maezo.agents.lucas.cancelamento_pf import (
    FatosCancelamento,
    FatosIndisponiveis,
    FonteCancelamento,
    tipo_plano_do_billing,
)
from maezo.agents.lucas.fonte_cobranca import FatosCobranca, FonteCobranca
from maezo.agents.lucas.graph import (
    LucasState,
    build,
    competencias_da_resposta,
    new_lucas_state,
    ref_de_resposta,
)
from maezo.agents.lucas.politica_atraso import LimiaresDeAtraso
from maezo.gateway.effect_pep import PHI_ZONE_GENERAL
from maezo.gateway.seams import SeamContext
from maezo.gateway.seams.whatsapp import gate_whatsapp
from maezo.platform.observability import record_agent_first_response
from maezo.runtime.cobranca_foco import foco_valido
from maezo.runtime.cobranca_intencao import confirmacao_valida
from maezo.runtime.cobranca_lista import PedidoDeLista, lista_do_handoff
from maezo.runtime.cobranca_reclamacao import reclamacao_valida
from maezo.runtime.competencia import competencia_valida
from maezo.runtime.inference import InferenceProvider
from maezo.runtime.metrics import classify_agent_error_type
from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
from maezo.tools.workers.dmn_transport import DmnTransport

from .dedup import WhatsAppDedupGuard
from .roteamento import COBRANCA_SUBTIPOS, MemoriaDoLucas

logger = structlog.get_logger(__name__)

#: O principal deste turno. Literal unico: as metricas, o gate e a zona falam deste id.
AGENT_ID: Final[str] = "lucas"
#: Prefixo do espaco de chaves de saida do Lucas (ver docstring do modulo).
PREFIXO_CHAVE_SAIDA: Final[str] = "lucas:"

#: As chaves do `HandoffCobranca` (§2.4), nem uma a mais, nem uma a menos. `cobranca_foco` (DL-0087,
#: 10/10/2026): o campo perguntado, enum fechado decidido pela Helena sobre o texto — nunca o texto.
CHAVES_DO_HANDOFF: Final[frozenset[str]] = frozenset(
    {"para", "cobranca_subtipo", "competencia", "cobranca_foco", "message_ref"}
)
#: Chaves OPCIONAIS do handoff. `cobranca_lista` (12/10/2026): o pedido de varios meses
#: (`runtime/cobranca_lista.py`), que o despachante acrescenta em `_turno_do_lucas` quando o texto pede
#: lista. Ausente = um mes so', o comportamento de antes.
#: Rodada 12 (14/10/2026): `cobranca_negociacao` (negociar/parcelar; `True`) e `cobranca_confirmacao`
#: ("confirma"/"nega", a resposta a "Voce quer pedir o cancelamento do seu plano?") — enums fechados
#: decididos sobre o texto (`runtime/cobranca_intencao.py`), nunca o texto.
CHAVES_OPCIONAIS_DO_HANDOFF: Final[frozenset[str]] = frozenset(
    {"cobranca_lista", "cobranca_reclamacao", "cobranca_negociacao", "cobranca_confirmacao"}
)

_CONVERSATION_ID: Final[re.Pattern[str]] = re.compile(r"^wa:[^:]+:hk1_[0-9a-f]+$")

#: §2.5: subtipo do handoff -> entrada do Lucas. J1 = `cobranca_info` sem flag, J2 =
#: `confirmacao_pagamento`, J3 = contestacao/cancelamento (sempre humano).
#:
#: `cobranca_recebida` (DL-0082, 08/10/2026): a intencao `inadimplencia` fica como DICA de jornada,
#: mas o `tipo_solicitacao` e' `status_pagamento` — "minha mensalidade esta em aberto?" e', para a
#: DMN, uma pergunta de status de pagamento. Com `""` a DMN so' teria o catch-all (humano) mesmo com
#: o fato "conciliado"; com `status_pagamento` conciliado responde "em dia" e atraso escala.
#:
#: `consulta_valores` (DL-0086, 08/10/2026): pergunta de VALOR (mensalidade, coparticipacao, saldo,
#: historico, data de pagamento) e' J1 (`cobranca_info`) com `tipo_solicitacao=consulta_valores`: a DMN
#: responde pelos fatos do `billing-status` quando eles vieram e escala quando nao vieram ou ha' atraso.
ENTRADA_POR_SUBTIPO: Final[Mapping[str, Mapping[str, Any]]] = {
    "boleto_2via": {"intencao": "cobranca_info", "tipo_solicitacao": "2a_via"},
    "vencimento": {"intencao": "cobranca_info", "tipo_solicitacao": "vencimento"},
    "outro": {"intencao": "cobranca_info", "tipo_solicitacao": ""},
    "confirmacao_pagamento": {"intencao": "confirmacao_pagamento", "tipo_solicitacao": "status_pagamento"},
    "contestacao": {"intencao": "cobranca_info", "tipo_solicitacao": "", "contesta_cobranca": True},
    "cobranca_recebida": {"intencao": "inadimplencia", "tipo_solicitacao": "status_pagamento"},
    "consulta_valores": {"intencao": "cobranca_info", "tipo_solicitacao": "consulta_valores"},
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


class HandoffDeOutraMensagemError(HandoffInvalidoError):
    """O `message_ref` do handoff nao e' o da mensagem de entrada DESTE turno (onda f).

    Um handoff velho nao se reaproveita: ir ao Lucas exige a saida tipada da Helena neste mesmo
    request (§2.3), e e' o `message_ref` que amarra a saida a mensagem. O despachante trata esta
    recusa como `helena`/`retorno_falha`, SEM envio do Lucas.
    """


class ZonaDeSegurancaError(RuntimeError):
    """O agente nao e' `security_zone: general` e nao pode rodar neste processo (§3)."""


@dataclass(frozen=True, slots=True)
class HandoffValidado:
    cobranca_subtipo: str
    competencia: str | None
    message_ref: str
    #: DL-0087: o campo perguntado (`runtime/cobranca_foco.py`). Nao e' persistido pelo roteador.
    cobranca_foco: str = "geral"
    #: 12/10/2026: o pedido de varios meses (`runtime/cobranca_lista.py`); `None` = um mes so'.
    cobranca_lista: PedidoDeLista | None = None
    #: Rodada 4 (13/10/2026): o tipo da reclamacao (`runtime/cobranca_reclamacao.py`); `None` = nenhum.
    cobranca_reclamacao: str | None = None
    #: Rodada 12: pedido de negociacao/parcelamento (`True`) e confirmacao do cancelamento (enum).
    cobranca_negociacao: bool = False
    cobranca_confirmacao: str | None = None


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
    #: Hash `amh-phone-lookup-v1` do numero (decisao do dono 06/10/2026), calculado pelo despachante
    #: no RECEBIMENTO e SO' com a fonte AMH ligada; `None` caso contrario. E' a chave com que a AMH
    #: resolve o sujeito — o `to_hash` (`hk1_`, chave do Maezo) nao significa nada para ela. Vive so'
    #: neste turno: nunca vai para log, estado do grafo nem armazenamento (`repr=False`).
    phone_hash_amh: str | None = field(default=None, repr=False)
    #: 13/10/2026 (simulador): a variante de redacao do turno (a revisao do roteador antes dele) e o
    #: assunto da resposta ANTERIOR do Lucas (`cobranca_subtipo`, so' com ele ainda com a conversa). Os
    #: dois so' evitam o mesmo texto duas vezes seguidas; nenhum e' texto do beneficiario.
    variante_de_redacao: int = 0
    assunto_anterior: str | None = None
    #: Rodada 3 (13/10/2026): a memoria curta do Lucas lida do roteador (migration 0025) — a referencia
    #: (hash truncado) do ultimo texto enviado e os avisos de caso aberto ja' dados. Nenhum texto.
    resposta_anterior_ref: str | None = None
    avisos_anteriores: int = 0


class RemetenteDoTurno(Protocol):
    """O remetente de UM turno, sobre o numero cru (como o `_ScopedWhatsAppSender`). A chave e'
    obrigatoria: quem decide qual e' o `LucasTurno`, nunca o remetente."""

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]: ...


def validar_handoff(handoff: Mapping[str, Any]) -> HandoffValidado:
    """Confere o `HandoffCobranca` chave a chave. Fail-closed: forma errada nao vira turno."""
    if not isinstance(handoff, Mapping):
        raise HandoffInvalidoError("lucas_turno: handoff nao e' um mapeamento")
    chaves = frozenset(handoff)
    if not CHAVES_DO_HANDOFF <= chaves <= CHAVES_DO_HANDOFF | CHAVES_OPCIONAIS_DO_HANDOFF:
        # So' os NOMES das chaves, e so' os que sobram ou faltam: um valor plantado nunca ecoa.
        raise HandoffInvalidoError(
            "lucas_turno: chaves do handoff fora de §2.4 "
            f"(sobram={sorted(chaves - CHAVES_DO_HANDOFF - CHAVES_OPCIONAIS_DO_HANDOFF)}, "
            f"faltam={sorted(CHAVES_DO_HANDOFF - chaves)})"
        )
    if handoff["para"] != AGENT_ID:
        raise HandoffInvalidoError("lucas_turno: handoff nao e' para o lucas")
    subtipo = handoff["cobranca_subtipo"]
    if not isinstance(subtipo, str) or subtipo not in ENTRADA_POR_SUBTIPO:
        raise HandoffInvalidoError("lucas_turno: cobranca_subtipo fora do dominio")
    competencia = handoff["competencia"]
    if competencia is not None and not competencia_valida(competencia):
        raise HandoffInvalidoError("lucas_turno: competencia fora do formato YYYY-MM")
    foco = handoff["cobranca_foco"]
    if not foco_valido(foco):
        raise HandoffInvalidoError("lucas_turno: cobranca_foco fora do dominio")
    message_ref = handoff["message_ref"]
    if not isinstance(message_ref, str) or not message_ref:
        raise HandoffInvalidoError("lucas_turno: message_ref ausente")
    try:
        lista = lista_do_handoff(handoff.get("cobranca_lista"))
    except (TypeError, ValueError):
        raise HandoffInvalidoError("lucas_turno: cobranca_lista fora do dominio") from None
    reclamacao = handoff.get("cobranca_reclamacao")
    if reclamacao is not None and not reclamacao_valida(reclamacao):
        raise HandoffInvalidoError("lucas_turno: cobranca_reclamacao fora do dominio")
    negociacao = handoff.get("cobranca_negociacao")
    if negociacao is not None and negociacao is not True:
        raise HandoffInvalidoError("lucas_turno: cobranca_negociacao fora do dominio")
    confirmacao = handoff.get("cobranca_confirmacao")
    if confirmacao is not None and not confirmacao_valida(confirmacao):
        raise HandoffInvalidoError("lucas_turno: cobranca_confirmacao fora do dominio")
    return HandoffValidado(
        cobranca_subtipo=subtipo,
        competencia=competencia,
        message_ref=message_ref,
        cobranca_foco=foco,
        cobranca_lista=lista,
        cobranca_reclamacao=reclamacao,
        cobranca_negociacao=negociacao is True,
        cobranca_confirmacao=confirmacao,
    )


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
    handoff: HandoffValidado,
    conversa: ConversaDoTurno,
    *,
    tenant_id: str,
    fatos: FatosCobranca | None,
    fatos_cancelamento: FatosCancelamento | None = None,
) -> LucasState:
    """A entrada ESTRITA do turno (`new_lucas_state`): identificadores, a linha de §2.5 e os fatos
    da fonte. Nenhuma chave de texto existe para ser preenchida. `fatos_cancelamento` (DL-0111) so' chega
    com a chave `MAEZO_CANCEL_PF_V1` ligada e num pedido de cancelamento; `None` = nenhuma chave nova."""
    entrada: dict[str, Any] = {
        "tenant_id": tenant_id,
        "conversation_id": conversa.conversation_id,
        "canal": "whatsapp",
        "beneficiario_pseudo_id": conversa.beneficiario_pseudo_id,
        "to_hash": conversa.to_hash,
        **ENTRADA_POR_SUBTIPO[handoff.cobranca_subtipo],
        "cobranca_foco": handoff.cobranca_foco,
    }
    if handoff.competencia is not None:
        entrada["competencia"] = handoff.competencia
    if handoff.cobranca_lista is not None:
        entrada["cobranca_lista"] = handoff.cobranca_lista.como_handoff()
    if handoff.cobranca_reclamacao is not None:
        entrada["cobranca_reclamacao"] = handoff.cobranca_reclamacao
    if handoff.cobranca_negociacao:
        entrada["pedido_negociacao"] = True
    if handoff.cobranca_confirmacao is not None:
        entrada["confirmacao_cancelamento"] = handoff.cobranca_confirmacao
    variante = conversa.variante_de_redacao
    entrada["variante_de_redacao"] = variante % 2 if variante >= 0 else 0
    if conversa.assunto_anterior in ENTRADA_POR_SUBTIPO:
        entrada["assunto_anterior"] = conversa.assunto_anterior
    try:
        memoria = MemoriaDoLucas(conversa.resposta_anterior_ref, conversa.avisos_anteriores)
    except ValueError:
        memoria = MemoriaDoLucas()
    if memoria.resposta_ref is not None:
        entrada["resposta_anterior_ref"] = memoria.resposta_ref
    entrada["avisos_anteriores"] = memoria.avisos_caso_aberto
    if fatos is not None:
        entrada.update(fatos.como_entrada_lucas())
    if fatos_cancelamento is not None:
        entrada.update(fatos_cancelamento.como_entrada_lucas())
    return new_lucas_state(entrada)


def competencia_a_lembrar(final: Mapping[str, Any], pedida: object) -> str | None:
    """O mes que o roteador grava em `lucas_competencia` depois do turno: o mes RESPONDIDO, quando a
    resposta informativa citou um so' (teste real do dono, 12/10/2026: "de qual ano mesmo??" depois de
    uma resposta SEM mes pedido reaproveitava `None` e o Lucas respondia outro mes); nenhum, quando ela
    listou varios (cada linha ja' traz o ano); senao o mes PEDIDO, como antes (escalacao, texto recusado).
    """
    respondidas = competencias_da_resposta(final)
    if len(respondidas) == 1:
        return respondidas[0]
    if respondidas:
        return None
    return pedida if isinstance(pedida, str) and competencia_valida(pedida) else None


def memoria_do_turno(final: Mapping[str, Any]) -> MemoriaDoLucas | None:
    """A memoria do Lucas DEPOIS do turno (rodada 3): a referencia do texto que ele enviou e os avisos de
    caso aberto. `None` quando ele nao enviou nada (a memoria anterior segue no roteador)."""
    memoria = final.get("resposta_memoria_ref")
    ref = memoria if isinstance(memoria, str) and memoria else final.get("resposta_enviada_ref")
    if not isinstance(ref, str):
        return None
    avisos = final.get("avisos_de_caso_aberto")
    contagem = avisos if isinstance(avisos, int) and not isinstance(avisos, bool) and avisos >= 0 else 0
    try:
        return MemoriaDoLucas(ref, min(contagem, 100))
    except ValueError:
        return None


class _RemetenteDoLucas:
    """O `WhatsAppSender` que o grafo do Lucas recebe: troca a chave do grafo pela chave por
    entrega (`lucas:{message_id}`, ordinal n) e entrega ao remetente do turno."""

    def __init__(self, *, inner: RemetenteDoTurno, dedup: WhatsAppDedupGuard, message_id: str) -> None:
        self._inner = inner
        self._dedup = dedup
        self._message_id = message_id
        self._envios = 0
        #: O ultimo texto ENTREGUE ao remetente neste turno (so' para a referencia da memoria; nunca log).
        self.ultimo_texto: str | None = None

    def chave(self, ordinal: int) -> str:
        return self._dedup.outbound_key(f"{PREFIXO_CHAVE_SAIDA}{self._message_id}", occurrence=ordinal)

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del idempotency_key  # a do grafo (impressao digital); ver docstring do modulo
        self._envios += 1
        resultado = await self._inner.send(to_hash, text, idempotency_key=self.chave(self._envios))
        self.ultimo_texto = text
        return resultado


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
    #: So' com a fonte AMH (`MAEZO_LUCAS_FONTE_COBRANCA=amh`): numero cru -> hash
    #: `amh-phone-lookup-v1` (`gateway.amh_interop.AmhPhoneLookupHasher`). `None` = nenhum hash e'
    #: calculado (a simulada nao precisa dele). Ver `hash_telefone_para_amh`.
    hash_telefone_amh: Callable[[str], str | None] | None = field(default=None, repr=False)
    #: Drenagem dos recursos da fonte (os executores do gateway da fonte AMH). `None` na simulada.
    fonte_aclose: Callable[[], Awaitable[None]] | None = field(default=None, repr=False)
    #: Rodada 12: quando o atraso abre caso humano (`MAEZO_LUCAS_ATRASO_MESES_MIN`/`_DIAS_MIN`).
    limiares_atraso: LimiaresDeAtraso = field(default_factory=LimiaresDeAtraso)
    #: DL-0111: `MAEZO_CANCEL_PF_V1`. Desligada (o padrao), o turno e' o de antes byte a byte.
    cancelamento_pf_v1: bool = False
    #: DL-0111: a fonte dos fatos do cancelamento (TINA elegibilidade). So' com a chave e a fonte AMH;
    #: `None` = so' o tipo de contratacao do billing-status (sem titular: o pedido vai a analise humana).
    fonte_cancelamento: FonteCancelamento | None = field(default=None, repr=False)

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
                "limiares_atraso": self.limiares_atraso,
                "cancelamento_pf_v1": self.cancelamento_pf_v1 is True,
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

    def _conferir_mensagem(self, handoff: HandoffValidado, conversa: ConversaDoTurno) -> None:
        """O handoff e' DESTA mensagem (onda f)? O esperado e' recalculado aqui, do id de entrega
        cru, pelo MESMO pseudonimizador keyed com que o despachante escreveu o `message_ref` da
        Helena (`log_safe_message_id`). Nao se confia num valor que o chamador traga pronto."""
        esperado = self.dedup.pseudonym(conversa.message_id)
        if not hmac.compare_digest(handoff.message_ref.encode(), esperado.encode()):
            raise HandoffDeOutraMensagemError(
                "lucas_turno: handoff de outra mensagem — o message_ref nao e' o da entrada deste turno"
            )

    def hash_telefone_para_amh(self, numero_cru: str) -> str | None:
        """O hash `amh-phone-lookup-v1` do numero de ENTRADA, ou `None` sem a fonte AMH.

        Chamado SO' pelo despachante, em `_turno_do_lucas`, onde o numero cru ainda existe (ele
        nao entra no `LucasTurno`). Nunca levanta: um numero fora do padrao brasileiro, ou uma
        falha do hasher, vira `None` e o Lucas segue sem identidade (a fonte devolve
        `Indisponivel("sujeito_nao_resolvido")` e a DMN escala).
        """
        if self.hash_telefone_amh is None:
            return None
        try:
            return self.hash_telefone_amh(numero_cru)
        except Exception:
            return None

    async def _fatos(self, conversa: ConversaDoTurno, handoff: HandoffValidado) -> FatosCobranca | None:
        # O `phone_hash` que a fonte recebe e' o da AMH (`amh-phone-lookup-v1`), nunca o `to_hash`:
        # o pseudonimo `hk1_` e' chaveado pelo Maezo e nao resolve ninguem do lado da AMH.
        # Pedido de LISTA (12/10/2026): a janela inteira. Com `competencia` o contrato `billing-status`
        # devolve SO' aquele mes, e a lista sairia com um mes so'.
        competencia = None if handoff.cobranca_lista is not None else handoff.competencia
        resultado = await self.fonte.fatos(
            conversa.beneficiario_pseudo_id, competencia, phone_hash=conversa.phone_hash_amh
        )
        if isinstance(resultado, FatosCobranca):
            return resultado
        logger.warning(
            "lucas_turno_fonte_indisponivel",
            tenant_id=self.tenant_id,
            conversation_id=conversa.conversation_id,
            motivo=resultado.motivo,
        )
        return None

    async def _fatos_do_cancelamento(
        self, conversa: ConversaDoTurno, handoff: HandoffValidado, fatos: FatosCobranca | None
    ) -> FatosCancelamento | None:
        """DL-0111: os fatos do cancelamento, SO' com a chave ligada e num pedido de cancelamento. Nunca
        levanta: fonte fora vira os fatos do billing-status (sem titular), e o pedido vai a analise humana."""
        if self.cancelamento_pf_v1 is not True or handoff.cobranca_subtipo != "cancelamento":
            return None
        tipo_billing = fatos.tipo_contratacao if fatos is not None else None
        if self.fonte_cancelamento is None:
            return FatosCancelamento(tipo_plano=tipo_plano_do_billing(tipo_billing))
        resultado = await self.fonte_cancelamento.fatos(
            conversa.beneficiario_pseudo_id,
            phone_hash=conversa.phone_hash_amh,
            tipo_contratacao_billing=tipo_billing,
        )
        if isinstance(resultado, FatosIndisponiveis):
            logger.warning(
                "lucas_turno_fatos_cancelamento_indisponiveis",
                tenant_id=self.tenant_id,
                conversation_id=conversa.conversation_id,
                motivo=resultado.motivo,
            )
            return FatosCancelamento(tipo_plano=tipo_plano_do_billing(tipo_billing))
        return resultado

    async def executar(
        self, handoff: Mapping[str, Any], conversa: ConversaDoTurno, sender: RemetenteDoTurno
    ) -> dict[str, Any]:
        """UM turno do Lucas. Devolve o estado final do grafo.

        Levanta `HandoffInvalidoError`/`ValueError` ANTES de qualquer efeito quando a entrada nao
        tem a forma do contrato: um handoff malformado nunca vira turno do Lucas. Um handoff de
        OUTRA mensagem (`message_ref` diferente do da entrada deste turno) levanta
        `HandoffDeOutraMensagemError`, tambem antes de qualquer efeito (nem a fonte e' consultada).
        """
        inicio = time.monotonic()
        try:
            validado = validar_handoff(handoff)
            self._conferir_conversa(conversa)
            self._conferir_mensagem(validado, conversa)
            fatos = await self._fatos(conversa, validado)
            estado = entrada_do_lucas(
                validado,
                conversa,
                tenant_id=self.tenant_id,
                fatos=fatos,
                fatos_cancelamento=await self._fatos_do_cancelamento(conversa, validado, fatos),
            )
        except Exception as exc:
            # A falha ANTES do grafo (forma do handoff/conversa, fonte) tambem e' erro do agente
            # `lucas` (review do #589); a do grafo e' contada no `ainvoke` abaixo. Um ponto por
            # falha, e o despachante, que trata a falha, nao conta de novo.
            from maezo.platform.observability import record_agent_error

            record_agent_error(agent=AGENT_ID, error_type=classify_agent_error_type(exc))
            raise
        remetente_do_lucas = _RemetenteDoLucas(inner=sender, dedup=self.dedup, message_id=conversa.message_id)
        remetente = gate_whatsapp(remetente_do_lucas, self.seam_context)
        compilado = self._compilar(remetente)
        logger.info(
            "lucas_turno_iniciado",
            tenant_id=self.tenant_id,
            conversation_id=conversa.conversation_id,
            cobranca_subtipo=validado.cobranca_subtipo,
            intencao=estado.get("intencao"),
            competencia_presente=validado.competencia is not None,
            lista_pedida=validado.cobranca_lista is not None,
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
        saida = dict(final)
        if remetente_do_lucas.ultimo_texto:
            saida["resposta_enviada_ref"] = ref_de_resposta(remetente_do_lucas.ultimo_texto)
        return saida

    async def aclose(self) -> None:
        """Drenagem: fecha o transporte do motor do Lucas (o da Helena e' fechado a parte) e, com a
        fonte AMH, os executores dela — mesmo que o primeiro fechamento falhe."""
        try:
            close = getattr(self.cibseven, "close", None)
            if close is not None:
                await close()
        finally:
            if self.fonte_aclose is not None:
                await self.fonte_aclose()


__all__ = [
    "AGENT_ID",
    "CHAVES_DO_HANDOFF",
    "CHAVES_OPCIONAIS_DO_HANDOFF",
    "ENTRADA_POR_SUBTIPO",
    "PREFIXO_CHAVE_SAIDA",
    "ConversaDoTurno",
    "HandoffDeOutraMensagemError",
    "HandoffInvalidoError",
    "HandoffValidado",
    "LucasTurno",
    "RemetenteDoTurno",
    "ZonaDeSegurancaError",
    "competencia_a_lembrar",
    "entrada_do_lucas",
    "memoria_do_turno",
    "exigir_zona_geral",
    "validar_handoff",
]
