# Phase 3 Plan — "Onde a operadora acusa, suspende, descredencia ou paga, quem decide é o humano" (Rede + Fraude + Inadimplência + Pagamentos + Cuidado + Lago Populacional)

> Síntese executável das três pesquisas (R1-processes, R2-agents, R3-fraud-datalake do swarm `wr9q6qes1`),
> aterrada em fatos verificados do repo (não assumidos) e nos padrões provados em Phase 0–2.
> Irmão de [`phase2-plan.md`](phase2-plan.md); mesma estrutura de 10 seções.
>
> **Status das citações regulatórias:** TODAS as RN (593, 567, 259, 566), prazos, escadas de alçada e
> nomes de candidate-group são **DRAFT/verify** — exigem sign-off de regulatório/jurídico/finanças/PO
> antes de FINAL. Ver §8.

---

## 1. Objetivo e Definition of Done

**Objetivo:** materializar os seis processos SP-OP restantes do catálogo — investigação de fraude (cadeia de custódia), (des)credenciamento de rede, suspensão/rescisão por inadimplência, pagamentos de alçada, adequação geográfica de rede e programas de cuidado — mais cinco agentes (Carolina, Fernando, Valentina, Beatriz, André) e a camada de lago de dados populacional. **A invariante estrutural de Phase 1–2 é não-negociável e agora cobre TODO efeito adverso, não só negativa de cobertura:** nenhum caminho automatizado acusa fraude, descredencia prestador, suspende/rescinde contrato, nega reembolso/programa ou libera pagamento acima de alçada — todo efeito adverso nasce numa User Task humana. FRAUDE-001 introduz o padrão de **selagem de evidência antes da decisão humana** (cadeia de custódia tamper-evident).

**DoD (espelha phase2-plan; evidência = CI, exceto o que for AWS-blocked):**

| # | Critério | Evidência exigida |
|---|---|---|
| D1 | 6 quádruplas SP-OP completas (bpmn+contrato+dmn+test-spec) passam `make validate-artifacts` | gate verde: regex de nome, `camunda:candidateGroups` em todo userTask, topics em `topic_registry.yaml`, ordenação XSD (documentation<extensionElements<incoming<outgoing<eventDefinition), `camunda:historyTimeToLive` namespaced em process e em cada `<decision>`, typeRef allowlist (sem `"number"`; dinheiro=`integer` cents ou `double`), hitPolicy FIRST p/ catch-all, IDs XML únicos por arquivo (ENGINE-22004) |
| D2 | **Invariante no-denial/no-adverse verde** p/ FRAUDE, CRED, INADIMPLENCIA, PAGTO, ADEQUACAO, PROGRAMA | teste de integração consulta `history/activity-instance` do engine: todo end-event adverso co-ocorre com User Task humana concluída; varredura de TODAS as combinações de input das DMNs não produz nenhum efeito adverso; **FRAUDE: `End_FraudeConfirmada*` nunca aparece sem `UT_DecisaoInvestigador` no histórico** |
| D3 | Worker-guards `ERR_*_NOT_HUMAN` ativos em todo efeito adverso | testes unitários: o worker do efeito adverso (`register_fraud_accusation`/`register_descredenciamento`/`register_contract_suspension`/`release_high_value_payment`/`register_reembolso_*`/`register_program_discharge`) recusa sem variável de decisão setada por humano; carrega `*_id` do aprovador + tier na cadeia de auditoria (ADR-0007) |
| D4 | **Selagem de custódia antes da decisão (FRAUDE)** | `custody.py`: `bundle_root` (Merkle sobre `record_hashes` ordenados) é gravado na hash-chain (ADR-0007) ANTES de `UT_DecisaoInvestigador`; teste de integridade em 3 partes (tamper+reorder do root; no-PHI-in-custody; accusation-requires-human) |
| D5 | 5 agentes (Carolina, Fernando, Valentina, Beatriz, André) com agent.yaml válido + grafo + golden eval | `validate-artifacts` aceita agent.yaml; KPIs de falso-resultado==0 (ver §4); evals verdes; allowlists TIGHT (sem mcp inexistente/não-exercido) |
| D6 | Delegações A2A novas registradas + anti-loop | `credentialing.analyze`(Carolina), `care.stratify`/`care.enroll`(Valentina), `fraud.investigate`(Beatriz, **human-gated**), `analytics.population`(André); Lucas→Fernando `arrears.followup`; CONTAS/Marina→Beatriz `fraude.investigate`; testes A2A respeitam `accepted_task_types` + max-hops (ADR-0015) |
| D7 | `KNOWN_PROCESS_KEYS` ampliado com os 6 keys via PR humano (ADR-0016, fail-closed) | diff em `src/maezo/tools/process_allowlist.py` revisado; tenant opt-in via `process_allowlist.yaml` |
| D8 | Lago populacional **consome, não duplica** (ADR-0013 precedente) | `PopulationFeatureClient` read-only sobre amh Gold/Feature-Store via `ConsentGate(scope=operational_analytics)`; CI: zero tabelas de fato clínico paciente-nível em Maezo; agregados k-anonimizados carregam lineage `as_of` da amh; nenhum `fhir_patient_id` apagado em linha de analytics |
| D9 | Canal proativo PHI-minimizado | `agents.events.proactive`: trigger é start-signal-only `{trigger_type, pseudonym/fhir_patient_id, cohort/risk_band, source_ref, consent_checked, ts}`, SEM conteúdo clínico cru; consumidor re-busca PHI in-zone; efeito adverso/contratual só via SP-OP gated; `consent_checked==true` exigido antes de contato com beneficiário |
| D10 | Zonas PHI pinadas (ADR-0006) | Carolina/Valentina/Beatriz/André = `security_zone: phi`; Fernando = `general`; per-agent NetworkPolicy + Deployment zone-labeled (Wave-D pattern de phase2-plan §6.1); egress allowlist fail-closed (ADR-0017) |
| **AWS-blocked** | staging apply, acesso real ao lago/S3 Object-Lock, LLM PHI-zone BR, grants IAM/Lake-Formation tenant-scoped | rastreado em issue #16 — **não bloqueia D1–D7, D9 (design+teste local), D10 (template+render)** |

---

## 2. Estrutura de Waves (pathfinder-first, igual a Phase 2)

**Princípio (provado em Phase 2):** provar uma classe NOVA de artefato no engine real com UM processo antes de clonar. FRAUDE-001 é a pathfinder porque carrega o padrão inédito de **cadeia de custódia / selagem de evidência** e é o sink convergente de todos os handoffs `encaminhar_fraude` de Phase 2.

| Wave | Conteúdo | Depende de | Paralelizável? |
|---|---|---|---|
| **W0** | Fundações: `custody.py` (projeção sobre hash-chain ADR-0007), `PopulationFeatureClient` skeleton, schema do canal `agents.events.proactive`, ADR-0019 (lago-of-record) + ADR-0020 (cadeia de custódia) propostos, 6 contract-sheets + test-specs + registro de topics | main pós-#38 | contract-sheets/ADRs em paralelo; `custody.py` é caminho crítico |
| **W-A (pathfinder)** | **SP-OP-FRAUDE-001 quádrupla SOZINHA** (bpmn+contrato+dmn+worker+test) provada no engine real; valida selagem-antes-da-decisão + `register_fraud_accusation` gated | W0 (`custody.py`) | **NÃO** — pathfinder solo, igual CONTAS em Phase 2 |
| **W-B (clones de negativa-estrutural)** | SP-OP-CRED-001 (clona skeleton CANCEL; **duas direções adversas** — credenciar/descredenciar; inverte o auto-approve do `SP-PS-002_Credentialing` de referência), SP-OP-INADIMPLENCIA-001 (clona CANCEL; RN 593 cure-window event-gateway), SP-OP-PAGTO-001 (clona auto-approval `dentro_teto` do AUTH; **candidate-group dirigido por valor** via `pagto_alcada` DMN) | W-A merged | **SIM** — donos de arquivo disjuntos |
| **W-C (clones mais leves)** | SP-OP-ADEQUACAO-001 (monitoramento/remediação; consome fato de mudança de rede do CRED-001 — autorar CRED antes ou stub o fato), SP-OP-PROGRAMA-001 (consent-gated; introduz chokepoint `ERR_PROGRAMA_NO_CONSENT`; depende de LGPD-DSR já em main) | W-B (ADEQUACAO↔CRED) | **SIM** entre si (donos disjuntos) |
| **W-D (agentes)** | 5 agentes Carolina/Fernando/Valentina/Beatriz/André (clone+adapt de Rafael/Marina, NUNCA import — ADR-0011), BPMN-first/build-to-contract-sheet | quádrupla correspondente | **SIM** — um agente por dono |
| **W-E (integração cross-process + plataforma)** | invariantes cross-process (Phase2→FRAUDE handoff; CRED→ADEQUACAO fato de rede), **harmonização INADIMPLENCIA-001 ↔ CANCEL-001 (business-keys; quem detém o terminal de rescisão sob RN 593)**, lago populacional ligado, canal proativo ligado, sweep no-adverse consolidado, observabilidade Phase 3, relatório DoD | todas as anteriores | parcial |

**Caminho crítico:** `W0(custody.py) → FRAUDE-001 → Beatriz → W-E`. A harmonização INADIMPLENCIA↔CANCEL em W-E é o segundo gargalo (risco de dupla-rescisão).

---

## 3. As 6 quádruplas SP-OP

Cada uma entrega `.bpmn` + contract-sheet + DMNs engine-deployable + worker(s) + test-spec de integração JUNTOS, **antes** do agente (build-to-contract-sheet). Todas aplicam o **padrão estrutural no-adverse de 5 partes (ADR-0018)**, independente do nível da matriz de autonomia — só o nível L0/L1/L2 difere, a garantia estrutural é idêntica:

> (1) tipo Route/decisão SEM variante adversa nos ramos automatizados; (2) DMNs sem coluna de saída adversa; (3) todo ambíguo/desconhecido/DMN-indisponível faz fail-safe para humano via allowlist fechada (`frozenset`); (4) worker-guard `ERR_*_NOT_HUMAN` que só executa o efeito adverso se um humano setou a decisão; (5) teste de integração-invariante varrendo o histórico do engine.

| # | Processo | RN/âncora (DRAFT) | Nível matriz | Especificidade | Worker adverso gated |
|---|---|---|---|---|---|
| **1** | **SP-OP-FRAUDE-001** Investigação de fraude (**PATHFINDER**) | Cadeia de custódia; referral ANS/civil/penal | `fraud_accusation` **L0-hard** | Selagem Merkle do dossiê na hash-chain ANTES da UT do investigador; score de fraude é FATO de roteamento, NUNCA veredito; sink dos `encaminhar_fraude` de Phase 2 | `register_fraud_accusation` (guard `decisao_fraude!='ACUSAR_FRAUDE'` → `ERR_FRAUD_ACCUSATION_NOT_HUMAN` + `investigator_id`) |
| **2** | **SP-OP-CRED-001** (Des)credenciamento | RN 567 (descred. prior-notice) / RN 566 (rede) | `provider_decredentialing` **L1** | **Duas** direções adversas (descredenciar **e** negar credenciamento); inverte o gateway bare auto-approve de `SP-PS-002_Credentialing`; substituição/redimensionamento pode ser UT | `register_descredenciamento` / `register_cred_denial` (`ERR_*_NOT_HUMAN`) |
| **3** | **SP-OP-INADIMPLENCIA-001** Suspensão/rescisão | RN 593 (cure/purga + prior-notice) | `contract_termination` **L0-hard** | RN 593 cure-window = inversão de event-gateway (igual CANCEL); **harmonizar com CANCEL-001** (§risco dupla-rescisão) | `register_contract_suspension`/`register_contract_rescission` (`ERR_*_NOT_HUMAN`) |
| **4** | **SP-OP-PAGTO-001** Pagamentos de alçada | Política financeira interna | `high_value_payment` **L1** (`threshold_brl: 100000`) | Único processo com **candidate-group dirigido por valor**: `pagto_alcada` DMN emite `grupo_aprovador` → `camunda:candidateGroups`; catch-all conservador → tier mais alto/ANALISE_HUMANA; guard verifica tier do aprovador ≥ valor | `release_high_value_payment` (guard tier-match + `ERR_PAYMENT_RELEASE_NOT_HUMAN`) |
| **5** | **SP-OP-ADEQUACAO-001** Adequação de rede | RN 259 (tempos/distâncias de acesso) | L3 monitoramento (**mas** decisão de fallback é human-gated) | Menos óbvio como negativa-like; o **compromisso financeiro de fallback** (livre escolha / reembolso garantido) DEVE ser UT humana mesmo num processo majoritariamente L3 | `register_fallback_commitment` (`ERR_*_NOT_HUMAN`) |
| **6** | **SP-OP-PROGRAMA-001** Programas de cuidado | Consentimento LGPD (+ RN se houver) | L3 | Chokepoint de consentimento: `ERR_PROGRAMA_NO_CONSENT` gateia TODO processamento de PHI do programa; revogação **interrompe** (boundary event) e para o processamento — não modelar como happy-path de enrollment | `register_program_discharge` (clínico → `ERR_*_NOT_HUMAN`) |

**FRAUDE-001 — detalhe de cadeia de custódia (a classe nova):**
- `src/maezo/gateway/custody.py` = `CustodyBundle` (projeção tipada sobre `AuditRecord`/`verify_chain` existentes) + `bundle_root` Merkle sobre `record_hashes` ordenados. **Não forkar a hash-chain — projetar sobre ela.**
- Evidência crua fica como `input_hash` apenas (ADR-0006/0016); artefatos crus em S3 PHI-zone da amh com **Object Lock** (consumir a imutabilidade da amh, não reconstruir).
- Selar o `bundle_root` de volta na chain ANTES da `UT_DecisaoInvestigador` → decisão sobre corpus selado verificável (ANS/judicialmente defensável).
- Portar o scoring de referência (`detect_fraud_worker_v2` + 7 DMNs de fraude) **só como inputs de montagem de dossiê** ("indicadores presentes"), nunca como veredito (`FRAUD_DETECTED` é anti-padrão a inverter); preferir consumir `feature_store.claim_features/provider_features` da amh.
- Preservar a costura de Phase 2: `indicio_fraude_sinalizado` permanece **informativo**; um humano seta `encaminhar_fraude`; start neutro `operadora.fraude.intake` + topic `agents.events.fraude.intake_received` correlacionado `FRAUDE-{tenant}-{caso}`. FRAUDE nunca lê de volta / bloqueia / pré-empta a decisão de glosa.

---

## 4. Agentes a autorar (5)

> ⚠️ **R-PERSONA-MAP (DRAFT — exige confirmação PO/produto):** o mapeamento persona→domínio NÃO está explícito em nenhum repo; só a zona (ADR-0006) e a lista de domínios (prompt do swarm) estão pinadas. O mapeamento abaixo é **inferido** (ADR-0003 "CDC→Beatriz"; Fernando é o único `general` dos 5 → tier beneficiário/cobrança; André = lago é o único pin explícito). **Beatriz↔Valentina (fraude↔cuidado) é a chamada mais frágil.** Confirmar antes de autorar.

Cada agente: `contracts.py`/`RegistryToolInvoker`/grafo/`delegation.py` reautorados (estruturalmente idênticos a Gustavo, independentes por ADR-0004 > DRY). Allowlists TIGHT/action-real (W4): nenhum mcp id no allowlist até um nó do grafo exercê-lo; servidores port-pending (`mcp-datalake` André, registry NPI Carolina) ficam FORA até o port aterrar (precedente Gustavo deferindo `mcp-ans`/`mcp-regdata`).

| Agente | Zona (ADR-0006) | Domínio inferido (DRAFT) | Processo(s) | A2A `accepted_task_types` | KPI invariante (==0) |
|---|---|---|---|---|---|
| **Carolina** | **phi** | Credenciamento/rede | CRED-001 | `credentialing.analyze` | `false_decredentialing_rate==0` |
| **Fernando** | **general** | Inadimplência/cobrança avançada | INADIMPLENCIA-001 (ou AGJ navegador — ver OQ) | `[]` (originator, igual Lucas/Helena) | `false_denial_rate==0` |
| **Valentina** | **phi** | Programas de cuidado | PROGRAMA-001 | `care.stratify`, `care.enroll` | `false_denial_rate==0` (ramo clínico) |
| **Beatriz** | **phi** | Fraude | FRAUDE-001 | `fraud.investigate` (**human-gated**) | `zero_auto_accusation` |
| **André** | **phi** | População/atuarial + lago | PAGTO-001 + lago de dados | `analytics.population`, `analytics.actuarial` | `false_pricing_decision_rate==0`, `phi_egress_violations==0`, `lgpd_erasure_coverage==1.0` |

**Dois casos especiais:**
- **Beatriz** — grafo `instruct_investigation` single-node; cadeia de custódia (ADR-0007 signed audit + teste de invariante de histórico). NÃO deixa worker setar `decisao_fraude`.
- **André** — AGJ/analytics-first (dossiê agregado, ponteiros `dataset_ref`, `mcp-datalake` port-pending) e é o **crux de egress ADR-0006**: gatear egress de PHI cru estruturalmente no chokepoint gateway/registry, NÃO por convenção de prompt.

**Novas delegações a ligar:** Lucas→Fernando (`arrears.followup`), Marina/CONTAS→Beatriz (`fraude.investigate`, human-gated), Helena/Rafael→Valentina (`care.*`), CDC-proativo→{Beatriz,Valentina,André} via consumidor `agents.events.proactive`. AGJ-* (1 página por agente) em `docs/processes/journeys/`.

---

## 5. Plataforma: lago de dados, custódia e canal proativo

| Workstream | Entrega | Regra-mãe |
|---|---|---|
| **WP3.1 Custódia** | `src/maezo/gateway/custody.py` (projeção, não fork) + teste de integridade 3-partes | bundle_root selado na chain antes da decisão (D4) |
| **WP3.2 Lago-of-record** | ADR-0019 (amh = lago analítico de record, precedente ADR-0013); `src/maezo/platform/analytics/PopulationFeatureClient` read-only sobre amh Gold/Feature-Store via `ConsentGate(scope=operational_analytics)` papel consumidor | **Maezo só adiciona agregados pseudonimizados** população/atuarial/care-gap, nunca tabelas de fato clínico paciente-nível |
| **WP3.3 Erasure LGPD** | reconciliar com modelo amh (SAD §10.7): Maezo hard-deleta memória paciente-nível por `fhir_patient_id` (cascata ADR-0002) **e** dropa o surrogate `mpi_id↔fhir_patient_id`; agregados anônimos (sem `fhir_patient_id`, células pequenas suprimidas) sobrevivem | verificação mensal ADR-0002: nenhuma linha de analytics carrega `fhir_patient_id` apagado |
| **WP3.4 Canal proativo** | `agents.events.proactive` typed trigger PHI-minimizado; detectores (CDC + Feature-Store-refresh consumers) emitem `{trigger_type, pseudonym, cohort/risk_band, source_ref, consent_checked, ts}` | trigger = start-signal-only; consumidor re-busca PHI in-zone (precedente Helena/WhatsApp); efeito adverso só via SP-OP gated; auditar cada emissão (ADR-0007) |
| **WP3.5 Residência PHI** | André/Beatriz/Valentina = Zona PHI/Financeira; PHI cru só em endpoint BR-residency/zero-retention atrás de NetworkPolicy egress allowlist (ADR-0017); expor só agregados k-anonimizados à Zona-Geral | k-anonimato/small-cell-suppression = parâmetro de compliance (min cohort) |

**ADRs novos a propor (numeração correta — main está em 0018):**
- **ADR-0019** — amh-data-platform como lago analítico/de-record; Maezo consome agregados, nunca duplica fato clínico.
- **ADR-0020** — Cadeia de custódia / selagem de evidência tamper-evident como projeção da hash-chain de auditoria (ADR-0007).
- (Considerar) promover `provider_decredentialing` e `erase_patient_memory` ao `_hard_frozen.yaml` → mudança de governança = ADR próprio + compliance review.

---

## 6. Mapa de propriedade de arquivos (disjoint) + topologia + paralelização

**Donos disjuntos por wave (sem dois agentes tocando o mesmo arquivo — lição de Phase 2):**

| Owner | Arquivos exclusivos |
|---|---|
| FRAUDE-001 | `processes/bpmn/SP-OP-FRAUDE-001_*.bpmn`, `processes/dmn/fraude_*.dmn`, `tools/workers/fraude.py`, `gateway/custody.py`, `tests/integration/processes/test_sp_op_fraude_001.py` |
| CRED-001 | `...SP-OP-CRED-001_*`, `dmn/cred_*.dmn`, `workers/cred.py`, `test_sp_op_cred_001.py` |
| INADIMPLENCIA-001 | `...SP-OP-INADIMPLENCIA-001_*`, `dmn/inadimplencia_*.dmn`, `workers/inadimplencia.py`, `test_sp_op_inadimplencia_001.py` |
| PAGTO-001 | `...SP-OP-PAGTO-001_*`, `dmn/pagto_*.dmn`, `workers/pagto.py`, `test_sp_op_pagto_001.py` |
| ADEQUACAO-001 | `...SP-OP-ADEQUACAO-001_*`, `dmn/adequacao_*.dmn`, `workers/adequacao.py`, `test_sp_op_adequacao_001.py` |
| PROGRAMA-001 | `...SP-OP-PROGRAMA-001_*`, `dmn/programa_*.dmn`, `workers/programa.py`, `test_sp_op_programa_001.py` |
| cada agente | `agents/{carolina,fernando,valentina,beatriz,andre}/**` |
| plataforma | `platform/analytics/**` (André-adjacente mas owner de plataforma), `gateway/custody.py` (FRAUDE owner) |
| compartilhado (serial, orquestrador/PR humano) | `tools/process_allowlist.py`, `policies/process_allowlist.yaml`, `config/topic_registry.yaml`, `_hard_frozen.yaml` |

**Topologia de modelo (T1–T4, igual Phase 2):** BPMN/DMN authoring + workers + agentes em Opus (default — `fable`/T4 inacessível, ver nota); validações/lint em tiers menores; reviews adversariais em Opus. **Registrar todo topic novo no MESMO PR do BPMN** (validator hard-fail em topic não-registrado). XSD child-ordering generalizado desde o primeiro draft (evita ENGINE-09005).

**Paralelização:** W-A solo → W-B (3 em paralelo) → W-C (2 em paralelo) → W-D (5 em paralelo, um por agente) → W-E (serial-ish). Critical path em §2.

---

## 7. Registros DRAFT / review-queue (artefato → revisor)

| Artefato | Revisor obrigatório |
|---|---|
| RN 593 cure/purga + ownership rescisão (INADIMPLENCIA↔CANCEL) | regulatório + jurídico + arquitetura |
| RN 567 descred. prior-notice + substituição | regulatório |
| RN 259 thresholds de acesso (`adequacao_gap` DMN) | regulatório |
| Escada de alçada PAGTO + tier→candidate-group | finanças |
| Obrigações de referral fraude (ANS/civil/penal) + linkage CRED | jurídico + compliance |
| Nomes de candidate-group (todos os 6 processos) | PO / IdP do operador |
| Persona→domínio (R-PERSONA-MAP) | PO / produto |
| ADR-0019/0020 + promoção a `_hard_frozen` | arquitetura + compliance |
| `KNOWN_PROCESS_KEYS` +6 (ADR-0016) | PR humano (fail-closed) |
| k-anonimato/min-cohort para agregados Zona-Geral | compliance/DPO |

---

## 8. Riscos + Open Questions (consolidado das três pesquisas; precisa SME/regulatório)

**Riscos top:**
1. **Dupla-rescisão sob RN 593** — CANCEL-001 (Phase 2) já tem `End_ContratoSuspenso/Rescindido` no ramo inadimplência. Se INADIMPLENCIA-001 também rescindir, um contrato pode ser terminado duas vezes (`contract_termination` L0). **Definir ownership + coordenar business-keys (`INAD-{tenant}-{contrato}` vs `CANCEL-{tenant}-{contrato}`) antes de autorar.**
2. **Erosão de L0 (fraude/acusação)** — tentação de PR de "eficiência" deixar worker setar `decisao_fraude` ou auto-rotear indício → acusação. Mitigação: teste human-required + CI guard de não-downgrade de L0 por overlay de tenant (ADR-0008/0004), hard gate.
3. **Gap de integridade da custódia** — evidência sem WORM/Object-Lock ou sem selagem na chain antes da decisão → não-repúdio falha, acusação não defensável. Ordem selar-antes-de-decidir é load-bearing.
4. **Egress de PHI do André (lago)** — se agregação for só convenção (prompt) e não estrutural (gateway/`mcp-datalake` retornando só agregados), vaza PHI cru. Tem que ser chokepoint, não lembrete.
5. **Bypass de alçada PAGTO** — se o ramo L2 abaixo-do-teto for mal-escopado, pagamentos acima de alçada auto-liberam. DMN `pagto_alcada` com catch-all conservador → tier mais alto; guard verifica tier do aprovador vs valor.
6. **Leakage de consentimento PROGRAMA** — processar PHI de programa antes de consentimento (ou após revogação) = violação LGPD. Chokepoint `ERR_PROGRAMA_NO_CONSENT` gateia tudo; revogação interrompe.
7. **Tentação de import do reference (ADR-0011)** — `SP-PS-002_Credentialing` e `detect_fraud_worker` são atraentes mas devem ser **portados** (copiar+adaptar+re-testar+possuir aqui), nunca importados; anti-padrões de auto-decisão **invertidos**, não herdados.
8. **Erasure LGPD inconsistente amh↔Maezo** — surrogate `mpi_id↔fhir_patient_id` sobrevivente pode re-identificar. Erasure dropa o surrogate; verificação mensal.
9. **Leakage cross-tenant de analytics de fraude** — features multi-tenant na amh; papel consumidor com grant largo demais vê outros tenants. Consumir só por grants LF-Tag tenant-scoped.

**Open Questions (SME):**
- RN 593 consolida/supersede RN 412/2016? Dias úteis vs corridos? Quem detém o terminal de rescisão?
- `CONFIRMAR_INDICIO` (decisão do investigador) constitui `fraud_accusation` para a matriz, ou a acusação é ato downstream (`End_EncaminhadoJuridico`)? Onde desenhar a fronteira L0?
- WORM/retention vs erasabilidade LGPD: investigação aberta exige legal-hold que suspende erasure — confirmar política e interação com cascata ADR-0002.
- Fernando precisa de SP-OP-INADIMPLENCIA-001 próprio, ou opera como AGJ navegador que só escala para CANCEL-001 (igual Lucas)? (decide se há quádrupla).
- André é puramente AGJ, ou há SP-OP de relatório populacional HITL (espelhando ANS-SUBMIT) para filing atuarial/DIOPS? (se sim, rotear via Gustavo/ANS-SUBMIT).
- Contrato do lago: que interface de query agregada a amh expõe sobre S3/Parquet? `mcp-datalake` é port que possuímos ou cliente fino sobre API da plataforma? (ADR-0013 boundary).
- `ConsentGate` da amh é chamável de fora do repo amh (greenfield: não importamos), ou re-implementar contra `mpi.consent_log` (e possuir+testar aqui)?
- Janela de reprodutibilidade: snapshots time-travel do Feature Store rotacionam em 90d; dossiê de fraude precisa de evidência reproduzível por anos (regulatório 5+). Materializar/congelar o snapshot referenciado pelo bundle.
- `provider_decredentialing`/`erase_patient_memory` sobem para `_hard_frozen.yaml`? (governança = ADR + review).
- Programas de Valentina: quais (crônicos, pré-natal, oncologia, APS)? Enrollment exige captura de consentimento (UT) antes da decisão do coordenador?

---

## 9. AWS-blocked (issue #16) vs buildable agora

**Buildable agora (sem AWS):** todas as 6 quádruplas BPMN/DMN/worker + testes de integração no engine real (CI já tem CIB Seven); `custody.py` + teste de integridade; os 5 agentes + grafos + golden evals; `PopulationFeatureClient` + `ConsentGate` (contra fixtures/simulador, precedente ADR-0013); schema + lógica do canal proativo + testes; `process_allowlist` +6; ADR-0019/0020; templates Helm/NetworkPolicy zone-labeled (render + dry-run).

**AWS-blocked (design agora, enforce no deploy):** staging apply; acesso real ao S3 PHI-zone Object-Lock da amh + grants IAM/Lake-Formation tenant-scoped; LLM PHI-zone BR-residency real; conectividade real ANS/banco; validação fim-a-fim das NetworkPolicies PHI no cluster. Rastrear em issue #16 — não bloqueia D1–D7, D9, D10.

---

## Notas de execução para o orquestrador

- **`model: 'fable'` (T4/claude-fable-5) é INACESSÍVEL** nesta conta — usar default (herda Opus) para tudo, inclusive draft de ADR. Não rotear nenhum agente para `fable`.
- **Caminho crítico:** `W0(custody.py) → FRAUDE-001 (pathfinder, solo) → Beatriz → W-E`. Não clonar W-B antes de FRAUDE-001 mergear verde no engine real.
- **W-E DEVE harmonizar INADIMPLENCIA-001 ↔ CANCEL-001** (business-keys + ownership do terminal de rescisão) antes de qualquer autoria que toque rescisão — sob pena de dupla-rescisão (L0).
- **Toda RN/prazo/candidate-group/alçada é DRAFT/verify** — manter marcado, sign-off com regulatório/jurídico/finanças/PO antes de FINAL (mesma disciplina de CONTAS/AUTH em Phase 2).
- Fonte deste plano: dossiês R1/R2/R3 do swarm `wr9q6qes1` (o agente de síntese alegou ter escrito este arquivo mas não persistiu — reautorado aqui pelo orquestrador, aterrado em fatos verificados do repo).
