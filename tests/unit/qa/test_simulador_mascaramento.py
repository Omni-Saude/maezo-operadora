"""Mascaramento: nada de CPF, nascimento, nome, boleto ou telefone sai do processo do simulador."""

from __future__ import annotations

from maezo.qa.simulador.mascaramento import (
    TOKEN_BOLETO,
    TOKEN_CPF,
    TOKEN_NASCIMENTO,
    TOKEN_NOME,
    TOKEN_TELEFONE,
    Mascarador,
    contem_dado_do_sujeito,
    sem_acento,
)

from .apoio_simulador import CPF_SINTETICO, fatos_de_julho, sujeito


def test_cpf_com_e_sem_pontuacao() -> None:
    m = Mascarador()
    assert m.texto(f"meu cpf {CPF_SINTETICO}") == f"meu cpf {TOKEN_CPF}"
    assert m.texto("cpf 529.982.247-25 ok") == f"cpf {TOKEN_CPF} ok"


def test_nascimento_do_sujeito_em_varias_formas() -> None:
    m = Mascarador(sujeito())
    for forma in ("14/03/1980", "14-03-1980", "14031980", "1980-03-14", "14/3/1980"):
        assert m.texto(f"nasci em {forma}") == f"nasci em {TOKEN_NASCIMENTO}", forma


def test_nome_sem_diferenciar_acento_nem_caixa() -> None:
    m = Mascarador(sujeito(nome="Joana Pereira da Silva"))
    assert m.texto("Oi JOANA, aqui e a Joána") == f"Oi {TOKEN_NOME}, aqui e a {TOKEN_NOME}"
    # particula e palavra comum nao sao mascaradas
    assert m.texto("o plano da operadora") == "o plano da operadora"


def test_boleto_e_telefone() -> None:
    m = Mascarador()
    assert m.texto("boleto final 0037") == f"boleto final {TOKEN_BOLETO}"
    assert m.texto("rotulo ****1234") == f"rotulo {TOKEN_BOLETO}"
    assert TOKEN_TELEFONE in m.texto("ligue (11) 98765-4321")


def test_valor_competencia_e_vencimento_ficam() -> None:
    m = Mascarador(sujeito())
    texto = "A de julho de 2026 (07/2026) foi R$ 8.389,53, vencimento 10/07/2026."
    assert m.texto(texto) == texto


def test_objeto_mascara_boleto_dos_fatos_e_textos() -> None:
    mascarado = Mascarador(sujeito()).objeto([fatos_de_julho()])
    assert mascarado[0]["numero_boleto"] == TOKEN_BOLETO
    assert mascarado[0]["competencias"][0]["boleto"] == TOKEN_BOLETO
    assert mascarado[0]["competencias"][0]["valor_total"] == "8389.53"


def test_contem_dado_do_sujeito() -> None:
    s = sujeito()
    assert contem_dado_do_sujeito("cpf 529.982.247-25", s) == ["cpf"]
    assert contem_dado_do_sujeito("nasceu 14/03/1980", s) == ["nascimento"]
    assert contem_dado_do_sujeito("oi joana", s) == ["nome"]
    assert contem_dado_do_sujeito("tudo certo", s) == []


def test_sem_acento() -> None:
    assert sem_acento("Março ÉÇ") == "marco ec"
