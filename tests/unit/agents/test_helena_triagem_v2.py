"""NOVA TRIAGEM DA HELENA (DL-0104) — o grafo com a chave `MAEZO_HELENA_TRIAGEM_V2` LIGADA e DESLIGADA.

As tabelas sao as REAIS do repositorio (`spec/processes/dmn/*.dmn`), avaliadas pelo leitor FIRST-hit de
`tests/support/dmn_first_hit.py`: alarme (triage_redflag_*), suficiencia e a tabela nova. O modelo e' um
fake com a classificacao GRAVADA de cada mensagem. Dados sinteticos, sem PII.

CHAVE LIGADA (caminhos a-f do pedido):
  a) pergunta clinica SEM sintoma -> texto que nao avalia + caminho + oferta de pessoa, SEM caso;
  b) pergunta clinica COM sintoma -> ficha (sem escalar na hora), salvo se a tabela de alarme acusar;
  c) necessidade nao clara -> UMA pergunta de esclarecimento; de novo -> oferta de pessoa, sem caso;
  d) ficha de ate' 3 rodadas por tipo de queixa; completa ou esgotada -> caminho (T05), sem caso;
  e) a cada mensagem da ficha o alarme e o risco continuam valendo;
  f) quando abre caso, o `resumo_contexto` leva a ficha.
CHAVE DESLIGADA: o comportamento de hoje (pergunta clinica escala `intencao_clinica`), e a tabela nova
nunca e' consultada.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from maezo.agents.helena.graph import (
    RESPOSTA_DUVIDA_DE_SAUDE,
    RESPOSTA_NAVEGACAO,
    RESPOSTA_PECA_REENVIO,
    HelenaGraph,
    _conduta_clinica,
    new_helena_state,
)
from maezo.agents.helena.prompts import motivo_de_canal_nao_confirmado, motivo_de_recusa
from maezo.agents.helena.triagem_v2 import (
    COLETA_MAX_RODADAS_TRIAGEM_V2,
    PERGUNTAS_DA_FICHA,
    RESPOSTA_TRIAGEM_ESCLARECER,
    RESPOSTA_TRIAGEM_EXAME_RECEITA,
    RESPOSTA_TRIAGEM_OFERTA_PESSOA,
    RESPOSTA_TRIAGEM_OPCAO_PLANO,
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO,
    RESPOSTA_TRIAGEM_SEM_CONDUTA,
    TEXTOS_NOVOS,
    TRIAGEM_V2_DMN_KEY,
    grupo_da_queixa,
    necessidade_da_pergunta_clinica,
    necessidade_vaga,
    relata_sintoma,
    resposta_numerada,
)
from maezo.qa.simulador.checagens import frases_longas
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnVersion
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table

_REPO = Path(__file__).resolve().parents[3]
_CONVERSA = "wa:amh:hk1_triagem_v2"
_TABELAS = (
    "triage_redflag_adult",
    "triage_redflag_pediatric",
    "triage_redflag_gestante",
    "triage_redflag_mental_health",
    "triage_sufficiency",
    TRIAGEM_V2_DMN_KEY,
)


class _DmnReal:
    """As tabelas `.dmn` REAIS (primeiro acerto), no formato que o grafo espera. `fora_do_ar` derruba
    as chaves listadas (o motor que nao responde)."""

    def __init__(self, *, fora_do_ar: frozenset[str] = frozenset()) -> None:
        self._tabelas = {chave: read_live_table(DMN_DIR / f"{chave}.dmn") for chave in _TABELAS}
        self._fora = fora_do_ar
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        self.chamadas.append((decision_key, dict(variables)))
        if decision_key in self._fora or decision_key not in self._tabelas:
            raise DmnEvaluationError(f"tabela indisponivel: {decision_key}")
        tabela = self._tabelas[decision_key]
        veredito = evaluate(tabela, {nome: variables.get(nome) for nome in tabela.input_names})
        versao = DmnVersion(
            key=decision_key, id=f"{decision_key}:1:offline", version=1, deployment_id="offline"
        )
        return [veredito.saidas], versao

    def chaves(self) -> list[str]:
        return [chave for chave, _ in self.chamadas]


class _Gravador(FakeCibSevenTransport):
    def __init__(self) -> None:
        super().__init__()
        self.inicios: list[dict[str, Any]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.inicios.append(dict(variables))
        return await super().start_process_instance(process_key, business_key, variables)


class _Modelo:
    """O classificador devolve a extracao GRAVADA; as demais chamadas (resumo) recebem `""`."""

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
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    base.update(campos)
    return json.dumps(base)


def _grafo(
    respostas: list[str], *, v2: bool = True, dmn: _DmnReal | None = None
) -> tuple[Any, _Gravador, _DmnReal, _Modelo]:
    cib = _Gravador()
    tabelas = dmn or _DmnReal()
    modelo = _Modelo(respostas)
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=tabelas,  # type: ignore[arg-type]
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
        historico_enabled=True,
        coleta_enabled=True,
        triagem_v2_enabled=v2,
        relogio=lambda: datetime(2026, 10, 14, 15, 0, tzinfo=UTC),
    )
    return grafo.compile_graph().compile(), cib, tabelas, modelo


def _entrada(
    mensagem: str,
    *,
    helena_disse: list[str] | None = None,
    ficha: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Entrada do turno. `helena_disse` = falas anteriores da Helena (historico curto); `ficha` = a memoria
    de coleta de um turno anterior (`coleta_rodadas`/`coleta_pendente`/`coleta_contexto`)."""
    estado: dict[str, Any] = dict(
        new_helena_state(
            tenant_id="amh",
            conversation_id=_CONVERSA,
            canal="whatsapp",
            beneficiario_pseudo_id="PSEUDO-TRIAGEM-V2",
            message_body=mensagem,
            message_ref="hk1_msg_triagem_v2",
        )
    )
    agora = datetime.now(UTC).isoformat()
    if helena_disse:
        estado["historico_conversa"] = [{"papel": "helena", "texto": t, "em": agora} for t in helena_disse]
        estado["ultima_mensagem_em"] = agora
    if ficha:
        estado.update(ficha)
    return estado


async def _turno(
    mensagem: str, extracao: str, **kw: Any
) -> tuple[dict[str, Any], _Gravador, _DmnReal, _Modelo]:
    v2 = kw.pop("v2", True)
    dmn = kw.pop("dmn", None)
    grafo, cib, tabelas, modelo = _grafo([extracao], v2=v2, dmn=dmn)
    resultado = await grafo.ainvoke(_entrada(mensagem, **kw))
    return resultado, cib, tabelas, modelo


# =================================================================================================
# a) pergunta clinica SEM sintoma: nao abre caso
# =================================================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mensagem", "texto"),
    [
        ("Posso tomar dipirona?", RESPOSTA_TRIAGEM_SEM_CONDUTA),
        ("Isso que eu tenho é virose?", RESPOSTA_TRIAGEM_SEM_CONDUTA),
        ("O que é dengue?", RESPOSTA_TRIAGEM_SEM_CONDUTA),
        ("Saiu o resultado do meu exame de colesterol, está alterado?", RESPOSTA_TRIAGEM_EXAME_RECEITA),
        ("Preciso renovar minha receita", RESPOSTA_TRIAGEM_EXAME_RECEITA),
    ],
)
async def test_a_pergunta_clinica_sem_sintoma_nao_abre_caso(mensagem: str, texto: str) -> None:
    resultado, cib, tabelas, _ = await _turno(mensagem, _classify(intent="clinical_question"))

    assert cib.inicios == [], "pergunta clinica sem sintoma nao abre caso com a chave ligada"
    assert resultado["next_kind"] == "inform"
    assert resultado["response_text"] == texto
    assert TRIAGEM_V2_DMN_KEY in tabelas.chaves()


@pytest.mark.asyncio
async def test_quero_tirar_uma_duvida_sobre_minha_saude_responde_sem_caso() -> None:
    resultado, cib, _, _ = await _turno(
        "Quero tirar uma dúvida sobre minha saúde", _classify(intent="clinical_question")
    )

    assert cib.inicios == []
    assert resultado["response_text"] == RESPOSTA_DUVIDA_DE_SAUDE


@pytest.mark.asyncio
async def test_preciso_ir_ao_medico_e_navegacao_sem_caso() -> None:
    resultado, cib, _, _ = await _turno("Preciso ir ao médico?", _classify(intent="clinical_question"))

    assert cib.inicios == []
    assert resultado["response_text"] == RESPOSTA_NAVEGACAO


@pytest.mark.asyncio
async def test_quero_falar_com_uma_pessoa_continua_abrindo_caso() -> None:
    """RN 623/2024 art. 9, III: o pedido de pessoa continua no portao de sempre, ANTES da tabela."""
    resultado, cib, _, _ = await _turno("Quero falar com uma pessoa", _classify(intent="human_request"))

    assert resultado["escalation_motivo"] == "solicitacao_humano"
    assert [v["motivo_categoria"] for v in cib.inicios] == ["solicitacao_humano"]


# =================================================================================================
# b) pergunta clinica COM sintoma: ficha, sem escalar na hora (salvo alarme)
# =================================================================================================


@pytest.mark.asyncio
async def test_b_dor_ao_urinar_sem_intensidade_grave_vira_ficha_urinaria_sem_caso() -> None:
    resultado, cib, tabelas, _ = await _turno(
        "O que vc acha que pode ser, uma dor muito forte ao urinar?",
        _classify(intent="clinical_question", population="adult", intensidade="moderada"),
    )

    assert cib.inicios == []
    assert resultado["intent"] == "symptom"
    assert resultado["next_kind"] == "collect"
    assert resultado["response_text"] == PERGUNTAS_DA_FICHA["URINARIO"]
    assert "triage_redflag_adult" in tabelas.chaves(), "a tabela de alarme roda antes da ficha"


@pytest.mark.asyncio
async def test_b_dor_ao_urinar_lida_como_grave_cai_na_red_flag_fail_safe_r10() -> None:
    """RESULTADO DOCUMENTADO (nao mudamos a red flag): se o classificador ler "muito forte" como
    `intensidade=grave`, a regra fail-safe `r10` da tabela adulta ("sintoma nao mapeado relatado como
    grave") acusa P2 e o caso abre como `red_flag_clinico`/`moderada` (enfermagem, a mesma fila P2 que o
    `intencao_clinica` de hoje usava). A chave nova so' muda o ROTULO deste caso, nao a fila."""
    resultado, cib, _, _ = await _turno(
        "O que vc acha que pode ser, uma dor muito forte ao urinar?",
        _classify(intent="clinical_question", population="adult", intensidade="grave"),
    )

    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert resultado["escalation_severidade"] == "moderada"
    assert [v["motivo_categoria"] for v in cib.inicios] == ["red_flag_clinico"]


# =================================================================================================
# c) necessidade nao clara: UMA pergunta de esclarecimento, depois oferta de pessoa (sem caso)
# =================================================================================================


@pytest.mark.asyncio
async def test_c_necessidade_nao_clara_pergunta_uma_vez_com_opcoes() -> None:
    resultado, cib, _, _ = await _turno("Oi, preciso de ajuda", _classify(intent="information"))

    assert cib.inicios == []
    assert resultado["response_text"] == RESPOSTA_TRIAGEM_ESCLARECER


@pytest.mark.asyncio
async def test_c_continua_sem_clareza_oferece_pessoa_sem_abrir_caso() -> None:
    resultado, cib, _, _ = await _turno(
        "preciso de ajuda", _classify(intent="information"), helena_disse=[RESPOSTA_TRIAGEM_ESCLARECER]
    )

    assert cib.inicios == [], "o catch-all NAO abre caso (diverge da R24 do catalogo, D08 pendente)"
    assert resultado["response_text"] == RESPOSTA_TRIAGEM_OFERTA_PESSOA


@pytest.mark.asyncio
async def test_c_opcao_4_depois_do_esclarecimento_e_pedido_de_pessoa() -> None:
    resultado, cib, _, _ = await _turno(
        "4", _classify(intent="information"), helena_disse=[RESPOSTA_TRIAGEM_ESCLARECER]
    )

    assert resultado["escalation_motivo"] == "solicitacao_humano"
    assert [v["motivo_categoria"] for v in cib.inicios] == ["solicitacao_humano"]


@pytest.mark.asyncio
async def test_c_opcao_2_depois_do_esclarecimento_pergunta_o_assunto_do_plano() -> None:
    resultado, cib, _, _ = await _turno(
        "2", _classify(intent="information"), helena_disse=[RESPOSTA_TRIAGEM_ESCLARECER]
    )

    assert cib.inicios == []
    assert resultado["response_text"] == RESPOSTA_TRIAGEM_OPCAO_PLANO


@pytest.mark.asyncio
async def test_c_numero_sem_esclarecimento_antes_nao_e_opcao() -> None:
    _, cib, tabelas, _ = await _turno("4", _classify(intent="information"))

    assert cib.inicios == []
    assert TRIAGEM_V2_DMN_KEY not in tabelas.chaves()


# =================================================================================================
# d) a ficha: por tipo de queixa, ate' 3 rodadas, e o caminho sem caso
# =================================================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mensagem", "extracao", "grupo"),
    [
        ("Estou com dor nas costas", _classify(intent="symptom", population="adult"), "DOR"),
        ("Minha filha está com dor", _classify(intent="symptom", population="pediatric"), "CRIANCA"),
        ("Meu filho está com tosse", _classify(intent="symptom", population="pediatric"), "CRIANCA"),
        ("Estou com tosse e catarro", _classify(intent="symptom", population="adult"), "RESPIRATORIO"),
    ],
)
async def test_d_primeira_rodada_e_a_pergunta_da_ficha_por_tipo_de_queixa(
    mensagem: str, extracao: str, grupo: str
) -> None:
    resultado, cib, _, _ = await _turno(mensagem, extracao)

    assert cib.inicios == []
    assert resultado["next_kind"] == "collect"
    assert resultado["coleta_rodadas"] == 1
    assert resultado["response_text"] == PERGUNTAS_DA_FICHA[grupo]
    assert "você está sentindo" not in resultado["response_text"].lower()


@pytest.mark.asyncio
async def test_d_ficha_respondida_orienta_o_caminho_com_o_prazo_da_ans_sem_caso() -> None:
    resultado, cib, _, _ = await _turno(
        "É na lombar, média, começou ontem",
        _classify(intent="symptom", population="adult", intensidade="moderada"),
        ficha={
            "coleta_rodadas": 1,
            "coleta_pendente": "PERGUNTAR_FICHA_DOR",
            "coleta_contexto": "Estou com dor nas costas",
        },
    )

    assert cib.inicios == []
    assert resultado["response_text"] == RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO
    assert "7 dias úteis" in resultado["response_text"]
    assert resultado["coleta_pendente"] is None


@pytest.mark.asyncio
async def test_d_codigo_sem_intensidade_pergunta_a_intensidade_na_segunda_rodada() -> None:
    """Reaproveita `triage_sufficiency`: sintoma reconhecido sem intensidade -> PERGUNTAR_INTENSIDADE."""
    resultado, cib, _, _ = await _turno(
        "estou com febre desde ontem",
        _classify(intent="symptom", population="adult", sintoma_codigo="febre"),
        ficha={"coleta_rodadas": 1, "coleta_pendente": "PERGUNTAR_FICHA_FEBRE", "coleta_contexto": "febre"},
    )

    assert cib.inicios == []
    assert resultado["next_kind"] == "collect"
    assert resultado["coleta_pergunta"] == "PERGUNTAR_INTENSIDADE"
    assert resultado["coleta_rodadas"] == 2


@pytest.mark.asyncio
async def test_d_terceira_rodada_e_permitida_so_com_a_chave_ligada() -> None:
    resultado, cib, _, _ = await _turno(
        "continua com febre",
        _classify(intent="symptom", population="adult", sintoma_codigo="febre"),
        ficha={"coleta_rodadas": 2, "coleta_pendente": "PERGUNTAR_INTENSIDADE", "coleta_contexto": "febre"},
    )

    assert cib.inicios == []
    assert resultado["next_kind"] == "collect"
    assert resultado["coleta_rodadas"] == COLETA_MAX_RODADAS_TRIAGEM_V2 == 3


@pytest.mark.asyncio
async def test_d_ficha_esgotada_orienta_e_oferece_pessoa_sem_abrir_caso() -> None:
    resultado, cib, _, _ = await _turno(
        "continua igual",
        _classify(intent="symptom", population="adult", sintoma_codigo="febre"),
        ficha={"coleta_rodadas": 3, "coleta_pendente": "PERGUNTAR_INTENSIDADE", "coleta_contexto": "febre"},
    )

    assert cib.inicios == [], "ficha esgotada nao abre caso (diverge da R13 do catalogo)"
    assert resultado["response_text"] == RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO
    assert "pessoa" in resultado["response_text"]


# =================================================================================================
# e) durante a ficha, o alarme e o risco continuam valendo em cada mensagem
# =================================================================================================


@pytest.mark.asyncio
async def test_e_alarme_no_meio_da_ficha_escala_na_hora() -> None:
    resultado, cib, _, _ = await _turno(
        "agora estou com dor no peito muito forte",
        _classify(intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"),
        ficha={"coleta_rodadas": 1, "coleta_pendente": "PERGUNTAR_FICHA_DOR", "coleta_contexto": "dor"},
    )

    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert resultado["escalation_severidade"] == "grave"
    assert "192" in resultado["response_text"]
    assert [v["motivo_categoria"] for v in cib.inicios] == ["red_flag_clinico"]


@pytest.mark.asyncio
async def test_e_risco_psicossocial_no_meio_da_ficha_escala_na_hora() -> None:
    resultado, cib, _, _ = await _turno(
        "não aguento mais, quero morrer",
        _classify(
            intent="symptom",
            population="mental_health",
            psychosocial_risk=True,
            sintoma_codigo="ideacao_suicida",
            risco_imediato=True,
        ),
        ficha={"coleta_rodadas": 1, "coleta_pendente": "PERGUNTAR_FICHA_DOR", "coleta_contexto": "dor"},
    )

    assert resultado["escalation_motivo"] == "risco_psicossocial"
    assert [v["motivo_categoria"] for v in cib.inicios] == ["risco_psicossocial"]


# =================================================================================================
# f) quando abre caso, o resumo leva a ficha
# =================================================================================================


@pytest.mark.asyncio
async def test_f_pedido_de_pessoa_no_meio_da_ficha_leva_a_ficha_no_resumo() -> None:
    resultado, cib, _, modelo = await _turno(
        "quero falar com uma pessoa",
        _classify(intent="human_request", population="adult"),
        ficha={
            "coleta_rodadas": 1,
            "coleta_pendente": "PERGUNTAR_FICHA_DOR",
            "coleta_contexto": "Estou com dor nas costas",
        },
    )

    assert len(cib.inicios) == 1
    resumo = cib.inicios[0]["resumo_contexto"]
    assert "[ficha da triagem:" in resumo
    assert "perguntas_feitas=1" in resumo
    assert any("ficha_coletada" in p and "dor nas costas" in p for p in modelo.prompts)
    assert resultado["escalation_started"] is True


@pytest.mark.asyncio
async def test_f_sem_a_chave_o_resumo_e_o_de_sempre() -> None:
    _, cib, _, modelo = await _turno(
        "quero falar com uma pessoa",
        _classify(intent="human_request", population="adult"),
        v2=False,
        ficha={"coleta_rodadas": 1, "coleta_pendente": "PERGUNTAR_CARACTERIZACAO", "coleta_contexto": "dor"},
    )

    assert "[ficha da triagem:" not in cib.inicios[0]["resumo_contexto"]
    assert not any("ficha_coletada" in p for p in modelo.prompts)


# =================================================================================================
# Fallback seguro: a tabela nova fora do ar nunca vira silencio
# =================================================================================================


@pytest.mark.asyncio
async def test_tabela_nova_fora_do_ar_em_assunto_de_saude_escala_falha_tecnica() -> None:
    resultado, cib, _, _ = await _turno(
        "Posso tomar dipirona?",
        _classify(intent="clinical_question"),
        dmn=_DmnReal(fora_do_ar=frozenset({TRIAGEM_V2_DMN_KEY})),
    )

    assert resultado["escalation_motivo"] == "falha_tecnica"
    assert [v["motivo_categoria"] for v in cib.inicios] == ["falha_tecnica"]
    assert resultado["response_text"]


@pytest.mark.asyncio
async def test_tabela_nova_fora_do_ar_em_mensagem_vaga_pede_para_mandar_de_novo() -> None:
    resultado, cib, _, _ = await _turno(
        "preciso de ajuda",
        _classify(intent="information"),
        dmn=_DmnReal(fora_do_ar=frozenset({TRIAGEM_V2_DMN_KEY})),
    )

    assert cib.inicios == []
    assert resultado["response_text"] == RESPOSTA_PECA_REENVIO


@pytest.mark.asyncio
async def test_suficiencia_fora_do_ar_na_ficha_escala_falha_tecnica() -> None:
    resultado, cib, _, _ = await _turno(
        "continua",
        _classify(intent="symptom", population="adult", sintoma_codigo="febre"),
        ficha={"coleta_rodadas": 1, "coleta_pendente": "PERGUNTAR_FICHA_FEBRE", "coleta_contexto": "febre"},
        dmn=_DmnReal(fora_do_ar=frozenset({"triage_sufficiency"})),
    )

    assert resultado["escalation_motivo"] == "falha_tecnica"
    assert [v["motivo_categoria"] for v in cib.inicios] == ["falha_tecnica"]


# =================================================================================================
# CHAVE DESLIGADA = o comportamento de hoje
# =================================================================================================


@pytest.mark.asyncio
async def test_chave_desligada_pergunta_clinica_escala_intencao_clinica_como_hoje() -> None:
    resultado, cib, tabelas, _ = await _turno(
        "Posso tomar dipirona?", _classify(intent="clinical_question"), v2=False
    )

    assert resultado["escalation_motivo"] == "intencao_clinica"
    assert resultado["escalation_severidade"] == "moderada"
    assert [v["motivo_categoria"] for v in cib.inicios] == ["intencao_clinica"]
    assert TRIAGEM_V2_DMN_KEY not in tabelas.chaves()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mensagem", "extracao"),
    [
        ("Quero tirar uma dúvida sobre minha saúde", _classify(intent="clinical_question")),
        (
            "O que vc acha que pode ser, uma dor muito forte ao urinar?",
            _classify(intent="clinical_question", population="adult", intensidade="grave"),
        ),
        ("Minha filha está com dor", _classify(intent="symptom", population="pediatric")),
        ("Estou com dor nas costas", _classify(intent="symptom", population="adult")),
        ("Meu filho está com tosse", _classify(intent="symptom", population="pediatric")),
        ("Preciso ir ao médico?", _classify(intent="clinical_question")),
        ("Quero falar com uma pessoa", _classify(intent="human_request")),
        ("Oi, preciso de ajuda", _classify(intent="information")),
    ],
)
async def test_chave_desligada_nunca_consulta_a_tabela_nova(mensagem: str, extracao: str) -> None:
    _, _, tabelas, _ = await _turno(mensagem, extracao, v2=False)

    assert TRIAGEM_V2_DMN_KEY not in tabelas.chaves()


@pytest.mark.asyncio
async def test_chave_desligada_a_ficha_continua_com_duas_rodadas() -> None:
    """Sem a chave, a terceira rodada nao existe: com 2 feitas, a coleta escala como hoje."""
    resultado, cib, _, _ = await _turno(
        "continua com febre",
        _classify(intent="symptom", population="adult", sintoma_codigo="febre"),
        v2=False,
        ficha={"coleta_rodadas": 2, "coleta_pendente": "PERGUNTAR_INTENSIDADE", "coleta_contexto": "febre"},
    )

    assert resultado["escalation_motivo"] == "solicitacao_humano"
    assert len(cib.inicios) == 1


def test_a_chave_nasce_desligada_no_grafo_na_fabrica_e_nas_settings() -> None:
    from maezo.agents.helena.graph import build
    from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

    assert inspect.signature(HelenaGraph.__init__).parameters["triagem_v2_enabled"].default is False
    assert 'cfg.get("triagem_v2_enabled", False) is True' in inspect.getsource(build)
    assert WhatsAppWebhookSettings.model_fields["helena_triagem_v2"].default is False
    alias = WhatsAppWebhookSettings.model_fields["helena_triagem_v2"].validation_alias
    assert "MAEZO_HELENA_TRIAGEM_V2" in getattr(alias, "choices", ())
    assert HelenaDispatcher.__dataclass_fields__["triagem_v2_enabled"].default is False
    assert '"triagem_v2_enabled": self.triagem_v2_enabled' in inspect.getsource(
        HelenaDispatcher._compile_turn_graph
    )
    servico = (_REPO / "src/maezo/platform/webhooks/service.py").read_text(encoding="utf-8")
    assert "triagem_v2_enabled=settings.helena_triagem_v2" in servico


def test_a_chave_so_e_ligada_no_dev_pelo_terraform() -> None:
    envs = _REPO / "deploy" / "aws-ecs" / "envs"
    dev = envs / "dev-sa-east-1"
    tfvars = (dev / "amh-interop.auto.tfvars").read_text(encoding="utf-8")
    assert re.search(r"^helena_triagem_v2 = true$", tfvars, re.MULTILINE)
    receptor = (dev / "service-webhook-receiver.tf").read_text(encoding="utf-8")
    assert '{ name = "MAEZO_HELENA_TRIAGEM_V2", value = tostring(var.helena_triagem_v2) }' in receptor
    variaveis = (dev / "variables.tf").read_text(encoding="utf-8")
    bloco = variaveis.split('variable "helena_triagem_v2"', 1)[1].split("\n}\n", 1)[0]
    assert "default     = false" in bloco
    for outro in envs.iterdir():
        if outro.is_dir() and outro.name != "dev-sa-east-1":
            for arquivo in outro.rglob("*.tf*"):
                assert "helena_triagem_v2" not in arquivo.read_text(encoding="utf-8"), arquivo


# =================================================================================================
# Os textos: cercas de saida, regua de CLAREZA, nada de conduta, nada de "[RASCUNHO]"
# =================================================================================================


@pytest.mark.parametrize("texto", TEXTOS_NOVOS)
def test_todo_texto_novo_passa_nas_cercas_de_saida(texto: str) -> None:
    assert motivo_de_canal_nao_confirmado(texto) is None
    assert motivo_de_recusa(texto, "inform") is None
    assert motivo_de_recusa(texto, "collect") is None
    assert not _conduta_clinica(texto)
    assert "RASCUNHO" not in texto.upper()
    assert frases_longas(texto) == [], "regua de CLAREZA (DL-0087): frase com mais de 20 palavras"
    assert "helena" not in texto.lower(), "o nome dela acenderia `apresentacao_ja_feita`"


def test_o_prazo_da_ans_e_so_o_conferido() -> None:
    """Aba 6 do catalogo: so' o texto-base da RN 566 foi conferido (7 dias uteis para consulta basica).
    Prazos que dependem da retificacao (urgencia 'imediato', alta complexidade 21 dias) ficam fora."""
    juntos = " ".join(TEXTOS_NOVOS).lower()
    assert "7 dias úteis" in juntos
    for fora in ("imediato", "21 dias", "14 dias", "3 dias"):
        assert fora not in juntos, fora


# =================================================================================================
# Os lexicos (pequenos, deterministicos)
# =================================================================================================


@pytest.mark.parametrize(
    ("plano", "esperado"),
    [
        ("posso tomar dipirona", "S06"),
        ("isso e virose", "S06"),
        ("saiu o resultado do meu exame", "S08"),
        ("preciso de um atestado", "S09"),
        ("meu medico precisa fazer um laudo", "S09"),
    ],
)
def test_necessidade_da_pergunta_clinica(plano: str, esperado: str) -> None:
    assert necessidade_da_pergunta_clinica(plano, navegacao=False) == esperado


def test_navegacao_vira_s10() -> None:
    assert necessidade_da_pergunta_clinica("preciso ir ao medico", navegacao=True) == "S10"


@pytest.mark.parametrize(
    ("plano", "esperado"),
    [
        ("o que vc acha que pode ser uma dor muito forte ao urinar", True),
        ("posso tomar dipirona", False),
        ("o resultado do exame de urina saiu", False),
        ("estou com coceira", True),
    ],
)
def test_relato_de_sintoma(plano: str, esperado: bool) -> None:
    assert relata_sintoma(plano) is esperado


@pytest.mark.parametrize(
    ("plano", "esperado"),
    [
        ("preciso de ajuda", True),
        ("oi tenho um problema", True),
        ("oi", False),
        ("bom dia", False),
        ("preciso de ajuda com dor de cabeca", False),
        ("quero cancelar meu plano", False),
    ],
)
def test_necessidade_vaga(plano: str, esperado: bool) -> None:
    assert necessidade_vaga(plano) is esperado


@pytest.mark.parametrize(
    ("plano", "esperado"), [("4", "4"), ("opcao 2", "2"), ("a 1", "1"), ("5", None), ("quatro", None)]
)
def test_resposta_numerada(plano: str, esperado: str | None) -> None:
    assert resposta_numerada(plano) == esperado


@pytest.mark.parametrize(
    ("populacao", "codigo", "plano", "esperado"),
    [
        ("adult", None, "dor muito forte ao urinar", "URINARIO"),
        ("adult", None, "dor nas costas", "DOR"),
        ("pediatric", None, "tosse", "CRIANCA"),
        ("gestante", None, "dor", "GESTANTE"),
        ("adult", "febre", "estou mal", "FEBRE"),
        ("adult", None, "estou mal", None),
    ],
)
def test_grupo_da_queixa(populacao: str, codigo: str | None, plano: str, esperado: str | None) -> None:
    assert grupo_da_queixa(populacao, codigo, plano) == esperado
