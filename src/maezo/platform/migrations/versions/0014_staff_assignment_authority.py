"""E03 complete staff authority source and exact durable publication delivery.

No identities, policies, or trust are seeded. The deployment owner grants source writes
only to the reviewed administration workload and read-only state to the BFF.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""CREATE TABLE portal_assignment_source (
        tenant text PRIMARY KEY, source_revision bigint NOT NULL CHECK(source_revision >= 0),
        state text NOT NULL CHECK(state IN ('disabled','frozen','active')),
        active_generation_digest text, pending_publication_id text,
        native_revision bigint NOT NULL CHECK(native_revision >= 0),
        designation_bytes bytea NOT NULL, source_bytes bytea,
        owner_receipt bytea, committed_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )""")
    op.execute("""CREATE TABLE portal_assignment_publications (
        tenant text NOT NULL REFERENCES portal_assignment_source(tenant), publication_id text NOT NULL,
        source_revision bigint NOT NULL CHECK(source_revision >= 0),
        operation text NOT NULL CHECK(operation IN ('disable','replace','receipt')),
        source_bytes bytea NOT NULL, source_digest text NOT NULL, owner_receipt bytea NOT NULL,
        request_bytes bytea NOT NULL, expected_native_revision bigint NOT NULL,
        native_receipt bytea, delivery_state text NOT NULL
        CHECK(delivery_state IN ('prepared','uncertain','acknowledged')),
        committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(tenant,publication_id), UNIQUE(tenant,source_revision,operation)
    )""")
    op.execute("""CREATE TABLE portal_assignment_receipt_source (
        tenant text NOT NULL REFERENCES portal_assignment_source(tenant), task_id text NOT NULL,
        command_id text NOT NULL, identity_digest text NOT NULL, source_revision bigint NOT NULL,
        state text NOT NULL CHECK(state IN ('frozen','active','revoked')),
        pending_publication_id text NOT NULL, source_bytes bytea NOT NULL, owner_receipt bytea NOT NULL,
        expected_native_revision bigint NOT NULL, committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(tenant,task_id,command_id), UNIQUE(tenant,source_revision)
    )""")
    op.execute("""CREATE FUNCTION portal_assignment_publication_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF TG_OP = 'DELETE' OR ROW(NEW.tenant,NEW.publication_id,NEW.source_revision,NEW.operation,
              NEW.source_bytes,NEW.source_digest,NEW.owner_receipt,NEW.request_bytes,
              NEW.expected_native_revision,NEW.committed_at)
            IS DISTINCT FROM ROW(OLD.tenant,OLD.publication_id,OLD.source_revision,OLD.operation,
              OLD.source_bytes,OLD.source_digest,OLD.owner_receipt,OLD.request_bytes,OLD.expected_native_revision,OLD.committed_at)
            OR (OLD.native_receipt IS NOT NULL AND NEW.native_receipt IS DISTINCT FROM OLD.native_receipt)
            OR (OLD.delivery_state='acknowledged' AND NEW.delivery_state <> 'acknowledged')
          THEN RAISE EXCEPTION 'immutable assignment publication'; END IF;
          RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER portal_assignment_publication_immutable BEFORE UPDATE OR DELETE
        ON portal_assignment_publications FOR EACH ROW
        EXECUTE FUNCTION portal_assignment_publication_immutable()""")
    for table in (
        "portal_assignment_source",
        "portal_assignment_publications",
        "portal_assignment_receipt_source",
    ):
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION portal_assignment_publication_immutable() FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TABLE portal_assignment_receipt_source")
    op.execute("DROP TABLE portal_assignment_publications")
    op.execute("DROP FUNCTION portal_assignment_publication_immutable()")
    op.execute("DROP TABLE portal_assignment_source")
