-- DBA install template only. Replace quoted schema with independently admitted
-- installation name; owner/ACL/role/OID descriptor is mandatory before runtime.
-- No portal_memberships/native feed, grants, roles, production approval or seed.
CREATE TABLE portal_provider_admin.administrator_authority (
 tenant text NOT NULL, actor_ref text NOT NULL, session_ref text NOT NULL,
 command_id text NOT NULL, actor_kind text NOT NULL CHECK(actor_kind='human'),
 action text NOT NULL CHECK(action='provider_membership_administration'),
 source_authority_ref text NOT NULL, installation_receipt_ref text NOT NULL, policy_revision text NOT NULL,
 request_digest text NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
 allowed_roles text[] NOT NULL CHECK(cardinality(allowed_roles)>0 AND array_position(allowed_roles,NULL) IS NULL),
 allowed_groups text[] NOT NULL CHECK(array_position(allowed_groups,NULL) IS NULL),
 authority_receipt_ref text NOT NULL CHECK(length(authority_receipt_ref)>0),
 proof_state text NOT NULL CHECK(proof_state IN ('pending','received','validated','enabled','invalid','expired','revoked')),
 verified_at timestamptz NOT NULL, valid_until timestamptz NOT NULL CHECK(valid_until>verified_at),
 revoked boolean NOT NULL, PRIMARY KEY(tenant,actor_ref,session_ref,command_id)
);
CREATE TABLE portal_provider_admin.provider_relationship (
 tenant text NOT NULL, issuer text NOT NULL, subject text NOT NULL,
 principal_ref text NOT NULL, provider_ref text NOT NULL,
 source_receipt_ref text NOT NULL CHECK(length(source_receipt_ref)>0),
 proof_state text NOT NULL CHECK(proof_state IN ('pending','received','validated','enabled','invalid','expired','revoked')),
 verified_at timestamptz NOT NULL, valid_until timestamptz NOT NULL CHECK(valid_until>verified_at),
 revoked boolean NOT NULL, PRIMARY KEY(tenant,issuer,subject), UNIQUE(tenant,principal_ref)
);
CREATE TABLE portal_provider_admin.administrative_head (
 tenant text NOT NULL, principal_ref text NOT NULL, issuer text NOT NULL,
 subject text NOT NULL, revision bigint NOT NULL CHECK(revision>0), payload text NOT NULL,
 PRIMARY KEY(tenant,principal_ref), UNIQUE(tenant,issuer,subject)
);
CREATE TABLE portal_provider_admin.administrative_act (
 tenant text NOT NULL, command_id text NOT NULL, act_ref text NOT NULL,
 request_digest text NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'), receipt text NOT NULL,
 PRIMARY KEY(tenant,command_id), UNIQUE(tenant,act_ref)
);
CREATE TABLE portal_provider_admin.administrative_audit (
 tenant text NOT NULL, audit_ref text NOT NULL, command_id text NOT NULL,
 actor_ref text NOT NULL, request_digest text NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
 occurred_at timestamptz NOT NULL, PRIMARY KEY(tenant,audit_ref), UNIQUE(tenant,command_id)
);
REVOKE ALL ON ALL TABLES IN SCHEMA portal_provider_admin FROM PUBLIC;

-- Execution privilege is granted to the separately authenticated admin writer
-- only by the DBA installer. Table DML stays source-owner/publisher only.
CREATE FUNCTION portal_provider_admin.record_provider_administration(
 p_tenant text, p_actor text, p_session text, p_source text, p_policy text,
 p_install text, p_until timestamptz, p_payload text, p_act text, p_audit text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $function$
DECLARE
 c jsonb; m jsonb; old_record jsonb; member jsonb; rel jsonb;
 g portal_provider_admin.administrator_authority%ROWTYPE;
 r portal_provider_admin.provider_relationship%ROWTYPE;
 a portal_provider_admin.administrative_act%ROWTYPE;
 h portal_provider_admin.administrative_head%ROWTYPE;
 digest text; key text; keys text[]; result text; now_at timestamptz;
 expected bigint; next_revision bigint; until_at timestamptz;
BEGIN
 IF p_until IS NULL OR NOT isfinite(p_until)
 OR EXISTS(SELECT 1 FROM unnest(ARRAY[p_tenant,p_actor,p_session,p_source,p_policy,p_install,
                                    p_payload,p_act,p_audit]) v WHERE v IS NULL OR length(v)=0)
 THEN RETURN 'AUTHORITY_UNPROVEN'; END IF;
 IF octet_length(p_payload)>65536 THEN RETURN 'CONTRACT_MISMATCH'; END IF;
 c := p_payload::jsonb; m := c->'record';
 IF EXISTS(SELECT 1 FROM unnest(ARRAY['command_id','operation','provider_ref','schema_version']) x
           WHERE jsonb_typeof(c->x) IS DISTINCT FROM 'string' OR length(c->>x)=0)
 OR EXISTS(SELECT 1 FROM unnest(ARRAY['tenant','issuer','subject','principal_ref','audience','reviewed_until']) x
           WHERE jsonb_typeof(m->x) IS DISTINCT FROM 'string' OR length(m->>x)=0)
 OR jsonb_typeof(m->'memberships') IS DISTINCT FROM 'array'
 OR jsonb_typeof(m->'subject_bindings') IS DISTINCT FROM 'array'
 THEN RETURN 'CONTRACT_MISMATCH'; END IF;
 IF c->>'schema_version' IS DISTINCT FROM 'provider-membership-administration.v1'
 OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(c) k)
    IS DISTINCT FROM ARRAY['command_id','expected_revision','operation','provider_ref','record','schema_version']
 OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(m) k)
    IS DISTINCT FROM ARRAY['audience','issuer','memberships','principal_ref','reviewed_until','revoked','revision','subject','subject_bindings','tenant']
 OR m->>'tenant' IS DISTINCT FROM p_tenant OR m->>'audience' IS DISTINCT FROM 'provider'
 OR c->>'operation' NOT IN ('grant','revoke') OR jsonb_typeof(m->'revoked') IS DISTINCT FROM 'boolean'
 OR jsonb_typeof(c->'expected_revision') IS DISTINCT FROM 'number' OR jsonb_typeof(m->'revision') IS DISTINCT FROM 'number'
 OR jsonb_array_length(m->'subject_bindings') <> 1
 OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(m->'subject_bindings'->0) k)
    IS DISTINCT FROM ARRAY['kind','resource_ref']
 OR m->'subject_bindings'->0->>'kind' IS DISTINCT FROM 'provider'
 OR m->'subject_bindings'->0->>'resource_ref' IS DISTINCT FROM c->>'provider_ref'
 OR (m->>'revoked')::boolean IS DISTINCT FROM (c->>'operation'='revoke')
 OR length(c->>'command_id')=0 OR length(c->>'provider_ref')=0
 OR length(m->>'principal_ref')=0 OR length(m->>'issuer')=0 OR length(m->>'subject')=0
 OR length(p_act)=0 OR length(p_audit)=0 THEN RETURN 'CONTRACT_MISMATCH'; END IF;
 expected := (c->>'expected_revision')::bigint; next_revision := (m->>'revision')::bigint;
 IF expected < 0 OR expected=9223372036854775807 OR next_revision <> expected+1
 THEN RETURN 'CONTRACT_MISMATCH'; END IF;
 digest := encode(sha256(convert_to(p_payload,'UTF8')),'hex');
 SELECT * INTO g FROM portal_provider_admin.administrator_authority
 WHERE tenant=p_tenant AND actor_ref=p_actor AND session_ref=p_session
 AND command_id=c->>'command_id' FOR SHARE;
 now_at := clock_timestamp();
 IF NOT FOUND OR g.revoked OR g.proof_state IS DISTINCT FROM 'enabled' OR g.actor_kind IS DISTINCT FROM 'human'
 OR g.action IS DISTINCT FROM 'provider_membership_administration' OR g.policy_revision IS DISTINCT FROM p_policy
 OR g.source_authority_ref IS DISTINCT FROM p_source OR g.installation_receipt_ref IS DISTINCT FROM p_install
 OR g.request_digest IS DISTINCT FROM digest OR NOT (g.verified_at <= now_at AND now_at < g.valid_until)
 OR p_until > g.valid_until OR now_at >= p_until THEN RETURN 'AUTHORITY_UNPROVEN'; END IF;
 SELECT * INTO r FROM portal_provider_admin.provider_relationship
 WHERE tenant=p_tenant AND issuer=m->>'issuer' AND subject=m->>'subject' FOR SHARE;
 now_at := clock_timestamp();
 until_at := (m->>'reviewed_until')::timestamptz;
 IF until_at IS NULL OR NOT isfinite(until_at) OR NOT FOUND OR r.proof_state NOT IN ('enabled','expired','revoked')
 OR r.principal_ref IS DISTINCT FROM m->>'principal_ref' OR r.provider_ref IS DISTINCT FROM c->>'provider_ref'
 OR (c->>'operation'='grant' AND (r.revoked OR r.proof_state IS DISTINCT FROM 'enabled'
     OR NOT(r.verified_at <= now_at AND now_at < r.valid_until)
     OR until_at > LEAST(r.valid_until,g.valid_until,p_until) OR until_at <= now_at))
 THEN RETURN 'AUTHORITY_UNPROVEN'; END IF;
 IF jsonb_array_length(m->'memberships')=0 THEN RETURN 'CONTRACT_MISMATCH'; END IF;
 FOR member IN SELECT value FROM jsonb_array_elements(m->'memberships') LOOP
   IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(member) k)
      IS DISTINCT FROM ARRAY['groups','membership_ref','roles']
   OR jsonb_typeof(member->'membership_ref') IS DISTINCT FROM 'string'
   OR jsonb_typeof(member->'roles') IS DISTINCT FROM 'array'
   OR jsonb_typeof(member->'groups') IS DISTINCT FROM 'array'
   OR length(member->>'membership_ref')=0 OR jsonb_array_length(member->'roles')=0
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(member->'roles') x
             WHERE jsonb_typeof(x) IS DISTINCT FROM 'string' OR length(x#>>'{}')=0)
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(member->'groups') x
             WHERE jsonb_typeof(x) IS DISTINCT FROM 'string' OR length(x#>>'{}')=0)
   OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(member->'roles') x WHERE NOT(x=ANY(g.allowed_roles)))
   OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(member->'groups') x WHERE NOT(x=ANY(g.allowed_groups)))
   THEN RETURN 'AUTHORITY_UNPROVEN'; END IF;
 END LOOP;
 IF (SELECT count(*) FROM jsonb_array_elements(m->'memberships') b)
 <> (SELECT count(DISTINCT b->>'membership_ref') FROM jsonb_array_elements(m->'memberships') b)
 OR (SELECT count(*) FROM jsonb_array_elements(m->'memberships') b, jsonb_array_elements_text(b->'roles') x)
 <> (SELECT count(DISTINCT x) FROM jsonb_array_elements(m->'memberships') b, jsonb_array_elements_text(b->'roles') x)
 OR (SELECT count(*) FROM jsonb_array_elements(m->'memberships') b, jsonb_array_elements_text(b->'groups') x)
 <> (SELECT count(DISTINCT x) FROM jsonb_array_elements(m->'memberships') b, jsonb_array_elements_text(b->'groups') x)
 THEN RETURN 'CONTRACT_MISMATCH'; END IF;
 keys := ARRAY[p_tenant||':principal:'||(m->>'principal_ref'),
               p_tenant||':identity:'||(m->>'issuer')||':'||(m->>'subject'),
               p_tenant||':command:'||(c->>'command_id')];
 FOR key IN SELECT DISTINCT unnest(keys) ORDER BY 1 LOOP
   PERFORM pg_advisory_xact_lock(hashtextextended(key,0));
 END LOOP;
 SELECT * INTO a FROM portal_provider_admin.administrative_act
 WHERE tenant=p_tenant AND command_id=c->>'command_id';
 IF FOUND THEN
   IF a.request_digest IS DISTINCT FROM digest THEN RETURN 'REVISION_CONFLICT'; END IF;
   IF clock_timestamp() >= LEAST(g.valid_until,p_until)
   OR (c->>'operation'='grant' AND clock_timestamp() >= r.valid_until)
   THEN RETURN 'AUTHORITY_UNPROVEN'; END IF;
   RETURN a.receipt;
 END IF;
 SELECT * INTO h FROM portal_provider_admin.administrative_head
 WHERE tenant=p_tenant AND (principal_ref=m->>'principal_ref' OR (issuer=m->>'issuer' AND subject=m->>'subject'))
 FOR UPDATE;
 IF FOUND THEN
   old_record := h.payload::jsonb;
   IF (SELECT count(*) FROM portal_provider_admin.administrative_head WHERE tenant=p_tenant
       AND (principal_ref=m->>'principal_ref' OR (issuer=m->>'issuer' AND subject=m->>'subject'))) <> 1
   OR h.principal_ref IS DISTINCT FROM m->>'principal_ref' OR h.issuer IS DISTINCT FROM m->>'issuer' OR h.subject IS DISTINCT FROM m->>'subject'
   OR old_record->>'audience' IS DISTINCT FROM 'provider' OR h.revision <> expected
   THEN RETURN 'REVISION_CONFLICT'; END IF;
   IF c->>'operation'='revoke' AND (
       (m - ARRAY['revision','revoked','reviewed_until'])
       IS DISTINCT FROM (old_record - ARRAY['revision','revoked','reviewed_until'])
       OR until_at > (old_record->>'reviewed_until')::timestamptz)
   THEN RETURN 'CONTRACT_MISMATCH'; END IF;
 ELSE
   IF expected <> 0 OR c->>'operation' <> 'grant' THEN RETURN 'REVISION_CONFLICT'; END IF;
 END IF;
 now_at := clock_timestamp();
 IF now_at >= LEAST(g.valid_until,p_until)
 OR (c->>'operation'='grant' AND now_at >= LEAST(r.valid_until,until_at))
 THEN RETURN 'AUTHORITY_UNPROVEN'; END IF;
 result := jsonb_build_object(
  'schema_version','provider-membership-administration-receipt.v1','tenant',p_tenant,
  'command_id',c->>'command_id','request_digest',digest,'principal_ref',m->>'principal_ref',
  'provider_ref',c->>'provider_ref','membership_revision',next_revision,
  'administrative_act_ref',p_act,'authority_receipt_ref',g.authority_receipt_ref,
  'relationship_receipt_ref',r.source_receipt_ref,'audit_receipt_ref',p_audit,
  'committed_at',now_at,'proof_state','validated','application_status','pending')::text;
 INSERT INTO portal_provider_admin.administrative_audit
 (tenant,audit_ref,command_id,actor_ref,request_digest,occurred_at)
 VALUES(p_tenant,p_audit,c->>'command_id',p_actor,digest,now_at);
 INSERT INTO portal_provider_admin.administrative_head
 (tenant,principal_ref,issuer,subject,revision,payload)
 VALUES(p_tenant,m->>'principal_ref',m->>'issuer',m->>'subject',next_revision,m::text)
 ON CONFLICT(tenant,principal_ref) DO UPDATE SET revision=EXCLUDED.revision,payload=EXCLUDED.payload;
 INSERT INTO portal_provider_admin.administrative_act(tenant,command_id,act_ref,request_digest,receipt)
 VALUES(p_tenant,c->>'command_id',p_act,digest,result);
 SELECT receipt INTO result FROM portal_provider_admin.administrative_act
 WHERE tenant=p_tenant AND command_id=c->>'command_id';
 RETURN result;
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range OR datetime_field_overflow
 OR invalid_parameter_value THEN RETURN 'CONTRACT_MISMATCH';
END;
$function$;
REVOKE ALL ON FUNCTION portal_provider_admin.record_provider_administration(
 text,text,text,text,text,text,timestamptz,text,text,text) FROM PUBLIC;
