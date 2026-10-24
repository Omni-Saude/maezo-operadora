# W5 CLNT 5.5 — Mapa e disposição das 7 áreas admin (stubs + BFF de assignments)

**Status:** `MAPA/DISPOSIÇÃO` — pacote **só-documentos** (fase mapa do item 5.5; o plano permite "ou disposição por área"). **Data:** 2026-10-24 (`main` em `0616eb4f`). **Este documento não implementa nada:** zero mudança de comportamento, zero flip, zero rota nova; as disposições abaixo são recomendações de engenharia — aprovação de escopo por área permanece com o dono (§HDR do programa). Base de código inspecionada: checkout limpo de `origin/main` (worktree, sha `7f6c2576`).

Regra de leitura: toda afirmação de fato traz `arquivo:linha` ou `teste::nodeid`; o que não foi executado/verificado em runtime está marcado **NÃO VERIFICADO**. As contagens de testes são contagens estáticas de `def test_` nos arquivos citados (não execução).

---

## §1 O que "admin 7/7" é no código — fonte da verdade

A unidade de contagem do 5.5 é o tipo `StaffArea` do workspace de colaboradores do portal — exatamente 7 valores:

- `src/maezo/portal/web/src/PortalNavigation.tsx:5-12` — `export type StaffArea = "overview" | "my-work" | "team-queues" | "cases" | "documents" | "operations" | "administration"`.
- Rótulos/hints exibidos: `PortalNavigation.tsx:16-24` (documents = "Documentos — Pedidos, verificação e acesso"; operations = "Operações — Comandos, recibos e dependências"; administration = "Administração — Vínculos e permissões governados").
- Marcador "Em breve" por capability ausente: `PortalNavigation.tsx:44-49` + `:103`.
- Wiring dos painéis (o estado real de cada área): `src/maezo/portal/web/src/PortalSessionExperience.tsx:158-208` (`StaffPortalExperience`), mapa área→capability `:78-83`, títulos `:85-93`, rota endereçável única `/portal/cases/:ref` `:97-113`.

Nota de runtime: `StaffPortalWorkspace.tsx:1-28` (o componente que mapeia as 7 áreas para um registro de painéis) hoje só é consumido por `PortalNavigation.test.tsx:20,37`; a experiência viva é `StaffPortalExperience` (renderização por área ativa, `PortalSessionExperience.tsx:216-218`).

As 4 áreas **externas** (`ExternalArea` = requests/documents/communications/receipts, `PortalNavigation.tsx:14,26-31`) NÃO fazem parte das 7 admin; a área vendor externa renderiza indisponível por desenho (`PortalSessionExperience.tsx:243-270`).

## §2 Critério de "viva" (verificável)

Uma área está **VIVA** quando os três selos batem:

1. **Painel/rota funcional:** componente web real consumindo rotas BFF montadas em `create_app` (`src/maezo/portal/api/app.py:308-330`) — não `UnavailableArea`/texto estático.
2. **Testes:** suíte de API do lado BFF + suíte web do componente, ambas em lane de CI (`gateway-platform`/`restante` + `portal-web`, `.github/workflows/ci.yml:266-281,580`).
3. **BFF wired:** a cadeia browser→BFF→gateway/engine resolve sem `production_capabilities_unavailable` no caminho feliz (capability presente na sessão, `records.py:90` conjunto fechado `identity|staff_cases|human|vendor`; projeção web `sessionClient.ts:25-40`).

## §3 Inventário e disposição por área

### 3.1 overview (Visão geral) — VIVO

- **Existe hoje:** `PortalSessionExperience.tsx:162-172` renderiza `StaffOverview` (`StaffOverview.tsx:16-37`), que embute `EmployeeQueues` (`EmployeeQueues.tsx`, 530 linhas; cliente `taskReadClient.ts`; refresh 10s `EmployeeQueues.tsx:14`). Filas embutidas gated por `human` (`PortalSessionExperience.tsx:165-167`).
- **Testes:** `src/maezo/portal/web/src/StaffOverview.test.tsx`; `EmployeeQueues.test.tsx`; `EmployeeQueues.security.test.tsx`; `tests/unit/portal/test_task_queue_api.py` (12 testes).
- **Viva significa:** painel real + filas legíveis; já cumpre (o "resumo de prazos" do hint é cumprido pelos prazos por tarefa da fila; não há agregação separada).
- **Disposição: DISPOR-DEFER** (vivo; nenhum pacote exigido pelo alvo D-12).

### 3.2 my-work (Meu trabalho) — VIVO

- **Existe hoje:** `PortalSessionExperience.tsx:173-183` (`initialQueue="mine"`), capability `human` (`:79`). BFF: fila `tasks.py:134`, leitura `tasks.py:149`; decision-context `decisions.py:170-172`; decisão `decisions.py:193-196`; completion `tasks.py:299`; contexto `tasks.py:423`. UI: `EmployeeQueues` + `TaskOwnershipControls.tsx:1-17` (consome `assignmentClient`) + `TaskCompletionControls.tsx` + `TaskContextPanel.tsx`.
- **Testes:** `test_task_queue_api.py` (12); `test_decision_api.py` (29); `test_task_completion_api.py`; `test_task_context_api.py`; `assignmentClient.test.ts`; `TaskOwnershipControls.test.tsx`.
- **Disposição: DISPOR-DEFER** (vivo).

### 3.3 team-queues (Filas da equipe) — VIVO

- **Existe hoje:** mesma wiring de 3.2 com `initialQueue="team"` (`PortalSessionExperience.tsx:180`); reatribuição governada: candidates com digest canônico JCS verificado no browser (`assignmentClient.ts:74-85`), rota `decisions.py:295-297` (`GET /tasks/{task_id}/assignment-candidates`), submissão `decisions.py:256-258` (`POST /tasks/{task_id}/assignments`), contexto `decisions.py:224-226`.
- **Testes:** mesmos suites de 3.2 + `test_assignment_api.py` (12) + `test_assignment_policy.py` + `test_assignment_composition.py` (6) + `test_assignment_receipt.py`/`test_assignment_receipt_schema.py`.
- **Disposição: DISPOR-DEFER** (vivo).

### 3.4 cases (Casos) — VIVO

- **Existe hoje:** `PortalSessionExperience.tsx:184-192`; capability `staff_cases` (`:81`); única área com endereço `/portal/cases` e `/portal/cases/:ref` (`:97-113`). UI: `StaffCaseWorkspace.tsx` (592 linhas) + `staffCaseClient.ts` (198 linhas). BFF: `cases.py:22` router, rotas `cases.py:148,182`, gate de audiência staff `cases.py:108`. Gateway: `src/maezo/gateway/staff_cases/` (15 módulos; auditoria de leitura DL-0149 em `staff_cases/service.py:1-8`, sink `read_audit.py`).
- **Testes:** `test_staff_cases_api.py` (9); `test_staff_case_ddl_live_pg.py` (lane PG real); `StaffCaseWorkspace.test.tsx`; `StaffCaseRoutes.test.tsx`.
- **Disposição: DISPOR-DEFER** (vivo).

### 3.5 documents (Documentos) — STUB de UI

- **Existe hoje:** `PortalSessionExperience.tsx:193-194` — `UnavailableArea` fixa: "A consulta e o envio de documentos ainda não estão disponíveis nesta versão do portal."; capability gate `staff_cases` (`:82`) só controla o selo "Em breve" da navegação — **mesmo com a capability presente, a área renderiza indisponível** (não há componente).
- **Substrato backend que JÁ EXISTE:** BFF de documentos montado sob `/api/v1/portal` — `documents.py:23` router com 7 rotas (`:72,:82,:92,:102,:107,:114,:124`; bytes nunca no BFF: redirect 307 para `/api/v1/phi/documents/{ref}/content` em `documents.py:60-63`) + plano PHI `document_phi.py:37,138,155`; gateway `src/maezo/gateway/documents/` (`service.py` autorização/custódia, `storage.py`, `authority.py`, `postgres.py`, `schema.sql`) e `src/maezo/gateway/document_requests/` (10 módulos); contratos `portal/contracts/documents.py`.
- **Testes:** `test_case_document_services.py` (7); `test_intake_documents.py`. **Nenhum teste web de documentos staff** — não há componente.
- **Viva significa:** painel staff de documentos consumindo o BFF existente atrás de flag, com testes web+API.
- **Disposição: IMPLEMENTAR-DARK** (flag default-off) — esforço **M**: o backend é o substrato menos lacunar das 3 stubs (rotas e gateway existem e são testados); falta só a superfície staff.
- **Pacote seguinte (1 PR):** painel `documents` staff **read-only** (lista + pedidos de documento do caso) consumindo `document_router` existente, atrás de flag nova default-off em `PortalSettings`; testes web do componente + teste de API da rota com sessão staff; bytes permanecem exclusivamente no plano PHI (nenhuma rota de bytes nova). Envio/upload staff fica fora do 1º PR.

### 3.6 operations (Operações) — STUB de orientação estática

- **Existe hoje:** `PortalSessionExperience.tsx:195-205` — seção estática "Comandos por tarefa" que instrui abrir a tarefa em Meu trabalho/Filas; **sem dados, sem chamada, sem rota**. O hint promete "Comandos, recibos e dependências" (`PortalNavigation.tsx:22`).
- **Substrato backend que JÁ EXISTE:** recibo público por comando — `decisions.py:47-48` (`_COMMAND_PATH`/`_RECEIPT_PATH`) + leitura `decisions.py:337-348` (`PublicAssignmentReceipt`/`human-public-receipt.v1`, `gateway/human/receipt.py`); recibos de publicação/outbox (`gateway/human/outbox.py`, `publication_events.py`, `assignment_receipt.py`); dependências por tarefa já expostas no contexto `tasks.py:423`; contexto de escalonamento `gateway/human/escalation_context.py` (+`escalation_context_engine.py`, SP-OP-ESCALATION-001). No browser, `TaskOwnershipControls` já exibe o recibo do comando de assignment — o padrão existe, falta o painel agregado.
- **Testes:** do substrato: `test_decision_api.py` (29, inclui receipt), `test_assignment_receipt*.py`; **do painel: nada** (área sem componente).
- **Viva significa:** painel por tarefa com comandos permitidos + recibos + dependências, consumindo rotas BFF existentes, com testes.
- **Disposição: IMPLEMENTAR-DARK** (flag default-off) — esforço **M**: zero rota nova de mutação; é composição de leitura sobre rotas vivas.
- **Pacote seguinte (1 PR):** painel `operations` read-only por tarefa (recibos dos comandos da tarefa + dependências do task context), atrás de flag default-off; testes web + fixture de API. Não inclui nenhum comando novo.

### 3.7 administration (Administração) — STUB de UI, backend cercado FORA do portal

- **Existe hoje:** `PortalSessionExperience.tsx:206-207` (ramo `else` final) — `UnavailableArea` "Nenhuma função administrativa está disponível para esta sessão."; hint "Vínculos e permissões governados" (`PortalNavigation.tsx:23`). **Nenhuma função administrativa é montada para sessão de portal — por desenho.**
- **Substrato backend que JÁ EXISTE (vivo, em cerco próprio):**
  - Cerca declarada: `src/maezo/portal/admin/__init__.py:1` — "Private reviewed human-administration services; **never public portal routes**."
  - Serviço de administração de assignments: `portal/admin/assignments.py:80` (`PostgresStaffAssignmentAdministration`), docstring `:1-8` — "**No BFF routes call this service**; the independently granted administrative connection owns mutations." Consumido como fonte revisada pelo publicador nativo (`gateway/human/assignment_publisher.py:5`) e pelo recibo (`assignment_receipt.py:22`).
  - Administração de vínculos vendor: `gateway/human/vendor_membership_administration.py:29` (`ADMIN_ACTION`, taxonomia fail-closed) + `_postgres`; job de publicação `vendor_membership_publication_job.py:99` (`VendorLedgerEntry`); migrations `0019_vendor_channel_authority.py`, `0020_vendor_channel_submissions.py`; worker `tools/workers/vendor_admin.py:9`.
  - Administração de vínculos provider: `gateway/human/provider_membership_administration.py:23,26` (`ADMIN_ACTION`, `AdministrationReason`) + `_postgres` + `provider-membership-administration-schema.sql`; job `membership_publication_job.py:410` (`PublicationLedger`).
  - Conexão administrativa independente (fora do BFF): CLI `tools/staff_ops/` (`assignment.py:127,303` install/activate, `job.py`, `facts.py`, `rows.py`, `task_read.py`) + `tools/staff_materials/` (`assignment_trust.py`, `pki.py`, `native_secret.py`); migration `0014_staff_assignment_authority.py`.
- **Testes:** `test_vendor_membership_administration.py` (19); `test_vendor_membership_administration_postgres.py`; `test_vendor_membership_publication_job.py`; `tests/unit/gateway/test_provider_membership_administration.py` (16); `tests/integration/gateway/test_provider_membership_administration_live_pg.py` (12, lane PG real); `test_membership_publication_job_live_pg.py`; `test_assignment_publisher_codec.py`.
- **Viva significa — e aqui está a cerca:** para esta área, "viva" NÃO pode significar expor mutação administrativa por rota de portal: isso viola o cerco ADR-0049/ADR-0063 (`portal/admin/__init__.py:1`; `portal/admin/assignments.py:1-8`). "Viva" só é atingível como **painel read-only de vínculos/permissões governados**, o que exige uma NOVA decisão de desenho de autoridade de leitura (qual capability, qual perfil de leitura revisado, qual recorte por papel) — é decisão de dono/arquitetura, não pacote de engenharia.
- **Disposição: DISPOR-DEFER** (cerca de autoridade; condicionado a decisão do dono) — esforço do eventual pacote **M-L** (a leitura governada é desenho novo; a mutação fica permanentemente na conexão independente).
- **Pacote seguinte (condicionado; 1 PR):** painel `administration` read-only de vínculos via NOVO perfil de leitura revisado (nunca `portal/admin/*`, nunca rota de mutação), atrás de flag default-off — **só após** o dono registrar a decisão de autoridade de leitura (sugerido como pendência D-\* no §HDR do programa). Sem essa decisão, a área permanece stub e o registro desta disposição é a resposta do 5.5 para ela.

## §4 Tabela-síntese — 7×disposição (soma checada: 7)

| # | Área (`StaffArea`) | Estado hoje | Disposição | Esforço | Pacote seguinte (1 PR) |
|---|---|---|---|---|---|
| 1 | overview | VIVO (`PortalSessionExperience.tsx:162-172`) | DISPOR-DEFER | — | nenhum exigido |
| 2 | my-work | VIVO (`:173-183`) | DISPOR-DEFER | — | nenhum exigido |
| 3 | team-queues | VIVO (`:173-183`, reassign governado) | DISPOR-DEFER | — | nenhum exigido |
| 4 | cases | VIVO (`:184-192`; rota própria; DL-0149) | DISPOR-DEFER | — | nenhum exigido |
| 5 | documents | STUB (`:193-194`; backend de documentos existente e roteado) | **IMPLEMENTAR-DARK** | M | painel staff read-only atrás de flag |
| 6 | operations | STUB (`:195-205`; só texto estático) | **IMPLEMENTAR-DARK** | M | painel recibos+dependências atrás de flag |
| 7 | administration | STUB (`:206-207`; backend vivo FORA do portal, cercado) | **DISPOR-DEFER** (cerca; condicionado a decisão do dono) | M-L | read-only de vínculos, pós-decisão de autoridade |

**Soma:** 4 VIVO + 3 STUB = 7 áreas conferidas. Disposições: 2 IMPLEMENTAR-DARK + 5 DISPOR-DEFER (das quais 1 condicionada a decisão) = 7. ✔

**BFF de assignments (o "+" do enunciado do 5.5):** VIVO e wired de ponta a ponta — browser `assignmentClient.ts` (379 linhas) ↔ rotas BFF `decisions.py:224-226,256-258,295-297` ↔ runtime `AssignmentRuntime` (`gateway/human/assignment_composition.py:28`, composto em `app.py:21,225,247-250`, ciclo de vida `:273-279`) ↔ outbox/relay mTLS → engine; UI `TaskOwnershipControls.tsx`. Não é stub — ver §6 divergência (iii).

## §5 A conta do alvo D-12 (80% ≥implemented, inclui admin 7/7 vivo)

- **Hoje:** 4/7 áreas vivas (57,1% das áreas admin). O BFF de assignments já está vivo.
- **Com os 2 pacotes IMPLEMENTAR-DARK desta disposição (documents + operations):** 6/7 vivas (85,7%).
- **7/7 (o que D-12 literalmente inclui)** exige a área administration viva, que é **cerca-bloqueada**: qualquer versão viva exige nova decisão de autoridade de leitura admin (§3.7). A conta que sustenta o alvo é, portanto, **condicional**:
  - 7/7 = 4 vivas + documents + operations + **administration pós-decisão D-\*** (pacote M-L adicional, não é o mesmo PR de read-only simples);
  - sem a decisão, o teto executável é **6/7 (85,7%)** e D-12 precisa de emenda de escopo do dono ("admin 6/7 + cerca documentada") — emenda que este mapa NÃO faz; apenas a quantifica.
- **Lista de corte pré-acordada (risco 5 do programa: "só stubs W5 … são cortáveis"):** as 3 áreas stub são exatamente as cortáveis; com documents+operations implementados, o corte residual cai sobre administration apenas — o que reduz o raio do corte de 3 áreas para 1.

## §6 Divergências plano ↔ código (registro literal)

1. **"admin 7/7" não é 7 módulos admin** — são as 7 `StaffArea` do workspace staff (`PortalNavigation.tsx:5-12`). O mapa adota esta contagem como literal do código.
2. **"Documents/Operations/Administration hoje stubs" — CONFIRMADO** com precisão: `PortalSessionExperience.tsx:193-194`, `:195-205`, `:206-207`. As outras 4 áreas já estão vivas.
3. **"BFF de assignments" NÃO é stub** — o runtime está composto em produção (`app.py:247-250`), as 3 rotas+recibo estão montadas (`decisions.py`) e o cliente web existe com testes. O que existe de "não-BFF" é o serviço administrativo de fonte revisada (`portal/admin/assignments.py:80`), que **deliberadamente não tem rota BFF** (cerca `:1-8`) e é operado por conexão administrativa independente (`tools/staff_ops`). Leitura correta do 5.5: *stubs = as 3 áreas de UI; BFF de assignments = substrato já vivo que os pacotes seguintes consomem*.
4. **Fora do escopo 5.5, mas registrada por contágio de citação:** a referência de 5.1 a `settings.py:337-591` não bate com nenhum `settings.py` atual (o maior tem 195 linhas: `src/maezo/runtime/worker_runtime/settings.py`). Nenhum arquivo de settings atual contém o range citado — a linha do plano está driftada (provável snapshot de 2026-10-23). O pacote 5.1 deve re-ancorar a citação antes de despachar.
5. **Capacidades de sessão:** o conjunto fechado é `identity|staff_cases|human|vendor` (`records.py:90`), 4 valores — nenhuma das 3 áreas stub tem capability própria; documents/operations reusam `human`/`staff_cases` e administration não é gated por capability nenhuma (ramo `else`). Os pacotes seguintes precisam decidir se reusam capabilities existentes (recomendado para documents/operations) ou criam capability nova (só se a decisão D-\* de administration o exigir).

## §7 Cercas respeitadas e fora de escopo

- Este pacote **não toca**: PLANS.md, COORDINATION.md, `docs/processes/New_BPMN/*_v3.bpmn`, `docs/processes/provider-orchestrators/`, `.claude/` do checkout principal (WIP do orquestrador paralelo).
- Nenhuma flag flipada; nenhum shadow program; nenhum flip dark→enforcing (ROLL é onda própria).
- `portal/admin/*` permanece "never public portal routes" — nenhuma recomendação deste mapa o contradiz.
- Snapshots de auditoria consumidos sob o protocolo casa (cópia + sha256 antes da leitura): `INVENTORY.md` `30dee152…`, `INVENTORY-B-COMMITMENTS.md` `d9c7c784…`, `MASTER-PLAN-ANNEX.md` `8d1d4be7…`, `TRACEABILITY.md` `87694ddf…`, `GATE-VW5-TIER2-BLIND-VERDICT.md` `573069128…`, `FINDINGS.md` `ba2ed41e…`, `COMPLETENESS-PLAN.md` `30f29df1…`. O anexo (`MASTER-PLAN-ANNEX.md:53`) mapeia "16 flags OFF + stubs admin" para CLNT-5.1/5.5/5.6 — este documento fecha a parte 5.5 daquela linha.

## Histórico

- 2026-10-24: mapa e disposição escritos (pacote W5 CLNT 5.5, fase mapa; zero comportamento).
