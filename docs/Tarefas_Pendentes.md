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
| **Anexos episódicos S3 / Tasy Oracle / Avro** | **Reframe crítico.** O Helm aponta o deployment fhir-sync para `python -m maezo.platform.integrations.fhir_sync` (`deployment-fhir-sync.yaml`) — **esse módulo não existe**; `src/maezo/platform/integrations/` só tem `notifications_bridge.py` + `events_kafka_producer.py`. O footgun operacional (default `fhirSync.enabled: true`, que crash-loopava qualquer deploy real em `ModuleNotFoundError`) foi **corrigido em `main` (PR #166, `0ecacbb`)** — default agora `false` em `values.yaml` + 3 overlays, opt-in intacto. Construir o consumer real (S3/`boto3`, driver Oracle, Avro/Schema Registry) continua sendo trabalho de escopo grande, gated em contrato Tasy — **não é mais "só setar env vars"**. | #20/#25/#33 |
| **Token-metering / custo USD do LLM** | `_COST_PER_1K_TOKENS_USD` **não existe em lugar nenhum** do código, e a premissa de que "os tokens já são emitidos reais" é falsa: `AnthropicInferenceProvider.generate()` (`runtime/inference.py:242-278`) nunca lê `response.usage`. Antes de preencher qualquer tabela de preço é preciso **construir o próprio metering de tokens** — escopo maior que o item original sugeria. | #29 |
| **L2 ReviewQueue + sample_rate** | ✅ **RESOLVIDO por decisão arquitetural — removido do pending.** `ADR-0034` (Accepted, em `main` via PR #166) ratifica o descope: o `PEP.evaluate` do v2 tem **zero chamadores em runtime** (só usado como probe de `/readyz`; `gateway/pep.py:412`, confirmado por grep exaustivo) — não existe chokepoint por-tool-call para uma amostra L2 disparar. A garantia HITL é estrutural (BPMN no-denial 5 partes + DMN + tetos fail-closed + cadeia de auditoria, ADR-0018), não um PEP async com sampling como no donor v1. Pré-condição de revisita registrada na própria ADR (só reabre se um chokepoint PEP por-tool-call for introduzido). | #24 → ADR-0034 |
| **CronJob `verify-erasure`** | **Reframe crítico — a premissa "só alimentar o segredo" está errada.** Ver §3.2: o próprio entrypoint do CronJob recusa incondicionalmente (exit 78) e `ErasureManager` levanta `ErasureNotImplementedError` por design (T3.4-F3) — nada aqui liga com o conteúdo do segredo `ERASED_PATIENT_IDS`. Bloqueado em ratificação DPO da matriz de retenção, não em dado operacional. | #5 |
| **`PopulationFeatureClient` (André)** | Sem mudanças: `andre/graph.py:555` `population: PopulationFeatureClient \| None = None`, docstring confirma "PORT-PENDING (WB.4, mcp-datalake) — population=None is a supported configuration". | — |
| **Ingress + TLS** | Sem mudanças: `ingress.enabled=true` + `certificateArn` (`values.yaml:204`). | — |

**🔧 Novos itens agent-buildable-mas-decision-gated (não construídos, mas o código-alvo é pequeno assim que a decisão vier):**
- **Webhook-receiver sem `DATABASE_URL` (resíduo real pós-#167).** O deployment
  `deployment-webhook-receiver.yaml` já recebe `RUNTIME_MODE` e `PHI_HMAC_KEY` (fiados pelo #167),
  mas **ainda não recebe `DATABASE_URL`** — declara só
  `TENANT_ID`/`WHATSAPP_*`/`KAFKA_BOOTSTRAP_SERVERS`/`RUNTIME_MODE`/`PHI_HMAC_KEY`. O dispatcher da
  Helena (`platform/webhooks/service.py:_build_dispatcher`) **exige** `DATABASE_URL` — tanto para o
  sink de auditoria durável ADR-0007 (fence T-C2 antes de qualquer start de escalonamento) quanto
  para o checkpointer durável t4b; sem ele o build **levanta** (fail-closed), `_bring_up_dependencies`
  captura, `state.dispatcher` fica `None` e `/webhook` degrada para **501** — ou seja, com o template
  Helm atual o canal WhatsApp da Helena **não serve em produção**, e o checkpointer segue **dormente**.
  Fix já **enfileirado** (religar o deployment com `DATABASE_URL`, espelhando
  `deployment-agent-runtime.yaml`): PR mecânico pequeno, fora do escopo do #167.
- **DL-0033 (dossiês A2A Carolina/André) — ✅ RESOLVIDO pelo wiring real (DL-0037); resta só o
  merge da última borda.** Os 3 workers de dossiê sem função implementadora
  (`operadora.cred.prepare_dossier`, `operadora.pagto.prepare_approval_dossier`,
  `operadora.adequacao.prepare_remediation_dossier`) ganharam stubs locais neutros (espelhando
  `programa.enroll_beneficiario`, sem `DelegationDispatcher`) — **construídos, em `main` (PR #166;
  DL-0033 ACEITO)**. O texto anterior desta linha dizia que a integração A2A real até
  Carolina/André seguia "deliberadamente deferida": **não segue mais.** As bordas `cred` e
  `adequacao` foram religadas à delegação A2A REAL (envelope assinado via `DelegationDispatcher`)
  em **PR #178**, e a borda `pagto` — a última das três, delegação para André pelo `task_type`
  compartilhado `analytics.population` desambiguado por `envelope.origin` → fluxo `pagto_dossier`
  — na cadeia item-9 wave-3 (`d5e7571`), **ainda não em `main`**. Nenhum dos três é mais um stub
  local: os stubs permanecem apenas como o degradado-padrão quando não há dispatcher. Semântica
  em degradação (DL-0037, agora emendada para cobrir os três): fail-neutral-com-gap-disclosed —
  `{"dossier_prepared": false, "dossier_gap": <token>}`, log LOUD, external task COMPLETADA e a
  User Task humana SEMPRE abre, com ou sem dossiê. Pendência remanescente: só o merge da borda
  `pagto` em `main` (não confundir com o marco histórico M5 já concluído; o brief do orquestrador
  reusa o rótulo).
- **`ERR_ESC_NOTIFY_FAILED` (Tier-1, boundary de fallback de escalation).** Os workers
  `notify_team`/`notify_supervisor` (`tools/workers/escalation.py`) foram convertidos para handlers
  Kafka assíncronos reais — **em `main` (DL-0034 ACEITO, PR #166)** — a notificação agora
  **acontece de fato** (provado live). O que falta é o `raise` do `ERR_ESC_NOTIFY_FAILED` que
  dispara os boundaries de fallback `BE_FalhaNotificacao`/`BE_NotifFallbackFailed` quando o publish
  falha; os dois xfails correspondentes
  (`tests/integration/processes/test_sp_op_escalation_001.py:465` e `:487`) seguem `strict=True`.
  Está classificado como ADR-0030 Tier-1, co-agendado com o gap sistêmico de producer Kafka
  por-worker (não o `events.publish` genérico, já corrigido em T4).
- **DMN DUT×4 + `carencia_check`** — ver §1.6 e §2.1 (gated em sign-off médico-auditor, não em código).

### 1.5 🔴 Revisão de código pré-lançamento (lista `review-before-launch` — DESATUALIZADA, precisa de extensão)
A lista de 2026-06-14 (§5 do [relatório](reports/autonomous-completion-report.md)) só cobre #67–#86
(12 PRs). Pelo critério do próprio documento ("toca invariantes verificados § Seção 3, ou caminhos
CODEOWNERS"), **múltiplos PRs de #89–#166 também tocam invariantes e ainda não foram enumerados**
nessa lista formal — a lista está **incompleta**, não errada no que já lista. Exemplos que a equipe
deveria adicionar ao escopo de revisão pré-go-live (não exaustivo — enumerar a lista completa é
trabalho agent-buildable, a revisão em si é humana):
- **#156** (A2A W1–W4 completo: assinatura de card, dispatcher, enforcement fail-closed T-G/T-F).
- **#157** (bridge EB-3/EB-4: CONTAS→RECURSO→FRAUDE ao vivo).
- **#159** (T3.4 remediação: honestidade de erasure, A2A fail-closed, schema de checkpoint, PHI redaction backstop).
- **#165** (T4: persistência de checkpoint em prod, bridge armado + CONTAS→FRAUDE Phase-3, kafka producer, pytest 9).
- **#166** (T5: fix crítico de DSN de checkpoint, correções de workers ANS/nip/cred, DL-0033/0034, descope L2 — mergeado, `0ecacbb`).

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
o defeito de "falso sucesso" que T3.4-F3 fechou — **nada aqui é agent-buildable hoje**. Ação humana
necessária: DPO ratifica a matriz de retenção/bases-legais (ver `docs/review-queue.md` T2.9).

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
| 🔴 **Provedor LLM** (Zona Geral) + **construir token-metering antes de precificar** | `LLM_GENERAL_API_KEY` — custo USD por tier ainda não existe em código nenhum (ver §1.4) | #29 |
| 🔴 **Acesso Tasy/Oracle** (acordo de integração) — e o próprio consumer fhir-sync precisa ser construído (módulo não existe) | integração Tasy | #33 |
| 🟡 **WABA (WhatsApp Business)** | canal WhatsApp (Helena) | — |
| 🔴 **Billing AWS** (conta, limites, sa-east-1) | todo o `terraform apply` (EKS, Aurora, AMP/AMG) | #15/#32 |

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

_Última atualização: 2026-07-26 (reconciliação de ground-truth pós-T6; base `main`@`9871555`,
PRs #165, #166 e #167 mergeados). Fonte primária de evidência: `docs/evidence-ledger.md`,
`docs/decisions-log.md`, `docs/adr/`. Para o histórico #65–#88, ver
`docs/reports/autonomous-completion-report.md` §6._
