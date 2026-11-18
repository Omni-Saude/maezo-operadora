"""Durable inbox consumer for `escalation.notify_team` — the leg that makes a notification real.

**The gap this closes.** `SP-OP-ESCALATION-001`'s `ST_NotificarTime` publishes
`type=escalation.notify_team` onto `operadora.notifications.internal`
(`tools/workers/escalation.py::make_notify_team_handler`) and, until this module, NOTHING
consumed it. The worker never overclaimed — its docstring calls the publish an internal
observability record and refuses to report `teams_notified` for zero publishes — but the leg
still ended nowhere: the only human surface was the engine's own `UT_TratarEscalonamento`, and
the notification was a message into an empty room. WP-J1-09 (owner decision #17) put a REAL
escalation on the other end of an AUTH SLA-risk alert, which turned a cosmetic gap into a
load-bearing one.

**What counts as delivery, stated exactly.** A committed row in `escalation_team_notice`
(migration `0015_escalation_team_notice.py`) plus the receipt this module returns for it — NEVER
a committed Kafka offset. That is ADR-0037 XRD-10's rule ("offsets Kafka nunca representam
conclusao de negocio") applied to the escalation channel, and it is enforced by ORDER in
`run_inbox_loop`: the row is committed and a receipt returned BEFORE `consumer.commit()` is
reached, and any failure to record propagates so the offset stays where it was and the message is
redelivered. There is no branch in this file that advances an offset for a notice that was not
durably recorded.

**Audience scoping.** `TeamNotice.audience` is `staff` and is the only value the schema's CHECK
admits. The row is addressed to a `grupo_atendimento` — the value the `escalation_routing` DMN
chose, carried verbatim from the notification, which is the SAME expression that sets
`UT_TratarEscalonamento`'s `camunda:candidateGroups`. It is NOT expanded into people: a candidate
group is the addressing unit of this process, the engine does not expand it either, and this repo
has no group->principal authority to expand it with. Inventing one here would be a fabricated
membership decision, so the row stays group-addressed and says so.

**This module decides nothing.** It validates and records. `grupo_atendimento`, `severidade`,
`prioridade` and `motivo_categoria` are read verbatim off a notification whose publisher already
validated them against their contractual/DMN domains before publishing
(`escalation.py::_exigir_grupo_atendimento` / `_exigir_severidade` / `_rotulo_opcional_validado`).
Re-deciding any of them here would let an inbox line contradict the queue the engine actually
paged — the exact failure `GAP-ESC-SEVERITY-GROUP` closed on the publishing side.

**Why a second consumer of one topic, and not a rule in the bridge.** `NotificationBridge`'s job
is to START processes through the ADR-0007/T-C2 fenced chokepoint; a notify_team notification
starts nothing — its escalation is already running, and the notification is its human-facing
side effect. Putting it in the bridge would mean a handoff rule with no handoff. Two consumers in
DIFFERENT consumer groups read the same topic independently, which is ordinary Kafka fan-out: the
bridge's offsets and this daemon's offsets are unrelated, and neither can stall the other.

**Shared machinery, not duplicated machinery.** The Kafka seam (`BridgeKafkaConsumer`,
`BridgeMessage`, `AioKafkaBridgeConsumer`, `FakeBridgeKafkaConsumer`), the malformed-message
error type and the poison-message DLQ shunt are IMPORTED from `notifications_bridge`, not
re-implemented. The two daemons therefore share one fail-closed posture, one DLQ contract and one
set of reason codes; a fix to either applies to both.

**Honest results only.** `record` reports what it OBSERVED: `RECORDED` for a fresh row,
`DUPLICATE` for a redelivery of byte-identical notification content. There is no "assume it
worked" branch, and a duplicate is never reported as fresh — the two are distinguishable because
`notice_ref` is a digest of the notification's own content, so redelivery converges on one row
while a genuinely NEW notification about the same escalation (a re-notify after a channel
fallback, a later SLA cycle) is a different row the human still sees.

**DL-0186 — `rco.aviso_auditoria` (unidade 2b).** The same topic carries the K1 audit-signal notices the
outbox relay publishes (`platform/rco_avisos_relay.py`, DL-0185). When an `InboxAvisosRco` sink is wired
(`avisos_rco=`), each one is checked by `rco_avisos.linha_da_mensagem` (exact keys, the 0036 shapes, a
`chave_idempotencia` that matches the message's own content) and addressed to the `grupo_principal` the
PINNED K6 declares for its `fila` (`rco_pin.RcoRulesPin.grupos_principais`) — data from the pin, never a
name written here. One message = one fila = one group = one row; `notice_ref` digests
`(type, tenant, chave_idempotencia, grupo)`, so a redelivery converges on one row. A fila with no
`grupo_principal` ends as a closed-code failure (`rco_fila_sem_grupo`, dead-lettered like any deterministic
refusal), never delivered to another group; the relay already marks such a row `fila_sem_grupo` before
publishing, so this refusal is the SECOND barrier (a consumer pin that differs from the relay's). Without a
sink the type is skipped as before (counted as `nao_configurado`). The durable sink is
`PostgresInboxAvisosRco` over its own immutable table `rco_aviso_grupo` (migration 0037; NOT
`escalation_team_notice`, which has no column for the notice kind, `sinal`, `fila` or `prazo_parametro`),
wired by `main()` with the same DSN and tenant as the escalation inbox. Counted by
`maezo_rco_aviso_consumo_total{desfecho}`.

**HONEST EXTERNAL-VALIDATION BOUNDARY** (same convention as `notifications_bridge`'s own
docstring). Unit-provable here: parsing, fail-closed refusal, the receipt/ordering contract, the
loop's dispatch/commit behaviour against the fake consumer, and the SQL this repository issues.
NOT provable in this sandbox, and therefore not claimed: `AioKafkaBridgeConsumer` against a real
broker, and `PostgresEscalationTeamNoticeInbox` against a live Postgres with migration 0015
applied. Those are listed for the engine runner.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import signal
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream
import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.platform import rco_avisos
from maezo.platform.integrations.notifications_bridge import (
    REASON_MISSING_ESCALATION_ANCHOR,
    REASON_NOT_A_JSON_OBJECT,
    REASON_RCO_AVISO_INVALIDO,
    REASON_RCO_FILA_SEM_GRUPO,
    AioKafkaBridgeConsumer,
    BridgeDlqShunt,
    BridgeKafkaConsumer,
    MalformedBridgeMessageError,
)
from maezo.tools.workers.escalation import (
    NOTIFY_SUPERVISOR_NOTIFICATION_TYPE,
    NOTIFY_TEAM_NOTIFICATION_TYPE,
)

logger = structlog.get_logger(__name__)

#: The ONE topic both notification daemons read. Named here (not re-derived) so a topic rename is
#: a single reviewed edit.
NOTIFICATIONS_TOPIC: Final[str] = "operadora.notifications.internal"

#: This daemon's consumer group. DISTINCT from the bridge's by construction: sharing a group id
#: would make the two daemons compete for partitions and each would see only a fraction of the
#: messages — the bridge would miss SLA alerts and this inbox would miss notices, both silently.
DEFAULT_INBOX_CONSUMER_GROUP_ID: Final[str] = "maezo-notifications-inbox"

#: The only audience an escalation candidate group can be. Mirrored by the schema's CHECK, so a
#: drift between this constant and the table is a write failure, not a wrong row.
NOTICE_AUDIENCE: Final[str] = "staff"


class MalformedTeamNoticeError(MalformedBridgeMessageError):
    """A message CLAIMING to be `escalation.notify_team` that cannot be recorded as one.

    A subclass, deliberately: `run_inbox_loop` reuses `notifications_bridge`'s DLQ shunt, which is
    typed on `MalformedBridgeMessageError`, so this error quarantines through exactly the same
    confirmed-publish-plus-durable-audit path and inherits its fail-closed guarantee (a failed
    shunt re-raises and the offset does not advance).

    It is raised ONLY for deterministic malformation — a message whose `type` says notify_team but
    which carries no `grupo_atendimento` or no `business_key`. Those bytes fail identically on
    every redelivery, so blocking the offset forever would stall the partition and never succeed.
    A message of ANY OTHER `type` is not malformed at all: it belongs to another consumer of this
    shared topic and is skipped silently (see `parse_team_notice`).
    """


class NoticeRecordOutcome(StrEnum):
    """What `record` OBSERVED. Two outcomes because there are two distinguishable states."""

    RECORDED = "recorded"
    """A fresh row was committed by THIS call. The team notice is newly durable."""

    DUPLICATE = "duplicate"
    """`(tenant, notice_ref)` was already present — a redelivery of byte-identical notification
    content. Nothing was mutated (the row is immutable by trigger anyway), and the caller may let
    the offset advance: the delivery this message represents is already durable."""


@dataclass(frozen=True, slots=True, kw_only=True)
class TeamNotice:
    """One `escalation.notify_team` notification, validated into the columns of a durable row.

    Every field is either a business identifier the engine itself owns (`tenant_id`,
    `business_key`) or a bounded vocabulary token the PUBLISHER already validated against its
    contractual/DMN domain. There is no free-text field and no beneficiary reference: the
    notification never carried either, and this type deliberately has nowhere to put them.
    """

    tenant_id: str
    #: `ESC-{tenant}-{conversation_id}` — the engine's own business key, read off the task by the
    #: publishing worker. The case reference a human opens; never re-derived on this side.
    business_key: str
    grupo_atendimento: str
    #: `None` is a CONTRACTUAL value, not a missing one: under `motivo_categoria=falha_tecnica`
    #: the contract declares `severidade` null and the publisher forwards it as `None` rather than
    #: fabricating a domain value (§Delta-3 in `escalation.py`). Coercing it to a default here
    #: would invent the clinical severity that exception exists to withhold.
    severidade: str | None = None
    prioridade: str | None = None
    motivo_categoria: str | None = None
    #: Which notification this line came from. `escalation.notify_team` (default) or
    #: `escalation.notify_supervisor` (DL-0140). NOT a column: it only enters the digest below,
    #: and only for the supervisor notice.
    notice_type: str = NOTIFY_TEAM_NOTIFICATION_TYPE
    #: The supervisor notice's reason (`sla_ack_breached` / `notificacao_primaria_falhou`, BPMN
    #: literals). Digest-only, like `notice_type`; always `None` for a team notice.
    motivo_alerta: str | None = None

    @property
    def audience(self) -> str:
        """Always `staff` — see the module docstring's audience-scoping paragraph."""
        return NOTICE_AUDIENCE

    @property
    def notice_ref(self) -> str:
        """`sha256` over this notice's canonical content — the row's identity and its idempotency.

        Derived from CONTENT, never from broker coordinates or a clock. That is what makes a
        redelivery converge on one row (same bytes -> same ref -> the insert is a no-op) while a
        genuinely new notification about the SAME escalation — a re-notify after the channel
        fallback, a second SLA cycle — differs in at least one field and therefore earns its own
        row that the human still sees. Keying on `business_key` alone would have collapsed those
        into the first one and silently lost every later notice; keying on coordinates would have
        created a duplicate row for every redelivery.

        `sort_keys` + compact separators is the canonicalisation this repo already uses for
        digests it must be able to recompute (`portal/engine/profile.py::canonicalize`, the audit
        chain), so the same notice hashes identically in any process.

        DL-0140 — SUPERVISOR NOTICES ONLY add `type` and `motivo_alerta` to the canonical content.
        The two supervisor alerts the BPMN can raise for ONE case (channel fallback
        `notificacao_primaria_falhou` and ack deadline `sla_ack_breached`) carry otherwise identical
        columns; without the reason in the digest the second would collapse into the first as a
        `DUPLICATE` and the supervisor would never see it. A TEAM notice's canonical content is
        left byte-for-byte as it was, so every `notice_ref` already in the table stays valid and a
        redelivered team notice still converges on its existing row (pinned by a fixed-value test).
        """
        content: dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "business_key": self.business_key,
            "audience": self.audience,
            "grupo_atendimento": self.grupo_atendimento,
            "severidade": self.severidade,
            "prioridade": self.prioridade,
            "motivo_categoria": self.motivo_categoria,
        }
        if self.notice_type == NOTIFY_SUPERVISOR_NOTIFICATION_TYPE:
            content["type"] = self.notice_type
            content["motivo_alerta"] = self.motivo_alerta
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class TeamNoticeReceipt:
    """Proof that a notice is durable — what a caller may advance an offset on.

    Carries the row's identity and the SERVER's commit timestamp (read back from the row, not
    taken from this process's clock), so the receipt names a fact the database attests rather
    than one the consumer asserts.
    """

    notice_ref: str
    outcome: NoticeRecordOutcome
    recorded_at: datetime


def _non_blank(payload: dict[str, Any], field_name: str) -> str | None:
    """The field's stripped value, or `None` when absent/blank/explicitly null.

    Mirrors `notification_bridge._non_blank`'s posture for the same reason recorded there: a naive
    `bool(str(...).strip())` treats an explicit `None` as the non-blank string `"None"`, which
    would let a null anchor through as literal text.
    """
    value = payload.get(field_name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_label(payload: dict[str, Any], field_name: str) -> str | None:
    """An OPTIONAL bounded label, or `None`.

    No domain check here, and that is deliberate rather than lax: `escalation.py` validates
    `prioridade` against the DMN's `{P1,P2,P3}` (fail-closed) and `motivo_categoria` against the
    contract's six values (degrade-and-omit) BEFORE publishing, so a value that arrives here was
    already admitted by the authority that owns the vocabulary. Re-deciding it on this side could
    only produce an inbox line that disagrees with the queue the engine paged. The DATABASE still
    constrains `prioridade` (`CHECK (prioridade IN ('P1','P2','P3'))`), so a corrupted value is a
    write failure — loud — not a wrong row.
    """
    return _non_blank(payload, field_name)


def parse_team_notice(value: Any) -> TeamNotice | None:
    """Validate ONE message off the shared topic. `None` means "not ours"; raising means poison.

    Three outcomes, and the distinction between the last two is the whole point:

      * `None` — the message is neither `escalation.notify_team` nor (DL-0140)
        `escalation.notify_supervisor` (see `_parse_supervisor_notice`). This topic is shared
        with the SLA-risk alerts and every other internal notification, so a foreign `type` is the
        NORMAL case, not an error. Skipped silently; the offset may advance.
      * `TeamNotice` — a well-formed notice, ready to record.
      * `MalformedTeamNoticeError` — the message SAYS it is a notify_team notification and is not
        recordable as one. Fail-closed: a notice with no `grupo_atendimento` could not be routed
        to a queue, and one with no `business_key` names no case, so a human receiving it could
        not act on it. Recording a notice nobody can act on is worse than refusing it, because it
        would look like a delivered escalation.

    A non-object message is refused with the bridge's own `not_a_json_object` code so both daemons
    speak one DLQ vocabulary.
    """
    if not isinstance(value, dict):
        raise MalformedTeamNoticeError(
            f"team notice must be a JSON object, got {type(value).__name__}",
            value,
            code=REASON_NOT_A_JSON_OBJECT,
        )
    if value.get("type") == NOTIFY_SUPERVISOR_NOTIFICATION_TYPE:
        return _parse_supervisor_notice(value)
    if value.get("type") != NOTIFY_TEAM_NOTIFICATION_TYPE:
        return None

    tenant_id = _non_blank(value, "tenant_id")
    business_key = _non_blank(value, "business_key")
    grupo = _non_blank(value, "grupo_atendimento")
    missing = [
        name
        for name, present in (
            ("tenant_id", tenant_id),
            ("business_key", business_key),
            ("grupo_atendimento", grupo),
        )
        if present is None
    ]
    if missing:
        raise MalformedTeamNoticeError(
            f"{NOTIFY_TEAM_NOTIFICATION_TYPE} notification is missing required anchors: "
            f"{sorted(missing)} — it names no case and/or no queue, so it cannot become an "
            "actionable inbox line",
            value,
            code=REASON_MISSING_ESCALATION_ANCHOR,
        )
    # No `assert` for the narrowing: the `missing` check above already refused every `None`, and
    # an `assert` in a production path is a statement that vanishes under `-O`. The empty-string
    # fallbacks below are therefore UNREACHABLE — they exist only to give the type checker a
    # non-optional value, and reaching one would mean the refusal above stopped working.
    return TeamNotice(
        tenant_id=tenant_id or "",
        business_key=business_key or "",
        grupo_atendimento=grupo or "",
        # Absent AND explicitly-null both arrive as `None`, which is the contract's own value for
        # the `falha_tecnica` exception — see the field's docstring.
        severidade=_non_blank(value, "severidade"),
        prioridade=_optional_label(value, "prioridade"),
        motivo_categoria=_optional_label(value, "motivo_categoria"),
    )


def _parse_supervisor_notice(value: dict[str, Any]) -> TeamNotice:
    """`escalation.notify_supervisor` -> a line in the SAME table, addressed to the supervisor group.

    DL-0140. The row's `grupo_atendimento` is the notice's `alert_to` (`supervisao-atendimento`,
    set by the publisher as a constant) — the group that must act — not the payload's own
    `grupo_atendimento`, which names the team that was (or should have been) on the hook. The
    payload carries only identifiers and closed labels (`type`, `tenant_id`, `business_key`,
    `alert_to`, `grupo_atendimento`, `severidade`, `prioridade`, `motivo_alerta`): no free text
    (ADR-0006). No `business_key` (or no `alert_to`) names no case/queue -> dead letter, exactly like
    an anchorless team notice.
    """
    tenant_id = _non_blank(value, "tenant_id")
    business_key = _non_blank(value, "business_key")
    grupo = _non_blank(value, "alert_to")
    missing = [
        name
        for name, present in (("tenant_id", tenant_id), ("business_key", business_key), ("alert_to", grupo))
        if present is None
    ]
    if missing:
        raise MalformedTeamNoticeError(
            f"{NOTIFY_SUPERVISOR_NOTIFICATION_TYPE} notification is missing required anchors: "
            f"{sorted(missing)} — it names no case and/or no queue, so it cannot become an "
            "actionable inbox line",
            value,
            code=REASON_MISSING_ESCALATION_ANCHOR,
        )
    return TeamNotice(
        tenant_id=tenant_id or "",
        business_key=business_key or "",
        grupo_atendimento=grupo or "",
        severidade=_non_blank(value, "severidade"),
        prioridade=_optional_label(value, "prioridade"),
        motivo_categoria=None,
        notice_type=NOTIFY_SUPERVISOR_NOTIFICATION_TYPE,
        motivo_alerta=_optional_label(value, "motivo_alerta"),
    )


# ---------------------------------------------------------------------------------------------------
# DL-0186: `rco.aviso_auditoria` — o aviso de `sinalAuditoria` do K1, enderecado ao grupo da fila no pin
# ---------------------------------------------------------------------------------------------------

#: O `type` publicado pelo relay do outbox (DL-0185); definido em `rco_avisos`, nao repetido aqui.
RCO_AVISO_TYPE: Final[str] = rco_avisos.TIPO_NOTIFICACAO

#: Desfechos de `maezo_rco_aviso_consumo_total` (= `observability.RCO_AVISO_CONSUMO_DESFECHOS`, paridade em
#: teste): linha nova, re-entrega, fila sem grupo no pin, mensagem fora da forma, sem inbox ligado, pin
#: ilegivel.
RCO_AVISO_CONSUMO: Final[frozenset[str]] = frozenset(
    {"registrado", "repetido", "fila_sem_grupo", "invalido", "nao_configurado", "pin_indisponivel"}
)


class MalformedRcoAvisoError(MalformedTeamNoticeError):
    """Uma mensagem `rco.aviso_auditoria` que nao pode virar linha de grupo. Recusa DETERMINISTICA.

    Subclasse de `MalformedTeamNoticeError` de proposito: o laco a manda para a fila morta pelo MESMO
    `BridgeDlqShunt` (publicacao confirmada + fato duravel na cadeia de auditoria com o `reason_code`), que e
    o registro duravel da falha sem mudanca de schema. Dois codigos fechados (`BRIDGE_DLQ_REASONS`):
    `rco_aviso_invalido` (forma, chaves ou `chave_idempotencia` que nao bate com o conteudo) e
    `rco_fila_sem_grupo` (o K6 pinado nao declara `grupo_principal` para a fila). Nos dois casos o aviso
    TERMINA aqui como falha: nunca e entregue a outro grupo. `reason` e texto fixo mais, no maximo, o NOME de
    um campo; nunca um valor da mensagem.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class AvisoRcoGrupo:
    """Um aviso `rco.aviso_auditoria` conferido e enderecado a UM grupo (o `grupo_principal` da fila no pin).

    So ids e tokens fechados: os da linha do outbox (`rco_avisos.LinhaAviso`: tenant, task_id, composicao,
    sinal, fila, prazo_parametro, chave_idempotencia) e o grupo. Nenhum dado de beneficiario, nenhum texto
    livre: a mensagem nao os traz e este tipo nao tem onde po-los.
    """

    linha: rco_avisos.LinhaAviso
    grupo: str

    @property
    def tenant_id(self) -> str:
        return self.linha.tenant

    @property
    def audience(self) -> str:
        """Sempre `staff`: o grupo de uma fila do K6 e fila de equipe, como o grupo de escalonamento."""
        return NOTICE_AUDIENCE

    @property
    def notice_ref(self) -> str:
        """sha256 canonico de `(type, tenant, chave_idempotencia, grupo, audience)`: a identidade da linha.

        Idempotente na `chave_idempotencia` do outbox: a re-entrega (do relay ou do broker) cai na MESMA
        linha.
        O grupo entra no digest porque e ele o endereco da linha: se um pin novo mudar o grupo da fila, a
        re-entrega vira uma linha do grupo novo (a direcao nao adversa), nunca reescreve a antiga.
        """
        content = {
            "type": RCO_AVISO_TYPE,
            "tenant_id": self.linha.tenant,
            "chave_idempotencia": self.linha.chave_idempotencia,
            "grupo": self.grupo,
            "audience": self.audience,
        }
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_aviso_rco(value: Any) -> rco_avisos.LinhaAviso | None:
    """`None` se a mensagem nao e `rco.aviso_auditoria`; a linha conferida; ou `MalformedRcoAvisoError`."""
    if not isinstance(value, dict) or value.get("type") != RCO_AVISO_TYPE:
        return None
    try:
        return rco_avisos.linha_da_mensagem(value)
    except rco_avisos.AvisoInvalidoError as exc:
        raise MalformedRcoAvisoError(
            f"{RCO_AVISO_TYPE}: campo fora da forma ({exc})", value, code=REASON_RCO_AVISO_INVALIDO
        ) from None


def resolver_grupo(linha: rco_avisos.LinhaAviso, grupos: Mapping[str, str]) -> AvisoRcoGrupo:
    """O grupo da fila e DADO do pin (`grupos`); sem grupo -> `MalformedRcoAvisoError` (`rco_fila_sem_grupo`).

    Nunca ha grupo padrao, grupo "parecido" nem outro destino: sem `grupo_principal` o aviso termina como
    falha com codigo fechado.
    """
    grupo = grupos.get(linha.fila)
    if not isinstance(grupo, str) or not grupo:
        raise MalformedRcoAvisoError(
            f"{RCO_AVISO_TYPE}: a fila nao tem grupo_principal no K6 pinado",
            rco_avisos.montar_payload(linha),
            code=REASON_RCO_FILA_SEM_GRUPO,
        )
    return AvisoRcoGrupo(linha=linha, grupo=grupo)


@runtime_checkable
class InboxAvisosRco(Protocol):
    """O destino duravel da linha de grupo de um aviso `rco.aviso_auditoria` (DL-0186).

    Mesmo contrato de `EscalationTeamNoticeInbox.record`: devolve o recibo SO depois de a linha estar
    commitada (`RECORDED`) ou de achar a mesma linha ja gravada (`DUPLICATE`); qualquer falha levanta e o
    offset nao avanca. Implementacao Postgres: `PostgresInboxAvisosRco` (tabela `rco_aviso_grupo`, 0037).
    """

    async def registrar(self, aviso: AvisoRcoGrupo) -> TeamNoticeReceipt: ...


GruposDoPin = Callable[[], Mapping[str, str]]


def _grupos_do_pin_de_bordo() -> Mapping[str, str]:
    """`grupo_principal` por fila do pin de bordo (import tardio: o inbox nao carrega o pin sem aviso rco)."""
    from maezo.tools.workers import rco_pin

    return rco_pin.rco_rules_pin().grupos_principais


def _contar_consumo(desfecho: str) -> None:
    try:
        from maezo.platform.observability import record_rco_aviso_consumo

        record_rco_aviso_consumo(desfecho)
    except Exception:  # pragma: no cover - a telemetria nunca muda o que o inbox grava
        logger.debug("notifications_inbox.rco_aviso_metric_failed", desfecho=desfecho)


async def handle_aviso_rco(
    inbox: InboxAvisosRco, value: Any, *, grupos_do_pin: GruposDoPin | None = None
) -> TeamNoticeReceipt | None:
    """Confere, endereca e grava UM aviso `rco.aviso_auditoria`. `None` se a mensagem nao e desse tipo.

    Ordem: forma (`rco_aviso_invalido`) -> grupo da fila no pin (`rco_fila_sem_grupo`; pin ilegivel levanta e
    o offset fica) -> `inbox.registrar` -> recibo. Toda falha sai levantando, como em `handle_inbox_message`.
    """
    try:
        linha = parse_aviso_rco(value)
    except MalformedRcoAvisoError:
        _contar_consumo("invalido")
        logger.warning("notifications_inbox.rco_aviso_recusado", codigo=REASON_RCO_AVISO_INVALIDO)
        raise
    if linha is None:
        return None
    try:
        grupos = (grupos_do_pin or _grupos_do_pin_de_bordo)()
    except Exception as exc:
        # Pin ilegivel e transitorio/configuracao: o offset NAO avanca (re-entrega), nada vai a fila morta.
        _contar_consumo("pin_indisponivel")
        logger.error(
            "notifications_inbox.rco_aviso_pin_indisponivel", task_id=linha.task_id, erro=type(exc).__name__
        )
        raise
    try:
        aviso = resolver_grupo(linha, grupos)
    except MalformedRcoAvisoError:
        _contar_consumo("fila_sem_grupo")
        logger.warning(
            "notifications_inbox.rco_aviso_recusado",
            codigo=REASON_RCO_FILA_SEM_GRUPO,
            tenant_id=linha.tenant,
            task_id=linha.task_id,
            composicao=linha.composicao,
            sinal=linha.sinal,
            fila=linha.fila,
        )
        raise
    receipt = await inbox.registrar(aviso)
    _contar_consumo("registrado" if receipt.outcome is NoticeRecordOutcome.RECORDED else "repetido")
    logger.info(
        "notifications_inbox.rco_aviso_registrado",
        tenant_id=linha.tenant,
        task_id=linha.task_id,
        composicao=linha.composicao,
        sinal=linha.sinal,
        fila=linha.fila,
        grupo=aviso.grupo,
        prazo_parametro=linha.prazo_parametro,
        outcome=str(receipt.outcome),
        notice_ref=receipt.notice_ref,
    )
    return receipt


@runtime_checkable
class EscalationTeamNoticeInbox(Protocol):
    """The durable sink a notice must reach before any offset advances.

    A Protocol so the loop can be driven in unit tests without Postgres, and `runtime_checkable`
    so `run_inbox_loop` can REFUSE a collaborator that does not implement it rather than
    discovering the missing method mid-message with the offset already at risk.
    """

    async def record(self, notice: TeamNotice) -> TeamNoticeReceipt: ...


_INSERT_SQL: Final[str] = (
    "INSERT INTO escalation_team_notice "
    "(tenant, notice_ref, business_key, audience, grupo_atendimento, severidade, prioridade, "
    "motivo_categoria) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
    "ON CONFLICT (tenant, notice_ref) DO NOTHING "
    "RETURNING recorded_at"
)

_SELECT_RECORDED_AT_SQL: Final[str] = (
    "SELECT recorded_at FROM escalation_team_notice WHERE tenant = $1 AND notice_ref = $2"
)


class _PostgresTenantInbox:
    """O pool por tenant dos inboxes Postgres deste modulo (escalonamento e, DL-0186, avisos rco).

    Lazy asyncpg pool with `search_path` pinned to the tenant's schema on EVERY acquire via
    `setup=` — NOT `init=`: asyncpg resets session state on release, so an `init`-only search_path
    silently reverts on a reused connection (the defect `PostgresAuditSink` fixed and every
    sibling repository in this tree inherited the fix from).

    `tenant` is validated as a schema identifier by `schema_for_tenant` before it is ever
    interpolated into `SET search_path` — the same anti-injection rule the audit sink, the
    checkpointer and the A2A idempotency store apply.
    """

    def __init__(self, *, dsn: str, tenant: str, pool: asyncpg.Pool | None = None) -> None:
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        self._dsn = normalize_dsn(dsn)
        self._pool = pool

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                # Same deliberate default as the four sibling pools (D4-02 / R-108, owner option
                # B): a2a/idempotency.py, a2a/outbox.py, platform/integrations/amh_inbox.py,
                # gateway/audit_postgres.py. Change all of them together, and only after SC-05
                # produces the aggregate connection arithmetic.
                min_size=1,
                max_size=10,
                setup=self._set_search_path,
            )
        return self._pool

    async def _set_search_path(self, conn: asyncpg.Connection) -> None:
        # `self._schema` was validated by `schema_for_tenant()` in `__init__` (anti-injection).
        await conn.execute(f'SET search_path TO "{self._schema}"')


class PostgresEscalationTeamNoticeInbox(_PostgresTenantInbox):
    """The real durable inbox over Postgres (satisfies `EscalationTeamNoticeInbox`).

    The pool (lazy, `search_path` pinned on EVERY acquire via `setup=`, tenant validated by
    `schema_for_tenant`) is `_PostgresTenantInbox`'s, shared with `PostgresInboxAvisosRco` (DL-0186).
    """

    async def record(self, notice: TeamNotice) -> TeamNoticeReceipt:
        """Idempotently commit the notice's row and return the receipt that licenses an ack.

        REFUSES a notice for another tenant. This repository is constructed per tenant and pins
        `search_path` to that tenant's schema, so writing a foreign tenant's notice here would put
        it in the WRONG schema while the row's own `tenant` column claimed otherwise — a
        cross-tenant leak that reads as correct data. Loud refusal instead.

        `ON CONFLICT DO NOTHING` + a read-back is what distinguishes the two honest outcomes: a
        returned row means this call inserted it (`RECORDED`); no returned row means the identical
        notice was already durable (`DUPLICATE`), and the read-back supplies the ORIGINAL commit
        timestamp so the receipt dates the delivery rather than this redelivery. A read-back that
        finds nothing is impossible without an outside deleter (the table's trigger refuses
        `DELETE`), so it raises rather than inventing a timestamp.
        """
        if notice.tenant_id != self._tenant:
            raise ValueError(
                f"PostgresEscalationTeamNoticeInbox is bound to tenant {self._tenant!r} and was "
                f"handed a notice for {notice.tenant_id!r} — refusing to write it into the wrong "
                "tenant schema"
            )
        notice_ref = notice.notice_ref
        pool = await self._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            inserted = await conn.fetchrow(
                _INSERT_SQL,
                notice.tenant_id,
                notice_ref,
                notice.business_key,
                notice.audience,
                notice.grupo_atendimento,
                notice.severidade,
                notice.prioridade,
                notice.motivo_categoria,
            )
            if inserted is not None:
                return TeamNoticeReceipt(
                    notice_ref=notice_ref,
                    outcome=NoticeRecordOutcome.RECORDED,
                    recorded_at=inserted["recorded_at"],
                )
            existing = await conn.fetchrow(_SELECT_RECORDED_AT_SQL, notice.tenant_id, notice_ref)
        if existing is None:
            raise RuntimeError(
                "escalation_team_notice: the insert conflicted but the conflicting row could not "
                f"be read back (tenant={self._tenant!r}, notice_ref={notice_ref!r}) — the table "
                "refuses DELETE by trigger, so this means an outside writer removed it; refusing "
                "to report a delivery this process cannot evidence"
            )
        return TeamNoticeReceipt(
            notice_ref=notice_ref,
            outcome=NoticeRecordOutcome.DUPLICATE,
            recorded_at=existing["recorded_at"],
        )


_INSERT_AVISO_RCO_SQL: Final[str] = (
    "INSERT INTO rco_aviso_grupo "
    "(tenant, notice_ref, chave_idempotencia, task_id, composicao, sinal, fila, prazo_parametro, grupo, "
    "audience) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
    "ON CONFLICT (tenant, notice_ref) DO NOTHING "
    "RETURNING registrado_em"
)

_SELECT_AVISO_RCO_SQL: Final[str] = (
    "SELECT registrado_em FROM rco_aviso_grupo WHERE tenant = $1 AND notice_ref = $2"
)


class PostgresInboxAvisosRco(_PostgresTenantInbox):
    """O destino duravel dos avisos `rco.aviso_auditoria` sobre a tabela imutavel `rco_aviso_grupo` (0037).

    Satisfaz `InboxAvisosRco` (e so ela: nao tem `record`). O pool e' o mesmo de
    `PostgresEscalationTeamNoticeInbox` (`_PostgresTenantInbox`: lazy, `search_path` fixado no schema do
    tenant a CADA acquire por `setup=`, tenant validado por `schema_for_tenant`). Mesma semantica de recibo
    de `record`: `ON CONFLICT DO NOTHING` + releitura distingue `RECORDED` (esta chamada inseriu) de
    `DUPLICATE` (a mesma linha ja estava commitada; o recibo leva o `registrado_em` ORIGINAL); releitura
    vazia so' com um removedor de fora (o trigger recusa `DELETE`) e levanta. Aviso de outro tenant e'
    recusado: gravaria no schema errado.
    """

    async def registrar(self, aviso: AvisoRcoGrupo) -> TeamNoticeReceipt:
        if aviso.tenant_id != self._tenant:
            raise ValueError(
                f"PostgresInboxAvisosRco is bound to tenant {self._tenant!r} and was handed a notice for "
                f"{aviso.tenant_id!r} — refusing to write it into the wrong tenant schema"
            )
        linha = aviso.linha
        notice_ref = aviso.notice_ref
        pool = await self._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            inserted = await conn.fetchrow(
                _INSERT_AVISO_RCO_SQL,
                linha.tenant,
                notice_ref,
                linha.chave_idempotencia,
                linha.task_id,
                linha.composicao,
                linha.sinal,
                linha.fila,
                linha.prazo_parametro,
                aviso.grupo,
                aviso.audience,
            )
            if inserted is not None:
                return TeamNoticeReceipt(
                    notice_ref=notice_ref,
                    outcome=NoticeRecordOutcome.RECORDED,
                    recorded_at=inserted["registrado_em"],
                )
            existing = await conn.fetchrow(_SELECT_AVISO_RCO_SQL, linha.tenant, notice_ref)
        if existing is None:
            raise RuntimeError(
                "rco_aviso_grupo: the insert conflicted but the conflicting row could not be read back "
                f"(tenant={self._tenant!r}, notice_ref={notice_ref!r}) — the table refuses DELETE by "
                "trigger, so this means an outside writer removed it; refusing to report a delivery this "
                "process cannot evidence"
            )
        return TeamNoticeReceipt(
            notice_ref=notice_ref,
            outcome=NoticeRecordOutcome.DUPLICATE,
            recorded_at=existing["registrado_em"],
        )


async def handle_inbox_message(
    inbox: EscalationTeamNoticeInbox,
    value: Any,
    *,
    avisos_rco: InboxAvisosRco | None = None,
    grupos_do_pin: GruposDoPin | None = None,
) -> TeamNoticeReceipt | None:
    """Parse ONE message and, when it is ours, record it. `None` means the message was not ours.

    The whole unit of "delivery" lives here: this coroutine returns only after `record` has
    committed a row and handed back a receipt. Every failure — a malformed notice, a database
    fault, a tenant mismatch — leaves by raising, so the caller cannot reach its commit.

    DL-0186: a `rco.aviso_auditoria` message goes to `handle_aviso_rco` when `avisos_rco` is wired; without
    it the type stays foreign (skipped, as before) and is counted as `nao_configurado`.
    """
    if isinstance(value, dict) and value.get("type") == RCO_AVISO_TYPE:
        if avisos_rco is None:
            _contar_consumo("nao_configurado")
            logger.debug("notifications_inbox.rco_aviso_sem_inbox")
            return None
        return await handle_aviso_rco(avisos_rco, value, grupos_do_pin=grupos_do_pin)
    notice = parse_team_notice(value)
    if notice is None:
        return None
    receipt = await inbox.record(notice)
    logger.info(
        "notifications_inbox.recorded",
        tenant_id=notice.tenant_id,
        grupo_atendimento=notice.grupo_atendimento,
        audience=notice.audience,
        outcome=str(receipt.outcome),
        notice_ref=receipt.notice_ref,
    )
    return receipt


async def run_inbox_loop(
    consumer: BridgeKafkaConsumer,
    inbox: EscalationTeamNoticeInbox,
    *,
    stop_event: asyncio.Event | None = None,
    dlq: BridgeDlqShunt | None = None,
    avisos_rco: InboxAvisosRco | None = None,
    grupos_do_pin: GruposDoPin | None = None,
) -> None:
    """Drive `handle_inbox_message` over every message `consumer` yields.

    THE ORDER IS THE GUARANTEE (ADR-0037 XRD-10). `consumer.commit()` is reached only after the
    message was recorded (a committed row + a receipt), skipped as foreign, or — when a shunt is
    wired — CONFIRMED into the dead-letter topic with its durable audit fact. A database fault, a
    tenant mismatch or any other exception propagates, the commit is never reached, and the broker
    redelivers; `record`'s idempotency is what makes that redelivery converge instead of
    duplicating.

    The DLQ arm is narrowed to `MalformedTeamNoticeError` exactly as the bridge's loop narrows its
    own: that error class is DETERMINISTIC malformation, where retry provably cannot succeed.
    Everything else — the transient faults where retry is right — still blocks the offset. With
    `dlq=None` a malformed notice re-raises and nothing is committed.

    The collaborator is checked ONCE, before the first message: an inbox that cannot `record`
    would otherwise be discovered mid-stream, with a message already consumed. The same holds for
    `avisos_rco` (DL-0186): when given it must implement `registrar` (`InboxAvisosRco`), and a
    `MalformedRcoAvisoError` (`rco_aviso_invalido` / `rco_fila_sem_grupo`) takes the same DLQ arm.
    """
    if not isinstance(inbox, EscalationTeamNoticeInbox):
        raise TypeError(
            "run_inbox_loop: `inbox` must implement `record` (EscalationTeamNoticeInbox) — a sink "
            "that cannot durably record a notice could never license a commit, and refusing here "
            "keeps that from being discovered with a message already in flight"
        )
    if avisos_rco is not None and not isinstance(avisos_rco, InboxAvisosRco):
        raise TypeError(
            "run_inbox_loop: `avisos_rco` must implement `registrar` (InboxAvisosRco) — refused before the "
            "first message, like `inbox`"
        )
    async for message in consumer:
        try:
            if message.parse_error:
                raise MalformedTeamNoticeError(
                    message.parse_error,
                    message.raw,
                    code=message.parse_code or REASON_NOT_A_JSON_OBJECT,
                )
            receipt = await handle_inbox_message(
                inbox, message.value, avisos_rco=avisos_rco, grupos_do_pin=grupos_do_pin
            )
        except MalformedTeamNoticeError as exc:
            if dlq is None:
                raise
            # Raises on a failed publish / failed audit -> the commit below is never reached.
            await dlq.shunt(message, exc)
        else:
            if receipt is None:
                logger.debug("notifications_inbox.skipped_foreign_type")
        await consumer.commit()
        if stop_event is not None and stop_event.is_set():
            break


class NotificationsInboxSettings(BaseSettings):
    """Config for the notifications-inbox daemon. Mirrors `NotificationsBridgeSettings`' shape."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    tenant_id: str = Field(default="amh", alias="TENANT_ID")
    # FAIL-CLOSED: absent means `main()` REFUSES to start rather than construct a fake/in-memory
    # inbox. An inbox that is not durable cannot license an ack, and a daemon that acked anyway
    # would silently discard every escalation notice it ever saw — the precise failure this
    # module exists to end.
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    kafka_bootstrap_servers: str = Field(default="localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    kafka_topic: str = Field(default=NOTIFICATIONS_TOPIC, alias="NOTIFICATIONS_INBOX_KAFKA_TOPIC")
    kafka_group_id: str = Field(
        default=DEFAULT_INBOX_CONSUMER_GROUP_ID, alias="NOTIFICATIONS_INBOX_KAFKA_GROUP_ID"
    )


def build_inbox(settings: NotificationsInboxSettings) -> PostgresEscalationTeamNoticeInbox:
    """Composition root for the durable sink. Fail-closed on a missing `DATABASE_URL`."""
    if not settings.database_url:
        raise RuntimeError(
            "notifications_inbox: DATABASE_URL is required — this daemon's only effect is a "
            "durable inbox row, and without a database it could only pretend to deliver"
        )
    return PostgresEscalationTeamNoticeInbox(dsn=settings.database_url, tenant=settings.tenant_id)


def build_avisos_rco(settings: NotificationsInboxSettings) -> PostgresInboxAvisosRco:
    """Raiz de composicao do destino dos avisos `rco.aviso_auditoria` (DL-0186): mesmo DSN e tenant do inbox.

    Fail-closed como `build_inbox`: sem `DATABASE_URL` nao ha linha duravel que licencie o commit.
    """
    if not settings.database_url:
        raise RuntimeError(
            "notifications_inbox: DATABASE_URL is required — a rco.aviso_auditoria notice is delivered only "
            "by a durable rco_aviso_grupo row"
        )
    return PostgresInboxAvisosRco(dsn=settings.database_url, tenant=settings.tenant_id)


async def main() -> None:
    """Run the consume loop until SIGTERM/SIGINT. Mirrors `notifications_bridge.main()`."""
    settings = NotificationsInboxSettings()
    inbox = build_inbox(settings)
    avisos_rco = build_avisos_rco(settings)
    consumer: BridgeKafkaConsumer = AioKafkaBridgeConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        topic=settings.kafka_topic,
        group_id=settings.kafka_group_id,
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)
    await consumer.start()
    logger.info(
        "notifications_inbox.started",
        topic=settings.kafka_topic,
        group_id=settings.kafka_group_id,
        tenant_id=settings.tenant_id,
    )
    try:
        await run_inbox_loop(consumer, inbox, stop_event=stop_event, avisos_rco=avisos_rco)
    finally:
        await consumer.stop()
        logger.info("notifications_inbox.stopped")


if __name__ == "__main__":  # pragma: no cover - process entry point
    asyncio.run(main())
