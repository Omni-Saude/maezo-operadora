"""As 28 personas, os marcadores e o plano de conversas."""

from __future__ import annotations

import random

import pytest

from maezo.platform.webhooks.whatsapp.acesso import cpf_valido, extrair_cpf
from maezo.qa.simulador import personas as p

from .apoio_simulador import CPF_SINTETICO, sujeito

#: O prefixo de verificacao PADRAO (as personas de ataque rt_ comecam por ele; as de fluxo de
#: acesso NAO — elas testam a verificacao do zero, com recusa ou CPF errado).
_PREFIXO_VERIFICACAO = ("Oi", "ACEITO", p.MARCADOR_CPF, p.MARCADOR_NASCIMENTO)


def test_sao_vinte_e_oito_personas_com_ids_unicos_e_cobertura_do_desenho() -> None:
    assert len(p.PERSONAS) == 28
    ids = {x.id for x in p.PERSONAS}
    assert len(ids) == 28
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
    # as seis personas de red team de cobranca
    assert {x.id for x in p.PERSONAS if x.id.startswith("rt_")} == {
        "rt_injecao_cobranca",
        "rt_valor_menor",
        "rt_desconto",
        "rt_pede_boleto",
        "rt_link_falso",
        "rt_ameaca_rescisao",
    }
    assert {x.escalacao for x in p.PERSONAS} == {"nunca", "permitida", "exigida"}
    assert any(x.emergencia for x in p.PERSONAS)


def test_duas_personas_exercitam_o_fluxo_de_acesso_do_zero() -> None:
    # Fluxo de acesso = roteiro que NAO comeca pelo prefixo de verificacao padrao (recusa, CPF errado).
    acesso = [
        x
        for x in p.PERSONAS
        if x.roteiro_acesso is not None and tuple(x.roteiro_acesso[:4]) != _PREFIXO_VERIFICACAO
    ]
    assert {x.id for x in acesso} == {"recusa_revoga", "cpf_errado"}
    for persona in acesso:
        assert p.MARCADOR_CPF in persona.roteiro_acesso  # type: ignore[operator]


def test_personas_de_ataque_rt_sao_scriptadas_e_verificam_antes() -> None:
    ataque = [x for x in p.PERSONAS if x.id.startswith("rt_")]
    assert len(ataque) == 6
    for persona in ataque:
        assert persona.cobranca and persona.roteiro_acesso is not None
        assert tuple(persona.roteiro_acesso[:4]) == _PREFIXO_VERIFICACAO
        # o ataque fixo vem depois da verificacao, sem marcador residual
        assert len(persona.roteiro_acesso) >= 5
        assert all("{{" not in passo for passo in persona.roteiro_acesso[4:])


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
    assert {x.id for x in plano[:28]} == {x.id for x in p.PERSONAS}
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
