-- Explicit DBA-installed PHI database schema. No runtime DDL or default roles.
CREATE SCHEMA portal_communication;
REVOKE ALL ON SCHEMA portal_communication FROM PUBLIC;
CREATE TABLE portal_communication.content (
 tenant text NOT NULL, environment text NOT NULL, case_ref text NOT NULL,
 sender_identity_digest char(64) NOT NULL, command_id text NOT NULL,
 body_ref text NOT NULL, request_digest char(64) NOT NULL,
 content_digest char(64) NOT NULL, key_id text NOT NULL,
 nonce bytea NOT NULL CHECK (octet_length(nonce)=12), ciphertext bytea NOT NULL,
 authority_receipt_ref text NOT NULL, authority_digest char(64) NOT NULL,
 authored_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant,environment,sender_identity_digest,command_id),
 UNIQUE(tenant,environment,body_ref),
 UNIQUE(tenant,environment,case_ref,body_ref)
);
CREATE TABLE portal_communication.message (
 tenant text NOT NULL, environment text NOT NULL, case_ref text NOT NULL,
 sender_identity_digest char(64) NOT NULL, sender_kind text NOT NULL
   CHECK(sender_kind IN ('staff','beneficiary','provider','system')),
 command_id text NOT NULL, communication_ref text NOT NULL,
 body_ref text NOT NULL, request_digest char(64) NOT NULL,
 recipient_set_ref text NOT NULL, recipients_digest char(64) NOT NULL,
 authority_receipt_ref text NOT NULL, authority_digest char(64) NOT NULL,
 sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 authored_at timestamptz NOT NULL, inbox_available_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant,environment,sender_identity_digest,command_id),
 UNIQUE(tenant,environment,communication_ref),
 UNIQUE(tenant,environment,body_ref),
 FOREIGN KEY(tenant,environment,case_ref,body_ref)
  REFERENCES portal_communication.content(tenant,environment,case_ref,body_ref)
);
CREATE TABLE portal_communication.intended_recipient (
 tenant text NOT NULL, environment text NOT NULL, communication_ref text NOT NULL,
 recipient_identity_digest char(64) NOT NULL,
 audience text NOT NULL CHECK(audience IN ('staff','beneficiary','provider')),
 source_revision text NOT NULL, policy_digest char(64) NOT NULL,
 PRIMARY KEY(tenant,environment,communication_ref,recipient_identity_digest),
 FOREIGN KEY(tenant,environment,communication_ref)
  REFERENCES portal_communication.message(tenant,environment,communication_ref)
);
CREATE TABLE portal_communication.inbox (
 tenant text NOT NULL, environment text NOT NULL, communication_ref text NOT NULL,
 recipient_identity_digest char(64) NOT NULL, available_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant,environment,communication_ref,recipient_identity_digest),
 FOREIGN KEY(tenant,environment,communication_ref,recipient_identity_digest)
  REFERENCES portal_communication.intended_recipient(tenant,environment,communication_ref,recipient_identity_digest)
);
CREATE TABLE portal_communication.history (
 tenant text NOT NULL, environment text NOT NULL, case_ref text NOT NULL,
 event_ref text NOT NULL, sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 kind text NOT NULL CHECK(kind IN ('communication_available','command_receipt_indexed')),
 communication_ref text, command_ref text, receipt_ref text, receipt_digest char(64),
 occurred_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(tenant,environment,event_ref),
 UNIQUE(tenant,environment,case_ref,kind,communication_ref),
 UNIQUE(tenant,environment,case_ref,kind,command_ref,receipt_ref),
 CHECK((kind='communication_available' AND communication_ref IS NOT NULL
    AND command_ref IS NULL AND receipt_ref IS NULL AND receipt_digest IS NULL)
   OR (kind='command_receipt_indexed' AND communication_ref IS NULL
    AND command_ref IS NOT NULL AND receipt_ref IS NOT NULL AND receipt_digest IS NOT NULL))
);
CREATE TABLE portal_communication.cursor (
 tenant text NOT NULL, environment text NOT NULL, cursor_ref text NOT NULL,
 case_ref text NOT NULL, identity_digest char(64) NOT NULL, membership_revision text NOT NULL,
 operation text NOT NULL CHECK(operation IN ('list_messages','list_history')),
 after_sequence bigint NOT NULL CHECK(after_sequence>=0), page_limit integer NOT NULL CHECK(page_limit BETWEEN 1 AND 100),
 authority_digest char(64) NOT NULL, valid_until timestamptz NOT NULL,
 PRIMARY KEY(tenant,environment,cursor_ref)
);
REVOKE ALL ON ALL TABLES IN SCHEMA portal_communication FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA portal_communication FROM PUBLIC;
-- DBA grants separate PHI-content and metadata roles. Metadata roles must have
-- NO SELECT on content ciphertext/nonce, key access or raw content operations.
CREATE VIEW portal_communication.content_metadata AS
 SELECT tenant,environment,case_ref,sender_identity_digest,body_ref,authored_at,
        authority_receipt_ref,authority_digest
 FROM portal_communication.content;
REVOKE ALL ON portal_communication.content_metadata FROM PUBLIC;
