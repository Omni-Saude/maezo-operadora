# Custodia cifrada do telefone do beneficiario para a retomada (GAP-XHITL-4, ADR-0061 — Proposto).
#
# DESLIGADO por padrao (`recipient_vault_enabled = false`): com false NENHUM recurso deste arquivo
# existe e as task definitions do receptor e da retomada ficam byte-identicas — o `terraform plan`
# continua sem mudanca. Ligar exige ciencia do DPO (ADR-0061).
#
# SEPARACAO DE PAPEIS, por IAM (decisao do dono, 25/09/2026):
#   * receptor do webhook — role propria, SO' `kms:Encrypt` na chave da custodia;
#   * agent-resume        — role propria, SO' `kms:Decrypt` na chave da custodia.
# As duas condicionadas ao encryption context `finalidade = maezo-retomada-pos-humano`
# (`gateway/recipient_custody.py::FINALIDADE`): a chave nao serve para outra coisa.
# As roles novas carregam as MESMAS politicas de runtime que a role compartilhada `task` da'
# a esses servicos hoje (Bedrock + ECS Exec) — sem isso a Helena perderia o modelo ao ligar.

locals {
  recipient_vault_ativo = var.recipient_vault_enabled ? 1 : 0
  recipient_vault_contexto = {
    "kms:EncryptionContext:finalidade" = "maezo-retomada-pos-humano"
  }
}

resource "aws_kms_key" "recipient_vault" {
  count                   = local.recipient_vault_ativo
  description             = "${local.name} — custodia do telefone do beneficiario (ADR-0061)"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  tags                    = merge(local.base_tags, { zona = "phi" })
}

resource "aws_kms_alias" "recipient_vault" {
  count         = local.recipient_vault_ativo
  name          = "alias/${local.name}-recipient-vault"
  target_key_id = aws_kms_key.recipient_vault[0].key_id
}

# --- Receptor: so' cifra ---------------------------------------------------------------------

resource "aws_iam_role" "webhook_receiver_task" {
  count              = local.recipient_vault_ativo
  name               = "${local.name}-webhook-receiver-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.base_tags
}

data "aws_iam_policy_document" "recipient_vault_encrypt" {
  count = local.recipient_vault_ativo
  statement {
    sid       = "CustodiaSoCifra"
    effect    = "Allow"
    actions   = ["kms:Encrypt"]
    resources = [aws_kms_key.recipient_vault[0].arn]
    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:finalidade"
      values   = [local.recipient_vault_contexto["kms:EncryptionContext:finalidade"]]
    }
  }
}

resource "aws_iam_role_policy" "webhook_receiver_vault_encrypt" {
  count  = local.recipient_vault_ativo
  name   = "${local.name}-recipient-vault-encrypt"
  role   = aws_iam_role.webhook_receiver_task[0].id
  policy = data.aws_iam_policy_document.recipient_vault_encrypt[0].json
}

resource "aws_iam_role_policy" "webhook_receiver_bedrock" {
  count  = local.recipient_vault_ativo
  name   = "${local.name}-bedrock"
  role   = aws_iam_role.webhook_receiver_task[0].id
  policy = data.aws_iam_policy_document.task_bedrock.json
}

resource "aws_iam_role_policy" "webhook_receiver_exec_ssm" {
  count  = local.recipient_vault_ativo
  name   = "${local.name}-ecs-exec"
  role   = aws_iam_role.webhook_receiver_task[0].id
  policy = data.aws_iam_policy_document.task_exec_ssm.json
}

# --- Retomada: so' decifra -------------------------------------------------------------------

resource "aws_iam_role" "agent_resume_task" {
  count              = local.recipient_vault_ativo
  name               = "${local.name}-agent-resume-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.base_tags
}

data "aws_iam_policy_document" "recipient_vault_decrypt" {
  count = local.recipient_vault_ativo
  statement {
    sid       = "CustodiaSoDecifra"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.recipient_vault[0].arn]
    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:finalidade"
      values   = [local.recipient_vault_contexto["kms:EncryptionContext:finalidade"]]
    }
  }
}

resource "aws_iam_role_policy" "agent_resume_vault_decrypt" {
  count  = local.recipient_vault_ativo
  name   = "${local.name}-recipient-vault-decrypt"
  role   = aws_iam_role.agent_resume_task[0].id
  policy = data.aws_iam_policy_document.recipient_vault_decrypt[0].json
}

resource "aws_iam_role_policy" "agent_resume_bedrock" {
  count  = local.recipient_vault_ativo
  name   = "${local.name}-bedrock"
  role   = aws_iam_role.agent_resume_task[0].id
  policy = data.aws_iam_policy_document.task_bedrock.json
}

locals {
  # O ambiente da custodia, so' quando ligada — lista vazia deixa as task definitions intactas.
  recipient_vault_env = var.recipient_vault_enabled ? [
    { name = "RECIPIENT_VAULT_KMS_KEY_ARN", value = aws_kms_key.recipient_vault[0].arn },
    { name = "RECIPIENT_VAULT_TTL_DAYS", value = tostring(var.recipient_vault_ttl_days) },
  ] : []
  webhook_receiver_task_role_arn = var.recipient_vault_enabled ? aws_iam_role.webhook_receiver_task[0].arn : aws_iam_role.task.arn
  agent_resume_task_role_arn     = var.recipient_vault_enabled ? aws_iam_role.agent_resume_task[0].arn : aws_iam_role.task.arn
}
