"""Cerca: o modulo observability (AMP/AMG, ADR-0014) e' invocado por um env root, DARK.

F-070 / R-075. `deploy/terraform/modules/observability` existia, mas nenhum env root o
instanciava: `grep -rEn 'modules?/observability' deploy/terraform/envs` retornava zero
linhas. No dia em que staging subisse, o collector OTel nao teria workspace AMP para o
remote-write nem role IRSA para o SigV4 — e nenhum `terraform validate` acusaria.

A invocacao entra em `staging-sa-east-1` atras de `var.enable_observability`, que nasce
`false` (flag default-off): `count = 0` => zero recursos, zero custo (ADR-0014 estima
~$30-50/mes quando ligado). Ligar e' ato deliberado do dono, nao default.
"""

from __future__ import annotations

from typing import Final

from tests.unit.deploy._hcl_probe import (
    attr_raw,
    block_body,
    direct_attrs,
    has_attr,
    read_tf,
)

_ENV: Final[str] = "envs/staging-sa-east-1"
_MODULO: Final[str] = "../../modules/observability"


def _corpo_do_modulo() -> str:
    return block_body(read_tf(f"{_ENV}/main.tf"), r'module\s+"observability"\s*')


def test_staging_invoca_o_modulo_observability() -> None:
    assert _MODULO in attr_raw(_corpo_do_modulo(), "source")


def test_a_invocacao_e_dark_por_flag() -> None:
    diretos = direct_attrs(_corpo_do_modulo())
    assert has_attr(diretos, "count"), "module.observability perdeu o `count` que o mantem dark"
    count = attr_raw(diretos, "count")
    assert "var.enable_observability" in count
    assert "? 1 : 0" in count


def test_a_flag_nasce_desligada() -> None:
    corpo = direct_attrs(block_body(read_tf(f"{_ENV}/variables.tf"), r'variable\s+"enable_observability"\s*'))
    assert attr_raw(corpo, "type") == "bool"
    assert attr_raw(corpo, "default") == "false", (
        "enable_observability deixou de nascer `false`: ligar AMP/AMG gera custo e exige decisao do dono"
    )


def test_irsa_vem_do_cluster_do_proprio_env() -> None:
    corpo = _corpo_do_modulo()
    assert "module.eks.oidc_issuer_url" in attr_raw(corpo, "eks_oidc_provider_url")
    assert "module.eks.oidc_provider_arn" in attr_raw(corpo, "eks_oidc_provider_arn")
