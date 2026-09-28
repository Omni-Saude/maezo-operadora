-- Onda 8 / H5 (plano portal-autoridade-nativa-dev, D-N). Os dois logins do plano humano do BFF do
-- portal, no schema do tenant (dono: o dono do schema, `maezo_app` no dev). Idempotente.
-- Executado PELO DONO do schema (nunca pelo portal, nunca no boot). GUCs da sessao:
--   SET maezo.human_plane.schema        = 'amh';
--   SET maezo.human_plane.outbox_login  = 'portal_human_outbox_amh';
--   SET maezo.human_plane.source_login  = 'portal_human_source_amh';
-- O `production_materials` exige logins DISTINTOS para outbox e source (`:299`).
--   outbox (pool do relay e da admissao de comando, `outbox.py`):
--     human_command_outbox   SELECT, INSERT
--     human_command_delivery SELECT, INSERT, UPDATE
--     audit_chain, audit_emit_dedup  SELECT, INSERT (28/09/2026, #558: a conclusao do portal - DL-0049,
--       `gateway/human/production.py` - audita pelo MESMO pool do outbox: `PostgresAuditSink.emit_once`
--       le a cauda + a reivindicacao e INSERE o elo e a reivindicacao (`ON CONFLICT DO NOTHING`, sem
--       UPDATE). Sem INSERT a conclusao responde 503 `completion_dependency_unavailable`, medido no dev.)
--       A cadeia e append-only: UPDATE/DELETE/TRUNCATE nela seguem proibidos e conferidos no fim.
--   source (`assignment_transport.py`/`assignment_receipt.py`, so leitura):
--     portal_assignment_source, portal_assignment_receipt_source, human_command_outbox  SELECT
-- Nenhum DELETE/TRUNCATE; INSERT do outbox so nas 4 relacoes acima e UPDATE so em
-- human_command_delivery; nada fora destas 6 relacoes (conferido no fim).
DO $human_plane$
DECLARE
 tenant_schema text := current_setting('maezo.human_plane.schema', true);
 outbox text := current_setting('maezo.human_plane.outbox_login', true);
 source text := current_setting('maezo.human_plane.source_login', true);
 login text;
 r record;
BEGIN
 IF tenant_schema IS NULL OR tenant_schema !~ '^[a-z_][a-z0-9_]{0,62}$' THEN
   RAISE EXCEPTION 'portal-human-plane-grants: maezo.human_plane.schema ausente ou invalido';
 END IF;
 FOREACH login IN ARRAY ARRAY[outbox, source] LOOP
   IF login IS NULL OR login !~ '^[a-z_][a-z0-9_]{0,62}$' OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=login) THEN
     RAISE EXCEPTION 'portal-human-plane-grants: login ausente, invalido ou inexistente';
   END IF;
   IF (SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolreplication FROM pg_roles WHERE rolname=login) THEN
     RAISE EXCEPTION 'portal-human-plane-grants: % tem atributo privilegiado', login;
   END IF;
 END LOOP;
 IF outbox = source THEN
   RAISE EXCEPTION 'portal-human-plane-grants: outbox e source precisam de logins distintos';
 END IF;
 IF current_user IS DISTINCT FROM (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=tenant_schema) THEN
   RAISE EXCEPTION 'portal-human-plane-grants: execute como dono do schema %', tenant_schema;
 END IF;
 EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I', tenant_schema, outbox, source);
 EXECUTE format('REVOKE ALL ON %I.human_command_outbox, %I.human_command_delivery, %I.audit_chain, '
                '%I.audit_emit_dedup, %I.portal_assignment_source, %I.portal_assignment_receipt_source FROM %I, %I',
                tenant_schema, tenant_schema, tenant_schema, tenant_schema, tenant_schema, tenant_schema, outbox, source);
 EXECUTE format('GRANT SELECT, INSERT ON %I.human_command_outbox TO %I', tenant_schema, outbox);
 EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %I.human_command_delivery TO %I', tenant_schema, outbox);
 EXECUTE format('GRANT SELECT, INSERT ON %I.audit_chain, %I.audit_emit_dedup TO %I', tenant_schema, tenant_schema, outbox);
 EXECUTE format('GRANT SELECT ON %I.portal_assignment_source, %I.portal_assignment_receipt_source, '
                '%I.human_command_outbox TO %I', tenant_schema, tenant_schema, tenant_schema, source);
 FOR r IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname = tenant_schema AND c.relkind IN ('r','p','v','m')
       AND (has_table_privilege(outbox, c.oid, 'DELETE, TRUNCATE')
            OR has_table_privilege(source, c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE')) LOOP
   RAISE EXCEPTION 'portal-human-plane-grants: privilegio de escrita inesperado em %.%', tenant_schema, r.relname;
 END LOOP;
 -- Postura do outbox: INSERT so onde a escrita e dele (fila, entrega e os dois elos da auditoria) e
 -- UPDATE so na entrega. A cadeia de auditoria nunca e atualizada: um UPDATE nela reescreveria o hash.
 FOR r IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname = tenant_schema AND c.relkind IN ('r','p','v','m')
       AND ((has_table_privilege(outbox, c.oid, 'INSERT')
             AND c.relname NOT IN ('human_command_outbox', 'human_command_delivery', 'audit_chain', 'audit_emit_dedup'))
            OR (has_table_privilege(outbox, c.oid, 'UPDATE') AND c.relname <> 'human_command_delivery')) LOOP
   RAISE EXCEPTION 'portal-human-plane-grants: privilegio de escrita inesperado do outbox em %.%', tenant_schema, r.relname;
 END LOOP;
 IF NOT (has_table_privilege(outbox, format('%I.audit_chain', tenant_schema), 'SELECT, INSERT')
         AND has_table_privilege(outbox, format('%I.audit_emit_dedup', tenant_schema), 'SELECT, INSERT')) THEN
   RAISE EXCEPTION 'portal-human-plane-grants: outbox sem SELECT+INSERT na cadeia de auditoria';
 END IF;
END $human_plane$;
