variable "aws_region" {
  description = <<-EOT
    Regiao das APIs do Identity Center. E' a regiao onde a INSTANCIA foi criada
    (sa-east-1, medido) — nao ha escolha aqui: chamar sso-admin em outra regiao nao
    encontra a instancia.
  EOT
  type        = string
  default     = "sa-east-1"
}

variable "management_account_id" {
  description = "Conta de gestao da organizacao o-4idsrpx8v8, dona da instancia do Identity Center."
  type        = string
  default     = "169446931765"
}

variable "data_account_id" {
  description = <<-EOT
    Conta onde o ambiente maezo roda e para onde os acessos sao atribuidos. E' TAMBEM a
    conta que guarda os 11,45M de recursos FHIR — motivo pelo qual as politicas abaixo
    sao escritas por recurso e nao por servico.
  EOT
  type        = string
  default     = "203312548462"
}

variable "cluster_name" {
  description = "Cluster ECS do ambiente. Usado para restringir as acoes de ECS por ARN."
  type        = string
  default     = "maezo-operadora-dev"
}

variable "session_duration" {
  description = <<-EOT
    Duracao da sessao do permission set, em ISO-8601. PT8H = um dia de trabalho: curto o
    bastante para que uma estacao esquecida aberta expire no mesmo dia, longo o bastante
    para nao reautenticar no meio de uma investigacao. O `AdministratorAccess` existente
    usa PT8H, entao isto tambem evita duas reguas de sessao na mesma instancia.
  EOT
  type        = string
  default     = "PT8H"
}
