"""OP16 submission machine: idempotency, PHI firewall, channel eligibility, audit, contract pin.

Unit half of VW1-P4 (the ASGI half lives in `tests/unit/portal/test_vendor_submissions.py`).
Nothing here claims engine, Postgres or a published core: the stores are in-memory reference
implementations of the protocols the migration-0020 DDL constrains.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from maezo.gateway.audit import AuditSink
from maezo.gateway.capabilities.models import CONTRACT_STATE
from maezo.gateway.human.vendor_membership_administration import VendorChannelState
from maezo.gateway.vendor_submissions import (
    DECLARED_PAYLOAD_CLASSES,
    OPERATION_NAME,
    SubmissionAbsentError,
    VendorSubmissionError,
    VendorSubmissionMachine,
    VendorSubmissionRefusal,
    command_business_bytes,
    command_business_digest,
    submission_id_for,
)
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding
from maezo.portal.contracts.vendor_submissions import (
    ChannelSubmissionCommand,
    ChannelSubmissionPayload,
    ChannelSubmissionReceipt,
    VendorSubmissionRefusalCode,
)

TENANT = "test-tenant"
CHANNEL = "vendor-channel-0001"
NOW = datetime.now(UTC)


def vendor_membership(channel_ref: str = CHANNEL, **changes: Any) -> MembershipRecord:
    return MembershipRecord(
        **dict(
            tenant=TENANT,
            issuer="https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_TestPool",
            subject="00000000-0000-4000-8000-000000000001",
            principal_ref="vendor-principal",
            revision=1,
            audience="vendor",
            memberships=(
                MembershipBinding(membership_ref="vendor-binding", roles=("vendor_portal",), groups=()),
            ),
            subject_bindings=(SubjectBinding(kind="vendor", resource_ref=channel_ref),),
            reviewed_until=NOW + timedelta(hours=2),
            revoked=False,
        )
        | changes
    )


def command(**changes: Any) -> ChannelSubmissionCommand:
    values: dict[str, Any] = dict(
        schema_version=1,
        command_id="command-0001-attempt01",
        channel_ref=CHANNEL,
        submission_ref="submission-000001",
        business_revision="7",
        contract_ref="contract-00000001",
        payload=ChannelSubmissionPayload(content_class="commercial"),
    )
    values.update(changes)
    return ChannelSubmissionCommand(**values)


class ChannelDirectory:
    """In-memory view of the migration-0019 store; rows are real `VendorChannelState` models
    (the protocol returns models, never dicts) and `None` models an ABSENT STORE."""

    def __init__(self, rows: dict[str, tuple[str, str]] | None) -> None:
        # rows: channel_ref -> (tenant, status).
        self.rows = rows

    async def channel(self, tenant: str, channel_ref: str) -> VendorChannelState | None:
        if self.rows is None:
            return None
        if (row := self.rows.get(channel_ref)) is None:
            return None
        return VendorChannelState(
            tenant=row[0],
            channel_ref=channel_ref,
            revision=3,
            status=row[1],
            updated_at=NOW,
        )


class SubmissionStore:
    """In-memory reference store; UNIQUE on the named business key, like the 0020 PK."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._rows: dict[tuple[str, str, str, str], Any] = {}

    async def find(self, tenant: str, submission_ref: str) -> tuple[Any, ...]:
        return tuple(
            row
            for key, row in sorted(self._rows.items())
            if key[0] == tenant and key[2] == submission_ref
        )

    async def record(self, submission: Any) -> None:
        key = (submission.tenant, OPERATION_NAME, submission.submission_ref, submission.business_revision)
        async with self._lock:
            if key in self._rows:
                raise ValueError("duplicate business key")
            self._rows[key] = submission

    def count(self, tenant: str = TENANT) -> int:
        return sum(1 for key in self._rows if key[0] == tenant)


class AuditTrail:
    def __init__(self) -> None:
        self.records: list[Any] = []
        self._sink = AuditSink()
        self.fail = False

    async def emit(self, record: Any) -> str:
        if self.fail:
            raise RuntimeError("audit sink unavailable")
        self.records.append(record)
        return self._sink.emit(record)

    def decisions(self) -> list[tuple[str, str]]:
        return [(record.decision, str(record.details.get("reason"))) for record in self.records]


def machine(
    *,
    channels: Any = "default",
    store: SubmissionStore | None = None,
    audit: AuditTrail | None = None,
) -> tuple[VendorSubmissionMachine, SubmissionStore, AuditTrail]:
    built_store = store if store is not None else SubmissionStore()
    built_audit = audit if audit is not None else AuditTrail()
    built_channels = ChannelDirectory({CHANNEL: (TENANT, "active")}) if channels == "default" else channels
    return (
        VendorSubmissionMachine(channels=built_channels, store=built_store, audit=built_audit),
        built_store,
        built_audit,
    )


# -------------------------------------------------------------------------------- positive


def test_submissao_comercial_valida_recebe_recibo_received() -> None:
    built, store, audit = machine()
    receipt = asyncio.run(built.submit(TENANT, vendor_membership(), command()))
    assert receipt.lifecycle == "received"
    assert receipt.refusal_code is None
    assert receipt.case_ref is None and receipt.document_request_ref is None  # C-10: no assertion
    assert receipt.submission_id == submission_id_for(command_business_digest(command()))
    assert store.count() == 1
    assert audit.decisions() == [("ALLOW", "received")]


def test_reenvio_da_mesma_business_key_retorna_a_mesma_instancia() -> None:
    built, store, audit = machine()
    first = asyncio.run(built.submit(TENANT, vendor_membership(), command()))
    retry = asyncio.run(
        built.submit(TENANT, vendor_membership(), command(command_id="command-0001-attempt02"))
    )
    assert retry.submission_id == first.submission_id  # the SAME active instance, never a 2nd
    assert retry.lifecycle == first.lifecycle == "received"
    assert retry.command_id == "command-0001-attempt02"  # the reply correlates THIS attempt
    assert store.count() == 1
    assert audit.decisions() == [("ALLOW", "received"), ("ALLOW", "replayed_active_instance")]


def test_nova_business_revision_cria_instancia_propria_e_a_leitura_traz_a_mais_recente() -> None:  # noqa: E501
    built, store, _ = machine()
    asyncio.run(built.submit(TENANT, vendor_membership(), command(business_revision="7")))
    second = asyncio.run(built.submit(TENANT, vendor_membership(), command(business_revision="8")))
    assert store.count() == 2
    status = asyncio.run(built.read(TENANT, vendor_membership(), "submission-000001"))
    assert status.business_revision == "8"
    assert status.submission_id == second.submission_id


# ------------------------------------------------------------------- idempotency negatives


def test_reenvio_com_conteudo_mutado_sob_a_mesma_chave_e_contract_mismatch() -> None:
    built, store, _ = machine()
    asyncio.run(built.submit(TENANT, vendor_membership(), command()))
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(
            built.submit(
                TENANT,
                vendor_membership(),
                command(command_id="command-0001-attempt02", contract_ref="contract-00000002"),
            )
        )
    assert exc.value.reason is VendorSubmissionRefusal.CONTRACT_MISMATCH
    assert store.count() == 1  # the active instance was never overwritten


def test_revisao_mais_antiga_que_a_ativa_e_stale_revision() -> None:
    built, store, _ = machine()
    asyncio.run(built.submit(TENANT, vendor_membership(), command(business_revision="9")))
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(built.submit(TENANT, vendor_membership(), command(business_revision="5")))
    assert exc.value.reason is VendorSubmissionRefusal.STALE_REVISION
    assert store.count() == 1


# ------------------------------------------------------------------------ PHI firewall


@pytest.mark.parametrize(
    "classe",
    [
        "declaracao_saude",  # the NB-12a health-declaration class — NOT declared (VW0-D16)
        "health_declaration",  # its English proxy
        "clinical",
        "unknown",
    ],
)
def test_payload_de_classe_nao_declarada_cai_no_firewall_com_zero_efeito(classe: str) -> None:
    built, store, audit = machine()
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(
            built.submit(
                TENANT,
                vendor_membership(),
                command(payload=ChannelSubmissionPayload(content_class=classe)),
            )
        )
    assert exc.value.reason is VendorSubmissionRefusal.PHI_IN_COMMERCIAL_INPUT
    assert store.count() == 0  # ZERO downstream effect: no instance, no trail
    assert audit.decisions() == [("DENY", "PHI_IN_COMMERCIAL_INPUT")]  # the refusal IS audited


def test_proxies_de_forma_sao_recusados_na_camada_wire() -> None:
    """The bounded class pattern is the FIRST firewall layer: uppercase, spacing and free-text
    shapes never reach the machine — they are wire refusals, not semantic ones."""
    for classe in (
        "commercial ", "COMMERCIAL", "Declaracao_Saude", "1numeric", "-dash", "a" * 65, "com mercial"
    ):
        with pytest.raises(ValidationError):
            ChannelSubmissionPayload(content_class=classe)


def test_o_firewall_decide_antes_de_qualquer_escrita_mesmo_sem_canal() -> None:
    """A clinical class refuses with the PHI code even when the channel source is absent."""
    built, store, _ = machine(channels=ChannelDirectory(None))
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(
            built.submit(
                TENANT,
                vendor_membership(),
                command(payload=ChannelSubmissionPayload(content_class="declaracao_saude")),
            )
        )
    assert exc.value.reason is VendorSubmissionRefusal.PHI_IN_COMMERCIAL_INPUT
    assert store.count() == 0


# ---------------------------------------------------------------- channel eligibility


def test_canal_revogado_e_channel_status_ineligible() -> None:
    built, store, _ = machine(
        channels=ChannelDirectory({CHANNEL: (TENANT, "revoked")})
    )
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(built.submit(TENANT, vendor_membership(), command()))
    assert exc.value.reason is VendorSubmissionRefusal.CHANNEL_STATUS_INELIGIBLE
    assert store.count() == 0


def test_canal_ausente_e_fonte_ausente_sao_source_unavailable() -> None:
    for channels in (ChannelDirectory(None), None):
        built, store, _ = machine(channels=channels)
        with pytest.raises(VendorSubmissionError) as exc:
            asyncio.run(built.submit(TENANT, vendor_membership(), command()))
        assert exc.value.reason is VendorSubmissionRefusal.SOURCE_UNAVAILABLE
        assert store.count() == 0


def test_canal_de_outro_tenant_e_contract_mismatch() -> None:
    built, _, _ = machine(
        channels=ChannelDirectory({CHANNEL: ("other-tenant", "active")})
    )
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(built.submit(TENANT, vendor_membership(), command()))
    assert exc.value.reason is VendorSubmissionRefusal.CONTRACT_MISMATCH


# --------------------------------------------------------------------------- authority


def test_sessao_sem_membership_vendor_e_authority_unproven() -> None:
    built, store, _ = machine()
    staff = vendor_membership(audience="staff", subject_bindings=())
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(built.submit(TENANT, staff, command()))
    assert exc.value.reason is VendorSubmissionRefusal.AUTHORITY_UNPROVEN
    assert store.count() == 0


def test_binding_de_outro_canal_e_authority_unproven() -> None:
    built, _, _ = machine()
    other = vendor_membership(channel_ref="vendor-channel-9999")
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(built.submit(TENANT, other, command()))
    assert exc.value.reason is VendorSubmissionRefusal.AUTHORITY_UNPROVEN


def test_membership_revogada_ou_de_outro_tenant_e_authority_unproven() -> None:
    built, _, _ = machine()
    for record in (vendor_membership(revoked=True), vendor_membership(tenant="other-tenant")):
        with pytest.raises(VendorSubmissionError) as exc:
            asyncio.run(built.submit(TENANT, record, command()))
        assert exc.value.reason is VendorSubmissionRefusal.AUTHORITY_UNPROVEN


# ------------------------------------------------------------------------------- audit


def test_auditoria_indisponivel_para_a_submissao_com_zero_efeito() -> None:
    audit = AuditTrail()
    audit.fail = True
    built, store, _ = machine(audit=audit)
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(built.submit(TENANT, vendor_membership(), command()))
    assert exc.value.reason is VendorSubmissionRefusal.AUDIT_UNAVAILABLE
    assert store.count() == 0  # audit runs BEFORE any write: zero downstream effect


def test_auditoria_indisponivel_para_a_recusa_phi_tambem_para_a_maquina() -> None:
    audit = AuditTrail()
    audit.fail = True
    built, store, _ = machine(audit=audit)
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(
            built.submit(
                TENANT,
                vendor_membership(),
                command(payload=ChannelSubmissionPayload(content_class="declaracao_saude")),
            )
        )
    assert exc.value.reason is VendorSubmissionRefusal.AUDIT_UNAVAILABLE
    assert store.count() == 0


# ------------------------------------------------------------------------ read + formalization


def test_leitura_de_instancia_alheia_ou_ausente_e_indistinguivel() -> None:
    built, _, _ = machine()
    asyncio.run(built.submit(TENANT, vendor_membership(), command()))
    other = vendor_membership(channel_ref="vendor-channel-9999")
    with pytest.raises(SubmissionAbsentError):
        asyncio.run(built.read(TENANT, other, "submission-000001"))
    with pytest.raises(SubmissionAbsentError):
        asyncio.run(built.read(TENANT, vendor_membership(), "submission-004040"))


def test_formalizacao_recusa_source_unavailable_por_construcao() -> None:
    """The core publish (VW1-P1) landed the ledger; its export is a VW2 decision, so the
    machine keeps the typed refusal. The tripwire: if the module-level default state flips,
    this package MUST be revisited before any vendor formalization answer exists."""
    built, _, _ = machine()
    assert CONTRACT_STATE == "PROPOSED_INTERNAL_CONTRACT_NOT_PUBLISHED"
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(built.formalization_response(TENANT, vendor_membership(), "submission-000001"))
    assert exc.value.reason is VendorSubmissionRefusal.SOURCE_UNAVAILABLE


def test_formalizacao_sem_autoridade_recusa_authority_antes_de_qualquer_fonte() -> None:
    built, _, _ = machine()
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(
            built.formalization_response(
                TENANT, vendor_membership(audience="staff", subject_bindings=()), "x"
            )
        )
    assert exc.value.reason is VendorSubmissionRefusal.AUTHORITY_UNPROVEN


# ------------------------------------------------------------------------ contract pin


def test_bytes_exatos_do_conteudo_de_negocio_do_comando() -> None:
    """Canonical business bytes: command_id excluded (attempt identity), keys sorted."""
    assert command_business_bytes(command()) == (
        b'{"business_revision":"7","channel_ref":"vendor-channel-0001",'
        b'"contract_ref":"contract-00000001","payload":{"content_class":"commercial"},'
        b'"submission_ref":"submission-000001"}'
    )


def test_bytes_exatos_do_recibo_para_um_comando_fixo() -> None:
    built, _, _ = machine()
    receipt = asyncio.run(built.submit(TENANT, vendor_membership(), command()))
    assert receipt.model_dump_json() == (
        '{"schema_version":1,'
        '"command_id":"command-0001-attempt01",'
        '"channel_ref":"vendor-channel-0001",'
        '"submission_ref":"submission-000001",'
        '"business_revision":"7",'
        '"submission_id":"' + submission_id_for(command_business_digest(command())) + '",'
        '"lifecycle":"received",'
        '"refusal_code":null,'
        '"case_ref":null,'
        '"document_request_ref":null}'
    )


def test_o_enum_de_recusas_bate_com_o_literal_do_contrato() -> None:
    wire_codes = set(get_args(VendorSubmissionRefusalCode))
    assert {reason.value for reason in VendorSubmissionRefusal} == wire_codes
    assert {"commercial"} == DECLARED_PAYLOAD_CLASSES


def test_a_business_key_nomeada_cita_a_operacao_do_registry() -> None:
    assert OPERATION_NAME == "channel_submission"


def test_receipt_exige_evidencia_nativa_por_estagio() -> None:
    def receipt(**changes: Any) -> ChannelSubmissionReceipt:
        values: dict[str, Any] = dict(
            schema_version=1,
            command_id="command-0001-attempt01",
            channel_ref=CHANNEL,
            submission_ref="submission-000001",
            business_revision="7",
            submission_id="vsub" + "a" * 28,
            lifecycle="received",
        )
        values.update(changes)
        return ChannelSubmissionReceipt(**values)

    # responded is unconstructible without a native case object (never a notice).
    with pytest.raises(ValidationError, match="native case evidence required"):
        receipt(lifecycle="responded")
    # documents_pending is unconstructible without a native document request.
    with pytest.raises(ValidationError, match="native document request evidence required"):
        receipt(lifecycle="documents_pending")
    # staging binds nothing: received/refused carry no native object and no dangling refusal.
    with pytest.raises(ValidationError, match="unproven native binding"):
        receipt(case_ref="case-ref-000000001")
    with pytest.raises(ValidationError, match="refusal evidence required"):
        receipt(lifecycle="refused")
    with pytest.raises(ValidationError, match="refusal evidence required"):
        receipt(refusal_code="SOURCE_UNAVAILABLE")
    # nothing outside the closed refusal set:
    with pytest.raises(ValidationError):
        receipt(lifecycle="refused", refusal_code="SOME_INVENTED_CODE")
    # the well-formed native bindings DO construct:
    assert receipt(lifecycle="responded", case_ref="case-ref-000000001").lifecycle == "responded"
    assert receipt(
        lifecycle="documents_pending", document_request_ref="request-ref-0000001"
    ).document_request_ref == "request-ref-0000001"
    assert receipt(lifecycle="refused", refusal_code="PHI_IN_COMMERCIAL_INPUT").refusal_code == (
        "PHI_IN_COMMERCIAL_INPUT"
    )
