-- Native v2 additive migration template. Render only with install_v2.py render-migration.
-- D-owned helper must already be independently installed and qualified. No D table is created here.
-- Transaction is owned by the migration executor. This file contains no COMMIT or runtime DDL.
-- Exact replay uses the generated catalogue verifier, never CREATE OR REPLACE or destructive down DDL.
CREATE SCHEMA maezo_native_v2 AUTHORIZATION __SCHEMA_OWNER__;
REVOKE ALL ON SCHEMA maezo_native_v2 FROM PUBLIC;
GRANT USAGE ON SCHEMA maezo_native_v2 TO __FUNCTION_OWNER__, __ISSUER_ROLE__, __RUNTIME_ROLE__;
-- D owner already granted the exact helper route; native installation only verifies it.

CREATE TABLE maezo_native_v2.schema_version (
  migration_key text PRIMARY KEY, manifest_digest text NOT NULL CHECK(manifest_digest ~ '^[0-9a-f]{64}$'),
  act_schema text NOT NULL, act_schema_oid oid NOT NULL, database_oid oid NOT NULL,
  database_binding_sha256 text NOT NULL CHECK(database_binding_sha256 ~ '^[0-9a-f]{64}$'),
  function_owner oid NOT NULL, issuer_role oid NOT NULL, d_helper_oid oid NOT NULL,
  abi_digest text NOT NULL CHECK(abi_digest ~ '^[0-9a-f]{64}$'), migration_receipt text NOT NULL,
  preparation_binding jsonb NOT NULL, preparation_receipt_bytes bytea NOT NULL CHECK(octet_length(preparation_receipt_bytes)<=1048576),
  catalogue_digest text NOT NULL CHECK(catalogue_digest ~ '^[0-9a-f]{64}$')
);
CREATE TABLE maezo_native_v2.admission (
  scope jsonb NOT NULL, activation_ref text NOT NULL, binding jsonb NOT NULL,
  login_name text NOT NULL, login_oid oid NOT NULL, runtime_generation bigint NOT NULL CHECK(runtime_generation>0),
  purpose text NOT NULL CHECK(purpose IN ('runtime','candidate')),
  state text NOT NULL CHECK(state IN ('prepared','admitted','retired')),
  proof jsonb NOT NULL, deadline bigint NOT NULL, revision bigint NOT NULL CHECK(revision>0),
  PRIMARY KEY(scope,activation_ref), CHECK(jsonb_typeof(scope)='object' AND octet_length(scope::text)<=4096),
  CHECK(octet_length(binding::text)<=1048576 AND octet_length(proof::text)<=16384)
);
CREATE TABLE maezo_native_v2.capability (
  scope jsonb NOT NULL, activation_ref text NOT NULL, capability_digest text NOT NULL CHECK(capability_digest ~ '^[0-9a-f]{64}$'),
  identity jsonb NOT NULL, native_user text NOT NULL, kind text NOT NULL CHECK(kind IN ('operation','outcome')),
  binding jsonb NOT NULL, qualification boolean NOT NULL,
  PRIMARY KEY(scope,activation_ref,capability_digest), CHECK(octet_length(binding::text)<=1048576)
);
CREATE TABLE maezo_native_v2.acquisition (
  scope jsonb NOT NULL, acquisition_ref text NOT NULL CHECK(acquisition_ref ~ '^[A-Za-z0-9_-]{43}$'), task_id text NOT NULL,
  binding jsonb NOT NULL, lease_revision bigint NOT NULL CHECK(lease_revision>0), lock_expires_at bigint NOT NULL,
  state text NOT NULL CHECK(state IN ('live','closed')), closed_by jsonb,
  PRIMARY KEY(scope,acquisition_ref), CHECK(octet_length(binding::text)<=16384),
  CHECK((state='live' AND closed_by IS NULL) OR (state='closed' AND closed_by IS NOT NULL))
);
CREATE UNIQUE INDEX acquisition_one_live_task ON maezo_native_v2.acquisition(scope,task_id) WHERE state='live';
CREATE TABLE maezo_native_v2.operation_receipt (
  scope jsonb NOT NULL, original_identity jsonb NOT NULL, command_id text NOT NULL CHECK(command_id ~ '^[A-Za-z0-9_-]{43}$'),
  command jsonb NOT NULL, receipt_ref text NOT NULL UNIQUE CHECK(receipt_ref ~ '^[A-Za-z0-9_-]{43}$'),
  receipt_bytes text NOT NULL CHECK(octet_length(receipt_bytes)<=1048576),
  receipt_digest text NOT NULL CHECK(receipt_digest ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY(scope,original_identity,command_id)
);
CREATE TABLE maezo_native_v2.receipt_history (
  scope jsonb PRIMARY KEY, state text NOT NULL CHECK(state IN ('complete','unavailable')),
  reconciliation_receipt_digest text NOT NULL CHECK(reconciliation_receipt_digest ~ '^[0-9a-f]{64}$'),
  issuer_revision bigint NOT NULL CHECK(issuer_revision>0)
);
ALTER TABLE maezo_native_v2.schema_version OWNER TO __SCHEMA_OWNER__;
ALTER TABLE maezo_native_v2.admission OWNER TO __SCHEMA_OWNER__;
ALTER TABLE maezo_native_v2.capability OWNER TO __SCHEMA_OWNER__;
ALTER TABLE maezo_native_v2.acquisition OWNER TO __SCHEMA_OWNER__;
ALTER TABLE maezo_native_v2.operation_receipt OWNER TO __SCHEMA_OWNER__;
ALTER TABLE maezo_native_v2.receipt_history OWNER TO __SCHEMA_OWNER__;
REVOKE ALL ON ALL TABLES IN SCHEMA maezo_native_v2 FROM PUBLIC, __RUNTIME_ROLE__, __ISSUER_ROLE__;
GRANT SELECT ON maezo_native_v2.schema_version TO __FUNCTION_OWNER__;
GRANT SELECT,INSERT,UPDATE ON maezo_native_v2.admission,maezo_native_v2.capability,maezo_native_v2.acquisition,maezo_native_v2.receipt_history TO __FUNCTION_OWNER__;
GRANT SELECT,INSERT ON maezo_native_v2.operation_receipt TO __FUNCTION_OWNER__;
-- PostgreSQL row locking requires UPDATE on at least one column. This exact technical
-- column grant supports R FOR UPDATE only; no function contains a receipt UPDATE path.
GRANT UPDATE(command_id) ON maezo_native_v2.operation_receipt TO __FUNCTION_OWNER__;
GRANT USAGE ON SCHEMA __ACT_SCHEMA__ TO __FUNCTION_OWNER__;
GRANT SELECT(id_,tenant_id_,proc_def_id_,proc_inst_id_,execution_id_,topic_name_,worker_id_,lock_exp_time_,suspension_state_,retries_,rev_) ON __ACT_SCHEMA__.act_ru_ext_task TO __FUNCTION_OWNER__;

CREATE FUNCTION maezo_native_v2.assert_keys(j jsonb,k text[]) RETURNS void LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF (j IS NULL OR jsonb_typeof(j)<>'object' OR (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(j) key) IS DISTINCT FROM (SELECT array_agg(v ORDER BY v COLLATE "C") FROM unnest(k) v)) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N01',MESSAGE='invalid_request'; END IF;
END $$;
CREATE FUNCTION maezo_native_v2.assert_scope(s jsonb) RETURNS void LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
DECLARE v text;
BEGIN
 PERFORM maezo_native_v2.assert_keys(s,ARRAY['tenant','environment','engine_name','database_incarnation']);
 FOR v IN SELECT value #>> '{}' FROM jsonb_each(s) LOOP IF v IS NULL OR v !~ '^[!-~]{1,256}$' OR position('*' in v)>0 THEN RAISE EXCEPTION USING ERRCODE='P7N01',MESSAGE='invalid_request'; END IF; END LOOP;
END $$;
CREATE FUNCTION maezo_native_v2.catalogue_v2() RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT jsonb_build_object('d_helper',(SELECT jsonb_build_object('oid',p.oid::bigint,'namespace_oid',p.pronamespace::bigint,'namespace_owner',(SELECT nspowner::bigint FROM pg_namespace WHERE oid=p.pronamespace),'namespace_acl',(SELECT nspacl::text FROM pg_namespace WHERE oid=p.pronamespace),'owner',p.proowner::bigint,'acl',p.proacl::text,'config',p.proconfig,'returns',p.prorettype::regtype::text,'arguments',oidvectortypes(p.proargtypes),'definer',p.prosecdef,'volatility',p.provolatile,'parallel',p.proparallel,'source',encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')) FROM pg_proc p WHERE p.oid=to_regprocedure('maezo_d7_control.lock_runtime_v2_scope(jsonb)')),'functions',(SELECT jsonb_agg(jsonb_build_object('name',p.proname,'oid',p.oid::bigint,'arguments',oidvectortypes(p.proargtypes),'returns',p.prorettype::regtype::text,'owner',p.proowner::bigint,'acl',p.proacl::text,'config',p.proconfig,'definer',p.prosecdef,'volatility',p.provolatile,'parallel',p.proparallel,'source',encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')) ORDER BY p.proname COLLATE "C",p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='maezo_native_v2'),
 'relations',(SELECT jsonb_agg(jsonb_build_object('name',c.relname,'oid',c.oid::bigint,'owner',c.relowner::bigint,'acl',c.relacl::text,'kind',c.relkind,'rls',c.relrowsecurity,'columns',(SELECT jsonb_agg(jsonb_build_object('name',a.attname,'type',a.atttypid::regtype::text,'notnull',a.attnotnull,'acl',a.attacl::text) ORDER BY a.attnum) FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),'constraints',(SELECT jsonb_agg(pg_get_constraintdef(k.oid) ORDER BY k.conname COLLATE "C") FROM pg_constraint k WHERE k.conrelid=c.oid)) ORDER BY c.relname COLLATE "C") FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='maezo_native_v2'),
 'namespace',(SELECT jsonb_build_object('oid',oid::bigint,'owner',nspowner::bigint,'acl',nspacl::text) FROM pg_namespace WHERE nspname='maezo_native_v2'))
$$;
CREATE FUNCTION maezo_native_v2.assert_owner() RETURNS maezo_native_v2.schema_version LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v maezo_native_v2.schema_version; actual oid;
BEGIN
 SELECT * INTO STRICT v FROM maezo_native_v2.schema_version WHERE migration_key='native-acquisition-v2';
 SELECT oid INTO STRICT actual FROM pg_roles WHERE rolname=current_user AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication AND NOT rolbypassrls;
 IF pg_has_role(session_user,v.function_owner,'MEMBER') OR pg_has_role(session_user,(SELECT nspowner FROM pg_namespace WHERE nspname='maezo_native_v2'),'MEMBER') OR pg_has_role(session_user,(SELECT proowner FROM pg_proc WHERE oid=v.d_helper_oid),'MEMBER') THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 IF (actual<>v.function_owner OR v.database_oid<>(SELECT oid FROM pg_database WHERE datname=current_database()) OR v.act_schema_oid<>(SELECT oid FROM pg_namespace WHERE nspname=v.act_schema) OR v.d_helper_oid<>to_regprocedure('maezo_d7_control.lock_runtime_v2_scope(jsonb)')::oid) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N02',MESSAGE='unavailable'; END IF;
 IF (encode(sha256(convert_to(maezo_native_v2.catalogue_v2()::text,'UTF8')),'hex')<>v.catalogue_digest) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N02',MESSAGE='unavailable';END IF;
 RETURN v;
END $$;
CREATE FUNCTION maezo_native_v2.d_guard(s jsonb,e jsonb,d jsonb,a text) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE q jsonb; r jsonb;
BEGIN
 PERFORM maezo_native_v2.assert_owner();PERFORM maezo_native_v2.assert_scope(s);
 q:=jsonb_build_object('protocol','maezo.d7-native-guard-request.v1','account',e->'account','region',e->'region','tenant',s->'tenant','environment',s->'environment','engine_name',s->'engine_name','database_incarnation',s->'database_incarnation','database_binding_sha256',d->'database_binding_sha256','runtime_generation',e->'runtime_generation','epoch',e->'epoch','decision_digest',e->'decision_digest','purpose',e->'purpose','admission_phase',e->'admission_phase','action',a);
 r:=maezo_d7_control.lock_runtime_v2_scope(q);
 PERFORM maezo_native_v2.assert_keys(r,ARRAY['protocol','account','region','tenant','environment','engine_name','database_incarnation','database_binding_sha256','runtime_generation','epoch','decision_digest','purpose','admission_phase','generation_epoch','fence_revision','generation_revision','login_name','login_oid','valid_until','observation_digest','observation_deadline','restore_receipt_digest','readiness_digest','journal_revision','journal_digest','generation_binding_digest','trust_profile_digest','admission_proof_digest','database_clock_ms']);
 IF (r->>'protocol'<>'maezo.d7-native-guard-result.v1' OR (r - ARRAY['protocol','generation_epoch','fence_revision','generation_revision','login_name','login_oid','valid_until','observation_digest','observation_deadline','restore_receipt_digest','readiness_digest','journal_revision','journal_digest','generation_binding_digest','trust_profile_digest','admission_proof_digest','database_clock_ms']) IS DISTINCT FROM (q - ARRAY['protocol','action'])) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N02',MESSAGE='unavailable'; END IF;
 RETURN r;
END $$;
CREATE FUNCTION maezo_native_v2.guard_runtime_v2(p jsonb) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE d jsonb;a maezo_native_v2.admission;c maezo_native_v2.capability;v maezo_native_v2.schema_version;g jsonb;deadline bigint;now_ms bigint;bag jsonb;login oid;kind text;
BEGIN
 PERFORM maezo_native_v2.assert_keys(p,ARRAY['scope','expected','database','policy_digest','schema_digest','identity','native_user','login_name','login_oid','capability_digest','capability_binding','kind']);
 v:=maezo_native_v2.assert_owner();SELECT oid INTO STRICT login FROM pg_roles WHERE rolname=session_user;
 PERFORM set_config('lock_timeout','1000ms',true);PERFORM set_config('statement_timeout','5000ms',true);
 IF (p->>'login_name'<>session_user OR (p->>'login_oid')::oid<>login OR p->'database'->>'migration_digest'<>v.manifest_digest OR (p->'database'->>'database_oid')::oid<>v.database_oid OR (p->'database'->>'act_schema_oid')::oid<>v.act_schema_oid OR p->'database'->>'act_schema'<>v.act_schema OR p->'database'->>'database_binding_sha256'<>v.database_binding_sha256) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 kind:=p->>'kind';IF (kind NOT IN ('operation','outcome','readiness')) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 -- The only first lock acquisition: D helper F, then G. This retains both locks in this transaction.
 d:=maezo_native_v2.d_guard(p->'scope',p->'expected',p->'database',CASE WHEN kind='outcome' THEN 'native_history' ELSE 'native_guard' END);
 SELECT * INTO STRICT a FROM maezo_native_v2.admission WHERE scope=p->'scope' AND activation_ref=p->'expected'->>'activation_ref' FOR UPDATE;
 SELECT * INTO STRICT c FROM maezo_native_v2.capability WHERE scope=a.scope AND activation_ref=a.activation_ref AND capability_digest=p->>'capability_digest' FOR UPDATE;
 now_ms:=(extract(epoch FROM clock_timestamp())*1000)::bigint;
 IF (a.state<>'admitted' OR a.login_oid<>login OR a.login_name<>session_user OR a.binding->'expected' IS DISTINCT FROM p->'expected' OR a.binding->'database' IS DISTINCT FROM p->'database' OR a.binding->>'policy_digest'<>p->>'policy_digest' OR a.binding->>'schema_digest'<>p->>'schema_digest' OR c.identity IS DISTINCT FROM p->'identity' OR c.native_user<>p->>'native_user' OR c.binding IS DISTINCT FROM p->'capability_binding' OR c.kind<>CASE WHEN kind='outcome' THEN 'outcome' ELSE 'operation' END OR (p->'expected'->>'admission_phase'<>'ACTIVE' AND NOT c.qualification) OR d->>'login_name'<>session_user OR (d->>'login_oid')::oid<>login OR a.proof->>'generation_binding_digest'<>d->>'generation_binding_digest' OR a.proof->>'admission_proof_digest' IS DISTINCT FROM d->>'admission_proof_digest') IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 deadline:=least(a.deadline,(d->>'valid_until')::bigint,(d->>'observation_deadline')::bigint);
 IF (deadline IS NULL OR now_ms>=deadline) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N02',MESSAGE='unavailable';END IF;
 g:=jsonb_build_object('scope',a.scope,'activation_ref',a.activation_ref,'runtime_generation',a.runtime_generation,'decision_digest',a.binding->'expected'->'decision_digest','identity',c.identity,'native_user',c.native_user,'capability_digest',c.capability_digest,'kind',kind,'deadline',deadline,'lock_timeout',least((a.binding->>'lock_timeout')::bigint,deadline-now_ms),'statement_timeout',least((a.binding->>'statement_timeout')::bigint,deadline-now_ms),'guard_ref',rtrim(translate(encode(sha256(convert_to(txid_current()::text||a.scope::text||c.capability_digest||clock_timestamp()::text,'UTF8')),'base64'),'+/','-_'),'='));
 -- Transient transaction-local context, never durable authority; qualified runtime/JVM is trusted.
 -- Subsequent function work checks this captured deadline and does not reacquire F/G after P/T/L.
 bag:=coalesce(nullif(current_setting('maezo_native_v2.guards',true),''),'{}')::jsonb;
 PERFORM set_config('maezo_native_v2.guards',(bag||jsonb_build_object(g->>'guard_ref',jsonb_build_object('transaction',txid_current(),'guard',g)))::text,true);
 RETURN g;
END $$;
CREATE FUNCTION maezo_native_v2.check_guard(g jsonb) RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE b jsonb;a maezo_native_v2.admission;login oid;d jsonb;
BEGIN
 PERFORM maezo_native_v2.assert_owner();b:=coalesce(nullif(current_setting('maezo_native_v2.guards',true),''),'{}')::jsonb->(g->>'guard_ref');
 IF (b->'guard' IS DISTINCT FROM g OR (b->>'transaction')::bigint IS DISTINCT FROM txid_current() OR (extract(epoch FROM clock_timestamp())*1000)::bigint >= (g->>'deadline')::bigint) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N02',MESSAGE='unavailable';END IF;
 SELECT oid INTO STRICT login FROM pg_roles WHERE rolname=session_user;
 SELECT * INTO STRICT a FROM maezo_native_v2.admission WHERE scope=g->'scope' AND activation_ref=g->>'activation_ref';
 IF (a.state<>'admitted' OR a.login_oid<>login OR a.login_name<>session_user OR a.runtime_generation<>(g->>'runtime_generation')::bigint OR a.binding->'expected'->>'decision_digest'<>g->>'decision_digest') IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 -- Re-enter only the exact F/G locks already held by this guard. Revalidate D's DB clock,
 -- current catalog/phase/proof without extending the original captured deadline.
 d:=maezo_native_v2.d_guard(a.scope,a.binding->'expected',a.binding->'database',CASE WHEN g->>'kind'='outcome' THEN 'native_history' ELSE 'native_guard' END);
 IF (a.proof->>'generation_binding_digest' IS DISTINCT FROM d->>'generation_binding_digest' OR a.proof->>'admission_proof_digest' IS DISTINCT FROM d->>'admission_proof_digest' OR d->>'login_name' IS DISTINCT FROM session_user OR (d->>'login_oid')::oid IS DISTINCT FROM login) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N02',MESSAGE='unavailable';END IF;
END $$;
CREATE FUNCTION maezo_native_v2.validate_guard_v2(p jsonb) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 PERFORM maezo_native_v2.assert_keys(p,ARRAY['guard']);PERFORM maezo_native_v2.check_guard(p->'guard');RETURN jsonb_build_object('valid',true);
END $$;
CREATE FUNCTION maezo_native_v2.read_receipt_v2(p jsonb) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE g jsonb;cmd jsonb;s jsonb;r maezo_native_v2.operation_receipt;c maezo_native_v2.capability;h text;
BEGIN
 PERFORM maezo_native_v2.assert_keys(p,ARRAY['guard','command','history']);g:=p->'guard';cmd:=p->'command';PERFORM maezo_native_v2.check_guard(g);
 PERFORM maezo_native_v2.assert_keys(cmd,ARRAY['engine','database_incarnation','identity','native_user','operation','capability_digest','command_id','request_digest','activation_ref']);
 s:=jsonb_build_object('tenant',cmd->'identity'->'tenant','environment',cmd->'identity'->'environment','engine_name',cmd->'engine','database_incarnation',cmd->'database_incarnation');PERFORM maezo_native_v2.assert_scope(s);
 IF (p->>'history')::boolean THEN
   SELECT * INTO STRICT c FROM maezo_native_v2.capability WHERE scope=g->'scope' AND activation_ref=g->>'activation_ref' AND capability_digest=g->>'capability_digest';
   IF (c.kind<>'outcome' OR c.binding->'document'->'selector' IS DISTINCT FROM (cmd - ARRAY['command_id','request_digest','activation_ref'])) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 ELSE
   IF (s IS DISTINCT FROM g->'scope' OR cmd->'identity' IS DISTINCT FROM g->'identity' OR cmd->>'native_user'<>g->>'native_user' OR cmd->>'capability_digest'<>g->>'capability_digest' OR cmd->>'activation_ref'<>g->>'activation_ref') IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 END IF;
 -- R follows existing F/G/A. An absent key remains protected by the scope fence.
 SELECT * INTO r FROM maezo_native_v2.operation_receipt WHERE scope=s AND original_identity=cmd->'identity' AND command_id=cmd->>'command_id' FOR UPDATE;
 IF FOUND THEN
   IF r.command IS DISTINCT FROM cmd THEN RETURN jsonb_build_object('status','conflict','receipt',NULL);END IF;
   IF (encode(sha256(convert_to(r.receipt_bytes,'UTF8')),'hex')<>r.receipt_digest) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N02',MESSAGE='unavailable';END IF;
   RETURN jsonb_build_object('status','committed','receipt',r.receipt_bytes::jsonb);
 END IF;
 SELECT state INTO h FROM maezo_native_v2.receipt_history WHERE scope=s;
 IF h IS DISTINCT FROM 'complete' THEN RETURN jsonb_build_object('status','unavailable','receipt',NULL);END IF;
 RETURN jsonb_build_object('status','not_observed','receipt',NULL);
END $$;
CREATE FUNCTION maezo_native_v2.read_acquisitions_v2(p jsonb) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE r maezo_native_v2.acquisition;result jsonb:='[]';g jsonb;selector jsonb;
BEGIN
 PERFORM maezo_native_v2.assert_keys(p,ARRAY['guard','references','fetch_task_ids']);g:=p->'guard';PERFORM maezo_native_v2.check_guard(g);
 IF (jsonb_typeof(p->'references')<>'array' OR jsonb_typeof(p->'fetch_task_ids')<>'array' OR jsonb_array_length(p->'references')>2 OR (jsonb_array_length(p->'references')>0 AND jsonb_array_length(p->'fetch_task_ids')>0)) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N01',MESSAGE='invalid_request';END IF;
 FOR selector IN SELECT value FROM jsonb_array_elements(p->'references') LOOP
  PERFORM maezo_native_v2.assert_keys(selector,ARRAY['task_id','reference']);
  PERFORM maezo_native_v2.assert_keys(selector->'reference',ARRAY['acquisition_ref','lease_revision']);
  IF (jsonb_typeof(selector->'task_id')<>'string' OR selector->>'task_id'='' OR jsonb_typeof(selector->'reference'->'acquisition_ref')<>'string' OR selector->'reference'->>'acquisition_ref' !~ '^[A-Za-z0-9_-]{43}$' OR jsonb_typeof(selector->'reference'->'lease_revision')<>'number' OR selector->'reference'->>'lease_revision' !~ '^[1-9][0-9]*$' OR (selector->'reference'->>'lease_revision')::numeric>9223372036854775807) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N01',MESSAGE='invalid_request';END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p->'fetch_task_ids') x WHERE jsonb_typeof(x)<>'string' OR x#>>'{}'='') OR EXISTS(SELECT 1 FROM jsonb_array_elements(p->'references') x GROUP BY x->>'task_id' HAVING count(*)>1) OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(p->'fetch_task_ids') x GROUP BY x HAVING count(*)>1) THEN RAISE EXCEPTION USING ERRCODE='P7N01',MESSAGE='invalid_request';END IF;
 -- Only exact current consumed refs, or the one live predecessor per genuinely fetched task.
 -- A predecessor from an older generation must be retired too, but unrelated history is never selected.
 FOR r IN SELECT a.* FROM maezo_native_v2.acquisition a
 WHERE a.scope=g->'scope' AND a.state='live' AND (
  (a.binding->'runtime_generation'=g->'runtime_generation' AND a.binding->>'activation_ref'=g->>'activation_ref' AND a.binding->>'decision_digest'=g->>'decision_digest' AND EXISTS(SELECT 1 FROM jsonb_array_elements(p->'references') s WHERE a.task_id=s->>'task_id' AND a.acquisition_ref=s->'reference'->>'acquisition_ref' AND a.lease_revision=(s->'reference'->>'lease_revision')::bigint))
  OR a.task_id IN (SELECT jsonb_array_elements_text(p->'fetch_task_ids')))
 ORDER BY a.task_id COLLATE "C",a.acquisition_ref COLLATE "C" FOR UPDATE OF a LOOP
  result:=result||jsonb_build_array(r.binding||jsonb_build_object('acquisition_ref',r.acquisition_ref,'task_id',r.task_id,'lease_revision',r.lease_revision,'lock_expires_at',r.lock_expires_at,'state',r.state));
 END LOOP;RETURN jsonb_build_object('acquisitions',result);
END $$;
CREATE FUNCTION maezo_native_v2.native_task(id text,s jsonb) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v maezo_native_v2.schema_version;r jsonb;
BEGIN
 v:=maezo_native_v2.assert_owner();
 -- Identifier comes exclusively from installed migration/OID custody, never a request field.
 EXECUTE format('SELECT jsonb_build_object(''task_id'',id_,''worker_id'',worker_id_,''definition_id'',proc_def_id_,''topic'',topic_name_,''process_instance_id'',proc_inst_id_,''execution_id'',execution_id_,''expiry'',(extract(epoch FROM lock_exp_time_)*1000)::bigint,''suspension'',suspension_state_) FROM %I.act_ru_ext_task WHERE id_=$1 AND tenant_id_=$2',v.act_schema) INTO r USING id,s->>'tenant';
 RETURN r;
END $$;
CREATE FUNCTION maezo_native_v2.insert_acquisition_v2(p jsonb) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE g jsonb;a jsonb;t jsonb;binding jsonb;c maezo_native_v2.capability;previous maezo_native_v2.acquisition;
BEGIN
 PERFORM maezo_native_v2.assert_keys(p,ARRAY['guard','acquisition','predecessor']);g:=p->'guard';a:=p->'acquisition';PERFORM maezo_native_v2.check_guard(g);
 PERFORM maezo_native_v2.assert_keys(a,ARRAY['acquisition_ref','task_id','owner_identity','native_user','worker_id','target','process_instance_id','execution_id','activation_ref','runtime_generation','decision_digest','fetch_capability_digest','command_id','request_digest','lease_revision','lock_expires_at','state']);
 SELECT * INTO STRICT c FROM maezo_native_v2.capability WHERE scope=g->'scope' AND activation_ref=g->>'activation_ref' AND capability_digest=g->>'capability_digest';
 t:=maezo_native_v2.native_task(a->>'task_id',g->'scope');
 IF (c.binding->'document'->'schema'->>'operation'<>'fetch_lock' OR a->'owner_identity' IS DISTINCT FROM g->'identity' OR a->>'native_user'<>g->>'native_user' OR a->>'activation_ref'<>g->>'activation_ref' OR a->'runtime_generation' IS DISTINCT FROM g->'runtime_generation' OR a->>'decision_digest'<>g->>'decision_digest' OR a->>'fetch_capability_digest'<>g->>'capability_digest' OR a->'target' IS DISTINCT FROM c.binding->'document'->'target' OR a->>'worker_id'<>c.binding->'document'->>'worker_id' OR a->>'state'<>'live' OR a->>'lease_revision'<>'1' OR t IS NULL OR t->>'worker_id'<>a->>'worker_id' OR t->>'definition_id'<>a->'target'->>'definition_id' OR t->>'topic'<>a->'target'->>'topic' OR t->'process_instance_id' IS DISTINCT FROM a->'process_instance_id' OR t->'execution_id' IS DISTINCT FROM a->'execution_id' OR t->'expiry' IS DISTINCT FROM a->'lock_expires_at' OR (t->>'expiry')::bigint<=(extract(epoch FROM clock_timestamp())*1000)::bigint OR (t->'suspension'<>'null'::jsonb AND t->>'suspension'<>'1')) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 -- T/L were captured before the native fetch. Close exactly that retained predecessor.
 SELECT * INTO previous FROM maezo_native_v2.acquisition WHERE scope=g->'scope' AND task_id=a->>'task_id' AND state='live';
 IF p->'predecessor'='null'::jsonb THEN
  IF FOUND THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 ELSE
  PERFORM maezo_native_v2.assert_keys(p->'predecessor',ARRAY['acquisition_ref','lease_revision']);
  IF (previous.acquisition_ref IS NULL OR previous.acquisition_ref<>p->'predecessor'->>'acquisition_ref' OR previous.lease_revision<>(p->'predecessor'->>'lease_revision')::bigint) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 END IF;
 UPDATE maezo_native_v2.acquisition SET state='closed',closed_by=jsonb_build_object('engine',g->'scope'->'engine_name','database_incarnation',g->'scope'->'database_incarnation','identity',g->'identity','native_user',g->'native_user','operation','fetch_lock','capability_digest',g->'capability_digest','command_id',a->'command_id','request_digest',a->'request_digest','activation_ref',g->'activation_ref') WHERE scope=g->'scope' AND task_id=a->>'task_id' AND state='live' AND acquisition_ref=previous.acquisition_ref AND lease_revision=previous.lease_revision;
 binding:=a-ARRAY['acquisition_ref','task_id','lease_revision','lock_expires_at','state'];
 INSERT INTO maezo_native_v2.acquisition VALUES(g->'scope',a->>'acquisition_ref',a->>'task_id',binding,1,(a->>'lock_expires_at')::bigint,'live',NULL);
 RETURN a;
END $$;
CREATE FUNCTION maezo_native_v2.renew_acquisition_v2(p jsonb) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE g jsonb;r maezo_native_v2.acquisition;t jsonb;
BEGIN
 PERFORM maezo_native_v2.assert_keys(p,ARRAY['guard','reference','task_id','lock_expires_at','command']);g:=p->'guard';PERFORM maezo_native_v2.check_guard(g);
 SELECT * INTO STRICT r FROM maezo_native_v2.acquisition WHERE scope=g->'scope' AND acquisition_ref=p->'reference'->>'acquisition_ref';
 t:=maezo_native_v2.native_task(p->>'task_id',g->'scope');
 IF (r.state<>'live' OR r.task_id<>p->>'task_id' OR r.lease_revision<>(p->'reference'->>'lease_revision')::bigint OR r.lease_revision=9223372036854775807 OR r.binding->'owner_identity' IS DISTINCT FROM g->'identity' OR r.binding->>'native_user'<>g->>'native_user' OR r.binding->>'activation_ref'<>g->>'activation_ref' OR t IS NULL OR t->>'worker_id'<>r.binding->>'worker_id' OR t->>'definition_id'<>r.binding->'target'->>'definition_id' OR t->>'topic'<>r.binding->'target'->>'topic' OR t->'process_instance_id' IS DISTINCT FROM r.binding->'process_instance_id' OR t->'execution_id' IS DISTINCT FROM r.binding->'execution_id' OR t->'expiry' IS DISTINCT FROM p->'lock_expires_at' OR (t->>'expiry')::bigint<=(extract(epoch FROM clock_timestamp())*1000)::bigint OR p->'command'->>'operation'<>'external_extend_lock') IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 UPDATE maezo_native_v2.acquisition SET lease_revision=lease_revision+1,lock_expires_at=(p->>'lock_expires_at')::bigint WHERE scope=r.scope AND acquisition_ref=r.acquisition_ref RETURNING * INTO r;
 RETURN r.binding||jsonb_build_object('acquisition_ref',r.acquisition_ref,'task_id',r.task_id,'lease_revision',r.lease_revision,'lock_expires_at',r.lock_expires_at,'state',r.state);
END $$;
CREATE FUNCTION maezo_native_v2.close_acquisition_v2(p jsonb) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE g jsonb;r maezo_native_v2.acquisition;
BEGIN
 PERFORM maezo_native_v2.assert_keys(p,ARRAY['guard','reference','task_id','command']);g:=p->'guard';PERFORM maezo_native_v2.check_guard(g);
 SELECT * INTO STRICT r FROM maezo_native_v2.acquisition WHERE scope=g->'scope' AND acquisition_ref=p->'reference'->>'acquisition_ref';
 IF (r.state<>'live' OR r.task_id<>p->>'task_id' OR r.lease_revision<>(p->'reference'->>'lease_revision')::bigint OR r.binding->'owner_identity' IS DISTINCT FROM g->'identity' OR r.binding->>'native_user'<>g->>'native_user' OR r.binding->>'activation_ref'<>g->>'activation_ref' OR p->'command'->>'operation' NOT IN ('external_complete','external_failure','external_bpmn_error','external_unlock')) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 UPDATE maezo_native_v2.acquisition SET state='closed',closed_by=p->'command' WHERE scope=r.scope AND acquisition_ref=r.acquisition_ref RETURNING * INTO r;
 RETURN r.binding||jsonb_build_object('acquisition_ref',r.acquisition_ref,'task_id',r.task_id,'lease_revision',r.lease_revision,'lock_expires_at',r.lock_expires_at,'state',r.state);
END $$;
CREATE FUNCTION maezo_native_v2.append_receipt_v2(p jsonb) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE g jsonb;r jsonb;c jsonb;stored maezo_native_v2.operation_receipt;s jsonb;a maezo_native_v2.acquisition;
BEGIN
 PERFORM maezo_native_v2.assert_keys(p,ARRAY['guard','receipt','receipt_bytes','receipt_digest']);g:=p->'guard';r:=p->'receipt';c:=r->'command';PERFORM maezo_native_v2.check_guard(g);
 PERFORM maezo_native_v2.assert_keys(r,ARRAY['receipt_ref','state','command','resource_ref_digest','source_ref_digest','resource_acquisition','source_acquisition','acquisitions']);
 PERFORM maezo_native_v2.assert_keys(c,ARRAY['engine','database_incarnation','identity','native_user','operation','capability_digest','command_id','request_digest','activation_ref']);
 IF (r->>'state'<>'committed' OR c->'identity' IS DISTINCT FROM g->'identity' OR c->>'native_user'<>g->>'native_user' OR c->>'activation_ref'<>g->>'activation_ref' OR c->>'capability_digest'<>g->>'capability_digest' OR c->>'engine'<>g->'scope'->>'engine_name' OR c->>'database_incarnation'<>g->'scope'->>'database_incarnation' OR p->>'receipt_digest'<>encode(sha256(convert_to(p->>'receipt_bytes','UTF8')),'hex') OR (p->>'receipt_bytes')::jsonb IS DISTINCT FROM r) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 FOR s IN SELECT value FROM jsonb_array_elements(r->'acquisitions') LOOP
  PERFORM maezo_native_v2.assert_keys(s,ARRAY['role','task_ref','acquisition_ref','lease_revision','lock_expires_at','state']);
  SELECT * INTO STRICT a FROM maezo_native_v2.acquisition WHERE scope=g->'scope' AND acquisition_ref=s->>'acquisition_ref';
  IF (a.task_id<>s->>'task_ref' OR a.lease_revision<>(s->>'lease_revision')::bigint OR a.lock_expires_at<>(s->>'lock_expires_at')::bigint OR a.state<>s->>'state') IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 END LOOP;
 SELECT * INTO stored FROM maezo_native_v2.operation_receipt WHERE scope=g->'scope' AND original_identity=c->'identity' AND command_id=c->>'command_id';
 IF FOUND THEN IF (stored.command IS DISTINCT FROM c OR stored.receipt_bytes<>p->>'receipt_bytes') IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N04',MESSAGE='conflict';END IF;RETURN stored.receipt_bytes::jsonb;END IF;
 INSERT INTO maezo_native_v2.operation_receipt VALUES(g->'scope',c->'identity',c->>'command_id',c,r->>'receipt_ref',p->>'receipt_bytes',p->>'receipt_digest');RETURN r;
END $$;
CREATE FUNCTION maezo_native_v2.register_admission_v2(p jsonb) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v maezo_native_v2.schema_version;proof jsonb;old maezo_native_v2.admission;entry jsonb;binding jsonb;deadline bigint;
BEGIN
 PERFORM maezo_native_v2.assert_keys(p,ARRAY['scope','expected','database','policy_digest','schema_digest','capabilities','lock_timeout','statement_timeout','legacy_closure_digest']);
 v:=maezo_native_v2.assert_owner();IF (SELECT oid FROM pg_roles WHERE rolname=session_user)<>v.issuer_role THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 proof:=maezo_native_v2.d_guard(p->'scope',p->'expected',p->'database','native_register');
 IF (p->>'legacy_closure_digest' !~ '^[0-9a-f]{64}$' OR p->>'policy_digest' !~ '^[0-9a-f]{64}$' OR p->>'schema_digest' !~ '^[0-9a-f]{64}$' OR (p->>'lock_timeout')::bigint<1 OR (p->>'statement_timeout')::bigint<(p->>'lock_timeout')::bigint OR jsonb_array_length(p->'capabilities')<1 OR p->'database'->>'migration_digest'<>v.manifest_digest OR p->'database'->>'database_binding_sha256'<>v.database_binding_sha256) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 deadline:=least((proof->>'valid_until')::bigint,(proof->>'observation_deadline')::bigint);
 IF (deadline IS NULL OR deadline<=(extract(epoch FROM clock_timestamp())*1000)::bigint) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N02',MESSAGE='unavailable';END IF;
 binding:=p-ARRAY['scope','capabilities'];
 SELECT * INTO old FROM maezo_native_v2.admission WHERE scope=p->'scope' AND activation_ref=p->'expected'->>'activation_ref' FOR UPDATE;
 IF FOUND THEN
  IF (old.state='retired' OR old.binding IS DISTINCT FROM binding OR old.login_oid<>(proof->>'login_oid')::oid) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N04',MESSAGE='conflict';END IF;
  IF ((SELECT count(*) FROM maezo_native_v2.capability WHERE scope=old.scope AND activation_ref=old.activation_ref)<>jsonb_array_length(p->'capabilities')) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N04',MESSAGE='conflict';END IF;
 ELSE
  INSERT INTO maezo_native_v2.admission VALUES(p->'scope',p->'expected'->>'activation_ref',binding,proof->>'login_name',(proof->>'login_oid')::oid,(proof->>'runtime_generation')::bigint,proof->>'purpose','admitted',proof,deadline,1);
 END IF;
 FOR entry IN SELECT value FROM jsonb_array_elements(p->'capabilities') ORDER BY value->>'digest' COLLATE "C" LOOP
  PERFORM maezo_native_v2.assert_keys(entry,ARRAY['digest','identity','native_user','kind','binding','qualification']);
  IF (entry->>'digest' !~ '^[0-9a-f]{64}$' OR entry->'identity'->'tenant' IS DISTINCT FROM p->'scope'->'tenant' OR entry->'identity'->'environment' IS DISTINCT FROM p->'scope'->'environment' OR entry->>'kind' NOT IN ('operation','outcome') OR jsonb_typeof(entry->'qualification')<>'boolean' OR (proof->>'admission_phase'<>'ACTIVE' AND (entry->>'qualification')::boolean IS NOT TRUE)) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
  IF EXISTS(SELECT 1 FROM maezo_native_v2.capability WHERE scope=p->'scope' AND activation_ref=p->'expected'->>'activation_ref' AND capability_digest=entry->>'digest') THEN
   IF (NOT EXISTS(SELECT 1 FROM maezo_native_v2.capability WHERE scope=p->'scope' AND activation_ref=p->'expected'->>'activation_ref' AND capability_digest=entry->>'digest' AND identity=entry->'identity' AND native_user=entry->>'native_user' AND kind=entry->>'kind' AND binding=entry->'binding' AND qualification=(entry->>'qualification')::boolean)) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N04',MESSAGE='conflict';END IF;
  ELSE INSERT INTO maezo_native_v2.capability VALUES(p->'scope',p->'expected'->>'activation_ref',entry->>'digest',entry->'identity',entry->>'native_user',entry->>'kind',entry->'binding',(entry->>'qualification')::boolean);END IF;
 END LOOP;
 RETURN jsonb_build_object('activation_ref',p->'expected'->'activation_ref','state','admitted');
END $$;
CREATE FUNCTION maezo_native_v2.refresh_admission_v2(p jsonb) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v maezo_native_v2.schema_version;fresh jsonb;a maezo_native_v2.admission;new_deadline bigint;
BEGIN
 PERFORM maezo_native_v2.assert_keys(p,ARRAY['scope','expected','database','expected_revision']);v:=maezo_native_v2.assert_owner();
 IF ((SELECT oid FROM pg_roles WHERE rolname=session_user)<>v.issuer_role) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 fresh:=maezo_native_v2.d_guard(p->'scope',p->'expected',p->'database','native_refresh');
 SELECT * INTO STRICT a FROM maezo_native_v2.admission WHERE scope=p->'scope' AND activation_ref=p->'expected'->>'activation_ref' FOR UPDATE;
 IF (a.state<>'admitted' OR a.revision<>(p->>'expected_revision')::bigint OR a.revision=9223372036854775807 OR a.binding->'expected' IS DISTINCT FROM p->'expected' OR a.binding->'database' IS DISTINCT FROM p->'database' OR a.proof->>'generation_binding_digest'<>fresh->>'generation_binding_digest' OR a.login_oid<>(fresh->>'login_oid')::oid) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N04',MESSAGE='conflict';END IF;
 new_deadline:=least((fresh->>'valid_until')::bigint,(fresh->>'observation_deadline')::bigint);IF new_deadline IS NULL OR new_deadline<=(extract(epoch FROM clock_timestamp())*1000)::bigint THEN RAISE EXCEPTION USING ERRCODE='P7N02',MESSAGE='unavailable';END IF;
 UPDATE maezo_native_v2.admission SET proof=fresh,deadline=new_deadline,revision=revision+1 WHERE scope=a.scope AND activation_ref=a.activation_ref;
 RETURN jsonb_build_object('activation_ref',a.activation_ref,'revision',a.revision+1,'deadline',new_deadline);
END $$;
CREATE FUNCTION maezo_native_v2.retire_admission_v2(p jsonb) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v maezo_native_v2.schema_version;proof jsonb;a maezo_native_v2.admission;
BEGIN
 PERFORM maezo_native_v2.assert_keys(p,ARRAY['scope','expected','database']);v:=maezo_native_v2.assert_owner();
 IF ((SELECT oid FROM pg_roles WHERE rolname=session_user)<>v.issuer_role) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 proof:=maezo_native_v2.d_guard(p->'scope',p->'expected',p->'database','native_retire');
 IF (proof->>'admission_phase'<>'RETIREMENT') IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 SELECT * INTO STRICT a FROM maezo_native_v2.admission WHERE scope=p->'scope' AND activation_ref=p->'expected'->>'activation_ref' FOR UPDATE;
 IF (a.runtime_generation<>(proof->>'runtime_generation')::bigint OR a.proof->>'generation_binding_digest'<>proof->>'generation_binding_digest') IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N04',MESSAGE='conflict';END IF;
 UPDATE maezo_native_v2.admission SET state='retired' WHERE scope=a.scope AND activation_ref=a.activation_ref;
 RETURN jsonb_build_object('activation_ref',a.activation_ref,'state','retired');
END $$;
CREATE FUNCTION maezo_native_v2.set_receipt_history_v2(p jsonb) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v maezo_native_v2.schema_version;proof jsonb;old maezo_native_v2.receipt_history;
BEGIN
 PERFORM maezo_native_v2.assert_keys(p,ARRAY['scope','expected','database','historical_scope','state','reconciliation_receipt_digest','issuer_revision']);v:=maezo_native_v2.assert_owner();
 IF ((SELECT oid FROM pg_roles WHERE rolname=session_user)<>v.issuer_role) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 proof:=maezo_native_v2.d_guard(p->'scope',p->'expected',p->'database','native_register');PERFORM maezo_native_v2.assert_scope(p->'historical_scope');
 IF (p->>'state' NOT IN ('complete','unavailable') OR p->>'reconciliation_receipt_digest' !~ '^[0-9a-f]{64}$' OR (p->>'issuer_revision')::bigint<1 OR (p->'scope'-'database_incarnation') IS DISTINCT FROM (p->'historical_scope'-'database_incarnation')) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N03',MESSAGE='denied';END IF;
 SELECT * INTO old FROM maezo_native_v2.receipt_history WHERE scope=p->'historical_scope' FOR UPDATE;
 IF (FOUND AND ((p->>'issuer_revision')::bigint<old.issuer_revision OR ((p->>'issuer_revision')::bigint=old.issuer_revision AND (p->>'state'<>old.state OR p->>'reconciliation_receipt_digest'<>old.reconciliation_receipt_digest)))) IS NOT FALSE THEN RAISE EXCEPTION USING ERRCODE='P7N04',MESSAGE='conflict';END IF;
 INSERT INTO maezo_native_v2.receipt_history VALUES(p->'historical_scope',p->>'state',p->>'reconciliation_receipt_digest',(p->>'issuer_revision')::bigint) ON CONFLICT(scope) DO UPDATE SET state=excluded.state,reconciliation_receipt_digest=excluded.reconciliation_receipt_digest,issuer_revision=excluded.issuer_revision;
 RETURN jsonb_build_object('state',p->'state','issuer_revision',p->'issuer_revision');
END $$;

-- Owned migration transaction installs ownership and revokes before publication.
__FUNCTION_OWNERSHIP_AND_ACL__
__VERIFY_NATIVE_ACL__
-- Owner-generated exact receipt/catalogue identity; no runtime seed or automatic history completeness.
INSERT INTO maezo_native_v2.schema_version VALUES('native-acquisition-v2','__MANIFEST_DIGEST__','__ACT_SCHEMA_VALUE__','__ACT_SCHEMA_OID__'::oid,'__DATABASE_OID__'::oid,'__DATABASE_BINDING_DIGEST__','__FUNCTION_OWNER_OID__'::oid,'__ISSUER_ROLE_OID__'::oid,to_regprocedure('maezo_d7_control.lock_runtime_v2_scope(jsonb)')::oid,'__CIB_ABI_DIGEST__','__MIGRATION_RECEIPT__',__PREPARATION_BINDING__,(SELECT result_bytes FROM maezo_d7_control."MZO_OWNER_PREPARATION_RECEIPT" WHERE preparation_id='__PREPARATION_ID__'::uuid AND event='PREPARED'),encode(sha256(convert_to(maezo_native_v2.catalogue_v2()::text,'UTF8')),'hex'));
