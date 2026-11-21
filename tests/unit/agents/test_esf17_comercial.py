"""E-SF-17 / GTM-E-12 (+ SC-03): o agente `comercial`, com teto de autonomia e flag desligada.

Guardas nomeadas no plano: `test_comercial_sem_acao_phi` (`read_phi_data` ->
`COMMERCIAL_PHI_ACTION_FORBIDDEN`) e `test_comercial_sem_hard` (`contract_termination` -> "declares
the hard action"). Mais: Zona Geral, `process_keys: []`, flag off (build recusa), `_hard_frozen.yaml`
byte-identico, `send_prospect_message` L3 nao-hard, 1o turno com "atendimento automatizado" e oferta
de pessoa, saude vence comercial dentro do proprio agente, copiloto (`staff_assist`) nunca envia.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.agents import AgentLoader
from maezo.agents.comercial.graph import (
    ACAO_ENVIO_PROSPECT,
    ROTA_FILA_HUMANA,
    ROTA_NOTA_INTERNA,
    ROTA_SAIDA_CLINICA,
    TEXTO_IDENTIFICACAO_PRIMEIRO_TURNO,
    ComercialDesligadoError,
    ComercialGraph,
    build,
    new_comercial_state,
)
from maezo.agents.comercial.passagem import ComercialInputError
from maezo.gateway.pep import HARD_ACTIONS, Decision, build_pep
from maezo.platform.validation import agent_def
from maezo.platform.validation.result import Report

_REPO = Path(__file__).resolve().parents[3]
_SPEC = _REPO / "spec" / "agents" / "comercial" / "agent.yaml"
_AUTONOMIA = _REPO / "spec" / "policies" / "autonomy"
_TOOLS_ROOT = _REPO / "src" / "maezo" / "tools"
_REF = "hk1_" + "b" * 64
_PASSAGEM = {
    "schema_version": "esf17-passagem-comercial.proposed.v1",
    "para": "comercial",
    "motivo": "intencao_contratar",
    "message_ref": _REF,
}

#: sha256 de `_hard_frozen.yaml` em 211c6ae. E-SF-17 (CD-13) nao pode mudar um byte deste arquivo.
SHA256_HARD_FROZEN = "1c6ce210730d475d3782ff9d1853ef03db5373894e8a5fd14b7730a216098dc6"


def _raw() -> dict[str, Any]:
    data = yaml.safe_load(_SPEC.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _gate(tmp_path: Path, data: dict[str, Any]) -> Report:
    path = tmp_path / "comercial" / "agent.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    report = Report()
    agent_def.validate_dir(tmp_path, _TOOLS_ROOT, report)
    return report


def _mensagens(report: Report) -> str:
    return " | ".join(f.message for f in report.findings)


# --- Definicao e gate de artefatos -------------------------------------------------------------


def test_definicao_real_passa_o_gate_sem_achado(tmp_path: Path) -> None:
    report = _gate(tmp_path, _raw())
    assert report.ok, _mensagens(report)


def test_zona_geral_sem_processo_e_sem_nome_de_persona() -> None:
    definicao = AgentLoader().load(_SPEC)
    assert definicao.security_zone == "general"
    assert definicao.process_keys == []
    assert set(definicao.autonomy_actions) == {
        "informational_response",
        "triage_and_routing",
        "send_prospect_message",
        "staff_assist_draft",  # E-SF-ADD01-03: o rascunho do copiloto (L1), so' no modo staff_assist
    }
    assert definicao.tools == ["mcp-whatsapp.send_message"]
    assert definicao.channels["whatsapp"] == {
        "outbound": True,
        "inbound": True,
        "inbound_condicao": "roteador_comercial",
    }
    # Persona sem nome (GAP-PERSONA-NAME): o papel, nunca uma pessoa.
    assert "GAP-PERSONA-NAME" in definicao.name


@pytest.mark.parametrize(
    "acao",
    [
        "read_phi_data",
        "read_member_plan_facts",
        "read_member_billing_identity",
        "generate_model_inference_phi",
    ],
)
def test_comercial_sem_acao_phi(tmp_path: Path, acao: str) -> None:
    data = _raw()
    data["autonomy_actions"] = [*data["autonomy_actions"], acao]
    msgs = _mensagens(_gate(tmp_path, data))
    assert f"COMMERCIAL_PHI_ACTION_FORBIDDEN: autonomy_actions declares '{acao}'" in msgs


@pytest.mark.parametrize("tool", ["mcp-fhir.read_patient", "mcp-amh.get_subject_context"])
def test_comercial_sem_tool_phi(tmp_path: Path, tool: str) -> None:
    data = _raw()
    data["tools"] = [*data["tools"], tool]
    assert f"COMMERCIAL_PHI_ACTION_FORBIDDEN: tool '{tool}'" in _mensagens(_gate(tmp_path, data))


@pytest.mark.parametrize("hard", sorted(HARD_ACTIONS))
def test_comercial_sem_hard(tmp_path: Path, hard: str) -> None:
    data = _raw()
    data["autonomy_actions"] = [*data["autonomy_actions"], hard]
    msgs = _mensagens(_gate(tmp_path, data))
    assert f"autonomy_actions declares the hard action '{hard}'" in msgs


@pytest.mark.parametrize(
    "acao", ["send_beneficiary_message", "reminders_nudges", "start_compliance_process", "scheduling"]
)
def test_comercial_fora_do_teto_e_recusado(tmp_path: Path, acao: str) -> None:
    data = _raw()
    data["autonomy_actions"] = [*data["autonomy_actions"], acao]
    assert f"COMMERCIAL_ACTION_NOT_ALLOWED: autonomy_actions declares '{acao}'" in _mensagens(
        _gate(tmp_path, data)
    )


def test_comercial_zona_phi_ou_processo_e_recusado(tmp_path: Path) -> None:
    data = _raw()
    data["security_zone"] = "phi"
    data["process_keys"] = ["SP-OP-ESCALATION-001"]
    msgs = _mensagens(_gate(tmp_path, data))
    assert "COMMERCIAL_ZONE_NOT_GENERAL" in msgs
    assert "COMMERCIAL_PROCESS_FORBIDDEN" in msgs


def test_inbound_condicao_e_so_a_do_proprio_agente(tmp_path: Path) -> None:
    data = _raw()
    data["channels"]["whatsapp"]["inbound_condicao"] = "roteador_lucas"
    assert "inbound_condicao is only valid" in _mensagens(_gate(tmp_path, data))


# --- Matriz de autonomia (CD-13) ---------------------------------------------------------------


def test_hard_frozen_byte_identico() -> None:
    conteudo = (_AUTONOMIA / "_hard_frozen.yaml").read_bytes()
    assert hashlib.sha256(conteudo).hexdigest() == SHA256_HARD_FROZEN


def test_send_prospect_message_e_l3_e_nao_hard() -> None:
    l0 = yaml.safe_load((_AUTONOMIA / "L0-core.yaml").read_text(encoding="utf-8"))
    assert l0["actions"]["send_prospect_message"] == {"level": "L3"}
    assert "send_prospect_message" not in HARD_ACTIONS
    pep = build_pep()
    ctx = {"agent_id": "comercial", "level": "L3"}
    for acao in ("informational_response", "triage_and_routing", "send_prospect_message"):
        assert pep.evaluate(acao, agent_context=ctx) is not Decision.DENY, acao
    for hard in HARD_ACTIONS:
        assert pep.evaluate(hard, agent_context=ctx) is Decision.DENY


# --- Flag off e comportamento do grafo ---------------------------------------------------------


@pytest.mark.parametrize("config", [None, {}, {"comercial_ligado": "true"}, {"comercial_ligado": 1}])
def test_flag_off_build_recusa(config: dict[str, Any] | None) -> None:
    with pytest.raises(ComercialDesligadoError):
        build(config)


async def _turno(**entrada: Any) -> dict[str, Any]:
    compilado = build({"comercial_ligado": True}).compile()
    return dict(await compilado.ainvoke(new_comercial_state(entrada)))


async def test_primeiro_turno_identifica_automatizado_e_oferece_pessoa() -> None:
    saida = await _turno(modo="prospect", passagem=_PASSAGEM, primeiro_turno=True)
    assert "atendimento automatizado" in saida["response_text"]
    assert "pessoa da equipe comercial" in saida["response_text"]
    assert saida["response_text"] == TEXTO_IDENTIFICACAO_PRIMEIRO_TURNO
    assert saida["acao_de_envio"] == ACAO_ENVIO_PROSPECT


async def test_saude_vence_comercial_no_agente() -> None:
    saida = await _turno(modo="prospect", passagem=_PASSAGEM, primeiro_turno=True, sinal_saude=True)
    assert saida["rota"] == ROTA_SAIDA_CLINICA
    assert saida["response_text"] == ""
    assert saida["acao_de_envio"] is None


async def test_pedido_de_pessoa_vai_a_fila_humana() -> None:
    saida = await _turno(modo="prospect", passagem=_PASSAGEM, pedido_humano=True)
    assert saida["rota"] == ROTA_FILA_HUMANA


async def test_staff_assist_nunca_envia_ao_prospect() -> None:
    saida = await _turno(modo="staff_assist", primeiro_turno=True)
    assert saida["rota"] == ROTA_NOTA_INTERNA
    assert saida["acao_de_envio"] is None
    assert saida["envio_pelo_humano"] is True
    assert saida["response_text"] == ""


def test_prospect_sem_passagem_ou_com_texto_e_recusado() -> None:
    with pytest.raises(ComercialInputError):
        new_comercial_state({"modo": "prospect"})
    with pytest.raises(ComercialInputError):
        new_comercial_state({"modo": "prospect", "passagem": _PASSAGEM, "message_body": "oi"})


def test_saida_plantada_e_zerada_no_receive() -> None:
    reset = ComercialGraph().receive({"modo": "staff_assist", "rota": "forjada"})
    assert reset["rota"] == ""


def test_textos_sem_prazo_preco_ou_nome() -> None:
    from maezo.agents.comercial import graph

    for texto in (graph.TEXTO_IDENTIFICACAO_PRIMEIRO_TURNO, graph.TEXTO_ENCAMINHAMENTO_ESPECIALISTA):
        baixo = texto.lower()
        for proibido in ("r$", "minuto", "hora", "preço", "desconto", "cobertura garantida", "comissão"):
            assert proibido not in baixo, (proibido, texto)


def test_cd13_nenhuma_acao_existente_mudou() -> None:
    l0 = yaml.safe_load((_AUTONOMIA / "L0-core.yaml").read_text(encoding="utf-8"))["actions"]
    novas = {
        "send_prospect_message",
        "staff_assist_draft",  # PROG-COORD-37 (W2-MZO-CA b)
        "retention_staff_assist_draft",  # PROG-COORD-37 (W2-MZO-CA c)
    }
    antigas = {k: v for k, v in l0.items() if k not in novas}
    assert (
        hashlib.sha256(json.dumps(antigas, sort_keys=True).encode()).hexdigest()
        == "c6dd883f253d89f1a1247ffe9e04b44c3a76619ff39a13df34b4dea872631b64"
    )
