"""Historico curto da conversa da Helena (DL-0080, `MAEZO_HELENA_HISTORICO`, default off).

O DEFEITO MEDIDO EM 07/10/2026: a Helena nao mantinha conversa. "Minha mae esta com febre moderada"
seguido de "Ela tem 80 anos" chegava ao classificador como duas mensagens sem relacao, e a DMN nunca
via febre + 80 anos como um caso so' (I05 da bateria).

O QUE ESTE ARQUIVO PROVA:
  1. flag DESLIGADA = comportamento de antes byte a byte (prompt do classificador e da redacao,
     textos enviados, e nenhum historico no estado — nem plantado);
  2. I05: com a flag ligada, o turno 2 ve o turno 1 num bloco NAO CONFIAVEL e a DMN recebe o par
     febre + 80 anos; a rota e' a que a DMN der para esse par;
  3. a janela de 6 h (a mesma da memoria clinica) e a validacao fail-closed;
  4. o historico guarda SO' texto `{papel, texto, em}` — nunca telefone, hash, pseudo-id ou
     identidade — e da Helena so' o texto ENVIADO, nunca o rascunho barrado pela cerca;
  5. a fiacao: settings, despachante (liga a coleta junto) e `build`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from maezo.agents.helena import graph as graph_module
from maezo.agents.helena.graph import (
    MEMORIA_CLINICA_JANELA_HORAS,
    RESPOSTA_SINTOMA_SEM_ALERTA,
    HelenaGraph,
    HelenaState,
)
from maezo.agents.helena.historico import (
    HISTORICO_JANELA_HORAS,
    HISTORICO_MAX_TROCAS,
    HISTORICO_TEXTO_MAX_CHARS,
    acrescentar_turno,
    historico_valido,
)
from maezo.agents.helena.prompts import (
    classify_historico_adendo,
    classify_prompt,
    response_historico_adendo,
)
from maezo.platform.webhooks.whatsapp import dispatch as dispatch_module
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
from maezo.runtime.inference import InferenceProvider
from maezo.runtime.prompt_format import render_untrusted_block
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_MARCA_CLASSIFY = "Tarefa: leia a mensagem do beneficiario"
_PSEUDO = "PSEUDO-HIST-0001"
_IDENTIDADE = {
    "portable_subject_ref": "subj-ref-HIST-0001",
    "faixa_etaria": "idoso",
    "plano_ativo": True,
    "vigencia_inicio": "2019-03-17",
    "vigencia_fim": "2031-12-31",
    "carencia_vigente": True,
    "titular_ref": "subj-ref-TITULAR-HIST",
}


class _Inferencia:
    """Classificador roteirizado (fila) + redacao com texto fixo; grava TODO prompt recebido."""

    def __init__(
        self, classificacoes: list[dict[str, Any]], rascunho: str = "Posso ajudar com isso."
    ) -> None:
        self._classificacoes = [json.dumps(c) for c in classificacoes]
        self._rascunho = rascunho
        self.prompts: list[str] = []
        self.model_id = "fake"

    @property
    def prompts_de_classify(self) -> list[str]:
        return [p for p in self.prompts if _MARCA_CLASSIFY in p]

    @property
    def prompts_de_redacao(self) -> list[str]:
        return [p for p in self.prompts if _MARCA_CLASSIFY not in p]

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        if _MARCA_CLASSIFY in prompt:
            return self._classificacoes.pop(0)
        return self._rascunho


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


class _DmnAdultoR8(FakeDmnTransport):
    """A tabela de adulto com a `r8` (febre + 70 anos ou mais = bandeira); o resto, catch-all."""

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], Any]:
        rows, versao = await super().evaluate(decision_key, variables, tenant=tenant)
        idade = variables.get("idade_anos")
        if variables.get("sintoma_codigo") == "febre" and isinstance(idade, int) and idade >= 70:
            return [
                {"red_flag": True, "prioridade": "P2", "conduta": "ESCALATE_URGENT", "motivo": "febre idoso"}
            ], versao
        return rows, versao


def _dmn() -> _DmnAdultoR8:
    dmn = _DmnAdultoR8()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": False, "prioridade": "-", "conduta": "CONTINUE", "motivo": "sem criterio"}],
    )
    return dmn


def _classify(**campos: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent": "information",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    base.update(campos)
    return base


def _estado(mensagem: str, **extra: Any) -> HelenaState:
    estado: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_historico",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": _PSEUDO,
        "message_body": mensagem,
    }
    estado.update(extra)
    return cast(HelenaState, estado)


def _grafo(
    inferencia: _Inferencia, *, historico: bool, dmn: FakeDmnTransport | None = None, **kw: Any
) -> HelenaGraph:
    return HelenaGraph(
        inference=cast(InferenceProvider, inferencia),
        dmn=dmn or _dmn(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
        historico_enabled=historico,
        **kw,
    )


async def _conversa(
    mensagens: list[str],
    classificacoes: list[dict[str, Any]],
    *,
    historico: bool,
    rascunho: str = "Posso ajudar.",
) -> tuple[list[dict[str, Any]], _Inferencia, _WhatsApp, FakeDmnTransport]:
    """Roda os turnos no MESMO thread de checkpoint, como o receptor faz."""
    inferencia = _Inferencia(classificacoes, rascunho)
    zap = _WhatsApp()
    dmn = _dmn()
    grafo = HelenaGraph(
        inference=cast(InferenceProvider, inferencia),
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=zap,
        historico_enabled=historico,
    )
    compilado = grafo.compile_graph().compile(checkpointer=InMemorySaver())
    config: RunnableConfig = {"configurable": {"thread_id": "wa:amh:hk1_historico"}}
    estados = [dict(await compilado.ainvoke(_estado(m), config)) for m in mensagens]
    return estados, inferencia, zap, dmn


# =================================================================================================
# 1. Flag desligada = a Helena de antes, byte a byte
# =================================================================================================


@pytest.mark.asyncio
async def test_flag_desligada_e_byte_a_byte_o_comportamento_de_antes() -> None:
    mensagens = ["Bom dia, quanto custa a segunda via?", "E onde eu pego o boleto?"]
    classificacoes = [_classify(), _classify()]
    estados, inferencia, zap, _ = await _conversa(mensagens, classificacoes, historico=False)

    # O prompt do classificador e' EXATAMENTE o de antes: instrucoes + bloco da mensagem, nada mais.
    esperado = [f"{classify_prompt()}\n\n{render_untrusted_block('message_body', m)}" for m in mensagens]
    assert inferencia.prompts_de_classify == esperado
    for prompt in inferencia.prompts:
        assert "historico_conversa" not in prompt
        assert "HISTORICO DA CONVERSA" not in prompt
    assert all(e.get("historico_conversa") is None for e in estados)
    assert len(zap.enviados) == 2


@pytest.mark.asyncio
async def test_flag_desligada_descarta_historico_plantado() -> None:
    """Um historico vindo do checkpoint (flag ligada antes, desligada agora) nao atravessa o `receive`."""
    plantado = [{"papel": "beneficiario", "texto": "SEGREDO-PLANTADO", "em": datetime.now(UTC).isoformat()}]
    grafo = _grafo(_Inferencia([]), historico=False)
    reset = await grafo.receive(
        _estado("oi", historico_conversa=plantado, ultima_mensagem_em=datetime.now(UTC).isoformat())
    )
    assert reset["historico_conversa"] is None


def test_o_default_do_grafo_e_da_fabrica_e_desligado() -> None:
    import inspect

    assert inspect.signature(HelenaGraph.__init__).parameters["historico_enabled"].default is False
    assert 'cfg.get("historico_enabled", False) is True' in inspect.getsource(graph_module.build)


# =================================================================================================
# 2. I05 — "minha mae com febre moderada" -> "ela tem 80 anos" e' UM caso para a DMN
# =================================================================================================


@pytest.mark.asyncio
async def test_i05_o_par_febre_e_80_anos_chega_a_dmn_como_um_caso_so() -> None:
    turno1 = "Minha mãe está com febre moderada"
    turno2 = "Ela tem 80 anos"
    classificacoes = [
        _classify(intent="symptom", population="adult", sintoma_codigo="febre", intensidade="moderada"),
        # O que o classificador devolve VENDO o historico: o quadro acumulado.
        _classify(
            intent="symptom",
            population="adult",
            sintoma_codigo="febre",
            intensidade="moderada",
            idade_anos=80,
        ),
    ]
    estados, inferencia, zap, dmn = await _conversa([turno1, turno2], classificacoes, historico=True)

    # Turno 1: sem historico ainda — o prompt e' o de antes, e a resposta e' o texto fixo de sintoma.
    assert inferencia.prompts_de_classify[0] == (
        f"{classify_prompt()}\n\n{render_untrusted_block('message_body', turno1)}"
    )
    assert zap.enviados[0] == RESPOSTA_SINTOMA_SEM_ALERTA

    # Turno 2: o turno 1 (e a resposta que SAIU) chegam ao classificador num bloco NAO CONFIAVEL.
    prompt2 = inferencia.prompts_de_classify[1]
    bloco = render_untrusted_block(
        "historico_conversa", f"beneficiario: {turno1}\nhelena: {RESPOSTA_SINTOMA_SEM_ALERTA}"
    )
    assert prompt2 == (
        f"{classify_prompt()}\n\n{classify_historico_adendo()}\n\n{bloco}\n"
        f"{render_untrusted_block('message_body', turno2)}"
    )
    # o texto da pessoa so' aparece DENTRO dos blocos nao confiaveis, nunca nas instrucoes
    instrucoes = prompt2.split("<<<NAO_CONFIAVEL", 1)[0]
    assert turno1 not in instrucoes and turno2 not in instrucoes

    # A DMN recebeu o par como um caso so' — e a rota e' a que ela deu (bandeira -> escalate).
    tabela, entrada = dmn.calls[-1]
    assert tabela == "triage_redflag_adult"
    assert entrada["sintoma_codigo"] == "febre"
    assert entrada["idade_anos"] == 80
    assert estados[1]["population"] == "adult"
    assert estados[1]["next_kind"] == "escalate"
    assert estados[1]["escalation_motivo"] == "red_flag_clinico"


@pytest.mark.asyncio
async def test_a_redacao_do_inform_recebe_o_historico_em_bloco_nao_confiavel() -> None:
    turno1, turno2 = "Quero saber da minha carteirinha", "E da rede credenciada?"
    estados, inferencia, zap, _ = await _conversa(
        [turno1, turno2], [_classify(), _classify()], historico=True, rascunho="Posso ajudar."
    )
    redacao2 = inferencia.prompts_de_redacao[1]
    assert response_historico_adendo() in redacao2
    assert render_untrusted_block(
        "historico_conversa", f"beneficiario: {turno1}\nhelena: {zap.enviados[0]}"
    ) in (redacao2)
    # o primeiro turno nao tinha historico: redacao de antes, sem adendo
    assert response_historico_adendo() not in inferencia.prompts_de_redacao[0]
    assert [e["papel"] for e in estados[1]["historico_conversa"]] == [
        "beneficiario",
        "helena",
        "beneficiario",
        "helena",
    ]


# =================================================================================================
# 3. Janela de 6 h e validacao fail-closed
# =================================================================================================

_AGORA = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _hist(*textos: str) -> list[dict[str, str]]:
    return [{"papel": "beneficiario", "texto": t, "em": _AGORA.isoformat()} for t in textos]


def test_a_janela_e_a_mesma_da_memoria_clinica() -> None:
    assert HISTORICO_JANELA_HORAS == MEMORIA_CLINICA_JANELA_HORAS == 6.0


@pytest.mark.parametrize(
    ("delta", "vale"),
    [
        (timedelta(hours=5, minutes=59), True),
        (timedelta(hours=6), True),
        (timedelta(hours=6, seconds=1), False),
        (timedelta(hours=7), False),
        (timedelta(minutes=-1), False),  # carimbo no futuro
    ],
)
def test_historico_expira_seis_horas_depois_da_ultima_mensagem(delta: timedelta, vale: bool) -> None:
    resultado = historico_valido(_hist("oi"), ultima_mensagem_em=(_AGORA - delta).isoformat(), agora=_AGORA)
    assert (resultado is not None) is vale


@pytest.mark.asyncio
async def test_receive_zera_o_historico_expirado_e_preserva_o_valido() -> None:
    grafo = _grafo(_Inferencia([]), historico=True)
    agora = datetime.now(UTC)
    velho = await grafo.receive(
        _estado(
            "oi", historico_conversa=_hist("a"), ultima_mensagem_em=(agora - timedelta(hours=7)).isoformat()
        )
    )
    assert velho["historico_conversa"] is None
    recente = await grafo.receive(
        _estado(
            "oi", historico_conversa=_hist("a"), ultima_mensagem_em=(agora - timedelta(hours=1)).isoformat()
        )
    )
    assert recente["historico_conversa"] == _hist("a")
    sem_carimbo = await grafo.receive(_estado("oi", historico_conversa=_hist("a")))
    assert sem_carimbo["historico_conversa"] is None


@pytest.mark.parametrize(
    "bruto",
    [
        "texto solto",
        {"papel": "beneficiario"},
        [{"papel": "beneficiario", "texto": "x"}],  # sem carimbo
        [{"papel": "atendente", "texto": "x", "em": _AGORA.isoformat()}],  # papel fora do vocabulario
        [{"papel": "beneficiario", "texto": 123, "em": _AGORA.isoformat()}],
        [{"papel": "beneficiario", "texto": "x", "em": "ontem"}],
        [{"papel": "beneficiario", "texto": "x", "em": _AGORA.isoformat(), "telefone": "5511999990000"}],
        [*_hist("ok"), "lixo"],
    ],
)
def test_historico_malformado_vira_none_e_nunca_parcial(bruto: Any) -> None:
    assert historico_valido(bruto, ultima_mensagem_em=_AGORA.isoformat(), agora=_AGORA) is None


def test_corta_o_texto_e_guarda_so_as_ultimas_trocas() -> None:
    historico: list[dict[str, str]] = []
    for i in range(10):
        historico = acrescentar_turno(
            historico, mensagem=f"m{i} " + "x" * 900, resposta=f"r{i}", agora=_AGORA
        )
    assert len(historico) == HISTORICO_MAX_TROCAS
    assert all(len(e["texto"]) <= HISTORICO_TEXTO_MAX_CHARS for e in historico)
    assert historico[-1]["texto"] == "r9"


# =================================================================================================
# 4. So' texto: nunca telefone/identidade; da Helena so' o que SAIU
# =================================================================================================


@pytest.mark.asyncio
async def test_historico_nao_guarda_telefone_hash_nem_identidade() -> None:
    inferencia = _Inferencia([_classify()])
    zap = _WhatsApp()
    grafo = HelenaGraph(
        inference=cast(InferenceProvider, inferencia),
        dmn=_dmn(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=zap,
        historico_enabled=True,
    )
    estado = _estado(
        "Quero a segunda via",
        identidade_beneficiario=dict(_IDENTIDADE),
        identidade_desfecho="indeterminado",
        message_ref="wamid-ref-HIST-0001",
    )
    resultado = dict(await grafo.compile_graph().compile().ainvoke(estado))
    historico = resultado["historico_conversa"]
    assert [set(e) for e in historico] == [{"papel", "texto", "em"}] * 2
    serializado = json.dumps(historico, ensure_ascii=False)
    proibidos = [_PSEUDO, "hk1_historico", "wa:amh", "wamid-ref-HIST-0001", *map(str, _IDENTIDADE.values())]
    for valor in proibidos:
        assert valor not in serializado, valor
    assert [e["texto"] for e in historico] == ["Quero a segunda via", zap.enviados[-1]]


@pytest.mark.asyncio
async def test_o_rascunho_barrado_pela_cerca_nunca_entra_no_historico() -> None:
    barrado = "Baixe o aplicativo Austa Saude e veja a segunda via por la."
    estados, _, zap, _ = await _conversa(
        ["Quero a segunda via do boleto"], [_classify()], historico=True, rascunho=barrado
    )
    historico = estados[0]["historico_conversa"]
    assert zap.enviados and zap.enviados[-1] != barrado, "a cerca deveria ter trocado o rascunho"
    assert barrado not in json.dumps(historico, ensure_ascii=False)
    assert historico[-1] == {"papel": "helena", "texto": zap.enviados[-1], "em": historico[-1]["em"]}


# =================================================================================================
# 5. Fiacao: settings, despachante, fabrica
# =================================================================================================


def _settings(**extra: object) -> WhatsAppWebhookSettings:
    return WhatsAppWebhookSettings(app_secret="s", verify_token="v", **extra)  # type: ignore[arg-type]


def test_settings_default_desligado_e_nome_canonico(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAEZO_HELENA_HISTORICO", raising=False)
    assert _settings().helena_historico is False
    monkeypatch.setenv("MAEZO_HELENA_HISTORICO", "true")
    assert _settings().helena_historico is True


@pytest.mark.parametrize("ligado", [False, True])
def test_despachante_passa_o_historico_e_liga_a_coleta_junto(
    monkeypatch: pytest.MonkeyPatch, ligado: bool
) -> None:
    capturado: dict[str, Any] = {}

    class _Grafo:
        def compile(self, **_: Any) -> str:
            return "compilado"

    def _build(cfg: dict[str, Any]) -> _Grafo:
        capturado.update(cfg)
        return _Grafo()

    monkeypatch.setattr(dispatch_module, "build", _build)
    nada = cast(Any, None)
    despachante = dispatch_module.HelenaDispatcher(
        tenant_id="amh",
        inference=nada,
        dmn=nada,
        cibseven=nada,
        whatsapp_client=nada,
        pseudonymizer=nada,
        audit_sink=nada,
        historico_enabled=ligado,
    )
    despachante._compile_turn_graph(nada)
    assert capturado["historico_enabled"] is ligado
    assert capturado["coleta_enabled"] is ligado
