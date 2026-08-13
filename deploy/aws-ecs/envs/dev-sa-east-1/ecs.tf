# Cluster ECS, registro de imagem, logs e descoberta de servico.

resource "aws_ecr_repository" "app" {
  name = "amh/maezo-operadora"

  # MUTABLE em dev: a iteracao reaproveita tag. Em producao isto vira IMMUTABLE —
  # sem imutabilidade nao ha como provar qual artefato gerou um comportamento.
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(local.base_tags, { Name = "amh/maezo-operadora" })
}

# Retencao de imagem: sem isto o repo cresce sem limite e ninguem percebe ate a
# fatura. Mantem as 20 ultimas por prefixo de commit + expira nao-tageadas.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expira imagens sem tag apos 1 dia"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Mantem as 20 imagens tageadas mais recentes"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 20
        }
        action = { type = "expire" }
      },
    ]
  })
}

resource "aws_ecs_cluster" "this" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = local.base_tags
}

# Um log group POR componente. Um group unico com todos os componentes
# transforma qualquer investigacao em filtro de string.
resource "aws_cloudwatch_log_group" "migrations" {
  name              = "/ecs/${local.name}/migrations"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${local.name}/worker-runtime"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

# ---------------------------------------------------------------------------
# Cloud Map — DNS privado estavel para os componentes conversarem entre si.
# O IP de uma task Fargate muda a cada deploy; CIBSEVEN_BASE_URL nao pode.
# A conta nao tinha namespace nenhum (medido 2026-08-13), entao criamos o nosso.
# ---------------------------------------------------------------------------
resource "aws_service_discovery_private_dns_namespace" "this" {
  name        = "${local.name}.internal"
  description = "Descoberta interna dos componentes do maezo-operadora (${local.env})"
  vpc         = data.aws_vpc.this.id

  tags = local.base_tags
}
