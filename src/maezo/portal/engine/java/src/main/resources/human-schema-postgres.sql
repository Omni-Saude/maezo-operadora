-- ADR0049 D5. Apply to the SAME schema/database as ACT_* with the deployment owner.
-- The runtime role needs SELECT/INSERT/UPDATE only; DELETE and schema DDL are not granted.
-- No FK to ACT_RU_TASK: receipts/evidence retain identity after task deletion.
CREATE TABLE MZO_HUMAN_TENANT (
  TENANT_ varchar(255) PRIMARY KEY, REV_ bigint NOT NULL CHECK (REV_ >= 0)
);
CREATE TABLE MZO_HUMAN_PRINCIPAL (
  TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_),
  PRINCIPAL_ varchar(255) NOT NULL, ISSUER_ text NOT NULL, SUBJECT_ varchar(255) NOT NULL,
  REV_ bigint NOT NULL, ACTIVE_ boolean NOT NULL, VALID_UNTIL_ bigint NOT NULL,
  GROUPS_ text NOT NULL,
  PRIMARY KEY(TENANT_, PRINCIPAL_), UNIQUE(TENANT_, ISSUER_, SUBJECT_)
);
CREATE TABLE MZO_HUMAN_EVIDENCE (
  TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_), TASK_ varchar(255) NOT NULL,
  REV_ bigint NOT NULL, REF_ varchar(255) NOT NULL, DIGEST_ char(64) NOT NULL,
  VALID_UNTIL_ bigint NOT NULL, PROCESS_ varchar(255) NOT NULL,
  PRIMARY KEY(TENANT_, TASK_)
);
CREATE TABLE MZO_HUMAN_REVOKED_KEY (
  TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_),
  FINGERPRINT_ char(64) NOT NULL, REV_ bigint NOT NULL,
  PRIMARY KEY(TENANT_, FINGERPRINT_)
);
CREATE TABLE MZO_HUMAN_RECEIPT (
  TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_), TASK_ varchar(255) NOT NULL,
  COMMAND_ varchar(255) NOT NULL, DIGEST_ char(64) NOT NULL, PRINCIPAL_ varchar(255) NOT NULL,
  WORKLOAD_ varchar(255) NOT NULL, RECEIPT_ text NOT NULL,
  PRIMARY KEY(TENANT_, TASK_, COMMAND_)
);
-- Bootstrap is deliberately explicit, outside runtime; bind the actual configured tenant:
-- INSERT INTO MZO_HUMAN_TENANT(TENANT_,REV_) VALUES (<trusted tenant>,0);
-- Production retention/deletion follows the separately approved DPO matrix, not a default TTL.

-- D3/D5 version-specific classification + PHI consumer qualification. EMPTY by default.
-- This is a deployment-owner projection, never populated by command/browser/signing code.
-- Runtime principal must have SELECT ONLY on this table; installation principal owns writes.
-- Owner installation/readback contract (one enlisted transaction):
-- 1. Lock MZO_HUMAN_TENANT FOR UPDATE; authenticate independently reviewed classification,
--    PHI custody + actual worker hydration contract and deployed consumer digest.
-- 2. Compare exact deployed process bytes/id/version, form bytes/version and candidate role.
-- 3. Increment tenant REV_; install exact immutable pins and the NEW authority revision.
--    Retain previous rows immutably. Revocation follows the SAME tenant lock and revision
--    bump WITHOUT a new qualifying row; never mutate or delete earlier pinned evidence.
-- 4. Read back the entire row, verify exact equality and runtime SELECT-only table grants;
--    commit before publishing the same qualified binding to the gateway.
-- Absence, drift, stale revision/expiry or unqualified consumer MUST prevent installation.
-- This file provides no fabricated row, default expiry, human signature or activation.
CREATE TABLE MZO_HUMAN_DECISION_BINDING (
  TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_),
  ENVIRONMENT_ varchar(255) NOT NULL, PROCESS_ varchar(255) NOT NULL,
  PROCESS_KEY_ varchar(255) NOT NULL, PROCESS_VERSION_ varchar(255) NOT NULL,
  PROCESS_DIGEST_ char(64) NOT NULL, TASK_KEY_ varchar(255) NOT NULL,
  FORM_KEY_ varchar(255) NOT NULL, FORM_VERSION_ varchar(255) NOT NULL, FORM_DIGEST_ char(64) NOT NULL,
  BINDING_DIGEST_ char(64) NOT NULL, CONSUMER_DIGEST_ char(64) NOT NULL,
  INPUT_KIND_ varchar(255) NOT NULL, REQUIRED_GROUP_ varchar(255) NOT NULL,
  WORKLOAD_ varchar(255) NOT NULL, AUTHORITY_REV_ bigint NOT NULL CHECK (AUTHORITY_REV_ >= 0),
  ACTIVE_ boolean NOT NULL, VALID_UNTIL_ bigint NOT NULL,
  PRIMARY KEY (TENANT_, ENVIRONMENT_, PROCESS_, TASK_KEY_, AUTHORITY_REV_)
);
