# Métricas do canal — o coletor, os quatro números, e quem lê o que a Helena escreve

**Frente 5 de `HELENA_EM_PRODUCAO_O_QUE_FALTA.md`.** Cobre os três passos: o coletor (5.1), onde
os quatro números vivem (5.2) e a rotina de leitura humana (5.3).

## O defeito que esta frente fecha

`maezo_agent_desfecho_total` é emitido desde CC-09. As regras de agregação existem em
`deploy/observability/alert-rules.yml` desde o ADR-0014. O workspace gerenciado está `ACTIVE`.
**E ninguém coletava.** Medido em 14/09/2026: zero scrapers no workspace, zero coletor no cluster,
e o `prometheus.yml` do repositório apontando para o docker-compose da máquina de quem desenvolve.

A consequência não era só "falta um gráfico". Era que todo contador do canal zerava a cada
redeploy, e a taxa de escalonamento que o `agent.yaml` chama de KPI — *"não pode ser nem alta
demais (inútil) nem baixa demais (risco)"* — **nunca foi um número**. Era uma definição.

Vale registrar por que isso durou tanto: **métrica ausente e métrica saudável desenham o mesmo
gráfico.** Um painel vazio parece um canal tranquilo. Foi a mesma ambiguidade que escondeu, por
semanas, que `agent-helena` não executa turno nenhum.

---

## 5.1 · O coletor

Duas metades, deliberadamente separadas:

| Arquivo | O que decide |
|---|---|
| `deploy/observability/otel-collector-ecs-dev.yaml` | **o que** raspar, em que porta, para onde escrever |
| `deploy/aws-ecs/envs/dev-sa-east-1/service-metrics-collector.tf` | a task, a role e as regras carregadas no workspace |

O YAML entra na task definition por `file()`, então **o que roda no cluster é o que está
versionado** e um `terraform plan` mostra qualquer diferença. Não há parâmetro no SSM: uma segunda
fonte de verdade, editável pelo console e invisível no `git log`, é exatamente como configuração de
observabilidade apodrece.

### O que ele raspa

Descoberta pelo **Cloud Map**, nunca por IP: o IP de uma task Fargate muda a cada deploy. Os
registros são `MULTIVALUE`, então o A record devolve um IP por task e o coletor acompanha um
`desired_count` que sobe sem editar arquivo nenhum.

- `agent-helena` / `agent-rafael` / `agent-marina` na **8000**
- `webhook-receiver` na **8080** — **é aqui que a Helena roda de verdade.** `agent-helena` não
  executa turno (`agent_graph_execution_not_performed_here`); quem conduz o turno é o receptor, em
  processo. Um coletor que raspasse só os três agentes teria `maezo_agent_desfecho_total` sempre em
  zero e pareceria canal sem tráfego
- ele mesmo, na **8888** — sem isso não há como distinguir "canal quieto" de "coletor parado"

`tests/unit/deploy/test_coletor_de_metricas.py` é a cerca que liga as duas metades: um serviço
renomeado no Cloud Map, um agente novo não raspado, ou uma `containerPort` alterada quebram o teste
em vez de produzirem silêncio.

### O que não foi aberto

**Nenhuma regra de rede nova.** As regras auto-referenciadas de 8000 e 8080 existem desde 19/08, e
a saída 443 pela NAT é a que leva ao endpoint do workspace. Uma frente de observabilidade que
precisa abrir porta é uma frente medindo do lugar errado — vale conferir isso no `plan`.

### O apply

```
terraform apply \
  -target=aws_iam_role.metrics_collector_task \
  -target=aws_iam_role_policy.metrics_collector_remote_write \
  -target=aws_cloudwatch_log_group.metrics_collector \
  -target=aws_ecs_task_definition.metrics_collector \
  -target=aws_ecs_service.metrics_collector \
  -target=aws_prometheus_rule_group_namespace.maezo
```

`-target` sempre, como todo apply em dev neste ambiente.

### Como saber se está coletando de verdade

Um health check verde **não** prova coleta: o coletor sobe saudável escrevendo para lugar nenhum.
As três leituras que provam, em ordem de custo:

1. **O log da task** (`/ecs/maezo-operadora-dev/metrics-collector`). Uma escrita recusada aparece
   aqui como erro do exportador — 403 significa SigV4 ou permissão; 404 significa endpoint/workspace
   errado.
2. **A auto-telemetria**: `otelcol_exporter_send_failed_metric_points` acima de zero e subindo é
   escrita sendo recusada em silêncio para quem só olha o painel.
3. **A consulta ao workspace**, que é a única prova de fim a fim:
   ```
   awscurl --region sa-east-1 --service aps \
     "https://aps-workspaces.sa-east-1.amazonaws.com/workspaces/<id>/api/v1/query?query=maezo_agent_desfecho_total"
   ```
   Série vazia depois de uma bateria de conversas é o defeito de novo — não um canal tranquilo.

> **Ainda em aberto, e é honesto dizer:** o repositório não tem um Grafana apontado para este
> workspace. Os JSON de `deploy/observability/dashboards/` são provisionados para o compose local.
> Ligar um Grafana gerenciado ao workspace é uma decisão de plataforma, não desta frente; até lá os
> quatro números se leem por consulta.

---

## 5.2 · Os quatro números

| Número | Série | Onde |
|---|---|---|
| Taxa de escalonamento | `maezo_helena_escalation_rate` | `alert-rules.yml`, grupo `maezo_agent_kpi_derived` |
| Taxa de resolução automática | `maezo_helena_resolution_rate` | idem |
| Latência de primeira resposta (alvo < 15s) | `maezo_agent_first_response_seconds` | histograma, painel em `dashboards/agentes.json` |
| Taxa de recusa da cerca de saída | `maezo_agent_resposta_recusada_total` | **chega com a cerca de saída (PR #395)** — a métrica não existe na `main` |

### As duas primeiras mudaram nesta frente, e a mudança importa

Elas dividiam `sum(<contador>)` por `sum(<contador>)` — a soma do valor **acumulado**. Dois
defeitos, que só se tornam visíveis quando alguém finalmente coleta:

1. **Reset.** O contador vive na memória do processo e zera a cada redeploy. Numerador e
   denominador zeram em momentos diferentes, e a razão dá um salto que não corresponde a
   comportamento nenhum da Helena.
2. **Memória infinita.** Depois de um mês no ar, uma semana ruim mal move o número — e o
   `agent.yaml` quer usar esse número para decidir se a taxa está alta ou baixa demais *agora*.

Passaram a usar `increase(...[1h])`. Sem tráfego na janela a série fica **ausente** em vez de zero,
que é a leitura honesta de "não houve conversa" — um zero faria passar por "nenhuma resolvida".

---

## 5.3 · Alguém lê o que a Helena escreve

**Este passo não é opcional e não é substituível por métrica.** Os defeitos mais graves de 12 e
13/09 — a Helena desmentindo o próprio P1, prometendo contato humano com zero processos abertos,
afirmando ausência de alerta — **não apareceram em contador nenhum.** Apareceram porque a resposta
passou a voltar para a tela e alguém leu o texto. As canárias de vazamento nunca dispararam: o
texto morria no 401 do envio.

Nenhum dos quatro números acima teria mostrado qualquer um desses três defeitos. Todos os turnos
eram `resolvido_automatico`, dentro do prazo, sem erro.

### A rotina

**Cadência:** semanal, mesma janela toda semana.

**Amostra:** 20 turnos, ou todos, se houver menos de 20. Estratificada de propósito, porque uma
amostra aleatória de um canal saudável é quase toda conversa banal:

- 5 turnos que **escalaram** — a Helena acertou o encaminhamento *e* o texto dizia a coisa certa?
- 5 turnos **resolvidos automaticamente** com sintoma clínico no texto — o caso do achado de 12/09
- 5 turnos de **segunda ou terceira mensagem** na mesma conversa — onde vive o defeito de memória
  entre turnos (Frente 2)
- 5 **aleatórios**

**Quem lê:** o dono técnico lê todos. O dono clínico lê os 10 primeiros grupos — são os que exigem
juízo sobre o que ela *pode afirmar*, que não é decisão de engenharia. (Os dois donos estão
declarados em `spec/agents/helena/agent.yaml`.)

**O que se registra por turno lido:** o texto que ela enviou, o desfecho, e **uma linha** dizendo se
o texto é aceitável. Não há formulário — o que trava a rotina é burocracia, não falta de campo.

### O fecho: o achado vira teste

Um achado que não vira caso de teste é um achado que se repete. O caminho é o mesmo que já foi
percorrido três vezes:

1. o texto real vira caso na base da **Frente 3**;
2. se for frase que ela **nunca pode dizer**, entra numa das listas de
   `src/maezo/agents/helena/prompts.py` e a versão da recusa sobe;
3. **o teste vermelho vem antes da correção** — a cerca tem de ser vista pegando o defeito, senão
   não se sabe se ela pega.

> **A ordem importa, e foi aprendida na prática:** proibir no prompt não segura. O `response-v4`
> proibiu promessa de humano e o modelo parou de tentar *naqueles turnos* — o contador de recusa
> ficou em zero. Isso não prova que a cerca funciona; quem prova são os testes de unidade. **Zero
> sustentado só é interpretável junto do volume de turnos.**

### O limite desta rotina, e ele é sério

Ler mensagem de beneficiário é **ler PHI**. Hoje a rotina roda sobre tráfego de dev, que é
sintético por declaração (`var.caso_sintetico_zona_geral`) — e por isso é livre. **No dia em que
houver conversa real, esta rotina passa a ser tratamento de dado de saúde:** quem pode ler, onde, e
com que registro, é decisão de DPO, não desta página. Montar a rotina agora, no sintético, é
justamente o que permite chegar lá com o procedimento já rodando.
