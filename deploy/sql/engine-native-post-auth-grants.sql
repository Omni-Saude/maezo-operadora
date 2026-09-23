-- T1.4 / D-H.1+3. Executar como maezo_native_schema_owner DEPOIS de AuthInstallation.installSchema,
-- que cria as MZO_AUTH_* em maezo_native e recusa se elas ja existirem (por isso o DDL AUTH nao esta
-- em engine-native-install.sql). Idempotente.
SET search_path TO maezo_native;
DO $post$
BEGIN
 IF current_user <> 'maezo_native_schema_owner' OR to_regclass('maezo_native.mzo_auth_guide_claim') IS NULL THEN
   RAISE EXCEPTION 'engine-native-post-auth-grants: execute como dono, depois da instalacao AUTH';
 END IF;
 REVOKE ALL ON maezo_native.mzo_auth_guide_claim FROM maezo_native_case_issuer;
 GRANT SELECT (tenant_, instance_, case_) ON maezo_native.mzo_auth_guide_claim TO maezo_native_case_issuer;
END $post$;
