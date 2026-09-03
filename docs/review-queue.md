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
| `spec/processes/dmn/auth_auto_approval.dmn` (v0.2.0 — reescrita pelo portao de criterios) | A tabela nao decide mais sobre fatos semeados: seus inputs sao os quatro criterios COMPUTADOS por `operadora.auth.validate_auto_criteria` + `auto_criteria_verificado`. O que resta para revisao e a **politica de COMBINACAO**: (a) exigir os quatro criterios e' a conjuncao correta? (b) `carater_atendimento` e' don't-care em r1 — uma urgencia auto-aprova sob os mesmos fatos de um eletivo; quais criterios uma urgencia/emergencia pode dispensar, dados os prazos da RN 259? | medico auditor + compliance | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/auth_criteria_contratual.dmn` (NOVA — costura VAZIA) | Nenhuma regra contratual/milestone/KPI foi inventada: a tabela tem UMA regra catch-all devolvendo `false` / `SEM_REGRA_RATIFICADA`. Precisa que um SME popule as regras reais de plano/produto/contrato/KPI (e declare as variaveis que elas exigem no contrato do processo) antes de ser ratificada. Pergunta aberta: os criterios de KPI sao a nivel de prestador, de plano, ou ambos? | juridico/contratos + financas + medico auditor | `DRAFT — requires human review (SME contratual) before any deploy` |
| `spec/processes/dmn/auth-criteria-ratification.yaml` (NOVO — manifesto de ratificacao) | **O portao de seguranca do GAP-AUTH-4.** Declara quais fontes de regra um humano ratificou; enquanto uma fonte esta `ratificado: false`, o criterio dela e' FALSE com `*_FONTE_NAO_RATIFICADA` **independentemente do que a tabela computa** (modo SOMBRA registra o que ela TERIA decidido, como evidencia para esta revisao). Ratificar exige os tres campos (`ratificado: true` + `revisor` + `ratificado_em`) e e' mudanca de DADOS — nenhuma linha de codigo muda. Contem tambem o `mapeamento_dut_criteria` (dut_ref -> tabela clinica), DELIBERADAMENTE INCOMPLETO: so as duas correspondencias inequivocas foram declaradas; as terapias/oncologia/OPME restantes exigem julgamento clinico e resolvem `TECNICO_PROCEDIMENTO_NAO_MAPEADO` ate um SME as declarar | medico auditor + juridico/regulatorio + financas (por fonte, ver o proprio arquivo) | `DRAFT — nada ratificado` |
| `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn` — `ST_ValidateAutoApprovalCriteria` + `BRT_AutoApproval` (**GAP-AUTH-4 — fechado ESTRUTURALMENTE 2026-08-06; residuo de DADOS aberto**) | **Era:** na rota automatica, `dut_atendida`/`dentro_teto_l2`/`rede_credenciada` chegavam SEMEADOS no payload de start e a DMN decidia `AUTO_APROVAR` sobre eles sem verificacao; `CeilingResolver` nao rodava antes do `GW_AutoAprovacao` (unico chamador em AUTH = `AnalyzeRequestWorker`, na perna humana *depois* do gateway), logo o teto era DECORATIVO. A patologia raiz: ausencia de validacao lia-se como PASS implicito. **Agora:** o worker DETERMINISTICO `operadora.auth.validate_auto_criteria` roda entre `BRT_SlaAnalise` e `BRT_AutoApproval` (precedente GAP-REEMBOLSO-5) e COMPUTA quatro criterios — tecnico (`dut_rol_coverage` + `dut_criteria_*`), financeiro (`within_l2_ceiling`), regulatorio (`carencia_check`), contratual (`auth_criteria_contratual`) — mais `auto_criteria_verificado`, que a regra r1 da DMN v0.2.0 EXIGE: pular o validador cai no catch-all -> `ANALISE_HUMANA`. Os quatro criterios sao reescritos em TODO caminho, entao nenhum homonimo semeado sobrevive. Evidencia per-criterio em `auto_criteria_falhas` (historico do engine) e `motivo_bloqueio_criterios` + os 4 booleanos na cadeia duravel ADR-0007. **PORTAO DE RATIFICACAO:** as tabelas clinicas sao SINTETICAS — um criterio so contribui com PASS se a fonte estiver ratificada no manifesto; fonte DRAFT devolve `false` com `*_FONTE_NAO_RATIFICADA` INDEPENDENTE do que a tabela computou, e o modo SOMBRA registra o que ela teria decidido. **DESFECHO HOJE, sem exagero:** com teto 0 (D-07) e nada ratificado, os quatro criterios sao false — NADA auto-aprova, todo pedido vai para analise humana; o MESMO desfecho seguro de antes, agora por quatro motivos explicitos e auditaveis. Cada criterio ativa sozinho conforme sua fonte for ratificada, sem mudanca de codigo. **RESIDUO ABERTO (dado/escopo, nao estrutura):** (1) ratificar as 5 tabelas + popular a contratual; (2) D-07 (`max_value_brl: 0`); (3) `rede_credenciada` NAO e coberto por nenhum criterio — nao existe fonte de rede no repo e inventar uma seria fabricar um fato (fail-closed: ele so deixou de participar da decisao automatica); (4) os inputs de `carencia_check` (`tipo_procedimento`/`dias_desde_adesao`/`cpt_declarada`) nao existem no contrato de start — dado cadastral da fronteira AMH (MZO-050b, bloqueado) + mapeamento regulatorio pendente de SME, entao o criterio regulatorio falha FECHADO com `REGULATORIO_ENTRADA_AUSENTE` hoje; (5) os seeds de admissibilidade (`requer_autorizacao`/`beneficiario_ativo`/`documentacao_completa`) seguem nao verificados a montante; (6) `carater_atendimento` e don't-care em r1 — uma urgencia auto-aprova sob os mesmos fatos de um eletivo (input mantido de proposito para o SME diferenciar). **A promocao de SP-OP-AUTH-001 de DRAFT para FINAL segue vinculada a (1) e (2).** Mantida em defesa-em-profundidade a verificacao de teto no ponto de emissao de `issue_authorization` (canal AUTOMATICO apenas; o canal humano nunca e gateado por teto). Findings 4/5 do GK-CEILING registrados anteriormente permanecem: sob recusa de emissao a rota ainda publica `desfecho=aprovada_automatica` sem numero — hoje inalcancavel (nada chega la), mas nao removido; e `AnalyzeRequestWorker` continua divergindo no MODO de falha da derivacao de centavos (`OverflowError` nao tratado em inf). | medico auditor + ANS/regulatorio + seguranca + juridico/contratos + financas (D-07) | `DRAFT — structural gap CLOSED; ratification+D-07 pending, blocks FINAL promotion` |
| `spec/processes/dmn/auth_sla.dmn` | TODOS os prazos (dias uteis vs corridos; RN 259/395 vigentes; urgencia operacionalizada em 2h) | juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn` + `docs/processes/contracts/SP-OP-LGPD-DSR-001.md` | Fluxos LGPD (prazos internos de prova de identidade; revogacao de consentimento; matriz de retencao legal) | DPO + juridico | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/lgpd_dsr_routing.dmn` | Roteamento e prazos por tipo de requisicao (art. 18/19) | DPO + juridico | `DRAFT — requires human review before any deploy` |
| `spec/policies/retention/erasure-plan.template.yaml` (NOVO — plano de eliminacao POR CAMADA; pacote de revisao em `docs/reviews/adr-0029-erasure-packet.md`) | **O portao de DADOS da ADR-0029.** A ESTRUTURA esta escrita e e fato de schema: 15 relacoes persistentes enumeradas das migracoes 0001-0007 (mais as 4 do checkpointer LangGraph, que NENHUMA migracao cria — `0006:14-23`,`:25-35`), cada uma com sua citacao, como as linhas do titular seriam identificadas, a ordem logica de operacoes e a mecanica de eliminacao/anonimizacao parametrizada. A DECISAO esta vazia: `decisao_dpo`/`base_legal`/`retencao` sao placeholders `PENDENTE` nas 15 linhas, e o carregador recusa qualquer valor que ainda pareca placeholder. **Tres achados que o revisor precisa ver antes de decidir:** (1) so DUAS colunas em todo o encadeamento identificam um titular (`agent_memory.fhir_patient_id`, `0001:69`, NULLABLE; `erasure_log.fhir_patient_id`, `0004:67`) e NENHUMA das duas pontes de identidade existe (`titular_pseudo_id`->`fhir_patient_id`, `lgpd.py:293-294`; `thread_id`->`fhir_patient_id`, `erasure.py:196-206`) — sao lacunas de ENGENHARIA, nao de DPO, e ratificar nao as fecha; (2) em `audit_chain` anonimizar quebra a cadeia EXATAMENTE como eliminar, porque `decision_basis` esta no preimage de `record_hash` (`audit.py:266-284`), e o mecanismo da ADR-0029 admite so prefixo a partir do genesis (`ADR-0029:64-70`, que proibe buraco no meio em termos) — as linhas de um titular nao formam prefixo, entao `ELIMINAR` em `audit_chain` e hoje INEXEQUIVEL pelo mecanismo ratificado (decisao D-1 do pacote); (3) reter linhas de idempotencia/inbox PROTEGE a eliminacao (uma re-entrega re-materializaria o dado apagado), invertendo a intuicao de que eliminar mais e sempre mais seguro. Ratificar e mudanca de DADOS — nenhuma linha de codigo muda — e NAO liga nada: o modo nao-dry continua recusado, com razao `execution_mechanism_absent` em vez de `plan_unratified` (`ErasureManager.erase()` levanta `ErasureNotImplementedError`, `erasure.py:152`). O unico executavel entregue e um dry-run inerte que so emite `SELECT count(*)` | DPO + juridico-privacidade | `DRAFT — nada ratificado; 15 decisoes PENDENTE` |

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

**Consumidor — CORRIGIDO 2026-08-06 (GAP-AUTH-3 FECHADO).** O paragrafo anterior desta secao
afirmava que as 5 tabelas eram avaliadas pelo grafo do Rafael via `mcp-dmn.evaluate`. **Isso era
FALSO.** O grafo do Rafael referencia apenas `auth_admissibility` / `auth_sla` /
`auth_auto_approval` (`src/maezo/agents/rafael/graph.py:91-93`), e
`grep -rn 'dut_rol_coverage|dut_criteria|carencia_check' src/` retornava ZERO ocorrencias — as 5
tabelas nao tinham consumidor nenhum. Essa discrepancia E' o GAP-AUTH-3
(`docs/reports/business-logic-audit-improvement-plan.md:396`, severidade `high`).

**Consumidor REAL (desde 2026-08-06):** o worker DETERMINISTICO
`operadora.auth.validate_auto_criteria` (`ST_ValidateAutoApprovalCriteria`, entre `BRT_SlaAnalise`
e `BRT_AutoApproval`) avalia as 5 tabelas engine-side pela costura `dmn=` de worker (ADR-0028) —
nao por `businessRuleTask`, e nao por um agente. Elas continuam listadas em
`orphans-allowlist.yaml` porque `crossref.py` define orfa como "sem `decisionRef` de
`businessRuleTask`": sao orfas PERMANENTES por design, como as 7 tabelas de `fraude_scoring`.

**Estar wired NAO as torna deployaveis.** O portao de ratificacao
(`spec/processes/dmn/auth-criteria-ratification.yaml`) mantem cada uma inerte enquanto DRAFT: o
criterio correspondente e' `false` com `*_FONTE_NAO_RATIFICADA` **independentemente do que a
tabela computa**. O que a wiring habilita e' o modo SOMBRA — o validador registra em
`auto_criteria_shadow` o que cada tabela TERIA decidido, para que a revisao abaixo aconteca contra
dados de desfecho reais em vez de regras no abstrato. Nenhuma produz negativa (catch-all fail-safe
-> analise humana). A selecao da `dut_criteria_*` por `dut_ref` e' plumbing declarado no manifesto
(por SME, nao adivinhado em codigo); a REGRA fica na DMN.

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
| `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md` (:59) + `workers/ans_submit.py` (`make_assemble_handler`) + BPMN `ST_AssembleDataset`/`UT_CorrigirPendenciaEnvio` (GAP-ANS-5) | LGPD: `lgpd_anonimizado` era documentado como "pre-resolvido por worker" mas nenhum worker o calculava/ecoava — so `notification_bridge.py` (`_ans_submit_variables_from_nip_handoff`/`_ans_submit_variables_from_cron_due` — nao um modulo `notifications_bridge/consumer.py`, que nao existe em `src/`) o seedava, sempre `false` fail-closed, sem caminho de correcao documentado (UT_CorrigirPendenciaEnvio so mencionava `dataset_complete`/`schema_valid`). Fix: `regulatorio.anssubmit.assemble` agora ecoa `lgpd_anonimizado` (mesmo padrao non-computing de `dataset_complete` — NAO e um calculo real de anonimizacao; `mcp-regdata`, a fonte de um atestado real, esta AWS-blocked issue #16) e a UT documenta que o humano so marca `lgpd_anonimizado=true` apos confirmar que o dataset e de fato agregado/anonimizado. Confirmar que este fail-closed + correcao-humana-explicita e a postura LGPD aceitavel para o envio periodico ANS (vs. um atestado automatico real quando `mcp-regdata` for desbloqueado) | regulatorio-ANS + DPO/compliance (ADR-0006) | `DRAFT — requires human review before any deploy` |
| `SP-OP-CANCEL-001.md` + test-spec (+ cancel_*.dmn futuras) | RN 593 (cancelamento/rescisao/suspensao por inadimplencia); prazo de notificacao previa; hipoteses de rescisao unilateral por tipo_plano (Lei 9.656 art.13); contract_termination L0 hard | juridico/contratos + compliance | `DRAFT — requires human review before any deploy` |
| `SP-OP-REEMBOLSO-001.md` + test-spec (+ reembolso_*.dmn futuras) | RN 259 / Lei 9.656 art.12 / RN reembolso vigente; todos os prazos (analise ~30d, urgencia, pendencia, pagamento) + dias-uteis→ISO; tabela de referencia/multiplo + teto auto-aprovacao L2 | medico-auditor + analise-reembolso + atuarial + juridico | `DRAFT — requires human review before any deploy` |
| `src/maezo/domain/glosa.py` (PR #29) — GlosaReasonCode (26) + REASON_CODE_TO_TYPE + is_appealable — *(modulo nao existe em `src/` nesta checkout — citacao de design/PR historico; `find src -path "*domain/glosa*"` e `grep -rn "GlosaReasonCode" src/` = zero hits)* | Validar cada codigo e mapeamento contra a tabela oficial de motivos de glosa TISS/ANS vigente; confirmar conjunto recorrivel antes de WA.4 usar `is_appealable` em producao | equipe-faturamento + juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `src/maezo/integrations/cnab/parser.py` (PR #29) — offsets CNAB 240/400 — *(modulo nao existe em `src/` nesta checkout — citacao de design/PR historico; `find src -path "*cnab*"` = zero hits)* | Validar offsets de campo contra arquivos de retorno reais do banco contratado (variantes por banco); cross-check com o `cnab_parser.py` do reference | WC.3/Lucas + operacoes financeiras | `DRAFT — requires bank-file validation before production` |

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
| `docs/processes/contracts/SP-OP-ADEQUACAO-001.md` (+ `adequacao_*.dmn`/`adequacao.py` futuros) | Thresholds tempo/distancia RN 259 por `tipo_carater` + RN 566 (dimensionamento) + periodicidade do `ciclo_avaliacao`; fato `agents.events.cred.network_changed` (STUB — harmonizar com CRED-001 em W-E); candidate groups `gestao-rede`/`coordenacao-rede`; politica financeira do compromisso de fallback (interacao PAGTO/REEMBOLSO); taxonomia especialidade/geografia. **`spec/processes/dmn/adequacao_gap.dmn` — ordem de regras `hitPolicy="FIRST"` (ADR-0028 §7: DMN wins, nao patchear aqui): `r_eletivo_leve` (linhas 90-99: `tipo_carater="eletivo"`, `tempo_acesso_apurado_min<=60`, `distancia_apurada_km<=50.0`, `prestadores_disponiveis>0`, `cobertura_geo_suficiente=true` -> `GAP_LEVE`) precede `r_conforme` (linhas 110-119: SEM gate de tempo/distancia — wildcard `-` nas 3 primeiras colunas — so exige `prestadores_disponiveis>0` e `cobertura_geo_suficiente=true` -> `CONFORME`) na ordem de avaliacao (linha 32). CONSEQUENCIA CONTRA-INTUITIVA CONFIRMADA (nao e hipotese — live-verified e ja pinada nos testes unitarios): para `tipo_carater="eletivo"`, um acesso MELHOR (dentro do threshold — `tempo<=60min`/`distancia<=50km`) sempre casa com `r_eletivo_leve` primeiro e le `GAP_LEVE`; um acesso PIOR (tempo ou distancia ACIMA desse mesmo threshold) nunca casa com `r_eletivo_leve` e cai, sem nenhum teto, na linha `r_conforme` (que nao tem gate algum de tempo/distancia) -> `CONFORME`. Par minimo que isola o efeito (`distancia`/`prestadores`/`cobertura` identicos, so `tempo` varia): `tests/unit/tools/workers/test_adequacao.py::test_adequacao_gap_leve` (`tempo=45min` -> `GAP_LEVE`) vs `::test_adequacao_gap_conforme_reachable_beyond_leve_gate` (`tempo=70min`, mesma `distancia=10.0`/`prestadores=2`/`cobertura=true` -> `CONFORME`) — o proprio docstring desse segundo teste ja registra "reads as MORE severe... yet is labeled CONFORME". Nota adicional (leitura direta da tabela, sem extrapolar): como `r_conforme` nao tem NENHUM teto superior de tempo/distancia, isto vale para qualquer `tempo_acesso_apurado_min`/`distancia_apurada_km`, por maior que seja, sempre que `prestadores_disponiveis>0` e `cobertura_geo_suficiente=true` — nao ha uma regra `GAP_CRITICO` especifica para `tipo_carater="eletivo"` com tempo/distancia excessivos (as duas regras `*_critico` por tempo/distancia, linhas 50-69, gateiam exclusivamente em `tipo_carater="urgencia_emergencia"`). Nao mexido (fora de escopo, MZO-050b/regulatorio): registrado aqui para o portao regulatorio decidir se a ordem/gate de `r_conforme` precisa de um teto de tempo/distancia tambem para `eletivo` antes de promocao a FINAL. Ratificar agora TAMBEM exige que o `tabela_viva.sha256` do manifesto `adequacao-gap-shadow-candidate.yaml` ainda bata com os bytes da tabela viva no disco (#221) — uma edicao da tabela invalida uma ratificacao pendente/concedida ate re-revisao.** | regulatório (RN 259 thresholds + RN 566 + periodicidade + ordem/gate de `r_conforme` vs `r_eletivo_leve` no `adequacao_gap.dmn`); arquitetura (harmonizacao STUB network_changed); PO/IdP (candidate groups); finanças (politica de fallback) | `DRAFT — requires human review before any deploy` |
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
| `spec/processes/dmn/{upcoding_complexity_ceiling,unbundling_partial_bundles,phantom_no_diagnosis,phantom_suspicious_prefix,frequency_zscore_threshold,provider_peer_deviation,risk_thresholds}.dmn` (7 portadas 1:1 do v1 donor Maezo-Healthcare-Plan `src/maezo/processes/dmn/fraude_scoring/` @1ba8cb8 — ja invertidas: saida `indicador_score:integer`/`indicador_label`/`motivo`, SEM coluna de veredito; ported, wiring pending T2.7 fase 2 apos T1.4; orphan-allowlisted) | Limiares/scores de indicadores (upcoding/unbundling/phantom/zscore/peer-deviation/risk) — SINTETICOS; `indicador_score` e FATO DE MONTAGEM/roteamento, nunca veredito (alimenta `score_indicadores`/`indicadores_presentes` via worker `operadora.fraude.score_indicators` na fase 2) | medico-auditor + auditoria especial + financas | `DRAFT — requires human review before any deploy` |
| Cadeia de custodia (`src/maezo/gateway/custody.py` + ADR-0020) | Aceitabilidade probatoria do `bundle_root`-Merkle-sobre-hash-chain; retencao 5+ anos vs erasure LGPD; desenho de legal-hold | juridico/regulatorio + DPO | `DRAFT — requires human review before any deploy` |

| `src/maezo/tools/workers/fraude.py:876` (`start_contratual`, o caminho CANONICO in-flow) — a mesma logica de split e espelhada inline (nao numa funcao nomeada) nos predicados FRAUDE→CANCEL/FRAUDE→INADIMPLENCIA de `src/maezo/platform/notification_bridge.py:634-678` (o mirror Kafka desacoplado). **Citacao corrigida:** nenhum simbolo `contratual_target` nem modulo `notifications_bridge/consumer.py` existe em `src/` nesta checkout (`grep -rn "contratual_target" src/` = zero hits) — a citacao original apontava para um caminho que nunca existiu. | Split de roteamento `fraude.start_contratual` por `entidade_tipo`: beneficiario→SP-OP-CANCEL-001, contrato→SP-OP-INADIMPLENCIA-001, default CANCEL (PR #103, GAP-XPROC-1) — semantica de encaminhamento adverso precisa de confirmacao de negocio | produto + juridico | `DRAFT — requires human review before any deploy` |


---

## Wave-1B — network_change_bridge: CRED→ADEQUACAO choreografia (GAP-XPROC-2)

Consumidor real de `agents.events.cred.network_changed` que INICIA SP-OP-ADEQUACAO-001 (PR #117).
A ponte so INICIA a avaliacao da celula; o unico efeito adverso de ADEQUACAO (compromisso
financeiro de fallback) permanece human-gated LA (`UT_DecisaoFallback`). Nenhum PHI de beneficiario
no fato nem nas vars de start (teste de arquitetura estrutural).

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `src/maezo/platform/integrations/network_change_bridge/consumer.py` (`derive_ciclo_avaliacao`) — **Citacao corrigida: o modulo E a funcao NAO existem em `src/` nesta checkout.** `find src -type d -name "*network_change*"` = zero diretorios; `grep -rn "derive_ciclo_avaliacao" src/` = zero hits. O proprio repo ja registra essa ponte como entrypoint pendurado: `docs/design/T3.3-chaos-resilience.md:43-44` ("Same for `consent_revocation_bridge` / `network_change_bridge`. These are dangling entrypoints" — a frase atravessa as duas linhas; `network_change_bridge` esta em `:44`). A linha descreve, portanto, o DESIGN da ponte (PR #117), nao codigo de `main` | **Periodicidade do `ciclo_avaliacao`**: a ponte deriva o ciclo como TRIMESTRE (`YYYY-Qn`) de `data_efeito_iso` do fato (deterministico — idempotencia da reentrega; 1 avaliacao por celula por trimestre de efeito). Trimestre vs mes e escolha de politica regulatoria (RN 259/566 — periodicidade de avaliacao de adequacao) **DRAFT/verify**. **Cross-ref de formato (escopo honesto — `YYYY-Qn` NAO e um formato implementado):** nenhum codigo em `src/` produz uma string `YYYY-Qn`; as duas unicas ocorrencias do token em `src/` sao comentarios de anotacao de tipo (`agents/gustavo/graph.py:236`, `agents/andre/graph.py:380` — este ultimo grafado `YYYY-QN`), nao geradores de valor. `YYYY-Qn` e o que o DESIGN da ponte (nao construida) DECLARA. A unica convencao IMPLEMENTADA e a de `ans_cron.py::_compute_competencia` — `YYYY-MM` (`src/maezo/tools/workers/ans_cron.py:88` para P3M, que emite o MES INICIAL do trimestre fechado, e `:97` para P1M) e `YYYY-01` no caso anual P12M (`:73`) — ver as linhas `spec/processes/dmn/ans_calendar.dmn` e `ans_cron.py::_compute_competencia` abaixo. A questao do SME e portanto entre a convencao IMPLEMENTADA (`YYYY-MM`/`YYYY-01`) e a convencao `YYYY-Qn` que o design da ponte declara: quem ratificar uma delas deve ser confrontado com a outra antes de aprovar, para nao fixar duas convencoes de competencia incompativeis na mesma operadora | regulatorio + PO | `DRAFT — requires human review before any deploy` |
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
| `spec/processes/dmn/pagto_alcada.dmn` (nova saida `tier_minimo`: DENTRO_TETO_L2->0, L1->1, L2->2, L3->3, catch-all->4) + achatamento em `SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn` (BRT_AlcadaRouting) | Confirmar o mapeamento faixa->tier-minimo-de-aprovador com financas (segregacao de funcoes — mesma revisao pendente da escada de valores DRAFT); a saida e FATO de tier-match, nunca liberacao. Antes do sign-off, financas deve re-rodar `tests/unit/sec/test_pagto_valor_fence.py` — agora pina a posicao do catch-all (`r_catchall`) + `tier_minimo`/`grupo_aprovador` e a propriedade end-to-end de que nenhuma regra produz DENTRO_TETO_L2 com `dentro_teto_l2=False`; qualquer edicao da escada que reordene/altere essas linhas quebra o fence antes de chegar a producao | financas + compliance | `DRAFT — requires human review before any deploy` |

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
| **NOTE (t3.1-followup-nits, 2026-07-25): stale path corrected.** No `src/maezo/platform/integrations/notifications_bridge/consumer.py` exists on `main` as-of-today — that package/filename was never merged. The fix this row describes lives on the still-unmerged `t2.6-eb3-bridge-wiring` branch, which implements it inside the PRE-EXISTING `src/maezo/platform/notification_bridge.py` (constants `_ANS_REPORT_TYPE_NIP_FILING`/`_COMPETENCIA_PENDENTE`/`_PERIODICIDADE_NIP_FILING`, functions `_ans_submit_variables_from_nip_handoff`/`_ans_nip_business_key`), not a `consumer.py` module. That same branch separately adds a NEW Kafka-consumer entry point at `maezo.platform.integrations.notifications_bridge` (a module, not a `consumer.py` file inside a package) for Helm's `deployment-bridge.yaml` — this is likely what the original path was anticipating pre-rename; it too is pending merge, so its final path may still change. On `main` today, `nip.handoff_ans_submit` (`src/maezo/tools/workers/nip.py`) still leaves `report_type`/`competencia`/`periodicidade` unset — GAP-ANS-3 remains fully open pending that branch's merge. | Confirmar se `RN_209_UTILIZACAO` ("utilizacao de servicos") e de fato a classificacao regulatoria correta para um filing de resposta formal a NIP, ou se merece um `report_type` proprio (o contrato hoje so declara 5 valores, todos de relatorios PERIODICOS agregados — nenhum modela um ato vinculante POR CASO); confirmar `periodicidade=mensal` como granularidade conservadora adequada para o calendario deste caminho | regulatorio-ANS + juridico | `DRAFT — requires human review before any deploy` |

## Wave-3 — INADIMPLENCIA test-spec + contract reconciliation, ADEQUACAO A2A wiring (GAP-INAD-4/GAP-INAD-5/GAP-ADEQ-4)

`fix/w2-inad-adeq` (T2 finisher batch, coordinated by orchestrator-wave3): three residue gaps
against the already-shipped INADIMPLENCIA-001/ADEQUACAO-001 artifacts (no BPMN/DMN content changed).
**NOTE (corrected 2026-07-26, t5-workers-f2):** the branch `fix/w2-inad-adeq` no longer exists in
git history. GAP-INAD-4 and GAP-INAD-5 (docs) DID land on `main` (their deliverables exist:
`docs/processes/test-specs/SP-OP-INADIMPLENCIA-001.md`; contract v0.2.0 §GAP-INAD-5) — a deleted
branch after a squash-merge, not fiction. GAP-ADEQ-4, however, was NEVER built (see the corrected
bullet below): its claimed `andre/delegation.py` / `_flow_for` code does not exist anywhere in the
tree.

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
- **GAP-ADEQ-4 — CORRECTED 2026-07-26 (t5-workers-f2): the entry below was FICTION; ground truth
  restored.** ~~`operadora.adequacao.prepare_remediation_dossier` now delegates `analytics.population`
  to Andre via a DI-optional `DelegationDispatcher` (mirrors GAP-INAD-6); `andre/delegation.py` gained
  `_flow_for(envelope)`, which disambiguates the task_type shared with SP-OP-PAGTO-001 by
  `envelope.origin` (`origin=adequacao` → `adequacao_dossier` flow) — closing the gap between
  `graph.py`'s docstring and the code.~~ **This build NEVER landed.** The branch `fix/w2-inad-adeq`
  attributed to this Wave-3 batch does NOT exist anywhere in git history (`git branch -a` / `git log
  --all` return zero hits), and the code confirms the topic was an UNREGISTERED gap — the very premise
  of DL-0033 (which cites `adequacao.py`'s own "Spec topic with NO implementing function today (gap,
  not fabricated here, Andre A2A-gated)" comment, and `andre/delegation.py` has no `_flow_for`). The
  real Andre A2A `DelegationDispatcher` delegation remains UNBUILT and deferred to the full-A2A wiring
  task. **As of t5-workers-f2 the topic is now BUILT as a LOCAL STUB** (DL-0033, ratified 2026-07-26):
  `adequacao.prepare_remediation_dossier`, a neutral `FunctionWorker` (log + `{"dossier_prepared":
  True, ...}`, no `DelegationDispatcher`) registered on `operadora.adequacao.prepare_remediation_
  dossier`, closing the BPMN topic orphanage — NOT the real A2A delegation the fiction above claimed.

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
(**citacao corrigida abaixo — este modulo/funcao NAO existem em `src/` nesta checkout; ver NOTE
apos este paragrafo**) descreveria, se existisse, um primitivo puro compartilhado pelos dois
caminhos que deriva `competencia` (`YYYY-MM` | `YYYY-Qn`) de
uma data-ANCORA que agora viaja no fato Kafka — NUNCA do relogio de processamento da ponte (replay/
reentrega do MESMO fato sempre le a MESMA ancora, preservando a idempotencia da business key
`ANSSUB-{tenant}-{report_type}-{competencia}[-nipfiling-{submit_id}]`):

- **Caminho cron:** `tools/workers/events.py:187::make_publish_event_handler` (**citacao corrigida:**
  o caminho antes citado, `tools/workers/phase0.py`, nao existe em `src/` nesta checkout — a funcao e
  real e mora em `events.py`) grava
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

**NOTE (close-out-sprint-09-08-26, 2026-08-09): citacoes corrigidas — modulo/funcoes nao estao em
`main`.** `grep -rn "_competencia_from_anchor|_ans_cron_competencia|_nip_filing_competencia" src/`
retorna ZERO resultados nesta checkout: nao existe modulo `notifications_bridge/consumer.py` nem
funcao com nenhum dos tres nomes citados acima. O calculo de periodo-fechado que esta secao
descreve (mes/trimestre/ano IMEDIATAMENTE ANTERIOR a ancora, incluindo a forma `"YYYY-01"` do caso
P12M) EXISTE de fato, mas como `ans_cron.py::_compute_competencia`
(`src/maezo/tools/workers/ans_cron.py:37`) — e a propria funcao que a chama,
`trigger_submissions`, documenta-se como INALCANCAVEL no BPMN implantado (o engine liga
`ST_PublishCronDue*` direto ao topico generico `operadora.events.publish`, nunca a
`operadora.ans_cron.trigger_submissions` — ver `ans_cron.py:120-126`). As funcoes que REALMENTE
rodam hoje, `notification_bridge.py::_ans_submit_variables_from_cron_due`/
`_ans_submit_variables_from_nip_handoff`, NAO computam `competencia` de nenhuma ancora: o caminho
cron repassa o valor de `competencia` que ja vinha no fato (ou cai na sentinela
`COMPETENCIA_PENDENTE`), e o caminho nip_filing sempre semeia a sentinela. No mesmo espirito da
NOTE ja honesta desta pagina (linha ~335 acima): o restante desta secao descreve a INTENCAO de
projeto de uma mudanca ainda nao mesclada, nao o comportamento de `main` hoje.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| **Citacao corrigida — ver NOTE acima.** Nenhum modulo `notifications_bridge/consumer.py` nem funcao `_competencia_from_anchor`/`_ans_cron_competencia`/`_nip_filing_competencia` existe em `src/`. O analogo real mais proximo e `src/maezo/tools/workers/ans_cron.py:37` (`_compute_competencia`), cujo unico chamador (`trigger_submissions`) e INALCANCAVEL no BPMN implantado por documentacao propria | Mapeamento ASSUMIDO (nao confirmado): `competencia` = o periodo de calendario (mes ou trimestre, por `periodicidade`) IMEDIATAMENTE ANTERIOR ao mes da data-ancora — o padrao usual de relatorio periodico regulatorio (reporta-se em N o periodo fechado em N-1). **Caso ANUAL (P12M, hoje so `QUALIFICACAO`):** mesmo principio aplicado ao ANO CALENDARIO — a competencia e o ano anterior por INTEIRO, representado `"YYYY-01"` (nao `"YYYY"` bare) por consistencia de formato com os demais `report_type` (`"YYYY-MM"`; ver `ans_cron.py::_compute_competencia`). Confirmar contra o texto vigente ANS por `report_type` (RN 124/209/388/424, DIOPS) se este e de fato o corte correto (vs. competencia = mes/trimestre/ano DA PROPRIA ancora) e se a representacao `"YYYY-01"` do caso anual esta correta | regulatorio-ANS + juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `src/maezo/tools/workers/events.py:187` (`make_publish_event_handler` — stamp de `ans_cron_reference_date_iso` quando `event_type == ans.cron_due`; ver a chave `_ANS_CRON_REFERENCE_DATE_KEY` em `events.py:150`). **Citacao corrigida:** o caminho citado antes, `src/maezo/tools/workers/phase0.py`, nao existe em `src/` nesta checkout (`find src -name "phase0*"` = zero); a funcao e real, mas mora em `events.py` | Confirmar que capturar a data no instante do serviceTask de publish (imediatamente apos o TimerStartEvent disparar) e a ancora regulatoriamente correta do ciclo — vs. uma data de referencia diferente (ex.: dia fixo do mes) que um scheduler futuro possa precisar carregar explicitamente | regulatorio-ANS | `DRAFT — requires human review before any deploy` |

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

---

## T2.9 — SME package pointers (LGPD #55 R-C/R-D worker design; PROGRAMA-001 stratify_risk)

`docs-p2b-escalation-packages` (T2.9 SME-packaging pass): zero BPMN/DMN/worker edits — this batch
packages open design questions into dated addenda in `docs/sme-dispatch/dpo/PACKAGE.md` and
`docs/sme-dispatch/medico-auditor/PACKAGE.md` (both 2026-07-24; status refreshed 2026-07-25
post-merge of `origin/main` 6288341 — #125 built R-F/R-G, #126 built `stratify_risk`, #131 landed
T-E; the questions themselves are unchanged). Both artifacts are already
tracked generically above (LGPD-DSR-001's "Fluxos LGPD..." row near the top; PROGRAMA-001's
Phase-3-foundations row) — this section only points at the NEW, specific questions; it does not
duplicate them.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn` (`ST_CompilarPacote`/`ST_ExecutarRequisicao`; `Error_LgpdErasureFalhou`/`Error_LgpdErasureNaoHumana` declared-unbound) + `src/maezo/tools/workers/lgpd.py` (#55 R-C/R-D orphan topics) | Legal-bases/retention matrix for `compile_data_package` (#55 R-C); collapse 3 orphan `execute_*` workers into 1 `execute_request`-topic worker (#55 R-D) + ratify the erasure-failure/denial routing shape — see `docs/sme-dispatch/dpo/PACKAGE.md` SP-OP-LGPD-DSR-001 addendum | DPO + juridico-privacidade | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/programa_routing.dmn` (`risco_estratificado` input) + `operadora.programa.stratify_risk` (BUILT #126 — fail-closed stub, default `"alto"` live-proven; criteria still SME-gated) / `proactive_contact` (unbuilt, `programa.py:318-319`) | Actual clinical stratification criteria for `risco_estratificado` (prescribed nowhere today) + ratification of the shipped fail-closed `"alto"`→`ANALISE_HUMANA` default; `proactive_contact` channel/consent constraints (joint DPO) — see `docs/sme-dispatch/medico-auditor/PACKAGE.md` and `docs/sme-dispatch/dpo/PACKAGE.md` SP-OP-PROGRAMA-001 addenda | médico-auditor (+ DPO on `proactive_contact`) | `DRAFT — requires human review before any deploy` |

---

## M-2 — Reembolso: tabela de referencia deixa de mentir na cadeia de auditoria (`calculate_value`)

`operadora.reembolso.calculate_amount` escrevia TRES fatos falsos na cadeia ADR-0007. (1) A busca
na tabela de referencia caia de volta para o **proprio valor pedido pelo beneficiario** quando a
`categoria_procedimento` era desconhecida ou vinha com erro de digitacao — o "valor de tabela"
virava identico ao pedido, e portanto `valor_solicitado <= valor_calculado` era True para
QUALQUER quantia; o revisor humano lia `dentro_tabela=true` como fato verificado. (2) O
multiplicador 1.5 (urgencia/emergencia, fora de rede) era aplicado ao valor, mas
`multiplo_tabela_aplicado` era gravado como `1.0`. (3) `fonte_tabela` era sempre
`"TUSS-REFERENCIA"` — rotulando como leitura da tabela TUSS um numero que a tabela nunca
produziu. Hoje nada auto-aprova (teto reembolso 0, D-07), entao o efeito era sobre a decisao
HUMANA, nao sobre pagamento automatico — mas era o dossie que mentia.

**Corrigido nesta mudanca (estrutura/honestidade, sem mexer em valores):** categoria fora da
tabela agora resolve `SEM_TABELA` / valor 0 / multiplo 0.0 e FORCA `dentro_tabela=false` (o token
e o do proprio catch-all da `reembolso_calculo.dmn`, ja pinado no contrato, no test-spec e numa
assercao engine-side); `multiplo_tabela_aplicado` grava o multiplicador realmente aplicado; e
`fonte_tabela` distingue `TABELA_REFERENCIA` de `TABELA_REFERENCIA_MULTIPLICADA`. Desfecho
conservador identico ao pretendido pela BPMN (`ST_CalculateAmount`: "fora de tabela (SEM_TABELA)
-> dentro_tabela=false -> analise humana"). O caminho valido (categoria conhecida) computa
exatamente os mesmos valores de antes — pinado por teste de regressao.

**NAO corrigido — divida ADR-0012, e o que esta linha registra:**

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `src/maezo/tools/workers/reembolso.py` — `_BASE_VALUES_CENTS` + `_MULTIPLO_ACESSO` (1.5) + `_TIPOS_COM_MULTIPLICADOR`, consumidos por `_lookup_tabela_referencia` | **Regra de negocio deterministica vivendo em Python, contra ADR-0012** (DMN e a fonte unica; precedente da migracao: `_FAIXA_TIER_MINIMO` do PAGTO, que saiu do dict Python para `pagto_alcada.dmn` na Wave-1 dos tetos, secao acima). A tabela autoritativa JA existe e ja declara as tres saidas — `spec/processes/dmn/reembolso_calculo.dmn` (`BRT_Calculo`), que por GAP-REEMBOLSO-5 roda ANTES deste worker justamente para que o resultado esteja disponivel aqui. Migrar nao foi feito de proposito: muda QUAIS VALORES sao computados e depende do mesmo sign-off regulatorio+atuarial em que a propria DMN ja esta bloqueada (ela e DRAFT: "multiplo/limite por categoria/segmentacao requer sign-off de regulatorio + atuarial"). **Duas divergencias PRE-EXISTENTES que o revisor precisa ver:** (a) os valores do Python DISCORDAM dos da DMN para a mesma categoria (`consulta` 35000 aqui vs 12000 la; `exame_especial` 45000 vs 25000) e `internacao`/`opme`/`alta_complexidade` NAO tem linha na DMN — sob a DMN resolveriam `SEM_TABELA`; (b) `ST_CalculateAmount` nao tem nenhum `inputParameter` mapeando `calculo.*` para dentro do worker e `ReembolsoInput` nao tem campo para isso, logo o worker RE-ORIGINA o valor de referencia da tabela-sombra em Python em vez de ler a saida da DMN que a documentacao da BPMN afirma que ele le. Decidir: migrar a tabela+multiplicador para a `reembolso_calculo.dmn` (e reconciliar os valores divergentes), e wirear o worker para CONSUMIR `calculo.valor_calculado_tabela_cents`. A mesma migracao muda `fonte_tabela`: hoje o worker emite o token GENERICO `TABELA_REFERENCIA` (generalizacao do lado Python, sem correspondente na DMN); apos migrar, os hits passam a carregar os tokens PER-CATEGORIA que a `reembolso_calculo.dmn` ja emite (`TABELA_REFERENCIA_CONSULTA`/`_EXAME`/`_TERAPIA`), entao qualquer consumidor que hoje compara contra o token generico precisa ser atualizado junto. Os valores em Python sao SINTETICOS — DRAFT/verify contra a tabela de reembolso vigente. Tocar em qualquer um deles MOVE DINHEIRO | atuarial + regulatorio (tabela/multiplo, mesmo revisor da linha Phase 2 de `SP-OP-REEMBOLSO-001`) + arquitetura (ADR-0012: migracao para DMN + consumo do output) | `DRAFT — regra de negocio fora da DMN (ADR-0012); requires human review before any deploy` |

---

## W4 — shadow candidates para as quatro tabelas irmas conhecidas-erradas (M-4..M-7)

Mesmo padrao do precedente MERGEADO da RN 259 (`spec/processes/dmn/adequacao-gap-shadow-candidate.yaml`,
PR #204): cada tabela viva conhecida-errada ganha um MANIFESTO CANDIDATO em `.yaml` ao lado dela.
O candidato e DADO, nunca artefato — todo caminho de selecao de artefato globa `*.dmn`/`*.bpmn`
apenas (`collect_artifacts`, `engine_deploy.py:140`; CLI de validacao, `validation/cli.py:103`), o
censo 16 BPMN + 62 DMN segue intacto, e cada manifesto e NOMEADO por um teste que prova a exclusao.
Ratificar exige os TRES campos (`ratificado: true` + `revisor` + `ratificado_em`, nenhum
`PLACEHOLDER_*`) e e mudanca de DADOS; aplicar as regras a TABELA VIVA e, per ADR-0028 §7, o ato do
DONO regulatorio/clinico — a engenharia nunca corrige a tabela. Os DOIS arquivos de cada par estao
em `.github/CODEOWNERS` (com a ressalva honesta, escrita la, de que so existem dois handles de dono
nesta org: as linhas garantem que UM revisor humano e requerido, nao codificam competencia de
dominio — a competencia esta nesta fila).

**DIFERENCA DELIBERADA PARA O W3: NENHUM WIRING DE WORKER NESTA ONDA.** Nao ha modulo em `src/` que
leia estes manifestos (pinado por `test_no_src_consumer_of_the_candidate_manifests`, com o
manifesto W3 — que E consumido — servindo de ancora de nao-vacuidade), nao ha telemetria de sombra e
nao ha consumidor de enforcement. A evidencia de divergencia vive inteiramente em testes
(`tests/unit/spec/`), que leem AMBAS as tabelas dos artefatos (XML vivo e YAML candidato) com o
mesmo leitor FIRST-hit FAIL-CLOSED (`tests/support/dmn_first_hit.py`): um formato de entrada que ele
nao entende LEVANTA excecao, nunca "nao casa".

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `spec/processes/dmn/glosa-triage-shadow-candidate.yaml` + `glosa_triage.dmn` (M-4) | **`r_sem_glosa` (row 1, :53-62) gateia `categoria_normalizada` num conjunto NEGATIVO `not("tecnica","clinica")` (:56), que admite `"desconhecida"`.** Sob `hitPolicy=FIRST` isso torna INALCANCAVEL o fail-safe que a propria tabela declara para "categoria desconhecida" (:94) em todo o subespaco {`item_conforme_tabela=true`, `divergencia_valor=false`, `documentacao_anexa=true`} — e `SEM_GLOSA` e end-state TERMINAL sem User Task (`SP-OP-CONTAS-001_...bpmn:292-294` -> `ST_PublishSemGlosa` -> `End_SemGlosa`, :90-104), logo o caso FECHA sem revisao humana. A tabela A MONTANTE declara o contrato oposto: "Codigo desconhecido -> `categoria_normalizada="desconhecida"` (rota conservadora a humano a jusante NA GLOSA_TRIAGE, nunca aceite automatico)" (`glosa_reason_normalization.dmn:13-15`); e `"desconhecida"` e o DEFAULT do proprio worker (`contas.py:373`). **Candidato:** move a regra permissiva da posicao 1 para a 4 (especifico antes de permissivo) e troca UMA celula — o conjunto negativo pelo conjunto POSITIVO `"documental","administrativa","valor"`, que e o dominio fechado declarado pelas duas tabelas (`...bpmn:106`) MENOS os dois que ja eram excluidos MENOS o fail-safe `desconhecida`. Nenhum literal novo. Provado por exaustao: divergem EXATAMENTE onde a categoria esta fora do dominio declarado com os tres fatos favoraveis. **REGISTRADO E NAO FECHADO:** ACHADO-1 (reordenar sozinho NAO fecha o buraco — provado no teste); ACHADO-3 (`tipo_item` e input DECORATIVO: `-` nas cinco regras, mesma classe de GAP-CANCEL-2); ACHADO-4 = a pergunta ja aberta na linha Wave-1/GAP-CONTAS-3 acima (`documental`/`administrativa` com anexos presentes continuam em SEM_GLOSA — o candidato PRESERVA isso de proposito, DECISAO-A). Ratificar agora TAMBEM exige que o `tabela_viva.sha256` deste manifesto ainda bata com os bytes de `glosa_triage.dmn` no disco (#221) — uma edicao da tabela invalida uma ratificacao pendente/concedida ate re-revisao | auditoria-contas + compliance (mesmo revisor da linha Wave 1 / GAP-CONTAS-3 acima) | `DRAFT — candidato nao ratificado; a edicao da tabela viva e ato do dono (ADR-0028 §7)` |
| `spec/processes/dmn/carencia-check-shadow-candidate.yaml` + `carencia_check.dmn` (M-5) | **O bloco CPT (`r01`/`r02`, :89-108) abre a tabela com WILDCARD em `tipo_procedimento` (:91, :102) e gateia so em `dias_desde_adesao` + `cpt_declarada=true`; todas as demais regras exigem `cpt_declarada=false`.** Consequencia (provada como fato de alcancabilidade, nao afirmada): com CPT declarada, as regras 3..12 sao INALCANCAVEIS — inclusive a escada de urgencia `r03`/`r04` (24h/1 dia, que a propria descricao da tabela declara em :19-20) e o catch-all `r99` que a tabela chama de "OBRIGATORIO L0 HARD" (:230-232). REPRO: `urgencia_emergencia` + 30 dias + CPT -> `carencia_cumprida=false`, `prazo_restante_dias=700` (prazo de 24 MESES aplicado a uma urgencia). **BASE DE RATIFICACAO (citada, nao aplicada): Lei 9.656/1998 art. 12, V (prazo maximo de 24 HORAS para urgencia/emergencia) + art. 35-C (cobertura obrigatoria), contra o art. 11 (CPT restringe o eletivo de alta complexidade ligado a doenca preexistente)** — as tres ja citadas pela propria tabela (:19-20, :24-25) e todas marcadas DRAFT/verify por ela (:26-27). O candidato CONTRADIZ EXPLICITAMENTE a premissa que a tabela declara ("CPT tem precedencia sobre qualquer outro prazo de carencia ordinario", :85) para UM `tipo_procedimento` so, e essa contradicao e exatamente o que o SME ratifica ou recusa. **Candidato:** move `r03`/`r04` para as posicoes 1-2 e alarga UMA celula em cada (`cpt_declarada`: `false` -> `-`). NENHUM dia-contagem novo: `< 1`/`>= 1`/`1`/`0` sao os da propria `r03`/`r04` (um teste pina que os literais numericos do candidato sao subconjunto dos da tabela viva). Monotono a favor do beneficiario: nunca transforma carencia cumprida em nao cumprida, nunca alonga prazo informado. **REGISTRADO E NAO FECHADO:** ACHADO-1 (`parto` sob CPT tambem le 730 dias em vez de 300 — exige adjudicar o escopo do art. 11, nao feito); ACHADO-3 (o catch-all L0 CONTINUA inalcancavel sob CPT — o candidato NAO fecha isso, e diz isso); ACHADO-4 (a tabela cita "art. 12 II" onde esta revisao cita art. 12, V — reconciliar inciso/alinea faz parte da ratificacao; este arquivo nao escolhe qual esta certo); ACHADO-5 (portabilidade RN 186/2009 segue sem input). Ratificar agora TAMBEM exige que o `tabela_viva.sha256` deste manifesto ainda bata com os bytes de `carencia_check.dmn` no disco (#221) — uma edicao da tabela invalida uma ratificacao pendente/concedida ate re-revisao. **MITIGACAO HOJE, sem exagero:** a tabela NAO e avaliada — nenhum dos tres inputs existe no contrato de start de SP-OP-AUTH-001, entao o criterio REGULATORIO falha fechado com `REGULATORIO_ENTRADA_AUSENTE` ANTES da chamada DMN (`auth.py:598-604`; ja e o residuo (4) da linha GAP-AUTH-4 acima); e mesmo se existissem, `_gate_on_ratification` (`auth.py:833-869`) devolve `false` com `REGULATORIO_FONTE_NAO_RATIFICADA` enquanto `carencia_check` estiver `ratificado: false` em `auth-criteria-ratification.yaml:97-105`, INDEPENDENTE do que a tabela computar (modo SOMBRA guarda o que ela teria decidido). Nada auto-aprova; todo pedido vai a analise humana | juridico/regulatorio + medico auditor (mesmo revisor da linha `carencia_check.dmn` acima) | `DRAFT — candidato nao ratificado; contradiz uma premissa declarada da tabela viva, so o SME decide` |
| `spec/processes/dmn/upcoding-complexity-ceiling-shadow-candidate.yaml` + `upcoding_complexity_ceiling.dmn` (M-6) | **Das SETE tabelas `fraude_scoring/*` que `operadora.fraude.score_indicators` agrega (`fraude.py:150-158`), esta e a UNICA cujo catch-all emite `0`/`"none"` (:73-74); as outras seis emitem `10`/`"<decision_id>_indeterminado"`.** E e a unica SEM uma regra explicita de "indicador ausente" com condicao POSITIVA — o catch-all serve as DUAS populacoes ("dentro do teto de complexidade / nao mapeado", :70) e resolve a ambiguidade no sentido permissivo. Consequencia: um `encounter_class` fora dos tres literais declarados contribui `0` ao `score_indicadores` e NADA a `indicadores_presentes` (o rotulo `"none"` e filtrado, `fraude.py:344-346`) — indistinguivel, no dossie, de um encontro verificado e conforme. **Candidato:** acrescenta as tres regras "dentro do teto" (`< 2`/`<= 3`/`<= 4` — complementos EXATOS dos tetos `> 2`/`2`/`> 3`/`> 4` da propria tabela, nenhum tier novo) com as saidas `0`/`"none"` do proprio catch-all vivo, e alinha o catch-all a convencao da familia. O `10` e o rotulo `_indeterminado` sao LIDOS DOS ARTEFATOS das seis irmas pelo teste, nao digitados. **O DEFEITO JA E LIVE-ENGINE-VERIFICADO NESTE REPO:** `tests/integration/dmn/test_fraude_scoring_chain.py::test_scenario_a_no_signal_falls_to_catchalls` (:110-132) avalia as sete tabelas no motor REAL sem nenhum sinal de evidencia e pina `score_indicadores == 75` com SEIS rotulos — `upcoding_complexity_ceiling` e o UNICO dos sete que falta, e a sua contribuicao para esse 75 e ZERO. **O QUE QUEBRA AO APLICAR (ACHADO-3B):** essa expectativa passa a 85 (+10, exatamente o catch-all) e ganha o setimo rotulo; os cenarios B e C nao mudam (usam `encounter_class="ambulatorio"`). O delta de +10 e derivado dos artefatos e pinado em `tests/unit/spec/test_upcoding_ceiling_shadow_candidate.py` para nao virar surpresa. **CONSEQUENCIA QUE O DONO PRECISA ACEITAR (ACHADO-2):** +10 no `score_indicadores` de todo caso com classe nao mapeada pode cruzar as faixas de `fraude_indicadores` (`>= 20` APROFUNDADA :51, `>= 50` PRIORITARIA :43) — sempre na direcao de MAIS investigacao humana, nunca menos, e `fraude_indicadores` nao emite veredito (invariante L0 :12-15). **REGISTRADO E NAO FECHADO:** ACHADO-1 (o comentario de `_NO_INDICATOR_LABEL`, `fraude.py:216-218`, afirma que TODA tabela emite `"none"` no catch-all — falso hoje para seis delas; aplicar o candidato torna o comentario verdadeiro, mas mexer no worker seria wiring, fora do escopo desta onda); ACHADO-3 (input AUSENTE vs NULO — este arquivo NAO afirma o que o motor faz com variavel ausente do payload; o corpus de divergencia usa so valores presentes, e verificar contra o motor e item de plataforma); ACHADO-4 (nao ha faixa "borderline" para pronto-socorro — inventar uma seria inventar conteudo de auditoria medica); ACHADO-5 (os cabecalhos .dmn das 7 tabelas seguem dizendo "orfa ate o wiring" — `upcoding_complexity_ceiling.dmn:11-12` e o :11 das seis irmas — mas o wiring ja existe e `orphans-allowlist.yaml:118-133` ja esta corrigida, "PERMANENT orphan... not a pending-wiring gap"). Ratificar agora TAMBEM exige que o `tabela_viva.sha256` deste manifesto ainda bata com os bytes de `upcoding_complexity_ceiling.dmn` no disco (#221) — uma edicao da tabela invalida uma ratificacao pendente/concedida ate re-revisao | medico-auditor + auditoria especial + financas (mesmo revisor da linha das 7 tabelas `fraude_scoring/*` acima) | `DRAFT — candidato nao ratificado; scores seguem SINTETICOS` |
| `spec/processes/dmn/triage-redflag-gestante-shadow-candidate.yaml` + `triage_redflag_gestante.dmn` **e** `spec/processes/dmn/triage-redflag-pediatric-shadow-candidate.yaml` + `triage_redflag_pediatric.dmn` (M-7 — CLINICO) | **OS DOIS CANDIDATOS NAO PROPOEM MUDANCA DE REGRA NENHUMA.** `regras_candidatas` e a tabela viva, celula por celula, motivo por motivo — provado por exaustao, para que "nada foi alterado" seja FATO VERIFICADO e nao frase. Razao: as oito regras de cada tabela ja estao ordenadas por severidade NAO-CRESCENTE (P1/ESCALATE_EMERGENCY > P2/ESCALATE_NURSE > CONTINUE, dominios declarados em `triage_redflag_adult.dmn:18-19`), logo sob FIRST-hit **a tabela ja emite, para toda entrada, o veredito MAIS SEVERO entre as regras que casam** — nenhuma permutacao das oito escalaria mais. NAO HA MASCARAMENTO A DESFAZER; os defeitos sao LACUNAS DE BANDA, e fechar qualquer uma exige afirmar um limiar ou uma conduta clinica que a tabela nao contem. Todos foram registrados como ACHADO com vetores de repro VERIFICADOS CONTRA O ARTEFATO VIVO pelo teste (um vetor que deriva da tabela quebra o build). **PEDIATRIA:** ACHADO-P1 — febre so e red flag em `idade_meses < 3` (:35); de 3 meses em diante NAO HA REGRA (febre moderada aos 4 meses -> `red_flag=false`/`CONTINUE`; o MESMO quadro aos 2 meses -> P1/EMERGENCIA). A tabela ADULTA mostra a FORMA de uma regra de febre com banda etaria (`>= 65`, :103-112) — a banda e a conduta pediatricas seguem sendo do pediatra. ACHADO-P2 — `desconhecida` e valor DECLARADO do dominio de `intensidade` (`adult:17`) e nao tem regra em nenhuma das duas tabelas: gravidade explicitamente desconhecida le `red_flag=false`. ACHADO-P3 (fail-safe so cobre "grave"), P4 (sem teto etario superior apesar da descricao), P5. **GESTANTE:** ACHADO-G1 — perda de liquido e contracoes regulares param em `IG < 37` e A TERMO NAO HA REGRA (perda de liquido moderada com 38 semanas -> `red_flag=false`; com 36 -> P1). ACHADO-G2 — pre-eclampsia so a partir de 20 semanas. ACHADO-G3 — movimentos fetais so a partir de 26 semanas (25 -> nenhum red flag; 26 -> emergencia). ACHADO-G4 (identico ao P2 — e questao de FAMILIA, nao de uma tabela), G5, G6. **Alargar uma banda existente com o wildcard `-` foi deliberadamente RECUSADO:** cada regra declara o seu escopo clinico na propria motivo ("pre-termo", "3o trimestre", "apos 20 semanas") e alarga-la faria a regra disparar fora do quadro que ela mesma nomeia — inventar conteudo clinico por omissao. Ratificar aqui significa algo mais estreito e mais util que o usual: o clinico REGISTRA que leu e dispositionou os achados; fechar qualquer um deles e edicao DIRETA da tabela viva, com conteudo que so ele pode escrever. Ratificar agora TAMBEM exige que o `tabela_viva.sha256` de CADA um dos dois manifestos ainda bata com os bytes de `triage_redflag_gestante.dmn`/`triage_redflag_pediatric.dmn` respectivamente no disco (#221) — uma edicao de qualquer uma das tabelas invalida a ratificacao pendente/concedida daquele manifesto ate re-revisao | medico obstetra (gestante) / medico pediatra (pediatrica) — mesmos revisores das linhas Phase 1 acima | `DRAFT — achados registrados, nenhuma regra proposta; requires human review before any deploy` |

---

## T2.6-2 TISS-schema-pin ratification gate (dark build; NOT wired into `ans_submit.py` this wave)

Calibration-scope work: a SEPARATE, ratification-gated TISS/XSD validation seam
(`src/maezo/tools/workers/tiss_schema_pin.py` + `spec/policies/ans/tiss-schema-pin.yaml` +
`spec/policies/ans/synthetic-tiss-v1.xsd`), built alongside — and unrelated to — the
ALREADY-SHIPPED, ALREADY-WIRED T2.6-2 `TissSchemaValidator` (`tools/workers/tiss_schema.py`,
already called by `ans_submit.validate_data`/`validate_entry`; UNCHANGED by this batch). The
validator, its fixtures, and its full test suite (`tests/unit/tools/workers/
test_tiss_schema_pin.py`) are built and proven entirely against a hand-written, explicitly-labeled
SYNTHETIC XSD (header comment `SYNTHETIC — NOT the real ANS Padrão-TISS XSD`) because the real,
ANS-published schema is an open SME-gated external dependency (already recorded in
`docs/design/T2.6-ans-submission-rescope.md` §2.B/§7 — exact padrão-TISS version, and whether
every report type even flows through TISS XSD, are open regulatory questions).

**Relationship to the 14 `_NOTIFY_REGULATORIO_GAP_REASON` strict-xfails**
(`tests/integration/processes/test_sp_op_ans_submit_001.py`): read and analyzed before writing
this module, per task instructions. Their CURRENT cause is that they run on the UNPINNED
`ans_probe` fixture — the ALREADY-WIRED `TissSchemaValidator` computes `schema_valid=False`
unconditionally with `MAEZO_TISS_SCHEMA_VERSION` unset, so the admissibility DMN routes every
submission to `PENDENTE` before those tests' real assertions run (proven correct-by-contrast by
the sibling `ans_probe_tiss_pinned`-based tests, which DO reach `SEGUE_ENVIO`). **This dark build
is UNRELATED to those 14 xfails and changes nothing about them** — their markers are untouched
here; they flip only when the real schema lands and is proven against the live engine. What the
analysis DOES inform is exactly what a schema-pin gate must validate structurally: a schema-valid
payload passes, and each structural violation class a TISS submission can carry — missing
required element, wrong type, cardinality, namespace mismatch — fails with a diagnostic bounded
enough to audit without ever carrying payload content (PHI discipline).

**What this batch does NOT do:** wire `tiss_schema_pin.py` into `register_ans_submit_workers`/
`validate_data`/`validate_entry`, or touch `ans_submit.py` at all (pinned by
`test_tiss_schema_pin.py::test_tiss_schema_pin_not_imported_or_referenced_by_ans_submit`). A
fully tested, but UNREGISTERED, demonstration consumption function
(`tiss_schema_pin_gate_entry`) traces how a future, separately reviewed change would consume this
gate's outcome — see `tiss_schema_pin.py`'s module docstring "SCOPING DECISION" for the full
reasoning, including why that reconciliation (how this folds into `validate_data`'s existing
`schema_valid`/`errors` keys, and how it relates to the already-wired `TissSchemaValidator`) is
deliberately left undecided this wave.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `spec/policies/ans/tiss-schema-pin.yaml` + `spec/policies/ans/synthetic-tiss-v1.xsd` | O manifesto de ratificacao (DRAFT — `status: DRAFT`, `ratificado: false`, campos de responsabilidade nulos) + o artefato XSD referenciado (SINTETICO — `schema_artifact.synthetic: true` — para estrutura apenas, NAO o schema real da ANS). Ativacao exige TODOS os quatro campos independentes (o loader recusa uma ratificacao forjada com `ratificado: true` enquanto `status` permanece `DRAFT` — pinado por `test_forged_ratificado_true_with_status_still_draft_refuses`): SME (regulatorio-TISS) substitui o XSD sintetico pelo XSD real publicado pela ANS (Padrao TISS / Componente de Comunicacao, RN 501/2022 — versao exata SME-gated, ver `docs/design/T2.6-ans-submission-rescope.md` §2.B/§7) + `schema_artifact.synthetic: false` + `status: RATIFIED` + `ratificado: true` + `revisor` + `ratificado_em`. Isso e mudanca de DADOS, sem codigo, sem redeploy — mesma logica do manifesto GAP-AUTH-4. Depois da ratificacao, decidir COMO (e SE) `ans_submit.validate_data` consome este portao, e como ele se reconcilia com o `TissSchemaValidator`/`MAEZO_TISS_SCHEMA_VERSION` ja wired (T2.6-2), e trabalho de uma mudanca revisada separada — nao decidido aqui | regulatorio-ANS + juridico/regulatorio (schema real + versao) + seguranca/compliance (CODEOWNERS do pin, `.github/CODEOWNERS`) | `DRAFT — dark build, nao wired em ans_submit.py; nada ratificado` |

---

## PR-2 — ADR-0040 perspectiva-operadora (SP-OP-CONTAS-001 / SP-OP-RECURSO-001) — pontos DRAFT/verify

As dezessete linhas abaixo espelham, uma a uma, a tabela "O que permanece `DRAFT/verify` (SME /
juridico / owner)" de `docs/adr/0040-perspectiva-operadora-contas-recurso.md` (ADR **Status:
Proposed**, nao ratificado). Nenhum item e fechado por este PR — ele apenas cumpre o registro
obrigatorio desta fila. Texto integral, evidencia `file:line` e a tabela de emendas a ADR-0018/0030
ficam no proprio ADR; aqui vai o resumo + revisor de cada item.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `docs/adr/0040-...md` OQ-1 | Nomes de componentes TISS ("Demonstrativo de Analise de Conta", "Recurso de Glosa", "Protocolo de recebimento") e estrutura de campos por linha (`valor_apresentado`/`valor_processado`/`valor_liberado`/`valor_glosa`/`codigo_glosa`); acrescido pelo discriminador `tipo_comunicacao ∈ {demonstrativo_analise, devolucao_para_correcao}` — a devolucao de conta para correcao e um demonstrativo, ou um artefato TISS proprio? | regulatorio + faturamento/auditoria de contas | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-2 | Prazo de analise da conta — substituir a citacao RN 424/2017 por «prazo contratual + RN 501/2022 (fluxo TISS)» e confirmar o valor (hoje P30D/P20D em `contas_sla.dmn`), o marco inicial (recebimento do lote vs. protocolo) e dias uteis vs. corridos; de onde vem `data_vencimento`, obrigatoria e recusada em branco pelos dois handoffs a PAGTO (D6-bis) | juridico/regulatorio + financas | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-3 | Prazo de resposta ao recurso de glosa (`recurso_sla.dmn`, P10D/P15D analise, P30D teto) — o teto hoje e atribuido a RN 424/2017, que `docs/compliance/rn-currency-review.md:85-87` refuta | juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-4 | Quando RN 424/2017 se aplica de fato (so junta medica/odontologica para divergencia tecnico-assistencial); confirmar se o merito de glosa tecnico-clinica decidido por `UT_RevisaoAuditorMedico` configura essa hipotese | medico-auditor + juridico | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-5 | Tabela TISS de codigos de glosa — `glosa_reason_normalization.dmn` e um mapa SINTETICO auto-declarado `DRAFT/verify`; o redesign nao o valida | faturamento/auditoria de contas | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-6 | Candidate groups `auditoria-contas`, `coordenacao-contas`, `analista-recurso-glosa`, `coordenacao-recurso` seguem PROPOSTOS; o redesign os mantem verbatim para nao introduzir uma segunda pendencia | PO/IdP + operacao | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-7 | `DEVOLVER` e adverso? Classificado aqui como L1 neutro→prestador-adjacente (espelha `End_PagamentoRecusadoHumano` de PAGTO); se auditoria de contas entender que e uma glosa administrativa de fato, precisa do mesmo guard L0 de `GLOSAR` | auditoria de contas + compliance | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-8 | `DEFERIR_PARCIAL` como adverso — classificado L0 por espelhar `APROVAR_PARCIAL` de REEMBOLSO (guard `reembolso.py:564-572`); confirmar que a analogia se sustenta para prestador, nao so para beneficiario | compliance + juridico | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-9 | Suspensao de prazo durante pendencia documental — ja aberta em `SP-OP-RECURSO-001.md:204`; nao fechada por este redesign | juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-10 | Achado M-4 de `glosa_triage` — o conjunto negativo `not("tecnica","clinica")` na row permissiva (agora `r_pagar`) admite `"desconhecida"`. Preservado verbatim de proposito; corrigir e ato do dono da tabela (ADR-0028 §7) | auditoria de contas + compliance | `DRAFT — candidato nao ratificado; a edicao da tabela viva e ato do dono` |
| `docs/adr/0040-...md` OQ-11 | `cid10` fora de `PHI_PROCESS_VARS` — Marina semeia `cid10` nas variaveis de start de RECURSO, mas `PHI_PROCESS_VARS` contem `cid10_referencia`, nao `cid10`. Achado pre-existente, nao criado nem fechado por este redesign | DPO/seguranca | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-12 | MZO-040 / `action-approvals.yaml` — as superficies novas de CONTAS/RECURSO sao declaradas, mas o arquivo continua `status: DRAFT`, `modo: shadow`, todas as aprovacoes `false`/`PENDENTE`. Nenhuma ratificacao e afirmada por este ADR | medica + ANS + seguranca (MZO-040) | `DRAFT — nada ratificado` |
| `docs/adr/0040-...md` OQ-13 | `process_keys` de Andre esta vazio (`spec/agents/andre/agent.yaml`) e contradiz as tools/acoes que o arquivo ja concede para `SP-OP-PAGTO-001` — hoje fail-closed (nega), lacuna funcional latente, nao dependencia deste redesign. **Se adotada, a mesma decisao deve, no mesmo PR, vincular `_contract_variables` de Andre a I-PAGTO-1 (D6-bis)** — conceder a chave sem essa amarra reabriria o caminho automatico que I-PAGTO-1 fecha | seguranca + PO | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-14 | Comunicacao ao prestador no terminal de fraude — `End_EncaminhadaFraude` e a excecao deliberada de D6-ter (notificar um prestador sob investigacao o alertaria). Existe dever de comunicar? Em que momento? Ha forma de comunicar sem comprometer a investigacao? | fraude/investigacao + juridico + compliance | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-15 | Ma-classificacao em `action-approvals.yaml:649` — `operadora.recurso.submit_appeal: submissao_regulatoria_ans` tratava interpor recurso junto a operadora como submissao regulatoria a ANS; a matriz MZO-040 nao apanhou. A remocao da linha (exigida pela delecao do topico) nao e a correcao da classificacao — e a remocao do objeto classificado | seguranca + ANS (MZO-040) | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-R1 | O adaptador de intake TISS do recurso nao existe. `REDESIGN-SP-OP-RECURSO-001.md` §3.1 especifica a forma do evento `agents.events.recurso.intake_recebido` e a regra de bridge que o consome, mas nao constroi o adaptador; a regra fica DORMENTE de proposito, com teste que assere a ausencia de publicador (fica vermelho quando o adaptador chegar). Ate la, RECURSO-001 continua iniciado por Marina ou pela operacao | PO + integracoes | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-R2 | Arredondamento em `DEFERIR_PARCIAL` — a invariante `valor_deferido_brl + valor_glosa_mantido_brl == valor_glosado_brl` e imposta em centavos-inteiros com igualdade exata. Se a operacao usa tolerancia ou arredondamento contratual, o guard do worker precisa refletir isso; ate la o guard recusa a soma que nao fecha | financas + auditoria de contas | `DRAFT — requires human review before any deploy` |


