# ---------------------------------------------------------------------------
# PACOTES DE COMPORTAMENTO DOS AGENTES (ADR-0067 fase 3) — PROD
# ---------------------------------------------------------------------------
# Mesmo desenho do dev: pacote IMUTAVEL por sha256 (`<agente>/pacotes/<sha256>.json`) e um PONTEIRO por
# ambiente (`<agente>/prod/ativo.json`), lido pelo receptor a cada 60 s (`maezo.runtime.pacote_do_agente`).
#
# DIFERENCA DELIBERADA DO DEV: aqui NAO existe a role OIDC de publicacao do GitHub. Em prod o pacote nao e'
# publicado pela esteira: o pacote JA' validado no dev (mesmo sha256) e' COPIADO do bucket de artefatos do dev
# para o de prod, e mover `prod/ativo.json` e' um ato humano de promocao (runbook de cutover). Uma role que a
# `main` assumisse para escrever o ponteiro de PROD seria deploy automatico de comportamento clinico em
# producao — decisao que ninguem tomou.
#
# Desligado (`pacotes_dos_agentes = false`, o default) nada disto existe e a task fica byte a byte igual.

variable "pacotes_dos_agentes" {
  description = "Liga a leitura dos pacotes de comportamento dos agentes (prefixo no bucket de artefatos de prod) pelo receptor e pela task generica (ADR-0067 fase 3). Sem role de publicacao em prod."
  type        = bool
  default     = false
}

# O BUCKET e' o de artefatos de pipeline do foundation de prod (remote state), sob `maezo-operadora/agentes/`.
locals {
  pacotes_bucket  = local.bucket_artefatos
  pacotes_prefixo = "${var.prefixo_fonte_build}/agentes"
  pacotes_dos_agentes_env = var.pacotes_dos_agentes ? [
    { name = "MAEZO_PACOTES_URI", value = "s3://${local.pacotes_bucket}/${local.pacotes_prefixo}" },
    { name = "MAEZO_PACOTES_AMBIENTE", value = local.env },
  ] : []
}

# Leitura: receptor e task generica. So' o prefixo dos pacotes, so' GetObject.
data "aws_iam_policy_document" "pacotes_leitura" {
  count = var.pacotes_dos_agentes ? 1 : 0
  statement {
    sid       = "LePacotesDosAgentes"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${data.aws_s3_bucket.fonte.arn}/${local.pacotes_prefixo}/*"]
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

output "pacotes_dos_agentes" {
  description = "Bucket/prefixo dos pacotes em prod (o ponteiro `<agente>/prod/ativo.json` e' movido por pessoa, no runbook)."
  value = var.pacotes_dos_agentes ? {
    uri = "s3://${local.pacotes_bucket}/${local.pacotes_prefixo}"
  } : null
}
