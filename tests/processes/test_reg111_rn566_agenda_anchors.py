"""Static regression pin for REG-1.11 (RN 566/2022 agenda/priorization anchors).

REG-1.11 (JOURNEYS-COMPLETENESS-2026-10, REG-RESIDUAL-AUDIT-v1.md §3.10/§4 item 7; plan
docs/plans/maezo-completion-program.md W1 row ``:77``, closes F-M12; owner decision
2026-10-28 in COORDINATION.md "OWNER DECISIONS 2026-10-28" item 5: "REG: 1.11+1.12
EXECUTAR (verbatim já banked: RN-566-2022.md + LGPD)"): the attendance-guarantee
deadlines of RN 566/2022 (prazos máximos para a efetivação do atendimento na agenda
credenciada) existed in the tracked corpus ONLY as (a) the RN 623/2024 art. 12 § 1º
precedence rule inside the CA_1 REG-1.2 anchor and (b) DRAFT/verify citations in
SP-OP-CRED-001 — never as anchors on the agenda/priorization elements themselves.
This wave anchors the banked verbatim (docs/compliance/legal/textos-base/RN-566-2022.md,
sha256 49c550bc…, MANIFEST 16/16, acquired by #1054 e68cefad) on the EDITABLE corpus:

- ``U1_B_DiaServico`` (CA_2 v4) — the "Dia do serviço" boundary timer is the agenda's
  EFETIVA REALIZAÇÃO instant: art. 3º, § 1º counts the deadline from the demanda to the
  efetiva realização (documentation-only; the timer expression is pinned unchanged).
- ``S3_T_Priorizar`` (CA_3 v4) — the prioritization element treats RN 566/2022
  attendance-guarantee deadlines (art. 3º) and the art. 10 integral reimbursement as
  documentary risk inputs, never as modeled SLAs (PT4H proposal preserved).
- ``SP-OP-AUTH-001`` (contract) — art. 11 (prior authorization must viabilize art. 3º)
  and art. 12 (protocol) anchored DARK; the RN 259/395 currency of that contract stays
  in the R5 queue (rn-currency-review.md §3.4), NOT decided here.

Fences respected (asserted below): CA_1 v4 is LEITURA-ONLY (plan §0(1) + D-30) — zero
REG-1.11 lines land there; the v3 files are intocáveis; agents never fill ratification
blocks (ADR-0007) — the SME jurídico gate over deadline classes is registered, not
simulated.

What is asserted (static ElementTree/text only — no engine, no new dependencies):

(a) The CA_2 anchor: ``U1_B_DiaServico`` documentation cites "RN 566/2022", "art. 3º",
    the § 1º realization count, the banked textos-base pointer, and preserves the
    pre-existing "Origem: ES_Boundary_SemCheckin" suffix byte-identically.
(b) The CA_3 anchor: ``S3_T_Priorizar`` documentation cites "RN 566/2022", "art. 3º" and
    "art. 10", and preserves the pre-existing "Origem: AH_T_Priorizar" suffix.
(c) Documentation-only proof: the pinned timers keep their exact event definitions
    (``U1_B_DiaServico`` = one interruptive timeDate ``${dataHoraServico}`` attached to
    ``U1_T_CheckIn``; ``S3_T_Priorizar`` boundary = one non-interruptive PT4H) — the
    anchors added zero timer/flow/element structure.
(d) Corpus-wide refusal of the wrong year form "RN 566/2023" in ANY v4 documentation.
(e) Corpus-wide stale-norm refusal: v4 documentation citing the superseded
    garantia-de-atendimento norm "RN 259/2011" WITHOUT co-citing "RN 566/2022" fails
    (RN 566/2022 art. 16, I revoked RN 259/2011 — banked RN-566-2022.md:205-207).
(f) The SP-OP-AUTH-001 REG-1.11 block exists (DARK framing + art. 11 verbatim pointer +
    the R5 boundary citation to rn-currency-review.md §3.4).
(g) Fence proof: CA_1 v4 carries NO "REG-1.11" anchor (broker fence respected) while
    still holding the pre-existing REG-1.2 RN 566/2022 precedence citation.

Evidence anchors: REG-RESIDUAL-AUDIT-v1.md §3.10/§4 item 7 (snapshot sha256
5f25e8a4490eb26eccea0ff52efcd51f184d52bc32714ce85db3f301154c0597, cp+sha256 before
consumption) · CORRECOES_BPMN_2026-10.md §14 · MAPA-ELEMENTOS-V4.md §12. Any failure
here means a REG-1.11 anchor regressed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
CAMUNDA_NS = "http://camunda.org/schema/1.0/bpmn"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

CORPUS_DIR = Path(__file__).resolve().parents[2] / "docs" / "processes" / "New_BPMN" / "Adapted_BPMN"
CA1_PATH = CORPUS_DIR / "CA_1_COMPRA (com suporte)_v4_adaptado.bpmn"
CA2_PATH = CORPUS_DIR / "CA_2_UTILIZACAO_v4_adaptado.bpmn"
CA3_PATH = CORPUS_DIR / "CA_3_SUPORTE_v4_adaptado.bpmn"
AUTH_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "processes" / "contracts" / "SP-OP-AUTH-001.md"
)

CA2_ANCHOR_ID = "U1_B_DiaServico"
CA3_ANCHOR_ID = "S3_T_Priorizar"

#: Exact citation forms pinned by REG-1.11 (wording mínimo e exato; casefolded).
CURRENT_NORM = "rn 566/2022"
REALIZATION_COUNT = "efetiva realização"
TEXTO_BASE_POINTER = "rn-566-2022.md"
CA2_ORIGIN_SUFFIX = "origem: es_boundary_semcheckin"
CA3_ORIGIN_SUFFIX = "origem: ah_t_priorizar"
#: Wrong year form — refused corpus-wide.
WRONG_YEAR_FORM = "rn 566/2023"
#: Superseded garantia-de-atendimento norm (revoked by RN 566/2022 art. 16, I) — only
#: citable WITH the current norm in the same text.
SUPERSEDED_NORM = "rn 259/2011"


def _load(path: Path) -> ET.Element:
    assert path.is_file(), f"missing BPMN under pin: {path}"
    return ET.parse(path).getroot()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _documentation_of(root: ET.Element, element_id: str) -> str:
    elements = [el for el in root.iter() if el.get("id") == element_id]
    assert elements, f"{element_id} is missing from the BPMN"
    docs = [" ".join(el.itertext()) for el in elements[0].findall(f"{{{BPMN_NS}}}documentation")]
    assert docs, f"{element_id} has no documentation"
    return _norm(" | ".join(docs))


def _documentation_texts(root: ET.Element) -> list[str]:
    """Every bpmn:documentation text in the file, whitespace-normalized."""
    return [
        re.sub(r"\s+", " ", doc.text).strip() for doc in root.iter(f"{{{BPMN_NS}}}documentation") if doc.text
    ]


def _wrong_year_offenders(texts: list[str]) -> list[str]:
    return [t[:160] for t in texts if WRONG_YEAR_FORM in _norm(t)]


def _stale_norm_offenders(texts: list[str]) -> list[str]:
    """Texts citing the superseded norm without the current norm beside them."""
    out = []
    for text in texts:
        folded = _norm(text)
        if SUPERSEDED_NORM not in folded:
            continue
        if CURRENT_NORM in folded:
            continue
        out.append(text[:160])
    return out


def test_ca2_dia_do_servico_anchor_cites_rn566_realization_deadline() -> None:
    """(a) U1_B_DiaServico documentation anchors the art. 3º realization deadline."""
    docs = _documentation_of(_load(CA2_PATH), CA2_ANCHOR_ID)
    assert CURRENT_NORM in docs, f"anchor must cite '{CURRENT_NORM}'; got: {docs[:240]}"
    assert "art. 3º" in docs, f"anchor must cite 'art. 3º'; got: {docs[:240]}"
    assert REALIZATION_COUNT in docs, f"anchor must carry the § 1º count phrase; got: {docs[:240]}"
    assert TEXTO_BASE_POINTER in docs, f"anchor must point to the banked textos-base; got: {docs[:240]}"
    assert CA2_ORIGIN_SUFFIX in docs, f"anchor lost the pre-existing origin suffix: {docs[:240]}"


def test_ca3_priorizar_anchor_cites_rn566_deadlines_and_reimbursement() -> None:
    """(b) S3_T_Priorizar documentation anchors art. 3º deadlines + art. 10 refund."""
    docs = _documentation_of(_load(CA3_PATH), CA3_ANCHOR_ID)
    assert CURRENT_NORM in docs, f"anchor must cite '{CURRENT_NORM}'; got: {docs[:240]}"
    assert "art. 3º" in docs, f"anchor must cite 'art. 3º'; got: {docs[:240]}"
    assert "art. 10" in docs, f"anchor must cite 'art. 10' (reembolso integral); got: {docs[:240]}"
    assert TEXTO_BASE_POINTER in docs, f"anchor must point to the banked textos-base; got: {docs[:240]}"
    assert CA3_ORIGIN_SUFFIX in docs, f"anchor lost the pre-existing origin suffix: {docs[:240]}"


def test_anchors_are_documentation_only_timers_unchanged() -> None:
    """(c) The pinned event definitions are structurally untouched by the anchors."""
    ca2 = _load(CA2_PATH)
    boundaries = [el for el in ca2.iter(f"{{{BPMN_NS}}}boundaryEvent") if el.get("id") == CA2_ANCHOR_ID]
    assert boundaries, f"{CA2_ANCHOR_ID} vanished from CA_2 v4"
    boundary = boundaries[0]
    assert boundary.get("attachedToRef") == "U1_T_CheckIn"
    assert boundary.get("cancelActivity") == "true"
    timers = boundary.findall(f"{{{BPMN_NS}}}timerEventDefinition")
    assert len(timers) == 1, f"{CA2_ANCHOR_ID} must keep exactly one timer definition"
    expr = timers[0].find(f"{{{BPMN_NS}}}timeDate")
    assert expr is not None and expr.text == "${dataHoraServico}"

    ca3 = _load(CA3_PATH)
    slas = [el for el in ca3.iter(f"{{{BPMN_NS}}}boundaryEvent") if el.get("id") == "S3_B_SLA_Priorizar"]
    assert slas, "S3_B_SLA_Priorizar vanished from CA_3 v4"
    sla_timers = slas[0].findall(f"{{{BPMN_NS}}}timerEventDefinition")
    assert len(sla_timers) == 1
    dur = sla_timers[0].find(f"{{{BPMN_NS}}}timeDuration")
    assert dur is not None and dur.text == "PT4H"
    # The prioritization task itself stays a human User Task (no automation added).
    tasks = [el for el in ca3.iter(f"{{{BPMN_NS}}}userTask") if el.get("id") == CA3_ANCHOR_ID]
    assert tasks, f"{CA3_ANCHOR_ID} must remain a userTask"


def test_no_wrong_year_form_in_v4_documentation() -> None:
    """(d) Corpus sweep: 'RN 566/2023' never cited in any v4 documentation."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(CORPUS_DIR.glob("*_v4_adaptado.bpmn")):
        bad = _wrong_year_offenders(_documentation_texts(_load(path)))
        if bad:
            offenders[path.name] = bad
    assert not offenders, f"wrong year form '{WRONG_YEAR_FORM}' found in the v4 corpus: {offenders}"


def test_superseded_rn259_only_cited_with_rn566() -> None:
    """(e) Corpus sweep: RN 259/2011 only inside revocation framing anchored on RN 566/2022."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(CORPUS_DIR.glob("*_v4_adaptado.bpmn")):
        bad = _stale_norm_offenders(_documentation_texts(_load(path)))
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        f"'{SUPERSEDED_NORM}' cited without '{CURRENT_NORM}' in the same v4 documentation "
        f"(RN 566/2022 art. 16, I revoked it): {offenders}"
    )


def test_auth_contract_carries_dark_reg111_block_with_r5_boundary() -> None:
    """(f) SP-OP-AUTH-001 hosts the DARK REG-1.11 block with the R5 boundary citation."""
    assert AUTH_CONTRACT_PATH.is_file(), f"missing contract: {AUTH_CONTRACT_PATH}"
    text = _norm(AUTH_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert "reg-1.11" in text, "SP-OP-AUTH-001 lost the REG-1.11 block"
    assert "dark" in text, "the REG-1.11 block must keep its DARK framing"
    assert "art. 11" in text, "the REG-1.11 block must anchor RN 566/2022 art. 11"
    assert TEXTO_BASE_POINTER in text, "the REG-1.11 block must point to the banked textos-base"
    assert "rn-currency-review.md" in text and "§3.4" in text, (
        "the REG-1.11 block must keep the R5 boundary citation (rn-currency-review.md §3.4)"
    )


def test_ca1_fence_respected_no_reg111_anchor() -> None:
    """(g) CA_1 v4 is LEITURA-ONLY: zero REG-1.11 anchors; REG-1.2 citation preserved."""
    assert CA1_PATH.is_file(), f"missing BPMN: {CA1_PATH}"
    texts = _documentation_texts(_load(CA1_PATH))
    offenders = [t[:160] for t in texts if "reg-1.11" in _norm(t)]
    assert not offenders, f"CA_1 v4 is broker-fenced (plan §0(1), D-30): REG-1.11 found there: {offenders}"
    joined = _norm(" ".join(texts))
    assert CURRENT_NORM in joined, "CA_1 v4 lost its pre-existing REG-1.2 RN 566/2022 citation"
