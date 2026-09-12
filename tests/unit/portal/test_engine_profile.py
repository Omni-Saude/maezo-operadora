"""ADR0049 D5: independent, number-free JCS and dedicated signing boundary."""

import base64
import hashlib
from dataclasses import asdict, replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from maezo.portal.engine.profile import (
    HumanCommand,
    ProfileError,
    SigningContext,
    canonicalize,
    seal,
    strict_loads,
)

# The two Java-tree parity fixtures (`jcs-vectors.json`, `python-ed25519-vector.json`) live under
# src/maezo/portal/engine/java/src/test/resources and land with the Java plugin (PR-C); the JCS
# vector parametrization and the interop-envelope comparison travel with them. Everything else
# in this module is Python-only.
# Public RFC8032 test material; never a deployment credential.
SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
# Public synthetic form identifier, shared with the committed Java wire vector.
SYNTHETIC_FORM = "maezo.synthetic-ack.v1"


def command():
    return HumanCommand(
        tenant="tenant-test",
        task_id="task-test",
        command_id="command-test",
        principal_ref="human-test",
        principal_issuer="https://issuer.example.test/",
        principal_subject="subject-test",
        workload_ref="gateway-test",
        operation="decision",
        process_definition_id="process-test:1:id",
        process_definition_key="MZO-HUMAN-SYNTHETIC",
        process_definition_version="1",
        process_definition_digest="a" * 64,
        task_definition_key="UT_Acknowledge",
        form_key=SYNTHETIC_FORM,
        form_version="1",
        form_digest="b" * 64,
        task_revision="2",
        authority_revision="3",
        membership_revision="1",
        evidence_revision="3",
        evidence_ref="evidence-test",
        evidence_digest="c" * 64,
        assignee_ref="human-test",
        audit_intent_ref="intent-test",
        outcome="ACK",
    )


def context():
    return SigningContext(
        tenant="tenant-test",
        audience="engine-test",
        workload_ref="gateway-test",
        key_id="command-key",
        max_lifetime_seconds=60,
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":null,"a":true}',
        b'{"a":{"x":true,"x":false}}',
        b"0",
        b"-0",
        b"1.0",
        b"1e2",
        b"9007199254740993",
        b"NaN",
        b"Infinity",
        b"-Infinity",
        b'"\\ud800"',
        b'"\\udfff"',
        b'"\xff"',
        b'{"\\ud800":true}',
        b"{} {}",
        b"\xef\xbb\xbf{}",
    ],
)
def test_strict_json_rejects_ambiguous_or_nonprofile_input(raw):
    with pytest.raises(ProfileError):
        strict_loads(raw)


@pytest.mark.parametrize("value", [1, 1.0, float("nan"), ("x",), {1: "a"}, {"a": object()}, "\ud800"])
def test_no_numeric_coercion_or_custom_objects(value):
    with pytest.raises(ProfileError):
        canonicalize(value)


def test_decimal_strings_preserve_arbitrary_precision_without_int_conversion():
    huge = "9" * 12000
    assert strict_loads(canonicalize({"centavos": huge})) == {"centavos": huge}


def test_no_unicode_normalization():
    assert canonicalize({"x": "é"}) != canonicalize({"x": "e\u0301"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant", "other"),
        ("command_id", "other"),
        ("principal_ref", "other"),
        ("evidence_digest", "d" * 64),
        ("task_revision", "4"),
        ("membership_revision", "4"),
        ("authority_revision", "4"),
        ("process_definition_digest", "e" * 64),
    ],
)
def test_every_semantic_identity_and_revision_changes_digest(field, value):
    assert replace(command(), **{field: value}).digest != command().digest


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_revision", 3),
        ("task_revision", "03"),
        ("outcome", "NEGAR"),
        ("schema", "a2a.v1"),
        ("principal_ref", "x\n"),
        ("principal_issuer", "https://x/\ud800"),
        ("evidence_digest", "G" * 64),
        ("operation", "delegate"),
    ],
)
def test_typed_command_rejects_unsafe_inputs(field, value):
    with pytest.raises(ProfileError):
        replace(command(), **{field: value})


def test_signing_envelope_is_exact_and_verifiable():
    key = Ed25519PrivateKey.from_private_bytes(SEED)
    raw = seal(command(), context=context(), issued_at=1700000000, expires_at=1700000060, sign=key.sign)
    envelope = strict_loads(raw)
    signature = base64.urlsafe_b64decode(envelope.pop("signature") + "==")
    key.public_key().verify(signature, canonicalize(envelope))
    assert envelope["digest"] == hashlib.sha256(command().canonical).hexdigest()


@pytest.mark.parametrize("issued,expires", [(0, 0), (0, 61), (True, 3), (-1, 3), (4, 3)])
def test_explicit_validity_and_signer_scope(issued, expires):
    with pytest.raises(ProfileError):
        seal(command(), context=context(), issued_at=issued, expires_at=expires, sign=lambda _: b"x" * 64)


def test_scope_failure_precedes_signer():
    def forbidden(_):
        pytest.fail("cross-tenant command reached signing port")

    with pytest.raises(ProfileError):
        seal(
            replace(command(), tenant="other"), context=context(), issued_at=0, expires_at=60, sign=forbidden
        )


def test_unknown_command_fields_cannot_be_engine_variables():
    with pytest.raises(TypeError):
        HumanCommand(**{**asdict(command()), "variables": {}})


def test_revision_strings_do_not_acquire_an_arbitrary_precision_limit():
    assert replace(command(), task_revision="9" * 12000).task_revision == "9" * 12000


def test_wire_and_timestamp_limits_match_java_profile():
    with pytest.raises(ProfileError):
        strict_loads(b'"' + b"x" * 65536 + b'"')
    with pytest.raises(ProfileError):
        seal(command(), context=context(), issued_at=2**63, expires_at=2**63 + 1, sign=lambda _: b"x" * 64)
