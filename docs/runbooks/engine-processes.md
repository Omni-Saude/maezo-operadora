# Runbook: Engine Processes — BPMN/DMN deployment & instance management

**Audience:** Process engineers, compliance, on-call  
**Last updated:** 2026-09-03  
**Applies to:** Maezo Healthcare Plan Phase 0+

---

## Table of Contents

1. [Overview](#1-overview)
2. [BPMN/DMN deployment to CIB Seven](#2-bpmndmn-deployment-to-cib-seven)
3. [Business key conventions](#3-business-key-conventions)
4. [Human task completion](#4-human-task-completion)
5. [Finding & managing stuck instances](#5-finding--managing-stuck-instances)
6. [SLA timers & alerts](#6-sla-timers--alerts)

---

## 1. Overview

**Code:** `spec/processes/` (BPMN + DMN files)

Maezo uses CIB Seven (open-source Camunda) as the governance engine for compliance processes (ADR-0001). Processes enforce:

- **SLA deadlines** (RN259 — healthcare regulatory timeline)
- **Human-in-the-loop** (HITL) gates — mandatory human approval steps
- **Audit trails** — every process variable and task completion is logged

**Three processes in Phase 0/1:**

| Process | Key | Purpose | Status |
|---------|-----|---------|--------|
| SP-OP-ESCALATION-001 | Triage escalation to human navigator | Mandatory human review of red flags | Phase 0 (live) |
| SP-OP-AUTH-001 | Prior authorization end-to-end | Physician auditor reviews coverage request | Phase 1 |
| SP-OP-LGPD-DSR-001 | Data subject rights (LGPD Art. 18) | Member exercises right-to-access/delete | Phase 1 |

---

## 2. BPMN/DMN deployment to CIB Seven

**Code:** `spec/processes/bpmn/`, `spec/processes/dmn/`

### CI validation

All BPMN and DMN files are validated on PR:

```bash
make validate-artifacts
```

**Validation checks:**
- BPMN XML well-formedness (schema)
- All user tasks have `assignee` or `candidateGroups` defined
- All message events have matching catch events
- DMN tables have decision keys + input expressions
- No references to undefined process variables

### Manual deployment (dev)

Deployment goes through the `maezo-deploy` CLI (`maezo.platform.deploy.cli`), which submits **every** `.bpmn`/`.dmn` under `spec/processes/{bpmn,dmn}/` as one named deployment via `EngineDeployClient` (`POST /deployment/create`, multipart) — not a hand-rolled `curl` per file. It honors `ENGINE_REST_URL` (default `http://localhost:8080/engine-rest`) and is idempotent: the engine skips resources whose content is unchanged.

```bash
# 1. Start CIB Seven locally
make dev-stack          # or: docker compose --profile core up -d

# 2. Deploy every spec/processes/{bpmn,dmn} artifact (idempotent, versioned)
python -m maezo.platform.deploy          # equivalently: make deploy-artifacts

# 3. Verify — list the engine's deployments
python -m maezo.platform.deploy --list   # GET /deployment
```

### Automated deployment (staging/prod)

There is **no** `deploy.yml` workflow, and no CI job deploys processes with `curl`. `.github/workflows/cd.yml` builds/pushes the app image and rolls out the Helm chart (the runtime), but it does **not** push BPMN/DMN to an engine. Process deployment is done by running the same `maezo-deploy` CLI against the target engine, with `ENGINE_REST_URL` pointed at it:

```bash
# Against the target engine (staging/prod REST endpoint):
ENGINE_REST_URL="https://<engine-host>/engine-rest" python -m maezo.platform.deploy
```

`EngineDeployClient` runs the same `POST /deployment/create` path as the dev deploy, so the engine's own deployment parser is the final validator — a BPMN/DMN rejection is a real engine-side defect, not a tool bug.

**Note:** New deployments create new versions; old instances continue running with their deployed version. No migration needed.

---

## 3. Business key conventions

**Code:** `src/maezo/tools/mcp_cibseven/server.py` + agent code

Each process instance must have a **business key** — a globally unique identifier that enables idempotent process starts.

### Business key formulas

| Process | Key format | Example | Source |
|---------|-----------|---------|--------|
| SP-OP-ESCALATION-001 | `ESC-{tenant}-{conversation_id}` | `ESC-amh-conv-001` | Helena graph (deterministic from conversation) |
| SP-OP-AUTH-001 | `AUTH-{tenant}-{guia_number}` | `AUTH-amh-GUIA123456` | Authorization request doc |
| SP-OP-LGPD-DSR-001 | `DSR-{tenant}-{member_id}-{request_date}` | `DSR-amh-bnf-001-20260612` | Data subject request |

### Idempotence guarantee

```python
# Calling start_process twice with same business_key returns existing instance
result1 = await mcp.start_process(
    process_key="SP-OP-ESCALATION-001",
    business_key="ESC-amh-conv-001",
    variables={...}
)
result2 = await mcp.start_process(
    process_key="SP-OP-ESCALATION-001",
    business_key="ESC-amh-conv-001",
    variables={...}
)
assert result1.instance_id == result2.instance_id
assert result2.already_existed == True
```

---

## 4. Human task completion

**Code:** CIB Seven REST API; Tasklist UI

User Tasks (manual approval steps) are **not** completed by the agent. They are completed by humans via the Tasklist interface.

### Example: SP-OP-ESCALATION-001 escalation review

```
Helena initiates:
  POST /engine-rest/process-definition/key/SP-OP-ESCALATION-001/start
  {
    "businessKey": "ESC-amh-conv-001",
    "variables": {
      "beneficiario_pseudo_id": { "value": "bnf-..." },
      "motivo_categoria": { "value": "red_flag_clinico" },
      "descricao": { "value": "Suspeita de infarto..." }
    }
  }

Process starts → User Task "Revisar Escalonamento"
  Created: /tasklist/#/tasks/1234567

Human auditor:
  1. Opens Tasklist (browser)
  2. Views task details (beneficiary context, reason, Helena notes)
  3. Clicks "Approve" or "Reject"
  4. Process continues → responds to beneficiary
```

### Task assignment

User Tasks can be assigned to:

- **Individual user** (email)
- **Candidate group** (role-based; e.g., "auditor_team", "medical_reviewers")

Example from BPMN:

```xml
<userTask id="review_escalation" name="Revisar Escalonamento">
  <incoming>...</incoming>
  <outgoing>...</outgoing>
  <potentialOwner>
    <resourceAssignmentExpression>
      <formalExpression>${auditor_group}</formalExpression>
    </resourceAssignmentExpression>
  </potentialOwner>
</userTask>
```

Candidate groups are managed in CIB Seven's identity provider (LDAP, local, or Keycloak).

---

## 5. Finding & managing stuck instances

### List active instances

```bash
# All active SP-OP-ESCALATION instances for tenant
curl "http://cibseven:8080/engine-rest/process-instance?processDefinitionKey=SP-OP-ESCALATION-001&active=true" | jq '.'

# Filter by business key
curl "http://cibseven:8080/engine-rest/process-instance?businessKey=ESC-amh-conv-001" | jq '.[] | {id, businessKey, state}'
```

### Diagnose stuck instance

```bash
# Get instance details + active tasks
INSTANCE_ID="12345"
curl http://cibseven:8080/engine-rest/process-instance/$INSTANCE_ID | jq '.variables'

# Get activity history
curl http://cibseven:8080/engine-rest/process-instance/$INSTANCE_ID/activity-instance | jq '.'

# Get active tasks waiting for human
curl "http://cibseven:8080/engine-rest/task?processInstanceId=$INSTANCE_ID" | jq '.[] | {id, name, assignee, dueDate}'
```

**Common stuck states:**

| State | Cause | Fix |
|-------|-------|-----|
| Waiting on User Task for days | Task not assigned to anyone; assignee unreachable | Reassign task in Tasklist; notify team |
| Timer expired (red) | SLA deadline breached | Document in compliance log; continue process manually |
| External task unhandled | Agent tool failed; no fallback | Complete externally or retry via engine cockpit |
| Intermediate message never arrives | Agent disconnected; webhook never fired | Correlate message manually: `POST /engine-rest/message` |

### Manually advance process

**Via Tasklist:**
1. Open task in Tasklist UI
2. Review form fields
3. Click "Complete" with form data

**Via REST API (for automated recovery):**

```bash
# Complete a user task
TASK_ID="task-123"
curl -X POST http://cibseven:8080/engine-rest/task/$TASK_ID/complete \
  -H "Content-Type: application/json" \
  -d '{"variables": {"approval": {"value": true}}}'

# Correlate a message (trigger message event)
#
# ATENCAO — `documentacao_completa` NAO e' opcional nesta mensagem.
#
# Correlacionar sem ele reavalia `BRT_Admissibilidade` com o valor ANTIGO (false), a
# admissibilidade volta a PENDENTE_DOCUMENTACAO e o fluxo repete tudo: novo pedido de
# documento ao prestador, novo prazo, novo evento. A instancia volta ao MESMO ponto de
# espera e nada sinaliza que houve laco — medido 4x seguidas em 26/08/2026.
#
# O `docs_url` sozinho nao destrava: ele registra ONDE o documento esta'. Quem muda a
# decisao e' o FATO, e o fato e' `documentacao_completa`.
curl -X POST http://cibseven:8080/engine-rest/message \
  -H "Content-Type: application/json" \
  -d '{
    "messageName": "msg.auth.docs_received",
    "businessKey": "AUTH-amh-GUIA123456",
    "processVariables": {
      "documentacao_completa": {"value": true, "type": "Boolean"},
      "docs_url": {"value": "s3://..."}
    }
  }'
```

> A especificacao de teste (`docs/processes/test-specs/SP-OP-AUTH-001.md`, caso
> `test_pendencia_docs_recebidos_reavalia`) sempre disse isto corretamente — "correlacionada
> com `documentacao_completa=true`". Era este runbook que ensinava o comando incompleto, e
> quem copiava daqui causava o laco.

### Suspend or terminate instance

**Suspend** (pause; can resume):

```bash
curl -X PUT http://cibseven:8080/engine-rest/process-instance/$INSTANCE_ID/suspended \
  -H "Content-Type: application/json" \
  -d '{"suspended": true}'
```

**Terminate** (end; no resume):

```bash
curl -X DELETE http://cibseven:8080/engine-rest/process-instance/$INSTANCE_ID \
  -H "Content-Type: application/json" \
  -d '{"skipCustomListeners": false, "skipIoMappings": false}'
```

**Before terminating:** Save instance data to audit log; notify compliance if SLA-critical process.

### Chave travada: claim duravel sem instancia (`StartClaimWithoutInstanceError`)

**Codigo:** `src/maezo/tools/mcp_cibseven/transport.py` (`StartClaimWithoutInstanceError`,
`_resolve_strict_dedup_hit`, `_START_DEDUP_POLICY`) · **Decisao:** DL-0046 ·
**Prova executavel:** `tests/integration/chaos/test_crash_between_seams.py` (B1b).

**Quando isto aparece.** Só para as chaves de postura *gated* — hoje `SP-OP-PAGTO-001`
(`PERMANENT`) e `SP-OP-CANCEL-001` (`EXCLUSIVE`). Nessas duas, o *claim* durável escrito antes do
POST ao engine (ADR-0007, emit-before-effect) é também o **token de exclusão mútua** do start. Se
o claim existe e o engine **não tem instância nenhuma** para a chave — nem ativa, nem em história
— o portão não tem como decidir e **recusa, alto**: o processo NÃO é iniciado, o start devolve o
erro, e toda re-entrega cai no mesmo ponto até que um humano resolva. Nas chaves `NON_STRICT` (as
outras 13) este estado não existe: lá o claim deduplica apenas a linha de auditoria.

**Por que o sistema não resolve sozinho.** O mesmo estado é produzido por duas histórias que nada
durável distingue: (A) o POST nunca teve efeito (engine fora, 4xx, requisição perdida) — reiniciar
seria o certo; (B) um concorrente ganhou o claim e o POST dele **ainda está em voo** — reiniciar
criaria uma SEGUNDA instância: um segundo pagamento em `SP-OP-PAGTO-001`, uma segunda
`UT_AnaliseRescisao` concorrente em `SP-OP-CANCEL-001`. Chutar (A) é o defeito de efeito duplicado
vestido de recuperação; chutar (B) é o sucesso falso que este portão existe para matar. O erro
carrega o `dedup_key` exato justamente porque a decisão é humana.

**Sinais.** Log `cibseven_start_claim_without_instance` (nível `error`) com `process_key`,
`business_key`, `dedup_key` e `attempts`; e/ou `cibseven_start_claim_orphaned`, emitido no momento
em que o claim órfão nasce (start falhou logo após o claim), sem esperar a próxima re-entrega.

**Procedimento.**

1. **Confirme contra o engine** que realmente não há instância — ativa nem histórica — para a
   `business_key` do erro. Esta confirmação é a única precondição do passo 3:

```bash
BUSINESS_KEY="<business_key do erro>"
# Ativa:
curl "http://cibseven:8080/engine-rest/process-instance?businessKey=$BUSINESS_KEY" | jq '.'
# Historica (inclui COMPLETED/EXTERNALLY_TERMINATED):
curl "http://cibseven:8080/engine-rest/history/process-instance?processInstanceBusinessKey=$BUSINESS_KEY" | jq '.'
```

2. **Se houver instância** (ativa ou histórica): NÃO apague nada. Uma instância viva significa que
   o portão vai resolver sozinho na próxima re-entrega; uma instância encerrada resolve sozinha em
   `EXCLUSIVE` (a geração acabou, a próxima pode começar) e é recusa correta em `PERMANENT`
   (`ALREADY_COMPLETED`). Se mesmo assim o erro persiste, a causa provável é **retenção**: a
   história do engine expirou antes do claim. Trate como incidente de retenção (as duas janelas
   têm ordem obrigatória: retenção do claim ≤ retenção da história — ver o bloco de comentário de
   `_START_DEDUP_POLICY`), não como chave travada.

3. **Se não houver instância nenhuma**, escolha UMA das duas saídas e registre qual:
   - iniciar a instância manualmente no engine com a MESMA `business_key` (o claim já existente
     passa a ter a instância que ele reivindicou, e a próxima entrega resolve para ela); ou
   - apagar **aquela única linha** de `audit_emit_dedup` para rearmar o portão, deixando a próxima
     entrega iniciar o processo normalmente:

```sql
-- Uma linha, nomeada pelo dedup_key do proprio erro. Rode dentro do schema do tenant.
DELETE FROM audit_emit_dedup WHERE tenant = '<tenant>' AND dedup_key = '<dedup_key do erro>';
```

> **Isto NÃO é cirurgia na cadeia de auditoria.** `audit_emit_dedup` é tabela IRMÃ de
> `audit_chain` (migration 0005: `tenant, dedup_key, record_hash, created_at`), não carrega elo de
> hash, e apagar uma linha aqui não altera nada que `verify_chain` leia. A proibição absoluta de
> `UPDATE`/`DELETE`/`INSERT` manual de [`audit-recovery.md`](audit-recovery.md) §3 é sobre
> `audit_chain` e continua absoluta. Efeito colateral esperado e correto: a próxima entrega
> re-emite, então a cadeia **ganha um elo** (append-only) — ela nunca reescreve o primeiro.
> Apague `WHERE dedup_key = ...` — nunca a tabela inteira, nunca por faixa de data: cada outra
> linha é o token de exclusão de outra chave.

4. **Verifique** que a cadeia continua íntegra depois da operação (`verify_chain`, ver
   [`audit-recovery.md`](audit-recovery.md) §2) e que a chave iniciou exatamente uma vez.

O ciclo completo — travamento, passo 3 e rearme com exatamente UM start — é executado em
`tests/integration/chaos/test_crash_between_seams.py::test_b1b_operator_rearm_after_the_gated_wedge_starts_exactly_once`.

---

## 6. SLA timers & alerts

### SLA definition

**Code:** DMN outputs include `sla_deadline_hours`

SLAs are regulatory (RN259 healthcare timelines):

| Process | SLA | Source | Alert |
|---------|-----|--------|-------|
| SP-OP-ESCALATION-001 | 24 hours | Healthcare best practice | MaezoUserTaskSLABreach |
| SP-OP-AUTH-001 | 72 hours | RN259 (CONSU) | MaezoUserTaskSLABreach |
| SP-OP-LGPD-DSR-001 | 15 days | LGPD Art. 18 | MaezoUserTaskSLABreach |

### SLA monitoring

**Metric:** `maezo_process_sla_breach_total{process=SP-OP-ESCALATION-001}`

**Alert:** `MaezoUserTaskSLABreach` — any User Task overdue

**Response:**

1. **Immediate:** Page oncall-medical-auditor
2. **Notify compliance:** SLA breach is compliance event (log to regulatory file)
3. **Investigate:** Why was task not completed? Was human unreachable?
4. **Document:** Open a compliance incident ticket (per your org's incident tracker)
   with the resolution. There is no `COMPLIANCE_EVENTS.yaml` or equivalent file in
   this repository — no such artifact is checked in or read by any code path (a
   repo-wide search finds only unrelated analytics-compliance config at
   `config/analytics_compliance.yaml` / `src/maezo/platform/analytics/compliance.py`).
   Compliance-event logging today is a manual, external process; the fields below
   describe what such a ticket should capture, not a file format this repo produces.

**Example incident record (illustrative — captured in your ticketing system, not a repo file):**

```yaml
date: 2026-06-12T14:30:00Z
process: SP-OP-ESCALATION-001
business_key: ESC-amh-conv-001
event: SLA_BREACH (24h deadline)
root_cause: Auditor on leave; task not reassigned
resolution: Reassigned to backup auditor; completed 2h late
impact: Minor (2h delay, no clinical impact)
preventive_action: Auto-reassign to backup if unassigned after 8h
```

---

## Dashboard & monitoring

**Cockpit URL:** http://cibseven:8080/app/cockpit/ (dev) or AWS dashboards (prod)

**Key views:**
- **Dashboard:** Process instances by state (active, completed, failed)
- **Process instance list:** Filter by process key, business key, state
- **Task inbox:** All pending tasks across all processes
- **Variables:** Raw data stored in instance (for debugging)

**Metrics to export to Prometheus:**

```
maezo_process_instances_active{process_key, state}
maezo_process_completion_latency_hours{process_key}
maezo_process_sla_breach_total{process_key}
```
