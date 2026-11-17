"""E-SF-36 (EP-REG-34) — RN 413 art. 4º disclosure checklist, fallback variant (guard tests).

Plan row E-SF-36, record REG-34 / NORM-GAP-10. Each guard injects the violation REG-34 names
and asserts the typed refusal:

- `test_disclosure_checklist_cannot_claim_complete_without_guia`: status forced to a complete
  claim with the Guia slot empty → `fonte_normativa_pendente_rn413_art18`.
- `test_disclosure_items_i_to_ix_present_before_decision_button`: inciso VII (and every other
  item of I–IX/§1º) absent before the decision → `disclosure_incompleto_rn413_art4`. maezo has
  no buyer UI button: the "decision button" here is the server-side precondition of the C6-4
  `acceptance.record` `decision: accept` (`require_disclosure_before_decision`). It is NOT
  wired into `CommercialGateway` — the pinned C6-4 terms carry no per-inciso content (GAP,
  asserted by `test_checklist_slots_are_candidates_absent_from_the_pinned_wire`).

The pinned `tests/candidate_artifacts/commercial/contract-c6-4.json` is only read.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import get_args

import pytest

from maezo.gateway import commercial_disclosure_checklist as checklist_module
from maezo.gateway.commercial_disclosure_checklist import (
    DISCLOSURE_STATUS_PENDING,
    GUIA_ART18_SLOT,
    GUIA_ART18_SOURCE,
    PUBLIC_PRICE_FIELD,
    RN413_ART4_CHECKLIST,
    DisclosureChecklistError,
    DisclosureRefusal,
    DisclosureStatus,
    claim_status,
    require_disclosure_before_decision,
)

REPO = Path(__file__).resolve().parents[3]
CONTRACT = REPO / "tests/candidate_artifacts/commercial/contract-c6-4.json"

_ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX")


def _full_terms() -> dict[str, object]:
    """Synthetic content for every I–IX/§1º slot; VII is a public price in integer cents."""
    terms: dict[str, object] = {
        item.terms_field: f"Texto sintético de {item.dispositivo}" for item in RN413_ART4_CHECKLIST
    }
    terms[PUBLIC_PRICE_FIELD] = 45_990
    return terms


def _refusal(exc: pytest.ExceptionInfo[DisclosureChecklistError]) -> DisclosureRefusal:
    return exc.value.reason


# --- REG-34 guard 1 ------------------------------------------------------------------------


@pytest.mark.parametrize("claim", ["completo", "complete", "COMPLETO", "disclosure_completo", "ok", ""])
def test_disclosure_checklist_cannot_claim_complete_without_guia(claim: str) -> None:
    assert GUIA_ART18_SOURCE is None  # GAP-10 open: the Guia slot is empty by construction
    with pytest.raises(DisclosureChecklistError) as exc:
        claim_status(claim)
    assert _refusal(exc) is DisclosureRefusal.FONTE_NORMATIVA_PENDENTE
    assert str(exc.value) == "fonte_normativa_pendente_rn413_art18"

    # The pending status is the only representable one, and stays so with I–IX/§1º present.
    assert get_args(DisclosureStatus) == ("incompleto_fonte_pendente",)
    assert claim_status(DISCLOSURE_STATUS_PENDING) == "incompleto_fonte_pendente"
    assert require_disclosure_before_decision(_full_terms(), decision="accept") == "incompleto_fonte_pendente"

    # A filled Guia slot is not a way around it: no source exists to fill it.
    with pytest.raises(DisclosureChecklistError) as filled:
        require_disclosure_before_decision(
            {**_full_terms(), GUIA_ART18_SLOT.terms_field: "itens do Guia"}, decision="accept"
        )
    assert _refusal(filled) is DisclosureRefusal.FONTE_NORMATIVA_PENDENTE


# --- REG-34 guard 2 ------------------------------------------------------------------------


@pytest.mark.parametrize("item", RN413_ART4_CHECKLIST, ids=lambda item: item.terms_field)
def test_disclosure_items_i_to_ix_present_before_decision_button(item) -> None:
    assert require_disclosure_before_decision(_full_terms(), decision="accept") == DISCLOSURE_STATUS_PENDING

    absent = _full_terms()
    del absent[item.terms_field]
    with pytest.raises(DisclosureChecklistError) as missing:
        require_disclosure_before_decision(absent, decision="accept")
    assert _refusal(missing) is DisclosureRefusal.DISCLOSURE_INCOMPLETO
    assert str(missing.value) == "disclosure_incompleto_rn413_art4"

    for blank in (None, "", "   ", "​﻿"):
        with pytest.raises(DisclosureChecklistError) as empty:
            require_disclosure_before_decision({**_full_terms(), item.terms_field: blank}, decision="accept")
        assert _refusal(empty) is DisclosureRefusal.DISCLOSURE_INCOMPLETO

    # A decline is never blocked by the disclosure gate (it finalizes nothing).
    assert require_disclosure_before_decision(absent, decision="decline") == DISCLOSURE_STATUS_PENDING


def test_unknown_decision_refuses() -> None:
    with pytest.raises(DisclosureChecklistError) as exc:
        require_disclosure_before_decision(_full_terms(), decision="maybe")  # type: ignore[arg-type]
    assert _refusal(exc) is DisclosureRefusal.DISCLOSURE_INCOMPLETO


# --- acceptance: 1:1 mapping, verbatim or GAP ----------------------------------------------


def test_checklist_maps_i_to_ix_and_par1_one_to_one_to_terms_fields() -> None:
    dispositivos = [item.dispositivo for item in RN413_ART4_CHECKLIST]
    assert dispositivos == [f"art. 4º, {numeral}" for numeral in _ROMAN] + ["art. 4º, §1º"]
    fields = [item.terms_field for item in RN413_ART4_CHECKLIST]
    assert len(set(fields)) == len(fields) == 10
    assert GUIA_ART18_SLOT.terms_field not in fields
    assert RN413_ART4_CHECKLIST[6].terms_field == PUBLIC_PRICE_FIELD

    by_dispositivo = {item.dispositivo: item for item in RN413_ART4_CHECKLIST}
    # Incisos V and VI are elided in NORMATIVE-BASELINE-v2 §2.3: GAP, never written from memory.
    for gap in ("art. 4º, V", "art. 4º, VI"):
        assert by_dispositivo[gap].verbatim is None
        assert by_dispositivo[gap].source.startswith("GAP")
    for item in RN413_ART4_CHECKLIST:
        if item.dispositivo not in {"art. 4º, V", "art. 4º, VI"}:
            assert item.verbatim and item.source.startswith("RN 413/2016")
    assert by_dispositivo["art. 4º, VII"].verbatim == "formação do preço"
    assert GUIA_ART18_SLOT.verbatim and "artigo 18" in GUIA_ART18_SLOT.verbatim


def test_checklist_slots_are_candidates_absent_from_the_pinned_wire() -> None:
    """GAP evidence: C6-4 `offer.terms` carries no per-inciso field; the slots are successor candidates."""
    contract = CONTRACT.read_text(encoding="utf-8")
    assert "rn413" not in contract.casefold()
    definitions = json.loads(contract)["$defs"]
    assert set(definitions["OfferTermsPayload"]["properties"]) == {
        "quote_ref",
        "source_revision",
        "terms_digest",
    }
    assert set(definitions["HostedTermsDocument"]["properties"]) == {"url", "expires_at"}


# --- boundary: inciso VII = only the public price fact (Q7 open, CADE) ---------------------


@pytest.mark.parametrize(
    "value",
    [
        {"preco_centavos": 45_990, "comissao_centavos": 1_000},
        {"componentes": [{"faixa": "a", "valor": 1}]},
        "R$ 459,90",
        459.90,
        True,
        0,
        -1,
        None,
    ],
    ids=["commission", "components", "text", "float", "bool", "zero", "negative", "none"],
)
def test_inciso_vii_carries_only_the_public_price_fact(value: object) -> None:
    with pytest.raises(DisclosureChecklistError) as exc:
        require_disclosure_before_decision({**_full_terms(), PUBLIC_PRICE_FIELD: value}, decision="accept")
    assert _refusal(exc) is DisclosureRefusal.DISCLOSURE_INCOMPLETO


@pytest.mark.parametrize(
    "extra",
    [
        {"terms.comissao_corretor_centavos": 1_000},
        {"terms.comparativo_operadoras": [{"operadora": "B", "preco_centavos": 40_000}]},
        {PUBLIC_PRICE_FIELD + ".componentes": {"administracao": 1}},
    ],
    ids=["remuneration", "cross_operator", "price_components"],
)
def test_price_formation_and_cross_operator_content_have_no_slot(extra: dict[str, object]) -> None:
    """The shape is closed: price-formation detail or a cross-operator comparison has no slot.

    A text-level comparator check over slot copy is EP-REG-07a (E-SF-36r), out of this fallback.
    """
    with pytest.raises(DisclosureChecklistError) as exc:
        require_disclosure_before_decision({**_full_terms(), **extra}, decision="accept")
    assert _refusal(exc) is DisclosureRefusal.DISCLOSURE_INCOMPLETO


# --- acceptance: no surface or log claims a complete RN 413 disclosure ---------------------

_CLAIM = re.compile(
    r"disclosure[\s_\-:=]*(?:(?:e|esta|is|status)[\s_\-:=]+)?complet"
    r"|(?<![a-z])complet[a-z]*[\s_\-:]*(?:(?:de|da|do|com|per|under)[\s_\-]+)?"
    r"(?:rn|resolucao normativa)[\s_\-n.o]*413"
)
_SCANNED_SUFFIXES = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".json",
        ".yaml",
        ".yml",
        ".md",
        ".txt",
        ".html",
        ".j2",
        ".dmn",
        ".bpmn",
        ".toml",
    }
)
_SKIPPED_DIRS = frozenset({"node_modules", "__pycache__", "dist", "build", ".mypy_cache"})


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").casefold()


def _claims(text: str) -> bool:
    return _CLAIM.search(_fold(text)) is not None


@pytest.mark.parametrize(
    "sample",
    [
        "disclosure completo RN 413",
        "Disclosure está completo",
        "DISCLOSURE_COMPLETE",
        "log: disclosure_status=completo",
        "Checklist completo da RN nº 413",
        "complete per RN 413",
    ],
)
def test_claim_lint_catches_a_completeness_claim(sample: str) -> None:
    assert _claims(sample)


def test_no_surface_or_log_claims_complete_rn413_disclosure() -> None:
    # The module's own vocabulary never reads as a claim.
    for token in (DISCLOSURE_STATUS_PENDING, *(reason.value for reason in DisclosureRefusal)):
        assert not _claims(token)
    assert not _claims(Path(checklist_module.__file__).read_text(encoding="utf-8"))

    offenders = []
    for root in (REPO / "src", REPO / "spec"):
        for path in root.rglob("*"):
            if path.suffix not in _SCANNED_SUFFIXES or not path.is_file():
                continue
            if _SKIPPED_DIRS.intersection(path.relative_to(REPO).parts):
                continue
            if _claims(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(path.relative_to(REPO)))
    assert offenders == []
