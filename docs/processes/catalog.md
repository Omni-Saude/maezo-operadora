# Catalogo de Processos SP-OP (compliance backbone)

Processo BPMN so existe se entregar: SLA regulatorio, HITL mandatorio,
auditoria de nao-repudio ou transacao multi-ator legal. Todo o resto e jornada
de agente (AGJ-*), documentada no `agent.yaml` de cada agente.

| ID | Nome | Gatilho regulatorio | Fase | Status |
|---|---|---|---|---|
| SP-OP-ESCALATION-001 | Escalonamento humano universal | Seguranca assistencial | 0 | modelado FINAL v1.0.0 (SLAs em review-queue) |
| SP-OP-AUTH-001 | Autorizacao previa | RN 259; negativa = medico auditor (L0 hard) | 1 | DRAFT — requires human review before any deploy |
| SP-OP-LGPD-DSR-001 | Direitos do titular | LGPD art. 18 (15 dias) | 1 | DRAFT — requires human review before any deploy |
| SP-OP-CONTAS-001 | Análise e adjudicação de conta médica (glosa) | Padrão TISS (RN 501/2022 — DRAFT/verify); prazo contratual; Lei 9.656 art. 18 | 2 | modelado (suite de integração planejada — T3.1) |
| SP-OP-RECURSO-001 | Análise de recurso de glosa (resposta ao recurso) | Prazo contratual de resposta; Padrão TISS (RN 501/2022 — DRAFT/verify) | 2 | modelado (suite de integração planejada — T3.1) |
| SP-OP-NIP-001 | Resposta a NIP | Prazos ANS | 2 | modelado (suite de integração planejada — T3.1) |
| SP-OP-ANS-SUBMIT-001 | Envios periodicos ANS | Calendario regulatorio | 2 | modelado (suite de integração planejada — T3.1; DMN ans_sla adicionado ao contrato) |
| SP-OP-ANS-CRON-001 | Agendador dos envios ANS (5 definitions, 1 timer por report_type) | Calendario regulatorio (RN 124/209/388/424, DIOPS — DRAFT/verify) | 2 | modelado (agendador puro, NAO negativa-like; sem DMN propria e sem User Task propria — ver a nota sobre a quadrupla abaixo) |
| SP-OP-CANCEL-001 | Cancelamento de contrato | RN 412 | 2 | modelado (suite de integração planejada — T3.1) |
| SP-OP-REEMBOLSO-001** | Reembolso | RN 259 | 2 | modelado (suite de integração planejada — T3.1) |
| SP-OP-INADIMPLENCIA-001 | Suspensao/rescisao | RN 593 | 3 | modelado (suite de integração planejada — T3.1) |
| SP-OP-CRED-001 | (Des)credenciamento | RN 567 | 3 | modelado (suite de integração planejada — T3.1) |
| SP-OP-ADEQUACAO-001 | Adequacao de rede | RN 259 geografia | 3 | modelado (suite de integração planejada — T3.1) |
| SP-OP-FRAUDE-001 | Investigacao de fraude | Cadeia de custodia | 3 | modelado (suite de integração planejada — T3.1) |
| SP-OP-PROGRAMA-001 | Programas de cuidado | Consentimento LGPD | 3 | modelado (suite de integração planejada — T3.1) |
| SP-OP-PAGTO-001 | Pagamentos de alcada | Politica financeira | 3 | modelado (suite de integração planejada — T3.1) |

Quadrupla obrigatoria por processo: `.bpmn` + contrato (`docs/processes/contracts/`) +
DMNs derivadas (`spec/processes/dmn/`) + test spec (`docs/processes/test-specs/`).
Jornadas de agente (sem BPMN): `docs/processes/journeys/` (Phase 0: AGJ-HELENA-TRIAGE).

**Excecao declarada — SP-OP-ANS-CRON-001 nao tem DMN propria** (`grep -o 'camunda:decisionRef'`
no seu BPMN retorna ZERO): e um agendador puro, sem decisao de negocio a tabelar. O calendario
regulatorio (`ans_calendar`) e avaliado ENGINE-SIDE (ADR-0028) por `BRT_Calendario` no processo de
ENVIO, SP-OP-ANS-SUBMIT-001, que e quem consome o fato `ans.cron_due`. As outras tres pernas da
quadrupla existem: BPMN, `contracts/SP-OP-ANS-CRON-001.md` e `test-specs/SP-OP-ANS-CRON-001.md`.
O processo tambem NAO esta em `KNOWN_PROCESS_KEYS` por design (e iniciado por TimerStartEvent,
nunca por `start_process_idempotent`).

**DRAFT/verify — um dos 5 timers agenda obrigacao EXTINTA.** `docs/decisions-log.md:26-27`
(DL-0029/DL-0028) registra de fonte primaria que a **RN 639/2025** desobriga o envio do **SIP**
apos o 4o trimestre/2025 (vigencia 02/03/2026); o despacho regulatorio poe
`SP-OP-ANS-CRON-001-RN124SIP` em **retirada cirurgica** e **exclui a sua cadencia da revisao**
(`docs/sme-dispatch/regulatorio/PACKAGE.md:79-82`). `RN_209_UTILIZACAO` e `RN_388_QUALIDADE` sao
citacoes **miscitadas** que precisam de re-derivacao (`:90-94`). Os literais permanecem no BPMN/
DMN/contrato porque sao a **chave compartilhada** entre esses artefatos — troca-los e decisao de
SME/re-scope (T2.6), nao de engenharia. A ancora temporal da competencia usa o fuso civil
`America/Sao_Paulo` (`ans_cron._BUSINESS_TZ`), tambem **DRAFT/verify** (default de engenharia).

**Registro de decisao — `operadora.programa.monitor_programa` REMOVIDO** (PERSP-C5-MONITOR-PROGRAMA):
o worker estava registrado sob um topico derivado do nome da funcao que NENHUM `serviceTask` de
`SP-OP-PROGRAMA-001` declara — orfao inalcancavel — e o seu unico output era
`monitoramento_atualizado: True`, um fato fabricado (a funcao so logava; nao executava
monitoramento algum). Escolha: **remover** funcao e registro, em vez de criar um `serviceTask`
para servi-lo — nao ha atividade de monitoramento modelada no BPMN, e inventar uma seria modelar
processo regulatorio sem dono. `register_programa_workers` passa de 8 para 7 topicos, todos
declarados pelo BPMN; pinado por
`tests/unit/tools/workers/test_programa.py::test_register_programa_workers_registers_all_7_topics`.

**Registro de decisao — `operadora.fraude.publish_completed` e `operadora.pagto.publish_completed`
REMOVIDOS** (FAB-PUBLISH-CONTACT, achado NEW-A2-1): os dois workers estavam registrados sob topicos
derivados do nome da funcao que NENHUM `serviceTask` declara — todo `ST_Publish*` de SP-OP-FRAUDE-001
e de SP-OP-PAGTO-001 roteia pelo generico `operadora.events.publish` — logo eram orfaos inalcancaveis
pelo engine. Cada um devolvia (a) `evento_publicado: True`, fato fabricado que entrava na instancia de qualquer
jeito — o `complete` da harness grava o retorno INTEIRO no escopo (`dict(out_vars)`, sem filtro) — e
que, por a chave estar em `_SAFE_DECISION_BASIS_KEYS` (a allowlist do `decision_basis` do ADR-0007,
`harness.py::build_decision_basis`), ainda ia parar na trilha NAO-REPUDIAVEL; tudo isso de um corpo
cuja unica instrucao era `logger.info`
(nenhum dos dois modulos tem call site de `kafka.publish(`; `register_fraude_workers` faz
`del kafka  # unused`), e (b) um `desfecho` recalculado em Python, SEGUNDA fonte de verdade para o
vocabulario que o BPMN ja fixa como literal `event_desfecho` em cada task de publicacao — e errada no
caso de fraude, que mapeava tudo que nao fosse `ACUSAR_FRAUDE` para `arquivado_sem_indicio`, isto e,
4 dos 5 terminais. Escolha: **remover** funcao e registro, em vez de (i) criar um `serviceTask` para
servi-los — publicacao ja e modelada e servida por `events.py`, o unico ponto do repo que publica de
verdade e que reporta `event_published` a partir do bool de entrega REAL do produtor — ou (ii) emitir
um token `GAP_*`, que afirmaria uma lacuna INEXISTENTE (o evento E publicado), o mesmo defeito de
honestidade na direcao oposta ja registrado na docstring de `fraude.intake`. Mesma decisao e mesma
forma de `operadora.lgpd.publish_completed` (LGPD-PUBLISH-COMPLETED-ORPHAN-TOPIC, decisao do dono
R-103, remedio R-H) e de `operadora.programa.monitor_programa` (PERSP-C5-MONITOR-PROGRAMA, acima).
`register_fraude_workers` passa de 11 para 10 topicos, todos declarados pelo BPMN; pinado nos DOIS
sentidos (sem worker faltando E sem orfao) por `tests/integration/processes/test_sp_op_fraude_001.py::
test_bpmn_fraude_topics_vs_registered_workers` — que era, ele proprio, o pin que pedia esta edicao
("atualize este teste e a suite se corrigido") — e por
`tests/unit/tools/workers/test_{fraude,pagto}.py::test_*_nao_registra_topico_orfao_publish_completed`.
A familia irma que devolve `published: True` sob a mesma forma (`ans_submit`, `cancel`, `contas`,
`nip`, `recurso`, `reembolso`) NAO foi tocada aqui e segue **ABERTA**: cada um desses seis tem o seu
proprio mapa de consumidores a refazer, e cerca-los sem corrigi-los exigiria seis entradas de baseline
que pareceriam cobertura sem nada ter mudado.

\*\* SP-OP-REEMBOLSO-001: nenhum `agent.yaml` declara este processo em `process_keys`
(`grep -rn REEMBOLSO spec/agents/*/agent.yaml` -> 0 hits) — arbitrado como MENOR PRIVILEGIO
CORRETO, nao lacuna (PERSP-REEMBOLSO-BINDING, WP-AGENT-BINDINGS-EXEC). Marina serve o processo
via A2A (`operadora.reembolso.analyze_request`) mas nunca o INICIA: seu node `start_process` é
NO-OP nesse fluxo — `grep -n start_process src/maezo/agents/marina/graph.py` mostra o comentario
"`start_process` is a NO-OP (Marina never starts a second instance)" na linha 33, e o corpo real
do guard em `graph.py:685` (`if _flow(state) == "reembolso": return {}  # SP-OP-REEMBOLSO-001 is
already running`). Ela é convocada de DENTRO de uma instância já em execução
(`ST_PrepararDossie`, depois de `BRT_Admissibilidade`/`BRT_Calculo`/`BRT_AutoApproval` já terem
rodado) — nunca abre uma instância nova. `process_keys` é o allowlist do effect-PEP
(`effect_pep.py:487,493-494` — `declared_keys & KNOWN_PROCESS_KEYS`, consultado por
`allows_process_key`), logo a ausência de SP-OP-REEMBOLSO-001 ali é exatamente o comportamento
correto para um agente que nunca chama `start_process_idempotent` para esse processo. Fechado
como NÃO-DEFEITO nesta linha, para que a próxima auditoria não a reabra.

> Validar prazos exatos das RNs com regulatorio antes de modelar timers.
Redesign completo: ver `operadora-process-redesign.md` no projeto de arquitetura.

## Fundação product-first — inventário E01 (2026-09-10)

Inventário XML do candidato `2ec0ac99ef1baef34e283468f151b5d02b774fb0`: **16 arquivos, 20 definições, 43 User Tasks**. ANS-CRON contém cinco definições; não são cinco jornadas humanas. Esta seção é desenho/inventário de fonte, não aceitação operacional. Contratos ligados abaixo preservam seus estados DRAFT/FINAL e suas pendências de ratificação.

A jornada AUTH e sua matriz requisito → BPMN/DMN → fato/worker/gateway → evento → API/público/recibo ficam no [contrato AUTH](contracts/SP-OP-AUTH-001.md#portal-auth--fronteira-de-construção-e01-2026-09-10); o complemento [ESCALATION](contracts/SP-OP-ESCALATION-001.md#portal-e-relação-com-auth--interface-e01-2026-09-10) delimita o handoff. Os contratos são a fonte dos inputs autoritativos e decisões; nomes abaixo são referências executáveis, não regras novas.

### Responsabilidades, reutilização e lacunas

| Família | Responsabilidade e gatilho / públicos | Classificação product-first e handoffs |
|---|---|---|
| AUTH | Guia de autorização do prestador; beneficiário acompanha/responde; auditor/coordenação/junta decidem | Reuso da instância e quatro tarefas; wiring novo de intake/documentos/inbox e autoridade. Sem AUTH→PAGTO ou start ESC automático. Desistência livre não contratada; prazo extra é lacuna comportamental explícita no contrato |
| ESCALATION | Handoff de conversa por agente; atendimento/supervisão; beneficiário acompanha | Reuso universal. Inbox e autoridade corrente são wiring; humano-only start requer extensão de proveniência. Retorno `devolvido_agente` não decide outro processo |
| CONTAS | Conta recebida do prestador; operadora adjudica/glosa | Reuso. Expor intake/resultado; glosa é decisão da operadora. Não abrir RECURSO automaticamente |
| RECURSO | Recurso iniciado pelo prestador contra glosa; operadora revisa | Reuso de lifecycle distinto de CONTAS; exige glosa/caso de origem e manifestação do prestador, não duplica adjudicação inicial |
| PAGTO | Ordem de pagamento com lastro; finanças e alçada | Reuso após obrigação e controles próprios. Não anexar a AUTH por conveniência nem duplicar pagamento; preservar dedup permanente e I-PAGTO-1 |
| REEMBOLSO | Pedido do beneficiário; análise e coordenação de reembolso | Reuso distinto de guia de prestador. Evidência/valor aprovados precedem handoff PAGTO; Marina prepara dossiê sem segundo start |
| CANCEL | Pedido de cancelamento ou referência contratada; gestão/jurídico | Reuso de cancelamento de CONTRATO; não usar para retirada de guia. Recebe INAD/FRAUDE sob gates humanos/anti-dupla-terminação |
| INADIMPLENCIA | Inadimplência contratual; cobrança/coordenação | Reuso de purga/notificação/suspensão e handoff CANCEL. Responsabilidade distinta do terminal de rescisão. Correção DI somente; regras/prazos não alterados |
| CRED | Solicitação de rede/credenciamento ou descredenciamento; rede/jurídico | Reuso do contrato/tarefas; prestador recebe projeção autorizada. Reutilizar padrão documental, não extrair subprocesso por similaridade |
| ADEQUACAO | Lacuna de rede; rede/coordenação | Reuso da remediação populacional/geográfica, não duplicação do caso individual CRED; integração de fatos/visibilidade segue contrato |
| FRAUDE | Indício com evidências; investigação/jurídico/coordenação | Reuso restrito; acusação permanece humana. Handoff contratual não vira decisão automática; nenhuma inbox externa expõe investigação |
| PROGRAMA | Adesão/consentimento e plano de cuidado; equipe/coordenação | Reuso dos subprocessos e mensagens de consentimento; não criar BPMN por canal. Não recriar worker monitor_programa órfão removido |
| LGPD-DSR | Pedido do titular; identidade e revisão DPO | Reuso próprio de direitos/custódia; upload de AUTH não autoriza operação DSR. Não generalizar identidade verificada entre finalidades |
| NIP | Reclamação ANS; regulatório/jurídico/coordenação | Reuso de resposta e handoff ANS-SUBMIT; comunicação portal não substitui protocolo regulatório |
| ANS-SUBMIT | Obrigação/report ou handoff NIP; regulatório/jurídico | Reuso de envio/retry/retificação com protocolo; não duplica o scheduler. Não tratar publicação Kafka como aceite ANS |
| ANS-CRON | Cinco timer starts do calendário; monitoramento staff | Reuso de agendamento sem task ou DMN própria; wiring para ANS-SUBMIT. SIP e referências regulatórias continuam pendências SME já registradas; nenhum start manual novo |

**Conclusão da inspeção de duplicação:** nenhuma duplicação de responsabilidade demonstrada justifica BPMN novo ou fusão neste slice. AUTH/REEMBOLSO, CONTAS/RECURSO, INAD/CANCEL e CRON/SUBMIT compartilham padrões, mas têm gatilhos/autoridade/terminais distintos. Catálogo humano, custódia documental, inbox, gateways e receipts devem ser reutilizados no código. Dez jornadas AGJ existentes continuam fora deste inventário BPMN; falta de aceitação consolidada não prova ausência de implementação.

**Classificação de ações comuns:** login, navegação, busca/filtros, histórico e leitura de recibo são aplicação; claim/release são wiring de gateway; reassign exige extensão backend/nativa; intake e resposta documental são wiring durável do processo existente; inbox exige consumidor real; novos prazos/desfechos exigem contrato/SME. Nenhum processo novo foi autorizado pela inspeção.

### Inventário executável por definição

IDs estáveis abaixo permitem conferir tarefas, atores, entradas/saídas e esperas sem abrir toda a especificação. Versão numérica é a do deployment, ausente no XML: o conjunto compatível deve fixar versão/digest real e jamais aplicar o formulário mais novo a uma tarefa antiga. Inputs e seu significado ficam na seção de variáveis do contrato indicado; fontes ainda não qualificadas permanecem lacunas, não fatos assumidos.

#### SP-OP-ADEQUACAO-001

Fonte: [SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn](../../spec/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-ADEQUACAO-001.md).

- Gatilhos: `Start_AvaliacaoAdequacao` (contract start).
- Humanos (2): `UT_DecisaoFallback` → `gestao-rede,coordenacao-rede`; `UT_CoordenacaoRede` → `coordenacao-rede`.
- DMNs: `adequacao_gap`, `adequacao_remediation_routing`, `adequacao_sla`. Workers: `operadora.adequacao.calculate_gap`, `operadora.adequacao.measure_coverage`, `operadora.adequacao.notify_rede`, `operadora.adequacao.notify_sla_risk`, `operadora.adequacao.prepare_remediation_dossier`, `operadora.adequacao.register_fallback_commitment`, `operadora.adequacao.start_credenciamento`, `operadora.adequacao.update_monitoring_plan`, `operadora.events.publish`.
- Esperas/timers: `TD_AlertaSlaAdequacao` = `${sla.sla_alerta}`; `TD_SlaRemediacao` = `${sla.sla_remediacao}`. Mensagens: `msg.adequacao.rede_atualizada`, `msg.adequacao.rede_atualizada`.
- Eventos: `agents.events.adequacao.completed`, `agents.events.adequacao.gap_detected`, `agents.events.adequacao.received`, `agents.events.adequacao.sla_breached`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_AdequacaoConforme` (Adequacao conforme (rede dentro dos tempos/distancias RN 259)); `End_MonitoramentoAtualizado` (Plano de monitoramento atualizado (segue observando)); `End_RemediacaoEncaminhada` (Remediacao encaminhada a credenciamento (sem compromisso de caixa)); `End_RiscoSlaNotificado` (Risco de SLA notificado (analise segue)); `End_CompromissoFallbackHumano` (Compromisso de fallback firmado (no-adverse — humano)).

#### SP-OP-ANS-CRON-001-RN124SIP

Fonte: [SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn](../../spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-ANS-CRON-001.md).

- Gatilhos: `Start_CronRn124Sip` (timeCycle:R/P1M).
- Humanos (0): nenhum.
- DMNs: —. Workers: `operadora.ans_cron.trigger_submissions`, `operadora.events.publish`.
- Esperas/timers: `TD_CronRn124Sip` = `R/P1M`. Mensagens: —.
- Eventos: `operadora.notifications.internal`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_CronRn124Sip` (Ciclo RN_124_SIP concluido).

#### SP-OP-ANS-CRON-001-RN209

Fonte: [SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn](../../spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-ANS-CRON-001.md).

- Gatilhos: `Start_CronRn209` (timeCycle:R/P1M).
- Humanos (0): nenhum.
- DMNs: —. Workers: `operadora.ans_cron.trigger_submissions`, `operadora.events.publish`.
- Esperas/timers: `TD_CronRn209` = `R/P1M`. Mensagens: —.
- Eventos: `operadora.notifications.internal`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_CronRn209` (Ciclo RN_209_UTILIZACAO concluido).

#### SP-OP-ANS-CRON-001-RN388

Fonte: [SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn](../../spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-ANS-CRON-001.md).

- Gatilhos: `Start_CronRn388` (timeCycle:R/P1Y).
- Humanos (0): nenhum.
- DMNs: —. Workers: `operadora.ans_cron.trigger_submissions`, `operadora.events.publish`.
- Esperas/timers: `TD_CronRn388` = `R/P1Y`. Mensagens: —.
- Eventos: `operadora.notifications.internal`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_CronRn388` (Ciclo RN_388_QUALIDADE concluido).

#### SP-OP-ANS-CRON-001-RN424TISS

Fonte: [SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn](../../spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-ANS-CRON-001.md).

- Gatilhos: `Start_CronRn424Tiss` (timeCycle:R/P1M).
- Humanos (0): nenhum.
- DMNs: —. Workers: `operadora.ans_cron.trigger_submissions`, `operadora.events.publish`.
- Esperas/timers: `TD_CronRn424Tiss` = `R/P1M`. Mensagens: —.
- Eventos: `operadora.notifications.internal`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_CronRn424Tiss` (Ciclo RN_424_TISS_MONITORAMENTO concluido).

#### SP-OP-ANS-CRON-001-DIOPS

Fonte: [SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn](../../spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-ANS-CRON-001.md).

- Gatilhos: `Start_CronDiops` (timeCycle:R/P3M).
- Humanos (0): nenhum.
- DMNs: —. Workers: `operadora.ans_cron.trigger_submissions`, `operadora.events.publish`.
- Esperas/timers: `TD_CronDiops` = `R/P3M`. Mensagens: —.
- Eventos: `operadora.notifications.internal`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_CronDiops` (Ciclo DIOPS_TRIMESTRAL concluido).

#### SP-OP-ANS-SUBMIT-001

Fonte: [SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn](../../spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-ANS-SUBMIT-001.md).

- Gatilhos: `Start_DespachoEnvio` (contract start); `Start_Retry` (contract start).
- Humanos (5): `UT_CorrigirPendenciaEnvio` → `regulatorio-ans`; `UT_RevisarEnvioJuridico` → `juridico-regulatorio`; `UT_RevisarEnvio` → `regulatorio-ans`; `UT_CoordenacaoEnvioAssume` → `coordenacao-regulatorio`; `UT_TratarNack` → `regulatorio-ans`.
- DMNs: `ans_calendar`, `ans_retry_policy`, `ans_sla`, `ans_submission_admissibility`. Workers: `operadora.events.publish`, `regulatorio.anssubmit.assemble`, `regulatorio.anssubmit.notify_regulatorio`, `regulatorio.anssubmit.retransmit`, `regulatorio.anssubmit.submit`, `regulatorio.anssubmit.track_protocol`, `regulatorio.anssubmit.validate`.
- Esperas/timers: `TD_DeadlineRiskPendencia` = `${sla.sla_alerta}`; `TD_DueDatePendencia` = `${sla.sla_analise}`; `TD_DeadlineRiskJuridico` = `${sla.sla_alerta}`; `TD_DueDateJuridico` = `${sla.sla_analise}`; `TD_DeadlineRisk` = `${sla.sla_alerta}`; `TD_DueDate` = `${sla.sla_analise}`; `TD_AguardarAck` = `P1D`; `TD_Backoff` = `${retry.backoff}`. Mensagens: `msg.anssubmit.ack_received`.
- Eventos: `agents.events.anssubmit.acked`, `agents.events.anssubmit.completed`, `agents.events.anssubmit.deadline_risk`, `agents.events.anssubmit.failed`, `agents.events.anssubmit.generated`, `agents.events.anssubmit.received`, `agents.events.anssubmit.submitted`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_DeadlineRiskNotificado` (Risco de prazo notificado (revisao segue)); `End_EnviadoAck` (Enviado e ACK recebido (assinado por humano)); `End_EnviadoPendenteAck` (Enviado, ACK pendente (nao trava)); `End_RetryOk` (Retransmissao OK (resume aguardo de ACK)); `End_RetryEsgotado` (Retry esgotado (erro -> tratamento humano)); `End_FalhaRetransmissao` (Falha de retransmissao (tratada por humano)); `End_AdiadoHumano` (Envio adiado (decisao humana registrada)).

#### SP-OP-AUTH-001

Fonte: [SP-OP-AUTH-001_Autorizacao_Previa.bpmn](../../spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-AUTH-001.md).

- Gatilhos: `Start_SolicitacaoRecebida` (contract start).
- Humanos (4): `UT_DecidirPendenciaExpirada` → `medico-auditor`; `UT_AnaliseMedicoAuditor` → `medico-auditor`; `UT_CoordenacaoAssume` → `coordenacao-auditoria-medica`; `UT_RegistrarParecerJunta` → `junta-medica`.
- DMNs: `auth_admissibility`, `auth_auto_approval`, `auth_sla`. Workers: `operadora.auth.analyze_request`, `operadora.auth.convene_junta`, `operadora.auth.issue_authorization`, `operadora.auth.notify_sla_risk`, `operadora.auth.request_documents`, `operadora.auth.send_denial_notice`, `operadora.auth.validate_auto_criteria`, `operadora.events.publish`.
- Esperas/timers: `TD_PrazoPendencia` = `P5D`; `TD_AlertaSla` = `${sla.sla_alerta}`; `TD_SlaAnalise` = `${sla.sla_analise}`. Mensagens: `msg.auth.docs_received`.
- Eventos: `agents.events.auth.completed`, `agents.events.auth.pended`, `agents.events.auth.received`, `agents.events.auth.sla_breached`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_NaoRequerAutorizacao` (Nao requer autorizacao); `End_CanceladaPendencia` (Cancelada por pendencia (decisao humana)); `End_AprovadaAutomatica` (Aprovada automaticamente (L2)); `End_RiscoSlaNotificado` (Risco de SLA notificado (analise segue)); `End_AprovadaAuditor` (Aprovada pelo medico auditor); `End_FundamentacaoIncompletaBloqueada` (Negativa formal barrada por fundamentacao incompleta (guard L0) — nada transmitido); `End_NegadaAuditor` (Negada pelo medico auditor (L0)); `End_ErrDecisaoInvalida` (Erro: decisao do auditor ausente ou desconhecida (ERR_AUTH_DECISION_INVALID)).

#### SP-OP-CANCEL-001

Fonte: [SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn](../../spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-CANCEL-001.md).

- Gatilhos: `Start_SolicitacaoCancelamento` (contract start).
- Humanos (2): `UT_AnaliseRescisao` → `juridico-contratos,gestao-contratos`; `UT_CoordenacaoCancelamento` → `coordenacao-contratos`.
- DMNs: `cancel_admissibility`, `cancel_routing`, `cancel_sla`. Workers: `operadora.cancel.confirm_maintained_decision`, `operadora.cancel.effectuate_member_request`, `operadora.cancel.notify_sla_risk`, `operadora.cancel.prepare_dossier`, `operadora.cancel.request_notification`, `operadora.cancel.resolve_facts`, `operadora.cancel.send_cancellation_notice`, `operadora.events.publish`.
- Esperas/timers: `TD_PrazoNotificacao` = `${sla.prazo_notificacao_previa}`; `TD_AlertaSla` = `${sla.sla_alerta}`; `TD_SlaAnalise` = `${sla.sla_analise}`. Mensagens: `msg.cancel.notification_ack`, `msg.cancel.info_received`, `msg.cancel.info_received`.
- Eventos: `agents.events.cancel.completed`, `agents.events.cancel.pended`, `agents.events.cancel.received`, `agents.events.cancel.sla_breached`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_CanceladoBeneficiario` (Cancelado a pedido do beneficiario (direito do titular)); `End_RiscoSlaNotificado` (Risco de SLA notificado (analise segue)); `End_ContratoRescindido` (Contrato rescindido pela operadora (L0 — humano)); `End_ContratoSuspenso` (Contrato suspenso por inadimplencia (L0 — humano)); `End_ManterNaoConfirmado` (Confirmacao MANTER barrada pelo guard (L0) — nada registrado); `End_PedidoCancelamentoNegado` (Pedido de cancelamento negado (L0 — humano)); `End_ContratoMantido` (Vinculo mantido (decisao humana)).

#### SP-OP-CONTAS-001

Fonte: [SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn](../../spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-CONTAS-001.md).

- Gatilhos: `Start_LoteTissRecebido` (contract start).
- Humanos (2): `UT_AnalistaContas` → `auditoria-contas`; `UT_CoordenacaoContasAssume` → `coordenacao-contas`.
- DMNs: `contas_sla`, `glosa_classification`, `glosa_reason_normalization`, `glosa_triage`. Workers: `operadora.contas.analyze_reason`, `operadora.contas.calculate_impact`, `operadora.contas.devolver_conta`, `operadora.contas.emitir_demonstrativo`, `operadora.contas.handoff_pagamento`, `operadora.contas.identify_glosa`, `operadora.contas.notify_sla_risk`, `operadora.contas.prepare_triage_dossier`, `operadora.contas.registrar_glosa`, `operadora.contas.start_fraude`, `operadora.events.publish`.
- Esperas/timers: `TD_AlertaSlaContas` = `${sla.sla_alerta_absoluto_iso}`; `TD_SlaAnaliseContas` = `${sla.sla_analise_absoluto_iso}`. Mensagens: `msg.contas.linhas_atualizadas`.
- Eventos: `agents.events.contas.completed`, `agents.events.contas.received`, `agents.events.contas.sla_breached`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_RiscoSlaNotificado` (Risco de SLA notificado (analise segue)); `End_ContaAprovadaIntegral` (Conta aprovada integralmente — pagamento encaminhado); `End_ContaAprovadaHumano` (Conta aprovada pelo analista (decisao humana)); `End_GlosaAplicadaHumano` (Glosa aplicada pelo analista (L0 — adverso, humano-gated)); `End_PagamentoParcialHumano` (Pagamento parcial com glosa (L0 — adverso, humano-gated, reducao)); `End_ContaDevolvidaPrestador` (Conta devolvida ao prestador para correcao (decisao humana)); `End_EncaminhadaFraude` (Encaminhada a investigacao de fraude (decisao humana)); `End_ErrContasDecisaoInvalida` (Erro: decisao do analista de contas ausente ou desconhecida (ERR_CONTAS_DECISAO_INVALIDA)).

#### SP-OP-CRED-001

Fonte: [SP-OP-CRED-001_Descredenciamento.bpmn](../../spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-CRED-001.md).

- Gatilhos: `Start_SolicitacaoCred` (contract start).
- Humanos (4): `UT_AnaliseDescredenciamento` → `gestao-rede,juridico-rede`; `UT_CoordenacaoRedeDescred` → `coordenacao-rede`; `UT_AnaliseCredenciamento` → `gestao-rede`; `UT_CoordenacaoRedeCred` → `coordenacao-rede`.
- DMNs: `cred_admissibility`, `cred_prior_notice`, `cred_route`, `cred_sla`. Workers: `operadora.cred.check_network_criteria`, `operadora.cred.check_prior_notice`, `operadora.cred.notify_doc_pendente`, `operadora.cred.notify_sla_risk`, `operadora.cred.prepare_dossier`, `operadora.cred.register_cred_denial`, `operadora.cred.register_credenciamento`, `operadora.cred.register_descredenciamento`, `operadora.cred.verify_credentials`, `operadora.events.publish`.
- Esperas/timers: `TD_PrazoNotificacao` = `${notice.prazo_notificacao}`; `TD_AlertaSlaDescred` = `${sla.sla_alerta}`; `TD_SlaDescred` = `${sla.sla_analise}`; `TD_AlertaSlaCred` = `${sla.sla_alerta}`; `TD_SlaCred` = `${sla.sla_analise}`. Mensagens: `msg.cred.info_received`, `msg.cred.notification_ack`, `msg.cred.info_received`, `msg.cred.info_received`, `msg.cred.info_received`, `msg.cred.info_received`.
- Eventos: `agents.events.cred.completed`, `agents.events.cred.network_changed`, `agents.events.cred.pended`, `agents.events.cred.received`, `agents.events.cred.sla_breached`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_CredPrestadorInvalido` (Solicitacao com prestador inconsistente na origem — validacao barrou (fail-safe, nao adverso)); `End_PrestadorCredenciado` (Prestador credenciado (neutro — direcao favoravel)); `End_DecredBloqueadoNaoHumano` (Incidente: registro de descredenciamento barrado pelo guard (sem decisao humana) — NADA descredenciado); `End_PrestadorDescredenciado` (Prestador descredenciado pela operadora (L1 — humano)); `End_SubstituicaoRegistrada` (Substituicao/redimensionamento registrado (neutro)); `End_VinculoMantido` (Vinculo mantido (decisao humana)); `End_CredGuardBloqueadoNaoHumano` (Incidente: registro de negativa de credenciamento barrado pelo guard (sem decisao humana) — negativa NAO registrada); `End_CredenciamentoNegado` (Credenciamento negado (L1 — humano)); `End_RiscoSlaNotificado` (Risco de SLA notificado (analise segue)).

#### SP-OP-ESCALATION-001

Fonte: [SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn](../../spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-ESCALATION-001.md).

- Gatilhos: `Start_EscalonamentoSolicitado` (contract start).
- Humanos (2): `UT_TratarEscalonamento` → `${roteamento.grupo_atendimento}`; `UT_SupervisorAssume` → `supervisao-atendimento`.
- DMNs: `escalation_routing`. Workers: `operadora.escalation.notify_supervisor`, `operadora.escalation.notify_team`, `operadora.events.publish`.
- Esperas/timers: `TD_SlaAck` = `${roteamento.sla_ack}`; `TD_SlaResolucao` = `${roteamento.sla_resolucao}`. Mensagens: —.
- Eventos: `agents.events.escalation.requested`, `agents.events.escalation.resolved`, `agents.events.escalation.sla_breached`, `agents.events.process_completed`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_SupervisorAlertado` (Supervisor alertado (caso segue aberto)); `End_DevolvidoAoAgente` (Devolvido ao agente); `End_ResolvidoPorHumano` (Resolvido por humano).

#### SP-OP-FRAUDE-001

Fonte: [SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn](../../spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-FRAUDE-001.md).

- Gatilhos: `Start_CasoFraudeRecebido` (contract start).
- Humanos (3): `UT_DecisaoInvestigador` → `investigacao-fraude`; `UT_CoordenacaoInvestigacao` → `coordenacao-investigacao`; `UT_RevisaoReferral` → `juridico-fraude`.
- DMNs: `fraude_indicadores`, `fraude_routing`, `fraude_sla`. Workers: `operadora.events.publish`, `operadora.fraude.assemble_dossier`, `operadora.fraude.gather_evidence`, `operadora.fraude.intake`, `operadora.fraude.notify_sla_risk`, `operadora.fraude.refer_to_legal`, `operadora.fraude.register_fraud_accusation`, `operadora.fraude.score_indicators`, `operadora.fraude.seal_custody_bundle`, `operadora.fraude.start_contratual`, `operadora.fraude.start_credenciamento`.
- Esperas/timers: `TED_AlertaSlaFraude` = `${sla.sla_alerta}`; `TED_SlaInvestigacao` = `${sla.sla_investigacao}`; `TED_PrazoDiligencia` = `${sla.sla_diligencia}`. Mensagens: `msg.fraude.evidencia_anexada`, `msg.fraude.diligencia_concluida`.
- Eventos: `agents.events.fraude.completed`, `agents.events.fraude.custody_sealed`, `agents.events.fraude.intake_received`, `agents.events.fraude.sla_breached`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_CustodiaNaoSelada` (Erro: custodia nao selada antes da decisao (ERR_CUSTODY_NOT_SEALED)); `End_RiscoSlaNotificado` (Risco de SLA notificado (investigacao segue)); `End_FraudeConfirmadaHumano` (Fraude confirmada (humano sobre dossie selado) — L0); `End_EncaminhadoCredenciamento` (Encaminhado a SP-OP-CRED-001 (descredenciamento)); `End_EncaminhadoContratual` (Encaminhado a SP-OP-CANCEL/INADIMPLENCIA (rescisao por fraude)); `End_EncaminhadoJuridico` (Encaminhado a juridico/ANS/civel/penal (downstream)); `End_ArquivadoSemIndicio` (Arquivado sem indicio (decisao humana)); `End_MonitorarSemAcao` (Manter sob monitoramento (decisao humana)).

#### SP-OP-INADIMPLENCIA-001

Fonte: [SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn](../../spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-INADIMPLENCIA-001.md).

- Gatilhos: `Start_Inadimplencia` (contract start).
- Humanos (2): `UT_AnaliseInadimplencia` → `juridico-contratos,gestao-cobranca`; `UT_CoordenacaoCobranca` → `coordenacao-cobranca`.
- DMNs: `inadimplencia_purga`, `inadimplencia_sla`, `inadimplencia_status`. Workers: `operadora.events.publish`, `operadora.inadimplencia.check_prior_notice`, `operadora.inadimplencia.handoff_rescisao`, `operadora.inadimplencia.notify_sla_risk`, `operadora.inadimplencia.prepare_dossier`, `operadora.inadimplencia.register_contract_suspension`, `operadora.inadimplencia.resolve_facts`.
- Esperas/timers: `TD_PrazoPurga` = `${purga.prazo_purga}`; `TD_AlertaSla` = `${sla.sla_alerta}`; `TD_SlaAnalise` = `${sla.sla_analise}`. Mensagens: `msg.inadimplencia.pagamento_recebido`, `msg.inadimplencia.notificacao_ack`, `msg.inadimplencia.info_received`, `msg.inadimplencia.info_received`.
- Eventos: `agents.events.inadimplencia.completed`, `agents.events.inadimplencia.notified`, `agents.events.inadimplencia.received`, `agents.events.inadimplencia.sla_breached`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_Purgado` (Inadimplencia purgada (pagamento dentro da janela)); `End_RiscoSlaNotificado` (Risco de SLA notificado (analise segue)); `End_SuspensaoBloqueadaNaoHumano` (Incidente: registro de suspensao barrado pelo guard (sem decisao humana) — NADA suspenso); `End_ContratoSuspenso_Inad` (Contrato suspenso por inadimplencia (L0 — humano)); `End_RescisaoHandoffCancel` (Rescisao encaminhada a CANCEL-001 (handoff — neutro)); `End_ContratoMantido` (Vinculo mantido (decisao humana)).

#### SP-OP-LGPD-DSR-001

Fonte: [SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn](../../spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-LGPD-DSR-001.md).

- Gatilhos: `Start_RequisicaoTitular` (contract start); `Start_SlaGlobal` (timeDuration:P15D).
- Humanos (1): `UT_RevisaoDpo` → `${roteamento_dsr.grupo_revisor}`.
- DMNs: `lgpd_dsr_routing`. Workers: `operadora.events.publish`, `operadora.lgpd.compile_data_package`, `operadora.lgpd.execute_request`, `operadora.lgpd.notify_sla_risk`, `operadora.lgpd.request_additional_proof`, `operadora.lgpd.send_response`, `operadora.lgpd.verify_identity`.
- Esperas/timers: `TD_PrazoProva` = `P10D`; `TD_AlertaDpo` = `${roteamento_dsr.sla_alerta}`; `TD_SlaGlobal` = `P15D`. Mensagens: `msg.lgpd.proof_received`.
- Eventos: `agents.events.lgpd_dsr.completed`, `agents.events.lgpd_dsr.received`, `agents.events.lgpd_dsr.sla_breached`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_IdentidadeInverificavel` (Identidade do titular inverificavel — sem sujeito identificavel (fail-safe, nao adverso)); `End_ExpiradaIdentidade` (Expirada sem verificacao de identidade); `End_RiscoNotificado` (Risco notificado (revisao segue)); `End_ErrDecisaoInvalida` (Erro: decisao do revisor ausente ou desconhecida (ERR_DSR_DECISION_INVALID)); `End_ErrFundamentacaoAusente` (Erro: NEGAR_FUNDAMENTADO sem fundamentacao_legal (ERR_DSR_FUNDAMENTACAO_AUSENTE)); `End_RequisicaoConcluida` (Requisicao concluida (atendida ou negada fundamentada)); `End_SlaGlobalAlertado` (Juridico alertado (caso segue aberto)).

#### SP-OP-NIP-001

Fonte: [SP-OP-NIP-001_Resposta_NIP.bpmn](../../spec/processes/bpmn/SP-OP-NIP-001_Resposta_NIP.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-NIP-001.md).

- Gatilhos: `Start_NipRecebida` (contract start); `Start_NipInstruct` (contract start).
- Humanos (3): `UT_ElaborarRespostaNip` → `${grupo_humano}`; `UT_CoordenacaoNip` → `coordenacao-regulatorio,juridico-regulatorio`; `UT_RevisaoJuridicaNip` → `juridico-regulatorio,medico-auditor`.
- DMNs: `nip_classification`, `nip_routing`, `nip_sla`. Workers: `operadora.events.publish`, `operadora.nip.handoff_ans_submit`, `operadora.nip.instruct_dossier`, `operadora.nip.notify_deadline_risk`, `operadora.nip.submit_response`.
- Esperas/timers: `TD_AlertaPrazoNip` = `${sla.sla_alerta_absoluto_iso}`; `TD_PrazoNipEstourado` = `${sla.prazo_resposta_absoluto_iso}`; `TD_AlertaPrazoRevisao` = `${sla.sla_alerta_absoluto_iso}`; `TD_PrazoRevisaoEstourado` = `${sla.prazo_resposta_absoluto_iso}`; `TD_PrazoInfo` = `${sla.sla_alerta_absoluto_iso}`. Mensagens: `msg.nip.instruct`, `msg.nip.info_recebida`.
- Eventos: `agents.events.nip.breached`, `agents.events.nip.classified`, `agents.events.nip.completed`, `agents.events.nip.deadline_risk`, `agents.events.nip.received`, `operadora.notifications.internal`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_RiscoPrazoNotificado` (Risco de prazo notificado (elaboracao segue)); `End_RiscoPrazoRevisaoNotificado` (Risco de prazo notificado (revisao segue)); `End_NipNegativaMantida` (Negativa mantida (L0 — unico adverso)); `End_NipResolvidaFavoravel` (NIP resolvida favoravelmente (decisao humana)); `End_NipProtocoloInvalido` (Protocolo ANS inconsistente na origem — validacao barrou (fail-safe, nao adverso)); `End_NipNaoAssistencialRespondida` (NIP nao-assistencial respondida (decisao humana)).

#### SP-OP-PAGTO-001

Fonte: [SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn](../../spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-PAGTO-001.md).

- Gatilhos: `Start_OrdemPagamentoRecebida` (contract start).
- Humanos (3): `UT_AnaliseAdmissibilidade` → `coordenacao-financeira`; `UT_AprovacaoAlcada` → `${pagto_alcada.grupo_aprovador}`; `UT_CoordenacaoAlcada` → `coordenacao-financeira`.
- DMNs: `pagto_admissibility`, `pagto_alcada`, `pagto_sla`. Workers: `operadora.events.publish`, `operadora.pagto.calculate_facts`, `operadora.pagto.notify_sla_risk`, `operadora.pagto.prepare_approval_dossier`, `operadora.pagto.register_payment_refusal`, `operadora.pagto.release_high_value_payment`, `operadora.pagto.release_low_value_payment`, `operadora.pagto.validate_payment_data`.
- Esperas/timers: `TD_AlertaSlaPagto` = `${sla.sla_alerta}`; `TD_SlaAprovacao` = `${sla.sla_aprovacao}`. Mensagens: `msg.pagto.dados_corrigidos`.
- Eventos: `agents.events.pagto.completed`, `agents.events.pagto.received`, `agents.events.pagto.routed`, `agents.events.pagto.sla_breached`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_PagtoOrdemInvalida` (Ordem de pagamento inconsistente na origem — validacao barrou (fail-safe, nao adverso)); `End_PagamentoLiberadoAutomatico` (Pagamento liberado automaticamente (abaixo do teto L2)); `End_RiscoSlaNotificado` (Risco de SLA notificado (aprovacao segue)); `End_PagamentoLiberadoHumano` (Pagamento de alto valor liberado (L1 — humano + tier-match)); `End_PagamentoRecusadoHumano` (Pagamento recusado/devolvido para revisao (decisao humana)); `End_PagamentoCancelado` (Ordem de pagamento cancelada/duplicada/sem lastro (neutro — sem liberacao)).

#### SP-OP-PROGRAMA-001

Fonte: [SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn](../../spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-PROGRAMA-001.md).

- Gatilhos: `Start_Programa` (contract start); `Start_Cuidado` (contract start).
- Humanos (2): `UT_DecisaoClinica` → `coordenacao-clinica,equipe-cuidado`; `UT_CoordenacaoDecisao` → `coordenacao-clinica`.
- DMNs: `programa_routing`, `programa_sla`. Workers: `operadora.events.publish`, `operadora.programa.build_care_plan`, `operadora.programa.check_consent`, `operadora.programa.notify_sla_risk`, `operadora.programa.proactive_contact`, `operadora.programa.register_program_discharge`, `operadora.programa.stop_processing`, `operadora.programa.stratify_risk`.
- Esperas/timers: `TD_AlertaSlaPrograma` = `${sla.sla_alerta}`; `TD_SlaDecisao` = `${sla.sla_decisao}`. Mensagens: `msg.programa.info_received`, `msg.programa.info_received`, `msg.programa.consent_revoked`.
- Eventos: `agents.events.programa.completed`, `agents.events.programa.consent_blocked`, `agents.events.programa.processing_stopped`, `agents.events.programa.received`, `agents.events.programa.sla_breached`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_SemConsentimento` (Sem consentimento ativo — nada processado (fail-safe LGPD)); `End_EnrollmentRealizado` (Enrollment realizado (consentido; cuidado em curso)); `End_NaoElegivel` (Nao elegivel ao programa (informativo; NAO e negativa de cobertura)); `End_RiscoSlaNotificado` (Risco de SLA notificado (decisao clinica segue)); `End_DesligamentoClinicoHumano` (Desligamento clinico do programa (L0 — humano)); `End_EnrollmentRealizadoHumano` (Enrollment confirmado por decisao clinica (consentido)); `End_AcompanhamentoConcluido` (Ciclo de acompanhamento concluido (alta administrativa nao-clinica)); `End_ProcessamentoInterrompidoRevogacao` (Processamento interrompido por revogacao (fail-safe LGPD)); `End_ProgramaConcluido` (Programa concluido (desfecho publicado)).

#### SP-OP-RECURSO-001

Fonte: [SP-OP-RECURSO-001_Recurso_Glosa.bpmn](../../spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-RECURSO-001.md).

- Gatilhos: `Start_RecursoRecebido` (contract start).
- Humanos (4): `UT_AnaliseRecursoAnalista` → `analista-recurso-glosa`; `UT_CoordenacaoRecursoAssume` → `coordenacao-recurso`; `UT_EscalonamentoPrazo` → `coordenacao-recurso`; `UT_RevisaoAuditorMedico` → `medico-auditor`.
- DMNs: `recurso_admissibility`, `recurso_eligibility`, `recurso_sla`. Workers: `operadora.events.publish`, `operadora.recurso.analyze_request`, `operadora.recurso.comunicar_resposta`, `operadora.recurso.escalate_ans_timeout`, `operadora.recurso.handoff_pagamento`, `operadora.recurso.notify_sla_risk`, `operadora.recurso.registrar_indeferimento`, `operadora.recurso.request_documents`, `operadora.recurso.validate_recurso`.
- Esperas/timers: `TD_PrazoPendencia` = `P5D`; `TD_AlertaSlaRecurso` = `${sla.sla_alerta}`; `TD_SlaAnaliseRecurso` = `${sla.sla_analise}`; `TD_PrazoMaxRecurso` = `${sla.prazo_max_absoluto_iso}`; `TD_PrazoMaxCoord` = `${sla.prazo_max_absoluto_iso}`; `TD_PrazoMaxAuditor` = `${sla.prazo_max_absoluto_iso}`. Mensagens: `msg.recurso.docs_received`.
- Eventos: `agents.events.recurso.completed`, `agents.events.recurso.pended`, `agents.events.recurso.received`, `agents.events.recurso.sla_breached`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_RecursoGlosaInvalidaOrigem` (Identificador de glosa inconsistente na origem — validacao barrou (fail-safe, nao adverso)); `End_RiscoSlaNotificado` (Risco de SLA notificado (analise segue)); `End_RecursoDeferido` (Recurso deferido (decisao humana — glosa revertida)); `End_RecursoDeferidoParcial` (Recurso parcialmente deferido (decisao humana — L0, reducao adversa)); `End_RecursoIndeferido` (Recurso indeferido (decisao humana do analista — L0 adverso)); `End_RecursoIndeferidoAuditor` (Recurso indeferido pelo medico auditor (decisao humana — L0 adverso)); `End_RecursoInadmissivel` (Recurso inadmissivel (decisao humana — L0 adverso, humano-gated)); `End_ErrRecursoDecisaoInvalida` (Erro: decisao do recurso ausente ou desconhecida (ERR_RECURSO_DECISAO_INVALIDA)).

#### SP-OP-REEMBOLSO-001

Fonte: [SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn](../../spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn); [contrato e inputs autoritativos](contracts/SP-OP-REEMBOLSO-001.md).

- Gatilhos: `Start_SolicitacaoReembolso` (contract start).
- Humanos (4): `UT_DecidirPendenciaExpirada` → `analise-reembolso`; `UT_AnaliseReembolso` → `analise-reembolso`; `UT_CoordenacaoReembolso` → `coordenacao-reembolso`; `UT_RevisaoAuditorMedico` → `medico-auditor`.
- DMNs: `reembolso_admissibility`, `reembolso_auto_approval`, `reembolso_calculo`, `reembolso_sla`. Workers: `operadora.events.publish`, `operadora.reembolso.analyze_request`, `operadora.reembolso.calculate_amount`, `operadora.reembolso.check_coverage`, `operadora.reembolso.check_prazo`, `operadora.reembolso.issue_payment`, `operadora.reembolso.notify_sla_risk`, `operadora.reembolso.request_documents`, `operadora.reembolso.send_reembolso_denial`.
- Esperas/timers: `TD_PrazoPendencia` = `P5D`; `TD_AlertaSla` = `${sla.sla_alerta}`; `TD_SlaAnalise` = `${sla.sla_analise}`. Mensagens: `msg.reembolso.docs_received`.
- Eventos: `agents.events.reembolso.completed`, `agents.events.reembolso.pended`, `agents.events.reembolso.received`, `agents.events.reembolso.sla_breached`. Call activities: —.
- Terminais (inclusive neutros/técnicos, não todos decisões de negócio): `End_ReembolsoProtocoloInvalido` (Solicitacao com protocolo/guia inconsistente na origem — validacao barrou (fail-safe, nao adverso)); `End_ReembolsoCancelado` (Cancelado por pendencia (decisao humana — nao adverso)); `End_ReembolsoAprovadoAutomatico` (Reembolso aprovado automaticamente (L2 — integral)); `End_RiscoSlaNotificado` (Risco de SLA notificado (analise segue)); `End_ReembolsoAprovadoAnalista` (Reembolso aprovado pelo analista/auditor); `End_ReembolsoNegado` (Reembolso negado (L0 — humano-gated)); `End_ReembolsoParcial` (Reembolso aprovado parcial (L0 — humano-gated, reducao adversa)).

### Convenção BPMNDI e limite da prova

Preservar progressão principal esquerda→direita, IDs/posições, tarefas 100×80, eventos 36×36 e gateways 50×50 do padrão AUTH. Rótulos de exceção podem ampliar seu espaço sem mudar nomes/semântica. A correção de INAD adiciona somente duas shapes e uma edge para o guard L0 existente: boundary anexado à borda superior de `ST_RegisterSuspension`, terminal neutro acima da linha de suspensão, conector ortogonal e labels explícitos. Não muda guard, allowlist, eventos, timer ou decisão.

`platform.validation.bpmn.validate_di` verifica cada plano de processo: cobertura de nodes/sequence flows, representação duplicada ou fora do plano, bounds finitos/positivos e waypoints finitos suficientes. Subprocesso colapsado oculta conteúdo; expandido exige cobertura dos filhos. Os cinco planos ANS-CRON são conferidos separadamente. XML sem DI continua permitido como snippet pelo parser; o teste de artefatos exige DI em cada arquivo do repositório. Collaboration planes, endpoint geométrico, crossings, sobreposição e legibilidade precisam de revisão de modeler; o gate não os certifica.

Mudança apenas visual exige comparação de XML executável normalizado excluindo somente namespaces de diagrama, sem descartar extensões executáveis, e renderização do diagrama alterado. Nenhum digest/DI/static PASS significa engine, canal ou jornada operacionalmente aceitos. Revisão visual integral dos demais arquivos e prova de runtime são gates posteriores; não há re-layout amplo neste pacote.
