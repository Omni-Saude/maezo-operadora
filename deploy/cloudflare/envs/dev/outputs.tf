output "url" {
  description = "Onde o time entra. Pede codigo por e-mail antes de qualquer byte chegar ao engine."
  value       = "https://${var.hostname}"
}

output "quem_entra" {
  description = "Lista efetiva de e-mails autorizados, para conferencia."
  value       = var.emails_autorizados
}

output "destino" {
  description = "Servico interno que o tunel entrega."
  value       = var.destino_interno
}
