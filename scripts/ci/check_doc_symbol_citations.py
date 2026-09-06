#!/usr/bin/env python3
"""CI gate: `docs/adr/*.md` citations must resolve against the real tree (R-089).

Non-vacuity case (the defect that MOTIVATED this gate — gap `ADR-0026-0028-STALE-ANSCRON`,
`OWNER-DECISIONS-REGISTER` R-089, 2026-09-06; FIXED FORWARD by the reparo below, 2026-09-06):
`docs/adr/0026-worker-standardization.md:72` used to quote a Python import example —
``from maezo.tools.workers.ans_cron import check_calendar, trigger_submissions`` — as evidence that
"tests bind to the *functions*, not classes." `check_calendar` had been **removed** (not renamed)
from `ans_cron.py` once the ANS-cron taxonomy reconciliation (GAP-ANS-1/ANS-CRON-DEAD-CODE) let
`ADR-0028` §7's own gate fire ("only after 100% parity in CI: delete the Python re-implementation").
Nobody had re-checked the quoted example against the tree. The owner's approved decision for R-089
ordered the two stale citations (this one, and `docs/adr/0028-dmn-evaluation-engine-side.md:192`'s
`ans_cron.py:116` row) corrected IN PLACE — a pointed, owner-authorised exception to the default
`docs/adr/README.md:8` no-rewrite convention for exactly these two named lines (reparo F2/
VER-ADR-BATCH, 2026-09-06; see the `CORRECAO 2026-09-06` addenda in both files for the record of
the change). Both citations resolve cleanly today. This script remains so the NEXT rot of this kind
— whichever citation drifts next — is caught the same day, not left for a reader to notice.

Scope: `docs/adr/*.md` **only**. `ADR-PHANTOM-PATH-RESIDUE-NON-ADR`: roughly a dozen known
phantom-path citations live in OTHER doc trees (reports, plans, review packets) — this gate does
not scan them and makes no claim about them; widening scope beyond `docs/adr/` is a separate,
larger decision for the owner.

Three citation shapes are HARD-CHECKED (fail the build on an unresolved one, unless explicitly
disclosed — see `_DISCLOSED_ROT` below):

  1. **Symbol citations** — `path/to/file.py::Symbol` or `path/to/file.py::Class.method`, the
     convention already used by ~40 existing citations across `docs/adr/*.md` (e.g. ADR-0029,
     ADR-0042, ADR-0044). FAILS if `path` does not resolve to a tracked file, or if `Symbol` does
     not resolve via AST in that file (a class, a module- or class-level function/method, or a
     module- or class-level assignment/annotated-assignment/import name).
  2. **Quoted Python import statements** — a backtick- or code-fence-quoted
     ``from dotted.module import name1, name2`` line (the ADR-0026 case: an inline code example,
     not a `::` citation, but the same underlying claim — "these names exist in this module"). The
     dotted module is resolved via the `maezo.` package convention (`maezo.x.y` ->
     `src/maezo/x/y.py`); FAILS if the module does not resolve, or if any imported name is not a
     symbol of that module.
  3. **Bare line citations, file existence only** (reparo F4/VER-ADR-BATCH, 2026-09-06) —
     `path/to/file.py:116` or `:116-120`. A bare line citation cannot be proven wrong without
     knowing what the line USED to say — only that the FILE still exists, which line-drift alone
     does not violate — so the LINE NUMBER stays purely informational (see the `line_citation_count`
     in the render output: 753 counted, none individually vetted). But file existence IS cheap and
     reliable, and was left unchecked entirely until this reparo measured 6 live citations (5
     distinct paths) across `docs/adr/*.md` naming a file absent from the tree — one of them inside
     ADR-0041, whose purpose is reconciling exactly this species of claim. FAILS only if the path
     resolves to nothing (via the same `resolve_path_candidates` used by shapes 1-2), UNLESS the
     path either (a) contains a literal `...` (an elided placeholder in prose, e.g. `0018-...md` in
     a summary table — no real filename in this tree has three consecutive dots) or (b) falls under
     a directory this repo's `.gitignore` excludes (e.g. `docs/prompts/` — untracked BY
     CONSTRUCTION, so `git ls-files` can never confirm it either way). Both are structural RULES,
     not `_DISCLOSED_ROT` allowlist entries — see `is_ellipsis_artifact_path` /
     `gitignored_directory_prefixes`. A line citation immediately preceded by an
     ``<!-- anchor: ... -->``-style HTML comment naming the same file is treated as a deliberately
     pinned, stable anchor and is excluded from both the rot-prone count and the file-existence
     check (none exist in the corpus today; the mechanism is here for the day one is added
     deliberately).

**Disclosed rot** (`_DISCLOSED_ROT`): a small, dated, named allowlist of citations already known to
be unresolved when this gate was built — never a substitute for fixing forward, and never silent.
Each entry is actively re-checked: if the citation now RESOLVES, the entry is stale and the gate
fails, telling the maintainer to delete it (an allowlist that nobody prunes is the same disclosure
failure this gate exists to catch). Seven entries ship with this gate today: one pre-existing
symbol-citation rot (ADR-0022, unrelated to R-089), and six bare-line-citation rot found by reparo
F4 (five in ADR-0024/0041, genuinely never-built modules; two in ADR-0037/0025, citations that were
always cross-repo and could never resolve against THIS tree) — see the `_DISCLOSED_ROT` tuple's own
inline comments for the per-entry evidence, not repeated here.

  - `docs/adr/0022-mcp-in-process-boot.md` :: `src/maezo/runtime/tool_wiring.py::build_tool_invoker`
    — genuine PRE-EXISTING rot found while building this gate, unrelated to R-089's own scope
    (`tool_wiring.py` does not exist; already disclosed independently in
    `src/maezo/tools/mcp_cibseven/__init__.py`'s module docstring: "no `ToolRegistry`/
    `tool_wiring.py`/`build_tool_invoker` exists anywhere in this v2 tree"). Tracked as an open row
    in `docs/review-queue.md`; deliberately NOT fixed here (out of scope for R-089).

  (Historical: this gate originally shipped a SECOND entry for
  `docs/adr/0026-worker-standardization.md`'s `check_calendar` import citation — its own
  non-vacuity case. Reparo F2/VER-ADR-BATCH (2026-09-06, terceiro agente) FIXED that citation
  forward, per the owner's decision for R-089, instead of leaving it allowlisted; the entry was
  removed once the citation resolved, exactly as the self-audit above demands of any entry.)

Wired into `make check-doc-symbol-citations` and `.github/workflows/ci.yml`'s `validate-artifacts`
job. Uses `git ls-files` only (no `git merge-base`/history walk), so it runs unchanged in a shallow
clone (`git clone --depth 1`) — proven by `tests/unit/ci/test_check_doc_symbol_citations.py`.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Citation extraction
# ---------------------------------------------------------------------------

#: `path.py::Symbol` / `path.py::Class.method` (also tolerates non-.py extensions, though none are
#: used in the corpus today — kept general per the module docstring's shape 1).
_SYMBOL_CITATION_RE = re.compile(
    r"(?P<path>[A-Za-z0-9_./-]+\.(?:py|bpmn|yaml|yml|dmn))::(?P<symbol>[A-Za-z0-9_.]+)"
)

#: A quoted `from dotted.module import a, b, c` statement (module must be under the `maezo` package
#: for this repo's path convention to resolve it; anything else is left alone rather than guessed).
_IMPORT_CITATION_RE = re.compile(
    r"from (?P<module>maezo(?:\.[A-Za-z0-9_]+)+) import (?P<names>[A-Za-z0-9_,\s]+)"
)

#: Bare `path.py:NNN` / `path.py:NNN-MMM` — NOT immediately preceded by another `:` (which would
#: make it the second half of a `::Symbol` citation instead).
_LINE_CITATION_RE = re.compile(
    r"(?<!:)\b(?P<path>[A-Za-z0-9_./-]+\.(?:py|md|yaml|yml|bpmn|dmn)):(?P<line>\d+)(?:-\d+)?\b"
)

#: An `<!-- anchor: ... -->`-style comment naming the file it pins — see shape 3 in the docstring.
_ANCHOR_COMMENT_RE = re.compile(r"<!--\s*anchor:\s*(?P<path>\S+)\s*-->")


@dataclass(frozen=True)
class SymbolCitation:
    doc: str
    doc_line: int
    path: str
    symbol: str


@dataclass(frozen=True)
class ImportCitation:
    doc: str
    doc_line: int
    module: str
    names: tuple[str, ...]


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def extract_symbol_citations(doc_name: str, text: str) -> list[SymbolCitation]:
    return [
        SymbolCitation(doc_name, _line_number(text, m.start()), m.group("path"), m.group("symbol"))
        for m in _SYMBOL_CITATION_RE.finditer(text)
    ]


def extract_import_citations(doc_name: str, text: str) -> list[ImportCitation]:
    citations: list[ImportCitation] = []
    for m in _IMPORT_CITATION_RE.finditer(text):
        names = tuple(n.strip() for n in m.group("names").split(",") if n.strip())
        if names:
            doc_line = _line_number(text, m.start())
            citations.append(ImportCitation(doc_name, doc_line, m.group("module"), names))
    return citations


def extract_line_citations(text: str) -> list[tuple[str, int]]:
    """`(path, doc_line)` for every bare line citation NOT paired with an anchor comment."""
    anchored_paths = {m.group("path") for m in _ANCHOR_COMMENT_RE.finditer(text)}
    out: list[tuple[str, int]] = []
    for m in _LINE_CITATION_RE.finditer(text):
        if m.group("path") in anchored_paths:
            continue
        out.append((m.group("path"), _line_number(text, m.start())))
    return out


# ---------------------------------------------------------------------------
# Reparo F4/VER-ADR-BATCH (2026-09-06, terceiro agente): file-existence for bare line citations
# ---------------------------------------------------------------------------
#
# Shape 3 (bare `path:line` citations) used to be COLLECTED but never hard-checked at all, on the
# stated grounds that "a bare `path:line` citation cannot be proven wrong without knowing what the
# line USED to say — only that the FILE still exists, which line-drift alone does not violate."
# That distinction was correct but the gate never acted on the half of it that IS cheap and
# reliable: file existence. Measured over the real corpus at the time of this reparo, 6 of the 753
# bare line citations named a path absent from the tree entirely (not merely line-drifted) — one
# of them (`runtime/inbound_driver.py`) inside ADR-0041, the very ADR whose purpose is reconciling
# this species of phantom claim. This section hard-checks file existence only; the line number
# itself stays informational (see `test_bare_line_citation_with_a_drifted_line_number_still_passes`).
#
# Two RULES (not allowlist entries) exclude paths that a file-existence check would flag for
# reasons that have nothing to do with citation rot:
#
#   - **Ellipsis artefacts.** A cited "path" containing a literal `...` (e.g. `docs/adr/0018-...md`
#     inside ADR-0040's own two-column summary table) is prose shorthand for an elided run of
#     similar filenames, not a real path — no tracked filename in this repo has ever contained
#     three consecutive dots (`git ls-files | grep -c '\.\.\.'` is 0). Structural, not per-citation.
#   - **Gitignored paths.** A citation under a directory this repo's own `.gitignore` excludes
#     (e.g. `docs/prompts/`) can never be confirmed via `git ls-files`, which is this gate's ONLY
#     source of truth (by design, for shallow-clone compatibility — no `git status`/working-tree
#     walk). Treating an untracked-by-construction path as "not found" would be a false positive
#     baked into the gate's own architecture, not a fact about the citation's health.
_ELLIPSIS_ARTIFACT_MARKER = "..."


def is_ellipsis_artifact_path(path: str) -> bool:
    """See the module-level note above shape 3's file-existence check."""
    return _ELLIPSIS_ARTIFACT_MARKER in path


def gitignored_directory_prefixes(repo_root: Path) -> tuple[str, ...]:
    """Directory-pattern lines (ending in `/`) from this repo's own `.gitignore`, read fresh each
    call (a small file; no caching needed) — see the module-level note above shape 3's
    file-existence check for why these are excluded structurally, not via `_DISCLOSED_ROT`."""
    gitignore_path = repo_root / ".gitignore"
    try:
        lines = gitignore_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    return tuple(
        stripped
        for raw in lines
        if (stripped := raw.strip()) and not stripped.startswith("#") and stripped.endswith("/")
    )


def is_under_gitignored_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


# ---------------------------------------------------------------------------
# Tree resolution
# ---------------------------------------------------------------------------


def tracked_files(repo_root: Path) -> list[str]:
    """`git ls-files` — works unchanged in a shallow clone (no history needed)."""
    proc = subprocess.run(["git", "ls-files"], cwd=repo_root, capture_output=True, text=True, check=True)
    return proc.stdout.splitlines()


def resolve_path_candidates(cited: str, tracked: list[str]) -> list[str]:
    """Exact match, then suffix match (`a/b.py` cites `x/a/b.py`), then bare-basename match.

    Permissive by design: many citations in this corpus omit the `src/maezo/` prefix
    (`gateway/pseudonymizer.py::...`) or cite a bare filename (`pep.py:71`). Returning every
    candidate (rather than the first) lets the caller try each before declaring a symbol missing —
    important for basenames that collide across agents (`graph.py` exists once per agent).
    """
    if cited in tracked:
        return [cited]
    suffix_hits = [t for t in tracked if t.endswith("/" + cited)]
    if suffix_hits:
        return suffix_hits
    base = cited.rsplit("/", 1)[-1]
    return [t for t in tracked if t.rsplit("/", 1)[-1] == base]


def module_to_path(module: str) -> str:
    """`maezo.tools.workers.ans_cron` -> `src/maezo/tools/workers/ans_cron.py` (this repo's one
    package root, `src/`)."""
    return "src/" + module.replace(".", "/") + ".py"


class _SymbolCollector(ast.NodeVisitor):
    """Collects every name resolvable as `Symbol` or `Class.Symbol` from a module's AST.

    Deliberately permissive (also walks into function bodies, collecting nested assignments) —
    this gate's job is catching a symbol that is GONE, not linting which symbols "should" be
    citable. A false negative (missing a symbol that does exist) would wrongly fail the build; a
    false positive (accepting a symbol that isn't really public API) does no harm here.
    """

    def __init__(self) -> None:
        self.symbols: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.symbols.add(node.name)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.symbols.add(f"{node.name}.{child.name}")
            elif isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        self.symbols.add(f"{node.name}.{target.id}")
            elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                self.symbols.add(f"{node.name}.{child.target.id}")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.symbols.add(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.symbols.add(node.name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.symbols.add(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self.symbols.add(node.target.id)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.symbols.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.symbols.add((alias.asname or alias.name).split(".")[0])
        self.generic_visit(node)


def collect_python_symbols(source: str) -> set[str] | None:
    """`None` means "could not parse" (syntax error) — caller treats that as unresolved, not as an
    empty-but-valid symbol set, so a citation into an unparseable file fails loudly rather than
    silently."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    collector = _SymbolCollector()
    collector.visit(tree)
    return collector.symbols


def resolve_symbol(repo_root: Path, path_candidates: list[str], symbol: str) -> bool:
    for candidate in path_candidates:
        full = repo_root / candidate
        try:
            content = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if candidate.endswith(".py"):
            symbols = collect_python_symbols(content)
            if symbols is not None and symbol in symbols:
                return True
        elif symbol in content:  # non-.py target: a plain substring/id match (e.g. BPMN `id="..."`)
            return True
    return False


# ---------------------------------------------------------------------------
# Disclosed rot — see the module docstring
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DisclosedRot:
    doc: str
    citation_repr: str  # human-readable form matched against a failure's own repr, below
    reason: str


_DISCLOSED_ROT: tuple[DisclosedRot, ...] = (
    # NOTE (reparo F2/VER-ADR-BATCH, 2026-09-06, terceiro agente): the R-089 entry that used to
    # live here (`0026-worker-standardization.md` / `import maezo.tools.workers.ans_cron::check_calendar`)
    # is REMOVED, not merely edited — the owner's approved decision ordered the import example fixed
    # in place (see `docs/adr/0026-worker-standardization.md`'s `CORRECAO 2026-09-06` addendum), so
    # the citation resolves cleanly now and would itself trip the stale-allowlist self-audit below if
    # left here. `docs/adr/0028-dmn-evaluation-engine-side.md`'s sibling citation (`ans_cron.py:116`)
    # was ALSO fixed forward (re-anchored to `...bpmn::BRT_Calendario`, a `::symbol` citation) in
    # the same reparo — before that it was a bare `path:line` citation, which at the time was
    # collected but never hard-checked; F4/VER-ADR-BATCH (below) later added a file-existence check
    # for that shape too, but by then `ans_cron.py:116` was already gone from the corpus, so there
    # was nothing to allowlist for it either way.
    DisclosedRot(
        doc="0022-mcp-in-process-boot.md",
        citation_repr="src/maezo/runtime/tool_wiring.py::build_tool_invoker",
        reason=(
            "pre-existing rot found while building this gate (2026-09-06), unrelated to R-089's "
            "scope (ADR-0026/0028) — `tool_wiring.py` does not exist. Already independently "
            "disclosed in src/maezo/tools/mcp_cibseven/__init__.py's module docstring "
            '("no ToolRegistry/tool_wiring.py/build_tool_invoker exists anywhere in this v2 '
            'tree"). Tracked as an open docs/review-queue.md row; deliberately not fixed here.'
        ),
    ),
    # Reparo F4/VER-ADR-BATCH (2026-09-06, terceiro agente): the 6 live occurrences (5 distinct
    # paths) the verifier measured once bare line citations gained a file-existence check — see the
    # "Reparo F4" note above `is_ellipsis_artifact_path`. Not fixed here (F4 is explicit: "do NOT
    # edit those ADRs"); each is also listed in one `docs/review-queue.md` ABERTO row
    # (`ADR-PHANTOM-PATH` family) for a future, separate repair task.
    DisclosedRot(
        doc="0024-durable-idempotency-resume-inbound-drivers.md",
        citation_repr="runtime/inbound_driver.py",
        reason=(
            "the `InboundDriver`/`ResumeDriver` classes and the `IdempotencyGuard` protocol this "
            "ADR describes were NEVER BUILT in this v2 tree — confirmed independently by "
            "`src/maezo/platform/driver_idempotency.py`'s own module docstring: \"`driver_idempotency` "
            "was created by `migrations/versions/0003_a2a_idempotency.py:61-72` for ADR-0024's "
            "`InboundDriver`/`ResumeDriver`, which were never built — it had zero readers and zero "
            "writers\". Not a rename, not a move; the module simply does not exist. Found while "
            "building the F4 file-existence check; not investigated further, out of scope for R-089."
        ),
    ),
    DisclosedRot(
        doc="0024-durable-idempotency-resume-inbound-drivers.md",
        citation_repr="a2a_assembly.py",
        reason=(
            "cited (line 20) as the `worker_runtime` module that constructs `PostgresIdempotencyStore` "
            "for the never-built drivers above — no `a2a_assembly.py` exists anywhere in this tree "
            "(`git ls-files` confirms 0 hits), same underlying defect as `runtime/inbound_driver.py` "
            "on this doc. Found while building the F4 check; not investigated further, out of scope."
        ),
    ),
    DisclosedRot(
        doc="0024-durable-idempotency-resume-inbound-drivers.md",
        citation_repr="inbound_driver.py",
        reason=(
            "cited (line 27, inside an inline code-comment quoting `IdempotencyGuard`'s definition) "
            "under the bare basename (no `runtime/` prefix) — same never-built module as the "
            "`runtime/inbound_driver.py` entry on this doc; a distinct citation string, so a "
            "distinct entry. Found while building the F4 check; not investigated further."
        ),
    ),
    DisclosedRot(
        doc="0041-reconciliacao-adrs-0005-0006-0008-0012-0015-0024-0032.md",
        citation_repr="runtime/inbound_driver.py",
        reason=(
            "the SAME phantom path as the `0024-...` entry above, re-cited (line 68) by ADR-0041's "
            "own §1 while it SUMMARISES what ADR-0024 claims — that summary does not itself "
            "re-verify this particular sub-claim (ADR-0024's `InboundDriver`/`ResumeDriver` were "
            "never built; see `src/maezo/platform/driver_idempotency.py`'s docstring). Found while "
            "building the F4 check; not investigated further, out of scope for R-089."
        ),
    ),
    DisclosedRot(
        doc="0037-amh-compatibility-boundary-canonical-contracts.md",
        citation_repr="schemas-validate.yml",
        reason=(
            "cited (line 73) as an AMH-side CI workflow file — the surrounding prose says so "
            'explicitly ("Drift AMH-side desde o snapshot do plano ... commit AMH `8a3a061`"): '
            "this citation was NEVER meant to resolve against THIS repo's tree. A file-existence "
            "check against `git ls-files` here is a structural false positive for any cross-repo "
            "citation, not evidence the ADR is wrong. Found while building the F4 check."
        ),
    ),
    DisclosedRot(
        doc="0025-pep-policy-unification.md",
        citation_repr="test_autonomy.py",
        reason=(
            'cited (line 245) as "port v1 test_autonomy.py:79-94" — the same ADR\'s §1.4 names the '
            '"v1 donor" explicitly as a DIFFERENT repository on disk '
            "(`/Users/familia/code/Maezo-Healthcare-Plan/...`), not this v2 tree. Same cross-repo "
            "false-positive shape as the ADR-0037 entry above. Found while building the F4 check."
        ),
    ),
)


def _disclosed_reason(doc: str, citation_repr: str) -> str | None:
    for entry in _DISCLOSED_ROT:
        if entry.doc == doc and entry.citation_repr == citation_repr:
            return entry.reason
    return None


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    failures: list[str]
    disclosed: list[str]
    line_citation_count: int
    ok: bool

    def render(self) -> str:
        lines = [
            "[check-doc-symbol-citations] "
            + ("PASS" if self.ok else "FAIL")
            + f" — {len(self.failures)} unresolved, {len(self.disclosed)} disclosed, "
            f"{self.line_citation_count} bare line citations (line number informational; "
            "file existence hard-checked since reparo F4/VER-ADR-BATCH)"
        ]
        for d in self.disclosed:
            lines.append(f"  DISCLOSED (not blocking): {d}")
        for f in self.failures:
            lines.append(f"  FAIL: {f}")
        return "\n".join(lines)


def run_gate(repo_root: Path, adr_dir: Path) -> GateResult:
    tracked = tracked_files(repo_root)
    failures: list[str] = []
    disclosed: list[str] = []
    # Reparo F3/VER-ADR-BATCH (2026-09-06, terceiro agente): the allowlist-rot self-audit below
    # used to ask "did ANY disclosed message mention this doc's FILENAME?" — a substring match on
    # `entry.doc` against the rendered `disclosed` strings. Two entries on the SAME doc cover for
    # each other under that check: a bogus, fully-resolvable entry parked on a doc that already has
    # one genuine disclosure passes silently, because the doc name still shows up in `disclosed`
    # from the OTHER entry. Tracking exactly which `(doc, citation_repr)` PAIR actually fired —
    # rather than re-deriving it from rendered text — makes the self-audit ask the right question:
    # "was THIS entry the thing that got disclosed?"
    fired_pairs: set[tuple[str, str]] = set()
    line_count = 0
    gitignored_prefixes = gitignored_directory_prefixes(repo_root)

    for md_path in sorted(adr_dir.glob("*.md")):
        doc_name = md_path.name
        text = md_path.read_text(encoding="utf-8")

        for sc in extract_symbol_citations(doc_name, text):
            candidates = resolve_path_candidates(sc.path, tracked)
            citation_repr = f"{sc.path}::{sc.symbol}"
            if not candidates:
                reason = _disclosed_reason(doc_name, citation_repr)
                if reason:
                    fired_pairs.add((doc_name, citation_repr))
                msg = f"{doc_name}:{sc.doc_line}: `{citation_repr}` — file not found in tree"
                (disclosed if reason else failures).append(
                    msg + (f" [DISCLOSED: {reason}]" if reason else "")
                )
                continue
            if not resolve_symbol(repo_root, candidates, sc.symbol):
                reason = _disclosed_reason(doc_name, citation_repr)
                if reason:
                    fired_pairs.add((doc_name, citation_repr))
                msg = f"{doc_name}:{sc.doc_line}: `{citation_repr}` — symbol not found (tried {candidates})"
                (disclosed if reason else failures).append(
                    msg + (f" [DISCLOSED: {reason}]" if reason else "")
                )

        for ic in extract_import_citations(doc_name, text):
            module_path = module_to_path(ic.module)
            candidates = resolve_path_candidates(module_path, tracked)
            if not candidates:
                for name in ic.names:
                    citation_repr = f"import {ic.module}::{name}"
                    reason = _disclosed_reason(doc_name, citation_repr)
                    if reason:
                        fired_pairs.add((doc_name, citation_repr))
                    msg = (
                        f"{doc_name}:{ic.doc_line}: `from {ic.module} import {name}` — "
                        f"module does not resolve to {module_path}"
                    )
                    (disclosed if reason else failures).append(
                        msg + (f" [DISCLOSED: {reason}]" if reason else "")
                    )
                continue
            for name in ic.names:
                if resolve_symbol(repo_root, candidates, name):
                    continue
                citation_repr = f"import {ic.module}::{name}"
                reason = _disclosed_reason(doc_name, citation_repr)
                if reason:
                    fired_pairs.add((doc_name, citation_repr))
                msg = (
                    f"{doc_name}:{ic.doc_line}: `from {ic.module} import {name}` — "
                    f"name not found in {module_path}"
                )
                (disclosed if reason else failures).append(
                    msg + (f" [DISCLOSED: {reason}]" if reason else "")
                )

        line_citations = extract_line_citations(text)
        line_count += len(line_citations)

        # Reparo F4/VER-ADR-BATCH: file-existence-only hard check — see the module-level note
        # above `is_ellipsis_artifact_path`/`gitignored_directory_prefixes`. The line number
        # itself is NEVER checked here (it stays informational, per the module docstring's own
        # rationale for why shape 3 cannot be proven wrong on the line alone).
        for cited_path, doc_line in line_citations:
            if is_ellipsis_artifact_path(cited_path):
                continue
            if is_under_gitignored_prefix(cited_path, gitignored_prefixes):
                continue
            if resolve_path_candidates(cited_path, tracked):
                continue
            citation_repr = cited_path
            reason = _disclosed_reason(doc_name, citation_repr)
            if reason:
                fired_pairs.add((doc_name, citation_repr))
            msg = (
                f"{doc_name}:{doc_line}: `{cited_path}` — file not found in tree "
                "(bare line citation, file-existence only)"
            )
            (disclosed if reason else failures).append(
                msg + (f" [DISCLOSED: {reason}]" if reason else "")
            )

    # An entry that no longer reproduces is allowlist rot in the other direction — the
    # maintainer must prune it, or a fixed citation could regress silently under the same name.
    # An entry whose `doc` is not even part of THIS scan (a different `--adr-dir`, or a synthetic
    # fixture repo in tests) is simply not applicable here — skipped, not failed.
    #
    # Keyed on the (doc, citation_repr) PAIR that actually fired this run (`fired_pairs`), not on
    # a substring match of the doc name against the rendered `disclosed` messages — see the
    # `fired_pairs` comment above for why the old check let a second, bogus entry on an
    # already-disclosed doc pass silently (reparo F3/VER-ADR-BATCH).
    for entry in _DISCLOSED_ROT:
        doc_path = adr_dir / entry.doc
        if not doc_path.is_file():
            continue
        still_present = (entry.doc, entry.citation_repr) in fired_pairs
        if not still_present:
            failures.append(
                f"_DISCLOSED_ROT: `{entry.citation_repr}` in {entry.doc} now resolves (or was "
                "never found this run) — prune this entry, it is stale allowlist rot"
            )

    return GateResult(failures=failures, disclosed=disclosed, line_citation_count=line_count, ok=not failures)


# ---------------------------------------------------------------------------
# CLI wrapper (I/O boundary)
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_doc_symbol_citations",
        description=(
            "R-089: docs/adr/*.md `path::symbol` and quoted-import citations must resolve "
            "against the tree; bare `path:line` citations are counted, not gated."
        ),
    )
    parser.add_argument(
        "--adr-dir",
        default="docs/adr",
        help="Directory of ADR markdown files to scan (default: docs/adr).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
    adr_dir = repo_root / args.adr_dir

    if not adr_dir.is_dir():
        print(f"[check-doc-symbol-citations] FAIL: ADR dir not found: {adr_dir}", file=sys.stderr)
        return 1

    result = run_gate(repo_root, adr_dir)
    print(result.render())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
