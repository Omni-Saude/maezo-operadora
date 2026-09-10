-- DBA-installed addition to schema.sql, in the SAME database as the ORIGINAL
-- public.portal_sessions/public.portal_memberships. No runtime DDL or default grants.
-- Separate/copy identity databases are unsupported. Qualify all identity writers,
-- authority invalidation writers, database search_path, roles and source coverage.
CREATE TABLE portal_communication.authority_head (
 tenant text NOT NULL, environment text NOT NULL, access_digest char(64) NOT NULL,
 revision text NOT NULL CHECK(revision ~ '^(0|[1-9][0-9]*)$'),
 publication_ref text, payload bytea CHECK(octet_length(payload)<=65536),
 PRIMARY KEY(tenant,environment,access_digest),
 CHECK((revision='0' AND publication_ref IS NULL AND payload IS NULL)
    OR (revision<>'0' AND publication_ref IS NOT NULL AND payload IS NOT NULL))
);
CREATE TABLE portal_communication.authority_publication (
 tenant text NOT NULL, environment text NOT NULL, publication_ref text NOT NULL,
 request_digest char(64) NOT NULL, revision text NOT NULL CHECK(revision ~ '^[1-9][0-9]*$'),
 PRIMARY KEY(tenant,environment,publication_ref)
);
-- Installed only by the independent deployment owner. Caller cannot select tenant.
CREATE TABLE portal_communication.admission_login (
 login_role name PRIMARY KEY, tenant text NOT NULL
);
REVOKE ALL ON portal_communication.authority_head,
 portal_communication.authority_publication,portal_communication.admission_login FROM PUBLIC;

CREATE FUNCTION portal_communication.lock_session(requested_secret_hash text)
 RETURNS TABLE(session_tenant text,secret_hash text,expires_at timestamptz,session_payload text,
 membership_tenant text,issuer text,subject text,principal_ref text,membership_payload text)
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,portal_communication AS $identity$
DECLARE installed_tenant text;s record;m record;identity jsonb;
BEGIN
 IF requested_secret_hash IS NULL OR requested_secret_hash !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='communication_denied';
 END IF;
 SELECT x.tenant INTO installed_tenant FROM portal_communication.admission_login x
  WHERE x.login_role=session_user;
 IF installed_tenant IS NULL THEN
  RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='communication_denied';
 END IF;
 SELECT x.* INTO s FROM public.portal_sessions x
  WHERE x.tenant=installed_tenant AND x.secret_hash=requested_secret_hash FOR SHARE;
 IF NOT FOUND OR s.expires_at<=clock_timestamp() OR octet_length(s.payload)>65536 THEN
  RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='communication_denied';
 END IF;
 identity:=s.payload::jsonb;
 IF jsonb_typeof(identity) IS DISTINCT FROM 'object'
 OR jsonb_typeof(identity->'issuer') IS DISTINCT FROM 'string'
 OR jsonb_typeof(identity->'subject') IS DISTINCT FROM 'string' THEN
  RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='communication_denied';
 END IF;
 SELECT x.* INTO m FROM public.portal_memberships x WHERE x.tenant=installed_tenant
  AND x.issuer=identity->>'issuer' AND x.subject=identity->>'subject' FOR SHARE;
 IF NOT FOUND OR octet_length(s.payload)+octet_length(m.payload)>65536 THEN
  RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='communication_denied';
 END IF;
 RETURN QUERY SELECT s.tenant,s.secret_hash,s.expires_at,s.payload,
  m.tenant,m.issuer,m.subject,m.principal_ref,m.payload;
END $identity$;
REVOKE ALL ON FUNCTION portal_communication.lock_session(text) FROM PUBLIC;
-- Deployment owner assigns this SECURITY DEFINER function to a dedicated NOLOGIN
-- identity reader with SELECT and UPDATE(payload) solely for FOR SHARE on the two
-- original identity tables; metadata/PHI callers get EXECUTE only. No identity DML.
-- Consumers get SELECT + a narrowly scoped UPDATE grant needed for FOR SHARE on
-- authority_head, never authority INSERT/DELETE/payload UPDATE. Publisher alone gets
-- head INSERT/UPDATE and immutable authority_publication INSERT/SELECT; no UPDATE or
-- DELETE on publication receipts. Roles must NOT inherit function-owner/publisher.
-- General metadata role retains NO access to content ciphertext/nonce or keys.
-- No role or tenant registration is populated by this script.
