variable "aws_region" {
  description = "Regiao. sa-east-1 por residencia de dado (LGPD) — o mesmo motivo que prende a plataforma de dados aqui."
  type        = string
  default     = "sa-east-1"
}

variable "aws_account_id" {
  description = "Conta de dados da AMH. Usado como trava em provider.allowed_account_ids e na montagem de ARNs."
  type        = string
  default     = "203312548462"
}

# ---------------------------------------------------------------------------
# Rede e banco — recursos da PLATAFORMA, lidos por data source. Nunca criados aqui.
# ---------------------------------------------------------------------------

variable "vpc_name" {
  description = "Name tag da VPC da plataforma (infrastructure/modules/network do amh-data-platform)."
  type        = string
  default     = "amh-data-vpc-dev"
}

variable "aurora_cluster_identifier" {
  description = <<-EOT
    Cluster Aurora compartilhado. Decisao do dono (2026-08-13): reaproveitar em vez de
    subir um proprio, porque as roles `maezo_app` e `cibseven_app` JA existem nele com
    senha gravada desde 2026-06-04 — foram criadas exatamente para isto.
  EOT
  type        = string
  default     = "amh-aurora-hapi-dev"
}

variable "aurora_database_name" {
  description = <<-EOT
    Database alvo das migrations do maezo. `applications/maezo/migrations/README.md` (repo da
    plataforma) descreve os schemas `maezo` e `cibseven` dentro de um database `maezo`.
    ATENCAO: a existencia desse database NAO foi verificada — nao ha rota da minha maquina ate
    a subnet privada. A task de migrations e' o primeiro ponto onde isso aparece; se falhar com
    "database maezo does not exist", rode o bootstrap descrito no runbook.
  EOT
  type        = string
  default     = "maezo"
}

variable "aurora_bootstrap_database" {
  description = <<-EOT
    Database usado apenas para ABRIR a conexao da task de bootstrap, antes de o
    database do maezo existir. `hapi` e' o unico que o cluster tem ao nascer
    (DatabaseName do modulo aurora-cluster da plataforma). Nada e' criado nele.
  EOT
  type        = string
  default     = "hapi"
}

variable "aurora_app_secret_name" {
  description = "Secret com usuario/senha da role de aplicacao do maezo (populado fora do Terraform)."
  type        = string
  default     = "amh-aurora-hapi-dev/maezo_app"
}

variable "cibseven_app_secret_name" {
  description = "Secret com usuario/senha da role do engine BPMN."
  type        = string
  default     = "amh-aurora-hapi-dev/cibseven_app"
}

variable "hapi_internal_alb_name" {
  description = "ALB interno do HAPI FHIR — por onde o maezo consome FHIR sem sair para a internet."
  type        = string
  default     = "amh-hapi-fhir-dev-int"
}

# ---------------------------------------------------------------------------
# Aplicacao
# ---------------------------------------------------------------------------

variable "tenant_id" {
  description = "Tenant desta instancia. Instancia-por-cliente e' principio inegociavel do produto (README, principio 3)."
  type        = string
  default     = "amh"
}

variable "image_tag" {
  description = <<-EOT
    Tag da imagem no ECR. NAO use `latest` fora de um bootstrap: o service so troca de imagem
    quando a task definition muda, e `latest` torna impossivel saber o que esta rodando.
    A CD passa o SHA curto do commit.
  EOT
  type        = string
  default     = "bootstrap"
}

variable "log_retention_days" {
  description = "Retencao dos log groups. 30 dias em dev; auditoria de verdade nao mora em CloudWatch (ADR de retencao de 5 anos)."
  type        = number
  default     = 30
}

variable "worker_cpu" {
  description = "CPU do worker-runtime (256 = 0.25 vCPU)."
  type        = number
  default     = 512
}

variable "worker_memory" {
  description = "Memoria do worker-runtime em MB."
  type        = number
  default     = 1024
}

variable "worker_desired_count" {
  description = <<-EOT
    Quantas tasks do worker-runtime (o harness registra os 16 workers BPMN por task).
    Nasce em ZERO pelo mesmo motivo do engine: sem o banco alcancavel o harness falha
    fechado e registra zero workers. Suba para 1 depois que as migrations passarem E o
    engine estiver de pe — o worker precisa dos dois.
  EOT
  type        = number
  default     = 0
}

variable "cibseven_desired_count" {
  description = <<-EOT
    Quantas tasks do engine BPMN. Nasce em ZERO de proposito: enquanto o SG deste stack
    nao estiver liberado no Aurora (PR no amh-data-platform — ver README), o engine nao
    conecta no banco e entraria em crash-loop cobrando Fargate sem entregar nada. Suba
    para 1 DEPOIS que a task de migrations rodar limpa.
    Replica unica: o engine faz aquisicao de job com lock em banco; duas replicas em dev
    so multiplicam contencao.
  EOT
  type        = number
  default     = 0
}

variable "agent_runtime_mode" {
  description = <<-EOT
    `production` liga os gates fail-closed (checkpoint duravel, assinatura de Agent Card,
    recusa de MAEZO_SPEC_DIR — Wave-1 Q-6). `local` afrouxa. Em qualquer ambiente da AWS isto
    e' `production`: um ambiente de nuvem rodando em modo local seria uma mentira silenciosa
    sobre quais controles estao ativos.
  EOT
  type        = string
  default     = "production"

  validation {
    condition     = contains(["production", "local"], var.agent_runtime_mode)
    error_message = "agent_runtime_mode deve ser 'production' ou 'local'."
  }
}
