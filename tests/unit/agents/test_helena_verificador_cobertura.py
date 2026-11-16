"""Verificador deterministico de fatos da cobertura (DL-0167, HEL-3b): nada sai sem conferir com a fonte.

Trechos SINTETICOS (valores de teste, nao sao regra de nenhuma operadora)."""

from __future__ import annotations

import pytest

from maezo.agents.helena.habilidades.cobertura.verificador import FORMULA_FIXA, verificar

_FONTE = (
    "AMOSTRA. O exame de exemplo precisa de autorização prévia do plano. O prazo de resposta é de 10 dias "
    "úteis. São até 12 sessões por ano. Vale desde 01/02/2026, conforme a norma 465/2021."
)
_SEM_AUTORIZACAO = "AMOSTRA. A consulta de exemplo não precisa de autorização prévia."


def _r(corpo: str) -> str:
    return f"{corpo} {FORMULA_FIXA}"


@pytest.mark.parametrize(
    "corpo",
    [
        "Esse exame precisa de autorização prévia, e a resposta sai em até 10 dias úteis.",
        "A resposta sai em dez dias úteis.",
        "São até doze sessões por ano.",
        "Isso vale desde 01/02/2026 (norma 465/2021).",
        "Para esse exame, peça a autorização antes de marcar.",
    ],
)
def test_fatos_que_batem_com_a_fonte_passam(corpo: str) -> None:
    v = verificar(_r(corpo), [_FONTE])
    assert v.ok, v.motivos


@pytest.mark.parametrize(
    ("corpo", "motivo"),
    [
        ("A resposta sai em até 5 dias úteis.", "quantidade"),
        ("A resposta sai em 10 dias.", "quantidade"),  # dias corridos != dias uteis
        ("São até 20 sessões por ano.", "quantidade"),
        ("Vale desde 01/03/2026.", "data"),
        ("Conforme a norma 999/2099.", "numero"),
        ("Esse exame não precisa de autorização.", "autorizacao"),
        ("Esse exame dispensa autorização prévia.", "autorizacao"),
        ("Seu plano cobre esse exame.", "decisao_do_caso"),
        ("O pedido vai ser autorizado.", "decisao_do_caso"),
        ("Você tem direito a esse exame.", "decisao_do_caso"),
        ("Veja em www.exemplo.com.br.", "link"),
        # revisao independente: sinonimos e polaridade da autorizacao, artigo com unidade
        ("A ressonância nunca precisa de autorização.", "autorizacao"),
        ("Para a ressonância, a autorização não é necessária.", "autorizacao"),
        ("A autorização é dispensada nesse exame.", "autorizacao"),
        ("A resposta sai em uma semana.", "quantidade"),
        ("A resposta sai em um mês.", "quantidade"),
    ],
)
def test_fato_que_nao_bate_bloqueia_o_envio(corpo: str, motivo: str) -> None:
    v = verificar(_r(corpo), [_FONTE])
    assert not v.ok and motivo in v.motivos


def test_sem_a_formula_fixa_nao_envia() -> None:
    v = verificar("Esse exame precisa de autorização prévia.", [_FONTE])
    assert not v.ok and "sem_formula_fixa" in v.motivos


@pytest.mark.parametrize("trechos", [[], [""], ["   "]])
def test_sem_fonte_nada_confere(trechos: list[str]) -> None:
    assert verificar(_r("Precisa de autorização."), trechos).motivos == ("sem_fonte",)


def test_fontes_conflitantes_sobre_autorizacao_nao_deixam_afirmar() -> None:
    v = verificar(_r("Precisa de autorização prévia."), [_FONTE, _SEM_AUTORIZACAO])
    assert not v.ok and "autorizacao" in v.motivos


def test_a_polaridade_negativa_confere_com_a_fonte_negativa() -> None:
    assert verificar(_r("A consulta não precisa de autorização prévia."), [_SEM_AUTORIZACAO]).ok


def test_o_veredito_nunca_leva_o_texto() -> None:
    v = verificar(_r("Seu plano cobre a ressonância em 3 dias."), [_FONTE])
    assert all("ressonancia" not in m and "ressonância" not in m for m in v.motivos)


@pytest.mark.parametrize(
    "corpo",
    [
        "A consulta precisa de liberação prévia.",
        "É preciso pedir a guia antes.",
        "Precisa de aprovação prévia.",
    ],
)
def test_sinonimo_de_autorizacao_sem_fonte_nao_passa(corpo: str) -> None:
    v = verificar(_r(corpo), ["AMOSTRA. A consulta básica deve ser oferecida em até 7 dias úteis."])
    assert not v.ok and "autorizacao" in v.motivos


def test_um_exame_nao_vira_numero() -> None:
    assert verificar(_r("Esse exame precisa de autorização prévia, com um pedido médico."), [_FONTE]).ok


# --- HEL-5a: bloqueios da revisao independente --------------------------------------------------------

_FONTE_RM = "AMOSTRA. A ressonância magnética eletiva precisa de autorização prévia da operadora."
_FONTE_FISIO = "AMOSTRA. Cada pedido médico vale até 20 sessões de fisioterapia por pedido."


@pytest.mark.parametrize(
    "corpo",
    [
        "A consulta básica precisa de autorização.",  # o mesmo "precisa", mas de OUTRO procedimento
        "A tomografia precisa de autorização prévia.",
        # segunda revisao: pontuacao colada, plural, nome fora do vocabulario e anafora
        "A tomografia, por exemplo, precisa de autorização prévia.",
        "As tomografias precisam de autorização prévia.",
        "A mamografia precisa de autorização prévia.",
        "A ressonância precisa de autorização prévia. A tomografia também.",
        # terceira revisao: sigla, procedimento numa frase e autorizacao na seguinte, outras anaforas, apelido
        "A TC precisa de autorização prévia.",
        "O PET-CT precisa de autorização prévia.",
        "A tomografia? Precisa de autorização prévia.",
        "Quanto à tomografia: sim. Precisa de autorização prévia.",
        "A ressonância precisa de autorização prévia. Com a tomografia é igual.",
        "A ressonância precisa de autorização prévia. Isso se aplica à tomografia.",
        "A ressonância precisa de autorização prévia. Vale igual para a tomografia.",
        "A ressonância precisa de autorização prévia. A mamo também.",
    ],
)
def test_autorizacao_de_outro_procedimento_nao_confere(corpo: str) -> None:
    v = verificar(_r(corpo), [_FONTE_RM])
    assert not v.ok and "autorizacao_de_outro_procedimento" in v.motivos


def test_autorizacao_do_procedimento_da_fonte_confere() -> None:
    assert verificar(_r("A ressonância precisa de autorização prévia."), [_FONTE_RM]).ok


@pytest.mark.parametrize(
    ("corpo", "motivo"),
    [
        ("O reembolso é de até R$ 20.", "quantidade"),  # "20" existe na fonte, mas como sessoes
        ("São até 20 sessões de fisioterapia por mês.", "quantidade"),  # frequencia inventada
        # segunda revisao: unidade por extenso, outra unidade, frequencia antes do numero ou adjetiva
        ("O reembolso é de até 20 reais.", "quantidade"),
        ("São até 20 exames.", "quantidade"),
        ("São até 20 sessões mensais.", "quantidade"),
        ("Por mês, são até 20 sessões.", "quantidade"),
        ("Toda semana, são até 20 sessões.", "quantidade"),
        # terceira revisao: numero sem unidade, frequencia em outra frase, somada, ou fora da lista
        ("O reembolso máximo é de 20.", "quantidade"),
        ("O valor é 20, em reais.", "quantidade"),
        ("Por mês: 20.", "quantidade"),
        ("São até 20 sessões. Isso vale por mês.", "quantidade"),
        ("São até 20 sessões por pedido e por mês.", "quantidade"),
        ("São até 20 sessões no mês.", "quantidade"),
        ("São até 20 sessões, mensalmente.", "quantidade"),
        ("São até 20 sessões, semanalmente.", "quantidade"),
    ],
)
def test_numero_solto_e_frequencia_inventada_nao_conferem(corpo: str, motivo: str) -> None:
    v = verificar(_r(corpo), [_FONTE_FISIO])
    assert not v.ok and motivo in v.motivos


def test_a_frequencia_da_fonte_confere() -> None:
    assert verificar(_r("São até 20 sessões por pedido."), [_FONTE_FISIO]).ok


@pytest.mark.parametrize(
    "corpo",
    [
        "A ressonância precisa de autorização prévia.",
        "A ressonância magnética eletiva precisa de autorização.",
    ],
)
def test_texto_literal_da_fonte_continua_conferindo(corpo: str) -> None:
    """Sem falso positivo no caminho legitimo: a frase da fonte (ou o seu sujeito) passa."""
    assert verificar(_r(corpo), [_FONTE_RM]).ok


def test_frase_literal_da_fonte_colada_a_autorizacao_nao_herda_a_afirmacao() -> None:
    """A frase da propria fonte ao lado da autorizacao continua passando (camada 1 envia o texto da regra)."""
    fonte = "AMOSTRA. A ressonância precisa de autorização prévia. A consulta de retorno é agendada pelo app."
    corpo = "A ressonância precisa de autorização prévia. A consulta de retorno é agendada pelo app."
    assert verificar(_r(corpo), [fonte]).ok


def test_palavra_comum_com_sufixo_nao_e_procedimento() -> None:
    corpo = "Envie a fotografia do pedido: a ressonância precisa de autorização prévia."
    assert verificar(_r(corpo), [_FONTE_RM]).ok


def test_exame_que_nenhuma_fonte_cita_nao_passa_nem_sem_afirmar_autorizacao() -> None:
    v = verificar(_r("Com a tomografia é igual."), [_FONTE_RM])
    assert not v.ok and "procedimento_sem_fonte" in v.motivos
