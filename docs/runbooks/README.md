# Runbooks index

Operational runbooks for Maezo Healthcare Plan. Audience: platform engineers, SRE, on-call,
compliance/audit, and — for the knowledge-concentration mitigation this index exists to
support — **every runbook below is written so that at least two maintainers can operate that
area independently**, per the hardening-program audit (PLANS.md §0.8).

**Language:** English. This matches the dominant language of the existing runbook corpus
(`gateway.md`, `devops-stack.md`, `engine-processes.md`, `fhir-sync.md`, `helena.md`,
`whatsapp-webhook.md`, `cd-rollback.md` are all English prose, even though several code
comments and `PLANS.md`/`docs/evidence-ledger.md` are Portuguese). New runbooks below follow
the established convention rather than introducing a second language into this directory.

## Runbooks

| Runbook | Area | Audience |
|---|---|---|
| [`gateway.md`](gateway.md) | PEP, pseudonymization, audit trail (in-process gateway library) | Security, compliance, platform |
| [`devops-stack.md`](devops-stack.md) | Local dev stack, Terraform, Helm, staging promotion, alerts | Platform, SRE, on-call |
| [`engine-processes.md`](engine-processes.md) | BPMN/DMN process instance management on CIB Seven (business keys, HITL tasks, SLA) — deployment half superseded, see [`dmn-bpmn-deployment.md`](dmn-bpmn-deployment.md) §5 | Process engineers, compliance, on-call |
| [`fhir-sync.md`](fhir-sync.md) | Tasy CDC integration & FHIR canonical store | Integration engineers, DBAs |
| [`helena.md`](helena.md) | Helena (Health Navigator agent) | Operations, medical audit |
| [`whatsapp-webhook.md`](whatsapp-webhook.md) | WhatsApp message ingestion & validation | Platform engineers, operations |
| [`cd-rollback.md`](cd-rollback.md) | Helm release rollback (bad CD deploy) | Platform, SRE, on-call |
| [`phase0-demo.md`](phase0-demo.md) | Phase 0 Definition-of-Done manual demo script | Product, QA |
| [`worker-runtime.md`](worker-runtime.md) | Worker daemon start/stop/health, `RUNTIME_MODE`, local engine bring-up, logs/metrics | Platform, SRE, on-call |
| [`dmn-bpmn-deployment.md`](dmn-bpmn-deployment.md) | Artifact validation (`make validate-artifacts`) & deployment (`make deploy-artifacts`) to CIB Seven, rollback posture, ratification constraints | Process engineers, compliance |
| [`audit-recovery.md`](audit-recovery.md) | Audit hash-chain design, integrity verification, recovery posture | Security, compliance, DBA |
| [`phi-inference-ops.md`](phi-inference-ops.md) | LLM provider selection, fail-closed PHI routing, credential handling | Platform, security, PHI operations |
| [`a2a-key-rotation.md`](a2a-key-rotation.md) | Agent Card signing key today, registry verification, what's pending | Security, platform |
| [`aws-ecs.md`](aws-ecs.md) | `maezo-operadora` on ECS/Fargate (AMH data account `amh-data-dev`, dev/staging) — the `deploy/aws-ecs/` path that superseded the never-provisioned EKS/Helm one | Platform, SRE, on-call |
| [`melhorar-com-o-uso.md`](melhorar-com-o-uso.md) | De conversa real a caso de teste: o comando, o rotulo que continua humano, e a cadencia de revisao das reguas como cerca de CI (Frente 8) | Plataforma, QA, dono clinico |
| [`metricas-do-canal.md`](metricas-do-canal.md) | O coletor de metricas em dev (ECS/Cloud Map -> workspace gerenciado), os quatro numeros do canal, e a rotina semanal de leitura humana das respostas da Helena (Frente 5) | Plataforma, SRE, dono clinico |

## Alert runbooks

One runbook per Prometheus alert in [`deploy/observability/alert-rules.yml`](../../deploy/observability/alert-rules.yml)
(GAP-D12-01-a) — every `runbook_url` annotation the owner adds to that file (a separate,
owner-gated change) has a real target here. Companion document:
[`docs/observability/SLO.md`](../observability/SLO.md) — SLI definitions, the metric→emitter
trace for every metric these alerts reference (several are DEAD or DO-NOT-EXIST metrics; read
that document before trusting an alert at face value), proposed SLOs, and the burn-rate
alerting shape that would replace these static thresholds.

| Alert | Rule (`alert-rules.yml:line`) | Runbook |
|---|---|---|
| `MaezoSLAWorkerLatencyHigh` | :18 | [`alerts/MaezoSLAWorkerLatencyHigh.md`](alerts/MaezoSLAWorkerLatencyHigh.md) |
| `MaezoSLAAgentErrorRateHigh` | :36 | [`alerts/MaezoSLAAgentErrorRateHigh.md`](alerts/MaezoSLAAgentErrorRateHigh.md) |
| `MaezoSLAWorkerErrorRateHigh` | :56 | [`alerts/MaezoSLAWorkerErrorRateHigh.md`](alerts/MaezoSLAWorkerErrorRateHigh.md) |
| `MaezoWorkerCrashLoop` | :81 | [`alerts/MaezoWorkerCrashLoop.md`](alerts/MaezoWorkerCrashLoop.md) |
| `MaezoAgentCrashLoop` | :98 | [`alerts/MaezoAgentCrashLoop.md`](alerts/MaezoAgentCrashLoop.md) |
| `MaezoDeadLetterBacklog` | :121 | [`alerts/MaezoDeadLetterBacklog.md`](alerts/MaezoDeadLetterBacklog.md) |
| `MaezoDeadLetterGrowth` | :138 | [`alerts/MaezoDeadLetterGrowth.md`](alerts/MaezoDeadLetterGrowth.md) |
| `MaezoLifecycleJobFailed` | :171 | [`alerts/MaezoLifecycleJobFailed.md`](alerts/MaezoLifecycleJobFailed.md) |

## Drills

Quarterly operational drills — see [`drills/README.md`](drills/README.md).

| Drill | Purpose |
|---|---|
| [`drills/recovery-drill.md`](drills/recovery-drill.md) | Restore audit DB from backup + verify chain integrity + verify against engine history |
| [`drills/key-rotation-drill.md`](drills/key-rotation-drill.md) | Rotate Agent Card signing keys end-to-end in a dev environment |

## Conventions

Each runbook states: **Audience**, **Last updated**, **Applies to**, a numbered Table of
Contents, and per-section `**Code:**` pointers to the source of truth. Commands are copied
verbatim from `Makefile` targets, `docker-compose.yml`, or module CLIs that exist on disk at
the time of writing — re-verify against the live tree before relying on an older runbook's
command, since code moves faster than docs (see each runbook's "Last updated").

Where a procedure depends on infrastructure or a human/ratification gate that does not exist
yet, the runbook says so explicitly in place, rather than describing it as if it works today.
