"""OP21 substrato — o modelo fechado do `legitimacy.screen` (desenho §3 DECISÃO 2).

O que esta unidade prova SEM banco (as provas de DDL viva são
`tests/unit/platform/test_legitimacy_screens_migration_live_pg.py`):

- o recibo fecha: campo extra é recusa (não existe slot onde bytes de documento caibam);
- a referência de evidência é TIPADA (`kind` de custódia + `locator` com teto + hex-64) e
  recusa LOUD as formas de abuso anti-custódia (conteúdo embutido, locator longo, sha ruim,
  canal como custodiante);
- as âncoras carregam a forma canônica do parecer §1 e recusam as formas imprecisas
  ("RN 659/2025, art. 23-A" e "RN 557/659");
- o estado é fechado e a sinalização do art. 39 é par exclusivo com a âncora literal
  "RN 557/2022, art. 39" (espelho do CHECK da 0030);
- instante ingênuo e bloco de critérios vazio são recusa; o recibo é imutável.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.gateway.commercial_legitimacy_screen import (
    ART39_ANCHOR,
    PROHIBITED_ANCHOR_FRAGMENTS,
    LegitimacyCriterion,
    LegitimacyEvidenceRef,
    LegitimacyScreenRecord,
)

_SHA = "b" * 64


def _ref(**overrides: object) -> LegitimacyEvidenceRef:
    fields: dict[str, object] = {
        "kind": "operadora",
        "locator": "custodia/2026/contrato-abc.pdf",
        "document_sha256": _SHA,
    }
    fields.update(overrides)
    return LegitimacyEvidenceRef(**fields)  # type: ignore[arg-type]


def _criterion(**overrides: object) -> LegitimacyCriterion:
    fields: dict[str, object] = {
        "criterion_id": "vinculo_empresarial",
        "result": "proven",
        "norm_anchor": "RN 557/2022, art. 5º",
        "evidence_refs": (_ref(),),
    }
    fields.update(overrides)
    return LegitimacyCriterion(**fields)  # type: ignore[arg-type]


def _record(**overrides: object) -> LegitimacyScreenRecord:
    now = datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)
    fields: dict[str, object] = {
        "tenant": "tenant-op21",
        "screen_ref": "screen-" + "a" * 16,
        "screen_revision": 1,
        "legal_entity_ref": "pj-contratante-1",
        "opportunity_ref": "opportunity-1",
        "contract_kind": "empresarial",
        "contracting_form": "direta",
        "criteria": (_criterion(),),
        "status": "valid",
        "would_block": False,
        "art39_signaled": False,
        "created_by": "staff-op21",
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return LegitimacyScreenRecord(**fields)  # type: ignore[arg-type]


def test_recibo_fechado_critério_resultado_referência_sobrevive_ao_round_trip() -> None:
    """O recibo persistível é exatamente critério→resultado→referência — e volta inteiro."""
    record = _record()
    restored = LegitimacyScreenRecord.model_validate_json(record.model_dump_json())
    assert restored == record
    assert restored.criteria[0].evidence_refs[0].document_sha256 == _SHA
    assert restored.operation == "legitimacy_screen"


def test_referência_tipada_recusa_bytes_e_abuso_de_locator() -> None:
    """Anti-custódia: conteúdo embutido, locator longo e sha ruim não atravessam a forma."""
    with pytest.raises(ValidationError):
        _ref(content=b"%PDF-1.7 bytes do contrato")  # type: ignore[unknown-kwargs]
    with pytest.raises(ValidationError):
        _ref(documento="qualquer coisa que fosse o arquivo")  # type: ignore[unknown-kwargs]
    with pytest.raises(ValidationError):
        _ref(locator="x" * 257)  # acima do teto de 256 — a cerca de abuso do desenho §3
    with pytest.raises(ValidationError):
        _ref(locator="custodia/contrato.pdf?página=1")  # charset fechado
    with pytest.raises(ValidationError):
        _ref(document_sha256="zz")  # não é hex-64
    assert len(_ref(locator="x" * 256).locator) == 256  # o teto exato passa


def test_canal_nunca_é_custodiante_representável() -> None:
    """Parecer §3: o `kind` é atribuição de custódia (art. 12), nunca papel de canal."""
    for kind in ("corretor", "canal", "maezo", "operadora/administradora"):
        with pytest.raises(ValidationError):
            _ref(kind=kind)  # type: ignore[arg-type]


def test_âncora_carrega_a_forma_canônica_e_recusa_as_imprecisas() -> None:
    """Parecer §1/§4: forma longa canônica passa; as formas imprecisas são recusa LOUD."""
    canônica = "RN 489/2022, art. 23-A (redação dada pela RN 659/2025)"
    assert (
        LegitimacyCriterion(
            criterion_id="penalidade_regime", result="not_applicable", norm_anchor=canônica
        ).norm_anchor
        == canônica
    )
    for imprecisa in PROHIBITED_ANCHOR_FRAGMENTS:
        with pytest.raises(ValidationError):
            _criterion(norm_anchor=imprecisa)
        with pytest.raises(ValidationError):
            _criterion(norm_anchor=f"contratação pela {imprecisa} e mais texto")


def test_formas_de_citação_por_artigo_proprio_da_rn_557_passam() -> None:
    """Contratação cita RN 557/2022 com artigo próprio (parecer §4) — todas representáveis."""
    for anchor in (
        "RN 557/2022, art. 5º",
        "RN 557/2022, art. 15",
        "RN 557/2022, art. 16",
        "RN 557/2022, art. 29",
        "RN 557/2022, art. 29, § 1º",
        "RN 557/2022, art. 10",
        "RN 557/2022, art. 39",
        "RN 557/2022, arts. 9º a 14",
    ):
        assert _criterion(norm_anchor=anchor).norm_anchor == anchor


def test_domínios_fechados_sem_quarto_valor() -> None:
    """Sem quarto tipo/forma/resultado/estado — e `pending` é resultado declarado, não inferido."""
    with pytest.raises(ValidationError):
        _record(contract_kind="coletivo_por_adesao")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _record(contracting_form="com_corretor")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _criterion(result="assumed")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _record(status="approved")  # type: ignore[arg-type]
    assert (
        LegitimacyCriterion(
            criterion_id="reconfirmacao", result="pending", norm_anchor="RN 557/2022, art. 10"
        ).evidence_refs
        == ()
    )
    pendente = _record(criteria=(_criterion(result="pending", evidence_refs=()),), status="pending")
    assert pendente.status == "pending"


def test_art39_é_par_exclusivo_com_a_âncora_literal() -> None:
    """Sinalizada ⟺ âncora "RN 557/2022, art. 39" (espelho do CHECK da 0030, desenho §3/§6)."""
    assert ART39_ANCHOR == "RN 557/2022, art. 39"  # a constante e o Literal são a mesma âncora
    sinalizada = _record(art39_signaled=True, art39_anchor=ART39_ANCHOR, would_block=True, status="invalid")
    assert sinalizada.art39_anchor == "RN 557/2022, art. 39"
    with pytest.raises(ValidationError):
        _record(art39_signaled=True)  # sinal sem âncora
    with pytest.raises(ValidationError):
        _record(art39_anchor=ART39_ANCHOR)  # âncora sem sinal
    with pytest.raises(ValidationError):
        _record(art39_signaled=True, art39_anchor="RN 659/2025, art. 39")  # âncora estranha


def test_instante_ingênuo_e_bloco_vazio_são_recusa_e_recibo_é_imutável() -> None:
    """Vigência sem offset não é fato; verificação sem critério não é verificação."""
    now = datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(ValidationError):
        _record(reconfirmation_due_at=datetime(2027, 10, 26, 12, 0, 0))  # naive (art. 10)
    with pytest.raises(ValidationError):
        _record(criteria=())
    with pytest.raises(ValidationError):
        _record(updated_at=datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC), created_at=now)
    record = _record()
    with pytest.raises(ValidationError):
        record.status = "invalid"  # type: ignore[misc]
