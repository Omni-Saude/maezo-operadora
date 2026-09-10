-- W6A approved interface, installation-only additive PostgreSQL migration.
-- Execute only as migration owner after effective-role review. No ACT_* DDL or startup migration.
-- Actual LOGIN roles and scope bindings are independently provisioned; no default tenant/data.
CREATE SCHEMA maezo_external;
REVOKE ALL ON SCHEMA maezo_external FROM PUBLIC;
CREATE ROLE portal_external_source_importer NOLOGIN;
CREATE ROLE portal_external_case_publisher NOLOGIN;
CREATE ROLE portal_external_case_runtime NOLOGIN;
CREATE ROLE portal_external_source_definer NOLOGIN;
CREATE ROLE portal_external_ingress_reader NOLOGIN;
CREATE ROLE portal_external_checkpoint_definer NOLOGIN;
CREATE ROLE portal_external_publisher_definer NOLOGIN;
GRANT USAGE ON SCHEMA maezo_external TO portal_external_source_importer,
 portal_external_case_publisher,portal_external_case_runtime,
 portal_external_source_definer,portal_external_checkpoint_definer,portal_external_publisher_definer,portal_external_ingress_reader;

CREATE TABLE maezo_external.mzo_external_caller_scope (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 login_role name NOT NULL, capability text NOT NULL CHECK(capability IN ('importer','publisher','native')),
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,login_role,capability)
);

CREATE TABLE maezo_external.mzo_external_source_generation (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 accepted_generation bigint NOT NULL CHECK(accepted_generation>=0), published_generation bigint NOT NULL CHECK(published_generation>=0 AND published_generation<=accepted_generation),
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation)
);

CREATE TABLE maezo_external.mzo_external_case_reservation (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 upstream_resource_key text NOT NULL, case_ref text NOT NULL COLLATE "C", allocated_at timestamptz NOT NULL,
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,upstream_resource_key), UNIQUE(tenant,environment,engine_name,database_incarnation,case_ref)
);

CREATE TABLE maezo_external.mzo_external_source_event (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 source_ref text NOT NULL, source_revision bigint NOT NULL CHECK(source_revision>=0),
 ingress_id text NOT NULL, source_generation bigint NOT NULL CHECK(source_generation>0),
 canonical_payload bytea NOT NULL, payload_digest char(64) NOT NULL, committed_at timestamptz NOT NULL,
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,source_ref,source_revision), UNIQUE(tenant,environment,engine_name,database_incarnation,ingress_id), UNIQUE(tenant,environment,engine_name,database_incarnation,source_generation)
);

CREATE TABLE maezo_external.mzo_external_source_head (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 source_ref text NOT NULL, source_revision bigint NOT NULL, payload_digest char(64) NOT NULL,
 source_generation bigint NOT NULL, identity jsonb NOT NULL, source_namespace text NOT NULL,
 revoked boolean NOT NULL, valid_until timestamptz NOT NULL,
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,source_ref), FOREIGN KEY(tenant,environment,engine_name,database_incarnation,source_ref,source_revision) REFERENCES maezo_external.mzo_external_source_event
);

CREATE TABLE maezo_external.mzo_external_ingress_receipt (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 ingress_id text NOT NULL, request_digest char(64) NOT NULL, canonical_receipt bytea NOT NULL,
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,ingress_id)
);

CREATE TABLE maezo_external.mzo_external_publication_outbox (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 source_ref text NOT NULL, source_revision bigint NOT NULL, publication_id text NOT NULL,
 canonical_request bytea, request_digest char(64), delivery_state text NOT NULL DEFAULT 'pending'
 CHECK(delivery_state IN ('pending','uncertain','committed','covered','covered_unprepared','refused')),
 native_receipt bytea, claimant_ref text, claim_epoch bigint NOT NULL DEFAULT 0 CHECK(claim_epoch>=0),
 claim_expires_at timestamptz, covering_publication_id text,
 CHECK((canonical_request IS NULL)=(request_digest IS NULL)),
 CHECK(delivery_state NOT IN ('uncertain','committed','covered') OR canonical_request IS NOT NULL),
 CHECK(delivery_state NOT IN ('covered','covered_unprepared') OR covering_publication_id IS NOT NULL),
 CHECK(delivery_state<>'covered_unprepared' OR canonical_request IS NULL),
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,source_ref,source_revision), UNIQUE(tenant,environment,engine_name,database_incarnation,publication_id)
);

CREATE TABLE maezo_external.mzo_external_checkpoint_outbox (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 checkpoint_ref text NOT NULL, epoch bigint NOT NULL, publication_id text NOT NULL,
 canonical_request bytea, request_digest char(64), delivery_state text NOT NULL DEFAULT 'pending'
 CHECK(delivery_state IN ('pending','uncertain','committed','covered','covered_unprepared','refused')),
 native_receipt bytea, claimant_ref text, claim_epoch bigint NOT NULL DEFAULT 0 CHECK(claim_epoch>=0),
 claim_expires_at timestamptz, covering_publication_id text,
 CHECK((canonical_request IS NULL)=(request_digest IS NULL)),
 CHECK(delivery_state NOT IN ('uncertain','committed','covered') OR canonical_request IS NOT NULL),
 CHECK(delivery_state NOT IN ('covered','covered_unprepared') OR covering_publication_id IS NOT NULL),
 CHECK(delivery_state<>'covered_unprepared' OR canonical_request IS NULL),
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,checkpoint_ref,epoch), UNIQUE(tenant,environment,engine_name,database_incarnation,publication_id)
);

CREATE TABLE maezo_external.mzo_external_publication_receipt (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 publication_id text NOT NULL, kind text NOT NULL CHECK(kind IN ('case','checkpoint')), request_digest char(64) NOT NULL, requester_fingerprint char(64) NOT NULL, canonical_receipt bytea NOT NULL, committed_at timestamptz NOT NULL,
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,publication_id)
);

CREATE TABLE maezo_external.mzo_external_event_terminal (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 source_generation bigint NOT NULL, source_ref text NOT NULL, source_revision bigint NOT NULL, source_digest char(64) NOT NULL, terminal_kind text NOT NULL CHECK(terminal_kind IN ('projected','covered')), publication_id text NOT NULL, covering_source_revision bigint NOT NULL, covering_source_digest char(64) NOT NULL,
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,source_generation), FOREIGN KEY(tenant,environment,engine_name,database_incarnation,source_ref,source_revision) REFERENCES maezo_external.mzo_external_source_event, FOREIGN KEY(tenant,environment,engine_name,database_incarnation,publication_id) REFERENCES maezo_external.mzo_external_publication_receipt
);

CREATE TABLE maezo_external.mzo_external_checkpoint_event (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 checkpoint_ref text NOT NULL, epoch bigint NOT NULL CHECK(epoch>0), predecessor_digest char(64),
 checkpoint_digest char(64) NOT NULL, designation_digest char(64) NOT NULL,
 canonical_signed_checkpoint bytea NOT NULL, namespace_positions_canonical bytea NOT NULL,
 heads_digest char(64) NOT NULL, heads_count bigint NOT NULL CHECK(heads_count>=0),
 observed_at timestamptz NOT NULL, valid_until timestamptz NOT NULL,
 ingress_id text NOT NULL, canonical_ingress_receipt bytea NOT NULL,
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,checkpoint_ref,epoch), UNIQUE(tenant,environment,engine_name,database_incarnation,epoch), UNIQUE(tenant,environment,engine_name,database_incarnation,ingress_id)
);

CREATE TABLE maezo_external.mzo_external_checkpoint_head_entry (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 checkpoint_ref text NOT NULL, epoch bigint NOT NULL, namespace text NOT NULL, source_ref text NOT NULL, source_revision bigint NOT NULL, source_digest char(64) NOT NULL, upstream_resource_key text NOT NULL, case_ref text NOT NULL COLLATE "C", state text NOT NULL CHECK(state IN ('active','revoked')),
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,checkpoint_ref,epoch,source_ref), FOREIGN KEY(tenant,environment,engine_name,database_incarnation,checkpoint_ref,epoch) REFERENCES maezo_external.mzo_external_checkpoint_event
);

CREATE TABLE maezo_external.mzo_external_checkpoint_accepted (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 checkpoint_ref text NOT NULL, epoch bigint NOT NULL, checkpoint_digest char(64) NOT NULL,
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation), FOREIGN KEY(tenant,environment,engine_name,database_incarnation,checkpoint_ref,epoch) REFERENCES maezo_external.mzo_external_checkpoint_event
);

CREATE TABLE maezo_external.mzo_external_checkpoint_current (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 checkpoint_ref text NOT NULL, epoch bigint NOT NULL, checkpoint_digest char(64) NOT NULL,
 designation_digest char(64) NOT NULL, publication_id text NOT NULL,
 accepted_generation bigint NOT NULL, published_generation bigint NOT NULL,
 valid_until timestamptz NOT NULL, CHECK(accepted_generation=published_generation),
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation), FOREIGN KEY(tenant,environment,engine_name,database_incarnation,checkpoint_ref,epoch) REFERENCES maezo_external.mzo_external_checkpoint_event, FOREIGN KEY(tenant,environment,engine_name,database_incarnation,publication_id) REFERENCES maezo_external.mzo_external_publication_receipt
);

CREATE TABLE maezo_external.mzo_external_authority_event (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 designation_revision bigint NOT NULL, designation_digest char(64) NOT NULL, canonical_designation bytea NOT NULL, installation_receipt bytea NOT NULL, authority_revision bigint NOT NULL, valid_until timestamptz NOT NULL,
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,designation_revision), UNIQUE(tenant,environment,engine_name,database_incarnation,designation_digest)
);

CREATE TABLE maezo_external.mzo_external_authority_current (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 designation_revision bigint NOT NULL, designation_digest char(64) NOT NULL, authority_revision bigint NOT NULL,
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation), FOREIGN KEY(tenant,environment,engine_name,database_incarnation,designation_revision) REFERENCES maezo_external.mzo_external_authority_event
);

CREATE TABLE maezo_external.mzo_external_case (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 case_ref text NOT NULL COLLATE "C", source_ref text NOT NULL, source_revision bigint NOT NULL,
 identity jsonb NOT NULL, owners_canonical bytea NOT NULL, projection_digest char(64) NOT NULL,
 publication_id text NOT NULL, revoked boolean NOT NULL,
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,case_ref), UNIQUE(tenant,environment,engine_name,database_incarnation,source_ref), FOREIGN KEY(tenant,environment,engine_name,database_incarnation,source_ref,source_revision) REFERENCES maezo_external.mzo_external_source_event
);

CREATE TABLE maezo_external.mzo_external_case_grant (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 case_ref text NOT NULL COLLATE "C", grant_ref text NOT NULL, operation text NOT NULL CHECK(operation IN ('list','detail')),
 principal_ref text NOT NULL, issuer text NOT NULL, subject_ref text NOT NULL, membership_revision bigint NOT NULL,
 audience text NOT NULL CHECK(audience IN ('beneficiary','provider')), source_revision bigint NOT NULL,
 canonical_grant bytea NOT NULL, valid_until timestamptz NOT NULL, revoked boolean NOT NULL,
 PRIMARY KEY (tenant,environment,engine_name,database_incarnation,case_ref,grant_ref,operation), FOREIGN KEY(tenant,environment,engine_name,database_incarnation,case_ref) REFERENCES maezo_external.mzo_external_case
);

CREATE INDEX external_grant_principal ON maezo_external.mzo_external_case_grant
 (tenant,environment,engine_name,database_incarnation,principal_ref,audience,operation,case_ref);
-- This helper canonicalizes closed ASCII-key receipt objects, NEVER signed input bytes.
CREATE FUNCTION maezo_external.receipt_json(v jsonb) RETURNS text LANGUAGE plpgsql IMMUTABLE
 SET search_path=pg_catalog,maezo_external AS $$
DECLARE r text;
BEGIN
 CASE jsonb_typeof(v)
 WHEN 'object' THEN SELECT '{'||coalesce(string_agg(to_jsonb(key)::text||':'||maezo_external.receipt_json(value),',' ORDER BY key COLLATE "C"),'')||'}' INTO r FROM jsonb_each(v);
 WHEN 'array' THEN SELECT '['||coalesce(string_agg(maezo_external.receipt_json(value),',' ORDER BY ord),'')||']' INTO r FROM jsonb_array_elements(v) WITH ORDINALITY a(value,ord);
 WHEN 'string','boolean','null' THEN r:=v::text;
 ELSE RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid';
 END CASE;
 RETURN r;
END $$;
REVOKE ALL ON FUNCTION maezo_external.receipt_json(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION maezo_external.receipt_json(jsonb) TO portal_external_source_definer,portal_external_checkpoint_definer;

CREATE FUNCTION maezo_external.lock_ingress(scope_binding jsonb) RETURNS text
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,maezo_external AS $$
DECLARE d text;
BEGIN
 IF (SELECT count(*) FROM jsonb_object_keys(scope_binding))<>4 OR NOT EXISTS
 (SELECT 1 FROM maezo_external.mzo_external_caller_scope WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment' AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation' AND login_role=session_user AND capability='importer')
 OR (SELECT count(*) FROM maezo_external.mzo_external_caller_scope WHERE login_role=session_user AND capability='importer')<>1
 THEN RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='external_case_denied'; END IF;
 PERFORM 1 FROM maezo_external.mzo_external_source_generation WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment' AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 -- The latch prevents insertion; every existing head is locked before the tenant.
 PERFORM source_ref FROM maezo_external.mzo_external_source_head
 WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment'
 AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation'
 ORDER BY source_ref COLLATE "C" FOR UPDATE;
 PERFORM epoch FROM maezo_external.mzo_external_checkpoint_accepted
 WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment'
 AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation' FOR UPDATE;

 PERFORM 1 FROM public.mzo_human_tenant WHERE tenant_=scope_binding->>'tenant' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 SELECT c.designation_digest INTO d FROM maezo_external.mzo_external_authority_current c
 JOIN maezo_external.mzo_external_authority_event a USING (tenant,environment,engine_name,database_incarnation,designation_revision)
 WHERE c.tenant=scope_binding->>'tenant' AND c.environment=scope_binding->>'environment'
 AND c.engine_name=scope_binding->>'engine_name' AND c.database_incarnation=scope_binding->>'database_incarnation'
 AND c.designation_digest=a.designation_digest AND a.valid_until>clock_timestamp();
 IF d IS NULL THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 RETURN d;
END $$;
ALTER FUNCTION maezo_external.lock_ingress(jsonb) OWNER TO portal_external_source_definer;
REVOKE ALL ON FUNCTION maezo_external.lock_ingress(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION maezo_external.lock_ingress(jsonb) TO portal_external_checkpoint_definer;

CREATE FUNCTION maezo_external.reserve_external_case_ref(scope_binding jsonb, signed_upstream_resource_key text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,maezo_external AS $$
DECLARE r text;
BEGIN
 PERFORM maezo_external.lock_ingress(scope_binding);
 IF signed_upstream_resource_key !~ '^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$' THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 SELECT case_ref INTO r FROM maezo_external.mzo_external_case_reservation WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment' AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation' AND upstream_resource_key=signed_upstream_resource_key;
 IF r IS NOT NULL THEN RETURN r; END IF;
 r:='case_'||replace(gen_random_uuid()::text,'-','');
 INSERT INTO maezo_external.mzo_external_case_reservation VALUES(scope_binding->>'tenant',scope_binding->>'environment',scope_binding->>'engine_name',scope_binding->>'database_incarnation',signed_upstream_resource_key,r,clock_timestamp());
 RETURN r;
END $$;

CREATE FUNCTION maezo_external.accept_external_case_source(scope_binding jsonb, ingress_id text,
 expected_source_revision bigint, expected_payload_digest text, canonical_signed_packet bytea)
 RETURNS bytea LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,maezo_external AS $$
DECLARE st jsonb; h maezo_external.mzo_external_source_head%ROWTYPE; oldr maezo_external.mzo_external_ingress_receipt%ROWTYPE;
 d text; r bytea; g bigint; rev bigint; stamp text; receipt_dig text;
BEGIN
 PERFORM maezo_external.lock_ingress(scope_binding);
 IF octet_length(canonical_signed_packet)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 d:=encode(sha256(canonical_signed_packet),'hex');
 SELECT * INTO oldr FROM maezo_external.mzo_external_ingress_receipt x WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment' AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation' AND x.ingress_id=accept_external_case_source.ingress_id;
 IF FOUND THEN IF oldr.request_digest<>d THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF; RETURN oldr.canonical_receipt; END IF;
 st:=convert_from(canonical_signed_packet,'UTF8')::jsonb->'statement';
 IF st->>'schema' IS DISTINCT FROM 'portal-external-case-source.v1' OR st->'scope' IS DISTINCT FROM scope_binding OR ingress_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$'
 OR (st->>'valid_until')::timestamptz<=clock_timestamp() THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 rev:=(st->>'source_revision')::bigint;
 SELECT * INTO h FROM maezo_external.mzo_external_source_head WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment' AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation' AND source_ref=st->>'source_ref' FOR UPDATE;
 IF FOUND THEN
  IF h.source_revision IS DISTINCT FROM expected_source_revision OR h.payload_digest IS DISTINCT FROM expected_payload_digest
   OR rev<=h.source_revision OR h.identity<>st->'identity' OR h.source_namespace<>st->>'source_namespace'
  THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 ELSIF expected_source_revision IS NOT NULL OR expected_payload_digest IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 IF NOT EXISTS(SELECT 1 FROM maezo_external.mzo_external_case_reservation WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment' AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation'
  AND upstream_resource_key=st->'identity'->>'upstream_resource_key' AND case_ref=st->'identity'->>'case_ref') THEN RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='external_case_denied'; END IF;
 UPDATE maezo_external.mzo_external_source_generation SET accepted_generation=accepted_generation+1 WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment' AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation' AND accepted_generation<9223372036854775807 RETURNING accepted_generation INTO g;
 IF g IS NULL THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
 receipt_dig:=encode(sha256(convert_to(maezo_external.receipt_json(jsonb_build_object('ownership',st->'ownership_receipt_digest','disclosure',
  coalesce((SELECT jsonb_agg(v->'decision_digest' ORDER BY ord) FROM jsonb_array_elements(st->'disclosure_grants') WITH ORDINALITY x(v,ord)),'[]'::jsonb))),'UTF8')),'hex');
 r:=convert_to(maezo_external.receipt_json(jsonb_build_object('schema','portal-external-source-ingress-receipt.v1','scope',scope_binding,
 'ingress_id',ingress_id,'request_digest',d,'source_ref',st->>'source_ref','source_revision',rev::text,'source_digest',d,
 'upstream_receipts_digest',receipt_dig,'source_generation',g::text,'committed_at',stamp)),'UTF8');
 INSERT INTO maezo_external.mzo_external_source_event VALUES(scope_binding->>'tenant',scope_binding->>'environment',scope_binding->>'engine_name',scope_binding->>'database_incarnation',st->>'source_ref',rev,ingress_id,g,canonical_signed_packet,d,stamp::timestamptz);
 INSERT INTO maezo_external.mzo_external_source_head VALUES(scope_binding->>'tenant',scope_binding->>'environment',scope_binding->>'engine_name',scope_binding->>'database_incarnation',st->>'source_ref',rev,d,g,st->'identity',st->>'source_namespace',st->>'state'='revoked',(st->>'valid_until')::timestamptz)
 ON CONFLICT(tenant,environment,engine_name,database_incarnation,source_ref) DO UPDATE SET source_revision=excluded.source_revision,payload_digest=excluded.payload_digest,source_generation=excluded.source_generation,revoked=excluded.revoked,valid_until=excluded.valid_until;
 INSERT INTO maezo_external.mzo_external_ingress_receipt VALUES(scope_binding->>'tenant',scope_binding->>'environment',scope_binding->>'engine_name',scope_binding->>'database_incarnation',ingress_id,d,r);
 INSERT INTO maezo_external.mzo_external_publication_outbox(tenant,environment,engine_name,database_incarnation,source_ref,source_revision,publication_id)
 VALUES(scope_binding->>'tenant',scope_binding->>'environment',scope_binding->>'engine_name',scope_binding->>'database_incarnation',st->>'source_ref',rev,'pub_'||replace(gen_random_uuid()::text,'-',''));
 RETURN r;
END $$;
CREATE FUNCTION maezo_external.revoke_external_case_source(scope_binding jsonb, ingress_id text, expected_source_revision bigint, expected_payload_digest text, canonical_signed_packet bytea)
 RETURNS bytea LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,maezo_external AS $$
BEGIN
 IF convert_from(canonical_signed_packet,'UTF8')::jsonb->'statement'->>'state' IS DISTINCT FROM 'revoked' THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 RETURN maezo_external.accept_external_case_source(scope_binding,ingress_id,expected_source_revision,expected_payload_digest,canonical_signed_packet);
END $$;

CREATE FUNCTION maezo_external.accept_external_scope_checkpoint(scope_binding jsonb, ingress_id text,
 expected_checkpoint_epoch bigint, expected_checkpoint_digest text, canonical_signed_checkpoint bytea)
 RETURNS bytea LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,maezo_external AS $$
DECLARE st jsonb; oldr maezo_external.mzo_external_checkpoint_event%ROWTYPE; h maezo_external.mzo_external_checkpoint_accepted%ROWTYPE;
 designation text; d text; ep bigint; r bytea; stamp text; ent jsonb;
BEGIN
 designation:=maezo_external.lock_ingress(scope_binding);
 IF octet_length(canonical_signed_checkpoint)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 d:=encode(sha256(canonical_signed_checkpoint),'hex');
 SELECT * INTO oldr FROM maezo_external.mzo_external_checkpoint_event x WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment' AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation' AND x.ingress_id=accept_external_scope_checkpoint.ingress_id;
 IF FOUND THEN IF oldr.checkpoint_digest<>d THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF; RETURN oldr.canonical_ingress_receipt; END IF;
 st:=convert_from(canonical_signed_checkpoint,'UTF8')::jsonb->'statement'; ep:=(st->>'epoch')::bigint;
 IF st->>'schema' IS DISTINCT FROM 'portal-external-scope-checkpoint.v1' OR st->'scope' IS DISTINCT FROM scope_binding OR st->>'designation_digest'<>designation
 OR ep<=0 OR (st->>'valid_until')::timestamptz<=clock_timestamp() OR ingress_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$'
 OR (st->>'heads_count')::bigint<>jsonb_array_length(st->'heads')
 OR st->>'heads_digest'<>encode(sha256(convert_to(maezo_external.receipt_json(st->'heads'),'UTF8')),'hex')
 THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 SELECT * INTO h FROM maezo_external.mzo_external_checkpoint_accepted WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment' AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation' FOR UPDATE;
 IF FOUND THEN
  IF h.epoch IS DISTINCT FROM expected_checkpoint_epoch OR h.checkpoint_digest IS DISTINCT FROM expected_checkpoint_digest OR ep<=h.epoch
   OR st->>'predecessor_checkpoint_digest' IS DISTINCT FROM h.checkpoint_digest THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 ELSIF expected_checkpoint_epoch IS DISTINCT FROM 0 OR expected_checkpoint_digest IS NOT NULL OR st->>'predecessor_checkpoint_digest' IS NOT NULL
 THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
 r:=convert_to(maezo_external.receipt_json(jsonb_build_object('schema','portal-external-checkpoint-ingress-receipt.v1','scope',scope_binding,
 'ingress_id',ingress_id,'request_digest',d,'checkpoint_ref',st->>'checkpoint_ref','epoch',ep::text,'checkpoint_digest',d,'designation_digest',designation,'committed_at',stamp)),'UTF8');
 INSERT INTO maezo_external.mzo_external_checkpoint_event VALUES(scope_binding->>'tenant',scope_binding->>'environment',scope_binding->>'engine_name',scope_binding->>'database_incarnation',st->>'checkpoint_ref',ep,st->>'predecessor_checkpoint_digest',d,designation,canonical_signed_checkpoint,
 convert_to(maezo_external.receipt_json(st->'namespace_positions'),'UTF8'),st->>'heads_digest',(st->>'heads_count')::bigint,(st->>'observed_at')::timestamptz,(st->>'valid_until')::timestamptz,ingress_id,r);
 FOR ent IN SELECT value FROM jsonb_array_elements(st->'heads') LOOP
  INSERT INTO maezo_external.mzo_external_checkpoint_head_entry VALUES(scope_binding->>'tenant',scope_binding->>'environment',scope_binding->>'engine_name',scope_binding->>'database_incarnation',st->>'checkpoint_ref',ep,ent->>'namespace',ent->>'source_ref',(ent->>'source_revision')::bigint,ent->>'source_digest',ent->>'upstream_resource_key',ent->>'case_ref',ent->>'state');
 END LOOP;
 INSERT INTO maezo_external.mzo_external_checkpoint_accepted VALUES(scope_binding->>'tenant',scope_binding->>'environment',scope_binding->>'engine_name',scope_binding->>'database_incarnation',st->>'checkpoint_ref',ep,d)
 ON CONFLICT(tenant,environment,engine_name,database_incarnation) DO UPDATE SET checkpoint_ref=excluded.checkpoint_ref,epoch=excluded.epoch,checkpoint_digest=excluded.checkpoint_digest;
 INSERT INTO maezo_external.mzo_external_checkpoint_outbox(tenant,environment,engine_name,database_incarnation,checkpoint_ref,epoch,publication_id)
 VALUES(scope_binding->>'tenant',scope_binding->>'environment',scope_binding->>'engine_name',scope_binding->>'database_incarnation',st->>'checkpoint_ref',ep,'pub_'||replace(gen_random_uuid()::text,'-',''));
 RETURN r;
END $$;

-- Function owners cannot be assumed by caller roles. No direct importer table writes.
ALTER FUNCTION maezo_external.reserve_external_case_ref(jsonb,text) OWNER TO portal_external_source_definer;
ALTER FUNCTION maezo_external.accept_external_case_source(jsonb,text,bigint,text,bytea) OWNER TO portal_external_source_definer;
ALTER FUNCTION maezo_external.revoke_external_case_source(jsonb,text,bigint,text,bytea) OWNER TO portal_external_source_definer;
ALTER FUNCTION maezo_external.accept_external_scope_checkpoint(jsonb,text,bigint,text,bytea) OWNER TO portal_external_checkpoint_definer;
REVOKE ALL ON ALL TABLES IN SCHEMA maezo_external FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA maezo_external FROM PUBLIC;
GRANT EXECUTE ON FUNCTION maezo_external.reserve_external_case_ref(jsonb,text),
 maezo_external.accept_external_case_source(jsonb,text,bigint,text,bytea),
 maezo_external.revoke_external_case_source(jsonb,text,bigint,text,bytea),
 maezo_external.accept_external_scope_checkpoint(jsonb,text,bigint,text,bytea) TO portal_external_source_importer;
GRANT SELECT ON maezo_external.mzo_external_caller_scope,maezo_external.mzo_external_source_generation,
 maezo_external.mzo_external_source_head,maezo_external.mzo_external_checkpoint_accepted,
 maezo_external.mzo_external_authority_current,maezo_external.mzo_external_authority_event,
 maezo_external.mzo_external_case_reservation,maezo_external.mzo_external_ingress_receipt
 TO portal_external_source_definer;
GRANT SELECT ON maezo_external.mzo_external_checkpoint_event,maezo_external.mzo_external_checkpoint_accepted
 TO portal_external_checkpoint_definer;
GRANT UPDATE(epoch) ON maezo_external.mzo_external_checkpoint_accepted TO portal_external_source_definer;
GRANT SELECT,UPDATE(rev_) ON public.mzo_human_tenant TO portal_external_source_definer;
GRANT UPDATE(accepted_generation) ON maezo_external.mzo_external_source_generation TO portal_external_source_definer;
GRANT INSERT ON maezo_external.mzo_external_case_reservation,maezo_external.mzo_external_source_event,
 maezo_external.mzo_external_ingress_receipt,maezo_external.mzo_external_publication_outbox TO portal_external_source_definer;
GRANT INSERT ON maezo_external.mzo_external_source_head TO portal_external_source_definer;
GRANT UPDATE(source_revision,payload_digest,source_generation,revoked,valid_until) ON maezo_external.mzo_external_source_head TO portal_external_source_definer;
GRANT INSERT ON maezo_external.mzo_external_checkpoint_event,maezo_external.mzo_external_checkpoint_head_entry,
 maezo_external.mzo_external_checkpoint_outbox TO portal_external_checkpoint_definer;
GRANT INSERT,UPDATE ON maezo_external.mzo_external_checkpoint_accepted TO portal_external_checkpoint_definer;
GRANT SELECT ON maezo_external.mzo_external_caller_scope,maezo_external.mzo_external_source_generation,
 maezo_external.mzo_external_case_reservation,
 maezo_external.mzo_external_source_event,
 maezo_external.mzo_external_source_head,
 maezo_external.mzo_external_ingress_receipt,
 maezo_external.mzo_external_publication_outbox,
 maezo_external.mzo_external_checkpoint_outbox,
 maezo_external.mzo_external_publication_receipt,
 maezo_external.mzo_external_event_terminal,
 maezo_external.mzo_external_checkpoint_event,
 maezo_external.mzo_external_checkpoint_head_entry,
 maezo_external.mzo_external_checkpoint_accepted,
 maezo_external.mzo_external_checkpoint_current,
 maezo_external.mzo_external_authority_event,
 maezo_external.mzo_external_authority_current,
 maezo_external.mzo_external_case,
 maezo_external.mzo_external_case_grant TO portal_external_case_runtime;
GRANT INSERT,UPDATE ON maezo_external.mzo_external_case,maezo_external.mzo_external_case_grant,
 maezo_external.mzo_external_checkpoint_current TO portal_external_case_runtime;
GRANT DELETE ON maezo_external.mzo_external_case_grant TO portal_external_case_runtime;
GRANT INSERT ON maezo_external.mzo_external_publication_receipt,maezo_external.mzo_external_event_terminal TO portal_external_case_runtime;
GRANT UPDATE(published_generation) ON maezo_external.mzo_external_source_generation TO portal_external_case_runtime;
-- Installation owner alone binds LOGIN roles, initializes scope generation0/0 and installs
-- independently signed authority. Empty tables are not completeness or readiness proof.

-- Read-only means no DML: row-locking PostgreSQL functions are explicitly VOLATILE.
CREATE FUNCTION maezo_external.lock_external_ingress_authority(
 scope_binding jsonb, receipt_kind text, ingress_id text, request_digest text)
 RETURNS TABLE(scope jsonb,designation_digest text,canonical_designation bytea,
 installation_receipt bytea,authority_revision bigint,revoked_fingerprints jsonb,
 historical_ingress_receipt bytea)
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,maezo_external AS $$
DECLARE rowref record; a record; native_rev bigint; historic bytea; stored_digest text;
 pins jsonb:='[]'::jsonb; framed bytea;
BEGIN
 IF jsonb_typeof(scope_binding) IS DISTINCT FROM 'object'
 OR (SELECT count(*) FROM jsonb_object_keys(scope_binding))<>4
 OR (SELECT count(*) FROM maezo_external.mzo_external_caller_scope
 WHERE login_role=session_user AND capability='importer')<>1
 OR NOT EXISTS(SELECT 1 FROM maezo_external.mzo_external_caller_scope c
 WHERE c.login_role=session_user AND c.capability='importer' AND c.tenant=scope_binding->>'tenant'
 AND c.environment=scope_binding->>'environment' AND c.engine_name=scope_binding->>'engine_name'
 AND c.database_incarnation=scope_binding->>'database_incarnation')
 THEN RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='external_case_denied'; END IF;
 IF receipt_kind IS NULL OR receipt_kind NOT IN ('reserve','case','checkpoint')
 OR (receipt_kind='reserve' AND (ingress_id IS NOT NULL OR request_digest IS NOT NULL))
 OR (receipt_kind<>'reserve' AND (ingress_id IS NULL OR request_digest IS NULL
 OR ingress_id !~ '^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$' OR request_digest !~ '^[0-9a-f]{64}$'))
 THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 PERFORM 1 FROM maezo_external.mzo_external_source_generation g WHERE
 g.tenant=scope_binding->>'tenant' AND g.environment=scope_binding->>'environment'
 AND g.engine_name=scope_binding->>'engine_name' AND g.database_incarnation=scope_binding->>'database_incarnation' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 FOR rowref IN SELECT h.source_ref FROM maezo_external.mzo_external_source_head h WHERE
 h.tenant=scope_binding->>'tenant' AND h.environment=scope_binding->>'environment'
 AND h.engine_name=scope_binding->>'engine_name' AND h.database_incarnation=scope_binding->>'database_incarnation'
 ORDER BY h.source_ref COLLATE "C" FOR UPDATE LOOP NULL; END LOOP;
 PERFORM 1 FROM maezo_external.mzo_external_checkpoint_accepted c WHERE
 c.tenant=scope_binding->>'tenant' AND c.environment=scope_binding->>'environment'
 AND c.engine_name=scope_binding->>'engine_name' AND c.database_incarnation=scope_binding->>'database_incarnation' FOR UPDATE;
 SELECT h.rev_ INTO native_rev FROM public.mzo_human_tenant h
 WHERE h.tenant_=scope_binding->>'tenant' FOR UPDATE;
 IF native_rev IS NULL THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 SELECT e.* INTO a FROM maezo_external.mzo_external_authority_current c
 JOIN maezo_external.mzo_external_authority_event e USING(tenant,environment,engine_name,database_incarnation,designation_revision)
 WHERE c.tenant=scope_binding->>'tenant' AND c.environment=scope_binding->>'environment'
 AND c.engine_name=scope_binding->>'engine_name' AND c.database_incarnation=scope_binding->>'database_incarnation'
 AND c.designation_digest=e.designation_digest AND c.authority_revision=e.authority_revision;
 IF NOT FOUND OR octet_length(a.canonical_designation)>65536 OR octet_length(a.installation_receipt)>65536
 THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 IF receipt_kind='case' THEN
  SELECT r.request_digest,r.canonical_receipt INTO stored_digest,historic
  FROM maezo_external.mzo_external_ingress_receipt r WHERE r.tenant=scope_binding->>'tenant'
  AND r.environment=scope_binding->>'environment' AND r.engine_name=scope_binding->>'engine_name'
  AND r.database_incarnation=scope_binding->>'database_incarnation' AND r.ingress_id=lock_external_ingress_authority.ingress_id;
 ELSIF receipt_kind='checkpoint' THEN
  SELECT r.checkpoint_digest,r.canonical_ingress_receipt INTO stored_digest,historic
  FROM maezo_external.mzo_external_checkpoint_event r WHERE r.tenant=scope_binding->>'tenant'
  AND r.environment=scope_binding->>'environment' AND r.engine_name=scope_binding->>'engine_name'
  AND r.database_incarnation=scope_binding->>'database_incarnation' AND r.ingress_id=lock_external_ingress_authority.ingress_id;
 END IF;
 IF stored_digest IS NOT NULL AND stored_digest IS DISTINCT FROM request_digest
 THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 IF octet_length(historic)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 FOR rowref IN SELECT r.fingerprint_ FROM public.mzo_portal_read_revocation r
 WHERE r.tenant_=scope_binding->>'tenant' AND r.environment_=scope_binding->>'environment'
 AND r.engine_=scope_binding->>'engine_name' AND r.incarnation_=scope_binding->>'database_incarnation'
 ORDER BY r.fingerprint_ COLLATE "C" LOOP
  IF rowref.fingerprint_ !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
  pins:=pins||jsonb_build_array(rowref.fingerprint_);
  IF octet_length(pins::text)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 END LOOP;
 framed:=convert_to(maezo_external.receipt_json(jsonb_build_object('scope',scope_binding,
 'designation_digest',a.designation_digest,'canonical_designation',replace(encode(a.canonical_designation,'base64'),E'\n',''),
 'installation_receipt',replace(encode(a.installation_receipt,'base64'),E'\n',''),
 'authority_revision',native_rev::text,'revoked_fingerprints',pins,
 'historical_ingress_receipt',replace(encode(historic,'base64'),E'\n',''))),'UTF8');
 IF octet_length(framed)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 RETURN QUERY SELECT scope_binding,a.designation_digest::text,a.canonical_designation,
 a.installation_receipt,native_rev,pins,historic;
END $$;
ALTER FUNCTION maezo_external.lock_external_ingress_authority(jsonb,text,text,text) OWNER TO portal_external_ingress_reader;
REVOKE ALL ON FUNCTION maezo_external.lock_external_ingress_authority(jsonb,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION maezo_external.lock_external_ingress_authority(jsonb,text,text,text) TO portal_external_source_importer;
GRANT EXECUTE ON FUNCTION maezo_external.receipt_json(jsonb) TO portal_external_ingress_reader;
GRANT SELECT ON maezo_external.mzo_external_caller_scope,maezo_external.mzo_external_source_generation,
 maezo_external.mzo_external_source_head,maezo_external.mzo_external_checkpoint_accepted,
 maezo_external.mzo_external_authority_current,maezo_external.mzo_external_authority_event,
 maezo_external.mzo_external_ingress_receipt,maezo_external.mzo_external_checkpoint_event,
 public.mzo_human_tenant,public.mzo_portal_read_revocation TO portal_external_ingress_reader;
GRANT UPDATE(accepted_generation) ON maezo_external.mzo_external_source_generation TO portal_external_ingress_reader;
GRANT UPDATE(payload_digest) ON maezo_external.mzo_external_source_head TO portal_external_ingress_reader;
GRANT UPDATE(epoch) ON maezo_external.mzo_external_checkpoint_accepted TO portal_external_ingress_reader;
GRANT UPDATE(rev_) ON public.mzo_human_tenant TO portal_external_ingress_reader;

-- Publisher mutation capability: closed operations, no direct outbox UPDATE.
CREATE FUNCTION maezo_external.lock_publisher(scope_binding jsonb) RETURNS void
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,maezo_external AS $$
DECLARE item record;
BEGIN
 IF jsonb_typeof(scope_binding) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(scope_binding))<>4
 OR (SELECT count(*) FROM maezo_external.mzo_external_caller_scope WHERE login_role=session_user AND capability='publisher')<>1
 OR NOT EXISTS(SELECT 1 FROM maezo_external.mzo_external_caller_scope WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment' AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation' AND login_role=session_user AND capability='publisher')
 THEN RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='external_case_denied'; END IF;
 PERFORM 1 FROM maezo_external.mzo_external_source_generation WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment' AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 FOR item IN SELECT source_ref FROM maezo_external.mzo_external_source_head WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment' AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation' ORDER BY source_ref COLLATE "C" FOR UPDATE LOOP NULL; END LOOP;
 PERFORM 1 FROM maezo_external.mzo_external_checkpoint_accepted WHERE tenant=scope_binding->>'tenant' AND environment=scope_binding->>'environment' AND engine_name=scope_binding->>'engine_name' AND database_incarnation=scope_binding->>'database_incarnation' FOR UPDATE;
 PERFORM 1 FROM public.mzo_human_tenant WHERE tenant_=scope_binding->>'tenant' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
END $$;
ALTER FUNCTION maezo_external.lock_publisher(jsonb) OWNER TO portal_external_publisher_definer;
REVOKE ALL ON FUNCTION maezo_external.lock_publisher(jsonb) FROM PUBLIC;

CREATE FUNCTION maezo_external.capture_external_case(scope_binding jsonb, publication text, claimant text, deadline timestamptz)
 RETURNS TABLE(packet_bytes bytea,ingress_bytes bytea,canonical_request bytea,native_receipt bytea,covering_receipt bytea,delivery_state text,claim_epoch bigint,claim_expires_at timestamptz,canonical_designation bytea,installation_receipt bytea,designation_digest text,authority_revision bigint,revoked_fingerprints jsonb)
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,maezo_external AS $$
DECLARE o maezo_external.mzo_external_publication_outbox%ROWTYPE; a record; packet bytea; ingress bytea; native bytea; covering bytea; pins jsonb:='[]'::jsonb; pin record;
BEGIN
 PERFORM maezo_external.lock_publisher(scope_binding);
 IF claimant IS NULL OR claimant !~ '^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$' OR deadline IS NULL OR deadline<=clock_timestamp() OR deadline>clock_timestamp()+interval '10 seconds'
 THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 SELECT * INTO o FROM maezo_external.mzo_external_publication_outbox x WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication FOR UPDATE;
 IF NOT FOUND OR o.claim_expires_at>clock_timestamp() OR o.claim_epoch=9223372036854775807 THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 SELECT e.canonical_payload,(SELECT r.canonical_receipt FROM maezo_external.mzo_external_ingress_receipt r WHERE r.tenant=scope_binding->>'tenant' AND r.environment=scope_binding->>'environment' AND r.engine_name=scope_binding->>'engine_name' AND r.database_incarnation=scope_binding->>'database_incarnation' AND r.ingress_id=e.ingress_id) INTO packet,ingress FROM maezo_external.mzo_external_source_event e WHERE e.tenant=scope_binding->>'tenant' AND e.environment=scope_binding->>'environment' AND e.engine_name=scope_binding->>'engine_name' AND e.database_incarnation=scope_binding->>'database_incarnation' AND e.source_ref=o.source_ref AND e.source_revision=o.source_revision;
 IF packet IS NULL OR ingress IS NULL OR octet_length(packet)>65536 OR octet_length(ingress)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 SELECT r.canonical_receipt INTO native FROM maezo_external.mzo_external_publication_receipt r WHERE r.tenant=scope_binding->>'tenant' AND r.environment=scope_binding->>'environment' AND r.engine_name=scope_binding->>'engine_name' AND r.database_incarnation=scope_binding->>'database_incarnation' AND r.publication_id=publication;
 SELECT r.canonical_receipt INTO covering FROM maezo_external.mzo_external_event_terminal t JOIN maezo_external.mzo_external_publication_receipt r USING(tenant,environment,engine_name,database_incarnation,publication_id) WHERE t.tenant=scope_binding->>'tenant' AND t.environment=scope_binding->>'environment' AND t.engine_name=scope_binding->>'engine_name' AND t.database_incarnation=scope_binding->>'database_incarnation' AND t.source_ref=o.source_ref AND t.source_revision=o.source_revision AND t.terminal_kind='covered';
 SELECT e.*,h.rev_ AS native_revision INTO a FROM maezo_external.mzo_external_authority_current c JOIN maezo_external.mzo_external_authority_event e USING(tenant,environment,engine_name,database_incarnation,designation_revision) JOIN public.mzo_human_tenant h ON h.tenant_=c.tenant
 WHERE c.tenant=scope_binding->>'tenant' AND c.environment=scope_binding->>'environment' AND c.engine_name=scope_binding->>'engine_name' AND c.database_incarnation=scope_binding->>'database_incarnation' AND c.designation_digest=e.designation_digest AND c.authority_revision=e.authority_revision;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 FOR pin IN SELECT r.fingerprint_ FROM public.mzo_portal_read_revocation r WHERE r.tenant_=scope_binding->>'tenant' AND r.environment_=scope_binding->>'environment' AND r.engine_=scope_binding->>'engine_name' AND r.incarnation_=scope_binding->>'database_incarnation' ORDER BY r.fingerprint_ COLLATE "C" LOOP
  pins:=pins||jsonb_build_array(pin.fingerprint_);IF octet_length(pins::text)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 END LOOP;
 IF greatest(octet_length(packet),octet_length(ingress),coalesce(octet_length(o.canonical_request),0),coalesce(octet_length(native),0),coalesce(octet_length(covering),0),octet_length(a.canonical_designation),octet_length(a.installation_receipt),octet_length(pins::text))>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 UPDATE maezo_external.mzo_external_publication_outbox x SET claimant_ref=claimant,claim_epoch=o.claim_epoch+1,claim_expires_at=deadline WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication;
 RETURN QUERY SELECT packet,ingress,o.canonical_request,native,covering,o.delivery_state,o.claim_epoch+1,deadline,a.canonical_designation,a.installation_receipt,a.designation_digest::text,a.native_revision,pins;
END $$;
CREATE FUNCTION maezo_external.persist_external_case(scope_binding jsonb,publication text,claimant text,epoch bigint,request bytea)
 RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,maezo_external AS $$
DECLARE o maezo_external.mzo_external_publication_outbox%ROWTYPE; r jsonb; d text;
BEGIN
 PERFORM maezo_external.lock_publisher(scope_binding);
 SELECT * INTO o FROM maezo_external.mzo_external_publication_outbox x WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication FOR UPDATE;
 IF NOT FOUND OR o.claimant_ref IS DISTINCT FROM claimant OR o.claim_epoch IS DISTINCT FROM epoch OR o.claim_expires_at<=clock_timestamp() THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 IF request IS NULL OR octet_length(request)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 r:=convert_from(request,'UTF8')::jsonb;d:=encode(sha256(request),'hex');
 IF r->>'schema' IS DISTINCT FROM 'portal-external-publication-request.v1' OR r->'scope' IS DISTINCT FROM scope_binding OR r->>'publication_id' IS DISTINCT FROM publication OR r->>'kind' IS DISTINCT FROM 'case' THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 IF o.canonical_request IS NOT NULL THEN IF o.canonical_request IS DISTINCT FROM request OR o.request_digest IS DISTINCT FROM d THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;RETURN;END IF;
 UPDATE maezo_external.mzo_external_publication_outbox x SET canonical_request=request,request_digest=d WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication;
END $$;

CREATE FUNCTION maezo_external.transition_external_case(scope_binding jsonb,publication text,claimant text,epoch bigint,action text,receipt bytea,covered_digest text)
 RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,maezo_external AS $$
DECLARE o maezo_external.mzo_external_publication_outbox%ROWTYPE;nr maezo_external.mzo_external_publication_receipt%ROWTYPE;rr jsonb;covered_count bigint;
BEGIN
 PERFORM maezo_external.lock_publisher(scope_binding);
 SELECT * INTO o FROM maezo_external.mzo_external_publication_outbox x WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication FOR UPDATE;
 IF NOT FOUND OR o.claimant_ref IS DISTINCT FROM claimant OR o.claim_epoch IS DISTINCT FROM epoch OR o.claim_expires_at<=clock_timestamp() THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 IF action='live' THEN RETURN o.canonical_request IS NOT NULL; END IF;
 IF action='release' THEN UPDATE maezo_external.mzo_external_publication_outbox x SET claimant_ref=NULL,claim_expires_at=NULL WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication;RETURN true;END IF;
 IF action='uncertain' AND o.canonical_request IS NOT NULL AND o.delivery_state IN ('pending','uncertain') THEN
  UPDATE maezo_external.mzo_external_publication_outbox x SET delivery_state='uncertain' WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication;RETURN true;END IF;
 IF receipt IS NULL OR octet_length(receipt)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;

 IF action='covered' THEN
  SELECT r.* INTO nr FROM maezo_external.mzo_external_event_terminal t JOIN maezo_external.mzo_external_publication_receipt r USING(tenant,environment,engine_name,database_incarnation,publication_id)
  WHERE t.tenant=scope_binding->>'tenant' AND t.environment=scope_binding->>'environment' AND t.engine_name=scope_binding->>'engine_name' AND t.database_incarnation=scope_binding->>'database_incarnation' AND t.source_ref=o.source_ref AND t.source_revision=o.source_revision AND t.source_digest=covered_digest AND t.terminal_kind='covered';
  IF NOT FOUND OR nr.kind<>'case' OR nr.canonical_receipt IS DISTINCT FROM receipt THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
  -- Exact canonical receipt equality above binds the native-computed coverage digest.
  -- Immutable terminal rows are append-only; inspect membership and total count without
  -- materializing an unbounded history array or inventing a cap on source generations.
  SELECT count(*) INTO covered_count FROM maezo_external.mzo_external_event_terminal t
  WHERE t.tenant=scope_binding->>'tenant' AND t.environment=scope_binding->>'environment'
  AND t.engine_name=scope_binding->>'engine_name' AND t.database_incarnation=scope_binding->>'database_incarnation'
  AND t.publication_id=nr.publication_id AND t.terminal_kind='covered';
  rr:=convert_from(nr.canonical_receipt,'UTF8')::jsonb;
  IF (rr->>'coverage_count')::bigint IS DISTINCT FROM covered_count OR covered_count<1
  OR rr->>'coverage_digest' IS NULL OR rr->>'coverage_digest' !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
  UPDATE maezo_external.mzo_external_publication_outbox x SET delivery_state=CASE WHEN o.canonical_request IS NULL THEN 'covered_unprepared' ELSE 'covered' END,covering_publication_id=nr.publication_id,native_receipt=receipt WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication;
  RETURN true;
 END IF;

 IF action IS DISTINCT FROM 'committed' OR o.canonical_request IS NULL THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 SELECT * INTO nr FROM maezo_external.mzo_external_publication_receipt r WHERE r.tenant=scope_binding->>'tenant' AND r.environment=scope_binding->>'environment' AND r.engine_name=scope_binding->>'engine_name' AND r.database_incarnation=scope_binding->>'database_incarnation' AND r.publication_id=publication;
 IF NOT FOUND OR nr.kind<>'case' OR nr.request_digest IS DISTINCT FROM o.request_digest OR nr.canonical_receipt IS DISTINCT FROM receipt
 OR nr.requester_fingerprint IS DISTINCT FROM (convert_from(o.canonical_request,'UTF8')::jsonb->>'requester_fingerprint') THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 UPDATE maezo_external.mzo_external_publication_outbox x SET delivery_state='committed',native_receipt=receipt WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication;
 RETURN true;
END $$;
ALTER FUNCTION maezo_external.capture_external_case(jsonb,text,text,timestamptz) OWNER TO portal_external_publisher_definer;
ALTER FUNCTION maezo_external.persist_external_case(jsonb,text,text,bigint,bytea) OWNER TO portal_external_publisher_definer;
ALTER FUNCTION maezo_external.transition_external_case(jsonb,text,text,bigint,text,bytea,text) OWNER TO portal_external_publisher_definer;
REVOKE ALL ON FUNCTION maezo_external.capture_external_case(jsonb,text,text,timestamptz),maezo_external.persist_external_case(jsonb,text,text,bigint,bytea),maezo_external.transition_external_case(jsonb,text,text,bigint,text,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION maezo_external.capture_external_case(jsonb,text,text,timestamptz),maezo_external.persist_external_case(jsonb,text,text,bigint,bytea),maezo_external.transition_external_case(jsonb,text,text,bigint,text,bytea,text) TO portal_external_case_publisher;

CREATE FUNCTION maezo_external.capture_external_checkpoint(scope_binding jsonb, publication text, claimant text, deadline timestamptz)
 RETURNS TABLE(packet_bytes bytea,ingress_bytes bytea,canonical_request bytea,native_receipt bytea,covering_receipt bytea,delivery_state text,claim_epoch bigint,claim_expires_at timestamptz,canonical_designation bytea,installation_receipt bytea,designation_digest text,authority_revision bigint,revoked_fingerprints jsonb)
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,maezo_external AS $$
DECLARE o maezo_external.mzo_external_checkpoint_outbox%ROWTYPE; a record; packet bytea; ingress bytea; native bytea; covering bytea; pins jsonb:='[]'::jsonb; pin record;
BEGIN
 PERFORM maezo_external.lock_publisher(scope_binding);
 IF claimant IS NULL OR claimant !~ '^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$' OR deadline IS NULL OR deadline<=clock_timestamp() OR deadline>clock_timestamp()+interval '10 seconds'
 THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 SELECT * INTO o FROM maezo_external.mzo_external_checkpoint_outbox x WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication FOR UPDATE;
 IF NOT FOUND OR o.claim_expires_at>clock_timestamp() OR o.claim_epoch=9223372036854775807 THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 SELECT e.canonical_signed_checkpoint,e.canonical_ingress_receipt INTO packet,ingress FROM maezo_external.mzo_external_checkpoint_event e WHERE e.tenant=scope_binding->>'tenant' AND e.environment=scope_binding->>'environment' AND e.engine_name=scope_binding->>'engine_name' AND e.database_incarnation=scope_binding->>'database_incarnation' AND e.checkpoint_ref=o.checkpoint_ref AND e.epoch=o.epoch;
 IF packet IS NULL OR ingress IS NULL OR octet_length(packet)>65536 OR octet_length(ingress)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 SELECT r.canonical_receipt INTO native FROM maezo_external.mzo_external_publication_receipt r WHERE r.tenant=scope_binding->>'tenant' AND r.environment=scope_binding->>'environment' AND r.engine_name=scope_binding->>'engine_name' AND r.database_incarnation=scope_binding->>'database_incarnation' AND r.publication_id=publication;

 SELECT e.*,h.rev_ AS native_revision INTO a FROM maezo_external.mzo_external_authority_current c JOIN maezo_external.mzo_external_authority_event e USING(tenant,environment,engine_name,database_incarnation,designation_revision) JOIN public.mzo_human_tenant h ON h.tenant_=c.tenant
 WHERE c.tenant=scope_binding->>'tenant' AND c.environment=scope_binding->>'environment' AND c.engine_name=scope_binding->>'engine_name' AND c.database_incarnation=scope_binding->>'database_incarnation' AND c.designation_digest=e.designation_digest AND c.authority_revision=e.authority_revision;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 FOR pin IN SELECT r.fingerprint_ FROM public.mzo_portal_read_revocation r WHERE r.tenant_=scope_binding->>'tenant' AND r.environment_=scope_binding->>'environment' AND r.engine_=scope_binding->>'engine_name' AND r.incarnation_=scope_binding->>'database_incarnation' ORDER BY r.fingerprint_ COLLATE "C" LOOP
  pins:=pins||jsonb_build_array(pin.fingerprint_);IF octet_length(pins::text)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 END LOOP;
 IF greatest(octet_length(packet),octet_length(ingress),coalesce(octet_length(o.canonical_request),0),coalesce(octet_length(native),0),coalesce(octet_length(covering),0),octet_length(a.canonical_designation),octet_length(a.installation_receipt),octet_length(pins::text))>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='external_case_unavailable'; END IF;
 UPDATE maezo_external.mzo_external_checkpoint_outbox x SET claimant_ref=claimant,claim_epoch=o.claim_epoch+1,claim_expires_at=deadline WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication;
 RETURN QUERY SELECT packet,ingress,o.canonical_request,native,covering,o.delivery_state,o.claim_epoch+1,deadline,a.canonical_designation,a.installation_receipt,a.designation_digest::text,a.native_revision,pins;
END $$;
CREATE FUNCTION maezo_external.persist_external_checkpoint(scope_binding jsonb,publication text,claimant text,epoch bigint,request bytea)
 RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,maezo_external AS $$
DECLARE o maezo_external.mzo_external_checkpoint_outbox%ROWTYPE; r jsonb; d text;
BEGIN
 PERFORM maezo_external.lock_publisher(scope_binding);
 SELECT * INTO o FROM maezo_external.mzo_external_checkpoint_outbox x WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication FOR UPDATE;
 IF NOT FOUND OR o.claimant_ref IS DISTINCT FROM claimant OR o.claim_epoch IS DISTINCT FROM epoch OR o.claim_expires_at<=clock_timestamp() THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 IF request IS NULL OR octet_length(request)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 r:=convert_from(request,'UTF8')::jsonb;d:=encode(sha256(request),'hex');
 IF r->>'schema' IS DISTINCT FROM 'portal-external-publication-request.v1' OR r->'scope' IS DISTINCT FROM scope_binding OR r->>'publication_id' IS DISTINCT FROM publication OR r->>'kind' IS DISTINCT FROM 'checkpoint' THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 IF o.canonical_request IS NOT NULL THEN IF o.canonical_request IS DISTINCT FROM request OR o.request_digest IS DISTINCT FROM d THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;RETURN;END IF;
 UPDATE maezo_external.mzo_external_checkpoint_outbox x SET canonical_request=request,request_digest=d WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication;
END $$;

CREATE FUNCTION maezo_external.transition_external_checkpoint(scope_binding jsonb,publication text,claimant text,epoch bigint,action text,receipt bytea,covered_digest text)
 RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,maezo_external AS $$
DECLARE o maezo_external.mzo_external_checkpoint_outbox%ROWTYPE;nr maezo_external.mzo_external_publication_receipt%ROWTYPE;rr jsonb;covered_count bigint;
BEGIN
 PERFORM maezo_external.lock_publisher(scope_binding);
 SELECT * INTO o FROM maezo_external.mzo_external_checkpoint_outbox x WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication FOR UPDATE;
 IF NOT FOUND OR o.claimant_ref IS DISTINCT FROM claimant OR o.claim_epoch IS DISTINCT FROM epoch OR o.claim_expires_at<=clock_timestamp() THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 IF action='live' THEN RETURN o.canonical_request IS NOT NULL; END IF;
 IF action='release' THEN UPDATE maezo_external.mzo_external_checkpoint_outbox x SET claimant_ref=NULL,claim_expires_at=NULL WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication;RETURN true;END IF;
 IF action='uncertain' AND o.canonical_request IS NOT NULL AND o.delivery_state IN ('pending','uncertain') THEN
  UPDATE maezo_external.mzo_external_checkpoint_outbox x SET delivery_state='uncertain' WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication;RETURN true;END IF;
 IF receipt IS NULL OR octet_length(receipt)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;

 IF action IS DISTINCT FROM 'committed' OR o.canonical_request IS NULL THEN RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='external_case_invalid'; END IF;
 SELECT * INTO nr FROM maezo_external.mzo_external_publication_receipt r WHERE r.tenant=scope_binding->>'tenant' AND r.environment=scope_binding->>'environment' AND r.engine_name=scope_binding->>'engine_name' AND r.database_incarnation=scope_binding->>'database_incarnation' AND r.publication_id=publication;
 IF NOT FOUND OR nr.kind<>'checkpoint' OR nr.request_digest IS DISTINCT FROM o.request_digest OR nr.canonical_receipt IS DISTINCT FROM receipt
 OR nr.requester_fingerprint IS DISTINCT FROM (convert_from(o.canonical_request,'UTF8')::jsonb->>'requester_fingerprint') THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='external_case_conflict'; END IF;
 UPDATE maezo_external.mzo_external_checkpoint_outbox x SET delivery_state='committed',native_receipt=receipt WHERE x.tenant=scope_binding->>'tenant' AND x.environment=scope_binding->>'environment' AND x.engine_name=scope_binding->>'engine_name' AND x.database_incarnation=scope_binding->>'database_incarnation' AND x.publication_id=publication;
 RETURN true;
END $$;
ALTER FUNCTION maezo_external.capture_external_checkpoint(jsonb,text,text,timestamptz) OWNER TO portal_external_publisher_definer;
ALTER FUNCTION maezo_external.persist_external_checkpoint(jsonb,text,text,bigint,bytea) OWNER TO portal_external_publisher_definer;
ALTER FUNCTION maezo_external.transition_external_checkpoint(jsonb,text,text,bigint,text,bytea,text) OWNER TO portal_external_publisher_definer;
REVOKE ALL ON FUNCTION maezo_external.capture_external_checkpoint(jsonb,text,text,timestamptz),maezo_external.persist_external_checkpoint(jsonb,text,text,bigint,bytea),maezo_external.transition_external_checkpoint(jsonb,text,text,bigint,text,bytea,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION maezo_external.capture_external_checkpoint(jsonb,text,text,timestamptz),maezo_external.persist_external_checkpoint(jsonb,text,text,bigint,bytea),maezo_external.transition_external_checkpoint(jsonb,text,text,bigint,text,bytea,text) TO portal_external_case_publisher;

GRANT EXECUTE ON FUNCTION maezo_external.receipt_json(jsonb) TO portal_external_publisher_definer;
GRANT SELECT ON maezo_external.mzo_external_caller_scope,maezo_external.mzo_external_source_generation,
 maezo_external.mzo_external_source_head,maezo_external.mzo_external_source_event,
 maezo_external.mzo_external_ingress_receipt,maezo_external.mzo_external_publication_outbox,
 maezo_external.mzo_external_checkpoint_outbox,maezo_external.mzo_external_checkpoint_accepted,
 maezo_external.mzo_external_checkpoint_event,maezo_external.mzo_external_authority_current,
 maezo_external.mzo_external_authority_event,maezo_external.mzo_external_publication_receipt,
 maezo_external.mzo_external_event_terminal,public.mzo_human_tenant,public.mzo_portal_read_revocation
 TO portal_external_publisher_definer;
GRANT UPDATE(accepted_generation) ON maezo_external.mzo_external_source_generation TO portal_external_publisher_definer;
GRANT UPDATE(payload_digest) ON maezo_external.mzo_external_source_head TO portal_external_publisher_definer;
GRANT UPDATE(epoch) ON maezo_external.mzo_external_checkpoint_accepted TO portal_external_publisher_definer;
GRANT UPDATE(rev_) ON public.mzo_human_tenant TO portal_external_publisher_definer;
GRANT UPDATE(canonical_request,request_digest,delivery_state,native_receipt,claimant_ref,claim_epoch,claim_expires_at,covering_publication_id)
 ON maezo_external.mzo_external_publication_outbox,maezo_external.mzo_external_checkpoint_outbox TO portal_external_publisher_definer;
-- Native lock privileges, separate from installer and source mutation authority.
GRANT UPDATE(payload_digest) ON maezo_external.mzo_external_source_head TO portal_external_case_runtime;
GRANT UPDATE(epoch) ON maezo_external.mzo_external_checkpoint_accepted TO portal_external_case_runtime;
GRANT SELECT,UPDATE(rev_) ON public.mzo_human_tenant TO portal_external_case_runtime;
GRANT SELECT ON public.mzo_portal_read_revocation TO portal_external_case_runtime;

/* BEGIN IDENTITY DATABASE INSTALLATION
 * Execute the SQL between these markers separately against the existing dedicated
 * identity database, after migration0012. It is deliberately not run on the engine
 * database and never run by application startup. Actual LOGIN mappings remain empty.

CREATE SCHEMA portal_identity;
REVOKE ALL ON SCHEMA portal_identity FROM PUBLIC;
CREATE ROLE portal_external_identity_reader NOLOGIN;
CREATE ROLE portal_external_identity_bff NOLOGIN;
GRANT USAGE ON SCHEMA portal_identity TO portal_external_identity_reader,portal_external_identity_bff;
CREATE TABLE portal_identity.external_login_tenant(login_role name PRIMARY KEY,tenant text NOT NULL);
REVOKE ALL ON portal_identity.external_login_tenant FROM PUBLIC;
GRANT SELECT ON portal_identity.external_login_tenant TO portal_external_identity_reader;
CREATE FUNCTION portal_identity.lock_external_session(requested_secret_hash text)
 RETURNS TABLE(session_tenant text,secret_hash text,expires_at timestamptz,session_payload text,
 membership_tenant text,issuer text,subject text,principal_ref text,membership_payload text)
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,portal_identity AS $identity$
DECLARE installed_tenant text;s record;m record;identity jsonb;
BEGIN
 IF requested_secret_hash IS NULL OR requested_secret_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='external_case_denied'; END IF;
 SELECT x.tenant INTO installed_tenant FROM portal_identity.external_login_tenant x WHERE x.login_role=session_user;
 IF installed_tenant IS NULL THEN RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='external_case_denied'; END IF;
 SELECT x.* INTO s FROM public.portal_sessions x WHERE x.tenant=installed_tenant AND x.secret_hash=requested_secret_hash FOR SHARE;
 IF NOT FOUND OR s.expires_at<=clock_timestamp() OR octet_length(s.payload)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='external_case_denied'; END IF;
 identity:=s.payload::jsonb;
 IF jsonb_typeof(identity) IS DISTINCT FROM 'object' OR jsonb_typeof(identity->'issuer') IS DISTINCT FROM 'string'
 OR jsonb_typeof(identity->'subject') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='external_case_denied'; END IF;
 SELECT x.* INTO m FROM public.portal_memberships x WHERE x.tenant=installed_tenant AND x.issuer=identity->>'issuer' AND x.subject=identity->>'subject' FOR SHARE;
 IF NOT FOUND OR octet_length(s.payload)+octet_length(m.payload)>65536 THEN RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='external_case_denied'; END IF;
 RETURN QUERY SELECT s.tenant,s.secret_hash,s.expires_at,s.payload,m.tenant,m.issuer,m.subject,m.principal_ref,m.payload;
END $identity$;
ALTER FUNCTION portal_identity.lock_external_session(text) OWNER TO portal_external_identity_reader;
REVOKE ALL ON FUNCTION portal_identity.lock_external_session(text) FROM PUBLIC;
GRANT SELECT,UPDATE(payload) ON public.portal_sessions,public.portal_memberships TO portal_external_identity_reader;
GRANT EXECUTE ON FUNCTION portal_identity.lock_external_session(text) TO portal_external_identity_bff;
-- BFF role gets no membership UPDATE/DELETE or owner membership. Installation owner
-- alone binds each authenticated LOGIN role to exactly one installed tenant.
END IDENTITY DATABASE INSTALLATION */
