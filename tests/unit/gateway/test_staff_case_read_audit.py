"""DL-0149: o detalhe do caso do colaborador registra QUEM leu QUAL caso e QUANDO.

Mesmo cenario sintetico de `test_staff_case_authority.py` (sem banco, sem motor nativo). Prova:
uma leitura de detalhe aceita gera exatamente um registro; leitura negada, em conflito ou com
cursor nao gera; BEST-EFFORT (decisao do dono, 19/10/2026): sem sink ou com o sink falhando o
detalhe sai igual e o evento `portal_read_audit_skipped` e' logado so' com codigos; e o registro so'
tem a lista fechada de campos, sem nada do que foi lido (guia, desfecho, sujeito).
"""

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from maezo.gateway.external_cases.models import digest
from maezo.gateway.human.models import Scope as HumanScope
from maezo.gateway.human.read_audit import (
    READ_AUDIT_DETAIL_FIELDS,
    ReadAccessAuditSink,
    ReadAccessEntry,
    read_access_record,
)
from maezo.gateway.human.read_profile import parse_model
from maezo.gateway.staff_cases.models import MembershipWitness, StaffCaseError
from maezo.gateway.staff_cases.service import StaffCaseService
from tests.unit.gateway.test_staff_case_authority import _detail_observation, scenario

pytestmark = pytest.mark.asyncio

AUDIT_SCOPE = HumanScope(tenant="tenant", environment="test", workload_ref="portal-bff")


class RecordingReadAudit(ReadAccessAuditSink):
    def __init__(self, *, fail=None, scope=AUDIT_SCOPE):
        self.scope = scope
        self.entries: list[ReadAccessEntry] = []
        self.fail = fail

    async def record(self, entry):
        if self.fail is not None:
            raise self.fail
        self.entries.append(entry)
        return "r" * 64


def _service(mode, audit):
    _, principal, identity, witness, _, now, _, _ = scenario()
    original = _detail_observation(identity, witness, now)
    calls: list[dict] = []

    class Resolver:
        async def resolve(self, secret):
            return SimpleNamespace(
                principal=principal,
                record=SimpleNamespace(expires_at=now + timedelta(seconds=20)),
                membership=SimpleNamespace(
                    audience="beneficiary" if mode == "beneficiary" else "staff",
                    reviewed_until=now + timedelta(seconds=20),
                ),
            )

    class Witnesses:
        async def observe(self, expected, until):
            return parse_model(MembershipWitness, witness)

    class Native:
        async def read(self, **request):
            calls.append(request)
            value = deepcopy(original)
            if request["operation"] == "finalize":
                assert request["query"]["frozen_projection_digest"] == digest(original["projection"])
                if mode == "changed_receipt":
                    value["continuity_digest"] = "f" * 64
            return value, "a" * 64

    class Lease:
        clock = staticmethod(lambda: now)

        @asynccontextmanager
        async def acquire(self, secret, expected):
            yield SimpleNamespace(valid_until=now + timedelta(seconds=20), current=lambda now: None)

    service = object.__new__(StaffCaseService)
    service.resolver, service.witnesses, service.native, service.lease = (
        Resolver(),
        Witnesses(),
        Native(),
        Lease(),
    )
    service.read_audit = audit
    return service, principal, identity, now, calls, original


async def test_an_accepted_detail_read_writes_exactly_one_record():
    audit = RecordingReadAudit()
    service, principal, identity, now, calls, _ = _service("accepted", audit)
    raw = await service.read("s" * 43, case_ref=identity.case_ref)
    assert json.loads(raw)["case"]["case_ref"] == identity.case_ref
    assert [c["operation"] for c in calls] == ["detail", "finalize"]
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert (entry.read_kind, entry.resource_ref) == ("staff_case_detail", identity.case_ref)
    assert (entry.principal_ref, entry.session_ref, entry.membership_revision) == (
        principal.principal_ref,
        principal.session_ref,
        principal.membership_revision,
    )
    assert entry.read_at == now
    assert entry.scope == AUDIT_SCOPE


async def test_the_staff_record_has_only_the_allowed_fields_and_nothing_that_was_read():
    audit = RecordingReadAudit()
    service, principal, identity, _, _, original = _service("accepted", audit)
    await service.read("s" * 43, case_ref=identity.case_ref)
    record = read_access_record(audit.entries[0], workload_ref=AUDIT_SCOPE.workload_ref)
    assert set(record.details) == READ_AUDIT_DETAIL_FIELDS
    assert record.action == "portal_read.staff_case_detail"
    assert record.details["resource_kind"] == "case"
    serialized = json.dumps(record.details, sort_keys=True)
    for leaked in (
        principal.subject,
        principal.issuer,
        identity.upstream_resource_key,  # a guia
        identity.process_instance_ref,
    ):
        assert leaked not in serialized, leaked


@pytest.mark.parametrize("mode", ["beneficiary", "changed_receipt"])
async def test_a_denied_or_conflicting_detail_read_writes_no_record(mode):
    audit = RecordingReadAudit()
    service, _, identity, _, _, _ = _service(mode, audit)
    with pytest.raises(StaffCaseError):
        await service.read("s" * 43, case_ref=identity.case_ref)
    assert audit.entries == []


async def test_a_cursor_or_a_bad_case_ref_writes_no_record():
    audit = RecordingReadAudit()
    service, _, identity, _, calls, _ = _service("accepted", audit)
    with pytest.raises(StaffCaseError):
        await service.read("s" * 43, case_ref=identity.case_ref, task_cursor="cursor")
    with pytest.raises(StaffCaseError):
        await service.read("s" * 43, case_ref="nao e um case ref")
    assert audit.entries == [] and calls == []


def _skipped(logs):
    events = [e for e in logs if e["event"] == "portal_read_audit_skipped"]
    for e in events:
        assert set(e) == {"event", "log_level", "reason", "read_kind"}, e
    return [(e["reason"], e["read_kind"]) for e in events]


async def test_a_failing_sink_serves_the_detail_and_logs_write_failed():
    audit = RecordingReadAudit(fail=RuntimeError("PRIVATE audit_chain down"))
    service, _, identity, _, calls, _ = _service("accepted", audit)
    with capture_logs() as logs:
        raw = await service.read("s" * 43, case_ref=identity.case_ref)
    assert json.loads(raw)["case"]["case_ref"] == identity.case_ref
    assert [c["operation"] for c in calls] == ["detail", "finalize"]
    assert _skipped(logs) == [("write_failed", "staff_case_detail")]
    assert "PRIVATE" not in repr(logs)


async def test_the_list_is_not_a_detail_and_writes_no_record():
    # The list never reaches the record call; proven by the absence of the call site in `_list`.
    import inspect

    from maezo.gateway.staff_cases import service as module

    assert "read_audit" not in inspect.getsource(module.StaffCaseService._list)
    assert "read_audit" in inspect.getsource(module.StaffCaseService._read)


async def test_without_a_sink_the_detail_read_is_unchanged_and_logs_sink_absent():
    # The `identity,staff_cases` profile (no human plane): no record, no refusal, one event.
    service, _, identity, _, _, _ = _service("accepted", None)
    with capture_logs() as logs:
        raw = await service.read("s" * 43, case_ref=identity.case_ref)
    assert json.loads(raw)["case"]["case_ref"] == identity.case_ref
    assert _skipped(logs) == [("sink_absent", "staff_case_detail")]


@pytest.mark.parametrize("mode", ["beneficiary", "changed_receipt"])
async def test_a_denied_detail_read_logs_no_skipped_event_either(mode):
    service, _, identity, _, _, _ = _service(mode, None)
    with capture_logs() as logs, pytest.raises(StaffCaseError):
        await service.read("s" * 43, case_ref=identity.case_ref)
    assert _skipped(logs) == []


async def test_a_sink_of_another_tenant_cannot_be_installed():
    resolver = SimpleNamespace(settings=SimpleNamespace(tenant="tenant", issuer="https://issuer.test"))
    lease = SimpleNamespace(tenant="tenant", issuer="https://issuer.test")
    designation_scope = SimpleNamespace(tenant="tenant")
    witnesses = SimpleNamespace(source=SimpleNamespace(scope=designation_scope))
    native = SimpleNamespace(
        authority=SimpleNamespace(designation=SimpleNamespace(scope=designation_scope)),
        signer=SimpleNamespace(role="read_requester"),
    )
    good = StaffCaseService(resolver, lease, witnesses, native, read_audit=RecordingReadAudit())  # type: ignore[arg-type]
    assert good.read_audit is not None
    other = RecordingReadAudit(scope=AUDIT_SCOPE.model_copy(update={"tenant": "other"}))
    with pytest.raises(StaffCaseError):
        StaffCaseService(resolver, lease, witnesses, native, read_audit=other)  # type: ignore[arg-type]
