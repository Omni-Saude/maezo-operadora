"""Static regression pin for REG-1.4 (CA_2 v4) — TISS obligation anchor citation form.

REG-1.4 (JOURNEYS-COMPLETENESS-2026-10, REG-RESIDUAL-AUDIT-v1.md §4 item 8): the
four v4 journeys carried ZERO mentions of the TISS obligation (grep case-insensitive
"tiss" = 0/0/0/0) and the RX8 bridge lived only in the gitignored audit bundle. This
wave anchors the obligation on the EDITABLE half of the corpus: the documentation of
``U4_T_Liquidar`` in ``CA_2_UTILIZACAO_v4_adaptado.bpmn`` now cites the padrão TISS
norm "RN 501/2022" (the norm that revoked RN 305/2012 + RN 341/2013 and hosts the
Componente de Comunicação — docs/compliance/rn-currency-review.md §2.5) and the RX8
registry entry (PROVIDER-CAPABILITY-DESIGN: where RN 503/2022 art. 6º applies, the
channel is the TISS Communication Component 04.03.00, competência set/2026; the
receipt is registered, never attested). Fixed 2026-10-27 with a single documentation
block; no other element, attribute or DI touched.

BROKER PENDENCY (registered, not fixable here): the other named anchor target,
``C3_T_Requisitar``, lives in ``CA_1_COMPRA (com suporte)_v4_adaptado.bpmn``, which
is LEITURA-ONLY under the permanent fence §0(1) of
docs/plans/maezo-completion-program.md ("CA_1 exige broker via COORDINATION",
decision D-30). Its TISS anchor is NOT applied in this wave; the formal disposition
lives in MAPA-ELEMENTOS-V4.md §11 and CORRECOES_BPMN_2026-10.md §13. This pin only
asserts the pending element still exists so the broker packet finds its target.

What is asserted (static ElementTree only — no engine, no new dependencies):

(a) The pinned anchor: ``U4_T_Liquidar`` exists as a task in CA_2 v4 and its
    documentation cites the canonical norm form "RN 501/2022", the RX8 pointer and
    the "Componente de Comunicação TISS" channel — all in one documentation block.

(b) File-wide + corpus-wide refusal of the wrong year form "RN 501/2024" (the
    GOV-0.5 defective citation) in ANY documentation of the four v4 files.

(c) Stale-norm refusal: any documentation text citing the superseded TISS norms
    ("RN 305/2012", "RN 305/2016", "RN 341/2013") WITHOUT co-citing "RN 501/2022"
    in the same text fails — the superseded norms may only appear inside a
    revocation framing anchored on the current norm (rn-currency-review.md §2.5).

(d) Broker pendency: ``C3_T_Requisitar`` exists in CA_1 v4 (existence only — when
    the broker packet applies its anchor, this assertion still holds).

Evidence anchors: REG-RESIDUAL-AUDIT-v1.md §3.3/§4 item 8 · RX8 source snapshot
sha256 64980b42da1693343cb927c874086e791d45dc551495f9fa87d270877cea72f5
(docs/audits/PROVIDER-XP-2026-10/PROVIDER-CAPABILITY-DESIGN.md, cp+sha256 before
consumption) · CORRECOES_BPMN_2026-10.md §13 · MAPA-ELEMENTOS-V4.md §11. Any
failure here means the REG-1.4 anchor regressed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"

CA2_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "processes"
    / "New_BPMN"
    / "Adapted_BPMN"
    / "CA_2_UTILIZACAO_v4_adaptado.bpmn"
)
CA1_PATH = CA2_PATH.parent / "CA_1_COMPRA (com suporte)_v4_adaptado.bpmn"
CORPUS_DIR = CA2_PATH.parent

ANCHOR_ID = "U4_T_Liquidar"
PENDING_BROKER_ID = "C3_T_Requisitar"

#: Exact citation forms pinned by REG-1.4 (wording mínimo e exato; casefolded).
CURRENT_NORM = "rn 501/2022"
RX8_POINTER = "rx8"
TISS_CHANNEL = "componente de comunicação tiss"
#: Wrong year form (GOV-0.5 defect) — refused corpus-wide.
WRONG_YEAR_FORM = "rn 501/2024"
#: Superseded TISS norms — only citable WITH the current norm in the same text.
SUPERSEDED_NORMS = ("rn 305/2012", "rn 305/2016", "rn 341/2013")


def _load(path: Path = CA2_PATH) -> ET.Element:
    assert path.is_file(), f"missing BPMN under pin: {path}"
    return ET.parse(path).getroot()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _documentation_texts(root: ET.Element) -> list[str]:
    """Every bpmn:documentation text in the file, whitespace-normalized."""
    return [
        re.sub(r"\s+", " ", doc.text).strip() for doc in root.iter(f"{{{BPMN_NS}}}documentation") if doc.text
    ]


def _wrong_year_offenders(texts: list[str]) -> list[str]:
    """Documentation texts carrying the GOV-0.5 wrong year form."""
    return [t[:160] for t in texts if WRONG_YEAR_FORM in _norm(t)]


def _stale_norm_offenders(texts: list[str]) -> list[str]:
    """Texts citing superseded TISS norms without the current norm beside them."""
    out = []
    for text in texts:
        folded = _norm(text)
        if not any(norm in folded for norm in SUPERSEDED_NORMS):
            continue
        if CURRENT_NORM in folded:
            continue
        out.append(text[:160])
    return out


def test_anchor_u4_t_liquidar_cites_tiss_obligation_and_rx8() -> None:
    """(a) The pinned anchor cites the padrão TISS norm, RX8 and the channel."""
    root = _load()
    anchors = [el for el in root.iter(f"{{{BPMN_NS}}}task") if el.get("id") == ANCHOR_ID]
    assert anchors, f"{ANCHOR_ID} (the REG-1.4 anchor) is missing"
    docs = [d.text or "" for d in anchors[0].findall(f"{{{BPMN_NS}}}documentation")]
    assert docs, f"{ANCHOR_ID} documentation missing"
    joined = _norm(" | ".join(docs))
    assert CURRENT_NORM in joined, (
        f"{ANCHOR_ID} documentation must cite the canonical form 'RN 501/2022'; got: {joined[:240]}"
    )
    assert RX8_POINTER in joined, (
        f"{ANCHOR_ID} documentation must point to the RX8 registry entry; got: {joined[:240]}"
    )
    assert TISS_CHANNEL in joined, (
        f"{ANCHOR_ID} documentation must name the TISS channel "
        f"('Componente de Comunicação TISS'); got: {joined[:240]}"
    )
    # The pre-existing REG-1.5(b) provenance suffix must survive the anchor.
    assert "origem: [sem origem]" in joined, (
        f"{ANCHOR_ID} documentation lost its provenance suffix '| Origem:': {joined[:240]}"
    )


def test_no_wrong_year_form_in_ca2_documentation() -> None:
    """(b) File-wide refusal: 'RN 501/2024' never cited in CA_2 v4."""
    offenders = _wrong_year_offenders(_documentation_texts(_load()))
    assert not offenders, (
        f"wrong year form 'RN 501/2024' (GOV-0.5 defect) found in CA_2 v4 documentation: {offenders}"
    )


def test_corpus_v4_free_of_wrong_year_form() -> None:
    """(b) Corpus sweep: 'RN 501/2024' absent from all four v4 files."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(CORPUS_DIR.glob("*_v4_adaptado.bpmn")):
        bad = _wrong_year_offenders(_documentation_texts(_load(path)))
        if bad:
            offenders[path.name] = bad
    assert not offenders, f"wrong year form 'RN 501/2024' found in the v4 corpus: {offenders}"


def test_superseded_tiss_norms_only_cited_with_current_norm() -> None:
    """(c) Corpus sweep: superseded norms only inside revocation framing."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(CORPUS_DIR.glob("*_v4_adaptado.bpmn")):
        bad = _stale_norm_offenders(_documentation_texts(_load(path)))
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        "superseded TISS norms (RN 305/2012, RN 305/2016, RN 341/2013) cited "
        f"without 'RN 501/2022' in the same documentation: {offenders}"
    )


def test_pending_broker_anchor_target_still_present_in_ca1() -> None:
    """(d) Broker pendency: C3_T_Requisitar exists, untouched, awaiting broker."""
    root = _load(CA1_PATH)
    pending = [el for el in root.iter() if el.get("id") == PENDING_BROKER_ID]
    assert pending, (
        f"{PENDING_BROKER_ID} vanished from CA_1 v4 — the REG-1.4 broker pendency "
        "(MAPA-ELEMENTOS-V4.md §11) lost its target"
    )
    # Existence is the only claim: the anchor itself is the broker packet's job.
