"""T1.7b/T1.5 parity vector: the Python membership projection and the Java provider agree byte for byte.

The same file (tests/fixtures/portal_read/jcs-membership-vector.json) is read by the Java provider's
MembershipProjectionTest. Here every accepted row goes through the REAL publisher path
(MembershipRecord.model_validate_json, as postgres.py get_membership parses it, then
PostgresMembershipPublicationSource.read) and its wired JCS bytes and SHA-256 must be the vector's.
Synthetic data only.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from maezo.gateway.human.read_profile import SourceProvenance, digest, wire
from maezo.gateway.human.read_publisher import (
    PostgresMembershipPublicationSource,
    SourceFreezeLease,
)
from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.api.records import MembershipRecord
from maezo.portal.engine.profile import canonicalize

VECTOR = Path(__file__).resolve().parents[2] / "fixtures" / "portal_read" / "jcs-membership-vector.json"


def _vector() -> dict:
    return json.loads(VECTOR.read_text(encoding="utf-8"))


async def _projection(tenant: str, row: str):
    parsed = MembershipRecord.model_validate_json(row)
    # Unit isolation of DB I/O: the stored payload is parsed exactly as get_membership does.
    store = object.__new__(PostgresIdentityStore)
    store.tenant = tenant
    store.get_membership = AsyncMock(return_value=parsed)
    now = datetime(2026, 9, 23, tzinfo=UTC)
    source = SourceProvenance(
        publisher_ref="publisher",
        source_ref="membership",
        source_revision=0,
        source_digest="0" * 64,
        receipt_ref="receipt",
        observed_at=now,
        valid_until=now + timedelta(seconds=300),
    )
    lease = SourceFreezeLease(source, lambda _: None, lambda: None, lambda _: None, lambda: None)
    handshake = AsyncMock()
    handshake.freeze.return_value = lease
    result = await PostgresMembershipPublicationSource(store=store, handshake=handshake).read(
        parsed.issuer, parsed.subject
    )
    return result.payload


def test_vector_is_well_formed():
    vector = _vector()
    assert vector["schema"] == "portal-read-jcs-membership-vector.v1"
    assert len(vector["cases"]) >= 8 and len(vector["refused"]) >= 10
    names = [c["name"] for c in vector["cases"] + vector["refused"]]
    assert len(names) == len(set(names))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _vector()["cases"], ids=lambda c: c["name"])
async def test_python_projection_matches_the_shared_vector(case):
    payload = await _projection(case["tenant"], case["row_payload"])
    raw = canonicalize(wire(payload))
    assert raw == case["projection_jcs"].encode("utf-8")
    assert hashlib.sha256(raw).hexdigest() == case["projection_sha256"]
    # source_digest := SHA-256(JCS(MembershipProjection)) (plan section 3.1, T1.5 contract)
    assert digest(payload) == case["projection_sha256"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _vector()["refused"], ids=lambda c: c["name"])
async def test_python_refuses_what_the_vector_refuses(case):
    with pytest.raises(Exception):  # noqa: B017 - validation error or ReadRefusalError, both refuse
        await _projection(case["tenant"], case["row_payload"])
