variable "repository_name" {
  description = "ECR repository name."
  type        = string
  default     = "maezo-agent"
}

variable "image_tag_mutability" {
  description = "MUTABLE or IMMUTABLE. Use IMMUTABLE in prod for provenance guarantees."
  type        = string
  default     = "IMMUTABLE"
}

variable "kms_key_arn" {
  description = "KMS key ARN for ECR encryption. Leave empty to use AWS-managed key."
  type        = string
  default     = ""
}

variable "push_role_arns" {
  description = "IAM role ARNs allowed to push images (e.g., GitHub Actions deploy role)."
  type        = list(string)
  default     = []
}

variable "pull_role_arns" {
  description = "IAM role ARNs allowed to pull images (e.g., EKS node role)."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Additional tags."
  type        = map(string)
  default     = {}
}
