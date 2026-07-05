# Runbook — Demo do Phase 0 (DoD manual)

Roteiro manual da Definition of Done da Phase 0. Espelha, passo a passo, o teste e2e
`tests/integration/e2e/test_phase0_journey.py`. Tudo com dados **sinteticos** (`Paciente
Teste`, tenant `amh`, pseudo-ids tipo `[CPF_...]`). Nada aqui usa PHI real.

Jornada demonstrada:

```
WhatsApp webhook (HMAC) -> Kafka whatsapp.message-received -> Helena (grafo)
   -> mcp-dmn (DMN real) -> SP-OP-ESCALATION-001 (engine real, business key ESC-amh-{conv})
```

## Pré-requisitos

```bash
# Stack core (postgres, cibseven, hapi-fhir, kafka) + simulator
docker compose --profile core --profile simulator up -d

# Espera saúde do engine e do FHIR
until curl -sf http://localhost:8080/engine-rest/version; do sleep 3; done
until curl -sf http://localhost:8081/fhir/metadata; do sleep 3; done
```

Variáveis usadas abaixo:

```bash
export ENGINE=http://localhost:8080/engine-rest
export CONV=conv-demo-001
export BKEY=ESC-amh-$CONV
export APP_SECRET=synthetic-test-app-secret-phase0   # sintético — não é segredo real
```

**Webhook receiver (processo local — NÃO é um serviço do docker-compose):** o
`docker-compose.yml` não define nenhum serviço `webhook-receiver` (perfis
core/simulator/observability sobem só postgres, cibseven, hapi-fhir, kafka,
otel-collector, prometheus, grafana, tasy-simulator). Para o Passo 1 funcionar,
suba o webhook como processo local na porta 8082 (o default do módulo é 8080 —
`WEBHOOK_PORT` sobrescreve; em K8s o Deployment `webhook-receiver` usa 8080):

```bash
WHATSAPP_APP_SECRET="$APP_SECRET" \
WHATSAPP_VERIFY_TOKEN=synthetic-verify-token-phase0 \
WEBHOOK_PORT=8082 \
uv run python -m maezo.platform.webhooks &
# KAFKA_BOOTSTRAP_SERVERS default = localhost:9092 — correto aqui: processo no HOST
# usa o listener EXTERNAL do compose (containers usariam kafka:29092).

# Prontidão via handshake de verificação do Meta (o app expõe só GET/POST /webhook):
until curl -sf "http://localhost:8082/webhook?hub.mode=subscribe&hub.challenge=ping&hub.verify_token=synthetic-verify-token-phase0" | grep -q ping; do sleep 1; done
```

## Passo 0 — Deploy dos artefatos no engine

```bash
curl -sf -X POST "$ENGINE/deployment/create" \
  -F "deployment-name=phase0-demo" \
  -F "enable-duplicate-filtering=true" \
  -F "bpmn=@src/maezo/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn" \
  -F "dmn=@src/maezo/processes/dmn/escalation_routing.dmn" | jq .id
```

## Passo 1 — POST no webhook WhatsApp com HMAC válido

O webhook (`src/maezo/platform/webhooks/whatsapp/app.py`) valida `X-Hub-Signature-256`
(HMAC-SHA256 com o app secret) e publica em `agents.events.whatsapp.message-received` com o
telefone **hasheado** (nunca o número cru — ADR-0006).

```bash
# Payload sintético no formato Meta WhatsApp Business
BODY='{"object":"whatsapp_business_account","entry":[{"id":"WABA_TEST","changes":[{"field":"messages","value":{"messaging_product":"whatsapp","metadata":{"display_phone_number":"551130000000","phone_number_id":"PNID_TEST"},"messages":[{"from":"5511999990000","id":"wamid.DEMO001","timestamp":"1700000000","type":"text","text":{"body":"Sou Paciente Teste. Tenho dor no peito forte ha 2 horas."}}]}}]}]}'

# Assinatura HMAC
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$APP_SECRET" | awk '{print $2}')"

# POST (webhook local em :8082 — subiu nos Pré-requisitos via WEBHOOK_PORT=8082;
# em K8s o Deployment webhook-receiver escuta em 8080)
curl -sf -X POST http://localhost:8082/webhook \
  -H "X-Hub-Signature-256: $SIG" \
  -H "Content-Type: application/json" \
  -d "$BODY"
# -> {"status":"ok"}   (HMAC inválido => 401)
```

Verifique o evento no Kafka (telefone hasheado, `message_body` presente):

```bash
docker compose exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic agents.events.whatsapp.message-received --from-beginning --max-messages 1
```

## Passo 2-3 — Helena classifica e a DMN decide o red flag

A Helena consome o evento, classifica a intenção (`symptom`) e **sempre** consulta a DMN
populacional de red flag (`triage_redflag_adult`) — o LLM nunca decide red flag (ADR-0012).
Com "dor no peito ha 2h", a DMN retorna `red_flag=true` / `P1`.

A DMN pode ser avaliada diretamente no engine para a demo:

```bash
curl -sf -X POST "$ENGINE/decision-definition/key/escalation_routing/evaluate" \
  -H "Content-Type: application/json" \
  -d '{"variables":{"motivo_categoria":{"value":"red_flag_clinico","type":"String"},"severidade":{"value":"grave","type":"String"}}}' | jq
# -> prioridade=P1, grupo_atendimento=plantao-clinico, sla_ack=PT5M, sla_resolucao=PT30M
```

## Passo 4 — SP-OP-ESCALATION-001 inicia (business key idempotente)

A Helena chama `mcp-cibseven.start_process` (via Tool Gateway -> PEP). O start é idempotente
pela business key `ESC-amh-{conversation_id}`:

```bash
curl -sf -X POST "$ENGINE/process-definition/key/SP-OP-ESCALATION-001/start" \
  -H "Content-Type: application/json" \
  -d "{\"businessKey\":\"$BKEY\",\"variables\":{
        \"tenant_id\":{\"value\":\"amh\"},
        \"source_agent_id\":{\"value\":\"helena\"},
        \"source_agent_version\":{\"value\":\"demo-1.0\"},
        \"conversation_id\":{\"value\":\"$CONV\"},
        \"beneficiario_pseudo_id\":{\"value\":\"[CPF_demo001]\"},
        \"canal\":{\"value\":\"whatsapp\"},
        \"motivo_categoria\":{\"value\":\"red_flag_clinico\"},
        \"severidade\":{\"value\":\"grave\"},
        \"resumo_contexto\":{\"value\":\"Paciente Teste — dor toracica, encaminhar plantao\"}
      }}" | jq .id
```

Confirme **uma** instância ativa para a business key (idempotência):

```bash
curl -sf "$ENGINE/process-instance?businessKey=$BKEY&active=true" | jq 'length'   # -> 1
```

Confirme a User Task no grupo roteado pela DMN (`plantao-clinico`, P1):

```bash
IID=$(curl -sf "$ENGINE/process-instance?businessKey=$BKEY&active=true" | jq -r '.[0].id')
TID=$(curl -sf "$ENGINE/task?processInstanceId=$IID" | jq -r '.[0].id')
curl -sf "$ENGINE/task/$TID/identity-links?type=candidate" | jq '.[].groupId'   # -> "plantao-clinico"
```

## Passo 5 — Humano resolve (HITL)

Um humano do grupo completa a User Task pela Tasklist (ou, na demo, via REST):

```bash
curl -sf -X POST "$ENGINE/task/$TID/complete" \
  -H "Content-Type: application/json" \
  -d '{"variables":{"resultado":{"value":"resolvido_humano","type":"String"},"notas_resolucao":{"value":"Paciente orientado a UPA","type":"String"}}}'
```

A instância termina em `End_ResolvidoPorHumano` e publica `agents.events.escalation.resolved`
**antes** do fim (sem fim silencioso — ADR-0007).

## Chaos (variante de staging — kubectl)

O teste `tests/integration/chaos/test_mid_conversation_kill.py` faz o kill **in-process**
(fecha o pool do checkpointer + descarta o objeto de grafo) e resume pelo `thread_id`. A
variante de **staging** mata o pod de verdade no meio da conversa.

> **AVISO — aponte para STAGING antes de rodar.** `maezo-amh` é o namespace de
> **PRODUÇÃO** (ver a tabela de convenções em `cd-rollback.md` §0 e o
> `.github/workflows/cd.yml`: staging = `maezo-staging`). Um force-delete aqui com
> o kubeconfig ainda apontado para prod mata pods de agente **em produção**.
> Fixe cluster e namespace explicitamente:

```bash
# 0. Contexto de STAGING explícito (nunca confie no contexto corrente):
aws eks update-kubeconfig --name maezo-staging --region sa-east-1
export NS=maezo-staging

# 1. Dispare uma conversa que vai parar num checkpoint (antes de `respond`).
# 2. Mate o pod da Helena no meio — o seletor correto é o label maezo.io/agent
#    (os pods de agente NÃO têm label `app=agent-runtime`; cada agente é um
#    Deployment `agent-{nome}` com labels maezo.io/agent + app.kubernetes.io/component):
kubectl -n "$NS" delete pod -l maezo.io/agent=helena --grace-period=0 --force
# 3. O pod reinicia; a próxima mensagem na mesma conversa resume pelo thread_id
#    (o estado vive no Postgres/checkpointer, não no processo). Verifique que a resposta
#    sai sem reprocessar do zero e sem perder o contexto acumulado.
```

A garantia testada é a mesma: **o estado da conversa sobrevive à morte do processo**. O teste
in-process prova a invariante em CI; esta variante a demonstra no cluster.

## SEC — evidência de não-vazamento de PHI e de PEP

Duas invariantes de segurança do DoD, verificáveis sem deploy:

```bash
# 1. PHI-leak: nenhum identificador cru (CPF/nome/telefone) chega ao LLM nem aos tool payloads,
#    e a auditoria JSONL só carrega input_hash (SHA-256). Roda in-process (sem engine).
pytest tests/integration/sec/test_phi_leak.py tests/unit/sec/test_audit_no_phi.py -q

# 2. PEP pen-test: nenhum overlay/encoding/spoof enfraquece um item `hard`, ações fora da
#    matriz/allowlist são negadas e auditadas, e ninguém invoca handler de tool fora do gateway.
pytest tests/unit/sec/test_pep_bypass.py -q
```

Na demo manual, a evidência de auditoria sem PHI pode ser inspecionada no JSONL produzido pelo
gateway (cada linha tem `input_hash` de 64 hex, nunca o payload cru):

```bash
# O caminho real do sink JSONL depende do wiring do deployment; o formato é o de audit.py.
# Exemplo: confirmar que nenhuma linha contém um CPF pontuado ou um nome.
grep -E '[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}' audit.jsonl && echo "FALHA: PHI cru" || echo "OK: sem CPF cru"
```

## Suites automatizadas do DoD (referência)

| Evidência DoD | Suite | Precisa de |
|---|---|---|
| Processo SP-OP-ESCALATION-001 (happy paths, timers, DMN, idempotência, fallback) | `tests/integration/processes/` | engine real (`--profile core`) |
| Jornada e2e WhatsApp→Helena→DMN→escalonamento | `tests/integration/e2e/test_phase0_journey.py` | engine real + `--profile simulator` |
| Chaos resume após kill | `tests/integration/chaos/` | Postgres real (`--profile core`) |
| PHI-leak (grafo) | `tests/integration/sec/test_phi_leak.py` | nenhum (in-process) |
| PEP pen-test + auditoria sem PHI | `tests/unit/sec/` | nenhum |
| Eval gate (golden 270 / FakeProvider) | `tests/evals/` (`make evals`) | nenhum |

O suite de processo executa os **workers reais de Phase 0** (`register_phase0_workers` +
`WorkerHarness`, com um `FakeKafkaPublisher` capturando os eventos de domínio) contra o engine
real — não há mock de engine nem reimplementação de worker no teste (ADR-0011 / AGENTS.md §3).

```bash
# Tudo que não precisa de engine (roda local sem Docker):
pytest tests/unit tests/evals tests/integration/sec/test_phi_leak.py -q

# Suite completa de integração (requer a stack de cima):
make test-integration
```

## Limpeza

```bash
docker compose --profile core --profile simulator down -v
```
