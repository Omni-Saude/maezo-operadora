"""FAIL-CLOSED FIRST-hit readers for live DMN tables and their shadow-candidate manifests.

WHY THIS EXISTS. The W3 precedent (`spec/processes/dmn/adequacao-gap-shadow-candidate.yaml` +
`src/maezo/tools/workers/adequacao_shadow.py`) proves a known-wrong table's defect by DERIVING the
live verdict from the artifact instead of asserting it in prose. Its reader, however, lives inside
`tests/unit/tools/workers/test_adequacao_shadow.py` (`_matches`/`_live_rules`/`live_verdict`) — it
is NOT part of `adequacao_shadow.py` — and its entry grammar covers exactly the shapes
`adequacao_gap.dmn` uses: `-`, a single quoted literal, `true`/`false`, a bare number and the unary
comparisons. It DELIBERATELY REFUSES `not(...)` and the FEEL comma-disjunction `"a","b"`.

The four tables this module serves need those two shapes:

  - `glosa_triage.dmn`            — `not("tecnica","clinica")` (:56) and `"tecnica","clinica"` (:66)
  - `triage_redflag_gestante`/`_pediatric` — `"grave","moderada"` (:80 / :79)

so the grammar had to widen. It widened HERE, in a new module, rather than in the W3 test module:
that module's refusal of `not(...)`/disjunction is itself a pin on `adequacao_gap.dmn`'s entry
shapes, and loosening it would silently retire that pin. This module is a SUPERSET grammar, and it
refuses everything outside it — including shapes that do exist elsewhere in `spec/` and are NOT
supported here (`starts with(...)` in `phantom_suspicious_prefix.dmn`, FEEL ranges). A reader that
returned "no match" for an entry it could not parse would turn a table edit into a quietly wrong
divergence corpus; every such entry RAISES instead.

WHAT IT DOES NOT CLAIM. This is a reader, not an engine. It reproduces FIRST-hit evaluation over
the entry grammar it declares, and it refuses tables whose `hitPolicy` is not `FIRST`. It makes NO
claim about what a Camunda-7-family engine does when an input VARIABLE IS ABSENT from the payload
(see `NULL_MATCHES_ONLY_WILDCARD`).
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: Repository root — `tests/support/<this file>` -> up 2.
REPO_ROOT = Path(__file__).resolve().parents[2]
DMN_DIR = REPO_ROOT / "spec" / "processes" / "dmn"
CODEOWNERS = REPO_ROOT / ".github" / "CODEOWNERS"

#: A `None` input value models an input that is PRESENT AND NULL. Only the `-` wildcard matches it;
#: every other entry reads as "no match". This is the FEEL unary-test reading of null, and it is
#: NOT a claim about an input that is ABSENT from the evaluation payload altogether — whether the
#: engine even reaches rule evaluation in that case is a live-engine question this module does not
#: answer. No divergence corpus in this wave uses a `None` value; the ACHADOs that concern absent
#: inputs say so in prose, with the uncertainty stated.
NULL_MATCHES_ONLY_WILDCARD = True

#: Ratification fields and the machine-detectable placeholder marker — the SAME three fields, same
#: strictness, as `maezo.tools.workers.adequacao_shadow` (and `auth_criteria` before it, ADR-0007).
RATIFIED_FLAG = "ratificado"
RATIFIED_BY = "revisor"
RATIFIED_AT = "ratificado_em"
PLACEHOLDER_MARKER = "PLACEHOLDER"

#: LIVE-TABLE BINDING. Ratifying answers "did a human approve"; it does not answer "approve WHAT".
#: Every candidate manifest declares `tabela_viva: {path, sha256}` — the live table it was authored
#: and reviewed against, identified by the DIGEST OF ITS BYTES. `verify_live_table_binding`
#: re-derives that digest from disk, so editing a live table breaks the build until the candidate is
#: re-authored against the new content, and a ratification can never silently carry over. Same
#: standard as `amh_inbox.migration_digest()` and `tiss_schema_pin`'s gate 5 — bind to bytes, never
#: to a filename. The `src/`-side half of this binding exists only for the W3 manifest (the only one
#: with a `src/` consumer): `maezo.tools.workers.adequacao_shadow` re-checks it at LOAD time too.
LIVE_TABLE_BLOCK = "tabela_viva"
LIVE_TABLE_PATH = "path"
LIVE_TABLE_SHA256 = "sha256"
_KNOWN_LIVE_TABLE_KEYS = frozenset({LIVE_TABLE_PATH, LIVE_TABLE_SHA256})
#: The one directory a candidate may bind to — its own. `spec/processes/dmn/<bare filename>.dmn`.
LIVE_TABLE_RELDIR = "spec/processes/dmn"
#: Lowercase hex sha256 and nothing else — which is also the placeholder guard for this field: no
#: string containing `PLACEHOLDER` can match it.
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

#: `"a"` | `"a","b"` | `"a", "b"` — a quoted literal or a FEEL comma-disjunction of quoted literals.
_QUOTED_LIST = re.compile(r'^"[^"]*"(?:\s*,\s*"[^"]*")*$')
#: `not( <quoted list> )` — FEEL negation of a literal disjunction.
_NOT_CALL = re.compile(r"^not\s*\(\s*(?P<inner>.*?)\s*\)$", re.DOTALL)
#: `730 - dias_desde_adesao` — the only OUTPUT expression shape any target table uses.
_LINEAR_SUB = re.compile(r"^(?P<const>-?\d+)\s*-\s*(?P<var>[A-Za-z_][A-Za-z0-9_]*)$")
#: A bare integer output (`0`, `10`, `-1`).
_INT_LITERAL = re.compile(r"^-?\d+$")


class UnreadableEntryError(RuntimeError):
    """An entry shape this reader does not understand — NEVER silently treated as a match."""


class LiveTableBindingError(RuntimeError):
    """A candidate manifest's `tabela_viva` binding does not hold against the live table on disk.

    Deliberately NOT an `UnreadableEntryError`: that one means "this reader does not understand a
    grammar". This one means "the ratification record and the table content have come apart" — a
    governance-integrity failure, which must never be confused with a parsing problem.
    """


class EnforcementNotRatifiedError(RuntimeError):
    """An ENFORCING use of a candidate rule set was attempted while it is not ratified.

    A candidate is a PROPOSAL to the table's regulatory/clinical owner, not a rule. Until the owner
    ratifies the manifest AND applies the rules to the live table (per ADR-0028 §7 the live-table
    edit is the OWNER's act, never engineering's), the only sanctioned use is observation.
    """


@dataclass(frozen=True, slots=True)
class LinearSub:
    """`const - <var>` — e.g. `carencia_check.dmn:95` `730 - dias_desde_adesao`."""

    const: int
    var: str

    def evaluate(self, values: Mapping[str, Any]) -> int:
        operand = values[self.var]
        if isinstance(operand, bool) or not isinstance(operand, int):
            raise UnreadableEntryError(f"{self.var} is not an integer: {operand!r}")
        return self.const - operand


@dataclass(frozen=True, slots=True)
class Rule:
    """One FIRST-hit rule: its id, its input entries and its parsed output values, in column order."""

    rule_id: str
    inputs: tuple[str, ...]
    outputs: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class DecisionTable:
    """A FIRST-hit decision table — read from live DMN XML or from a candidate YAML manifest."""

    decision_id: str
    source: Path
    input_names: tuple[str, ...]
    output_names: tuple[str, ...]
    rules: tuple[Rule, ...]

    def rule_ids(self) -> tuple[str, ...]:
        return tuple(rule.rule_id for rule in self.rules)


@dataclass(frozen=True, slots=True)
class Verdict:
    """The matched rule id plus every output of that rule, keyed by output name."""

    regra: str
    saidas: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LiveTableBinding:
    """A VERIFIED binding: the manifest, the live table it declares, and that table's actual digest.

    Only ever constructed by `verify_live_table_binding`, and only after the declared sha256 has
    been compared against a digest RE-DERIVED from the table's bytes — so an instance's existence is
    itself the proof that the candidate (and any ratification of it) covers the table on disk.
    """

    manifest: Path
    live_table: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class CandidateRatification:
    """Ratification state of a candidate manifest. FAIL-CLOSED default: not ratified."""

    ratificado: bool
    revisor: str = ""
    ratificado_em: str = ""


_NOT_RATIFIED = CandidateRatification(ratificado=False)


# -------------------------------------------------------------------------------------------------
# Entry grammar
# -------------------------------------------------------------------------------------------------


def _quoted_literals(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r'"([^"]*)"', text))


def matches(entry: str, value: Any) -> bool:
    """Does one input entry match `value`? Raises `UnreadableEntryError` on any other shape.

    Grammar: `-`; `true`/`false`; `>=`/`<=`/`>`/`<` + number; a bare number; a quoted literal; a
    FEEL comma-disjunction of quoted literals; `not(<disjunction>)`. Nothing else.
    """
    text = entry.strip()
    if text == "-":
        return True

    negated = _NOT_CALL.match(text)
    if negated is not None:
        inner = negated.group("inner")
        if not _QUOTED_LIST.match(inner):
            raise UnreadableEntryError(
                f"unsupported DMN input entry (not(...) over a non-literal expression): {entry!r}"
            )
        if value is None:
            return False
        return value not in _quoted_literals(inner)

    if value is None:
        # PRESENT AND NULL: only the wildcard above matches. See NULL_MATCHES_ONLY_WILDCARD. The
        # entry is still VALIDATED below so an unreadable shape raises rather than reading as False.
        _validate_entry(text)
        return False

    if text in ("true", "false"):
        return value is (text == "true")

    for op in (">=", "<=", ">", "<"):
        if text.startswith(op):
            try:
                operand = float(text[len(op) :].strip())
            except ValueError as exc:
                raise UnreadableEntryError(f"unsupported DMN input entry: {entry!r}") from exc
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            if op == ">":
                return value > operand
            if op == "<":
                return value < operand
            if op == ">=":
                return value >= operand
            return value <= operand

    if _QUOTED_LIST.match(text):
        return value in _quoted_literals(text)

    try:
        operand = float(text)
    except ValueError as exc:
        raise UnreadableEntryError(f"unsupported DMN input entry: {entry!r}") from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == operand


def _validate_entry(text: str) -> None:
    """Raise unless `text` is a shape `matches` understands (used on the null path)."""
    if text in ("true", "false") or _QUOTED_LIST.match(text):
        return
    for op in (">=", "<=", ">", "<"):
        if text.startswith(op):
            try:
                float(text[len(op) :].strip())
            except ValueError as exc:
                raise UnreadableEntryError(f"unsupported DMN input entry: {text!r}") from exc
            return
    try:
        float(text)
    except ValueError as exc:
        raise UnreadableEntryError(f"unsupported DMN input entry: {text!r}") from exc


def parse_output(text: str) -> Any:
    """Parse one output entry: `true`/`false`, an integer, a quoted string, or `<int> - <var>`."""
    stripped = text.strip()
    if stripped in ("true", "false"):
        return stripped == "true"
    if stripped.startswith('"') and stripped.endswith('"') and len(stripped) >= 2:
        return stripped[1:-1]
    if _INT_LITERAL.match(stripped):
        return int(stripped)
    linear = _LINEAR_SUB.match(stripped)
    if linear is not None:
        return LinearSub(const=int(linear.group("const")), var=linear.group("var"))
    raise UnreadableEntryError(f"unsupported DMN output entry: {text!r}")


# -------------------------------------------------------------------------------------------------
# Live table (DMN XML)
# -------------------------------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _text_of(element: ET.Element) -> str:
    return (next((child.text for child in element if _local(child.tag) == "text"), "") or "").strip()


def read_live_table(path: Path) -> DecisionTable:
    """Read a live `.dmn` file. REFUSES anything but a single `hitPolicy="FIRST"` decision table."""
    root = ET.parse(path).getroot()
    decisions = [el for el in root.iter() if _local(el.tag) == "decision"]
    if len(decisions) != 1:
        raise UnreadableEntryError(f"{path.name}: expected exactly one <decision>, got {len(decisions)}")
    tables = [el for el in decisions[0] if _local(el.tag) == "decisionTable"]
    if len(tables) != 1:
        raise UnreadableEntryError(f"{path.name}: expected exactly one <decisionTable>")
    table = tables[0]
    if table.get("hitPolicy") != "FIRST":
        raise UnreadableEntryError(
            f"{path.name}: this reader assumes FIRST-hit; got {table.get('hitPolicy')!r}"
        )

    input_names = tuple(
        _text_of(next(child for child in el if _local(child.tag) == "inputExpression"))
        for el in table
        if _local(el.tag) == "input"
    )
    output_names = tuple(el.get("name") or "" for el in table if _local(el.tag) == "output")

    rules: list[Rule] = []
    for rule in (el for el in table if _local(el.tag) == "rule"):
        entries = tuple(_text_of(el) for el in rule if _local(el.tag) == "inputEntry")
        outputs = tuple(parse_output(_text_of(el)) for el in rule if _local(el.tag) == "outputEntry")
        if len(entries) != len(input_names) or len(outputs) != len(output_names):
            raise UnreadableEntryError(f"{path.name}:{rule.get('id')}: column arity mismatch")
        rules.append(Rule(rule_id=rule.get("id") or "", inputs=entries, outputs=outputs))

    return DecisionTable(
        decision_id=decisions[0].get("id") or "",
        source=path,
        input_names=input_names,
        output_names=output_names,
        rules=tuple(rules),
    )


# -------------------------------------------------------------------------------------------------
# Candidate manifest (YAML)
# -------------------------------------------------------------------------------------------------


def load_manifest(path: Path) -> dict[str, Any]:
    """Parse a candidate manifest. Raises on anything that is not a YAML mapping."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise UnreadableEntryError(f"{path.name}: root must be a mapping")
    return data


def read_candidate_table(manifest_path: Path, live: DecisionTable) -> DecisionTable:
    """Build the candidate's FIRST-hit table from `regras_candidatas`, checked against `live`.

    FAIL-CLOSED cross-checks, so a candidate can never quietly grow a column the live table does not
    have (which would make the divergence corpus meaningless): every rule's `entradas` keys must be
    EXACTLY the live table's input names, and its `saidas` keys exactly the live output names; the
    `ordem` field must be 1..N in order; and every rule must carry `candidato: true`.
    """
    data = load_manifest(manifest_path)
    raw_rules = data.get("regras_candidatas")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise UnreadableEntryError(f"{manifest_path.name}: `regras_candidatas` must be a non-empty list")

    rules: list[Rule] = []
    for position, raw in enumerate(raw_rules, start=1):
        if not isinstance(raw, dict):
            raise UnreadableEntryError(f"{manifest_path.name}: rule {position} is not a mapping")
        if raw.get("ordem") != position:
            raise UnreadableEntryError(
                f"{manifest_path.name}: rule {position} has ordem={raw.get('ordem')!r}"
            )
        if raw.get("candidato") is not True:
            raise UnreadableEntryError(
                f"{manifest_path.name}: rule {position} is not labelled candidato: true"
            )
        entradas = raw.get("entradas")
        saidas = raw.get("saidas")
        if not isinstance(entradas, dict) or tuple(entradas) != live.input_names:
            raise UnreadableEntryError(
                f"{manifest_path.name}: rule {position} `entradas` keys must be {live.input_names}"
            )
        if not isinstance(saidas, dict) or tuple(saidas) != live.output_names:
            raise UnreadableEntryError(
                f"{manifest_path.name}: rule {position} `saidas` keys must be {live.output_names}"
            )
        rules.append(
            Rule(
                rule_id=str(raw.get("id") or ""),
                inputs=tuple(str(entradas[name]) for name in live.input_names),
                outputs=tuple(_candidate_output(saidas[name]) for name in live.output_names),
            )
        )

    return DecisionTable(
        decision_id=str(data.get("decisao_alvo") or ""),
        source=manifest_path,
        input_names=live.input_names,
        output_names=live.output_names,
        rules=tuple(rules),
    )


def _candidate_output(value: Any) -> Any:
    """A manifest output: native YAML `bool`/`int`, or a string (possibly the `<int> - <var>` form)."""
    if isinstance(value, (bool, int)):
        return value
    if isinstance(value, str):
        linear = _LINEAR_SUB.match(value.strip())
        if linear is not None:
            return LinearSub(const=int(linear.group("const")), var=linear.group("var"))
        return value
    raise UnreadableEntryError(f"unsupported candidate output value: {value!r}")


# -------------------------------------------------------------------------------------------------
# Evaluation
# -------------------------------------------------------------------------------------------------


def evaluate(table: DecisionTable, values: Mapping[str, Any]) -> Verdict:
    """FIRST-hit evaluation. Refuses a `values` mapping whose keys are not exactly the inputs."""
    if tuple(values) != table.input_names:
        raise UnreadableEntryError(
            f"{table.source.name}: inputs must be exactly {table.input_names}, got {tuple(values)}"
        )
    ordered = tuple(values[name] for name in table.input_names)
    for rule in table.rules:
        if all(matches(entry, value) for entry, value in zip(rule.inputs, ordered, strict=True)):
            resolved = {
                name: (out.evaluate(values) if isinstance(out, LinearSub) else out)
                for name, out in zip(table.output_names, rule.outputs, strict=True)
            }
            return Verdict(regra=rule.rule_id, saidas=resolved)
    raise AssertionError(f"{table.source.name}: no rule matched {dict(values)} — it has no catch-all?")


# -------------------------------------------------------------------------------------------------
# Live-table binding — the ratification record is bound to the table's BYTES, not to its name
# -------------------------------------------------------------------------------------------------


def live_table_digest(path: Path) -> str:
    """sha256 (lowercase hex) of a live table's bytes on disk. Re-derived, never cached."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_live_table_binding(manifest_path: Path) -> LiveTableBinding:
    """Check a manifest's `tabela_viva` block against the live table's ACTUAL bytes. RAISES on any
    failure — a binding that cannot be confirmed is never "probably fine".

    The block must exist (an optional binding is a forgettable one), carry exactly `{path, sha256}`,
    declare the SAME path as the manifest's own `alvo` (so the two declarations cannot drift), name
    a `.dmn` file directly inside `spec/processes/dmn/` (no traversal, no other directory — checked
    both as a STRING and against the RESOLVED path, since the string check alone cannot see a
    symlink), and declare a lowercase-hex sha256 equal to the digest re-derived here from that
    file's bytes.

    The live table is always read from the REAL `spec/processes/dmn/` tree (`DMN_DIR`), never
    relative to `manifest_path`: a scratch copy of a manifest in `tmp_path` must be checked against
    the same table the committed manifest binds to, or a forged copy could bind itself to a forged
    table sitting beside it.

    Raises:
        LiveTableBindingError: on every failure mode above, including the digest mismatch a live
            table edit produces.
    """
    data = load_manifest(manifest_path)
    block = data.get(LIVE_TABLE_BLOCK)
    if not isinstance(block, dict):
        raise LiveTableBindingError(
            f"{manifest_path.name}: `{LIVE_TABLE_BLOCK}` must be a mapping declaring the live table "
            f"this candidate was authored against (got {type(block).__name__}) — without it a "
            "ratification would silently carry over to edited table content"
        )

    unknown = sorted(str(key) for key in block if key not in _KNOWN_LIVE_TABLE_KEYS)
    if unknown:
        raise LiveTableBindingError(
            f"{manifest_path.name}: `{LIVE_TABLE_BLOCK}` has unrecognized key(s) {unknown} "
            f"(known: {sorted(_KNOWN_LIVE_TABLE_KEYS)})"
        )

    declared_path = block.get(LIVE_TABLE_PATH)
    if not isinstance(declared_path, str) or not declared_path.strip():
        raise LiveTableBindingError(
            f"{manifest_path.name}: `{LIVE_TABLE_BLOCK}.{LIVE_TABLE_PATH}` must be a non-empty string"
        )
    if declared_path != data.get("alvo"):
        raise LiveTableBindingError(
            f"{manifest_path.name}: `{LIVE_TABLE_BLOCK}.{LIVE_TABLE_PATH}` is {declared_path!r} but "
            f"`alvo` is {data.get('alvo')!r} — a candidate binds to the table it targets, not another"
        )

    directory, _, filename = declared_path.rpartition("/")
    if (
        directory != LIVE_TABLE_RELDIR
        or not filename.endswith(".dmn")
        or filename in ("", ".dmn")
        or ".." in filename
        or "\\" in filename
    ):
        raise LiveTableBindingError(
            f"{manifest_path.name}: `{LIVE_TABLE_BLOCK}.{LIVE_TABLE_PATH}` is {declared_path!r}, must "
            f"be a bare `.dmn` filename directly inside {LIVE_TABLE_RELDIR}/"
        )

    live_table = (DMN_DIR / filename).resolve()
    if live_table.parent != DMN_DIR.resolve() or not live_table.is_file():
        raise LiveTableBindingError(
            f"{manifest_path.name}: `{LIVE_TABLE_BLOCK}.{LIVE_TABLE_PATH}` resolves to {live_table}, "
            f"which is not a file inside {DMN_DIR}"
        )

    declared_sha = block.get(LIVE_TABLE_SHA256)
    if not isinstance(declared_sha, str) or not _SHA256_HEX.match(declared_sha.strip().lower()):
        raise LiveTableBindingError(
            f"{manifest_path.name}: `{LIVE_TABLE_BLOCK}.{LIVE_TABLE_SHA256}` is not a lowercase hex "
            "sha256 — a blank or placeholder digest is not a binding"
        )

    actual = live_table_digest(live_table)
    if declared_sha.strip().lower() != actual:
        raise LiveTableBindingError(
            f"{manifest_path.name}: `{LIVE_TABLE_BLOCK}.{LIVE_TABLE_SHA256}` is "
            f"{declared_sha.strip().lower()!r} but {live_table.name} hashes to {actual!r} — the live "
            "table changed after this candidate was authored. The candidate (and any ratification of "
            "it) covers the OLD content: re-author it against the new table and have the owner "
            "review it again. Updating the digest alone would defeat the entire point of this field."
        )

    return LiveTableBinding(manifest=manifest_path, live_table=live_table, sha256=actual)


# -------------------------------------------------------------------------------------------------
# Ratification gate — the same shape as `adequacao_shadow.load_candidate_ratification`
# -------------------------------------------------------------------------------------------------


def _accountable(value: Any) -> str:
    """`value.strip()` when it is a non-blank, non-PLACEHOLDER string; `""` otherwise."""
    if not isinstance(value, str):
        return ""
    stripped = value.strip()
    if not stripped or PLACEHOLDER_MARKER in stripped.upper():
        return ""
    return stripped


def load_candidate_ratification(path: Path) -> CandidateRatification:
    """Load a manifest's ratification state. FAILS CLOSED — never raises, never defaults to true.

    Every failure mode (missing file, unreadable, non-UTF-8, malformed YAML, wrong schema,
    blank/placeholder accountability fields) resolves to `ratificado=False`.
    """
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - fail-closed: an unreadable manifest ratifies nothing
        return _NOT_RATIFIED
    if not isinstance(data, dict) or data.get(RATIFIED_FLAG) is not True:
        return _NOT_RATIFIED
    revisor = _accountable(data.get(RATIFIED_BY))
    ratificado_em = _accountable(data.get(RATIFIED_AT))
    if not revisor or not ratificado_em:
        return _NOT_RATIFIED
    return CandidateRatification(ratificado=True, revisor=revisor, ratificado_em=ratificado_em)


def evaluate_for_enforcement(manifest_path: Path, live: DecisionTable, values: Mapping[str, Any]) -> Verdict:
    """ENFORCING entry point — REFUSES while the manifest is not ratified.

    There is no enforcing consumer of any candidate manifest in this repository (pinned by
    `test_no_src_consumer_of_the_candidate_manifests`). This function exists so that "a candidate
    may not be enforced" is a property a test can fail on rather than a comment someone can miss,
    and so the refusal lifts by a DATA change (the owner's three fields) and never by code.

    Ratifying is NECESSARY BUT NOT SUFFICIENT for live behaviour to change: the engine evaluates
    the DEPLOYED table, so the owner must also apply the rules to the live `.dmn` — which per
    ADR-0028 §7 is the owner's act.

    A ratified manifest whose `tabela_viva` binding no longer holds is refused too
    (`LiveTableBindingError`), and the binding is checked AFTER the ratification gate on purpose: an
    unratified manifest binds nothing, so reporting a digest problem first would say the wrong thing
    about which gate is actually shut.
    """
    status = load_candidate_ratification(manifest_path)
    if not status.ratificado:
        raise EnforcementNotRatifiedError(
            f"{manifest_path.name} is NOT ratified — enforcement refused. Ratify the three fields "
            f"({RATIFIED_FLAG}/{RATIFIED_BY}/{RATIFIED_AT}) and apply the rules to the live table; "
            "per ADR-0028 the live-table edit is the regulatory/clinical owner's act, never engineering's."
        )
    verify_live_table_binding(manifest_path)
    return evaluate(read_candidate_table(manifest_path, live), values)
