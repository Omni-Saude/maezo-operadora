# Plano — autoridade nativa do portal humano em dev (fila de casos)

**Status:** PLANO (leitura + desenho). Nada foi aplicado na AWS, nenhum segredo foi criado,
nenhum código de produção foi escrito. **Data:** 22/09/2026 (medições do repo nesta data, `main`
em `7ca37fa6`; medição da conta pela thread principal em 23/09 UTC).
**Autor:** software-architect (agente), a pedido do dono do repositório (Leonardo), que autorizou
executar o programa. **Este documento não aprova pin nenhum**: ele separa quem GERA de quem
APROVA (§4) e o aprovador é uma pessoa nomeada pelo dono (§6).

Regra de leitura: toda afirmação de fato traz `arquivo:linha` ou o comando que a mede. O que
depende de execução e não foi executado está marcado **NÃO VERIFICADO**.

**Onda 0 medida em 23/09/2026** (infra-specialist; M1 e M2 só leitura na conta, S1 local; §7).
Resultado: **D-C refutada** e substituída por **D-C2** (§2). As medições também corrigiram
três premissas do plano: o `/cases` depende da autoridade Q2 do engine (`PortalReadPlugin` com um
provedor SPI que não existe), o DDL staff não instala em PostgreSQL, e o perfil staff exige o
plano AUTH instalado. Cada correção está marcada **[Onda 0]** no ponto do texto em que vale.

**Decisões de 23/09/2026 (dono + software-architect).** O dono aprovou D-C2 (N7), e a decisão
virou o **ADR-0060** (`docs/adr/0060-schema-nativo-dedicado-maezo-native.md`). A meta segue sendo
`/cases` (N8); N3 e N5 ficaram com as recomendações. O arquiteto desenhou a T1.7 (§3.1),
redistribuiu a Onda 1 (T1.7a/T1.7b, T1.9 nova, escopo maior na T1.5) e reestimou as Ondas 1 a 7
(§5). Os pontos alterados estão marcados **[23/09]**.

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
   **[Onda 0] Essa estimativa não vale mais** (§5). Toda rota staff passa por
   `PortalReadPlugin.staffLease` (`HumanCommandPlugin.java:209`), e o `PortalReadPlugin` não sobe
   sem um provedor `PortalReadTrust.Providers` que **não existe no repositório**. S1 executou isso:
   a imagem `INSTALL_PORTAL_READ=true` morre no boot com `READ_DEPENDENCY_UNAVAILABLE`. Assim, a
   autoridade Q2 do engine, que o plano punha na Onda 8, fica no caminho crítico de `/cases`
   (nova T1.7).
   **[23/09] Nova estimativa: 4 a 6 semanas** de calendário para as Ondas 1 a 7, com confiança
   média-baixa (§5). Ela vale porque a T1.7 foi recortada ao que o staff usa (§3.1). A autoridade
   Q2 inteira, com publicação de tarefas, classificação e política de identidade, continua na
   Onda 8.

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
| I1 | A função de lock referencia `public.portal_sessions` e `public.portal_memberships`, mas em dev as tabelas estão no schema `amh`: a migration cria sem qualificar, com `search_path` `{tenant}, public`, e a role `portal_bff_amh` usa `search_path=amh` | `external-case-schema-postgres.sql:658`, `:663`, `:669`; `src/maezo/platform/migrations/env.py:57-62`; `0012_portal_identity_session.py:36-45` | Lido. Plpgsql não valida a relação no `CREATE`: a função instala e **falha na 1ª chamada**. **[Onda 0] Catálogo confirma** (M2): `amh.portal_sessions`/`amh.portal_memberships` existem, `public.portal_*` não, e o schema `portal_identity` não existe. A falha da função em si continua NÃO VERIFICADA por execução |
| I2 | O witness Python e o `StaffCaseStore` Java só aceitam `mzo_*` em `nspname='public'`. O Java ainda exige `databaseSchema` nulo ou `public`. O engine de dev usa `currentSchema=cibseven` no JDBC, e o SQL sem qualificação do plugin (`FROM MZO_PORTAL_READ_MEMBERSHIP`) resolve pelo `search_path` | `postgres.py:241`, `:266`; `StaffCaseStore.java:31-33`, `:44`; `service-cibseven.tf:90` | **[Onda 0] Provado por execução** (S1 R1): com `currentSchema=cibseven` e `MZO_*` em outro schema, o engine cria os 49 `ACT_*` e morre em `postProcessEngineBuild` com `human engine store unavailable`, porque `MZO_HUMAN_TENANT` não resolve. É o que aconteceria hoje em dev se só a imagem fosse trocada |
| I3 | O plano staff exige `authorizationEnabled` e `tenantCheckEnabled` no engine **compartilhado** que serve Helena/worker/agentes | `HumanCommandPlugin.java:64` | O descritor secured herda `authorizationEnabled=true` (`secured/descriptors/bpm-platform.xml:16`). **[Onda 0] Medido** (M1): o engine vivo **já** tem `authorizationEnabled=true` (`/camunda/conf/bpm-platform.xml:18`). `tenantCheckEnabled` não aparece no descritor, então vale o default do engine (true), o que continua NÃO VERIFICADO por execução. Ligar o plugin não muda essas duas flags |
| I4 | A imagem "secured" fecha o `engine-rest` por certificado, e worker/agentes/canal chamam sem credencial (a migração de callers é o pacote C, pendente) | `secured/README.md:10-15`, `:196-198`; `deploy/cibseven/Dockerfile:14-21` | Lido. Trocar o engine de dev para `secured-v2` **derruba a Helena**. Por isso o plano usa `Dockerfile.human` |
| I5 **[Onda 0]** | **O plano AUTH, obrigatório para staff, não qualifica com D-C.** `staffConfigured()` exige `authRuntime` (`HumanCommandPlugin.java:60-65`). `AuthInstallation.runtime` relê, **na conexão do engine**, `ConsumerEdgeInstallation.session(owner=false)`, que exige quatro coisas: `current_schema()` = schema das `mzo_auth_*`, dono desse schema ≠ login do engine, login do engine **sem CREATE** nele e sem nenhuma role-membro. Com `currentSchema=cibseven,public`, `current_schema()` vira `cibseven`, e o dono e o CREATE são do próprio `cibseven_app` | `AuthInstallation.java:90-94`; `ConsumerEdgeInstallation.java:60-74`; `AuthInstallation.java:21` (`owner_role ≠ runtime_role`) | **Provado** (S1 R2, SQL das mesmas precondições como `cibseven_app`): `current_schema=cibseven`, `owner=cibseven_app`, `owner_differs=f`, `runtime_lacks_create=f` |
| I6 **[Onda 0]** | **`INSTALL_PORTAL_READ=true` não sobe.** `PortalReadPlugin.preInit` exige `MAEZO_PORTAL_READ_TRUST_FILE` **e exatamente um** `PortalReadTrust.Providers` via `ServiceLoader`. Não existe implementação nem registro `META-INF/services` no repositório, só provedores anônimos de teste | `PortalReadPlugin.java:19-24`; `find src/maezo/portal/engine/java -path "*META-INF/services*"` → vazio; `LIFECYCLE.md:128-143` ("Q2 cannot currently be built from configuration alone") | **Provado** (S1 R0): Tomcat não sobe, `ENGINE-08043 … Start process engine default: READ_DEPENDENCY_UNAVAILABLE`. Staff precisa dele: `executeStaff` chama `PortalReadPlugin.staffLease` em **toda** operação (`HumanCommandPlugin.java:209`) e o `Q2Intersection` monta um `PortalReadCommand` com a admissão Q2 (`StaffCaseReadCommand.java:290-306`) |
| I7 **[Onda 0]** | **O DDL staff não instala em PostgreSQL.** Três `FOREIGN KEY` sem lista de colunas apontam para tabelas cuja PK tem uma coluna a menos: `designation_current` (6 colunas) → `designation_event` (PK de 5); `policy_current`/`policy_dependency` (7) → `policy_version` (PK de 6). O único teste que abre o arquivo faz busca de string (`StaffCaseListRepairTest.java:98-104`) | `staff-case-schema-postgres.sql:18-19`, `:112-113`, `:120-121` | **Provado** (S1, PG 17.11): `ERROR: number of referencing and referenced columns for foreign key disagree` na linha 19. O spike seguiu com um patch local (lista explícita das colunas da UNIQUE), sem alterar o repo |
| I8 **[Onda 0]** | **A matriz de grants do DDL staff contradiz o `StaffCaseStore`.** O store trata como imutável só o que termina em `_event/_receipt/_continuity/_cursor/_version/_dependency` e, para as outras, **exige UPDATE**. `mzo_staff_case_checkpoint_chunk` recebe só `SELECT,INSERT` no DDL, então o pin recusa **toda** chamada staff | `StaffCaseStore.java:50-60`; `staff-case-schema-postgres.sql:138` (grant `SELECT,INSERT` inclui `checkpoint_chunk`) | **Provado** (S1 R2, matriz lida como `cibseven_app`): `checkpoint_chunk ins=t upd=f`. A correção (sufixo `_chunk` imutável no código **ou** UPDATE no DDL) é do backend: decide o que é imutável. **[23/09] Decidido (ADR-0060 D6):** `_chunk` passa a imutável no código (T1.8). Só existe INSERT (`StaffCaseStore.java:155`) e leitura (`:89`); o DDL fica `SELECT,INSERT` |
| I9 **[Onda 0]** | **Ordem do primeiro boot.** Quando o login do engine não tem CREATE no primeiro schema do path (o que I5 exige), um banco **vazio** não sobe: o auto-update tenta criar `ACT_*` ali | S1 R4 | **Provado**: `ENGINE-03015 … permission denied for schema public`. Em dev o `ACT_*` já existe (49 tabelas em `cibseven`, M2), então isso não bloqueia; é regra de DR e restore: subir primeiro com `currentSchema=cibseven` |

---

## 2. Decisões de arquitetura deste plano

- **D-A. O servidor nativo é o engine de dev existente, com o plugin.** A imagem do serviço
  `cibseven` passa a ser `Dockerfile.human` com `INSTALL_PORTAL_READ=true`, mais o connector
  mTLS. O `engine-rest` continua como está (sem autenticação, atrás de SG e Access, como hoje).
  Não se cria um engine separado: a fila e os casos que interessam (as escalações da Helena)
  estão **neste** engine e **neste** banco. Trade-off aceito: em dev a fronteira D7 (bypass pelo
  REST) continua aberta. Isso é dívida registrada e não vale para staging nem produção.
  **[Onda 0] Correção:** com `INSTALL_PORTAL_READ=true` a imagem só sobe quando existe o provedor
  `PortalReadTrust.Providers` qualificado (I6), e ele não existe. Até a T1.7 fechar, a única imagem
  que sobe é `INSTALL_PORTAL_READ=false`, e nela toda rota staff devolve `503
  HUMAN_ENGINE_UNAVAILABLE` (S1). O restante de D-A continua valendo. O engine vivo é
  `cibseven` 2.1.0 com `engine_name=default`, a imagem `amh/cibseven-maezo@sha256:6f478e23…`
  (task definition `maezo-operadora-dev-cibseven:5`) e nenhum JAR Maezo (M1).
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
- ~~**D-C. Relações `mzo_*` em `public` e engine com `currentSchema=cibseven,public`.**~~
  **REFUTADA na Onda 0 (23/09/2026).** O que ela prometia funciona: o engine sobe, o auto-update
  mantém os 49 `ACT_*` em `cibseven`, `MZO_*` resolve em `public` e o pin do `StaffCaseStore` passa
  (S1 R2). O que ela não viu é o plano AUTH, obrigatório para staff. Ele exige que
  `current_schema()` da conexão do engine seja o schema das `mzo_auth_*`, com dono ≠ login do
  engine e sem CREATE para ele (I5). Com `cibseven` primeiro no path, `current_schema()` é
  `cibseven`, que é do próprio `cibseven_app`. Nenhum layout com `cibseven` na frente qualifica.
  A inversão `currentSchema=public,cibseven` passa nas precondições de sessão (S1 R3), mas foi
  **rejeitada** por três motivos: (i) o dono de `public` é `pg_database_owner`, uma role reservada
  que não faz login (`ALTER ROLE … LOGIN` → `role name "pg_database_owner" is reserved`), e a
  instalação AUTH exige login = dono do schema, o que obrigaria a trocar o dono de `public` no banco
  compartilhado; (ii) `maezo_app` tem CREATE em `public` (M2) e com `public` na frente qualquer
  tabela criada ali **sombreia** o `ACT_*` do engine (S1: `public.act_ge_property` passou a ser a
  resolvida); (iii) as tabelas `checkpoint*` do checkpointer da Helena moram em `public`.
- **D-C2 (substitui D-C). Schema nativo dedicado, pinado, na frente do path do engine.**
  - **Layout:** um schema novo (`maezo_native`, ADR-0060) no database `maezo`,
    dono um login de instalação dedicado (`maezo_native_schema_owner`, ADR-0060), que não é `maezo_app`,
    nem `cibseven_app`, nem master. Nele ficam **todas** as `mzo_*`: human, portal-read, staff e
    auth. O engine roda com `currentSchema=maezo_native,cibseven` e `cibseven_app` recebe só
    `USAGE` no schema e os grants de tabela de cada DDL.
  - **Código (com ADR):** o `nspname='public'` fixo do `StaffCaseStore` (`StaffCaseStore.java:44`)
    e do witness (`postgres.py:241`, `:266`) vira schema pinado no manifesto,
    `portal-staff-material.v2`.
  - **Provas no spike:** S1 R5 subiu o engine nesse layout. `ACT_*` ficou em `cibseven`, `MZO_*`
    resolveu em `maezo_native`, as precondições AUTH passaram (`owner=native_owner≠cibseven_app`,
    sem CREATE, sem membership) e o SQL de pin staff com `nspname='maezo_native'` passou nas 14
    relações. O mTLS se comportou igual.
  - **Sombreamento:** só o dono cria no schema da frente.
  - **O que D-C2 herda:** a regra do primeiro boot (I9). Ela não afeta dev, onde o `ACT_*` já
    existe, mas vira passo do runbook de DR.
  - **Custo:** o "~1 semana" que o plano previa para o substituto, mais a T1.8 (§3).
  - ~~**Dono da decisão:** esta é uma proposta de infra a partir da medição. **Quem decide D-C2 é o
    software-architect**, com o ADR.~~
  - **[23/09] DECIDIDA: ADR-0060.** O dono aprovou (N7) e o ADR fixa o seguinte:
    - **Nomes:** schema `maezo_native` e login dono **`maezo_native_schema_owner`**. O nome
      proposto antes, `maezo_native_owner`, colidia na leitura com o schema `maezo_native_owner_v1`
      do programa native-v2 (ADR-0054).
    - **Schema como pin:** ele entra em `portal-staff-material.v2` (`native_schema`) e no `digest()`
      da configuração Java staff.
    - **Resolução provada no pin:** para cada relação, `to_regclass('<nome sem schema>')::oid` tem
      que ser igual ao OID pinado. Isso cobre um homônimo em `pg_temp`, que o PostgreSQL busca
      antes do path.
    - **Comparação exata**, sempre. Nunca por prefixo: `maezo_native` é prefixo de
      `maezo_native_v2`.
    - **I8** se resolve no código: `_chunk` passa a imutável, e o DDL continua `SELECT,INSERT`.
    - **Runbook de banco vazio** (ADR-0060, Consequência 4): subir a imagem **antiga** com
      `currentSchema=cibseven`, reinstalar com incarnation nova, fazer nova admissão Q2 e nova
      designação, e só então subir a imagem nova.
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

- **D-H [23/09, consulta da T1.6 / PR #483]. Seis decisões que fecham as portas do `case_issuer`.**
  Cada uma diz o que foi decidido, onde muda e qual tarefa implementa.
  1. **Âncora escalação → caso staff (`CaseAnchor`).** O engine só reconhece como caso staff uma
     guia AUTH reivindicada (`NativeCaseIdentityReader.java:24`, `kind=authorization`). **Não** se
     cria um `kind` novo nesta onda. A âncora vai pela **instância**, sem interpretar número de guia:
     - só entra escalação com business key `ESC-{tenant}-sla-auth-{guia}` (ADR-0051);
     - dela se deriva `AUTH-{tenant}-{guia}`, e o engine REST devolve **exatamente uma** instância
       `SP-OP-AUTH-001` do mesmo tenant;
     - a reivindicação humana precisa ter `instance_` igual a essa instância e `tenant_` igual ao
       tenant. O caso é o `case_` dela.

     Zero ou mais de uma instância, ou reivindicação ausente, contam como `unanchored`: sem grant e
     com contador exposto no log do job. Um erro da âncora não vaza, porque o engine revalida a
     reivindicação no grant (`identity_digest`).
     **Consequência de produto:** as escalações conversacionais da Helena (P1, red flag) **não**
     aparecem em `/cases` nesta onda. Um `kind` de caso "escalation" é programa próprio, com ADR,
     depois da Onda 7. É a **N9** (§6). Muda em `case_issuer_sources.py`: `AuthClaimAnchor` lê
     `mzo_auth_guide_claim` pelo login do item 3, só com SELECT nas colunas `tenant_`, `instance_`
     e `case_`. **Tarefa: T1.6.**
  2. **Witness sem sessão.** O emissor ganha a **própria** entrada `identity_verifier` na
     designação (`entry_ref=case-issuer-witness`, purpose só `membership_current`), com chave
     própria e o login SELECT-only **`maezo_native_issuer_witness`**, que tem os mesmos grants do
     witness do portal. O engine aceita porque a entrada é procurada por fingerprint
     (`StaffCaseInstallation.java:55-61`, `:82`), e no caminho de publicação o `session_ref` não é
     comparado a uma sessão (`StaffCasePublicationCommand.java:40`, `principal=null`). O
     `session_ref` vale `case-issuer-run:{run_id}`.
     **Rejeitado:** reusar a chave witness do portal, porque daria dois custodiantes para uma chave,
     e revogar um derrubaria o outro.
     Muda em:
     - T1.3: `generate` cria a chave e a entrada no rascunho;
     - T1.4: login e grants;
     - T1.6: composição.

     **Pronto quando:** o `InstalledStaffAuthority` (Python) e o loader do portal aceitam duas
     entradas `identity_verifier`. Isso é **NÃO VERIFICADO**, e o teste entra na T1.6.
  3. **Estado durável.** Fica numa tabela nova, `maezo_native.mzo_staff_case_issuer_ledger`
     (revisão, grants e checkpoints emitidos, pedido pendente em bytes):
     - dono: `maezo_native_schema_owner`;
     - login novo **`maezo_native_case_issuer`**, com SELECT, INSERT e UPDATE só no ledger, sem
       DELETE, e SELECT de coluna em `mzo_auth_guide_claim` (item 1) e em `amh.portal_memberships`
       (este grant vem do dono de `amh`);
     - `cibseven_app` e os logins witness **sem** grant nenhum no ledger.

     A escrita é CAS por `revision` (`UPDATE … WHERE revision=$old`). Perder o ledger é recuperado
     com `policy_ref` novo e a revisão recomeçando (item 6). **NÃO VERIFICADO** que o engine aceita
     reiniciar `source_revision` sob policy nova: é teste da T1.6.
     Muda em: `deploy/sql/engine-native-install.sql` e o teste PG 17 (T1.4); `PostgresIssuerLedger`
     em `case_issuer_sources.py` (T1.6).
  4. **Operações da entrada de leitura: exatamente `["detail","list"]`.** O Java já aceita isso
     (`StaffCaseInstallation.java:63`). O defeito está no loader Python
     (`src/maezo/gateway/staff_cases/materials.py:158`, que exige `("detail",)`). A correção:
     - `materials.py` passa a exigir `("detail","list")`, e `("detail",)` sozinho é recusado, porque
       o portal serve `/cases`;
     - o `generate` da T1.3 emite essa lista para `read_requester` e `case_issuer`.

     A projeção `staff_current_task.v1` continua na **entrada**, e o que a Onda 7 corta é o
     **grant**. **Tarefa: T1.10**, com negativo `["detail"]` recusado e `["list"]` recusado, em
     `tests/unit/gateway/test_staff_production_materials.py`.
  5. **Tenant de dev = `amh`.** O tenant é declarado explicitamente na configuração do emissor, sem
     default no código nem no módulo Terraform. Continua valendo que, sem `tenant-id`, o emissor
     recusa tudo (fail-closed). Muda na variável `staff_case_issuer_tenant_id = "amh"` em
     `deploy/aws-ecs/envs/dev-sa-east-1`, **tarefa T6.2** (Onda 6, a task do emissor ao lado de
     `portal.staff`).
  6. **Rotação da chave `case_issuer` com `policy_ref` novo: aceita.**
     - Formato determinístico: `policy_ref = "staff-escalation-routing@d{designation_revision}"`.
     - A cada designação nova (ciclo N2, 14 d), o emissor publica o head novo, reemite grants e
       checkpoint e revoga a policy anterior, tudo **na mesma rodada**.
     - A janela sem casos é de uma rodada do emissor. Ela vira passo do runbook da Onda 5: rodar o
       emissor logo depois de instalar a designação.

     **Rejeitado:** manter a chave entre designações, porque a chave viveria além do ciclo que o
     aprovador assina.
     **Tarefa:** T1.6 (código) e Onda 5 (runbook).
- **D-I [23/09, achado desta consulta]. `public.ACT_*` fixo no Java quebra D-C2 em tempo de
  execução.** São 9 ocorrências no `origin/main` depois da T1.8: `NativeCaseIdentityReader`,
  `ExternalCaseStore`, `ExternalCaseReadCommand`, `ExternalCasePublicationCommand`,
  `StaffCaseStore:260` e `AssignmentReceiptAuthority`. Com os `ACT_*` em `cibseven`, a leitura
  staff falha, e a S1 R5 só provou o SQL de pin.
  - **Decisão:** um pin novo `engine_schema` (`cibseven`), que entra em
    `StaffCaseInstallation.Configuration` e no `digest()`, e no manifesto **v2 sem bump**, porque
    nenhum pacote v2 foi assinado. Depois da primeira assinatura, qualquer mudança exige v3. O SQL
    usa o identificador validado e quotado.
  - `maezo_external.*` e `portal_identity` continuam como schemas próprios do DDL canônico
    (`external-case-schema-postgres.sql:4`, `:641`). São exceção nomeada ao "todas as `mzo_*` em
    `maezo_native`" e ficam fora do path.
  - **Tarefas:** T1.8b (Java e Python). A T1.4 tem que instalar `maezo_external`, com USAGE e SELECT
    para `cibseven_app`. A C1 passa a depender da T1.8b.
- **D-J [23/09, consulta T1.1 / PR #492 e T1.4 / PR #493].**
  1. **Composição (T1.1): confirmada** — `staff-deployment-composition.v1` em JCS, campos fixos,
     caminho por `MAEZO_STAFF_COMPOSITION_FILE`, chaves/senha em arquivos irmãos referenciados por
     nome, log só do digest. Campo desconhecido ou arquivo irmão ausente = recusa no boot.
     **Requisito da Onda 4:** composição e segredos montados read-only, modo `0400`, dono diferente
     do uid do processo (1000) e sem diretório gravável por ele; o boot recusa se o arquivo for
     gravável pelo processo. Muda em: runbook da Onda 4 + teste em T1.2.
  2. **T1.4:**
     a. DDL AUTH e `human-consumer-lineage` são instalados **pelo engine no boot**
        (`AuthInstallation`/`ConsumerEdgeInstallation` continuam donos e continuam recusando tabela
        pré-existente). O script da T1.4 só cria o schema e dá a `cibseven_app` `CREATE`+`USAGE`
        nele; depois do primeiro boot, a Onda 3 revoga `CREATE`. Tarefa: T1.4 (script) + Onda 3/4.
     b. DDL externo apontando para `public.*`: requalificar para `maezo_external.*`. Tarefa: T1.4
        (mesmo PR); dono do DDL canônico é o builder da T1.4.
     c. DML de `cibseven_app`: `MZO_HUMAN_*` = SELECT, INSERT, UPDATE, sem DELETE, exceto tabelas de
        ledger/recibo (sufixo imutável, I8) = SELECT, INSERT apenas; `MZO_PORTAL_READ_*` = SELECT,
        INSERT; `MZO_PORTAL_READ_PUBLICATION_RECEIPT` = SELECT, INSERT. Nenhum TRUNCATE/REFERENCES.
        Vai no `engine-native-install.sql` com teste PG 17 negativo (DELETE recusado). Tarefa: T1.4.
     d. Ledger do emissor: colunas propostas pelo builder **confirmadas como piso** no DDL da T1.4;
        a T1.6 só acrescenta por expand (coluna nullable/default), nunca renomeia.
     e. `CREATE SCHEMA … AUTHORIZATION <dono>` no RDS: a role admin precisa ser membro do dono com
        SET (PG16+: `GRANT <dono> TO <admin> WITH SET TRUE`; ADMIN implícito do criador da role).
        Passo explícito no runbook da Onda 3, antes do script; revogar a membership ao final.
  3. `ExternalCaseReadCommand` lê `MZO_PORTAL_READ_PUBLICATION_RECEIPT` sem schema: qualificar com o
     pin `maezo_native` validado e quotado, igual a D-I. Tarefa: **T1.8b**; teste com `search_path`
     vazio.
  4. **[23/09, revisão após PR #495] Correções da D-J 2.a/2.b e 2.c:**
     - **2.a substituída:** o engine **não** cria tabela no boot, porque o dono seria o runtime
       (contra ADR-0060 D1) e `ConsumerEdgeInstallation.session` exige que a sessão seja do dono.
       O script da T1.4 instala o DDL AUTH e `human-consumer-lineage` **como dono**. Os instaladores
       passam a aceitar tabela pré-existente **somente se ela for idêntica**: comparam o digest do
       catálogo (colunas, tipos, constraints, dono e grants) com o pin, e recusam se divergir.
       `cibseven_app` continua sem CREATE. A ideia de Job "install" do engine rodando como dono foi
       rejeitada, porque põe a credencial do dono na imagem do runtime. Tarefa: T1.4 (script e
       instaladores Java, com teste de divergência recusada).
     - **2.b ajustada:** `mzo_human_tenant` e `mzo_portal_read_revocation` ficam em `maezo_native`
       (confirmado); `maezo_external` guarda só o DDL externo de casos. Os `CREATE ROLE
       portal_external_*` saem do DDL e vão para `deploy/sql/engine-native-roles.sql` (já existe na main), que roda pela role admin
       (com CREATEROLE) no início da Onda 3. Os roles são NOLOGIN e sem membership com o dono; os
       GRANTs sobre objetos continuam no DDL, executados pelo dono. Tarefa: T1.4.
     - **2.c, exceção ratificada:** `cibseven_app` tem UPDATE em `MZO_PORTAL_READ_DESIGNATION`
       (a revogação faz UPDATE em `PortalReadPublication.java:171`). O UPDATE é por coluna, só nas
       colunas de revogação, com teste negativo de UPDATE em outra coluna.

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

- **[Onda 0] FECHADA em 23/09/2026.** Resultados em §7. D-C refutada e substituída por D-C2.
  S1 não usou `StaffNativeClient` em si: ele exige signer e `InstalledStaffAuthority`, que são
  materiais da T1.3. Usou a mesma forma de TLS do cliente
  (`ssl.create_default_context(cafile)` + `load_cert_chain`, `publisher.py:173-179`). O handshake
  com o próprio `StaffNativeClient` fica para quando a T1.3 existir.
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
| T1.1 | backend/Java | `src/maezo/portal/engine/java/.../human/StaffDeploymentComposition.java` (novo) + teste; `deploy/cibseven/human-webapp/` intacto | `mvn -B package` verde; teste que prova: sem arquivo → engine não sobe; arquivo adulterado → recusa; log emite só o digest público; digest igual a `Configuration.digest()`. **[23/09]** O arquivo de composição traz `native_schema`, que vai para o `Configuration.nativeSchema` da T1.8. A T1.1 começa já contra essa assinatura, mas **só mergeia depois da T1.8/Java**. Ela não edita `StaffCaseInstallation.java` |
| T1.2 | infra | `deploy/cibseven/Dockerfile.human` (ARG da composição, `server.xml` de deploy com **um** connector `SSLEnabled` 8443 `certificateVerification="required"`, `allowTrace=false`, sem atributos de proxy, keystore/truststore **montados**); `deploy/cibseven/native-deploy/server.xml` (novo) | build local verde; `S1` reexecutado sobre esta imagem |
| T1.3 | backend/Python (ferramenta, fora do runtime) | `tools/staff_materials/` (novo): `generate` (chaves Ed25519 read/witness/result, CA nativa, cert do servidor com SAN = hostname de D-B, cert do cliente, rascunho da designação **sem assinatura**), `sign-designation` (roda **na máquina do aprovador**, com a raiz dele), `assemble` (monta o bundle), `verify` (chama `decode_bundle` com os pins lidos de um arquivo **fornecido pelo aprovador**) | testes unitários com fixture sintética; `verify` recusa bundle com 1 byte trocado, pin trocado, designação sem assinatura da raiz |
| T1.4 | backend/Python + SQL | `deploy/sql/portal-identity-lock.sql.tmpl` (novo, variante por tenant, D-D) + `deploy/sql/engine-native-install.sql` (ordem dos resources `mzo_*` + login witness SELECT-only + grants) + teste que compara o template com o bloco canônico (`external-case-schema-postgres.sql:636-672`), com diferença **só** no schema | teste verde; diff revisado |
| T1.5 | backend/Python | `src/maezo/gateway/human/membership_publication_job.py` (novo `__main__`): publica `portal_memberships` → engine (`PortalReadPublication`) e principal → `/v1/authority`, idempotente por revisão | teste de integração contra o harness; 2ª execução não muda nada (CAS) |
| | | **[23/09] Escopo ampliado.** `/cases` também precisa do **catálogo Q2 designado**: o `Q2Intersection` lê `read.catalog(anchor)` em toda operação staff (`StaffCaseReadCommand.java:307`), e sem designação `PortalReadStore.catalog` recusa (`PortalReadStore.java:103-104`). Ninguém publica catálogo hoje. `PortalReadPublisher` exige as cinco fontes no construtor (`read_publisher.py:167-176`), e `MembershipPublicationHandshake` e `DeploymentCatalogPublicationSource` são abstratas, sem implementação em `src/`. Por isso a T1.5 entrega: (i) o handshake de membership sobre `amh.portal_memberships`; (ii) uma fonte de catálogo **mínima** para o staff (`entries=[]`, `policies=[]`, `forms=[]`, digest igual ao admitido na T1.7); (iii) `resource`, `pagto` e `revocation` como fontes que **sempre recusam** (fail-closed, Onda 8); (iv) o `__main__` que publica o catálogo uma vez e as memberships por revisão | além do acima: catálogo publicado uma vez e **re-publicação idempotente** (receipt igual, `PortalReadPublication.java:66-79`). O "pronto" depende da **T1.7b**, porque sem provedor o engine não sobe com `PortalReadPlugin`. **NÃO VERIFICADO** que `entries=[]` passa: o shape aceita lista vazia (`PortalReadModels.java:324-331`) e `verifyCatalog` só itera, mas nunca foi executado. Se não passar, o catálogo leva uma entrada real do SP-OP-ESCALATION-001 e a T1.5 cresce 1-2 d |
| T1.6 | backend/Python | fonte de publicação de casos staff (`case_issuer`): lê as escalações vivas e publica `staff_case_grant` / `staff_policy_head` / `scope_complete`. ~~**Bloqueada pela decisão de negócio N3** (§6): qual grupo vê qual caso~~ **[23/09] N3 decidida:** o caso só é visível ao grupo que a DMN `escalation_routing` escolheu para aquela escalação. **Restrição da Onda 7:** o grant **não** inclui a projeção `staff_current_task.v1`. Com ela, `collectDetail` exige publicação Q2 de cada tarefa (`StaffCaseReadCommand.java:117-124`), e publicar tarefas é Onda 8. `/cases/{ref}` responde sem a lista de tarefas correntes | teste de integração; grant só para o grupo que a `escalation_routing` escolheu; negativo: outro grupo não recebe grant; nenhum grant com `staff_current_task.v1` |
| T1.7 **[Onda 0]** | backend/Java (pacote revisado à parte) | **Provedor Q2 do engine:** uma implementação `PortalReadTrust.Providers` com registro `META-INF/services/br.com.maezo.human.PortalReadTrust$Providers` na imagem. Ela faz admissão viva por escopo, encarnação, digests e propósito, com validade, revogação e timeout, e traz custódia das chaves de continuidade nativas e qualificação das publicações (catálogo, membership, principal). Os requisitos estão em `LIFECYCLE.md:128-143` ("never copy the anonymous Java test provider"). Sem ela o `PortalReadPlugin` não sobe (I6) e nenhuma rota staff responde | engine `INSTALL_PORTAL_READ=true` sobe no harness com trust real, e `staff-case-list` passa do `staffLease`; teste negativo com provedor ausente ou duplicado → boot recusado |
| **[23/09] A T1.7 se divide em duas**, com desenho em §3.1: | | | |
| T1.7a | backend/Java (módulo novo, revisão à parte) | `src/maezo/portal/engine/read-provider/` (novo módulo Maven: `pom.xml`, `InstalledReadProviders.java`, `AdmissionRecord.java`, `NativeContinuityKeys.java`, registro `META-INF/services/br.com.maezo.human.PortalReadTrust$Providers`, DDL `portal-read-admission-postgres.sql`) + testes. Na última etapa, o `COPY` do JAR no ramo `true` de `deploy/cibseven/Dockerfile.human`, **depois** que a T1.2 mergear | **admissão e chaves:** `acquire`, `requireCurrent`, `continuity`. O engine com `INSTALL_PORTAL_READ=true` **sobe** no harness com o provedor real, e um IT Java chama `PortalReadPlugin.staffLease` e recebe um `Q2Lease` com o `Admission` do provedor. Pela rota HTTP, o staff ainda devolve `503 HUMAN_ENGINE_UNAVAILABLE`, porque `executeStaff` chama `staffConfigured()` antes do lease (`HumanCommandPlugin.java:203`) e a composição é a T1.1. Os negativos da §3.1 passam |
| T1.7b | backend/Java (mesmo módulo, depois da T1.7a) | `MembershipSourceObserver.java`, `StaffScopeQualification.java` + testes; vetor de paridade JCS compartilhado com o Python em `tests/fixtures/portal_read/jcs-membership-vector.json` (novo) | **qualificação das publicações** do escopo staff: `catalog-designate`, `catalog-revoke`, `membership` e `revoke-key` qualificam; `resource`, `verifyIdentityPolicy` e `verifyClassification` recusam sempre. IT: uma publicação de membership com o payload igual à linha viva de `amh.portal_memberships` gera receipt; o mesmo payload com um campo trocado é recusado; o vetor JCS dá o mesmo digest em Java e em Python |
| **C1 [23/09]** | checkpoint de integração (thread principal + builder Java) | nenhum arquivo novo: é a S1 reexecutada sobre a imagem da T1.2 com T1.1, T1.4, T1.5, T1.7a/b e T1.8 mergeadas | no harness, com o layout D-C2: `staff-case-list` pela rota mTLS, com um principal e o catálogo publicados, sai do `staffLease` e do `catalog` e para no `checkpoint` (`unavailable`), porque ainda não há grant da T1.6. Com a T1.6: 200 com um caso. Estimativa: 1 d |
| T1.8 **[Onda 0]** | backend/Java + Python (com ADR) | **Schema nativo pinado (D-C2):** `StaffCaseStore.java:44` e o witness (`postgres.py:241`, `:266`) deixam o `'public'` fixo e passam a comparar com o schema pinado; o manifesto sobe para `portal-staff-material.v2` com o campo novo; a validação de settings e o Terraform aprendem esse campo. **[23/09]** Arquivos: `StaffCaseInstallation.java` (`nativeSchema` no `Configuration` e no `digest()`), `StaffCaseStore.java` (pin com `?`, `current_schema()`, `to_regclass` e `_chunk` imutável, D6), `production_config.py`, `materials.py`, `postgres.py`, `portal-variables.tf`, `service-portal.tf`, `tests/unit/gateway/test_staff_production_materials.py` e o parágrafo de `src/maezo/portal/engine/README.md:238-239`. A ordem interna é Java primeiro (a T1.1 espera), depois Python e Terraform | testes: o pin recusa um schema diferente do pinado e um `public` homônimo. ~~ADR registrado~~ **ADR-0060 registrado (23/09)**. Negativos novos: um homônimo em `pg_temp` recusado pelo `to_regclass`; uma v1 recusada no load; `native_schema` fora do regex recusado; `checkpoint_chunk` com `SELECT,INSERT` aceito e com UPDATE recusado |
| T1.8b **[23/09, D-I]** | backend/Java + Python | Java com `public.ACT_*` (9 ocorrências, D-I); `StaffCaseInstallation.java` (`engineSchema`); `materials.py` e `production_config.py` (`engine_schema` no v2) | IT no layout D-C2: `staff-case-list` e `detail` leem `ACT_*` em `cibseven`; `engine_schema` divergente é recusado; um teste de grep garante nenhum `public.ACT_` em `src/main`. 2-3 d |
| T1.10 **[23/09, D-H.4]** | backend/Python | `src/maezo/gateway/staff_cases/materials.py:158` + `tests/unit/gateway/test_staff_production_materials.py` | exige `("detail","list")`; `["detail"]` e `["list"]` são recusados. 0,5 d |
| T1.9 **[23/09]** | infra/CI | `tests/unit/deploy/test_engine_rest_open_only_dev.py` (novo) + `tests/unit/deploy/fixtures/engine_rest_fence/` (novo) | **cerca da N5.** Pelo digest não dá para saber se a imagem é aberta ou `secured*`, então a cerca é declarativa. O teste lê o HCL de cada diretório em `deploy/aws-ecs/envs/*` com o `_hcl_probe.py` que já existe. Todo ambiente cujo nome **não** comece com `dev-` tem que declarar `engine_rest_authentication = "client-certificate"`; ausência ou outro valor reprova. `dev-*` pode omitir. Hoje só existe `dev-sa-east-1`, e o teste passa. **Controle negativo:** um fixture `staging-x` sem a declaração reprova, e o mesmo fixture com a declaração passa. A variável do módulo nasce quando o primeiro ambiente não-dev nascer; até lá, a cerca é o que impede copiar o dev. Estimativa: 0,5-1 d |

**[Onda 0] Acréscimos às tarefas existentes:**
- **T1.4:** tem que corrigir o DDL canônico staff **antes** de gerar qualquer script. São as três
  FKs de I7 (lista explícita das colunas da UNIQUE) e a matriz de `checkpoint_chunk` de I8, junto
  com o backend. Precisa de um teste que **execute** o DDL num PostgreSQL 17 e releia a matriz de
  grants contra `StaffCaseStore`, porque busca de string não pega esse tipo de erro. O
  `engine-native-install.sql` instala no schema de D-C2 e **inclui o DDL AUTH**
  (`human-auth-intake-documents-postgres.sql`) e `portal-read-schema-postgres.sql`, que o plano
  não listava.
- **T1.2:** o `DB_URL` alvo é `currentSchema=maezo_native,cibseven` (D-C2), não
  `cibseven,public`. A imagem da Onda 4 só pode ter `INSTALL_PORTAL_READ=true` depois da T1.7.
- **T1.3:** os certificados gerados devem trazer `AuthorityKeyIdentifier` e
  `SubjectKeyIdentifier`. O harness atual não traz, e um cliente Python ≥ 3.13
  (`VERIFY_X509_STRICT` ligado por padrão) recusa o servidor com `Missing Authority Key
  Identifier` (S1, Python 3.14 local). O portal roda `python:3.12-slim` (`deploy/Dockerfile:85`),
  então isso não quebra hoje, mas quebraria na próxima troca de Python.

**[23/09] Acréscimos do desenho (ADR-0060 e §3.1):**
- **T1.3:**
  - monta o manifesto **`portal-staff-material.v2`**, com `native_schema`. O modelo v2 é da T1.8/Python.
    Até ele mergear, o `assemble` fica atrás de um teste marcado como pendente, e o
    `generate`/`sign-designation` não esperam;
  - ganha `sign-admission`, que roda na máquina do aprovador e assina o `portal-read-admission.v1` da
    T1.7 com a mesma raiz de D-F, em domínio separado (§3.1);
  - gera a chave de continuidade nativa (32 bytes) e o compromisso dela;
  - soma **+1 d** à estimativa.
- **T1.4:**
  - o script instala com o login `maezo_native_schema_owner`, em `maezo_native`, e inclui o DDL de
    admissão da T1.7a (`portal-read-admission-postgres.sql`);
  - cria o login observador **`portal_read_source_amh`**, com SELECT só nas colunas de
    `amh.portal_memberships` que a T1.7b lê. O grant vem do dono de `amh` (`maezo_app`/migration),
    não do dono nativo;
  - o teste executável (PG 17) confere que o `cibseven_app` não tem INSERT, UPDATE nem DELETE
    na tabela de admissão, e que `maezo_native` não tem nenhuma relação `act_%`.
- **T1.2:** o `DB_URL` alvo fixa `sslmode=verify-full`. A T1.2 é **dona** de `Dockerfile.human`:
  a T1.7a só acrescenta o estágio de build do módulo novo e o `COPY` no ramo `true`, **depois**
  do merge da T1.2.

- **Onde roda:** repositório, CI e local. Aviso: o CI está bloqueado por billing do Actions desde
  22/09, então o "verde" é local até o billing voltar.
- ~~**Pré-requisito:** Onda 0 fechada (T1.2, T1.4 e T1.8 dependem de D-C2, que exige ADR); T1.6
  depende de N3; T1.7 não depende de decisão de negócio, mas é o maior item e o menos conhecido.~~
  Substituído pelo pré-requisito [23/09] logo abaixo.
- **Reversão:** revert do PR (nada foi implantado).
- **Estimativa:** T1.1 3-4 d · T1.2 2 d · T1.3 3 d · T1.4 1-2 d · T1.5 3-4 d · T1.6 3-5 d. Em
  paralelo: **~1,5 a 2 semanas** de calendário.
  **[Onda 0]** Somam-se a T1.8 (~1 semana, o custo que D-C já previa para o substituto), mais
  1-2 d na T1.4 para as correções de DDL e o teste executável, e a **T1.7, NÃO ESTIMADA**. A T1.7
  é o "next package" Q2 de `LIFECYCLE.md`: admissão viva, revogação e custódia de chaves. É do
  mesmo porte das partes da Onda 8 que o plano estimava em semanas, com confiança baixa.
  **[23/09] Estimativa refeita (d-pessoa):**

  | Tarefa | Estimativa | Observação |
  |---|---|---|
  | T1.1 | 3-4 d | |
  | T1.2 | 2 d | |
  | T1.3 | 4 d | +1 pelo v2 e pela admissão |
  | T1.4 | 2-4 d | |
  | T1.5 | 5-7 d | escopo ampliado |
  | T1.6 | 3-5 d | |
  | T1.7a | 5-6 d | |
  | T1.7b | 4-6 d | |
  | T1.8 | 5 d | |
  | T1.9 | 0,5-1 d | |
  | C1 | 1 d | checkpoint de integração |
  | **Total** | **~35-46 d-pessoa** | |

  **Calendário: 2,5 a 3 semanas**, com cinco frentes (dois builders Java, dois Python, um de infra).
  Com três frentes, 4 a 5 semanas. O caminho crítico é T1.7a → T1.7b → fim da T1.5 → C1
  (~12-15 d úteis). Ver §3.2.
- **Pré-requisito [23/09]:** D-C2 está decidida (ADR-0060) e N3 também (§6). Nada na Onda 1 espera
  decisão de negócio. As dependências entre tarefas estão em §3.2.

### 3.1 Desenho da T1.7 — o provedor Q2 recortado ao escopo staff **[23/09]**

**O que o `PortalReadPlugin` exige** (leitura de `PortalReadPlugin.java:18-39`,
`PortalReadTrust.java:33-68`, `:178-195`):
- **Registro:** exatamente um `PortalReadTrust.Providers` pelo `ServiceLoader`, mais o arquivo
  `MAEZO_PORTAL_READ_TRUST_FILE`.
- **No boot:**
  - `acquire("portal-task-read")` devolve um `Admission` com `generation`, `providerRef`,
    `providerRevision`, `capabilityDigest`, `observedAt ≤ agora < validUntil` e
    `statementTimeoutSeconds ≥ 1`;
  - `continuity(admission)` devolve um `NativeKeySet` com uma chave HMAC-SHA256 de 32 bytes
    vigente;
  - em `postProcessEngineBuild`, um novo `acquire` e o lock do tenant.
- **Em toda operação staff** (`PortalReadPlugin.java:76-88`): `acquire` **fora** de comando do
  engine e antes de qualquer lock (`:78`). Dentro do comando só se chamam `requireCurrent`,
  `verifySource` e `verifyCatalog`, que são síncronos, locais e sem I/O (`PortalReadTrust.java:26-31`).
- **O que o staff realmente usa:**
  - `verifySource` (membership e catálogo);
  - `verifyCatalog`;
  - `qualifyPublication` + `verify` (publicações `membership` e `catalog-designate`, da T1.5).

  `verifyIdentityPolicy` e `verifyClassification` só rodam em leitura e publicação de **tarefa**
  (`PortalReadCommand.java:367`, `:405`; `PortalReadPublication.java:240`), e isso é Onda 8. O
  provedor **recusa** as duas, e recusa a publicação `resource`.

**Onde mora.** Um módulo Maven novo, `src/maezo/portal/engine/read-provider/`, gera
`maezo-portal-read-provider.jar`. Ele depende do JAR do engine como `provided`, e o pacote é
`br.com.maezo.human.readprovider`. Só usa tipos públicos: `Providers`, `Admission`,
`NativeKeySet`, `NativeKey` e `PublicationQualification`, que são públicos em `PortalReadTrust`, e
`Rejected`, também público. Por que um módulo à parte:
- o README do engine diz que o registro pertence a "that separately qualified provider package"
  (`src/maezo/portal/engine/README.md:231-233`);
- o JAR só entra na imagem `INSTALL_PORTAL_READ=true`. A imagem `false` continua sem provedor.

**Configuração** (`MAEZO_PORTAL_READ_PROVIDER_FILE`, `portal-read-provider.v1`, JCS de chaves
fechadas, montada read-only pelo segredo nativo da Onda 4):
- a SPKI pública da **raiz de instalação do aprovador** (a mesma de D-F) e o pin SHA-256 dela;
- `admission_ref` e `minimum_admission_revision`;
- o nome JNDI do DataSource, que é o mesmo do descritor (`java:jdbc/ProcessEngine`,
  `deploy/cibseven/secured/descriptors/bpm-platform.xml:11`);
- o schema pinado (`maezo_native`) e o pin `oid`/`owner` da tabela de admissão;
- o caminho do arquivo de chaves de continuidade;
- a fonte de membership: arquivo do DSN, CA, schema `amh` e `publisher_ref`.

**Registro de admissão** (`portal-read-admission.v1`):
- **Onde fica:** uma linha em `maezo_native.mzo_portal_read_admission`, DDL novo da T1.7a. Quem
  grava é **só** `maezo_native_schema_owner`, na Onda 3, e o `cibseven_app` tem SELECT.
- **O que carrega:**
  - `scope`, `engine_name`, `database_incarnation`, `read_deployment_ref` e `read_deployment_digest`;
  - `trust_configuration_digest`: o SHA-256 do trust file parseado, que o plugin passa em
    `acquire` como `configurationDigest`;
  - os `purposes` admitidos;
  - `code_digests`: SHA-256 dos JARs **carregados** do engine e do provedor, lidos pelo
    `CodeSource`, no mesmo padrão de `AuthRuntime.java:49`;
  - os compromissos das chaves de continuidade;
  - o catálogo admitido: `catalog_ref`, `publisher_ref` e `catalog_digest`;
  - os publicadores admitidos por kind, com prefixo de `source_ref`;
  - `statement_timeout_seconds` (1 a 10) e `observation_seconds` (60 a 900; proposta 300);
  - `not_before`, `valid_until` (no máximo 14 dias, N2) e `admission_revision`.
- **Assinatura:** Ed25519 da raiz sobre `"maezo/portal-read-admission/v1\0" ‖ JCS(registro)`. É o
  mesmo aprovador da designação staff, em outro domínio de assinatura. A engenharia não consegue se
  auto-admitir, como em D-F.

**Como cada método se comporta (tudo fail-closed: exceção vira `503`, `PortalReadServlet.java:50`,
`HumanServlet.java:76`):**

| Método | Faz | Recusa quando |
|---|---|---|
| `acquire` | Lê de novo a linha de admissão. Usa uma conexão do DataSource JNDI, **fora** de comando do engine, com transação read-only e `SET LOCAL statement_timeout`, e qualifica o nome pelo schema pinado. Confere o pin da tabela (OID, dono, e o `session_user` sem INSERT, UPDATE nem DELETE), a assinatura, os campos contra os parâmetros e o propósito, a janela, `revoked=false`, `revision ≥ max(minimum, maior revisão já vista)` e os `code_digests` (calculados uma vez no boot). Devolve `generation=providerRevision=revision`, `capabilityDigest=SHA-256(JCS(registro))`, `observedAt=agora` e `validUntil=min(valid_until, agora+observation_seconds)` | linha ausente, assinatura ou pin inválido, qualquer campo divergente, propósito fora da lista, revogada, revisão menor que a já vista (anti-rollback em memória), JAR diferente, ou timeout |
| `Admission.requireCurrent` | síncrono: `agora < validUntil` e a geração não foi superada nem revogada por um `acquire` posterior | qualquer uma das duas condições. **Latência de revogação:** a próxima operação staff, porque o staff faz `acquire` por requisição. Para uma admissão já retida, no máximo `observation_seconds` |
| `verifySource` | `publisher_ref` igual ao admitido para o kind e `source_ref` com o prefixo admitido; `observed_at ≤ agora < valid_until` | kind fora de {`membership`, `catalog-designate`} |
| `verifyCatalog` | `SHA-256(JCS(artifact))` igual ao `catalog_digest` admitido | qualquer outro catálogo |
| `verifyIdentityPolicy`, `verifyClassification` | nada | **sempre** (Onda 8) |
| `continuity` | Carrega, uma vez no boot, o arquivo `portal-read-continuity-keys.v1`: dono do processo, modo 0400 e chave de 32 bytes. Exige `HMAC(chave, "maezo/portal-native-read-continuity/v1/commitment")` igual ao compromisso admitido. `current()` é a chave admitida vigente mais nova, e `verification(id)` só devolve chave admitida. `NativeKey.digest` é o compromisso, nunca o hash da chave | arquivo ausente ou com modo errado, compromisso fora da admissão, nenhuma chave vigente |
| `qualifyPublication` | **Fora** do comando do engine: `catalog-designate` exige `catalog_digest`, `catalog_ref` e `publisher_ref` iguais aos admitidos. `membership` lê a **linha viva** de `amh.portal_memberships` pelo login `portal_read_source_amh` (TLS `verify-full`, read-only, timeout), com o mesmo SQL de `src/maezo/portal/api/postgres.py:142`, e monta a projeção em Java igual a `read_publisher.py:111-121`. `catalog-revoke` e `revoke-key` passam, porque só reduzem autoridade e o envelope já foi verificado pela chave de publicação. O `verify` compara em memória: payload igual à projeção montada, `source_revision` igual à revisão da linha e `source_digest` igual a `SHA-256(JCS(payload))` | `resource`; linha ausente; qualquer divergência; snapshot mais velho que `observation_seconds` |

**Contrato com a T1.5** (fixado aqui para as duas correrem em paralelo):
- no handshake de membership, `source_digest := SHA-256(JCS(MembershipProjection))`;
- `receipt_ref := "portal-identity:amh:membership:" + principal_ref + "@" + revision`;
- `valid_until := observed_at + observation_seconds`;
- o vetor `tests/fixtures/portal_read/jcs-membership-vector.json` é a prova de paridade, lida pelos
  testes Java e Python.

**Testes que provam** (T1.7a e T1.7b):
- **Boot negativo** (harness, PG 17):
  - provedor ausente (a imagem sem o JAR) e provedor duplicado (dois registros) dão
    `READ_DEPENDENCY_UNAVAILABLE`;
  - também dão o mesmo resultado: arquivo de config ausente, assinatura de outra raiz, admissão
    revogada, admissão vencida, `trust_configuration_digest` trocado e `code_digests` de outro JAR.
- **Boot positivo:** engine `INSTALL_PORTAL_READ=true` sobe no layout D-C2, e o `staffLease` devolve
  um lease.
- **Revogação:** `UPDATE … SET revoked=true` pelo dono faz a **próxima** operação devolver 503. O
  teste mede também que uma admissão retida expira em `observation_seconds`.
- **Anti-rollback:** reinstalar uma revisão anterior válida com o engine vivo é recusado.
- **Sem escrita:** o `cibseven_app` não consegue INSERT na tabela de admissão (grant), e o provedor
  recusa se conseguir (pin).
- **Publicação:** membership igual à linha viva gera receipt; um campo trocado, ou a linha alterada
  entre o `qualifyPublication` e o `verify`, é recusado; `resource` é recusado.
- **Nada vaza:** o log não traz chave, DSN nem assinatura. É o mesmo grep do §1.7 #7.

**Porte.** A T1.7 **não cabe numa tarefa só**. São **9 a 12 d-pessoa** num builder Java,
confiança média, e ela vira duas tarefas sequenciais no mesmo módulo:
- **T1.7a** (admissão, chaves e boot, 5-6 d) já destrava o boot da imagem `INSTALL_PORTAL_READ=true`;
- **T1.7b** (qualificação das publicações, 4-6 d) destrava a T1.5.

O que pode estourar a faixa:
- o `ServiceLoader` pelo classloader do Tomcat (`/camunda/lib`) é **NÃO VERIFICADO** e o boot
  positivo da T1.7a o prova;
- a paridade JCS Java × Python;
- um catálogo com `entries=[]` (NÃO VERIFICADO, T1.5).

**O que a T1.7 não é:** não é a autoridade Q2 completa de `LIFECYCLE.md:128-143`. Continuam na
Onda 8 a publicação de tarefas (`resource`), a classificação, a política de identidade, os grants
de READ_HISTORY e a leitura de tarefas.

### 3.2 Grafo de dependências da Onda 1 **[23/09]**

Uma aresta `A → B` quer dizer que B não fica **pronta** (merge) sem A. "Começa já" quer dizer que
não há aresta de entrada que impeça **começar a codar** hoje, sem esperar a D-C2 codificada, que é a
T1.8.

```
                 ┌──────────── T1.8/Java ──► T1.1 (merge) ──────────────┐
ADR-0060 ──► T1.8 ┤                                                       │
                 └── T1.8/Python ──► T1.3 `assemble` v2                   │
T1.2 ──► T1.7a (COPY no Dockerfile.human)                                  ├──► C1 ──► Onda 2
T1.7a ──► T1.7b ──► T1.5 (pronto: IT contra o harness) ─────────────────┤
T1.4 (DDL corrigido + admissão) ──► T1.5, T1.7a (IT com o DDL real) ──────┤
T1.6 ──────────────────────────────────────────────────────────────────┘   (C1 só dá 200 com a T1.6)
T1.9 (independente; não entra em C1)
```

| Tarefa | Começa já? | Espera para **mergear** | Frente |
|---|---|---|---|
| T1.8 | **sim**: é a própria D-C2, e o ADR-0060 existe | nada | Java #1, depois Python #1 |
| T1.7a | **sim**: o DDL de admissão é dela e não depende do schema | T1.2 (só o `COPY`); T1.4 para o IT com o DDL real | Java #2 |
| T1.2 | **sim**: `server.xml`/connector não dependem do schema, e o `DB_URL` é env | nada | infra |
| T1.4 | **sim**: nomes fixados no ADR-0060; correções I7/I8 independentes | nada (I8: o DDL fica como está, a mudança é da T1.8) | infra |
| T1.9 | **sim** | nada | infra |
| T1.3 | **sim** (`generate`, `sign-designation`, `sign-admission`, `verify`) | `assemble` v2 espera a T1.8/Python | Python #2 |
| T1.6 | **sim**: N3 decidida | nada para o código. A aceitação é na C1 | Python #2, depois da T1.3 |
| T1.1 | **sim**, contra a assinatura `Configuration.nativeSchema` fixada aqui | **T1.8/Java** | Java #1, depois da T1.8/Java |
| T1.5 | **sim**, código e unitários com o contrato de §3.1 | **T1.7b** e T1.4 para o IT | Python #1 |
| T1.7b | **não**: precisa do `Admission` da T1.7a | T1.7a | Java #2 |
| C1 | não | T1.1, T1.2, T1.4, T1.5, T1.7a/b, T1.8 (e T1.6 para o 200) | thread principal |

**Disjunção de arquivos, conferida pelos caminhos:**
- a T1.8 é a única que edita `StaffCaseInstallation.java`, `StaffCaseStore.java`,
  `production_config.py`, `materials.py`, `postgres.py` e os `.tf` do portal;
- a T1.1 só cria `StaffDeploymentComposition.java`;
- a T1.7 só toca o módulo novo `read-provider/` e, no fim, o `Dockerfile.human`, **depois** da
  T1.2, que é a dona do arquivo;
- a T1.4 fica em `deploy/sql/` e nos DDL canônicos de `src/main/resources/` (I7). A T1.8 não toca
  DDL;
- a T1.5 e a T1.6 são módulos Python novos. `read_publisher.py` **não** é editado: a T1.5
  implementa as ABCs em arquivo próprio;
- a T1.3 fica em `tools/`, e a T1.9 em `tests/unit/deploy/`.

**Interseções de arquivo:**
- `tests/fixtures/portal_read/jcs-membership-vector.json`: a T1.7b cria e a T1.5 só lê;
- `Dockerfile.human`: T1.2 primeiro, T1.7a depois.

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
- **[23/09] Acréscimos (T1.7):**
  - **Imagem candidata.** Construir e assinar a imagem candidata de `Dockerfile.human` (T1.2 + T1.7a)
    **nesta onda**, e não na Onda 4. A admissão Q2 amarra os `code_digests` dos JARs e o
    `read_deployment_digest`, então a imagem tem que existir antes da assinatura. Com o CI parado
    por billing, o build é local, e a assinatura `supply-chain.yml` fica pendente até o billing
    voltar. É **bloqueio da Onda 4**, não desta.
  - **Chave de continuidade.** Gerada pelo `generate` (T1.3); só o compromisso sai do diretório 0700.
  - **Admissão Q2 pelo aprovador.** Ele confere cada campo do `portal-read-admission.v1` contra as
    fontes da §4 (linhas novas) e roda `sign-admission` na máquina dele.
  - **Login observador.** DSN de `portal_read_source_amh`, pela mesma técnica SCRAM.
  - **Estimativa:** +0,5 d de engenharia e +0,5 d do aprovador.

### Onda 3 — instalação no banco (dono do banco, não o portal)

- **Artefatos (banco de identidade):** schema `portal_identity`, roles `NOLOGIN`, tabela
  `external_login_tenant`, função `lock_external_session` (variante `amh`, D-D), login
  `portal_staff_lock_amh` (não-owner, sem super/bypassrls/createrole/createdb/replication,
  `EXECUTE` só na função) e linha `('portal_staff_lock_amh','amh')` em `external_login_tenant`.
- **Artefatos (banco do engine):** resources `mzo_*` em `public` (ordem da T1.4), login witness
  `portal_staff_witness_amh` **SELECT-only** em `public.mzo_portal_read_membership` e
  `public.mzo_human_principal`, sem RLS nessas duas (o witness exige `relrowsecurity=false`,
  `postgres.py:254`), e a linha de designação instalada.
  **[Onda 0] Com D-C2, troque `public` pelo schema nativo** e mude três coisas:
  - **Banco:** o banco do engine **é o mesmo** banco de identidade. `maezo` recebe `cibseven_app`,
    `maezo_app` e `portal_bff_amh` ao mesmo tempo (sessões vivas, M2), então as duas listas de
    artefatos desta onda vão no mesmo database.
  - **Instalação:** ela entra com o login dono do schema nativo, não com `maezo_app`. `maezo_app`
    é dono do database e o runtime da Helena; se ele fosse dono das tabelas de autoridade, o
    próprio app poderia gravar grants e designações.
  - **AUTH:** a instalação AUTH entra nesta onda. São o DDL `mzo_auth_*` e as linhas de instalação
    e designação por `AuthInstallation.installSchema`/`designate`, que exigem sessão do dono com
    `current_schema()` = schema nativo e TLS (`ConsumerEdgeInstallation.java:60-69`). Os insumos de
    qualificação AUTH (`human-auth-installation-qualification.v1`, `AuthInstallation.java:23-30`)
    também não têm gerador hoje e entram na T1.3.
  **[23/09] Com o ADR-0060:**
  - **Dono e schema.**
    - Criar a role `maezo_native_schema_owner`, sem nenhum atributo de privilégio e sem membership.
    - `CREATE SCHEMA maezo_native AUTHORIZATION maezo_native_schema_owner`, pela role de
      administração.
    - O DDL todo entra por sessão **do dono**, com `current_schema()=maezo_native` e TLS.
  - **Admissão Q2.** Instalar `mzo_portal_read_admission` e a linha assinada da Onda 2 (T1.7a).
  - **Grants.**
    - `GRANT USAGE ON SCHEMA maezo_native` ao `cibseven_app` e ao witness.
    - Login `portal_read_source_amh` com SELECT nas colunas de `amh.portal_memberships`, concedido
      pelo dono de `amh`.
  - **Negativos a mais no "pronto":**
    - o `cibseven_app` não cria em `maezo_native` nem escreve na admissão;
    - o `portal_read_source_amh` não lê nada além daquela tabela.
  - **Reversão:** `DROP SCHEMA maezo_native CASCADE` e `DROP ROLE` das roles novas (tabelas vazias,
    antes da Onda 4).
  - **Estimativa:** a onda passa a **1,5 d**.
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
  ~~`currentSchema=cibseven,public`~~ `currentSchema=maezo_native,cibseven` (**[Onda 0]** D-C2),
  `portMappings` 8443, NLB interno + target group TCP 8443 +
  listener TCP 443, zona privada + registro, SG do NLB, ingress 8443 no SG do engine só a partir
  do SG do NLB.
- **Onde roda:** `terraform plan`/`apply` do state `envs/dev-sa-east-1/maezo-operadora`, na
  receita conhecida: var-file PHI, 4 digests, cerca de plano. Janela combinada, porque **a
  Helena usa este engine**.
- **Pré-requisito:** Ondas 1 (T1.1, T1.2), 2 e 3. **[Onda 0]** Também a T1.7, sem a qual a imagem
  com `PortalReadPlugin` não sobe, e a T1.8. A janela está liberada pelo dono (N4, 23/09), mas só
  vale quando esses pré-requisitos fecharem.
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
  viva antes do apply) e `DB_URL` para `currentSchema=cibseven`. **[Onda 0]** O digest foi
  confirmado na task viva em 23/09 (`sha256:6f478e230869d9fbe2dfa7dc58079134562dd8cd0d3cc5dac11adf8c5b9f27bf`,
  task definition `:5`). A reversão da imagem precisa voltar **junto** com a do `DB_URL`: a
  imagem nova com `currentSchema=cibseven` morre no boot (I2, S1 R1), e a antiga com o path novo
  sobe, mas não serve para nada. Ao restaurar o banco do engine do zero, suba primeiro com
  `currentSchema=cibseven` (I9). As tabelas `mzo_*` são aditivas e
  ficam. NLB, zona e SGs saem por `terraform apply` do commit anterior.
  **[23/09] Precisão (ADR-0060, Consequência 4):** "suba primeiro com `currentSchema=cibseven`"
  vale para a **imagem antiga**. A nova morre com esse path. Depois vêm incarnation nova,
  reinstalação, nova admissão Q2 e nova designação, e só então a imagem nova.
- **[23/09] Pré-requisito corrigido:** T1.7a e T1.7b (não só "a T1.7"), mais o checkpoint C1 verde
  e a imagem candidata assinada (Onda 2).
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
  2. `GET /cases/{ref}` → 200. **[23/09]** A resposta vem **sem** a lista de tarefas correntes,
     porque o grant não leva `staff_current_task.v1` (T1.6). Tarefas no detalhe são Onda 8;
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

~~**Total até `/cases` com caso real (Ondas 0 a 7): 3 a 5 semanas** de calendário, com um builder
por área e o aprovador disponível nos dias das Ondas 2 e 5. A variação vem de S1 (D-C) e de N3.~~

**[23/09] Versão atual (substitui o diagrama e o total acima):**

```
Onda 0 ✔ ─► Onda 1 (grafo em §3.2; caminho crítico T1.7a → T1.7b → T1.5 → C1)
          ─► Onda 2 [geração ∥ imagem candidata] ─► assinatura do aprovador (designação rascunho + admissão Q2)
          ─► Onda 3 (DDL/logins podem começar com a T1.4 mergeada; a linha de admissão espera a assinatura da Onda 2)
          ─► Onda 4 ─► Onda 5 ─► Onda 6 ─► Onda 7
```

A geração da Onda 2 pode começar assim que a T1.3 fechar, em paralelo ao resto da Onda 1. A
instalação da Onda 3 (schema, dono, DDL e logins) pode rodar antes da assinatura, mas a **linha de
admissão** só entra depois dela. Ondas 4 → 5 → 6 continuam **sem** paralelismo, porque os pins de
SPKI e de configuração nativa só existem com o servidor vivo.

**Total até `/cases` com caso real (Ondas 1 a 7): 4 a 6 semanas** de calendário, confiança
**média-baixa**. Hipóteses: cinco frentes na Onda 1 e o aprovador disponível em dois momentos
(Onda 2 e Onda 5). A variação vem de três coisas, nesta ordem:
1. a T1.7 (classloader, paridade JCS);
2. o catálogo vazio (T1.5);
3. o CI parado por billing, que segura a assinatura da imagem da Onda 4.

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
| *(dentro do manifesto)* `native_relation_pins` | `oid`, `owner` das 2 relações | **Catálogo do banco do engine** | `SELECT c.relname, c.oid, pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN ('mzo_portal_read_membership','mzo_human_principal')`. **[Onda 0]** Com D-C2 o filtro é `n.nspname=<schema nativo pinado>`, e o próprio nome do schema vira pin do manifesto v2 (T1.8) |
| *(arquivos públicos)* `session-lock-ca.pem`, `native-witness-ca.pem` | raízes RDS | bundle vendorizado e pins do repo | SHA-256 contra `deploy/certificates/README.md` |
| *(arquivos públicos)* `native-ca.pem` | CA que emitiu o cert do servidor | o listener vivo | `openssl verify -CAfile native-ca.pem <cert lido do s_client>` → `OK` |
| *(arquivos públicos)* `read-client-certificate.pem` | cert do cliente | designação assinada | SPKI do cert = `entries[read_requester].certificate_spki` |
| **[23/09]** *(dentro do manifesto v2)* `native_schema` | schema das `mzo_*` | **Catálogo do banco** | `SELECT n.nspname, pg_get_userbyid(n.nspowner) FROM pg_namespace n WHERE n.nspname='maezo_native'` → 1 linha, dono `maezo_native_schema_owner`; `SELECT has_schema_privilege('cibseven_app','maezo_native','CREATE')` → `f` |
| **[23/09]** *(admissão Q2, T1.7)* `trust_configuration_digest` | SHA-256 do trust file parseado do `PortalReadPlugin` | o arquivo que vai no segredo nativo | recalcular com o `verify` da T1.3 sobre o arquivo. Nunca copiar o valor do rascunho |
| **[23/09]** *(admissão Q2)* `code_digests` | SHA-256 dos JARs do engine e do provedor | **a imagem candidata assinada** (Onda 2) | `docker create` + `docker cp` dos dois JARs, depois `sha256sum`, na máquina do aprovador, sobre o digest da imagem que ele aprovou |
| **[23/09]** *(admissão Q2)* compromissos de continuidade | `HMAC(chave, domínio)` | a saída do `generate` e o log do boot | o `generate` imprime o compromisso. Na Onda 4, o boot registra só o `key_id` e o compromisso, e o aprovador compara os dois |
| **[23/09]** *(admissão Q2)* `catalog_digest` | digest do catálogo mínimo do staff | o artefato de catálogo da T1.5 | `sha256` do JCS do artefato, que o aprovador lê (tem que ter `entries=[]`) |
| **[23/09]** *(admissão Q2)* escopo, engine, incarnation, janela | iguais aos do staff | as mesmas fontes das linhas `scope.*` acima | igual. `valid_until − not_before ≤ 14 d` (N2) |
| **[23/09]** *(provedor Q2, T1.7a)* `portal_read_root_sha256` / `capability_digest` | SHA-256 do SPKI da raiz que o provedor **carregou** (`root_public_key_sha256` do `portal-read-provider.v1`) e SHA-256 dos bytes da admissão que ele aceitou | **A chave do próprio aprovador** (D-F) e **o registro de admissão que ele assinou**, contra **o engine vivo** (linha pública do 1º `acquire`) | `aws logs filter-log-events --log-group-name /ecs/maezo-operadora-dev/cibseven --filter-pattern portal_read_provider` na última task. `root_sha256` tem que ser igual ao `openssl pkey -in root.pem -pubout -outform DER \| sha256sum` da máquina dele, e `capability_digest` igual ao `sha256sum` do JCS que o `sign-admission` assinou. `admission_ref`/`revision`/`engine_code`/`provider_code` batem com a admissão e com a linha `code_digests` acima. Sem essa conferência, quem monta o arquivo do provedor pode trocar par e pin e se auto-admitir |

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
| **0-7** | | ~~**3-5 semanas**~~ **reestimar** (ver abaixo) | **baixa** |
| 8 | não estimado com precisão | +3-6 semanas | **baixa** |

**[Onda 0] A linha "0-7" não vale mais.** A Onda 0 custou ~0,5 dia (a S1 levou horas, não 2 a 3
dias, porque o harness já existia). O resto cresce:
- a Onda 1 ganha a T1.8 (~1 semana) e as correções de DDL (1-2 d);
- a Onda 1 ganha também a **T1.7, que não foi estimada**. Ela é a autoridade Q2 do engine, que o
  plano punha fora do caminho de `/cases`.
Proposta de infra, **a validar pelo software-architect**: tratar a T1.7 como o mesmo risco da
Onda 8. Enquanto ela não tiver desenho próprio, `/cases` fica em **5 a 9 semanas, confiança
baixa**.

Pode rodar em paralelo: as 5 tarefas da Onda 1 (T1.6 depois de N3); a Onda 2 com T1.1/T1.5
assim que T1.3 fechar; a Onda 3 com a Onda 2. **Não** paraleliza: 4 → 5 → 6, porque os pins de
SPKI e de configuração nativa só existem depois que o servidor vivo sobe.

**[23/09] Reestimativa do software-architect (substitui a linha "0-7" e a proposta de 5 a 9
semanas).** A T1.7 **não** tem o risco da Onda 8. O desenho da §3.1 a recorta ao que o staff chama:
admissão, chaves, catálogo e membership. Tarefa, classificação e política de identidade recusam
sempre.

| Onda | Esforço | Calendário | Confiança |
|---|---|---|---|
| 0 | ~0,5 d (feita) | feita, 23/09 | — |
| 1 | ~35-46 d-pessoa | 2,5-3 sem com 5 frentes (4-5 sem com 3) | média-baixa (T1.7) |
| 2 | 2 d (+ imagem candidata e admissão) | 1-2 d (agenda do aprovador) | média (CI/billing) |
| 3 | 1,5 d | 1-2 d (janela) | alta |
| 4 | 1-2 d | 1-2 d (janela) | média |
| 5 | 1-1,5 d | 1-2 d (aprovador) | alta |
| 6 | 1 d | 1 d | alta |
| 7 | 0,5-1 d | 1 d | média |
| **1-7** | | **4 a 6 semanas** | **média-baixa** |
| 8 | fora da meta (N6/N8) | — | — |

**Custo recorrente a mais:** a admissão Q2 também vence (no máximo 14 dias, N2). A renovação usa o
mesmo ciclo da designação: o aprovador assina as duas na mesma sessão, e a Onda 5 repetida ganha
+0,5 h.

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

**Decisões do dono em 23/09/2026:** N2 = 14 dias. N4 = ninguém usa o motor de dev, a troca da
imagem (Onda 4) está liberada quando chegar a hora. N6 = a meta é `/cases` (Onda 7), a fila
(Onda 8) fica fora. **Continuam abertas:** N1, N3 e N5.

7. **N7 [Onda 0] — D-C2 (schema nativo dedicado + ADR)**, a substituta de D-C. Decisão técnica,
   do software-architect e não do dono. Mesmo assim precisa de ciência do dono por dois motivos:
   cria um schema e um login novos no database `maezo` compartilhado, e rejeita a alternativa que
   mexeria no dono de `public`.
8. **N8 [Onda 0] — Com a T1.7 no caminho crítico, `/cases` ainda é a meta certa?** O custo real de
   `/cases` agora inclui a autoridade Q2 do engine. Reabre N6 com um número novo, que ainda não
   existe.

**[23/09] Decisões do dono (segunda rodada):**
- **N7 aprovada:** D-C2, registrada no **ADR-0060**.
- **N8:** a meta continua sendo `/cases` (Onda 7), e a fila (Onda 8) fica fora. O número novo existe:
  4 a 6 semanas (§5).
- **N4 liberada.**
- **N2 = 14 dias.** Vale também para a admissão Q2.
- **N3:** o caso é visível **só ao grupo que a DMN `escalation_routing` escolheu** para aquela
  escalação (T1.6).
- **N5:** o `engine-rest` aberto é aceito **só em dev**, com uma cerca de CI que reprova staging e
  prod (T1.9).

9. **N9 [23/09, D-H.1] — Na Onda 7, `/cases` só mostra escalações ancoradas a uma guia AUTH** (risco de SLA, ADR-0051). As escalações conversacionais da Helena (P1, red flag) só entram quando existir um `kind` de caso "escalation", que é programa próprio, com ADR. Proposta: aceitar isso para o teste conjunto do diretor. **Precisa de ciência do dono.**

**[23/09/2026, dono] Decididas N1, N2 e N9 conforme as propostas:** N1 — Leonardo é o aprovador nomeado; a raiz Ed25519 fica só com ele, gerada na máquina dele; nenhum agente a recebe. N2 — pacote válido por 14 dias, lembrete 3 dias antes. N9 — aceito: no teste conjunto do diretor `/cases` mostra só escalações ancoradas a guia AUTH.

~~Continua aberta: só a N1~~ (aprovador nomeado). O plano assume Leonardo, que é a proposta, e as
Ondas 2 e 5 não começam sem ela.

---

## 7. Registro de medições (preencher na Onda 0)

| Medição | Comando | Resultado | Data |
|---|---|---|---|
| M1 `authorizationEnabled`/`tenantCheck`/`engine_name` | §7.1 | `authorizationEnabled=true`; `tenantCheck*` ausente (default); `engine_name=default`; 2.1.0; sem JAR Maezo | 23/09/2026 |
| M2 identity DB = engine DB? schema das tabelas do portal | §7.2 | **mesmo database `maezo`** (sessões vivas de `cibseven_app`, `maezo_app` e `portal_bff_amh`); tabelas do portal só em `amh`; `portal_identity` não existe | 23/09/2026 |
| M2 colisões `mzo_`/`act_` em `public` | §7.2 | **nenhuma**; `ACT_*` só em `cibseven` (49); nenhuma `mzo_*` em schema nenhum | 23/09/2026 |
| S1 D-C (`currentSchema=cibseven,public`) | §7.3 | **REFUTADA** (AUTH, I5); substituta D-C2 com o engine subindo (R5); de quebra, I6, I7, I8 e I9 | 23/09/2026 |
| Conta 203312548462: segredos `maezo-operadora/dev/portal/*` | `aws secretsmanager list-secrets` (thread principal, 23/09 UTC) | só `…/amh/session-dsn`; nenhum `staff-materials` | 23/09/2026 |

### 7.1 M1 — engine vivo (23/09/2026, só leitura)

Caminho: `ecs execute-command` no container `cibseven`, task
`9fab9e2d436f43c0ae903f50715a399f`, iniciada em 20/09 17:43 -03. Perfil `adm-dev`, Git Bash com
`MSYS_NO_PATHCONV=1`.

```sh
aws ecs describe-tasks --cluster maezo-operadora-dev --tasks 9fab9e2d436f43c0ae903f50715a399f \
  --query "tasks[0].{td:taskDefinitionArn,digest:containers[0].imageDigest}"
# td = task-definition/maezo-operadora-dev-cibseven:5
# digest = sha256:6f478e230869d9fbe2dfa7dc58079134562dd8cd0d3cc5dac11adf8c5b9f27bf
aws ecs execute-command --cluster maezo-operadora-dev --task 9fab9e2d436f43c0ae903f50715a399f \
  --container cibseven --interactive --command "sh -c 'cat /camunda/conf/bpm-platform.xml'"
#  9:  <process-engine name="default">
# 17:      <property name="databaseSchemaUpdate">true</property>
# 18:      <property name="authorizationEnabled">true</property>
#     nenhum tenantCheckEnabled (vale o default do engine)
#     plugins ativos: ProcessApplicationEventListenerPlugin, SpinProcessEnginePlugin,
#     ConnectProcessEnginePlugin (LDAP e AdministratorAuthorizationPlugin estão COMENTADOS)
aws ecs execute-command ... --command "sh -c 'curl -s localhost:8080/engine-rest/engine; curl -s localhost:8080/engine-rest/version'"
# [{"name":"default"}]
# {"version":"2.1.0"}
aws ecs execute-command ... --command "sh -c 'env | grep -E \"^(DB_URL|DB_DRIVER|DB_SCHEMA_UPDATE)=\" | sed -E \"s#//[^/]+/#//<host>/#\"'"
# DB_URL=jdbc:postgresql://<host>/maezo?currentSchema=cibseven   (sem sslmode explícito)
aws ecs execute-command ... --command "sh -c 'ls /camunda/lib | grep -i -E \"maezo|human|portal\"; ls /camunda/webapps'"
# nenhum JAR Maezo; webapps: ROOT camunda cibseven-welcome docs engine-rest examples host-manager manager webapp
```

Leitura: `scope.engine_name = default` (§4). O plugin não está no engine vivo, o que confirma
§1.3. `authorizationEnabled` já está ligado, então o I3 não pede mudança de flag.

### 7.2 M2 — bancos (23/09/2026, só leitura)

Caminho: quatro tasks avulsas `run-task` com a TD `maezo-operadora-dev-webhook-receiver:17`,
subnet `subnet-0e1f840dbec44e66a`, SG `sg-0e2aebe1a2c1d253d`, sem IP público. Override
`command: ["python","-c",<script asyncpg>]`, sessão `SET SESSION CHARACTERISTICS AS TRANSACTION
READ ONLY`, login `maezo_app`, só `SELECT` de catálogo, sem DSN nem senha no log. Tasks:
`667517aa…`, `1c6b479e…`, `9a5017dd…`, `480cb283…`. Log em `/ecs/maezo-operadora-dev/webhook-receiver`,
stream `webhook/webhook-receiver/<taskId>`.

**Por que não usei a credencial do engine.** A TD do `cibseven` é Java, sem Python nem `psql`, e o
`--overrides` do ECS não injeta `secrets`. Todas as perguntas da Onda 0 são de catálogo
(`pg_class`, `pg_namespace`, `pg_roles`, `pg_stat_activity`), que qualquer login do database lê.

| Consulta | Resultado |
|---|---|
| `current_database(), current_setting('server_version')` | `maezo`, **PostgreSQL 17.9** (Aurora `amh-aurora-hapi-dev`) |
| `pg_stat_activity` por `usename, datname` | `cibseven_app→maezo` (5), `maezo_app→maezo` (6), `portal_bff_amh→maezo` (1), `hapi_app→hapi`. **O DSN do portal e o do engine apontam para o mesmo database** |
| `pg_namespace` | `amh` (dono `maezo_app`), `cibseven` (dono `cibseven_app`), `public` (dono `pg_database_owner`) |
| `pg_database.datdba` de `maezo` | `maezo_app`, que por isso é quem age como `pg_database_owner` em `public` |
| ACL de `public` | `{pg_database_owner=UC, maezo_app=U, cibseven_app=U}`; PUBLIC sem direito |
| `has_schema_privilege` | `cibseven_app`: CREATE em `cibseven` = t, em `public` = f; `maezo_app`: CREATE em `public` = t |
| `pg_auth_members` de `cibseven_app` | vazio (AUTH exige isso, I5) |
| `pg_roles` | `cibseven_app`, `maezo_app`, `portal_bff_amh`: sem super/createdb/createrole/bypassrls/replication; `portal_bff_amh.rolconfig = {search_path=amh}` |
| relações do portal (`portal_sessions`, `portal_memberships`, …) | só em `amh`, dono `maezo_app`, `relrowsecurity=false` |
| `to_regclass` | `amh.portal_sessions` ✓, `public.portal_sessions` = NULL, `amh.portal_memberships` ✓, `public.portal_memberships` = NULL |
| `portal_identity` / `lock_external_session` | schema inexistente; função inexistente |
| `public` ∩ (`mzo\_%` ∪ `act\_%`) | **vazio** |
| `act\_%` por schema | só `cibseven` (49 tabelas) |
| `mzo\_%` em qualquer schema | **vazio** (nenhuma relação nativa instalada) |
| tabelas em `public` | `checkpoint_blobs`, `checkpoint_migrations`, `checkpoint_writes`, `checkpoints` (dono `maezo_app`: checkpointer da Helena) |
| TLS das sessões | `maezo_app`: ssl=t. As sessões de `cibseven_app` não são visíveis para outro login: **NÃO VERIFICADO**. O JDBC não fixa `sslmode`, então o default do pgjdbc é `prefer`. AUTH exige TLS na conexão do engine (`ConsumerEdgeInstallation.java:69`); vale fixar `sslmode=verify-full` na T1.2 |

### 7.3 S1 — spike local (23/09/2026, Docker Desktop 4.91 / Engine 29.8.0, nada na AWS)

**Montagem do spike**

- **Imagens**, construídas do SHA `aa8829bc3b92910d6b2590daacbf6f9a8e0cfc9e`, contexto na raiz:

  ```sh
  docker build -f deploy/cibseven/Dockerfile.human --build-arg INSTALL_PORTAL_READ=true  -t maezo-s1-human:onda0 .
  # -> sha256:6ab9cbdc3e89…
  docker build -f deploy/cibseven/Dockerfile.human --build-arg INSTALL_PORTAL_READ=false -t maezo-s1-human:onda0-noread .
  # -> sha256:1db398dc04c5…
  ```

- **Fixture:** gerada por `prepare._generate_fixture` do harness (CA, servidor, clientes, trust,
  `server.xml` com 8080 e 8443 TLS 1.3 `certificateVerification=required`). O `server.xml` recebeu
  variantes só no `url` do JDBC, com usuário `cibseven_app`.
- **Banco:** `postgres:17-alpine` (`sha256:d4bb0a8c…`, 17.11), com o layout medido em M2:
  - `maezo` com dono `maezo_app`;
  - `cibseven` com dono `cibseven_app`;
  - `public` com a mesma ACL do dev;
  - `mzo_*` (human + staff + staff-event) instaladas **por `maezo_app`** e grants para
    `cibseven_app`.
- **Descarte:** a fixture, com chaves e senhas, foi apagada ao final. Containers e rede foram
  removidos.

| Rodada | Configuração | Resultado |
|---|---|---|
| R0 | imagem `INSTALL_PORTAL_READ=true`, `cibseven,public` | **não sobe**: `ENGINE-08043 … 'Start process engine default': READ_DEPENDENCY_UNAVAILABLE` (I6) |
| R1 | `noread`, `currentSchema=cibseven`, banco vazio | cria os 49 `ACT_*` em `cibseven` e morre em `human engine store unavailable` (I2 provado) |
| R2 | `noread`, **`cibseven,public` (D-C)**, `ACT_*` existente | **sobe** (`Server startup in [21226] ms`) e sobe de novo no restart. `ACT_*` 49 em `cibseven`, 0 em `public`. `MZO_HUMAN_TENANT`, `ACT_GE_PROPERTY` e `mzo_staff_case_grant` resolvem. Pin staff (SQL do `StaffCaseStore`, `nspname='public'`) passa nas 14 relações. **Precondições AUTH falham**: `current_schema=cibseven`, `owner=cibseven_app`, `owner_differs=f`, `runtime_lacks_create=f` (I5). Matriz de escrita: `checkpoint_chunk upd=f` contra o `upd=t` exigido (I8) |
| R3 | `noread`, `public,cibseven`, `ACT_*` existente | sobe. `current_schema=public`, dono `pg_database_owner`, sem CREATE e sem membership. Mas `ALTER ROLE pg_database_owner LOGIN` → `role name "pg_database_owner" is reserved`, e uma `public.act_ge_property` criada por `maezo_app` passa a ser a resolvida pelo engine (sombreamento provado) |
| R4 | `noread`, `public,cibseven`, banco **vazio** | não sobe: `ENGINE-03015 … permission denied for schema public` (I9) |
| R5 | `noread`, **`maezo_native,cibseven` (D-C2)**, `ACT_*` existente, `mzo_*` em `maezo_native` com dono `native_owner` | **sobe**. `ACT_*` 49 em `cibseven`, `mzo_*` 20 em `maezo_native`. Precondições AUTH: `current_schema=maezo_native`, `owner=native_owner`, `owner_differs=t`, `runtime_lacks_create=t`, memberships 0. Pin staff com `nspname='maezo_native'` passa nas 14 relações. Com o `'public'` fixo de hoje dá 0, o que mostra que a T1.8 é necessária |

**mTLS** (R2, R3 e R5 deram o mesmo resultado). O cliente usou a mesma forma de TLS do
`StaffNativeClient` (`publisher.py:173-179`). O Python local é 3.14, então o spike desligou
`VERIFY_X509_STRICT` para emular o 3.12 do portal (ver T1.3).

| Chamada | Resultado |
|---|---|
| `POST https://localhost:18443/maezo-human/v1/staff-case-list`, sem certificado de cliente | recusado no handshake: `SSLV3_ALERT_BAD_CERTIFICATE` |
| mesma chamada com certificado de CA não confiável | recusado no handshake: `SSLV3_ALERT_CERTIFICATE_UNKNOWN` |
| mesma chamada com certificado confiável, `{}` | **`HTTP 503` `{"error":"HUMAN_ENGINE_UNAVAILABLE"}`**, a recusa fechada do plugin (`staffConfigured()`, sem configuração staff). Não é 404 nem página de erro do Tomcat |
| mesma rota por HTTP 8080 | `HTTP 403` `{"error":"AUTHORITY_DENIED"}` (`HumanServlet.peer` exige `isSecure`) |
| `GET http://localhost:18080/engine-rest/engine` | `200 [{"name":"default"}]`: o REST de worker e agentes continua intacto |

**Veredito sobre D-C:** refutada e substituída por **D-C2** (§2). O plano foi corrigido nos pontos
marcados **[Onda 0]**:
- §0 item 4;
- §1.4, com I1 a I3 atualizados e I5 a I9 novos;
- §2, em D-A, D-C e D-C2;
- §3, na Onda 0, na Onda 1 (T1.7, T1.8 e acréscimos à T1.2, T1.3 e T1.4) e nas Ondas 3 e 4;
- §4 (`native_relation_pins`);
- §5;
- §6 (N7 e N8).
