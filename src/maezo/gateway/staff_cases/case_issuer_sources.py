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

Qual caso staff a escalacao nomeia e a porta `CaseAnchor`. Nao ha implementacao de producao: o
engine so reconhece como caso staff uma guia AUTH reivindicada pelo plano humano
(`MZO_AUTH_GUIDE_CLAIM`, `NativeCaseIdentityReader`), e nenhum artefato do repositorio liga uma
escalacao a essa reivindicacao (ver o PR). Escalacao sem ancora nao gera grant e entra na contagem
`unanchored` do relatorio.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.gateway.external_cases.models import Identity, now_utc
from maezo.portal.api.records import MembershipRecord

from .case_issuer import ROUTING_DECISION, ROUTING_OUTPUT, CaseIssuerError, RoutedEscalation, StaffGrantee

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
                        "SELECT tenant, issuer, subject, principal_ref, payload "
                        f'FROM "{self.schema}".portal_memberships '
                        "WHERE tenant=:tenant ORDER BY principal_ref"
                    ),
                    {"tenant": self.tenant},
                )
            ).all()
            await connection.rollback()
        grantees: list[StaffGrantee] = []
        for row in rows:
            record = MembershipRecord.model_validate_json(row.payload)
            if (row.tenant, row.issuer, row.subject, row.principal_ref) != (
                record.tenant,
                record.issuer,
                record.subject,
                record.principal_ref,
            ):
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

    async def __call__(self) -> tuple[RoutedEscalation, ...]:
        routed: list[RoutedEscalation] = []
        live = await self.live()
        for escalation in live:
            value = self.anchor(escalation)
            if inspect.isawaitable(value):
                value = await value
            if value is None:
                self.report.unanchored += 1
                continue
            routed.append(
                RoutedEscalation(
                    tenant=escalation.tenant,
                    escalation_ref=escalation.escalation_ref,
                    case=value,
                    grupo_atendimento=escalation.grupo_atendimento,
                )
            )
        self.report.anchored = len(routed)
        return tuple(routed)
