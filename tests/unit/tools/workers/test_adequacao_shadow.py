"""The `adequacao_gap` CANDIDATE shadow: divergence evidence, refusal, and non-influence.

WHAT THIS MODULE IS FOR. `spec/processes/dmn/adequacao_gap.dmn` is KNOWN WRONG and OPEN
(PLANS.md:150-159 §0.5.4 item 1; docs/review-queue.md:160): under `hitPolicy=FIRST`,
`r_eletivo_leve` (:90-99) precedes `r_conforme` (:110-119), and `r_conforme` declares NO
time/distance ceiling — so an arbitrarily bad ELECTIVE access reads `CONFORME`. Per ADR-0028
(docs/adr/0028-dmn-evaluation-engine-side.md:214-216) engineering NEVER corrects the table; the
regulatory owner does, in spec. `spec/processes/dmn/adequacao-gap-shadow-candidate.yaml` is the
correction as DATA, and this module is the evidence the owner ratifies against.

Grouped by the property under test:

  1. LIVE-TABLE READER — a minimal FIRST-hit evaluator over the REAL `adequacao_gap.dmn`, so every
     "the live table says CONFORME" below is DERIVED FROM THE ARTIFACT, not asserted in prose. It
     is anchored to the two LIVE-ENGINE-VERIFIED data points already pinned in
     `test_adequacao.py` (45min -> GAP_LEVE, 70min -> CONFORME), and it REFUSES any input-entry
     shape it does not understand, so a future table cannot pass through it unread.
  2. THE FLAW, ENUMERATED — the divergence corpus: inputs where the live table says CONFORME and
     the candidate says gap. This doubles as the ratification evidence.
  3. STRUCTURAL FACTS about the live table that make (2) a property rather than a sample.
  4. MANIFEST <-> PYTHON EQUIVALENCE — the YAML is the source of truth; `evaluate_candidate` is a
     transcription. Proven over a dense grid with the SAME entry-matcher used in (1), so a drift in
     any threshold, output, rule id or ORDER fails.
  5. REFUSAL — the candidate cannot be enforced while unratified, and the shipped
     `PLACEHOLDER_*` accountability values cannot be mistaken for a ratification.
  5b. LIVE-TABLE BINDING — the ratification is bound to the live table's BYTES, re-derived from disk
     on every load, so a ratification given to one version of `adequacao_gap.dmn` can never silently
     carry over to edited table content. This is the `src/`-side half of the binding that
     `tests/unit/spec/test_shadow_candidates_common.py` pins (at test time) for all six manifests;
     this manifest is the only one with a `src/` consumer, so it is the only one that also has it at
     LOAD time.
  6. NON-INFLUENCE — the shadow can never change `route_remediation`'s verdict or routing:
     neutralised, raising, or real evaluator all yield identical outcomes over a behaviour matrix.
  7. NON-PHI — the divergence event carries only verdicts, the five rule inputs and a rule id.
  8. THE ACTIVATION PATH IS REAL — the candidate is invisible to artifact deployment/validation,
     and both it and the live table are CODEOWNERS-gated.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
import structlog.testing
import yaml

from maezo.platform.deploy.engine_deploy import collect_artifacts, resolve_spec_processes_dir
from maezo.tools.workers import adequacao as adequacao_module
from maezo.tools.workers.adequacao import route_remediation
from maezo.tools.workers.adequacao_shadow import (
    CANDIDATE_EMITS_CONFORME,
    CANDIDATE_RULE_ORDER,
    ELETIVO_DISTANCIA_MAX_KM,
    ELETIVO_TEMPO_MAX_MIN,
    LIVE_TABLE_FILENAME,
    LIVE_TABLE_RELPATH,
    MANIFEST_FILENAME,
    SHADOW_DIVERGENCE_EVENT,
    URGENCIA_DISTANCIA_MAX_KM,
    URGENCIA_TEMPO_MAX_MIN,
    CandidateRatification,
    EnforcementNotRatifiedError,
    evaluate_candidate,
    evaluate_for_enforcement,
    live_table_digest,
    live_table_path,
    load_candidate_ratification,
    record_shadow_divergence,
    shadow_divergence_event,
)
from maezo.tools.workers.dmn_transport import FakeDmnTransport

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DMN_DIR = _REPO_ROOT / "spec" / "processes" / "dmn"
_LIVE_TABLE = _DMN_DIR / "adequacao_gap.dmn"
_CANDIDATE_MANIFEST = _DMN_DIR / MANIFEST_FILENAME
_CODEOWNERS = _REPO_ROOT / ".github" / "CODEOWNERS"

#: The live table's input columns, in declaration order (adequacao_gap.dmn:33-47).
_INPUT_ORDER: tuple[str, ...] = (
    "tipo_carater",
    "tempo_acesso_apurado_min",
    "distancia_apurada_km",
    "prestadores_disponiveis",
    "cobertura_geo_suficiente",
)


# =================================================================================================
# 1. LIVE-TABLE READER
# =================================================================================================


class UnreadableEntryError(RuntimeError):
    """An input-entry shape this reader does not understand — never silently treated as a match."""


def _matches(entry: str, value: Any) -> bool:
    """Does one DMN input entry match `value`?

    Supports EXACTLY the entry grammar `adequacao_gap.dmn` (and the candidate manifest) uses:
    `-` (wildcard), a quoted string literal, `true`/`false`, a bare number, and the `>` / `<=`
    unary comparisons. Anything else RAISES: a reader that silently returned False on an entry it
    could not parse would turn a table change into a quietly wrong corpus.
    """
    text = entry.strip()
    if text == "-":
        return True
    if text.startswith('"') and text.endswith('"'):
        inner = text[1:-1]
        if '"' in inner.replace('\\"', ""):
            # A second, unescaped quote inside the literal is not a single string — it is a FEEL
            # comma-disjunction (e.g. `"eletivo","urgencia_emergencia"`), which this reader does not
            # support. Refuse rather than silently comparing `value` against the mangled literal
            # (which would just read as a permanent non-match, i.e. False for every input).
            raise UnreadableEntryError(
                f"unsupported DMN input entry (quoted literal contains an unescaped '\"' — "
                f"comma-disjunction?): {entry!r}"
            )
        return value == inner
    if text in ("true", "false"):
        return value is (text == "true")
    for op in (">=", "<=", ">", "<"):
        if text.startswith(op):
            operand = float(text[len(op) :].strip())
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            if op == ">":
                return value > operand
            if op == "<":
                return value < operand
            if op == ">=":
                return value >= operand
            return value <= operand
    try:
        operand = float(text)
    except ValueError as exc:
        raise UnreadableEntryError(f"unsupported DMN input entry: {entry!r}") from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == operand


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _live_rules() -> list[tuple[str, list[str], str]]:
    """(rule id, input entries in column order, `gap_adequacao` output) read off the REAL table."""
    root = ET.parse(_LIVE_TABLE).getroot()
    table = next(el for el in root.iter() if _local(el.tag) == "decisionTable")
    assert table.get("hitPolicy") == "FIRST", "this reader assumes FIRST-hit; the table changed"
    rules: list[tuple[str, list[str], str]] = []
    for rule in (el for el in table if _local(el.tag) == "rule"):
        entries = [
            (next((c.text for c in el if _local(c.tag) == "text"), "") or "").strip()
            for el in rule
            if _local(el.tag) == "inputEntry"
        ]
        outputs = [
            (next((c.text for c in el if _local(c.tag) == "text"), "") or "").strip()
            for el in rule
            if _local(el.tag) == "outputEntry"
        ]
        rules.append((rule.get("id") or "", entries, outputs[0].strip('"')))
    return rules


def live_verdict(
    *,
    tipo_carater: str,
    tempo_acesso_apurado_min: int,
    distancia_apurada_km: float,
    prestadores_disponiveis: int,
    cobertura_geo_suficiente: bool,
) -> tuple[str, str]:
    """FIRST-hit evaluation of the REAL live table -> (gap_adequacao, rule id)."""
    values = (
        tipo_carater,
        tempo_acesso_apurado_min,
        distancia_apurada_km,
        prestadores_disponiveis,
        cobertura_geo_suficiente,
    )
    for rule_id, entries, output in _live_rules():
        assert len(entries) == len(values), f"{rule_id}: column arity changed"
        if all(_matches(entry, value) for entry, value in zip(entries, values, strict=True)):
            return output, rule_id
    raise AssertionError("live table matched no rule — it has a catch-all, so this cannot happen")


def test_live_reader_reproduces_the_live_engine_verified_pair() -> None:
    """NON-VACUITY ANCHOR. The reader is only evidence if it agrees with the real engine.

    Both data points below were verified against the live CIB Seven engine and are already pinned
    in `test_adequacao.py` (`test_adequacao_gap_conforme_now_leve` /
    `test_adequacao_gap_conforme_reachable_beyond_leve_gate`) and restated in
    docs/review-queue.md:160 as the minimal pair isolating the defect — identical `distancia`,
    `prestadores` and `cobertura`, only `tempo` differs.
    """
    common = {"distancia_apurada_km": 10.0, "prestadores_disponiveis": 2, "cobertura_geo_suficiente": True}
    better, better_rule = live_verdict(tipo_carater="eletivo", tempo_acesso_apurado_min=45, **common)
    worse, worse_rule = live_verdict(tipo_carater="eletivo", tempo_acesso_apurado_min=70, **common)

    assert (better, better_rule) == ("GAP_LEVE", "r_eletivo_leve")
    assert (worse, worse_rule) == ("CONFORME", "r_conforme")
    # The inversion in one line: the WORSE access gets the BETTER label.
    assert worse == "CONFORME" and better != "CONFORME"


@pytest.mark.parametrize(
    "entry",
    [
        "[1..10]",
        'not("eletivo")',
        '"a","b"',
    ],
)
def test_live_reader_refuses_an_entry_shape_it_cannot_parse(entry: str) -> None:
    """Non-vacuity for the refusal itself — an unreadable entry must never read as 'no match'.

    `'"a","b"'` is the FEEL comma-disjunction shape (e.g. `"eletivo","urgencia_emergencia"`): it
    both starts AND ends with `"`, so a reader that only checked those two characters would read it
    as a (wrong) single-string literal and silently return `False` instead of refusing.
    """
    with pytest.raises(UnreadableEntryError):
        _matches(entry, 5)


# =================================================================================================
# 2. THE FLAW, ENUMERATED — the divergence corpus (ratification evidence)
# =================================================================================================

#: (label, inputs, expected LIVE verdict, expected CANDIDATE verdict, expected candidate rule).
#: The live verdict is ALSO recomputed from the artifact below, so a wrong literal here fails.
_DIVERGENCIAS: tuple[tuple[str, dict[str, Any], str, str, str], ...] = (
    (
        "eletivo 120min/100km — arbitrarily bad elective access reads CONFORME (the headline case)",
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 120,
            "distancia_apurada_km": 100.0,
            "prestadores_disponiveis": 1,
            "cobertura_geo_suficiente": True,
        },
        "CONFORME",
        "GAP_CRITICO",
        "c_eletivo_tempo_acima_teto",
    ),
    (
        "eletivo 61min — one minute past the table's OWN declared elective ceiling (60)",
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 61,
            "distancia_apurada_km": 5.0,
            "prestadores_disponiveis": 3,
            "cobertura_geo_suficiente": True,
        },
        "CONFORME",
        "GAP_CRITICO",
        "c_eletivo_tempo_acima_teto",
    ),
    (
        "eletivo 50.1km — one tenth of a km past the table's OWN declared elective ceiling (50.0)",
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 30,
            "distancia_apurada_km": 50.1,
            "prestadores_disponiveis": 3,
            "cobertura_geo_suficiente": True,
        },
        "CONFORME",
        "GAP_CRITICO",
        "c_eletivo_distancia_acima_teto",
    ),
    (
        "eletivo 600min/900km — unbounded: there is no elective ceiling at all in the live table",
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 600,
            "distancia_apurada_km": 900.0,
            "prestadores_disponiveis": 1,
            "cobertura_geo_suficiente": True,
        },
        "CONFORME",
        "GAP_CRITICO",
        "c_eletivo_tempo_acima_teto",
    ),
    (
        "ACHADO-1: tipo_carater='' (what the worker sends when the variable is absent, "
        "adequacao.py:297) masks the table's OWN declared conservative catch-all",
        {
            "tipo_carater": "",
            "tempo_acesso_apurado_min": 300,
            "distancia_apurada_km": 400.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
        },
        "CONFORME",
        "GAP_CRITICO",
        "c_catchall",
    ),
    (
        "ACHADO-1: an unmapped tipo_carater literal reaches CONFORME just the same",
        {
            "tipo_carater": "ambulatorial",
            "tempo_acesso_apurado_min": 45,
            "distancia_apurada_km": 15.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
        },
        "CONFORME",
        "GAP_CRITICO",
        "c_catchall",
    ),
)


@pytest.mark.parametrize(
    ("label", "inputs", "live_expected", "candidate_expected", "candidate_rule"), _DIVERGENCIAS
)
def test_live_says_conforme_where_the_candidate_says_gap(
    label: str,
    inputs: dict[str, Any],
    live_expected: str,
    candidate_expected: str,
    candidate_rule: str,
) -> None:
    """THE RATIFICATION EVIDENCE. Each row: the live table's verdict (read off the artifact) vs the
    candidate's, on an input where the difference is the OPEN defect."""
    live, live_rule = live_verdict(**inputs)
    assert live == live_expected, f"{label}: live table read as {live} via {live_rule}"

    candidate = evaluate_candidate(**inputs)
    assert candidate.gap_adequacao == candidate_expected, label
    assert candidate.regra == candidate_rule, label
    assert live != candidate.gap_adequacao, f"{label}: no divergence — the corpus row is vacuous"


def test_the_divergence_corpus_is_not_all_of_the_table() -> None:
    """COUNTER-PROOF OF NON-VACUITY: the candidate is not "everything is a gap".

    Without this, a candidate that verdicted GAP_CRITICO unconditionally would satisfy every row of
    the corpus above and still be worthless. These are inputs where live and candidate AGREE.
    """
    agreements = (
        # within the elective ceiling: GAP_LEVE on both
        (
            {
                "tipo_carater": "eletivo",
                "tempo_acesso_apurado_min": 45,
                "distancia_apurada_km": 10.0,
                "prestadores_disponiveis": 2,
                "cobertura_geo_suficiente": True,
            },
            "GAP_LEVE",
        ),
        # within the urgencia ceiling: GAP_LEVE on both
        (
            {
                "tipo_carater": "urgencia_emergencia",
                "tempo_acesso_apurado_min": 25,
                "distancia_apurada_km": 20.0,
                "prestadores_disponiveis": 4,
                "cobertura_geo_suficiente": True,
            },
            "GAP_LEVE",
        ),
        # urgencia past its ceiling: GAP_CRITICO on both (this rule was never broken)
        (
            {
                "tipo_carater": "urgencia_emergencia",
                "tempo_acesso_apurado_min": 45,
                "distancia_apurada_km": 20.0,
                "prestadores_disponiveis": 4,
                "cobertura_geo_suficiente": True,
            },
            "GAP_CRITICO",
        ),
        # zero providers: GAP_CRITICO on both
        (
            {
                "tipo_carater": "eletivo",
                "tempo_acesso_apurado_min": 10,
                "distancia_apurada_km": 1.0,
                "prestadores_disponiveis": 0,
                "cobertura_geo_suficiente": True,
            },
            "GAP_CRITICO",
        ),
        # insufficient coverage: GAP_MODERADO on both
        (
            {
                "tipo_carater": "eletivo",
                "tempo_acesso_apurado_min": 10,
                "distancia_apurada_km": 1.0,
                "prestadores_disponiveis": 2,
                "cobertura_geo_suficiente": False,
            },
            "GAP_MODERADO",
        ),
    )
    for inputs, expected in agreements:
        live, _ = live_verdict(**inputs)
        candidate = evaluate_candidate(**inputs)
        assert live == expected == candidate.gap_adequacao, inputs


# =================================================================================================
# 3. STRUCTURAL FACTS about the live table (why section 2 is a property, not a sample)
# =================================================================================================


def test_live_r_conforme_has_no_ceiling_and_follows_r_eletivo_leve() -> None:
    """The defect, read straight off the artifact: `r_conforme` wildcards tipo/tempo/distancia and
    is evaluated AFTER `r_eletivo_leve` under FIRST."""
    rules = _live_rules()
    ids = [rule_id for rule_id, _, _ in rules]
    assert ids.index("r_eletivo_leve") < ids.index("r_conforme")

    entries = dict(zip(_INPUT_ORDER, next(e for i, e, _ in rules if i == "r_conforme"), strict=True))
    assert entries["tipo_carater"] == "-"
    assert entries["tempo_acesso_apurado_min"] == "-"
    assert entries["distancia_apurada_km"] == "-"
    assert entries["prestadores_disponiveis"] == "> 0"
    assert entries["cobertura_geo_suficiente"] == "true"


def test_live_table_has_no_elective_ceiling_rule_at_all() -> None:
    """Both `*_critico` time/distance rules gate on `urgencia_emergencia` (:52, :62), so nothing
    can catch a bad ELECTIVE access before it reaches the gateless `r_conforme`."""
    ceilinged = [
        rule_id
        for rule_id, entries, output in _live_rules()
        if output == "GAP_CRITICO"
        and any(e.startswith(">") for e in entries[1:3])  # a tempo/distancia upper bound
    ]
    assert ceilinged == ["r_urgencia_critico", "r_urgencia_distancia_critico"]
    for rule_id, entries, _ in _live_rules():
        if rule_id in ceilinged:
            assert entries[0] == '"urgencia_emergencia"'


def test_live_conforme_reachable_for_eletivo_only_above_its_own_declared_ceiling() -> None:
    """THE INVERSION AS A PROPERTY, swept over a grid rather than sampled.

    For tipo_carater='eletivo' with prestadores>0 and cobertura=true:
        live verdict == CONFORME  <=>  (tempo > 60 OR distancia > 50.0)
    i.e. `CONFORME` is emitted EXACTLY on the inputs that BREACH the elective ceiling the table
    itself declares, and never on the inputs that satisfy it.
    """
    for tempo in (0, 30, 59, 60, 61, 90, 200):
        for distancia in (0.0, 25.0, 49.9, 50.0, 50.1, 75.0, 400.0):
            live, _ = live_verdict(
                tipo_carater="eletivo",
                tempo_acesso_apurado_min=tempo,
                distancia_apurada_km=distancia,
                prestadores_disponiveis=2,
                cobertura_geo_suficiente=True,
            )
            breaches = tempo > ELETIVO_TEMPO_MAX_MIN or distancia > ELETIVO_DISTANCIA_MAX_KM
            assert (live == "CONFORME") is breaches, (tempo, distancia, live)


def test_live_conforme_is_unreachable_for_urgencia_achado_2() -> None:
    """ACHADO-2: the same rule ordering, seen from the other side — a PERFECT urgencia access never
    reads CONFORME (it is GAP_LEVE, hence a monitoring plan), while an arbitrarily bad elective one
    does (hence NO monitoring plan). More attention to the best case, none to the worst."""
    for tempo in (0, 15, 30, 31, 90):
        for distancia in (0.0, 15.0, 30.0, 30.1, 90.0):
            live, _ = live_verdict(
                tipo_carater="urgencia_emergencia",
                tempo_acesso_apurado_min=tempo,
                distancia_apurada_km=distancia,
                prestadores_disponiveis=2,
                cobertura_geo_suficiente=True,
            )
            assert live != "CONFORME", (tempo, distancia)


def test_live_catchall_is_masked_for_the_whole_covered_subspace_achado_1() -> None:
    """ACHADO-1: the table's own `<description>` (:22-23) promises GAP_CRITICO for an uncovered
    combination, but `r_conforme` precedes the catch-all with three wildcards, so for the whole
    {prestadores>0 AND cobertura=true} subspace the declared fail-safe is unreachable."""
    for tipo in ("", "ambulatorial", "URGENCIA_EMERGENCIA", "domiciliar"):
        live, rule_id = live_verdict(
            tipo_carater=tipo,
            tempo_acesso_apurado_min=999,
            distancia_apurada_km=999.0,
            prestadores_disponiveis=1,
            cobertura_geo_suficiente=True,
        )
        assert (live, rule_id) == ("CONFORME", "r_conforme"), tipo
    # ...and the candidate restores it.
    for tipo in ("", "ambulatorial", "URGENCIA_EMERGENCIA", "domiciliar"):
        verdict = evaluate_candidate(
            tipo_carater=tipo,
            tempo_acesso_apurado_min=999,
            distancia_apurada_km=999.0,
            prestadores_disponiveis=1,
            cobertura_geo_suficiente=True,
        )
        assert (verdict.gap_adequacao, verdict.regra) == ("GAP_CRITICO", "c_catchall"), tipo


# =================================================================================================
# 4. MANIFEST <-> PYTHON EQUIVALENCE (the YAML is the source of truth)
# =================================================================================================


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    data = yaml.safe_load(_CANDIDATE_MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _manifest_rules(manifest: dict[str, Any]) -> list[tuple[str, list[str], str]]:
    rules: list[tuple[str, list[str], str]] = []
    for rule in manifest["regras_candidatas"]:
        entries = [str(rule["entradas"][column]) for column in _INPUT_ORDER]
        rules.append((str(rule["id"]), entries, str(rule["saidas"]["gap_adequacao"])))
    return rules


def test_manifest_rule_ids_and_order_match_the_evaluator(manifest: dict[str, Any]) -> None:
    ordens = [rule["ordem"] for rule in manifest["regras_candidatas"]]
    assert ordens == list(range(1, len(ordens) + 1)), "`ordem:` must be 1..N with no gaps"
    assert tuple(rule_id for rule_id, _, _ in _manifest_rules(manifest)) == CANDIDATE_RULE_ORDER


def test_every_manifest_rule_is_labelled_candidate(manifest: dict[str, Any]) -> None:
    """Every rule is a CANDIDATE — none of them is in force anywhere."""
    assert all(rule["candidato"] is True for rule in manifest["regras_candidatas"])
    assert all(
        str(rule["saidas"]["motivo"]).startswith("CANDIDATO") for rule in manifest["regras_candidatas"]
    )


def test_evaluator_is_equivalent_to_the_manifest_over_a_dense_grid(manifest: dict[str, Any]) -> None:
    """THE ANTI-DRIFT PROOF. The manifest is what the owner ratifies; the Python is a transcription.

    Both are evaluated FIRST-hit with the SAME entry matcher used against the real DMN, over a grid
    that straddles every declared threshold. A changed threshold, output, rule id or ORDER in either
    artifact fails here.
    """
    rules = _manifest_rules(manifest)
    checked = 0
    for tipo in ("eletivo", "urgencia_emergencia", "", "ambulatorial"):
        for tempo in (0, 29, 30, 31, 59, 60, 61, 120):
            for distancia in (0.0, 29.9, 30.0, 30.1, 49.9, 50.0, 50.1, 100.0):
                for prestadores in (0, 1, 5):
                    for cobertura in (True, False):
                        values = (tipo, tempo, distancia, prestadores, cobertura)
                        expected = next(
                            (rule_id, output)
                            for rule_id, entries, output in rules
                            if all(_matches(e, v) for e, v in zip(entries, values, strict=True))
                        )
                        got = evaluate_candidate(
                            tipo_carater=tipo,
                            tempo_acesso_apurado_min=tempo,
                            distancia_apurada_km=distancia,
                            prestadores_disponiveis=prestadores,
                            cobertura_geo_suficiente=cobertura,
                        )
                        assert (got.regra, got.gap_adequacao) == expected, values
                        checked += 1
    assert checked == 4 * 8 * 8 * 3 * 2  # non-vacuity: the grid really was swept


def test_candidate_thresholds_are_the_live_tables_own_numbers() -> None:
    """NO NUMBER IS INVENTED. Every candidate threshold is a literal of the live table."""
    entries = {rule_id: entries for rule_id, entries, _ in _live_rules()}
    assert entries["r_urgencia_critico"][1] == f"> {URGENCIA_TEMPO_MAX_MIN}"
    assert entries["r_urgencia_distancia_critico"][2] == f"> {URGENCIA_DISTANCIA_MAX_KM}"
    assert entries["r_eletivo_leve"][1] == f"<= {ELETIVO_TEMPO_MAX_MIN}"
    assert entries["r_eletivo_leve"][2] == f"<= {ELETIVO_DISTANCIA_MAX_KM}"
    # ...and they are the SAME numbers the owner-ratified runtime fail-safe already uses.
    assert adequacao_module._ELETIVO_LEVE_TEMPO_MAX_MIN == ELETIVO_TEMPO_MAX_MIN
    assert adequacao_module._ELETIVO_LEVE_DISTANCIA_MAX_KM == ELETIVO_DISTANCIA_MAX_KM


def test_candidate_emits_no_conforme_and_says_so(manifest: dict[str, Any]) -> None:
    """DECISAO-A, pinned honestly: the candidate has NO rule emitting CONFORME, because the live
    table declares no condition separating CONFORME from GAP_LEVE (ACHADO-3), and inventing one
    would be inventing a regulatory value. The manifest must keep saying so."""
    assert CANDIDATE_EMITS_CONFORME is False
    rules = manifest["regras_candidatas"]
    assert all(rule["saidas"]["gap_adequacao"] != "CONFORME" for rule in rules)
    decisoes = {d["id"] for d in manifest["decisao_pendente_do_dono"]}
    assert "DECISAO-A-conforme-sem-condicao-distintiva" in decisoes


def test_manifest_cites_its_governing_norm_and_the_open_finding(manifest: dict[str, Any]) -> None:
    """The owner must be able to trace every claim; a manifest that lost its citations is unusable
    as ratification evidence."""
    text = _CANDIDATE_MANIFEST.read_text(encoding="utf-8")
    assert "RN 259" in str(manifest["norma_regente"])
    assert manifest["alvo"] == "spec/processes/dmn/adequacao_gap.dmn"
    assert manifest["decisao_alvo"] == "adequacao_gap"
    assert manifest["status"] == "DRAFT"
    for citation in (
        "PLANS.md:150-159",  # the OPEN finding
        "docs/review-queue.md:160",  # the same finding in the review queue
        "docs/adr/0028-dmn-evaluation-engine-side.md:214-216",  # the DMN wins / owner's act
        "adequacao_gap.dmn:93",  # the 60min ceiling's source line
        "adequacao_gap.dmn:94",  # the 50km ceiling's source line
        "src/maezo/tools/workers/adequacao.py:182",  # the ratified fail-safe's own constant
    ):
        assert citation in text, f"missing in-repo citation: {citation}"
    # Findings recorded as comments/data, not fixes.
    assert {a["id"] for a in manifest["achados_adicionais"]} >= {
        "ACHADO-1-catchall-mascarado",
        "ACHADO-2-assimetria-eletivo-vs-urgencia",
        "ACHADO-3-conforme-duplica-as-regras-leve",
    }


# =================================================================================================
# 5. REFUSAL — no enforcing use while unratified
# =================================================================================================

_ENFORCEMENT_INPUTS: dict[str, Any] = {
    "tipo_carater": "eletivo",
    "tempo_acesso_apurado_min": 120,
    "distancia_apurada_km": 100.0,
    "prestadores_disponiveis": 1,
    "cobertura_geo_suficiente": True,
}


def test_shipped_manifest_is_not_ratified() -> None:
    """The shipped state, asserted rather than assumed."""
    assert load_candidate_ratification(_CANDIDATE_MANIFEST).ratificado is False


def test_enforcement_refuses_while_unratified() -> None:
    """THE REFUSAL. There is no enforcing consumer today; this pins that trying to create one
    raises rather than quietly enforcing a rule set no human has ratified."""
    with pytest.raises(EnforcementNotRatifiedError, match="NOT ratified"):
        evaluate_for_enforcement(**_ENFORCEMENT_INPUTS, manifest_path=_CANDIDATE_MANIFEST)


def test_enforcement_refuses_on_every_load_failure(tmp_path: Path) -> None:
    """Fail-closed on every manifest failure mode — an absent, malformed or wrongly-shaped file
    can only refuse, never enable."""
    missing = tmp_path / "absent.yaml"
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("ratificado: [unclosed\n", encoding="utf-8")
    not_a_mapping = tmp_path / "list.yaml"
    not_a_mapping.write_text("- ratificado: true\n", encoding="utf-8")
    for path in (missing, malformed, not_a_mapping):
        assert load_candidate_ratification(path).ratificado is False
        with pytest.raises(EnforcementNotRatifiedError):
            evaluate_for_enforcement(**_ENFORCEMENT_INPUTS, manifest_path=path)


def _binding_block(sha256: str | None = None, *, path: str = LIVE_TABLE_RELPATH) -> str:
    """The `tabela_viva` block a manifest must carry. Defaults to the live table's ACTUAL digest.

    Re-derived from disk here too, so these fixtures follow the table instead of pinning a literal
    that a legitimate (owner-applied) table edit would turn into a false failure of the WRONG test —
    the property "an edit invalidates the shipped manifest" is pinned once, in
    `test_shipped_manifest_is_bound_to_the_live_table_on_disk`, and nowhere else.
    """
    digest = live_table_digest() if sha256 is None else sha256
    return f'tabela_viva:\n  path: {path}\n  sha256: "{digest}"\n'


#: A complete, well-formed ratification — the baseline the malformed variants below mutate. Carries
#: a VALID live-table binding, so every variant below fails at exactly the gate it names.
_RATIFIED_BODY = (
    'ratificado: true\nrevisor: "dono regulatorio"\nratificado_em: "2026-08-09"\n' + _binding_block()
)


@pytest.mark.parametrize(
    ("label", "body"),
    (
        ("flag flipped, shipped PLACEHOLDER identity left as-is", None),
        ("flag flipped, reviewer blank", 'ratificado: true\nrevisor: "  "\nratificado_em: "2026-08-09"\n'),
        ("flag flipped, date absent", 'ratificado: true\nrevisor: "dono regulatorio"\n'),
        ("truthy string, not the boolean literal", _RATIFIED_BODY.replace("true", '"true"', 1)),
        ("truthy int, not the boolean literal", _RATIFIED_BODY.replace("true", "1", 1)),
    ),
)
def test_partial_or_placeholder_ratification_is_not_a_ratification(
    label: str, body: str | None, tmp_path: Path
) -> None:
    """An unfinished edit must read as NOT ratified, never as an anonymous approval (ADR-0007).

    The first case is the one the shipped file is designed for: the `PLACEHOLDER_*` accountability
    values are machine-detectable, so flipping ONLY the flag changes nothing.
    """
    path = tmp_path / "candidate.yaml"
    if body is None:
        original = _CANDIDATE_MANIFEST.read_text(encoding="utf-8")
        body = original.replace("ratificado: false", "ratificado: true", 1)
        assert "PLACEHOLDER" in body
    path.write_text(body, encoding="utf-8")
    assert load_candidate_ratification(path).ratificado is False, label


def test_a_complete_ratification_does_lift_the_refusal(tmp_path: Path) -> None:
    """COUNTER-PROOF: the gate is not "always refuse". A complete, accountable ratification — WITH a
    live-table binding that holds — lifts it, which is what makes ratification a DATA change with no
    code change."""
    path = tmp_path / "candidate.yaml"
    path.write_text(_RATIFIED_BODY, encoding="utf-8")
    assert load_candidate_ratification(path) == CandidateRatification(
        ratificado=True, revisor="dono regulatorio", ratificado_em="2026-08-09"
    )
    verdict = evaluate_for_enforcement(**_ENFORCEMENT_INPUTS, manifest_path=path)
    assert verdict.gap_adequacao == "GAP_CRITICO"


# =================================================================================================
# 5b. LIVE-TABLE BINDING — the ratification covers the table CONTENT the owner read
# =================================================================================================
#
# Section 5 proves a human must act before the candidate can be enforced. It says nothing about
# WHICH VERSION of `adequacao_gap.dmn` that human reviewed. Without the binding below, an edit to the
# live table would leave a prior ratification in force over content nobody approved — the exact
# failure `amh_inbox` (migration bytes) and `tiss_schema_pin` (XSD bytes) already close elsewhere in
# this repo. Here the manifest declares `tabela_viva: {path, sha256}` and the loader re-derives that
# digest from disk on EVERY load, refusing back to "not ratified" on any mismatch.
#
# The gate is INERT on the shipped manifest (which is unratified, so the loader never reaches it) —
# it binds the FUTURE ratification. What breaks the build TODAY if the table is edited is
# `test_shipped_manifest_is_bound_to_the_live_table_on_disk` below, and its six-manifest sibling in
# `tests/unit/spec/test_shadow_candidates_common.py`.


def test_shipped_manifest_is_bound_to_the_live_table_on_disk() -> None:
    """THE BINDING, on the shipped file: the declared digest IS the live table's bytes right now.

    An edit to `spec/processes/dmn/adequacao_gap.dmn` turns this RED, and it must stay red until the
    candidate is re-authored and re-reviewed against the new content. Bumping the digest alone would
    be a deliberate defeat of the mechanism, not a repair.
    """
    manifest = yaml.safe_load(_CANDIDATE_MANIFEST.read_text(encoding="utf-8"))
    binding = manifest["tabela_viva"]
    assert binding["path"] == LIVE_TABLE_RELPATH == f"spec/processes/dmn/{LIVE_TABLE_FILENAME}"
    assert binding["path"] == manifest["alvo"], "the binding and the target may not drift apart"
    assert binding["sha256"] == live_table_digest()
    assert live_table_path().resolve() == _LIVE_TABLE.resolve()


def test_the_declared_digest_is_a_digest_of_the_table_and_not_of_the_manifest() -> None:
    """Non-vacuity of the comparison: hashing the wrong file would also produce 64 hex chars."""
    assert live_table_digest() == hashlib.sha256(_LIVE_TABLE.read_bytes()).hexdigest()
    assert live_table_digest() != hashlib.sha256(_CANDIDATE_MANIFEST.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("label", "block"),
    (
        ("binding absent altogether", ""),
        ("digest of no table at all", _binding_block("0" * 64)),
        ("digest still a placeholder", _binding_block("PLACEHOLDER_SHA256")),
        ("digest blank", _binding_block("   ")),
        ("digest not hex", _binding_block("z" * 64)),
        ("digest truncated", _binding_block("abc123")),
        ("binding not a mapping", "tabela_viva: spec/processes/dmn/adequacao_gap.dmn\n"),
        ("path names another table", _binding_block(path="spec/processes/dmn/carencia_check.dmn")),
        ("path traverses out of spec/", _binding_block(path="spec/processes/dmn/../../../etc/passwd")),
        ("unknown key beside the two known ones", _binding_block() + "  sha512: x\n"),
    ),
)
def test_a_broken_binding_unratifies_an_otherwise_complete_ratification(
    label: str, block: str, tmp_path: Path
) -> None:
    """MUTATION PROOF. Each row is `_RATIFIED_BODY` — a complete, accountable, would-be-valid
    ratification — with ONLY its binding broken in the way `label` names. Every row must read as NOT
    ratified, and every row must refuse enforcement. If the gate were vacuous they would all pass.
    """
    body = _RATIFIED_BODY.split("tabela_viva:")[0] + block
    path = tmp_path / "candidate.yaml"
    path.write_text(body, encoding="utf-8")

    assert load_candidate_ratification(path).ratificado is False, label
    with pytest.raises(EnforcementNotRatifiedError):
        evaluate_for_enforcement(**_ENFORCEMENT_INPUTS, manifest_path=path)


def test_a_stale_digest_refuses_and_the_current_one_does_not(tmp_path: Path) -> None:
    """The two directions on one file: flipping ONE hex character of the declared digest — which is
    exactly what an edit to the live table produces — turns a complete ratification back into "not
    ratified"; leaving it correct does not."""
    current = live_table_digest()
    assert current is not None
    stale = ("b" if current[0] != "b" else "c") + current[1:]

    ok = tmp_path / "ok.yaml"
    ok.write_text(_RATIFIED_BODY, encoding="utf-8")
    assert load_candidate_ratification(ok).ratificado is True

    drifted = tmp_path / "stale.yaml"
    drifted.write_text(_RATIFIED_BODY.replace(current, stale), encoding="utf-8")
    assert load_candidate_ratification(drifted).ratificado is False


# =================================================================================================
# 6. NON-INFLUENCE — the shadow cannot change the worker's verdict or routing
# =================================================================================================

#: (label, live DMN gap verdict, live DMN route, worker inputs). Chosen to cover: the fail-safe
#: firing, the fail-safe NOT firing, and each non-CONFORME verdict — i.e. every branch of
#: `route_remediation` that the shadow call sits next to.
_BEHAVIOUR_MATRIX: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    (
        "CONFORME contradicted by measurements -> fail-safe fires",
        "CONFORME",
        "MONITORAR",
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 120,
            "distancia_apurada_km": 100.0,
            "prestadores_disponiveis": 1,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
    ),
    (
        "CONFORME within the ceiling -> fail-safe silent",
        "CONFORME",
        "MONITORAR",
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 45,
            "distancia_apurada_km": 10.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
    ),
    (
        "GAP_LEVE (candidate agrees)",
        "GAP_LEVE",
        "MONITORAR",
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 45,
            "distancia_apurada_km": 10.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
    ),
    (
        "GAP_LEVE where the candidate DIVERGES to GAP_CRITICO",
        "GAP_LEVE",
        "MONITORAR",
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 200,
            "distancia_apurada_km": 90.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
    ),
    (
        "GAP_MODERADO",
        "GAP_MODERADO",
        "ENCAMINHAR_CREDENCIAMENTO",
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 45,
            "distancia_apurada_km": 10.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": False,
            "dados_geo_completos": True,
        },
    ),
    (
        "GAP_CRITICO",
        "GAP_CRITICO",
        "ANALISE_HUMANA",
        {
            "tipo_carater": "urgencia_emergencia",
            "tempo_acesso_apurado_min": 90,
            "distancia_apurada_km": 10.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
    ),
    (
        "absent tipo_carater (worker default '')",
        "CONFORME",
        "MONITORAR",
        {
            "tempo_acesso_apurado_min": 300,
            "distancia_apurada_km": 400.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
    ),
)


def _fake(gap: str, route: str) -> FakeDmnTransport:
    fake = FakeDmnTransport()
    fake.register("adequacao_gap", [{"gap_adequacao": gap, "motivo": "m"}])
    fake.register("adequacao_remediation_routing", [{"roteamento_remediacao": route, "motivo": "m"}])
    return fake


@pytest.mark.parametrize(("label", "gap", "route", "variables"), _BEHAVIOUR_MATRIX)
def test_shadow_cannot_influence_the_worker(
    label: str,
    gap: str,
    route: str,
    variables: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE NON-BEHAVIOUR PROOF. Identical outcomes with the REAL evaluator, with the evaluator
    NEUTRALISED (a no-op), and with the evaluator RAISING.

    The third case is the important one: it proves the wiring is non-behavioural even if the shadow
    is outright broken, so a future bug in the candidate logic cannot reach a beneficiary.
    """
    real = route_remediation(dict(variables), dmn=_fake(gap, route))

    def _noop(**_kwargs: Any) -> None:
        return None

    def _boom(**_kwargs: Any) -> None:
        raise RuntimeError("shadow evaluator deliberately broken")

    monkeypatch.setattr(adequacao_module, "record_shadow_divergence", _noop)
    neutralised = route_remediation(dict(variables), dmn=_fake(gap, route))

    monkeypatch.setattr(adequacao_module, "record_shadow_divergence", _boom)
    broken = route_remediation(dict(variables), dmn=_fake(gap, route))

    assert real == neutralised == broken, label
    # ...and the outcome is still the one the fail-safe/DMN dictate, not something the shadow chose.
    assert real["gap_adequacao"] == gap, label


def test_the_behaviour_matrix_is_not_vacuous() -> None:
    """NON-VACUITY for section 6: the matrix must contain rows where the shadow ACTUALLY diverges
    (otherwise "identical outcomes" would be trivially true) and rows where it does not."""
    diverging = 0
    agreeing = 0
    for _label, gap, _route, variables in _BEHAVIOUR_MATRIX:
        event = shadow_divergence_event(
            gap_adequacao_dmn=gap,
            tipo_carater=variables.get("tipo_carater", ""),
            tempo_acesso_apurado_min=variables["tempo_acesso_apurado_min"],
            distancia_apurada_km=variables["distancia_apurada_km"],
            prestadores_disponiveis=variables["prestadores_disponiveis"],
            cobertura_geo_suficiente=variables["cobertura_geo_suficiente"],
        )
        if event is None:
            agreeing += 1
        else:
            diverging += 1
    assert diverging >= 3, "the matrix never exercises a divergence"
    assert agreeing >= 3, "the matrix never exercises agreement"


def test_worker_emits_the_divergence_event_and_still_routes_by_the_fail_safe() -> None:
    """END TO END through the worker: the divergence IS recorded, and the routing is still the
    fail-safe's ANALISE_HUMANA — the shadow observed, it did not decide."""
    variables = {
        "tipo_carater": "eletivo",
        "tempo_acesso_apurado_min": 120,
        "distancia_apurada_km": 100.0,
        "prestadores_disponiveis": 1,
        "cobertura_geo_suficiente": True,
        "dados_geo_completos": True,
    }
    with structlog.testing.capture_logs() as logs:
        result = route_remediation(dict(variables), dmn=_fake("CONFORME", "MONITORAR"))

    divergences = [entry for entry in logs if entry["event"] == SHADOW_DIVERGENCE_EVENT]
    assert len(divergences) == 1
    assert divergences[0]["gap_adequacao_dmn"] == "CONFORME"
    assert divergences[0]["gap_adequacao_candidato"] == "GAP_CRITICO"
    assert divergences[0]["regra_candidata"] == "c_eletivo_tempo_acima_teto"

    assert result["gap_adequacao"] == "CONFORME"  # the DMN verdict is preserved and auditable
    assert result["roteamento_remediacao"] == "ANALISE_HUMANA"  # the OWNER-RATIFIED fail-safe
    assert result["motivo"] == "CONFORME_RECUSADO_ACESSO_ACIMA_DO_TETO_LEVE"


def test_no_event_when_the_verdicts_agree() -> None:
    """Silence when there is nothing to report — otherwise the log would be noise the reviewer
    cannot ratify against."""
    with structlog.testing.capture_logs() as logs:
        record_shadow_divergence(
            gap_adequacao_dmn="GAP_LEVE",
            tipo_carater="eletivo",
            tempo_acesso_apurado_min=45,
            distancia_apurada_km=10.0,
            prestadores_disponiveis=2,
            cobertura_geo_suficiente=True,
        )
    assert [entry for entry in logs if entry["event"] == SHADOW_DIVERGENCE_EVENT] == []


def test_record_shadow_divergence_never_raises() -> None:
    """Its own contract, independent of the worker's belt-and-braces try/except."""
    record_shadow_divergence(
        gap_adequacao_dmn="CONFORME",
        tipo_carater=None,  # type: ignore[arg-type]  # engine variables arrive untyped
        tempo_acesso_apurado_min="not a number",  # type: ignore[arg-type]
        distancia_apurada_km=None,  # type: ignore[arg-type]
        prestadores_disponiveis=None,  # type: ignore[arg-type]
        cobertura_geo_suficiente="maybe",  # type: ignore[arg-type]
    )


# =================================================================================================
# 7. NON-PHI
# =================================================================================================


def test_divergence_event_carries_no_identifier_and_no_cell_identity() -> None:
    """The event is verdicts + the five rule inputs + a rule id. This process carries no
    beneficiary identifier at all (ADR-0006, SP-OP-ADEQUACAO-001.md:70), and the cell identity is
    omitted too: a divergence is a property of the RULE SET, not of a cell."""
    event = shadow_divergence_event(
        gap_adequacao_dmn="CONFORME",
        tipo_carater="eletivo",
        tempo_acesso_apurado_min=120,
        distancia_apurada_km=100.0,
        prestadores_disponiveis=1,
        cobertura_geo_suficiente=True,
    )
    assert event is not None
    assert set(event) == {
        "gap_adequacao_dmn",
        "gap_adequacao_candidato",
        "regra_candidata",
        "tipo_carater",
        "tempo_acesso_apurado_min",
        "distancia_apurada_km",
        "prestadores_disponiveis",
        "cobertura_geo_suficiente",
    }
    forbidden = (
        "cpf",
        "cns",
        "beneficiario",
        "beneficiary",
        "patient",
        "paciente",
        "nome",
        "nasc",
        "telefone",
        "email",
        "endereco",
        "regiao",
        "especialidade",
        "tenant",
        "business_key",
        "protocolo",
        "carteirinha",
    )
    for fragment in forbidden:
        assert not any(fragment in key.lower() for key in event), fragment


def test_worker_divergence_log_line_carries_no_identifier() -> None:
    """The same guarantee at the emission site — a `regiao_saude`/`especialidade`/`tenant_id`
    present on `variables` must not leak into the event."""
    with structlog.testing.capture_logs() as logs:
        route_remediation(
            {
                "tipo_carater": "eletivo",
                "tempo_acesso_apurado_min": 120,
                "distancia_apurada_km": 100.0,
                "prestadores_disponiveis": 1,
                "cobertura_geo_suficiente": True,
                "dados_geo_completos": True,
                "regiao_saude": "R-001",
                "especialidade": "cardiologia",
                "tenant_id": "amh",
            },
            dmn=_fake("CONFORME", "MONITORAR"),
        )
    entry = next(e for e in logs if e["event"] == SHADOW_DIVERGENCE_EVENT)
    assert "R-001" not in str(entry)
    assert "cardiologia" not in str(entry)
    assert "amh" not in str(entry)


# =================================================================================================
# 8. THE ACTIVATION PATH IS REAL
# =================================================================================================


def test_candidate_manifest_is_invisible_to_artifact_deployment() -> None:
    """THE DEPLOYMENT-GLOB PROOF. `make deploy-artifacts` (Makefile:73-80 ->
    `python -m maezo.platform.deploy`) selects artifacts via `collect_artifacts`, which globs
    `*.bpmn`/`*.dmn` ONLY (engine_deploy.py:139-140). The candidate is `.yaml`, so it can never be
    submitted to the engine — it is a proposal, not a deployable rule."""
    assert _CANDIDATE_MANIFEST.is_file(), "the manifest must exist for this proof to mean anything"
    artifacts = collect_artifacts(resolve_spec_processes_dir())
    assert _CANDIDATE_MANIFEST.name not in {p.name for p in artifacts}
    assert _CANDIDATE_MANIFEST.resolve() not in {p.resolve() for p in artifacts}
    assert all(p.suffix in (".bpmn", ".dmn") for p in artifacts)
    # Non-vacuity: the live table it proposes to correct IS selected.
    assert _LIVE_TABLE.name in {p.name for p in artifacts}


def test_candidate_manifest_is_a_sibling_of_the_table_it_corrects() -> None:
    """It lives next to `adequacao_gap.dmn` on purpose — the owner edits both in one place — and
    that is only safe because of the glob proved above."""
    assert _CANDIDATE_MANIFEST.parent == _LIVE_TABLE.parent
    assert _CANDIDATE_MANIFEST.suffix == ".yaml"


def _codeowners_rules() -> dict[str, list[str]]:
    """`{pattern: [owner, ...]}` from `.github/CODEOWNERS`, comments stripped FIRST.

    Stripping `# ...` before looking at anything is not a nicety: that file's header carries literal
    `@handles` in prose, and a rule-shaped path can appear inside a comment. A reader that matches on
    the raw line is fooled by `<pattern>  # TODO ask @somebody` (a commented-out rule that owns
    nothing but scans as owned) and by a comment that merely mentions the path. Owners are split on
    whitespace RUNS because the real file column-aligns them with multiple spaces. Mirrors
    `tests/unit/spec/test_shadow_candidates_common.py`.
    """
    rules: dict[str, list[str]] = {}
    for raw in _CODEOWNERS.read_text(encoding="utf-8").splitlines():
        fields = raw.split("#", 1)[0].split()
        if fields:
            rules[fields[0]] = fields[1:]  # CODEOWNERS resolves LAST-match
    return rules


def test_both_halves_of_the_owners_act_are_codeowners_gated() -> None:
    """The activation path is ONE human act in two files: ratify the manifest, and apply the rules
    to the live table (per ADR-0028 the live-table edit is the owner's act, not engineering's).
    Both must require the compliance reviewer, or the gate is decorative."""
    rules = _codeowners_rules()
    for path in (
        "/spec/processes/dmn/adequacao-gap-shadow-candidate.yaml",
        "/spec/processes/dmn/adequacao_gap.dmn",
    ):
        assert path in rules, f"{path} is NOT covered by .github/CODEOWNERS"
        owners = rules[path]
        assert owners, f"{path} has no owner assigned"
        assert all(o.startswith("@") for o in owners), f"{path} has a malformed owner token: {owners}"


def test_manifest_header_documents_the_activation_path(manifest: dict[str, Any]) -> None:
    """The header must state the single human act, including that the live-table edit itself is the
    owner's act per ADR-0028 — that sentence is the whole point of the file."""
    text = _CANDIDATE_MANIFEST.read_text(encoding="utf-8")
    assert "ACTIVATION PATH" in text
    assert "ADR-0028" in text
    assert "THE LIVE-TABLE EDIT ITSELF IS THE OWNER'S ACT" in text
    assert "CODEOWNERS" in text
    assert manifest["modo_operacao"] == "sombra"
