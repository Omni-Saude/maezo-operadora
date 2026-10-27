"""Habilidade de PROTOCOLO da Helena (decisao do dono, 25/10/2026): o MODELO escolhe o protocolo e le' as
respostas, o PROTOCOLO traz as perguntas e os destinos, o CODIGO decide.

Tudo pelo grafo COMPILADO com respostas falsas do modelo (roteador, escolha, leitura e mapa). Frases escritas
para cada comportamento (3 ou mais), com o controle negativo; as conversas do dono (22/10 e 25/10) entram
parafraseadas, sem nenhum dado pessoal."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from maezo.agents.helena.graph import HelenaGraph
from maezo.agents.helena.habilidades.atendimento_humano.textos import PENDENTE_OFERTA_EQUIPE
from maezo.agents.helena.habilidades.protocolo import nucleo as n
from maezo.agents.helena.habilidades.sintoma.textos import TEXTO_OFERTA_EQUIPE
from maezo.conhecimento import base_saude as bs
from maezo.conhecimento import protocolos as p
from maezo.conhecimento import saude_gerativa as sg
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_rodada_3_simulador import (
    _CONVERSA,
    _classify,
    _entrada,
    _starts,
    _WhatsApp,
)
from tests.unit.agents.test_helena_roteador_ativo import _FonteGrafo
from tests.unit.agents.test_helena_saude_gerativa import _Transporte
from tests.unit.agents.test_helena_v4 import _DmnReal

_RAIZ = Path(__file__).resolve().parents[3]
_BASE_REAL = _RAIZ / "spec" / "conhecimento" / "saude"
_CATALOGO = p.carregar(
    _RAIZ / "spec" / "agents" / "helena" / "pacote" / "protocolos",
    esp_ids=frozenset(x.stem for x in (_BASE_REAL / "especialidade").glob("*.yaml")),
)
_TEXTOS = _CATALOGO.textos
assert _TEXTOS is not None
_URINAR = _CATALOGO.protocolos["tri-adulto-dor-ao-urinar"]
_COSTAS = _CATALOGO.protocolos["tri-adulto-dor-nas-costas"]
_QUEDA = _CATALOGO.protocolos["tri-adulto-queda-ou-trauma"]
_UNIVERSAL = _CATALOGO.protocolos[p.UNIVERSAL_ADULTO]
_PROIBIDOS_NA_SAIDA = ("dias úteis", "dias uteis", "ANS", "http", "www.", "carteirinha", "Você prefere: 1)")


class _Modelo:
    """O modelo falso: cada tipo de chamada tem a sua fila (roteador, escolha, leitura, mapa, o resto)."""

    def __init__(
        self,
        roteador: list[str],
        *,
        escolha: list[str] | None = None,
        leitura: list[str] | None = None,
        mapa: list[str] | None = None,
        pre: list[str] | None = None,
        outras: list[str] | None = None,
    ) -> None:
        self.filas = {
            "roteador": list(roteador),
            "escolha": list(escolha or []),
            "leitura": list(leitura or []),
            "mapa": list(mapa or []),
            "pre": list(pre or []),
            "outras": list(outras or [_classify(intent="symptom", intensidade="moderada")] * 4),
        }
        self.prompts: dict[str, list[str]] = {k: [] for k in self.filas}
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        if "roteador de um assistente" in prompt:
            tipo = "roteador"
        elif "Voce escolhe o PROTOCOLO" in prompt:
            tipo = "escolha"
        elif "PERGUNTAS DE ALERTA que um assistente" in prompt:
            tipo = "leitura"
        elif "JA RESPONDIDAS" in prompt:
            tipo = "pre"
        elif "QUEIXA sobre a qual a pessoa pergunta" in prompt:
            tipo = "mapa"
        else:
            tipo = "outras"
        self.prompts[tipo].append(prompt)
        fila = self.filas[tipo]
        return fila.pop(0) if fila else ""


def _escolha(protocolo: str, publico: str = "adulto", faixa: str = "nao_se_aplica") -> str:
    return json.dumps({"publico": publico, "faixa_crianca": faixa, "protocolo": protocolo})


def _leitura(*respostas: str, outro: bool = False) -> str:
    return json.dumps({"respostas": list(respostas), "outro_sinal_grave": outro})


def _mapa(queixa: str, publico: str = "adulto") -> str:
    return json.dumps({"queixa": queixa, "publico": publico})


def _grafo(modelo: _Modelo, *, protocolos: bool = True) -> tuple[Any, _WhatsApp, FakeCibSevenTransport]:
    whatsapp = _WhatsApp()
    cib = FakeCibSevenTransport()
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=_DmnReal(),  # type: ignore[arg-type]
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        coleta_enabled=True,
        helena_v4_enabled=True,
        consultas_plano=_FonteGrafo(),  # type: ignore[arg-type]
        relogio=lambda: datetime(2026, 10, 25, 21, 0, tzinfo=UTC),
        saude_gerativa=sg.RedatorDeSaude(transporte=_Transporte(), conversas=frozenset({_CONVERSA})),
        helena_base_saude_enabled=True,
        base_saude=bs.carregar_base(_BASE_REAL),
        helena_roteador="ativo",
        helena_roteador_conversas=frozenset({_CONVERSA}),
        helena_protocolos_enabled=protocolos,
    )
    return grafo.compile_graph().compile(), whatsapp, cib


def _estado(
    fala: str, falas_da_helena: list[str] | None = None, pendente: str | None = None
) -> dict[str, Any]:
    estado = _entrada(fala, falas_da_helena)
    if pendente:
        estado |= {"coleta_pendente": pendente, "coleta_contexto": ""}
    return estado


def _limpo(texto: str) -> None:
    for proibido in _PROIBIDOS_NA_SAIDA:
        assert proibido not in texto, proibido


def _escalou(final: dict[str, Any]) -> bool:
    return bool(final.get("escalation_motivo"))


# --- Nucleo: a REGRA deterministica ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("respostas", "destino", "sinal"),
    [
        (("sim", "nao", "nao", "nao"), "LIGAR_192_AGORA", "choque_ou_alteracao_consciencia"),
        (("nao", "sim", "sim", "nao"), "ATENDIMENTO_HOJE", "suspeita_de_infeccao_no_rim"),
        (("nao", "nao", "nao", "sim"), "ATENDIMENTO_HOJE", "infeccao_urinaria_de_maior_risco"),
        (("sim", "sim", "nao", "sem_clareza"), "LIGAR_192_AGORA", "choque_ou_alteracao_consciencia"),
    ],
)
def test_o_sinal_mais_grave_presente_decide(respostas: tuple[str, ...], destino: str, sinal: str) -> None:
    d = n.decidir(_URINAR, (1, 2, 3, 4), respostas, 0)
    assert (d.tipo, d.destino, d.sinal) == ("destino", destino, sinal)


@pytest.mark.parametrize(
    ("respostas", "indices"),
    [
        (("sem_clareza", "nao", "nao", "nao"), (1,)),
        (("nao", "sem_clareza", "nao", "sem_clareza"), (2, 4)),
        (("sem_clareza",) * 4, (1, 2, 3, 4)),
    ],
)
def test_sem_clareza_repergunta_uma_vez_e_depois_oferece_a_equipe(
    respostas: tuple[str, ...], indices: tuple[int, ...]
) -> None:
    d = n.decidir(_URINAR, (1, 2, 3, 4), respostas, 0)
    assert d == n.Decisao("repergunta", indices=indices)
    assert n.decidir(_URINAR, indices, ("sem_clareza",) * len(indices), 1) == n.Decisao("oferta_equipe")


@pytest.mark.parametrize("protocolo", [_URINAR, _COSTAS, _UNIVERSAL])
def test_nenhum_sinal_vai_ao_destino_sem_sinal(protocolo: p.Protocolo) -> None:
    k = len(protocolo.perguntas_de_alerta)
    d = n.decidir(protocolo, tuple(range(1, k + 1)), ("nao",) * k, 0)
    assert d == n.Decisao("destino", protocolo.destino_se_nenhum_sinal)


@pytest.mark.parametrize("pid", sorted(_CATALOGO.protocolos))
def test_os_textos_de_todo_protocolo_real_seguem_as_regras_do_dono(pid: str) -> None:
    protocolo = _CATALOGO.protocolos[pid]
    assert _TEXTOS is not None
    textos = [
        n.texto_das_perguntas(protocolo),
        n.texto_sem_sinal(protocolo, _TEXTOS),
        n.texto_de_seguimento(protocolo, _TEXTOS, reclamou=True, agendamento=False),
        n.texto_de_seguimento(protocolo, _TEXTOS, reclamou=False, agendamento=True),
    ]
    for texto in textos:
        assert p.problemas_de_texto({"texto": texto}) == [], pid
        _limpo(texto)
    pergunta = textos[0]
    assert pergunta.startswith(n.cabecalho(protocolo)) and len(n.cabecalho(protocolo)) < 300
    assert pergunta.count("\n") == len(protocolo.perguntas_de_alerta) + 1
    # Toda orientacao termina com a frase de seguranca (192 ou 188).
    assert "192" in textos[1].splitlines()[-1] or "188" in textos[1].splitlines()[-1]


def test_cabecalhos_identificam_o_protocolo_no_historico() -> None:
    falas = [n.texto_das_perguntas(_COSTAS), "orientacao", n.texto_das_perguntas(_URINAR)]
    # A ultima pergunta ainda nao foi respondida: so' a primeira conta.
    assert n.protocolos_na_conversa(falas, _CATALOGO) == [_COSTAS.id]
    assert n.protocolos_na_conversa([*falas, "orientacao"], _CATALOGO) == [_COSTAS.id, _URINAR.id]


@pytest.mark.parametrize(
    "bruto",
    [
        '{"publico":"idoso","faixa_crianca":"nao_se_aplica","protocolo":"tri-adulto-dor-ao-urinar"}',
        '{"publico":"adulto","faixa_crianca":"nao_se_aplica","protocolo":"tri-adulto-inventado"}',
        '{"publico":"crianca","faixa_crianca":"2m_5a","protocolo":"tri-adulto-dor-ao-urinar"}',
        "nao sei",
    ],
)
def test_escolha_fora_do_vocabulario_e_invalida(bruto: str) -> None:
    escolha, ruins = n.ler_escolha(bruto, _CATALOGO)
    assert escolha is None and ruins


@pytest.mark.parametrize(
    "bruto",
    ['{"respostas":["nao","nao"],"outro_sinal_grave":false}', '{"respostas":["nao","x","nao","nao"]}', "{}"],
)
def test_leitura_fora_do_vocabulario_e_invalida(bruto: str) -> None:
    leitura, ruins = n.ler_leitura(bruto, 4)
    assert leitura is None and ruins


# --- (i) o modelo escolhe o protocolo pelo SENTIDO; perguntas do protocolo numa mensagem so' ------------
@pytest.mark.parametrize(
    "fala",
    [
        "estou com dificuldades ao urinar",
        "arde muito quando eu faço xixi",
        "to indo no banheiro toda hora e queima pra fazer xixi",
    ],
)
async def test_queixa_urinaria_recebe_as_perguntas_do_protocolo_e_nao_a_generica(fala: str) -> None:
    modelo = _Modelo([decisao_v7("sintoma")], escolha=[_escolha(_URINAR.id)])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala))
    texto = whatsapp.enviados[-1]
    assert texto == n.texto_das_perguntas(_URINAR)
    assert "peito" not in texto.lower() and "boca torta" not in texto.lower()
    assert final["coleta_pendente"] == n.codigo_do_alerta(_URINAR.id)
    assert not _escalou(final) and not _starts(cib)
    # O prompt da escolha leva a lista de protocolos com os exemplos e a fala da pessoa (bloco nao confiavel).
    prompt = modelo.prompts["escolha"][0]
    assert f"- {_URINAR.id}:" in prompt and fala in prompt


@pytest.mark.parametrize(
    "fala", ["estou me sentindo esquisito", "to com uma coisa estranha no corpo", "não estou nada bem hoje"]
)
async def test_sem_protocolo_proprio_vai_o_alerta_universal(fala: str) -> None:
    modelo = _Modelo([decisao_v7("sintoma")], escolha=[_escolha("nenhum")])
    grafo, whatsapp, _ = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala))
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(_UNIVERSAL)
    assert final["coleta_pendente"] == n.codigo_do_alerta(_UNIVERSAL.id)


@pytest.mark.parametrize("bruto", ["", "nao sei", '{"publico":"adulto"}'])
async def test_escolha_invalida_duas_vezes_vai_o_alerta_universal(bruto: str) -> None:
    modelo = _Modelo([decisao_v7("sintoma")], escolha=[bruto, bruto])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_estado("estou com uma dor estranha"))
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(_UNIVERSAL)
    assert len(modelo.prompts["escolha"]) == 2 and "ATENCAO" in modelo.prompts["escolha"][1]


# --- (ii)/(iii) o modelo le' a resposta, a REGRA decide -------------------------------------------------
@pytest.mark.parametrize(
    "resposta",
    ["não", "nenhuma dessas", "não, mas o que febre tem a ver com isso?"],
)
async def test_nenhum_sinal_orienta_com_especialista_e_canais_sem_192_falso(resposta: str) -> None:
    modelo = _Modelo([decisao_v7("sintoma", responde=True)], leitura=[_leitura("nao", "nao", "nao", "nao")])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(
        _estado(resposta, [n.texto_das_perguntas(_URINAR)], pendente=n.codigo_do_alerta(_URINAR.id))
    )
    texto = whatsapp.enviados[-1]
    assert texto == n.texto_sem_sinal(_URINAR, _TEXTOS)
    assert "urologista" in texto and "aplicativo Austa Clínicas" in texto and "portal do plano" in texto
    _limpo(texto)
    assert not _escalou(final) and not _starts(cib) and not final.get("coleta_pendente")


@pytest.mark.parametrize(
    ("resposta", "leitura"),
    [
        ("sim, estou com febre e calafrio", _leitura("nao", "sim", "nao", "nao")),
        ("a 3 sim, saiu sangue no xixi", _leitura("nao", "nao", "sim", "nao")),
        ("sou diabética", _leitura("nao", "nao", "nao", "sim")),
    ],
)
async def test_sinal_de_atendimento_hoje_orienta_sem_caso(resposta: str, leitura: str) -> None:
    modelo = _Modelo([decisao_v7("sintoma", responde=True)], leitura=[leitura])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(
        _estado(resposta, [n.texto_das_perguntas(_URINAR)], pendente=n.codigo_do_alerta(_URINAR.id))
    )
    assert whatsapp.enviados[-1].startswith(n.texto_do_destino_por_sinal("ATENDIMENTO_HOJE", "adulto"))
    assert "urologista" in whatsapp.enviados[-1]  # dono, 27/10: todo final diz o especialista
    assert not _escalou(final) and not _starts(cib)


@pytest.mark.parametrize("resposta", ["sim, a primeira", "a 1 é sim", "sim pra primeira pergunta"])
async def test_sinal_de_192_escala_como_o_alarme_de_sempre(resposta: str) -> None:
    modelo = _Modelo([decisao_v7("sintoma", responde=True)], leitura=[_leitura("sim", "nao", "nao", "nao")])
    grafo, _, _ = _grafo(modelo)
    final = await grafo.ainvoke(
        _estado(resposta, [n.texto_das_perguntas(_URINAR)], pendente=n.codigo_do_alerta(_URINAR.id))
    )
    assert final["escalation_motivo"] == "red_flag_clinico" and final["escalation_severidade"] == "grave"


@pytest.mark.parametrize("resposta", ["talvez", "acho que sim, não sei", "mais ou menos"])
async def test_sem_clareza_repergunta_so_as_duvidosas_e_depois_oferece_a_equipe_sem_192(
    resposta: str,
) -> None:
    modelo = _Modelo(
        [decisao_v7("sintoma", responde=True)], leitura=[_leitura("nao", "sem_clareza", "nao", "nao")]
    )
    grafo, whatsapp, _ = _grafo(modelo)
    final = await grafo.ainvoke(
        _estado(resposta, [n.texto_das_perguntas(_URINAR)], pendente=n.codigo_do_alerta(_URINAR.id))
    )
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(_URINAR, (2,))
    assert final["coleta_pendente"] == n.codigo_do_alerta(_URINAR.id, 1, (2,))
    # Segunda vez sem clareza: a oferta da equipe de saude, sem 192 e sem caso.
    modelo2 = _Modelo([decisao_v7("sintoma", responde=True)], leitura=[_leitura("sem_clareza")])
    grafo2, whatsapp2, cib2 = _grafo(modelo2)
    final2 = await grafo2.ainvoke(
        _estado(
            resposta,
            [n.texto_das_perguntas(_URINAR), n.texto_das_perguntas(_URINAR, (2,))],
            pendente=n.codigo_do_alerta(_URINAR.id, 1, (2,)),
        )
    )
    assert whatsapp2.enviados[-1] == TEXTO_OFERTA_EQUIPE and "192" not in whatsapp2.enviados[-1]
    assert final2["coleta_pendente"] == PENDENTE_OFERTA_EQUIPE and not _starts(cib2)


async def test_leitura_que_falha_pede_para_repetir_e_guarda_as_perguntas() -> None:
    # Teste do dono (26/10/2026): JSON invalido duas vezes e' falha TECNICA, nao "sem clareza" da pessoa.
    from maezo.agents.helena.graph import RESPOSTA_PECA_REENVIO

    modelo = _Modelo([decisao_v7("sintoma", responde=True)], leitura=["", ""])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(
        _estado("hmm", [n.texto_das_perguntas(_URINAR)], pendente=n.codigo_do_alerta(_URINAR.id))
    )
    assert whatsapp.enviados[-1] == RESPOSTA_PECA_REENVIO and not _starts(cib)
    assert final["coleta_pendente"] == n.codigo_do_alerta(_URINAR.id)


# --- (iv) a mesma queixa respondida nunca recebe a pergunta de alerta de novo ----------------------------
_RESPONDIDO = [n.texto_das_perguntas(_COSTAS), n.texto_sem_sinal(_COSTAS, _TEXTOS)]


@pytest.mark.parametrize(
    "fala",
    [
        "eu dormi de mau jeito, será que é isso?",
        "pode ser por causa do colchão?",
        "continua doendo as costas, o que será?",
    ],
)
async def test_seguimento_da_mesma_queixa_recebe_quem_avalia_e_o_especialista(fala: str) -> None:
    modelo = _Modelo([decisao_v7("sintoma", responde=True)], escolha=[_escolha(_COSTAS.id)])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala, _RESPONDIDO))
    texto = whatsapp.enviados[-1]
    assert texto.startswith(n.TEXTO_AVALIAR_A_CAUSA) and "ortopedista" in texto
    assert "1)" not in texto and not final.get("coleta_pendente") and not _starts(cib)
    _limpo(texto)


@pytest.mark.parametrize(
    "fala", ["já te respondi que não", "eu já tinha dito que não", "de novo essa pergunta?"]
)
async def test_reclamacao_da_repeticao_pede_desculpa_sem_perguntar_de_novo(fala: str) -> None:
    modelo = _Modelo([decisao_v7("sintoma", responde=True, correcao=True)])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_estado(fala, _RESPONDIDO))
    texto = whatsapp.enviados[-1]
    assert texto.startswith(n.TEXTO_JA_RESPONDIDO) and "1)" not in texto
    assert modelo.prompts["escolha"] == []


async def test_controle_queixa_nova_recebe_as_perguntas_dela() -> None:
    modelo = _Modelo([decisao_v7("sintoma")], escolha=[_escolha(_URINAR.id)])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_estado("agora também está ardendo pra fazer xixi", _RESPONDIDO))
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(_URINAR)


# --- ITEM 4: especialista e agendamento -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("fala", "habilidade"),
    [
        ("já posso procurar um médico para agendar?", "sintoma"),
        ("consigo agendar uma consulta por aqui?", "plano"),
        ("onde eu marco a consulta?", "conversa"),
    ],
)
async def test_agendamento_depois_da_triagem_recebe_o_especialista_e_os_canais(
    fala: str, habilidade: str
) -> None:
    modelo = _Modelo([decisao_v7(habilidade)], outras=[_classify(intent="consulta_plano")] * 3)
    grafo, whatsapp, cib = _grafo(modelo)
    await grafo.ainvoke(_estado(fala, _RESPONDIDO))
    texto = whatsapp.enviados[-1]
    assert (
        "ortopedista" in texto and "aplicativo Austa Clínicas" in texto and "central de atendimento" in texto
    )
    assert ("por aqui" in fala) is texto.startswith(n.TEXTO_NAO_MARCO)
    _limpo(texto)
    assert not _starts(cib) and modelo.prompts["escolha"] == []


@pytest.mark.parametrize(
    ("fala", "queixa", "especialista"),
    [
        ("que médico eu procuro para enxaqueca?", "esp-queixa-enxaqueca", "neurologista"),
        ("qual especialista cuida de labirintite?", "esp-queixa-tontura-vertigem", "otorrinolaringologista"),
        ("que médico trata dor de cabeça que não passa?", "esp-queixa-dor-de-cabeca", "neurologista"),
    ],
)
async def test_que_medico_procuro_usa_o_mapa(fala: str, queixa: str, especialista: str) -> None:
    modelo = _Modelo([decisao_v7("saude")], mapa=[_mapa(queixa)])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_estado(fala))
    texto = whatsapp.enviados[-1]
    assert especialista in texto and "clínico geral" in texto and "aplicativo Austa Clínicas" in texto
    assert texto.rstrip().endswith("ligue 192.")
    _limpo(texto)


@pytest.mark.parametrize("publico", ["crianca", "gestante"])
async def test_especialista_de_crianca_e_gestante_usa_a_porta_de_entrada(publico: str) -> None:
    modelo = _Modelo([decisao_v7("saude")], mapa=[_mapa("esp-queixa-enxaqueca", publico)])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_estado("que médico eu procuro pra isso?"))
    assert ("pediatra" if publico == "crianca" else "obstetra") in whatsapp.enviados[-1]


async def test_controle_duvida_geral_sem_pergunta_de_medico_nao_usa_o_mapa() -> None:
    modelo = _Modelo([decisao_v7("saude")], outras=[_classify(intent="information")] * 3)
    grafo, _, _ = _grafo(modelo)
    await grafo.ainvoke(_estado("o que é cistite?"))
    assert modelo.prompts["mapa"] == [] and modelo.prompts["escolha"] == []


# --- A chave ----------------------------------------------------------------------------------------------
async def test_chave_desligada_e_a_helena_de_antes() -> None:
    modelo = _Modelo([decisao_v7("sintoma")])
    grafo, whatsapp, _ = _grafo(modelo, protocolos=False)
    await grafo.ainvoke(_estado("estou com dor nas costas desde ontem"))
    assert modelo.prompts["escolha"] == [] and "sim ou não" in whatsapp.enviados[-1].lower()


async def test_chave_do_ambiente_desliga(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(p.CHAVE_DO_AMBIENTE, "0")
    modelo = _Modelo([decisao_v7("sintoma")])
    grafo, _, _ = _grafo(modelo)
    await grafo.ainvoke(_estado("estou com dor nas costas desde ontem"))
    assert modelo.prompts["escolha"] == []


# --- Conversas do dono (parafraseadas) ------------------------------------------------------------------
async def _turnos(passos: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    """Roda os turnos em sequencia, levando o historico curto e a pendencia de um turno ao outro."""
    falas: list[str] = []
    pendente: str | None = None
    saidas: list[tuple[str, dict[str, Any]]] = []
    for fala, filas in passos:
        modelo = _Modelo(**filas)
        grafo, whatsapp, cib = _grafo(modelo)
        final = await grafo.ainvoke(_estado(fala, list(falas), pendente))
        assert not _starts(cib), fala
        texto = whatsapp.enviados[-1]
        falas.append(texto)
        pendente = final.get("coleta_pendente")
        saidas.append((texto, final))
    return saidas


async def test_conversa_do_dono_25_10_manha_queda_da_escada_e_dor_nas_costas() -> None:
    saidas = await _turnos(
        [
            (
                "caí da escada uns dias atrás e agora minhas costas doem",
                {"roteador": [decisao_v7("sintoma")], "escolha": [_escolha(_QUEDA.id)]},
            ),
            (
                "não",
                {
                    "roteador": [decisao_v7("sintoma", responde=True, sim_nao="nao")],
                    "leitura": [_leitura(*("nao",) * len(_QUEDA.perguntas_de_alerta))],
                },
            ),
            ("e já posso procurar um médico pra marcar?", {"roteador": [decisao_v7("sintoma")]}),
            (
                "eu já te falei que não tenho nada daquilo",
                {"roteador": [decisao_v7("sintoma", correcao=True)]},
            ),
        ]
    )
    perguntas, final, agendar, repeticao = (s[0] for s in saidas)
    assert perguntas == n.texto_das_perguntas(_QUEDA) and "peito" not in perguntas.lower()
    assert final == n.texto_sem_sinal(_QUEDA, _TEXTOS)
    assert "ortopedista" in agendar and "1)" not in agendar
    assert repeticao.startswith(n.TEXTO_JA_RESPONDIDO) and "1)" not in repeticao
    for texto, estado in saidas:
        _limpo(texto)
        assert not _escalou(estado)


async def test_conversa_do_dono_25_10_noite_dificuldade_para_urinar() -> None:
    saidas = await _turnos(
        [
            (
                "estou com dificuldades ao urinar",
                {"roteador": [decisao_v7("sintoma")], "escolha": [_escolha(_URINAR.id)]},
            ),
            (
                "não, mas o que isso tem a ver com o meu xixi?",
                {
                    "roteador": [decisao_v7("sintoma", responde=True, sim_nao="nao")],
                    "leitura": [_leitura("nao", "nao", "nao", "nao")],
                },
            ),
            (
                "consigo agendar uma consulta por aqui?",
                {"roteador": [decisao_v7("plano")], "outras": [_classify(intent="consulta_plano")] * 3},
            ),
        ]
    )
    perguntas, final, agendar = (s[0] for s in saidas)
    assert perguntas == n.texto_das_perguntas(_URINAR)
    assert "peito" not in perguntas.lower() and "fala enrolada" not in perguntas.lower()
    assert "urologista" in final
    assert agendar.startswith(n.TEXTO_NAO_MARCO) and "urologista" in agendar
    for texto, estado in saidas:
        _limpo(texto)
        assert not _escalou(estado)


async def test_conversa_do_dono_22_10_dor_nas_costas() -> None:
    saidas = await _turnos(
        [
            (
                "to com uma dor nas costas faz uns dias",
                {"roteador": [decisao_v7("sintoma")], "escolha": [_escolha(_COSTAS.id)]},
            ),
            (
                "nada disso",
                {
                    "roteador": [decisao_v7("sintoma", responde=True, sim_nao="nao")],
                    "leitura": [_leitura(*("nao",) * len(_COSTAS.perguntas_de_alerta))],
                },
            ),
            (
                "dormi de mau jeito, será que é isso?",
                {"roteador": [decisao_v7("sintoma", responde=True)], "escolha": [_escolha(_COSTAS.id)]},
            ),
        ]
    )
    perguntas, final, seguimento = (s[0] for s in saidas)
    assert perguntas == n.texto_das_perguntas(_COSTAS)
    assert final == n.texto_sem_sinal(_COSTAS, _TEXTOS)
    assert seguimento.startswith(n.TEXTO_AVALIAR_A_CAUSA) and "1)" not in seguimento
    for texto, estado in saidas:
        _limpo(texto)
        assert not _escalou(estado)


# --- Simulador de 26/10/2026: nunca perguntar o que a pessoa acabou de contar; queda/trauma tem prioridade --
def _pre(*respostas: str) -> str:
    return json.dumps({"respostas": list(respostas)})


@pytest.mark.parametrize(
    "fala",
    [
        "Bom dia, eu caí de uma escada uns dias atrás e estou com dor nas costas",
        "levei um tombo e o joelho dói",
        "escorreguei e bati as costas no chão",
        "bati o carro e estou com dor no pescoço",
        "torci o pé e está inchado",
    ],
)
async def test_queda_ou_trauma_escolhido_pelo_modelo_faz_as_perguntas_de_trauma(fala: str) -> None:
    """A prioridade da queda/trauma e' do MODELO (regra 5 do prompt; sonda de 26/10 com o zai.glm-5: 48/48
    nas 3 rodadas), sem troca por palavra no codigo."""
    modelo = _Modelo([decisao_v7("sintoma")], escolha=[_escolha(_QUEDA.id)])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala))
    texto = whatsapp.enviados[-1]
    assert texto == n.texto_das_perguntas(_QUEDA)
    assert "A dor veio depois de uma queda" not in texto
    assert final["coleta_pendente"] == n.codigo_do_alerta(_QUEDA.id) and not _starts(cib)
    assert "QUEDA/TRAUMA" in modelo.prompts["escolha"][0]


@pytest.mark.parametrize(
    ("fala", "escolhido"),
    [
        ("bati papo com a vizinha e depois fiquei com dor de cabeça", "tri-adulto-dor-de-cabeca"),
        ("meu pai sofreu um acidente ano passado e eu estou com dor nas costas", _COSTAS.id),
        ("minha mãe caiu semana passada, eu estou com dor de garganta", "tri-adulto-dor-de-garganta"),
    ],
)
async def test_palavra_de_trauma_nao_troca_a_escolha_do_modelo(fala: str, escolhido: str) -> None:
    modelo = _Modelo([decisao_v7("sintoma")], escolha=[_escolha(escolhido)])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_estado(fala))
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(_CATALOGO.protocolos[escolhido])


@pytest.mark.parametrize(
    "fala", ["estou com dor nas costas há 3 dias", "costas doendo desde segunda", "não caí, só dói"]
)
async def test_controle_dor_nas_costas_sem_trauma_continua_com_as_perguntas_dela(fala: str) -> None:
    modelo = _Modelo([decisao_v7("sintoma")], escolha=[_escolha(_COSTAS.id)])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_estado(fala))
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(_COSTAS)


@pytest.mark.parametrize(
    ("fala", "pre", "faltam"),
    [
        (
            "caí da escada e não bati a cabeça, as costas doem",
            ("nao_dito", "nao_dito", "nao", "nao_dito"),
            (1, 2, 4),
        ),
        ("levei um tombo, não desmaiei e não bati a cabeça", ("nao", "nao_dito", "nao", "nao_dito"), (2, 4)),
        ("escorreguei, consigo mexer tudo, só dói", ("nao_dito", "nao_dito", "nao_dito", "nao"), (1, 2, 3)),
    ],
)
async def test_pergunta_ja_respondida_sai_e_so_as_outras_sao_feitas(
    fala: str, pre: tuple[str, ...], faltam: tuple[int, ...]
) -> None:
    modelo = _Modelo([decisao_v7("sintoma")], escolha=[_escolha(_QUEDA.id)], pre=[_pre(*pre)])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala))
    texto = whatsapp.enviados[-1]
    assert texto == n.texto_das_perguntas(_QUEDA, faltam, primeira=True)
    assert texto.startswith(n.cabecalho(_QUEDA))
    assert final["coleta_pendente"] == n.codigo_do_alerta(_QUEDA.id, 0, faltam) and not _starts(cib)
    # A resposta seguinte le' SO' as perguntas que faltavam, e "nao" leva a orientacao sem 192.
    modelo2 = _Modelo([decisao_v7("sintoma", responde=True)], leitura=[_leitura(*("nao",) * len(faltam))])
    grafo2, whatsapp2, cib2 = _grafo(modelo2)
    final2 = await grafo2.ainvoke(_estado("não", [texto], pendente=final["coleta_pendente"]))
    assert whatsapp2.enviados[-1] == n.texto_sem_sinal(_QUEDA, _TEXTOS)
    assert not _escalou(final2) and not _starts(cib2)


@pytest.mark.parametrize(
    ("fala", "pre"),
    [
        ("caí da escada e agora não consigo apoiar a perna", ("nao_dito", "nao_dito", "nao_dito", "sim")),
        ("levei um tombo, bati a cabeça e estou com enjoo", ("nao_dito", "nao_dito", "sim", "nao_dito")),
        ("tropecei e o braço ficou torto", ("nao", "nao", "nao", "sim")),
    ],
)
async def test_sinal_urgente_ja_dito_vai_direto_ao_destino(fala: str, pre: tuple[str, ...]) -> None:
    modelo = _Modelo([decisao_v7("sintoma")], escolha=[_escolha(_QUEDA.id)], pre=[_pre(*pre)])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala))
    assert whatsapp.enviados[-1].startswith(n.texto_do_destino_por_sinal("PRONTO_SOCORRO_AGORA", "adulto"))
    assert "ortopedista" in whatsapp.enviados[-1] or "clínico geral" in whatsapp.enviados[-1]
    assert not final.get("coleta_pendente") and not _starts(cib)


@pytest.mark.parametrize(
    "fala",
    [
        "caí agora há pouco e fiquei muito confuso",
        "levei um tombo e estou meio sonolento",
        "bati o carro e estou confuso",
    ],
)
async def test_sinal_de_192_ja_dito_escala_na_hora(fala: str) -> None:
    modelo = _Modelo(
        [decisao_v7("sintoma")],
        escolha=[_escolha(_QUEDA.id)],
        pre=[_pre("sim", "nao_dito", "nao_dito", "nao_dito")],
    )
    grafo, _, _ = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala))
    assert final["escalation_motivo"] == "red_flag_clinico"


@pytest.mark.parametrize(
    "fala",
    [
        "caí da escada, não bati a cabeça, não desmaiei, mexo tudo e a dor é leve",
        "levei um tombo mas está tudo bem, sem nada disso, só uma dorzinha",
        "escorreguei; nada de desmaio, sangue, cabeça ou inchaço",
    ],
)
async def test_tudo_ja_respondido_sem_sim_vai_ao_destino_sem_sinal(fala: str) -> None:
    modelo = _Modelo(
        [decisao_v7("sintoma")], escolha=[_escolha(_QUEDA.id)], pre=[_pre("nao", "nao", "nao", "nao")]
    )
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala))
    assert whatsapp.enviados[-1] == n.texto_sem_sinal(_QUEDA, _TEXTOS)
    assert not _escalou(final) and not _starts(cib)


@pytest.mark.parametrize(
    "fala",
    ["será que se eu cair da escada vou ficar confuso?", "e se eu bater a cabeça?", "pode dar tontura?"],
)
async def test_sim_so_conta_em_frase_afirmada(fala: str) -> None:
    modelo = _Modelo(
        [decisao_v7("sintoma")],
        escolha=[_escolha(_QUEDA.id)],
        pre=[_pre("sim", "nao_dito", "sim", "nao_dito")],
    )
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala))
    assert not _escalou(final) and not _starts(cib)
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(_QUEDA)


def test_plano_das_perguntas_regra() -> None:
    primeiro = n.plano_das_perguntas(_COSTAS, ("sim", "nao_dito", "nao_dito", "nao_dito"))
    assert primeiro.decisao == n.Decisao(
        "destino", "PRONTO_SOCORRO_AGORA", _COSTAS.perguntas_de_alerta[0].sinal
    )
    assert n.plano_das_perguntas(_COSTAS, ("nao", "nao_dito", "nao", "nao_dito")).faltam == (2, 4)
    assert n.plano_das_perguntas(_COSTAS, ("nao",) * 4).decisao == n.Decisao("destino", "CONSULTA_ROTINA")
