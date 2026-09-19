-- Explicit owner migration for the human document plane (WP-J1-04); no runtime DDL.
-- Same database family as portal_intake / portal_communication / portal_document_request:
-- the General metadata role and the PHI byte role are DIFFERENT logins, and the byte
-- table is granted to the PHI role only, so no General-zone reader can reach ciphertext.
CREATE SCHEMA portal_document;
REVOKE ALL ON SCHEMA portal_document FROM PUBLIC;

-- An upload slot: one command_id per creator per resource, replayed not duplicated.
CREATE TABLE portal_document.upload (
 tenant text NOT NULL, environment text NOT NULL, upload_ref text NOT NULL,
 resource_kind text NOT NULL CHECK(resource_kind IN ('intake','case')),
 resource_ref text NOT NULL, policy_ref text NOT NULL, policy_digest char(64) NOT NULL,
 document_type_ref text NOT NULL, creator_identity_digest char(64) NOT NULL, command_id text NOT NULL,
 admitted_digest char(64) NOT NULL, state text NOT NULL CHECK(state IN ('awaiting_content','screening','verified','quarantined','rejected')),
 document_ref text, key_id text NOT NULL, valid_until timestamptz NOT NULL,
 PRIMARY KEY(tenant,environment,upload_ref),
 UNIQUE(tenant,environment,creator_identity_digest,command_id),
 UNIQUE(tenant,environment,document_ref),
 CHECK((state='awaiting_content')=(document_ref IS NULL))
);
CREATE TABLE portal_document.object (
 tenant text NOT NULL, environment text NOT NULL, upload_ref text NOT NULL,
 key_id text NOT NULL, nonce bytea NOT NULL CHECK(octet_length(nonce)=12),
 ciphertext bytea NOT NULL CHECK(octet_length(ciphertext) BETWEEN 1 AND 262160),
 content_sha256 char(64) NOT NULL,
 PRIMARY KEY(tenant,environment,upload_ref),
 FOREIGN KEY(tenant,environment,upload_ref) REFERENCES portal_document.upload(tenant,environment,upload_ref)
);
-- A completed document: exactly the fields the engine-side custody publisher needs to
-- mint a `DocumentRef` (auth_profile.DocumentRef) — custody revision, content digest,
-- storage version and the policy digest it was admitted under. No clinical field.
CREATE TABLE portal_document.document (
 tenant text NOT NULL, environment text NOT NULL, document_ref text NOT NULL,
 upload_ref text NOT NULL, resource_kind text NOT NULL CHECK(resource_kind IN ('intake','case')),
 resource_ref text NOT NULL, document_type_ref text NOT NULL,
 content_sha256 char(64) NOT NULL, creator_identity_digest char(64) NOT NULL,
 custody_revision bigint NOT NULL CHECK(custody_revision BETWEEN 0 AND 9223372036854775807),
 storage_version_ref text NOT NULL, policy_ref text NOT NULL, policy_digest char(64) NOT NULL,
 completed_at timestamptz NOT NULL,
 PRIMARY KEY(tenant,environment,document_ref),
 UNIQUE(tenant,environment,upload_ref),
 CHECK(custody_revision=0),
 FOREIGN KEY(tenant,environment,upload_ref) REFERENCES portal_document.upload(tenant,environment,upload_ref)
);
-- Custody projection. `pending` is the only state this plane may write on its own
-- (no published screening head yet); every other screening_result/custody_state pair is
-- copied verbatim from a VERIFIED published `document_custody` head and is bound to that
-- head by `head_generation` + `publication_digest`. Nothing here is inferred client-side.
CREATE TABLE portal_document.custody (
 tenant text NOT NULL, environment text NOT NULL, document_ref text NOT NULL,
 resource_kind text NOT NULL CHECK(resource_kind IN ('intake','case')),
 resource_ref text NOT NULL, creator_identity_digest char(64) NOT NULL,
 screening_ref text, screening_revision bigint NOT NULL CHECK(screening_revision BETWEEN 0 AND 9223372036854775807),
 screening_result text NOT NULL CHECK(screening_result IN ('clean','quarantined','rejected','pending')),
 custody_state text NOT NULL CHECK(custody_state IN ('available','revoked','deleted')),
 head_generation bigint, publication_digest char(64), valid_until timestamptz NOT NULL,
 PRIMARY KEY(tenant,environment,document_ref),
 CHECK((screening_result='pending')=(publication_digest IS NULL)),
 CHECK((screening_result='pending')=(screening_ref IS NULL)),
 CHECK((screening_revision=0)=(screening_ref IS NULL)),
 CHECK((head_generation IS NULL)=(publication_digest IS NULL)),
 FOREIGN KEY(tenant,environment,document_ref) REFERENCES portal_document.document(tenant,environment,document_ref)
);
-- Durable exact-request admission (`PostgresAuthDispatchStore.admit_response` pattern):
-- one row per command, replayed with the SAME digest, never silently reinterpreted, and
-- sealed so only the PHI role can read the admitted request payload.
CREATE TABLE portal_document.response (
 tenant text NOT NULL, environment text NOT NULL, response_ref text NOT NULL,
 command_id text NOT NULL, case_ref text NOT NULL, request_ref text NOT NULL,
 request_revision bigint NOT NULL CHECK(request_revision BETWEEN 0 AND 9223372036854775807),
 principal_ref text NOT NULL, admitted_digest char(64) NOT NULL,
 key_id text NOT NULL, nonce bytea NOT NULL CHECK(octet_length(nonce)=12),
 ciphertext bytea NOT NULL CHECK(octet_length(ciphertext) BETWEEN 1 AND 262160),
 valid_until timestamptz NOT NULL,
 PRIMARY KEY(tenant,environment,command_id),
 UNIQUE(tenant,environment,response_ref)
);
CREATE INDEX response_case ON portal_document.response(tenant,environment,case_ref,request_ref);
REVOKE ALL ON ALL TABLES IN SCHEMA portal_document FROM PUBLIC;
-- Role split (owner installs the exact logins/OIDs before activation):
--   * General metadata role: SELECT on upload/document/custody ONLY (references and
--     dispositions); it holds NO grant on `object` or `response` and cannot decrypt.
--   * PHI byte role: owns `object` and `response` (INSERT/SELECT), reads its own key set;
--     never a BFF-held key, never the General-zone key id.
--   * Engine-side custody publisher: appends `document_custody` AUTH input heads from
--     `document` + `custody`; it never writes `object`.
-- Required role/column/source-writer coverage is independently qualified before activation.
