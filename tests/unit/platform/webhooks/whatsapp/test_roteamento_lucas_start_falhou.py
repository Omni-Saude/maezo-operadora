"""DL-0130 (frente C do SP-OP-ESCALATION-001): start falho do Lucas nao e' "o Lucas terminou escalando".

Antes, `HelenaDispatcher._turno_do_lucas` devolvia `route == "escalate_human"` como `lucas_escalou`, e um
start que FALHOU (motor fora, inclusive o do SP-OP-CANCEL-001) gravava `helena`/`lucas_encerrou` — a
tabela do roteador dizia que um humano foi acionado quando nenhuma instancia nasceu. Agora:

  - `lucas_escalou` exige `process_started is True`;
  - `start_failed is True` vira o sinal `lucas_start_falhou`, que o roteador soma a `falha_tecnica`
    (como `lucas_recusou`) e grava `helena`/`retorno_falha`;
  - o despachante nao manda texto a mais: o UNICO aviso da falha e' o do proprio Lucas
    (`TEXTO_CANCELAMENTO_FALHA_AO_REGISTRAR`, aprovado pelo dono em 19/10/2026), pelo remetente do turno.
    Nenhum caso novo, nenhuma duplicata, nenhuma retentativa automatica.

Contra o despachante e o roteador REAIS (store em memoria). Nos testes do despachante o Lucas e' um duble
que devolve o estado final do turno e, como o grafo real, envia o aviso pelo remetente que recebeu: o que se
prova ali e' a leitura desse estado e que o despachante nao acrescenta nada. O ultimo bloco roda o
`LucasTurno` REAL (grafo + gate do principal `lucas`) com o motor fora: o aviso sai uma vez, pela chave
`lucas:{message_id}`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from maezo.agents.helena.graph import FRASE_PASSAGEM_CANCELAMENTO, FRASE_PASSAGEM_COBRANCA
from maezo.agents.lucas.fonte_cobranca import FatosCobranca
from maezo.agents.lucas.graph import (
    TEXTO_CANCELAMENTO_FALHA_AO_REGISTRAR,
    TEXTO_CONFIRMA_CANCELAMENTO,
    ref_de_resposta,
)
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.tool_registry import build_agent_seam_context
from maezo.platform.webhooks.whatsapp import lucas_turno as lt
from maezo.platform.webhooks.whatsapp import pre_roteamento
from maezo.platform.webhooks.whatsapp.dedup import WhatsAppDedupGuard
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage
from maezo.platform.webhooks.whatsapp.roteamento import (
    ConversaRouter,
    PedidoDeHandoff,
    decidir_transicao,
    evento_do_resultado_da_helena,
)
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.evals.lucas.programa import DmnDraftLocal, InferenciaRoteirizada
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dedup_fakes import FakeDedupRegistry
from tests.support.roteamento_fakes import FakeAgenteAtivoStore

TENANT = "amh"
_TELEFONE = "5511900000178"  # faixa sintetica de teste

_START_FALHOU: dict[str, Any] = {
    "route": "escalate_human",
    "process_started": False,
    "start_failed": True,
    "iniciar_cancelamento": True,
    "desfecho": "erro_inicio_processo",
}
_ESCALOU: dict[str, Any] = {
    "route": "escalate_human",
    "process_started": True,
    "start_failed": False,
    "desfecho": "escalado_humano",
}


def _classify() -> str:
    return json.dumps(
        {
            "intent": "cobranca",
            "population": "none",
            "psychosocial_risk": False,
            "sintoma_codigo": None,
            "intensidade": "desconhecida",
            "cobranca_subtipo": "cancelamento",
            "competencia": "2026-09",
        }
    )


class _Inferencia:
    def __init__(self) -> None:
        self._classificacoes = [_classify()]

    async def generate(self, prompt: str, **_: Any) -> str:
        if "Tarefa: leia a mensagem do beneficiario" in prompt and self._classificacoes:
            return self._classificacoes.pop(0)
        return "Recebemos sua mensagem."


class _Cliente:
    def __init__(self, registry: FakeDedupRegistry) -> None:
        self.registry = registry
        self.textos: list[str] = []

    async def send_message(self, to: str, text: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        del to
        if idempotency_key is not None and not await self.registry.claim(idempotency_key):
            return {"suppressed_duplicate": True}
        self.textos.append(text)
        if idempotency_key is not None:
            await self.registry.mark_processed(idempotency_key)
        return {"messages": [{"id": f"out-{len(self.textos)}"}]}


class _LucasComFinal:
    """Duble do `LucasTurno`: devolve o estado final dado. Como o grafo real (DL-0130), um start de
    cancelamento que falhou envia o aviso fixo UMA vez pelo remetente recebido; nada mais."""

    def __init__(self, final: dict[str, Any]) -> None:
        self._final = final
        self.chamadas = 0

    def hash_telefone_para_amh(self, numero_cru: str) -> str | None:
        del numero_cru
        return None

    async def executar(self, handoff: Any, conversa: Any, sender: Any) -> dict[str, Any]:
        del handoff
        self.chamadas += 1
        if self._final.get("start_failed") is True and self._final.get("iniciar_cancelamento") is True:
            await sender.send(
                conversa.to_hash, TEXTO_CANCELAMENTO_FALHA_AO_REGISTRAR, idempotency_key="lucas:duble"
            )
        return dict(self._final)


def _montar(
    final: dict[str, Any],
) -> tuple[HelenaDispatcher, _Cliente, FakeAgenteAtivoStore, FakeCibSevenTransport]:
    registry = FakeDedupRegistry()
    pseudo = Pseudonymizer()
    store = FakeAgenteAtivoStore()
    motor = FakeCibSevenTransport()
    cliente = _Cliente(registry)
    dispatcher = HelenaDispatcher(
        tenant_id=TENANT,
        inference=_Inferencia(),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=motor,
        whatsapp_client=cliente,  # type: ignore[arg-type]
        pseudonymizer=pseudo,
        audit_sink=FakeStartAuditSink(),
        dedup=WhatsAppDedupGuard(registry=registry, pseudonymizer=pseudo, tenant=TENANT),
        roteador=ConversaRouter(
            tenant_id=TENANT,
            store=store,
            lexicos=pre_roteamento.carregar(),
            inatividade=timedelta(minutes=60),
            lucas_disponivel=True,
        ),
        lucas_turno=_LucasComFinal(final),  # type: ignore[arg-type]
    )
    return dispatcher, cliente, store, motor


def _linha(store: FakeAgenteAtivoStore) -> Any:
    assert len(store.linhas) == 1
    return next(iter(store.linhas.values()))


def _msg(texto: str) -> InboundMessage:
    return InboundMessage(from_number=_TELEFONE, text=texto, message_id="wamid.SINTETICO-C2")


async def test_start_falhou_do_lucas_volta_a_helena_como_retorno_falha_com_um_aviso_so() -> None:
    dispatcher, cliente, store, motor = _montar(_START_FALHOU)
    await dispatcher.dispatch(_msg("quero cancelar meu plano, pode confirmar o boleto?"))

    assert dispatcher.lucas_turno is not None and dispatcher.lucas_turno.chamadas == 1  # type: ignore[attr-defined]
    linha = _linha(store)
    assert (linha.agente_ativo, linha.transicao_motivo) == ("helena", "retorno_falha")
    assert linha.transicao_motivo != "lucas_encerrou"
    # A frase de passagem (neutra, nao afirma pedido registrado) e o aviso do Lucas, UMA vez cada; o
    # despachante nao acrescenta nada.
    assert cliente.textos in (
        [FRASE_PASSAGEM_COBRANCA, TEXTO_CANCELAMENTO_FALHA_AO_REGISTRAR],
        [FRASE_PASSAGEM_CANCELAMENTO, TEXTO_CANCELAMENTO_FALHA_AO_REGISTRAR],
    )
    assert cliente.textos.count(TEXTO_CANCELAMENTO_FALHA_AO_REGISTRAR) == 1
    assert motor._history == {}  # nenhum escalonamento aberto pela Helena neste turno


async def test_start_falhou_com_pedido_explicito_de_pessoa_nao_abre_outro_caso_nem_envia() -> None:
    """O pedido de pessoa junto (DL-0092) continuava suprimido com o start falho (antes `lucas_escalou`
    era `True`); a frente C nao muda isso — abrir outro caso aqui seria envio novo, decisao do dono. O
    unico texto alem da passagem e' o aviso do Lucas, uma vez."""
    dispatcher, cliente, store, motor = _montar(_START_FALHOU)
    resultado = await dispatcher.dispatch(_msg("quero falar com um atendente sobre o boleto"))

    assert not resultado.get("escalation_started")
    assert cliente.textos in (
        [FRASE_PASSAGEM_COBRANCA, TEXTO_CANCELAMENTO_FALHA_AO_REGISTRAR],
        [FRASE_PASSAGEM_CANCELAMENTO, TEXTO_CANCELAMENTO_FALHA_AO_REGISTRAR],
    )
    assert motor._history == {}
    assert _linha(store).agente_ativo == "helena"


async def test_lucas_com_processo_iniciado_continua_lucas_encerrou() -> None:
    dispatcher, cliente, store, _ = _montar(_ESCALOU)
    await dispatcher.dispatch(_msg("estao me cobrando um valor errado"))
    assert TEXTO_CANCELAMENTO_FALHA_AO_REGISTRAR not in cliente.textos  # sem falha, sem aviso
    assert dispatcher.lucas_turno is not None and dispatcher.lucas_turno.chamadas == 1  # type: ignore[attr-defined]
    linha = _linha(store)
    assert (linha.agente_ativo, linha.transicao_motivo) == ("helena", "lucas_encerrou")


async def test_turno_do_lucas_devolve_escalou_falso_e_start_falhou_verdadeiro() -> None:
    dispatcher, _, _, _ = _montar(_START_FALHOU)
    _, escalou, start_falhou = await dispatcher._turno_do_lucas(
        _msg("quero cancelar"),
        phone_hash="hk1_x",
        conversation_id="wa:amh:c2",
        beneficiario_pseudo_id="pseudo-c2",
        handoff={
            "para": "lucas",
            "cobranca_subtipo": "cancelamento",
            "competencia": None,
            "message_ref": "r",
        },
    )
    assert (escalou, start_falhou) == (False, True)


def test_evento_soma_lucas_start_falhou_a_falha_e_a_maquina_grava_retorno_falha() -> None:
    pedido = PedidoDeHandoff(cobranca_subtipo="cancelamento", competencia=None)
    evento = evento_do_resultado_da_helena(
        {"intent": "cobranca"}, None, handoff=pedido, lucas_escalou=False, lucas_start_falhou=True
    )
    assert evento.falha_tecnica is True
    decisao = decidir_transicao(None, evento, agora=datetime(2026, 10, 16, tzinfo=UTC))
    assert (decisao.agente_ativo, decisao.transicao_motivo) == ("helena", "retorno_falha")
    sem_falha = evento_do_resultado_da_helena({"intent": "cobranca"}, None, handoff=pedido)
    assert sem_falha.falha_tecnica is False


# --- O LucasTurno REAL: o aviso sai pelo gate do principal `lucas`, uma vez -------------------------


class _MotorFora(FakeCibSevenTransport):
    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> Any:
        raise CibSevenError("motor fora (teste)")


class _FontePf:
    async def fatos(self, pseudo_id: str, competencia: str | None, *, phone_hash: str | None = None) -> Any:
        del pseudo_id, competencia, phone_hash
        return FatosCobranca(
            status_conciliado=True,
            ciclos_sem_conciliacao=0,
            numero_boleto="",
            cnab_ref="CNAB-SINT",
            pagador_tipo="pessoa_fisica",
        )


class _RemetenteDoTurno:
    def __init__(self) -> None:
        self.envios: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del to_hash
        self.envios.append((text, idempotency_key))
        return {"messages": [{"id": f"out-{len(self.envios)}"}]}


async def _turno_real_do_cancelamento(
    motor: FakeCibSevenTransport,
) -> tuple[dict[str, Any], _RemetenteDoTurno, WhatsAppDedupGuard]:
    dedup = WhatsAppDedupGuard(registry=FakeDedupRegistry(), pseudonymizer=Pseudonymizer(), tenant=TENANT)
    turno = lt.LucasTurno(
        tenant_id=TENANT,
        inference=InferenciaRoteirizada(None, rota_padrao="escalate_human"),  # type: ignore[arg-type]
        dmn=DmnDraftLocal(),
        cibseven=motor,
        audit_sink=FakeStartAuditSink(),
        seam_context=build_agent_seam_context(tenant=TENANT, agent_id="lucas"),
        dedup=dedup,
        fonte=_FontePf(),
    )
    conversa = lt.ConversaDoTurno(
        conversation_id="wa:amh:hk1_5ea1fa1c0c2",
        beneficiario_pseudo_id="pseudo-startfalhou",
        to_hash="hash-startfalhou",
        message_id="wamid.SINTETICO-C2-REAL",
        assunto_anterior="cancelamento",
        resposta_anterior_ref=ref_de_resposta(TEXTO_CONFIRMA_CANCELAMENTO),
    )
    handoff = {
        "para": "lucas",
        "cobranca_subtipo": "cancelamento",
        "competencia": None,
        "cobranca_foco": "geral",
        "cobranca_confirmacao": "confirma",
        "message_ref": dedup.pseudonym(conversa.message_id),
    }
    remetente = _RemetenteDoTurno()
    final = await turno.executar(handoff, conversa, remetente)
    return final, remetente, dedup


async def test_lucas_real_com_motor_fora_envia_o_aviso_uma_vez_pelo_remetente_gated() -> None:
    final, remetente, dedup = await _turno_real_do_cancelamento(_MotorFora())
    assert final.get("start_failed") is True and final.get("iniciar_cancelamento") is True
    assert final["desfecho"] == "erro_inicio_processo"
    assert remetente.envios == [
        (
            TEXTO_CANCELAMENTO_FALHA_AO_REGISTRAR,
            dedup.outbound_key("lucas:wamid.SINTETICO-C2-REAL", occurrence=1),
        )
    ]


async def test_lucas_real_com_start_ok_nao_envia_o_aviso() -> None:
    final, remetente, _ = await _turno_real_do_cancelamento(FakeCibSevenTransport())
    assert final["process_started"] is True and final.get("start_failed") is not True
    assert len(remetente.envios) == 1
    assert TEXTO_CANCELAMENTO_FALHA_AO_REGISTRAR not in [t for t, _ in remetente.envios]
