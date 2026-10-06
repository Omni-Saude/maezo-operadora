"""DL-0076 (05/10/2026): a Helena se apresenta de novo depois de 12 h sem a pessoa escrever.

O defeito: o cartao era uma vez por CONVERSA, e a conversa de um numero de WhatsApp nao expira. Quem voltava
dias depois (o numero do proprio testador, em 04/10) mandava "oi" e recebia a frase curta, como se a Helena
ainda estivesse no mesmo atendimento. Agora o sinal `apresentacao_ja_feita` so' atravessa o turno quando o
carimbo `ultima_mensagem_em` e' recente; o PRAZO (12 h) e' decisao de produto e esta' marcado provisorio.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import (
    APRESENTACAO_VALIDADE_HORAS,
    RESPOSTA_SAUDACAO_ABERTURA,
    RESPOSTA_SAUDACAO_CURTA,
    HelenaGraph,
    HelenaState,
    _apresentacao_continua_valida,
)
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_AGORA = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _ha(horas: float) -> str:
    return (_AGORA - timedelta(hours=horas)).isoformat()


@pytest.mark.parametrize(
    ("carimbo", "vale"),
    [
        (_ha(0), True),
        (_ha(11.9), True),
        (_ha(12), False),
        (_ha(30), False),
        (_ha(-1), False),  # no futuro: carimbo plantado ou relogio torto
        (None, False),  # conversa de antes da regra
        ("", False),
        ("ontem de manha", False),
        (12345, False),
        ("2026-10-05T11:00:00", True),  # sem fuso: lido como UTC
    ],
)
def test_o_cartao_vale_ate_a_janela_e_o_resto_reapresenta(carimbo: Any, vale: bool) -> None:
    assert _apresentacao_continua_valida(carimbo, agora=_AGORA) is vale


def test_a_janela_e_de_12_horas() -> None:
    assert APRESENTACAO_VALIDADE_HORAS == 12.0


class _Gravador:
    def __init__(self) -> None:
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        del prompt
        return json.dumps(
            {
                "intent": "greeting",
                "population": "none",
                "psychosocial_risk": False,
                "sintoma_codigo": None,
                "intensidade": "desconhecida",
            }
        )


class _WhatsApp:
    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        return {"ok": True}


def _graph() -> HelenaGraph:
    return HelenaGraph(
        inference=cast(InferenceProvider, _Gravador()),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
    )


def _estado(**extra: Any) -> HelenaState:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_validade",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-VAL",
        "message_body": "oi",
    }
    base.update(extra)
    return cast(HelenaState, base)


async def _responde(**extra: Any) -> str:
    compilado = _graph().compile_graph().compile()
    return str((await compilado.ainvoke(_estado(**extra)))["response_text"])


@pytest.mark.asyncio
async def test_depois_de_12_horas_o_oi_recebe_o_cartao_de_novo() -> None:
    """O caso do testador: 'oi' num numero que ja' tinha conversado, dias antes."""
    antigo = (datetime.now(UTC) - timedelta(hours=13)).isoformat()

    assert (
        await _responde(apresentacao_ja_feita=True, ultima_mensagem_em=antigo) == RESPOSTA_SAUDACAO_ABERTURA
    )


@pytest.mark.asyncio
async def test_dentro_das_12_horas_o_oi_recebe_a_frase_curta() -> None:
    recente = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    assert await _responde(apresentacao_ja_feita=True, ultima_mensagem_em=recente) == RESPOSTA_SAUDACAO_CURTA


@pytest.mark.asyncio
async def test_conversa_de_antes_da_regra_sem_carimbo_e_reapresentada_uma_vez() -> None:
    """Sem `ultima_mensagem_em` (checkpoint antigo) o cartao reaparece e o carimbo e' gravado no turno."""
    graph = _graph()
    reset = await graph.receive(_estado(apresentacao_ja_feita=True))

    assert reset["apresentacao_ja_feita"] is False
    assert _apresentacao_continua_valida(reset["ultima_mensagem_em"], agora=datetime.now(UTC)) is True


@pytest.mark.asyncio
async def test_cada_turno_da_pessoa_renova_o_carimbo() -> None:
    graph = _graph()
    quase_vencido = (datetime.now(UTC) - timedelta(hours=11)).isoformat()

    reset = await graph.receive(_estado(apresentacao_ja_feita=True, ultima_mensagem_em=quase_vencido))

    assert reset["apresentacao_ja_feita"] is True
    renovado = datetime.fromisoformat(reset["ultima_mensagem_em"])
    assert (datetime.now(UTC) - renovado).total_seconds() < 60
