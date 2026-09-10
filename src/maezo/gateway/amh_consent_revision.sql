-- Reviewed deployment input, never executed automatically by gateway construction.
-- The deployment owner supplies PHI-zone placement, migration ownership and a
-- dedicated runtime role with SELECT/INSERT/UPDATE on revision_guard; scoped DELETE on pending_observation. No runtime DELETE on revision_guard, TRUNCATE
-- or DDL privilege; backup/restore must not roll back the observed revision floor.
CREATE SCHEMA IF NOT EXISTS maezo_amh_consent;
REVOKE ALL ON SCHEMA maezo_amh_consent FROM PUBLIC;
CREATE TABLE maezo_amh_consent.revision_guard (
    domain_digest TEXT NOT NULL CHECK (domain_digest ~ '^[0-9a-f]{64}$'),
    subject_purpose_digest TEXT NOT NULL CHECK (subject_purpose_digest ~ '^[0-9a-f]{64}$'),
    revision NUMERIC NOT NULL CHECK (revision >= 0 AND revision = trunc(revision)
        AND revision::text NOT IN ('NaN', 'Infinity', '-Infinity')),
    decision_digest TEXT NOT NULL CHECK (decision_digest ~ '^[0-9a-f]{64}$'),
    conflicted BOOLEAN NOT NULL,
    PRIMARY KEY (domain_digest, subject_purpose_digest)
);
REVOKE ALL ON maezo_amh_consent.revision_guard FROM PUBLIC;

-- Intent is committed before source IO. Do not expire, replace or remove pending
-- rows on restart. The only normal deletion is atomic with the corresponding
-- observed floor in the guard's owned-token completion transaction. Recovery
-- after losing the observed value requires qualified source reconciliation;
-- arbitrary DBA deletion is not a supported recovery operation.
CREATE TABLE maezo_amh_consent.pending_observation (
    domain_digest TEXT NOT NULL CHECK (domain_digest ~ '^[0-9a-f]{64}$'),
    subject_purpose_digest TEXT NOT NULL CHECK (subject_purpose_digest ~ '^[0-9a-f]{64}$'),
    observation_token TEXT NOT NULL CHECK (observation_token ~ '^[0-9a-f]{32}$'),
    PRIMARY KEY (domain_digest, subject_purpose_digest)
);
REVOKE ALL ON maezo_amh_consent.pending_observation FROM PUBLIC;
