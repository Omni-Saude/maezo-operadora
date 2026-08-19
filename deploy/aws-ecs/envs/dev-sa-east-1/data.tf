# Recursos da PLATAFORMA de dados — somente leitura. Este state nunca cria nem
# altera VPC, subnet, NAT, Aurora, Cognito ou o ALB do HAPI: eles vivem no state
# de infrastructure/envs/dev-sa-east-1 (e -hapi) do repo amh-data-platform.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

data "aws_vpc" "this" {
  tags = { Name = var.vpc_name }
}

# Tier=private-app: onde as tasks Fargate da casa rodam (o service do HAPI usa
# exatamente estas duas subnets — medido 2026-08-13). Saida para internet pelo
# NAT, sem IP publico.
data "aws_subnets" "private_app" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.this.id]
  }

  tags = { Tier = "private-app" }
}

# ---------------------------------------------------------------------------
# Aurora compartilhado
# ---------------------------------------------------------------------------
data "aws_rds_cluster" "shared" {
  cluster_identifier = var.aurora_cluster_identifier
}

# So o ARN e' lido aqui — o VALOR do segredo nunca passa pelo Terraform (nao
# entra no state). Quem resolve o valor e' a task execution role, no momento em
# que o ECS materializa o container, via bloco `secrets` da container definition.
data "aws_secretsmanager_secret" "maezo_app_db" {
  name = var.aurora_app_secret_name
}

data "aws_secretsmanager_secret" "cibseven_app_db" {
  name = var.cibseven_app_secret_name
}

# ---------------------------------------------------------------------------
# HAPI FHIR — ALB interno da plataforma. E' o FHIR_BASE_URL dos agentes: o
# trafego clinico nao sai da VPC.
# ---------------------------------------------------------------------------
data "aws_lb" "hapi_internal" {
  name = var.hapi_internal_alb_name
}

# Credencial do app client `agent-rafael-<particao>` no Cognito. Criada e rotacionada pelo
# amh-data-platform; aqui e' LIDA, nunca criada — o nome e' derivavel da particao, o que
# mantem uma fonte de verdade so'.
data "aws_secretsmanager_secret" "fhir_cognito" {
  name = "amh/cognito/dev/agent-rafael-${var.fhir_partition}"
}
