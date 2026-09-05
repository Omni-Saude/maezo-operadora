provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Platform    = "maezo"
      ManagedBy   = "terraform"
      Repo        = "Maezo-Healthcare-Plan"
      environment = "prod-amh"
      tenant      = "amh"
      DataClass   = "PHI"
    }
  }
}

# Budgets e Cost Explorer sao servicos globais ancorados em us-east-1: o modulo
# `cost-guardrails` recebe este provider aliado no slot padrao (gap B-02-b / R-045).
# O restante do ambiente continua inteiramente em sa-east-1 (residencia LGPD).
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Platform    = "maezo"
      ManagedBy   = "terraform"
      Repo        = "Maezo-Healthcare-Plan"
      environment = "prod-amh"
    }
  }
}
