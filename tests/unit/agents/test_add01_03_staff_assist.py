"""E-SF-ADD01-03 (SC-03): o copiloto interno do atendente (`comercial`, modo `staff_assist`).

O agente só PREPARA: devolve a rota de nota interna e, como dado, a ação de autonomia do rascunho
(`staff_assist_draft`, L1, sem parâmetros, sem tool, não `hard`). Quem avalia a ação é o gateway: L1
dá REQUIRE_HUMAN. Nada é enviado pelo agente; o envio é sempre clique do humano.

Estado FECHADO desta entrega (flag desligada, sem LLM, sem texto gerado):
- SC-03.D1 (DPO) sem resposta e provedor/região não escolhidos (E38): nenhum conteúdo gerado;
- `build` recusa sem `comercial_ligado=True`;
- entrada fechada (`INPUT_FIELDS`): não há campo para texto livre nem para dado de saúde.

Deriva registrada: o plano cita a recusa `PHI_IN_COMMERCIAL_INPUT` para entrada clínica. O agente
não pode importar o detector do gateway (ADR-0067), então a recusa aqui é por construção (contrato
de entrada fechado); o código `PHI_IN_COMMERCIAL_INPUT` segue sendo o da projeção do console
(ADD01-02, `portal/api/specialist_console.py`), que este arquivo também fixa.
"""

from __future__ import annotations

import ast
import hashlib
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.agents.comercial import graph
from maezo.agents.comercial.graph import (
    ACAO_ENVIO_PROSPECT,
    ACAO_RASCUNHO_ATENDENTE,
    INPUT_FIELDS,
    NEUTRAL_OUTPUTS,
    ROTA_NOTA_INTERNA,
    ComercialDesligadoError,
    ComercialGraph,
    build,
    new_comercial_state,
)
from maezo.agents.comercial.passagem import ComercialInputError
from maezo.gateway.pep import HARD_ACTIONS, Decision, build_pep
from maezo.platform.validation import agent_def

_REPO = Path(__file__).resolve().parents[3]
_SPEC = _REPO / "spec" / "agents" / "comercial" / "agent.yaml"
_AUTONOMIA = _REPO / "spec" / "policies" / "autonomy"

#: sha256 de `_hard_frozen.yaml` (o mesmo pino de `test_esf17_comercial.py`): nenhum byte muda aqui.
SHA256_HARD_FROZEN = "1c6ce210730d475d3782ff9d1853ef03db5373894e8a5fd14b7730a216098dc6"

#: A pasta de autonomia e as classes de efeito na main `dcfedd29`, que esta linha só LÊ (CD-144/233).
#: Uma unidade do programa que mudar um destes arquivos atualiza o pino no mesmo PR.
_AUTO = "spec/policies/autonomy/"
SHA256_SO_LIDOS = {
    _AUTO + "L0-core.yaml": "515ab01027f2caefb3414b3bc262340aca918b8feafb41b9761edc81607fb280",
    _AUTO + "_hard_frozen.yaml": SHA256_HARD_FROZEN,
    _AUTO + "action-approvals.yaml": "fcaa430c7921700cc088e3bb618237cbbd1c1f0bcafa7e5131e6c33ab31c217e",
    _AUTO + "tenants-amh.yaml": "d78168bddb6362c0556028e0cb29cd69c43743963ccab4c16698f15ded72baa0",
    "src/maezo/gateway/effect_classes.py": "ac307b0c781020fbd65c8c216f7a43047efd1c94b9643efcc12c1a9da92d4f21",
}

#: O teto do agente comercial depois desta linha: os três de E-SF-17 e o rascunho (#1349).
_TETO_COMERCIAL = frozenset(
    {"informational_response", "triage_and_routing", "send_prospect_message", "staff_assist_draft"}
)

#: Nomes que denunciariam cálculo ou decisão sensível na saída do copiloto.
_CAMPOS_VEDADOS = re.compile(
    r"(?i)pre[cç]o|price|valor|comiss|commission|desconto|discount|elegib|eligib|legitim|ranking|"
    r"score|aceite|acceptance|cobertura|coverage"
)


def _chamadas_do_grafo() -> set[str]:
    arvore = ast.parse(Path(graph.__file__).read_text(encoding="utf-8"))
    return {
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
        for n in ast.walk(arvore)
        if isinstance(n, ast.Call)
    }


async def _turno_staff(**extra: Any) -> dict[str, Any]:
    compilado = build({"comercial_ligado": True}).compile()
    return dict(await compilado.ainvoke(new_comercial_state({"modo": "staff_assist", **extra})))


# --- Os três testes nomeados no plano --------------------------------------------------------------


@pytest.mark.parametrize("primeiro_turno", [False, True])
async def test_staff_assist_never_sends_without_human_click(primeiro_turno: bool) -> None:
    saida = await _turno_staff(primeiro_turno=primeiro_turno)
    assert saida["rota"] == ROTA_NOTA_INTERNA
    assert saida["acao_de_envio"] is None
    assert saida["envio_pelo_humano"] is True
    assert saida["response_text"] == ""
    assert saida["acao_de_rascunho"] == ACAO_RASCUNHO_ATENDENTE
    assert ACAO_RASCUNHO_ATENDENTE != ACAO_ENVIO_PROSPECT
    # O grafo não chama tool nenhum: nenhuma chamada a cliente MCP ou a envio no módulo.
    assert not {
        c for c in _chamadas_do_grafo() if re.search(r"(?i)send|enviar|call_tool|invoke_tool|dispatch", c)
    }


async def test_staff_assist_output_has_no_price_or_commission_computation() -> None:
    saida = await _turno_staff()
    # Nenhum campo de saída com nome de preço, comissão, elegibilidade, legitimidade ou ranking.
    assert not [k for k in saida if _CAMPOS_VEDADOS.search(k)], sorted(saida)
    assert not [k for k in NEUTRAL_OUTPUTS if _CAMPOS_VEDADOS.search(k)]
    # Nenhum valor numérico ou monetário na saída (bool é o único não-texto admitido).
    for chave, valor in saida.items():
        assert not isinstance(valor, (int, float, Decimal)) or isinstance(valor, bool), (chave, valor)
        if isinstance(valor, str):
            assert not re.search(r"\d|r\$|%", valor.lower()) or chave in {"tenant_id", "conversation_id"}, (
                chave,
                valor,
            )


@pytest.mark.parametrize(
    "campo",
    ["texto", "mensagem", "texto_do_prospect", "dado_de_saude", "cid", "laudo", "snapshot_do_prospect"],
)
def test_staff_assist_refuses_clinical_input(campo: str) -> None:
    """Entrada fechada: qualquer campo fora de `INPUT_FIELDS` (inclusive um texto clínico) é recusa
    tipada. O copiloto não tem por onde receber dado de saúde nem o snapshot do prospect."""
    with pytest.raises(ComercialInputError, match="comercial_input_contract_mismatch"):
        new_comercial_state({"modo": "staff_assist", campo: "Paciente com diagnóstico de diabetes, CID E11"})
    assert campo not in INPUT_FIELDS
    # O próprio `receive` revalida: um campo clínico plantado no estado não chega ao `decide`.
    plantado: Any = {"modo": "staff_assist", campo: "Paciente com diagnóstico de diabetes, CID E11"}
    reset = ComercialGraph().receive(plantado)
    assert reset == dict(NEUTRAL_OUTPUTS)
    assert campo not in reset


def test_a_projecao_do_console_segue_recusando_dado_clinico_com_o_codigo_do_plano() -> None:
    """A outra ponta da deriva: o código `PHI_IN_COMMERCIAL_INPUT` do plano mora no console
    (ADD01-02), que alimenta o atendente; o copiloto nunca recebe a projeção."""
    from maezo.portal.api import specialist_console

    fonte = Path(specialist_console.__file__).read_text(encoding="utf-8")
    assert "PHI_IN_COMMERCIAL_INPUT" in fonte
    assert "projetar_jornada" in fonte


# --- A ação L1 do rascunho ----------------------------------------------------------------------


def test_a_acao_do_rascunho_e_l1_sem_parametros_e_nao_hard() -> None:
    l0 = yaml.safe_load((_AUTONOMIA / "L0-core.yaml").read_text(encoding="utf-8"))["actions"]
    assert l0[ACAO_RASCUNHO_ATENDENTE] == {"level": "L1"}
    assert ACAO_RASCUNHO_ATENDENTE not in HARD_ACTIONS
    # O gateway avalia: L1 exige humano, nunca permite sozinho.
    pep = build_pep()
    for nivel in ("L1", "L3"):
        decisao = pep.evaluate(
            ACAO_RASCUNHO_ATENDENTE, agent_context={"agent_id": "comercial", "level": nivel}
        )
        assert decisao is Decision.REQUIRE_HUMAN, nivel


def test_o_agente_declara_a_acao_sem_tool_novo_e_sem_llm() -> None:
    spec = yaml.safe_load(_SPEC.read_text(encoding="utf-8"))
    assert ACAO_RASCUNHO_ATENDENTE in spec["autonomy_actions"]
    assert spec["tools"] == ["mcp-whatsapp.send_message"]  # nenhum tool novo para o rascunho
    assert spec["prompt_versions"] == {}  # sem LLM: SC-03.D1 sem resposta, provedor não escolhido
    assert spec["security_zone"] == "general"
    assert spec["process_keys"] == []


async def test_so_o_modo_staff_assist_devolve_a_acao_do_rascunho() -> None:
    compilado = build({"comercial_ligado": True}).compile()
    passagem = {
        "schema_version": "esf17-passagem-comercial.proposed.v1",
        "para": "comercial",
        "motivo": "intencao_contratar",
        "message_ref": "hk1_" + "b" * 64,
    }
    for entrada in (
        {"modo": "prospect", "passagem": passagem, "primeiro_turno": True},
        {"modo": "prospect", "passagem": passagem},
        {"modo": "staff_assist", "sinal_saude": True},
        {"modo": "staff_assist", "pedido_humano": True},
    ):
        saida = dict(await compilado.ainvoke(new_comercial_state(entrada)))
        assert saida["acao_de_rascunho"] is None, entrada
    # Saída plantada é zerada no `receive` antes do `decide`.
    assert (
        ComercialGraph().receive({"modo": "staff_assist", "acao_de_rascunho": "forjada"})["acao_de_rascunho"]
        is None
    )


# --- Estado fechado -----------------------------------------------------------------------------


@pytest.mark.parametrize("config", [None, {}, {"comercial_ligado": "true"}, {"comercial_ligado": 1}])
def test_flag_desligada_o_copiloto_nao_monta(config: dict[str, Any] | None) -> None:
    with pytest.raises(ComercialDesligadoError):
        build(config)


def test_hard_frozen_byte_identico() -> None:
    conteudo = (_AUTONOMIA / "_hard_frozen.yaml").read_bytes()
    assert hashlib.sha256(conteudo).hexdigest() == SHA256_HARD_FROZEN


def test_pasta_de_autonomia_e_classes_de_efeito_so_lidas() -> None:
    """Nenhum arquivo da pasta de autonomia (nem `action-approvals.yaml`) e nem `effect_classes`
    muda nesta linha: o conjunto de arquivos e cada sha256 são os da main."""
    pasta = sorted(p.relative_to(_REPO).as_posix() for p in _AUTONOMIA.iterdir() if p.is_file())
    assert pasta == sorted(k for k in SHA256_SO_LIDOS if k.startswith(_AUTO))
    for rel, esperado in SHA256_SO_LIDOS.items():
        assert hashlib.sha256((_REPO / rel).read_bytes()).hexdigest() == esperado, rel


def test_o_teto_comercial_ganha_so_o_nome_do_rascunho() -> None:
    """Igualdade de conjunto (não ⊇): nenhum outro nome entra no teto; o nome novo vem da pasta."""
    assert frozenset(agent_def._ACOES_COMERCIAIS) == _TETO_COMERCIAL
    l0 = yaml.safe_load((_AUTONOMIA / "L0-core.yaml").read_text(encoding="utf-8"))["actions"]
    novos = _TETO_COMERCIAL - {"informational_response", "triage_and_routing", "send_prospect_message"}
    assert novos == {ACAO_RASCUNHO_ATENDENTE}
    assert all(nome in l0 for nome in _TETO_COMERCIAL)
    assert l0[ACAO_RASCUNHO_ATENDENTE] == {"level": "L1"}
    assert frozenset({"mcp-whatsapp.send_message"}) == agent_def._TOOLS_COMERCIAIS  # nenhum tool novo


def test_nenhuma_rota_do_console_ligada() -> None:
    """O console do atendente (ADD01-02) segue sem rota montada: nem o app o importa, nem o módulo
    declara roteador. O copiloto não ganha superfície HTTP nesta linha."""
    app = _REPO / "src" / "maezo" / "portal" / "api" / "app.py"
    arvore = ast.parse(app.read_text(encoding="utf-8"))
    importados = {
        *(n.module or "" for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom)),
        *(a.name for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom | ast.Import) for a in n.names),
    }
    assert not [m for m in importados if "specialist_console" in m or "comercial" in m]
    from maezo.portal.api import specialist_console

    modulo = ast.parse(Path(specialist_console.__file__).read_text(encoding="utf-8"))
    nomes = {n.id for n in ast.walk(modulo) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(modulo) if isinstance(n, ast.Attribute)
    }
    assert "APIRouter" not in nomes and "FastAPI" not in nomes
