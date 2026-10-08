<!-- V21-CAPABILITY-EXECUTION-BEGIN -->
## Execução v2.1 — 2026-10-04

Entrega técnica revisável no [PR #633](https://github.com/Omni-Saude/maezo-operadora/pull/633), **draft**. Candidato desabilitado: contratos/admissão compartilhados e consumidores administrativos Compras/Suporte. Freeze técnico `90c1717ee028eae0471ed9a1d0c163e052370f71`, fingerprint `5b82a9d6517e822f8f1c9b84f4d24db35887b000b28836b35527937e096ae6d8`; base original `e7b14522a4f70242504d2b152a57ee5269ee66e2`, main admitida por delta `33aa7acba59dd5027513834d07d8e9a517649b3e`. Bundles originais e trabalho concorrente do ROOT preservados.

CI hosted [37231985559](https://github.com/Omni-Saude/maezo-operadora/actions/runs/37231985559) SUCCESS nesse freeze: 9 shards unitários, 5 integrações/engine real, portal/evals aplicáveis; 38 checks SUCCESS, um nightly condicionado a schedule. Dois reviewers finais independentes emitiram PASS limitado ao candidato desabilitado e ao dossiê W0: [arquitetura](docs/gates/v21-capability-execution-20261004/final-architecture-pass-r6.json) e [segurança/custódia](docs/gates/v21-capability-execution-20261004/final-security-custody-pass-r6.json). Histórico REVISE e falhas locais/hosted preservados; a suíte local ampla R2 falhou e não foi convertida em PASS.

W0 documental passou após repair terceiro e delta original (195 verificações), mas **ratificação humana não recebida**. Autoridade de catálogo/aceite/matrícula/agenda/casos/comunicação, memberships e publicação CR-F1..6 continuam ausentes ou não qualificadas. W1 durable/CAS/outbox/resume e DI operacional não estão entregues; W2/W3 jornadas completas dependem desses gates. W4 U4/payment-independent feedback e demais fontes/autoridades permanecem abertos. W5 benefício/controles e baseline/ROI permanecem UNMEASURED.

[Entrega e handoff exatos](docs/gates/v21-capability-execution-20261004/delivery-final-r6.json) registram donos, contratos, reparos, evidências e ordem de retomada. Esta atualização é delta de evidência/coordenação posterior ao freeze técnico, sujeito a readback próprio. Sem promoção de ADR/policy, assinatura de humano/fornecedor, novo binding produtivo, flags reais, implantação ou merge. Registros anteriores são snapshots históricos, não instruções vigentes.
Base de integração posterior admitida por dois deltas independentes: `4208c08717f577636e5e04a1c1455d8e25d5970b`, incorporada em `4c7a9f31214f71343318e349dd4b076dba97697d`; os 53 blobs R6 são idênticos. Inclui fonte billing ainda desligada, correção/versionamento do classificador Helena e SDK Java CIB 2.2 somente em testes. [Composição e limites](docs/gates/v21-capability-execution-20261004/integration-r7-source-composition.json); [handoff integrado](docs/gates/v21-capability-execution-20261004/delivery-final-r7.json). CI do conjunto posterior ainda PENDING; PASS R6 não é promovido. Avanços de main após4208 são declarados separadamente e não admitidos por inferência.

Fechamento técnico do conjunto publicado `75618442d4b7729f0194d5207248cdc6ab9c0267`: CI [37238750715](https://github.com/Omni-Saude/maezo-operadora/actions/runs/37238750715) SUCCESS, 39 checks aprovados e um nightly schedule-only; nove shards unitários (26.107 PASS) e cinco integrações (767 PASS), com skips/xfails preservados. Checkout real `e7864a46abbde875679e20e81213935f2009e8bd`, base `ccf00d41dea9d277dd28255b3e187548778ba7ed`, admitidos pelo steward; 15 caminhos adicionais são upstream, sem overlap. Os dois reviewers finais emitiram PASS limitado ao candidato publicado nesse contexto. [Fechamento e dependências exatas](docs/gates/v21-capability-execution-20261004/delivery-closeout.json). O objetivo integral permanece bloqueado pelos gates humanos/fonte/durabilidade/operacionalidade descritos nesse registro; nenhuma jornada completa, produção ou merge foi inferida. Esta consolidação posterior só acrescenta evidência/coordenação e exige igualdade de source e readback próprios.

<!-- V21-CAPABILITY-EXECUTION-END -->

# PLANS.md — Maezo Operadora (Greenfield v2) — STATUS REAL (GROUND-TRUTH, corrigido 2026-07-19)

> **Current continuity — 2026-09-10, WIP preservation complete:** [Current checkpoint](https://github.com/Omni-Saude/maezo-operadora/blob/completion/product-c27-auth-lifecycle/CHECKPOINT.md) supersedes historical scheduling/status below. Fresh status inspection covered 488 registered worktrees plus four standalone repositories; all 22 discovered uncommitted source files exactly match nine remotely verified recovery commits. Use `docs/prompts/maezo-wip-preservation-only.md` for a lower-cost WIP-only pass. Product implementation and release gates remain paused/pending. Frozen historical handoffs retain their original bytes; they are evidence, not current dispatch instructions.

> **Express cleanup completed 2026-09-10:** ROOT coordination and nine historical source WIPs published; five worktrees/four local branches/one remote branch removed after independent checks. Read the latest express section of CHECKPOINT.md. Product merge gates remain open.

> **Current execution state — 2026-09-10:** User-requested graceful pause. Read [CHECKPOINT.md](CHECKPOINT.md) first for current GitHub refs, preserved WIP, remaining product work and evidence limits. Historical completion percentages below do not describe this candidate.

> **⚠️ ESTE DOCUMENTO FOI CORRIGIDO EM 2026-07-19 PARA REFLETIR A VERDADE VERIFICADA.**
> As marcações originais **"✅ CONCLUÍDO / 15 de 15 / 100%"** eram **auto-certificação NÃO verificada** de agentes anteriores. Quando auditadas (2026-07-16), a plataforma estava em **v0.2.0 alpha-dev** (o LLM levantava `NotImplementedError`, sem entrypoints, com um bypass de aprovação financeira ao vivo). **Aquele "100%" é a lição cautelar, não uma medição.** Os envelopes de milestone (§3) são preservados como *referência do que cada milestone deve entregar* — mas os seus selos "✅ CONCLUÍDO" inline estão **SUPERSEDED**; o status real de cada um está na tabela de reconciliação em **§0**.
>
> **O modelo de verdade não é "15 milestones concluídos".** É o modelo **fase/gate (P0–P4 / G0–G4)** com verificação **zero-trust** (todo "done" carrega uma linha de verificador independente + evidência reproduzível). **A fonte de verdade durável e versionada no repositório é:** `docs/evidence-ledger.md` (**tamanho cresce a cada gap fechado — meça com `wc -l docs/evidence-ledger.md` antes de citar, nunca reescreva um número fixo aqui**: esta mesma linha já publicou duas contagens stale em sequência, "211" e depois "229", cada uma tornada errada por um commit posterior antes mesmo do merge — achado do verificador independente, ver `docs/evidence-ledger.md` linhas AF-06/AF-05/PERSP-B5-SHADOW-LINES/PERSP-C5-FRAUDE-METADATA; achado F5/AF-06 2026-09-05, `scripts/ci/check_plans_counts.py` não fenceia este número de propósito — um ledger append-only multi-escritor mudaria de novo antes do próximo commit), `docs/decisions-log.md` (DLs), `docs/adr/` (63 ADRs numerados, 65 arquivos incl. README+template — AF-06, recontado 2026-10-05 (+ADR-0063, construção prestador sob delegação); recontado 2026-10-01 (+1 ADR-0062, roteamento de conversa por agente no numero unico); recontado 2026-09-25 (+1 ADR-0061, custodia do telefone para retomada); recontado 2026-09-23 (+1 ADR-0060, schema nativo dedicado); recontado 2026-09-18 via `scripts/ci/check_plans_counts.py`; NUNCA cite um número fixo sem reexecutar o gate primeiro — este mesmo número já ficou stale uma vez, 32→39→48, achado do gap AF-06) e `docs/gates/` (registros de gate). *(O plano detalhado `V2-COMPLETION-PLAN.md` e o prompt de execução do backlog vivem apenas localmente em `docs/prompts/` por decisão do dono — o ledger é a verdade clonável.)*
>
> **Orquestrador:** Hive-Mind Queen (Opus / Fable 5) · **Repo:** Omni-Saude/maezo-operadora · **main:** `85c9618` (2026-08-07 — PRs #199/#200/#197/#198: cred gateway fact · adequacao fact-preservation + fail-safe · ans_submit NACK/assemble + ADR-0030 · **portão de critérios de aprovação automática**) — *(header 2026-07-19 preservado abaixo; reconciliação em §0.5; execução Tier-2 em §0.5.2)*
> **Estratégia:** Keep Brain (`docs/`), Rebuild Spine (`src/`) — **em execução, NÃO concluída.**
> **STATUS REAL (2026-07-27): G0 conditional-pass · G1 FECHADO · G2/G3 SUBSTÂNCIA DE ENGENHARIA COMPLETA (falta a metade humana de sign-off SME) · G4 PENDENTE 0% (externo). ~88–90% do plano-até-produção · ~97% da engenharia agent-controlável (Tier-2 §0.5.1: 7 de 9 itens EM MAIN — §0.5.2). NÃO é produção-ready — o que resta é o teto humano + a cauda-de-item-9 (xfail tail, live-engine). Detalhe em §0.5.**

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
| M1 ADR Ratification | **◑** | 63 ADRs (não 24); recontado 2026-10-05 (+ADR-0063, construção prestador sob delegação); recontado 2026-10-01 (AF-06; +1 ADR-0062, roteamento de conversa por agente, numero unico Helena -> Lucas); recontado 2026-09-25 (AF-06; +1 ADR-0061, custódia do telefone para retomada, GAP-XHITL-4); recontado 2026-09-23 (AF-06; +1 ADR-0060, schema `maezo_native` do portal staff, D-C2); recontado 2026-09-22 (AF-06; +1 ADR-0059, conduta da Helena, PR #460); recontado 2026-09-20 (AF-06; +5 do carve B do pr-c-infra, ADR-0054..0058); recontado 2026-09-18 (AF-06) — era "32" desde 2026-07, ficou stale conforme a árvore cresceu (ADR-0030/0031 adicionados 2026-07; até ADR-0049 em 2026-09-08; ADR-0050 em 2026-09-12, WP-J1-11; ADR-0051 em 2026-09-12 por este WP-J1-09, ADR-0052 em 2026-09-18). Ainda há ADRs "Proposed". **Reserva da fatia seguinte do carve (rastreio, 2026-09-21):** Slice C = perfil Maven `native-v2-qualification` (material de qualificação de engine), CORTADO do carve `build/pr-c-infra` na #443 — a spec ficou em árvore em `deploy/cibseven/secured/NATIVE-V2-QUALIFICATION.md`, cujo header a declara "registered as the next slice" desse carve; NÃO é fatia de código até ordem do dono; âncora de conteúdo = branch LOCAL `build/pr-c-infra` @`a18eda36` (remota deletada — PRESERVAR, read-only); evidência V443 em `docs/audits/journeys-first-phase4/2026-09-12` (gitignored — cite a missão interna). Contínuo. |
| M2 BPMN/DMN Regeneration | **◑** | BPMN/DMN existem + `validate-artifacts` verde; **MAS contratos ainda DRAFT — o gap<5% e a validação por SME NÃO foram feitos** (G2-bloqueado). |
| M3 Core Runtime | **✅** | G1 fechado; runtime spine verified-complete. |
| M4 Gateway & Security | **◑** | Cadeia de auditoria real e wired **nesta sessão** (T1.10 wave). PEP/pseudonymizer/vault/custody existem com lacunas — ex.: o gate de identidade LGPD estava **fail-OPEN** (corrigindo #113). |
| M5 Agent Framework + MCP + A2A | **◑→✅** | 10 grafos de agente reais (B6 fechado). **A2A dispatcher + card-signing COMPLETO (#156, 2026-07-27).** ToolRegistry implementado desde 2026-08-11 (`gateway/tool_registry.py`, classe `ToolRegistry`, ≈1082 linhas em 2026-10-06 — medir com `wc -l` antes de citar, este número já foi atualizado nesta linha [534→540→794→917→1082, achados do verificador, do AF-06 residual, da integração do portal (PR-A) e da fonte AMH do Lucas (DL-0075)] — D-M3-1; residuo ADR-0022/T2.4 fechado; a partir desta rodada `scripts/ci/check_plans_counts.py` reconcilia esta alegação com tolerância de 10% — AF-06 residual, round-6). |
| M6 Foundation Processes | **◑** | ESCALATION/AUTH/LGPD com workers; invariante L0 do AUTH provado. LGPD identity fail-open (#113); workers LGPD faltantes (`compile_data_package`/`execute_request`/`send_response`, #55 R-C/R-D/R-F). |
| M7 Core Compliance | **◑→✅** | Workers existem. Protocolo ANS fabricado removido (#109). **Recurso: `recurso.pended` (#138) + os tópicos de worker (#128) LANDARAM (2026-07-27).** ⚠️ **CORRIGIDO (ADR-0040, PR-3):** o caminho auditor `ACEITAR_GLOSA` (#123) era vocabulário de **recorrente** — SP-OP-RECURSO-001 estava bifacial (a Maezo aparecia como a parte que interpõe o recurso, com a operadora nomeada como "terceiro externo" no próprio artefato). A cadeia foi reconstruída na perspectiva do **pagador**: `ACEITAR_GLOSA` → `INDEFERIR`, `register_desistencia` → `registrar_indeferimento`, e o ramo do recorrente (`submit_appeal`/`track_status`/`reconcile_payment`/loop de acompanhamento) foi **deletado sem shim**. T2.6 ANS XSD/TISS fail-closed (#132). |
| M8 Advanced Processes | **◑** | Anti-dupla corrigido real (#108); fraude DMN-input endurecido (#111). Lacunas/workers de família restantes. |
| M9 Cross-Process Choreography | **◑→✅** | **Notification bridge ARMADO; CONTAS→FRAUDE live + Kafka producer (#157/#165); NIP/cron→SUBMIT via fenced start (#148) — LANDARAM (2026-07-27).** ⚠️ **CORRIGIDO (ADR-0040, PR-3+PR-4):** a aresta **CONTAS→RECURSO foi DELETADA** — a operadora não recorre da própria glosa. No lugar entrou a regra de **intake** (`agents.events.recurso.intake_recebido`), registrada **DORMENTE e declarada como tal**: o adaptador TISS que emitiria o evento não existe (**OQ-R1**). Novas arestas **RECURSO→PAGTO** (glosa revertida) e **CONTAS→PAGTO** (conta adjudicada — a premissa que o BPMN de PAGTO já afirmava e que não era verdade até aqui), ambas sob a invariante **I-PAGTO-1**: a origem semeia a EVIDÊNCIA do lastro, nunca o FATO, e por construção toda ordem entra em PAGTO por `UT_AnaliseAdmissibilidade`. |
| M10 Multi-Tenancy & Infra | **◔** | Helm/Terraform passam `lint`/`validate`, mas **NÃO provisionados** — Phase 4 = 0%, external-blocked (AWS/creds/secrets). IaC-only. |
| M11 Observability | **◔** | Configs podem existir; **sem verificação zero-trust**; não é um gate fechado. |
| M12 PHI & LGPD Hardening | **◑** | PHI egress NetworkPolicy existe; **o fail-open de identidade provava que não estava completo** (#113). Erasure/retention não totalmente verificados; DPO+RIPD external-blocked. |
| M13 Production Hardening | **✗→◑** | **Chaos/seam-fault + cross-process suites LANDARAM (T3.3, #147/#155/#157, 2026-07-27).** Load/DR reais = P4-externo (ainda não iniciado). |
| M14 Docs & Handoff | **◔** | Docs existem; handoff operacional formal não feito. |

### 0.4 Sprints ainda pendentes (resumo — detalhe técnico no prompt de execução em `docs/prompts/`)

**Agent-controlável (construível agora):**
- **Sprint P2-close (→ engenharia de G2):** substância T2.6 (validação XSD/TISS real — `validate_data` é stub; notification-bridge NIP/cron→SUBMIT); workers faltantes (LGPD R-C/R-D/R-F, recurso tópicos + caminho-auditor + `recurso.pended`); sub-tarefas de auditoria T-B/T-E/T-F/T-G; **verificar+merge #113**.
- **Sprint P3 (→ G3, o maior bloco restante, quase não iniciado):** T3.2 (~44 evals + lane de CI), T3.3 (chaos/cross-process/anti-dupla), T3.4 (auditoria adversarial pré-deploy — por último). Mais divergência de checkpoint-schema. (PLANS-A2A-CONTRADICTION: "A2A dispatcher + card-signing" removido daqui — já COMPLETO desde #156/2026-07-27, linha 43 acima; listá-lo também como pendente contradizia a própria tabela §0.3.)

**External-blocked (agentes NÃO fecham — o teto real):**
- **G2:** sign-offs de SME (médico-auditor → jurídico/DPO/regulatório/finanças/PO) para ≥12/16 contratos FINAL.
- **G4:** conta+creds AWS + 6 secrets; designação de DPO + RIPD; provisionamento GHAS; 3 gatekeepers + go/no-go humano.

> **Caveat estrutural:** cada fase gerou **1,5–3× as sub-tarefas planejadas** (T1.10 "1 tarefa" virou uma wave de 7; esta sessão sozinha gerou o bug cross-loop anti-dupla, os findings de recurso e a cascata do gitleaks). Leia "~30% restante" como "~30% com cauda longa à direita", não linear.

---

## 0.5 — ATUALIZAÇÃO 2026-07-27 (reconciliação pós-ondas T2–T8; PR #171 merged)

> **Esta camada reconcilia §0 (2026-07-19) contra `origin/main` @ `8092681`.** O ledger durável agora vai até `t8-token-metering`; ADRs até **0036**; decisions-log até **DL-0035**. **Tudo que §0.4 listava como "sprint pendente agent-controlável" LANDOU e está verificado no ledger.** Engenharia agent-controlável ≈ **95%+**; o que resta é o teto humano (Tier-3) + uma cauda Tier-2 agent-buildable. *(Metodologia: reconciliado contra `origin/main`, não contra a árvore local — a local esteve 49 commits atrás e faz varreduras reportarem como "pendente" trabalho já mesclado.)*

**Executado desde 2026-07-19 (merged + ledger-backed):**
- **G2 substância:** T2.6 XSD/TISS fail-closed (#132) + bridge NIP/cron→SUBMIT (#148); notification-bridge ARMADO EB-3/EB-4 CONTAS→FRAUDE live + A3 (#157) + Kafka producer (#165) *(a perna CONTAS→RECURSO foi deletada por ADR-0040 — ver M9)*. LGPD R-F/R-G (#125) + DSR identity fail-closed (#113). Recurso: tópicos + boundary (#128) + `recurso.pended` (#138) *(o caminho `ACEITAR_GLOSA` de #123 foi reescrito como `INDEFERIR` por ADR-0040 — ver M7)*. Famílias cred/reembolso/programa/nip/pagto/adequacao/fraude (#126/#129/#130/#133/#136 + #134–#141). Fraude scoring DMNs (#48/#79/#111/#126). Auditoria T-B (#124)/T-E (#131)/T-F+T-G via A2A W1–W4 (#156)/ADR-0033 (#151). **A2A dispatcher + card-signing COMPLETO (#156).**
- **G3 engenharia FECHADA:** T3.1 integração (#94/#146/#153/#158), T3.2 44 evals + lane CI (#149), T3.3 chaos/cross-process (#147/#155/#157), T3.4 auditoria adversarial + remediação (#159).
- **Completude de plataforma T4/T5:** checkpoint durável, fix DSN prod, correção de workers, DL-0033/0034, **descope L2-review-sampling ratificado (ADR-0034)**, pytest 9 (#165/#166); **t6** pseudonymizer HMAC + ADR-0035 (#167); **t7** webhook DATABASE_URL (#169).
- **Auditoria adversarial desta janela → PR #171 MERGED (`8092681`):** escalation notify fail-closed (best-effort→propaga→`ERR_ESC_NOTIFY_FAILED`→fallback→UT), conversation-id PHI com chave HMAC + ADR-0036, handoff NIP→ANS armado, BK-parity numero_caso, LLM token-metering. Cada um dos 5 branches passou por gatekeeper adversarial independente (tier ≥ autor).

**Pendências reais (2026-07-27):**
- **Tier 1 — FEITO:** PR #171 merged; #170 (versão hollow) curado pelo v2; PRs #172–#176 = bumps dependabot (fora de escopo).
- **Tier 2 — agent-buildable, decision-gated (o PRÓXIMO orquestrador DECIDE + EXECUTA — ver §0.5.1):** (1) auditoria de postura dos 4 callers de swallow restantes em `operadora.notifications.internal` (`lgpd.request_additional_proof`/`send_response`, `recurso.notify_sla_risk`, `ans_submit.notify_regulatorio`) — mesma forma de perda-silenciosa que a Fix A fechou para escalation; (2) durabilidade do handoff one-shot NIP→ANS (outbox/reconciliação — hoje rides best-effort sem retry natural); (3) predicado bridge CONTAS→FRAUDE sem `non_blank(tenant_id)` (assimetria pré-existente); (4) correlation-ids do token-metering (`agent_id`/`tenant_id` = None hoje); (5) delegação A2A real de dossiê além dos stubs DL-0033 (adequacao `prepare_remediation_dossier`, cred `prepare_dossier`); (6) CronJobs de retenção/expurgo — **JÁ EXISTEM** (Helm `deploy/helm/maezo-tenant/templates/cronjob-lifecycle.yaml`, três jobs `expurgo-working`/`verify-erasure`/`audit-retention`, anotados `maezo.io/expected-fail-until` — R-040/SC-07) e falham POR DESENHO até a matriz de retenção `AF-07` existir (`src/maezo/platform/lifecycle/__init__.py` recusa fail-closed, nenhum comando implementado); implementar o expurgo real é decisão B do dono (`OWNER-DECISIONS-REGISTER` R-106) — só depois de `AF-07`, sem antecipar código sem regra; (7) `PopulationFeatureClient` (André); (8) ratificação de ADRs Proposed (0025/0026/0028/0029/0030; há conflito de status em ADR-0028 ledger-vs-arquivo); (9) cauda de xfails por família (~102 sites/~39 constantes em origin/main — lista viva: `git grep '_.*_REASON =' tests/integration/processes/`). **Gated-por-valor (construir o seam, deixar o valor humano):** cost-table USD do LLM (#29 — os counts do metering já landaram), retention-matrix (item 6), módulo fhir-sync (Tasy-contract-gated).
- **Tier 3 — teto humano (escalate/track SOMENTE — NÃO forçar):** sign-offs SME (contratos DRAFT→FINAL, médico-auditor→jurídico/DPO/regulatório/finanças/PO); AWS + `terraform apply` + billing; 6 secrets de produção (LLM/Tasy/WABA/**PHI_HMAC_KEY**/card-signing); DPA endpoint PHI-BR; DPO + RIPD + retention-matrix; sign-off DMN clínico + validação de personas (Beatriz↔Valentina) + atestação de segurança clínica; atestação ANS + competências reais; GHAS; **valores de teto D-07** (diretoria); parecer ANVISA SaMD (GAP-C10); emissão de cert T-G (ADR-0033); 3 gatekeepers + go/no-go. Detalhe humano: `docs/Tarefas_Pendentes.md`.
- **Resolvido nesta janela:** edições BPMN obsoletas descartadas (stash-drop aprovado pelo dono, commit 259e6a42e) → main local sincronizado; higiene de branches remotas/locais executada.

### 0.5.1 — AUTORIZAÇÃO AO PRÓXIMO ORQUESTRADOR

> **O próximo orquestrador (Hive-Mind Queen, Fable 5/Opus) está AUTORIZADO a usar deep-think para DECIDIR e EXECUTAR os itens Tier-2 (agent-buildable, decision-gated)** — não apenas registrá-los. Para cada item Tier-2: (1) deep-think da causa-raiz e do design correto (SEM workarounds); (2) decidir se é agent-buildable-agora ou gated-por-valor-humano; (3) se agent-buildable, **desbloquear e EXECUTAR** sob zero-trust VERIFY→GATE (build → gatekeeper adversarial independente, tier ≥ autor → PR → merge on green → byte-check `main`); (4) se gated-por-valor (cost-table USD, retention-matrix, schema Tasy), **construir todo o seam + lógica fail-closed** e deixar SOMENTE o valor/contrato humano como ponto de injeção claramente marcado — **NUNCA inventar o valor**. **Tier-3 (teto humano) permanece escalate/track-only — o orquestrador NÃO deve forçá-lo.** Roteamento inteligente de modelo (R1 opus para L0/PHI/financeiro/segurança/cripto; R2 sonnet para impl/testes/infra; R3 haiku para scans/docs), máxima paralelização dependency-aware, cláusula anti-injeção em todo brief. Fontes autoritativas: `docs/evidence-ledger.md`, `docs/decisions-log.md`, `docs/adr/` (até 0036), `docs/gates/`, e `docs/Tarefas_Pendentes.md` (teto humano).

### 0.5.2 — EXECUÇÃO TIER-2 (2026-07-27, sessão Fable5→Opus) — 7 de 9 itens EM MAIN

> A autorização §0.5.1 foi executada: 6 scouts read-only mapearam cada item Tier-2 → matriz de decisão travada → 5 builders worktree-isolados com gatekeepers adversariais independentes (tier ≥ autor, zero-trust) → **2 PRs mesclados em `main` com todos os 15 checks verdes (incl. lane real-engine ~1.5h), byte-check limpo**. O zero-trust pegou 4 defeitos reais antes do merge (imprecisão de governança, regressão de trigger-dormante, crash de encoding, e — na lane real-engine do #178 — um teste de predicado que ainda fixava o contrato pré-anchor; diagnosticado como teste-only, corrigido, re-verde).

| Item §0.5.1 | Entrega | PR | Verificação |
|---|---|---|---|
| **4** | correlation-ids agent_id/tenant_id no seam LLM (log-only, cardinality-safe) | **#177** | GK-corr PASS, mutation-proven |
| **6** | seam RetentionMatrix fail-closed + taxonomia de recusa + alerta (SEM valores humanos inventados) | **#177** | GK-seam REVISE→corrigido |
| **8** | ratifica ADR-0025/0026/0028/0030→Accepted; DL-0036/37/38; reconcilia PLANS | **#177** | GK-adr REVISE→corrigido |
| **1+2+3** | notify posture fail-close (perda-silenciosa LGPD/regulatório), fix false-success do producer (bool), durabilidade NIP handoff, anchor tenant nas 7 regras do bridge, stamp deployment-tenant (arma cron→ANSSUB ao vivo) | **#178** | orquestrador R1 (independente do autor) |
| **5** | delegação A2A de dossiê REAL (Carolina `credentialing.analyze` / André `analytics.population` origin→flow), gate de assinatura fail-closed, degradação DL-0037 (worker nunca levanta; UT humana sempre abre) | **#178** | orquestrador R1 (substância do builder); cauda de bring-up orq-autorada, divulgada |
| **7** | `PopulationFeatureClient` (André) | — | **TETO HUMANO** (ADR-0019: lake externo + AWS LF-Tags + k-floor DPO) — não construir |
| **9** | xfail tail (95 strict-xfails vivos, censo recon-f) | #179/#180/#181 + assembly-PR (waves 3-7) | **censo 95→36 vivos (31 flips neste PR)** (restantes: 6 auth Class-A, 3 ans_submit, 3 cred cure-window, 2 cred guard-shape T-E, 4 adequacao bucket-3, ~18 bucket-2 humanos) |

**Item 9 (xfail tail) — o único item agent-buildable restante; engine local (`make dev-stack`) itera ~2min/suíte, removendo a barreira do ciclo-CI-cego (memória [[local-engine-integration-iteration]]).** ⚠️ Cada flip que remove um marcador strict-xfail PRECISA dar XPASS ao vivo senão o CI fica VERMELHO — dependência real, não workaround. **Bucket 1 (33 sites) DESBLOQUEADO pelo #178** (os stubs DL-0033 viraram workers reais). **PROGRESSO (2026-07-27, engine local):**
> - **`adequacao` — COMPLETO** (9/9): #179 flipou 4, este wave adaptou os 5 `notifications_of_type` mortos → `activity_instances_ended`/`activity_instance_count` (novo helper p/ contar re-execução do dossie). Restam só os 4 bucket-3 (monitoring/measure-gap).
> - **`cred` — 12 de 12 em bucket-1** (tally corrigido 2026-08-06: os 3 de descredenciamento foram corrigidos e flipados nesta branch; os 2 de `cred.pended` já haviam flipado em item-9 w5; restam apenas os 2 xfails de bucket-3 guard-shape, T-E-deferidos) (branch `item9-b1-adequacao-adapt`): 6 flip limpo + `notify_sla_risk` adaptado; 5 re-xfailados com 2 ACHADOS NOVOS que o desbloqueio do dossie revelou — (a) 3 happy-paths de descredenciamento agora chegam corretamente à cure-window RN-567 (`GW_AguardarNotificacao`/`ICE_PrazoNotificacao`) e passam por ela (os 3 testes já disparam o timer via `_drive_to_descred`); **CORREÇÃO (2026-08-06, `cred-substituicao-fact`): o diagnóstico "follow-up de completar o teste, NÃO worker faltando" estava ERRADO — era um defeito real de src.** `ST_RegisterDescredenciamento` roda, mas o gateway seguinte `GW_Substituicao` (BPMN ~:436) roteia por `${tem_plano_substituicao == true}`, um booleano FLAT que NENHUM worker jamais setava (`register_descredenciamento` só retornava `descredenciamento_registrado`/`network_changed`/`data_efeito_iso`) — travando a instância no gateway com "Cannot resolve identifier", exatamente o hazard que o próprio comentário do BPMN documenta. Corrigido em `src/maezo/tools/workers/credenciamento.py` (`_register_descredenciamento` agora ecoa `tem_plano_substituicao`, derivado do campo OPCIONAL `plano_substituicao` da UT); (b) RESOLVIDO em item-9 w5: `cred.pended` passou a ser publicado pelo splice remedy-B (`ST_PublishCredPendedDoc`/`ST_PublishCredPendedNotice`) e os 2 testes flipraram.
> - **`pagto` — COMPLETO (12/12)**: todos os 12 flipam limpo na primeira run fresh (19 passed, 1 xfailed [FINDING-1 ceiling, não relacionado], 0 failed) — SEM achado downstream novo (diferente de `cred`). 2 dead-echoes de re-execução do dossie adaptados p/ `activity_instance_count(iid, "ST_PrepareApprovalDossier")`; 1 dead-echo de `notify_sla_risk` adaptado p/ `"ST_NotificarRiscoSla" in ended`. **GAP src/** SINALIZADO (não corrigido aqui):** ao contrário de adequacao/cred (#178), `operadora.pagto.prepare_approval_dossier` NUNCA foi religado ao `build_dossier_delegation_dispatcher` (`_DOSSIER_EDGE_AGENT_IDS = ("carolina", "andre")` só cobre os outros dois) — o worker segue o stub local DL-0033 original (sem seam `DelegationDispatcher`, nem o formato de gap disclosed do DL-0037), embora `agents/andre/graph.py` já documente `pagto_dossier` como o flow DEFAULT dele (consumidor pronto, produtor não ligado). Follow-up de fiação A2A real fica para tarefa dedicada. **FECHAMENTO (2026-09-04, w3-docs-sweep, achado AND-01 do fleet audit = STALE):** este GAP já não reflete o disco — `src/maezo/tools/workers/pagto.py::make_prepare_approval_dossier_handler` chama `delegate_pagto_dossier` (~:921-945) e o handler está registrado no topico `operadora.pagto.prepare_approval_dossier` (~:1129-1133); o mesmo parágrafo acima já registra o wave "w3 pagto-dossier" (delegação A2A real do dossiê de pagto) como landed. Nota mantida por histórico; não apagada.
> - **Bucket-3 Class-B (migração ADR-0030 guard→WorkerBpmnError) — PARCIAL (src landado):** `nip.handoff_ans_submit` agora levanta `WorkerBpmnError(ERR_NIP_PROTOCOLO_INVALIDO)` (era `NipProtocoloInvalidoError`/ValueError; classe removida) e `programa.check_consent` levanta `WorkerBpmnError(ERR_PROGRAMA_NO_CONSENT)` (era `ProgramaError`); ambos wired em `service.py` (`PRODUCTION_BPMN_ERROR_ALLOWLIST` = 6 códigos, ambos roteando p/ terminais NEUTROS End_NipProtocoloInvalido/End_SemConsentimento). **NÃO migrados (corretos):** `cred` guard-shape (já migrado em t5-workers-f2, T-E-deferred); `programa.stratify_risk` (o MESMO código mas topic SEM boundary — `WorkerBpmnError` ali encerraria o escopo silenciosamente = regressão L0); códigos `*_NOT_HUMAN` (T-E-gated). Unit+gate verdes; os 5 strict-xfails de integração (nip×3/programa×2) seguem marcados com razão CORRIGIDA (migração src landou; o flip exige wiring do `bpmn_error_allowlist` da probe de integração + prova live-engine — follow-up).
> - **Receita provada:** add tópico ao drain-list + adapta `notifications_of_type("X")` morto → `assert "ST_<worker>" in await engine.activity_instances_ended(iid)` (ou `activity_instance_count` p/ re-execução); remove marcadores; roda fresh (`down -v` entre runs!); re-xfaila com razão corrigida os que revelam achado downstream real. **Bucket 2 (18 sites) = teto humano, NÃO tocar** (TISS-XSD SME / DPO-DSR / teto D-07 finanças). **Bucket 3 (44 sites) = migrações ADR-0030 Class-B coded-exc→WorkerBpmnError (nip `_PROTOCOLO_INVALIDO`×3 [boundary BPMN já existe], programa consent-guard×2, cred guard-shape×2) + adaptações Class-A de asserts mortos (auth×6/reembolso×5/nip×2/recurso×1) + Class-C buildable (bug cred `doc_completa`-overwrite×6, pagto ceiling×1).** **Também DIFERIR (teto humano): item 7, ratificação ADR-0029, SQL de erasure por-camada + VALORES da retention-matrix.** Detalhe vivo: memória `maezo-v2-execution-state.md` + `docs/prompts/NEXT-ORCHESTRATOR-HANDOFF.md`.
> - **WAVES 3-7 + ASSEMBLY (2026-08-03, sessão Fable5 — o assembly-PR desta linha):** 6 branches construídos em paralelo (worktrees pré-criados pelo orquestrador), CADA UM gatekept por revisor opus independente (zero-trust), defeitos corrigidos, e o conjunto montado numa cadeia linear + 2 merges com prova live per-suite em engine fresh (receita 8GB-host da memória): **w3 engine-flips** (nip 30p / recurso 34p / inadimplencia 24p / programa / reembolso — 13 flips + allowlists de probe importadas do src); **w3 pagto-dossier** (delegação A2A real do dossiê de pagto — o GK pegou um BLOCKER financeiro: business_key re-derivada podia iniciar uma 2ª instância SP-OP-PAGTO-001 = duplicate-release; corrigido com key do engine + no-op de origem, +timeout DL-0037, +redação, +money-int estrito — 10 findings, 10 commits); **w4 Class-C** (cred fact-preservation doc_completa, pagto dentro_teto_l2 write-back, migração ADR-0030 ERR_CRED_INVALID_PRESTADOR [allowlist prod = 7 códigos], auth guards ancorados em evidência humana real); **w46** (auditor_id modelado como formField nas 3 UTs do AUTH + contrato — o GK provou que a variável não era modelada; signoff-gate verificado self-serve p/ contratos DRAFT); **w5 event-wiring** (programa: seam kafka religado [4 raw handlers], workers proactive_contact/notify_sla_risk NOVOS, splices BPMN processing_stopped + cred.pended ×2 [remédio-B T3.1 que cred nunca recebeu], reclassify_coded_exception single-source em base.py); **w7 valor_cents** (GK-descoberta: pagamentos emitiam R$0,00 nos 3 caminhos — issue_payment lia variável que NADA produzia; agora resolve valor_reembolso_aprovado_cents com guard fail-closed de dinheiro). **18 marcadores XPASS-mandatórios flipados NA ÁRVORE MONTADA e provados ao vivo: cred 26p+5xf · pagto 20p+0xf (COMPLETO) · programa 23p+0xf (COMPLETO) · reembolso 22p+1xf (só D-07) · auth 12p+6xf (sem regressão).** Assembly-GK consolidado: 21 findings das 4 rodadas rastreados FECHADOS com evidência, aritmética de merge recomputada independente, replay per-branch = zero hunks perdidos. Zero-trust pegou 6+ defeitos reais nesta arco (duplicate-payment, R$0,00-payment, auditor_id não-modelado, XPASS landmines ×2 rodadas, colisão de worktree).

---

## 0.5.3 — Portão de critérios de aprovação automática + cauda item-9 (2026-08-07, LANDADO)

> **main `85c9618`.** Quatro PRs, cada um live-provado em CIB Seven 2.1.0 real, cada um com gatekeeper
> R1 independente (≠ autor) que devolveu REVISE, e cada um byte-checado delta-vs-delta no merge.

**MANDATO DO DONO (2026-08-06):** *toda aprovação automática tem de cumprir critérios técnico,
teto financeiro, regulatório e marcos/regras/KPIs contratuais; o agente PODE validar e sinalizar o
que não cumprir um ou mais.*

- **#198 `auth-auto-criteria-gate` (PRINCIPAL) — fecha GAP-AUTH-4 estruturalmente e GAP-AUTH-3.**
  Novo worker determinístico `operadora.auth.validate_auto_criteria` (`ST_ValidateAutoApprovalCriteria`,
  inserido entre `BRT_SlaAnalise` e `BRT_AutoApproval`, espelhando o precedente do REEMBOLSO) computa
  quatro critérios + `auto_criteria_verificado`. A DMN `auth_auto_approval` passou a ler **apenas** os
  critérios COMPUTADOS + essa flag — exigi-la é a cerca que faltava: pular o validador cai no
  catch-all → ANÁLISE HUMANA. **Portão de RATIFICAÇÃO** (`spec/processes/dmn/auth-criteria-ratification.yaml`,
  agora CODEOWNERS-gated junto com `spec/policies/autonomy/`): uma tabela clínica DRAFT/sintética
  **nunca** concede PASS, por mais que a tabela diga sim — espelha o loader da matriz de retenção que
  recusa o próprio template não ratificado. **Shadow mode** grava o que a tabela DRAFT *teria* decidido
  (dado real para o SME ratificar), sem nunca influenciar o veredito. **Efeito hoje: nada auto-aprova**
  — mesmo resultado seguro de antes, mas por quatro razões auditáveis por critério em vez de três
  booleanos semeados no payload de start. Cada critério ativa sozinho quando sua fonte for ratificada,
  **sem mudança de código**.
- **#199 `cred-substituicao-fact`** — descredenciamento travava em `GW_Substituicao`, que roteia por
  `${tem_plano_substituicao == true}`: variável com **2 ocorrências no repo inteiro, ambas dentro do
  BPMN**, que worker nenhum setava. Estava arquivado (aqui, no texto do xfail e na memória do
  orquestrador) como "follow-up de completar o teste" — diagnóstico ERRADO nas três fontes.
- **#200 `adequacao-fact-preservation`** — `measure_gap` sobrescrevia quatro fatos que o próprio BPMN
  diz que ele "ecoa"; `update_monitoring_plan` não publicava; e `ST_CalculateGap` **documentava**
  publicar `agents.events.adequacao.gap_detected` mas seu tópico é worker que não publica (evento
  nunca saía). Inclui o **fail-safe ratificado pelo dono** — ver §0.5.4.
- **#197 `ans-submit-nack-assemble`** — NACK tornou-se produzível em dev/test e **impossível em
  produção**; `AnsRetryEsgotadoError` (família TRANSIENTE do harness) impedia o token de avançar e foi
  removida (o MODELO lança `ERR_ANS_RETRY_ESGOTADO`, não um worker); guarda de montagem ganhou o
  boundary modelado que o contrato já especificava; família iniciou sua migração ADR-0030.

**Censo de strict-xfail: 95 → 36 → 24 → <!-- xfail-census:total:begin -->22<!-- xfail-census:total:end -->**
(2026-08-10: #226 flipou os 2 NACK variables-channel com prova live). Restante (AST sobre
`tests/integration/processes/`, gerado por `scripts/ci/generate_xfail_census.py` — ledger em
`docs/xfail-census.json`): <!-- xfail-census:breakdown:begin -->TISS-XSD SME ×14 · LGPD/DPO ×3 · cred guard-shape T-E ×2 · auth D-07 ×1 · reembolso D-07 ×1 · adequacao RN259 ×1<!-- xfail-census:breakdown:end -->
— **inteiramente teto humano.** A partir da Onda 0 do §0.8 o censo passa a ser GERADO em CI (o
drift 24-vs-22 entre PLANS/handoff.yaml/NEXT-ORCHESTRATOR nesta mesma semana foi o gatilho); as
duas regiões acima entre marcadores HTML são reescritas por esse gerador — edição manual dentro
delas falha o CI (`--check`); o texto ao redor permanece prosa normal.

## 0.5.4 — Achados que precisam de decisão HUMANA (nenhuma engenharia adicional é possível)

1. **RN 259 — inversão de ordem de regra em `adequacao_gap.dmn` (ABERTO).** `hitPolicy=FIRST` com
   `r_eletivo_leve` ANTES de `r_conforme`, e `r_conforme` **não tem teto algum** de tempo/distância:
   um acesso eletivo **arbitrariamente ruim** lê CONFORME (as regras `*_critico` por tempo/distância
   gateiam só em `urgencia_emergencia`). **Mitigado em RUNTIME** pelo fail-safe ratificado pelo dono —
   o worker preserva o veredito da DMN (auditável) mas **recusa AGIR** sobre um CONFORME que as
   medições contradizem, usando o teto que a própria tabela declara (60min/50km), roteando a humano.
   **A TABELA CONTINUA ERRADA**; ordem/tetos são do dono regulatório. ADR-0028 §7 mantém "a DMN vence,
   não se corrige em engenharia".
2. **Critério regulatório não computável.** `carencia_check` precisa de `tipo_procedimento`/
   `dias_desde_adesao`/`cpt_declarada`; nenhum está no contrato de start e `dias_desde_adesao` exige
   cadastro atrás da fronteira AMH (MZO-050b bloqueado). Falha fechado com
   `REGULATORIO_ENTRADA_AUSENTE` — a costura existe e ativa no dia em que o dado existir.
3. **`rede_credenciada` sem critério algum.** Nenhuma fonte de credenciamento legível por máquina
   existe. Declarado em `criterios_nao_cobertos` **dentro do manifesto que o SME obrigatoriamente
   abre para ratificar** e pinado por teste de cerca — senão, ratificar `auth_criteria_contratual`
   abriria aprovação automática para prestador FORA DA REDE sem que critério nenhum dissesse nada.

## 0.5.5 — Decisão do dono: superfície humana do operador (R-031, 2026-09-04) — RESOLVIDA

**Pergunta [M-19 / gap `9.1`]:** a superfície do operador para as jornadas com User Task é o
**Cockpit do CIB Seven**, e o `testchannel` é declarado **demo sem autenticação própria**?

**Resposta aprovada, citada verbatim (OWNER-DECISIONS-REGISTER R-031, Bold-Decision Review v2 CEO
2026-09-04, status `APROVADO-APOS-REVISÃO-HUMANA`):** *"Manter o SIM (Cockpit + `testchannel`
declarado demo sem autenticação própria) e DELETAR a espera em vez de datá-la: a linha de
`docs/review-queue.md` sai de DRAFT e a nota em `src/maezo/platform/testchannel/README.md` entra
no MESMO PR que registra esta resposta do registro — sem teto de calendário, porque não sobra
espera a limitar —, e `antes do 1º operador real` deixa de ser âncora e vira mera nota de
revisão."*

**Implementado por este registro:** `src/maezo/platform/testchannel/README.md` (novo — nota "demo
sem autenticação própria") + linha em `docs/review-queue.md` citando esta resposta como o ato de
sign-off do dono. Desbloqueia `WP-SUPERFICIE-HUMANA` (`9.1`, `11.1`, `9.2`, `9.6`, `10.1`, `10.2`,
`11.7`) e, indiretamente, `WP-EVALS`. Dependência: `11.1` (mapa de personas de R-032, M-20) — as
jornadas julgadas por `9.1` vêm de lá; ainda não resolvida por este registro.

## 0.6 — Programa de compatibilidade AMH (AMH-compat) — registrado 2026-08-05

> **Este documento não tinha, até este registro, nenhuma menção ao programa AMH-compat — lacuna de rastreio corrigida agora; §0/§0.5 não são reabertos.** Governado pela **ADR-0037** (Accepted, ratificado pelo dono do repositório em 2026-08-03, DL-0040): supersede PARCIAL o ADR-0013 (clausulas 1, 2 [wire dev-JSON], 3, 4, 5; princípios consume-not-duplicate/TASY-write-DROP re-ancorados) e AMENDS o ADR-0034 (XRD-09, chokepoint por chamada). Detalhe completo: `docs/adr/0037-*.md`; decisões: DL-0039 (draft)/DL-0040 (ratificação)/DL-0041 (XRG-3) em `docs/decisions-log.md`; ledger: rows `mzo-000`/`mzo-000-ratify`/`mzo-010`/`mzo-030` em `docs/evidence-ledger.md`.

**Gates cross-repo (plano §6.3 do ADR-0037) — os três FECHADOS:**
- **XRG-1 (ownership).** Metade Maezo: ADR-0037 Accepted (DL-0040, `b6de8d2`, PR #188). Metade AMH: ADR-042 do repo `amh-data-platform`, Accepted na mesma data (2026-08-03), registrando explicitamente que a aceitação fecha XRG-1 por completo com o Maezo ADR-0037 já Accepted.
- **XRG-2 (publicação).** A AMH publicou o contract manifest canônico + artefatos; evidence id **`XRG2-AMH-DEV-GHA-30991849241`** (publicação Glue real, run não-dry-run), verificado independentemente 2× antes do pin (DL-0041).
- **XRG-3 (pin do consumidor).** Fechado por este repo — **MZO-010** (PR #190).

**Work packages landados (Wave 0-1):**
- **MZO-000** — a própria ADR-0037 (draft + ratificação humana): PRs #187/#188.
- **MZO-010** — pin imutável `config/integrations/amh/contracts.lock.json` + gate fail-closed `make verify-amh-contract-pin` + contract tests (`tests/contract/amh/`): PR #190.
- **MZO-030** — `src/maezo/ports/` — cinco seams `typing.Protocol` ERP-neutros (`WorkItemSource`, `ConsentDecisionSource`, `ClinicalContextPort`, `PopulationFeaturePort`, `OutcomePublisherPort`): PR #191.

**Portões humanos NOMEADOS — isto NÃO é trabalho agendado, é teto humano:**
- **MZO-020** (objetos de valor de identidade portável) — portão **DPO/Legal DESCARREGADO em 2026-08-05**: aprovação de **Lucas (Diretor Jurídico e de Compliance)** e **Rodrigo (CEO / dono do repositório)**, declarada pelo dono na sessão de orquestração; registrada como **DL-0042**. O work package passa a ser executável.
- **MZO-040** (`ActionExecutionGateway`) — **AINDA BLOQUEADO**: aguarda aprovações **Médica, ANS e Security** (nenhuma concedida; DL-0042 não as alcança). **Qualificação (2026-08-09, DL-0045): bloqueado como DECISÃO — o gateway sombra EXISTE e nega em termos de enforcement até a aprovação.** O build "dark" landou: `src/maezo/gateway/action_execution.py` (loader fail-closed + `evaluate` total) ligado em SOMBRA no chokepoint por-chamada `WorkerHarness._handle`, com o registro de aprovações como DADO em `spec/policies/autonomy/action-approvals.yaml` (10 classes × 3 domínios = 30 blocos, **todos `aprovado: false` / `PENDENTE`**) e o pacote de evidência em `docs/reviews/mzo-040-approval-packet.md`. **Nada foi aprovado e nada é bloqueado**: `status: DRAFT` + `modo: shadow`, e a inércia é provada por teste (caminho choked executado com e sem o gateway, observáveis comparados por igualdade). Isto **não** reabre nem antecipa o portão — existe porque a XRD-09 da ADR-0037 só produz efeito com "evidência MZO-040 entregue", e pedir a três aprovadores que assinem uma descrição de projeto seria o inverso; eles ratificam contra a telemetria de sombra que o sistema em execução produz. **Ratificar continua sendo ato humano de DADO** (preencher os blocos, completar `mapeamento_topicos`, `status: RATIFICADO`, `modo: enforcing`), sem mudança de Python. Resíduos declarados para a Security na §6 do pacote — com destaque para o override de deployment `MAEZO_ACTION_APPROVALS_PATH` (fechado na perna de enforcement por flag-companheira) e para o fato de que o CODEOWNERS do manifesto é **listagem, não portão**, enquanto o `main` seguir sem proteção server-side (achado do dono já na row `mzo-000` do ledger).
- DL-0040 registra que a ratificação da ADR-0037 NÃO fechou nenhum destes dois portões — o do MZO-020 caiu por ato humano próprio e posterior, não por consequência da ADR.

**Próxima dependência:** **MZO-050** (adapters AMH de work-item/consent) depende de MZO-010/020/030 + XRG-3 (mapa de gating na seção "Consequências" do ADR-0037) — com o portão do MZO-020 descarregado, deixa de haver bloqueio humano nesta perna da cadeia.

> **Achado de conformidade em aberto, levantado durante o MZO-020 (decisão do dono, não corrigido aqui).** O esquema de chaves CIB **em produção diverge da proibição 6 da ADR-0037 na FORMA**: as business keys são unidas por hífen e com prefixo à frente (p.ex. `ANSSUB-{tenant}-{report_type}-{competencia}`, `RECURSO-{tenant}-{numero_guia_tiss}-{glosa_id}`), não `{company_tenant_ref}:{workflow_type}:{workflow_business_ref}`; e as process-definition keys são `SP-OP-<DOMAIN>-<NNN>` (15 chaves congeladas em `src/maezo/tools/process_allowlist.py`), não `maezo-payer-*`. Além disso, várias dessas business keys **embutem identificadores de registro de fonte** (`numero_guia_tiss`, `numero_contrato`, `prestador_id`), o que tensiona a proibição 5 ("nenhum ID cru de fonte em keys") de forma independente do MZO-020. Reconciliar isto muda chaves de engine JÁ IMPLANTADAS — exige janela de dual-read/redeploy ou emenda da ADR-0037. Nada foi alterado: registrado para decisão humana.

## 0.7 — Sprint 09-08-26 "dark-build offense" (2026-08-09) — execução em duas ondas

> Mandato: `docs/prompts/09-08-26_sprint.md` (analyze-AND-build: todo portão humano deixa de
> bloquear um projeto e passa a bloquear um switch — build merged, testado, inerte até ratificação,
> ativação = mudança de DADO). Cada PR passou a cadeia zero-trust completa (scouts → autor →
> verificação do orquestrador → gatekeeper adversarial ≠ autor → repair por agente ≠ autor →
> re-veredito do MESMO GK → prova live em CIB Seven → squash pinado ao SHA revisado → byte-check).

**Fase 1 (auditoria de fundação):** conformance AMH 15/15 CLEAN (digests recomputados contra o
commit pinado), matriz de interferência CLEAN, calc-audit achou **4 BLOCKER + 9 MAJOR** — todos
corrigidos e mergeados na onda 1. O achado central: **B-3 idempotência de start** — o claim durável
tratado como "start aconteceu" reintroduzia a classe de falso-sucesso do DL-0038 (pedido PAGTO
nunca iniciado reportando `process_started: True` para sempre); reproduzido AO VIVO pelo GK antes
do merge e redesenhado (`StartOutcome` resolvido contra evidência do engine, recusa tipada).

**Onda 1 — 11 PRs em main (união verificada: 5718 unit passed):** `f8f5da6` #203 ADR-0038
(DL-0044) → `d0328cd` #204 shadow RN259 → `0a66ee1` #205 reembolso M-2 → `7e48cd4` #206 codec seam
MZO-050b → `bda30de` #208 criterios M-3 → `e4912bc` #207 sweep+backfill → `2bcb628` #209 pagto B-1
→ `dc319ae` #210 PHI flag+B-2 → `410d671` #212 idempotência B-3 → `3a0ccea` #211 gateway MZO-040
em sombra (DL-0045) → `c78833f` #213 inbox MZO-060.

**Onda 2 — 5 builds gatekept + live-proven; estado no fechamento da sessão (22:35Z):**
- **Mergeados pelo orquestrador:** #214 minors (`662a75c`, revisado `ab32ac1…`, byte-check 0-diff)
  · #215 fail-safe de `tipo_carater` em adequacao (`fe282dd`, revisado `3267737…`).
- **Mergeados PELO DONO:** #202 dependabot (`1b97054`, classe user-gated) · **#216 esqueleto de
  erasure LGPD (`f7b1bf4`, 22:26Z; revisado `6b347ce…`)** — byte-check post-hoc da sessão: os 5
  arquivos substantivos idênticos ao SHA revisado; bloco CODEOWNERS + 5 rows de review-queue
  presentes na main.
- **Trem fechado pelo orquestrador #2 (2026-08-09/10):** #217 shadows DMN M-4/5/6/7 (squash
  `b9054df`, revisado `50f2690…`) → #218 pin TISS-XSD 6-gates (squash `79bb620`, revisado
  `15ffb61…`). Ambos: rebase na worktree viva (conflito SÓ em CODEOWNERS/review-queue — união
  keep-all com verificação de integridade linha-a-linha contra os DOIS lados), byte-check
  patch-vs-patch IDÊNTICO fora dos dois arquivos de append, merge acelerado sancionado
  (lane de engine PASS no SHA revisado + fast checks re-disparados verdes no head rebasado —
  justificativa registrada no corpo de cada squash), byte-check pós-merge 0-diff.

**Repo irmão (AMH):** PR **#149** aberto — addendum `wire_framing` CANDIDATE no contract manifest
deles (proposta; mergear é ato do steward, nunca nosso). Bloqueado por 2 falhas PRÉ-existentes do
workstream steward-ui deles, diagnosticadas com evidência em comentário no PR.

**Rastreio pendente (PR de close-out):** rows de evidence-ledger das PRs da onda 2 (#214–#218 não
carregaram rows — backfill), citações fantasma `notifications_bridge/consumer.py` ×5,
`transport:560`→`transport.py:1052` ×2 no packet MZO-040, cross-ref trimestral `YYYY-Qn`, nuance
ACHADO-5, teste negativo gate-6 XSD. Registro de decisões desta sprint: DL-0044 (ADR-0038) e
DL-0045 (qualificação MZO-040) em `docs/decisions-log.md`.

---

## 0.8 — Programa de Hardening Operacional (registrado 2026-08-10, sessão-3 do orquestrador #2)

> Origem: análise comparativa tripla (2 análises independentes + cross-review adjudicado com
> verificação em código) contra NVIDIA-NeMo/labs-OO-Agents (`nooa` @ `8237a88`). Veredito NeMo:
> **rejeitar como runtime/dependência; adaptar 4–5 padrões como reimplementação in-repo** (memória
> `nemo-oo-agents-evaluation`). O subproduto mais valioso foi o inventário VERIFICADO das fraquezas
> do próprio Maezo abaixo. Diagnóstico-síntese: **arquiteturalmente seguro, operacionalmente
> incompleto** — contratos fortes, realização integralmente fiada ainda parcial. Prompt de
> execução: `docs/prompts/10-08-26_hardening.md` (local-only). Detalhe vivo: memória
> `maezo-hardening-program`. Parecer do 2º analista arquivado em
> `docs/audits/architecturally safe, operationally incomplete.md` (gitignored, local-only) —
> adotado com emendas: ADR-0029 como veículo da âncora externa; censo atual já é 100% teto-humano
> (a meta é MANTER zero P0 automatizável, não burn-down); branch protection = ação imediata do
> dono, não apenas pré-condição de ratificação.

### Fraquezas priorizadas (evidência verificada em código, 2026-08-10)

| # | Prio | Fraqueza | Evidência |
| --- | --- | --- | --- |
| W1 | **P0** | Plano de autorização de efeitos INCOMPLETO: sem ToolRegistry/PEP por-tool-call nos grafos (gap T2.4), MZO-040 só em sombra, `ProcessAllowlist` (ADR-0016) com zero importers de produção | `agents/beatriz/graph.py:81-84` · `gateway/action_execution.py:1-58` · achado fase-1 sprint 09-08-26 |
| W2 | **P0** | Inferência PHI real INEXISTENTE — único provider PHI-capable é o mock sintético; Anthropic é general-zone | `runtime/inference.py:795-812,875-891` |
| W3 | **P0-cond.** | A2A não é production-grade p/ DISTRIBUIÇÃO: envelope sem assinatura (só Cards), idempotência durável opcional no seam, facts Kafka = no-op rotulado, sem mTLS/identidade de serviço | `a2a/delegation.py` (zero refs de assinatura) · `a2a/dispatcher.py:260-282` · `a2a_composition.py:20-24,186-190` |
| W4 | **P1** | Cadeia de auditoria PARA na borda do Postgres — hash-linked + `UNIQUE(prev_record_hash)` anti-fork, mas sem âncora externa contra rewrite privilegiado do banco; ADR-0029 (re-anchor assinado) DESENHADO, não ratificado nem implementado | `gateway/audit_postgres.py:28-40` · `docs/adr/0029-*` (Proposed) |
| W5 | **P1** | Drift de ledgers de controle: censo real 22 ≠ PLANS 24 ≠ handoff.yaml (internamente inconsistente) ≠ NEXT-ORCHESTRATOR — 3 superfícies, 3 idades, na mesma semana | comprovado 2026-08-10; corrigido em §0.5.3 acima |
| W6 | **P1** | Governança descrita > infra impõe: branch protection AUSENTE na main, CODEOWNERS advisory — inclusive sobre `action-approvals.yaml` do MZO-040; sign-offs não expiram | achado mzo-000; docstring de `action_execution.py` |
| W7 | **P2** | Concentração de conhecimento/autoria: 212/244 commits = 1 identidade humana (+orquestradores); sem 2º operador independente p/ runtime, DMN deploy, recovery de audit, rotação de chaves | censo git 2026-08-10 |
| W8 | **P2** | Hardening operacional do cliente de inferência: sem retry/backoff com budget de rate-limit, sem cache-aware prompt formatting, telemetria de turno fina | comparativo nooa |
| W9 | **P2** | Supply-chain: sem quarentena de idade de dependência (`exclude-newer` do uv — o repo JÁ usa uv) | ausência verificada em `pyproject.toml` |
| W10 | **P2** | Fan-in critico concentrado em 4 modulos (harness/mcp_cibseven.transport/dmn_transport/workers.base) — o numero do recon (32/30/27/23) nunca foi reproduzido (HX-14, status UNREPRODUCED); remedido por AST em 2026-09-03 (`grep -rn`/import-walk restrito a `src/`, sem testes): harness=19, mcp_cibseven.transport=24, dmn_transport=28, workers.base=19. **Regra**: qualquer refactor que toque um destes 4 exige replay da suite de integracao antes do merge — nao usar o numero do recon como criterio ate ser remedido de novo com o metodo aqui citado. | recon/as-built-map.md:114 (achado HIGH, sem metodologia registrada); remedicao 2026-09-03 nesta linha |

### Estratégia — ondas (agent-executável vs teto humano SEMPRE separado)

- **Onda 0 — Verdade gerada + cercas de governança (S, 2–3 sessões, inerte):** **✅ DONE
  2026-08-11 (PR #229, squash `336e440`, cadeia zero-trust completa: GK REVISE 3 MAJ empíricos →
  repair → mesmo-GK delta 6/6 CLOSED):** censo de
  strict-xfail GERADO em CI (artefato + gate que FALHA em drift vs ledger commitado; classificação
  P0/P1/P2/humano por constante `_*_REASON`); workflow que consulta a API de branch-protection e
  falha ALTO enquanto ausente (drift de governança vira check executável); reconciliação das
  superfícies restantes. **AÇÃO DO DONO (10 min, destrava a integridade de TUDO): ligar branch
  protection + required CODEOWNERS na main.**
- **Onda 1 — Plano de controle de efeitos (L, 8–12 sessões, engine-path):** **[scouts ×4 + design
  DONE 2026-08-10/11 · ✅ FASE 0 (build inerte) DONE 2026-08-11 via esta PR (tip `a861cde`), cadeia
  zero-trust TRIPLA — B1 núcleo de decisão/catálogo/modelo de dados + B2 registry/7 seams/5 raízes
  (inclui o caminho vivo do webhook) + B3 cerca AST §8; cada leg: autor → GK adversarial REVISE →
  repair por 3º agente → delta do mesmo GK PASS/CONFIRMED. Restam: prova live do incident-shape
  (aguarda "go" do dono), pacote de evidência de sombra §9.2, flips por classe (§9.4) e Q-1..Q-12,
  todos HUMANOS · estrutura do pacote §9.2 DONE (telemetria pendente de deployment)]** inventário completo de
  efeitos (scouts R3: nós de grafo, tools MCP, topics de worker, transports diretos) → UM
  chokepoint `ToolRegistry`/PEP por-chamada UNIFICADO com o `ActionExecutionGateway` MZO-040
  (veículo: precondição de revisita do ADR-0034 + XRD-09 do ADR-0037 — nunca design paralelo) →
  cerca CI estática rejeitando chamada-de-efeito fora de chokepoint sancionado → prova live do
  incident-shape (único desbloqueio agent-executável já identificado; aguarda "go" do dono) →
  pacote de evidência de sombra p/ ratificação Médica/ANS/Security — **com rulesets/branch
  protection ATIVOS ANTES do aceite da ratificação** (o manifesto `action-approvals.yaml` só é
  confiável com enforcement server-side). **Flip enforcing = HUMANO,
  progressivo por classe de ação (inertes → adversos/dinheiro), com mutação-de-negação por classe.**
- **Onda 2 — Inferência PHI real (L, 7–10 sessões, PHI):** **[CONSTRUÍDA INERTE 2026-08-12 —
  branch `wave2-phi-inference-inert`, legs 1-4 (capability-schema · adapter BR-resident vs endpoint
  fake · harness de canário + fix CRLF · retry budgetado idempotency-aware) cada uma autor R1 →
  GK-A adversarial → repair 3º agente → delta mesmo-GK PASS; default `noop`, `br_resident`
  não-bootável sem `MAEZO_PHI_VENDOR_DPA_REF`, retry off por default. PRONTO PARA A DECISÃO
  VENDOR/DPA DO DONO — o adapter real + prova de NetworkPolicy ADR-0017 são o próximo trem, HUMANO]**
  capability-schema além de booleano
  (região, retenção, proibição de treino, classificação máxima, fonte de credencial); adapter real
  BR-resident zero-retention atrás de `BaseInferenceProvider` (imports SÓ em
  `runtime/inference.py`); canários sintéticos (só endpoint regional aprovado, zero fallback
  general-zone, zero conteúdo em logs/métricas, reconciliação de tokens, outage→humano); egress de
  rede imposto INDEPENDENTEMENTE por política de deploy (infra/dono);
  retry/backoff budgetado idempotência-aware (W8) entra aqui. **Vendor/DPA/credenciais = DONO.
  NÃO ligar LLM mais capaz em produção antes da Onda 1 enforçar + canários da Onda 2 verdes.**
- **Onda 3 — A2A p/ distribuição (M–L, 6–9 sessões, engine-path):** **[METADE DURÁVEL CONSTRUÍDA
  2026-08-12 — branch `wave3-a2a-distribution`, legs 1-4: ADR-0039 (Proposed, digest §4.1/§4.2) ·
  outbox transacional (migração 0008, substitui `_NoopKafkaProducer`) · idempotência durável
  OBRIGATÓRIA fora de dev-local (porta fail-closed nas 2 raízes, acordo com a porta de facts
  provado) · suite adversarial (cross-tenant/crash-before-complete reais + tamper/replay/expiry via
  verifier de referência LABELADO, negativos honestos p/ Q1/Q2). Cada leg autor R1 → GK adversarial
  → repair → delta PASS; live-PG (18 binders) provado. ASSINATURA DE ENVELOPE NÃO IMPLEMENTADA —
  gated na ratificação HUMANA das Perguntas 1 (`payload_meta_hash`) e 3 (max-signature-age) do
  ADR-0039; mTLS deferido até transporte remoto]** **[DECISÕES DO DONO RECEBIDAS 2026-08-12 — as
  7 Perguntas abertas do ADR-0039 (:712-758) estão RESPONDIDAS: (1) adotar
  `payload_meta_hash: sha256(canonical(payload_meta))` na canonicalização do §4.1 · (2) incluir
  `task_type` no digest · (3) rotação ordinária de chave roda por 7 dias (donde a idade máxima de
  assinatura e o PISO de retenção de `a2a_idempotency`, que o DBA/MZO-060 então escolhe) · (4)
  custódia no mesmo seam vault/KMS de `MAEZO_A2A_CARD_SIGNING_KEY`, com keyset POR TENANT (não
  repo-wide) — assinatura de envelope também por tenant · (5) `replay_epoch` tolera janela curta de
  graça p/ o epoch anterior · (6) `MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES` confirmado · (7) mudar o
  default de `AgentRuntimeSettings` (`settings.py:38`) p/ fail-closed. DESBLOQUEIA a implementação
  da assinatura — trem seguinte, brief em `docs/prompts/13-08-26_signing-unlock.md`. O flip de
  `**Status:**` e a tabela de aprovadores do ADR seguem sendo edição do DONO, nunca de agente]**
  ADR de assinatura do envelope
  (digest canônico: tenant, task_id, origin, target, payload_hash, deadline, budget, chain;
  key-id e epoch p/ rotação/replay) → idempotência durável OBRIGATÓRIA fora de dev-local → outbox
  transacional substituindo o no-op de facts → testes orientados a ataque (tamper, replay
  cross-tenant, expiry, chave stale, crash-before-complete) → mTLS/identidade quando houver
  transporte remoto. **Pré-condição DURA para qualquer A2A não-local.**
- **Onda 4 — Âncora externa da auditoria (M, 5–7 sessões, audit-critical):** **[CONSTRUÍDA DARK
  2026-08-12 — branch `wave4-audit-anchor-dark`, legs 1-3: escritor de checkpoint assinado (seams
  signer/store + fakes LABELADOS, flag `MAEZO_AUDIT_ANCHOR_ENABLED` off) · job contínuo de verify
  Postgres↔âncora (recompute-never-trust, defesa de listing-suspect, vocabulário de outcome fechado)
  · drills tamper/restore (ALTER/REMOVE/FORK/RECONSTRUCT) provados vs Postgres VIVO. Additive-only:
  `audit.py`/`audit_postgres.py` byte-idênticos em todos os 3 legs. Cada leg autor R1 → GK-B
  adversarial → repair → delta PASS; GK-B re-rodou a prova live + bateria de forgery. ATIVAÇÃO =
  ratificação DPO do ADR-0029 (HUMANO, na fila) + KMS/WORM/IAM reais = infra do dono]** IMPLEMENTAR o
  re-anchor assinado do ADR-0029 + escritor de âncora externa (WORM/retention-lock, chave KMS/HSM,
  conta separada) flag-gated INERTE até ratificação DPO; job contínuo de comparação
  Postgres-vs-âncora; drills de tamper/restore. **Ratificação ADR-0029 = HUMANO (já na fila).**
- **Contínuo (S cada, filler p/ agentes baratos):** telemetria de turno PHI-gated (campos pinados
  hash/count, padrão do #222); `exclude-newer` no uv; cache-aware prompt formatting; piso de
  capability em release; runbooks + drills trimestrais de recovery/rotação (mitiga W7).
  **[PARCIAL 2026-08-12 — branch `waveD-docs-fillers`: runbooks W7 (5 áreas + 2 drills, drills
  live-executados) + piso de capability em release (`scripts/ci/generate_release_floor.py` + gate
  CI `release-floor`, fecha o buraco da audit §5: P0 gated independente do total) DONE. `exclude-newer`
  + cache-aware formatting entregues no trem da Onda 2. Telemetria de turno = ainda pendente]**

**Total: ~32–47 sessões-agente** intercaladas com portões humanos. Três primeiros release-gates:
(1) nenhum caminho de efeito não-sancionado + MZO-040 enforcing nas classes ratificadas;
(2) provider PHI real provado regionalmente sem fallback; (3) admissão assinada + integridade de
delegação + idempotência durável p/ A2A não-local.

### Decisões do dono — 2ª leva de ratificações (recebidas 2026-08-12)

> **Registro fiel, não re-litigável.** Este bloco registra o SEGUNDO lote de decisões do dono de
> 2026-08-12 (o primeiro — as 7 Perguntas do ADR-0039 — está no marcador da Onda 3 acima). Cada
> item é a decisão como o dono a formulou; onde há implementação, ela é citada. Agentes registram
> e implementam; **não reabrem**. O flip de `**Status:**` de qualquer ADR e as tabelas de
> aprovadores seguem sendo edição do DONO, nunca de agente.

- **Q-6 — `MAEZO_SPEC_DIR` FAIL-CLOSED. ✅ IMPLEMENTADA nesta leva.** *"Ratifique Q-6 como
  fail-closed: produção deve recusar `MAEZO_SPEC_DIR`, permitindo-o apenas em runtime local
  explícito; remova o companion bypass e aceite futuras exceções somente por bundle imutável com
  digest permitido."* Fecha o adversário A-6 (`docs/design/wave1-effect-chokepoint.md` §5.7/C-B4):
  uma única variável substituía TODO o plano de política (matriz de autonomia, action-approvals,
  L0-core, `_hard_frozen`, todo `agent.yaml`), invisível à cerca de override de
  `MAEZO_ACTION_APPROVALS_PATH`. A recusa vive em `maezo.agents.resolve_spec_dir()` — o
  chokepoint T0.3 único, portanto fechamento POR CONSTRUÇÃO, não opt-in por raiz — e levanta
  `SpecDirOverrideRefusedError`; **modo de runtime AUSENTE é PRODUÇÃO** (default fail-closed do
  ADR-0039 Q7 / Trem F). A forma FRACA (avalia-mas-nunca-enforça) e o companion
  `MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT` que a levantava foram REMOVIDOS; o companion
  sobrevive apenas para `MANIFEST_PATH_ENV`, que a Q-6 não alcançou. Ferramenta de operador
  (deploy CLI) NÃO é isenta: seu override sancionado é a flag EXPLÍCITA `--spec-dir`, nunca a
  variável ambiente. Exceção futura (bundle imutável com digest permitido) está REGISTRADA e
  **NÃO construída** — não existe escape hatch por env.
- **Q-1 — flips por classe via PR/CODEOWNERS.** Cada virada `shadow → enforcing` por classe de
  ação é um PR revisado sob CODEOWNERS, nunca um ato de runtime.
- **Q-2 / Q-10 — não-mapeados e C2 seguem em SOMBRA**, com **owner nomeado, prazo máximo e
  critérios mensuráveis** de saída. Sombra sem dono e sem prazo é desvio permanente disfarçado.
  **VALORES CONFIRMADOS PELO DONO EM 2026-08-13 e IMPLEMENTADOS como dado auto-cobrável na mesma
  data.** Os dois desvios passam a viver como bloco `deviation:` ao lado do valor que justificam,
  em `spec/policies/autonomy/action-approvals.yaml` (aditivo — nenhum valor mudou: os dois seguem
  `shadow`, `status` segue `DRAFT` e os 45 blocos de aprovação seguem `PENDENTE` byte a byte;
  declarar prazo não é aprovar):
  - **Q-2 — `enforcement_padrao_nao_mapeado: shadow`** (o default de refs não mapeadas).
    `owner_role`: *Security/crypto R1 reviewer (interim: dono)* · **`expires: 2027-02-09`** ·
    `checkpoint: 2026-09-12`. Critérios de saída (referenciados pelo `criteria_ref`, não
    duplicados no YAML): censo de não-mapeados = 0 (gerado por máquina) + ≥30 dias consecutivos de
    zero `WOULD_DENY` em refs não mapeadas (ou SLA de triagem de 7 dias) + sampler/ReviewQueue
    mergeados (Q-3) + benchmarks da Q-9 assinados.
  - **Q-10 — classe C2 `leitura_phi_clinica: shadow`** (leituras PHI via FHIR — a classe que a
    pergunta da Médica realmente endereça: o design §10 Q-10 ancora em "a denied FHIR read
    degrades to a dossier gap note (`rafael/graph.py:44-48`)", que é o `SHAPE_LACUNA_DECLARADA`
    dessa classe e de nenhuma outra C2). `owner_role`: *Diretor(a) Médico(a) (interim,
    deadline-enforcement only: dono)* · **`review_by: 2027-02-09`** · `checkpoint: 2027-02-09`.
    Critérios: aprovador Médico nomeado existe + um trimestre de telemetria de sombra C2 + triagem
    clínica de todo `WOULD_DENY` + limiar de carga que a Médica ratifique + interação com
    consentimento resolvida (nenhum flip de C2 com `consentimento_exigido` enquanto o adapter de
    consentimento estiver desfiado — Q-5).
  - **RENOVAÇÃO (decisão do dono 2026-09-20, PR `fix/d7-exception-renewal-2027`).** O `expires` de
    Q-2 (original 2026-11-11) e o `checkpoint` de Q-10 passaram a **2027-02-09** (= `review_by` de
    Q-10), alinhando as TRÊS cercas same-date (desvios Q-2/Q-10, marcador de ciclo de vida
    `lifecycle.expectedFailUntil`, xfails do catálogo D7) num ÚNICO momento de revisão do dono em
    vez de três muros no mesmo dia — a saída do catálogo D7 depende do programa D7 secured-startup
    (#438 Slice A pousou 2026-09-20); checkpoint de revisão sugerido: 2026-12-15.
  - **AUTO-COBRÁVEL, sem renovação silenciosa.** `scripts/ci/check_deviation_expiry.py`
    (`make deviation-expiry-check`) roda no job `artifact-validation`, que reporta como o check
    **obrigatório** `validate-artifacts` — logo o prazo é bloqueante de merge desde já. A partir do
    dia seguinte à data, com o valor ainda em `shadow`, TODA PR fica vermelha; bloco ausente ou
    malformado também é vermelho; 14 dias antes há aviso alto. Sair do vermelho é ato humano: virar
    o valor para `enforcing`, ou o dono re-ratificar um desvio NOVO E DATADO em PR de dados sob
    CODEOWNERS (Q-1) — PR que é verde por construção, porque o gate lê as datas da árvore em teste.
- **Q-3 — sampler + ReviewQueue são CONSTRUÍDOS ANTES do enforcement** (cláusula de amostragem do
  ADR-0034, design §5.6). Não se liga enforcement sem a superfície de revisão pronta.
- **Q-4 — quatro ações canônicas, SEM aliases:** inferência de zona-split, A2A e leitura
  populacional entram no vocabulário como nomes próprios. Nada de camada de alias PT↔EN
  (`pep.py:12-18`: um mapa de alias é um segundo vocabulário e uma superfície fail-open).
  **STRINGS FIXADAS (2026-09-06)**, como DADO no bloco `vocabulario_l0_canonico` de
  `spec/policies/autonomy/action-approvals.yaml` — a Q-4 decidiu NOMEAR; o que faltava eram as
  strings:
  - `generate_model_inference` — inferência na Zona Geral (`inference.generate`).
  - `generate_model_inference_phi` — inferência na Zona PHI/Financeira
    (`inference.generate_phi`). Duas strings, não uma com dois usos: a zona é o fato que o
    aprovador precisa ver sem carregar o prompt. O `PhiZoneRoutingError` do provider segue
    fail-closed INDEPENDENTE deste vocabulário (I-6) — nomear não é o controle.
  - `delegate_agent_task` — delegação A2A por envelope assinado (`a2a.delegate`).
  - `read_population_aggregate` — agregados populacionais/atuariais k-anônimos
    (`population.actuarial_risk`, `population.population_metrics`). Um nome para duas operações é
    escopo de ação, não alias. Deliberadamente NÃO é `read_phi_data`: agregado k-anônimo não é
    PHI (ADR-0042).
  - **NOMEAR NÃO É INSTALAR.** Nenhum destes nomes está em `L0-core.yaml`: toda entrada carrega
    `instalado_em_l0_core: false` e `ratificacao.ratificado: false`, com accountability em
    `PENDENTE`. Instalar na matriz é ato HUMANO ADR-0008/0025 COM o cross-check de
    `_hard_frozen.yaml`; até lá o catálogo mantém `autonomy_action=None` e a camada L-2 do PEP de
    efeito NEGA com `VOCABULARIO_PENDENTE`. `tests/unit/sec/test_l0_canonical_action_naming.py`
    prova o piso: nem um manifesto forjado para `RATIFICADO`, com todas as aprovações
    preenchidas, abre ALLOW para essas operações.
- **Sequência humana de flips de enforcement C0→C4 — fixada por escrito (2026-09-06). Nenhum
  passo é ato de agente.** A Q-1 já dizia QUE cada virada é um PR sob CODEOWNERS; isto fixa a
  ORDEM e os passos. Ordem de degrau, crescente e sem pular: **C0 (leitura interna) → C1
  (notificação) → C2 (PHI ou modelo) → C3 (mutação de engine) → C4 (adverso / dinheiro /
  regulatório)**. Qual classe está em qual degrau é dado de código (`RUNG_C0_LEITURA_INTERNA` ..
  `RUNG_C4_ADVERSO` em `src/maezo/gateway/effect_classes.py`) e NÃO é redigitado aqui — uma
  segunda cópia só existiria para divergir. Passos, por classe, nesta ordem:
  1. Os três domínios preenchem CADA UM o seu bloco em `acoes.<classe>.aprovacoes`. Preenchimento
     parcial é recusado pelo loader — nunca lido como ratificação.
  2. `status: RATIFICADO` — uma única vez, no primeiro flip de todos.
  3. `modo: enforcing` — uma única vez, no primeiro flip de todos. É o TETO, não o interruptor.
  4. `acoes.<classe>.enforcement: enforcing` — **este é o interruptor por classe.**
  5. Verificar CONTRA TELEMETRIA: o evento tem de virar `action_execution_gateway_enforced` e o
     campo `mode` tem de ler `enforcing`. Um flip que falhou é, de fora, idêntico a uma sombra
     saudável — por isso verificar é PASSO, não zelo.
  6. Soak pela janela acordada; só então o degrau seguinte.
  7. **Ato terminal, depois do último degrau:** `enforcement_padrao_nao_mapeado: enforcing`,
     restaurando o XRD-09 do ADR-0037 na letra.
  Pré-condições que atravessam a sequência e não são dispensáveis por conveniência: sampler +
  ReviewQueue construídos ANTES de qualquer enforcement (Q-3); benchmarks p99 de PEP e auditoria
  separados e assinados (Q-9); guarda de single-tenant para o manifesto global (Q-8); nenhum flip
  de C2 com `consentimento_exigido` enquanto o adapter de consentimento não existir (Q-5); e as
  duas PRÉ-CONDIÇÕES já registradas dentro de `acoes.consulta_processo` (resíduo pós-claim; e o
  alcance do enforcement sobre daemons sem registro de capacidade). O detalhe operacional vive em
  `docs/design/wave1-effect-chokepoint.md` §9.4; o que fica aqui é a ORDEM e a AUTORIDADE — **cada
  passo é ato humano de Security, jamais de agente e jamais de runtime.**
- **Q-5 — consentimento NUNCA é declarado implementado até haver adapter completo.** Enquanto só
  existir a porta (`ports/consent.py`), a perna L-4 permanece honestamente não-implementada.
- **Q-7 — rollback = restart**, com **SLO e teste** que provem a latência de rollback (em vez de
  TTL de cache re-lido, que é a alternativa descartada).
- **Q-8 — manifesto global SOMENTE com guarda de single-tenant.** Um registro de ratificação sem
  eixo de tenant só é correto se algo impedir o deployment multi-tenant de usá-lo.
- **Q-9 — benchmarks de p99 SEPARADOS: PEP e auditoria.** Um número agregado esconde qual das duas
  metades gastou o orçamento; `audita_antes: true` adiciona escrita durável e é medido à parte.
- **Q-11 — allowlists FECHADAS por agente** (`process_keys` por agente vale, não o
  `DEFAULT_ALLOWED_PROCESS_KEYS` global).
- **Q-12 — proteger a `main` + apagar 12 branches verificadas. AMBAS FEITAS 2026-08-12** pelo
  dono. Fecha a pré-condição de governança do W6/`mzo-000` que a ratificação MZO-040 exigia.
- **ADR-0039 — revisões + `replay_epoch` ciente de tenant**, e **análise DBA/jurídica ANTES** de
  fixar a retenção de 30 dias (a retenção é escolha do DBA/MZO-060 informada pelo prazo legal, não
  um default de código).
- **Correções PRÉ-FLIP (bloqueiam a virada, não a construção):** Job de migração do Helm ·
  default do WhatsApp · LEG3-B · regex de tenant.

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
- Cross-process integration tests (ex: CONTAS→PAGTO / CONTAS→FRAUDE, AUTH→ESCALATION) — a aresta CONTAS→RECURSO foi deletada por ADR-0040

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


## 2026-10-04 — Encerramento da revisão de feedback BPMN CA

Os quatro BPMNs adaptados e os dois documentos acompanhantes foram refinados e verificados como devolutiva à equipe autora. Esta entrega não implementa processos, agentes, workers ou integrações MAEZO. Os originais de terceiro continuam locais e intocados; a evidência detalhada permanece deliberadamente fora do Git em `docs/audits/BPMN-CA-2026-10/feedback/revisions/20261004T153751Z-codex-refinement/`.

O bundle inicial `e73cbc80fe82a8eec4f7be51e1c26054c80a1f8f022c1cf221827e2be4f8fefa` tem dois gates frescos independentes. A edição posterior do Modeler em Compras foi preservada, reparada pontualmente e revalidada (366 verificações semânticas e 16 visuais); source final de Compras `067cf607ddb0c6647598d1b52d1237f2e1eafbbfc944aedcfbdddf91bcf12cb9`. Engine e contratos externos não foram executados; sugestões mantêm essa qualificação.

O flywheel registrou seis lições e um checker read-only com controles sobre o snapshot real rejeitado, incluindo quatro aliases businessKey→journeyId. A checagem anterior case-sensitive não reconhecia processBusinessKey; o novo controle identifica o caso sem reescrever a evidência congelada. Artefatos de retomada: `session-close/FLYWHEEL-LESSONS.md` e `HANDOFF.yaml` no diretório de evidência acima.

Pacote de publicação: branch `codex/feedback-bpmn-refinement-closeout`. Estado Git/PR/CI e disposição de integração são registrados no recibo de encerramento desta sessão; não inferir aprovação de main, produção ou outras PRs desta nota. O programa de implementação MAEZO mantém seus próprios gates e responsáveis. Não retomá-lo automaticamente após esta devolutiva.

## 2026-10-04 — Implementação prestador (Codex, em execução)

Autorizada execução de `maezo-provider-implementation-pickup-prompt-v2.md` com análise/decisão delegada, especialistas e revisão independente. Bundle prestador cc7f4248 teve admissão independente PASS; V2 histórico íntegro mas admissão atual FAIL_SOURCE_DRIFT (Helena/DL-0071). A revisão de execução fora dos bundles congelados aguarda dois gates independentes. Não há fonte/ativação fictícia nem pacote funcional encerrado.

Evidência privada própria sob custódia em ROOT `docs/audits/PROVIDER-IMPL-2026-10/20261005T024404Z/`; fontes assinadas somente leitura. ROOT preserva WIP anterior; implementação nesta branch isolada `codex/provider-implementation`, baseline cbf630bc. Núcleo PR #633 /9680e5db é dependência técnica candidata desabilitada sob verificação, não PW5 concluído. Protocolos PW0, desenhos PW1/PW2 e mapeamentos PW3/PW4 em andamento; engine/full-unit serial por ROOT.


### 2026-10-05 — Gate da revisão prestador para construção

PROVIDER-EXEC-V1-ARCHITECTURE e PROVIDER-EXEC-V1-SECURITY PASS no mesmo digest `a7c9d3722c2ba9f671b637c9740914a62aa3067ea46f508470cedd5e68fc593c`, registrados e relidos no SQLite próprio. Original V2 SOURCE_DRIFT exit1 preservado; revisão independente atual qualifica somente construção delimitada. ADR0063 aceito nesse escopo. Autoria PW1-A allowlists e PW2 OP12/core em paralelo, workers compartilhados serializados. Nenhum gate funcional ou ato externo/ativação declarado.

### 2026-10-05 — Reconciliação da base integrada

PR #633 integrado por squash `54fa791298bc59a92a1c673246b1568fe93e27e2`; `main` consultada em `feba801cf5eea6383f2db88bb02d52d9911049f3`, incluindo análise CA do PR #634. Merge explícito preserva os reparos provider; seis conflitos de add/add têm bytes incoming iguais à dependência pinada `9680e5db`. Evidência própria em `evidence/current-main-base-delta/`, sujeita à revisão independente do delta. PW1-A tem três testes CIB reais PASS sem skip. Rodada direta D1 exit0 teve oito skips e não qualifica PG; nova rodada hermética CIB+PG está em execução. PW1-B passou revisão delimitada de código; PW1-C tem REVISE por ACL/TRUNCATE com reparador terceiro. Nenhuma fonte produtiva, ato externo, ativação ou pacote funcional integral declarado.


## 2026-10-05 — Sucessor DUR em execução

Owner ROOT; checkout `/Users/familia/.codex/worktrees/913e/maezo-operadora`, branch `codex/v21-durability-successor-20261005`, baseline `feba801cf5eea6383f2db88bb02d52d9911049f3`. PR633/634 merged confirmadas; CI main37258786442 SUCCESS. Freeze DUR rejeitado preservado no TaskWT e tar0242ae1a; 31 paths source/tests/design rehashados para reparo no candidato, sem promoção dos gates antigos.

Fila: DUR1 reparo test-only (postgres_race_author); DUR2↔DUR3 contrato de autoridade (authority_contract_architect), aguardando revisão independente authority_independent_verifier antes do source. Admission/custódia e source/runtime readiness em leitura independente. ROOT mantém migração, composição, Git e lanes pesadas serializados. Write sets disjuntos; limite quatro implementações/dois aprovados/um candidato amplo. Evidência privada `/Users/familia/.codex/private-evidence/v21-dur-successor-20261005/`.

Liberação: sucessor publicado e qualificado com transferência de pins/evidências; TaskWT e ROOT originais permanecem preservados. Ratificação humana, source/provider, operacionalidade e produção continuam gates próprios; sem merge futuro autorizado.


### Gates DUR — reprodução independente e reparos focais

DUR1 source/component gate `c113af8243f1d1a3c4ff726dbc423607974e33a1c97132a1f6440d7b32e0730f`: 40 UNIT + 28 PG reais PASS, sem skips/failures; teste race SHA730ae2b. Migração ROOT0018 original1b6708 REVISE PG-MIG-F01: SQL comment split; terceiro postgres_race_author reparou somente comentário, freeze7f988429, delta original PG reviewer pendente. Container próprio observado idcdc3dfd/imagef4c66, novo mapeamento127.0.0.1:32769; só lane PG própria autorizada, sem flags reais.

DUR2/DUR3 R3 contract REVISE `862f2c247175bd69d3736b93a6f4a2249c55d5f9d0c0aaf78034250e46a0128b`: teto acumulado/autorização original não chegam ao efeito interno do source; references MD privadas ausentes no pacote. Third contract repair source_runtime_readiness_steward em R4; implementação source aguarda delta independente. Residual F01 original reproduzido com efeito após expiry; original REVISE preservado.

Admission custódia PASS parcial; original checkers SOURCE_DRIFT em cinco arquivos integradosfeba, gate semântico fresco separado. AMH quinze pin digests PASS, remote62779aee com APIs manifest byte-identical; sem nova autoridade comercial inferida. Runtime composition specialist prepara contrato completo, roots compartilhados ROOT serial. Evidência privada nesta sessão; nenhum merge/PR novo/produção declarados.


### DUR1 publicação técnica independente

Pacote `DUR1-JOURNAL-COMPONENT`: journal PostgreSQL default-disabled, migration0018 e inventário estrutural34relações com todas decisõesDPOpendentes. Gates independentes PG28+12, UNIT40 e lifecycle135 PASS; nine existing wamid PASS e um outboxhumano SKIP local dependem do engine hosted. Public projection docs/gates/v21-durability-successor-20261005/journal-qualification.json; draft/source custody/CI separados, sem merge futuro autorizado. DUR2/3 source e runtime seguem WIP separado no checkout, não incluídos automaticamente neste pacote. ROOT preserva inputs e pins até publicação/CI/gates frescos.


### DUR2/DUR3 autor e runtime — contrato admitido, implementação em curso

Novo branch `codex/v21-journey-effect-boundary-20261005`, baseado no incremento journal `a6059c39f58b1601c99947ad76bf88bbe96b4646` (dependência técnica explícita, ainda não em main). Quatro contratos R5 admitidos pelo verifieroriginal próprio em876de480; hashes818a4663/526e76a4/62ac7e3b/5bc851ad compostos. Source authorjourney_gateway_author e childnative_domain_boundary_author em write sets exclusivos; ROOTadmission/service serial. RuntimeR2 contract gate34b6052f admitido, runtime_composition_specialist tem somente runtime.py+doistestes; registry/ingress/resume ficam ROOTserial, sem ativar novos memberships/canais.

Journal21paths source/component+schema+pendingprivacy commitados separadamente; source/CI/PR/gates próprios. Author checks da admissão privada57PASS; source afetado inteiro ainda aguarda review distinto. RootRED request-copy regression e fixturemutation preservados; negative caller-mutation assertion intacta. Codegate/source/runtime/CI/ratificaçãohumanas continuam separados.


### Source e runtime qualificados para composição técnica

DUR2/DUR3 fonte UNIT277 e delta68af0733; Runtime ROOT+bindings/currentness589UNIT e delta1ddf7f8c, originaisREVISE/copypoisons preservados. Defaults off; canais/memberships/owners/receipts operacionais não admitidos. Source congelado será checkpointado e composto com journal PR644 C5 (`c5d9cb79`): CI37269454216 source/testPASS26.190UNIT+807integrações/40PG, CODEOWNERheadapproval pending. Composição ROOT, exact dependency refresh e PG22/runtime ainda pendentes; nenhum merge emmain autorizado. Evidência privada v21-dur-successor-20261005; todoWIP/pins retido até publicação/CI/gates finais.
### DUR1 SEC-DUR1-F01 — rejeição preservada e reparo qualificado

a6059c39 não publicado: gate segurança4f578c88 REVISE por autorização de dados antes de await posterior ao commit. Terceiro postgres_race_author reparou somentepostgres.py+unitboundary: source4a94e672/test71211b18, freeze3abf829d. Deltaoriginal segurança d9d9ff06 PASS_CODE_ONLY; PGverifier369ba56e novo45UNIT+40PG e cinco controles reais PASS, zero skips. Metadados negados após revogação sem apagar commit/fence; limites de fonte rechecados sincronamente depois do últimoawait. SQL AST/schema/migration unchanged demonstra reuse limitado do gate mínimo de roles, source gate antigo não promovido.

Worktree próprio gerenciado dur1-journal-security-repair, branchjournal, owner ROOT; candidato atual913e fica comDUR2/runtime em reparos separados. Projeção/ledger atualizados sem duplicar evento; gate do novo commit e hostedCI permanecem pendentes. Nenhum push/PR/merge/flag/deploy nesta etapa. TaskWT antigo observado ausente, não removido por esta tarefa; tar0242ae1a/manifest10f61ce5 rehashados intactos emcustódia privada. Liberação doWT novo só após CI/gates frescos e pins/evidência transferidos.


### PR644 primeiro CI e reparo estrutural

PR644 draft/anexado em8176317d, CI37266143835 terminalFAIL:26188PASS/132SKIP/5XFAIL/2FAIL,0error/duplicate,24ZIPdigests verificados e625files rehashados. Integrações não executadas; sourcePGlocal não promovido aCI. Failures: DPO ScopeB omitiasix newrelations ePortalchain esperava0017. Terceiros independentes repararam docf870/test622fc/portal8a08; gate segurançaaf51+chain6596, DPO21+portal1+erasure19/chain64 PASS. Não houve alteração de journal/sourceSQL/schema ou humanretention/signatures. Rootatualiza projeção/ledger semduplicar evento; novoCI automático exigido no próximoHEAD. Flipgate exige CODEOWNERqualifiedapproval noheadexato (autor não selfapprove); nenhuma solicitação a terceiros, aprovação ou merge inventados.

### Incremento DUR-JOURNEY-RUNTIME pronto para draft e CI próprio

Composição exata journal PR644C5 + gateway/journeys R5 + runtime/root qualificados. Verifier distinto reproduziu source277, root/runtime589 e journal45 UNIT; PG completo22 PASS após reparo terceiro somente do fixturetenant, original21PASS/1FAIL preservado. Journal/migration40PG e CI próprio PR644 permanecem gates separados. Seis propostas W0/W4/W5 publicadas por allowlistfinal1a0f6cbc (29requisitos/21conceitos/13métricas, 314controles); todos atos de fonte/humano e valores UNMEASURED preservados. Projeções públicas emdocs/gates/v21-durability-successor-20261005; hostedCI/finalcandidate ainda pendentes, defaultsoff, nenhum futuro merge/main/produção autorizado.

### Orientação adicional do usuário — integração e manutenção

Usuário autorizou em2026-10-05 esforço para fechar/integrar as PRs próprias644/646 emmain, com commits menores nos limites naturais e manutenção conservadora após cada merge. Essa orientação substitui a limitação anterior de não inferir merge futuro somente para estas PRs próprias, mantendo CI exato, revisões independentes e CODEOWNER exigido. Leitura GitHub atual: mainfeba, ambasdraftOPEN/semreviews;644C5technicalCI+gatesfinaisPASS masflipCODEOWNERpending,646fc5CIFAIL preservado. Novo checkpointbdefe40e contém reparos originais62bed6e0/1b1ee9de e agregado d119ba9a; PGcomposto27 aindaWIP/gate. Nenhuma aprovação humana/política/produção foi inferida. Apósmerge executar seteetapas CONTRIBUTING; preservar WIP, evidência ignorada exclusiva e pins.


### Snapshot técnico pré-publicação do sucessor — 2026-10-05

PR644 integrada em d6fa26f5, árvore igual ao C5 qualificado; manutenção conservadora concluída e worktree próprio recuperavelmente arquivado. Usuário autorizou merge das PRs próprias com CI técnico verde e dispensa somente CODEOWNER quando for o único gate restante; nenhuma revisão humana foi inventada. A orientação anterior que mantinha CODEOWNER exigido foi substituída por essa autorização explícita.

PR646: source/test checkpoint70c43a62, composição1080a320 com main df8796ca (upstream647/648 somente três caminhos de workflows/infra, todos inputs source/test/contratos idênticos). Conflitos resolvidos preservando exatamente os contratos R5 admitidos. Driver currentness foi reparado por terceiro, deltas independentes b961d9a5/a9c10c75 fecharam os replays com zero efeitos/callbacks. PG local127 PASS,90 casos reais+37 puros, zero skips/xfails/retries; source/schema/roles/conexões restaurados, fixtures sintéticas sem aprovação operacional. Todos resultados RED anteriores preservados. Type548, lint1367 e artifacts0errors PASS; wheel corrente846 source/resources idênticos e smoke do módulo/asset agregado PASS.

Esta entrada é snapshot pré-publicação: novo CI hosted, gates finais frescos, merge646 e manutenção ainda pendentes. Per-binding owners/source/privacy/human/produção continuam não ratificados; AMH publicado sem drift não os habilita. Projeções e recibos em docs/gates/v21-durability-successor-20261005; ROOT mantém custódia privada e pins até readback final.


### Composição v21/provider publicada em main — snapshot pré-CI

PR646 head58769f42 passou CI37328889978/Security37328890038 no base df8796:27528UNIT e894integrações,26xfails estritos executados,90PG reais+37puros no corpus127,34ZIPs verificados. Esse resultado foi preservado sem promover aprovação à main posterior d2b31131/PR645. ROOT compôs main emc53a64ba e preservou ambos contratos. Mapa independente d0dedfec admitiu coexistência condicionada; autorjourney_gateway_author e ROOTserial fecharam guards estritosv21 nos seamsR5, mantendo12operações provider/generic e autoridades separadas. Checkpoint1b0b6fa5: sourceverifierf4b4d51a reproduziu831UNIT+37controles; execução real9211cc7a127PASS(90PG37puros),zero skips/xfails/retries e recursos restaurados. Type555, lint1388 e artifacts0errors PASS; wheel04bc2674 tem856source/resources iguais e smoke agregado PASS. Novo hostedCI/tree combinado, gates finais frescos, merge646 e manutenção ainda pendentes. Sem contato/WIP de outra frota, migração JR1/JR2, grant operacional ou ratificação humana por inferência.

2026-10-05 — ROOT compôs main298c89eb em007da1aa: apenas publicação canônica provider_contract_read/version2 (PR656) e atualização Maven compiler3.16 (PR650) sobre248f. Gate independente de política9c50e98e confirma hard-set e todas ações anteriores preservados. Os inputs Python R5 permanecem iguais ao source1b0b efetivamente executado; recibo127PG conserva esse escopo. O revisor identificou1recurso empacotado alterado(pom.xml):855/856 iguais ao wheel04bc, que agora é histórico; novo wheel em validação. Novo CI do candidato combinado, build Maven e gates finais continuam pendentes; nenhuma ratificação operacional inferida.

Composição007da:233UNIT focais real loader/PEP/R5 PASS semskip/xfail; artifacts0errors0notices e Ruff novo teste PASS. Novo wheelda08d222:856source/resources atuais iguais ao ZIP, apenaspom alterado contra04bc; smokeisolado1PASS. Custody6b755ee0 e reciboUNIT2ea6737e; build Maven/novo CI/final/merge pendentes.

### Status WIP — 2026-10-05T18:39:53.741002+00:00

Estimativa: **95% da entrega técnica em andamento**. Prioridade do usuário: finalizar o WIP existente antes de abrir novas frentes. ROOT está na worktree `/Users/familia/.codex/worktrees/913e/maezo-operadora`, branch `codex/v21-journey-effect-boundary-20261005`. O destino é `main` pelo PR646; publicação da branch não equivale a integração.

- [x] PR644 integrado em `d6fa26f5`; manutenção de sete etapas concluída e worktree do journal arquivada com recuperação.
- [x] PR646 publicado: head `aadf1ac93e1e8ace342ba2285b9642dcf2d402ce`, tree `f29b049cb039398c41f61eb0bbc7610575ce2882`. Todo código desta entrega está no GitHub; checkout sem WIP de código.
- [x] CI37349288758 e Security37349288793 SUCCESS na base `298c89eb`: 27.994 UNIT PASS, 132 skips e 5 xfails; 958 integrações PASS, 26 xfails estritos executados e dois companions inativos. Conjunto composto: 90 PG reais + 37 controles puros; 64 casos novos de prestador. Custódia: 34 ZIPs, 1.363 arquivos, zero divergências; recibos `8fd960b1`/`42e02b2c`. Native v2 HTTP root_fixture não foi selecionado/executado. Suite Python3.14 do agregado permanece explicitamente não qualificada.
- [x] Dossiê W0 privado concluído por revisão independente, reparo terceiro mínimo e delta original `d65c2a62`; nenhuma autoridade operacional foi ratificada.
- [x] PostgreSQL próprio parado e preservado após zero schemas/clientes de teste; demais containers intactos.
- [ ] Compor `main 76e0094d055b8a60a470a5db6a96dcea8883d4f0` (PR657, TISS) no PR646. Intake independente `75f286e5` e revisão de build/packaging admitiram composição, exigindo novas verificações.
- [ ] Verificar wheel instalado fora do checkout, assets TISS/agregado, build/fences e testes focais; criar checkpoints coesos e publicar no mesmo PR646.
- [ ] Concluir novo CI/custódia e gates finais no head/base atualizados. Segurança `096dc3d` cobre somente aadf/base298c; arquitetura reteve a assinatura em `a2e14df1` por drift de main.
- [ ] Fazer merge autorizado do PR646 com CI e gates aplicáveis verdes. Dispensa CODEOWNER somente se for o único restante; recibo humano `a9b13ada`, sem criar revisão fictícia.
- [ ] Conferir main e executar as sete etapas de CONTRIBUTING. Worktree do agregado fica retida até igualdade dos três arquivos em main e liberação de consumidores; depois snapshot e archive conservador. Preservar checkout principal sujo e worktrees de terceiros.

Custódia: `/Users/familia/.codex/private-evidence/v21-dur-successor-20261005/`. Cards/mandatos operativos, grants/retention/DPO, ativação e baseline/ROI seguem gates próprios. Próxima sequência: composição657 → verificações → CI → gates finais → merge646 → manutenção. Sem nova frente ou novo PR.

2026-10-05 — WIP composto em155132c5/main76e sem conflitos após intake75f286e5/abc56e46. Focais460 PASS sem skips/xfails; lint1392/type557/artifacts0erros PASS. Wheel instalado3d1cfbc1/receipt4dccbaec verifica858source+120assets=978, dois perfis TISS offline e seis negativos, asset agregado9f706 sem grant numérico. Build app Docker permanece não qualificado: pull GHCR uv0.11.26 falha antes do build; manifesto público exato confirmado, diagnóstico isolado em andamento. Próximo candidato será publicado no mesmoPR646 com novoCI; nenhum gate de imagem/origem/humano/produção promovido.

2026-10-05 — WIP646 código fechado emcff107b5 após originaldelta2500f80e/segurança226aff:72TISS(59originais13novos) e7controles originais independentes PASS; doisP2 fechados por cinco guards exatos, sem alterar pins/reasons/limits/API normal. Lote composto473 PASS; lint1393/type557/artifacts0erros. Wheel2e197ba2 instalado19af2ba6 e imagemLinuxAMD64cd7ee828/bedb27d0 verificam978arquivos atuais, dois perfis TISS/seis negativos/cinco carriers-hook0, sem rede/startup/deployment. Scanner exato684c7862/ac6799bb preserva futuroscanários. Histórico98/155 não promovido. Próximo: congelar metadata e publicar mesmoPR646→CI completo atual→freshfinal→merge autorizado→seteetapascleanup; sem nova frente.

2026-10-05 — Pré-publicação WIP646 concluída: source/doc/artefatos atuais ARCHc1bbb980/SEC2b3ded62 PASS; scanner original36ae67c4 com somente três fingerprints históricos classificados, quatro canários futuros detectados e regras intactas. ROOT atualiza exclusivamente o digest existente de .gitleaksignore no inventário; mapas de auth/recibos/source permanecem sem reorder. Código/artefatos cff107 imutáveis. Próximo: push coeso no mesmoPR646 e CI completo do candidato, gates finais, merge e limpeza segura.

2026-10-05 — WIP646 base423 composta emc093241f/treeff1c idêntica ao merge GitHub510. Intake4files1df96a70 PASS; SOURCE R5/TISS intacto. Currentwheelb21e905a/4c699876 e image7f0f97f7/d63cba43:978bytes atuais, somente3resourcesMaven alterados975iguais, smokesoffline/nohooks/noexportpermission PASS. CI441 parcial não qualificado por hostedrunner não adquirido;8f77a60b/acbbfb12 preservam3shards2446PASS2SKIP,6shards/Java/secrets/flip não executados. Não cancelar workload ativo nem mascararFAIL. Publicação corretiva adota inputs reais Maven+novo passo SAMEJAR compatível com versões implantadas; CI e freshfinal atuais exigidos antesmerge/cleanup.

2026-10-05 — Finalização WIP646: CI1dd attempt3 SUCCESS (28156UNIT,958integrações; custódia145dccb6/3b6755aa), Security rejeitou SDK0.4.2 HIGH. Reparo terceiro784185bb, deltas originaisSEC04f18adb/ARCH0898163d: SDK0.4.4 pinado, exceção temporal somente desse pacote,125outras versões intactas; cinco markers redundantes admitidos por42casos. Main46be apenasdocs composta emaa9d91; checkpoint46b554bb aplica exatamente2arquivos. Ambiente frozen atualizado,1410focais semskip/xfail,lint1393/type557/artifacts0erros. ImagemAMD64c1cf5f4 construída; wheel/imagem em verificação independente. Pendente publicação final→CI+Security atuais→gates finais→merge646→sete etapas de manutenção. Nenhuma autoridade operacional inferida.

2026-10-05 — Artefatos atuais46b554 qualificados independentemente: wheel3cf3fc50/81994c3d e imagemAMD64c1cf5f4/70c615b4;978project+48SDKbytes exatos,12controlesactions/18malformed/3legacy,2TISSprofiles/7carriers/numericFalse/net0. Documentação pré-publicação congelada sem promoverCI histórico. Próximo único WIP: push646→CI/Security frescos→final ARCH/SEC→merge→cleanup.



<!-- PROVIDER-SPRINT-MULTI-ORCHESTRATOR-V1:BEGIN -->
## Sprint prestador — plano canônico de execução e coordenação multi-orquestrador v1

**Autoridade única:** `/Users/familia/code/maezo-operadora/PLANS.md`, este bloco v1 e concessões append-only emitidas por ROOT abaixo dele. A autorização do dono para implementar o sprint e separar orquestradores foi concedida em 2026-10-05. Esta seção incorpora o plano aprovado; os prompts são instruções de papel, nunca planos concorrentes. Cópias locais de PLANS.md são históricas e não autorizam despacho.

### Entrada e estados

- Etapa 0 permanece prioritária: fechar WIP B/D/nativo antes de abrir novas implementações. D5463070 tem dois gates de código; SourceC R5/8cb1903a tem dois gates de desenho; NativeV2/475114c4 foi preservado em 2bd7157 e seus re-gates são separados da instalação. Reconsultar os registros próprios: esta nota não é prova de gate.
- B original d216:26PASS/1FAIL, diagnóstico preflight sem corpos e diagnóstico controlado43988:27PASS são resultados distintos. Causa histórica não determinada; disposição independente obrigatória, sem retry até verde ou classificação automática como benigno.
- Estado de cada lote: decisão delegada, código/testes, publicação, integração em main, evidência externa e ativação separados. Preservação local, UNIT, compilação, coleta e desenho não encerram runtime.
- Main muda: baseline e compatibilidade de CIB/Java/Tomcat/JAR/imagem/classpath são verificados no candidato atual. Resultados de bibliotecas/pins anteriores não são transferidos silenciosamente.

### Sequência do plano aprovado

1. **E0:** preservar NativeV2/handoff, obter dois re-gates originais, disposition B, CA genuína, medições/receipts preliminares independentes, owner/faseB e native não vazio. Qualificar D/C com casts reais, catálogo/pins e expiração durante espera do ACK; publicar incrementos coesos, CI SHA entregue e manutenção. SourceC antigo inseguro fica fora da entrega.
2. **E1:** reconciliar adendos de ADRs/contratos B/CONTAS/PAGTO exigidos por R5; congelar interfaces, migração, preimages, autoria e prova de qualificação. CORE coordena SQL/Java/Python SourceC; CHANNEL fecha PW1 OP09/OP10/OP07/canal. Primeiro smoke prova mesma conexão/transação física CIB/source, não apenas DSN igual. PW2 exige fonte verificável e ≥2 consumidores reais; sem instrumento válido unknown/SOURCE_UNAVAILABLE, sem valores fabricados.
3. **E2:** PW3 NETWORK e PW4 RECURSO em paralelo somente após PW2 e gates próprios PW1. CORE é único dono das projeções/núcleo compartilhados. PW3 prova produtor de rede/emissão administrativa/receipts/starts/correlação/routingDMN/due process. PW4 prova D3/PV13/simetria OP12/status factual/PV16, com RX4/atores/grupos/timers preservados.
4. **E3:** CORE conduz PW5 após PW1+PW2+PW3+coreV2 qualificado, sem dependência artificial PW4. Mesmo código/versão em JR1/JR2/JR3, matriz replay/concurrency/tenant/currentness/purpose/actor/receipt. NETWORK pode receber GP4, RECURSO GP3 e PERFORMANCE coorte/OP06, um candidato PW6 por slot e admissão individual. Pacientes fora; sem ranking/veredito/consequência automática; fatos externos ausentes restringem apenas os efeitos dependentes.

### Topologia e capacidade global

| Fase | Orquestradores ativos | Autores ativos | Revisores independentes | Total máximo |
|---|---|---|---|---|
| E0 | ROOT | especialistas já atribuídos ao WIP, conforme slots reais | pool arquitetura/segurança | 9 global |
| E1 | ROOT + CORE + CHANNEL | CORE: SQL/Java/Python; CHANNEL: canal | 2 no pool global | 9 |
| E2 | ROOT + CORE + NETWORK + RECURSO | 1 especialista por satélite | 2 no pool global | 9 |
| E3 | ROOT + CORE + um satélite PW6 admitido | conforme slots concedidos | 2 no pool global | ≤9 |

Limites globais: quatro worksets de implementação, dois aprovados esperando composição, um candidato amplo e uma lane engine/instalação. Nove inclui ROOT, coordenadores, autores, reparadores e revisores em TODOS os chats; limites por chat não provam enforcement global. Cada spawn exige ticket reservado por ROOT; reparador substitui autor liberado. Quando dois aprovados aguardarem, interromper novos despachos e drenar integração. ROOT pode reduzir capacidade por recursos locais sem reduzir gates.

### Plano único, concessões e worktrees

- Somente ROOT escreve neste PLANS.md, RUNBOOK.md/CHECKPOINT.md/SESSION.log centrais, registro global de ownership, SQLite próprio central e fila de entrega. Satélites escrevem evidência/handoff de seu pacote no namespace exclusivo concedido, sem novos PROGRESS/STATUS/TODO/tracker.
- ROOT inventaria checkouts existentes e provisiona/reutiliza managed worktree apenas quando um lote tem gate de entrada, baseline composto recuperável e motivo de isolamento. Nenhum checkout novo é criado só para representar um prompt; não iniciar chats nem implementação automaticamente por estes documentos.
- Uma worktree e branch exclusiva por satélite ativo. Nome solicitado ao provisionador: provider-orch-core, provider-orch-channel, provider-orch-network, provider-orch-recurso ou provider-orch-performance. O caminho REAL retornado/registrado, não um caminho presumido, entra na concessão. Nenhum satélite usa ROOT sujo, PRIMARY misto ou runtime/tooling de outro papel como checkout de autoria.
- Concessão ROOT append-only obrigatória: grant_id, dispatch_epoch, hash do trecho de despacho, snapshot_sha256 do PLANS lido; papel/pacote/destinatário; realpath/worktree/common_git_dir/branch; baseline SHA/tree/frontier; entradas SHA/digests e GVRs; paths EXATOS/autor por arquivo; efeitos/checks permitidos; namespace absoluto de evidência; tickets/worksets; expiryUTC/revogação; saída/nextgate/condição de liberação. Nenhuma concessão está emitida implicitamente por este plano.
- O hash deste bloco imutável é registrado fora dele. Hash integral PLANS é proveniência; notas append-only de status não invalidam contrato. Mudança semântica/inputs/ownership/gates revoga concessões afetadas e gera epoch/contrato sucessor; não editar retroativamente o bloco ou a concessão. Hash operacional não é assinatura nem autoridade de negócio.
- Antes de escrever/commitar, confirmar realpath/cwd/repo/branch/frontier e concessão vigente; rehash entradas. Commits próprios legítimos avançam frontier e são relatados; baseline não muda. Input unreadable/missing/hash divergente/discovery incompleto = INPUT_UNVERIFIED; parar apenas efeito dependente, não inventar cópia/actor/API.
- Expiração/revogação não renova por silêncio/heartbeat: cessar novas mutações, levar operação já iniciada ao ponto seguro e preservar WIP/outputs/checkpoint. ROOT revalida e emite concessão nova. Não apagar WIP nem chamar o controle operacional de sandbox automático.

### Ownership, Git, recursos e comunicação

- Satélite coordena autores delimitados na própria worktree; SOMENTE ele faz commits locais de seus paths/branch. Autores filhos não disputam índice/HEAD. Sem push/PR/merge/rebase/cherry-pick/admin worktree/config Git comum/GC/prune/refs alheias. ROOT integra, atualiza base, publica e mergeia.
- Paths candidatos de papéis NÃO são allowlist ativa: ROOT enumera arquivos por concessão, depois de discovery atual. Cada arquivo/contrato/ID BPMN/definição tem um dono. Sem permissão de pasta inteira por conveniência. Mudança fora do scope gera pedido de ownership, não correção silenciosa.
- CORE recebe shared models/projeções/capabilities/worker consumers apenas por concessão serial explícita. Registry/topic/autonomy/notification_bridge/runtime service/Makefile/CI/runner/pom/loaders/shared helpers e contratos/DMNs/BPMNs comuns ficam ROOT ou autor nominal transferido; satélites de domínio entregam interface/testes para composição.
- Docker/PG/CIB/ports/network/migrations/ownerAPI/deploy e testes amplos estão proibidos aos satélites por padrão. Worktrees não isolam esses recursos. Requisições entram na lane ROOT; exceção precisa concessão exclusiva com owner/process/token/endpoints/cleanup, sem segundo candidato amplo.
- Venv/Node/Maven/target/dist não importam bytes de outro checkout sem qualificação explícita; registrar origem real dos módulos/classes/resources. Sem overlays/PYTHONPATH que burlem fence, engine mocks, fixtures de autoridade/receipts fabricados, skips/xfails para verde.
- Relatórios ao ROOT: grant/frontier/delta, estado, comandos/exits, hash de freeze, dependências, findings, nextgate e recursos retidos. Não escrever coordenação central ou GVR alheio. Mensagem a outro chat somente com autorização humana direta conforme ferramenta; sem ela, handoff em evidência própria + resposta no chat atual para consumo ROOT. Não enviar comunicação externa.

### Gates, delegação e entrega

Autoria/reparo/revisão/integração são papéis distintos para o mesmo pacote. Pool global de dois reviewers, não dois pares; SourceC/desenhos estruturais mantêm dois novos reviewers quando exigido, separados dos autores. Re-gates usam originais elegíveis para reparos de código. GVR sobre mesmo digest define escopo de desenho/código/runtime; ROOT verifica/readback/inscreve SQLite. Satélite nunca aprova sua própria saída como independente.

Decisões técnicas/produto/negócio já delegadas são exercidas dentro do grant, sem confirmação humana repetida; alteração de ADR/plano assinado passa por revisão admitida fora do bundle. Contrato/mandato/credencial/parecer profissional/clock/receipt/ativação inexistentes não são fabricados. Hard clínica/fraude/negativa/financeiro, allowlists, PHI/tenant/purpose, DMN noengine, AMHpin e trilha contrato→BPMN→worker→teste preservados.

Cada lote: gates baratos aplicáveis + lint/type/unit/artifacts, positivo físico cedo, testes reais de races/expiry/revoke/replay/currentness/receipts/crash/rollback e revisão candidato. ROOT executa `make lint type test validate-artifacts` e fences vigentes noSHA composto, CI aplicável atual, ref remoto e revisão. Merge autorizado; CODEOWNER dispensável só como único restante. Sem inferir deploy/efeito externo de CI/merge.

Após merge executar integralmente CONTRIBUTING: inventário/fetchno-prune/custódia/recuperação e prova de contenção/squash, remoção conservadora apenas recursos elegíveis e verificação final. Nunca prune/reset/clean/branch-D/forcedworktree removal. Temporário sólibera após ausência de consumidores/processos/locks/evidência única. Métricas por lote: freeze/review/rework/lane/aprovação→publicação→main e retenções semdisposição; agentes e contagens históricas não medem conclusão.
<!-- PROVIDER-SPRINT-MULTI-ORCHESTRATOR-V1:END -->

Registro ROOT: SHA-256 do bloco v1 44311ad5460cfc39517c68e5b750a70985e62e7b7d6c293f8627ca88ddfde060 (UTF-8 LF, marcadores inclusive, LF final). Nenhum grant satélite emitido. Quarta vaga de workset E2 não amplia nove agentes globais: PW6 adicional exige slots reais liberados/reorganizados por ROOT; sem eles aguardar E3. As notas de status ficam fora do bloco imutável.

<!-- ROOT-SUCCESSOR-GRANTS:BEGIN (worktree .claude/worktrees/provider-implementation, branch provider/e0-native-vertical) -->
## Grants sucessor ROOT — 2026-10-06 (após custódia WORKTREE-CUSTODY-HANDOFF, owner "ROOT sucessor")

Referências: bloco canônico acima (sha 44311ad5460cfc39517c68e5b750a70985e62e7b7d6c293f8627ca88ddfde060 conforme commit a0b92544); 94 grants históricos vivem no snapshot sujo do checkout principal sha256-prefix c64bacbb7a88003c (preservados lá, não duplicados aqui). Ambiência: main a924a563; candidato vertical 0702030a importado por merge --no-ff em provider/e0-native-vertical; blob-identidade dos 46 arquivos provada (overlap 0/46 contra diff de 25 arquivos de commits vendor/promoção); lane livre (engine.lock ausente, socket colima OK); env isolado uv sync OK.

- grant_id: ROOT-SUCCESSOR-20261006-P03 — Pacote: E0-R2 forensics. Destinatário: agente especialista (forense Maven/JAR). Entrada: pom.xml + scripts/ci/build_provider_native_observation.py no head de integração; JARs staged no audit tree (leitura in-place); diagnóstico MR-ENTRY-VS-DECLARED-CLASS-DIAGNOSTIC.json; build conhecido 73d88cba na worktree codex provider-native-manifest-name-repair (somente leitura). Efeito permitido: nenhum (analítico, read-only). Saída: relatório causa + config efetiva divergente + correção proposta. Expira: fim da sessão. Próximo gate: P1.1 reparo com nova compilação.
- grant_id: ROOT-SUCCESSOR-20261006-P04 — Pacote: E0-B disposição documental. Destinatário: terceiro especialista (≠ autor original). Entrada: evidence/pw1-d-b-real-pg-d216, pw1-d-b-failure-diagnosis/{DIAGNOSIS.md,probe-v1}, junit/logs preservados (snapshot worktree). Efeito permitido: nenhum. Saída: comparação de digest de entradas d216 vs diagnóstico-27P + recomendação de disposição (repro única | atribuição ambiental | retenção delimitada) + asserção explícita se B é predecessor obrigatório de PW1/PW2. Expira: fim da sessão. Próximo gate: registro de disposição + eventual repro única em P4.2.
<!-- ROOT-SUCCESSOR-GRANTS:END -->
- grant_id: ROOT-SUCCESSOR-20261006-P22 — Pacote: E0-R1 qualificação de privilégios/FD. Destinatário: especialista (opus, segurança). Entrada: imagem selada 9d0707ed (uid 1000/1001, gid 8801), probe 1ed5d1f5, materiais fd-stability 825cb0b7 + evidências pw1-d-native-*. Efeitos permitidos: containers docker próprios prefixo maezo-r1- (sem engine/compose/engine.lock), writes somente em evidence/root-successor-20261006/p22-r1/ + /private/tmp. Saída: LINUX-100READ-CAUSE.json (classificação errno, conjunto mínimo de privilégios, disposição). Expira: fim da sessão. Próximo gate: SEC GVR de confinamento + primeiro N2 (P3).
- grant_id: ROOT-SUCCESSOR-20261006-P24 — Pacote: autorias do runner N2 (passos 8-10 da receita). Destinatário: package-engineer (sonnet). Entrada: padrão probe 553, harness tests/support/provider_auth_native_{measurement,runtime}.py + provider_tls_pg.py, schema v1, imagem selada bd43149c, GVR minimal-grants (observador uid1001 CapEff 0x80004). Efeitos permitidos: escritas somente em evidence/root-successor-20261006/p3-rebuild/n2/ + testes lane-free; SEM engine.lock/docker/rede. Saída: runner.py + runbook + gaps de harness. Expira: fim da sessão. Próximo gate: revisão ROOT + execução da lane N2 pela ROOT.
- grant_id: OWNER-CODEOWNER-WAIVER-671-20261007 — Dono autoriza merge do PR671 com dispensa CODEOWNER quando os demais checks estiverem resolvidos (CI green em todos os checks aplicáveis no SHA entregue; dispensa vale ainda que CODEOWNER não seja o único restante NESTE PR). GVRs escopados do delta pós-candidato continuam obrigatórios antes do merge; manutenção de 7 etapas após.
- merge记录: PR671 merged ff4d7680 (2026-10-08T11:46Z) sob OWNER-MERGE-CI-GREEN + OWNER-CODEOWNER-WAIVER-671-20261007; GVR par PASS; manutenção 7 etapas em evidence/root-successor-20261006/POSTMERGE-MAINTENANCE-671.json; branch provider/e0-native-vertical preservado (5c659fc4) como base dos follow-ups N2.

<!-- N2-RESUMPTION-GRANTS:BEGIN (worktree .claude/worktrees/provider-implementation, branch provider/n2-startupnaming-envsub, sessão 2026-10-08) -->
## Grants N2+tail — 2026-10-08 (sucessão de SESSION-CLOSE-QUIESCENCE; protocolo = prompt docs/prompts/maezo-provider-n2-resumption-prompt-v1.md)

- grant_id: ROOT-SUCCESSOR-20261008-P25 — Pacote: N2 follow-up PR#1, reparo StartupNaming env-substitution. Destinatário: package-engineer (sonnet). Entrada: repair_spec frozen em evidence/root-successor-20261006/p3-rebuild/n2/N2-NAMING-ROOT-CAUSE.json; fonte src/maezo/portal/engine/java/src/main/java/br/com/maezo/workload/StartupNaming.java (construtor L45-57) + StartupNamingTest.java; semântica ${NAME} derivada da classe EnvironmentPropertySource REAL no ~/.m2 pinado (tomcat digester), incluindo comportamento placeholder-não-resolvido. Efeitos permitidos: edição SOMENTE em src/maezo/portal/engine/java/src/{main,test}/java/br/com/maezo/workload/ (arquivos StartupNaming*); patch UNCOMMITTED; SEM git commit/push/docker/PG/engine.lock; denylist tests/**/test_provider_identity_lock_live_pg.py (never read/import/execute). Saída: patch + raciocínio EngineIT-level + prova local (unit test do resolvedor, JDK17 host mvn -o lane-free). Expira: fim da sessão. Próximo gate: GVR ARCH+SEC cegos no diff (P26/P27), depois cadeia ROOT (commit→compile JDK17→surefire 737+→support gate→rebuild selado→readback→lane N2).
- grant_id: ROOT-SUCCESSOR-20261008-P26 — Pacote: GVR ARCH cego do diff P25. Destinatário: blind-verifier (opus). Entrada: diff do patch + arquivos fonte (sem racional do autor). Efeitos: nenhum (read-only + relatório). Saída: veredito GVR ARCH JSON. Expira: fim da sessão. Próximo gate: composição ROOT do PR.
- grant_id: ROOT-SUCCESSOR-20261008-P27 — Pacote: GVR SEC cego do diff P25. Destinatário: blind-verifier (opus). Entrada: idem P26, questões de segurança (evasão de fence, injeção via env, fechamento closed-world). Saída: veredito GVR SEC JSON. Expira: fim da sessão. Próximo gate: composição ROOT do PR.
- nota: GVR ronda 1 do P25 (2026-10-08): ARCH FAIL (N2-GVR-ARCH-P26.json — F1 alto: StartupEnv omite o auto-append do SystemPropertySource do construtor do Digester, presente em 10.1.47 e 10.1.59; F1b alto: dois testes pinam semântica infiel; F2 baixo: catch estreito vs Throwable; O1 info: loader) + SEC PASS_WITH_SCOPE (N2-GVR-SEC-P27.json — sem laundering, TOCTOU recusa, superfície de reflexão idêntica). RECONCILIAÇÃO F2: catch estreito MANTIDO (SEC: direção fail-closed correta; disposição registrada, sem reparo). Reparo F1+F1b+F2-comentário despachado ao mesmo autor sob P25; re-gate escopado no delta antes da cadeia ROOT. Hashes dos artefatos v1 pinados nos records GVR.
<!-- N2-RESUMPTION-GRANTS:END -->
