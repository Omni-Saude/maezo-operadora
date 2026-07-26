# Tarefas Pendentes — MAEZO Healthcare Plan

> **Contexto.** O build de software está **completo**: todos os 37 gaps da auditoria forense
> (`docs/audits/forensic-adr-audit.md`) foram resolvidos e mergeados (PRs #65–#88), `main` verde.
> Relatório completo: [`docs/reports/autonomous-completion-report.md`](reports/autonomous-completion-report.md).
>
> O que resta **não é trabalho de código** — são ações que **só humanos podem executar** (§6.2 do
> prompt de conclusão autônoma): segredos reais, contratos comerciais, *apply* de infra AWS,
> sign-off clínico/jurídico/regulatório, e a revisão pré-lançamento. **Para cada item, o código já
> está fiado até a "borda automatizável"** — basta a entrada do mundo real para ativar.
>
> Convenção de severidade: 🔴 bloqueia go-live · 🟡 necessário antes de PHI real · 🟢 melhoria/operacional.

---

## Resumo por time

| Time | Itens | Bloqueia go-live? |
|---|---|---|
| **Desenvolvedores / DevOps / Plataforma** | Deploy, segredos, migrations, features *gated*, `terraform apply`, revisão de código pré-lançamento | 🔴 sim |
| **Médicos / Médico-auditor** | Conteúdo clínico DRAFT (DMN DUT/ROL/carência), personas, atestação de segurança clínica | 🔴 sim |
| **Jurídico / Compliance / DPO** | DPA do endpoint BR, verificação LGPD, retenção de auditoria, atestação regulatória ANS | 🔴 sim |
| **Finanças / Procurement** | Contratos: endpoint LLM BR-resident, acesso Tasy/Oracle, provedor LLM + pricing, billing AWS | 🔴 sim |
| **PO / Regulatório / Outros** | Datas de competência ANS, confirmação do mapa de personas, decisão de lançamento | 🟡 |

---

## 1. Desenvolvedores / DevOps / Plataforma

> O **runbook de deploy passo-a-passo** está no [README → Deploy](../README.md#deploy-produção--staging).
> Esta seção lista as ações **humanas/credenciais** que o deploy exige.

### 1.1 🔴 Popular os segredos reais no cofre (AWS Secrets Manager) — §6.2
O Terraform cria apenas as **"shells"** dos segredos (nomes/ARNs); os **valores** entram fora-de-banda
e **nunca** são commitados. O *External Secrets Operator* (ESO) sincroniza para o cluster via IRSA
(`deploy/helm/maezo-tenant/templates/externalsecret.yaml`). Caminho-base: `${secretPathPrefix}/${env}/...`.

| Segredo (Secrets Manager) | Propriedades | Gap / código que consome | Bloqueador |
|---|---|---|---|
| `rds!cluster-…` (RDS-gerido, **não** shell do TF) | `username`, `password` (o ESO **compõe** `database_url`) | Aurora `manage_master_user_password=true`; ESO compõe o DSN no sync (predeploy DB-4) | **grant IRSA** no ARN `rds!cluster-…` + preencher `aurora.*` do `terraform output` (ver nota ⬇) |
| `…/llm/api-keys` | `general_api_key`, `phi_api_key` | Zona Geral/PHI (`inference.py`) | contrato LLM |
| `…/tasy/oracle` | `dsn`, `user`, `password` | fhir-sync (`TASY_ORACLE_DSN`, gap #33) | acordo Tasy |
| `…/whatsapp/waba-token` | `token` | webhook WhatsApp | contrato WABA |
| chave HMAC do pseudonymizer | `phi_hmac_key` | `gateway/hmac_key_provider.VaultHmacKeyProvider` → `Pseudonymizer.from_vault` (gap #12) | — |
| chave de assinatura do Agent Card | `agent_card_signing_key` | `a2a/registry.py` sign/verify (gap #9) | — |

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

Comando-modelo (substituir `<…>`):
```bash
aws secretsmanager put-secret-value \
  --secret-id "maezo/prod-amh/llm/api-keys" \
  --secret-string '{"general_api_key":"<…>","phi_api_key":"<…>"}' \
  --region sa-east-1
```
> ⚠️ Sem a chave HMAC, `Pseudonymizer.from_vault` **levanta erro** (fail-closed — nunca usa default).
> Sem `BR_INFERENCE_ENDPOINT`, a inferência da Zona PHI **falha fechada** (não vaza para endpoint US).

### 1.2 🔴 Provisionar a infra AWS (`terraform apply`) — §6.2 (issue #16)
Hoje a CD é **no-op** quando `AWS_ENABLED != true`. Para subir:
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
O chart **já** roda as migrations automaticamente via **Helm hook `pre-install,pre-upgrade`**
(`deploy/helm/maezo-tenant/templates/job-migrations.yaml`, toggle `migrations.enabled`): executa
`alembic upgrade head` no schema do tenant ANTES dos runtimes subirem (cria `audit_chain`,
`agent_memory`, `a2a_idempotency`, índice de retenção + **pgvector em `public`** — DL-0017). O Job
deriva a URL síncrona do secret Aurora (`+asyncpg` → psycopg) e usa `MAEZO_TENANT`; o Dockerfile
agora inclui `alembic.ini`. **Ações humanas restantes:**
- garantir que o **secret Aurora (ESO-synced) exista ANTES do `helm upgrade`** (já é pré-requisito do
  app — §1.1); o `backoffLimit` cobre atrasos transitórios do ESO;
- garantir que o papel do DB tenha privilégio para `CREATE EXTENSION vector` (ex.: `rds_superuser`);
- execução manual de debug, se preciso: `ALEMBIC_DATABASE_URL=postgresql://… MAEZO_TENANT=amh python -m alembic upgrade head`.

### 1.4 🟡/🟢 Ativar features *gated* quando os insumos chegarem
Cada uma tem o código pronto; falta só o insumo/flag:

| Feature | Como ativar | Gap |
|---|---|---|
| **Endpoint PHI BR-resident** | setar `BR_INFERENCE_ENDPOINT` em `config/inference_routing.yaml`/env (após contrato) | #2 |
| **CIB Seven/HAPI in-cluster** | `--set cibSeven.statefulSet.enabled=true` + `hapiFhir.statefulSet.enabled=true` (default `false` → usa URL externa) | #14 |
| **Anexos episódicos S3** | criar bucket + IAM/IRSA; **adicionar `boto3>=1.34,<2.0` a `pyproject.toml`**; setar `EPISODIC_ATTACHMENTS_BUCKET`/`_REGION` | #20 |
| **Tasy Oracle real** | setar `TASY_ORACLE_DSN`/`TASY_ORACLE_USER`; adicionar driver `cx_Oracle`/`oracledb` | #33 |
| **Avro/Schema Registry** | habilitar o seam env-gated do consumer fhir-sync | #25 |
| **Tabela de custo USD do LLM** | preencher `inference.py::_COST_PER_1K_TOKENS_USD` (tokens já são emitidos reais) | #29 |
| **L2 ReviewQueue + sample_rate** | **Intencionalmente fora do v2 — ADR-0034 (descope ratificado).** ~~Instrução anterior (STALE): construir `L2ReviewSampler` + `ReviewQueue` e injetar em `PolicyEnforcementPoint(...)`.~~ Não construir: o `PEP.evaluate` do v2 não tem chamadores em runtime (`build_pep()` é só readiness probe), a autonomia é enforce-ada estruturalmente via BPMN (ADR-0018), e um sampler injetado amostraria zero ações. Revisitar só se o v2 introduzir um chokepoint PEP por-tool-call em runtime (ver ADR-0034). | #24 → ADR-0034 |
| **CronJob `verify-erasure`** | criar/alimentar o segredo `ERASED_PATIENT_IDS` (lista de pacientes apagados a auditar) | #5 |
| **`PopulationFeatureClient` (André)** | injetar cliente concreto do lago (port WB.4/ADR-0019) em `make_andre_handler` (hoje `population=None`, degrada) | — |
| **Ingress + TLS** | `ingress.enabled=true` + `certificateArn` | — |

### 1.5 🔴 Revisão de código pré-lançamento (12 PRs `review-before-launch`)
Todos **já mergeados** (não bloqueiam main), mas tocam invariantes verificados (auditoria Seção 3)
ou caminhos CODEOWNERS — a equipe revisa antes do go-live. Lista completa com "o que checar" na
§5 do [relatório](reports/autonomous-completion-report.md). Resumo: #67 (egress), #68 (TLS prod),
#69 (allowlist), #72 (PHI/LGPD + cadeia de auditoria), #73 (credencial), #76 (assinatura de card),
#77 (BR-region), #78 (allowlist-prod), #80 (L2), #82 (isolamento de tenant), #84 (proveniência de
auditoria), #86 (anexos PHI).

### 1.6 🟢 Itens menores de hardening (opcional)
- Asserção de que `dmn_refs`/`evidence` são `str` no `decision_basis` estruturado (nota da revisão #84).
- Atribuir consumidor (com o domínio) às tabelas DMN órfãs restantes — a aplicação é mecânica, a
  **colocação** precisa de dono de negócio (gap #22).

---

## 2. Médicos / Médico-auditor / Clínico

> O runtime **garante por arquitetura** que nenhuma negativa/decisão clínica sai sem médico humano
> (ADR-0005/0018, invariante *no-adverse* CI-enforced). O que falta é **conteúdo clínico** e **sign-off**.

### 2.1 🔴 Revisar e aprovar o conteúdo DMN clínico (DRAFT)
As tabelas DMN de cobertura (DUT/ROL/carência, critérios de adequação, *red-flags* de fraude) estão
marcadas **DRAFT/sintéticas** (`config/artifact_signoff.yaml`). Um médico-auditor deve revisar as
regras (critérios, *thresholds*, hit-policies) e promover de `intentional_draft` → assinado.
Fila de revisão: `docs/review-queue.md`. Gate de promoção: `make validate-signoff`.

### 2.2 🔴 Validar as personas dos agentes
Confirmar o mapa de personas (esp. **Beatriz↔Valentina** — fraude↔cuidado, marcado DRAFT no
`phase3-plan.md §4`) e os *prompts* clínicos de Carolina/Fernando/Valentina/Beatriz/André.

### 2.3 🔴 Atestação de segurança clínica (pré-lançamento)
Atestar que os fluxos de autorização/recurso/negativa preservam a decisão humana e que os dossiês
(evidências citadas, score, refs DMN) são clinicamente suficientes para o médico decidir.

---

## 3. Jurídico / Compliance / DPO

### 3.1 🔴 DPA do endpoint de inferência PHI BR-resident (gap #2)
Contratar/assinar o **Data Processing Agreement** com o provedor do endpoint de inferência
**residente no Brasil** (LGPD/residência de dados, sa-east-1). Até lá a inferência PHI fica
**fechada** (`inference.py` levanta `PhiEndpointNotConfigured`). O código de validação de região +
o *hook* `BR_INFERENCE_ENDPOINT` já existem.

### 3.2 🔴 Verificação LGPD de apagamento (gap #5)
Definir o processo que alimenta `ERASED_PATIENT_IDS` (lista mensal de pacientes apagados) consumido
pelo CronJob `verify-erasure` (`deploy/helm/maezo-tenant/templates/cronjob-lifecycle.yaml`), que
prova **zero PHI residual** nas 3 camadas (memória, checkpoints, surrogate store).

### 3.3 🟡 Política de retenção de auditoria (5 anos)
Validar a política de retenção implementada: `audit_chain` **não-particionada** com anti-fork
DB-atômico (`UNIQUE(prev_record_hash)`) + retenção por `DELETE`-por-idade >5 anos (CronJob
`audit-retention`; ver **DL-0018**). Confirmar 5 anos como o prazo regulatório correto.

### 3.4 🔴 Atestação regulatória / ANS
Sign-off jurídico/regulatório dos artefatos de política e da submissão ANS (a decisão de lançamento
é humana, ADR-0018/§6.2).

---

## 4. Finanças / Procurement

| Contrato a fechar | Habilita | Gap |
|---|---|---|
| 🔴 **Endpoint LLM PHI BR-resident** + DPA | inferência da Zona PHI | #2 |
| 🔴 **Provedor LLM** (Zona Geral) + tabela de **pricing** | `LLM_GENERAL_API_KEY` + custo USD por tier (`_COST_PER_1K_TOKENS_USD`) | #29 |
| 🔴 **Acesso Tasy/Oracle** (acordo de integração) | `TASY_ORACLE_DSN` no fhir-sync | #33 |
| 🟡 **WABA (WhatsApp Business)** | canal WhatsApp (Helena) | — |
| 🔴 **Billing AWS** (conta, limites, sa-east-1) | todo o `terraform apply` (EKS, Aurora, AMP/AMG) | #15/#32 |

---

## 5. PO / Regulatório / Outros

- 🟡 **Datas de competência ANS** reais (Track C6) — substituir placeholders.
- 🟡 **Confirmar o mapa de personas** com o PO (Beatriz↔Valentina) antes de fixar (evita retrabalho).
- 🟡 **Decisão de lançamento** (go/no-go) após as revisões clínica/jurídica/regulatória acima.

---

## Apêndice — Variáveis de ambiente (referência rápida)

`.env.example` é a fonte. Principais para produção:

| Var | Significado | Fonte em prod |
|---|---|---|
| `DATABASE_URL` | Aurora (estado/memória/auditoria) | ExternalSecret `aurora/master-user-secret` |
| `CIBSEVEN_BASE_URL` / `FHIR_BASE_URL` | engine + FHIR | URL externa ou StatefulSet in-cluster (gated) |
| `KAFKA_BOOTSTRAP_SERVERS` | eventos/CDC/auditoria | MSK (amh-data-platform) |
| `LLM_GENERAL_API_KEY` | LLM Zona Geral (PHI pseudonimizado) | ExternalSecret `llm/api-keys` |
| `LLM_PHI_ENDPOINT` / `BR_INFERENCE_ENDPOINT` / `LLM_PHI_API_KEY` | LLM Zona PHI (BR-resident) | contrato + ExternalSecret (§6.2) |
| `PHI_HMAC_KEY` | chave HMAC do pseudonymizer | cofre/KMS (§6.2; vazia em dev = chave determinística) |
| `TASY_ORACLE_DSN` / `TASY_ORACLE_USER` | integração Tasy | ExternalSecret `tasy/oracle` (§6.2) |
| `EPISODIC_ATTACHMENTS_BUCKET` / `_REGION` | anexos episódicos S3 | bucket + IRSA (§6.2) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | export de traços | collector → AMP/AMG (TLS+SigV4 em prod) |
| `WHATSAPP_TOKEN` / `_PHONE_NUMBER_ID` / `_WEBHOOK_VERIFY_TOKEN` | canal WhatsApp | ExternalSecret `whatsapp/waba-token` (§6.2) |
| `AWS_ENABLED` | habilita CD/`terraform apply` reais | `true` em prod/staging |

---

_Última atualização: 2026-06-14 (run de conclusão autônoma — 22 PRs, 37 gaps). Para o detalhe de
cada gap e o item de código já fiado, ver `docs/reports/autonomous-completion-report.md` §6._
