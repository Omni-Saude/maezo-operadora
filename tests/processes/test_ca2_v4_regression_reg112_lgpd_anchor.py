"""Static regression pin for REG-1.12 (CA_2 v4) — LGPD hypothesis anchor form.

REG-1.12 (JOURNEYS-COMPLETENESS-2026-10, REG-RESIDUAL-AUDIT-v1.md §3.11/§4 item 6):
the clinical-event -> client communication flow existed WITHOUT any LGPD basis
mapping (`CA_2 v4:257-273`, sub-process ``UT_ES_EventoClinico``: message start
``UT_EC_Start`` -> ``UT_EC_Comunicar`` -> ``UT_EC_End``; F-M15: the PHI firewall
covers commercial INPUTS only, not this flow). Fixed 2026-10-28 with ONE prefixed
documentation sentence on ``UT_EC_Comunicar``; no other element, attribute or DI
touched (structural counts pinned below).

HOUSE RULE (audit, verbatim): "Nao inventar base: documentar o candidato e o gate."
The anchor documents the CANDIDATE devices (arts. 7º and 11 of Lei 13.709/2018)
for the hypothesis "comunicacao de evento clinico ao proprio titular = tratamento
de dado pessoal sensivel", states that NO basis is asserted, and points to the
COUNSEL gate via docs/review-queue.md. It deliberately does NOT name any inciso
(the inciso-level choice is the lawyer's, not the model's) and does NOT mention
art. 37 (whose registry-only framing is pinned by REG-1.7 in CA_0 and would drag
the co-occurrence forms across files for no REG-1.12 purpose).

What is asserted (static ElementTree only — no engine, no new dependencies):

(a) The pinned anchor: ``UT_EC_Comunicar`` exists as an intermediateThrowEvent
    wired to ``Message_CA_ComunicarCliente`` with the ``comunicarClientePublisher``
    delegate preserved, and its documentation carries the exact hypothesis forms —
    the REG-1.12 tag, "NÃO ratificada", the COUNSEL gate, the candidate bases
    "arts. 7º e 11 da Lei 13.709/2018" (same bases form pinned by REG-1.7), the
    no-assertion sentence and the review-queue pointer.

(b) Provenance suffix preserved byte-exact: the documentation still ends with
    "| Origem: M3_F01 → M3_T_Classificar" (the REG-1.5(b)/§13 house convention).

(c) M3-scoped fail-closed: inside the ``UT_ES_EventoClinico`` sub-process, ANY
    documentation text mentioning "base legal" must carry the hypothesis tag, the
    COUNSEL gate and the no-assertion sentence — an uncited/affirmative basis in
    the clinical-event block fails (the F-M15 defect class cannot come back as a
    "quietly affirmed" basis).

(d) Structural identity of the emenda: the pinned element counts of CA_2 v4 are
    unchanged (documentation-only delta; same class as REG-1.4/§13, DI untouched).

(e) Corpus coherence with REG-1.7: any CA_2 v4 documentation text mentioning
    "art. 37" must co-cite the bases AND the registry framing (0 occurrences
    today — pinned so the REG17-F01 defective form cannot be introduced here).

Negative proof: the predicate helpers are exercised against defective synthetic
strings below, AND the pin was run against the pre-fix baseline (anchor absent)
where assertion (a) fails — the guard has teeth (recorded in
CORRECOES_BPMN_2026-10.md §14).

Evidence anchors: REG-RESIDUAL-AUDIT-v1.md §3.11/§4 item 6 · primary source
https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm (access
2026-10-28; law text byte-identical to the banked VENDOR-XP snapshots of
2026-10-06 — the historical hash divergence is the post-</html> F5 anti-bot
beacon, see §14) · CORRECOES_BPMN_2026-10.md §14 · docs/review-queue.md row
"REG-1.12". Any failure here means the REG-1.12 hypothesis anchor regressed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
CAMUNDA_NS = "http://camunda.org/schema/1.0/bpmn"

#: File under pin. Tracked on main; a missing file is itself a regression.
CA2_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "processes"
    / "New_BPMN"
    / "Adapted_BPMN"
    / "CA_2_UTILIZACAO_v4_adaptado.bpmn"
)
CORPUS_DIR = CA2_PATH.parent

ANCHOR_ID = "UT_EC_Comunicar"
SUBPROCESS_ID = "UT_ES_EventoClinico"
MESSAGE_REF = "Message_CA_ComunicarCliente"
DELEGATE = "${comunicarClientePublisher}"

#: Exact citation forms pinned by REG-1.12 (wording mínimo e exato; casefolded).
HYPOTHESIS_TAG = "hipótese reg-1.12"
NOT_RATIFIED_FORM = "não ratificada"
GATE_FORM = "gate counsel"
BASES_FORM = "arts. 7º e 11 da lei 13.709/2018"
NO_ASSERTION_FORM = "nenhuma base é afirmada"
QUEUE_FORM = "docs/review-queue.md"
#: Provenance suffix from the REG-1.5(b) wave, preserved byte-exact (raw case).
ORIGEM_SUFFIX = "| Origem: M3_F01 → M3_T_Classificar"

#: REG-1.7 co-occurrence forms for the (e) corpus coherence predicate.
REG17_BASES_FORM = BASES_FORM
REG17_REGISTRY_FORM = "registro das operações de tratamento: art. 37"

#: Pinned structural counts of CA_2 v4 (baseline sha256 b85afa4d1633dfde1…,
#: pre-anchor; a documentation-only emenda must not move any of them).
PINNED_COUNTS: dict[str, int] = {
    "process": 5,
    "subProcess": 3,
    "task": 5,
    "userTask": 2,
    "serviceTask": 2,
    "callActivity": 4,
    "startEvent": 8,
    "endEvent": 16,
    "intermediateThrowEvent": 9,
    "intermediateCatchEvent": 2,
    "boundaryEvent": 7,
    "exclusiveGateway": 11,
    "parallelGateway": 2,
    "inclusiveGateway": 0,
    "eventBasedGateway": 1,
    "sequenceFlow": 63,
    "messageFlow": 10,
    "documentation": 104,
    "lane": 12,
    "laneSet": 5,
    "dataObject": 10,
    "dataObjectReference": 10,
    "dataStoreReference": 1,
}


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


def _m3_documentation_texts(root: ET.Element) -> list[str]:
    """Documentation texts scoped to the UT_ES_EventoClinico sub-process."""
    subs = [el for el in root.iter(f"{{{BPMN_NS}}}subProcess") if el.get("id") == SUBPROCESS_ID]
    assert subs, f"sub-process {SUBPROCESS_ID} (the M3 block) is missing"
    return [
        re.sub(r"\s+", " ", doc.text).strip()
        for doc in subs[0].iter(f"{{{BPMN_NS}}}documentation")
        if doc.text
    ]


def _art37_offenders(texts: list[str]) -> list[str]:
    """Documentation texts citing "art. 37" outside the REG-1.7 correct form."""
    out = []
    for text in texts:
        folded = _norm(text)
        if "art. 37" not in folded:
            continue
        if REG17_BASES_FORM in folded and REG17_REGISTRY_FORM in folded:
            continue
        out.append(text[:160])
    return out


def test_anchor_documentation_carries_hypothesis_forms() -> None:
    """(a) The pinned anchor documents the CANDIDATE bases and the COUNSEL gate."""
    root = _load()
    anchors = [el for el in root.iter(f"{{{BPMN_NS}}}intermediateThrowEvent") if el.get("id") == ANCHOR_ID]
    assert anchors, f"{ANCHOR_ID} (the REG-1.12 anchor) is missing"
    anchor = anchors[0]
    definitions = [el for el in anchor if el.tag.endswith("messageEventDefinition")]
    assert definitions, f"{ANCHOR_ID} lost its messageEventDefinition"
    assert definitions[0].get("messageRef") == MESSAGE_REF, (
        f"{ANCHOR_ID} messageRef drifted: {definitions[0].get('messageRef')}"
    )
    assert definitions[0].get(f"{{{CAMUNDA_NS}}}delegateExpression") == DELEGATE, (
        f"{ANCHOR_ID} delegate drifted: {definitions[0].get(f'{{{CAMUNDA_NS}}}delegateExpression')}"
    )
    docs = [d.text or "" for d in anchor.findall(f"{{{BPMN_NS}}}documentation")]
    assert docs, f"{ANCHOR_ID} documentation missing"
    joined = _norm(" | ".join(docs))
    for form in (
        HYPOTHESIS_TAG,
        NOT_RATIFIED_FORM,
        GATE_FORM,
        BASES_FORM,
        NO_ASSERTION_FORM,
        QUEUE_FORM,
    ):
        assert form in joined, f"{ANCHOR_ID} documentation must carry '{form}'; got: {joined[:240]}"


def test_anchor_provenance_suffix_preserved() -> None:
    """(b) The Origem provenance suffix survives byte-exact."""
    root = _load()
    anchors = [el for el in root.iter(f"{{{BPMN_NS}}}intermediateThrowEvent") if el.get("id") == ANCHOR_ID]
    assert anchors, f"{ANCHOR_ID} is missing"
    docs = [d.text or "" for d in anchors[0].findall(f"{{{BPMN_NS}}}documentation")]
    assert docs, f"{ANCHOR_ID} documentation missing"
    assert ORIGEM_SUFFIX in docs[0], (
        f"provenance suffix '{ORIGEM_SUFFIX}' was not preserved byte-exact; got tail: {docs[0][-120:]}"
    )


def test_m3_block_rejects_uncited_legal_basis() -> None:
    """(c) Any 'base legal' mention inside M3 carries the hypothesis + gate forms."""
    texts = _m3_documentation_texts(_load())
    assert any("base legal" in _norm(t) for t in texts), (
        "the REG-1.12 anchor is expected to be the M3 'base legal' mention; none "
        "found — the anchor was removed or reworded away from this predicate"
    )
    offenders = []
    for text in texts:
        folded = _norm(text)
        if "base legal" not in folded:
            continue
        if HYPOTHESIS_TAG in folded and GATE_FORM in folded and NO_ASSERTION_FORM in folded:
            continue
        offenders.append(text[:160])
    assert not offenders, (
        "'base legal' asserted in the M3 clinical-event block without the "
        f"hypothesis+gate framing (F-M15 defect class): {offenders}"
    )


def test_emenda_is_documentation_only() -> None:
    """(d) The REG-1.12 emenda moved zero elements, flows or DI objects."""
    root = _load()
    for tag, expected in PINNED_COUNTS.items():
        found = sum(1 for _ in root.iter(f"{{{BPMN_NS}}}{tag}"))
        assert found == expected, (
            f"structural drift in CA_2 v4: <{tag}> count {found} != pinned {expected} "
            "(the REG-1.12 emenda is documentation-only)"
        )


def test_ca2_free_of_defective_art_37_form() -> None:
    """(e) Coherence with REG-1.7: 'art. 37' only inside the registry framing."""
    offenders = _art37_offenders(_documentation_texts(_load()))
    assert not offenders, (
        "'art. 37' cited outside the registry+bases correct form (REG17-F01 "
        f"pattern) in CA_2 v4 documentation: {offenders}"
    )


def test_predicates_reject_defective_forms() -> None:
    """Negative proof inside the file: the guards have teeth on synthetic input."""
    defective_basis = "Base legal: art. 11, II, 'f' da Lei 13.709/2018. | Origem: M3_F01"
    folded = _norm(defective_basis)
    assert "base legal" in folded
    assert not (HYPOTHESIS_TAG in folded and GATE_FORM in folded and NO_ASSERTION_FORM in folded), (
        "predicate must reject an affirmative uncited basis"
    )
    defective_art37 = "Base legal por fonte mapeada no registro de tratamento (LGPD art. 37)."
    assert _art37_offenders([defective_art37]), "art. 37 predicate must reject the REG17-F01 defective form"
    correct_art37 = (
        "Bases legais por fonte mapeada: arts. 7º e 11 da Lei 13.709/2018. "
        "Registro das operações de tratamento: art. 37."
    )
    assert not _art37_offenders([correct_art37]), "art. 37 predicate must accept the REG-1.7 correct form"
