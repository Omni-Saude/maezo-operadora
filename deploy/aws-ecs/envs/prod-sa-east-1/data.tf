# Recursos da PLATAFORMA de dados de PROD (conta 441395443640) — somente leitura. Este
# state nunca cria nem altera VPC, subnet, NAT, Aurora, Cognito ou o ALB do HAPI: eles
# vivem nos states de infrastructure/envs/prod-sa-east-1 (foundation) e
# prod-sa-east-1-hapi do repo amh-data-platform, aplicados em 05/11/2026.
#
# REMOTE STATE em vez de ID cravado. O bucket de state de prod e' cifrado com a CMK
# `alias/amh-tfstate-prod`: quem roda o plan precisa de s3:GetObject no bucket e
# kms:Decrypt na CMK (o perfil `adm-prod` tem). Ler nao exige `kms_key_id`.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

data "terraform_remote_state" "foundation" {
  backend = "s3"
  config = {
    bucket = var.platform_state_bucket
    key    = "envs/prod-sa-east-1/foundation/terraform.tfstate"
    region = var.aws_region
  }
}

data "terraform_remote_state" "hapi" {
  backend = "s3"
  config = {
    bucket = var.platform_state_bucket
    key    = "envs/prod-sa-east-1-hapi/terraform.tfstate"
    region = var.aws_region
  }
}

# VPC 10.42.0.0/16 de prod, NAT por AZ. Resolvida pelo ID que o foundation publica.
data "aws_vpc" "this" {
  id = local.plataforma.vpc_id
}

# Tier=private-app do foundation de prod: as MESMAS subnets do HAPI. Filtro por ID (vindo
# do remote state), nao por tag: um rename de tag no foundation nao muda onde as tasks rodam.
data "aws_subnets" "private_app" {
  filter {
    name   = "subnet-id"
    values = local.plataforma.private_app_subnet_ids
  }

  lifecycle {
    postcondition {
      condition     = length(self.ids) >= 2
      error_message = "Esperadas >= 2 subnets private-app (uma por AZ) no foundation de prod."
    }
  }
}

# ---------------------------------------------------------------------------
# Aurora do HAPI de prod (`amh-aurora-hapi-prod`, PostgreSQL 17.9, Multi-AZ)
# ---------------------------------------------------------------------------
data "aws_rds_cluster" "shared" {
  cluster_identifier = var.aurora_cluster_identifier
}

# Segredos das roles `maezo_app`/`cibseven_app`: o foundation os cria VAZIOS (o valor e'
# passo de runbook). So' o ARN entra aqui; o VALOR nunca passa pelo Terraform.
data "aws_secretsmanager_secret" "maezo_app_db" {
  arn = local.plataforma.aurora_hapi_app_role_secrets.maezo_app
}

data "aws_secretsmanager_secret" "cibseven_app_db" {
  arn = local.plataforma.aurora_hapi_app_role_secrets.cibseven_app
}

# ---------------------------------------------------------------------------
# Cognito de prod (`amh-maezo-bpm-prod`). Pool NOVO: client_id e segredo NAO sao os do dev.
# ---------------------------------------------------------------------------
data "aws_cognito_user_pool" "prod" {
  user_pool_id = local.plataforma.cognito_user_pool_id
}

data "aws_cognito_user_pool_clients" "prod" {
  user_pool_id = local.plataforma.cognito_user_pool_id
}

locals {
  cognito_token_url = "https://${data.aws_cognito_user_pool.prod.domain}.auth.${var.aws_region}.amazoncognito.com/oauth2/token"

  _cognito_client_id_por_nome = zipmap(
    data.aws_cognito_user_pool_clients.prod.client_names,
    data.aws_cognito_user_pool_clients.prod.client_ids,
  )

  fhir_cognito_client_name = "agent-rafael-${var.fhir_partition}"
  # Resolvido pelo NOME do client no pool de prod. Um valor explicito em
  # `var.fhir_cognito_client_id` vence (so' para diagnostico; o default e' vazio).
  fhir_cognito_client_id = var.fhir_cognito_client_id != "" ? var.fhir_cognito_client_id : lookup(local._cognito_client_id_por_nome, local.fhir_cognito_client_name, "")

  amh_interop_client_id_efetivo        = var.amh_interop_client_id != "" ? var.amh_interop_client_id : lookup(local._cognito_client_id_por_nome, "maezo-operadora-interop", "")
  amh_interop_boleto_client_id_efetivo = var.amh_interop_boleto_client_id != "" ? var.amh_interop_boleto_client_id : lookup(local._cognito_client_id_por_nome, "maezo-operadora-interop-boleto", "")
}

# Credencial do client `agent-rafael-<particao>` no pool de PROD. Criada pelo
# amh-data-platform (modulo cognito-user-pool, `amh/cognito/prod/<client>`); aqui e' LIDA.
data "aws_secretsmanager_secret" "fhir_cognito" {
  name = "amh/cognito/${local.env}/${local.fhir_cognito_client_name}"
}

check "fhir_client_no_pool_de_prod" {
  assert {
    condition     = local.fhir_cognito_client_id != ""
    error_message = "O client ${local.fhir_cognito_client_name} nao existe no pool de prod: o agente rafael levaria 401 no HAPI."
  }
}

# Bucket de artefatos de pipeline de prod (pacotes dos agentes). Criado pelo foundation.
data "aws_s3_bucket" "fonte" {
  bucket = local.bucket_artefatos
}
