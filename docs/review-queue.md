# Review queue — artefatos regulados/clinicos DRAFT

Registro obrigatorio de todo artefato com conteudo clinico ou regulatorio em DRAFT.
Regra dura: nada desta lista vai a deploy sem revisao humana registrada (PR aprovado
pelo revisor competente). O orquestrador espelha pendencias em
`docs/handoffs/HANDOFF.yaml -> review_queue_pending`.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `spec/processes/dmn/triage_redflag_adult.dmn` | Conteudo clinico: lista de sintomas, limiares de intensidade/idade, prioridades P1/P2, regra fail-safe | medico (diretriz de triagem) | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/triage_redflag_pediatric.dmn` | Conteudo clinico pediatrico (limiar febre <3 meses, desidratacao, etc.) | medico pediatra | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/triage_redflag_gestante.dmn` | Conteudo clinico obstetrico (pre-eclampsia >=20s, MF reduzidos >=26s, etc.) | medico obstetra | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/triage_redflag_mental_health.dmn` | Conteudo clinico de saude mental + postura conservadora (tudo escala?) | psiquiatra/psicologo + gestao assistencial | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/escalation_routing.dmn` | Valores de SLA (ack/resolucao por prioridade) e mapeamento grupo humano | gestao assistencial + compliance | `DRAFT — requires human review before any deploy` (shape FINAL) |
| `spec/processes/dmn/lucas_billing_admissibility.dmn` (GAP-XHITL-4B; consumida pelo agente Lucas via mcp-dmn) | Regra de negocio de cobranca: LIMIAR `ciclos_sem_conciliacao` >= 1 como indicio de inadimplencia (confirmar politica de conciliacao CNAB); mapeamento `tipo_solicitacao` (boleto/2a_via/vencimento/status_pagamento) -> RESPONDER/LEMBRETE/ESCALAR_HUMANO; confirmar que status_pagamento nao-conciliado sem ciclo cai no catch-all humano (nunca reporta status incerto); catch-all fail-safe conservador. Nenhum caminho cobra/suspende/nega (invariante estrutural {RESPONDER,LEMBRETE,ESCALAR_HUMANO}) | financeiro/PO (limiar de inadimplencia + politica de cobranca) | `DRAFT — requires human review (financeiro/PO)` (shape FINAL) |
| `spec/processes/dmn/lucas_escalation_routing.dmn` (GAP-XHITL-4B; consumida pelo agente Lucas via mcp-dmn) | Mapeamento `motivo`/`intencao` -> grupo humano (CONTRATOS_HUMANO/COBRANCA_HUMANO/ATENDIMENTO_HUMANO); confirmar taxonomia de grupos (cobranca vs atendimento vs contratos) e se `ciclos_sem_conciliacao` deve discriminar prioridade; catch-all fail-safe conservador (atendimento humano generico). Nenhum destino e adverso — todos sao grupos humanos | financeiro/PO + gestao assistencial (taxonomia de grupos humanos) | `DRAFT — requires human review (financeiro/PO)` (shape FINAL) |
| `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn` + `docs/processes/contracts/SP-OP-AUTH-001.md` | Fluxo completo (pendencia, junta RN 424, negativa formal RN 395); confirmar textos vigentes RN 259/395/424 e consolidacoes ANS; anexos TISS obrigatorios | medico auditor + juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn` — `camunda:formData` de `UT_AnaliseMedicoAuditor` (GAP-AUTH-2) | Conteudo clinico do formulario do auditor: rotulos dos campos e o conjunto EXATO de campos obrigatorios ao NEGAR (hoje justificativa_clinica / cid10_referencia / fundamentacao_dut — confirmar completude e nomes contra RN 395 art. 10), e se a requiredness condicional (`requiredIf`) deve virar validacao server-side (submit-form) alem do guard do worker | medico auditor + juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/auth_admissibility.dmn` | Criterios de admissibilidade; confirmacao de que nenhum caminho gera negativa automatica | medico auditor + compliance | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/auth_auto_approval.dmn` | Criterios de aprovacao automatica L2 (DUT/ROL, teto por tenant, rede) | medico auditor + compliance | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/auth_sla.dmn` | TODOS os prazos (dias uteis vs corridos; RN 259/395 vigentes; urgencia operacionalizada em 2h) | juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn` + `docs/processes/contracts/SP-OP-LGPD-DSR-001.md` | Fluxos LGPD (prazos internos de prova de identidade; revogacao de consentimento; matriz de retencao legal) | DPO + juridico | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/lgpd_dsr_routing.dmn` | Roteamento e prazos por tipo de requisicao (art. 18/19) | DPO + juridico | `DRAFT — requires human review before any deploy` |

Notas:
- SP-OP-ESCALATION-001 (BPMN/contrato/test spec) e FINAL em estrutura; entra aqui apenas
  pelos VALORES de SLA da `escalation_routing` (linha acima).
- Citacoes de RN marcadas `DRAFT/verify` nos artefatos devem ser confirmadas contra o
  texto vigente da ANS antes da promocao (RN 259 pode ter sido consolidada/substituida).

---

## Revisao de compliance W8 — 9 acoes de autonomia tool-level (PR #8 `feat/tools-mcp-phase0`)

Avaliacao das acoes adicionadas em `src/maezo/policies/autonomy/L0-core.yaml` (linhas 21-35)
pela revisao de seguranca W8 (QA/SEC). Para cada acao: o nivel atribuido e adequado? Veredito
e um paragrafo de justificativa. Acoes marcadas `DOWNGRADE` devem ser endurecidas antes do
deploy de Phase 1 (Phase 0 so usa Helena, cuja allowlist e read-only + escalonamento — ver
nota final).

| Acao | Nivel | Veredito W8 |
|---|---|---|
| `query_decision_engine` (mcp-dmn) | L3 | **OK.** Consulta deterministica de DMN (ADR-0012), sem efeito externo nem PHI; o agente raciocina sobre o resultado mas a decisao clinica/regulatoria continua na DMN versionada + revisor humano. L3 (executa + telemetria) e adequado. |
| `start_compliance_process` (mcp-cibseven) | L2 | **OK, com ressalva.** Iniciar um SP-OP e efeito externo auditado e regulatoriamente mandatorio (escalonamento nunca pode se perder), entao L2 (executa, auditado) e defensavel. Ressalva: o `process_key` deve ser restrito por allowlist a processos cujo start e seguro (ESCALATION/LGPD-DSR). Iniciar AUTH-001 (que pode levar a negativa) por agente nao deveria ser L2 — recomendo que o handler valide `process_key in {SP-OP-ESCALATION-001, SP-OP-LGPD-DSR-001}` para Phase 0/1. |
| `correlate_process_message` (mcp-cibseven) | L2 | **OK.** Entregar uma mensagem de correlacao a uma instancia ja existente (avanca o fluxo HITL) e efeito externo limitado e auditado; nao cria nem decide nada. L2 adequado. |
| `query_process_status` (mcp-cibseven) | L3 | **OK.** Leitura de estado de processo, sem efeito nem PHI cru (business key + status). L3 adequado. |
| `read_phi_data` (mcp-fhir) | L3 | **DOWNGRADE p/ revisao — manter L3 funcional, mas exige controle compensatorio.** Ler dado clinico e a acao mais sensivel do conjunto. L3 e aceitavel SOMENTE porque o gateway pseudonimiza ANTES da Zona Geral (ADR-0006) e o teste de PHI-leak (tests/integration/sec/test_phi_leak.py) prova que nenhum identificador cru chega ao agente. O risco residual NAO esta no nivel, mas na garantia de que a leitura sempre passe pelo pseudonimizador: recomendo um teste de arquitetura que falhe se um caminho de `read_phi_data` ignorar o gateway. Sem esse controle, eu trataria como L2 (auditar cada leitura individualmente). Flag aberta. |
| `send_beneficiary_message` (mcp-whatsapp) | L3 | **DOWNGRADE recomendado p/ texto livre.** Enviar mensagem de texto livre ao beneficiario e efeito externo IRREVERSIVEL com superficie de risco (conteudo clinico nao revisado, tom, alucinacao). L3 e arriscado para texto livre gerado por LLM. Recomendo: texto livre permanece L3 apenas dentro de um conjunto restrito de respostas informativas/template-like; qualquer mensagem que cite cobertura/conduta deveria escalar. Em Phase 0 a Helena so escala (nao responde texto livre clinico), entao o risco esta contido — mas marcar para endurecer antes de habilitar resposta autonoma. |
| `send_beneficiary_template` (mcp-whatsapp) | L2 | **OK.** Template pre-aprovado (WABA) e conteudo fixo e auditavel; L2 (executa, auditado) e o nivel certo — mais restrito que texto livre, coerente. |
| `read_write_memory` (mcp-memory) | L3 | **OK, com ressalva.** Memoria episodica/semantica opera sobre dados ja pseudonimizados; leitura/escrita sem efeito externo justifica L3. Ressalva: "write" pode persistir PHI cru se o agente compuser conteudo a partir do turno do usuario — o mesmo controle compensatorio do `read_phi_data` (pseudonimizacao garantida) se aplica. A bateria de PHI-leak cobre o caminho do grafo; recomendo estender ao path de escrita de memoria quando mcp-memory tiver wiring real. |
| `erase_patient_memory` (mcp-memory) | L1 | **OK.** Apagamento (LGPD art. 18) e operacao destrutiva e regulatoriamente sensivel; L1 (agente propoe, DPO/humano confirma) e o nivel correto. Coerente com o fluxo SP-OP-LGPD-DSR-001. |

**Nota de contencao para Phase 0:** a allowlist da Helena (`agent.yaml`) e, na pratica,
read-only + escalonamento: `mcp-dmn.evaluate`, `mcp-fhir.read_patient_summary`,
`mcp-cibseven.start_process/get_status`, `mcp-whatsapp.send_message`, `mcp-memory.read_write`.
Nenhuma acao `hard` ou de negativa esta ao alcance dela, e o PEP nega por allowlist antes da
matriz (provado em tests/unit/sec/test_pep_bypass.py). Os DOWNGRADE/ressalvas acima sao para a
PROMOCAO de Phase 1 (resposta autonoma, mais agentes), nao bloqueiam o DoD de Phase 0. As
duas flags abertas a endurecer: (1) restringir `start_compliance_process` por `process_key`;
(2) controle de arquitetura garantindo que `read_phi_data`/memory-write nunca contornem o
pseudonimizador.

---

## Phase 1 — DUT/ROL/Carencia DMN set (feat/proc-phase1-dut-rol-drafts; consumidor wired em GAP-AUTH-3)

Artefatos adicionados na branch `feat/proc-phase1-dut-rol-drafts`. Nenhum pode ir a
deploy sem revisao humana registrada. Conteudo clinico e regulatorio SINTETICO.

**Consumidor (GAP-AUTH-3, `fix/w1-auth-dut-consumers`):** as 5 tabelas sao avaliadas pelo grafo
do Rafael (via `mcp-dmn.evaluate`) na ROTA HUMANA de SP-OP-AUTH-001 como INPUTS informativos do
dossie do medico-auditor — a DMN informa, o humano decide (ADR-0005/0018). Nenhuma altera o
roteamento nem produz negativa (catch-all fail-safe -> analise humana). Deliberadamente SEM
businessRuleTask no BPMN enquanto o conteudo clinico for DRAFT (a wiring de agente nao as torna
deployaveis; o gate de sign-off as governa como `intentional_draft` — `config/artifact_signoff.yaml`).
A selecao da `dut_criteria_*` por classe do procedimento e plumbing no grafo; a REGRA fica na DMN.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `spec/processes/dmn/dut_rol_coverage.dmn` | Mapeamento TUSS→ROL/DUT: codigos TUSS SINTETICOS (verificar tabela TUSS vigente ANS); flags no_rol e requer_dut por codigo; catch-all fail-safe | medico auditor + juridico/regulatorio | `DRAFT — requires human review (médico auditor) before any deploy` |
| `spec/processes/dmn/dut_criteria_bariatrica.dmn` | Criterios clinicos cirurgia bariatrica: limiares IMC (35/40), comorbidades qualificantes, numero de tentativas previas, avaliacao multidisciplinar — todos SINTETICOS; verificar DUT ANS vigente + CFM 2.131/2015 e atualizacoes; validar o mapeamento classe→tabela (internacao/opme → bariatrica) do grafo do Rafael | medico auditor + cirurgiao bariatrico + juridico/regulatorio | `DRAFT — requires human review (médico auditor) before any deploy` |
| `spec/processes/dmn/dut_criteria_oncologia_pet_ct.dmn` | Criterios DUT PET-CT oncologia: finalidades elegíveis (estadiamento/reestadimento/avaliacao_resposta/deteccao_recorrencia), lista de neoplasias elegiveis, exigencia de exames convencionais inconclusivos por finalidade — SINTETICOS; verificar DUT ANS PET-CT vigente; validar o mapeamento classe→tabela (exame_especial/alta_complexidade → PET-CT) do grafo do Rafael | medico auditor + oncologista/nucleo-medicina + juridico/regulatorio | `DRAFT — requires human review (médico auditor) before any deploy` |
| `spec/processes/dmn/dut_criteria_terapias_especiais.dmn` | Criterios DUT terapias especiais (TEA/neurodesenvolvimento): modalidades (ABA, fono, TO sensorial, psicoterapia); limites de sessoes/mes SINTETICOS (ABA 20/mes, demais 8/mes); composicao equipe multidisciplinar; verificar contra DUT ANS + Lei 12.764/2012 + Lei 14.254/2021 + RN 465/2021 | medico auditor + neuropediatra/psiquiatra infantil + juridico/regulatorio | `DRAFT — requires human review (médico auditor) before any deploy` |
| `spec/processes/dmn/carencia_check.dmn` | TODOS os prazos de carencia (urgencia 24h/1d, parto 300d, eletivo 180d, CPT 24m/730d) sao SINTETICOS; verificar Lei 9.656/1998 art. 11/12/35-C + RN 195/2009 + RN 259/2011 + consolidacoes ANS vigentes; portabilidade de carencia NAO implementada (SME necessario); validar o mapeamento carater/categoria→tipo_procedimento do grafo do Rafael (urgencia→urgencia_emergencia; internacao/alta_complexidade/opme→alta_complexidade; resto→eletivo) | juridico/regulatorio + medico auditor | `DRAFT — requires human review (médico auditor) before any deploy` |

Itens que necessitam SME humano antes de qualquer draft tecnico adicional:
- **Portabilidade de carencia** (RN 186/2009): logica de reducao/zeragem de carencia
  por portabilidade entre planos nao implementada em `carencia_check.dmn`; requer
  mapeamento juridico detalhado antes de qualquer codificacao.
- **PET-CT neurologico/cardiologico**: `dut_criteria_oncologia_pet_ct.dmn` cobre
  apenas indicacoes oncologicas; indicacoes neurologicas e cardiologicas requerem
  DUT separada e SME especialista adicional.
- **Lista completa de CIDs elegiveis para terapias especiais TEA**: tabela atual usa
  flag booleana `diagnostico_tea_ou_neurodesenvolvimento`; mapeamento granular de
  CIDs aceitos por tipo de terapia requer validacao com neuropediatra e juridico.
- **Limites de sessoes por modalidade de terapia**: valores SINTETICOS usados
  (ABA 20/mes, demais 8/mes) — valores reais dependem de DUT ANS especifica e
  contrato de plano; necessita medico auditor + juridico para confirmar.

---

## Phase 2 — contract sheets (Wave 0) + ports

Quadruplas Phase 2 (PR #28): contrato + test-spec por processo. Estrutura no-denial
revisada e aprovada (review adversarial PASS) — mas todo conteudo regulatorio/clinico e
DRAFT. As DMN/BPMN bodies ainda nao existem (proxima wave, autoradas contra estes contratos).

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `SP-OP-CONTAS-001.md` + test-spec (+ DMN glosa_* futuras) | Fluxo glosa; SLA triagem/analise (P30D dias uteis vs corridos); RN 305/2012 (TISS-glosa) e 424/2017 vigentes; excecao auto-route glosa tecnica (R4) so com sign-off; nenhum branch auto-flagueia fraude (L0 hard); candidate groups auditoria-contas/coordenacao-contas | medico-auditor/auditoria-contas + compliance + juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `SP-OP-RECURSO-001.md` + test-spec (+ recurso_*.dmn futuras) | Admissibilidade do recurso; prazo recursal RN 424/2017; prazo max P30D; fluxo TISS RN 305/501; pinar terminal `inadmissivel` como humano-gated | auditoria-contas + juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `recurso_sla.dmn` v0.2.0 + `SP-OP-RECURSO-001` boundary de teto (GAP-RECURSO-1) | **Semantica do teto RN 424 P30D: JANELA UNICA ABSOLUTA** contada do inicio regulatorio — ancora `data_recebimento_recurso_iso` (recebimento do recurso; seeded pelo intake), fail-safe `data_ciencia_glosa`; os 3 boundary (analista/coordenacao/auditor) expiram no MESMO instante (`timeDate`), a escalada NAO estende o teto; confirmar com juridico/regulatorio a ancora legal exata (recebimento vs ciencia) e dias uteis vs corridos | juridico/regulatorio + auditoria-contas | `DRAFT — requires human review before any deploy` |
| `SP-OP-NIP-001.md` + test-spec (+ nip_*.dmn futuras; nip_sla.dmn v0.2.0 GAP-NIP-1: deadline ABSOLUTO = `data_recebimento_nip_iso` + duracao, FEEL `date and time(...) + duration(...)`, anexado aos boundary timers via `timeDate`) | Prazos ANS **HARD** (RN 388: assistencial ~5 / nao-assistencial ~10 dias uteis) + conversao dias-uteis→ISO conservadora; textos legais da resposta; candidate groups nucleo-ans/regulatorio-ans; **confirmar que a ancora de meia-noite (`T00:00:00`) da data de recebimento e o corte correto (vs. horario real de protocolo)** | juridico/regulatorio + regulatorio-ANS (+ medico-auditor quando assistencial) | `DRAFT — requires human review before any deploy` |
| `SP-OP-ANS-SUBMIT-001.md` + test-spec (+ ans_*.dmn futuras) | Datas de competencia, periodicidades, cron de TimerStartEvent, fontes RN do calendario (124/209/388/424, DIOPS); papel de sign-off vinculante pre-filing | regulatorio-ANS (sign-off vinculante) | `DRAFT — requires human review before any deploy` |
| `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md` (:59) + `workers/ans_submit.py` (`make_assemble_handler`) + BPMN `ST_AssembleDataset`/`UT_CorrigirPendenciaEnvio` (GAP-ANS-5) | LGPD: `lgpd_anonimizado` era documentado como "pre-resolvido por worker" mas nenhum worker o calculava/ecoava — so `notifications_bridge/consumer.py` o seedava, sempre `false` fail-closed, sem caminho de correcao documentado (UT_CorrigirPendenciaEnvio so mencionava `dataset_complete`/`schema_valid`). Fix: `regulatorio.anssubmit.assemble` agora ecoa `lgpd_anonimizado` (mesmo padrao non-computing de `dataset_complete` — NAO e um calculo real de anonimizacao; `mcp-regdata`, a fonte de um atestado real, esta AWS-blocked issue #16) e a UT documenta que o humano so marca `lgpd_anonimizado=true` apos confirmar que o dataset e de fato agregado/anonimizado. Confirmar que este fail-closed + correcao-humana-explicita e a postura LGPD aceitavel para o envio periodico ANS (vs. um atestado automatico real quando `mcp-regdata` for desbloqueado) | regulatorio-ANS + DPO/compliance (ADR-0006) | `DRAFT — requires human review before any deploy` |
| `SP-OP-CANCEL-001.md` + test-spec (+ cancel_*.dmn futuras) | RN 593 (cancelamento/rescisao/suspensao por inadimplencia); prazo de notificacao previa; hipoteses de rescisao unilateral por tipo_plano (Lei 9.656 art.13); contract_termination L0 hard | juridico/contratos + compliance | `DRAFT — requires human review before any deploy` |
| `SP-OP-REEMBOLSO-001.md` + test-spec (+ reembolso_*.dmn futuras) | RN 259 / Lei 9.656 art.12 / RN reembolso vigente; todos os prazos (analise ~30d, urgencia, pendencia, pagamento) + dias-uteis→ISO; tabela de referencia/multiplo + teto auto-aprovacao L2 | medico-auditor + analise-reembolso + atuarial + juridico | `DRAFT — requires human review before any deploy` |
| `src/maezo/domain/glosa.py` (PR #29) — GlosaReasonCode (26) + REASON_CODE_TO_TYPE + is_appealable | Validar cada codigo e mapeamento contra a tabela oficial de motivos de glosa TISS/ANS vigente; confirmar conjunto recorrivel antes de WA.4 usar `is_appealable` em producao | equipe-faturamento + juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `src/maezo/integrations/cnab/parser.py` (PR #29) — offsets CNAB 240/400 | Validar offsets de campo contra arquivos de retorno reais do banco contratado (variantes por banco); cross-check com o `cnab_parser.py` do reference | WC.3/Lucas + operacoes financeiras | `DRAFT — requires bank-file validation before production` |

**Nota:** nomes de candidate group em TODOS os contratos Phase 2 sao PROPOSTOS — confirmar
contra a taxonomia organizacional da operadora antes de congelar os BPMN (`camunda:candidateGroups`).

---

## Phase 3 — foundations (one-phase-ahead drafting; branch `feat/phase3-foundations`)

Fundacoes Phase 3 autoradas UMA FASE A FRENTE (playbook 5-bis): 6 contract sheets + 2 ADRs,
todos DRAFT. Nenhum pode ir a deploy sem revisao humana registrada. O padrao no-adverse de
cinco partes (ADR-0018) cobre AGORA **todo** efeito adverso (nao so negativa de cobertura):
fraude/descredenciamento/rescisao-suspensao/negativa-de-reembolso-ou-programa/liberacao acima
de alcada. Revisores mapeados por phase3-plan §7. Conteudo regulatorio/clinico/financeiro SINTETICO.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `docs/processes/contracts/SP-OP-FRAUDE-001.md` + test-spec (+ `fraude_*.dmn`/`fraude.py`/`custody.py` futuros) | Fraude referral (ANS/civel/penal) — obrigacoes, prazos, autoridade competente; fronteira L0 (`ACUSAR_FRAUDE` constitui acusacao por si vs. ato downstream); RN 593/RN 567/Lei 9.656 art.13 (interacao CRED/CANCEL/INADIMPLENCIA); escada de alcada/tier por destino; candidate groups `investigacao-fraude`/`coordenacao-investigacao`/`juridico-fraude`; cadeia de custodia probatoriamente aceitavel (CPC/CPP); k-anon/min-cohort + snapshot 90d; regra de correlacao de `numero_caso`; persona Beatriz↔dominio fraude | jurídico + compliance (referral fraude); regulatório (RN 593/567); DPO (custodia/retencao probatoria vs LGPD); PO/IdP (candidate groups); PO/produto (R-PERSONA-MAP Beatriz) | `DRAFT — requires human review before any deploy` |
| `docs/processes/contracts/SP-OP-CRED-001.md` (+ `cred_*.dmn`/`cred.py` futuros) | RN 567 (descredenciamento — notificacao previa/substituicao equivalente) e RN 566 (dimensionamento de rede); duas direcoes adversas (negar credenciamento; descredenciar) L1; promocao de `provider_decredentialing` a `_hard_frozen` (L1); candidate groups `gestao-rede`/`juridico-rede`/`coordenacao-rede`; matriz de hipoteses por `tipo_prestador`; payload `network_changed` (handoff a ADEQUACAO); linkage FRAUDE | regulatório + jurídico + arquitetura (RN 593/567 ownership); regulatório (RN 567); arquitetura + compliance (`_hard_frozen` L1); PO/IdP (candidate groups) | `DRAFT — requires human review before any deploy` |
| `docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md` (+ `inadimplencia_*.dmn`/`inadimplencia.py` shipped) | ~~HARMONIZACAO BLOQUEANTE com CANCEL-001~~ — **RESOLVIDA e SHIPPED (DRAFT-A, GAP-INAD-5)**: CANCEL-001 detem o UNICO terminal de rescisao; INADIMPLENCIA-001 detem cobranca/purga/suspensao + handoff neutro (`handoff_rescisao`); contrato reconciliado com o BPMN (`decisao_inadimplencia=ENCAMINHAR_RESCISAO`, nao `RESCINDIR`; `register_contract_rescission`/`Error_ContractRescissionNotHuman` DROPPED — nunca wireados). O que resta DRAFT/verify: RN 593 (janela de purga/cura, notificacao previa, periodo minimo; supersede/consolida RN 412/2016?); dias uteis vs corridos; candidate groups `juridico-contratos`/`gestao-cobranca`/`coordenacao-cobranca` (coordenar com CANCEL-001); `contract_termination` L0 cobre ambos os processos | regulatório (RN 593 dias/supersessao); arquitetura + compliance (`_hard_frozen` L0, confirmar cobertura); PO/IdP (candidate groups) | `DRAFT — requires human review (médico auditor / jurídico) before any deploy` |
| `docs/processes/contracts/SP-OP-PAGTO-001.md` (+ `pagto_*.dmn`/`pagto.py` futuros) | Escada de alcada completa (`threshold_brl` L1, faixas L1/L2/L3, teto de auto-liberacao L2 por tenant) + mapeamento faixa→grupo→tier (value-driven candidate groups); segregacao de funcoes (aprovador ≠ solicitante; quorum de comite); candidate groups `aprovacao-financeira-l1/l2/l3`/`comite-financeiro`/`coordenacao-financeira`; interacao com CONTAS-001 (conta adjudicada→ordem) e tesouraria/CNAB | finanças (alçada/tier→group, segregacao de funcoes); finanças + PO/IdP (candidate groups value-driven) | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/pagto_admissibility.dmn` (gate pre-alcada de SP-OP-PAGTO-001; GAP-PAGTO-1) | Regra de admissibilidade da ordem de pagamento (lastro/duplicidade/dados) que gateia o roteamento de alcada: mapeamento dos 3 booleanos → `roteamento` (SEGUE_ROTEAMENTO/PENDENTE_DADOS/ANALISE_HUMANA); politica de "lastro nao confirmado" e "duplicidade suspeita" → analise humana (jamais auto-libera); grupo humano da analise de admissibilidade (`coordenacao-financeira` PROPOSTO — confirmar taxonomia); catch-all fail-safe conservador | jurídico/financeiro (lastro/duplicidade → analise humana; segregacao de funcoes); finanças + PO/IdP (grupo `coordenacao-financeira` da UT de admissibilidade) | `DRAFT — requires human review (jurídico/financeiro)` |
| `docs/processes/contracts/SP-OP-ADEQUACAO-001.md` (+ `adequacao_*.dmn`/`adequacao.py` futuros) | Thresholds tempo/distancia RN 259 por `tipo_carater` + RN 566 (dimensionamento) + periodicidade do `ciclo_avaliacao`; fato `agents.events.cred.network_changed` (STUB — harmonizar com CRED-001 em W-E); candidate groups `gestao-rede`/`coordenacao-rede`; politica financeira do compromisso de fallback (interacao PAGTO/REEMBOLSO); taxonomia especialidade/geografia | regulatório (RN 259 thresholds + RN 566 + periodicidade); arquitetura (harmonizacao STUB network_changed); PO/IdP (candidate groups); finanças (politica de fallback) | `DRAFT — requires human review before any deploy` |
| `docs/processes/contracts/SP-OP-PROGRAMA-001.md` (+ `programa_*.dmn`/`programa.py` futuros) | Consent-gate LGPD (art.7/11/8§5/18§2) + correlacao revogacao com SP-OP-LGPD-DSR-001; desligamento clinico L0-hard (decisao clinica); taxonomia/criterios de programa (cronicos/pre-natal/oncologia/APS); RN ANS de programas de promocao a saude (existe?); candidate groups `coordenacao-clinica`/`equipe-cuidado`; retencao/cessacao de PHI pos-revogacao; k-anon/small-cell para agregados populacionais (WP3.5); enrollment exige UT de consentimento antes do coordenador? | DPO + jurídico (consent/revogacao/retencao + k-anon/min-cohort); médico-auditor (criterios clinicos + desligamento); regulatório (RN de programa); PO/IdP (candidate groups); PO/produto (persona Valentina) | `DRAFT — requires human review before any deploy` |
| `docs/adr/0019-amh-lake-of-record.md` (ADR-0019 — Proposed) | `ConsentGate scope=operational_analytics` (separacao de finalidade LGPD); k-anonimato/small-cell-suppression (piso `k` versionado); fronteira "agregado anonimo sobrevive ao erasure"; drop do surrogate `mpi_id<->fhir_patient_id`; grants LF-Tag tenant-scoped (desenho; enforcement AWS-blocked) | arquitetura + compliance (decisao ADR); DPO (k-anon/min-cohort + consent scope + fronteira de erasure); jurídico/regulatório/atuarial (base legal `operational_analytics`) | `DRAFT — requires human review before any deploy` |
| `docs/adr/0020-custody-chain.md` (ADR-0020 — Proposed) | Custody chain como PROJECAO sobre ADR-0007 (nao fork); `bundle_root` Merkle selado antes da decisao humana; evidencia bruta como `input_hash`+Object-Lock S3 PHI-zone; freeze de snapshot de feature-store (reprodutibilidade); retencao 5+ anos vs erasure LGPD + legal-hold | arquitetura + compliance (decisao ADR + projecao sobre ADR-0007); jurídico + regulatório + DPO (retencao 5+ anos vs erasure/legal-hold; aceitabilidade probatoria) | `DRAFT — requires human review before any deploy` |

Itens transversais Phase 3 (gates de promocao — registrados aqui para nao se perderem):
- **`_hard_frozen.yaml` promotion** — `provider_decredentialing` (L1, CRED-001), `contract_termination`
  por inadimplencia (L0, INADIMPLENCIA-001 ∩ CANCEL-001), `fraud_accusation` (L0, FRAUDE-001),
  `high_value_payment` (L1, PAGTO-001), desligamento clinico (L0 "qualquer decisao clinica",
  PROGRAMA-001): promover a `_hard_frozen` (CI-enforced, nao rebaixavel por overlay de tenant) →
  **arquitetura + compliance** (ADR-0008/0018/0019/0020).
- **`KNOWN_PROCESS_KEYS` +6** — adicionar `SP-OP-FRAUDE-001`, `SP-OP-CRED-001`,
  `SP-OP-INADIMPLENCIA-001`, `SP-OP-PAGTO-001`, `SP-OP-ADEQUACAO-001`, `SP-OP-PROGRAMA-001` ao
  allowlist de start (ADR-0016) → **PR humano CODEOWNERS** (fail-closed; nao habilitado em main
  ate o merge).
- **persona→domínio (R-PERSONA-MAP)** — Beatriz↔fraude ("a chamada mais fragil"), Carolina↔cred,
  Fernando↔inadimplencia, Andre↔pagto/adequacao, Valentina↔programa: confirmar mapeamento antes
  de autorar os agentes → **PO/produto**.
- **k-anon / min-cohort (small-cell suppression)** — piso `k` versionado para todo agregado
  populacional exposto a Zona Geral (ADR-0019; WP3.5; PROGRAMA-001) → **DPO**.

**Nota Phase 3:** todos os candidate groups dos 6 contratos sao PROPOSTOS — confirmar contra a
taxonomia organizacional da operadora (PO/IdP) antes de congelar os `camunda:candidateGroups`.
Toda citacao RN (593/567/566/259) e prazo/alçada/tier e DRAFT/verify — confirmar contra texto
vigente ANS / politica financeira antes da promocao. A harmonizacao INADIMPLENCIA↔CANCEL-001
(ownership do terminal de rescisao) **foi RESOLVIDA e esta SHIPPED** (DRAFT-A — ver
`docs/processes/harmonization-inadimplencia-cancel.md` + GAP-INAD-5 abaixo); ja NAO bloqueia nada.

---

## Phase 3 — SP-OP-FRAUDE-001 (materializado em W-A; pathfinder cadeia de custodia)

Artefatos materializados contra `docs/processes/contracts/SP-OP-FRAUDE-001.md`. Nenhum vai a
deploy sem revisao humana registrada. Conteudo regulatorio/clinico SINTETICO.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn` | Fluxo completo (selagem-antes-da-decisao, terminais de referral ANS/civel/penal, diligencia); fronteira L0 `fraud_accusation` (ACUSAR vs referral downstream) | juridico + compliance + medico-auditor | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/fraude_sla.dmn` | TODOS os prazos (`sla_investigacao`/`sla_alerta`/`sla_diligencia` ISO 8601) + `fonte_regulatoria` — obrigacoes de referral nao pinadas | juridico/regulatorio + compliance | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/fraude_indicadores.dmn`, `fraude_routing.dmn` | Intensidade/roteamento + nomes de candidate group (`investigacao-fraude`/`coordenacao-investigacao`/`juridico-fraude`) | PO/IdP + compliance | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/fraude_scoring/*.dmn` (7 portadas/invertidas) (não portado — pendente T2.7) | Limiares de indicadores (upcoding/unbundling/phantom/zscore/peer-deviation/risk) — SINTETICOS; `score` e FATO DE ROTEAMENTO, nunca veredito | medico-auditor + auditoria especial | `DRAFT — requires human review before any deploy` |
| Cadeia de custodia (`src/maezo/gateway/custody.py` + ADR-0020) | Aceitabilidade probatoria do `bundle_root`-Merkle-sobre-hash-chain; retencao 5+ anos vs erasure LGPD; desenho de legal-hold | juridico/regulatorio + DPO | `DRAFT — requires human review before any deploy` |

| `src/maezo/platform/integrations/notifications_bridge/consumer.py` (`contratual_target`) | Split de roteamento `fraude.start_contratual` por `entidade_tipo`: beneficiario→SP-OP-CANCEL-001, contrato→SP-OP-INADIMPLENCIA-001, default CANCEL (PR #103, GAP-XPROC-1) — semantica de encaminhamento adverso precisa de confirmacao de negocio | produto + juridico | `DRAFT — requires human review before any deploy` |


---

## Wave-1B — network_change_bridge: CRED→ADEQUACAO choreografia (GAP-XPROC-2)

Consumidor real de `agents.events.cred.network_changed` que INICIA SP-OP-ADEQUACAO-001 (PR #117).
A ponte so INICIA a avaliacao da celula; o unico efeito adverso de ADEQUACAO (compromisso
financeiro de fallback) permanece human-gated LA (`UT_DecisaoFallback`). Nenhum PHI de beneficiario
no fato nem nas vars de start (teste de arquitetura estrutural).

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `src/maezo/platform/integrations/network_change_bridge/consumer.py` (`derive_ciclo_avaliacao`) | **Periodicidade do `ciclo_avaliacao`**: a ponte deriva o ciclo como TRIMESTRE (`YYYY-Qn`) de `data_efeito_iso` do fato (deterministico — idempotencia da reentrega; 1 avaliacao por celula por trimestre de efeito). Trimestre vs mes e escolha de politica regulatoria (RN 259/566 — periodicidade de avaliacao de adequacao) **DRAFT/verify** | regulatorio + PO | `DRAFT — requires human review before any deploy` |
| `spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn` (`ST_PublishNetwork*` `event_payload_vars`) + contrato | `regiao_saude`/`especialidade` agora viajam no fato `network_changed` (identidade da celula p/ ADEQUACAO). Sao dado CADASTRAL do prestador semeado no start de CRED — confirmar a FONTE cadastral (base de rede / Carolina `credentialing.analyze`) e a taxonomia (regiao de saude vs municipio IBGE; TUSS/CBO) | PO + arquitetura (base de rede) | `DRAFT — requires human review before any deploy` |
| `deploy/helm/maezo-tenant/templates/deployment-bridge-netchange.yaml` | Daemon novo (sibling do notifications-bridge; MESMA identidade de principal de servico `notifications-bridge` — allowlist minima start_process). Confirmar se as duas pontes devem compartilhar a identidade ou ter principals distintos na auditoria | arquitetura + seguranca | `DRAFT — requires human review before any deploy` |

---

## Wave-1 — SP-OP-LGPD-DSR-001 fail-closed decision gate (GAP-LGPD-3 / GAP-LGPD-4)

`GW_DecisaoDsr` (`fix/w1-lgpd-fail-closed`): o default do gateway deixou de enviar dados
(`Flow_GWDec_Enviar` era incondicional) e agora vai para `End_ErrDecisaoInvalida`
(`ERR_DSR_DECISION_INVALID`) — o envio so ocorre com `decisao_dsr` EXPLICITO
(`APROVAR_ENVIO` | `EXECUTAR_E_ENVIAR`). Novo guard `GW_GuardFundamentacao` (avaliado pelo
ENGINE, GAP-LGPD-3) barra `NEGAR_FUNDAMENTADO` sem `fundamentacao_legal` antes de
`ST_EnviarResposta` (`End_ErrFundamentacaoAusente`, `ERR_DSR_FUNDAMENTACAO_AUSENTE`).

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn` + `docs/processes/contracts/SP-OP-LGPD-DSR-001.md` (secoes Invariantes/Codigos de erro atualizadas) | Confirmar que `APROVAR_ENVIO` (send-only, sem execucao) permanece o vocabulario correto para o caminho antes-default (nao renomeado — ja usado por `UT_RevisaoDpo`/testes); confirmar que um `decisao_dsr` invalido/ausente deve virar um INCIDENTE tecnico (nao uma negativa automatica ao titular) — texto/tratamento operacional de `ERR_DSR_DECISION_INVALID` e `ERR_DSR_FUNDAMENTACAO_AUSENTE` a definir (reabertura de UT? escalonamento?) | DPO + juridico | `DRAFT — requires human review before any deploy` |

Nota: este item soma-se (nao substitui) a entrada existente do corpo LGPD-DSR-001 acima
(linha "Fluxos LGPD..."), que ja cobre prazos internos/revogacao/matriz de retencao.

---

## Wave-1 — calendario ANS per-competencia + cron per-report_type (GAP-ANS-1 / GAP-ANS-2)

`fix/w1-ans-calendar`: `ans_calendar` v0.2.0 ganhou `competencia` como segundo input obrigatorio
(due_date/sla_alerta diferem por competencia; rows de fallback conservadoras por tipo; catch-all
global REVISAO_HUMANA preservado). O cron per-`report_type` foi re-modelado: o start de
SP-OP-ANS-SUBMIT-001 virou none (`Start_DespachoEnvio`, variaveis seedadas) e o agendador vive em
SP-OP-ANS-CRON-001 (um process definition por tipo, cada um com seu TimerStartEvent — o CIB Seven
2.1.0 rejeita multiplos timer starts e `camunda:inputOutput` em start events, ENGINE-09005,
verificado empiricamente; cada tick publica o fato `ans.cron_due` em
`operadora.notifications.internal` e o `notifications_bridge` inicia o envio com bk deterministica
`ANSSUB-{tenant}-{report_type}-{competencia}` — event-choreographed ADR-0003, nunca callActivity).

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `spec/processes/dmn/ans_calendar.dmn` (v0.2.0 — input `competencia`) | TODAS as datas due_date/sla_alerta por (report_type, competencia) sao EXEMPLO/SINTETICAS — confirmar contra o calendario ANS vigente (RN 124/209/388/424, DIOPS); rows de fallback por tipo (competencia nao enumerada) usam prazos conservadores SINTETICOS; estrategia de enumeracao de competencias (rows por competencia vs derivacao por worker/scheduler) requer decisao regulatoria | regulatorio-ANS + juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn` + `docs/processes/contracts/SP-OP-ANS-CRON-001.md` | Periodicidades `timeCycle` por report_type (R/P1M / R/P3M / R/P1Y) DRAFT/verify — confirmar cadencia e ancora de data (dia do mes/ano) contra as RN vigentes. Scheduler que computa a competencia: **IMPLEMENTADO** (res-ans-competencia-sentinel, secao dedicada abaixo) — `COMPETENCIA_PENDENTE` so sobrevive fail-closed quando o fato nao carrega a ancora; confirmar aceitabilidade operacional desse fallback residual | regulatorio-ANS | `DRAFT — requires human review before any deploy` |

---

## Wave 1 — batch de gaps s-complexity DMN/boundary (fix/w1-s-dmn-boundaries)
Correcoes de conteudo de regra em DMNs ja DRAFT (`cancel_admissibility.dmn`, `glosa_triage.dmn`) e
plumbing estrutural de BPMN (boundary catches + promocao de variavel). Nenhuma DMN passa a ter
saida adversa nova; ambas continuam DRAFT — as correcoes precisam do MESMO revisor ja mapeado nas
linhas Phase 2 de CANCEL-001/CONTAS-001 acima antes de qualquer deploy.
| `spec/processes/dmn/cancel_admissibility.dmn` (GAP-CANCEL-1) | `vinculo_ativo` promovido a 6o input da tabela; r_pedido_l2 (EFETIVAR_PEDIDO) agora exige vinculo_ativo=true — vinculo inativo cai no catch-all (ANALISE_HUMANA). Confirmar que "vinculo inativo" e de fato causa de bloqueio do direito potestativo do titular (RN 412) e nao apenas um dado de cadastro desatualizado | juridico/contratos + compliance | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/glosa_triage.dmn` (GAP-CONTAS-3) | r_sem_glosa (row 1) agora exige `categoria_normalizada` fora de `{tecnica, clinica}` (antes preemptava r_tecnica_humano sob hitPolicy FIRST). Confirmar que a lista de exclusao (tecnica/clinica) e suficiente — ou se `documental`/`administrativa` tambem deveriam ser excluidas do auto-SEM_GLOSA | auditoria-contas + compliance | `DRAFT — requires human review before any deploy` |

---

## Wave 2 — CANCEL medium batch (fix/w2-cancel-batch)
Mesmo padrao do Wave 1 (linhas acima): correcao de conteudo de regra em DMN ja DRAFT
(`cancel_admissibility.dmn`) e wiring estrutural de uma DMN ja DRAFT porem ate entao ORFA
(`cancel_routing.dmn`, GAP-CANCEL-5 — nao alcancava nenhum businessRuleTask; passa a ser
consumida de fato por `BRT_Classificacao`). Nenhuma DMN passa a ter saida adversa nova; ambas
continuam DRAFT — mesmo revisor ja mapeado na linha Phase 2 de CANCEL-001 acima.
| `spec/processes/dmn/cancel_admissibility.dmn` (GAP-CANCEL-2) | `dentro_prazo` promovido de input decorativo (toda row usava `-`) a gate real de r_pedido_l2 (EFETIVAR_PEDIDO): agora exige `dentro_prazo=true`, ao lado de titularidade_confirmada/vinculo_ativo. Confirmar a semantica regulatoria exata de "dentro do prazo contratual/regulatorio" para o pedido de cancelamento do titular (RN 412) — e se fora do prazo deve mesmo cair em ANALISE_HUMANA (nao um bloqueio distinto) | juridico/contratos + compliance | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/cancel_routing.dmn` (GAP-CANCEL-5) | Antes orfa (nenhum businessRuleTask a consumia, apesar da descricao afirmar "Consumida por SP-OP-CANCEL-001"). Agora wireada via `BRT_Classificacao` (entre `BRT_CancelSla` e `ST_PrepareDossier`) — `natureza_caso`/`grupo_sugerido` alimentam o dossie de analise humana (rotulos neutros, sem saida adversa). Confirmar o allowlist de `natureza_caso`/`grupo_sugerido` e o mapeamento tipo_solicitacao→natureza contra a taxonomia real de triagem juridico/contratos | juridico/contratos + compliance | `DRAFT — requires human review before any deploy` |

---

## Wave-1 — Tetos financeiros deixam de ser decorativos (GAP-XHITL-2, absorve GAP-AUTH-4/GAP-REEMBOLSO-3/GAP-PAGTO-3)

`fix/w1-financial-ceilings`: os tetos de governanca (`authorization_approval.max_value_brl`,
`high_value_payment.threshold_brl`) eram lidos por ZERO Python — `dentro_teto_l2` era ecoado da
variavel de entrada (um upstream que seedasse `true` furava o teto). Agora um resolver
deterministico (`tools/workers/ceilings.py`) carrega a MESMA matriz que o PEP avalia
(`gateway.pep.load_matrix`: L0-core + overlay do tenant) e RECOMPUTA `dentro_teto_l2` nos workers
AUTH (`auth_analyze`), REEMBOLSO (`calculate_amount`) e PAGTO (`calculate_facts`) — o booleano
COMPUTADO alimenta as DMN de auto-aprovacao. Teto 0/ausente/matriz-quebrada = FAIL-CLOSED (False):
com o D-07 em aberto (max_value_brl=0), TODO AUTO_APROVAR cai em analise humana. A escada
faixa->tier de PAGTO saiu do dict Python (`_FAIXA_TIER_MINIMO`) para a DMN `pagto_alcada`
(saida `tier_minimo`, ADR-0012); o tier-match do worker le o fato da DMN (fail-closed no comite).

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `src/maezo/policies/autonomy/L0-core.yaml` (nova acao `reembolso_auto_approval`, L2, `max_value_brl: 0`) + `src/maezo/policies/autonomy/tenants-amh.yaml` (override espelho) | D-07: definir os tetos INICIAIS reais com a diretoria AMH (`authorization_approval.max_value_brl`, `reembolso_auto_approval.max_value_brl` — ambos 0 = todo auto-approval vai a humano ate la) e confirmar `high_value_payment.threshold_brl=100000` (DRAFT); nivel L2 + `requires: dmn_favorable` da nova acao adequados? (nao toca `_hard_frozen.yaml` — nenhuma acao hard alterada) | diretoria AMH + financas + compliance (CODEOWNERS) | `DRAFT — max_value_brl=0 fail-closed ate D-07; requires human sign-off before raising` |
| `spec/processes/dmn/pagto_alcada.dmn` (nova saida `tier_minimo`: DENTRO_TETO_L2->0, L1->1, L2->2, L3->3, catch-all->4) + achatamento em `SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn` (BRT_AlcadaRouting) | Confirmar o mapeamento faixa->tier-minimo-de-aprovador com financas (segregacao de funcoes — mesma revisao pendente da escada de valores DRAFT); a saida e FATO de tier-match, nunca liberacao | financas + compliance | `DRAFT — requires human review before any deploy` |

---

## Wave-2 — NIP MANTER_NEGATIVA autonomy-class parity com AUTH (GAP-NIP-3 / GAP-XHITL-3)

`fix/w2-nip-autonomy-parity`: a matriz de autonomia classificava TODA a familia NIP sob
`nip_response: {level: L1}`, mas `MANTER_NEGATIVA` embute uma negativa de cobertura
(authorization_denial-class) — L0 hard em AUTH, porem rebaixavel-L1 em NIP. **PROMOCAO
`_hard_frozen.yaml`** (endurecimento ADITIVO, nunca enfraquecimento): nova acao dedicada
`nip_manter_negativa` (L0 hard) congelada em `_hard_frozen.yaml` + `pep.HARD_ACTIONS` +
`agent_def_merge.HARD_AUTONOMY_ACTIONS`, em PARIDADE com `authorization_denial`. `nip_response`
permanece L1 (SO `CONCEDER` / `RESPONDER_NAO_ASSISTENCIAL`). A HITL estrutural nao muda (User Task
`UT_RevisaoJuridicaNip` + guard do worker `submit_response` `ERR_NIP_NEGATIVA_NOT_HUMAN`); a
correcao e de CLASSE de autonomia (nao rebaixavel por overlay de tenant — teste prova).

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `src/maezo/policies/autonomy/L0-core.yaml` (nova acao `nip_manter_negativa`, L0 hard) + `src/maezo/policies/autonomy/_hard_frozen.yaml` (novo item congelado) | **Promocao de item `hard`** (mudanca de governanca, cabecalho `_hard_frozen.yaml`): confirmar que a negativa NIP (`MANTER_NEGATIVA`) e de fato authorization_denial-class e deve ser L0 hard nao-rebaixavel (paridade RN 259/395 com AUTH); confirmar que `CONCEDER`/`RESPONDER_NAO_ASSISTENCIAL` corretamente permanecem `nip_response` L1; nenhum item hard existente foi enfraquecido/superseded | arquitetura + compliance (CODEOWNERS `_hard_frozen`) + juridico/regulatorio (RN 259/388/395) | `DRAFT — requires human review (CODEOWNERS) before any deploy` |

---

## Wave-2 — notifications_bridge: report_type/competencia do caminho nip_filing (GAP-ANS-3)

`fix/w2-pagto-prog-ans`: `ans_submit_variables()`/`ans_submit_business_key()` (caminho
`nip.handoff_ans_submit` → SP-OP-ANS-SUBMIT-001) deixavam `report_type`/`competencia`/
`periodicidade` em branco (todos "obrigatoria: sim" no contrato) e a business key
(`ANSSUB-{tenant}-nipfiling-{submit_id}`) nao carregava nenhum dos dois primeiros. Fix: os tres
campos passam a ser sempre setados com CONSTANTES (nunca derivadas do relogio de processamento —
preserva idempotencia da bk na reentrega): `report_type=RN_209_UTILIZACAO`,
`competencia=COMPETENCIA_PENDENTE` (mesma sentinela do caminho cron), `periodicidade=mensal`. A bk
passa a `ANSSUB-{tenant}-{report_type}-{competencia}-nipfiling-{submit_id}` — `submit_id` continua
a ANCORA de unicidade por caso (a calendar-key sozinha colidiria multiplos filings NIP no mesmo
mes).

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `src/maezo/platform/integrations/notifications_bridge/consumer.py` (`_ANS_REPORT_TYPE_NIP_FILING`, `ans_submit_variables`, `ans_submit_business_key`) | Confirmar se `RN_209_UTILIZACAO` ("utilizacao de servicos") e de fato a classificacao regulatoria correta para um filing de resposta formal a NIP, ou se merece um `report_type` proprio (o contrato hoje so declara 5 valores, todos de relatorios PERIODICOS agregados — nenhum modela um ato vinculante POR CASO); confirmar `periodicidade=mensal` como granularidade conservadora adequada para o calendario deste caminho | regulatorio-ANS + juridico | `DRAFT — requires human review before any deploy` |

## Wave-3 — INADIMPLENCIA test-spec + contract reconciliation, ADEQUACAO A2A wiring (GAP-INAD-4/GAP-INAD-5/GAP-ADEQ-4)

`fix/w2-inad-adeq` (T2 finisher batch, coordinated by orchestrator-wave3): three residue gaps
closed against the already-shipped INADIMPLENCIA-001/ADEQUACAO-001 artifacts (no BPMN/DMN
content changed).

- **GAP-INAD-4:** authored the missing `docs/processes/test-specs/SP-OP-INADIMPLENCIA-001.md`
  (the "mandatory quadruple" — contract+BPMN+DMN+test-spec — was broken; the integration suite
  `tests/integration/processes/test_sp_op_inadimplencia_001.py` already existed but had no
  design-level spec accompanying it). Documents every test by name, given/when/then, mirroring
  `SP-OP-CANCEL-001.md`'s test-spec structure; flags one genuine coverage gap (no
  `test_business_key_uma_instancia_por_contrato` analog exists yet for INADIMPLENCIA-001).
- **GAP-INAD-5:** reconciled `docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md`, which still
  framed the CANCEL-001 rescission-ownership question as an unresolved BLOQUEANTE open question
  (DRAFT-A vs DRAFT-B) and documented a `register_contract_rescission`/`RESCINDIR`/
  `Error_ContractRescissionNotHuman` design that was never wired into the shipped BPMN/worker
  (which implement DRAFT-A: `ENCAMINHAR_RESCISAO` → neutral `handoff_rescisao` → CANCEL-001 is the
  sole rescission-terminal owner; `register_contract_rescission` was dropped entirely). Contract
  now states the harmonization is RESOLVED/SHIPPED, matches the real `decisao_inadimplencia` enum,
  and removes the dead error/topic entries. Regulatory content (RN 593 prazos/dias-uteis,
  RN 412 consolidation, candidate-group taxonomy) remains genuinely open and DRAFT.
- **GAP-ADEQ-4:** `operadora.adequacao.prepare_remediation_dossier` now delegates
  `analytics.population` to Andre via a DI-optional `DelegationDispatcher` (mirrors GAP-INAD-6);
  `andre/delegation.py` gained `_flow_for(envelope)`, which disambiguates the task_type shared with
  SP-OP-PAGTO-001 by `envelope.origin` (`origin=adequacao` → `adequacao_dossier` flow) — closing the
  gap between `graph.py`'s docstring (which already claimed this behavior) and the code.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md` (v0.2.0 — GAP-INAD-5 reconciliation) | Confirmar que a reconciliacao textual (DRAFT-A shipped, `decisao_inadimplencia` enum, topicos/erros dropped) reflete fielmente o BPMN/worker; conteudo regulatorio RN 593 (prazos/dias-uteis/consolidacao RN 412) e candidate groups permanecem DRAFT/verify — nao alterados por este batch | médico auditor + jurídico (reconciliacao textual); regulatório (conteudo RN 593, ja pendente) | `DRAFT — requires human review (médico auditor / jurídico) before any deploy` |
| `docs/processes/test-specs/SP-OP-INADIMPLENCIA-001.md` (novo — GAP-INAD-4) | Confirmar que a descricao given/when/then de cada teste corresponde ao comportamento regulatorio pretendido (nenhum conteudo clinico/regulatorio novo — so descreve testes ja implementados) | médico auditor + jurídico | `DRAFT — requires human review before any deploy` |

---

## Wave-3 — SP-OP-LGPD-DSR-001: drift-guard sla_resposta + boundary catch Error_LgpdIdentidade (GAP-LGPD-5/GAP-LGPD-6)

`fix/w3-lgpd` (T2 builder, coordinated by orchestrator-wave3). Duas correcoes independentes no
mesmo corpo LGPD-DSR-001, ambas ja cobertas pelas linhas gerais de artefato acima (Fluxos LGPD /
`lgpd_dsr_routing.dmn`) — esta secao documenta a DECISAO DE ENGENHARIA especifica de cada gap.

- **GAP-LGPD-5 (`sla_resposta` da DMN nunca consumido; drift risk vs literal `P15D` do BPMN):**
  escolhida a alternativa SANCIONADA (drift-guard mecanico), NAO o wiring do timer. Racional: o
  timer `Start_SlaGlobal` e o START event de um EVENT SUBPROCESS de escopo-RAIZ (`ESP_SlaGlobal`,
  `triggeredByEvent="true"`) — sua assinatura (job) e resolvida NA INSTANCIACAO do processo, antes
  de QUALQUER no do fluxo principal correr, inclusive `BRT_RotearDsr` (que so avalia a DMN DEPOIS
  da verificacao de identidade, podendo aguardar ate P10D por prova adicional). `roteamento_dsr`
  NUNCA existe no instante em que o timer seria assinado, em NENHUM caminho — diferente de
  `BT_AlertaDpo` (boundary event JA downstream de `BRT_RotearDsr`, onde
  `roteamento_dsr.sla_alerta` resolve sem problema). Alem do bloqueio estrutural, o contrato ja
  declarava a ancora como INTENCIONALMENTE do inicio da instancia ("SLA legal de 15 dias conta do
  INICIO da instancia ... nao da tarefa", LGPD art. 19 II) — amarrar o timer global a uma saida de
  roteamento por-tipo seria substituir uma ancora legal fixa por uma dependente de fluxo, errado
  por design alem de por engine. Adicionado `tests/architecture/test_lgpd_sla_dmn_bpmn_parity.py` (pendente port — T3.x)
  (engine-free, lane rapido): fica VERMELHO se qualquer regra da DMN divergir do literal fixo do
  BPMN, ou vice-versa. Nota espelhada na `<description>` de `lgpd_dsr_routing.dmn`.
- **GAP-LGPD-6 (`Error_LgpdIdentidade` declarado, sem boundary catch — dangling-catch, classe
  provada perigosa em #136/GAP-RECURSO-3):** escolhida a opcao (a) — boundary catch adicionado
  AGORA, nao deferido. `BE_IdentidadeInverificavel` (boundary em `ST_VerificarIdentidade`, o
  PRIMEIRO touchpoint da mainline) captura `Error_LgpdIdentidade`
  (`ERR_DSR_IDENTITY_UNVERIFIED`, lancado por `lgpd.py` quando `titular_pseudo_id` chega
  ausente/vazio — impossibilidade MECANICA de verificar, NUNCA acusacao automatica de fraude, L0
  hard `fraud_accusation`). Terminal NEUTRO (`End_IdentidadeInverificavel`, fail-safe, nao
  adverso) — espelha `End_RecursoGlosaInvalidaOrigem`/`End_CredPrestadorInvalido`/
  `End_ReembolsoProtocoloInvalido`, mas e MAIS conservador que esse precedente: publica
  `lgpd_dsr.completed` (desfecho=`identidade_inverificavel`) ANTES do fim, dando visibilidade a
  DPO/juridico-privacidade (que ja acompanham este topico) para revisao manual — nunca um drop
  silencioso, nunca prosa de negativa automatica. O tratamento NUANCED (distinguir impossibilidade
  mecanica de fraude evidente de identidade) permanece pendente para a promocao a FINAL, como o
  contrato ja registrava.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn` (`BE_IdentidadeInverificavel`/`ST_PublishIdentidadeInverificavel`/`End_IdentidadeInverificavel`, GAP-LGPD-6) + `docs/processes/contracts/SP-OP-LGPD-DSR-001.md` (novo desfecho `identidade_inverificavel`) | Confirmar que publicar `lgpd_dsr.completed` (desfecho=identidade_inverificavel) sem nenhuma acao ATIVA adicional (ex.: alerta dedicado a DPO, distinto do canal generico de eventos) e visibilidade suficiente para este caso — ou se a promocao a FINAL deve adicionar uma notificacao ativa (mesmo padrao de `ST_NotificarJuridicoBreach`) | DPO + juridico | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/lgpd_dsr_routing.dmn` (nota GAP-LGPD-5 na `<description>`) + `tests/architecture/test_lgpd_sla_dmn_bpmn_parity.py` (pendente port — T3.x) | Confirmar que a decisao de NAO diferenciar `sla_resposta` por `tipo_requisicao`/`fluxo` (todas as regras usam o mesmo P15D global) e a intencao regulatoria correta — se algum tipo de requisicao um dia precisar de um prazo de resposta PROPRIO (diferente do teto legal global de 15 dias), o wiring do timer precisa ser revisitado (ver docstring do teste), nao apenas o valor da regra | DPO + juridico | `DRAFT — requires human review before any deploy` |

---

## Redacao de payload estruturado no webhook WhatsApp — decisao de geolocalizacao (res-xphi-raw-dict-payloads)

`fix/res-xphi-dict-payloads` (T2 builder, coordinated by orchestrator-wave3): fecha a lacuna
achada pela verificacao T3 de #133 (GAP-XPHI-3) — `interactive_payload`/`location_payload`
publicados como dicts CRUS no Kafka; `redact_free_text` so cobre strings soltas.
`gateway.pseudonymizer.redact_payload` agora percorre dict/list recursivamente: strings soltas
passam pela mesma pass de padrao (CPF/CNS/telefone/nome); campos PHI-por-chave (endereco/nome/
cpf/cns/telefone/email/cep/data_nascimento — ve `_PAYLOAD_FORCE_KEYS`) sao tokenizados ONE-WAY
por INTEIRO mesmo sem match de padrao; `nfm_reply.response_json` de um WhatsApp Flow (formulario
de cadastro como JSON-string embutida) e' parseado e redigido recursivamente.

**Decisao que precisa de confirmacao do DPO:** `location.latitude`/`longitude` sao arredondados
para 2 casas decimais (~1.1km de grade) em vez de tokenizados/derrubados. Justificativa registrada
no codigo (`gateway/pseudonymizer.py::_GEO_COORDINATE_KEYS`): uma coordenada precisa e' quase-
identificador LGPD Art. 5 (revela onde o beneficiario mora/busca atendimento), mas NENHUM
consumidor atual le esses campos (grep confirmado — nada em `agents/`/`tools/workers/` consome
`location_payload`), entao nao ha caso de uso comprovado que exija precisao total nem motivo para
derrubar o campo por completo. Arredondamento e' o meio-termo conservador: preserva utilidade
geografica grosseira futura (ex.: localizar rede credenciada mais proxima) sem expor o pino
residencial exato. Este arredondamento JA ESTA em producao (comportamento padrao, fail-closed —
nao ha modo de desativa-lo no codigo); a decisao DRAFT e' se/quando um consumidor futuro precisar
de precisao total, o que exigiria um mecanismo de consentimento explicito NOVO (fora do escopo
deste PR).

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `src/maezo/gateway/pseudonymizer.py::_GEO_COORDINATE_KEYS` (arredondamento de lat/long a 2 casas) | Confirmar que 2 casas decimais (~1.1km) e' a granularidade correta de minimizacao para LGPD Art. 5 quase-identificador geografico neste contexto (saude); confirmar que arredondar (em vez de tokenizar/derrubar) e' a escolha certa dado que nao ha consumidor hoje; definir o mecanismo de consentimento caso um consumidor futuro precise de precisao total | DPO + jurídico (LGPD) | `DRAFT — requires DPO confirmation; rounding is the safe default already in production` |

## Wave-1 residue (`res-ans-competencia-sentinel`) — competencia REAL computada de ancoras de evento

`fix/res-ans-competencia`: fecha o gap deixado pelo Wave-1 `ans_calendar` (GAP-ANS-1/GAP-ANS-2) e
pelo Wave-2 `notifications_bridge` (GAP-ANS-3) — `ans_calendar.dmn` CONSOME `competencia`
(`report_type`, `competencia` -> `due_date`/`sla_alerta`) mas ate aqui NADA a computava; os dois
caminhos que iniciam SP-OP-ANS-SUBMIT-001 (cron e `nip.handoff_ans_submit`) so publicavam a
sentinela fixa `COMPETENCIA_PENDENTE`. `notifications_bridge/consumer.py::_competencia_from_anchor`
(primitivo puro, compartilhado pelos dois caminhos) deriva `competencia` (`YYYY-MM` | `YYYY-Qn`) de
uma data-ANCORA que agora viaja no fato Kafka — NUNCA do relogio de processamento da ponte (replay/
reentrega do MESMO fato sempre le a MESMA ancora, preservando a idempotencia da business key
`ANSSUB-{tenant}-{report_type}-{competencia}[-nipfiling-{submit_id}]`):

- **Caminho cron:** `tools/workers/phase0.py::make_publish_event_handler` grava
  `ans_cron_reference_date_iso` no payload de `ans.cron_due` sempre que `event_type == ans.cron_due`
  — a data (UTC) do instante em que o serviceTask de publish executa (o tick do TimerStartEvent
  acabou de disparar). Nenhum topico/worker/serviceTask novo: o worker generico ja reusado por todo
  SP-OP grava a ancora; o agendador continua "puro" (1 serviceTask por definition).
- **Caminho nip_filing:** `tools/workers/nip.py::make_handoff_ans_submit_handler` passa a ecoar
  `data_recebimento_nip_iso` no payload de `nip.handoff_ans_submit` — a MESMA ancora regulatoria de
  prazo (GAP-NIP-1) ja normalizada e fixada por caso NIP em `operadora.nip.instruct_dossier`, bem
  antes do handoff.
- **Fail-closed preservado:** sem a ancora (fato antigo/legado publicado antes desta correcao),
  `_competencia_from_anchor` retorna `""` e o chamador aplica a sentinela `COMPETENCIA_PENDENTE`
  (nunca adivinha a partir de `now()`). Um `competencia` explicito no fato (scheduler futuro/
  override, ou humano resolvendo em `UT_CorrigirPendenciaEnvio`) sempre tem precedencia sobre o
  valor computado — inalterado.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `src/maezo/platform/integrations/notifications_bridge/consumer.py` (`_competencia_from_anchor`, `_ans_cron_competencia`, `_nip_filing_competencia`) | Mapeamento ASSUMIDO (nao confirmado): `competencia` = o periodo de calendario (mes ou trimestre, por `periodicidade`) IMEDIATAMENTE ANTERIOR ao mes da data-ancora — o padrao usual de relatorio periodico regulatorio (reporta-se em N o periodo fechado em N-1). Confirmar contra o texto vigente ANS por `report_type` (RN 124/209/388/424, DIOPS) se este e de fato o corte correto (vs. competencia = mes/trimestre DA PROPRIA ancora) | regulatorio-ANS + juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `src/maezo/tools/workers/phase0.py` (`make_publish_event_handler` — stamp de `ans_cron_reference_date_iso` quando `event_type == ans.cron_due`) | Confirmar que capturar a data no instante do serviceTask de publish (imediatamente apos o TimerStartEvent disparar) e a ancora regulatoriamente correta do ciclo — vs. uma data de referencia diferente (ex.: dia fixo do mes) que um scheduler futuro possa precisar carregar explicitamente | regulatorio-ANS | `DRAFT — requires human review before any deploy` |

---

## Wave-3 — docs contract reconciliation batch (GAP-CONTAS-6/GAP-FRAUDE-6/GAP-INAD-7/GAP-RECURSO-4/GAP-NIP-5)

`fix/w3-docs-contracts` (T2 builder, coordinated by orchestrator-wave3): doc-only batch, zero
BPMN/DMN/worker edits. Closes five contract-text gaps identified in the business-logic audit
(§4.2): three are hitPolicy text drift (contract prose said `UNIQUE`, the shipped DMN correctly
implements `FIRST`), one is a missing output-var declaration, and one is a contract-vs-BPMN
routing-prose mismatch that turned out to require the CONTRACT to change (not the BPMN).

- **GAP-CONTAS-6:** `SP-OP-CONTAS-001.md` — `glosa_reason_normalization` and `glosa_classification`
  headers corrected from `hitPolicy UNIQUE` to `hitPolicy FIRST`, matching the shipped
  `glosa_reason_normalization.dmn`/`glosa_classification.dmn` (both `hitPolicy="FIRST"`;
  `glosa_reason_normalization.dmn` already carried an inline NOTA flagging this exact drift).
  `glosa_triage` was already reconciled (no edit needed).
- **GAP-FRAUDE-6:** `SP-OP-FRAUDE-001.md` — added `sla_diligencia: string (ISO 8601)` to the
  `fraude_sla` out-list; `fraude_sla.dmn` emits it and the contract's own SLA table (Diligencia
  row) already referenced `${fraude_sla.sla_diligencia}` — the out-list was just missing the
  declaration.
- **GAP-INAD-7:** `SP-OP-INADIMPLENCIA-001.md` — `inadimplencia_purga` and `inadimplencia_sla`
  headers corrected from `hitPolicy UNIQUE` to `hitPolicy FIRST`, matching the shipped DMNs (both
  `hitPolicy="FIRST"`, each with an inline NOTA already flagging the drift).
  RN 593 content (purga/notification windows, dias uteis vs corridos) remains DRAFT/verify —
  unchanged by this batch.
- **GAP-RECURSO-4:** `SP-OP-RECURSO-001.md` — declared `resposta_operadora` in the output-vars
  table (was driving `GW_RecursoResolvido`, bpmn:439, without ever being declared); documented as
  delivered via `Msg_RecursoRespostaRecebida`/`ICE_RespostaRecebida`. Plain doc-add, no regulatory
  content changed.
- **GAP-NIP-5 (rescoped):** `SP-OP-NIP-001.md` — the plan's original direction was "fix the BPMN
  to route the info-wait timeout back to elaboracao/coordenacao per the contract." Verification
  against the merged #137 finding inverted that: the BPMN's unconditional `ICE_PrazoInfo` →
  `UT_RevisaoJuridicaNip` route is the CORRECT behavior (preserves the L0 "revisao sempre juridica"
  invariant — `UT_RevisaoJuridicaNip`'s two non-`GW_Roteamento` incoming flows can't be routed
  dynamically off the flat `roteamento`/`grupo_humano` vars without risking a non-juridico group on
  the sole `MANTER_NEGATIVA` gate). The SLA table row and a new inline Nota were rewritten to state
  the incondicional route and cite the L0 rationale; the BPMN was NOT touched.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `docs/processes/contracts/SP-OP-CONTAS-001.md` (GAP-CONTAS-6 — hitPolicy text fix) | Confirmar que a correcao textual (`UNIQUE`→`FIRST` em `glosa_reason_normalization`/`glosa_classification`) nao mascara uma divergencia de conteudo mais profunda; conteudo regulatorio de glosa (RN 305/424, tabela TISS de motivos) permanece DRAFT/verify — nao alterado por este batch | medico-auditor/auditoria-contas + compliance + juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md` (GAP-INAD-7 — hitPolicy text fix) | Confirmar que a correcao textual (`UNIQUE`→`FIRST` em `inadimplencia_purga`/`inadimplencia_sla`) nao mascara uma divergencia de conteudo mais profunda; conteudo regulatorio RN 593 (janela de purga/cura, notificacao previa, periodo minimo) permanece DRAFT/verify — nao alterado por este batch | médico auditor + jurídico (reconciliacao textual); regulatório (conteudo RN 593, ja pendente) | `DRAFT — requires human review (médico auditor / jurídico) before any deploy` |
| `docs/processes/contracts/SP-OP-NIP-001.md` (GAP-NIP-5 — rescoped: contrato corrigido p/ bater com o BPMN) | Confirmar que a rota incondicional `ICE_PrazoInfo` → `UT_RevisaoJuridicaNip` (nunca elaboracao/coordenacao) e de fato a postura regulatoriamente correta para RN 388 (o texto formal da NIP so pode ser fechado por revisao juridica apos qualquer estouro de prazo de info, mesmo quando a rodada anterior foi so `regulatorio-ans`/`nucleo-ans`) — este batch so alinhou a prosa do contrato ao comportamento ja shippado do BPMN (nenhum BPMN/DMN alterado) | juridico/regulatorio + regulatorio-ANS | `DRAFT — requires human review before any deploy` |


