-- Dedicated protected intake database, explicitly installed by its owner.
-- No runtime DDL, retention policy, general audit payload or engine table mutation.
CREATE SCHEMA portal_intake;
REVOKE ALL ON SCHEMA portal_intake FROM PUBLIC;
CREATE TABLE portal_intake.intake (
 tenant text NOT NULL, principal_ref text NOT NULL,
 command_id text NOT NULL, intake_ref text NOT NULL,
 guide_identity_ref text NOT NULL, request_digest char(64) NOT NULL,
 authority_receipt_ref text NOT NULL, authority_digest char(64) NOT NULL,
 key_id text NOT NULL, nonce bytea NOT NULL CHECK (octet_length(nonce)=12),
 ciphertext bytea NOT NULL,
 disposition text NOT NULL CHECK (disposition IN ('admitted','dispatching','reconciling','started','rejected')),
 revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
 case_ref text, start_receipt_ref text,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY (tenant, principal_ref, command_id),
 UNIQUE (tenant, intake_ref), UNIQUE (tenant, guide_identity_ref),
 CHECK ((disposition='started' AND case_ref IS NOT NULL AND start_receipt_ref IS NOT NULL)
     OR (disposition<>'started' AND case_ref IS NULL AND start_receipt_ref IS NULL))
);
REVOKE ALL ON portal_intake.intake FROM PUBLIC;
CREATE TABLE portal_intake.admission_event (
 tenant text NOT NULL, intake_ref text NOT NULL, command_id text NOT NULL,
 principal_ref text NOT NULL, request_digest char(64) NOT NULL,
 authority_receipt_ref text NOT NULL, authority_digest char(64) NOT NULL,
 admitted_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY (tenant,intake_ref),
 FOREIGN KEY (tenant,intake_ref) REFERENCES portal_intake.intake(tenant,intake_ref)
);
REVOKE ALL ON portal_intake.admission_event FROM PUBLIC;
