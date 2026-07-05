# ADR-0014: Plataforma de observabilidade: AMP/AMG gerenciados vs Prometheus/Grafana in-cluster

**Status:** Accepted
**Data:** 2026-06-13
**Area:** Operacoes

## Contexto

ADR-0010 definiu o _que_ observar (OTel com semantica de agentes, metricas de custo LLM, HITL,
SLA, replay de conversa) e deixou em aberto _onde_ as metricas seriam armazenadas e visualizadas
em staging/prod, pendente avaliacao de custo (§2-bis do PROJECT.md: "decidir por custo").

Na Wave 7 (Phase 0), o workstream W7 avaliou as alternativas:

| Alternativa | Prós | Contras |
|---|---|---|
| In-cluster Prometheus + Grafana (self-managed) | Zero custo de SaaS; pleno controle | Operacao de HA/retenção propria; risco de corrupção de série histórica; escalonamento manual; duplica operação da `amh-data-platform` (que já usa AMP) |
| Amazon Managed Prometheus (AMP) + Amazon Managed Grafana (AMG) | SLA AWS, retenção gerenciada, SigV4 nativo, alinha com módulo `observability-stack` da `amh-data-platform`; multi-tenant nativo; ~$30–50/mês na escala Phase 0-1 | Custo de SaaS; lock-in AWS; curva de SigV4 remote-write |

O stack `amh-data-platform` já usa o módulo Terraform `observability-stack` com AMP/AMG para o
pipeline CDC (Debezium/MSK Serverless — ADR-0013). Adotar a mesma base alinha operação,
reutiliza dashboards e permite agregação cross-tenant na mesma workspace AMP.

O custo estimado de AMP para o volume de séries da Phase 0-1 (< 10 agentes, < 50 métricas
distintas por tenant, retenção de 15 dias) é de ~$30–50/mês — abaixo do limiar de decisão
autônoma definido em §2-bis.

Para desenvolvimento local, manter Prometheus + Grafana no docker-compose é obrigatório: sem
acesso AWS, sem custo, feedback imediato no ciclo de desenvolvimento.

## Decisao

- **Staging e prod usam Amazon Managed Prometheus (AMP) + Amazon Managed Grafana (AMG).**
  - O OTel Collector (`config/otel-collector.yaml`) faz remote-write para o endpoint AMP via
    autenticação **SigV4** (IAM role do pod; `Authorization` header injetado por ADOT ou via
    variável `AWS_AMP_TOKEN` no exporter `prometheusremotewrite`).
  - A workspace AMG consome a workspace AMP como datasource; dashboards são provisionados como
    código em `deploy/observability/grafana-provisioning/`.
  - Regras de alerta (`deploy/observability/alert-rules.yaml`) são carregadas na workspace AMP
    via API Terraform — os mesmos arquivos servem dev (via `rule_files` no prometheus.yml) e
    prod (via AMP Rules API), sem bifurcação de conteúdo.
  - O catálogo de métricas (`src/maezo/runtime/metrics.py`) é a **fonte da verdade** de nomes,
    tipos e esquemas de labels; ele é compartilhado por ambos os ambientes.

- **Dev local usa Prometheus + Grafana in-cluster via docker-compose.**
  - Perfil `--profile observability` em `docker-compose.yml` levanta Prometheus (port 9090) +
    Grafana (port 3000) com os mesmos dashboards e regras de alerta.
  - `config/prometheus.yml` define o scrape config de dev; o exporter `prometheusremotewrite`
    aponta para `http://prometheus:9090/api/v1/write` (default quando
    `PROMETHEUS_REMOTE_WRITE_ENDPOINT` não está definido).

- **Não duplicamos a infraestrutura CDC da `amh-data-platform`** (ADR-0013): métricas Kafka
  (consumer lag, DLQ) são scrapeadas pelo OTel Collector do JMX exporter do broker local no dev;
  em prod, compartilhamos a workspace AMP da plataforma de dados.

## Consequencias

**Positivas:**
- Operação de retenção e HA delegada à AWS; foco em conteúdo (métricas, alertas, dashboards).
- Alinhamento com `amh-data-platform`: mesmo módulo Terraform, mesma workspace AMP/AMG,
  agregação cross-tenant nativa.
- Dashboards e regras de alerta são código versionado; sem drift entre dev e prod (mesmo YAML,
  destino diferente).
- SigV4 é o único ponto de autenticação; sem credenciais longas em config.

**Negativas (aceitas):**
- Custo recorrente (~$30–50/mês na escala Phase 1); reavaliar em Phase 2 se o volume crescer.
- Dependência de acesso AWS para staging/prod (bloqueio de DoD documentado em issue #16 —
  independente desta decisão).
- SigV4 exige IAM role corretamente configurada; erro de permissão torna o remote-write silente
  até o alerta `MaezoAgentRuntimeDown` disparar.
- TLS desabilitado no exporter em dev (`insecure: true` em `config/otel-collector.yaml`);
  **nunca habilitar este modo fora do docker-compose local**.

## Supersedes
ADR-0010 (complementa — não supersede; ADR-0010 define o que observar; este ADR define onde
armazenar e visualizar em cada ambiente).
