variable "create_cluster" {
  description = <<-EOT
    If false (default), the module uses data sources to reference an existing EKS
    cluster managed by amh-data-platform. Set to true to provision a dedicated
    cluster for Maezo (expected for isolated prod tenants per ADR-0004).
  EOT
  type        = bool
  default     = false
}

variable "existing_cluster_name" {
  description = "Name of the existing EKS cluster to reference when create_cluster=false."
  type        = string
  default     = ""
}

variable "name_prefix" {
  description = "Resource name prefix (e.g. 'maezo')."
  type        = string
}

variable "environment" {
  description = "Environment name (staging, prod-amh)."
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes version for the EKS cluster."
  type        = string
  default     = "1.31"
}

variable "subnet_ids" {
  description = "All subnet IDs for the EKS VPC config (public + private)."
  type        = list(string)
  default     = []
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for node groups."
  type        = list(string)
  default     = []
}

variable "endpoint_public_access" {
  description = "Whether to enable public API endpoint. Disable in prod."
  type        = bool
  default     = false
}

variable "public_access_cidrs" {
  description = "CIDR blocks allowed for public endpoint access."
  type        = list(string)
  default     = []
}

variable "node_desired_size" {
  description = "Desired number of worker nodes."
  type        = number
  default     = 2
}

variable "node_min_size" {
  description = "Minimum number of worker nodes."
  type        = number
  default     = 1
}

variable "node_max_size" {
  description = "Maximum number of worker nodes."
  type        = number
  default     = 6
}

variable "node_instance_types" {
  description = "EC2 instance types for node group."
  type        = list(string)
  default     = ["m6g.xlarge"]
}

variable "node_capacity_type" {
  description = "ON_DEMAND or SPOT."
  type        = string
  default     = "ON_DEMAND"
}

variable "tags" {
  description = "Additional tags to merge onto all resources."
  type        = map(string)
  default     = {}
}
