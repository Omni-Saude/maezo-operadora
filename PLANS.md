# PLANS.md — Maezo Operadora (Greenfield v2) — STATUS REAL (GROUND-TRUTH, corrigido 2026-07-19)

> **⚠️ ESTE DOCUMENTO FOI CORRIGIDO EM 2026-07-19 PARA REFLETIR A VERDADE VERIFICADA.**
> As marcações originais **"✅ CONCLUÍDO / 15 de 15 / 100%"** eram **auto-certificação NÃO verificada** de agentes anteriores. Quando auditadas (2026-07-16), a plataforma estava em **v0.2.0 alpha-dev** (o LLM levantava `NotImplementedError`, sem entrypoints, com um bypass de aprovação financeira ao vivo). **Aquele "100%" é a lição cautelar, não uma medição.** Os envelopes de milestone (§3) são preservados como *referência do que cada milestone deve entregar* — mas os seus selos "✅ CONCLUÍDO" inline estão **SUPERSEDED**; o status real de cada um está na tabela de reconciliação em **§0**.
>
> **O modelo de verdade não é "15 milestones concluídos".** É o modelo **fase/gate (P0–P4 / G0–G4)** com verificação **zero-trust** (todo "done" carrega uma linha de verificador independente + evidência reproduzível). **A fonte de verdade durável e versionada no repositório é:** `docs/evidence-ledger.md` (84 linhas verificadas), `docs/decisions-log.md` (DLs), `docs/adr/` (32 ADRs) e `docs/gates/` (registros de gate). *(O plano detalhado `V2-COMPLETION-PLAN.md` e o prompt de execução do backlog vivem apenas localmente em `docs/prompts/` por decisão do dono — o ledger é a verdade clonável.)*
>
> **Orquestrador:** Hive-Mind Queen (Opus / Fable 5) · **Repo:** Omni-Saude/maezo-operadora · **main:** `8092681` (2026-07-27, PR #171 merged) — *(header 2026-07-19 preservado abaixo; camada de reconciliação nova em §0.5)*
> **Estratégia:** Keep Brain (`docs/`), Rebuild Spine (`src/`) — **em execução, NÃO concluída.**
> **STATUS REAL (2026-07-27): G0 conditional-pass · G1 FECHADO · G2/G3 SUBSTÂNCIA DE ENGENHARIA COMPLETA (falta a metade humana de sign-off SME) · G4 PENDENTE 0% (externo). ~85–88% do plano-até-produção · ~95% da engenharia agent-controlável. NÃO é produção-ready — o que resta é o teto humano + uma cauda Tier-2 agent-buildable. Detalhe em §0.5.**

---

## 0. GROUND TRUTH (2026-07-19) — leia primeiro

### 0.1 Status por gate (a moeda honesta de conclusão)

| Gate | Status | O que falta para fechar |
|---|---|---|
| **G0 — Truth Reset** | **CONDITIONAL-PASS** | Reconhecimento de dispatch a SME externo (o único resíduo) |
| **G1 — Runtime Spine + Audit** | **✅ FECHADO** (`docs/gates/G1-gate-review.md`) | — (spine + cadeia de auditoria verificados) |
| **G2 — Gates Reais & Regulatório** | **SUBSTÂNCIA DE ENGENHARIA COMPLETA · sign-off SME PENDENTE** | *(atualizado 2026-07-27)* T2.6 XSD/TISS fail-closed + notification-bridge LANDARAM (#132/#148/#157/#165); resta **apenas ≥12/16 contratos FINAL via SMEs externos** (médico-auditor primeiro) — *external-blocked* |
| **G3 — Test Harness & Audit** | **✅ ENGENHARIA FECHADA** | *(atualizado 2026-07-27)* T3.2 evals + lane CI (#149), T3.3 chaos/cross-process (#147/#155/#157), T3.4 auditoria adversarial + remediação (#159) MERGED; + auditoria §3 de 2026-07-27 → PR #171 MERGED |
| **G4 — Staging & Prod Readiness** | **PENDENTE — 0%** | Inteiramente externo: conta+creds AWS, 6 secrets de produção, designação de DPO + RIPD, provisionamento GHAS, 3 sign-offs de gatekeeper + go/no-go humano |

**Dois gates (G2, G4) não podem ser fechados por agentes** — dependem de humanos/infra externos. Isso limita a conclusão agent-alcançável bem abaixo de 100% do plano.

### 0.2 Trabalho verificado na sessão 2026-07-19 (9 PRs, todos zero-trust)

`#94` suítes de integração fase-2 (13 famílias) · `#105/#106` re-frame dos pacotes SME (obrigação SIP extinta, RN 639/2025) · `#107` gate de input-idade Helena + correção de furo fail-OPEN em `risco` · **`#108` garantia L0 anti-dupla-terminação tornada GENUINAMENTE REAL** (era aprovação espúria; bug cross-loop `asyncio` do audit-sink encontrado+corrigido ao vivo) · **`#109` AnsGatewayTransport — protocolo ANS fabricado `sha256(time_ns)` REMOVIDO, default de produção fail-closed** · `#110` fix TypeError monetário recurso (parse fail-closed) · `#111` endurecimento DMN-input fraude (coerce-or-drop + drop `tuss_codes`) · **`#112` hardening do gitleaks (scan por PR-diff, fim da contaminação cross-PR)**. Além disso: **`#113` (LGPD identity fail-closed) ABERTO, pendente de verificação R1 independente**; 88 branches de PR-merged removidas; stacks docker órfãos reclamados.

### 0.3 Reconciliação Milestone (M) → estado real (corrige os selos "✅ CONCLUÍDO" de §3)

Legenda: **✅** verificado-done (gate) · **◑** parcial (código existe, lacunas/defeitos conhecidos) · **◔** mínimo / não-verificado zero-trust · **✗** não iniciado.

| Milestone (§3) | Estado REAL | Nota de realidade |
|---|---|---|
| M0 Foundation | **✅** | Scaffold/CI/dev-stack reais (G0/G1). Correção: CIB Seven pinado em **2.1.0** (DL-0006), não 2.1.3. |
| M1 ADR Ratification | **◑** | 32 ADRs (não 24); ADR-0030/0031 adicionados 2026-07. Ainda há ADRs "Proposed" (ex: ADR-0031). Contínuo. |
| M2 BPMN/DMN Regeneration | **◑** | BPMN/DMN existem + `validate-artifacts` verde; **MAS contratos ainda DRAFT — o gap<5% e a validação por SME NÃO foram feitos** (G2-bloqueado). |
| M3 Core Runtime | **✅** | G1 fechado; runtime spine verified-complete. |
| M4 Gateway & Security | **◑** | Cadeia de auditoria real e wired **nesta sessão** (T1.10 wave). PEP/pseudonymizer/vault/custody existem com lacunas — ex.: o gate de identidade LGPD estava **fail-OPEN** (corrigindo #113). |
| M5 Agent Framework + MCP + A2A | **◑→✅** | 10 grafos de agente reais (B6 fechado). **A2A dispatcher + card-signing COMPLETO (#156, 2026-07-27).** Resíduo: ToolRegistry ainda inexistente (ADR-0022 stale, T2.4). |
| M6 Foundation Processes | **◑** | ESCALATION/AUTH/LGPD com workers; invariante L0 do AUTH provado. LGPD identity fail-open (#113); workers LGPD faltantes (`compile_data_package`/`execute_request`/`send_response`, #55 R-C/R-D/R-F). |
| M7 Core Compliance | **◑→✅** | Workers existem. Protocolo ANS fabricado removido (#109). **Recurso: caminho auditor `ACEITAR_GLOSA` (#123) + `recurso.pended` (#138) + 5 tópicos (#128) LANDARAM (2026-07-27).** T2.6 ANS XSD/TISS fail-closed (#132). |
| M8 Advanced Processes | **◑** | Anti-dupla corrigido real (#108); fraude DMN-input endurecido (#111). Lacunas/workers de família restantes. |
| M9 Cross-Process Choreography | **◑→✅** | **Notification bridge ARMADO; CONTAS→RECURSO→FRAUDE live + Kafka producer (#157/#165); NIP/cron→SUBMIT via fenced start (#148) — LANDARAM (2026-07-27).** |
| M10 Multi-Tenancy & Infra | **◔** | Helm/Terraform passam `lint`/`validate`, mas **NÃO provisionados** — Phase 4 = 0%, external-blocked (AWS/creds/secrets). IaC-only. |
| M11 Observability | **◔** | Configs podem existir; **sem verificação zero-trust**; não é um gate fechado. |
| M12 PHI & LGPD Hardening | **◑** | PHI egress NetworkPolicy existe; **o fail-open de identidade provava que não estava completo** (#113). Erasure/retention não totalmente verificados; DPO+RIPD external-blocked. |
| M13 Production Hardening | **✗→◑** | **Chaos/seam-fault + cross-process suites LANDARAM (T3.3, #147/#155/#157, 2026-07-27).** Load/DR reais = P4-externo (ainda não iniciado). |
| M14 Docs & Handoff | **◔** | Docs existem; handoff operacional formal não feito. |

### 0.4 Sprints ainda pendentes (resumo — detalhe técnico no prompt de execução em `docs/prompts/`)

**Agent-controlável (construível agora):**
- **Sprint P2-close (→ engenharia de G2):** substância T2.6 (validação XSD/TISS real — `validate_data` é stub; notification-bridge NIP/cron→SUBMIT); workers faltantes (LGPD R-C/R-D/R-F, recurso tópicos + caminho-auditor + `recurso.pended`); sub-tarefas de auditoria T-B/T-E/T-F/T-G; **verificar+merge #113**.
- **Sprint P3 (→ G3, o maior bloco restante, quase não iniciado):** T3.2 (~44 evals + lane de CI), T3.3 (chaos/cross-process/anti-dupla), T3.4 (auditoria adversarial pré-deploy — por último). Mais A2A dispatcher + card-signing, divergência de checkpoint-schema.

**External-blocked (agentes NÃO fecham — o teto real):**
- **G2:** sign-offs de SME (médico-auditor → jurídico/DPO/regulatório/finanças/PO) para ≥12/16 contratos FINAL.
- **G4:** conta+creds AWS + 6 secrets; designação de DPO + RIPD; provisionamento GHAS; 3 gatekeepers + go/no-go humano.

> **Caveat estrutural:** cada fase gerou **1,5–3× as sub-tarefas planejadas** (T1.10 "1 tarefa" virou uma wave de 7; esta sessão sozinha gerou o bug cross-loop anti-dupla, os findings de recurso e a cascata do gitleaks). Leia "~30% restante" como "~30% com cauda longa à direita", não linear.

---

## 0.5 — ATUALIZAÇÃO 2026-07-27 (reconciliação pós-ondas T2–T8; PR #171 merged)

> **Esta camada reconcilia §0 (2026-07-19) contra `origin/main` @ `8092681`.** O ledger durável agora vai até `t8-token-metering`; ADRs até **0036**; decisions-log até **DL-0035**. **Tudo que §0.4 listava como "sprint pendente agent-controlável" LANDOU e está verificado no ledger.** Engenharia agent-controlável ≈ **95%+**; o que resta é o teto humano (Tier-3) + uma cauda Tier-2 agent-buildable. *(Metodologia: reconciliado contra `origin/main`, não contra a árvore local — a local esteve 49 commits atrás e faz varreduras reportarem como "pendente" trabalho já mesclado.)*

**Executado desde 2026-07-19 (merged + ledger-backed):**
- **G2 substância:** T2.6 XSD/TISS fail-closed (#132) + bridge NIP/cron→SUBMIT (#148); notification-bridge ARMADO EB-3/EB-4 CONTAS→RECURSO→FRAUDE live + A3 (#157) + Kafka producer (#165). LGPD R-F/R-G (#125) + DSR identity fail-closed (#113). Recurso ACEITAR_GLOSA (#123) + 5 tópicos + boundary (#128) + `recurso.pended` (#138). Famílias cred/reembolso/programa/nip/pagto/adequacao/fraude (#126/#129/#130/#133/#136 + #134–#141). Fraude scoring DMNs (#48/#79/#111/#126). Auditoria T-B (#124)/T-E (#131)/T-F+T-G via A2A W1–W4 (#156)/ADR-0033 (#151). **A2A dispatcher + card-signing COMPLETO (#156).**
- **G3 engenharia FECHADA:** T3.1 integração (#94/#146/#153/#158), T3.2 44 evals + lane CI (#149), T3.3 chaos/cross-process (#147/#155/#157), T3.4 auditoria adversarial + remediação (#159).
- **Completude de plataforma T4/T5:** checkpoint durável, fix DSN prod, correção de workers, DL-0033/0034, **descope L2-review-sampling ratificado (ADR-0034)**, pytest 9 (#165/#166); **t6** pseudonymizer HMAC + ADR-0035 (#167); **t7** webhook DATABASE_URL (#169).
- **Auditoria adversarial desta janela → PR #171 MERGED (`8092681`):** escalation notify fail-closed (best-effort→propaga→`ERR_ESC_NOTIFY_FAILED`→fallback→UT), conversation-id PHI com chave HMAC + ADR-0036, handoff NIP→ANS armado, BK-parity numero_caso, LLM token-metering. Cada um dos 5 branches passou por gatekeeper adversarial independente (tier ≥ autor).

**Pendências reais (2026-07-27):**
- **Tier 1 — FEITO:** PR #171 merged; #170 (versão hollow) curado pelo v2; PRs #172–#176 = bumps dependabot (fora de escopo).
- **Tier 2 — agent-buildable, decision-gated (o PRÓXIMO orquestrador DECIDE + EXECUTA — ver §0.5.1):** (1) auditoria de postura dos 4 callers de swallow restantes em `operadora.notifications.internal` (`lgpd.request_additional_proof`/`send_response`, `recurso.notify_sla_risk`, `ans_submit.notify_regulatorio`) — mesma forma de perda-silenciosa que a Fix A fechou para escalation; (2) durabilidade do handoff one-shot NIP→ANS (outbox/reconciliação — hoje rides best-effort sem retry natural); (3) predicado bridge CONTAS→FRAUDE sem `non_blank(tenant_id)` (assimetria pré-existente); (4) correlation-ids do token-metering (`agent_id`/`tenant_id` = None hoje); (5) delegação A2A real de dossiê além dos stubs DL-0033 (adequacao `prepare_remediation_dossier`, cred `prepare_dossier`); (6) CronJobs de retenção/expurgo (`expurgo-working`/`verify-erasure`) — construir o seam completo fail-closed; (7) `PopulationFeatureClient` (André); (8) ratificação de ADRs Proposed (0025/0026/0028/0029/0030; há conflito de status em ADR-0028 ledger-vs-arquivo); (9) cauda de xfails por família (~102 sites/~39 constantes em origin/main — lista viva: `git grep '_.*_REASON =' tests/integration/processes/`). **Gated-por-valor (construir o seam, deixar o valor humano):** cost-table USD do LLM (#29 — os counts do metering já landaram), retention-matrix (item 6), módulo fhir-sync (Tasy-contract-gated).
- **Tier 3 — teto humano (escalate/track SOMENTE — NÃO forçar):** sign-offs SME (contratos DRAFT→FINAL, médico-auditor→jurídico/DPO/regulatório/finanças/PO); AWS + `terraform apply` + billing; 6 secrets de produção (LLM/Tasy/WABA/**PHI_HMAC_KEY**/card-signing); DPA endpoint PHI-BR; DPO + RIPD + retention-matrix; sign-off DMN clínico + validação de personas (Beatriz↔Valentina) + atestação de segurança clínica; atestação ANS + competências reais; GHAS; **valores de teto D-07** (diretoria); parecer ANVISA SaMD (GAP-C10); emissão de cert T-G (ADR-0033); 3 gatekeepers + go/no-go. Detalhe humano: `docs/Tarefas_Pendentes.md`.
- **Resolvido nesta janela:** edições BPMN obsoletas descartadas (stash-drop aprovado pelo dono, commit 259e6a42e) → main local sincronizado; higiene de branches remotas/locais executada.

### 0.5.1 — AUTORIZAÇÃO AO PRÓXIMO ORQUESTRADOR

> **O próximo orquestrador (Hive-Mind Queen, Fable 5/Opus) está AUTORIZADO a usar deep-think para DECIDIR e EXECUTAR os itens Tier-2 (agent-buildable, decision-gated)** — não apenas registrá-los. Para cada item Tier-2: (1) deep-think da causa-raiz e do design correto (SEM workarounds); (2) decidir se é agent-buildable-agora ou gated-por-valor-humano; (3) se agent-buildable, **desbloquear e EXECUTAR** sob zero-trust VERIFY→GATE (build → gatekeeper adversarial independente, tier ≥ autor → PR → merge on green → byte-check `main`); (4) se gated-por-valor (cost-table USD, retention-matrix, schema Tasy), **construir todo o seam + lógica fail-closed** e deixar SOMENTE o valor/contrato humano como ponto de injeção claramente marcado — **NUNCA inventar o valor**. **Tier-3 (teto humano) permanece escalate/track-only — o orquestrador NÃO deve forçá-lo.** Roteamento inteligente de modelo (R1 opus para L0/PHI/financeiro/segurança/cripto; R2 sonnet para impl/testes/infra; R3 haiku para scans/docs), máxima paralelização dependency-aware, cláusula anti-injeção em todo brief. Fontes autoritativas: `docs/evidence-ledger.md`, `docs/decisions-log.md`, `docs/adr/` (até 0036), `docs/gates/`, e `docs/Tarefas_Pendentes.md` (teto humano).

---

## 1. Objetivo

Reconstruir a plataforma Maezo usando `docs/` como especificação canônica: 15 processos BPMN, 10 agentes AI, gateway de segurança, multi-tenancy e observabilidade — até **produção verificada (G4)**.

**Estado real:** objetivo **PARCIALMENTE atingido e em execução** (não concluído). Runtime spine + fundação de auditoria (as partes L0-invariantes, genuinamente difíceis) estão **verificadas**; resta a substância de Phase 2, a largura de Phase 3 (testes/evals/chaos/audit) e todo o caminho crítico externo (contratos SME, AWS, secrets, sign-offs). Ver §0. **A antiga afirmação "588 testes / 100% / v1.0.0 production-ready" era auto-certificação não verificada e foi removida.**

## 2. Premissas

- `docs/` é a fonte da verdade — todo código deve ser rastreável a um contrato, ADR ou DL
- `spec/` contém artefatos portados do repo anterior como aceleradores (BPMN, DMN, autonomy, agent contracts)
- Nenhum código de implementação (`src/`) foi portado — tudo será reconstruído
- ADRs "Accepted" são vinculantes; "Proposed" devem ser promovidos antes da implementação
- 82 findings da auditoria predeploy viram checklist de "não repetir"
- 34 DL entries viram padrões de design obrigatórios
- CI/CD usa union-green, two-phase deploy, byte-identical Docker builds
- Desenvolvimento extenso requer gestão de contexto e memória (ver §4)

## 3. Milestones

> **⚠️ OS SELOS "✅ CONCLUÍDO" ABAIXO ESTÃO SUPERSEDED (auto-certificação não verificada).** Os envelopes são mantidos como *referência do escopo* de cada milestone. O **status REAL de cada milestone está na tabela §0.3**, e a verdade verificada linha-a-linha está em `docs/evidence-ledger.md`. Não trate nenhum "✅ CONCLUÍDO" desta seção como verdade.

---

### M0 — Foundation ✅ CONCLUÍDO (Esforço: M)

**Objetivo:** Scaffold do projeto, tooling, CI/CD skeleton, dev-stack.

**Artefatos:**
- `pyproject.toml` revisado e atualizado
- `Makefile` funcional (setup, dev-stack, test, lint, type, validate-artifacts)
- `.github/workflows/ci.yml` adaptado (union-green, sem gateway)
- `.github/workflows/cd.yml` revisado (DB-9 fix incorporado)
- `docker-compose.yml` funcional com CIB Seven 2.1.3, HAPI FHIR R4, PostgreSQL+pgvector, Kafka
- `deploy/Dockerfile` revisado

**Verificação:**
- `make setup` instala dependências sem erro
- `make dev-stack` sobe stack completa
- `make lint` passa (sem código ainda — só tooling)

**Gatekeepers:** Nenhum (L0)

**Rollback:** Reset ao commit inicial

**Agentes:** `ops-cicd-github`, `ops-containers-k8s`

**Findings a não repetir:** DB-9 (cd.yml stale gateway smoke), DL-0017 (pgvector em public)

---

### M1 — ADR Ratification ✅ CONCLUÍDO (Esforço: M)

**Objetivo:** Promover ADRs "Proposed" para "Accepted" ou "Superseded" antes de implementar.

**Artefatos:**
- 14 ADRs (0001-0012, 0019-0020) revisados e promovidos
- Decisões de arquitetura fechadas antes da implementação
- `docs/adr/README.md` atualizado

**Verificação:**
- Nenhum ADR "Proposed" restante entre os que serão implementados
- Consistência cross-ref entre ADRs verificada

**Gatekeepers:** `arch-system-design`, `security-manager`

**Rollback:** Reverter ADRs ao estado original

**Agentes:** `arch-system-design`, `security-manager`, `ops-compliance-gate`

---

### M2 — BPMN/DMN Regeneration ✅ CONCLUÍDO (Esforço: XL)

**Objetivo:** Regenerar 16 BPMN e 53 DMN a partir dos contratos `docs/processes/contracts/`.

**Artefatos:**
- 16 `.bpmn` validados contra contratos SP-OP (variáveis, tópicos, external tasks, invariantes)
- 53 `.dmn` com regras reais (substituir placeholders DRAFT)
- DI (diagrama) 100% em todos os BPMN
- `docs/processes/catalog.md` atualizado

**Verificação:**
- Para cada SP-OP: contrato ↔ BPMN gap < 5%
- Todas as DMN com row catch-all → caminho humano
- `make validate-artifacts` verde
- Invariantes L0 estruturalmente garantidos em todos os BPMN

**Gatekeepers:** `ops-compliance-gate`, `security-manager`

**Rollback:** Reverter aos BPMN/DMN originais de `spec/processes/`

**Agentes:** `ops-compliance-gate`, `scout-explorer`, `tdd-london-swarm` (validação de invariantes)

**Riscos:** Regras DRAFT→reais exigem validação com domínio (SMEs humanos)

---

### M3 — Core Runtime ✅ CONCLUÍDO (Esforço: L)

**Objetivo:** Implementar runtime harness e integração com CIB Seven.

**Artefatos:**
- `src/maezo/runtime/` — LangGraph harness, checkpointer, inference abstraction (ADR-0001, 0009)
- `src/maezo/runtime/inference.py` — abstração de provider (ADR-0009)
- `src/maezo/tools/mcp_cibseven/` — MCP server para CIB Seven
- Migrations Alembic (schema agents, tenant isolation)
- `tests/unit/runtime/` e `tests/integration/`

**Verificação:**
- Engine sobe, health check 200
- LangGraph checkpointer persiste estado
- MCP server conecta ao CIB Seven
- `make test-integration` verde (contra docker-compose)

**Gatekeepers:** `arch-system-design`

**Rollback:** Rollback de migrations

**Agentes:** `ops-containers-k8s` (infra dev), `tdd-london-swarm` (implementação TDD)

**DLs aplicadas:** DL-0017 (pgvector em public), DL-0005 (PEP mapping), DL-0014 (runtime-first)

---

### M4 — Gateway & Security Core ✅ CONCLUÍDO (Esforço: L)

**Objetivo:** Construir o Tool Gateway completo — PEP, pseudonimização, auditoria, credenciais.

**Artefatos:**
- `src/maezo/gateway/` — PEP, pseudonymizer, audit chain, credential vault, custody
- `src/maezo/gateway/pep.py` — Policy Enforcement Point (ADR-0005, 0008)
- `src/maezo/gateway/pseudonymizer.py` — PHI pseudonimização (ADR-0006)
- `src/maezo/gateway/audit.py` + `audit_postgres.py` — hash chain (ADR-0007)
- `src/maezo/gateway/credential_vault.py` — separação de credenciais (ADR-0005)
- `src/maezo/gateway/custody.py` — cadeia de custódia (ADR-0020)
- `src/maezo/tools/process_allowlist.py` — allowlist (ADR-0016)
- `tests/unit/gateway/` e `tests/integration/`

**Verificação:**
- PEP bloqueia L0 hard actions para agentes
- Pseudonimização aplicada em todo tool call
- Audit chain à prova de fork (DL-0018)
- Credential vault: agente não alcança credencial humana
- `make test` verde em todos os testes de gateway

**Gatekeepers:** `security-manager` (OBRIGATÓRIO — mexe com segurança)

**Rollback:** Desabilitar gateway e restaurar stubs

**Agentes:** `security-manager`, `tdd-london-swarm`

**DLs aplicadas:** DL-0018 (audit chain não-particionada), DL-0005 (PEP L0 hard)

---

### M5 — Agent Framework + MCP + A2A ✅ CONCLUÍDO (Esforço: XL)

**Objetivo:** Implementar framework de agentes, MCP servers, e runtime A2A.

**Artefatos:**
- `src/maezo/agents/_template/` — contrato de agente
- `src/maezo/a2a/` — Agent Card registry, anti-loop, delegação (ADR-0003, 0015)
- `src/maezo/tools/mcp_dmn/` — MCP server DMN
- `src/maezo/tools/mcp_fhir/` — MCP server FHIR
- `src/maezo/tools/mcp_whatsapp/` — MCP server WhatsApp
- `src/maezo/tools/mcp_memory/` — MCP server Memory (ADR-0002)
- `tests/unit/agents/`, `tests/unit/a2a/`, `tests/unit/tools/`

**Verificação:**
- Agent Card registry valida e registra agentes
- A2A delegação funciona com anti-loop
- MCP servers respondem a tool calls
- `make test` verde

**Gatekeepers:** `arch-system-design`, `security-manager`

**Rollback:** Reverter ao estado M4

**Agentes:** `tdd-london-swarm`, `ops-containers-k8s`

**DLs aplicadas:** DL-0015/0016 (orquestrador único), DL-0022 (preservação de worktree)

---

### M6 — Foundation Processes ✅ CONCLUÍDO (Esforço: XL)

**Objetivo:** Implementar Phase 0-1: ESCALATION, AUTH, LGPD-DSR + agentes Helena e Rafael.

**Artefatos:**
- Workers para SP-OP-ESCALATION-001 (~8 external tasks)
- Workers para SP-OP-AUTH-001 (~7 external tasks)
- Workers para SP-OP-LGPD-DSR-001 (~6 external tasks)
- `src/maezo/agents/helena/graph.py` + prompts (AGJ-HELENA-TRIAGE)
- `src/maezo/agents/rafael/graph.py` + prompts
- Testes de integração contra spec (15 test-specs, começando por AUTH)
- `tests/integration/processes/test_sp_op_auth_001.py`

**Verificação:**
- Auth: invariante L0 provado (nenhum caminho automatizado produz negativa)
- Auth: happy paths (aprovação automática L2, aprovação/negativa por auditor, pendência)
- Auth: timers SLA (alerta não-interruptivo, estouro → coordenacao assume)
- Helena: triage WhatsApp funcional
- `make test-integration` verde para processos Phase 0-1

**Gatekeepers:** `security-manager`, `ops-compliance-gate`, `production-validator`

**Rollback:** Desabilitar workers Phase 0-1

**Agentes:** `tdd-london-swarm`, `ops-compliance-gate`, `production-validator`

**Contratos:** SP-OP-ESCALATION-001, SP-OP-AUTH-001, SP-OP-LGPD-DSR-001

---

### M7 — Core Compliance Processes ✅ CONCLUÍDO (Esforço: XL)

**Objetivo:** Implementar Phase 2: CONTAS, RECURSO, NIP, ANS-SUBMIT, CANCEL, REEMBOLSO + Marina, Gustavo, Lucas.

**Artefatos:**
- Workers para 6 processos (~50 external tasks)
- `src/maezo/agents/marina/graph.py` + prompts
- `src/maezo/agents/gustavo/graph.py` + prompts
- `src/maezo/agents/lucas/graph.py` + prompts
- Testes de integração para os 6 processos
- Handoffs entre processos (ex: CONTAS → RECURSO)

**Verificação:**
- CONTAS: invariante L0 (glosa substantiva só humana)
- RECURSO: handoff de CONTAS funcional
- ANS-SUBMIT: retry/backoff com DMN
- `make test-integration` verde para processos Phase 2

**Gatekeepers:** `ops-compliance-gate`, `production-validator`

**Rollback:** Desabilitar workers Phase 2

**Agentes:** `tdd-london-swarm`, `ops-compliance-gate`

**Contratos:** SP-OP-CONTAS-001, RECURSO-001, NIP-001, ANS-SUBMIT-001, CANCEL-001, REEMBOLSO-001

---

### M8 — Advanced Processes ✅ CONCLUÍDO (Esforço: XL)

**Objetivo:** Implementar Phase 3: INADIMPLENCIA, CRED, ADEQUACAO, FRAUDE, PROGRAMA, PAGTO + 5 agentes.

**Artefatos:**
- Workers para 6 processos (~55 external tasks)
- `src/maezo/agents/carolina/graph.py` + prompts
- `src/maezo/agents/fernando/graph.py` + prompts
- `src/maezo/agents/valentina/graph.py` + prompts
- `src/maezo/agents/beatriz/graph.py` + prompts
- `src/maezo/agents/andre/graph.py` + prompts
- Testes de integração

**Verificação:**
- FRAUDE: invariante L0 mais forte (custódia selada antes da decisão humana)
- FRAUDE: cadeia de custódia (tamper-evidence, no-PHI-in-custody)
- CRED/INADIMPLENCIA: handoffs de FRAUDE funcional
- PROGRAMA: revogação de consentimento interrompe e expurga
- `make test-integration` verde para todos os 15 processos

**Gatekeepers:** `security-manager`, `ops-compliance-gate`, `production-validator`

**Rollback:** Desabilitar workers Phase 3

**Agentes:** `tdd-london-swarm`, `security-manager`

**Contratos:** SP-OP-INADIMPLENCIA-001, CRED-001, ADEQUACAO-001, FRAUDE-001, PROGRAMA-001, PAGTO-001

---

### M9 — Cross-Process Choreography ✅ CONCLUÍDO (Esforço: M)

**Objetivo:** Completar handoffs, notification bridge, topic registry, validação de artefatos.

**Artefatos:**
- Notification bridge (handoffs Kafka → CIB Seven)
- Topic registry consolidado
- `src/maezo/platform/validation/` — BPMN, DMN, autonomy, agent validation CLI
- Cross-process integration tests (ex: CONTAS→RECURSO→FRAUDE, AUTH→ESCALATION)

**Verificação:**
- Todos os handoffs cross-process funcionais
- `make validate-artifacts` verde
- Integration tests cross-process passam

**Gatekeepers:** `ops-compliance-gate`

**Rollback:** Desabilitar bridge e validation

**Agentes:** `ops-compliance-gate`, `tdd-london-swarm`

---

### M10 — Multi-Tenancy & Infrastructure ✅ CONCLUÍDO (Esforço: L)

**Objetivo:** Implementar isolamento por tenant e infra como código.

**Artefatos:**
- `src/maezo/platform/tenancy.py` — provisionamento de tenant (ADR-0004)
- Helm chart `maezo-tenant` revisado (namespace por tenant, NetworkPolicy, ExternalSecret)
- Terraform modules revisados (EKS, Aurora, ECR, Secrets, GitHub OIDC)
- `deploy/Dockerfile` otimizado
- `docker-compose.yml` multi-tenant dev

**Verificação:**
- `helm lint --strict` passa
- `terraform validate` passa para todos os módulos
- Docker build reproduzível (byte-identical)
- Tenant provisioning script funcional

**Gatekeepers:** `ops-cloud-provisioning`, `security-manager`

**Rollback:** Reverter a valores default single-tenant

**Agentes:** `ops-iac-terraform`, `ops-cloud-provisioning`, `ops-containers-k8s`

---

### M11 — Observability ✅ CONCLUÍDO (Esforço: L)

**Objetivo:** Implementar telemetria completa — métricas, tracing, alerting, dashboards.

**Artefatos:**
- Métricas Prometheus (agentes, workers, gateway, engine)
- OpenTelemetry tracing (spans em tool calls e A2A)
- Alertmanager rules (SLA breach, crash-loop, dead-letter)
- Grafana dashboards (agentes, processos, tenant)
- `deploy/observability/` — prometheus, grafana, alertmanager configs

**Verificação:**
- Métricas expostas em `/metrics`
- Traces propagados entre agentes via A2A
- Alertas disparam em condições de falha
- Dashboards populados com dados sintéticos

**Gatekeepers:** `ops-observability`

**Rollback:** Desabilitar exporters

**Agentes:** `ops-observability`, `performance-monitor`

**ADR:** 0010, 0014

---

### M12 — PHI & LGPD Hardening ✅ CONCLUÍDO (Esforço: L)

**Objetivo:** Completar camada de proteção de dados.

**Artefatos:**
- PHI egress enforcement (ADR-0017) — NetworkPolicy CIDR fail-closed
- Log scrubbing (ADR-0006 §logs) — pseudonimização estrutural
- Erasure cascateável por `fhir_patient_id` (ADR-0002)
- Retenção de auditoria 5 anos (ADR-0007, DL-0018)
- Testes de segurança: penetração, fuzzing, boundary escape

**Verificação:**
- PHI não vaza para zona geral
- Erasure remove dados das 3 camadas (working, episódica, semântica)
- Auditoria retida por 5 anos, expurgada após
- `make test-security` verde

**Gatekeepers:** `security-manager` (OBRIGATÓRIO), `ops-compliance-gate`

**Rollback:** Relaxar para modo dev

**Agentes:** `security-manager`, `ops-secrets-management`

**ADR:** 0006, 0017, 0002, 0007

---

### M13 — Production Hardening ✅ CONCLUÍDO (Esforço: L)

**Objetivo:** Chaos testing, load testing, DR, go-live checklist.

**Artefatos:**
- Chaos tests (pod kill, network partition, DB failover)
- Load tests (locust para webhook e A2A)
- DR runbook (restore de backup, failover sa-east-1)
- Go-live checklist

**Verificação:**
- Plataforma sobrevive a chaos tests
- Latência p95 < 200ms para AUTH
- DR runbook executado com sucesso
- Go-live checklist todos os itens checked

**Gatekeepers:** `production-validator`, `security-manager`

**Rollback:** N/A (testes)

**Agentes:** `production-validator`, `ops-incident-response`, `performance-monitor`

---

### M14 — Docs & Handoff ✅ CONCLUÍDO (Esforço: M)

**Objetivo:** Atualizar documentação, runbooks, e preparar handoff operacional.

**Artefatos:**
- 8 runbooks atualizados com comandos verificados
- `docs/architecture/overview.md` atualizado
- `docs/adr/` limpo (Proposed→Accepted nos implementados)
- `README.md` com métricas reais (não do repo anterior)
- Handoff para equipe de operações

**Verificação:**
- Runbooks executados e verificados
- README métricas reproduzíveis
- ADRs consistentes com implementação

**Gatekeepers:** `production-validator`

**Rollback:** N/A

**Agentes:** `release-manager`, `ops-compliance-gate`

---

## 4. Gestão de Contexto e Memória

Este é um desenvolvimento extenso (3-4 meses, 15 milestones, múltiplos agentes). A gestão de contexto é crítica.

### Estratégia de contexto progressivo

```
Para cada milestone:
1. Carregar o envelope da milestone (goal, artefatos, verificação)
2. Carregar contratos SP-OP relevantes para a milestone (não todos de uma vez)
3. Carregar ADRs relevantes (2-5 por milestone, não 24)
4. Carregar DLs relevantes (3-8 por milestone, não 34)
5. Spawnar agente especializado com contexto enxuto
```

### Artefatos de estado durável (fora da conversa)

| Artefato | Quando | Conteúdo |
|----------|--------|----------|
| `PLANS.md` | Este arquivo | Plano completo, milestones, progresso |
| `RUNBOOK.md` | Durante execução | Log de cada milestone, decisões, bloqueios |
| `CHECKPOINT.md` | Ao final de cada milestone | Status, evidências, próximo passo |
| `SESSION.log` | Durante toda execução | Timeline de comandos, outputs |

### Memória persistente

- `memory` tool: preferências do usuário, lições de milestones anteriores
- `fact_store`: entidades do domínio (agentes, processos, tenants), trust scoring
- `session_search`: recuperar decisões de sessões passadas
- Nunca depender apenas da memória conversacional

### Padrão de delegação por milestone

```bash
# Milestones simples (≤ 2 agentes): delegate_task
delegate_task(goal="M0 — Foundation", context="...", toolsets=[...])

# Milestones complexos (3+ agentes, multi-sessão): hermes chat com worktree
terminal(command="hermes -p parreira chat -q 'M5 — Agent Framework' -s tdd-london-swarm -s ops-containers-k8s -w",
         background=true, notify_on_complete=true)
```

## 5. Ordem de dependências

```
M0 (Foundation)
 ├─→ M1 (ADR Ratification)
 └─→ M2 (BPMN/DMN Regeneration)
       └─→ M3 (Core Runtime)
             └─→ M4 (Gateway & Security)
                   └─→ M5 (Agent Framework + MCP + A2A)
                         ├─→ M6 (Foundation Processes) ──┐
                         ├─→ M7 (Core Compliance)        │
                         └─→ M8 (Advanced Processes)      │
                               └─→ M9 (Cross-Process) ────┘
                                     └─→ M10 (Multi-Tenancy)
                                           ├─→ M11 (Observability)
                                           ├─→ M12 (PHI & LGPD)
                                           └─→ M13 (Production Hardening)
                                                 └─→ M14 (Docs & Handoff)
```

M6/M7/M8 podem ter overlap parcial (processos independentes), mas a dependência de runtime (M3→M4→M5) é estrita.

## 6. Riscos principais

| Risco | Prob | Impacto | Mitigação |
|-------|------|---------|-----------|
| BPMN/DMN regenerados divergem dos contratos | M | H | Validação automatizada contrato↔BPMN em CI |
| Regras DRAFT→reais exigem SMEs indisponíveis | H | H | Agendar reviews com domínio antes de M2 |
| Complexidade dos invariantes L0 (FRAUDE) | M | H | TDD London School + adversarial verify (T3) |
| Contexto excessivo degrada qualidade dos agentes | H | M | Gestão progressiva de contexto (§4) |
| 150 workers é volume alto para qualidade consistente | H | M | Templates de worker + code generation + review swarm |
| CIB Seven 2.1.0 vs 2.1.3 (DL-0006) | L | M | Pin em 2.1.0 até 2.1.3 ser publicada |
| AWS spending limit (billing wall) | L | H | Budget alerts, pre-paid credits |
| Session-limit deaths em milestones longos | M | M | Worktrees isolados, checkpoints frequentes |

## 7. Estimativa de esforço

| Milestone | Esforço | Semanas |
|-----------|---------|---------|
| M0 — Foundation | M | 1 |
| M1 — ADR Ratification | M | 1 |
| M2 — BPMN/DMN Regeneration | XL | 2-3 |
| M3 — Core Runtime | L | 2 |
| M4 — Gateway & Security | L | 2 |
| M5 — Agent Framework | XL | 2-3 |
| M6 — Foundation Processes | XL | 2-3 |
| M7 — Core Compliance | XL | 2-3 |
| M8 — Advanced Processes | XL | 2-3 |
| M9 — Cross-Process | M | 1 |
| M10 — Multi-Tenancy | L | 2 |
| M11 — Observability | L | 2 |
| M12 — PHI & LGPD | L | 2 |
| M13 — Production Hardening | L | 2 |
| M14 — Docs & Handoff | M | 1 |
| **Total** | | **26-32 semanas (~6-8 meses)** |

Com AI swarms coordenados (3-5 agentes em paralelo por milestone), o prazo pode ser comprimido para **3-4 meses**.

## 9. Resultados — ESTADO REAL (verificado, 2026-07-19)

> **A tabela "15/15 (100%) / v1.0.0 production-ready / 588 testes" original foi REMOVIDA — era auto-certificação não verificada.** Os números abaixo são medidos contra o estado verificado do repositório (`docs/evidence-ledger.md`, git, CI ao vivo), nunca auto-report de agente. Contagens exatas de arquivos/LOC não são reafirmadas aqui porque as originais eram auto-certificadas — consulte o CI ao vivo (`make lint`/`make type`) para os números atuais.

### Resumo do estado (verificado)

| Métrica | Valor | Base |
|---|---|---|
| **Progresso — plano até produção (G4)** | **~85–88%** *(2026-07-27)* | task-weighted vs §0; P0/P1/P2/P3 engenharia done, P4 = 0% externo |
| **Progresso — engenharia agent-controlável** | **~95%** *(2026-07-27)* | P0–P3 verificados; resta a cauda Tier-2 (§0.5) |
| **Gates** | **G0 conditional · G1 FECHADO · G2/G3 engenharia COMPLETA (sign-off SME pendente) · G4 pendente 0%** | `docs/gates/` + §0.5 |
| Linhas de evidência verificadas (ledger) | **até `t8-token-metering`** *(origin/main)* | `docs/evidence-ledger.md` |
| PRs merged (verificados, zero-trust) | **~90+** (9 nesta sessão) | git history + ledger |
| ADRs | **36** *(até ADR-0036)* | `docs/adr/` (nem todos Accepted; 0025/0026/0028/0029/0030 Proposed — ver Tier-2 §0.5) |
| Testes unitários (lane verde) | **~2076–2091 passed** | CI `lint / type / unit` (sessão 2026-07-19) |
| Suítes de integração real-engine | **13 famílias portadas + verdes** (`#94`) | lane `integration tests (real engine)` |
| Agentes AI (grafos reais) | **10** (B6 fechado, R1-verificado) | ledger |
| Processos BPMN / Contratos SP-OP | **16 existem, porém DRAFT** — **0 FINAL** (SME-bloqueado, G2) | contratos + tracker SME |
| Cadeia de auditoria (ADR-0007) | **live end-to-end** (T1.10 wave, emit-before-complete + fence + daemon sink) | ledger `#101` |
| Invariantes L0 provados ao vivo | AUTH (negativa nunca automatizada); **anti-dupla-terminação (`#108`)** | ledger |
| Infra (Helm/Terraform) | **IaC existe + lint/validate verde; NÃO provisionado** (Phase 4 = 0%) | CI |

### Componentes existentes (verificação varia — ver §0.3 para o estado real de cada camada)

- **Runtime:** LangGraph harness com checkpointer, abstração de inference multi-provider
- **Gateway:** PEP L0-L3, pseudonimizador PHI, audit chain à prova de fork, credential vault, custody chain
- **Processos:** 15 processos BPMN com workers implementados — AUTH, CONTAS, RECURSO, NIP, ANS-SUBMIT, CANCEL, REEMBOLSO, INADIMPLENCIA, CREDENCIAMENTO, ADEQUACAO, FRAUDE, PROGRAMA, PAGTO, ESCALATION, LGPD-DSR
- **Segurança:** PHI egress enforcement, log scrubbing, erasure cascateável, retenção de auditoria 5 anos
- **Infra:** Helm chart multi-tenant, Terraform modules (EKS, Aurora, ECR, Secrets, OIDC), Docker build reproduzível
- **Observabilidade:** Prometheus metrics, OpenTelemetry tracing, Alertmanager rules, Grafana dashboards
- **CI/CD:** Union-green pipeline (lint/type/unit/terraform/helm/integration), CD two-phase deploy com smoke tests, security scanning (CodeQL, gitleaks, dependency review)

### Comandos de verificação (rode-os — não confie em contagens escritas aqui)

```bash
make test               # unit lane (contagem: ver saída ao vivo; ~2076–2091 na sessão 2026-07-19)
make lint               # ruff check + ruff format --check
make type               # mypy --strict
make validate-artifacts # BPMN/DMN/policies/agent-definitions
make check-bpmn-error-allowlist   # ADR-0030 boundary-proof gate
helm lint deploy/helm/maezo-tenant/ --strict
terraform validate      # todos os módulos
# Integração real-engine (lento, ~1.5–2h): make test-integration  (engine CIB Seven isolado)
# Verdade verificada linha-a-linha: docs/evidence-ledger.md
```

## 8. Gatekeepers

| Gatekeeper | Quando |
|-----------|--------|
| `security-manager` | M4 (gateway), M6 (processos com HITL), M8 (FRAUDE), M12 (PHI/LGPD) |
| `ops-compliance-gate` | M2 (BPMN/DMN), M6-M9 (processos) |
| `production-validator` | M6, M7, M8, M13, M14 |
| `arch-system-design` | M1, M3, M5 |
| `ops-cloud-provisioning` | M10 |
| `ops-observability` | M11 |
| `performance-monitor` | M11, M13 |
