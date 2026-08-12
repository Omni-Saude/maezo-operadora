"""External audit-chain anchor — signed checkpoint writer, INERT (Onda 4, ADR-0029 idiom).

=================================================================================================
THE THREAT THIS ADDRESSES
=================================================================================================
`audit.py` + `audit_postgres.py` already give the chain SHA-256 hash linking, a per-tenant
advisory lock as the primary write guard, and `UNIQUE(prev_record_hash)` as the belt-and-suspenders
anti-fork constraint. All three defenses live INSIDE the database. The external audit
(`docs/audits/architecturally safe, operationally incomplete.md` §4) names the one adversary they
structurally cannot stop: **a privileged actor who rewrites the whole database and recomputes the
chain**. Every in-DB invariant is satisfied by the forged chain, because the forger controls every
byte the verifiers read.

The remedy that audit prescribes, verbatim: periodically compute a signed tenant-chain root; anchor
those roots OUTSIDE the database (object storage with retention-lock/WORM, a separate security
account, or both); sign through KMS/HSM-held keys; include the last record hash, record count, time
window and schema/version; continuously compare Postgres against the latest external anchor; and
add restore/tamper drills. This module is the **writer** half of that: it computes and seals the
signed checkpoint. It performs no comparison and runs no drill.

=================================================================================================
WHAT THIS MODULE IS — AND IS NOT
=================================================================================================
IS: a pure canonicalization + root computation, two SEAMS (signer, store), fail-closed refusing
defaults for both, unmistakably-labeled synthetic implementations of both for dev/test, and one
flag-gated writer function.

IS NOT, deliberately:

  - **Not a fourth chain verifier.** `AuditSink.verify_chain()` (audit.py) and
    `audit_postgres.verify_chain()` recompute record hashes; this module never does. It attests
    *what the chain looked like*, not *whether the chain is internally consistent* — those are
    different questions, and the whole point of an external anchor is that the later COMPARISON
    (leg 2) is what turns a silent rewrite into a detected one.
  - **Not a mutation of the existing audit path.** Nothing in `audit.py` / `audit_postgres.py` is
    touched, imported-into, or wrapped. This module imports FROM them — `GENESIS_PREV_HASH` at
    module scope, `AuditRecord` under `TYPE_CHECKING` only, and `schema_for_tenant` LAZILY (see
    IMPORT WEIGHT below) — and is imported BY exactly ONE module in `src/maezo/`, declared by name
    in an otherwise-empty allowlist a test enforces by AST-scanning the tree
    (`test_audit_anchor.py`): `audit_anchor_verify`, the leg-2 verification job this writer exists
    for. That importer is itself dark — its own sibling flag is OFF by default and its own fence
    pins an EMPTY allowlist — so the audit path still cannot reach this file by any static route.
  - **Not a pruner.** ADR-0029 §2's checkpoint is an IN-`audit_chain` row that re-anchors a chain
    whose genesis prefix was deleted; it requires net-new columns, a migration, and verifier
    changes, and it is BLOCKED behind DPO ratification of ADR-0029 *and* the ADR-0020 legal-hold
    amendment. NOTHING here writes to `audit_chain`, adds a column, or enables any DELETE. What is
    borrowed from ADR-0029 is only its **idiom**: a checkpoint that commits to {head hash, count,
    time window, schema/version} under a signature, canonically serialized, treated as tamper when
    the signature fails.
  - **Not a real KMS/HSM integration, and not a real WORM bucket.** Both are deployment/owner
    concerns. The seams below are where the owner wires them; the shipped implementations are a
    REFUSAL (production default) and a LOUDLY LABELED FAKE (dev/test).

=================================================================================================
DARK-BUILD DOCTRINE — the flag
=================================================================================================
`MAEZO_AUDIT_ANCHOR_ENABLED` (:data:`ANCHOR_ENABLED_ENV`), default OFF. With the flag off,
:func:`write_anchor` returns `AnchorWriteOutcome(written=False, reason=REASON_DISABLED)` BEFORE it
canonicalizes anything, BEFORE it calls the signer, and BEFORE it touches the store — so there is
provably zero anchor I/O and zero added I/O on the audit path. Activation is a data/config change
(set the variable, wire a real signer and a real store), never a code change.

The default direction here is the OPPOSITE of the repo's security fences, and deliberately so: for
a fence, "absent config" must resolve to the RESTRICTIVE state; for a dark build, "absent config"
must resolve to the INERT state. Both readings share the same rule — the absence of an explicit
operator decision may never be read as consent.

=================================================================================================
IMPORT WEIGHT — why `schema_for_tenant` is imported lazily
=================================================================================================
`gateway/__init__.py`'s docstring (lines 11-15) states the house rule: `PostgresAuditSink` is NOT
re-exported from the package because `audit_postgres` imports `asyncpg` at module scope, and the
in-memory `AuditSink` path must not acquire an unconditional hard dependency on the Postgres
driver. A module-level `from maezo.gateway.audit_postgres import schema_for_tenant` HERE would
re-create exactly that coupling one file over: importing this module — a pure canonicalizer that
opens no socket — pulled in `asyncpg` and ~310 modules.

So the import lives INSIDE :meth:`AnchorCheckpoint.__post_init__` instead. The validator is still
the SAME one that guards the `SET search_path` / advisory-lock interpolation (a second copy of the
grammar would drift from the one that matters), it is simply paid for by callers who construct a
checkpoint rather than by everyone who imports the file. A test pins BOTH levels — module-scope
and function-scope — so regressing the lazy import back to module scope goes RED rather than
silently restoring the weight, and a second test asserts in a FRESH interpreter that importing
this module leaves `asyncpg` out of `sys.modules`.

=================================================================================================
CANONICALIZATION (the byte-stability contract)
=================================================================================================
A signature is only meaningful over bytes that every future verifier can reproduce exactly. The
canonical form is:

    json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
               allow_nan=False).encode("utf-8")

with NO trailing newline. Each choice, and why:

  - `sort_keys=True` — key order is otherwise insertion order, i.e. a property of the Python
    source, so a harmless field reordering would silently invalidate every prior signature.
  - `separators=(",", ":")` — no whitespace; `json.dumps`' default inserts `", "` / `": "`, which
    is stable but wastes bytes and invites a "cosmetic" reformat.
  - `ensure_ascii=True` — the byte stream is pure ASCII regardless of locale/filesystem encoding.
    Nothing in a checkpoint is non-ASCII today (hashes, integers, ISO timestamps, a tenant id
    validated to `[a-z][a-z0-9_]*`), so this costs nothing and removes an entire class of
    environment-dependent divergence.
  - `allow_nan=False` — `NaN`/`Infinity` are not JSON; the stdlib emits them anyway by default.
    Fail-closed instead.
  - **NO `default=str`.** This is the ONE deliberate divergence from `audit.hash_input()` /
    `AuditRecord._compute_hash()`, which both pass `default=str`. There, a lenient fallback is
    right: `details` is caller-supplied and the alternative is refusing to audit an effect that
    already happened. Here every field is a typed primitive owned by this module, so a value that
    `json` cannot encode is a BUG, and stringifying it would bake an unstable `repr` (`<object at
    0x7f...>`) into a signed root. It raises instead — see :class:`AnchorSerializationError`.
  - `datetime` → `.astimezone(UTC).isoformat()`, naive rejected. Same reasoning as
    `AuditRecord.__post_init__`: a naive timestamp's instant is ambiguous, and guessing a timezone
    would fabricate evidence. Normalizing to UTC first makes the string a function of the INSTANT,
    not of the writer's local zone.

No external prefix / domain-separation tag is prepended before signing: `anchor_format` is the
FIRST key of the canonical mapping (alphabetically) and is inside the signed bytes, so the format
identifier is already in-band. A future incompatible format bumps that constant, and old
signatures cannot be replayed as new-format ones.

=================================================================================================
PHI
=================================================================================================
An anchor carries hashes, counts, timestamps and version strings — nothing else. There is no
free-form field, no record content, no `details`, no subject identifier. `tenant_id` is the
Postgres schema name (`[a-z][a-z0-9_]*`, validated by the existing
`audit_postgres.schema_for_tenant`), which is organizational, not personal, data. A test pins the
checkpoint's field set by set-equality against a hardcoded literal so a PHI-bearing field cannot be
added silently.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

import structlog

from maezo.gateway.audit import GENESIS_PREV_HASH

# `schema_for_tenant` is imported LAZILY, inside `AnchorCheckpoint.__post_init__` — see the
# module docstring's "IMPORT WEIGHT" section. `maezo.gateway.audit_postgres` imports `asyncpg` at
# module scope, and pulling it in here would give every consumer of this module an unconditional
# hard dependency on the Postgres driver. That is exactly the coupling `gateway/__init__.py`'s
# docstring (lines 11-15) documents avoiding when it declines to re-export `PostgresAuditSink`;
# this module follows the same precedent rather than re-creating the problem one file over.

if TYPE_CHECKING:
    from collections.abc import Sequence

    from maezo.gateway.audit import AuditRecord

logger = structlog.get_logger(__name__)


# =================================================================================================
# The flag (dark build)
# =================================================================================================

#: The ONE switch. Absent / blank / anything outside :data:`_TRUTHY` => anchor writing is INERT.
#: Activation is a config change on a deployment that has ALSO wired a real signer and a real
#: store; flipping this alone still yields a refusal from `RefusingAnchorSigner` (§ Seams).
ANCHOR_ENABLED_ENV: Final[str] = "MAEZO_AUDIT_ANCHOR_ENABLED"

#: Same truthy vocabulary as `runtime/agent_runtime/a2a_composition.py`'s `_TRUTHY` — one
#: spelling set for opt-in switches across the repo, so an operator does not have to remember a
#: per-flag dialect. Compared after `.strip().lower()`.
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def anchor_writes_enabled() -> bool:
    """True iff an operator EXPLICITLY opted this deployment into external anchor writes."""
    return os.environ.get(ANCHOR_ENABLED_ENV, "").strip().lower() in _TRUTHY


# =================================================================================================
# Constants pinned into the signed bytes / the labeled fakes
# =================================================================================================

#: Format identifier of the anchor envelope AND of the checkpoint inside it. Lives inside the
#: signed canonical bytes (see the module docstring's canonicalization note). Bump on any
#: incompatible change to the field set or the serialization.
ANCHOR_FORMAT: Final[str] = "maezo.audit-anchor.v1"

#: Algorithm string the LABELED FAKE signer stamps on every signature. Deliberately NOT a real
#: algorithm name (`RSASSA_PSS_SHA_256`, `ECDSA_SHA_256`, `Ed25519`, ...): a reader of a stored
#: anchor must never have to reason about whether the blob came from an HSM.
FAKE_ANCHOR_SIGNATURE_ALGORITHM: Final[str] = "LABELED-FAKE-HMAC-SHA256-NAO-VINCULATIVO"

#: Prefix every labeled-fake key id carries. Mirrors `tools/workers/ans_gateway.py`'s
#: `MOCK_ANS_PROTOCOL_PREFIX` discipline — a synthetic artifact self-labels loudly enough that it
#: can never be mistaken for the binding thing.
FAKE_ANCHOR_KEY_ID_PREFIX: Final[str] = "FAKE-KMS-NAO-VINCULATIVO:"

#: Prefix every labeled-fake signature VALUE carries. The label travels with the blob itself, not
#: just with the metadata around it, so a signature copied out of an anchor file into any other
#: context is still self-evidently synthetic.
FAKE_ANCHOR_SIGNATURE_PREFIX: Final[str] = "FAKE-SIG-NAO-VINCULATIVO:"

#: Marker file the labeled-fake WORM store drops in its root on first write, so an operator who
#: finds the directory cannot mistake it for a real retention-locked bucket.
FAKE_WORM_STORE_MARKER_FILENAME: Final[str] = "LABELED-FAKE-WORM-STORE-NAO-VINCULATIVO.txt"

_FAKE_WORM_STORE_MARKER_BODY: Final[str] = (
    "This directory is a LABELED FAKE of a WORM/retention-locked anchor store\n"
    "(maezo.gateway.audit_anchor.LabeledFakeWormAnchorStore). It is a local filesystem\n"
    "simulation for dev/test only. It provides NO retention lock, NO immutability against a\n"
    "privileged local actor, and NO off-host durability. It is NOT evidence.\n"
)

#: `write_anchor` outcome reasons. Stable tokens (not prose) so a caller/test can branch on them.
REASON_DISABLED: Final[str] = "ANCHOR_DESABILITADO"
REASON_WRITTEN: Final[str] = "ANCHOR_GRAVADO"

#: 64 lowercase hex characters — the shape of every SHA-256 digest in the chain, including
#: `GENESIS_PREV_HASH` ("0" * 64). Uppercase is rejected on purpose: hex case is not normalized
#: anywhere in the chain, so accepting both spellings would let the SAME head produce two
#: different roots.
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

#: Conservative anchor-key grammar for the fake store: slash-separated segments, each starting
#: with an alphanumeric. `..` and absolute paths are excluded by construction (a leading `.` or
#: `/` cannot open a segment), as are backslashes, NUL, and anything non-ASCII.
_SAFE_ANCHOR_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$")

#: Substrings a labeled-fake key LABEL may not contain — they would let the resulting key id read
#: as a reference to a real managed key. Checked case-insensitively.
_MASQUERADING_LABEL_SUBSTRINGS: Final[tuple[str, ...]] = ("arn:", "kms:", "hsm:", "projects/")


# =================================================================================================
# Errors — all loud, none swallowed
# =================================================================================================


class AnchorError(RuntimeError):
    """Base class for every refusal this module raises."""


class AnchorSerializationError(AnchorError):
    """A checkpoint/envelope could not be canonicalized to stable bytes (fail-closed).

    Raised rather than falling back to `default=str`: an unstable `repr` inside signed bytes would
    produce a root nobody can reproduce, i.e. an anchor that fails verification for a reason
    indistinguishable from tamper.
    """


class AnchorChainDiscontinuityError(AnchorError):
    """The record sequence handed to :func:`checkpoint_for_chain` is not one contiguous run.

    NOT a chain verification (this module never recomputes record hashes — see the module
    docstring). It is a PRECONDITION of the computation: "the last record's hash" is only a
    meaningful chain head when the records actually link, genesis-first, one after another.
    """


class AnchorSignerUnavailableError(AnchorError):
    """Fail-closed refusal to sign — no real signing key/KMS transport is wired.

    The production default (:class:`RefusingAnchorSigner`) raises this unconditionally, so an
    unwired anchor seam REFUSES rather than emitting an unsigned or self-signed "anchor" that
    would look like evidence without being any.
    """


class AnchorStoreUnavailableError(AnchorError):
    """Fail-closed refusal to store — no real WORM/retention-locked store is wired."""


class AnchorWormViolationError(AnchorError):
    """An anchor key that already exists was written again — WORM says NO, loudly.

    This is the property a retention-locked bucket enforces server-side and the property a
    filesystem does NOT enforce on its own; the labeled fake enforces it explicitly so that tests
    (and any future drill) pin the REFUSAL, not merely the happy path.
    """


class AnchorKeyError(AnchorError):
    """An anchor key is unusable: outside the safe grammar, or it resolves outside the store root.

    Two distinct refusals share this type because they are the same property from the caller's
    side — "this key does not name a location inside this store":

      - SPELLING: path traversal, absolute paths, backslashes, empty segments (:data:`_SAFE_ANCHOR
        _KEY`). Rejected without touching the filesystem.
      - RESOLUTION: the key is spelled safely but a SYMLINK on the path makes it land elsewhere.
        The grammar cannot see this — `amh/a.anchor.json` is a perfectly legal key whether or not
        `<root>/amh` is a symlink to somewhere else on the host.
    """


# =================================================================================================
# The checkpoint
# =================================================================================================


@dataclass(frozen=True, slots=True)
class AnchorCheckpoint:
    """What one external anchor commits to, for exactly one tenant's chain.

    Every field named by the external audit §4 ("the last record hash, record count, time window
    and schema/version") plus the tenant the chain belongs to and the anchor format identifier.
    Frozen: an anchor is evidence; it is computed once and never edited.

    Attributes:
        tenant_id: Owning tenant (== the Postgres schema; validated by `schema_for_tenant`).
        chain_head_hash: `record_hash` of the newest record in the window — 64 lowercase hex.
            `GENESIS_PREV_HASH` iff `record_count == 0`.
        record_count: How many records the anchor attests to. `>= 0`.
        window_start: Oldest record timestamp covered (UTC, aware).
        window_end: Newest record timestamp covered (UTC, aware).
        chain_schema_version: The DB schema the head/count were observed under — the alembic
            revision of the tenant's `audit_chain` (e.g. `"0005_audit_emit_dedup"`). Without it, a
            root computed before a migration and one computed after are indistinguishable, and a
            schema change would read as a chain divergence.
        anchor_format: :data:`ANCHOR_FORMAT`. Kept as a FIELD, not merely a module constant, so it
            is inside the signed bytes and an old signature can never be replayed as a new-format
            one.

    Validation is fail-closed at construction, because an invalid checkpoint that reaches the
    signer produces a *signed* lie.
    """

    tenant_id: str
    chain_head_hash: str
    record_count: int
    window_start: datetime
    window_end: datetime
    chain_schema_version: str
    anchor_format: str = ANCHOR_FORMAT

    def __post_init__(self) -> None:
        # LAZY, not module-level: importing `audit_postgres` at module scope drags `asyncpg` (and
        # ~310 modules) into every process that so much as imports this file. Same reasoning, and
        # the same precedent, as `gateway/__init__.py` lines 11-15 declining to re-export
        # `PostgresAuditSink`. The rule this reuses is deliberately NOT re-implemented here —
        # a second copy of the schema grammar would drift from the one that actually guards the
        # `SET search_path` / advisory-lock interpolation.
        from maezo.gateway.audit_postgres import schema_for_tenant

        schema_for_tenant(self.tenant_id)  # raises ValueError for anything not [a-z][a-z0-9_]*

        if not _SHA256_HEX.match(self.chain_head_hash):
            raise ValueError(
                f"chain_head_hash {self.chain_head_hash!r} is not 64 lowercase hex characters "
                "(hex case is not normalized anywhere in the chain, so accepting both spellings "
                "would let one head produce two different roots)"
            )
        if not isinstance(self.record_count, int) or isinstance(self.record_count, bool):
            raise ValueError(f"record_count must be an int, got {type(self.record_count).__name__}")
        if self.record_count < 0:
            raise ValueError(f"record_count must be >= 0, got {self.record_count}")

        # An empty chain has no head; a non-empty chain's head is never the genesis sentinel.
        # Either mismatch is a caller bug that would otherwise be sealed under a signature.
        if (self.record_count == 0) != (self.chain_head_hash == GENESIS_PREV_HASH):
            raise ValueError(
                f"record_count={self.record_count} is inconsistent with "
                f"chain_head_hash={self.chain_head_hash!r}: an empty chain must anchor "
                "GENESIS_PREV_HASH, and a non-empty chain must not"
            )

        if not self.chain_schema_version.strip():
            raise ValueError("chain_schema_version must be a non-empty identifier")
        if self.anchor_format != ANCHOR_FORMAT:
            raise ValueError(
                f"anchor_format {self.anchor_format!r} is not the format this module writes "
                f"({ANCHOR_FORMAT!r})"
            )

        object.__setattr__(self, "window_start", _require_utc(self.window_start, "window_start"))
        object.__setattr__(self, "window_end", _require_utc(self.window_end, "window_end"))
        if self.window_end < self.window_start:
            raise ValueError(
                f"window_end ({self.window_end.isoformat()}) precedes window_start "
                f"({self.window_start.isoformat()})"
            )

    def to_canonical_mapping(self) -> dict[str, Any]:
        """The exact mapping that gets serialized, signed and stored. Primitives only."""
        return {
            "anchor_format": self.anchor_format,
            "chain_head_hash": self.chain_head_hash,
            "chain_schema_version": self.chain_schema_version,
            "record_count": self.record_count,
            "tenant_id": self.tenant_id,
            "window_end": self.window_end.isoformat(),
            "window_start": self.window_start.isoformat(),
        }


def _require_utc(value: datetime, field_name: str) -> datetime:
    """Normalize an aware datetime to UTC; reject naive ones (fail-closed)."""
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None:
        raise ValueError(
            f"{field_name} must be timezone-aware — a naive timestamp's instant is ambiguous, "
            "and guessing a zone here would fabricate the window an anchor attests to"
        )
    return value.astimezone(UTC)


def canonical_bytes(mapping: dict[str, Any]) -> bytes:
    """Byte-stable serialization of `mapping` (see the module docstring for every choice)."""
    try:
        text = json.dumps(
            mapping,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AnchorSerializationError(
            f"anchor payload is not canonically serializable ({exc}) — refusing to seal bytes "
            "no future verifier could reproduce"
        ) from exc
    return text.encode("utf-8")


def checkpoint_bytes(checkpoint: AnchorCheckpoint) -> bytes:
    """The canonical bytes of `checkpoint` — the exact preimage of the root AND of the signature."""
    return canonical_bytes(checkpoint.to_canonical_mapping())


def checkpoint_root(checkpoint: AnchorCheckpoint) -> str:
    """SHA-256 (hex) over :func:`checkpoint_bytes` — the tenant-chain root for this window."""
    return hashlib.sha256(checkpoint_bytes(checkpoint)).hexdigest()


def checkpoint_for_chain(
    records: Sequence[AuditRecord],
    *,
    tenant_id: str,
    chain_schema_version: str,
) -> AnchorCheckpoint:
    """Derive a checkpoint from an ordered, contiguous run of chain records. PURE — no I/O.

    `records` must be genesis-first and link prev->record: `records[0].prev_hash ==
    GENESIS_PREV_HASH` and `records[i].prev_hash == records[i-1].record_hash`. That is a
    PRECONDITION, not a verification — see :class:`AnchorChainDiscontinuityError`. Record hashes
    are never recomputed here; `AuditSink.verify_chain()` / `audit_postgres.verify_chain()` own
    that question and this module does not become a third, drifting implementation of it.

    The window is `min`/`max` over the records' timestamps rather than `records[0]`/`records[-1]`:
    chain ORDER is structural (prev-hash links) but timestamps can be skewed across replicas —
    `audit_postgres._TAIL_SQL`'s docstring documents exactly why order must never be derived from
    them. Taking the extrema keeps the attested window truthful under skew.

    An EMPTY sequence is refused: a chain with no records has no time window to attest, and
    silently inventing one (e.g. "now") would put a fabricated interval under a signature. A
    caller that genuinely wants to anchor an empty chain constructs :class:`AnchorCheckpoint`
    directly with `record_count=0`, `chain_head_hash=GENESIS_PREV_HASH` and an explicit,
    out-of-band window it can justify.
    """
    if not records:
        raise AnchorChainDiscontinuityError(
            "cannot derive a checkpoint from an empty record sequence — an empty chain has no "
            "time window to attest (construct AnchorCheckpoint directly with an explicit window)"
        )

    expected_prev = GENESIS_PREV_HASH
    for index, record in enumerate(records):
        if record.prev_hash != expected_prev:
            raise AnchorChainDiscontinuityError(
                f"record {index} has prev_hash={record.prev_hash!r}, expected {expected_prev!r} — "
                "the sequence is not one contiguous run from genesis, so 'the last record's hash' "
                "is not a meaningful chain head"
            )
        if record.record_hash is None:
            raise AnchorChainDiscontinuityError(
                f"record {index} has no record_hash (never emitted through a sink) — refusing to "
                "anchor a head that does not exist"
            )
        expected_prev = record.record_hash

    return AnchorCheckpoint(
        tenant_id=tenant_id,
        chain_head_hash=expected_prev,
        record_count=len(records),
        window_start=min(record.timestamp for record in records),
        window_end=max(record.timestamp for record in records),
        chain_schema_version=chain_schema_version,
    )


def checkpoint_field_names() -> frozenset[str]:
    """The checkpoint's field set, derived from the dataclass (pinned by set-equality in tests)."""
    return frozenset(field.name for field in fields(AnchorCheckpoint))


# =================================================================================================
# Seam 1 — the signer (real KMS/HSM is the OWNER's wiring, never this module's)
# =================================================================================================


@dataclass(frozen=True, slots=True)
class AnchorSignature:
    """A signature over :func:`checkpoint_bytes`, plus the metadata a verifier needs to check it."""

    algorithm: str
    key_id: str
    value: str

    def to_canonical_mapping(self) -> dict[str, str]:
        return {"algorithm": self.algorithm, "key_id": self.key_id, "value": self.value}


@runtime_checkable
class AnchorSigner(Protocol):
    """Seam: turn canonical checkpoint bytes into a signature. One method, injectable.

    The REAL implementation is a KMS/HSM client where the private key never leaves the module —
    that is a deployment concern, wired by the owner at a composition root, and deliberately NOT
    shipped here. The two implementations in this file are the fail-closed production default
    (:class:`RefusingAnchorSigner`) and a loudly-labeled synthetic (
    :class:`LabeledFakeKmsAnchorSigner`).
    """

    def sign(self, payload: bytes) -> AnchorSignature: ...


@runtime_checkable
class AnchorSignatureVerifier(Protocol):
    """Seam: check a signature against the bytes it claims to cover.

    Separate from :class:`AnchorSigner` because the roles genuinely separate in production: the
    periodic writer needs `sign` (and a key with sign permission); the continuous comparison job
    needs only `verify` (and a key with verify permission, or just the public half). Splitting the
    Protocols keeps a verifier from being handed signing authority it does not need.
    """

    def verify(self, payload: bytes, signature: AnchorSignature) -> bool: ...


class RefusingAnchorSigner:
    """PRODUCTION DEFAULT: refuses to sign — no real signing key is wired.

    `sign` raises :class:`AnchorSignerUnavailableError` unconditionally, so an anchor writer that
    was enabled without a real signer produces NOTHING rather than something that looks like
    evidence. This is what :func:`resolve_anchor_signer` selects when no signer is injected —
    the same anti-fabrication posture as `RefusingAnsGatewayTransport` (`tools/workers/
    ans_gateway.py`), for the same reason: a stub that returns a plausible artifact is worse than
    a refusal, because only the refusal is visibly incomplete.
    """

    def sign(self, payload: bytes) -> AnchorSignature:
        raise AnchorSignerUnavailableError(
            "no anchor signing key is wired — external anchoring requires a KMS/HSM-held key "
            f"(audit §4). Refusing to fabricate a signature over {len(payload)} bytes."
        )


class LabeledFakeKmsAnchorSigner:
    """DEV/TEST ONLY: HMAC-SHA256 under an UNMISTAKABLY SYNTHETIC label. Not a KMS. Not evidence.

    Why HMAC and not `cryptography` (which IS a declared dependency of this project)? Precisely
    because a fake must not resemble the real thing. A real anchor signature is ASYMMETRIC and its
    private half never leaves an HSM; this one is symmetric, its key material is an ordinary
    in-process `bytes`, and anyone holding that key can forge any anchor. Producing an Ed25519
    signature here would yield a blob that is byte-indistinguishable from a genuine one — exactly
    the masquerade this class exists to make impossible. `cryptography` stays available and unused,
    for the owner's real signer.

    Three independent labels travel with every signature so it cannot be mistaken anywhere:
    :data:`FAKE_ANCHOR_SIGNATURE_ALGORITHM` (algorithm), :data:`FAKE_ANCHOR_KEY_ID_PREFIX` (key
    id) and :data:`FAKE_ANCHOR_SIGNATURE_PREFIX` (the value itself). `verify` requires all three,
    so a signature stripped of its labels does not verify — the fake refuses to be laundered into
    looking real.

    Deterministic: the same bytes under the same key always yield the same signature, which is what
    lets a test pin a literal.
    """

    def __init__(self, *, key_label: str, secret: bytes) -> None:
        if not key_label.strip():
            raise ValueError("key_label must be a non-empty label for the synthetic key")
        lowered = key_label.lower()
        for fragment in _MASQUERADING_LABEL_SUBSTRINGS:
            if fragment in lowered:
                raise ValueError(
                    f"key_label {key_label!r} contains {fragment!r} — a labeled FAKE key must not "
                    "be given a label that reads as a reference to a real managed key"
                )
        if not secret:
            raise ValueError("secret must be non-empty (a keyless 'signature' attests to nothing)")
        self._key_label = key_label
        self._secret = secret

    @property
    def key_id(self) -> str:
        """The synthetic key id — always prefixed, never caller-controlled in full."""
        return f"{FAKE_ANCHOR_KEY_ID_PREFIX}{self._key_label}"

    def sign(self, payload: bytes) -> AnchorSignature:
        digest = hmac.new(self._secret, payload, hashlib.sha256).hexdigest()
        return AnchorSignature(
            algorithm=FAKE_ANCHOR_SIGNATURE_ALGORITHM,
            key_id=self.key_id,
            value=f"{FAKE_ANCHOR_SIGNATURE_PREFIX}{digest}",
        )

    def verify(self, payload: bytes, signature: AnchorSignature) -> bool:
        """True iff `signature` is this key's labeled fake signature over exactly `payload`."""
        if signature.algorithm != FAKE_ANCHOR_SIGNATURE_ALGORITHM:
            return False
        if signature.key_id != self.key_id:
            return False
        if not signature.value.startswith(FAKE_ANCHOR_SIGNATURE_PREFIX):
            return False
        presented = signature.value[len(FAKE_ANCHOR_SIGNATURE_PREFIX) :]
        expected = hmac.new(self._secret, payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(presented, expected)


def resolve_anchor_signer(signer: AnchorSigner | None) -> AnchorSigner:
    """Return `signer`, or the REFUSING default when none was injected (fail-closed)."""
    return signer if signer is not None else RefusingAnchorSigner()


# =================================================================================================
# Seam 2 — the anchor store (real WORM bucket / separate account is the OWNER's wiring)
# =================================================================================================


@runtime_checkable
class AnchorStore(Protocol):
    """Seam: an append-only, WORM-semantics store for anchor envelopes.

    `put` MUST refuse an existing key. That refusal is the entire security property: a store that
    silently overwrites lets the same privileged actor who rewrote the database also rewrite the
    anchors, which collapses the external anchor back into an in-DB one.

    The real implementation is object storage with a retention lock, ideally in a separate security
    account — owner wiring, not shipped here.
    """

    def put(self, key: str, payload: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def list_keys(self) -> tuple[str, ...]: ...


class RefusingAnchorStore:
    """PRODUCTION DEFAULT: refuses every operation — no real WORM store is wired.

    Same anti-fabrication posture as :class:`RefusingAnchorSigner`. In particular `put` refuses
    rather than writing somewhere convenient: an anchor written to a place with no retention lock
    is not an anchor, and having one on disk would be actively misleading during an incident.
    """

    def put(self, key: str, payload: bytes) -> None:
        raise AnchorStoreUnavailableError(
            "no WORM/retention-locked anchor store is wired — refusing to write anchor "
            f"{key!r} ({len(payload)} bytes) to an unprotected location (audit §4)"
        )

    def get(self, key: str) -> bytes:
        raise AnchorStoreUnavailableError(f"no anchor store is wired — cannot read {key!r}")

    def list_keys(self) -> tuple[str, ...]:
        raise AnchorStoreUnavailableError("no anchor store is wired — cannot list anchors")


class LabeledFakeWormAnchorStore:
    """DEV/TEST ONLY: a local-filesystem SIMULATION of WORM. Not durable, not retention-locked.

    What it genuinely enforces (so tests pin a consequence, not a comment):

      - **Write-once.** `put` on an existing key raises :class:`AnchorWormViolationError`. The
        write itself uses `open(..., "xb")` (`O_CREAT|O_EXCL`), so even a race that slips past the
        pre-check is refused by the kernel, not by a comment.
      - **Read-only after write — against ACCIDENT, not against the writer.** Each anchor file is
        chmod'ed to `0o444`, so ordinary code that opens it for writing fails. That is the whole
        extent of it: `0o444` is DISCRETIONARY, and the file's OWNER — the same unprivileged UID
        that wrote it, i.e. this very process — may `chmod(0o644)` and rewrite the bytes at will,
        after which `get()` returns the forgery. No `root` is needed and no privilege escalation is
        involved. The write-once property that this class genuinely enforces is `O_EXCL` on
        CREATION; there is no post-creation integrity property, and the tests are written to
        isolate exactly that (see `test_worm_store_refuses_overwrite_even_without_the_exists_
        precheck`, which chmods the file writable first precisely to strip this defense away).
      - **No key escapes the root — spelling AND resolution.** Keys must match
        :data:`_SAFE_ANCHOR_KEY` (absolute paths, `..` segments and backslashes cannot even be
        spelled) AND the resolved path must stay under the resolved root. The second half is not
        redundant: a perfectly legal key like `amh/a.anchor.json` writes OUTSIDE the root when
        `<root>/amh` is a symlink to somewhere else, and the grammar cannot see that — it reads
        characters, not the filesystem. Both refusals raise :class:`AnchorKeyError`.
      - **Self-labeling.** The first write drops :data:`FAKE_WORM_STORE_MARKER_FILENAME` in the
        root, stating in prose that this directory is not evidence.

    What it does NOT provide, and what a real store must: retention lock enforced by the storage
    service — on a local filesystem the file's OWNER (this process's own UID, no `root` and no
    escalation required) or any `root` actor can `chmod`/rewrite/`rm` at will — off-host
    durability, and a separate security account/credential boundary. Those are the owner's
    deployment concerns. Stating this as "only a privileged actor can tamper" would be an
    overstatement, and an overstated fake is worse than no fake: it is the exact failure the
    external audit §4 names, one layer down.

    Residual, disclosed rather than papered over: the containment check is a CHECK-THEN-WRITE, so
    an adversary who can plant a symlink on the key's path in the window between `_resolve` and the
    `open` still escapes (classic TOCTOU). The final path component is not exposed that way —
    `O_CREAT|O_EXCL` refuses an existing symlink outright — but intermediate directories are, and
    closing that would take an `openat`/`O_NOFOLLOW` descent this labeled fake deliberately does
    not carry. Anyone who can write inside the store root can also just `rm` its contents, so this
    residual does not change the class's honest posture: a local filesystem is not a WORM bucket,
    and only the owner's retention-locked store makes any of it binding.

    Construction is PURE (no I/O) — the same discipline `FreshSinkAuditEmitter` documents. That is
    load-bearing for the dark build: constructing this store while the flag is off must not create
    a directory, and a test probes exactly that.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def _resolve(self, key: str) -> Path:
        """Map `key` to a path, refusing anything that is not spelled AND located inside the root.

        The grammar check alone is not containment. `amh/a.anchor.json` is a legal key, but if
        `<root>/amh` is a SYMLINK to a directory elsewhere on the host, the write lands outside the
        root while every character of the key looks innocent — the grammar cannot see the
        filesystem. So the resolved path is checked against the resolved root as well.

        BOTH sides are resolved. Resolving only the candidate would produce false refusals whenever
        the root itself sits under a symlink (macOS `/var` -> `/private/var` is the everyday case,
        including every `tmp_path` in this repo's tests). `Path.resolve()` is non-strict, so a not
        yet created path resolves its existing prefix and keeps the rest — which is exactly the
        pre-planted-symlink case this refuses.
        """
        if not _SAFE_ANCHOR_KEY.match(key):
            raise AnchorKeyError(
                f"anchor key {key!r} is outside the safe grammar "
                "(slash-separated segments, each starting alphanumeric; no absolute paths, no "
                "'..', no backslashes)"
            )
        path = self._root.joinpath(*key.split("/"))
        resolved_root = self._root.resolve()
        if not path.resolve().is_relative_to(resolved_root):
            raise AnchorKeyError(
                f"anchor key {key!r} resolves to {str(path.resolve())!r}, outside the store root "
                f"{str(resolved_root)!r} — a symlink on the key's path escapes the store, and an "
                "anchor written outside the store is not in the store"
            )
        return path

    def put(self, key: str, payload: bytes) -> None:
        """Write `payload` under `key`. Refuses LOUDLY if the key already exists."""
        path = self._resolve(key)
        if path.exists():
            raise AnchorWormViolationError(
                f"anchor key {key!r} already exists — WORM stores are write-once; an anchor that "
                "can be overwritten is not an anchor"
            )
        self._root.mkdir(parents=True, exist_ok=True)
        self._write_marker()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "xb") as handle:  # noqa: SIM115 — explicit O_EXCL is the WORM guard
                handle.write(payload)
        except FileExistsError as exc:  # a racer created it between exists() and open()
            raise AnchorWormViolationError(
                f"anchor key {key!r} was created concurrently — WORM stores are write-once"
            ) from exc
        path.chmod(0o444)
        logger.info("audit_anchor_stored_labeled_fake", anchor_key=key, byte_count=len(payload))

    def get(self, key: str) -> bytes:
        return self._resolve(key).read_bytes()

    def list_keys(self) -> tuple[str, ...]:
        """Every stored anchor key, sorted. The self-labeling marker is not an anchor."""
        if not self._root.is_dir():
            return ()
        keys = [
            "/".join(path.relative_to(self._root).parts)
            for path in self._root.rglob("*")
            if path.is_file() and path.name != FAKE_WORM_STORE_MARKER_FILENAME
        ]
        return tuple(sorted(keys))

    def _write_marker(self) -> None:
        marker = self._root / FAKE_WORM_STORE_MARKER_FILENAME
        if marker.exists():
            return
        with open(marker, "x", encoding="utf-8") as handle:  # noqa: SIM115 — mirrors the O_EXCL put
            handle.write(_FAKE_WORM_STORE_MARKER_BODY)


def resolve_anchor_store(store: AnchorStore | None) -> AnchorStore:
    """Return `store`, or the REFUSING default when none was injected (fail-closed)."""
    return store if store is not None else RefusingAnchorStore()


# =================================================================================================
# The envelope + the flag-gated writer
# =================================================================================================


def anchor_key(checkpoint: AnchorCheckpoint, root: str) -> str:
    """Deterministic store key: `<tenant>/<window_end compact UTC>-<root>.anchor.json`.

    Two properties are load-bearing. (1) The ROOT is in the key, so re-anchoring an IDENTICAL
    checkpoint collides and the WORM store refuses it — a duplicate anchor is a no-op that must be
    loud, not silent. (2) The compact UTC `window_end` leads, so a plain lexical listing of a
    tenant's prefix is chronological, which is what the comparison job (`audit_anchor_verify`, leg
    2) uses to find "the latest anchor" without parsing every envelope — over a set it first
    CORROBORATES against a second, independent enumeration, because a listing that silently omits
    the newest anchor would otherwise make it verify against an older one and report clean (see
    that module's "THE LATEST-ANCHOR PROBLEM"). Nothing IN an anchor points at its predecessor, so
    ordering is all this key shape can offer; completeness it cannot.

    `tenant_id` was validated by `schema_for_tenant` at checkpoint construction, so it can contain
    neither a slash nor a dot and cannot escape its prefix.
    """
    stamp = checkpoint.window_end.strftime("%Y%m%dT%H%M%SZ")
    return f"{checkpoint.tenant_id}/{stamp}-{root}.anchor.json"


def build_envelope(checkpoint: AnchorCheckpoint, root: str, signature: AnchorSignature) -> dict[str, Any]:
    """The stored anchor document: format, the checkpoint verbatim, its root, and the signature.

    `root` is stored even though it is derivable from `checkpoint` — a reader must be able to say
    "the root I recomputed differs from the root that was sealed" without first having to trust
    that it reconstructed the canonicalization correctly. The signature covers the CHECKPOINT
    bytes, not the envelope, so an envelope whose `root` field was edited still fails the
    recomputation check while its signature stays valid — the two checks are independent on
    purpose.
    """
    return {
        "anchor_format": ANCHOR_FORMAT,
        "checkpoint": checkpoint.to_canonical_mapping(),
        "root": root,
        "signature": signature.to_canonical_mapping(),
    }


@dataclass(frozen=True, slots=True)
class AnchorWriteOutcome:
    """What :func:`write_anchor` did — or, with the flag off, what it deliberately did not do."""

    written: bool
    reason: str
    anchor_key: str | None = None
    root: str | None = None


def write_anchor(
    checkpoint: AnchorCheckpoint,
    *,
    signer: AnchorSigner | None = None,
    store: AnchorStore | None = None,
) -> AnchorWriteOutcome:
    """Sign `checkpoint` and seal it into the external anchor store. INERT unless the flag is on.

    FLAG OFF (the default, everywhere, today) — returns immediately with
    `written=False, reason=REASON_DISABLED`. Nothing is canonicalized, the signer is never called,
    the store is never touched, and no filesystem path is created. That is the dark-build
    guarantee, and it is asserted by probing filesystem state, not by reading this docstring.

    FLAG ON — canonicalizes, computes the root, signs the CHECKPOINT bytes (not the envelope; see
    :func:`build_envelope`), and `put`s the envelope under :func:`anchor_key`. Every failure
    propagates: an unwired signer raises :class:`AnchorSignerUnavailableError`, an unwired store
    raises :class:`AnchorStoreUnavailableError`, and re-anchoring an identical checkpoint raises
    :class:`AnchorWormViolationError`. There is no partial/best-effort path — an anchor that
    "mostly" wrote is not evidence, and silently degrading would recreate the exact blind spot
    external anchoring exists to close.

    Injection, not construction: with no `signer`/`store` the refusing defaults are resolved, so
    enabling the flag on a deployment that never wired the real KMS key and the real WORM bucket
    fails loudly instead of writing a comfortable-looking artifact somewhere useless.
    """
    if not anchor_writes_enabled():
        logger.debug(
            "audit_anchor_write_skipped_disabled",
            tenant_id=checkpoint.tenant_id,
            flag=ANCHOR_ENABLED_ENV,
        )
        return AnchorWriteOutcome(written=False, reason=REASON_DISABLED)

    payload = checkpoint_bytes(checkpoint)
    root = hashlib.sha256(payload).hexdigest()
    signature = resolve_anchor_signer(signer).sign(payload)
    key = anchor_key(checkpoint, root)
    resolve_anchor_store(store).put(key, canonical_bytes(build_envelope(checkpoint, root, signature)))

    logger.info(
        "audit_anchor_written",
        tenant_id=checkpoint.tenant_id,
        anchor_key=key,
        root=root,
        record_count=checkpoint.record_count,
        signature_key_id=signature.key_id,
        signature_algorithm=signature.algorithm,
    )
    return AnchorWriteOutcome(written=True, reason=REASON_WRITTEN, anchor_key=key, root=root)
