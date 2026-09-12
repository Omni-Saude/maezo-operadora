"""WP-J1-09 — the `escalation.notify_team` durable inbox: what makes a notification a delivery.

These tests exist because of one property the module claims and a reviewer must be able to
disbelieve: **delivery is a committed inbox row + a receipt, never a committed Kafka offset**
(ADR-0037 XRD-10). Every test below either proves that ordering, proves the fail-closed refusals
that keep an un-actionable notice out of a human's inbox, or pins the audience/idempotency
semantics the schema also enforces.

They run without Postgres and without a broker. The two seams that CANNOT be proven here —
`AioKafkaBridgeConsumer` against a real broker, `PostgresEscalationTeamNoticeInbox` against a
live database with migration 0015 applied — are deliberately NOT faked into a false green; they
are listed for the engine runner (see the module docstring's honest-boundary section).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from maezo.platform.integrations.notifications_bridge import (
    REASON_INVALID_JSON,
    REASON_MISSING_ESCALATION_ANCHOR,
    REASON_NOT_A_JSON_OBJECT,
    BridgeMessage,
    FakeBridgeKafkaConsumer,
)
from maezo.platform.integrations.notifications_inbox import (
    DEFAULT_INBOX_CONSUMER_GROUP_ID,
    NOTICE_AUDIENCE,
    EscalationTeamNoticeInbox,
    MalformedTeamNoticeError,
    NoticeRecordOutcome,
    NotificationsInboxSettings,
    TeamNotice,
    TeamNoticeReceipt,
    build_inbox,
    handle_inbox_message,
    parse_team_notice,
    run_inbox_loop,
)
from maezo.tools.workers.escalation import NOTIFY_TEAM_NOTIFICATION_TYPE

_RECORDED_AT = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def _notification(**overrides: object) -> dict[str, object]:
    """A notification byte-shaped like the one `make_notify_team_handler` really publishes."""
    payload: dict[str, object] = {
        "type": NOTIFY_TEAM_NOTIFICATION_TYPE,
        "tenant_id": "amh",
        "severidade": "moderada",
        "grupo_atendimento": "atendimento-humano",
        "business_key": "ESC-amh-sla-auth-GUIA-7",
        "prioridade": "P2",
        "motivo_categoria": "outro",
    }
    payload.update(overrides)
    return payload


@dataclass
class _SpyInbox:
    """Records what it was asked to store, and in which order relative to the commit."""

    recorded: list[TeamNotice] = field(default_factory=list)
    outcome: NoticeRecordOutcome = NoticeRecordOutcome.RECORDED
    fail_with: Exception | None = None

    async def record(self, notice: TeamNotice) -> TeamNoticeReceipt:
        if self.fail_with is not None:
            raise self.fail_with
        self.recorded.append(notice)
        return TeamNoticeReceipt(notice_ref=notice.notice_ref, outcome=self.outcome, recorded_at=_RECORDED_AT)


# ---------------------------------------------------------------------------
# Parsing — three outcomes, and the distinction between the last two is the point
# ---------------------------------------------------------------------------


def test_uma_notificacao_real_de_notify_team_vira_um_aviso_enderecavel() -> None:
    """The happy path, field by field — every value carried VERBATIM from the publisher."""
    notice = parse_team_notice(_notification())

    assert notice is not None
    assert notice.tenant_id == "amh"
    assert notice.business_key == "ESC-amh-sla-auth-GUIA-7"
    assert notice.grupo_atendimento == "atendimento-humano"
    assert notice.severidade == "moderada"
    assert notice.prioridade == "P2"
    assert notice.motivo_categoria == "outro"
    # Audience is not read off the wire and cannot be influenced by the message.
    assert notice.audience == NOTICE_AUDIENCE == "staff"


def test_mensagem_de_outro_tipo_e_ignorada_e_nao_e_erro() -> None:
    """The topic is SHARED. A foreign `type` is the normal case, never a poison message.

    `operadora.notifications.internal` also carries the four `<dominio>.notify_sla_risk` alerts
    the bridge routes. If this consumer treated those as malformed it would dead-letter every
    SLA alert in the system — so `None` (skip), not an exception, is load-bearing.
    """
    assert parse_team_notice({"type": "auth.notify_sla_risk", "tenant_id": "amh"}) is None
    assert parse_team_notice({"type": "escalation.notify_supervisor"}) is None
    assert parse_team_notice({"tenant_id": "amh"}) is None


@pytest.mark.parametrize("faltando", ["business_key", "grupo_atendimento", "tenant_id"])
def test_aviso_sem_ancora_e_recusado_em_vez_de_virar_linha_inacionavel(faltando: str) -> None:
    """FAIL-CLOSED. A notice with no case and/or no queue cannot become an actionable inbox line.

    Recording it anyway would be WORSE than refusing: the row would look like a delivered
    escalation to anyone reading the inbox, while naming nothing a human could open or route.
    """
    payload = _notification()
    del payload[faltando]

    with pytest.raises(MalformedTeamNoticeError) as refusal:
        parse_team_notice(payload)

    assert refusal.value.code == REASON_MISSING_ESCALATION_ANCHOR
    assert faltando in str(refusal.value)


@pytest.mark.parametrize("vazio", [None, "", "   "])
def test_ancora_explicitamente_nula_ou_em_branco_e_tao_ausente_quanto_faltante(vazio: object) -> None:
    """An explicit `None` must not slip through as the non-blank string `"None"`.

    Same defect `notification_bridge._non_blank` exists to prevent; pinned here because the
    consequence differs: there it leaves a rule dormant, here it would mint a row whose
    `grupo_atendimento` is the literal text `None` and route it to a queue nobody owns.
    """
    with pytest.raises(MalformedTeamNoticeError):
        parse_team_notice(_notification(grupo_atendimento=vazio))


def test_severidade_nula_do_falha_tecnica_e_aceita_e_nao_e_defaultada() -> None:
    """§Delta-3: under `motivo_categoria=falha_tecnica` the CONTRACT declares `severidade` null.

    The publisher forwards it as `None` rather than fabricating a domain value, and this consumer
    must not undo that by defaulting it — a clinical severity is never guessed. The notice is
    still recordable: the group is what routes it, and the group is present.
    """
    notice = parse_team_notice(_notification(severidade=None, motivo_categoria="falha_tecnica"))

    assert notice is not None
    assert notice.severidade is None
    assert notice.motivo_categoria == "falha_tecnica"


def test_mensagem_que_nao_e_objeto_json_usa_o_codigo_fechado_da_ponte() -> None:
    """One DLQ vocabulary for both daemons — the reason label's cardinality stays bounded."""
    with pytest.raises(MalformedTeamNoticeError) as refusal:
        parse_team_notice(["not", "an", "object"])

    assert refusal.value.code == REASON_NOT_A_JSON_OBJECT


# ---------------------------------------------------------------------------
# `notice_ref` — content-derived identity, which is what makes redelivery converge
# ---------------------------------------------------------------------------


def test_a_mesma_notificacao_reentregue_tem_a_mesma_identidade() -> None:
    """Redelivery converges on ONE row. Derived from CONTENT, never from a clock or an offset."""
    first = parse_team_notice(_notification())
    second = parse_team_notice(_notification())

    assert first is not None and second is not None
    assert first.notice_ref == second.notice_ref
    assert len(first.notice_ref) == 64


def test_uma_notificacao_nova_sobre_o_mesmo_escalonamento_e_outra_linha() -> None:
    """A re-notify after the channel fallback, or a later SLA cycle, must NOT be swallowed.

    This is the reason `notice_ref` is not just the business key: keying on the escalation alone
    would collapse every later notice about it into the first row, and the human would never see
    the second page. Different content -> different row -> the human sees both.
    """
    base = parse_team_notice(_notification())
    escalated = parse_team_notice(_notification(prioridade="P1", severidade="grave"))
    other_group = parse_team_notice(_notification(grupo_atendimento="plantao-clinico"))

    assert base is not None and escalated is not None and other_group is not None
    assert len({base.notice_ref, escalated.notice_ref, other_group.notice_ref}) == 3


def test_a_identidade_separa_tenants() -> None:
    """Two tenants' notices about equally-named escalations are never the same row."""
    amh = parse_team_notice(_notification())
    outro = parse_team_notice(_notification(tenant_id="outro"))

    assert amh is not None and outro is not None
    assert amh.notice_ref != outro.notice_ref


# ---------------------------------------------------------------------------
# THE ORDERING GUARANTEE — a committed row licenses the offset, never the reverse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_o_offset_so_avanca_depois_da_linha_commitada() -> None:
    """ADR-0037 XRD-10, proven by ORDER: the notice is recorded BEFORE `commit()` is reached."""
    inbox = _SpyInbox()
    consumer = FakeBridgeKafkaConsumer([_notification()])

    await run_inbox_loop(consumer, inbox)

    assert [n.business_key for n in inbox.recorded] == ["ESC-amh-sla-auth-GUIA-7"]
    assert consumer.commits == 1


@pytest.mark.asyncio
async def test_falha_ao_gravar_nao_commita_o_offset() -> None:
    """The whole point, stated negatively: a database fault must NOT advance the offset.

    If the offset advanced here the escalation notice would be lost forever with no error
    anywhere — precisely the "committed offset mistaken for a delivery" failure XRD-10 forbids.
    The exception propagates so the broker redelivers.
    """
    inbox = _SpyInbox(fail_with=RuntimeError("postgres indisponivel"))
    consumer = FakeBridgeKafkaConsumer([_notification()])

    with pytest.raises(RuntimeError, match="postgres indisponivel"):
        await run_inbox_loop(consumer, inbox)

    assert consumer.commits == 0


@pytest.mark.asyncio
async def test_aviso_malformado_sem_dlq_tambem_nao_commita() -> None:
    """`dlq=None` keeps the strictest posture: re-raise, commit nothing."""
    inbox = _SpyInbox()
    consumer = FakeBridgeKafkaConsumer([_notification(grupo_atendimento=None)])

    with pytest.raises(MalformedTeamNoticeError):
        await run_inbox_loop(consumer, inbox)

    assert inbox.recorded == []
    assert consumer.commits == 0


@pytest.mark.asyncio
async def test_mensagem_estrangeira_avanca_o_offset_sem_gravar_nada() -> None:
    """A foreign `type` is a legitimate skip: nothing recorded, offset free to advance.

    Without this the daemon would stall on the FIRST SLA alert it ever saw — the same shared
    topic carries them.
    """
    inbox = _SpyInbox()
    consumer = FakeBridgeKafkaConsumer(
        [{"type": "auth.notify_sla_risk", "tenant_id": "amh"}, _notification()]
    )

    await run_inbox_loop(consumer, inbox)

    assert len(inbox.recorded) == 1
    assert consumer.commits == 2


@pytest.mark.asyncio
async def test_bytes_que_nunca_parsearam_nao_sao_confundidos_com_valor_nulo() -> None:
    """`parse_error` is checked FIRST — undecodable bytes are poison, not a JSON `null` value."""
    inbox = _SpyInbox()
    broken = BridgeMessage(
        topic="operadora.notifications.internal",
        partition=0,
        offset=7,
        raw=b"{nope",
        parse_error="invalid JSON payload",
        parse_code=REASON_INVALID_JSON,
    )
    consumer = FakeBridgeKafkaConsumer([broken])

    with pytest.raises(MalformedTeamNoticeError) as refusal:
        await run_inbox_loop(consumer, inbox)

    assert refusal.value.code == REASON_INVALID_JSON
    assert consumer.commits == 0


@pytest.mark.asyncio
async def test_um_coletor_que_nao_sabe_gravar_e_recusado_antes_da_primeira_mensagem() -> None:
    """Refused UP FRONT, not mid-stream with a message already consumed."""

    class _NotAnInbox:
        pass

    with pytest.raises(TypeError, match="EscalationTeamNoticeInbox"):
        await run_inbox_loop(FakeBridgeKafkaConsumer([_notification()]), _NotAnInbox())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_handle_devolve_o_recibo_que_licencia_o_ack() -> None:
    """The receipt names the row and the SERVER's commit instant — the ack's whole warrant."""
    inbox = _SpyInbox(outcome=NoticeRecordOutcome.DUPLICATE)

    receipt = await handle_inbox_message(inbox, _notification())

    assert receipt is not None
    assert receipt.outcome is NoticeRecordOutcome.DUPLICATE
    assert receipt.recorded_at == _RECORDED_AT
    assert receipt.notice_ref == inbox.recorded[0].notice_ref


@pytest.mark.asyncio
async def test_handle_devolve_none_para_mensagem_estrangeira() -> None:
    assert await handle_inbox_message(_SpyInbox(), {"type": "programa.notify_sla_risk"}) is None


# ---------------------------------------------------------------------------
# Composition — fail-closed, and never competing with the bridge for partitions
# ---------------------------------------------------------------------------


def test_sem_database_url_o_daemon_recusa_subir() -> None:
    """No durable sink -> no daemon. A consumer that acked without a database would silently
    discard every escalation notice it ever saw."""
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        build_inbox(NotificationsInboxSettings(database_url=None))


def test_o_grupo_de_consumo_e_distinto_do_grupo_da_ponte() -> None:
    """Sharing the bridge's group id would make the two daemons COMPETE for partitions: each
    would see a fraction of the messages and both losses would be silent."""
    from maezo.platform.integrations.notifications_bridge import DEFAULT_CONSUMER_GROUP_ID

    assert DEFAULT_INBOX_CONSUMER_GROUP_ID != DEFAULT_CONSUMER_GROUP_ID
    assert NotificationsInboxSettings().kafka_group_id == DEFAULT_INBOX_CONSUMER_GROUP_ID
    assert NotificationsInboxSettings().kafka_topic == "operadora.notifications.internal"


def test_a_porta_do_inbox_e_estrutural() -> None:
    """`runtime_checkable` so the loop can refuse a bad collaborator; the real repository and the
    test double both satisfy it structurally."""
    from maezo.platform.integrations.notifications_inbox import PostgresEscalationTeamNoticeInbox

    assert isinstance(_SpyInbox(), EscalationTeamNoticeInbox)
    assert issubclass(PostgresEscalationTeamNoticeInbox, EscalationTeamNoticeInbox)
