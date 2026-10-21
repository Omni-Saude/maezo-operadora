# Modelo PROPRIO do roteador de habilidades da Helena (ADR-0066, DL-0137). SO' DEV.
#
# O QUE E'. `bedrock:InvokeModel` no modelo de base REGIONAL de sa-east-1 escolhido para o roteador
# (`helena_roteador_modelo`), nos QUATRO papeis que executam a Helena (os mesmos de `task_bedrock`: agentes, receptor,
# retomada e simulador). Separado de `task_bedrock` para o roteador continuar autorizado quando a zona PHI trocar de
# modelo. Vazio = nenhuma policy (o roteador usa o modelo da zona PHI, ja' autorizado em `task_bedrock`).
#
# COMO REMOVER. `helena_roteador_modelo = ""` + apply direcionado destes quatro recursos (o plan mostra 4 destroys).

locals {
  roteador_modelo_ativo = var.helena_roteador_modelo == "" ? 0 : 1
}

data "aws_iam_policy_document" "roteador_modelo" {
  statement {
    sid       = "RoteadorModeloRegionalSaEast1"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:${data.aws_partition.current.partition}:bedrock:sa-east-1::foundation-model/${var.helena_roteador_modelo == "" ? var.phi_model_id : var.helena_roteador_modelo}"]
  }
}

resource "aws_iam_role_policy" "task_roteador_modelo" {
  count  = local.roteador_modelo_ativo
  name   = "${local.name}-roteador-modelo"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.roteador_modelo.json
}

resource "aws_iam_role_policy" "webhook_receiver_roteador_modelo" {
  count  = local.recipient_vault_ativo * local.roteador_modelo_ativo
  name   = "${local.name}-roteador-modelo"
  role   = aws_iam_role.webhook_receiver_task[0].id
  policy = data.aws_iam_policy_document.roteador_modelo.json
}

resource "aws_iam_role_policy" "agent_resume_roteador_modelo" {
  count  = local.recipient_vault_ativo * local.roteador_modelo_ativo
  name   = "${local.name}-roteador-modelo"
  role   = aws_iam_role.agent_resume_task[0].id
  policy = data.aws_iam_policy_document.roteador_modelo.json
}

resource "aws_iam_role_policy" "simulador_roteador_modelo" {
  count  = local.roteador_modelo_ativo
  name   = "${local.name}-roteador-modelo"
  role   = aws_iam_role.simulador_task.id
  policy = data.aws_iam_policy_document.roteador_modelo.json
}
