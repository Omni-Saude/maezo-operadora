"""Simulador da SAUDE GERATIVA (ADR-0065, DL-0119): o roteiro de aceite `helena_saude_gerativa`.

Roda no dev (task do simulador, telefones da allowlist), nunca no CI. Aqui se prova: (a) o roteiro
existe, todo padrao compila e cobre as 9 perguntas do dono, o alarme, a recusa e a conversa FORA da
allowlist; (b) respostas REAIS do Haiku 5.5 no experimento de 17/10/2026 (PR #819) passam nos passos de
utilidade, e o texto fixo da base, a resposta segura padrao e uma resposta com dose REPROVAM (o criterio
e' "resolveu?", nao "nao abriu caso"); (c) os telefones do roteiro: 9 na allowlist, o ultimo fora.
"""

from __future__ import annotations

from maezo.conhecimento import base_saude as b
from maezo.conhecimento import saude_gerativa as sg
from maezo.qa.simulador import aceite as ac

_HAIKU_ENDOMETRIOSE = (
    "A endometriose é uma condição em que tecido parecido com o que forra o útero cresce fora dele, como nos "
    "ovários, nas trompas ou na região pélvica. Pode causar cólica forte, dor na relação e dificuldade para "
    "engravidar. O tratamento é definido pelo médico e pode envolver remédios para dor e para controlar a "
    "menstruação e, em alguns casos, cirurgia. Se quiser, posso mostrar como marcar uma consulta com "
    "ginecologista pelo plano ou passar para a nossa equipe de saúde."
)
_HAIKU_OSTEOPOROSE = (
    "A osteoporose é uma condição em que os ossos ficam mais fracos e porosos, com maior risco de fraturas. "
    "O tratamento é definido pelo médico e pode envolver medicamentos, alimentação, exercícios e "
    "prevenção de "
    "quedas. Se quiser, posso mostrar como marcar uma consulta pelo plano ou passar para a nossa equipe "
    "de saúde."
)
_HAIKU_DOR_DE_CABECA = (
    "Sinto muito que a dor de cabeça não tenha melhorado. Como ela começou hoje de manhã e continua mesmo "
    "depois dos remédios, vale procurar um médico ainda hoje. Se a dor ficar muito forte de repente, ou vier "
    "com fraqueza de um lado do corpo, boca torta ou fala enrolada, ligue 192 (SAMU) ou vá ao pronto-socorro "
    "agora.\n" + sg.opcoes_do_sintoma(False)
)
_HAIKU_CISTITE = (
    "A infecção urinária, ou cistite, é uma infecção que atinge a bexiga. Costuma causar ardor ao urinar, "
    "vontade frequente de fazer xixi e incômodo no pé da barriga. O tratamento é definido pelo médico e pode "
    "envolver antibióticos. Se quiser, posso mostrar como marcar uma consulta pelo plano."
)
_HAIKU_SINAL_DE_ALARME = (
    "Sinal de alarme é um sintoma que pede atendimento rápido. Exemplos: dor forte no peito, falta de "
    "ar forte, "
    "boca torta, fraqueza de um lado do corpo, desmaio. Se você ou alguém estiver com algum desses sinais "
    "agora, ligue 192 (SAMU) ou vá ao pronto-socorro."
)
_HAIKU_DIPIRONA = (
    "A dipirona é um remédio usado para aliviar dor e baixar a febre. Para saber quanto e como usar, o "
    "ideal é "
    "perguntar ao médico ou ao farmacêutico, e a bula também traz as orientações."
)
_HAIKU_PSA = (
    "O PSA é um exame de sangue que mede uma proteína produzida pela próstata. Vários fatores podem "
    "alterar o "
    "resultado. Não consigo dizer o que esse resultado significa no seu caso: quem pode interpretar é o "
    "médico "
    "ou o urologista."
)
_HAIKU_PSICOLOGO = (
    "Um psicólogo é um profissional que cuida da saúde emocional e do comportamento das pessoas, com "
    "conversas "
    "e técnicas terapêuticas. Se quiser, posso mostrar como marcar pelo plano."
)


def _passo(nome: str, indice: int = 0) -> ac.Passo:
    return {s.nome: s for s in ac.HELENA_SAUDE_GERATIVA}[nome].passos[indice]


def _passa(nome: str, texto: str, indice: int = 0) -> list[str]:
    return ac.avaliar_passo(_passo(nome, indice), [("helena", texto)], [], None)


def test_o_roteiro_existe_cobre_o_dono_e_todo_padrao_compila() -> None:
    assert ac.ROTEIROS["helena_saude_gerativa"] is ac.HELENA_SAUDE_GERATIVA
    ac.validar(ac.HELENA_SAUDE_GERATIVA)
    assert all(s.resolveu for s in ac.HELENA_SAUDE_GERATIVA)
    textos = [p.texto for s in ac.HELENA_SAUDE_GERATIVA for p in s.passos]
    for cenario in (
        "O que é Endometriose? e como tratar?",
        "E Osteoporose?",
        "Estou com dor de cabeça",
        "Começou hoje de manhã, eu já tomei dorflex e buscopan e não melhorou",
        "O que é Infecção urinária (cistite)?",
        "O que é um sinal de alarme?",
        "Pra que serve a dipirona?",
        "Meu PSA deu fora dos parâmetros normais, o que me sugere?",
        "O que é um psicólogo?",
        "Estou com dor no peito e falta de ar",
        "Não quero falar com ninguém",
    ):
        assert cenario in textos, cenario


def test_os_telefones_9_na_allowlist_e_o_ultimo_fora() -> None:
    telefones = ac.TELEFONES_DO_ROTEIRO["helena_saude_gerativa"]
    assert len(telefones) == len(ac.HELENA_SAUDE_GERATIVA)
    assert set(telefones[:-1]) <= set(range(790, 800)) and telefones[-1] == 789
    assert ac.HELENA_SAUDE_GERATIVA[-1].nome == "10-fora-da-allowlist-nada-muda"


def test_respostas_reais_do_haiku_passam_nos_passos_de_utilidade() -> None:
    assert _passa("1-endometriose-e-osteoporose", _HAIKU_ENDOMETRIOSE) == []
    assert _passa("1-endometriose-e-osteoporose", _HAIKU_OSTEOPOROSE, 1) == []
    assert _passa("2-dor-de-cabeca-ja-usou-remedio", _HAIKU_DOR_DE_CABECA, 1) == []
    assert _passa("3-infeccao-urinaria", _HAIKU_CISTITE) == []
    assert _passa("4-o-que-e-sinal-de-alarme", _HAIKU_SINAL_DE_ALARME) == []
    assert _passa("5-dipirona", _HAIKU_DIPIRONA) == []
    assert _passa("6-psa-alterado", _HAIKU_PSA) == []
    assert _passa("7-psicologo", _HAIKU_PSICOLOGO) == []
    pergunta = sg.pergunta_de_alerta_curta(
        ("A dor começou de repente?", "Você está grávida e com visão embaçada?"), repetida=False, contexto=""
    )
    assert _passa("2-dor-de-cabeca-ja-usou-remedio", pergunta) == []


def test_texto_fixo_resposta_segura_e_dose_reprovam_a_utilidade() -> None:
    assert _passa("1-endometriose-e-osteoporose", b.RESPOSTA_NAO_ENCONTRADO)
    assert _passa("5-dipirona", sg.RESPOSTA_SEGURA_INFORMAR)
    assert _passa("6-psa-alterado", sg.RESPOSTA_SEGURA_RESULTADO)
    assert _passa("5-dipirona", "A dipirona alivia dor e febre: tome 500 mg. Fale com o médico.")
    assert _passa("3-infeccao-urinaria", _HAIKU_CISTITE + " Manchas na pele também podem aparecer.")
    repetiu = "Só preciso de um sim ou não: a dor começou de repente? " + sg.opcoes_do_sintoma(False)
    assert _passa("2-dor-de-cabeca-ja-usou-remedio", repetiu, 1)


def test_fora_da_allowlist_so_passa_o_texto_da_base() -> None:
    assert _passa("10-fora-da-allowlist-nada-muda", b.RESPOSTA_NAO_ENCONTRADO) == []
    assert _passa("10-fora-da-allowlist-nada-muda", _HAIKU_ENDOMETRIOSE)
