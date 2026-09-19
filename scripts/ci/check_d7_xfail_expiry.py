#!/usr/bin/env python3
"""CI gate: the 2 D7-catalogue expected-fails may not outlive their deadline (owner, 2026-09-12).

Purpose
-------
The owner overrode V6-Q6's advice on 2026-09-12: KEEP the D7 catalogue (`D7unit802` in
`scripts/ci/ledger_history_proofs.py::D7_CATALOG`) instead of excluding it, and declare its two
durable-red consequences as dated expected-fails instead of deleting the tests that prove them:

  - `tests/unit/dev/test_historical_async_recipe.py
    ::test_explicit_cli_preflight_reads_only_closed_d7_source` (R1) — preflights `D7unit802`,
    whose `source_commit` is train-only and never an ancestor of `main`.
  - `tests/unit/ci/test_ledger_invalid_declarations.py
    ::test_exact_test_bodies_and_ledger_history_unchanged` (R2) — asserts `main`'s ledger begins
    with the train's fully-qualified historical ledger; measured unsatisfiable on `main` by
    construction (main already diverges inside that required prefix).

This mirrors the SAME discipline `scripts/ci/check_deviation_expiry.py` and
`scripts/ci/check_lifecycle_expected_fail_expiry.py` already apply elsewhere: a deadline written
only in a `pytest.mark.xfail` reason string has no mechanism to notice it went by. This gate is
that mechanism, scoped to these two tests.

THERE IS NO SILENT RENEWAL. Once `2026-11-11` passes, nothing merges green until a human does one
of exactly two things:

  1. a main-derived D7 catalogue lands (the measurable exit criterion both xfail reasons name) and
     the two tests are restored to plain (non-xfail) tests or removed with the entry; or
  2. the owner re-ratifies a NEW, DATED deviation — a reviewed PR that updates both the xfail
     reason strings AND this gate's code-frozen `TRACKED_XFAILS` deadline together, so the two
     never drift apart. That renewal PR is itself green, because the gate reads the tree under
     test and that tree already carries the new date.

Fail-closed contract
---------------------
For every entry in the code-frozen `TRACKED_XFAILS` table below:

- The file does not exist, or no `def <test_name>` is found in it              -> FAIL.
- The function's decorators do not include a `pytest.mark.xfail(...)` call     -> FAIL. A test
  that lost its xfail marker either started passing (good — remove the tracked entry too, in the
  same PR) or started failing non-strictly (a silent downgrade this gate must not miss).
- `strict=` is missing or not literally `True`                                 -> FAIL. A
  non-strict xfail silently tolerates the test passing OR failing — exactly the "absence of a
  diagnostic read as resolution" pattern this repo's other gates exist to catch.
- `reason=` is missing, not a literal string, or does not contain the tracked deadline's ISO date
  -> FAIL. The reason and this gate's registry must name the identical date, or a reason could be
  silently edited to claim a date this gate never checked.
- `today` is PAST the tracked deadline                                        -> FAIL, naming the
  test, the deadline, and the renewal procedure verbatim.

Usage
-----
    python scripts/ci/check_d7_xfail_expiry.py                    # CI gate (system date)
    python scripts/ci/check_d7_xfail_expiry.py --today 2026-11-12  # what CI does then
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

RENEWAL_PROCEDURE: Final[str] = (
    "COMO SAIR DO VERMELHO (ato humano, uma das duas — nenhuma e' editavel por agente sem PR "
    "revisado):\n"
    "      (a) um catalogo D7 derivado da propria historia de main aterrissa (WP novo) — os dois\n"
    "          testes voltam a ser testes comuns (nao-xfail) ou sao removidos junto com a entrada\n"
    "          antiga, e esta entrada sai de TRACKED_XFAILS no mesmo PR; ou\n"
    "      (b) o dono RE-RATIFICA um prazo NOVO: atualiza a data em AMBOS os lugares no mesmo PR\n"
    "          revisado — a string `reason=` do xfail E o campo `deadline` de TRACKED_XFAILS\n"
    "          abaixo (codigo, sob revisao CODEOWNERS de scripts/ci/).\n"
    "      O PR de renovacao e' VERDE por construcao — este gate le a data da arvore em teste, e a\n"
    "      arvore dele ja carrega a data nova. Prorrogar e' possivel; prorrogar EM SILENCIO nao e'."
)


@dataclass(frozen=True, slots=True)
class TrackedXfail:
    """One D7-catalogue expected-fail this gate is responsible for. CODE-FROZEN.

    Which tests are tracked, and their deadline, is a governance fact ratified by the owner
    (2026-09-12) — it lives in code a reviewer reads here, never inferred from scanning arbitrary
    xfail markers across the tree (that would let any agent silently add a permanent expected-fail
    by copying the reason-string shape without a reviewed registry entry).
    """

    path: str
    test_name: str
    deadline: date
    what_expiry_means: str


#: THE TRACKED SET. Both entries are the owner's 2026-09-12 ratification (BRIEF-R6b-Q6).
TRACKED_XFAILS: Final[tuple[TrackedXfail, ...]] = (
    TrackedXfail(
        path="tests/unit/dev/test_historical_async_recipe.py",
        test_name="test_explicit_cli_preflight_reads_only_closed_d7_source",
        deadline=date(2026, 11, 11),
        what_expiry_means=(
            "R1 — preflighta a entrada 'D7unit802' de D7_CATALOG, cujo source_commit "
            "(80206e29ab4b...) e' exclusivo da linhagem do train e nunca sera ancestral de main."
        ),
    ),
    TrackedXfail(
        path="tests/unit/ci/test_ledger_invalid_declarations.py",
        test_name="test_exact_test_bodies_and_ledger_history_unchanged",
        deadline=date(2026, 11, 11),
        what_expiry_means=(
            "R2 — afirma que o ledger de main comeca com o ledger integralmente qualificado do "
            "train; medido insatisfazivel em main por construcao (main ja diverge dentro do "
            "prefixo obrigatorio)."
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One violation."""

    reason: str

    def render(self) -> str:
        return f"  - {self.reason}"


class MarkerError(ValueError):
    """The tracked function's xfail marker could not be located or does not have the expected shape."""


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _xfail_call(func: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.Call:
    for decorator in func.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        callee = decorator.func
        # Matches `pytest.mark.xfail(...)` (an Attribute chain ending in `.xfail`).
        if isinstance(callee, ast.Attribute) and callee.attr == "xfail":
            return decorator
    raise MarkerError(f"nenhum decorador `pytest.mark.xfail(...)` encontrado em `{func.name}`")


def _keyword_value(call: ast.Call, name: str) -> object:
    for kw in call.keywords:
        if kw.arg == name:
            if isinstance(kw.value, ast.Constant):
                return kw.value.value
            raise MarkerError(
                f"`{name}=` do xfail nao e' um literal constante (e' {type(kw.value).__name__}) — "
                "este gate so consegue verificar literais, nunca expressoes computadas."
            )
    return None


def _extract_marker(source: str, test_name: str) -> tuple[bool, str]:
    """Returns (strict, reason) for the named test's `pytest.mark.xfail`. Raises MarkerError."""
    tree = ast.parse(source)
    func = _find_function(tree, test_name)
    if func is None:
        raise MarkerError(f"nenhuma `def {test_name}` encontrada no arquivo")
    call = _xfail_call(func)
    strict = _keyword_value(call, "strict")
    reason = _keyword_value(call, "reason")
    if not isinstance(reason, str):
        raise MarkerError(f"`reason=` do xfail em `{test_name}` ausente ou nao-string")
    return bool(strict is True), reason


def evaluate(repo_root: Path, tracked: Sequence[TrackedXfail], today: date) -> list[Finding]:
    """The whole comparator, pure over its inputs (repo_root only for file reads).

    Args:
        repo_root: where to resolve each tracked `path` from.
        tracked: the code-frozen registry (normally `TRACKED_XFAILS`).
        today: the reference date. Injected so expiry is testable at any point in its life instead
            of only on the day the suite happens to run.
    """
    findings: list[Finding] = []
    for entry in tracked:
        full_path = repo_root / entry.path
        if not full_path.is_file():
            findings.append(
                Finding(
                    f"{entry.path}::{entry.test_name}: arquivo nao encontrado — o desvio "
                    "rastreado perdeu o objeto dele (renomeado/movido sem atualizar "
                    "TRACKED_XFAILS, ou removido sem fechar a excecao)."
                )
            )
            continue
        try:
            strict, reason = _extract_marker(full_path.read_text(encoding="utf-8"), entry.test_name)
        except (MarkerError, SyntaxError) as exc:
            findings.append(Finding(f"{entry.path}::{entry.test_name}: {exc}"))
            continue
        if not strict:
            findings.append(
                Finding(
                    f"{entry.path}::{entry.test_name}: xfail nao e' `strict=True` — um xfail "
                    "nao-estrito tolera em silencio tanto o teste passar quanto falhar."
                )
            )
        deadline_iso = entry.deadline.isoformat()
        if deadline_iso not in reason:
            findings.append(
                Finding(
                    f"{entry.path}::{entry.test_name}: a string `reason=` nao contem a data "
                    f"rastreada `{deadline_iso}` — o motivo do xfail e o prazo deste gate podem "
                    "ter divergido."
                )
            )
        if today > entry.deadline:
            findings.append(
                Finding(
                    f"PRAZO VENCIDO — {entry.path}::{entry.test_name}: `{deadline_iso}` passou "
                    f"(hoje = {today.isoformat()}).\n      {entry.what_expiry_means}\n"
                    f"{RENEWAL_PROCEDURE}"
                )
            )
    return findings


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--today",
        default=None,
        metavar="YYYY-MM-DD",
        help="data de referencia (default: data do sistema). Existe para tornar o vencimento testavel.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.today is None:
        today = date.today()
    else:
        try:
            today = date.fromisoformat(args.today)
        except ValueError:
            print(
                f"[check-d7-xfail-expiry] FAIL: --today {args.today!r} nao e uma data ISO YYYY-MM-DD",
                file=sys.stderr,
            )
            return 1

    findings = evaluate(REPO_ROOT, TRACKED_XFAILS, today)

    if findings:
        print(f"[check-d7-xfail-expiry] FAIL @ {today.isoformat()}:", file=sys.stderr)
        for finding in findings:
            print(finding.render(), file=sys.stderr)
        return 1

    print(
        f"[check-d7-xfail-expiry] PASS @ {today.isoformat()} "
        f"({len(TRACKED_XFAILS)} expected-fail(s) rastreado(s), em dia)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
