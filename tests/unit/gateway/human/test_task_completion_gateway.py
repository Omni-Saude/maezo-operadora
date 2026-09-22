"""INTERIM completion (DL-0049): the gateway's authorization, order, scrub and trail.

Adversarial unit proofs over the SAME `HumanGateway` the two read routes use. The doubles here
are not a qualified engine, audit chain or credential: nothing in this file claims the interim
path works against CIB Seven or PostgreSQL. What it does prove is what the mandate's §2.3/§2.5
ask for, in code: the closed outcome domain with no default, the same-membership role/group
rule, the candidate-group rule, the session re-resolution after the remote reads, the
pseudonymization of `notas_resolucao` BEFORE the engine sees it, and two audit links carrying
who/basis/revision with the intent claim acting as the mutual-exclusion gate.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.gateway.human.test_gateway import CSRF, SCOPE, SECRET, setup, snapshot
from tests.unit.portal.test_human_session import membership

from maezo.gateway.human.completion import (
    CompletionAck,
    CompletionAuditEntry,
    CompletionAuditSink,
    PseudonymizedNotes,
    pseudonymize_notes,
)
from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.ports import BoundHumanPorts, HumanTaskTransport
from maezo.gateway.human.queue import ReadRefusalError
from maezo.portal.contracts.models import MembershipBinding

pytestmark = pytest.mark.asyncio

NOTES = "paciente orientado, contato 11 91234-5678 e email jo@ex.test, cpf 123.456.789-09"
CLINICAL_GROUP = "plantao-clinico"
HUMAN_GROUP = "atendimento-humano"


def real_now():
    """`datetime.now(UTC)` sem o stub de relogio dos testes desta secao."""
    return datetime.now(UTC)


def escalation_snapshot(**changes):
    """A real ESCALATION user task: the closed binding in `models.py` allows nothing else.

    `_binding_for(("SP-OP-ESCALATION-001", "UT_TratarEscalonamento"))` is `("escalation",
    "BPMN_FORMDATA")` and `_INPUTS_BY_FORM["escalation"]` is exactly the BPMN's two mandatory
    outputs, so this fixture cannot drift from the catalog without `TaskSnapshot` refusing.
    """
    data = dict(
        process_definition_key="SP-OP-ESCALATION-001",
        process_definition_id="escalation:1:id",
        task_definition_key="UT_TratarEscalonamento",
        form_key="escalation",
        form_source_status="BPMN_FORMDATA",
        eligible_candidate_groups=(CLINICAL_GROUP,),
        allowed_actions=("claim", "release"),
        allowed_inputs=("resultado", "notas_resolucao"),
    )
    data.update(changes)
    return snapshot(**data)


def staff_in(*groups: str):
    return membership(
        memberships=tuple(
            MembershipBinding(membership_ref=f"review-{g}", roles=("staff",), groups=(g,)) for g in groups
        )
    )


class RecordingCompletion:
    """The engine side of the interim path, recorded. Never an engine."""

    def __init__(self, *, fail: Exception | None = None):
        self.scope = SCOPE
        self.calls: list[tuple[str, str, PseudonymizedNotes, int]] = []
        self.fail = fail

    async def complete(self, *, task_id, resultado, notes, expected_task_revision):
        self.calls.append((task_id, resultado, notes, expected_task_revision))
        if self.fail is not None:
            raise self.fail
        return CompletionAck(
            task_id=task_id,
            resultado=resultado,
            consumed_task_revision=expected_task_revision,
            completed_at=datetime.now(UTC),
        )


class CompletingTransport(HumanTaskTransport):
    """Fake transport whose `complete_task` mirrors the REAL port, parameter for parameter.

    P-17 discipline (`tests/unit/runtime/test_inference_fakes_match_protocol.py`): a double that
    drifts from its protocol passes today and breaks the day the call site changes. The parity is
    asserted below instead of trusted.
    """

    def __init__(self, task, completion=None):
        self.scope = SCOPE
        self.task = task
        self.reads = 0
        self.completion = completion

    async def read_task(self, task_id):
        self.reads += 1
        return self.task

    async def complete_task(self, task_id, *, resultado, notes, expected_task_revision):
        if self.completion is None:
            return await super().complete_task(
                task_id,
                resultado=resultado,
                notes=notes,
                expected_task_revision=expected_task_revision,
            )
        return await self.completion.complete(
            task_id=task_id,
            resultado=resultado,
            notes=notes,
            expected_task_revision=expected_task_revision,
        )


class RecordingAudit(CompletionAuditSink):
    """One in-memory chain stand-in that keeps the ONE property under test: the dedup claim."""

    def __init__(self, *, fail_on: str | None = None, dedup: set[str] | None = None):
        self.scope = SCOPE
        self.entries: list[CompletionAuditEntry] = []
        self.claims: set[str] = set(dedup or ())
        self.fail_on = fail_on

    async def record(self, entry):
        self.entries.append(entry)
        if self.fail_on == entry.phase:
            raise RuntimeError("PRIVATE audit narrative")
        key = entry.dedup_key
        deduped = key in self.claims
        self.claims.add(key)
        return ("a" * 64 if entry.phase == "intent" else "b" * 64), deduped


async def harness(*, member=None, completion=None, audit=None, snap=None, **task_changes):
    g, store, transport, authority, admission = await setup(
        snap=snap or escalation_snapshot(),
        member=member or staff_in(CLINICAL_GROUP),
        **task_changes,
    )
    port = CompletingTransport(transport.task, completion)
    g._ports = BoundHumanPorts(port, authority, admission)
    g._completion_audit = audit if audit is not None else RecordingAudit()
    return g, port, g._completion_audit


async def close(g, **kw):
    values: dict[str, object] = dict(
        session_secret=SECRET,
        csrf_token=CSRF,
        task_id="task-1",
        resultado="resolvido_humano",
        notas_resolucao=NOTES,
    )
    values.update(kw)
    return await g.complete_task(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# The happy path, and what it leaves behind.
# ---------------------------------------------------------------------------------------
async def test_completion_scrubs_the_note_before_the_engine_and_leaves_both_audit_links():
    completion = RecordingCompletion()
    g, _, audit = await harness(completion=completion)
    result = await close(g)

    assert result.resultado == "resolvido_humano"
    assert result.consumed_task_revision == 2
    assert (result.audit_intent_ref, result.audit_result_ref) == ("a" * 64, "b" * 64)

    # The engine never sees the identifiers the colleague typed.
    (task_id, resultado, notes, revision) = completion.calls[0]
    assert (task_id, resultado, revision) == ("task-1", "resolvido_humano", 2)
    assert type(notes) is PseudonymizedNotes
    assert notes.text == pseudonymize_notes(NOTES).text
    for leaked in ("11 91234-5678", "jo@ex.test", "123.456.789-09"):
        assert leaked not in notes.text
    assert "[REDACTED_PHONE]" in notes.text and "[REDACTED_EMAIL]" in notes.text

    # Two links, in order, and the intent one commits BEFORE the engine call.
    assert [e.phase for e in audit.entries] == ["intent", "result"]
    assert audit.entries[0].outcome is None and audit.entries[1].outcome == "completed"


async def test_audit_entry_carries_who_on_what_basis_at_which_revision_and_no_narrative():
    g, _, audit = await harness(completion=RecordingCompletion())
    await close(g)
    entry = audit.entries[0]
    # WHO — pseudonymous server-side references, never the Cognito subject or an e-mail.
    resolved = await g._resolver.resolve(SECRET)
    assert entry.principal_ref == resolved.principal.principal_ref
    assert entry.session_ref == resolved.record.session_ref
    assert entry.membership_revision == resolved.principal.membership_revision
    assert resolved.principal.subject not in (entry.principal_ref, entry.session_ref)
    # ON WHAT BASIS — the outcome and the note's DIGEST, not the note.
    assert entry.resultado == "resolvido_humano"
    assert entry.notes_digest == pseudonymize_notes(NOTES).digest
    assert NOTES not in repr(entry) and pseudonymize_notes(NOTES).text not in repr(entry)
    # AT WHICH REVISION — task, authority and evidence all pinned.
    assert entry.snapshot.task_revision == 2
    assert entry.authority_revision == 7
    assert entry.snapshot.evidence_revision == 3
    # The claim is keyed on the revision, which is what makes it an effect gate.
    assert entry.dedup_key == "portal-direct-completion:intent:task-1:2"


# ---------------------------------------------------------------------------------------
# Fail-closed: the outcome domain, the capability, the contract.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "resultado", ["", "RESOLVIDO_HUMANO", "resolvido", "aprovar", "devolvido agente", "None"]
)
async def test_outcome_outside_the_bpmn_domain_is_refused_with_no_engine_call(resultado):
    completion = RecordingCompletion()
    g, _, audit = await harness(completion=completion)
    with pytest.raises(ReadRefusalError) as refusal:
        await close(g, resultado=resultado)
    assert refusal.value.code == "invalid_request"
    assert completion.calls == [] and audit.entries == []


@pytest.mark.parametrize("notes", ["", "   ", "\n\t "])
async def test_blank_note_is_refused_with_no_engine_call(notes):
    completion = RecordingCompletion()
    g, _, _ = await harness(completion=completion)
    with pytest.raises(ReadRefusalError):
        await close(g, notas_resolucao=notes)
    assert completion.calls == []


async def test_without_the_interim_capability_the_transport_refuses_through_the_base_port():
    """The shipped state: no `DirectTaskCompletion` installed anywhere."""
    g, port, audit = await harness(completion=None)
    with pytest.raises(GatewayRefusalError) as refusal:
        await close(g)
    assert refusal.value.code == "production_capabilities_unavailable"
    # The intent link was already claimed; the result link was not.
    assert [e.phase for e in audit.entries] == ["intent"]


async def test_without_an_audit_sink_nothing_is_completed():
    """A completion with no trail is exactly what §2.5 refuses."""
    completion = RecordingCompletion()
    g, _, _ = await harness(completion=completion)
    g._completion_audit = None
    with pytest.raises(GatewayRefusalError) as refusal:
        await close(g)
    assert refusal.value.code == "production_capabilities_unavailable"
    assert completion.calls == []


async def test_audit_sink_from_another_tenant_is_refused():
    completion = RecordingCompletion()
    audit = RecordingAudit()
    audit.scope = SCOPE.model_copy(update={"tenant": "other-tenant"})
    g, _, _ = await harness(completion=completion, audit=audit)
    with pytest.raises(GatewayRefusalError):
        await close(g)
    assert completion.calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"form_key": "auth_decisao", "process_definition_key": "SP-OP-AUTH-001"},
        {"assignee_ref": "another-colleague"},
    ],
)
async def test_only_an_escalation_task_that_is_not_someone_elses_is_completed(change):
    if "form_key" in change:
        snap = snapshot(
            process_definition_key="SP-OP-AUTH-001",
            process_definition_id="auth:1:id",
            task_definition_key="UT_AnaliseMedicoAuditor",
            form_key="auth_decisao",
            eligible_candidate_groups=(CLINICAL_GROUP,),
            allowed_actions=("claim", "release"),
        )
    else:
        snap = escalation_snapshot(**change)
    completion = RecordingCompletion()
    g, _, audit = await harness(completion=completion, snap=snap)
    with pytest.raises(GatewayRefusalError) as refusal:
        await close(g)
    assert refusal.value.code == "operation_forbidden"
    assert completion.calls == [] and audit.entries == []


# ---------------------------------------------------------------------------------------
# Tenant/group isolation — the mandate's §2.3 authorization list, proved one rule at a time.
# ---------------------------------------------------------------------------------------
async def test_a_colleague_from_another_group_cannot_close_the_case():
    completion = RecordingCompletion()
    g, _, audit = await harness(member=staff_in(HUMAN_GROUP), completion=completion)
    with pytest.raises(GatewayRefusalError) as refusal:
        await close(g)
    assert refusal.value.code == "operation_forbidden"
    assert completion.calls == [] and audit.entries == []


async def test_role_and_group_must_coincide_in_the_same_membership():
    """Role here, group there is NOT authorization — `_authorized`'s same-`m` rule."""
    split = membership(
        memberships=(
            MembershipBinding(membership_ref="role-only", roles=("staff",), groups=("outra-fila",)),
            MembershipBinding(membership_ref="group-only", roles=("leitor",), groups=(CLINICAL_GROUP,)),
        )
    )
    completion = RecordingCompletion()
    g, _, _ = await harness(member=split, completion=completion)
    with pytest.raises(GatewayRefusalError) as refusal:
        await close(g)
    assert refusal.value.code == "operation_forbidden"
    assert completion.calls == []


@pytest.mark.parametrize("audience", ["beneficiary", "provider"])
async def test_only_the_staff_audience_reaches_this_route(audience):
    from maezo.portal.contracts.models import SubjectBinding

    member = membership(
        audience=audience,
        subject_bindings=(SubjectBinding(kind=audience, resource_ref="resource-1"),),
        memberships=(
            MembershipBinding(membership_ref="review-1", roles=("staff",), groups=(CLINICAL_GROUP,)),
        ),
    )
    completion = RecordingCompletion()
    g, _, _ = await harness(member=member, completion=completion)
    with pytest.raises(ReadRefusalError) as refusal:
        await close(g)
    assert refusal.value.code == "employee_access_required"
    assert completion.calls == []


@pytest.mark.parametrize("csrf", ["", "x" * 43, CSRF[:-1] + "z"])
async def test_the_csrf_token_is_compared_against_the_server_side_session(csrf):
    completion = RecordingCompletion()
    g, _, _ = await harness(completion=completion)
    with pytest.raises(ReadRefusalError) as refusal:
        await close(g, csrf_token=csrf)
    assert refusal.value.code == "session_unavailable"
    assert completion.calls == []


# ---------------------------------------------------------------------------------------
# Order and conflict.
# ---------------------------------------------------------------------------------------
async def test_session_is_resolved_again_after_the_remote_reads_and_a_moved_membership_conflicts(
    monkeypatch,
):
    completion = RecordingCompletion()
    g, _, _ = await harness(completion=completion)
    original = await g._resolver.resolve(SECRET)
    moved = replace(
        original,
        principal=original.principal.model_copy(update={"membership_revision": 99}),
    )
    calls = {"n": 0}

    async def resolve(secret):
        calls["n"] += 1
        return original if calls["n"] == 1 else moved

    monkeypatch.setattr(g._resolver, "resolve", resolve)
    with pytest.raises(GatewayRefusalError) as refusal:
        await close(g)
    assert refusal.value.code == "revision_conflict"
    # Two resolutions: one before the reads, one after them and BEFORE the effect.
    assert calls["n"] == 2
    assert completion.calls == []


async def test_a_second_completion_of_the_same_revision_is_refused_by_the_audit_claim():
    completion = RecordingCompletion()
    audit = RecordingAudit(dedup={"portal-direct-completion:intent:task-1:2"})
    g, _, _ = await harness(completion=completion, audit=audit)
    with pytest.raises(GatewayRefusalError) as refusal:
        await close(g)
    assert refusal.value.code == "revision_conflict"
    assert completion.calls == []


async def test_a_lapsed_authority_refuses_before_the_effect():
    completion = RecordingCompletion()
    g, _, _ = await harness(completion=completion, valid_until=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(GatewayRefusalError) as refusal:
        await close(g)
    assert refusal.value.code in ("authority_unavailable", "task_unavailable")
    assert completion.calls == []


# ---------------------------------------------------------------------------------------
# A cerca ENTRE a claim de intent e o efeito (review DL-0049, rodaquino, 21/09/2026).
#
# O passo 3 (resolver a sessao de novo) acontecia antes do passo 4 (gravar o intent), e o passo 4
# e' um INSERT sob `pg_advisory_xact_lock` por tenant: sob contencao ele ESPERA. Uma autoridade
# revogada ou um prazo lapsado nessa espera ainda produziam o efeito no motor — e este caminho e'
# o REST cru, sem a cerca otimista do rele duravel. Os dois testes abaixo exercitam exatamente a
# janela: eles mudam o mundo DENTRO do `record` do elo de intent.
# ---------------------------------------------------------------------------------------
async def test_membership_revogada_entre_a_claim_e_o_efeito_recusa_e_fecha_a_claim(monkeypatch):
    """Revogacao durante a gravacao do intent: nada vai ao motor, e a claim nao fica pendurada."""
    completion = RecordingCompletion()
    g, _, audit = await harness(completion=completion)
    original = await g._resolver.resolve(SECRET)
    movido = replace(original, principal=original.principal.model_copy(update={"membership_revision": 99}))
    chamadas = {"n": 0}

    async def resolve(secret):
        chamadas["n"] += 1
        # 1 e 2 sao os passos 1 e 3 (autorizacao normal); a revogacao aparece SO' na terceira,
        # que e' a leitura nova, imediatamente antes do efeito.
        return original if chamadas["n"] <= 2 else movido

    monkeypatch.setattr(g._resolver, "resolve", resolve)

    with pytest.raises(GatewayRefusalError) as recusa:
        await close(g)

    assert recusa.value.code == "authority_unavailable"
    assert completion.calls == [], "o efeito foi enviado com autoridade revogada"
    assert chamadas["n"] == 3, "a terceira leitura (antes do efeito) nao aconteceu"
    fases = [(e.phase, e.outcome) for e in audit.entries]
    assert fases == [("intent", None), ("result", "refused_membership_revoked")], fases


async def test_prazo_lapsado_entre_a_claim_e_o_efeito_recusa_e_diz_que_foi_relogio(monkeypatch):
    """O outro motivo, com o outro desfecho: quem audita separa governanca de relogio."""
    import maezo.gateway.human.gateway as modulo

    completion = RecordingCompletion()
    g, _, audit = await harness(completion=completion)
    real = modulo.datetime
    estado = {"depois_do_intent": False}

    class _Relogio:
        """`now()` salta para o futuro assim que o elo de intent e' gravado."""

        @staticmethod
        def now(tz=None):
            agora = real.now(tz)
            return agora + timedelta(hours=2) if estado["depois_do_intent"] else agora

    monkeypatch.setattr(modulo, "datetime", _Relogio)
    registrar = audit.record

    async def record(entry):
        resultado = await registrar(entry)
        if entry.phase == "intent":
            estado["depois_do_intent"] = True
        return resultado

    monkeypatch.setattr(audit, "record", record)

    with pytest.raises(GatewayRefusalError) as recusa:
        await close(g)

    assert recusa.value.code == "authority_unavailable"
    assert completion.calls == [], "o efeito foi enviado com o prazo vencido"
    fases = [(e.phase, e.outcome) for e in audit.entries]
    assert fases == [("intent", None), ("result", "refused_deadline_lapsed")], fases


async def test_o_cookie_expirando_nessa_janela_nao_recusa(monkeypatch):
    """A assimetria declarada: `record.expires_at` nao entra na cerca pre-efeito.

    A pessoa clicou com sessao valida. Um cookie que expira durante um INSERT nao torna a decisao
    dela invalida — e recusar ali travaria a revisao para todo mundo, porque a claim de intent
    tem `dedup_key` de tarefa + revisao e nao inclui quem pediu.
    """
    completion = RecordingCompletion()
    g, _, audit = await harness(completion=completion)
    original = await g._resolver.resolve(SECRET)
    expirado = replace(
        original,
        record=original.record.model_copy(update={"expires_at": real_now() - timedelta(seconds=1)}),
    )
    chamadas = {"n": 0}

    async def resolve(secret):
        chamadas["n"] += 1
        return original if chamadas["n"] <= 2 else expirado

    monkeypatch.setattr(g._resolver, "resolve", resolve)

    resultado = await close(g)

    assert completion.calls, "a conclusao foi barrada por um cookie expirado, e nao deveria"
    assert resultado.resultado == "resolvido_humano"
    assert [(e.phase, e.outcome) for e in audit.entries] == [
        ("intent", None),
        ("result", "completed"),
    ]


async def test_a_proven_engine_conflict_surfaces_as_409_after_the_intent_claim():
    completion = RecordingCompletion(fail=GatewayRefusalError("revision_conflict"))
    g, _, audit = await harness(completion=completion)
    with pytest.raises(GatewayRefusalError) as refusal:
        await close(g)
    assert refusal.value.code == "revision_conflict"
    assert [e.phase for e in audit.entries] == ["intent"]


async def test_an_unreadable_engine_answer_is_uncertain_not_a_conflict():
    completion = RecordingCompletion(fail=GatewayRefusalError("admission_unavailable"))
    g, _, _ = await harness(completion=completion)
    with pytest.raises(GatewayRefusalError) as refusal:
        await close(g)
    assert refusal.value.code == "admission_unavailable"


async def test_a_failed_result_link_never_claims_the_completion_was_rolled_back():
    """The effect committed; only its result link is missing. The caller hears 'uncertain'."""
    completion = RecordingCompletion()
    g, _, audit = await harness(completion=completion, audit=RecordingAudit(fail_on="result"))
    with pytest.raises(GatewayRefusalError) as refusal:
        await close(g)
    assert refusal.value.code == "admission_unavailable"
    assert len(completion.calls) == 1  # it DID happen
    assert "PRIVATE audit narrative" not in str(refusal.value)
