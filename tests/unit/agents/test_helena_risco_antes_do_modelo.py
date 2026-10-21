"""RISCO PSICOSSOCIAL PELO TEXTO, ANTES DO MODELO (pendencia 15, DL-0151).

Antes: o termo de risco (`saude_gerativa.tem_risco`, lista do DL-0123) so' era lido DEPOIS do
classificador. Com o classificador falhando, "quero me matar" escalava `falha_tecnica` (r6 da
`escalation_routing`: P3, atendimento-humano, PT4H) em vez de `risco_psicossocial` (r2: P1, plantao
clinico). Agora o termo decide o turno ANTES do modelo:

  1. classificador falhando + frase de risco -> `risco_psicossocial` P1, nunca `falha_tecnica`;
  2. classificador ok + frase de risco -> o mesmo resultado, e o classificador nem e' chamado;
  3. frase neutra -> o caminho de sempre (o classificador e' chamado e decide);
  4. caso de cobranca aberto + frase de risco -> abre o caso clinico PROPRIO (`-clin`), como hoje.

Grafo REAL com as tabelas `.dmn` REAIS (avaliador offline), inclusive a `escalation_routing` para ler a
prioridade/grupo que o motor daria ao caso aberto.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from maezo.agents.helena.graph import (
    RESPOSTA_ESCALONAMENTO_PSICOSSOCIAL,
    HelenaGraph,
    new_helena_state,
)
from maezo.runtime.caso_clinico import chave_do_escalonamento
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnVersion
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table

TENANT = "amh"
CONVERSA = "wa:amh:hk1_" + "e" * 64
CHAVE = chave_do_escalonamento(TENANT, CONVERSA)
CHAVE_CLINICA = chave_do_escalonamento(TENANT, CONVERSA, clinico=True)
_TABELAS = (
    "triage_redflag_adult",
    "triage_redflag_pediatric",
    "triage_redflag_gestante",
    "triage_redflag_mental_health",
    "triage_sufficiency",
)
#: Marca do prompt do CLASSIFICADOR (o unico que pede `psychosocial_risk` de volta).
_MARCA_DO_CLASSIFY = '"psychosocial_risk"'
_FALHA = "<<classificador cai>>"

FRASES_DE_RISCO = (
    "quero me matar",
    "nao aguento mais viver, quero morrer",
    "estou pensando em suicidio",
    "me cortei de novo ontem",
)


def _classify(**campos: Any) -> str:
    dados: dict[str, Any] = {
        "intent": "information",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    dados.update(campos)
    return json.dumps(dados)


class _DmnReal:
    def __init__(self) -> None:
        self._tabelas = {chave: read_live_table(DMN_DIR / f"{chave}.dmn") for chave in _TABELAS}

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        if decision_key not in self._tabelas:
            raise DmnEvaluationError(f"tabela indisponivel: {decision_key}")
        tabela = self._tabelas[decision_key]
        veredito = evaluate(tabela, {nome: variables.get(nome) for nome in tabela.input_names})
        return [veredito.saidas], DmnVersion(
            key=decision_key, id=f"{decision_key}:1:offline", version=1, deployment_id="offline"
        )


class _Modelo:
    """Classificador falso: devolve a extracao da fila, ou LEVANTA quando a fila diz `_FALHA`."""

    def __init__(self, classificacao: str) -> None:
        self.classificacao = classificacao
        self.chamadas_ao_classify = 0
        self.model_id = "fake-glm"

    async def generate(self, prompt: str, **_: Any) -> str:
        if _MARCA_DO_CLASSIFY not in prompt:
            return "resumo para o atendente"
        self.chamadas_ao_classify += 1
        if self.classificacao == _FALHA:
            raise RuntimeError("provedor de inferencia indisponivel")
        return self.classificacao


class _Motor(FakeCibSevenTransport):
    def __init__(self) -> None:
        super().__init__()
        self.inicios: list[tuple[str, dict[str, Any]]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.inicios.append((business_key, dict(variables)))
        return await super().start_process_instance(process_key, business_key, variables)


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


async def _turno(
    mensagem: str, classificacao: str, *, motor: _Motor | None = None, historico: Any = None
) -> tuple[dict[str, Any], _Modelo, _Motor, _WhatsApp]:
    modelo = _Modelo(classificacao)
    motor = motor or _Motor()
    whatsapp = _WhatsApp()
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=_DmnReal(),  # type: ignore[arg-type]
        cibseven=motor,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        historico_enabled=historico is not None,
    )
    estado: dict[str, Any] = dict(
        new_helena_state(
            tenant_id=TENANT,
            conversation_id=CONVERSA,
            canal="whatsapp",
            beneficiario_pseudo_id="PSEUDO-RISCO",
            message_body=mensagem,
            message_ref="hk1_msg_risco_1",
        )
    )
    if historico is not None:
        estado["historico_conversa"] = historico
    resultado = await grafo.compile_graph().compile().ainvoke(estado)
    return dict(resultado), modelo, motor, whatsapp


def _roteamento(motivo: str, severidade: str | None) -> dict[str, Any]:
    """O que a `escalation_routing` REAL daria ao caso aberto (prioridade, grupo, prazo)."""
    tabela = read_live_table(DMN_DIR / "escalation_routing.dmn")
    return dict(evaluate(tabela, {"motivo_categoria": motivo, "severidade": severidade}).saidas)


def _prioridade_e_grupo(rota: dict[str, Any]) -> tuple[str, str]:
    return str(rota["prioridade"]), str(rota["grupo_atendimento"])


@pytest.mark.parametrize("mensagem", FRASES_DE_RISCO)
@pytest.mark.parametrize("classificacao", [_FALHA, "isto nao e json"], ids=["levanta", "json_ilegivel"])
async def test_classificador_falhando_com_frase_de_risco_escala_risco_psicossocial_p1(
    mensagem: str, classificacao: str
) -> None:
    resultado, modelo, motor, whatsapp = await _turno(mensagem, classificacao)

    assert resultado["escalation_motivo"] == "risco_psicossocial"
    assert resultado["escalation_motivo"] != "falha_tecnica"
    assert not resultado.get("error"), "nao e' falha tecnica: o turno nao dependeu do modelo"
    assert modelo.chamadas_ao_classify == 0, "o risco pelo texto decide ANTES do modelo"
    [(chave, variaveis)] = motor.inicios
    assert chave in (CHAVE_CLINICA, CHAVE)
    assert variaveis["motivo_categoria"] == "risco_psicossocial"
    assert _prioridade_e_grupo(_roteamento("risco_psicossocial", variaveis["severidade"])) == (
        "P1",
        "plantao-clinico",
    )
    assert whatsapp.enviados == [RESPOSTA_ESCALONAMENTO_PSICOSSOCIAL]
    assert "188" in whatsapp.enviados[0]


@pytest.mark.parametrize("mensagem", FRASES_DE_RISCO)
async def test_classificador_ok_com_frase_de_risco_tem_o_mesmo_resultado(mensagem: str) -> None:
    ok, modelo_ok, motor_ok, zap_ok = await _turno(mensagem, _classify(intent="greeting"))
    falho, _, motor_falho, zap_falho = await _turno(mensagem, _FALHA)

    assert modelo_ok.chamadas_ao_classify == 0
    campos = ("next_kind", "escalation_motivo", "escalation_severidade", "psychosocial_risk", "intent")
    assert {c: ok.get(c) for c in campos} == {c: falho.get(c) for c in campos}
    assert ok["escalation_motivo"] == "risco_psicossocial"
    assert [v for _, v in motor_ok.inicios][0]["motivo_categoria"] == "risco_psicossocial"
    assert [v["severidade"] for _, v in motor_ok.inicios] == [v["severidade"] for _, v in motor_falho.inicios]
    assert zap_ok.enviados == zap_falho.enviados == [RESPOSTA_ESCALONAMENTO_PSICOSSOCIAL]


@pytest.mark.parametrize(
    "mensagem",
    ["qual o telefone da central?", "preciso da segunda via do boleto", "morri de rir com o atendente"],
)
async def test_frase_neutra_segue_pelo_classificador(mensagem: str) -> None:
    resultado, modelo, motor, _ = await _turno(mensagem, _classify(intent="greeting"))

    assert modelo.chamadas_ao_classify >= 1, "sem termo de risco o classificador decide, como antes"
    assert resultado.get("escalation_motivo") is None
    assert resultado.get("psychosocial_risk") is False
    assert motor.inicios == []


async def test_frase_neutra_com_classificador_falhando_segue_o_caminho_de_falha() -> None:
    """Sem termo de risco a falha do classificador continua a de hoje (DL-0093: pede reenvio sem caso)."""
    resultado, modelo, motor, _ = await _turno("qual o telefone da central?", _FALHA)

    assert modelo.chamadas_ao_classify >= 1
    assert resultado.get("escalation_motivo") is None
    assert motor.inicios == []


async def test_classificador_marca_risco_sem_termo_no_texto_continua_escalando() -> None:
    """O gatilho 5 pelo classificador (texto sem termo da lista) nao mudou."""
    resultado, modelo, _, _ = await _turno("nao estou bem, nao vejo saida", _classify(psychosocial_risk=True))

    assert modelo.chamadas_ao_classify == 1
    assert resultado["escalation_motivo"] == "risco_psicossocial"


async def test_com_caso_de_cobranca_aberto_frase_de_risco_abre_o_caso_clinico_proprio() -> None:
    motor = _Motor()
    motor.seed_instance(
        ProcessInstance(
            instance_id="caso-cobranca",
            process_key="SP-OP-ESCALATION-001",
            business_key=CHAVE,
            state="ACTIVE",
            already_existed=True,
        ),
        variables={"motivo_categoria": "cobranca"},
    )

    resultado, modelo, motor, whatsapp = await _turno("quero me matar", _FALHA, motor=motor)

    assert modelo.chamadas_ao_classify == 0
    assert resultado["escalation_business_key"] == CHAVE_CLINICA
    assert resultado["start_desfecho"] == "novo"
    [(chave, variaveis)] = motor.inicios
    assert chave == CHAVE_CLINICA
    assert variaveis["motivo_categoria"] == "risco_psicossocial"
    status = await motor.get_process_status(CHAVE)
    assert status.instance_id == "caso-cobranca", "o caso de cobranca segue como estava"
    assert "188" in whatsapp.enviados[-1]
