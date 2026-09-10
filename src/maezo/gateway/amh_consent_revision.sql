-- Reviewed deployment input, never executed automatically by gateway construction.
-- The deployment owner supplies PHI-zone placement, migration ownership and a
-- dedicated runtime role with SELECT/INSERT/UPDATE only. No runtime DELETE/TRUNCATE
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
