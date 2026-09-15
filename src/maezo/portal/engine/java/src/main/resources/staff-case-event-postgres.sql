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
