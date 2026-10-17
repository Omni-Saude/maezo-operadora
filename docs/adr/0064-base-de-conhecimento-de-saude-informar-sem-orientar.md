# ADR-0064: Base de conhecimento de saúde de fontes públicas — informar sem orientar

**Status:** Accepted pelo dono para dev (17/10/2026); revisão médica e jurídica antes de produção
**Data:** 2026-10-17
**Área:** Produto | Agentes (Helena) | Segurança assistencial | Processos (SP-OP-ESCALATION-001)
**Decisor:** dono do repositório (filipecarmo81 — CTO e DPO), como "MODELO DE TESTE" reversível, só em dev.
**Registro curto:** DL-0118 (`docs/decisions-log.md`). **Runbook:** `docs/runbooks/helena-base-saude.md`.

## Contexto

Até aqui a regra da Helena era "nunca orienta; pergunta clínica escala": o Gatilho 2 de `classify`
(`clinical_question` → `intencao_clinica`, P2) abre caso humano para QUALQUER pergunta de saúde, inclusive
"o que é PSA?" ou "pra que serve a dipirona?". A regra vem de ADR-0005/ADR-0008 (decisão clínica por agente =
L0 hard) e de ADR-0012 (quem decide alarme é a DMN, nunca o modelo). O dono mediu em dev (DL-0104, DL-0115)
que isso abre caso para quase tudo e que a pessoa sai sem a informação geral que qualquer site oficial dá.

Três pedidos do dono (17/10/2026), todos atrás de UMA chave e só em dev:

1. a Helena **informa** — sem diagnosticar, sem orientar conduta, sem dose — perguntas gerais de saúde
   (especialidade, medicamento, exame, procedimento, cirurgia, material, condição, vacina, sintoma) a partir de
   uma **base curada versionada no repo**, **sem abrir caso**;
2. "muita dor"/sintoma **sem sinal de alarme** recebe **perguntas de alerta** antes de abrir caso (sinal grave
   continua escalando na hora, com 192);
3. "não quero falar com ninguém"/recusa explícita **com caso aberto** → confirmação → **encerrar** o caso
   (P2/P3); caso P1/grave **não** se encerra sozinho (explica e mantém o 192).

Alternativas consideradas: (a) deixar o modelo responder com instruções de prompt — rejeitada: o texto ao
beneficiário não pode depender da obediência do modelo (a lição de toda a série de cercas de saída);
(b) consultar fontes ao vivo na hora da conversa — rejeitada: sem revisão, sem versão e sem controle de
licença; (c) base curada no repo, texto LITERAL do arquivo, gates de CI — **escolhida**.

## Decisão

### 1. A base e o seu formato

`spec/conhecimento/saude/<categoria>/<id>.yaml`, um assunto por arquivo; categorias `especialidade`,
`medicamento`, `exame`, `procedimento`, `cirurgia`, `material`, `condicao`, `vacina`, `sintoma`; o `id` é
kebab com o prefixo da categoria (`esp-`, `med-`, `exa-`, `pro-`, `cir-`, `mat-`, `con-`, `vac-`, `sin-`) e é
o nome do arquivo. Campos: `id`, `categoria`, `titulo`, `sinonimos`, `texto` (≤ 700 caracteres; no `sintoma`,
o texto de orientação SEM alarme), `tipo_texto` (`proprio` | `citacao_literal`), `fontes` (`nome`, `url`,
`licenca`, `coletado_em`, `sha256_trecho` opcional), `link_leitura` (opcional), `revisao` (`status:
rascunho_dono_aprovado_dev`, `autor`, `aprovado_por: "dono (17/10/2026, dev)"`, `revisao_medica`,
`revisar_ate`) e, só no `sintoma`, `perguntas_alerta` (1 a 3, sim/não), `sinais_alarme` e `populacao`.
O mecanismo (schema, carregador, índice, busca, respostas) é `src/maezo/conhecimento/base_saude.py`. Base que
não carrega **desliga a chave** (falha fechada para o comportamento de antes, com log).

### 2. A resposta é o texto do arquivo, nunca do modelo

Resposta = `texto` literal + linha de crédito curta ("Fonte: …" na citação literal; "Texto da operadora com
base em …" no texto próprio) + `link_leitura` (se houver) + o aviso padrão: "Esta informação é geral e não
substitui avaliação médica. Se quiser, posso te passar para a equipe de saúde." A busca é DETERMINÍSTICA
(sinônimos normalizados, tolerância a acento e a erro leve de digitação; sigla curta só casa exata). Só quando
ela fica ambígua o modelo é chamado para **escolher um id de uma lista fechada de candidatos** (ou "nenhum") —
qualquer outra saída do modelo vira "nenhum". O modelo nunca escreve o conteúdo. Assunto não encontrado:
"Não tenho uma informação aprovada sobre isso por aqui." + onde buscar + oferta de pessoa, sem caso.
Resultado/medida PESSOAL ("meu PSA deu alto") recebe o texto GERAL + "leve o resultado ao seu médico" + a
oferta, sem caso automático e sem interpretar o resultado. A régua de tom (≤ 20 palavras por frase) e as
cercas de saída valem para todo texto da base, e o CI confere cada um.

### 3. A lista FECHADA de fontes (também em `spec/conhecimento/saude/FONTES.md`)

- **Para RESPONDER** (citação literal curta, com crédito e link; gov.br é **CC BY-ND 3.0**: permite uso
  comercial, exige crédito, proíbe obra derivada — por isso a citação é literal): Saúde de A a Z
  (gov.br/saude); páginas **web** do INCA (não os PDFs); Calendário Nacional de Vacinação (gov.br/saude);
  ANS — RN 566/2022 e Rol (ato oficial). **Bula do paciente da ANVISA: por ora só LINK** (licença de
  reprodução pendente no jurídico): nunca `citacao_literal`.
- **TEXTOS PRÓPRIOS** (escritos pela operadora, curtos, citando a fonte oficial em que se apoiam; revisão
  médica pendente): especialidades, procedimentos, cirurgias, materiais, exames sem fonte leiga e o "para que
  serve" de medicamentos (texto próprio curto + link da bula).
- **SÓ VOCABULÁRIO/MAPEAMENTO** (nunca vira resposta nem é a fonte creditada): TUSS (ANS), dados abertos de
  medicamentos da ANVISA, SIGTAP, CBO, CID-11 (OMS). **DeCS** exige acordo de licença: **não incorporar
  ainda** (o CI reprova).
- **NÃO USAR** (o CI reprova): BVS Dicas em Saúde, Caderno de Atenção Básica nº 28, cartilhas/PDFs "não
  comercial" do INCA/MS, OPAS/OMS, Fiocruz, Manual MSD.
- **Leitura do mecanismo, a confirmar pelo dono** (o conteúdo do piloto precisou dela): (i) **ato oficial** que
  não está na lista de responder — resolução do CFM sobre especialidades, lei federal de uma profissão
  (planalto.gov.br) — é aceito SÓ como **apoio de texto próprio** (crédito "Texto da operadora com base em …"),
  nunca como citação literal (texto de ato oficial não tem proteção autoral, Lei 9.610/1998, art. 8º, IV);
  (ii) a **tabela de alarme interna** do repo (`triage_redflag_*`) é aceita como referência do texto de
  orientação do `sintoma`, nunca como fonte creditada (o crédito diz "Texto da operadora."). Qualquer outra
  fonte fora da lista reprova o CI.

### 4. Regras de manutenção (o CI confere — `tests/unit/agents/test_helena_base_saude_conteudo.py`)

Reprova o PR: schema inválido; `revisar_ate` vencido (ciclo de **6 meses**; vencido, o assunto também deixa de
responder em runtime); fonte sem `url`/`licenca`/`coletado_em`; fonte da lista NÃO USAR ou fora da lista;
`citacao_literal` que não seja de CC BY-ND/ato oficial da lista; termo proibido no `texto` (dose/posologia,
quantidade — mg, ml, gotas —, "tome", "pode tomar", "não é grave", "é normal", diagnóstico, promessa de
cobertura); `texto` > 700 caracteres; sinônimo duplicado entre ids; frase > 20 palavras; resposta barrada pelas
cercas de saída da Helena. A frequência de rastreio atribuída à fonte ("o Ministério da Saúde recomenda … a
cada dois anos") NÃO é reprovada pelo CI: é informação geral da fonte, mas fica para a revisão médica
(achado do piloto: `exa-mamografia`). O verificador
`scripts/ops/base_saude_verificar_fontes.py` (fora do CI: faz rede) baixa as fontes, compara `sha256_trecho`
quando houver e lista o que venceu. **Quem aprova:** o dono, em dev; **médico e jurídico antes de produção.**

### 5. A ordem de decisão da Helena com a chave ligada

Os portões de segurança atuais vêm ANTES e ficam intactos: falha do classificador; risco psicossocial (tabela
`mental_health`, CVV 188); tabela de alarme `triage_redflag_*`; emergência com caso aberto; recém-nascido.
Depois: (1) queixa com termo de alarme de um arquivo `sin-*` → escala na hora (192); (2) recusa com caso
aberto → confirmação de encerramento; (3) pergunta informativa (inclusive `clinical_question` sem queixa
pessoal atual) ou resultado pessoal → resposta da base, SEM caso — nunca por cima de pedido explícito de
pessoa (RN 623/2024), ameaça ANS/Procon, cobrança (Lucas) ou consulta/cobertura do plano; (4) a v4 (respostas
às perguntas de alerta, especialista "onde marcar"); (5) os gatilhos de sempre. Sintoma SEM alarme segue para
as perguntas de alerta da Helena v4 (as do arquivo `sin-*` quando ele existe); "sim" → alerta P1 com 192;
"não" → o texto de orientação do arquivo + as opções 1) onde marcar consulta 2) falar com a equipe de saúde.

### 6. MUDANÇA DE REGRA CLÍNICA (dev): o fail-safe de intensidade grave na DOR sem alarme

A regra r10 de `triage_redflag_adult` ("sintoma não mapeado com intensidade grave → enfermagem, P2") deixa de
abrir caso NA HORA quando, e só quando: as chaves da base E da v4 estão ligadas; a saída da tabela é
EXATAMENTE a da r10 (motivo literal, P2, ESCALATE_NURSE — o teste amarra o literal ao arquivo); a população é
adulto (não gestante, não saúde mental, não criança); o relato fala de DOR; e não há sinal de emergência do
léxico nem termo de alarme dos arquivos `sin-*`. Nesse caso o turno vai às perguntas de alerta. **A DMN não
muda** — escolha deliberada pela forma mais segura: a tabela continua sendo consultada e registrada como
sempre (proveniência intacta), nenhuma outra regra dela é tocada (dor no peito, falta de ar, AVC, sangramento,
alergia grave, síncope, febre em idoso, dor abdominal grave continuam escalando), e desligar a chave devolve a
regra antiga sem deploy de tabela. Pediatria, gestante e saúde mental não mudam.

### 7. Encerrar o caso a pedido (emenda v1.1.0 do SP-OP-ESCALATION-001)

O caminho mais AUDITÁVEL: mensagem BPMN `msg.escalation.withdrawn`, correlacionada pela business key, num
**event subprocess interruptivo** (`ESP_EncerramentoAPedido`) que publica `agents.events.escalation.resolved`
com `resultado=encerrado_a_pedido_beneficiario` e termina em `End_EncerradoAPedidoDoBeneficiario` — o motor
registra o fim, o evento de domínio sai antes dele (como em todo fim do processo) e a User Task some da fila
do portal. Rejeitado: `DELETE` da instância (fim `EXTERNALLY_TERMINATED` sem evento de domínio, contra a regra
"nenhum fim sem evento"). A correlação passa por `correlate_message_audited` (auditoria ADR-0007 ANTES do
efeito; falha de auditoria = nenhuma correlação). Os nomes `End_DesistenciaBeneficiario`/`desistencia_beneficiario`
pedidos inicialmente não puderam ser usados: o gate de vocabulário de perspectiva (`perspective.py`, ADR-0040
D7, sem exceção) proíbe "desistência" em `spec/`. Regra: só se encerra caso NÃO grave; `risco_psicossocial`,
severidade `grave` (red flag P1, recém-nascido) ou caso ilegível NÃO se encerram ("Seu caso é de saúde e
precisa de avaliação. Não posso encerrá-lo por aqui. Se for emergência, ligue 192."). O caso `-clin` paralelo
segue a mesma regra.

### 8. A chave e a volta atrás

`MAEZO_HELENA_BASE_SAUDE` (default `false`; settings `helena_base_saude`; var Terraform `helena_base_saude`,
ligada só em `deploy/aws-ecs/envs/dev-sa-east-1/amh-interop.auto.tfvars`; env do receptor e do simulador).
Desligada = a Helena de antes, byte a byte (provado em teste). Voltar: chave `false` + apply direcionado.
Desfazer de vez: revert dos PRs (+ deploy-processes para a v1.0.0 do BPMN, se quiser).

## Consequências

**Positivas:** a pessoa recebe a informação geral aprovada, com fonte, sem fila humana; o texto é versionado,
revisável e conferido pelo CI (licença, termos, régua, cercas); a fonte é auditável por assunto; encerrar um
caso deixa rastro no motor, no evento de domínio e na trilha de auditoria; tudo volta atrás por uma chave.

**Negativas (aceitas):** o conteúdo é RASCUNHO aprovado pelo dono só para dev — **revisão médica e jurídica
antes de produção**; a Helena passa a responder perguntas que antes iam a uma pessoa (mitigado pela oferta de
pessoa em toda resposta e pelos portões de segurança antes); a regra do fail-safe muda para dor sem alarme em
adulto (mitigado pelas perguntas de alerta e pelo "sim" → P1); instâncias anteriores à v1.1.0 não recebem a
mensagem (a pessoa ouve que não foi possível encerrar); a lista fechada exige PR para cada fonte nova.

## Supersedes

— (emenda, só em dev e só com a chave, a regra "pergunta clínica escala" de ADR-0005/ADR-0008 e o Gatilho 2;
ADR-0012 continua: quem decide alarme é a DMN).
