variable "name_prefix" {
  description = "Resource name prefix (e.g. 'maezo')."
  type        = string
}

variable "environment" {
  description = "Environment name (staging, prod-amh)."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID where Aurora lives."
  type        = string
}

variable "db_subnet_ids" {
  description = "Private data-tier subnet IDs for the DB subnet group."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security group IDs (EKS nodes) allowed to reach Aurora on port 5432."
  type        = list(string)
}

variable "database_name" {
  description = "Default database name created in the cluster."
  type        = string
  default     = "maezo"
}

variable "master_username" {
  description = "Master username. Password managed by Secrets Manager (manage_master_user_password=true)."
  type        = string
  default     = "maezo"
}

variable "backup_retention_days" {
  description = "Days of automated backups to retain."
  type        = number
  default     = 7
}

variable "enable_deletion_protection" {
  description = "Enable deletion protection. Must be false to destroy."
  type        = bool
  default     = true
}

variable "serverless" {
  description = "Use Aurora Serverless v2 scaling. True = scale-to-zero friendly (staging). False = provisioned (prod)."
  type        = bool
  default     = true
}

variable "serverless_min_acu" {
  description = "Minimum Aurora Capacity Units for Serverless v2."
  type        = number
  default     = 0.5
}

variable "serverless_max_acu" {
  description = "Maximum Aurora Capacity Units for Serverless v2."
  type        = number
  default     = 4.0
}

variable "instance_class" {
  description = "DB instance class when serverless=false."
  type        = string
  default     = "db.r6g.large"
}

variable "reader_count" {
  description = "Number of Aurora reader instances."
  type        = number
  default     = 1
}

# SC-05 / R-026 (OWNER-DECISIONS-REGISTER, APROVADO-APOS-REVISAO-HUMANA): RDS Proxy e' o caminho
# escolhido pelo dono a partir do segundo tenant real; pgbouncer fica formalmente descartado (o
# modo transaction do pgbouncer quebra prepared statements, que `asyncpg` — todos os quatro
# `create_pool` desta arvore, SC-05's bloco em `deploy/helm/maezo-tenant/values.yaml` — usa por
# padrao). DEFAULT `false`: nenhum recurso AWS e' criado por esta variavel ate alguem ligar. O
# ligamento real depende de D6-04/D13-03 (credenciais AWS, HUMAN-GATED) e do segundo tenant.
variable "enable_rds_proxy" {
  description = "Provision an RDS Proxy in front of this Aurora cluster (SC-05/R-026). false = no proxy resources created at all; DSNs keep pointing at the cluster endpoint directly."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Additional tags to merge onto all resources."
  type        = map(string)
  default     = {}
}
