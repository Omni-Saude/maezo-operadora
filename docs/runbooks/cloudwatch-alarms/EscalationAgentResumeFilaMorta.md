# Alert Runbook: agent-resume-fila-morta (CloudWatch)

**Audience:** quem recebe o topico `staff_alerts` do dev
**Alarm source:** `deploy/aws-ecs/envs/dev-sa-east-1/escalation-alarms.tf` (`aws_cloudwatch_metric_alarm.agent_resume_fila_morta`)
**Event source:** `src/maezo/platform/integrations/agent_resume.py::run_resume_loop` (`EVENTO_RESUME_FILA_MORTA`)
**Estado:** desligado por padrao (`escalation_alarms_enabled = false`; so existe com o servico `agent-resume` ligado)

## 1. Gatilho

Pelo menos uma linha `agent_resume.fila_morta` no log group `/ecs/<name>/agent-resume` em 5
minutos. O evento e emitido DEPOIS que a fila morta confirmou o desvio para
`agents.events.process_completed.dlq` (com fato de auditoria); o campo `reason` e um codigo do
vocabulario fechado `BRIDGE_DLQ_REASONS` (`notifications_bridge.py`).

Motivos mais comuns da retomada:

| reason | significado |
|---|---|
| `resume_missing_anchor` / `resume_anchor_mismatch` / `resume_unknown_resultado` | evento malformado ou de outro tenant/conversa |
| `resume_phi_in_event` | o evento trazia variavel da Zona PHI e foi recusado sem leitura |
| `resume_history_mismatch` | o historico do motor nao confirma o evento (ou a nota esta vazia) |
| `resume_recipient_unavailable` | a custodia nao tem o numero da conversa (TTL ou pedido LGPD) |

## 2. O que significa

Nenhuma reentrega conserta esse evento. Nos casos `resume_history_mismatch` com nota vazia e
`resume_recipient_unavailable`, a equipe ja foi avisada (`escalation.notify_team`, DL-0066) antes
do desvio; nos demais, ninguem foi avisado porque o evento nao corresponde a um caso real
esperando retorno.

## 3. Primeiros passos (so leitura)

1. No CloudWatch Logs Insights do log group `/ecs/<name>/agent-resume`, contar
   `agent_resume.fila_morta` por `reason`.
2. Para `resume_history_mismatch` e `resume_recipient_unavailable`: conferir no inbox da equipe
   (`escalation_team_notice`) que o aviso do caso existe.

## 4. Escalada

Volume repetido do mesmo `reason`: acionar o responsavel pela plataforma.
NAO reprocessar a fila morta nem republicar eventos sem decisao registrada.

## 5. Falso alarme

Um evento isolado de `resume_anchor_mismatch` pode ser um produtor de teste; confirmar a origem
antes de escalar.
