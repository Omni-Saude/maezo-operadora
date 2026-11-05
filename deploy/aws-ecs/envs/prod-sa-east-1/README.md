# maezo-operadora — PROD (`deploy/aws-ecs/envs/prod-sa-east-1`)

Conta **441395443640** (AMH prod), `sa-east-1`. Porte do `dev-sa-east-1` para o cutover dev → prod da
plataforma AMH. **Nasce sem tráfego**: o ambiente existe (ECR, cluster, task definitions, SGs, roles, logs,
segredos vazios) e não processa nada até um PR humano virar os interruptores. **Nada aqui foi aplicado.**

> O `dev-sa-east-1` é a **Helena viva**. Este diretório não lê nem escreve o state do dev, e a trava
> `allowed_account_ids = ["441395443640"]` impede aplicar na conta do dev.

## O que este state assume da conta de prod (aplicado em 05/11/2026 pelo amh-data-platform)

Lido por **remote state** (`data.tf`, bucket `amh-tfstate-prod`), sem ID cravado:

| Recurso | De onde vem | Output lido |
|---|---|---|
| VPC 10.42.0.0/16, NAT por AZ | `envs/prod-sa-east-1` (foundation) | `vpc_id`, `private_app_subnet_ids` |
| Aurora `amh-aurora-hapi-prod` | foundation | por identificador (`aurora_cluster_identifier`) |
| Ingresso no banco | foundation (SG-marcador `amh-aurora-hapi-prod-clientes`) | `aurora_hapi_clientes_sg_id` |
| Roles `maezo_app` / `cibseven_app` | foundation (segredos VAZIOS) | `aurora_hapi_app_role_secrets` |
| Cognito `amh-maezo-bpm-prod` | foundation | `cognito_user_pool_id`; clients resolvidos **por nome** |
| FHIR | `envs/prod-sa-east-1-hapi` | `internal_ingest_url` |
| Bucket de artefatos (pacotes dos agentes) | foundation | `lake_buckets.artifacts` |
| CMK dos logs | foundation | `alias/amh-logs-prod` |

Diferença do dev que **simplifica**: o banco abre pelo SG-marcador, então **não** há PR cruzado para o Aurora.
Diferença que **acrescenta** PR cruzado: o ALB interno do HAPI de prod só aceita os SGs de
`ingest_allowed_source_sg_ids` (passo 5 do cutover).

## Interruptores (todos em `prod.auto.tfvars`, versionado)

| Variável | Default | Efeito |
|---|---|---|
| `servicos_habilitados` | `false` | `false` = todo `aws_ecs_service` com `desired_count = 0` e o agendamento do lembrete `DISABLED`. |
| `borda_publica_habilitada` | `false` | Túnel Cloudflare de prod. Só sobe com **os dois** em `true`: dá para provar o ambiente inteiro sem entrada pública. |
| `ecs_exec_habilitado` | `false` | Shell nas tasks (break-glass por PR). |
| `engine_db_schema_update` | `false` | DDL automática do CIB Seven (só no primeiro boot). |
| `bootstrap_db_habilitado` | `false` | Cria a task `bootstrap-db` e a execution role **própria** dela, a única que lê o segredo MESTRE do Aurora (compartilhado com o HAPI). Ligar só na janela do bootstrap. |
| `engine_image_variante` | `""` | Atestação de quem promove o engine. Com serviços ligados, o plan exige `"secured-v2"` **e** o digest com tag `secured-v2-*` no ECR de prod (precondition em `aws_ecs_service.cibseven`). |

**Saídas de emergência do dev travadas por validation** (review de segurança do PR #1204): `phi_excecao_regional`
só `""`, `phi_endpoint_url` só sa-east-1, `caso_sintetico_zona_geral = false`, `helena_saude_gerativa = false`
(o arquivo do experimento com perfil global nem existe aqui), `bedrock_model_id` sem perfil cross-region
(`global.`/`us.`/...), `aws_account_id = 441395443640`. Toda policy Bedrock de prod usa ARN de **sa-east-1**; o
`terraform test` varre o texto dos `.tf` e reprova região curinga ou perfil global.

**Atestação PHI só por `-var`**, sem default no repo (mesma regra do dev): `phi_zone_provider`, `phi_model_id`,
`phi_vendor_dpa_ref`, `helena_zona_phi`. O `plan` não roda sem elas — é proposital.

**Imagens só por digest**, sem default: `app_image_digest`, `webhook_receiver_image_digest`,
`agents_image_digest`, `worker_image_digest`, `engine_image_digest` (validação `^sha256:[0-9a-f]{64}$`; tag
reprova). As públicas (`cp-kafka:7.7.0`, `cloudflared:2026.8.2`, `aws-otel-collector`) estão pinadas por
digest do **índice multi-arch**.

## Decisões de custo (05/11/2026)

- **ARM64 só onde a imagem já é multi-arch** (medido com `docker buildx imagetools inspect`): kafka, cloudflared e
  metrics-collector. App, receptor, agentes, worker e engine ficam **X86_64** — o CodeBuild do dev só gera amd64.
  Migrar exige build multi-arch no dev (tarefa própria), nunca rebuild em prod.
- **Sem Container Insights** (`containerInsights = disabled`).
- **Logs**: auditoria/LGPD **365 dias** (receptor, agentes, worker, engine, retomada, relay A2A, migrations,
  bootstrap, deploy-processes); aplicação 90 (ponte, inbox, lembrete); infra 30 (kafka, cloudflared, coletor).
  Todos cifrados com a CMK `alias/amh-logs-prod`.
- **Nenhum load balancer criado aqui.** Os componentes falam por Cloud Map. O único ALB interno no caminho é o do
  HAPI (plataforma); o do interop só existe quando aquele state liga o serviço. O NLB do engine nativo do dev
  não foi portado.

Custo estimado (preço público sa-east-1, sem Bedrock e sem a fundação):
- **Parado** (como nasce): ~US$ 3/mês — 5 segredos (US$ 2), zona privada do Cloud Map (US$ 0,50), ECR com as
  imagens copiadas (~2 GB, US$ 0,25), 1 alarme.
- **Ligado** (desired_count padrão, borda ligada): ~US$ 300/mês — Fargate ~US$ 285 (engine 1 vCPU/2 GB; receptor,
  worker, 3 agentes 0,5 vCPU/1 GB; ponte e relay 0,25; kafka e cloudflared em ARM64) + logs ~US$ 10.

## Fora deste PR (de propósito)

| Item do dev | Por que não veio |
|---|---|
| Canal de Teste, QA-simulador, simulador, bateria, sonda de diagnóstico, experimentos de medição/redação | Ferramenta de teste; várias cercas de CI só admitem `dev-sa-east-1`. |
| CodeBuild e roles OIDC de supply-chain/assinatura (`kms-image-signing`) | Prod **não builda**: a imagem assinada no dev é copiada por digest. |
| Role OIDC que publica pacotes dos agentes | Mover `<agente>/prod/ativo.json` é promoção humana, não esteira da `main`. |
| Portal humano, plano staff (`task-staff-*`), engine nativo (NLB mTLS, materiais KMS), sincronização do egresso Cognito | Os materiais são por ambiente (KMS, versão, catálogo admitido) e as validações do dev exigem caminhos `maezo-operadora/dev/...`. É uma onda própria de prod, não cópia. |
| A env que devolve o turno no corpo do ack (`..._DEVOLVE_TURNO`) | O próprio dev registra que produção fica sem ele (o corpo do ack vai para a Meta). |
| Allowlists de conversa/telefone do time (`helena_*_conversas`) | Nenhum número do dev aponta para prod. |

## Bloqueios conhecidos do cutover (decisão humana, não de engenharia)

1. **Engine `client-certificate` (N5).** Este env declara `engine_rest_authentication = "client-certificate"`
   (exigido por `scripts/ci/check_engine_rest_auth.py`). A imagem de engine do dev é a **aberta**; a `secured*`
   (`deploy/cibseven/Dockerfile.secured-v2`) derruba worker/agentes/canal até a migração de callers (pacote C).
   Copiar o engine do dev para cá contradiz a declaração — e o plan com serviços ligados **reprova** (precondition
   `secured-v2`). Pendente também: o health check do engine usa `/engine-rest/version` sem credencial na 8080;
   conferir que ele continua respondendo na imagem `secured-v2` antes do cutover. O `Dockerfile.secured-v2` não
   tem `LABEL`: a prova hoje é a tag `secured-v2-*` (ECR IMMUTABLE) + a atestação.
2. **Lucas só em dev.** `check_roteador_lucas.py` reprova a env da fonte de cobrança do Lucas presente fora de dev; aqui
   ela não entra e `roteador_lucas_enabled = false`. Cobrança, cancelamento PF, reembolso e Lucas v4 dependem de
   emendar a cerca por PR do dono.
3. **Flags de comportamento da Helena** que o dev liga (`helena_v4`, `helena_identidade_amh`, `helena_historico`,
   carteirinha, dependentes, base de saúde, roteador, saúde gerativa, busca semântica, consultas AMH, acesso do
   beneficiário) **nascem desligadas aqui**. Vários são "só dev" por DL; ligar em prod é PR com aval do dono/DPO.
4. **Sem AMP em prod** (foundation `create_amp = false`): `metrics_collector_desired_count = 0`, sem métrica de
   aplicação.
5. **Portal/plano staff** não existe em prod (acima): a fila humana de casos não tem onde aparecer.
6. **`maezo/prod/phi-hmac-key`**: se o cutover migrar o banco do dev (conversas, custódia), a chave tem de ser **a
   mesma** do dev (o pseudônimo `hk1_...` depende dela); se prod começar vazio, chave nova.
7. **Zona geral sem modelo**: o dev usa `global.anthropic.claude-opus-5` (perfil cross-region), recusado em prod.
   `bedrock_model_id` nasce vazio (sem grant); escolher um modelo **regional** de sa-east-1 é decisão do cutover.

## Segredos — só o recurso, sem valor

Este state cria **vazios**: `maezo/prod/a2a/card-signing-key/amh`, `maezo/prod/phi-hmac-key`,
`maezo/prod/cibseven/admin`, `maezo/prod/whatsapp/meta`, `maezo/prod/cloudflared/tunnel-token`
(`terraform output segredos_sem_valor`). Os da plataforma (`amh-aurora-hapi-prod/maezo_app` e `/cibseven_app`,
`amh/cognito/prod/*`, `amh/interop/*`) são do amh-data-platform e só são **lidos** aqui.

**Criar segredo na conta de prod exige a role `OrganizationAccountAccessRole` (OAAR).** A SCP
`deny-secrets-without-rotation` nega `secretsmanager:CreateSecret` **e** `PutSecretValue` a todo principal que não
seja a OAAR (ou break-glass/bootstrap/CI). Consequências:

- o **`apply` deste state** (que cria os 5 `aws_secretsmanager_secret`) tem de rodar com credencial da OAAR — com o
  `adm-prod` (AdministratorAccess do SSO) ele falha no `CreateSecret` no meio do apply;
- **gravar o valor** de qualquer segredo (os 5 daqui e os da plataforma) idem.

Caminho sancionado (o mesmo do dev): assumir a OAAR de prod a partir do perfil da conta de gestão e exportar as
credenciais temporárias como variáveis de ambiente (o Terraform não lê o cache de MFA da CLI). Credencial de ADMIN:
nunca em arquivo que sobreviva à sessão, e `tr -d '\r'` em todo valor lido no Windows (um `\r` no token já fez um
traceback do boto3 ecoar a credencial).

```bash
CRED=$(aws sts assume-role --profile adm-mgmt \
  --role-arn arn:aws:iam::441395443640:role/OrganizationAccountAccessRole \
  --role-session-name maezo-prod --query Credentials --output json | tr -d '\r')
export AWS_ACCESS_KEY_ID=$(jq -r .AccessKeyId <<<"$CRED") AWS_SECRET_ACCESS_KEY=$(jq -r .SecretAccessKey <<<"$CRED") \
       AWS_SESSION_TOKEN=$(jq -r .SessionToken <<<"$CRED")
unset AWS_PROFILE
# valor de segredo: sempre file://, nunca JSON inline (o PowerShell come as aspas)
aws secretsmanager put-secret-value --region sa-east-1 \
  --secret-id maezo/prod/whatsapp/meta --secret-string file://whatsapp-meta.json
```

## Subida (pessoa autorizada; NUNCA rodado por agente)

`plan` com `adm-prod` (SSO) basta. O `apply` precisa da OAAR (seção acima) por causa dos segredos.

```bash
cd deploy/aws-ecs/envs/prod-sa-east-1
aws sso login --profile adm-prod
terraform init -backend-config="kms_key_id=<arn de alias/amh-tfstate-prod>"
terraform test            # offline, provider mockado: prova "sem trafego"
terraform plan -out=prod.tfplan \
  -var app_image_digest=sha256:... -var webhook_receiver_image_digest=sha256:... \
  -var agents_image_digest=sha256:... -var worker_image_digest=sha256:... -var engine_image_digest=sha256:... \
  -var phi_zone_provider=... -var phi_model_id=... -var 'phi_vendor_dpa_ref=...' -var helena_zona_phi=...
# revisar: 0 servicos com task, 0 LB, 0 destroy
terraform apply prod.tfplan
```

**Rollback**: com `servicos_habilitados = false` não há tráfego; desfazer é `terraform destroy` deste state (os ECR têm
imagem: esvaziar antes; os segredos entram em janela de 30 dias). Depois do cutover, o rollback é apontar o webhook da
Meta de volta para o dev e virar `servicos_habilitados = false` aqui (PR + apply).

## Cutover — passos manuais (em ordem)

1. **Apply da casca** (acima) com os interruptores em `false`.
2. **Digests**: resolver no ECR do **dev** os digests do que roda lá (`aws ecr describe-images --profile adm-dev
   --repository-name amh/maezo-operadora --image-ids imageTag=<tag>`, ou o `image` das task definitions ACTIVE).
3. **Copiar as imagens** dev → prod, com a assinatura cosign: `crane copy <dev>/amh/maezo-operadora@sha256:X
   <prod>/amh/maezo-operadora:<tag>` e o mesmo para `sha256-X.sig` (o ECR de prod é IMMUTABLE e só expira o que
   não tem tag). Idem `amh/cibseven-maezo` — com o bloqueio 1 resolvido.
4. **Segredos** (com `OrganizationAccountAccessRole`): os 5 deste state + `amh-aurora-hapi-prod/maezo_app` e
   `/cibseven_app` (e as roles no Postgres, runbook do foundation) + valores de interop/HMAC do MPI (o MESMO do dev).
5. **PRs no amh-data-platform**: `ecs_tasks_security_group_id` em `ingest_allowed_source_sg_ids` do
   `prod-sa-east-1-hapi` (com o certificado interno); SG das tasks na origem do ALB interno do
   `prod-sa-east-1-interop`; `task_execution_role_arn` como leitor das chaves de interop (`maezo_receiver_task_role_arn`).
6. **Banco**: PR `bootstrap_db_habilitado = true` → apply → `run-task` de `bootstrap-db` (cria o database `maezo`;
   use a execution role própria que o state cria) → PR voltando a `false` → `migrations` (`terraform output
   run_migrations_command`).
7. **Primeiro boot do engine**: imagem `secured-v2` copiada com tag `secured-v2-<sha>`; PR com
   `engine_image_variante = "secured-v2"`, `engine_db_schema_update = true` + `servicos_habilitados = true` → apply → schema
   `cibseven` criado → PR voltando `engine_db_schema_update = false`. Depois `deploy-processes` e `engine-bootstrap`.
8. **Comportamento**: PR com as flags da Helena decididas (bloqueio 3), pacotes copiados do bucket do dev e ponteiro
   `<agente>/prod/ativo.json` movido; `-var` da atestação PHI no apply. `staff_alerts_email` preenchido.
9. **Prova interna** (sem borda): receptor e agentes saudáveis, worker registrado no engine, FHIR 200 pelo endpoint
   interno.
10. **Borda**: túnel Cloudflare de prod (novo env em `deploy/cloudflare/`, Terraform + Access), token no segredo, PR
    `borda_publica_habilitada = true`.
11. **Meta**: cadastrar o webhook do número de WhatsApp de **produção** no hostname de prod e gravar
    `maezo/prod/whatsapp/meta`. Este é o ato do cutover; o número do dev continua no dev.
