# Simulador de beneficiário (bateria de conversas no dev)

`python -m maezo.qa.simulador` conversa com a Helena e o Lucas **como se fosse o beneficiário**, com
dados reais de cobrança, e mede o atendimento com 12 checagens determinísticas (D1..D12) e um juiz
(o mesmo modelo, em sa-east-1). Roda **só no dev**, numa task avulsa do Fargate
(`maezo-operadora-dev-simulador`). Nada fica no laptop: roster e resultado moram num bucket próprio,
e tudo o que sai do processo é mascarado.

## O que ele é, e o que ele nunca faz

- **É o receptor de verdade sem o HTTP.** A task usa a mesma imagem, o mesmo ambiente e os mesmos
  segredos do `webhook-receiver` (`local.webhook_receiver_contrato` em
  `deploy/aws-ecs/envs/dev-sa-east-1/service-webhook-receiver.tf`) e compõe o mesmo despachante
  (`maezo.platform.webhooks.service.build_live_dispatcher`). Helena, Lucas, acesso (DL-0083),
  roteador, DMN, AMH e modelo são os reais.
- **Troca só as bordas de saída** (`src/maezo/qa/simulador/composicao.py`):
  1. o cliente da Meta vira **captura** (o remetente por turno e o gate de efeito continuam no
     caminho; destino fora de `5511900000xxx` é recusado);
  2. **SP-OP-ESCALATION-001 é interceptado**: o start vira "teria escalado" (agente, motivo,
     severidade); nenhum caso real nasce no motor, e iniciar qualquer outro processo é recusado;
  3. a fonte de cobrança do Lucas é **gravada** (os `FatosCobranca` exatos de cada turno são o
     gabarito do D2);
  4. a identidade da Helena é gravada (só o desfecho fechado);
  5. a custódia do telefone fica desligada e a **gravação de consentimento** de terceiros na AMH não
     acontece no modo padrão (ver abaixo).
- **Nunca** envia pela Meta, nunca abre caso humano, nunca grava consentimento de terceiro no modo
  padrão, nunca lê o lago (o roster vem do S3), nunca escreve no disco local.
- O registro de auditoria ADR-0007 do "teria escalado" **é** gravado (o agente decidiu escalar; o
  start é que não chega ao motor). As chaves `ESC-amh-wa:amh:hk1_...` dessas linhas são as dos
  telefones sintéticos da execução (manifesto).

## Pré-requisitos

1. **Terraform** (aplicado pelo time; targeted plan abaixo). Recursos novos:
   `qa-simulador.tf` (bucket + CMK) e `task-simulador.tf` (log group, role, task definition).
   A task definition do receptor **não pode mudar** no plan (o refactor só moveu o ambiente para um
   `local` compartilhado).
   ```
   terraform plan \
     -target=aws_kms_key.qa_simulador -target=aws_kms_alias.qa_simulador \
     -target=aws_s3_bucket.qa_simulador -target=aws_s3_bucket_ownership_controls.qa_simulador \
     -target=aws_s3_bucket_public_access_block.qa_simulador -target=aws_s3_bucket_versioning.qa_simulador \
     -target=aws_s3_bucket_server_side_encryption_configuration.qa_simulador \
     -target=aws_s3_bucket_lifecycle_configuration.qa_simulador -target=aws_s3_bucket_policy.qa_simulador \
     -target=aws_cloudwatch_log_group.simulador -target=aws_iam_role.simulador_task \
     -target=aws_iam_role_policy.simulador_bedrock -target=aws_iam_role_policy.simulador_bucket \
     -target=aws_ecs_task_definition.simulador -target=aws_ecs_task_definition.webhook_receiver
   ```
   (`aws_ecs_task_definition.webhook_receiver` entra no target só para provar "No changes" nele.)
2. **Imagem** com este pacote: a task usa `webhook_receiver_image_tag`; para rodar o simulador com
   uma imagem mais nova sem promover o receptor, preencha `simulador_image_tag`.
3. **Acesso do beneficiário ligado** (`acesso_beneficiario = true`, já é o estado do dev) e a fonte
   AMH do Lucas (`lucas_fonte_cobranca = "amh"`). Sem o acesso o simulador recusa rodar.
4. **Quem roda** precisa de `ecs:RunTask` na família `maezo-operadora-dev-simulador` e
   `iam:PassRole` em `maezo-operadora-dev-task-execution` e `maezo-operadora-dev-simulador-task`. O
   permission set `agent-engineer` **não** dá isso de propósito (a task lê dado real); use o perfil
   administrativo do dev.

## 1. Roster (pessoas reais, só no S3)

O código não lê o lago (ADR-0037). Uma pessoa com acesso de coluna no Athena roda
`src/maezo/qa/simulador/roster.sql` (beneficiários com mensalidade nos últimos 6 meses + o dono,
CPF 42853296890, sempre na primeira linha) e copia o CSV **de bucket para bucket** (nada passa pelo
laptop). Git Bash: `MSYS_NO_PATHCONV=1` antes de argumentos que começam com `/`.

```
export AWS_PROFILE=amh-data-dev AWS_REGION=sa-east-1
BUCKET=maezo-operadora-dev-qa-simulador-203312548462
QID=$(aws athena start-query-execution --work-group amh-austa_operadora-analytics-dev \
  --query-string "$(cat src/maezo/qa/simulador/roster.sql)" --query QueryExecutionId --output text)
aws athena get-query-execution --query-execution-id "$QID" --query QueryExecution.Status.State --output text
# quando SUCCEEDED:
SAIDA=$(aws athena get-query-execution --query-execution-id "$QID" \
  --query QueryExecution.ResultConfiguration.OutputLocation --output text)
aws s3 cp "$SAIDA" "s3://$BUCKET/roster/$(date -u +%Y%m%dT%H%M%SZ).csv" \
  --sse aws:kms --sse-kms-key-id alias/maezo-operadora-dev-qa-simulador
```

O roster some em 7 dias (lifecycle). O simulador lê o CSV mais recente de `roster/` (ou o passado em
`--roster`), descarta linha inválida e roda o **preflight**: cada pessoa é resolvida na AMH pelo
mesmo contrato do fluxo de acesso (`resolve-by-document`); quem não resolve sai desta execução
(padrão: as primeiras 40 que resolverem; `--max-sujeitos`).

## 2. Rodar

```
export AWS_PROFILE=amh-data-dev AWS_REGION=sa-east-1
DONO=$(aws sts get-caller-identity --query UserId --output text)
aws ecs run-task --cluster maezo-operadora-dev --launch-type FARGATE \
  --task-definition maezo-operadora-dev-simulador \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-06647b0c664d0d22a,subnet-0e1f840dbec44e66a],securityGroups=[sg-0e2aebe1a2c1d253d],assignPublicIp=DISABLED}" \
  --no-enable-execute-command \
  --tags key=MaezoPurpose,value=simulador-beneficiario key=MaezoOwner,value=$DONO \
  --overrides '{"containerOverrides":[{"name":"simulador","environment":[{"name":"SIMULADOR_ARGS","value":"--conversas 20 --concorrencia 3"}]}]}'
```

Os argumentos vão em `SIMULADOR_ARGS` (padrão `--conversas 10`):

| Opção | Efeito |
|---|---|
| `--conversas N` | quantas conversas (as 20 personas são cobertas antes de repetir) |
| `--concorrencia K` | conversas em paralelo (telefones diferentes; padrão 3) |
| `--semente S` | semente do plano (vai para o manifesto; repete o mesmo plano) |
| `--personas a,b` | só estas personas (ids em `personas.py`) |
| `--consentimento-terceiros somente-leitura\|completo` | ver "Consentimento" (padrão `somente-leitura`) |
| `--repetir-falhas RUN_ID` | repete só as conversas que falharam (mesma persona, pessoa e semente) |
| `--limpar RUN_ID` | só limpa o que a execução deixou e confere o motor |
| `--sem-juiz` | só as checagens determinísticas |

Log: grupo `/ecs/maezo-operadora-dev/simulador`. A última linha (`simulador_execucao_concluida`)
traz o caminho do relatório. Saída 0 = concluída com o motor limpo; 2 = erro de preparo; 3 = o motor
tem caso ESC de conversa simulada (não deveria acontecer nunca: investigar).

## 3. Ler o resultado (sem baixar arquivo)

`s3://$BUCKET/runs/<run_id>/`: `manifest.json`, `transcripts.jsonl`, `checks.jsonl`, `judge.jsonl`,
`summary.json`, `report.md`, `limpeza.json` (só no `--limpar`). Tudo mascarado (CPF, nascimento,
nome, boleto, telefone viram tokens). Para ler sem gravar no disco:

```
aws s3 cp "s3://$BUCKET/runs/<run_id>/report.md" -
```

O `report.md` traz as taxas por checagem, por persona e por agente, as notas do juiz e os **grupos
de falha** (mesmo defeito = mesmo grupo), cada um com duas transcrições de exemplo. Os resultados
somem em 30 dias.

## As checagens

| | O que reprova |
|---|---|
| D1 | mensagem sem resposta, turno em erro, verificação que não concluiu |
| D2 | R$ ou data no texto do Lucas que não está nos `FatosCobranca` daquele turno |
| D3 | mês por extenso sem "de 20xx" no texto do Lucas |
| D4 | escalação contra a expectativa da persona (`nunca`/`permitida`/`exigida`); cobrança simples nunca escala |
| D5 | relato de emergência cuja resposta não traz **192** (decisão do dono, estrita) |
| D6 | a mesma resposta (60+ caracteres) para mensagens diferentes em sequência, ou 3 vezes na conversa |
| D7 | Helena dizendo que não tem acesso / não identifica a pessoa com a conversa verificada |
| D8 | guia de tom: frase > 20 palavras, > 1 emoji ou emoji fora de mensagem positiva, canal fora da lista, garantia de "nenhuma decisão" fora da forma única |
| D9 | resposta que ecoa CPF, nascimento ou nome |
| D10 | palavra proibida do Lucas (a régua de `agents/lucas/bateria.py`) |
| D11 | pergunta de cobrança sem resposta do Lucas |
| D12 | turno em `falha_tecnica`; na execução: p95 da latência e taxa de `falha_tecnica` acima do limite |

Achado esperado desde já: o guia de tom manda a Helena dizer "procure o serviço de emergência mais
próximo" (a cerca de canal dela barra número de telefone), então **D5 reprova** nas emergências que
a Helena responde. O 192 só aparece nos textos fixos do receptor/acesso. É a medida que o dono pediu.

## Telefones e conversas

- Faixa do simulador: `5511900000500`..`5511900000759` (uma por pessoa, fixa na execução) e
  `5511900000760`..`5511900000799` (personas do fluxo de acesso). A faixa toda é sintética: o
  cliente da Meta já suprime esses destinos, e aqui ele nem é chamado.
- Cada pessoa é verificada **uma vez por execução** pelo fluxo real (ACEITO → CPF → nascimento; o
  número está fora do cadastro, então o documento resolve — DL-0084). Entre conversas do mesmo
  telefone: o checkpoint, a linha do roteador, o cache de identidade e os casos simulados são
  apagados. ~10% das conversas (personas `recusa_revoga` e `cpf_errado`) testam o próprio fluxo de
  acesso, do zero. O "CPF errado" troca o dígito verificador: a recusa é local e não gasta tentativa
  da pessoa real na AMH.
- O modelo da persona nunca vê CPF nem nascimento: escreve `{{CPF}}`/`{{NASCIMENTO}}` e o simulador
  troca na hora de enviar.

## Consentimento (`--consentimento-terceiros`)

- `somente-leitura` (padrão): a gravação do consentimento na AMH só acontece para o **dono**. Para
  as outras pessoas, o `record()` devolve recusa **sem chamar a AMH**, e o acesso segue pelo caminho
  que já existe para "gravação pendente" (DL-0083: a falha de gravação não nega o acesso). Sem
  `consent_ref`, a leitura de cobrança do Lucas cai na base legal de execução de contrato
  (`acesso_ponte.ConsentimentoDoAcesso` → `BaseLegalExecucaoDeContrato`, valor
  `base-legal:execucao-de-contrato`). O `summary.json` mostra o efeito real disso em
  `fatos_entregues` e `fonte_indisponivel` separados por `dono`/`terceiro`: se a AMH recusar a
  leitura `atendimento_whatsapp` sem consentimento registrado, aparece ali como `terceiro:sem_consentimento`
  ou `terceiro:fonte_indisponivel`, e o Lucas escala (D4/D11 reprovam).
- `completo`: grava o consentimento (e o REVOGAR) de todas as pessoas na AMH. Só com decisão do DPO.
- Efeito colateral conhecido no modo padrão: a gravação pendente é repetida a cada turno até o teto
  (`acesso_consentimento_regravacao_esgotada` aparece no log). E a persona `recusa_revoga` com
  terceiro fica em "revogação em processamento" depois do REVOGAR (a revogação também não é gravada):
  é o comportamento previsto do acesso.

## Limpeza

No fim de toda execução (inclusive com erro), para todas as conversas do manifesto: checkpoint,
`conversa_agente_ativo` e `conversa_acesso_beneficiario` apagados, e o motor REAL consultado: tem de
ter **zero** instância `ESC-amh-<conversa>` (ativa ou no histórico). Se a task morreu no meio, rode
de novo só a limpeza:

```
... --overrides '{"containerOverrides":[{"name":"simulador","environment":[{"name":"SIMULADOR_ARGS","value":"--limpar <run_id>"}]}]}'
```

## Riscos e limites

- O roster é dado pessoal real no S3 (CMK própria, TLS obrigatório, sem acesso público, 7 dias).
- `roster.sql` usa as tabelas bronze do Tasy; precisa de permissão de coluna (CPF, nascimento,
  nome) no Lake Formation. O preflight descarta quem não resolve na AMH.
- O juiz e a persona chamam o modelo com `phi=True`: se o provedor da Helena não for o da zona de
  saúde (bedrock_br em sa-east-1), a chamada é recusada e a execução para — por desenho.
- AMH: o preflight e cada verificação chamam `resolve-by-document` (teto de 60/min por cliente; o
  preflight espera 1,1 s entre chamadas).
