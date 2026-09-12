-- ADR0049 D3 / ADR0006: install ONLY on the dedicated PHI database as its owner.
-- No runtime installer, General migration, TTL, deletion schedule or clinical rule.
CREATE SCHEMA maezo_phi_decision;
REVOKE ALL ON SCHEMA maezo_phi_decision FROM PUBLIC;
CREATE TABLE maezo_phi_decision.human_decision (
    tenant text NOT NULL,
    environment text NOT NULL,
    workload_ref text NOT NULL,
    task_id text NOT NULL,
    command_id text NOT NULL,
    principal_ref text NOT NULL,
    request_digest text NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    content_digest text NOT NULL CHECK (content_digest ~ '^[0-9a-f]{64}$'),
    custody_ref text NOT NULL UNIQUE CHECK (custody_ref ~ '^phi-decision:[0-9a-f-]{36}$'),
    key_id text NOT NULL,
    nonce bytea NOT NULL CHECK (octet_length(nonce)=12),
    ciphertext bytea NOT NULL CHECK (octet_length(ciphertext)>16),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant, environment, workload_ref, task_id, command_id)
);
REVOKE ALL ON maezo_phi_decision.human_decision FROM PUBLIC;
CREATE FUNCTION maezo_phi_decision.immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable PHI decision custody';
END;
$$;
REVOKE ALL ON FUNCTION maezo_phi_decision.immutable() FROM PUBLIC;
CREATE TRIGGER human_decision_immutable BEFORE UPDATE OR DELETE
ON maezo_phi_decision.human_decision FOR EACH ROW
EXECUTE FUNCTION maezo_phi_decision.immutable();
CREATE TRIGGER human_decision_no_truncate BEFORE TRUNCATE
ON maezo_phi_decision.human_decision FOR EACH STATEMENT
EXECUTE FUNCTION maezo_phi_decision.immutable();
