variable "name_prefix" {
  description = "Resource name prefix (e.g. 'maezo')."
  type        = string
}

variable "environment" {
  description = "Environment name (staging, prod-amh)."
  type        = string
}

variable "aws_region" {
  description = "AWS region for AMP and AMG workspaces (must be sa-east-1 for LGPD data residency)."
  type        = string
  default     = "sa-east-1"
}

variable "eks_oidc_provider_url" {
  description = "OIDC provider URL for the EKS cluster (used to create IRSA trust policy for the OTel collector)."
  type        = string
}

variable "eks_oidc_provider_arn" {
  description = "OIDC provider ARN for the EKS cluster (IRSA trust policy)."
  type        = string
}

variable "collector_namespace" {
  description = "Kubernetes namespace where the OTel collector runs (used for IRSA trust policy)."
  type        = string
  default     = "observability"
}

variable "collector_service_account" {
  description = "Kubernetes ServiceAccount name for the OTel collector pod (used for IRSA trust policy)."
  type        = string
  default     = "otel-collector"
}

variable "kms_key_arn" {
  description = "KMS key ARN for encrypting AMP workspace data at rest."
  type        = string
  default     = ""   # AMP uses AWS-managed key if empty
}

variable "alert_rules_content" {
  description = <<-EOT
    Content of the Prometheus alert rules YAML to upload to AMP.
    Pass via file() from the calling env root, e.g.:
      alert_rules_content = file("$${path.root}/../../../../deploy/observability/alert-rules.yaml")
    Defaults to a minimal valid placeholder so `terraform validate` passes in isolation.
  EOT
  type    = string
  default = <<-YAML
    groups:
      - name: placeholder
        rules: []
    YAML
}

variable "grafana_admin_group_id" {
  description = "AWS SSO / IAM Identity Center group ID for Grafana admin access. Leave empty to skip."
  type        = string
  default     = ""
}

variable "grafana_viewer_group_id" {
  description = "AWS SSO / IAM Identity Center group ID for Grafana viewer access. Leave empty to skip."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags to merge onto all resources."
  type        = map(string)
  default     = {}
}
