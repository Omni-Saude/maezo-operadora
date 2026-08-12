"""Tests for `maezo.gateway.audit_anchor` — external audit-chain anchor writer (Onda 4, leg 1).

TEST DOCTRINE FOR THIS FILE — consequence-level, with named RED controls.

Every defense below is paired with the test that goes RED when the defense is neutered. The
mapping is stated once, here, so a reviewer can neuter any one of them and predict the failure:

  D1  Canonical serialization covers ALL seven checkpoint fields.
      NEUTER: drop (or rename, or reorder into a non-sorted emission of) any key in
      `AnchorCheckpoint.to_canonical_mapping()`.
      RED: `test_checkpoint_bytes_match_pinned_literal`, `test_checkpoint_root_matches_pinned
      _literal` (the pinned 292-byte literal / the pinned root no longer match),
      `test_canonical_mapping_keys_match_the_field_set`, and
      `test_every_checkpoint_field_changes_the_root[<field>]` for the dropped field — two
      checkpoints differing only in it now hash identically. DEMONSTRATED: dropping
      `"record_count"` from `to_canonical_mapping()` turned 9 tests red (see the commit message).
      That run also EXPOSED a vacuity in `test_every_checkpoint_field_changes_the_root`, which
      then compared against `_PINNED_ROOT` and so stayed green under the very mutation it exists
      to catch; it now recomputes its baseline. A canary that cannot fail is not a canary.

  D2  Canonicalization is byte-stable (sorted keys, no whitespace, ASCII).
      NEUTER: remove `sort_keys=True`, or `separators`, or `ensure_ascii`.
      RED: `test_checkpoint_bytes_match_pinned_literal` (the literal was computed out-of-band,
      by hand, in the exact target form — see its provenance comment).

  D3  The field SET itself cannot drift (no PHI-bearing field may be added silently).
      NEUTER: add a field to `AnchorCheckpoint`.
      RED: `test_checkpoint_field_set_is_pinned` (hardcoded set-equality, not derived).

  D4  Naive timestamps are refused.
      NEUTER: delete the `tzinfo is None` branch in `_require_utc`.
      RED: `test_naive_window_is_refused`.

  D5  The empty-chain/genesis consistency rule.
      NEUTER: delete the `(record_count == 0) != (head == GENESIS)` check.
      RED: `test_record_count_and_genesis_head_must_agree`.

  D6  The labeled fake signer is unmistakably synthetic AND verifies only its own labeled form.
      NEUTER: drop `FAKE_ANCHOR_SIGNATURE_PREFIX` from `sign`, or drop any of the three label
      checks from `verify`.
      RED: `test_fake_signature_carries_all_three_labels` / `test_verify_rejects_unlabeled_
      signature` / `test_verify_rejects_wrong_algorithm` / `test_verify_rejects_wrong_key_id`.

  D7  The WORM fake refuses an overwrite.
      NEUTER: delete the `path.exists()` guard, or change `"xb"` to `"wb"`.
      RED: `test_worm_store_refuses_overwrite` (the guard) and
      `test_worm_store_refuses_overwrite_even_without_the_exists_precheck` (the O_EXCL belt),
      which deletes the pre-check's effect by chmod-ing the file writable first.
      SCOPE: write-once is enforced at CREATION only. `0o444` is discretionary and buys nothing
      against the file's own owner — `test_worm_store_read_only_mode_is_not_integrity_against_the
      _owner` pins that LIMIT so the prose describing it cannot quietly overstate again.

  D8  Flag OFF => provably zero writes and zero seam calls.
      NEUTER: remove the `anchor_writes_enabled()` early return in `write_anchor`.
      RED: `test_disabled_writer_touches_no_seam_and_no_filesystem` (an exploding signer and an
      exploding store are injected AND the store root is probed for non-existence).

  D9  The writer is unreachable from the audit path (dark build).
      NEUTER: import `audit_anchor` from any module under `src/maezo/` OTHER than the one named
      importer, in ANY of the spellings enumerated in `_CAUGHT_IMPORT_SPELLINGS`.
      RED: `test_no_production_module_imports_the_anchor_writer` (hardcoded allowlist, one named
      entry — leg 2's `gateway/audit_anchor_verify.py`, itself dark and itself unimported), plus
      `test_import_fence_predicate_catches_every_spelling[<spelling>]` if the predicate itself is
      narrowed. HISTORY: the predicate originally read only `ImportFrom.module`, so
      `from maezo.gateway import audit_anchor` and `from . import audit_anchor` — where the module
      is a *name*, not the module path — passed straight through and the fence stayed GREEN on a
      fully live import. Found by external review re-running the D9 probe with that spelling. The
      spelling matrix and its per-spelling test exist so the fence can never again be exercised by
      only the one spelling a probe happened to pick. The dynamic routes it still cannot see
      (`importlib`, `__import__`, attribute access through a parent package) are DISCLOSED in
      `_UNCAUGHT_IMPORT_SPELLINGS` and compensated by the allowlist naming its importers ONE BY
      ONE (it was EMPTY through leg 1; leg 2 added exactly one), not by pretending.

  D10 The anchor envelope carries no free-form content (PHI posture).
      NEUTER: add any nested/free-form value to `build_envelope`.
      RED: `test_envelope_shape_is_pinned` (hardcoded key sets + scalar-only assertion).

  D11 No key escapes the store root — by SPELLING and by RESOLUTION.
      NEUTER (spelling): delete the `_SAFE_ANCHOR_KEY` check in `_resolve`.
      RED: `test_worm_store_refuses_unsafe_keys[...]`.
      NEUTER (resolution): delete the `is_relative_to` containment check in `_resolve`.
      RED: `test_worm_store_refuses_a_key_that_escapes_via_a_symlinked_intermediate_dir`,
      `test_worm_store_refuses_reading_through_a_symlinked_intermediate_dir`,
      `test_worm_store_refuses_a_final_component_symlink`. HISTORY: the grammar check alone was
      claimed as "no key escapes the root"; with `<root>/amh` pre-planted as a symlink, the legal
      key `amh/a.anchor.json` wrote OUTSIDE the root while every test stayed green. Found by
      external review. Its CONTROL is
      `test_worm_store_still_accepts_a_normal_nested_key_under_a_symlinked_root` — the check must
      not degenerate into "refuse everything", and it must survive a root that is itself reached
      through a symlink (macOS `tmp_path`).

  D12 Importing the anchor does not drag the Postgres driver into the process.
      NEUTER: hoist `from maezo.gateway.audit_postgres import schema_for_tenant` out of
      `AnchorCheckpoint.__post_init__` back to module scope.
      RED: `test_anchor_module_imports_only_pure_names_from_the_audit_modules` (the pin is
      per-SCOPE, so the name set alone cannot keep it green) and
      `test_importing_the_anchor_module_does_not_drag_in_asyncpg` (fresh interpreter: no
      `asyncpg`, no `audit_postgres`, and a marginal module budget over `maezo.gateway.audit`).
      HISTORY: the module-level import made `import maezo.gateway.audit_anchor` pull in `asyncpg`
      and ~310 modules — the exact coupling `gateway/__init__.py` lines 11-15 documents avoiding.
      Found by external review. Its CONTROL is
      `test_constructing_a_checkpoint_still_uses_the_real_schema_validator` — going lazy must not
      quietly become going to a DIFFERENT (locally re-implemented, drifting) rule.

The RED probes actually executed for this file — each defense mutated, watched go red, and
reverted — are tabulated in `docs/design/wave4-audit-anchor.md` §7 with their exact counts.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

import pytest

from maezo.gateway.audit import GENESIS_PREV_HASH, AuditRecord, AuditSink
from maezo.gateway.audit_anchor import (
    ANCHOR_ENABLED_ENV,
    ANCHOR_FORMAT,
    FAKE_ANCHOR_KEY_ID_PREFIX,
    FAKE_ANCHOR_SIGNATURE_ALGORITHM,
    FAKE_ANCHOR_SIGNATURE_PREFIX,
    FAKE_WORM_STORE_MARKER_FILENAME,
    REASON_DISABLED,
    REASON_WRITTEN,
    AnchorChainDiscontinuityError,
    AnchorCheckpoint,
    AnchorKeyError,
    AnchorSignature,
    AnchorSignerUnavailableError,
    AnchorStoreUnavailableError,
    AnchorWormViolationError,
    LabeledFakeKmsAnchorSigner,
    LabeledFakeWormAnchorStore,
    RefusingAnchorSigner,
    RefusingAnchorStore,
    anchor_key,
    anchor_writes_enabled,
    build_envelope,
    canonical_bytes,
    checkpoint_bytes,
    checkpoint_field_names,
    checkpoint_for_chain,
    checkpoint_root,
    resolve_anchor_signer,
    resolve_anchor_store,
    write_anchor,
)

# tests/unit/gateway/<file> -> parents[3] == repo root (same idiom as test_effect_enforcement.py).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_MAEZO = _REPO_ROOT / "src" / "maezo"


# =================================================================================================
# The pinned fixture. Every literal below was computed OUT-OF-BAND, before the module existed, by
# hand-typing the target canonical JSON string into a standalone interpreter — NOT by calling the
# functions under test. Reproduce verbatim with:
#
#   python - <<'PY'
#   import hashlib, hmac
#   canonical = (
#       '{"anchor_format":"maezo.audit-anchor.v1",'
#       '"chain_head_hash":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",'
#       '"chain_schema_version":"0005_audit_emit_dedup",'
#       '"record_count":3,'
#       '"tenant_id":"amh",'
#       '"window_end":"2026-01-02T03:04:05+00:00",'
#       '"window_start":"2026-01-01T00:00:00+00:00"}'
#   )
#   raw = canonical.encode("utf-8")
#   print(len(raw), hashlib.sha256(raw).hexdigest())
#   print(hmac.new(b"labeled-fake-anchor-secret-nao-vinculativo", raw, hashlib.sha256).hexdigest())
#   PY
#
#   292 a598ab16640fe0079239f210960ef9432cc6b80419d7528c5b059e7f4fa36836
#       546253dfb3aa30ddebd8f301c9826630c6b2f5b18b5d73c9c3e7ef121cca3ecb
# =================================================================================================

_FIXTURE_HEAD = "0123456789abcdef" * 4
_FIXTURE_SCHEMA_VERSION = "0005_audit_emit_dedup"
_FIXTURE_WINDOW_START = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
_FIXTURE_WINDOW_END = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

_PINNED_CANONICAL_BYTES = (
    b'{"anchor_format":"maezo.audit-anchor.v1",'
    b'"chain_head_hash":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",'
    b'"chain_schema_version":"0005_audit_emit_dedup",'
    b'"record_count":3,'
    b'"tenant_id":"amh",'
    b'"window_end":"2026-01-02T03:04:05+00:00",'
    b'"window_start":"2026-01-01T00:00:00+00:00"}'
)
_PINNED_ROOT = "a598ab16640fe0079239f210960ef9432cc6b80419d7528c5b059e7f4fa36836"
_PINNED_FAKE_HMAC = "546253dfb3aa30ddebd8f301c9826630c6b2f5b18b5d73c9c3e7ef121cca3ecb"
_FIXTURE_SECRET = b"labeled-fake-anchor-secret-nao-vinculativo"
_FIXTURE_KEY_LABEL = "leg1-fixture"
_PINNED_ANCHOR_KEY = f"amh/20260102T030405Z-{_PINNED_ROOT}.anchor.json"


def _fixture_checkpoint(**overrides: Any) -> AnchorCheckpoint:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "chain_head_hash": _FIXTURE_HEAD,
        "record_count": 3,
        "window_start": _FIXTURE_WINDOW_START,
        "window_end": _FIXTURE_WINDOW_END,
        "chain_schema_version": _FIXTURE_SCHEMA_VERSION,
    }
    base.update(overrides)
    return AnchorCheckpoint(**base)


def _fixture_signer() -> LabeledFakeKmsAnchorSigner:
    return LabeledFakeKmsAnchorSigner(key_label=_FIXTURE_KEY_LABEL, secret=_FIXTURE_SECRET)


class _ExplodingSigner:
    """Any call is a test failure — proves the disabled writer never reaches the signer."""

    def sign(self, payload: bytes) -> AnchorSignature:  # pragma: no cover - must never run
        raise AssertionError("signer was called while the anchor flag was OFF")


class _ExplodingStore:
    """Any call is a test failure — proves the disabled writer never reaches the store."""

    def put(self, key: str, payload: bytes) -> None:  # pragma: no cover - must never run
        raise AssertionError("store.put was called while the anchor flag was OFF")

    def get(self, key: str) -> bytes:  # pragma: no cover - must never run
        raise AssertionError("store.get was called while the anchor flag was OFF")

    def list_keys(self) -> tuple[str, ...]:  # pragma: no cover - must never run
        raise AssertionError("store.list_keys was called while the anchor flag was OFF")


# =================================================================================================
# D3 — the checkpoint field set is pinned (no silent PHI-bearing field)
# =================================================================================================


def test_checkpoint_field_set_is_pinned() -> None:
    """HARDCODED, not derived. Provenance: the seven fields named by the external audit §4
    ("last record hash, record count, time window and schema/version") plus the tenant the chain
    belongs to and the anchor format identifier. Set-equality, so BOTH an addition (a new field —
    the PHI risk) and a removal (dropping `record_count`) fail."""
    expected = frozenset(
        {
            "tenant_id",
            "chain_head_hash",
            "record_count",
            "window_start",
            "window_end",
            "chain_schema_version",
            "anchor_format",
        }
    )
    assert checkpoint_field_names() == expected


def test_canonical_mapping_keys_match_the_field_set() -> None:
    """The serialized mapping must not silently omit a field the dataclass declares (that is
    exactly the D1 neutering) nor invent one the dataclass does not have."""
    assert frozenset(_fixture_checkpoint().to_canonical_mapping()) == checkpoint_field_names()


# =================================================================================================
# D1/D2 — byte-exact canonicalization and root
# =================================================================================================


def test_checkpoint_bytes_match_pinned_literal() -> None:
    assert checkpoint_bytes(_fixture_checkpoint()) == _PINNED_CANONICAL_BYTES
    assert len(_PINNED_CANONICAL_BYTES) == 292


def test_checkpoint_root_matches_pinned_literal() -> None:
    assert checkpoint_root(_fixture_checkpoint()) == _PINNED_ROOT


def test_canonical_bytes_have_no_whitespace_and_are_ascii() -> None:
    raw = checkpoint_bytes(_fixture_checkpoint())
    assert b" " not in raw
    assert b"\n" not in raw
    raw.decode("ascii")  # raises if any byte is non-ASCII


def test_canonical_bytes_are_key_order_independent() -> None:
    """Insertion order must not leak into the signed bytes (that is what `sort_keys` buys)."""
    forward = canonical_bytes({"a": 1, "b": 2, "c": 3})
    reversed_insertion = canonical_bytes({"c": 3, "b": 2, "a": 1})
    assert forward == reversed_insertion == b'{"a":1,"b":2,"c":3}'


def test_canonical_bytes_refuse_unserializable_values() -> None:
    """No `default=str` fallback: an unstable repr must never be baked into a signed root."""
    from maezo.gateway.audit_anchor import AnchorSerializationError

    with pytest.raises(AnchorSerializationError, match="not canonically serializable"):
        canonical_bytes({"bad": object()})


def test_canonical_bytes_refuse_non_finite_numbers() -> None:
    from maezo.gateway.audit_anchor import AnchorSerializationError

    with pytest.raises(AnchorSerializationError):
        canonical_bytes({"bad": float("nan")})


@pytest.mark.parametrize(
    ("field_name", "mutated"),
    [
        ("tenant_id", "public"),
        ("chain_head_hash", "f" * 64),
        ("record_count", 4),
        ("window_start", datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)),
        ("window_end", datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC)),
        ("chain_schema_version", "0002_audit_chain"),
    ],
    ids=["tenant_id", "chain_head_hash", "record_count", "window_start", "window_end", "schema"],
)
def test_every_checkpoint_field_changes_the_root(field_name: str, mutated: Any) -> None:
    """EVERY attested field must be inside the root. A field that can be changed without moving
    the root is a field an attacker can edit under a still-valid signature.

    (`anchor_format` is excluded from the parametrization only because construction refuses any
    other value — its presence in the bytes is pinned by the byte-exact literal instead.)

    The baseline is RECOMPUTED, not `_PINNED_ROOT`. Comparing against the pinned literal made this
    test VACUOUS under exactly the mutation it exists to catch: dropping a key from
    `to_canonical_mapping()` moves the baseline root too, so `mutated != pinned` stayed true while
    the two checkpoints hashed identically. Found by running the D1 neutering (see the commit
    message) and noticing this test stayed green. Comparing two live roots cannot be fooled that
    way."""
    baseline = checkpoint_root(_fixture_checkpoint())
    assert checkpoint_root(_fixture_checkpoint(**{field_name: mutated})) != baseline


def test_utc_normalization_makes_the_root_a_function_of_the_instant() -> None:
    """The same instant expressed in a different offset must yield the SAME root — otherwise two
    honest writers in different zones would look like a chain divergence."""
    same_instant_other_offset = _FIXTURE_WINDOW_END.astimezone(timezone(timedelta(hours=-3)))
    assert same_instant_other_offset != _FIXTURE_WINDOW_END.replace(tzinfo=None)
    assert checkpoint_root(_fixture_checkpoint(window_end=same_instant_other_offset)) == _PINNED_ROOT


# =================================================================================================
# D4/D5 — fail-closed construction
# =================================================================================================


@pytest.mark.parametrize("field_name", ["window_start", "window_end"])
def test_naive_window_is_refused(field_name: str) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _fixture_checkpoint(**{field_name: datetime(2026, 1, 1, 12, 0, 0)})


def test_record_count_and_genesis_head_must_agree() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        _fixture_checkpoint(record_count=0)
    with pytest.raises(ValueError, match="inconsistent"):
        _fixture_checkpoint(record_count=3, chain_head_hash=GENESIS_PREV_HASH)


def test_empty_chain_anchors_the_genesis_sentinel() -> None:
    empty = _fixture_checkpoint(record_count=0, chain_head_hash=GENESIS_PREV_HASH)
    assert empty.chain_head_hash == GENESIS_PREV_HASH
    assert checkpoint_root(empty) != _PINNED_ROOT


@pytest.mark.parametrize(
    "bad_head",
    ["", "abc", "0" * 63, "0" * 65, "A" * 64, "z" * 64, "0123456789ABCDEF" * 4],
    ids=["empty", "short", "63", "65", "uppercase-A", "non-hex", "uppercase-hex"],
)
def test_chain_head_hash_must_be_64_lowercase_hex(bad_head: str) -> None:
    with pytest.raises(ValueError, match="64 lowercase hex"):
        _fixture_checkpoint(chain_head_hash=bad_head, record_count=3)


def test_tenant_id_is_validated_by_the_existing_schema_validator() -> None:
    """Reuses `audit_postgres.schema_for_tenant` — not a second, drifting copy of the rule."""
    with pytest.raises(ValueError, match="not a valid schema identifier"):
        _fixture_checkpoint(tenant_id="Amh; DROP TABLE audit_chain")


def test_negative_record_count_is_refused() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        _fixture_checkpoint(record_count=-1)


def test_bool_record_count_is_refused() -> None:
    """`True` is an `int` in Python; a boolean count would serialize as `true` and silently
    change the byte shape of the signed root."""
    with pytest.raises(ValueError, match="must be an int"):
        _fixture_checkpoint(record_count=True, chain_head_hash=_FIXTURE_HEAD)


def test_inverted_window_is_refused() -> None:
    with pytest.raises(ValueError, match="precedes window_start"):
        _fixture_checkpoint(window_end=_FIXTURE_WINDOW_START - timedelta(seconds=1))


def test_foreign_anchor_format_is_refused() -> None:
    with pytest.raises(ValueError, match="not the format this module writes"):
        _fixture_checkpoint(anchor_format="maezo.audit-anchor.v99")


def test_blank_chain_schema_version_is_refused() -> None:
    with pytest.raises(ValueError, match="non-empty identifier"):
        _fixture_checkpoint(chain_schema_version="   ")


# =================================================================================================
# checkpoint_for_chain — derivation from a REAL fixture chain (no DB)
# =================================================================================================


def _emit_fixture_chain(count: int = 3) -> tuple[AuditSink, list[AuditRecord]]:
    sink = AuditSink()
    records: list[AuditRecord] = []
    for index in range(count):
        record = AuditRecord(
            agent_id="anchor-fixture",
            tenant_id="amh",
            agent_version="1.0.0",
            action="fixture.acao",
            decision="ALLOW",
            details={"i": index},
            timestamp=datetime(2026, 3, 1, 12, index, 0, tzinfo=UTC),
        )
        sink.emit(record)
        records.append(record)
    return sink, records


def test_checkpoint_for_chain_pins_count_window_and_head() -> None:
    sink, records = _emit_fixture_chain()
    assert sink.verify_chain() is True  # the fixture really is a valid chain

    checkpoint = checkpoint_for_chain(records, tenant_id="amh", chain_schema_version=_FIXTURE_SCHEMA_VERSION)

    assert checkpoint.record_count == 3  # hardcoded: the fixture emits exactly three records
    assert checkpoint.window_start == datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    assert checkpoint.window_end == datetime(2026, 3, 1, 12, 2, 0, tzinfo=UTC)
    assert checkpoint.chain_head_hash == records[-1].record_hash


def test_fixture_chain_root_matches_an_independent_recomputation() -> None:
    """Recompute the root WITHOUT the module's canonicalizer — an inline stdlib `json.dumps` typed
    out here, so a bug in `canonical_bytes` cannot hide behind itself."""
    _, records = _emit_fixture_chain()
    checkpoint = checkpoint_for_chain(records, tenant_id="amh", chain_schema_version=_FIXTURE_SCHEMA_VERSION)
    independent = json.dumps(
        {
            "anchor_format": "maezo.audit-anchor.v1",
            "chain_head_hash": records[-1].record_hash,
            "chain_schema_version": "0005_audit_emit_dedup",
            "record_count": 3,
            "tenant_id": "amh",
            "window_end": "2026-03-01T12:02:00+00:00",
            "window_start": "2026-03-01T12:00:00+00:00",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert checkpoint_bytes(checkpoint) == independent
    assert checkpoint_root(checkpoint) == hashlib.sha256(independent).hexdigest()


def test_checkpoint_for_chain_window_survives_clock_skew() -> None:
    """Window is min/max, not first/last: a skewed replica must not produce an inverted window."""
    _, records = _emit_fixture_chain()
    records[1].timestamp = datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)  # earlier than records[0]
    checkpoint = checkpoint_for_chain(records, tenant_id="amh", chain_schema_version=_FIXTURE_SCHEMA_VERSION)
    assert checkpoint.window_start == datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)
    assert checkpoint.window_end == datetime(2026, 3, 1, 12, 2, 0, tzinfo=UTC)


def test_checkpoint_for_chain_refuses_a_non_contiguous_sequence() -> None:
    _, records = _emit_fixture_chain()
    with pytest.raises(AnchorChainDiscontinuityError, match="not one contiguous run"):
        checkpoint_for_chain(records[1:], tenant_id="amh", chain_schema_version=_FIXTURE_SCHEMA_VERSION)


def test_checkpoint_for_chain_refuses_a_broken_link() -> None:
    _, records = _emit_fixture_chain()
    records[2].prev_hash = "d" * 64
    with pytest.raises(AnchorChainDiscontinuityError, match="record 2"):
        checkpoint_for_chain(records, tenant_id="amh", chain_schema_version=_FIXTURE_SCHEMA_VERSION)


def test_checkpoint_for_chain_refuses_an_empty_sequence() -> None:
    with pytest.raises(AnchorChainDiscontinuityError, match="no time window to attest"):
        checkpoint_for_chain([], tenant_id="amh", chain_schema_version=_FIXTURE_SCHEMA_VERSION)


def test_checkpoint_for_chain_refuses_an_unemitted_record() -> None:
    _, records = _emit_fixture_chain(1)
    records[0].record_hash = None
    with pytest.raises(AnchorChainDiscontinuityError, match="no record_hash"):
        checkpoint_for_chain(records, tenant_id="amh", chain_schema_version=_FIXTURE_SCHEMA_VERSION)


# =================================================================================================
# D6 — the signer seam
# =================================================================================================


def test_fake_signature_carries_all_three_labels() -> None:
    """HARDCODED label literals with provenance: they mirror `ans_gateway.py`'s
    `MOCK_ANS_PROTOCOL_PREFIX` discipline — a synthetic artifact self-labels loudly enough that it
    can never be mistaken for the binding thing."""
    assert FAKE_ANCHOR_SIGNATURE_ALGORITHM == "LABELED-FAKE-HMAC-SHA256-NAO-VINCULATIVO"
    assert FAKE_ANCHOR_KEY_ID_PREFIX == "FAKE-KMS-NAO-VINCULATIVO:"
    assert FAKE_ANCHOR_SIGNATURE_PREFIX == "FAKE-SIG-NAO-VINCULATIVO:"

    signature = _fixture_signer().sign(_PINNED_CANONICAL_BYTES)
    assert signature.algorithm == FAKE_ANCHOR_SIGNATURE_ALGORITHM
    assert signature.key_id == f"{FAKE_ANCHOR_KEY_ID_PREFIX}{_FIXTURE_KEY_LABEL}"
    assert signature.value == f"{FAKE_ANCHOR_SIGNATURE_PREFIX}{_PINNED_FAKE_HMAC}"


def test_fake_signature_value_matches_the_pinned_hmac() -> None:
    """Out-of-band literal (see the provenance block at the top of this file)."""
    signature = _fixture_signer().sign(_PINNED_CANONICAL_BYTES)
    assert signature.value.removeprefix(FAKE_ANCHOR_SIGNATURE_PREFIX) == _PINNED_FAKE_HMAC


def test_signature_round_trip_verifies() -> None:
    signer = _fixture_signer()
    payload = checkpoint_bytes(_fixture_checkpoint())
    assert signer.verify(payload, signer.sign(payload)) is True


def test_verify_rejects_a_tampered_payload() -> None:
    signer = _fixture_signer()
    payload = checkpoint_bytes(_fixture_checkpoint())
    signature = signer.sign(payload)
    tampered = checkpoint_bytes(_fixture_checkpoint(record_count=4))
    assert signer.verify(tampered, signature) is False


def test_verify_rejects_a_signature_from_a_different_key() -> None:
    payload = checkpoint_bytes(_fixture_checkpoint())
    other = LabeledFakeKmsAnchorSigner(key_label=_FIXTURE_KEY_LABEL, secret=b"a-different-secret")
    assert _fixture_signer().verify(payload, other.sign(payload)) is False


def test_verify_rejects_a_signature_carrying_a_different_key_id() -> None:
    payload = checkpoint_bytes(_fixture_checkpoint())
    signer = _fixture_signer()
    foreign = LabeledFakeKmsAnchorSigner(key_label="other-label", secret=_FIXTURE_SECRET)
    assert signer.verify(payload, foreign.sign(payload)) is False


def test_verify_rejects_unlabeled_signature() -> None:
    """A fake stripped of its label must NOT verify — the fake refuses to be laundered."""
    payload = checkpoint_bytes(_fixture_checkpoint())
    signer = _fixture_signer()
    labeled = signer.sign(payload)
    unlabeled = AnchorSignature(
        algorithm=labeled.algorithm,
        key_id=labeled.key_id,
        value=labeled.value.removeprefix(FAKE_ANCHOR_SIGNATURE_PREFIX),
    )
    assert signer.verify(payload, unlabeled) is False


def test_verify_rejects_wrong_algorithm() -> None:
    payload = checkpoint_bytes(_fixture_checkpoint())
    signer = _fixture_signer()
    labeled = signer.sign(payload)
    disguised = AnchorSignature(algorithm="ECDSA_SHA_256", key_id=labeled.key_id, value=labeled.value)
    assert signer.verify(payload, disguised) is False


@pytest.mark.parametrize(
    "bad_label",
    [
        "arn:aws:kms:sa-east-1:123456789012:key/abcd",
        "KMS:prod-audit-anchor",
        "hsm:slot-3",
        "projects/maezo/locations/global/keyRings/audit",
    ],
    ids=["arn", "kms-uppercase", "hsm", "gcp-resource"],
)
def test_fake_signer_refuses_a_masquerading_key_label(bad_label: str) -> None:
    with pytest.raises(ValueError, match="reads as a reference to a real managed key"):
        LabeledFakeKmsAnchorSigner(key_label=bad_label, secret=_FIXTURE_SECRET)


@pytest.mark.parametrize(("label", "secret"), [("", _FIXTURE_SECRET), ("ok", b"")], ids=["label", "secret"])
def test_fake_signer_refuses_empty_construction(label: str, secret: bytes) -> None:
    with pytest.raises(ValueError):
        LabeledFakeKmsAnchorSigner(key_label=label, secret=secret)


def test_refusing_signer_never_fabricates_a_signature() -> None:
    with pytest.raises(AnchorSignerUnavailableError, match="KMS/HSM-held key"):
        RefusingAnchorSigner().sign(b"anything")


def test_resolve_anchor_signer_defaults_to_refusing() -> None:
    assert isinstance(resolve_anchor_signer(None), RefusingAnchorSigner)
    injected = _fixture_signer()
    assert resolve_anchor_signer(injected) is injected


# =================================================================================================
# D7 — the WORM store seam
# =================================================================================================


def test_worm_store_round_trips_and_lists(tmp_path: Path) -> None:
    store = LabeledFakeWormAnchorStore(tmp_path / "anchors")
    store.put("amh/a.anchor.json", b"first")
    store.put("amh/b.anchor.json", b"second")
    assert store.get("amh/a.anchor.json") == b"first"
    assert store.list_keys() == ("amh/a.anchor.json", "amh/b.anchor.json")


def test_worm_store_refuses_overwrite(tmp_path: Path) -> None:
    store = LabeledFakeWormAnchorStore(tmp_path / "anchors")
    store.put("amh/a.anchor.json", b"original")
    with pytest.raises(AnchorWormViolationError, match="already exists"):
        store.put("amh/a.anchor.json", b"forged")
    assert store.get("amh/a.anchor.json") == b"original"


def test_worm_store_refuses_overwrite_even_without_the_exists_precheck(tmp_path: Path) -> None:
    """Belt: `open(..., "xb")` is O_EXCL, so the refusal survives a neutered pre-check.

    Making the file writable first defeats the chmod defense specifically, isolating the O_EXCL
    one — the pre-check is still in front, so this pins that BOTH guards refuse rather than only
    one of them carrying the property."""
    store = LabeledFakeWormAnchorStore(tmp_path / "anchors")
    store.put("amh/a.anchor.json", b"original")
    (tmp_path / "anchors" / "amh" / "a.anchor.json").chmod(0o644)
    with pytest.raises(AnchorWormViolationError):
        store.put("amh/a.anchor.json", b"forged")
    assert store.get("amh/a.anchor.json") == b"original"


def test_worm_store_makes_files_read_only(tmp_path: Path) -> None:
    store = LabeledFakeWormAnchorStore(tmp_path / "anchors")
    store.put("amh/a.anchor.json", b"original")
    mode = (tmp_path / "anchors" / "amh" / "a.anchor.json").stat().st_mode
    assert stat.S_IMODE(mode) == 0o444


def test_worm_store_read_only_mode_is_not_integrity_against_the_owner(tmp_path: Path) -> None:
    """A DISCLOSURE PIN, deliberately inverted: it asserts the documented LIMIT, not a defense.

    `0o444` is a DISCRETIONARY mode. The file's owner — the same unprivileged UID that wrote it,
    i.e. this very test process, with no `root` and no escalation — can `chmod(0o644)`, rewrite the
    bytes, and `get()` hands back the forgery. The module docstring and
    `docs/design/wave4-audit-anchor.md` §5 previously said only that "a local `root` privileged
    actor can chmod/rm at will", which understated the reach by an entire privilege level; this
    test exists so that prose cannot drift back into the overstatement unnoticed.

    If a future change makes this class genuinely resist its own UID, THIS TEST GOES RED — and the
    correct response is to rewrite the disclosure upward, not to delete the test."""
    store = LabeledFakeWormAnchorStore(tmp_path / "anchors")
    store.put("amh/a.anchor.json", b"GENUINE")
    path = tmp_path / "anchors" / "amh" / "a.anchor.json"

    assert os.getuid() != 0, "this pin is only meaningful as an UNPRIVILEGED process"
    assert path.stat().st_uid == os.getuid()  # the writer owns it — that is the whole point
    assert stat.S_IMODE(path.stat().st_mode) == 0o444

    path.chmod(0o644)  # the OWNER, not root
    path.write_bytes(b"FORGED")

    assert store.get("amh/a.anchor.json") == b"FORGED"


def test_worm_store_self_labels_its_root(tmp_path: Path) -> None:
    store = LabeledFakeWormAnchorStore(tmp_path / "anchors")
    store.put("amh/a.anchor.json", b"original")
    marker = tmp_path / "anchors" / FAKE_WORM_STORE_MARKER_FILENAME
    assert marker.is_file()
    assert "LABELED FAKE" in marker.read_text(encoding="utf-8")
    assert "NOT evidence" in marker.read_text(encoding="utf-8")
    assert FAKE_WORM_STORE_MARKER_FILENAME not in store.list_keys()


@pytest.mark.parametrize(
    "bad_key",
    ["", "/etc/passwd", "../escape.json", "amh/../../escape.json", "amh\\a.json", ".hidden", "amh//a"],
    ids=["empty", "absolute", "traversal", "nested-traversal", "backslash", "dotfile", "empty-segment"],
)
def test_worm_store_refuses_unsafe_keys(tmp_path: Path, bad_key: str) -> None:
    store = LabeledFakeWormAnchorStore(tmp_path / "anchors")
    with pytest.raises(AnchorKeyError, match="outside the safe grammar"):
        store.put(bad_key, b"payload")
    assert not (tmp_path / "anchors").exists()


def test_worm_store_refuses_a_key_that_escapes_via_a_symlinked_intermediate_dir(
    tmp_path: Path,
) -> None:
    """CONSEQUENCE probe, not a grammar probe. `amh/a.anchor.json` is a perfectly LEGAL key — it
    passes `_SAFE_ANCHOR_KEY` character by character. With `<root>/amh` pre-planted as a symlink
    to a directory outside the root, the write used to land outside the store entirely while the
    docstring claimed "no key escapes the root". The refusal must therefore come from RESOLUTION,
    which is the only check that can see a symlink at all."""
    root = tmp_path / "anchors"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "amh").symlink_to(outside, target_is_directory=True)

    store = LabeledFakeWormAnchorStore(root)
    with pytest.raises(AnchorKeyError, match="outside the store root"):
        store.put("amh/a.anchor.json", b"ESCAPED-PAYLOAD")

    # The consequence, probed on the filesystem rather than on the exception: nothing was written
    # anywhere — not outside, and not (via the symlink) inside.
    assert list(outside.iterdir()) == []
    assert store.list_keys() == ()


def test_worm_store_refuses_reading_through_a_symlinked_intermediate_dir(tmp_path: Path) -> None:
    """The same escape read BACKWARDS: a planted file outside the root must not become readable
    as if it were a stored anchor. Otherwise leg 2's comparison job could be fed an envelope the
    store never wrote."""
    root = tmp_path / "anchors"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "a.anchor.json").write_bytes(b"PLANTED-FROM-OUTSIDE")
    (root / "amh").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AnchorKeyError, match="outside the store root"):
        LabeledFakeWormAnchorStore(root).get("amh/a.anchor.json")


def test_worm_store_refuses_a_final_component_symlink(tmp_path: Path) -> None:
    """The narrower case: the KEY's own file is the symlink. `O_CREAT|O_EXCL` would refuse this on
    its own, but the containment check gets there first and says why."""
    root = tmp_path / "anchors"
    outside = tmp_path / "outside"
    root.mkdir()
    (outside).mkdir()
    (root / "amh").mkdir()
    (root / "amh" / "a.anchor.json").symlink_to(outside / "target.json")

    with pytest.raises(AnchorKeyError, match="outside the store root"):
        LabeledFakeWormAnchorStore(root).put("amh/a.anchor.json", b"ESCAPED-PAYLOAD")
    assert not (outside / "target.json").exists()


def test_worm_store_still_accepts_a_normal_nested_key_under_a_symlinked_root(tmp_path: Path) -> None:
    """CONTROL for the containment check — the refusal must not be "refuse everything".

    Two things at once: an ordinary nested key round-trips, AND it does so when the ROOT ITSELF is
    reached through a symlink. That second half is the false-positive the naive spelling of this
    check produces, and it is not hypothetical: on macOS `tmp_path` already lives under
    `/var -> /private/var`, so resolving only the candidate path would refuse every write in this
    entire test file."""
    real_root = tmp_path / "real-anchors"
    real_root.mkdir()
    linked_root = tmp_path / "linked-anchors"
    linked_root.symlink_to(real_root, target_is_directory=True)

    store = LabeledFakeWormAnchorStore(linked_root)
    store.put("amh/deep/nested/a.anchor.json", b"payload")

    assert store.get("amh/deep/nested/a.anchor.json") == b"payload"
    assert store.list_keys() == ("amh/deep/nested/a.anchor.json",)
    assert (real_root / "amh" / "deep" / "nested" / "a.anchor.json").read_bytes() == b"payload"


def test_worm_store_construction_is_pure(tmp_path: Path) -> None:
    """Construction must create nothing — load-bearing for the dark build."""
    root = tmp_path / "anchors"
    LabeledFakeWormAnchorStore(root)
    assert not root.exists()


def test_worm_store_lists_nothing_before_any_write(tmp_path: Path) -> None:
    assert LabeledFakeWormAnchorStore(tmp_path / "anchors").list_keys() == ()


def test_refusing_store_never_writes(tmp_path: Path) -> None:
    store = RefusingAnchorStore()
    with pytest.raises(AnchorStoreUnavailableError, match="retention-locked"):
        store.put("amh/a.anchor.json", b"payload")
    with pytest.raises(AnchorStoreUnavailableError):
        store.get("amh/a.anchor.json")
    with pytest.raises(AnchorStoreUnavailableError):
        store.list_keys()
    assert list(tmp_path.iterdir()) == []


def test_resolve_anchor_store_defaults_to_refusing(tmp_path: Path) -> None:
    assert isinstance(resolve_anchor_store(None), RefusingAnchorStore)
    injected = LabeledFakeWormAnchorStore(tmp_path)
    assert resolve_anchor_store(injected) is injected


# =================================================================================================
# D8 — the flag: OFF by default, provably inert
# =================================================================================================


def test_flag_name_is_pinned() -> None:
    """HARDCODED. Provenance: the single Onda-4 dark-build switch declared in the module
    docstring. Any rename must be a deliberate, reviewed change, not a refactor side effect."""
    assert ANCHOR_ENABLED_ENV == "MAEZO_AUDIT_ANCHOR_ENABLED"


def test_flag_is_off_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ANCHOR_ENABLED_ENV, raising=False)
    assert anchor_writes_enabled() is False


@pytest.mark.parametrize("raw", ["", "   ", "0", "false", "no", "off", "maybe", "2", "enabled", "sim"])
def test_flag_is_off_for_non_truthy_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(ANCHOR_ENABLED_ENV, raw)
    assert anchor_writes_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on", "On"])
def test_flag_is_on_only_for_the_pinned_truthy_vocabulary(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """HARDCODED truthy vocabulary. Provenance: the same `_TRUTHY` set as
    `runtime/agent_runtime/a2a_composition.py` — {"1", "true", "yes", "on"}, compared after
    strip+lower — so an operator does not have to remember a per-flag dialect."""
    monkeypatch.setenv(ANCHOR_ENABLED_ENV, raw)
    assert anchor_writes_enabled() is True


def test_disabled_writer_touches_no_seam_and_no_filesystem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """THE inertness proof. Exploding seams prove no call; the filesystem probe proves no write.

    Both halves matter: a writer that skipped the store but still called the signer would leak an
    anchor signature into KMS audit logs (and cost money) on every run of a "disabled" feature."""
    monkeypatch.delenv(ANCHOR_ENABLED_ENV, raising=False)
    root = tmp_path / "anchors"

    outcome = write_anchor(_fixture_checkpoint(), signer=_ExplodingSigner(), store=_ExplodingStore())

    assert outcome.written is False
    assert outcome.reason == REASON_DISABLED
    assert outcome.anchor_key is None
    assert outcome.root is None
    assert not root.exists()
    assert list(tmp_path.iterdir()) == []


def test_disabled_writer_creates_no_file_even_with_a_real_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same proof against the REAL labeled-fake store: probe filesystem state, not a mock's
    call count."""
    monkeypatch.delenv(ANCHOR_ENABLED_ENV, raising=False)
    root = tmp_path / "anchors"
    store = LabeledFakeWormAnchorStore(root)

    assert write_anchor(_fixture_checkpoint(), signer=_fixture_signer(), store=store).written is False
    assert not root.exists()
    assert store.list_keys() == ()


def test_disabled_writer_is_inert_even_with_no_seams_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the flag off the refusing defaults are never even resolved — no exception escapes."""
    monkeypatch.delenv(ANCHOR_ENABLED_ENV, raising=False)
    assert write_anchor(_fixture_checkpoint()).written is False


def test_enabled_writer_without_a_signer_refuses_rather_than_fabricating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Flipping the flag alone is NOT activation: the real KMS key and the real WORM bucket are
    owner wiring, and their absence must be loud."""
    monkeypatch.setenv(ANCHOR_ENABLED_ENV, "1")
    root = tmp_path / "anchors"
    with pytest.raises(AnchorSignerUnavailableError):
        write_anchor(_fixture_checkpoint(), store=LabeledFakeWormAnchorStore(root))
    assert not root.exists()


def test_enabled_writer_without_a_store_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ANCHOR_ENABLED_ENV, "1")
    with pytest.raises(AnchorStoreUnavailableError):
        write_anchor(_fixture_checkpoint(), signer=_fixture_signer())


# =================================================================================================
# The enabled path (exercised only under an explicitly set flag) + D10 envelope shape
# =================================================================================================


def test_enabled_writer_seals_a_verifiable_anchor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ANCHOR_ENABLED_ENV, "true")
    store = LabeledFakeWormAnchorStore(tmp_path / "anchors")
    signer = _fixture_signer()

    outcome = write_anchor(_fixture_checkpoint(), signer=signer, store=store)

    assert outcome.written is True
    assert outcome.reason == REASON_WRITTEN
    assert outcome.root == _PINNED_ROOT
    assert outcome.anchor_key == _PINNED_ANCHOR_KEY
    assert store.list_keys() == (_PINNED_ANCHOR_KEY,)

    envelope = json.loads(store.get(_PINNED_ANCHOR_KEY))
    assert envelope["root"] == _PINNED_ROOT
    assert envelope["signature"]["value"] == f"{FAKE_ANCHOR_SIGNATURE_PREFIX}{_PINNED_FAKE_HMAC}"
    # An independent verifier recomputes the preimage from the STORED checkpoint, then checks the
    # signature over it — exactly what leg 2's comparison job will do.
    replayed = canonical_bytes(envelope["checkpoint"])
    assert hashlib.sha256(replayed).hexdigest() == envelope["root"]
    assert (
        signer.verify(
            replayed,
            AnchorSignature(
                algorithm=envelope["signature"]["algorithm"],
                key_id=envelope["signature"]["key_id"],
                value=envelope["signature"]["value"],
            ),
        )
        is True
    )


def test_re_anchoring_an_identical_checkpoint_is_refused_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The root is inside the key, so a duplicate anchor collides — a no-op that must be loud."""
    monkeypatch.setenv(ANCHOR_ENABLED_ENV, "1")
    store = LabeledFakeWormAnchorStore(tmp_path / "anchors")
    checkpoint = _fixture_checkpoint()
    assert write_anchor(checkpoint, signer=_fixture_signer(), store=store).written is True
    with pytest.raises(AnchorWormViolationError, match="write-once"):
        write_anchor(checkpoint, signer=_fixture_signer(), store=store)
    assert store.list_keys() == (_PINNED_ANCHOR_KEY,)


def test_anchor_key_is_pinned_and_chronologically_sortable() -> None:
    assert anchor_key(_fixture_checkpoint(), _PINNED_ROOT) == _PINNED_ANCHOR_KEY
    later = anchor_key(_fixture_checkpoint(window_end=datetime(2027, 1, 1, tzinfo=UTC)), "f" * 64)
    assert later > _PINNED_ANCHOR_KEY  # plain lexical order == chronological order


def test_envelope_shape_is_pinned() -> None:
    """HARDCODED key sets. Provenance: the four envelope keys declared by `build_envelope` and the
    seven checkpoint keys of the field-set pin above. Set-equality on BOTH levels, plus a
    scalar-only assertion — that is the PHI guarantee (no free-form field, no record content)."""
    checkpoint = _fixture_checkpoint()
    envelope = build_envelope(checkpoint, _PINNED_ROOT, _fixture_signer().sign(b"x"))

    assert frozenset(envelope) == frozenset({"anchor_format", "checkpoint", "root", "signature"})
    assert frozenset(envelope["signature"]) == frozenset({"algorithm", "key_id", "value"})
    assert frozenset(envelope["checkpoint"]) == checkpoint_field_names()
    assert envelope["anchor_format"] == ANCHOR_FORMAT

    scalars = [
        value for section in (envelope["checkpoint"], envelope["signature"]) for value in section.values()
    ]
    scalars.extend([envelope["anchor_format"], envelope["root"]])
    assert all(isinstance(value, str | int) for value in scalars)
    assert not any(isinstance(value, dict | list) for value in scalars)


def test_envelope_root_field_is_independent_of_the_signature() -> None:
    """Editing the stored `root` must break the recomputation check while the signature (which
    covers the CHECKPOINT bytes) still validates — the two checks are deliberately independent."""
    checkpoint = _fixture_checkpoint()
    signer = _fixture_signer()
    payload = checkpoint_bytes(checkpoint)
    envelope = build_envelope(checkpoint, "0" * 64, signer.sign(payload))

    assert envelope["root"] != checkpoint_root(checkpoint)
    assert (
        signer.verify(
            canonical_bytes(envelope["checkpoint"]),
            AnchorSignature(**envelope["signature"]),
        )
        is True
    )


# =================================================================================================
# D9 — the dark build: nothing in src/maezo reaches the anchor writer
# =================================================================================================


#: Every import spelling this fence catches, stated once so a reviewer can check the predicate
#: below against the list instead of re-deriving it from `ast` semantics. Each entry is exercised
#: by `test_import_fence_predicate_catches_every_spelling`, which parses the snippet and asserts
#: the predicate fires — so the matrix cannot rot away from the code that implements it.
_CAUGHT_IMPORT_SPELLINGS: Final[tuple[str, ...]] = (
    "from maezo.gateway.audit_anchor import write_anchor",  # absolute, module in `node.module`
    "from maezo.gateway.audit_anchor import write_anchor as wa",
    "from .audit_anchor import write_anchor",  # relative, module in `node.module`
    "import maezo.gateway.audit_anchor",  # plain import
    "import maezo.gateway.audit_anchor as aa",
    "from maezo.gateway import audit_anchor",  # module in `node.names` — MISSED before this fix
    "from maezo.gateway import audit_anchor as aa",
    "from . import audit_anchor",  # relative sibling — MISSED before this fix
    "from .. import gateway, audit_anchor",
)

#: Spellings this AST scan structurally CANNOT catch, disclosed rather than pretended away.
#: Compensated by the NAMED allowlist above (any importer at all is a reviewed diff) plus review:
#: none of these can appear without a human reading the line that spells them.
_UNCAUGHT_IMPORT_SPELLINGS: Final[tuple[str, ...]] = (
    "import maezo.gateway  # then maezo.gateway.audit_anchor.write_anchor(...)",
    "importlib.import_module('maezo.gateway.audit_anchor')",
    "__import__('maezo.gateway.audit_anchor')",
)


def _imports_the_anchor_module(node: ast.AST) -> bool:
    """True iff `node` is an import statement that binds `audit_anchor` (any spelling).

    TWO branches, because `audit_anchor` can appear on either side of an `ImportFrom`:

      - `node.module` — `from maezo.gateway.audit_anchor import X` / `from .audit_anchor import X`.
        Matched by suffix so every package path and the relative form are covered at once.
      - `node.names` — `from maezo.gateway import audit_anchor` / `from . import audit_anchor`.
        Here the MODULE being imported is a *name*, and `node.module` is the PACKAGE (or `None`
        for `from . import`). A predicate that only reads `node.module` misses this entirely — it
        did, and the fence was silently vacuous against it (see this file's D9 entry).

    The `node.names` branch matches on EXACT equality (not suffix), so it fires regardless of how
    the package path is spelled while staying narrow. It would false-positive on `from <unrelated>
    import audit_anchor`, i.e. some OTHER module also named `audit_anchor`; there is exactly one
    `audit_anchor` in the tree today (`src/maezo/gateway/audit_anchor.py`), so the ambiguity does
    not exist, and if a second one ever appears the failure mode is a FALSE ALARM on a fence whose
    allowlist is empty — loud and safe, never a silent miss.
    """
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").endswith("audit_anchor") or any(
            alias.name == "audit_anchor" for alias in node.names
        )
    if isinstance(node, ast.Import):
        return any(alias.name.endswith("audit_anchor") for alias in node.names)
    return False


@pytest.mark.parametrize("source", _CAUGHT_IMPORT_SPELLINGS)
def test_import_fence_predicate_catches_every_spelling(source: str) -> None:
    """The fence's predicate, exercised directly against each spelling in the matrix.

    Without this, the matrix above is a comment, and the fence test itself only ever sees the ONE
    spelling a probe happens to plant — which is precisely how the `node.names` blind spot
    survived."""
    tree = ast.parse(source)
    assert any(_imports_the_anchor_module(node) for node in ast.walk(tree)), source


@pytest.mark.parametrize(
    "source",
    ["from maezo.gateway.audit import AuditRecord", "import maezo.gateway.audit_postgres", "import ast"],
    ids=["sibling-audit", "sibling-audit-postgres", "stdlib"],
)
def test_import_fence_predicate_does_not_fire_on_unrelated_imports(source: str) -> None:
    """A fence that flags everything is a fence nobody keeps. Neighbouring audit modules — the
    most likely false-positive source — must stay clean."""
    tree = ast.parse(source)
    assert not any(_imports_the_anchor_module(node) for node in ast.walk(tree)), source


def test_no_production_module_imports_the_anchor_writer() -> None:
    """HARDCODED allowlist, ONE named entry. Provenance: Onda 4 leg 1 shipped the anchor writer
    merged, tested and INERT, with this list EMPTY; leg 2 landed the verification job that consumes
    it and declared itself here, by name (see below). The strongest inertness proof is not "the
    flag is off" but "the audit path cannot reach this code at all", and that is what this AST scan
    pins.

    SCOPE, stated honestly. This is a STATIC scan of import STATEMENTS; the spellings it catches
    are enumerated in `_CAUGHT_IMPORT_SPELLINGS` and each one is exercised by
    `test_import_fence_predicate_catches_every_spelling`. It structurally cannot catch dynamic
    reach — `importlib.import_module("maezo.gateway.audit_anchor")`, `__import__`, or attribute
    access through an already-imported parent package (`import maezo.gateway` then
    `maezo.gateway.audit_anchor.write_anchor(...)`, which only resolves if something else already
    imported the submodule). Those are listed in `_UNCAUGHT_IMPORT_SPELLINGS` and are OUT OF
    AST SCOPE by construction: a string-keyed import is not an import node. What compensates is
    not a cleverer predicate but the shape of the allowlist — it names its importers ONE BY ONE, so
    any code that reaches the anchor by any route is a reviewed diff.

    THE ALLOWLIST'S ONE ENTRY, and why it is not a widening. Leg 2 landed the module this writer
    exists FOR: `gateway/audit_anchor_verify.py`, the continuous Postgres↔anchor verification job.
    It reads a signature-verified anchor back out of the store and recomputes the tenant-chain root
    from the database, and it imports `audit_anchor` for the canonicalization contract
    (`canonical_bytes`), the checkpoint derivation (`checkpoint_for_chain`, `checkpoint_root`), the
    two seam Protocols and the labeled fakes — DELIBERATELY, rather than re-implementing any of
    them: a second canonicalizer would drift from the one the signatures were made over, and a
    drifted canonicalizer reports divergence on a chain that is fine.

    The entry does not weaken the inertness claim, because the importer is itself dark: its own
    sibling flag (`MAEZO_AUDIT_ANCHOR_VERIFY_ENABLED`) is OFF by default, and
    `test_no_production_module_imports_the_verify_job` (tests/unit/gateway/
    test_audit_anchor_verify.py) pins an EMPTY allowlist for IT — so the audit path still cannot
    reach the anchor writer by any static route; it can only reach a module nothing reaches.

    The list stays a NAMED allowlist rather than a prefix/pattern: the next importer must be typed
    out here too, in a diff a reviewer sees, never admitted by a rule that happens to match it."""
    allowed_importers: frozenset[str] = frozenset({"gateway/audit_anchor_verify.py"})

    importers: set[str] = set()
    for path in sorted(_SRC_MAEZO.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(_imports_the_anchor_module(node) for node in ast.walk(tree)):
            importers.add(str(path.relative_to(_SRC_MAEZO)))

    assert frozenset(importers) == allowed_importers


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """`id()` of every `ast.Constant` that IS a docstring — same technique as
    `scripts/ci/check_effect_chokepoint_fence.py::_docstring_constant_ids`. Prose is not code, so
    a rationale paragraph naming `audit_chain` must not read as a SQL literal."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def test_the_anchor_module_carries_no_chain_mutating_literal() -> None:
    """No SQL, no table name, no INSERT/UPDATE/DELETE anywhere in EXECUTABLE code — the anchor is
    strictly additive and never participates in an `audit_chain` write. Docstrings are excluded
    (prose is not a bypass, and it is not a statement either)."""
    tree = ast.parse((_SRC_MAEZO / "gateway" / "audit_anchor.py").read_text(encoding="utf-8"))
    docstrings = _docstring_constant_ids(tree)
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]
    forbidden = ("INSERT INTO", "UPDATE ", "DELETE FROM", "SELECT ", "audit_chain", "audit_emit_dedup")
    offenders = [(fragment, literal) for literal in literals for fragment in forbidden if fragment in literal]
    assert offenders == [], f"anchor module carries chain-mutating literals: {offenders}"


def _audit_imports_by_scope(tree: ast.AST) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """Split `from maezo.gateway.audit*` imports into (module-scope, function-scope) maps.

    The distinction is the whole point of the pin: WHICH names are imported is a coupling question,
    WHERE they are imported is a WEIGHT question, and only the second one decides whether importing
    the anchor drags `asyncpg` into the process. An import nested in any function/method is
    function-scope; everything else — including one inside `if TYPE_CHECKING:`, which costs nothing
    at runtime and is therefore never the weight problem — is module-scope.
    """
    function_scoped = {
        id(child)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for child in ast.walk(node)
        if isinstance(child, ast.ImportFrom)
    }
    module_level: dict[str, set[str]] = {}
    function_level: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not (node.module or "").startswith("maezo.gateway.audit"):
            continue
        bucket = function_level if id(node) in function_scoped else module_level
        bucket.setdefault(node.module or "", set()).update(alias.name for alias in node.names)
    return (
        {module: frozenset(names) for module, names in module_level.items()},
        {module: frozenset(names) for module, names in function_level.items()},
    )


def test_anchor_module_imports_only_pure_names_from_the_audit_modules() -> None:
    """HARDCODED, AT BOTH SCOPES. Provenance: the anchor may only READ the chain's vocabulary. Any
    new import from `audit`/`audit_postgres` — especially a sink or an emit path — is a diff a
    reviewer must see, because it would mean the anchor started participating in the audit write
    path.

    WHY TWO MAPS. `schema_for_tenant` is imported LAZILY, inside `AnchorCheckpoint.__post_init__`,
    because `audit_postgres` imports `asyncpg` at module scope: a module-level import here made
    `import maezo.gateway.audit_anchor` — a pure canonicalizer that opens no socket — pull in
    `asyncpg` and ~310 modules, re-creating exactly the coupling `gateway/__init__.py` lines 11-15
    documents avoiding. Pinning only the name set would let a future refactor hoist that import
    back to module scope with the pin still green. So the SCOPE is pinned too, and a regression of
    the lazy import goes RED here.

    `AuditRecord` sits in the module-level map because it is imported under `if TYPE_CHECKING:` —
    module-scope in the AST, zero weight at runtime. `GENESIS_PREV_HASH` is a genuine module-level
    import; `maezo.gateway.audit` is stdlib-only and carries no driver."""
    expected_module_level = {
        "maezo.gateway.audit": frozenset({"GENESIS_PREV_HASH", "AuditRecord"}),
    }
    expected_function_level = {
        "maezo.gateway.audit_postgres": frozenset({"schema_for_tenant"}),
    }

    tree = ast.parse((_SRC_MAEZO / "gateway" / "audit_anchor.py").read_text(encoding="utf-8"))
    module_level, function_level = _audit_imports_by_scope(tree)

    assert module_level == expected_module_level
    assert function_level == expected_function_level


#: Measured in a fresh interpreter: importing the anchor AFTER `maezo.gateway.audit` costs exactly
#: ONE new module (`maezo.gateway.audit_anchor` itself). Before the lazy import it cost 34 and
#: dragged `asyncpg` in. The budget is stated relative to `audit` on purpose — an ABSOLUTE module
#: count is dominated by `structlog`, a repo-wide dependency the anchor neither can nor should
#: avoid, so an absolute pin would be a dependency-bump tripwire rather than a coupling canary.
_ANCHOR_MARGINAL_MODULE_BUDGET: Final[int] = 5


def test_importing_the_anchor_module_does_not_drag_in_asyncpg() -> None:
    """The CONSEQUENCE of the lazy import, measured in a FRESH interpreter.

    A subprocess is not ceremony here: this very test file imports `audit_postgres` (and pytest
    imports a great deal more), so `asyncpg` is already in THIS process's `sys.modules` regardless
    of what the anchor does. Only a clean interpreter can answer the question at all.

    Three assertions, weakest to strongest. (1) No `asyncpg` — the driver the audit path needs and
    a pure canonicalizer does not. (2) No `maezo.gateway.audit_postgres` — the precise coupling,
    named directly, so the test keeps its meaning even if `audit_postgres` some day stops importing
    `asyncpg` at module scope. (3) The MARGINAL cost over `maezo.gateway.audit` stays within
    :data:`_ANCHOR_MARGINAL_MODULE_BUDGET`, which is what catches a re-hoisted import that happens
    to be cheap today and expensive tomorrow."""
    program = (
        "import sys\n"
        "base = len(sys.modules)\n"
        "import maezo.gateway.audit\n"
        "after_audit = len(sys.modules)\n"
        "import maezo.gateway.audit_anchor\n"
        "after_anchor = len(sys.modules)\n"
        "print(after_anchor - after_audit,\n"
        "      any(m == 'asyncpg' or m.startswith('asyncpg.') for m in sys.modules),\n"
        "      'maezo.gateway.audit_postgres' in sys.modules)\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, this interpreter
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(_REPO_ROOT),
    )
    marginal, asyncpg_present, audit_postgres_present = completed.stdout.split()

    assert asyncpg_present == "False", f"importing the anchor pulled in asyncpg: {completed.stdout!r}"
    assert audit_postgres_present == "False", (
        f"importing the anchor pulled in audit_postgres: {completed.stdout!r}"
    )
    assert int(marginal) <= _ANCHOR_MARGINAL_MODULE_BUDGET, (
        f"the anchor now costs {marginal} modules beyond `maezo.gateway.audit` (budget "
        f"{_ANCHOR_MARGINAL_MODULE_BUDGET}) — a module-level import was probably re-hoisted"
    )


def test_constructing_a_checkpoint_still_uses_the_real_schema_validator() -> None:
    """The lazy import must not have become a lazy DIFFERENT rule. Construction still refuses via
    `audit_postgres.schema_for_tenant`'s exact message — the same validator that guards the
    `SET search_path` / advisory-lock interpolation, not a second copy of the grammar."""
    from maezo.gateway.audit_postgres import schema_for_tenant

    with pytest.raises(ValueError, match="not a valid schema identifier"):
        schema_for_tenant("Amh; DROP TABLE audit_chain")
    with pytest.raises(ValueError, match="not a valid schema identifier"):
        _fixture_checkpoint(tenant_id="Amh; DROP TABLE audit_chain")
    assert _fixture_checkpoint(tenant_id="amh_tenant_2").tenant_id == "amh_tenant_2"
