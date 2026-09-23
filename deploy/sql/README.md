# deploy/sql — instalação do schema nativo (T1.4, runbook da Onda 3)

Plano `docs/plans/portal-autoridade-nativa-dev.md` (D-C2, D-H, D-I, D-J) e ADR-0060. Nada aqui
roda no boot nem pelo portal. Os verificadores SCRAM vêm de `tools/staff_materials` (T1.3,
`dba/role-verifiers.json`); nenhum script gera ou recebe senha em claro.

## Ordem

1. **Admin** (`engine-native-roles.sql`), com um `SET maezo.verifier.<login> = '<verificador>'`
   por login. Cria os 4 logins, os schemas `maezo_native` e `maezo_external` (dono
   `maezo_native_schema_owner`) e o USAGE. **No RDS (D-J.2e)**, antes do script:
   `GRANT maezo_native_schema_owner TO <admin> WITH SET TRUE;` (o `CREATE SCHEMA … AUTHORIZATION`
   exige SET no dono; o ADMIN implícito do criador não basta). **No fim da Onda 3:**
   `REVOKE maezo_native_schema_owner FROM <admin>;` e reexecute o script, que recusa qualquer
   membro dos 4 logins além do ADMIN implícito sem INHERIT/SET.
2. **Dono nativo** (`engine-native-install.sql`, gerado por `build_engine_native_install.py`):
   DDL `mzo_*`, ledger do emissor, grants e a postura final.
3. **Dono de `amh`** (`amh-native-source-grants.sql`, via migration do `maezo_app`).
4. **Dono de `maezo_external`**: `external-case-schema-postgres.sql` (já qualificado com
   `maezo_native.*` para `mzo_human_tenant` e `mzo_portal_read_revocation`). **Pendente:** o DDL
   cria papéis `portal_external_*` (`CREATE ROLE`), que o dono não pode (NOCREATEROLE); quem
   executa e como a posse passa ao dono ainda não está decidido.
5. **Depois da instalação AUTH** (`AuthInstallation.installSchema`, dono nativo):
   `engine-native-post-auth-grants.sql`.

## D-J.2a — BLOQUEADO (não implementado)

A decisão pede CREATE em `maezo_native` para `cibseven_app`, para o engine instalar AUTH e
consumer-lineage no boot. O código atual recusa esse layout: `ConsumerEdgeInstallation.session`
(`:68`, `:73`, usado também por `AuthInstallation`) exige sessão do **dono** (`owner_role` ≠
`runtime_role`) e `has_schema_privilege(runtime,schema,'CREATE') = false`, e o pin staff
(`postgres.py`, `runtime_create`) também. Tabelas criadas pelo `cibseven_app` teriam dono = runtime,
contra ADR-0060 D1. Por isso o CREATE não é concedido; volta ao software-architect.
