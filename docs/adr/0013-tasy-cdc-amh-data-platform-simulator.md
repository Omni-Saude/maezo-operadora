# ADR-0013: Integracao Tasy via CDC da Plataforma de Dados; simulador como contrato

**Status:** Accepted
**Data:** 2026-06-12
**Area:** Integracao / Dados

## Contexto

A operadora AMH (primeiro tenant piloto) ja opera Tasy como HIS (Hospital Information System).
A plataforma de dados AMH (`amh-data-platform`) JA roda um pipeline CDC Tasy em producao:
Debezium 2.7 sobre Oracle LogMiner (ECS Fargate, per ADR-025 do amh-data-platform — IAM-auth
classloader incompatibility impediu MSK Connect), MSK Serverless sa-east-1, schema Avro com
Glue Schema Registry, `snapshot.mode=schema_only_recovery` com DMS full-load handoff,
DLQ por conector (`cdc.<tenant>.tasy.dlq`), heartbeat 30 s. Conectores por tenant instanciados
via modulo Terraform `kafka-connect-fargate` (ADR-029 amh-data-platform).

Tópicos publicados pelo amh-data-platform:
- `cdc.amh.tasy.pessoa_fisica` (VW_FHIR_PATIENT — PESSOA_FISICA + PACIENTE)
- `cdc.amh.tasy.convenio_paciente` (VW_FHIR_COVERAGE — CONVENIO_PACIENTE + CONVENIO + PLANO_CONVENIO)
- `cdc.amh.tasy.autorizacao_convenio` (VW_FHIR_CLAIM_AUTH — AUTORIZACAO_CONVENIO)
- `cdc.<tenant>.tasy.dlq` (mensagens nao-processaveis — per-connector DLQ)

Encoding: Avro + Glue Schema Registry em producao; JSON em dev (Confluent Debezium envelope).

Havia tres opcoes para consumir dados Tasy:

1. **Construir CDC proprio**: Debezium + MSK neste repo, credenciais Oracle AMH diretamente.
   Problemas: duplica infraestrutura ja existente; credenciais Oracle sao responsabilidade
   do time de dados AMH; IAM-auth classloader issue reportado no ADR-025 amh-data-platform
   tornaria isso um projeto de infra de varios sprints.

2. **Consultas REST Tasy**: polling via API Tasy SOAP/REST.
   Problemas: ausencia de changelog, latencia alta, nao escala para CDC payer.

3. **Consumir os topicos CDC do amh-data-platform como contrato de integracao** (opcao escolhida).
   O amh-data-platform e a plataforma de dados de registro — CDC ja rodando, equipe proprietaria,
   SLA definido. Nos somos consumer, nao producer.

Credenciais Oracle AMH reais sao um **BLOCKED item** (depende de acordo de acesso e
provisionamento pelo time amh-data-platform). Ate que esse desbloqueio ocorra — e como
deliverable permanente de CI mesmo apos o desbloqueio — precisamos de um simulador que
produza eventos CDC com o mesmo envelope Debezium, permitindo desenvolvimento e testes
end-to-end sem Oracle real.

Biblioteca Kafka: o `pyproject.toml` ja declara `aiokafka`. A alternativa `confluent-kafka`
exige extensao C e e mais adequada para producers de alto throughput com Avro nativo; para
o padrao de consumo assíncrono Python-first deste repo e para o simulador dev, `aiokafka`
e mais adequado e ja esta disponivel como dep transitiva.

## Decisao

1. **Consumimos os topicos CDC do `amh-data-platform` como o contrato de integracao Tasy.**
   Este repo NAO contem pipeline CDC, conectores Debezium nem credenciais Oracle.
   Mudancas no schema CDC sao upstream change no amh-data-platform; consumidores DEVEM
   pinnar schema version nos fixtures de teste.

2. **Usamos `aiokafka`** para producer (simulador) e consumer (fhir_sync, whatsapp_webhook).
   Encoding dev: JSON (envelope Debezium). Encoding prod: Avro + Glue (referenciado em
   comentarios de codigo, nao implementado aqui — responsabilidade do amh-data-platform broker).

3. **O simulador Tasy e um deliverable permanente de CI**, nao um hack temporario.
   Ele e o contrato vivo do envelope CDC: qualquer divergencia entre simulador e amh-data-platform
   e uma discrepancia de contrato a ser resolvida. CI roda o simulador como service no
   docker-compose profile `simulator`.

4. **Credenciais Oracle AMH** serao injetadas via env/secrets gateway quando o acesso for
   provisionado — sem mudanca de arquitetura, apenas adicao de credencial ao cofre.

5. **Topicos DLQ** por workload:
   - `cdc.amh.tasy.dlq` — DLQ upstream (amh-data-platform, nao gerenciado aqui)
   - `cdc.amh.tasy.fhir-sync.dlq` — DLQ do nosso consumer fhir_sync
   - `agents.events.whatsapp.message-received` — eventos de mensagem WhatsApp para agentes
   - `agents.events.whatsapp.status` — status de entrega/leitura WhatsApp para agentes

## Consequencias

**Positivas:**
- Zero duplicacao de infra CDC; aproveitamos SLA e expertise do amh-data-platform.
- Desacoplamento: mudancas no HIS Tasy sao absorcidas pelo amh-data-platform antes de chegarem aqui.
- Simulador como contrato garante que o CI nao "passa" sem evidencia de consumo correto do envelope.
- `aiokafka` homogeneo com o resto do runtime async Python; sem extensao C adicional.
- Credencial Oracle como segredo unico em cofre — nao dispersa entre repos.

**Negativas (aceitas):**
- Dependencia de SLA do amh-data-platform para dados em tempo real em producao.
- JSON dev vs Avro prod: diferenca de schema enforcement; mitigada por tipos Pydantic validados em ambos os casos.
- Simulador precisa ser mantido sincronizado com o schema real — custo de manutencao pequeno mas nao zero.
- `aiokafka` pode ter limitacoes de throughput vs `confluent-kafka` para producers de alto volume; aceitavel para os padroes de consumo atuais.

## Supersedes
—
