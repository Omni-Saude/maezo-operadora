terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    # grafana provider omitted: AMG datasource wiring requires a two-phase bootstrap
    # (workspace URL only available after aws_grafana_workspace apply).
    # See the BLOCKED comment in main.tf for the manual datasource config steps.
  }
}
