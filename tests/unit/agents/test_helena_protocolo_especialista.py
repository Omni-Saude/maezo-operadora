"""QUAL ESPECIALISTA procurar (teste do dono de 26/10/2026), pelo grafo COMPILADO com o modelo falso.

No teste do dono, a pergunta de qual profissional procurar recebeu a navegacao antiga ("Eu nao avalio
sintomas, entao nao consigo dizer se voce precisa de medico...") e a sugestao "o correto nao seria um
ortopedista?" recebeu o texto da v4 ("Consulta com X e' consulta de especialista. Por aqui eu nao pesquiso
especialistas...").
Agora: o especialista do MAPA do protocolo e os canais, sem prazo da ANS; com relato de queixa, as perguntas
do protocolo vem antes e o especialista do mapa vai na resposta final; a sugestao da pessoa e' aceita so'
quando o mapa a admite. As falas do dono entram PARAFRASEADAS, sem nenhum dado pessoal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.agents.helena.habilidades.protocolo import especialista as e
from maezo.agents.helena.habilidades.protocolo import nucleo as n
from maezo.agents.helena.habilidades.protocolo import sinais
from maezo.conhecimento import protocolos as p
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_protocolos import (
    _CATALOGO,
    _QUEDA,
    _TEXTOS,
    _UNIVERSAL,
    _escolha,
    _grafo,
    _leitura,
    _limpo,
    _mapa,
    _Modelo,
)
from tests.unit.agents.test_helena_rodada_3_simulador import _entrada, _starts

_PALPITACOES = _CATALOGO.protocolos["tri-adulto-palpitacoes"]
#: Os textos antigos que NUNCA podem sair quando a pessoa pergunta qual profissional procurar.
_TEXTOS_ANTIGOS = ("Eu não avalio sintomas", "não pesquiso especialistas", "Consulta com ")
_CANAIS = ("aplicativo Austa Clínicas", "portal do plano", "central de atendimento")


def _com_historico(fala: str, pares: list[tuple[str, str]], pendente: str | None = None) -> dict[str, Any]:
    """A conversa com as falas REAIS da pessoa e da Helena (pares pessoa -> Helena), mais antigas primeiro."""
    estado = _entrada(fala)
    agora = datetime.now(UTC).isoformat()
    historico: list[dict[str, str]] = []
    for da_pessoa, da_helena in pares:
        historico.append({"papel": "beneficiario", "texto": da_pessoa, "em": agora})
        historico.append({"papel": "helena", "texto": da_helena, "em": agora})
    if historico:
        estado["historico_conversa"] = historico
        estado["ultima_mensagem_em"] = agora
    if pendente:
        estado |= {"coleta_pendente": pendente, "coleta_contexto": ""}
    return estado


def _pre_leitura(*respostas: str) -> str:
    import json

    return json.dumps({"respostas": list(respostas)})


def _sem_texto_antigo(texto: str) -> None:
    for antigo in _TEXTOS_ANTIGOS:
        assert antigo not in texto, antigo
    for canal in _CANAIS:
        assert canal in texto, canal
    _limpo(texto)


# --- Detectores (puros) -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fala",
    [
        "Sinto o coração acelerado e me canso à toa, que tipo de profissional eu devo procurar?",
        "Para um senhor de 50 anos com PSA alterado, ele deve se consultar com qual tipo de profissional?",
        "com quem eu devo me consultar por causa disso?",
        "quero procurar um médico para atender minha filha",
        "qual especialista cuida de ondas de calor?",
    ],
)
def test_pergunta_de_qual_profissional(fala: str) -> None:
    assert e.pergunta_qual_especialista(fala)


@pytest.mark.parametrize(
    "fala",
    [
        "consigo agendar uma consulta com qual médico por aqui?",
        "quero marcar com um cardiologista",
        "o que é cistite?",
        "onde fica o pronto-socorro mais perto?",
    ],
)
def test_controle_marcar_ou_outra_pergunta_nao_e_qual_profissional(fala: str) -> None:
    assert not e.pergunta_qual_especialista(fala)


@pytest.mark.parametrize(
    ("fala", "citados"),
    [
        ("o certo não seria procurar um ortopedista?", ["o ortopedista"]),
        ("não seria melhor uma reumatologista?", ["o reumatologista"]),
        ("você não acha que seria ortopedista ou fisioterapeuta?", ["o ortopedista", "o fisioterapeuta"]),
        ("O ideal não era com um reumatologista ou ortopedista?", ["o reumatologista", "o ortopedista"]),
    ],
)
def test_sugestao_de_especialista(fala: str, citados: list[str]) -> None:
    assert e.sugere_especialista(fala, _TEXTOS) and e.sugeridos(fala, _TEXTOS) == citados


@pytest.mark.parametrize(
    "fala",
    [
        "quero marcar com um ortopedista, seria possível?",
        "onde tem dermatologista pelo plano?",
        "preciso consultar com um cardiologista",
        "o correto não seria eu descansar mais?",
    ],
)
def test_controle_nao_e_sugestao_de_especialista(fala: str) -> None:
    assert not e.sugere_especialista(fala, _TEXTOS)


@pytest.mark.parametrize(
    ("fala", "relata"),
    [
        ("Estou sentindo palpitações, que médico procuro?", True),
        ("ela está com febre desde ontem, qual médico?", True),
        ("ando tendo dor nas juntas, com quem devo me consultar?", True),
        ("que médico trata dor de cabeça que não passa?", False),
        ("para um homem com PSA alterado, qual profissional?", False),
    ],
)
def test_relato_de_queixa(fala: str, relata: bool) -> None:
    assert e.relata_queixa(fala) is relata


# --- Item 5: a pergunta de qual profissional, sem relato -> o mapa direto -------------------------------
@pytest.mark.parametrize(
    ("fala", "queixa", "especialista", "habilidade"),
    [
        (
            "Um homem de 50 anos com o exame de PSA alterado deve procurar qual tipo de profissional?",
            "esp-queixa-prostata",
            "urologista",
            "saude",
        ),
        (
            "Tenho muita dificuldade para perder peso, parece que meu metabolismo ficou lento. Que tipo de "
            "profissional devo procurar?",
            "esp-queixa-obesidade",
            "endocrinologista",
            "saude",
        ),
        (
            "Qual tipo de médico cuida de menstruação irregular com calorões?",
            "esp-queixa-menstruacao",
            "ginecologista",
            "conversa",
        ),
        (
            "que tipo de profissional eu devo procurar para dor no tornozelo depois de uma torção?",
            "esp-queixa-pe-tornozelo",
            "ortopedista",
            "sintoma",
        ),
    ],
)
async def test_qual_profissional_sem_relato_recebe_o_mapa_e_os_canais(
    fala: str, queixa: str, especialista: str, habilidade: str
) -> None:
    modelo = _Modelo([decisao_v7(habilidade)], mapa=[_mapa(queixa)])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_entrada(fala))
    texto = whatsapp.enviados[-1]
    assert especialista in texto and "clínico geral" in texto
    assert texto.rstrip().endswith("ligue 192.") and "1)" not in texto
    _sem_texto_antigo(texto)
    assert modelo.prompts["escolha"] == [] and not final.get("coleta_pendente") and not _starts(cib)


# --- Item 5 (dono, 27/10/2026, opcao A): QUAL profissional = resposta direta, sem perguntas -------------
@pytest.mark.parametrize(
    ("fala", "protocolo", "queixa", "especialista"),
    [
        (
            "Estou sentindo o coração disparar e me canso com pouco esforço, que tipo de profissional eu "
            "devo procurar?",
            _PALPITACOES,
            "esp-queixa-palpitacao",
            "cardiologista",
        ),
        (
            "Ando sentindo palpitação e falta de fôlego leve ao subir escada, com quem devo me consultar?",
            _PALPITACOES,
            "esp-queixa-palpitacao",
            "cardiologista",
        ),
        (
            "Estou sentindo dores nas juntas e fraqueza nos músculos no dia a dia, que profissional devo "
            "procurar?",
            _UNIVERSAL,
            "esp-queixa-varias-articulacoes",
            "reumatologista",
        ),
    ],
)
async def test_qual_profissional_com_relato_responde_direto_com_o_mapa_e_os_sinais(
    fala: str, protocolo: Any, queixa: str, especialista: str
) -> None:
    modelo = _Modelo(
        [decisao_v7("sintoma")],
        escolha=[_escolha(protocolo.id)],
        pre=[_pre_leitura(*("nao_dito",) * len(protocolo.perguntas_de_alerta))],
        mapa=[_mapa(queixa)],
    )
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_entrada(fala))
    texto = whatsapp.enviados[-1]
    assert especialista in texto and "clínico geral" in texto and "aplicativo Austa Clínicas" in texto
    assert "me diga se acontece" not in texto and "1)" not in texto
    assert texto.rstrip().endswith(sinais.linha(protocolo, "adulto"))
    _sem_texto_antigo(texto)
    assert not final.get("coleta_pendente") and not final.get("escalation_motivo") and not _starts(cib)


async def test_qual_profissional_com_sinal_afirmado_vai_ao_protocolo() -> None:
    """So' com um sinal AFIRMADO na propria mensagem o protocolo decide (aqui: o destino na hora)."""
    fala = "Estou com o coração disparado e desmaiei agora há pouco, que tipo de profissional devo procurar?"
    modelo = _Modelo(
        [decisao_v7("sintoma")],
        escolha=[_escolha(_PALPITACOES.id)],
        pre=[_pre_leitura("nao_dito", "sim", "nao_dito", "nao_dito")],
    )
    grafo, _, _ = _grafo(modelo)
    final = await grafo.ainvoke(_entrada(fala))
    assert final.get("escalation_motivo") == "red_flag_clinico"


async def test_dor_no_peito_qual_profissional_diz_avaliar_hoje_junto_do_cardiologista() -> None:
    """A TABELA decide: dor no peito sem sinal e' atendimento hoje, junto do especialista."""
    peito = _CATALOGO.protocolos["tri-adulto-dor-no-peito"]
    fala = (
        "Estou sentindo dores leves no peito ou tonturas ao fazer exercícios, que tipo de profissional eu "
        "devo procurar?"
    )
    modelo = _Modelo(
        [decisao_v7("sintoma")],
        escolha=[_escolha(peito.id)],
        pre=[_pre_leitura("nao_dito", "nao_dito", "nao_dito", "nao_dito")],
        mapa=[_mapa("esp-queixa-dor-no-peito-nao-aguda")],
    )
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_entrada(fala))
    texto = whatsapp.enviados[-1]
    assert "ainda hoje" in texto and "cardiologista" in texto and "suor frio" in texto
    assert "1)" not in texto and not final.get("escalation_motivo") and not _starts(cib)


async def test_controle_sem_pergunta_de_profissional_a_resposta_final_e_a_do_protocolo() -> None:
    """Queixa SEM pergunta de qual profissional: a resposta final de sempre (sem chamar o mapa)."""
    fala = "estou sentindo o coração disparado desde cedo"
    perguntas = n.texto_das_perguntas(_PALPITACOES)
    k = len(_PALPITACOES.perguntas_de_alerta)
    modelo = _Modelo([decisao_v7("sintoma", responde=True, sim_nao="nao")], leitura=[_leitura(*("nao",) * k)])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_com_historico("nenhuma", [(fala, perguntas)], n.codigo_do_alerta(_PALPITACOES.id)))
    assert whatsapp.enviados[-1] == n.texto_sem_sinal(_PALPITACOES, _TEXTOS)
    assert modelo.prompts["mapa"] == []


@pytest.mark.parametrize(
    "fala",
    [
        "Estou com o ciclo menstrual irregular e ondas de calor de repente, que tipo de profissional devo "
        "procurar?",
        "Estou tendo calorões e a menstruação está desregulada, qual médico eu procuro?",
        "Ando sentindo ondas de calor e a menstruação atrasando, com quem devo me consultar?",
    ],
)
async def test_relato_sem_protocolo_proprio_recebe_o_mapa_direto(fala: str) -> None:
    modelo = _Modelo(
        [decisao_v7("sintoma")], escolha=[_escolha("nenhum")], mapa=[_mapa("esp-queixa-menstruacao")]
    )
    grafo, whatsapp, _ = _grafo(modelo)
    final = await grafo.ainvoke(_entrada(fala))
    texto = whatsapp.enviados[-1]
    assert "ginecologista" in texto and "1)" not in texto and not final.get("coleta_pendente")
    assert "tive um problema" not in texto.lower()
    _sem_texto_antigo(texto)


async def test_relato_com_escolha_que_falha_responde_direto_com_os_sinais_universais() -> None:
    """Sem a escolha do modelo e sem sinal afirmado: a resposta direta com a linha do alerta universal."""
    modelo = _Modelo([decisao_v7("sintoma")], escolha=["", ""], mapa=["", ""])
    grafo, whatsapp, _ = _grafo(modelo)
    final = await grafo.ainvoke(_entrada("Estou sentindo uma pressão estranha, que profissional procuro?"))
    texto = whatsapp.enviados[-1]
    assert texto.rstrip().endswith(sinais.LINHAS[p.UNIVERSAL_ADULTO]) and "clínico geral" in texto
    assert not final.get("coleta_pendente")


# --- Item 6: a sugestao de especialista, pela queixa da conversa ----------------------------------------
_TORCAO = "Ontem no futebol eu torci o tornozelo"
_JUNTAS = "Ando com dor nas juntas e fraqueza nos músculos, que profissional procuro?"
_CONVERSA_DA_TORCAO = [
    (_TORCAO, n.texto_das_perguntas(_QUEDA)),
    ("nenhuma", n.texto_sem_sinal(_QUEDA, _TEXTOS)),
]
_CONVERSA_DAS_JUNTAS = [
    (_JUNTAS, n.texto_das_perguntas(_UNIVERSAL)),
    ("nenhuma", n.texto_sem_sinal(_UNIVERSAL, _TEXTOS)),
]


@pytest.mark.parametrize(
    ("fala", "conversa", "queixa", "aceitos", "outros"),
    [
        (
            "O correto não seria eu me consultar com um ortopedista?",
            _CONVERSA_DA_TORCAO,
            "esp-queixa-pe-tornozelo",
            ["ortopedista"],
            [],
        ),
        (
            "Você não acha que o certo seria ortopedista ou fisioterapeuta?",
            _CONVERSA_DA_TORCAO,
            "esp-queixa-pe-tornozelo",
            ["ortopedista"],
            ["fisioterapeuta"],
        ),
        (
            "não seria melhor um reumatologista?",
            _CONVERSA_DAS_JUNTAS,
            "esp-queixa-varias-articulacoes",
            ["reumatologista"],
            [],
        ),
        (
            "O correto não seria com um reumatologista ou ortopedista?",
            _CONVERSA_DAS_JUNTAS,
            "esp-queixa-varias-articulacoes",
            ["reumatologista"],
            ["ortopedista"],
        ),
    ],
)
@pytest.mark.parametrize("habilidade", ["saude", "sintoma", "conversa"])
async def test_sugestao_recebe_o_especialista_do_mapa_e_aceita_so_o_que_o_mapa_admite(
    fala: str,
    conversa: list[tuple[str, str]],
    queixa: str,
    aceitos: list[str],
    outros: list[str],
    habilidade: str,
) -> None:
    modelo = _Modelo([decisao_v7(habilidade)], mapa=[_mapa(queixa)])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_com_historico(fala, conversa))
    texto = whatsapp.enviados[-1]
    assert texto.startswith("Sim, ") and all(a in texto.split(".")[0] for a in aceitos)
    for outro in outros:
        assert f"Sobre o {outro}: se for preciso, o médico indica na consulta." in texto
    assert "clínico geral" in texto and "1)" not in texto
    _sem_texto_antigo(texto)
    assert modelo.prompts["escolha"] == [] and not final.get("coleta_pendente") and not _starts(cib)


@pytest.mark.parametrize(
    "fala",
    [
        "não seria melhor um cardiologista?",
        "o correto não seria procurar um dermatologista?",
        "você não acha que seria um neurologista?",
    ],
)
async def test_controle_sugestao_fora_do_mapa_nao_e_aceita(fala: str) -> None:
    modelo = _Modelo([decisao_v7("saude")], mapa=[_mapa("esp-queixa-pe-tornozelo")])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_com_historico(fala, _CONVERSA_DA_TORCAO))
    texto = whatsapp.enviados[-1]
    assert not texto.startswith("Sim") and "se for preciso, o médico indica na consulta" in texto
    assert "ortopedista" in texto
    _sem_texto_antigo(texto)


async def test_sugestao_com_o_mapa_fora_usa_o_ultimo_protocolo() -> None:
    modelo = _Modelo([decisao_v7("saude")], mapa=["", ""])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_com_historico("o correto não seria um ortopedista?", _CONVERSA_DA_TORCAO))
    texto = whatsapp.enviados[-1]
    assert texto.startswith("Sim, o ortopedista é uma boa opção") and "clínico geral" in texto
    _sem_texto_antigo(texto)


# --- O roteador fora: a mesma resposta (nunca a navegacao nem o texto da v4) ---------------------------
@pytest.mark.parametrize(
    ("fala", "conversa", "queixa", "especialista"),
    [
        (
            "Para um homem de 50 anos com PSA alterado, qual tipo de profissional ele procura?",
            [],
            "esp-queixa-prostata",
            "urologista",
        ),
        (
            "Tenho dificuldade para emagrecer, que tipo de profissional devo procurar?",
            [],
            "esp-queixa-obesidade",
            "endocrinologista",
        ),
        (
            "o correto não seria um reumatologista ou ortopedista?",
            _CONVERSA_DAS_JUNTAS,
            "esp-queixa-varias-articulacoes",
            "reumatologista",
        ),
    ],
)
async def test_roteador_fora_a_pergunta_de_profissional_segue_o_mapa(
    fala: str, conversa: list[tuple[str, str]], queixa: str, especialista: str
) -> None:
    modelo = _Modelo([], mapa=[_mapa(queixa)])
    grafo, whatsapp, cib = _grafo(modelo)
    await grafo.ainvoke(_com_historico(fala, conversa))
    texto = whatsapp.enviados[-1]
    assert especialista in texto
    _sem_texto_antigo(texto)
    assert not _starts(cib)


# --- Controles: o pedido de MARCAR continua com o agendamento de sempre --------------------------------
@pytest.mark.parametrize(
    "fala",
    [
        "consigo agendar uma consulta por aqui?",
        "quero marcar uma consulta com o ortopedista",
        "como eu marco a consulta?",
    ],
)
async def test_controle_pedido_de_marcar_fica_com_o_agendamento(fala: str) -> None:
    modelo = _Modelo([decisao_v7("plano")], outras=[""] * 3)
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_com_historico(fala, _CONVERSA_DA_TORCAO))
    texto = whatsapp.enviados[-1]
    assert not texto.startswith("Sim, ") and "aplicativo Austa Clínicas" in texto
    assert modelo.prompts["escolha"] == []


async def test_o_mapa_le_a_queixa_da_fala_que_abriu_as_perguntas_nao_a_resposta() -> None:
    """Rodada de 26/10/2026: com "poxa, eu ja' respondi nao..." como mensagem nova, o mapa escolheu "dor nas
    costas" para uma queixa de articulacoes. A queixa do mapa e' a fala que abriu as perguntas."""
    abertura = (
        "Ando sentindo dor nas articulações e fraqueza nos músculos, que tipo de profissional devo procurar?"
    )
    perguntas = n.texto_das_perguntas(_UNIVERSAL)
    resposta = "Poxa, eu já respondi não para todas as perguntas acima"
    k = len(_UNIVERSAL.perguntas_de_alerta)
    modelo = _Modelo(
        [decisao_v7("sintoma", responde=True, sim_nao="nao")],
        leitura=[_leitura(*("nao",) * k)],
        mapa=[_mapa("esp-queixa-varias-articulacoes")],
    )
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_com_historico(resposta, [(abertura, perguntas)], n.codigo_do_alerta(_UNIVERSAL.id)))
    assert len(modelo.prompts["mapa"]) == 1
    nova = modelo.prompts["mapa"][0].split("MENSAGEM NOVA:", 1)[1]
    assert "articulações" in nova and "já respondi" not in nova
    assert "reumatologista" in whatsapp.enviados[-1]


@pytest.mark.parametrize(
    ("conversa", "abertura", "fala"),
    [
        (
            _CONVERSA_DA_TORCAO,
            _TORCAO,
            "Você não acha que o certo seria um ortopedista ou um fisioterapeuta?",
        ),
        (_CONVERSA_DAS_JUNTAS, _JUNTAS, "O certo não seria com um reumatologista ou um ortopedista?"),
    ],
)
async def test_a_sugestao_le_a_queixa_so_da_fala_que_abriu_o_protocolo(
    conversa: list[tuple[str, str]], abertura: str, fala: str
) -> None:
    """Rodada de 26/10/2026: "o certo nao seria ortopedista?" depois da torcao recebeu "boa opcao para dor
    nas costas (lombar)". O mapa da sugestao recebe so' a fala que abriu o protocolo, sem a conversa."""
    modelo = _Modelo([decisao_v7("saude")], mapa=[_mapa("esp-queixa-pe-tornozelo")])
    grafo, _, _ = _grafo(modelo)
    await grafo.ainvoke(_com_historico(fala, conversa))
    assert len(modelo.prompts["mapa"]) == 1
    prompt = modelo.prompts["mapa"][0]
    nova = prompt.split("MENSAGEM NOVA:", 1)[1]
    assert abertura in nova and fala not in nova
    assert prompt.split("CONVERSA:", 1)[1].split("MENSAGEM NOVA:", 1)[0].strip() == "(vazia)"
