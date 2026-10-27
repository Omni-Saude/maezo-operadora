"""Static regression pin for REG-1.7 (CA_0 v4) — LGPD anchor citation form.

REG-1.7 (JOURNEYS-COMPLETENESS-2026-10, REG-RESIDUAL-AUDIT-v1.md §4 item 1): the
documentation of ``EC_CA_H1`` in ``CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn``
cited "LGPD art. 37" as a legal basis ("Base legal por fonte mapeada no registro
de tratamento (LGPD art. 37)."). In Lei 13.709/2018 the art. 37 disciplines the
REGISTRY of processing operations ("O controlador e o operador devem manter
registro das operações de tratamento de dados pessoais que realizarem…"), while
the LEGAL BASES are art. 7º (personal data) and art. 11 (sensitive data). Fixed
2026-10-27 with a single documentation block; no other element touched.

What is asserted (static ElementTree only — no engine, no new dependencies):

(a) The pinned anchor: ``EC_CA_H1`` exists as a callActivity (calledElement
    ``Process_CA_H1_IdentidadeConsentimento``) and its documentation cites the
    bases correctly — "arts. 7º e 11 da Lei 13.709/2018" — AND frames art. 37 in
    the registry role ("Registro das operações de tratamento: art. 37").

(b) File-wide refusal: in CA_0 v4, EVERY ``bpmn:documentation`` text that mentions
    "art. 37" must ALSO carry the explicit bases citation AND the registry framing.
    Any "art. 37" mention without that co-occurrence fails — i.e. "art. 37" can
    never again be cited as a legal basis in this file (the REG17-F01 defect form).

(c) Corpus sweep: the same refusal predicate over the other three v4 files
    (currently 0 occurrences of "art. 37" — pinned so a future edit that
    reintroduces the defective form anywhere in the v4 corpus is caught).

Evidence anchors: REG-RESIDUAL-AUDIT-v1.md §3.6/§4 item 1 · primary source
https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm (access
2026-10-27) · CORRECOES_BPMN_2026-10.md §11. Any failure here means the REG-1.7
citation repair regressed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"

#: File under pin. Tracked on main; a missing file is itself a regression.
CA0_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "processes"
    / "New_BPMN"
    / "Adapted_BPMN"
    / "CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn"
)
CORPUS_DIR = CA0_PATH.parent

ANCHOR_ID = "EC_CA_H1"
CALLED_ELEMENT = "Process_CA_H1_IdentidadeConsentimento"
#: Exact citation forms pinned by REG-1.7 (wording mínimo e exato).
BASES_FORM = "arts. 7º e 11 da lei 13.709/2018"
REGISTRY_FORM = "registro das operações de tratamento: art. 37"
DEFECTIVE_SUBSTRING = "registro de tratamento (lgpd art. 37)"


def _load(path: Path = CA0_PATH) -> ET.Element:
    assert path.is_file(), f"missing BPMN under pin: {path}"
    return ET.parse(path).getroot()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _documentation_texts(root: ET.Element) -> list[str]:
    """Every bpmn:documentation text in the file, whitespace-normalized."""
    return [
        re.sub(r"\s+", " ", doc.text).strip()
        for doc in root.iter(f"{{{BPMN_NS}}}documentation")
        if doc.text
    ]


def _defect_offenders(texts: list[str]) -> list[str]:
    """Documentation texts citing "art. 37" outside the pinned correct form."""
    out = []
    for text in texts:
        folded = _norm(text)
        if "art. 37" not in folded:
            continue
        if BASES_FORM in folded and REGISTRY_FORM in folded:
            continue
        out.append(text[:160])
    return out


def test_anchor_block_cites_bases_and_registry_correctly() -> None:
    """(a) The pinned anchor cites the bases and the registry role of art. 37."""
    root = _load()
    anchors = [
        el
        for el in root.iter(f"{{{BPMN_NS}}}callActivity")
        if el.get("id") == ANCHOR_ID
    ]
    assert anchors, f"{ANCHOR_ID} (the REG-1.7 anchor) is missing"
    assert anchors[0].get("calledElement") == CALLED_ELEMENT, (
        f"{ANCHOR_ID} calledElement drifted: {anchors[0].get('calledElement')}"
    )
    docs = [d.text or "" for d in anchors[0].findall(f"{{{BPMN_NS}}}documentation")]
    assert docs, f"{ANCHOR_ID} documentation missing"
    joined = _norm(" | ".join(docs))
    assert BASES_FORM in joined, (
        f"{ANCHOR_ID} documentation must cite the bases as "
        f"'arts. 7º e 11 da Lei 13.709/2018'; got: {joined[:200]}"
    )
    assert REGISTRY_FORM in joined, (
        f"{ANCHOR_ID} documentation must frame art. 37 as the registry of "
        f"processing operations ('Registro das operações de tratamento: art. 37'); "
        f"got: {joined[:200]}"
    )
    assert DEFECTIVE_SUBSTRING not in joined, (
        f"the REG17-F01 defective form resurfaced in {ANCHOR_ID}: {joined[:200]}"
    )


def test_no_art_37_cited_as_legal_basis_filewide() -> None:
    """(b) File-wide refusal: "art. 37" only inside the pinned correct form."""
    texts = _documentation_texts(_load())
    offenders = _defect_offenders(texts)
    assert not offenders, (
        "'art. 37' cited outside the registry+bases correct form (REG17-F01 "
        f"pattern) in CA_0 v4 documentation: {offenders}"
    )


def test_corpus_v4_free_of_defective_art_37_form() -> None:
    """(c) Corpus sweep: the defective form is absent from all four v4 files."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(CORPUS_DIR.glob("*_v4_adaptado.bpmn")):
        root = _load(path)
        bad = _defect_offenders(_documentation_texts(root))
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        f"defective 'art. 37' citation form found in the v4 corpus: {offenders}"
    )
