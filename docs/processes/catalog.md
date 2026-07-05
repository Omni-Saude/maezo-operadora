# Catalogo de Processos SP-OP (compliance backbone)

Processo BPMN so existe se entregar: SLA regulatorio, HITL mandatorio,
auditoria de nao-repudio ou transacao multi-ator legal. Todo o resto e jornada
de agente (AGJ-*), documentada no `agent.yaml` de cada agente.

| ID | Nome | Gatilho regulatorio | Fase | Status |
|---|---|---|---|---|
| SP-OP-ESCALATION-001 | Escalonamento humano universal | Seguranca assistencial | 0 | modelado FINAL v1.0.0 (SLAs em review-queue) |
| SP-OP-AUTH-001 | Autorizacao previa | RN 259; negativa = medico auditor (L0 hard) | 1 | DRAFT — requires human review before any deploy |
| SP-OP-LGPD-DSR-001 | Direitos do titular | LGPD art. 18 (15 dias) | 1 | DRAFT — requires human review before any deploy |
| SP-OP-CONTAS-001 | Processamento contas/glosa | Contratos; auditabilidade | 2 | modelado (suite integracao real-engine) |
| SP-OP-RECURSO-001 | Recurso de glosa | Prazos contratuais | 2 | modelado (suite integracao real-engine) |
| SP-OP-NIP-001 | Resposta a NIP | Prazos ANS | 2 | modelado (suite integracao real-engine) |
| SP-OP-ANS-SUBMIT-001 | Envios periodicos ANS | Calendario regulatorio | 2 | modelado (suite integracao real-engine; DMN ans_sla adicionado ao contrato) |
| SP-OP-CANCEL-001 | Cancelamento de contrato | RN 412 | 2 | modelado (suite integracao real-engine) |
| SP-OP-REEMBOLSO-001 | Reembolso | RN 259 | 2 | modelado (suite integracao real-engine) |
| SP-OP-INADIMPLENCIA-001 | Suspensao/rescisao | RN 593 | 3 | modelado (suite integracao real-engine) |
| SP-OP-CRED-001 | (Des)credenciamento | RN 567 | 3 | modelado (suite integracao real-engine) |
| SP-OP-ADEQUACAO-001 | Adequacao de rede | RN 259 geografia | 3 | modelado (suite integracao real-engine) |
| SP-OP-FRAUDE-001 | Investigacao de fraude | Cadeia de custodia | 3 | modelado (suite integracao real-engine) |
| SP-OP-PROGRAMA-001 | Programas de cuidado | Consentimento LGPD | 3 | modelado (suite integracao real-engine) |
| SP-OP-PAGTO-001 | Pagamentos de alcada | Politica financeira | 3 | modelado (suite integracao real-engine) |

Quadrupla obrigatoria por processo: `.bpmn` + contrato (`docs/processes/contracts/`) +
DMNs derivadas (`src/maezo/processes/dmn/`) + test spec (`docs/processes/test-specs/`).
Jornadas de agente (sem BPMN): `docs/processes/journeys/` (Phase 0: AGJ-HELENA-TRIAGE).

> Validar prazos exatos das RNs com regulatorio antes de modelar timers.
Redesign completo: ver `operadora-process-redesign.md` no projeto de arquitetura.
