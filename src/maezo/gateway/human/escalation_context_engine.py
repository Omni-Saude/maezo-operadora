"""Concrete INTERIM escalation-context adapter (DL-0050): the engine REST reads, made explicit.

This is the shortcut named plainly, not a new architecture. `EngineRestEscalationContext` issues a
fixed set of `GET`s against the engine's REST surface — the SAME origin, transport posture and
private-name rule `EngineRestTaskCompletion` already uses for DL-0049 — and assembles the handful
of facts the attendant needs. It is NOT the signed, digest-bound read model: nothing it returns is
attested, and the engine's `engine-rest` has no authentication in this distribution, so the network
is the only boundary this call has. That missing attestation is the debt DL-0050 registers.

What it reads, and nothing else:

* `GET /task/{id}` — proves the task is an escalation attendant task of THIS tenant and yields
  the process instance and the task creation time (the start of the two SLA clocks).
* `GET /history/process-instance/{id}` — proves the instance is `SP-OP-ESCALATION-001` of this
  tenant and yields its start time.
* `GET /history/variable-instance` — exactly three process variables, one request each, by fixed
  name: `motivo_categoria`, `severidade` and `resumo_contexto`. There is NO request for "all the
  variables of this instance": a process that carries clinical fields must never have them
  fetched into this service just to be filtered.
* `GET /history/decision-instance` — the outputs of the `escalation_routing` evaluation.

Nothing the browser sends chooses a URL, a variable name or a decision key. The two ids that ARE
interpolated are the task id (already an authorized `OpaqueRef`) and the process instance id the
engine itself returned; both must match a strict segment shape or the call is refused before any
request leaves. The engine's response text is never read into an error.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from .completion_engine import MAX_RESPONSE, _fixed_engine_origin
from .errors import GatewayRefusalError
from .escalation_context import (
    ESCALATION_PROCESS_KEY,
    ROUTING_DECISION_KEY,
    SUMMARY_VARIABLE,
    TASK_STAGES,
    EscalationContext,
    EscalationContextSource,
    clean_group,
    clean_priority,
    clean_summary,
    clean_token,
    parse_engine_datetime,
    parse_iso_duration,
)
from .models import Scope

#: Engine ids are UUIDs. The shape is deliberately broader than a UUID but excludes every
#: character that could turn one path segment into two, a query or a fragment.
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_MOTIVO_VARIABLE = "motivo_categoria"
_SEVERIDADE_VARIABLE = "severidade"


def _segment(value: object) -> str:
    if not isinstance(value, str) or _SEGMENT.fullmatch(value) is None:
        raise GatewayRefusalError("task_unavailable")
    return value


class EngineRestEscalationContext(EscalationContextSource):
    def __init__(
        self,
        *,
        scope: Scope,
        origin: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise GatewayRefusalError("production_capabilities_unavailable")
        self.scope = Scope.model_validate(scope)
        self._origin = _fixed_engine_origin(origin).rstrip("/")
        # Registered byte-exactly in `scripts/ci/check_effect_chokepoint_fence.py`
        # `_HTTPX_SCOPED_SEAMS`, with the same controls as `EngineRestTaskCompletion`: system
        # trust store, no proxy env, no redirect that could forward a read elsewhere.
        self._http = httpx.AsyncClient(
            verify=True,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=timeout_seconds,
        )
        self._closed = False

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        """One bounded GET. `404` is a proven absence; every other non-200 is UNCERTAIN."""
        try:
            async with self._http.stream(
                "GET",
                f"{self._origin}{path}",
                params=params,
                headers={"Accept": "application/json"},
            ) as response:
                status = response.status_code
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE:
                        raise GatewayRefusalError("admission_unavailable")
        except GatewayRefusalError:
            raise
        except Exception:
            raise GatewayRefusalError("admission_unavailable") from None
        if status == 404:
            raise GatewayRefusalError("task_unavailable")
        if status != 200:
            raise GatewayRefusalError("admission_unavailable")
        try:
            return json.loads(bytes(body))
        except ValueError:
            raise GatewayRefusalError("admission_unavailable") from None

    def _same_tenant(self, payload: Any) -> None:
        if not isinstance(payload, dict) or payload.get("tenantId") != self.scope.tenant:
            raise GatewayRefusalError("task_unavailable")

    async def _variable(self, process_instance_id: str, name: str) -> str | None:
        rows = await self._get(
            "/history/variable-instance",
            {
                "processInstanceId": process_instance_id,
                "variableName": name,
                "deserializeValues": "false",
            },
        )
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0]
        if not isinstance(row, dict) or row.get("name") != name or row.get("type") != "String":
            return None
        value = row.get("value")
        return value if isinstance(value, str) else None

    async def _routing(self, process_instance_id: str) -> dict[str, Any]:
        rows = await self._get(
            "/history/decision-instance",
            {
                "processInstanceId": process_instance_id,
                "decisionDefinitionKey": ROUTING_DECISION_KEY,
                "includeOutputs": "true",
                "disableBinaryFetching": "true",
                "sortBy": "evaluationTime",
                "sortOrder": "desc",
                "maxResults": "1",
            },
        )
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return {}
        outputs = rows[0].get("outputs")
        if not isinstance(outputs, list):
            return {}
        return {
            row["variableName"]: row.get("value")
            for row in outputs
            if isinstance(row, dict) and isinstance(row.get("variableName"), str)
        }

    async def read(self, *, task_id: str) -> EscalationContext:
        if self._closed:
            raise GatewayRefusalError("task_unavailable")
        task = await self._get(f"/task/{_segment(task_id)}")
        self._same_tenant(task)
        key = task.get("taskDefinitionKey")
        stage = TASK_STAGES.get(key) if isinstance(key, str) else None
        if stage is None:
            raise GatewayRefusalError("task_unavailable")
        process_instance_id = _segment(task.get("processInstanceId"))
        created = parse_engine_datetime(task.get("created"))

        process = await self._get(f"/history/process-instance/{process_instance_id}")
        self._same_tenant(process)
        if process.get("processDefinitionKey") != ESCALATION_PROCESS_KEY:
            raise GatewayRefusalError("task_unavailable")

        motivo, severidade, resumo, routing = await asyncio.gather(
            self._variable(process_instance_id, _MOTIVO_VARIABLE),
            self._variable(process_instance_id, _SEVERIDADE_VARIABLE),
            self._variable(process_instance_id, SUMMARY_VARIABLE),
            self._routing(process_instance_id),
        )

        # The two SLA clocks are boundary timers on `UT_TratarEscalonamento`, so they start when
        # THAT task starts. The supervisor task is created later and carries no clock: a deadline
        # computed from its creation time would be a number nobody agreed to.
        ack_due = resolution_due = None
        if stage == "atendimento" and created is not None:
            ack = parse_iso_duration(routing.get("sla_ack"))
            resolution = parse_iso_duration(routing.get("sla_resolucao"))
            ack_due = None if ack is None else created + ack
            resolution_due = None if resolution is None else created + resolution

        return EscalationContext(
            etapa=stage,
            motivo_categoria=clean_token(motivo),
            severidade=clean_token(severidade),
            prioridade=clean_priority(routing.get("prioridade")),
            grupo_atendimento=clean_group(routing.get("grupo_atendimento")),
            aberto_em=parse_engine_datetime(process.get("startTime")),
            ack_vence_em=ack_due,
            resolucao_vence_em=resolution_due,
            resumo_contexto=clean_summary(resumo),
        )

    async def close(self) -> None:
        self._closed = True
        await self._http.aclose()
