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
