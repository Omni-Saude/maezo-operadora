"""R-055-DPO-DRAFTS — the DPO pre-designation drafts must not rot between merges.

Why this module exists (and why the ledger row declares IT, not an unrelated floor suite)
-----------------------------------------------------------------------------------------
`docs/sme-dispatch/dpo/` holds four RASCUNHO dossiers written for a signer who does not exist yet
(the encarregado of `R-027`/`D7-03`). They are pure prose ABOUT code — 40+ citations into `src/`,
`spec/` and `docs/` — and the adversarial verification of their first revision found the exact
failure mode that shape invites: the drafts were authored on `0433db0`, one merge later ten of
their `file:line` citations pointed at the wrong lines, one draft still described a PHI leak that
had been remediated in the meantime (HEL-05), and a `diff` block advertised as "o diff exato que o
encarregado aplicaria" did not apply (`git apply --check` -> "corrupt patch").

A document a human is asked to SIGN cannot be held by a hash over an untouched test file. This
module is the fence that actually goes RED when the drafts drift, and it holds the four things the
verification proved were not self-holding:

  1. every path and `path:line` / `file.py::symbol` citation in the drafts still resolves, and a
     line citation still lands on a line that carries its declared anchor text;
  2. the candidate diff hunks still apply to the real, untouched policy file (the `@@` counts and
     the context lines are re-read from disk here);
  3. the FLOOR the drafts promise the signer is still true — the retention matrix template is
     still `unratified`, `phi-business-key-remediation.yaml` is still `DRAFT`/`off`/not ratified,
     and every signature field in every draft is still BLANK (no agent ever fills one);
  4. the two measured claims the drafts rest their recommendations on — `redact_phi_vars` has
     exactly two production call sites, and `register_lgpd_workers` registers exactly four topics
     the BPMN does not declare — are re-derived from the tree, not trusted.

Same shape as `tests/unit/docs/test_contract_bpmn_dmn_citations.py` and
`tests/unit/docs/test_adr_amendments.py`: docs held to artifacts, mechanically, on every build.
"""

from __future__ import annotations

import copy
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from maezo.platform.lifecycle.erasure_plan import PERSISTENCE_LAYERS, IdentityResolution
from maezo.platform.lifecycle.legal_bases_matrix import REQUIRED_ENTRY_FIELDS
from maezo.tools.workers import lgpd
from maezo.tools.workers.base import WorkerBase
from maezo.tools.workers.phi_vars import PHI_FREE_TEXT_VARS, PHI_PROCESS_VARS

_REPO_ROOT = Path(__file__).parents[3]
_DPO_DIR = _REPO_ROOT / "docs" / "sme-dispatch" / "dpo"

#: The four RASCUNHO dossiers this fence holds. `PACKAGE.md` is the pre-existing dispatch package
#: (only its appended pointer section belongs to R-055), so it is held by a targeted test below
#: rather than swept for citations — its body predates this work and is not ours to re-anchor.
_DRAFTS: tuple[str, ...] = (
    "RETENTION-MATRIX-CANDIDATE.md",
    "PHI-DISPOSITIONS-RECOMMENDATION.md",
    "DSR-PROCEDURE-DRAFT.md",
    "PHI-BUSINESS-KEY-REMEDIATION-FIELDS-DRAFT.md",
)

_POLICY_YAML = "spec/policies/privacy/phi-business-key-remediation.yaml"
_RETENTION_TEMPLATE = "spec/policies/retention/UNRATIFIED-retention-matrix.template.yaml"


def _draft_paths() -> list[Path]:
    return [_DPO_DIR / name for name in _DRAFTS]


def _draft_text(name: str) -> str:
    return (_DPO_DIR / name).read_text(encoding="utf-8")


def _all_draft_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _draft_paths())


def test_the_four_drafts_exist() -> None:
    """Sanity: the fences below are not silently scanning zero files."""
    missing = [p.name for p in _draft_paths() if not p.is_file()]
    assert not missing, f"R-055 draft(s) missing from {_DPO_DIR}: {missing}"


# ---------------------------------------------------------------------------------------------
# 1. Citations
# ---------------------------------------------------------------------------------------------

#: Backtick-quoted repo paths the drafts cite. A path here must EXIST unless it is listed in
#: `_DELIBERATELY_ABSENT` below.
_PATH_RE = re.compile(
    r"`((?:src|spec|tests|scripts|docs|deploy|config)/[A-Za-z0-9_./*-]+"
    r"\.(?:py|yaml|yml|bpmn|dmn|md|sql|json))`"
)

#: Paths the drafts cite BECAUSE THEY DO NOT EXIST — each one is a named, declared gap the signer
#: is being told about. The assertion below is two-way: they must stay absent, so this fence goes
#: RED when one lands and the draft's "não construído / arquivo ainda não criado" prose becomes a
#: lie.
_DELIBERATELY_ABSENT: dict[str, str] = {
    "spec/policies/retention/retention-matrix.yaml": (
        "the NAME the ratified matrix would take; creating it is the encarregado's act (AF-07)"
    ),
    "docs/compliance/dsr-procedimento-manual.md": (
        "the promotion target of DSR-PROCEDURE-DRAFT.md; promoting it is the encarregado's act (R-029)"
    ),
    "docs/processes/contracts/signoffs/SP-OP-LGPD-DSR-001.signoff.yaml": (
        "the signoff the drafts explicitly do NOT create — `docs/sme-dispatch/README.md` rule 5 "
        "forbids an agent from supplying one"
    ),
    "src/maezo/platform/phi_vars.py": (
        "cited BY the drafts as the wrong path the brief used; the real module is "
        "src/maezo/tools/workers/phi_vars.py (declared drift, INFO)"
    ),
}


def test_every_repo_path_cited_by_the_drafts_exists_or_is_a_declared_absence() -> None:
    cited = {m for m in _PATH_RE.findall(_all_draft_text())}
    assert cited, "expected the drafts to cite repo paths; the regex found none (fence inert?)"

    missing = sorted(p for p in cited if p not in _DELIBERATELY_ABSENT and not (_REPO_ROOT / p).exists())
    assert not missing, f"draft(s) cite a repo path that does not exist: {missing}"

    landed = sorted(p for p in _DELIBERATELY_ABSENT if (_REPO_ROOT / p).exists())
    assert not landed, (
        f"path(s) the drafts describe as NOT existing now exist: {landed} — update the draft "
        "prose (and this map); a draft that keeps calling a landed artefact 'não construído' "
        "misleads the signer"
    )


#: Short forms the drafts use for readability, resolved to their canonical repo path. A short form
#: that appears in a draft without an entry here fails the test below — that is the point: no
#: ambiguous basename citation ever enters silently.
_SHORT_PATHS: dict[str, str] = {
    "auth.py": "src/maezo/tools/workers/auth.py",
    "base.py": "src/maezo/tools/workers/base.py",
    "events.py": "src/maezo/tools/workers/events.py",
    "lgpd.py": "src/maezo/tools/workers/lgpd.py",
    "reembolso.py": "src/maezo/tools/workers/reembolso.py",
    "inadimplencia.py": "src/maezo/tools/workers/inadimplencia.py",
    "phi_vars.py": "src/maezo/tools/workers/phi_vars.py",
    "transport.py": "src/maezo/tools/mcp_cibseven/transport.py",
    "phi_completeness.py": "src/maezo/platform/validation/phi_completeness.py",
    "erasure.py": "src/maezo/platform/erasure.py",
    "erasure_plan.py": "src/maezo/platform/lifecycle/erasure_plan.py",
    "retention.py": "src/maezo/platform/retention.py",
    "observability.py": "src/maezo/platform/observability.py",
    "helena/graph.py": "src/maezo/agents/helena/graph.py",
    "gateway/pseudonymizer.py": "src/maezo/gateway/pseudonymizer.py",
    "agents/valentina/graph.py": "src/maezo/agents/valentina/graph.py",
    "test_sp_op_escalation_001.py": "tests/integration/processes/test_sp_op_escalation_001.py",
    "scripts/ci/check_deviation_expiry.py": "scripts/ci/check_deviation_expiry.py",
}


def _resolve(cited_path: str) -> Path:
    if cited_path in _SHORT_PATHS:
        return _REPO_ROOT / _SHORT_PATHS[cited_path]
    return _REPO_ROOT / cited_path


#: `path:line` (and `path:start-end`) citations, each mapped to text that MUST appear on the cited
#: line (on the first line of a range). Keys use exactly the string the draft writes.
#:
#: The map is compared for EQUALITY against what the drafts actually cite, so adding a line
#: citation to a draft without registering its anchor here fails — a moving-line citation may
#: exist, but never unheld. (The house rule is "cite by symbol"; these three survive because the
#: line IS the evidence: two `raise` statements and one numbered protocol rule.)
_LINE_ANCHORS: dict[str, str] = {
    # The last surviving line citation, kept because the line IS the evidence: a NUMBERED rule in
    # a protocol document, not a moving code symbol. The two `erasure.py:152`/`:187` citations that
    # used to live here were retired in the post-`abb9d60` re-anchoring — the pgvector removal
    # shifted them to `:160`/`:195` and this fence went red, so they became
    # `ErasureManager.erase` / `.verify` symbol citations (BRIEF-COMMON's rule).
    "docs/sme-dispatch/README.md:109-111": (
        "No agent — this one included — ever creates, edits, or backdates a signoff file."
    ),
}

_LINE_CITATION_RE = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|yaml|yml|bpmn|dmn|md|sql)):(\d+)(?:-(\d+))?`")


def test_every_line_citation_is_registered_and_lands_on_its_anchor() -> None:
    found = {m.group(0)[1:-1] for m in _LINE_CITATION_RE.finditer(_all_draft_text())}
    assert found == set(_LINE_ANCHORS), (
        "the drafts' `path:line` citations and _LINE_ANCHORS have diverged.\n"
        f"  cited but unregistered: {sorted(found - set(_LINE_ANCHORS))}\n"
        f"  registered but no longer cited: {sorted(set(_LINE_ANCHORS) - found)}"
    )

    wrong: dict[str, str] = {}
    for citation, anchor in _LINE_ANCHORS.items():
        raw_path, _, span = citation.rpartition(":")
        start = int(span.split("-")[0])
        target = _resolve(raw_path)
        assert target.is_file(), f"citation {citation!r} names a file that does not exist"
        lines = target.read_text(encoding="utf-8").splitlines()
        if start > len(lines):
            wrong[citation] = f"file has only {len(lines)} lines"
        elif anchor not in lines[start - 1]:
            wrong[citation] = f"line {start} is {lines[start - 1].strip()!r}"
    assert not wrong, (
        f"stale `path:line` citation(s) in docs/sme-dispatch/dpo/ — re-anchor or cite by symbol: {wrong}"
    )


_SYMBOL_CITATION_RE = re.compile(r"`([A-Za-z0-9_./-]+\.py)::([A-Za-z_][A-Za-z0-9_.]*)`")


def _defines(text: str, name: str) -> bool:
    """True when `name` is DEFINED in `text` (def / async def / class / module- or class-level
    binding or annotation). Deliberately textual, not an import: several cited modules are
    expensive or side-effectful to import, and the claim being held is "this name still lives
    here", which the source text answers exactly."""
    escaped = re.escape(name)
    patterns = (
        rf"^\s*def\s+{escaped}\b",
        rf"^\s*async\s+def\s+{escaped}\b",
        rf"^\s*class\s+{escaped}\b",
        rf"^\s*{escaped}\s*[:=]",
    )
    return any(re.search(p, text, re.MULTILINE) for p in patterns)


def test_every_symbol_citation_resolves_in_the_file_it_names() -> None:
    unresolved: dict[str, str] = {}
    checked = 0
    for path in _draft_paths():
        for match in _SYMBOL_CITATION_RE.finditer(path.read_text(encoding="utf-8")):
            cited_path, dotted = match.group(1), match.group(2)
            key = f"{path.name} :: `{cited_path}::{dotted}`"
            base = cited_path.rsplit("/", 1)[-1]
            if not cited_path.startswith(("src/", "tests/", "scripts/")) and (
                cited_path not in _SHORT_PATHS and base not in _SHORT_PATHS
            ):
                unresolved[key] = (
                    f"ambiguous short path {cited_path!r} — register it in _SHORT_PATHS or cite "
                    "the full repo path"
                )
                continue
            target = _resolve(cited_path if cited_path in _SHORT_PATHS else cited_path)
            if not target.is_file():
                unresolved[key] = f"file not found: {target.relative_to(_REPO_ROOT)}"
                continue
            text = target.read_text(encoding="utf-8")
            checked += 1
            for part in dotted.split("."):
                if not _defines(text, part):
                    unresolved[key] = f"{part!r} is not defined in {target.relative_to(_REPO_ROOT)}"
                    break
    assert checked >= 20, f"expected the drafts to cite many symbols; only {checked} resolved"
    assert not unresolved, f"unresolved symbol citation(s) in the DPO drafts: {unresolved}"


# ---------------------------------------------------------------------------------------------
# 2. The candidate diff really applies
# ---------------------------------------------------------------------------------------------

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+),(\d+) \+(\d+),(\d+) @@")


_DIFF_BLOCK_RE = re.compile(r"^```diff\n(.*?)^```", re.MULTILINE | re.DOTALL)


def _diff_blocks_verbatim(text: str) -> list[str]:
    """The ```diff blocks EXACTLY as the document prints them — nothing added, nothing stripped.

    §Delta Δ1 is why this returns raw text and not parsed lines: the second block used to lack its
    `--- a/… / +++ b/…` header, and the earlier parser tolerated that (it only skipped header lines
    when present). A reader who copies the block out of the doc and runs `git apply` gets
    "patch fragment without header at line 1" — so the fence must consume exactly what the reader
    copies, with no reconstruction of any kind."""
    return _DIFF_BLOCK_RE.findall(text)


def _diff_blocks(text: str) -> list[list[str]]:
    return [block.splitlines() for block in _diff_blocks_verbatim(text)]


def test_each_candidate_diff_block_applies_verbatim_as_printed(tmp_path: Path) -> None:
    """§Delta Δ1 — hands each block, byte-for-byte as printed, to the real `git apply --check`.

    Every block individually AND both together must apply, because the document tells the
    encarregado to copy the block out and apply it. A block missing its file header, or with a
    hunk header that drifted off the file, fails here exactly as it would in their hands."""
    blocks = _diff_blocks_verbatim(_draft_text("PHI-BUSINESS-KEY-REMEDIATION-FIELDS-DRAFT.md"))
    assert len(blocks) == 2, f"expected the 4-field block + the `modo` companion, found {len(blocks)}"

    header = f"--- a/{_POLICY_YAML}\n+++ b/{_POLICY_YAML}\n"
    for index, block in enumerate(blocks, start=1):
        assert block.startswith(header), (
            f"```diff block {index} does not open with its own `--- a/…` / `+++ b/…` header — "
            "`git apply` refuses it with 'patch fragment without header at line 1'. Copy-pasting "
            "the block out of the document must be enough (§Delta Δ1)"
        )

    candidates = {"bloco 1": blocks[0], "bloco 2": blocks[1], "bloco 1+2": blocks[0] + blocks[1]}
    failures: dict[str, str] = {}
    for label, patch_text in candidates.items():
        patch_file = tmp_path / f"{label.replace(' ', '_').replace('+', 'e')}.patch"
        patch_file.write_text(patch_text, encoding="utf-8")
        completed = subprocess.run(
            ["git", "apply", "--check", str(patch_file)],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            failures[label] = (completed.stderr or completed.stdout).strip()
    assert not failures, (
        f"the candidate diff the draft advertises as appliable does NOT apply as printed: {failures}"
    )


def test_the_candidate_diff_hunks_still_apply_to_the_real_policy_file() -> None:
    """Re-reads every hunk of the FIELDS draft against the untouched policy file: the `@@` counts
    must describe the hunk body, and every context/removed line must be exactly what is on disk at
    that offset.

    This is the fence for the defect the verifier found: the first revision's three hunk headers
    under-described their bodies and `git apply --check` refused the block as a corrupt patch,
    while the document advertised it as "o diff exato que o encarregado aplicaria"."""
    policy_lines = (_REPO_ROOT / _POLICY_YAML).read_text(encoding="utf-8").splitlines()
    blocks = _diff_blocks(_draft_text("PHI-BUSINESS-KEY-REMEDIATION-FIELDS-DRAFT.md"))
    assert len(blocks) == 2, f"expected the 4-field block + the `modo` companion, found {len(blocks)}"

    hunks = 0
    for block in blocks:
        i = 0
        while i < len(block):
            header = _HUNK_HEADER_RE.match(block[i])
            if header is None:
                assert block[i].startswith(("--- ", "+++ ")), (
                    f"unexpected line outside a hunk in a ```diff block: {block[i]!r}"
                )
                i += 1
                continue
            old_start, old_len = int(header.group(1)), int(header.group(2))
            body: list[str] = []
            i += 1
            while i < len(block) and _HUNK_HEADER_RE.match(block[i]) is None:
                body.append(block[i])
                i += 1
            old_side = [line[1:] for line in body if line[:1] in (" ", "-")]
            assert len(old_side) == old_len, (
                f"hunk `@@ -{old_start},{old_len} ...` describes {old_len} pre-image line(s) but "
                f"its body carries {len(old_side)} — `git apply` refuses this as a corrupt patch"
            )
            actual = policy_lines[old_start - 1 : old_start - 1 + old_len]
            assert old_side == actual, (
                f"hunk at {_POLICY_YAML}:{old_start} no longer matches the file on disk.\n"
                f"  draft: {old_side}\n  disk : {actual}"
            )
            hunks += 1
    assert hunks == 3, f"expected 3 hunks across the two blocks, found {hunks}"


# ---------------------------------------------------------------------------------------------
# 3. The floor the drafts promise the signer
# ---------------------------------------------------------------------------------------------

_SIGNATURE_LINE_RE = re.compile(r"^\*\*(Ratificado por|Ouvido)[^*]*:\*\*(.*)$", re.MULTILINE)


def test_every_signature_field_in_every_draft_is_blank() -> None:
    """No agent ever supplies a human signature (`docs/sme-dispatch/README.md` rule 5). A filled
    field here is a fabricated approval, which is a compliance event — so it is a test, not a
    convention."""
    filled: dict[str, list[str]] = {}
    total = 0
    for path in _draft_paths():
        for match in _SIGNATURE_LINE_RE.finditer(path.read_text(encoding="utf-8")):
            total += 1
            remainder = match.group(2)
            # Only underscores, whitespace and further bold field labels may follow.
            if re.sub(r"[_\s]|\*\*[^*]*:\*\*", "", remainder):
                filled.setdefault(path.name, []).append(match.group(0).strip())
    assert total >= 6, f"expected the drafts to carry signature fields; found {total}"
    assert not filled, f"signature field(s) are NOT blank: {filled}"


def test_every_draft_carries_the_unratified_label() -> None:
    for path in _draft_paths():
        text = path.read_text(encoding="utf-8")
        assert "rascunho — pendente de designação e assinatura do encarregado" in text, (
            f"{path.name} lost its RASCUNHO label"
        )
        assert "recomendação — pendente de assinatura de" in text, (
            f"{path.name} lost its 'recomendação — pendente de assinatura' label"
        )


def test_the_policy_file_the_drafts_describe_is_still_unratified() -> None:
    """The FIELDS draft's whole premise: the file is untouched and the effective mode is `off`."""
    data = yaml.safe_load((_REPO_ROOT / _POLICY_YAML).read_text(encoding="utf-8"))
    assert data["status"] == "DRAFT"
    assert data["modo"] == "off"
    assert data["ratificacao"]["ratificado"] is False
    assert data["ratificacao"]["revisor"] is None
    assert data["ratificacao"]["ratificado_em"] is None


def _candidate_matrix_block() -> dict[str, object]:
    text = _draft_text("RETENTION-MATRIX-CANDIDATE.md")
    blocks = re.findall(r"^```yaml\n(.*?)^```", text, re.MULTILINE | re.DOTALL)
    assert len(blocks) == 1, f"expected exactly one fenced YAML block, found {len(blocks)}"
    loaded = yaml.safe_load(blocks[0])
    assert isinstance(loaded, dict)
    return loaded


def test_the_candidate_matrix_block_keeps_its_fail_closed_root_and_is_schema_valid() -> None:
    """The block is deliberately copy-safe: `unratified: true` makes `load_retention_matrix`
    refuse it even if someone copies it into a file before the signature. It must nonetheless be
    schema-valid against the REAL loader constant, so the signer's only remaining act is the
    decision, not a debugging session."""
    block = _candidate_matrix_block()
    assert block.get("unratified") is True, (
        "the candidate YAML lost its `unratified: true` root — a copy of it would now be LOADABLE "
        "before any human signed it"
    )
    assert block["ratificacao"]["ratificado"] is False
    assert block["ratificacao"]["revisor"] is None
    assert block["ratificacao"]["ratificado_em"] is None

    entries = block["categorias"]
    assert isinstance(entries, list) and entries
    seen: set[str] = set()
    for entry in entries:
        for field in REQUIRED_ENTRY_FIELDS:
            value = entry.get(field)
            assert isinstance(value, str) and value.strip(), (
                f"candidate entry {entry.get('categoria')!r} has empty/absent {field!r}"
            )
        assert entry["categoria"] not in seen, f"duplicate categoria {entry['categoria']!r}"
        seen.add(entry["categoria"])


def test_the_candidate_scope_b_layers_are_derived_from_persistence_layers() -> None:
    """§Delta-3 Δ3·1 — `escopo_b` in the SIGNABLE YAML block is held against the real
    `erasure_plan.PERSISTENCE_LAYERS`, in both directions:

      * every `(camada, tabela)` the block names must EXIST in the enumeration — a signer must
        never be asked to declare retention over a relation the platform does not know;
      * every named layer whose `resolucao` is `RETIRADA` must carry `retirada: true`, and no
        live layer may carry it.

    This is the fence for the defect `abb9d60` created: `0009_drop_pgvector` (DU-01-b, owner
    decision R-005) retired `semantica`/`agent_memory.embedding` — same enum as the
    `agent_checkpoints`/`agent_checkpoint_writes` pair `0006` retired — while the block still
    listed it under `camadas_cobertas` as live. Signing that block would have declared retention
    over a relation that no longer exists."""
    block = _candidate_matrix_block()
    escopo = block["escopo_b"]
    assert isinstance(escopo, dict)

    by_key = {(layer.camada, layer.tabela): layer for layer in PERSISTENCE_LAYERS}
    unknown: list[str] = []
    mismarked: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()

    for group in ("camadas_cobertas", "nao_cobertas"):
        entries = escopo[group]
        assert isinstance(entries, list) and entries, f"escopo_b.{group} is empty"
        for entry in entries:
            camada = entry["camada"]
            marked_retired = bool(entry.get("retirada"))
            for tabela in entry["tabelas"]:
                key = (camada, tabela)
                seen.add(key)
                layer = by_key.get(key)
                if layer is None:
                    unknown.append(f"{group}: {camada}/{tabela}")
                    continue
                really_retired = layer.resolucao is IdentityResolution.RETIRADA
                if really_retired and not marked_retired:
                    mismarked[f"{camada}/{tabela}"] = (
                        f"PERSISTENCE_LAYERS ordem {layer.ordem} says resolucao=RETIRADA "
                        f"({layer.migracao}) but the signable block does not mark it "
                        "`retirada: true` — the DPO would be signing retention over a relation "
                        "that no longer exists"
                    )
                elif marked_retired and not really_retired:
                    mismarked[f"{camada}/{tabela}"] = (
                        f"the block marks it `retirada: true` but PERSISTENCE_LAYERS ordem "
                        f"{layer.ordem} says resolucao={layer.resolucao.value} — the layer is "
                        "live and its retention IS a decision the DPO owes"
                    )

    assert not unknown, f"escopo_b names relation(s) absent from erasure_plan.PERSISTENCE_LAYERS: {unknown}"
    assert not mismarked, f"escopo_b disagrees with PERSISTENCE_LAYERS: {mismarked}"
    assert seen, "escopo_b named no relation at all (fence inert?)"

    # A retired layer must also be visible as retired in the prose, not only in the YAML.
    retired_named = {
        f"{camada}/{tabela}"
        for camada, tabela in seen
        if by_key[(camada, tabela)].resolucao is IdentityResolution.RETIRADA
    }
    text = _draft_text("RETENTION-MATRIX-CANDIDATE.md")
    for name in retired_named:
        tabela = name.split("/", 1)[1]
        assert "RETIRADA" in text and tabela in text, (
            f"{name} is retired but the draft's prose does not say so"
        )


def test_the_candidate_scope_b_enumerates_every_persistence_layer() -> None:
    """§Delta-4 Δ4·1 — COMPLETENESS, the direction its sibling above does not cover.

    The correctness fence judges only what the block NAMES, so DELETING a whole `camada` entry
    passed green. For a document a human signs, disappearance is the worse failure: a layer
    vanishes and the signer never learns it existed.

    The draft's §8.3 records the choice made here. Rather than keep a "prose-only" exception list
    — one more thing that rots silently, which is exactly how `semantica` survived `0009` inside
    the block as "live" — the two relations `0006` retired (`agent_checkpoints`,
    `agent_checkpoint_writes`) were ADDED to the block marked `retirada: true`. The signable block
    therefore enumerates ALL of `PERSISTENCE_LAYERS`, and this is a plain set equality with no
    exceptions to maintain."""
    escopo = _candidate_matrix_block()["escopo_b"]
    _assert_candidate_scope_complete(escopo)


def _assert_candidate_scope_complete(escopo: object) -> None:
    assert isinstance(escopo, dict)
    named = {
        (entry["camada"], tabela)
        for group in ("camadas_cobertas", "nao_cobertas")
        for entry in escopo[group]
        for tabela in entry["tabelas"]
    }
    real = {(layer.camada, layer.tabela) for layer in PERSISTENCE_LAYERS}

    missing = sorted(real - named)
    assert not missing, (
        f"escopo_b does NOT enumerate every relation in erasure_plan.PERSISTENCE_LAYERS — "
        f"missing: {missing}. A layer removed from the signable block disappears from the "
        "encarregado's review scope without anyone deciding to remove it; retired layers STAY, "
        "marked `retirada: true` (draft §8.2/§8.3)"
    )
    assert named == real, f"escopo_b names relations the enumeration does not: {sorted(named - real)}"


def test_the_nine_portal_human_command_and_assignment_relations_are_explicitly_uncovered() -> None:
    escopo = _candidate_matrix_block()["escopo_b"]
    assert isinstance(escopo, dict)

    def keys(group: str) -> set[tuple[str, str]]:
        return {(entry["camada"], tabela) for entry in escopo[group] for tabela in entry["tabelas"]}

    expected = {
        ("identidade_portal", "portal_login_transactions"),
        ("identidade_portal", "portal_code_claims"),
        ("identidade_portal", "portal_sessions"),
        ("identidade_portal", "portal_memberships"),
        ("comando_humano", "human_command_delivery"),
        ("comando_humano", "human_command_outbox"),
        ("autoridade_atribuicao", "portal_assignment_source"),
        ("autoridade_atribuicao", "portal_assignment_publications"),
        ("autoridade_atribuicao", "portal_assignment_receipt_source"),
    }
    uncovered = keys("nao_cobertas")
    covered = keys("camadas_cobertas")
    retired = {
        (entry["camada"], tabela)
        for group in ("camadas_cobertas", "nao_cobertas")
        for entry in escopo[group]
        if entry.get("retirada") is True
        for tabela in entry["tabelas"]
    }
    assert expected <= uncovered
    assert expected.isdisjoint(covered)
    assert expected.isdisjoint(retired)
    # WP-J1-09 added `inbox_escalonamento`/`escalation_team_notice` (migration 0015), declared
    # NOT covered: the row is addressed to a staff candidate GROUP and has no subject column, and
    # no retention disposition has been ratified for it. The signer's scope GROWS with a new
    # relation and must never shrink — 25 -> 26 total, 14 -> 15 uncovered.
    assert len(covered | uncovered) == 26
    assert len(uncovered) == 15
    by_key = {(layer.camada, layer.tabela): layer for layer in PERSISTENCE_LAYERS}
    assert all(by_key[key].count_statement is None for key in expected)
    assert {key: by_key[key].resolucao for key in expected} == {
        ("identidade_portal", "portal_login_transactions"): IdentityResolution.SEM_COLUNA_DE_TITULAR,
        ("identidade_portal", "portal_code_claims"): IdentityResolution.SEM_COLUNA_DE_TITULAR,
        ("identidade_portal", "portal_sessions"): IdentityResolution.SEM_COLUNA_DE_TITULAR,
        ("identidade_portal", "portal_memberships"): IdentityResolution.PONTE_AUSENTE,
        ("comando_humano", "human_command_delivery"): IdentityResolution.SEM_COLUNA_DE_TITULAR,
        ("comando_humano", "human_command_outbox"): IdentityResolution.PONTE_AUSENTE,
        ("autoridade_atribuicao", "portal_assignment_source"): IdentityResolution.SEM_COLUNA_DE_TITULAR,
        ("autoridade_atribuicao", "portal_assignment_publications"): (
            IdentityResolution.SEM_COLUNA_DE_TITULAR
        ),
        ("autoridade_atribuicao", "portal_assignment_receipt_source"): (
            IdentityResolution.SEM_COLUNA_DE_TITULAR
        ),
    }


def test_candidate_scope_completeness_fence_rejects_a_missing_live_relation() -> None:
    escopo = copy.deepcopy(_candidate_matrix_block()["escopo_b"])
    assert isinstance(escopo, dict)
    command_group = next(entry for entry in escopo["nao_cobertas"] if entry["camada"] == "comando_humano")
    command_group["tabelas"].remove("human_command_outbox")
    with pytest.raises(AssertionError, match="does NOT enumerate every relation"):
        _assert_candidate_scope_complete(escopo)


def test_the_retention_matrix_template_is_still_the_unratified_placeholder() -> None:
    data = yaml.safe_load((_REPO_ROOT / _RETENTION_TEMPLATE).read_text(encoding="utf-8"))
    assert data.get("unratified") is True


# ---------------------------------------------------------------------------------------------
# 4. The measured claims the recommendations rest on
# ---------------------------------------------------------------------------------------------


def test_redact_phi_vars_still_has_exactly_the_two_production_call_sites_the_draft_names() -> None:
    """`PHI-DISPOSITIONS-RECOMMENDATION.md` §3.0 replaced an overclaim ("todos os oito nomes já são
    suprimidos na egressão hoje") with a MEASUREMENT: two call sites, worker egress only. If a
    third appears, the measurement — and the "SUPRIMIR (manter)" that rests on it — must be
    re-read before a DPO signs it."""
    owner = _REPO_ROOT / "src" / "maezo" / "tools" / "workers" / "phi_vars.py"
    call_sites = {
        str(py.relative_to(_REPO_ROOT))
        for py in (_REPO_ROOT / "src").rglob("*.py")
        if py != owner and "redact_phi_vars(" in py.read_text(encoding="utf-8")
    }
    assert call_sites == {
        "src/maezo/tools/workers/auth.py",
        "src/maezo/tools/mcp_cibseven/transport.py",
    }, f"the production call sites of redact_phi_vars changed: {sorted(call_sites)}"

    text = _draft_text("PHI-DISPOSITIONS-RECOMMENDATION.md")
    assert "exatamente dois" in text and "SendDenialNoticeWorker.execute" in text


def test_the_two_names_the_start_chokepoint_leaves_unscrubbed_are_still_those_two() -> None:
    """§3.2 of the dispositions draft is entirely about this: of the eight `PHI_PROCESS_VARS`,
    `matricula_beneficiario` and `cid10_referencia` are DECLARED out of `PHI_FREE_TEXT_VARS` and
    cross the agent->engine edge unchanged."""
    unscrubbed_at_the_start_edge = set(PHI_PROCESS_VARS - PHI_FREE_TEXT_VARS)
    assert unscrubbed_at_the_start_edge == {"matricula_beneficiario", "cid10_referencia"}
    assert len(PHI_PROCESS_VARS) == 8


def test_the_two_dsr_free_text_names_are_still_outside_both_name_anchored_controls() -> None:
    """The MAJOR the DSR runbook now discloses in §2.4: the two variables the runbook makes a human
    type are covered by NEITHER name-anchored control. If either gets listed, §2.4's instruction
    stops being necessary and the draft must be re-read."""
    for name in ("detalhes_requisicao", "fundamentacao_legal"):
        assert name not in PHI_PROCESS_VARS, (
            f"{name} is now in PHI_PROCESS_VARS — DSR-PROCEDURE-DRAFT.md §2.4 and §6 describe a "
            "risk that no longer exists"
        )
        assert name not in PHI_FREE_TEXT_VARS, (
            f"{name} is now in PHI_FREE_TEXT_VARS — DSR-PROCEDURE-DRAFT.md §2.4 must be re-read"
        )
    runbook = _draft_text("DSR-PROCEDURE-DRAFT.md")
    assert "LEIA §2.4 ANTES DE DIGITAR" in runbook
    assert "texto mínimo e pseudonimizado" in runbook


def _bpmn_topics() -> set[str]:
    bpmn = _REPO_ROOT / "spec" / "processes" / "bpmn" / "SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn"
    ns = {"camunda": "http://camunda.org/schema/1.0/bpmn"}
    root = ET.parse(bpmn).getroot()
    return {
        topic for element in root.iter() for topic in (element.get(f"{{{ns['camunda']}}}topic"),) if topic
    }


#: pt-BR cardinals, so the runbook's PROSE has to state the number the tree yields. Deliberately
#: small: an orphan set outside this range means something structural changed and a human should
#: read the reconciliation document, not extend a lookup table.
_PT_BR_CARDINAL: dict[int, str] = {0: "zero", 1: "um", 2: "dois", 3: "três", 4: "quatro", 5: "cinco"}


def _registered_lgpd_topics() -> set[str]:
    """Every `operadora.lgpd.*` topic `register_lgpd_workers` puts on a harness, DERIVED from the
    module: each `WorkerBase` subclass defined in `lgpd` plus each module-level `_*_TOPIC`
    constant. Nothing is hardcoded — `abb9d60` (train #326) deleted `PublishCompletedWorker`
    (R-H / owner decision R-103) and the previous hardcoded tuple raised `AttributeError` instead
    of reporting the real, smaller orphan set."""
    topics: set[str] = set()
    for name in dir(lgpd):
        obj = getattr(lgpd, name)
        if (
            isinstance(obj, type)
            and issubclass(obj, WorkerBase)
            and obj is not WorkerBase
            and obj.__module__ == lgpd.__name__
        ):
            topics.add(obj().topic)
        elif name.endswith("_TOPIC") and isinstance(obj, str):
            topics.add(obj)
    return {t for t in topics if t.startswith("operadora.lgpd.")}


def test_the_runbook_states_the_orphan_topic_count_the_tree_actually_yields() -> None:
    """The count is DERIVED (registered `operadora.lgpd.*` topics minus the BPMN's `camunda:topic`
    set) and the runbook's §5 prose must state it in words.

    History this fence carries, in every direction it has been pushed: the first revision said
    "os três" when the tree said four (`publish_completed` had no service task); the
    post-`abb9d60` re-anchoring said "três" again because R-H/R-103 DELETED that worker; R-181
    then collapsed the last three (O2-O4) onto the modelled `operadora.lgpd.execute_request` and
    the derived count is now ZERO (§8.2). Hardcoding any of those numbers is what made the prose
    rot, so neither the set nor the number is written down here — only the derivation.

    The fence is NOT inert at zero: it now holds the CLOSED state (a returning orphan goes red),
    and it still binds the prose, plus the OTHER side of the drift — the one BPMN topic that
    remains worker-less, which is DPO-gated and must stay named."""
    orphans = _registered_lgpd_topics() - _bpmn_topics()
    assert orphans == set(), (
        f"an orphan LGPD code topic is back: {sorted(orphans)} — R-181 (§8.2) collapsed the last "
        "three into operadora.lgpd.execute_request; the runbook's §5 and the reconciliation "
        "document both need re-reading before a DPO signs"
    )

    count = len(orphans)
    assert count in _PT_BR_CARDINAL, f"unexpected orphan count {count}: {sorted(orphans)}"
    runbook = _draft_text("DSR-PROCEDURE-DRAFT.md")
    expected = f"**{_PT_BR_CARDINAL[count]}** tópicos que o BPMN não declara"
    assert expected in runbook, (
        f"DSR-PROCEDURE-DRAFT.md §5 does not state the derived orphan count: expected "
        f"{expected!r} (orphans = {sorted(orphans)})"
    )
    assert "docs/compliance/lgpd-topic-reconciliation.md" in runbook

    # The other side of the same drift, also derived: BPMN topics with NO worker. Exactly one —
    # `compile_data_package` (#55 R-C) — and the runbook must keep naming it, because it is
    # DPO-gated (the legal-bases / retention matrix `ST_CompilarPacote` must carry).
    # `_bpmn_topics()` is unfiltered: it also yields the SHARED generic publisher
    # `operadora.events.publish` (5 `ST_Publish*` tasks), which `register_events_workers` serves
    # and `register_lgpd_workers` deliberately does not (ADR-0026 §2b). Only the `operadora.lgpd.*`
    # namespace is this module's to answer for.
    sem_worker = {t for t in _bpmn_topics() if t.startswith("operadora.lgpd.")} - _registered_lgpd_topics()
    assert sem_worker == {"operadora.lgpd.compile_data_package"}, (
        f"the set of worker-less BPMN LGPD topics changed: {sorted(sem_worker)} — the runbook's "
        "§8.2 and the reconciliation document need re-reading before a DPO signs"
    )
    for topic in sem_worker:
        assert topic.rsplit(".", 1)[-1] in runbook, (
            f"worker-less BPMN topic {topic!r} is not named in the runbook"
        )


def test_publish_completed_stayed_retired_after_abb9d60() -> None:
    """R-H / owner decision R-103 landed in `main` `abb9d60` (train #326): `PublishCompletedWorker`
    and its registration were REMOVED. §8.1 of the runbook records that as the reason the orphan
    count fell back to three. If the worker or its topic returns, that addendum is wrong."""
    assert not hasattr(lgpd, "PublishCompletedWorker")
    assert "operadora.lgpd.publish_completed" not in _registered_lgpd_topics()
    runbook = _draft_text("DSR-PROCEDURE-DRAFT.md")
    assert "Reancoragem pós-merge `abb9d60`" in runbook
    assert "R-103" in runbook


# ---------------------------------------------------------------------------------------------
# 5. PACKAGE.md's pointer section (the only part of that file R-055 owns)
# ---------------------------------------------------------------------------------------------


def test_package_pointer_does_not_claim_sc07_landed_on_main() -> None:
    """The verifier resolved this: the SC-07 annotation exists on PR #327's branch
    (`r5/observability-wiring`) and is NOT an ancestor of `main`. A hedge in a report does not
    license an unhedged claim in the repo."""
    text = (_DPO_DIR / "PACKAGE.md").read_text(encoding="utf-8")
    sc07 = [line for line in text.splitlines() if "`SC-07`" in line and line.startswith("| 5 |")]
    assert len(sc07) == 1, "expected exactly one SC-07 row in the R-055 pointer table"
    row = sc07[0]
    assert "pousou pela via de observabilidade" not in row, (
        "PACKAGE.md again claims SC-07 landed; it is on PR #327's branch, not on main"
    )
    assert "#327" in row and "não foi mergeado" in row
