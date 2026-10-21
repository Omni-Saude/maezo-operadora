# ---------------------------------------------------------------------------
# Material staff/human do portal NA AWS (DL-0113; runbook docs/runbooks/material-staff-na-aws.md).
#
# Troca o volume Docker `maezo-onda2b-materials` do notebook do dono por:
#   * um S3 DEDICADO (versionado, SSE-KMS com CMK PROPRIA, Block Public Access, sem DELETE por API)
#     que guarda a RAIZ de assinatura de DEV (delegacao N1, emenda de 25/09/2026; a troca de ancora
#     em dev foi aprovada pelo dono em 15/10/2026; producao exige aprovador humano, D-F) e as saidas
#     de cada renovacao;
#   * uma task Fargate avulsa `staff-material` (imagem de operacao, `python -m tools.staff_ops material`)
#     que remonta os materiais dos segredos vivos, gera a raiz e roda o `gerar`.
#
# O que isto NAO faz, de proposito: gravar segredo. A SCP `deny-secrets-without-rotation` (p-9m8f47yd)
# nega `PutSecretValue` a qualquer task role e a role abaixo ainda leva um Deny explicito. A publicacao
# continua com a OrganizationAccountAccessRole (runbook passo 5), lendo a saida deste bucket.
#
# INERTE: `staff_material = null` (default) => nenhum recurso, nenhum data source. Sem `image_digest`
# so a infraestrutura (bucket, CMK, role, log group) existe; a task definition so nasce com o digest.
# A task ainda roda em modo `verificar` por padrao (DRY-RUN: le e confere, nao escreve nada).
# ---------------------------------------------------------------------------

variable "staff_material" {
  description = <<-EOT
    Material staff na AWS. null = nada e criado.
    image_digest: digest (sha256:<64 hex>) da imagem de operacao com `tools.staff_ops material`; null =
    so a infraestrutura (sem task definition).
    *_secret_arn: ARNs EXATOS dos segredos que o `restaurar` le (a task so tem GetSecretValue neles).
    syn_secret_arn: opcional (fixture SYN).
  EOT
  type = object({
    image_digest          = optional(string)
    native_secret_arn     = string
    staff_secret_arn      = string
    job_secret_arn        = string
    rows_secret_arn       = string
    assignment_secret_arn = string
    syn_secret_arn        = optional(string)
  })
  default = null

  validation {
    # Ternarios, nao `||`: o Terraform 1.10 do CI nao faz curto-circuito e avaliaria o lado direito com null.
    condition = var.staff_material == null ? true : (
      (var.staff_material.image_digest == null ? true : can(regex("^sha256:[0-9a-f]{64}$", var.staff_material.image_digest))) &&
      can(regex("^arn:aws:secretsmanager:sa-east-1:${var.aws_account_id}:secret:maezo-operadora/dev/engine/native-materials-[A-Za-z0-9]{6}$", var.staff_material.native_secret_arn)) &&
      can(regex("^arn:aws:secretsmanager:sa-east-1:${var.aws_account_id}:secret:maezo-operadora/dev/portal/amh/staff-materials-[A-Za-z0-9]{6}$", var.staff_material.staff_secret_arn)) &&
      can(regex("^arn:aws:secretsmanager:sa-east-1:${var.aws_account_id}:secret:maezo-operadora/dev/staff-job/materials-[A-Za-z0-9]{6}$", var.staff_material.job_secret_arn)) &&
      can(regex("^arn:aws:secretsmanager:sa-east-1:${var.aws_account_id}:secret:maezo-operadora/dev/staff-install/rows-[A-Za-z0-9]{6}$", var.staff_material.rows_secret_arn)) &&
      can(regex("^arn:aws:secretsmanager:sa-east-1:${var.aws_account_id}:secret:maezo-operadora/dev/staff-install/assignment-[A-Za-z0-9]{6}$", var.staff_material.assignment_secret_arn)) &&
      (var.staff_material.syn_secret_arn == null ? true : can(regex("^arn:aws:secretsmanager:sa-east-1:${var.aws_account_id}:secret:maezo-operadora/dev/staff-install/syn-fixture-[A-Za-z0-9]{6}$", var.staff_material.syn_secret_arn)))
    )
    error_message = "staff_material: digest sha256 (opcional) e os ARNs exatos (native-materials, staff-materials, staff-job/materials, staff-install/rows, staff-install/assignment e, opcional, syn-fixture) desta conta/regiao."
  }
}

locals {
  staff_material      = var.staff_material == null ? {} : { this = var.staff_material }
  staff_material_task = var.staff_material == null ? {} : (var.staff_material.image_digest == null ? {} : { this = var.staff_material })
  staff_material_secret_arns = var.staff_material == null ? [] : compact([
    var.staff_material.native_secret_arn,
    var.staff_material.staff_secret_arn,
    var.staff_material.job_secret_arn,
    var.staff_material.rows_secret_arn,
    var.staff_material.assignment_secret_arn,
    var.staff_material.syn_secret_arn,
  ])
}

# ------------------------------------------------------------------ role da task
resource "aws_iam_role" "staff_material" {
  for_each           = local.staff_material
  name               = "${local.name}-staff-material"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.base_tags
}

data "aws_iam_policy_document" "staff_material_task" {
  for_each = local.staff_material

  statement {
    sid       = "LerSegredosExatos"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = sort(local.staff_material_secret_arns)
  }
  statement {
    sid       = "DecifrarSegredosViaSecretsManager"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.native_materials["engine-native"].arn, aws_kms_key.native_materials["portal-staff"].arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
    }
  }
  statement {
    sid       = "EstadoNoS3"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.staff_material[each.key].arn}/*"]
  }
  statement {
    sid       = "ListarEstado"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.staff_material[each.key].arn]
  }
  statement {
    sid       = "CMKDoEstadoViaS3"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [aws_kms_key.staff_material[each.key].arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.aws_region}.amazonaws.com"]
    }
  }
  # Cinto e suspensorios: a SCP ja nega; a role tambem nunca grava segredo.
  statement {
    sid    = "NuncaGravarSegredo"
    effect = "Deny"
    actions = [
      "secretsmanager:PutSecretValue", "secretsmanager:CreateSecret", "secretsmanager:UpdateSecret",
      "secretsmanager:DeleteSecret", "secretsmanager:RestoreSecret", "secretsmanager:RotateSecret",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "staff_material" {
  for_each = local.staff_material
  name     = "${local.name}-staff-material"
  role     = aws_iam_role.staff_material[each.key].id
  policy   = data.aws_iam_policy_document.staff_material_task[each.key].json
}

# ------------------------------------------------------------------ CMK propria do estado
data "aws_iam_policy_document" "staff_material_key" {
  for_each = local.staff_material

  statement {
    sid    = "EnableIAMAdminOfAccount"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${var.aws_account_id}:root"]
    }
    actions   = ["kms:*"]
    resources = ["*"]
  }
  statement {
    sid    = "TarefaUsaOEstadoViaS3"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.staff_material[each.key].arn]
    }
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.aws_region}.amazonaws.com"]
    }
  }
  statement {
    sid    = "CaminhoSancionadoLeSaidaEGravaInsumos"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [local.native_kms_writer_arn]
    }
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_kms_key" "staff_material" {
  for_each                = local.staff_material
  description             = "Estado do material staff na AWS (DL-0113): raiz de assinatura de DEV e saidas das renovacoes. Decrypt so via S3."
  key_usage               = "ENCRYPT_DECRYPT"
  enable_key_rotation     = true
  rotation_period_in_days = 365
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.staff_material_key[each.key].json
  tags                    = merge(local.base_tags, { Name = "${local.name}-staff-material" })
}

resource "aws_kms_alias" "staff_material" {
  for_each      = local.staff_material
  name          = "alias/${local.name}-staff-material"
  target_key_id = aws_kms_key.staff_material[each.key].key_id
}

# ------------------------------------------------------------------ bucket de estado
resource "aws_s3_bucket" "staff_material" {
  for_each      = local.staff_material
  bucket        = "${local.name}-staff-material-${var.aws_account_id}"
  force_destroy = false
  # SCP `require-mandatory-tags` (p-bb3h9tps) nega CreateBucket sem estas cinco tags.
  tags = merge(local.base_tags, {
    tenant      = "amh"
    environment = "dev"
    workload    = "maezo-staff-material"
    owner       = "data-platform@amh.health"
    cost_center = "amh-data-dev"
  })
}

resource "aws_s3_bucket_versioning" "staff_material" {
  for_each = local.staff_material
  bucket   = aws_s3_bucket.staff_material[each.key].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "staff_material" {
  for_each = local.staff_material
  bucket   = aws_s3_bucket.staff_material[each.key].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.staff_material[each.key].arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "staff_material" {
  for_each                = local.staff_material
  bucket                  = aws_s3_bucket.staff_material[each.key].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "staff_material_bucket" {
  for_each = local.staff_material

  statement {
    sid     = "SoTLS"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [aws_s3_bucket.staff_material[each.key].arn, "${aws_s3_bucket.staff_material[each.key].arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
  # Dados: so a task e o caminho sancionado (OAAR, que le a saida para publicar). Administrar o
  # bucket (policy, config) continua com o admin da conta; ler/gravar objeto nao.
  statement {
    sid       = "ObjetosSoATarefaEOCaminhoSancionado"
    effect    = "Deny"
    actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"]
    resources = ["${aws_s3_bucket.staff_material[each.key].arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "ArnNotEquals"
      variable = "aws:PrincipalArn"
      values   = [aws_iam_role.staff_material[each.key].arn, local.native_kms_writer_arn]
    }
  }
  # A raiz de assinatura: so a task a le ou grava. Nem o caminho sancionado.
  statement {
    sid       = "RaizSoATarefa"
    effect    = "Deny"
    actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"]
    resources = ["${aws_s3_bucket.staff_material[each.key].arn}/raiz/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "ArnNotEquals"
      variable = "aws:PrincipalArn"
      values   = [aws_iam_role.staff_material[each.key].arn]
    }
  }
  # Sem apagar objeto/versao por API, para ninguem: a raiz nao se perde por engano. Remover exige
  # editar esta policy (admin da conta), um ato deliberado e auditavel.
  statement {
    sid       = "SemApagarObjeto"
    effect    = "Deny"
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = ["${aws_s3_bucket.staff_material[each.key].arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

resource "aws_s3_bucket_policy" "staff_material" {
  for_each = local.staff_material
  bucket   = aws_s3_bucket.staff_material[each.key].id
  policy   = data.aws_iam_policy_document.staff_material_bucket[each.key].json

  depends_on = [aws_s3_bucket_public_access_block.staff_material]
}

# ------------------------------------------------------------------ task avulsa
resource "aws_cloudwatch_log_group" "staff_material" {
  for_each          = local.staff_material_task
  name              = "/ecs/${local.name}/staff-material"
  retention_in_days = 30
  tags              = local.base_tags
}

resource "aws_ecs_task_definition" "staff_material" {
  for_each                 = local.staff_material_task
  family                   = "${local.name}-staff-material"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.staff_material[each.key].arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([
    {
      name                   = "prepare"
      image                  = "${aws_ecr_repository.app.repository_url}@${each.value.image_digest}"
      essential              = false
      entryPoint             = ["sh", "-c"]
      command                = [local.staff_ops_prepare_script]
      user                   = "0:0"
      readonlyRootFilesystem = true
      linuxParameters        = local.staff_ops_init_caps
      mountPoints = [
        { sourceVolume = "tmp", containerPath = "${local.staff_ops_scratch_root}/tmp", readOnly = false }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.staff_material[each.key].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "staff-material-prepare"
        }
      }
    },
    merge(local.staff_ops_common, {
      name      = "staff-material"
      image     = "${aws_ecr_repository.app.repository_url}@${each.value.image_digest}"
      essential = true
      command   = ["tools.staff_ops", "material"]
      dependsOn = [{ containerName = "prepare", condition = "SUCCESS" }]
      # Scratch efemero para o TMPDIR (rootfs read-only), fora de /tmp de proposito: cerca G2.
      mountPoints = [{ sourceVolume = "tmp", containerPath = "/run/staff-tmp", readOnly = false }]
      environment = concat([
        { name = "AWS_REGION", value = var.aws_region },
        { name = "TMPDIR", value = "/run/staff-tmp" },
        { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
        # Padrao SEGURO: sem override a tarefa so VERIFICA (dry-run, nao escreve nada).
        { name = "MATERIAL_MODO", value = "verificar" },
        { name = "MATERIAL_BUCKET", value = aws_s3_bucket.staff_material[each.key].bucket },
        { name = "MATERIAL_KMS_KEY_ARN", value = aws_kms_key.staff_material[each.key].arn },
        { name = "MATERIAL_SECRET_NATIVE", value = each.value.native_secret_arn },
        { name = "MATERIAL_SECRET_STAFF", value = each.value.staff_secret_arn },
        { name = "MATERIAL_SECRET_JOB", value = each.value.job_secret_arn },
        { name = "MATERIAL_SECRET_ROWS", value = each.value.rows_secret_arn },
        { name = "MATERIAL_SECRET_ASSIGNMENT", value = each.value.assignment_secret_arn },
        ],
        each.value.syn_secret_arn == null ? [] : [{ name = "MATERIAL_SECRET_SYN", value = each.value.syn_secret_arn }]
      )
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.staff_material[each.key].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "staff-material"
        }
      }
    })
  ])

  tags = local.base_tags
}

output "staff_material" {
  description = "Material staff na AWS (vazio com staff_material = null): bucket de estado, CMK, role e familia da task."
  value = {
    for key, value in local.staff_material : key => {
      bucket       = aws_s3_bucket.staff_material[key].bucket
      kms_key_arn  = aws_kms_key.staff_material[key].arn
      task_role    = aws_iam_role.staff_material[key].arn
      task_family  = "${local.name}-staff-material"
      task_created = contains(keys(local.staff_material_task), key)
    }
  }
}
