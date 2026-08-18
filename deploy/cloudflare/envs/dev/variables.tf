variable "account_id" {
  description = "ID da conta Cloudflare. Não é segredo; descoberto uma vez com o token."
  type        = string
}

variable "zone_id" {
  description = "ID da zona `austa.com.br`. Não é segredo."
  type        = string
}

variable "hostname" {
  description = "Hostname público completo. Ex.: maezo-dev.austa.com.br"
  type        = string
}

variable "tunnel_id" {
  description = <<-EOT
    ID do túnel cloudflared (UUID). Não é segredo — o segredo é o TOKEN, que vive no
    Secrets Manager da AWS e nunca passa por aqui nem pelo state.
  EOT
  type        = string
}

variable "destino_interno" {
  description = <<-EOT
    O serviço que o túnel entrega. Padrão: o Cockpit/Tasklist do CIB Seven, resolvido
    pelo Cloud Map dentro da VPC — é onde o médico auditor decide a tarefa de verdade.
  EOT
  type        = string
  default     = "http://cibseven.maezo-operadora-dev.internal:8080"
}

variable "emails_autorizados" {
  description = <<-EOT
    Quem pode entrar, por e-mail nominal. Lista vazia é PROIBIDA de propósito: uma
    aplicação Access sem política de inclusão é um subdomínio público com etapa extra.

    Nominal em vez de domínio inteiro porque este ambiente alcança o engine de
    processos com o usuário demo ainda ativo — enquanto isso for verdade, "todo mundo
    de @austa.com.br" é uma superfície maior do que alguém pretende conceder.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.emails_autorizados) > 0
    error_message = "Informe ao menos um e-mail — uma aplicação Access sem inclusão não protege nada."
  }

  validation {
    condition     = alltrue([for e in var.emails_autorizados : can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", e))])
    error_message = "Todo item deve ser um e-mail válido."
  }
}

variable "sessao_duracao" {
  description = <<-EOT
    Quanto tempo a sessão do Access dura antes de pedir o código de novo. `24h` é o
    default do Cloudflare; aqui é mais curto porque a aplicação dá acesso
    administrativo ao motor de processos.
  EOT
  type        = string
  default     = "8h"
}

variable "nome_aplicacao" {
  description = "Nome exibido na tela de login e no painel do Zero Trust."
  type        = string
  default     = "MAEZO Operadora — Cockpit (dev)"
}
