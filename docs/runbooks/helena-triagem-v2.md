# Nova triagem da Helena (experimento em dev) — `MAEZO_HELENA_TRIAGEM_V2`

**Status:** experimento SÓ no ambiente de desenvolvimento (dev), decisão do dono registrada no
`docs/decisions-log.md` (DL-0104). **A Helena v4 (`docs/runbooks/helena-v4.md`, DL-0114) substitui esta
triagem:** com `MAEZO_HELENA_V4` ligada a v2 fica inerte. **Conteúdo clínico DRAFT — pendente de ratificação do
médico-auditor.** Não ligar em outro ambiente sem essa ratificação.

## 1. O que é, em linguagem simples

Hoje a Helena abre um caso para a equipe (processo SP-OP-ESCALATION-001) em situações em que a
pessoa só tinha uma dúvida. O exemplo mais claro: toda "pergunta clínica" ("posso tomar dipirona?",
"isso é virose?") abre um caso P2 para a enfermagem na hora.

A nova triagem segue o catálogo do dono (`Catalogo_Necessidades_Helena_Lucas.xlsx`, 14/10/2026):

1. **Os portões de segurança continuam iguais e vêm primeiro**: risco psicossocial, sinal de alarme
   (as quatro tabelas `triage_redflag_*`), emergência com caso aberto, recém-nascido, pedido de
   pessoa, ameaça de queixa à ANS/Procon, cobrança (Lucas) e consulta do plano. Se qualquer um deles
   disparar, o caso abre como hoje.
2. **Depois deles**, uma tabela nova (`spec/processes/dmn/helena_triage_admissibility.dmn`) decide:
   - **pergunta clínica sem sintoma** → a Helena diz que não avalia nem indica remédio, mostra o
     caminho (consulta na rede do plano, UBS, pronto-atendimento, 192) e oferece uma pessoa. **Não
     abre caso.**
   - **pergunta clínica com sintoma** ("o que pode ser essa dor ao urinar?") → é tratada como sintoma:
     tabela de alarme e ficha. Não escala na hora (a não ser que a tabela de alarme acuse).
   - **sintoma sem alarme** → **ficha mínima** de até **3 perguntas** (antes eram 2), por tipo de
     queixa (aba "4 Ficha mínima"). A ficha **não pergunta o que a pessoa já disse** ("dor nas
     costas" não ouve "em que parte do corpo?"; "minha filha de 6 anos" ou "recém-nascida" não ouve
     "qual a idade?"), fala do paciente como a pessoa disse ("sua filha", "seu filho", "o bebê") e,
     se a pessoa responder outra coisa, **pergunta de novo só o que faltou** na rodada seguinte
     ("Só para eu entender: ..."). Ficha completa → a Helena mostra o caminho com o prazo da ANS
     (consulta básica em até 7 dias úteis) **da população**: adulto → clínico geral; criança →
     pediatra; gestante → obstetra ou ginecologista. Ficha esgotada → o mesmo texto, **sem abrir
     caso**.
   - **bebê com menos de 3 meses (inclusive recém-nascido) sem alarme** no fim da ficha → **não**
     recebe o prazo de 7 dias: recebe a oferta clara de falar agora com a equipe de saúde (sem abrir
     caso sozinho), com pronto-atendimento/192 se piorar. **Decisão clínica pendente** (seção 7).
   - **necessidade não clara** ("preciso de ajuda") → UMA pergunta com opções (1 saúde, 2 plano,
     3 mensalidade, 4 falar com uma pessoa). Se continuar sem clareza → oferece uma pessoa, **sem
     abrir caso**. A resposta à oferta ("4", "4)", "opção 4", "a 4", "quatro", "falar com uma
     pessoa"), logo depois dela ou depois do "Ainda não entendi", é decidida no código: 1 → convida a
     contar o que sente; 2 → consulta do plano; 3 → Lucas (cobrança); **4 → abre caso** (pedido de
     pessoa). Um número solto sem oferta recente segue o fluxo normal.
3. **A cada mensagem da ficha**, a checagem de alarme e de risco continua valendo: "agora estou com
   dor no peito" no meio da ficha abre o caso P1 com o 192, como hoje.
4. Quando um caso abre, o resumo que o atendente lê traz a ficha coletada.

**Pedido de pessoa sempre abre caso** (RN 623/2024, art. 9º, III). A Helena **nunca** diagnostica,
diz se é grave, orienta conduta ou indica remédio.

## 2. A chave

| | |
|---|---|
| Nome | `MAEZO_HELENA_TRIAGEM_V2` (setting `helena_triagem_v2`, `src/maezo/platform/webhooks/whatsapp/settings.py`) |
| Padrão | `false` — **desligada = a Helena de antes, byte a byte** |
| Onde liga no dev | `deploy/aws-ecs/envs/dev-sa-east-1/amh-interop.auto.tfvars` (`helena_triagem_v2 = true`) |
| Variável Terraform | `helena_triagem_v2` (`variables.tf`, default `false`) |
| Vai para | o receptor do WhatsApp (`service-webhook-receiver.tf`) e, pelo mesmo contrato de ambiente, a task do simulador |

## 3. Como LIGAR no dev

A ordem importa: a tabela tem de estar no motor **antes** de a chave ligar. Se a chave ligar sem a
tabela, nada quebra em silêncio — pergunta de saúde vira caso `falha_tecnica` (uma pessoa olha) e
mensagem vaga recebe "pode me mandar de novo?" —, mas o experimento não vale nada assim.

1. **Imagem** com este código (merge do PR + build normal da imagem).
2. **Publicar a tabela**: rodar a task `maezo-operadora-dev-deploy-processes` com a imagem nova
   (`docs/runbooks/aws-ecs.md`, passo 5). Ela publica tudo de `spec/processes/` e é idempotente. A
   tabela nova aparece como `helena_triage_admissibility` no motor.
3. **Ligar a chave** com apply direcionado (a linha `helena_triagem_v2 = true` já está no
   `amh-interop.auto.tfvars` deste PR). Recursos que mudam:
   - `aws_ecs_task_definition.webhook_receiver` — nova revisão (ganha `MAEZO_HELENA_TRIAGEM_V2=true`);
   - `aws_ecs_service.webhook_receiver` — passa a usar a nova revisão (atualização no lugar);
   - `aws_ecs_task_definition.simulador` — nova revisão (o simulador compõe o mesmo receptor);
   - `aws_ecs_task_definition.lembrete_cobranca["this"]` — só se `var.lembrete_cobranca` estiver
     definido (hoje não está no dev, então não existe).
   ```
   cd deploy/aws-ecs/envs/dev-sa-east-1
   terraform plan  -target=aws_ecs_task_definition.webhook_receiver -target=aws_ecs_service.webhook_receiver \
                   -target=aws_ecs_task_definition.simulador -var=... (as tags de imagem de sempre)
   terraform apply -target=aws_ecs_task_definition.webhook_receiver -target=aws_ecs_service.webhook_receiver \
                   -target=aws_ecs_task_definition.simulador -var=...
   ```
   Confira no plan que a ÚNICA diferença de ambiente é `MAEZO_HELENA_TRIAGEM_V2` (e a tag da imagem,
   se ela também mudou).
4. **Redeploy**: o serviço troca de revisão sozinho; acompanhe até `runningCount = desiredCount`.

## 4. Como DESLIGAR (voltar ao comportamento de hoje, sem tirar o código)

1. Trocar a linha para `helena_triagem_v2 = false` no `amh-interop.auto.tfvars` (PR pequeno).
2. Mesmo apply direcionado da seção 3 (os mesmos três recursos).
3. O serviço volta a uma revisão com `MAEZO_HELENA_TRIAGEM_V2=false`: a Helena volta a escalar
   pergunta clínica como `intencao_clinica` e a ficha volta a 2 rodadas. A tabela fica no motor,
   sem efeito (ninguém a consulta com a chave desligada).

Em emergência dá para pular o PR: `terraform apply ... -var=helena_triagem_v2=false` com os mesmos
`-target`. Depois registre a mudança no arquivo para o próximo apply não religar.

## 5. Como DESFAZER DE VEZ

1. `git revert` do PR (código, tabela, testes, Terraform e este runbook saem juntos).
2. Build da imagem + apply direcionado da seção 3 (a variável e a env somem da task definition).
3. **A tabela continua no motor**: o CIB Seven não tem "undeploy" pelo nosso deploy de processos.
   Ela fica lá sem efeito nenhum — nenhum código a consulta depois do revert.

## 6. Como verificar

- **Logs do receptor** (`/ecs/maezo-operadora-dev/webhook-receiver`): `helena_triagem_v2_decisao`
  com `necessidade`, `decisao`, `texto` e `ref=helena_triage_admissibility#...`. Se aparecer
  `helena_triagem_v2_tabela_indisponivel`, a tabela não está no motor (volte ao passo 2 da seção 3).
- **Roteiro de aceite** (`src/maezo/qa/simulador/aceite.py`, roteiro `triagem_v2`), com a chave
  ligada: a mesma task do simulador com `SIMULADOR_ARGS=--roteiro triagem_v2`
  (`docs/runbooks/simulador-beneficiario.md`, seção do roteiro de aceite). Cenários: dúvida de saúde,
  pergunta clínica sem sintoma, "preciso ir ao médico?", dor ao urinar, filha com dor, dor nas
  costas, filho com tosse, necessidade não clara (pergunta, oferta de pessoa, opção 4), pedido de
  pessoa, alarme no meio da ficha, e a ficha que não repete o que foi dito (dor nas costas sem "em que
  parte"; filha de 6 anos sem "qual a idade").
- **Simulador com personas**: com a chave ligada a checagem **D13 "caso evitável"** passa a valer nas
  personas `sintoma_leve` e `duvida_saude`: caso aberto sem alarme, sem risco e sem pedido de pessoa
  conta como falha. Com a chave desligada a D13 não se aplica.
- **Ponto de atenção conhecido**: "dor muito forte ao urinar" pode ser lida pelo classificador como
  intensidade `grave`; aí a regra de segurança `r10` da tabela adulta (sintoma não mapeado relatado
  como grave) abre caso P2 de enfermagem. Isso é a tabela de alarme funcionando, não a triagem nova.

## 7. O que ainda depende de decisão (aba "7 Decisões pendentes" do catálogo)

| ID | Assunto | Como está no experimento |
|---|---|---|
| D01 | Pergunta clínica sem sintoma deixa de abrir caso? | Sim (texto T08 + oferta de pessoa) |
| D02 | Ficha mínima e limite de rodadas | 3 rodadas; perguntas da aba 4 (rascunho) |
| D03 | Ficha esgotada: para onde vai o caso | **Não abre caso**: orienta e oferece pessoa (diverge da R13) |
| D08 | Catch-all da Helena | **Não abre caso**: oferece pessoa (diverge da R24, que abriria P3) |
| D09 | Pergunta de esclarecimento | Texto T11 (rascunho), uma vez por conversa (pelo histórico curto) |
| D10 | Informação geral de saúde (S07) | Sem conteúdo aprovado: recebe o mesmo texto da R15 |
| D11 | Prazos da RN 566 nos textos | Só o valor conferido (7 dias úteis, consulta básica) |
| D14 | Textos novos | T05 (adulto/criança/gestante), T08, T09, T11, oferta de pessoa, bebê pequeno e perguntas da ficha: **RASCUNHO** |
| — (DL-0105) | Bebê com menos de 3 meses sem alarme ao fim da ficha | **Decisão clínica pendente (médico-auditor)**: oferta explícita de falar agora com a equipe de saúde, sem o prazo de 7 dias e sem abrir caso sozinho; o corte de 3 meses é do pedido do dono |

Todos os textos novos estão marcados no código como "RASCUNHO — pendente de aprovação do dono"; o
texto enviado ao beneficiário não leva essa marca.

## 8. Onde está cada peça

| Peça | Arquivo |
|---|---|
| Tabela nova | `spec/processes/dmn/helena_triage_admissibility.dmn` |
| Vocabulário, léxicos e textos | `src/maezo/agents/helena/triagem_v2.py` |
| Integração no grafo | `src/maezo/agents/helena/graph.py` (`_triagem_v2_decidir`, `_ficha_v2`) |
| Testes | `tests/unit/spec/test_helena_triagem_v2_dmn.py`, `tests/unit/agents/test_helena_triagem_v2.py`, `tests/unit/qa/test_simulador_triagem_v2.py` |
| Roteiro de aceite | `src/maezo/qa/simulador/aceite.py` (`TRIAGEM_V2`) |
