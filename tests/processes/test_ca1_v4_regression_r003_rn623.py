"""R-003 (REG-1.2, PEND-046) — re-ancoragem RN 395/2016 -> RN 623/2024 no CA_1 v4.

A RN 623/2024 (DOU 19/12/2024, Seção 1, p. 285) revogou a RN 395/2016 no art. 28 e entrou em
vigor em 1º/7/2025 (art. 29) — texto-base em ``docs/compliance/legal/textos-base/RN-623-2024.md``.
O pacote D-30 (handover ii, #952) já re-ancorou ``C3_EV_Expirou`` no art. 12 e trocou o timer
PT48H -> PT168H, mas preservou intocada a anotação histórica, que citava "RN 395/2016" sem
dizer que ela está revogada. Este pacote acrescenta só a qualificação "(revogada pela RN
623/2024, art. 28)" a essa citação: nenhum timer, fluxo, elemento ou DI muda (CORRECOES §16).

O que é cercado (estático, sem engine):

(a) Corpus v4 inteiro: toda documentation que cita "RN 395" também diz que ela foi revogada
    (a mesma forma do DoD do R-003: ``grep "RN 395" ... | grep -v revog`` vazio).
(b) ``C3_EV_Expirou`` mantém as 2 documentações, o sufixo de rastreio "| Origem: M2_EV_Expirou",
    a âncora RN 623/2024 art. 12 e o timer PT168H (prova de que a emenda é só de citação).
(c) A anotação histórica continua lá como registro (não foi apagada): ela ainda nomeia a RN
    395/2016, agora qualificada como revogada.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from maezo.platform.xml_safe import parse_xml_without_dtd

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"

CA1_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "processes"
    / "New_BPMN"
    / "Adapted_BPMN"
    / "CA_1_COMPRA (com suporte)_v4_adaptado.bpmn"
)
CORPUS_DIR = CA1_PATH.parent
TIMER_ID = "C3_EV_Expirou"
REVOKED_FORM = "RN 395/2016 (revogada pela RN 623/2024, art. 28)"


def _load(path: Path = CA1_PATH) -> ET.Element:
    assert path.is_file(), f"BPMN sob pin ausente: {path}"
    return parse_xml_without_dtd(path.read_bytes())


def _docs(element: ET.Element) -> list[str]:
    return [re.sub(r"\s+", " ", d.text or "").strip() for d in element.findall(f"{{{BPMN_NS}}}documentation")]


def _stale_rn395(texts: list[str]) -> list[str]:
    """Documentações que citam a RN 395 sem dizer que ela foi revogada."""
    return [t[:160] for t in texts if "RN 395" in t and "revog" not in t.casefold()]


def test_corpus_v4_nunca_cita_rn395_sem_revogacao() -> None:
    """(a) Nenhuma documentation do v4 cita a RN 395 como se estivesse vigente."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(CORPUS_DIR.glob("*_v4_adaptado.bpmn")):
        root = _load(path)
        texts = [t for el in root.iter() for t in _docs(el)]
        bad = _stale_rn395(texts)
        if bad:
            offenders[path.name] = bad
    assert not offenders, f"RN 395 citada sem revogação no corpus v4: {offenders}"


def test_c3_ev_expirou_emenda_so_de_citacao() -> None:
    """(b) Timer, documentações e sufixo de rastreio intactos; âncora RN 623 art. 12 presente."""
    root = _load()
    hits = [el for el in root.iter(f"{{{BPMN_NS}}}intermediateCatchEvent") if el.get("id") == TIMER_ID]
    assert len(hits) == 1, f"{TIMER_ID} ausente ou duplicado"
    docs = _docs(hits[0])
    assert len(docs) == 2, f"{TIMER_ID} deveria manter 2 documentações; tem {len(docs)}"
    assert docs[0].endswith("| Origem: M2_EV_Expirou"), "sufixo de rastreio do v3 perdido"
    assert 'RN 623/2024 art. 12, II, "a"' in docs[1], "âncora RN 623/2024 art. 12 perdida"
    durations = [
        (td.text or "").strip()
        for ted in hits[0].findall(f"{{{BPMN_NS}}}timerEventDefinition")
        for td in ted.findall(f"{{{BPMN_NS}}}timeDuration")
    ]
    assert durations == ["PT168H"], f"timer de {TIMER_ID} mudou: {durations}"


def test_anotacao_historica_preservada_e_qualificada() -> None:
    """(c) O registro histórico continua, agora dizendo que a RN 395/2016 está revogada."""
    root = _load()
    el = next(e for e in root.iter(f"{{{BPMN_NS}}}intermediateCatchEvent") if e.get("id") == TIMER_ID)
    first = _docs(el)[0]
    assert first.startswith("⚠️ PD-01 / I-01:"), "anotação histórica PD-01 removida ou reescrita"
    assert REVOKED_FORM in first, f"citação histórica sem a qualificação de revogação: {first[:200]}"
