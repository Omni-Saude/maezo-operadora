# Cluster ECS, registro de imagem, logs e descoberta de servico.

resource "aws_ecr_repository" "app" {
  name = "amh/maezo-operadora"

  # IMMUTABLE desde 19/09/2026 (decisao do dono, pacote trivy/IaC D2): dev deixa de
  # reaproveitar tag. O comentario antigo — "MUTABLE em dev: a iteracao reaproveita
  # tag. Em producao isto vira IMMUTABLE" — ja admitia o custo: sem imutabilidade nao
  # ha como provar qual artefato gerou um comportamento. Promover imagem segue sendo
  # apontar para outro tag (o CodeBuild imprime o digest de cada push); reenviar a
  # MESMA tag agora falha alto no push em vez de sobrescrever em silencio.
  image_tag_mutability = "IMMUTABLE"

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
      # Artefatos de assinatura do cosign (`sha256-<digest>.sig` / `.att`, publicados
      # por .github/workflows/supply-chain.yml) sao IMAGENS TAGEADAS para o ECR e
      # concorriam na mesma contagem de 20 da regra seguinte. Duas consequencias, as
      # duas ruins: cada digest assinado empurrava DUAS imagens de aplicacao para
      # fora da retencao, e a propria assinatura podia expirar antes da imagem que
      # ela prova — um portao que se apaga sozinho nao e' portao.
      #
      # Uma imagem que casa uma regra de prioridade MENOR (numero menor) nao e'
      # expirada por regra de prioridade maior, entao esta regra tambem TIRA os
      # artefatos de assinatura da contagem da regra 3.
      {
        rulePriority = 2
        description  = "Mantem os 60 artefatos de assinatura/atestacao (cosign) mais recentes"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["sha256-*"]
          countType      = "imageCountMoreThan"
          countNumber    = 60
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 3
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

# Registro do ENGINE. Imagem propria porque a oficial embute o showcase de
# demonstracao, que cria o usuario `demo` a cada boot — ver deploy/cibseven/Dockerfile.
resource "aws_ecr_repository" "engine" {
  name = "amh/cibseven-maezo"
  # IMMUTABLE desde 19/09/2026 (decisao do dono, pacote trivy/IaC D2). O engine e' a
  # autoridade BPMN/PHI-adjacente: era o alvo de maior valor de uma tag reescrita em
  # silencio. A imagem consome digest na task def (service-cibseven.tf), entao a
  # promocao passa por tag nova + digest, nao por sobrescrita.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(local.base_tags, { Name = "amh/cibseven-maezo" })
}

resource "aws_ecr_lifecycle_policy" "engine" {
  repository = aws_ecr_repository.engine.name

  policy = jsonencode({
    rules = [
      # Mesma razao da regra 2 do repositorio da aplicacao: desde que o
      # supply-chain.yml assina tambem o engine, `.sig`/`.att` sao imagens tageadas
      # que, sem esta regra, concorreriam na contagem de 10 e expulsariam imagens
      # reais (ou expirariam antes da imagem que provam).
      {
        rulePriority = 1
        description  = "Mantem os 60 artefatos de assinatura/atestacao (cosign) mais recentes"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["sha256-*"]
          countType      = "imageCountMoreThan"
          countNumber    = 60
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Mantem as 10 imagens mais recentes do engine"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
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
