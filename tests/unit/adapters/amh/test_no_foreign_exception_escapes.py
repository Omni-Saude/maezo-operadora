"""ONE invariant, proved two ways: nothing but `AmhAdapterError` leaves `maezo.adapters.amh`.

`maezo.ports.errors` states the rule without qualification — "No adapter exception may cross a port
boundary" — because a port implementation can only map what it can catch. A bare `TypeError`,
`ValueError`, `UnicodeEncodeError`, `RecursionError` or `RuntimeError` escaping this package does not
merely skip the closed `PortFailureReason` taxonomy: in phase B it wedges the consumer loop on ONE
poison delivery instead of quarantining it, turning fail-closed into fail-crash. A foreign exception
also carries its own MESSAGE across, which ADR-0037 immutable prohibition #5 forbids independently of
the type.

**Why this file exists rather than another handful of point regressions.** Two adversarial rounds
found the same defect six times between them, and the diagnosis was about METHOD, not about lines:
the guards had been placed at the call the author was thinking about instead of derived from "every
stdlib call that runs on this data". The sharpest illustration sat ONE LINE below a correct fix from
the previous round — `json.dumps` was guarded, and `encoded.encode("utf-8")` on the very next
statement was not, because the `try` closed early. Point regressions cannot stop that recurring; only
an exhaustiveness check can. So the invariant is proved twice, and the two halves fail for different
reasons on purpose:

* **Behavioural (`TestEveryEntryPointConverts`)** — a hostile-but-in-contract corpus is pushed through
  EVERY public entry point of both modules, at every injection site each one has, and the only
  exception type admitted is `AmhAdapterError`. This catches a missing conversion wherever the corpus
  reaches.
* **Structural (`TestNoEscapeProneCallIsUnguarded`)** — the AST of both modules is walked and every
  call to a primitive whose raise-set depends on attacker-chosen data must be lexically inside a `try`
  that has handlers, or carry a written justification in `_JUSTIFIED_UNGUARDED`. This catches a missing
  conversion the corpus does NOT reach, which is precisely the case the previous round shipped: no
  fixture in the suite happened to carry a lone surrogate, so nothing behavioural could have failed —
  but the AST check fails on sight, because the call sits outside the `try`.

Neither half is allowed to pass vacuously: both assert their own reach (`test_corpus_is_not_vacuous`,
`test_the_scan_finds_real_calls`, `test_every_watched_primitive_is_actually_present`,
`test_no_justification_is_stale`).
"""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import sys
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import pytest

from maezo.adapters.amh import contract as contract_mod
from maezo.adapters.amh import mapping as mapping_mod
from maezo.adapters.amh import wire_framing as wire_framing_mod
from maezo.adapters.amh.contract import (
    CONTRACT_PIN_RELATIVE_PATH,
    MAEZO_AMH_CONTRACT_PIN_ENV,
    AmhAdapterError,
    load_contract_pin,
    parse_semver,
    resolve_contract_pin_path,
)
from maezo.adapters.amh.mapping import (
    canonical_payload_hash,
    map_consent_event,
    map_outcome,
    map_work_item,
    millis_to_utc,
    outcome_to_wire,
    project_consent_decision,
    truncate_to_wire_millis,
    utc_to_millis,
)
from maezo.adapters.amh.wire_framing import select_wire_framing_codec
from maezo.ports.envelope import ENVELOPE_FIELD_ORDER

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[4]
FIXTURES: Final[Path] = REPO_ROOT / "tests/contract/amh/fixtures"
PIN_PATH: Final[Path] = REPO_ROOT / CONTRACT_PIN_RELATIVE_PATH


# ---------------------------------------------------------------------------
# The hostile corpus
# ---------------------------------------------------------------------------


class _RaisingTz(tzinfo):
    """A `tzinfo` whose `utcoffset` raises — and whose message must never cross the boundary."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        raise RuntimeError("PHI-SHAPED-SECRET")


class _UnimplementedTz(tzinfo):
    """A `tzinfo` that never overrode `utcoffset`; the stdlib base raises `NotImplementedError`."""


class _WrongTypeTz(tzinfo):
    """A `tzinfo` returning a non-`timedelta`; `astimezone` raises `TypeError`, not this call."""

    def utcoffset(self, dt: datetime | None) -> Any:
        return "PHI-SHAPED-SECRET"


class _OutOfRangeTz(tzinfo):
    """A `tzinfo` returning an offset outside ±24h; `astimezone` raises `ValueError`."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return timedelta(days=2)


def _deep_dict(depth: int) -> dict[str, Any]:
    """A dict nested `depth` levels. Built ONCE at import: 20k levels is not free to construct."""
    root: dict[str, Any] = {}
    current = root
    for _ in range(depth):
        nxt: dict[str, Any] = {}
        current["a"] = nxt
        current = nxt
    return root


def _circular() -> dict[str, Any]:
    node: dict[str, Any] = {}
    node["self"] = node
    return node


#: Digits enough to exceed CPython's `int(str)` conversion limit. Read from the interpreter rather
#: than hardcoded at 4300, because an embedder may lower `sys.int_max_str_digits` and the corpus must
#: still exceed whatever it is.
_OVER_INT_LIMIT: Final[str] = "1" * (sys.get_int_max_str_digits() + 1)

#: Values that are hostile but IN CONTRACT: each is something a conforming Avro/JSON decoder can hand
#: this adapter, or something a caller can legally put in an unvalidated port dataclass. Deliberately
#: NOT in here: values that violate the declared parameter TYPES (an `int` where the signature says
#: `Mapping`), because refusing those is mypy's job at every call site and a runtime check for each
#: would be unbounded. Each entry names the primitive it is aimed at, so a reader can see the corpus
#: is derived from the call inventory rather than from imagination.
_HOSTILE_ATOMS: Final[tuple[tuple[str, Any], ...]] = (
    # --- int(str) conversion limit -----------------------------------------------------------
    ("digits over int limit", _OVER_INT_LIMIT),
    ("semver major over int limit", f"{_OVER_INT_LIMIT}.0.0"),
    ("topic-shaped major over int limit", f"t.v{_OVER_INT_LIMIT}"),
    # --- str.encode / json text --------------------------------------------------------------
    ("lone high surrogate", "\ud800"),
    ("lone low surrogate", "\udfff"),
    ("surrogate mid-string", "a\ud800b"),
    ("NUL in string", "a\x00b"),
    ("astral plane", "\U0010ffff"),
    ("unicode decimal digits", "１.０.٠"),
    ("superscript digit", "².0.0"),
    ("empty string", ""),
    ("leading-zero semver", "01.0.0"),
    # --- numbers -----------------------------------------------------------------------------
    ("long max", 2**63 - 1),
    ("long min", -(2**63)),
    ("int far past datetime range", 10**5000),
    ("nan", float("nan")),
    ("inf", float("inf")),
    ("-inf", float("-inf")),
    ("bool", True),
    ("none", None),
    # --- types an Avro decode legitimately produces -------------------------------------------
    ("avro bytes", b"\xff\xfe"),
    ("avro decimal", Decimal("1.5")),
    ("avro timestamp-millis", datetime(2026, 8, 4, 10, 0, tzinfo=UTC)),
    ("naive datetime", datetime(2026, 8, 4, 10, 0)),
    ("set", {1, 2}),
    # --- json.dumps recursion / cycles --------------------------------------------------------
    ("dict nested past encoder depth", _deep_dict(20_000)),
    ("circular dict", _circular()),
    ("mixed-type key dict", {"a": 1, 2: "b"}),
    ("dict with surrogate value", {"k": "\ud800"}),
    ("dict with bytes value", {"k": b"\x00"}),
    # --- foreign tzinfo (the last disclosure channel) -----------------------------------------
    ("datetime with raising tzinfo", datetime(2026, 8, 4, 10, 0, tzinfo=_RaisingTz())),
    ("datetime with unimplemented tzinfo", datetime(2026, 8, 4, 10, 0, tzinfo=_UnimplementedTz())),
    ("datetime with wrong-type tzinfo", datetime(2026, 8, 4, 10, 0, tzinfo=_WrongTypeTz())),
    ("datetime with out-of-range tzinfo", datetime(2026, 8, 4, 10, 0, tzinfo=_OutOfRangeTz())),
    # --- datetime range edges (astimezone / epoch arithmetic overflow) -------------------------
    ("datetime.max at -14h", datetime.max.replace(tzinfo=timezone(timedelta(hours=-14)))),
    ("datetime.min at +14h", datetime.min.replace(tzinfo=timezone(timedelta(hours=14)))),
    ("datetime.min plus 500us UTC", datetime.min.replace(tzinfo=UTC) + timedelta(microseconds=500)),
    ("sub-millisecond instant", datetime(2026, 8, 4, 10, 0, 0, 1, tzinfo=UTC)),
)


def _is_json_writable(value: Any) -> bool:
    """Whether the HARNESS can write this atom into a pin file at all.

    Decided by trying, not by a type list: `10**5000` is a perfectly ordinary `int` that `json.dumps`
    nonetheless refuses (its decimal form exceeds `sys.int_max_str_digits` — the same limit the atom
    exists to probe), and a type-only filter let it through and failed the harness instead of the code
    under test. `allow_nan=False` matches the adapter's own spelling, so `NaN`/`inf` are excluded here
    and reach the mapping layer through `_HOSTILE_ATOMS` where they belong.
    """
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return False
    return True


#: The JSON-writable subset, for injection into a pin FILE. `bytes`/`Decimal`/`datetime`/`set`/a cycle
#: cannot be written to JSON at all, so the pin layer is attacked with these plus the raw-BYTES corpus
#: below — which is where the un-writable cases are covered.
_JSON_SAFE_ATOMS: Final[tuple[tuple[str, Any], ...]] = tuple(
    (label, value) for label, value in _HOSTILE_ATOMS if _is_json_writable(value)
)

#: Raw pin-FILE bytes that never reach a Python value at all — the `read_text`/`json.loads` layer.
_HOSTILE_PIN_BYTES: Final[tuple[tuple[str, bytes], ...]] = (
    ("invalid utf-8", b'{"a": "\xff\xfe"}'),
    ("utf-16 BOM'd", '{"a": 1}'.encode("utf-16")),
    ("nesting past json.loads budget", b"[" * 200_000 + b"]" * 200_000),
    ("integer literal over int limit", b'{"envelope": {"field_count": ' + _OVER_INT_LIMIT.encode() + b"}}"),
    ("truncated json", b'{"provenance": {'),
    ("top level is a list", b"[]"),
    ("top level is a bare int", b"1"),
    ("empty file", b""),
    ("lone surrogate escape", rb'{"provenance": {"status": "\ud800"}}'),
)

#: Dotted pin paths the loader actually reads. Injection targets for `_JSON_SAFE_ATOMS`.
_PIN_INJECTION_PATHS: Final[tuple[str, ...]] = (
    "provenance.status",
    "provenance.contract_name",
    "provenance.compatibility_mode",
    "provenance.canonical_schema_version",
    "provenance.amh_commit_sha",
    "provenance.evidence_id",
    "envelope.canonical_schema_version",
    "envelope.field_count",
    "envelope.payload_hash_canonicalization",
    "manifest_pin.sha256",
    "manifest_pin.path",
    "glue_registration.schema_version_status",
    "glue_registration.registry_name",
    "glue_registration.region",
    "glue_registration.environment",
    "compatibility_report.result",
    "xrg3_verification.verified_at_utc",
    "xrg3_verification.verified_by",
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pin() -> Any:
    return load_contract_pin(PIN_PATH)


def _fixture(name: str) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return parsed


#: Every observation the harness makes. A test asserts this is non-empty per entry point, so a
#: silently-skipped entry point cannot make the suite pass by doing nothing.
_OBSERVED: dict[str, int] = {}


def _assert_converts(entry_point: str, case: str, call: Callable[[], object]) -> None:
    """Call `call`; allow a return, allow `AmhAdapterError`, fail on anything else.

    The failure message names the entry point, the case and the escaping type, because the whole
    value of an exhaustiveness test is that a future failure points straight at the unguarded call.
    """
    _OBSERVED[entry_point] = _OBSERVED.get(entry_point, 0) + 1
    try:
        call()
    except AmhAdapterError as exc:
        # Non-disclosure rides along: the sentinel planted in `_RaisingTz`/`_WrongTypeTz` must not
        # reach the rendered refusal either. A converted exception that pasted the foreign message
        # into its own text would satisfy the TYPE rule and still leak.
        assert "PHI-SHAPED-SECRET" not in str(exc), f"{entry_point}[{case}] disclosed the foreign message"
    except Exception as exc:  # noqa: BLE001 - the assertion IS that this never happens
        pytest.fail(
            f"{entry_point}[{case}] leaked {type(exc).__name__} across the port boundary: {exc!r}\n"
            "maezo.ports.errors forbids ANY non-AmhAdapterError leaving this package — a port "
            "implementation cannot map what it cannot catch, and in phase B this wedges the consumer "
            "loop on one poison delivery instead of quarantining it. Convert it at the call, then add "
            "the case to this corpus."
        )


def _write_pin(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> Path:
    raw: dict[str, Any] = json.loads(PIN_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    out = tmp_path / "pin.json"
    out.write_text(json.dumps(raw), encoding="utf-8")
    return out


def _set_dotted(raw: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node: Any = raw
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return
        node = node[part]
    if isinstance(node, dict):
        node[parts[-1]] = value


# ---------------------------------------------------------------------------
# Behavioural half
# ---------------------------------------------------------------------------


class TestEveryEntryPointConverts:
    """The hostile corpus through every public entry point, at every injection site it has."""

    @pytest.mark.parametrize(("label", "atom"), _HOSTILE_ATOMS, ids=[a[0] for a in _HOSTILE_ATOMS])
    def test_canonical_payload_hash(self, label: str, atom: Any) -> None:
        _assert_converts(
            "canonical_payload_hash", f"wrapped={label}", lambda: canonical_payload_hash({"v": atom})
        )
        if isinstance(atom, Mapping):
            _assert_converts(
                "canonical_payload_hash", f"payload={label}", lambda: canonical_payload_hash(atom)
            )

    @pytest.mark.parametrize(("label", "atom"), _HOSTILE_ATOMS, ids=[a[0] for a in _HOSTILE_ATOMS])
    def test_timestamp_helpers(self, label: str, atom: Any) -> None:
        _assert_converts("millis_to_utc", label, lambda: millis_to_utc(atom, field="occurred_at"))
        # `utc_to_millis`/`truncate_to_wire_millis` are annotated `datetime`, but `CanonicalOutcome`
        # carries no runtime validation, so `outcome_to_wire` can reach them with anything at all.
        _assert_converts("utc_to_millis", label, lambda: utc_to_millis(atom, field="occurred_at"))
        _assert_converts("truncate_to_wire_millis", label, lambda: truncate_to_wire_millis(atom))

    @pytest.mark.parametrize(("label", "atom"), _HOSTILE_ATOMS, ids=[a[0] for a in _HOSTILE_ATOMS])
    def test_parse_semver_never_raises_at_all(self, label: str, atom: Any) -> None:
        """`parse_semver` is TOTAL, a strictly stronger claim than "converts": its docstring says a
        caller cannot leak an exception by using it, and the mapping layer was consolidated onto it
        partly on that promise. So `AmhAdapterError` is not acceptable here either."""
        assert parse_semver(atom) is None or isinstance(parse_semver(atom), tuple)

    @pytest.mark.parametrize(
        ("entry_point", "mapper", "fixture_name"),
        [
            ("map_work_item", map_work_item, "work_item.authorization_review.json"),
            ("map_consent_event", map_consent_event, "consent.granted.json"),
            ("map_outcome", map_outcome, "outcome.revision-1.json"),
        ],
    )
    def test_every_envelope_field_and_the_payload(
        self, entry_point: str, mapper: Any, fixture_name: str, pin: Any
    ) -> None:
        """Every one of the 28 envelope fields, `payload`, and each `source_position` sub-field, set to
        each hostile atom in turn. This is the cross-product the previous rounds' point fixes lacked."""
        base = _fixture(fixture_name)
        sites = [*ENVELOPE_FIELD_ORDER, "payload"]
        for label, atom in _HOSTILE_ATOMS:
            for site in sites:
                event = dict(base)
                event[site] = atom
                _assert_converts(entry_point, f"{site}={label}", lambda e=event: mapper(e, pin=pin))
            for sub in ("kind", "value", "transaction_ref"):
                event = dict(base)
                event["source_position"] = {**base["source_position"], sub: atom}
                _assert_converts(
                    entry_point, f"source_position.{sub}={label}", lambda e=event: mapper(e, pin=pin)
                )

    def test_project_consent_decision_over_every_body_field(self, pin: Any) -> None:
        base = _fixture("consent.granted.json")
        event = map_consent_event(base, pin=pin)
        for label, atom in _HOSTILE_ATOMS:
            for site in ("purpose", "decision", "consent_revision", "decided_at"):
                mutated = dataclasses.replace(event, payload={**dict(event.payload), site: atom})
                _assert_converts(
                    "project_consent_decision",
                    f"payload.{site}={label}",
                    lambda e=mutated: project_consent_decision(e),
                )
            _assert_converts(
                "project_consent_decision",
                f"whole payload={label}",
                lambda a=atom: project_consent_decision(dataclasses.replace(event, payload={"x": a})),
            )

    def test_outcome_to_wire_over_every_field(self, pin: Any) -> None:
        """Egress. `CanonicalOutcome` is a plain frozen dataclass with NO runtime validation, so every
        field is an injection site a caller can actually reach — including the timestamps, whose
        `.tzinfo` on a non-datetime was a bare `AttributeError`."""
        outcome = map_outcome(_fixture("outcome.revision-1.json"), pin=pin)
        for label, atom in _HOSTILE_ATOMS:
            for site in (*ENVELOPE_FIELD_ORDER, "payload"):
                if site == "source_position":
                    continue  # a 3-tuple value type; covered by its own sub-field cases on ingress
                mutated = dataclasses.replace(outcome, **{site: atom})
                _assert_converts(
                    "outcome_to_wire", f"{site}={label}", lambda o=mutated: outcome_to_wire(o, pin=pin)
                )

    @pytest.mark.parametrize(("label", "raw"), _HOSTILE_PIN_BYTES, ids=[b[0] for b in _HOSTILE_PIN_BYTES])
    def test_load_contract_pin_over_hostile_file_bytes(self, label: str, raw: bytes, tmp_path: Path) -> None:
        path = tmp_path / "pin.json"
        path.write_bytes(raw)
        _assert_converts("load_contract_pin", f"bytes={label}", lambda: load_contract_pin(path))

    def test_load_contract_pin_over_every_read_path(self, tmp_path: Path) -> None:
        for label, atom in _JSON_SAFE_ATOMS:
            for dotted in _PIN_INJECTION_PATHS:
                path = _write_pin(tmp_path, lambda r, d=dotted, a=atom: _set_dotted(r, d, a))
                _assert_converts(
                    "load_contract_pin", f"{dotted}={label}", lambda p=path: load_contract_pin(p)
                )

    def test_load_contract_pin_over_hostile_topic_entries(self, tmp_path: Path) -> None:
        for label, atom in _JSON_SAFE_ATOMS:
            for key in ("name", "quarantine", "direction", "schema_path", "major_version"):
                path = _write_pin(tmp_path, lambda r, k=key, a=atom: r["topics"][0].__setitem__(k, a))
                _assert_converts(
                    "load_contract_pin", f"topics[0].{key}={label}", lambda p=path: load_contract_pin(p)
                )

    def test_load_contract_pin_over_deep_nesting_json_loads_accepts(self, tmp_path: Path) -> None:
        """The gap that made the recursive placeholder walk unsafe: `json.loads` parses ~10x deeper
        than a Python-level walk of the SAME object survives, so every depth in this range is a file
        the parser accepts and the walker must not die on."""
        base = PIN_PATH.read_text(encoding="utf-8").rstrip().rstrip("}").rstrip()
        for depth in (900, 1_200, 5_000, 9_000, 9_900):
            nested = '{"a":' * depth + '"x"' + "}" * depth
            path = tmp_path / "pin.json"
            path.write_text(f'{base},\n"extra_nest": {nested}\n}}', encoding="utf-8")
            _assert_converts(
                "load_contract_pin", f"nesting depth={depth}", lambda p=path: load_contract_pin(p)
            )

    @pytest.mark.parametrize(
        "override",
        [
            "~nosuchuser4711/pin.json",
            "/tmp/" + "x" * 5_000 + ".json",
            "",
            "   ",
            "/dev/null/nope.json",
            "relative/pin.json",
        ],
    )
    def test_resolve_contract_pin_path_over_hostile_overrides(
        self, override: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`$MAEZO_AMH_CONTRACT_PIN` is operator input, and `expanduser`/`resolve`/`is_file` each have
        a platform-dependent raise set that no docstring enumerates."""
        monkeypatch.setenv(MAEZO_AMH_CONTRACT_PIN_ENV, override)
        _assert_converts(
            "resolve_contract_pin_path", f"override={override[:40]!r}", resolve_contract_pin_path
        )

    @pytest.mark.parametrize(
        ("label", "hostile_pin"),
        [("none", None), ("string", "not-a-pin"), ("empty dict", {}), ("list", []), ("int", 123)],
        ids=["none", "string", "empty-dict", "list", "int"],
    )
    def test_select_wire_framing_codec_over_hostile_pin_shapes(self, label: str, hostile_pin: Any) -> None:
        """`select_wire_framing_codec`'s own hostile corpus: a `pin` argument that is not the verified
        `AmhContractPin` `load_contract_pin` returns must convert, not raise a bare `AttributeError`
        from `pin.source_path` (`wire_framing.py`'s shape guard)."""
        _assert_converts(
            "select_wire_framing_codec",
            f"pin={label}",
            lambda p=hostile_pin: select_wire_framing_codec(p),
        )

    def test_select_wire_framing_codec_over_a_pin_with_a_hostile_source_path(self, pin: Any) -> None:
        """Same class of leak, narrower: a REAL `AmhContractPin` whose `source_path` has been
        corrupted to a non-`Path` — the other proven-leak shape the shape guard closes."""
        for label, bad_source_path in [
            ("string", "not-a-path"),
            ("none", None),
            ("int", 123),
            ("bytes", b"/tmp/x"),
        ]:
            mutated = dataclasses.replace(pin, source_path=bad_source_path)
            _assert_converts(
                "select_wire_framing_codec",
                f"source_path={label}",
                lambda m=mutated: select_wire_framing_codec(m),
            )

    def test_corpus_is_not_vacuous(self, request: pytest.FixtureRequest) -> None:
        """Every entry point above must have been exercised, and the corpus must be broad enough that
        no single-atom mistake shrinks it silently.

        Reads `_OBSERVED`, which the tests above accumulate, so it is only meaningful when the whole
        class ran — the state of the gate (`pytest tests/`) and of any full-file run. Under a partial
        `-k` selection it skips rather than failing on someone else's deselection.
        """
        selected = {item.nodeid.rsplit("::", 1)[-1] for item in request.session.items}
        siblings = {name for name in vars(type(self)) if name.startswith("test_")}
        if not siblings <= {n.split("[")[0] for n in selected}:
            pytest.skip("partial selection — the accumulated-reach assertion needs the full class")

        expected = {
            "canonical_payload_hash",
            "millis_to_utc",
            "utc_to_millis",
            "truncate_to_wire_millis",
            "map_work_item",
            "map_consent_event",
            "map_outcome",
            "project_consent_decision",
            "outcome_to_wire",
            "load_contract_pin",
            "resolve_contract_pin_path",
            "select_wire_framing_codec",
        }
        assert expected <= set(_OBSERVED), f"never exercised: {sorted(expected - set(_OBSERVED))}"
        assert len(_HOSTILE_ATOMS) >= 35, "the hostile corpus shrank — atoms are removed deliberately"
        assert len(_JSON_SAFE_ATOMS) >= 15
        # PER-DIMENSION floor, not just the global sum. A single global total cannot notice the loss
        # of an entire injection dimension: emptying `_PIN_INJECTION_PATHS` removes only 18x18=324
        # of ~5.5k observations, so the sum stays above its threshold and the pin-path dimension
        # disappears in silence. Found by mutating this file, not by reading it.
        assert len(_PIN_INJECTION_PATHS) >= 15, "the pin-injection dimension shrank"
        assert sum(_OBSERVED.values()) >= 5_000, _OBSERVED


def test_public_surface_is_covered_by_the_corpus() -> None:
    """The fuzz above enumerates entry points BY HAND, so a newly exported function could slip in
    uncovered. This closes that: every public name of both modules is either a callable the corpus
    drives, or a non-callable (constant / exception class / dataclass) with nothing to convert."""
    driven = {
        "canonical_payload_hash",
        "map_consent_event",
        "map_outcome",
        "map_work_item",
        "millis_to_utc",
        "outcome_to_wire",
        "project_consent_decision",
        "truncate_to_wire_millis",
        "utc_to_millis",
        "load_contract_pin",
        "parse_semver",
        "resolve_contract_pin_path",
        "select_wire_framing_codec",
    }
    exported_callables: set[str] = set()
    for module in (mapping_mod, contract_mod, wire_framing_mod):
        for name in module.__all__:
            obj = getattr(module, name)
            if callable(obj) and not isinstance(obj, type):
                exported_callables.add(name)
    assert exported_callables == driven, (
        f"public callables not driven by the hostile corpus: {sorted(exported_callables - driven)}; "
        f"stale corpus entries: {sorted(driven - exported_callables)}. A new public function on this "
        "boundary must be added to TestEveryEntryPointConverts in the same commit."
    )


# ---------------------------------------------------------------------------
# Structural half
# ---------------------------------------------------------------------------

#: Primitives whose raise-set depends on attacker-chosen DATA. Every one of these has either produced
#: an escape in this package or sits on the identical line of reasoning:
#:
#:   json.dumps      TypeError / ValueError / RecursionError / circular-reference ValueError
#:   json.loads      JSONDecodeError, plus RecursionError and a plain ValueError (int literal limit)
#:                   that JSONDecodeError does NOT cover
#:   hashlib.sha256  hashes bytes; kept in the set because it is the SECOND half of the statement the
#:                   previous round left outside the `try`
#:   int             ValueError past sys.int_max_str_digits — the NEW-2 root cause, in two functions
#:   encode          UnicodeEncodeError on an unpaired surrogate — the NEW-1 root cause, ONE LINE
#:                   below a correct guard
#:   astimezone      OverflowError near the datetime bounds, plus anything the tzinfo raises
#:   utcoffset       arbitrary foreign code; the last disclosure channel
#:   read_text       OSError AND UnicodeDecodeError, which is a ValueError and not an OSError
#:   expanduser      RuntimeError for an unresolvable ~user
#:   resolve         OSError(ENAMETOOLONG) / ValueError(embedded NUL)
#:   is_file         looks total, re-raises every errno outside its own small ignore list
#:   timedelta       OverflowError for a legal-on-the-wire `long`
#:   divmod          ZeroDivisionError
_ESCAPE_PRONE: Final[frozenset[str]] = frozenset(
    {
        "json.dumps",
        "json.loads",
        "hashlib.sha256",
        "int",
        "encode",
        "astimezone",
        "utcoffset",
        "read_text",
        "expanduser",
        "resolve",
        "is_file",
        "timedelta",
        "divmod",
    }
)

#: Non-vacuity: each of these MUST still appear in the scanned sources. Without it, a refactor that
#: renamed a call would quietly empty the rule and the test would keep passing on nothing.
_MUST_BE_PRESENT: Final[frozenset[str]] = frozenset(
    {"json.dumps", "json.loads", "hashlib.sha256", "int", "encode", "astimezone", "utcoffset", "read_text"}
)

#: `(module, enclosing function, callee)` -> why this call needs no `try`. An entry here is a CLAIM,
#: and `test_no_justification_is_stale` deletes the claim's cover the moment the call disappears. A new
#: escape-prone call that is neither guarded nor listed here fails the scan — which is the mechanism
#: that turns "derive the guards from every stdlib call on the path" from advice into a gate.
_JUSTIFIED_UNGUARDED: Final[Mapping[tuple[str, str, str], str]] = {
    ("contract", "parse_semver", "int"): (
        "The regex bounds every component to MAX_SEMVER_COMPONENT_DIGITS (9) digits, which is orders "
        "of magnitude below the smallest sys.int_max_str_digits CPython permits (640), so int() on a "
        "captured group cannot raise. This is WHY the bound is in the pattern rather than in a try: it "
        "makes the function's documented totality provable by inspection."
    ),
    ("contract", "_topic_major", "int"): (
        "Same bound, same reason: `\\.v(\\d{1,9})$` cannot capture a digit run int() would refuse."
    ),
    ("contract", "_default_pin_path_candidates", "resolve"): (
        "Resolves `__file__` — this module's own absolute path, fixed at install time and not "
        "attacker-influenced. The override path, which IS attacker-influenced, is guarded in "
        "resolve_contract_pin_path."
    ),
    ("mapping", "utc_to_millis", "divmod"): (
        "The divisor is the literal timedelta(milliseconds=1), so ZeroDivisionError — divmod's only "
        "exception on two timedeltas — is unreachable."
    ),
}

_MODULE_ALIASES: Final[frozenset[str]] = frozenset({"json", "hashlib", "re", "os", "sys", "Path"})


@dataclasses.dataclass(frozen=True)
class _CallSite:
    module: str
    function: str
    callee: str
    lineno: int
    guarded: bool

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.module, self.function, self.callee)

    def __str__(self) -> str:
        return f"{self.module}.py:{self.lineno} in {self.function}(): {self.callee}(...)"


def _callee_spelling(node: ast.expr) -> str | None:
    """`json.dumps`, `encode`, `int` — the shortest spelling the rule can key on."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in _MODULE_ALIASES:
            return f"{node.value.id}.{node.attr}"
        return node.attr
    return None


def _carries_data(call: ast.Call, callee: str) -> bool:
    """Whether this call can fail on DATA, as opposed to on compile-time constants only.

    `timedelta(milliseconds=1)` cannot overflow; `timedelta(milliseconds=value)` can. For a METHOD
    call the receiver carries the data (`encoded.encode("utf-8")` has none but constant arguments and
    is exactly the call that escaped), so a method on anything other than a module alias always counts.
    """
    if isinstance(call.func, ast.Attribute) and "." not in callee:
        return True  # a method on a non-module receiver: the receiver is the data
    args: list[ast.expr] = [*call.args, *(kw.value for kw in call.keywords)]
    return any(not isinstance(arg, ast.Constant) for arg in args)


def _walk_calls(node: ast.AST, module: str, function: str, in_try: bool) -> Iterator[_CallSite]:
    """Depth-first, carrying the enclosing function name and whether we are inside a `try` with
    handlers. `try/finally` alone does NOT count: `finally` converts nothing."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        function = node.name
        in_try = False  # a nested def's body does not run inside the enclosing try
    if isinstance(node, ast.Try):
        guards = bool(node.handlers)
        for child in node.body:
            yield from _walk_calls(child, module, function, in_try or guards)
        for child in [*node.handlers, *node.orelse, *node.finalbody]:
            yield from _walk_calls(child, module, function, in_try)
        return
    if isinstance(node, ast.Call):
        callee = _callee_spelling(node.func)
        if callee in _ESCAPE_PRONE and _carries_data(node, callee):
            yield _CallSite(module, function, callee, node.lineno, in_try)
    for child in ast.iter_child_nodes(node):
        yield from _walk_calls(child, module, function, in_try)


def _scan() -> list[_CallSite]:
    sites: list[_CallSite] = []
    for module in (contract_mod, mapping_mod, wire_framing_mod):
        path = Path(module.__file__ or "")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        sites.extend(_walk_calls(tree, path.stem, "<module>", False))
    return sites


class TestNoEscapeProneCallIsUnguarded:
    """The structural half — what would have caught the previous round's omission on sight."""

    def test_no_escape_prone_call_is_unguarded_and_unjustified(self) -> None:
        offenders = [site for site in _scan() if not site.guarded and site.key not in _JUSTIFIED_UNGUARDED]
        assert not offenders, (
            "escape-prone stdlib call(s) outside any `try` with handlers:\n"
            + "\n".join(f"  - {site}" for site in offenders)
            + "\n\nEvery one of these can raise on attacker-chosen data, and `maezo.ports.errors` "
            "forbids the result crossing a port boundary. Either wrap it in a `try` that converts to "
            "AmhAdapterError, or — if it provably cannot raise — add a (module, function, callee) "
            "entry to _JUSTIFIED_UNGUARDED stating WHY. This is the check that catches a guard whose "
            "`try` closed one line too early."
        )

    def test_the_scan_finds_real_calls(self) -> None:
        """Non-vacuity: a scanner that matched nothing would pass the rule above trivially."""
        sites = _scan()
        assert len(sites) >= 15, f"the AST scan found only {len(sites)} escape-prone calls: {sites}"
        assert any(site.guarded for site in sites), "no guarded call found — the scan is broken"
        assert {site.module for site in sites} == {"contract", "mapping", "wire_framing"}

    def test_every_watched_primitive_is_actually_present(self) -> None:
        """A stale entry in `_ESCAPE_PRONE` is a rule that guards nothing. Catch the drift here rather
        than discover it when the primitive comes back unguarded."""
        found = {site.callee for site in _scan()}
        assert found >= _MUST_BE_PRESENT, (
            f"watched but absent from the sources: {sorted(_MUST_BE_PRESENT - found)}"
        )

    def test_no_justification_is_stale(self) -> None:
        """A justification outlives its call only as dead cover for the next one added in that spot."""
        keys = {site.key for site in _scan() if not site.guarded}
        stale = set(_JUSTIFIED_UNGUARDED) - keys
        assert not stale, (
            f"_JUSTIFIED_UNGUARDED entries no longer match any unguarded call: {sorted(stale)}. Delete "
            "them: a stale justification silently excuses whatever is written there next."
        )

    def test_the_scanner_detects_a_synthetic_omission(self) -> None:
        """The scanner itself is tested — the failure mode of every AST fence is that it silently
        matches nothing. This is decision 11's exact shape: a guarded call, then an escape-prone call
        one line below, outside the `try`."""
        source = (
            "def f(payload):\n"
            "    try:\n"
            "        encoded = json.dumps(payload)\n"
            "    except TypeError:\n"
            "        raise AmhMappingError('payload', 'nope') from None\n"
            "    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()\n"
        )
        sites = list(_walk_calls(ast.parse(source), "synthetic", "<module>", False))
        by_callee = {site.callee: site for site in sites}
        assert by_callee["json.dumps"].guarded, "the guarded call must read as guarded"
        assert not by_callee["encode"].guarded, "the escaped `.encode` must read as UNGUARDED"
        assert not by_callee["hashlib.sha256"].guarded
        assert by_callee["encode"].function == "f"

    def test_a_finally_only_try_does_not_count_as_a_guard(self) -> None:
        """`try/finally` converts nothing, so it must not launder an unguarded call."""
        source = "def f(v):\n    try:\n        return int(v)\n    finally:\n        pass\n"
        sites = list(_walk_calls(ast.parse(source), "synthetic", "<module>", False))
        assert sites and not sites[0].guarded

    def test_a_nested_def_does_not_inherit_the_enclosing_try(self) -> None:
        """A closure defined inside a `try` runs when it is CALLED, not where it was written."""
        source = (
            "def outer(v):\n"
            "    try:\n"
            "        def inner():\n"
            "            return int(v)\n"
            "    except Exception:\n"
            "        pass\n"
            "    return inner\n"
        )
        sites = list(_walk_calls(ast.parse(source), "synthetic", "<module>", False))
        assert sites and not sites[0].guarded and sites[0].function == "inner"


def test_the_environment_matches_the_assumptions_the_bounds_rest_on() -> None:
    """`MAX_SEMVER_COMPONENT_DIGITS` is only a proof of totality while it stays below the interpreter's
    int-conversion limit. CPython refuses to set that limit below 640, so 9 is safe — but assert the
    relationship rather than trusting a comment."""
    assert sys.get_int_max_str_digits() > contract_mod.MAX_SEMVER_COMPONENT_DIGITS
    assert contract_mod.MAX_SEMVER_COMPONENT_DIGITS < 640

    # The OTHER proof this package rests on, asserted rather than left as a comment in the source:
    # `truncate_to_wire_millis` has NO underflow guard because `datetime.min` sits exactly ON the
    # epoch-millisecond grid, so a floor can never land below it. That is arithmetic on two
    # interpreter constants — but if it were ever false, the missing guard would become a live
    # OverflowError escape, so the premise is pinned here where a future reader can falsify it.
    epoch_to_min = datetime(1970, 1, 1, tzinfo=UTC) - datetime.min.replace(tzinfo=UTC)
    assert epoch_to_min == timedelta(days=719162)
    assert epoch_to_min % timedelta(milliseconds=1) == timedelta(0)
    assert datetime.min.microsecond == 0
    assert os.sep  # sanity: the platform is a real one
