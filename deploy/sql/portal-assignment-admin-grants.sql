-- Onda 8 (plano portal-autoridade-nativa-dev, D-N): o login da ADMINISTRACAO da fonte de atribuicao
-- (`PostgresStaffAssignmentAdministration`, `src/maezo/portal/admin/assignments.py`), no schema do
-- tenant. Executado PELO DONO do schema (`maezo_app` no dev), nunca pelo portal nem no boot.
-- Idempotente. GUCs da sessao:
--   SET maezo.assignment_admin.schema = 'amh';
--   SET maezo.assignment_admin.login  = 'portal_assignment_admin_amh';
-- Matriz (o que o codigo faz, e nada alem):
--   portal_assignment_source        SELECT, INSERT, UPDATE  (linha do tenant: disabled -> frozen -> active)
--   portal_assignment_publications  SELECT, INSERT, UPDATE  (pedido duravel; UPDATE so delivery_state/
--                                                           native_receipt: o gatilho da 0014 barra o resto)
--   portal_memberships              SELECT + UPDATE (payload)
--       O UPDATE de coluna existe SO porque `prepare_change` le as memberships com `FOR SHARE`
--       (o PostgreSQL exige UPDATE em ao menos uma coluna para travar linha). A ativacao nao
--       passa `membership_changes`: o ops nunca escreve membership (quem escreve e a revisao).
-- Nenhum DELETE/TRUNCATE; nada em portal_sessions/outbox/audit (conferido no fim). O portal (BFF) so
-- LE portal_assignment_source pelo login `source` de portal-human-plane-grants.sql.
DO $assignment_admin$
DECLARE
 tenant_schema text := current_setting('maezo.assignment_admin.schema', true);
 login text := current_setting('maezo.assignment_admin.login', true);
 r record;
BEGIN
 IF tenant_schema IS NULL OR tenant_schema !~ '^[a-z_][a-z0-9_]{0,62}$' THEN
   RAISE EXCEPTION 'portal-assignment-admin-grants: maezo.assignment_admin.schema ausente ou invalido';
 END IF;
 IF login IS NULL OR login !~ '^[a-z_][a-z0-9_]{0,62}$' OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=login) THEN
   RAISE EXCEPTION 'portal-assignment-admin-grants: login ausente, invalido ou inexistente';
 END IF;
 IF (SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolreplication FROM pg_roles WHERE rolname=login) THEN
   RAISE EXCEPTION 'portal-assignment-admin-grants: % tem atributo privilegiado', login;
 END IF;
 IF current_user IS DISTINCT FROM (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=tenant_schema) THEN
   RAISE EXCEPTION 'portal-assignment-admin-grants: execute como dono do schema %', tenant_schema;
 END IF;
 IF login = (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=tenant_schema) THEN
   RAISE EXCEPTION 'portal-assignment-admin-grants: o login da administracao nao pode ser o dono do schema';
 END IF;
 EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', tenant_schema, login);
 EXECUTE format('REVOKE ALL ON %I.portal_assignment_source, %I.portal_assignment_publications, '
                '%I.portal_assignment_receipt_source, %I.portal_memberships FROM %I',
                tenant_schema, tenant_schema, tenant_schema, tenant_schema, login);
 EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %I.portal_assignment_source, %I.portal_assignment_publications TO %I',
                tenant_schema, tenant_schema, login);
 EXECUTE format('GRANT SELECT, UPDATE (payload) ON %I.portal_memberships TO %I', tenant_schema, login);
 FOR r IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname = tenant_schema AND c.relkind IN ('r','p','v','m')
       AND (has_table_privilege(login, c.oid, 'DELETE, TRUNCATE, REFERENCES, TRIGGER')
            OR (c.relname NOT IN ('portal_assignment_source','portal_assignment_publications')
                AND has_table_privilege(login, c.oid, 'INSERT, UPDATE'))
            OR (c.relname NOT IN ('portal_assignment_source','portal_assignment_publications','portal_memberships')
                AND has_table_privilege(login, c.oid, 'SELECT'))) LOOP
   RAISE EXCEPTION 'portal-assignment-admin-grants: privilegio inesperado em %.%', tenant_schema, r.relname;
 END LOOP;
END $assignment_admin$;
