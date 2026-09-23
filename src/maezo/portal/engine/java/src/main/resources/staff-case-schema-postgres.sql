-- SC1 exact-detail authority. Apply once as the separately admitted installer.
-- No startup DDL, ACT_* changes, role creation, policy grant or sample designation.
-- Before applying, the installer sets maezo.staff_native_role to the EXISTING
-- qualified CIB login. Its exact role/table OIDs/owners are separately pinned by
-- StaffCaseStore; successfully executing DDL is not owner/source activation.
CREATE TABLE mzo_staff_case_designation_event (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 designation_revision bigint NOT NULL CHECK(designation_revision>0), designation_digest char(64) NOT NULL,
 canonical_designation text NOT NULL CHECK(octet_length(canonical_designation)<=65536),
 installation_proof text NOT NULL CHECK(octet_length(installation_proof)<=65536),
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,designation_revision),
 UNIQUE(tenant,environment,engine_name,database_incarnation,designation_revision,designation_digest)
);
CREATE TABLE mzo_staff_case_designation_current (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 designation_revision bigint NOT NULL, designation_digest char(64) NOT NULL,
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation),
 FOREIGN KEY(tenant,environment,engine_name,database_incarnation,designation_revision,designation_digest)
 REFERENCES mzo_staff_case_designation_event(tenant,environment,engine_name,database_incarnation,designation_revision,designation_digest)
);
CREATE TABLE mzo_staff_case_source_event (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 source_ref text NOT NULL, source_revision bigint NOT NULL CHECK(source_revision>0), publication_id text NOT NULL,
 request_digest char(64) NOT NULL, canonical_publication text NOT NULL CHECK(octet_length(canonical_publication)<=65536),
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,source_ref,source_revision),
 UNIQUE(tenant,environment,engine_name,database_incarnation,publication_id),
 UNIQUE(tenant,environment,engine_name,database_incarnation,publication_id,request_digest)
);
CREATE TABLE mzo_staff_case_source_head (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 source_ref text NOT NULL, source_revision bigint NOT NULL CHECK(source_revision>0),
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,source_ref),
 FOREIGN KEY(tenant,environment,engine_name,database_incarnation,source_ref,source_revision)
 REFERENCES mzo_staff_case_source_event DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE mzo_staff_case_publication_receipt (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 publication_id text NOT NULL, request_digest char(64) NOT NULL,
 canonical_receipt text NOT NULL CHECK(octet_length(canonical_receipt)<=65536),
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,publication_id),
 FOREIGN KEY(tenant,environment,engine_name,database_incarnation,publication_id,request_digest)
 REFERENCES mzo_staff_case_source_event(tenant,environment,engine_name,database_incarnation,publication_id,request_digest)
);
CREATE TABLE mzo_staff_case_grant (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 grant_ref text NOT NULL, grant_revision bigint NOT NULL CHECK(grant_revision>0),
 publication_id text NOT NULL, source_ref text NOT NULL, case_ref text NOT NULL,
 issuer text NOT NULL, subject text NOT NULL, principal_ref text NOT NULL,
 membership_revision bigint NOT NULL CHECK(membership_revision>=0),
 state text NOT NULL CHECK(state IN ('active','revoked')), identity_digest char(64) NOT NULL, effective boolean NOT NULL,
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,grant_ref),
 FOREIGN KEY(tenant,environment,engine_name,database_incarnation,publication_id)
 REFERENCES mzo_staff_case_source_event(tenant,environment,engine_name,database_incarnation,publication_id)
);
CREATE UNIQUE INDEX mzo_staff_case_one_active_detail ON mzo_staff_case_grant
 (tenant,environment,engine_name,database_incarnation,case_ref,issuer,subject,principal_ref,membership_revision)
 WHERE state='active' AND effective;
CREATE TABLE mzo_staff_case_checkpoint_chunk (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 checkpoint_ref text NOT NULL, generation bigint NOT NULL CHECK(generation>=0), chunk_index bigint NOT NULL CHECK(chunk_index>=0),
 chunk_digest char(64) NOT NULL, entry_count bigint NOT NULL CHECK(entry_count BETWEEN 1 AND 256),
 publication_id text NOT NULL, source_ref text NOT NULL,
 canonical_chunk text NOT NULL CHECK(octet_length(canonical_chunk)<=65536),
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,checkpoint_ref,generation,chunk_index),
 UNIQUE(tenant,environment,engine_name,database_incarnation,checkpoint_ref,generation,chunk_index,chunk_digest),
 FOREIGN KEY(tenant,environment,engine_name,database_incarnation,publication_id)
 REFERENCES mzo_staff_case_source_event(tenant,environment,engine_name,database_incarnation,publication_id)
);
CREATE TABLE mzo_staff_case_checkpoint_accepted (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 checkpoint_ref text NOT NULL, generation bigint NOT NULL CHECK(generation>=0), checkpoint_digest char(64) NOT NULL,
 publication_id text NOT NULL, source_ref text NOT NULL, principal_identity_digest char(64) NOT NULL,
 issuer text NOT NULL, subject text NOT NULL, principal_ref text NOT NULL, membership_revision bigint NOT NULL CHECK(membership_revision>=0),
 kind text NOT NULL CHECK(kind='authorization'), active boolean NOT NULL,
 valid_until timestamptz NOT NULL, canonical_checkpoint text NOT NULL CHECK(octet_length(canonical_checkpoint)<=65536),
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,checkpoint_ref,generation),
 UNIQUE(tenant,environment,engine_name,database_incarnation,checkpoint_ref,generation,checkpoint_digest),
 FOREIGN KEY(tenant,environment,engine_name,database_incarnation,publication_id)
 REFERENCES mzo_staff_case_source_event(tenant,environment,engine_name,database_incarnation,publication_id)
);
CREATE UNIQUE INDEX mzo_staff_case_one_active_checkpoint ON mzo_staff_case_checkpoint_accepted
 (tenant,environment,engine_name,database_incarnation,issuer,subject,principal_ref,membership_revision,kind)
 WHERE active;
CREATE TABLE mzo_staff_case_continuity (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 continuity_ref text NOT NULL, continuity_digest char(64) NOT NULL,
 canonical_continuity text NOT NULL CHECK(octet_length(canonical_continuity)<=65536),
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,continuity_ref)
);
CREATE TABLE mzo_staff_case_cursor (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 cursor_ref text NOT NULL, principal_identity_digest char(64) NOT NULL, membership_revision bigint NOT NULL,
 session_ref text NOT NULL, checkpoint_digest char(64) NOT NULL, query_digest char(64) NOT NULL,
 after_ref text NOT NULL, limit_ bigint NOT NULL CHECK(limit_ BETWEEN 1 AND 100), initial_valid_until timestamptz NOT NULL,
 canonical_cursor text NOT NULL CHECK(octet_length(canonical_cursor)<=65536),
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,cursor_ref),
 UNIQUE(tenant,environment,engine_name,database_incarnation,principal_identity_digest,membership_revision,session_ref,checkpoint_digest,query_digest,after_ref,limit_,initial_valid_until)
);
CREATE TABLE mzo_staff_case_policy_version (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 policy_ref text NOT NULL, head_revision bigint NOT NULL CHECK(head_revision>0), head_digest char(64) NOT NULL,
 publication_id text NOT NULL, canonical_head text NOT NULL CHECK(octet_length(canonical_head)<=65536),
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,policy_ref,head_revision),
 UNIQUE(tenant,environment,engine_name,database_incarnation,policy_ref,head_revision,head_digest),
 FOREIGN KEY(tenant,environment,engine_name,database_incarnation,publication_id)
 REFERENCES mzo_staff_case_source_event(tenant,environment,engine_name,database_incarnation,publication_id)
);
CREATE TABLE mzo_staff_case_policy_current (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 policy_ref text NOT NULL, head_revision bigint NOT NULL, head_digest char(64) NOT NULL,
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,policy_ref),
 FOREIGN KEY(tenant,environment,engine_name,database_incarnation,policy_ref,head_revision,head_digest)
 REFERENCES mzo_staff_case_policy_version(tenant,environment,engine_name,database_incarnation,policy_ref,head_revision,head_digest)
);
CREATE TABLE mzo_staff_case_policy_dependency (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 grant_ref text NOT NULL, grant_revision bigint NOT NULL CHECK(grant_revision>0), policy_ref text NOT NULL,
 head_revision bigint NOT NULL, head_digest char(64) NOT NULL,
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,grant_ref,grant_revision,policy_ref),
 FOREIGN KEY(tenant,environment,engine_name,database_incarnation,policy_ref,head_revision,head_digest)
 REFERENCES mzo_staff_case_policy_version(tenant,environment,engine_name,database_incarnation,policy_ref,head_revision,head_digest)
);
CREATE INDEX mzo_staff_policy_dependents ON mzo_staff_case_policy_dependency
 (tenant,environment,engine_name,database_incarnation,policy_ref,grant_ref,grant_revision);
REVOKE ALL ON mzo_staff_case_designation_event,mzo_staff_case_designation_current,
 mzo_staff_case_source_event,mzo_staff_case_source_head,mzo_staff_case_publication_receipt,
 mzo_staff_case_grant,mzo_staff_case_checkpoint_chunk,mzo_staff_case_checkpoint_accepted,
 mzo_staff_case_continuity,mzo_staff_case_cursor,
 mzo_staff_case_policy_version,mzo_staff_case_policy_current,mzo_staff_case_policy_dependency FROM PUBLIC;
DO $$
DECLARE native_role name := current_setting('maezo.staff_native_role')::name;
BEGIN
 IF native_role=current_user OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=native_role AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
   OR pg_has_role(native_role,current_user,'MEMBER') THEN
   RAISE EXCEPTION 'invalid staff native role';
 END IF;
 -- This matrix is exactly what StaffCaseStore pins: DELETE/TRUNCATE never; designation_* SELECT
 -- only; no UPDATE on a relation whose name ends in an immutable suffix
 -- (_event/_receipt/_continuity/_cursor/_version/_dependency/_chunk), UPDATE on the rest.
 -- checkpoint_chunk is insert-only (least privilege): the store never updates a chunk.
 EXECUTE format('GRANT SELECT ON mzo_staff_case_designation_event,mzo_staff_case_designation_current TO %I',native_role);
 EXECUTE format('GRANT SELECT,INSERT ON mzo_staff_case_source_event,mzo_staff_case_publication_receipt,mzo_staff_case_checkpoint_chunk,mzo_staff_case_continuity,mzo_staff_case_cursor,mzo_staff_case_policy_version,mzo_staff_case_policy_dependency TO %I',native_role);
 EXECUTE format('GRANT SELECT,INSERT,UPDATE ON mzo_staff_case_source_head,mzo_staff_case_grant,mzo_staff_case_checkpoint_accepted,mzo_staff_case_policy_current TO %I',native_role);
END $$;
-- No current designation/source/head/grant row is inserted here. Those rows
-- require authenticated installation or the native signed publication command.
