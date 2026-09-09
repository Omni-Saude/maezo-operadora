# Segredos PROPRIOS do maezo — apenas as CASCAS (nome/ARN). O valor NUNCA passa
# pelo Terraform: se passasse, ficaria em texto no state, e o state e' um arquivo
# que muita gente le. E' a mesma postura do repo (docs/Tarefas_Pendentes.md §1.1:
# "populate via AWS Console ou put-secret-value — nunca via Terraform").
#
# Estas duas chaves NAO dependem de contrato com terceiro: sao chaves internas,
# geradas aleatoriamente. Diferente das do WhatsApp/LLM/Tasy, que sao credenciais
# de fora e continuam bloqueadas em acordo comercial.

resource "aws_secretsmanager_secret" "a2a_card_signing_key" {
  # PER-TENANT no nome, nao repo-wide: ADR-0039 decisao 4 proibe uma chave unica
  # servindo varios tenants (confusao de chave cross-tenant). O sufixo do env var
  # que o runtime le e' o tenant em maiuscula: MAEZO_A2A_CARD_SIGNING_KEY__AMH.
  name        = "maezo/${local.env}/a2a/card-signing-key/${var.tenant_id}"
  description = "Chave de assinatura do Agent Card do tenant ${var.tenant_id} (ADR-0039). Valor populado fora do Terraform."

  # Curto de proposito em dev: se a casca for destruida por engano, queremos poder
  # recriar no mesmo nome sem esperar 30 dias.
  recovery_window_in_days = 7

  tags = merge(local.base_tags, { Name = "maezo-${local.env}-a2a-card-signing-key-${var.tenant_id}" })
}

resource "aws_secretsmanager_secret" "phi_hmac_key" {
  name        = "maezo/${local.env}/phi-hmac-key"
  description = "Chave HMAC-SHA256 do Pseudonymizer (ADR-0035). Sem ela o runtime falha FECHADO em producao, por desenho."

  recovery_window_in_days = 7

  tags = merge(local.base_tags, { Name = "maezo-${local.env}-phi-hmac-key" })
}

resource "aws_secretsmanager_secret" "engine_admin" {
  name        = "maezo/${local.env}/cibseven/admin"
  description = "Credencial do administrador REAL do engine BPMN. Substitui o usuario `demo` da imagem oficial."

  recovery_window_in_days = 7

  tags = merge(local.base_tags, { Name = "maezo-${local.env}-cibseven-admin" })
}

resource "aws_secretsmanager_secret" "whatsapp_meta" {
  # Contrato do receptor e do sender no mesmo processo: app_secret/verify_token para
  # entrada; waba_token/phone_number_id para resposta. Todos os quatro campos devem
  # existir no JSON antes de iniciar a task. Valores sao populados fora do Terraform.
  # Segredos sinteticos de entrada permitem testar somente a recepcao assinada;
  # nao constituem credenciais Meta nem comprovam entrega de uma resposta real.
  name        = "maezo/${local.env}/whatsapp/meta"
  description = "Credenciais WhatsApp: app_secret, verify_token, waba_token e phone_number_id. Valores populados fora do Terraform."

  recovery_window_in_days = 7

  tags = merge(local.base_tags, { Name = "maezo-${local.env}-whatsapp-meta" })
}

# Grant de leitura para a execution role — restrito a estes ARNs.
data "aws_iam_policy_document" "task_execution_maezo_secrets" {
  statement {
    sid     = "LerSegredosPropriosDoMaezo"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.a2a_card_signing_key.arn,
      aws_secretsmanager_secret.phi_hmac_key.arn,
      aws_secretsmanager_secret.engine_admin.arn,
      aws_secretsmanager_secret.whatsapp_meta.arn,
    ]
  }
}

resource "aws_iam_role_policy" "task_execution_maezo_secrets" {
  name   = "${local.name}-maezo-secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution_maezo_secrets.json
}

# ---------------------------------------------------------------------------
# Bedrock — permissao de INFERENCIA na task role (nao na execution role: quem
# chama o modelo e' o processo da aplicacao, em runtime).
# ---------------------------------------------------------------------------
# Escopo: o perfil `global.anthropic.claude-opus-5` e as variantes regionais do
# mesmo modelo. `global.*` ROTEIA ENTRE REGIOES por desenho (inference.py:485) —
# aceitavel na zona GERAL deste staging tecnico, que nao ve dado de paciente, e
# EXATAMENTE o motivo pelo qual a zona PHI nao sobe aqui (ver service-agents.tf).
data "aws_iam_policy_document" "task_bedrock" {
  statement {
    sid    = "InvocarModeloDaZonaGeral"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      # Perfil global (cross-region) e o inference profile correspondente.
      "arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/anthropic.claude-opus-5*",
      "arn:${data.aws_partition.current.partition}:bedrock:*:${var.aws_account_id}:inference-profile/global.anthropic.claude-opus-5*",
      # ZONA PHI: modelo REGIONAL, e a regiao esta FIXA no ARN — nao e' `*` como as duas
      # linhas acima. Aquelas usam `*` porque um perfil `global.*` roteia entre regioes por
      # desenho e restringir a regiao ali quebraria a invocacao de forma intermitente. Aqui e'
      # o oposto: a regiao presa em `sa-east-1` e' a propria garantia de residencia, e um
      # `*` desfaria o que este provedor existe para dar.
      "arn:${data.aws_partition.current.partition}:bedrock:sa-east-1::foundation-model/${var.phi_model_id}",
    ]
  }
}

resource "aws_iam_role_policy" "task_bedrock" {
  name   = "${local.name}-bedrock"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_bedrock.json
}
