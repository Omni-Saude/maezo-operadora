"""Durable inbox for `escalation.notify_team` — the line that makes the notification a delivery.

**Why this table exists, in one sentence.** ADR-0037 XRD-10 states that "offsets Kafka nunca
representam conclusao de negocio", so a committed consumer offset is NOT proof that a human team
was told anything — the only thing that can be is a committed row in the payer's own database,
and for the escalation notification that row is `escalation_team_notice`.

**What was actually broken.** `SP-OP-ESCALATION-001`'s `ST_NotificarTime` publishes
`type=escalation.notify_team` onto `operadora.notifications.internal`
(`tools/workers/escalation.py::make_notify_team_handler`) and NOTHING consumed it. The worker's
own docstring is careful never to claim otherwise — it labels the publish an internal
observability record, not proof anyone was notified — but the consequence stood: the human queue
existed only as the engine User Task `UT_TratarEscalonamento`, and the notification leg was a
message into an empty room. WP-J1-09 (owner decision #17) put a REAL escalation on the other end
of an AUTH SLA alert, which made the missing consumer load-bearing rather than cosmetic.

**Group-addressed, not recipient-addressed — and that is the modelled semantics, not a shortcut.**
The row names a `grupo_atendimento`, the value the `escalation_routing` DMN chose and the SAME
expression that sets `UT_TratarEscalonamento`'s `camunda:candidateGroups`. A candidate group IS
the addressing unit of this process: the engine itself does not expand it into people, and this
repo has no group->principal resolution anywhere (measured: `grep -rn "grupo_atendimento"
src/maezo --include=*.py` reaches only the escalation worker's validation). Expanding it here
would mean INVENTING a membership authority, which is exactly the fabrication the engineering
standard forbids. `audience` is therefore fixed to `staff` by a CHECK: an escalation candidate
group is a staff queue, and there is no route in this schema by which a beneficiary or provider
could be addressed.

**PHI posture.** Five columns, and none of them is clinical: `tenant`, `business_key` (the
engine's own `ESC-{tenant}-{conversation_id}`, the case reference the human opens — see the
worker's own comment on the field), and three bounded vocabulary tokens
(`grupo_atendimento`/`severidade`/`prioridade`/`motivo_categoria`) validated against their
contractual/DMN domains BEFORE they are published. There is no payload column, no free text, no
`resumo_contexto`, and no beneficiary reference — the notification never carried one, and this
table deliberately does not grow a place to put one.

**Idempotency: the PRIMARY KEY, not a sweep.** `(tenant, notice_ref)` where `notice_ref` is a
one-way digest of the notification's own canonical content. A Kafka redelivery of the same
notification therefore lands on the same row and the insert is a no-op — which is what lets the
consumer treat "row committed" as delivery and only THEN let the offset advance. Two DIFFERENT
notifications about the same escalation (a re-notify after a channel fallback, a second SLA
cycle) have different content, so they are different rows: the human sees both, which is the
non-adverse direction.

**Retention is deliberately absent.** No expiry, no purge, no TTL. Those values belong to the
DPO/security retention matrix, and this migration does not invent them.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE escalation_team_notice (
            tenant text NOT NULL,
            notice_ref text NOT NULL CHECK (notice_ref ~ '^[0-9a-f]{64}$'),
            business_key text NOT NULL CHECK (business_key <> ''),
            audience text NOT NULL DEFAULT 'staff' CHECK (audience = 'staff'),
            grupo_atendimento text NOT NULL CHECK (grupo_atendimento <> ''),
            severidade text,
            prioridade text CHECK (prioridade IN ('P1','P2','P3')),
            motivo_categoria text,
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant, notice_ref)
        )
    """)
    # The read a staff queue actually performs: "what is waiting for MY group, newest first".
    op.execute(
        "CREATE INDEX escalation_team_notice_queue ON escalation_team_notice "
        "(tenant, grupo_atendimento, recorded_at DESC)"
    )
    # A notice is an immutable record of what was published. Correcting one means publishing a
    # new notification (a new `notice_ref`), never rewriting the line a human may already have
    # read and acted on.
    op.execute("""
        CREATE FUNCTION escalation_team_notice_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'immutable escalation team notice';
        END $$
    """)
    op.execute(
        "CREATE TRIGGER escalation_team_notice_immutable BEFORE UPDATE OR DELETE ON "
        "escalation_team_notice FOR EACH ROW EXECUTE FUNCTION escalation_team_notice_immutable()"
    )
    op.execute(
        "CREATE TRIGGER escalation_team_notice_no_truncate BEFORE TRUNCATE ON "
        "escalation_team_notice FOR EACH STATEMENT EXECUTE FUNCTION "
        "escalation_team_notice_immutable()"
    )
    op.execute("REVOKE ALL ON TABLE escalation_team_notice FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION escalation_team_notice_immutable() FROM PUBLIC")


def downgrade() -> None:
    # Destructive downgrade is an explicit migration/operator action, never a runtime purge.
    op.execute("DROP TABLE escalation_team_notice")
    op.execute("DROP FUNCTION escalation_team_notice_immutable()")
