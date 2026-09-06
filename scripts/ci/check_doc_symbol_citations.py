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

Two citation shapes are HARD-CHECKED (fail the build on an unresolved one, unless explicitly
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

A third shape is COLLECTED but never hard-failed, per design (a bare `path:line`/`path:line-line`
citation cannot be proven wrong without knowing what the line USED to say — only that the FILE
still exists, which line-drift alone does not violate):

  3. **Bare line citations** — `path/to/file.py:116` or `:116-120`. Reported as a rot-prone count
     (informational only). A line citation immediately preceded by an ``<!-- anchor: ... -->``-style
     HTML comment naming the same file is treated as a deliberately pinned, stable anchor and is
     excluded from the rot-prone count (none exist in the corpus today; the mechanism is here for
     the day one is added deliberately).

**Disclosed rot** (`_DISCLOSED_ROT`): a small, dated, named allowlist of citations already known to
be unresolved when this gate was built — never a substitute for fixing forward, and never silent.
Each entry is actively re-checked: if the citation now RESOLVES, the entry is stale and the gate
fails, telling the maintainer to delete it (an allowlist that nobody prunes is the same disclosure
failure this gate exists to catch). One entry ships with this gate today:

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
    # left here. `docs/adr/0028-dmn-evaluation-engine-side.md`'s sibling citation (`ans_cron.py:116`,
    # re-anchored to `...bpmn::BRT_Calendario`) was never in this tuple to begin with — it was always
    # a bare `path:line` citation, which this gate does not hard-check (see the module docstring and
    # F4/VER-ADR-BATCH) — so there was nothing to remove for it.
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
            f"{self.line_citation_count} bare line citations (informational, not gated)"
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
    line_count = 0

    for md_path in sorted(adr_dir.glob("*.md")):
        doc_name = md_path.name
        text = md_path.read_text(encoding="utf-8")

        for sc in extract_symbol_citations(doc_name, text):
            candidates = resolve_path_candidates(sc.path, tracked)
            citation_repr = f"{sc.path}::{sc.symbol}"
            if not candidates:
                reason = _disclosed_reason(doc_name, citation_repr)
                msg = f"{doc_name}:{sc.doc_line}: `{citation_repr}` — file not found in tree"
                (disclosed if reason else failures).append(
                    msg + (f" [DISCLOSED: {reason}]" if reason else "")
                )
                continue
            if not resolve_symbol(repo_root, candidates, sc.symbol):
                reason = _disclosed_reason(doc_name, citation_repr)
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
                msg = (
                    f"{doc_name}:{ic.doc_line}: `from {ic.module} import {name}` — "
                    f"name not found in {module_path}"
                )
                (disclosed if reason else failures).append(
                    msg + (f" [DISCLOSED: {reason}]" if reason else "")
                )

        line_count += len(extract_line_citations(text))

    # An entry that no longer reproduces is allowlist rot in the other direction — the
    # maintainer must prune it, or a fixed citation could regress silently under the same name.
    # An entry whose `doc` is not even part of THIS scan (a different `--adr-dir`, or a synthetic
    # fixture repo in tests) is simply not applicable here — skipped, not failed.
    for entry in _DISCLOSED_ROT:
        doc_path = adr_dir / entry.doc
        if not doc_path.is_file():
            continue
        still_present = any(entry.doc in item for item in disclosed)
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
