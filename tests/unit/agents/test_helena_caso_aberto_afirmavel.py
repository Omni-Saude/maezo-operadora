"""DL-0116 (frente C do SP-OP-ESCALATION-001), parte (b): so' o caso CONFIRMADO no motor e' afirmado.

Os quatro pontos que AFIRMAM caso aberto ao beneficiario — a despedida, a navegacao, a resposta substituta
do `inform` recusado e o sinal `caso_aberto` do prompt — leem `_caso_aberto_afirmavel` (o
`caso_aberto_confirmado` de `receive`). O historico continua INDICIO para seguranca e rota: o alerta de
emergencia com caso aberto (DL-0072) nao espera o motor.

Contra o grafo REAL da Helena (modelo roteirizado, motor `FakeCibSevenTransport`), com os mesmos
auxiliares da rodada 3 do simulador.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena.graph import (
    PROCESS_KEY,
    RESPOSTA_DESPEDIDA,
    RESPOSTA_DESPEDIDA_CASO_ABERTO,
    RESPOSTA_ESCALONAMENTO_URGENTE,
    RESPOSTA_HANDOFF_RECUSADA,
    RESPOSTA_INFORM_CASO_ABERTO,
    RESPOSTA_NAVEGACAO,
    RESPOSTA_NAVEGACAO_CASO_ABERTO,
)
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from tests.unit.agents.test_helena_rodada_3_simulador import (
    _CHAVE,
    _caso_aberto,
    _classify,
    _entrada,
    _grafo,
)

_DESPEDIDA = "Valeu pela ajuda... ou falta dela kkk"
_NAVEGACAO = "Blz, mas não é emergência não. Só queria saber se preciso ir no médico..."
_SINAL_DO_PROMPT = "'caso_aberto': True"
_RASCUNHO_BARRADO = "Já encaminhamos seu caso, e a equipe vai entrar em contato com você."


def _encerrado(motivo: str) -> FakeCibSevenTransport:
    """O caso que o atendente ja' concluiu: a instancia existe no historico do motor, mas nao esta ATIVA."""
    cib = FakeCibSevenTransport()
    cib.seed_instance(
        ProcessInstance(
            instance_id="caso-encerrado",
            process_key=PROCESS_KEY,
            business_key=_CHAVE,
            state="COMPLETED",
            already_existed=True,
        ),
        variables={"motivo_categoria": motivo, "resultado": "resolvido_humano"},
    )
    return cib


def _clinico_paralelo() -> FakeCibSevenTransport:
    """Cobranca na chave da conversa e o caso clinico paralelo (`-clin`, DL-0072) ativo."""
    cib = _caso_aberto("cobranca")
    cib.seed_instance(
        ProcessInstance(
            instance_id="caso-clin",
            process_key=PROCESS_KEY,
            business_key=f"{_CHAVE}-clin",
            state="ACTIVE",
            already_existed=True,
        ),
        variables={"motivo_categoria": "red_flag_clinico"},
    )
    return cib


class _MotorQueNaoLe(FakeCibSevenTransport):
    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        raise CibSevenError("motor fora (teste)")


# --- despedida ------------------------------------------------------------------------------------


@pytest.mark.parametrize("motivo", ["solicitacao_humano", "red_flag_clinico"])
async def test_caso_encerrado_recebe_a_despedida_comum_nunca_a_de_caso_aberto(motivo: str) -> None:
    grafo, whatsapp, _, _ = _grafo([_classify(intent="human_request")], cib=_encerrado(motivo))
    resultado = await grafo.ainvoke(_entrada(_DESPEDIDA, [RESPOSTA_HANDOFF_RECUSADA]))
    assert resultado["caso_aberto_confirmado"] is None
    assert whatsapp.enviados == [RESPOSTA_DESPEDIDA]
    assert RESPOSTA_DESPEDIDA_CASO_ABERTO not in whatsapp.enviados


async def test_caso_ativo_no_motor_recebe_a_despedida_de_caso_aberto() -> None:
    grafo, whatsapp, _, _ = _grafo(
        [_classify(intent="human_request")], cib=_caso_aberto("solicitacao_humano")
    )
    await grafo.ainvoke(_entrada(_DESPEDIDA, [RESPOSTA_HANDOFF_RECUSADA]))
    assert whatsapp.enviados == [RESPOSTA_DESPEDIDA_CASO_ABERTO]


async def test_leitura_que_falha_nao_afirma_e_nao_derruba_o_turno() -> None:
    grafo, whatsapp, _, _ = _grafo([_classify(intent="human_request")], cib=_MotorQueNaoLe())
    resultado = await grafo.ainvoke(_entrada(_DESPEDIDA, [RESPOSTA_HANDOFF_RECUSADA]))
    assert resultado["caso_aberto_confirmado"] is None
    assert whatsapp.enviados == [RESPOSTA_DESPEDIDA]


# --- navegacao: so' com caso CLINICO ativo ----------------------------------------------------------


@pytest.mark.parametrize(
    "motor",
    [
        pytest.param(lambda: _caso_aberto("red_flag_clinico"), id="alerta_clinico_na_chave"),
        pytest.param(lambda: _caso_aberto("risco_psicossocial"), id="risco_psicossocial_na_chave"),
        pytest.param(_clinico_paralelo, id="clin_paralelo_ativo"),
    ],
)
async def test_navegacao_cita_o_caso_de_saude_so_com_instancia_clinica_ativa(motor: Any) -> None:
    grafo, whatsapp, _, _ = _grafo([_classify()], cib=motor())
    await grafo.ainvoke(_entrada(_NAVEGACAO, [RESPOSTA_ESCALONAMENTO_URGENTE]))
    assert whatsapp.enviados == [RESPOSTA_NAVEGACAO_CASO_ABERTO]


@pytest.mark.parametrize(
    "motor",
    [
        pytest.param(lambda: _encerrado("red_flag_clinico"), id="clinico_encerrado"),
        pytest.param(lambda: _caso_aberto("cobranca"), id="so_cobranca_ativa"),
        pytest.param(FakeCibSevenTransport, id="sem_instancia"),
        pytest.param(_MotorQueNaoLe, id="leitura_falha"),
    ],
)
async def test_navegacao_sem_caso_clinico_ativo_nao_cita_caso(motor: Any) -> None:
    grafo, whatsapp, _, _ = _grafo([_classify()], cib=motor())
    resultado = await grafo.ainvoke(_entrada(_NAVEGACAO, [RESPOSTA_ESCALONAMENTO_URGENTE]))
    assert resultado.get("escalation_motivo") != "red_flag_clinico"
    assert whatsapp.enviados == [RESPOSTA_NAVEGACAO]


# --- inform recusado e prompt -----------------------------------------------------------------------


async def test_inform_recusado_com_caso_encerrado_nao_diz_que_o_caso_continua() -> None:
    grafo, whatsapp, modelo, _ = _grafo([_classify(), _RASCUNHO_BARRADO], cib=_encerrado("cobranca"))
    resultado = await grafo.ainvoke(_entrada("e aí, alguma novidade?", [RESPOSTA_HANDOFF_RECUSADA]))
    assert resultado["error"]  # a cerca barrou o rascunho
    assert whatsapp.enviados and whatsapp.enviados[0] != RESPOSTA_INFORM_CASO_ABERTO
    # O sinal do contexto (o texto fixo do prompt explica a chave; o que conta e' o `contexto=` do turno).
    assert _SINAL_DO_PROMPT not in modelo.prompts[1], "o prompt nao afirma caso que o motor nao confirmou"


async def test_inform_recusado_com_caso_ativo_diz_que_o_caso_continua() -> None:
    grafo, whatsapp, modelo, _ = _grafo([_classify(), _RASCUNHO_BARRADO], cib=_caso_aberto("cobranca"))
    await grafo.ainvoke(_entrada("e aí, alguma novidade?", [RESPOSTA_HANDOFF_RECUSADA]))
    assert whatsapp.enviados == [RESPOSTA_INFORM_CASO_ABERTO]
    assert _SINAL_DO_PROMPT in modelo.prompts[1]


# --- seguranca: a emergencia continua pelo historico ------------------------------------------------


@pytest.mark.parametrize(
    "motor", [FakeCibSevenTransport, _MotorQueNaoLe], ids=["sem_instancia", "leitura_falha"]
)
async def test_emergencia_com_historico_clinico_e_motor_sem_caso_continua_escalando(motor: Any) -> None:
    """DL-0072 intacto: o sinal de emergencia com caso clinico no historico NAO espera o motor."""
    grafo, _, _, _ = _grafo([_classify()], cib=motor())
    resultado = await grafo.ainvoke(
        _entrada("ELE TA ROXO, PARANDO DE RESPIRAR", [RESPOSTA_ESCALONAMENTO_URGENTE])
    )
    assert resultado["caso_aberto_confirmado"] is None
    assert resultado["next_kind"] == "escalate"
    assert resultado["escalation_motivo"] == "red_flag_clinico"
