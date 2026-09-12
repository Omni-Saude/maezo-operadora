-- Explicit owner migration; same database as portal_communication, no runtime DDL.
CREATE SCHEMA portal_document_request;
REVOKE ALL ON SCHEMA portal_document_request FROM PUBLIC;
CREATE TABLE portal_document_request.system_identity (
 tenant text NOT NULL, environment text NOT NULL, binding_kind text NOT NULL CHECK(binding_kind IN ('producer','publisher')),
 binding_ref text NOT NULL, login_name name NOT NULL, login_oid oid NOT NULL,
 identity_payload bytea, operations text[] NOT NULL, valid_until timestamptz NOT NULL, revoked boolean NOT NULL,
 PRIMARY KEY(tenant,environment,binding_kind,binding_ref,login_name),
 CHECK(identity_payload IS NULL OR octet_length(identity_payload)<=65536),
 CHECK((binding_kind='producer')=(identity_payload IS NOT NULL)),
 CHECK(cardinality(operations) BETWEEN 1 AND 4),
 CHECK(operations <@ ARRAY['preserve_request_body','publish_request_inbox','read_request_receipt','read_request_body_receipt']::text[])
);
CREATE TABLE portal_document_request.authority_head (
 tenant text NOT NULL, environment text NOT NULL, access_digest char(64) NOT NULL,
 revision text NOT NULL CHECK(revision ~ '^(0|[1-9][0-9]{0,18})$'), publication_ref text, payload bytea,
 PRIMARY KEY(tenant,environment,access_digest), CHECK(payload IS NULL OR octet_length(payload)<=65536)
);
CREATE TABLE portal_document_request.authority_publication (
 tenant text NOT NULL, environment text NOT NULL, publication_ref text NOT NULL,
 request_digest char(64) NOT NULL, revision text NOT NULL,
 PRIMARY KEY(tenant,environment,publication_ref)
);
CREATE TABLE portal_document_request.request (
 tenant text NOT NULL, environment text NOT NULL, request_ref text NOT NULL, case_ref text NOT NULL,
 identity_digest char(64) NOT NULL, identity_payload bytea NOT NULL CHECK(octet_length(identity_payload)<=65536),
 PRIMARY KEY(tenant,environment,request_ref)
);
CREATE TABLE portal_document_request.request_definition_revision (
 tenant text NOT NULL, environment text NOT NULL, request_ref text NOT NULL,
 request_revision text NOT NULL CHECK(request_revision ~ '^(0|[1-9][0-9]{0,18})$'), stable_definition_digest char(64) NOT NULL,
 PRIMARY KEY(tenant,environment,request_ref,request_revision),
 FOREIGN KEY(tenant,environment,request_ref) REFERENCES portal_document_request.request(tenant,environment,request_ref)
);
CREATE TABLE portal_document_request.assessment_version (
 tenant text NOT NULL, environment text NOT NULL, request_ref text NOT NULL, request_revision text NOT NULL,
 native_scope_digest char(64) NOT NULL, assessment_version_digest char(64) NOT NULL, publication_ref text NOT NULL,
 payload bytea NOT NULL CHECK(octet_length(payload)<=65536),
 PRIMARY KEY(tenant,environment,request_ref,assessment_version_digest),
 UNIQUE(tenant,environment,native_scope_digest,publication_ref),
 FOREIGN KEY(tenant,environment,request_ref,request_revision)
  REFERENCES portal_document_request.request_definition_revision(tenant,environment,request_ref,request_revision)
);
CREATE TABLE portal_document_request.command (
 tenant text NOT NULL, environment text NOT NULL, producer_identity_digest char(64) NOT NULL,
 command_id text NOT NULL, request_digest char(64) NOT NULL,
 payload bytea NOT NULL CHECK(octet_length(payload)<=65536),
 receipt bytea NOT NULL CHECK(octet_length(receipt)<=65536),
 PRIMARY KEY(tenant,environment,producer_identity_digest,command_id)
);
CREATE TABLE portal_document_request.delivery (
 tenant text NOT NULL, environment text NOT NULL, request_ref text NOT NULL,
 recipient_identity_digest char(64) NOT NULL, channel text NOT NULL CHECK(channel='portal'),
 communication_ref text NOT NULL, body_ref text NOT NULL,
 record bytea NOT NULL CHECK(octet_length(record)<=65536),
 PRIMARY KEY(tenant,environment,request_ref,recipient_identity_digest,channel),
 UNIQUE(tenant,environment,communication_ref), UNIQUE(tenant,environment,body_ref),
 FOREIGN KEY(tenant,environment,request_ref) REFERENCES portal_document_request.request(tenant,environment,request_ref),
 FOREIGN KEY(tenant,environment,communication_ref) REFERENCES portal_communication.message(tenant,environment,communication_ref),
 FOREIGN KEY(tenant,environment,communication_ref,recipient_identity_digest)
  REFERENCES portal_communication.inbox(tenant,environment,communication_ref,recipient_identity_digest)
);
CREATE TABLE portal_document_request.producer_journal (
 tenant text NOT NULL, environment text NOT NULL, sender_identity_digest char(64) NOT NULL,
 command_id text NOT NULL, kind text NOT NULL, key_id text NOT NULL,
 nonce bytea NOT NULL CHECK(octet_length(nonce)=12), ciphertext bytea NOT NULL CHECK(octet_length(ciphertext)<=262160),
 payload_digest char(64) NOT NULL, state text NOT NULL CHECK(state IN ('sealed','possibly_sent','acknowledged','committed')),
 PRIMARY KEY(tenant,environment,sender_identity_digest,command_id,kind)
);
REVOKE ALL ON ALL TABLES IN SCHEMA portal_document_request FROM PUBLIC;
-- Owner installs exact login/OID/source bindings; no consumer INSERT/UPDATE/DELETE on
-- system_identity or authority tables. Only publisher role writes authority heads.
-- PHI-only role owns phi-body provenance and existing content ciphertext. General
-- metadata role can SELECT content_metadata only and cannot decrypt journal entries.
-- Protected producer-journal writer is a separate PHI deployment role/key, never a BFF key.
-- Source revokers update the same identity/head rows with exclusive locks. Required
-- role/column/source-writer coverage is independently qualified before activation.
