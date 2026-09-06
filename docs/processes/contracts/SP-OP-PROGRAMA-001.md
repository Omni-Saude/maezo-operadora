# Contrato — SP-OP-PROGRAMA-001 (Programas de Cuidado — Consent-Gated)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review (medico-auditor/juridico/DPO/regulatorio/financas/PO) before any deploy` (docs/review-queue.md)
**Fase:** 3 (Wave W-C; modelado uma fase a frente — playbook 5-bis) · **BPMN (alvo, autorado em wave posterior):** `spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn`
**Gatilho regulatorio:** **LGPD (Lei 13.709/2018)** — art. 7/art. 11 (consentimento para tratamento de **dados de saude = sensiveis**), art. 9 (transparencia), art. 8 §5 / art. 18 §2 (**revogacao do consentimento a qualquer tempo, com efeito**); eventual RN ANS de programas de promocao a saude / APS (**DRAFT/verify** com regulatorio — confirmar se ha RN especifica). **Depende de SP-OP-LGPD-DSR-001** (ja em main): a revogacao de consentimento e um `tipo_requisicao` do DSR e aqui e o gatilho de interrupcao.

> **Processo L3 (autonomo com telemetria — ADR-0008: triagem/estratificacao/enrollment informativo),
> CONSENT-GATED de ponta a ponta.** O processamento de PHI do programa so existe **com consentimento
> ativo**; a revogacao **interrompe e para** o processamento. **Nao modelar como happy-path de
> enrollment** — modelar como um fluxo cujo chokepoint de entrada e o consentimento e cujo evento
> de borda interruptivo e a revogacao. (phase3-plan §processos, linha PROGRAMA + risco #6.)

## Invariantes (LGPD + no-adverse clinico, ADR-0018)

**(A) Chokepoint de consentimento — gateia TODO processamento de PHI do programa.** Antes de
qualquer tratamento de dados de saude do beneficiario no ambito do programa (estratificacao de
risco, montagem de plano de cuidado, contato clinico proativo, registro clinico), o consentimento
DEVE estar **ativo e verificado** para o escopo `programa_cuidado`. O worker
`operadora.programa.check_consent` e o **chokepoint**: se nao ha consentimento ativo, lanca
`ERR_PROGRAMA_NO_CONSENT` e o processamento de PHI **nao ocorre** (fail-closed). Nenhum caminho
processa PHI de programa sem passar por este gate.

**(B) Revogacao = evento de borda INTERRUPTIVO que PARA o processamento.** A revogacao de
consentimento (`msg.programa.consent_revoked`, originada de SP-OP-LGPD-DSR-001
`revogacao_consentimento`, ou de canal direto) e um **interrupting boundary event** anexado ao
subprocesso de cuidado: ao disparar, **interrompe** o subprocesso, dispara o worker de purga/parada
(`operadora.programa.stop_processing`) e leva ao terminal `End_ProcessamentoInterrompidoRevogacao`.
**NAO e um caminho de happy-path**; e uma interrupcao que cessa o tratamento de PHI imediatamente
(LGPD art. 8 §5 / art. 18 §2).

**(C) No-adverse clinico — alta/desligamento do programa so por humano (ADR-0018).** O
**desligamento clinico do programa** (`register_program_discharge` — alta por motivo clinico, que
e uma decisao clinica e, por ADR-0008, **L0-hard "qualquer decisao clinica"**) so nasce numa User
Task humana (`UT_DecisaoClinica`, grupo `coordenacao-clinica`/`equipe-cuidado`), com
`decisao_programa == DESLIGAR_CLINICO` setado por humano. Nenhuma DMN deste processo possui saida
de desligamento/alta clinica: a DMN `programa_routing` apenas **estratifica risco e sugere
elegibilidade ao programa** (informativo); ela nunca produz `DESLIGAR`/`ALTA`/`NEGAR_CUIDADO`.
Caso clinico ambiguo, risco alto, criterio de alta aparente, indisponibilidade da DMN e estouro de
SLA **todos fail-safe para a User Task clinica** (catch-all → `ANALISE_HUMANA`). O efeito adverso
clinico e materializado **apenas** pelo worker `operadora.programa.register_program_discharge`,
guardado por `ERR_PROGRAM_DISCHARGE_NOT_HUMAN`. O terminal `End_DesligamentoClinicoHumano` so e
alcancavel apos a User Task clinica concluida.

> **Distincao importante:** *parar por revogacao* (B) e LGPD/automatico-fail-safe (parar de tratar
> PHI e o comportamento seguro — nao e adverso ao beneficiario). *Desligar por motivo clinico* (C)
> e decisao clinica adversa e **so humana**. Os dois sao terminais diferentes e nunca se confundem.

## Terminais (humano-gated, interruptivo, neutros)

| End event | Natureza | So alcancavel via |
|---|---|---|
| `End_DesligamentoClinicoHumano` | **ADVERSO (decisao clinica L0-hard)** — alta/desligamento clinico do programa | `UT_DecisaoClinica` com `decisao_programa=DESLIGAR_CLINICO` humano |
| `End_ProcessamentoInterrompidoRevogacao` | **fail-safe LGPD (nao adverso)** — revogacao parou o tratamento de PHI | interrupting boundary event `msg.programa.consent_revoked` → `stop_processing` |
| `End_SemConsentimento` | fail-safe LGPD (nao adverso) — sem consentimento ativo; nada processado | chokepoint `check_consent` → `ERR_PROGRAMA_NO_CONSENT` capturado → fim sem processar PHI |
| `End_EnrollmentRealizado` | neutro — beneficiario incluido no programa (com consentimento; cuidado em curso) | caminho L3 consentido (estratificacao + plano) |
| `End_NaoElegivel` | neutro — nao elegivel ao programa (criterio populacional/clinico informativo; **nao e negativa de cobertura**) | caminho L3 (`programa_routing` informativa) |
| `End_AcompanhamentoConcluido` | neutro — ciclo de acompanhamento concluido (alta administrativa nao-clinica / fim de ciclo) | caminho L3 |

O terminal ADVERSO `End_DesligamentoClinicoHumano` NUNCA aparece na history do engine sem uma
User Task clinica concluida (teste de invariante — ver test-spec). Nenhum fim ocorre sem evento de
dominio publicado antes (auditoria dupla engine+Kafka, ADR-0007).

> **`End_NaoElegivel` nao e negativa de cobertura** (que seria L0-hard, dominio do AUTH/REEMBOLSO):
> e apenas nao-inclusao num programa de promocao a saude. Nao nega tratamento; o beneficiario segue
> com cobertura normal. Por isso e neutro e pode ser L3. Caso a "nao elegibilidade" tivesse
> qualquer efeito adverso de cobertura, roteria a humano — mas por design nao tem.

## Business key (idempotencia)

```
PROG-{tenant_id}-{programa_id}-{beneficiario_pseudo_id}-{ciclo}
```

`ciclo` = ciclo de acompanhamento do programa (ex.: `YYYY-Qn` ou id de coorte, **DRAFT/verify**).
Uma instancia ativa por (programa × beneficiario × ciclo); re-disparo do mesmo enrollment retorna a
instancia ativa (`mcp-cibseven.start_process` consulta a business key antes de iniciar — start
idempotente, sem duplicar enrollment do mesmo beneficiario no mesmo ciclo).

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `programa_id` | string | sim | Id do programa de cuidado (ex.: cronicos/pre-natal/oncologia/APS — taxonomia DRAFT) |
| `beneficiario_pseudo_id` | string | sim | Pseudonimo do beneficiario (ADR-0006 — **NUNCA CPF/nome**) |
| `ciclo` | string | sim | Ciclo de acompanhamento (compoe a business key) |
| `gatilho` | string | sim | `canal_proativo` (start-signal-only PHI-minimizado — D9) \| `indicacao_clinica` \| `auto_inscricao_beneficiario` \| `estratificacao_populacional` |
| `proactive_trigger_ref` | string | nao | Ref do `agents.events.proactive` que originou (D9: `{trigger_type, pseudonym/cohort/risk_band, source_ref, consent_checked, ts}` — **sem conteudo clinico cru**) |
| `consent_scope` | string | sim | Escopo de consentimento requerido (`programa_cuidado` — fixo nesta fase) |
| `consentimento_ativo` | boolean | sim* | Pre-resolvido por `operadora.programa.check_consent` (chokepoint): consentimento ativo e nao-revogado para `consent_scope` |
| `consent_checked` | boolean | sim | Veio do trigger proativo (D9 exige `consent_checked==true` antes de contato com beneficiario); re-verificado in-zone pelo chokepoint |
| `risco_estratificado` | string | nao | Pre-resolvido in-zone por worker/agente (Valentina) APOS o gate de consentimento; banda de risco (nunca conteudo clinico cru em Zona Geral) |
| `elegibilidade_criterios_atendidos` | boolean | nao | Pre-resolvido in-zone APOS consentimento (criterios do programa) |

\* `consentimento_ativo` e resolvido **dentro do gate** `check_consent`; nenhuma variavel de PHI clinico (`risco_estratificado`, criterios) e populada **antes** do gate passar. PHI clinico permanece em Zona PHI (Valentina = `security_zone: phi`, D10); Zona Geral so ve bandas/pseudonimos.

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `elegivel_programa` | string | `ELEGIVEL` \| `NAO_ELEGIVEL` \| `ANALISE_HUMANA` (estratificacao informativa da DMN; **sugere, nao decide cuidado/alta**) |
| `decisao_programa` | string | `ENROLL` \| `MANTER_ACOMPANHAMENTO` \| `DESLIGAR_CLINICO` \| `SOLICITAR_INFO` (preenchida SO por User Task humana quando ha decisao clinica adversa; `ENROLL`/`MANTER` podem ser L3 quando consentidos e nao-adversos) |
| `motivo_desligamento_clinico` | string | **Obrigatoria se `DESLIGAR_CLINICO`** — fundamentacao clinica da alta/desligamento |
| `referencia_clinica` | string | **Obrigatoria se `DESLIGAR_CLINICO`** — protocolo/criterio clinico citado (ex.: alta de ciclo terapeutico) |
| `responsavel_clinico_id` | string | Humano (clinico) que decidiu o desligamento (cadeia de auditoria ADR-0007); carregado no worker do efeito adverso |
| `consent_event_ref` | string | Referencia ao registro de consentimento/revogacao (auditoria LGPD; liga a SP-OP-LGPD-DSR-001) |
| `decisao_coordenacao` | string | `assumir_decisao` \| `prorrogar_prazo` \| `seguir_analise` (estouro de SLA da decisao clinica — humano `coordenacao-clinica`) |
| `enrollment_gap` | string | **ENROLL-BENEFICIARIO-SEM-EFEITO-REAL — preenchida por WORKER, nao pela UT.** Vocabulario FECHADO, hoje de um unico valor: `enroll_a2a_nao_ligado`. Emitida por `operadora.programa.build_care_plan` (`enroll_beneficiario`) enquanto a delegacao A2A `care.enroll` a Valentina NAO estiver ligada — declara que **nenhum plano de cuidado/dossie foi montado**. **AUSENTE quando a integracao estiver ligada** — ausencia significa "montagem real ocorreu", presenca significa "nao ocorreu". Sem PHI (token fechado). NAO e lida por nenhum gateway/DMN: existe para que a instancia e quem decide em `UT_DecisaoClinica` vejam a lacuna em vez de um fato fabricado — e, desde o reparo §Delta F2, tambem para quem consome `agents.events.programa.completed` (o token entrou no `event_payload_vars` de `ST_PublishCompleted`) |
| `contato_gap` | string | **NEW-A2-2 (FAB-PUBLISH-CONTACT) — preenchida por WORKER, nao pela UT.** Vocabulario FECHADO, hoje de um unico valor: `contato_beneficiario_nao_ligado`. Emitida por `operadora.programa.proactive_contact` enquanto NAO existir canal de saida ao beneficiario ligado a `ST_ProactiveContact` — declara que a task de contato **nao contatou ninguem**: o handler raw so publica um envelope de OBSERVABILIDADE em `operadora.notifications.internal` (`programa.proactive_contact`), que nao chega a pessoa alguma e nao e consumido por nenhuma regra da ponte; no caminho `kafka=None` nem isso sai. Os remetentes reais da classe de acao `comunicacao_beneficiario` sao as superficies WhatsApp dos agentes, nunca este worker. **AUSENTE quando o canal estiver ligado** — ausencia significa "contato real ocorreu", presenca significa "nao ocorreu". Sem PHI (token fechado). NAO e lida por nenhum gateway/DMN. **Quem a le, HOJE, exatamente (reparo §Delta F1 — a redacao anterior desta linha nomeava tres leitores, dois inexistentes):** (i) o **escopo do processo** — a harness escreve o retorno inteiro do worker no `complete`, entao o token e variavel da instancia e aparece em Cockpit/history; (ii) a **linha estruturada de log** `programa_proactive_contact_gap` (`warning`, com `contato_asserted=False`); e (iii) o **payload de `agents.events.programa.completed`**, desde este reparo (`event_payload_vars` de `ST_PublishCompleted`). **NAO ha User Task neste ramo:** `ST_ProactiveContact` e alcancada so pela saida ELEGIVEL de `GW_Elegibilidade` e seu UNICO sucessor e `End_EnrollmentRealizado` — `UT_DecisaoClinica` fica no ramo ANALISE_HUMANA/SLA e nao e alcancavel a partir dela, logo nenhum humano le `contato_gap` dentro do processo. **E NAO entra na trilha ADR-0007:** `harness.py::build_decision_basis` admite de `out_vars` SOMENTE as chaves de `_SAFE_DECISION_BASIS_KEYS`, que nao contem chave `*_gap` alguma — e o token deliberadamente NAO foi adicionado la (ampliar a superficie de auditoria exigiria justificativa propria). O carimbo `desfecho=enrollment_realizado` continua sendo do `outputParameter` do BPMN (C3), NAO deste worker |

> **SUPERSEDE a frase abaixo sobre `enrollment_realizado` (ENROLL-BENEFICIARIO-SEM-EFEITO-REAL, achado do verificador de FAB-PROGRAMA-NOW, `VERIFY-FABPROG.md` §4(ii)/achado 1).** A nota original (preservada abaixo, nao reescrita — o ledger e este contrato registram a correcao para a frente, nunca apagam a caracterizacao anterior) chamou `enrollment_realizado` de "fato booleano real" e concluiu "nao ha lacuna a declarar". Essa caracterizacao NAO era substanciada: `enroll_beneficiario` nunca executou enrollment algum — o corpo fazia SO `logger.info`, sem escrita, sem chamada A2A — apesar deste MESMO contrato (linha `operadora.programa.build_care_plan` em "Topicos", abaixo) e do proprio codigo (`programa.py`, comentario de bootstrap "spec match: task name says 'care.enroll'") ja declararem a delegacao A2A `care.enroll` a Valentina que o codigo nunca chamava. Mesma especie que BEA-09 corrigiu para `dossie_montado`/`referral_executado` em SP-OP-FRAUDE-001 — e, ao contrario do que a nota original concluiu por analogia, AQUI TAMBEM ha uma lacuna real a declarar, agora como `enrollment_gap` (linha acima). A chave `enrollment_realizado` foi REMOVIDA do retorno do worker sem substituicao por um fato equivalente; nao ha regressao no gate de consentimento (`check_consent` continua o UNICO chokepoint, inalterado por esta correcao).

> **`consent_verified_at`/`data_enrollment` NAO existem como variavel de processo (FAB-PROGRAMA-NOW-TIMESTAMPS).** `operadora.programa.check_consent` retornava `{consentimento_ativo: true, consent_verified_at: "now"}` e `operadora.programa.build_care_plan` (`enroll_beneficiario`) retornava `{enrollment_realizado: true, data_enrollment: "now"}` — dois carimbos com o literal `"now"` em vez de um instante ISO-8601, gravados no escopo da instancia pela harness e portanto na trilha de auditoria LGPD. Mapa de consumidores refeito (BPMN `conditionExpression`/timer/DMN `inputExpression`/worker a jusante/golden/este contrato) antes da correcao: **zero** para as duas chaves. Ao contrario de `referral_gap`/`dossie_gap` (SP-OP-FRAUDE-001, BEA-09/FAB-REFER-TO-LEGAL — la o consumidor e o humano na UT, que precisa ver a lacuna), aqui o proprio ato ocorre de fato dentro da funcao (`check_consent` avalia `consentimento_ativo`/`consent_checked` e gateia fail-closed; `enroll_beneficiario` de fato executa e loga o enrollment) — nao ha lacuna a declarar. O defeito e so o carimbo redundante e mentiroso: o instante exato de cada execucao **ja e fato do engine** — `activity-instance` de `ST_CheckConsent` (endTime = quando o gate rodou e passou) e de `ST_BuildCarePlan` (endTime = quando o enrollment rodou), consultavel via `GET /history/activity-instance?processInstanceId=...&activityId=ST_CheckConsent|ST_BuildCarePlan`. Mesmo tratamento e mesma razao do `intake_ts` removido em SP-OP-FRAUDE-001 (FAB-INTAKE-CASO-REGISTRADO): um segundo carimbo no escopo seria fonte de verdade redundante, nao correcao. As duas chaves foram REMOVIDAS sem substituicao; `consentimento_ativo`/`enrollment_realizado` (os fatos booleanos reais, com consumidor documentado alhures) permanecem inalterados.

## Variaveis de proveniencia do agente (Valentina — ADR-0007/ADR-0015)

Convencao repo-wide de nao-repudio (ADR-0007) e delegacao A2A (ADR-0015) — nao especifica de
PROGRAMA (mirror `source_agent_id`/`source_agent_version` de
`docs/processes/contracts/SP-OP-ESCALATION-001.md`). Semeadas por
`ValentinaGraph._contract_variables` (`src/maezo/agents/valentina/graph.py`) junto com as variaveis
de entrada; NENHUMA delas e um desligamento clinico nem uma decisao de programa — so proveniencia,
dossie instrutivo e roteamento humano (CC-13 — Agent Fleet Audit: antes deste registro,
`_contract_variables` as emitia sem declaracao no contrato).

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `source_agent_id` | string | nao | Agente que preparou o dossie de estratificacao/cuidado (`valentina`) — cadeia de nao-repudio (ADR-0007) |
| `source_agent_version` | string | nao | Versao do agente Valentina que preparou o dossie (auditoria ADR-0007) |
| `dossie_valentina` | json | nao | Dossie de estratificacao/plano de cuidado montado por Valentina — instrui `UT_DecisaoClinica`; carrega `decisao_programa`/`motivo_desligamento_clinico`/`referencia_clinica`/`responsavel_clinico_id` sempre `None` (Valentina NUNCA decide) |
| `valentina_task` | string | nao | Tarefa do grafo do Valentina que originou o dossie (`stratify` \| `enroll`) |
| `valentina_route` | string | nao | Roteamento do grafo do Valentina (`auto_route` \| `human_review`) — espelha, nao decide, o roteamento do processo |
| `motivo_encaminhamento` | string | nao | Presente so quando `valentina_route=human_review`; motivo do encaminhamento (`estratificacao_analise_humana` \| `criterio_alta_aparente` \| `dmn_indisponivel` \| `falha_tecnica` \| `outro`) |
| `grupo_destino` | string | nao | Presente so quando `valentina_route=human_review`; grupo humano sugerido por Valentina (catch-all `coordenacao-clinica`) |
| `dmn_decision_refs` | json | nao | Referencias auditaveis (tabela→regra) das DMN que Valentina consultou (`programa_routing`/`programa_sla`) — cadeia de decisao (ADR-0007/ADR-0012) |

## Topicos

Convencao `{dominio}.{contexto}.{acao}` (registro central em `config/topic_registry.yaml` — **W0.2 e o unico editor**; este contrato apenas declara o que precisa ser registrado). Contexto = `programa`.

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.proactive` | **consome (start)** | trigger proativo PHI-minimizado (D9: start-signal-only; `consent_checked==true` exigido) |
| Kafka | `agents.events.programa.received` | produz | apos start (enrollment iniciado; **sem PHI clinico cru**) |
| Kafka | `agents.events.programa.consent_blocked` | produz | chokepoint barrou por falta de consentimento (auditoria LGPD) |
| Kafka | `agents.events.programa.processing_stopped` | produz | processamento interrompido por revogacao (auditoria LGPD) |
| Kafka | `agents.events.programa.sla_breached` | produz | SLA de decisao clinica estourado |
| Kafka | `agents.events.programa.completed` | produz | fim (payload.desfecho = `enrollment_realizado` \| `nao_elegivel` \| `sem_consentimento` \| `interrompido_revogacao` \| `desligamento_clinico_humano` \| `acompanhamento_concluido`). **NEW-05 (reparo §Delta F1/F2 de FAB-PUBLISH-CONTACT):** o payload de `ST_PublishCompleted` passa a carregar tambem os tokens de lacuna `contato_gap` e `enrollment_gap` — vocabulario FECHADO e sem PHI, ver "Variaveis de saida". Antes, quem consumia o desfecho lia `enrollment_realizado` sem saber que **ninguem foi contatado** (`ST_ProactiveContact` nao tem canal de saida ligado) nem que **nenhum plano de cuidado foi montado** (`care.enroll` nao esta ligada): a lacuna existia no escopo do processo e PARAVA nele — verbatim o defeito ja corrigido nas seis tasks de desfecho de SP-OP-FRAUDE-001 pela mesma WP. Os dois emissores (`ST_BuildCarePlan`, `ST_ProactiveContact`) vivem em `SUB_Cuidado`, unico predecessor de `ST_PublishCompleted`, entao as variaveis estao em escopo quando a task de publicacao inicia. A composicao e do BPMN (`event_payload_vars`), nao do worker; `events.py` so copia a variavel se ela existir no escopo, entao ao ligar as costuras reais a chave some do payload — **ausencia continua significando "ocorreu de verdade"** |
| External task | `operadora.events.publish` | consome (worker) | publicador generico de eventos de dominio (reuso) |
| External task | `operadora.programa.check_consent` | consome (worker) | **CHOKEPOINT** — verifica consentimento ativo para `consent_scope`; lanca `ERR_PROGRAMA_NO_CONSENT` se ausente/revogado (fail-closed) |
| External task | `operadora.programa.stratify_risk` | consome (worker→A2A) | estratificacao de risco **in-zone** APOS o gate (delegacao `care.stratify` a Valentina — **instrui, nao decide**) |
| External task | `operadora.programa.build_care_plan` | consome (worker→A2A) | **ALVO (a ligar):** monta plano de cuidado / dossie para a User Task clinica (delegacao `care.enroll` a Valentina — **sem decidir alta**). **HOJE (ENROLL-BENEFICIARIO-SEM-EFEITO-REAL):** o worker `enroll_beneficiario` NAO monta plano de cuidado algum — sem chamada A2A, sem plano, sem dossie — e retorna exatamente `{enrollment_gap: enroll_a2a_nao_ligado}`. **NAO afirma `enrollment_realizado=true`** (antes retornava essa constante de um corpo cuja unica instrucao era `logger.info`) |
| External task | `operadora.programa.proactive_contact` | consome (worker) | **ALVO (a ligar):** contato proativo com o beneficiario (so com `consent_checked==true`; re-busca PHI in-zone — precedente Helena/WhatsApp, D9). **HOJE (NEW-A2-2):** o worker `proactive_contact` **nao contata ninguem** — nenhum canal de saida ao beneficiario esta ligado a esta task — e retorna exatamente `{contato_gap: contato_beneficiario_nao_ligado}`. **NAO afirma `contato_realizado=true`** (antes retornava essa constante em ambos os caminhos, inclusive no `kafka=None`, de um corpo cuja unica instrucao efetiva era `logger.info`). O guard de consentimento (`ERR_PROGRAMA_NO_CONSENT` como `ProgramaError` -> incidente, pois a task nao carrega error boundary) segue inalterado e fail-closed |
| External task | `operadora.programa.stop_processing` | consome (worker) | **para o tratamento de PHI** apos revogacao (boundary interruptivo); registra cessacao (LGPD) |
| External task | `operadora.programa.notify_sla_risk` | consome (worker) | worker `notify_sla_risk` + wrapper `make_notify_sla_risk_handler`: o wrapper publica o alerta em `operadora.notifications.internal` (`best_effort=False`) quando ha produtor Kafka, e AMBOS os caminhos retornam `{}` — **NAO afirmam `sla_risk_notified`** (FAB-SLA-RISK-NOTIFIED-SLICE4; antes o caminho `kafka is None` devolvia `True` sem ter publicado nada, e o registro interno e pedido de alerta, nunca prova de que a `coordenacao-clinica`/`equipe-cuidado` foi avisada). Informativo e nunca adverso: `UT_DecisaoClinica` segue aberta |
| External task | `operadora.programa.register_program_discharge` | consome (worker) | **efeito adverso gated (clinico)** — registra desligamento clinico; recusa sem decisao humana (`ERR_PROGRAM_DISCHARGE_NOT_HUMAN`); carrega `responsavel_clinico_id` |
| Message BPMN | `msg.programa.consent_revoked` | **recebe (interruptivo)** | correlacao por business key — revogacao de consentimento (de SP-OP-LGPD-DSR-001 ou canal direto) → boundary event interrompe e para |
| Message BPMN | `msg.programa.info_received` | recebe | correlacao por business key — info clinica solicitada pela User Task chegou |

## DMN referenciadas

Shape engine-deployavel (§4-bis-A, ADR-0018 parte 2): `typeRef ∈ {string, boolean, integer, long, double, date}` — **`number` e invalido**; dias/SLA → string ISO 8601. Toda `decisionTable` com `hitPolicy`; toda tabela com row catch-all → caminho humano conservador. `camunda:historyTimeToLive` namespaced (`P###D`) em cada `<decision>`. **Nenhuma DMN tem coluna de saida de desligamento/alta clinica nem de negativa de cuidado.** As DMN clinicas operam **in-zone** (PHI) e so emitem bandas/roteamento a Zona Geral.

### `programa_routing` (hitPolicy FIRST — DRAFT; **in-zone**; criterios clinicos DRAFT/verify medico; GAP-PROG-5: id deployado — o nome de trabalho `programa_stratification` nunca foi deployado)
in: `risco_estratificado: string`, `elegibilidade_criterios_atendidos: boolean`
out: `elegivel_programa: string` (`ELEGIVEL` | `NAO_ELEGIVEL` | `ANALISE_HUMANA`), `motivo: string`
**Apenas estratifica/sugere elegibilidade — sem saida que desligue/dê alta/negue cuidado.** Caso clinico ambiguo / risco alto / criterio nao avaliavel → `ANALISE_HUMANA`. Catch-all → `ANALISE_HUMANA` (conservador → clinico humano). **Roda so APOS o chokepoint de consentimento.**

### `programa_sla` (hitPolicy UNIQUE — DRAFT; prazos DRAFT/verify)
in: `programa_id: string`
out: `sla_decisao: string` (ISO 8601), `sla_alerta: string` (ISO 8601), `fonte: string`
SLA da decisao clinica (quando ha User Task). Prazos como string ISO — **DRAFT/verify** (politica clinica interna; confirmar se ha RN de programa). **GAP-PROG-6 (reconciliado):** este contrato ja declarou `elegivel_programa` como segundo input num rascunho anterior a autoria do BPMN (fase-a-frente, playbook 5-bis) — aspiracional, nunca implementado. Com o BPMN autorado (`SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn`), `BRT_Sla` (`decisionRef=programa_sla`) so e alcancado pelo ramo `ANALISE_HUMANA` de `GW_Elegibilidade` (`Flow_GW_AnaliseHumana`); os ramos `ELEGIVEL`/`NAO_ELEGIVEL` vao direto a `ST_ProactiveContact`/`End_NaoElegivel` e NUNCA chegam a esta DMN. Logo `elegivel_programa` seria SEMPRE `"ANALISE_HUMANA"` no unico ponto em que `programa_sla` roda — diferenciar o SLA por esse input e vacuo: sob `hitPolicy UNIQUE` exigiria linhas exaustivas para `ELEGIVEL`/`NAO_ELEGIVEL` que nunca disparariam (branching morto — risco de sugerir falsa capacidade numa DMN de prazo regulatorio). O input single (`programa_id`) e a realidade arquitetural correta; a discriminacao por elegibilidade fica no gateway a montante (`GW_Elegibilidade`), nao na DMN de SLA. Taxonomia de `programa_id` segue sendo o unico eixo de discriminacao (placeholder DRAFT/verify medico — nota da DMN).

> **Nota:** o chokepoint de consentimento e a revogacao **nao** sao DMN — sao gate de worker
> (`check_consent`/`ERR_PROGRAMA_NO_CONSENT`) e boundary event interruptivo (`consent_revoked`),
> respectivamente. Consentimento e fato verificavel, nao regra de decisao.

## Papeis humanos (candidate groups)

> **PROPOSTO — confirmar contra a taxonomia organizacional da operadora** (ver Pendencias). Nomes
> DRAFT; podem nao corresponder aos grupos reais do IdP/console de User Tasks.

| Grupo | Papel | Tarefa |
|---|---|---|
| `coordenacao-clinica` | Coordenacao clinica / medico do programa | `UT_DecisaoClinica` (**desligamento clinico SO aqui**: `decisao_programa ∈ {ENROLL, MANTER_ACOMPANHAMENTO, DESLIGAR_CLINICO, SOLICITAR_INFO}`) |
| `equipe-cuidado` | Equipe multiprofissional de cuidado | `UT_DecisaoClinica` (co-candidate group para decisao clinica de cuidado revisada) |

Toda `<bpmn:userTask>` traz `camunda:candidateGroups` (gate D1). O desligamento clinico (decisao
clinica L0-hard) NUNCA muda de natureza por estouro de SLA — `coordenacao-clinica` assume com os
mesmos campos obrigatorios.

## SLAs

| Timer | Valor (DRAFT/verify) | Tipo | Fonte |
|---|---|---|---|
| Alerta de risco (`BT_AlertaSlaPrograma`) | 60–70% de `${sla.sla_alerta}` (DMN `programa_sla`) | nao-interruptivo → `operadora.programa.notify_sla_risk` | politica interna |
| Decisao clinica (`BT_SlaDecisao`) | `${sla.sla_decisao}` (ex.: **P7D** — **DRAFT/verify** politica clinica) | interruptivo → publica `programa.sla_breached` → (se ha decisao clinica pendente) cancela `UT_DecisaoClinica`, cria tarefa de `coordenacao-clinica` | politica clinica interna — **DRAFT** |

Nota: o caminho L3 consentido (estratificar, enroll informativo, acompanhar) e majoritario e nao
tem timer interruptivo de decisao humana. **No estouro de SLA a coordenacao clinica assume; nunca
ha auto-desligamento por timeout** (desligamento clinico nunca nasce de timer). O **boundary de
revogacao** (B) e independente do SLA — pode interromper a qualquer momento.

## Desfecho de agente: falha de start (CC-01)

| Desfecho | Onde vive | Quem escreve | Significado |
|---|---|---|---|
| `erro_inicio_processo` | **estado do agente valentina** — NAO e variavel de processo | no `notify_start_failure` do grafo, via o helper unico `maezo.runtime.start_outcome.notify_start_failure` | o agente TENTOU iniciar SP-OP-PROGRAMA-001 pelo chokepoint `start_process_idempotent` e o engine recusou (`CibSevenError`). NENHUMA instancia nasceu |

ONDE ESTE VALOR **NAO** ESTA, e por que. Ele nunca chega ao engine: nao consta de
`## Variaveis de entrada` nem de `## Variaveis de saida`, nao tem `bpmnError` associado, nao
aparece em nenhum `camunda:` do BPMN e **nao exige mudanca nenhuma no BPMN deste processo**. Nao
poderia ser diferente — o processo NAO nasceu, entao nao existe instancia onde gravar uma
variavel nem escopo onde lancar um erro. Ele e declarado AQUI, no contrato, porque e um desfecho
CONTRATUAL do agente que serve este processo e porque quem consome o estado do agente (o handler
A2A, um golden de eval, uma regra de alerta) precisa do literal exato e estavel.

POR QUE ELE EXISTE (auditoria de frota 2026-09-04, achado CC-01). Ate essa data a falha de start
era engolida: o `except CibSevenError` do no de start devolvia apenas `process_started=false` e a
aresta seguinte era INCONDICIONAL para um terminal no-op, de modo que o desfecho de SUCESSO ja
gravado a montante (`enrollment_realizado`) sobrevivia — o estado do agente AFIRMAVA um fato que nao
aconteceu. E o caso se perdia em silencio, porque os prazos desta especificacao vivem em timers
da instancia BPMN que nunca nasceu: sem SLA, sem alerta, sem retry.

EFEITOS ASSOCIADOS ao desfecho, todos no lado do agente:

* `process_started = false` (e, quando o agente distingue no-ops legitimos, o marcador
  `start_failed = true`, que e o que a aresta condicional le);
* `record_agent_error()` -> `maezo_agent_errors_total`, o contador que a regra
  `MaezoAgentCrashLoop` observa;
* evento estruturado `agent_process_start_failed` com a business key idempotente deste contrato —
  este evento e o SUBSTITUTO operacional do prazo enquanto a instancia nao existe;
* RETRY seguro por construcao: `start_process_idempotent` e idempotente por business key, entao
  uma reentrega reencontra a instancia viva (`ALREADY_ACTIVE`) em vez de abrir uma segunda.


## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_PROGRAMA_NO_CONSENT` | **chokepoint** — worker `operadora.programa.check_consent` | gate fail-closed: lancado quando nao ha consentimento ativo/nao-revogado para `consent_scope`. Capturado por boundary error → `End_SemConsentimento`; **nenhum PHI de programa e processado**. (parte estrutural unica deste processo — alem do guard no-adverse) |
| `ERR_PROGRAM_DISCHARGE_NOT_HUMAN` | worker-guard em `operadora.programa.register_program_discharge` | o worker **recusa** registrar desligamento clinico se `decisao_programa != DESLIGAR_CLINICO` setado por humano numa User Task, ou se faltarem `motivo_desligamento_clinico`/`referencia_clinica`/`responsavel_clinico_id`. Lanca BPMN error; instancia nao atinge `End_DesligamentoClinicoHumano`. Registra a tentativa (ADR-0007). (espelha `ERR_*_NOT_HUMAN` de AUTH/CONTAS) |
| `ERR_PROGRAMA_INSTANCIA_INVALIDA` | declarado (`Error_ProgramaInstanciaInvalida`) para uso dos workers | worker lanca BPMN error se a instancia (programa×beneficiario×ciclo) for inconsistente na origem; tratamento a detalhar na promocao a FINAL |

> **Dois mecanismos estruturais distintos:** (1) `ERR_PROGRAMA_NO_CONSENT` e o **chokepoint LGPD**
> que gateia TODO processamento de PHI (risco #6 do phase3-plan: "processar PHI de programa antes
> de consentimento (ou apos revogacao) = violacao LGPD"); (2) `ERR_PROGRAM_DISCHARGE_NOT_HUMAN` e o
> **guard no-adverse clinico** (parte 4 de 5 do padrao ADR-0018). O teste de invariante (parte 5)
> verifica **ambos** na history do engine: (a) nenhum processamento de PHI sem consentimento; (b)
> nenhuma `End_DesligamentoClinicoHumano` sem User Task clinica concluida.

## Notas de design / inversao do reference

- **Consent-gated, NAO happy-path de enrollment.** O processo nao assume que enrollment "da certo";
  assume que o consentimento e o portao de entrada e a revogacao e uma interrupcao sempre possivel.
  O subprocesso de cuidado vive sob um boundary interruptivo de revogacao. Inverte o anti-padrao de
  programas que tratam PHI antes/sem consentimento ou que ignoram a revogacao.
- **Revogacao para de tratar (fail-safe), nao e adverso.** Parar de processar PHI a pedido do
  titular e o comportamento seguro/legal — terminal neutro de cessacao. So o **desligamento
  clinico** (decisao clinica) e adverso e humano.
- **Depende de SP-OP-LGPD-DSR-001** (em main): a `revogacao_consentimento` do DSR e a origem
  canonica do `msg.programa.consent_revoked`; o `consent_event_ref` liga as duas trilhas de
  auditoria. GAP-PROG-4: a correlacao e produzida pela `consent_revocation_bridge`
  (`src/maezo/platform/integrations/consent_revocation_bridge/`) — consome
  `agents.events.lgpd_dsr.completed` (JA publicado por SP-OP-LGPD-DSR-001, nenhum topico novo) e,
  quando `tipo_requisicao=revogacao_consentimento` E `decisao_dsr=EXECUTAR_E_ENVIAR` (revogacao de
  fato EXECUTADA — nao so aprovada/enviada), correlaciona `msg.programa.consent_revoked` por
  `correlation_keys={tenant_id, beneficiario_pseudo_id}` com `all_matching=True` (fan-out ao
  ENGINE, nao business_key unico — um titular pode ter MULTIPLAS instancias PROGRAMA-001 ativas,
  uma por programa x ciclo). `consent_event_ref` = `_business_key` da instancia DSR de origem.
- **Valentina (Zona PHI, D10)** faz `care.stratify`/`care.enroll` **in-zone**, APOS o gate de
  consentimento; **instrui, nao decide** — o clinico humano decide enrollment/alta na User Task
  (principio Rafael/ADR-0005). KPI `false_denial_rate==0` (ramo clinico). NUNCA deixa worker setar
  `decisao_programa=DESLIGAR_CLINICO`.
- **Canal proativo PHI-minimizado (D9):** o trigger `agents.events.proactive` e start-signal-only
  (`consent_checked`, `cohort/risk_band`, `source_ref`), sem conteudo clinico cru; o consumidor
  re-busca PHI in-zone. `consent_checked==true` exigido antes de qualquer contato com o
  beneficiario.
- **TASY write DROP** em todo worker: consumimos o CDC/Feature-Store do amh-data-platform; nunca
  escrevemos no Tasy (MEMORY/ADR-0013).

## Pendencias para promocao a FINAL

- DI (diagrama BPMN) e o BPMN/DMN bodies (autorados em wave posterior contra este contrato).
- **Confirmar a correlacao revogacao** com SP-OP-LGPD-DSR-001 (`revogacao_consentimento` →
  `msg.programa.consent_revoked`) e o efeito imediato (LGPD art. 18 §2 — caminho dedicado citado em
  LGPD-DSR §promocao a FINAL).
- **Taxonomia dos programas** (cronicos / pre-natal / oncologia / APS — OQ do phase3-plan) e os
  criterios de elegibilidade/estratificacao por programa — **DRAFT/verify medico**.
- Confirmar se ha **RN ANS especifica** de programas de promocao a saude / APS (citacao hoje so
  LGPD) — **DRAFT/verify regulatorio**.
- **Confirmacao dos candidate groups** `coordenacao-clinica` / `equipe-cuidado` contra a taxonomia
  organizacional da operadora.
- Politica de retencao/cessacao de PHI apos revogacao (interacao com retencao legal de prontuario —
  Lei 13.787/2018/CFM, espelha LGPD-DSR) — **DRAFT/verify DPO/juridico**.
- Parametros de k-anonimato / small-cell-suppression para qualquer agregado populacional do
  programa exposto a Zona Geral (WP3.5).
- Definir se enrollment exige captura de consentimento (User Task de consentimento) **antes** da
  decisao do coordenador (OQ do phase3-plan: "Enrollment exige captura de consentimento (UT) antes
  da decisao do coordenador?").
- **Ligar um canal real de contato ao beneficiario em `ST_ProactiveContact` (NEW-A2-2 /
  FAB-PUBLISH-CONTACT — ABERTO).** Enquanto nao ligado, `proactive_contact` nao contata ninguem e
  emite `contato_gap=contato_beneficiario_nao_ligado`; o terminal `End_EnrollmentRealizado` e o
  `desfecho=enrollment_realizado` significam "o processo roteou o caminho consentido e terminou",
  NAO "o beneficiario foi contatado". O canal existe no repo — a classe de acao
  `comunicacao_beneficiario` (`spec/policies/autonomy/action-approvals.yaml`) ja lista as
  superficies WhatsApp — mas **quem** fala com o beneficiario nesta etapa, por **qual** canal e com
  **qual** conteudo e decisao de produto/clinica + DPO (D9: re-busca de PHI in-zone), nao de
  engenharia. Ao ligar: a variavel de lacuna deixa de ser emitida (ausencia = contato real) e a
  linha de topico volta a descrever so o ALVO.
- **`ERR_PROGRAMA_INSTANCIA_INVALIDA` declarado e NAO implementado (NEW-06, ABERTO — BLOQUEADO NO
  CONTRATO).** O BPMN declara o objeto de erro (`Error_ProgramaInstanciaInvalida`) e a tabela de
  codigos diz "worker lanca BPMN error se a instancia (programa x beneficiario x ciclo) for
  inconsistente na origem", mas (i) **nenhuma regra define o que torna a instancia inconsistente**
  (o proprio contrato remete o tratamento "a detalhar na promocao a FINAL") e (ii) **nenhum
  `errorEventDefinition` no BPMN captura esse codigo**: nao ha boundary event algum para ele. Os
  dois pontos sao bloqueantes e nao sao de engenharia. O (ii) e mecanico e verificavel:
  `scripts/ci/check_bpmn_error_allowlist.py` FALHA (clausula (b), "FAIL on an unproven raise") para
  um `WorkerBpmnError` sem cobertura de consumo, e no CIB Seven 2.1.0 um `bpmnError` nao modelado
  **encerra silenciosamente o escopo do processo** em vez de abrir incidente — implementar o raise
  hoje trocaria um codigo declarado-sem-uso por um encerramento silencioso. Ordem obrigatoria:
  produto/clinica pina a regra de consistencia -> o BPMN ganha o boundary + o caminho de tratamento
  -> so entao o worker levanta. Ate la a constante permanece **declarada e sem call site**.
- **Ligar a delegacao A2A `care.enroll` a Valentina (ENROLL-BENEFICIARIO-SEM-EFEITO-REAL —
  ABERTO).** Enquanto nao ligada, `enroll_beneficiario` nao monta plano de cuidado/dossie algum e
  emite `enrollment_gap=enroll_a2a_nao_ligado`; o desfecho `enrollment_realizado` publicado em
  `programa.completed` significa hoje "o caso foi roteado como elegivel", NAO "um plano de
  cuidado foi montado" — e, **corrigindo para a frente a redacao anterior desta mesma linha**
  ("...e o contato proativo ocorreu"), tambem NAO "o beneficiario foi contatado": ver a
  pendencia de `contato_gap` abaixo. Requer o handler A2A de Valentina para
  `care.enroll` **e** o registro em `a2a_composition` — o registro e **decisao do dono**
  (`FERNANDO-DELEGATION-CALL-SITE`), nao de engenharia. Ao ligar: a variavel de lacuna deixa de ser
  emitida (ausencia = montagem real) e a linha de topico de `build_care_plan` volta a descrever so
  o ALVO.
