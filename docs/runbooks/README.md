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
| [`portal-dev-provisionamento.md`](portal-dev-provisionamento.md) | O portal humano (ADR-0049) provisionado em dev com `portal_enabled=false`: client Cognito, role/grants do BFF, segredo da DSN, egresso exato, NLB/TLS, memberships de teste — e as tres pecas externas que faltam para ativar | Plataforma, SRE, dono do DNS |
| [`portal-egresso-cognito.md`](portal-egresso-cognito.md) | A Lambda que mantem os `/32` do Cognito (token + JWKS) no SG do portal a cada 5 min: o que faz, travas, logs/metricas/alarme, como desligar e o `apply -target` | Plataforma, SRE, on-call |
| [`renovar-material-staff-dev.md`](renovar-material-staff-dev.md) | A renovacao de 14 dias (N2) do material staff/human do portal nativo em dev: gerar no container, publicar os segredos, linhas, task definitions, destravar o emissor, verificar; o que vence e o sintoma quando vence (motor nao sobe) | Plataforma, SRE |
| [`supply-chain-imagem.md`](supply-chain-imagem.md) | SBOM + assinatura (cosign keyless/Sigstore) do digest da imagem: qual identidade assina, qual issuer, o comando exato de verificacao e o que o portao 1.4 de `portal.md` passa a significar | Plataforma, seguranca, dono do produto |
| [`melhorar-com-o-uso.md`](melhorar-com-o-uso.md) | De conversa real a caso de teste: o comando, o rotulo que continua humano, e a cadencia de revisao das reguas como cerca de CI (Frente 8) | Plataforma, QA, dono clinico |
| [`simulador-beneficiario.md`](simulador-beneficiario.md) | Simulador de beneficiario no dev: roster real no S3, task avulsa que compoe o receptor real com as bordas trocadas (captura, ESC interceptado), checagens D1..D12 + juiz, relatorio mascarado, limpeza | Plataforma, QA, dono do produto |
| [`acesso-bloqueio-por-cpf.md`](acesso-bloqueio-por-cpf.md) | Bloqueio de verificacao por CPF (3 falhas em 6 h, somadas entre conversas): o que conta, o que o beneficiario ve, como reconhecer no log, custo aceito, sem desbloqueio manual | Plataforma, dono do produto |
| [`lembrete-cobranca.md`](lembrete-cobranca.md) | Lembrete proativo de cobranca (job agendado): quem recebe, texto fixo, dry-run, allowlist em dev, modo de teste `--simular`, template da Meta fora da janela de 24 h, PARAR, como ligar e desligar | Plataforma, dono do produto |
| [`metricas-do-canal.md`](metricas-do-canal.md) | O coletor de metricas em dev (ECS/Cloud Map -> workspace gerenciado), os quatro numeros do canal, e a rotina semanal de leitura humana das respostas da Helena (Frente 5) | Plataforma, SRE, dono clinico |
| [`helena-triagem-v2.md`](helena-triagem-v2.md) | Nova triagem da Helena (experimento em dev, DL-0104): a chave `MAEZO_HELENA_TRIAGEM_V2`, como ligar/desligar no dev (Terraform + apply direcionado), como desfazer de vez, como verificar (logs, roteiro `triagem_v2`, D13) e as decisoes pendentes do catalogo | Plataforma, dono do produto, medico-auditor |
| [`helena-v4.md`](helena-v4.md) | Helena v4 (catálogo de necessidades v4, só em dev, DL-0115): a chave `MAEZO_HELENA_V4`, o que a pessoa recebe (especialista, carteirinha, sintoma leve só com perguntas de alerta), como ligar/desligar/desfazer (Terraform + apply direcionado), como verificar (logs, roteiro `helena_v4`, D13) e as decisões pendentes (prazo de 14 dias úteis a confirmar pelo jurídico) | Plataforma, dono do produto, jurídico, médico-auditor |
| [`helena-base-saude.md`](helena-base-saude.md) | Base de conhecimento de saúde da Helena (MODELO DE TESTE só em dev, ADR-0064/DL-0118): a chave `MAEZO_HELENA_BASE_SAUDE`, o que a pessoa recebe (informação geral da base sem caso, perguntas de alerta na dor sem alarme, encerramento do caso P2/P3 a pedido), recursos Terraform, ligar/desligar/desfazer, como adicionar um assunto, o verificador de fontes, o ciclo de revisão de 6 meses, a lista fechada de fontes e quem aprova | Plataforma, dono do produto, médico, jurídico |
| [`cancel-001-pf.md`](cancel-001-pf.md) | Cancelamento a pedido do beneficiario pessoa fisica (dev, DL-0111): a chave `MAEZO_CANCEL_PF_V1`, o que o Lucas e o SP-OP-CANCEL-001 v0.2.0 fazem, o passo a passo de AWS/portal para ligar (processos, apply direcionado, admissao das tarefas, grupo `cadastro-beneficiarios`, usuario de teste), desligar/desfazer, testar (roteiro `cancelamento_v1`) e o atalho de teste so' dev | Plataforma, dono do produto, juridico |
| [`lucas-v4.md`](lucas-v4.md) | Lucas do catálogo v4 (dev, DL-0114): a chave `MAEZO_LUCAS_V4`, só o pagador pede valores/2ª via (não pagador e empresa sem valor, sem nome), total primeiro e detalhe por beneficiário, código de barras como credencial (marcador no grafo, código só no último passo), recursos Terraform afetados, roteiros `lucas_v4`/`lucas_v4_pagador`/`lucas_v4_nao_pagador`, e o que falta (contratos billing-status 0.4.0 e billing-segunda-via publicados e pinados, executor do gateway) para ligar de verdade | Plataforma, dono do produto, DPO |

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
