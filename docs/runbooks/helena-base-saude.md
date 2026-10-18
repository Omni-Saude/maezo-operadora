# Helena — base de conhecimento de saúde (só em dev) — `MAEZO_HELENA_BASE_SAUDE`

**Status:** MODELO DE TESTE do dono, SÓ no ambiente de desenvolvimento (dev). Decisão do dono (17/10/2026):
ADR-0064 e DL-0118. **Conteúdo RASCUNHO aprovado pelo dono só para dev; revisão médica e jurídica antes de
produção.** Não ligar em outro ambiente.

## 1. O que muda, em linguagem simples

Com a chave ligada, depois dos portões de segurança de sempre (risco psicossocial com CVV 188, tabela de
alarme, emergência com caso aberto, recém-nascido):

| A pessoa diz | A Helena faz |
|---|---|
| "O que é PSA?", "Pra que serve a dipirona?", "O que é cardiologista?", "O que é angioplastia?" | Responde com o texto APROVADO da base (`spec/conhecimento/saude`), a linha de crédito da fonte, o link para ler mais (quando há) e o aviso: "Esta informação é geral e não substitui avaliação médica. Se quiser, posso te passar para a equipe de saúde." **Sem caso. Sem dose. Sem diagnóstico.** |
| "Meu PSA deu fora dos parâmetros, o que me sugere?" | O texto geral do assunto + "Quem pode avaliar o seu resultado é o seu médico: leve o resultado a ele." + a oferta de pessoa. Sem caso automático; nunca interpreta o resultado. |
| "O que é xyzabc?" (assunto fora da base) | "Não tenho uma informação aprovada sobre isso por aqui." + onde buscar + oferta de pessoa. Sem caso. |
| "Estou com muita dor de cabeça desde ontem" (dor SEM sinal de alarme) | Perguntas de ALERTA (as do arquivo `sin-*` do sintoma, ou as genéricas da v4). "Sim" → alerta P1 com 192. "Não" → o texto de orientação do sintoma + 1) onde marcar consulta 2) falar com a equipe de saúde. |
| "Estou com dor no peito e falta de ar" (sinal grave) | Escala na hora, com 192 — como sempre. |
| "Não quero falar com ninguém" / "Pode cancelar o atendimento" / "Não preciso mais" / "Cancela o caso" **com caso aberto** | "Quer que eu encerre o seu atendimento com a equipe? Responda SIM para encerrar." SIM → encerra o caso (P2/P3): "Pronto, encerrei o seu atendimento. Se precisar, é só me chamar." Caso P1/grave: "Seu caso é de saúde e precisa de avaliação. Não posso encerrá-lo por aqui. Se for emergência, ligue 192." |

Sem caso aberto, a recusa recebe a resposta de antes (DL-0088). Pedido de pessoa (RN 623/2024), ameaça de
ANS/Procon, cobrança (Lucas) e consulta/cobertura do plano **vencem** a base.

O fail-safe "intensidade grave sem código → escala" (r10 da tabela de adulto) só deixa de abrir caso na hora
para **dor sem sinal de alarme em adulto**, e só com a base **e a v4** ligadas (é a v4 que faz as perguntas de
alerta). Criança, gestante e saúde mental não mudam. A DMN não muda.

## 2. A chave e os recursos Terraform

- Variável Terraform `helena_base_saude` (`deploy/aws-ecs/envs/dev-sa-east-1/variables.tf`, default `false`),
  ligada em `deploy/aws-ecs/envs/dev-sa-east-1/amh-interop.auto.tfvars` (`helena_base_saude = true`).
- Vira a env `MAEZO_HELENA_BASE_SAUDE` do receptor (`service-webhook-receiver.tf`, lista `environment_app`),
  que a task do simulador herda (`task-simulador.tf` usa `local.webhook_receiver_contrato.environment_app`).
- Recursos afetados: `aws_ecs_task_definition.webhook_receiver`, `aws_ecs_service.webhook_receiver`,
  `aws_ecs_task_definition.simulador`.
- A imagem precisa ter a base: `deploy/Dockerfile` copia `spec/conhecimento/saude` e o `pyproject.toml` a
  empacota (force-include). Sem ela (ou com a base inválida) a chave se **desliga sozinha** e o receptor loga
  `helena_base_saude_invalida`.

## 3. Ligar (primeira vez)

1. Mesclar o PR (CI verde) e gerar a imagem do receptor/simulador como de costume.
2. **Antes de ligar:** deploy-processes do `SP-OP-ESCALATION-001` v1.1.0 (a mensagem
   `msg.escalation.withdrawn`), como no runbook `dmn-bpmn-deployment.md`. Sem isso, o encerramento a pedido
   falha honestamente ("Não consegui encerrar o seu atendimento agora...") e o caso continua aberto.
3. Apply direcionado (com as tags de imagem de sempre):

   ```
   terraform plan  -target=aws_ecs_task_definition.webhook_receiver -target=aws_ecs_service.webhook_receiver \
                   -target=aws_ecs_task_definition.simulador -var=...
   terraform apply -target=aws_ecs_task_definition.webhook_receiver -target=aws_ecs_service.webhook_receiver \
                   -target=aws_ecs_task_definition.simulador -var=...
   ```
4. Verificar: logs `helena_base_saude_responde` / `helena_base_saude_nao_encontrado` /
   `helena_base_saude_failsafe_de_dor_sem_alarme` / `helena_base_saude_caso_encerrado`; roteiro de aceite
   `--roteiro helena_base_saude` no simulador (seção 7).

## 4. Desligar ("voltar ao estágio anterior")

1. `helena_base_saude = false` em `amh-interop.auto.tfvars` (PR) e o mesmo apply direcionado da seção 3.
2. Em emergência: `terraform apply ... -var=helena_base_saude=false` com os mesmos `-target`; depois registre
   no arquivo para o próximo apply não religar.
3. O BPMN v1.1.0 pode ficar no motor: sem a chave ninguém envia a mensagem. A base fica no repo sem efeito.

## 5. Desfazer de vez

Desligar (seção 4) + `git revert` dos PRs desta entrega (mecanismo + conteúdo) + build + apply direcionado;
opcionalmente deploy-processes da v1.0.0 do `SP-OP-ESCALATION-001`.

## 6. Adicionar ou atualizar um assunto

1. Um arquivo por assunto: `spec/conhecimento/saude/<categoria>/<id>.yaml` (formato no ADR-0064 §1 e em
   `spec/conhecimento/saude/FONTES.md` §5). O `id` é o nome do arquivo e tem o prefixo da categoria.
2. Fonte: SÓ da lista fechada (`FONTES.md`). Citação literal (`citacao_literal`) só de CC BY-ND (gov.br) ou
   ato oficial da ANS — curta, sem mudar uma palavra, com `coletado_em` e, de preferência, `sha256_trecho`
   (`python scripts/ops/base_saude_verificar_fontes.py --hash "texto literal"`). Medicamento: texto PRÓPRIO
   curto + `link_leitura` da bula (bula é só link).
3. Texto: até 700 caracteres, frases de até 20 palavras, sem dose/quantidade/"tome"/"pode tomar"/"não é
   grave"/"é normal"/diagnóstico/promessa de cobertura. `sintoma`: 1 a 3 perguntas de alerta sim/não,
   `sinais_alarme` e `populacao`.
4. `revisao.revisar_ate` = data da coleta + 6 meses; `aprovado_por: "dono (17/10/2026, dev)"`;
   `revisao_medica: pendente`.
5. Rodar o gate local: `pytest tests/unit/agents/test_helena_base_saude_conteudo.py tests/unit/conhecimento`.
   Sinônimo repetido entre dois assuntos reprova a base INTEIRA.
6. PR → CI verde → merge → nova imagem (a base vai dentro da imagem).

## 7. Verificar as fontes e o ciclo de revisão (6 meses)

- `python scripts/ops/base_saude_verificar_fontes.py` — baixa cada fonte, diz o que está fora do ar, o que
  mudou (o trecho com `sha256_trecho` não está mais na página, ou o texto do arquivo diverge do coletado) e
  o que vence nos próximos 30 dias (`--dias N`). `--sem-rede` só olha as datas; `--falhar` sai com 1 se houver
  achado. **Não roda no CI** (faz rede e depende de sites de terceiros): rode antes de cada ciclo de revisão
  ou num job agendado do dev.
- Assunto com `revisar_ate` vencido: o CI reprova e a Helena deixa de responder aquele assunto (responde "não
  tenho informação aprovada"). Renovar = revisar o texto contra a fonte, nova `coletado_em`, novo
  `revisar_ate`.
- Roteiro de aceite no dev: `--roteiro helena_base_saude` (critério "RESOLVEU?": confere o CONTEÚDO, não só
  "não abriu caso"; o passo do SIM confere no motor que a instância não está mais ativa).

## 8. Quem aprova

| O quê | Em dev | Antes de produção |
|---|---|---|
| Ligar/desligar a chave | Dono | Dono + médico + jurídico |
| Conteúdo (texto de cada assunto) | Dono | **Médico** (revisão de cada texto) |
| Fonte nova na lista fechada / licença | Dono | **Jurídico** (inclusive a reprodução da bula ANVISA e o DeCS) |
| Regra clínica (fail-safe da dor, perguntas de alerta, encerramento) | Dono | **Médico** |

## 9. Leituras do mecanismo a confirmar pelo dono

- Ato oficial fora da lista (resolução do CFM sobre especialidades; lei federal no planalto.gov.br) é aceito
  SÓ como apoio de texto próprio, nunca como citação literal.
- A tabela de alarme interna do repo é aceita como referência do texto de orientação de `sintoma` (crédito
  "Texto da operadora.").
- Achado do piloto: `exa-mamografia` diz que o Ministério da Saúde recomenda o rastreio "a cada dois anos".
  O CI não reprova (é informação atribuída à fonte), mas fica para a revisão médica.

## 10. Saúde gerativa (ADR-0065, DL-0119) — o Haiku 5.5 redige, só na allowlist

**O que é.** Com `helena_saude_gerativa = true` (e a base ligada), para as conversas de
`helena_saude_gerativa_conversas` o Claude Haiku 5.5 REDIGE a resposta das perguntas informativas de saúde (no
lugar do texto fixo da base) e a orientação da queixa sem alarme (resposta "não" ou contexto às perguntas de
alerta), com checagem antes de enviar. A chamada sai pela rota PRÓPRIA
**`experimento_global_autorizado_dpo_2026-10-17`** — perfil GLOBAL do Bedrock, **pode processar fora do
Brasil**; o dono autorizou COMO DPO só para a conversa dele e as do simulador, só em dev. Classificação,
alarme/P1 (192), saúde mental com risco (CVV 188), plano, carteirinha, cobrança, cancelamento e casos seguem no
GLM-5 em sa-east-1, decididos ANTES da redação.

**Quem está na allowlist (dev):** a conversa do dono (`wa:amh:hk1_34d934f1…`) e as conversas sintéticas dos
telefones `5511900000790..799` (roteiro `helena_saude_gerativa`; o segmento 10 roda no `…789`, FORA da lista,
para provar que nada mudou). Para incluir alguém: só com autorização do DPO, PR no tfvars.

**O que vai ao modelo:** a mensagem e até 2 mensagens anteriores da pessoa, com CPF, nome dito, carteirinha,
telefone, e-mail e datas trocados por `[dado removido]`; nunca as falas da Helena, nunca dado do cadastro.

**Como sai:** rascunho → checagem (cercas da Helena, termos proibidos da base, regras: dose, mandar usar/não
usar/parar/continuar/dobrar, dose esquecida, combinação, minimizar, diagnóstico, promessa, gênero, pediatra para
adulto, 192 nos modos de sintoma, ~100 palavras, link/fonte não fornecidos) → 1 reescrita → senão a resposta
segura padrão ("Não consigo te responder isso com segurança por aqui…"). Erro/prazo do modelo → a mesma resposta
segura (nunca silêncio). Logs: `helena_saude_gerativa_redigida` (rota, modelo, modo, desfecho, regras —
nunca o texto) e `rota_global_experimental_chamada`/`_falhou`.

**Recursos Terraform:** env `MAEZO_HELENA_SAUDE_GERATIVA` e `MAEZO_HELENA_SAUDE_GERATIVA_CONVERSAS` em
`environment_app` (receptor, simulador e lembrete de cobrança herdam); IAM em
`experimento-saude-gerativa.tf` (`aws_iam_role_policy.task_saude_gerativa`, `.webhook_receiver_saude_gerativa[0]`,
`.agent_resume_saude_gerativa[0]`, `.simulador_saude_gerativa`).

**Desligar** (frase do dono: "desligue a saúde gerativa"): `helena_saude_gerativa = false` em
`amh-interop.auto.tfvars` (PR) + apply direcionado das task definitions/serviço do receptor e da task do
simulador (e do lembrete, se existir), como na seção 3. Em emergência: o mesmo apply com
`-var=helena_saude_gerativa=false` e depois registrar no arquivo. Desligada, o processo nem constrói o cliente
da rota global; a Helena volta ao texto fixo da base.

**Desfazer** ("desfaça"): desligar + `git revert` dos PRs desta entrega + apply direcionado que REMOVE as 4
políticas IAM (o plan mostra 4 destroys) + build/deploy.

**Verificar:** roteiro `--roteiro helena_saude_gerativa` no simulador (utilidade: "resolveu a dúvida?"); logs
acima no grupo `/ecs/maezo-operadora-dev/webhook-receiver`.

## 11. Fase 5 (DL-0120) — rascunhos por IA e dossiê de segurança

- **Rascunhos da base pelo Sonnet 5.5** (`scripts/base_saude_rascunhos/README.md`): arquivos com
  `revisao.status: rascunho_pendente_revisao_medica` e `escrito_por`. Respondem em dev pela mesma chave
  (`helena_base_saude`); desligar a chave desliga todos. Tirar só os rascunhos: apagar os arquivos com esse
  status (ou revert do PR do conteúdo) e gerar a imagem. Assunto sem página leiga na lista fechada usa a
  referência sem link (`FONTES.md` §4-bis).
- **Dossiê de segurança** (`scripts/dossie_seguranca_saude/README.md`, explicação ao médico em
  `docs/clinico/dossie-seguranca-helena.md`): tarefa avulsa que roda os cenários fixos contra o grafo e o
  redator, sem enviar nada e sem abrir caso real; resultados em
  `s3://maezo-operadora-dev-qa-simulador-203312548462/dossie-seguranca/<data>/`.
- **IAM**: a política experimental do simulador (`experimento-redacao-saude.tf`) ganhou os prefixos
  `experimentos/base-saude-rascunhos/` e `dossie-seguranca/` (apply direcionado:
  `-target=aws_iam_role_policy.simulador_experimento_redacao_saude`). Remover junto com o experimento.
