-- T1.4 (plano portal-autoridade-nativa-dev; ADR-0060 D1/D2; D-H.2/D-H.3; D-I; T1.7b).
-- Passo 1 de 3. Executar pela role de ADMINISTRACAO do database `maezo` (nunca no boot, nunca por
-- app). Idempotente: reexecutavel; normaliza atributos e recusa o que nao deve corrigir sozinho.
--
-- Entrada: um verificador SCRAM-SHA-256 por login, calculado pela ferramenta T1.3
-- (`tools/staff_materials`, dba/role-verifiers.json), passado como GUC da sessao:
--   SET maezo.verifier.maezo_native_schema_owner = 'SCRAM-SHA-256$...';   (idem para os outros 3)
-- Este arquivo nunca ve senha em claro e nao gera senha.
-- Pre-condicao: o login `cibseven_app` existe.
-- Depois: engine-native-install.sql (dono nativo) e amh-native-source-grants.sql (dono de amh).
DO $roles$
DECLARE
 login name; verifier text;
 logins name[] := ARRAY['maezo_native_schema_owner','maezo_native_case_issuer',
                        'maezo_native_issuer_witness','portal_read_source_amh']::name[];
BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='cibseven_app') THEN
   RAISE EXCEPTION 'engine-native-roles: cibseven_app ausente';
 END IF;
 FOREACH login IN ARRAY logins LOOP
   verifier := current_setting('maezo.verifier.'||login, true);
   IF verifier IS NULL OR verifier !~ '^SCRAM-SHA-256\$[0-9]+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$' THEN
     RAISE EXCEPTION 'engine-native-roles: verificador SCRAM ausente ou invalido para %', login;
   END IF;
   IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=login) THEN
     EXECUTE format('CREATE ROLE %I LOGIN', login);
   END IF;
   -- Normaliza sempre: sem super/createdb/createrole/replication/bypassrls, NOINHERIT.
   EXECUTE format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
                  login, verifier);
   -- Membro de nenhum papel (ADR-0060 D1; T1.7b). Um papel alcancavel por SET ROLE foi concedido
   -- por alguem de proposito: nao se desfaz em silencio, recusa.
   IF EXISTS(SELECT 1 FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.member WHERE r.rolname=login) THEN
     RAISE EXCEPTION 'engine-native-roles: % e membro de um papel', login;
   END IF;
 END LOOP;
END $roles$;

DO $schemas$
DECLARE s name; owner name; g record;
BEGIN
 -- maezo_native (ADR-0060 D1) e maezo_external (D-I, schema proprio do DDL externo, fora do path).
 FOREACH s IN ARRAY ARRAY['maezo_native','maezo_external']::name[] LOOP
   SELECT pg_get_userbyid(nspowner) INTO owner FROM pg_namespace WHERE nspname=s;
   IF owner IS NULL THEN
     EXECUTE format('CREATE SCHEMA %I AUTHORIZATION maezo_native_schema_owner', s);
   ELSIF owner <> 'maezo_native_schema_owner' THEN
     RAISE EXCEPTION 'engine-native-roles: schema % pertence a %', s, owner;
   END IF;
   EXECUTE format('REVOKE ALL ON SCHEMA %I FROM PUBLIC', s);
   -- So o dono cria (sombreamento, ADR-0060 D2): tira CREATE de qualquer outro grantee.
   FOR g IN SELECT DISTINCT a.grantee FROM pg_namespace n,
       aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a
       WHERE n.nspname=s AND a.privilege_type='CREATE' AND a.grantee<>n.nspowner AND a.grantee<>0 LOOP
     EXECUTE format('REVOKE CREATE ON SCHEMA %I FROM %I', s, pg_get_userbyid(g.grantee));
   END LOOP;
 END LOOP;
 GRANT USAGE ON SCHEMA maezo_native TO cibseven_app, maezo_native_case_issuer, maezo_native_issuer_witness;
 GRANT USAGE ON SCHEMA maezo_external TO cibseven_app;
END $schemas$;
