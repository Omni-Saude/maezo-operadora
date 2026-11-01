# Anexos do pedido de reembolso pelo WhatsApp (DL-0160/DL-0161): foto/PDF do pedido medico e da nota fiscal.
#
# DESLIGADO por padrao (`reembolso_anexos_enabled = false`): com false NENHUM recurso deste arquivo existe e as
# task definitions do receptor e do portal ficam byte-identicas. Dado de saude (Zona PHI, ADR-0006): ligar exige
# ciencia do DPO (retencao de 5 anos segundo a planilha do dono; decisao final do DPO).
#
# SEPARACAO DE PAPEIS, por IAM:
#   * receptor do webhook — SO' grava (`s3:PutObject` no prefixo `reembolso/`) e pede chave de dados ao KMS;
#     nunca le um anexo de volta;
#   * portal              — SO' le (`s3:GetObject` no prefixo `reembolso/`) e decifra; nunca grava.
# A chave KMS e' propria e so' serve a este bucket (condicao `kms:ViaService` + contexto do bucket). O bucket nega
# transporte sem TLS e objeto sem SSE-KMS desta chave.

locals {
  reembolso_anexos_ativo  = var.reembolso_anexos_enabled ? 1 : 0
  reembolso_anexos_bucket = "${local.name}-reembolso-anexos-${data.aws_caller_identity.current.account_id}"
  reembolso_anexos_prefixo = "reembolso/"
}

resource "aws_kms_key" "reembolso_anexos" {
  count                   = local.reembolso_anexos_ativo
  description             = "${local.name} — anexos do pedido de reembolso (PHI, DL-0160)"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  tags                    = merge(local.base_tags, { zona = "phi" })
}

resource "aws_kms_alias" "reembolso_anexos" {
  count         = local.reembolso_anexos_ativo
  name          = "alias/${local.name}-reembolso-anexos"
  target_key_id = aws_kms_key.reembolso_anexos[0].key_id
}

resource "aws_s3_bucket" "reembolso_anexos" {
  count         = local.reembolso_anexos_ativo
  bucket        = local.reembolso_anexos_bucket
  force_destroy = false
  # SCP `require-mandatory-tags` (p-bb3h9tps) nega CreateBucket sem estas cinco tags.
  tags = merge(local.base_tags, {
    tenant      = "amh"
    environment = "dev"
    workload    = "maezo-reembolso-anexos"
    owner       = "data-platform@amh.health"
    cost_center = "amh-data-dev"
    zona        = "phi"
  })
}

resource "aws_s3_bucket_ownership_controls" "reembolso_anexos" {
  count  = local.reembolso_anexos_ativo
  bucket = aws_s3_bucket.reembolso_anexos[0].id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "reembolso_anexos" {
  count                   = local.reembolso_anexos_ativo
  bucket                  = aws_s3_bucket.reembolso_anexos[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reembolso_anexos" {
  count  = local.reembolso_anexos_ativo
  bucket = aws_s3_bucket.reembolso_anexos[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.reembolso_anexos[0].arn
    }
    bucket_key_enabled = true
  }
}

# Retencao: 5 anos (1826 dias) a partir da gravacao — PROPOSTA a partir da planilha do dono; o prazo final e' do
# DPO (`erasure-plan.template.yaml`, camada `coleta_reembolso`). Sem versionamento: um anexo nunca e' reescrito
# (a referencia e' aleatoria) e apagar por pedido LGPD tem de apagar de verdade.
resource "aws_s3_bucket_lifecycle_configuration" "reembolso_anexos" {
  count  = local.reembolso_anexos_ativo
  bucket = aws_s3_bucket.reembolso_anexos[0].id
  rule {
    id     = "reembolso-5-anos"
    status = "Enabled"
    filter {
      prefix = local.reembolso_anexos_prefixo
    }
    expiration {
      days = 1826
    }
  }
}

data "aws_iam_policy_document" "reembolso_anexos_bucket" {
  count = local.reembolso_anexos_ativo
  statement {
    sid       = "SoTls"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.reembolso_anexos[0].arn, "${aws_s3_bucket.reembolso_anexos[0].arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
  statement {
    sid       = "SoComEstaChave"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.reembolso_anexos[0].arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption-aws-kms-key-id"
      values   = [aws_kms_key.reembolso_anexos[0].arn]
    }
  }
}

resource "aws_s3_bucket_policy" "reembolso_anexos" {
  count      = local.reembolso_anexos_ativo
  bucket     = aws_s3_bucket.reembolso_anexos[0].id
  policy     = data.aws_iam_policy_document.reembolso_anexos_bucket[0].json
  depends_on = [aws_s3_bucket_public_access_block.reembolso_anexos]
}

# --- Receptor: so' grava ---------------------------------------------------------------------

data "aws_iam_policy_document" "reembolso_anexos_gravar" {
  count = local.reembolso_anexos_ativo
  statement {
    sid       = "AnexoSoGrava"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.reembolso_anexos[0].arn}/${local.reembolso_anexos_prefixo}*"]
  }
  statement {
    sid       = "ChaveDeDadosParaGravar"
    effect    = "Allow"
    actions   = ["kms:GenerateDataKey"]
    resources = [aws_kms_key.reembolso_anexos[0].arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "webhook_receiver_reembolso_anexos" {
  count  = local.reembolso_anexos_ativo
  name   = "${local.name}-reembolso-anexos-gravar"
  role   = aws_iam_role.webhook_receiver_task[0].id
  policy = data.aws_iam_policy_document.reembolso_anexos_gravar[0].json
}

# --- Portal: so' le --------------------------------------------------------------------------

data "aws_iam_policy_document" "reembolso_anexos_ler" {
  count = local.reembolso_anexos_ativo
  statement {
    sid       = "AnexoSoLe"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.reembolso_anexos[0].arn}/${local.reembolso_anexos_prefixo}*"]
  }
  statement {
    sid       = "DecifraParaLer"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.reembolso_anexos[0].arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "portal_reembolso_anexos" {
  for_each = var.reembolso_anexos_enabled ? local.portal_config : {}
  name     = "${local.portal_name}-reembolso-anexos-ler"
  role     = aws_iam_role.portal_task[each.key].id
  policy   = data.aws_iam_policy_document.reembolso_anexos_ler[0].json
}

locals {
  # O ambiente dos anexos, so' quando ligado — lista vazia deixa as task definitions intactas.
  reembolso_anexos_env = var.reembolso_anexos_enabled ? [
    { name = "MAEZO_REEMBOLSO_ANEXOS_BUCKET", value = aws_s3_bucket.reembolso_anexos[0].bucket },
    { name = "MAEZO_REEMBOLSO_ANEXOS_KMS_KEY_ARN", value = aws_kms_key.reembolso_anexos[0].arn },
  ] : []
  # O portal so' LE: recebe so' o bucket (o S3 decifra com a role; a chave nao e' parametro de leitura). Prefixo
  # `MAEZO_PORTAL_` porque e' o das settings do portal (`PortalProductionSettings`).
  reembolso_anexos_env_portal = var.reembolso_anexos_enabled ? [
    { name = "MAEZO_PORTAL_REEMBOLSO_ANEXOS_BUCKET", value = aws_s3_bucket.reembolso_anexos[0].bucket },
  ] : []
}
