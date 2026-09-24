"""Gera `engine-native-install.sql` (T1.4) a partir dos DDL canonicos, numa ordem fixa.

O arquivo gerado e commitado; `tests/unit/deploy/test_engine_native_install_pg.py` recusa
divergencia entre ele e esta montagem. Uso: `python deploy/sql/build_engine_native_install.py`.

D-J.4: o DDL AUTH e o `human-consumer-lineage` entram aqui, instalados pelo dono, com a MESMA
matriz de grants dos instaladores Java; `AuthInstallation`/`ConsumerEdgeInstallation` aceitam as
tabelas so se o digest do catalogo bater com `native-catalog-pin-*.sha256`. Fora do schema nativo:
`external-case-schema-postgres.sql` (maezo_external, passo proprio) e os programas
native-v2/provisioning, que tem schema e instalador proprios.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESOURCES = ROOT / "src/maezo/portal/engine/java/src/main/resources"
READ_PROVIDER = ROOT / "src/maezo/portal/engine/read-provider/src/main/resources"
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "engine-native-install.sql"

ORDER: tuple[Path, ...] = (
    RESOURCES / "human-schema-postgres.sql",
    RESOURCES / "human-assignment-postgres.sql",
    RESOURCES / "human-decision-qualification-postgres.sql",
    RESOURCES / "portal-read-schema-postgres.sql",
    RESOURCES / "staff-case-schema-postgres.sql",
    RESOURCES / "staff-case-event-postgres.sql",
    RESOURCES / "auth-document-producer-postgres.sql",
    RESOURCES / "human-auth-intake-documents-postgres.sql",
    RESOURCES / "human-consumer-lineage-postgres.sql",
    READ_PROVIDER / "portal-read-admission-postgres.sql",
    HERE / "staff-case-issuer-ledger-postgres.sql",
)

HEADER = """\
-- GERADO por deploy/sql/build_engine_native_install.py. NAO EDITE: edite os DDL canonicos e regere.
-- T1.4 (plano portal-autoridade-nativa-dev; ADR-0060). Passo 2 de 3, depois de engine-native-roles.sql.
-- Executar como maezo_native_schema_owner (login proprio, TLS), nunca no boot. Idempotente:
-- schema vazio -> instala tudo; instalacao completa -> so reaplica grants e confere a postura;
-- qualquer estado parcial -> recusa.
SET search_path TO maezo_native;
SELECT pg_catalog.set_config('maezo.staff_native_role', 'cibseven_app', false);
DO $guard$
BEGIN
 IF current_user <> 'maezo_native_schema_owner' OR current_schema() IS DISTINCT FROM 'maezo_native'
    OR (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='maezo_native')
       IS DISTINCT FROM 'maezo_native_schema_owner' THEN
   RAISE EXCEPTION 'engine-native-install: execute como maezo_native_schema_owner em maezo_native';
 END IF;
END $guard$;
DO $install$
DECLARE relations int;
BEGIN
 SELECT count(*) INTO relations FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='maezo_native' AND c.relkind IN ('r','p','v','m','f');
 IF relations > 0 THEN
   IF to_regclass('maezo_native.mzo_staff_case_issuer_ledger') IS NULL
      OR to_regclass('maezo_native.mzo_portal_read_admission') IS NULL THEN
     RAISE EXCEPTION 'engine-native-install: instalacao parcial em maezo_native (% relacoes)', relations;
   END IF;
   RETURN;
 END IF;
"""

FOOTER = """\
END $install$;

-- Grants (idempotentes; reaplicados a cada execucao).
-- Admissao Q2 (T1.7a): SELECT de tabela para o engine e mais nada. Sem grant por coluna, trigger,
-- regra ou RLS: a postura do provedor recusa qualquer um deles.
REVOKE ALL ON mzo_portal_read_admission FROM PUBLIC;
GRANT SELECT ON mzo_portal_read_admission TO cibseven_app;
-- Ledger do emissor (D-H.3): so o emissor, sem DELETE.
REVOKE ALL ON mzo_staff_case_issuer_ledger FROM PUBLIC, cibseven_app, maezo_native_issuer_witness;
GRANT SELECT, INSERT, UPDATE ON mzo_staff_case_issuer_ledger TO maezo_native_case_issuer;
-- Witness do emissor (D-H.2): SELECT-only nas relacoes que o witness le.
GRANT SELECT ON mzo_portal_read_membership, mzo_human_principal TO maezo_native_issuer_witness;
-- maezo_external (D-I): USAGE vem de engine-native-roles.sql; SELECT em toda tabela do dono.
GRANT SELECT ON ALL TABLES IN SCHEMA maezo_external TO cibseven_app;
ALTER DEFAULT PRIVILEGES FOR ROLE maezo_native_schema_owner IN SCHEMA maezo_external
 GRANT SELECT ON TABLES TO cibseven_app;

-- DML do engine (D-J.2c) nas relacoes que ESTE script instala. MZO_HUMAN_*: SELECT,INSERT,UPDATE;
-- sufixo imutavel (I8) e MZO_PORTAL_READ_*: SELECT,INSERT. Nunca DELETE/TRUNCATE/REFERENCES.
-- Excecao por evidencia: PortalReadPublication.java:171 faz UPDATE em MZO_PORTAL_READ_DESIGNATION
-- (catalog-revoke); sem UPDATE o engine recusaria a revogacao. A admissao Q2 fica SELECT (acima);
-- as MZO_HUMAN_CONSUMER_* e MZO_AUTH_* sao do instalador do engine e nao sao tocadas aqui.
DO $dml$
DECLARE r record; privs text;
BEGIN
 FOR r IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='maezo_native' AND c.relkind='r'
     AND (starts_with(c.relname, 'mzo_human_') OR starts_with(c.relname, 'mzo_portal_read_'))
     AND NOT starts_with(c.relname, 'mzo_human_consumer_') AND c.relname<>'mzo_portal_read_admission' LOOP
   privs := CASE
     WHEN r.relname='mzo_portal_read_designation' THEN 'SELECT, INSERT'
     WHEN r.relname='mzo_human_decision_binding' THEN 'SELECT'
     WHEN starts_with(r.relname, 'mzo_portal_read_')
       OR r.relname ~ '_(event|receipt|continuity|cursor|version|dependency|chunk|ledger)$'
       THEN 'SELECT, INSERT'
     ELSE 'SELECT, INSERT, UPDATE' END;
   EXECUTE format('REVOKE ALL ON maezo_native.%I FROM cibseven_app', r.relname);
   EXECUTE format('GRANT %s ON maezo_native.%I TO cibseven_app', privs, r.relname);
 END LOOP;
 -- D-J.4 2.c: a revogacao (PortalReadPublication.java:171) escreve REVOKED_ e PUBLICATION_; C1 (F4b):
 -- a publicacao (ON CONFLICT DO UPDATE, PortalReadPublication.java:149) reescreve REVISION_, DIGEST_,
 -- PUBLICATION_, SOURCE_, PUBLISHER_, VALID_UNTIL_. A chave (TENANT_..CATALOG_) segue sem UPDATE.
 GRANT UPDATE (revoked_, publication_, revision_, digest_, source_, publisher_, valid_until_)
   ON maezo_native.mzo_portal_read_designation TO cibseven_app;
 -- C1 (F4b): idem para os outros dois upserts da publicacao (PortalReadPublication.java:191/257).
 GRANT UPDATE (revision_, payload_, publication_, source_)
   ON maezo_native.mzo_portal_read_membership TO cibseven_app;
 GRANT UPDATE (payload_, publication_, source_)
   ON maezo_native.mzo_portal_read_resource TO cibseven_app;
END $dml$;

-- D-J.4: matriz EXATA de AuthInstallation.installSchema e ConsumerEdgeInstallation.installSchema
-- (o pin do catalogo inclui os grants; divergir aqui faz o instalador Java recusar).
DO $engine_owned$
DECLARE t text; privs text;
BEGIN
 FOREACH t IN ARRAY ARRAY['installation','trust','revoked_key','input_head','input_version','guide_claim',
                          'instance_head','doc_occurrence','effect_receipt'] LOOP
   privs := 'SELECT'
     || CASE WHEN t IN ('input_head','input_version','guide_claim','instance_head',
                        'doc_occurrence','effect_receipt') THEN ',INSERT' ELSE '' END
     || CASE WHEN t IN ('input_head','instance_head','doc_occurrence') THEN ',UPDATE' ELSE '' END;
   EXECUTE format('REVOKE ALL ON TABLE maezo_native.%I FROM PUBLIC, cibseven_app', 'mzo_auth_'||t);
   EXECUTE format('GRANT %s ON TABLE maezo_native.%I TO cibseven_app', privs, 'mzo_auth_'||t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['database','trust','revoked','qualification','head','pointer',
                          'pointer_head','link'] LOOP
   privs := 'SELECT'
     || CASE WHEN t IN ('pointer','pointer_head','link') THEN ',INSERT' ELSE '' END
     || CASE WHEN t = 'pointer_head' THEN ',UPDATE' ELSE '' END;
   EXECUTE format('REVOKE ALL ON TABLE maezo_native.%I FROM PUBLIC, cibseven_app', 'mzo_human_consumer_'||t);
   EXECUTE format('GRANT %s ON TABLE maezo_native.%I TO cibseven_app', privs, 'mzo_human_consumer_'||t);
 END LOOP;
 -- D-H.1/3: o emissor le so tenant_, instance_, case_ da reivindicacao AUTH (entra no pin AUTH).
 REVOKE ALL ON maezo_native.mzo_auth_guide_claim FROM maezo_native_case_issuer;
 GRANT SELECT (tenant_, instance_, case_) ON maezo_native.mzo_auth_guide_claim TO maezo_native_case_issuer;
END $engine_owned$;

-- Postura final: recusa (e desfaz a transacao, se houver) o que as verificacoes do runtime recusariam.
DO $posture$
DECLARE
 t oid := 'maezo_native.mzo_portal_read_admission'::regclass;
 l oid := 'maezo_native.mzo_staff_case_issuer_ledger'::regclass;
 anything text := 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE';
BEGIN
 -- SoD do schema inteiro: o engine nao alcanca o dono nem por heranca nem por SET ROLE.
 IF pg_has_role('cibseven_app', 'maezo_native_schema_owner', 'USAGE')
    OR pg_has_role('cibseven_app', 'maezo_native_schema_owner', 'SET') THEN
   RAISE EXCEPTION 'engine-native-install: cibseven_app alcanca o dono nativo';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=t AND a.attacl IS NOT NULL)
    OR EXISTS(SELECT 1 FROM pg_trigger g WHERE g.tgrelid=t)
    OR EXISTS(SELECT 1 FROM pg_rewrite w WHERE w.ev_class=t)
    OR (SELECT relrowsecurity OR relforcerowsecurity FROM pg_class WHERE oid=t)
    OR EXISTS(SELECT 1 FROM pg_class c, aclexplode(c.relacl) x WHERE c.oid=t
              AND NOT (x.grantee=c.relowner AND x.grantor=c.relowner)
              AND NOT (x.grantee='cibseven_app'::regrole AND x.grantor=c.relowner
                       AND x.privilege_type='SELECT' AND NOT x.is_grantable)) THEN
   RAISE EXCEPTION 'engine-native-install: postura da tabela de admissao recusada';
 END IF;
 IF has_table_privilege('maezo_native_case_issuer', l, 'DELETE,TRUNCATE')
    OR has_table_privilege('cibseven_app', l, anything)
    OR has_table_privilege('maezo_native_issuer_witness', l, anything) THEN
   RAISE EXCEPTION 'engine-native-install: matriz do ledger recusada';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname='maezo_native' AND lower(c.relname) LIKE 'act\\_%') THEN
   RAISE EXCEPTION 'engine-native-install: relacao act_* em maezo_native';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_namespace n, aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a
           WHERE n.nspname IN ('maezo_native','maezo_external') AND a.privilege_type='CREATE'
             AND a.grantee<>n.nspowner) THEN
   RAISE EXCEPTION 'engine-native-install: CREATE em schema nativo para alguem alem do dono';
 END IF;
END $posture$;
"""


def build() -> str:
    parts = [HEADER]
    for index, path in enumerate(ORDER, start=1):
        body = path.read_text(encoding="utf-8")
        tag = f"$mzo_ddl_{index}$"
        if tag in body or "\r" in body:
            raise SystemExit(f"{path.name}: tag {tag} ou CR no DDL")
        name = path.relative_to(ROOT).as_posix()
        parts.append(f" -- {index}. {name}\n EXECUTE {tag}\n{body.rstrip()}\n{tag};\n")
    parts.append(FOOTER)
    return "".join(parts)


def main() -> int:
    text = build()
    if sys.argv[1:] == ["--check"]:
        return 0 if OUTPUT.read_text(encoding="utf-8") == text else 1
    OUTPUT.write_bytes(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
