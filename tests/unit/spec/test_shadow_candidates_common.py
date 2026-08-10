"""Cross-cutting properties of the W4 shadow-candidate manifests (M-4 .. M-7).

The four sibling known-wrong tables of the calc audit each got a candidate manifest, following the
merged W3 precedent (`adequacao-gap-shadow-candidate.yaml` + `adequacao_shadow.py`). This module
pins the properties that must hold for ALL of them at once:

  0. THE ROSTER IS THE DISK — every property below is parametrized over the hardcoded roster, so a
     manifest that never joins it is checked by NOTHING. A disk-derived census closes that.
  1. THE READER ITSELF is fail-closed — an entry shape it cannot parse RAISES, never reads as
     "no match". Without this, every divergence corpus in the sibling modules would be worthless.
  2. INVISIBLE TO THE ENGINE — no artifact-selection path can pick a candidate up.
  3. NO CONSUMER IN `src/` — this wave wired NOTHING. The W3 manifest, which IS consumed, is the
     non-vacuity anchor for that grep.
  4. REFUSAL — a candidate cannot be enforced while unratified, and the shipped `PLACEHOLDER_*`
     accountability values cannot be mistaken for a ratification.
  5. BOTH HALVES OF THE OWNER'S ACT ARE CODEOWNERS-GATED — the manifest AND the live table.
  6. SCHEMA — every manifest is DRAFT, names its target, and its rules' columns are exactly the
     live table's own (enforced inside `read_candidate_table`).
  7. LIVE-TABLE BINDING — every manifest, INCLUDING the merged W3 one, declares the digest of the
     live table it was authored against, and that digest is re-derived from disk here. This is what
     stops a ratification given to one version of a table from silently carrying over to edited
     table content: editing a live table breaks this module until the candidate is re-authored.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from maezo.platform.deploy.engine_deploy import collect_artifacts, resolve_spec_processes_dir
from tests.support.dmn_first_hit import (
    CODEOWNERS,
    DMN_DIR,
    LIVE_TABLE_BLOCK,
    LIVE_TABLE_PATH,
    LIVE_TABLE_SHA256,
    REPO_ROOT,
    EnforcementNotRatifiedError,
    LiveTableBindingError,
    UnreadableEntryError,
    evaluate_for_enforcement,
    live_table_digest,
    load_candidate_ratification,
    load_manifest,
    matches,
    parse_output,
    read_candidate_table,
    read_live_table,
    verify_live_table_binding,
)

#: candidate manifest filename -> the live table it proposes a correction to.
CANDIDATES: dict[str, str] = {
    "glosa-triage-shadow-candidate.yaml": "glosa_triage.dmn",
    "carencia-check-shadow-candidate.yaml": "carencia_check.dmn",
    "upcoding-complexity-ceiling-shadow-candidate.yaml": "upcoding_complexity_ceiling.dmn",
    "triage-redflag-gestante-shadow-candidate.yaml": "triage_redflag_gestante.dmn",
    "triage-redflag-pediatric-shadow-candidate.yaml": "triage_redflag_pediatric.dmn",
}

#: The MERGED W3 manifest. It is NOT part of this wave; it is the non-vacuity anchor for the
#: "nothing in src/ reads a W4 manifest" grep — that grep must find the W3 one.
W3_MANIFEST = "adequacao-gap-shadow-candidate.yaml"

#: ALL SIX candidate manifests — the five of this wave plus the merged W3 one. Used ONLY by section
#: 7: the live-table binding is a property of every ratifiable candidate in the repository, whatever
#: wave shipped it, so it is the one section that must not stop at this wave's five.
ALL_CANDIDATES: dict[str, str] = {W3_MANIFEST: "adequacao_gap.dmn", **CANDIDATES}

#: The filename shape every candidate manifest has. The census below globs it off disk, so the glob
#: and the roster are the two halves of one fact and cannot drift apart silently.
CANDIDATE_GLOB = "*-shadow-candidate.yaml"


# =================================================================================================
# 0. THE ROSTER IS THE DISK
# =================================================================================================


def test_the_roster_is_exactly_the_candidate_manifests_on_disk() -> None:
    """CENSUS. `CANDIDATES`/`ALL_CANDIDATES` are hardcoded, and every property in this module is
    parametrized over one of them — so a manifest that never joins the roster is checked by NOTHING
    here: it could ship with no `tabela_viva` binding at all, no CODEOWNERS line and no refusal
    coverage, and this suite would stay entirely green. Deriving the set from disk is what makes the
    roster a claim about the repository instead of a claim about itself; it is the same disk-derived
    census as the 62-DMN/16-BPMN pin in section 2-3 below.
    """
    on_disk = {path.name for path in DMN_DIR.glob(CANDIDATE_GLOB)}
    roster = set(ALL_CANDIDATES)
    strangers = sorted(on_disk - roster)
    absent = sorted(roster - on_disk)
    assert on_disk == roster, (
        f"the shadow-candidate roster and {DMN_DIR.name}/{CANDIDATE_GLOB} have come apart — "
        f"on disk but NOT in ALL_CANDIDATES: {strangers}; in ALL_CANDIDATES but NOT on disk: {absent}. "
        "A new candidate manifest must do BOTH of these, or it is governed by nothing: "
        "(1) JOIN THE ROSTER in this module — add it to `CANDIDATES` mapped to the live `.dmn` it "
        "corrects (or to `ALL_CANDIDATES` directly if, like the W3 manifest, it already has a `src/` "
        "consumer); and (2) DECLARE `tabela_viva: {path, sha256}` in the manifest itself, binding it "
        "to the bytes of that live table. Deleting a manifest means removing it from the roster too."
    )


# =================================================================================================
# 1. THE READER IS FAIL-CLOSED
# =================================================================================================


@pytest.mark.parametrize(
    "entry",
    [
        "[1..10]",  # FEEL range
        '[1..10]"',  # malformed
        'starts with(tuss_prefix, "99")',  # phantom_suspicious_prefix.dmn:31 — real, unsupported
        'not(starts with(tuss_prefix, "99"))',  # phantom_suspicious_prefix.dmn:52 — idem
        "not(> 3)",  # not() over a non-literal
        "> abc",  # comparison against a non-number
        "sintoma_codigo",  # a bare identifier
        '"a" "b"',  # two literals without the comma
    ],
)
def test_reader_refuses_every_entry_shape_it_cannot_parse(entry: str) -> None:
    """An unreadable entry must RAISE — a reader that returned False would quietly corrupt a corpus."""
    with pytest.raises(UnreadableEntryError):
        matches(entry, "x")


@pytest.mark.parametrize(
    ("entry", "value", "expected"),
    [
        ("-", "anything", True),
        ('"tecnica"', "tecnica", True),
        ('"tecnica"', "clinica", False),
        ('"tecnica","clinica"', "clinica", True),  # glosa_triage.dmn:66
        ('"tecnica","clinica"', "desconhecida", False),
        ('not("tecnica","clinica")', "desconhecida", True),  # glosa_triage.dmn:56 — THE DEFECT
        ('not("tecnica","clinica")', "tecnica", False),
        ('"grave","moderada"', "grave", True),  # triage_redflag_*.dmn
        ('"grave","moderada"', "leve", False),
        ("true", True, True),
        ("true", False, False),
        ("false", False, True),
        ("> 2", 3, True),
        ("> 2", 2, False),
        ("< 730", 30, True),
        (">= 1", 1, True),
        ("<= 3", 3, True),
        ("2", 2, True),
        ("2", 3, False),
        ("> 2", True, False),  # bool is never a numeric input
    ],
)
def test_reader_grammar(entry: str, value: object, expected: bool) -> None:
    assert matches(entry, value) is expected


@pytest.mark.parametrize("entry", ["-", '"tecnica"', 'not("tecnica")', "true", "> 2", "2"])
def test_only_the_wildcard_matches_a_null_input(entry: str) -> None:
    """`None` models an input PRESENT AND NULL — see `NULL_MATCHES_ONLY_WILDCARD`.

    No divergence corpus in this wave uses a null; this pins the reader's declared semantics so the
    ACHADOs that discuss absent inputs cannot be silently contradicted by the reader's behaviour.
    """
    assert matches(entry, None) is (entry == "-")


def test_reader_still_refuses_an_unreadable_entry_on_the_null_path() -> None:
    with pytest.raises(UnreadableEntryError):
        matches('starts with(x, "99")', None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("true", True), ("false", False), ("0", 0), ("-1", -1), ('"SEM_GLOSA"', "SEM_GLOSA")],
)
def test_output_grammar(text: str, expected: object) -> None:
    assert parse_output(text) == expected


def test_output_grammar_refuses_an_unknown_shape() -> None:
    with pytest.raises(UnreadableEntryError):
        parse_output("date and time(x)")


def test_linear_output_expression_is_evaluated_not_stringified() -> None:
    """`730 - dias_desde_adesao` (carencia_check.dmn:95) is an EXPRESSION, not a literal."""
    linear = parse_output("730 - dias_desde_adesao")
    assert linear.evaluate({"dias_desde_adesao": 30}) == 700


# =================================================================================================
# 2-3. INVISIBLE TO THE ENGINE, AND UNCONSUMED BY `src/`
# =================================================================================================


def test_candidate_manifests_are_invisible_to_artifact_deployment() -> None:
    """`collect_artifacts` globs `*.dmn`/`*.bpmn` — the manifests cannot reach the engine."""
    artifacts = collect_artifacts(resolve_spec_processes_dir())
    resolved = {path.resolve() for path in artifacts}
    for manifest, live in CANDIDATES.items():
        assert (DMN_DIR / manifest).resolve() not in resolved, manifest
        assert (DMN_DIR / live).resolve() in resolved, live


def test_the_dmn_census_is_unchanged_by_this_wave() -> None:
    """The five new files are `.yaml`; the DMN census pin (62) must be untouched."""
    artifacts = collect_artifacts(resolve_spec_processes_dir())
    assert len([p for p in artifacts if p.suffix == ".dmn"]) == 62
    assert len([p for p in artifacts if p.suffix == ".bpmn"]) == 16


def test_no_src_consumer_of_the_candidate_manifests() -> None:
    """NO WORKER WIRING THIS WAVE — nothing under `src/` names a W4 manifest.

    Non-vacuity: the same grep DOES find the merged W3 manifest, which is genuinely consumed by
    `src/maezo/tools/workers/adequacao_shadow.py`. If the grep found nothing at all it would be
    proving that the search is broken, not that the wave is unwired.
    """
    sources = {path: path.read_text(encoding="utf-8") for path in (REPO_ROOT / "src").rglob("*.py")}
    for manifest in CANDIDATES:
        offenders = sorted(str(p.relative_to(REPO_ROOT)) for p, text in sources.items() if manifest in text)
        assert offenders == [], f"{manifest} is referenced by src/: {offenders}"

    anchors = sorted(str(p.relative_to(REPO_ROOT)) for p, text in sources.items() if W3_MANIFEST in text)
    assert "src/maezo/tools/workers/adequacao_shadow.py" in anchors, anchors


# =================================================================================================
# 4. REFUSAL
# =================================================================================================


@pytest.mark.parametrize("manifest", sorted(CANDIDATES))
def test_shipped_manifest_is_not_ratified(manifest: str) -> None:
    status = load_candidate_ratification(DMN_DIR / manifest)
    assert status.ratificado is False
    assert status.revisor == ""
    assert status.ratificado_em == ""


@pytest.mark.parametrize("manifest", sorted(CANDIDATES))
def test_shipped_manifest_carries_machine_detectable_placeholders(manifest: str) -> None:
    """The accountability fields ship as `PLACEHOLDER_*` so a half-finished edit cannot read as an
    anonymous ratification (ADR-0007)."""
    data = load_manifest(DMN_DIR / manifest)
    assert data["ratificado"] is False
    assert "PLACEHOLDER" in str(data["revisor"]).upper()
    assert "PLACEHOLDER" in str(data["ratificado_em"]).upper()


@pytest.mark.parametrize(("manifest", "live"), sorted(CANDIDATES.items()))
def test_enforcement_refuses_while_unratified(manifest: str, live: str) -> None:
    table = read_live_table(DMN_DIR / live)
    values = dict.fromkeys(table.input_names, "")
    with pytest.raises(EnforcementNotRatifiedError):
        evaluate_for_enforcement(DMN_DIR / manifest, table, values)


@pytest.mark.parametrize(
    "content",
    [
        "",  # empty
        "não é yaml: [",  # malformed YAML
        "- a\n- b\n",  # a list, not a mapping
        "ratificado: false\nrevisor: x\nratificado_em: '2026-01-01'\n",
        "ratificado: 'true'\nrevisor: x\nratificado_em: '2026-01-01'\n",  # the STRING "true"
        "ratificado: 1\nrevisor: x\nratificado_em: '2026-01-01'\n",  # truthy, not `true`
        "ratificado: true\nratificado_em: '2026-01-01'\n",  # revisor absent
        "ratificado: true\nrevisor: '  '\nratificado_em: '2026-01-01'\n",  # blank
        "ratificado: true\nrevisor: PLACEHOLDER_X\nratificado_em: '2026-01-01'\n",  # placeholder
        "ratificado: true\nrevisor: x\nratificado_em: PLACEHOLDER_YYYY-MM-DD\n",  # placeholder
        "ratificado: true\nrevisor: x\n",  # date absent
    ],
)
def test_ratification_fails_closed_on_every_partial_or_malformed_form(tmp_path: Path, content: str) -> None:
    path = tmp_path / "candidate.yaml"
    path.write_text(content, encoding="utf-8")
    assert load_candidate_ratification(path).ratificado is False


def test_ratification_fails_closed_when_the_file_does_not_exist(tmp_path: Path) -> None:
    assert load_candidate_ratification(tmp_path / "absent.yaml").ratificado is False


def test_a_complete_ratification_does_lift_the_refusal(tmp_path: Path) -> None:
    """NON-VACUITY for the refusal: it is the DATA, not the code, that keeps enforcement shut."""
    replacements = {
        "ratificado:": "ratificado: true",
        "revisor:": 'revisor: "auditoria-contas (teste)"',
        "ratificado_em:": 'ratificado_em: "2026-08-09"',
    }
    source = (DMN_DIR / "glosa-triage-shadow-candidate.yaml").read_text(encoding="utf-8")
    lines = [
        next((new for prefix, new in replacements.items() if line.startswith(prefix)), line)
        for line in source.splitlines()
    ]
    path = tmp_path / "glosa-triage-shadow-candidate.yaml"
    path.write_text("\n".join(lines), encoding="utf-8")

    status = load_candidate_ratification(path)
    assert status.ratificado is True
    assert status.revisor == "auditoria-contas (teste)"

    table = read_live_table(DMN_DIR / "glosa_triage.dmn")
    verdict = evaluate_for_enforcement(
        path,
        table,
        {
            "tipo_item": "",
            "categoria_normalizada": "desconhecida",
            "item_conforme_tabela": True,
            "divergencia_valor": False,
            "documentacao_anexa": True,
        },
    )
    assert verdict.saidas["roteamento"] == "ANALISE_HUMANA"


# =================================================================================================
# 5. BOTH HALVES OF THE OWNER'S ACT ARE CODEOWNERS-GATED
# =================================================================================================


@pytest.mark.parametrize(("manifest", "live"), sorted(CANDIDATES.items()))
def test_both_halves_of_the_owners_act_are_codeowners_gated(manifest: str, live: str) -> None:
    """Ratifying is a DATA change with no code and no redeploy — which is exactly why the data needs
    the reviewer the code path would otherwise have had. The live table needs it too: per ADR-0028
    the table edit is the OWNER's act."""
    owned: dict[str, str] = {}
    for raw in CODEOWNERS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, _, owners = line.partition(" ")
        if owners.strip():
            owned[pattern] = owners.strip()  # CODEOWNERS resolves LAST-match

    for path in (f"/spec/processes/dmn/{manifest}", f"/spec/processes/dmn/{live}"):
        assert path in owned, f"{path} has no CODEOWNERS line"
        assert "@" in owned[path], f"{path} has a CODEOWNERS line with no owner"


# =================================================================================================
# 6. SCHEMA
# =================================================================================================


@pytest.mark.parametrize(("manifest", "live"), sorted(CANDIDATES.items()))
def test_manifest_is_draft_shadow_and_targets_its_live_table(manifest: str, live: str) -> None:
    data = load_manifest(DMN_DIR / manifest)
    assert data["status"] == "DRAFT"
    assert data["modo_operacao"] == "sombra"
    assert data["alvo"] == f"spec/processes/dmn/{live}"
    assert data["decisao_alvo"] == read_live_table(DMN_DIR / live).decision_id


@pytest.mark.parametrize(("manifest", "live"), sorted(CANDIDATES.items()))
def test_candidate_columns_are_exactly_the_live_tables_own(manifest: str, live: str) -> None:
    """`read_candidate_table` refuses a manifest that grows or renames a column, and refuses a rule
    not labelled `candidato: true` or out of `ordem`. Calling it IS the assertion."""
    table = read_live_table(DMN_DIR / live)
    candidate = read_candidate_table(DMN_DIR / manifest, table)
    assert candidate.input_names == table.input_names
    assert candidate.output_names == table.output_names
    assert len(set(candidate.rule_ids())) == len(candidate.rules), "duplicate candidate rule id"


def test_read_candidate_table_raises_when_a_rules_entradas_grows_a_column_the_live_table_lacks(
    tmp_path: Path,
) -> None:
    """Anti-defang pin for the `entradas`-keys guard (`dmn_first_hit.py:332-335`).

    `test_candidate_columns_are_exactly_the_live_tables_own` only asserts `candidate.input_names ==
    table.input_names` — but `read_candidate_table` ALWAYS returns `live.input_names` verbatim
    (`dmn_first_hit.py:351`), copied from the `live` table it was passed, never derived from what the
    manifest actually declared. That equality is therefore tautological: it would keep passing even
    if the guard that inspects each rule's `entradas` keys were gutted. This test exercises the guard
    directly, on a scratch manifest, independent of that tautology: a rule whose `entradas` carries
    one column beyond the live table's own MUST raise, never silently pass.
    """
    live = read_live_table(DMN_DIR / "glosa_triage.dmn")
    grown_entradas = dict.fromkeys(live.input_names, "-")
    grown_entradas["coluna_extra_que_a_tabela_viva_nao_tem"] = "-"
    manifest = {
        "regras_candidatas": [
            {
                "ordem": 1,
                "id": "r_grown_column",
                "candidato": True,
                "entradas": grown_entradas,
                "saidas": dict.fromkeys(live.output_names, "PLACEHOLDER"),
            }
        ],
    }
    path = tmp_path / "grown-column-shadow-candidate.yaml"
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")

    with pytest.raises(UnreadableEntryError):
        read_candidate_table(path, live)


@pytest.mark.parametrize(("manifest", "live"), sorted(CANDIDATES.items()))
def test_candidate_manifest_is_a_sibling_of_the_table_it_corrects(manifest: str, live: str) -> None:
    """Both files live in `spec/processes/dmn/`, so a reviewer opening one sees the other."""
    assert (DMN_DIR / manifest).is_file()
    assert (DMN_DIR / live).is_file()


# =================================================================================================
# 7. LIVE-TABLE BINDING — a ratification covers the table CONTENT it reviewed, and nothing else
# =================================================================================================
#
# Sections 4-6 pin that a candidate cannot be enforced without a human act, and that its columns are
# the live table's own. Neither says anything about WHICH VERSION of the live table the candidate was
# written against — so, before this section, an edit to a live table would leave every candidate
# silently claiming to correct text nobody had read, and a ratification given to the old content
# would carry over to the new content with no signal at all.
#
# `tabela_viva: {path, sha256}` closes that: each manifest declares the digest of the table it was
# authored against, and `verify_live_table_binding` RE-DERIVES that digest from the bytes on disk on
# every run. Editing any of the six live tables therefore turns this section RED until the candidate
# is re-authored and re-reviewed against the new content. Updating only the digest would be a
# deliberate defeat of the mechanism, not a fix — the manifests say so in their own comments.
#
# Scope note: this section runs over `ALL_CANDIDATES` (six), not `CANDIDATES` (this wave's five).
# The W3 manifest is the one with a `src/` consumer, and it carries the SAME binding at LOAD time in
# `maezo.tools.workers.adequacao_shadow` (pinned in `tests/unit/tools/workers/test_adequacao_shadow.py`).


def _forge(manifest: str, tmp_path: Path, *, drop: str | None = None, **changes: object) -> Path:
    """A SCRATCH copy of a shipped manifest with top-level keys changed/dropped.

    Round-tripped through YAML (so comments are lost) on purpose: these fixtures exist to exercise
    the DATA the readers see, and a data-level forge cannot accidentally pass because a comment
    happened to mention the right value.
    """
    data = load_manifest(DMN_DIR / manifest)
    if drop is not None:
        del data[drop]
    data.update(changes)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / manifest
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _binding_of(manifest: str) -> dict[str, str]:
    block = load_manifest(DMN_DIR / manifest)[LIVE_TABLE_BLOCK]
    assert isinstance(block, dict)
    return dict(block)


#: The shipped `glosa-triage` binding — the PASSING baseline every mutation row below differs from
#: by exactly one field.
_BASELINE_MANIFEST = "glosa-triage-shadow-candidate.yaml"
_BASELINE_BINDING = _binding_of(_BASELINE_MANIFEST)


@pytest.mark.parametrize(("manifest", "live"), sorted(ALL_CANDIDATES.items()))
def test_every_candidate_is_bound_to_the_live_table_content_it_was_authored_against(
    manifest: str, live: str
) -> None:
    """THE BINDING. The declared digest must equal the live table's bytes on disk, re-derived now.

    This is the assertion an edit to `spec/processes/dmn/<live>` breaks. It is deliberately stated
    twice — once through `verify_live_table_binding` (which enforces the whole block's shape) and
    once as a bare digest comparison — so that a future weakening of the helper cannot quietly
    retire the property itself.
    """
    binding = verify_live_table_binding(DMN_DIR / manifest)
    assert binding.live_table == (DMN_DIR / live).resolve()
    assert binding.sha256 == live_table_digest(DMN_DIR / live)
    assert _binding_of(manifest)[LIVE_TABLE_SHA256] == live_table_digest(DMN_DIR / live)


@pytest.mark.parametrize(("manifest", "live"), sorted(ALL_CANDIDATES.items()))
def test_the_binding_declares_the_same_table_the_manifest_targets(manifest: str, live: str) -> None:
    """`tabela_viva.path` and `alvo` are two declarations of one fact; they may not drift apart."""
    data = load_manifest(DMN_DIR / manifest)
    assert _binding_of(manifest)[LIVE_TABLE_PATH] == f"spec/processes/dmn/{live}" == data["alvo"]


def test_the_six_bindings_are_six_distinct_digests() -> None:
    """Anti-copy-paste: six different tables must produce six different digests. A repeated digest
    would mean one manifest is bound to a table it does not correct."""
    digests = {m: _binding_of(m)[LIVE_TABLE_SHA256] for m in ALL_CANDIDATES}
    assert len(set(digests.values())) == len(ALL_CANDIDATES), digests
    assert len(ALL_CANDIDATES) == 6


@pytest.mark.parametrize("manifest", sorted(ALL_CANDIDATES))
def test_a_manifest_without_a_binding_is_refused(manifest: str, tmp_path: Path) -> None:
    """The field is REQUIRED. An optional binding is a forgettable one, and a forgotten binding is
    exactly the silent carry-over this whole section exists to prevent."""
    with pytest.raises(LiveTableBindingError, match=LIVE_TABLE_BLOCK):
        verify_live_table_binding(_forge(manifest, tmp_path, drop=LIVE_TABLE_BLOCK))


@pytest.mark.parametrize(
    ("label", "block", "alvo"),
    [
        ("digest of no table at all", {**_BASELINE_BINDING, LIVE_TABLE_SHA256: "0" * 64}, None),
        ("placeholder digest", {**_BASELINE_BINDING, LIVE_TABLE_SHA256: "PLACEHOLDER_SHA256"}, None),
        ("blank digest", {**_BASELINE_BINDING, LIVE_TABLE_SHA256: "   "}, None),
        ("digest absent", {LIVE_TABLE_PATH: _BASELINE_BINDING[LIVE_TABLE_PATH]}, None),
        ("non-hex digest", {**_BASELINE_BINDING, LIVE_TABLE_SHA256: "z" * 64}, None),
        ("digest of the wrong length", {**_BASELINE_BINDING, LIVE_TABLE_SHA256: "abc123"}, None),
        ("digest not a string", {**_BASELINE_BINDING, LIVE_TABLE_SHA256: 12345}, None),
        ("path absent", {LIVE_TABLE_SHA256: _BASELINE_BINDING[LIVE_TABLE_SHA256]}, None),
        ("block is not a mapping", "spec/processes/dmn/glosa_triage.dmn", None),
        (
            "path outside the dmn directory",
            {**_BASELINE_BINDING, LIVE_TABLE_PATH: "spec/policies/ans/tiss-schema-pin.yaml"},
            "spec/policies/ans/tiss-schema-pin.yaml",
        ),
        (
            "traversal path",
            {**_BASELINE_BINDING, LIVE_TABLE_PATH: "spec/processes/dmn/../dmn/glosa_triage.dmn"},
            "spec/processes/dmn/../dmn/glosa_triage.dmn",
        ),
        (
            "path names a table that does not exist",
            {**_BASELINE_BINDING, LIVE_TABLE_PATH: "spec/processes/dmn/nao_existe.dmn"},
            "spec/processes/dmn/nao_existe.dmn",
        ),
        ("unknown key beside the two known ones", {**_BASELINE_BINDING, "sha512": "x"}, None),
    ],
)
def test_a_broken_binding_is_refused(label: str, block: object, alvo: str | None, tmp_path: Path) -> None:
    """MUTATION PROOF, one mutation per row: every way the binding can fail to hold RAISES.

    Each row is the SHIPPED `glosa-triage` binding with exactly the mutation `label` names (the
    `alvo` column moves the manifest's own target along with the path, so the rows about the
    directory/traversal guards reach that guard instead of stopping at the path-vs-`alvo` check).
    If the helper were vacuous every row here would pass.
    """
    changes: dict[str, object] = {LIVE_TABLE_BLOCK: block}
    if alvo is not None:
        changes["alvo"] = alvo
    with pytest.raises(LiveTableBindingError):
        verify_live_table_binding(_forge(_BASELINE_MANIFEST, tmp_path, **changes))


def test_a_binding_that_names_a_real_but_different_table_is_refused(tmp_path: Path) -> None:
    """It is the CONTENT that is compared, not the name — and the name is compared too.

    Two rows: (a) `path` moved to a real sibling table while `alvo` stays put — refused for naming a
    table the manifest does not target; (b) BOTH moved consistently — still refused, because the
    declared digest is `glosa_triage.dmn`'s and the table on disk is `carencia_check.dmn`. Row (b)
    is the one that proves a digest comparison is happening at all.
    """
    manifest = "glosa-triage-shadow-candidate.yaml"
    other = "spec/processes/dmn/carencia_check.dmn"
    moved = {**_binding_of(manifest), LIVE_TABLE_PATH: other}

    with pytest.raises(LiveTableBindingError, match="alvo"):
        verify_live_table_binding(_forge(manifest, tmp_path / "a", **{LIVE_TABLE_BLOCK: moved}))

    with pytest.raises(LiveTableBindingError, match="hashes to"):
        verify_live_table_binding(_forge(manifest, tmp_path / "b", alvo=other, **{LIVE_TABLE_BLOCK: moved}))


def test_a_stale_digest_is_refused_and_a_current_one_is_not(tmp_path: Path) -> None:
    """NON-VACUITY of the digest comparison itself, on one file, both directions.

    `stale` is the shipped binding with ONE hex character flipped — i.e. precisely what a live-table
    edit produces (a digest that no longer describes the bytes). `current` is the shipped binding
    untouched. Same manifest, same helper, opposite outcomes.
    """
    manifest = "upcoding-complexity-ceiling-shadow-candidate.yaml"
    current = _binding_of(manifest)
    digest = current[LIVE_TABLE_SHA256]
    flipped = ("b" if digest[0] != "b" else "c") + digest[1:]
    stale = {**current, LIVE_TABLE_SHA256: flipped}

    assert verify_live_table_binding(_forge(manifest, tmp_path / "ok", **{LIVE_TABLE_BLOCK: current}))
    with pytest.raises(LiveTableBindingError, match="hashes to"):
        verify_live_table_binding(_forge(manifest, tmp_path / "stale", **{LIVE_TABLE_BLOCK: stale}))


def test_enforcement_refuses_a_fully_ratified_manifest_whose_binding_is_stale(tmp_path: Path) -> None:
    """THE POINT OF THE WHOLE SECTION, stated at the enforcement seam.

    A manifest with all three ratification fields correctly filled — a real, accountable, complete
    ratification — STILL cannot be enforced once the table it reviewed has changed underneath it.
    The counter-proof sits beside it: the identical forge with the CURRENT digest evaluates fine, so
    the refusal is the staleness and not the forge.
    """
    manifest = "glosa-triage-shadow-candidate.yaml"
    ratified: dict[str, object] = {
        "ratificado": True,
        "revisor": "auditoria-contas (teste)",
        "ratificado_em": "2026-08-09",
    }
    current = _binding_of(manifest)
    stale = {**current, LIVE_TABLE_SHA256: "f" * 64}
    live = read_live_table(DMN_DIR / "glosa_triage.dmn")
    values: dict[str, object] = {
        "tipo_item": "",
        "categoria_normalizada": "desconhecida",
        "item_conforme_tabela": True,
        "divergencia_valor": False,
        "documentacao_anexa": True,
    }

    ok = _forge(manifest, tmp_path / "ok", **ratified, **{LIVE_TABLE_BLOCK: current})
    assert evaluate_for_enforcement(ok, live, values).saidas["roteamento"] == "ANALISE_HUMANA"

    stale_path = _forge(manifest, tmp_path / "stale", **ratified, **{LIVE_TABLE_BLOCK: stale})
    assert load_candidate_ratification(stale_path).ratificado is True, "ratification itself is intact"
    with pytest.raises(LiveTableBindingError, match="re-author"):
        evaluate_for_enforcement(stale_path, live, values)
