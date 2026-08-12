# Tarefas Pendentes — MAEZO Healthcare Plan

> **Contexto.** O build original (auditoria forense, 37 gaps, PRs #65–#88) fechou em 2026-06-14.
> Desde então uma segunda leva de reconstrução (v2 "Keep Brain, Rebuild Spine": tracks T0–T6, PRs
> #89–#167) fechou a maior parte do que faltava — runtime spine, cadeia de auditoria, A2A completo,
> chaos/cross-process, auditoria adversarial pré-deploy (T3.4) + remediação total, persistência de
> checkpoint, pytest 9. **PRs #165 (`t4`), #166 (`t5`) e #167 (`t6`) estão todos MERGEADOS em `main`
> (tip: `9871555`).** O #166 fechou os últimos achados adversariais da T5 — fix crítico de DSN no
> checkpoint (`normalize_dsn`), correções de workers ANS/nip/cred (o failsafe de âncora do NIP está
> **provado em engine real**: o strict-xfail de `test_sp_op_nip_001` foi removido com prova live em CI
> — finding-5 fechado end-to-end), DL-0033/DL-0034 construídos, descope do L2 ratificado via ADR-0034,
> footgun de deploy do fhir-sync corrigido. O #167 (T6) endureceu a pseudonimização PHI:
> `Pseudonymizer` agora usa **HMAC-SHA256 keyed** (era SHA-256 puro, reversível), fail-closed em prod
> (ADR-0035). Tudo com a mesma verificação zero-trust (R1/R2 independente) das demais linhas do ledger.
>
> Fonte de verdade viva, mais granular que este documento:
> [`docs/evidence-ledger.md`](evidence-ledger.md) (toda linha "verified" carrega reprodução
> independente) + [`docs/decisions-log.md`](decisions-log.md) (DLs) + [`docs/adr/`](adr/) (35 ADRs).
> O relatório histórico de 2026-06-14 segue válido só para o arco #65–#88:
> [`docs/reports/autonomous-completion-report.md`](reports/autonomous-completion-report.md).
>
> O que resta **majoritariamente não é trabalho de código** — são ações que **só humanos podem
> executar**: segredos reais, contratos comerciais, *apply* de infra AWS, designação de DPO +
> ratificação de 2 ADRs de retenção pendentes, sign-off clínico/jurídico/regulatório, e a revisão
> pré-lançamento. Um pequeno número de itens é **agent-buildable mas decision-gated** (sinalizado
> abaixo com 🔧) — o código ainda não existe porque falta uma decisão/ratificação, não credenciais.
>
> Convenção de severidade: 🔴 bloqueia go-live · 🟡 necessário antes de PHI real · 🟢 melhoria/operacional.
>
> **Atualização 2026-08-10 (reconciliação de ground-truth; base `main`@`091abd8`, ~50 PRs desde a
> última atualização deste doc em 2026-07-26 @ `9871555`).** Quatro frentes fecharam desde então:
> (1) um **programa de quatro tracks** (#197 NACK do ANS-SUBMIT, #198 portão de critérios de
> auto-aprovação AUTH, #199 preservação de fato em CRED, #200 preservação de fato em ADEQUACAO); (2)
> o **arco item-9 de flips de strict-xfail em engine real** (#177–#196, incl. as waves 3-7
> assembly-PR #185 e o fechamento Class-A de AUTH #196); (3) o **programa de compatibilidade AMH**
> (`ADR-0037` Accepted — DL-0040 — supersede PARCIAL do ADR-0013 e AMENDS o ADR-0034; work packages
> MZO-000/010/020/030/040/050a/050b/060; os três gates cross-repo **XRG-1/2/3 estão TODOS
> FECHADOS**; detalhe: PLANS.md §0.6); e (4) o **sprint 09-08-26 "dark-build offense"** — **19 PRs,
> #203–#221, todos mergeados** — que converteu praticamente todo portão humano *acelerável* (aquele
> que só precisa que alguém ratifique um DADO, não escreva código) num switch fail-closed: build
> mergeado, testado, **INERTE até ratificação**, ativação = mudança de dado, nunca de Python.
> Detalhe completo: PLANS.md §0.5.3 (portão de critérios de auto-aprovação + cauda item-9), §0.5.4
> (achados que exigem decisão humana), §0.6 (programa AMH-compat) e §0.7 (sprint dark-build).
>
> **Censo de strict-xfail hoje: exatamente 24**, nenhum defeito de engenharia — todos VALUE-GATED ou
> teto humano nomeado no próprio `reason=` do teste: 14 TISS-XSD/SME
> (`_NOTIFY_REGULATORIO_GAP_REASON`, `tests/integration/processes/test_sp_op_ans_submit_001.py`), 3
> DPO/LGPD-DSR (`test_sp_op_lgpd_dsr_001.py:488,517,546`), 2 NACK do ANS-SUBMIT
> (`test_sp_op_ans_submit_001.py:1426,1464`, `_SUBMIT_NACK_UNREACHABLE_REASON` — **em fechamento
> (build ao vivo nesta sessão; PR #226 aberto, cadeia completa autor R1 → GK R1 REVISE → repair → delta PASS com re-prova live)**), 2 T-E de guard-shape em CRED
> (`test_sp_op_cred_001.py:1005,1037`), 2 tetos D-07 financeiro (`test_sp_op_auth_001.py:697`,
> `test_sp_op_reembolso_001.py:627`), 1 RN 259 (`test_sp_op_adequacao_001.py:714`).

---

## Resumo por time

| Time | Itens | Bloqueia go-live? |
|---|---|---|
| **Desenvolvedores / DevOps / Plataforma** | Deploy, segredos, migrations, features *gated*, `terraform apply`, revisão de código pré-lançamento (agora estendida a #89–#166) | 🔴 sim |
| **Médicos / Médico-auditor** | Conteúdo clínico DRAFT (DMN DUT×4/ROL/carência), personas, atestação de segurança clínica | 🔴 sim |
| **Jurídico / Compliance / DPO** | DPA do endpoint BR, ratificação do processo de apagamento LGPD, ratificação de 2 ADRs de retenção de auditoria, atestação regulatória ANS | 🔴 sim |
| **Finanças / Procurement** | Contratos: endpoint LLM BR-resident, acesso Tasy/Oracle, provedor LLM + token-metering, billing AWS | 🔴 sim |
| **PO / Regulatório / Outros** | Datas de competência ANS, confirmação do mapa de personas, decisão de lançamento | 🟡 |

---

## 1. Desenvolvedores / DevOps / Plataforma

> O **runbook de deploy passo-a-passo** está no [README → Deploy](../README.md#deploy-produção--staging).
> Esta seção lista as ações **humanas/credenciais** que o deploy exige.

### 1.1 🔴 Popular os segredos reais no cofre (AWS Secrets Manager) — §6.2
O Terraform cria apenas as **"shells"** dos segredos (nomes/ARNs); os **valores** entram fora-de-banda
e **nunca** são commitados. O *External Secrets Operator* (ESO) sincroniza para o cluster via IRSA
(`deploy/helm/maezo-tenant/templates/externalsecret.yaml`). Caminho-base: `${secretPathPrefix}/${env}/...`.
Nenhum PR de #89–#166 tocou provisionamento de segredos reais — todo o trabalho dessa leva foi
camada de aplicação (auditoria, A2A, checkpoint, bridge, kafka producer).

| Segredo (Secrets Manager) | Propriedades | Gap / código que consome | Bloqueador |
|---|---|---|---|
| `rds!cluster-…` (RDS-gerido, **não** shell do TF) | `username`, `password` (o ESO **compõe** `database_url`) | Aurora `manage_master_user_password=true`; ESO compõe o DSN no sync (predeploy DB-4) | **grant IRSA** no ARN `rds!cluster-…` + preencher `aurora.*` do `terraform output` (ver nota ⬇) |
| `…/llm/api-keys` | `general_api_key`, `phi_api_key` | Zona Geral/PHI (`runtime/inference.py`) | contrato LLM |
| `…/tasy/oracle` | `dsn`, `user`, `password` | consumer fhir-sync — **módulo ainda não existe** (ver §1.4) | acordo Tasy + build do consumer |
| `…/whatsapp/waba-token` | `token` | webhook WhatsApp (`deployment-webhook-receiver.yaml`) | contrato WABA |
| `phi-hmac-key` (ExternalSecret `maezo-phi-hmac`) | `PHI_HMAC_KEY` | `Pseudonymizer.from_settings` → **HMAC-SHA256 keyed**, fail-closed em prod (chave ausente/blank/whitespace ⇒ `PseudonymizerKeyMissingError`); ver ⬇nota HMAC. **Código corrigido em `main` (PR #167, `9871555`; ADR-0035 Accepted)** — falta só **provisionar o valor real** do segredo no cofre. | provisionar segredo (§6.2) |
| chave de assinatura do Agent Card | `agent_card_signing_key` | `a2a/signing.py::CardSigner`; enforcement fail-closed em `runtime/agent_runtime/a2a_composition.py::_require_signer_or_fail_closed` (gap #9, agora **completo e fail-closed em produção** — T2.4/T3.4-F2) | — |

> **Aurora `database_url` — composto automaticamente pelo ESO (predeploy DB-4; corrige registro
> falso "gerado no apply").** Com `manage_master_user_password=true` (`aurora-postgres/main.tf`), a
> AWS/RDS **cria e roda** o segredo mestre em `rds!cluster-…` contendo apenas `{username,password}` —
> **não** existe propriedade `database_url` e o path fica **fora** de `${secretPathPrefix}`. O
> ExternalSecret `aurora-master-credentials` (`templates/externalsecret.yaml`) lê esse segredo **pelo
> ARN completo** e **compõe** `postgresql+asyncpg://user:senha@host:porta/db` no sync via
> `target.template` (rotation-safe: a cada rotação o ESO relê e recompõe). **Não há `put-secret-value`
> manual para o Aurora.** Pré-requisitos de deploy (§6.2 — exigem cluster, não verificáveis aqui):
> 1. Preencher `aurora.masterSecretArn` / `aurora.endpoint` / `aurora.port` / `aurora.database` em
>    `values-<env>.yaml` a partir do `terraform output` (`aurora_master_user_secret_arn`,
>    `aurora_writer_endpoint`, `aurora_port`, `aurora_database_name`). Ausência ⇒ `helm` **falha
>    fechado** (`required`).
> 2. Conceder à role IRSA que o ESO assume (`serviceAccount` → anotação
>    `eks.amazonaws.com/role-arn`) a ação `secretsmanager:GetSecretValue` no ARN `rds!cluster-…`.
>    Esse ARN está **fora** do grant `maezo/${env}/*` e a role é provisionada **fora deste repo**.
> 3. Confirmar no cluster que o ESO materializa a chave `database-url` (template `engine: v2`;
>    a senha é percent-encoded via `urlquery`).
> 4. **(DB-7) O ESO precisa estar rodando cluster-wide E o secret `database-url` já sincronizado
>    ANTES do hook `pre-install` de migrações.** O `ExternalSecret` é recurso *normal* (aplicado
>    DEPOIS dos hooks), então o **primeiro install** é **two-phase**: instalar com
>    `--set migrations.enabled=false`, `kubectl wait --for=condition=Ready
>    externalsecret/aurora-master-credentials`, e só então o release completo. O `cd.yml` e o
>    runbook §3 já fazem isso; um `helm install` manual precisa seguir a mesma ordem.
> 5. **(T4/T4b, em `main`) Essa mesma `database_url` agora também alimenta a persistência durável
>    de checkpoint langgraph** (`AsyncPostgresSaver`, prod fail-closed — ver Apêndice). O formato
>    real do DSN de produção (`postgresql+asyncpg://`, que o psycopg do checkpointer não parseia)
>    quebrava TODO boot de produção — **corrigido em `main` (PR #166, `0ecacbb`)** via
>    `normalize_dsn` no seam compartilhado com o audit-sink
>    (`runtime/checkpoint.py:151-153` → `gateway/audit_postgres.py:100`). Nenhuma ação humana aqui,
>    só verificar que o pod sobe com `checkpointer_ready=true` em `/readyz`.

Comando-modelo (substituir `<…>`):
```bash
aws secretsmanager put-secret-value \
  --secret-id "maezo/prod-amh/llm/api-keys" \
  --secret-string '{"general_api_key":"<…>","phi_api_key":"<…>"}' \
  --region sa-east-1
```
> ⚠️ **Nota HMAC (PHI-hardening — RESOLVIDO em `main`, PR #167).** A versão anterior deste doc citava
> `gateway/hmac_key_provider.VaultHmacKeyProvider` → `Pseudonymizer.from_vault` ("gap #12") — símbolos
> que **nunca existiram** no código fiado (só no commit greenfield `d4e6189`), e até `0ecacbb` o
> `Pseudonymizer` real fazia **SHA-256 puro SEM chave** (reversível por força-bruta — o espaço de CPF
> é pequeno — logo pseudonimização LGPD fraca), com `PHI_HMAC_KEY` como setting **morta**. **Corrigido
> em `main` (PR #167, `9871555`; ADR-0035 Accepted):** `Pseudonymizer.from_settings`
> (`gateway/pseudonymizer.py`) agora deriva pseudônimos por **HMAC-SHA256 keyed** e é **fail-closed em
> produção** — chave ausente **ou blank/whitespace-only** ⇒ `PseudonymizerKeyMissingError` (nunca cai
> para um pseudônimo determinístico/reversível; dev/CI usa uma chave-derivada não-secreta, também HMAC,
> nunca SHA-256 puro). Resíduo humano: **provisionar o valor real** do `PHI_HMAC_KEY` no cofre
> (ExternalSecret `maezo-phi-hmac`, §6.2) — só então a pseudonimização passa a ser irreversível em prod.
> Sem um provedor de inferência PHI real, a Zona PHI **falha fechada** (ver §1.4 — o mecanismo não é
> mais `BR_INFERENCE_ENDPOINT`).

### 1.2 🔴 Provisionar a infra AWS (`terraform apply`) — §6.2 (issue #16)
Hoje a CD é **no-op** quando `AWS_ENABLED != true`. Sem mudanças desde 2026-06-14 (nenhum módulo
`kms`/`acm`/`pca` existe ainda em `deploy/terraform/modules/`; confirma DL-0035). Para subir:
1. `cd deploy/terraform/envs/<staging-sa-east-1|prod-amh-sa-east-1>`
2. `cp terraform.tfvars.example terraform.tfvars` e preencher (`aws_account_id`, `owner_email`,
   `cost_center`, `create_eks_cluster` — `true` = cluster dedicado por ADR-0004).
3. `terraform init && terraform plan && terraform apply`.
   Provisiona: **EKS** (dedicado opcional), **Aurora PG16** (provisioned + *deletion-protected* em prod),
   **ECR** (tags imutáveis em prod), **GitHub OIDC**, **KMS + Secrets Manager** (shells),
   **observability** (workspace **AMP** + **AMG** + datasource — gap #32).
4. Após criar o cluster, preencher `eks_node_security_group_ids` + `eks_node_role_arns` no tfvars e
   re-`apply` (NetworkPolicies/IRSA).
5. `terraform output` → anotar ARNs (ECR, Aurora endpoint, IRSA role, AMP workspace, KMS key).
> Pré-requisito externo: a **VPC do amh-data-platform** e o **MSK Serverless** (CDC Tasy/Debezium já em
> produção lá — *consumimos, não duplicamos*, ADR-0013/DL-0002). O segredo
> `debezium/msk-bootstrap-servers` precisa existir (data-source do Terraform).

### 1.3 ✅ Migrations automáticas (Helm hook) — RESOLVIDO; resta garantir o pré-requisito
Sem mudanças desde 2026-06-14. O chart **já** roda as migrations automaticamente via **Helm hook
`pre-install,pre-upgrade`** (`deploy/helm/maezo-tenant/templates/job-migrations.yaml`, toggle
`migrations.enabled`): executa `alembic upgrade head` no schema do tenant ANTES dos runtimes subirem
(cria `audit_chain`, `agent_memory`, `a2a_idempotency`, índice de retenção + **pgvector em `public`**
— DL-0017; migração 0006 desde então também **derrubou** as tabelas mortas `agent_checkpoints`/
`agent_checkpoint_writes`, T3.4/F4). O Job deriva a URL síncrona do secret Aurora (`+asyncpg` →
psycopg) e usa `MAEZO_TENANT`; o Dockerfile inclui `alembic.ini`. **Ações humanas restantes:**
- garantir que o **secret Aurora (ESO-synced) exista ANTES do `helm upgrade`** (já é pré-requisito do
  app — §1.1); o `backoffLimit` cobre atrasos transitórios do ESO;
- garantir que o papel do DB tenha privilégio para `CREATE EXTENSION vector` (ex.: `rds_superuser`);
- execução manual de debug, se preciso: `ALEMBIC_DATABASE_URL=postgresql://… MAEZO_TENANT=amh python -m alembic upgrade head`.

### 1.4 🟡/🟢/🔧 Ativar features *gated* quando os insumos chegarem
Esta seção mudou substancialmente desde 2026-06-14: vários mecanismos nomeados na versão anterior
**não existem mais** (foram substituídos por reescritas T1.7–T1.10), um item foi **resolvido por
decisão arquitetural** (ADR-0034), e o cluster fhir-sync está **pior** do que a versão anterior
implicava (o consumer nunca foi escrito).

| Feature | Status real hoje | Gap |
|---|---|---|
| **Endpoint PHI BR-resident** | Mecanismo **não é mais** `BR_INFERENCE_ENDPOINT`/`config/inference_routing.yaml` (esse arquivo/env não existem no v2). Hoje: `MAEZO_INFERENCE_PROVIDER` seleciona o provider (`runtime/inference.py:118`, default `"noop"`); chamadas com `phi_capable=True` só funcionam com `PhiZoneMockProvider` (`:295`/`:311`) ou levantam `PhiZoneRoutingError` (`:88`) — **continua fail-closed em substância**, só a citação de env/arquivo estava obsoleta. Falta o **contrato DPA** (§3.1) para existir um provider real. | #2 |
| **CIB Seven/HAPI in-cluster** | Sem mudanças, confirmado ainda preciso: `--set cibSeven.inCluster.enabled=true` + `fhir.inCluster.enabled=true` (`values.yaml:434`/`:467`, default `false` → usa URL externa; manifests StatefulSet reais e não-triviais, ADR-0021). | #14 |
| **Anexos episódicos S3 / Tasy Oracle / Avro** | **Reframe crítico.** O Helm aponta o deployment fhir-sync para `python -m maezo.platform.integrations.fhir_sync` (`deployment-fhir-sync.yaml`) — **esse módulo não existe**; `src/maezo/platform/integrations/` só tem `notifications_bridge.py`, `events_kafka_producer.py` e `amh_inbox.py` (adicionado pelo PR #213, MZO-060) — nenhum deles é o consumer fhir-sync. O footgun operacional (default `fhirSync.enabled: true`, que crash-loopava qualquer deploy real em `ModuleNotFoundError`) foi **corrigido em `main` (PR #166, `0ecacbb`)** — default agora `false` em `values.yaml` + 3 overlays, opt-in intacto. Construir o consumer real (S3/`boto3`, driver Oracle, Avro/Schema Registry) continua sendo trabalho de escopo grande, gated em contrato Tasy — **não é mais "só setar env vars"**. | #20/#25/#33 |
| **Token-metering / custo USD do LLM** | **Metade de captura ✅ RESOLVIDA (T8, PR #171) — reframe do texto anterior, que já estava obsoleto quando escrito.** `_emit_llm_token_usage()` (`runtime/inference.py:579-662`, chamada de `AnthropicInferenceProvider.generate()` em `:724-778`) lê `response.usage.input_tokens`/`.output_tokens` de toda resposta Anthropic real e emite (a) o evento structlog `llm_token_usage` (`provider`, `model`, `input_tokens`, `output_tokens`, `total_tokens`, `agent_id`, `tenant_id` — PHI-safe por construção, nunca lê `response.content`) e (b) o contador Prometheus `maezo_llm_tokens_total` via `record_llm_token_usage()` (`platform/observability.py:322`); providers mock/noop deliberadamente não emitem nada. `_COST_PER_1K_TOKENS_USD` **continua não existindo em lugar nenhum** — a metade que falta é só a **humana**: finanças define preço/tiers antes de existir qualquer tabela USD (nenhum código adicional necessário para a captura). Um branch de hardening (fence de campo obrigatório + prova de não-metering para mocks) está aberto como **PR #222** ("Cerca de PHI no metering de tokens LLM: conjunto de campos do evento `llm_token_usage` pinado + prova de zero-emissão dos providers mock"), ainda não mergeado. | #29 |
| **L2 ReviewQueue + sample_rate** | ✅ **RESOLVIDO por decisão arquitetural — removido do pending.** `ADR-0034` (Accepted, em `main` via PR #166) ratifica o descope: o `PEP.evaluate` do v2 tem **zero chamadores em runtime** (só usado como probe de `/readyz`; `gateway/pep.py:412`, confirmado por grep exaustivo) — não existe chokepoint por-tool-call para uma amostra L2 disparar. A garantia HITL é estrutural (BPMN no-denial 5 partes + DMN + tetos fail-closed + cadeia de auditoria, ADR-0018), não um PEP async com sampling como no donor v1. Pré-condição de revisita registrada na própria ADR (só reabre se um chokepoint PEP por-tool-call for introduzido). | #24 → ADR-0034 |
| **CronJob `verify-erasure`** | **Reframe crítico — a premissa "só alimentar o segredo" está errada.** Ver §3.2: o próprio entrypoint do CronJob recusa incondicionalmente (exit 78) e `ErasureManager` levanta `ErasureNotImplementedError` por design (T3.4-F3) — nada aqui liga com o conteúdo do segredo `ERASED_PATIENT_IDS`. Bloqueado em ratificação DPO da matriz de retenção, não em dado operacional. | #5 |
| **`PopulationFeatureClient` (André)** | Sem mudanças: `andre/graph.py:555` `population: PopulationFeatureClient \| None = None`, docstring confirma "PORT-PENDING (WB.4, mcp-datalake) — population=None is a supported configuration". | — |
| **Ingress + TLS** | Sem mudanças: `ingress.enabled=true` + `certificateArn` (`values.yaml:204`). | — |

**✅ Itens que eram "agent-buildable-mas-decision-gated" e RESOLVERAM desde 2026-07-26:**
- **Webhook-receiver sem `DATABASE_URL` — ✅ RESOLVIDO.** O deployment
  `deployment-webhook-receiver.yaml:66-70` agora injeta `DATABASE_URL` a partir do MESMO
  ExternalSecret Aurora que `deployment-agent-runtime.yaml`/`deployment-worker-daemon.yaml` leem
  (`.Values.aurora.secretName` / chave `database-url`, mirror-exact, comentário inline documenta o
  fail-closed: ausente ⇒ `_build_dispatcher` levanta ⇒ dispatcher `None` ⇒ `/webhook` 501). O
  dispatcher da Helena (`platform/webhooks/service.py:_build_dispatcher`, `:88-102`) continua
  **exigindo** `DATABASE_URL` (`ValueError` explícito se ausente) — mas agora o template Helm
  entrega o valor. O canal WhatsApp da Helena e o checkpointer durável t4b deixam de estar
  dormentes assim que o chart for aplicado. Nenhuma ação humana restante aqui além de aplicar o
  chart (§6.2).
- **DL-0033 (dossiês A2A Carolina/André) — ✅ RESOLVIDO por completo; as TRÊS bordas estão
  religadas.** Os 3 workers de dossiê sem função implementadora
  (`operadora.cred.prepare_dossier`, `operadora.pagto.prepare_approval_dossier`,
  `operadora.adequacao.prepare_remediation_dossier`) foram religados à delegação A2A REAL (envelope
  assinado via `DelegationDispatcher`). As bordas `cred` e `adequacao` fecharam no PR #178; a
  última — `pagto`, delegação para André pelo `task_type` compartilhado `analytics.population`,
  desambiguado por `envelope.origin` — fechou na wave item-9 (`make_prepare_approval_dossier_handler`,
  `tools/workers/pagto.py:792`; documentado como terceira origem em
  `runtime/agent_runtime/a2a_composition.py:82-93`; `agents/andre/delegation.py:104` fixa
  `_DEFAULT_FLOW: Flow = "pagto_dossier"`, `:484-492 _flow_for` roteia QUALQUER origem que não seja
  `adequacao-worker` para esse default). Nenhum dos três é mais um stub local: os stubs permanecem
  apenas como o degradado-padrão quando não há dispatcher (fail-neutral-com-gap-disclosed, DL-0037
  — `{"dossier_prepared": false, "dossier_gap": <token>}`, log LOUD, User Task humana SEMPRE abre,
  com ou sem dossiê). Sem pendência remanescente de código.
- **`ERR_ESC_NOTIFY_FAILED` (Tier-1, boundary de fallback de escalation) — ✅ RESOLVIDO.** Os
  workers `notify_team`/`notify_supervisor` (`tools/workers/escalation.py`) já eram handlers Kafka
  assíncronos reais (PR #166); o `raise WorkerBpmnError(ERR_ESC_NOTIFY_FAILED)` que dispara os
  boundaries de fallback `BE_FalhaNotificacao`/`BE_NotifFallbackFailed` numa falha de publish agora
  está construído (`escalation.py:206-209` e `:295-298`). Os dois strict-xfails correspondentes
  foram **removidos** (a suíte documenta a remoção inline, `test_sp_op_escalation_001.py:102`) e os
  dois testes agora afirmam o caminho de fallback AO VIVO. Limpeza correlata: PR #220 removeu
  marcadores de conflito git pré-existentes que tinham sobrevivido na docstring de
  `test_falha_notificacao_usa_fallback` (achado factual — a descrição concorrente em `HEAD` estava
  incorreta sobre o uso de `FakeKafkaPublisher` naquele arquivo).
- **DMN DUT×4 + `carencia_check`** — ver §1.6 e §2.1 (gated em sign-off médico-auditor, não em
  código).

**🔧 Novos switches "dark-build" do sprint 09-08-26 (PLANS.md §0.7) — código mergeado, testado,
INERTE; ativação = ato humano de DADO, nunca de Python:**
- **MZO-040 `ActionExecutionGateway` (programa AMH-compat) — aprovações Médica + ANS + Security.**
  `src/maezo/gateway/action_execution.py` (loader fail-closed + `evaluate` total) está ligado em
  **SOMBRA** no chokepoint por-chamada `WorkerHarness._handle` (PR #211); o registro de aprovações
  é DADO em `spec/policies/autonomy/action-approvals.yaml` (10 classes × 3 domínios = 30 blocos,
  **todos `aprovado: false`/`PENDENTE`**); pacote de evidência em
  `docs/reviews/mzo-040-approval-packet.md`. Nada foi aprovado e nada é bloqueado (a inércia é
  provada por teste: caminho executado com e sem o gateway, observáveis idênticos). Ratificar é
  preencher os blocos + `status: RATIFICADO` + `modo: enforcing`, sem mudança de código — ato dos
  aprovadores **Médica** (§2), **ANS/regulatório** (§3.4/§5) e **Security** (§1, resíduos na §6 do
  pacote). Detalhe: PLANS.md §0.6.
- **MZO-060 inbox durável AMH — DBA revisa, não autora.** Migração `0007_amh_inbox` (idempotência
  por `(tenant, idempotency_key/event_id)`, lifecycle RECEIVED→PROCESSED→SETTLED, provada
  up→down→up em PostgreSQL 16 real, zero PHI) + repositório fail-closed que recusa operar sem
  artefato ratificado (`spec/policies/amh/inbox-ratification.yaml`) e re-deriva o digest da
  migração do disco a cada construção (PR #213). Pacote DBA:
  `docs/reviews/mzo-060-dba-review-packet.md` — DDL verbatim, plano de índices, análise de
  locks/contention, rollback executável, decisões abertas D-1..D-6. Ratificar: DBA computa o
  `sha256` da migração no tree mergeado, preenche `migration_sha256` + `dba_review: APPROVED` +
  `ratificado: true` e decide D-1..D-6 — sem mudança de código.
- **TISS-XSD — pin de schema ratificável (SME troca pelo XSD real).** Seam separado e inerte
  (`src/maezo/tools/workers/tiss_schema_pin.py` + `spec/policies/ans/tiss-schema-pin.yaml` +
  `spec/policies/ans/synthetic-tiss-v1.xsd`, PR #218) construído e provado inteiramente contra um
  XSD **SINTÉTICO** rotulado como tal — o validador `lxml` já-shipado T2.6-2
  (`tools/workers/tiss_schema.py`) continua sendo o que `ans_submit.py` chama hoje, sem mudança.
  Ratificar: SME substitui o XSD sintético pelo Padrão-TISS ANS real publicado + confirma a versão
  exata no manifesto — sem mudança de código no validador.
- **DMN shadow candidates M-4..M-7 + RN 259 — digest binding (#221).** RN 259
  (`adequacao_gap.dmn`, PR #204, MERGEADO) e as 4 tabelas irmãs da mesma classe de inversão de
  regra — `glosa_triage` (M-4), `carencia_check` (M-5), `upcoding_complexity_ceiling` (M-6),
  `triage_redflag_gestante`/`triage_redflag_pediatric` (M-7, 2 tabelas) — têm cada uma um MANIFESTO
  candidato `.yaml` ao lado da tabela viva (DADO, nunca artefato deployável; PR #217). Desde o
  PR #221, cada manifesto carrega `tabela_viva: {path, sha256}` — uma ratificação **nunca sobrevive
  em silêncio** a uma edição da tabela que revisou: se o `sha256` gravado deixar de bater com os
  bytes da tabela viva no disco, o manifesto degrada para `ratificado: false` mesmo que o campo
  literal ainda diga `true`. Nenhum dos 6 manifestos está ratificado hoje. Detalhe: §2.1 e
  `docs/review-queue.md` (linha da RN 259 + seção W4).
- **D-07 — tetos de auto-aprovação AUTH/REEMBOLSO (financeiro).** Ver §4.

### 1.5 🔴 Revisão de código pré-lançamento (lista `review-before-launch` — agora ENUMERADA por completo)
**Correção de framing: a versão anterior deste item dizia "exemplos, não exaustivo" — isso não é
mais verdade.** A enumeração completa foi feita (critério do relatório de 2026-06-14, "toca
invariantes verificados § Seção 3, ou caminhos CODEOWNERS") e vive em
[`docs/reports/review-before-launch-extension.md`](reports/review-before-launch-extension.md):
**63 dos 122 PRs mergeados no intervalo #89–#221** tocam invariante/CODEOWNERS e entram no escopo
formal de revisão pré-go-live; **11 números de PR do intervalo nunca foram mergeados** (fechados
sem merge: #145, #150, #152, #154, #161, #163, #164, #175, #182, #183, #184); os 59 PRs restantes
são docs-only/test-only/dependências. Método: `gh pr list`/`gh pr view --json files` por PR,
classificado contra as superfícies CODEOWNERS (`src/maezo/policies/`, `spec/policies/`,
`spec/processes/dmn/`, `docs/adr/`, allowlists de processo, `config/artifact_signoff.yaml`) mais as
categorias review-relevant de gateway/platform/workers/BPMN — detalhe completo do critério no
arquivo linkado. 5 linhas sorteadas da tabela foram re-verificadas nesta reconciliação via
`gh pr view N --json files` (#93, #132, #148, #171, #209): título e arquivos batem 5/5.

Lista original (#65–#88), ainda válida: #67 (egress), #68 (TLS prod), #69 (allowlist), #72 (PHI/LGPD
+ cadeia de auditoria), #73 (credencial), #76 (assinatura de card), #77 (BR-region), #78
(allowlist-prod), #80 (L2 — **nota: a substância desse item foi resolvida por descope, ver §1.4**), #82
(isolamento de tenant), #84 (proveniência de auditoria), #86 (anexos PHI).

### 1.6 🟢 Itens menores de hardening (opcional)
- ~~Asserção de que `dmn_refs`/`evidence` são `str` no `decision_basis` estruturado~~ — **MOOT,
  removido.** Essa forma nunca existiu no `decision_basis` atual; o mecanismo real (T1.10) é mais
  estrito: `build_decision_basis()` (`tools/workers/harness.py:224`) usa um allowlist explícito
  (`_SAFE_DECISION_BASIS_KEYS`, `:156`) gated por `_is_bounded_token()` (`:213-221`) — regex-bound por
  valor, não um `isinstance(str)` solto. `AuditRecord.dmn_versions` é intencionalmente um dict
  estruturado (`DmnVersion.to_audit_dict()`), não `str` — a asserção antiga estaria errada se
  aplicada hoje. Nada a construir.
- **DMN órfãs — parcialmente resolvido.** As 7 tabelas `fraude_scoring/*`
  (frequency_zscore_threshold, phantom_no_diagnosis, phantom_suspicious_prefix,
  provider_peer_deviation, risk_thresholds, unbundling_partial_bundles,
  upcoding_complexity_ceiling) **já têm consumidor real** desde T2.7 fase 2:
  `tools/workers/fraude.py:344` `score_indicators()` (registro `:994`) as avalia via
  `_evaluate_scoring_chain`. **DONE — remover da lista.** Restam órfãs **por design**, aguardando
  dono de negócio médico: `dut_rol_coverage`, `dut_criteria_bariatrica`,
  `dut_criteria_oncologia_pet_ct`, `dut_criteria_terapias_especiais` (4) + `carencia_check` (1) — a
  aplicação continua mecânica, mas a **colocação** exige sign-off médico-auditor (mesmo item de
  §2.1). `spec/processes/dmn/orphans-allowlist.yaml` é hoje a lista oficial validada por
  `make validate-artifacts` (verde, 0 erros/0 notices) — nenhuma órfã não-catalogada existe.

---

## 2. Médicos / Médico-auditor / Clínico

> O runtime **garante por arquitetura** que nenhuma negativa/decisão clínica sai sem médico humano
> (ADR-0005/0018, invariante *no-adverse* CI-enforced). O que falta é **conteúdo clínico** e **sign-off**.

### 2.1 🔴 Revisar e aprovar o conteúdo DMN clínico (DRAFT) — mecanismo de gate mudou, substância igual
O mecanismo de sign-off foi **reconstruído** desde 2026-06-14: `config/artifact_signoff.yaml` **não
existe mais** no repo (era um manifesto único que sempre passava). O gate real hoje é
`src/maezo/platform/validation/signoff.py` (fail-closed de verdade), chaveado por arquivos
`docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` — **zero desses arquivos existem
ainda**, exceto a lista de exceção "avó" `retro-verification-pending.yaml`. `make validate-signoff`
(`Makefile:44`) agora **realmente** gate-ia (chama `python -m maezo.platform.validation.cli signoff`),
em vez do stub antigo que sempre passava.

As tabelas DMN de cobertura (DUT/ROL/carência, critérios de adequação, *red-flags* de fraude)
continuam marcadas **DRAFT/sintéticas**. `docs/sme-dispatch/tracker.md` cataloga 16 contratos (15
DRAFT + 1 FINAL — `SP-OP-ESCALATION-001` — que por sua vez **não tem arquivo de signoff**, sinalizado
para retro-verificação). Status de todos os 16: **"preparado — aguardando roster" (bloqueio
externo)** — o roster de SMEs humanos (médico-auditor etc.) ainda não foi nomeado pelo dono do
produto, então nada foi de fato enviado para revisão ainda. Um médico-auditor deve revisar as regras
(critérios, *thresholds*, hit-policies) e promover de `intentional_draft` → assinado.
Fila de revisão: `docs/review-queue.md`.

**Novo (2026-08-10) — candidatos shadow para 6 tabelas DMN conhecidas-erradas, com binding de
digest.** RN 259 (`adequacao_gap.dmn`, PR #204, MERGEADO — inversão de ordem `hitPolicy=FIRST`
entre `r_eletivo_leve`/`r_conforme`) e as 4 tabelas irmãs `glosa_triage` (M-4), `carencia_check`
(M-5), `upcoding_complexity_ceiling` (M-6), `triage_redflag_gestante`/`triage_redflag_pediatric`
(M-7, 2 tabelas) têm cada uma um manifesto candidato `.yaml` DADO (nunca artefato deployável, o
glob de deploy/validação só pega `*.dmn`/`*.bpmn`) ao lado da tabela viva (PR #217). Desde o
PR #221 cada manifesto carrega `tabela_viva: {path, sha256}`, re-derivado do disco a cada carga: se
a tabela viva for editada depois de uma ratificação, o `sha256` deixa de bater e o manifesto
degrada para `ratificado: false` mesmo com o campo literal ainda `true` — uma ratificação nunca
sobrevive em silêncio à edição da tabela que revisou. Nenhum dos 6 manifestos está ratificado hoje;
detalhe por tabela (achados, base regulatória citada, revisor esperado) em `docs/review-queue.md`
(linha da RN 259 + seção "W4 — shadow candidates").

### 2.2 🔴 Validar as personas dos agentes
Sem mudanças de substância. Confirmar o mapa de personas (esp. **Beatriz↔Valentina** — fraude↔cuidado,
ainda marcado DRAFT em `docs/reports/phase3-plan.md §4` — nota: o path mudou de
`docs/phase3-plan.md` para `docs/reports/phase3-plan.md`) e os *prompts* clínicos de
Carolina/Fernando/Valentina/Beatriz/André.

### 2.3 🔴 Atestação de segurança clínica (pré-lançamento)
Sem mudanças. Atestar que os fluxos de autorização/recurso/negativa preservam a decisão humana e que
os dossiês (evidências citadas, score, refs DMN) são clinicamente suficientes para o médico decidir.

---

## 3. Jurídico / Compliance / DPO

### 3.1 🔴 DPA do endpoint de inferência PHI BR-resident (gap #2)
Contratar/assinar o **Data Processing Agreement** com o provedor do endpoint de inferência
**residente no Brasil** (LGPD/residência de dados, sa-east-1). Até lá a inferência PHI fica fechada
— não mais via `PhiEndpointNotConfigured`/`BR_INFERENCE_ENDPOINT` (mecanismo antigo, não existe mais),
e sim via `PhiZoneRoutingError` quando nenhum provider PHI-capable real está configurado
(`runtime/inference.py:88`, seleção por `MAEZO_INFERENCE_PROVIDER`). Substância do bloqueio
inalterada: sem contrato, sem provider real.

### 3.2 🔴 Verificação/ratificação do processo de apagamento LGPD (gap #5) — reframe crítico
**A premissa da versão anterior deste documento estava errada e precisa ser corrigida, não só
atualizada.** Não é "definir o processo que alimenta `ERASED_PATIENT_IDS` para o CronJob
`verify-erasure` provar zero PHI residual" — hoje **não há nada para o CronJob verificar**:
- `ErasureManager.erase()`/`.verify()` (`src/maezo/platform/erasure.py`) levantam
  incondicionalmente `ErasureNotImplementedError` — corrigido para ser **honesto** em T3.4-F3 (era
  um falso `status="completed"` sem executar SQL nenhum). Os helpers por-camada são código morto.
- O entrypoint do CronJob (`src/maezo/platform/lifecycle/__init__.py::main()`) recusa **qualquer**
  subcomando, incluindo `verify-erasure`, incondicionalmente (`exit 78`) — desde T2.8 (2026-07-17),
  já antes da última atualização deste doc.
- `ExecuteErasureWorker` (`tools/workers/lgpd.py`), no caminho aprovado, levanta
  `WorkerFailureError(retries_left=0)` — nunca retorna "erasure_completed".

**Populate o segredo `ERASED_PATIENT_IDS` não muda nada disso.** O bloqueio real é: (a) a exclusão
SQL por camada depende da **ratificação DPO da matriz de bases-legais/retenção**
(`erasure.py` docstring); (b) o mapeamento `titular_pseudo_id → fhir_patient_id` segue não resolvido
(`lgpd.py:380-382`). Construir a cascata de DELETE antes dessa ratificação reintroduziria exatamente
o defeito de "falso sucesso" que T3.4-F3 fechou.

**Atualização 2026-08-10 (PR #216, ADR-0029 dark build) — "nada aqui é agent-buildable" fica
PARCIALMENTE SUPERADO; reframe honesto, não celebração.** O **esqueleto por camada** agora existe,
testado, e recusa executar até o DPO ratificar valores: as **15 relações** da cadeia de persistência
(migrações 0001-0007) foram enumeradas, cada uma com identificação do titular, resolução
(erase/anonymize/retain) e ordem — **toda `decisao_dpo`/`base_legal`/`retencao` é placeholder
`PENDENTE` detectável por máquina**, e o loader do plano recusa operar sem `status: RATIFICADO` +
`ratificado: true` + `revisor` + `data` + `dpo_review: APPROVED`. O dry-run é **INERTE por
construção** (só `SELECT count(*)`, provado por guarda AST de verbo+forma que sobrevive a
`python -O`) e a referência do titular alcança um bind, nunca repr/report/SQL/erro/log. Pacote de
revisão por camada: `docs/reviews/adr-0029-erasure-packet.md`. **O build também revelou dois achados
que são MAIORES que o build e continuam sem solução de engenharia possível hoje:** (1)
**erasure por-titular é estruturalmente inalcançável pelo mecanismo atual da ADR-0029** — o
preimage do `record_hash` inclui `decision_basis`, então anonimizar um titular ≡ deletar para a
cadeia de auditoria, e a ADR-0029 só admite poda de PREFIXO genesis-anchored, nunca as linhas
esparsas de um titular específico; (2) **só 2 colunas identificam um titular em toda a cadeia**
(ambas `fhir_patient_id`), **zero FKs** existem em 0001-0007, e **nenhuma ponte de identidade
real** existe — o dry-run reporta `NOT_COUNTED_IDENTITY_BRIDGE_ABSENT`, nunca `0`. **O gate
continua sendo a ratificação DPO** (ver `docs/review-queue.md` T2.9) — mas o DPO agora ratifica
contra um plano concreto por camada em vez de contra uma descrição de projeto, e a ratificação por
si só NÃO resolve os dois achados estruturais acima (esses exigem decisão de arquitetura adicional,
também humana).

### 3.3 🟡 Política de retenção de auditoria (5 anos) — incompleto, precisa de 2 ratificações
A parte de design segue válida: `audit_chain` **não-particionada** com anti-fork DB-atômico
(`UNIQUE(prev_record_hash)`, DL-0018). **O que a versão anterior deste doc omitia:** o CronJob
`audit-retention` **também** é uma recusa incondicional fail-closed (`AUDIT_RETENTION_REFUSAL`,
`lifecycle/__init__.py`, desde T2.8/2026-07-17) — não é só "confirmar 5 anos como prazo", é que o
`DELETE` em si **não pode rodar hoje de forma nenhuma**, por design, até que:
1. Um **legal-hold registry** seja ratificado — draft existente em
   `docs/compliance/ADR-0020-amendment-draft.md` (Status: **Proposed — requires DPO + orchestrator
   ratification**), que documenta a contradição hoje real: `RetentionManager.retention_query()`
   (`retention.py:132-143`) emite um `DELETE` incondicional sem predicado de legal-hold, contradizendo
   ADR-0020 item 6 ("hold vence" durante litígio) — e não existe registro de legal-hold algum no
   código (`grep legal_hold src/` = zero hits);
2. O mecanismo de **re-anchor de checkpoint assinado** seja ratificado
   (`docs/adr/0029-audit-chain-pruning-reanchor.md`, Status: **Proposed**, "até existir, a deleção
   não deve ser habilitada de forma alguma" — apagar o prefixo genesis-anchored hoje quebraria
   `verify_chain()` para os sobreviventes).

`RetentionManager.retention_query()` (`src/maezo/platform/retention.py`) constrói o DELETE cru mas
tem **zero chamadores em produção** — travado por design até as 2 ratificações acima. Confirmar 5
anos como prazo regulatório correto (`docs/compliance/ripd-kickoff.md`, ainda DRAFT) continua sendo
ação humana separada, mas não é a única pendência aqui.

### 3.4 🔴 Atestação regulatória / ANS
Sem mudanças. Sign-off jurídico/regulatório dos artefatos de política e da submissão ANS (a decisão
de lançamento é humana, ADR-0018/§6.2).

---

## 4. Finanças / Procurement

| Contrato a fechar | Habilita | Gap |
|---|---|---|
| 🔴 **Endpoint LLM PHI BR-resident** + DPA | inferência da Zona PHI (`MAEZO_INFERENCE_PROVIDER`) | #2 |
| 🔴 **Provedor LLM** (Zona Geral) + **definir preço/tiers por token** | `LLM_GENERAL_API_KEY` — a **captura** de tokens já está construída (T8, PR #171: `_emit_llm_token_usage` lê `response.usage` real, ver §1.4); só falta a tabela USD de preço (`_COST_PER_1K_TOKENS_USD`, decisão de negócio, não código) | #29 |
| 🔴 **Acesso Tasy/Oracle** (acordo de integração) — e o próprio consumer fhir-sync precisa ser construído (módulo não existe) | integração Tasy | #33 |
| 🟡 **WABA (WhatsApp Business)** | canal WhatsApp (Helena) | — |
| 🔴 **Billing AWS** (conta, limites, sa-east-1) | todo o `terraform apply` (EKS, Aurora, AMP/AMG) | #15/#32 |
| 🔴 **D-07 — tetos de auto-aprovação AUTH/REEMBOLSO** | `authorization_approval.max_value_brl`/`reembolso_auto_approval.max_value_brl` em `spec/policies/autonomy/{L0-core,tenants-amh}.yaml` estão em `0` (fail-closed: nenhuma auto-aprovação automática ocorre até a diretoria definir o teto real); `CeilingResolver.within_l2_ceiling` é consultado no canal automático de AUTH (`auth.py`) e REEMBOLSO antes da emissão — 2 strict-xfails vivos (`test_sp_op_auth_001.py:697`, `test_sp_op_reembolso_001.py:627`) FLIPAM quando o teto for decidido; canal humano nunca é afetado pelo teto | diretoria AMH + finanças |

---

## 5. PO / Regulatório / Outros

- 🟡 **Datas de competência ANS** reais (Track C6) — substituir placeholders (`COMPETENCIA_PENDENTE`
  ainda presente em `tools/workers/ans_cron.py`/`notification_bridge.py` — confirmado inalterado).
- 🟡 **Confirmar o mapa de personas** com o PO (Beatriz↔Valentina) antes de fixar (evita retrabalho).
- 🟡 **Decisão de lançamento** (go/no-go) após as revisões clínica/jurídica/regulatória acima.

---

## Apêndice — Variáveis de ambiente (referência rápida)

`.env.example` é a fonte. Principais para produção:

| Var | Significado | Fonte em prod |
|---|---|---|
| `DATABASE_URL` | Aurora (estado/memória/auditoria **e, desde T4/T4b, persistência durável de checkpoint langgraph** via `AsyncPostgresSaver` — agent-runtime E webhook da Helena/WhatsApp) | ExternalSecret `aurora/master-user-secret`. Fail-closed em prod: `AGENT_RUNTIME_MODE != local` + DSN ausente/`setup()` falhando ⇒ `checkpointer_ready=false`, sem fallback silencioso em memória. O DSN `postgresql+asyncpg://` do ESO, que o psycopg não parseava (quebrava todo boot de prod), está **corrigido** (`normalize_dsn`, PR #166, `0ecacbb`). **Gap remanescente:** o deployment `webhook-receiver` ainda **não recebe `DATABASE_URL`** (ver §1.4) — sem ele o dispatcher da Helena não constrói (fail-closed) e `/webhook` degrada para 501; fix enfileirado. |
| `AGENT_RUNTIME_MODE` | Seleciona modo prod vs local (gate fail-closed de checkpoint, A2A card-signing, etc.) | `production` nos deployments reais |
| `MAEZO_INFERENCE_PROVIDER` | Seleciona o provider de inferência (Geral/PHI); substitui o antigo `BR_INFERENCE_ENDPOINT` | contrato + ExternalSecret (§3.1) |
| `CIBSEVEN_BASE_URL` / `FHIR_BASE_URL` | engine + FHIR | URL externa ou StatefulSet in-cluster (gated) |
| `KAFKA_BOOTSTRAP_SERVERS` | eventos/CDC/auditoria/bridge (agora inclui o producer leg real do bridge, T4) | MSK (amh-data-platform) |
| `LLM_GENERAL_API_KEY` / `LLM_PHI_API_KEY` | LLM Zona Geral/PHI | ExternalSecret `llm/api-keys` |
| `PHI_HMAC_KEY` | chave do pseudonymizer HMAC-SHA256 keyed (`Pseudonymizer.from_settings`, fail-closed em prod; ADR-0035) — **código efetivo em `main` (PR #167, `9871555`)**; falta só provisionar o valor real do segredo no cofre (§6) | ExternalSecret `maezo-phi-hmac` (§6.2) |
| `TASY_ORACLE_DSN` / `TASY_ORACLE_USER` | integração Tasy (consumer ainda não existe, ver §1.4/§4) | ExternalSecret `tasy/oracle` (§6.2) |
| `EPISODIC_ATTACHMENTS_BUCKET` / `_REGION` | anexos episódicos S3 (consumer ainda não existe) | bucket + IRSA (§6.2) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | export de traços | collector → AMP/AMG (TLS+SigV4 em prod) |
| `WHATSAPP_TOKEN` / `_PHONE_NUMBER_ID` / `_WEBHOOK_VERIFY_TOKEN` | canal WhatsApp | ExternalSecret `whatsapp/waba-token` (§6.2) |
| `AWS_ENABLED` | habilita CD/`terraform apply` reais | `true` em prod/staging |

---

_Última atualização: 2026-08-10 (reconciliação de ground-truth pós sprint-09-08-26; base
`main`@`091abd8`, ~50 PRs mergeados desde a atualização anterior de 2026-07-26 @ `9871555`).
Método: cada claim de código/status foi re-verificado nesta sessão contra o repo (leitura direta
dos arquivos citados, `gh pr view --json files/mergeCommit` para título/SHA/squash de PR, contagem
de strict-xfail via `grep -rn "strict=True" tests/`) — não copiado de sessões anteriores sem
re-checagem. Fonte primária de evidência: `docs/evidence-ledger.md`, `docs/decisions-log.md`,
`docs/adr/`, `PLANS.md` §0.5.3/§0.5.4/§0.6/§0.7. Para o histórico #65–#88, ver
`docs/reports/autonomous-completion-report.md` §6; para a enumeração formal de revisão pré-launch
#89–#221, ver `docs/reports/review-before-launch-extension.md`._
