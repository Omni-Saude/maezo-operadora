# Helena v4 (catálogo de necessidades v4, só em dev) — `MAEZO_HELENA_V4`

**Status:** experimento SÓ no ambiente de desenvolvimento (dev), decisão do dono registrada no
`docs/decisions-log.md` (DL-0115). Textos **aprovados pelo dono para dev** (16/10/2026); **revisão
jurídica/clínica antes de produção**. **Conteúdo clínico DRAFT** (ainda não há médico-auditor) e o prazo de
**14 dias úteis** do especialista é **RASCUNHO, a confirmar pelo jurídico**. Não ligar em outro ambiente.

## 1. O que é, em linguagem simples

A v4 vem do `Catalogo_Necessidades_v4.xlsx` (aba "1 Necessidades": o que a pessoa **recebe**). A lição do
teste de 15/10/2026 (triagem v2) foi que o roteiro conferia "não abriu caso" e não **"resolveu?"**: as
respostas eram genéricas, ignoravam o que a pessoa disse e o resumo do caso misturou assuntos. Aqui cada
resposta entrega algo útil, **usa o que a pessoa disse**, e o roteiro de aceite confere o **conteúdo**.

Os portões de segurança **continuam iguais e vêm primeiro**: falha do classificador, risco psicossocial,
tabela de alarme (`triage_redflag_*`), emergência com caso aberto e recém-nascido. O pedido de pessoa
(RN 623/2024, art. 9º, III), a ameaça de ANS/Procon e a cobrança (Lucas) **vencem** a v4.

| Necessidade | O que a pessoa recebe |
|---|---|
| **Especialista** ("Preciso consultar com um cardiologista", "Onde tem dermatologista pelo plano?") | O texto da coluna "HOJE": "Consulta com cardiologista é consulta de especialista. [No cadastro, seu plano consta com segmentação X.] Por aqui eu não pesquiso especialistas nem marco consulta. Você marca pelo aplicativo Austa Clínicas, pelo portal do plano ou pela central de atendimento do plano. Pelas regras da ANS, o plano deve oferecer consulta com especialista em até 14 dias úteis. Se quiser falar com uma pessoa da equipe, é só me pedir." Sem nomes, sem lista de clínicas, sem promessa de cobertura. Se a pessoa perguntou de cobertura, vem também a frase de sempre de onde confirmar. |
| **Carteirinha** ("Qual o número da minha carteirinha?", "Me manda minha carteirinha") | Plano, segmentação, vigência e nº ANS do plano, com o **código mascarado**, e o aviso de que o cartão completo está no aplicativo Austa Clínicas e no portal. Nunca CNS, código inteiro, nome nem data de nascimento. |
| **Sintoma leve** ("Estou com dor nas costas desde ontem", "Meu filho está com tosse") | UMA mensagem com 1 ou 2 perguntas fechadas **só de alerta** (`sim`/`não`). Com "não": **"Entendi: dor nas costas desde ontem. Eu não avalio sintomas, mas te ajudo com o caminho. Você prefere: 1) saber onde marcar consulta pelo plano (clínico geral ou pediatra: até 7 dias úteis pela ANS); ou 2) falar com a nossa equipe de saúde? Se piorar, procure um pronto-atendimento; se for grave, ligue 192."** Opção **1** → como marcar pelo plano, sem diagnóstico (aplicativo/portal/central, 7 dias úteis, UBS, 192). Opção **2** → abre o caso para a equipe de saúde com o resumo pronto. |

### 1.1 O especialista — decisões e limites

- **Léxico pequeno e fechado** (`src/maezo/agents/helena/helena_v4.py`, `_ESPECIALIDADES`): cardiologista,
  dermatologista, ortopedista, endocrinologista, urologista, oftalmologista (oculista), neurologista,
  otorrino, gastroenterologista, pneumologista, reumatologista. Novas entram por PR, uma a uma.
- **Fora do léxico, de propósito:** psiquiatra e psicólogo (saúde mental pede acolhimento e a tabela de
  risco; psicologia tem prazo de 10 dias úteis, não 14); fisioterapia, nutricionista e fonoaudiólogo
  (10 dias úteis); oncologista (assunto de pessoa; alta complexidade tem prazo próprio); "especialista" sozinho.
  Esses seguem o caminho de hoje.
- **Ginecologista, obstetra, pediatra e clínico geral** são consulta **básica** na RN 566 (7 dias úteis, já
  conferido e usado pela triagem v2): o texto diz "consulta básica" e **7** dias, não 14 (dizer 14 seria
  falso). **A confirmar com o dono** (o catálogo lista ginecologista junto dos especialistas).
- **Não confunde com:** pergunta de cobertura de **procedimento ou exame** ("cobre ressonância?", "cobre
  cirurgia com o ortopedista?") — segue o caminho de cobertura de hoje; sintoma na mesma mensagem ("estou
  com dor no joelho e preciso de ortopedista") — vai para o sintoma; pedido de pessoa; cobrança.
- **Prazo de 14 dias úteis:** vem de `docs/compliance/rn-currency-review.md` §2.1 ("demais especialidades
  14 d.ú."), lido da página do consumidor da ANS. **RASCUNHO, a confirmar pelo jurídico** (marcado no código
  em `PRAZO_ESPECIALISTA_DIAS_UTEIS`).

### 1.2 O sintoma leve — como funciona

1. A tabela de alarme roda **antes** (como sempre). Sem bandeira, a v4 pergunta **só alertas**, por tipo de
   queixa (`ALERTAS` em `helena_v4.py`; **nenhum sinal novo**, cada um vem da aba "3 Sinais de alarme" do
   catálogo v1 e das tabelas `triage_redflag_*`): geral (dor no peito, falta de ar, desmaio, boca torta/fraqueza
   de um lado), cabeça (início súbito "a pior da vida"; sinal neurológico), barriga/vômito (dor muito forte,
   sangue, fezes pretas), pele (inchaço de rosto/boca/língua, dificuldade para respirar), criança
   (dificuldade para respirar, muito mole/sonolenta, convulsão; febre com manchinhas, pouco xixi).
2. A resposta é lida de forma **determinística e do lado seguro**: "não" só vale quando a mensagem começa por
   negativa, é curta, sem ressalva ("não, mas…") e sem agravamento ("não consigo respirar", "piorou");
   **dúvida** ("não sei", "um pouco") vale como **sim**.
3. **Sim** (ou sinal de alarme, em qualquer momento) → alerta clínico P1 (`red_flag_clinico`/`grave`) com 192,
   como hoje. **Não deu para entender** → pergunta de novo **uma** vez ("Só para eu entender: …") e, se
   continuar, abre o caso (`intencao_clinica`/`moderada`) por precaução.
4. A pergunta e a oferta 1/2 valem **só no turno seguinte** (a memória é zerada em todo turno): um "2" solto,
   depois de outro assunto, não abre caso.
5. O **resumo do caso** (opção 2, "sim" ou sem resposta) é **fixo e legível**, sem modelo: só o relato da
   pessoa sobre **esta** queixa, o que a Helena perguntou e o que foi respondido, e que a Helena não avaliou
   nem orientou. Sem asteriscos, sem "ficha da triagem: …", sem o que foi falado antes sobre outros assuntos
   (corrige o defeito visto no portal em 15/10/2026).
6. **Fora da v4** (seguem o caminho de hoje): gestante, saúde mental, bebê com menos de 3 meses ou recém-nascido.

### 1.3 A carteirinha

Resposta determinística (`texto_da_carteirinha`) sobre uma **lista fechada** de fatos da consulta do plano
(`fatos_da_carteirinha`): código (só se **já vier mascarado**), plano, segmentação, início da vigência,
cancelamento e nº ANS do plano (só dígitos). Campo ausente diz "não consta no cadastro" (nunca se completa).
Sem identidade verificada ou com a fonte falhando, valem os textos fixos de sempre.

## 2. A chave

| | |
|---|---|
| Nome | `MAEZO_HELENA_V4` (setting `helena_v4`, `src/maezo/platform/webhooks/whatsapp/settings.py`) |
| Padrão | `false` — **desligada = a Helena de antes, byte a byte** (provado em `tests/unit/agents/test_helena_v4.py`) |
| Onde liga no dev | `deploy/aws-ecs/envs/dev-sa-east-1/amh-interop.auto.tfvars` (`helena_v4 = true`) |
| Variável Terraform | `helena_v4` (`variables.tf`, default `false`) |
| Vai para | o receptor do WhatsApp (`service-webhook-receiver.tf`, `local.webhook_receiver_contrato.environment_app`) e, pelo mesmo contrato de ambiente, a task do simulador |
| Relação com a v2 | A v4 **substitui** a nova triagem (`MAEZO_HELENA_TRIAGEM_V2`): com as duas ligadas manda a v4 e a v2 fica inerte (a tabela `helena_triage_admissibility` não é consultada). |

## 3. Como LIGAR no dev

Não há tabela nova nem deploy de processos: só imagem + variável de ambiente.

1. **Imagem** com este código (merge do PR + build normal da imagem).
2. **Apply direcionado** (a linha `helena_v4 = true` já está no `amh-interop.auto.tfvars` deste PR). Recursos
   que mudam:
   - `aws_ecs_task_definition.webhook_receiver` — nova revisão (ganha `MAEZO_HELENA_V4=true`);
   - `aws_ecs_service.webhook_receiver` — passa a usar a nova revisão (atualização no lugar);
   - `aws_ecs_task_definition.simulador` — nova revisão (o simulador compõe o mesmo contrato de ambiente);
   - `aws_ecs_task_definition.lembrete_cobranca["this"]` — só se `var.lembrete_cobranca` estiver definido
     (hoje não está no dev, então não existe).
   ```
   cd deploy/aws-ecs/envs/dev-sa-east-1
   terraform plan  -target=aws_ecs_task_definition.webhook_receiver -target=aws_ecs_service.webhook_receiver \
                   -target=aws_ecs_task_definition.simulador -var=... (as tags de imagem de sempre)
   terraform apply -target=aws_ecs_task_definition.webhook_receiver -target=aws_ecs_service.webhook_receiver \
                   -target=aws_ecs_task_definition.simulador -var=...
   ```
   Confira no plan que a ÚNICA diferença de ambiente é `MAEZO_HELENA_V4` (e a tag da imagem, se ela também mudou).
3. **Redeploy**: o serviço troca de revisão sozinho; acompanhe até `runningCount = desiredCount`.

## 4. Como DESLIGAR (voltar ao comportamento de hoje, sem tirar o código)

1. Trocar a linha para `helena_v4 = false` no `amh-interop.auto.tfvars` (PR pequeno).
2. Mesmo apply direcionado da seção 3 (os mesmos três recursos).
3. O serviço volta a uma revisão com `MAEZO_HELENA_V4=false`: a Helena volta ao que era (especialista e
   carteirinha com os textos antigos, sintoma com o texto genérico). Uma pergunta da v4 que estivesse em
   andamento numa conversa é descartada no turno seguinte (`receive`), sem efeito.

Em emergência dá para pular o PR: `terraform apply ... -var=helena_v4=false` com os mesmos `-target`. Depois
registre a mudança no arquivo para o próximo apply não religar.

## 5. Como DESFAZER DE VEZ

1. `git revert` do PR (código, testes, roteiro, Terraform e este runbook saem juntos).
2. Build da imagem + apply direcionado da seção 3 (a variável e a env somem da task definition).
3. Nada fica no motor: a v4 não publica tabela nem processo.

## 6. Como verificar

- **Logs do receptor** (`/ecs/maezo-operadora-dev/webhook-receiver`): `helena_v4_especialista`
  (`basica`, `com_segmentacao`), `helena_v4_sintoma_leve` (`grupo`, `populacao`), `helena_v4_alerta`
  (`resposta=nao|sim|repete`), `helena_v4_escolha` (`opcao=1|2`), `helena_v4_caso` (`etapa`, `motivo`) e
  `helena_consulta_plano ... resultado=v4` (carteirinha). Nenhum log leva o texto da pessoa.
- **Roteiro de aceite `helena_v4`** (`src/maezo/qa/simulador/aceite.py`), com a chave ligada: a mesma task do
  simulador com `SIMULADOR_ARGS=--roteiro helena_v4` (`docs/runbooks/simulador-beneficiario.md`, seção do
  roteiro de aceite). Cada segmento declara o critério **"RESOLVEU?"** e confere o **conteúdo**:

  | Segmento | Mensagem(ns) | O que tem de aparecer / não pode aparecer |
  |---|---|---|
  | 1 | "Preciso consultar com um cardiologista" | consulta com cardiologista; aplicativo Austa Clínicas, portal, central; **14 dias úteis**; "não pesquiso"; pessoa da equipe. **Não**: promessa de cobertura, nome/clínica/endereço, caso aberto |
  | 2 | "Onde tem dermatologista pelo plano?" | idem, **sem lista** de clínicas |
  | 3 | "Meu plano cobre consulta com endocrinologista?" | onde marcar + 14 dias úteis + "não consigo confirmar cobertura"; sem promessa |
  | 4 | "Meu plano cobre ressonância?" | caminho de cobertura de hoje; **sem** "14 dias úteis" nem "consulta de especialista" |
  | 5 | "Qual o número da minha carteirinha?" | `Código: ***NNNN`, plano, segmentação, vigência, nº ANS, aplicativo/portal. **Não**: CNS, código inteiro (13+ dígitos; o nº ANS de 9 dígitos é público e **pode**), nome, nascimento, CPF. No relatório mascarado o código aparece como `[CARTEIRINHA-MASCARADA]` (nunca `[BOLETO]`) |
  | 6 | "Estou com dor nas costas desde ontem" → "Não" → "1" | pergunta de alerta com "Entendi: dor nas costas desde ontem" e "sim ou não"; depois o texto final do dono (1/2, 7 dias úteis pela ANS, 192); depois como marcar (aplicativo/portal/central, 7 dias úteis). Sem caso, sem diagnóstico |
  | 7 | "Meu filho está com tosse" → "Não" → "2" | "seu filho está com tosse" na pergunta e na resposta final; a opção 2 abre **um** caso `intencao_clinica` |
  | 8 | "Estou com dor nas costas" → "Sim" | `red_flag_clinico` com 192 |
  | 9 | "Estou com dor no peito" | `red_flag_clinico` na hora, sem pergunta |
  | 10 | "Quero falar com uma pessoa" | `solicitacao_humano` |
  | 11 | "Estou com dor de cabeça" → "Quero falar com uma pessoa" | o pedido de pessoa vence a pergunta de alerta: abre o caso clínico `intencao_clinica` (enfermagem) com o resumo da queixa, **sem** rodar a tabela de alarme (aceite de 16/10/2026: o modelo devolvia o código da queixa anterior e saía `red_flag_clinico`). Se a própria mensagem trouxer queixa ou sinal de alarme, a tabela roda como sempre |

  O resumo do caso da opção 2 não aparece no roteiro (o simulador intercepta o caso); ele é conferido no teste
  unitário e **deve ser lido no portal** no teste do dono.
- **Simulador com personas**: a checagem **D13 "caso evitável"** vale com a v2 **ou** a v4 ligada, nas personas
  `sintoma_leve` e `duvida_saude`: caso aberto sem alarme, sem risco e sem pedido de pessoa conta como falha. Na
  v4 a escolha da opção 2 ("2", "equipe de saúde") é pedido de pessoa.

## 7. Riscos e decisões pendentes

| Assunto | Como está | Quem decide |
|---|---|---|
| Prazo de **14 dias úteis** (demais especialidades) | RASCUNHO; só a página do consumidor da ANS foi lida | Jurídico |
| Ginecologista/obstetra/pediatra/clínico geral = consulta **básica** (7 dias úteis) | A v4 usa 7; o catálogo do dono lista ginecologista junto dos especialistas | Dono / Jurídico |
| Perguntas de alerta por tipo de queixa e a leitura "dúvida = sim" | DRAFT; só sinais já existentes nas tabelas de alarme | Médico-auditor (ainda não há) |
| Opção 2 abre `intencao_clinica`/`moderada` (enfermagem); "sim" abre P1 | Como a v2/o alarme de hoje | Dono / médico-auditor |
| Resposta ininteligível 2× abre caso por precaução | Lado seguro; pode abrir caso que a pessoa não queria | Dono |
| Léxico do especialista | Pequeno e fechado; fora: saúde mental, fisio/nutri/fono, oncologia | Dono |
| Pergunta clínica ("o que pode ser essa dor?") e dúvida geral | **Não** mudam: seguem como hoje (`intencao_clinica`) | Dono |

## 8. Onde está cada peça

| Peça | Arquivo |
|---|---|
| Léxicos, textos fixos, perguntas de alerta, resumo | `src/maezo/agents/helena/helena_v4.py` |
| Integração no grafo | `src/maezo/agents/helena/graph.py` (`_v4_antes_dos_gatilhos`, `_v4_seguimento`, `_v4_sintoma_leve`, `_v4_especialista`; ramos em `collect`, `inform`, `consultar_plano`, `_resumo_contexto`) |
| Chave | `settings.py` (`helena_v4`), `dispatch.py` (`helena_v4_enabled`), `service.py` |
| Terraform | `variables.tf`, `amh-interop.auto.tfvars`, `service-webhook-receiver.tf` |
| Roteiro de aceite | `src/maezo/qa/simulador/aceite.py` (`HELENA_V4`) e D13 em `checagens.py` |
| Testes | `tests/unit/agents/test_helena_v4.py`, `tests/unit/qa/test_simulador_triagem_v2.py` |
