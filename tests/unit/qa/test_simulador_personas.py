"""As 22 personas, os marcadores e o plano de conversas."""

from __future__ import annotations

import random

import pytest

from maezo.platform.webhooks.whatsapp.acesso import cpf_valido, extrair_cpf
from maezo.qa.simulador import personas as p

from .apoio_simulador import CPF_SINTETICO, sujeito


def test_sao_vinte_e_duas_personas_com_ids_unicos_e_cobertura_do_desenho() -> None:
    assert len(p.PERSONAS) == 22
    ids = {x.id for x in p.PERSONAS}
    assert len(ids) == 22
    assert {
        "emergencia",
        "quer_humano",
        "contesta",
        "recusa_revoga",
        "cpf_errado",
        "terceiro",
        "cancelamento",
        "cancelamento_empresa",
    } <= ids
    assert {x.escalacao for x in p.PERSONAS} == {"nunca", "permitida", "exigida"}
    assert any(x.emergencia for x in p.PERSONAS)


def test_cerca_de_dez_por_cento_exercita_o_fluxo_de_acesso() -> None:
    com_roteiro = [x for x in p.PERSONAS if x.roteiro_acesso is not None]
    assert len(com_roteiro) == 2  # 2 de 22 = ~9%
    for persona in com_roteiro:
        assert p.MARCADOR_CPF in persona.roteiro_acesso  # type: ignore[operator]


def test_persona_com_turnos_invalidos_recusa() -> None:
    with pytest.raises(ValueError):
        p.Persona(
            id="x",
            nome="x",
            descricao="x",
            objetivo="x",
            estilo="x",
            escalacao="nunca",
            turnos_min=5,
            turnos_max=3,
        )


def test_selecionar_e_por_id() -> None:
    assert p.selecionar(None) == p.PERSONAS
    assert [x.id for x in p.selecionar(["idoso", " contesta ", ""])] == ["idoso", "contesta"]
    with pytest.raises(ValueError):
        p.por_id("nao-existe")


def test_planejar_cobre_todas_antes_de_repetir_e_e_deterministico() -> None:
    plano = p.planejar(p.PERSONAS, 30, random.Random(7))
    assert len(plano) == 30
    assert {x.id for x in plano[:22]} == {x.id for x in p.PERSONAS}
    assert plano == p.planejar(p.PERSONAS, 30, random.Random(7))


def test_cpf_errado_tem_forma_valida_e_digito_errado() -> None:
    errado = p.cpf_errado(CPF_SINTETICO)
    assert len(errado) == 11 and errado != CPF_SINTETICO
    assert not cpf_valido(errado)
    assert extrair_cpf(errado) is None  # recusa LOCAL: a AMH nao e' chamada
    with pytest.raises(ValueError):
        p.cpf_errado("123")


def test_substituir_troca_os_marcadores_pelo_dado_real() -> None:
    s = sujeito()
    assert p.substituir("{{CPF}}", s) == CPF_SINTETICO
    assert p.substituir("{{NASCIMENTO}}", s) == "14/03/1980"
    assert p.substituir("{{CPF_ERRADO}}", s) == p.cpf_errado(CPF_SINTETICO)
    assert p.substituir("sem marcador", s) == "sem marcador"
