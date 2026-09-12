-- AUTH-SL1 owner-installed identity SOURCE database upgrade, never the PHI/native DB.
-- Owner supplies psql source_schema from the qualified identity binding; no public default.
-- Installation deliberately seeds no scope, principal, source receipt, key or active head.
-- Owner assigns distinct control/receipt/writer/reader roles after code/DDL readback.
CREATE SCHEMA portal_auth;
REVOKE ALL ON SCHEMA portal_auth FROM PUBLIC;

CREATE TABLE portal_auth.installation (
 tenant text PRIMARY KEY, installation_ref text NOT NULL, revision bigint NOT NULL CHECK(revision>0),
 binding_digest char(64) NOT NULL, binding jsonb NOT NULL, valid_until timestamptz NOT NULL,
 control_role name NOT NULL, receipt_role name NOT NULL, writer_role name NOT NULL,
 CHECK(control_role<>receipt_role AND control_role<>writer_role AND receipt_role<>writer_role)
);
CREATE UNIQUE INDEX portal_sessions_tenant_session_ref
 ON :"source_schema".portal_sessions(tenant,((payload::jsonb)->>'session_ref'));

CREATE TABLE portal_auth.source_head (
 tenant text NOT NULL REFERENCES portal_auth.installation(tenant),
 category text NOT NULL CHECK(category IN ('session','membership')),
 identity_ref text NOT NULL, revision bigint NOT NULL CHECK(revision>0),
 record_digest char(64) NOT NULL, state text NOT NULL CHECK(state IN ('active','frozen','revoked')),
 pending_change text, dependency_set_digest char(64), PRIMARY KEY(tenant,category,identity_ref)
);
CREATE TABLE portal_auth.reservation (
 tenant text NOT NULL REFERENCES portal_auth.installation(tenant), reservation_ref text NOT NULL,
 command_id text NOT NULL, operation text NOT NULL CHECK(operation IN ('auth.start','auth.documents.respond')),
 principal_ref text NOT NULL, session_ref text NOT NULL, admission_ref text NOT NULL,
 admitted_digest char(64) NOT NULL, request_digest char(64) NOT NULL, scope jsonb NOT NULL,
 session_revision bigint NOT NULL, membership_revision bigint NOT NULL,
 session_digest char(64) NOT NULL, membership_digest char(64) NOT NULL,
 session_binding jsonb NOT NULL, reserved_at timestamptz NOT NULL,
 authorization_until timestamptz NOT NULL, source_receipt jsonb NOT NULL,
 state text NOT NULL CHECK(state IN ('reserved','protected_admission_confirmed','publication_pending','active_ack','issuance_disabled','frozen_ack','revoked_ack')),
 protected_digest char(64), active_publication text,
 PRIMARY KEY(tenant,reservation_ref), UNIQUE(tenant,command_id), UNIQUE(tenant,admission_ref)
);

CREATE TABLE portal_auth.issuance (
 tenant text NOT NULL, publication_id text NOT NULL, reservation_ref text NOT NULL,
 state text NOT NULL CHECK(state IN ('pending','acknowledged')),
 target_state text NOT NULL CHECK(target_state IN ('active','frozen','revoked')),
 expected_generation bigint NOT NULL CHECK(expected_generation>=0),
 request_digest char(64) NOT NULL, request jsonb NOT NULL,
 receipt jsonb, receipt_digest char(64), committed_generation bigint,
 PRIMARY KEY(tenant,publication_id),
 FOREIGN KEY(tenant,reservation_ref) REFERENCES portal_auth.reservation(tenant,reservation_ref),
 CHECK((state='acknowledged')=(receipt IS NOT NULL)),
 CHECK((receipt IS NULL)=(receipt_digest IS NULL)),
 CHECK((receipt IS NULL)=(committed_generation IS NULL))
);
CREATE UNIQUE INDEX portal_auth_one_pending_issuance ON portal_auth.issuance(tenant,reservation_ref)
 WHERE state='pending';
-- Owner registers the complete non-session AUTH source dependency set. No
-- runtime grant is inferred from an actor/resource head merely being present.
CREATE TABLE portal_auth.membership_dependency (
 tenant text NOT NULL REFERENCES portal_auth.installation(tenant),
 principal_ref text NOT NULL, dependency_ref text NOT NULL, binding jsonb NOT NULL,
 -- Initial owner pin/receipt are metadata only; no active clinical payload is copied.
 last_publication jsonb, last_receipt jsonb NOT NULL,
 pending_publication jsonb, pending_change text,
 PRIMARY KEY(tenant,dependency_ref), UNIQUE(tenant,principal_ref,dependency_ref)
);
CREATE TABLE portal_auth.dependency_issuance (
 tenant text NOT NULL, publication_id text NOT NULL, dependency_ref text NOT NULL,
 request jsonb NOT NULL, request_digest char(64) NOT NULL,
 receipt jsonb, receipt_digest char(64),
 PRIMARY KEY(tenant,publication_id),
 FOREIGN KEY(tenant,dependency_ref) REFERENCES portal_auth.membership_dependency(tenant,dependency_ref),
 CHECK((receipt IS NULL)=(receipt_digest IS NULL))
);
CREATE TABLE portal_auth.source_change (
 tenant text NOT NULL REFERENCES portal_auth.installation(tenant), change_id text NOT NULL,
 category text NOT NULL CHECK(category IN ('session','membership')),
 identity_ref text NOT NULL, source_revision bigint NOT NULL,
 operation text NOT NULL CHECK(operation IN ('put','revoke','rotate','membership')),
 -- Existing identity custody only; never exported in a reservation/native projection.
 old_record jsonb, new_record jsonb, new_record_digest char(64), request_digest char(64) NOT NULL,
 state text NOT NULL CHECK(state IN ('freeze_pending','native_frozen','source_committed','complete')),
 dependencies jsonb NOT NULL, membership_dependencies jsonb NOT NULL, applying_xid xid8,
 source_receipt jsonb, PRIMARY KEY(tenant,change_id)
);
CREATE UNIQUE INDEX portal_auth_one_source_change ON portal_auth.source_change(tenant,category,identity_ref)
 WHERE state IN ('freeze_pending','native_frozen');

-- One shared barrier for ALL normal session/membership writers. A caller-set SQL
-- setting is not a permit: only the protected change row, qualified role, complete
-- recorded dispositions and same transaction's owner function can authorize DML.
CREATE FUNCTION portal_auth.source_write_guard() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,portal_auth AS $$
DECLARE t text; c text; ident text; change portal_auth.source_change%ROWTYPE;
BEGIN
 IF TG_OP='TRUNCATE' THEN RAISE EXCEPTION 'AUTH_SOURCE_UNAVAILABLE'; END IF;
 t:=COALESCE(NEW.tenant,OLD.tenant);
 c:=CASE WHEN TG_TABLE_NAME='portal_sessions' THEN 'session' ELSE 'membership' END;
 ident:=CASE WHEN c='session' THEN COALESCE(NEW.payload::jsonb,OLD.payload::jsonb)->>'session_ref'
             ELSE COALESCE(NEW.principal_ref,OLD.principal_ref) END;
 SELECT * INTO change FROM portal_auth.source_change
 WHERE tenant=t AND applying_xid=pg_current_xact_id() AND state='native_frozen'
 AND category=c AND (identity_ref=ident OR (c='session' AND new_record->>'session_ref'=ident));
 IF NOT FOUND THEN RAISE EXCEPTION 'AUTH_SOURCE_UNAVAILABLE'; END IF;
 IF TG_OP='DELETE' AND OLD.payload::jsonb IS DISTINCT FROM change.old_record THEN
   RAISE EXCEPTION 'AUTH_SOURCE_CONFLICT';
 END IF;
 IF TG_OP IN ('INSERT','UPDATE') AND NEW.payload::jsonb IS DISTINCT FROM change.new_record THEN
   RAISE EXCEPTION 'AUTH_SOURCE_CONFLICT';
 END IF;
 IF TG_OP='UPDATE' AND OLD.payload::jsonb IS DISTINCT FROM change.old_record THEN
   RAISE EXCEPTION 'AUTH_SOURCE_CONFLICT';
 END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER portal_auth_session_write BEFORE INSERT OR UPDATE OR DELETE ON :"source_schema".portal_sessions
 FOR EACH ROW EXECUTE FUNCTION portal_auth.source_write_guard();
CREATE TRIGGER portal_auth_session_truncate BEFORE TRUNCATE ON :"source_schema".portal_sessions
 FOR EACH STATEMENT EXECUTE FUNCTION portal_auth.source_write_guard();
CREATE TRIGGER portal_auth_membership_write BEFORE INSERT OR UPDATE OR DELETE ON :"source_schema".portal_memberships
 FOR EACH ROW EXECUTE FUNCTION portal_auth.source_write_guard();
CREATE TRIGGER portal_auth_membership_truncate BEFORE TRUNCATE ON :"source_schema".portal_memberships
 FOR EACH STATEMENT EXECUTE FUNCTION portal_auth.source_write_guard();
ALTER TABLE :"source_schema".portal_sessions ENABLE ALWAYS TRIGGER portal_auth_session_write;
ALTER TABLE :"source_schema".portal_sessions ENABLE ALWAYS TRIGGER portal_auth_session_truncate;
ALTER TABLE :"source_schema".portal_memberships ENABLE ALWAYS TRIGGER portal_auth_membership_write;
ALTER TABLE :"source_schema".portal_memberships ENABLE ALWAYS TRIGGER portal_auth_membership_truncate;

-- The writer login deliberately has no UPDATE privilege on identity rows. This
-- owner function acquires the retained membership locks and returns the complete
-- ordered source snapshot without granting that login direct write authority.
CREATE FUNCTION portal_auth.lock_staff_memberships(p_tenant text) RETURNS SETOF text
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,portal_auth AS $$
DECLARE installed portal_auth.installation%ROWTYPE; source_schema text;
BEGIN
 SELECT * INTO STRICT installed FROM portal_auth.installation WHERE tenant=p_tenant FOR SHARE;
 IF session_user<>installed.writer_role OR clock_timestamp()>=installed.valid_until THEN
   RAISE EXCEPTION 'AUTH_SOURCE_UNAVAILABLE';
 END IF;
 source_schema:=installed.binding->>'source_schema_name';
 IF source_schema IS NULL OR source_schema !~ '^[a-z][a-z0-9_]{0,62}$' THEN
   RAISE EXCEPTION 'AUTH_SOURCE_UNAVAILABLE';
 END IF;
 RETURN QUERY EXECUTE format(
   'SELECT payload FROM %I.portal_memberships WHERE tenant=$1 ORDER BY principal_ref FOR SHARE',
   source_schema
 ) USING p_tenant;
END $$;

CREATE FUNCTION portal_auth.apply_change(p_tenant text,p_change text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,portal_auth AS $$
DECLARE change portal_auth.source_change%ROWTYPE; installed portal_auth.installation%ROWTYPE;
 source_schema text; affected bigint; dependency text;
BEGIN
 SELECT * INTO STRICT installed FROM portal_auth.installation WHERE tenant=p_tenant FOR SHARE;
 IF session_user<>installed.writer_role OR clock_timestamp()>=installed.valid_until THEN
   RAISE EXCEPTION 'AUTH_SOURCE_UNAVAILABLE';
 END IF;
 source_schema:=installed.binding->>'source_schema_name';
 IF source_schema IS NULL OR source_schema !~ '^[a-z][a-z0-9_]{0,62}$' THEN RAISE EXCEPTION 'AUTH_SOURCE_UNAVAILABLE'; END IF;
 SELECT * INTO STRICT change FROM portal_auth.source_change WHERE tenant=p_tenant AND change_id=p_change FOR UPDATE;
 IF change.state IN ('source_committed','complete') THEN RETURN; END IF;
 IF change.state<>'native_frozen' THEN RAISE EXCEPTION 'AUTH_SOURCE_UNAVAILABLE'; END IF;
 PERFORM 1 FROM portal_auth.source_head WHERE tenant=p_tenant AND category=change.category
   AND identity_ref=change.identity_ref AND revision=change.source_revision
   AND state='frozen' AND pending_change=p_change FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'AUTH_SOURCE_CONFLICT'; END IF;
 FOR dependency IN SELECT jsonb_array_elements_text(change.dependencies) LOOP
   PERFORM 1 FROM portal_auth.reservation WHERE tenant=p_tenant AND reservation_ref=dependency
     AND state IN ('issuance_disabled','frozen_ack','revoked_ack') FOR UPDATE;
   IF NOT FOUND THEN RAISE EXCEPTION 'AUTH_SOURCE_UNAVAILABLE'; END IF;
   IF EXISTS(SELECT 1 FROM portal_auth.reservation r WHERE r.tenant=p_tenant
       AND r.reservation_ref=dependency AND r.state='issuance_disabled') THEN
     IF EXISTS(SELECT 1 FROM portal_auth.issuance WHERE tenant=p_tenant AND reservation_ref=dependency) THEN
       RAISE EXCEPTION 'AUTH_SOURCE_UNAVAILABLE';
     END IF;
   ELSE
     PERFORM 1 FROM portal_auth.issuance i JOIN portal_auth.reservation r USING(tenant,reservation_ref)
       WHERE i.tenant=p_tenant AND i.reservation_ref=dependency AND i.state='acknowledged'
       AND i.target_state IN ('frozen','revoked') AND i.receipt->>'state'=i.target_state
       AND i.receipt->>'publication_id'=i.publication_id
       AND i.receipt->>'request_digest'=i.request_digest
       AND i.receipt->>'resource_ref'=r.admission_ref AND i.receipt->>'kind'='audit_intent'
       AND i.receipt->'scope'=r.scope
       AND (i.receipt->>'previous_generation')::bigint=i.expected_generation
       AND (i.receipt->>'head_generation')::bigint=i.committed_generation;
     IF NOT FOUND THEN RAISE EXCEPTION 'AUTH_SOURCE_UNAVAILABLE'; END IF;
   END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM portal_auth.issuance i JOIN portal_auth.reservation r USING(tenant,reservation_ref)
   WHERE r.tenant=p_tenant AND ((change.category='session' AND r.session_ref=change.identity_ref)
   OR (change.category='membership' AND r.principal_ref=change.identity_ref)) AND i.state='pending') THEN
   RAISE EXCEPTION 'AUTH_SOURCE_UNAVAILABLE';
 END IF;
 -- Complete census, not just the previously supplied list.
 IF EXISTS(SELECT 1 FROM portal_auth.reservation r WHERE tenant=p_tenant
   AND ((change.category='session' AND r.session_ref=change.identity_ref)
   OR (change.category='membership' AND r.principal_ref=change.identity_ref))
   AND NOT change.dependencies ? r.reservation_ref) THEN RAISE EXCEPTION 'AUTH_SOURCE_CONFLICT'; END IF;
 IF change.category='membership' THEN
   IF EXISTS(SELECT 1 FROM portal_auth.membership_dependency d WHERE d.tenant=p_tenant
       AND d.principal_ref=change.identity_ref
       AND NOT change.membership_dependencies ? d.dependency_ref)
      OR jsonb_array_length(change.membership_dependencies)<>(SELECT count(*) FROM portal_auth.membership_dependency
         WHERE tenant=p_tenant AND principal_ref=change.identity_ref) THEN
     RAISE EXCEPTION 'AUTH_SOURCE_CONFLICT';
   END IF;
   FOR dependency IN SELECT jsonb_array_elements_text(change.membership_dependencies) LOOP
     PERFORM 1 FROM portal_auth.membership_dependency d
       JOIN portal_auth.dependency_issuance i ON i.tenant=d.tenant
         AND i.dependency_ref=d.dependency_ref
       WHERE d.tenant=p_tenant AND d.dependency_ref=dependency
         AND d.pending_change=p_change AND d.pending_publication IS NULL
         AND d.last_publication=i.request AND d.last_receipt=i.receipt
         AND i.receipt->>'state' IN ('frozen','revoked')
         AND i.receipt->>'publication_id'=i.publication_id
         AND i.receipt->>'request_digest'=i.request_digest
         AND i.receipt->'scope'=i.request->'scope'
         AND i.receipt->>'kind'=i.request->>'kind'
         AND i.receipt->>'resource_ref'=i.request->>'resource_ref';
     IF NOT FOUND THEN RAISE EXCEPTION 'AUTH_SOURCE_UNAVAILABLE'; END IF;
   END LOOP;
 END IF;
 UPDATE portal_auth.source_change SET applying_xid=pg_current_xact_id() WHERE tenant=p_tenant AND change_id=p_change;
 IF change.category='session' THEN
   IF change.old_record IS NOT NULL THEN
     EXECUTE format('DELETE FROM %I.portal_sessions WHERE tenant=$1 AND payload::jsonb=$2',source_schema) USING p_tenant,change.old_record;
     GET DIAGNOSTICS affected=ROW_COUNT;
     IF affected<>1 THEN RAISE EXCEPTION 'AUTH_SOURCE_CONFLICT'; END IF;
   END IF;
   IF change.new_record IS NOT NULL THEN
     EXECUTE format('INSERT INTO %I.portal_sessions(tenant,secret_hash,expires_at,payload) VALUES($1,$2,$3,$4)',source_schema) USING p_tenant,change.new_record->>'secret_hash',(change.new_record->>'expires_at')::timestamptz,change.new_record::text;
   END IF;
 ELSE
   EXECUTE format('INSERT INTO %I.portal_memberships(tenant,issuer,subject,principal_ref,payload) VALUES($1,$2,$3,$4,$5) ON CONFLICT(tenant,issuer,subject) DO UPDATE SET payload=EXCLUDED.payload',source_schema) USING p_tenant,change.new_record->>'issuer',change.new_record->>'subject',change.new_record->>'principal_ref',change.new_record::text;
 END IF;
 UPDATE portal_auth.source_head SET revision=revision+1,state='revoked',pending_change=NULL
   WHERE tenant=p_tenant AND category=change.category AND identity_ref=change.identity_ref;
 IF change.new_record IS NOT NULL THEN
   UPDATE portal_auth.source_head SET state='frozen',pending_change=p_change,record_digest=change.new_record_digest
   WHERE tenant=p_tenant AND category=change.category
     AND identity_ref=CASE WHEN change.category='session' THEN change.new_record->>'session_ref' ELSE change.identity_ref END;
   IF NOT FOUND THEN RAISE EXCEPTION 'AUTH_SOURCE_CONFLICT'; END IF;
 END IF;
 UPDATE portal_auth.source_change SET state='source_committed',applying_xid=NULL,
   source_receipt=jsonb_build_object('change_id',p_change,'revision',(change.source_revision+1)::text,
     'committed_at',clock_timestamp()) WHERE tenant=p_tenant AND change_id=p_change;
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA portal_auth FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA portal_auth FROM PUBLIC;
-- Owner applies exact grants and reads them back. Never grant runtime TRUNCATE,
-- ownership, trigger control, replication bypass, registry or ACK write privileges.
