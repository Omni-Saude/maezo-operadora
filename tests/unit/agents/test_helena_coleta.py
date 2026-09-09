"""COLETA (passo 4 do fluxo de triagem) — o no' que PERGUNTA em vez de responder sobre dado que nao tem.

O DEFEITO (medido em 09/09/2026 nas quatro tabelas do motor de dev): "estou com dor de cabeca" ->
`sintoma_codigo=None`, `intensidade="desconhecida"` -> red flag `false` -> resposta automatica.
Nenhuma tabela decidiu isso; a tabela respondeu com confianca sobre dados que nao tinha.

O que estes testes travam (e' a parte deterministica — o conteudo da tabela e' do medico):
  1. com `coleta_enabled=False` (default) o grafo e' o de antes: nenhuma consulta a suficiencia;
  2. a red flag e' avaliada ANTES da suficiencia — emergencia nunca espera pergunta;
  3. os tres vereditos: SUFICIENTE segue `inform`; PERGUNTAR_* vira `collect`; ESCALAR escala;
  4. TEM FIM: na rodada `COLETA_MAX_RODADAS` escala sem consultar a tabela;
  5. FAIL-CLOSED: tabela indisponivel ou veredito fora do vocabulario -> `falha_tecnica`;
  6. memoria entre turnos sobrevive ao `receive`, saturada no maximo;
  7. `_route` recusa `collect` sem veredito de pergunta (backstop estrutural);
  8. o desfecho do turno de pergunta e' `pergunta_coleta`, presente no vocabulario de telemetria.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from maezo.agents.helena import graph as helena_graph
from maezo.agents.helena.graph import (
    _HELENA_MEMORIA_DE_CONVERSA,
    _HELENA_NEUTRAL_OUTPUTS,
    COLETA_MAX_RODADAS,
    DESFECHO_PERGUNTA_COLETA,
    MOTIVO_COLETA_ESGOTADA,
    SUFFICIENCY_DMN_KEY,
    HelenaGraph,
    HelenaState,
)
from maezo.agents.helena.prompts import coleta_prompt
from maezo.runtime.turn_telemetry import _DESFECHO_VOCAB
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink


class _FakeInference:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("fake inference esgotado — o teste pediu mais respostas do que registrou")
        return self._responses.pop(0)


class _FakeWhatsApp:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.sent.append((to_hash, text))
        return {"ok": True}


def _classify_json(**overrides: Any) -> str:
    base = {
        "intent": "symptom",
        "population": "adult",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    base.update(overrides)
    return json.dumps(base)


def _state(**overrides: Any) -> HelenaState:
    s: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:deadbeef",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-1",
        "message_body": "estou com dor de cabeca",
    }
    s.update(overrides)  # type: ignore[typeddict-item]
    return s


_SEM_RED_FLAG = [{"red_flag": False, "prioridade": "-", "conduta": "CONTINUE", "motivo": "Sem criterio"}]
_COM_RED_FLAG = [
    {"red_flag": True, "prioridade": "P1", "conduta": "ESCALATE_EMERGENCY", "motivo": "Dor toracica"}
]


def _dmn(
    *, red_flag: list[dict[str, Any]] = _SEM_RED_FLAG, veredito: str | None = "PERGUNTAR_CARACTERIZACAO"
) -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", red_flag)
    if veredito is not None:
        dmn.register(SUFFICIENCY_DMN_KEY, [{"veredito": veredito}])
    return dmn


def _graph(
    inference: _FakeInference, dmn: FakeDmnTransport, *, coleta: bool, whatsapp: _FakeWhatsApp | None = None
) -> HelenaGraph:
    return HelenaGraph(
        inference=inference,
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp or _FakeWhatsApp(),
        coleta_enabled=coleta,
    )


async def _turno(g: HelenaGraph, state: HelenaState) -> dict[str, Any]:
    """receive -> classify, devolvendo o estado acumulado (sem o LangGraph, como os demais testes)."""
    acumulado: dict[str, Any] = dict(state)
    acumulado.update(await g.receive(acumulado))  # type: ignore[arg-type]
    acumulado.update(await g.classify(acumulado))  # type: ignore[arg-type]
    return acumulado


# ---------------------------------------------------------------------------------------------
# 1. desligada por default: NADA muda
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_desligada_por_default_o_grafo_nao_consulta_a_suficiencia() -> None:
    dmn = _dmn(veredito=None)  # se alguem consultar `triage_sufficiency`, o fake levanta
    g = _graph(_FakeInference([_classify_json()]), dmn, coleta=False)
    saida = await _turno(g, _state())
    assert saida["next_kind"] == "inform"
    assert [c[0] for c in dmn.calls] == ["triage_redflag_adult"]


def test_o_default_do_construtor_e_desligado() -> None:
    g = _graph(_FakeInference([]), _dmn(), coleta=False)
    assert g._coleta_enabled is False  # noqa: SLF001
    assert HelenaGraph.__init__.__kwdefaults__["coleta_enabled"] is False


# ---------------------------------------------------------------------------------------------
# 2. a red flag vem ANTES — emergencia nao espera pergunta
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_red_flag_escala_sem_nunca_consultar_a_suficiencia() -> None:
    dmn = _dmn(red_flag=_COM_RED_FLAG, veredito=None)
    g = _graph(_FakeInference([_classify_json(sintoma_codigo="dor_toracica")]), dmn, coleta=True)
    saida = await _turno(g, _state(message_body="dor forte no peito"))
    assert saida["next_kind"] == "escalate"
    assert saida["escalation_motivo"] == "red_flag_clinico"
    assert [c[0] for c in dmn.calls] == ["triage_redflag_adult"], (
        "a suficiencia nao pode ser consultada numa emergencia"
    )


# ---------------------------------------------------------------------------------------------
# 3. os tres vereditos
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_perguntar_vira_collect_e_registra_a_memoria() -> None:
    g = _graph(_FakeInference([_classify_json()]), _dmn(veredito="PERGUNTAR_CARACTERIZACAO"), coleta=True)
    saida = await _turno(g, _state())
    assert saida["next_kind"] == "collect"
    assert saida["coleta_veredito"] == "PERGUNTAR_CARACTERIZACAO"
    assert saida["coleta_pergunta"] == "PERGUNTAR_CARACTERIZACAO"
    assert saida["coleta_rodadas"] == 1
    assert saida["coleta_pendente"] == "PERGUNTAR_CARACTERIZACAO"
    assert "dor de cabeca" in saida["coleta_contexto"]
    assert saida.get("error") is None


@pytest.mark.asyncio
async def test_suficiente_segue_o_caminho_informativo_de_antes() -> None:
    dmn = _dmn(veredito="SUFICIENTE")
    g = _graph(
        _FakeInference([_classify_json(sintoma_codigo="febre", intensidade="leve", idade_anos=40)]),
        dmn,
        coleta=True,
    )
    saida = await _turno(g, _state(message_body="febre leve desde ontem, 40 anos"))
    assert saida["next_kind"] == "inform"
    assert saida["coleta_rodadas"] == 0
    assert [c[0] for c in dmn.calls] == ["triage_redflag_adult", SUFFICIENCY_DMN_KEY]


@pytest.mark.asyncio
async def test_escalar_da_tabela_escala_com_o_motivo_do_documento() -> None:
    g = _graph(_FakeInference([_classify_json()]), _dmn(veredito="ESCALAR"), coleta=True)
    saida = await _turno(g, _state())
    assert saida["next_kind"] == "escalate"
    assert saida["escalation_motivo"] == MOTIVO_COLETA_ESGOTADA
    assert saida["coleta_pendente"] is None


@pytest.mark.asyncio
async def test_a_tabela_recebe_fatos_e_nao_o_texto() -> None:
    dmn = _dmn(veredito="PERGUNTAR_INTENSIDADE")
    g = _graph(_FakeInference([_classify_json(sintoma_codigo="febre")]), dmn, coleta=True)
    await _turno(g, _state(message_body="estou com febre"))
    chave, entrada = dmn.calls[-1]
    assert chave == SUFFICIENCY_DMN_KEY
    assert entrada == {
        "intent": "symptom",
        "population": "adult",
        "sintoma_reconhecido": True,
        "intensidade_informada": False,
        "campo_populacao_disponivel": False,
        "rodadas": 0,
    }
    assert "febre" not in json.dumps(entrada), "texto do beneficiario nunca vai para a tabela"


# ---------------------------------------------------------------------------------------------
# 4. TEM FIM
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_na_rodada_maxima_escala_sem_consultar_a_tabela() -> None:
    dmn = _dmn(veredito=None)  # consultar a suficiencia aqui levantaria
    g = _graph(_FakeInference([_classify_json()]), dmn, coleta=True)
    saida = await _turno(
        g,
        _state(
            coleta_rodadas=COLETA_MAX_RODADAS, coleta_pendente="PERGUNTAR_CARACTERIZACAO", coleta_contexto="x"
        ),
    )  # type: ignore[arg-type]
    assert saida["next_kind"] == "escalate"
    assert saida["escalation_motivo"] == MOTIVO_COLETA_ESGOTADA
    assert saida["coleta_veredito"] == "ESCALAR"
    assert [c[0] for c in dmn.calls] == ["triage_redflag_adult"]


@pytest.mark.asyncio
async def test_a_segunda_pergunta_ainda_e_permitida_e_acumula_o_contexto() -> None:
    g = _graph(_FakeInference([_classify_json()]), _dmn(veredito="PERGUNTAR_INTENSIDADE"), coleta=True)
    saida = await _turno(
        g,
        _state(
            message_body="e' na nuca",
            coleta_rodadas=1,
            coleta_pendente="PERGUNTAR_CARACTERIZACAO",
            coleta_contexto="estou com dor de cabeca",
        ),
    )  # type: ignore[arg-type]
    assert saida["next_kind"] == "collect"
    assert saida["coleta_rodadas"] == 2
    assert saida["coleta_contexto"] == "estou com dor de cabeca | e' na nuca"


# ---------------------------------------------------------------------------------------------
# 5. FAIL-CLOSED
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tabela_de_suficiencia_indisponivel_escala_falha_tecnica() -> None:
    g = _graph(_FakeInference([_classify_json()]), _dmn(veredito=None), coleta=True)
    saida = await _turno(g, _state())
    assert saida["next_kind"] == "escalate"
    assert saida["escalation_motivo"] == "falha_tecnica"
    assert SUFFICIENCY_DMN_KEY in str(saida.get("error"))


@pytest.mark.asyncio
async def test_veredito_fora_do_vocabulario_escala_falha_tecnica() -> None:
    g = _graph(_FakeInference([_classify_json()]), _dmn(veredito="TALVEZ"), coleta=True)
    saida = await _turno(g, _state())
    assert saida["next_kind"] == "escalate"
    assert saida["escalation_motivo"] == "falha_tecnica"


# ---------------------------------------------------------------------------------------------
# 6. memoria entre turnos
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_receive_preserva_so_a_memoria_de_conversa_e_zera_o_resto() -> None:
    g = _graph(_FakeInference([]), _dmn(), coleta=True)
    plantado = _state(
        coleta_rodadas=1,
        coleta_pendente="PERGUNTAR_INTENSIDADE",
        coleta_contexto="antes",  # type: ignore[arg-type]
        next_kind="inform",
        dmn_decision_ref="forjado",
        coleta_veredito="SUFICIENTE",
        coleta_pergunta="x",  # type: ignore[arg-type]
    )
    reset = await g.receive(plantado)
    assert reset["coleta_rodadas"] == 1
    assert reset["coleta_pendente"] == "PERGUNTAR_INTENSIDADE"
    assert reset["coleta_contexto"] == "antes"
    # tudo o que NAO e' memoria volta ao neutro — inclusive os dois campos de coleta do turno
    for chave, neutro in _HELENA_NEUTRAL_OUTPUTS.items():
        if chave not in _HELENA_MEMORIA_DE_CONVERSA:
            assert reset[chave] == neutro, chave


@pytest.mark.asyncio
@pytest.mark.parametrize("plantado", [99, -3, "muitas", None])
async def test_receive_satura_as_rodadas_no_maximo(plantado: Any) -> None:
    g = _graph(_FakeInference([]), _dmn(), coleta=True)
    reset = await g.receive(_state(coleta_rodadas=plantado))  # type: ignore[arg-type]
    assert 0 <= reset["coleta_rodadas"] <= COLETA_MAX_RODADAS


def test_a_memoria_e_subconjunto_das_saidas_e_nunca_das_entradas() -> None:
    assert frozenset(_HELENA_NEUTRAL_OUTPUTS) >= _HELENA_MEMORIA_DE_CONVERSA
    assert _HELENA_MEMORIA_DE_CONVERSA.isdisjoint(helena_graph.HELENA_INPUT_FIELDS)


@pytest.mark.asyncio
async def test_com_pergunta_em_aberto_o_classificador_ve_as_mensagens_anteriores() -> None:
    inf = _FakeInference([_classify_json(sintoma_codigo="cefaleia_subita_intensa", intensidade="grave")])
    g = _graph(inf, _dmn(red_flag=_COM_RED_FLAG, veredito=None), coleta=True)
    await _turno(
        g,
        _state(
            message_body="forte, a pior da vida",
            coleta_rodadas=1,
            coleta_pendente="PERGUNTAR_INTENSIDADE",
            coleta_contexto="estou com dor de cabeca",
        ),
    )  # type: ignore[arg-type]
    assert "estou com dor de cabeca" in inf.prompts[0]
    assert "forte, a pior da vida" in inf.prompts[0]


@pytest.mark.asyncio
async def test_sem_pergunta_em_aberto_o_prompt_do_classificador_e_o_de_antes() -> None:
    inf = _FakeInference([_classify_json()])
    g = _graph(inf, _dmn(veredito="SUFICIENTE"), coleta=True)
    await _turno(g, _state(coleta_contexto="lixo sem pendencia"))  # type: ignore[arg-type]
    assert "mensagens anteriores" not in inf.prompts[0]


# ---------------------------------------------------------------------------------------------
# 7. backstop estrutural em `_route`
# ---------------------------------------------------------------------------------------------


def test_route_recusa_collect_sem_veredito_de_pergunta() -> None:
    assert HelenaGraph._route({"next_kind": "collect"}) == "escalate"  # type: ignore[arg-type]  # noqa: SLF001
    assert (
        HelenaGraph._route({"next_kind": "collect", "coleta_veredito": "SUFICIENTE", "coleta_rodadas": 1})
        == "escalate"
    )  # type: ignore[arg-type]  # noqa: SLF001
    assert (
        HelenaGraph._route(
            {
                "next_kind": "collect",
                "coleta_veredito": "PERGUNTAR_INTENSIDADE",
                "coleta_rodadas": 1,
                "error": "x",
            }
        )
        == "escalate"
    )  # type: ignore[arg-type]  # noqa: SLF001


def test_route_aceita_collect_justificado() -> None:
    assert (
        HelenaGraph._route(
            {"next_kind": "collect", "coleta_veredito": "PERGUNTAR_INTENSIDADE", "coleta_rodadas": 1}
        )
        == "collect"
    )  # type: ignore[arg-type]  # noqa: SLF001


def test_o_grafo_compilado_tem_o_no_collect_ligado_ao_respond() -> None:
    g = _graph(_FakeInference([]), _dmn(), coleta=True).compile_graph().compile()
    nomes = {n for n in g.get_graph().nodes if n not in ("__start__", "__end__")}
    assert "collect" in nomes
    arestas = {(e.source, e.target) for e in g.get_graph().edges}
    assert ("collect", "respond") in arestas


# ---------------------------------------------------------------------------------------------
# 8. o no' collect e o desfecho
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_redige_uma_pergunta_com_o_prompt_proprio_e_respond_envia() -> None:
    inf = _FakeInference(["Para eu encaminhar do jeito certo: esta' leve, moderado ou forte agora?"])
    wa = _FakeWhatsApp()
    g = _graph(inf, _dmn(), coleta=True, whatsapp=wa)
    estado = _state(coleta_pergunta="PERGUNTAR_INTENSIDADE", coleta_rodadas=1, population="adult")  # type: ignore[arg-type]
    saida = await g.collect(estado)
    assert saida["response_kind"] == "collect"
    assert "PERGUNTAR_INTENSIDADE" in inf.prompts[0]
    assert "NAO diagnostique" in inf.prompts[0]
    assert "response_kind" not in inf.prompts[0], "o prompt de coleta e' proprio, nao o response_prompt"
    estado.update(saida)  # type: ignore[typeddict-item]
    fim = await g.respond(estado)
    assert wa.sent and wa.sent[0][1].endswith("agora?")
    assert fim["desfecho"] == DESFECHO_PERGUNTA_COLETA


@pytest.mark.asyncio
async def test_collect_com_modelo_fora_ainda_faz_uma_pergunta() -> None:
    class _Fora(_FakeInference):
        async def generate(self, prompt: str, **_: Any) -> str:
            raise TimeoutError("modelo fora")

    g = _graph(_Fora([]), _dmn(), coleta=True)
    saida = await g.collect(_state(coleta_pergunta="PERGUNTAR_IDADE"))  # type: ignore[arg-type]
    assert saida["response_kind"] == "collect"
    assert saida["response_text"].strip().endswith("?"), "fallback tem de ser uma PERGUNTA"


def test_o_desfecho_de_pergunta_esta_no_vocabulario_de_telemetria() -> None:
    assert DESFECHO_PERGUNTA_COLETA in _DESFECHO_VOCAB["helena"]


def test_o_prompt_de_coleta_proibe_diagnostico_e_manda_perguntar_uma_coisa_so() -> None:
    p = coleta_prompt()
    assert "UMA pergunta" in p
    assert "NAO diagnostique" in p
    for token in (
        "PERGUNTAR_INTENSIDADE",
        "PERGUNTAR_CARACTERIZACAO",
        "PERGUNTAR_IDADE",
        "PERGUNTAR_IDADE_GESTACIONAL",
    ):
        assert token in p
