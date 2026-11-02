"""DEC-30 D11 (decisao do dono 2026-11-02, issue #1084): o incidente visivel da ordem PAGTO de outra
conta ganha ALERTA. Fixa a forma da regra, que ela le um contador com emissor vivo e rotulos
fechados (sem PHI/identificador), que o runbook dedicado existe e carrega o procedimento do
financeiro, e que a prova de comportamento (promtool) cobre a regra."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_ALERT_RULES: Final[Path] = _REPO_ROOT / "deploy" / "observability" / "alert-rules.yml"
_GROUP: Final[str] = "maezo_pagamento_contas"
_ALERTA: Final[str] = "MaezoContasPagtoOrdemDeOutraConta"
_METRICA: Final[str] = "maezo_contas_pagto_ordem_recusada_total"
_RUNBOOK: Final[str] = f"docs/runbooks/alerts/{_ALERTA}.md"

_FORBIDDEN_LABEL_FRAGMENTS: Final[tuple[str, ...]] = (
    "tenant",
    "lote",
    "guia",
    "conta",
    "prestador",
    "business_key",
    "chave",
    "instance",
    "cpf",
    "beneficiario",
)


def _regras() -> dict[str, dict[str, Any]]:
    document: Any = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    (group,) = [g for g in document["groups"] if g["name"] == _GROUP]
    return {rule["alert"]: rule for rule in group["rules"]}


def test_grupo_tem_exatamente_o_alerta_de_d11() -> None:
    assert set(_regras()) == {_ALERTA}


def test_regra_dispara_no_proprio_evento_sobre_o_contador_da_recusa() -> None:
    regra = _regras()[_ALERTA]
    expr = regra["expr"]
    assert f"increase({_METRICA}[5m])" in expr
    assert re.search(r">\s*0\s*$", expr.strip()), expr  # D11 nomeia o evento, nao uma taxa
    assert "for" not in regra
    assert regra["labels"]["severity"] == "warning"
    assert regra["annotations"]["runbook_url"] == _RUNBOOK
    assert (_REPO_ROOT / _RUNBOOK).is_file()


def test_rotulos_usados_existem_no_contador_e_nao_carregam_identificador() -> None:
    from maezo.runtime.metrics import MetricsCollector

    rotulos = tuple(MetricsCollector().contas_pagto_ordem_recusada._labelnames)
    assert rotulos == ("veredito", "fase")
    for rotulo in rotulos:
        assert not any(fragmento in rotulo for fragmento in _FORBIDDEN_LABEL_FRAGMENTS), rotulo
    expr = _regras()[_ALERTA]["expr"]
    for agrupado in re.findall(r"by \(([^)]*)\)", expr):
        assert {x.strip() for x in agrupado.split(",")} <= set(rotulos)


def test_emissor_vivo_fora_do_modulo_de_observabilidade() -> None:
    """O contador tem chamador em `src/` fora de `platform/observability.py` (o detector de
    biblioteca morta do `test_alert_metrics_fence`)."""
    contas = (_REPO_ROOT / "src" / "maezo" / "tools" / "workers" / "contas.py").read_text(encoding="utf-8")
    assert "record_contas_pagto_ordem_recusada(veredito=veredito, fase=fase)" in contas
    assert "preinit_contas_pagto_ordem_recusada()" in contas


def test_runbook_carrega_o_procedimento_do_financeiro() -> None:
    texto = (_REPO_ROOT / _RUNBOOK).read_text(encoding="utf-8")
    for trecho in (
        "Identify the conta",
        "Record it in the inventory",
        "Retry only after `escrita_alvo`",
        "Never pay outside the system",
        "pagto-chave-por-conta-migracao.md",
    ):
        assert trecho in texto, trecho
    migracao = (_REPO_ROOT / "docs" / "runbooks" / "pagto-chave-por-conta-migracao.md").read_text(
        encoding="utf-8"
    )
    assert "alerts/MaezoContasPagtoOrdemDeOutraConta.md" in migracao


def test_prova_promtool_cobre_a_regra() -> None:
    caso = (Path(__file__).resolve().parent / "promtool" / "alert-rules-pagamento.promtool.yml").read_text(
        encoding="utf-8"
    )
    assert f"alertname: {_ALERTA}" in caso
    promtool_py = (Path(__file__).resolve().parent / "test_alert_rules_promtool.py").read_text(
        encoding="utf-8"
    )
    assert "alert-rules-pagamento.promtool.yml" in promtool_py
