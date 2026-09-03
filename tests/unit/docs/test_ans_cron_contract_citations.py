"""GAP-FAB-NOTIF item C — contract citations vs. real code (SP-OP-ANS-CRON-001 / SP-OP-ANS-SUBMIT-001
/ SP-OP-NIP-001).

The three contracts below used to cite a module and three functions that do not exist ANYWHERE in
this repo — `notifications_bridge/consumer.py`, `_competencia_from_anchor`, `_ans_cron_competencia`,
`_nip_protocolo_origem` (as a standalone function; it never existed as one), plus a citation to a
now-nonexistent handler name `make_handoff_ans_submit_handler` and a phantom test-proof file
`tests/integration/processes/test_cross_process_handoff_seam.py`. Two of those claims went further
than a stale citation: the contracts asserted competencia auto-resolution was **IMPLEMENTADA**/
**resolvido** in production, when the real implementation (`ans_cron._compute_competencia`) is
registered-but-UNREACHABLE from the deployed BPMN (GAP-ANS-1, tracked separately — see
`docs/reports/business-logic-audit-improvement-plan.md:445` — not wired/deleted by this fix; see
this WP's final report for the reasoning).

This module is a STATIC TEXT fence (mirrors `test_worker_handler_purity.py`'s AST fences in spirit —
a blunt structural check on the committed prose, not a semantic contract-language parser):

  1. The phantom names never reappear in the corrected contracts/test-spec.
  2. Every REAL symbol name the corrected prose now cites actually exists in `src/` (or, for the
     test citation, in `tests/`) — so a future rename/deletion that forgets to update these docs
     trips this fence too, not just a rename INTO a phantom name.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]
_CONTRACTS_DIR = _REPO_ROOT / "docs" / "processes" / "contracts"
_TEST_SPECS_DIR = _REPO_ROOT / "docs" / "processes" / "test-specs"
_SRC_DIR = _REPO_ROOT / "src"

# The exact phantom names the R1 verifier confirmed absent from the whole repo (part-B
# verification report §6 D-B2, D-N3; `grep -rn` reproduced independently below). None of these
# strings may appear in the four corrected documents — a corrected doc may reference them only
# as an explicit "does not exist" callout, which is why this fence checks the FIXED files as a
# whole rather than line-by-line: every remaining occurrence in these files is EXPECTED to be
# inside a "GAP-FAB-NOTIF fix" / "NAO existe" sentence pointing OUT at the absence, not a claim
# that the name is real. `test_no_bare_phantom_citation_left` below still guards that no fresh
# citation slips back in by construction (see its own docstring).
_PHANTOM_TOKENS: tuple[str, ...] = (
    "notifications_bridge/consumer.py",
    "_competencia_from_anchor",
    "_ans_cron_competencia",
    "make_handoff_ans_submit_handler",
    "tests/integration/processes/test_cross_process_handoff_seam.py",
)

_FIXED_DOCS: tuple[Path, ...] = (
    _CONTRACTS_DIR / "SP-OP-ANS-CRON-001.md",
    _CONTRACTS_DIR / "SP-OP-ANS-SUBMIT-001.md",
    _CONTRACTS_DIR / "SP-OP-NIP-001.md",
    _TEST_SPECS_DIR / "SP-OP-ANS-SUBMIT-001.md",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected fixed doc to exist: {path}"
    return path.read_text(encoding="utf-8")


def test_fixed_docs_exist() -> None:
    for path in _FIXED_DOCS:
        assert path.is_file(), path


def test_phantom_module_and_function_names_confirmed_absent_from_src() -> None:
    """Re-proves the R1 verifier's own grep (part-B verification report §6 D-B2): none of these
    names exist anywhere under `src/`. If one ever gets added back (e.g. a future PR actually
    builds `notifications_bridge/consumer.py`), this test is the signal to REVERT the "does not
    exist" callouts these docs now carry — it does not itself forbid building the module, but it
    keeps the two states (docs vs. code) from silently diverging again."""
    hits: dict[str, list[str]] = {}
    for token in _PHANTOM_TOKENS:
        # A literal substring scan mirrors the verifier's own `grep -rn` methodology exactly.
        matches = [
            str(p.relative_to(_REPO_ROOT))
            for p in _SRC_DIR.rglob("*.py")
            if token in p.read_text(encoding="utf-8", errors="ignore")
        ]
        if matches:
            hits[token] = matches
    assert not hits, f"phantom name(s) now exist in src/ — the docs' 'does not exist' text is stale: {hits}"


def test_nip_protocolo_origem_not_a_standalone_function_anywhere() -> None:
    """`_nip_protocolo_origem` never existed as a `def` anywhere in the repo — only ever cited as
    one. Distinct from the tuple-scanned tokens above because the bare substring
    `nip_protocolo_origem` (no leading underscore) IS legitimately a real process/dict variable
    name (`notification_bridge.py:298`) and must keep appearing; only the FUNCTION shape is
    phantom."""
    offenders = [
        str(p.relative_to(_REPO_ROOT))
        for p in _SRC_DIR.rglob("*.py")
        if re.search(r"\bdef\s+_nip_protocolo_origem\b", p.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert not offenders, f"_nip_protocolo_origem now exists as a function — update the docs: {offenders}"


def test_corrected_docs_no_longer_cite_the_phantom_names() -> None:
    """The four corrected documents no longer claim any of the phantom names are real
    implementations. (They MAY still mention a phantom token inside a "does not exist" sentence —
    that is the point of the fix — so this asserts each occurrence, if present at all, has one of
    the disclosure markers this PR's edits consistently use within a small surrounding window —
    prose wraps across markdown lines, so a strict same-line check is too brittle.)"""
    disclosure_markers_lower = ("nao existe", "gap-fab-notif")
    window = 2
    for path in _FIXED_DOCS:
        lines = _read(path).splitlines()
        for token in _PHANTOM_TOKENS:
            for i, line in enumerate(lines):
                if token not in line:
                    continue
                context = lines[max(0, i - window) : i + window + 1]
                assert any(
                    marker in ctx_line.lower() for ctx_line in context for marker in disclosure_markers_lower
                ), (
                    f"{path.name}: line cites phantom token {token!r} WITHOUT a disclosure marker "
                    f"nearby — looks like a bare (fabricated-success) citation again: {line!r}"
                )


def test_corrected_docs_cite_the_real_competencia_function_and_it_exists() -> None:
    """The real (if unreachable) competencia computation is `ans_cron._compute_competencia` — the
    corrected ANS-CRON/ANS-SUBMIT contracts and the ANS-SUBMIT test-spec must name it, and it must
    actually exist at the cited path."""
    ans_cron_py = _SRC_DIR / "maezo" / "tools" / "workers" / "ans_cron.py"
    assert ans_cron_py.is_file()
    assert re.search(r"^def _compute_competencia\(", ans_cron_py.read_text(encoding="utf-8"), re.MULTILINE), (
        "ans_cron._compute_competencia no longer exists — the corrected contracts cite it as the "
        "real (dead) implementation; update both if this function is ever removed/renamed."
    )
    for path in (
        _CONTRACTS_DIR / "SP-OP-ANS-CRON-001.md",
        _CONTRACTS_DIR / "SP-OP-ANS-SUBMIT-001.md",
        _TEST_SPECS_DIR / "SP-OP-ANS-SUBMIT-001.md",
    ):
        assert "_compute_competencia" in _read(path), f"{path.name} should cite the real function name"


def test_corrected_docs_cite_the_real_nip_handoff_mapping_and_it_exists() -> None:
    """The real nip_protocolo_origem mapping lives in `notification_bridge.
    _ans_submit_variables_from_nip_handoff` — the corrected NIP/ANS-SUBMIT contracts must name it,
    and it must actually exist."""
    bridge_py = _SRC_DIR / "maezo" / "platform" / "notification_bridge.py"
    assert bridge_py.is_file()
    bridge_src = bridge_py.read_text(encoding="utf-8")
    assert re.search(r"^def _ans_submit_variables_from_nip_handoff\(", bridge_src, re.MULTILINE)
    for path in (
        _CONTRACTS_DIR / "SP-OP-NIP-001.md",
        _CONTRACTS_DIR / "SP-OP-ANS-SUBMIT-001.md",
    ):
        assert "_ans_submit_variables_from_nip_handoff" in _read(path), (
            f"{path.name} should cite the real mapping function name"
        )


def test_corrected_docs_cite_a_real_test_as_proof_not_the_phantom_one() -> None:
    """The corrected NIP/ANS-SUBMIT contracts must cite a test that actually exists
    (`test_notification_bridge.py::test_nip_handoff_triggers_ans_submit`), not the phantom
    `test_cross_process_handoff_seam.py`."""
    real_test_path = _REPO_ROOT / "tests" / "unit" / "platform" / "test_notification_bridge.py"
    assert real_test_path.is_file()
    assert re.search(
        r"^def test_nip_handoff_triggers_ans_submit\(",
        real_test_path.read_text(encoding="utf-8"),
        re.MULTILINE,
    ), "the cited proof test no longer exists under that name — update the contracts"
    for path in (_CONTRACTS_DIR / "SP-OP-NIP-001.md", _CONTRACTS_DIR / "SP-OP-ANS-SUBMIT-001.md"):
        assert "test_nip_handoff_triggers_ans_submit" in _read(path)


def test_ans_cron_module_name_is_singular_notification_bridge_not_a_package() -> None:
    """A recurring form of the same phantom citation: `notifications_bridge` (plural, a package
    path) instead of the real `notification_bridge.py` (singular, a single module). The corrected
    docs must use the singular, real module path at least once each."""
    real_module = _SRC_DIR / "maezo" / "platform" / "notification_bridge.py"
    assert real_module.is_file()
    for path in (
        _CONTRACTS_DIR / "SP-OP-ANS-CRON-001.md",
        _CONTRACTS_DIR / "SP-OP-ANS-SUBMIT-001.md",
        _CONTRACTS_DIR / "SP-OP-NIP-001.md",
    ):
        assert "src/maezo/platform/notification_bridge.py" in _read(path), (
            f"{path.name} should cite the real singular module path at least once"
        )
