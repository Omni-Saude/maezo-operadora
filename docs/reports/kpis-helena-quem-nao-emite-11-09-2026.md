# Os KPIs da Helena: o contador existe, o coletor não

**Medido em 11/09/2026** na conta de dev `203312548462`, em resposta ao §5 do runbook
*Fechar a jornada da Helena*. Este documento corrige a premissa daquele item, porque agir sobre
ela levaria a escrever código que já existe e a não resolver o que falta.

## A premissa e o que se mede

O runbook afirma: *"`runtime/metrics.py` cita `maezo_helena_escalation_rate` como regra de
agregação, mas o contador de base não é emitido pelo grafo"*, e pede para emitir os dois
contadores no ponto onde o desfecho já é registrado.

**O contador de base é emitido.** `observability.record_agent_desfecho` incrementa
`maezo_agent_desfecho_total` na linha imediatamente anterior ao log `agent_desfecho_recorded`, e a
Helena chama `emit_turn_desfecho` em dois pontos do grafo. O endpoint `/metrics` do receptor
respondeu `HTTP 200` com 8.724 bytes e estes valores vivos:

```
maezo_agent_desfecho_total{agent_id="helena",desfecho="escalado_humano",motivo_categoria="red_flag_clinico",route="escalate"} 6.0
maezo_agent_desfecho_total{agent_id="helena",desfecho="resolvido_automatico",motivo_categoria="",route="inform"} 5.0
maezo_agent_desfecho_total{agent_id="helena",desfecho="escalado_humano",motivo_categoria="solicitacao_humano",route="escalate"} 1.0
maezo_agent_first_response_seconds_count{agent_id="helena"} 12.0
```

Doze turnos: sete escalonamentos, cinco resoluções automáticas. Dá `escalation_rate` de 58% e
`resolution_rate` de 42% — os dois números que o runbook diz não existirem. A linha de
`solicitacao_humano` com contagem 1 é o próprio "Opa" relatado no §3.

**A regra de agregação também existe e casa.** `deploy/observability/alert-rules.yml` declara
`maezo_helena_resolution_rate` e `maezo_helena_escalation_rate` sobre
`maezo_agent_desfecho_total{agent_id="helena"}`, com o mesmo nome de métrica e os mesmos rótulos
que o código emite. O painel `dashboards/agentes.json` já as consulta.

## Onde está o buraco, então

Nada coleta. Medido na conta de dev:

| Peça | Estado |
|---|---|
| Workspace do Amazon Managed Prometheus `amh-prometheus-dev` | **ACTIVE** |
| Scraper do AMP | **nenhum** (`aws amp list-scrapers` vazio) |
| Coletor OTel/ADOT no cluster | **nenhum** (nenhuma family de task definition com `otel`, `adot`, `collector` ou `prometheus`) |
| Rule group namespace carregado no workspace | **nenhum** (`ResourceNotFoundException`) |
| `deploy/observability/prometheus.yml` | alvos de `docker-compose` local, não do ECS de dev |

Ou seja: as tasks expõem `/metrics`, o workspace está de pé, e entre os dois não há nada. As
regras de agregação vivem num arquivo YAML do repositório que nunca foi carregado no workspace, e
o AMP não avalia arquivo em repositório — avalia rule group namespace criado nele.

Consequência prática: o contador zera a cada redeploy da task, porque o valor só existe na memória
do processo enquanto ninguém o raspa.

## O que fazer, e é trabalho de infraestrutura

Não é emitir contador. São três peças, nesta ordem:

1. **Um coletor.** Uma task ADOT (ou um sidecar) com `prometheus_receiver` apontando para as
   tasks do cluster e `prometheusremotewrite` para o workspace. O scraper gerenciado do AMP só
   descobre alvos em EKS (`source.eksConfiguration`), então em Fargate/ECS o caminho é o coletor.
   Precisa de descoberta de alvos — `ecs_observer` do ADOT, ou alvos estáticos pelo DNS do
   Cloud Map, que é o que o cluster já usa (`<servico>.maezo-operadora-dev.internal`).
2. **O rule group namespace.** Um `aws_prometheus_rule_group_namespace` no Terraform, alimentado
   pelo mesmo YAML que hoje só serve ao compose, para que o arquivo continue sendo a única fonte.
3. **Só então** conferir os dois KPIs no painel.

**Estimativa:** meio dia para o coletor com alvos estáticos do Cloud Map, mais uma hora para o
rule group namespace e a conferência. Alvos dinâmicos custam mais e não são necessários enquanto o
cluster tiver o número de serviços que tem hoje.

## Por que isto importa para o teste

O comentário do próprio `agent.yaml` diz que `escalation_rate` não pode ser "nem alto demais
(inútil) nem baixo (risco)". Hoje o número existe dentro do processo e morre com ele. Analisar o
teste contra esse critério exige o caminho de coleta, não um contador novo — e a cada redeploy a
contagem volta a zero, o que torna qualquer leitura manual do endpoint válida apenas para a janela
desde o último deploy.

## O que NÃO foi alterado

Nada. Este documento é só o levantamento. Nenhum contador foi adicionado (já existiam), nenhuma
regra foi mexida (já casavam), e nenhum coletor foi criado — porque criar coletor é decisão de
infraestrutura com custo mensal, e o runbook não pediu isso.
