"""Gatilho de LOOP da Helena (DL-0148; pendencia 16 da planilha do diretor).

A FICHA: `spec/agents/helena/agent.yaml`, `escalation.triggers` -> `signal: tool_failure  # falha
tecnica/loop -> falha_tecnica`; a DMN `escalation_routing` r6 ("Falha tecnica do agente (loop, tool
down)"). Previsto e nunca implementado: `_sem_repetir` so' variava o texto.

O QUE ESTE ARQUIVO PROVA:
  1. a Helena prestes a mandar a MESMA resposta pela `REPETICOES_PARA_ESCALAR + 1`-esima vez seguida
     escala `falha_tecnica` (severidade `null`), pelo start idempotente, com o texto fixo de handoff;
  2. uma repeticao a menos nao escala (o texto so' varia, como antes);
  3. conversa normal, texto curto e repeticao intercalada nao escalam;
  4. caso ja' aberto na conversa nao abre outro; rota que ja' escala nao escala de novo;
  5. falha do start -> a resposta honesta de falha (nunca promessa de humano);
  6. o numero do codigo e' o da ficha (igualdade fixada).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from maezo.agents.helena import graph as g
from maezo.agents.helena.graph import HelenaGraph, HelenaState
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_CONVERSA = "wa:amh:hk1_repeticao"
_CHAVE = f"ESC-amh-{_CONVERSA}"
#: Resposta longa (>= 60 caracteres) que a Helena ja' mandou: um texto FIXO do proprio grafo.
_A = g.RESPOSTA_INFORM_RECUSADA
_B = g.RESPOSTA_FORA_DO_CANAL
_AGORA = datetime.now(UTC).isoformat()


class _Inferencia:
    def __init__(self, classificacoes: list[dict[str, Any]] | None = None, rascunho: str = "") -> None:
        self._classificacoes = [json.dumps(c) for c in (classificacoes or [])]
        self._rascunho = rascunho
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        if "Tarefa: leia a mensagem do beneficiario" in prompt:
            return self._classificacoes.pop(0)
        return self._rascunho or "resumo do caso"


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


class _CibSevenQueFalha(FakeCibSevenTransport):
    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        raise CibSevenError("motor fora")


def _dmn() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": False, "prioridade": "-", "conduta": "CONTINUE", "motivo": "sem criterio"}],
    )
    return dmn


def _grafo(cib: FakeCibSevenTransport, zap: _WhatsApp, inferencia: _Inferencia | None = None) -> HelenaGraph:
    return HelenaGraph(
        inference=cast(InferenceProvider, inferencia or _Inferencia()),
        dmn=_dmn(),
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=zap,
        historico_enabled=True,
    )


def _historico(*falas_da_helena: str) -> list[dict[str, str]]:
    hist: list[dict[str, str]] = []
    for fala in falas_da_helena:
        hist.append({"papel": "beneficiario", "texto": "e ai?", "em": _AGORA})
        hist.append({"papel": "helena", "texto": fala, "em": _AGORA})
    return hist


def _estado(falas: tuple[str, ...], texto: str = _A, **extra: Any) -> HelenaState:
    estado: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": _CONVERSA,
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-REP-0001",
        "message_body": "e ai?",
        "response_kind": "inform",
        "response_text": texto,
        "historico_conversa": _historico(*falas),
    }
    estado.update(extra)
    return cast(HelenaState, estado)


def _starts(cib: FakeCibSevenTransport) -> int:
    return sum(cib._generations.values())


# --- 1. N repeticoes escalam, com o motivo certo ---------------------------------------------------


@pytest.mark.asyncio
async def test_n_repeticoes_escalam_falha_tecnica_pelo_start_idempotente() -> None:
    cib, zap = FakeCibSevenTransport(), _WhatsApp()
    falas = (_A,) * g.REPETICOES_PARA_ESCALAR
    saida = await _grafo(cib, zap).respond(_estado(falas))

    assert saida["escalation_started"] is True
    assert saida["escalation_motivo"] == "falha_tecnica"
    assert saida["escalation_severidade"] is None
    assert saida["response_kind"] == "escalate"
    assert saida["error"] == g.ERRO_HELENA_EM_REPETICAO
    assert saida["desfecho"] == "escalado_humano"
    # o texto e' o handoff fixo JA' APROVADO para `falha_tecnica` (sem sinal de saude), nunca a repeticao
    assert zap.enviados == [g.RESPOSTA_HANDOFF_RECUSADA]
    assert _starts(cib) == 1
    variaveis = (await cib.get_process_status(_CHAVE)).variables
    assert variaveis["motivo_categoria"] == "falha_tecnica"
    assert variaveis["severidade"] is None
    assert g.ERRO_HELENA_EM_REPETICAO in variaveis["resumo_contexto"]
    # o turno entra no historico com o handoff: o proximo turno ve o caso aberto
    assert saida["historico_conversa"][-1]["texto"] == g.RESPOSTA_HANDOFF_RECUSADA


@pytest.mark.asyncio
async def test_prefixo_de_variacao_nao_esconde_a_repeticao() -> None:
    """`_sem_repetir` manda "Entendi. <mesmo texto>": continua sendo a mesma resposta."""
    cib, zap = FakeCibSevenTransport(), _WhatsApp()
    falas = (_A,) + ("Entendi. " + _A,) * (g.REPETICOES_PARA_ESCALAR - 1)
    saida = await _grafo(cib, zap).respond(_estado(falas))
    assert saida["escalation_motivo"] == "falha_tecnica"
    assert _starts(cib) == 1


# --- 2. N-1 nao escala ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uma_repeticao_a_menos_nao_escala() -> None:
    cib, zap = FakeCibSevenTransport(), _WhatsApp()
    falas = (_A,) * (g.REPETICOES_PARA_ESCALAR - 1)
    saida = await _grafo(cib, zap).respond(_estado(falas))
    assert saida.get("escalation_started") is not True
    assert _starts(cib) == 0
    assert len(zap.enviados) == 1 and zap.enviados[0].endswith(_A)  # so' variada, como antes


# --- 3. conversa normal, texto curto, repeticao intercalada ---------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "falas",
    [
        (),
        (_B, _B),
        (_A, _B),
        (_A, _B) + (_A,) * (g.REPETICOES_PARA_ESCALAR - 1),
    ],
    ids=["sem-historico", "outra-resposta", "anterior-diferente", "intercalada"],
)
async def test_conversa_normal_nao_escala(falas: tuple[str, ...]) -> None:
    cib, zap = FakeCibSevenTransport(), _WhatsApp()
    saida = await _grafo(cib, zap).respond(_estado(falas))
    assert saida.get("escalation_started") is not True
    assert _starts(cib) == 0


@pytest.mark.asyncio
async def test_texto_curto_repetido_e_legitimo() -> None:
    curto = g.RESPOSTA_SAUDACAO_CURTA
    assert len(curto) < 60
    cib, zap = FakeCibSevenTransport(), _WhatsApp()
    saida = await _grafo(cib, zap).respond(_estado((curto,) * 5, texto=curto))
    assert saida.get("escalation_started") is not True
    assert zap.enviados == [curto]


# --- 4. caso ja' aberto / rota que ja' escala -----------------------------------------------------


@pytest.mark.asyncio
async def test_caso_ja_aberto_na_conversa_nao_abre_outro() -> None:
    cib, zap = FakeCibSevenTransport(), _WhatsApp()
    falas = (g.RESPOSTA_HANDOFF_RECUSADA,) + (_A,) * (g.REPETICOES_PARA_ESCALAR + 2)
    saida = await _grafo(cib, zap).respond(_estado(falas))
    assert saida.get("escalation_started") is not True
    assert _starts(cib) == 0


@pytest.mark.asyncio
async def test_rota_que_ja_escala_nao_escala_de_novo() -> None:
    cib, zap = FakeCibSevenTransport(), _WhatsApp()
    estado = _estado((_A,) * 5, response_kind="escalate", escalation_started=True, start_desfecho="novo")
    await _grafo(cib, zap).respond(estado)
    assert _starts(cib) == 0


# --- 5. start que falha --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_que_falha_diz_a_verdade() -> None:
    cib, zap = _CibSevenQueFalha(), _WhatsApp()
    saida = await _grafo(cib, zap).respond(_estado((_A,) * g.REPETICOES_PARA_ESCALAR))
    assert saida["start_failed"] is True
    assert zap.enviados == [g.RESPOSTA_FALHA_TECNICA_START]


# --- 6. ponta a ponta no grafo compilado (historico ligado) ---------------------------------------


@pytest.mark.asyncio
async def test_ponta_a_ponta_a_mesma_resposta_vira_caso_e_o_caso_nao_se_repete() -> None:
    rascunho = (
        "Por aqui eu consigo te orientar sobre o seu plano e sobre como funciona o atendimento, "
        "mas preciso que voce me diga o que deseja."
    )
    turnos = g.REPETICOES_PARA_ESCALAR + 2
    classificacao = {
        "intent": "information",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    cib, zap = FakeCibSevenTransport(), _WhatsApp()
    grafo = _grafo(cib, zap, _Inferencia([classificacao] * turnos, rascunho))
    compilado = grafo.compile_graph().compile(checkpointer=InMemorySaver())
    config: RunnableConfig = {"configurable": {"thread_id": _CONVERSA}}
    estados = []
    for _ in range(turnos):
        entrada = {
            "tenant_id": "amh",
            "conversation_id": _CONVERSA,
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "PSEUDO-REP-0001",
            "message_body": "Quero saber o horario da central",
        }
        estados.append(dict(await compilado.ainvoke(cast(HelenaState, entrada), config)))

    n = g.REPETICOES_PARA_ESCALAR
    # os N primeiros turnos: a mesma resposta (variada), nenhum caso
    assert all(e.get("escalation_started") is not True for e in estados[:n])
    assert len({g._sem_prefixo(t) for t in zap.enviados[:n]}) == 1
    # o turno N+1: escala `falha_tecnica`, com o handoff fixo
    assert estados[n]["escalation_motivo"] == "falha_tecnica"
    assert zap.enviados[n] == g.RESPOSTA_HANDOFF_RECUSADA
    # o turno seguinte NAO abre outro caso
    assert _starts(cib) == 1


# --- 7. o numero e' o da ficha ---------------------------------------------------------------------


def test_o_numero_do_codigo_e_o_da_ficha() -> None:
    ficha = yaml.safe_load(Path("spec/agents/helena/agent.yaml").read_text(encoding="utf-8"))
    loop = ficha["escalation"]["loop"]
    assert loop["repeticoes_para_escalar"] == g.REPETICOES_PARA_ESCALAR
    assert loop["status"] == "PENDENTE_VALOR_DO_DONO"
    assert {"signal": "tool_failure"} in ficha["escalation"]["triggers"]
