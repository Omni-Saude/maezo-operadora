# Deploy AWS — ECS/Fargate (`deploy/aws-ecs/`)

> **Este diretório substitui `deploy/helm/` + `deploy/terraform/` como caminho de entrega
> na AWS.** Os dois antigos continuam no repositório enquanto a paridade não fecha; não
> aplique os dois. Ver "Por que ECS" abaixo.

O BFF humano tem composição dedicada, preparação desligada e pré-requisitos próprios:
[portal ECS/Fargate](envs/dev-sa-east-1/portal.md). Ela não reutiliza o Canal de Teste.

## Por que ECS, e não o EKS que o repo assumia

`deploy/terraform/envs/staging-sa-east-1/main.tf:5` declara *"EKS: references the existing
amh-data-platform cluster (`create_cluster=false`)"*. **Esse cluster nunca existiu.** Medido em
2026-08-13: `grep -ri eks infrastructure/**/*.tf` no repositório `amh-data-platform` retorna
**zero** ocorrências, e `aws eks list-clusters --region sa-east-1` na conta 203312548462 retorna
vazio. A plataforma de dados da AMH é **ECS/Fargate** — HAPI FHIR, Steward UI e Grafana rodam
assim hoje.

Decisão do dono (2026-08-13): re-alvejar o maezo para ECS/Fargate, alinhando com o que a casa
opera e mantém, em vez de criar um EKS só para este produto.

## O que este stack assume da conta 203312548462 (medido, não suposto)

| Recurso | Valor medido em 2026-08-13 | Quem é dono |
|---|---|---|
| VPC | `amh-data-vpc-dev` / `vpc-0a850a9d40b36ac5b` (10.40.0.0/16) | `infrastructure/envs/dev-sa-east-1` (plataforma) |
| Subnets de app | `Tier=private-app`: `subnet-0e1f840dbec44e66a` (1a), `subnet-06647b0c664d0d22a` (1b) | idem |
| Saída para internet | 1 NAT (`nat-04b444149ecdf9083`, em `sa-east-1a`) — **ponto único de falha**, aceitável em dev | idem |
| Aurora | `amh-aurora-hapi-dev`, PostgreSQL **17.9** (o módulo antigo do maezo pedia 16) | idem |
| Credencial de banco | secret `amh-aurora-hapi-dev/maezo_app`, valor gravado em 2026-06-04 | idem |
| Credencial do BPMN | secret `amh-aurora-hapi-dev/cibseven_app`, valor gravado em 2026-06-04 | idem |
| FHIR | ALB interno `internal-amh-hapi-fhir-dev-int-964450180.sa-east-1.elb.amazonaws.com` | `envs/dev-sa-east-1-hapi` |
| Identidade dos agentes | secrets Cognito `amh/cognito/dev/agent-rafael`, `agent-marina`, `maezo-bpm` | plataforma |
| Kafka/MSK | **não existe** — ADR-036 estacionou o CDC e deletou o cluster | — |

Nada aqui cria VPC, subnet, NAT, Aurora ou Cognito. Este state só **lê** o que a plataforma
possui, e cria exclusivamente o que é do maezo: ECR, cluster ECS, task definitions, services,
SGs, roles e logs.

## A dependência cross-repo que trava o banco

O SG do Aurora (`sg-0a8364a76c605d782`) aceita 5432 **apenas** de `sg-0bd4dc8c3c307fbcb` (a task
do HAPI). Para as tasks do maezo alcançarem o banco é preciso incluir o SG criado aqui em
`module.aurora_hapi.allowed_security_group_ids`, **no state da plataforma**
(`infrastructure/envs/dev-sa-east-1`).

Não tente resolver com um `aws_vpc_security_group_ingress_rule` deste state: o módulo
`aurora-cluster` usa um bloco `ingress` *dynamic inline*, que é **autoritativo** — a regra
externa seria apagada no próximo `apply` daquele state. A armadilha já está documentada em
`envs/dev-sa-east-1-hapi/security_groups.tf:69-78`.

**Ordem obrigatória:**

1. `terraform apply` aqui → nasce o SG das tasks (`terraform output ecs_tasks_security_group_id`)
2. PR no `amh-data-platform` acrescentando esse ID a `allowed_security_group_ids`
3. `apply` do env principal da plataforma
4. só então a task de migrations consegue conectar

## Ordem de subida

```bash
cd deploy/aws-ecs/envs/dev-sa-east-1

terraform init \
  -backend-config="bucket=amh-tfstate-dev" \
  -backend-config="key=envs/dev-sa-east-1/maezo-operadora/terraform.tfstate" \
  -backend-config="region=sa-east-1"

terraform plan    # revisar SEMPRE — este env vive na mesma conta dos dados de paciente
terraform apply
```

Depois do apply, a fatia vertical 1 se prova assim (ver `docs/runbooks/aws-ecs.md`):

```bash
# 1. imagem no ECR
# 2. task de migrations (roda alembic e SAI — não é service)
# 3. worker-runtime de pé, registrando workers no CIB Seven
```

## Nomes

Tudo leva o prefixo **`maezo-operadora`**, nunca `maezo` sozinho: a plataforma de dados tem um
serviço *dela* chamado "maezo" (`applications/maezo/` — API de orquestração Flink/DLQ/RNDS, hoje
também não implantada). Os dois produtos coexistem na mesma conta e não podem colidir em nome de
cluster, ECR, log group ou task family.

## Ambiente novo fora de dev: declarar como o `engine-rest` autentica

O engine de `dev-sa-east-1` expõe o `engine-rest` **sem autenticação** na 8080. Isso é aceito só
em dev (decisão N5, `docs/plans/portal-autoridade-nativa-dev.md` §6). Todo diretório em
`envs/` cujo nome não seja `dev` nem comece com `dev-` tem de declarar, com valor literal (ou
`local.`/`var.` que resolva para ele):

```hcl
engine_rest_authentication = "client-certificate"
```

Sem isso o CI reprova (`scripts/ci/check_engine_rest_auth.py`). A declaração não troca a imagem:
ela registra, num lugar que a revisão lê, que aquele ambiente assumiu subir uma imagem `secured*`
e migrar os callers. Copiar `dev-sa-east-1` para outro nome sem ela é exatamente o que a cerca
existe para barrar.
