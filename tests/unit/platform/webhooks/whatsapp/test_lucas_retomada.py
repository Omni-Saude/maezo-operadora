"""`LucasRetomada` — o caminho de VOLTA ao WhatsApp quando o atendente devolve um caso de cobranca.

O defeito que isto fecha (medido em dev em 02/10/2026): o `agent-resume` so' registrava a Helena, e um caso
aberto pelo Lucas devolvido pelo atendente acabava em `agent_resume.no_resumer_for_agent` — a instrucao
nunca chegava a quem perguntou. Aqui se prova, sem modelo, DMN nem motor:
  - a instrucao chega ao destino certo, UMA vez, com a chave `lucas-resume:{instancia}`;
  - destino que nao bate com o hash keyed e conversa alheia sao recusados ANTES de qualquer envio;
  - nota vazia nao envia nada; nota que as cercas do Lucas barram (desfecho adverso, valor sem fato,
    promessa de humano) vira a constante segura;
  - falha de envio PROPAGA (o consumidor nao confirma o offset) e nao duplica na nova tentativa;
  - o `ResumeHandler` real encaminha um evento `agent_id=lucas` para esta porta;
  - o vocabulario de telemetria do Lucas conhece os desfechos de retomada.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from maezo.agents.lucas.graph import RESPOSTA_INFORMATIVA_RECUSADA
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.tool_registry import build_agent_seam_context
from maezo.platform.integrations import agent_resume as ar
from maezo.platform.webhooks.whatsapp import lucas_retomada as lr
from maezo.platform.webhooks.whatsapp.dedup import WhatsAppDedupGuard
from maezo.platform.webhooks.whatsapp.security import hash_phone
from maezo.runtime import turn_telemetry
from tests.support.dedup_fakes import FakeDedupRegistry

TENANT = "amh"
#: Numero SINTETICO (faixa de teste): o hash keyed dele e' o que a conversa carrega.
NUMERO = "5511900000123"


class _Cliente:
    """O protocolo de `WhatsAppServer.send_message` com dedup de saida."""

    def __init__(self, registry: FakeDedupRegistry, *, falha: bool = False) -> None:
        self.registry = registry
        self.falha = falha
        self.enviados: list[tuple[str, str, str]] = []

    async def send_message(self, to: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        if self.falha:
            raise RuntimeError("meta indisponivel")
        if not await self.registry.claim(idempotency_key):
            return {"suppressed_duplicate": True}
        self.enviados.append((to, text, idempotency_key))
        await self.registry.mark_processed(idempotency_key)
        return {"messages": [{"id": f"out-{len(self.enviados)}"}]}


def _montar(*, falha: bool = False) -> tuple[lr.LucasRetomada, _Cliente, str, WhatsAppDedupGuard]:
    pseudonimizador = Pseudonymizer()
    registry = FakeDedupRegistry()
    dedup = WhatsAppDedupGuard(registry=registry, pseudonymizer=pseudonimizador, tenant=TENANT)
    cliente = _Cliente(registry, falha=falha)
    retomada = lr.LucasRetomada(
        tenant_id=TENANT,
        pseudonymizer=pseudonimizador,
        whatsapp_client=cliente,
        dedup=dedup,
        seam_context=build_agent_seam_context(tenant=TENANT, agent_id="lucas"),
    )
    conversa = f"wa:{TENANT}:{hash_phone(NUMERO, TENANT, pseudonimizador)}"
    return retomada, cliente, conversa, dedup


async def _retomar(r: lr.LucasRetomada, conversa: str, nota: str, ref: str = "inst-1") -> dict[str, Any]:
    return await r.resume(conversation_id=conversa, instrucoes=nota, raw_to=NUMERO, resume_ref=ref)


# --- composicao ---------------------------------------------------------------------------------------


def test_a_mensagem_cita_a_instrucao_entre_aspas_e_atribui_a_equipe() -> None:
    texto = lr.compor_mensagem_de_retomada('Seu boleto de   setembro vence dia 10.\nUse a "2a via" do portal do plano.')
    assert "Um profissional da nossa equipe" in texto
    assert "Seu boleto de setembro vence dia 10. Use a '2a via' do portal do plano." in texto
    assert "\n" not in texto


def test_o_modelo_fixo_passa_nas_cercas_do_lucas_com_uma_nota_ordinaria() -> None:
    from maezo.agents.lucas.prompts import motivo_de_recusa

    texto = lr.compor_mensagem_de_retomada("Seu boleto de setembro vence dia 10; use a 2a via no portal do plano.")
    from maezo.agents.helena.graph import motivo_de_recusa_da_retomada

    assert motivo_de_recusa(texto, lr.RESPONSE_KIND_RETOMADA, {}) is None
    assert motivo_de_recusa_da_retomada(texto) is None


# --- envio ----------------------------------------------------------------------------------------------


async def test_a_instrucao_chega_ao_destino_uma_vez_com_a_chave_da_retomada() -> None:
    retomada, cliente, conversa, dedup = _montar()

    primeiro = await _retomar(retomada, conversa, "Seu boleto vence dia 10; use a 2a via no portal do plano.")
    repetido = await _retomar(retomada, conversa, "Seu boleto vence dia 10; use a 2a via no portal do plano.")

    assert primeiro == {"desfecho": lr.DESFECHO_RETOMADA_ENVIADA}
    assert repetido == {"desfecho": lr.DESFECHO_RETOMADA_ENVIADA}  # o evento pode ser reentregue...
    assert len(cliente.enviados) == 1  # ...e a instrucao sai UMA vez
    destino, texto, chave = cliente.enviados[0]
    assert destino == NUMERO
    assert "vence dia 10" in texto
    assert chave == dedup.outbound_key("lucas-resume:inst-1", occurrence=1)
    assert chave != dedup.outbound_key("resume:inst-1", occurrence=1)  # nao colide com a da Helena


async def test_instancias_diferentes_usam_chaves_diferentes() -> None:
    retomada, cliente, conversa, _ = _montar()
    await _retomar(retomada, conversa, "Primeira orientacao.", ref="inst-A")
    await _retomar(retomada, conversa, "Segunda orientacao.", ref="inst-B")
    assert len(cliente.enviados) == 2


# --- destino --------------------------------------------------------------------------------------------


async def test_destinatario_que_nao_bate_com_o_hash_keyed_e_recusado_antes_do_envio() -> None:
    retomada, cliente, conversa, _ = _montar()
    with pytest.raises(ValueError, match="nao bate"):
        await retomada.resume(
            conversation_id=conversa, instrucoes="Orientacao.", raw_to="5511900000999", resume_ref="i"
        )
    assert cliente.enviados == []


@pytest.mark.parametrize(
    "conversa",
    ["wa:outro-tenant:hk1_abc", f"wa:{TENANT}:", f"wa:{TENANT}:hk1_a:b", "sem-prefixo", ""],
)
async def test_conversa_alheia_ou_malformada_e_recusada(conversa: str) -> None:
    retomada, cliente, _, _ = _montar()
    with pytest.raises(ValueError, match="conversation_id"):
        await retomada.resume(conversation_id=conversa, instrucoes="Orientacao.", raw_to=NUMERO, resume_ref="i")
    assert cliente.enviados == []


# --- cercas ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("nota", ["", "   ", "\n\t"])
async def test_nota_vazia_nao_envia_nada(nota: str) -> None:
    retomada, cliente, conversa, _ = _montar()
    assert await _retomar(retomada, conversa, nota) == {"desfecho": lr.DESFECHO_RETOMADA_SEM_INSTRUCOES}
    assert cliente.enviados == []


async def test_nota_acima_do_teto_nao_envia_nada() -> None:
    retomada, cliente, conversa, _ = _montar()
    resultado = await _retomar(retomada, conversa, "a " * (lr.RETOMADA_MAX_INSTRUCOES + 1))
    assert resultado == {"desfecho": lr.DESFECHO_RETOMADA_SEM_INSTRUCOES}
    assert cliente.enviados == []


@pytest.mark.parametrize(
    "nota",
    [
        "Seu plano sera cancelado amanha por falta de pagamento.",  # desfecho adverso
        "Voce deve R$ 1.500,00 e precisa pagar hoje.",  # valor sem fato
        "Um atendente vai te ligar em instantes.",  # promessa de humano (o caso acabou de ser devolvido)
    ],
)
async def test_nota_que_as_cercas_barram_vira_a_constante_segura(nota: str) -> None:
    retomada, cliente, conversa, _ = _montar()
    resultado = await _retomar(retomada, conversa, nota)
    assert resultado == {"desfecho": lr.DESFECHO_RETOMADA_RECUSADA}
    assert [texto for _, texto, _ in cliente.enviados] == [RESPOSTA_INFORMATIVA_RECUSADA]


# --- falha de envio -------------------------------------------------------------------------------------


async def test_falha_de_envio_propaga_para_o_consumidor_nao_confirmar() -> None:
    retomada, cliente, conversa, _ = _montar(falha=True)
    with pytest.raises(lr.RetomadaDoLucasEnvioFalhouError):
        await _retomar(retomada, conversa, "Orientacao ordinaria.")
    assert cliente.enviados == []


# --- construcao -----------------------------------------------------------------------------------------


def test_sem_guard_de_dedup_nao_constroi() -> None:
    with pytest.raises(ValueError, match="dedup"):
        lr.LucasRetomada(
            tenant_id=TENANT,
            pseudonymizer=Pseudonymizer(),
            whatsapp_client=object(),
            dedup=None,
            seam_context=build_agent_seam_context(tenant=TENANT, agent_id="lucas"),
        )


def test_seam_de_outro_principal_nao_constroi() -> None:
    with pytest.raises(ValueError, match="principal lucas"):
        lr.LucasRetomada(
            tenant_id=TENANT,
            pseudonymizer=Pseudonymizer(),
            whatsapp_client=object(),
            dedup=WhatsAppDedupGuard(registry=FakeDedupRegistry(), pseudonymizer=Pseudonymizer(), tenant=TENANT),
            seam_context=build_agent_seam_context(tenant=TENANT, agent_id="helena"),
        )


# --- integracao com o ResumeHandler ---------------------------------------------------------------------


class _Instrucoes:
    async def fetch(self, event: Any) -> str:
        return "Seu boleto vence dia 10; use a 2a via no portal do plano."


class _Contato:
    def __init__(self, phone: str) -> None:
        self.phone = phone
        from datetime import UTC, datetime

        self.last_inbound_at = datetime.now(UTC)


class _Destinatarios:
    async def lookup(self, *, tenant_id: str, conversation_id: str) -> _Contato:
        return _Contato(NUMERO)


class _Alerta:
    async def alert_outside_window(self, event: Any) -> None:  # pragma: no cover - nao deve ocorrer
        raise AssertionError("janela aberta: nao deveria alertar")


def _evento(conversa: str, agent_id: str = "lucas") -> Mapping[str, Any]:
    return {
        "tenant_id": TENANT,
        "agent_id": agent_id,
        "conversation_id": conversa,
        "resultado": "devolvido_agente",
        "_business_key": f"ESC-{TENANT}-{conversa}",
        "_process_instance_id": "inst-integracao",
    }


async def test_o_resume_handler_encaminha_o_caso_do_lucas_para_esta_porta() -> None:
    retomada, cliente, conversa, _ = _montar()
    handler = ar.ResumeHandler(
        tenant_id=TENANT,
        instructions=_Instrucoes(),  # type: ignore[arg-type]
        recipients=_Destinatarios(),  # type: ignore[arg-type]
        resumers={"lucas": retomada},
        alerter=_Alerta(),  # type: ignore[arg-type]
    )

    recibo = await handler.handle(_evento(conversa))

    assert recibo.outcome is ar.ResumeOutcome.RESUMED
    assert recibo.agent_id == "lucas"
    assert recibo.desfecho == lr.DESFECHO_RETOMADA_ENVIADA
    assert len(cliente.enviados) == 1


async def test_sem_a_porta_do_lucas_o_caso_continua_sendo_descartado() -> None:
    """A testemunha do defeito original: sem `lucas` no registro, ninguem retoma a conversa."""
    _, cliente, conversa, _ = _montar()
    handler = ar.ResumeHandler(
        tenant_id=TENANT,
        instructions=_Instrucoes(),  # type: ignore[arg-type]
        recipients=_Destinatarios(),  # type: ignore[arg-type]
        resumers={},
        alerter=_Alerta(),  # type: ignore[arg-type]
    )
    recibo = await handler.handle(_evento(conversa))
    assert recibo.outcome is ar.ResumeOutcome.SKIPPED_NO_RESUMER
    assert cliente.enviados == []


# --- telemetria -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "desfecho",
    [
        lr.DESFECHO_RETOMADA_ENVIADA,
        lr.DESFECHO_RETOMADA_RECUSADA,
        lr.DESFECHO_RETOMADA_SEM_INSTRUCOES,
        lr.DESFECHO_RETOMADA_FALHA_ENVIO,
        "retomada_fora_da_janela",
    ],
)
def test_o_vocabulario_de_telemetria_do_lucas_conhece_a_retomada(desfecho: str) -> None:
    assert desfecho in turn_telemetry._DESFECHO_VOCAB["lucas"]
    assert "retomada" in turn_telemetry._ROUTE_VOCAB["lucas"]
