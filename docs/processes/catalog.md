# Catalogo de Processos SP-OP (compliance backbone)

Processo BPMN so existe se entregar: SLA regulatorio, HITL mandatorio,
auditoria de nao-repudio ou transacao multi-ator legal. Todo o resto e jornada
de agente (AGJ-*), documentada no `agent.yaml` de cada agente.

| ID | Nome | Gatilho regulatorio | Fase | Status |
|---|---|---|---|---|
| SP-OP-ESCALATION-001 | Escalonamento humano universal | Seguranca assistencial | 0 | modelado FINAL v1.0.0 (SLAs em review-queue) |
| SP-OP-AUTH-001 | Autorizacao previa | RN 259; negativa = medico auditor (L0 hard) | 1 | DRAFT — requires human review before any deploy |
| SP-OP-LGPD-DSR-001 | Direitos do titular | LGPD art. 18 (15 dias) | 1 | DRAFT — requires human review before any deploy |
| SP-OP-CONTAS-001 | Processamento contas/glosa | Contratos; auditabilidade | 2 | modelado (suite de integração planejada — T3.1) |
| SP-OP-RECURSO-001 | Recurso de glosa | Prazos contratuais | 2 | modelado (suite de integração planejada — T3.1) |
| SP-OP-NIP-001 | Resposta a NIP | Prazos ANS | 2 | modelado (suite de integração planejada — T3.1) |
| SP-OP-ANS-SUBMIT-001 | Envios periodicos ANS | Calendario regulatorio | 2 | modelado (suite de integração planejada — T3.1; DMN ans_sla adicionado ao contrato) |
| SP-OP-ANS-CRON-001 | Agendador dos envios ANS (5 definitions, 1 timer por report_type) | Calendario regulatorio (RN 124/209/388/424, DIOPS — DRAFT/verify) | 2 | modelado (agendador puro, NAO negativa-like; sem DMN propria e sem User Task propria — ver a nota sobre a quadrupla abaixo) |
| SP-OP-CANCEL-001 | Cancelamento de contrato | RN 412 | 2 | modelado (suite de integração planejada — T3.1) |
| SP-OP-REEMBOLSO-001 | Reembolso | RN 259 | 2 | modelado (suite de integração planejada — T3.1) |
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

> Validar prazos exatos das RNs com regulatorio antes de modelar timers.
Redesign completo: ver `operadora-process-redesign.md` no projeto de arquitetura.
