"""E-SF-08 / EP-REG-23a (+ EP-REG-23d) — catalogo de prazos normativos do funil: forma, refs de aprovacao,
hash do verbatim e SLAs de escalonamento que continuam DRAFT e internos.

Cada teste afirma DUAS coisas: o catalogo real nao tem achado (estado fechado: nada ratificado) e a violacao
injetada produz o motivo tipado. Afrouxar a regra de leitura em `_catalogo_prazos.auditar_catalogo` ou gravar
na DMN uma linha "ratificada" sem lastro deixa o teste vermelho.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

from tests.support.dmn_first_hit import DMN_DIR, read_live_table
from tests.unit.dmn._catalogo_prazos import (
    CATALOGO,
    VERBATIM_SHA256_APROVADO,
    LinhaCatalogo,
    auditar_catalogo,
    duracoes_draft_internas,
    linha_para,
    linhas,
    status_efetivo,
)

_REGISTRO = Path(__file__).resolve().parents[3] / "spec" / "aprovacoes-de-regras.yaml"
_CATALOGO_REL = "spec/processes/dmn/funnel_deadline_catalog.dmn"


def _por_ref(ref: str) -> LinhaCatalogo:
    (linha,) = (x for x in linhas() if x.deadline_ref == ref)
    return linha


def test_deadline_catalog_shape_first_with_catch_all_last() -> None:
    todas = linhas()
    assert [x.deadline_ref for x in todas] == ["RN413-art5-par2", "RN413-art5-par7", "Lei9656-art12-VI", ""]
    assert todas[-1].regra == "r_catchall"
    assert set(VERBATIM_SHA256_APROVADO) == {x.deadline_ref for x in todas if x.deadline_ref}
    desconhecido = linha_para("estagio-5-analise")
    assert desconhecido.regra == "r_catchall"
    assert desconhecido.prazo_quantidade == -1 and desconhecido.dispositivo_verbatim == ""


def test_deadline_catalog_requires_approval_refs() -> None:
    todas = linhas()
    # Estado fechado: nada ratificado nesta onda.
    assert auditar_catalogo(todas) == []
    assert {x.status for x in todas} == {"DRAFT"}
    assert {x.regulatory_approval_ref for x in todas} == {""}
    assert {x.clinical_approval_ref for x in todas} == {""}
    assert {status_efetivo(x) for x in todas} == {"DRAFT"}

    par2 = _por_ref("RN413-art5-par2")
    # (a) RATIFIED sem regulatory_approval_ref -> deadline_unratified, e continua lendo como DRAFT.
    injetada = dataclasses.replace(par2, status="RATIFIED", evento_ancora_ref="ancora-x")
    assert ("r_rn413_art5_par2", "deadline_unratified") in auditar_catalogo([injetada])
    assert status_efetivo(injetada) == "DRAFT"
    # (b) assistencial RATIFIED sem clinical_approval_ref -> recusada.
    assistencial = dataclasses.replace(
        par2,
        status="RATIFIED",
        regulatory_approval_ref="REF-J",
        assistencial=True,
        evento_ancora_ref="ancora-x",
    )
    assert auditar_catalogo([assistencial]) == [("r_rn413_art5_par2", "deadline_clinical_unratified")]
    assert status_efetivo(assistencial) == "DRAFT"
    # (c) status fora do vocabulario.
    assert ("r_rn413_art5_par2", "deadline_status_unknown") in auditar_catalogo(
        [dataclasses.replace(par2, status="APROVADO")]
    )


def test_deadline_catalog_verbatim_hash() -> None:
    todas = linhas()
    assert auditar_catalogo(todas) == []
    par2 = _por_ref("RN413-art5-par2")
    alterado = par2.dispositivo_verbatim.replace("25 (vinte e cinco)", "30 (trinta)")
    # (a) verbatim alterado, hash velho.
    assert auditar_catalogo([dataclasses.replace(par2, dispositivo_verbatim=alterado)]) == [
        ("r_rn413_art5_par2", "deadline_verbatim_mismatch")
    ]
    # (b) verbatim E hash alterados juntos na tabela: o pin independente continua recusando.
    import hashlib

    novo_hash = hashlib.sha256(alterado.encode("utf-8")).hexdigest()
    assert auditar_catalogo(
        [dataclasses.replace(par2, dispositivo_verbatim=alterado, verbatim_sha256=novo_hash)]
    ) == [("r_rn413_art5_par2", "deadline_verbatim_mismatch")]
    # (c) catch-all nunca carrega verbatim.
    catchall = todas[-1]
    assert auditar_catalogo([dataclasses.replace(catchall, dispositivo_verbatim="x")]) == [
        ("r_catchall", "deadline_verbatim_mismatch")
    ]


def test_escalation_sla_stays_draft_internal_and_unexhibited() -> None:
    """EP-REG-23d (reusa 23a): SLAs P1-P3 seguem DRAFT, pendentes no registro e fora do catalogo."""
    descricao = (DMN_DIR / "escalation_routing.dmn").read_text(encoding="utf-8")
    assert "valores de SLA: DRAFT" in descricao
    registro = yaml.safe_load(_REGISTRO.read_text(encoding="utf-8"))
    aprovados = {e["artefato"] for e in registro.get("aprovados") or []}
    pendentes = {e["artefato"] for e in registro.get("pendentes") or []}
    assert "spec/processes/dmn/escalation_routing.dmn" in pendentes - aprovados
    sla_escalonamento = {
        o
        for r in read_live_table(DMN_DIR / "escalation_routing.dmn").rules
        for o in r.outputs
        if str(o).startswith("PT")
    }
    assert sla_escalonamento <= duracoes_draft_internas()
    saidas_catalogo = {str(v) for x in linhas() for v in dataclasses.asdict(x).values()}
    assert not (sla_escalonamento & saidas_catalogo)
    assert not any(x.assistencial for x in linhas())


def test_catalog_is_pending_in_the_approval_registry() -> None:
    """DL-0148: o catalogo aparece como PENDENTE (nunca aprovado), com a linha de status copiada."""
    registro = yaml.safe_load(_REGISTRO.read_text(encoding="utf-8"))
    assert _CATALOGO_REL not in {e["artefato"] for e in registro.get("aprovados") or []}
    (entrada,) = (e for e in registro["pendentes"] if e["artefato"] == _CATALOGO_REL)
    assert entrada["marca_no_arquivo"] in CATALOGO.read_text(encoding="utf-8")
