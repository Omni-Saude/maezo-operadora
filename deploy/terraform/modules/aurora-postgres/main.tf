# Module: aurora-postgres
# Aurora PostgreSQL 16 cluster for Maezo agent state, memory, and audit.
# NAO inclui pgvector: DU-01-b (decisao do dono R-005, 2026-09-04) removeu a camada semantica —
# `0009_drop_pgvector` dropa `agent_memory.embedding` e a extensao `vector`, e ADR-0002 §3 fica
# suspenso ate existir consumidor (emenda DRAFT em ADR-0047).
#
# Per-tenant KMS CMK pattern mirrors amh-data-platform kms-tenant module.
# Region: sa-east-1 (data residency for PHI — LGPD compliance).

locals {
  cluster_identifier = "${var.name_prefix}-aurora-${var.environment}"
  common_tags = merge(var.tags, {
    Module      = "aurora-postgres"
    ManagedBy   = "terraform"
    Platform    = "maezo"
    environment = var.environment
    DataClass   = "PHI-adjacent"   # memoria episodica de agente; pseudonimizada no gateway
  })
}

# ---------------------------------------------------------------------------
# KMS CMK for Aurora encryption (per-tenant, mirrors amh kms-tenant pattern)
# ---------------------------------------------------------------------------
resource "aws_kms_key" "aurora" {
  description             = "CMK for Aurora cluster ${local.cluster_identifier}"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = merge(local.common_tags, { Name = "${local.cluster_identifier}-cmk" })
}

resource "aws_kms_alias" "aurora" {
  name          = "alias/maezo/${var.environment}/aurora"
  target_key_id = aws_kms_key.aurora.id
}

# ---------------------------------------------------------------------------
# Subnet group + parameter group
# ---------------------------------------------------------------------------
resource "aws_db_subnet_group" "this" {
  name        = "${local.cluster_identifier}-subnet-group"
  description = "Subnet group for Maezo Aurora cluster"
  subnet_ids  = var.db_subnet_ids

  tags = merge(local.common_tags, { Name = "${local.cluster_identifier}-subnet-group" })
}

resource "aws_rds_cluster_parameter_group" "this" {
  name        = "${local.cluster_identifier}-params"
  family      = "aurora-postgresql16"
  description = "Maezo Aurora PostgreSQL 16 parameter group"

  # `shared_preload_libraries` so aceita bibliotecas carregadas no start do servidor.
  # pgvector NAO e uma delas: e uma extensao SQL, criada por `CREATE EXTENSION` nas
  # migrations pos-cluster. Declara-la aqui faria o boot do cluster falhar (DU-03).
  parameter {
    name  = "shared_preload_libraries"
    value = "pg_stat_statements"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "1000"   # log slow queries >1s
  }

  parameter {
    name  = "log_connections"
    value = "1"
  }

  tags = local.common_tags
}

resource "aws_db_parameter_group" "instance" {
  name   = "${local.cluster_identifier}-instance-params"
  family = "aurora-postgresql16"
  tags   = local.common_tags
}

# ---------------------------------------------------------------------------
# Security group
# ---------------------------------------------------------------------------
resource "aws_security_group" "aurora" {
  name        = "${local.cluster_identifier}-sg"
  description = "Allow PostgreSQL from EKS nodes only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "PostgreSQL from EKS nodes"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = var.allowed_security_group_ids
  }

  egress {
    description = "No outbound (Aurora does not initiate connections)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["127.0.0.1/32"]  # effectively deny all egress
  }

  tags = merge(local.common_tags, { Name = "${local.cluster_identifier}-sg" })
}

# ---------------------------------------------------------------------------
# Aurora Serverless v2 cluster (cost-effective; scale-to-zero for staging)
# Switch to provisioned by setting var.serverless=false.
# ---------------------------------------------------------------------------
resource "aws_rds_cluster" "this" {
  cluster_identifier      = local.cluster_identifier
  engine                  = "aurora-postgresql"
  engine_version          = "16.4"
  database_name           = var.database_name
  master_username         = var.master_username
  manage_master_user_password = true   # Secrets Manager rotation; no plaintext

  db_subnet_group_name            = aws_db_subnet_group.this.name
  vpc_security_group_ids          = [aws_security_group.aurora.id]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.this.name

  storage_encrypted = true
  kms_key_id        = aws_kms_key.aurora.arn

  backup_retention_period   = var.backup_retention_days
  preferred_backup_window   = "03:00-04:00"
  preferred_maintenance_window = "mon:04:00-mon:05:00"

  deletion_protection         = var.enable_deletion_protection
  skip_final_snapshot         = !var.enable_deletion_protection
  final_snapshot_identifier   = var.enable_deletion_protection ? "${local.cluster_identifier}-final" : null

  enabled_cloudwatch_logs_exports = ["postgresql"]

  # Serverless v2 scaling (ignored when serverless=false)
  dynamic "serverlessv2_scaling_configuration" {
    for_each = var.serverless ? [1] : []
    content {
      min_capacity = var.serverless_min_acu
      max_capacity = var.serverless_max_acu
    }
  }

  tags = merge(local.common_tags, { Name = local.cluster_identifier })
}

resource "aws_rds_cluster_instance" "writer" {
  identifier          = "${local.cluster_identifier}-writer-1"
  cluster_identifier  = aws_rds_cluster.this.id
  instance_class      = var.serverless ? "db.serverless" : var.instance_class
  engine              = aws_rds_cluster.this.engine
  engine_version      = aws_rds_cluster.this.engine_version
  db_parameter_group_name = aws_db_parameter_group.instance.name

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.aurora.arn
  performance_insights_retention_period = 7

  tags = merge(local.common_tags, { Name = "${local.cluster_identifier}-writer-1", Role = "writer" })
}

resource "aws_rds_cluster_instance" "reader" {
  count               = var.reader_count
  identifier          = "${local.cluster_identifier}-reader-${count.index + 1}"
  cluster_identifier  = aws_rds_cluster.this.id
  instance_class      = var.serverless ? "db.serverless" : var.instance_class
  engine              = aws_rds_cluster.this.engine
  engine_version      = aws_rds_cluster.this.engine_version
  db_parameter_group_name = aws_db_parameter_group.instance.name

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.aurora.arn
  performance_insights_retention_period = 7

  tags = merge(local.common_tags, { Name = "${local.cluster_identifier}-reader-${count.index + 1}", Role = "reader" })
}

# ---------------------------------------------------------------------------
# RDS Proxy (SC-05 / R-026, OWNER-DECISIONS-REGISTER, APROVADO-APOS-REVISAO-HUMANA) — DESLIGADO
# por padrao (`var.enable_rds_proxy = false`, zero recursos AWS criados enquanto assim for).
#
# Por que RDS Proxy e nao pgbouncer: este repo usa `asyncpg` em todos os quatro `create_pool`
# (SC-05's bloco de aritmetica em `deploy/helm/maezo-tenant/values.yaml` os enumera), que emite
# prepared statements por padrao — exatamente o que o modo `transaction` do pgbouncer quebra,
# obrigando a desativar prepared statements em cada pool da aplicacao. RDS Proxy e nativo do
# `aws_rds_cluster.this` (engine `aurora-postgresql`) ja provisionado acima, nao acrescenta
# processo a operar, e no pior caso (pinning de sessao por prepared statement) degrada para o
# comportamento de HOJE em vez de quebrar.
#
# Quando `enable_rds_proxy = true`: os DSNs da aplicacao passam a apontar para
# `aws_db_proxy.this[0].endpoint` em vez do endpoint direto do cluster (`aws_rds_cluster.this.
# endpoint`) — mudanca de configuracao (Helm `aurora.endpoint`/secret), nao deste modulo.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "rds_proxy" {
  count = var.enable_rds_proxy ? 1 : 0
  name  = "${local.cluster_identifier}-rds-proxy"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "rds_proxy_secrets" {
  count = var.enable_rds_proxy ? 1 : 0
  name  = "${local.cluster_identifier}-rds-proxy-secrets"
  role  = aws_iam_role.rds_proxy[0].id

  # Somente o secret master gerenciado por `manage_master_user_password` — o Proxy precisa dele
  # para autenticar contra o cluster em nome da aplicacao (auth_scheme = SECRETS abaixo).
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_rds_cluster.this.master_user_secret[0].secret_arn]
    }]
  })
}

resource "aws_db_proxy" "this" {
  count                  = var.enable_rds_proxy ? 1 : 0
  name                   = "${local.cluster_identifier}-proxy"
  engine_family          = "POSTGRESQL"
  role_arn               = aws_iam_role.rds_proxy[0].arn
  vpc_subnet_ids         = var.db_subnet_ids
  # Mesmo security group do cluster: quem alcanca o cluster hoje (EKS nodes, `var.
  # allowed_security_group_ids`) alcanca o proxy pelas mesmas regras de ingress.
  vpc_security_group_ids = [aws_security_group.aurora.id]
  require_tls            = true

  auth {
    auth_scheme = "SECRETS"
    iam_auth    = "DISABLED"
    secret_arn  = aws_rds_cluster.this.master_user_secret[0].secret_arn
  }

  tags = merge(local.common_tags, { Name = "${local.cluster_identifier}-proxy" })
}

resource "aws_db_proxy_default_target_group" "this" {
  count         = var.enable_rds_proxy ? 1 : 0
  db_proxy_name = aws_db_proxy.this[0].name

  connection_pool_config {
    max_connections_percent      = 100
    max_idle_connections_percent = 50
  }
}

resource "aws_db_proxy_target" "this" {
  count                 = var.enable_rds_proxy ? 1 : 0
  db_proxy_name         = aws_db_proxy.this[0].name
  target_group_name     = aws_db_proxy_default_target_group.this[0].name
  db_cluster_identifier = aws_rds_cluster.this.id
}
