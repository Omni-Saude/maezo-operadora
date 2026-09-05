variable "aws_region" {
  description = "AWS region."
  type        = string
  default     = "sa-east-1"
}

variable "aws_account_id" {
  description = "AWS account ID. Used in IAM ARN construction. No default — set in tfvars."
  type        = string
}

variable "owner_email" {
  description = "Owner email for mandatory tagging."
  type        = string
}

variable "cost_center" {
  description = "Cost center tag."
  type        = string
  default     = "maezo-staging"
}

variable "github_org" {
  description = "GitHub organization."
  type        = string
  default     = "Omni-Saude"
}

variable "github_repo" {
  description = "GitHub repository name."
  type        = string
  default     = "Maezo-Healthcare-Plan"
}

variable "shared_vpc_name" {
  description = "Name tag of the shared VPC (amh-data-platform)."
  type        = string
  default     = "amh-data-platform-vpc-staging"
}

variable "eks_cluster_name" {
  description = "Name of the existing EKS cluster to reference."
  type        = string
}

variable "eks_node_security_group_ids" {
  description = "Security group IDs of EKS node groups (allowed to reach Aurora)."
  type        = list(string)
  default     = []
}

variable "eks_node_role_arns" {
  description = "IAM role ARNs of EKS node groups (allowed to pull ECR images)."
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------------------
# Guardrails de custo (gap B-02-b / decisao do dono R-045).
# SEM DEFAULT de proposito: enquanto financas nao informar o teto e o e-mail de
# alerta, `terraform plan` deste ambiente falha FECHADO. Nao acrescente default.
# ---------------------------------------------------------------------------
variable "monthly_budget_amount" {
  description = "Teto mensal de custo em USD para staging. Sem default (R-045): informe via tfvars."
  type        = number
}

variable "cost_alert_email" {
  description = "E-mail que recebe alertas de orcamento e de anomalia de custo. Sem default."
  type        = string
}
