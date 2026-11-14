"""Chave PAGTO por conta (decisao do dono de 2026-11-01, opcao A; DEC-30 D13) — o compositor de dominio.

`pagto_business_key_por_conta` e' a forma que o handoff CONTAS->PAGTO escreve na fase
`escrita_alvo` quando CONTAS adjudicou por guia/conta. Fixa: o formato exato
`PAGTO-{t}-C-{prestador}-{guia}-{conta}` (DEC-30 D13: o prestador entra), a recusa de `-` no
prestador e na guia (segmentos do meio; R-101 + D13, fail-closed), a injetividade que essas recusas
compram, e que a regra de FORMA e' a MESMA do compositor de CONTAS (um predicado, nao duas condicoes).
"""

from __future__ import annotations

import pytest

from maezo.domain.chave_de_negocio import (
    PAGTO_BUSINESS_KEY_PREFIX,
    PAGTO_FORMA_POR_CONTA,
    BusinessKeyComponentError,
    contas_adjudica_por_conta,
    contas_business_key,
    pagto_business_key_por_conta,
)


def _chave(
    prestador: object = "P1", guia: object = "G1", conta: object = "C1", tenant: object = "amh"
) -> str:
    return pagto_business_key_por_conta(
        tenant_id=tenant, prestador_id=prestador, numero_guia_tiss=guia, numero_conta=conta
    )


def test_formato_exato_da_chave_por_conta() -> None:
    assert PAGTO_BUSINESS_KEY_PREFIX == "PAGTO-"
    assert PAGTO_FORMA_POR_CONTA == "C"
    assert _chave(prestador="PREST9", guia="G123", conta="CT9") == "PAGTO-amh-C-PREST9-G123-CT9"


def test_o_prestador_e_obrigatorio_na_assinatura() -> None:
    """DEC-30 D13: sem prestador nao ha chave por conta (nenhum padrao silencioso)."""
    with pytest.raises(TypeError):
        pagto_business_key_por_conta(tenant_id="amh", numero_guia_tiss="G1", numero_conta="C1")  # type: ignore[call-arg]


def test_duas_contas_do_mesmo_lote_e_prestador_tem_chaves_distintas() -> None:
    """O defeito em si: a chave legada `PAGTO-{t}-{lote}-{prestador}` era a mesma para as duas."""
    assert (
        len({_chave(guia="G1", conta="C1"), _chave(guia="G1", conta="C2"), _chave(guia="G2", conta="C1")})
        == 3
    )


def test_a_mesma_guia_e_conta_de_prestadores_distintos_tem_chaves_distintas() -> None:
    """DEC-30 D13 (opcao B): o prestador separa duas contas com a mesma guia/conta."""
    assert _chave(prestador="P1") != _chave(prestador="P2")


def test_a_mesma_conta_compoe_a_mesma_chave() -> None:
    """Dedup estrito preservado: reenvio da mesma conta converge na mesma chave."""
    assert _chave() == _chave()


@pytest.mark.parametrize("guia", ["A-B", "-G1", "G1-", "G-1-2"])
def test_guia_com_hifen_e_recusada(guia: str) -> None:
    """Decisao do dono R-101 ("recusar '-' no numero da guia", fail-closed)."""
    with pytest.raises(BusinessKeyComponentError, match="numero_guia_tiss"):
        _chave(guia=guia)


@pytest.mark.parametrize("prestador", ["A-B", "-P1", "P1-", "prov:teste-0001"])
def test_prestador_com_hifen_e_recusado(prestador: str) -> None:
    """DEC-30 D13: o prestador e' segmento do meio — com `-` a chave seria ambigua. Fail-closed."""
    with pytest.raises(BusinessKeyComponentError, match="prestador_id"):
        _chave(prestador=prestador)


def test_hifen_na_conta_e_aceito_e_continua_injetivo() -> None:
    """Com prestador e guia sem `-`, a conta (ultimo componente) pode conter `-` sem ambiguidade:
    os pares que colidiriam (`prestador='A-B', guia='C'` x `prestador='A', guia='B', conta='C-...'`;
    `guia='A-B', conta='C'` x `guia='A', conta='B-C'`) nao compoem mais — o lado com `-` no meio e'
    recusado."""
    assert _chave(prestador="P", guia="A", conta="B-C") == "PAGTO-amh-C-P-A-B-C"
    with pytest.raises(BusinessKeyComponentError):
        _chave(prestador="P", guia="A-B", conta="C")
    assert _chave(prestador="A", guia="B", conta="C-D") == "PAGTO-amh-C-A-B-C-D"
    with pytest.raises(BusinessKeyComponentError):
        _chave(prestador="A-B", guia="C", conta="D")


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("tenant", ""),
        ("tenant", "am-h"),
        ("tenant", None),
        ("prestador", ""),
        ("prestador", " P1"),
        ("prestador", "P 1"),
        ("prestador", None),
        ("prestador", 7),
        ("guia", ""),
        ("guia", "G 1"),
        ("guia", 123),
        ("conta", ""),
        ("conta", " C1"),
        ("conta", None),
    ],
)
def test_componente_invalido_e_recusado(campo: str, valor: object) -> None:
    """A MESMA normalizacao/recusa (`componente`) para todo segmento, prestador incluido."""
    entrada: dict[str, object] = {"tenant": "amh", "prestador": "P1", "guia": "G1", "conta": "C1"}
    entrada[campo] = valor
    with pytest.raises(BusinessKeyComponentError):
        _chave(**entrada)


@pytest.mark.parametrize(
    ("guia", "conta", "por_conta"),
    [("G1", "C1", True), ("G1", "", False), ("", "C1", False), ("", "", False), (None, "C1", False)],
)
def test_a_forma_e_a_mesma_regra_do_compositor_de_contas(
    guia: object, conta: object, por_conta: bool
) -> None:
    """O handoff pergunta a forma ao MESMO predicado que `contas_business_key` usa."""
    assert contas_adjudica_por_conta(numero_guia_tiss=guia, numero_conta=conta) is por_conta
    chave = contas_business_key(
        tenant_id="amh", numero_lote_tiss="L1", numero_guia_tiss=guia or "", numero_conta=conta or ""
    )
    assert (chave == "CONTAS-amh-L1") is (not por_conta)
