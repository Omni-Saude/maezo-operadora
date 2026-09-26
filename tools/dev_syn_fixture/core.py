"""Nucleo da fixture SINTETICA AUTH: payloads puros + operacoes REST/banco parametrizadas.

Nada aqui conhece caminho de volume, host ou segredo: quem chama (harness C1 ou executor de dev)
passa conexoes, clientes e referencias. Tudo o que e escrito leva o prefixo `SYN-`; nenhum dado
clinico, so os campos estruturais minimos (procedimento de consulta, valor simbolico).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from maezo.gateway.human.auth_profile import (
    Actor,
    AuditIntent,
    AuditIntentPayload,
    Definition,
    DocumentPolicy,
    GuideIdentity,
    HumanStartCommand,
    InputPublication,
    Pin,
    ResourceAuthority,
    Scope,
    SessionBinding,
    StartFacts,
)
from maezo.gateway.human.auth_projection import projection_digest, start_variables
from maezo.gateway.human.auth_transport import AuthCredentialLease, AuthEffectCeiling, sign_request
from maezo.gateway.human.read_profile import ArtifactPin, MembershipProjection, SourceProvenance, digest
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding
from maezo.portal.engine.profile import canonicalize
from maezo.tools.process_business_keys import auth_business_key

PREFIX = "SYN-"
GUIDE_PATTERN = re.compile(r"^SYN-[A-Z0-9]+$")
PUBLICATION_PURPOSE = "human-auth-input-publication"
START_PURPOSE = "human-auth-start"
AUTH_PROCESS = "SP-OP-AUTH-001"
ESCALATION_PROCESS = "SP-OP-ESCALATION-001"
HUMAN_TASK = "UT_TratarEscalonamento"
#: As duas tarefas externas da escalacao antes da tarefa humana.
ESCALATION_TOPICS = ("operadora.events.publish", "operadora.escalation.notify_team")
SYN_ISSUER_BASE = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_SYN"
SUBJECT = "00000000-0000-4000-8000-00000000c1a1"


class SyntheticRefusedError(RuntimeError):
    """Uma referencia ou guia nao e sintetica (`SYN-`): nada e escrito."""


@dataclass(frozen=True)
class FixtureRefs:
    """As referencias opacas da fixture. `for_label` gera todas a partir de um rotulo e da guia."""

    label: str
    guide_number: str
    workload: str
    publisher: str
    source: str
    guide_ref: str
    intake: str
    command: str
    intent: str
    issuer: str
    audience: str
    principal: str = "SYN-provider-principal-1"
    provider: str = "SYN-provider-1"
    beneficiary: str = "SYN-beneficiary-1"
    assessment: str = "SYN-assessment-1"
    subject: str = SUBJECT
    keys: dict[str, str] = field(default_factory=dict)

    @classmethod
    def for_label(cls, label: str, guide_number: str, *, audience: str) -> FixtureRefs:
        return cls(
            label=label,
            guide_number=guide_number,
            workload=f"SYN-{label}-auth-fixture",
            publisher=f"SYN-{label}-fixture-publisher",
            source=f"SYN-{label}-fixture-source",
            guide_ref=f"SYN-guide-{label}-0001",
            intake=f"SYN-intake-{label}-0001",
            command=f"SYN-command-{label}-0001",
            intent=f"SYN-intent-{label}-0001",
            issuer=f"{SYN_ISSUER_BASE}{label.replace('-', '')}Fixture",
            audience=audience,
            keys={
                PUBLICATION_PURPOSE: f"SYN-{label}-fixture-publication",
                START_PURPOSE: f"SYN-{label}-fixture-start",
            },
        )

    def synthetic_values(self) -> tuple[str, ...]:
        return (
            self.guide_number,
            self.workload,
            self.publisher,
            self.source,
            self.guide_ref,
            self.intake,
            self.command,
            self.intent,
            self.principal,
            self.provider,
            self.beneficiary,
            self.assessment,
            *self.keys.values(),
        )

    def check(self) -> None:
        """Recusa (antes de qualquer escrita) guia fora de `^SYN-[A-Z0-9]+$` ou referencia sem `SYN-`."""
        if not GUIDE_PATTERN.fullmatch(self.guide_number):
            raise SyntheticRefusedError(f"guia fora de {GUIDE_PATTERN.pattern}: {self.guide_number!r}")
        for value in self.synthetic_values():
            if not value.startswith(PREFIX):
                raise SyntheticRefusedError(f"referencia sem prefixo {PREFIX}: {value}")
        if set(self.keys) != {PUBLICATION_PURPOSE, START_PURPOSE}:
            raise SyntheticRefusedError("chaves da fixture incompletas")


def auth_instance_key(tenant: str, guide_number: str) -> str:
    return auth_business_key(tenant_id=tenant, numero_guia_tiss=guide_number)


def escalation_key(tenant: str, guide_number: str) -> str:
    return f"ESC-{tenant}-sla-auth-{guide_number}"


def refuse_non_synthetic(guides: Iterable[str]) -> None:
    """Recusa se QUALQUER guia ja reivindicada no tenant nao for `SYN-` (existe intake real)."""
    real = sorted(g for g in guides if not str(g).startswith(PREFIX))
    if real:
        raise SyntheticRefusedError(f"{len(real)} guia(s) nao-{PREFIX} no tenant: recusado")


# --- chaves e designacoes -----------------------------------------------------------------------


def spki(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def key_designation(
    refs: FixtureRefs,
    purpose: str,
    key: Ed25519PrivateKey,
    peer: str,
    grants: list[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    return dict(
        schema="human-auth-key-designation.v1",
        key_id=refs.keys[purpose],
        issuer=refs.workload,
        purpose=purpose,
        peer_spki_sha256=peer,
        public_key_base64=base64.b64encode(spki(key)).decode("ascii"),
        not_before=iso(start),
        not_after=iso(end),
        source_grants=grants if purpose == PUBLICATION_PURPOSE else [],
    )


def result_designation(
    result_spki: bytes, key_id: str, issuer: str, start: datetime, end: datetime
) -> dict[str, Any]:
    """O signatario de RESULTADO do engine: sem a designacao do dono todo efeito AUTH e recusado."""
    return dict(
        schema="human-auth-key-designation.v1",
        key_id=key_id,
        issuer=issuer,
        purpose="human-auth-result",
        peer_spki_sha256=hashlib.sha256(result_spki).hexdigest(),
        public_key_base64=base64.b64encode(result_spki).decode("ascii"),
        not_before=iso(start),
        not_after=iso(end),
        source_grants=[],
    )


async def designate(
    owner: Any, native_schema: str, tenant: str, designations: Iterable[dict[str, Any]]
) -> None:
    """Substitui (idempotente) as linhas `mzo_auth_trust` pelo dono nativo, numa transacao."""
    async with owner.transaction():
        await owner.execute(f"SET LOCAL search_path = {native_schema}")
        for designation in designations:
            await owner.execute(
                "DELETE FROM mzo_auth_trust WHERE tenant_=$1 AND key_id_=$2", tenant, designation["key_id"]
            )
            await owner.execute(
                "INSERT INTO mzo_auth_trust(tenant_,key_id_,designation_) VALUES($1,$2,$3)",
                tenant,
                designation["key_id"],
                canonicalize(designation).decode(),
            )


def lease(
    refs: FixtureRefs,
    purpose: str,
    key: Ed25519PrivateKey,
    scope: Scope,
    peer: str,
    start: datetime,
    end: datetime,
) -> AuthCredentialLease:
    return AuthCredentialLease(
        scope=scope,
        purpose=purpose,  # type: ignore[arg-type]
        workload_ref=refs.workload,
        key_id=refs.keys[purpose],
        audience=refs.audience,
        public_key_sha256=hashlib.sha256(spki(key)).hexdigest(),
        peer_spki_sha256=peer,
        not_before=start,
        valid_until=end,
        max_envelope_seconds=50,
        key=key,
        live=lambda: None,
    )


# --- payloads do intake -------------------------------------------------------------------------


@dataclass(frozen=True)
class Inputs:
    actor: Actor
    session: SessionBinding
    facts: StartFacts
    admitted: str
    #: (kind, resource_ref, payload, provenance), na ordem de publicacao.
    items: tuple[tuple[str, str, Any, SourceProvenance], ...]

    def grants(self, refs: FixtureRefs) -> list[dict[str, Any]]:
        return [
            dict(kind=kind, source_ref=refs.source, publisher_ref=refs.publisher, resource_ref=ref)
            for kind, ref, _, _ in self.items
        ]


def build_inputs(refs: FixtureRefs, start: datetime, until: datetime, cutover: str) -> Inputs:
    """As 6 entradas `SYN-` do start.

    actor, resource_authority, guide, start_facts, document_policy e audit_intent.
    """
    label = refs.label.encode()

    def source(receipt: str) -> SourceProvenance:
        return SourceProvenance(
            publisher_ref=refs.publisher,
            source_ref=refs.source,
            source_revision=1,
            source_digest=hashlib.sha256(b"SYN " + label + b" fixture").hexdigest(),
            receipt_ref=f"SYN-receipt-{receipt}",
            observed_at=start,
            valid_until=until,
        )

    actor = Actor(
        principal_ref=refs.principal,
        issuer=refs.issuer,
        subject=refs.subject,
        membership_revision=1,
        audience="provider",
    )
    membership = MembershipProjection(
        principal_ref=refs.principal,
        issuer=refs.issuer,
        subject=refs.subject,
        membership_revision=1,
        audience="provider",
        memberships=(MembershipBinding(membership_ref="SYN-membership-1", roles=("provider",), groups=()),),
        subject_bindings=(SubjectBinding(kind="provider", resource_ref=refs.provider),),
        state="active",
        reviewed_until=until,
    )
    session = SessionBinding(
        session_ref="SYN-session-1",
        authenticated_at=start,
        session_expires_at=until,
        authorization_until=until,
        session_source_revision=1,
        session_record_digest=hashlib.sha256(b"SYN session").hexdigest(),
    )
    admitted = hashlib.sha256(b"SYN " + label + b" admitted request").hexdigest()
    intent = AuditIntentPayload(
        intent_ref=refs.intent,
        intake_or_response_ref=refs.intake,
        command_id=refs.command,
        actor=actor,
        admitted_digest=admitted,
        operation="auth.start",
        state="committed",
        admitted_at=start + timedelta(seconds=1),
        session_binding=session,
    )
    authority = ResourceAuthority(
        authority_ref="SYN-authority-1",
        actor=actor,
        beneficiary_ref=refs.beneficiary,
        provider_ref=refs.provider,
        resource_kind="guide",
        resource_ref=refs.guide_ref,
        action="auth.start",
        request_ref=None,
        relationship_revision=1,
        consent_revision=1,
        grant_ref="SYN-grant-1",
        basis_ref="SYN-basis-1",
        legal_basis="other_qualified_basis",
        consent_state="not_required",
        state="active",
        valid_from=start,
        valid_until=until,
        source=source("authority"),
    )
    guide = GuideIdentity(
        guide_identity_ref=refs.guide_ref,
        source_ref=refs.source,
        namespace_ref="SYN-namespace",
        source_guide_ref="SYN-source-guide-1",
        numero_guia_tiss=refs.guide_number,
        cutover_ref=cutover,
        cutover_revision=1,
        legacy_state="absent_at_cutover",
        prior_instance_id=None,
        prior_case_ref=None,
        source=source("guide"),
    )
    policy_pin = ArtifactPin(artifact_ref="SYN-policy-1", digest=hashlib.sha256(b"SYN policy").hexdigest())
    facts = StartFacts(
        facts_ref="SYN-facts-1",
        intake_ref=refs.intake,
        guide_identity_ref=refs.guide_ref,
        beneficiary_pseudo_id=refs.beneficiary,
        provider_ref=refs.provider,
        procedure_code="10101012",
        category="consulta",
        character="eletivo",
        claimed_amount_cents=100,
        document_refs=(),
        requer_autorizacao=True,
        beneficiario_ativo=True,
        carencia_cumprida=True,
        documentacao_completa=True,
        missing_requirement_codes=(),
        documentary_assessment_ref=refs.assessment,
        request_digest=admitted,
        factual_sources=(source("facts"),),
        policy_artifacts=(policy_pin,),
    )
    policy = DocumentPolicy(
        assessment_ref=refs.assessment,
        resource_kind="intake",
        resource_ref=refs.intake,
        request_ref=None,
        request_revision=0,
        policy=policy_pin,
        policy_revision=1,
        recipient_principal_refs=(refs.principal,),
        required_codes=(),
        missing_codes=(),
        submitted_response_digest=None,
        effective_document_refs=(),
        document_set_digest=digest(()),
        complete=True,
        source=source("policy"),
        valid_until=until,
    )
    items = (
        ("actor", refs.principal, membership, source("actor")),
        ("resource_authority", "SYN-authority-1", authority, authority.source),
        ("guide", refs.guide_ref, guide, guide.source),
        ("start_facts", "SYN-facts-1", facts, source("facts-head")),
        ("document_policy", refs.assessment, policy, policy.source),
        ("audit_intent", refs.intent, intent, source("intent")),
    )
    return Inputs(actor=actor, session=session, facts=facts, admitted=admitted, items=items)


def publication(
    refs: FixtureRefs,
    scope: Scope,
    kind: str,
    ref: str,
    payload: Any,
    provenance: SourceProvenance,
    until: datetime,
    expected_generation: int = 0,
) -> InputPublication:
    """`publication_id` e unico por tenant: uma republicacao (geracao > 0) ganha sufixo `-g{n}`, e
    cada guia (rotulo) tem os seus — senao a 2a guia `SYN-` colide com a 1a (409, medido 25/09)."""
    suffix = "" if expected_generation == 0 else f"-g{expected_generation + 1}"
    return InputPublication.model_validate(
        dict(
            schema="human-auth-input-publication.v1",
            scope=scope,
            workload_ref=refs.workload,
            publication_id=f"SYN-publication-{refs.label}-{kind}{suffix}",
            kind=kind,
            resource_ref=ref,
            expected_generation=expected_generation,
            source=provenance,
            state="active",
            payload=payload,
            payload_digest=digest(payload),
            valid_until=until,
        )
    )


def start_command(
    refs: FixtureRefs,
    scope: Scope,
    definition: Definition,
    inputs: Inputs,
    heads: dict[str, tuple[str, int, SourceProvenance, str]],
) -> HumanStartCommand:
    pins = tuple(
        sorted(
            (
                Pin(
                    kind=kind,  # type: ignore[arg-type]
                    resource_ref=ref,
                    head_generation=generation,
                    source=provenance,
                    payload_digest=payload,
                )
                for kind, (ref, generation, provenance, payload) in heads.items()
                if kind != "audit_intent"
            ),
            key=lambda p: (p.kind, p.resource_ref),
        )
    )
    return HumanStartCommand.model_validate(
        dict(
            schema="human-auth-start.v1",
            scope=scope,
            workload_ref=refs.workload,
            actor=inputs.actor,
            intake_ref=refs.intake,
            command_id=refs.command,
            admission=AuditIntent(
                intent_ref=refs.intent,
                admitted_command_id=refs.command,
                admitted_digest=inputs.admitted,
                source=heads["audit_intent"][2],
            ),
            guide_identity_ref=refs.guide_ref,
            definition=definition,
            input_pins=pins,
            start_facts_ref="SYN-facts-1",
            start_facts_digest=digest(inputs.facts),
            projected_variables_digest=projection_digest(start_variables(scope.tenant, inputs.facts)),
        )
    )


def post(client: httpx.Client, path: str, raw: bytes) -> tuple[int, dict[str, Any]]:
    response = client.post(path, content=raw, headers={"content-type": "application/json"})
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:200]}
    return response.status_code, body


def find(value: object, name: str) -> object:
    """O campo `name` no primeiro nivel em que aparecer (o resultado nativo vem envelopado e assinado)."""
    if isinstance(value, dict):
        if name in value:
            return value[name]
        for item in value.values():
            found = find(item, name)
            if found is not None:
                return found
    return None


@dataclass
class StartOutcome:
    publications: list[str]
    status: int | None = None
    body: dict[str, Any] = field(default_factory=dict)
    failed: str | None = None

    @property
    def outcome(self) -> object:
        return find(self.body, "outcome")

    @property
    def ok(self) -> bool:
        return self.failed is None and self.status == 200 and self.outcome in ("started", "existing")


def publish_and_start(
    client: httpx.Client,
    refs: FixtureRefs,
    scope: Scope,
    definition: Definition,
    keys: dict[str, Ed25519PrivateKey],
    peer: str,
    inputs: Inputs,
    *,
    start: datetime,
    until: datetime,
    end: datetime,
    now: Callable[[], datetime],
    expected: dict[str, int] | None = None,
    base: str = "/maezo-human/v1",
) -> StartOutcome:
    """Publica as 6 entradas e envia o `human-auth-start.v1` assinado. `expected`: geracao atual por kind."""
    expected = expected or {}
    publication_lease = lease(refs, PUBLICATION_PURPOSE, keys[PUBLICATION_PURPOSE], scope, peer, start, end)
    heads: dict[str, tuple[str, int, SourceProvenance, str]] = {}
    result = StartOutcome(publications=[])
    for kind, ref, payload, provenance in inputs.items:
        item = publication(refs, scope, kind, ref, payload, provenance, until, expected.get(kind, 0))
        status, body = post(
            client, f"{base}/auth-input-publication", sign_request(item, publication_lease, now())
        )
        result.publications.append(f"{kind}={status}")
        heads[kind] = (ref, int(str(find(body, "head_generation") or 0)), provenance, digest(payload))
        if status != 200:
            result.failed, result.body = kind, body
            result.status = status
            return result
    command = start_command(refs, scope, definition, inputs, heads)
    ceiling = AuthEffectCeiling(command_digest=digest(command), session=inputs.session, valid_until=until)
    start_lease = lease(refs, START_PURPOSE, keys[START_PURPOSE], scope, peer, start, end)
    result.status, result.body = post(
        client, f"{base}/auth-start", sign_request(command, start_lease, now(), effect_ceiling=ceiling)
    )
    return result


async def input_generations(owner: Any, native_schema: str, tenant: str, inputs: Inputs) -> dict[str, int]:
    """A geracao atual de cada entrada da fixture em `mzo_auth_input_head` (0 = nunca publicada)."""
    found: dict[str, int] = {}
    async with owner.transaction():
        await owner.execute(f"SET LOCAL search_path = {native_schema}")
        for kind, ref, _, _ in inputs.items:
            generation = await owner.fetchval(
                "SELECT generation_ FROM mzo_auth_input_head WHERE tenant_=$1 AND kind_=$2 AND resource_=$3",
                tenant,
                kind,
                ref,
            )
            found[kind] = int(generation or 0)
    return found


async def claimed_guides(owner: Any, native_schema: str, tenant: str) -> list[str]:
    async with owner.transaction():
        await owner.execute(f"SET LOCAL search_path = {native_schema}")
        rows = await owner.fetch("SELECT guide_ FROM mzo_auth_guide_claim WHERE tenant_=$1", tenant)
    return [row["guide_"] for row in rows]


# --- qualificacao AUTH (D8 / B12) ---------------------------------------------------------------


def deploy(client: httpx.Client, tenant: str, spec: Any, name: str) -> dict[str, Any]:
    """Deploy (filtro de duplicata) de SP-OP-AUTH-001, da escalacao e da DMN `escalation_routing`."""
    files = {
        "auth": (
            "SP-OP-AUTH-001_Autorizacao_Previa.bpmn",
            (spec / "bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn").read_bytes(),
        ),
        "esc": (
            "SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn",
            (spec / "bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn").read_bytes(),
        ),
        "dmn": ("escalation_routing.dmn", (spec / "dmn/escalation_routing.dmn").read_bytes()),
    }
    response = client.post(
        "/deployment/create",
        files=files,
        data={"deployment-name": name, "tenant-id": tenant, "enable-duplicate-filtering": "true"},
    )
    response.raise_for_status()
    deployed: dict[str, Any] = response.json()
    return deployed


def deployed_definition(client: httpx.Client, tenant: str) -> dict[str, Any]:
    """`definition_id`, `deployment_id` e SHA-256 dos BYTES deployados da ultima SP-OP-AUTH-001 do tenant."""
    found = client.get(
        "/process-definition", params={"key": AUTH_PROCESS, "tenantIdIn": tenant, "latestVersion": "true"}
    ).json()
    if len(found) != 1:
        raise RuntimeError(f"{AUTH_PROCESS} deployada {len(found)} vezes no tenant {tenant}")
    definition = found[0]
    resources = client.get(f"/deployment/{definition['deploymentId']}/resources").json()
    resource = [r for r in resources if r["name"] == definition["resource"]]
    data = client.get(f"/deployment/{definition['deploymentId']}/resources/{resource[0]['id']}/data")
    data.raise_for_status()
    return dict(
        id=definition["id"],
        deployment=definition["deploymentId"],
        digest=hashlib.sha256(data.content).hexdigest(),
    )


def qualify_definition(qualification: dict[str, Any], definition: dict[str, Any]) -> dict[str, Any]:
    """A qualificacao com a definicao DEPLOYADA (o que `AuthRuntime.Invocation.definition` confere)."""
    updated = dict(qualification, definition=dict(qualification["definition"]))
    updated["definition"].update(
        definition_id=definition["id"],
        deployment_id=definition["deployment"],
        definition_digest=definition["digest"],
    )
    return updated


def synthetic_qualification(
    definition: dict[str, Any], *, native_code_digest: str, label: str, valid_until: datetime
) -> dict[str, Any]:
    """Qualificacao SINTETICA completa (sem produtor no repo): refs `SYN-`, digests de rotulo."""
    tag = f"SYN {label}".encode()
    return dict(
        schema="human-auth-installation-qualification.v1",
        definition=dict(
            process_key=AUTH_PROCESS,
            definition_id=definition["id"],
            definition_digest=definition["digest"],
            deployment_id=definition["deployment"],
            input_profile="portal-auth-intake.v1",
            profile_digest=hashlib.sha256(tag + b" profile").hexdigest(),
        ),
        native_code_digest=native_code_digest,
        source_freeze_contract_digest=hashlib.sha256(tag + b" freeze").hexdigest(),
        cutover_ref=f"SYN-{label}-cutover",
        review_receipt_ref=f"SYN-{label}-review",
        runtime_qualification_ref=f"SYN-{label}-runtime",
        valid_until=iso(valid_until),
    )


async def requalify(
    owner: Any, native_schema: str, tenant: str, definition: dict[str, Any]
) -> dict[str, Any]:
    """UPDATE da `qualification_` (mesma revisao) com a definicao deployada; devolve a `definition`."""
    async with owner.transaction():
        await owner.execute(f"SET LOCAL search_path = {native_schema}")
        raw = await owner.fetchval(
            "SELECT qualification_ FROM mzo_auth_installation WHERE tenant_=$1 FOR UPDATE", tenant
        )
        if raw is None:
            raise RuntimeError(f"mzo_auth_installation sem linha para {tenant}")
        qualification = qualify_definition(json.loads(raw), definition)
        await owner.execute(
            "UPDATE mzo_auth_installation SET qualification_=$2 WHERE tenant_=$1",
            tenant,
            canonicalize(qualification).decode(),
        )
        definition_: dict[str, Any] = qualification["definition"]
        return definition_


# --- escalacao ---------------------------------------------------------------------------------


def escalation_variables(
    tenant: str, motivo: str, severidade: str, *, agent: str, conversation: str, canal: str
) -> dict[str, Any]:
    return {
        name: {"value": value, "type": "String"}
        for name, value in dict(
            tenant_id=tenant,
            motivo_categoria=motivo,
            severidade=severidade,
            source_agent_id=agent,
            conversation_id=conversation,
            canal=canal,
        ).items()
    }


def complete_external(client: httpx.Client, topic: str, business_key: str, worker: str) -> int:
    # So as tarefas DESTA escalacao: a instancia AUTH nativa usa os mesmos topicos.
    locked = client.post(
        "/external-task/fetchAndLock",
        json={
            "workerId": worker,
            "maxTasks": 5,
            "topics": [{"topicName": topic, "lockDuration": 60000, "businessKey": business_key}],
        },
    ).json()
    for task in locked:
        client.post(f"/external-task/{task['id']}/complete", json={"workerId": worker}).raise_for_status()
    return len(locked)


@dataclass
class EscalationOutcome:
    key: str
    created: bool
    tasks: int
    groups: list[str]
    auth_instances: int


def open_escalation(
    client: httpx.Client,
    tenant: str,
    guide_number: str,
    *,
    motivo: str,
    severidade: str,
    agent: str,
    conversation: str,
    canal: str,
    worker: str,
) -> EscalationOutcome:
    """Abre (se ainda nao existe) `ESC-{tenant}-sla-auth-{guia}` e conclui as tarefas externas dela."""
    key = escalation_key(tenant, guide_number)
    existing = client.get("/process-instance", params={"businessKey": key, "tenantIdIn": tenant}).json()
    if not existing:
        variables = escalation_variables(
            tenant, motivo, severidade, agent=agent, conversation=conversation, canal=canal
        )
        client.post(
            f"/process-definition/key/{ESCALATION_PROCESS}/tenant-id/{tenant}/start",
            json={"businessKey": key, "variables": variables},
        ).raise_for_status()
    for topic in ESCALATION_TOPICS:
        complete_external(client, topic, key, worker)
    tasks = client.get(
        "/task", params={"processInstanceBusinessKey": key, "taskDefinitionKey": HUMAN_TASK}
    ).json()
    groups: list[str] = []
    for task in tasks:
        links = client.get(f"/task/{task['id']}/identity-links", params={"type": "candidate"}).json()
        groups += [link["groupId"] for link in links if link.get("groupId")]
    found = client.get(
        "/process-instance",
        params={"businessKey": auth_instance_key(tenant, guide_number), "tenantIdIn": tenant},
    ).json()
    return EscalationOutcome(
        key=key, created=not existing, tasks=len(tasks), groups=groups, auth_instances=len(found)
    )


def auth_instance_exists(client: httpx.Client, tenant: str, guide_number: str) -> bool:
    found = client.get(
        "/process-instance",
        params={"businessKey": auth_instance_key(tenant, guide_number), "tenantIdIn": tenant},
    ).json()
    return bool(found)


def escalation_state(client: httpx.Client, tenant: str, guide_number: str) -> str:
    """`absent` (nunca aberta), `live` (instancia viva COM tarefa humana), `supervisor` (viva, SLA de
    resolucao vencido: parada em `UT_SupervisorAssume`), `pending` (viva, tarefa ainda nao criada: as
    externas completam) ou `closed` (so no historico: SLA e tarefa encerrados)."""
    key = escalation_key(tenant, guide_number)
    running = client.get("/process-instance", params={"businessKey": key, "tenantIdIn": tenant}).json()
    if running:
        tasks = client.get(
            "/task", params={"processInstanceBusinessKey": key, "taskDefinitionKey": HUMAN_TASK}
        ).json()
        if tasks:
            return "live"
        supervisor = client.get(
            "/task", params={"processInstanceBusinessKey": key, "taskDefinitionKey": "UT_SupervisorAssume"}
        ).json()
        return "supervisor" if supervisor else "pending"
    history = client.get(
        "/history/process-instance", params={"processInstanceBusinessKey": key, "tenantIdIn": tenant}
    ).json()
    return "closed" if history else "absent"


#: `numero_guia_tiss` tem no maximo 20 caracteres (GuideIdentity): `SYN-R` + AAMMDDhhmmss = 17,
#: `N<1..99>` = ate 20. Medido no dev em 26/09: `SYN-DEVGUIA2R20260926...` (27) quebrava a validacao.
GUIDE_MAX_LENGTH = 20


def fresh_guide(base: str, now: datetime, taken: Callable[[str], bool]) -> str:
    """Guia `SYN-` NOVA com sufixo unico por execucao (UTC ate o segundo + contador), <= 20 chars.

    `base` so passa pelas cercas (tem de ser uma guia SYN- valida); o numero novo nao a repete,
    porque base + sufixo estoura o limite do `numero_guia_tiss`."""
    if not GUIDE_PATTERN.fullmatch(base):
        raise SyntheticRefusedError(f"guia base fora de {GUIDE_PATTERN.pattern}: {base!r}")
    stem = "SYN-R" + now.strftime("%y%m%d%H%M%S")
    for counter in range(100):
        candidate = stem if counter == 0 else f"{stem}N{counter}"
        if not GUIDE_PATTERN.fullmatch(candidate) or len(candidate) > GUIDE_MAX_LENGTH:
            raise SyntheticRefusedError(f"guia derivada fora de {GUIDE_PATTERN.pattern}: {candidate!r}")
        if not taken(candidate):
            return candidate
    raise SyntheticRefusedError("sem sufixo livre para a guia derivada")


SUPERVISOR_TASK = "UT_SupervisorAssume"
SLA_RESOLUTION_TIMER = "BT_SlaResolucao"


def breach_resolution_sla(client: httpx.Client, tenant: str, guide_number: str, *, worker: str) -> int:
    """Antecipa o SLA de resolucao da escalacao (executa o job do timer `BT_SlaResolucao`, sem mudar
    o BPMN nem a DMN) e conclui a publicacao do breach: a escalacao cai em `UT_SupervisorAssume`.
    Devolve quantas tarefas do supervisor a escalacao tem depois."""
    key = escalation_key(tenant, guide_number)
    instances = client.get("/process-instance", params={"businessKey": key, "tenantIdIn": tenant}).json()
    if len(instances) != 1:
        raise RuntimeError(f"escalacao {key}: {len(instances)} instancias vivas")
    jobs = client.get(
        "/job", params={"processInstanceId": instances[0]["id"], "activityId": SLA_RESOLUTION_TIMER}
    ).json()
    for job in jobs:
        client.post(f"/job/{job['id']}/execute").raise_for_status()
    for topic in ESCALATION_TOPICS:
        complete_external(client, topic, key, worker)
    tasks = client.get(
        "/task", params={"processInstanceBusinessKey": key, "taskDefinitionKey": SUPERVISOR_TASK}
    ).json()
    return len(tasks)
