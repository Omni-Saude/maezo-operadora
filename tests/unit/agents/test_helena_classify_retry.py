"""Classificador da Helena: uma segunda chamada quando o JSON vem ilegivel ou fora do schema.

Bateria de 02/10/2026: ~2% das classificacoes falhavam e cada falha perdia a leitura de saude. A repeticao
converte falha transitoria em acerto, NUNCA aceita JSON invalido, e NAO repete excecao do provedor.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import CLASSIFY_TENTATIVAS, HelenaGraph
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_VALIDO = json.dumps(
    {
        "intent": "symptom",
        "population": "adult",
        "psychosocial_risk": False,
        "sintoma_codigo": "dor_toracica",
        "intensidade": "grave",
        "idade_anos": 54,
        "gestante": False,
        "idade_gestacional_semanas": None,
    }
)


class _Roteirizado:
    def __init__(self, respostas: list[Any]) -> None:
        self.respostas = list(respostas)
        self.chamadas = 0

    async def generate(self, *_: Any, **__: Any) -> str:
        self.chamadas += 1
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return cast(str, r)


def _grafo(llm: _Roteirizado) -> HelenaGraph:
    return HelenaGraph(
        inference=cast(InferenceProvider, llm),
        whatsapp=cast(Any, object()),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )


_ESTADO: Any = {"tenant_id": "amh", "message_body": "estou com dor no peito"}


@pytest.mark.asyncio
async def test_json_ilegivel_na_primeira_e_valido_na_segunda_recupera() -> None:
    llm = _Roteirizado(["isto nao e json", _VALIDO])
    extracao, falha = await _grafo(llm)._classify_llm(_ESTADO)
    assert falha is None
    assert extracao is not None and extracao["sintoma_codigo"] == "dor_toracica"
    assert llm.chamadas == 2


@pytest.mark.asyncio
async def test_schema_invalido_nas_duas_continua_falha_fail_closed() -> None:
    invalido = json.dumps(
        {
            "intent": "symptom",
            "population": "adult",
            "psychosocial_risk": False,
            "sintoma_codigo": "codigo_inventado",
            "intensidade": "grave",
        }
    )
    llm = _Roteirizado([invalido, invalido])
    extracao, falha = await _grafo(llm)._classify_llm(_ESTADO)
    assert extracao is None
    assert falha is not None and "schema-invalid" in falha
    assert llm.chamadas == CLASSIFY_TENTATIVAS


@pytest.mark.asyncio
async def test_sucesso_na_primeira_nao_chama_de_novo() -> None:
    llm = _Roteirizado([_VALIDO])
    extracao, falha = await _grafo(llm)._classify_llm(_ESTADO)
    assert falha is None and extracao is not None
    assert llm.chamadas == 1


@pytest.mark.asyncio
async def test_excecao_do_provedor_nao_e_repetida() -> None:
    llm = _Roteirizado([TimeoutError("provedor fora"), _VALIDO])
    extracao, falha = await _grafo(llm)._classify_llm(_ESTADO)
    assert extracao is None
    assert falha is not None and falha.startswith("classify LLM call failed")
    assert llm.chamadas == 1
