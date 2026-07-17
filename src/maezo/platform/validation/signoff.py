"""Human sign-off gate for promoted process contracts (Track C2 / T2.2).

Ported into a real, fail-closed check from the loud stub that used to live in
`cli.py` (`validate_signoff()` — see T2.1's docstring there, which explicitly
warned "a green `make validate-signoff` today proves nothing").

Spec of record: `docs/sme-dispatch/README.md` ("Signoff artifact spec"),
cross-checked against `docs/sme-dispatch/tracker.md` (16-contract roster: 15
DRAFT + 1 FINAL). Neither file is owned or edited by this module or its
callers — both are read-only inputs.

Rule (fail-closed, no exceptions except the one below):

    Every contract under `docs/processes/contracts/*.md` carries a
    `**Status:** DRAFT|FINAL (vX.Y.Z)` line. A contract whose Status is FINAL
    MUST have `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml`
    on disk containing at least one well-formed record — matching the
    contract's *current* version exactly — with `verdict: approved`. Anything
    short of that (missing file, malformed YAML, wrong version, a
    `needs-changes` verdict at the current version, a missing/empty required
    field, a signoff filename that doesn't exactly case-match a real contract
    ID, or two signoff-directory files differing only by case) is a Finding
    and fails the gate. There is no code path in this module that reports a
    real problem and still lets the caller treat the run as passing.

The one narrow exception is the grandfather list at
`docs/processes/contracts/signoffs/retro-verification-pending.yaml` — see
`load_exceptions()` / `check_exceptions()` below. It is visible (every grant
emits a loud `Report.notice()`), frozen (this module never writes to it), and
cross-checked against `docs/sme-dispatch/tracker.md` on every run — it is not
a general allowlist, and growing it is an orchestrator decision, not
something this code (or the human editing the YAML) can quietly expand
without also updating `tracker.md` to match.

No agent — this module included — ever creates, edits, or infers a signoff
record. Signoff is an exclusively human act (constraint: no self-certification).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

from ._loaders import ParseError, load_yaml
from .result import Report

SIGNOFF_SUFFIX = ".signoff.yaml"
SIGNOFFS_DIRNAME = "signoffs"
EXCEPTIONS_FILENAME = "retro-verification-pending.yaml"

REQUIRED_FIELDS = (
    "reviewer_name",
    "role",
    "date",
    "contract_version_reviewed",
    "verdict",
    "notes",
)
VALID_VERDICTS = frozenset({"approved", "needs-changes"})
VALID_ROLES = frozenset({"medico-auditor", "juridico", "dpo", "regulatorio", "financas", "po"})

_STATUS_RE = re.compile(r"\*\*Status:\*\*\s*(DRAFT|FINAL)\s*\(v([0-9]+(?:\.[0-9]+)*)\)")
_VERSION_RE = re.compile(r"^v[0-9]+(?:\.[0-9]+)*$")

# How much context (characters) around a contract-ID mention in tracker.md counts as
# "the same discussion" when cross-checking a retro-verification flag.
_TRACKER_CONTEXT_WINDOW = 400


@dataclass(frozen=True, slots=True)
class ContractStatus:
    """A contract's Status line, as parsed from its `.md` file."""

    contract_id: str
    path: Path
    status: str  # "DRAFT" | "FINAL"
    version: str  # e.g. "v1.0.0"


# ---------------------------------------------------------------------------
# Contract discovery
# ---------------------------------------------------------------------------


def parse_contract_status(path: Path, report: Report) -> ContractStatus | None:
    """Parse the `**Status:** DRAFT|FINAL (vX.Y.Z)` line out of a contract file.

    Returns None (with a Finding recorded) if the file can't be read or the
    line can't be found — an unclassifiable contract can never be silently
    treated as "doesn't need a signoff".
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        report.error(path, f"could not read contract file: {exc}")
        return None

    match = _STATUS_RE.search(text)
    if match is None:
        report.error(
            path,
            "could not find a '**Status:** DRAFT|FINAL (vX.Y.Z)' line — cannot determine "
            "whether this contract requires a sign-off (fail-closed: unclassifiable is a "
            "finding, not a skip)",
        )
        return None

    status, version_digits = match.group(1), match.group(2)
    return ContractStatus(
        contract_id=path.stem,
        path=path,
        status=status,
        version=f"v{version_digits}",
    )


def discover_contracts(contracts_dir: Path, report: Report) -> dict[str, ContractStatus]:
    """Parse every `*.md` contract in `contracts_dir` into a {contract_id: ContractStatus} map."""
    contracts: dict[str, ContractStatus] = {}
    for path in sorted(contracts_dir.glob("*.md")):
        status = parse_contract_status(path, report)
        if status is not None:
            contracts[status.contract_id] = status
    return contracts


# ---------------------------------------------------------------------------
# Signoff-file parsing / schema validation
# ---------------------------------------------------------------------------


def _validate_record(path: Path, index: int, record: object, report: Report) -> dict[str, str] | None:
    """Validate a single signoff record. Returns a normalized {field: str} dict, or None."""
    if not isinstance(record, dict):
        report.error(path, f"record #{index} is not a mapping")
        return None

    out: dict[str, str] = {}
    complete = True
    for field_name in REQUIRED_FIELDS:
        value = record.get(field_name)
        if field_name == "date":
            valid = isinstance(value, (str, _date)) and str(value).strip() != ""
        else:
            valid = isinstance(value, str) and value.strip() != ""
        if not valid:
            reviewer = record.get("reviewer_name", "?")
            report.error(
                path,
                f"record #{index} (reviewer_name={reviewer!r}) is missing or has an empty "
                f"required field '{field_name}'",
            )
            complete = False
            continue
        out[field_name] = str(value).strip() if isinstance(value, str) else str(value)

    if not complete:
        return None

    if out["verdict"] not in VALID_VERDICTS:
        report.error(
            path,
            f"record #{index}: invalid verdict {out['verdict']!r} (expected one of {sorted(VALID_VERDICTS)})",
        )
        return None

    if out["role"] not in VALID_ROLES:
        report.error(
            path,
            f"record #{index}: invalid role {out['role']!r} (expected one of {sorted(VALID_ROLES)})",
        )
        return None

    if not _VERSION_RE.match(out["contract_version_reviewed"]):
        report.error(
            path,
            f"record #{index}: contract_version_reviewed {out['contract_version_reviewed']!r} is "
            "not a version string shaped like 'v1.0.0'",
        )
        return None

    return out


def load_signoff_records(path: Path, report: Report) -> list[dict[str, str]] | None:
    """Parse + schema-validate a `*.signoff.yaml` file.

    Returns the list of well-formed records (per README's "list of records, one per
    reviewer per round" spec), or None if the file itself is unusable (malformed YAML,
    wrong root shape, empty, or every record invalid) — always with at least one
    Finding recorded first.
    """
    try:
        data = load_yaml(path)
    except ParseError as exc:
        report.error(path, f"malformed signoff file: {exc}")
        return None

    if not isinstance(data, list):
        report.error(
            path,
            "root must be a YAML list of signoff records, one per reviewer per round "
            "(see docs/sme-dispatch/README.md 'Signoff artifact spec')",
        )
        return None

    if not data:
        report.error(path, "signoff file has zero records — an empty file is not evidence of sign-off")
        return None

    records: list[dict[str, str]] = []
    for i, raw in enumerate(data, start=1):
        validated = _validate_record(path, i, raw, report)
        if validated is not None:
            records.append(validated)

    return records or None


# ---------------------------------------------------------------------------
# signoffs/ directory listing + case-collision guard
# ---------------------------------------------------------------------------


def _list_signoff_dir(signoffs_dir: Path) -> list[Path]:
    if not signoffs_dir.is_dir():
        return []
    return sorted(p for p in signoffs_dir.iterdir() if p.is_file())


def _flag_case_duplicates(entries: list[Path], report: Report) -> set[str]:
    """Report any set of >=2 files whose names differ only by case.

    Returns the set of lower-cased names involved in a collision, so callers can
    skip further processing of those ambiguous files (fail-closed: an ambiguous
    signoff can never count as a valid one).
    """
    groups: dict[str, list[Path]] = {}
    for p in entries:
        groups.setdefault(p.name.lower(), []).append(p)

    ambiguous: set[str] = set()
    for lower_name, paths in sorted(groups.items()):
        if len(paths) > 1:
            ambiguous.add(lower_name)
            names = ", ".join(str(p) for p in paths)
            report.error(
                paths[0].parent,
                f"duplicate signoff-directory entries differing only in case: {names} — "
                "ambiguous on case-sensitive CI filesystems; keep exactly one",
            )
    return ambiguous


def _match_contract_id(
    stem: str, contracts: dict[str, ContractStatus], path: Path, report: Report
) -> ContractStatus | None:
    """Resolve a signoff filename stem to a known contract, fail-closed on any mismatch."""
    if stem in contracts:
        return contracts[stem]

    ci_matches = [cid for cid in contracts if cid.lower() == stem.lower()]
    if ci_matches:
        report.error(
            path,
            f"signoff filename case must exactly match the contract id {ci_matches[0]!r} (found {stem!r})",
        )
        return None

    report.error(path, f"signoff file references a nonexistent contract {stem!r}")
    return None


# ---------------------------------------------------------------------------
# Grandfather exception list (retro-verification-pending.yaml)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExceptionEntry:
    contract_id: str
    path: Path
    justification: str
    tracker_ref: str


def load_exceptions(path: Path | None, report: Report) -> dict[str, ExceptionEntry]:
    """Parse `retro-verification-pending.yaml` into {contract_id: ExceptionEntry}.

    Absence of the file is not an error here — it just means zero exceptions are
    granted (every FINAL contract then needs a real signoff). Malformed content IS
    an error (fail-closed): a broken exception list can never silently grant an
    exception.
    """
    if path is None or not path.is_file():
        return {}

    try:
        data = load_yaml(path)
    except ParseError as exc:
        report.error(path, f"malformed exceptions file: {exc}")
        return {}

    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        report.error(
            path,
            "exceptions file must be a mapping with an 'entries' list "
            "(see docs/processes/contracts/signoffs/retro-verification-pending.yaml header)",
        )
        return {}

    result: dict[str, ExceptionEntry] = {}
    for i, raw in enumerate(data["entries"], start=1):
        if not isinstance(raw, dict):
            report.error(path, f"exceptions entry #{i} is not a mapping")
            continue

        contract_id = raw.get("contract_id")
        justification = raw.get("justification")
        tracker_ref = raw.get("tracker_ref")

        if not (isinstance(contract_id, str) and contract_id.strip()):
            report.error(path, f"exceptions entry #{i} is missing/empty 'contract_id'")
            continue
        if not (isinstance(justification, str) and justification.strip()):
            report.error(path, f"exceptions entry #{i} ({contract_id}) is missing/empty 'justification'")
            continue
        if not (isinstance(tracker_ref, str) and "tracker.md" in tracker_ref):
            report.error(
                path,
                f"exceptions entry #{i} ({contract_id}) has a missing/invalid 'tracker_ref' "
                "(must point at docs/sme-dispatch/tracker.md)",
            )
            continue
        if contract_id in result:
            report.error(path, f"exceptions entry {contract_id!r} is listed more than once")
            continue

        result[contract_id] = ExceptionEntry(
            contract_id=contract_id,
            path=path,
            justification=justification.strip(),
            tracker_ref=tracker_ref.strip(),
        )

    return result


def _flagged_in_tracker(contract_id: str, tracker_text: str) -> bool:
    """True iff `contract_id` and "retro-verif..." appear near each other in tracker.md."""
    lowered = tracker_text.lower()
    needle = contract_id.lower()
    start = 0
    while True:
        idx = lowered.find(needle, start)
        if idx == -1:
            return False
        window_start = max(0, idx - _TRACKER_CONTEXT_WINDOW)
        window_end = min(len(lowered), idx + len(needle) + _TRACKER_CONTEXT_WINDOW)
        if "retro-verif" in lowered[window_start:window_end]:
            return True
        start = idx + len(needle)


def check_exceptions(
    exceptions: dict[str, ExceptionEntry],
    contracts: dict[str, ContractStatus],
    signoffs_by_contract: dict[str, tuple[Path, list[dict[str, str]]]],
    tracker_path: Path,
    report: Report,
) -> set[str]:
    """Validate every grandfather-exception entry; return the set of contract IDs granted.

    Each grant is loud: a `Report.notice()` is emitted (never silent — the exception
    is visible even on an otherwise-clean run). Any entry that is stale, forged, or
    unverifiable is a Finding, not a silent no-op — this is a shrink-only, auditable
    list, never a general allowlist.
    """
    granted: set[str] = set()
    if not exceptions:
        return granted

    tracker_text: str | None = None
    if tracker_path.is_file():
        try:
            tracker_text = tracker_path.read_text(encoding="utf-8")
        except OSError as exc:
            report.error(tracker_path, f"could not read tracker.md to cross-check exceptions: {exc}")
    else:
        report.error(
            tracker_path,
            "docs/sme-dispatch/tracker.md not found — cannot cross-check the grandfather "
            "exception list (fail-closed: no exception can be granted without it)",
        )

    for contract_id, entry in sorted(exceptions.items()):
        contract = contracts.get(contract_id)
        if contract is None:
            report.error(entry.path, f"exceptions list references a nonexistent contract {contract_id!r}")
            continue

        if contract.status != "FINAL":
            report.error(
                entry.path,
                f"exceptions list entry {contract_id!r} is not FINAL (currently {contract.status}) — "
                "the grandfather list only covers already-FINAL contracts; remove this entry",
            )
            continue

        if contract_id in signoffs_by_contract:
            signoff_path, _records = signoffs_by_contract[contract_id]
            report.error(
                entry.path,
                f"exceptions list entry {contract_id!r} now has a signoff file on record "
                f"({signoff_path}) — this grandfather exception is obsolete; remove this entry",
            )
            continue

        if tracker_text is None:
            report.error(
                entry.path,
                f"cannot verify {contract_id!r} is flagged for retro-verification in "
                "docs/sme-dispatch/tracker.md (tracker.md unreadable) — fail-closed",
            )
            continue

        if not _flagged_in_tracker(contract_id, tracker_text):
            report.error(
                entry.path,
                f"exceptions list entry {contract_id!r} is not flagged for retro-verification in "
                "docs/sme-dispatch/tracker.md — cross-check failed, exception not granted",
            )
            continue

        granted.add(contract_id)
        report.notice(
            f"::warning:: GRANDFATHERED SIGN-OFF EXCEPTION — {contract_id} is FINAL "
            f"({contract.version}) with NO human signoff file on record. Allowed ONLY because it "
            f"is explicitly listed in docs/processes/contracts/signoffs/{EXCEPTIONS_FILENAME} as "
            "pre-existing debt from before this gate (T2.2) existed, and is cross-checked against "
            "docs/sme-dispatch/tracker.md on every run. This is NOT a general allowlist: it is "
            "visible, frozen, shrink-only, and adding a new entry requires an explicit orchestrator "
            "decision. It must still be resolved by human retro-verification producing a real "
            f"signoff file. Justification on file: {entry.justification}"
        )

    return granted


# ---------------------------------------------------------------------------
# Per-FINAL-contract check
# ---------------------------------------------------------------------------


def _check_final_signoff(
    contract: ContractStatus, signoff_path: Path, records: list[dict[str, str]], report: Report
) -> None:
    matching = [r for r in records if r["contract_version_reviewed"] == contract.version]
    if not matching:
        seen_versions = sorted({r["contract_version_reviewed"] for r in records})
        report.error(
            signoff_path,
            f"no signoff record for {contract.contract_id}'s current version {contract.version} "
            f"(file has record(s) for: {seen_versions}) — a stale-version signoff does not count "
            "toward FINAL promotion",
        )
        return

    blocking = [r for r in matching if r["verdict"] != "approved"]
    if blocking:
        roles = ", ".join(sorted({r["role"] for r in blocking}))
        report.error(
            signoff_path,
            f"{contract.contract_id} has a non-approved verdict at its current version "
            f"{contract.version} (role(s): {roles}) — a FINAL contract cannot carry an unresolved "
            "needs-changes verdict",
        )


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def validate_signoffs(contracts_dir: Path, tracker_path: Path, report: Report) -> None:
    """Run the whole sign-off gate against `contracts_dir` (+ its `signoffs/` subdir).

    Fail-closed throughout — see the module docstring for the exact rule and the
    one narrow, visible, auditable grandfather exception.
    """
    if not contracts_dir.is_dir():
        report.error(contracts_dir, "contracts directory does not exist")
        return

    contracts = discover_contracts(contracts_dir, report)
    if not contracts:
        report.error(contracts_dir, "no contract files (*.md) found — nothing to certify as passing")
        return

    signoffs_dir = contracts_dir / SIGNOFFS_DIRNAME
    entries = _list_signoff_dir(signoffs_dir)
    ambiguous = _flag_case_duplicates(entries, report)

    signoffs_by_contract: dict[str, tuple[Path, list[dict[str, str]]]] = {}
    exceptions_path: Path | None = None

    for path in entries:
        if path.name.lower() in ambiguous:
            continue  # already reported as a case collision; skip, never trust either copy
        if path.name == EXCEPTIONS_FILENAME:
            exceptions_path = path
            continue
        if not path.name.lower().endswith(SIGNOFF_SUFFIX):
            continue  # not a signoff file and not the exceptions file — out of scope

        stem = path.name[: -len(SIGNOFF_SUFFIX)]
        contract = _match_contract_id(stem, contracts, path, report)
        if contract is None:
            continue

        records = load_signoff_records(path, report)
        if records is None:
            continue

        signoffs_by_contract[contract.contract_id] = (path, records)

    exceptions = load_exceptions(exceptions_path, report)
    granted = check_exceptions(exceptions, contracts, signoffs_by_contract, tracker_path, report)

    for contract_id, contract in sorted(contracts.items()):
        if contract.status != "FINAL":
            continue
        if contract_id in granted:
            continue  # grandfathered — already loudly noticed above

        record_info = signoffs_by_contract.get(contract_id)
        if record_info is None:
            expected = signoffs_dir / f"{contract_id}{SIGNOFF_SUFFIX}"
            report.error(
                contract.path,
                f"FINAL contract has no signoff file at {expected} — promotion to FINAL is "
                "blocked without human sign-off (docs/sme-dispatch/README.md 'Signoff artifact "
                "spec')",
            )
            continue

        signoff_path, records = record_info
        _check_final_signoff(contract, signoff_path, records, report)
