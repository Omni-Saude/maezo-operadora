terraform {
  required_version = ">= 1.10"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
  }

  # State SEPARADO do stack AWS, de proposito. Sao dois dominios de confianca com
  # credenciais diferentes: um `terraform plan` do ambiente ECS nao deve exigir token
  # do Cloudflare, e vice-versa. Misturar os dois num state faz o portao de um virar
  # pre-requisito do outro.
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}
