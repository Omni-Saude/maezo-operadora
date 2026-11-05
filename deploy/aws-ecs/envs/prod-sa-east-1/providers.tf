provider "aws" {
  region = var.aws_region

  # Trava de conta: este state so pode ser aplicado na conta de dados. Sem isto,
  # um `terraform apply` com o profile `default` carregado (usuario da conta
  # LEGADA 169446931765, ou o adm-dev da conta 203312548462 onde a Helena VIVE) criaria
  # toda a stack na conta errada, silenciosamente. Em prod a trava e' 441395443640.
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Platform  = "maezo-operadora"
      ManagedBy = "terraform"
      Repo      = "Omni-Saude/maezo-operadora"
      env       = "prod"
    }
  }
}
