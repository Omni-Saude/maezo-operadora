# ---------------------------------------------------------------------------
# Chave de ASSINATURA DE IMAGEM (cosign `awskms://`).
#
# POR QUE CHAVE, E NAO KEYLESS (Sigstore/Fulcio), que e' o que o `cd.yml` declara:
#   o portao nao e' "assinar", e' "VERIFICAR NO DEPLOY". Verificacao keyless
#   precisa ALCANCAR Fulcio/Rekor no minuto da verificacao, e o egresso deste
#   ambiente e' allowlist de /32 (o SG do portal tem 14 regras nominais; nao ha
#   internet generica). Uma assinatura KMS verifica com a chave publica da
#   PROPRIA CONTA — `kms:Verify` pelo endpoint do KMS, que o ambiente ja alcanca.
#   E o caminho keyless do `cd.yml` nunca rodou: ele esta atras do gate
#   `AWS_ENABLED`, ausente por desenho. "Declarado" la e' aspiracao, nao operacao.
#
# CONDICOES DA DIRETORIA, cumpridas aqui:
#   - assimetrica, `SIGN_VERIFY` apenas, mesma conta e regiao;
#   - ASSINAR so' pela role do CI (statement dedicado + deny explicito);
#   - VERIFICAR liberado no restante da conta (as roles de deploy, por IAM);
#   - ALIAS FIXO: KMS assimetrica NAO tem rotacao automatica. O alias e' o ponto
#     estavel que workflow e runbook citam, e a rotacao passa a ser um ATO
#     DECLARADO (criar chave nova, repontar o alias, RE-ASSINAR os digests ainda
#     em uso — assinatura antiga nao migra sozinha) — ver
#     `docs/runbooks/supply-chain-imagem.md`.
# ---------------------------------------------------------------------------

resource "aws_kms_key" "image_signing" {
  description = "Assinatura das imagens de aplicacao (cosign). SIGN_VERIFY assimetrica; assinar so pela role do CI."

  key_usage = "SIGN_VERIFY"
  # ECC P-256 + SHA256: o par que o cosign usa por padrao em `awskms://`, e o
  # mesmo formato de chave publica que um `cosign verify --key` consome sem
  # nenhuma infraestrutura externa.
  customer_master_key_spec = "ECC_NIST_P256"

  # Assimetrica nao aceita rotacao automatica (a AWS recusa o argumento como
  # true). A rotacao e' o ato declarado do runbook, nao um flag.
  deletion_window_in_days = 30
  enable_key_rotation     = false

  policy = data.aws_iam_policy_document.image_signing_key.json

  tags = merge(local.base_tags, { Name = "${local.name}-image-signing" })
}

# O ALIAS e' o nome estavel: workflow, runbook e comando de verificacao humana
# citam `alias/...`, nunca o key id. Rotacionar = repontar este alias.
resource "aws_kms_alias" "image_signing" {
  name          = "alias/${local.name}-image-signing"
  target_key_id = aws_kms_key.image_signing.key_id
}

data "aws_iam_policy_document" "image_signing_key" {
  # Sem esta statement a chave fica inadministravel (a AWS recusa a policy).
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

  # VERIFICAR e' liberado a conta (na pratica: as roles de deploy e quem opera o
  # terraform), delegando o controle fino ao IAM. Verificacao tem de ser BARATA —
  # um portao que so' o CI consegue conferir nao e' conferivel por ninguem.
  statement {
    sid    = "AllowVerifyByAccount"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${var.aws_account_id}:root"]
    }
    actions = [
      "kms:Verify",
      "kms:GetPublicKey",
      "kms:DescribeKey",
    ]
    resources = ["*"]
  }

  # ASSINAR: so' a role do CI.
  statement {
    sid    = "AllowSignByCIRole"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.github_supply_chain.arn]
    }
    actions = [
      "kms:Sign",
      "kms:Verify",
      "kms:GetPublicKey",
      "kms:DescribeKey",
    ]
    resources = ["*"]
  }

  # E NINGUEM MAIS assina — nem administrador. Deny por condicao de
  # `aws:PrincipalArn`, e nao `NotPrincipal`: numa role assumida o NotPrincipal
  # nao casa com o ARN da role e o deny acabaria batendo na propria role do CI.
  # Administrador que precise assinar fora do CI muda esta policy por PR, que e'
  # exatamente o registro que se quer.
  statement {
    sid    = "DenySignToEveryoneElse"
    effect = "Deny"
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    actions   = ["kms:Sign"]
    resources = ["*"]

    condition {
      test     = "ArnNotEquals"
      variable = "aws:PrincipalArn"
      values   = [aws_iam_role.github_supply_chain.arn]
    }
  }
}

output "image_signing_key_alias" {
  description = <<-EOT
    Alias FIXO da chave de assinatura de imagem. E' o que vai em
    `cosign verify --key awskms:///alias/<este nome>` — ver
    `docs/runbooks/supply-chain-imagem.md`.
  EOT
  value       = aws_kms_alias.image_signing.name
}

output "image_signing_key_arn" {
  description = "ARN da chave (o alias e' o ponto estavel; este ARN muda se a chave for rotacionada)."
  value       = aws_kms_key.image_signing.arn
}
