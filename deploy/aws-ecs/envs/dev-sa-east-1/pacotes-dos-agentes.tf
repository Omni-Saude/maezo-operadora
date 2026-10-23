# ---------------------------------------------------------------------------
# PACOTES DE COMPORTAMENTO DOS AGENTES (ADR-0067 fase 3)
# ---------------------------------------------------------------------------
# O comportamento de um agente (configuracao, roteador, textos) sai da imagem: um pacote IMUTAVEL por
# sha256 (`<agente>/pacotes/<sha256>.json`) e um PONTEIRO por ambiente (`<agente>/<ambiente>/ativo.json`).
# O receptor (e o simulador, que e' o receptor sem HTTP) le' o ponteiro a cada 60 s
# (`maezo.runtime.pacote_do_agente`). Mudar o agente = publicar pacote + mover ponteiro: sem build e sem
# deploy do Maezo. Quem escreve e' SO' a esteira (`agentes-pacote.yml`, role OIDC abaixo, so' da main).
#
# Desligado (`pacotes_dos_agentes = false`, o default) nada disto existe e a task fica byte a byte igual.

variable "pacotes_dos_agentes" {
  description = "Liga o bucket dos pacotes de comportamento dos agentes, a leitura pelo receptor/simulador e a role de publicacao do GitHub (ADR-0067 fase 3)."
  type        = bool
  default     = false
}

locals {
  pacotes_bucket  = "${local.name}-agentes-${var.aws_account_id}"
  pacotes_prefixo = "agentes"
  pacotes_dos_agentes_env = var.pacotes_dos_agentes ? [
    { name = "MAEZO_PACOTES_URI", value = "s3://${local.pacotes_bucket}/${local.pacotes_prefixo}" },
    { name = "MAEZO_PACOTES_AMBIENTE", value = "dev" },
  ] : []
}

resource "aws_s3_bucket" "pacotes" {
  count         = var.pacotes_dos_agentes ? 1 : 0
  bucket        = local.pacotes_bucket
  force_destroy = false
  tags          = merge(local.base_tags, { Name = local.pacotes_bucket, proposito = "pacotes-dos-agentes" })
}

resource "aws_s3_bucket_ownership_controls" "pacotes" {
  count  = var.pacotes_dos_agentes ? 1 : 0
  bucket = aws_s3_bucket.pacotes[0].id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "pacotes" {
  count                   = var.pacotes_dos_agentes ? 1 : 0
  bucket                  = aws_s3_bucket.pacotes[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versionamento: o historico dos ponteiros (quem apontou o que, quando) e a protecao contra sobrescrita.
resource "aws_s3_bucket_versioning" "pacotes" {
  count  = var.pacotes_dos_agentes ? 1 : 0
  bucket = aws_s3_bucket.pacotes[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "pacotes" {
  count  = var.pacotes_dos_agentes ? 1 : 0
  bucket = aws_s3_bucket.pacotes[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Leitura: receptor, task generica e simulador. So' o prefixo dos pacotes, so' GetObject.
data "aws_iam_policy_document" "pacotes_leitura" {
  count = var.pacotes_dos_agentes ? 1 : 0
  statement {
    sid       = "LePacotesDosAgentes"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.pacotes[0].arn}/${local.pacotes_prefixo}/*"]
  }
}

resource "aws_iam_role_policy" "pacotes_leitura_task" {
  count  = var.pacotes_dos_agentes ? 1 : 0
  name   = "pacotes-dos-agentes-leitura"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.pacotes_leitura[0].json
}

resource "aws_iam_role_policy" "pacotes_leitura_receptor" {
  count  = var.pacotes_dos_agentes && var.recipient_vault_enabled ? 1 : 0
  name   = "pacotes-dos-agentes-leitura"
  role   = aws_iam_role.webhook_receiver_task[0].id
  policy = data.aws_iam_policy_document.pacotes_leitura[0].json
}

resource "aws_iam_role_policy" "pacotes_leitura_simulador" {
  count  = var.pacotes_dos_agentes ? 1 : 0
  name   = "pacotes-dos-agentes-leitura"
  role   = aws_iam_role.simulador_task.id
  policy = data.aws_iam_policy_document.pacotes_leitura[0].json
}

# Escrita: SO' a esteira da main (`.github/workflows/agentes-pacote.yml`). Publica pacote e move o ponteiro
# do dev; nao le' nada alem do proprio prefixo, nao apaga.
data "aws_iam_policy_document" "github_agentes_trust" {
  count = var.pacotes_dos_agentes ? 1 : 0
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.github_repo_sub}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_agentes" {
  count                = var.pacotes_dos_agentes ? 1 : 0
  name                 = "${local.name}-github-agentes"
  description          = "GitHub Actions (Omni-Saude/maezo-operadora, main): publica pacotes de comportamento dos agentes e move o ponteiro do dev. Sem deploy."
  assume_role_policy   = data.aws_iam_policy_document.github_agentes_trust[0].json
  max_session_duration = 3600
  tags                 = merge(local.base_tags, { Name = "${local.name}-github-agentes" })
}

data "aws_iam_policy_document" "github_agentes" {
  count = var.pacotes_dos_agentes ? 1 : 0
  statement {
    sid     = "PublicaPacotesEMoveOPonteiroDoDev"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:PutObject"]
    resources = [
      "${aws_s3_bucket.pacotes[0].arn}/${local.pacotes_prefixo}/*/pacotes/*",
      "${aws_s3_bucket.pacotes[0].arn}/${local.pacotes_prefixo}/*/dev/ativo.json",
    ]
  }
}

resource "aws_iam_role_policy" "github_agentes" {
  count  = var.pacotes_dos_agentes ? 1 : 0
  name   = "publica-pacotes-dos-agentes"
  role   = aws_iam_role.github_agentes[0].id
  policy = data.aws_iam_policy_document.github_agentes[0].json
}

output "pacotes_dos_agentes" {
  description = "Bucket dos pacotes e a role que a esteira `agentes-pacote.yml` assume (variavel de repositorio AWS_AGENTES_ROLE_ARN)."
  value = var.pacotes_dos_agentes ? {
    uri      = "s3://${local.pacotes_bucket}/${local.pacotes_prefixo}"
    role_arn = aws_iam_role.github_agentes[0].arn
  } : null
}
