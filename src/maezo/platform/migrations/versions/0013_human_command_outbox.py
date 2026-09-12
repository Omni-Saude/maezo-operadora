"""ADR-0049 D6 dedicated human intent/outbox and fenced technical delivery.

No expiry, purge, backfill or reuse of A2A/WhatsApp state. Retention requires the
separate DPO/security matrix; this migration does not invent its values.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE human_command_outbox (
            tenant text NOT NULL,
            task_id text NOT NULL,
            command_id text NOT NULL,
            environment text NOT NULL,
            workload_ref text NOT NULL,
            principal_ref text NOT NULL,
            principal_issuer text NOT NULL,
            principal_subject text NOT NULL,
            payload_digest text NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
            canonical_payload bytea NOT NULL CHECK (octet_length(canonical_payload) <= 65536),
            audit_intent_ref text NOT NULL,
            audit_intent_hash text NOT NULL CHECK (audit_intent_hash ~ '^[0-9a-f]{64}$'),
            outbox_ref text NOT NULL UNIQUE,
            transaction_ref text NOT NULL,
            admitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant, task_id, command_id),
            UNIQUE (tenant, audit_intent_ref)
        )
    """)
    op.execute("""
        CREATE TABLE human_command_delivery (
            tenant text NOT NULL,
            task_id text NOT NULL,
            command_id text NOT NULL,
            status text NOT NULL DEFAULT 'pending' CHECK (status IN
            ('pending','committed','conflict')),
            lease_id text,
            lease_until timestamptz,
            fence bigint NOT NULL DEFAULT 0 CHECK (fence >= 0),
            next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            engine_receipt bytea,
            technical_code text CHECK (technical_code IN
            ('REVISION_CONFLICT','COMMAND_CONFLICT','FORM_NOT_ACTIVATED')),
            audit_result_hash text CHECK (audit_result_hash ~ '^[0-9a-f]{64}$'),
            PRIMARY KEY (tenant, task_id, command_id),
            FOREIGN KEY (tenant,task_id,command_id) REFERENCES
            human_command_outbox(tenant,task_id,command_id),
            CHECK ((lease_id IS NULL) = (lease_until IS NULL)),
            CHECK (
                (status='pending' AND engine_receipt IS NULL AND audit_result_hash IS NULL AND
                technical_code IS NULL)
                OR (status='committed' AND engine_receipt IS NOT NULL AND audit_result_hash IS NOT
                NULL AND technical_code IS NULL AND lease_id IS NULL)
                OR (status='conflict' AND engine_receipt IS NULL AND audit_result_hash IS NOT NULL
                AND technical_code IS NOT NULL AND lease_id IS NULL)
            )
        )
    """)
    op.execute(
        "CREATE INDEX human_command_pending ON human_command_delivery "
        "(tenant,next_attempt_at) WHERE status='pending'"
    )
    op.execute("""
        CREATE FUNCTION human_command_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'immutable human command';
        END $$
    """)
    op.execute(
        "CREATE TRIGGER human_command_outbox_immutable BEFORE UPDATE OR DELETE ON "
        "human_command_outbox FOR EACH ROW EXECUTE FUNCTION "
        "human_command_immutable()"
    )
    op.execute("""
        CREATE FUNCTION human_delivery_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'immutable human command delivery'; END IF;
            IF OLD.status <> 'pending' OR (NEW.tenant,NEW.task_id,NEW.command_id) IS DISTINCT FROM
            (OLD.tenant,OLD.task_id,OLD.command_id) OR NEW.fence < OLD.fence THEN
                RAISE EXCEPTION 'immutable human command result';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER human_command_delivery_guard BEFORE UPDATE OR DELETE ON "
        "human_command_delivery FOR EACH ROW EXECUTE FUNCTION human_delivery_guard()"
    )
    for table in ("human_command_outbox", "human_command_delivery"):
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION human_command_immutable()"
        )
    for function in ("human_command_immutable", "human_delivery_guard"):
        op.execute(f"REVOKE ALL ON FUNCTION {function}() FROM PUBLIC")


def downgrade() -> None:
    # Destructive downgrade is an explicit migration/operator action, never a runtime purge.
    op.execute("DROP TABLE human_command_delivery")
    op.execute("DROP TABLE human_command_outbox")
    op.execute("DROP FUNCTION human_delivery_guard()")
    op.execute("DROP FUNCTION human_command_immutable()")
