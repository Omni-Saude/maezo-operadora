# Alert Runbook: escalation-prazo-estourado (CloudWatch)

**Audience:** quem recebe o topico `staff_alerts` do dev
**Alarm source:** `deploy/aws-ecs/envs/dev-sa-east-1/escalation-alarms.tf` (`aws_cloudwatch_metric_alarm.escalation_supervisor_alertado`)
**Event source:** `src/maezo/tools/workers/escalation.py` (`EVENTO_SUPERVISOR_ALERTADO`, `MOTIVO_ALERTA_SLA_ACK`)
**Estado:** desligado por padrao (`escalation_alarms_enabled = false`)

## 1. Gatilho

Pelo menos uma linha `escalation.supervisor_alertado` com `motivo_alerta=sla_ack_breached` no log
group do worker na ultima hora (periodo 3600 s). O worker emite o evento depois de publicar o aviso
ao supervisor em `ST_NotificarSupervisor`, que o BPMN alcanca quando o timer nao-interruptivo
`BT_SlaAck` (prazo de ciencia, `${roteamento.sla_ack}`) dispara sobre `UT_TratarEscalonamento`.
O alarme tem `ok_actions`: volta a OK (e avisa) na primeira hora sem estouro.

Avisos ao supervisor por fallback de canal (`motivo_alerta=notificacao_primaria_falhou`) NAO contam
aqui: eles aparecem no alarme `escalation-aviso-falhou` (tipo `equipe`).

## 2. O que significa

Um caso escalado ficou sem ciencia de ninguem do grupo roteado dentro do prazo da DMN
`escalation_routing`. O caso continua aberto em `UT_TratarEscalonamento`; se o prazo de resolucao
(`BT_SlaResolucao`) tambem estourar, o processo interrompe a tarefa e a entrega a
`supervisao-atendimento` (`UT_SupervisorAssume`).

## 3. Primeiros passos (so leitura)

1. No CloudWatch Logs Insights do log group do worker, listar `escalation_supervisor_notified` no
   periodo para obter os casos (`business_key`).
2. Na fila do motor, conferir quem tem a tarefa `UT_TratarEscalonamento` de cada caso.

## 4. Escalada

Estouros repetidos no mesmo grupo indicam fila sem gente: acionar a coordenacao do grupo
roteado. A decisao sobre o caso e sempre humana; o alarme nao decide nada.

## 5. Falso alarme

Em dev, prazos curtos da DMN podem disparar em casos de teste. Conferir se os casos sao de teste
antes de escalar.
