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

# RETENCAO DE IMAGEM — SO' EXPIRA O QUE NAO TEM TAG (27/09/2026, incidente).
#
# ATE 27/09 havia duas regras por CONTAGEM ("mantem as 20 tageadas mais recentes" e "mantem 60
# assinaturas"). O ECR nao sabe o que esta' EM USO: a contagem expirou `07e72eae`/`871ecc0b` (a
# imagem de helena, rafael, marina, worker, canal-teste, relay, ponte, migrations...) e o init
# humano do portal ENQUANTO as task definitions vivas apontavam para eles. Os servicos so' seguiram
# de pe' porque a task ja' estava rodando; o primeiro replace (apply do portal) falhou com
# `CannotPullContainerError ... not found`, e qualquer restart teria derrubado a Helena do WhatsApp.
#
# POR QUE NAO "UMA CONTAGEM MAIOR": qualquer numero continua sendo um relogio que apaga imagem
# viva — so' demora mais para acontecer, e acontece sem aviso. Nenhuma regra de lifecycle do ECR
# enxerga task definition. Entao:
#   * imagem TAGEADA nunca expira sozinha (tags sao IMMUTABLE, logo uma imagem tageada nunca vira
#     untagged por reescrita). Isso inclui `.sig`/`.att` do cosign: uma assinatura que expira antes
#     da imagem que ela prova reprova o portao 1.4;
#   * imagem SEM tag (camadas orfas de build) expira em 1 dia, como antes;
#   * limpeza de tag e' MANUAL e so' do que NENHUMA task definition ACTIVE referencia —
#     `scripts/ops/ecr_imagens_vivas.py` lista o que esta' em uso (e sai 1 se faltar alguma).
# Custo medido em 27/09: 69 artefatos, 1,3 GB no repositorio da aplicacao (~US$ 0,13/mes).
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expira so imagens sem tag apos 1 dia (tageada nunca expira: ver ecs.tf)"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
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

# Mesma regra do repositorio da aplicacao (ver o comentario acima de `aws_ecr_lifecycle_policy.app`):
# a contagem "mantem as 10 mais recentes" (tagStatus any) apagaria o engine VIVO do cibseven
# depois de mais algumas imagens human. Tageada nunca expira; sem tag expira em 1 dia.
# Medido em 27/09: 12 artefatos, 0,9 GB.
resource "aws_ecr_lifecycle_policy" "engine" {
  repository = aws_ecr_repository.engine.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expira so imagens sem tag apos 1 dia (tageada nunca expira: ver ecs.tf)"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
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
