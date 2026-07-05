terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  backend "s3" {
    # bucket  = "amh-terraform-state-sa-east-1"
    # key     = "maezo/prod-amh-sa-east-1/terraform.tfstate"
    # region  = "sa-east-1"
    # encrypt = true
    # dynamodb_table = "amh-terraform-locks"
  }
}
