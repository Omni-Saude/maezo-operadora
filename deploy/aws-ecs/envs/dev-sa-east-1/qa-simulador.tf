# Bucket do SIMULADOR DE BENEFICIARIO (`python -m maezo.qa.simulador`, runbook
# `docs/runbooks/simulador-beneficiario.md`). So' dev.
#
# O QUE MORA AQUI
#   roster/  o CSV de pessoas reais (cpf, nascimento, nome) que alguem com acesso ao Athena copia do
#            resultado de `roster.sql`. E' DADO PESSOAL: some em 7 dias (lifecycle) e so' a task do
#            simulador le'. O codigo nunca le' o lago (ADR-0037).
#   runs/    o resultado de cada execucao (manifesto, transcricoes, checagens, juiz, resumo,
#            relatorio), TUDO MASCARADO pelo simulador antes de sair do processo. Some em 30 dias.
#
# GUARDAS: CMK propria (rotacao anual), SSE-KMS por padrao com bucket key, versionamento (as versoes
# antigas expiram junto), bloqueio de acesso publico, dono do bucket dono dos objetos (sem ACL) e
# politica que recusa qualquer acesso sem TLS. O nome leva a conta, como o ledger do staff.

locals {
  qa_simulador_bucket = "${local.name}-qa-simulador-${var.aws_account_id}"
}

data "aws_iam_policy_document" "qa_simulador_kms" {
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
}

resource "aws_kms_key" "qa_simulador" {
  description             = "${local.name} — simulador de beneficiario: roster e resultados (so' dev)"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.qa_simulador_kms.json
  tags                    = merge(local.base_tags, { workload = "maezo-qa-simulador" })
}

resource "aws_kms_alias" "qa_simulador" {
  name          = "alias/${local.name}-qa-simulador"
  target_key_id = aws_kms_key.qa_simulador.key_id
}

resource "aws_s3_bucket" "qa_simulador" {
  bucket        = local.qa_simulador_bucket
  force_destroy = false
  # SCP `require-mandatory-tags` (p-bb3h9tps) nega CreateBucket sem estas cinco tags.
  tags = merge(local.base_tags, {
    tenant      = "amh"
    environment = "dev"
    workload    = "maezo-qa-simulador"
    owner       = "data-platform@amh.health"
    cost_center = "amh-data-dev"
  })
}

resource "aws_s3_bucket_ownership_controls" "qa_simulador" {
  bucket = aws_s3_bucket.qa_simulador.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "qa_simulador" {
  bucket                  = aws_s3_bucket.qa_simulador.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "qa_simulador" {
  bucket = aws_s3_bucket.qa_simulador.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "qa_simulador" {
  bucket = aws_s3_bucket.qa_simulador.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.qa_simulador.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "qa_simulador" {
  bucket = aws_s3_bucket.qa_simulador.id

  rule {
    id     = "runs-30d"
    status = "Enabled"
    filter {
      prefix = "runs/"
    }
    expiration {
      days = 30
    }
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  rule {
    id     = "roster-7d"
    status = "Enabled"
    filter {
      prefix = "roster/"
    }
    expiration {
      days = 7
    }
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  rule {
    id     = "marcadores-de-exclusao"
    status = "Enabled"
    filter {}
    expiration {
      expired_object_delete_marker = true
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.qa_simulador]
}

data "aws_iam_policy_document" "qa_simulador_bucket" {
  statement {
    sid     = "SoComTLS"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [aws_s3_bucket.qa_simulador.arn, "${aws_s3_bucket.qa_simulador.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "qa_simulador" {
  bucket = aws_s3_bucket.qa_simulador.id
  policy = data.aws_iam_policy_document.qa_simulador_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.qa_simulador]
}

output "qa_simulador_bucket" {
  description = "Bucket do simulador de beneficiario (roster/ e runs/)."
  value       = aws_s3_bucket.qa_simulador.bucket
}

output "qa_simulador_kms_key_arn" {
  description = "CMK do bucket do simulador (use no `aws s3 cp --sse-kms-key-id` do roster)."
  value       = aws_kms_key.qa_simulador.arn
}
