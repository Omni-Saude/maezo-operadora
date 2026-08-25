output "url_cockpit" {
  description = "Cockpit/Tasklist do engine — onde o medico auditor decide."
  value       = "https://${var.hostname}"
}

output "url_canal_teste" {
  description = "Canal de Teste — lancar processo e ler evidencia, sem CLI."
  value       = "https://${var.hostname_canal}"
}

output "quem_entra" {
  description = "Regra efetiva de acesso, para conferencia depois do apply."
  value = {
    dominios = var.dominios_autorizados
    emails   = var.emails_autorizados
  }
}

output "destino" {
  description = "Servico interno que o tunel entrega."
  value       = var.destino_interno
}
