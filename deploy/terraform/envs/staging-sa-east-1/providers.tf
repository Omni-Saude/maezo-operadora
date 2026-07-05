provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Platform    = "maezo"
      ManagedBy   = "terraform"
      Repo        = "Maezo-Healthcare-Plan"
      environment = "staging"
    }
  }
}
