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
| `src/maezo/agents/helena/graph.py:243` (estado inicial `"escalation_severidade": "leve"`) e `:633` (`state.get("escalation_severidade", "leve")`) | **Achado ADJ-2 do WP-ESCALATION-CORRECAO (VERIFY-WP-ESC.md §9) — mesma classe de defeito do GAP-ESC-SEVERITY-GROUP (worker `escalation.py`), uma camada acima, NAO corrigido neste pacote.** Hoje INALCANCAVEL: os cinco caminhos de escalonamento atuais setam `escalation_severidade` explicitamente (`:545` leve, `:566` grave, `:573` moderada, `:580` leve, `:592` leve, `:601` `_severidade_from_prioridade`), entao o default nunca dispara. Risco latente: um futuro gatilho que sete `escalation_motivo` e esqueca `escalation_severidade` despacharia `leve` silenciosamente — para `motivo_categoria=red_flag_clinico` isso rebaixa a DMN `escalation_routing` da regra `r1` (P1/`plantao-clinico`/ack PT5M) para `r3` (P2/`enfermagem-triagem`/ack PT30M), o MESMO padrao de "severidade clinica assumida como a mais branda" que este pacote corrigiu no worker. Recomendacao: espelhar o fix do worker — sem default, fail-closed na fronteira de start | gestao assistencial + engenharia | `DRAFT — achado latente, nao corrigido neste WP (rastreado)` |
| `src/maezo/agents/lucas/graph.py:713-714` (`"motivo_categoria": state.get("motivo_categoria") or "outro"` e `"severidade": state.get("severidade") or "moderada"`) | **Achado ADJ-3 do WP-ESCALATION-CORRECAO (VERIFY-WP-ESC.md §9, achado do repair-engineer, nao do autor original) — mesma classe do GAP-ESC-SEVERITY-GROUP, uma camada acima do worker, na fronteira de START de SP-OP-ESCALATION-001, NAO corrigido neste pacote.** Os dois defaults sao valores REAIS do dominio contratual (`outro`/`moderada`) usados como fallback silencioso — exatamente a classe de fabricacao que este pacote removeu do worker (`motivo_categoria="outro"`) uma camada abaixo. Sem exposicao ativa hoje: os escritores de `motivo_categoria`/`severidade` no estado do Lucas sao tipados (`Literal`, `lucas/graph.py:193,202,291,341`), entao um valor fora do dominio contratual nao pode chegar por essa via — mas o default em si ainda mascara um `state` incompleto como um caso valido `outro`/`moderada` em vez de recusar. Recomendacao: espelhar o fix do worker — sem default, fail-closed no start | gestao assistencial + engenharia | `DRAFT — achado latente, nao corrigido neste WP (rastreado)` |

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
| `recurso_sla.dmn` v0.2.0 + `SP-OP-RECURSO-001` boundary de teto (GAP-RECURSO-1) | **Semantica do teto RN 424 P30D: JANELA UNICA ABSOLUTA** contada do inicio regulatorio — ancora UNICA `data_recebimento_recurso_iso` (recebimento do recurso; normalizada/defaultada fail-safe para HOJE/UTC pelo intake `ST_ValidarRecurso` — o antigo fail-safe para a data de ciencia alegada pelo prestador, a ancora do RECORRENTE, foi REMOVIDO das 4 rows da DMN por ADR-0040/PR-3); os 3 boundary (analista/coordenacao/auditor) expiram no MESMO instante (`timeDate`), a escalada NAO estende o teto; confirmar com juridico/regulatorio a ancora legal exata (recebimento vs ciencia) e dias uteis vs corridos | juridico/regulatorio + auditoria-contas | `DRAFT — requires human review before any deploy` |
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
| `src/maezo/platform/integrations/network_change_bridge/consumer.py` (`derive_ciclo_avaliacao`) — **Citacao corrigida: o modulo E a funcao NAO existem em `src/` nesta checkout.** `find src -type d -name "*network_change*"` = zero diretorios; `grep -rn "derive_ciclo_avaliacao" src/` = zero hits. O proprio repo ja registra essa ponte como entrypoint pendurado: `docs/design/T3.3-chaos-resilience.md:43-44` ("Same for `consent_revocation_bridge` / `network_change_bridge`. These are dangling entrypoints" — a frase atravessa as duas linhas; `network_change_bridge` esta em `:44`). A linha descreve, portanto, o DESIGN da ponte (PR #117), nao codigo de `main` | **Periodicidade do `ciclo_avaliacao`**: a ponte deriva o ciclo como TRIMESTRE (`YYYY-Qn`) de `data_efeito_iso` do fato (deterministico — idempotencia da reentrega; 1 avaliacao por celula por trimestre de efeito). Trimestre vs mes e escolha de politica regulatoria (RN 259/566 — periodicidade de avaliacao de adequacao) **DRAFT/verify**. **Cross-ref de formato (escopo honesto — `YYYY-Qn` NAO e um formato implementado):** nenhum codigo em `src/` produz uma string `YYYY-Qn`; as duas unicas ocorrencias do token em `src/` sao comentarios de anotacao de tipo (`agents/gustavo/graph.py:236`, `agents/andre/graph.py:380` — este ultimo grafado `YYYY-QN`), nao geradores de valor. `YYYY-Qn` e o que o DESIGN da ponte (nao construida) DECLARA. A unica convencao IMPLEMENTADA e a de `ans_cron.py::_compute_competencia` — `YYYY-MM` (`src/maezo/tools/workers/ans_cron.py:183` para P3M, que emite o MES INICIAL do trimestre fechado, e `:188` para P1M) e `YYYY-01` no caso anual P12M (`:171`) — **ancoras re-derivadas em 2026-09-03 (ANS-CRON-DEAD-CODE re-diagramou o arquivo; eram `:88`/`:97`/`:73`)** — ver as linhas `spec/processes/dmn/ans_calendar.dmn` e `ans_cron.py::_compute_competencia` abaixo. A questao do SME e portanto entre a convencao IMPLEMENTADA (`YYYY-MM`/`YYYY-01`) e a convencao `YYYY-Qn` que o design da ponte declara: quem ratificar uma delas deve ser confrontado com a outra antes de aprovar, para nao fixar duas convencoes de competencia incompativeis na mesma operadora | regulatorio + PO | `DRAFT — requires human review before any deploy` |
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
| `spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn` + `docs/processes/contracts/SP-OP-ANS-CRON-001.md` | Periodicidades `timeCycle` por report_type (R/P1M / R/P3M / R/P1Y) DRAFT/verify — confirmar cadencia e ancora de data (dia do mes/ano) contra as RN vigentes. Scheduler que computa a competencia: **IMPLEMENTADO** (ANS-CRON-DEAD-CODE/GAP-ANS-1; a secao "res-ans-competencia-sentinel" do contrato foi substituida pela topologia `ST_ResolverCompetencia*` -> `ST_PublishCronDue*`; confirmado em `43722b9` contra `ans_cron.py::_compute_competencia` — merge `f80e74c`, PR #287, "feat(ans-cron): competencia real por report_type, taxonomia unica, orfaos removidos, test spec 16/16 (WP-ANS-CRON-COMPETENCIA)"). **Correcao da clausula (ANS-CRON-DEAD-CODE, 2026-09-03 — a redacao anterior dizia "`COMPETENCIA_PENDENTE` so sobrevive fail-closed quando o fato nao carrega a ancora", condicao que nao e a real):** a sentinela sobrevive quando o `report_type` esta **FORA da taxonomia ratificada** (`ans_cron._REPORT_PERIODICIDADE`) — e tambem quando a ancora e inparseavel — e NAO quando o fato nao carrega a ancora (o resolver sempre produz a sua propria ancora; o repasse sem `competencia` na ponte e um fallback distinto, em `notification_bridge._ans_submit_variables_from_cron_due`). Nos 5 tipos que o BPMN pode emitir a sentinela e **inalcancavel** (fence estatico `tests/unit/spec/test_ans_cron_timers_taxonomy.py`). Confirmar aceitabilidade operacional desse fallback residual. **Ancora temporal:** a competencia e derivada no fuso civil `America/Sao_Paulo` (`ans_cron._BUSINESS_TZ`), nao em UTC — a escolha do fuso e default de ENGENHARIA, **DRAFT/verify** (pergunta 2b de `docs/sme-dispatch/regulatorio/PACKAGE.md`) | regulatorio-ANS | `DRAFT — requires human review before any deploy` |

---

## Wave 1 — batch de gaps s-complexity DMN/boundary (fix/w1-s-dmn-boundaries)
Correcoes de conteudo de regra em DMNs ja DRAFT (`cancel_admissibility.dmn`, `glosa_triage.dmn`) e
plumbing estrutural de BPMN (boundary catches + promocao de variavel). Nenhuma DMN passa a ter
saida adversa nova; ambas continuam DRAFT — as correcoes precisam do MESMO revisor ja mapeado nas
linhas Phase 2 de CANCEL-001/CONTAS-001 acima antes de qualquer deploy.
| `spec/processes/dmn/cancel_admissibility.dmn` (GAP-CANCEL-1) | `vinculo_ativo` promovido a 6o input da tabela; r_pedido_l2 (EFETIVAR_PEDIDO) agora exige vinculo_ativo=true — vinculo inativo cai no catch-all (ANALISE_HUMANA). Confirmar que "vinculo inativo" e de fato causa de bloqueio do direito potestativo do titular (RN 412) e nao apenas um dado de cadastro desatualizado | juridico/contratos + compliance | `DRAFT — requires human review before any deploy` |
| `spec/processes/dmn/glosa_triage.dmn` (GAP-CONTAS-3) | `r_pagar` (row 1; era `r_sem_glosa` antes da reconstrucao na perspectiva do pagador — ADR-0040) exige `categoria_normalizada` fora de `{tecnica, clinica}` (antes preemptava `r_tecnica_humano` sob hitPolicy FIRST). Confirmar que a lista de exclusao (tecnica/clinica) e suficiente — ou se `documental`/`administrativa` tambem deveriam ser excluidas do `PAGAR` automatico. **O que mudou com ADR-0040 e a CONSEQUENCIA, nao a condicao:** as cinco celulas de entrada continuam byte-identicas, mas a saida passou de `SEM_GLOSA` (terminal sem efeito e sem humano) para `PAGAR`, que emite demonstrativo ao prestador e abre uma ordem em SP-OP-PAGTO-001 — ordem que para na User Task humana `UT_AnaliseAdmissibilidade` (I-PAGTO-1). O raio de dano do caso duvidoso ENCOLHEU (de fecha-sozinho-e-invisivel para ordem-visivel-e-humano-gated), mas a pergunta ao revisor e a mesma | auditoria-contas + compliance | `DRAFT — requires human review before any deploy` |

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
(`src/maezo/tools/workers/ans_cron.py:127` — re-ancorado 2026-09-03; era `:37`).

**ATUALIZACAO (ANS-CRON-DEAD-CODE, 2026-09-03):** a segunda metade desta NOTE — "a propria funcao
que a chama, `trigger_submissions`, documenta-se como INALCANCAVEL no BPMN implantado (o engine
liga `ST_PublishCronDue*` direto ao topico generico `operadora.events.publish`)" — **deixou de ser
verdadeira**. Cada uma das 5 definitions executa agora `ST_ResolverCompetencia*` no topico
`operadora.ans_cron.trigger_submissions` ANTES do publish, e a competencia viaja computada no
fato. O que permanece ABERTO e o CONSUMIDOR VIVO da ponte (FINDING #2). As funcoes que REALMENTE
rodam hoje, `notification_bridge.py::_ans_submit_variables_from_cron_due`/
`_ans_submit_variables_from_nip_handoff`, NAO computam `competencia` de nenhuma ancora: o caminho
cron repassa o valor de `competencia` que ja vinha no fato (ou cai na sentinela
`COMPETENCIA_PENDENTE`), e o caminho nip_filing sempre semeia a sentinela. No mesmo espirito da
NOTE ja honesta desta pagina (linha ~335 acima): o restante desta secao descreve a INTENCAO de
projeto de uma mudanca ainda nao mesclada, nao o comportamento de `main` hoje.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| **Citacao corrigida — ver NOTE acima.** Nenhum modulo `notifications_bridge/consumer.py` nem funcao `_competencia_from_anchor`/`_ans_cron_competencia`/`_nip_filing_competencia` existe em `src/`. O analogo real mais proximo e `src/maezo/tools/workers/ans_cron.py:127` (`_compute_competencia`) — **re-ancorado e corrigido 2026-09-03 (ANS-CRON-DEAD-CODE): o texto anterior citava `:37` e dizia que o seu unico chamador `trigger_submissions` era INALCANCAVEL no BPMN implantado; nao e mais** — os 5 `ST_ResolverCompetencia*` declaram `operadora.ans_cron.trigger_submissions` | Mapeamento ASSUMIDO (nao confirmado): `competencia` = o periodo de calendario (mes ou trimestre, por `periodicidade`) IMEDIATAMENTE ANTERIOR ao mes da data-ancora — o padrao usual de relatorio periodico regulatorio (reporta-se em N o periodo fechado em N-1). **Caso ANUAL (P12M, hoje so `RN_388_QUALIDADE` — o literal `QUALIFICACAO` da taxonomia paralela foi eliminado em 2026-09-03):** mesmo principio aplicado ao ANO CALENDARIO — a competencia e o ano anterior por INTEIRO, representado `"YYYY-01"` (nao `"YYYY"` bare) por consistencia de formato com os demais `report_type` (`"YYYY-MM"`; ver `ans_cron.py::_compute_competencia`). Confirmar contra o texto vigente ANS por `report_type` (RN 124/209/388/424, DIOPS) se este e de fato o corte correto (vs. competencia = mes/trimestre/ano DA PROPRIA ancora) e se a representacao `"YYYY-01"` do caso anual esta correta | regulatorio-ANS + juridico/regulatorio | `DRAFT — requires human review before any deploy` |
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

> **Atualizacao GAP F-1 (auditoria D1):** os tokens `TABELA_REFERENCIA` / `TABELA_REFERENCIA_MULTIPLICADA` citados no paragrafo acima NAO existem mais — o worker deixou de emitir vocabulario proprio e passou a RELAIAR o token per-categoria da propria `reembolso_calculo.dmn`. O paragrafo fica como esta, datado; a linha da tabela abaixo descreve o estado atual.

**Divida ADR-0012 FECHADA no codigo (GAP F-1, auditoria D1). O que esta linha registra AGORA e a divida que sobrou, que e' toda do lado da DMN:**

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `spec/processes/dmn/reembolso_calculo.dmn` — os valores da tabela de referencia (`r_consulta` 12000, `r_exame_simples` 8000, `r_exame_especial` 25000, `r_terapia` 15000) e a AUSENCIA de rows para `internacao`/`opme`/`alta_complexidade` (hoje caem no catch-all `SEM_TABELA`) | **Valores SINTETICOS/representativos — DRAFT/verify contra a tabela de reembolso vigente antes de qualquer deploy**; a propria DMN declara "multiplo/limite por categoria/segmentacao requer sign-off de regulatorio + atuarial". Tocar em qualquer um deles MOVE DINHEIRO. **O QUE MUDOU (GAP F-1, fechado no codigo, sem tocar em nenhum valor da DMN):** `src/maezo/tools/workers/reembolso.py` nao tem mais tabela em Python — `_BASE_VALUES_CENTS` + `_MULTIPLO_ACESSO` (1.5) + `_TIPOS_COM_MULTIPLICADOR` + `_lookup_tabela_referencia` foram REMOVIDOS (sem shim, sem fallback) e o worker passou a CONSUMIR as saidas de `BRT_Calculo` (`valor_calculado_tabela_cents`/`multiplo_tabela_aplicado`/`fonte_tabela`), achatadas para dentro de `ST_CalculateAmount` por `camunda:inputParameter` (`${calculo.*}` — mesmo idioma de `ST_IssuePaymentAuto` e do `ST_EmitirAutorizacaoAuto` do AUTH-001, necessario porque o `resultVariable` singleResult chega ao external task como Object NAO desserializado). As duas divergencias que esta linha registrava DEIXARAM de existir: (a) o calculo passa a seguir a DMN (consulta 12000, nao 35000; exame_especial 25000, nao 45000; `internacao`/`opme`/`alta_complexidade` -> `SEM_TABELA` -> `dentro_tabela=false` -> analise humana) e (b) o worker le a saida da DMN que a documentacao da BPMN sempre afirmou que ele lia. `fonte_tabela` passa a carregar os tokens PER-CATEGORIA da propria DMN (`TABELA_REFERENCIA_CONSULTA`/`_EXAME`/`_TERAPIA`); os tokens genericos inventados pelo worker (`TABELA_REFERENCIA`, `TABELA_REFERENCIA_MULTIPLICADA`) foram RETIRADOS. Saida da DMN ausente/malformada => `ERR_REEMBOLSO_CALCULO_DMN_INDISPONIVEL` (fail-closed: incidente, nenhum `dentro_tabela`/`dentro_teto_l2` escrito, logo nenhuma auto-aprovacao). **O QUE AINDA PRECISA DE HUMANO:** (1) ratificar/corrigir os valores da tabela e decidir se `internacao`/`opme`/`alta_complexidade` merecem row propria — enquanto nao tiverem, todo reembolso dessas categorias vai para analise humana (conservador, mas e' uma decisao, nao um acaso); (2) decidir se existe multiplo de acesso (urgencia/emergencia, fora de rede) e, existindo, expressa-lo NA DMN — o 1.5 que vivia no worker foi REMOVIDO e deliberadamente NAO transplantado para a DMN (transplanta-lo seria criar regra de negocio nova sem sign-off); (3) limite conhecido do rastro de auditoria: o worker cita `dmn_decisao_id`/`dmn_atividade_bpmn`, mas NAO a VERSAO da decision definition — `mapDecisionResult=singleResult` nao a entrega e nenhuma expressao JUEL de businessRuleTask a expoe, entao a versao autoritativa fica so' no historico de decision-instance do motor; decidir se isso satisfaz ADR-0007 ou se a versao precisa ser materializada. | atuarial + regulatorio (tabela/multiplo, mesmo revisor da linha Phase 2 de `SP-OP-REEMBOLSO-001`) | `DRAFT — valores sinteticos; requires human review before any deploy` (a parte de arquitetura ADR-0012 desta linha esta FECHADA) |

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
| `spec/processes/dmn/glosa-triage-shadow-candidate.yaml` + `glosa_triage.dmn` (M-4) | **`r_pagar` (row 1, `glosa_triage.dmn:61-70`; chamava-se `r_sem_glosa` antes de ADR-0040) gateia `categoria_normalizada` num conjunto NEGATIVO `not("tecnica","clinica")` (`:64`), que admite `"desconhecida"`.** Sob `hitPolicy=FIRST` isso torna INALCANCAVEL o fail-safe que a propria tabela declara para "categoria desconhecida" (`r_catchall`, `:101-109`) em todo o subespaco {`item_conforme_tabela=true`, `divergencia_valor=false`, `documentacao_anexa=true`}. A tabela A MONTANTE declara o contrato oposto: codigo desconhecido -> `categoria_normalizada="desconhecida"` (rota conservadora a humano a jusante NA GLOSA_TRIAGE, nunca desfecho automatico adverso — `glosa_reason_normalization.dmn:16`); e `"desconhecida"` e o DEFAULT do proprio worker (`contas.py`, `GlosaTriageResult.categoria_normalizada`). **O QUE MUDOU COM ADR-0040 (leia antes de ratificar):** a condicao defeituosa e a MESMA, mas a consequencia nao. Antes, o caso duvidoso caia em `SEM_GLOSA` -> `ST_PublishSemGlosa` -> `End_SemGlosa`, um terminal sem efeito e SEM nenhuma User Task: fechava sozinho e invisivel. Agora cai em `PAGAR` -> `ST_EmitirDemonstrativoIntegral` -> `ST_HandoffPagamentoAuto` -> `End_ContaAprovadaIntegral`, e a ordem gerada PARA na User Task humana `UT_AnaliseAdmissibilidade` de SP-OP-PAGTO-001 (I-PAGTO-1: CONTAS semeia EVIDENCIA do lastro, nunca o FATO). O buraco de roteamento continua aberto; o raio de dano encolheu e ficou visivel. A divulgacao completa esta em `glosa-triage-shadow-candidate.yaml` §2.4. **Candidato:** move a regra permissiva da posicao 1 para a 4 (especifico antes de permissivo) e troca UMA celula — o conjunto negativo pelo conjunto POSITIVO `"documental","administrativa","valor"`, que e o dominio fechado declarado pelas duas tabelas MENOS os dois que ja eram excluidos MENOS o fail-safe `desconhecida`. Nenhum literal novo. Provado por exaustao: divergem EXATAMENTE onde a categoria esta fora do dominio declarado com os tres fatos favoraveis. **REGISTRADO E NAO FECHADO:** ACHADO-1 (reordenar sozinho NAO fecha o buraco — provado no teste); ACHADO-3 (`tipo_item` e input DECORATIVO: `-` nas cinco regras, mesma classe de GAP-CANCEL-2); ACHADO-4 = a pergunta ja aberta na linha Wave-1/GAP-CONTAS-3 acima (`documental`/`administrativa` com anexos presentes continuam no ramo permissivo — o candidato PRESERVA isso de proposito, DECISAO-A). Ratificar agora TAMBEM exige que o `tabela_viva.sha256` deste manifesto ainda bata com os bytes de `glosa_triage.dmn` no disco (#221) — uma edicao da tabela invalida uma ratificacao pendente/concedida ate re-revisao | auditoria-contas + compliance (mesmo revisor da linha Wave 1 / GAP-CONTAS-3 acima) | `DRAFT — candidato nao ratificado; a edicao da tabela viva e ato do dono (ADR-0028 §7)` |
| `spec/processes/dmn/carencia-check-shadow-candidate.yaml` + `carencia_check.dmn` (M-5) | **O bloco CPT (`r01`/`r02`, :89-108) abre a tabela com WILDCARD em `tipo_procedimento` (:91, :102) e gateia so em `dias_desde_adesao` + `cpt_declarada=true`; todas as demais regras exigem `cpt_declarada=false`.** Consequencia (provada como fato de alcancabilidade, nao afirmada): com CPT declarada, as regras 3..12 sao INALCANCAVEIS — inclusive a escada de urgencia `r03`/`r04` (24h/1 dia, que a propria descricao da tabela declara em :19-20) e o catch-all `r99` que a tabela chama de "OBRIGATORIO L0 HARD" (:230-232). REPRO: `urgencia_emergencia` + 30 dias + CPT -> `carencia_cumprida=false`, `prazo_restante_dias=700` (prazo de 24 MESES aplicado a uma urgencia). **BASE DE RATIFICACAO (citada, nao aplicada): Lei 9.656/1998 art. 12, V (prazo maximo de 24 HORAS para urgencia/emergencia) + art. 35-C (cobertura obrigatoria), contra o art. 11 (CPT restringe o eletivo de alta complexidade ligado a doenca preexistente)** — as tres ja citadas pela propria tabela (:19-20, :24-25) e todas marcadas DRAFT/verify por ela (:26-27). O candidato CONTRADIZ EXPLICITAMENTE a premissa que a tabela declara ("CPT tem precedencia sobre qualquer outro prazo de carencia ordinario", :85) para UM `tipo_procedimento` so, e essa contradicao e exatamente o que o SME ratifica ou recusa. **Candidato:** move `r03`/`r04` para as posicoes 1-2 e alarga UMA celula em cada (`cpt_declarada`: `false` -> `-`). NENHUM dia-contagem novo: `< 1`/`>= 1`/`1`/`0` sao os da propria `r03`/`r04` (um teste pina que os literais numericos do candidato sao subconjunto dos da tabela viva). Monotono a favor do beneficiario: nunca transforma carencia cumprida em nao cumprida, nunca alonga prazo informado. **REGISTRADO E NAO FECHADO:** ACHADO-1 (`parto` sob CPT tambem le 730 dias em vez de 300 — exige adjudicar o escopo do art. 11, nao feito); ACHADO-3 (o catch-all L0 CONTINUA inalcancavel sob CPT — o candidato NAO fecha isso, e diz isso); ACHADO-4 (a tabela cita "art. 12 II" onde esta revisao cita art. 12, V — reconciliar inciso/alinea faz parte da ratificacao; este arquivo nao escolhe qual esta certo); ACHADO-5 (portabilidade RN 186/2009 segue sem input). Ratificar agora TAMBEM exige que o `tabela_viva.sha256` deste manifesto ainda bata com os bytes de `carencia_check.dmn` no disco (#221) — uma edicao da tabela invalida uma ratificacao pendente/concedida ate re-revisao. **MITIGACAO HOJE, sem exagero:** a tabela NAO e avaliada — nenhum dos tres inputs existe no contrato de start de SP-OP-AUTH-001, entao o criterio REGULATORIO falha fechado com `REGULATORIO_ENTRADA_AUSENTE` ANTES da chamada DMN (`auth.py:598-604`; ja e o residuo (4) da linha GAP-AUTH-4 acima); e mesmo se existissem, `_gate_on_ratification` (`auth.py:833-869`) devolve `false` com `REGULATORIO_FONTE_NAO_RATIFICADA` enquanto `carencia_check` estiver `ratificado: false` em `auth-criteria-ratification.yaml:97-105`, INDEPENDENTE do que a tabela computar (modo SOMBRA guarda o que ela teria decidido). Nada auto-aprova; todo pedido vai a analise humana | juridico/regulatorio + medico auditor (mesmo revisor da linha `carencia_check.dmn` acima) | `DRAFT — candidato nao ratificado; contradiz uma premissa declarada da tabela viva, so o SME decide` |
| `spec/processes/dmn/upcoding-complexity-ceiling-shadow-candidate.yaml` + `upcoding_complexity_ceiling.dmn` (M-6) | **Das SETE tabelas `fraude_scoring/*` que `operadora.fraude.score_indicators` agrega (`fraude.py:150-158`), esta e a UNICA cujo catch-all emite `0`/`"none"` (:73-74); as outras seis emitem `10`/`"<decision_id>_indeterminado"`.** E e a unica SEM uma regra explicita de "indicador ausente" com condicao POSITIVA — o catch-all serve as DUAS populacoes ("dentro do teto de complexidade / nao mapeado", :70) e resolve a ambiguidade no sentido permissivo. Consequencia: um `encounter_class` fora dos tres literais declarados contribui `0` ao `score_indicadores` e NADA a `indicadores_presentes` (o rotulo `"none"` e filtrado, `fraude.py:344-346`) — indistinguivel, no dossie, de um encontro verificado e conforme. **Candidato:** acrescenta as tres regras "dentro do teto" (`< 2`/`<= 3`/`<= 4` — complementos EXATOS dos tetos `> 2`/`2`/`> 3`/`> 4` da propria tabela, nenhum tier novo) com as saidas `0`/`"none"` do proprio catch-all vivo, e alinha o catch-all a convencao da familia. O `10` e o rotulo `_indeterminado` sao LIDOS DOS ARTEFATOS das seis irmas pelo teste, nao digitados. **O DEFEITO JA E LIVE-ENGINE-VERIFICADO NESTE REPO:** `tests/integration/dmn/test_fraude_scoring_chain.py::test_scenario_a_no_signal_falls_to_catchalls` (:110-132) avalia as sete tabelas no motor REAL sem nenhum sinal de evidencia e pina `score_indicadores == 75` com SEIS rotulos — `upcoding_complexity_ceiling` e o UNICO dos sete que falta, e a sua contribuicao para esse 75 e ZERO. **O QUE QUEBRA AO APLICAR (ACHADO-3B):** essa expectativa passa a 85 (+10, exatamente o catch-all) e ganha o setimo rotulo; os cenarios B e C nao mudam (usam `encounter_class="ambulatorio"`). O delta de +10 e derivado dos artefatos e pinado em `tests/unit/spec/test_upcoding_ceiling_shadow_candidate.py` para nao virar surpresa. **CONSEQUENCIA QUE O DONO PRECISA ACEITAR (ACHADO-2):** +10 no `score_indicadores` de todo caso com classe nao mapeada pode cruzar as faixas de `fraude_indicadores` (`>= 20` APROFUNDADA :51, `>= 50` PRIORITARIA :43) — sempre na direcao de MAIS investigacao humana, nunca menos, e `fraude_indicadores` nao emite veredito (invariante L0 :12-15). **REGISTRADO E NAO FECHADO:** ACHADO-1 (o comentario de `_NO_INDICATOR_LABEL`, `fraude.py:216-218`, afirma que TODA tabela emite `"none"` no catch-all — falso hoje para seis delas; aplicar o candidato torna o comentario verdadeiro, mas mexer no worker seria wiring, fora do escopo desta onda); ACHADO-3 (input AUSENTE vs NULO — este arquivo NAO afirma o que o motor faz com variavel ausente do payload; o corpus de divergencia usa so valores presentes, e verificar contra o motor e item de plataforma); ACHADO-4 (nao ha faixa "borderline" para pronto-socorro — inventar uma seria inventar conteudo de auditoria medica); ACHADO-5 (os cabecalhos .dmn das 7 tabelas seguem dizendo "orfa ate o wiring" — `upcoding_complexity_ceiling.dmn:11-12` e o :11 das seis irmas — mas o wiring ja existe e `orphans-allowlist.yaml:118-133` ja esta corrigida, "PERMANENT orphan... not a pending-wiring gap"). Ratificar agora TAMBEM exige que o `tabela_viva.sha256` deste manifesto ainda bata com os bytes de `upcoding_complexity_ceiling.dmn` no disco (#221) — uma edicao da tabela invalida uma ratificacao pendente/concedida ate re-revisao | medico-auditor + auditoria especial + financas (mesmo revisor da linha das 7 tabelas `fraude_scoring/*` acima) | `DRAFT — candidato nao ratificado; scores seguem SINTETICOS` |
| `spec/processes/dmn/triage-redflag-gestante-shadow-candidate.yaml` + `triage_redflag_gestante.dmn` **e** `spec/processes/dmn/triage-redflag-pediatric-shadow-candidate.yaml` + `triage_redflag_pediatric.dmn` (M-7 — CLINICO) | **OS DOIS CANDIDATOS NAO PROPOEM MUDANCA DE REGRA NENHUMA.** `regras_candidatas` e a tabela viva, celula por celula, motivo por motivo — provado por exaustao, para que "nada foi alterado" seja FATO VERIFICADO e nao frase. Razao: as oito regras de cada tabela ja estao ordenadas por severidade NAO-CRESCENTE (P1/ESCALATE_EMERGENCY > P2/ESCALATE_NURSE > CONTINUE, dominios declarados em `triage_redflag_adult.dmn:18-19`), logo sob FIRST-hit **a tabela ja emite, para toda entrada, o veredito MAIS SEVERO entre as regras que casam** — nenhuma permutacao das oito escalaria mais. NAO HA MASCARAMENTO A DESFAZER; os defeitos sao LACUNAS DE BANDA, e fechar qualquer uma exige afirmar um limiar ou uma conduta clinica que a tabela nao contem. Todos foram registrados como ACHADO com vetores de repro VERIFICADOS CONTRA O ARTEFATO VIVO pelo teste (um vetor que deriva da tabela quebra o build). **PEDIATRIA:** ACHADO-P1 — febre so e red flag em `idade_meses < 3` (:35); de 3 meses em diante NAO HA REGRA (febre moderada aos 4 meses -> `red_flag=false`/`CONTINUE`; o MESMO quadro aos 2 meses -> P1/EMERGENCIA). A tabela ADULTA mostra a FORMA de uma regra de febre com banda etaria (`>= 65`, :103-112) — a banda e a conduta pediatricas seguem sendo do pediatra. ACHADO-P2 — `desconhecida` e valor DECLARADO do dominio de `intensidade` (`adult:17`) e nao tem regra em nenhuma das duas tabelas: gravidade explicitamente desconhecida le `red_flag=false`. ACHADO-P3 (fail-safe so cobre "grave"), P4 (sem teto etario superior apesar da descricao), P5. **GESTANTE:** ACHADO-G1 — perda de liquido e contracoes regulares param em `IG < 37` e A TERMO NAO HA REGRA (perda de liquido moderada com 38 semanas -> `red_flag=false`; com 36 -> P1). ACHADO-G2 — pre-eclampsia so a partir de 20 semanas. ACHADO-G3 — movimentos fetais so a partir de 26 semanas (25 -> nenhum red flag; 26 -> emergencia). ACHADO-G4 (identico ao P2 — e questao de FAMILIA, nao de uma tabela), G5, G6. **Alargar uma banda existente com o wildcard `-` foi deliberadamente RECUSADO:** cada regra declara o seu escopo clinico na propria motivo ("pre-termo", "3o trimestre", "apos 20 semanas") e alarga-la faria a regra disparar fora do quadro que ela mesma nomeia — inventar conteudo clinico por omissao. Ratificar aqui significa algo mais estreito e mais util que o usual: o clinico REGISTRA que leu e dispositionou os achados; fechar qualquer um deles e edicao DIRETA da tabela viva, com conteudo que so ele pode escrever. Ratificar agora TAMBEM exige que o `tabela_viva.sha256` de CADA um dos dois manifestos ainda bata com os bytes de `triage_redflag_gestante.dmn`/`triage_redflag_pediatric.dmn` respectivamente no disco (#221) — uma edicao de qualquer uma das tabelas invalida a ratificacao pendente/concedida daquele manifesto ate re-revisao | medico obstetra (gestante) / medico pediatra (pediatrica) — mesmos revisores das linhas Phase 1 acima | `DRAFT — achados registrados, nenhuma regra proposta; requires human review before any deploy` |
| `spec/processes/dmn/frequency_zscore_threshold.dmn` (GAP-PERSP-DMN-DEAD-INPUTS) | A coluna `encounter_class` (rotulo doador "Encounter Class") era declarada e usada como `-` nas 5 rows — efeito ZERO, e nenhuma expressao FEEL de saida a lia. Foi REMOVIDA (a tabela passou a declarar so o que le; avaliacao no motor identica). **A pergunta que sobra e de SME, e nao foi respondida:** o limiar de z-score DEVE variar por classe/regime de atendimento (ambulatorio x pronto-socorro x internacao)? Se sim, os limiares por classe sao conteudo atuarial/medico que so o SME pode fixar — engenharia NAO os inventou (por isso a coluna saiu em vez de virar gate, ao contrario de GAP-CANCEL-2, onde o fato ja era coletado e o gate so tornava a row mais conservadora). Uma regra futura RATIFICADA pode reintroduzir a coluna; ate la a tabela decide por `z_score` e so por ele. Lembrete: os scores/limiares desta tabela seguem SINTETICOS (linha das 7 tabelas `fraude_scoring/*` acima) | medico-auditor + financas (mesmo revisor da linha das 7 tabelas `fraude_scoring/*`) | `DRAFT — coluna removida; regra por classe de atendimento NAO ratificada` |
| `spec/processes/dmn/unbundling_partial_bundles.dmn` (GAP-PERSP-DMN-DEAD-INPUTS) | A coluna `tuss_codes` (rotulo doador "TUSS Codes Present") estava morta nas DUAS pontas: `-` nas 4 rows, nao lida por nenhuma saida FEEL, e nao enviada pelo worker desde T1.5 (`_SCORING_INPUT_KEYS`, `src/maezo/tools/workers/fraude.py`). Foi REMOVIDA. **Pergunta de SME em aberto:** quais combinacoes/prefixos de codigos TUSS caracterizam desagrupamento (unbundling) alem do `bundle_group_id` ja usado? Isso e conteudo de auditoria medica/TUSS — engenharia NAO o inventou. Se uma regra futura RATIFICADA precisar dos codigos, o caminho esta documentado em `fraude.py`: declarar o `typeRef` de colecao correto e normalizar deliberadamente (join), NUNCA um cast cego (`str(["30101012"])` -> `"['30101012']"`) | medico-auditor + auditoria especial (mesmo revisor da linha das 7 tabelas `fraude_scoring/*`) | `DRAFT — coluna removida; regra por codigo TUSS NAO ratificada` |
| `spec/processes/dmn/upcoding_complexity_ceiling.dmn` — rotulo doador (GAP-PERSP-DMN-DEAD-INPUTS, ACHADO NAO FECHADO) | `in_encounter_class` (:28) ainda carrega o rotulo doador em ingles "Encounter Class", fora da convencao pt-BR `Rotulo (variavel)` do resto do corpus. Aqui a coluna e VIVA (a tabela realmente le `encounter_class`), entao nao pode ser removida — so renomeada. **NAO renomeei, de proposito, por DOIS motivos independentes:** (1) qualquer edicao nos bytes desta tabela quebra o binding `tabela_viva.sha256` de `upcoding-complexity-ceiling-shadow-candidate.yaml:111`, e `tests/unit/spec/test_shadow_candidates_common.py:423-424` declara em texto que atualizar so o digest "would be a deliberate defeat of the mechanism, not a fix" — enforcement por TESTE; (2) razao mais simples e mais forte, independente do binding: `upcoding_complexity_ceiling.dmn` e CODEOWNED (`.github/CODEOWNERS:176`, `@rodaquino-OMNI @Omni-Saude/security-team`) — enforcement por PLATAFORMA (revisao humana obrigatoria no PR, quer o binding de digest existisse ou nao). Renomear exige reautorar/rerrevisar o candidato — ato do dono da tabela (ADR-0028 §7), nao de engenharia | dono da tabela (medico-auditor + auditoria especial + financas) — decidir se o rename entra junto com a reautoria do candidato | `DRAFT — achado registrado, NAO corrigido (bloqueado pelo binding #221 e por CODEOWNERS:176)` |
| 21 colunas DMN mortas remanescentes no corpus (GAP-PERSP-DMN-DEAD-INPUTS — varredura) | A varredura generica das 62 DMNs achou 21 outras colunas com `-` em TODA row e nao lidas por nenhuma saida FEEL — congeladas e divulgadas em `tests/unit/spec/test_dmn_dead_inputs_fence.py::KNOWN_DEAD_INPUTS` (a fence recusa QUALQUER nova). NENHUMA foi investigada nem ratificada. As de maior peso aparente, para triagem do dono: `fraude_indicadores.in_indicadores_presentes` (a tabela que define a INTENSIDADE da investigacao declara ler a lista de indicadores e roteia so por `score_indicadores`/`entidade_tipo`), `auth_auto_approval.in_carater` (`carater_atendimento` — ja registrado na linha do auth_auto_approval acima), `recurso_eligibility.in_valor`/`in_reason_code`, `inadimplencia_status.in_meses_inadimplencia`, `glosa_triage.in_tipo_item` (= ACHADO-3 da linha M-4 abaixo). Cada uma exige a mesma pergunta de SME que as duas corrigidas: a regra DEVE ler a variavel, ou a coluna nunca deveria ter sido declarada? | por tabela: o revisor ja designado na linha do respectivo artefato; **excecao explicita (5 das 21 entradas nao tem linha de artefato propria nesta fila):** `cancel_sla.dmn.in_tipo_plano`, `cred_admissibility.dmn.in_tipo_prestador`, `cred_route.dmn.in_tipo_prestador` e `cred_route.dmn.in_origem_solicitacao` (as duas de `cred_route`), `pagto_sla.dmn.in_tipo_pagamento` — para essas o revisor e HERDADO da linha de FAMILIA do processo, nomeado aqui para que a heranca deixe de ser implicita: `cancel_sla.dmn` -> juridico/contratos + compliance (`SP-OP-CANCEL-001.md (+ cancel_*.dmn futuras)`, :136); `cred_admissibility.dmn` e `cred_route.dmn` -> regulatorio + juridico + arquitetura (`SP-OP-CRED-001.md (+ cred_*.dmn/cred.py futuros)`, :157); `pagto_sla.dmn` -> financas (`SP-OP-PAGTO-001.md (+ pagto_*.dmn/pagto.py futuros)`, :159) | `DRAFT — 21 achados registrados, NENHUM investigado nem corrigido` |

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

## GAP-FAB-NOTIF followup (1/2) — hitPolicy drift ainda aberto (GAP-CRED-7/GAP-ADEQ-9/GAP-PAGTO-9/GAP-REEMBOLSO-7)

`fix/fatos-fabricados-notificacao` (WP-FABRICATED-FACTS, R2) introduziu
`tests/unit/docs/test_contract_bpmn_dmn_citations.py::_HIT_POLICY_DRIFT_BASELINE` — um ratchet
que exige que nenhum CONTRATO NOVO reivindique `hitPolicy COLLECT/UNIQUE` para uma DMN
implantada como `FIRST`, mas grandfathera seis entradas pre-existentes fora do escopo do WP (so
`SP-OP-FRAUDE-001.md` foi corrigido nesta PR). O gatekeeper R2 (verificacao adversarial,
2026-09-02) re-derivou as seis com `grep -n hitPolicy spec/processes/dmn/<arquivo>.dmn` contra o
`main` e confirmou todas reais; nenhuma tinha linha propria nesta fila antes desta entrada. Quatro
das seis (a quinta, `reembolso_calculo`, esta sendo corrigida pela branch paralela
`fix/reembolso-consome-dmn`, fora desta PR) sao registradas abaixo:

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `docs/processes/contracts/SP-OP-CRED-001.md` (GAP-CRED-7 — hitPolicy drift, pre-existente, nao corrigido nesta PR) | `cred_prior_notice` (cabecalho `:127` reivindica `hitPolicy UNIQUE`) e `cred_sla` (cabecalho `:132` reivindica `hitPolicy UNIQUE`) — as duas DMNs shippadas sao `hitPolicy="FIRST"` (`spec/processes/dmn/cred_prior_notice.dmn:28`, nota de downgrade `:23-25`; `spec/processes/dmn/cred_sla.dmn:21`, nota `:18`). Achado D-B6 do revisor R1 (part-B verification report §6), re-confirmado pelo gatekeeper R2 desta PR contra o XML das duas DMNs. Rastreado em `_HIT_POLICY_DRIFT_BASELINE` (`tests/unit/docs/test_contract_bpmn_dmn_citations.py`) — remover as duas entradas de la quando o texto do contrato for corrigido para `FIRST`. Conteudo regulatorio RN 567 (descredenciamento) permanece DRAFT/verify — nao alterado por esta correcao textual pendente | engenharia (correcao textual `UNIQUE`→`FIRST`) + juridico/regulatorio (RN 567, ja pendente em outra linha desta fila) | `DRAFT — hitPolicy drift rastreado; texto do contrato ainda nao corrigido` |
| `docs/processes/contracts/SP-OP-ADEQUACAO-001.md` (GAP-ADEQ-9 — hitPolicy drift, pre-existente, nao corrigido nesta PR) | `adequacao_sla` (cabecalho `:176` reivindica `hitPolicy UNIQUE`) — a DMN shippada e `hitPolicy="FIRST"` (`spec/processes/dmn/adequacao_sla.dmn:29`, nota de downgrade `:25`). Achado D-B6 do revisor R1, re-confirmado pelo gatekeeper R2 contra o XML. Rastreado em `_HIT_POLICY_DRIFT_BASELINE` (`tests/unit/docs/test_contract_bpmn_dmn_citations.py`) — remover a entrada de la quando o texto do contrato for corrigido para `FIRST` | engenharia (correcao textual `UNIQUE`→`FIRST`) | `DRAFT — hitPolicy drift rastreado; texto do contrato ainda nao corrigido` |
| `docs/processes/contracts/SP-OP-PAGTO-001.md` (GAP-PAGTO-9 — hitPolicy drift, NOVO achado deste WP, nao corrigido) | `pagto_sla` (cabecalho `:168` reivindica `hitPolicy UNIQUE`) — a DMN shippada e `hitPolicy="FIRST"` (`spec/processes/dmn/pagto_sla.dmn:26`, nota de downgrade `:20-24`: "o contrato menciona UNIQUE, mas uma row catch-all sob UNIQUE colidiria com qualquer row especifica no engine"). NOVO achado deste WP (fora do escopo dos itens A-D corrigidos por `fix/fatos-fabricados-notificacao`); nao corrigido nesta PR. Rastreado em `_HIT_POLICY_DRIFT_BASELINE` (`tests/unit/docs/test_contract_bpmn_dmn_citations.py`) — remover a entrada de la quando o texto do contrato for corrigido para `FIRST` | engenharia (correcao textual `UNIQUE`→`FIRST`) | `DRAFT — hitPolicy drift rastreado; texto do contrato ainda nao corrigido` |
| `docs/processes/contracts/SP-OP-REEMBOLSO-001.md` (GAP-REEMBOLSO-7 — hitPolicy drift em `reembolso_sla`, NOVO achado deste WP, nao corrigido) | `reembolso_sla` (cabecalho `:138` reivindica `hitPolicy UNIQUE`) — a DMN shippada e `hitPolicy="FIRST"` (`spec/processes/dmn/reembolso_sla.dmn:24`, nota de downgrade `:21`). NOVO achado deste WP; nao corrigido nesta PR. **Nota de coordenacao:** a entrada irma `reembolso_calculo` no mesmo ratchet esta sendo corrigida pela branch paralela `fix/reembolso-consome-dmn` (fora desta PR, so o cabecalho de `reembolso_calculo` muda) — `reembolso_sla` permanece drift aberto mesmo apos aquele merge. Rastreado em `_HIT_POLICY_DRIFT_BASELINE` (`tests/unit/docs/test_contract_bpmn_dmn_citations.py`) — remover a entrada de la quando o texto do contrato for corrigido para `FIRST` | engenharia (correcao textual `UNIQUE`→`FIRST`) | `DRAFT — hitPolicy drift rastreado; texto do contrato ainda nao corrigido` |

---

## GAP-FAB-NOTIF followup (2/2) — GAP-INAD-8: `inadimplencia.notify_beneficiario` fato fabricado — **CORRIGIDO** (WP-FATOS-FABRICADOS slice 2)

`fix/fatos-fabricados-notificacao` (WP-FABRICATED-FACTS, R2) corrigiu dois fatos regulatorios
fabricados (CRED `dispatch_prior_notice`, ADEQUACAO `notify_coordenacao`, ambos zero-consumidores
— agora retornam `{}`) e deliberadamente NAO corrigiu um terceiro, de risco maior, tracked apenas
no comentario de codigo do ratchet AST `_FABRICATED_FACT_BASELINE["inadimplencia"]`
(`tests/unit/tools/workers/test_worker_handler_purity.py:190-202`). Esta linha promove esse
achado a uma entrada formal da fila.

> **STATUS 2026-09-03 — CORRIGIDO em `fix/fatos-fabricados-inadimplencia-notify` (WP-FATOS-FABRICADOS
> slice 2, R1).** A linha abaixo fica como REGISTRO do defeito e da sua analise; o que mudou:
> `notify_beneficiario` virou `dispatch_prior_notice` e retorna `{}` (`inadimplencia.py`);
> `handoff_rescisao` deixou de repassar `notificacao_previa_feita` e passou a DERIVA-lo de
> `comprovacao_notificacao_previa` (`_notificacao_previa_comprovada`) — de modo que o valor lido por
> `cancel_admissibility.dmn` e por `cancel.assess_admissibility` sai da comprovacao humana, nao de uma
> constante; e `cancel.validate_cancel`/`assess_admissibility` passaram a exigir `is True` estrito
> (fail-closed em `PENDENTE_NOTIFICACAO`, que NAO e negativa — o timer daquele ramo converge na User
> Task humana). A entrada `_FABRICATED_FACT_BASELINE["inadimplencia"]` foi REMOVIDA, como a catraca
> exige. **Correcao de uma premissa desta linha:** ela afirmava que "INADIMPLENCIA nao parece ter um
> campo equivalente [ao `comprovacao_notificacao_previa` do CRED] ja religado" — tem: o campo existe,
> e coletado em `UT_AnaliseInadimplencia` e ja era exigido fail-closed pelo guard de
> `_register_contract_suspension` (`inadimplencia.py`, `errors.append("comprovacao_notificacao_previa
> ausente (RN 593)")`; contrato `SP-OP-INADIMPLENCIA-001.md:98,157`). Era exatamente o sinal honesto
> que faltava, e e o que a correcao usa. **RN 593 e o art. 13, par. unico, II da Lei 9.656/98
> permanecem DRAFT/verify** — nenhuma ancora regulatoria foi confirmada por esta correcao; o
> sign-off medico/juridico/regulatorio continua PENDENTE.


| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `src/maezo/tools/workers/inadimplencia.py:325-342` (`notify_beneficiario` — GAP-INAD-8, fato fabricado, NAO corrigido nesta PR) | `notify_beneficiario` retorna incondicionalmente `{"notificacao_previa_feita": True, "notificacao_previa_registrada_em": "now"}` (`:339-342`) — mesmo formato dos dois fatos ja corrigidos, mais um placeholder literal `"now"` no lugar de um timestamp real. **Ao contrario dos dois casos corrigidos (zero consumidores), este fato tem DOIS consumidores DMN reais:** (1) `spec/processes/dmn/inadimplencia_status.dmn:37-38` (`in_notificacao_previa` le `notificacao_previa_feita` diretamente para rotear `AGUARDA_PURGA`/`PENDENTE_NOTIFICACAO`); (2) o valor e' carregado por `_HANDOFF_CARRY_KEYS` (`inadimplencia.py:552-567`, chave `notificacao_previa_feita` em `:558`) para o payload de start do CANCEL-001 via `handoff_rescisao`, onde `spec/processes/dmn/cancel_admissibility.dmn:63-64` TAMBEM le `in_notificacao_previa`, e `src/maezo/tools/workers/cancel.py:252-260` (`assess_admissibility`) ramifica sobre ele: para `tipo_solicitacao in ("inadimplencia", "for_cause_operadora")`, `if not validation.notificacao_previa_feita: roteamento = "PENDENTE_NOTIFICACAO"` (`:255-256`) senao `"SEGUE_ANALISE"` (`:258-259`); tambem aparece no `decision_basis` da trilha de auditoria ADR-0007 do start do CANCEL-001 (`inadimplencia.py:713`). **Efeito liquido:** como o fato e' sempre `True`, o ramo `PENDENTE_NOTIFICACAO` NUNCA pode disparar para um caso originado em inadimplencia, em nenhum dos dois processos — supressao silenciosa de um sinal de compliance da RN 593 (suspensao/rescisao por inadimplencia, efeito adverso ao beneficiario). **Escopo/owner recomendado:** pacote R1 separado (`WP-FATOS-FABRICADOS` slice 2) — toca `inadimplencia.py` + `cancel.py` + duas DMNs, exige prova de regressao nos dois processos, e exige definicao (com sign-off regulatorio RN 593) de como seria um sinal honesto de "beneficiario efetivamente notificado" para o fluxo INADIMPLENCIA, ja que — ao contrario do CRED, que tem o campo `comprovacao_notificacao_previa` confirmado por humano — INADIMPLENCIA nao parece ter um campo equivalente ja religado alimentando esta variavel; trocar a constante ingenuamente (para `False`/removida) poderia travar toda rescisao por inadimplencia em `PENDENTE_NOTIFICACAO` sem caminho adiante, uma regressao funcional, nao so uma correcao de honestidade. RN 593 permanece DRAFT/verify — nao alterado por esta entrada. Rastreado em `_FABRICATED_FACT_BASELINE["inadimplencia"]` (`tests/unit/tools/workers/test_worker_handler_purity.py:190-202`) — remover a entrada de la quando corrigido | medico auditor + juridico/regulatorio (RN 593) + arquitetura (design do sinal honesto de notificacao para INADIMPLENCIA) | `DRAFT — requires human review (RN 593) before any fix; pacote R1 separado recomendado (WP-FATOS-FABRICADOS slice 2)` |

---

## WP-CONTRATOS-SYNC — fechamento dos 7 drifts contrato/test-spec vs artefato vivo

`fix/contratos-sync-artefatos-vivos` (WP-CONTRATOS-SYNC, R2) sincronizou contratos/test-specs com
os artefatos vivos para os gaps `PERSP-CONTRACT-FUNCS`, `PERSP-C5-CANCEL-FILENAME`,
`PERSP-LGPD-ROLE`, `PERSP-B5-HITPOLICY`, `PERSP-B5-INPUTS`, `PERSP-B5-TESTSPEC-AUTH` e
`CONTRACT-HITPOLICY-DRIFT`. Esta entrada NAO reabre nem edita as linhas acima desta PR (append-
only) — registra o desfecho para quem consultar as entradas antigas de `GAP-CRED-7`/`GAP-ADEQ-9`/
`GAP-REEMBOLSO-7` (secao "GAP-FAB-NOTIF followup (1/2)" acima).

| Artefato | O que foi corrigido | Revisor | Status |
|---|---|---|---|
| `docs/processes/contracts/SP-OP-CRED-001.md` (GAP-CRED-7, hitPolicy `cred_prior_notice`/`cred_sla`) | Cabecalhos corrigidos de `UNIQUE` para `FIRST` (as DMNs shippadas `spec/processes/dmn/cred_prior_notice.dmn:28` e `spec/processes/dmn/cred_sla.dmn:21` ja eram `FIRST`) — texto apenas, nenhuma DMN alterada. Entrada removida de `_HIT_POLICY_DRIFT_BASELINE` (`tests/unit/docs/test_contract_bpmn_dmn_citations.py`); `test_no_new_contract_claims_collect_or_unique_for_a_dmn_actually_deployed_as_first` PASSED confirma a ausencia de regressao | docs-verifier (R2) | `RESOLVIDO — texto do contrato corrigido para FIRST nesta PR` |
| `docs/processes/contracts/SP-OP-ADEQUACAO-001.md` (GAP-ADEQ-9, hitPolicy `adequacao_sla`) | Cabecalho corrigido de `UNIQUE` para `FIRST` (`spec/processes/dmn/adequacao_sla.dmn:29` ja era `FIRST`) — texto apenas. Entrada removida de `_HIT_POLICY_DRIFT_BASELINE` | docs-verifier (R2) | `RESOLVIDO — texto do contrato corrigido para FIRST nesta PR` |
| `docs/processes/contracts/SP-OP-REEMBOLSO-001.md` (GAP-REEMBOLSO-7, hitPolicy `reembolso_sla`; CONTRACT-HITPOLICY-DRIFT) | Cabecalho corrigido de `UNIQUE` para `FIRST` (`spec/processes/dmn/reembolso_sla.dmn:24` ja era `FIRST`) — texto apenas. Entrada removida de `_HIT_POLICY_DRIFT_BASELINE`. **`reembolso_calculo` (a entrada irma no mesmo ratchet) permanece FORA DO ESCOPO desta PR** — pertence a branch paralela `fix/reembolso-consome-dmn` — e continua grandfathered em `_HIT_POLICY_DRIFT_BASELINE` | docs-verifier (R2) | `RESOLVIDO (reembolso_sla) — reembolso_calculo permanece aberto, fora de escopo` |
| `docs/processes/contracts/SP-OP-PAGTO-001.md` (GAP-PAGTO-9, hitPolicy `pagto_sla`) | **NAO CORRIGIDO nesta PR** — `SP-OP-PAGTO-001.md` e propriedade das branches paralelas PR-3/PR-4 do programa de perspectiva (`WP-PERSP-CONTAS-RECURSO-EXEC`/`-DECISAO`); o brief de WP-CONTRATOS-SYNC proibe explicitamente editar este arquivo. Continua grandfathered em `_HIT_POLICY_DRIFT_BASELINE` ate essa branch fechar a correcao | — | `DEFERIDO — aguarda a arvore PR-4 (SP-OP-PAGTO-001.md fora do escopo desta WP)` |
| `docs/processes/contracts/SP-OP-CRED-001.md` (PERSP-B5-INPUTS, inputs/saida de `cred_admissibility`) | `in:` completado com `indicio_irregularidade_sinalizado` (6º input; faltava — `spec/processes/dmn/cred_admissibility.dmn:53-55`); `out:` do dominio de `roteamento` completado com `CLERICAL_CREDENCIAR` (faltava; `cred_admissibility.dmn:91-101`, consumido por `spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn:689`) — texto apenas, nenhuma DMN/BPMN alterada | dmn-semantics-verifier (R1) | `RESOLVIDO nesta PR` |
| `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md` (PERSP-B5-INPUTS, inputs/saida de `ans_sla`) | Removida a alegacao de `origem_envio` como input e `fonte_regulatoria` como output de `ans_sla` — a DMN shippada (`spec/processes/dmn/ans_sla.dmn`) so declara `report_type` como input (`:34-36`) e `sla_analise`/`sla_alerta` como outputs (`:37-38`); confirmado tambem `grep -rl origem_envio spec/processes/dmn/*.dmn` -> 0 arquivos. `origem_envio` e real como variavel de processo (seed no start) e condicao de gateway BPMN (`:611`), nunca como input de DMN — texto apenas | dmn-semantics-verifier (R1) | `RESOLVIDO nesta PR` |
| `docs/processes/contracts/SP-OP-LGPD-DSR-001.md` (PERSP-LGPD-ROLE) | Adicionada secao "Papel LGPD (controlador/operador) — DRAFT/verify": registra que a cadeia SP-OP-LGPD-DSR-001 nunca declarou `controlador`/`operador` (art. 5 VI/VII; 0 ocorrencias antes desta PR em contrato/BPMN/DMN/worker) e que o papel de controladora e' INFERIDO do comportamento (`ST_ExecutarRequisicao` bpmn:270, `ST_EnviarResposta` bpmn:277, `ESP_SlaGlobal` bpmn:307-311) — marcado explicitamente `DRAFT/verify pendente do DPO`, nunca como fato ratificado. Nenhum artefato `spec/` (BPMN/DMN) foi editado (fora do escopo/permissao desta WP) | regulatory-verifier (R1) | `DRAFT/verify — declaracao textual adicionada ao contrato; ratificacao formal do papel permanece pendente do DPO (WP-GOVERNANCA-DPO)` |
| `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md` + `SP-OP-ANS-CRON-001.md` (PERSP-CONTRACT-FUNCS) | As tres funcoes fantasma (`_competencia_from_anchor`, `_ans_cron_competencia`, `_nip_protocolo_origem`) e o modulo fantasma `notifications_bridge/consumer.py` ja tinham sido corrigidos por `fix/fatos-fabricados-notificacao` (commit `71dd4da`, GAP-FAB-NOTIF) ANTES desta PR comecar — confirmado nesta PR por `grep -rn 'def _competencia_from_anchor\|def _ans_cron_competencia\|def _nip_protocolo_origem' src/` (0 linhas) e `find src -iname consumer.py` (0 arquivos). Esta PR apenas ACRESCENTA, nos dois pontos que citam `GAP-ANS-1`, o apontamento para o gap/WP correntes do registro (`ANS-CRON-DEAD-CODE`/`WP-ANS-CRON-COMPETENCIA`), para que a declaracao nao fique presa a um tag antigo | docs-verifier (R2) | `RESOLVIDO (substancialmente por commit anterior 71dd4da) — esta PR so atualiza o apontamento de gap` |
| `docs/processes/contracts/SP-OP-CANCEL-001.md` (PERSP-C5-CANCEL-FILENAME) | Ja corrigido por `fix/fatos-fabricados-notificacao` (commit `71dd4da`) ANTES desta PR comecar — confirmado nesta PR por `sed -n '4p' docs/processes/contracts/SP-OP-CANCEL-001.md` (cita `..._Cancelamento_Contrato.bpmn`, o arquivo real) e pelas 14 suites verdes de `tests/unit/docs/test_contract_bpmn_dmn_citations.py`. Nenhuma edicao necessaria nesta PR | docs-verifier (R3) | `JA RESOLVIDO por 71dd4da antes desta PR — nenhuma acao necessaria` |
| `docs/processes/test-specs/SP-OP-AUTH-001.md` (PERSP-B5-TESTSPEC-AUTH) | Linhas `:10`, `:50` e `:55` (achado adicional na mesma varredura, mesma classe de drift) reescritas: `dut_atendida`/`dentro_teto_l2`/`rede_credenciada` substituidos por `auto_criteria_verificado`/`criterio_tecnico_ok`/`criterio_financeiro_ok`/`criterio_regulatorio_ok`/`criterio_contratual_ok` (os inputs reais de `auth_auto_approval` v0.2.0 — `spec/processes/dmn/auth_auto_approval.dmn:60-77`), conforme o contrato ja documentava (`SP-OP-AUTH-001.md:47-49`) — texto apenas | docs-verifier (R2) | `RESOLVIDO nesta PR` |
## WP-COREOGRAFIA-XPROC — PERSP-ADEQ-CRED-HANDOFF (handoff real) + PERSP-NETBRIDGE (bridge fantasma, docs/spec)

Duas linhas do `GAP-REGISTER.md` (`:121-122`) fechadas por este WP (worktree `coreografia-xproc`,
branch `fix/coreografia-xproc-handoff-adequacao-cred`):

**PERSP-ADEQ-CRED-HANDOFF (terceira variante de fato fabricado — junto de GAP-FAB-NOTIF item B/A
acima):** `adequacao.execute_remediation` (`ST_StartCredenciamentoL3`, topico
`operadora.adequacao.start_credenciamento`) retornava incondicionalmente `{handoff_credenciamento:
True, processo_destino: "SP-OP-CRED-001"}` sem nunca chamar `start_process_idempotent` — BPMN
(`SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn:247-249` antes da correcao) e contrato
(`SP-OP-ADEQUACAO-001.md:152` antes da correcao) afirmavam "dispara SP-OP-CRED-001". Corrigido:
agora chama o chokepoint fenced de verdade, com business key `CRED-{tenant}-{prestador_id}`
(identica a `fraude._cred_business_key`/`notification_bridge._cred_business_key`). **Achado novo,
nao coberto por nenhuma linha anterior desta fila:** o handoff so pode executar quando um
`prestador_id` candidato ja foi identificado — e ESTA fonte (quem/o que identifica o candidato
antes deste task disparar) **nao e definida por nenhum contrato**. A propria contrato ADEQUACAO ja
tinha uma pendencia adjacente ("a fonte cadastral de regiao_saude/especialidade do prestador no
start de CRED", secao Pendencias) que aponta na mesma direcao sem a resolver.

**PERSP-NETBRIDGE (nao-novo como fato — ja registrado nas entradas Wave-1B/GR-B2 acima; o defeito
vivo era os CONTRATOS/BPMN nunca terem sido sincronizados com esse registro):** `network_change_bridge`
era afirmada como ponte de runtime viva em 2 contratos e 2 BPMN
(`SP-OP-ADEQUACAO-001.md:55-56,74,107,141,256,270`, `SP-OP-CRED-001.md:70,96`,
`SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn:55,76,84`, `SP-OP-CRED-001_Descredenciamento.bpmn:67`) —
o modulo nao existe (`grep -rn "network_change_bridge" src/` -> 1 hit, docstring em `src/maezo/agents/carolina/graph.py:101`; `find src -name '*network_change*'` -> 0 resultados). Corrigido: toda
assercao agora diz a verdade (o PAYLOAD do fato `network_changed` esta harmonizado; o CONSUMIDOR
nao existe) e cita **AF-01** como a decisao do owner que resolve o futuro (construir a ponte,
adotar outro mecanismo, ou aceitar que ADEQUACAO nao tem starter de producao hoje).

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `docs/processes/contracts/SP-OP-ADEQUACAO-001.md` (novo campo `prestador_id`/`tipo_prestador`, secao Variaveis de entrada) — PERSP-ADEQ-CRED-HANDOFF | Este WP nao define a fonte de `prestador_id` (quem/o que identifica um prestador candidato para fechar o gap da celula ANTES de `ST_StartCredenciamentoL3` disparar) — hoje NENHUM worker de ADEQUACAO resolve esse valor; sem ele, o handoff recusa (fail-closed, `ERR_ADEQUACAO_SEM_PRESTADOR_CANDIDATO`). Precisa de decisao de produto/arquitetura: um novo worker de prospeccao (analytics/Andre?), um campo semeado por humano na `UT_DecisaoFallback`/dossie, ou uma integracao com a base de rede | produto + arquitetura (base de rede) | `DRAFT — requires human review before any deploy` |
| `docs/processes/contracts/SP-OP-ADEQUACAO-001.md`, `SP-OP-CRED-001.md`, `spec/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn`, `SP-OP-CRED-001_Descredenciamento.bpmn` (`network_change_bridge`) — PERSP-NETBRIDGE, ja tratado como AF-01/Wave-1B acima; entrada aqui SO para registrar que o texto dos 4 artefatos foi corrigido nesta PR (deixou de afirmar a ponte como viva) | A decisao de negocio (construir `network_change_bridge`, adotar outro mecanismo de starter para SP-OP-ADEQUACAO-001, ou aceitar a lacuna) continua em aberto — ver a entrada Wave-1B acima e **AF-01** (registro `GAP-REGISTER`, linha `AF-01`, P0, `owner-decision`, fora do escopo docs/spec deste WP) | arquitetura + PO (mesma revisao de AF-01) | `DRAFT — requires human review before any deploy (texto corrigido; decisao de negocio pendente)` |


---

## PERSP-C5-ANSCRON-TESTSPEC — colisao de merge com `feat/ans-cron-timers-por-report-type` (regra registrada pelo verificador)

Esta branch (`fix/docs-hygiene-contadores-runbooks`) e a branch paralela
`feat/ans-cron-timers-por-report-type` (remodelagem dos timers de SP-OP-ANS-CRON-001 por
`report_type`) tocam os mesmos tres arquivos de forma que `git merge-tree` confirma como conflito
real (nao mecanico) em um deles. Isto NAO e um achado de conteudo — nao mexe em
`docs/processes/test-specs/SP-OP-ANS-CRON-001.md` nem em `catalog.md`, so registra a regra de
merge que o verificador independente (VER-DOCS-HYG) derivou, para quem integrar as duas branches
depois desta:

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| Colisao de merge `fix/docs-hygiene-contadores-runbooks` × `feat/ans-cron-timers-por-report-type` (3 arquivos, `git merge-tree --write-tree --merge-base=71dd4da HEAD feat/ans-cron-timers-por-report-type`, reproduzido por VER-DOCS-HYG) | **Regra de merge, nao correcao de conteudo — quem integrar decide, nao esta branch:** (1) `docs/processes/test-specs/SP-OP-ANS-CRON-001.md` — conflito add/add, as duas branches criam o arquivo do zero. A versao desta branch (75 linhas) documenta o estado ATUAL/pre-remodelagem, incl. `FINDING #1 — topicos registrados por ans_cron.py sao inalcancaveis a partir do BPMN` (achado de codigo-morto). A versao de `feat/ans-cron-timers-por-report-type` (222 linhas) documenta o estado POS-remodelagem onde esse mesmo achado esta corrigido. Manter as duas e autocontraditorio (uma afirma que os topicos sao inalcancaveis, a outra que foi corrigido) — **a versao ans-cron deve vencer INTEIRA; a versao desta branch deve ser descartada**, nao porque esta errada hoje, mas porque documenta um estado que a outra branch ativamente remedia. (2) `docs/processes/catalog.md` — as duas branches adicionam uma linha para `SP-OP-ANS-CRON-001` na mesma posicao da tabela com status/rodape diferentes (`DRAFT` nesta branch vs `modelado` em ans-cron) MAIS rodapes independentes sem sobreposicao real: esta branch acrescenta o rodape `SP-OP-REEMBOLSO-001**` (PERSP-REEMBOLSO-BINDING) que ans-cron nao toca; ans-cron acrescenta `PERSP-C5-MONITOR-PROGRAMA` que esta branch nao toca. **Merge manual: tomar a linha/paragrafo ANS-CRON de ans-cron, manter o rodape REEMBOLSO desta branch, manter o rodape MONITOR-PROGRAMA de ans-cron** — tres blocos independentes, nenhum e descartado. (3) `docs/evidence-ledger.md` — as duas branches so acrescentam linhas (append/append) apos a mesma ultima linha; conflito puramente mecanico, concatenar os dois blocos de linhas em qualquer ordem, nenhum bloco referencia o outro. | orquestrador/integrador (decisao de ordem de merge; nenhuma decisao de conteudo tecnico/regulatorio pendente) | `NOTA — regra de merge registrada, nao acao pendente; aplicavel so no momento em que as duas branches forem integradas` |

---

## Auditoria 09 — achado 9.4 residuo: `WHATSAPP_PHONE_NUMBER_ID` nao e injetado por nenhum deployment

`fix/whatsapp-token-vazamento`: o achado 9.4 (token WABA no PATH da URL) foi fechado trocando a URL
para `POST {base_url}/{phone_number_id}/messages` com o token so no header `Authorization`
(`src/maezo/tools/mcp_whatsapp/server.py:168,177`) e uma recusa fail-closed quando
`phone_number_id` esta vazio (`:163-164`). O residuo e OPERACIONAL, nao de codigo, e esta declarado
aqui para nao virar um brick silencioso: **reply path inoperative in Helm until
`WHATSAPP_PHONE_NUMBER_ID` is provisioned (owner-gated, see OWNER-DECISIONS)**.

Fato de deploy, verificado: `deploy/helm/maezo-tenant/templates/deployment-webhook-receiver.yaml`
injeta `WHATSAPP_TOKEN` (`:38`), `WHATSAPP_APP_SECRET` (`:47`) e `WHATSAPP_VERIFY_TOKEN` (`:52`) —
e nenhum `WHATSAPP_PHONE_NUMBER_ID`. `docker-compose.yml:252-253` injeta dois dos tres; so
`.env.example:72` (dev local) declara o phone-number id. Consequencia hoje: TODA resposta da Helena
pelo caminho vivo (`src/maezo/platform/webhooks/service.py:123` -> `HelenaDispatcher` -> `dispatch.py:122`
`_ScopedWhatsAppSender.send` -> `server.py:111 send_message`) recusa em `server.py:163-164`. Esse e
o modo de falha CORRETO (nunca um envio com URL malformada ou credencial vazia), mas e' uma recusa
de 100% do trafego de resposta ate a variavel existir. Nada em `deploy/` foi tocado por este pacote
— `deploy/`, `.github/` e `spec/policies/` sao owner-gated.

Nota de honestidade sobre a referencia: `OWNER-DECISIONS` e o registro de decisoes do dono do
pacote de gap-closure de 2026-09-02; nao existe arquivo com esse nome nesta arvore hoje. A linha
acima e' o registro em-repo do item ate que esse registro exista.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `deploy/helm/maezo-tenant/templates/deployment-webhook-receiver.yaml` (env do container) + ExternalSecret `maezo-whatsapp-config` | Provisionar `WHATSAPP_PHONE_NUMBER_ID` (o id Graph do numero remetente da WABA — dado de configuracao, NAO um segredo) e injeta-lo no deployment, na mesma forma dos tres `WHATSAPP_*` ja presentes (`:38,47,52`). Decidir tambem se ele entra como `value:` literal por tenant ou como chave do secret. Enquanto nao entrar, `send_message` recusa toda resposta da Helena (fail-closed, `server.py:163-164`) | dono/ops (mudanca em `deploy/` e owner-gated; sem conteudo clinico ou regulatorio) | `PENDENTE — caminho de resposta inoperante em Helm ate o provisionamento` |
## WP-COMPOSICAO-V2 fatia 1 — raizes de composicao (AF-13 / ALERTS-WITHOUT-METRICS-a / WORKER-METRICS-COVERAGE / AF-12)

O que esta fatia fechou e' WIRING: `setup_observability` passou a ter chamadores de producao nas
tres raizes de daemon (`gateway/service.py`, `runtime/worker_runtime/service.py`,
`runtime/agent_runtime/service.py`); `maezo_tool_calls_total`/`maezo_agent_errors_total` passaram a
ser incrementados; os topicos servidos por handler cru voltaram a emitir as metricas M11; e
`task_kind` atravessa `InferenceProvider.generate()` a partir do `model:` do `agent.yaml` nas DUAS
raizes que servem agente — `runtime/agent_runtime/service.py` e `platform/webhooks/service.py`.
Sao, portanto, QUATRO raizes de composicao tocadas, nao tres.

O que NAO foi decidido aqui, e por que cada item e' humano — nenhum deles bloqueia o que foi
entregue, todos limitam o quanto ele vale:

| Artefato | O que precisa de decisao humana | Revisor | Status |
|---|---|---|---|
| `deploy/observability/alert-rules.yml` — `MaezoSLAAgentErrorRateHigh` (`:36-52`) | **Rotulos dos dois contadores de agente.** `maezo_agent_errors_total` e `maezo_tool_calls_total` ficaram SEM rotulo, deliberadamente: a expr do alerta DIVIDE um pelo outro, e o casamento de vetores do PromQL exige conjuntos de rotulos IDENTICOS — dar `{tool}` so' ao denominador produziria vetor vazio, isto e', um alerta que nunca dispara (exatamente o defeito que ALERTS-WITHOUT-METRICS-a existe para fechar). Um rotulo `{agent}` em AMBOS e' viavel e mais util, mas exige editar a regra, e `deploy/` e' owner-gated neste pacote de trabalho. Pinado por `test_alert_metrics_fence.py::test_the_two_agent_counters_are_label_free_so_the_ratio_alert_can_match` | SRE/observabilidade + owner (`deploy/` gated) | `PENDENTE — decisao de owner; contadores label-free ate la` |
| `deploy/observability/alert-rules.yml` — `MaezoDeadLetterBacklog`/`MaezoDeadLetterGrowth` (`:121-151`) e `MaezoLifecycleJobFailed` (`:171-189`) | **Exporters ausentes (fatia ALERTS-WITHOUT-METRICS-b, do owner).** `maezo_dead_letter_queue_size` e `kube_job_status_failed` sao series de EXPORTER (Kafka/JMX e kube-state-metrics), nao telemetria da aplicacao — emiti-las de `src/` seria fabricar um fato de infraestrutura. Declaradas explicitamente na allowlist da fence (`test_alert_metrics_fence.py::EXTERNAL_ALERT_METRICS`) em vez de silenciosamente ignoradas. Fechar exige alvo de scrape em `deploy/`, owner-gated | SRE/plataforma + owner (`deploy/` gated) | `DECLARADO EXTERNO — nao emitido, nao fabricado` |
| `src/maezo/runtime/inference.py` — `InferenceSettings.model` / `_resolve_task_model` | **Quais modelos cada tier nomeia (ADR-0009 §2/§3).** Hoje ha' UM modelo configurado e nenhum mapa por tier, entao `fast` e `frontier` resolvem para o mesmo modelo — resolucao fail-closed, registrada em log e no contador `maezo_llm_tier_resolution_total{resolution="modelo_unico"}`, nunca um default silencioso para OUTRO modelo. Escolher os ids por tier e' decisao de owner/financeiro (ADR-0009: "precos variam 10x") e de qualidade (o gate de eval golden por agente, ADR-0009 §4, precisa rodar contra o modelo novo antes de promover). Nenhuma linha de codigo muda quando a decisao vier: e' config | owner/financeiro + engenharia de agentes (gate de eval golden) | `PENDENTE — tier declarado e roteado; valores por tier nao decididos` |
| `spec/agents/*/agent.yaml` — bloco `model:` | **Tier `batch` nao e' declarado por nenhum agente.** ADR-0009 §2 nomeia tres tiers ("lote -> batch tier"); os 10 agentes + `_template` declaram apenas `task_default` e `reasoning`. O vocabulario aceita `batch` (`inference.MODEL_TIERS`), mas NENHUM `agent.yaml` foi editado para introduzi-lo: qual trabalho pode ser diferido para lote e' decisao de produto/assistencial, nao detalhe de implementacao, e inventar a declaracao recriaria a config-morta que AF-12 acabou de remover | PO + gestao assistencial | `PENDENTE — vocabulario pronto, nenhuma declaracao inventada` |
| `src/maezo/platform/observability.py` — `setup_observability` (FORMATO DE LOG) | **Mudanca de formato de log em producao, ja' aplicada — DESCRICAO CORRIGIDA apos a revisao (MAJOR-1).** A versao anterior desta linha dizia que ligar as raizes "troca a configuracao DEFAULT do structlog pelo `ConsoleRenderer` + `PrintLoggerFactory` do modulo". Isso e' **materialmente falso**: `ConsoleRenderer` sobre `PrintLoggerFactory` JA' E' o default do structlog 25.5.0 — a familia de renderer nunca mudou, e SRE teria assinado uma descricao que nao corresponde ao diff. O delta que existia de fato era (a) `add_log_level` derrubado — nenhuma linha carregava token de severidade, (b) `merge_contextvars` derrubado — latente, nada em `src/` usa contextvars hoje, (c) cores ANSI FORCADAS independentemente de TTY (o default de PARAMETRO do `ConsoleRenderer` e' `colors=True`, ao contrario do renderer que o structlog constroi para si), e (d) timestamp local -> ISO-8601/UTC. (a) e (c) quebravam `kubectl logs \| grep` de duas maneiras: nivel e CAMPO (os escapes ficam ENTRE chave, `=` e valor, entao `topic=t` deixa de ser substring). **(a), (b) e (c) foram corrigidos na raiz** — processors de volta na cadeia, cores perguntando ao stdout se ele e' terminal, `NO_COLOR` honrado — e ficam pinados por `test_observability_bootstrap.py`. **O que resta para SRE:** (i) o timestamp passa a ser ISO-8601/UTC em vez de local — deliberado, nao byte-identico; (ii) o nivel e' renderizado em MINUSCULAS entre colchetes (`[error    ]`), o que ja' era verdade do default pre-wiring, entao runbook com `grep ERROR` precisa de `-i` (`docs/runbooks/devops-stack.md:318` corrigido); (iii) trocar por um renderer JSON continua decisao aberta — nenhum parser deste repo depende de JSON (o collector de dev nao tem receiver `filelog`, o chart nao tem sidecar de parsing e o ECS usa o driver `awslogs` puro) | SRE/observabilidade | `APLICADO com o delta corrigido — add_log_level e cores TTY-aware restaurados; timestamp UTC e renderer JSON seguem decisao aberta` |
| `src/maezo/platform/observability.py` — `MAEZO_LOG_LEVEL` | **Variavel que era lida, logada e nunca aplicada (revisao, minor-7) — agora e' real.** `structlog.stdlib.BoundLogger` sobre `PrintLoggerFactory` nao filtra nada, entao `MAEZO_LOG_LEVEL=ERROR` imprimia todo DEBUG do mesmo jeito; o wiring e' o que colocou essa docstring no caminho de producao. Passou a usar `make_filtering_bound_logger`, com default **`NOTSET` (sem filtragem)** — identico ao comportamento pre-AF-13 e ao proprio default do structlog, porque ligar o wiring nao pode, ele mesmo, comecar a descartar linha de log. Valor invalido LEVANTA (o bootstrap contem e deixa `observability_configured` vermelho) em vez de cair para INFO. **Decisao de SRE:** se os tres daemons devem passar a declarar `MAEZO_LOG_LEVEL=INFO` no `deploy/` (owner-gated) — hoje nenhuma task definition de `deploy/aws-ecs/envs/dev-sa-east-1/` a define | SRE/observabilidade + owner (`deploy/` gated) | `IMPLEMENTADO — default sem filtragem; declarar o nivel em deploy/ e' decisao pendente` |
## Completude da classificacao de PHI por NOME (GAP-DU-07) — perguntas ao DPO

A redacao de PHI da plataforma e **ancorada em nome**: `redact_phi_vars` so age quando a CHAVE
esta entre os 8 nomes de `PHI_PROCESS_VARS` (`src/maezo/tools/workers/phi_vars.py:52-61`) e o
`LogScrubber` quando esta entre os 4 de `PHI_FIELDS` (`src/maezo/gateway/pseudonymizer.py:41-47`).
Os dois conjuntos vieram do doador e sao CI-enforced — mas nada jamais provou que esses 12 nomes
COBREM as variaveis de processo que os 16 BPMN e 62 DMN de fato declaram e leem.

A cerca `src/maezo/platform/validation/phi_completeness.py` mecaniza a prova: extrai todo nome de
variavel de dez superficies de `spec/processes/` com proveniencia `arquivo:linha`, classifica em
tres baldes (LISTED / SHAPE-SUSPECT-UNLISTED / CLEAN) e **falha fechado** em todo nome PHI-shaped
que nao esteja listado NEM disposto. Medicao sobre o `spec/` vivo: **6 LISTED, 9
SHAPE-SUSPECT-UNLISTED, 313 CLEAN** (328 nomes, **1623 ocorrencias** — pinado por
`test_the_occurrence_count_is_pinned`; uma versao anterior desta secao dizia 1637, numero que
nenhum estado da branch produz e que nenhum teste sustentava). **(corrigido 2026-09-03: era 314
CLEAN / 329 nomes / 1616 ocorrencias — o trem onda-0 mexeu no corpus depois da medicao original:
`#276` removeu a coluna morta `tuss_codes`, unica ocorrencia do nome em `spec/`, entao ele SAI do
corpus inteiro — CLEAN 314->313, nomes 329->328; `#271` acrescentou net +7 ocorrencias de nomes
ja-corpus ao achatar `calculo.*` em `SP-OP-REEMBOLSO-001` — 1616-1(tuss_codes)-1(encounter_class,
mesma PR de #276, nome sobrevive)+9(calculo/valor_calculado_tabela_cents/multiplo_tabela_aplicado/
fonte_tabela) = 1623. Detalhe completo em `CORPUS_DELTA_LOG`,
`tests/unit/platform/test_validation_phi_completeness.py:261-327`, e na linha GAP-DU-07 mais
recente de `docs/evidence-ledger.md`.)**

**A classificacao usa DOIS sinais, e so um deles le o nome.** (a) SINAL ESTRUTURAL: um
`camunda:formField` cujo tipo nao e limitado e que nao declara dominio `camunda:value` e uma caixa
livre onde um humano digita prosa — SUSPECT independentemente de como se chame. Medido no corpus
vivo: 14 ocorrencias, 5 nomes, 4 ja LISTED e **exatamente um novo (`auditor_id`)** — todo o custo
de ruido do sinal hoje. O mesmo vale para uma entrada do rol `VARIAVEIS DE ENTRADA` cuja propria
anotacao diz `(texto livre)`; esse braco dispara ZERO vezes hoje e o zero esta pinado por teste.
(b) VOCABULARIO PHI-SHAPE, agora com casamento por RADICAL (fecha `laudos`/`resumos`/
`justificativas`/`diagnostica_*`, que eram evasoes de um caractere). **A recall foi MEDIDA e esta
declarada** (`TestClassificationRecall`, bateria adversarial ponta a ponta sobre uma copia do
BPMN vivo): todos os nomes PHI-shaped da bateria sao pegos; os 5 deliberadamente limpos
(`numero_lote_tiss`, `prazo_dias`, `codigo_tuss`, `valor_cents`, `cnpj_prestador`) seguem limpos.
Residuo declarado, ainda NAO pego por nome: `descricao_procedimento`, `sexo`, `gestante`, `idade`
(este ultimo deixado de fora com custo medido — incluiria `idade_anos`/`idade_meses`/
`idade_gestacional_semanas`, tres criterios DUT).

Tres observacoes de fato, registradas sem interpretacao:

- `laudo` e `diagnostico` (`phi_vars.py:60-61`) **nao sao declarados por artefato nenhum** de
  `spec/` — sao heranca do doador que os 16 BPMN nunca usam. **Isso NAO os torna entradas mortas**:
  `phi_vars.py:48-51` diz que o conjunto e "kept aligned with the donor's `PHI_PROCESS_VARS` so the
  invariant's coverage does not drift between codebases", e `:24` os enumera entre o conteudo
  clinico que o conjunto existe para cobrir — remove-los criaria exatamente o drift que o docstring
  proibe. Custo de mante-los: zero (redigir uma chave que nunca aparece e no-op). Sao defesa em
  profundidade PRE-POSICIONADA: se um BPMN futuro declarar `laudo`, ele nasce coberto.
  Nenhum dos 4 `PHI_FIELDS` (`cpf`/`nome`/`telefone`/`email`) aparece como variavel de processo: e
  a fronteira do gateway funcionando (uma variavel carrega `beneficiario_pseudo_id`, nunca um CPF).
- Nenhum dos candidatos do relatorio de auditoria (`data_nascimento`, `cns`, `nome_mae`,
  `endereco`, `cpf_titular`) existe hoje em `spec/`. A cerca os reconhece pela heuristica — se um
  deles for adicionado a qualquer BPMN/DMN/manifesto, o build fica vermelho ate haver disposicao.
- **Limite de escopo declarado (nao-objetivo):** a varredura le a arvore `spec/processes`, entao
  variavel que NENHUM artefato declara e um worker inventa em runtime e invisivel por construcao —
  `contas.py:330` devolve `total_glosado_candidato_brl` e `:334` `impacto_percentual`, nenhuma
  declarada em `spec/`. Elas nao estao fora da PERGUNTA da DU-07 (trafegam a mesma borda
  worker->engine em que `redact_phi_vars` age, e um nome que aquele conjunto nao carrega tambem nao
  e redigido la) — estao fora da ENTRADA desta cerca. Estender a varredura as chaves de
  `return {...}` de `src/maezo/tools/workers/*.py` foi considerado e **recusado**: exigiria uma
  passagem de AST sobre 31 modulos com historia propria de falso-positivo e mudaria o contrato da
  cerca de "os artefatos declaram X" para "os artefatos e o codigo declaram X" — outro portao, com
  outro dono. Fica como item de acompanhamento nomeado abaixo, nao como lacuna silenciosa.

**As 6 perguntas abaixo sao do DPO. A recomendacao ao lado e da ENGENHARIA, com evidencia — nunca
uma decisao tomada.** O texto completo de cada uma esta em `phi_completeness.DISPOSITIONS`, onde
todo item nasce e permanece `DRAFT/verify (DPO)` (o `__post_init__` recusa qualquer outro status).

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `detalhes_requisicao` (`spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn:55`, rol VARIAVEIS DE ENTRADA) | Texto livre com o detalhe da requisicao do titular. **Recomendacao da engenharia:** listar em `PHI_PROCESS_VARS` — o caso mais forte da tabela, porque o proprio codigo da casa ja o chama de "(free-text PHI)" (`src/maezo/tools/workers/lgpd.py:483`) e o exclui A MAO de UMA saida; hoje a protecao e uma omissao manual num unico call site, nao o controle ancorado em nome. Qualquer outro worker que copie process vars para um dict de saida o emite cru. Nenhuma DMN o le, entao listar nao custa decisao nenhuma | DPO + juridico-privacidade | `DRAFT/verify (DPO) — pergunta aberta` |
| `fundamentacao_legal` (`SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn:78` outputParameter, `:378` conditionExpression do `GW_GuardFundamentacao`) | Texto livre que fundamenta uma negativa de DSR. Essas duas superficies BPMN sao TODA a proveniencia: **nenhuma DMN de `spec/processes/dmn/` declara saida com esse nome**, entao nada limita o valor alem da prosa de `:195`. **Recomendacao da engenharia:** mesma classe do irmao ja listado `fundamentacao_dut` (`phi_vars.py:56`) — ambos sao texto livre que um humano escreve para justificar um desfecho ADVERSO sobre um titular identificado; o campo e ilimitado por construcao (sem enum, sem dominio `camunda:value`, sem tabela de decisao que o escreva) e a prosa do proprio BPMN espera fundamento CLINICO dentro dele ("NEGAR_FUNDAMENTADO exige fundamentacao_legal (ex.: retencao obrigatoria de prontuario)", `:195`). **Contraevidencia que o DPO precisa pesar, na forca real que ela tem:** essa linha de prosa e a UNICA coisa que sugere o que o revisor digita, e nada MECANICAMENTE prende o campo a ela. **Contraevidencia RETIRADA** (registrada porque uma versao anterior desta linha a colocou diante do DPO): dizia que os valores em `spec/processes/dmn/lgpd_dsr_routing.dmn` sao citacoes estatutarias, nao narrativa. E FALSO — as saidas daquela tabela sao `fluxo`/`grupo_revisor`/`sla_resposta`/`sla_alerta` (`:40-43`), com valores `"EXPORTACAO"`/`"dpo"`/`"P15D"`/`"P7D"` (`:48-51`); as citacoes estatutarias sao prosa do `<description>` (`:11-19`) sobre roteamento/SLA, e aquela DMN nao emite `fundamentacao_legal` alguma | DPO + juridico-privacidade | `DRAFT/verify (DPO) — pergunta aberta` |
| `cid10` (`SP-OP-AUTH-001_Autorizacao_Previa.bpmn:59`, `SP-OP-RECURSO-001_Recurso_Glosa.bpmn:69`, `SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn:61` — os tres rois VARIAVEIS DE ENTRADA, i.e. variavel que o CHAMADOR seta no start) | OQ-11 do redesenho CONTAS/RECURSO. **Recomendacao da engenharia:** listar. `cid10_referencia` JA esta em `PHI_PROCESS_VARS` (`phi_vars.py:55`) e o `cid10` nu e O MESMO DADO sob uma segunda grafia — exatamente o modo de falha que a DU-07 nomeia, ja que um controle ancorado em nome nao dobra grafias; `phi_vars.py:24` nomeia CID-10 entre o conteudo clinico que o conjunto existe para cobrir, e essa e toda a ancoragem no repo em que esta linha se apoia. **Custo de listar, medido: ZERO decisoes o leem** — nenhuma `inputExpression` de `spec/processes/dmn/` avalia `cid10`. **Contraevidencia:** um codigo CID-10 e codigo limitado, nao texto livre; se o DPO ler codigos limitados como fora de escopo, entao a inconsistencia a revisitar e a listagem do proprio `cid10_referencia` — os dois nao podem ser classificados de forma diferente. **Apoio RETIRADO** (registrado porque uma versao anterior desta linha o colocou diante do DPO): dizia que "a ADR-0006 poe diagnostico na Zona PHI". Nao poe — `docs/adr/0006-phi-two-zones.md` particiona por AGENTE ("Zona PHI/Financeira (Rafael, Marina, Beatriz...)", `:12`) e nunca nomeia diagnostico, laudo ou CID; era inferencia apresentada como afirmacao de ADR ratificada | DPO + medico auditor | `DRAFT/verify (DPO) — pergunta aberta` |
| `diagnostico_oncologico_confirmado` (`spec/processes/dmn/dut_criteria_oncologia_pet_ct.dmn:56`, `typeRef="boolean"`) | **Recomendacao da engenharia:** listar. O valor e booleano, nao texto livre, entao nao e para isso que `redact_phi_vars` foi escrito — mas "este beneficiario tem diagnostico oncologico confirmado" e dado de saude sobre titular identificado (LGPD art. 5, II — sensivel), e o booleano viaja ao lado do pseudo-id que diz de quem e. **Custo de listar, medido: nenhum para a decisao** — a avaliacao de DMN e engine-side (ADR-0028) e `redact_phi_vars` so roda na SAIDA do worker rumo a Zona Geral. **Contraevidencia:** um booleano redigido no payload de auditoria tira do operador um fato que ele pode precisar para explicar uma negativa | DPO + medico auditor | `DRAFT/verify (DPO) — pergunta aberta` |
| `diagnostico_tea_ou_neurodesenvolvimento` (`spec/processes/dmn/dut_criteria_terapias_especiais.dmn:62`, `typeRef="boolean"`) | Mesma leitura da linha acima — asserção booleana de diagnostico, aqui de neurodesenvolvimento (F84.0 pela prosa da propria tabela, `:26`). **Recomendacao da engenharia:** listar; se algo, o caso e mais forte — diagnostico de neurodesenvolvimento de menor e o dado sensivel arquetipico. Mesmo custo medido e mesma contraevidencia | DPO + medico auditor | `DRAFT/verify (DPO) — pergunta aberta` |
| `has_cid10_codes` (`spec/processes/dmn/phantom_no_diagnosis.dmn:30`, `typeRef="boolean"`) | **Recomendacao da engenharia: registrar como NAO-PHI** — a unica entrada da tabela em que a leitura da engenharia e que a heuristica disparou so pelo token `cid`. O valor e flag de presenca sobre uma submissao de cobranca ("esta conta documenta codigos CID-10?"), pontuada como um INDICADOR de phantom billing para SP-OP-FRAUDE-001 (`:20`, "NUNCA um veredito"); nao carrega codigo nem diagnostico. **Ainda assim quem diz isso e o DPO**: esta cerca registra a pergunta, nao a resposta | DPO + medico auditor | `DRAFT/verify (DPO) — pergunta aberta` |
| `exames_convencionais_inconclusivos` (`spec/processes/dmn/dut_criteria_oncologia_pet_ct.dmn:74`, `typeRef="boolean"` em `:73`) | NOVO nesta revisao: o nome era CLEAN ate o vocabulario PHI-shape ganhar radical para `exame` — a pergunta sempre esteve la e nao estava sendo feita. A prosa da propria tabela descreve o dado como "TC/RM/cintilografia convencionais realizados e inconclusivos para a finalidade solicitada" (`:34-36`). **Recomendacao da engenharia:** listar, pela MESMA leitura dos dois booleanos `diagnostico_*` acima — "exame convencional foi feito neste beneficiario e veio inconclusivo" afirma um evento de cuidado e seu resultado sobre titular identificado (LGPD art. 5, II), ao lado do pseudo-id que diz de quem. **Custo medido:** nenhum para a decisao (avaliacao DMN e engine-side, ADR-0028; `redact_phi_vars` age so na saida do worker). **Contraevidencia:** e criterio DUT booleano, nao texto livre, e os criterios DUT sao a base a partir da qual uma negativa e explicada — redigi-lo custa essa explicacao ao operador | DPO + medico auditor | `DRAFT/verify (DPO) — pergunta aberta` |
| `sintoma_codigo` (`spec/processes/dmn/triage_redflag_adult.dmn:26`, `triage_redflag_gestante.dmn:19`, `triage_redflag_pediatric.dmn:19`, `triage_redflag_mental_health.dmn:22` — quatro `inputExpression`, `typeRef="string"`) | NOVO nesta revisao (radical `sintoma`). **Recomendacao da engenharia: registrar a pergunta E a tensao, nao uma listagem** — e a linha em que a leitura da engenharia esta menos assentada. A FAVOR de tratar como dado de saude: sintoma normalizado de beneficiario identificado e dado de saude (LGPD art. 5, II) por mais limitado que seja o codigo, e as quatro tabelas que o leem decidem escalonamento de red flag clinico. CONTRA: e codigo normalizado FECHADO, nunca narrativa (schema comum declarado em `triage_redflag_adult.dmn:16-20`), e o repo poe DELIBERADAMENTE o agente que o produz (Helena, `src/maezo/agents/helena/graph.py:12-13`, ADR-0012) na ZONA GERAL, onde todo campo e pseudonimizado ponta a ponta (`graph.py:157`) — listar contradiria uma escolha arquitetural viva, e por isso mesmo e pergunta de DPO e nao edicao de engenharia | DPO + medico auditor + arquitetura | `DRAFT/verify (DPO) — pergunta aberta` |
| `auditor_id` (`spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:324`, `:409`, `:547` — `camunda:formField type="string"` sem dominio `camunda:value`) | NOVO nesta revisao, e o unico item da tabela levantado pelo SINAL ESTRUTURAL: nenhum token de nome dispara nele. **Recomendacao da engenharia: registrar como NAO-PHI**, e registrar POR QUE ele esta aqui. O rotulo do proprio artefato e "Id do medico auditor responsavel (obrigatorio se NEGAR)" (`:324`), e o BPMN o exige justamente para que uma decisao adversa carregue a identidade do humano que a tomou (trilha de auditoria, ADR-0007, `:272-273`) — identifica EQUIPE DA OPERADORA, nao beneficiario, e nao carrega conteudo clinico. E tambem TODO o custo de ruido do sinal estrutural no corpus de hoje (14 ocorrencias de campo ilimitado, 5 nomes, os outros 4 ja LISTED), e foi disposto em vez de recortado da heuristica: regra que exclui os proprios falsos positivos deixa de ser auditavel. **Contraevidencia:** identificador de operador continua sendo dado pessoal (LGPD art. 5, I) mesmo nao sendo dado de saude, e o campo ser ilimitado e o ponto — nada estruturalmente impede um revisor de digitar um nome ali | DPO | `DRAFT/verify (DPO) — pergunta aberta` |

**Item de MIGRACAO (ato do dono, nao da engenharia).** A tabela `DISPOSITIONS` mora HOJE dentro de
`src/maezo/platform/validation/phi_completeness.py`, e nao em `spec/policies/privacy/`, por um
motivo unico: aquele diretorio e CODEOWNED pelos revisores de DPO/seguranca
(`.github/CODEOWNERS:116` — `/spec/policies/privacy/ @rodaquino-OMNI @Omni-Saude/security-team
@lucasreisEvah`; `spec/policies/privacy/phi-business-key-remediation.yaml:48-49` afirma o mesmo
fato mas cita `@rodrigotaquino`/`@Omni-Saude/security`, dois handles que o cabecalho de auditoria
do proprio CODEOWNERS prova inexistentes, `.github/CODEOWNERS:8-20`),
e uma tabela de recomendacoes NAO RATIFICADAS nao pode ser lavada para dentro de um caminho de
politica do DPO pelo mesmo commit que a escreve. **Quando o DPO ratificar as 9 perguntas acima, a
tabela deve MIGRAR para um manifesto de privacidade CODEOWNED** (nos moldes de
`phi-business-key-remediation.yaml`: `status`/`ratificacao.ratificado`/`revisor`/`ratificado_em`,
com o carregador recusando rascunho), e a constante no modulo e substituida pelo carregador desse
manifesto. Enquanto a migracao nao acontece, uma disposicao **nao adiciona o nome a conjunto PHI
algum e nao muda um byte de redacao em runtime** — so silencia a cerca e registra a pergunta.

**Item de ACOMPANHAMENTO (engenharia, escopo declarado acima).** Variaveis de processo CRIADAS POR
WORKER e declaradas por nenhum artefato de `spec/` ficam fora da varredura por construcao
(`contas.py:330` `total_glosado_candidato_brl`, `:334` `impacto_percentual`; e a sub-coleta do rol
em prosa perde `total_glosado_candidato_centavos` em
`SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:48`, embora `contas.py:263` a compute e `:331` a
devolva). Todas sao CLEAN hoje, entao nao ha consequencia de balde — mas a pergunta da DU-07 ("os
12 nomes cobrem as variaveis que a plataforma manipula?") as alcanca. Decisao registrada: **nao
estender esta cerca**; se a cobertura dessa classe for exigida, ela e um portao separado sobre
`src/maezo/tools/workers/*.py`, com dono e contrato proprios.

---

## SC-04 — escala do notifications-bridge: chave de particao + DLQ (GAP-SC-04-a; fatia SC-04-b e do owner)

Fatia **SC-04-a** (engenharia, entregue): `publish()` recusa publicacao sem chave de particao
derivavel (`platform/integrations/partition_key.py`), e o notifications-bridge desvia mensagem
malformada para `<topic>.dlq` com publicacao confirmada + fato ADR-0007 duravel antes do commit do
offset (`platform/integrations/notifications_bridge.py`). Nenhum contrato de processo mudou: os
`variables_fn` das 7 regras do bridge, os `event_payload_vars` das BPMN e as variaveis de start de
RECURSO/ANS-SUBMIT/CANCEL estao intocados — logo nenhum `docs/processes/contracts/*.md` nem
test-spec precisa de revisao por esta fatia.

**Garantia de ordenacao, dita com precisao** (correcao de uma afirmacao anterior que era falsa —
achado MAJOR-3 do gatekeeper). Em `agents.events.{familia}.{acao}` a familia vem do TOPICO. No canal
compartilhado `operadora.notifications.internal` ela vem do `type` do payload
(`{familia}.{acao}`, carimbado de constante de modulo em todos os 16 call sites de notificacao) —
o nome do topico ali nao nomeia entidade nenhuma. Consequencia pratica: com business key em branco,
`recurso.*` e `anssubmit.*` ordenam POR ENTIDADE tambem no canal compartilhado, enquanto
`lgpd`/`escalation`/`programa`/`adequacao` ordenam POR INSTANCIA de processo — este repo nao tem
derivacao de business key por entidade verificada para essas quatro familias, e inventar uma seria
fabricar uma ancora. Isso e estritamente melhor que o round-robin da main em todos os casos, mas nao
e ordenacao por entidade em todos eles.

As tres linhas abaixo sao o que esta fatia NAO decide e nao pode decidir sozinha.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `deploy/helm/maezo-tenant/` — `replicaCount` do `deployment-bridge.yaml` (**fatia SC-04-b**, nao tocada por SC-04-a) | Subir o notifications-bridge acima de UMA replica. SC-04-a removeu os dois bloqueios tecnicos: (a) toda publicacao carrega chave de particao deterministica por entidade, entao a semantica de consumer-group da ordenacao por entidade para qualquer numero de replicas do MESMO `NOTIFICATIONS_BRIDGE_KAFKA_GROUP_ID`; (b) uma mensagem-veneno nao trava mais a particao atras dela. O que continua sendo decisao de operacao/owner: **o numero em si**, e os dois fatos que o limitam — o paralelismo e limitado pela contagem de particoes do topico (default do registry = 3, `src/maezo/platform/topic_registry.py:75`; replicas acima disso ficam ociosas), e cada replica mantem entrega at-least-once (`enable_auto_commit=False` + commit-apos-dispatch), sendo o fence `start_process_idempotent` o que faz uma reentrega CONVERGIR em vez de duplicar o start. Exige tambem confirmar que o topico realmente foi criado com >1 particao no cluster alvo (o default do registry e uma declaracao do repo, nao um fato do broker) | owner/operacoes (plataforma) + SRE | `owner-gated — nao alterado por SC-04-a; deploy/ e fora do escopo desta fatia` |
| Metrica-gauge `maezo_dead_letter_queue_size` (lida por `MaezoDeadLetterBacklog`/`MaezoDeadLetterGrowth`, `deploy/observability/alert-rules.yml`) | **A distincao, declarada para nao virar falsa equivalencia.** SC-04-a passa a emitir o CONTADOR `maezo_bridge_dlq_total{topic,reason}` (`src/maezo/runtime/metrics.py`, `src/maezo/platform/observability.py::record_bridge_dlq`) — o fluxo de entrada de mensagens em quarentena, com `reason` de vocabulario FECHADO (`BRIDGE_DLQ_REASONS`), sem conteudo de payload. Isso **nao** e o gauge que os dois alertas leem: `maezo_dead_letter_queue_size` e PROFUNDIDADE ATUAL da fila, um fato do lado do broker (produzidas menos consumidas pelo leitor do proprio DLQ) que nenhum processo em `src/` observa — emiti-lo do src fabricaria um numero que diverge do broker no instante em que alguem drenar o DLQ. Fechar o gauge exige um job de scrape de exporter Kafka/JMX que nao existe em `deploy/observability/prometheus.yml` (owner-gated). **A MESMA constatacao, com o MESMO dono, ja esta registrada duas vezes: sob a fatia `ALERTS-WITHOUT-METRICS-b` na secao WP-COMPOSICAO-V2 fatia 1 (branch `fix/composicao-root-wiring-v2`, que declara `maezo_dead_letter_queue_size` como EXTERNAL na allowlist de `test_alert_metrics_fence.py`) e aqui sob `SC-04-b`. NAO sao dois itens: sao um so, e no merge as duas linhas devem colapsar em UMA, sob `ALERTS-WITHOUT-METRICS-b` — que e quem tem a fence. Esta linha fica com o que e proprio da SC-04-a (a distincao contador-vs-gauge e a opcao (b) de reapontamento) e DELEGA a titularidade do gauge aquela fatia.** `docs/observability/SLO.md` registra a mesma coisa mas AINDA NAO EXISTE nesta branch nem na main — vive so' em `docs/slo-runbooks-alertas` (PR #270, aberta); se SC-04-a mergear primeiro, esta citacao fica pendurada ate #270 entrar. Decisao pendente: (a) subir o exporter e manter os dois alertas como estao, ou (b) reapontar os alertas para `rate(maezo_bridge_dlq_total[5m])`, que ja funciona hoje sem exporter mas alerta sobre FLUXO, nao sobre BACKLOG — semanticamente diferente e a diferenca precisa ser aceita por quem opera | owner/operacoes (plataforma) + SRE (observabilidade) | `owner-gated — contador src-side entregue; gauge de exporter NAO existe e nao foi fabricado` |
| `docs/design/wave1-effect-chokepoint.md` §8.1 — `FORBIDDEN_CONSTRUCTION_NAMES` (fence `scripts/ci/check_effect_chokepoint_fence.py:116-141`) | **Um produtor Kafka novo fora da cerca — escalado, nao alargado em silencio** (achado MINOR-8 do gatekeeper). SC-04-a adiciona `AioKafkaDlqPublisher` (`src/maezo/platform/integrations/notifications_bridge.py:480`), uma classe de efeito externo real. A cerca PASSA porque so' guarda os nomes ENUMERADOS em §8.1, e essa lista e verbatim do documento de design — entao inclui-la exige editar o design, o que esta fatia deliberadamente NAO faz. A razao de nao reusar `AioKafkaEventsProducer` e solida (o DLQ precisa dos bytes VERBATIM, e alargar o allowlist dele seria pior), mas a consequencia — um produtor sem cerca — fica declarada aqui em vez de silenciosa. Decisao pendente: acrescentar `AioKafkaDlqPublisher` a §8.1 e a `FORBIDDEN_CONSTRUCTION_NAMES` (com a composicao ja fenceada por `build_dlq_shunt`), ou registrar por escrito por que a classe fica de fora | owner/orquestrador (documento de design) + plataforma | `ESCALADO — cerca intocada por SC-04-a; nenhum arquivo de fence alterado` |
| `docs/design/requisitos-acessibilidade-ui.md` (NOVO — GAP 10.1) | **Tres ancoras regulatorias brasileiras citadas como DRAFT/verify: Lei 13.146/2015 (LBI), eMAG, ABNT NBR 17060.** O baseline tecnico (WCAG 2.2 AA) foi confirmado ao vivo contra `https://www.w3.org/TR/WCAG22/` nesta sessao; as tres ancoras brasileiras NAO foram — nem a aplicabilidade direta de cada uma a uma operadora PRIVADA de plano de saude (em oposicao a orgao publico/eMAG), nem o texto/numero exato de cada uma foi confirmado contra fonte juridica. `docs/compliance/` nao contem nenhum documento sobre acessibilidade (verificado: `grep -rli acessibilidade\|wcag\|13.146\|eMAG\|17060 docs/compliance/` so retorna `lgpd-topic-reconciliation.md`, que nao trata do tema). Promover o documento a ADR (`docs/adr/`, owner-gated) e' follow-up explicito, nao feito aqui | juridico/regulatorio + especialista em acessibilidade | `DRAFT — requires human review before any deploy/ADR` |

---

## GAP-INAD-8 followup — tres fabricacoes `notified=True` reveladas pelo alargamento do fence (RESOLVIDAS em FAB-NOTIFIED-TRIO)

**STATUS 2026-09-03: as tres foram CORRIGIDAS por `FAB-NOTIFIED-TRIO`** (slice 3 do
WP-FATOS-FABRICADOS). O historico abaixo fica preservado; cada linha da tabela ganhou o mecanismo
da correcao e o novo status. O `_FABRICATED_FACT_BASELINE` do ratchet AST ficou **VAZIO** no MESMO
commit da correcao — a catraca
(`resolved = baseline_modules - actual_modules; assert not resolved`) exige exatamente isso.

**Historico (entrada original, WP-FATOS-FABRICADOS slice 2).** Slice 2 adicionou `notified` ao
`_FABRICATED_FACT_KEYS` do ratchet AST
(`tests/unit/tools/workers/test_worker_handler_purity.py`), porque `cancel.notify_beneficiario`
fabricava exatamente essa chave. O alargamento revelou **tres instancias identicas em tres outras
familias de processo**, que aquele pacote NAO corrigiu (escopo declarado: INADIMPLENCIA->CANCEL) e
que ficaram registradas no `_FABRICATED_FACT_BASELINE` com as linhas abaixo — nunca so num
comentario de codigo.

Todas as tres foram verificadas **zero-consumidores** no momento daquela entrada, e a verificacao
foi REFEITA por FAB-NOTIFIED-TRIO antes da correcao, elemento a elemento:
`grep -rn '\bnotified\b' src/ spec/` nao encontra nenhum `conditionExpression` de BPMN, nenhum
`inputExpression` de DMN e nenhuma leitura Python da chave — os unicos hits nao-produtores sao prosa;
`grep -rn 'message_type' spec/ docs/processes/` e `grep -rn 'sla_remaining' src/ spec/ docs/ tests/`
nao encontram consumidor algum; nenhuma DMN de recurso/reembolso/contas le `status`, `grupo` ou
`notified` (`inputExpression` conferidos um a um); e as tres test-specs so afirmam que a task
"recebeu task", nunca uma variavel de retorno. Isso as tornou correcoes mecanicas (`return {}`, o
precedente do ADEQUACAO e do INADIMPLENCIA), nao problemas de dois consumidores como o do
INADIMPLENCIA. Nenhuma edicao de BPMN/DMN foi necessaria — a consistencia do processo se mantem
sem tocar em nenhum gateway.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `src/maezo/tools/workers/recurso.py::request_documents` (era `notify_prestador` — GAP-RECURSO-5) | **O achado:** retornava incondicionalmente `{"notified": True, "prestador_id": ..., "glosa_id": ..., "message_type": ...}` no topico `operadora.recurso.request_documents` (`ST_SolicitarDocumentos`): afirmava que o prestador foi avisado sem nenhum canal contatado e sem entrega observada. A harness grava o retorno no escopo do processo no `complete` (`harness.py:1778-1782`), entao a afirmacao entrava na instancia. **A correcao (FAB-NOTIFIED-TRIO):** a funcao passou a se chamar `request_documents` — o nome tambem afirmava o ato — e retorna `{}`; o topico BPMN e o guard `ERR_RECURSO_INVALID_GLOSA` ficaram byte-identicos. Opcao (a) (canal real) rejeitada com evidencia: nao existe seam de worker para o PRESTADOR (`engine`/`dmn`/`kafka`/`audit_sink`); `operadora.notifications.internal` e topico interno de observabilidade e `tests/integration/processes/test_sp_op_recurso_001.py:1250` PINA que esta task nao emite notificacao; e `agents.events.recurso.pended` ja e publicado pelo proprio BPMN em `ST_PublishRecursoPended`. **Aberto (nao resolvido aqui):** o `camunda:inputParameter name="event_topic_pended"` de `ST_SolicitarDocumentos` (`spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:194`) continua pendurado — o REEMBOLSO teve o seu removido em t3.1, o RECURSO nao; e a linha 124 do contrato ainda diz "publica tambem `recurso.pended`" atribuindo ao worker um publish que e do BPMN (corrigida por este pacote, ver `docs/processes/contracts/SP-OP-RECURSO-001.md`). A pergunta original — se algum relatorio/dashboard FORA da arvore lia `notified` — permanece sem resposta e foi decidida contra a fabricacao: um leitor externo estaria lendo uma afirmacao falsa, entao remove-la nao pode piora-lo. RN 424/2017 permanece **DRAFT/verify** | dono do processo RECURSO + regulatorio (RN 424/2017 permanece **DRAFT/verify**) | `resolvido 2026-09-03 (FAB-NOTIFIED-TRIO) — retorna {}; entrada removida do _FABRICATED_FACT_BASELINE. Residuo BPMN event_topic_pended:194 ABERTO (owner do processo, exige edicao de BPMN)` |
| `src/maezo/tools/workers/reembolso.py::request_documents` (GAP-REEMBOLSO-8) | **O achado:** retornava `{"notified": True, "status": "pended", ...}`, e **a propria docstring da funcao ja divulgava** que este worker NAO publica o evento de pendencia — devolvendo `notified=True` assim mesmo: a divulgacao e o retorno se contradiziam no mesmo corpo de funcao. Zero consumidores. **A correcao (FAB-NOTIFIED-TRIO):** retorna `{}`. **Duas afirmacoes daquela docstring estavam desatualizadas e foram removidas em vez de reproduzidas:** (1) o `event_topic_pended` que ela citava NAO existe mais neste BPMN (`grep -n event_topic_pended spec/processes/bpmn/*.bpmn` so encontra CRED-001 e RECURSO-001) e (2) o "no downstream ST_Publish* exists on this branch" e falso — `ST_PublishReembolsoPended` (`SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn:151-161`) publica `agents.events.reembolso.pended` uma task adiante, em AMBOS os ramos que chegam ali; as duas vieram de t3.1 (`docs/evidence-ledger.md:125`). Ou seja: a "tarefa de wiring do produtor Kafka" que esta linha apontava como dona do fix **ja tinha sido feita, no BPMN** — o worker nao publicar esta CERTO; errado era afirmar `notified=True` como se tivesse publicado. `status: "pended"` era a unica das cinco chaves que ESCREVIA valor novo no escopo, sob nome generico e sem dono declarado — foi junto | dono do processo REEMBOLSO | `resolvido 2026-09-03 (FAB-NOTIFIED-TRIO) — retorna {}; entrada removida do _FABRICATED_FACT_BASELINE; prosa da docstring reconciliada com o BPMN vigente` |
| `src/maezo/tools/workers/contas.py::notify_sla_risk` (GAP-CONTAS-7) | **O achado:** retornava `{"notified": True, "grupo": ..., "sla_remaining": ..., "numero_lote_tiss": ...}` no timer nao-interruptivo de risco de SLA. Informativo e nunca adverso — a menor aposta das tres — mas a mesma afirmacao falsa: nenhum canal e contatado e a coordenacao-contas pode nao ter sido avisada. Zero consumidores. **A correcao (FAB-NOTIFIED-TRIO):** retorna `{}`; o nome da funcao ficou (e o do topico BPMN `operadora.contas.notify_sla_risk`, que este pacote nao edita). **Opcao (a) NAO feita, e por que:** ao contrario dos outros dois o destinatario e INTERNO (`coordenacao-contas`) e a arvore TEM canal — `operadora.notifications.internal`, para onde `recurso.make_notify_sla_risk_handler` e `lgpd.make_notify_sla_risk_handler` publicam o alerta equivalente com `best_effort=False`. Ligar esse canal aqui (i) troca o registro `FunctionWorker` por raw handler assincrono, (ii) e efeito externo novo cuja prova exige o MOTOR (nao concedido a este pacote) e (iii) o proprio SP-OP-CONTAS-001 ja teve essa checagem e a SUBSTITUIU por verificacao de historia do engine porque a versao `notifications_of_type("contas.notify_sla_risk")` era teste MORTO (`tests/integration/processes/test_sp_op_contas_001.py:1616-1637`, FINDING 1). Alem disso um registro Kafka interno seria PEDIDO de alerta, nunca prova de que alguem foi avisado | dono do processo CONTAS (para a decisao de ligar o canal) | `resolvido 2026-09-03 (FAB-NOTIFIED-TRIO) — retorna {}; entrada removida do _FABRICATED_FACT_BASELINE. ABERTO/opcional: ligar o alerta ao canal interno real (exige motor + decisao do dono)` |

---

## GAP-INAD-9 — `agents.events.inadimplencia.notified` e um asserto sem lastro, publicado incondicionalmente pelo proprio BPMN (mesma especie das tres fabricacoes acima, uma camada acima)

Achado do gatekeeper VER-FAB2 (`VERIFY-WP-FAB2.md`, veredito REVISE MINOR sobre
`fix/fatos-fabricados-inadimplencia-notify`). WP-FATOS-FABRICADOS slice 2 corrigiu
`inadimplencia.dispatch_prior_notice` para nao mais fabricar `notificacao_previa_feita=True`
(GAP-INAD-8) — a task agora **nao afirma nada** sobre a notificacao ter ocorrido. Um passo depois
dela, porem, a propria BPMN publica incondicionalmente `agents.events.inadimplencia.notified` via
`ST_PublishInadimplenciaNotified`
(`spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn:149-158`), em toda instancia,
numa arvore onde **nenhum worker tem canal para notificar um beneficiario** — o unico seam de
WhatsApp (`maezo.tools.mcp_whatsapp.server.WhatsAppServer`) so e alcancavel pelos agentes
conversacionais/webhook (`agents/helena/adapters.py`, `agents/lucas/adapters.py`,
`platform/webhooks/whatsapp/dispatch.py`), nunca por um worker BPMN, e
`platform/notification_bridge.py` e uma ponte processo-a-processo (inicia processos a partir de
eventos), nao um canal de mensageria. O topico esta no particípio passado ("notified") mas nenhum
componente desta arvore de fato notifica ninguem — o evento e um rastro de disparo de tarefa, nunca
uma prova de entrega.

**Zero consumidores hoje**, verificado com o comando exato do brief:
`grep -rn "inadimplencia.notified" src/ spec/ tests/` retorna apenas: o produtor na propria BPMN
(`SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn:60,133,139,149,153` — documentacao + declaracao
do `serviceTask` + `event_topic`); e o teste de integracao que so' AFIRMA a publicacao
(`tests/integration/processes/test_sp_op_inadimplencia_001.py:211,257,568,577,597`); mais a propria
prosa corrigida em `src/maezo/tools/workers/inadimplencia.py:389` (ver abaixo). Ampliando para
`docs/` o mesmo grep so acrescenta a linha "produz" do contrato
(`docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md:111,116`) e a entrada historica do ledger
(`docs/evidence-ledger.md:125`). Nenhum `conditionExpression` de BPMN, nenhum `inputExpression` de
DMN, nenhuma leitura Python em lugar nenhum da arvore.

**Pre-existente, nao adverso.** O evento foi introduzido em t3.1 (`docs/evidence-ledger.md:125`,
2026-07-25), muito antes de GAP-INAD-8; este pacote NAO o criou e NAO o corrigiu — apenas parou de
agravar a situacao (a task que o antecede, `dispatch_prior_notice`, parou de fabricar o fato interno
correspondente). Nao roteia nada, nao bloqueia nada, nao produz efeito adverso ao beneficiario por
si so — o risco e de trilha/auditoria, a mesma especie das tres fabricacoes de worker registradas na
secao "GAP-INAD-8 followup" logo acima (GAP-RECURSO-5/GAP-REEMBOLSO-8/GAP-CONTAS-7), so que uma
camada acima: la e o worker que fabrica o fato interno; aqui e o proprio BPMN que publica um evento
externo sem lastro.

**Correcao de prosa feita nesta mesma entrada:** `src/maezo/tools/workers/inadimplencia.py:387-395`
(docstring de `dispatch_prior_notice`) chamava esse evento de "the notice requested event that DOES
exist" — caracterizacao otimista demais: o evento nao e um "pedido de notificacao", e publicado
incondicionalmente e nao afirma que nenhum pedido foi de fato feito a nenhum canal. A prosa foi
corrigida para descrever o evento com precisao (publicado incondicionalmente pela BPMN, nao afirma
nada sobre entrega) e para apontar para esta linha da fila (GAP-INAD-9). Nenhuma linha executavel
mudou (`git diff` do arquivo mostra so' docstring).

**Resolucao recomendada (decisao do dono do processo + regulatorio, NAO feita aqui):** renomear/
re-semantizar o topico para algo que afirme so' o que aconteceu (ex.: algo como
"...prior_notice_dispatch_requested" ou "...prior_notice_step_completed", nunca ".notified"), OU
condicionar a publicacao a `comprovacao_notificacao_previa` (o campo humano ja existente, coletado
em `UT_AnaliseInadimplencia` e exigido fail-closed por `_register_contract_suspension`,
`inadimplencia.py:569,588`) — nesse caso o evento so dispararia quando houvesse, de fato, uma
notificacao comprovada. Qualquer uma das duas opcoes toca a BPMN, o contrato
(`SP-OP-INADIMPLENCIA-001.md:111`), o catalogo de processos e a asserção de integracao existente
(`test_sp_op_inadimplencia_001.py:568-597`) — fora do escopo de um pacote de docs/comentario. RN 593
permanece **DRAFT/verify**; nenhuma ancora regulatoria foi confirmada ou alterada por esta entrada.

**Regra de merge do ratchet `_FABRICATED_FACT_BASELINE` (achado do VER-FAB2, item informativo do
merge rule).** `_FABRICATED_FACT_BASELINE` (`tests/unit/tools/workers/test_worker_handler_purity.py
:221-238`) e shrink-only: `resolved = baseline_modules - actual_modules; assert not resolved`
(`test_worker_handler_purity.py:389-393`). As branches paralelas `feat/perspectiva-operadora-
recurso` (PR #288) e `feat/perspectiva-operadora-contas-recurso` (PR #294) **DELETAM**
`recurso.notify_prestador` e `contas.notify_sla_risk` (as duas fontes das entradas
`_FABRICATED_FACT_BASELINE["recurso"]`/`["contas"]`). **Quem integrar qualquer uma delas nesta
arvore deve, no MESMO commit de integracao**, remover a entrada `_FABRICATED_FACT_BASELINE["recurso"]`
e/ou `["contas"]` correspondente (`test_worker_handler_purity.py:221-238`) e marcar
GAP-RECURSO-5/GAP-CONTAS-7 resolvidas na secao "GAP-INAD-8 followup" acima — senao
`test_no_domain_worker_returns_unconditional_true_for_fabricated_fact_keys` fica RED por design
(`:389-393`). Isso e a catraca fazendo o trabalho dela, nao um defeito; e a regra de merge que
precisa ser seguida quando #288/#294 chegarem.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn:149-158` (`ST_PublishInadimplenciaNotified`) | Publica `agents.events.inadimplencia.notified` incondicionalmente, em toda instancia, logo apos `ST_CheckPriorNotice`/`dispatch_prior_notice` (que desde GAP-INAD-8 nao afirma nada sobre a notificacao ter ocorrido). Nenhum worker desta arvore tem canal para notificar um beneficiario. Zero consumidores hoje (`grep -rn "inadimplencia.notified" src/ spec/ tests/` — so producer na BPMN + prosa/teste-que-afirma-publicacao, ver acima). Pre-existente (introduzido em t3.1, `docs/evidence-ledger.md:125`), nao corrigido nem agravado por WP-FATOS-FABRICADOS slice 2. Mesma especie das tres fabricacoes de worker registradas na secao "GAP-INAD-8 followup" acima (GAP-RECURSO-5/GAP-REEMBOLSO-8/GAP-CONTAS-7), uma camada acima (BPMN publicando, nao worker fabricando). Resolucao recomendada: renomear/re-semantizar o topico (nunca ".notified") OU condicionar a publicacao a `comprovacao_notificacao_previa` (campo humano ja exigido fail-closed por `_register_contract_suspension`, `inadimplencia.py:569,588`) — decisao do dono do processo INADIMPLENCIA + regulatorio (RN 593, **DRAFT/verify**), toca BPMN + contrato + catalogo + teste de integracao, fora do escopo de um pacote de docs/comentario. Regra de merge relacionada (CUMPRIDA, com correcao de premissa): a entrada previa dizia que PR #288/#294 DELETARIAM `recurso.notify_prestador`/`contas.notify_sla_risk` e que por isso as entradas `_FABRICATED_FACT_BASELINE["recurso"]`/`["contas"]` teriam de sair no MESMO commit. As duas PRs integraram (main `43722b9`) e **nao deletaram nenhuma das duas funcoes** — a premissa estava errada; ambas seguiam vivas e fabricando. Quem cumpriu a regra foi `FAB-NOTIFIED-TRIO` (2026-09-03), que corrigiu as TRES (recurso/reembolso/contas) e removeu as TRES entradas do baseline no mesmo commit, deixando `_FABRICATED_FACT_BASELINE` vazio; o ratchet (`test_worker_handler_purity.py::test_no_domain_worker_returns_unconditional_true_for_fabricated_fact_keys`) segue verde | dono do processo INADIMPLENCIA + regulatorio (RN 593, **DRAFT/verify**) + arquitetura (registro de topico/nomenclatura de eventos) | `DRAFT — nao corrigido; residuo pre-existente registrado nesta entrada, nao renomeado nem regated aqui` |
| `.github/workflows/ci.yml` — passo "Wait for stack to be healthy" (~:414-423) | **O gap CI-KAFKA-HEALTH-WAIT: o job `integration tests (real engine)` espera postgres/cibseven/hapi ficarem saudaveis antes de rodar `make test-integration`, mas NUNCA espera pelo Kafka — mesmo o `docker-compose.yml` declarando um healthcheck proprio do servico `kafka` (`kafka-broker-api-versions --bootstrap-server kafka:29092`, `start_period: 45s`, `docker-compose.yml:96-129`).** Consequencia observada: CI run 33740368677 falhou (`TimeoutError`) em `test_notifications_bridge_live_kafka.py::test_aiokafka_bridge_consumer_consumes_a_real_published_message` — o topico `operadora.notifications.internal` nunca e pre-criado em lugar nenhum do repo, entao esse teste e a primeira coisa a toca-lo no broker compartilhado de CI, e a corrida de auto-criacao + o `group.initial.rebalance.delay.ms` (~3s, todo grupo consumidor novo paga) competem com o deadline fixo de 15s do teste sob a carga especifica do job (~487 testes / ~90min dentro do mesmo processo pytest). **Mitigacao TEST-SIDE ja aplicada** (`tests/integration/platform/test_notifications_bridge_live_kafka.py`, gap `CI-KAFKA-HEALTH-WAIT` no ledger, worktree `live-kafka-fix`): o proprio teste agora cria o topico explicitamente e confirma lider+atribuicao ANTES do deadline estrito — o job deve voltar a passar sem esta mudanca de workflow. Mas o gap estrutural do workflow (nenhuma espera por Kafka) permanece: qualquer OUTRO teste live-Kafka futuro sob este job herda a mesma corrida, e a garantia atual (cibseven/hapi custarem >45s e por acidente aquecerem o Kafka de brinde) e' **incidental, nao projetada** — a mesma classificacao que este proprio gatekeeper (`VERIFY-WP-ESCALA.md §Delta 990df53`) ja tinha registrado ao descobrir o defeito. `.github/` e owner-gated (nao tocado por esta mitigacao, nem por nenhum agente REP). **Correcao de uma linha, pronta para o dono aplicar:** acrescentar `kafka` ao loop de "Wait for stack to be healthy" (ex.: `timeout 120 bash -c 'until docker compose exec -T kafka kafka-broker-api-versions --bootstrap-server kafka:29092; do sleep 5; done'`), espelhando o padrao ja usado para os outros tres servicos do mesmo passo | owner/plataforma (`.github/workflows/ci.yml`) | `owner-gated — mitigacao test-side aplicada (gap CI-KAFKA-HEALTH-WAIT); correcao do workflow em si NAO aplicada, .github/ intocado por design` |
## PR-3 — ADR-0040 perspectiva-operadora (SP-OP-RECURSO-001) — pontos DRAFT/verify citados por artefatos deste PR

As quatro linhas abaixo sao as que os artefatos entregues pelo PR-3 CITAM pelo nome — o BPMN
(`SP-OP-RECURSO-001_Recurso_Glosa.bpmn`), o contrato (`docs/processes/contracts/SP-OP-RECURSO-001.md`),
a divulgacao de dormencia do bridge (`src/maezo/platform/notification_bridge.py`, em
`_register_default_handoffs`) e o PACKAGE de financas. Sem elas, aquelas referencias sao
ponteiros pendurados e o argumento de que a regra dormente e «declarada, testada E DATADA»
fica sem o registro que o sustenta (achado M4 do gatekeeper R1).

O texto de cada linha e copiado **byte a byte** de `docs/review-queue.md` do PR-2 (#274), que
traz a tabela completa de dezessete linhas junto com a propria ADR-0040. A duplicacao e
deliberada e temporaria: com a mesma redacao dos dois lados, a resolucao do merge dos dois PRs
e ficar com a tabela do PR-2 e apagar esta secao — nenhuma reconciliacao de texto.
**Nada aqui e ratificado**: ADR-0040 permanece *Proposed*.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `docs/adr/0040-...md` OQ-3 | Prazo de resposta ao recurso de glosa (`recurso_sla.dmn`, P10D/P15D analise, P30D teto) — o teto hoje e atribuido a RN 424/2017, que `docs/compliance/rn-currency-review.md:85-87` refuta | juridico/regulatorio | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-15 | Ma-classificacao em `action-approvals.yaml:649` — `operadora.recurso.submit_appeal: submissao_regulatoria_ans` tratava interpor recurso junto a operadora como submissao regulatoria a ANS; a matriz MZO-040 nao apanhou. A remocao da linha (exigida pela delecao do topico) nao e a correcao da classificacao — e a remocao do objeto classificado | seguranca + ANS (MZO-040) | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-R1 | O adaptador de intake TISS do recurso nao existe. `REDESIGN-SP-OP-RECURSO-001.md` §3.1 especifica a forma do evento `agents.events.recurso.intake_recebido` e a regra de bridge que o consome, mas nao constroi o adaptador; a regra fica DORMENTE de proposito, com teste que assere a ausencia de publicador (fica vermelho quando o adaptador chegar). Ate la, RECURSO-001 continua iniciado por Marina ou pela operacao | PO + integracoes | `DRAFT — requires human review before any deploy` |
| `docs/adr/0040-...md` OQ-R2 | Arredondamento em `DEFERIR_PARCIAL` — a invariante `valor_deferido_brl + valor_glosa_mantido_brl == valor_glosado_brl` e imposta em centavos-inteiros com igualdade exata. Se a operacao usa tolerancia ou arredondamento contratual, o guard do worker precisa refletir isso; ate la o guard recusa a soma que nao fecha | financas + auditoria de contas | `DRAFT — requires human review before any deploy` |

---

## PR-3 — residuos do gatekeeper independente (VER-PR3-B, §Delta-B `e90aa8e`) — 3º reparo `cdca913`

Tres residuos NAO-BLOQUEANTES levantados na reverificacao independente do delta. Nenhum e
clinico ou regulatorio: sao itens de harness/documentacao de teste, registrados aqui porque o
§Delta-B pediu registro explicito e porque cada um limita o que uma evidencia deste PR garante.
Os dois achados REQUIRED do mesmo relatorio (o hash do ledger e os dois testes nao-discriminantes)
foram CORRIGIDOS em `cdca913`, junto com D2-r1 (o predicado `is_variable_absent_response`), e por
isso nao aparecem aqui.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `tests/integration/processes/test_sp_op_recurso_001.py:266` (docstring do guard de proveniencia) — residuo **D2-r2** | O CODIGO esta certo e a DOCSTRING esta errada. Ela afirma que um teste «nao pode falhar por um guard sobre um processo que ele nao usa»; o log `integ-recurso-e90b-test_sp_op_recurso_001.log:1116-1127` mostra `test_controle_negativo_lastro_confirmado_libera_pela_faixa_clerical` — que exercita SO SP-OP-PAGTO-001 — sendo reprovado pelo guard de RECURSO em `_deploy_pagto`. Falhar alto num engine contaminado e' melhor que emitir um resultado de PAGTO de proveniencia desconhecida, entao a decisao de engenharia esta correta; o que precisa mudar e' o texto, para nao prometer um isolamento que o guard deliberadamente nao da. Docs-only, nao tocado neste reparo por estar fora dos arquivos que este agente pode editar | orquestrador / autor do PR-3 | `ABERTO — correcao de texto, sem efeito de comportamento` |
| `tests/integration/processes/engine_rest.py::assert_definition_provenance` + os 6 marcadores de `test_sp_op_recurso_001.py` — **ponto cego de ramo-irmao** (§B3c) | O guard discrimina esta arvore contra a `main` 6/6, nos dois sentidos — mas NAO contra um ramo-irmao. O BPMN de RECURSO do PR-4 (`feat/perspectiva-operadora-contas-recurso`) satisfaz os marcadores EXATAMENTE (`Start_RecursoRecebido`=3, `ST_ValidarRecurso`=12, e 0 para os quatro marcadores exclusivos da `main`), logo um `make deploy-artifacts` a partir do worktree do PR-4 passaria pelo guard sem ser notado. Hoje isso e inofensivo porque os dois BPMN diferem apenas em prosa mais o bloco de inicializacao `${""}` que so este ramo tem — mas e' exatamente essa diferenca que decide o desfecho fail-closed, entao o `DEPLOY-VERIFIED` NAO garante, sozinho, que o engine rodou a definicao deste PR e nao a do PR-4. Delimita o que a evidencia de engine 43/43 warranta; a correcao (um marcador que discrimine tambem ramos-irmaos, ou um hash da definicao) e' decisao do orquestrador | orquestrador + autor do harness de integracao | `ABERTO — limite de garantia, nao defeito de codigo` |
| `tests/integration/platform/*_live_*.py` (4 arquivos, nenhum com `pytestmark = pytest.mark.integration`) — custo concreto do **m10** | O m10 ja estava divulgado no ponto do defeito (`test_notifications_bridge_live_engine.py:37-55`); o §B6 mediu o seu CUSTO: como estas suites nao tem o marker, elas sao COLETADAS na perna `-m "not integration"` e passam ou skippam conforme haja um engine em `:8080` no host naquele instante. Consequencia: o numero `passed`/`skipped` do gate unitario e' NAO-DETERMINISTICO (`8160/19` com engine vs `8158/21` sem — o mesmo total `8179`), e um numero registrado como fixo num ledger e' uma afirmacao que nao se reproduz. Enquanto nao houver marker, so `passed+skipped` deve ser citado como evidencia. Verificado em 2026-09-03: nenhuma branch local traz o marker, e a `main` `71dd4da` tambem nao | orquestrador (correcao e' do harness, atribuida fora deste PR) | `ABERTO — pre-existente; afeta a reprodutibilidade de todo numero de gate unitario` |

---

## CONTAS-DEAD-ERROR-CATALOG — catalogo de erros `declared-uncaught` de SP-OP-CONTAS-001

O catalogo `bpmn:error` de SP-OP-CONTAS-001 declara tres codigos e so um
(`Error_ContasDecisaoInvalida`) e referenciado — e ainda assim por um throw-end, nao por boundary
catch. Isso foi auditado como "entradas mortas"; a analise contra ADR-0030 §2/§4/§5 e a emenda de
ADR-0040 concluiu o contrario (estado ratificado), a decisao foi MANTER as duas declaracoes, e a
invariante foi fixada em teste. O que sobra para humano nao e o estado atual — e a condicao de
saida dele.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `spec/processes/bpmn/SP-OP-CONTAS-001_...bpmn` `Error_ContasGlosaNotHuman` (+ `docs/adr/0030-...md` §4) | **Quando — e se — `ERR_CONTAS_GLOSA_NOT_HUMAN` deve ganhar `boundaryEvent` e entrar na allowlist de producao.** Hoje NAO deve: ADR-0030 §4 torna T-E (audited refusal) co-requisito HARD de qualquer codigo `*_NOT_HUMAN`, porque ativar antes troca um "guaranteed-human-visible incident" por um "clean, silent end ... no incident, no audit row, no notification". A allowlist de producao segue vazia e a recusa e um `PermissionError` auditado. A decisao de virar isso e pos-T-E, exige alvo roteado projetado (a invariante HITL proibe alcancar glosa aplicada / pagamento negado / demonstrativo emitido sem User Task) e toca `docs/adr/` sob CODEOWNERS | dono do processo CONTAS + quem conduz T-E + seguranca | `ABERTO — nao acionavel antes de T-E; estado atual e o mais protetivo` |
| `docs/adr/0030-...md` §2 clausula (c) no fecho do Tier-3 | Quando a clausula (c) endurecer (`--strict-dead-models`), confirmar que a leitura "entrada de catalogo de raiz sem boundary" continua FORA do escopo do gate — e nao vira falha. Hoje `_parse_bpmn_file` so promove a `spec_codes` codigos ligados a boundary sobre external task, entao as 20 declaracoes `declared-uncaught` do repositorio (13 dos 16 BPMN) sao invisiveis por construcao. Se o fecho do Tier-3 mudar essa fronteira, 13 arquivos — nao so CONTAS — precisam de decisao conjunta | arquitetura (dono do ADR-0030) | `ABERTO — pergunta de escopo do gate, levantada aqui porque CONTAS a expos` |
## WHATSAPP-NON-TEXT-DROPPED — o que o ack de nao-texto DELIBERADAMENTE nao faz (2026-09-03)

O gap `WHATSAPP-NON-TEXT-DROPPED` foi fechado na parte MECANICA: inbound nao-texto
(audio/imagem/documento/localizacao/...) deixou de ser descartado em silencio e passa a receber UMA
resposta fixa em pt-BR pelo mesmo seam gateado (`src/maezo/platform/webhooks/whatsapp/dispatch.py
::HelenaDispatcher.acknowledge_non_text`, texto em `::NON_TEXT_ACK_TEXT`). As tres linhas abaixo
registram o que NAO foi feito e por que — nenhuma delas e defeito novo introduzido por este
pacote; sao decisoes de dono e um limite de plataforma pre-existente.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `src/maezo/platform/webhooks/whatsapp/dispatch.py::NON_TEXT_ACK_TEXT` (decisao 10.2 do dono — fallback mais rica em nao-texto) | O texto entregue promete APENAS o que o codigo faz: que o canal aceita texto. NAO promete humano, transcricao/STT, Libras nem retorno, porque nenhum follow-up humano foi ligado — `acknowledge_non_text` nao chama `audit_sink` e nao inicia SP-OP-ESCALATION-001. Um beneficiario com deficiencia auditiva/visual, baixa letramento ou em urgencia que so consegue mandar audio continua **sem caminho** neste canal: ele e informado, nao atendido. Decidir a fallback real (rotear para humano via o start audidado da Helena? transcrever? oferecer telefone?) e decisao de produto/assistencial + acessibilidade, e muda o texto E o codigo no MESMO commit — o teste `test_acknowledge_non_text_sends_exactly_one_fixed_reply_to_the_verified_hash` PINA o literal justamente para que uma promessa nao entre sem a implementacao | dono do produto + gestao assistencial + acessibilidade/juridico (LGPD/EIA) | `ABERTO — decisao 10.2 do dono; ack mecanico entregue, fallback NAO` |
| `WEBHOOK-WAMID-DEDUP` — `src/maezo/platform/webhooks/whatsapp/` (nao existe `idempotency.py`; `docs/runbooks/whatsapp-webhook.md` §3 descreve um store que nao existe nesta arvore) | Sem deduplicacao por `wamid`, uma re-entrega do mesmo webhook pela Meta (que retenta tudo que nao respondeu 2xx) faz o receiver RE-ENVIAR o ack fixo. Hoje o impacto e mensagem de cortesia duplicada, nunca efeito adverso duplicado — o ack nao inicia processo, nao grava checkpoint e nao audita nada; o turno de TEXTO da Helena, esse sim, ja e re-executavel por re-entrega desde T1.11 e nao foi agravado nem corrigido aqui. Divulgado em comentario no proprio modulo, no runbook §1 e nesta linha; NAO implementado por ser owner-gated | owner/plataforma (idempotencia de webhook) | `ABERTO — owner-gated, divulgado, nao implementado` |
| `WHATSAPP_PHONE_NUMBER_ID` ausente em Helm (`deploy/helm/maezo-tenant/templates/deployment-webhook-receiver.yaml` injeta `WHATSAPP_TOKEN`/`_APP_SECRET`/`_VERIFY_TOKEN` e nada injeta o `PHONE_NUMBER_ID`) | Consequencia para ESTE pacote, registrada para nao superestimar o que ele entrega: enquanto o segredo nao for provisionado, `WhatsAppServer.send_message` RECUSA fail-closed, entao o ack de nao-texto **nao chega ao beneficiario em producao** — exatamente como ja acontece com toda resposta da Helena (divulgacao de ops ja registrada em `src/maezo/tools/mcp_whatsapp/server.py` e na linha propria desta fila). A falha e contada como `failed` e logada (`whatsapp_dispatch_failed`), nunca escondida. `deploy/` e owner-gated e NAO foi tocado | owner/ops (provisionar `WHATSAPP_PHONE_NUMBER_ID` no secret `maezo-whatsapp-config`) | `ABERTO — pre-existente; limita o efeito real deste pacote em producao` |
| `src/maezo/platform/webhooks/whatsapp/app.py` — decisao `dispatched == 0 and acked == 0 and failed > 0` do handler (mudanca de comportamento nao divulgada ate esta linha) | Um lote MISTO com 1 turno de TEXTO que falha + 1 mensagem nao-texto reconhecida com sucesso agora retorna HTTP 200 (`{"status":"ok","dispatched":0,"failed":1,"acked":1}`), nao mais 500. Em `main`, antes deste pacote existir, o mesmo lote (sem ack de nao-texto) retornava 500 e a Meta re-entregava o lote inteiro, dando ao turno de texto uma nova chance. Aqui a falha e contada e logada (`whatsapp_dispatch_failed`) mas NAO retentada — o turno de texto daquele beneficiario e perdido a menos que ele reenvie por conta propria. Trade-off deliberado, nao defeito: devolver 500 faria a Meta re-entregar o lote inteiro, o que re-enviaria o ack de nao-texto JA entregue ao beneficiario, e nao ha dedup por `wamid` para absorver essa duplicata — ver a linha `WEBHOOK-WAMID-DEDUP` acima, que e o PRE-REQUISITO para restaurar o retry por mensagem individual em vez de por lote inteiro. Divulgado em comentario no proprio `app.py` e nesta linha | owner/plataforma (mesma decisao de idempotencia de `WEBHOOK-WAMID-DEDUP`) | `ABERTO — divulgado nesta linha; retry do turno de texto em lote misto depende de WEBHOOK-WAMID-DEDUP` |
## PERSPECTIVE-FENCE-XML-COMMENT-ASYMMETRY — marcador de referencia historica NAO criado (pergunta ao dono)

O registro P2 pedia «um marcador de referencia historica para que um comentario XML que registra
uma delecao e carrega token de prestador nao seja hit bloqueante, mantendo a semantica Tier A/B
documentada». A implementacao **recusou o marcador** e fechou o gap pela convencao que a propria
racionalidade da fence implica (commit `a95914a`): narrativa de delecao nao vive em comentario XML
de BPMN/DMN — vive na narrativa de docs ou num comentario de YAML de manifesto de spec, que Tier B
nao le por desenho. Ver `src/maezo/platform/validation/perspective.py`, secao «Where historical
references go» do docstring de modulo, e `CONTRIBUTING.md`, subsecao «Onde vive a referencia
historica (convencao, nao marcador)».

Razao da recusa, citada: ADR-0040 D7 diz «Nao ha allowlist de excecoes, nem por arquivo nem por
bloco `historico:`» (`docs/adr/0040-perspectiva-operadora-contas-recurso.md:289-290`), e a secao
«Fail-closed» do modulo repete «There is no exception mechanism: no allowlist file, no inline
waiver, no `historico:` block». Um marcador que dispensasse o comentario E esse mecanismo. Criar um
e ato do DONO sobre o ADR — nao edicao da fence por quem a implementa.

**Nada aqui e ratificado.** ADR-0040 permanece *Proposed*, o que reforca (nao enfraquece) a
conclusao: uma decisao ainda nao ratificada nao e emendada por uma edicao de codigo.

| Item | O que precisa de decisao humana | Revisor | Status |
|---|---|---|---|
| ADR-0040 D7 — mecanismo de excecao | Registro de delecao deve ser permitido dentro de comentario XML de BPMN/DMN por meio de marcador explicito? Opcao A: manter o desenho sem excecao (recomendada; a convencao acima ja resolve o caso de uso). Opcao B: novo ADR emendando D7 com marcador + alargamento de Tier B para simetria. Memorando com consequencias e custo de teste das duas opcoes entregue ao orquestrador para `OWNER-DECISIONS.md` | dono (`@rodaquino-OMNI`) + Security/compliance | `ABERTO — decisao do dono; sem impacto no build (fence segue 0/0 em Tier A e Tier B)` |
## TEST-ROBUSTNESS-R2 — gaps de robustez de teste (registro P2, `docs/evidence-ledger.md` linha `TEST-ROBUSTNESS-R2`)

Dois gaps de harness/CI fechados nesta sessão. O primeiro passe (autor R2, `e7f041a`) foi feito
SEM engine/broker; o gatekeeper R1 rodou a suíte ao vivo, achou tres testes vermelhos-latentes e
devolveu REVISE; o reparo `f1ea7f7` (REP-TEST-ROBUSTNESS, R1, com o stack isolado concedido)
reconciliou os tres e tirou a vacuidade da prova de particao. Falta a reproducao INDEPENDENTE do
delta pelo mesmo gatekeeper. Nenhum conteudo clinico ou regulatorio aqui; registrados por
completude do processo, nao por exigencia da tabela principal acima.

| Artefato | O que precisa de revisao humana | Revisor | Status |
|---|---|---|---|
| `tests/integration/platform/test_events_kafka_producer_live.py` (gap `PRODUCER-LIVE-19092-DEFAULT`) | Defaults realinhados ao stack compose real (`localhost:9092` / porta `5433`) + prontidao de topico/consumer frio portada de `test_notifications_bridge_live_kafka.py` (`_await_topic_ready`/`_await_assigned`/`seek_to_end`). **A corrida AO VIVO foi feita** (reparo `f1ea7f7`, stack isolado, sem env algum): **7 passed / 0 skipped / 0 failed** a frio, **8 passed** junto com a suite irma (zero interferencia), e `1 passed, 6 skipped` com o stack derrubado, cada skip citando `localhost:9092`. O que ela revelou: assim que a suite passou a RODAR, 3 testes ficaram vermelhos por exigirem a aresta CONTAS->RECURSO que a **PR #294 (ADR-0040 §3.1) aposentou na propria base deste branch** — falham identicamente na base `43722b9`, e sem o reparo o lane `integration tests (real engine)` do CI ficaria VERMELHO ao mergear. Os 3 foram re-apontados para a unica regra sobrevivente de `agents.events.contas.completed` (CONTAS->FRAUDE), sem xfail/skip e sem asserção enfraquecida (34 -> 47 `assert`), com os payloads REAIS que o BPMN de CONTAS publica. **O que ainda precisa de olho humano:** a decisao de driblar CONTAS->FRAUDE em vez de INTAKE->RECURSO nos tres testes (razao estrutural: so `MIRROR_TOPICS` e espelhado, e `recurso.intake_recebido` nao tem publisher) e a reproducao independente do delta pelo gatekeeper R1 | verificador R1 (stack isolado) + revisor da perspectiva ADR-0040 | `implemented — corrida ao vivo feita pelo reparador; reproducao independente pendente` |
| `tests/integration/platform/test_events_kafka_producer_live.py::test_same_entity_events_land_on_the_same_partition` (vacuidade, achado do gatekeeper R1) | O teste passava sem provar nada: todo topico do compose e auto-criado com **1 particao** (medido: `PartitionCount: 1` em `agents.events.contas.completed`, `operadora.notifications.internal` e `…internal.dlq`), e em 1 particao `first.partition == second.partition` vale para QUALQUER entrada — inclusive para um produtor que ignorasse a chave. Agora cria um topico proprio por execucao com 3 particoes (o default declarado em `TopicEntry.partitions`), ASSEVERA a precondicao (falha alto, nunca skip), e prova as duas metades: mesma entidade -> mesma particao, 12 entidades distintas espalhadas por >1 particao. Mutacao rodada e revertida: com o topico de volta a 1 particao o teste fica VERMELHO. **Para revisao:** se o compose deveria passar a declarar `KAFKA_NUM_PARTITIONS=3` (alinhando o dev-stack ao registro) em vez de cada teste criar o seu — `docker-compose.yml` e CODEOWNED e nao foi tocado aqui | dono do dev-stack / orquestrador | `ABERTO — correcao do teste entregue; alinhamento do compose e decisao do dono` |
| Classe de risco exposta pelo gap acima: **suite live que pula em silencio nao protege nada** | A PR #294 aposentou a aresta CONTAS->RECURSO e tres testes desta suite ficaram vermelhos-LATENTES sem que ninguem visse, porque os defaults ficticios (`19092`/`5659`) faziam 6 dos 7 pularem em todo lugar, CI incluso. Os outros tres `tests/integration/platform/*_live_*.py` merecem a mesma auditoria de default/alcancabilidade: um `SKIPPED` num lane verde e indistinguivel de um `PASSED` para quem le so o resumo | orquestrador (auditoria de harness) | `ABERTO — nao investigado nesta PR` |
| `tests/unit/ci/test_integration_suites_carry_marker.py` (gap `CI-FENCE-TESTS-COUPLING`) | `_Coverage.covered` deixou de aceitar `via_conftest_chain` como alternativa suficiente — agora exige o marcador do próprio módulo, fechando o acoplamento onde o teste 1 só ficava correto por causa do teste 3. Duas mutações reproduzidas e revertidas na sessão do autor (não commitadas): apagar o teste 3 (suite continua verde) e, com o teste 3 ainda apagado, remover `pytestmark` de um módulo cujo `conftest.py` pai carrega o marcador (`test_sp_op_adequacao_001.py`, restaurado por byte após a prova) — RED confirmado nos dois testes que checam cobertura própria. **Este arquivo NAO foi tocado pelo reparo `f1ea7f7`** (byte-identico desde `e7f041a`), e o hash de prova segue valendo | verificador R1 | `implemented — unverified; mutações reproduzidas pelo autor, pendente reprodução independente` |
