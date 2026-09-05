"""CERCA (nao teste de bug): a rota `auto_approve` de Rafael e' INALCANCAVEL POR DESENHO a
partir de todo seam tipado, e tem de continuar assim (RAF-01).

Isto NAO documenta um defeito. Documenta uma propriedade que o contrato SP-OP-AUTH-001 v0.2.0
exige e que hoje o codigo satisfaz por acidente feliz de duas cercas independentes — e que
portanto ninguem consegue quebrar sem primeiro apagar um destes testes.

A PROPRIEDADE, em duas metades:

  (1) SEAM TIPADO. A regra r1 de `auth_auto_approval` v0.2.0 so' devolve `AUTO_APROVAR` quando
      os CINCO booleanos (`auto_criteria_verificado` + os quatro `criterio_*_ok`) chegam `true`.
      Nenhum dos cinco e' campo de `RafaelState`/`RAFAEL_INPUT_FIELDS`, nem chave de
      `rafael/delegation.py::_BOOLEAN_META_KEYS`. `new_rafael_state` os RECUSA com `ValueError`
      nomeando-os (usado pelo ingresso `POST /v1/autorizacoes` e pelo seam A2A
      `authorization.analyze`) e `gate_inbound_state` os DESCARTA com log. Consequencia:
      `assess` sempre manda os cinco `False` para a tabela, a r1 nunca casa, o catch-all r99
      devolve `ANALISE_HUMANA`, e `route` e' `human_auditor`.

  (2) TOPOLOGIA DO PROCESSO. `ST_PrepararDossie` — o unico ponto do BPMN que convoca Rafael
      (topico `operadora.auth.analyze_request`) — tem UM UNICO incoming, `Flow_GW_AnaliseHumana`,
      saindo de `GW_AutoAprovacao`. Quando o motor chama Rafael, a decisao L2 ja' foi tomada, e
      foi ANALISE_HUMANA. A perna AUTO_APROVAR do gateway vai para a emissao automatica e nunca
      passa por este agente.

POR QUE NAO "CONSERTAR" ADICIONANDO OS CINCO CAMPOS AO INPUT-BOUNDARY: seria reabrir GAP-AUTH-4
— fatos de auto-aprovacao SEMEADOS no payload de start, que e' exatamente o que a v0.2.0 fechou
("Todos os inputs booleanos sao COMPUTADOS por `operadora.auth.validate_auto_criteria`; nenhum
vem do payload de start"). A cerca aqui e' contra esse conserto, nao a favor dele.

L0 (AGENTS.md regra dura 7) INTACTA e nao e' o assunto: nenhuma das duas DMNs tem saida de
negativa; negativa nasce so' em `UT_AnaliseMedicoAuditor`. O que este arquivo cerca e' a
APROVACAO automatica, nao a negativa.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from maezo.agents.rafael.delegation import _BOOLEAN_META_KEYS
from maezo.agents.rafael.graph import (
    RAFAEL_INPUT_FIELDS,
    RafaelGraph,
    RafaelState,
    gate_inbound_state,
    new_rafael_state,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

#: Os CINCO inputs booleanos da r1 de `auth_auto_approval` v0.2.0 (contrato SP-OP-AUTH-001,
#: secao "`auth_auto_approval` (FIRST — DRAFT, v0.2.0)"). Nenhum e' campo de entrada de Rafael.
CRITERIOS_R1: tuple[str, ...] = (
    "auto_criteria_verificado",
    "criterio_tecnico_ok",
    "criterio_financeiro_ok",
    "criterio_regulatorio_ok",
    "criterio_contratual_ok",
)

#: Estado tipado COMPLETO e maximamente favoravel: todo booleano pre-resolvido `True`,
#: eletivo, valor baixo. Se alguma entrada pudesse levar a `auto_approve`, seria esta.
_ESTADO_FAVORAVEL: dict[str, Any] = {
    "tenant_id": "amh",
    "numero_guia_tiss": "GUIA-RAF01-CERCA",
    "beneficiario_pseudo_id": "PSEUDO-TESTE-RAF01",
    "prestador_id": "PREST-TESTE-001",
    "canal": "a2a",
    "codigo_procedimento_tuss": "40304361",
    "categoria_procedimento": "exame_simples",
    "carater_atendimento": "eletivo",
    "valor_estimado_brl": 350.0,
    "cid10": "Z00.0",
    "documentos_refs": [],
    "requer_autorizacao": True,
    "documentacao_completa": True,
    "beneficiario_ativo": True,
    "carencia_cumprida": True,
    "dut_atendida": True,
    "dentro_teto_l2": True,
    "rede_credenciada": True,
}


class _FakeInference:
    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        return "NARRATIVA SINTETICA"


class _R1FaithfulDmn(FakeDmnTransport):
    """`auth_auto_approval` FIEL a r1 v0.2.0, em vez de um `AUTO_APROVAR` incondicional.

    Este e' o ponto do teste. Uma fake que devolve `AUTO_APROVAR` sempre prova apenas que o
    grafo repassa o que a DMN disser — nunca que a DMN PODE dizer isso a partir do que `assess`
    de fato lhe manda. Aqui a fake decide como a tabela real decide: `AUTO_APROVAR` somente se
    os cinco booleanos chegarem `True`; qualquer outra combinacao cai no catch-all r99.
    `carater_atendimento` e' don't-care em r1 (ADR-0012) e por isso nao e' consultado.
    """

    async def evaluate(
        self,
        decision_key: str,
        variables: dict[str, Any],
        *,
        tenant: str | None = None,
    ) -> Any:
        if decision_key == "auth_auto_approval":
            self.calls.append((decision_key, dict(variables)))
            todos = all(variables.get(k) is True for k in CRITERIOS_R1)
            row = (
                {"recomendacao": "AUTO_APROVAR", "motivo": "r1"}
                if todos
                else {"recomendacao": "ANALISE_HUMANA", "motivo": "r99_catch_all"}
            )
            _, version = self._responses[decision_key]
            return [row], version
        return await super().evaluate(decision_key, variables, tenant=tenant)


def _dmn_r1_fiel() -> _R1FaithfulDmn:
    dmn = _R1FaithfulDmn()
    dmn.register("auth_admissibility", [{"resultado": "SEGUE_ANALISE"}], version=2)
    dmn.register("auth_sla", [{"sla_analise": "5d", "sla_alerta": "2d"}], version=2)
    # Registrada so' para existir a `DmnVersion`; as LINHAS sao decididas por `evaluate` acima.
    dmn.register("auth_auto_approval", [{"recomendacao": "ANALISE_HUMANA"}], version=2)
    return dmn


# ---------------------------------------------------------------------------
# Metade (1): os cinco criterios nao sao campos de entrada, em nenhum seam.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("criterio", CRITERIOS_R1)
def test_criterio_r1_nao_e_campo_de_entrada_de_rafael(criterio: str) -> None:
    """Nem em `RafaelState`, nem em `RAFAEL_INPUT_FIELDS`, nem no seam A2A."""
    assert criterio not in RafaelState.__annotations__
    assert criterio not in RAFAEL_INPUT_FIELDS
    assert criterio not in _BOOLEAN_META_KEYS


@pytest.mark.parametrize("criterio", CRITERIOS_R1)
def test_new_rafael_state_recusa_criterio_r1_nomeando_o(criterio: str) -> None:
    """Seam estrito (ingresso `POST /v1/autorizacoes`, delegacao A2A): `ValueError` que NOMEIA a
    chave recusada — quem chamou precisa saber qual chave sobrou, nao so' que sobrou uma."""
    with pytest.raises(ValueError) as exc:
        new_rafael_state({**_ESTADO_FAVORAVEL, criterio: True})
    assert criterio in str(exc.value)
    assert "non-input keys" in str(exc.value)


@pytest.mark.parametrize("criterio", CRITERIOS_R1)
def test_gate_inbound_state_descarta_criterio_r1_com_log(criterio: str) -> None:
    """Seam leniente: a chave e' DESCARTADA e o descarte e' LOGADO nomeando-a (nunca silencioso —
    um drop mudo seria indistinguivel de um produtor que nunca mandou o campo)."""
    with structlog.testing.capture_logs() as logs:
        gated = gate_inbound_state({**_ESTADO_FAVORAVEL, criterio: True})

    assert criterio not in gated
    assert frozenset(gated) <= RAFAEL_INPUT_FIELDS
    eventos = [e for e in logs if e.get("event") == "rafael_inbound_output_fields_dropped"]
    assert eventos, f"descarte de `{criterio}` nao foi logado"
    assert criterio in eventos[0]["dropped"]


def test_gate_e_construtor_concordam_sobre_os_cinco_de_uma_vez() -> None:
    """Os cinco juntos, que e' a forma que um chamador hostil de fato tentaria."""
    plantado = {**_ESTADO_FAVORAVEL, **dict.fromkeys(CRITERIOS_R1, True)}
    with pytest.raises(ValueError) as exc:
        new_rafael_state(plantado)
    for criterio in CRITERIOS_R1:
        assert criterio in str(exc.value)
    assert frozenset(gate_inbound_state(plantado)).isdisjoint(CRITERIOS_R1)


# ---------------------------------------------------------------------------
# Metade (2): rota negativa — estado tipado completo + DMN fiel a r1 => humano.
# ---------------------------------------------------------------------------


async def test_estado_tipado_maximamente_favoravel_ainda_roteia_para_humano() -> None:
    """O teste de rota NEGATIVO. Estado tipado completo, todo fato pre-resolvido `True`, DMN
    `auth_auto_approval` FIEL a r1 v0.2.0 => `route == "human_auditor"`, motivo de analise
    humana. A rota `auto_approve` e' inalcancavel, e nao por falta de dados favoraveis."""
    dmn = _dmn_r1_fiel()
    compiled = (
        RafaelGraph(
            inference=_FakeInference(),
            dmn=dmn,
            cibseven=FakeCibSevenTransport(),
            audit_sink=FakeStartAuditSink(),
        )
        .compile_graph()
        .compile()
    )

    result = await compiled.ainvoke(dict(new_rafael_state(_ESTADO_FAVORAVEL)))

    assert result["route"] == "human_auditor"
    assert result["motivo_auditor"] == "dmn_analise_humana"
    assert result["recomendacao_auto"] == "ANALISE_HUMANA"
    assert result["desfecho"] == "encaminhado_auditor"
    assert result["admissibilidade"] == "SEGUE_ANALISE"
    # L0 hard: a decisao de cobertura continua sendo do medico-auditor.
    assert (result.get("dossier") or {}).get("decisao_cobertura") is None


async def test_assess_manda_os_cinco_criterios_false_para_a_dmn() -> None:
    """A CAUSA da rota humana, medida no ponto exato: o payload que `assess` entrega a
    `auth_auto_approval` traz os cinco booleanos `False`, porque nao ha' de onde vir `True`.

    Esta e' a assercao que quebra se alguem der default `True` a qualquer um deles — o conserto
    que a auditoria propos e que reabriria GAP-AUTH-4.
    """
    dmn = _dmn_r1_fiel()
    compiled = (
        RafaelGraph(
            inference=_FakeInference(),
            dmn=dmn,
            cibseven=FakeCibSevenTransport(),
            audit_sink=FakeStartAuditSink(),
        )
        .compile_graph()
        .compile()
    )

    await compiled.ainvoke(dict(new_rafael_state(_ESTADO_FAVORAVEL)))

    chamadas = [v for k, v in dmn.calls if k == "auth_auto_approval"]
    assert len(chamadas) == 1, "auth_auto_approval deveria ser avaliada exatamente uma vez"
    for criterio in CRITERIOS_R1:
        assert chamadas[0][criterio] is False, f"`{criterio}` chegou a DMN diferente de False"
