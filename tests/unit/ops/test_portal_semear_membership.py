"""Semeadura idempotente de `amh.portal_memberships` (DL-0113, pessoas de teste do CANCEL-001)."""

from __future__ import annotations

import json

import pytest
from scripts.ops.portal_semear_membership import ISSUER_PADRAO, REVIEWED_PADRAO, igual, montar_registro

from maezo.portal.api.records import MembershipRecord

SUB = "13ac3a7a-30c1-70b4-7f0c-73e26de7c128"


def _registro(**mudancas: object) -> dict:
    base = dict(
        tenant="amh",
        issuer=ISSUER_PADRAO,
        subject=SUB,
        groups=["cadastro-beneficiarios"],
        roles=["cancelamento-executar"],
        reviewed_until=REVIEWED_PADRAO,
    )
    base.update(mudancas)
    return montar_registro(**base)  # type: ignore[arg-type]


def test_o_registro_passa_no_modelo_do_store() -> None:
    registro = _registro()
    modelo = MembershipRecord.model_validate_json(json.dumps(registro))
    assert modelo.revision == 1 and modelo.audience == "staff" and not modelo.revoked
    assert modelo.principal_ref.startswith("prn_") and len(modelo.principal_ref) == 36
    assert modelo.memberships[0].membership_ref.startswith("mbr_")
    assert list(modelo.memberships[0].groups) == ["cadastro-beneficiarios"]
    assert list(modelo.memberships[0].roles) == ["cancelamento-executar"]


def test_mesmo_sub_gera_os_mesmos_refs_e_sub_diferente_gera_outros() -> None:
    a, b = _registro(), _registro()
    assert a == b
    outro = _registro(subject="outro-sub-0000")
    assert outro["principal_ref"] != a["principal_ref"]


def test_o_mesmo_conteudo_e_igual_e_nao_sobe_a_revisao() -> None:
    antigo = _registro()
    novo = _registro(anterior=antigo)
    assert igual(antigo, novo)


def test_grupos_diferentes_sobem_a_revisao_e_mantem_os_refs() -> None:
    antigo = _registro()
    novo = _registro(groups=["cadastro-beneficiarios", "coordenacao-contratos"], anterior=antigo)
    assert not igual(antigo, novo)
    assert novo["revision"] == antigo["revision"] + 1
    assert novo["principal_ref"] == antigo["principal_ref"]
    assert novo["memberships"][0]["membership_ref"] == antigo["memberships"][0]["membership_ref"]


def test_registro_revogado_nao_e_igual() -> None:
    antigo = _registro()
    novo = _registro(anterior=antigo)
    assert not igual(dict(antigo, revoked=True), novo)


@pytest.mark.parametrize(
    "mudanca",
    [{"groups": []}, {"roles": []}, {"subject": ""}, {"groups": ["com espaco"]}, {"roles": ["../x"]}],
)
def test_entradas_invalidas_sao_recusadas(mudanca: dict) -> None:
    with pytest.raises(SystemExit):
        _registro(**mudanca)
