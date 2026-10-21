# EXPERIMENTAL — REMOVER DEPOIS DO EXPERIMENTO "redacao de saude" da Helena (aprovado pelo dono em 17/10/2026).
#
# O QUE E'. Permissao MINIMA, so' para a role da tarefa avulsa do SIMULADOR (`simulador_task`), para rodar
# `scripts/experimentos/redacao_saude/experimento.py` (README na mesma pasta):
#   * invocar os modelos do experimento que a role NAO tem hoje — Mistral Large 3 (regional, sa-east-1; saiu
#     do ARN quando a zona PHI do dev virou GLM-5, DL-0117) e Claude Sonnet 5.5 / Haiku 5.5 pelo perfil
#     GLOBAL. GLM-5 e Claude Opus 5 (juiz) ja' estao em `task_bedrock` (secrets.tf);
#   * ler o codigo/perguntas e gravar os resultados SO' sob `experimentos/redacao-saude/` do bucket do
#     simulador (a CMK do bucket ja' esta' liberada via S3 em `simulador_bucket`).
#   * FASE 5 (aprovada pelo dono em 18/10/2026, so' dev): os mesmos direitos de S3 em mais DOIS prefixos,
#     para as tarefas avulsas de `scripts/tarefa_aws/lancar.py`:
#       - `experimentos/base-saude-rascunhos/` — rascunhos da base de saude pelo Sonnet 5.5
#         (`scripts/base_saude_rascunhos/`, prompts sinteticos);
#       - `dossie-seguranca/` — o dossie de seguranca para o dono clinico (`scripts/dossie_seguranca_saude/`).
#     Nenhum modelo novo: Sonnet/Haiku 5.5 ja' estavam aqui; GLM-5 e Opus 5 estao em `task_bedrock`.
#
# O QUE NAO E'. Nao toca a Helena no ar: o receptor (role `task`), os agentes e as chaves do dev ficam como
# estao; a task definition do simulador nao muda (o experimento roda com override de COMANDO no RunTask).
# Perfil GLOBAL so' porque as perguntas sao SINTETICAS, sem dado de beneficiario (o dono foi informado de que
# podem ser processadas fora do Brasil).
#
# COMO REMOVER. Apagar este arquivo, PR, merge e apply DIRECIONADO:
#   terraform apply -target=aws_iam_role_policy.simulador_experimento_redacao_saude
# (o plan mostra 1 destroy). Os resultados no bucket nao expiram sozinhos: apagar o prefixo quando o dono
# nao precisar mais deles.

data "aws_iam_policy_document" "simulador_experimento_redacao_saude" {
  statement {
    sid    = "ExperimentoRedacaoSaudeInvocarModelos"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:bedrock:sa-east-1::foundation-model/mistral.mistral-large-3-675b-instruct",
      # Perfil global: o perfil (na conta) e o modelo de base em qualquer regiao para onde ele rotear.
      "arn:${data.aws_partition.current.partition}:bedrock:*:${var.aws_account_id}:inference-profile/global.anthropic.claude-sonnet-5-5",
      "arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/anthropic.claude-sonnet-5-5",
      "arn:${data.aws_partition.current.partition}:bedrock:*:${var.aws_account_id}:inference-profile/global.anthropic.claude-haiku-5-5",
      "arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/anthropic.claude-haiku-5-5",
    ]
  }
  statement {
    sid       = "ExperimentoRedacaoSaudeListarPrefixo"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.qa_simulador.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["experimentos/redacao-saude/*", "experimentos/base-saude-rascunhos/*", "dossie-seguranca/*"]
    }
  }
  statement {
    sid     = "ExperimentoRedacaoSaudeLerEGravar"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:PutObject"]
    resources = [
      "${aws_s3_bucket.qa_simulador.arn}/experimentos/redacao-saude/*",
      "${aws_s3_bucket.qa_simulador.arn}/experimentos/base-saude-rascunhos/*",
      "${aws_s3_bucket.qa_simulador.arn}/dossie-seguranca/*",
    ]
  }
}

resource "aws_iam_role_policy" "simulador_experimento_redacao_saude" {
  name   = "${local.name}-simulador-experimento-redacao-saude"
  role   = aws_iam_role.simulador_task.id
  policy = data.aws_iam_policy_document.simulador_experimento_redacao_saude.json
}
