-- GERADO por deploy/sql/build_engine_native_install.py. NAO EDITE: edite os DDL canonicos e regere.
-- T1.4 (plano portal-autoridade-nativa-dev; ADR-0060). Passo 2 de 3, depois de engine-native-roles.sql.
-- Executar como maezo_native_schema_owner (login proprio, TLS), nunca no boot. Idempotente:
-- schema vazio -> instala tudo; instalacao completa -> so reaplica grants e confere a postura;
-- qualquer estado parcial -> recusa.
SET search_path TO maezo_native;
SELECT pg_catalog.set_config('maezo.staff_native_role', 'cibseven_app', false);
DO $guard$
BEGIN
 IF current_user <> 'maezo_native_schema_owner' OR current_schema() IS DISTINCT FROM 'maezo_native'
    OR (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='maezo_native')
       IS DISTINCT FROM 'maezo_native_schema_owner' THEN
   RAISE EXCEPTION 'engine-native-install: execute como maezo_native_schema_owner em maezo_native';
 END IF;
END $guard$;
DO $install$
DECLARE relations int;
BEGIN
 SELECT count(*) INTO relations FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='maezo_native' AND c.relkind IN ('r','p','v','m','f');
 IF relations > 0 THEN
   IF to_regclass('maezo_native.mzo_staff_case_issuer_ledger') IS NULL
      OR to_regclass('maezo_native.mzo_portal_read_admission') IS NULL THEN
     RAISE EXCEPTION 'engine-native-install: instalacao parcial em maezo_native (% relacoes)', relations;
   END IF;
   RETURN;
 END IF;
 -- 1. src/maezo/portal/engine/java/src/main/resources/human-schema-postgres.sql
 EXECUTE $mzo_ddl_1$
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
$mzo_ddl_1$;
 -- 2. src/maezo/portal/engine/java/src/main/resources/human-assignment-postgres.sql
 EXECUTE $mzo_ddl_2$
-- E03 deployment owner applies to the actual engine schema; no grants/activation seeded.
CREATE TABLE MZO_HUMAN_ASSIGNMENT_INSTALLATION (
 TENANT_ varchar(255) PRIMARY KEY REFERENCES MZO_HUMAN_TENANT(TENANT_),
 ENVIRONMENT_ varchar(255) NOT NULL, ENGINE_NAME_ varchar(255) NOT NULL,
 DATABASE_INCARNATION_ varchar(255) NOT NULL,
 DATABASE_OID_ oid NOT NULL, ENGINE_SCHEMA_OID_ oid NOT NULL, ENGINE_LOGIN_OID_ oid NOT NULL,
 CONFIGURATION_DIGEST_ char(64) NOT NULL, DEPLOYMENT_RECEIPT_REF_ varchar(255) NOT NULL,
 DEPLOYMENT_RECEIPT_DIGEST_ char(64) NOT NULL,
 STATE_ varchar(16) NOT NULL CHECK(STATE_ IN ('qualified','disabled')),
 REVISION_ bigint NOT NULL CHECK(REVISION_>=0), VALID_UNTIL_ bigint NOT NULL
);
CREATE TABLE MZO_HUMAN_ASSIGNMENT_GENERATION (
 TENANT_ varchar(255) PRIMARY KEY REFERENCES MZO_HUMAN_TENANT(TENANT_),
 SOURCE_REVISION_ bigint NOT NULL CHECK(SOURCE_REVISION_>=0), AUTHORITY_REVISION_ bigint NOT NULL CHECK(AUTHORITY_REVISION_>=0),
 GENERATION_DIGEST_ char(64) NOT NULL, STATE_ varchar(16) NOT NULL CHECK(STATE_ IN ('active','disabled')),
 GENERATION_ text NOT NULL CHECK(octet_length(GENERATION_)<=65536),
 ATTESTATION_ text NOT NULL CHECK(octet_length(ATTESTATION_)<=65536), VALID_UNTIL_ bigint NOT NULL
);
CREATE TABLE MZO_HUMAN_ASSIGNMENT_PUBLICATION (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_), PUBLICATION_ID_ varchar(255) NOT NULL,
 SOURCE_REVISION_ bigint NOT NULL CHECK(SOURCE_REVISION_>=0), OPERATION_ varchar(16) NOT NULL CHECK(OPERATION_ IN ('disable','replace')),
 REQUEST_DIGEST_ char(64) NOT NULL, REQUEST_ text NOT NULL CHECK(octet_length(REQUEST_)<=65536),
 RECEIPT_ text NOT NULL CHECK(octet_length(RECEIPT_)<=65536),
 PRIMARY KEY(TENANT_,PUBLICATION_ID_), UNIQUE(TENANT_,SOURCE_REVISION_,OPERATION_)
);
REVOKE ALL ON MZO_HUMAN_ASSIGNMENT_INSTALLATION,MZO_HUMAN_ASSIGNMENT_GENERATION,MZO_HUMAN_ASSIGNMENT_PUBLICATION FROM PUBLIC;
-- Runtime: SELECT-only installation; SELECT/INSERT/UPDATE generation; SELECT/INSERT publication.
-- Independently controlled deployment role alone owns installation writes and DDL.

-- C9 retained effect association and separately source-issued current receipt disclosure.
CREATE TABLE MZO_HUMAN_ASSIGNMENT_RECEIPT_RESOURCE (
 TENANT_ varchar(255) NOT NULL, TASK_ varchar(255) NOT NULL, COMMAND_ varchar(255) NOT NULL,
 DIGEST_ char(64) NOT NULL, PRINCIPAL_ varchar(255) NOT NULL, WORKLOAD_ varchar(255) NOT NULL,
 LINKAGE_ text NOT NULL CHECK(octet_length(LINKAGE_)<=65536),
 PRIMARY KEY(TENANT_,TASK_,COMMAND_),
 FOREIGN KEY(TENANT_,TASK_,COMMAND_) REFERENCES MZO_HUMAN_RECEIPT(TENANT_,TASK_,COMMAND_)
);
CREATE TABLE MZO_HUMAN_ASSIGNMENT_RECEIPT_DISCLOSURE (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_),TASK_ varchar(255) NOT NULL,COMMAND_ varchar(255) NOT NULL,
 SOURCE_REVISION_ bigint NOT NULL,DISCLOSURE_DIGEST_ char(64) NOT NULL,
 REQUEST_ text NOT NULL CHECK(octet_length(REQUEST_)<=65536),PRIMARY KEY(TENANT_,TASK_,COMMAND_)
);
CREATE TABLE MZO_HUMAN_ASSIGNMENT_RECEIPT_PUBLICATION (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_),PUBLICATION_ID_ varchar(255) NOT NULL,
 REQUEST_DIGEST_ char(64) NOT NULL,REQUEST_ text NOT NULL CHECK(octet_length(REQUEST_)<=65536),
 RECEIPT_ text NOT NULL CHECK(octet_length(RECEIPT_)<=65536),PRIMARY KEY(TENANT_,PUBLICATION_ID_)
);
REVOKE ALL ON MZO_HUMAN_ASSIGNMENT_RECEIPT_RESOURCE,MZO_HUMAN_ASSIGNMENT_RECEIPT_DISCLOSURE,MZO_HUMAN_ASSIGNMENT_RECEIPT_PUBLICATION FROM PUBLIC;
$mzo_ddl_2$;
 -- 3. src/maezo/portal/engine/java/src/main/resources/human-decision-qualification-postgres.sql
 EXECUTE $mzo_ddl_3$
-- ADR0049 D3/D5. Additive migration; SAME schema/database as existing MZO_HUMAN_* / ACT_*.
-- Execute once as the independently designated migration owner. Creates no role,
-- credential, trust anchor, tenant, qualification, activation or consumer evidence.
CREATE TABLE MZO_HUMAN_DECISION_AUTHORITY (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_),
 INSTALLATION_ varchar(255) NOT NULL, GENERATION_ bigint NOT NULL CHECK(GENERATION_>=0),
 DOCUMENT_ text NOT NULL, DIGEST_ char(64) NOT NULL,
 PRIMARY KEY(TENANT_,INSTALLATION_,GENERATION_)
);
CREATE TABLE MZO_HUMAN_DECISION_QUALIFICATION (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_),
 INSTALLATION_ varchar(255) NOT NULL, AUTHORITY_REV_ bigint NOT NULL CHECK(AUTHORITY_REV_>=0),
 PACKET_ text NOT NULL, DIGEST_ char(64) NOT NULL,
 PRIMARY KEY(TENANT_,INSTALLATION_,AUTHORITY_REV_,DIGEST_)
);
CREATE TABLE MZO_HUMAN_DECISION_INSTALL_RECEIPT (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_),
 INSTALLATION_ varchar(255) NOT NULL, OPERATION_ varchar(255) NOT NULL,
 KIND_ varchar(32) NOT NULL CHECK(KIND_ IN ('designate','install','revoke')),
 REQUEST_DIGEST_ char(64) NOT NULL, EXPECTED_REV_ bigint NOT NULL CHECK(EXPECTED_REV_>=0),
 RESULTING_REV_ bigint NOT NULL CHECK(RESULTING_REV_=EXPECTED_REV_+1),
 PACKET_DIGESTS_ text NOT NULL,
 RECORDED_AT_ timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(TENANT_,INSTALLATION_,OPERATION_)
);
REVOKE ALL ON MZO_HUMAN_DECISION_AUTHORITY,MZO_HUMAN_DECISION_QUALIFICATION,
 MZO_HUMAN_DECISION_INSTALL_RECEIPT FROM PUBLIC;
-- Explicit separate grants, applied by install_decision_qualification_schema:
-- migration owner retains ownership; installer SELECT/INSERT only on immutable
-- records and binding, SELECT/UPDATE on tenant revision; source reader SELECT only.
-- The actual engine runtime remains SELECT ONLY on MZO_HUMAN_DECISION_BINDING.
-- Never grant runtime/reader membership in installer/owner, or schema CREATE.
$mzo_ddl_3$;
 -- 4. src/maezo/portal/engine/java/src/main/resources/portal-read-schema-postgres.sql
 EXECUTE $mzo_ddl_4$
-- Q2 additive projection schema. Apply once with a dedicated migration identity, never at startup.
-- Same database/transaction as CIB 2.1 and human-schema-postgres.sql. No ACT_* DDL.
-- No role/credential is provisioned by this file. PUBLIC receives no rights.
CREATE TABLE MZO_PORTAL_READ_CATALOG (
 TENANT_ varchar(255) NOT NULL, ENVIRONMENT_ varchar(255) NOT NULL,
 ENGINE_ varchar(255) NOT NULL, INCARNATION_ varchar(255) NOT NULL,
 CATALOG_ varchar(255) NOT NULL, REVISION_ bigint NOT NULL CHECK (REVISION_>=0),
 DIGEST_ char(64) NOT NULL, ARTIFACT_ text NOT NULL, PUBLICATION_ varchar(255) NOT NULL,
 SOURCE_ text NOT NULL, PUBLISHER_ varchar(255) NOT NULL, VALID_UNTIL_ timestamptz NOT NULL,
 PRIMARY KEY(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,CATALOG_,REVISION_)
);
CREATE TABLE MZO_PORTAL_READ_DESIGNATION (
 TENANT_ varchar(255) NOT NULL, ENVIRONMENT_ varchar(255) NOT NULL,
 ENGINE_ varchar(255) NOT NULL, INCARNATION_ varchar(255) NOT NULL,
 CATALOG_ varchar(255) NOT NULL, REVISION_ bigint NOT NULL CHECK (REVISION_>=0),
 DIGEST_ char(64) NOT NULL, PUBLICATION_ varchar(255) NOT NULL,
 SOURCE_ text NOT NULL, PUBLISHER_ varchar(255) NOT NULL, VALID_UNTIL_ timestamptz NOT NULL,
 REVOKED_ boolean NOT NULL,
 PRIMARY KEY(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,CATALOG_)
);
CREATE TABLE MZO_PORTAL_READ_MEMBERSHIP (
 TENANT_ varchar(255) NOT NULL, ENVIRONMENT_ varchar(255) NOT NULL,
 ENGINE_ varchar(255) NOT NULL, INCARNATION_ varchar(255) NOT NULL,
 PRINCIPAL_ varchar(255) NOT NULL, ISSUER_ text NOT NULL, SUBJECT_ varchar(255) NOT NULL,
 REVISION_ bigint NOT NULL CHECK (REVISION_>=0), PAYLOAD_ text NOT NULL,
 PUBLICATION_ varchar(255) NOT NULL, SOURCE_ text NOT NULL,
 PRIMARY KEY(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,PRINCIPAL_),
 UNIQUE(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,ISSUER_,SUBJECT_)
);
CREATE TABLE MZO_PORTAL_READ_RESOURCE (
 TENANT_ varchar(255) NOT NULL, ENVIRONMENT_ varchar(255) NOT NULL,
 ENGINE_ varchar(255) NOT NULL, INCARNATION_ varchar(255) NOT NULL,
 TASK_ varchar(64) NOT NULL, PAYLOAD_ text NOT NULL, PUBLICATION_ varchar(255) NOT NULL,
 SOURCE_ text NOT NULL,
 PRIMARY KEY(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,TASK_)
);
CREATE TABLE MZO_PORTAL_READ_REVOCATION (
 TENANT_ varchar(255) NOT NULL, ENVIRONMENT_ varchar(255) NOT NULL,
 ENGINE_ varchar(255) NOT NULL, INCARNATION_ varchar(255) NOT NULL,
 FINGERPRINT_ char(64) NOT NULL, REVISION_ bigint NOT NULL CHECK(REVISION_>=0),
 PUBLICATION_ varchar(255) NOT NULL,
 PRIMARY KEY(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,FINGERPRINT_)
);
CREATE TABLE MZO_PORTAL_READ_PUBLICATION_RECEIPT (
 TENANT_ varchar(255) NOT NULL, ENVIRONMENT_ varchar(255) NOT NULL,
 ENGINE_ varchar(255) NOT NULL, INCARNATION_ varchar(255) NOT NULL,
 PUBLICATION_ varchar(255) NOT NULL, DIGEST_ char(64) NOT NULL,
 KEY_FINGERPRINT_ char(64) NOT NULL, RECEIPT_ text NOT NULL,
 PRIMARY KEY(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,PUBLICATION_)
);
REVOKE ALL ON MZO_PORTAL_READ_CATALOG,MZO_PORTAL_READ_DESIGNATION,
 MZO_PORTAL_READ_MEMBERSHIP,MZO_PORTAL_READ_RESOURCE,MZO_PORTAL_READ_REVOCATION,
 MZO_PORTAL_READ_PUBLICATION_RECEIPT FROM PUBLIC;
-- Publication receiver alone may write these relations and MZO_HUMAN_TENANT REV_.
-- Its explicit resource fence uses TaskEntity.forceUpdate in the SAME engine transaction.
-- Read routes execute only prepared SELECT on the enlisted connection. The plugin's
-- runtime identity must be qualified separately; this DDL is not an authority grant.
$mzo_ddl_4$;
 -- 5. src/maezo/portal/engine/java/src/main/resources/staff-case-schema-postgres.sql
 EXECUTE $mzo_ddl_5$
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
$mzo_ddl_5$;
 -- 6. src/maezo/portal/engine/java/src/main/resources/staff-case-event-postgres.sql
 EXECUTE $mzo_ddl_6$
-- SC1 revision head only. No timeline, DMN-history or dossier availability is asserted.
-- Apply once as the same qualified installer, beside staff-case-schema-postgres.sql.
CREATE TABLE mzo_staff_native_event_head (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 case_ref text NOT NULL, process_instance_id text NOT NULL, case_revision bigint NOT NULL CHECK(case_revision>0),
 observed_at timestamptz NOT NULL,
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,case_ref),
 UNIQUE(tenant,environment,engine_name,database_incarnation,process_instance_id)
);
REVOKE ALL ON mzo_staff_native_event_head FROM PUBLIC;
DO $$
DECLARE native_role name := current_setting('maezo.staff_native_role')::name;
BEGIN
 IF native_role=current_user OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=native_role AND rolcanlogin AND NOT rolsuper AND NOT rolbypassrls)
   OR pg_has_role(native_role,current_user,'MEMBER') THEN RAISE EXCEPTION 'invalid staff native role'; END IF;
 EXECUTE format('GRANT SELECT,INSERT,UPDATE ON mzo_staff_native_event_head TO %I',native_role);
END $$;
-- No historical initial rows are guessed. ROOT must compose initializeClaim after
-- genuine AUTH admission, the installed history observer, and occurrence transition
-- observers before qualifying this revision as a current source.
$mzo_ddl_6$;
 -- 7. src/maezo/portal/engine/java/src/main/resources/auth-document-producer-postgres.sql
 EXECUTE $mzo_ddl_7$
-- Owner-only migration. Runtime may SELECT designation, INSERT technical query audit.
-- No default grant/designation. Source installer/revoker takes MZO_HUMAN_TENANT
-- FOR UPDATE before changing designation rows, matching the AUTH invalidation lock.
CREATE TABLE MZO_AUTH_PRODUCER_DESIGNATION (
 TENANT_ text NOT NULL, DIGEST_ char(64) NOT NULL, RECORD_ text NOT NULL,
 PRIMARY KEY(TENANT_,DIGEST_),
 CHECK(octet_length(RECORD_)<=65536)
);
CREATE TABLE MZO_AUTH_PRODUCER_QUERY (
 TENANT_ text NOT NULL, QUERY_ text NOT NULL, QUERY_DIGEST_ char(64) NOT NULL,
 DESIGNATION_DIGEST_ char(64) NOT NULL, ACQUISITION_ text NOT NULL,
 OBSERVED_ timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(TENANT_,QUERY_),
 FOREIGN KEY(TENANT_,DESIGNATION_DIGEST_) REFERENCES MZO_AUTH_PRODUCER_DESIGNATION(TENANT_,DIGEST_)
);
REVOKE ALL ON MZO_AUTH_PRODUCER_DESIGNATION,MZO_AUTH_PRODUCER_QUERY FROM PUBLIC;
$mzo_ddl_7$;
 -- 8. src/maezo/portal/engine/java/src/main/resources/human-auth-intake-documents-postgres.sql
 EXECUTE $mzo_ddl_8$
-- E04 approved construction profile; owner-applied to the SAME CIB database/schema.
-- No executable designation, source grant or profile activation is bootstrapped here.
-- Runtime grants are installed/read back by AuthInstallation, never PUBLIC.
CREATE TABLE MZO_AUTH_INSTALLATION (
 TENANT_ varchar(255) PRIMARY KEY REFERENCES MZO_HUMAN_TENANT(TENANT_),
 INCARNATION_ varchar(255) NOT NULL, REV_ bigint NOT NULL CHECK(REV_>0),
 SCOPE_ text NOT NULL, BINDING_ text NOT NULL, QUALIFICATION_ text NOT NULL
);
CREATE TABLE MZO_AUTH_TRUST (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_AUTH_INSTALLATION(TENANT_),
 KEY_ID_ varchar(255) NOT NULL, DESIGNATION_ text NOT NULL,
 PRIMARY KEY(TENANT_,KEY_ID_)
);
CREATE TABLE MZO_AUTH_REVOKED_KEY (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_AUTH_INSTALLATION(TENANT_),
 KEY_ID_ varchar(255) NOT NULL, REV_ bigint NOT NULL CHECK(REV_>0),
 PRIMARY KEY(TENANT_,KEY_ID_)
);
CREATE TABLE MZO_AUTH_INPUT_HEAD (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_AUTH_INSTALLATION(TENANT_),
 KIND_ varchar(32) NOT NULL CHECK(KIND_ IN ('actor','resource_authority','guide','start_facts','document_custody','document_policy','audit_intent')),
 RESOURCE_ varchar(255) NOT NULL, GENERATION_ bigint NOT NULL CHECK(GENERATION_>0),
 STATE_ varchar(16) NOT NULL CHECK(STATE_ IN ('active','frozen','revoked')),
 PUBLICATION_ varchar(255) NOT NULL, PUBLICATION_DIGEST_ char(64) NOT NULL,
 PUBLISHER_KEY_ varchar(255) NOT NULL, PUBLISHER_DIGEST_ char(64) NOT NULL,
 SOURCE_ text NOT NULL, PAYLOAD_ text, PAYLOAD_DIGEST_ char(64), VALID_UNTIL_ timestamptz NOT NULL,
 CHECK((STATE_='active')=(PAYLOAD_ IS NOT NULL)),
 CHECK((PAYLOAD_ IS NULL)=(PAYLOAD_DIGEST_ IS NULL)),
 PRIMARY KEY(TENANT_,KIND_,RESOURCE_)
);
CREATE TABLE MZO_AUTH_INPUT_VERSION (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_AUTH_INSTALLATION(TENANT_),
 KIND_ varchar(32) NOT NULL, RESOURCE_ varchar(255) NOT NULL, GENERATION_ bigint NOT NULL CHECK(GENERATION_>0),
 PUBLISHER_KEY_ varchar(255) NOT NULL, PUBLISHER_DIGEST_ char(64) NOT NULL,
 PUBLICATION_ varchar(255) NOT NULL, DIGEST_ char(64) NOT NULL, REQUEST_ text NOT NULL, RECEIPT_ text NOT NULL,
 PRIMARY KEY(TENANT_,KIND_,RESOURCE_,GENERATION_), UNIQUE(TENANT_,PUBLICATION_)
);
CREATE TABLE MZO_AUTH_GUIDE_CLAIM (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_AUTH_INSTALLATION(TENANT_),
 GUIDE_ varchar(255) NOT NULL, INTAKE_ varchar(255) NOT NULL,
 COMMAND_ varchar(255) NOT NULL, DIGEST_ char(64) NOT NULL,
 PRINCIPAL_ varchar(255) NOT NULL, INSTANCE_ varchar(255) NOT NULL, CASE_ varchar(255) NOT NULL,
 DEFINITION_ text NOT NULL,
 PRIMARY KEY(TENANT_,GUIDE_), UNIQUE(TENANT_,INTAKE_), UNIQUE(TENANT_,INSTANCE_), UNIQUE(TENANT_,CASE_)
);
CREATE TABLE MZO_AUTH_INSTANCE_HEAD (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_AUTH_INSTALLATION(TENANT_),
 INSTANCE_ varchar(255) NOT NULL, REV_ bigint NOT NULL CHECK(REV_>=0),
 GENERATION_ bigint NOT NULL CHECK(GENERATION_>=0), CURRENT_REQUEST_ varchar(255),
 PRIMARY KEY(TENANT_,INSTANCE_),
 FOREIGN KEY(TENANT_,INSTANCE_) REFERENCES MZO_AUTH_GUIDE_CLAIM(TENANT_,INSTANCE_)
);
CREATE TABLE MZO_AUTH_DOC_OCCURRENCE (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_AUTH_INSTALLATION(TENANT_),
 REQUEST_ varchar(255) NOT NULL, INSTANCE_ varchar(255) NOT NULL,
 GENERATION_ bigint NOT NULL CHECK(GENERATION_>0), REV_ bigint NOT NULL CHECK(REV_>=0),
 PRODUCER_TASK_ varchar(255) NOT NULL, PUBLICATION_TASK_ varchar(255),
 SUBSCRIPTION_ varchar(255), STATE_ varchar(32) NOT NULL CHECK(STATE_ IN
 ('created','awaiting_publication_worker','bound','consumed','expired','cancelled','replaced')),
 RECORD_ text NOT NULL,
 PRIMARY KEY(TENANT_,REQUEST_), UNIQUE(TENANT_,INSTANCE_,PRODUCER_TASK_),
 UNIQUE(TENANT_,INSTANCE_,GENERATION_), UNIQUE(TENANT_,PUBLICATION_TASK_), UNIQUE(TENANT_,SUBSCRIPTION_),
 FOREIGN KEY(TENANT_,INSTANCE_) REFERENCES MZO_AUTH_INSTANCE_HEAD(TENANT_,INSTANCE_)
);
CREATE TABLE MZO_AUTH_EFFECT_RECEIPT (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_AUTH_INSTALLATION(TENANT_),
 COMMAND_ varchar(255) NOT NULL, DIGEST_ char(64) NOT NULL,
 OPERATION_ varchar(32) NOT NULL CHECK(OPERATION_ IN ('auth.start','auth.documents.respond')),
 PRINCIPAL_ varchar(255) NOT NULL, ADMISSION_ varchar(255) NOT NULL,
 REQUEST_ varchar(255), RECEIPT_ text NOT NULL,
 CHECK((OPERATION_='auth.documents.respond')=(REQUEST_ IS NOT NULL)),
 PRIMARY KEY(TENANT_,COMMAND_), UNIQUE(TENANT_,REQUEST_)
);
REVOKE ALL ON TABLE MZO_AUTH_INSTALLATION,MZO_AUTH_TRUST,MZO_AUTH_REVOKED_KEY,
 MZO_AUTH_INPUT_HEAD,MZO_AUTH_INPUT_VERSION,MZO_AUTH_GUIDE_CLAIM,
 MZO_AUTH_INSTANCE_HEAD,MZO_AUTH_DOC_OCCURRENCE,MZO_AUTH_EFFECT_RECEIPT FROM PUBLIC;
$mzo_ddl_8$;
 -- 9. src/maezo/portal/engine/java/src/main/resources/human-consumer-lineage-postgres.sql
 EXECUTE $mzo_ddl_9$
-- P2: owner-installed, same native schema. No default qualification or production grant.
CREATE TABLE MZO_HUMAN_CONSUMER_DATABASE (
  TENANT_ varchar(255) PRIMARY KEY REFERENCES MZO_HUMAN_TENANT(TENANT_),
  BINDING_ text NOT NULL
);
CREATE TABLE MZO_HUMAN_CONSUMER_TRUST (
  TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_CONSUMER_DATABASE(TENANT_),
  KEY_ID_ varchar(255) NOT NULL, DESIGNATION_ text NOT NULL,
  PRIMARY KEY(TENANT_,KEY_ID_)
);
CREATE TABLE MZO_HUMAN_CONSUMER_REVOKED (
  TENANT_ varchar(255) NOT NULL, KEY_ID_ varchar(255) NOT NULL,
  GENERATION_ bigint NOT NULL CHECK(GENERATION_>0),
  PRIMARY KEY(TENANT_,KEY_ID_),
  FOREIGN KEY(TENANT_,KEY_ID_) REFERENCES MZO_HUMAN_CONSUMER_TRUST(TENANT_,KEY_ID_)
);
CREATE TABLE MZO_HUMAN_CONSUMER_QUALIFICATION (
  TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_CONSUMER_DATABASE(TENANT_),
  GENERATION_ bigint NOT NULL CHECK(GENERATION_>0),
  AUTHORITY_REV_ bigint NOT NULL CHECK(AUTHORITY_REV_>=0),
  QUALIFICATION_ varchar(255) NOT NULL, ENVELOPE_ text NOT NULL,
  PRIMARY KEY(TENANT_,GENERATION_), UNIQUE(TENANT_,QUALIFICATION_)
);
CREATE TABLE MZO_HUMAN_CONSUMER_HEAD (
  TENANT_ varchar(255) PRIMARY KEY REFERENCES MZO_HUMAN_CONSUMER_DATABASE(TENANT_),
  GENERATION_ bigint NOT NULL CHECK(GENERATION_>=0),
  QUALIFICATION_GENERATION_ bigint,
  FOREIGN KEY(TENANT_,QUALIFICATION_GENERATION_) REFERENCES MZO_HUMAN_CONSUMER_QUALIFICATION(TENANT_,GENERATION_)
);
CREATE TABLE MZO_HUMAN_CONSUMER_POINTER (
  TENANT_ varchar(255) NOT NULL, SCOPE_DIGEST_ char(64) NOT NULL,
  TASK_ varchar(255) NOT NULL, COMMAND_ varchar(255) NOT NULL,
  EXECUTION_ varchar(255) NOT NULL, GENERATION_ bigint NOT NULL,
  POINTER_ text NOT NULL,
  PRIMARY KEY(TENANT_,TASK_,COMMAND_),
  FOREIGN KEY(TENANT_,GENERATION_) REFERENCES MZO_HUMAN_CONSUMER_QUALIFICATION(TENANT_,GENERATION_),
  FOREIGN KEY(TENANT_,TASK_,COMMAND_) REFERENCES MZO_HUMAN_RECEIPT(TENANT_,TASK_,COMMAND_) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE MZO_HUMAN_CONSUMER_POINTER_HEAD (
  TENANT_ varchar(255) NOT NULL, SCOPE_DIGEST_ char(64) NOT NULL,
  EXECUTION_ varchar(255) NOT NULL, TASK_ varchar(255) NOT NULL, COMMAND_ varchar(255) NOT NULL,
  PRIMARY KEY(TENANT_,SCOPE_DIGEST_,EXECUTION_),
  FOREIGN KEY(TENANT_,TASK_,COMMAND_) REFERENCES MZO_HUMAN_CONSUMER_POINTER(TENANT_,TASK_,COMMAND_)
);
CREATE TABLE MZO_HUMAN_CONSUMER_LINK (
  TENANT_ varchar(255) NOT NULL, SCOPE_DIGEST_ char(64) NOT NULL,
  EXTERNAL_TASK_ varchar(255) NOT NULL, TASK_ varchar(255) NOT NULL, COMMAND_ varchar(255) NOT NULL,
  GENERATION_ bigint NOT NULL, LINK_ text NOT NULL, LINK_DIGEST_ char(64) NOT NULL,
  CONSUMER_KIND_ varchar(255) NOT NULL, CONSUMER_DIGEST_ char(64) NOT NULL,
  OUTCOME_ varchar(255) NOT NULL,
  PRIMARY KEY(TENANT_,SCOPE_DIGEST_,EXTERNAL_TASK_),
  FOREIGN KEY(TENANT_,TASK_,COMMAND_) REFERENCES MZO_HUMAN_CONSUMER_POINTER(TENANT_,TASK_,COMMAND_),
  FOREIGN KEY(TENANT_,GENERATION_) REFERENCES MZO_HUMAN_CONSUMER_QUALIFICATION(TENANT_,GENERATION_)
);
CREATE FUNCTION MZO_HUMAN_CONSUMER_IMMUTABLE() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable consumer evidence'; END $$;
CREATE TRIGGER MZO_HUMAN_CONSUMER_DATABASE_IMMUTABLE BEFORE UPDATE OR DELETE OR TRUNCATE ON MZO_HUMAN_CONSUMER_DATABASE FOR EACH STATEMENT EXECUTE FUNCTION MZO_HUMAN_CONSUMER_IMMUTABLE();
CREATE TRIGGER MZO_HUMAN_CONSUMER_TRUST_IMMUTABLE BEFORE UPDATE OR DELETE OR TRUNCATE ON MZO_HUMAN_CONSUMER_TRUST FOR EACH STATEMENT EXECUTE FUNCTION MZO_HUMAN_CONSUMER_IMMUTABLE();
CREATE TRIGGER MZO_HUMAN_CONSUMER_REVOKED_IMMUTABLE BEFORE UPDATE OR DELETE OR TRUNCATE ON MZO_HUMAN_CONSUMER_REVOKED FOR EACH STATEMENT EXECUTE FUNCTION MZO_HUMAN_CONSUMER_IMMUTABLE();
CREATE TRIGGER MZO_HUMAN_CONSUMER_QUAL_IMMUTABLE BEFORE UPDATE OR DELETE OR TRUNCATE ON MZO_HUMAN_CONSUMER_QUALIFICATION FOR EACH STATEMENT EXECUTE FUNCTION MZO_HUMAN_CONSUMER_IMMUTABLE();
CREATE TRIGGER MZO_HUMAN_CONSUMER_POINTER_IMMUTABLE BEFORE UPDATE OR DELETE OR TRUNCATE ON MZO_HUMAN_CONSUMER_POINTER FOR EACH STATEMENT EXECUTE FUNCTION MZO_HUMAN_CONSUMER_IMMUTABLE();
CREATE TRIGGER MZO_HUMAN_CONSUMER_LINK_IMMUTABLE BEFORE UPDATE OR DELETE OR TRUNCATE ON MZO_HUMAN_CONSUMER_LINK FOR EACH STATEMENT EXECUTE FUNCTION MZO_HUMAN_CONSUMER_IMMUTABLE();
$mzo_ddl_9$;
 -- 10. src/maezo/portal/engine/read-provider/src/main/resources/portal-read-admission-postgres.sql
 EXECUTE $mzo_ddl_10$
-- Q2 installed admission (portal-read-admission.v1, T1.7a). Apply once, in the pinned native schema
-- (maezo_native, ADR-0060), as its owner login (maezo_native_schema_owner), never at startup.
-- One row per admission revision. RECORD_ holds the exact JCS bytes the approver's installation root
-- signed over "maezo/portal-read-admission/v1\0" || RECORD_; SIGNATURE_ is the raw Ed25519 signature.
-- Only the owner writes (install a revision, or set REVOKED_ = true). The engine login receives
-- SELECT only, granted by the installation script (T1.4); the provider refuses to run when the
-- engine login can INSERT, UPDATE, DELETE or TRUNCATE here, or is (a member of) the owner.
-- No role/credential is provisioned by this file. PUBLIC receives no rights.
CREATE TABLE MZO_PORTAL_READ_ADMISSION (
 ADMISSION_REF_ varchar(255) NOT NULL,
 REVISION_ bigint NOT NULL CHECK (REVISION_>=1),
 RECORD_ bytea NOT NULL CHECK (octet_length(RECORD_) BETWEEN 2 AND 65536),
 SIGNATURE_ bytea NOT NULL CHECK (octet_length(SIGNATURE_)=64),
 REVOKED_ boolean NOT NULL DEFAULT false,
 INSTALLED_AT_ timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(ADMISSION_REF_,REVISION_)
);
REVOKE ALL ON MZO_PORTAL_READ_ADMISSION FROM PUBLIC;
$mzo_ddl_10$;
 -- 11. deploy/sql/staff-case-issuer-ledger-postgres.sql
 EXECUTE $mzo_ddl_11$
-- D-H.3 (plano portal-autoridade-nativa-dev, T1.4): estado duravel do emissor de casos staff.
-- Instalado pelo dono (maezo_native_schema_owner) em maezo_native, dentro de engine-native-install.sql.
-- Uma linha por (escopo, policy_ref). A escrita e CAS por revision (UPDATE ... WHERE revision=$old).
-- Rotacao (D-H.6): policy_ref novo = linha nova, revision recomecando. Nunca ha DELETE: o login
-- maezo_native_case_issuer recebe SELECT,INSERT,UPDATE e mais nada; cibseven_app e witnesses, nada.
CREATE TABLE mzo_staff_case_issuer_ledger (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 policy_ref text NOT NULL CHECK(octet_length(policy_ref) BETWEEN 1 AND 255),
 revision bigint NOT NULL CHECK(revision>=0),
 issued_state bytea NOT NULL CHECK(octet_length(issued_state)<=1048576),
 pending_request bytea CHECK(pending_request IS NULL OR octet_length(pending_request) BETWEEN 1 AND 65536),
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,policy_ref)
);
REVOKE ALL ON mzo_staff_case_issuer_ledger FROM PUBLIC;
$mzo_ddl_11$;
END $install$;

-- Grants (idempotentes; reaplicados a cada execucao).
-- Admissao Q2 (T1.7a): SELECT de tabela para o engine e mais nada. Sem grant por coluna, trigger,
-- regra ou RLS: a postura do provedor recusa qualquer um deles.
REVOKE ALL ON mzo_portal_read_admission FROM PUBLIC;
GRANT SELECT ON mzo_portal_read_admission TO cibseven_app;
-- Ledger do emissor (D-H.3): so o emissor, sem DELETE.
REVOKE ALL ON mzo_staff_case_issuer_ledger FROM PUBLIC, cibseven_app, maezo_native_issuer_witness;
GRANT SELECT, INSERT, UPDATE ON mzo_staff_case_issuer_ledger TO maezo_native_case_issuer;
-- Witness do emissor (D-H.2): SELECT-only nas relacoes que o witness le.
GRANT SELECT ON mzo_portal_read_membership, mzo_human_principal TO maezo_native_issuer_witness;
-- maezo_external (D-I): USAGE vem de engine-native-roles.sql; SELECT em toda tabela do dono.
GRANT SELECT ON ALL TABLES IN SCHEMA maezo_external TO cibseven_app;
ALTER DEFAULT PRIVILEGES FOR ROLE maezo_native_schema_owner IN SCHEMA maezo_external
 GRANT SELECT ON TABLES TO cibseven_app;

-- DML do engine (D-J.2c) nas relacoes que ESTE script instala. MZO_HUMAN_*: SELECT,INSERT,UPDATE;
-- sufixo imutavel (I8) e MZO_PORTAL_READ_*: SELECT,INSERT. Nunca DELETE/TRUNCATE/REFERENCES.
-- Excecao por evidencia: PortalReadPublication.java:171 faz UPDATE em MZO_PORTAL_READ_DESIGNATION
-- (catalog-revoke); sem UPDATE o engine recusaria a revogacao. A admissao Q2 fica SELECT (acima);
-- as MZO_HUMAN_CONSUMER_* e MZO_AUTH_* sao do instalador do engine e nao sao tocadas aqui.
DO $dml$
DECLARE r record; privs text;
BEGIN
 FOR r IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='maezo_native' AND c.relkind='r'
     AND (starts_with(c.relname, 'mzo_human_') OR starts_with(c.relname, 'mzo_portal_read_'))
     AND NOT starts_with(c.relname, 'mzo_human_consumer_') AND c.relname<>'mzo_portal_read_admission' LOOP
   privs := CASE
     WHEN r.relname='mzo_portal_read_designation' THEN 'SELECT, INSERT'
     WHEN r.relname='mzo_human_decision_binding' THEN 'SELECT'
     WHEN starts_with(r.relname, 'mzo_portal_read_')
       OR r.relname ~ '_(event|receipt|continuity|cursor|version|dependency|chunk|ledger)$'
       THEN 'SELECT, INSERT'
     ELSE 'SELECT, INSERT, UPDATE' END;
   EXECUTE format('REVOKE ALL ON maezo_native.%I FROM cibseven_app', r.relname);
   EXECUTE format('GRANT %s ON maezo_native.%I TO cibseven_app', privs, r.relname);
 END LOOP;
 -- D-J.4 2.c: a revogacao (PortalReadPublication.java:171) escreve REVOKED_ e PUBLICATION_; C1 (F4b):
 -- a publicacao (ON CONFLICT DO UPDATE, PortalReadPublication.java:149) reescreve REVISION_, DIGEST_,
 -- PUBLICATION_, SOURCE_, PUBLISHER_, VALID_UNTIL_. A chave (TENANT_..CATALOG_) segue sem UPDATE.
 GRANT UPDATE (revoked_, publication_, revision_, digest_, source_, publisher_, valid_until_)
   ON maezo_native.mzo_portal_read_designation TO cibseven_app;
 -- C1 (F4b): idem para os outros dois upserts da publicacao (PortalReadPublication.java:191/257).
 GRANT UPDATE (revision_, payload_, publication_, source_)
   ON maezo_native.mzo_portal_read_membership TO cibseven_app;
 GRANT UPDATE (payload_, publication_, source_)
   ON maezo_native.mzo_portal_read_resource TO cibseven_app;
END $dml$;

-- D-J.4: matriz EXATA de AuthInstallation.installSchema e ConsumerEdgeInstallation.installSchema
-- (o pin do catalogo inclui os grants; divergir aqui faz o instalador Java recusar).
DO $engine_owned$
DECLARE t text; privs text;
BEGIN
 FOREACH t IN ARRAY ARRAY['installation','trust','revoked_key','input_head','input_version','guide_claim',
                          'instance_head','doc_occurrence','effect_receipt'] LOOP
   privs := 'SELECT'
     || CASE WHEN t IN ('input_head','input_version','guide_claim','instance_head',
                        'doc_occurrence','effect_receipt') THEN ',INSERT' ELSE '' END
     || CASE WHEN t IN ('input_head','instance_head','doc_occurrence') THEN ',UPDATE' ELSE '' END;
   EXECUTE format('REVOKE ALL ON TABLE maezo_native.%I FROM PUBLIC, cibseven_app', 'mzo_auth_'||t);
   EXECUTE format('GRANT %s ON TABLE maezo_native.%I TO cibseven_app', privs, 'mzo_auth_'||t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['database','trust','revoked','qualification','head','pointer',
                          'pointer_head','link'] LOOP
   privs := 'SELECT'
     || CASE WHEN t IN ('pointer','pointer_head','link') THEN ',INSERT' ELSE '' END
     || CASE WHEN t = 'pointer_head' THEN ',UPDATE' ELSE '' END;
   EXECUTE format('REVOKE ALL ON TABLE maezo_native.%I FROM PUBLIC, cibseven_app', 'mzo_human_consumer_'||t);
   EXECUTE format('GRANT %s ON TABLE maezo_native.%I TO cibseven_app', privs, 'mzo_human_consumer_'||t);
 END LOOP;
 -- D-H.1/3: o emissor le so tenant_, instance_, case_ da reivindicacao AUTH (entra no pin AUTH).
 REVOKE ALL ON maezo_native.mzo_auth_guide_claim FROM maezo_native_case_issuer;
 GRANT SELECT (tenant_, instance_, case_) ON maezo_native.mzo_auth_guide_claim TO maezo_native_case_issuer;
END $engine_owned$;

-- Postura final: recusa (e desfaz a transacao, se houver) o que as verificacoes do runtime recusariam.
DO $posture$
DECLARE
 t oid := 'maezo_native.mzo_portal_read_admission'::regclass;
 l oid := 'maezo_native.mzo_staff_case_issuer_ledger'::regclass;
 anything text := 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE';
BEGIN
 -- SoD do schema inteiro: o engine nao alcanca o dono nem por heranca nem por SET ROLE.
 IF pg_has_role('cibseven_app', 'maezo_native_schema_owner', 'USAGE')
    OR pg_has_role('cibseven_app', 'maezo_native_schema_owner', 'SET') THEN
   RAISE EXCEPTION 'engine-native-install: cibseven_app alcanca o dono nativo';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=t AND a.attacl IS NOT NULL)
    OR EXISTS(SELECT 1 FROM pg_trigger g WHERE g.tgrelid=t)
    OR EXISTS(SELECT 1 FROM pg_rewrite w WHERE w.ev_class=t)
    OR (SELECT relrowsecurity OR relforcerowsecurity FROM pg_class WHERE oid=t)
    OR EXISTS(SELECT 1 FROM pg_class c, aclexplode(c.relacl) x WHERE c.oid=t
              AND NOT (x.grantee=c.relowner AND x.grantor=c.relowner)
              AND NOT (x.grantee='cibseven_app'::regrole AND x.grantor=c.relowner
                       AND x.privilege_type='SELECT' AND NOT x.is_grantable)) THEN
   RAISE EXCEPTION 'engine-native-install: postura da tabela de admissao recusada';
 END IF;
 IF has_table_privilege('maezo_native_case_issuer', l, 'DELETE,TRUNCATE')
    OR has_table_privilege('cibseven_app', l, anything)
    OR has_table_privilege('maezo_native_issuer_witness', l, anything) THEN
   RAISE EXCEPTION 'engine-native-install: matriz do ledger recusada';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname='maezo_native' AND lower(c.relname) LIKE 'act\_%') THEN
   RAISE EXCEPTION 'engine-native-install: relacao act_* em maezo_native';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_namespace n, aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a
           WHERE n.nspname IN ('maezo_native','maezo_external') AND a.privilege_type='CREATE'
             AND a.grantee<>n.nspowner) THEN
   RAISE EXCEPTION 'engine-native-install: CREATE em schema nativo para alguem alem do dono';
 END IF;
END $posture$;
