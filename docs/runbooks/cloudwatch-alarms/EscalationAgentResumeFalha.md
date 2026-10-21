# Alert Runbook: agent-resume-falha (CloudWatch)

**Audience:** quem recebe o topico `staff_alerts` do dev
**Alarm source:** `deploy/aws-ecs/envs/dev-sa-east-1/escalation-alarms.tf` (`aws_cloudwatch_metric_alarm.agent_resume_falha`)
**Event source:** `src/maezo/platform/integrations/agent_resume.py::run_resume_loop` (`EVENTO_RESUME_FALHA`)
**Estado:** desligado por padrao (`escalation_alarms_enabled = false`; so existe com o servico `agent-resume` ligado)

## 1. Gatilho

Pelo menos uma linha `agent_resume.falha` no log group `/ecs/<name>/agent-resume` em 5 minutos.
O consumidor de retomada emite esse evento ANTES de deixar uma falha nao-malformada propagar:
o offset NAO e confirmado e o evento `agents.events.process_completed` volta para reentrega.

O campo `motivo` diz em que etapa falhou (conjunto fechado, `MOTIVOS_FALHA_RESUME`):

| motivo | etapa |
|---|---|
| `motor_indisponivel` | leitura do historico do motor (`notas_resolucao`/`resultado`, ou o fato do cancelamento) |
| `envio_falhou` | envio da retomada ao beneficiario pelo agente (Helena ou Lucas) |
| `aviso_equipe_falhou` | aviso a equipe (`escalation.notify_team`) quando a retomada nao chega a pessoa |
| `outro` | qualquer outra etapa (custodia do destinatario, publicacao na fila morta, etc.) |

## 2. O que significa

Um atendente devolveu um caso ao agente e a pessoa ainda NAO recebeu a instrucao. Como o offset
nao anda, o consumidor tenta de novo; o envio e idempotente por instancia de escalonamento, entao
a reentrega converge em vez de mandar duas vezes. Se a causa persistir, a particao fica parada e
as retomadas seguintes da mesma particao esperam.

## 3. Primeiros passos (so leitura)

1. No CloudWatch Logs Insights do log group `/ecs/<name>/agent-resume`, filtrar
   `agent_resume.falha` e contar por `motivo`.
2. `motor_indisponivel`: conferir se o servico `cibseven` esta de pe.
3. `envio_falhou`: conferir o estado do canal WhatsApp (mesmas dependencias do receptor).
4. `aviso_equipe_falhou` ou `outro`: conferir o broker Kafka e o banco.

## 4. Escalada

Falha que nao se resolve com a volta da dependencia: acionar o responsavel pela plataforma.
Nao reprocessar fila morta nem mexer em offset sem decisao registrada.

## 5. Falso alarme

O evento so e emitido quando uma falha de fato propagou. Um unico evento seguido de retomada
bem-sucedida (`agent_resume.dispatched`) indica falha transitoria ja superada pela reentrega.
