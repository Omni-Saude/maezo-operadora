variable "name_prefix" {
  description = "Prefix for secret names (e.g. 'maezo')."
  type        = string
  default     = "maezo"
}

variable "environment" {
  description = "Environment name (staging, prod-amh)."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key ARN used to encrypt secrets."
  type        = string
}

variable "tags" {
  description = "Additional tags."
  type        = map(string)
  default     = {}
}
