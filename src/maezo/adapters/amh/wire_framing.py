"""Pluggable wire-framing codec seam for the AMH boundary — DARK BUILD, fails closed (MZO-050b).

**What this module is.** The one layer phase A explicitly could not ship (see the package docstring's
item 4, "Undeclared wire framing (blocking)"): a `WireFramingCodec` Protocol that turns raw Kafka
message bytes into `(schema_version_id, avro_payload_bytes)`, plus a registry/factory
(`select_wire_framing_codec`) that picks an implementation from a `wire_framing` declaration read from
the pinned manifest (`config/integrations/amh/contracts.lock.json`). Today that declaration DOES NOT
EXIST — verified by test against the real pin committed to this repo — so the factory refuses closed
with a typed `WireFramingUndeclaredError` rather than guess.

**The point of this build.** It converts MZO-050a's PROJECT-blocker ("phase B cannot be built at all")
into a MANIFEST-DECLARATION-blocker ("phase B can be built the moment AMH publishes the missing
declaration"). The seam, the fail-closed refusal, and a CANDIDATE implementation all exist and are
tested TODAY — and none of them decode a single byte of production traffic, because nothing in this
package is wired into a consumer, a broker client, or a `WorkItemSource`/`OutcomePublisherPort`
implementation. That remains MZO-060-gated (durable inbox for `ack`); see the package docstring.

**Why "undeclared" is a fact, not a guess.** ADR-0037's canonical baseline
(`docs/adr/0037-amh-compatibility-boundary-canonical-contracts.md`) freezes the 28 envelope fields and
states plainly: "Este ADR não inventa nenhum campo de wire além deste baseline." The Avro/Glue framing
that precedes the Avro-encoded envelope on the wire — the magic byte(s) and schema-version-id layout —
is exactly such an undeclared field: `docs/evidence-ledger.md` row `mzo-050a` records it verbatim as
"o framing Avro/Glue (magic byte + schema-version id) que NÃO está declarado em NENHUM lugar do
contrato pinado — exige declaração da AMH, adivinhar seria fabricação." Guessing the layout and shipping
it as fact would be exactly that fabrication. This module never does that: `select_wire_framing_codec`
reads ONLY a `wire_framing` key in the pin file and refuses when it is absent — see
`WireFramingUndeclaredError`.

**The CANDIDATE codec exists to be REVIEWED, not to be trusted.** `GlueSchemaRegistryCandidateCodec`
implements the byte layout AWS's own open-source Glue Schema Registry client library documents (cited
in its docstring) — 1-byte header version, 1-byte compression, 16-byte schema-version UUID. It is
registered under the vocabulary token `"glue-schema-registry-v1"`, which is THIS REPO'S PROPOSAL for
what the pin's `wire_framing` key should say once AMH declares it — not AMH's word, and never selected
by default. `select_wire_framing_codec` can only ever return it when a pin explicitly carries
`"wire_framing": "glue-schema-registry-v1"`, which the real pinned manifest does not (and this repo
never edits the AMH-owned pin to make it so — ADR-0037 immutable prohibition #4). The exact YAML this
repo proposes AMH add to `schemas/contracts/maezo/v1/contract-manifest.yaml`, plus the PR body proposing
it, live in the mission's scratchpad output — never in this repository's version control.

**Decoded, not interpreted.** Like `maezo.adapters.amh.mapping`, this seam does no Avro binary decoding
— `avro_payload` stays opaque `bytes` on `DecodedFrame`. The framing header itself carries no Avro
content (it is fixed-width bytes plus a UUID), so stripping it off needs no Avro library at all. This is
also why this module adds NO runtime dependency: `fastavro` decodes the Avro BODY the header points at,
which is out of scope for a framing-only seam and would be exactly the "guess more than the frame needs"
mistake this build is designed to avoid.

**Fail-closed against an unknown schema version, too.** Even once a frame parses structurally,
`GlueSchemaRegistryCandidateCodec.decode` refuses one whose 16-byte schema-version id is NOT one of the
pin's three published Glue `SchemaVersionIds` (`AmhContractPin.glue.schema_version_ids`). A structurally
well-formed frame naming an unpinned schema is not evidence the frame is trustworthy — it is evidence
the wire diverged from what this adapter was told to expect, and ADR-0037 XRD-04's fail-closed posture
("schema ausente ... falham fail-closed antes de merge/deploy de adapter") applies here exactly as it
does to the envelope pin itself.

**No adapter exception may cross a port boundary (`maezo.ports.errors`).** Every exception raised here
is an `AmhAdapterError` subclass. `decode`/`encode` convert every reachable `TypeError`/`ValueError`/
`AttributeError` at the boundary instead of letting it escape — this package has been bitten by exactly
that escape shape three times before (see `maezo.adapters.amh.mapping`'s module docstring, decisions
7-13), and this module applies the same discipline from its first commit rather than discovering the
gaps by incident.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable

from maezo.adapters.amh.contract import AmhAdapterError, AmhContractPin

#: Sentinel: no `wire_framing` key exists in the pinned manifest at all — distinct from a key that
#: exists with value `None` (JSON `null`), which `dict.get`'s own missing-default would otherwise
#: produce identically. `select_wire_framing_codec` needs this distinction to render the correct one
#: of `WireFramingUndeclaredError`'s two messages ("no key is present" vs "key is present but not
#: usable"). Declared here, ahead of `WireFramingUndeclaredError`, because it is also that
#: exception's `__init__` default parameter value — a forward reference would not resolve at class
#: body evaluation time.
_WIRE_FRAMING_KEY_ABSENT: Final[object] = object()

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WireFramingError(AmhAdapterError):
    """Base class for every failure in `maezo.adapters.amh.wire_framing`.

    Deterministic by construction — an undeclared manifest, an unregistered declaration and a
    malformed frame are all facts about the BYTES or the DECLARATION, never a transient condition — so
    a future port implementation built on this seam must map every subclass to
    `PortFailureReason.CONTRACT_VIOLATION` and quarantine, never retry.
    """


class WireFramingUndeclaredError(WireFramingError):
    """The pinned AMH manifest does not declare a usable `wire_framing`.

    **Two distinct causes, two distinct messages.** `select_wire_framing_codec` raises this both when
    the `wire_framing` key is ABSENT from the manifest and when it is PRESENT but not a usable
    declaration (not a string, or a blank one) — those are different facts about the manifest, and
    conflating them into one wording ("no key is present") was itself a bug when the key WAS present:
    it told a reader to go looking for a key that was already there. `declared` (default: the
    `_WIRE_FRAMING_KEY_ABSENT` sentinel, meaning "absent") carries which case this instance is, and
    the message echoes `type(declared)` in the present-but-unusable case so a reader sees exactly what
    kind of value the manifest actually held.

    **The manifest-declaration-blocker, made visible and testable.** ADR-0037 forbids inventing wire
    shape (see the module docstring's "Why 'undeclared' is a fact, not a guess"), and the Avro/Glue
    frame layout is exactly such an undeclared field today. `select_wire_framing_codec` raises this
    INSTEAD of guessing, so the blocker is a red test against the real pin rather than a silent wrong
    decode waiting in production.
    """

    def __init__(self, *, manifest_path: Path, declared: object = _WIRE_FRAMING_KEY_ABSENT) -> None:
        self.manifest_path = manifest_path
        self.declared = declared
        if declared is _WIRE_FRAMING_KEY_ABSENT:
            detail = f"no {WIRE_FRAMING_MANIFEST_KEY!r} key is present"
        else:
            detail = (
                f"the {WIRE_FRAMING_MANIFEST_KEY!r} key is present but not usable (a non-empty "
                f"string is required; got {type(declared).__name__})"
            )
        super().__init__(
            f"AMH wire framing is UNDECLARED in the pinned manifest ({manifest_path}); {detail}. "
            "Refusing to guess the Avro/Glue frame layout (ADR-0037: this repo never invents a wire "
            "field outside the frozen baseline) — this is a manifest-declaration-blocker on the AMH "
            "steward side, not a Maezo defect. See GlueSchemaRegistryCandidateCodec for the CANDIDATE "
            "framing proposed pending AMH ratification (never selected by default)."
        )


class WireFramingUnknownDeclarationError(WireFramingError):
    """The pinned manifest declares a `wire_framing` value with no codec registered for it.

    Distinct from `WireFramingUndeclaredError`: here the manifest DID declare something — this is a
    forward refusal for a declaration value this build predates (e.g. a future AMH publication using a
    different vocabulary token than the CANDIDATE one this build registers), never a silent
    best-effort decode under the nearest match.
    """

    def __init__(self, declared: str, *, known: tuple[str, ...]) -> None:
        self.declared = declared
        self.known = known
        super().__init__(
            f"the pinned manifest declares {WIRE_FRAMING_MANIFEST_KEY}={declared!r}, which has no "
            f"registered codec (known: {list(known)})"
        )


class WireFramingDecodeError(WireFramingError):
    """A raw Kafka message could not be decoded by the selected wire-framing codec.

    Covers: input that is not `bytes`/`bytearray` at all, a frame shorter than the fixed header, an
    unrecognised header-version or compression byte, and — fail-closed against an unpinned schema — a
    structurally valid 16-byte schema-version id that is not one of the pin's published Glue
    SchemaVersionIds. Deterministic: identical bytes fail identically on retry, so a caller must
    quarantine, never retry.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"AMH wire frame refused: {reason}")


class WireFramingEncodeError(WireFramingError):
    """A `DecodedFrame` could not be encoded to wire bytes (test-support path only).

    Only reachable from `WireFramingCodec.encode`, which exists so tests can construct synthetic
    frames deterministically — nothing in this repository publishes through it; egress framing is
    unbuilt and MZO-060 gates a real consumer/publisher.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"AMH wire frame could not be encoded: {reason}")


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class DecodedFrame:
    """The framing layer's result: which pinned schema version produced this Avro body, plus the body
    itself, still opaque. `avro_payload` is NEVER decoded here — see the module docstring's "Decoded,
    not interpreted" note. Binary Avro decode (`fastavro`) is out of scope for this seam.
    """

    schema_version_id: str
    avro_payload: bytes


@runtime_checkable
class WireFramingCodec(Protocol):
    """Structural seam: raw Kafka message bytes <-> `(schema_version_id, avro_payload_bytes)`.

    `encode` is TEST SUPPORT ONLY — it exists so a test can build a synthetic on-wire frame without
    hand-rolling the byte layout, not because anything in this repository publishes through it (egress
    framing/publish is unbuilt; MZO-060 gates a real consumer/publisher). Every implementation MUST
    raise only `WireFramingDecodeError`/`WireFramingEncodeError` — see `maezo.ports.errors`: "No
    adapter exception may cross a port boundary."
    """

    def decode(self, raw: bytes) -> DecodedFrame:
        """Raw Kafka message value -> `DecodedFrame`. Fail-closed on anything malformed or unpinned."""
        ...

    def encode(self, frame: DecodedFrame) -> bytes:
        """The reverse of `decode` — builds a synthetic on-wire frame for a test."""
        ...


# ---------------------------------------------------------------------------
# Manifest declaration + factory
# ---------------------------------------------------------------------------

#: The manifest key `select_wire_framing_codec` looks for. **CANDIDATE key name** — this repo's
#: proposed spelling, not an AMH declaration (none exists today). Proposed to AMH in the mission's
#: scratchpad addendum (YAML block + PR body); never written into the committed pin by this repo
#: (ADR-0037 immutable prohibition #4 — Maezo never edits the AMH-owned contract).
WIRE_FRAMING_MANIFEST_KEY: Final[str] = "wire_framing"


def _read_manifest_declaration(pin: AmhContractPin) -> object:
    """Best-effort re-read of `pin.source_path`'s top-level `wire_framing` key.

    Returns `_WIRE_FRAMING_KEY_ABSENT` when the key genuinely is not present in the manifest, or the
    second read could not be trusted at all (see below) — never when it merely holds an unusable
    value. Otherwise returns whatever JSON value the key holds VERBATIM, including `None` for an
    explicit `"wire_framing": null`: the sentinel exists precisely so that case stays distinguishable
    from "no key at all", since `dict.get`'s own missing-default would otherwise collapse both to the
    identical Python `None`. `select_wire_framing_codec` relies on this distinction for its two
    differently-worded refusals.

    **Caller shape is checked first, before either read is attempted.** `pin` must be the verified
    `AmhContractPin` `load_contract_pin` returns, with a `pathlib.Path` `source_path` — anything else
    (a bare `None`/string/mapping in place of `pin`, or a pin whose `source_path` was replaced with a
    non-`Path`) raises `WireFramingError` here rather than reaching the read below and surfacing a
    bare `AttributeError` from `pin.source_path.read_text(...)`. `AmhContractPin` carries no runtime
    field validation of its own (same "annotation is not validation" posture as `mapping.py`), so this
    guard is this module's only defense against that shape.

    `pin` was already loaded and strictly validated by `load_contract_pin` before reaching here, so
    `pin.source_path` names known-good JSON — this re-reads it rather than threading a raw dict through
    `AmhContractPin`, because `contract.py`'s public surface is closed and exhaustively fuzzed
    (`tests/unit/adapters/amh/test_no_foreign_exception_escapes.py`); widening it for one
    not-yet-existent key would require extending that harness in lockstep for a field the pin does not
    carry today. Any failure on this second read (the file vanished, changed to non-UTF-8, or stopped
    parsing between the two reads) is treated as "absent" rather than raised: a `wire_framing` this
    function cannot confirm is not one this module may act on, so the fail-closed outcome is identical
    either way — and no bare `OSError`/`ValueError`/`RecursionError`/`AttributeError`/`TypeError`
    escapes this boundary.

    **The divergence this design accepts.** Between `load_contract_pin`'s read (which produced `pin`)
    and this function's re-read, the file at `pin.source_path` can change to a DIFFERENT, STILL-VALID
    pin — not just vanish or corrupt. `load_contract_pin` validated one snapshot of the file;
    `select_wire_framing_codec` then trusts whatever `wire_framing` value a LATER snapshot declares,
    read here. Two reads of one file at two different instants are two sources of truth, not one: a
    caller holding a `pin` object built from snapshot A can end up selecting a codec that was only
    ever declared in snapshot B. This is a real race between two reads of the same path, not a
    hypothetical — neither this function nor `load_contract_pin` locks, hashes, or otherwise ties the
    second read back to the first.

    **Phase-B intent (not implemented in this dark build).** The fix is to stop re-reading entirely:
    thread `wire_framing` through `contract.py` as a validated field on `AmhContractPin` itself,
    populated once by `load_contract_pin` from the SAME parse it already performs, and delete this
    second read. That belongs at activation, when `contract.py`'s closed, exhaustively-fuzzed surface
    is next deliberately extended for phase B — not smuggled into this seam-only commit. Recorded here
    so the divergence above is not silently carried forward as "acceptable" past that point.
    """
    if not isinstance(pin, AmhContractPin):
        raise WireFramingError(
            "select_wire_framing_codec requires the verified AmhContractPin load_contract_pin "
            f"returns; got {type(pin).__name__}"
        )
    if not isinstance(pin.source_path, Path):
        raise WireFramingError(
            "the pin's source_path must be a pathlib.Path (load_contract_pin's own invariant); got "
            f"{type(pin.source_path).__name__}"
        )
    try:
        raw = json.loads(pin.source_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError, AttributeError, TypeError):
        return _WIRE_FRAMING_KEY_ABSENT
    if not isinstance(raw, dict):
        return _WIRE_FRAMING_KEY_ABSENT
    return raw.get(WIRE_FRAMING_MANIFEST_KEY, _WIRE_FRAMING_KEY_ABSENT)


def select_wire_framing_codec(pin: AmhContractPin) -> WireFramingCodec:
    """Select the `WireFramingCodec` the pinned manifest declares. FAILS CLOSED — never guesses.

    Args:
        pin: the verified pin (`maezo.adapters.amh.contract.load_contract_pin`).

    Returns:
        The codec instance for the declared `wire_framing` vocabulary token.

    Raises:
        WireFramingError: `pin` is not the verified `AmhContractPin` `load_contract_pin` returns, or
            its `source_path` is not a `pathlib.Path`.
        WireFramingUndeclaredError: the pin carries no `wire_framing` key, or the key is present but
            not a non-empty string. This is the state of the REAL pinned manifest today.
        WireFramingUnknownDeclarationError: the pin declares a `wire_framing` value with no codec
            registered for it.
    """
    declared = _read_manifest_declaration(pin)
    if declared is _WIRE_FRAMING_KEY_ABSENT:
        raise WireFramingUndeclaredError(manifest_path=pin.source_path)
    if not isinstance(declared, str) or not declared.strip():
        raise WireFramingUndeclaredError(manifest_path=pin.source_path, declared=declared)
    factory = _WIRE_FRAMING_CODECS.get(declared)
    if factory is None:
        raise WireFramingUnknownDeclarationError(declared, known=tuple(sorted(_WIRE_FRAMING_CODECS)))
    return factory(pin)


# ---------------------------------------------------------------------------
# CANDIDATE codec: AWS Glue Schema Registry SerDe framing
# ---------------------------------------------------------------------------

#: Byte layout constants — all CANDIDATE. See `GlueSchemaRegistryCandidateCodec` for the primary
#: source citation these values are drawn from verbatim.
_HEADER_VERSION_BYTE: Final[int] = 3
_COMPRESSION_NONE_BYTE: Final[int] = 0
_COMPRESSION_ZLIB_BYTE: Final[int] = 5
_HEADER_VERSION_SIZE: Final[int] = 1
_COMPRESSION_BYTE_SIZE: Final[int] = 1
_SCHEMA_VERSION_ID_SIZE: Final[int] = 16
_HEADER_SIZE: Final[int] = _HEADER_VERSION_SIZE + _COMPRESSION_BYTE_SIZE + _SCHEMA_VERSION_ID_SIZE

#: **CANDIDATE vocabulary token** — this repo's proposal for the pin's `wire_framing` VALUE, mirroring
#: `WIRE_FRAMING_MANIFEST_KEY`'s proposed KEY. Neither is AMH's word; both are named CANDIDATE
#: everywhere they appear, per the mission that added this file.
GLUE_SCHEMA_REGISTRY_CANDIDATE_WIRE_FRAMING_VALUE: Final[str] = "glue-schema-registry-v1"


@dataclass(frozen=True, slots=True, kw_only=True)
class GlueSchemaRegistryCandidateCodec:
    """CANDIDATE `WireFramingCodec` for the AWS Glue Schema Registry SerDe wire framing. NOT ratified.

    **CANDIDATE — this class name, this docstring, and every exception it raises say so on purpose.**
    Nothing in `config/integrations/amh/contracts.lock.json` declares that AMH's producers actually use
    this framing; it is this repo's best-evidence proposal for what the eventual `wire_framing`
    declaration will say, built from AWS's OWN documentation of its Glue Schema Registry client
    library — not from AMH, and not guessed past that citation.

    **Primary source (byte layout).** AWS Glue Schema Registry client library,
    `AWSSchemaRegistryConstants.java`:
    https://github.com/awslabs/aws-glue-schema-registry/blob/master/common/src/main/java/com/amazonaws/services/schemaregistry/utils/AWSSchemaRegistryConstants.java
    Verbatim javadoc from that file: "A buffer is allocated for the serialized message. A header of 18
    bytes is written. Byte 0 is an 8 bit version number. Byte 1 is the compression. Byte 2-17 is a 128
    bit UUID representing the schema-version-id." The same file defines `HEADER_VERSION_BYTE = 3`,
    `COMPRESSION_DEFAULT_BYTE = 0` (no compression) and `COMPRESSION_BYTE = 5` (zlib) — the exact
    values used below.

    **Byte layout implemented here** (18-byte header + opaque Avro body):

        offset  size  field
        0       1     header version byte  (must equal 3 — refused otherwise)
        1       1     compression byte     (0 = none, decoded; 5 = zlib, REFUSED — no decompressor
                                             wired, so a compressed frame fails closed rather than
                                             silently returning compressed bytes as if they were Avro)
        2       16    schema-version id, a 16-byte UUID
        18      ...   the Avro-encoded envelope, returned verbatim as `DecodedFrame.avro_payload`

    **Fail-closed against an unpinned schema.** `decode` additionally refuses a structurally valid
    frame whose schema-version UUID is not one of the pin's three published Glue `SchemaVersionIds`
    (`AmhContractPin.glue.schema_version_ids`) — see the module docstring's "Fail-closed against an
    unknown schema version, too."

    **Selectable only through `select_wire_framing_codec`, and only when the pin says so.** This class
    has NO default-selection path: it is registered under
    `GLUE_SCHEMA_REGISTRY_CANDIDATE_WIRE_FRAMING_VALUE`, and the factory returns it only when the pin's
    `wire_framing` key equals that exact CANDIDATE token — never as a fallback, never unconditionally.
    """

    #: The pin's three published Glue SchemaVersionIds, frozen at construction time.
    _known_schema_version_ids: frozenset[str]

    @classmethod
    def from_pin(cls, pin: AmhContractPin) -> GlueSchemaRegistryCandidateCodec:
        """Build the codec from a verified pin's Glue registration section."""
        return cls(_known_schema_version_ids=frozenset(pin.glue.schema_version_ids.values()))

    def decode(self, raw: bytes) -> DecodedFrame:
        if not isinstance(raw, bytes | bytearray):
            raise WireFramingDecodeError(f"expected bytes or bytearray, got {type(raw).__name__}")
        if len(raw) < _HEADER_SIZE:
            raise WireFramingDecodeError(
                f"frame is {len(raw)} byte(s), shorter than the {_HEADER_SIZE}-byte CANDIDATE header"
            )
        version = raw[0]
        if version != _HEADER_VERSION_BYTE:
            raise WireFramingDecodeError(
                f"unsupported header version byte {version!r}; the CANDIDATE framing expects "
                f"{_HEADER_VERSION_BYTE!r}"
            )
        compression = raw[1]
        if compression == _COMPRESSION_ZLIB_BYTE:
            raise WireFramingDecodeError(
                "zlib-compressed frame (compression byte 5) — no decompressor is wired in this "
                "framing-only seam; refusing rather than returning compressed bytes as Avro"
            )
        if compression != _COMPRESSION_NONE_BYTE:
            raise WireFramingDecodeError(f"unrecognised compression byte {compression!r}")
        schema_id_bytes = bytes(raw[_HEADER_VERSION_SIZE + _COMPRESSION_BYTE_SIZE : _HEADER_SIZE])
        # `uuid.UUID(bytes=...)` cannot raise here: the slice above is exactly `_SCHEMA_VERSION_ID_SIZE`
        # (16) bytes by construction (the length guard above already refused anything shorter), and
        # `UUID`'s `bytes=` constructor accepts every 16-byte value — there is no bit pattern it
        # refuses. No `try` here is dead code, not a missing guard.
        schema_version_id = str(uuid.UUID(bytes=schema_id_bytes))
        if schema_version_id not in self._known_schema_version_ids:
            raise WireFramingDecodeError(
                "schema-version-id is not one of the pin's published Glue SchemaVersionIds — refusing "
                "an unknown schema version rather than decoding against one nobody published"
            )
        avro_payload = bytes(raw[_HEADER_SIZE:])
        return DecodedFrame(schema_version_id=schema_version_id, avro_payload=avro_payload)

    def encode(self, frame: DecodedFrame) -> bytes:
        if not isinstance(frame, DecodedFrame):
            raise WireFramingEncodeError(f"expected a DecodedFrame, got {type(frame).__name__}")
        try:
            schema_bytes = uuid.UUID(frame.schema_version_id).bytes
        except (ValueError, AttributeError, TypeError) as exc:
            # ValueError: malformed UUID string. AttributeError: `.replace` called on a non-str `hex`
            # argument inside `uuid.UUID.__init__`. TypeError: `hex=None` alongside every other
            # constructor argument absent. All three are real `uuid.UUID` raise shapes reachable from a
            # caller-supplied string — `DecodedFrame` is a plain frozen dataclass with NO runtime
            # validation, so `schema_version_id` can be anything a test constructs it with (same
            # "annotation is not validation" reasoning as `mapping.py` decision 5 applied to a
            # different field).
            raise WireFramingEncodeError(
                f"schema_version_id is not a valid UUID string: {type(exc).__name__}: {exc}"
            ) from None
        if not isinstance(frame.avro_payload, bytes | bytearray):
            raise WireFramingEncodeError(
                f"avro_payload must be bytes or bytearray, got {type(frame.avro_payload).__name__}"
            )
        header = bytes([_HEADER_VERSION_BYTE, _COMPRESSION_NONE_BYTE])
        return header + schema_bytes + bytes(frame.avro_payload)


#: The registry `select_wire_framing_codec` reads. `MappingProxyType` so a caller cannot mutate the
#: registered vocabulary in place (same posture as `AmhContractPin`'s read-only views).
_WIRE_FRAMING_CODECS: Final[Mapping[str, Callable[[AmhContractPin], WireFramingCodec]]] = MappingProxyType(
    {GLUE_SCHEMA_REGISTRY_CANDIDATE_WIRE_FRAMING_VALUE: GlueSchemaRegistryCandidateCodec.from_pin}
)


__all__ = [
    "GLUE_SCHEMA_REGISTRY_CANDIDATE_WIRE_FRAMING_VALUE",
    "WIRE_FRAMING_MANIFEST_KEY",
    "DecodedFrame",
    "GlueSchemaRegistryCandidateCodec",
    "WireFramingCodec",
    "WireFramingDecodeError",
    "WireFramingEncodeError",
    "WireFramingError",
    "WireFramingUndeclaredError",
    "WireFramingUnknownDeclarationError",
    "select_wire_framing_codec",
]
