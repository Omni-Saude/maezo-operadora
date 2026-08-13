#!/usr/bin/env python3
"""CI gate: a ratified SHADOW DEVIATION may not outlive its date (PLANS.md §0.8, 2ª leva Q-2/Q-10).

Purpose
-------
The owner ratified on 2026-08-13 that the two surviving `shadow` values stay shadow **with a named
owner, a hard deadline and measurable exit criteria** — "sombra sem dono e sem prazo é desvio
permanente disfarçado". A deadline written in prose has no mechanism to notice it went by. This
gate is that mechanism: it reads the `deviation:` blocks that now sit next to those two values and
turns EVERY pull request red the day one of them expires while its value is still `shadow`.

THERE IS NO SILENT RENEWAL. Once a deadline passes, nothing merges green until a human does one of
exactly two things:

  1. flip the value to `enforcing` — the terminal act of the rollout (design §9.4 step 7), after
     which the deviation is over and its block becomes history; or
  2. ratify a NEW, DATED deviation — a data-only PR under CODEOWNERS review, the same discipline
     the owner ratified for per-class flips (Q-1).

Option 2 deserves a note, because it is the property that makes this gate honest rather than
merely annoying: the renewal PR is itself GREEN, because the gate reads the dates out of the tree
under test and that tree already carries the new date. No PR before it goes green, and no
after-the-fact exception is needed to land the renewal. Extending a deviation is possible; doing it
QUIETLY is not.

Fail-closed contract
--------------------
For every deviation in the code-frozen `TRACKED_DEVIATIONS` table below:

- Value still `shadow` + `deviation:` block ABSENT            -> FAIL. Deleting the deadline is not
  the same act as not having one, and it must not be the cheaper one.
- Value still `shadow` + block MALFORMED                      -> FAIL, quoting every defect the
  loader recorded. A block that does not parse is a deadline nobody can check.
- Value still `shadow` + `today` PAST the deadline            -> FAIL, naming the deviation, its
  owner role, the date it blew, and the renewal procedure verbatim.
- Value still `shadow` + deadline within `WARNING_WINDOW_DAYS` -> WARNING (`::warning::` + step
  summary), exit 0. Loud, early, and never a blocker on its own.
- Value flipped to `enforcing`                                 -> EXEMPT, exit 0. The deviation
  ended; its block is historical. A stale block left behind emits an informational note (it is
  tidy-up, not a violation) — silence there would be the gate quietly disagreeing with the record.
- The manifest will not load, or a tracked class is gone      -> FAIL. A gate that cannot read the
  record must not report that the record is fine.

Reading the record
------------------
Through the LOADER (`maezo.gateway.action_execution.load_action_approvals`), never by re-parsing
YAML here. Both the live values (`default_enforcement`, `class_enforcement`) and the metadata
(`deviations`, `deviation_defects`) come off the same typed view the runtime resolves, so this gate
cannot drift into checking a different reading of the file than the gateway acts on.

Non-vacuity
-----------
Before trusting any real measurement, `self_check()` runs the same comparator against synthetic
records and asserts it can FAIL (expired, missing, malformed) and can PASS (in-date, flipped). A
green result from a comparator that has not been shown capable of red is not evidence — the same
posture `generate_release_floor.py` takes.

Usage
-----
    python scripts/ci/check_deviation_expiry.py                    # CI gate (system date)
    python scripts/ci/check_deviation_expiry.py --today 2026-11-12  # what CI does on that date
    python scripts/ci/check_deviation_expiry.py --manifest PATH     # against another manifest
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # pragma: no cover - typing only; the runtime import is deferred in `main`
    from maezo.gateway.action_execution import ActionApprovals, DeviationRecord

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path("spec/policies/autonomy/action-approvals.yaml")

#: How long before a deadline the gate starts shouting. Two weeks is one sprint plus slack: long
#: enough that "flip it or re-ratify it" can be scheduled, short enough that the warning still
#: means something when it appears. A warning NEVER fails the build on its own — the deadline does.
WARNING_WINDOW_DAYS: Final[int] = 14

#: The renewal procedure, verbatim in every red message. A gate that says "expired" without saying
#: what closes it teaches people to look for the override instead of the owner.
RENEWAL_PROCEDURE: Final[str] = (
    "COMO SAIR DO VERMELHO (ato humano, uma das duas — nenhuma é editável por agente):\n"
    "      (a) VIRAR O VALOR para `enforcing` no manifesto — o desvio acabou, e o bloco\n"
    "          `deviation` vira histórico; ou\n"
    "      (b) RE-RATIFICAR um desvio NOVO E DATADO: o dono atualiza `owner_role`/data/\n"
    "          `checkpoint` no bloco `deviation`, em PR DE DADOS sob revisão CODEOWNERS\n"
    "          (a disciplina que o dono ratificou na Q-1 para todo flip de classe).\n"
    "      O PR de renovação é VERDE por construção — este gate lê as datas da árvore em teste,\n"
    "      e a árvore dele já carrega a data nova. Prorrogar é possível; prorrogar EM SILÊNCIO\n"
    "      não é, e nenhum outro PR fica verde antes."
)


@dataclass(frozen=True, slots=True)
class TrackedDeviation:
    """One deviation this gate is responsible for. CODE-FROZEN — data cannot add or drop one.

    Which deviations exist is a governance fact ratified by the owner, so it lives in code that a
    reviewer reads, not in the manifest the gate is checking. A manifest that could remove itself
    from the tracked set would be a record that grades its own homework.

    Attributes:
        slot: the key the loader files this deviation under (`ActionApprovals.deviations`).
        action_class: the `acoes` class whose `enforcement` this governs, or None for the root
            `enforcement_padrao_nao_mapeado` default.
        label: how the deviation is named in output — the design question plus the value.
        deadline_field: the deadline name the ratified record uses (`expires` / `review_by`).
            PINNED: swapping one for the other rewrites what the owner ratified, so it is a
            finding, not a formatting choice.
        what_expiry_means: the one-line consequence, printed with every red so the reader does not
            have to reconstruct why this date exists.
    """

    slot: str
    action_class: str | None
    label: str
    deadline_field: str
    what_expiry_means: str


#: THE TRACKED SET. Both entries are the owner's 2026-08-13 ratification (PLANS.md §0.8, 2ª leva).
TRACKED_DEVIATIONS: Final[tuple[TrackedDeviation, ...]] = (
    TrackedDeviation(
        slot="enforcement_padrao_nao_mapeado",
        action_class=None,
        label="Q-2 — `enforcement_padrao_nao_mapeado: shadow` (default de refs NÃO MAPEADAS)",
        deadline_field="expires",
        what_expiry_means=(
            "desvio explícito do literal do XRD-09 do ADR-0037 ('ação/política desconhecida … "
            "negam'). Vencido significa: o rollout passou do prazo que o dono deu para restaurar "
            "o literal, e o default de refs não mapeadas continua sem negar."
        ),
    ),
    TrackedDeviation(
        slot="acoes.leitura_phi_clinica",
        action_class="leitura_phi_clinica",
        label="Q-10 — classe C2 `leitura_phi_clinica: shadow` (leituras PHI via FHIR)",
        deadline_field="review_by",
        what_expiry_means=(
            "a Médica ratificou SOMBRA com data de REVISÃO, não sombra indefinida (design §10 "
            "Q-10). Vencido significa: a revisão clínica que justificaria manter a classe fora "
            "do ramp não aconteceu na data marcada."
        ),
    ),
)

LEVEL_OK: Final[str] = "OK"
LEVEL_INFO: Final[str] = "INFO"
LEVEL_WARN: Final[str] = "WARN"
LEVEL_FAIL: Final[str] = "FAIL"


@dataclass(frozen=True, slots=True)
class Finding:
    """One verdict about one tracked deviation."""

    level: str
    slot: str
    headline: str
    detail: str = ""

    def render(self) -> str:
        body = f"[{self.level}] {self.slot}: {self.headline}"
        return f"{body}\n{self.detail}" if self.detail else body


def _describe(record: DeviationRecord, today: date) -> str:
    remaining = record.days_remaining(today)
    when = (
        f"venceu há {-remaining} dia(s)"
        if remaining < 0
        else "vence hoje"
        if remaining == 0
        else f"faltam {remaining} dia(s)"
    )
    deadline_label = f"{record.deadline_field}:"
    return (
        f"      owner_role:   {record.owner_role}\n"
        f"      {deadline_label:<13} {record.deadline.isoformat()}  "
        f"({when}; hoje = {today.isoformat()})\n"
        f"      checkpoint:   {record.checkpoint.isoformat()}\n"
        f"      ratificado:   {record.ratified_on.isoformat()} por {record.ratified_by}\n"
        f"      criteria_ref: {record.criteria_ref}"
    )


def evaluate(approvals: ActionApprovals, today: date) -> list[Finding]:
    """The whole comparator, as a pure function. No I/O, no environment, no clock.

    Args:
        approvals: the typed manifest view (from the loader — never re-parsed YAML).
        today: the date to compare against. Injected so the expiry behaviour is testable at any
            point in its life instead of only on the day the suite happens to run.
    """
    findings: list[Finding] = []

    # A view that could not be loaded approves nothing at runtime (fail-closed there) — but HERE
    # it would silently mean "no shadow values found, nothing to check", which is a pass for the
    # wrong reason. Refuse to grade an unreadable record.
    if approvals.degraded:
        return [
            Finding(
                level=LEVEL_FAIL,
                slot="(manifesto)",
                headline=(
                    "o manifesto não carregou — este gate não pode declarar 'em dia' um registro "
                    "que não conseguiu ler"
                ),
                detail=(
                    "      Rode `make effect-chokepoint-fence` e os testes de cerca em "
                    "tests/unit/sec/ para o motivo exato."
                ),
            )
        ]

    for tracked in TRACKED_DEVIATIONS:
        findings.append(_evaluate_one(approvals, tracked, today))
    return findings


def _evaluate_one(approvals: ActionApprovals, tracked: TrackedDeviation, today: date) -> Finding:
    # -- the LIVE value this deviation excuses ---------------------------------------------------
    if tracked.action_class is None:
        live_value = approvals.default_enforcement
    elif tracked.action_class not in approvals.declared:
        # Catalogue/manifest drift. The class this deviation is ABOUT stopped existing, so the
        # deviation is now unanchored — the gate must say so rather than quietly track nothing.
        return Finding(
            level=LEVEL_FAIL,
            slot=tracked.slot,
            headline=(
                f"a classe {tracked.action_class!r} não está mais declarada em `acoes` — o desvio "
                "rastreado perdeu o objeto dele"
            ),
            detail=(
                f"      {tracked.label}\n"
                "      Ou a classe foi removida/renomeada (então TRACKED_DEVIATIONS em\n"
                "      scripts/ci/check_deviation_expiry.py precisa acompanhar, em PR revisado), ou o\n"
                "      manifesto regrediu. Nenhum dos dois é coisa que um gate deva assumir sozinho."
            ),
        )
    else:
        live_value = approvals.class_enforcement[tracked.action_class]

    record = approvals.deviations.get(tracked.slot)
    defects = approvals.deviation_defects.get(tracked.slot, ())

    # -- the value was FLIPPED: the deviation is over -------------------------------------------
    if live_value != "shadow":
        if record is not None or defects:
            return Finding(
                level=LEVEL_INFO,
                slot=tracked.slot,
                headline=(
                    f"valor está `{live_value}` — desvio ENCERRADO; o bloco `deviation` que sobrou é "
                    "histórico e pode ser removido no próximo PR de dados"
                ),
                detail=f"      {tracked.label}",
            )
        return Finding(
            level=LEVEL_OK,
            slot=tracked.slot,
            headline=f"valor está `{live_value}` — desvio ENCERRADO, nada a cobrar",
        )

    # -- still shadow: the block is REQUIRED -----------------------------------------------------
    if defects:
        return Finding(
            level=LEVEL_FAIL,
            slot=tracked.slot,
            headline="o valor segue `shadow` e o bloco `deviation` NÃO É VÁLIDO",
            detail=(
                f"      {tracked.label}\n"
                + "\n".join(f"      defeito: {d}" for d in defects)
                + "\n      Um bloco que não parseia é um prazo que ninguém consegue conferir.\n"
                + f"      {RENEWAL_PROCEDURE}"
            ),
        )
    if record is None:
        return Finding(
            level=LEVEL_FAIL,
            slot=tracked.slot,
            headline="o valor segue `shadow` e NÃO EXISTE bloco `deviation` — sombra sem dono e sem prazo",
            detail=(
                f"      {tracked.label}\n"
                f"      {tracked.what_expiry_means}\n"
                "      Apagar o prazo NÃO é o mesmo ato que não ter prazo, e não pode ser o mais barato:\n"
                "      o dono ratificou estes desvios COM owner nomeado e data (PLANS.md §0.8, 2ª leva).\n"
                f"      {RENEWAL_PROCEDURE}"
            ),
        )
    if record.deadline_field != tracked.deadline_field:
        return Finding(
            level=LEVEL_FAIL,
            slot=tracked.slot,
            headline=(
                f"o bloco usa `{record.deadline_field}` mas o desvio ratificado usa "
                f"`{tracked.deadline_field}`"
            ),
            detail=(
                f"      {tracked.label}\n"
                "      O nome do campo faz parte do que o dono ratificou: `expires` diz 'vire até esta\n"
                "      data', `review_by` diz 'revise até esta data'. Trocar um pelo outro reescreve a\n"
                "      decisão. Se a troca é intencional, ela é uma re-ratificação — e o pin em\n"
                "      TRACKED_DEVIATIONS tem de mudar junto, no mesmo PR revisado."
            ),
        )

    remaining = record.days_remaining(today)
    if remaining < 0:
        return Finding(
            level=LEVEL_FAIL,
            slot=tracked.slot,
            headline=(
                f"DESVIO VENCIDO — `{record.deadline_field}: {record.deadline.isoformat()}` passou e "
                "o valor segue `shadow`"
            ),
            detail=(
                f"      {tracked.label}\n"
                f"      {tracked.what_expiry_means}\n"
                f"{_describe(record, today)}\n"
                f"      {RENEWAL_PROCEDURE}"
            ),
        )
    if remaining <= WARNING_WINDOW_DAYS:
        return Finding(
            level=LEVEL_WARN,
            slot=tracked.slot,
            headline=(
                f"vence em {remaining} dia(s) — `{record.deadline_field}: "
                f"{record.deadline.isoformat()}`, e o valor ainda é `shadow`"
            ),
            detail=(f"      {tracked.label}\n{_describe(record, today)}\n      {RENEWAL_PROCEDURE}"),
        )
    return Finding(
        level=LEVEL_OK,
        slot=tracked.slot,
        headline=(
            f"em dia — `{record.deadline_field}: {record.deadline.isoformat()}` "
            f"(faltam {remaining} dia(s)), owner: {record.owner_role}"
        ),
    )


def exit_code_for(findings: Sequence[Finding]) -> int:
    """1 iff anything failed. WARNINGs are loud and green — the deadline is what blocks."""
    return 1 if any(f.level == LEVEL_FAIL for f in findings) else 0


# ---------------------------------------------------------------------------------------------
# Non-vacuity self-check — prove the comparator can go red AND green before trusting it.
#
# THE SCENARIO SET IS A MODULE-LEVEL CONSTANT, not a local inside `self_check()`. That shape is
# copied from `scripts/ci/generate_release_floor.py` (`SELF_CHECK_SCENARIOS` +
# `test_self_check_scenarios_are_internally_consistent`) for a specific reason: a self-check whose
# fixtures are invisible from outside is itself unwatched. Gutting `self_check()` to `return []`
# would then be a green no-op that no test can see — the watchman with nobody watching him. Hoisted
# here, the SET can be pinned against a hardcoded expectation, each declared RED scenario can be
# driven independently through the real `evaluate`, and `self_check`'s ability to REPORT can be
# proved by feeding it a deliberately broken evaluator. See `tests/unit/ci/
# test_check_deviation_expiry.py`, section "Non-vacuity of the gate itself".
# ---------------------------------------------------------------------------------------------

#: The date every self-check scenario is evaluated on. Fixed, so the fixtures below mean the same
#: thing on every future run — a self-check that drifts with the wall clock is not a control.
SELF_CHECK_TODAY = date(2026, 8, 13)

#: `(scenario name, expected level)` — THE declared scenario set, as pure data so it can be pinned
#: without importing the gateway. Every level the gate can emit for a tracked slot appears at least
#: once, and the four FAIL rows are the four distinct ways a shadow deviation can escape its date:
#:   vencido            — the deadline passed and the value is still `shadow` (the headline case);
#:   ausente            — the whole `deviation` block was deleted (deleting the deadline must never
#:                        be cheaper than honouring it);
#:   malformado         — the block is present but does not parse (an uncheckable deadline);
#:   manifesto ilegível — the manifest itself cannot be read (fail-closed on the file, not the slot).
#: The WARN and OK rows are what keep the set honest: a comparator that returns FAIL for everything
#: would satisfy the RED rows alone, so green has to be reachable too.
SELF_CHECK_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("vencido", LEVEL_FAIL),
    ("ausente", LEVEL_FAIL),
    ("malformado", LEVEL_FAIL),
    ("janela de aviso", LEVEL_WARN),
    ("em dia", LEVEL_OK),
    ("valor virado", LEVEL_OK),
    ("manifesto ilegível", LEVEL_FAIL),
)


def build_self_check_cases() -> tuple[tuple[str, ActionApprovals, str], ...]:
    """Materialize `SELF_CHECK_SCENARIOS` into `(name, ActionApprovals view, expected level)`.

    Separate from `self_check()` so the tests can drive each scenario through the real `evaluate`
    themselves, instead of only being able to observe the aggregate verdict.

    Raises if the built cases and the declared set ever disagree: the constant is the contract, and
    a scenario silently dropped from one side is precisely the drift this split exists to catch.
    """
    from maezo.gateway.action_execution import (  # noqa: PLC0415 - deferred with the rest
        ActionApprovals,
        DeviationRecord,
    )

    tracked = TRACKED_DEVIATIONS[0]
    today = SELF_CHECK_TODAY

    def _record(deadline: date) -> DeviationRecord:
        return DeviationRecord(
            slot=tracked.slot,
            owner_role="self-check role",
            ratified_on=today,
            ratified_by="self-check",
            deadline_field=tracked.deadline_field,
            deadline=deadline,
            checkpoint=deadline,
            criteria_ref="self-check",
        )

    def _view(**kwargs: object) -> ActionApprovals:
        base: dict[str, object] = {
            "mode": "shadow",
            "approved": frozenset(),
            "declared": frozenset({c.action_class for c in TRACKED_DEVIATIONS if c.action_class}),
            "topic_to_class": {},
            "class_enforcement": {c.action_class: "shadow" for c in TRACKED_DEVIATIONS if c.action_class},
            "default_enforcement": "shadow",
        }
        base.update(kwargs)
        return ActionApprovals(**base)  # type: ignore[arg-type]

    views: dict[str, ActionApprovals] = {
        "vencido": _view(deviations={tracked.slot: _record(date(2026, 8, 12))}),
        "ausente": _view(),
        "malformado": _view(deviation_defects={tracked.slot: ("synthetic defect",)}),
        "janela de aviso": _view(deviations={tracked.slot: _record(date(2026, 8, 20))}),
        "em dia": _view(deviations={tracked.slot: _record(date(2027, 8, 20))}),
        "valor virado": _view(default_enforcement="enforcing"),
        "manifesto ilegível": _view(degraded=True),
    }
    declared = [name for name, _ in SELF_CHECK_SCENARIOS]
    if sorted(views) != sorted(declared):
        raise RuntimeError(
            "self-check scenarios drifted: SELF_CHECK_SCENARIOS declares "
            f"{sorted(declared)} but build_self_check_cases builds {sorted(views)}"
        )
    return tuple((name, views[name], expected) for name, expected in SELF_CHECK_SCENARIOS)


def self_check() -> list[str]:
    """Drive `evaluate` over synthetic records. Returns the problems found (empty == healthy).

    A gate whose green has never been shown to be reachable-from-red is decoration. This runs
    first, every time, on synthetic data only — it never touches the shipped manifest.
    """
    problems: list[str] = []
    tracked = TRACKED_DEVIATIONS[0]

    def _level(view: ActionApprovals) -> str:
        """The verdict for `tracked`, or the whole-manifest verdict when there is no per-slot one.

        The unreadable-manifest case short-circuits before any slot is examined and reports one
        finding for the file, so there is deliberately nothing keyed to a slot to look up.
        """
        findings = evaluate(view, SELF_CHECK_TODAY)
        return next((f.level for f in findings if f.slot == tracked.slot), findings[0].level)

    for name, view, expected in build_self_check_cases():
        actual = _level(view)
        if actual != expected:
            problems.append(f"self-check '{name}': esperava {expected}, veio {actual}")
    return problems


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------


def _emit_github(findings: Sequence[Finding]) -> None:
    """GitHub annotations + step summary. A no-op outside Actions (both are opt-in by env)."""
    for finding in findings:
        if finding.level == LEVEL_WARN:
            print(f"::warning title=Desvio de sombra vencendo::{finding.slot}: {finding.headline}")
        elif finding.level == LEVEL_FAIL:
            print(f"::error title=Desvio de sombra vencido::{finding.slot}: {finding.headline}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = ["### Desvios de sombra ratificados (PLANS.md §0.8, 2ª leva Q-2/Q-10)", ""]
    icon = {LEVEL_OK: "✅", LEVEL_INFO: "ℹ️", LEVEL_WARN: "⚠️", LEVEL_FAIL: "❌"}
    for finding in findings:
        lines.append(f"- {icon.get(finding.level, '•')} **{finding.slot}** — {finding.headline}")
    lines.append("")
    try:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:  # pragma: no cover - a broken summary must never fail the gate
        print(f"[deviation-expiry] aviso: não consegui escrever o step summary ({exc})")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--today",
        default=None,
        metavar="YYYY-MM-DD",
        help="data de referência (default: data do sistema). Existe para tornar o vencimento testável.",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help=f"manifesto a verificar (default: {DEFAULT_MANIFEST})",
    )
    args = parser.parse_args(argv)

    if args.today is None:
        today = date.today()
    else:
        try:
            today = date.fromisoformat(args.today)
        except ValueError:
            print(
                f"[deviation-expiry] FAIL: --today {args.today!r} não é uma data ISO YYYY-MM-DD",
                file=sys.stderr,
            )
            return 1

    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from maezo.gateway.action_execution import load_action_approvals  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - an unimportable loader is itself a fail-closed finding
        print(f"[deviation-expiry] FAIL: não consegui importar o loader ({exc})", file=sys.stderr)
        return 1

    problems = self_check()
    if problems:
        print("[deviation-expiry] FAIL: o auto-teste de não-vacuidade não passou —", file=sys.stderr)
        print("  um comparador que não se prova capaz de reprovar não vale como prova.", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = REPO_ROOT / manifest_path
    findings = evaluate(load_action_approvals(manifest_path), today)

    stream = sys.stderr if exit_code_for(findings) else sys.stdout
    print(f"[deviation-expiry] {manifest_path} @ {today.isoformat()}", file=stream)
    for finding in findings:
        print(f"  {finding.render()}", file=stream)

    _emit_github(findings)

    code = exit_code_for(findings)
    if code:
        print(
            "\n[deviation-expiry] FAIL: um desvio de sombra ratificado passou do prazo (ou perdeu o "
            "registro dele).\n"
            "  Este gate roda em TODO PR, dentro do check obrigatório `validate-artifacts`. Ele não "
            "tem override,\n"
            "  e é assim de propósito: renovação silenciosa é exatamente o que o dono recusou ao "
            "ratificar\n"
            "  'sombra sem dono e sem prazo é desvio permanente disfarçado' (PLANS.md §0.8, 2ª leva).",
            file=sys.stderr,
        )
    else:
        print("[deviation-expiry] PASS")
    return code


if __name__ == "__main__":
    sys.exit(main())
