"""Retorno do pedido de cancelamento ao beneficiario (DL-0111, `MAEZO_CANCEL_PF_V1`, SP-OP-CANCEL-001 v0.2.0).

O BPMN publica `agents.events.process_completed` com `resultado` de cancelamento SO' nas instancias do
Lucas com a chave ligada; o `agent_resume` confere o HISTORICO do motor e o Lucas envia um TEXTO FIXO
(aprovado pelo dono para dev em 15/10/2026; revisao juridica antes de producao). Prova, sem motor real:
  * EXECUTADO -> "Seu plano foi cancelado a partir de DD/MM/AAAA" com o protocolo, UMA vez, chave propria;
  * SOLICITAR_INFO / MANTER -> o texto fixo com a oferta de pessoa;
  * evento sem respaldo no historico (aviso desligado, outra conversa, fato que nao bate) -> fila morta,
    nada enviado; EXECUTADO sem data -> a equipe e' avisada, nada enviado;
  * ancora errada (chave ESC, outro agente) -> fila morta; sem a fonte de historico -> nada enviado;
  * fora da janela de 24h -> nada sai e a equipe e' avisada.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from maezo.agents.lucas import cancelamento_pf as cpf
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.tool_registry import build_agent_seam_context
from maezo.platform.integrations import agent_resume as ar
from maezo.platform.webhooks.whatsapp import lucas_retomada as lr
from maezo.platform.webhooks.whatsapp.dedup import WhatsAppDedupGuard
from maezo.platform.webhooks.whatsapp.security import hash_phone
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, HistoricProcessVariables
from tests.support.dedup_fakes import FakeDedupRegistry

TENANT = "amh"
#: Numero SINTETICO (faixa de teste): o hash keyed dele e' o que a conversa carrega.
NUMERO = "5511900000124"
INSTANCIA = "inst-cancel-1"
CHAVE = f"CANCEL-{TENANT}-pseudo-sintetico"


class _Cliente:
    def __init__(self, registry: FakeDedupRegistry) -> None:
        self.registry = registry
        self.enviados: list[tuple[str, str, str]] = []

    async def send_message(self, to: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        if not await self.registry.claim(idempotency_key):
            return {"suppressed_duplicate": True}
        self.enviados.append((to, text, idempotency_key))
        await self.registry.mark_processed(idempotency_key)
        return {"messages": [{"id": f"out-{len(self.enviados)}"}]}


def _montar() -> tuple[lr.LucasRetomada, _Cliente, str]:
    pseudonimizador = Pseudonymizer()
    registry = FakeDedupRegistry()
    dedup = WhatsAppDedupGuard(registry=registry, pseudonymizer=pseudonimizador, tenant=TENANT)
    cliente = _Cliente(registry)
    retomada = lr.LucasRetomada(
        tenant_id=TENANT,
        pseudonymizer=pseudonimizador,
        whatsapp_client=cliente,
        dedup=dedup,
        seam_context=build_agent_seam_context(tenant=TENANT, agent_id="lucas"),
    )
    return retomada, cliente, f"wa:{TENANT}:{hash_phone(NUMERO, TENANT, pseudonimizador)}"


# --- a porta do Lucas ---------------------------------------------------------------------------------


async def test_executado_envia_o_texto_fixo_com_data_e_protocolo_uma_vez() -> None:
    retomada, cliente, conversa = _montar()
    protocolo = cpf.protocolo_do_cancelamento(INSTANCIA)
    for _ in range(2):  # reentrega do evento: a mesma chave, um envio so'
        r = await retomada.retomar_cancelamento(
            conversation_id=conversa,
            resultado=cpf.RESULTADO_EXECUTADO,
            protocolo=protocolo,
            data_efeito_iso="2026-10-15",
            raw_to=NUMERO,
            resume_ref=f"{INSTANCIA}:{cpf.RESULTADO_EXECUTADO}",
        )
        assert r["desfecho"] == lr.DESFECHO_RETOMADA_ENVIADA
    assert len(cliente.enviados) == 1
    destino, texto, _chave = cliente.enviados[0]
    assert destino == NUMERO
    assert texto == cpf.TEXTO_CANCELADO.format(data="15/10/2026", protocolo=protocolo)


@pytest.mark.parametrize(
    ("resultado", "modelo"),
    [(cpf.RESULTADO_INFO_SOLICITADA, cpf.TEXTO_INFO_SOLICITADA), (cpf.RESULTADO_MANTIDO, cpf.TEXTO_MANTIDO)],
)
async def test_info_e_mantido_enviam_o_texto_fixo_com_oferta_de_pessoa(resultado: str, modelo: str) -> None:
    retomada, cliente, conversa = _montar()
    protocolo = cpf.protocolo_do_cancelamento(INSTANCIA)
    await retomada.retomar_cancelamento(
        conversation_id=conversa,
        resultado=resultado,
        protocolo=protocolo,
        data_efeito_iso=None,
        raw_to=NUMERO,
        resume_ref=f"{INSTANCIA}:{resultado}",
    )
    assert [t for _, t, _ in cliente.enviados] == [modelo.format(protocolo=protocolo)]
    assert "cancelado" not in cliente.enviados[0][1].lower()


async def test_executado_sem_data_ou_destino_alheio_nao_envia_nada() -> None:
    retomada, cliente, conversa = _montar()
    protocolo = cpf.protocolo_do_cancelamento(INSTANCIA)
    with pytest.raises(ValueError):
        await retomada.retomar_cancelamento(
            conversation_id=conversa,
            resultado=cpf.RESULTADO_EXECUTADO,
            protocolo=protocolo,
            data_efeito_iso=None,
            raw_to=NUMERO,
            resume_ref="x",
        )
    with pytest.raises(ValueError):
        await retomada.retomar_cancelamento(
            conversation_id=conversa,
            resultado=cpf.RESULTADO_MANTIDO,
            protocolo=protocolo,
            data_efeito_iso=None,
            raw_to="5511900000999",
            resume_ref="y",
        )
    assert cliente.enviados == []


# --- o ResumeHandler ----------------------------------------------------------------------------------


class _Contato:
    def __init__(self, phone: str, *, horas: float = 0.0) -> None:
        self.phone = phone
        self.last_inbound_at = datetime.now(UTC) - timedelta(hours=horas)


class _Destinatarios:
    def __init__(self, *, horas: float = 0.0) -> None:
        self.horas = horas

    async def lookup(self, *, tenant_id: str, conversation_id: str) -> _Contato:
        return _Contato(NUMERO, horas=self.horas)


class _Alerta:
    def __init__(self) -> None:
        self.fora = 0
        self.sem_entrega = 0

    async def alert_outside_window(self, event: Any) -> None:
        self.fora += 1

    async def alert_undeliverable(self, event: Any) -> None:
        self.sem_entrega += 1


class _SemInstrucoes:
    async def fetch(self, event: Any) -> str:  # pragma: no cover - o retorno do cancelamento nao le notas
        raise AssertionError("o retorno do cancelamento nunca le notas de atendente")


def _motor(conversa: str, **variaveis: Any) -> FakeCibSevenTransport:
    motor = FakeCibSevenTransport()
    base: dict[str, Any] = {
        "aviso_beneficiario_whatsapp": True,
        "conversation_id": conversa,
        "execucao_cadastro": "EXECUTADO",
        "data_efeito_iso": "2026-10-15",
    }
    base.update(variaveis)
    motor.seed_historic_variables(
        HistoricProcessVariables(
            instance_id=INSTANCIA,
            business_key=CHAVE,
            process_key="SP-OP-CANCEL-001",
            state="COMPLETED",
            variables=base,
        )
    )
    return motor


def _evento(conversa: str, resultado: str = cpf.RESULTADO_EXECUTADO, **extra: Any) -> Mapping[str, Any]:
    return {
        "tenant_id": TENANT,
        "agent_id": "lucas",
        "conversation_id": conversa,
        "resultado": resultado,
        "_business_key": CHAVE,
        "_process_instance_id": INSTANCIA,
        **extra,
    }


def _handler(
    retomada: lr.LucasRetomada, motor: FakeCibSevenTransport | None, *, horas: float = 0.0
) -> tuple[ar.ResumeHandler, _Alerta]:
    alerta = _Alerta()
    handler = ar.ResumeHandler(
        tenant_id=TENANT,
        instructions=_SemInstrucoes(),  # type: ignore[arg-type]
        recipients=_Destinatarios(horas=horas),  # type: ignore[arg-type]
        resumers={"lucas": retomada},
        alerter=alerta,  # type: ignore[arg-type]
        cancelamentos=ar.EngineHistoryCancelamentoSource(motor) if motor is not None else None,
    )
    return handler, alerta


async def test_o_handler_confere_o_historico_e_entrega_o_retorno_executado() -> None:
    retomada, cliente, conversa = _montar()
    handler, alerta = _handler(retomada, _motor(conversa))
    recibo = await handler.handle(_evento(conversa))
    assert recibo.outcome is ar.ResumeOutcome.RESUMED and recibo.resultado == cpf.RESULTADO_EXECUTADO
    ((_, texto, _),) = cliente.enviados
    assert "15/10/2026" in texto and cpf.protocolo_do_cancelamento(INSTANCIA) in texto
    assert (alerta.fora, alerta.sem_entrega) == (0, 0)


@pytest.mark.parametrize(
    ("resultado", "decisao"),
    [(cpf.RESULTADO_INFO_SOLICITADA, "SOLICITAR_INFO"), (cpf.RESULTADO_MANTIDO, "MANTER")],
)
async def test_o_handler_entrega_info_e_mantido_com_a_decisao_no_historico(
    resultado: str, decisao: str
) -> None:
    retomada, cliente, conversa = _montar()
    handler, _ = _handler(retomada, _motor(conversa, decisao_cancelamento=decisao, execucao_cadastro=None))
    recibo = await handler.handle(_evento(conversa, resultado))
    assert recibo.outcome is ar.ResumeOutcome.RESUMED
    assert len(cliente.enviados) == 1


@pytest.mark.parametrize(
    "variaveis",
    [
        {"aviso_beneficiario_whatsapp": False},  # instancia sem a chave: nunca avisa
        {"conversation_id": "wa:amh:hk1_outra"},  # outra conversa
        {"execucao_cadastro": "IMPEDIDO"},  # o fato nao aconteceu
    ],
)
async def test_sem_respaldo_no_historico_vai_para_a_fila_morta_sem_enviar(variaveis: dict[str, Any]) -> None:
    retomada, cliente, conversa = _montar()
    handler, _ = _handler(retomada, _motor(conversa, **variaveis))
    with pytest.raises(ar.MalformedResumeEventError):
        await handler.handle(_evento(conversa))
    assert cliente.enviados == []


async def test_mantido_sem_a_decisao_manter_no_historico_nao_envia() -> None:
    retomada, cliente, conversa = _montar()
    handler, _ = _handler(retomada, _motor(conversa, decisao_cancelamento="SOLICITAR_INFO"))
    with pytest.raises(ar.MalformedResumeEventError):
        await handler.handle(_evento(conversa, cpf.RESULTADO_MANTIDO))
    assert cliente.enviados == []


async def test_executado_sem_data_de_efeito_avisa_a_equipe_e_nao_envia() -> None:
    retomada, cliente, conversa = _montar()
    handler, alerta = _handler(retomada, _motor(conversa, data_efeito_iso=None))
    with pytest.raises(ar.ResumeUndeliverableError):
        await handler.handle(_evento(conversa))
    assert cliente.enviados == [] and alerta.sem_entrega == 1


async def test_outra_instancia_de_processo_nao_serve() -> None:
    retomada, cliente, conversa = _montar()
    motor = FakeCibSevenTransport()
    motor.seed_historic_variables(
        HistoricProcessVariables(
            instance_id=INSTANCIA,
            business_key=CHAVE,
            process_key="SP-OP-ESCALATION-001",
            state="COMPLETED",
            variables={
                "aviso_beneficiario_whatsapp": True,
                "conversation_id": conversa,
                "execucao_cadastro": "EXECUTADO",
            },
        )
    )
    handler, _ = _handler(retomada, motor)
    with pytest.raises(ar.MalformedResumeEventError):
        await handler.handle(_evento(conversa))
    assert cliente.enviados == []


@pytest.mark.parametrize(
    "extra",
    [
        {"_business_key": "ESC-amh-wa:amh:hk1_x"},  # chave do escalonamento num resultado de cancelamento
        {"agent_id": "helena"},  # o retorno do cancelamento e' do Lucas
        {"_business_key": "CANCEL-outro-tenant-x"},
    ],
)
async def test_ancora_errada_vai_para_a_fila_morta(extra: dict[str, Any]) -> None:
    retomada, cliente, conversa = _montar()
    handler, _ = _handler(retomada, _motor(conversa))
    with pytest.raises(ar.MalformedResumeEventError):
        await handler.handle(_evento(conversa, **extra))
    assert cliente.enviados == []


async def test_evento_de_cancelamento_com_phi_e_recusado_antes_de_tudo() -> None:
    retomada, cliente, conversa = _montar()
    handler, _ = _handler(retomada, _motor(conversa))
    with pytest.raises(ar.MalformedResumeEventError):
        await handler.handle(_evento(conversa, matricula_beneficiario="x"))
    assert cliente.enviados == []


async def test_sem_a_fonte_de_historico_nada_e_enviado() -> None:
    retomada, cliente, conversa = _montar()
    handler, _ = _handler(retomada, None)
    recibo = await handler.handle(_evento(conversa))
    assert recibo.outcome is ar.ResumeOutcome.SKIPPED_NO_RESUMER and cliente.enviados == []


async def test_fora_da_janela_da_meta_nada_sai_e_a_equipe_e_avisada() -> None:
    retomada, cliente, conversa = _montar()
    handler, alerta = _handler(retomada, _motor(conversa), horas=30)
    recibo = await handler.handle(_evento(conversa))
    assert recibo.outcome is ar.ResumeOutcome.OUTSIDE_WINDOW
    assert cliente.enviados == [] and alerta.fora == 1


async def test_o_devolvido_agente_do_escalonamento_continua_igual() -> None:
    """O caminho de sempre (ESC + devolvido_agente) nao passa pelo retorno do cancelamento."""
    evento = {
        "tenant_id": TENANT,
        "agent_id": "lucas",
        "conversation_id": "wa:amh:hk1_abc",
        "resultado": "devolvido_agente",
        "_business_key": "ESC-amh-wa:amh:hk1_abc",
        "_process_instance_id": "inst-esc",
    }
    parsed = ar.parse_process_completed(evento, tenant_id=TENANT)
    assert parsed.resultado == "devolvido_agente" and parsed.business_key.startswith("ESC-")
    with pytest.raises(ar.MalformedResumeEventError):
        ar.parse_process_completed({**evento, "resultado": "cancelado"}, tenant_id=TENANT)
