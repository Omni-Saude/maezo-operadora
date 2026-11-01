"""Retorno do pedido de reembolso ao beneficiario (DL-0159/DL-0160, `MAEZO_REEMBOLSO_V1`).

O BPMN publica `agents.events.process_completed` com `resultado` de reembolso SO' nas instancias que o Lucas
abriu (`aviso_beneficiario_whatsapp=true`); o `agent_resume` confere o HISTORICO do motor e o Lucas envia a
moldura FIXA; o texto que o ANALISTA escreveu (negativa, reducao, pedido de informacao) entra entre aspas,
como escrito, e so' ele passa pelas cercas de saida. Prova, sem motor real:
  * aprovado / pago -> texto fixo (pago com a data registrada), UMA vez, chave propria;
  * parcial / negado / info -> a moldura com o texto da equipe; texto barrado pelas cercas -> o aviso sai
    SEM o texto e diz isso;
  * evento sem respaldo no historico (aviso desligado, outra conversa, decisao que nao bate, protocolo que
    nao e' o da chave) -> fila morta, nada enviado; sem texto da equipe / pago sem data -> a equipe e'
    avisada;
  * ancora errada (chave ESC, outro agente) -> fila morta; sem a fonte de historico -> nada enviado;
  * fora da janela de 24h -> nada sai e a equipe e' avisada.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from maezo.domain.reembolso import pedido as rp
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.tool_registry import build_agent_seam_context
from maezo.platform.integrations import agent_resume as ar
from maezo.platform.webhooks.whatsapp import lucas_retomada as lr
from maezo.platform.webhooks.whatsapp.dedup import WhatsAppDedupGuard
from maezo.platform.webhooks.whatsapp.security import hash_phone
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, HistoricProcessVariables
from tests.support.dedup_fakes import FakeDedupRegistry

TENANT = "amh"
NUMERO = "5511900000124"  # sintetico
INSTANCIA = "inst-reemb-1"
PROTOCOLO = "REE-AB12-CD34"
CHAVE = f"REEMB-{TENANT}-{PROTOCOLO}"


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


async def _retomar(
    retomada: lr.LucasRetomada, conversa: str, resultado: str, **extra: Any
) -> Mapping[str, Any]:
    argumentos: dict[str, Any] = {
        "conversation_id": conversa,
        "resultado": resultado,
        "protocolo": PROTOCOLO,
        "texto_da_equipe": "",
        "data_pagamento_iso": None,
        "raw_to": NUMERO,
        "resume_ref": f"{INSTANCIA}:{resultado}",
    }
    argumentos.update(extra)
    return await retomada.retomar_reembolso(**argumentos)


# --- a porta do Lucas ---------------------------------------------------------------------------------


async def test_aprovado_e_pago_enviam_o_texto_fixo_uma_vez() -> None:
    retomada, cliente, conversa = _montar()
    for _ in range(2):  # reentrega do evento: a mesma chave, um envio so'
        r = await _retomar(retomada, conversa, rp.RESULTADO_APROVADO)
        assert r["desfecho"] == lr.DESFECHO_RETOMADA_ENVIADA
    await _retomar(retomada, conversa, rp.RESULTADO_PAGO, data_pagamento_iso="2026-10-27")
    textos = [t for _, t, _ in cliente.enviados]
    assert textos == [
        rp.TEXTO_RETORNO_APROVADO.format(protocolo=PROTOCOLO),
        rp.TEXTO_RETORNO_PAGO.format(protocolo=PROTOCOLO, data="27/10/2026"),
    ]


@pytest.mark.parametrize(
    ("resultado", "modelo"),
    [
        (rp.RESULTADO_NEGADO, rp.TEXTO_RETORNO_NEGADO),
        (rp.RESULTADO_PARCIAL, rp.TEXTO_RETORNO_PARCIAL),
        (rp.RESULTADO_INFO_SOLICITADA, rp.TEXTO_RETORNO_INFO),
    ],
)
async def test_texto_da_equipe_entra_entre_aspas_como_escrito(resultado: str, modelo: str) -> None:
    retomada, cliente, conversa = _montar()
    escrito = "O procedimento não está na cobertura do seu contrato, conforme a cláusula informada."
    await _retomar(retomada, conversa, resultado, texto_da_equipe=escrito)
    ((destino, texto, _),) = cliente.enviados
    assert destino == NUMERO and texto == modelo.format(protocolo=PROTOCOLO, texto=escrito)


async def test_texto_barrado_pelas_cercas_sai_sem_o_texto_e_diz_isso() -> None:
    retomada, cliente, conversa = _montar()
    barrado = "Aguarde que um atendente vai te ligar amanhã para explicar."  # promessa de humano
    r = await _retomar(retomada, conversa, rp.RESULTADO_NEGADO, texto_da_equipe=barrado)
    assert r["desfecho"] == lr.DESFECHO_RETOMADA_RECUSADA
    ((_, texto, _),) = cliente.enviados
    assert texto == rp.TEXTO_RETORNO_TEXTO_BARRADO.format(protocolo=PROTOCOLO)
    assert "atendente" not in texto and "ligar" not in texto


async def test_texto_enorme_e_barrado() -> None:
    retomada, cliente, conversa = _montar()
    r = await _retomar(retomada, conversa, rp.RESULTADO_NEGADO, texto_da_equipe="x " * 2000)
    assert r["desfecho"] == lr.DESFECHO_RETOMADA_RECUSADA and len(cliente.enviados) == 1


async def test_sem_texto_da_equipe_ou_destino_alheio_nao_envia_nada() -> None:
    retomada, cliente, conversa = _montar()
    with pytest.raises(ValueError):
        await _retomar(retomada, conversa, rp.RESULTADO_NEGADO, texto_da_equipe="")
    with pytest.raises(ValueError):
        await _retomar(retomada, conversa, rp.RESULTADO_PAGO, data_pagamento_iso="27/10")
    with pytest.raises(ValueError):
        await _retomar(retomada, conversa, rp.RESULTADO_APROVADO, raw_to="5511900000999")
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
    async def fetch(self, event: Any) -> str:  # pragma: no cover - o retorno do reembolso nao le notas
        raise AssertionError("o retorno do reembolso nunca le notas de atendente")


def _motor(conversa: str, **variaveis: Any) -> FakeCibSevenTransport:
    motor = FakeCibSevenTransport()
    base: dict[str, Any] = {
        "aviso_beneficiario_whatsapp": True,
        "conversation_id": conversa,
        "protocolo_reembolso": PROTOCOLO,
        "decisao_reembolso": "APROVAR",
        "data_pagamento": "2026-10-27",
        "texto_ao_beneficiario": "Texto da equipe sobre o pedido.",
        "info_solicitada": "Envie a nota fiscal legível.",
    }
    base.update(variaveis)
    motor.seed_historic_variables(
        HistoricProcessVariables(
            instance_id=INSTANCIA,
            business_key=CHAVE,
            process_key="SP-OP-REEMBOLSO-001",
            state="COMPLETED",
            variables=base,
        )
    )
    return motor


def _evento(conversa: str, resultado: str = rp.RESULTADO_APROVADO, **extra: Any) -> Mapping[str, Any]:
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
        reembolsos=ar.EngineHistoryReembolsoSource(motor) if motor is not None else None,
    )
    return handler, alerta


@pytest.mark.parametrize(
    ("resultado", "decisao"),
    [
        (rp.RESULTADO_APROVADO, "APROVAR"),
        (rp.RESULTADO_PARCIAL, "APROVAR_PARCIAL"),
        (rp.RESULTADO_NEGADO, "NEGAR"),
        (rp.RESULTADO_INFO_SOLICITADA, "SOLICITAR_INFO"),
        (rp.RESULTADO_PAGO, "APROVAR"),
        (rp.RESULTADO_PAGO, "APROVAR_PARCIAL"),
    ],
)
async def test_o_handler_confere_o_historico_e_entrega_cada_resultado(resultado: str, decisao: str) -> None:
    retomada, cliente, conversa = _montar()
    handler, alerta = _handler(retomada, _motor(conversa, decisao_reembolso=decisao))
    recibo = await handler.handle(_evento(conversa, resultado))
    assert recibo.outcome is ar.ResumeOutcome.RESUMED and recibo.resultado == resultado
    ((_, texto, _),) = cliente.enviados
    assert PROTOCOLO in texto and (alerta.fora, alerta.sem_entrega) == (0, 0)


async def test_o_texto_de_info_vem_de_info_solicitada_e_o_de_negativa_de_texto_ao_beneficiario() -> None:
    retomada, cliente, conversa = _montar()
    motor = _motor(conversa, decisao_reembolso="SOLICITAR_INFO")
    handler, _ = _handler(retomada, motor)
    await handler.handle(_evento(conversa, rp.RESULTADO_INFO_SOLICITADA))
    assert "Envie a nota fiscal legível." in cliente.enviados[0][1]
    assert "Texto da equipe sobre o pedido." not in cliente.enviados[0][1]


@pytest.mark.parametrize(
    "variaveis",
    [
        {"aviso_beneficiario_whatsapp": False},  # instancia sem a chave: nunca avisa
        {"conversation_id": "wa:amh:hk1_outra"},  # outra conversa
        {"decisao_reembolso": "NEGAR"},  # a decisao nao e' a que o resultado afirma (aprovado)
        {"protocolo_reembolso": "REE-FFFF-FFFF"},  # nao e' o protocolo da chave
        {"protocolo_reembolso": "livre"},  # fora da forma
    ],
)
async def test_sem_respaldo_no_historico_vai_para_a_fila_morta_sem_enviar(variaveis: dict[str, Any]) -> None:
    retomada, cliente, conversa = _montar()
    handler, _ = _handler(retomada, _motor(conversa, **variaveis))
    with pytest.raises(ar.MalformedResumeEventError):
        await handler.handle(_evento(conversa))
    assert cliente.enviados == []


@pytest.mark.parametrize(
    ("resultado", "decisao", "ausente"),
    [
        (rp.RESULTADO_NEGADO, "NEGAR", "texto_ao_beneficiario"),
        (rp.RESULTADO_PARCIAL, "APROVAR_PARCIAL", "texto_ao_beneficiario"),
        (rp.RESULTADO_INFO_SOLICITADA, "SOLICITAR_INFO", "info_solicitada"),
        (rp.RESULTADO_PAGO, "APROVAR", "data_pagamento"),
    ],
)
async def test_sem_o_fato_que_o_texto_afirma_a_equipe_e_avisada_e_nada_sai(
    resultado: str, decisao: str, ausente: str
) -> None:
    retomada, cliente, conversa = _montar()
    handler, alerta = _handler(retomada, _motor(conversa, decisao_reembolso=decisao, **{ausente: None}))
    with pytest.raises(ar.ResumeUndeliverableError):
        await handler.handle(_evento(conversa, resultado))
    assert cliente.enviados == [] and alerta.sem_entrega == 1


async def test_outra_instancia_de_processo_nao_serve() -> None:
    retomada, cliente, conversa = _montar()
    motor = FakeCibSevenTransport()
    motor.seed_historic_variables(
        HistoricProcessVariables(
            instance_id=INSTANCIA,
            business_key=CHAVE,
            process_key="SP-OP-CANCEL-001",
            state="COMPLETED",
            variables={
                "aviso_beneficiario_whatsapp": True,
                "conversation_id": conversa,
                "protocolo_reembolso": PROTOCOLO,
                "decisao_reembolso": "APROVAR",
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
        {"_business_key": "ESC-amh-wa:amh:hk1_x"},  # chave do escalonamento num resultado de reembolso
        {"_business_key": "CANCEL-amh-x"},
        {"agent_id": "helena"},  # o retorno do reembolso e' do Lucas
        {"_business_key": "REEMB-outro-tenant-x"},
    ],
)
async def test_ancora_errada_vai_para_a_fila_morta(extra: dict[str, Any]) -> None:
    retomada, cliente, conversa = _montar()
    handler, _ = _handler(retomada, _motor(conversa))
    with pytest.raises(ar.MalformedResumeEventError):
        await handler.handle(_evento(conversa, **extra))
    assert cliente.enviados == []


async def test_evento_de_reembolso_com_phi_e_recusado_antes_de_tudo() -> None:
    retomada, cliente, conversa = _montar()
    handler, _ = _handler(retomada, _motor(conversa))
    for campo in ("texto_ao_beneficiario", "matricula_beneficiario", "info_solicitada"):
        with pytest.raises(ar.MalformedResumeEventError):
            await handler.handle(_evento(conversa, **{campo: "x"}))
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


async def test_os_cinco_resultados_sao_aceitos_e_um_desconhecido_nao() -> None:
    conversa = "wa:amh:hk1_abc"
    for resultado in sorted(rp.RESULTADOS_DO_REEMBOLSO):
        parsed = ar.parse_process_completed(_evento(conversa, resultado), tenant_id=TENANT)
        assert parsed.resultado == resultado
    with pytest.raises(ar.MalformedResumeEventError):
        ar.parse_process_completed(_evento(conversa, "reembolso_cancelado"), tenant_id=TENANT)
