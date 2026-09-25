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
| D5 | truststore da 8443 com a CA de clientes do `generate` + a CA do pacote humano | a CA do pacote humano vem do `human-bundle` e emite também o cliente da fixture AUTH; no dev o `native-secret --human-keys` já soma as duas CAs, o harness monta o p12 à parte por causa da fixture |
| D7 | só o `observer-dsn.txt` (login `portal_read_source_amh`) | o resto do segredo nativo sai do `native-secret` |
| D8 | linha `MZO_AUTH_INSTALLATION` inserida pelo dono, com qualificação que não nomeia uma definição deployada | não há instalador AUTH por script nem gerador da qualificação (plano, Onda 3) |
| W1 | `GRANT UPDATE (designation_revision)` em `designation_current` para `cibseven_app` | contorno do F1, só para medir o que vem depois |

Removidos por `fix/c1-python`: **D2** (o `generate` emite a entrada `case-issuer-witness` e a chave
`issuer/issuer-witness-signing-key.pem`), **D3** (`deploy/sql/portal-identity-lock.sql.tmpl` +
`python -m tools.staff_materials lock-sql`), **D9** (`sign-admission` confere o shape fechado da T1.7a)
e **W3** (`JobConfig` carrega sem `model_rebuild`).

`engine.Dockerfile` acrescenta à imagem de `Dockerfile.human` só o link do `sslrootcert` pinado para a
CA local; o JAR do provedor já vem da base (`Dockerfile.human` com `INSTALL_PORTAL_READ=true`, B1).

## Achados (o que falhou, com a causa medida)

| # | Achado | Dono |
|---|---|---|
| F1 | `StaffCaseStore.designation()` usa `FOR UPDATE OF c`, que exige UPDATE em `designation_current`; o pin do mesmo store exige que o engine NÃO tenha UPDATE ali, e a T1.4 concede só SELECT | backend/Java |
| F2 | `staffCurrent` e `AuthRuntime.Invocation` chamam `WorkloadPlugin.running()`, mas `Dockerfile.human` não registra o `WorkloadPlugin` (só o secured D7): o engine com composição staff **nunca sobe** | backend/Java + infra (T1.2) |
| F3 | `JobConfig.authority` é `ForwardRef` e o `_decode` não desce nele: `load_config` recusa todo arquivo; o `__main__` da T1.5 não roda. **Corrigido** (classe declarada antes) | backend/Python (T1.5) |
| F4 | o provedor Q2 resolve `java:jdbc/ProcessEngine` com `new InitialContext()`; no boot funciona, na thread do servlet dá `NameNotFoundException`. Toda publicação e todo `staffLease` por request recusam | backend/Java (T1.7a; o README dela marcava NÃO VERIFICADO) |
| F5 | `assemble` indexa a designação por `role`: com a entrada `case-issuer-witness` (D-H.2) ele confere a errada, a não ser que ela venha antes. **Corrigido** (índice por `entry_ref`, em qualquer ordem) | backend/Python (T1.3) |
| F6 | `approver.sign-admission` valida `scope` com o Scope de 4 campos; a T1.7a exige `{tenant, environment, workload_ref}`. **Corrigido** (espelho do shape fechado de `AdmissionRecord.java`) | backend/Python (T1.3) |
| F7 | faltam geradores: entrada `case-issuer-witness` (D2), cert de cliente do job (D5), pacote humano (D6), segredo nativo da Onda 4 (D7), instalação AUTH (D8), template D-D do lock com USAGE do login (D3), grant do witness do portal (D4). **Feitos:** D2, cert do job, D3, D6 (`human-bundle` da ferramenta), D7 (menos o DSN do observador). **Abertos:** D8, D4 | T1.3/T1.4 |
| F8 | o cliente Q2 Python assina `portal-task-read` e `portal-read-publication` com UMA chave/`key_id`; o trust do engine exige chave, `key_id` **e peer mTLS** únicos por propósito. **Corrigido no Python:** a chave de leitura só assina `portal-task-read`; o job publica com chave, `key_id` e certificado próprios (`JobConfig.publication`, `client_*_file`) | backend (Python × Java) |
| F9 | a membership publicada tem `source_ref = prefixo + principal_ref` (`PostgresMembershipHandshake.freeze`; o witness lê `m.source_` dessa linha), e a designação conferia `source_ref` exato: um só principal casaria. **Corrigido no Python:** `identity_verifier.source_ref` é prefixo terminado em `:`/`/` (spec recusa outro) e `source_matches` confere por prefixo. **Falta o Java:** `StaffCaseInstallation.entry` ainda compara `equals` (linha 84) | backend (Python × Java) |

Pendências abertas pelos builders (#499/#500): **recomeço de revisão com policy nova** e **revogação da
policy anterior**: não mensuráveis (F2). **Chave `human-authority` no trust do engine**: registrada aqui
(D7), mas o job para no catálogo (F4) antes de publicar o principal. **Credencial do emissor no REST**:
a composição não tem nenhuma, e só funciona com o `engine-rest` aberto (dev, N5).

## Correcao do lado Java/imagem (branch `fix/c1-java`)

| # | Correcao | Por que mantem o controle |
|---|---|---|
| F1 | `StaffCaseStore.designation()` sem `FOR UPDATE`: serializa com `pg_advisory_xact_lock_shared` de chave derivada do escopo (tenant, environment, engine, incarnation) | o pin continua exigindo o engine SEM UPDATE em `designation_*` (nada foi concedido); quem trocar a designacao toma o lock EXCLUSIVO da mesma chave (`StaffCaseStore.DESIGNATION_LOCK`). W1 saiu do `run.sh all` |
| F2 | `WorkloadPlugin.requireHumanAuthPeerSeparatedInProcess`: registrar o `WorkloadPlugin` no `Dockerfile.human` nao sobe nunca (o `preInit` exige o layout D7: 1 conector, `BoundaryFilter`, politica montada), entao a checagem E04 passa a valer "sem D7 neste processo, nao ha par D7 para colidir" | fail-closed: plugin rodando e sempre consultado; env de boundary (v1/v2) presente, ou `conf/bpm-platform.xml` nomeando o plugin sem ele rodar, ou descritor ilegivel -> `unavailable` |
| F4 | o provedor Q2 resolve o `DataSource` UMA vez no construtor (boot do engine) e reusa; a thread do request so faz lookup se nada foi resolvido | o nome JNDI e o configurado/admitido; nada do request o escolhe |
| F4b | (medido depois do F4) a publicacao faz `INSERT .. ON CONFLICT DO UPDATE` em `mzo_portal_read_designation/membership/resource`, e o install so dava SELECT,INSERT (+ `revoked_,publication_`): `permission denied` | GRANT UPDATE por COLUNA, exatamente as do `SET` dos upserts; as chaves seguem sem UPDATE (teste `test_engine_native_install_pg.py`) |
| F8 | contrato do engine CONFIRMADO (`PortalReadTrust`): cada entrada de `public_keys` tem `key_id`, chave Ed25519 E `peer_spki_sha256` UNICOS no arquivo inteiro. Logo `portal-task-read` e `portal-read-publication` exigem par de chaves e certificado de cliente mTLS distintos | lado Python: o cliente Q2 precisa de uma chave/`key_id` e um cert de cliente por proposito |

Medido (`run.sh all`, 23/09/2026): `engine-up` PASS com a composicao staff; `publish` PASS (1a rodada
publica catalogo + 2 memberships + 2 principais; 2a rodada idempotente). Proximo bloqueio: `issuer`
rc=1 `staff_case_denied` e `/cases` 403 `operation_forbidden` (provavel F9/Python, nao investigado aqui).

### Rodada apos o merge de `fix/c1-python` + main (23/09/2026)

F9 Java: `StaffCaseInstallation.sourceMatches` casa o `source_ref` do `identity_verifier` por prefixo
terminado em `:`/`/` (estritamente menor; `amh:` nao casa `amhx:`); os demais papeis seguem exatos
(`StaffCaseSourcePrefixTest`). `run.sh all`: tudo PASS ate `publish` (idempotente); `issuer` rc=0 com
`anchored=0, grants=0, reasons={claim_absent:1}`; `/cases` 200 com `items: []` para os DOIS grupos.
Bloqueio do criterio: a escalacao so ancora com a linha `mzo_auth_guide_claim`, que so nasce pelo intake
AUTH (`/v1/auth-start` nativo), e o C1 nao exercita esse intake: falta D8 (instalacao AUTH qualificando a
definicao DEPLOYADA de SP-OP-AUTH-001, hoje `c1-auth-definition-not-qualified` e deployada so no passo
`issuer`, depois do `engine-config`) e um produtor do envelope `human-auth-start.v1` assinado pela chave
de intake (nao ha fixture ponta a ponta no repo).

### D8 e o intake AUTH (24/09/2026)

**D8 feito, com uma ressalva de ordem.** O novo passo `auth-install` roda logo depois do `engine-up` e
antes do `seed`. Ele faz o deploy de SP-OP-AUTH-001, da escalação e da DMN no tenant `amh` e atualiza a
`qualification_` de `MZO_AUTH_INSTALLATION` com o `definition_id`, o `deployment_id` e o SHA-256 dos
bytes deployados, exatamente o que `AuthRuntime.Invocation.definition` confere. Não dá para rodar
antes do `engine-config`: sem engine não há deploy. O `AuthRuntime` lê a instalação a cada invocação,
então a ordem não muda o efeito. Continuam sintéticos, porque nenhum produtor no repo os gera:
`profile_digest`, `source_freeze_contract_digest`, `cutover_ref`, `review_receipt_ref` e
`runtime_qualification_ref`.

**Produtor do `human-auth-start.v1`: não implementado. Está fora do escopo do harness, por dois bloqueios lidos no código:**
1. **Business key.** `HumanAuthStartCommand.java:48` inicia a instância com `"AUTHI-" + guide_identity_ref`.
   O `AuthClaimAnchor` do emissor procura `AUTH-{tenant}-{numero_guia_tiss}`
   (`process_business_keys.auth_business_key`, decisão do dono #16). Uma claim criada pelo intake
   nativo nunca ancora `ESC-amh-sla-auth-{guia}`. O modelo `guide` (`AuthModels.java:19`) não tem
   `numero_guia_tiss`, então o engine não consegue montar a chave contratual. Fechar isso é mudar o
   contrato (DTO `GuideIdentity`/`StartFacts`, publicador WP-J1-02, `native_dispatch.py`
   `AuthIntakeGuideNumberUnavailableError`). É decisão do arquiteto, não do harness.
2. **Entradas publicadas.** O start exige `input_pins` de `actor`, `resource_authority`, `guide`,
   `start_facts` e `document_policy` ativos em `MZO_AUTH_INPUT_HEAD`, além de uma chave de intake
   designada em `MZO_AUTH_TRUST` (`AuthInstallation.designate`). Nenhum gerador ou publicador de
   ponta a ponta no repo faz isso: `dispatch_prepared_start` não tem chamador em `src/`. Fabricar
   essas linhas no harness seria inventar a fonte clínica.

Consequência: o critério do C1 (`/cases` com o caso para o grupo) continua dependendo do item 1.

Medido (`run.sh all` do zero, 24/09/2026):
- **PASS:** todos os passos de `build` a `publish`, inclusive `auth-install` (a qualificação aponta para
  `SP-OP-AUTH-001:1:…` e o sha256 dos bytes deployados). `publish` publica 2 memberships e 2 principais;
  a segunda rodada é idempotente.
- **`issuer`:** `anchored=0` e `reasons={claim_absent:1}`, com o candidato `atendimento-humano` correto.
- **`/cases`:** 200 com `items: []` para os dois grupos.
- **Critério:** não atingido, pelo bloqueio 1 acima.

## Resultado medido (24/09/2026, `feat/t1-11-chave-auth`, `run.sh all` depois de `down`)

17/17 PASS, com `auth-fixture` (6 publicações `SYN-` → 200; `human-auth-start` → 200 `started`, instância
`AUTH-amh-SYN-C1GUIA1`), `issuer` (`anchored: 1`, `grants: 1`) e `portal` (`/cases` → 200 com o caso para o grupo
`atendimento-humano`; 200 `items: []` para o outro grupo). Em 1 de 4 execuções completas o `/cases` do grupo deu
503 `dependency_unavailable` sem erro no engine (NÃO investigado; lado BFF). **D10:** o tenant da fixture continua
`amh` (instalação e schema do harness); o prefixo `SYN-` vai na guia e em todas as referências.

## Resultado medido (24/09/2026, D-M: main com #514, #515, #516 e #511, `run.sh all` depois de `down`)

18/18 PASS. O passo novo `portal-dm` confere a `staff_escalation.v1` (D-M) nos dois lados:

- **Fila:** `guide_number` `***UIA1`, `reason_code` `solicitacao_humano`, `priority` `P3`, `ack_due_at` antes de `resolution_due_at`, `escalation_state` `resolved`.
- **Detalhe:** `/cases/{ref}` 200 com a guia inteira `SYN-C1GUIA1`.
- **Outro grupo:** segue `items: []`.

Antes do #516, o detalhe dava 503 `dependency_unavailable`: o engine não emitia a chave `outcome` que o `StaffDetailShape` exige desde o #392. O C1 não pegava porque só chamava a fila.

## H1 (D-N): tarefa humana pelo Q2 (25/09/2026, `feat/h1-q2-tarefas-humanas`, `run.sh all` do zero)

Passo novo `h1-task` (servico `job`, logo depois do `issuer`): admissao Q2 revisao 2 assinada pela raiz de
TESTE (revisao 1 + catalogo v2 com a entrada `UT_TratarEscalonamento` + publicador `resource` + bloco `human`),
catalogo v2, evidencia (`human-authority` `evidence`) e recurso `SYN-` da tarefa viva, depois `catalog` ->
`discover team` como os dois principais e `task`. Medido: catalogo 200, evidencia 200, recurso 200;
`team[staff-c1-no-grupo]` = `[<tarefa>]`, `team[staff-c1-outro-grupo]` = `[]` (o recurso concede os DOIS: o
que separa e o candidato real); `task` 200 com `eligible_candidate_groups=['atendimento-humano']`; `/cases`
segue 200/`[]` sob a revisao 2. **A imagem gera ids UUID** (`task_id_format=uuid`), nao decimais.

| # | Desvio | Por que |
|---|---|---|
| D11 | `engine-config` soma `resource` aos `publication_kinds` da chave do job | o default da ferramenta segue staff-only ate a H4 decidir o trust de dev; quem admite e a admissao |
| D12 | os envelopes do `h1-task` sao assinados no passo, com as chaves do volume | o `PortalReadClient` pina a admissao revisao 1 no pacote humano; a revisao 2 so existe depois do deploy |
| D13 | a renovacao das memberships no `h1-task` e melhor-esforco (rc=2 medido) | depois do `issuer` o ledger do job fica atras da revisao, e depois da revisao 2 o job recusa o catalogo v1; o passo roda dentro de `observation_seconds` do `publish` |
