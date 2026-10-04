"""LU090 (bateria do Lucas, 02/10/2026; DL-0072): alerta clinico grave nunca e suprimido por um caso
de outra classe. Com cobranca aberta na conversa, "dor no peito e falta de ar" abre um caso clinico
PROPRIO (`...-clin`); o caso de cobranca segue como esta. Com caso clinico ja aberto, nao abre outro.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena.graph import HelenaGraph, HelenaState
from maezo.runtime.caso_clinico import chave_do_escalonamento, chave_e_da_conversa
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

TENANT = "amh"
CONVERSA = "wa:amh:deadbeef"
CHAVE = chave_do_escalonamento(TENANT, CONVERSA)
CHAVE_CLINICA = chave_do_escalonamento(TENANT, CONVERSA, clinico=True)


class _Inferencia:
    async def generate(self, *_: Any, **__: Any) -> str:
        return "resumo"


class _WhatsApp:
    async def send(self, *_: Any, **__: Any) -> dict[str, Any]:
        return {}


class _Gravador(FakeCibSevenTransport):
    def __init__(self) -> None:
        super().__init__()
        self.inicios: list[tuple[str, dict[str, Any]]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.inicios.append((business_key, dict(variables)))
        return await super().start_process_instance(process_key, business_key, variables)


def _grafo(cibseven: FakeCibSevenTransport) -> HelenaGraph:
    return HelenaGraph(
        inference=_Inferencia(),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),  # type: ignore[arg-type]
    )


def _estado(**extra: Any) -> HelenaState:
    estado: dict[str, Any] = {
        "tenant_id": TENANT,
        "conversation_id": CONVERSA,
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-1",
        "message_body": "estou com dor no peito e falta de ar",
        **extra,
    }
    return estado  # type: ignore[return-value]


def _com_caso_aberto(motivo: str | None) -> _Gravador:
    cib = _Gravador()
    cib.seed_instance(
        ProcessInstance(
            instance_id="caso-1",
            process_key="SP-OP-ESCALATION-001",
            business_key=CHAVE,
            state="ACTIVE",
            already_existed=True,
        ),
        variables={"motivo_categoria": motivo} if motivo else None,
    )
    return cib


GRAVE = {"escalation_motivo": "red_flag_clinico", "escalation_severidade": "grave"}


@pytest.mark.parametrize("motivo_aberto", ["cobranca", "solicitacao_humano", "falha_tecnica", "outro", None])
async def test_alerta_clinico_grave_com_caso_de_outra_classe_abre_caso_clinico_proprio(
    motivo_aberto: str | None,
) -> None:
    cib = _com_caso_aberto(motivo_aberto)
    resultado = await _grafo(cib).escalate(_estado(**GRAVE))

    assert resultado["escalation_business_key"] == CHAVE_CLINICA
    assert resultado["start_desfecho"] == "novo"
    assert resultado["escalation_process_ref"]["instance_id"] != "caso-1"
    variaveis = dict(cib.inicios)[CHAVE_CLINICA]
    assert variaveis["motivo_categoria"] == "red_flag_clinico"
    assert variaveis["severidade"] == "grave"
    assert "outro caso aberto" in variaveis["resumo_contexto"]
    # o caso que ja existia continua exatamente como estava: nada foi cancelado nem alterado
    status = await cib.get_process_status(CHAVE)
    assert status.instance_id == "caso-1"


@pytest.mark.parametrize("motivo_clinico", ["red_flag_clinico", "risco_psicossocial"])
async def test_com_caso_clinico_ja_aberto_nao_abre_outro(motivo_clinico: str) -> None:
    cib = _com_caso_aberto(motivo_clinico)
    resultado = await _grafo(cib).escalate(_estado(**GRAVE))

    assert resultado["start_desfecho"] == "ja_ativo"
    assert resultado["escalation_business_key"] == CHAVE
    assert all(k != CHAVE_CLINICA for k, _ in cib.inicios)


async def test_alerta_clinico_nao_grave_nao_abre_caso_paralelo() -> None:
    cib = _com_caso_aberto("cobranca")
    resultado = await _grafo(cib).escalate(
        _estado(escalation_motivo="red_flag_clinico", escalation_severidade="moderada")
    )

    assert resultado["start_desfecho"] == "ja_ativo"
    assert all(k != CHAVE_CLINICA for k, _ in cib.inicios)


async def test_motivo_nao_clinico_nunca_abre_caso_clinico() -> None:
    cib = _com_caso_aberto("cobranca")
    resultado = await _grafo(cib).escalate(
        _estado(escalation_motivo="solicitacao_humano", escalation_severidade="moderada")
    )

    assert resultado["start_desfecho"] == "ja_ativo"
    assert all(k != CHAVE_CLINICA for k, _ in cib.inicios)


async def test_segundo_alerta_com_o_caso_clinico_paralelo_ja_aberto_nao_abre_um_terceiro() -> None:
    cib = _com_caso_aberto("cobranca")
    grafo = _grafo(cib)

    primeiro = await grafo.escalate(_estado(**GRAVE))
    segundo = await grafo.escalate(_estado(**GRAVE))

    assert primeiro["escalation_business_key"] == CHAVE_CLINICA
    assert primeiro["start_desfecho"] == "novo"
    assert segundo["escalation_business_key"] == CHAVE_CLINICA
    assert segundo["start_desfecho"] == "ja_ativo"
    assert (
        segundo["escalation_process_ref"]["instance_id"] == primeiro["escalation_process_ref"]["instance_id"]
    )


async def test_sem_caso_aberto_a_chave_e_a_de_sempre() -> None:
    cib = _Gravador()
    resultado = await _grafo(cib).escalate(_estado(**GRAVE))

    assert resultado["escalation_business_key"] == CHAVE
    assert [k for k, _ in cib.inicios] == [CHAVE]


async def test_falha_ao_ler_o_caso_aberto_abre_o_caso_clinico() -> None:
    cib = _com_caso_aberto("cobranca")

    async def _falha(_: str) -> Any:
        raise CibSevenError("motor fora")

    cib.get_process_status = _falha  # type: ignore[method-assign]
    resultado = await _grafo(cib).escalate(_estado(**GRAVE))

    assert resultado["escalation_business_key"] == CHAVE_CLINICA


def test_a_chave_clinica_e_reconhecida_como_da_conversa() -> None:
    assert chave_e_da_conversa(CHAVE, TENANT, CONVERSA)
    assert chave_e_da_conversa(CHAVE_CLINICA, TENANT, CONVERSA)
    assert not chave_e_da_conversa(CHAVE + "-outra", TENANT, CONVERSA)
    assert not chave_e_da_conversa(chave_do_escalonamento(TENANT, "wa:amh:outra"), TENANT, CONVERSA)
