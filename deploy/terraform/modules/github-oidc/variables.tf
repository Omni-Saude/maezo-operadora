variable "github_org" {
  description = "GitHub organization name (e.g. 'Omni-Saude')."
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name (e.g. 'Maezo-Healthcare-Plan')."
  type        = string
  default     = "Maezo-Healthcare-Plan"
}

variable "environment" {
  description = "Environment name used in role names and trust conditions (staging, prod-amh)."
  type        = string
}

variable "create_oidc_provider" {
  description = <<-EOT
    If true, creates the GitHub OIDC provider in this account.
    If false (default), uses a data source to reference an existing provider
    (e.g., already created by amh-data-platform bootstrap).
  EOT
  type        = bool
  default     = false
}

variable "ecr_repository_arns" {
  description = "ECR repository ARNs the deploy role may push to."
  type        = list(string)
  default     = []
}

variable "secrets_manager_arns" {
  description = "Secrets Manager secret ARNs the deploy role may read."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Additional tags."
  type        = map(string)
  default     = {}
}
