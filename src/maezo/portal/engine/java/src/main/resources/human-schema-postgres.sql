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
