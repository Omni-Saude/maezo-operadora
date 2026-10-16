# Cancelamento a pedido do beneficiário pessoa física (dev): `MAEZO_CANCEL_PF_V1`

**Status:** SÓ no ambiente de desenvolvimento (dev). Aprovado pelo dono em 15/10/2026 para uso em dev
(`docs/decisions-log.md`, DL-0111). **Revisão jurídica/regulatória (RN 593/RN 412), DPO e operação antes de
produção.** Contrato: `docs/processes/contracts/SP-OP-CANCEL-001.md` v0.2.0 (continua rascunho, sem sign-off
de promoção).

## 1. O que é, em linguagem simples

O beneficiário de plano **individual ou familiar** pede pelo WhatsApp para cancelar o plano. O Lucas:

1. lê na AMH (contratos já pinados, nenhum campo novo) se a pessoa é **titular**, o **tipo de contratação**
   e se o vínculo está **ativo**;
2. se for **plano de empresa**, responde que quem decide é a empresa (RH); se for **adesão**, a administradora
   (como já era); se for **dependente**, explica que o pedido é feito pelo titular e oferece uma pessoa — em
   nenhum desses casos abre processo;
3. se for o **titular**, pergunta UMA vez ("Você quer pedir para cancelar o seu plano? Se sim, responda: SIM,
   QUERO CANCELAR.") e, confirmado, abre o processo SP-OP-CANCEL-001 com os **fatos reais** e responde:
   "Recebemos o seu pedido de cancelamento. Protocolo: CAN-XXXX-XXXX. A nossa equipe vai concluir o pedido.
   Quando terminar, você recebe a confirmação."

No processo:

- com os fatos confirmados (titular PF verificado, vínculo ativo), a regra (`cancel_admissibility`) manda o
  pedido direto para a **tarefa de execução** da equipe de **cadastro** (`UT_ExecutarCancelamentoCadastro`,
  grupo `cadastro-beneficiarios`) — o Maezo **não grava no Tasy** (ADR-0013): quem cancela no Tasy é a
  pessoa do cadastro;
- qualquer dúvida (titularidade não verificada, vínculo não ativo, tipo não afirmado) vai para a **análise
  humana** do jurídico (`UT_AnaliseRescisao`), como antes. Ninguém nega nem rescinde por automação (L0);
- a pessoa do cadastro conclui com **EXECUTADO** ("cancelamento executado no sistema") ou **IMPEDIDO** (volta
  ao jurídico). Prazo da execução: **5 dias** (provisório, **a confirmar pelo jurídico**); estourou, a
  coordenação (`coordenacao-contratos`) assume a mesma tarefa;
- **retorno ao beneficiário** (pela retomada, `agent-resume`, com o texto fixo e o histórico do motor
  conferido):
  - executado: "Seu plano foi cancelado a partir de DD/MM/AAAA. Protocolo: CAN-XXXX-XXXX. Se precisar de
    algo, é só responder esta mensagem." (data = data do pedido — RN 412, **a confirmar pelo jurídico**);
  - jurídico pediu informação: "Sobre o seu pedido de cancelamento (protocolo …): a nossa equipe precisa de
    mais informações. Se quiser falar com uma pessoa da equipe, é só pedir aqui.";
  - jurídico decidiu manter: "… a nossa equipe analisou e o seu plano continua ativo. Se quiser entender, é só
    pedir aqui para falar com uma pessoa da equipe."

Textos aprovados pelo dono para dev em 15/10/2026; revisão jurídica antes de produção. O protocolo é derivado
do id da instância do motor (sem dado pessoal): o mesmo pedido, o mesmo protocolo.

**Janela de 24 h da Meta:** o retorno só sai se a última mensagem da pessoa tiver menos de 24 h. Fora disso,
nada sai pelo WhatsApp (precisaria de template aprovado) e a equipe recebe o aviso para contatar por outro
canal. Para o teste em dev, conclua a tarefa dentro de 24 h da última mensagem.

## 2. A chave

| | |
|---|---|
| Nome | `MAEZO_CANCEL_PF_V1` (setting `cancel_pf_v1`, `src/maezo/platform/webhooks/whatsapp/settings.py`) |
| Padrão | `false` — **desligada = o Lucas de antes, byte a byte** (o pedido abre o processo com fatos `false` e tudo vai à análise humana; o aviso ao beneficiário nunca sai) |
| Onde liga no dev | `deploy/aws-ecs/envs/dev-sa-east-1/lucas.auto.tfvars` (`cancel_pf_v1 = true`) |
| Variável Terraform | `cancel_pf_v1` (`variables.tf`, default `false`; exige `lucas_fonte_cobranca = "amh"`) |
| Vai para | o receptor do WhatsApp (`service-webhook-receiver.tf`) e, pelo mesmo contrato de ambiente, a task do simulador |
| Depende de | `lucas_fonte_cobranca = "amh"`; para o titular: `helena_consultas_amh = true` (contrato TINA na composição) e `acesso_beneficiario = true` (identidade verificada). Sem TINA, todo pedido vai à análise humana. |

O BPMN v0.2.0, o consumidor de retomada e o worker **não** dependem da chave: sem as variáveis que só o Lucas
com a chave manda (`aviso_beneficiario_whatsapp`, `conversation_id`), nenhum aviso é publicado.

## 3. O que eu (dono) preciso executar na AWS — passo a passo

> Nada disto foi executado pelo PR. Rode na ordem.

### 3.1 Imagem e processos

1. Merge do(s) PR(s) e build normal da imagem (CD).
2. **Publicar o BPMN novo**: task `maezo-operadora-dev-deploy-processes` com a imagem nova
   (`docs/runbooks/aws-ecs.md`, passo 5). Sobe a versão nova de `SP-OP-CANCEL-001` (instâncias vivas
   continuam na versão antiga). Anote o `process_definition_id` novo (`SP-OP-CANCEL-001:<versão>:<deployment>`):
   ```bash
   AWS_PROFILE=adm-dev scripts/ops/bateria_vpc_run.sh scripts/ops/cancel_pf_dev.py MODO=listar
   # e, para a definicao, no mesmo estilo: GET /engine-rest/process-definition/key/SP-OP-CANCEL-001
   ```
3. **Serviços com a imagem nova** (o código mudou neles): `worker-runtime` (grava a data de efeito),
   `agent-resume` (retorno ao beneficiário), `webhook-receiver` (Lucas), a task do simulador, o **motor
   `cibseven`** (plugin humano com o binding `cancel_execucao` — ANTES do catálogo da seção 3.3) e o `portal`
   (formulário de conclusão, PR do portal).

### 3.2 Ligar a chave (apply direcionado)

Recursos Terraform afetados pela chave:

- `aws_ecs_task_definition.webhook_receiver` — nova revisão (ganha `MAEZO_CANCEL_PF_V1=true`);
- `aws_ecs_service.webhook_receiver` — passa a usar a nova revisão (atualização no lugar);
- `aws_ecs_task_definition.simulador` — nova revisão (o simulador compõe o mesmo receptor);
- `aws_ecs_task_definition.lembrete_cobranca["this"]` — só se `var.lembrete_cobranca` estiver definido
  (hoje não está no dev).

```bash
cd deploy/aws-ecs/envs/dev-sa-east-1
terraform plan  -target=aws_ecs_task_definition.webhook_receiver -target=aws_ecs_service.webhook_receiver \
                -target=aws_ecs_task_definition.simulador -var=... (as tags de imagem de sempre)
terraform apply -target=aws_ecs_task_definition.webhook_receiver -target=aws_ecs_service.webhook_receiver \
                -target=aws_ecs_task_definition.simulador -var=...
```

Confira no plan que a ÚNICA diferença de ambiente é `MAEZO_CANCEL_PF_V1` (e a tag da imagem, se mudou).

### 3.3 Portal: admitir as tarefas do cancelamento e o grupo `cadastro-beneficiarios`

A fila do portal só mostra pares (definição do processo, tarefa) **admitidos** no material staff assinado
(segredo `staff-job`: `job/task-catalog.json` + `job/task-admission.json`), e o motor só aceita a tarefa que
tem binding compilado no plugin humano. `UT_AnaliseRescisao`/`UT_CoordenacaoCancelamento` usam o formulário
`cancel_decisao` (só leitura no portal); este PR acrescenta o formulário `cancel_execucao` (`BPMN_FORMDATA`, um
campo fechado `execucao_cadastro`) para `UT_ExecutarCancelamentoCadastro`/`UT_CoordenacaoExecucaoCadastro` no
contrato Python e no `COMPILED` do plugin Java. **Ordem obrigatória:** primeiro a imagem nova do motor (plugin
com o binding), depois o catálogo — um catálogo com tarefa que o plugin não conhece derruba TODAS as filas (503).
A conclusão pelo portal (botão) vem no PR do portal (seção 8); a leitura na fila não depende dele.

Depois do deploy do BPMN (o `process_definition_id` muda a cada versão), na próxima renovação do material
(`docs/runbooks/renovar-material-staff-dev.md`, seções 4 a 7 — a janela atual vence em 22/10/2026):

1. **Catálogo** (`job/task-catalog.json`): acrescentar uma entrada por tarefa, com o `process_definition_id`
   e o `process_definition_digest` (sha256 dos bytes do BPMN publicado) da versão nova:
   - `UT_AnaliseRescisao`, `UT_CoordenacaoCancelamento`: `form_key=cancel_decisao`,
     `form_source_status=BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY`, `group_domain.kind=static`, grupos
     `juridico-contratos`, `gestao-contratos` (e `coordenacao-contratos` para a coordenação);
   - `UT_ExecutarCancelamentoCadastro`: `form_key=cancel_execucao`, `form_source_status=BPMN_FORMDATA`,
     `allowed_inputs=["execucao_cadastro"]`, grupo `cadastro-beneficiarios`; `UT_CoordenacaoExecucaoCadastro`:
     o mesmo formulário, grupo `coordenacao-contratos`. O catálogo também precisa do artefato do formulário em
     `forms[]` (`artifact_ref=cancel_execucao`, como `read-form-escalation` em `deploy/c1-local/c1/h1_task.py`).
   O `gerar` de hoje copia o catálogo anterior ("mesmo catálogo"): edite o JSON do catálogo no `estado/` da
   renovação antes de rodar (o `review_catalog` exige que catálogo e admissão cubram exatamente os mesmos
   pares e que os grupos admitidos estejam no `group_domain`).
2. **Admissão** (`job/task-admission.json`, bloco `human.entries[]`): uma entrada por tarefa
   (`process_definition_id`, `task_definition_key`, `candidate_groups` iguais aos do BPMN, `task_id_format`
   `uuid`, `identity_policy` e `classification` como as de `UT_TratarEscalonamento`).
3. Assinar (`python -m tools.staff_materials.approver sign-admission --catalog ...` e
   `sign-human-admission`), publicar os segredos, rodar `staff-rows` e o apply dos pins (seções 5 a 7 do
   runbook de renovação). Verificar: `tools.staff_ops task-read` mostra as tarefas do cancelamento.

### 3.4 Grupo e usuário de teste

`docs/runbooks/portal-dev-provisionamento.md` §3.8, com:

1. Cognito: `admin-create-user` (`--message-action SUPPRESS`, `email_verified=true`), por exemplo
   `cadastro.teste` (o username não pode ser e-mail; senha temporária de 7 dias).
2. Linha em `amh.portal_memberships` (dentro da VPC, `scripts/ops/run-db-task.ps1`), validada por
   `MembershipRecord.model_validate_json`: `principal_ref=prn_<32hex>` do usuário, `groups=["cadastro-beneficiarios"]`,
   o papel exigido pela entrada do catálogo (`required_roles`), `revision=1`. Para testar o jurídico,
   outro usuário com `juridico-contratos`.
3. O job T1.5 (`membership_publication_job`) publica a membership em até ~5 min.

Pessoas reais nos grupos ficam para a operação.

### 3.5 Concluir pelo portal (DL-0112) e o atalho de TESTE (só dev)

Com o PR do portal (DL-0112) e a imagem nova do portal, a pessoa do grupo `cadastro-beneficiarios` abre a
tarefa na fila e conclui com **"Cancelamento executado no sistema"** ou **"Impedido"** (conclusão direta do
DL-0049, já ligada no dev: `MAEZO_PORTAL_DIRECT_COMPLETION=true` em `service-portal.tf`). As decisões do jurídico
(`UT_AnaliseRescisao`) continuam só leitura no portal; para testar os retornos de informação/manter use o atalho
abaixo.

Atalho de teste

`scripts/ops/cancel_pf_dev.py`, rodado na task `bateria` (sem senha; o SG dela alcança o `engine-rest`, aberto
só em dev — decisão N5). Mesma natureza do atalho do DL-0049: sem assinatura, **só para teste**.

```bash
export AWS_PROFILE=adm-dev
# instancias vivas, tarefa aberta e protocolo de cada uma
scripts/ops/bateria_vpc_run.sh scripts/ops/cancel_pf_dev.py MODO=listar
# "cancelamento executado no sistema" (ou IMPEDIDO)
scripts/ops/bateria_vpc_run.sh scripts/ops/cancel_pf_dev.py MODO=executar TASK_ID=<id> RESULTADO=EXECUTADO
# juridico (teste dos retornos): SOLICITAR_INFO | MANTER | EFETIVAR_PEDIDO (nunca RESCINDIR/SUSPENDER)
scripts/ops/bateria_vpc_run.sh scripts/ops/cancel_pf_dev.py MODO=decidir TASK_ID=<id> DECISAO=SOLICITAR_INFO
# limpeza de uma instancia de TESTE (nenhuma mensagem sai)
scripts/ops/bateria_vpc_run.sh scripts/ops/cancel_pf_dev.py MODO=apagar INSTANCE_ID=<id>
```

## 4. Como DESLIGAR (voltar ao comportamento de hoje, sem tirar o código)

1. Trocar a linha para `cancel_pf_v1 = false` no `lucas.auto.tfvars` (PR pequeno).
2. O mesmo apply direcionado da seção 3.2.
3. O Lucas volta a abrir o processo com os fatos `false` (tudo na análise humana) e o texto antigo, e nenhum
   aviso ao beneficiário é publicado (as instâncias novas não têm `aviso_beneficiario_whatsapp`). Instâncias
   abertas com a chave ligada continuam e ainda avisam no fim (a variável está nelas): conclua ou apague as de
   teste (seção 3.5).

Emergência: `terraform apply ... -var=cancel_pf_v1=false` com os mesmos `-target`; depois registre no arquivo.

## 5. Como DESFAZER DE VEZ

1. `git revert` do(s) PR(s) (código, BPMN, contrato v0.2.0, testes, Terraform e este runbook saem juntos).
2. Build da imagem + `deploy-processes` (a versão anterior do BPMN volta a ser a mais nova publicada) + apply
   da seção 3.2.
3. A versão v0.2.0 do BPMN continua no motor (o CIB Seven não tem "undeploy" pelo nosso deploy); instâncias
   vivas nela seguem nela. Conclua/apague as de teste antes do revert.

## 6. Como testar

- **Unitários/integração (CI):** `tests/unit/agents/test_lucas_cancelamento_pf_v1.py` (chave ligada e
  desligada, titular, dependente, empresa, adesão, protocolo, textos, Terraform),
  `tests/unit/platform/webhooks/whatsapp/test_cancelamento_pf_retorno.py` (retorno conferido pelo histórico),
  `tests/unit/qa/test_simulador_cancelamento_v1.py` e `tests/integration/processes/test_sp_op_cancel_001.py`
  (motor real: execução no cadastro, prazo, impedido, avisos, invariante L0).
- **Roteiro de aceite `cancelamento_v1`** (`src/maezo/qa/simulador/aceite.py`), com a chave ligada:
  `SIMULADOR_ARGS="--roteiro cancelamento_v1 --roster <chave do roster PF>"`
  (`docs/runbooks/simulador-beneficiario.md`). Cenários: titular PF pede, recebe a pergunta única, confirma,
  recebe o protocolo e o próximo passo; pede de novo e recebe o mesmo caso; e o titular que desiste (com a
  pergunta pendente, "não"/"deixa pra lá" vai ao Lucas, que diz que nenhum pedido foi registrado e que pode
  retomar; a Helena não responde esse turno). Critério
  "RESOLVEU?" por segmento no relatório. **Nenhum cancelamento real**: o interceptador finge
  SP-OP-CANCEL-001 (com `instancia_de_teste=true`) e a limpeza do fim pergunta ao motor REAL se alguma chave
  CANCEL do roteiro tem instância viva (`cancelamentos_no_motor` tem de ser 0; se não for, o roteiro reprova).
  - **Beneficiário do roteiro:** o primeiro do roster que resolver na AMH e **não** for o dono (o contrato do
    dono é de empresa). Monte um roster SÓ com titulares de plano individual/familiar: rode o
    `src/maezo/qa/simulador/roster.sql` no Athena acrescentando o filtro de titular
    (`tasy_pls_segurado.nr_seq_titular IS NULL`) e o de contratação individual/familiar do contrato (a coluna do
    tipo de contratação em `tasy_pls_contrato` — **nome a confirmar no Athena**), e copie o CSV para o
    `roster/` do bucket do simulador como no runbook do simulador. O CSV fica só no S3 cifrado e na memória
    do processo; o relatório usa rótulos (`s1`) e o mascarador. Se a pessoa não for titular PF, o passo 1
    reprova sozinho (o Lucas responde RH/administradora/dependente).
- **Ponta a ponta no dev (WhatsApp real):** com um número de teste de um titular PF verificado: pedir o
  cancelamento, confirmar, conferir o protocolo; `MODO=listar` mostra a instância com o mesmo protocolo e a
  tarefa `UT_ExecutarCancelamentoCadastro`; concluir com `EXECUTADO` (portal, quando o PR do portal estiver no
  ar, ou seção 3.5) e conferir a mensagem "Seu plano foi cancelado a partir de …" no WhatsApp (dentro de 24 h).
  **Não use beneficiário real que não seja de teste** e apague a instância depois (`MODO=apagar`).

## 7. Logs

- Receptor (`/ecs/maezo-operadora-dev/webhook-receiver`): `lucas_turno_construido` com
  `cancelamento_pf_v1=true` e `fatos_do_cancelamento=tina|billing-status`;
  `lucas_turno_fatos_cancelamento_indisponiveis` (motivo de classe) quando a AMH não respondeu.
- Retomada (`/ecs/maezo-operadora-dev/agent-resume`): `agent_resume.cancel_notice_sent`,
  `agent_resume.cancel_notice_outside_meta_window`, `agent_resume.cancel_notice_undeliverable_team_alerted`,
  `agent_resume.cancel_notice_without_port`.

## 8. Pendências e riscos

- Portal: a conclusão da execução pelo portal (DL-0112) é o atalho interino do DL-0049 (sem envelope assinado,
  só dev). A decisão do jurídico (`UT_AnaliseRescisao`) segue só leitura no portal (caminho de decisão assinado
  não cobre o CANCEL).
- Jurídico: prazo de 5 dias da execução, `dentro_prazo=true` para todo pedido (RN 412), data de efeito = data
  do pedido, textos. DPO: uso dos dados de elegibilidade para o cancelamento. Operação: pessoas nos grupos.
- Meta: retorno fora da janela de 24 h precisa de template aprovado (hoje só o aviso à equipe).
- **Identificação para executar no Tasy (precisa de decisão do dono / AMH):** a AMH não entrega ao Maezo número
  de contrato nem matrícula/carteirinha em claro, e o portal não mostra referência do beneficiário na tarefa. A
  instância carrega só o pseudônimo do Maezo (`beneficiario_pseudo_id`). Para a pessoa do cadastro achar o
  beneficiário no Tasy é indispensável uma rota NOVA da AMH, na Zona PHI e só para staff, por exemplo
  `GET /subjects/{portable_subject_ref}/identificacao-cadastro` (finalidade `execucao-cancelamento`) devolvendo
  `numero_contrato`, `matricula`/`carteirinha` (sem máscara) e `titular` — mais o Maezo gravar na instância o
  `portable_subject_ref` (opaco) para o portal pedir essa rota. **Não implementado** (aguarda o dono). Para o
  teste em dev, quem testa sabe quem é o beneficiário de teste.
