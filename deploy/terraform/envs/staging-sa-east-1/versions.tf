terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote state backend — configure before first apply.
  # Bucket and key created by amh-data-platform bootstrap (shared S3 state bucket).
  backend "s3" {
    # bucket  = "amh-terraform-state-sa-east-1"   # set via -backend-config
    # key     = "maezo/staging-sa-east-1/terraform.tfstate"
    # region  = "sa-east-1"
    # encrypt = true
    # dynamodb_table = "amh-terraform-locks"
  }
}
