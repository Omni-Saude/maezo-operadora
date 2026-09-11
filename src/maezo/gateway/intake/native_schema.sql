-- Owner-installed gateway schema extension. Separate from native engine tables.
-- Protected role/key/TLS grants require independent qualification; no runtime DDL.
CREATE TABLE portal_intake.native_outbox (
 tenant text NOT NULL, command_id text NOT NULL, admission_ref text NOT NULL,
 resource_ref text NOT NULL, operation text NOT NULL CHECK(operation IN ('auth.start','auth.documents.respond')),
 principal_ref text NOT NULL, admitted_digest char(64) NOT NULL,
 authorization_until timestamptz NOT NULL,
 command_digest char(64), command_ciphertext bytea, command_nonce bytea,
 key_id text NOT NULL,
 state text NOT NULL DEFAULT 'admitted' CHECK(state IN ('admitted','claimed','sending','reconciling','executed','rejected')),
 generation bigint NOT NULL DEFAULT 0 CHECK(generation>=0),
 revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),
 owner_digest char(64), lease_until timestamptz,
 receipt_ciphertext bytea, receipt_nonce bytea, receipt_digest char(64),
 PRIMARY KEY(tenant,command_id), UNIQUE(tenant,admission_ref),
 CHECK((command_digest IS NULL AND command_ciphertext IS NULL AND command_nonce IS NULL)
    OR(command_digest IS NOT NULL AND command_ciphertext IS NOT NULL AND octet_length(command_nonce)=12)),
 CHECK((state='executed')=(receipt_digest IS NOT NULL)),
 CHECK(state NOT IN ('claimed','sending','reconciling','executed') OR command_digest IS NOT NULL)
);
CREATE TABLE portal_intake.native_response (
 tenant text NOT NULL, response_ref text NOT NULL, command_id text NOT NULL,
 case_ref text NOT NULL, request_ref text NOT NULL, request_revision bigint NOT NULL,
 principal_ref text NOT NULL, admitted_digest char(64) NOT NULL,
 key_id text NOT NULL, nonce bytea NOT NULL CHECK(octet_length(nonce)=12), ciphertext bytea NOT NULL,
 admitted_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant,response_ref), UNIQUE(tenant,principal_ref,command_id),
 FOREIGN KEY(tenant,command_id) REFERENCES portal_intake.native_outbox(tenant,command_id)
    DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE portal_intake.native_audit (
 tenant text NOT NULL, command_id text NOT NULL, event_kind text NOT NULL,
 admission_ref text NOT NULL, digest char(64) NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant,command_id,event_kind),
 FOREIGN KEY(tenant,command_id) REFERENCES portal_intake.native_outbox(tenant,command_id)
    DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE portal_intake.native_case_binding (
 tenant text NOT NULL, case_ref text NOT NULL, intake_ref text NOT NULL,
 command_id text NOT NULL, receipt_ref text NOT NULL, receipt_digest char(64) NOT NULL,
 process_instance_id text NOT NULL, definition_id text NOT NULL,
 PRIMARY KEY(tenant,case_ref), UNIQUE(tenant,intake_ref), UNIQUE(tenant,command_id),
 FOREIGN KEY(tenant,command_id) REFERENCES portal_intake.native_outbox(tenant,command_id)
);
CREATE TABLE portal_intake.native_publication (
 tenant text NOT NULL, publication_id text NOT NULL, kind text NOT NULL, resource_ref text NOT NULL,
 request_digest char(64) NOT NULL, key_id text NOT NULL,
 nonce bytea NOT NULL CHECK(octet_length(nonce)=12), ciphertext bytea NOT NULL,
 state text NOT NULL CHECK(state IN ('frozen','acknowledged')),
 receipt_digest char(64), receipt_nonce bytea, receipt_ciphertext bytea,
 PRIMARY KEY(tenant,publication_id),
 CHECK((state='acknowledged')=(receipt_digest IS NOT NULL))
);
REVOKE ALL ON ALL TABLES IN SCHEMA portal_intake FROM PUBLIC;
CREATE TABLE portal_intake.native_identity (
 tenant text NOT NULL, command_id text NOT NULL, key_id text NOT NULL,
 nonce bytea NOT NULL CHECK(octet_length(nonce)=12), ciphertext bytea NOT NULL,
 PRIMARY KEY(tenant,command_id),
 FOREIGN KEY(tenant,command_id) REFERENCES portal_intake.native_outbox(tenant,command_id)
    DEFERRABLE INITIALLY DEFERRED
);
REVOKE ALL ON portal_intake.native_identity FROM PUBLIC;
CREATE UNIQUE INDEX native_publication_one_frozen ON portal_intake.native_publication(tenant,kind,resource_ref) WHERE state='frozen';
