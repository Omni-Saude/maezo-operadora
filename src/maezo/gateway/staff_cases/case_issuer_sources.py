"""Adaptadores de LEITURA da T1.6: quem sao os grantees e quais escalacoes estao vivas.

* `PostgresStaffGranteeSource` le a membership revisada (`portal_memberships`, migracao 0012) do
  tenant, num schema nomeado e quotado (licao do ADR-0060: nunca depender do `search_path`), em
  transacao read-only com `statement_timeout`. Linha cujas colunas divergem do payload e recusada
  inteira: uma fonte incoerente nao vira grant.
* `EngineEscalationSource` le do engine as escalacoes VIVAS: cada `UT_TratarEscalonamento` ativa
  de SP-OP-ESCALATION-001. O grupo e a SAIDA `grupo_atendimento` que a DMN `escalation_routing`
  registrou no historico da instancia (imutavel), conferida contra o grupo candidato da tarefa
  (`camunda:candidateGroups="${roteamento.grupo_atendimento}"`); o tenant e o `tenantId` nativo.
  O grupo e lido, nunca calculado, e qualquer divergencia recusa a escalacao.

Portas de producao (decisao D-H do plano `portal-autoridade-nativa-dev.md`):

* `AuthClaimAnchor` (D-H.1): so a escalacao `ESC-{tenant}-sla-auth-{guia}` (ADR-0051) e ancorada.
  Dela sai `AUTH-{tenant}-{guia}`; o engine devolve EXATAMENTE uma instancia SP-OP-AUTH-001 do
  tenant; a reivindicacao humana (`mzo_auth_guide_claim`, so `tenant_`, `instance_`, `case_`) tem
  de nomear essa instancia. Qualquer outra forma e `unanchored` (sem grant). Erro de infraestrutura
  NAO vira `unanchored`: a rodada falha, senao uma queda de banco revogaria todos os grants.
* `IssuerWitness` (D-H.2): witness de membership pela entrada `identity_verifier` PROPRIA do emissor
  (`entry_ref=case-issuer-witness`), com `session_ref = case-issuer-run:{run_id}`.
* `PostgresIssuerLedger` (D-H.3): `maezo_native.mzo_staff_case_issuer_ledger`, uma linha por
  (escopo, `policy_ref`), escrita CAS por `revision`, sem DELETE.
"""

from __future__ import annotations

import hashlib
import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.gateway.external_cases.models import Identity, Scope, now_utc
from maezo.portal.api.records import MembershipRecord
from maezo.tools.process_business_keys import auth_business_key

from .authority import fingerprint
from .case_issuer import (
    MAX_CASE_TASKS,
    ROUTING_DECISION,
    ROUTING_OUTPUT,
    CaseIssuerError,
    CaseTask,
    IssuerState,
    RoutedEscalation,
    StaffGrantee,
    apply,
    decode_state,
    encode_state,
)
from .models import MembershipWitness, StaffPublication
from .production_config import is_native_schema
from .publisher import StaffWitnessSource

SCHEMA = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
PROCESS_KEY = "SP-OP-ESCALATION-001"
TASK_KEY = "UT_TratarEscalonamento"


class PostgresStaffGranteeSource:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        tenant: str,
        schema: str,
        seconds: int = 5,
        clock: Callable[[], datetime] = now_utc,
    ):
        if not SCHEMA.fullmatch(schema) or not 1 <= seconds <= 30:
            raise CaseIssuerError("invalid_grantee_source")
        self.engine, self.tenant, self.schema, self.seconds, self.clock = (
            engine,
            tenant,
            schema,
            seconds,
            clock,
        )

    async def __call__(self) -> tuple[StaffGrantee, ...]:
        now = self.clock()
        async with self.engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text(f"SET LOCAL statement_timeout = '{int(self.seconds)}s'"))
            rows = (
                await connection.execute(
                    text(
                        # So as colunas que `amh-native-source-grants.sql` concede ao login
                        # `maezo_native_case_issuer` (sem `principal_ref`: vem do payload).
                        "SELECT tenant, issuer, subject, payload "
                        f'FROM "{self.schema}".portal_memberships '
                        "WHERE tenant=:tenant ORDER BY issuer, subject"
                    ),
                    {"tenant": self.tenant},
                )
            ).all()
            await connection.rollback()
        grantees: list[StaffGrantee] = []
        for row in rows:
            record = MembershipRecord.model_validate_json(row.payload)
            if (row.tenant, row.issuer, row.subject) != (record.tenant, record.issuer, record.subject):
                raise CaseIssuerError("membership_row_mismatch")
            grantee = StaffGrantee.from_membership(record, tenant=self.tenant, now=now)
            if grantee is not None:
                grantees.append(grantee)
        return tuple(grantees)


@dataclass(frozen=True)
class LiveEscalation:
    tenant: str
    escalation_ref: str
    business_key: str
    task_id: str
    grupo_atendimento: str


CaseAnchor = Callable[[LiveEscalation], Identity | None | Awaitable[Identity | None]]


@dataclass
class EscalationReport:
    live: int = 0
    anchored: int = 0
    unanchored: int = 0
    refused: int = 0
    foreign: int = 0
    #: Motivo de cada `unanchored` (so contadores; nunca guia, tenant alheio ou PHI).
    reasons: dict[str, int] = field(default_factory=dict)


class EngineEscalationSource:
    """Escalacoes vivas lidas do engine REST, com o grupo que a DMN escolheu."""

    def __init__(self, client: httpx.AsyncClient, *, tenant: str, anchor: CaseAnchor, page: int = 100):
        if not 1 <= page <= 500:
            raise CaseIssuerError("invalid_escalation_source")
        self.client, self.tenant, self.anchor, self.page = client, tenant, anchor, page
        self.report = EscalationReport()

    async def _json(self, path: str, **params: Any) -> Any:
        response = await self.client.get(path, params=params or None)
        response.raise_for_status()
        return response.json()

    async def _qualify(self, task: dict[str, Any], report: EscalationReport) -> LiveEscalation | None:
        """Uma tarefa so vira escalacao quando TRES fontes concordam; qualquer divergencia recusa.

        O candidate group e a variavel `tenant_id` sao mutaveis pelo `engine-rest` (identity-links e
        variaveis). Por isso eles so CONFIRMAM: o grupo tem de ser igual a SAIDA registrada da DMN
        no historico (`/history/decision-instance`, imutavel), e o tenant e o `tenantId` nativo da
        instancia (fixado no deploy/start), que a variavel tem de repetir.
        """
        instance = str(task["processInstanceId"])
        process = await self._json(f"/process-instance/{instance}")
        if process.get("tenantId") != self.tenant or task.get("tenantId") != self.tenant:
            # Instancia sem tenant nativo (deploy sem `tenant-id`) tambem cai aqui: sem ancora
            # imutavel de tenant nao ha grant.
            report.foreign += 1
            return None
        variable = await self._json(
            f"/process-instance/{instance}/variables/tenant_id", deserializeValue="false"
        )
        if variable.get("type") != "String" or variable.get("value") != self.tenant:
            report.refused += 1
            return None
        decisions = await self._json(
            "/history/decision-instance",
            decisionDefinitionKey=ROUTING_DECISION,
            processInstanceId=instance,
            includeOutputs="true",
            disableBinaryFetching="true",
        )
        outputs = [
            output
            for decision in decisions
            for output in decision.get("outputs") or []
            if output.get("variableName") == ROUTING_OUTPUT
        ]
        if (
            len(decisions) != 1
            or decisions[0].get("tenantId") != self.tenant
            or len(outputs) != 1
            or not isinstance(outputs[0].get("value"), str)
        ):
            report.refused += 1
            return None
        routed = outputs[0]["value"]
        links = await self._json(f"/task/{task['id']}/identity-links", type="candidate")
        groups = sorted({str(link["groupId"]) for link in links if link.get("groupId")})
        # `candidateGroups="${roteamento.grupo_atendimento}"` resolve para UM grupo, o da DMN.
        # Zero, varios, ou um diferente do historico: identity-link adulterado ou forma inesperada.
        if groups != [routed]:
            report.refused += 1
            return None
        return LiveEscalation(
            tenant=self.tenant,
            escalation_ref=instance,
            business_key=str(process.get("businessKey") or ""),
            task_id=str(task["id"]),
            grupo_atendimento=routed,
        )

    async def live(self) -> tuple[LiveEscalation, ...]:
        report = EscalationReport()
        found: list[LiveEscalation] = []
        first = 0
        while True:
            tasks = await self._json(
                "/task",
                processDefinitionKey=PROCESS_KEY,
                taskDefinitionKey=TASK_KEY,
                active="true",
                sortBy="id",
                sortOrder="asc",
                firstResult=str(first),
                maxResults=str(self.page),
            )
            for task in tasks:
                escalation = await self._qualify(task, report)
                if escalation is not None:
                    found.append(escalation)
            if len(tasks) < self.page:
                break
            first += self.page
        report.live = len(found)
        self.report = report
        return tuple(found)

    async def case_tasks(self, case: Identity) -> tuple[CaseTask, ...]:
        """H3: as tarefas vivas da instancia do CASO, como `StaffCaseStore.taskRows` as varre.

        Mesma instancia (`process_instance_ref`), mesmo tenant nativo, ordenadas por id. Uma
        tarefa de outro tenant ou de outra instancia na resposta e defeito da fonte: recusa.
        Mais que `MAX_CASE_TASKS` tambem recusa (o grant nao cabe; nunca uma lista cortada).
        """
        tasks = await self._json(
            "/task",
            processInstanceId=case.process_instance_ref,
            tenantIdIn=self.tenant,
            sortBy="id",
            sortOrder="asc",
            firstResult="0",
            maxResults=str(MAX_CASE_TASKS + 1),
        )
        if not isinstance(tasks, list) or len(tasks) > MAX_CASE_TASKS:
            raise CaseIssuerError("case_tasks_unavailable")
        found = []
        for task in tasks:
            if (
                not isinstance(task, dict)
                or task.get("tenantId") != self.tenant
                or task.get("processInstanceId") != case.process_instance_ref
                or task.get("processDefinitionId") != case.process_definition_id
            ):
                raise CaseIssuerError("case_tasks_unavailable")
            found.append(CaseTask(str(task["id"]), str(task["taskDefinitionKey"])))
        return tuple(sorted(found))

    async def __call__(self) -> tuple[RoutedEscalation, ...]:
        routed: list[RoutedEscalation] = []
        live = await self.live()
        for escalation in live:
            value = self.anchor(escalation)
            if inspect.isawaitable(value):
                value = await value
            if value is None:
                self.report.unanchored += 1
                reason = getattr(self.anchor, "last_reason", None) or "unanchored"
                self.report.reasons[reason] = self.report.reasons.get(reason, 0) + 1
                continue
            routed.append(
                RoutedEscalation(
                    tenant=escalation.tenant,
                    escalation_ref=escalation.escalation_ref,
                    case=value,
                    grupo_atendimento=escalation.grupo_atendimento,
                    case_tasks=await self.case_tasks(value),
                )
            )
        self.report.anchored = len(routed)
        # Revisao de seguranca do #500: um engine que responde 200 com `[]` para a AUTH faria o plano
        # revogar tudo. Maioria zerada (com pelo menos 3 vivas) e falha da rodada, nunca revogacao.
        zero = self.report.reasons.get("auth_instances_zero", 0)
        if len(live) >= 3 and 2 * zero > len(live):
            raise CaseIssuerError("anchor_zero_quorum")
        return tuple(routed)


# ---------------------------------------------------------------------------- D-H.1: ancora

AUTH_PROCESS_KEY = "SP-OP-AUTH-001"
#: A guia e opaca (nunca interpretada): so se limita o alfabeto, para caber em business key/REST.
GUIDE = re.compile(r"[A-Za-z0-9._-]{1,128}")


class AuthClaimAnchor:
    """Escalacao de SLA da AUTH -> a identidade do caso staff que o engine reconhece.

    Le `mzo_auth_guide_claim` pelo login `maezo_native_case_issuer` (SELECT de coluna so em
    `tenant_`, `instance_`, `case_`) e o engine REST (instancia, definicao e bytes deployados). A
    identidade e montada como `NativeCaseIdentityReader.fromClaim`; o engine a revalida no grant
    (`identity_digest`), entao um erro aqui recusa, nao vaza.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        engine: AsyncEngine,
        *,
        tenant: str,
        native_schema: str,
        seconds: int = 5,
    ):
        if not tenant or not is_native_schema(native_schema) or not 1 <= seconds <= 30:
            raise CaseIssuerError("invalid_anchor")
        self.client, self.engine, self.tenant = client, engine, tenant
        self.native_schema, self.seconds = native_schema, seconds
        self.prefix = f"ESC-{tenant}-sla-auth-"
        self.last_reason: str | None = None

    async def _json(self, path: str, **params: Any) -> Any:
        response = await self.client.get(path, params=params or None)
        response.raise_for_status()
        return response.json()

    def _none(self, reason: str) -> Identity | None:
        self.last_reason = reason
        return None

    async def _claims(self, instance: str) -> list[Any]:
        async with self.engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text(f"SET LOCAL statement_timeout = '{int(self.seconds)}s'"))
            rows = (
                await connection.execute(
                    text(
                        "SELECT tenant_, instance_, case_ "
                        f'FROM "{self.native_schema}".mzo_auth_guide_claim '
                        "WHERE tenant_=:tenant AND instance_=:instance"
                    ),
                    {"tenant": self.tenant, "instance": instance},
                )
            ).all()
            await connection.rollback()
        return list(rows)

    async def _definition_digest(self, definition: dict[str, Any]) -> str:
        deployment, resource = str(definition["deploymentId"]), str(definition["resource"])
        resources = await self._json(f"/deployment/{deployment}/resources")
        found = [r for r in resources if r.get("name") == resource]
        if len(found) != 1:
            raise CaseIssuerError("definition_resource_unavailable")
        response = await self.client.get(f"/deployment/{deployment}/resources/{found[0]['id']}/data")
        response.raise_for_status()
        # O mesmo que `EngineSchema.identitySql`: sha256 dos BYTES deployados.
        return hashlib.sha256(response.content).hexdigest()

    async def __call__(self, escalation: LiveEscalation) -> Identity | None:
        self.last_reason = None
        if escalation.tenant != self.tenant:
            return self._none("foreign_tenant")
        key = escalation.business_key
        if not key.startswith(self.prefix):
            return self._none("not_sla_auth")
        guide = key[len(self.prefix) :]
        if not GUIDE.fullmatch(guide):
            return self._none("invalid_guide")
        auth_key = auth_business_key(tenant_id=self.tenant, numero_guia_tiss=guide)
        instances = await self._json(
            "/process-instance",
            businessKey=auth_key,
            processDefinitionKey=AUTH_PROCESS_KEY,
            tenantIdIn=self.tenant,
        )
        if len(instances) != 1:
            return self._none("auth_instances_" + ("zero" if not instances else "many"))
        instance = instances[0]
        if instance.get("tenantId") != self.tenant or instance.get("businessKey") != auth_key:
            return self._none("auth_instance_mismatch")
        instance_id = str(instance["id"])
        claims = await self._claims(instance_id)
        if len(claims) != 1:
            return self._none("claim_absent" if not claims else "claim_ambiguous")
        claim = claims[0]
        if claim.tenant_ != self.tenant or claim.instance_ != instance_id or not claim.case_:
            return self._none("claim_mismatch")
        definition = await self._json(f"/process-definition/{instance['definitionId']}")
        if (
            definition.get("key") != AUTH_PROCESS_KEY
            or definition.get("tenantId") != self.tenant
            or definition.get("id") != instance["definitionId"]
        ):
            return self._none("definition_mismatch")
        try:
            return Identity(
                upstream_resource_key=guide,
                case_ref=str(claim.case_),
                process_instance_ref=instance_id,
                process_definition_id=str(definition["id"]),
                process_definition_key=AUTH_PROCESS_KEY,
                process_definition_version=str(int(definition["version"])),
                process_definition_digest=await self._definition_digest(definition),
                kind="authorization",
            )
        except CaseIssuerError:
            raise
        except (TypeError, ValueError):
            return self._none("identity_invalid")


# ---------------------------------------------------------------------------- D-H.2: witness

WITNESS_ENTRY = "case-issuer-witness"


class IssuerWitness:
    """Witness de membership do emissor, sem sessao humana, pela entrada `identity_verifier` dele."""

    def __init__(
        self,
        source: StaffWitnessSource,
        *,
        run_id: str,
        ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = now_utc,
    ):
        entry = source.signer.authority.entries.get(fingerprint(source.signer.key.public_key()))
        if (
            entry is None
            or entry.entry_ref != WITNESS_ENTRY
            or entry.role != "identity_verifier"
            or tuple(entry.purposes) != ("membership_current",)
            or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", run_id)
            or not timedelta(0) < ttl <= timedelta(minutes=15)
        ):
            raise CaseIssuerError("not_the_issuer_witness")
        self.source, self.session_ref, self.ttl, self.clock = source, f"case-issuer-run:{run_id}", ttl, clock

    async def __call__(self, grantee: StaffGrantee) -> MembershipWitness:
        now = self.clock()
        principal = grantee.principal(session_ref=self.session_ref, authenticated_at=now)
        return await self.source.observe(principal, now + self.ttl)


# ---------------------------------------------------------------------------- D-H.3: ledger


class PostgresIssuerLedger:
    """Ledger duravel: uma linha por (escopo, `policy_ref`), CAS por `revision`, sem DELETE."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        scope: Scope,
        policy_ref: str,
        login: str = "maezo_native_case_issuer",
        native_schema: str = "maezo_native",
        seconds: int = 5,
    ):
        if (
            not is_native_schema(native_schema)
            or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", login)
            or not 1 <= len(policy_ref.encode()) <= 255
            or not 1 <= seconds <= 30
        ):
            raise CaseIssuerError("invalid_ledger")
        self.engine, self.scope, self.policy_ref, self.login = engine, scope, policy_ref, login
        self.seconds = seconds
        self.table = f'"{native_schema}".mzo_staff_case_issuer_ledger'
        self.key = dict(
            tenant=scope.tenant,
            environment=scope.environment,
            engine_name=scope.engine_name,
            database_incarnation=scope.database_incarnation,
            policy_ref=policy_ref,
        )
        self.where = (
            "tenant=:tenant AND environment=:environment AND engine_name=:engine_name "
            "AND database_incarnation=:database_incarnation AND policy_ref=:policy_ref"
        )
        self._revision: int | None = None
        self._state: IssuerState | None = None

    async def _begin(self, connection: Any) -> None:
        await connection.execute(text(f"SET LOCAL statement_timeout = '{int(self.seconds)}s'"))
        user = (await connection.execute(text("SELECT session_user::text, current_user::text"))).one()
        if tuple(user) != (self.login, self.login):
            raise CaseIssuerError("ledger_login")

    async def load(self) -> IssuerState:
        async with self.engine.begin() as connection:
            await self._begin(connection)
            exists = (
                await connection.execute(text(f"SELECT 1 FROM {self.table} WHERE {self.where}"), self.key)
            ).first()
            if exists is None:
                await connection.execute(
                    text(
                        f"INSERT INTO {self.table} (tenant, environment, engine_name, database_incarnation,"
                        " policy_ref, revision, issued_state, pending_request) VALUES (:tenant, :environment,"
                        " :engine_name, :database_incarnation, :policy_ref, 0, :state, NULL)"
                        " ON CONFLICT DO NOTHING"
                    ),
                    dict(self.key, state=encode_state(await self._seed(connection))),
                )
            row = (
                await connection.execute(
                    text(
                        f"SELECT revision, issued_state, pending_request FROM {self.table} WHERE {self.where}"
                    ),
                    self.key,
                )
            ).one()
        self._revision = int(row.revision)
        pending = None if row.pending_request is None else bytes(row.pending_request)
        self._state = decode_state(bytes(row.issued_state), pending)
        return self._state

    async def _seed(self, connection: Any) -> IssuerState:
        """Estado inicial da linha de uma politica NOVA (rotacao da designacao: `@d{N}` -> `@d{N+1}`).

        No engine as cadeias sao por fonte (`source_ref`), por `grant_ref` e por checkpoint, nao por
        politica: recomecar do zero repetia `source@1` / `grant_revision=1` com outro pedido
        (`READ_REVISION_CONFLICT`). A politica nova herda do ledger mais adiantado do escopo a
        revisao da fonte, os grants e os checkpoints; a POLITICA nao passa (`policy=None`), entao o
        plano publica o `policy_head` novo e reemite cada grant/checkpoint sob ele (intent com o
        digest da politica nova -> revisao seguinte da mesma cadeia). Publicacao pendente em outra
        politica = recusa, ate ela ser recuperada pela rodada da propria politica.
        """
        scope_where = (
            "tenant=:tenant AND environment=:environment AND engine_name=:engine_name "
            "AND database_incarnation=:database_incarnation AND policy_ref<>:policy_ref"
        )
        rows = (
            await connection.execute(
                text(
                    f"SELECT issued_state, pending_request FROM {self.table} WHERE {scope_where} FOR UPDATE"
                ),
                self.key,
            )
        ).all()
        latest = IssuerState.empty()
        for row in rows:
            if row.pending_request is not None:
                raise CaseIssuerError("pending_publication_other_policy")
            state = decode_state(bytes(row.issued_state), None)
            if state.source_revision > latest.source_revision:
                latest = state
        return replace(latest, policy=None, pending=None)

    async def _write(self, state: IssuerState) -> IssuerState:
        if self._revision is None or self._state is None:
            raise CaseIssuerError("ledger_not_loaded")
        async with self.engine.begin() as connection:
            await self._begin(connection)
            result = await connection.execute(
                text(
                    f"UPDATE {self.table} SET revision=revision+1, issued_state=:state,"
                    f" pending_request=:pending, updated_at=now() WHERE {self.where} AND revision=:old"
                ),
                dict(self.key, state=encode_state(state), pending=state.pending, old=self._revision),
            )
            if result.rowcount != 1:
                # Outro emissor (ou uma rodada concorrente) escreveu: nada e sobrescrito.
                raise CaseIssuerError("ledger_conflict")
        self._revision += 1
        self._state = state
        return state

    async def begin(self, raw: bytes) -> None:
        state = self._state if self._state is not None else await self.load()
        if state.pending is not None and state.pending != raw:
            raise CaseIssuerError("pending_publication")
        await self._write(replace(state, pending=raw))

    async def commit(self, publication: StaffPublication) -> IssuerState:
        state = self._state if self._state is not None else await self.load()
        return await self._write(replace(apply(state, publication), pending=None))
