# EXPERIMENTAL — rota `experimento_global_autorizado_dpo_2026-10-17` (ADR-0065, DL-0119). SO' DEV.
#
# O QUE E'. Permissao MINIMA para os QUATRO papeis que executam a Helena (os mesmos de `task_bedrock`, runbook
# `phi-inference-ops.md` §6: agentes, receptor, retomada e simulador) invocarem o Claude Haiku 5.5 pelo perfil
# GLOBAL do Bedrock — o perfil (na conta) e o modelo de base em qualquer regiao para onde ele rotear. O dono
# (CTO e DPO) autorizou, como DPO, em 17/10/2026, que a REDACAO de respostas informativas de saude da Helena
# use este perfil SO' para as conversas da allowlist (`helena_saude_gerativa_conversas`), so' em dev.
#
# O QUE NAO E'. Nao mexe em `task_bedrock` (zona geral + zona PHI regional em sa-east-1): a classificacao e todo o
# resto da Helena continuam no GLM-5 regional. Sem a chave `helena_saude_gerativa` o codigo nem constroi o
# cliente desta rota, e esta permissao fica sem uso.
#
# COMO REMOVER. Apagar este arquivo, PR, merge e apply DIRECIONADO (o plan mostra os destroys):
#   terraform apply -target=aws_iam_role_policy.task_saude_gerativa \
#     -target=aws_iam_role_policy.webhook_receiver_saude_gerativa \
#     -target=aws_iam_role_policy.agent_resume_saude_gerativa -target=aws_iam_role_policy.simulador_saude_gerativa

data "aws_iam_policy_document" "saude_gerativa_experimental" {
  statement {
    sid     = "ExperimentoGlobalAutorizadoDpoHaiku55"
    effect  = "Allow"
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:${data.aws_partition.current.partition}:bedrock:*:${var.aws_account_id}:inference-profile/global.anthropic.claude-haiku-5-5",
      "arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/anthropic.claude-haiku-5-5",
    ]
  }
}

resource "aws_iam_role_policy" "task_saude_gerativa" {
  name   = "${local.name}-saude-gerativa-experimental"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.saude_gerativa_experimental.json
}

resource "aws_iam_role_policy" "webhook_receiver_saude_gerativa" {
  count  = local.recipient_vault_ativo
  name   = "${local.name}-saude-gerativa-experimental"
  role   = aws_iam_role.webhook_receiver_task[0].id
  policy = data.aws_iam_policy_document.saude_gerativa_experimental.json
}

resource "aws_iam_role_policy" "agent_resume_saude_gerativa" {
  count  = local.recipient_vault_ativo
  name   = "${local.name}-saude-gerativa-experimental"
  role   = aws_iam_role.agent_resume_task[0].id
  policy = data.aws_iam_policy_document.saude_gerativa_experimental.json
}

resource "aws_iam_role_policy" "simulador_saude_gerativa" {
  name   = "${local.name}-saude-gerativa-experimental"
  role   = aws_iam_role.simulador_task.id
  policy = data.aws_iam_policy_document.saude_gerativa_experimental.json
}
