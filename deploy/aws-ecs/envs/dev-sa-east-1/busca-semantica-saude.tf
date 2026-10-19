# BUSCA POR SIGNIFICADO na base de saude da Helena (fase 3, decisao do dono de 18/10/2026). SO' DEV.
#
# O QUE E'. Permissao MINIMA para os QUATRO papeis que executam a Helena (os mesmos de `task_bedrock` e da saude
# gerativa: agentes, receptor, retomada e simulador) invocarem os modelos de EMBEDDINGS REGIONAIS em sa-east-1
# (modelo de base, ON_DEMAND, sem perfil de inferencia e sem perfil global). O texto enviado e' o da saude gerativa
# ja' minimizado; a regiao e' a mesma da zona PHI do GLM-5.
#
# O QUE NAO E'. Nao mexe em `task_bedrock` nem na politica experimental da rota global. Sem a chave
# `helena_busca_semantica` o codigo nem constroi o cliente, e esta permissao fica sem uso.
#
# COMO REMOVER. Apagar este arquivo, PR, merge e apply DIRECIONADO (o plan mostra 4 destroys):
#   terraform apply -target=aws_iam_role_policy.task_busca_semantica \
#     -target=aws_iam_role_policy.webhook_receiver_busca_semantica[0] \
#     -target=aws_iam_role_policy.agent_resume_busca_semantica[0] -target=aws_iam_role_policy.simulador_busca_semantica

data "aws_iam_policy_document" "busca_semantica_saude" {
  statement {
    sid     = "EmbeddingsRegionaisSaEast1"
    effect  = "Allow"
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:${data.aws_partition.current.partition}:bedrock:sa-east-1::foundation-model/cohere.embed-multilingual-v3",
      "arn:${data.aws_partition.current.partition}:bedrock:sa-east-1::foundation-model/amazon.titan-embed-text-v2:0",
    ]
  }
}

resource "aws_iam_role_policy" "task_busca_semantica" {
  name   = "${local.name}-busca-semantica-saude"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.busca_semantica_saude.json
}

resource "aws_iam_role_policy" "webhook_receiver_busca_semantica" {
  count  = local.recipient_vault_ativo
  name   = "${local.name}-busca-semantica-saude"
  role   = aws_iam_role.webhook_receiver_task[0].id
  policy = data.aws_iam_policy_document.busca_semantica_saude.json
}

resource "aws_iam_role_policy" "agent_resume_busca_semantica" {
  count  = local.recipient_vault_ativo
  name   = "${local.name}-busca-semantica-saude"
  role   = aws_iam_role.agent_resume_task[0].id
  policy = data.aws_iam_policy_document.busca_semantica_saude.json
}

resource "aws_iam_role_policy" "simulador_busca_semantica" {
  name   = "${local.name}-busca-semantica-saude"
  role   = aws_iam_role.simulador_task.id
  policy = data.aws_iam_policy_document.busca_semantica_saude.json
}
