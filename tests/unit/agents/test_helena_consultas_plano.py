"""Consulta do plano na Helena (DL de 07/10/2026): fatos do cadastro pelo contrato TINA da AMH.

O que este arquivo prova:

1. DESLIGADA = o de antes: o classify enviado ao modelo nao muda um byte, `consulta_plano` e'
   `invalid_intent` e o grafo nem tem o no' `consultar_plano`.
2. LIGADA, COM identidade resolvida: a fonte e' consultada com a referencia do estado e o texto que
   sai e' o redigido sobre os FATOS — ou a resposta deterministica quando a cerca recusa o rascunho
   (numero fora dos fatos, promessa de cobertura, CPF).
3. LIGADA, SEM identidade (ou com a AMH falhando): o texto FIXO "Nao consegui confirmar seus dados...",
   sem chamar a fonte e sem abrir processo.
4. Saude e pedido de pessoa continuam vencendo: consulta + sintoma vai a triagem.
5. As respostas fixas/deterministicas passam nas cercas de saida, inclusive com o status "Em analise"
   do cadastro (que a cerca de capacidade recusaria se fosse invencao do modelo).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from maezo.agents.helena.consultas_plano import (
    ADENDO_CLASSIFY_CONSULTAS,
    RESPOSTA_CONSULTA_SEM_IDENTIDADE,
    fatos_da_consulta,
    literais_dos_fatos,
    motivo_de_recusa_da_consulta,
    resposta_deterministica,
    texto_para_cerca,
)
from maezo.agents.helena.graph import (
    IDENTIDADE_RECONHECIDA,
    PROMPT_VERSIONS,
    HelenaGraph,
    HelenaState,
    _validate_extraction,
    build,
    new_helena_state,
)
from maezo.agents.helena.prompts import motivo_de_canal_nao_confirmado, motivo_de_recusa
from maezo.ports.tina import (
    CarenciaPlano,
    CarenciasView,
    ElegibilidadeView,
    RequisicaoPlano,
    RequisicoesView,
    VinculoPlano,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
IDENTIDADE = {
    "portable_subject_ref": REF,
    "faixa_etaria": "adulto",
    "plano_ativo": True,
    "vigencia_inicio": "2024-03-01",
    "vigencia_fim": None,
    "carencia_vigente": False,
    "titular_ref": None,
}


class _FakeInference:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses or [])
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0) if self._responses else ""


class _Sender:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.sent.append(text)
        return {"ok": True}


def _vinculo(**sobre: Any) -> VinculoPlano:
    base: dict[str, Any] = {
        "carteirinha_mascarada": "******4567",
        "plano": "PLANO SINTETICO ENFERMARIA",
        "registro_ans_plano": "999999",
        "segmentacao": "Ambulatorial + Hospitalar",
        "acomodacao": "Enfermaria",
        "tipo_contratacao": "Coletivo empresarial",
        "vigencia_inicio": "2024-03-01",
        "cancelamento": None,
        "atendimento_liberado": True,
        "situacao_vinculo": "Ativo",
        "situacao_contrato": "Ativo",
        "titular": True,
        "relacao_dependencia": None,
        "titular_ref": None,
    }
    base.update(sobre)
    return VinculoPlano(**base)


ELEGIBILIDADE = ElegibilidadeView(
    portable_subject_ref=REF,
    ativo=True,
    vinculos=(_vinculo(),),
    fonte_atualizada_em="2026-10-06T10:00:00Z",
    campos_ausentes=frozenset(),
)
CARENCIAS = CarenciasView(
    portable_subject_ref=REF,
    pendentes=1,
    carencias=(
        CarenciaPlano(carencia="PARTO", inicio="2026-01-01", dias=300, validade="2026-10-28", cumprida=False),
        CarenciaPlano(
            carencia="CONSULTAS", inicio="2026-01-01", dias=30, validade="2026-01-31", cumprida=True
        ),
    ),
    fonte_atualizada_em="2026-10-06T10:00:00Z",
)
REQUISICOES = RequisicoesView(
    portable_subject_ref=REF,
    requisicoes=(
        RequisicaoPlano(
            solicitacao=123456,
            solicitada_em="2026-10-01T09:00:00Z",
            status="Em análise",
            senha_mascarada="********42",
            senha_validade=None,
            senha_vigente=None,
            sla_dias=5,
            liberacao_prevista="2026-10-08",
        ),
    ),
    fonte_atualizada_em="2026-10-06T10:00:00Z",
)
_VIEWS = {
    "elegibilidade": ELEGIBILIDADE,
    "carteirinha": ELEGIBILIDADE,
    "carencia": CARENCIAS,
    "autorizacao": REQUISICOES,
}


class _Fonte:
    def __init__(self, view: Any = "padrao") -> None:
        self.chamadas: list[tuple[str, str]] = []
        self._view = view

    async def consultar(self, portable_subject_ref: str, subtipo: str) -> Any:
        self.chamadas.append((portable_subject_ref, subtipo))
        return _VIEWS[subtipo] if self._view == "padrao" else self._view


def _classify(**sobre: Any) -> str:
    base: dict[str, Any] = {
        "intent": "consulta_plano",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
        "consulta_subtipo": "elegibilidade",
    }
    base.update(sobre)
    return json.dumps(base)


def _grafo(inference: Any, fonte: Any | None) -> tuple[Any, _Sender, FakeCibSevenTransport]:
    sender = _Sender()
    cib = FakeCibSevenTransport()
    g = HelenaGraph(
        inference=inference,
        dmn=FakeDmnTransport(),
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=sender,
        consultas_plano=fonte,
    )
    return g.compile_graph().compile(), sender, cib


def _entrada(*, identidade: bool = True, **kw: Any) -> HelenaState:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_deadbeef",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-1",
        "message_body": "meu plano esta ativo?",
    }
    if identidade:
        base["identidade_beneficiario"] = IDENTIDADE
        base["identidade_desfecho"] = IDENTIDADE_RECONHECIDA
    base.update(kw)
    return new_helena_state(**base)


# --- 1. Desligada = o de antes ------------------------------------------------------------------


def test_desligada_o_validador_recusa_consulta_plano() -> None:
    assert _validate_extraction(json.loads(_classify())) == "invalid_intent"
    assert _validate_extraction(json.loads(_classify()), consultas_habilitadas=True) is None


@pytest.mark.parametrize(
    "dados",
    [
        {"consulta_subtipo": "boleto"},
        {"consulta_subtipo": None},
        {"intent": "information", "consulta_subtipo": "carencia"},
    ],
)
def test_ligada_o_subtipo_tem_dominio_fechado(dados: dict[str, Any]) -> None:
    falha = _validate_extraction(json.loads(_classify(**dados)), consultas_habilitadas=True)
    assert falha == "invalid_consulta_subtipo"


async def test_desligada_o_classify_enviado_e_o_grafo_sao_byte_a_byte_os_de_antes() -> None:
    """Desligada (`consultas_plano=None`, o default e o que `build` monta sem a chave): o texto que vai
    ao modelo e o conjunto de nos sao EXATAMENTE os de um grafo construido sem saber da consulta."""
    resposta = _classify(intent="information", consulta_subtipo=None)
    antes = _FakeInference([resposta, "Posso ajudar com isso."])
    depois = _FakeInference([resposta, "Posso ajudar com isso."])
    deps = {
        "dmn": FakeDmnTransport(),
        "cibseven": FakeCibSevenTransport(),
        "audit_sink": FakeStartAuditSink(),
    }
    g_antes = build({**deps, "inference": antes, "whatsapp": _Sender()})
    g_depois = build({**deps, "inference": depois, "whatsapp": _Sender(), "consultas_plano": None})
    assert set(g_antes.nodes) == set(g_depois.nodes)
    assert "consultar_plano" not in g_depois.nodes
    for g, inf in ((g_antes, antes), (g_depois, depois)):
        await g.compile().ainvoke(_entrada(identidade=False, message_body="qual o horario de visita?"))
        assert ADENDO_CLASSIFY_CONSULTAS not in inf.prompts[0]
    assert antes.prompts == depois.prompts


async def test_desligada_a_mesma_pergunta_continua_fora_do_canal() -> None:
    inferencia = _FakeInference([_classify(intent="outside_channel", consulta_subtipo=None)])
    compilado, sender, _ = _grafo(inferencia, None)
    out = await compilado.ainvoke(_entrada())
    assert out["response_kind"] == "inform"
    assert RESPOSTA_CONSULTA_SEM_IDENTIDADE not in sender.sent


# --- 2. Ligada, com identidade ------------------------------------------------------------------


async def test_ligada_com_identidade_consulta_a_fonte_e_responde_o_rascunho_sobre_os_fatos() -> None:
    rascunho = (
        "Pelo que consta no cadastro, seu plano PLANO SINTETICO ENFERMARIA está ativo, com vigência "
        "desde 01/03/2024. Se quiser falar com uma pessoa da equipe, é só me pedir."
    )
    inferencia = _FakeInference([_classify(), rascunho])
    fonte = _Fonte()
    compilado, sender, cib = _grafo(inferencia, fonte)
    out = await compilado.ainvoke(_entrada())
    assert fonte.chamadas == [(REF, "elegibilidade")]
    assert sender.sent[-1] == rascunho
    assert out["response_kind"] == "inform" and out["desfecho"] == "resolvido_automatico"
    assert not out.get("escalation_started")  # consulta nao abre processo
    # o classify levou o adendo; a redacao levou os fatos DEMARCADOS e nunca a referencia
    assert ADENDO_CLASSIFY_CONSULTAS in inferencia.prompts[0]
    prompt_redacao = inferencia.prompts[1]
    assert "fatos_do_plano" in prompt_redacao and "PLANO SINTETICO ENFERMARIA" in prompt_redacao
    assert REF not in prompt_redacao


@pytest.mark.parametrize(
    "rascunho",
    [
        "Seu plano está ativo desde 01/03/2024 e cobre a sua cirurgia.",  # promessa de cobertura
        "Sua carteirinha é 1234567890123456.",  # numero fora dos fatos
        "Seu CPF 123.456.789-09 está ativo no plano.",  # documento
        "Consulte o aplicativo do plano para mais detalhes.",  # canal nao confirmado
        "",  # modelo nao respondeu
    ],
)
async def test_rascunho_recusado_vira_resposta_deterministica_so_com_os_fatos(rascunho: str) -> None:
    inferencia = _FakeInference([_classify(), rascunho])
    compilado, sender, _ = _grafo(inferencia, _Fonte())
    await compilado.ainvoke(_entrada())
    enviado = sender.sent[-1]
    assert enviado == resposta_deterministica(
        "elegibilidade", fatos_da_consulta("elegibilidade", ELEGIBILIDADE)
    )
    assert "PLANO SINTETICO ENFERMARIA" in enviado and "01/03/2024" in enviado


async def test_autorizacao_com_status_do_cadastro_em_analise_sai_inteiro() -> None:
    """ "em analise" e' status INVENTADO quando o modelo o escreve (cerca de capacidade) e FATO quando
    consta no cadastro: a resposta deterministica com o status sai inteira, sem troca."""
    inferencia = _FakeInference(
        [_classify(consulta_subtipo="autorizacao"), "Consta como aprovada e garantida."]
    )
    fonte = _Fonte()
    compilado, sender, _ = _grafo(inferencia, fonte)
    out = await compilado.ainvoke(_entrada(message_body="minha autorizacao ja saiu?"))
    enviado = sender.sent[-1]
    assert fonte.chamadas == [(REF, "autorizacao")]
    assert "Em análise" in enviado and "********42" in enviado and "123456" in enviado
    assert "aprovada" not in enviado  # o rascunho nem foi pedido
    assert not out.get("error")
    assert out["resposta_com_dados_do_plano"] is True


async def test_carencia_responde_o_que_consta_no_cadastro() -> None:
    inferencia = _FakeInference([_classify(consulta_subtipo="carencia"), ""])
    compilado, sender, _ = _grafo(inferencia, _Fonte())
    await compilado.ainvoke(_entrada(message_body="ja cumpri a carencia de parto?"))
    enviado = sender.sent[-1]
    assert "PARTO" in enviado and "28/10/2026" in enviado and "cumprida" in enviado
    assert "não confirmam cobertura" in enviado


# --- 3. Ligada, sem identidade ou com falha -----------------------------------------------------


async def test_sem_identidade_texto_fixo_sem_chamar_a_fonte_e_sem_processo() -> None:
    inferencia = _FakeInference([_classify()])
    fonte = _Fonte()
    compilado, sender, _ = _grafo(inferencia, fonte)
    out = await compilado.ainvoke(_entrada(identidade=False))
    assert fonte.chamadas == []
    assert sender.sent == [RESPOSTA_CONSULTA_SEM_IDENTIDADE]
    assert out["response_kind"] == "inform" and not out.get("escalation_started")
    assert len(inferencia.prompts) == 1  # so' o classify; nenhuma redacao


@pytest.mark.parametrize("view", [None, CARENCIAS])  # falha da AMH / view de outro tipo
async def test_falha_da_fonte_vira_o_texto_fixo(view: Any) -> None:
    compilado, sender, _ = _grafo(_FakeInference([_classify()]), _Fonte(view))
    await compilado.ainvoke(_entrada())
    assert sender.sent[-1] == RESPOSTA_CONSULTA_SEM_IDENTIDADE


async def test_fonte_que_levanta_vira_o_texto_fixo() -> None:
    class _Quebrada:
        async def consultar(self, portable_subject_ref: str, subtipo: str) -> Any:
            raise ConnectionError("AMH fora")

    compilado, sender, _ = _grafo(_FakeInference([_classify()]), _Quebrada())
    await compilado.ainvoke(_entrada())
    assert sender.sent[-1] == RESPOSTA_CONSULTA_SEM_IDENTIDADE


# --- 4. Saude vence consulta ----------------------------------------------------------------------


async def test_consulta_com_sintoma_vai_para_a_triagem_e_nunca_para_a_fonte() -> None:
    inferencia = _FakeInference([_classify(sintoma_codigo="febre", population="adult")])
    fonte = _Fonte()
    compilado, _, _ = _grafo(inferencia, fonte)
    out = await compilado.ainvoke(_entrada(message_body="meu plano cobre? estou com febre"))
    assert fonte.chamadas == []
    assert out["intent"] == "symptom"


async def test_consulta_com_risco_psicossocial_escala() -> None:
    inferencia = _FakeInference([_classify(psychosocial_risk=True, population="mental_health")])
    fonte = _Fonte()
    compilado, _, _ = _grafo(inferencia, fonte)
    out = await compilado.ainvoke(_entrada())
    assert fonte.chamadas == []
    assert out["escalation_motivo"] == "risco_psicossocial"


# --- 5. Cercas sobre os textos que saem ------------------------------------------------------------


def _passa_nas_cercas(texto: str, literais: tuple[str, ...] = ()) -> None:
    cercado = texto_para_cerca(texto, literais)
    assert motivo_de_recusa(cercado, "inform", start_aconteceu=False) is None, texto
    assert motivo_de_canal_nao_confirmado(cercado) is None, texto


def test_texto_fixo_passa_nas_cercas_de_saida() -> None:
    _passa_nas_cercas(RESPOSTA_CONSULTA_SEM_IDENTIDADE)


@pytest.mark.parametrize("subtipo", ["elegibilidade", "carteirinha", "carencia", "autorizacao"])
def test_resposta_deterministica_passa_nas_cercas_e_na_propria(subtipo: str) -> None:
    fatos = fatos_da_consulta(subtipo, _VIEWS[subtipo])
    assert fatos is not None
    texto = resposta_deterministica(subtipo, fatos)
    _passa_nas_cercas(texto, literais_dos_fatos(fatos))
    assert motivo_de_recusa_da_consulta(texto, fatos) is None, texto


def test_status_em_analise_so_passa_quando_e_literal_do_cadastro() -> None:
    texto = "Pelo que consta no cadastro, a solicitação está Em análise."
    assert motivo_de_recusa(texto, "inform", start_aconteceu=False) is not None
    _passa_nas_cercas(texto, ("Em análise",))


def test_fatos_nunca_levam_nome_nem_valor_nao_mascarado() -> None:
    fatos = fatos_da_consulta("autorizacao", REQUISICOES)
    assert fatos is not None and "DR NOME SINTETICO" not in json.dumps(fatos, ensure_ascii=False)
    eleg = ElegibilidadeView(
        portable_subject_ref=REF,
        ativo=True,
        vinculos=(_vinculo(carteirinha_mascarada="0012345678901234"),),
        fonte_atualizada_em="2026-10-06T10:00:00Z",
        campos_ausentes=frozenset(),
    )
    fatos_cart = fatos_da_consulta("carteirinha", eleg)
    assert fatos_cart is not None and fatos_cart["vinculos"][0]["carteirinha_mascarada"] is None


def test_versoes_da_consulta_estao_no_mapa_de_prompts() -> None:
    assert PROMPT_VERSIONS["classify_consultas"] == "classify-consultas-v1"
    assert PROMPT_VERSIONS["consulta_plano"] == "consulta-plano-v1"


def test_literal_do_cadastro_nunca_e_apagado_dentro_de_outra_palavra() -> None:
    """Regressao medida: o status "Ativo" apagado dentro de "aplicativo" desligava a cerca de canal."""
    texto = "Consulte o aplicativo do plano. Situação: Ativo."
    cercado = texto_para_cerca(texto, ("Ativo",))
    assert "aplicativo" in cercado and "[fato]" in cercado
    assert motivo_de_canal_nao_confirmado(cercado) is not None


@pytest.mark.parametrize("subtipo", ["autorizacao", "carencia"])
async def test_autorizacao_e_carencia_nunca_passam_pelo_modelo(subtipo: str) -> None:
    """Revisao do #690: status e prazo sao o que um rascunho transformaria em promessa — sempre a
    resposta deterministica, e a redacao nem e' chamada."""
    inferencia = _FakeInference(
        [_classify(consulta_subtipo=subtipo), "Sua solicitação 123456 está autorizada."]
    )
    compilado, sender, _ = _grafo(inferencia, _Fonte())
    await compilado.ainvoke(_entrada())
    assert len(inferencia.prompts) == 1  # so' o classify
    assert sender.sent[-1] == resposta_deterministica(subtipo, fatos_da_consulta(subtipo, _VIEWS[subtipo]))


async def test_resposta_sem_fatos_nao_marca_dados_do_plano() -> None:
    compilado, _, _ = _grafo(_FakeInference([_classify()]), _Fonte())
    out = await compilado.ainvoke(_entrada(identidade=False))
    assert out.get("resposta_com_dados_do_plano") is False


# --- revisao do #690: cerca de numeros por CONJUNTO, prazo inventado, literal que e' promessa ----------

_FATOS_SOLICITACAO = {
    "requisicoes": [{"solicitacao": 12345, "status": "Em análise"}],
    "fonte_atualizada_em": None,
}


@pytest.mark.parametrize(
    "rascunho",
    [
        "A solicitação 12345 leva 3 dias.",  # "3" e' substring de 12345, mas nao e' um fato
        "A solicitação 12345 fica pronta até 20/10.",
    ],
)
def test_numero_que_e_so_pedaco_de_outro_fato_e_recusado(rascunho: str) -> None:
    assert motivo_de_recusa_da_consulta(rascunho, _FATOS_SOLICITACAO) == "numero_fora_dos_fatos"


def test_data_dos_fatos_em_formato_brasileiro_passa() -> None:
    fatos = fatos_da_consulta("elegibilidade", ELEGIBILIDADE)
    assert fatos is not None
    assert motivo_de_recusa_da_consulta("Seu plano está ativo desde 01/03/2024.", fatos) is None
    assert motivo_de_recusa_da_consulta("Vigência desde 1/3/2024.", fatos) is None


@pytest.mark.parametrize(
    "rascunho",
    [
        "A solicitação 12345 deve ser liberada logo.",
        "A solicitação 12345 sai em breve.",
        "O prazo de análise é curto.",
        "A solicitação 12345 vai ser liberada.",
        "A solicitação 12345 será liberada.",
    ],
)
def test_prazo_inventado_e_promessa(rascunho: str) -> None:
    assert motivo_de_recusa_da_consulta(rascunho, _FATOS_SOLICITACAO) == "promessa_de_cobertura"


def test_literal_do_cadastro_que_e_promessa_nao_vira_salvo_conduto() -> None:
    """Status "Autorizada" antigo nao pode liberar "sua solicitacao esta' autorizada"."""
    fatos = {"requisicoes": [{"solicitacao": 123, "status": "está autorizada"}], "fonte_atualizada_em": None}
    assert "está autorizada" not in literais_dos_fatos(fatos)
    assert (
        motivo_de_recusa_da_consulta("Sua solicitação 123 está autorizada.", fatos) == "promessa_de_cobertura"
    )
    fatos_canal = {"vinculos": [{"plano": "aplicativo do plano"}], "fonte_atualizada_em": None}
    assert literais_dos_fatos(fatos_canal) == ()


# --- integracao com a onda 0 (historico curto, DL-0080) -------------------------------------------


async def test_com_historico_e_consultas_ligados_a_resposta_com_fatos_vira_marcador_no_historico() -> None:
    """As DUAS flags ligadas: o turno que respondeu com fatos do plano entra no `historico_conversa` so'
    como o marcador — nenhum fato do cadastro fica guardado na memoria da conversa."""
    from maezo.agents.helena.historico import MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO

    inferencia = _FakeInference([_classify(consulta_subtipo="carencia")])
    sender = _Sender()
    g = HelenaGraph(
        inference=inferencia,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=sender,
        consultas_plano=_Fonte(),
        historico_enabled=True,
    )
    out = await g.compile_graph().compile().ainvoke(_entrada(message_body="ja cumpri a carencia?"))
    assert "PARTO" in sender.sent[-1]  # a pessoa recebeu os fatos
    historico = out["historico_conversa"]
    falas = [
        str(item) for item in (historico if isinstance(historico, list) else historico.get("mensagens", []))
    ]
    assert any(MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO in fala for fala in falas), historico
    assert not any("PARTO" in fala for fala in falas)
