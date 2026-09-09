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
disclosed — see `_DISCLOSED_ROT` below). Recovery qualifier 2026-09-09: this static gate verifies
only the bindings described here. It does not reexecute ADR-0041's historical claims, certify
inbound delivery, or grant portal/runtime closure credit.

  1. **Symbol citations** — `path/to/file.py::Symbol` or `path/to/file.py::Class.method`, the
     convention already used by ~40 existing citations across `docs/adr/*.md` (e.g. ADR-0029,
     ADR-0042, ADR-0044). Paths beginning with a canonical repository root (`src/`, `spec/`,
     `tests/`, `config/`, `deploy/`, `docs/`, `scripts/` or `.github/`) bind exactly. Other
     slash-bearing paths are explicit package/tree abbreviations and bind by their supplied suffix;
     slash-free names explicitly bind by basename. A rooted miss never falls back to an archived,
     vendored or otherwise prefixed copy. Python names are collected statically from their real
     lexical namespace: module bindings and qualified class members, never function locals. BPMN
     and DMN symbols bind only to parsed XML `id` declarations, never comments or references.
  2. **Quoted Python import statements** — a backtick- or code-fence-quoted
     ``from dotted.module import name1, name2`` line (the ADR-0026 case: an inline code example,
     not a `::` citation, but the same underlying claim — "these names exist in this module"). The
     dotted module binds exactly via the `maezo.` package convention (`maezo.x.y` ->
     `src/maezo/x/y.py` or `src/maezo/x/y/__init__.py`); FAILS if the module does not resolve, or if
     any imported name is not a module binding. If both a same-name module file and package exist,
     the package wins, matching Python's import selection. The quoted statement is parsed with
     `ast`, including parentheses, multiline lists and aliases. CommonMark root fences using at
     least three matching backticks or tildes and up to three leading spaces are supported, as are
     arbitrary-length backtick code spans. Unsupported/unparseable cited forms fail explicitly.
  3. **Bare line citations, file existence only** (reparo F4/VER-ADR-BATCH, 2026-09-06) —
     `path/to/file.py:116` or `:116-120`. A bare line citation cannot be proven wrong without
     knowing what the line USED to say — only that the FILE still exists, which line-drift alone
     does not violate — so the LINE NUMBER stays purely informational (see the `line_citation_count`
     in the render output: 754 counted, none individually vetted). But file existence IS cheap and
     reliable, and was left unchecked entirely until this reparo measured 6 live citations (5
     distinct paths) across `docs/adr/*.md` naming a file absent from the tree — one of them inside
     ADR-0041, whose purpose is reconciling exactly this species of claim. FAILS only if the path
     resolves to nothing (via the explicit path forms above), UNLESS the path either (a) contains a
     literal `...` (an elided placeholder in prose) or (b) is a concrete missing path under `docs/`
     that Git itself reports ignored (e.g. `docs/prompts/`). Excluded abbreviations and ignored-local
     paths have separate counts and reasons; neither is presented as checked. Tracked files are
     checked before ignore handling, and ignored source/test/config paths receive no exemption.
     HTML line-anchor comments may stabilize line bookkeeping but never suppress file existence.

**Disclosed rot** (`_DISCLOSED_ROT`): a small, dated, named allowlist of citations already known to
be unresolved when this gate was built — never a substitute for fixing forward, and never silent.
Each entry is actively re-checked: if the citation now RESOLVES, the entry is stale and the gate
fails, telling the maintainer to delete it. Seven entries ship with this gate today: **one** missing
tool-wiring symbol, **four** inbound-driver path occurrences across ADR-0024/0041, and **two**
explicitly cross-repository paths in ADR-0037/0025. See `_DISCLOSED_ROT` for per-entry evidence.

  - `docs/adr/0022-mcp-in-process-boot.md` :: `src/maezo/runtime/tool_wiring.py::build_tool_invoker`
    — genuine PRE-EXISTING rot found while building this gate, unrelated to R-089's own scope
    (`runtime/tool_wiring.py::build_tool_invoker` does not resolve). Tracked as an open row in
    `docs/review-queue.md`; deliberately NOT fixed here (out of scope for R-089). This exact target
    is the disclosure; the checker makes no broader claim about the current ToolRegistry.

  (Historical: this gate originally shipped a SECOND entry for
  `docs/adr/0026-worker-standardization.md`'s `check_calendar` import citation — its own
  non-vacuity case. Reparo F2/VER-ADR-BATCH (2026-09-06, terceiro agente) FIXED that citation
  forward, per the owner's decision for R-089, instead of leaving it allowlisted; the entry was
  removed once the citation resolved, exactly as the self-audit above demands of any entry.)

Wired into `make check-doc-symbol-citations` and `.github/workflows/ci.yml`'s `validate-artifacts`
job. Tree binding uses `git ls-files`; the narrow local-doc exclusion asks Git's `check-ignore`
without history, so the gate runs unchanged in a shallow clone. An empty document corpus or a scan
with zero eligible citations is `EMPTY`/`UNCHECKED`, never PASS.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

# ---------------------------------------------------------------------------
# Citation extraction
# ---------------------------------------------------------------------------

#: `path.py::Symbol` / `path.py::Class.method` (also tolerates non-.py extensions, though none are
#: used in the corpus today — kept general per the module docstring's shape 1).
_SYMBOL_CITATION_RE = re.compile(
    r"(?P<path>[A-Za-z0-9_./-]+\.(?:py|bpmn|yaml|yml|dmn))::(?P<symbol>[A-Za-z0-9_.]+)"
)

#: Import examples are considered only inside Markdown inline-code spans or fenced code blocks.
#: Once such an example starts with ``from maezo... import``, it must either parse as one supported
#: absolute ``ast.ImportFrom`` statement or be reported explicitly.  This start pattern deliberately
#: does not try to parse Python; ``ast.parse`` owns that job below.
_IMPORT_START_RE = re.compile(r"\bfrom\s+maezo(?:\.[A-Za-z_][A-Za-z0-9_]*)+\s+import\b")

_FENCE_OPEN_RE = re.compile(r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})(?P<info>[^\r\n]*)$")

_INDENTED_IMPORT_RE = re.compile(
    r"(?m)^(?: {4,}|\t)(?P<statement>from\s+maezo(?:\.[A-Za-z_][A-Za-z0-9_]*)+\s+import\b[^\r\n]*)"
)

_CONTAINER_TILDE_FENCE_RE = re.compile(
    r"^(?P<prefix> {0,3}(?:> ?|(?:[-+*]|\d+[.)]) +))(?P<fence>~{3,})(?P<info>[^\r\n]*)$"
)

#: Bare `path.py:NNN` / `path.py:NNN-MMM` — NOT immediately preceded by another `:` (which would
#: make it the second half of a `::Symbol` citation instead).
_LINE_CITATION_RE = re.compile(
    r"(?<!:)\b(?P<path>[A-Za-z0-9_./-]+\.(?:py|md|yaml|yml|bpmn|dmn)):(?P<line>\d+)(?:-\d+)?\b"
)


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


@dataclass(frozen=True)
class ImportIssue:
    doc: str
    doc_line: int
    statement: str
    reason: str


@dataclass(frozen=True)
class CitationExclusion:
    doc: str
    doc_line: int
    citation_repr: str
    reason: str


@dataclass(frozen=True)
class _CodeRegion:
    text: str
    offset: int


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def extract_symbol_citations(doc_name: str, text: str) -> list[SymbolCitation]:
    return [
        SymbolCitation(doc_name, _line_number(text, m.start()), m.group("path"), m.group("symbol"))
        for m in _SYMBOL_CITATION_RE.finditer(text)
    ]


def _quoted_code_regions(text: str) -> list[_CodeRegion]:
    """Return supported CommonMark code regions without interpreting prose as Python.

    Root fenced blocks accept either delimiter, any delimiter length of at least three and zero to
    three leading spaces. A closing fence must use the same character and at least the opener's
    length. An unclosed fence extends to EOF, as CommonMark specifies. Fenced regions are masked
    before arbitrary-length backtick code spans are collected, preventing delimiters or examples
    inside a block from being interpreted twice.

    Container-nested fences (for example inside a block quote or list) are deliberately outside
    this finite checker grammar. Such an example must be rewritten as a root fence or inline code;
    the checker does not claim to parse general Markdown container structure.
    """
    regions: list[_CodeRegion] = []
    fenced_spans: list[tuple[int, int]] = []
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    index = 0
    while index < len(lines):
        line_without_ending = lines[index].rstrip("\r\n")
        opening = _FENCE_OPEN_RE.fullmatch(line_without_ending)
        if opening is None or (opening.group("fence").startswith("`") and "`" in opening.group("info")):
            index += 1
            continue

        fence = opening.group("fence")
        fence_char = fence[0]
        minimum_close_length = len(fence)
        code_start = offsets[index] + len(lines[index])
        close_index: int | None = None
        for candidate_index in range(index + 1, len(lines)):
            candidate = lines[candidate_index].rstrip("\r\n")
            close = re.fullmatch(r" {0,3}(?P<fence>`{3,}|~{3,})[ \t]*", candidate)
            if close is None:
                continue
            closing_fence = close.group("fence")
            if closing_fence[0] == fence_char and len(closing_fence) >= minimum_close_length:
                close_index = candidate_index
                break

        if close_index is None:
            regions.append(_CodeRegion(text[code_start:], code_start))
            fenced_spans.append((offsets[index], len(text)))
            break

        code_end = offsets[close_index]
        regions.append(_CodeRegion(text[code_start:code_end], code_start))
        fence_end = offsets[close_index] + len(lines[close_index])
        fenced_spans.append((offsets[index], fence_end))
        index = close_index + 1

    masked = list(text)
    for start, end in fenced_spans:
        masked[start:end] = " " * (end - start)
    masked_text = "".join(masked)

    # CommonMark code spans use a matching backtick run of any length. Runs of a different length
    # may occur inside the span and do not close it.
    cursor = 0
    while cursor < len(masked_text):
        opening = re.search(r"`+", masked_text[cursor:])
        if opening is None:
            break
        opening_start = cursor + opening.start()
        opening_end = cursor + opening.end()
        tick_count = opening_end - opening_start
        closing = re.compile(rf"(?<!`)`{{{tick_count}}}(?!`)").search(masked_text, opening_end)
        if closing is None:
            cursor = opening_end
            continue
        regions.append(_CodeRegion(text[opening_end : closing.start()], opening_end))
        cursor = closing.end()
    return sorted(regions, key=lambda region: region.offset)


def _unsupported_markdown_import_issues(doc_name: str, text: str) -> list[ImportIssue]:
    """Refuse import examples in Markdown code forms outside the finite supported grammar.

    Four-space/tab indented code blocks and container-nested tilde fences require contextual
    CommonMark container parsing, which this CI gate intentionally does not approximate. Detecting
    a Maezo import in either form is therefore a blocking, actionable error instead of an omission.
    Backtick container forms are still found by the matching-run code-span collector.
    """
    issues: list[ImportIssue] = []
    seen_offsets: set[int] = set()
    supported_regions = _quoted_code_regions(text)

    def in_supported_region(offset: int) -> bool:
        return any(region.offset <= offset < region.offset + len(region.text) for region in supported_regions)

    for match in _INDENTED_IMPORT_RE.finditer(text):
        offset = match.start("statement")
        if in_supported_region(offset):
            continue
        seen_offsets.add(offset)
        issues.append(
            ImportIssue(
                doc=doc_name,
                doc_line=_line_number(text, offset),
                statement=" ".join(match.group("statement").split()),
                reason=(
                    "unsupported Markdown indented-code import; use inline code or a root fence "
                    "with at most three leading spaces"
                ),
            )
        )

    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    for index, line in enumerate(lines):
        opening = _CONTAINER_TILDE_FENCE_RE.fullmatch(line.rstrip("\r\n"))
        if opening is None:
            continue
        if in_supported_region(offsets[index]):
            continue
        fence_length = len(opening.group("fence"))
        end_index = len(lines)
        for candidate_index in range(index + 1, len(lines)):
            candidate = lines[candidate_index].rstrip("\r\n")
            close = re.fullmatch(
                rf".*?(?P<fence>~{{{fence_length},}})[ \t]*",
                candidate,
            )
            if close is not None:
                end_index = candidate_index
                break
        body_start = offsets[index] + len(lines[index])
        body_end = offsets[end_index] if end_index < len(lines) else len(text)
        body = text[body_start:body_end]
        for import_match in _IMPORT_START_RE.finditer(body):
            absolute_offset = body_start + import_match.start()
            if absolute_offset in seen_offsets:
                continue
            seen_offsets.add(absolute_offset)
            statement_end = text.find("\n", absolute_offset)
            if statement_end < 0:
                statement_end = len(text)
            issues.append(
                ImportIssue(
                    doc=doc_name,
                    doc_line=_line_number(text, absolute_offset),
                    statement=" ".join(text[absolute_offset:statement_end].split()),
                    reason=(
                        "unsupported Markdown container-nested tilde fence; use inline code or a "
                        "root fence with at most three leading spaces"
                    ),
                )
            )
    return issues


def _import_statement_end(code: str, start_match: re.Match[str]) -> int:
    """Return the end offset for one quoted import candidate.

    An unparenthesized import ends at its physical line. A parenthesized import ends at its matching
    close parenthesis; if no close exists, the entire remaining code region is returned so the AST
    parser can report the malformed citation instead of silently dropping it.
    """
    cursor = start_match.end()
    while cursor < len(code) and code[cursor].isspace() and code[cursor] != "\n":
        cursor += 1
    if cursor >= len(code) or code[cursor] != "(":
        newline = code.find("\n", cursor)
        return len(code) if newline < 0 else newline

    depth = 0
    for index in range(cursor, len(code)):
        char = code[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
    return len(code)


def _is_abbreviated_ellipsis_import(statement: str) -> bool:
    """The one supported exclusion: an import list explicitly abbreviated with literal ``...``."""
    if "(" not in statement or "..." not in statement:
        return False
    body = statement[statement.find("(") + 1 : statement.rfind(")") if ")" in statement else None]
    return any(part.strip() == "..." for part in body.split(","))


def inspect_import_citations(
    doc_name: str, text: str
) -> tuple[list[ImportCitation], list[ImportIssue], list[CitationExclusion]]:
    """Parse supported quoted ``from maezo... import ...`` examples with Python's AST.

    Supported examples are absolute ``ImportFrom`` statements, including aliases and parenthesized
    or multiline name lists. Star imports, multiple statements in one code span, syntax errors and
    nested constructs are explicit failures. Literal ``...`` entries are a narrowly reported
    abbreviation exclusion used by ADR-0026's prose summary.
    """
    citations: list[ImportCitation] = []
    issues = _unsupported_markdown_import_issues(doc_name, text)
    exclusions: list[CitationExclusion] = []

    for region in _quoted_code_regions(text):
        cursor = 0
        while match := _IMPORT_START_RE.search(region.text, cursor):
            end = _import_statement_end(region.text, match)
            statement = region.text[match.start() : end].strip()
            absolute_offset = region.offset + match.start()
            doc_line = _line_number(text, absolute_offset)
            cursor = max(end, match.end())

            if _is_abbreviated_ellipsis_import(statement):
                exclusions.append(
                    CitationExclusion(
                        doc=doc_name,
                        doc_line=doc_line,
                        citation_repr=" ".join(statement.split()),
                        reason="abbreviated import list contains literal `...`",
                    )
                )
                continue

            try:
                tree = ast.parse(statement)
            except SyntaxError as exc:
                issues.append(
                    ImportIssue(
                        doc=doc_name,
                        doc_line=doc_line,
                        statement=" ".join(statement.split()),
                        reason=f"unsupported or unparseable ImportFrom syntax: {exc.msg}",
                    )
                )
                continue

            if len(tree.body) != 1 or not isinstance(tree.body[0], ast.ImportFrom):
                issues.append(
                    ImportIssue(
                        doc=doc_name,
                        doc_line=doc_line,
                        statement=" ".join(statement.split()),
                        reason="unsupported quoted import form (expected one ImportFrom statement)",
                    )
                )
                continue

            node = tree.body[0]
            if node.level != 0 or node.module is None or not node.module.startswith("maezo."):
                issues.append(
                    ImportIssue(
                        doc=doc_name,
                        doc_line=doc_line,
                        statement=" ".join(statement.split()),
                        reason="unsupported import module (expected absolute maezo.* module)",
                    )
                )
                continue
            if any(alias.name == "*" for alias in node.names):
                issues.append(
                    ImportIssue(
                        doc=doc_name,
                        doc_line=doc_line,
                        statement=" ".join(statement.split()),
                        reason="unsupported star import citation",
                    )
                )
                continue

            # A citation asserts that the source names exist. ``as alias`` changes the importing
            # document's local binding, not the names that must be exported by the cited module.
            names = tuple(alias.name for alias in node.names)
            citations.append(ImportCitation(doc_name, doc_line, node.module, names))

    return citations, issues, exclusions


def extract_import_citations(doc_name: str, text: str) -> list[ImportCitation]:
    """Compatibility view used by inventory tooling; ``run_gate`` also consumes issues/exclusions."""
    citations, _issues, _exclusions = inspect_import_citations(doc_name, text)
    return citations


def extract_line_citations(text: str) -> list[tuple[str, int]]:
    """``(path, doc_line)`` for every bare line citation, including anchored citations.

    An HTML anchor may stabilize line-position bookkeeping, but it never proves that the named file
    exists. Shape 3 always retains its path validation.
    """
    return [
        (match.group("path"), _line_number(text, match.start())) for match in _LINE_CITATION_RE.finditer(text)
    ]


# ---------------------------------------------------------------------------
# Reparo F4/VER-ADR-BATCH (2026-09-06, terceiro agente): file-existence for bare line citations
# ---------------------------------------------------------------------------
#
# Shape 3 (bare `path:line` citations) used to be COLLECTED but never hard-checked at all, on the
# stated grounds that "a bare `path:line` citation cannot be proven wrong without knowing what the
# line USED to say — only that the FILE still exists, which line-drift alone does not violate."
# That distinction was correct but the gate never acted on the half of it that IS cheap and
# reliable: file existence. Measured over the real corpus at the time of this reparo, 6 of the 754
# bare line citations named a path absent from the tree entirely (not merely line-drifted) — one
# of them (`runtime/inbound_driver.py`) inside ADR-0041, the very ADR whose purpose is reconciling
# this species of phantom claim. This section hard-checks file existence only; the line number
# itself stays informational (see `test_bare_line_citation_with_a_drifted_line_number_still_passes`).
#
# Two RULES (not allowlist entries) classify paths that a file-existence check would otherwise flag
# reasons that have nothing to do with citation rot:
#
#   - **Ellipsis artefacts.** A cited "path" containing a literal `...` (e.g. `docs/adr/0018-...md`
#     inside ADR-0040's own two-column summary table) is prose shorthand for an elided run of
#     similar filenames, not a real path — no tracked filename in this repo has ever contained
#     three consecutive dots (`git ls-files | grep -c '\.\.\.'` is 0). Structural, not per-citation.
#   - **Intentionally local docs.** A concrete missing path under `docs/` that Git itself reports
#     ignored (e.g. `docs/prompts/`) is reported separately as ignored-local. The exemption never
#     applies to source/test/config paths, and an existing tracked file is checked first.
_ELLIPSIS_ARTIFACT_MARKER = "..."


def is_ellipsis_artifact_path(path: str) -> bool:
    """See the module-level note above shape 3's file-existence check."""
    return _ELLIPSIS_ARTIFACT_MARKER in path


def is_intentionally_local_ignored_path(repo_root: Path, path: str, tracked: list[str]) -> bool:
    """Return whether a missing citation names a deliberately local-only documentation path.

    Existing tracked files are never excluded, even if a broad ignore pattern also matches them.
    The exemption is confined to ``docs/`` because ignored source/test/config paths are precisely
    the deletion case this file-existence gate must catch. Git itself decides whether the concrete
    path is ignored; simple textual prefix matching is not used as Git-ignore semantics.
    """
    if path in tracked or not path.startswith("docs/"):
        return False
    proc = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", "--", path],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Tree resolution
# ---------------------------------------------------------------------------

# These names are stable repository-root identities in this project. A citation that supplies one
# cannot be satisfied by a suffix copy under ``archive/``, ``vendor/`` or another prefix. Other
# slash-bearing forms remain the documented finite abbreviation grammar for package/tree paths
# such as ``gateway/pep.py`` and ``tools/workers/ans_cron.py``.
_EXACT_REPOSITORY_PATH_ROOTS = frozenset(
    {".github", "config", "deploy", "docs", "scripts", "spec", "src", "tests"}
)


def tracked_files(repo_root: Path) -> list[str]:
    """`git ls-files` — works unchanged in a shallow clone (no history needed)."""
    proc = subprocess.run(["git", "ls-files"], cwd=repo_root, capture_output=True, text=True, check=True)
    return proc.stdout.splitlines()


def resolve_path_candidates(cited: str, tracked: list[str]) -> list[str]:
    """Resolve one of three explicit path forms without discarding a supplied namespace.

    * a path beginning with a canonical repository root is always exact;
    * another path with a slash may be an abbreviated suffix (``gateway/pep.py``);
    * a string with no slash is explicitly a basename citation (``pep.py``).

    A qualified miss such as ``src/maezo/wrong/widget.py`` never falls back to a different
    ``widget.py``. Returning every legitimate abbreviated/basename candidate lets the symbol
    resolver check colliding basenames without inventing a match for a wrong directory.
    """
    if cited in tracked:
        return [cited]
    if cited.split("/", 1)[0] in _EXACT_REPOSITORY_PATH_ROOTS:
        return []
    if "/" in cited:
        return [tracked_path for tracked_path in tracked if tracked_path.endswith("/" + cited)]
    return [tracked_path for tracked_path in tracked if Path(tracked_path).name == cited]


def module_to_path(module: str) -> str:
    """`maezo.tools.workers.ans_cron` -> `src/maezo/tools/workers/ans_cron.py` (this repo's one
    package root, `src/`)."""
    return "src/" + module.replace(".", "/") + ".py"


def module_path_candidates(module: str, tracked: list[str]) -> list[str]:
    """Bind an absolute module to Python's selected file, with package precedence."""
    base = "src/" + module.replace(".", "/")
    package = base + "/__init__.py"
    module_file = base + ".py"
    if package in tracked:
        return [package]
    return [module_file] if module_file in tracked else []


def _target_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {name for element in target.elts for name in _target_names(element)}
    return set()


def _import_bound_names(node: ast.Import | ast.ImportFrom) -> set[str]:
    if isinstance(node, ast.ImportFrom):
        return {alias.asname or alias.name for alias in node.names if alias.name != "*"}
    return {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}


def _collect_lexical_bindings(statements: list[ast.stmt], *, prefix: str = "") -> set[str]:
    """Collect static bindings in one module/class lexical scope, never function-local names."""
    symbols: set[str] = set()

    def qualify(name: str) -> str:
        return f"{prefix}.{name}" if prefix else name

    for statement in statements:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.add(qualify(statement.name))
            continue
        if isinstance(statement, ast.ClassDef):
            class_name = qualify(statement.name)
            symbols.add(class_name)
            symbols.update(_collect_lexical_bindings(statement.body, prefix=class_name))
            continue
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                symbols.update(qualify(name) for name in _target_names(target))
            continue
        if isinstance(statement, ast.AnnAssign):
            symbols.update(qualify(name) for name in _target_names(statement.target))
            continue
        if isinstance(statement, ast.AugAssign):
            symbols.update(qualify(name) for name in _target_names(statement.target))
            continue
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            symbols.update(qualify(name) for name in _import_bound_names(statement))
            continue

        # Control-flow statements execute in their containing module/class namespace. Recurse into
        # those statement lists, while function bodies above remain intentionally opaque.
        nested_lists: list[list[ast.stmt]] = []
        for field in ("body", "orelse", "finalbody"):
            value = getattr(statement, field, None)
            if isinstance(value, list) and all(isinstance(item, ast.stmt) for item in value):
                nested_lists.append(value)
        handlers = getattr(statement, "handlers", None)
        if isinstance(handlers, list):
            nested_lists.extend(
                handler.body for handler in handlers if isinstance(handler, ast.ExceptHandler)
            )
        cases = getattr(statement, "cases", None)
        if isinstance(cases, list):
            nested_lists.extend(case.body for case in cases if isinstance(case, ast.match_case))
        for nested in nested_lists:
            symbols.update(_collect_lexical_bindings(nested, prefix=prefix))

    return symbols


def collect_python_symbols(source: str) -> set[str] | None:
    """`None` means "could not parse" (syntax error) — caller treats that as unresolved, not as an
    empty-but-valid symbol set, so a citation into an unparseable file fails loudly rather than
    silently."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    return _collect_lexical_bindings(tree.body)


def collect_declared_xml_ids(source: str) -> set[str] | None:
    """Return actual XML ``id`` declarations; comments and reference attributes do not count."""
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError:
        return None
    return {element.attrib["id"] for element in root.iter() if "id" in element.attrib}


def _candidate_symbol_status(candidate: str, content: str, symbol: str) -> tuple[bool, str | None]:
    suffix = Path(candidate).suffix
    if suffix == ".py":
        symbols = collect_python_symbols(content)
        if symbols is None:
            return False, f"{candidate} is malformed Python"
        return symbol in symbols, None
    if suffix in {".bpmn", ".dmn"}:
        identifiers = collect_declared_xml_ids(content)
        if identifiers is None:
            return False, f"{candidate} is malformed XML"
        return symbol in identifiers, None
    return False, f"{candidate} has unsupported structured-symbol extension {suffix or '<none>'}"


def resolve_symbol_details(
    repo_root: Path, path_candidates: list[str], symbol: str
) -> tuple[bool, list[str]]:
    problems: list[str] = []
    for candidate in path_candidates:
        full = repo_root / candidate
        try:
            content = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{candidate} could not be read: {exc}")
            continue
        resolved, problem = _candidate_symbol_status(candidate, content, symbol)
        if resolved:
            return True, []
        if problem:
            problems.append(problem)
    return False, problems


def resolve_symbol(repo_root: Path, path_candidates: list[str], symbol: str) -> bool:
    resolved, _problems = resolve_symbol_details(repo_root, path_candidates, symbol)
    return resolved


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
            "scope (ADR-0026/0028) — the exact target "
            "`src/maezo/runtime/tool_wiring.py::build_tool_invoker` does not resolve. Tracked as "
            "an open docs/review-queue.md row; deliberately not fixed here. This disclosure does "
            "not adopt any broader historical statement about the current ToolRegistry."
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
            'writers". Not a rename, not a move; the module simply does not exist. Found while '
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

# Exact historical-path statements that the recovered ADR corpus already carried. These are kept
# out of the seven current-tree disclosures because each surrounding passage explicitly describes
# an older snapshot or quotes what an older ADR said. They remain finite, visible and self-audited;
# no generic word such as "historical" suppresses a citation.
_HISTORICAL_LINE_EXCLUSIONS: tuple[DisclosedRot, ...] = (
    DisclosedRot(
        doc="0024-durable-idempotency-resume-inbound-drivers.md",
        citation_repr="platform/webhooks/whatsapp/idempotency.py",
        reason=(
            "historical July 2026 source inventory in the accepted ADR; the cited Redis module "
            "was later removed, and this repair may not rewrite that accepted record"
        ),
    ),
    DisclosedRot(
        doc="0025-pep-policy-unification.md",
        citation_repr="src/maezo/agents/gustavo/agent.yaml",
        reason=(
            "historical accepted-ADR path from before agent definitions moved to spec/agents; "
            "this repair does not rewrite accepted ADR-0025"
        ),
    ),
    DisclosedRot(
        doc="0041-reconciliacao-adrs-0005-0006-0008-0012-0015-0024-0032.md",
        citation_repr="platform/webhooks/whatsapp/idempotency.py",
        reason=(
            "DRAFT section explicitly quotes ADR-0024 Contexto fato 3 rather than asserting a "
            "current source binding; recovery qualifier keeps historical and current claims apart"
        ),
    ),
)


def _disclosed_reason(doc: str, citation_repr: str) -> str | None:
    for entry in _DISCLOSED_ROT:
        if entry.doc == doc and entry.citation_repr == citation_repr:
            return entry.reason
    return None


def _historical_exclusion_reason(doc: str, citation_repr: str) -> str | None:
    for entry in _HISTORICAL_LINE_EXCLUSIONS:
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
    ignored: list[str]
    excluded: list[str]
    historical: list[str]
    document_count: int
    eligible_citation_count: int

    def render(self) -> str:
        lines = [
            "[check-doc-symbol-citations] "
            + ("PASS" if self.ok else "FAIL")
            + f" — {len(self.failures)} unresolved, {len(self.disclosed)} disclosed, "
            f"{len(self.ignored)} ignored-local, {len(self.excluded)} excluded-abbreviated, "
            f"{len(self.historical)} historical-only; "
            f"{self.document_count} documents, {self.eligible_citation_count} eligible citations, "
            f"{self.line_citation_count} bare line citations (line number informational; "
            "file existence always hard-checked, including anchored citations)"
        ]
        for d in self.disclosed:
            lines.append(f"  DISCLOSED (not blocking): {d}")
        for item in self.ignored:
            lines.append(f"  IGNORED LOCAL (not checked): {item}")
        for item in self.excluded:
            lines.append(f"  EXCLUDED ABBREVIATION (not checked): {item}")
        for item in self.historical:
            lines.append(f"  HISTORICAL ONLY (not current-tree checked): {item}")
        for f in self.failures:
            lines.append(f"  FAIL: {f}")
        return "\n".join(lines)


def run_gate(repo_root: Path, adr_dir: Path) -> GateResult:
    tracked = tracked_files(repo_root)
    failures: list[str] = []
    disclosed: list[str] = []
    ignored: list[str] = []
    excluded: list[str] = []
    historical: list[str] = []
    # Reparo F3/VER-ADR-BATCH (2026-09-06, terceiro agente): the allowlist-rot self-audit below
    # used to ask "did ANY disclosed message mention this doc's FILENAME?" — a substring match on
    # `entry.doc` against the rendered `disclosed` strings. Two entries on the SAME doc cover for
    # each other under that check: a bogus, fully-resolvable entry parked on a doc that already has
    # one genuine disclosure passes silently, because the doc name still shows up in `disclosed`
    # from the OTHER entry. Tracking exactly which `(doc, citation_repr)` PAIR actually fired —
    # rather than re-deriving it from rendered text — makes the self-audit ask the right question:
    # "was THIS entry the thing that got disclosed?"
    fired_pairs: set[tuple[str, str]] = set()
    fired_historical_pairs: set[tuple[str, str]] = set()
    line_count = 0
    eligible_count = 0
    documents = sorted(adr_dir.glob("*.md"))

    for md_path in documents:
        doc_name = md_path.name
        text = md_path.read_text(encoding="utf-8")

        for sc in extract_symbol_citations(doc_name, text):
            eligible_count += 1
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
            resolved, resolution_problems = resolve_symbol_details(repo_root, candidates, sc.symbol)
            if not resolved:
                reason = _disclosed_reason(doc_name, citation_repr)
                if reason:
                    fired_pairs.add((doc_name, citation_repr))
                detail = (
                    "; ".join(resolution_problems)
                    if resolution_problems
                    else f"symbol not found (tried {candidates})"
                )
                msg = f"{doc_name}:{sc.doc_line}: `{citation_repr}` — {detail}"
                (disclosed if reason else failures).append(
                    msg + (f" [DISCLOSED: {reason}]" if reason else "")
                )

        import_citations, import_issues, import_exclusions = inspect_import_citations(doc_name, text)
        for issue in import_issues:
            eligible_count += 1
            failures.append(f"{issue.doc}:{issue.doc_line}: `{issue.statement}` — {issue.reason}")
        for item in import_exclusions:
            excluded.append(f"{item.doc}:{item.doc_line}: `{item.citation_repr}` — {item.reason}")

        for ic in import_citations:
            module_path = module_to_path(ic.module)
            candidates = module_path_candidates(ic.module, tracked)
            if not candidates:
                for name in ic.names:
                    eligible_count += 1
                    citation_repr = f"import {ic.module}::{name}"
                    reason = _disclosed_reason(doc_name, citation_repr)
                    if reason:
                        fired_pairs.add((doc_name, citation_repr))
                    msg = (
                        f"{doc_name}:{ic.doc_line}: `from {ic.module} import {name}` — "
                        f"module does not resolve exactly to {module_path} or its package __init__.py"
                    )
                    (disclosed if reason else failures).append(
                        msg + (f" [DISCLOSED: {reason}]" if reason else "")
                    )
                continue
            for name in ic.names:
                eligible_count += 1
                resolved, resolution_problems = resolve_symbol_details(repo_root, candidates, name)
                if resolved:
                    continue
                citation_repr = f"import {ic.module}::{name}"
                reason = _disclosed_reason(doc_name, citation_repr)
                if reason:
                    fired_pairs.add((doc_name, citation_repr))
                detail = "; ".join(resolution_problems) if resolution_problems else "name not found"
                msg = f"{doc_name}:{ic.doc_line}: `from {ic.module} import {name}` — {detail}"
                (disclosed if reason else failures).append(
                    msg + (f" [DISCLOSED: {reason}]" if reason else "")
                )

        line_citations = extract_line_citations(text)
        line_count += len(line_citations)

        # Reparo F4/VER-ADR-BATCH: file-existence-only hard check — see the module-level note
        # above `is_ellipsis_artifact_path`/`is_intentionally_local_ignored_path`. The line number
        # itself is NEVER checked here (it stays informational, per the module docstring's own
        # rationale for why shape 3 cannot be proven wrong on the line alone).
        for cited_path, doc_line in line_citations:
            if is_ellipsis_artifact_path(cited_path):
                excluded.append(
                    f"{doc_name}:{doc_line}: `{cited_path}` — abbreviated path contains literal `...`"
                )
                continue
            candidates = resolve_path_candidates(cited_path, tracked)
            if any((repo_root / candidate).is_file() for candidate in candidates):
                eligible_count += 1
                continue
            if candidates:
                eligible_count += 1
                failures.append(
                    f"{doc_name}:{doc_line}: `{cited_path}` — tracked target missing from worktree "
                    f"(tried {candidates}; bare line citation, file-existence only)"
                )
                continue
            if is_intentionally_local_ignored_path(repo_root, cited_path, tracked):
                ignored.append(
                    f"{doc_name}:{doc_line}: `{cited_path}` — concrete missing docs path is ignored by Git"
                )
                continue
            historical_reason = _historical_exclusion_reason(doc_name, cited_path)
            if historical_reason:
                fired_historical_pairs.add((doc_name, cited_path))
                historical.append(
                    f"{doc_name}:{doc_line}: `{cited_path}` — historical path: {historical_reason}"
                )
                continue
            eligible_count += 1
            citation_repr = cited_path
            reason = _disclosed_reason(doc_name, citation_repr)
            if reason:
                fired_pairs.add((doc_name, citation_repr))
            msg = (
                f"{doc_name}:{doc_line}: `{cited_path}` — file not found in tree "
                "(bare line citation, file-existence only)"
            )
            (disclosed if reason else failures).append(msg + (f" [DISCLOSED: {reason}]" if reason else ""))

    if not documents:
        failures.append("EMPTY: ADR scan found zero Markdown documents; no PASS is possible")
    if eligible_count == 0:
        failures.append("UNCHECKED: ADR scan found zero eligible citations; no PASS is possible")

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

    for entry in _HISTORICAL_LINE_EXCLUSIONS:
        doc_path = adr_dir / entry.doc
        if not doc_path.is_file():
            continue
        if (entry.doc, entry.citation_repr) not in fired_historical_pairs:
            failures.append(
                f"_HISTORICAL_LINE_EXCLUSIONS: `{entry.citation_repr}` in {entry.doc} now resolves "
                "(or was never found this run) — prune the stale historical exclusion"
            )

    return GateResult(
        failures=failures,
        disclosed=disclosed,
        line_citation_count=line_count,
        ok=not failures,
        ignored=ignored,
        excluded=excluded,
        historical=historical,
        document_count=len(documents),
        eligible_citation_count=eligible_count,
    )


# ---------------------------------------------------------------------------
# CLI wrapper (I/O boundary)
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_doc_symbol_citations",
        description=(
            "R-089: docs/adr/*.md `path::symbol`, quoted ImportFrom, and bare `path:line` "
            "citations bind to the tracked tree; line numbers remain informational."
        ),
        epilog=(
            "Canonical rooted paths bind exactly. Quoted imports support inline backtick spans "
            "and root CommonMark fences (backtick or tilde, matching length, 0-3 leading spaces); "
            "unsupported indented/container forms containing Maezo imports fail explicitly."
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
