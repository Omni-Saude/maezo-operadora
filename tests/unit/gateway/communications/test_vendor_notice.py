"""VW1-P3 vendor notice composition unit: determinism, F6 firewall, disclosure refusal.

No route, worker, DMN or sender is claimed here: the vehicle composes a canonical draft and
refuses everything else. The grant is constructed directly against the frozen comms models,
which also proves the merged `vendor` audience literal (models.py:77) is exercisable. The
case reference stays opaque end-to-end — the OP16 case object (PR #672) is consumed BY
REFERENCE, never imported.
"""

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from maezo.gateway.communications.models import (
    CommunicationAccess,
    CommunicationGrant,
    CommunicationScope,
    IntendedRecipient,
    fingerprint,
)
from maezo.gateway.communications.vendor_notice import (
    NOTICE_FIELDS,
    DisclosurePreparationCommand,
    VendorNoticeRefusalError,
    VendorNoticeRefusalReason,
    VendorNoticeSubject,
    compose_vendor_notice,
    prepare_client_disclosure,
    screen_commercial_input,
)
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.portal.contracts.models import HumanPrincipal

REF = "a" * 32
OTHER_CASE = "b" * 32
DIGEST = "c" * 64
# Grant deadlines are FIXED dates, never wall-clock arithmetic: the CI shard imports this
# module minutes before these tests execute, and an import-time `now() + delta` deadline is
# already expired by then (run 37546016719: 2 denied flakes at 9k-test shard scale).
DEADLINE = datetime(2126, 1, 1, tzinfo=UTC)
EXPIRED = datetime(2020, 1, 1, tzinfo=UTC)
OPAQUE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
FROZEN_MOLD_PATHS = (
    "src/maezo/gateway/communications/models.py",
    "src/maezo/gateway/communications/content.py",
    "src/maezo/gateway/communications/provider_notice.py",
    "src/maezo/gateway/communications/production.py",
    "src/maezo/portal/api/communications.py",
    "src/maezo/portal/contracts/communications.py",
)
PRINCIPAL = HumanPrincipal(
    schema_version=1,
    principal_ref="synthetic-principal",
    issuer="https://idp.example.test",
    subject="synthetic-subject",
    tenant="synthetic-tenant",
    membership_revision=1,
    memberships=(),
    session_ref="synthetic-session",
    authenticated_at=datetime(2026, 10, 6, tzinfo=UTC),
    subject_bindings=(),
)


def subject(**overrides: object) -> VendorNoticeSubject:
    values: dict[str, object] = {
        "notice_ref": REF,
        "notice_class": "mandatory",
        "kind": "submission_formal_response",
        "case_ref": REF,
        "business_revision": "7",
        "authorized_content_ref": "approved-content-artifact",
    }
    values.update(overrides)
    return VendorNoticeSubject(**values)


def vendor_grant(
    notice: VendorNoticeSubject,
    *,
    audiences: tuple[str, ...] = ("vendor",),
    operation: str = "publish",
    case_ref: str = REF,
    request_digest: str | None = None,
    permitted_fields: tuple[str, ...] | None = None,
    until: datetime = DEADLINE,
) -> CommunicationGrant:
    return CommunicationGrant(
        access=CommunicationAccess(
            scope=CommunicationScope(tenant="synthetic-tenant", environment="synthetic"),
            principal=PRINCIPAL,
            operation=operation,
            case_ref=case_ref,
            request_digest=(
                request_digest if request_digest is not None else fingerprint(notice.model_dump(mode="json"))
            ),
        ),
        authority_receipt_ref=REF,
        authority_digest=DIGEST,
        policy_valid_until=until,
        valid_until=until,
        permitted_fields=(permitted_fields if permitted_fields is not None else tuple(sorted(NOTICE_FIELDS))),
        intended_recipients=tuple(
            IntendedRecipient(
                identity_digest=f"{index:064x}",
                audience=audience,
                source_revision="1",
                policy_digest=DIGEST,
                valid_until=until,
            )
            for index, audience in enumerate(audiences)
        ),
    )


def test_merged_vendor_audience_composes_a_publish_grant():
    grant = vendor_grant(subject())
    assert [r.audience for r in grant.intended_recipients] == ["vendor"]
    assert grant.access.operation == "publish"


def test_composition_is_deterministic_and_idempotent():
    notice = subject()
    first = compose_vendor_notice(grant=vendor_grant(notice), subject=notice)
    second = compose_vendor_notice(grant=vendor_grant(notice), subject=notice)
    assert first == second
    assert first.distribution == "not_wired"
    assert first.canonical_payload == json.dumps(
        json.loads(first.canonical_payload), sort_keys=True, separators=(",", ":")
    )
    assert first.idempotency_key == fingerprint(
        {
            "tenant": "synthetic-tenant",
            "operation": "notice.prepare_or_send",
            "domain_object_ref": REF,
            "business_revision": "7",
        }
    )
    revised = compose_vendor_notice(
        grant=vendor_grant(subject(business_revision="8")), subject=subject(business_revision="8")
    )
    assert revised.idempotency_key != first.idempotency_key
    assert revised.payload_sha256 != first.payload_sha256


def test_draft_payload_carries_no_free_prose():
    draft = compose_vendor_notice(grant=vendor_grant(subject()), subject=subject())
    payload = json.loads(draft.canonical_payload)
    assert set(payload) == {
        "schema_version",
        "kind",
        "notice_class",
        "notice_ref",
        "case_ref",
        "business_revision",
        "authorized_content_ref",
        "tenant",
        "environment",
        "recipient_identity_digests",
        "authority_digest",
    }
    flat = [value for value in payload.values() if isinstance(value, str)]
    flat.extend(payload["recipient_identity_digests"])
    assert flat and all(OPAQUE.fullmatch(value) for value in flat)


def test_subject_has_no_free_text_inlet():
    assert set(VendorNoticeSubject.model_fields) == {
        "schema_version",
        "notice_ref",
        "notice_class",
        "kind",
        "case_ref",
        "business_revision",
        "authorized_content_ref",
    }
    with pytest.raises(ValueError):
        subject(summary="pacote com texto livre")
    screen_commercial_input(subject().model_dump(mode="json"))


@pytest.mark.parametrize("injected", [{"diagnostico": "condicao clinica"}, {"campo_desconhecido": "x"}])
def test_clinical_or_unknown_field_class_refuses_typed_with_zero_egress(injected: dict[str, str]):
    with pytest.raises(VendorNoticeRefusalError) as refusal:
        screen_commercial_input(injected)
    assert refusal.value.reason is VendorNoticeRefusalReason.PHI_IN_COMMERCIAL_INPUT
    assert refusal.value.decision_ref is None
    # A clinical-looking VALUE cannot even reach the screen: every declared field is a typed
    # closed shape, so the free-text inlet is the field class, and that class never composes.
    with pytest.raises(ValueError):
        subject(**injected)


def test_misdirected_or_unbound_grants_never_compose():
    notice = subject()
    for grant in (
        vendor_grant(notice, audiences=("provider",)),
        vendor_grant(notice, audiences=("vendor", "provider")),
        vendor_grant(notice, operation="read_content", audiences=()),
        vendor_grant(notice, request_digest=DIGEST),
        vendor_grant(notice, case_ref=OTHER_CASE),
        vendor_grant(notice, permitted_fields=("notice_ref", "case_ref")),
    ):
        with pytest.raises(ExternalCaseError):
            compose_vendor_notice(grant=grant, subject=notice)


def test_expired_authority_never_composes():
    with pytest.raises(ExternalCaseError):
        compose_vendor_notice(grant=vendor_grant(subject(), until=EXPIRED), subject=subject())


def test_client_disclosure_refuses_by_construction():
    for command in (
        DisclosurePreparationCommand(
            tenant="synthetic-tenant", commercial_event_ref=REF, artifact_version="1"
        ),
        DisclosurePreparationCommand(
            tenant="synthetic-tenant", commercial_event_ref=OTHER_CASE, artifact_version="2"
        ),
    ):
        with pytest.raises(VendorNoticeRefusalError) as refusal:
            prepare_client_disclosure(command=command)
        assert refusal.value.reason is VendorNoticeRefusalReason.RATIFICATION_MISSING
        assert refusal.value.decision_ref == "VW0-D18"


def test_this_package_never_touches_the_frozen_communication_molds():
    """Structural freeze proof: the package diff must not contain any PW1-C/PW1-D mold.

    Pins at dispatch (re-pin ritual, WAVES §2.10): models 72f72782…, content b82f01f4…,
    provider_notice f90cfe2c…, production f75eaea4…, portal api 82d1f704…, portal contracts
    fed94064…. Only the ABSENCE from the diff is asserted here, so an authorized future
    re-pin by the fence owner never breaks this suite.
    """
    root = Path(__file__).resolve().parents[4]
    base = subprocess.run(
        ["git", "-C", str(root), "merge-base", "origin/main", "HEAD"], capture_output=True, text=True
    )
    if base.returncode != 0:
        pytest.skip("origin/main ancestry unavailable (shallow checkout)")
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", f"{base.stdout.strip()}..HEAD"],
        capture_output=True,
        text=True,
    )
    assert diff.returncode == 0
    changed = set(diff.stdout.split())
    assert not changed.intersection(FROZEN_MOLD_PATHS)
