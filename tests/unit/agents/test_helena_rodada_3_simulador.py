"""DL-0090 — rodada 3: a segunda bateria do simulador (40 conversas, 13/10/2026) com os fatos do plano (TINA)
ligados no dev. Cada bloco reproduz um trecho de transcricao com o modelo simulado:

  1. Plano/cobertura/carencia vao a consulta do plano (inclusive com o acesso ligado, desfecho zerado) e
     cobertura de procedimento especifico nunca e' prometida; fonte falhando nao repete o mesmo texto.
  2. "me passa o numero da central pra eu ligar" nao escala; `human_request` sem pedido explicito, tambem nao.
  3. O fallback generico com caso aberto: o modelo escreveu "ja encaminhamos" e a cerca barrou.
  4. "nao e emergencia nao" nao acende alerta.
  5. Navegacao de sintoma nao urgente: as opcoes, sem dizer se precisa de medico.
  6. Encerramento ironico: despedida curta, sem aviso de caso aberto.
  7. D8 (frase > 20 palavras) e D7 (negar acesso) no texto do modelo.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.agents.helena.graph import (
    FRASE_PASSAGEM_COBRANCA,
    PROCESS_KEY,
    RESPONSE_KIND_HANDOFF,
    RESPOSTA_COBERTURA_ESPECIFICA,
    RESPOSTA_DESPEDIDA,
    RESPOSTA_DESPEDIDA_CASO_ABERTO,
    RESPOSTA_ESCALONAMENTO_URGENTE,
    RESPOSTA_HANDOFF_RECUSADA,
    RESPOSTA_INFORM_CASO_ABERTO,
    RESPOSTA_NAVEGACAO,
    RESPOSTA_NAVEGACAO_CASO_ABERTO,
    RESPOSTA_O_QUE_SEI_DE_VOCE,
    RESPOSTA_PLANO_FALHA_1,
    RESPOSTA_PLANO_FALHA_2,
    RESPOSTA_PLANO_FALHA_3,
    RESPOSTA_TELEFONE_DA_CENTRAL,
    HelenaGraph,
    new_helena_state,
)
from maezo.ports.tina import CarenciaPlano, CarenciasView, ElegibilidadeView, VinculoPlano
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_CONVERSA = "wa:amh:hk1_simulador3"
_CHAVE = f"ESC-amh-{_CONVERSA}"
_EMERGENCIA_192 = "Se for uma emergência, ligue agora para o 192 (SAMU) ou vá ao pronto-socorro mais próximo."
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
ELEGIBILIDADE = ElegibilidadeView(
    portable_subject_ref=REF,
    ativo=True,
    vinculos=(
        VinculoPlano(
            carteirinha_mascarada="******4567",
            plano="PLANO SINTETICO ENFERMARIA",
            registro_ans_plano="999999",
            segmentacao="Ambulatorial + Hospitalar",
            acomodacao="Enfermaria",
            tipo_contratacao="Coletivo empresarial",
            vigencia_inicio="2024-03-01",
            cancelamento=None,
            atendimento_liberado=True,
            situacao_vinculo="Ativo",
            situacao_contrato="Ativo",
            titular=True,
            relacao_dependencia=None,
            titular_ref=None,
        ),
    ),
    fonte_atualizada_em="2026-10-06T10:00:00Z",
    campos_ausentes=frozenset(),
)
CARENCIAS = CarenciasView(
    portable_subject_ref=REF,
    pendentes=1,
    carencias=(
        CarenciaPlano(carencia="PARTO", inicio="2026-01-01", dias=300, validade="2026-10-28", cumprida=False),
    ),
    fonte_atualizada_em="2026-10-06T10:00:00Z",
)


class _Fonte:
    def __init__(self, falha: bool = False) -> None:
        self.chamadas: list[str] = []
        self._falha = falha

    async def consultar(self, portable_subject_ref: str, subtipo: str) -> Any:
        self.chamadas.append(subtipo)
        if self._falha:
            return None
        return CARENCIAS if subtipo == "carencia" else ELEGIBILIDADE


class _Modelo:
    def __init__(self, respostas: list[str]) -> None:
        self._respostas = list(respostas)
        self.prompts: list[str] = []
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return self._respostas.pop(0) if self._respostas else ""


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


def _classify(**campos: Any) -> str:
    base: dict[str, Any] = {
        "intent": "information",
        "population": "adult",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
        "cobranca_subtipo": None,
        "competencia": None,
        "consulta_subtipo": None,
    }
    base.update(campos)
    return json.dumps(base)


def _caso_aberto(motivo: str) -> FakeCibSevenTransport:
    cib = FakeCibSevenTransport()
    cib.seed_instance(
        ProcessInstance(
            instance_id="caso-aberto-simulador-3",
            process_key=PROCESS_KEY,
            business_key=_CHAVE,
            state="ACTIVE",
            already_existed=True,
        ),
        variables={"motivo_categoria": motivo},
    )
    return cib


def _grafo(
    respostas: list[str], *, fonte: _Fonte | None = None, cib: FakeCibSevenTransport | None = None
) -> tuple[Any, _WhatsApp, _Modelo, FakeCibSevenTransport]:
    whatsapp = _WhatsApp()
    modelo = _Modelo(respostas)
    cibseven = cib or FakeCibSevenTransport()
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": True, "prioridade": "P1", "conduta": "ESCALATE_EMERGENCY", "motivo": "x"}],
    )
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=dmn,
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        consultas_plano=fonte or _Fonte(),
        relogio=lambda: datetime(2026, 10, 13, 15, 0, tzinfo=UTC),
    )
    return grafo.compile_graph().compile(), whatsapp, modelo, cibseven


def _entrada(mensagem: str, falas_da_helena: list[str] | None = None, **kw: Any) -> dict[str, Any]:
    """Conversa VERIFICADA com o acesso ligado: identidade presente e desfecho zerado (DL-0083)."""
    estado: dict[str, Any] = dict(
        new_helena_state(
            tenant_id="amh",
            conversation_id=_CONVERSA,
            canal="whatsapp",
            beneficiario_pseudo_id="PSEUDO-SIM3",
            message_body=mensagem,
            message_ref="hk1_msg3",
            identidade_beneficiario=IDENTIDADE,
            **kw,
        )
    )
    agora = datetime.now(UTC).isoformat()
    historico: list[dict[str, str]] = []
    for fala in falas_da_helena or []:
        historico.append({"papel": "beneficiario", "texto": "mensagem anterior", "em": agora})
        historico.append({"papel": "helena", "texto": fala, "em": agora})
    if historico:
        estado["historico_conversa"] = historico
        estado["ultima_mensagem_em"] = agora
    return estado


def _starts(cib: FakeCibSevenTransport) -> int:
    return sum(len(v) for v in cib._history.values())


# =================================================================================================
# 1. Plano, cobertura e carencia pela consulta do plano
# =================================================================================================


@pytest.mark.parametrize(
    ("mensagem", "intent_do_modelo", "subtipo"),
    [
        ("qual o tipo do meu plano", "information", "elegibilidade"),
        ("pelo menos me fala se o plano é hospitalar ou ambulatorial?", "information", "elegibilidade"),
        ("cobre parto e fisioterapia?", "clinical_question", "elegibilidade"),
        ("tenho carência?", "outside_channel", "carencia"),
    ],
)
async def test_pergunta_do_plano_usa_os_fatos(mensagem: str, intent_do_modelo: str, subtipo: str) -> None:
    fonte = _Fonte()
    grafo, whatsapp, _, cib = _grafo([_classify(intent=intent_do_modelo)], fonte=fonte)

    resultado = await grafo.ainvoke(_entrada(mensagem))

    assert fonte.chamadas == [subtipo]
    assert resultado["response_kind"] == "inform"
    enviado = whatsapp.enviados[0]
    if subtipo == "carencia":
        assert "PARTO" in enviado
    elif "cobre" in mensagem:
        # DL-0095 (rodada 8): a pergunta de cobertura recebe a segmentacao e onde confirmar (nao o bloco).
        assert "Ambulatorial + Hospitalar" in enviado and RESPOSTA_COBERTURA_ESPECIFICA in enviado
    else:
        assert "Ambulatorial + Hospitalar" in enviado and "Enfermaria" in enviado
    assert "Recebi sua mensagem, mas não consegui preparar" not in enviado
    assert _starts(cib) == 0


async def test_cobertura_de_procedimento_nunca_e_prometida() -> None:
    grafo, whatsapp, _, _ = _grafo([_classify(intent="clinical_question")])

    await grafo.ainvoke(_entrada("o plano cobre parto?"))

    enviado = whatsapp.enviados[0]
    assert RESPOSTA_COBERTURA_ESPECIFICA in enviado
    assert "está coberto" not in enviado and "cobre o parto" not in enviado


async def test_mensagem_mista_com_os_fatos_ligados_entrega_o_plano_junto_da_passagem() -> None:
    grafo, whatsapp, _, _ = _grafo([_classify()])

    resultado = await grafo.ainvoke(_entrada("quando vence o boleto? e o plano cobre parto?"))

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    # DL-0095 (rodada 8): a parte do plano vai JUNTO da frase de passagem (nunca "pergunte de novo").
    enviado = whatsapp.enviados[0]
    assert enviado.startswith(f"{FRASE_PASSAGEM_COBRANCA} No cadastro, seu plano consta com segmentação")
    assert enviado.endswith(RESPOSTA_COBERTURA_ESPECIFICA)
    assert "de novo" not in enviado


@pytest.mark.parametrize(
    ("falas", "esperado"),
    [
        # DL-0095 (rodada 8): com a fonte LIGADA a falha e' da consulta, nunca "por este chat".
        ([], RESPOSTA_PLANO_FALHA_1),
        ([RESPOSTA_PLANO_FALHA_1], RESPOSTA_PLANO_FALHA_2),
        ([RESPOSTA_PLANO_FALHA_1, RESPOSTA_PLANO_FALHA_2], RESPOSTA_PLANO_FALHA_3),
    ],
)
async def test_fonte_falhando_com_gestante_urgente_nao_repete_e_oferece_pessoa(
    falas: list[str], esperado: str
) -> None:
    """O transcrito: o mesmo texto 4x a "cobre parto? to gravida, e urgente"."""
    grafo, whatsapp, _, cib = _grafo([_classify()], fonte=_Fonte(falha=True))

    await grafo.ainvoke(_entrada("cobre parto? to grávida, é urgente", falas))

    enviado = whatsapp.enviados[0]
    # DL-0091 (rodada 4): "e' urgente" sobre cobertura, sem sinal de saude, leva o 192 no FIM.
    assert enviado == f"{esperado} {_EMERGENCIA_192}"
    assert enviado not in falas
    if len(falas) == 1:
        assert "pessoa da equipe" in enviado
    assert "Não consegui confirmar seus dados" not in enviado
    assert _starts(cib) == 0


# =================================================================================================
# 2. Telefone da central e human_request sem pedido explicito
# =================================================================================================


async def test_numero_da_central_nao_escala_e_nao_inventa_numero() -> None:
    grafo, whatsapp, _, cib = _grafo([_classify(intent="human_request")])

    resultado = await grafo.ainvoke(_entrada("me passa o número da central pra eu ligar"))

    assert not resultado.get("escalation_started")
    assert whatsapp.enviados == [RESPOSTA_TELEFONE_DA_CENTRAL]
    assert not re.search(r"\d{4}", whatsapp.enviados[0])
    assert _starts(cib) == 0


async def test_human_request_sem_pedido_explicito_nao_escala() -> None:
    grafo, whatsapp, _, cib = _grafo([_classify(intent="human_request")])

    resultado = await grafo.ainvoke(_entrada("o que você sabe sobre mim?"))

    assert not resultado.get("escalation_started")
    assert whatsapp.enviados == [RESPOSTA_O_QUE_SEI_DE_VOCE]
    assert _starts(cib) == 0


async def test_pedido_explicito_continua_escalando() -> None:
    grafo, _, _, _ = _grafo([_classify(intent="human_request")])

    resultado = await grafo.ainvoke(_entrada("quero falar com uma pessoa"))

    assert resultado["escalation_motivo"] == "solicitacao_humano"


# =================================================================================================
# 3. Fallback generico com caso aberto (log de 13/10: "ja encaminhamos", "vai entrar em contato")
# =================================================================================================


async def test_rascunho_barrado_com_caso_aberto_diz_que_o_caso_continua() -> None:
    # DL-0116: "o caso continua" so' com o caso ATIVO no motor (o historico sozinho e' so' indicio).
    grafo, whatsapp, modelo, _ = _grafo(
        [_classify(), "Já encaminhamos seu caso, e a equipe vai entrar em contato com você."],
        cib=_caso_aberto("cobranca"),
    )

    await grafo.ainvoke(_entrada("e aí, alguma novidade?", [RESPOSTA_HANDOFF_RECUSADA]))

    assert whatsapp.enviados == [RESPOSTA_INFORM_CASO_ABERTO]
    assert "caso_aberto" in modelo.prompts[1], "o modelo sabe que ha' caso aberto"


# =================================================================================================
# 4 e 5. Negacao e navegacao com caso de saude aberto
# =================================================================================================


async def test_nao_e_emergencia_nao_acende_alerta_e_recebe_navegacao() -> None:
    cib = _caso_aberto("red_flag_clinico")
    antes = _starts(cib)
    grafo, whatsapp, _, _ = _grafo([_classify()], cib=cib)

    resultado = await grafo.ainvoke(
        _entrada(
            "Blz, mas não é emergência não. Só queria saber se preciso ir no médico...",
            [RESPOSTA_ESCALONAMENTO_URGENTE],
        )
    )

    assert resultado.get("escalation_motivo") != "red_flag_clinico"
    assert whatsapp.enviados == [RESPOSTA_NAVEGACAO_CASO_ABERTO]
    assert _starts(cib) == antes


async def test_navegacao_nao_vira_texto_de_cobertura_nem_repete() -> None:
    mensagem = "vou na UBS ou espero? se eu for, qual lugar cobre?"
    grafo, whatsapp, _, _ = _grafo([_classify()])

    await grafo.ainvoke(_entrada(mensagem))
    assert whatsapp.enviados == [RESPOSTA_NAVEGACAO]
    texto = whatsapp.enviados[0]
    assert "UBS" in texto and "rede" in texto and "192" in texto

    grafo2, whatsapp2, _, _ = _grafo([_classify()])
    await grafo2.ainvoke(_entrada(mensagem, [RESPOSTA_NAVEGACAO]))
    assert whatsapp2.enviados[0] != RESPOSTA_NAVEGACAO


# =================================================================================================
# 6. Encerramento ironico
# =================================================================================================


@pytest.mark.parametrize("mensagem", ["Valeu pela ajuda... ou falta dela kkk", "vou ligar pra central então"])
async def test_encerramento_recebe_despedida_curta_sem_aviso_de_caso(mensagem: str) -> None:
    cib = _caso_aberto("solicitacao_humano")
    antes = _starts(cib)
    grafo, whatsapp, _, _ = _grafo([_classify(intent="human_request")], cib=cib)

    await grafo.ainvoke(_entrada(mensagem, [RESPOSTA_HANDOFF_RECUSADA]))

    assert whatsapp.enviados == [RESPOSTA_DESPEDIDA_CASO_ABERTO]
    assert _starts(cib) == antes


async def test_encerramento_sem_caso_e_a_despedida_de_sempre() -> None:
    grafo, whatsapp, _, _ = _grafo([_classify(intent="greeting")])

    await grafo.ainvoke(_entrada("Valeu pela ajuda... ou falta dela kkk"))

    assert whatsapp.enviados == [RESPOSTA_DESPEDIDA]


# =================================================================================================
# 7. D8 e D7 no texto do modelo
# =================================================================================================


async def test_frase_longa_do_modelo_e_partida() -> None:
    longa = (
        "A central de atendimento do plano funciona em dias úteis, e você pode falar com ela sempre que "
        "precisar tirar uma dúvida administrativa sobre o seu atendimento."
    )
    grafo, whatsapp, _, _ = _grafo([_classify(), longa])

    await grafo.ainvoke(_entrada("qual o horário da central?"))

    enviado = whatsapp.enviados[0]
    for frase in re.split(r"[.!?]\s*", enviado):
        assert len(frase.split()) <= 20, frase


async def test_modelo_negando_acesso_e_trocado() -> None:
    grafo, whatsapp, _, _ = _grafo([_classify(), "Não tenho acesso aos seus dados de pagamento por aqui."])

    await grafo.ainvoke(_entrada("e as minhas informações?"))

    assert whatsapp.enviados == [RESPOSTA_O_QUE_SEI_DE_VOCE]
