provider "aws" {
  region = var.aws_region

  # Trava de conta INVERTIDA em relacao ao root ECS: aqui o alvo legitimo e' a conta de
  # GESTAO, porque e' onde a instancia do Identity Center existe. Sem esta trava, um
  # apply com o profile `maezo-data` carregado falharia com erro obscuro de API em vez
  # de dizer que a conta esta errada.
  allowed_account_ids = [var.management_account_id]

  default_tags {
    tags = {
      Platform  = "maezo-operadora"
      ManagedBy = "terraform"
      Repo      = "Omni-Saude/maezo-operadora"
      Escopo    = "identity-center"
    }
  }
}
