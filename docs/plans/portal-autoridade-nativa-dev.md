# Plano — autoridade nativa do portal humano em dev (fila de casos)

**Status:** PLANO (leitura + desenho). Nada foi aplicado na AWS, nenhum segredo foi criado,
nenhum código de produção foi escrito. **Data:** 22/09/2026 (medições do repo nesta data, `main`
em `7ca37fa6`; medição da conta pela thread principal em 23/09 UTC).
**Autor:** software-architect (agente), a pedido do dono do repositório (Leonardo), que autorizou
executar o programa. **Este documento não aprova pin nenhum**: ele separa quem GERA de quem
APROVA (§4) e o aprovador é uma pessoa nomeada pelo dono (§6).

Regra de leitura: toda afirmação de fato traz `arquivo:linha` ou o comando que a mede. O que
depende de execução e não foi executado está marcado **NÃO VERIFICADO**.

---

## 0. Resumo em uma tela

1. **O servidor nativo existe como código, não como serviço.** O endpoint mTLS que o portal
   chama (`POST {native_origin}/maezo-human/v1/staff-case-*`) é servido pelo **plugin Java do
   CIB Seven** (`HumanServlet` + `HumanCommandPlugin`), exatamente onde o ADR-0049 D5 mandou.
   Refuta a hipótese "ninguém serve o endpoint". **Mas** esse plugin não está na imagem do
   engine que roda em dev, não existe listener mTLS em nenhum deploy, e a configuração staff do
   plugin **não tem quem a injete fora dos testes** — só um setter programático sem chamador.
2. **Preencher `portal.staff` não abre a fila.** `GET /tasks?queue=team` só liga no perfil
   `identity,staff_cases,human`, que exige um **segundo pacote de materiais (plano humano)** que
   o Terraform não suporta. `identity,staff_cases` (o que `portal.staff` produz) liga apenas
   `GET /cases`. A fila de tarefas é a Onda 8, com escopo e estimativa próprios.
3. **Três furos estruturais antes de qualquer pin:** (a) a instalação do `lock_external_session`
   aponta para `public.portal_sessions`, mas em dev as tabelas estão no schema `amh`; (b) o leitor
   witness e o plugin só aceitam as relações `mzo_*` em `public`, e o engine de dev roda com
   `currentSchema=cibseven`; (c) não existe fonte de publicação de casos staff (`case_issuer`) nem
   job que publique memberships e principals no engine: mesmo com tudo ligado, `/cases` ficaria
   sem casos.
4. O plano tem 9 ondas (0 a 8). As Ondas 0 a 7 entregam `/cases` com um caso real em 3 a 5
   semanas: o flip de capabilities acontece na Onda 6 e a aceitação na Onda 7. A fila de tarefas
   (Onda 8) leva mais 3 a 6 semanas, estimativa de baixa confiança.

---

## 1. Inventário medido

### 1.1 Por que o 503 acontece hoje (e onde exatamente)

| Fato | Prova |
|---|---|
| `staff = null` no tfvars de dev | `deploy/aws-ecs/envs/dev-sa-east-1/portal.auto.tfvars:176-179` |
| Terraform só emite dois perfis: `identity` ou `identity,staff_cases`; **nunca** `...,human` | `deploy/aws-ecs/envs/dev-sa-east-1/service-portal.tf:203` e `:250` (init) |
| Com `identity`, `create_app` não recebe `task_read_service_factory` → a rota recusa **antes** do gateway | `src/maezo/portal/api/tasks.py:103-106` (`factory is None → read_dependency_unavailable`) |
| A guarda citada (`gateway.py` ~540) é a **segunda** barreira, só alcançada com factory ligada | `src/maezo/gateway/human/gateway.py:540-545` |
| A factory de leitura de tarefas só é ligada em `_human_slots`, que só roda com `capabilities == "identity,staff_cases,human"` | `src/maezo/portal/api/production.py:22`, `:49-52`, `:113` |
| `identity,staff_cases` liga **só** `staff_case_service_factory` → rotas `/cases` | `src/maezo/portal/api/production.py:44-47`; consumo em `src/maezo/portal/api/cases.py:110` e rotas `:148` (`/cases`) e `:182` (`/cases/{case_ref}`) |
| O perfil `human` exige pacote próprio: diretório `/run/maezo-human-materials/current`, versão e SHA do manifesto (`portal-human-material.v1`, 14 arquivos) | `src/maezo/gateway/staff_cases/production_config.py:76-85`, `:141-149`; `src/maezo/gateway/human/production_materials.py:44-73` |
| Nenhum arquivo de `deploy/` menciona o plano humano | `grep -rln "HUMAN_MATERIAL\|human_material\|staff_cases,human\|maezo-human-materials" deploy/` → vazio (só em `tests/`) |

**Correção ao enunciado:** a fila **não** liga em `identity,staff_cases`. Ela liga só em
`identity,staff_cases,human`, e esse perfil pede os dois pacotes (staff e human) completos,
com versões e digests distintos (`production_config.py:145-149`).

### 1.2 Cliente (lado portal): existe e está completo

| Peça | Onde |
|---|---|
| Settings e validação "completo ou ausente" dos 12 pins staff | `src/maezo/gateway/staff_cases/production_config.py:67-237` |
| Manifesto fechado `portal-staff-material.v1`, 12 arquivos (7 públicos com SHA, 5 privados com `null`) | `production_config.py:20-40`, `:305-347` |
| Bundle `portal-staff-secret-bundle.v1`, limite 65.536 bytes | `production_config.py:19`, `:350-360` |
| **Validador offline reutilizável:** `decode_bundle(raw, settings)` → `verify_materials` (pins, SHAs, raiz Ed25519, designação assinada, chaves por papel, cert TLS do cliente) | `src/maezo/gateway/staff_cases/materials.py:64-99`, `:102-183`, `:186-197` |
| Init que materializa o segredo em `/run/maezo-staff-materials/current` | `src/maezo/gateway/staff_cases/materialize.py` (64 linhas); task def `service-portal.tf:241` |
| Runtime: qualifica os dois logins (sem super/bypassrls/createrole…), confere o `function_pin` pelo catálogo | `src/maezo/gateway/staff_cases/production.py:63-140` |
| Exige que o DSN do session-lock tenha **o mesmo host/porta/database** do DSN de identidade e login diferente | `production.py:174-182` |
| Cliente mTLS: `POST origin + "/maezo-human/v1/" + route`, confere o SPKI do servidor contra o pin | `src/maezo/gateway/staff_cases/publisher.py:227`, `:233-240` |
| Witness: lê `public.mzo_portal_read_membership` ⋈ `public.mzo_human_principal`, confere OID/owner, **`nspname='public'` fixo** | `src/maezo/gateway/staff_cases/postgres.py:241`, `:266-267` |
| Terraform do perfil staff (init, volumes, egress nativo, validação de formato) | `service-portal.tf:203-260`; `portal-variables.tf:82-117` |
| Imagem derivada com os dois diretórios 0700 | `deploy/portal.Dockerfile:1-16` |
| Fixture sintética que monta um bundle válido: serve de referência para o gerador | `tests/unit/gateway/test_staff_production_materials.py:34-175` |

### 1.3 Servidor (lado engine): o código existe, o deploy não

**O servidor existe como código.** ADR-0049 D5 decidiu "extensão Java na imagem pinada CIB Seven
2.1.0 … empacotado pela imagem existente em `deploy/cibseven/`"
(`docs/adr/0049-portal-humano-e-comandos-atomicos.md:242-246`). Esse código está em
`src/maezo/portal/engine/java/`:

| Peça | Onde |
|---|---|
| Servlet mTLS que atende `/v1/staff-case-detail`, `-list`, `-finalize` e `-publication` | `src/maezo/portal/engine/java/src/main/java/br/com/maezo/human/HumanServlet.java:39-46` |
| Exige `request.isSecure()` e uma cadeia X.509 do cliente, sem cabeçalho de identidade repassado | `HumanServlet.java:9`, `:92-95` |
| Plugin: `executeStaff` → `StaffCaseReadCommand` / `StaffCasePublicationCommand` na TX do engine | `HumanCommandPlugin.java:203-228` |
| Exige `MAEZO_HUMAN_TRUST_FILE`, senão o engine não sobe | `HumanCommandPlugin.java:70-73` |
| Staff só responde se houver `staffConfiguration`, `authRuntime` (plano AUTH), `authorizationEnabled` e `tenantCheckEnabled` | `HumanCommandPlugin.java:60-65` |
| Lease Q2 exige `PortalReadPlugin` instalado com trust (`MAEZO_PORTAL_READ_TRUST_FILE`) | `PortalReadPlugin.java:20`, `:76-88` |
| DDL das relações nativas (`MZO_PORTAL_READ_MEMBERSHIP`, `MZO_HUMAN_PRINCIPAL`, `mzo_staff_case_*`) | `src/main/resources/portal-read-schema-postgres.sql:21`, `human-schema-postgres.sql:7`, `staff-case-schema-postgres.sql`, `staff-case-event-postgres.sql` |
| DDL de `portal_identity.lock_external_session(text)`, **num bloco comentado** para instalação manual no banco de identidade | `src/main/resources/external-case-schema-postgres.sql:636-672` |
| Imagem opt-in com o plugin (sem mexer no `engine-rest`) | `deploy/cibseven/Dockerfile.human:1-33` |
| Imagem "secured" (autentica o `engine-rest` por certificado, sem fallback anônimo) | `deploy/cibseven/Dockerfile.secured-v2`; `deploy/cibseven/secured/README.md:10-15` |
| Listener mTLS (`8443`, `certificateVerification=required`): **só num fixture local descartável** | `deploy/cibseven/package-test/prepare.py:2`, `:220-256` |

**O que falta no servidor, com prova:**

| Falta | Prova |
|---|---|
| O plugin não está no engine de dev. O serviço roda a imagem `deploy/cibseven/Dockerfile` (sem showcase, sem JAR Maezo) | `service-cibseven.tf:70-78`; `deploy/cibseven/Dockerfile`: nenhum `COPY` de JAR (`grep -n "^COPY" deploy/cibseven/Dockerfile` → só `configure-group-whitelist.sh`) |
| Nenhum pipeline constrói `Dockerfile.human` ou `.secured*` | `grep -rn "Dockerfile.human\|Dockerfile.secured" .github/workflows deploy/aws-ecs scripts` → só um comentário em `security.yml:888` |
| O engine de dev expõe só HTTP 8080, sem TLS | `service-cibseven.tf:81` |
| **Ninguém injeta a configuração staff nem a AUTH em produção.** `setStaffCaseConfiguration` e `setHumanAuthConfiguration` recebem objetos Java e não têm chamador (nem em `src/main`, nem em `deploy/`, nem nos testes) | `HumanCommandPlugin.java:29`, `:43`; `grep -rn "setStaffCaseConfiguration\|setHumanAuthConfiguration" src deploy` → só as declarações |
| O caminho staff completo (plugin configurado + HTTP mTLS + portal) nunca foi exercitado junto | `StaffCaseReadEngineIT.java:7-9`: "This is not full staff installation/API acceptance." |
| Nenhum gerador de materiais (CAs, chaves, designação, bundle) | `grep -rln "installation-proof\|portal-staff-secret-bundle" scripts tools deploy src` → só o loader, o `portal.md` e o teste |
| **Nenhuma fonte de publicação de casos staff** (`case_issuer`: `staff_case_grant`, `staff_policy_head`, `scope_complete`). `StaffNativeClient.publish` não tem chamador de produção | `src/maezo/gateway/staff_cases/models.py:62`; `grep -rn "\.publish(" src/maezo` → nenhum em `staff_cases` |
| **Nenhum job publica memberships/principals no engine** (`MZO_PORTAL_READ_MEMBERSHIP` vem de `PortalReadPublication`, `MZO_HUMAN_PRINCIPAL` vem de `/v1/authority`). Existe `PostgresMembershipPublicationSource`, mas nenhum `__main__` o executa | `read_publisher.py:1-4`, `:87`; `PortalReadPublication.java:192`; `AuthorityCommand.java:80`; `find src/maezo/gateway -name __main__.py` → só `gateway/__main__.py` (outro serviço) |

**Veredito:** o servidor nativo **existe como código** e mora no lugar certo, o plugin do CIB
Seven em `src/maezo/portal/engine/java`, empacotado por `deploy/cibseven/Dockerfile.human`.
**Não existe como serviço** em nenhum ambiente. Para virar serviço ele precisa de código novo:
(i) a composição que carrega a configuração staff/AUTH dos arquivos montados; (ii) um
`server.xml` de deploy com connector mTLS; (iii) a fonte de publicação de casos; (iv) o job
de membership/principal. Não há um "servidor separado" a escrever, e ele não deve ser escrito
fora do engine: a autoridade precisa ler e travar a tarefa **na mesma transação do engine**
(ADR-0049 D5, `:272-278`).

### 1.4 Incompatibilidades com o dev real (achadas lendo, a confirmar executando)

| # | Incompatibilidade | Prova (leitura) | Status |
|---|---|---|---|
| I1 | A função de lock referencia `public.portal_sessions` e `public.portal_memberships`, mas em dev as tabelas estão no schema `amh`: a migration cria sem qualificar, com `search_path` `{tenant}, public`, e a role `portal_bff_amh` usa `search_path=amh` | `external-case-schema-postgres.sql:658`, `:663`, `:669`; `src/maezo/platform/migrations/env.py:57-62`; `0012_portal_identity_session.py:36-45` | Lido. Plpgsql não valida a relação no `CREATE`: a função instala e **falha na 1ª chamada**. NÃO VERIFICADO por execução |
| I2 | O witness Python e o `StaffCaseStore` Java só aceitam `mzo_*` em `nspname='public'`. O Java ainda exige `databaseSchema` nulo ou `public`. O engine de dev usa `currentSchema=cibseven` no JDBC, e o SQL sem qualificação do plugin (`FROM MZO_PORTAL_READ_MEMBERSHIP`) resolve pelo `search_path` | `postgres.py:241`, `:266`; `StaffCaseStore.java:31-33`, `:44`; `service-cibseven.tf:90` | Lido. NÃO VERIFICADO |
| I3 | O plano staff exige `authorizationEnabled` e `tenantCheckEnabled` no engine **compartilhado** que serve Helena/worker/agentes | `HumanCommandPlugin.java:64` | O descritor secured herda `authorizationEnabled=true` (`secured/descriptors/bpm-platform.xml:16`). O valor no engine vivo está NÃO VERIFICADO |
| I4 | A imagem "secured" fecha o `engine-rest` por certificado, e worker/agentes/canal chamam sem credencial (a migração de callers é o pacote C, pendente) | `secured/README.md:10-15`, `:196-198`; `deploy/cibseven/Dockerfile:14-21` | Lido. Trocar o engine de dev para `secured-v2` **derruba a Helena**. Por isso o plano usa `Dockerfile.human` |

---

## 2. Decisões de arquitetura deste plano

- **D-A. O servidor nativo é o engine de dev existente, com o plugin.** A imagem do serviço
  `cibseven` passa a ser `Dockerfile.human` com `INSTALL_PORTAL_READ=true`, mais o connector
  mTLS. O `engine-rest` continua como está (sem autenticação, atrás de SG e Access, como hoje).
  Não se cria um engine separado: a fila e os casos que interessam (as escalações da Helena)
  estão **neste** engine e **neste** banco. Trade-off aceito: em dev a fronteira D7 (bypass pelo
  REST) continua aberta. Isso é dívida registrada e não vale para staging nem produção.
- **D-B. Transporte: NLB interno TCP 443 → Tomcat 8443, TLS de ponta a ponta.** O `native_origin`
  tem que ser HTTPS numa porta que só pode ser 443 (`production_config.py:58`;
  `portal-variables.tf:112`), e o Tomcat roda como uid 1000, que não abre porta abaixo de 1024.
  O NLB em TCP (sem terminar TLS) deixa o certificado do cliente chegar ao Tomcat
  (`HumanServlet.peer` precisa dele). Nome estável: registro numa zona Route53 **privada** deste
  state (ex.: `engine-native.maezo-operadora-dev.internal`), alias para o NLB. O
  `native_https_security_group_id` é o SG do NLB, com ingress 443 só do SG do portal. O SG do
  engine ganha ingress 8443 só do SG do NLB. Tudo fica no state `maezo-operadora` (o SG `tasks`
  é deste state, `security_groups.tf:11`). Custo: um NLB interno. Alternativa rejeitada: sysctl
  `ip_unprivileged_port_start` no Fargate (suporte NÃO VERIFICADO, e acopla a porta de negócio
  ao kernel).
- **D-C. Relações `mzo_*` em `public` e engine com `currentSchema=cibseven,public`.** Resolve o
  I2 **sem mudar código nem schema de manifesto**: o `ACT_*` continua em `cibseven` (primeiro do
  path) e o `MZO_*` resolve em `public`, que é o que os dois validadores exigem. **Condicionada ao
  spike da Onda 0** (auto-update de schema do engine com dois schemas no path; nenhuma colisão
  `ACT_`/`MZO_` em `public`). Se o spike falhar, o substituto é mudança de código com ADR (schema
  nativo pinado no manifesto, `portal-staff-material.v2`), e as Ondas 1-3 crescem cerca de 1
  semana.
- **D-D. A função de lock é instalada numa variante por tenant** (`amh.portal_sessions`,
  `amh.portal_memberships`), gerada do bloco canônico por substituição revisada, com
  `search_path=pg_catalog, portal_identity` intacto (o runtime confere `proconfig` exato,
  `production.py:137`). O `definition_sha256` passa a ser o da variante instalada. Isso não
  enfraquece nada: o pin sempre foi o da definição **instalada**, lida do catálogo.
- **D-E. A composição Java da configuração staff/AUTH é código novo, pequeno e fail-closed.** Um
  `ProcessEnginePlugin` de composição, registrado **antes** do `HumanCommandPlugin` no
  `bpm-platform.xml`, lê arquivos montados read-only, constrói
  `StaffCaseInstallation.Configuration`, `HumanAuthConfiguration` e o `catalogAnchor`, e chama os
  setters. No boot ele emite **uma** linha de log pública com
  `staff_native_configuration_digest=<hex>`. Essa linha é o canal independente do pin
  `native_configuration_sha256` (§4). Sem arquivo, o engine não sobe com o perfil staff. Sem
  default e sem descoberta.
- **D-F. A raiz de instalação é do aprovador.** O par Ed25519 `installation-root` é gerado **na
  máquina do aprovador humano**, e só ele assina a designação (`installation-proof.json`). A
  engenharia (agente ou pessoa) **não tem como** produzir um pacote que o loader aceite sem essa
  assinatura (`materials.py:111-122`). É isso que impede a aprovação de virar carimbo: o
  aprovador não "confere um hash", ele assina o conteúdo que leu. Nenhum agente gera, guarda ou
  usa a chave privada raiz.
- **D-G. Segredos novos só pelo caminho sancionado.** `OrganizationAccountAccessRole` (SCP
  `deny-secrets-without-rotation`), `--secret-string file://<arquivo em diretório temporário
  0700>`, CMK dedicada, arquivo apagado com sobrescrita ao final. Nunca o valor na linha de
  comando, no state, no log ou no repo.

---

## 3. Ondas executáveis

Convenção: **Artefatos · Onde roda · Pré-requisito · Pronto quando (medível) · Reversão**.
"Builder" é quem implementa: backend/Java, backend/Python, infra. "Dono do banco" é quem tem a
role de administração/migration: **não** o portal (`portal.md:41-49`, `:195`).

### Onda 0 — medir e provar a premissa (sem escrever nada na conta)

| Tarefa | Detalhe |
|---|---|
| **M1 · engine vivo** | Via `ecs execute-command` no `cibseven` (é o único container com root gravável, caminho sancionado): `grep -n 'authorizationEnabled\|tenantCheck\|processEngineName\|<process-engine name' /camunda/conf/bpm-platform.xml`; `curl -s localhost:8080/engine-rest/engine`. Anotar `engine_name` real |
| **M2 · bancos** | Numa task avulsa in-VPC read-only (receita da sonda): `current_database()` do DSN do portal e do engine (são o mesmo database?); `to_regclass('amh.portal_sessions')`, `to_regclass('public.portal_sessions')`; `SELECT nspname FROM pg_namespace`; colisão `SELECT relname FROM pg_class c JOIN pg_namespace n … WHERE nspname='public' AND relname ILIKE 'mzo\_%' OR relname ILIKE 'act\_%'` |
| **S1 · spike local (o que decide D-C)** | Com o harness `deploy/cibseven/package-test/` (Postgres descartável): imagem `Dockerfile.human` `INSTALL_PORTAL_READ=true`, `currentSchema=cibseven,public`, DDL `mzo_*` em `public`. Provar: engine sobe com auto-update; `ACT_*` fica em `cibseven`; `StaffCaseStore` passa o pin de `public`; handshake mTLS com o cliente Python (`StaffNativeClient`) devolve uma recusa **fechada** do plugin (não 404 nem 503 de Tomcat) |

- **Onde roda:** conta de dev (M1 e M2, só leitura) e estação local (S1).
- **Pré-requisito:** nenhum.
- **Pronto quando:** os 3 resultados estão anotados neste arquivo (§7) com o comando exato;
  D-C confirmada ou substituída.
- **Reversão:** nada a reverter (só leitura ou local).
- **Estimativa:** M1 e M2 em 0,5 dia; S1 em 2 a 3 dias.

### Onda 1 — código (PRs revisados; sem tocar a conta)

Tarefas **disjuntas por arquivo**, que podem ir em paralelo:

| T | Área / builder | Arquivos previstos | Pronto quando |
|---|---|---|---|
| T1.1 | backend/Java | `src/maezo/portal/engine/java/.../human/StaffDeploymentComposition.java` (novo) + teste; `deploy/cibseven/human-webapp/` intacto | `mvn -B package` verde; teste que prova: sem arquivo → engine não sobe; arquivo adulterado → recusa; log emite só o digest público; digest igual a `Configuration.digest()` |
| T1.2 | infra | `deploy/cibseven/Dockerfile.human` (ARG da composição, `server.xml` de deploy com **um** connector `SSLEnabled` 8443 `certificateVerification="required"`, `allowTrace=false`, sem atributos de proxy, keystore/truststore **montados**); `deploy/cibseven/native-deploy/server.xml` (novo) | build local verde; `S1` reexecutado sobre esta imagem |
| T1.3 | backend/Python (ferramenta, fora do runtime) | `tools/staff_materials/` (novo): `generate` (chaves Ed25519 read/witness/result, CA nativa, cert do servidor com SAN = hostname de D-B, cert do cliente, rascunho da designação **sem assinatura**), `sign-designation` (roda **na máquina do aprovador**, com a raiz dele), `assemble` (monta o bundle), `verify` (chama `decode_bundle` com os pins lidos de um arquivo **fornecido pelo aprovador**) | testes unitários com fixture sintética; `verify` recusa bundle com 1 byte trocado, pin trocado, designação sem assinatura da raiz |
| T1.4 | backend/Python + SQL | `deploy/sql/portal-identity-lock.sql.tmpl` (novo, variante por tenant, D-D) + `deploy/sql/engine-native-install.sql` (ordem dos resources `mzo_*` + login witness SELECT-only + grants) + teste que compara o template com o bloco canônico (`external-case-schema-postgres.sql:636-672`), com diferença **só** no schema | teste verde; diff revisado |
| T1.5 | backend/Python | `src/maezo/gateway/human/membership_publication_job.py` (novo `__main__`): publica `portal_memberships` → engine (`PortalReadPublication`) e principal → `/v1/authority`, idempotente por revisão | teste de integração contra o harness; 2ª execução não muda nada (CAS) |
| T1.6 | backend/Python | fonte de publicação de casos staff (`case_issuer`): lê as escalações vivas e publica `staff_case_grant` / `staff_policy_head` / `scope_complete`. **Bloqueada pela decisão de negócio N3** (§6): qual grupo vê qual caso | teste de integração; grant só para o grupo decidido |

- **Onde roda:** repositório, CI e local. Aviso: o CI está bloqueado por billing do Actions desde
  22/09, então o "verde" é local até o billing voltar.
- **Pré-requisito:** Onda 0 fechada (T1.2 e T1.4 dependem de D-C); T1.6 depende de N3.
- **Reversão:** revert do PR (nada foi implantado).
- **Estimativa:** T1.1 3-4 d · T1.2 2 d · T1.3 3 d · T1.4 1-2 d · T1.5 3-4 d · T1.6 3-5 d. Em
  paralelo: **~1,5 a 2 semanas** de calendário.

### Onda 2 — gerar os materiais (engenharia) e a raiz (aprovador)

- **Artefatos:** chaves e certificados da T1.3 (`generate`) num diretório temporário 0700, fora
  do repo; DSNs das duas logins novas (senha gerada localmente e gravada como verificador SCRAM
  calculado no cliente, mesma técnica do `portal_bff_amh`); **raiz Ed25519 gerada pelo aprovador
  na máquina dele** (só a pública, `installation-root.der`, sai de lá).
- **Onde roda:** estação da engenharia (agente autorizado) e estação do aprovador.
- **Pré-requisito:** T1.3 mergeada; hostname de D-B decidido (entra no SAN); `engine_name` e
  `database_incarnation` fixados (M1 e N4).
- **Pronto quando:** `generate` produz 11 dos 12 arquivos (falta a prova de instalação); o
  aprovador tem `installation-root.der` e a chave privada **só** com ele.
- **Reversão:** apagar o diretório temporário (nada saiu da máquina).
- **Estimativa:** 0,5 d de engenharia + 0,5 d do aprovador.

### Onda 3 — instalação no banco (dono do banco, não o portal)

- **Artefatos (banco de identidade):** schema `portal_identity`, roles `NOLOGIN`, tabela
  `external_login_tenant`, função `lock_external_session` (variante `amh`, D-D), login
  `portal_staff_lock_amh` (não-owner, sem super/bypassrls/createrole/createdb/replication,
  `EXECUTE` só na função) e linha `('portal_staff_lock_amh','amh')` em `external_login_tenant`.
- **Artefatos (banco do engine):** resources `mzo_*` em `public` (ordem da T1.4), login witness
  `portal_staff_witness_amh` **SELECT-only** em `public.mzo_portal_read_membership` e
  `public.mzo_human_principal`, sem RLS nessas duas (o witness exige `relrowsecurity=false`,
  `postgres.py:254`), e a linha de designação instalada.
- **Onde roda:** task avulsa in-VPC com a role de **administração/migration** (nunca
  `portal_bff_amh`, nunca master para DML de aplicação), executada pelo dono do banco ou por
  engenharia com autorização explícita dele para esta janela.
- **Pré-requisito:** T1.4 mergeada; backup/snapshot do Aurora antes (Velero não se aplica; é
  snapshot do cluster, e quem faz é o dono da plataforma).
- **Pronto quando:** as consultas de §4 (pins de função e relações) devolvem 1 linha cada; as
  duas logins passam `_qualify_login` numa sonda (mesma SQL de `production.py:69-139`); há
  **negativos provados**: witness não faz `INSERT`/`UPDATE`, lock login não lê `amh.portal_sessions`
  direto, nenhuma das duas lê `amh.*` fora da função.
- **Reversão:** script inverso na mesma janela (`DROP FUNCTION`, `DROP ROLE`, `DROP` das tabelas
  `mzo_*`, que estão vazias). O `ACT_*` não é tocado.
- **Estimativa:** 1 dia.

### Onda 4 — o engine vira servidor nativo (toca o engine compartilhado)

- **Artefatos:** imagem `amh/cibseven-maezo` construída de `Dockerfile.human` (T1.2), com digest
  **assinado** pelo mesmo `supply-chain.yml`; segredo `maezo-operadora/dev/engine/native-materials`
  (keystore do servidor, truststore com a CA do cliente, trust files do `HumanCommandPlugin` e do
  `PortalReadPlugin`, entrada da T1.1), publicado pelo caminho D-G; Terraform: init
  container no `cibseven` (mesmo padrão do init do portal), `DB_URL` com
  `currentSchema=cibseven,public`, `portMappings` 8443, NLB interno + target group TCP 8443 +
  listener TCP 443, zona privada + registro, SG do NLB, ingress 8443 no SG do engine só a partir
  do SG do NLB.
- **Onde roda:** `terraform plan`/`apply` do state `envs/dev-sa-east-1/maezo-operadora`, na
  receita conhecida: var-file PHI, 4 digests, cerca de plano. Janela combinada, porque **a
  Helena usa este engine**.
- **Pré-requisito:** Ondas 1 (T1.1, T1.2), 2 e 3.
- **Pronto quando:**
  1. serviço `cibseven` 1/1 estável;
  2. log do boot com a linha `staff_native_configuration_digest=` e nada além de valores
     públicos;
  3. de uma task in-VPC, `openssl s_client` **sem** certificado de cliente é recusado no
     handshake, e com o certificado da T1.3 `POST /maezo-human/v1/staff-case-list` com `{}`
     devolve a recusa fechada do plugin;
  4. **bateria da Helena sem regressão** (mesma contagem OK/DIVERGE da última medida);
  5. `GET /engine-rest/version` continua 200 pela 8080 (worker e agentes intactos).
- **Reversão:** voltar `engine_image_digest` para o anterior (`6f478e23…`, lido da task definition
  viva antes do apply) e `DB_URL` para `currentSchema=cibseven`. As tabelas `mzo_*` são aditivas e
  ficam. NLB, zona e SGs saem por `terraform apply` do commit anterior.
- **Estimativa:** 1 a 2 dias (o apply em si é curto; a janela e a bateria custam o dia).

### Onda 5 — montar, validar e aprovar o pacote

1. Engenharia roda `assemble` com os 11 arquivos + a designação **rascunho** e entrega ao
   aprovador: a designação legível, o manifesto e a lista de fontes independentes de §4.
2. **O aprovador confere cada campo contra a fonte independente da tabela de §4, na máquina
   dele**. Só então roda `sign-designation` com a raiz dele e devolve `installation-proof.json`.
3. Engenharia roda `assemble` de novo e em seguida `verify`, com os pins **do arquivo que o
   aprovador escreveu**, não com os que o gerador calculou. `verify` chama `decode_bundle`
   (`materials.py:186-197`), o mesmo código que o init e o BFF executam.
4. Publicação: `aws secretsmanager create-secret --name maezo-operadora/dev/portal/amh/staff-materials
   --kms-key-id <CMK dedicada> --secret-string file://<bundle>`, **assumindo
   `OrganizationAccountAccessRole`**. Anotar `ARN` (com sufixo) e `VersionId`.

- **Onde roda:** estações da engenharia e do aprovador; a publicação usa a role sancionada.
- **Pré-requisito:** Onda 4 (os pins de SPKI e de configuração nativa vêm do servidor **vivo**).
- **Pronto quando:** `verify` sai com 0 usando os pins do aprovador; `describe-secret` mostra a
  versão com a CMK certa; o bundle local foi apagado; o aprovador registrou por escrito (PR ou
  comentário assinado **por ele**) os valores que aprovou.
- **Reversão:** `delete-secret --recovery-window-in-days 7` (o portal ainda não lê o segredo);
  revogar a raiz = o aprovador descarta a chave e a Onda 5 recomeça.
- **Estimativa:** 0,5 d de engenharia + 0,5 a 1 d do aprovador.

### Onda 6 — imagem derivada do portal e `portal.staff`

- **Artefatos:** imagem de `deploy/portal.Dockerfile` sobre o digest da app que já roda
  (`39c4afd6…`), assinada e atestada pelo `supply-chain.yml`; `portal.staff` preenchido no
  `portal.auto.tfvars` com os valores **aprovados** (campos em `portal.md:152-162`) e comentário
  de proveniência por campo, como o resto do arquivo.
- **Onde roda:** PR (o job `verificar` confere a assinatura do `portal_image_digest`), depois
  `terraform plan` (a cerca de plano precisa mostrar **só** o portal e as regras de egress
  nativas) e `apply`.
- **Pré-requisito:** Onda 5.
- **Pronto quando:** task do portal 1/1; init `SUCCESS`; `MAEZO_PORTAL_CAPABILITIES=identity,staff_cases`
  na task definition; `/session` continua 401 com o corpo estático.
- **Reversão:** `staff = null` + digest anterior, e novo apply. O portal volta a `identity` sem
  perda (sessões e auditoria ficam).
- **Estimativa:** 1 dia.

### Onda 7 — aceitação do perfil staff (o "flip" que importa)

- **Pronto quando**, com `plantao.teste`:
  1. `GET /api/v1/portal/cases` → 200 com **um caso real** publicado pela T1.6;
  2. `GET /cases/{ref}` → 200;
  3. `atendimento.teste` (outro grupo) **não** vê o caso (negativa por grupo);
  4. membership revogada → recusa na próxima leitura (currentness);
  5. log sem DSN, cookie ou token (mesmo grep do §1.7 #7).
- **`/tasks?queue=team` continua 503 nesta onda, por desenho** (§1.1).
- **Reversão:** Onda 6 ao contrário.
- **Estimativa:** 0,5 a 1 dia, mais o que a T1.6 revelar.

### Onda 8 — plano humano: a fila de tarefas (programa próprio)

O que ela exige, medido por leitura, **sem estimativa fina**:

- pacote `portal-human-material.v1`: 14 arquivos, 3 chaves Ed25519 por propósito, `cursor-keys`
  AEAD, **duas** superfícies mTLS (`read`, `command`), DSNs de outbox e de source
  (`production_materials.py:48-73`, `:79-105`);
- **suporte Terraform que não existe**: init, volume, egress e env `MAEZO_PORTAL_HUMAN_*`;
  `service-portal.tf:203` precisa aprender o terceiro literal;
- `PortalReadPlugin` com trust de leitura e de publicação, publicação do catálogo e das tarefas
  (Q2), relay de atribuição;
- tudo com versão e digest **distintos** do pacote staff (`production_config.py:145-149`).

Mesma disciplina das Ondas 2 a 7, com um segundo aprovador ou o mesmo (N1). **Estimativa: 3 a 6
semanas, confiança baixa.** O desenho detalhado vira um plano irmão depois da Onda 7, quando a
parte staff tiver provado o caminho.

### Paralelismo e caminho crítico

```
Onda 0 ─► Onda 1 [T1.1 ∥ T1.2 ∥ T1.3 ∥ T1.4 ∥ T1.5 ; T1.6 após N3] ─► Onda 2 ─► Onda 3 ─► Onda 4 ─► Onda 5 ─► Onda 6 ─► Onda 7 ─► Onda 8
                                     └─ Onda 2 (geração) pode começar assim que T1.3 fechar, em paralelo a T1.1/T1.5
                                     └─ Onda 3 pode rodar em paralelo à Onda 2 (não depende das chaves, só dos logins)
```

A disjunção de arquivos da Onda 1 foi conferida pelos caminhos previstos: T1.1 é Java novo;
T1.2 é `deploy/cibseven/`; T1.3 é `tools/` novo; T1.4 é `deploy/sql/` novo; T1.5 e T1.6 são
módulos Python novos e distintos. Risco de interseção: T1.1 e T1.2 se tocam no
`bpm-platform.xml` (ordem dos plugins). **A T1.2 é dona desse arquivo** e a T1.1 entrega só a
classe e o teste.

**Total até `/cases` com caso real (Ondas 0 a 7): 3 a 5 semanas** de calendário, com um builder
por área e o aprovador disponível nos dias das Ondas 2 e 5. A variação vem de S1 (D-C) e de N3.

---

## 4. Tabela de aprovação dos pins

**Quem gera:** engenharia (agente autorizado ou pessoa), com `tools/staff_materials generate/assemble`.
**Quem aprova:** o aprovador nomeado (N1). **Regra:** o aprovador nunca copia o valor que o
gerador mostrou. Ele recalcula o valor **da fonte da coluna "Fonte independente"** e compara.
Pin que o aprovador não consegue recalcular sozinho não é aprovado.

| Pin | O que é | Fonte independente | Como o aprovador confere (comando) |
|---|---|---|---|
| `root_key_sha256` | SHA-256 do SPKI DER da raiz Ed25519 | **A chave do próprio aprovador** (D-F) | `openssl pkey -in root.pem -pubout -outform DER \| sha256sum` na máquina dele |
| `designation_sha256` | digest canônico da designação | O texto da designação que **ele leu e assinou** | `sign-designation` imprime o digest do que foi assinado. Ele confere que cada `entries[].key_fingerprint` bate com as chaves públicas listadas e que papéis, propósitos e logins batem com §3/Onda 3 |
| `read_key_sha256`, `witness_key_sha256` | SHA-256 do SPKI das chaves de leitura e witness | (1) a designação assinada; (2) a linha de designação **instalada no banco do engine** (Onda 3) | ler `entries[]` da designação; no banco: `SELECT canonical_designation FROM mzo_staff_case_designation_current` (sessão read-only própria) e comparar; distintos entre si e da raiz |
| `native_configuration_sha256` | `StaffCaseInstallation.Configuration.digest()` do engine | **O próprio engine vivo** (log do boot, D-E) | `aws logs filter-log-events --log-group-name /ecs/maezo-operadora-dev/cibseven --filter-pattern staff_native_configuration_digest` na última task |
| `native_server_spki_sha256` | SHA-256 do SPKI do certificado que o listener apresenta | **O listener vivo**, não o bundle | de uma task in-VPC: `openssl s_client -connect <host>:443 -servername <host> </dev/null 2>/dev/null \| openssl x509 -pubkey -noout \| openssl pkey -pubin -outform DER \| sha256sum` |
| `native_origin` | `https://<host>` | Terraform output + DNS privado | `terraform output` do state; `getent hosts <host>` de dentro da VPC resolve para os IPs do NLB interno; `<host>` está no SAN do certificado lido acima |
| `scope.tenant` | `amh` | `portal.tenant` no tfvars | igual ao `tenant` do objeto `portal` |
| `scope.environment` | `dev` | domínio instalado (trust file do engine) | igual ao valor da linha de designação e do trust file |
| `scope.engine_name` | nome do process engine | **O engine vivo** (M1) | `curl -s <engine>:8080/engine-rest/engine` → `[{"name":…}]` |
| `scope.database_incarnation` | identidade da encarnação do banco do engine | registro da instalação (N4) | valor gravado na linha de designação instalada = valor no trust file |
| `public_manifest_sha256` | digest canônico do `manifest.json` | **Cálculo do aprovador, feito por último**, sobre o manifesto que ele conferiu campo a campo contra as linhas acima e abaixo | `verify --print-manifest-digest` na máquina dele, **depois** de conferir os campos. Não é "calcular de bundle não confiável": o bundle vira confiável quando cada campo foi conferido. O que o aprovador atesta é o próprio manifesto |
| `material_secret_version_id`, `material_secret_arn`, `material_kms_key_arn` | metadados do segredo | Secrets Manager | `aws secretsmanager describe-secret` e `list-secret-version-ids` (sem `get-secret-value`) |
| `native_https_security_group_id`, `native_database_security_group_id`, `native_database_port` | destinos de egress | state Terraform + EC2 | `aws ec2 describe-security-groups --group-ids …`: VPC certa; ingress 443 do NLB só do SG do portal; porta 5432 igual ao host dos DSNs |
| `maximum_seconds` | teto de espera | `native_maximum_seconds` do manifesto e configuração do engine | inteiro de 1 a 10, ≤ ao valor nativo |
| *(dentro do manifesto)* `function_pin` | `oid`, `owner`, `definition_sha256` da função instalada | **Catálogo do banco de identidade** | `SELECT p.oid, pg_get_userbyid(p.proowner), encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex') FROM pg_proc p WHERE p.oid=to_regprocedure('portal_identity.lock_external_session(text)')`. Mesma fórmula do runtime, `production.py:138` |
| *(dentro do manifesto)* `native_relation_pins` | `oid`, `owner` das 2 relações | **Catálogo do banco do engine** | `SELECT c.relname, c.oid, pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN ('mzo_portal_read_membership','mzo_human_principal')` |
| *(arquivos públicos)* `session-lock-ca.pem`, `native-witness-ca.pem` | raízes RDS | bundle vendorizado e pins do repo | SHA-256 contra `deploy/certificates/README.md` |
| *(arquivos públicos)* `native-ca.pem` | CA que emitiu o cert do servidor | o listener vivo | `openssl verify -CAfile native-ca.pem <cert lido do s_client>` → `OK` |
| *(arquivos públicos)* `read-client-certificate.pem` | cert do cliente | designação assinada | SPKI do cert = `entries[read_requester].certificate_spki` |

Não aprova: nenhum agente. Um agente pode **rodar** os comandos acima para mostrar a saída, mas
o valor aprovado é o que o aprovador obteve na sessão dele. A aprovação fica registrada por ele,
com a identidade dele (N1).

---

## 5. Estimativa honesta

| Onda | Esforço | Calendário | Confiança |
|---|---|---|---|
| 0 | 2,5-3,5 d | 3-4 d | média (S1 é a incógnita) |
| 1 | ~15-20 d-pessoa | 1,5-2 sem (paralelo) | média; T1.6 depende de N3 |
| 2 | 1 d | 1 d | alta |
| 3 | 1 d | 1 d (janela) | alta |
| 4 | 1-2 d | 1-2 d (janela) | média (engine compartilhado) |
| 5 | 1-1,5 d | 1-2 d (agenda do aprovador) | alta |
| 6 | 1 d | 1 d | alta |
| 7 | 0,5-1 d | 1 d | média |
| **0-7** | | **3-5 semanas** | |
| 8 | não estimado com precisão | +3-6 semanas | **baixa** |

Pode rodar em paralelo: as 5 tarefas da Onda 1 (T1.6 depois de N3); a Onda 2 com T1.1/T1.5
assim que T1.3 fechar; a Onda 3 com a Onda 2. **Não** paraleliza: 4 → 5 → 6, porque os pins de
SPKI e de configuração nativa só existem depois que o servidor vivo sobe.

Custo recorrente novo: um NLB interno em dev, a CMK do segredo staff e a do segredo nativo.
**Custo operacional recorrente:** a designação, o manifesto e o snapshot de revogação têm
validade (`production_config.py:309-311`, `:291-301`; `materials.py:96-97`). Quando vencem, o
portal **recusa subir** e cada renovação é uma Onda 5 nova (novos digests, nova aprovação, novo
apply). A cadência é a decisão N2.

---

## 6. O que não dá para decidir sem o dono ou o diretor

1. **N1 — Quem é o aprovador nomeado** (staff e, depois, human), e se a custódia da raiz exige
   uma segunda pessoa. Proposta: Leonardo aprova; a raiz fica só com ele; nenhum agente a
   recebe.
2. **N2 — Validade do pacote e cadência de reaprovação em dev.** Quanto tempo a designação e o
   snapshot de revogação valem, o que define a frequência das Ondas 5 e 6 repetidas. Proposta:
   14 dias, com lembrete 3 dias antes.
3. **N3 — Quem vê qual caso** (política de `staff_case_grant`): qual grupo Cognito vê quais
   escalações da Helena. É regra de negócio e de privacidade. A T1.6 não começa sem ela.
4. **N4 — Janela e risco da Onda 4.** Trocar a imagem do engine que a Helena usa em dev, com a
   reversão descrita; e o nome da `database_incarnation` que fica gravado.
5. **N5 — Aceitar o D-A em dev:** `engine-rest` continua sem autenticação enquanto o plano
   humano roda (dívida D7), e isso fica explicitamente proibido em staging e produção.
6. **N6 — Se a meta é a fila de tarefas** (Onda 8, +3 a 6 semanas) ou se `/cases` (Onda 7) basta
   para o teste conjunto do diretor.

---

## 7. Registro de medições (preencher na Onda 0)

| Medição | Comando | Resultado | Data |
|---|---|---|---|
| M1 `authorizationEnabled`/`tenantCheck`/`engine_name` | §3 Onda 0 | *pendente* | |
| M2 identity DB = engine DB? schema das tabelas do portal | §3 Onda 0 | *pendente* | |
| M2 colisões `mzo_`/`act_` em `public` | §3 Onda 0 | *pendente* | |
| S1 D-C (`currentSchema=cibseven,public`) | §3 Onda 0 | *pendente* | |
| Conta 203312548462: segredos `maezo-operadora/dev/portal/*` | `aws secretsmanager list-secrets` (thread principal, 23/09 UTC) | só `…/amh/session-dsn`; nenhum `staff-materials` | 23/09/2026 |
