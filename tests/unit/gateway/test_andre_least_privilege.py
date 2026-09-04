"""Regressao de MENOR PRIVILEGIO para o Andre — gap `ANDRE-PROCESS-KEYS` (decisao do dono R-036).

O defeito original: `spec/agents/andre/agent.yaml` declarava a tool `mcp-cibseven.start_process`
("somente SP-OP-PAGTO-001") e a acao de autonomia `start_compliance_process`, mas NAO declarava
nenhuma `process_keys`. Como `AgentCapabilities.of` intersecta `process_keys` com
`KNOWN_PROCESS_KEYS` (ADR-0016) e `allows_process_key` e um teste de pertinencia sobre esse
conjunto, o effect-PEP ja NEGAVA todo start do Andre — o manifesto afirmava uma autoridade que o
PEP nunca concedeu. Fail-closed por acidente, nao por decisao.

A decisao do dono (R-036, opcao B, 2026-09-04) foi ESTREITAR o manifesto ate o comportamento real:
remover a tool e a acao de autonomia, e FIXAR a recusa do PEP num teste de regressao. Este arquivo
e esse teste, e ele le o MANIFESTO REAL de `spec/agents/andre/agent.yaml` pelo carregador de
producao (`AgentLoader`, o mesmo que `tool_registry.build_agent_seam_context` usa) — de proposito:
uma reintroducao da concessao no YAML shipado tem de ficar VERMELHA aqui.

Nao ha `process_key` nova para agente nenhum. Reconceder autoridade de start ao Andre e um ato
explicito do dono (uma linha de YAML sob revisao), e voltaria a falhar estes testes primeiro.
"""

from __future__ import annotations

import pytest

from maezo.agents import AgentDefinition, AgentLoader
from maezo.gateway.effect_pep import (
    KNOWN_PROCESS_KEYS,
    REASON_PROCESS_KEY_FORBIDDEN,
    REASON_TOOL_UNDECLARED,
    AgentCapabilities,
    DecisionContext,
    EffectLayer,
    decide_effect,
)

_TENANT = "amh"
_ANDRE = "andre"
_START_OP = "cibseven.start_process"
_START_TOOL = "mcp-cibseven.start_process"
_START_ACTION = "start_compliance_process"
_PAGTO_KEY = "SP-OP-PAGTO-001"
_ADEQUACAO_KEY = "SP-OP-ADEQUACAO-001"


@pytest.fixture(scope="module")
def andre() -> AgentDefinition:
    """O manifesto SHIPADO, pelo carregador de producao — nunca uma fixture reescrita aqui."""
    return AgentLoader().load_by_id(_ANDRE)


@pytest.fixture(scope="module")
def andre_capabilities(andre: AgentDefinition) -> AgentCapabilities:
    """A MESMA construcao que `tool_registry.build_agent_seam_context` faz em producao."""
    return AgentCapabilities.of(principal=andre.id, tools=andre.tools, process_keys=andre.process_keys)


# ---------------------------------------------------------------------------
# Nivel manifesto — o que o YAML declara
# ---------------------------------------------------------------------------


def test_andre_nao_declara_a_tool_de_start_de_processo(andre: AgentDefinition) -> None:
    """R-036: a tool saiu do `tools:`. Reintroduzi-la e um ato de dono, nao um refactor."""
    assert _START_TOOL not in andre.tools


def test_andre_nao_declara_a_acao_de_autonomia_de_start(andre: AgentDefinition) -> None:
    """R-036: a acao de autonomia saiu junto com a tool — as duas metades da mesma concessao."""
    assert _START_ACTION not in andre.autonomy_actions


def test_andre_nao_declara_process_keys(andre: AgentDefinition) -> None:
    """A ausencia que tornava a declaracao anterior contraditoria — agora e a posicao coerente."""
    assert andre.process_keys == []


def test_andre_mantem_as_tools_que_o_fluxo_do_dossie_realmente_exerce(
    andre: AgentDefinition,
) -> None:
    """A remocao ESTREITA privilegio e nada mais: o resto da allowlist tight segue intacto."""
    assert andre.tools == [
        "mcp-dmn.evaluate",
        "mcp-memory.read_write",
        "mcp-fhir.read_patient",
    ]
    assert andre.autonomy_actions == [
        "query_decision_engine",
        "read_write_memory",
        "read_phi_data",
    ]


# ---------------------------------------------------------------------------
# Nivel PEP — o que a visao de capacidade concede
# ---------------------------------------------------------------------------


def test_a_visao_de_capacidade_do_andre_nao_carrega_process_key_alguma(
    andre_capabilities: AgentCapabilities,
) -> None:
    assert andre_capabilities.process_keys == frozenset()


@pytest.mark.parametrize("process_key", sorted(KNOWN_PROCESS_KEYS))
def test_o_pep_recusa_toda_process_key_conhecida_para_o_andre(
    andre_capabilities: AgentCapabilities, process_key: str
) -> None:
    """TODO o universo ADR-0016 (15 chaves), incluindo PAGTO (R-036) e ADEQUACAO (R-049).

    Parametrizado sobre `KNOWN_PROCESS_KEYS` em vez de uma lista copiada, para que uma 16a chave
    futura entre nesta prova automaticamente.
    """
    assert andre_capabilities.allows_process_key(process_key) is False


def test_o_pep_recusa_a_propria_tool_de_start_para_o_andre(
    andre_capabilities: AgentCapabilities,
) -> None:
    assert andre_capabilities.allows_tool(_START_TOOL) is False


def test_o_start_de_pagto_pelo_andre_e_negado_em_l1(
    andre_capabilities: AgentCapabilities,
) -> None:
    """A ladeira COMPLETA, com a visao de capacidade real do Andre: DENY em L-1 (CAPACIDADE).

    A razao hoje e `TOOL_NAO_DECLARADA` porque a tool foi removida e L-1 pergunta pela tool ANTES
    da process_key (`effect_pep._l1_capability`). A recusa por chave de processo continua provada
    logo abaixo, na configuracao em que ela e a que morde.
    """
    decision = decide_effect(
        tenant=_TENANT,
        principal=_ANDRE,
        operation=_START_OP,
        process_key=_PAGTO_KEY,
        ctx=DecisionContext(capabilities=andre_capabilities),
    )
    assert decision.allow is False
    assert decision.reason == REASON_TOOL_UNDECLARED
    assert decision.layer == EffectLayer.CAPACIDADE.value


@pytest.mark.parametrize("process_key", [_PAGTO_KEY, _ADEQUACAO_KEY])
def test_mesmo_reconcedendo_a_tool_o_start_morre_na_process_key(
    andre: AgentDefinition, process_key: str
) -> None:
    """A recusa que a decisao R-036 mandou fixar: `REASON_PROCESS_KEY_FORBIDDEN` para o Andre.

    Reconceder SO a tool (o meio-caminho tentador de quem quiser reabrir a concessao) nao move o
    veredito: sem `process_keys` no manifesto, L-1 nega na camada da chave. Provar as DUAS pernas
    e o que impede que uma futura reintroducao parcial passe silenciosa.
    """
    caps = AgentCapabilities.of(
        principal=andre.id,
        tools=[*andre.tools, _START_TOOL],
        process_keys=andre.process_keys,
    )
    decision = decide_effect(
        tenant=_TENANT,
        principal=_ANDRE,
        operation=_START_OP,
        process_key=process_key,
        ctx=DecisionContext(capabilities=caps),
    )
    assert decision.allow is False
    assert decision.reason == REASON_PROCESS_KEY_FORBIDDEN
    assert decision.layer == EffectLayer.CAPACIDADE.value
