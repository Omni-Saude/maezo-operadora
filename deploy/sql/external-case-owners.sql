-- D-J.4 2.b. Passo do ADMIN depois que o dono nativo executou external-case-schema-postgres.sql:
-- entrega cada funcao SECURITY DEFINER ao seu papel definer (NOLOGIN, criado por
-- engine-native-roles.sql). Os GRANTs sobre objetos ja foram feitos pelo dono, no DDL.
-- Idempotente (ALTER OWNER para o mesmo dono e no-op).
ALTER FUNCTION maezo_external.lock_ingress(jsonb) OWNER TO portal_external_source_definer;
ALTER FUNCTION maezo_external.reserve_external_case_ref(jsonb,text) OWNER TO portal_external_source_definer;
ALTER FUNCTION maezo_external.accept_external_case_source(jsonb,text,bigint,text,bytea) OWNER TO portal_external_source_definer;
ALTER FUNCTION maezo_external.revoke_external_case_source(jsonb,text,bigint,text,bytea) OWNER TO portal_external_source_definer;
ALTER FUNCTION maezo_external.accept_external_scope_checkpoint(jsonb,text,bigint,text,bytea) OWNER TO portal_external_checkpoint_definer;
ALTER FUNCTION maezo_external.lock_external_ingress_authority(jsonb,text,text,text) OWNER TO portal_external_ingress_reader;
ALTER FUNCTION maezo_external.lock_publisher(jsonb) OWNER TO portal_external_publisher_definer;
ALTER FUNCTION maezo_external.capture_external_case(jsonb,text,text,timestamptz) OWNER TO portal_external_publisher_definer;
ALTER FUNCTION maezo_external.persist_external_case(jsonb,text,text,bigint,bytea) OWNER TO portal_external_publisher_definer;
ALTER FUNCTION maezo_external.transition_external_case(jsonb,text,text,bigint,text,bytea,text) OWNER TO portal_external_publisher_definer;
ALTER FUNCTION maezo_external.capture_external_checkpoint(jsonb,text,text,timestamptz) OWNER TO portal_external_publisher_definer;
ALTER FUNCTION maezo_external.persist_external_checkpoint(jsonb,text,text,bigint,bytea) OWNER TO portal_external_publisher_definer;
ALTER FUNCTION maezo_external.transition_external_checkpoint(jsonb,text,text,bigint,text,bytea,text) OWNER TO portal_external_publisher_definer;
