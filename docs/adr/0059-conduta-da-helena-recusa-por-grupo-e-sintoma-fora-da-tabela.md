# ADR-0059: Conduta da Helena — a recusa da cerca de saída decide por GRUPO, e um `sintoma_codigo` fora da tabela da população é descartado

**Status:** Proposed — **NÃO RATIFICADO.** · **Data:** 2026-09-21 · **Área:** Conduta clínica da agente / Triagem / Governança de ADR

> **Enquadramento.** As duas regras abaixo mudam o que a Helena FAZ com um beneficiário — quando
> ela abre uma fila humana e qual código clínico ela leva à tabela. Elas foram implementadas no PR
> #460 (`fix/helena-criticos-da-bateria-22-09`, commit `613f48bc`) e hoje vivem **apenas em
> docstring de código**, que é o lugar onde ninguém que assina conduta lê.
>
> Este ADR existe para que o **dono clínico** possa **RATIFICAR OU DERRUBAR** cada uma delas,
> separadamente. Ele **não preenche nenhum campo de assinatura**: `owners.clinico`
> (`spec/agents/helena/agent.yaml:38`) segue vazio, e a cerca
> `tests/unit/agents/test_helena_dono_declarado.py` só passa a exigi-lo quando
> `docs/processes/contracts/signoffs/SP-OP-ESCALATION-001.signoff.yaml` existir. Assinatura é ato
> de pessoa; nenhum agente assina no lugar dela.
>
> **Escopo docs+spec.** Este ADR registra conduta **já implementada e em produção**; não propõe
> mudança de código, não edita ADR aceito e não altera nenhuma allowlist.

---

## Contexto

A bateria de 22/09/2026 mediu a Helena contra mensagens escritas como gente escreve. Dois achados
não eram bugs de implementação: eram **condutas** que nunca tinham sido decididas por ninguém.

### O que foi medido para D1 — a fila humana dependia da redação do modelo

Entre 13/09 e 21/09, **qualquer** rascunho barrado pela cerca de saída num turno `inform` virava
escalonamento com `motivo_categoria="falha_tecnica"`.

O custo apareceu em dois casos com **estado clínico idêntico** — extração sem sintoma, DMN no
catch-all, `red_flag=false`:

| Caso | Mensagem | O que o rascunho do modelo dizia | Desfecho |
|---|---|---|---|
| `C1` | *"estou com dor de cabeça"* | *"a equipe entre em contato"* | chamado P3 `atendimento-humano`, rotulado **falha técnica** |
| `B5` | *"estou com um pouco de dor de garganta desde ontem"* | (nada que a cerca barrasse) | nenhum chamado |

A mesma dor, o mesmo veredito de tabela, desfechos opostos. **Quem decidia a abertura de fila
humana era a frase que o modelo sorteou**, não critério clínico nenhum — e o rótulo *falha técnica*
era falso: não havia falha técnica alguma.

### O que foi medido para D2 — código de adulto sobreviveu à fusão numa conversa pediátrica

Mesmo telefone, três turnos: *"meu filho está com febre"* → *"3 anos"* → *"desde ontem"*. No turno
3 (`D2`) a extração trouxe `sintoma_codigo="cefaleia_subita_intensa"` — a `r4` da tabela **adulta**,
P1 com prazo de cinco minutos — numa conversa cujo único sintoma relatado foi **febre** e cuja
população é `pediatric`. Esse código **não existe** na tabela pediátrica.

A regra do qualificador (`classify-v4`, 21/09) não alcançava isto: ela mora no texto do prompt, e o
prompt só vê a mensagem do turno — *"desde ontem"*. Quem produziu a incoerência foi a **fusão**: o
modelo devolveu `population="adult"` (campo obrigatório, preenchido com o valor mais comum quando
ninguém foi citado na mensagem), a memória clínica corretamente impôs `pediatric` por falta de
lastro, e o código de adulto sobreviveu à troca. O par **saiu do modelo coerente e ficou incoerente
depois**.

---

## Decisão

### D1 · Escalar por recusa de saída é decisão por GRUPO da recusa, não por rota

Quando a cerca de saída recusa o texto redigido pelo modelo num turno `inform`:

- **`negativa_clinica` continua escalando.** Afirmar que não há sinal de alerta é exatamente o que
  a Helena não pode fazer sozinha — essa frase só nasce de User Task. Se era isso que ela tinha a
  dizer, ela não tinha nada a dizer, e quem tem é um humano. Escala como `falha_tecnica`, com
  severidade **derivada** da classificação já feita (HEL-04), nunca de um literal.
- **`promessa_de_humano`, `promessa_de_capacidade` e `canal_nao_confirmado` viram troca de TEXTO.**
  São defeitos de **redação** num turno cuja tarefa era orientar. A resposta honesta não é abrir
  uma fila que ninguém pediu: é não mentir. O texto é substituído pela constante
  `RESPOSTA_INFORM_RECUSADA`, o turno **segue `inform`**, e o rastro continua inteiro — log,
  contador (`record_resposta_recusada`, com `motivo=<grupo>` e `response_kind`) e o campo `error`
  do próprio turno.

A pergunta é feita ao **TEXTO** (`motivo_de_recusa`, que é pura), nunca ao atributo da exceção
ligada: `RespostaRecusadaError.grupo` é leitura proibida pela cerca LUC-06/NEW-01, e o grupo da
exceção depende da **ordem** das duas cercas — um texto que cita canal inventado *e* afirma ausência
de alerta clínico tem de escalar pelo segundo motivo, não virar troca de texto porque o primeiro
venceu o log.

É a mesma assimetria por grupo que a rota `collect` já aplicava desde 21/09.

### D2 · Um `sintoma_codigo` incoerente com a população do turno é descartado, e a tabela é consultada sem sintoma

Depois da fusão da memória clínica e **antes** de qualquer decisão, o código é confrontado com o
**vocabulário da tabela que este turno vai consultar**. Se ele não existir lá, vira `None` e o turno
segue: as quatro tabelas tratam a ausência no catch-all, e a conversa continua lembrando qual
sintoma já avaliou.

Quatro delimitações fazem parte da decisão:

1. **A pergunta é feita à TABELA, não à mensagem.** `adult` + `cefaleia_subita_intensa` continua
   passando em qualquer redação. O vocabulário por população é o mesmo que o `classify_prompt`
   lista, e `test_helena_codigos_casam_com_a_dmn.py` já fixa que ele é idêntico aos literais das
   `spec/processes/dmn/triage_redflag_*`.
2. **Ausência é coerente** (`None` → aceito): turno sem sintoma é o caso comum.
3. **População fora do mapa cai no vocabulário de adulto** — exatamente o default de `_evaluate_dmn`.
   As duas leituras têm de olhar a mesma tabela, senão a cerca mede outra coisa.
4. **`psychosocial_risk` fica fora.** O gatilho 5 força a tabela `mental_health` por outro caminho e
   tem prioridade máxima; recortar o código dele aqui mudaria qual regra daquela tabela casa, e isso
   é decisão clínica, não higiene de extração.

---

## Alternativas rejeitadas

**A1 (D1) — manter tudo escalando (o status quo até 21/09).** Rejeitada pela medição: torna a fila
humana função da frase que o modelo sorteou (`C1` × `B5`), e gasta atendimento humano com um chamado
rotulado *falha técnica* onde não houve falha técnica. Um rótulo falso na fila treina a operação a
desacreditar o rótulo.

**A2 (D1) — decidir pelo atributo `RespostaRecusadaError.grupo`.** Rejeitada por LUC-06/NEW-01 (um
ramo de hoje é um campo de estado amanhã) e porque o grupo depende da ordem das cercas.

**A3 (D1) — pedir ao modelo um segundo rascunho.** Rejeitada: o mesmo prompt com a mesma mensagem
tende ao mesmo texto, e um laço de tentativas transforma uma cerca num atraso (mesma escolha de
CC-01 e da cerca TEXTO × FATO). A troca é por **constante**, e a constante passa nas quatro cercas
por construção.

**A4 (D2) — a cerca literal que exige as palavras "súbita"/"intensa" na mensagem** (a que
`prompts.py` recusou escrever). Rejeitada porque reprovaria o caso `C4` (*"começou de repente e é a
pior da minha vida"*), que **diz** o qualificador com outras palavras e tem de continuar produzindo
o código. Deixar de traduzir isso esconderia uma emergência real.

**A5 (D2) — tratar o código incoerente como `falha_tecnica` e abrir fila.** Rejeitada pelo mesmo
motivo de D1, do outro lado: transformar cada escorregão de redação do modelo numa fila humana é o
defeito que D1 corrige.

**A6 (D2) — substituir o código incoerente pelo sintoma lembrado da conversa.** Rejeitada: lembrar
sintoma entre turnos é uma decisão já tomada em sentido contrário (`_correcao_de_dado_avaliado`), e
reabri-la aqui responderia à mensagem errada.

---

## Residual conhecido — o que o dono clínico está sendo convidado a aceitar, e não a ignorar

**R1 · D1 desliga uma rede de segurança ACIDENTAL, e ela existia.** A classificação erra
`human_request` → `information`. Até 21/09, parte desses turnos ainda chegava à fila humana por um
caminho torto: o modelo redigia uma promessa de atendimento humano, a cerca `promessa_de_humano`
barrava, e a rota escalava `falha_tecnica`. Fila aberta **pelo motivo errado e com o rótulo errado
— mas aberta**. Com D1, esse turno vira troca de texto e **não abre mais fila**.

O que mitiga é a própria constante, que pede em palavras: *"Se você quiser falar com uma pessoa,
escreva isso na próxima mensagem"*. **O custo é um turno**, e é pago por quem já tinha pedido. Se o
dono clínico julgar esse turno caro demais, o conserto certo **não** é voltar a escalar por redação:
é a classificação parar de errar `human_request`, cobrada por caso no corpus de extração. Derrubar
D1 devolve o `C1` × `B5`; mantê-la deixa este residual de pé até a extração melhorar.

**R2 · D2 pode descartar um código clinicamente correto** quando a população fundida estiver errada
— a memória impõe a população por falta de lastro, e uma população lembrada errada derruba um código
legítimo do turno, que então vai ao catch-all. O lado escolhido é o **verificável**: o oposto foi o
defeito medido (P1 de cefaleia adulta numa conversa pediátrica). Qual dos dois erros é pior é
**conduta**, e é do dono clínico.

**R3 · A observabilidade dos dois desfechos existe, mas é inferida.** O contador emitido na recusa
carrega `motivo=<grupo>` e `response_kind`; **não há contador que separe diretamente "recusa que
escalou" de "recusa que só trocou o texto"**. Um painel que queira acompanhar D1 tem de codificar a
regra `negativa_clinica` ⇒ escalou. É observável hoje; não é auto-explicativo.

**R4 · A régua de extração passa a ter seis regras e a cerca do corpus conhece cinco.**
`tests/unit/evals/test_corpus_de_extracao.py:56` fixa `{R1..R5}` e exige ≥3 casos por regra. A `R6`
escrita agora em `docs/design/regua-de-extracao.md` **não é cobrada por caso**: o caso
`e1-pergunta-sobre-dor-toracica` existe no corpus `extracao-v2` e está rotulado `R1`/`R5`. Fechar
isso é uma linha de teste mais a rerrotulagem de pelo menos três casos, e está fora do escopo deste
ADR.

**R5 · Não há dono clínico.** `owners.clinico` está vazio, as tabelas de triagem seguem
`CONTEUDO CLINICO: DRAFT` e as 34 regras não foram assinadas. Este ADR não fecha esse buraco; ele
nomeia duas decisões que estavam caindo nele.

---

## Consequências

**Positivas.** A abertura de fila humana deixa de depender da redação do modelo e passa a depender
de um critério nomeado, que cabe numa frase e pode ser discutido por quem entende de conduta. Um
código de outra população para de chegar à tabela — e a tabela para de emitir P1 a partir de um
código que ela não contém. Os dois comportamentos passam a ter um documento que o dono clínico pode
derrubar, em vez de uma docstring que ele nunca vai ler.

**Negativas (aceitas).** R1 e R2, acima, por extenso. E uma terceira, estrutural: a Helena agora
tem **duas** portas por onde um defeito do modelo é silenciosamente absorvido (texto trocado, código
descartado). As duas deixam rastro, e nenhuma delas é uma retentativa — mas absorver defeito em
silêncio é uma postura, e ela só é defensável enquanto o rastro for lido.

---

## Relação com ADRs, contratos e specs existentes

- **`spec/agents/helena/agent.yaml` — `escalation.triggers`.** O gatilho `signal: tool_failure ->
  falha_tecnica` **continua válido e não muda**; o que mudou foi seu **alcance**, e a nota de alcance
  foi escrita junto ao gatilho no mesmo PR deste ADR. Nenhum gatilho foi adicionado ou removido.
- **SP-OP-ESCALATION-001.** O vocabulário de `motivo_categoria` é o mesmo; D1 não cria categoria nova.
- **ADR-0052** — precedente direto: conduta da Helena registrada em ADR próprio, sem editar ADR
  aceito (`docs/adr/README.md:8`).
- **ADR-0012** — a DMN segue sendo a única ferramenta determinística. D2 é higiene da **entrada** da
  tabela, não regra de tabela: nenhuma das 34 regras é tocada.
- **`docs/design/regua-de-extracao.md`** — a `R6` (o quadro e o pedido são campos diferentes) é a
  contrapartida de extração do mesmo achado; ela é régua, não conduta, e continua esperando a mesma
  assinatura clínica.

---

## Ratificação

Ato do **dono clínico** — quem assina as 34 regras e a régua de extração. Enquanto o status for
`Proposed`, **nada aqui está ratificado**. O comportamento descrito, porém, **já está em produção**,
e é isso que torna a ratificação urgente em vez de formal: derrubar D1 ou D2 é uma mudança de código,
e quanto mais tarde ela vier, mais conversas terão passado pela conduta não assinada.

Campos abaixo deixados **deliberadamente em branco** — nenhum agente os preenche:

| Campo | Valor |
|---|---|
| Dono clínico | *(vazio — nasce do signoff `SP-OP-ESCALATION-001.signoff.yaml`, que ainda não existe)* |
| Ratificado por | *(em branco)* |
| Data da ratificação | *(em branco)* |
| Veredito sobre **D1** (recusa por grupo) | *(pendente: ratifica / derruba)* |
| Veredito sobre **D2** (sintoma fora da tabela) | *(pendente: ratifica / derruba)* |
| Veredito sobre o residual **R1** (rede de segurança acidental) | *(pendente: aceita / exige conserto na extração antes)* |

## Supersedes

Nenhum.
