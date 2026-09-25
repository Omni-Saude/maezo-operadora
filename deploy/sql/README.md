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
   DDL `mzo_*` (inclusive AUTH e `human-consumer-lineage`, D-J.4), ledger do emissor, grants e a
   postura final. `AuthInstallation`/`ConsumerEdgeInstallation` aceitam essas tabelas só se o digest
   do catálogo (`native-catalog-digest-postgres.sql`) bater com `native-catalog-pin-*.sha256`;
   qualquer divergência é recusa. `cibseven_app` nunca recebe CREATE. Mudou um desses DDL ou a
   matriz de grants: regere o install e atualize o pin (o teste PG 17 mostra o digest novo).
3. **Dono de `amh`** (`amh-native-source-grants.sql`, via migration do `maezo_app`).
4. **Dono nativo**: `external-case-schema-postgres.sql` em `maezo_external` (sem `CREATE ROLE`:
   os papéis `portal_external_*` são NOLOGIN e vêm do passo 1). Depois, **admin**:
   `external-case-owners.sql`, que entrega as funções SECURITY DEFINER aos papéis definer
   (`ALTER ... OWNER TO` exige poder assumir o papel de destino; o dono nativo não é membro de
   nada, por isso esse passo é do admin).

## Em dev (Aurora): a task `staff-install`

A ordem acima roda inteira, idempotente, por `python -m tools.staff_install` na imagem
`deploy/ops/staff-install.Dockerfile` (task `staff-install`, `task-staff-install.tf`). As senhas
vêm do Secrets Manager por `GetSecretValue` (as 4 nativas de
`python -m tools.staff_materials login-secrets`, as 2 do portal das DSNs do `generate`); os
verificadores SCRAM são calculados dentro da task. O admin do Aurora não é superusuário, e o
PostgreSQL 16+ cobra dele o que o `postgres` do harness pula; o instalador resolve assim
(detalhes no docstring de `tools/staff_install/installer.py`):

- os atributos SUPERUSER/REPLICATION/BYPASSRLS só um superusuário escreve, mesmo `NO*`. Os dois
  scripts de logins **recusam** um login que os tenha, em vez de normalizá-los;
- o GRANT do dono ao admin (D-J.2e) entra entre os blocos `$roles$`/`$external$` e `$schemas$` de
  `engine-native-roles.sql`: antes dele, o próprio script recusaria o membro;
- os passos com `ALTER ... OWNER TO` (4 e o lock) ganham, só dentro da transação, SET no papel de
  destino e CREATE no schema para ele;
- no fim, depois do `REVOKE`, só `$roles$`/`$external$` são reexecutados (`$schemas$` exige ser
  dono do schema).
