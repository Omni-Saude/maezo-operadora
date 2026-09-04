# PLANS.md — Maezo Operadora (Greenfield v2) — STATUS REAL (GROUND-TRUTH, corrigido 2026-07-19)

> **⚠️ ESTE DOCUMENTO FOI CORRIGIDO EM 2026-07-19 PARA REFLETIR A VERDADE VERIFICADA.**
> As marcações originais **"✅ CONCLUÍDO / 15 de 15 / 100%"** eram **auto-certificação NÃO verificada** de agentes anteriores. Quando auditadas (2026-07-16), a plataforma estava em **v0.2.0 alpha-dev** (o LLM levantava `NotImplementedError`, sem entrypoints, com um bypass de aprovação financeira ao vivo). **Aquele "100%" é a lição cautelar, não uma medição.** Os envelopes de milestone (§3) são preservados como *referência do que cada milestone deve entregar* — mas os seus selos "✅ CONCLUÍDO" inline estão **SUPERSEDED**; o status real de cada um está na tabela de reconciliação em **§0**.
>
> **O modelo de verdade não é "15 milestones concluídos".** É o modelo **fase/gate (P0–P4 / G0–G4)** com verificação **zero-trust** (todo "done" carrega uma linha de verificador independente + evidência reproduzível). **A fonte de verdade durável e versionada no repositório é:** `docs/evidence-ledger.md` (**229 linhas no HEAD desta branch em 2026-09-03**, medido via `wc -l docs/evidence-ledger.md` — cresce a cada gap fechado; NUNCA cite um número fixo sem reexecutar o comando primeiro: esta mesma branch já publicou uma contagem stale de "211" que um commit posterior seu tornou errada antes do merge — achado do verificador independente, ver `docs/evidence-ledger.md` linhas AF-06/AF-05/PERSP-B5-SHADOW-LINES/PERSP-C5-FRAUDE-METADATA), `docs/decisions-log.md` (DLs), `docs/adr/` (39 ADRs numerados, 41 arquivos incl. README+template — AF-06) e `docs/gates/` (registros de gate). *(O plano detalhado `V2-COMPLETION-PLAN.md` e o prompt de execução do backlog vivem apenas localmente em `docs/prompts/` por decisão do dono — o ledger é a verdade clonável.)*
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
| M1 ADR Ratification | **◑** | 32 ADRs (não 24); ADR-0030/0031 adicionados 2026-07. Ainda há ADRs "Proposed" (ex: ADR-0031). Contínuo. |
| M2 BPMN/DMN Regeneration | **◑** | BPMN/DMN existem + `validate-artifacts` verde; **MAS contratos ainda DRAFT — o gap<5% e a validação por SME NÃO foram feitos** (G2-bloqueado). |
| M3 Core Runtime | **✅** | G1 fechado; runtime spine verified-complete. |
| M4 Gateway & Security | **◑** | Cadeia de auditoria real e wired **nesta sessão** (T1.10 wave). PEP/pseudonymizer/vault/custody existem com lacunas — ex.: o gate de identidade LGPD estava **fail-OPEN** (corrigindo #113). |
| M5 Agent Framework + MCP + A2A | **◑→✅** | 10 grafos de agente reais (B6 fechado). **A2A dispatcher + card-signing COMPLETO (#156, 2026-07-27).** ToolRegistry implementado desde 2026-08-11 (`gateway/tool_registry.py`, classe `ToolRegistry`, ≈540 linhas em 2026-09-03 — medir com `wc -l` antes de citar, este número já mudou uma vez nesta mesma branch [534→540, achado do verificador] — D-M3-1; residuo ADR-0022/T2.4 fechado). |
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
- **Sprint P3 (→ G3, o maior bloco restante, quase não iniciado):** T3.2 (~44 evals + lane de CI), T3.3 (chaos/cross-process/anti-dupla), T3.4 (auditoria adversarial pré-deploy — por último). Mais A2A dispatcher + card-signing, divergência de checkpoint-schema.

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
- **Tier 2 — agent-buildable, decision-gated (o PRÓXIMO orquestrador DECIDE + EXECUTA — ver §0.5.1):** (1) auditoria de postura dos 4 callers de swallow restantes em `operadora.notifications.internal` (`lgpd.request_additional_proof`/`send_response`, `recurso.notify_sla_risk`, `ans_submit.notify_regulatorio`) — mesma forma de perda-silenciosa que a Fix A fechou para escalation; (2) durabilidade do handoff one-shot NIP→ANS (outbox/reconciliação — hoje rides best-effort sem retry natural); (3) predicado bridge CONTAS→FRAUDE sem `non_blank(tenant_id)` (assimetria pré-existente); (4) correlation-ids do token-metering (`agent_id`/`tenant_id` = None hoje); (5) delegação A2A real de dossiê além dos stubs DL-0033 (adequacao `prepare_remediation_dossier`, cred `prepare_dossier`); (6) CronJobs de retenção/expurgo (`expurgo-working`/`verify-erasure`) — construir o seam completo fail-closed; (7) `PopulationFeatureClient` (André); (8) ratificação de ADRs Proposed (0025/0026/0028/0029/0030; há conflito de status em ADR-0028 ledger-vs-arquivo); (9) cauda de xfails por família (~102 sites/~39 constantes em origin/main — lista viva: `git grep '_.*_REASON =' tests/integration/processes/`). **Gated-por-valor (construir o seam, deixar o valor humano):** cost-table USD do LLM (#29 — os counts do metering já landaram), retention-matrix (item 6), módulo fhir-sync (Tasy-contract-gated).
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
    `owner_role`: *Security/crypto R1 reviewer (interim: dono)* · **`expires: 2026-11-11`** ·
    `checkpoint: 2026-09-12`. Critérios de saída (referenciados pelo `criteria_ref`, não
    duplicados no YAML): censo de não-mapeados = 0 (gerado por máquina) + ≥30 dias consecutivos de
    zero `WOULD_DENY` em refs não mapeadas (ou SLA de triagem de 7 dias) + sampler/ReviewQueue
    mergeados (Q-3) + benchmarks da Q-9 assinados.
  - **Q-10 — classe C2 `leitura_phi_clinica: shadow`** (leituras PHI via FHIR — a classe que a
    pergunta da Médica realmente endereça: o design §10 Q-10 ancora em "a denied FHIR read
    degrades to a dossier gap note (`rafael/graph.py:44-48`)", que é o `SHAPE_LACUNA_DECLARADA`
    dessa classe e de nenhuma outra C2). `owner_role`: *Diretor(a) Médico(a) (interim,
    deadline-enforcement only: dono)* · **`review_by: 2027-02-09`** · `checkpoint: 2026-11-11`.
    Critérios: aprovador Médico nomeado existe + um trimestre de telemetria de sombra C2 + triagem
    clínica de todo `WOULD_DENY` + limiar de carga que a Médica ratifique + interação com
    consentimento resolvida (nenhum flip de C2 com `consentimento_exigido` enquanto o adapter de
    consentimento estiver desfiado — Q-5).
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
