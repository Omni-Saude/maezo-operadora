"""Chave PAGTO por conta (decisao do dono de 2026-11-01, opcao A) — o compositor de dominio.

`pagto_business_key_por_conta` e' a forma que o handoff CONTAS->PAGTO escreve na fase
`escrita_alvo` quando CONTAS adjudicou por guia/conta. Fixa: o formato exato, a recusa de `-` na
guia (decisao do dono R-101, fail-closed), a injetividade que essa recusa compra, e que a regra de
FORMA e' a MESMA do compositor de CONTAS (um predicado, nao duas condicoes).
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


def test_formato_exato_da_chave_por_conta() -> None:
    assert PAGTO_BUSINESS_KEY_PREFIX == "PAGTO-"
    assert PAGTO_FORMA_POR_CONTA == "C"
    assert (
        pagto_business_key_por_conta(tenant_id="amh", numero_guia_tiss="G123", numero_conta="CT9")
        == "PAGTO-amh-C-G123-CT9"
    )


def test_duas_contas_do_mesmo_lote_e_prestador_tem_chaves_distintas() -> None:
    """O defeito em si: a chave legada `PAGTO-{t}-{lote}-{prestador}` era a mesma para as duas."""
    primeira = pagto_business_key_por_conta(tenant_id="amh", numero_guia_tiss="G1", numero_conta="C1")
    segunda = pagto_business_key_por_conta(tenant_id="amh", numero_guia_tiss="G1", numero_conta="C2")
    terceira = pagto_business_key_por_conta(tenant_id="amh", numero_guia_tiss="G2", numero_conta="C1")
    assert len({primeira, segunda, terceira}) == 3


def test_a_mesma_conta_compoe_a_mesma_chave() -> None:
    """Dedup estrito preservado: reenvio da mesma conta converge na mesma chave."""
    assert pagto_business_key_por_conta(
        tenant_id="amh", numero_guia_tiss="G1", numero_conta="C1"
    ) == pagto_business_key_por_conta(tenant_id="amh", numero_guia_tiss="G1", numero_conta="C1")


@pytest.mark.parametrize("guia", ["A-B", "-G1", "G1-", "G-1-2"])
def test_guia_com_hifen_e_recusada(guia: str) -> None:
    """Decisao do dono R-101 ("recusar '-' no numero da guia", fail-closed)."""
    with pytest.raises(BusinessKeyComponentError, match="numero_guia_tiss"):
        pagto_business_key_por_conta(tenant_id="amh", numero_guia_tiss=guia, numero_conta="C1")


def test_hifen_na_conta_e_aceito_e_continua_injetivo() -> None:
    """Com a guia sem `-`, a conta (ultimo componente) pode conter `-` sem ambiguidade: os pares
    que colidiam na forma `G` de CONTAS (`guia='A-B', conta='C'` x `guia='A', conta='B-C'`) nao
    compoem mais — o primeiro e' recusado."""
    assert (
        pagto_business_key_por_conta(tenant_id="amh", numero_guia_tiss="A", numero_conta="B-C")
        == "PAGTO-amh-C-A-B-C"
    )
    with pytest.raises(BusinessKeyComponentError):
        pagto_business_key_por_conta(tenant_id="amh", numero_guia_tiss="A-B", numero_conta="C")


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("tenant_id", ""),
        ("tenant_id", "am-h"),
        ("tenant_id", None),
        ("numero_guia_tiss", ""),
        ("numero_guia_tiss", "G 1"),
        ("numero_guia_tiss", 123),
        ("numero_conta", ""),
        ("numero_conta", " C1"),
        ("numero_conta", None),
    ],
)
def test_componente_invalido_e_recusado(campo: str, valor: object) -> None:
    entrada: dict[str, object] = {"tenant_id": "amh", "numero_guia_tiss": "G1", "numero_conta": "C1"}
    entrada[campo] = valor
    with pytest.raises(BusinessKeyComponentError):
        pagto_business_key_por_conta(**entrada)  # type: ignore[arg-type]


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
