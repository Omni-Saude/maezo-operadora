"""DL-0097 — rodada 10: o teste real do dono (14/10/2026), conversa do recem-nascido com um caso aberto.

A transcricao: uma contestacao de cobranca abre um caso ADMINISTRATIVO; na MESMA conversa a pessoa diz que
a filha recem-nascida esta com alergia e "chorando muito, deve estar sentindo dor". Antes: a Helena perguntava
"o que voce esta sentindo", leu o terceiro turno como pedido de pessoa, achou o caso de cobranca aberto e
respondeu "seu atendimento ja esta aberto" — nenhum caso clinico, nenhuma orientacao de urgencia.

  A. paciente em terceira pessoa + recem-nascido com sinal de sofrimento escala na hora, com o 192;
  B. conteudo clinico nunca vira `solicitacao_humano`; o caso clinico abre na chave `-clin`;
  C. o aviso de "ja esta com a equipe" fala em termos humanos, sem estado interno;
  D. "dependentes" nunca diz "nao consta no cadastro".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.agents.helena import graph as graph_module
from maezo.agents.helena.graph import (
    PROCESS_KEY,
    RESPOSTA_DEPENDENTES,
    RESPOSTA_HANDOFF_JA_ABERTO,
    HelenaGraph,
    _paciente_da_conversa,
    _pergunta_de_dependentes,
    _pergunta_de_terceiro,
    new_helena_state,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_CONVERSA = "wa:amh:hk1_bebe_r10"
_CHAVE = f"ESC-amh-{_CONVERSA}"
_CHAVE_CLINICA = f"{_CHAVE}-clin"


class _Gravador(FakeCibSevenTransport):
    def __init__(self) -> None:
        super().__init__()
        self.inicios: list[tuple[str, dict[str, Any]]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.inicios.append((business_key, dict(variables)))
        return await super().start_process_instance(process_key, business_key, variables)


class _Modelo:
    def __init__(self, respostas: list[str]) -> None:
        self._respostas = list(respostas)
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
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


def _grafo(respostas: list[str], cib: FakeCibSevenTransport) -> tuple[Any, _WhatsApp]:
    whatsapp = _WhatsApp()
    dmn = FakeDmnTransport()
    for tabela in ("triage_redflag_adult", "triage_redflag_pediatric"):
        dmn.register(tabela, [{"red_flag": False, "prioridade": "P3", "conduta": "CONTINUE", "motivo": "x"}])
    grafo = HelenaGraph(
        inference=_Modelo(respostas),  # type: ignore[arg-type]
        dmn=dmn,
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        relogio=lambda: datetime(2026, 10, 14, 15, 0, tzinfo=UTC),
    )
    return grafo.compile_graph().compile(), whatsapp


def _entrada(mensagem: str, conversa: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """Entrada do turno; `conversa` e' o historico `(papel, texto)` ja' trocado na MESMA conversa."""
    estado: dict[str, Any] = dict(
        new_helena_state(
            tenant_id="amh",
            conversation_id=_CONVERSA,
            canal="whatsapp",
            beneficiario_pseudo_id="PSEUDO-BEBE-R10",
            message_body=mensagem,
            message_ref="hk1_msg_r10",
        )
    )
    agora = datetime.now(UTC).isoformat()
    if conversa:
        estado["historico_conversa"] = [{"papel": p, "texto": t, "em": agora} for p, t in conversa]
        estado["ultima_mensagem_em"] = agora
    return estado


def _caso_administrativo(motivo: str = "cobranca") -> _Gravador:
    cib = _Gravador()
    cib.seed_instance(
        ProcessInstance(
            instance_id="caso-adm-r10",
            process_key=PROCESS_KEY,
            business_key=_CHAVE,
            state="ACTIVE",
            already_existed=True,
        ),
        variables={"motivo_categoria": motivo},
    )
    return cib


# =================================================================================================
# A. Terceira pessoa e recem-nascido
# =================================================================================================


@pytest.mark.parametrize(
    ("mensagem", "esperado"),
    [
        ("minha filha recém-nascida está com uma alergia", ("sua filha", "da sua filha")),
        ("meu filho está com febre", ("seu filho", "do seu filho")),
        ("o bebê não para de chorar", ("o bebê", "do bebê")),
        ("minha mãe está passando mal", ("sua mãe", "da sua mãe")),
        ("eu estou com dor de cabeça", None),
        ("sou mãe e estou com dor de cabeça", None),
    ],
)
def test_paciente_da_conversa_e_quem_tem_o_sintoma(mensagem: str, esperado: tuple[str, str] | None) -> None:
    assert _paciente_da_conversa({"message_body": mensagem}) == esperado


def test_paciente_vem_do_historico_quando_a_mensagem_nao_diz() -> None:
    estado = {
        "message_body": "ele está chorando muito",
        "historico_conversa": [{"papel": "beneficiario", "texto": "meu bebê está com alergia"}],
    }

    assert _paciente_da_conversa(estado) == ("o bebê", "do bebê")


def test_a_pergunta_de_coleta_fala_do_paciente_e_nunca_com_o_adulto() -> None:
    for pergunta in (
        "PERGUNTAR_INTENSIDADE",
        "PERGUNTAR_CARACTERIZACAO",
        "PERGUNTAR_IDADE",
        "PERGUNTAR_IDADE_GESTACIONAL",
        "default",
    ):
        texto = _pergunta_de_terceiro(pergunta, ("sua filha", "da sua filha"))
        assert "sua filha" in texto or "da sua filha" in texto
        assert "você está" not in texto.lower() and "você sente" not in texto.lower()


async def test_recem_nascido_chorando_muito_escala_na_hora_com_o_192() -> None:
    cib = _Gravador()
    grafo, whatsapp = _grafo([_classify(intent="symptom", population="pediatric", idade_meses=0)], cib)

    resultado = await grafo.ainvoke(_entrada("minha filha recém-nascida está chorando muito, deve ser dor"))

    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert resultado["escalation_severidade"] == "grave"
    assert resultado["escalation_business_key"] == _CHAVE
    enviado = whatsapp.enviados[0]
    assert "Ligue 192 (SAMU) agora" in enviado
    # Rodada do dono (18/10/2026): o texto de alarme e' NEUTRO (nunca supor genero/parentesco).
    assert "esposa" not in enviado and "filha" not in enviado


async def test_recem_nascido_sem_sinal_de_sofrimento_nao_dispara_o_alerta() -> None:
    cib = _Gravador()
    grafo, _ = _grafo([_classify(intent="symptom", population="pediatric", idade_meses=0)], cib)

    resultado = await grafo.ainvoke(_entrada("minha filha recém-nascida tem uma manchinha no braço"))

    assert resultado.get("escalation_motivo") != "red_flag_clinico"


async def test_sinal_negado_em_recem_nascido_nao_dispara_o_alerta() -> None:
    cib = _Gravador()
    grafo, _ = _grafo([_classify(intent="symptom", population="pediatric", idade_meses=0)], cib)

    resultado = await grafo.ainvoke(_entrada("meu bebê recém-nascido está sem febre e sem dor, só dormindo"))

    assert resultado.get("escalation_motivo") != "red_flag_clinico"


# =================================================================================================
# B. Conteudo clinico nunca vira pedido de pessoa; caso clinico na chave propria
# =================================================================================================


async def test_conteudo_clinico_lido_como_pedido_de_pessoa_nao_abre_solicitacao_humano() -> None:
    cib = _caso_administrativo()
    grafo, _ = _grafo([_classify(intent="human_request", population="pediatric")], cib)

    resultado = await grafo.ainvoke(_entrada("minha filha está com alergia e bolinhas na pele"))

    assert resultado.get("escalation_motivo") != "solicitacao_humano"
    assert all(v.get("motivo_categoria") != "solicitacao_humano" for _, v in cib.inicios)


async def test_pedido_explicito_de_pessoa_continua_sendo_pedido_de_pessoa() -> None:
    cib = _Gravador()
    grafo, _ = _grafo([_classify(intent="human_request")], cib)

    resultado = await grafo.ainvoke(_entrada("quero falar com uma pessoa"))

    assert resultado["escalation_motivo"] == "solicitacao_humano"


# =================================================================================================
# C. Aviso de "ja esta com a equipe" em termos humanos
# =================================================================================================

_PROIBIDO_NO_AVISO = ("abri", "aberto", "aberta", "por isso", "não abri", "continua valendo")


def test_nenhum_aviso_de_caso_ja_com_a_equipe_fala_em_estado_interno() -> None:
    avisos = {
        nome: valor
        for nome, valor in vars(graph_module).items()
        if nome.startswith(("RESPOSTA_HANDOFF_JA_ABERTO", "RESPOSTA_CASO_ABERTO"))
        and isinstance(valor, str)
        and "EMERGENCIA" not in nome  # os da emergencia (caso clinico) tem texto proprio
    }
    assert RESPOSTA_HANDOFF_JA_ABERTO in avisos.values()

    for nome, texto in avisos.items():
        for proibido in _PROIBIDO_NO_AVISO:
            assert proibido not in texto.lower(), f"{nome} fala em estado interno: {proibido!r}"


async def test_pedido_de_pessoa_com_caso_de_cobranca_aberto_avisa_que_e_sobre_cobranca() -> None:
    cib = _caso_administrativo("cobranca")
    grafo, whatsapp = _grafo([_classify(intent="human_request")], cib)

    resultado = await grafo.ainvoke(_entrada("quero falar com uma pessoa"))

    assert resultado["start_desfecho"] == "ja_ativo"
    assert "cobrança" in whatsapp.enviados[0]
    assert all(proibido not in whatsapp.enviados[0].lower() for proibido in _PROIBIDO_NO_AVISO)


# =================================================================================================
# D. Dependentes
# =================================================================================================


@pytest.mark.parametrize(
    "mensagem",
    ["quais são meus dependentes?", "tenho dependente no plano?", "quem são os dependentes do meu plano"],
)
async def test_dependentes_nunca_diz_que_nao_consta_no_cadastro(mensagem: str) -> None:
    cib = _Gravador()
    grafo, whatsapp = _grafo([_classify(intent="information")], cib)

    await grafo.ainvoke(_entrada(mensagem))

    assert whatsapp.enviados == [RESPOSTA_DEPENDENTES]
    assert "não consta" not in whatsapp.enviados[0].lower()
    assert "cadastro" not in whatsapp.enviados[0].lower()
    assert cib.inicios == []


def test_a_pergunta_de_dependentes_so_pega_dependentes() -> None:
    assert _pergunta_de_dependentes("quais são meus dependentes?")
    assert not _pergunta_de_dependentes("qual o tipo do meu plano")


# =================================================================================================
# A transcricao: contestacao (caso administrativo) e, na mesma conversa, o bebe
# =================================================================================================


async def test_transcricao_contestacao_depois_bebe_na_mesma_conversa() -> None:
    # 1. a contestacao de cobranca abriu o caso ADMINISTRATIVO na chave da conversa (o Lucas atende).
    cib = _caso_administrativo("cobranca")
    conversa: list[tuple[str, str]] = [
        ("beneficiario", "quero contestar uma cobrança que veio errada"),
        ("helena", "Seu pedido sobre cobrança já está com a nossa equipe. O atendente responde por aqui."),
    ]

    # 2. o bebe, na MESMA conversa: alergia, sem sinal de sofrimento ainda. Nunca pedido de pessoa.
    grafo, whatsapp = _grafo([_classify(intent="human_request", population="pediatric", idade_meses=0)], cib)
    turno2 = "minha filha recém-nascida está com uma alergia, bolinhas vermelhas na pele"
    resultado = await grafo.ainvoke(_entrada(turno2, conversa))
    assert resultado.get("escalation_motivo") != "solicitacao_humano"
    assert cib.inicios == []
    conversa += [("beneficiario", turno2), ("helena", whatsapp.enviados[-1])]

    # 3. "deve estar sentindo dor, pois esta chorando muito": o modelo leu como pedido de pessoa. Agora e'
    #    alerta clinico: caso na chave `-clin`, 192, e NUNCA o aviso de "ja esta com a equipe".
    grafo, whatsapp = _grafo([_classify(intent="human_request")], cib)
    turno3 = "ele deve estar sentindo dor, pois está chorando muito"
    resultado = await grafo.ainvoke(_entrada(turno3, conversa))

    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert resultado["escalation_business_key"] == _CHAVE_CLINICA
    assert resultado["start_desfecho"] == "novo"
    assert [c for c, _ in cib.inicios] == [_CHAVE_CLINICA]
    assert dict(cib.inicios)[_CHAVE_CLINICA]["motivo_categoria"] == "red_flag_clinico"
    enviado = whatsapp.enviados[-1]
    assert "192" in enviado
    assert RESPOSTA_HANDOFF_JA_ABERTO not in enviado
    assert all(proibido not in enviado.lower() for proibido in _PROIBIDO_NO_AVISO)
    # o caso administrativo segue como estava: nada foi cancelado nem alterado.
    status = await cib.get_process_status(_CHAVE)
    assert status.instance_id == "caso-adm-r10"
