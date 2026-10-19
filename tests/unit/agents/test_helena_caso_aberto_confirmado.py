"""DL-0144 (frente C do SP-OP-ESCALATION-001): o caso aberto que a Helena pode AFIRMAR vem do MOTOR.

O defeito: "seu atendimento continua com a equipe" saia para quem ja' teve o caso ENCERRADO pelo atendente,
porque a Helena so' olhava as ultimas trocas do historico curto (`_caso_aberto_no_historico`). Agora
`receive` grava `caso_aberto_confirmado` lendo a instancia ATIVA de SP-OP-ESCALATION-001 nas chaves desta
conversa (`find_active_instance`), e so' quando o historico traz indicio de caso (sem indicio, zero leituras).

Parte (a): o campo e a leitura. Os pontos que afirmam caso aberto estao em
`test_helena_caso_aberto_afirmavel.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena.graph import (
    CASO_ABERTO_ADMINISTRATIVO,
    CASO_ABERTO_CLINICO,
    PROCESS_KEY,
    RESPOSTA_ESCALONAMENTO_URGENTE,
    RESPOSTA_HANDOFF_JA_ABERTO,
    HelenaGraph,
    new_helena_state,
)
from maezo.runtime.caso_clinico import chave_do_escalonamento
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

TENANT = "amh"
CONVERSA = "wa:amh:hk1_casoconfirmado"
CHAVE = chave_do_escalonamento(TENANT, CONVERSA)
CHAVE_CLIN = chave_do_escalonamento(TENANT, CONVERSA, clinico=True)


class _MotorContado(FakeCibSevenTransport):
    """O `FakeCibSevenTransport` contando as leituras `find_active_instance` (e, se pedido, falhando)."""

    def __init__(self, *, falha: Exception | None = None) -> None:
        super().__init__()
        self.leituras: list[str] = []
        self._falha = falha

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        self.leituras.append(business_key)
        if self._falha is not None:
            raise self._falha
        return await super().find_active_instance(business_key)


class _MotorSemLeitura:
    """Transporte minimo, sem `find_active_instance` (os dubles antigos de teste)."""


def _instancia(chave: str, *, estado: str = "ACTIVE") -> ProcessInstance:
    return ProcessInstance(
        instance_id=f"inst-{chave}",
        process_key=PROCESS_KEY,
        business_key=chave,
        state=estado,
        already_existed=True,
    )


def _helena(motor: Any) -> HelenaGraph:
    return HelenaGraph(
        inference=None,  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=motor,
        audit_sink=FakeStartAuditSink(),
        whatsapp=None,  # type: ignore[arg-type]
        historico_enabled=True,
    )


def _estado(falas_da_helena: list[str] | None = None, **plantado: Any) -> dict[str, Any]:
    estado: dict[str, Any] = dict(
        new_helena_state(
            tenant_id=TENANT,
            conversation_id=CONVERSA,
            canal="whatsapp",
            beneficiario_pseudo_id="PSEUDO-C1",
            message_body="obrigado, tchau",
            message_ref="hk1_msg",
        )
    )
    agora = datetime.now(UTC).isoformat()
    historico: list[dict[str, str]] = []
    for fala in falas_da_helena or []:
        historico.append({"papel": "beneficiario", "texto": "mensagem anterior", "em": agora})
        historico.append({"papel": "helena", "texto": fala, "em": agora})
    if historico:
        estado["historico_conversa"] = historico
        estado["ultima_mensagem_em"] = agora
    estado.update(plantado)
    return estado


_ADMIN = [RESPOSTA_HANDOFF_JA_ABERTO]
_CLINICO = [RESPOSTA_ESCALONAMENTO_URGENTE]


async def test_sem_indicio_no_historico_nenhuma_leitura_e_nada_confirmado() -> None:
    motor = _MotorContado()
    motor.seed_instance(_instancia(CHAVE), variables={"motivo_categoria": "cobranca"})
    saida = await _helena(motor).receive(_estado())  # type: ignore[arg-type]
    assert saida["caso_aberto_confirmado"] is None
    assert motor.leituras == []


async def test_valor_plantado_por_quem_chama_nao_atravessa() -> None:
    motor = _MotorContado()
    saida = await _helena(motor).receive(_estado(caso_aberto_confirmado=CASO_ABERTO_CLINICO))  # type: ignore[arg-type]
    assert saida["caso_aberto_confirmado"] is None
    assert motor.leituras == []


async def test_instancia_administrativa_ativa_confirma_administrativo() -> None:
    motor = _MotorContado()
    motor.seed_instance(_instancia(CHAVE), variables={"motivo_categoria": "cobranca"})
    saida = await _helena(motor).receive(_estado(_ADMIN))  # type: ignore[arg-type]
    assert saida["caso_aberto_confirmado"] == CASO_ABERTO_ADMINISTRATIVO
    assert motor.leituras == [CHAVE_CLIN, CHAVE]


@pytest.mark.parametrize("motivo", ["red_flag_clinico", "risco_psicossocial"])
async def test_instancia_de_alerta_clinico_na_chave_da_conversa_confirma_clinico(motivo: str) -> None:
    motor = _MotorContado()
    motor.seed_instance(_instancia(CHAVE), variables={"motivo_categoria": motivo})
    saida = await _helena(motor).receive(_estado(_ADMIN))  # type: ignore[arg-type]
    assert saida["caso_aberto_confirmado"] == CASO_ABERTO_CLINICO


async def test_caso_clinico_paralelo_ativo_confirma_clinico_com_uma_leitura() -> None:
    motor = _MotorContado()
    motor.seed_instance(_instancia(CHAVE_CLIN), variables={"motivo_categoria": "red_flag_clinico"})
    saida = await _helena(motor).receive(_estado(_CLINICO))  # type: ignore[arg-type]
    assert saida["caso_aberto_confirmado"] == CASO_ABERTO_CLINICO
    assert motor.leituras == [CHAVE_CLIN]


async def test_caso_encerrado_no_motor_nao_e_confirmado_mesmo_com_o_historico_dizendo_que_esta_aberto() -> (
    None
):
    motor = _MotorContado()
    motor.seed_instance(
        _instancia(CHAVE, estado="COMPLETED"), variables={"motivo_categoria": "red_flag_clinico"}
    )
    saida = await _helena(motor).receive(_estado(_CLINICO))  # type: ignore[arg-type]
    assert saida["caso_aberto_confirmado"] is None
    assert motor.leituras == [CHAVE_CLIN, CHAVE]


async def test_sem_instancia_nenhuma_nao_e_confirmado() -> None:
    saida = await _helena(_MotorContado()).receive(_estado(_ADMIN))  # type: ignore[arg-type]
    assert saida["caso_aberto_confirmado"] is None


@pytest.mark.parametrize(
    "falha",
    [CibSevenError("motor fora (teste)"), OSError("rede fora (teste)"), RuntimeError("portao (teste)")],
    ids=["cibseven", "oserror", "runtime"],
)
async def test_leitura_que_falha_nao_confirma_nao_levanta_e_loga_so_o_tipo(falha: Exception) -> None:
    motor = _MotorContado(falha=falha)
    with capture_logs() as logs:
        saida = await _helena(motor).receive(_estado(_ADMIN))  # type: ignore[arg-type]
    assert saida["caso_aberto_confirmado"] is None
    eventos = [e for e in logs if e.get("event") == "helena_caso_aberto_leitura_falhou"]
    assert len(eventos) == 1
    assert set(eventos[0]) == {"event", "log_level", "error_type"}
    assert eventos[0]["error_type"] == type(falha).__name__


async def test_erro_de_programacao_na_leitura_propaga() -> None:
    motor = _MotorContado(falha=TypeError("bug (teste)"))
    with pytest.raises(TypeError):
        await _helena(motor).receive(_estado(_ADMIN))  # type: ignore[arg-type]


async def test_transporte_sem_leitura_nao_confirma() -> None:
    saida = await _helena(_MotorSemLeitura()).receive(_estado(_ADMIN))  # type: ignore[arg-type]
    assert saida["caso_aberto_confirmado"] is None
