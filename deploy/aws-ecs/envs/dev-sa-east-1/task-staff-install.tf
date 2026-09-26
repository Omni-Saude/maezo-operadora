# Instalacao da Onda 3 no Aurora (bloqueio B5 do plano portal-autoridade-nativa-dev).
#
# Espelha `task-bootstrap-db.tf`, com tres diferencas deliberadas:
#   * a imagem e a de OPERACAO (`deploy/ops/staff-install.Dockerfile`), por DIGEST: o SQL da onda
#     (~130 KB) nao cabe nos 8192 bytes de `containerOverrides`, e nao deve viajar ali;
#   * a task role e PROPRIA (`staff_install`), nao a `task` compartilhada pelos services: ela le o
#     segredo MESTRE e as senhas dos 6 logins, e nenhum service deve herdar isso;
#   * nada de `secrets` na task definition: o instalador busca por `GetSecretValue` (ARNs em env,
#     que nao sao segredo) e calcula os verificadores SCRAM dentro do container. Senha e
#     verificador nunca passam por override (`docs/runbooks/portal-dev-provisionamento.md` §5).
#
# `staff_install = null` (default) => nenhum recurso, nenhum data source: o plan e NO-OP.

variable "staff_install" {
  description = <<-EOT
    Entradas NAO secretas da task avulsa `staff-install` (Onda 3). null = nada e criado.
    image_digest: digest (sha256:<64 hex>) da imagem de operacao no repositorio do app.
    login_secret_arns: ARN do segredo de cada um dos 6 logins (4 nativos de
    `python -m tools.staff_materials login-secrets` + as DSNs session-lock/native-witness do `generate`).
  EOT
  type = object({
    image_digest      = string
    login_secret_arns = map(string)
  })
  default = null

  validation {
    # Ternario, nao `||`: o Terraform 1.10 do CI nao faz curto-circuito e avaliaria o lado direito com null.
    condition = var.staff_install == null ? true : (
      can(regex("^sha256:[0-9a-f]{64}$", var.staff_install.image_digest)) &&
      toset(keys(var.staff_install.login_secret_arns)) == toset([
        "maezo_native_schema_owner", "maezo_native_case_issuer", "maezo_native_issuer_witness",
        "portal_read_source_amh", "portal_staff_lock_amh", "portal_staff_witness_amh",
      ]) &&
      alltrue([
        for arn in values(var.staff_install.login_secret_arns) :
        can(regex("^arn:aws:secretsmanager:sa-east-1:${var.aws_account_id}:secret:maezo-operadora/dev/[A-Za-z0-9/_-]+-[A-Za-z0-9]{6}$", arn))
      ]) &&
      length(distinct(values(var.staff_install.login_secret_arns))) == 6
    )
    error_message = "staff_install exige digest sha256 completo e exatamente os 6 logins, cada um com um ARN distinto de segredo desta conta/regiao sob maezo-operadora/dev/."
  }
}

locals {
  staff_install = var.staff_install == null ? {} : { this = var.staff_install }

  # Onda 8: os logins que o `staff_ops rows` criava com a credencial mestre agora nascem aqui
  # (passo 9 do instalador), para que o `rows` nunca leia o segredo mestre.
  staff_install_extra_login_arns = var.staff_install == null || var.staff_ops == null ? {} : {
    for login, arn in {
      portal_task_source_amh  = var.staff_ops.task_source_secret_arn
      portal_human_outbox_amh = var.staff_ops.human_outbox_secret_arn
      portal_human_source_amh = var.staff_ops.human_source_secret_arn
    } : login => arn if arn != null
  }

  staff_install_secret_arns = var.staff_install == null ? {} : merge(
    { admin = local.aurora_master_secret_arn },
    var.staff_install.login_secret_arns,
    local.staff_install_extra_login_arns,
  )
}

# Metadados apenas (DescribeSecret): resolve a CMK de cada segredo para o kms:Decrypt abaixo.
data "aws_secretsmanager_secret" "staff_install" {
  for_each = local.staff_install_secret_arns
  arn      = each.value
}

data "aws_kms_key" "staff_install" {
  for_each = {
    for key, secret in data.aws_secretsmanager_secret.staff_install : key => secret.kms_key_id
    if try(length(secret.kms_key_id), 0) > 0
  }
  key_id = each.value
}

resource "aws_iam_role" "staff_install" {
  for_each           = local.staff_install
  name               = "${local.name}-staff-install"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.base_tags
}

data "aws_iam_policy_document" "staff_install" {
  for_each = local.staff_install

  statement {
    sid       = "LerSegredosDaOnda3"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = sort(values(local.staff_install_secret_arns))
  }

  # Mesma postura de `task_portal_session_secret`: so pela API do Secrets Manager e so para o
  # segredo que a chave cifra. Com a chave padrao `aws/secretsmanager` nao ha statement.
  dynamic "statement" {
    for_each = data.aws_kms_key.staff_install

    content {
      actions   = ["kms:Decrypt"]
      resources = [statement.value.arn]
      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
      }
      condition {
        test     = "StringEquals"
        variable = "kms:EncryptionContext:SecretARN"
        values   = [local.staff_install_secret_arns[statement.key]]
      }
    }
  }
}

resource "aws_iam_role_policy" "staff_install" {
  for_each = local.staff_install
  name     = "${local.name}-staff-install-secrets"
  role     = aws_iam_role.staff_install[each.key].id
  policy   = data.aws_iam_policy_document.staff_install[each.key].json
}

resource "aws_cloudwatch_log_group" "staff_install" {
  for_each          = local.staff_install
  name              = "/ecs/${local.name}/staff-install"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_ecs_task_definition" "staff_install" {
  for_each                 = local.staff_install
  family                   = "${local.name}-staff-install"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.staff_install[each.key].arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "staff-install"
    image     = "${aws_ecr_repository.app.repository_url}@${each.value.image_digest}"
    essential = true
    # ENTRYPOINT da imagem: `python -m tools.staff_install`. Sem command: nada a sobrescrever.

    environment = concat(local.db_env, [
      { name = "STAFF_INSTALL_ADMIN_SECRET_ARN", value = local.aurora_master_secret_arn },
      { name = "STAFF_INSTALL_LOGIN_SECRET_ARNS", value = jsonencode(each.value.login_secret_arns) },
      { name = "STAFF_INSTALL_EXTRA_LOGIN_SECRET_ARNS", value = length(local.staff_install_extra_login_arns) == 0 ? "" : jsonencode(local.staff_install_extra_login_arns) },
      { name = "AWS_REGION", value = var.aws_region },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ])

    readonlyRootFilesystem = true

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.staff_install[each.key].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "staff-install"
      }
    }
  }])

  tags = local.base_tags
}
