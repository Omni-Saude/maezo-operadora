# Alert Runbook: escalation-aviso-falhou (CloudWatch)

**Audience:** quem recebe o topico `staff_alerts` do dev
**Alarm source:** `deploy/aws-ecs/envs/dev-sa-east-1/escalation-alarms.tf` (`aws_cloudwatch_metric_alarm.escalation_aviso_falhou`)
**Event source:** `src/maezo/tools/workers/escalation.py` (`EVENTO_AVISO_FALHOU`)
**Estado:** desligado por padrao (`escalation_alarms_enabled = false`)

## 1. Gatilho

Pelo menos uma linha `escalation.aviso_falhou` no log group do worker (`aws_cloudwatch_log_group.worker`)
em 5 minutos. O worker emite o evento quando a publicacao do aviso em
`operadora.notifications.internal` falha. O campo `tipo` diz qual aviso:

| tipo | tarefa BPMN | o que o processo faz em seguida |
|---|---|---|
| `equipe` | `ST_NotificarTime` | `BE_FalhaNotificacao` -> `ST_NotificarFallback` (supervisor) -> `UT_TratarEscalonamento` |
| `supervisor` | `ST_NotificarSupervisor` ou `ST_NotificarFallback` | `BE_NotifSupervisorFailed` -> `End_SupervisorAlertado`, ou `BE_NotifFallbackFailed` -> `UT_TratarEscalonamento` |

## 2. O que significa

A tarefa humana continua existindo no motor (o fallback e modelado, ERR_ESC_NOTIFY_FAILED,
ADR-0030), mas o aviso que deveria chegar a equipe ou ao supervisor nao saiu. Com `tipo=supervisor`
vindo do fallback, nem a equipe nem o supervisor foram avisados pelo topico: o caso so aparece na
fila do motor.

## 3. Primeiros passos (so leitura)

1. No CloudWatch Logs Insights do log group do worker, contar `escalation.aviso_falhou` por `tipo`.
2. Conferir o broker Kafka (servico `kafka`) e o produtor do worker.
3. Conferir na fila do motor que as tarefas `UT_TratarEscalonamento` dos casos afetados existem.

## 4. Escalada

Falha persistente do broker: acionar o responsavel pela plataforma. Os casos afetados precisam de
alguem olhando a fila do motor ate o canal voltar.

## 5. Falso alarme

O evento so e emitido quando uma publicacao foi de fato tentada e falhou. Sem produtor configurado
(`kafka=None`) o worker registra `escalation_notify_team_no_producer`, que NAO conta aqui.
