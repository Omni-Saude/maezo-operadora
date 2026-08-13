# Duas roles, com papeis distintos — confundir as duas e' o erro classico:
#   task_execution — quem o ECS AGENT usa para PREPARAR o container (puxar imagem
#                    do ECR, resolver segredos, escrever logs). A aplicacao nunca
#                    assume esta role.
#   task           — quem o PROCESSO dentro do container usa em runtime.

locals {
  # Vazio quando os segredos usam a chave padrao aws/secretsmanager.
  secret_kms_key_ids = compact(distinct([
    data.aws_secretsmanager_secret.maezo_app_db.kms_key_id,
    data.aws_secretsmanager_secret.cibseven_app_db.kms_key_id,
  ]))
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    # Confused-deputy: sem estas condicoes, qualquer conta com uma task ECS
    # poderia induzir o servico a assumir esta role.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${data.aws_partition.current.partition}:ecs:${var.aws_region}:${var.aws_account_id}:*"]
    }
  }
}

# ---------------------------------------------------------------------------
# Execution role
# ---------------------------------------------------------------------------
resource "aws_iam_role" "task_execution" {
  name               = "${local.name}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.base_tags
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Leitura dos DOIS segredos de banco, e de nada mais. A managed policy acima NAO
# cobre Secrets Manager — sem esta, o container falha em CreateContainerConfigError
# e o motivo so aparece em `describe-tasks`, nao no log da aplicacao.
data "aws_iam_policy_document" "task_execution_secrets" {
  statement {
    sid    = "LerSegredosDeBanco"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [
      data.aws_secretsmanager_secret.maezo_app_db.arn,
      data.aws_secretsmanager_secret.cibseven_app_db.arn,
    ]
  }

  # Se os segredos estiverem sob CMK (e nao sob a chave padrao aws/secretsmanager),
  # sem kms:Decrypt o GetSecretValue acima falha com AccessDenied — erro que parece
  # de permissao no segredo e nao na chave, e custa uma tarde. Com a chave padrao,
  # `kms_key_id` vem vazio, o compact() zera a lista e o statement nao e' emitido.
  dynamic "statement" {
    for_each = length(local.secret_kms_key_ids) > 0 ? [1] : []

    content {
      sid       = "DecifrarCMKDosSegredosDeBanco"
      effect    = "Allow"
      actions   = ["kms:Decrypt"]
      resources = local.secret_kms_key_ids
    }
  }
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  name   = "${local.name}-secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution_secrets.json
}

# ---------------------------------------------------------------------------
# Task role (runtime da aplicacao)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.base_tags
}

# ECS Exec (SSM) — abre shell numa task rodando. Em dev isto e' o que permite
# responder perguntas que so o interior da VPC responde (o database `maezo`
# existe? a role conecta?) sem subir bastion. Deve ser desligado em producao:
# e' acesso interativo a um processo que, la, ve dado de paciente.
data "aws_iam_policy_document" "task_exec_ssm" {
  statement {
    sid    = "ECSExecViaSSM"
    effect = "Allow"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"] # a API do SSM Messages nao suporta recurso granular
  }
}

resource "aws_iam_role_policy" "task_exec_ssm" {
  name   = "${local.name}-ecs-exec"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_exec_ssm.json
}

# NOTA — Bedrock ainda NAO esta aqui de proposito. O worker-runtime (fatia 1) nao
# chama LLM; quem chama e' o agent-runtime (fatia 2). A permissao
# bedrock:InvokeModel entra junto com aquele service, restrita aos model IDs que
# o provider realmente usa — e a decisao de zona (geral vs PHI) e' pre-requisito,
# porque inferencia com perfil `global.*` pode rotear fora do BR
# (docs/Tarefas_Pendentes.md §3.1).
