"""Synthetic native responses test adapter custody only; no fake engine integration."""

import base64
from datetime import timedelta

import pytest
from tests.unit.portal.test_queue_cursor_custody import setup
from tests.unit.portal.test_read_engine_profile import fixture

from maezo.gateway.human.engine_reads import (
    EngineHumanTaskTransport,
    EngineReadBundle,
    EngineTaskAuthority,
    EngineTaskDisclosure,
)
from maezo.gateway.human.models import AuthoritativeTask, CurrentTaskAuthority
from maezo.gateway.human.queue import (
    CatalogExpectation,
    CatalogTrustAnchor,
    ReadRefusalError,
    TaskDisclosureGrant,
)
from maezo.gateway.human.read_profile import (
    AuthorityValue,
    CatalogValue,
    DisclosureValue,
    NativeContinuity,
    TaskValue,
    digest,
    parse_model,
    wire,
)
from maezo.gateway.human.read_transport import ReadResult
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import canonicalize


async def bundle_fixture(change=None):
    clock, p, partition, cursor, binding = setup()
    await partition.prepare()
    clock.now += timedelta(seconds=2)
    data = fixture()
    task = parse_model(AuthoritativeTask, data["task"])
    principal = parse_model(HumanPrincipal, data["principal"])
    authority = parse_model(CurrentTaskAuthority, data["authority"])
    anchor = CatalogTrustAnchor(scope=p.scope, catalog_ref="catalog", publisher_ref="publisher")
    artifact = {
        "schema": "portal-read-catalog.v1",
        "catalog_ref": "catalog",
        "publisher_ref": "publisher",
        "entries": [],
        "policies": [],
        "forms": [],
        "deployment_receipt_ref": "deployment",
        "deployment_receipt_digest": "a" * 64,
    }
    catalog = CatalogValue(
        expectation=CatalogExpectation(
            anchor=anchor,
            catalog_revision=1,
            catalog_digest=digest(artifact),
            source_observed_at=clock() - timedelta(seconds=2),
            valid_until=task.valid_until,
        ),
        catalog_artifact_base64=base64.b64encode(canonicalize(artifact)).decode(),
    )
    context = base64.urlsafe_b64encode(b"x" * 32).rstrip(b"=").decode()
    c = data["task_continuity"]["claims"]
    c["binding"]["read_context_id"] = context
    c["binding"]["requester"] = wire(partition.signing.requester)
    c["origin_request_digest"] = "1" * 64
    ct = parse_model(NativeContinuity, data["task_continuity"])
    c = data["authority_continuity"]["claims"]
    c["binding"] = wire(ct.claims.binding)
    c["origin_request_digest"] = "2" * 64
    c["task_continuity_digest"] = digest(ct)
    ca = parse_model(NativeContinuity, data["authority_continuity"])

    class Client:
        def __init__(self):
            self.partition = partition
            self.read_context_id = context

        async def task(self, anchor, task_id):
            value = TaskValue(catalog=catalog, task=task, task_continuity=ct)
            if change == "context":
                value = value.model_copy(
                    update={
                        "task_continuity": ct.model_copy(
                            update={
                                "claims": ct.claims.model_copy(
                                    update={
                                        "binding": ct.claims.binding.model_copy(
                                            update={"read_context_id": "Y" * 43}
                                        )
                                    }
                                )
                            }
                        )
                    }
                )
            return ReadResult(value, clock(), task.valid_until, "1" * 64)

        async def authority(self, anchor, principal, task, continuity):
            value = AuthorityValue(
                catalog=catalog,
                authority=authority,
                task_continuity_digest=digest(ct),
                authority_continuity=ca,
            )
            if change == "predecessor":
                value = value.model_copy(update={"task_continuity_digest": "f" * 64})
            if change == "principal":
                value = value.model_copy(
                    update={
                        "authority_continuity": ca.model_copy(
                            update={"claims": ca.claims.model_copy(update={"principal_digest": "f" * 64})}
                        )
                    }
                )
            return ReadResult(value, clock(), authority.valid_until, "2" * 64)

        async def disclosure(self, anchor, principal, task, continuity, authority, authority_continuity):
            grant = TaskDisclosureGrant(
                scope=p.scope,
                principal=principal,
                snapshot=task.snapshot,
                authority_revision=task.authority_revision,
                classification_ref="classification",
                classification_digest="a" * 64,
                projection="full_task_detail.v1",
                valid_until=authority.valid_until,
            )
            if change == "snapshot":
                grant = grant.model_copy(
                    update={"snapshot": grant.snapshot.model_copy(update={"snapshot_at": clock()})}
                )
            value = DisclosureValue(
                catalog=catalog,
                grant=grant,
                task_continuity_digest=digest(ct),
                authority_continuity_digest=digest(ca),
            )
            return ReadResult(value, clock(), authority.valid_until, "3" * 64)

    bundle = EngineReadBundle(client=Client(), anchor=anchor, cursor=cursor)
    return bundle, principal


@pytest.mark.asyncio
async def test_equal_revalidated_copies_retain_private_chain():
    b, p = await bundle_fixture()
    task = await EngineHumanTaskTransport(b).read_task("task-1")
    copied = AuthoritativeTask.model_validate(task)
    assert copied is not task
    a = await EngineTaskAuthority(b).current_authority(p, copied)
    g = await EngineTaskDisclosure(b).classify(
        HumanPrincipal.model_validate(p),
        AuthoritativeTask.model_validate(copied),
        CurrentTaskAuthority.model_validate(a),
    )
    assert g.snapshot == task.snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["context", "predecessor", "principal", "snapshot"])
async def test_exact_receipt_and_snapshot_checks_poison_bundle(change):
    b, p = await bundle_fixture(change)
    with pytest.raises(ReadRefusalError):
        t = await EngineHumanTaskTransport(b).read_task("task-1")
        a = await EngineTaskAuthority(b).current_authority(p, t)
        await EngineTaskDisclosure(b).classify(p, t, a)
    assert b._closed and not b._tasks


@pytest.mark.asyncio
async def test_missing_foreign_duplicate_or_changed_row_is_not_imported():
    b, p = await bundle_fixture()
    other, _ = await bundle_fixture()
    task = await EngineHumanTaskTransport(b).read_task("task-1")
    with pytest.raises(ReadRefusalError):
        await EngineTaskAuthority(other).current_authority(p, task)
    with pytest.raises(ReadRefusalError):
        await EngineHumanTaskTransport(b).read_task("task-1")
    assert b._closed


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["context", "principal", "predecessor"])
async def test_invalid_native_binding_is_rejected_at_its_own_stage(change):
    bundle, principal = await bundle_fixture(change)
    if change == "context":
        with pytest.raises(ReadRefusalError):
            await EngineHumanTaskTransport(bundle).read_task("task-1")
    else:
        task = await EngineHumanTaskTransport(bundle).read_task("task-1")
        with pytest.raises(ReadRefusalError):
            await EngineTaskAuthority(bundle).current_authority(principal, task)
    assert bundle._closed
