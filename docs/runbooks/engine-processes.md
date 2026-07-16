# Runbook: Engine Processes — BPMN/DMN deployment & instance management

**Audience:** Process engineers, compliance, on-call  
**Last updated:** 2026-06-12  
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

```bash
# 1. Start CIB Seven locally
docker compose --profile core up -d

# 2. Deploy BPMN via REST API
curl -X POST http://localhost:8080/engine-rest/deployment \
  -F "data=@spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn" \
  -F "deploymentSource=helena-escalation"

# 3. Verify deployment
curl http://localhost:8080/engine-rest/process-definition/SP-OP-ESCALATION-001 | jq .
```

### Automated deployment (staging/prod)

In GitHub Actions CI pipeline:

```yaml
# .github/workflows/deploy.yml
- name: Deploy processes to CIB Seven
  run: |
    for bpmn in spec/processes/bpmn/*.bpmn; do
      curl -X POST $CIB_SEVEN_URL/engine-rest/deployment \
        -F "data=@$bpmn" \
        -F "deploymentSource=ci"
    done
```

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
curl -X POST http://cibseven:8080/engine-rest/message \
  -H "Content-Type: application/json" \
  -d '{
    "messageName": "msg.auth.docs_received",
    "businessKey": "AUTH-amh-GUIA123456",
    "processVariables": {"docs_url": {"value": "s3://..."}}
  }'
```

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
