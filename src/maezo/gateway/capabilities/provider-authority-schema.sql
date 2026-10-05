-- ADR-0063 source-plane installation template. DBA replaces :schema through
-- reviewed identifier binding, owns installation and supplies qualified OID/ACL
-- descriptor. NO runtime calls this DDL, creates roles or populates authority.
-- Install in dedicated source schema, never maezo_native or public.
CREATE TABLE evidence (
 tenant_ref text NOT NULL, legal_entity_ref text NOT NULL, provider_ref text NOT NULL,
 instrument_ref text NOT NULL, business_revision text NOT NULL,
 evidence_ref text NOT NULL, snapshot_bytes bytea NOT NULL CHECK(octet_length(snapshot_bytes)<=65536),
 snapshot_digest char(64) NOT NULL CHECK(snapshot_digest ~ '^[0-9a-f]{64}$'),
 received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_ref,legal_entity_ref,provider_ref,instrument_ref,business_revision),
 UNIQUE(tenant_ref,legal_entity_ref,evidence_ref)
);
CREATE TABLE source_binding (
 binding_ref text PRIMARY KEY, binding_bytes bytea NOT NULL CHECK(octet_length(binding_bytes)<=65536),
 binding_digest char(64) NOT NULL CHECK(binding_digest ~ '^[0-9a-f]{64}$'),
 revoked boolean NOT NULL DEFAULT false
);
CREATE TABLE authority_proof (
 proof_ref text PRIMARY KEY, binding_ref text NOT NULL REFERENCES source_binding(binding_ref),
 evidence_ref text NOT NULL, snapshot_digest char(64) NOT NULL CHECK(snapshot_digest ~ '^[0-9a-f]{64}$'),
 source_revision_ref text NOT NULL, currentness_ref text NOT NULL,
 state text NOT NULL CHECK(state IN ('validated','enabled','expired','revoked','invalid')),
 checked_at timestamptz NOT NULL, valid_until timestamptz NOT NULL,
 CHECK(valid_until>checked_at)
);
CREATE TABLE publication (
 publication_ref text PRIMARY KEY, proof_ref text NOT NULL REFERENCES authority_proof(proof_ref),
 tenant_ref text NOT NULL, legal_entity_ref text NOT NULL, provider_ref text NOT NULL,
 instrument_ref text NOT NULL, business_revision text NOT NULL,
 snapshot_digest char(64) NOT NULL, receipt_ref text NOT NULL UNIQUE,
 published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(tenant_ref,legal_entity_ref,provider_ref,instrument_ref,business_revision)
 REFERENCES evidence(tenant_ref,legal_entity_ref,provider_ref,instrument_ref,business_revision)
);
CREATE TABLE instrument_head (
 tenant_ref text NOT NULL, legal_entity_ref text NOT NULL, provider_ref text NOT NULL,
 instrument_ref text NOT NULL, business_revision text NOT NULL,
 publication_ref text NOT NULL REFERENCES publication(publication_ref),
 head_revision bigint NOT NULL CHECK(head_revision>0),
 PRIMARY KEY(tenant_ref,legal_entity_ref,provider_ref,instrument_ref)
);
CREATE FUNCTION immutable_source_record() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog AS $$ BEGIN
 RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='immutable_source_record';
END $$;
CREATE TRIGGER evidence_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON evidence
 FOR EACH STATEMENT EXECUTE FUNCTION immutable_source_record();
CREATE TRIGGER publication_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON publication
 FOR EACH STATEMENT EXECUTE FUNCTION immutable_source_record();
REVOKE ALL ON evidence,source_binding,authority_proof,publication,instrument_head FROM PUBLIC;
REVOKE ALL ON FUNCTION immutable_source_record() FROM PUBLIC;
-- Owner installs a separate validation login with SELECT/INSERT/UPDATE solely on
-- source_binding/authority_proof. Its independently qualified upstream must verify
-- actual signature/mandate/terms/purpose; this SQL does not create professional acts.
-- Publisher: SELECT all; INSERT evidence; EXECUTE qualified publish_validated only.
-- Never grant publisher UPDATE/INSERT authority_proof/source_binding, nor ownership.
-- Reader: SELECT only all five tables. No column write privileges, CREATE, TEMP,
-- elevated role or any role membership. All tables/functions/schema owner separate.
-- Readers/publisher/validation roles must not inherit each other or owner.
-- No default grants or populated authority rows ship with this template.

-- Installer search_path is exactly this dedicated source schema (no public).
-- Function owner is independent source owner. EXECUTE granted only publisher.
-- Locking proofs as owner avoids granting publisher UPDATE needed for FOR SHARE.
CREATE FUNCTION publish_validated(request_scope jsonb, requested_revision text,
 requested_proof text, expected_head bigint) RETURNS text LANGUAGE plpgsql
 SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE e evidence%ROWTYPE; a authority_proof%ROWTYPE; b source_binding%ROWTYPE;
 h instrument_head%ROWTYPE; s jsonb; scope jsonb; publication_id text;
BEGIN
 IF expected_head IS NULL OR expected_head<0 OR jsonb_typeof(request_scope)<>'object' THEN
  RAISE EXCEPTION USING ERRCODE='P7E01',MESSAGE='source_invalid';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(
   jsonb_build_array(request_scope->>'tenant_ref',request_scope->>'legal_entity_ref',
   request_scope->>'provider_ref')::text,0));
 SELECT * INTO a FROM authority_proof WHERE proof_ref=requested_proof FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='authority_unproven'; END IF;
 SELECT * INTO b FROM source_binding WHERE binding_ref=a.binding_ref FOR SHARE;
 IF NOT FOUND OR b.revoked OR a.state NOT IN ('validated','enabled')
 OR a.checked_at>clock_timestamp() OR a.valid_until<=clock_timestamp() THEN
  RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='authority_unproven';
 END IF;
 scope:=convert_from(b.binding_bytes,'UTF8')::jsonb;
 FOREACH publication_id IN ARRAY ARRAY['tenant_ref','legal_entity_ref','provider_ref','principal_ref',
   'task_ref','source_authority_ref','policy_revision','purpose_policy_ref','purpose_ref','data_classification'] LOOP
  IF scope->>publication_id IS DISTINCT FROM request_scope->>publication_id THEN
   RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='authority_unproven';
  END IF;
 END LOOP;
 IF (scope->>'valid_until')::timestamptz<=clock_timestamp()
 OR NOT (scope->'clause_purposes' ? (request_scope->>'clause_purpose')) THEN
  RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='authority_unproven';
 END IF;
 SELECT * INTO e FROM evidence WHERE tenant_ref=request_scope->>'tenant_ref'
 AND legal_entity_ref=request_scope->>'legal_entity_ref' AND provider_ref=request_scope->>'provider_ref'
 AND instrument_ref=request_scope->>'contract_instrument_ref' AND business_revision=requested_revision;
 IF NOT FOUND OR e.evidence_ref<>a.evidence_ref OR e.snapshot_digest<>a.snapshot_digest
 OR encode(sha256(e.snapshot_bytes),'hex')<>e.snapshot_digest
 OR encode(sha256(b.binding_bytes),'hex')<>b.binding_digest THEN
  RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='authority_unproven';
 END IF;
 s:=convert_from(e.snapshot_bytes,'UTF8')::jsonb;
 IF s->>'tenant_ref' IS DISTINCT FROM e.tenant_ref OR s->>'legal_entity_ref' IS DISTINCT FROM e.legal_entity_ref
 OR s->>'provider_ref' IS DISTINCT FROM e.provider_ref OR s->>'contract_instrument_ref' IS DISTINCT FROM e.instrument_ref
 OR s->>'business_revision' IS DISTINCT FROM requested_revision OR s->>'admission_state' IS DISTINCT FROM a.state
 OR s->>'source_revision_ref' IS DISTINCT FROM a.source_revision_ref OR s->>'currentness_ref' IS DISTINCT FROM a.currentness_ref
 OR (s->>'valid_from')::timestamptz>clock_timestamp() OR (s->>'declared_at')::timestamptz>clock_timestamp()
 OR (s->>'valid_until')::timestamptz<=clock_timestamp()
 OR (s->>'valid_until')::timestamptz>LEAST(a.valid_until,(scope->>'valid_until')::timestamptz) THEN
  RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='authority_unproven';
 END IF;
 SELECT * INTO h FROM instrument_head WHERE tenant_ref=e.tenant_ref AND legal_entity_ref=e.legal_entity_ref
 AND provider_ref=e.provider_ref AND instrument_ref=e.instrument_ref FOR UPDATE;
 publication_id:=s->>'source_publication_ref';
 IF FOUND AND h.publication_ref=publication_id THEN RETURN publication_id; END IF;
 IF COALESCE(h.head_revision,0)<>expected_head THEN
  RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='source_revision_conflict';
 END IF;
 INSERT INTO publication(publication_ref,proof_ref,tenant_ref,legal_entity_ref,provider_ref,
 instrument_ref,business_revision,snapshot_digest,receipt_ref)
 VALUES(publication_id,requested_proof,e.tenant_ref,e.legal_entity_ref,e.provider_ref,
 e.instrument_ref,e.business_revision,e.snapshot_digest,s->>'authority_receipt_ref');
 INSERT INTO instrument_head(tenant_ref,legal_entity_ref,provider_ref,instrument_ref,
 business_revision,publication_ref,head_revision)
 VALUES(e.tenant_ref,e.legal_entity_ref,e.provider_ref,e.instrument_ref,e.business_revision,publication_id,expected_head+1)
 ON CONFLICT(tenant_ref,legal_entity_ref,provider_ref,instrument_ref) DO UPDATE
 SET business_revision=EXCLUDED.business_revision,publication_ref=EXCLUDED.publication_ref,head_revision=EXCLUDED.head_revision;
 RETURN publication_id;
END $$;
REVOKE ALL ON FUNCTION publish_validated(jsonb,text,text,bigint) FROM PUBLIC;
-- Revised grants: publisher INSERT evidence only, SELECT all five tables and
-- EXECUTE publish_validated(jsonb,text,text,bigint). No direct publication/head DML.
-- Relation immutability and qualified function owner prevent authority fabrication.

CREATE FUNCTION protect_authority_history() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog AS $$ BEGIN
 IF TG_TABLE_NAME='source_binding' THEN
  IF ROW(NEW.binding_ref,NEW.binding_bytes,NEW.binding_digest)
   IS DISTINCT FROM ROW(OLD.binding_ref,OLD.binding_bytes,OLD.binding_digest)
   OR (OLD.revoked AND NOT NEW.revoked) THEN
   RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='immutable_authority';
  END IF;
 ELSIF TG_TABLE_NAME='authority_proof' THEN
  IF ROW(NEW.proof_ref,NEW.binding_ref,NEW.evidence_ref,NEW.snapshot_digest,NEW.source_revision_ref,
   NEW.currentness_ref,NEW.checked_at,NEW.valid_until)
   IS DISTINCT FROM ROW(OLD.proof_ref,OLD.binding_ref,OLD.evidence_ref,OLD.snapshot_digest,OLD.source_revision_ref,
   OLD.currentness_ref,OLD.checked_at,OLD.valid_until)
   OR (NEW.state<>OLD.state AND NEW.state NOT IN ('invalid','expired','revoked'))
   OR (OLD.state IN ('invalid','expired','revoked') AND NEW.state<>OLD.state) THEN
   RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='immutable_authority';
  END IF;
 ELSE RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='immutable_authority'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION protect_authority_history() FROM PUBLIC;
CREATE TRIGGER binding_history BEFORE UPDATE ON source_binding
 FOR EACH ROW EXECUTE FUNCTION protect_authority_history();
CREATE TRIGGER proof_history BEFORE UPDATE ON authority_proof
 FOR EACH ROW EXECUTE FUNCTION protect_authority_history();
CREATE TRIGGER binding_no_delete BEFORE DELETE OR TRUNCATE ON source_binding
 FOR EACH STATEMENT EXECUTE FUNCTION immutable_source_record();
CREATE TRIGGER proof_no_delete BEFORE DELETE OR TRUNCATE ON authority_proof
 FOR EACH STATEMENT EXECUTE FUNCTION immutable_source_record();
