"""Rodada de 26/10/2026 na imagem 7ac0219e (2 de 3 execucoes do roteiro do dono reprovadas):

1. "acabei torcendo o tornozelo" -> a pre-leitura marcou "nao consegue apoiar?" como "sim" e a pessoa foi
   mandada ao pronto-socorro sem nenhuma pergunta;
2. sem perguntas na conversa, a conferencia "o certo nao seria ortopedista?" recebeu "dor no pescoco";
3. com a re-pergunta pendente, a pergunta nova do PSA foi lida como a resposta (e recebeu o destino da
   crianca); e "ela ja' tomou dipirona, quero procurar um medico" virou "sem clareza".
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena.habilidades.protocolo import evidencia as ev
from maezo.agents.helena.habilidades.protocolo import nucleo as n
from maezo.conhecimento import protocolos as p
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_protocolo_especialista import _com_historico
from tests.unit.agents.test_helena_protocolos import (
    _CATALOGO,
    _QUEDA,
    _escolha,
    _grafo,
    _mapa,
    _Modelo,
)
from tests.unit.agents.test_helena_rodada_3_simulador import _entrada, _starts

_TORCAO = "Ontem eu jogava futebol e acabei torcendo o tornozelo."
_CRIANCA = _CATALOGO.protocolos[p.UNIVERSAL_CRIANCA]


def _pre(*respostas: str) -> str:
    import json

    return json.dumps({"respostas": list(respostas)})


# --- 1. O "sim" da pre-leitura so' com o sinal dito ---------------------------------------------------------
async def test_torci_o_tornozelo_faz_as_perguntas_mesmo_com_sim_da_pre_leitura() -> None:
    modelo = _Modelo(
        [decisao_v7("sintoma")],
        escolha=[_escolha(_QUEDA.id)],
        pre=[_pre("nao_dito", "nao_dito", "nao_dito", "sim")],
    )
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_entrada(_TORCAO))
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(_QUEDA)
    assert str(final.get("coleta_pendente")).startswith(n.PENDENTE_ALERTA) and not _starts(cib)
    assert "pronto-socorro" not in whatsapp.enviados[-1]


@pytest.mark.parametrize(
    ("fala", "indice"),
    [
        ("torci o tornozelo e não consigo apoiar o pé", 4),
        ("torci o pé e está muito inchado", 4),
        ("caí e o braço está torto", 4),
        ("caí agora há pouco e fiquei muito confuso", 1),
        ("levei um tombo, bati a cabeça e estou com enjoo", 3),
    ],
)
def test_controle_sinal_dito_mantem_o_sim(fala: str, indice: int) -> None:
    pre = tuple("sim" if i == indice else "nao_dito" for i in range(1, 5))
    assert ev.so_sim_com_sinal_dito(pre, _QUEDA, [fala]) == pre


@pytest.mark.parametrize("fala", [_TORCAO, "torci o pé jogando bola", "torci o braço ontem"])
def test_so_o_evento_nao_mantem_o_sim(fala: str) -> None:
    pre = ("nao_dito", "nao_dito", "nao_dito", "sim")
    assert ev.so_sim_com_sinal_dito(pre, _QUEDA, [fala]) == ("nao_dito",) * 4


# --- 2. A conferencia sem perguntas na conversa: a ultima fala que descreveu o problema ------------------
async def test_sugestao_sem_perguntas_na_conversa_le_a_ultima_queixa() -> None:
    conversa = [
        (_TORCAO, n.texto_do_destino_por_sinal("PRONTO_SOCORRO_AGORA", "adulto")),
        (
            "nenhuma. E achei essas perguntas sem nada a ver com o que eu contei.",
            "Este canal não faz diagnóstico nem avalia gravidade.",
        ),
    ]
    fala = "Você não acha que o certo seria eu me consultar com um ortopedista ou um fisioterapeuta?"
    modelo = _Modelo([decisao_v7("saude")], mapa=[_mapa("esp-queixa-pe-tornozelo")])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_com_historico(fala, conversa))
    assert len(modelo.prompts["mapa"]) == 1
    prompt = modelo.prompts["mapa"][0]
    assert "torcendo o tornozelo" in prompt.split("MENSAGEM NOVA:", 1)[1]
    assert prompt.split("CONVERSA:", 1)[1].split("MENSAGEM NOVA:", 1)[0].strip() == "(vazia)"
    assert "tornozelo" in whatsapp.enviados[-1]


def test_ultima_queixa_pula_respostas_e_assuntos_sem_queixa() -> None:
    falas = [
        _TORCAO,
        "nenhuma. E achei essas perguntas sem nada a ver com o que eu contei.",
        "Certo. Queria falar também de uma coisa com um dos meus dependentes.",
    ]
    assert ev.ultima_queixa(falas, _CATALOGO) == _TORCAO
    assert ev.ultima_queixa(["Não", "Poxa, eu já respondi não para todas"], _CATALOGO) == ""


# --- 3. Pergunta nova com a re-pergunta pendente ---------------------------------------------------------
async def test_pergunta_nova_na_repergunta_nao_e_lida_como_resposta() -> None:
    repergunta = n.codigo_do_alerta(_CRIANCA.id, 1, (1,))
    fala = "Um homem de 50 anos com o exame de PSA alterado deve procurar qual tipo de profissional?"
    modelo = _Modelo([decisao_v7("saude")], mapa=[_mapa("esp-queixa-prostata")])
    grafo, whatsapp, cib = _grafo(modelo)
    final: dict[str, Any] = await grafo.ainvoke(
        _com_historico(fala, [("Ela já tomou dipirona", n.texto_das_perguntas(_CRIANCA, (1,)))], repergunta)
    )
    texto = whatsapp.enviados[-1]
    assert "urolog" in texto and "criança" not in texto
    assert modelo.prompts["leitura"] == [] and not _starts(cib)
    assert not str(final.get("coleta_pendente") or "").startswith(n.PENDENTE_ALERTA)


async def test_controle_resposta_a_repergunta_continua_lida() -> None:
    repergunta = n.codigo_do_alerta(_CRIANCA.id, 1, (1,))
    modelo = _Modelo([decisao_v7("sintoma", responde=True, sim_nao="nao")])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(
        _com_historico("não", [("Ela já tomou dipirona", n.texto_das_perguntas(_CRIANCA, (1,)))], repergunta)
    )
    assert "pediatra" in whatsapp.enviados[-1]


def test_o_prompt_da_leitura_traz_o_exemplo_da_dipirona() -> None:
    prompt = n.prompt_da_leitura(_CRIANCA, (1, 2, 3, 4), bloco_conversa="", bloco_mensagem="x")
    assert "dipirona" in prompt and "nenhum sinal, quer um medico" in prompt


def test_o_prompt_da_pre_leitura_diz_que_a_torcao_nao_e_o_sinal() -> None:
    prompt = n.prompt_da_pre_leitura(_QUEDA, bloco_conversa="", bloco_mensagem="x")
    assert "'torci o tornozelo' NAO responde" in prompt
