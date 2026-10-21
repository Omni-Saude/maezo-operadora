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
    RESPOSTA_FORA_DO_CANAL,
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
    RESPOSTA_TRIAGEM_BEBE_PEQUENO,
    RESPOSTA_TRIAGEM_ESCLARECER,
    RESPOSTA_TRIAGEM_EXAME_RECEITA,
    RESPOSTA_TRIAGEM_OFERTA_PESSOA,
    RESPOSTA_TRIAGEM_OPCAO_PLANO,
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO,
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA,
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_GESTANTE,
    RESPOSTA_TRIAGEM_SEM_CONDUTA,
    TEXTOS_NOVOS,
    TRIAGEM_V2_DMN_KEY,
    bebe_pequeno,
    codigo_da_ficha,
    grupo_da_queixa,
    itens_ja_respondidos,
    itens_que_faltam,
    necessidade_da_pergunta_clinica,
    necessidade_vaga,
    paciente_da_conversa,
    pergunta_da_ficha,
    relata_sintoma,
    resposta_curta,
    resposta_numerada,
    texto_de_orientacao,
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
    respostas: list[str], *, v2: bool = True, dmn: _DmnReal | None = None, roteador: bool = False
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
        roteador_lucas_enabled=roteador,
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
    roteador = kw.pop("roteador", False)
    rascunho = kw.pop("rascunho", None)
    respostas = [extracao] if rascunho is None else [extracao, rascunho]
    grafo, cib, tabelas, modelo = _grafo(respostas, v2=v2, dmn=dmn, roteador=roteador)
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
    # "muito forte" ja' respondeu "A dor e' forte?": a ficha pergunta so' o que falta.
    assert resultado["response_text"] == "Tem febre? Apareceu sangue no xixi?"
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


# --- As opcoes do T11 depois da oferta (aceite em dev, 15/10/2026) ----------------------------------
#: A conversa do aceite: T11, depois "Ainda nao entendi..." (catch-all) — a oferta continua recente.
_OFERTA_E_CATCH_ALL = [RESPOSTA_TRIAGEM_ESCLARECER, RESPOSTA_TRIAGEM_OFERTA_PESSOA]


@pytest.mark.asyncio
@pytest.mark.parametrize("intent_do_modelo", ["information", "human_request", "greeting"])
async def test_opcao_4_depois_do_ainda_nao_entendi_abre_solicitacao_humano(intent_do_modelo: str) -> None:
    """O defeito do aceite: "4" depois do catch-all saiu `human_request`, virou `information` (sem pedido
    explicito no texto) e a Helena disse "Entendi que voce quer falar com uma pessoa... e' so' me pedir"
    sem abrir caso. A escolha da opcao 4 e' decidida antes da intencao do modelo."""
    resultado, cib, _, _ = await _turno(
        "4", _classify(intent=intent_do_modelo), helena_disse=_OFERTA_E_CATCH_ALL
    )

    assert resultado["escalation_motivo"] == "solicitacao_humano"
    assert resultado["escalation_severidade"] == "leve"
    assert [v["motivo_categoria"] for v in cib.inicios] == ["solicitacao_humano"]
    assert "entendi que você quer" not in str(resultado.get("response_text") or "").lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("mensagem", ["4)", "opção 4", "a 4", "quatro", "Falar com uma pessoa"])
async def test_variacoes_da_opcao_4_abrem_solicitacao_humano(mensagem: str) -> None:
    resultado, cib, _, _ = await _turno(
        mensagem, _classify(intent="information"), helena_disse=_OFERTA_E_CATCH_ALL
    )

    assert resultado["escalation_motivo"] == "solicitacao_humano"
    assert [v["motivo_categoria"] for v in cib.inicios] == ["solicitacao_humano"]


@pytest.mark.asyncio
async def test_opcao_3_passa_ao_lucas_pelo_handoff_de_cobranca() -> None:
    resultado, cib, _, _ = await _turno(
        "3", _classify(intent="information"), helena_disse=_OFERTA_E_CATCH_ALL, roteador=True
    )

    assert cib.inicios == []
    assert resultado["intent"] == "cobranca"
    assert resultado["next_kind"] == "handoff"
    assert resultado.get("handoff"), "o handoff nasce no no' `handoff_cobranca`, depois da precondicao"


@pytest.mark.asyncio
async def test_opcao_3_sem_o_roteador_e_fora_do_canal_sem_caso() -> None:
    resultado, cib, _, _ = await _turno(
        "3", _classify(intent="information"), helena_disse=[RESPOSTA_TRIAGEM_ESCLARECER]
    )

    assert cib.inicios == []
    assert resultado["response_text"] == RESPOSTA_FORA_DO_CANAL


@pytest.mark.asyncio
async def test_opcao_2_depois_do_ainda_nao_entendi_segue_a_consulta_do_plano() -> None:
    resultado, cib, _, _ = await _turno(
        "2", _classify(intent="information"), helena_disse=_OFERTA_E_CATCH_ALL
    )

    assert cib.inicios == []
    assert resultado["next_kind"] == "inform"
    assert resultado["response_text"] == RESPOSTA_TRIAGEM_OPCAO_PLANO


@pytest.mark.asyncio
async def test_opcao_1_convida_a_contar_o_que_sente() -> None:
    resultado, cib, _, _ = await _turno(
        "1", _classify(intent="information"), helena_disse=_OFERTA_E_CATCH_ALL
    )

    assert cib.inicios == []
    assert resultado["next_kind"] == "inform"
    assert resultado["response_text"] == RESPOSTA_DUVIDA_DE_SAUDE


@pytest.mark.asyncio
async def test_opcao_1_com_sintoma_ja_relatado_segue_para_a_ficha() -> None:
    resultado, cib, _, _ = await _turno(
        "1",
        _classify(intent="information", population="adult", sintoma_codigo="febre"),
        helena_disse=[RESPOSTA_TRIAGEM_ESCLARECER],
    )

    assert cib.inicios == []
    assert resultado["intent"] == "symptom"
    assert resultado["next_kind"] == "collect"


@pytest.mark.asyncio
async def test_numero_com_oferta_antiga_segue_o_fluxo_normal() -> None:
    """A oferta so' vale enquanto recente (ultimas falas da Helena): depois, "4" e' mensagem comum."""
    antigas = [RESPOSTA_TRIAGEM_ESCLARECER, "Certo.", "Combinado.", "Tudo bem."]
    _, cib, _, _ = await _turno("4", _classify(intent="information"), helena_disse=antigas)

    assert cib.inicios == []


@pytest.mark.asyncio
async def test_numero_sem_oferta_e_com_a_chave_desligada_nao_vira_opcao() -> None:
    _, cib, _, _ = await _turno(
        "4", _classify(intent="information"), helena_disse=_OFERTA_E_CATCH_ALL, v2=False
    )

    assert cib.inicios == [], "chave desligada: nada muda"


_RASCUNHO_CONTRADITORIO = "Entendi que você quer falar com uma pessoa da equipe. Se quiser, é só me pedir."


@pytest.mark.asyncio
async def test_rascunho_que_entendeu_pedido_de_pessoa_abre_o_caso() -> None:
    """A frase do aceite nao sai mais: se a Helena entendeu pedido de pessoa, o caso abre."""
    resultado, cib, _, _ = await _turno(
        "hum, sei la",
        _classify(intent="information"),
        rascunho=_RASCUNHO_CONTRADITORIO,
    )

    assert [v["motivo_categoria"] for v in cib.inicios] == ["solicitacao_humano"]
    assert resultado["response_text"] != _RASCUNHO_CONTRADITORIO


@pytest.mark.asyncio
async def test_chave_desligada_o_rascunho_segue_como_hoje() -> None:
    _, cib, _, _ = await _turno(
        "hum, sei la",
        _classify(intent="information"),
        rascunho=_RASCUNHO_CONTRADITORIO,
        v2=False,
    )

    assert cib.inicios == [], "chave desligada: nada muda"


# =================================================================================================
# d) a ficha: por tipo de queixa, ate' 3 rodadas, e o caminho sem caso
# =================================================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mensagem", "extracao", "esperado"),
    [
        (
            "Estou com dor nas costas",
            _classify(intent="symptom", population="adult"),
            "A dor está fraca, média ou forte? Começou de repente?",
        ),
        (
            "Minha filha está com dor",
            _classify(intent="symptom", population="pediatric"),
            "Qual é a idade da sua filha? Se for bebê, diga em meses ou dias. "
            "Sua filha está mamando ou comendo e fazendo xixi como de costume? "
            "Ela está muito mole ou sonolenta?",
        ),
        (
            "Meu filho está com tosse",
            _classify(intent="symptom", population="pediatric"),
            "Qual é a idade do seu filho? Se for bebê, diga em meses ou dias. "
            "Seu filho está mamando ou comendo e fazendo xixi como de costume? "
            "Ele está muito mole ou sonolento?",
        ),
        (
            "A criança está com dor",
            _classify(intent="symptom", population="pediatric"),
            PERGUNTAS_DA_FICHA["CRIANCA"],
        ),
        (
            "Estou com tosse e catarro",
            _classify(intent="symptom", population="adult"),
            PERGUNTAS_DA_FICHA["RESPIRATORIO"],
        ),
    ],
)
async def test_d_primeira_rodada_e_a_pergunta_da_ficha_por_tipo_de_queixa(
    mensagem: str, extracao: str, esperado: str
) -> None:
    resultado, cib, _, _ = await _turno(mensagem, extracao)

    assert cib.inicios == []
    assert resultado["next_kind"] == "collect"
    assert resultado["coleta_rodadas"] == 1
    assert resultado["response_text"] == esperado
    assert "você está sentindo" not in resultado["response_text"].lower()


@pytest.mark.asyncio
async def test_d_dor_nas_costas_nao_pergunta_o_local() -> None:
    """Aceite em dev (15/10/2026): "costas" ja' diz onde e' a dor."""
    resultado, cib, _, _ = await _turno(
        "Estou com dor nas costas", _classify(intent="symptom", population="adult")
    )

    assert cib.inicios == []
    assert resultado["next_kind"] == "collect"
    assert not re.search(r"em que parte", resultado["response_text"], re.IGNORECASE)
    assert "forte" in resultado["response_text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("idade_na_extracao", [False, True])
async def test_d_filha_de_6_anos_nao_pergunta_a_idade(idade_na_extracao: bool) -> None:
    """A idade dita na mensagem (pelo lexico, ou pela extracao do classificador) sai da pergunta."""
    extras: dict[str, Any] = {"idade_anos": 6} if idade_na_extracao else {}
    resultado, cib, _, _ = await _turno(
        "minha filha de 6 anos está com dor",
        _classify(intent="symptom", population="pediatric", **extras),
    )

    assert cib.inicios == []
    assert resultado["next_kind"] == "collect"
    assert "idade" not in resultado["response_text"].lower()
    assert resultado["response_text"] == (
        "Sua filha está mamando ou comendo e fazendo xixi como de costume? Ela está muito mole ou sonolenta?"
    )


@pytest.mark.asyncio
async def test_d_ficha_toda_respondida_na_mensagem_vai_direto_ao_caminho() -> None:
    """Local, intensidade e inicio ja' ditos: nada a perguntar -> veredito (T05), sem caso."""
    resultado, cib, _, _ = await _turno(
        "Estou com dor de cabeça forte desde ontem", _classify(intent="symptom", population="adult")
    )

    assert cib.inicios == []
    assert resultado["response_text"] == RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO


# --- dono_v1, segmento 5 com a chave ligada (aceite de 15/10/2026) -----------------------------------
_RECEM_NASCIDA = "Digo isso, pois minha filha, recém nascida está com uma alergia"
_BOLINHAS = "São umas bolinhas vermelhas, tipo uma irritação na pele."


def _passo_do_dono_v1(texto: str) -> Any:
    from maezo.qa.simulador import aceite as ac

    segmento = next(s for s in ac.DONO_V1 if s.nome == "5-caso-administrativo-aberto-e-clinico")
    return next(p for p in segmento.passos if p.texto == texto)


@pytest.mark.asyncio
async def test_recem_nascida_nao_pergunta_a_idade_e_fala_da_sua_filha() -> None:
    """Item 3: "recem nascida" e' idade informada (DL-0101) e a ficha fala do paciente como foi dito.
    E' a expectativa EXATA do passo 3 do segmento 5 do `dono_v1`."""
    from maezo.qa.simulador import aceite as ac

    resultado, cib, _, _ = await _turno(_RECEM_NASCIDA, _classify(intent="symptom", population="pediatric"))

    assert cib.inicios == []
    assert resultado["next_kind"] == "collect"
    texto = resultado["response_text"]
    assert "idade" not in texto.lower()
    assert texto == (
        "Sua filha está mamando ou comendo e fazendo xixi como de costume? Ela está muito mole ou sonolenta?"
    )
    assert ac.avaliar_passo(_passo_do_dono_v1(_RECEM_NASCIDA), [("helena", texto)], [], None) == []


@pytest.mark.asyncio
async def test_ficha_nao_conclui_sem_resposta_e_pergunta_de_novo_so_o_que_faltou() -> None:
    """Item 4: a pessoa respondeu outra coisa ("bolinhas vermelhas"): o que foi perguntado e nao respondido
    volta, curto, sem repetir o resto — e nunca "suficiente" com T05. Passo 4 do segmento 5 do `dono_v1`."""
    from maezo.qa.simulador import aceite as ac

    resultado, cib, _, _ = await _turno(
        _BOLINHAS,
        _classify(intent="symptom", population="pediatric"),
        ficha={
            "coleta_rodadas": 1,
            "coleta_pendente": "PERGUNTAR_FICHA_CRIANCA:2,3@filha",
            "coleta_contexto": _RECEM_NASCIDA,
        },
    )

    assert cib.inicios == []
    assert resultado["next_kind"] == "collect"
    assert resultado["coleta_rodadas"] == 2
    texto = resultado["response_text"]
    assert texto == (
        "Só para eu entender: sua filha está mamando ou comendo e fazendo xixi como de costume? "
        "Ela está muito mole ou sonolenta?"
    )
    assert "7 dias" not in texto
    anterior = ("helena", "Sua filha está mamando ou comendo e fazendo xixi como de costume?")
    assert ac.avaliar_passo(_passo_do_dono_v1(_BOLINHAS), [("helena", texto)], [], anterior[1]) == []


@pytest.mark.asyncio
async def test_resposta_parcial_volta_so_o_item_que_faltou() -> None:
    resultado, _, _, _ = await _turno(
        "está mamando bem e fazendo xixi",
        _classify(intent="symptom", population="pediatric"),
        ficha={
            "coleta_rodadas": 1,
            "coleta_pendente": "PERGUNTAR_FICHA_CRIANCA:2,3@filha",
            "coleta_contexto": _RECEM_NASCIDA,
        },
    )

    assert resultado["response_text"] == "Só para eu entender: ela está muito mole ou sonolenta?"


@pytest.mark.asyncio
async def test_resposta_curta_cobre_o_que_foi_perguntado() -> None:
    """ "nao" depois de "Esta' muito mole ou sonolenta?" responde: a ficha segue para o veredito."""
    resultado, cib, _, _ = await _turno(
        "não",
        _classify(intent="symptom", population="pediatric", idade_meses=24),
        ficha={
            "coleta_rodadas": 2,
            "coleta_pendente": "PERGUNTAR_FICHA_CRIANCA:3@filho#de_novo",
            "coleta_contexto": "meu filho de 2 anos está com dor | está comendo e fazendo xixi",
        },
    )

    assert cib.inicios == []
    assert resultado["response_text"] == RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA


@pytest.mark.asyncio
async def test_bebe_com_menos_de_3_meses_sem_alarme_recebe_a_oferta_da_equipe_e_nao_o_prazo() -> None:
    """Item 5 (DECISAO CLINICA PENDENTE): ficha esgotada num recem-nascido sem alarme -> oferta clara de
    falar agora com a equipe de saude, sem abrir caso, com pronto-atendimento/192."""
    resultado, cib, _, _ = await _turno(
        "não sei dizer",
        _classify(intent="symptom", population="pediatric"),
        ficha={
            "coleta_rodadas": 3,
            "coleta_pendente": "PERGUNTAR_FICHA_CRIANCA:2,3@filha#de_novo",
            "coleta_contexto": _RECEM_NASCIDA,
        },
    )

    assert cib.inicios == [], "a oferta nao abre caso sozinha"
    assert resultado["response_text"] == RESPOSTA_TRIAGEM_BEBE_PEQUENO
    assert "7 dias" not in resultado["response_text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mensagem", "extracao", "contexto", "esperado"),
    [
        (
            "continua igual",
            _classify(intent="symptom", population="pediatric", idade_meses=72),
            "minha filha de 6 anos está com dor",
            RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA,
        ),
        (
            "continua igual",
            _classify(intent="symptom", population="gestante", idade_gestacional_semanas=20),
            "estou gravida de 20 semanas com dor",
            RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_GESTANTE,
        ),
        (
            "continua igual",
            _classify(intent="symptom", population="adult", sintoma_codigo="febre"),
            "febre",
            RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO,
        ),
    ],
)
async def test_o_caminho_no_fim_da_ficha_e_o_da_populacao(
    mensagem: str, extracao: str, contexto: str, esperado: str
) -> None:
    resultado, cib, _, _ = await _turno(
        mensagem,
        extracao,
        ficha={"coleta_rodadas": 3, "coleta_pendente": "PERGUNTAR_INTENSIDADE", "coleta_contexto": contexto},
    )

    assert cib.inicios == []
    assert resultado["response_text"] == esperado


@pytest.mark.asyncio
async def test_chave_desligada_dor_nas_costas_e_a_coleta_de_sempre() -> None:
    resultado, _, tabelas, _ = await _turno(
        "Estou com dor nas costas", _classify(intent="symptom", population="adult"), v2=False
    )

    assert TRIAGEM_V2_DMN_KEY not in tabelas.chaves()
    assert not str(resultado.get("coleta_pergunta") or "").startswith("PERGUNTAR_FICHA_")


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
        "tenho 40 anos, sem manchas, febre desde ontem",
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
        "tenho 40 anos, sem manchas",
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
    # O dev declara a chave explicitamente (ligada ou desligada: desligada em 15/10/2026 a pedido do dono).
    assert re.search(r"^helena_triagem_v2 = (true|false)$", tfvars, re.MULTILINE)
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
    ("plano", "esperado"),
    [
        ("4", "4"),
        ("opcao 2", "2"),
        ("a 1", "1"),
        ("a opcao 4 por favor", "4"),
        ("quatro", "4"),
        ("tres", "3"),
        ("4 falar com uma pessoa", "4"),
        ("falar com uma pessoa", "4"),
        ("boleto", "3"),
        ("o plano", "2"),
        ("um sintoma", "1"),
        ("5", None),
        ("4 dias de febre", None),
        ("quero 4", None),
        ("2 falar com uma pessoa", None),
    ],
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


@pytest.mark.parametrize(
    ("plano", "extracao", "esperado"),
    [
        ("estou com dor nas costas", {}, {"LOCAL"}),
        ("dor de cabeca forte desde ontem", {}, {"LOCAL", "INTENSIDADE", "INICIO"}),
        ("minha filha de 6 anos esta com dor", {}, {"IDADE"}),
        ("meu filho esta com febre", {"idade_meses": 8}, {"IDADE", "FEBRE"}),
        # DURACAO nao e' idade: sem idade, a ficha pergunta (lado seguro).
        ("minha filha esta com dor ha 2 meses", {}, {"INICIO"}),
        ("dor ha mais de 2 meses", {}, {"INICIO"}),
        ("estou com dor", {"intensidade": "moderada"}, {"INTENSIDADE"}),
        ("gestante de 30 semanas com dor", {}, {"SEMANAS"}),
        ("estou com dor", {"sintoma_codigo": "dor_toracica"}, {"LOCAL"}),
    ],
)
def test_itens_ja_respondidos(plano: str, extracao: dict[str, Any], esperado: set[str]) -> None:
    assert itens_ja_respondidos(plano, extracao) == esperado


def test_o_codigo_da_ficha_leva_so_o_que_falta() -> None:
    assert itens_que_faltam("DOR", "", frozenset()) == (1, 2, 3)
    assert itens_que_faltam("DOR", "", frozenset({"LOCAL"})) == (2, 3)
    assert itens_que_faltam("DOR", "", frozenset({"LOCAL", "INTENSIDADE", "INICIO"})) == ()
    # O sinal de alarme so' sai quando a pessoa falou do assunto dele.
    assert itens_que_faltam("CRIANCA", "minha filha", frozenset({"IDADE"})) == (2, 3)
    assert itens_que_faltam("CRIANCA", "esta mamando bem", frozenset({"IDADE"})) == (3,)
    assert codigo_da_ficha("DOR", (1, 2, 3)) == "PERGUNTAR_FICHA_DOR"
    assert codigo_da_ficha("DOR", (2, 3)) == "PERGUNTAR_FICHA_DOR:2,3"
    assert codigo_da_ficha("CRIANCA", (2, 3), paciente="filha") == "PERGUNTAR_FICHA_CRIANCA:2,3@filha"
    assert codigo_da_ficha("CRIANCA", (3,), paciente="filho", de_novo=True) == (
        "PERGUNTAR_FICHA_CRIANCA:3@filho#de_novo"
    )
    assert pergunta_da_ficha("PERGUNTAR_FICHA_DOR") == PERGUNTAS_DA_FICHA["DOR"]
    assert (
        pergunta_da_ficha("PERGUNTAR_FICHA_DOR:2,3")
        == "A dor está fraca, média ou forte? Começou de repente?"
    )
    assert pergunta_da_ficha("PERGUNTAR_FICHA_CRIANCA:3@filho#de_novo") == (
        "Só para eu entender: ele está muito mole ou sonolento?"
    )
    for invalida in (
        "PERGUNTAR_FICHA_DOR:3,2",
        "PERGUNTAR_FICHA_DOR:4",
        "PERGUNTAR_FICHA_DOR:",
        "PERGUNTAR_FICHA_X",
        "PERGUNTAR_FICHA_CRIANCA@tia",
    ):
        assert pergunta_da_ficha(invalida) is None, invalida


@pytest.mark.parametrize(
    ("plano", "esperado"),
    [
        ("minha filha recem nascida esta com uma alergia", "filha"),
        ("meu filho esta com tosse", "filho"),
        ("o bebe esta com febre", "bebe"),
        ("a crianca esta com dor", None),
    ],
)
def test_paciente_da_conversa(plano: str, esperado: str | None) -> None:
    assert paciente_da_conversa(plano) == esperado


@pytest.mark.parametrize(
    ("plano", "idade_meses", "esperado"),
    [
        ("minha filha recem nascida", None, True),
        ("o bebe nasceu ha 5 dias", None, True),
        ("bebe de 2 meses com febre", None, True),
        ("minha filha de 6 anos", None, False),
        ("meu filho com tosse", 1, True),
        ("meu filho com tosse", 8, False),
    ],
)
def test_bebe_pequeno(plano: str, idade_meses: int | None, esperado: bool) -> None:
    assert bebe_pequeno(plano, idade_meses) is esperado


@pytest.mark.parametrize(
    ("plano", "esperado"),
    [("nao", True), ("sim normal", True), ("tudo bem", True), ("sao umas bolinhas vermelhas", False)],
)
def test_resposta_curta(plano: str, esperado: bool) -> None:
    assert resposta_curta(plano) is esperado


def test_o_texto_de_orientacao_e_da_populacao() -> None:
    adulto = texto_de_orientacao("adult", bebe_com_menos_de_3_meses=False)
    crianca = texto_de_orientacao("pediatric", bebe_com_menos_de_3_meses=False)
    gestante = texto_de_orientacao("gestante", bebe_com_menos_de_3_meses=False)
    bebe = texto_de_orientacao("pediatric", bebe_com_menos_de_3_meses=True)
    assert "clínico geral" in adulto and "pediatra" not in adulto
    assert "pediatra" in crianca
    assert "ginecologista" not in crianca and "cirurgião" not in crianca and "clínico" not in crianca
    assert "obstetra" in gestante and "pediatra" not in gestante
    assert bebe == RESPOSTA_TRIAGEM_BEBE_PEQUENO
    assert "7 dias" not in bebe, "bebe com menos de 3 meses nao recebe o prazo de consulta"
    assert "equipe de saúde" in bebe and "192" in bebe and "pronto-atendimento" in bebe
    for texto in (adulto, crianca, gestante):
        assert "7 dias úteis" in texto
        assert len(re.findall(r"[.!?](?:\s|$)", texto)) <= 6, "T05 enxuto: no maximo 6 frases"


@pytest.mark.parametrize("grupo", sorted(PERGUNTAS_DA_FICHA))
def test_toda_combinacao_de_itens_que_faltam_passa_nas_cercas(grupo: str) -> None:
    from itertools import combinations

    from maezo.agents.helena.triagem_v2 import ITENS_DA_FICHA

    total = len(ITENS_DA_FICHA[grupo])
    pacientes = (None, "filha", "filho", "bebe") if grupo == "CRIANCA" else (None,)
    for tamanho in range(1, total + 1):
        for escolhidos in combinations(range(1, total + 1), tamanho):
            for paciente in pacientes:
                for de_novo in (False, True):
                    codigo = codigo_da_ficha(grupo, escolhidos, paciente=paciente, de_novo=de_novo)
                    texto = pergunta_da_ficha(codigo)
                    assert texto, codigo
                    assert motivo_de_canal_nao_confirmado(texto) is None, codigo
                    assert motivo_de_recusa(texto, "collect") is None, codigo
                    assert frases_longas(texto) == [], codigo
                    assert "helena" not in texto.lower()
