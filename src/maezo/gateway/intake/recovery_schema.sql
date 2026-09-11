-- Cursor-only addition in the SAME qualified protected intake database.
-- No admission, native state, source authority, actor backfill or runtime DDL.
CREATE TABLE portal_intake.recovery_cursor (
 tenant text NOT NULL, environment text NOT NULL, cursor_ref text NOT NULL,
 actor_digest char(64) NOT NULL CHECK(actor_digest ~ '^[0-9a-f]{64}$'),
 membership_revision text NOT NULL CHECK(membership_revision ~ '^(0|[1-9][0-9]*)$'),
 page_size integer NOT NULL CHECK(page_size=50),
 upper_bound timestamptz NOT NULL, after_created_at timestamptz NOT NULL,
 after_intake_ref text NOT NULL, valid_until timestamptz NOT NULL,
 PRIMARY KEY(tenant,environment,cursor_ref),
 CHECK(after_created_at<=upper_bound)
);
REVOKE ALL ON portal_intake.recovery_cursor FROM PUBLIC;
-- Owner installs metadata SELECT + cursor INSERT/SELECT only; no cursor UPDATE,
-- authority/identity/admission/native-outbox DML, or intake ciphertext access.
-- Original identity nonce/ciphertext/key_id SELECT is restricted to the protected
-- gateway identity reader using the existing qualified native identity decoder.
-- Stored tenant lacks an environment column: sharing/importing admissions across
-- environments requires separate original-scope qualification, not this table.
