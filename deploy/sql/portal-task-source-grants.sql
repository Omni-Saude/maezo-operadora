-- H2/D13 (plano portal-autoridade-nativa-dev, D-N). Leitura do job T1.5 sobre as linhas do engine.
-- Idempotente. O login e passado como GUC da sessao e precisa existir (quem cria e a instalacao):
--   SET maezo.task_source.login = 'portal_task_source_amh';
-- Duas partes, cada uma executada pelo DONO do schema dela (nunca por app, nunca no boot):
--   SET maezo.task_source.part = 'native';  -- pelo dono de maezo_native (maezo_native_schema_owner)
--   SET maezo.task_source.part = 'engine';  -- pelo dono do schema do engine (cibseven_app)
-- O que o login le, coluna a coluna (nada mais; SELECT de coluna, nenhuma escrita):
--   maezo_native.mzo_human_tenant   (tenant_, rev_)  o contador CAS do tenant, que o job segue (D13)
--   maezo_native.mzo_human_evidence (tenant_, task_, rev_, ref_, digest_, valid_until_, process_)
--   <engine>.act_ru_task            (id_, rev_, proc_def_id_, task_def_key_, tenant_id_, suspension_state_)
-- Nao concede nada em mzo_portal_read_admission: o provedor Q2 pina aquela ACL fechada
-- (`InstalledReadProviders.PIN_SQL`, uma so entrada SELECT, a do engine).
DO $task_source$
DECLARE
 login text := current_setting('maezo.task_source.login', true);
 part text := current_setting('maezo.task_source.part', true);
 engine_schema text := coalesce(current_setting('maezo.task_source.engine_schema', true), 'cibseven');
 r record;
BEGIN
 IF login IS NULL OR login !~ '^[a-z_][a-z0-9_]{0,62}$' OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=login) THEN
   RAISE EXCEPTION 'portal-task-source-grants: maezo.task_source.login ausente, invalido ou inexistente';
 END IF;
 IF (SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolreplication FROM pg_roles WHERE rolname=login) THEN
   RAISE EXCEPTION 'portal-task-source-grants: % tem atributo privilegiado', login;
 END IF;
 IF engine_schema !~ '^[a-z_][a-z0-9_]{0,62}$' THEN
   RAISE EXCEPTION 'portal-task-source-grants: maezo.task_source.engine_schema invalido';
 END IF;
 IF part = 'native' THEN
   IF current_user IS DISTINCT FROM (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='maezo_native') THEN
     RAISE EXCEPTION 'portal-task-source-grants: parte native e do dono de maezo_native';
   END IF;
   EXECUTE format('GRANT USAGE ON SCHEMA maezo_native TO %I', login);
   EXECUTE format('REVOKE ALL ON maezo_native.mzo_human_tenant, maezo_native.mzo_human_evidence FROM %I', login);
   EXECUTE format('GRANT SELECT (tenant_, rev_) ON maezo_native.mzo_human_tenant TO %I', login);
   EXECUTE format('GRANT SELECT (tenant_, task_, rev_, ref_, digest_, valid_until_, process_) '
                  'ON maezo_native.mzo_human_evidence TO %I', login);
 ELSIF part = 'engine' THEN
   IF current_user IS DISTINCT FROM (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=engine_schema) THEN
     RAISE EXCEPTION 'portal-task-source-grants: parte engine e do dono do schema %', engine_schema;
   END IF;
   EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', engine_schema, login);
   EXECUTE format('REVOKE ALL ON %I.act_ru_task FROM %I', engine_schema, login);
   EXECUTE format('GRANT SELECT (id_, rev_, proc_def_id_, task_def_key_, tenant_id_, suspension_state_) '
                  'ON %I.act_ru_task TO %I', engine_schema, login);
 ELSE
   RAISE EXCEPTION 'portal-task-source-grants: maezo.task_source.part precisa ser native ou engine';
 END IF;
 -- Nenhuma escrita em lugar nenhum dos dois schemas (defesa contra um GRANT antigo esquecido).
 FOR r IN SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname IN ('maezo_native', engine_schema) AND c.relkind IN ('r','p','v','m')
       AND has_table_privilege(login, c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE') LOOP
   RAISE EXCEPTION 'portal-task-source-grants: % escreve em %.%', login, r.nspname, r.relname;
 END LOOP;
END $task_source$;
