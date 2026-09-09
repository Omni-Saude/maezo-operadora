# Phase 3 Report — Definition of Done (D1–D10)

> Relatório de fechamento da Phase 3 ("Onde a operadora acusa, suspende, descredencia ou paga,
> quem decide é o humano"). Documenta, critério a critério, o estado do **Definition of Done**
> definido em [`phase3-plan.md`](phase3-plan.md) §1, com **evidência citável** (PR #, caminho de
> artefato, gate de CI). Irmão de [`phase2-report.md`](phase2-report.md).
>
> **Status das citações regulatórias:** TODO conteúdo regulatório (RN 593, 567, 259, 566; prazos;
> escadas de alçada; nomes de candidate-group; mapeamento persona→domínio) permanece **DRAFT/verify**
> — exige sign-off de regulatório/jurídico/finanças/PO antes de FINAL. Ver `phase3-plan.md` §7–§8.
> As contract-sheets carregam os marcadores DRAFT/RN explicitamente (FRAUDE 26, CRED 33,
> INADIMPLENCIA 33, PAGTO 24, ADEQUACAO 31, PROGRAMA 13 marcadores).
>
> **Data do relatório:** 2026-06-14. **Branch de consolidação:** `wave/p3-we`.

> **ADENDO DATADO 2026-09-06 (gap `ADR-PHANTOM-PATH-RESIDUE-NON-ADR`):** a citação de
> `src/maezo/policies/` na seção D7 (linha "é owned por `@rodaquino-OMNI` (CODEOWNERS)... este é
> exatamente o estado correto") descreve um diretório que NUNCA existiu no repo
> (`.github/CODEOWNERS` documenta isso desde 2026-08-13; confirmado hoje por
> `git ls-files src/maezo/policies` -> vazio). A matriz real sempre viveu em
> `spec/policies/autonomy/`; o allowlist de processos real é
> `src/maezo/tools/process_allowlist.py::KNOWN_PROCESS_KEYS` (sem overlay YAML — nunca existiu). O
> relatório é HISTÓRICO (2026-06-14) e não é reescrito retroativamente; este adendo só registra os
> caminhos reais para quem consultar o documento hoje.

---

## 0. Mapa de PRs (evidência primária)

| PR | Conteúdo | Estado | Merge |
|---|---|---|---|
| **#51** | W0+W-A — `custody.py` + **SP-OP-FRAUDE-001** quádrupla (pathfinder) | **MERGED** | 2026-06-14T15:30:44Z |
| **#58** | W-D — 5 agentes (Carolina/Fernando/Valentina/Beatriz/André), **non-A2A** | **MERGED** | 2026-06-14T15:30:19Z |
| **#56** | Plataforma analytics — `PopulationFeatureClient` + `ConsentGate` (ADR-0019) + proactive-trigger | **MERGED** | 2026-06-14T15:31:25Z |
| **#53** | W-B — **CRED-001 + PAGTO-001 + PROGRAMA-001** quádruplas | **OPEN** (MERGEABLE) | — |
| **#54** | **SP-OP-INADIMPLENCIA-001** quádrupla (RN 593; no double-rescission) | **OPEN** (CONFLICTING) | — |
| **#57** | W-C — **SP-OP-ADEQUACAO-001** quádrupla (RN 259), empilhado sobre #53 | **OPEN** (MERGEABLE) | — |
| (`docs/processes/ALLOWLIST-PR-READY.md`) | D7 — 6 process-keys → `KNOWN_PROCESS_KEYS` | **PREPARADO, NÃO COMMITADO** (PR humano CODEOWNERS pendente) | — |

Os 6 processos + 5 agentes + plataforma estão **todos fisicamente presentes** na branch de
consolidação `wave/p3-we` (que mergeou `origin/wave/p3-wb`, `wave/p3-inad`, `wave/p3-adeq`). O gate
`make validate-artifacts` corre **verde localmente** sobre `processes/ policies/ agents/`:
`[validate] OK — 0 erros, 0 warning(s)`.

### Nota de CI — failure de integração nas PRs mergeadas é **infra de lane (W-R), não Phase-3**

As três PRs mergeadas (#51/#58/#56) mostram o job **"integration tests (real engine)" em FAIL**, mas
**todos os outros gates passam** (`validate-artifacts`, `evals`, `lint/type/unit`, `helm lint`,
`terraform validate`, `content-signoff-gate`). A inspeção do log (run de #51: **226 passed, 8 failed**)
mostra que **as 8 falhas são EXCLUSIVAMENTE da lane de runtime/plataforma**, não da lógica Phase-3:

- `tests/integration/platform/test_audit_chain_durable.py` → `UndefinedTableError: relation "audit_chain" does not exist`
- `tests/integration/platform/test_memory_pgvector.py` → `ValueError: unknown type: public.vector`

Estes são o bug de bootstrap de schema da lane W-R (tabela `audit_chain` / extensão pgvector no
schema `public`), **corrigido por #55** (`fix(runtime): red main — pgvector extension in public
schema`, mergeado em main no commit `0290392`; HANDOFF `84c8b65` confirma "lane FIXED (#55)"). As PRs
Phase-3 rodaram CI **antes** de #55 aterrar, por isso carregam a falha herdada. **Nenhum
`test_sp_op_*` nem teste de `custody` está entre as 8 falhas** nas PRs FRAUDE/agentes/plataforma.

**Exceção real (não-lane):** o run de #57 (ADEQUACAO) tem **9 failed** — as 8 de lane **mais**
`tests/integration/processes/test_sp_op_adequacao_001.py::test_l3_gap_moderado_encaminha_credenciamento_sem_user_task`
(`AssertionError` em `has_event(_ADEQ_GAP_DETECTED, gap_adequacao="GAP_MODERADO")`). Esta é uma falha
**genuína de processo** e está rastreada em D1/D2 abaixo (ADEQUACAO = MET-pending-fix).

---

## 1. DoD — D1 a D10 + AWS-blocked

### D1 — 6 quádruplas SP-OP completas passam `make validate-artifacts`

**Status: MET (3/6 em main) + MET-pending-merge (CRED/PAGTO/PROGRAMA #53, INADIMPLENCIA #54) + MET-pending-fix (ADEQUACAO #57).**

As 6 quádruplas (`.bpmn` + contract-sheet + DMNs + worker + test-spec) estão presentes e o gate
`validate-artifacts` passa em CI em **todas** as PRs (incluindo as abertas — é o controle
independente da lane de integração):

| Processo | BPMN | DMNs | Worker | Contract | Test | PR | `validate-artifacts` CI |
|---|---|---|---|---|---|---|---|
| FRAUDE-001 | `SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn` | `fraude_routing/indicadores/sla` + `fraude_scoring/*` (7) | `tools/workers/fraude.py` | `contracts/SP-OP-FRAUDE-001.md` | `test_sp_op_fraude_001.py` (28) | #51 | **pass** |
| CRED-001 | `SP-OP-CRED-001_Descredenciamento.bpmn` | `cred_route/prior_notice/admissibility/sla` | `workers/cred.py` | `SP-OP-CRED-001.md` | `test_sp_op_cred_001.py` (26) | #53 | **pass** |
| INADIMPLENCIA-001 | `SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn` | `inadimplencia_status/purga/sla` | `workers/inadimplencia.py` | `SP-OP-INADIMPLENCIA-001.md` | `test_sp_op_inadimplencia_001.py` (22) | #54 | **pass** |
| PAGTO-001 | `SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn` | `pagto_alcada/sla` | `workers/pagto.py` | `SP-OP-PAGTO-001.md` | `test_sp_op_pagto_001.py` (10) | #53 | **pass** |
| ADEQUACAO-001 | `SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn` | `adequacao_sla/gap` | `workers/adequacao.py` | `SP-OP-ADEQUACAO-001.md` | `test_sp_op_adequacao_001.py` (14) | #57 | **pass** |
| PROGRAMA-001 | `SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn` | `programa_routing/sla` | `workers/programa.py` | `SP-OP-PROGRAMA-001.md` | `test_sp_op_programa_001.py` (21) | #53 | **pass** |

O gate cobre o regex de nome, `camunda:candidateGroups` em todo userTask, topics registrados,
ordenação XSD, `historyTimeToLive` namespaced, typeRef allowlist, hitPolicy FIRST, IDs XML únicos.

**Caveat ADEQUACAO (#57):** `validate-artifacts` passa, mas **um teste de integração de processo
falha** (`test_l3_gap_moderado_encaminha_credenciamento_sem_user_task`) — o ramo L3 de roteamento
`GAP_MODERADO → encaminhada_credenciamento` não emite o evento esperado. Precisa correção antes de
merge. Os demais 5 processos não têm falhas de processo (só herdam a lane infra).

**Caveat #54 (INADIMPLENCIA):** PR `CONFLICTING` (`mergeStateStatus: DIRTY`) — exige rebase/resolução
de conflito antes de merge (relacionado à harmonização INADIMPLENCIA↔CANCEL de W-E; ver D2/risco
dupla-rescisão).

---

### D2 — Invariante no-denial/no-adverse verde p/ os 6 processos

**Status: MET (FRAUDE em main; CRED/PAGTO/PROGRAMA/INADIMPLENCIA em PR verde no lane-fixed) + MET-pending-fix (ADEQUACAO).**

O padrão estrutural de 5 partes (ADR-0018, `docs/adr/0018-no-denial-structural-replication.md`) é
aplicado nos 6 processos. Os testes de integração varrem o histórico do engine real e as combinações
de input das DMNs. Evidência por processo (nomes de teste reais):

- **FRAUDE (D2-hard):** `test_nenhum_caminho_automatizado_acusa_fraude`,
  `test_score_alto_roteia_para_humano_nunca_acusa`,
  `test_dmn_fraude_routing_sem_saida_de_acusacao`,
  `test_dmn_fraude_indicadores_sem_saida_de_acusacao`,
  `test_dmn_scoring_portadas_sem_veredito` — `End_FraudeConfirmada*` nunca aparece sem
  `UT_DecisaoInvestigador` no histórico; score é FATO de roteamento, nunca veredito.
- **CRED:** sweep de DMN sem coluna adversa (duas direções — descredenciar **e** negar credenciamento).
- **PAGTO:** `test_sp_op_pagto_001.py` — varredura de `pagto_alcada` (valor × `dentro_teto_l2` ×
  tipo); o terminal adverso só via UT humana **com tier-match**; catch-all conservador → tier mais
  alto.
- **PROGRAMA:** `test_chokepoint_consentimento_nenhum_phi_sem_consentimento` — `ERR_PROGRAMA_NO_CONSENT`
  gateia todo PHI; revogação interrompe.
- **INADIMPLENCIA:** sweep no-adverse; harmonização RN 593 com CANCEL-001.
- **ADEQUACAO:** sweep presente; **mas** o teste L3 `GAP_MODERADO` falha (ver D1) — MET-pending-fix.

Na execução de CI mergeada (lane-fixed em main pós-#55), os `test_sp_op_*` Phase-3 estão entre os
**226 passed** do run de #51; nenhum está entre as 8 falhas de lane.

---

### D3 — Worker-guards `ERR_*_NOT_HUMAN` ativos em todo efeito adverso

**Status: MET (todos os 6, código + testes unitários presentes).**

Cada worker de efeito adverso recusa execução sem a variável de decisão setada por humano e carrega
`*_id` do aprovador + tier na cadeia de auditoria (ADR-0007). Códigos de erro confirmados em código:

| Worker | Error code | Arquivo |
|---|---|---|
| `register_fraud_accusation` | `ERR_FRAUD_ACCUSATION_NOT_HUMAN` | `tools/workers/fraude.py:119` |
| `register_descredenciamento` / `register_cred_denial` | `ERR_DECRED_NOT_HUMAN` / `ERR_CRED_DENIAL_NOT_HUMAN` | `workers/cred.py:119` |
| `register_contract_suspension` / `_rescission` | `ERR_CONTRACT_SUSPENSION_NOT_HUMAN` | `workers/inadimplencia.py:118` |
| `release_high_value_payment` | `ERR_PAYMENT_RELEASE_NOT_HUMAN` (+ tier-match) | `workers/pagto.py:102` |
| `register_fallback_commitment` | `ERR_FALLBACK_COMMITMENT_NOT_HUMAN` | `workers/adequacao.py:108` |
| `register_program_discharge` (+ consent) | `ERR_PROGRAM_DISCHARGE_NOT_HUMAN` / `ERR_PROGRAMA_NO_CONSENT` | `workers/programa.py:109-110` |

Testes unitários dedicados existem para os 6: `tests/unit/workers/test_{fraude,cred,inadimplencia,pagto,adequacao,programa}_guards.py`.
Exemplos: `test_release_high_value_recusa_sem_humano` + `test_release_high_value_sucesso_carrega_aprovador_id_e_tier` (PAGTO),
`test_register_fallback_commitment_recusa_sem_humano` (ADEQUACAO),
`test_register_discharge_recusa_sem_humano` + `test_check_consent_recusa_sem_consentimento` (PROGRAMA),
`test_worker_register_fraud_accusation_recusa_sem_humano` (FRAUDE).

---

### D4 — Selagem de custódia antes da decisão (FRAUDE)

**Status: MET (em main via #51).**

`src/maezo/gateway/custody.py` implementa `CustodyBundle`, `compute_bundle_root` (Merkle
determinístico sobre `record_hashes` ordenados lexicograficamente, ADR-0020 §2) e
`seal_custody_bundle` (recomputa+verifica, grava `AuditRecord` na hash-chain ADR-0007 **antes** da
UT do investigador). É **projeção** sobre `AuditRecord`/`verify_chain`, não fork.

Teste de integridade — cobertura excede as 3 partes exigidas (`tests/unit/gateway/test_custody.py`):
- **tamper do root/record_hash:** `test_verify_false_when_record_hash_tampered`
- **reorder do conteúdo:** `test_verify_false_when_evidence_reordered_content`
- **no-PHI na custódia:** `test_assert_no_phi_rejects_raw_cpf_with_error_code`,
  `test_assert_no_phi_rejects_raw_name_nested`, `test_looks_like_phi_detects_raw_identifiers_and_ignores_refs`
- **seal-precede-decisão:** `test_seal_record_precedes_subsequent_decision_record`
- **legal-hold:** `test_legal_hold_collision_is_recorded_not_resolved`

Integração no processo (`test_sp_op_fraude_001.py`): `test_custodia_selada_antes_da_decisao`,
`test_acusar_fraude_round_trip_custodia_do_seal_worker` — o gate `GW_CustodiaSelada` só cria a UT
após o `bundle_root` estar ancorado na chain. ADR-0020 (`docs/adr/0020-custody-chain.md`) presente.

---

### D5 — 5 agentes com agent.yaml válido + grafo + golden eval

**Status: MET (em main via #58).**

Os 5 agentes existem com `agent.yaml` aceito por `validate-artifacts` (CI `validate-artifacts` =
pass em #58):

| Agente | Dir | `agent.yaml` | Golden eval | Dataset |
|---|---|---|---|---|
| Carolina | `agents/carolina/` | ✓ | `tests/evals/carolina/test_carolina_golden.py` | `tests/evals/golden/carolina/cases.jsonl` |
| Fernando | `agents/fernando/` | ✓ | `tests/evals/fernando/test_fernando_golden.py` | `.../fernando/cases.jsonl` |
| Valentina | `agents/valentina/` | ✓ | `tests/evals/valentina/test_valentina_golden.py` | `.../valentina/cases.jsonl` |
| Beatriz | `agents/beatriz/` | ✓ | `tests/evals/beatriz/test_beatriz_golden.py` | `.../beatriz/cases.jsonl` |
| André | `agents/andre/` | ✓ | `tests/evals/andre/test_andre_golden.py` | `.../andre/cases.jsonl` |

CI `evals (agent-touching paths)` = **pass** em #58. Allowlists TIGHT (servidores port-pending fora
até um nó do grafo exercê-los — precedente Gustavo). KPIs de falso-resultado==0 verificados via
golden evals.

---

### D6 — Delegações A2A novas registradas + anti-loop

**Status: DEFERRED-to-W-R2/R9 (metadata declarativa presente; handler executável NÃO autorado).**

Conforme planejado, PR #58 é explicitamente **"non-A2A"**. O **Agent Card** (metadata declarativa,
ADR-0003) está autorado em cada `agent.yaml` — `accepted_task_types` declara o contrato de delegação
(ex.: André `analytics.population`/`analytics.actuarial`; Carolina `credentialing.analyze`; Beatriz
`fraud.investigate` human-gated; Valentina `care.stratify`/`care.enroll`). **Mas o handler inbound
A2A executável (`delegation.py`) NÃO existe em nenhum agente** — `andre/agent.yaml:65-66` documenta:
*"A montagem do inbound handler A2A (delegation.py) é GATED em W-R2/R9 (NÃO autorada aqui — só a
metadata declarativa do contrato)."* As novas costuras (Lucas→Fernando, Marina/CONTAS→Beatriz,
Helena/Rafael→Valentina, CDC-proativo→{Beatriz,Valentina,André}) e os testes de `max-hops`/anti-loop
(ADR-0015) ficam para W-R2/R9. Sem `delegation.py` nem `accepted_task_types` runtime-wired hoje.

---

### D7 — `KNOWN_PROCESS_KEYS` ampliado com os 6 keys via PR humano (ADR-0016)

**Status: DEFERRED-to-human-CODEOWNERS-PR (mudança preparada, NÃO commitada).**

Verificação definitiva: **nem `origin/main` (84c8b65) nem o HEAD commitado de `wave/p3-we`
(bc86d8e) contêm os 6 keys Phase-3** em `src/maezo/tools/process_allowlist.py`
(`git show <sha>:... | grep -c SP-OP-FRAUDE-001` = 0 em ambos). Os 6 keys existem **apenas como
modificação de working-tree não-commitada** (`git status` → ` M process_allowlist.py` + ` M
policies/process_allowlist.yaml`), acompanhados de `docs/processes/ALLOWLIST-PR-READY.md` (artefato
untracked) que instrui:

> **STOP.** Esta mudança torna seis processos de governança Phase-3 **start-enabled** no engine. Sob
> **ADR-0016** (allowlist fail-closed) adicionar um key exige **PR separado, revisado por humano com
> aprovação CODEOWNERS**. NÃO deve entrar junto/auto-mergeado com Wave E.

`src/maezo/policies/` é owned por `@rodrigotaquino` (CODEOWNERS). Este é exatamente o estado correto
de D7: **deliberadamente fora dos artefatos mergeados**, pronto para cherry-pick num PR humano-gated
fail-closed. O tenant opt-in via `process_allowlist.yaml` está sincronizado no mesmo diff preparado.

---

### D8 — Lago populacional consome, não duplica (ADR-0013 precedente)

**Status: MET contra fixtures/simulador (em main via #56); apply real = AWS-blocked (#16).**

`src/maezo/platform/analytics/population_feature_client.py` = `PopulationFeatureClient` **read-only**
sobre um `FeatureStoreBackend` injetado (simulador local em dev/CI — `_simulator.py`). Propriedades
estruturais codificadas: sem método de escrita (read-only estrutural, ADR-0019 item 2); k-anonimato
/ small-cell (`cohort_size < min_k` suprimido, `suppression.py`); `as_of`/lineage snapshot pin; hook
de erasure LGPD `drop_surrogate` (ponte `mpi_id↔fhir_patient_id`, `compliance.py`). ADR-0019
(`docs/adr/0019-amh-lake-of-record.md`) presente. `ConsentGate(scope=operational_analytics)`
fail-closed em `consent_gate.py`.

Testes unitários: `tests/unit/platform/analytics/test_{suppression,consent_gate,population_feature_client,compliance}.py`.
CI `lint/type/unit` + `validate-artifacts` = pass em #56.

**AWS-blocked:** acesso real ao amh Gold/Feature-Store via S3 PHI-zone Object-Lock e grants
IAM/Lake-Formation tenant-scoped (#16) — o invariante CI "zero tabelas de fato clínico paciente-nível"
é testado contra o simulador/tipos; enforcement fim-a-fim contra o lago real fica no deploy.

---

### D9 — Canal proativo PHI-minimizado

**Status: MET (design+teste local; em main via #56).**

`src/maezo/platform/analytics/proactive_trigger.py` — `ProactiveTrigger` imutável sobre
`agents.events.proactive` (`TOPIC_PROACTIVE`). Schema start-signal-only:
`{trigger_type, pseudonym|fhir_patient_id (ponteiro), cohort|risk_band, source_ref, consent_checked,
ts}`, **ZERO conteúdo clínico cru** — `ProactivePhiLeakError` se `source_ref`/campos "parecem"
identificador bruto BR (CPF/CNS/CNPJ); `ProactiveConsentError` se um trigger que leva a contato com
beneficiário não tem `consent_checked == True` (gate LGPD, ADR-0019 §3). Consumidor re-busca PHI
in-zone; efeito adverso/contratual só via SP-OP gated.

Teste: `tests/unit/platform/analytics/test_proactive_trigger.py`;
`test_canal_proativo_sem_consent_checked_barra` (PROGRAMA integração). CI verde em #56.

**Nota:** `agents.events.proactive` é tópico **produtor-side** (não start de engine); não registrado
em `config/topic_registry.yaml` (que governa topics de engine BPMN) — consistente com o design.

---

### D10 — Zonas PHI pinadas (ADR-0006)

**Status: MET (template+render; em main via #58/#56); enforcement no cluster = AWS-blocked (#16).**

`security_zone` por agente confirmado nos `agent.yaml` (corresponde exatamente ao plano):

| Agente | `security_zone` |
|---|---|
| Carolina | `phi` |
| Valentina | `phi` |
| Beatriz | `phi` |
| André | `phi` (chokepoint de egress) |
| Fernando | `general` |

`deploy/helm/maezo-tenant/templates/networkpolicy.yaml` — NetworkPolicy fail-closed (ADR-0017):
(1) `default-deny-egress` baseline; (3) agent→gateway scoped (sem `podSelector: {}` blanket —
impede PHI-pod alcançar GENERAL-zone pod); (4) per-agent egress a CIDRs concretos (**nunca
0.0.0.0/0**). Deployment templates zone-labeled em `templates/deployment-agent-runtime.yaml`. CI
`helm lint` + `terraform validate` = pass.

**AWS-blocked:** validação fim-a-fim das NetworkPolicies no cluster + endpoint LLM PHI-zone
BR-residency real (#16).

---

### AWS-blocked — staging apply, lago real/S3 Object-Lock, LLM PHI-zone BR, grants IAM/Lake-Formation

**Status: AWS-blocked (rastreado em issue #16) — não bloqueia D1–D7, D9, D10.**

Buildado/testado agora contra fixtures/simulador/render; enforcement real adiado para deploy:
staging apply; S3 PHI-zone Object-Lock da amh + grants IAM/Lake-Formation tenant-scoped; LLM
PHI-zone BR-residency real; conectividade ANS/banco; validação das NetworkPolicies no cluster.

---

## 2. Sumário do DoD

| # | Critério | Status | Evidência principal |
|---|---|---|---|
| D1 | 6 quádruplas passam `validate-artifacts` | **MET** (3 em main) / **pending-merge** (#53,#54) / **pending-fix** (#57) | gate `validate-artifacts` pass em todas as PRs; 1 teste de processo ADEQUACAO falha |
| D2 | Invariante no-adverse | **MET** (5) / **pending-fix** (ADEQUACAO) | `test_sp_op_*` + sweep DMN; ADR-0018 |
| D3 | Worker-guards `ERR_*_NOT_HUMAN` | **MET** (6) | `workers/*.py` + `tests/unit/workers/test_*_guards.py` |
| D4 | Selagem de custódia antes da decisão | **MET** | `gateway/custody.py` + `tests/unit/gateway/test_custody.py` (#51) |
| D5 | 5 agentes válidos + grafo + golden eval | **MET** | `agents/{...}/agent.yaml` + `tests/evals/*` (#58) |
| D6 | Delegações A2A + anti-loop | **DEFERRED → W-R2/R9** | metadata em `agent.yaml`; sem `delegation.py` (#58 non-A2A) |
| D7 | `KNOWN_PROCESS_KEYS` +6 | **DEFERRED → PR humano CODEOWNERS** | não-commitado; `ALLOWLIST-PR-READY.md` (ADR-0016) |
| D8 | Lago consome, não duplica | **MET contra fixtures** / apply real **AWS-blocked** | `platform/analytics/*` (#56); ADR-0019 |
| D9 | Canal proativo PHI-minimizado | **MET** (local) | `proactive_trigger.py` + testes (#56) |
| D10 | Zonas PHI pinadas | **MET** (template/render) / cluster **AWS-blocked** | `agent.yaml security_zone` + `networkpolicy.yaml` |
| — | AWS real | **AWS-blocked (#16)** | não bloqueia D1–D7, D9, D10 |

**Itens abertos para FINAL:**
1. **Mergear #53/#54/#57** após (a) lane-fix #55 propagar (já em main) e (b) corrigir a falha de
   processo ADEQUACAO `test_l3_gap_moderado_encaminha_credenciamento_sem_user_task` e (c) resolver o
   conflito de #54 (INADIMPLENCIA — harmonização com CANCEL-001 / dupla-rescisão RN 593).
2. **PR humano CODEOWNERS** para os 6 process-keys (D7, ADR-0016 fail-closed).
3. **W-R2/R9** para os handlers A2A `delegation.py` (D6).
4. **Sign-off regulatório/jurídico/finanças/PO** de TODO conteúdo DRAFT (RN 593/567/259/566,
   alçadas, candidate-groups, persona→domínio) antes de FINAL.
5. **AWS (#16)** para enforcement real de lago/PHI-zones/grants.
