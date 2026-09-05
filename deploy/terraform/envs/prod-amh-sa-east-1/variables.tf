variable "aws_region" {
  description = "AWS region."
  type        = string
  default     = "sa-east-1"
}

variable "aws_account_id" {
  description = "AWS account ID. No default — set in tfvars."
  type        = string
}

variable "owner_email" {
  description = "Owner email for mandatory tagging."
  type        = string
}

variable "cost_center" {
  description = "Cost center tag."
  type        = string
  default     = "maezo-prod-amh"
}

variable "github_org" {
  type    = string
  default = "Omni-Saude"
}

variable "github_repo" {
  type    = string
  default = "Maezo-Healthcare-Plan"
}

variable "shared_vpc_name" {
  description = "Name tag of the shared VPC (amh-data-platform prod)."
  type        = string
  default     = "amh-data-platform-vpc-prod"
}

variable "create_eks_cluster" {
  description = <<-EOT
    If true, provision a dedicated EKS cluster for this tenant (ADR-0004 isolation).
    If false, reference the shared amh-data-platform cluster.
    Default: true for prod-amh to ensure full tenant isolation.
  EOT
  type        = bool
  default     = true
}

variable "eks_cluster_name" {
  description = "Existing EKS cluster name when create_eks_cluster=false."
  type        = string
  default     = ""
}

variable "eks_node_security_group_ids" {
  description = "Security group IDs of EKS node groups."
  type        = list(string)
  default     = []
}

variable "eks_node_role_arns" {
  description = "IAM role ARNs of EKS node groups."
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------------------
# Guardrails de custo (gap B-02-b / decisao do dono R-045).
# SEM DEFAULT de proposito: enquanto financas nao informar o teto e o e-mail de
# alerta, `terraform plan` deste ambiente falha FECHADO. Nao acrescente default.
# ---------------------------------------------------------------------------
variable "monthly_budget_amount" {
  description = "Teto mensal de custo em USD para prod-amh. Sem default (R-045): informe via tfvars."
  type        = number
}

variable "cost_alert_email" {
  description = "E-mail que recebe alertas de orcamento e de anomalia de custo. Sem default."
  type        = string
}
