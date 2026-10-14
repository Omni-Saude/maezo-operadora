"""DL-0099 — rodada 12: a regra do recem-nascido vem da TABELA pediatrica (r9-r13, ratificadas pelo dono).

O grafo so' (a) preenche `idade_meses=0` quando o recem-nascido vem dito em palavras e (b) mantem a regra
deterministica `_alerta_neonatal` como fail-safe. Quem decide o red flag e' a tabela (ADR-0012).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.agents.helena.graph import HelenaGraph, new_helena_state
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.agents.test_helena_rodada_10_bebe_e_caso_aberto import _classify, _Modelo, _WhatsApp

_LINHA_NEONATAL = {
    "red_flag": True,
    "prioridade": "P1",
    "conduta": "ESCALATE_EMERGENCY",
    "motivo": "Recem-nascido (menos de 28 dias) com sinal de sofrimento: emergencia neonatal",
}
_SEM_BANDEIRA = {"red_flag": False, "prioridade": "-", "conduta": "CONTINUE", "motivo": "Sem criterio"}


def _grafo(respostas: list[str], *, pediatrica: dict[str, Any]) -> tuple[Any, _WhatsApp, FakeDmnTransport]:
    whatsapp = _WhatsApp()
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_pediatric", [pediatrica] * 4)
    dmn.register("triage_redflag_adult", [_SEM_BANDEIRA] * 4)
    grafo = HelenaGraph(
        inference=_Modelo(respostas),  # type: ignore[arg-type]
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        historico_enabled=True,
        relogio=lambda: datetime(2026, 10, 14, 15, 0, tzinfo=UTC),
    )
    return grafo.compile_graph().compile(), whatsapp, dmn


def _entrada(mensagem: str, falas: list[str] | None = None) -> dict[str, Any]:
    estado: dict[str, Any] = dict(
        new_helena_state(
            tenant_id="amh",
            conversation_id="wa:amh:hk1_rn_r12",
            canal="whatsapp",
            beneficiario_pseudo_id="PSEUDO-RN-R12",
            message_body=mensagem,
            message_ref="hk1_msg_rn_r12",
        )
    )
    if falas:
        agora = datetime.now(UTC).isoformat()
        estado["historico_conversa"] = [{"papel": "beneficiario", "texto": f, "em": agora} for f in falas]
        estado["ultima_mensagem_em"] = agora
    return estado


@pytest.mark.parametrize(
    "codigo",
    ["choro_inconsolavel", "recusa_alimentar", "hipotonia", "cianose", "vomitos_persistentes"],
)
async def test_a_tabela_e_a_fonte_primaria_o_codigo_neonatal_escala_como_grave(codigo: str) -> None:
    grafo, whatsapp, dmn = _grafo(
        [_classify(intent="symptom", population="pediatric", sintoma_codigo=codigo, idade_meses=0)],
        pediatrica=_LINHA_NEONATAL,
    )

    resultado = await grafo.ainvoke(_entrada("meu bebe nasceu ha 5 dias e esta muito estranho"))

    assert dmn.calls[0][0] == "triage_redflag_pediatric"
    assert dmn.calls[0][1]["idade_meses"] == 0
    assert dmn.calls[0][1]["sintoma_codigo"] == codigo
    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert resultado["escalation_severidade"] == "grave"
    assert "192" in whatsapp.enviados[0]


async def test_recem_nascido_dito_em_palavras_vira_idade_zero_para_a_tabela() -> None:
    grafo, _, dmn = _grafo(
        [_classify(intent="symptom", population="pediatric", sintoma_codigo="choro_inconsolavel")],
        pediatrica=_LINHA_NEONATAL,
    )

    await grafo.ainvoke(_entrada("minha filha nasceu ha 5 dias e chora sem parar"))

    assert dmn.calls[0][1]["idade_meses"] == 0


async def test_idade_dita_pelo_modelo_nao_e_sobrescrita() -> None:
    grafo, _, dmn = _grafo(
        [_classify(intent="symptom", population="pediatric", sintoma_codigo="febre", idade_meses=5)],
        pediatrica=_SEM_BANDEIRA,
    )

    await grafo.ainvoke(_entrada("meu bebe de 5 meses esta com febre"))

    assert dmn.calls[0][1]["idade_meses"] == 5


async def test_sem_recem_nascido_a_idade_continua_desconhecida() -> None:
    grafo, _, dmn = _grafo(
        [_classify(intent="symptom", population="pediatric", sintoma_codigo="febre")],
        pediatrica=_SEM_BANDEIRA,
    )

    await grafo.ainvoke(_entrada("meu filho esta com febre"))

    assert dmn.calls[0][1]["idade_meses"] is None


async def test_fail_safe_do_grafo_escala_mesmo_quando_o_modelo_nao_devolve_o_codigo() -> None:
    """Cinto e suspensorio: sem codigo neonatal a tabela le' `-`, e a regra do grafo ainda escala."""
    grafo, whatsapp, _ = _grafo(
        [_classify(intent="symptom", population="pediatric")], pediatrica=_SEM_BANDEIRA
    )

    resultado = await grafo.ainvoke(_entrada("minha filha recém-nascida está chorando muito, deve ser dor"))

    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert resultado["escalation_severidade"] == "grave"
    assert "192" in whatsapp.enviados[0]
