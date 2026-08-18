# Plano: acesso e gestão do Bedrock para o time de engenharia de agentes

Pedido: liberar um time de engenharia de agentes para **acessar e gerenciar o Bedrock dos
agentes**. Este documento levanta o estado real, define o que "gerenciar" precisa
incluir, e propõe a implementação em ondas — separando o que é permissão, o que é
código e o que é decisão de alguém.

Tudo abaixo foi medido em 18/08/2026 na conta **203312548462**, região `sa-east-1`.

---

## 0. O retrato medido (o ponto de partida honesto)

| Dimensão | Estado hoje |
|---|---|
| Modelo em uso | `global.anthropic.claude-opus-5`, **um só**, igual para todos os agentes |
| Perfis disponíveis | todos `global.*` — opus 5/4.8/4.7, sonnet 5/4.6/4.5, haiku 4.5, fable 5 |
| Perfil BR-resident | **não existe** para Claude nesta região |
| Guardrails do Bedrock | **zero** |
| Logging de invocação | **desligado** |
| Application inference profiles | **zero** (nenhuma atribuição de custo por agente) |
| Permissão dos agentes | `bedrock:InvokeModel` restrito a `claude-opus-5` |
| Identity Center | 1 permission set (`AdministratorAccess`), **0 grupos**, 1 usuário |

### O achado que mais importa

`spec/agents/rafael/agent.yaml` declara o modelo por **tier**:

```yaml
model:
  task_default: { tier: fast }      # gather/assess/montagem do dossie
  reasoning:    { tier: frontier }  # casos ambiguos / dossie complexo
```

`AgentDefinition.model` (`agents/__init__.py:292`) **parseia** esse bloco. E o runtime
**não o consome**: `grep` por consumidores de `.model` no runtime devolve zero, e não há
nenhum mapeamento de `tier` para model id em nenhum lugar. O modelo real vem de
`MAEZO_BEDROCK_MODEL_ID`, uma variável de ambiente uniforme.

**Consequência para este pedido:** hoje "gerenciar o Bedrock dos agentes" não tem
alavanca no lugar onde o time de agentes naturalmente trabalharia. Dar acesso ao AWS
sem consertar isso entrega um console onde não há o que gerenciar — e mantém a decisão
de modelo numa variável de ambiente que só quem faz deploy alcança.

É a mesma classe de achado que o repositório já documentou sobre outros campos do
`agent.yaml`: *"aspirational prose with zero computing code"*
(`tools/workers/auth.py:653`).

---

## 1. O que "gerenciar o Bedrock dos agentes" precisa incluir

Seis capacidades. Nenhuma é só permissão.

| # | Capacidade | Hoje | O que falta |
|---|---|---|---|
| 1 | Escolher modelo por agente e por tarefa | impossível | código: `tier` → model id |
| 2 | Versionar e comparar prompt | versionado (`prompt_versions`), sem harness de comparação em nuvem | processo + eval |
| 3 | **Guardrails** (filtro de conteúdo, PII, tópicos negados) | inexistente | código + IAM + decisão clínica |
| 4 | Custo por agente | invisível | application inference profile + tags |
| 5 | Observabilidade (tokens, latência, erro) | log estruturado `llm_token_usage` | métrica + painel |
| 6 | Logging de invocação (prompt + resposta) | desligado | **decisão LGPD**, não tarefa técnica |

### Sobre o item 6, antes que alguém ligue "para depurar"

O logging de invocação do Bedrock persiste **requisição e resposta completas** em
S3/CloudWatch. No maezo, a requisição do Rafael é a narrativa do dossiê — fatos clínicos
de um beneficiário. Ligar isso transforma um bucket de log em repositório de dado de
saúde, com todas as consequências de retenção, acesso e apagamento (LGPD).

Não é uma configuração de conveniência. Se for ligado, tem de ser com destino dedicado,
CMK própria, retenção declarada e o mesmo tratamento de apagamento dos demais dados de
paciente. **Recomendação: manter desligado**, e resolver depuração por (a) `llm_token_usage`,
que já registra tokens sem conteúdo, e (b) reprodução com caso sintético.

---

## 2. O plano, em cinco ondas

Cada onda é independente e entrega valor sozinha. A ordem é por dependência, não por
importância.

### Onda 1 — Permissão (IAM) · pode ser hoje

Criar o permission set **`MaezoAgentEngineer`** no Identity Center e um grupo
`maezo-agent-engineers`. Detalhe no §3.

Entrega: o time invoca modelos, lê métricas e gerencia guardrails **sem** admin e **sem**
alcance a dado de paciente.

### Onda 2 — Fazer o `tier` funcionar (código) · a alavanca

Mapear `tier` → model id, com a tabela vindo de configuração versionada e não de código:

```
fast      → global.anthropic.claude-haiku-4-5   (ou sonnet-5, decisão do time)
frontier  → global.anthropic.claude-opus-5
```

O `agent.yaml` passa a decidir de verdade qual modelo cada agente usa em cada tarefa, e o
time de agentes trabalha em YAML revisável por PR — não em variável de ambiente.

Ganho colateral imediato: **custo**. Montagem de dossiê é sumarização de fatos
estruturados; hoje ela roda no modelo mais caro do catálogo porque não há como dizer
"esta tarefa é `fast`".

A capacidade de zona (`phi_allowed`) **continua sendo do provedor**, não do tier — trocar
de tier nunca pode mudar a zona. Isso precisa de teste que trave o comportamento.

### Onda 3 — Guardrails (código + IAM + decisão clínica)

O código hoje não passa `guardrailIdentifier` em nenhuma chamada. Implementar exige:

1. o provedor Bedrock aceitar guardrail (id + versão) por configuração;
2. um guardrail criado no Bedrock com as políticas decididas por **quem responde pelo
   conteúdo clínico**, não pela engenharia;
3. o comportamento em caso de bloqueio: para o maezo, `BLOCKED` deve **rotear ao humano**,
   nunca degradar silenciosamente — a mesma regra do resto da plataforma.

Candidatos naturais de política: filtro de PII (mesmo com pseudonimização, o prompt
carrega fatos clínicos), tópicos negados (o agente não opina sobre cobertura — já é regra
L0-hard, o guardrail é a segunda barreira), e limites de conteúdo sensível.

### Onda 4 — Custo por agente (IAM + config)

Criar um **application inference profile por agente**, com tag `agente=<nome>`, e apontar
cada task definition para o profile do seu agente. A partir daí o Cost Explorer responde
"quanto a Helena custou este mês" — hoje não há como saber.

### Onda 5 — Observabilidade (config)

Painel com as métricas nativas do namespace `AWS/Bedrock` (invocações, tokens de entrada
e saída, latência, throttling) somadas ao `llm_token_usage` que a aplicação já emite com
`agent_id` e `tenant_id`. Alarme para erro e throttling.

---

## 3. O permission set `MaezoAgentEngineer`

### O que entra

**Bedrock — plano de dados**
- `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`, `bedrock:Converse`,
  `bedrock:ConverseStream` nos perfis `global.anthropic.*` e nas foundation models
  `anthropic.*` desta conta.

**Bedrock — plano de controle (leitura)**
- `bedrock:ListFoundationModels`, `GetFoundationModel`, `ListInferenceProfiles`,
  `GetInferenceProfile`.

**Bedrock — guardrails e prompts (gestão)**
- `bedrock:CreateGuardrail`, `UpdateGuardrail`, `CreateGuardrailVersion`, `GetGuardrail`,
  `ListGuardrails`, `ApplyGuardrail`, `DeleteGuardrail`.
- `bedrock:CreatePrompt`, `UpdatePrompt`, `GetPrompt`, `ListPrompts`, `CreatePromptVersion`.
- `bedrock:CreateInferenceProfile`, `TagResource`, `UntagResource`, `ListTagsForResource`
  (para os profiles de aplicação da Onda 4).

**Observabilidade**
- `cloudwatch:GetMetricData`, `GetMetricStatistics`, `ListMetrics` no namespace
  `AWS/Bedrock`.
- `logs:FilterLogEvents`, `GetLogEvents`, `DescribeLogStreams` em
  `/ecs/maezo-operadora-dev/agent-*` — onde vive o `llm_token_usage`.

**Operação mínima dos agentes**
- `ecs:DescribeServices`, `DescribeTasks`, `ListTasks`, `UpdateService` **restrito** aos
  services `agent-*` do cluster `maezo-operadora-dev` (para reiniciar um agente após
  mudar prompt ou tier).

### O que fica de fora, de propósito

| Fora | Por quê |
|---|---|
| `bedrock:PutModelInvocationLoggingConfiguration` | ligar logging captura narrativa clínica — é decisão LGPD, não tarefa de engenharia (§1, item 6) |
| `bedrock:CreateModelCustomizationJob`, fine-tuning | treinar com dado desta conta é decisão de outra ordem |
| RDS, e Secrets Manager em `amh-aurora-*` | as credenciais do banco compartilhado; o time de agentes não precisa do banco |
| Lake (raw/bronze/silver/gold), Glue, Athena, Lake Formation | onde os 11,45M de recursos FHIR vivem |
| `iam:*` | quem pode criar política pode se dar qualquer permissão |
| `ecs:RegisterTaskDefinition` | mudar task definition é deploy, e deploy passa por PR e CodeBuild |
| Cognito, Cloudflare | identidade e borda |

O teste do desenho: **o time de agentes consegue trocar modelo, criar guardrail, medir
custo e reiniciar um agente — e não consegue ler dado de paciente nem se auto-promover.**

---

## 4. As fronteiras que este time não atravessa sozinho

Quatro mudanças continuam exigindo alguém além da engenharia de agentes. Elas não são
burocracia: cada uma já tem mecanismo no repositório.

| Mudança | Quem decide | Mecanismo |
|---|---|---|
| Declarar um provedor apto a PHI (`phi_allowed`) | DPO + arquitetura | `ProviderCapabilities` em código, com ADR |
| Mudar a zona de segurança de um agente | DPO + médico auditor | `agent.yaml` + revisão |
| Narrativa do dossiê como zona geral | DPO + médico auditor | `spec/policies/phi/dossier-narrative-zone.yaml` |
| Ligar logging de invocação | DPO | fora do permission set, de propósito |

A chave `MAEZO_DOSSIER_NARRATIVE_GENERAL_ZONE_SYNTHETIC_ONLY`, hoje ligada para o teste
com caso fictício, **não** é uma dessas quatro — ela afirma que o ambiente não tem dado
real, e some no dia em que tiver.

---

## 5. Ordem, esforço e o que falta decidir

| Onda | Esforço | Estado em 18/08/2026 |
|---|---|---|
| 1 — permission set + grupo | pequeno | **FEITO** — falta só pôr nome no grupo |
| 2 — `tier` → model id | médio | bloqueada: qual modelo é `fast` (decisão do time) |
| 3 — guardrails | médio | bloqueada: políticas de conteúdo (decisão clínica) |
| 4 — custo por agente | pequeno | pendente, sem bloqueio |
| 5 — painel e alarmes | pequeno | pendente, sem bloqueio |

### Onda 1 — o que ficou pronto

`deploy/aws-identity-center/` (Terraform, aplicado): os dois permission sets, dois grupos
**vazios** e as atribuições ligando grupo → permission set → conta `203312548462`. As
roles foram provisionadas de fato na conta de dados (`AWSReservedSSO_MaezoAgentEngineer_*`
e `AWSReservedSSO_MaezoOperadoraLeitura_*`, conferidas por `iam list-roles`).

A atribuição funciona com o grupo vazio, e é isso que faz o acesso estar pronto sem os
nomes: quando eles chegarem, dar acesso é `create-group-membership` — não é mudança de
política nem nova revisão. O runbook está no README daquela pasta.

**31 casos simulados** com `simulate-principal-policy`, todos conforme o desenho: invoca
Anthropic e reinicia `agent-*`; não lê o lake, não lê o segredo do MPI, não altera o
`cibseven`, não registra task definition, não liga log de invocação, não invoca modelo de
outro fornecedor.

Achado que muda como se testa política nesta organização: **sem passar
`aws:RequestedRegion` e `aws:SecureTransport` no `--context-entries`, o simulador nega
tudo** — a SCP `deny-region-outside-allowlist` usa `StringNotEquals`, que é verdadeiro
sobre chave ausente. A primeira rodada "passou" em todas as negativas por esse motivo, ou
seja, não provou nada sobre a política escrita. Confira sempre `OrganizationsDecisionDetail`
para saber se quem negou foi a organização ou a sua política.

### Pré-requisito — FECHADO

O `demo` do CIB Seven não existe mais, e com ele foram `john`, `mary`, `peter`, os grupos
`accounting`/`management`/`sales` e os dois deployments do showcase. O motor tem hoje um
único usuário, `maezoadmin`, com senha no Secrets Manager (`maezo/dev/cibseven/admin`) e
autenticação verificada por `POST /identity/verify`.

**O que quase passou:** apagar usuário no Camunda **não** apaga as autorizações dele.
Sobraram 15, entre elas uma dando a `mary` READ e UPDATE em `task` com alvo `*` — todas as
tarefas do motor, incluindo as do fluxo AUTH. Não é sujeira: é armadilha armada, porque
quem um dia criasse um usuário com aquele id herdaria a permissão sem ninguém conceder
nada. Todas removidas; o estado final confere **0 órfãs**.

Sobra uma pendência cosmética e nomeada: os 2 filtros do showcase (`My Tasks`,
`My Group Tasks`) continuam no banco. O `engine-rest` recusa apagá-los sem usuário
autenticado (403, inclusive com Basic auth — o filtro de autenticação da distribuição não
está ligado) e eles ficaram inertes, sem nenhuma autorização que os conceda. Somem com um
clique na Tasklist do admin.

### Cockpit: o grupo `maezoleitura`

Fechar o `demo` criou o problema seguinte: com `maezoadmin` como único usuário, convidar
alguém ao Cockpit significaria compartilhar a senha do administrador. Então o motor ganhou
`maezoleitura`, vazio, com ACCESS em cockpit/tasklist e READ nas definições, instâncias,
tarefas e deployments — sem UPDATE, sem CREATE, sem DELETE e sem a aplicação `admin`.
Mesmo desenho dos grupos da AWS: permissão revisada agora, pessoa depois.

São **três portões independentes** — Cloudflare Access (alcança o hostname), Identity
Center (opera a AWS), engine (entra no Cockpit). Uma pessoa precisa dos três. É
deliberado, mas significa que "dar acesso" são três passos, não um.

### O que eu preciso para seguir

1. **Nomes e e-mails** do time — é o único passo que falta na Onda 1.
2. **Confirmar o MFA do Identity Center** antes do primeiro convite. Não há API nem
   recurso Terraform para isso (conferido em `aws sso-admin` e `aws identitystore`): é
   console, _Settings → Authentication_. O portal é público e a conta guarda 11,45M de
   recursos FHIR.
3. **Qual modelo é o tier `fast`** — haiku 4.5 (mais barato, suficiente para sumarizar
   fatos estruturados) ou sonnet 5 (meio).
4. **Se a Onda 3 avança agora** ou depois — ela depende de decisão de conteúdo clínico, e
   o resto do plano não a espera.

_Método: cada afirmação de estado foi medida contra a conta 203312548462 em 18/08/2026 —
`bedrock list-guardrails`, `get-model-invocation-logging-configuration`,
`list-inference-profiles`, `iam get-role-policy`, `sso-admin list-permission-sets`,
`identitystore list-groups` — e cada afirmação de código, por leitura direta do arquivo
citado._
