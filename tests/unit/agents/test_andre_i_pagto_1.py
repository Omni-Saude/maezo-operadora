"""Andre amarrado a I-PAGTO-1 (ADR-0040 D6-bis + OQ-13 correcao D-m1) — pacote PAGTO PR-B.

ADR-0040 OQ-13 (D-m1): o caminho de start de Andre semeava os quatro fatos de admissibilidade de
SP-OP-PAGTO-001 a partir do payload A2A; era fail-closed SO porque `process_keys` de Andre esta
vazio. A propria OQ-13 exige que, antes de qualquer concessao de chave, `_contract_variables`
fique vinculado a I-PAGTO-1: "nunca semear `lastro_confirmado`/`dados_pagamento_validos`/
`duplicidade_suspeita`/`dentro_teto_l2`". Este modulo prova esse vinculo SEM tocar em
`process_keys` (ato de seguranca/dono) e prova o conjunto proibido ampliado aos fatos novos do
pacote (analyses/PAGTO-balanced-classification.md §3/§9; dono 2026-10-31, #1084).

UMA fonte: `pagto.PAGTO_FATOS_PROIBIDOS_NA_SEMENTE` (o processo dono dos fatos). Os handoffs de
CONTAS/RECURSO e o start de Andre leem o MESMO conjunto — nenhuma copia a mao.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.andre.graph import AndreGraph, AndreState
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers import contas, recurso
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from maezo.tools.workers.pagto import PAGTO_FATOS_PROIBIDOS_NA_SEMENTE
from tests.support.audit_fakes import FakeStartAuditSink

_QUATRO_FATOS_ORIGINAIS = frozenset(
    {"lastro_confirmado", "dados_pagamento_validos", "duplicidade_suspeita", "dentro_teto_l2"}
)

#: Fatos que o pacote PAGTO introduz (v0.2 da admissibilidade, §3 da analise) + o teto do
#: aprovado (saida da DMN, PR-A) + a identidade do analista que a SoD compara (PR-A) + o
#: `tier_minimo` que o guard do tier le (saida da DMN; follow-up A2 do PR-D).
_FATOS_NOVOS = frozenset(
    {
        "lastro_status",
        "lastro_origem_tipo",
        "duplicidade_status",
        "prestador_situacao",
        "prestador_conhecido",
        "valor_maximo_aprovavel_cents",
        "analista_admissibilidade_id",
        "tier_minimo",
    }
)


class _FakeInference:
    async def generate(self, prompt: str, **kwargs: Any) -> str:
        del prompt, kwargs
        return "dossie sintetico"


class _RecordingCibSeven(FakeCibSevenTransport):
    def __init__(self) -> None:
        super().__init__()
        self.started_variables: list[dict[str, Any]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.started_variables.append(dict(variables))
        return await super().start_process_instance(process_key, business_key, variables)


def _graph(cibseven: FakeCibSevenTransport | None = None) -> AndreGraph:
    return AndreGraph(
        inference=_FakeInference(),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )


def _estado_com_fatos_semeados(**overrides: Any) -> AndreState:
    """Payload A2A que chega com TODOS os fatos 'confirmados' — o vetor da OQ-13."""
    state: dict[str, Any] = {
        "flow": "pagto_dossier",
        "tenant_id": "amh",
        "canal": "a2a",
        "ordem_pagamento_id": "000000001",
        "prestador_id": "prestador-1",
        "tipo_pagamento": "prestador_rede",
        "valor_pagamento_cents": 85_000,
        "moeda": "BRL",
        "competencia": "2026-06",
        "data_vencimento": "2026-08-01",
        "conta_origem_ref": "token-conta-1",
        "instrumento_pagamento": "pix",
        "dados_pagamento_validos": True,
        "lastro_confirmado": True,
        "dentro_teto_l2": True,
        "duplicidade_suspeita": False,
    }
    state.update(overrides)
    return state  # type: ignore[return-value]


# ---------------------------------------------------------------------------------------------
# O conjunto canonico
# ---------------------------------------------------------------------------------------------


def test_conjunto_canonico_cobre_os_quatro_fatos_e_os_fatos_novos() -> None:
    assert _QUATRO_FATOS_ORIGINAIS <= PAGTO_FATOS_PROIBIDOS_NA_SEMENTE
    assert _FATOS_NOVOS <= PAGTO_FATOS_PROIBIDOS_NA_SEMENTE
    assert PAGTO_FATOS_PROIBIDOS_NA_SEMENTE == _QUATRO_FATOS_ORIGINAIS | _FATOS_NOVOS


def test_evidencia_de_lastro_nao_e_fato_proibido() -> None:
    """A origem fornece EVIDENCIA (`lastro_origem`/`lastro_decisor_id`) — nunca o fato."""
    assert not ({"lastro_origem", "lastro_decisor_id"} & PAGTO_FATOS_PROIBIDOS_NA_SEMENTE)


@pytest.mark.parametrize("modulo", [contas, recurso], ids=["contas", "recurso"])
def test_handoffs_usam_o_conjunto_canonico(modulo: Any) -> None:
    assert modulo.HANDOFF_PAGTO_FORBIDDEN_KEYS is PAGTO_FATOS_PROIBIDOS_NA_SEMENTE
    assert not (modulo.HANDOFF_PAGTO_SEEDED_KEYS & PAGTO_FATOS_PROIBIDOS_NA_SEMENTE)


# ---------------------------------------------------------------------------------------------
# Andre: o start nunca semeia um fato que PAGTO resolve
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["auto_route", "human_review"])
def test_variaveis_de_start_de_andre_nunca_carregam_fato_proibido(route: str) -> None:
    variables = _graph()._contract_variables(_estado_com_fatos_semeados(route=route))
    vazados = sorted(set(variables) & PAGTO_FATOS_PROIBIDOS_NA_SEMENTE)
    assert vazados == [], f"Andre semeia fatos que SP-OP-PAGTO-001 resolve sozinho: {vazados}"


def test_fatos_novos_plantados_no_estado_nao_chegam_ao_engine() -> None:
    """Um estado com os fatos NOVOS plantados tambem nao os embarca (allowlist, nao filtro)."""
    planted = {name: "PLANTADO" for name in _FATOS_NOVOS}
    variables = _graph()._contract_variables(_estado_com_fatos_semeados(**planted))
    assert not (set(variables) & PAGTO_FATOS_PROIBIDOS_NA_SEMENTE)


async def test_start_real_de_andre_nao_semeia_fato_proibido() -> None:
    """Turno de start completo: as variaveis que chegam ao engine sao disjuntas do conjunto."""
    cibseven = _RecordingCibSeven()
    graph = _graph(cibseven)
    result = await graph.start_process(
        _estado_com_fatos_semeados(route="auto_route", business_key="PAGTO-amh-000000001")
    )
    assert result["process_started"] is True
    started = cibseven.started_variables[0]
    assert not (set(started) & PAGTO_FATOS_PROIBIDOS_NA_SEMENTE)
    # A identidade/valor da ordem continuam indo: so os FATOS deixam de ser semeados.
    assert started["valor_pagamento_cents"] == 85_000
    assert started["ordem_pagamento_id"] == "000000001"
