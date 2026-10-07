"""PHI firewall of the OP16 vendor submission machine — EXECUTABLE negative guards (VW1-P4).

Guarda sob teste: `src/maezo/gateway/vendor_submissions.py` — `DECLARED_PAYLOAD_CLASSES` é o
único set declarado (`{"commercial"}`) e toda classe fora dele é classificada, AUDITADA e
recusada com `PHI_IN_COMMERCIAL_INPUT` com ZERO efeito a jusante: nenhuma instância persistida,
nenhuma consulta a fonte de estado (canal ou store), nenhum ALLOW na trilha.

Autoridade normativa citada (registros in-repo existentes; NÃO re-citada de memória):
- LGPD art. 11 (dado pessoal sensível de saúde) — base legal registrada para
  `dados_saude_prontuario` em `docs/sme-dispatch/dpo/RETENTION-MATRIX-CANDIDATE.md` (linhas da
  matriz `RETER_COM_BASE_LEGAL` e do bloco YAML `base_legal`);
- VW0-D16 (RATIFY-LATER DPO) — a perna de declaração de saúde (NB-12a) NÃO é declarada nesta
  onda; registrada no próprio módulo guardião (docstring e `DECLARED_PAYLOAD_CLASSES`) e na
  matriz de retenção (`portal_vendor_submissions`, migração 0020, "ZERO conteúdo de payload —
  a perna de declaração de saúde é RATIFY-LATER DPO (VW0-D16)");
- VW0-D11 (taxonomia, RATIFY-LATER DPO) — o LIMITE HONESTO deste firewall, documentado abaixo:
  a classificação é POR TOKEN. O envelope fechado não carrega campo de conteúdo a inspecionar
  (`ChannelSubmissionPayload` é `Closed`, `extra="forbid"`), logo nenhuma expressão executável
  de "payload comercial com conteúdo clínico" existe hoje — por isso esta suíte NÃO contém
  `xfail` de proxy: um xfail aqui seria fabricado (não há corpo executável que falhe por
  detecção ausente). Uma classe FUTURA declarada com semântica de saúde é decisão de taxonomia
  do DPO, não detecção de conteúdo.

NOTA DE EVIDÊNCIA: não existe snapshot em `evidence/regulatory/` nesta branch — as autoridades
acima são citadas pelos registros in-repo listados; gerar o snapshot verbatim é trabalho do
br-regulatory-analyst e está registrado no relatório desta suíte.

MÉTODO (prova de sensibilidade — casa WAVES §2/§3-cláusula-3): cada teste traz, no próprio
corpo, uma FASE DE VIOLAÇÃO via `monkeypatch.setattr` sobre o módulo guardião — o MESMO seam
que uma edição de fonte alteraria: o set declarado alargado, ou `_audit_record` vazando
conteúdo. A fase prova que a mutação MORDe (registro criado, estágio avançado, ALLOW auditado,
fonte consultada) — e é exatamente esse efeito a jusante que as asserções da fase guardada
recusam. Se o firewall ceder na fonte, a fase guardada passa a produzir o efeito e o teste fica
VERMELHO. Nenhuma mutação é commitada; nenhuma engine é mockada (os duplos abaixo são as
referências in-memory dos protocolos que a migração 0020/0019 constrangem — o mesmo molde da
suíte de feature `tests/unit/gateway/test_vendor_submissions.py`, repetido aqui de forma
autocontida para que esta guarda evolua independente da suíte de feature).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ConfigDict, ValidationError

from maezo.gateway import vendor_submissions as guard
from maezo.gateway.human.vendor_membership_administration import VendorChannelState
from maezo.gateway.vendor_submissions import (
    OPERATION_NAME,
    VendorSubmissionError,
    VendorSubmissionMachine,
    VendorSubmissionRefusal,
)
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding
from maezo.portal.contracts.vendor_submissions import (
    DECLARED_PAYLOAD_CLASS,
    ChannelSubmissionCommand,
    ChannelSubmissionPayload,
)

TENANT = "guard-tenant"
CHANNEL = "guard-channel-0001"
NOW = datetime.now(UTC)

#: The NB-12a health-declaration class — NOT declared in this wave (VW0-D16, RATIFY-LATER DPO).
CLINICAL_TOKEN = "declaracao_saude"

#: Obviously synthetic clinical content; never a real person's data. The envelope cannot carry
#: it (closed contract) — which is exactly what the proxy tests pin down.
SENTINEL = "SENTINELA-CLINICA-SINTETICA paciente-relata-dor-toracica"

CLINICAIS = (CLINICAL_TOKEN, "clinical", "prontuario", "exame_laboratorial", "cid10")


def vendor_membership(channel_ref: str = CHANNEL, **changes: Any) -> MembershipRecord:
    return MembershipRecord(
        **dict(
            tenant=TENANT,
            issuer="https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_TestPool",
            subject="00000000-0000-4000-8000-000000000042",
            principal_ref="vendor-principal-guard",
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
        command_id="command-guard-attempt01",
        channel_ref=CHANNEL,
        submission_ref="submission-guard-0001",
        business_revision="7",
        contract_ref="contract-guard-0001",
        payload=ChannelSubmissionPayload(content_class=DECLARED_PAYLOAD_CLASS),
    )
    values.update(changes)
    return ChannelSubmissionCommand(**values)


def clinical_command(**changes: Any) -> ChannelSubmissionCommand:
    return command(payload=ChannelSubmissionPayload(content_class=CLINICAL_TOKEN), **changes)


class CountingChannelDirectory:
    """Channel source that COUNTS queries — the firewall must never consult it for a refusal."""

    def __init__(self) -> None:
        self.queries = 0

    async def channel(self, tenant: str, channel_ref: str) -> VendorChannelState:
        self.queries += 1
        return VendorChannelState(
            tenant=tenant, channel_ref=channel_ref, revision=1, status="active", updated_at=NOW
        )


class CountingSubmissionStore:
    """Submission store that COUNTS reads and writes; UNIQUE on the 0020 business key."""

    def __init__(self) -> None:
        self.queries = 0
        self._rows: dict[tuple[str, str, str, str], Any] = {}

    async def find(self, tenant: str, submission_ref: str) -> tuple[Any, ...]:
        self.queries += 1
        return tuple(
            row for key, row in sorted(self._rows.items()) if key[0] == tenant and key[2] == submission_ref
        )

    async def record(self, submission: Any) -> None:
        key = (
            str(submission.tenant),
            OPERATION_NAME,
            submission.submission_ref,
            submission.business_revision,
        )
        if key in self._rows:
            raise ValueError("duplicate business key")
        self._rows[key] = submission

    def count(self) -> int:
        return len(self._rows)


class AuditTrail:
    def __init__(self) -> None:
        self.records: list[Any] = []

    async def emit(self, record: Any) -> str:
        self.records.append(record)
        return "guard-hash"

    def decisions(self) -> list[tuple[str, str]]:
        return [(record.decision, str(record.details.get("reason"))) for record in self.records]


def machine() -> tuple[VendorSubmissionMachine, CountingSubmissionStore, AuditTrail]:
    channels, store, audit = CountingChannelDirectory(), CountingSubmissionStore(), AuditTrail()
    return VendorSubmissionMachine(channels=channels, store=store, audit=audit), store, audit


def audit_surface(records: list[Any]) -> str:
    """Every audit field the guard's own trail carries, serialized for leak scanning."""
    return json.dumps([asdict(record) for record in records], ensure_ascii=False, default=str)


def assert_no_clinical_content(*surfaces: str) -> None:
    """The refusal may NAME the typed code; it may never echo clinical content or the token."""
    for surface in surfaces:
        for secret in (CLINICAL_TOKEN, SENTINEL):
            assert secret not in surface, f"clinical content leaked into a refusal surface: {secret!r}"


# ----------------------------------------------------------------- (i) clinical class refused


@pytest.mark.parametrize("clinica", CLINICAIS)
def test_classe_clinica_e_recusada_com_zero_efeito_e_a_mutacao_permissiva_cria_registro(
    monkeypatch: pytest.MonkeyPatch, clinica: str
) -> None:
    """(i)+(v) A declared-clinical token is a typed refusal with ZERO downstream effect; the
    violation phase proves the assertions bite: the SAME token under a widened declared set
    creates the instance the guarded phase must never create."""
    built, store, audit = machine()
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(
            built.submit(
                TENANT,
                vendor_membership(),
                command(payload=ChannelSubmissionPayload(content_class=clinica)),
            )
        )
    assert exc.value.reason is VendorSubmissionRefusal.PHI_IN_COMMERCIAL_INPUT
    assert store.count() == 0  # ZERO downstream effect
    assert audit.decisions() == [("DENY", "PHI_IN_COMMERCIAL_INPUT")]  # refusal IS audited
    assert_no_clinical_content(str(exc.value), audit_surface(audit.records))
    # violation phase (in-memory only): the class becomes declared — the effect APPEARS.
    monkeypatch.setattr(guard, "DECLARED_PAYLOAD_CLASSES", frozenset({DECLARED_PAYLOAD_CLASS, clinica}))
    permissive, permissive_store, permissive_audit = machine()
    receipt = asyncio.run(
        permissive.submit(
            TENANT, vendor_membership(), command(payload=ChannelSubmissionPayload(content_class=clinica))
        )
    )
    assert receipt.lifecycle == "received"  # the exact downstream effect the firewall forbids
    assert permissive_store.count() == 1
    assert permissive_audit.decisions() == [("ALLOW", "received")]


# ------------------------------------------------------- (ii) unknown class, idempotent refusal


def test_classe_desconhecida_e_idempotentemente_recusada_sem_instancia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(ii) An unknown class token is refused on EVERY attempt: two tries over the same business
    key yield two audited denials and zero instances; under the widened set the same two tries
    yield ONE replayed instance — proving the zero-instance assertion is load-bearing."""
    built, store, audit = machine()
    for attempt in ("command-guard-attempt01", "command-guard-attempt02"):
        with pytest.raises(VendorSubmissionError) as exc:
            asyncio.run(
                built.submit(
                    TENANT,
                    vendor_membership(),
                    command(
                        command_id=attempt,
                        payload=ChannelSubmissionPayload(content_class="protocolo_interno_desconhecido"),
                    ),
                )
            )
        assert exc.value.reason is VendorSubmissionRefusal.PHI_IN_COMMERCIAL_INPUT
    assert store.count() == 0
    assert audit.decisions() == [("DENY", "PHI_IN_COMMERCIAL_INPUT"), ("DENY", "PHI_IN_COMMERCIAL_INPUT")]
    # violation phase: with the class declared, attempt 1 creates and attempt 2 REPLAYS it.
    monkeypatch.setattr(
        guard,
        "DECLARED_PAYLOAD_CLASSES",
        frozenset({DECLARED_PAYLOAD_CLASS, "protocolo_interno_desconhecido"}),
    )
    permissive, permissive_store, permissive_audit = machine()
    first = asyncio.run(
        permissive.submit(
            TENANT,
            vendor_membership(),
            command(
                command_id="command-guard-attempt01",
                payload=ChannelSubmissionPayload(content_class="protocolo_interno_desconhecido"),
            ),
        )
    )
    second = asyncio.run(
        permissive.submit(
            TENANT,
            vendor_membership(),
            command(
                command_id="command-guard-attempt02",
                payload=ChannelSubmissionPayload(content_class="protocolo_interno_desconhecido"),
            ),
        )
    )
    assert first.submission_id == second.submission_id
    assert permissive_store.count() == 1
    assert permissive_audit.decisions() == [("ALLOW", "received"), ("ALLOW", "replayed_active_instance")]


# ------------------------------------------- (iii) the firewall decides BEFORE any source


def test_o_firewall_decide_antes_de_consultar_qualquer_fonte_de_estado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(iii) A clinical input learns NOTHING about channel state: the channel source and the
    submission store are never consulted on the refusal path; the widened set sends the SAME
    command through both sources — proving the never-consulted assertions have teeth."""
    channels, store, audit = CountingChannelDirectory(), CountingSubmissionStore(), AuditTrail()
    built = VendorSubmissionMachine(channels=channels, store=store, audit=audit)
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(built.submit(TENANT, vendor_membership(), clinical_command()))
    assert exc.value.reason is VendorSubmissionRefusal.PHI_IN_COMMERCIAL_INPUT
    assert channels.queries == 0  # the channel source is never consulted
    assert store.queries == 0  # the submission store is never read
    assert store.count() == 0  # and never written
    assert audit.decisions() == [("DENY", "PHI_IN_COMMERCIAL_INPUT")]
    # violation phase: the widened set pushes the SAME command through both sources.
    monkeypatch.setattr(
        guard, "DECLARED_PAYLOAD_CLASSES", frozenset({DECLARED_PAYLOAD_CLASS, CLINICAL_TOKEN})
    )
    p_channels, p_store, p_audit = CountingChannelDirectory(), CountingSubmissionStore(), AuditTrail()
    permissive = VendorSubmissionMachine(channels=p_channels, store=p_store, audit=p_audit)
    receipt = asyncio.run(permissive.submit(TENANT, vendor_membership(), clinical_command()))
    assert receipt.lifecycle == "received"
    assert p_channels.queries == 1  # eligibility consulted
    assert p_store.queries == 1  # idempotency lookup consulted
    assert p_store.count() == 1  # and the instance written
    assert p_audit.decisions() == [("ALLOW", "received")]


# -------------------------------------- (iv) refusal audited, ZERO clinical content leaked


def test_a_recusa_e_auditada_sem_vazar_conteudo_clinico_e_o_detector_tem_dentes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(iv) The refusal is audited and every guard-owned refusal surface (exception message,
    audit trail, wire shape-error) carries NO clinical content; the detector's teeth are proved
    by feeding it a deliberately leaking `_audit_record` — the same leak a source edit could
    introduce — and watching it raise."""
    built, store, audit = machine()
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(built.submit(TENANT, vendor_membership(), clinical_command()))
    assert exc.value.reason is VendorSubmissionRefusal.PHI_IN_COMMERCIAL_INPUT
    assert audit.decisions() == [("DENY", "PHI_IN_COMMERCIAL_INPUT")]
    surfaces = [str(exc.value), audit_surface(audit.records)]
    # the proxy attempt dies at the wire as a SHAPE error whose message hides the value too.
    with pytest.raises(ValidationError) as shape:
        ChannelSubmissionPayload(content_class=DECLARED_PAYLOAD_CLASS, anamnese=SENTINEL)
    surfaces.append(str(shape.value))
    assert_no_clinical_content(*surfaces)
    assert store.count() == 0
    # detector teeth (in-memory only): a leaking audit record IS caught by the same assertion.
    original_record = guard._audit_record

    def leaking_record(*, tenant: str, command: Any, decision: str, reason: str) -> Any:
        record = original_record(tenant=tenant, command=command, decision=decision, reason=reason)
        record.details = {
            **record.details,
            "rejected_payload": {"content_class": CLINICAL_TOKEN, "anamnese": SENTINEL},
        }
        return record

    monkeypatch.setattr(guard, "_audit_record", leaking_record)
    leaking_machine, _, leaking_audit = machine()
    with pytest.raises(VendorSubmissionError):
        asyncio.run(leaking_machine.submit(TENANT, vendor_membership(), clinical_command()))
    with pytest.raises(AssertionError, match="clinical content leaked"):
        assert_no_clinical_content(audit_surface(leaking_audit.records))


# -------------------------------------------- (v) the declared set is PINNED (VW0-D16 tripwire)


def test_o_set_declarado_esta_travado_e_declarar_a_classe_de_saude_e_o_cenario_vw0_d16(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The constants tripwire: the declared set is exactly `{"commercial"}` in source. The
    violation phase reenacts the precise VW0-D16 breach — someone "declares" the health
    class — and shows the downstream effect it would create (the effect phase (i) refuses)."""
    assert frozenset({DECLARED_PAYLOAD_CLASS}) == guard.DECLARED_PAYLOAD_CLASSES
    assert guard.DECLARED_PAYLOAD_CLASS == DECLARED_PAYLOAD_CLASS == "commercial"
    # violation phase, verbatim VW0-D16: the health-declaration class becomes declared.
    monkeypatch.setattr(
        guard, "DECLARED_PAYLOAD_CLASSES", frozenset({DECLARED_PAYLOAD_CLASS, CLINICAL_TOKEN})
    )
    built, store, audit = machine()
    receipt = asyncio.run(built.submit(TENANT, vendor_membership(), clinical_command()))
    assert receipt.lifecycle == "received"  # the exact effect the pin exists to prevent
    assert store.count() == 1
    assert audit.decisions() == [("ALLOW", "received")]


# ------------------------------------------- (vi) proxy of clinical content: honest limits


def test_o_envelope_fechado_nao_admite_campo_livre_e_o_proxy_de_conteudo_e_impossivel() -> None:
    """(iii-proxy) The contract is CLOSED: no free-text slot exists, so a clinical-content proxy
    inside a commercial payload is UNCONSTRUCTIBLE — the honest limit is that classification is
    token-bound (VW0-D11, RATIFY-LATER DPO), and no `xfail` is fabricated for a channel that
    structurally does not exist. The probe proves the ValidationError is `extra="forbid"`'s
    work, not a syntax accident: a permissive stand-in admits the field (it never reaches the
    machine)."""
    assert ChannelSubmissionPayload.model_config["extra"] == "forbid"
    assert ChannelSubmissionCommand.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError) as payload_shape:
        ChannelSubmissionPayload(content_class=DECLARED_PAYLOAD_CLASS, anamnese=SENTINEL)
    assert SENTINEL not in str(payload_shape.value)  # hide_input_in_errors holds on the wire too
    with pytest.raises(ValidationError) as command_shape:
        command(anamnese=SENTINEL)
    assert SENTINEL not in str(command_shape.value)
    # teeth: only the closed-config blocks the field.
    probe = type(
        "PermissivePayloadProbe",
        (ChannelSubmissionPayload,),
        {"model_config": ConfigDict(frozen=True, extra="allow", strict=True)},
    )
    leaked = probe(content_class=DECLARED_PAYLOAD_CLASS, anamnese=SENTINEL)
    assert leaked.anamnese == SENTINEL


# ----------------------------------- (vii) the refusal poisons nothing on the business key


def test_a_recusa_nao_envenena_a_business_key_e_so_a_classe_declarada_passa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused attempt leaves NO state: the same business key is still stageable with the
    declared class ([DENY, ALLOW]). Under the widened set attempt 1 itself creates the instance
    and attempt 2 hits the digest guard — proving the guarded audit sequence is load-bearing."""
    built, store, audit = machine()
    with pytest.raises(VendorSubmissionError) as first:
        asyncio.run(
            built.submit(TENANT, vendor_membership(), clinical_command(command_id="command-guard-attempt01"))
        )
    assert first.value.reason is VendorSubmissionRefusal.PHI_IN_COMMERCIAL_INPUT
    receipt = asyncio.run(
        built.submit(TENANT, vendor_membership(), command(command_id="command-guard-attempt02"))
    )
    assert receipt.lifecycle == "received"
    assert store.count() == 1
    assert audit.decisions() == [("DENY", "PHI_IN_COMMERCIAL_INPUT"), ("ALLOW", "received")]
    # violation phase: the clinical class declared — attempt 1 creates, attempt 2 is a digest clash.
    monkeypatch.setattr(
        guard, "DECLARED_PAYLOAD_CLASSES", frozenset({DECLARED_PAYLOAD_CLASS, CLINICAL_TOKEN})
    )
    permissive, permissive_store, _ = machine()
    first_mutated = asyncio.run(
        permissive.submit(TENANT, vendor_membership(), clinical_command(command_id="command-guard-attempt01"))
    )
    assert first_mutated.lifecycle == "received"
    assert permissive_store.count() == 1
    with pytest.raises(VendorSubmissionError) as clash:
        asyncio.run(
            permissive.submit(TENANT, vendor_membership(), command(command_id="command-guard-attempt02"))
        )
    assert clash.value.reason is VendorSubmissionRefusal.CONTRACT_MISMATCH
