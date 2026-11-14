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
    # Sem `archive`: a Lambda do egresso Cognito (portal) nao foi portada para prod.
  }

  # State proprio no bucket de state de PROD, key exclusiva do maezo. O bucket so'
  # aceita objeto cifrado com a CMK `alias/amh-tfstate-prod` (Sid StateSoComACmk):
  # passe o ARN no init, senao o `encrypt = true` manda AES256 e o PUT e' NEGADO.
  #   terraform init -backend-config="kms_key_id=<arn de alias/amh-tfstate-prod>"
  # NUNCA apontar para o state do dev (amh-tfstate-dev): e' a Helena VIVA.
  backend "s3" {
    bucket       = "amh-tfstate-prod"
    key          = "envs/prod-sa-east-1/maezo-operadora/terraform.tfstate"
    region       = "sa-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
