// Destinatario dos alarmes do plano staff em dev (topico `maezo-operadora-dev-staff-alerts`, task-staff-ops.tf).
// Decisao do dono em 08/10/2026, depois do incidente do material vencido: o alarme
// `staff-job-2-falhas-seguidas` foi para ALARM as 04:52Z para um topico SEM assinante. A assinatura
// por e-mail so passa a valer depois que o destinatario confirma o link que a AWS envia.
staff_alerts_email = "lsilva@austa.com.br"
