# EXPERIMENTAL — MEDICAO DO ROTEADOR de habilidades da Helena (ADR-0066; decisao do dono de 21/10/2026). SO' DEV.
#
# O QUE E'. `bedrock:InvokeModel` SO' para o papel do SIMULADOR (tarefas de medicao com mensagens SINTETICAS) nos
# modelos REGIONAIS de sa-east-1 (modelo de base, ON_DEMAND, sem perfil de inferencia e sem perfil global) que
# entram na comparacao de latencia e acerto do prompt do roteador, e no Mistral Large 3 (teste antes de voltar a
# zona PHI do dev para ele enquanto o GLM-5 regional falha).
#
# O QUE NAO E'. Nao mexe em `task_bedrock` (a zona PHI de verdade) nem nos papeis do receptor, dos agentes e da
# retomada; nenhum codigo de producao chama estes modelos.
#
# COMO REMOVER. Apagar este arquivo, PR, merge e apply DIRECIONADO (o plan mostra 1 destroy):
#   terraform apply -target=aws_iam_role_policy.simulador_medicao_roteador

data "aws_iam_policy_document" "medicao_roteador" {
  statement {
    sid     = "MedicaoRoteadorModelosRegionaisSaEast1"
    effect  = "Allow"
    actions = ["bedrock:InvokeModel"]
    resources = [
      for m in [
        "zai.glm-4.7-flash",
        "qwen.qwen3-32b-v1:0",
        "mistral.ministral-3-14b-instruct",
        "openai.gpt-oss-20b-1:0",
        "google.gemma-3-27b-it",
        "mistral.mistral-large-3-675b-instruct",
      ] : "arn:${data.aws_partition.current.partition}:bedrock:sa-east-1::foundation-model/${m}"
    ]
  }
}

resource "aws_iam_role_policy" "simulador_medicao_roteador" {
  name   = "${local.name}-medicao-roteador-experimental"
  role   = aws_iam_role.simulador_task.id
  policy = data.aws_iam_policy_document.medicao_roteador.json
}
