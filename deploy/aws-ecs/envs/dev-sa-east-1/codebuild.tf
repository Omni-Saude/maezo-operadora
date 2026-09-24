# Build da imagem DENTRO DA AWS — sem depender do Docker de ninguem.
#
# POR QUE ISTO EXISTE
#
# Ate 18/08/2026 a imagem so' era construida na estacao de trabalho: `docker build` +
# `docker push`. Isso quebrou duas vezes na pratica (Docker Desktop parado), e cada vez
# a consequencia foi a mesma — codigo commitado que NAO chega ao ambiente. Um pipeline
# que depende da maquina de uma pessoa nao e' pipeline, e' favor.
#
# CodeBuild resolve com o minimo de peca nova: fonte num zip no S3, build num container
# gerenciado, push no ECR. Nao exige credencial do GitHub dentro da AWS nem OIDC — e por
# isso pode existir HOJE, sem depender de nenhuma decisao de acesso.
#
# O QUE ISTO NAO E'
#
# Nao e' CI. Nao dispara em commit, nao roda teste, nao promove nada. E' o build
# reproduzivel de uma imagem, acionavel por comando. O CI de verdade (`cd.yml`) ainda
# aponta para EKS/Helm e precisa ser reescrito para ECS — trabalho proprio, e este
# projeto e' o que o `cd.yml` vai chamar quando chegar a hora.

# BUCKET: usamos um EXISTENTE, nao criamos.
#
# Medido em 18/08/2026: `s3:CreateBucket` nesta conta e' negado por SCP da organizacao
# (`policy/o-4idsrpx8v8/.../p-bb3h9tps`). Contornar guardrail de organizacao nao e'
# opcao — e nem precisa: `amh-pipeline-artifacts-dev-sa-east-1` ja existe e e' o lugar
# semanticamente certo para fonte de build.
#
# O bucket e' de OUTRO state (a plataforma). Por isso este stack le o bucket por data
# source e escreve SO' sob o proprio prefixo — nao altera versionamento, ciclo de vida
# nem politica dele. Mesma disciplina do SG do Aurora: nao se mexe em recurso de outro
# dono; usa-se.
data "aws_s3_bucket" "fonte" {
  bucket = var.bucket_fonte_build
}

# ---------------------------------------------------------------------------
# Role do CodeBuild — apenas o que o build precisa
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "codebuild_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
  }
}

resource "aws_iam_role" "codebuild" {
  name               = "${local.name}-codebuild"
  assume_role_policy = data.aws_iam_policy_document.codebuild_assume.json
  tags               = local.base_tags
}

data "aws_iam_policy_document" "codebuild" {
  statement {
    sid       = "Logs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/codebuild/${local.name}*"]
  }

  statement {
    sid       = "LerFonte"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${data.aws_s3_bucket.fonte.arn}/${var.prefixo_fonte_build}/*"]
  }

  # O token de push no ECR e' de conta, nao de repositorio — `GetAuthorizationToken`
  # nao aceita recurso granular.
  statement {
    sid       = "TokenDoEcr"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PushNoRepositorioDoMaezo"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      # DescribeImages: o buildspec imprime o DIGEST no fim, e o digest e' a
      # proveniencia real (a tag pode ser reescrita em dev). Faltou na primeira
      # versao e o build "falhou" com a imagem JA PUBLICADA — o push tinha passado,
      # so' o diagnostico morreu. Confuso o suficiente para merecer estar aqui.
      "ecr:DescribeImages",
    ]
    # Restrito ao repositorio do maezo: um build comprometido nao publica em outro.
    resources = [
      aws_ecr_repository.app.arn,
      aws_ecr_repository.engine.arn,
    ]
  }
}

resource "aws_iam_role_policy" "codebuild" {
  name   = "${local.name}-codebuild"
  role   = aws_iam_role.codebuild.id
  policy = data.aws_iam_policy_document.codebuild.json
}

# ---------------------------------------------------------------------------
# O projeto
# ---------------------------------------------------------------------------
resource "aws_codebuild_project" "imagem" {
  name          = "${local.name}-imagem"
  description   = "Build da imagem de aplicacao do maezo-operadora e push no ECR"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 30

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type = "BUILD_GENERAL1_SMALL"
    image        = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
    type         = "LINUX_CONTAINER"

    # `privileged_mode` e' obrigatorio para rodar `docker build` dentro do build.
    privileged_mode = true

    environment_variable {
      name  = "REGISTRO"
      value = split("/", aws_ecr_repository.app.repository_url)[0]
    }

    environment_variable {
      name  = "REPOSITORIO"
      value = aws_ecr_repository.app.name
    }

    # Sobrescritiveis no `start-build`. Um projeto so' constroi as DUAS imagens (a da
    # aplicacao e a do engine) porque o procedimento e' identico — o que muda e' o
    # Dockerfile e o destino. Dois projetos separados seriam duas coisas para manter em
    # sincronia sem ganho.
    environment_variable {
      name  = "DOCKERFILE"
      value = "deploy/Dockerfile"
    }

    # Build-args por ALLOWLIST FECHADA: tokens `NOME=valor` separados por espaco,
    # cada um conferido LITERALMENTE contra a lista do buildspec. Nada do valor chega
    # ao shell sem ter casado um literal — string livre aqui seria injecao de flag
    # no `docker build`. Vazio (default) = build de hoje, sem --build-arg.
    # Ex. imagem human: DOCKERFILE=deploy/cibseven/Dockerfile.human
    #   REPOSITORIO=amh/cibseven-maezo
    #   BUILD_ARGS="INSTALL_STAFF_COMPOSITION=true INSTALL_PORTAL_READ=true"
    environment_variable {
      name  = "BUILD_ARGS"
      value = ""
    }
  }

  source {
    type     = "S3"
    location = "${data.aws_s3_bucket.fonte.bucket}/${var.prefixo_fonte_build}/fonte.zip"

    # Buildspec inline: um arquivo a menos para sair de sincronia com o projeto, e o
    # conteudo fica revisavel no mesmo diff que a permissao que o executa.
    buildspec = <<-YAML
      version: 0.2

      env:
        shell: bash
        variables:
          DOCKER_BUILDKIT: "1"

      phases:
        pre_build:
          commands:
            # A tag vem de fora (TAG_IMAGEM). Sem default de proposito: um build sem
            # tag explicita produziria uma imagem que ninguem sabe identificar depois.
            - test -n "$TAG_IMAGEM" || { echo "TAG_IMAGEM nao informada"; exit 1; }
            # Allowlists fechadas. DOCKERFILE/REPOSITORIO/BUILD_ARGS sao sobrescritiveis
            # no start-build; o que nao casar um literal abaixo aborta o build.
            - |
              case "$DOCKERFILE" in
                deploy/Dockerfile|deploy/cibseven/Dockerfile|deploy/cibseven/Dockerfile.human) ;;
                *) echo "DOCKERFILE fora da allowlist: $DOCKERFILE"; exit 1 ;;
              esac
              case "$REPOSITORIO" in
                ${aws_ecr_repository.app.name}|${aws_ecr_repository.engine.name}) ;;
                *) echo "REPOSITORIO fora da allowlist: $REPOSITORIO"; exit 1 ;;
              esac
              ARGS_DOCKER=()
              for token in $BUILD_ARGS; do
                case "$token" in
                  INSTALL_STAFF_COMPOSITION=true|INSTALL_STAFF_COMPOSITION=false|INSTALL_PORTAL_READ=true|INSTALL_PORTAL_READ=false)
                    ARGS_DOCKER+=(--build-arg "$token") ;;
                  *) echo "BUILD_ARGS fora da allowlist: $token"; exit 1 ;;
                esac
              done
              : > /tmp/build-args.txt
              if [ "$${#ARGS_DOCKER[@]}" -gt 0 ]; then printf '%s\n' "$${ARGS_DOCKER[@]}" > /tmp/build-args.txt; fi
              echo "build-args: $${ARGS_DOCKER[*]:-(nenhum)}"
            - echo "construindo $REPOSITORIO:$TAG_IMAGEM a partir de $DOCKERFILE"
            - aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $REGISTRO
        build:
          commands:
            # `--provenance=false`: o buildx com provenance gera uma entrada
            # "unknown/unknown" no manifest list que confunde o Fargate ao resolver a
            # plataforma. A imagem que roda hoje foi construida sem provenance.
            # Os argumentos vem do arquivo gerado no pre_build (so' literais validados).
            - mapfile -t ARGS_DOCKER < /tmp/build-args.txt; docker build --provenance=false "$${ARGS_DOCKER[@]}" -f "$DOCKERFILE" -t "$REGISTRO/$REPOSITORIO:$TAG_IMAGEM" .
        post_build:
          commands:
            - docker push "$REGISTRO/$REPOSITORIO:$TAG_IMAGEM"
            - echo "publicado $REGISTRO/$REPOSITORIO:$TAG_IMAGEM"
            # O digest e' a proveniencia real: e' ele que amarra "o que rodou" a "o que
            # foi construido", nao a tag (que pode ser reescrita em dev).
            - aws ecr describe-images --repository-name "$REPOSITORIO" --image-ids imageTag="$TAG_IMAGEM" --query 'imageDetails[0].imageDigest' --output text
    YAML
  }

  logs_config {
    cloudwatch_logs {
      group_name = "/aws/codebuild/${local.name}-imagem"
    }
  }

  tags = local.base_tags
}
