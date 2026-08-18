# Validação do maezo na AWS — 18/08/2026

Pedido da diretoria: validar o que subiu, **com o agente já conectado ao Bedrock**.

Ambiente: conta **203312548462**, cluster ECS `maezo-operadora-dev`, região `sa-east-1`.
Tudo abaixo foi medido hoje, contra o ambiente real. Nada foi copiado do teste local.

---

## A conclusão, primeiro

**O fluxo de autorização atravessa de ponta a ponta na AWS.** Uma instância real do
SP-OP-AUTH-001 percorreu os dez passos, avaliou as três tabelas de decisão, calculou os
quatro critérios, armou os dois relógios, parou na tarefa do médico auditor, recebeu uma
decisão humana e **barrou a negativa mal fundamentada**.

**A conexão com o Bedrock está provada** — chamada real, medida em tokens, pela mesma
fachada que os agentes usam.

**Uma linha do quadro não fecha, e não é por configuração:** o agente Rafael não pode
rodar com Bedrock. Detalhe no §4.

Para isso funcionar foi preciso corrigir um bloqueio que só apareceu rodando: **sem broker
Kafka o processo não passava do primeiro passo** (§3).

---

## 1. O agente conectado ao Bedrock

Execução da fachada `maezo.runtime.inference` dentro da VPC, com as credenciais e a rede
de produção do ambiente:

```
inference_provider_initialized   provider=bedrock   is_mock=False   phi_capable=False
                                 deployment_region=global-multi-region
                                 max_data_classification=internal

llm_token_usage    agent_id=validacao-diretoria   model=claude-opus-5   provider=bedrock
                   input_tokens=20   output_tokens=5   total_tokens=25   tenant_id=amh

inference_bedrock_generate   aws_region=sa-east-1   stop_reason=end_turn
resposta = 'PONG'
```

`is_mock=False` e a medição de tokens são o que separa "configuramos o Bedrock" de
"o modelo respondeu". Na **mesma execução**, a fronteira PHI foi exercitada:

```
ZONA_PHI_RECUSADA — "PHI-tagged inference request cannot be served by provider 'bedrock'
                     — it is not PHI-capable. Route to a human / incident; NEVER falling
                     back to the general cloud provider (ADR-0006/ADR-0017)."
```

O controle recusou **antes do egresso**. É o comportamento correto.

---

## 2. O quadro, linha por linha, com a evidência da AWS

Instância `f6a6faa0-9aa7-11f1-a88f-06ad701ee363` · business key `AUTH-amh-VALID-B5D2951A86`
· aberta 17/08 22:56:01 (BRT).

| O que a repo define | O que rodou **na AWS** |
|---|---|
| A solicitação chega por um canal (portal, WhatsApp, TISS) | **Não rodou** — entrada pelo REST do engine. Nenhum canal está implantado. |
| Rafael recebe, busca os dados e monta o dossiê | **Rodou como programa comum** (`ST_PrepararDossie`, 22:56:04). Não como agente com LLM — ver §4. |
| O motor avalia as 3 tabelas de decisão | **Rodou de verdade** — `auth_admissibility`, `auth_sla`, `auth_auto_approval`, com entrada e saída de cada uma (§2.1) |
| Um validador calcula os 4 critérios | **Rodou e devolveu os 4 como falso** — `regulatorio`, `contratual`, `financeiro` e `tecnico` (DUT/ROL) |
| Os prazos são armados | **Rodou de verdade** — dois relógios: **20/08** (alerta P3D) e **22/08** (SLA P5D). Ressalva no §5. |
| O médico auditor decide | **Rodou de verdade** — tarefa `UT_AnaliseMedicoAuditor`, grupo autorizado `medico-auditor`, decisão registrada às 22:58:39 |
| A negativa mal fundamentada é barrada | **Rodou de verdade — nada foi transmitido** (§2.2) |
| A negativa é comunicada ao beneficiário | A etapa de emissão **foi alcançada e bloqueada** por fundamentação incompleta. O que não existe é canal do outro lado. |

### 2.1 As três decisões, com o que entrou e o que saiu

```
auth_admissibility    beneficiario_ativo=True  carencia_cumprida=True
                      documentacao_completa=True  requer_autorizacao=True
                   →  resultado='SEGUE_ANALISE'

auth_sla              carater_atendimento='eletivo'  categoria_procedimento='consulta'
                   →  sla_analise='P5D'  sla_alerta='P3D'
                      fonte_regulatoria='RN 395/2016 … : DRAFT/verify'

auth_auto_approval    criterio_regulatorio_ok=False   criterio_contratual_ok=False
                      criterio_financeiro_ok=False    criterio_tecnico_ok=False
                      auto_criteria_verificado=True
                   →  recomendacao='ANALISE_HUMANA'
```

O validador **executou** (`auto_criteria_verificado=True`) e os quatro deram falso —
distinção que importa: não é "não avaliou", é "avaliou e não há regra ratificada".
A variável `motivo_bloqueio_criterios` registra `TECNICO_FONTE_NAO_RATIFICADA`.

### 2.2 A negativa barrada

Decisão do auditor: `NEGAR` **sem** `fundamentacao_dut` (espelha o teste GAP-AUTH-2).

```
WorkerBpmnError: send_denial_notice: fundamentacao incompleta,
campos ausentes: ['fundamentacao_dut'] — negativa formal NAO transmitida
(RN 395 art. 10, L0 hard ADR-0005)
```

O processo parou em `ST_EnviarNegativaFormal` com **incidente**, e a negativa **não saiu**.

Uma observação que parece defeito e não é: o BPMN modela `BE_NegativaIncompleta` →
`End_FundamentacaoIncompletaBloqueada`, mas esse caminho **não** foi tomado. É deliberado —
`worker_runtime/service.py:99-106` explica que ativar esse código antes do T-E *"trocaria um
incidente garantidamente visível a um humano por um fim limpo e silencioso num terminal
neutro"*. Em produção, a negativa barrada tem de **incomodar alguém**. Foi o que aconteceu.

---

## 3. O bloqueio que só apareceu rodando: sem Kafka, o processo não anda

A primeira instância (`82456366-9aa6-…`) morreu no **primeiro** passo:

```
incidente failedExternalTask em ST_PublishReceived
KafkaConnectionError: Unable to bootstrap from [('localhost', 9092)]
```

`ST_PublishReceived` usa o tópico `operadora.events.publish`, onde **publicar é o trabalho**
— e ele falha fechado. O teste local passava porque o `docker-compose` subia Kafka; na AWS o
**ADR-036 da plataforma estacionou o CDC e deletou o cluster MSK**.

Isto corrige uma afirmação registrada no comentário do próprio `service-worker.tf` (minha):
*"sem broker o daemon sobe e trabalha, apenas não publica evento"*. Vale para o publisher
**opcional** dos 17 módulos de registro; **não** vale para este passo.

**O que foi feito:** um broker de nó único no nosso próprio cluster (~US$ 20/mês), não a
recriação do MSK. Ligar `create_msk=true` no ambiente da plataforma recriaria o cluster que
alguém deletou de propósito para cortar custo — decisão do dono daquele ADR, não efeito
colateral de um teste. Limites declarados: sem replicação, disco efêmero, e some com
`kafka_desired_count = 0`. **Produção é MSK, ou outra decisão ratificada.**

---

## 4. A linha que não fecha: Rafael com LLM

O diretor pediu a validação "com agente já conectado ao Bedrock". Para o Rafael isso **não é
possível por configuração**, e a razão está em três pontos do código:

```
agents/rafael/graph.py:583    a única chamada de LLM do fluxo passa  phi=True
runtime/inference.py:508      BEDROCK_CAPABILITIES declara  phi_allowed=False
runtime/inference.py:2620     a fachada levanta PhiZoneRoutingError ANTES do egresso
```

Só dois provedores satisfazem PHI:

- **`phi_zone_mock`** — `supported_model_versions=frozenset()`: **não roda modelo nenhum**.
- **`br_resident`** — o adaptador BR-resident real, e o comentário do arquivo é literal:
  *"WITHOUT IT THE PROVIDER REFUSES TO CONSTRUCT"* sem `MAEZO_PHI_VENDOR_DPA_REF`.
  **"PHI-eligible BY DESIGN, UN-BOOTABLE UNTIL AN OWNER ACTS."**

Por isso `agent-rafael` e `agent-marina` estão com zero réplicas, com as task definitions
prontas. Subi-los apontando para o perfil `global.*` — que **roteia entre regiões por
desenho** — criaria exatamente o precedente que a zona PHI existe para impedir.

**A decisão que cabe à diretoria**, e que é uma bifurcação real:

1. **Provisionar o endpoint BR-resident + o DPA** (`MAEZO_PHI_VENDOR_DPA_REF`). É o caminho
   que o desenho prevê. Depois disso, ativar é mudar variável, não escrever código.
2. **Decidir que a narrativa do dossiê pseudonimizada é chamada de zona geral.** O envelope
   A2A que chega ao Rafael carrega só referências e pseudo-ids — *"Rafael never receives a
   CPF/name over this seam"*. Se compliance e o corpo clínico entenderem que isso não é PHI,
   o `phi=True` do `graph.py:583` muda. **É decisão registrada, com ADR, não ajuste de
   deploy** — e não é minha para tomar.

---

## 5. Duas ressalvas que a diretoria deve ver

**O quadro fala em "RN 259"; a tabela do motor cita outra norma.** A saída real foi
`fonte_regulatoria = 'RN 395/2016 — resposta em até 5 dias úteis: DRAFT/verify'`. Os prazos
armados (P3D/P5D) vêm dessa tabela, e ela **se declara `DRAFT/verify`**. Alguém com autoridade
regulatória precisa confirmar norma e prazos antes de qualquer uso real.

**O manifesto de autonomia está `DRAFT`.** No boot dos agentes:
`action_approvals_manifest_not_ratified … status=DRAFT … every action class denies`, em modo
`shadow`. Toda classe de ação **nega**. É a ratificação clínica pendente, funcionando como
projetado.

---

## 6. Estado e custo

| Serviço | Réplicas | |
|---|---|---|
| `cibseven` | 1 | engine BPMN/DMN, 78 artefatos publicados |
| `worker-runtime` | 1 | 124 workers · `dossier_delegation_ready=True` |
| `agent-helena` | 1 | zona geral, Bedrock real |
| `kafka` | 1 | broker de dev (§3) |
| `agent-rafael` / `agent-marina` | 0 | zona PHI — bloqueada (§4) |

Quatro tasks Fargate ≈ **US$ 103/mês**. Banco, FHIR, VPC e NAT são reaproveitados da
plataforma: custo adicional zero.

---

## 7. Como reexecutar

O coletor de evidência é versionado — `python -m maezo.platform.evidence`, com
`ENGINE_REST_URL` apontando para o engine. Ele abre uma instância nova e imprime os oito
blocos deste relatório. Não conclui nada por conta própria: imprime o que o engine registrou
e deixa a leitura para quem confere.

_Método: cada afirmação aqui foi medida hoje contra a conta 203312548462 — ids de instância,
horários e mensagens vêm da API do engine e do CloudWatch, não de execução local._
