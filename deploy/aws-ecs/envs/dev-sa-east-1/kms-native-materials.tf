# ---------------------------------------------------------------------------
# CMKs dedicadas dos materiais nativos (D-G do plano portal-autoridade-nativa-dev).
#
# D-G: segredo novo so' pelo caminho sancionado (OrganizationAccountAccessRole, `file://`) e sob
# CMK DEDICADA. Duas chaves, uma por consumidor, para que o blast radius de uma policy errada seja
# um segredo e nao os dois:
#   - engine-native: `maezo-operadora/dev/engine/native-materials` (init do cibseven) e os segredos
#     das tasks de operacao do plano staff (instalador de linhas, job T1.5, runner SYN);
#   - portal-staff:  `maezo-operadora/dev/portal/amh/staff-materials` (init do portal, Onda 5).
#
# Key policy minima:
#   1. a conta (root) administra — sem isso a AWS recusa a policy e a chave fica inadministravel;
#      o uso fino continua sendo concedido por IAM (a execution role so ganha Decrypt condicionado
#      a ViaService + SecretARN em engine-native.tf / service-portal.tf);
#   2. a execution role do consumidor decifra, e SO via Secrets Manager;
#   3. a OrganizationAccountAccessRole (caminho D-G de escrita) cifra/gera data key, SO via
#      Secrets Manager. Ela nao decifra fora do servico.
# Simetricas, rotacao anual automatica ligada (a assimetrica de assinatura e' outro caso).
# ---------------------------------------------------------------------------

locals {
  native_kms_writer_arn = "arn:${data.aws_partition.current.partition}:iam::${var.aws_account_id}:role/OrganizationAccountAccessRole"
  native_kms_keys = {
    engine-native = {
      description = "Materiais nativos do engine e das tasks do plano staff (D-G). Decrypt so via Secrets Manager."
      readers     = [aws_iam_role.task_execution.arn]
    }
    portal-staff = {
      description = "Pacote staff do portal (portal-staff-material.v2, D-G). Decrypt so via Secrets Manager."
      readers     = [for role in aws_iam_role.portal_execution : role.arn]
    }
  }
}

data "aws_iam_policy_document" "native_materials_key" {
  for_each = local.native_kms_keys

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

  dynamic "statement" {
    for_each = length(each.value.readers) > 0 ? [each.value.readers] : []
    content {
      sid    = "ConsumerDecryptViaSecretsManager"
      effect = "Allow"
      principals {
        type        = "AWS"
        identifiers = statement.value
      }
      actions   = ["kms:Decrypt"]
      resources = ["*"]
      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
      }
    }
  }

  statement {
    sid    = "SanctionedWriterViaSecretsManager"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [local.native_kms_writer_arn]
    }
    actions   = ["kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_kms_key" "native_materials" {
  for_each                = local.native_kms_keys
  description             = each.value.description
  key_usage               = "ENCRYPT_DECRYPT"
  enable_key_rotation     = true
  rotation_period_in_days = 365
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.native_materials_key[each.key].json
  tags                    = merge(local.base_tags, { Name = "${local.name}-${each.key}" })
}

resource "aws_kms_alias" "native_materials" {
  for_each      = local.native_kms_keys
  name          = "alias/${local.name}-${each.key}"
  target_key_id = aws_kms_key.native_materials[each.key].key_id
}

output "native_materials_kms_key_arns" {
  description = "ARN das CMKs D-G: engine_native.kms_key_arn e portal.staff.material_kms_key_arn."
  value       = { for key, value in aws_kms_key.native_materials : key => value.arn }
}
