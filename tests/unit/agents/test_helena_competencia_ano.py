"""O ANO da competencia de cobranca e' decidido pelo texto do beneficiario, nunca pelo modelo (10/10/2026).

Teste real do dono (dev, 10/10/2026): "Qual foi o valor da mensalidade de julho?" -> o Lucas respondeu com a
competencia 07/2024 — o classify da Helena devolveu `2024-07` para um texto sem ano. Decisao do dono: sem ano
explicito no texto, a competencia e' a ocorrencia MAIS RECENTE do mes citado que nao esta' no futuro em
relacao a hoje (fuso de Brasilia); com ano explicito, vale o ano dito. O ponto e' deterministico, depois do
classify (`resolver_ano_da_competencia`), com relogio injetavel.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import pytest

from maezo.agents.helena.graph import HelenaGraph, new_helena_state
from maezo.runtime.competencia import hoje_em_brasilia, resolver_ano_da_competencia
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_HOJE = date(2026, 10, 10)


@pytest.mark.parametrize(
    ("do_modelo", "texto", "esperado"),
    [
        # O defeito medido: sem ano no texto, o ano do modelo nao vale.
        ("2024-07", "Qual foi o valor da mensalidade de julho?", "2026-07"),
        ("2026-07", "Qual foi o valor da mensalidade de julho?", "2026-07"),
        ("2026-05", "Quanto paguei em maio?", "2026-05"),
        # Mes depois do mes de hoje: a ocorrencia mais recente e' a do ano passado.
        ("2026-11", "e a de novembro?", "2025-11"),
        # O proprio mes de hoje nao esta' no futuro.
        ("2025-10", "a mensalidade de outubro", "2026-10"),
        ("2026-01", "segunda via de janeiro", "2026-01"),
        ("2026-12", "boleto de dezembro", "2025-12"),
        # Ano explicito: vale o ano dito.
        ("2024-07", "valor da mensalidade de julho de 2024", "2024-07"),
        ("2026-07", "valor da mensalidade de julho de 2024", "2024-07"),
        ("2023-03", "boleto 03/2023", "2023-03"),
        ("2027-01", "boleto de janeiro de 2027", "2027-01"),
        # Dois anos ditos e o do modelo e' um deles: o modelo escolheu entre os ditos.
        ("2025-07", "julho de 2025 ou julho de 2024?", "2025-07"),
        # As duas formas relativas tratadas.
        ("2026-07", "julho do ano passado", "2025-07"),
        ("2024-11", "novembro deste ano", "2026-11"),
        ("2024-03", "março desse ano", "2026-03"),
    ],
)
def test_o_ano_vem_do_texto_ou_da_ocorrencia_mais_recente(do_modelo: str, texto: str, esperado: str) -> None:
    assert resolver_ano_da_competencia(do_modelo, texto, _HOJE) == esperado


@pytest.mark.parametrize("ausente", [None, "07/2024", "2024-13", 202407])
def test_competencia_ausente_ou_fora_da_forma_volta_como_veio(ausente: Any) -> None:
    """Quem valida e recusa e' o chamador (`_validate_extraction`); aqui nada e' inventado."""
    assert resolver_ano_da_competencia(ausente, "mensalidade de julho", _HOJE) == ausente


def test_texto_que_nao_e_str_e_lido_como_sem_ano() -> None:
    assert resolver_ano_da_competencia("2024-07", None, _HOJE) == "2026-07"


def test_hoje_e_o_dia_em_brasilia_nao_o_de_utc() -> None:
    # 01/11/2026 02:00 UTC ainda e' 31/10/2026 em Brasilia: novembro ainda esta' no futuro.
    agora = datetime(2026, 11, 1, 2, 0, tzinfo=UTC)
    assert hoje_em_brasilia(agora) == date(2026, 10, 31)
    assert resolver_ano_da_competencia("2026-11", "novembro", hoje_em_brasilia(agora)) == "2025-11"
    assert hoje_em_brasilia(datetime(2026, 11, 1, 2, 0)) == date(2026, 10, 31)  # sem fuso = UTC


# --- No grafo: o classify grava a competencia resolvida e o handoff ao Lucas a carrega -------------------


class _Inferencia:
    def __init__(self, resposta: str) -> None:
        self._resposta = resposta

    async def generate(self, prompt: str, **_: Any) -> str:
        del prompt
        return self._resposta


class _Envio:
    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        del to_hash, text
        return {"ok": True}


def _classify_json(competencia: str | None) -> str:
    return json.dumps(
        {
            "intent": "cobranca",
            "population": "none",
            "psychosocial_risk": False,
            "sintoma_codigo": None,
            "intensidade": "desconhecida",
            "cobranca_subtipo": "consulta_valores",
            "competencia": competencia,
        }
    )


async def _handoff(texto: str, do_modelo: str | None) -> dict[str, Any]:
    grafo = HelenaGraph(
        inference=_Inferencia(_classify_json(do_modelo)),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_Envio(),
        roteador_lucas_enabled=True,
        # 10/10/2026 12:00 em Brasilia.
        relogio=lambda: datetime(2026, 10, 10, 15, 0, tzinfo=UTC),
    )
    estado = new_helena_state(
        tenant_id="amh",
        conversation_id="wa:amh:hk1_competencia",
        canal="whatsapp",
        beneficiario_pseudo_id="pseudo-competencia",
        message_body=texto,
        message_ref="hk1_competencia",
    )
    resultado = await grafo.compile_graph().compile().ainvoke(estado)
    handoff = resultado["handoff"]
    assert handoff["para"] == "lucas"
    assert resultado["cobranca_competencia"] == handoff["competencia"]
    return dict(handoff)


@pytest.mark.parametrize(
    ("texto", "do_modelo", "esperado"),
    [
        ("Qual foi o valor da mensalidade de julho?", "2024-07", "2026-07"),
        ("Quanto foi a mensalidade de novembro?", "2026-11", "2025-11"),
        ("Quanto foi a mensalidade de outubro?", "2025-10", "2026-10"),
        ("Qual foi o valor da mensalidade de julho de 2024?", "2024-07", "2024-07"),
        ("Qual o valor da minha mensalidade?", None, None),
    ],
    ids=["julho_sem_ano", "novembro_sem_ano", "outubro_sem_ano", "julho_de_2024", "ausente"],
)
async def test_o_handoff_ao_lucas_leva_a_competencia_resolvida(
    texto: str, do_modelo: str | None, esperado: str | None
) -> None:
    handoff = await _handoff(texto, do_modelo)
    assert handoff["competencia"] == esperado
