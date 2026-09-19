# A Helena errou com um beneficiário de verdade

**Frente 6 de `HELENA_EM_PRODUCAO_O_QUE_FALTA.md`.** Declarado em `spec/agents/helena/agent.yaml`,
campo `owners.runbook_incidente`.

Este documento existe para que a pessoa chamada às três da manhã saiba o que fazer. Ele trata do
erro **com uma pessoa do outro lado** — não de indisponibilidade, que é incidente de plataforma.

## O que conta como erro aqui

Quatro formas, em ordem de gravidade:

1. **A Helena minimizou um quadro clínico.** Disse que não havia sinais de alerta, que não era
   grave, ou que não precisava procurar atendimento. É o pior deles: a pessoa pode ter deixado de
   procurar ajuda por causa da frase.
2. **A Helena prometeu algo que não aconteceu.** Disse que alguém entraria em contato, ou que
   encaminhou uma solicitação, sem processo aberto. A pessoa está esperando um retorno que não vem.
3. **A triagem classificou errado.** Emergência tratada como rotina, ou o contrário.
4. **Vazou dado de outra pessoa.** Qualquer resposta que contenha informação que não é de quem
   escreveu.

As formas 1 e 2 têm cerca no código (`_respond_llm`, ver `prompts.py`). **Um incidente dessas duas
significa que a cerca falhou ou foi contornada** — e isso muda o que se faz no passo 3.

## Quem é avisado, e em quanto tempo

| Quem | Quando |
|---|---|
| Dono técnico (`owners.tecnico`) | imediatamente, em qualquer uma das quatro formas |
| Dono clínico (`owners.clinico`) | imediatamente nas formas 1 e 3; no mesmo dia nas outras |
| Gestor de atendimento (`reports_to`) | quando houver beneficiário afetado identificado |

Vazamento de dado (forma 4) é também incidente de privacidade: segue o caminho da LGPD além
deste, e o processo `SP-OP-LGPD-DSR-001` já existe para o direito do titular.

## Os cinco passos

### 1. Preservar a evidência antes de mexer

```bash
aws logs tail /ecs/maezo-operadora-dev/webhook-receiver --since 2h > incidente-<data>.log
```

O texto que a Helena enviou **não** fica no log por desenho — o que fica é o desfecho, o
`conversation_id` pseudonimizado e a rota. Para recuperar o texto é preciso o
`conversation_id`, e ele chega pela reclamação ou pelo Cockpit. Anote-o antes de qualquer deploy:
um redeploy zera os contadores em memória e rotaciona a task.

### 2. Decidir se o canal continua no ar

A pergunta é uma só: **o defeito atinge outras pessoas agora?**

- Um caso isolado de classificação errada (forma 3): o canal continua, e o caso vira teste.
- Uma frase que minimiza quadro clínico (forma 1): **desligue**. Se saiu uma vez, sai de novo.
- Promessa não cumprida (forma 2): desligue se for sistemática; um caso isolado pode esperar.

### 3. Como se desliga

Duas alavancas, e a diferença importa:

**Parar de responder** — o receptor para de despachar turnos. As mensagens que chegarem não são
atendidas por ninguém.

```
terraform -chdir=deploy/aws-ecs/envs/dev-sa-east-1 apply \
  -target=aws_ecs_service.webhook_receiver -var webhook_receiver_desired_count=0 \
  <mais o var-file da atestação PHI e as variáveis do runbook de publicação>
```

**Fazer tudo cair em humano** — preferível quando há gente na fila para atender. Tirar a Helena do
provedor de inferência faz todo turno virar falha técnica e escalar, que é o comportamento
fail-closed já existente:

```
... -var helena_zona_phi=false ...
```

A segunda é a que mantém o beneficiário atendido. **A primeira deixa a pessoa sem resposta
nenhuma** — só use se a fila humana também não puder receber.

Em qualquer das duas, confirme no `plan` que só os recursos pretendidos mudam, e passe sempre o
var-file da atestação PHI, senão o mesmo apply derruba `rafael` e `marina` para o provedor
simulado.

### 4. Se foi a forma 1 ou 2, a cerca falhou

Procure a recusa que não houve:

```bash
# o contador sobe quando a cerca barra algo
curl -s http://webhook-receiver.maezo-operadora-dev.internal:8080/metrics | grep resposta_recusada
```

- **Contador subiu e o texto saiu mesmo assim**: a cerca barrou outra coisa e deixou esta passar.
  O texto real entra na lista de padrões (`prompts.py`) com teste, e a versão `recusa-*` sobe.
- **Contador não subiu**: o padrão não estava na lista. Mesmo caminho, e vale revisar a família
  gramatical inteira — foi assim que "eu encaminho" e "entre em contato" escaparam em 13/09.

**O texto real vira teste antes da correção**, para a cerca ser vista pegando o defeito.

### 5. Falar com a pessoa

Decisão do dono clínico nas formas 1 e 3. O canal não retoma a conversa sozinho: quem procura o
beneficiário é gente, pelo caso já aberto no CIB Seven ou pela central.

## O que NÃO fazer

- **Não apague o log nem a conversa.** O rastro é o que permite dizer o que aconteceu.
- **Não corrija só o prompt.** Pedir ao modelo não é impedir o modelo — foi a lição de 13/09, e
  é por isso que a cerca de saída existe.
- **Não religue sem o teste.** Se o defeito não tem caso no conjunto, ele volta.

## Depois

Todo incidente fechado deixa três coisas: o caso nos conjuntos de teste, o padrão na cerca quando
couber, e uma linha neste runbook se o procedimento não serviu.
