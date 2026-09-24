# C1 — checkpoint de integração local (plano `portal-autoridade-nativa-dev`, Onda 1)

Harness re-executável do checkpoint **C1** (§3, linha C1; §3.2): o fluxo inteiro do plano staff em
Docker **local**, sem nada na AWS. PostgreSQL 17 no layout D-C2, engine de `Dockerfile.human`
(`INSTALL_STAFF_COMPOSITION=true`, `INSTALL_PORTAL_READ=true`) com o provedor Q2 (T1.7a/b), materiais da
`tools/staff_materials` (T1.3), job de publicação (T1.5), emissor (T1.6) e o BFF com
`capabilities=identity,staff_cases` chamando o engine pela 8443 mTLS.

**A raiz de instalação aqui é uma raiz de TESTE**, gerada dentro do volume `c1private`
(`/c1/TEST-ROOT-DESCARTAVEL-C1`) só para o C1 e apagada com `run.sh down`. Nunca é a do aprovador
(D-F): o C1 prova o encadeamento das ferramentas, não aprova nada.

## Como rodar

```sh
bash deploy/c1-local/run.sh all     # do zero: down -v, build, todos os passos, uma linha "C1 <passo>: PASS|FAIL" cada
bash deploy/c1-local/run.sh down    # apaga containers, rede e o volume (chaves, senhas, raiz de TESTE)
```

Passos avulsos: `run.sh <passo>` (`build tls db-base bootstrap materials db-native digests engine-config
assemble engine-up [staff|nostaff] [debug] w1 seed publish issuer portal-init portal logs`).
Diagnóstico: `python -m c1.sql <login> "<sql>"` (no serviço `runner`), `python -m c1.trace_http <passo>`
(status e corpo público de cada resposta do engine) e `jdb-catch.sh <passo>` (com `engine-up … debug`:
pilha do primeiro `Rejected` do plugin, que recusa sem logar o motivo). Pré-requisito: Docker Engine 29
(volume `subpath`), portas livres 15499 e 18499 em 127.0.0.1. Nada é copiado para dentro de container:
o checkout entra por bind read-only em `/repo`; os segredos vivem só no volume.

## O que cada passo faz

| Passo | O que roda | Ferramenta do repo usada |
|---|---|---|
| `tls` | CA local do PostgreSQL (SAN `postgres`) e senhas de `postgres`/`cibseven_app`/`maezo_app` | — (no dev esses logins já existem) |
| `db-base` | database `maezo` (dono `maezo_app`), schema `cibseven` (dono `cibseven_app`), `amh` com as tabelas da migração 0012 | texto de `0012_portal_identity_session.py` |
| `bootstrap` | imagem viva (`deploy/cibseven/Dockerfile`) com `currentSchema=cibseven` cria os `ACT_*` (I9) | `deploy/cibseven/Dockerfile` |
| `materials` | `generate` → raiz de TESTE (`approver.root_keygen`) → `approver.sign_designation` | `tools/staff_materials` |
| `db-native` | `engine-native-roles.sql` (verificadores SCRAM de `tools.staff_materials.scram`), `engine-native-install.sql` pelo dono, `amh-native-source-grants.sql` pelo `maezo_app`, `portal-identity-lock.sql.tmpl` renderizado (`lock-sql`); mede os pins | `deploy/sql/*` (T1.4), `tools/staff_materials` |
| `digests` | SHA-256 dos dois JARs carregados, lidos da imagem (a admissão os pina) | — |
| `engine-config` | o segredo `engine/native-materials` da Onda 4 pelo `native-secret`: trusts (Q2 com uma chave por propósito), provedor, chaves de continuidade, composição staff; admissão Q2 revisada e assinada por `approver.sign_admission` com a raiz de TESTE; designação e instalação AUTH no banco | `tools/staff_materials` (`native-secret`, `approver`) |
| `assemble` | `assemble` v2 → `verify --print-manifest-digest` → `verify` com os pins do "aprovador" + negativo de 1 byte | `tools/staff_materials` |
| `engine-up` | 1ª vez: imagem staff como a T1.2 entrega; 2ª: com o contorno W1; 3ª: imagem sem a composição staff | `Dockerfile.human` + provedor |
| `seed` / `publish` | duas memberships staff (grupo da DMN e outro grupo); o `__main__` do job T1.5, duas rodadas | `membership_publication_job` |
| `issuer` | deploy de `spec/processes` (tenant `amh`), a escalação `ESC-amh-sla-auth-C1GUIA1`, uma rodada de `python -m maezo.gateway.staff_cases` | T1.6 |
| `portal-init` / `portal` | `materialize` do pacote; `create_production_app()` + login + `GET /api/v1/portal/cases` para os dois principais | BFF de produção |

## Resultado medido (24/09/2026, Docker Engine 29.8.0)

| Passo | Resultado |
|---|---|
| build / tls / db-base / bootstrap | PASS; 49 `ACT_*` em `cibseven` pela imagem viva |
| materials | PASS: `generate` (23 arquivos), raiz de TESTE, designação de 6 entradas assinada |
| db-native | PASS: roles.sql + install.sql (idempotente na reexecução) + amh-grants; 56 relações em `maezo_native`; pins medidos |
| engine-config | PASS, com D9: `approver.sign-admission` recusa o registro T1.7a (F6) |
| assemble | PASS: v2 aceito pelo `verify` com os pins do aprovador; 1 byte trocado → rc=1 |
| engine-up (staff, como entregue) | **FAIL F1**: `human engine store unavailable` em `StaffCaseStore.designation()` (`permission denied`) |
| engine-up (staff + W1) | **FAIL F2**: `engine_profile_unavailable` em `WorkloadPlugin.running()` (`HumanCommandPlugin.java:175`). Antes disso passaram: composição (digest recalculado = o logado), admissão Q2 (linha pública `portal_read_provider`), trusts, pins das 14 relações, lock AUTH, designação e prova da raiz |
| engine-up (sem composição staff) | PASS |
| seed | PASS |
| publish (T1.5) | **FAIL F3** (config não carrega; W3) e **F4**: `POST /maezo-human-read/v1/publications` → 503 `READ_DEPENDENCY_UNAVAILABLE`; o provedor não acha `java:jdbc/ProcessEngine` na thread do request (`NameNotFoundException`, `InstalledReadProviders.java:191`) |
| issuer (T1.6) | deploy + DMN ok: `UT_TratarEscalonamento` com candidato `atendimento-humano`; rodada rc=1 `staff_case_uncertain` (a publicação nativa cai no engine sem staff) |
| portal-init / portal | PASS materialize; lifespan de produção sobe (material + 2 logins qualificados pelo TLS); `/cases` → **503** `dependency_unavailable` para os dois principais |

**Critério do C1 (200 no grupo, negado fora dele): NÃO atingido.** Bloqueio principal: F2, depois F4.

**Depois de `fix/c1-python` (F3, F5, F6, F7 em parte, F8, F9 do lado Python):** a tabela acima é a medida
de 24/09 e **não foi remedida** com as correções (o stack `maezo-c1` estava em uso por outra frente).
As correções têm testes unitários; o próximo `run.sh all` é que diz o que sobra depois de F1/F2/F4 (Java).

## Desvios do harness (cada um é uma lacuna do repo, não uma escolha)

| # | O quê | Por quê |
|---|---|---|
| D1 | `session-lock-ca.pem`/`native-witness-ca.pem` viram a CA local | o `generate` grava o bundle RDS pinado; mesma troca da prova local da T1.2 |
| D4 | `GRANT SELECT` do witness do PORTAL em `mzo_portal_read_membership`/`mzo_human_principal` | o install só concede ao witness do emissor |
| D5 | truststore da 8443 com a CA de clientes do `generate` + a CA do pacote humano | só por causa do D6: o job em si já usa o certificado de cliente do `generate` (`job/`) |
| D6 | pacote `portal-human-material.v1` do job montado pelo harness (`c1/human_bundle.py`) | nenhum gerador; o formato é o de `tests/support/materials_builder.py` |
| D7 | só o `observer-dsn.txt` (login `portal_read_source_amh`) | o resto do segredo nativo sai do `native-secret` |
| D8 | linha `MZO_AUTH_INSTALLATION` inserida pelo dono, com qualificação que não nomeia uma definição deployada | não há instalador AUTH por script nem gerador da qualificação (plano, Onda 3) |
| W1 | `GRANT UPDATE (designation_revision)` em `designation_current` para `cibseven_app` | contorno do F1, só para medir o que vem depois |

Removidos por `fix/c1-python`: **D2** (o `generate` emite a entrada `case-issuer-witness` e a chave
`issuer/issuer-witness-signing-key.pem`), **D3** (`deploy/sql/portal-identity-lock.sql.tmpl` +
`python -m tools.staff_materials lock-sql`), **D9** (`sign-admission` confere o shape fechado da T1.7a)
e **W3** (`JobConfig` carrega sem `model_rebuild`).

`engine.Dockerfile` acrescenta à imagem de `Dockerfile.human` só o JAR do provedor (o `COPY` que a T1.7a
deixou para depois da T1.2) e o link do `sslrootcert` pinado para a CA local.

## Achados (o que falhou, com a causa medida)

| # | Achado | Dono |
|---|---|---|
| F1 | `StaffCaseStore.designation()` usa `FOR UPDATE OF c`, que exige UPDATE em `designation_current`; o pin do mesmo store exige que o engine NÃO tenha UPDATE ali, e a T1.4 concede só SELECT | backend/Java |
| F2 | `staffCurrent` e `AuthRuntime.Invocation` chamam `WorkloadPlugin.running()`, mas `Dockerfile.human` não registra o `WorkloadPlugin` (só o secured D7): o engine com composição staff **nunca sobe** | backend/Java + infra (T1.2) |
| F3 | `JobConfig.authority` é `ForwardRef` e o `_decode` não desce nele: `load_config` recusa todo arquivo; o `__main__` da T1.5 não roda. **Corrigido** (classe declarada antes) | backend/Python (T1.5) |
| F4 | o provedor Q2 resolve `java:jdbc/ProcessEngine` com `new InitialContext()`; no boot funciona, na thread do servlet dá `NameNotFoundException`. Toda publicação e todo `staffLease` por request recusam | backend/Java (T1.7a; o README dela marcava NÃO VERIFICADO) |
| F5 | `assemble` indexa a designação por `role`: com a entrada `case-issuer-witness` (D-H.2) ele confere a errada, a não ser que ela venha antes. **Corrigido** (índice por `entry_ref`, em qualquer ordem) | backend/Python (T1.3) |
| F6 | `approver.sign-admission` valida `scope` com o Scope de 4 campos; a T1.7a exige `{tenant, environment, workload_ref}`. **Corrigido** (espelho do shape fechado de `AdmissionRecord.java`) | backend/Python (T1.3) |
| F7 | faltam geradores: entrada `case-issuer-witness` (D2), cert de cliente do job (D5), pacote humano (D6), segredo nativo da Onda 4 (D7), instalação AUTH (D8), template D-D do lock com USAGE do login (D3), grant do witness do portal (D4). **Feitos:** D2, cert do job, D3, D7 (menos o DSN do observador). **Abertos:** D6, D8, D4 | T1.3/T1.4 |
| F8 | o cliente Q2 Python assina `portal-task-read` e `portal-read-publication` com UMA chave/`key_id`; o trust do engine exige chave, `key_id` **e peer mTLS** únicos por propósito. **Corrigido no Python:** a chave de leitura só assina `portal-task-read`; o job publica com chave, `key_id` e certificado próprios (`JobConfig.publication`, `client_*_file`) | backend (Python × Java) |
| F9 | a membership publicada tem `source_ref = prefixo + principal_ref` (`PostgresMembershipHandshake.freeze`; o witness lê `m.source_` dessa linha), e a designação conferia `source_ref` exato: um só principal casaria. **Corrigido no Python:** `identity_verifier.source_ref` é prefixo terminado em `:`/`/` (spec recusa outro) e `source_matches` confere por prefixo. **Falta o Java:** `StaffCaseInstallation.entry` ainda compara `equals` (linha 84) | backend (Python × Java) |

Pendências abertas pelos builders (#499/#500): **recomeço de revisão com policy nova** e **revogação da
policy anterior**: não mensuráveis (F2). **Chave `human-authority` no trust do engine**: registrada aqui
(D7), mas o job para no catálogo (F4) antes de publicar o principal. **Credencial do emissor no REST**:
a composição não tem nenhuma, e só funciona com o `engine-rest` aberto (dev, N5).
