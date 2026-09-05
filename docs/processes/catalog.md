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
pelo engine. Cada um devolvia (a) `evento_publicado: True`, fato fabricado que a allowlist de escrita
de escopo da harness deixava entrar na instancia, de um corpo cuja unica instrucao era `logger.info`
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
