// Destinatario dos alarmes do plano staff em dev (topico `maezo-operadora-dev-staff-alerts`, task-staff-ops.tf).
// Decisao do dono em 08/10/2026, depois do incidente do material vencido: o alarme
// `staff-job-2-falhas-seguidas` foi para ALARM as 04:52Z para um topico SEM assinante. A assinatura
// por e-mail so passa a valer depois que o destinatario confirma o link que a AWS envia.
//
// TODO(decisao da operacao, Operacao_Decisoes_Escalonamento_2026-10-20.xlsx, linha 3): trocar pelo
// endereco da caixa compartilhada/lista da operacao e CONFIRMAR a assinatura SNS no mesmo dia.
// O valor vigente (decisao de 08/10) fica abaixo, comentado, ate a confirmacao:
// staff_alerts_email = "lsilva@austa.com.br"
//
// MARCADOR DE PROPOSITO: nao e e-mail, entao a validacao de `staff_alerts_email` (task-staff-ops.tf)
// RECUSA o plan. Este arquivo nao pode ser aplicado (nem mergeado) antes de a operacao decidir.
staff_alerts_email = "PREENCHER-DESTINATARIO-DOS-ALARMES"

// Alarmes do escalonamento humano e da retomada (escalation-alarms.tf, DL-0140): 5 alarmes, destino o
// topico acima. Ligar e decisao da operacao (planilha, linha 4); so depois do destinatario confirmado.
escalation_alarms_enabled = true

// Inbox duravel de avisos de escalonamento (service-notifications-inbox.tf; teto 1). Ligar e decisao da
// operacao (planilha, linha 5); antes, conferir (so leitura) a migracao 0015 no banco do dev.
notifications_inbox_desired_count = 1
