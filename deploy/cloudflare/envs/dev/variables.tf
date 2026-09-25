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

variable "dominios_autorizados" {
  description = <<-EOT
    Domínios de e-mail que podem entrar. Qualquer pessoa com e-mail nesses domínios
    recebe um código de uso único e entra.

    Decisão do dono (18/08/2026): `austa.com.br` e `americashealth.co`.

    É mais largo do que e-mail nominal, e a consequência precisa estar escrita onde
    alguém a leia: enquanto o usuário `demo` do CIB Seven existir, quem passa pelo
    Access tem ADMINISTRAÇÃO do motor de processos. O Access é fronteira de
    identidade, não de autorização — reduzir isso é trabalho no engine, não aqui.

    Lista vazia é proibida: uma aplicação Access sem inclusão é um subdomínio público
    com uma etapa extra.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.dominios_autorizados) > 0
    error_message = "Informe ao menos um domínio — sem regra de inclusão o Access não protege nada."
  }

  validation {
    condition     = alltrue([for d in var.dominios_autorizados : can(regex("^[^@[:space:]]+[.][^@[:space:]]+$", d))])
    error_message = "Cada item deve ser um domínio (ex.: austa.com.br), sem @ e sem espaços."
  }
}

variable "emails_autorizados" {
  description = <<-EOT
    E-mails NOMINAIS extras, além dos domínios. Vazio por padrão — existe para o caso
    de alguém de fora dos domínios precisar entrar sem que o domínio inteiro entre.
  EOT
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for e in var.emails_autorizados : can(regex("^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$", e))])
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

variable "hostname_canal" {
  description = <<-EOT
    Hostname público do Canal de Teste — a página que o time de dados usa para lançar
    processo e ler a evidência, sem CLI.

    Hostname SEPARADO do Cockpit de propósito: são superfícies diferentes, e um dia a
    política de acesso pode divergir (o Canal é para o time de dados; o Cockpit é onde
    o médico auditor decide). Hoje as duas usam a mesma política.
  EOT
  type        = string
  default     = "maezo-teste-dev.austa.com.br"
}

variable "destino_canal" {
  description = "Serviço interno do Canal de Teste, resolvido pelo Cloud Map."
  type        = string
  default     = "http://canal-teste.maezo-operadora-dev.internal:8500"
}

variable "nome_aplicacao" {
  description = "Nome exibido na tela de login e no painel do Zero Trust."
  type        = string
  default     = "MAEZO Operadora — Cockpit (dev)"
}

variable "hostname_webhook" {
  description = "Hostname publico do webhook do WhatsApp (Meta). Sem Access: a Meta nao faz login; o receptor exige a assinatura HMAC."
  type        = string
  default     = "whatsapp-dev.austa.com.br"
}

variable "destino_webhook" {
  description = "Servico interno do receptor de webhook (Cloud Map), porta 8080."
  type        = string
  default     = "http://webhook-receiver.maezo-operadora-dev.internal:8080"
}
