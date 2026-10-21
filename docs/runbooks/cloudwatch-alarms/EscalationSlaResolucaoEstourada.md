# Alert Runbook: escalation-prazo-resolucao-estourado (CloudWatch)

**Audience:** quem recebe o topico `staff_alerts` do dev
**Alarm source:** `deploy/aws-ecs/envs/dev-sa-east-1/escalation-alarms.tf` (`aws_cloudwatch_metric_alarm.escalation_sla_resolucao_estourada`)
**Event source:** `src/maezo/tools/workers/events.py` (`EVENTO_SLA_RESOLUCAO_ESTOURADA`)
**Estado:** desligado por padrao (`escalation_alarms_enabled = false`)

## 1. Gatilho

Pelo menos uma linha `escalation.sla_resolucao_estourada` no log group do worker na ultima hora
(periodo 3600 s). O worker generico `operadora.events.publish` emite o evento quando recebe a tarefa
`ST_PublishResolBreach` (`event_topic=agents.events.escalation.sla_breached`, `event_fase=resolucao`),
que o BPMN alcanca quando o timer INTERRUPTIVO `BT_SlaResolucao` (`${roteamento.sla_resolucao}`)
dispara sobre `UT_TratarEscalonamento`. O evento sai ANTES da publicacao: o prazo estourou mesmo que
o evento de dominio nao chegue ao broker. O alarme tem `ok_actions`: volta a OK na primeira hora sem
estouro. O evento nao leva campo nenhum (nem chave de negocio).

Este caminho NAO passa por `notify_supervisor`: nao conta no alarme `escalation-prazo-estourado`
(prazo de ciencia) e nao gera aviso ao supervisor no inbox.

## 2. O que significa

Um caso escalado nao foi resolvido pelo grupo roteado dentro do prazo da DMN `escalation_routing`.
A tarefa `UT_TratarEscalonamento` foi interrompida e o caso esta agora em `UT_SupervisorAssume`,
candidato `supervisao-atendimento` (ultima linha humana, sem timer adicional).

## 3. Primeiros passos (so leitura)

1. No CloudWatch Logs Insights do log group do worker, contar `escalation.sla_resolucao_estourada`
   no periodo.
2. Na fila do motor, listar as tarefas `UT_SupervisorAssume` abertas e quem as tem.
3. Conferir se o evento `agents.events.escalation.sla_breached` (fase `resolucao`) foi publicado:
   o log `event_published` do mesmo worker; falha aparece como `event_publish_failed`.

## 4. Escalada

Tarefas `UT_SupervisorAssume` sem dono: acionar a supervisao de atendimento. A decisao sobre o caso
e sempre humana; o alarme nao decide nada.

## 5. Falso alarme

Em dev, prazos curtos da DMN disparam em casos de teste. Conferir se os casos sao de teste antes de
escalar. Uma reentrega da mesma tarefa ao worker (lock expirado) pode contar o mesmo estouro duas
vezes; o limiar (>= 1 por hora) nao muda com isso.
