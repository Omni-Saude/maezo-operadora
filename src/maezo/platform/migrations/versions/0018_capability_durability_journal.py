"""Forward technical capability journal, DUR0 contract and DL-0017.

Six tenant-local tables; no source authority, runtime grants, retention policy or
activation is created. DDL is frozen here so future runtime schema changes cannot
rewrite migration history. Both directions reject public/system schema fallback.
Downgrade refuses populated journals; lifecycle/erasure belongs to qualified owners.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _require_tenant_schema() -> None:
    op.execute("""DO $$
    BEGIN
        IF current_schema() IS NULL OR current_schema() IN
           ('public', 'pg_catalog', 'information_schema') THEN
            RAISE EXCEPTION 'capability journal requires explicit tenant schema';
        END IF;
    END $$""")


def upgrade() -> None:
    _require_tenant_schema()
    op.execute("""-- DUR0 technical metadata only. ROOT owns forward migration and tenant-role grants.
-- Apply in the explicitly selected tenant schema; never public or human/A2A tables.
CREATE TABLE v21_journey_journal (
    environment_ref text NOT NULL,
    tenant_ref text NOT NULL,
    legal_entity_ref text NOT NULL,
    journey_ref text NOT NULL,
    principal_ref text NOT NULL,
    task_ref text NOT NULL,
    binding jsonb NOT NULL CHECK (jsonb_typeof(binding) = 'object'),
    journal_revision bigint NOT NULL DEFAULT 0 CHECK (journal_revision >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (environment_ref,tenant_ref,legal_entity_ref,journey_ref,principal_ref,task_ref)
);""")
    op.execute("""CREATE TABLE v21_capability_command (
    environment_ref text NOT NULL,
    tenant_ref text NOT NULL,
    legal_entity_ref text NOT NULL,
    journey_ref text NOT NULL,
    principal_ref text NOT NULL,
    task_ref text NOT NULL,
    command_ref text NOT NULL,
    operation_name text NOT NULL,
    idempotency_key text NOT NULL,
    descriptor jsonb NOT NULL CHECK (jsonb_typeof(descriptor) = 'object'),
    snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    dispatch_evidence jsonb CHECK (dispatch_evidence IS NULL OR jsonb_typeof(dispatch_evidence) = 'object'),
    pre_dispatch_refusal jsonb CHECK (pre_dispatch_refusal IS NULL OR
        jsonb_typeof(pre_dispatch_refusal) = 'object'),
    head_ref text,
    PRIMARY KEY (environment_ref,tenant_ref,legal_entity_ref,command_ref),
    UNIQUE (environment_ref,tenant_ref,legal_entity_ref,operation_name,idempotency_key),
    UNIQUE (environment_ref,tenant_ref,legal_entity_ref,journey_ref,principal_ref,task_ref,command_ref),
    FOREIGN KEY (environment_ref,tenant_ref,legal_entity_ref,journey_ref,principal_ref,task_ref)
        REFERENCES v21_journey_journal,
    CHECK (snapshot->>'technical_state' IN
        ('RECORDED','DISPATCH_FENCED','UNCERTAIN','RESPONSE_RECORDED','REFUSED_BEFORE_DISPATCH')),
    CHECK (descriptor->'envelope'->>'operation_name' = operation_name),
    CHECK (descriptor->'envelope'->>'idempotency_key' = idempotency_key),
    CHECK (descriptor->'envelope'->>'tenant_ref' = tenant_ref),
    CHECK (descriptor->'envelope'->>'legal_entity_ref' = legal_entity_ref),
    CHECK (descriptor->'envelope'->>'journey_ref' = journey_ref)
);""")
    op.execute("""CREATE TABLE v21_journal_observation (
    environment_ref text NOT NULL,
    tenant_ref text NOT NULL,
    legal_entity_ref text NOT NULL,
    command_ref text NOT NULL,
    observation_ref text NOT NULL,
    observation jsonb NOT NULL CHECK (jsonb_typeof(observation) = 'object'),
    causal_intents jsonb NOT NULL CHECK (jsonb_typeof(causal_intents) = 'object'),
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (environment_ref,tenant_ref,legal_entity_ref,command_ref,observation_ref),
    FOREIGN KEY (environment_ref,tenant_ref,legal_entity_ref,command_ref)
        REFERENCES v21_capability_command
);""")
    op.execute("""ALTER TABLE v21_capability_command ADD CONSTRAINT v21_command_head_fk
    FOREIGN KEY (environment_ref,tenant_ref,legal_entity_ref,command_ref,head_ref)
    REFERENCES v21_journal_observation
    (environment_ref,tenant_ref,legal_entity_ref,command_ref,observation_ref);""")
    op.execute("""CREATE TABLE v21_journal_inbox (
    environment_ref text NOT NULL,
    tenant_ref text NOT NULL,
    legal_entity_ref text NOT NULL,
    source_authority_ref text NOT NULL,
    producer_ref text NOT NULL,
    source_contract_revision_ref text NOT NULL,
    event_ref text NOT NULL,
    command_ref text NOT NULL,
    observation jsonb NOT NULL CHECK (jsonb_typeof(observation) = 'object'),
    causal_intents jsonb NOT NULL CHECK (jsonb_typeof(causal_intents) = 'object'),
    applied_journal_revision bigint NOT NULL CHECK (applied_journal_revision >= 0),
    PRIMARY KEY (environment_ref,tenant_ref,legal_entity_ref,source_authority_ref,
        producer_ref,source_contract_revision_ref,event_ref),
    FOREIGN KEY (environment_ref,tenant_ref,legal_entity_ref,command_ref)
        REFERENCES v21_capability_command
);""")
    op.execute("""CREATE TABLE v21_external_wait (
    environment_ref text NOT NULL,
    tenant_ref text NOT NULL,
    legal_entity_ref text NOT NULL,
    journey_ref text NOT NULL,
    principal_ref text NOT NULL,
    task_ref text NOT NULL,
    wait_ref text NOT NULL,
    command_ref text NOT NULL,
    descriptor jsonb NOT NULL CHECK (jsonb_typeof(descriptor) = 'object'),
    snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    PRIMARY KEY (environment_ref,tenant_ref,legal_entity_ref,journey_ref,task_ref,wait_ref),
    FOREIGN KEY (environment_ref,tenant_ref,legal_entity_ref,journey_ref,principal_ref,task_ref)
        REFERENCES v21_journey_journal,
    FOREIGN KEY (environment_ref,tenant_ref,legal_entity_ref,command_ref)
        REFERENCES v21_capability_command,
    CHECK (snapshot->>'technical_state' IN ('OPEN','ELAPSED','OBSERVATION_RECORDED'))
);""")
    op.execute("""CREATE TABLE v21_journal_outbox (
    environment_ref text NOT NULL,
    tenant_ref text NOT NULL,
    legal_entity_ref text NOT NULL,
    journey_ref text NOT NULL,
    principal_ref text NOT NULL,
    task_ref text NOT NULL,
    outbox_ref text NOT NULL,
    command_ref text NOT NULL,
    descriptor jsonb NOT NULL CHECK (jsonb_typeof(descriptor) = 'object'),
    snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    PRIMARY KEY (environment_ref,tenant_ref,legal_entity_ref,outbox_ref),
    FOREIGN KEY (environment_ref,tenant_ref,legal_entity_ref,journey_ref,principal_ref,task_ref)
        REFERENCES v21_journey_journal,
    FOREIGN KEY (environment_ref,tenant_ref,legal_entity_ref,journey_ref,principal_ref,task_ref,command_ref)
        REFERENCES v21_capability_command
        (environment_ref,tenant_ref,legal_entity_ref,journey_ref,principal_ref,task_ref,command_ref),
    CHECK ((snapshot->'descriptor') IS NOT DISTINCT FROM descriptor),
    CHECK ((descriptor->>'outbox_ref') IS NOT DISTINCT FROM outbox_ref),
    CHECK ((descriptor->>'command_ref') IS NOT DISTINCT FROM command_ref),
    CHECK (snapshot->>'technical_state' IN
        ('RECORDED','CLAIMED','DELIVERY_FENCED','UNCERTAIN','ACK_RECORDED'))
);""")
    op.execute("""CREATE UNIQUE INDEX v21_outbox_dedupe ON v21_journal_outbox
    (environment_ref,tenant_ref,legal_entity_ref,command_ref,
     (descriptor->>'kind'),(descriptor->>'target_binding_ref'),(descriptor->>'dedupe_identity_ref'));""")
    op.execute("""-- No retention duration, permanent tombstone or erase grant is chosen here.
CREATE FUNCTION v21_journal_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable journal evidence';
END $$;""")
    op.execute("""CREATE TRIGGER v21_observation_immutable BEFORE UPDATE ON v21_journal_observation
    FOR EACH ROW EXECUTE FUNCTION v21_journal_immutable();""")
    op.execute("""CREATE TRIGGER v21_inbox_immutable BEFORE UPDATE ON v21_journal_inbox
    FOR EACH ROW EXECUTE FUNCTION v21_journal_immutable();""")
    op.execute("""CREATE FUNCTION v21_command_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE old_state text := OLD.snapshot->>'technical_state';
        new_state text := NEW.snapshot->>'technical_state';
BEGIN
    IF (NEW.environment_ref,NEW.tenant_ref,NEW.legal_entity_ref,NEW.journey_ref,
        NEW.principal_ref,NEW.task_ref,NEW.command_ref,NEW.operation_name,NEW.idempotency_key,
        NEW.descriptor,NEW.snapshot->'handle',NEW.snapshot->'recorded_at') IS DISTINCT FROM
       (OLD.environment_ref,OLD.tenant_ref,OLD.legal_entity_ref,OLD.journey_ref,
        OLD.principal_ref,OLD.task_ref,OLD.command_ref,OLD.operation_name,OLD.idempotency_key,
        OLD.descriptor,OLD.snapshot->'handle',OLD.snapshot->'recorded_at')
    THEN RAISE EXCEPTION 'immutable command identity'; END IF;
    IF NOT ((old_state='RECORDED' AND new_state IN ('DISPATCH_FENCED','REFUSED_BEFORE_DISPATCH'))
        OR (old_state='DISPATCH_FENCED' AND new_state IN ('UNCERTAIN','RESPONSE_RECORDED'))
        OR (old_state IN ('UNCERTAIN','RESPONSE_RECORDED') AND new_state='RESPONSE_RECORDED'))
    THEN RAISE EXCEPTION 'invalid command transition'; END IF;
    IF old_state <> 'RECORDED' AND
       (NEW.snapshot->>'dispatch_ref',NEW.snapshot->>'fence_version',NEW.dispatch_evidence) IS DISTINCT FROM
       (OLD.snapshot->>'dispatch_ref',OLD.snapshot->>'fence_version',OLD.dispatch_evidence)
    THEN RAISE EXCEPTION 'immutable dispatch fence'; END IF;
    RETURN NEW;
END $$;""")
    op.execute("""CREATE TRIGGER v21_command_transition BEFORE UPDATE ON v21_capability_command
    FOR EACH ROW EXECUTE FUNCTION v21_command_guard();""")
    op.execute("""CREATE FUNCTION v21_outbox_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE old_state text := OLD.snapshot->>'technical_state';
        new_state text := NEW.snapshot->>'technical_state';
BEGIN
    IF (NEW.environment_ref,NEW.tenant_ref,NEW.legal_entity_ref,NEW.journey_ref,
        NEW.principal_ref,NEW.task_ref,NEW.outbox_ref,NEW.command_ref,NEW.descriptor,
        NEW.snapshot->'descriptor') IS DISTINCT FROM
       (OLD.environment_ref,OLD.tenant_ref,OLD.legal_entity_ref,OLD.journey_ref,
        OLD.principal_ref,OLD.task_ref,OLD.outbox_ref,OLD.command_ref,OLD.descriptor,
        OLD.snapshot->'descriptor')
    THEN RAISE EXCEPTION 'immutable outbox identity'; END IF;
    IF NOT ((old_state='RECORDED' AND new_state='CLAIMED') OR
        (old_state='CLAIMED' AND new_state IN ('CLAIMED','DELIVERY_FENCED')) OR
        (old_state='DELIVERY_FENCED' AND new_state IN ('UNCERTAIN','ACK_RECORDED')) OR
        (old_state='UNCERTAIN' AND new_state='ACK_RECORDED'))
    THEN RAISE EXCEPTION 'invalid outbox transition'; END IF;
    IF old_state IN ('DELIVERY_FENCED','UNCERTAIN','ACK_RECORDED') AND
       (NEW.snapshot->>'claim_ref',NEW.snapshot->>'worker_ref',NEW.snapshot->>'fence_version',
        NEW.snapshot->>'lease_until',NEW.snapshot->>'delivery_ref') IS DISTINCT FROM
       (OLD.snapshot->>'claim_ref',OLD.snapshot->>'worker_ref',OLD.snapshot->>'fence_version',
        OLD.snapshot->>'lease_until',OLD.snapshot->>'delivery_ref') THEN
        RAISE EXCEPTION 'immutable delivery fence'; END IF;
    RETURN NEW;
END $$;""")
    op.execute("""CREATE TRIGGER v21_outbox_transition BEFORE UPDATE ON v21_journal_outbox
    FOR EACH ROW EXECUTE FUNCTION v21_outbox_guard();""")
    op.execute("REVOKE ALL ON TABLE v21_journey_journal FROM PUBLIC")
    op.execute("REVOKE ALL ON TABLE v21_capability_command FROM PUBLIC")
    op.execute("REVOKE ALL ON TABLE v21_journal_observation FROM PUBLIC")
    op.execute("REVOKE ALL ON TABLE v21_journal_inbox FROM PUBLIC")
    op.execute("REVOKE ALL ON TABLE v21_external_wait FROM PUBLIC")
    op.execute("REVOKE ALL ON TABLE v21_journal_outbox FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION v21_journal_immutable() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION v21_command_guard() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION v21_outbox_guard() FROM PUBLIC")


def downgrade() -> None:
    _require_tenant_schema()
    op.execute("""DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM v21_journey_journal)
           OR EXISTS (SELECT 1 FROM v21_capability_command)
           OR EXISTS (SELECT 1 FROM v21_journal_observation)
           OR EXISTS (SELECT 1 FROM v21_journal_inbox)
           OR EXISTS (SELECT 1 FROM v21_external_wait)
           OR EXISTS (SELECT 1 FROM v21_journal_outbox) THEN
            RAISE EXCEPTION 'populated capability journal requires qualified lifecycle disposition';
        END IF;
    END $$""")
    op.execute("ALTER TABLE v21_capability_command DROP CONSTRAINT v21_command_head_fk")
    op.execute("DROP TABLE v21_journal_outbox")
    op.execute("DROP TABLE v21_external_wait")
    op.execute("DROP TABLE v21_journal_inbox")
    op.execute("DROP TABLE v21_journal_observation")
    op.execute("DROP TABLE v21_capability_command")
    op.execute("DROP TABLE v21_journey_journal")
    op.execute("DROP FUNCTION v21_outbox_guard()")
    op.execute("DROP FUNCTION v21_command_guard()")
    op.execute("DROP FUNCTION v21_journal_immutable()")
