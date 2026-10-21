# Lucas do catálogo v4 (dev) — `MAEZO_LUCAS_V4`

**Status:** SÓ no ambiente de desenvolvimento (dev). Decisão do dono (catálogo v4, 15–16/10/2026), registrada no
`docs/decisions-log.md` (DL-0114). Os textos são **rascunho aprovado pelo dono para dev**; **revisão jurídica e do
DPO antes de produção**. O código de barras da 2ª via (credencial de pagamento) só liga com os contratos novos da
AMH publicados e pinados — **hoje não liga** (seção 2).

## 1. O que é, em linguagem simples

O catálogo v4 (abas "1 Necessidades" e "Exemplos de SQL", linhas "Mensalidade: valor, vencimento, se está paga,
coparticipação" e "2ª via do boleto") ensina o Lucas a responder só a quem pode pedir. As regras do dono:

1. **Nunca** mostrar o nome de outro beneficiário nem do pagador. Só a relação: "você", "titular",
   "dependente 1", "dependente 2"…
2. **Só o pagador** (o responsável financeiro do contrato) pede valores, vencimento, situação do pagamento e 2ª
   via. Plano de **empresa** → o Lucas orienta o RH da empresa contratante (já era assim). **PF que não é o
   pagador** → "O responsável financeiro pelo contrato é o titular do contrato. Somente ele pode solicitar essa
   informação." (sem nome; sem valor; **sem abrir caso**, mesmo que o título do responsável esteja atrasado).
3. **Quando é o pagador:** primeiro o **valor total** da mensalidade (soma de todos os beneficiários do contrato)
   e, em seguida, o **detalhe por beneficiário sem nome**: "Titular: pré-estabelecido R$ X + coparticipação R$ Y
   (+ outros R$ Z) = R$ T", "Dependente 1: …".
4. **2ª via:** o pagador recebe o **código de barras** do boleto e a orientação de baixar o PDF pelo aplicativo
   Austa Clínicas. Sem boleto: "Não encontrei o código de barras deste boleto. Você consegue a 2ª via pelo
   aplicativo Austa Clínicas, pelo portal do plano ou pela central."

## 2. O que funciona JÁ e o que depende dos contratos novos

| Comportamento | Depende de | Hoje (chave ligada) |
|---|---|---|
| PF que não é o pagador ouve o texto do responsável financeiro, sem valor, em valor/vencimento/situação/2ª via; atraso do título do responsável nunca abre caso | billing-status **0.3.0** (`beneficiario_e_pagador`, já no ar em dev) **lido pelo receptor**: exige `amh_billing_status_contratacao_openapi_path` (`lucas.auto.tfvars`; sem ele a cobrança lê o 0.2.0, sem o sinal) | **funciona** com o caminho; **sem o sinal, FALHA FECHADA** (ver 2.1) |
| Plano de empresa → RH (e adesão → administradora) | billing-status 0.3.0 (`pagador_tipo`, `tipo_contratacao`) | **funciona** (igual a antes) |
| Pagador ouve o **valor total primeiro** e depois a parte dele | billing-status 0.3.0 (`valor_boleto_pagador`) | **funciona** |
| Pagador ouve o **detalhe por beneficiário** (sem nome) | billing-status **0.4.0** (manifest v1.10, `incluir_detalhamento`/`somente_pagador`) | espera o contrato (cai no "total primeiro") |
| Pagador recebe o **código de barras** | billing-segunda-via **0.1.0** (manifest v1.11, contrato separado) | espera o contrato (cai no texto de antes: os canais da 2ª via) |

### 2.1 Falha fechada (achado do aceite de 16/10/2026)

Só o sinal `beneficiario_e_pagador = true` (billing-status 0.3.0) com `pagador_tipo = pessoa_fisica` comprova que a
pessoa É o pagador. Com a chave ligada, **valor, vencimento, situação do pagamento, "já paguei?", "é a última?" e 2ª via
só saem com esse sinal**:

- sinal `false` (dependente) -> "O responsável financeiro pelo contrato é o titular do contrato. Somente ele pode
  solicitar essa informação.";
- sinal ausente, nulo, não booleano ou pagador desconhecido -> "Não consegui confirmar se você é o responsável
  financeiro do contrato, então não mostro valores por aqui" + os canais (aplicativo, portal, central). Não afirma quem
  é o responsável e **não abre caso**;
- empresa (RH) e adesão (administradora) seguem com o texto próprio de antes.

Causa do achado: a task do dev não tinha `MAEZO_AMH_BILLING_STATUS_CONTRATACAO_OPENAPI_PATH` (variável opcional, vazia
por padrão), então a cobrança lia o 0.2.0 (sem `beneficiario_e_pagador`) e um dependente recebeu valores como pagador.
Agora a variável é exigida pela validação de `lucas_v4` no Terraform e o receptor loga `lucas_v4_sem_billing_status_0_3_0`.

**O segundo sinal (`fonte_detalhamento_habilitada`).** Os dois contratos novos estão **UNPUBLISHED** na AMH (PRs
#245–#249 de `amh-data-platform`). O código só liga o detalhamento e o código de barras quando, ao mesmo tempo:
(a) `MAEZO_LUCAS_V4` está ligada; (b) o pin `config/integrations/amh/contracts.lock.json` traz os blocos
`manifest_v1_10` e `manifest_v1_11` com os dois artefatos; (c) a fonte de cobrança é `amh` e o acesso do
beneficiário está ligado (identidade verificada); (d) existe o **executor governado do gateway** para as duas
rotas (`interop.executor_v4`), que **ainda não foi escrito**. Sem isso o Lucas v4 faz só o que a rota 0.3.0 sustenta
e o boot registra `lucas_v4_sem_contratos_novos` (ou `..._sem_executor`). Nada falha: o comportamento de cobrança é o
de antes nos pontos que dependem dos contratos.

## 3. A chave

| | |
|---|---|
| Nome | `MAEZO_LUCAS_V4` (setting `lucas_v4`, `src/maezo/platform/webhooks/whatsapp/settings.py`) |
| Padrão | `false` — **desligada = o Lucas de antes, byte a byte** (provado em `tests/unit/agents/test_lucas_v4.py`, inclusive com os fatos novos plantados no estado) |
| Onde liga no dev | `deploy/aws-ecs/envs/dev-sa-east-1/lucas.auto.tfvars` (`lucas_v4 = true`) |
| Variável Terraform | `lucas_v4` (`variables.tf`, default `false`; **exige** `lucas_consulta_valores = true`, validado aqui e no boot) |
| Depende de | `MAEZO_LUCAS_CONSULTA_VALORES` (e, por ela, `MAEZO_ACESSO_BENEFICIARIO` + fonte `amh`): sem os fatos de valor não há como saber quem é o pagador |
| Vai para | o receptor do WhatsApp (`service-webhook-receiver.tf`, `environment_app`) e, pelo mesmo contrato, as tasks do simulador e do lembrete |

## 4. Recursos Terraform afetados

A linha `{ name = "MAEZO_LUCAS_V4", ... }` mora em `local.webhook_receiver_contrato.environment_app`, herdado por
três recursos (todos em `deploy/aws-ecs/envs/dev-sa-east-1`):

- `aws_ecs_task_definition.webhook_receiver` — nova revisão (ganha `MAEZO_LUCAS_V4=true`);
- `aws_ecs_service.webhook_receiver` — passa a usar a nova revisão (atualização no lugar);
- `aws_ecs_task_definition.simulador` — nova revisão (o simulador compõe o mesmo receptor);
- `aws_ecs_task_definition.lembrete_cobranca["this"]` — só se `var.lembrete_cobranca` estiver definido.

**Ligar (apply direcionado, como nas outras chaves).** A linha `lucas_v4 = true` já está em `lucas.auto.tfvars`.
```
cd deploy/aws-ecs/envs/dev-sa-east-1
terraform plan  -target=aws_ecs_task_definition.webhook_receiver -target=aws_ecs_service.webhook_receiver \
                -target=aws_ecs_task_definition.simulador -var=... (as tags de imagem de sempre)
terraform apply -target=aws_ecs_task_definition.webhook_receiver -target=aws_ecs_service.webhook_receiver \
                -target=aws_ecs_task_definition.simulador -var=...
```
Confira no plan que a **única** diferença de ambiente é `MAEZO_LUCAS_V4` (e a tag da imagem, se também mudou).
Não há processo BPMN/DMN novo: nenhum `deploy-processes` é necessário.

**Desligar:** `lucas_v4 = false` no `lucas.auto.tfvars` + o mesmo apply direcionado (os mesmos recursos). O Lucas
volta ao comportamento de antes (valor da parte, "boleto da família" depois, dependente com o texto do titular).
Em emergência: `-var=lucas_v4=false` com os mesmos `-target`, e depois registre no arquivo.
**Desfazer de vez:** `git revert` do PR + build + o mesmo apply (variável e env somem).

## 5. A credencial de pagamento (código de barras)

O código de barras é **credencial de pagamento**: quem o tem paga — ou desvia — o boleto. Por isso:

- **Só ao pagador verificado** (consentimento + identidade do acesso do beneficiário): o resolvedor da fonte v4 tem
  de ser o da identidade verificada (`FonteV4` recusa no boot qualquer outro); só o pagador PF (`beneficiario_e_pagador`
  do billing-status) chega a consultar a rota; a AMH repete a regra no servidor (404 idêntico para quem não é o
  pagador, empresa, adesão e sem boleto).
- **Contrato separado, escopo próprio** (`interop/billing.boleto`), `X-Amh-Request-Ref` opaco (pseudônimo da
  entrega), **sem** referência de consentimento na query.
- **Nunca no modelo, no estado, no log ou na métrica.** O texto é **fixo** (nunca o modelo de linguagem). O grafo
  escreve o **marcador** `{{CODIGO_DE_BARRAS}}`; o código vive só em `CredenciaisDoTurno` (repr redigido, não
  serializável) e `LucasTurno` o põe no texto **no último passo**, dentro do remetente do turno, depois do gate e da
  cerca de saída. Sem credencial, o remetente envia o texto honesto de "não encontrei" — nunca o marcador cru.
- **Checkpoint e histórico (decisão):** o Lucas **não tem checkpointer** (o grafo compila `checkpointer=None`; a
  continuidade é só a memória curta do roteador, migration 0025). O que o turno grava é a **referência** do texto
  (16 hex do sha256) — calculada sobre o texto **com o marcador**, nunca com o código. O estado final que o turno
  devolve ao despachante e a mensagem do grafo também têm só o marcador. Provado em
  `tests/unit/platform/webhooks/whatsapp/test_lucas_turno_v4.py`.
- **A resposta sai sempre inteira** (nunca a escada de repetição, que diria "não envio o boleto" contradizendo o
  código) e o PDF **não** é enviado: orienta o aplicativo Austa Clínicas.
- **Relatório do simulador:** o mascarador troca linha digitável, 44 dígitos e qualquer sequência de 20+ dígitos
  por `[CODIGO-DE-BARRAS]` (antes do CPF, que morderia um pedaço); um cinto em `como_json` recusa gravar o
  relatório se algo escapar.

## 6. Como testar

- **CI:** `tests/unit/agents/test_lucas_v4.py` (grafo: desligada = antes, não pagador, total primeiro, detalhe,
  2ª via, régua de tom, cerca), `tests/unit/agents/test_lucas_fonte_v4.py` (adaptadores com executor FAKE, credencial,
  segundo sinal), `tests/unit/platform/webhooks/whatsapp/test_lucas_turno_v4.py` (quem recebe o código e onde ele
  vive), `tests/unit/platform/webhooks/test_lucas_v4_chave_e_composicao.py` (chave, Terraform, composição),
  `tests/unit/qa/test_simulador_lucas_v4.py` (roteiros, rigor, mascaramento).
- **Roteiros de aceite** (`src/maezo/qa/simulador/aceite.py`; `dono_v1` e `cancelamento_v1` não mudaram), com a chave
  ligada, na task do simulador (`docs/runbooks/simulador-beneficiario.md`), critério **"RESOLVEU?"** por segmento:

  | Roteiro | Quem conversa | O que confere |
  |---|---|---|
  | `lucas_v4` | o dono (CPF 42853296890, `s000`, plano **empresarial**) | "Quanto está minha mensalidade?", "Qual o código de barras do meu boleto?", "Quando vence…?" → texto da empresa/RH, **sem** valor, vencimento nem código |
  | `lucas_v4_pagador` | primeiro titular PF do roster que resolveu na AMH (não o dono) | total **primeiro**; sem o sinal, a 2ª via é o texto atual (canais), sem código; com o sinal, o detalhe por beneficiário e o **código de barras** (passo `pendente_de_contrato` enquanto o sinal estiver desligado) |
  | `lucas_v4_nao_pagador` | idem, com um roster de **não pagadores** | o texto do responsável financeiro em valor, 2ª via, vencimento e "foi paga?", sem valor e sem nome |

  Os segmentos têm `rigor_v4`: nenhum nome/CPF/nascimento do beneficiário, nenhuma palavra proibida ("multa",
  "juros"…), **R$ e datas do Lucas dentro dos fatos do turno** (a D2 do simulador, com os fatos v4 gravados por
  `FonteV4Gravada`) e **nenhum código de barras** fora do passo que o espera. Exemplo:
  `SIMULADOR_ARGS="--roteiro lucas_v4_pagador --roster <chave do roster PF>"`.
- **Como escolher o sujeito sem expor PII.** O roteiro usa o **primeiro** do roster que resolveu na AMH e não é o
  dono; o CSV fica só no S3 cifrado e na memória do processo, e o relatório usa rótulos e o mascarador. Para
  `lucas_v4_pagador`: monte um roster só de **titulares PF que são o próprio pagador, com mensalidade em dia**
  (consulta 1 de `src/maezo/qa/simulador/roster_lucas_v4.sql`); se o passo 1 reprovar com "responsável financeiro",
  o sujeito não é o pagador — troque o roster. Para `lucas_v4_nao_pagador`: roster de **dependentes** (consulta 2 do
  mesmo arquivo). O roster PF atual (`roster/20261015T221333Z-pf-titulares.csv`) tem titulares PF: serve ao
  pagador **se** o titular for o pagador; para o não pagador é preciso o roster de dependentes (**o SQL não foi
  validado no Athena; confirme os nomes de colunas antes de rodar**, e nada é alterado no lago).
- **Dev ponta a ponta (WhatsApp real):** com os números de teste do dono (PJ), de um titular PF pagador e de um
  dependente, todos verificados no acesso.

## 7. Ligar de verdade (o detalhamento e o código de barras) — estado em 16/10/2026

**LIGADO NO DEV** (decisão direta do dono — CTO, owner e DPO — em 16/10/2026; produção continua pendente de DPO e
jurídico). O que existe e como se liga:

1. **Contratos publicados e pinados** (AMH #245–#249 e #253): billing-status 0.4.0 (manifest v1.10) e
   billing-segunda-via (manifest v1.11), com os OpenAPI vendorizados em `config/integrations/amh/openapi/`.
2. **Executor governado** — `gateway/amh_interop.py::AmhV4Executor` (`interop.executor_v4` da composição,
   `tool_registry.build_amh_interop(incluir_v4=True)`): `GET` com token M2M e `purpose_of_use=atendimento_whatsapp`.
   Detalhamento: token do cliente de cobrança (`interop/billing.read`) com `incluir_detalhamento=true` e
   `somente_pagador=true`. **2ª via: cliente Cognito dedicado** (`maezo-operadora-interop-boleto`, único escopo
   `interop/billing.boleto`; segredo `amh/cognito/dev/maezo-operadora-interop-boleto`), `X-Amh-Request-Ref` opaco, resposta
   sem `Cache-Control: no-store` recusada, **corpo nunca em log/auditoria/repr**. Os bytes dos dois OpenAPI têm de bater com o
   digest do pin (`manifest_v1_10` e `manifest_v1_11`); senão o receptor recusa servir.
3. **Catálogo de ações:** `amh.get_billing_detalhamento` e `amh.get_billing_segunda_via` em `gateway/effect_classes.py`
   (classe `leitura_phi_clinica`, C2, `tool_id=None`, shadow) e em `spec/policies/autonomy/action-approvals.yaml`. O
   detalhamento usa a ação `read_member_billing_identity`; a 2ª via, a ação própria **`read_member_payment_credential`** (L3 em
   `L0-core.yaml`, declarada no `agent.yaml` do Lucas), para a governança poder desligá-la sem tocar a cobrança.
4. **Terraform** (`deploy/aws-ecs/envs/dev-sa-east-1`): `amh_billing_status_detalhamento_openapi_path`,
   `amh_billing_segunda_via_openapi_path` (em `amh-interop.auto.tfvars`) e `amh_interop_boleto_client_id`; só com
   `lucas_v4` **e** o client id preenchido entram na task definition as três envs e o segredo (`secrets`, execution role e CMK).
   Vazio = o Lucas v4 segue sem o segundo sinal (log `lucas_v4_contratos_pinados_sem_executor`).
5. **AMH:** rotas ligadas no interop dev (cobertura no serviço geral; 2ª via no **serviço dedicado**, papel e workgroup
   próprios), cliente/escopo no Cognito, grants do Lake Formation e **auditoria durável** (S3, antes da resposta).
6. **Sinal:** com tudo isso o boot loga `lucas_turno_construido ... fonte_detalhamento_habilitada=true`, e os passos
   `pendente_de_contrato` dos roteiros passam a valer (o relatório sai sem pendentes). O roteiro `lucas_v4_pagador` exige o
   total primeiro **e** o detalhe por beneficiário sem nome, e a 2ª via com o código no texto real (mascarado no relatório);
   `lucas_v4_nao_pagador`, sem valor nem código.

**Para produção, ainda falta:** ratificação do DPO (código de barras = credencial de pagamento) e do jurídico (textos, base
legal), e a decisão do dono de levar `lucas_v4` além do dev.

## 8. Logs

- Receptor (`/ecs/maezo-operadora-dev/webhook-receiver`): `lucas_turno_construido` (`lucas_v4`,
  `fonte_detalhamento_habilitada`); `lucas_v4_sem_contratos_novos`, `lucas_v4_contratos_pinados_sem_fonte_amh`,
  `lucas_v4_contratos_pinados_sem_executor` (boot); `lucas_v4_detalhamento_sem_fato` e `lucas_v4_segunda_via_sem_fato`
  (motivo de classe, nunca valor); `lucas_turno_fonte_v4_falhou` (classe da exceção). **Nenhum** log carrega valor de
  boleto, código de barras ou texto da resposta.

## 9. Pendências e riscos

- **Atraso crítico e 2ª via:** a DMN de admissibilidade abre caso humano para pagador PF com atraso crítico (≥ 2
  meses ou > 30 dias) **antes** da linha da 2ª via; esse pagador não recebe o código, recebe o caso (decisão do dono
  de 14/10/2026, não alterada aqui). Se o dono quiser o código também nesses casos, é decisão e mudança de DMN.
- **404 ambíguo:** a rota responde 404 igual a "sem boleto", "não é o pagador" e "rota desligada"; o Lucas diz "não
  encontrei o código de barras" nos três. Com a rota desligada no serviço, o pagador ouve isso (e os canais).
- **Texto do não pagador:** diz "é o titular do contrato" como o dono pediu. Se o beneficiário for o próprio
  titular e o pagador for um terceiro, o texto é impreciso; a relação `outra_pessoa` (billing-status 0.4.0) permite
  afinar quando o contrato existir (decisão do dono).
- **Classificação:** "Qual o código de barras?" vira `boleto_2via` pela Helena; se uma conversa real cair em outro
  assunto, o roteiro `lucas_v4*` reprova o passo (sinal para ajustar o léxico, não o Lucas).
- **Coparticipação do não pagador:** a regra "só o pagador pede valores" também cobre coparticipação e "foi paga?";
  se o dono quiser que o beneficiário veja a própria coparticipação, é uma exceção a decidir.
- **Cerca de saída:** as chaves monetárias do detalhamento (`valor_pre_estabelecido`, `valor_outros`,
  `total_mensalidade`…) entraram como fatos aceitos em `agents/lucas/prompts.py` e no gabarito D2 do simulador; os
  padrões da cerca (`recusa-lucas-v5`) não mudaram.
