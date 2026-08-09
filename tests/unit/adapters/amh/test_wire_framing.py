"""Unit tests for the MZO-050b wire-framing codec seam (`maezo.adapters.amh.wire_framing`).

Three things this suite proves, matching the mission's own acceptance criteria:

1. **The refusal is real, against the REAL pin.** `select_wire_framing_codec` on the actual committed
   `config/integrations/amh/contracts.lock.json` raises `WireFramingUndeclaredError` — the manifest
   genuinely does not declare `wire_framing` today. If AMH ever adds the declaration, THIS test is the
   one that goes red first, which is the point: the blocker is a live assertion, not a comment.
2. **The CANDIDATE codec round-trips synthetic frames** built from the pin's own three published Glue
   SchemaVersionIds, and REFUSES a syntactically valid frame naming a schema version nobody published.
3. **Malformed input never escapes as a bare stdlib exception.** Every hostile shape below is asserted
   to surface as `WireFramingError` (an `AmhAdapterError`) and nothing else — the exact discipline
   `maezo.adapters.amh.mapping`'s module docstring describes, applied here from the first commit.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from maezo.adapters.amh.contract import CONTRACT_PIN_RELATIVE_PATH, AmhAdapterError, load_contract_pin
from maezo.adapters.amh.wire_framing import (
    GLUE_SCHEMA_REGISTRY_CANDIDATE_WIRE_FRAMING_VALUE,
    WIRE_FRAMING_MANIFEST_KEY,
    DecodedFrame,
    GlueSchemaRegistryCandidateCodec,
    WireFramingCodec,
    WireFramingDecodeError,
    WireFramingEncodeError,
    WireFramingError,
    WireFramingUndeclaredError,
    WireFramingUnknownDeclarationError,
    select_wire_framing_codec,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
REAL_PIN = REPO_ROOT / CONTRACT_PIN_RELATIVE_PATH


def _real_pin_dict() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(REAL_PIN.read_text(encoding="utf-8"))
    return parsed


def _write_pin(tmp_path: Path, mutate: Any) -> Path:
    raw = _real_pin_dict()
    mutate(raw)
    out = tmp_path / "contracts.lock.json"
    out.write_text(json.dumps(raw), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# 1. The refusal against the REAL pin
# ---------------------------------------------------------------------------


def test_the_real_pin_does_not_declare_wire_framing() -> None:
    """Non-vacuity for the whole suite: if this ever fails, the manifest-declaration-blocker has been
    lifted and the rest of this file's premise (CANDIDATE, never selected by default) must be revisited."""
    assert WIRE_FRAMING_MANIFEST_KEY not in _real_pin_dict()


def test_select_wire_framing_codec_refuses_the_real_pinned_manifest() -> None:
    pin = load_contract_pin(REAL_PIN)
    with pytest.raises(WireFramingUndeclaredError) as excinfo:
        select_wire_framing_codec(pin)
    assert excinfo.value.manifest_path == REAL_PIN
    assert isinstance(excinfo.value, AmhAdapterError)
    assert WIRE_FRAMING_MANIFEST_KEY in str(excinfo.value)


def test_select_wire_framing_codec_refusal_is_a_wire_framing_error() -> None:
    """The typed-error-family shape the mission asks for: catchable as the module's own base class."""
    pin = load_contract_pin(REAL_PIN)
    with pytest.raises(WireFramingError):
        select_wire_framing_codec(pin)


# ---------------------------------------------------------------------------
# select_wire_framing_codec: declared-but-unknown / malformed declarations
# ---------------------------------------------------------------------------


def test_select_wire_framing_codec_refuses_an_unknown_declaration(tmp_path: Path) -> None:
    path = _write_pin(tmp_path, lambda raw: raw.__setitem__(WIRE_FRAMING_MANIFEST_KEY, "some-other-framing"))
    pin = load_contract_pin(path)
    with pytest.raises(WireFramingUnknownDeclarationError) as excinfo:
        select_wire_framing_codec(pin)
    assert excinfo.value.declared == "some-other-framing"
    assert GLUE_SCHEMA_REGISTRY_CANDIDATE_WIRE_FRAMING_VALUE in excinfo.value.known


@pytest.mark.parametrize(
    "declared",
    [None, "", "   ", 123, [], {}, True, 3.14],
    ids=["none", "empty", "blank", "int", "list", "dict", "bool", "float"],
)
def test_select_wire_framing_codec_treats_a_non_string_declaration_as_undeclared(
    tmp_path: Path, declared: object
) -> None:
    """A `wire_framing` key that is present but not a real string is refused the SAME way as absent —
    fail-closed either way, never a type error and never a best-effort string coercion."""
    path = _write_pin(tmp_path, lambda raw: raw.__setitem__(WIRE_FRAMING_MANIFEST_KEY, declared))
    pin = load_contract_pin(path)
    with pytest.raises(WireFramingUndeclaredError):
        select_wire_framing_codec(pin)


def test_select_wire_framing_codec_returns_the_glue_candidate_when_declared(tmp_path: Path) -> None:
    path = _write_pin(
        tmp_path,
        lambda raw: raw.__setitem__(
            WIRE_FRAMING_MANIFEST_KEY, GLUE_SCHEMA_REGISTRY_CANDIDATE_WIRE_FRAMING_VALUE
        ),
    )
    pin = load_contract_pin(path)
    codec = select_wire_framing_codec(pin)
    assert isinstance(codec, GlueSchemaRegistryCandidateCodec)
    assert isinstance(codec, WireFramingCodec)


def test_select_wire_framing_codec_is_unaffected_by_an_unrelated_extra_top_level_key(tmp_path: Path) -> None:
    """The manifest-read helper looks ONLY at `wire_framing`; an unrelated addition must not change the
    refusal (guards against the helper accidentally keying on "any extra top-level key")."""
    path = _write_pin(tmp_path, lambda raw: raw.__setitem__("some_unrelated_future_field", "x"))
    pin = load_contract_pin(path)
    with pytest.raises(WireFramingUndeclaredError):
        select_wire_framing_codec(pin)


# ---------------------------------------------------------------------------
# 2. GlueSchemaRegistryCandidateCodec: round trip + fail-closed on unpinned schema
# ---------------------------------------------------------------------------


@pytest.fixture
def real_pin() -> Any:
    return load_contract_pin(REAL_PIN)


@pytest.fixture
def candidate_codec(real_pin: Any) -> GlueSchemaRegistryCandidateCodec:
    return GlueSchemaRegistryCandidateCodec.from_pin(real_pin)


def _pinned_schema_ids(real_pin: Any) -> list[str]:
    return list(real_pin.glue.schema_version_ids.values())


def test_pin_carries_exactly_three_glue_schema_version_ids(real_pin: Any) -> None:
    """Non-vacuity: the round-trip/refusal tests below are meaningless if this pinned set is empty."""
    ids = _pinned_schema_ids(real_pin)
    assert len(ids) == 3
    for schema_id in ids:
        assert uuid.UUID(schema_id)  # every pinned id really is a UUID


@pytest.mark.parametrize("payload", [b"", b"\x00", b"avro-body-bytes", b"\xff" * 500])
def test_round_trip_via_encode_then_decode_for_every_pinned_schema_id(
    candidate_codec: GlueSchemaRegistryCandidateCodec, real_pin: Any, payload: bytes
) -> None:
    for schema_id in _pinned_schema_ids(real_pin):
        frame = DecodedFrame(schema_version_id=schema_id, avro_payload=payload)
        wire_bytes = candidate_codec.encode(frame)
        decoded = candidate_codec.decode(wire_bytes)
        assert decoded.schema_version_id == schema_id
        assert decoded.avro_payload == payload


def test_encoded_frame_matches_the_documented_18_byte_header(
    candidate_codec: GlueSchemaRegistryCandidateCodec, real_pin: Any
) -> None:
    schema_id = _pinned_schema_ids(real_pin)[0]
    wire_bytes = candidate_codec.encode(DecodedFrame(schema_version_id=schema_id, avro_payload=b"body"))
    assert wire_bytes[0] == 3  # header version byte, per AWS's AWSSchemaRegistryConstants.java
    assert wire_bytes[1] == 0  # compression: none
    assert uuid.UUID(bytes=wire_bytes[2:18]) == uuid.UUID(schema_id)
    assert wire_bytes[18:] == b"body"
    assert len(wire_bytes) == 18 + len(b"body")


def test_decode_refuses_an_unpinned_schema_version_id(
    candidate_codec: GlueSchemaRegistryCandidateCodec,
) -> None:
    unpinned_id = str(uuid.uuid4())
    frame = DecodedFrame(schema_version_id=unpinned_id, avro_payload=b"whatever")
    wire_bytes = candidate_codec.encode(frame)  # encode does not gate on the pinned set
    with pytest.raises(WireFramingDecodeError, match="not one of the pin's published"):
        candidate_codec.decode(wire_bytes)


def test_decode_and_encode_agree_on_which_ids_are_pinned(
    candidate_codec: GlueSchemaRegistryCandidateCodec, real_pin: Any
) -> None:
    """A schema id that IS pinned must decode; a fresh random one must not — proves the refusal is
    keyed on membership, not on some incidental property of the bytes."""
    pinned = _pinned_schema_ids(real_pin)[1]
    wire_bytes = candidate_codec.encode(DecodedFrame(schema_version_id=pinned, avro_payload=b"x"))
    assert candidate_codec.decode(wire_bytes).schema_version_id == pinned


# ---------------------------------------------------------------------------
# 3. Malformed frames -> the typed error, never a bare stdlib exception
# ---------------------------------------------------------------------------

_18_BYTE_ID = uuid.uuid4().bytes


@pytest.mark.parametrize(
    ("label", "raw"),
    [
        ("empty bytes", b""),
        ("one byte", b"\x03"),
        ("17 bytes (one short of the header)", bytes([3, 0]) + b"\x00" * 15),
        ("wrong header version", bytes([9, 0]) + _18_BYTE_ID),
        ("zlib compression byte", bytes([3, 5]) + _18_BYTE_ID + b"body"),
        ("unrecognised compression byte", bytes([3, 7]) + _18_BYTE_ID + b"body"),
        ("header only, no schema id or body", bytes([3, 0])),
        ("string instead of bytes", "not-bytes"),
        ("int instead of bytes", 12345),
        ("none instead of bytes", None),
        ("list instead of bytes", [3, 0, 1, 2, 3]),
        ("dict instead of bytes", {"raw": b"x"}),
    ],
    ids=lambda p: p if isinstance(p, str) else None,
)
def test_decode_refuses_every_malformed_shape_with_the_typed_error(
    candidate_codec: GlueSchemaRegistryCandidateCodec, label: str, raw: Any
) -> None:
    with pytest.raises(WireFramingError) as excinfo:
        candidate_codec.decode(raw)
    # The narrow assertion: not JUST "some AmhAdapterError", but specifically the decode-shaped one.
    assert isinstance(excinfo.value, WireFramingDecodeError), label


def test_decode_accepts_bytearray_not_only_bytes(
    candidate_codec: GlueSchemaRegistryCandidateCodec, real_pin: Any
) -> None:
    schema_id = _pinned_schema_ids(real_pin)[2]
    wire_bytes = candidate_codec.encode(DecodedFrame(schema_version_id=schema_id, avro_payload=b"y"))
    decoded = candidate_codec.decode(bytearray(wire_bytes))
    assert decoded.schema_version_id == schema_id
    assert decoded.avro_payload == b"y"


@pytest.mark.parametrize(
    ("label", "frame_kwargs"),
    [
        ("not a UUID string", {"schema_version_id": "not-a-uuid", "avro_payload": b"x"}),
        ("empty string", {"schema_version_id": "", "avro_payload": b"x"}),
        ("integer id", {"schema_version_id": 12345, "avro_payload": b"x"}),
        ("none id", {"schema_version_id": None, "avro_payload": b"x"}),
        ("list id", {"schema_version_id": [1, 2, 3], "avro_payload": b"x"}),
    ],
)
def test_encode_refuses_every_malformed_schema_version_id_with_the_typed_error(
    candidate_codec: GlueSchemaRegistryCandidateCodec, label: str, frame_kwargs: dict[str, Any]
) -> None:
    frame = DecodedFrame(**frame_kwargs)
    with pytest.raises(WireFramingError) as excinfo:
        candidate_codec.encode(frame)
    assert isinstance(excinfo.value, WireFramingEncodeError), label


@pytest.mark.parametrize(
    "avro_payload",
    ["a string, not bytes", 12345, None, ["not", "bytes"], {"k": "v"}],
    ids=["string", "int", "none", "list", "dict"],
)
def test_encode_refuses_a_non_bytes_avro_payload(
    candidate_codec: GlueSchemaRegistryCandidateCodec, real_pin: Any, avro_payload: Any
) -> None:
    schema_id = _pinned_schema_ids(real_pin)[0]
    frame = DecodedFrame(schema_version_id=schema_id, avro_payload=avro_payload)
    with pytest.raises(WireFramingEncodeError):
        candidate_codec.encode(frame)


@pytest.mark.parametrize(
    "not_a_frame", [None, "frame", 123, {"schema_version_id": "x", "avro_payload": b"y"}]
)
def test_encode_refuses_a_non_decoded_frame_argument(
    candidate_codec: GlueSchemaRegistryCandidateCodec, not_a_frame: Any
) -> None:
    with pytest.raises(WireFramingEncodeError):
        candidate_codec.encode(not_a_frame)


def test_no_hostile_decode_or_encode_input_ever_escapes_as_a_bare_stdlib_exception(
    candidate_codec: GlueSchemaRegistryCandidateCodec, real_pin: Any
) -> None:
    """The exact discipline the mission calls out by name: this repo was bitten three times by
    OverflowError/TypeError/ValueError crossing `ports/errors.py`. Every hostile shape here must
    surface as `WireFramingError` and nothing else — proved with a broad `except Exception` that fails
    the test on anything but the one accepted type, so a future un-narrowed raise is caught on sight."""
    hostile_raw: tuple[Any, ...] = (
        b"",
        object(),
        3.14,
        b"\x03\x00" + b"\x00" * 16,  # valid header, zero-filled UUID: not pinned -> refused
        b"\x03\x05" + _18_BYTE_ID + b"x",  # zlib flagged
        2**256,
        (1, 2, 3),
    )
    for atom in hostile_raw:
        try:
            candidate_codec.decode(atom)
        except WireFramingError:
            pass
        except Exception as exc:  # noqa: BLE001 - the assertion IS that this never happens
            pytest.fail(f"decode({atom!r}) leaked {type(exc).__name__} instead of a WireFramingError")

    hostile_frames: tuple[DecodedFrame, ...] = (
        DecodedFrame(schema_version_id="", avro_payload=b""),
        DecodedFrame(schema_version_id="x" * 5000, avro_payload=b""),
        DecodedFrame(schema_version_id=object(), avro_payload=b""),  # type: ignore[arg-type]
        DecodedFrame(schema_version_id=_pinned_schema_ids(real_pin)[0], avro_payload=object()),  # type: ignore[arg-type]
    )
    for frame in hostile_frames:
        try:
            candidate_codec.encode(frame)
        except WireFramingError:
            pass
        except Exception as exc:  # noqa: BLE001 - the assertion IS that this never happens
            pytest.fail(f"encode({frame!r}) leaked {type(exc).__name__} instead of a WireFramingError")


# ---------------------------------------------------------------------------
# Structural sanity
# ---------------------------------------------------------------------------


def test_glue_candidate_codec_satisfies_the_protocol_structurally(
    candidate_codec: GlueSchemaRegistryCandidateCodec,
) -> None:
    assert isinstance(candidate_codec, WireFramingCodec)


def test_the_candidate_vocabulary_token_is_never_the_default_selection(tmp_path: Path) -> None:
    """No pin, real or synthetic-but-silent-on-the-key, ever resolves to the candidate implicitly."""
    path = _write_pin(tmp_path, lambda raw: None)  # no-op mutation: pin unchanged, key absent
    pin = load_contract_pin(path)
    with pytest.raises(WireFramingUndeclaredError):
        select_wire_framing_codec(pin)


def test_public_surface_is_exactly_what_is_exported() -> None:
    """A new public name here must be a deliberate edit, mirroring the sibling modules' own guard."""
    import maezo.adapters.amh.wire_framing as wf_mod

    expected = {
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
    }
    assert set(wf_mod.__all__) == expected
