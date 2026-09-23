"""Gera `engine-native-install.sql` (T1.4) a partir dos DDL canonicos, numa ordem fixa.

O arquivo gerado e commitado; `tests/unit/deploy/test_engine_native_install_pg.py` recusa
divergencia entre ele e esta montagem. Uso: `python deploy/sql/build_engine_native_install.py`.

Fora de proposito (instalados em runtime pelo proprio engine, que recusa se ja existirem):
`human-auth-intake-documents-postgres.sql` (AuthInstallation.installSchema) e
`human-consumer-lineage-postgres.sql` (ConsumerEdgeInstallation). Fora do schema nativo:
`external-case-schema-postgres.sql` (maezo_external, D-I; ainda qualifica `public.*`) e os
programas native-v2/provisioning, que tem schema e instalador proprios.
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
