"""Continuous Postgres-vs-anchor verification job — INERT (Onda 4, leg 2 of the external anchor).

=================================================================================================
WHY THIS MODULE EXISTS AT ALL
=================================================================================================
`audit_anchor.py` (leg 1) computes and seals a signed checkpoint OUTSIDE the database. On its own
that buys nothing: an anchor nobody ever compares against is a file. The external audit
(`docs/audits/architecturally safe, operationally incomplete.md` §4 — GITIGNORED, local-only; see
`PLANS.md`:256, which is why this file quotes it rather than linking it) prescribes the comparison
in the same breath as the anchor — *"continuously compare Postgres against the latest external
anchor"* — and this module is that comparison.

The adversary is the one every in-DB defense structurally cannot stop: a privileged actor who
rewrites the whole `audit_chain` and recomputes it. Such a chain satisfies the SHA-256 hash links,
the per-tenant advisory lock and `UNIQUE(prev_record_hash)`, and `audit_postgres.verify_chain()`
declares it VALID — because every byte the verifier reads is a byte the forger wrote. What the
forger does NOT control is a root that was signed by a KMS/HSM key and sealed in a retention-locked
store before the rewrite. This job recomputes today's root from today's database and compares it to
that sealed root. That comparison is the entire security value of leg 1.

=================================================================================================
THE TWO RULES THIS JOB IS BUILT AROUND
=================================================================================================
**1. RECOMPUTE, NEVER TRUST.** The comparison basis is ALWAYS
`sha256(canonical_bytes(envelope["checkpoint"]))` — recomputed from the checkpoint whose SIGNATURE
this job just verified. The `root` field stored inside the envelope is NEVER the basis of a
verdict. It is not covered by the signature (see `audit_anchor.build_envelope`), so an actor who
can edit the anchor file can set it to whatever the rewritten database happens to hash to; a job
that compared `envelope["root"]` against the database would hand that actor a clean verdict for the
price of one string edit. The field is read for exactly one purpose — as a TAMPER INDICATOR: in a
genuine WORM store it cannot change, so `envelope["root"] != recomputed anchor root` is reported as
a DIVERGENCE in its own right (`ANCHOR_ROOT_FIELD_INCONSISTENT`), never silently ignored.

Symmetrically, the database side is recomputed too, and through the SAME code path the writer used:
`audit_anchor.checkpoint_for_chain()` over `AuditRecord`s rebuilt from the stored rows. This module
does not contain a canonicalizer. A second canonicalizer would drift from the one the signatures
were made over, and a drifted canonicalizer reports DIVERGENCE for a chain that is fine — which is
worse than no verifier at all, because the first false alarm is the last one anybody believes.

**2. A LISTING IS ADVISORY; A CLEAN VERDICT MAY NEVER REST ON ONE.** See "THE LATEST-ANCHOR
PROBLEM" below. This is the sharpest failure mode in the whole design and it has its own section.

=================================================================================================
THE LATEST-ANCHOR PROBLEM (and this job's explicit position)
=================================================================================================
"Verify against the latest anchor" presumes you can find the latest anchor. You cannot, in general:

  - An anchor envelope carries NO pointer to its predecessor (the format is four fixed keys —
    `anchor_format`, `checkpoint`, `root`, `signature`). "Latest" is therefore a property of the
    STORE'S LISTING, not a property of the evidence. Nothing inside a signed anchor says "and there
    is no newer one".
  - `LabeledFakeWormAnchorStore.list_keys()` walks with `Path.rglob`, which does not descend into
    SYMLINKED DIRECTORIES, while `get()`/`put()` happily resolve through them (they only refuse a
    path that leaves the root). Measured, not assumed: with `<root>/amh -> <root>/real_amh`, a key
    written as `amh/<x>` is served by `get("amh/<x>")` and reported by `list_keys()` under a
    DIFFERENT string — or, one directory deeper, not reported at all.
  - A real object store is no better: `ListObjectsV2` is eventually consistent, so a just-written
    anchor can be legitimately absent from a listing taken a second later.

The consequence is the same in all three cases and it is the dangerous direction: **an anchor the
listing omits can be NEWER than the one the job selects**, so the job verifies the database against
a STALE anchor and reports MATCH. Concretely — the scenario `test_a_newer_anchor_invisible_to_the
_listing_does_not_produce_a_clean_verdict` builds — anchor T1 covers records 1..3, anchor T2 covers
records 1..5, an actor deletes records 4 and 5 and hides T2 from the listing: verifying against T1
is genuinely, correctly clean, and the deletion is never seen.

**Position taken here.** The selection is corroborated by a SECOND, INDEPENDENT enumeration
(:class:`AnchorKeyProbe`, a required seam — there is no default, because a default would be a
silent degradation of exactly the property this defends). The two enumerations are restricted to
the tenant's key prefix and compared by SET EQUALITY:

  - any disagreement, in EITHER direction, yields :data:`STATUS_STORE_LISTING_SUSPECT` — never
    MATCH, never a verdict about the database. Two independent enumerations that disagree mean at
    least one of them is wrong, and a verifier that picks a side is guessing.
  - a store that cannot list, or a probe that cannot probe, is likewise SUSPECT and never clean.
  - the key the listing named must actually be readable; a listed-but-unreadable key is the same
    class of store misbehaviour.

**Disclosed limit.** A corroborator built on the same primitive as the listing is vacuous, and this
module cannot enforce independence — it can only require that a second source exist.
:class:`FilesystemAnchorKeyProbe` (the companion to leg 1's labeled fake store) deliberately uses a
DIFFERENT traversal primitive — `os.walk(followlinks=True)` against `Path.rglob` — because
different symlink semantics are precisely what makes it able to see what the listing misses. A real
deployment's corroborator must be independent in the same spirit (an S3 Inventory manifest against
a live `ListObjectsV2`, or a second account's read-only view). That is owner wiring and a disclosed
assumption, not a property this file can prove.

**Not fixed here, flagged for humans:** the in-band repair is to chain the anchors themselves — a
`prev_anchor_root` field would make an omitted anchor detectable from the evidence instead of from
the storage layer. That is a format bump (`ANCHOR_FORMAT`) and therefore a leg-1/owner decision,
not something leg 2 may do unilaterally.

=================================================================================================
DARK-BUILD DOCTRINE — a SIBLING flag, not the writer's
=================================================================================================
:data:`ANCHOR_VERIFY_ENABLED_ENV` = `MAEZO_AUDIT_ANCHOR_VERIFY_ENABLED`, default OFF, with the same
truthy vocabulary as the writer's flag (imported, not re-spelled). It is deliberately a SECOND
switch rather than a reuse of `MAEZO_AUDIT_ANCHOR_ENABLED`:

  - **The two sides are separately deployable and separately privileged.** The writer needs a key
    with SIGN permission; this job needs only VERIFY (leg 1 split the Protocols for exactly this).
    One flag would mean enabling the writer on a host also arms a verifier there, or vice versa.
  - **"Anchoring on, verifying not yet" is a real intermediate state.** Anchors must exist before
    comparing them is meaningful; a single flag would make the first enablement start a DB-reading
    loop against a store with nothing in it.
  - **This side consumes resources the writer does not.** It opens a Postgres connection per run,
    reads every `audit_chain` row for a tenant, and (once wired) pages a human. An operator must
    consent to that separately from consenting to write an anchor file.

With the flag off, :func:`verify_latest_anchor` returns `STATUS_DISABLED` BEFORE it lists, BEFORE it
reads a byte from the store and BEFORE it opens a database connection, and
:func:`run_verification_loop` returns without ever awaiting its sleep. That is asserted by
exploding seams AND by filesystem probes, not by this paragraph.

`DISABLED` is a member of the outcome vocabulary and its CLI exit code is non-zero, on purpose: a
verification that did not run is not a verification that passed. "We did not look" must never be
readable as "we looked and it was fine".

=================================================================================================
THE OUTCOME VOCABULARY (closed, one per run)
=================================================================================================
`MATCH` · `DIVERGENCE` · `NO_ANCHOR` · `SIGNATURE_INVALID` · `STORE_LISTING_SUSPECT` · `DB_ERROR` ·
`ANCHOR_UNREADABLE` · `DISABLED`. Pinned by set-equality in the tests, with an exit code, a log
event name and a log level for every member (completeness is pinned too).

The last two extend the six named in the leg-2 brief, and the extension follows the brief's OWN
rule — every absence-state gets its own outcome, because "we could not check" must never collapse
into "clean" OR into a neighbouring alarm that sends an operator down the wrong path:

  - `ANCHOR_UNREADABLE` — the store served bytes that are not a usable anchor for this tenant
    (truncated file, unknown `anchor_format`, a checkpoint that fails leg-1 validation). Folding
    this into `SIGNATURE_INVALID` would tell an operator "someone forged an anchor" when the
    evidence says "an anchor file is corrupt"; those are different incidents with different first
    moves (key compromise review vs storage integrity review).
  - `DISABLED` — see above.

`reason` narrows the status to a specific finding within a likewise-closed token vocabulary
(:data:`REASON_TO_STATUS`); it never carries free prose.

=================================================================================================
PHI / SECRETS
=================================================================================================
An outcome carries hashes, counts, anchor KEYS (tenant + UTC stamp + root hex), a signature key id,
and an exception CLASS NAME. It carries no record content: no `details`, no `decision_basis`, no
agent id, no action, no subject identifier — a divergence report is a statement about roots, and
including the diverging records would put the very content under investigation into an alerting
sink. A test asserts the key set by set-equality and asserts the absence of record content in a
real divergence event.

The exception MESSAGE of a database failure is deliberately dropped in favour of
`type(exc).__name__`: an asyncpg connection error can quote the DSN, and a DSN carries a password.
The class name (`InvalidPasswordError`, `ConnectionDoesNotExistError`, ...) is diagnostic enough to
route an incident, and reproducing with the CLI recovers the detail on a human's terminal instead
of in a log aggregator.

=================================================================================================
WHAT THIS MODULE NEVER DOES
=================================================================================================
Read-only, both ways. It never writes an anchor (no `store.put` anywhere — AST-pinned), never
INSERTs/UPDATEs/DELETEs (AST-pinned), never mutates `audit.py` / `audit_postgres.py` (leg 1's
additive-only doctrine, still at zero modifications to both), and never starts itself: the loop is
an explicitly-invocable coroutine, nothing at import time schedules anything (AST-pinned), and a
fence asserts that no module under `src/maezo/` imports this one.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream
import structlog

from maezo.gateway.audit import GENESIS_PREV_HASH, AuditRecord
from maezo.gateway.audit_anchor import (
    _TRUTHY,
    ANCHOR_FORMAT,
    FAKE_WORM_STORE_MARKER_FILENAME,
    AnchorChainDiscontinuityError,
    AnchorCheckpoint,
    AnchorSignature,
    AnchorSignatureVerifier,
    AnchorStore,
    LabeledFakeKmsAnchorSigner,
    LabeledFakeWormAnchorStore,
    canonical_bytes,
    checkpoint_field_names,
    checkpoint_for_chain,
    checkpoint_root,
)

# `_row_to_record` is PRIVATE in `audit_postgres` and imported here deliberately, for the same
# anti-drift reason leg 1 reuses `schema_for_tenant` instead of re-spelling the schema grammar: it
# is the one place that knows a stored row must be turned back into an `AuditRecord` with
# `input_hash` RECOMPUTED rather than read back (see its docstring — that is what makes a row
# tampered in `decision_basis` alone still fail). A second copy here would drift from the mapping
# `audit_postgres.verify_chain()` uses, and the two verifiers would then disagree about the same
# database. Importing it (never modifying it) keeps `audit_postgres.py` at zero modifications while
# a test pins that this really is that function.
from maezo.gateway.audit_postgres import (
    _row_to_record,
    normalize_dsn,
    schema_for_tenant,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

logger = structlog.get_logger(__name__)


# =================================================================================================
# The flag (dark build) — a SIBLING of the writer's, see the module docstring
# =================================================================================================

#: The ONE switch for the verification side. Absent / blank / anything outside the writer's truthy
#: vocabulary => the job is INERT. Deliberately distinct from `MAEZO_AUDIT_ANCHOR_ENABLED`.
ANCHOR_VERIFY_ENABLED_ENV: Final[str] = "MAEZO_AUDIT_ANCHOR_VERIFY_ENABLED"


def anchor_verification_enabled() -> bool:
    """True iff an operator EXPLICITLY opted this deployment into anchor verification.

    Shares `audit_anchor`'s truthy vocabulary by IMPORT, not by a second copy of the literal set —
    a divergence between the two flags' dialects (`"on"` accepted by one, ignored by the other)
    would be invisible until an operator's enablement silently half-applied.
    """
    return os.environ.get(ANCHOR_VERIFY_ENABLED_ENV, "").strip().lower() in _TRUTHY


# =================================================================================================
# The closed outcome vocabulary
# =================================================================================================

#: The database agrees with a signature-verified anchor. The ONLY clean verdict.
STATUS_MATCH: Final[str] = "MATCH"
#: The recomputed root does not equal the anchored root — or the chain can no longer produce the
#: anchored window at all (records removed, chain forked, links broken). LOUD.
STATUS_DIVERGENCE: Final[str] = "DIVERGENCE"
#: No anchor exists for this tenant yet. NOT clean — it means nothing has been attested.
STATUS_NO_ANCHOR: Final[str] = "NO_ANCHOR"
#: An anchor exists but its signature does not verify. Treated as tamper (ADR-0029 §2 idiom).
STATUS_SIGNATURE_INVALID: Final[str] = "SIGNATURE_INVALID"
#: The anchor store misbehaved: listing and corroborating probe disagree, one of them is
#: unavailable, or a listed key is unreadable. No verdict about the database is issued.
STATUS_STORE_LISTING_SUSPECT: Final[str] = "STORE_LISTING_SUSPECT"
#: The database could not be read. NOT clean — an unreadable chain is an unverified chain.
STATUS_DB_ERROR: Final[str] = "DB_ERROR"
#: Bytes came back from the store but they are not a usable anchor for this tenant.
STATUS_ANCHOR_UNREADABLE: Final[str] = "ANCHOR_UNREADABLE"
#: The flag is off. Explicit, because "did not run" must never read as "ran and passed".
STATUS_DISABLED: Final[str] = "DISABLED"

#: Every status, pinned by set-equality in the tests. One per run.
ALL_STATUSES: Final[frozenset[str]] = frozenset(
    {
        STATUS_MATCH,
        STATUS_DIVERGENCE,
        STATUS_NO_ANCHOR,
        STATUS_SIGNATURE_INVALID,
        STATUS_STORE_LISTING_SUSPECT,
        STATUS_DB_ERROR,
        STATUS_ANCHOR_UNREADABLE,
        STATUS_DISABLED,
    }
)

# --- reason tokens (a closed narrowing of a status; never free prose) ----------------------------
REASON_VERIFICATION_DISABLED: Final[str] = "VERIFICATION_DISABLED"
REASON_LISTING_UNAVAILABLE: Final[str] = "LISTING_UNAVAILABLE"
REASON_PROBE_UNAVAILABLE: Final[str] = "PROBE_UNAVAILABLE"
REASON_LISTING_PROBE_DISAGREEMENT: Final[str] = "LISTING_PROBE_DISAGREEMENT"
REASON_SELECTED_ANCHOR_UNREADABLE: Final[str] = "SELECTED_ANCHOR_UNREADABLE"
REASON_ENVELOPE_NOT_JSON: Final[str] = "ENVELOPE_NOT_JSON"
REASON_ENVELOPE_SHAPE_UNKNOWN: Final[str] = "ENVELOPE_SHAPE_UNKNOWN"
REASON_ENVELOPE_FORMAT_UNKNOWN: Final[str] = "ENVELOPE_FORMAT_UNKNOWN"
REASON_CHECKPOINT_INVALID: Final[str] = "CHECKPOINT_INVALID"
REASON_CHECKPOINT_TENANT_MISMATCH: Final[str] = "CHECKPOINT_TENANT_MISMATCH"
REASON_SIGNATURE_REJECTED: Final[str] = "SIGNATURE_REJECTED"
REASON_SIGNATURE_VERIFIER_FAILED: Final[str] = "SIGNATURE_VERIFIER_FAILED"
REASON_ANCHOR_ROOT_FIELD_INCONSISTENT: Final[str] = "ANCHOR_ROOT_FIELD_INCONSISTENT"
REASON_CHAIN_FORK: Final[str] = "CHAIN_FORK"
REASON_RECORD_COUNT_SHORTFALL: Final[str] = "RECORD_COUNT_SHORTFALL"
REASON_CHAIN_DISCONTINUITY: Final[str] = "CHAIN_DISCONTINUITY"
REASON_ROOT_MISMATCH: Final[str] = "ROOT_MISMATCH"
REASON_DATABASE_UNREACHABLE: Final[str] = "DATABASE_UNREACHABLE"

#: Which status each reason may appear under. Pinned in tests, so a reason can never be emitted
#: beside a status that contradicts it (e.g. a `ROOT_MISMATCH` reported as `MATCH`).
REASON_TO_STATUS: Final[Mapping[str, str]] = {
    REASON_VERIFICATION_DISABLED: STATUS_DISABLED,
    REASON_LISTING_UNAVAILABLE: STATUS_STORE_LISTING_SUSPECT,
    REASON_PROBE_UNAVAILABLE: STATUS_STORE_LISTING_SUSPECT,
    REASON_LISTING_PROBE_DISAGREEMENT: STATUS_STORE_LISTING_SUSPECT,
    REASON_SELECTED_ANCHOR_UNREADABLE: STATUS_STORE_LISTING_SUSPECT,
    REASON_ENVELOPE_NOT_JSON: STATUS_ANCHOR_UNREADABLE,
    REASON_ENVELOPE_SHAPE_UNKNOWN: STATUS_ANCHOR_UNREADABLE,
    REASON_ENVELOPE_FORMAT_UNKNOWN: STATUS_ANCHOR_UNREADABLE,
    REASON_CHECKPOINT_INVALID: STATUS_ANCHOR_UNREADABLE,
    REASON_CHECKPOINT_TENANT_MISMATCH: STATUS_ANCHOR_UNREADABLE,
    REASON_SIGNATURE_REJECTED: STATUS_SIGNATURE_INVALID,
    REASON_SIGNATURE_VERIFIER_FAILED: STATUS_SIGNATURE_INVALID,
    REASON_ANCHOR_ROOT_FIELD_INCONSISTENT: STATUS_DIVERGENCE,
    REASON_CHAIN_FORK: STATUS_DIVERGENCE,
    REASON_RECORD_COUNT_SHORTFALL: STATUS_DIVERGENCE,
    REASON_CHAIN_DISCONTINUITY: STATUS_DIVERGENCE,
    REASON_ROOT_MISMATCH: STATUS_DIVERGENCE,
    REASON_DATABASE_UNREACHABLE: STATUS_DB_ERROR,
}

#: Process exit code per status for the offline CLI. Deliberately starting at 10 rather than 1:
#: `1` is what an uncaught traceback exits with and `2` is what `argparse` exits with on a usage
#: error, so a crash or a typo could otherwise be READ as a verdict. Only MATCH is 0.
EXIT_CODE_BY_STATUS: Final[Mapping[str, int]] = {
    STATUS_MATCH: 0,
    STATUS_DIVERGENCE: 10,
    STATUS_NO_ANCHOR: 11,
    STATUS_SIGNATURE_INVALID: 12,
    STATUS_STORE_LISTING_SUSPECT: 13,
    STATUS_DB_ERROR: 14,
    STATUS_ANCHOR_UNREADABLE: 15,
    STATUS_DISABLED: 16,
}

#: One structlog event name per status, so an alerting rule can match an event instead of parsing a
#: field. `audit_anchor_divergence_detected` is the one leg 3's drills and the runbook consume.
EVENT_BY_STATUS: Final[Mapping[str, str]] = {
    STATUS_MATCH: "audit_anchor_verification_match",
    STATUS_DIVERGENCE: "audit_anchor_divergence_detected",
    STATUS_NO_ANCHOR: "audit_anchor_absent",
    STATUS_SIGNATURE_INVALID: "audit_anchor_signature_invalid",
    STATUS_STORE_LISTING_SUSPECT: "audit_anchor_store_listing_suspect",
    STATUS_DB_ERROR: "audit_anchor_database_unreadable",
    STATUS_ANCHOR_UNREADABLE: "audit_anchor_envelope_unreadable",
    STATUS_DISABLED: "audit_anchor_verification_skipped_disabled",
}

#: Log level per status. Everything that is not MATCH and not a day-0 absence is `error`: an
#: unverified chain and a diverged chain are both incidents, and a warning-level incident is an
#: incident nobody is paged for.
LEVEL_BY_STATUS: Final[Mapping[str, str]] = {
    STATUS_MATCH: "info",
    STATUS_DIVERGENCE: "error",
    STATUS_NO_ANCHOR: "warning",
    STATUS_SIGNATURE_INVALID: "error",
    STATUS_STORE_LISTING_SUSPECT: "error",
    STATUS_DB_ERROR: "error",
    STATUS_ANCHOR_UNREADABLE: "error",
    STATUS_DISABLED: "debug",
}


# =================================================================================================
# The outcome
# =================================================================================================


@dataclass(frozen=True, slots=True)
class AnchorVerificationOutcome:
    """One run's verdict. Frozen, hash-only, and the SAME shape as the log event and the CLI JSON.

    Attributes:
        status: A member of :data:`ALL_STATUSES`.
        tenant_id: The tenant whose chain was (or was not) verified.
        reason: A member of :data:`REASON_TO_STATUS`, or `None` for MATCH.
        anchor_key: Store key of the anchor this run selected, when one was selected.
        anchor_root: Root RECOMPUTED from the signature-verified checkpoint — never the envelope's
            stored `root` field. This is the comparison basis.
        database_root: Root recomputed from the database over the anchored window.
        anchored_record_count / database_record_count: counts, not content.
        anchored_head_hash / database_head_hash: chain heads, 64 hex each.
        signature_key_id: Which key verified (or failed to verify) — routing information for an
            incident, and the labeled-fake prefix makes a synthetic run self-evident.
        error_type: Exception CLASS name for `DB_ERROR`. Never the message (see the PHI/SECRETS
            section of the module docstring).
        listing_disagreement: Keys the listing and the corroborating probe disagreed about
            (symmetric difference, sorted). Empty unless the status is STORE_LISTING_SUSPECT.
    """

    status: str
    tenant_id: str
    reason: str | None = None
    anchor_key: str | None = None
    anchor_root: str | None = None
    database_root: str | None = None
    anchored_record_count: int | None = None
    database_record_count: int | None = None
    anchored_head_hash: str | None = None
    database_head_hash: str | None = None
    signature_key_id: str | None = None
    error_type: str | None = None
    listing_disagreement: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        """`True` only for MATCH. Every other status — including every absence-state — is not."""
        return self.status == STATUS_MATCH

    @property
    def exit_code(self) -> int:
        return EXIT_CODE_BY_STATUS[self.status]

    def to_json_mapping(self) -> dict[str, Any]:
        """The pinned wire shape: ALL keys always present, `null` where not applicable.

        Always-present keys (rather than omitting nulls) so a consumer — the leg-3 drills, the
        runbook, a CI job — can index without `get()` guards and so a MISSING field reads as a
        contract break rather than as an absent value.
        """
        return {
            "status": self.status,
            "tenant_id": self.tenant_id,
            "reason": self.reason,
            "anchor_key": self.anchor_key,
            "anchor_root": self.anchor_root,
            "database_root": self.database_root,
            "anchored_record_count": self.anchored_record_count,
            "database_record_count": self.database_record_count,
            "anchored_head_hash": self.anchored_head_hash,
            "database_head_hash": self.database_head_hash,
            "signature_key_id": self.signature_key_id,
            "error_type": self.error_type,
            "listing_disagreement": list(self.listing_disagreement),
        }


def _emit(outcome: AnchorVerificationOutcome) -> AnchorVerificationOutcome:
    """Log `outcome` at its pinned event name and level, then return it unchanged.

    The event payload IS `to_json_mapping()` — one shape for the log, the CLI and the return value,
    so a field can never be present in one and missing from another.
    """
    getattr(logger, LEVEL_BY_STATUS[outcome.status])(
        EVENT_BY_STATUS[outcome.status], **outcome.to_json_mapping()
    )
    return outcome


# =================================================================================================
# Seam 1 — the corroborating key probe (see "THE LATEST-ANCHOR PROBLEM")
# =================================================================================================


@runtime_checkable
class AnchorKeyProbe(Protocol):
    """Seam: a SECOND, INDEPENDENT enumeration of a tenant's anchor keys.

    Not a convenience. It is the only thing standing between "the listing omitted the newest
    anchor" and a silent clean verdict, so it is a REQUIRED argument everywhere it appears — a
    default implementation would be a default answer to a question that has no safe default.

    Contract: return every anchor key the store holds under `tenant_id`'s prefix, by a route that
    does NOT share the listing's failure modes. The job compares the two sets and refuses to issue
    a database verdict when they disagree.
    """

    def probe_keys(self, tenant_id: str) -> tuple[str, ...]: ...


class FilesystemAnchorKeyProbe:
    """Corroborator for :class:`~maezo.gateway.audit_anchor.LabeledFakeWormAnchorStore` (dev/test).

    Independent by CONSTRUCTION, not by claim: `list_keys()` walks with `Path.rglob`, which never
    descends into a symlinked directory; this walks with `os.walk(followlinks=True)`, which does.
    That single difference is what lets it see an anchor the listing omits — the failure mode the
    module docstring's latest-anchor section describes.

    `followlinks=True` is otherwise a foot-gun: a symlink cycle makes `os.walk` recurse forever. So
    every directory is identified by `(st_dev, st_ino)` and pruned once seen — an anchor store that
    contains a loop must produce a finite (and then disagreeing, hence SUSPECT) enumeration rather
    than hanging the verification job.

    Construction is PURE (no I/O), the same discipline the labeled fake store documents, and
    load-bearing for the same reason: constructing the seams while the flag is off must touch
    nothing.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def probe_keys(self, tenant_id: str) -> tuple[str, ...]:
        if not self._root.is_dir():
            return ()
        prefix = f"{tenant_id}/"
        seen: set[tuple[int, int]] = set()
        keys: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self._root, followlinks=True):
            try:
                stat_result = os.stat(dirpath)
            except OSError:  # a directory that vanished mid-walk cannot contribute keys
                dirnames[:] = []
                continue
            # Enumerate THIS directory's files at EVERY visit — never gated on the identity
            # guard below. A file reached through a symlink alias yields a key DISTINCT from the
            # one under its physical path (`<tenant>/x` vs `physical/x`), so suppressing
            # enumeration at a re-seen inode made the probe's output depend on os.walk's readdir
            # order: on the OS ordering that walked the physical dir first, the tenant-prefixed key
            # was never reported and the listing-suspect defense could silently fail to fire. File
            # enumeration is pure string work (no recursion), so doing it unconditionally is finite.
            for name in filenames:
                if name == FAKE_WORM_STORE_MARKER_FILENAME:
                    continue
                relative = Path(dirpath, name).relative_to(self._root)
                key = "/".join(relative.parts)
                if key.startswith(prefix):
                    keys.append(key)
            # The (st_dev, st_ino) guard bounds RECURSION only — it must never skip the enumeration
            # above. On a re-seen inode (a symlink cycle, or a second route to the same directory)
            # prune descent so a true cycle terminates; the files at this alias are already counted.
            identity = (stat_result.st_dev, stat_result.st_ino)
            if identity in seen:
                dirnames[:] = []
                continue
            seen.add(identity)
        return tuple(sorted(set(keys)))


# =================================================================================================
# Seam 2 — the chain record source (the database side)
# =================================================================================================


@dataclass(frozen=True, slots=True)
class ChainSnapshot:
    """What one read of a tenant's `audit_chain` found. Structure, never a verdict.

    Attributes:
        records: The contiguous run reachable from genesis by following `prev_record_hash`, in
            order. May be SHORTER than `total_records` — that gap is the caller's finding to make,
            not this type's.
        total_records: How many rows the table held.
        fork_at_prev_hash: Set iff two rows share a `prev_record_hash`, i.e. the chain forked. When
            set, `records` is empty: there is no single run to walk.
    """

    records: tuple[AuditRecord, ...]
    total_records: int
    fork_at_prev_hash: str | None = None


@runtime_checkable
class ChainRecordSource(Protocol):
    """Seam: read one tenant's audit chain. The ONLY thing in this job that touches a database.

    Separated from the verification logic so the whole decision tree — every outcome, every reason
    — is provable offline against fixtures, and so leg 3's drills can inject a source that
    deliberately returns a tampered chain.
    """

    async def read_chain(self, tenant_id: str) -> ChainSnapshot: ...


def snapshot_from_rows(rows: Sequence[Any]) -> ChainSnapshot:
    """Walk stored `audit_chain` rows into a :class:`ChainSnapshot`. PURE — no I/O.

    Structural walk from genesis via `prev_record_hash`, never `ORDER BY timestamp`: the tail is
    the record nobody points at, and `audit_postgres._TAIL_SQL`'s docstring documents at length why
    order must not be derived from wall clocks that skew across replicas. Mirrors the traversal in
    `audit_postgres.verify_chain()` (fork detection by duplicate `prev_record_hash`, genesis lookup
    by `GENESIS_PREV_HASH`) so the two readers cannot disagree about the same rows.

    Deliberately NOT a hash verification: `verify_chain()` owns "is this chain internally
    consistent" and this job owns "does this chain match what was attested". A forger who rewrites
    and recomputes passes the first question and fails the second — which is the whole point of an
    external anchor, and the reason this module must not become a third recomputation of record
    hashes.
    """
    by_prev: dict[Any, Any] = {}
    for row in rows:
        previous = row["prev_record_hash"]
        if previous in by_prev:
            return ChainSnapshot(records=(), total_records=len(rows), fork_at_prev_hash=previous)
        by_prev[previous] = row

    ordered: list[AuditRecord] = []
    current = by_prev.get(GENESIS_PREV_HASH)
    while current is not None:
        ordered.append(_row_to_record(current))
        current = by_prev.get(current["record_hash"])
    return ChainSnapshot(records=tuple(ordered), total_records=len(rows))


class PostgresChainRecordSource:
    """READ-ONLY `audit_chain` reader for one deployment's Postgres. Opens one connection per read.

    Uses the same `schema_for_tenant` validation and `normalize_dsn` conversion as every other
    consumer of the platform's DSN convention, and the same one-connection-per-call shape as
    `audit_postgres.verify_chain()` — a verification job runs on a cadence measured in minutes, so
    a pool would hold an idle connection open for the entire interval to save nothing.

    Construction is PURE (no I/O): a bad DSN or tenant surfaces on the first read, which raises,
    never as a silent no-op. Every failure propagates to the caller, which converts it to
    `DB_ERROR` — an unreadable chain is an unverified chain, never a clean one.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = normalize_dsn(dsn)

    async def read_chain(self, tenant_id: str) -> ChainSnapshot:
        schema = schema_for_tenant(tenant_id)  # anti-injection: interpolated into SET search_path
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(f'SET search_path TO "{schema}"')
            rows = await conn.fetch("SELECT * FROM audit_chain")
        finally:
            await conn.close()
        return snapshot_from_rows(rows)


# =================================================================================================
# Anchor selection + envelope parsing
# =================================================================================================


@dataclass(frozen=True, slots=True)
class _ParsedAnchor:
    """A structurally valid anchor envelope, not yet signature-verified."""

    checkpoint: AnchorCheckpoint
    checkpoint_bytes: bytes
    stored_root: str
    signature: AnchorSignature


class _AnchorUnreadableError(Exception):
    """Internal: the bytes are not a usable anchor. Carries the closed reason token, not prose."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _parse_envelope(payload: bytes, *, tenant_id: str) -> _ParsedAnchor:
    """Structural parse of a stored anchor envelope. Raises :class:`_AnchorUnreadableError`.

    Fail-closed at every step, and by EXACT key-set equality rather than "contains the keys I
    need": an envelope carrying extra keys is not the format leg 1 writes, and quietly reading the
    four familiar keys out of an unfamiliar document is how a verifier ends up attesting to
    something nobody designed. `anchor_format` is checked against the constant for the same reason
    — a future v2 anchor must be REFUSED by a v1 verifier, not half-understood by it.
    """
    try:
        parsed: Any = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _AnchorUnreadableError(REASON_ENVELOPE_NOT_JSON) from exc
    if not isinstance(parsed, dict):
        raise _AnchorUnreadableError(REASON_ENVELOPE_SHAPE_UNKNOWN)
    if frozenset(parsed) != frozenset({"anchor_format", "checkpoint", "root", "signature"}):
        raise _AnchorUnreadableError(REASON_ENVELOPE_SHAPE_UNKNOWN)
    if parsed["anchor_format"] != ANCHOR_FORMAT:
        raise _AnchorUnreadableError(REASON_ENVELOPE_FORMAT_UNKNOWN)

    checkpoint_mapping = parsed["checkpoint"]
    signature_mapping = parsed["signature"]
    stored_root = parsed["root"]
    if not isinstance(checkpoint_mapping, dict) or not isinstance(signature_mapping, dict):
        raise _AnchorUnreadableError(REASON_ENVELOPE_SHAPE_UNKNOWN)
    if not isinstance(stored_root, str):
        raise _AnchorUnreadableError(REASON_ENVELOPE_SHAPE_UNKNOWN)
    if frozenset(checkpoint_mapping) != checkpoint_field_names():
        raise _AnchorUnreadableError(REASON_ENVELOPE_SHAPE_UNKNOWN)
    if frozenset(signature_mapping) != frozenset({"algorithm", "key_id", "value"}):
        raise _AnchorUnreadableError(REASON_ENVELOPE_SHAPE_UNKNOWN)
    if not all(isinstance(value, str) for value in signature_mapping.values()):
        raise _AnchorUnreadableError(REASON_ENVELOPE_SHAPE_UNKNOWN)

    # The preimage is re-canonicalized from the STORED mapping, exactly as leg 1's
    # `test_enabled_writer_seals_a_verifiable_anchor` replays it. Re-deriving it from the
    # reconstructed dataclass instead would NORMALIZE (a stored `-03:00` window would be rewritten
    # to UTC) and silently repair bytes the signature was never made over.
    preimage = canonical_bytes(checkpoint_mapping)

    try:
        checkpoint = AnchorCheckpoint(
            tenant_id=checkpoint_mapping["tenant_id"],
            chain_head_hash=checkpoint_mapping["chain_head_hash"],
            record_count=checkpoint_mapping["record_count"],
            window_start=_parse_timestamp(checkpoint_mapping["window_start"]),
            window_end=_parse_timestamp(checkpoint_mapping["window_end"]),
            chain_schema_version=checkpoint_mapping["chain_schema_version"],
            prev_anchor_root=checkpoint_mapping["prev_anchor_root"],
            anchor_format=checkpoint_mapping["anchor_format"],
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise _AnchorUnreadableError(REASON_CHECKPOINT_INVALID) from exc

    if checkpoint.tenant_id != tenant_id:
        # The key prefix said one tenant and the signed content says another. Whatever produced
        # that, this envelope is not this tenant's evidence.
        raise _AnchorUnreadableError(REASON_CHECKPOINT_TENANT_MISMATCH)

    return _ParsedAnchor(
        checkpoint=checkpoint,
        checkpoint_bytes=preimage,
        stored_root=stored_root,
        signature=AnchorSignature(
            algorithm=signature_mapping["algorithm"],
            key_id=signature_mapping["key_id"],
            value=signature_mapping["value"],
        ),
    )


def _parse_timestamp(raw: Any) -> datetime:
    """Parse a stored ISO timestamp. `AnchorCheckpoint` refuses a naive one; this refuses a non-str."""
    if not isinstance(raw, str):
        raise ValueError(f"window bound must be an ISO-8601 string, got {type(raw).__name__}")
    return datetime.fromisoformat(raw)


# =================================================================================================
# The verification itself
# =================================================================================================


async def verify_latest_anchor(
    *,
    tenant_id: str,
    store: AnchorStore,
    verifier: AnchorSignatureVerifier,
    key_probe: AnchorKeyProbe,
    records: ChainRecordSource,
) -> AnchorVerificationOutcome:
    """Compare one tenant's live Postgres chain against its latest signature-verified anchor.

    FLAG OFF (the default, everywhere, today) — returns `STATUS_DISABLED` immediately: the store is
    never listed, the probe is never called, and no database connection is opened. Proven by
    exploding seams plus a filesystem probe, not by this docstring.

    FLAG ON — the sequence, in this order, with the first failure winning:

      1. **Corroborate the listing.** `store.list_keys()` and `key_probe.probe_keys()`, both
         restricted to `f"{tenant_id}/"`. Unavailable or disagreeing => `STORE_LISTING_SUSPECT`,
         and no verdict about the database is issued. See the module docstring for why a
         disagreement can never be resolved in favour of either side.
      2. **Select and read.** Lexical max of the agreed set — leg 1's key shape
         (`<tenant>/<compact UTC window_end>-<root>.anchor.json`) makes lexical order chronological
         order. Empty set => `NO_ANCHOR` (explicitly NOT clean). Unreadable key =>
         `STORE_LISTING_SUSPECT`.
      3. **Parse** the envelope, fail-closed on shape/format/tenant => `ANCHOR_UNREADABLE`.
      4. **Verify the signature** over the checkpoint bytes. Rejected, or a verifier that raises,
         => `SIGNATURE_INVALID`. Everything after this point rests on a checkpoint whose
         authenticity was established here; everything before it is untrusted input.
      5. **Recompute the anchored root** as `sha256(checkpoint bytes)` and compare the envelope's
         stored `root` field against it. The field is NOT the comparison basis (it is unsigned) —
         it is a tamper indicator, and a mismatch is `DIVERGENCE`
         (`ANCHOR_ROOT_FIELD_INCONSISTENT`), because in a WORM store that field cannot change.
      6. **Read the database** => any failure is `DB_ERROR` (never clean).
      7. **Recompute the database root** over the anchor's window: the first `record_count` records
         from genesis, through `audit_anchor.checkpoint_for_chain` — the writer's own derivation,
         not a second copy of it — using the ANCHOR's `chain_schema_version` and `tenant_id` so
         that the comparison isolates chain CONTENT. A fork, too few records, or a broken link is
         `DIVERGENCE` in its own right: those states cannot even produce the window that was
         attested.
      8. **Compare roots.** Equal => `MATCH`. Otherwise `DIVERGENCE`, reporting both roots, both
         heads and both counts — hashes and counts only, never record content.

    A database that has GROWN since the anchor is not a divergence: the window is a genesis-anchored
    PREFIX of `record_count` records, and an append-only chain is supposed to grow.
    """
    if not anchor_verification_enabled():
        return _emit(
            AnchorVerificationOutcome(
                status=STATUS_DISABLED, tenant_id=tenant_id, reason=REASON_VERIFICATION_DISABLED
            )
        )

    prefix = f"{tenant_id}/"
    try:
        listed = frozenset(key for key in store.list_keys() if key.startswith(prefix))
    except Exception as exc:  # noqa: BLE001 — any listing failure is SUSPECT, never clean
        return _emit(
            AnchorVerificationOutcome(
                status=STATUS_STORE_LISTING_SUSPECT,
                tenant_id=tenant_id,
                reason=REASON_LISTING_UNAVAILABLE,
                error_type=type(exc).__name__,
            )
        )
    try:
        probed = frozenset(key for key in key_probe.probe_keys(tenant_id) if key.startswith(prefix))
    except Exception as exc:  # noqa: BLE001 — an uncorroborated listing may not produce a verdict
        return _emit(
            AnchorVerificationOutcome(
                status=STATUS_STORE_LISTING_SUSPECT,
                tenant_id=tenant_id,
                reason=REASON_PROBE_UNAVAILABLE,
                error_type=type(exc).__name__,
            )
        )

    if listed != probed:
        return _emit(
            AnchorVerificationOutcome(
                status=STATUS_STORE_LISTING_SUSPECT,
                tenant_id=tenant_id,
                reason=REASON_LISTING_PROBE_DISAGREEMENT,
                anchor_key=max(listed | probed),
                listing_disagreement=tuple(sorted(listed ^ probed)),
            )
        )

    if not listed:
        return _emit(AnchorVerificationOutcome(status=STATUS_NO_ANCHOR, tenant_id=tenant_id))

    selected_key = max(listed)
    try:
        payload = store.get(selected_key)
    except Exception as exc:  # noqa: BLE001 — listed-but-unreadable is store misbehaviour
        return _emit(
            AnchorVerificationOutcome(
                status=STATUS_STORE_LISTING_SUSPECT,
                tenant_id=tenant_id,
                reason=REASON_SELECTED_ANCHOR_UNREADABLE,
                anchor_key=selected_key,
                error_type=type(exc).__name__,
            )
        )

    try:
        anchor = _parse_envelope(payload, tenant_id=tenant_id)
    except _AnchorUnreadableError as exc:
        return _emit(
            AnchorVerificationOutcome(
                status=STATUS_ANCHOR_UNREADABLE,
                tenant_id=tenant_id,
                reason=exc.reason,
                anchor_key=selected_key,
            )
        )

    try:
        signature_ok = verifier.verify(anchor.checkpoint_bytes, anchor.signature)
    except Exception as exc:  # noqa: BLE001 — a verifier that raises has NOT verified
        return _emit(
            AnchorVerificationOutcome(
                status=STATUS_SIGNATURE_INVALID,
                tenant_id=tenant_id,
                reason=REASON_SIGNATURE_VERIFIER_FAILED,
                anchor_key=selected_key,
                signature_key_id=anchor.signature.key_id,
                error_type=type(exc).__name__,
            )
        )
    if not signature_ok:
        return _emit(
            AnchorVerificationOutcome(
                status=STATUS_SIGNATURE_INVALID,
                tenant_id=tenant_id,
                reason=REASON_SIGNATURE_REJECTED,
                anchor_key=selected_key,
                signature_key_id=anchor.signature.key_id,
            )
        )

    # RECOMPUTED from the signature-verified checkpoint. This — never `envelope["root"]` — is the
    # basis every comparison below is made against.
    anchor_root = hashlib.sha256(anchor.checkpoint_bytes).hexdigest()
    anchored_count = anchor.checkpoint.record_count
    anchored_head = anchor.checkpoint.chain_head_hash

    def _partial(**overrides: Any) -> AnchorVerificationOutcome:
        base: dict[str, Any] = {
            "tenant_id": tenant_id,
            "anchor_key": selected_key,
            "anchor_root": anchor_root,
            "anchored_record_count": anchored_count,
            "anchored_head_hash": anchored_head,
            "signature_key_id": anchor.signature.key_id,
        }
        base.update(overrides)
        return AnchorVerificationOutcome(**base)

    if anchor.stored_root != anchor_root:
        return _emit(_partial(status=STATUS_DIVERGENCE, reason=REASON_ANCHOR_ROOT_FIELD_INCONSISTENT))

    try:
        snapshot = await records.read_chain(tenant_id)
    except Exception as exc:  # noqa: BLE001 — an unreadable chain is an UNVERIFIED chain
        return _emit(
            _partial(
                status=STATUS_DB_ERROR,
                reason=REASON_DATABASE_UNREACHABLE,
                error_type=type(exc).__name__,
            )
        )

    if snapshot.fork_at_prev_hash is not None:
        return _emit(
            _partial(
                status=STATUS_DIVERGENCE,
                reason=REASON_CHAIN_FORK,
                database_record_count=snapshot.total_records,
                database_head_hash=snapshot.fork_at_prev_hash,
            )
        )
    if len(snapshot.records) < anchored_count:
        return _emit(
            _partial(
                status=STATUS_DIVERGENCE,
                reason=REASON_RECORD_COUNT_SHORTFALL,
                database_record_count=len(snapshot.records),
                database_head_hash=(
                    snapshot.records[-1].record_hash if snapshot.records else GENESIS_PREV_HASH
                ),
            )
        )

    window = snapshot.records[:anchored_count]
    try:
        recomputed = checkpoint_for_chain(
            window,
            tenant_id=anchor.checkpoint.tenant_id,
            chain_schema_version=anchor.checkpoint.chain_schema_version,
            prev_anchor_root=anchor.checkpoint.prev_anchor_root,
        )
    except AnchorChainDiscontinuityError:
        return _emit(
            _partial(
                status=STATUS_DIVERGENCE,
                reason=REASON_CHAIN_DISCONTINUITY,
                database_record_count=len(snapshot.records),
            )
        )

    database_root = checkpoint_root(recomputed)
    common: dict[str, Any] = {
        "database_root": database_root,
        "database_record_count": len(snapshot.records),
        "database_head_hash": recomputed.chain_head_hash,
    }
    if database_root != anchor_root:
        return _emit(_partial(status=STATUS_DIVERGENCE, reason=REASON_ROOT_MISMATCH, **common))
    return _emit(_partial(status=STATUS_MATCH, **common))


# =================================================================================================
# Continuous mode — explicitly invoked, never auto-started
# =================================================================================================


async def run_verification_loop(
    *,
    tenant_id: str,
    store: AnchorStore,
    verifier: AnchorSignatureVerifier,
    key_probe: AnchorKeyProbe,
    records: ChainRecordSource,
    interval_seconds: float,
    max_iterations: int | None = None,
    on_outcome: Callable[[AnchorVerificationOutcome], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[AnchorVerificationOutcome]:
    """Run :func:`verify_latest_anchor` every `interval_seconds`. NOTHING starts this on its own.

    There is no scheduler here, no background task, no module-level `create_task`, and no importer
    anywhere in `src/maezo/` (both facts are AST-pinned by tests). "Continuous" is the shape of this
    coroutine; making it actually run continuously is a composition-root decision an owner takes.

    Behaviour worth stating because it is a choice, not an accident:

      - **Flag off at entry => exactly one `DISABLED` outcome and an immediate return.** The loop
        does not spin doing nothing (a disabled feature should not own a task), and `sleep` is
        never awaited. A flag flipped off MID-flight also stops the loop, on the next iteration's
        own check.
      - **A divergence does NOT stop the loop.** A divergence does not resolve itself, and stopping
        would silence the alarm after the first page — precisely when a human most needs the next
        data point. Suppression is an alerting-sink concern, deliberately not decided here.
      - **`on_outcome` failures PROPAGATE.** A verification job whose alarm channel is broken must
        not keep running quietly green; a raised sink stops the loop loudly.
      - **`sleep` is injected** so tests drive many iterations without wall-clock time, and so an
        owner can substitute a jittered sleep without touching this file.

    Args:
        interval_seconds: Must be > 0. A zero/negative interval is a busy loop against Postgres,
            refused at the door rather than discovered in production.
        max_iterations: `None` runs until the flag goes off or the sink raises.
    """
    if interval_seconds <= 0:
        raise ValueError(
            f"interval_seconds must be > 0, got {interval_seconds!r} — a non-positive interval is "
            "a busy loop reading the whole audit chain as fast as the database will answer"
        )
    if max_iterations is not None and max_iterations < 1:
        raise ValueError(f"max_iterations must be >= 1 when given, got {max_iterations!r}")

    if not anchor_verification_enabled():
        return [
            _emit(
                AnchorVerificationOutcome(
                    status=STATUS_DISABLED, tenant_id=tenant_id, reason=REASON_VERIFICATION_DISABLED
                )
            )
        ]

    outcomes: list[AnchorVerificationOutcome] = []
    iteration = 0
    while True:
        outcome = await verify_latest_anchor(
            tenant_id=tenant_id,
            store=store,
            verifier=verifier,
            key_probe=key_probe,
            records=records,
        )
        outcomes.append(outcome)
        if on_outcome is not None:
            on_outcome(outcome)
        if outcome.status == STATUS_DISABLED:
            return outcomes  # the flag went off mid-flight; stop rather than poll a disabled job
        iteration += 1
        if max_iterations is not None and iteration >= max_iterations:
            return outcomes
        await sleep(interval_seconds)


# =================================================================================================
# Offline CLI — `python -m maezo.gateway.audit_anchor_verify ...`
# =================================================================================================

#: Env var the CLI reads the LABELED FAKE verifier's secret from, by default. A secret passed as an
#: argv value would land in `ps` output and shell history.
DEFAULT_FAKE_SECRET_ENV: Final[str] = "MAEZO_AUDIT_ANCHOR_FAKE_SECRET"

#: Exit code for a CLI USAGE/wiring refusal (missing secret, unusable store root). Distinct from
#: every verdict code so "I could not run the check" is never read as the check's answer, and
#: distinct from argparse's own 2.
EXIT_CLI_REFUSED: Final[int] = 3


def _print_verdict_line(payload: dict[str, Any]) -> None:
    """Write the machine-readable verdict as the LAST LINE of stdout — see :func:`_cli`."""
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))  # noqa: T201 — CLI output


def _cli(
    argv: Sequence[str] | None = None,
    *,
    record_source_factory: Callable[[str], ChainRecordSource] = PostgresChainRecordSource,
) -> int:
    """Verify one tenant's chain against its latest anchor once; print JSON; exit per outcome.

    Mirrors `audit_postgres._cli`'s shape (`--dsn`, `--tenant`, JSON on stdout, status in the exit
    code) and adds `--store-root`. Runs without the docker engine stack: a Postgres DSN and a
    directory are all it needs, which is what makes it usable from a CI job and from leg 3's drills.

    **OUTPUT CONTRACT: the verdict is the LAST LINE of stdout, and it is exactly one line.**
    Unlike `audit_postgres._cli` (which pretty-prints with `indent=2`), this command also EMITS a
    structured event for its own verdict, and the repo's logging wiring
    (`platform/observability.py` — `structlog.PrintLoggerFactory`) renders events to **stdout**.
    A multi-line JSON document interleaved with console log lines is not parseable, so the verdict
    is compact single-line JSON, emitted after the event: `... | tail -n 1 | jq` is the contract,
    and a test pins it. Routing the verdict to stderr instead would collide with the far more
    common convention that stderr is diagnostics; reconfiguring structlog from inside a library
    module would be a global side effect this file has no business taking.

    **The only verifier this CLI can wire is the LABELED FAKE** (`LabeledFakeKmsAnchorSigner`,
    whose `verify` requires all three synthetic labels). That is deliberate. A real anchor is
    verified with a KMS/HSM key, which is owner wiring injected programmatically at a composition
    root; a `--kms-key-arn` flag here would invite exactly the masquerade leg 1's three labels
    exist to make impossible, and a CLI that could be pointed at a real key would become the
    easiest place to point it at a fake one. Without a secret the CLI REFUSES (exit
    :data:`EXIT_CLI_REFUSED`) — it never falls back to "skip signature verification", because an
    unverified anchor is not evidence and a clean-looking line of JSON that skipped the signature
    is worse than no output.

    `record_source_factory` is a seam, defaulted to the real Postgres reader: it lets the CLI's own
    contract — argument parsing, JSON shape, exit codes — be proven offline, since otherwise every
    test of this function would need a live database and the exit codes leg 3 depends on would be
    pinned nowhere.
    """
    parser = argparse.ArgumentParser(
        description="Compare a tenant's Postgres audit chain against its latest external anchor."
    )
    parser.add_argument("--dsn", required=True, help="Postgres DSN (postgresql[+asyncpg]://...)")
    parser.add_argument("--tenant", required=True, help="Tenant id (schema name)")
    parser.add_argument("--store-root", required=True, help="Root directory of the anchor store")
    parser.add_argument(
        "--fake-verifier-key-label",
        required=True,
        help="Label of the LABELED FAKE verification key (never a real KMS key reference)",
    )
    parser.add_argument(
        "--fake-verifier-secret-env",
        default=DEFAULT_FAKE_SECRET_ENV,
        help=f"Env var holding the labeled-fake secret (default: {DEFAULT_FAKE_SECRET_ENV})",
    )
    args = parser.parse_args(argv)

    secret = os.environ.get(args.fake_verifier_secret_env, "")
    if not secret:
        _print_verdict_line(
            {
                "status": "CLI_REFUSED",
                "reason": "FAKE_VERIFIER_SECRET_ABSENT",
                "detail": (
                    f"{args.fake_verifier_secret_env} is unset or empty — refusing to run "
                    "without a verifier (an unverified anchor is not evidence)"
                ),
            }
        )
        return EXIT_CLI_REFUSED

    root = Path(args.store_root)
    outcome = asyncio.run(
        verify_latest_anchor(
            tenant_id=args.tenant,
            store=LabeledFakeWormAnchorStore(root),
            verifier=LabeledFakeKmsAnchorSigner(
                key_label=args.fake_verifier_key_label, secret=secret.encode("utf-8")
            ),
            key_probe=FilesystemAnchorKeyProbe(root),
            records=record_source_factory(args.dsn),
        )
    )
    _print_verdict_line(outcome.to_json_mapping())
    return outcome.exit_code


if __name__ == "__main__":
    sys.exit(_cli())
