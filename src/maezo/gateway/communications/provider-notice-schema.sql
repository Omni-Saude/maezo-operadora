-- Installation template only. Runtime receives EXECUTE, never authority/table DML.
-- DBA creates dedicated schema owner and independent validator/producer/ack roles.
-- Replace portal_provider_notice only with a validated descriptor schema identifier.
REVOKE ALL ON SCHEMA portal_provider_notice FROM PUBLIC;
CREATE TABLE portal_provider_notice.authority (
 tenant text NOT NULL, environment text NOT NULL, notice_ref text NOT NULL,
 command_ref text NOT NULL, request_digest char(64) NOT NULL,
 scope jsonb NOT NULL, intent jsonb NOT NULL,
 case_ref text NOT NULL, provider_ref text NOT NULL,
 recipient_principal_ref text NOT NULL, recipient_issuer text NOT NULL,
 recipient_subject text NOT NULL, membership_revision bigint NOT NULL CHECK(membership_revision>=0),
 body_ref text NOT NULL, content_digest char(64) NOT NULL,
 notice_revision text NOT NULL, source_receipt_ref text NOT NULL,
 acknowledgement_policy_ref text NOT NULL, channel_kind text NOT NULL CHECK(channel_kind='protected_portal_ack'),
 state text NOT NULL CHECK(state IN ('received','validated','enabled','revoked')),
 valid_from timestamptz NOT NULL, valid_until timestamptz NOT NULL,
 CHECK(valid_from<valid_until), PRIMARY KEY(tenant,environment,notice_ref),
 UNIQUE(tenant,environment,command_ref), CHECK(request_digest ~ '^[0-9a-f]{64}$'),
 CHECK(content_digest ~ '^[0-9a-f]{64}$')
);
CREATE TABLE portal_provider_notice.notice (
 tenant text NOT NULL, environment text NOT NULL, notice_ref text NOT NULL,
 command_ref text NOT NULL, request_digest char(64) NOT NULL,
 notice_revision text NOT NULL, source_receipt_ref text NOT NULL,
 metadata_receipt_ref text NOT NULL, authored_at timestamptz NOT NULL,
 PRIMARY KEY(tenant,environment,notice_ref), UNIQUE(tenant,environment,command_ref)
);
CREATE TABLE portal_provider_notice.acknowledgement (
 tenant text NOT NULL, environment text NOT NULL, notice_ref text NOT NULL,
 command_ref text NOT NULL, request_digest char(64) NOT NULL,
 principal_ref text NOT NULL, session_ref text NOT NULL, membership_revision bigint NOT NULL,
 notice_revision text NOT NULL, content_digest char(64) NOT NULL,
 receipt_ref text NOT NULL, acknowledged_at timestamptz NOT NULL,
 PRIMARY KEY(tenant,environment,notice_ref), UNIQUE(tenant,environment,principal_ref,command_ref)
);
CREATE TABLE portal_provider_notice.audit (
 tenant text NOT NULL, environment text NOT NULL, audit_ref text NOT NULL,
 notice_ref text NOT NULL, operation text NOT NULL CHECK(operation IN ('prepare','acknowledge')),
 actor_ref text NOT NULL, actor_session_ref text, request_digest char(64) NOT NULL,
 source_receipt_ref text NOT NULL, recorded_at timestamptz NOT NULL,
 PRIMARY KEY(tenant,environment,audit_ref)
);
CREATE TABLE portal_provider_notice.outbox (
 tenant text NOT NULL, environment text NOT NULL, event_ref text NOT NULL,
 notice_ref text NOT NULL, notice_revision text NOT NULL, receipt_ref text NOT NULL,
 event_kind text NOT NULL CHECK(event_kind IN ('notice_available','notice_acknowledged')),
 occurred_at timestamptz NOT NULL,
 PRIMARY KEY(tenant,environment,event_ref), UNIQUE(tenant,environment,notice_ref,event_kind)
);
REVOKE ALL ON ALL TABLES IN SCHEMA portal_provider_notice FROM PUBLIC;

-- Receipts and journal are append-only. Revocation never rewrites a historical fact.
CREATE FUNCTION portal_provider_notice.immutable_history() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION USING ERRCODE='P7E04', MESSAGE='immutable history'; END $$;
CREATE TRIGGER immutable_notice BEFORE UPDATE OR DELETE ON portal_provider_notice.notice
 FOR EACH ROW EXECUTE FUNCTION portal_provider_notice.immutable_history();
CREATE TRIGGER immutable_ack BEFORE UPDATE OR DELETE ON portal_provider_notice.acknowledgement
 FOR EACH ROW EXECUTE FUNCTION portal_provider_notice.immutable_history();
CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON portal_provider_notice.audit
 FOR EACH ROW EXECUTE FUNCTION portal_provider_notice.immutable_history();
CREATE TRIGGER immutable_outbox BEFORE UPDATE OR DELETE ON portal_provider_notice.outbox
 FOR EACH ROW EXECUTE FUNCTION portal_provider_notice.immutable_history();

CREATE FUNCTION portal_provider_notice.protect_authority() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-'state'-'valid_until')<>(to_jsonb(OLD)-'state'-'valid_until')
 OR NEW.valid_until>OLD.valid_until OR OLD.state='revoked'
 OR NOT (NEW.state=OLD.state OR (OLD.state='received' AND NEW.state='validated')
 OR (OLD.state='validated' AND NEW.state='enabled') OR NEW.state='revoked') THEN
  RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='immutable authority'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER authority_history BEFORE UPDATE OR DELETE ON portal_provider_notice.authority
 FOR EACH ROW EXECUTE FUNCTION portal_provider_notice.protect_authority();

CREATE FUNCTION portal_provider_notice.prepare_notice(
 p_scope jsonb,p_intent jsonb,p_command text,p_digest text,p_metadata text,p_audit text,p_until timestamptz
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a portal_provider_notice.authority%ROWTYPE; n portal_provider_notice.notice%ROWTYPE;
 k portal_provider_notice.acknowledgement%ROWTYPE;
BEGIN
 SELECT * INTO a FROM portal_provider_notice.authority
 WHERE tenant=p_scope->>'tenant_ref' AND environment=p_scope->>'environment'
 AND notice_ref=p_intent->>'notice_ref' FOR SHARE;
 IF NOT FOUND OR a.state<>'enabled' OR a.scope<>p_scope OR a.intent<>p_intent
 OR session_user::text IS DISTINCT FROM p_scope->>'producer_role'
 OR a.command_ref<>p_command OR a.request_digest<>p_digest
 OR a.body_ref<>p_intent->>'authorized_content_ref'
 OR a.acknowledgement_policy_ref<>p_intent->>'delivery_policy_ref'
 OR a.notice_revision<>p_scope->>'business_revision'
 OR clock_timestamp()<a.valid_from OR clock_timestamp()>=LEAST(a.valid_until,p_until)
 THEN RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='authority unproven'; END IF;
 -- This grants metadata columns only to the source owner, never content ciphertext/key.
 PERFORM 1 FROM portal_communication.content
 WHERE tenant=a.tenant AND environment=a.environment AND case_ref=a.case_ref
 AND body_ref=a.body_ref AND content_digest=a.content_digest FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='protected content unavailable'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(a.tenant||'|'||a.environment||'|'||a.notice_ref,0));
 SELECT * INTO n FROM portal_provider_notice.notice
 WHERE tenant=a.tenant AND environment=a.environment AND notice_ref=a.notice_ref;
 IF FOUND THEN
  IF n.command_ref<>p_command OR n.request_digest<>p_digest OR n.notice_revision<>a.notice_revision
  OR n.source_receipt_ref<>a.source_receipt_ref
  THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='notice conflict'; END IF;
 ELSE
  IF clock_timestamp()>=LEAST(a.valid_until,p_until) THEN
   RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='authority expired'; END IF;
  INSERT INTO portal_provider_notice.audit VALUES(a.tenant,a.environment,p_audit,a.notice_ref,'prepare',
   p_scope->>'principal_ref',NULL,p_digest,a.source_receipt_ref,clock_timestamp());
  INSERT INTO portal_provider_notice.notice VALUES(a.tenant,a.environment,a.notice_ref,p_command,p_digest,
   a.notice_revision,a.source_receipt_ref,p_metadata,clock_timestamp()) RETURNING * INTO n;
  INSERT INTO portal_provider_notice.outbox VALUES(a.tenant,a.environment,p_metadata,a.notice_ref,
   a.notice_revision,p_metadata,'notice_available',clock_timestamp());
 END IF;
 SELECT * INTO k FROM portal_provider_notice.acknowledgement
 WHERE tenant=a.tenant AND environment=a.environment AND notice_ref=a.notice_ref;
 RETURN jsonb_build_object('notice_ref',n.notice_ref,'notice_revision',n.notice_revision,
  'delivery_status',CASE WHEN k.receipt_ref IS NULL THEN 'pending' ELSE 'delivered' END,
  'metadata_publication_receipt_ref',n.metadata_receipt_ref,'provider_delivery_receipt_ref',k.receipt_ref);
END $$;


CREATE FUNCTION portal_provider_notice.recipient_authority(
 p_tenant text,p_environment text,p_notice text,p_secret_hash text,p_until timestamptz
) RETURNS portal_provider_notice.authority LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a portal_provider_notice.authority%ROWTYPE; s record; session_data jsonb; member_data jsonb;
BEGIN
 -- Same lock order as Python boundary: original session/member, then authority/journal.
 SELECT * INTO s FROM portal_communication.lock_session(p_secret_hash);
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='session unavailable'; END IF;
 session_data=s.session_payload::jsonb; member_data=s.membership_payload::jsonb;
 SELECT * INTO a FROM portal_provider_notice.authority
 WHERE tenant=p_tenant AND environment=p_environment AND notice_ref=p_notice FOR SHARE;
 IF NOT FOUND OR a.state<>'enabled' OR clock_timestamp()<a.valid_from
 OR clock_timestamp()>=LEAST(a.valid_until,p_until,s.expires_at,
  (member_data->>'reviewed_until')::timestamptz)
 OR s.session_tenant<>a.tenant OR s.membership_tenant<>a.tenant
 OR member_data->>'reviewed_until' IS NULL OR session_data->>'session_ref' IS NULL
 OR member_data->>'tenant' IS DISTINCT FROM a.tenant OR member_data->>'audience' IS DISTINCT FROM 'provider'
 OR (member_data->>'revoked')::boolean IS DISTINCT FROM false OR s.secret_hash<>p_secret_hash
 OR session_data->>'secret_hash' IS DISTINCT FROM p_secret_hash OR session_data->>'issuer' IS DISTINCT FROM a.recipient_issuer
 OR session_data->>'subject' IS DISTINCT FROM a.recipient_subject OR member_data->>'issuer' IS DISTINCT FROM a.recipient_issuer
 OR member_data->>'subject' IS DISTINCT FROM a.recipient_subject OR session_data->>'principal_ref' IS DISTINCT FROM a.recipient_principal_ref
 OR member_data->>'principal_ref' IS DISTINCT FROM a.recipient_principal_ref
 OR (session_data->>'membership_revision')::bigint IS DISTINCT FROM a.membership_revision
 OR (member_data->>'revision')::bigint IS DISTINCT FROM a.membership_revision
 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(member_data->'subject_bindings') b
   WHERE b->>'kind'='provider' AND b->>'resource_ref'=a.provider_ref)

 THEN RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='acknowledgement authority unproven'; END IF;
 PERFORM 1 FROM portal_communication.content WHERE tenant=a.tenant AND environment=a.environment
 AND case_ref=a.case_ref AND body_ref=a.body_ref AND content_digest=a.content_digest FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='P7E03',MESSAGE='protected content unavailable'; END IF;
 RETURN a;
END $$;
CREATE FUNCTION portal_provider_notice.acknowledge_notice(
 p_tenant text,p_environment text,p_notice text,p_command text,p_digest text,p_revision text,
 p_content_digest text,p_secret_hash text,p_receipt text,p_audit text,p_until timestamptz
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a portal_provider_notice.authority%ROWTYPE; n portal_provider_notice.notice%ROWTYPE;
 k portal_provider_notice.acknowledgement%ROWTYPE; s record; session_data jsonb; member_data jsonb;
BEGIN
 a=portal_provider_notice.recipient_authority(p_tenant,p_environment,p_notice,p_secret_hash,p_until);
 SELECT * INTO s FROM portal_communication.lock_session(p_secret_hash);
 session_data=s.session_payload::jsonb; member_data=s.membership_payload::jsonb;
 IF a.notice_revision IS DISTINCT FROM p_revision OR a.content_digest IS DISTINCT FROM p_content_digest THEN
  RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='notice mismatch'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(a.tenant||'|'||a.environment||'|'||a.notice_ref,0));
 SELECT * INTO n FROM portal_provider_notice.notice
 WHERE tenant=a.tenant AND environment=a.environment AND notice_ref=a.notice_ref;
 IF NOT FOUND OR n.notice_revision<>a.notice_revision OR n.source_receipt_ref<>a.source_receipt_ref THEN
  RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='notice unavailable'; END IF;
 SELECT * INTO k FROM portal_provider_notice.acknowledgement
 WHERE tenant=a.tenant AND environment=a.environment AND notice_ref=a.notice_ref;
 IF FOUND THEN
  IF k.command_ref<>p_command OR k.request_digest<>p_digest OR k.principal_ref<>a.recipient_principal_ref
  THEN RAISE EXCEPTION USING ERRCODE='P7E02',MESSAGE='acknowledgement conflict'; END IF;
 ELSE
  IF clock_timestamp()>=LEAST(a.valid_until,p_until,s.expires_at,
   (member_data->>'reviewed_until')::timestamptz) THEN
   RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='authority expired'; END IF;
  INSERT INTO portal_provider_notice.audit VALUES(a.tenant,a.environment,p_audit,a.notice_ref,'acknowledge',
   a.recipient_principal_ref,session_data->>'session_ref',p_digest,a.source_receipt_ref,clock_timestamp());
  INSERT INTO portal_provider_notice.acknowledgement VALUES(a.tenant,a.environment,a.notice_ref,p_command,
   p_digest,a.recipient_principal_ref,session_data->>'session_ref',a.membership_revision,a.notice_revision,
   a.content_digest,p_receipt,clock_timestamp()) RETURNING * INTO k;
  INSERT INTO portal_provider_notice.outbox VALUES(a.tenant,a.environment,p_receipt,a.notice_ref,
   a.notice_revision,p_receipt,'notice_acknowledged',clock_timestamp());
 END IF;
 RETURN jsonb_build_object('valid_until',LEAST(a.valid_until,p_until),'result',
  jsonb_build_object('notice_ref',n.notice_ref,'notice_revision',n.notice_revision,
  'delivery_status','delivered','metadata_publication_receipt_ref',n.metadata_receipt_ref,
  'provider_delivery_receipt_ref',k.receipt_ref));
END $$;
CREATE FUNCTION portal_provider_notice.inspect_notice(
 p_tenant text,p_environment text,p_notice text,p_secret_hash text,p_until timestamptz
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a portal_provider_notice.authority%ROWTYPE; n portal_provider_notice.notice%ROWTYPE;
 k portal_provider_notice.acknowledgement%ROWTYPE;
BEGIN
 a=portal_provider_notice.recipient_authority(p_tenant,p_environment,p_notice,p_secret_hash,p_until);
 SELECT * INTO n FROM portal_provider_notice.notice
 WHERE tenant=a.tenant AND environment=a.environment AND notice_ref=a.notice_ref;
 IF NOT FOUND OR n.source_receipt_ref<>a.source_receipt_ref OR n.notice_revision<>a.notice_revision THEN
  RAISE EXCEPTION USING ERRCODE='P7E04',MESSAGE='notice unavailable'; END IF;
 SELECT * INTO k FROM portal_provider_notice.acknowledgement
 WHERE tenant=a.tenant AND environment=a.environment AND notice_ref=a.notice_ref;
 RETURN jsonb_build_object('valid_until',LEAST(a.valid_until,p_until),'result',
 jsonb_build_object('notice_ref',n.notice_ref,'notice_revision',n.notice_revision,
 'body_ref',a.body_ref,'content_digest',a.content_digest,
 'receipt',jsonb_build_object('notice_ref',n.notice_ref,'notice_revision',n.notice_revision,
 'delivery_status',CASE WHEN k.receipt_ref IS NULL THEN 'pending' ELSE 'delivered' END,
 'metadata_publication_receipt_ref',n.metadata_receipt_ref,'provider_delivery_receipt_ref',k.receipt_ref)));
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA portal_provider_notice FROM PUBLIC;
