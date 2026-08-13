provider "aws" {
  region = var.aws_region

  # Trava de conta: este state so pode ser aplicado na conta de dados. Sem isto,
  # um `terraform apply` com o profile `default` carregado (usuario da conta
  # LEGADA 169446931765) criaria toda a stack na conta errada, silenciosamente.
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Platform  = "maezo-operadora"
      ManagedBy = "terraform"
      Repo      = "Omni-Saude/maezo-operadora"
      env       = "dev"
    }
  }
}
