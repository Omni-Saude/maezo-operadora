# Runbook: maezo-operadora em ECS/Fargate (conta de dados AMH)

> Ambiente **dev/staging técnico** — sem dado real de paciente. Subido em 2026-08-13.
> O caminho antigo (`deploy/helm/` + `deploy/terraform/`, EKS) está **superado**: o cluster EKS
> que ele referenciava nunca existiu. Não aplique os dois.

## 1. O retrato

| | |
|---|---|
| Conta | **203312548462** (`amh-data-dev`), região `sa-east-1` |
| Cluster ECS | `maezo-operadora-dev` |
| Imagem | `203312548462.dkr.ecr.sa-east-1.amazonaws.com/amh/maezo-operadora` |
| State | `s3://amh-tfstate-dev/envs/dev-sa-east-1/maezo-operadora/terraform.tfstate` |
| Terraform | `deploy/aws-ecs/envs/dev-sa-east-1/` |
| Banco | `amh-aurora-hapi-dev` (PG 17.9) — database `maezo`, schemas `amh` e `cibseven` |
| Engine BPMN | `http://cibseven.maezo-operadora-dev.internal:8080/engine-rest` (Cloud Map) |
| FHIR | ALB **interno** do HAPI da plataforma — tráfego clínico não sai da VPC |

### Serviços

| Serviço | Réplicas | Situação |
|---|---|---|
| `cibseven` | 1 | engine BPMN/DMN, 78 artefatos publicados |
| `worker-runtime` | 1 | 124 workers em 15 domínios |
| `agent-helena` | 1 | zona **geral**, inferência Bedrock real |
| `agent-rafael` | **0** | zona PHI — bloqueado pelo DPA (§3.1) |
| `agent-marina` | **0** | zona PHI — bloqueado pelo DPA (§3.1) |

## 2. Credencial (é onde todo mundo trava primeiro)

A conta de dados é alcançada por AssumeRole a partir da conta de gestão. A trust policy
**não exige MFA** (medido 13/08/2026: assumir sem código funciona) — o `mfa_serial` no
`~/.aws/config` é escolha nossa, e é o único segundo fator entre a chave estática do disco e
acesso admin à conta que guarda dado de paciente. **Mantenha.**

`MaxSessionDuration` do papel foi elevado para **43200s (12h)**: um código cobre o dia.

**O Terraform não consegue tratar prompt de MFA no backend S3.** Use um profile de sessão:

```powershell
# 1) uma vez por dia, gera a sessão (pede o código)
aws sts get-caller-identity --profile amh-data-dev

# 2) copia a sessão para um profile sem mfa_serial, que o Terraform aceita
$best = Get-ChildItem "$env:USERPROFILE\.aws\cli\cache\*.json" | ForEach-Object {
  $j = Get-Content $_.FullName -Raw | ConvertFrom-Json
  if ($j.AssumedRoleUser.Arn -like '*203312548462*') { [pscustomobject]@{ exp=[datetime]$j.Credentials.Expiration; c=$j.Credentials } }
} | Sort-Object exp -Descending | Select-Object -First 1
aws configure set aws_access_key_id     $best.c.AccessKeyId     --profile maezo-data
aws configure set aws_secret_access_key $best.c.SecretAccessKey --profile maezo-data
aws configure set aws_session_token     $best.c.SessionToken    --profile maezo-data
aws configure set region sa-east-1 --profile maezo-data
```

Depois: `$env:AWS_PROFILE='maezo-data'`.

> **PowerShell:** o `terraform` precisa do token de parada — `terraform --% apply -input=false ...`.
> E **nada de operador do PowerShell depois do `--%`** (`2>&1`, `|`): ele engole tudo como
> argumento e o comando roda sem fazer nada. Custou três execuções perdidas.

## 3. Subir do zero (ordem obrigatória)

```powershell
cd deploy/aws-ecs/envs/dev-sa-east-1
terraform init -backend-config="bucket=amh-tfstate-dev" `
  -backend-config="key=envs/dev-sa-east-1/maezo-operadora/terraform.tfstate" `
  -backend-config="region=sa-east-1"
terraform --% apply -input=false -var=image_tag=<sha>
```

**1. O SG das tasks tem de ser liberado no Aurora — em OUTRO repositório.**
`terraform output ecs_tasks_security_group_id` → o ID entra em
`module.aurora_hapi.allowed_security_group_ids`, em
`amh-data-platform/infrastructure/envs/dev-sa-east-1/databases.tf`. Não tente criar a regra
daqui: o módulo `aurora-cluster` usa `ingress` *dynamic inline*, que é autoritativo, e a regra
externa é apagada no próximo apply deles. Feito no PR #160.

**2. Bootstrap do banco** (idempotente; cria database, extensão e schemas):
```powershell
aws ecs run-task --cluster maezo-operadora-dev --task-definition maezo-operadora-dev-bootstrap-db `
  --launch-type FARGATE --region sa-east-1 `
  --network-configuration 'awsvpcConfiguration={subnets=[subnet-06647b0c664d0d22a,subnet-0e1f840dbec44e66a],securityGroups=[sg-0e2aebe1a2c1d253d],assignPublicIp=DISABLED}'
```

**3. Migrations** — mesma linha, trocando a task para `maezo-operadora-dev-migrations`.
Esperado: `Running upgrade ... -> 0008`, exit 0.

**4. Segredos** (as cascas nascem no apply; os valores NÃO passam pelo Terraform):
```powershell
# grave o JSON em ARQUIVO e use file:// — passar JSON inline pelo PowerShell
# come as aspas e o segredo é gravado como {key:...}, que o ECS rejeita com
# "invalid character 'k' looking for beginning of object key string"
aws secretsmanager put-secret-value --secret-id maezo/dev/a2a/card-signing-key/amh --secret-string file://s.json --region sa-east-1
aws secretsmanager put-secret-value --secret-id maezo/dev/phi-hmac-key --secret-string file://s2.json --region sa-east-1
```
Formato: `{"key":"<32 bytes base64>"}`. São chaves internas — gere aleatoriamente, não dependem
de contrato.

**5. Publicar os processos** no engine (task `maezo-operadora-dev-deploy-processes`).
Idempotente pelo nome do deployment: rodar de novo reporta `skipped (duplicate): 78`.

**6. Escalar os serviços**: `-var=cibseven_desired_count=1 -var=worker_desired_count=1`.

## 4. Como verificar que está de pé

```powershell
aws ecs describe-services --cluster maezo-operadora-dev `
  --services cibseven worker-runtime agent-helena --region sa-east-1 `
  --query 'services[].{s:serviceName,r:runningCount,d:desiredCount}' --output table
```

**Engine, de dentro da VPC** (não há rota da estação de trabalho — use `run-task` com override
na task de migrations, apontando um `python -c` para `/engine-rest/version`).
Esperado: `{"version":"2.1.0"}`, `deployment/count` ≥ 1, `process-definition/count` ≥ 19.

**Worker saudável** (log `/ecs/maezo-operadora-dev/worker-runtime`):
`worker_dependencies_brought_up ... audit_sink_ready=True harness_running=True workers_registered=124`
e `a2a_dossier_delegation_dispatcher_assembled ... card_signing_enforced=True`.

**Helena saudável** (log `/ecs/maezo-operadora-dev/agent-helena`):
`agent_dependencies_brought_up ... checkpointer_backend=postgres checkpointer_ready=True
effect_seams_gated=True graph_loaded=True inference_provider_ready=True policies_loadable=True`.

**Observabilidade (AF-13, a partir de 2026-09-03).** Os três daemons agora configuram structlog +
OpenTelemetry como STEP 0 de `run()`, ANTES da primeira linha de log, e o `/readyz` dos três
carrega uma checagem a mais: `observability_configured`. Duas consequências práticas neste
ambiente:

- A **primeira** linha de cada serviço passa a ser `observability_structlog_configured`, seguida de
  `observability_bootstrapped` **ou** — porque nenhuma task definition de
  `deploy/aws-ecs/envs/dev-sa-east-1/` define `OTEL_EXPORTER_OTLP_ENDPOINT` — do WARNING
  `observability_bootstrapped_without_trace_export`. Esse WARNING é a **postura documentada de
  dev** (ADR-0014: nenhum span sai do processo), não um incidente; ele existe justamente para que
  "não exporta trace nenhum" seja encontrável em vez de silencioso.
- `observability_configured` fica **saudável** nesse caso, com `detail` dizendo em palavras que os
  spans são construídos e descartados. Ele só fica VERMELHO quando o bootstrap LEVANTOU (p.ex.
  `PHI_HMAC_KEY` ausente sob uma política de chave ratificada, ou `MAEZO_LOG_LEVEL` com valor
  inválido) — e mesmo aí a liveness continua de pé, então o sintoma é "task não entra em serviço",
  nunca CrashLoop.

## 5. Falhas que já aconteceram (e o que elas significam)

| Sintoma | Causa real |
|---|---|
| `gaierror: Name or service not known` nas migrations | `env.py` não lia variável de ambiente; usava a URL do `alembic.ini` (`@postgres:5432`, host do compose). Corrigido — precedência `ALEMBIC_DATABASE_URL > DATABASE_URL > .ini`. |
| `TimeoutError` conectando no banco | SG do maezo não liberado no Aurora. É o PR no outro repositório (§3.1). Timeout, não erro de senha — o sintoma engana. |
| `InvalidCatalogNameError: database "maezo" does not exist` | O bootstrap não rodou. O README da plataforma afirma que o Terraform cria o schema; não cria. |
| `must be able to SET ROLE "maezo_app"` | No RDS o mestre não é superusuário: para criar objeto de outra role, precisa ser MEMBRO dela. O bootstrap faz o `GRANT` antes. |
| `cannot create /tmp/dsn.env: Permission denied` | Volume de task nasce `root:root`; o container roda como uid 1000. Não escreva arquivo — o DSN vem por substituição de comando. |
| `effect_seam_capabilities_unavailable` + `spec/agents not found` | `resolve_spec_agents_dir()` procura a árvore `spec/` **ao lado do pacote**. Copiar para outro caminho da imagem não resolve: tem de entrar no wheel por `force-include` (e ganhar a `COPY` correspondente no builder do Dockerfile). |
| `unable to retrieve secret from asm: invalid character 'k'` | JSON do segredo mal formado — o PowerShell comeu as aspas. Use `file://`. |
| `no Agent Card signing key ... Refusing to compose an unsigned dispatcher` | **Não é defeito.** É o ADR-0039 recusando Card não assinado. Provisione a chave por tenant; nunca use `MAEZO_A2A_ALLOW_UNSIGNED_CARDS` fora de dev. |

## 6. O que NÃO está no ar, e por quê

Nenhum destes é problema de engenharia — todos dependem de decisão ou contrato:

- **Zona PHI (rafael, marina).** O modelo default é `global.anthropic.claude-opus-5`, e um perfil
  `global.*` roteia entre regiões por desenho. Mandar PHI por ele exige o **DPA do endpoint
  BR-resident** (`docs/Tarefas_Pendentes.md` §3.1). As task definitions estão prontas: ativar é
  mudar variável, não escrever código.
- **Webhook do WhatsApp.** Exige token WABA e app secret reais (contrato comercial). Sem eles o
  endpoint só recusaria requisição — não vale um ALB público.
- **Kafka.** O ADR-036 da plataforma estacionou o CDC e deletou o cluster MSK. O outbox relay A2A
  e o notifications bridge ficam fora; o núcleo roda sem eles por desenho.
- **Manifesto de autonomia em `DRAFT`.** `action_approvals_manifest_not_ratified` → toda classe de
  ação **nega**, em modo `shadow`. É a ratificação clínica/do dono, não um bug.

## 7. Custo

Três tasks Fargate ligadas (engine 1 vCPU/2 GB, worker e helena 0,5 vCPU/1 GB cada) ≈
**US$ 83/mês**. Banco, FHIR, VPC e NAT são reaproveitados da plataforma — custo adicional zero.
Para zerar sem destruir nada: `-var=cibseven_desired_count=0 -var=worker_desired_count=0` e
`agentes.helena.replicas = 0`.
