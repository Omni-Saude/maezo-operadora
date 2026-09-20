-- ADR-0051 explicit separately versioned owner installation. Caller owns transaction.
-- Never applied by application startup; frozen D/native runtime objects remain unchanged.
CREATE SCHEMA maezo_native_owner_v1 AUTHORIZATION __SCHEMA_OWNER__;
REVOKE ALL ON SCHEMA maezo_native_owner_v1 FROM PUBLIC;
CREATE TABLE maezo_native_owner_v1.installation (
 singleton boolean PRIMARY KEY CHECK(singleton),
 installation_id uuid NOT NULL UNIQUE,
 source_manifest_sha256 text NOT NULL CHECK(source_manifest_sha256 ~ '^[0-9a-f]{64}$'),
 ddl_sha256 text NOT NULL CHECK(ddl_sha256 ~ '^[0-9a-f]{64}$'),
 database_oid oid NOT NULL,
 schema_oid oid NOT NULL,
 schema_owner_oid oid NOT NULL,
 owner_login_oid oid NOT NULL,
 owner_login_name text NOT NULL,
 catalog_bytes bytea NOT NULL CHECK(octet_length(catalog_bytes) BETWEEN 1 AND 1048576),
 catalog_sha256 text NOT NULL CHECK(catalog_sha256 ~ '^[0-9a-f]{64}$'),
 recorded_before_commit_at_ms bigint NOT NULL CHECK(recorded_before_commit_at_ms>=0),
 CHECK(encode(sha256(catalog_bytes),'hex')=catalog_sha256)
);
CREATE TABLE maezo_native_owner_v1.principal_qualification_receipt (
 native_owner_operation_id uuid PRIMARY KEY,
 creator_top_xid xid8 NOT NULL,
 owner_installation_id uuid NOT NULL REFERENCES maezo_native_owner_v1.installation(installation_id),
 native_installation_receipt_sha256 text NOT NULL CHECK(native_installation_receipt_sha256 ~ '^[0-9a-f]{64}$'),
 d_preparation_id uuid NOT NULL,
 request_bytes bytea NOT NULL CHECK(octet_length(request_bytes) BETWEEN 1 AND 1048576),
 request_sha256 text NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 result_bytes bytea NOT NULL CHECK(octet_length(result_bytes) BETWEEN 1 AND 1048576),
 result_sha256 text NOT NULL CHECK(result_sha256 ~ '^[0-9a-f]{64}$'),
 qualification_bytes bytea NOT NULL CHECK(octet_length(qualification_bytes) BETWEEN 1 AND 1048576),
 qualification_sha256 text NOT NULL CHECK(qualification_sha256 ~ '^[0-9a-f]{64}$'),
 recorded_before_commit_at_ms bigint NOT NULL CHECK(recorded_before_commit_at_ms>=0),
 UNIQUE(native_installation_receipt_sha256,d_preparation_id),
 CHECK(encode(sha256(request_bytes),'hex')=request_sha256),
 CHECK(encode(sha256(result_bytes),'hex')=result_sha256),
 CHECK(encode(sha256(qualification_bytes),'hex')=qualification_sha256)
);
ALTER TABLE maezo_native_owner_v1.installation OWNER TO __SCHEMA_OWNER__;
ALTER TABLE maezo_native_owner_v1.principal_qualification_receipt OWNER TO __SCHEMA_OWNER__;
REVOKE ALL ON ALL TABLES IN SCHEMA maezo_native_owner_v1 FROM PUBLIC;
CREATE FUNCTION maezo_native_owner_v1.deny_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $native_owner$
BEGIN RAISE EXCEPTION USING ERRCODE='P7N01',MESSAGE='immutable_native_owner_receipt'; END;
$native_owner$;
ALTER FUNCTION maezo_native_owner_v1.deny_mutation() OWNER TO __SCHEMA_OWNER__;
REVOKE ALL ON FUNCTION maezo_native_owner_v1.deny_mutation() FROM PUBLIC;
CREATE TRIGGER immutable_installation BEFORE UPDATE OR DELETE OR TRUNCATE
 ON maezo_native_owner_v1.installation FOR EACH STATEMENT EXECUTE FUNCTION maezo_native_owner_v1.deny_mutation();
CREATE TRIGGER immutable_receipt BEFORE UPDATE OR DELETE OR TRUNCATE
 ON maezo_native_owner_v1.principal_qualification_receipt FOR EACH STATEMENT EXECUTE FUNCTION maezo_native_owner_v1.deny_mutation();
-- xmin can identify a subtransaction. Only the database stamps the full parent
-- transaction, including inserts beneath SAVEPOINT or PL/pgSQL exception blocks.
CREATE FUNCTION maezo_native_owner_v1.stamp_creator() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $native_owner$
BEGIN NEW.creator_top_xid := pg_catalog.pg_current_xact_id(); RETURN NEW; END;
$native_owner$;
ALTER FUNCTION maezo_native_owner_v1.stamp_creator() OWNER TO __SCHEMA_OWNER__;
REVOKE ALL ON FUNCTION maezo_native_owner_v1.stamp_creator() FROM PUBLIC;
CREATE TRIGGER stamp_receipt_creator BEFORE INSERT
 ON maezo_native_owner_v1.principal_qualification_receipt FOR EACH ROW EXECUTE FUNCTION maezo_native_owner_v1.stamp_creator();
