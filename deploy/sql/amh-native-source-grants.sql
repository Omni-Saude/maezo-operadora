-- T1.4 / T1.7b / D-H.3. Executar pelo DONO de amh.portal_memberships (maezo_app, via migration),
-- nunca pelo dono nativo. Idempotente. Pre-condicao: engine-native-roles.sql aplicado.
-- portal_read_source_amh e maezo_native_case_issuer leem SO tenant, issuer, subject, payload.
DO $amh$
BEGIN
 IF current_user IS DISTINCT FROM (SELECT pg_get_userbyid(c.relowner) FROM pg_class c
     JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='amh' AND c.relname='portal_memberships') THEN
   RAISE EXCEPTION 'amh-native-source-grants: execute como dono de amh.portal_memberships';
 END IF;
 GRANT USAGE ON SCHEMA amh TO portal_read_source_amh, maezo_native_case_issuer;
 REVOKE ALL ON amh.portal_memberships FROM portal_read_source_amh, maezo_native_case_issuer;
 GRANT SELECT (tenant, issuer, subject, payload) ON amh.portal_memberships
   TO portal_read_source_amh, maezo_native_case_issuer;
END $amh$;
