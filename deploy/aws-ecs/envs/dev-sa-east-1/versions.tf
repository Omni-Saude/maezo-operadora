terraform {
  # >= 1.10: o backend usa `use_lockfile` (lock nativo do S3), que nao existe
  # antes disso. Mesmo piso do amh-data-platform — ver o comentario extenso em
  # infrastructure/envs/dev-sa-east-1/versions.tf sobre a tabela DynamoDB de
  # lock que a plataforma declarava e que NUNCA existiu.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # ~> 6.0 acompanha a plataforma. O stack EKS antigo deste repo pedia ~> 5.0;
      # conviver com duas majors do provider na mesma conta so gera confusao de
      # diff, entao este alinha com o vizinho que ja esta em producao.
      version = "~> 6.0"
    }
  }

  # Backend informado no init via -backend-config (ver README).
  #   bucket = "amh-tfstate-dev"
  #   key    = "envs/dev-sa-east-1/maezo-operadora/terraform.tfstate"
  #   region = "sa-east-1"
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}
