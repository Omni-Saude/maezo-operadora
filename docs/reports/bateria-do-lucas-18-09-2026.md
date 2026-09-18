# A bateria do Lucas — o risco não estava onde o documento supôs

**Status: MEDIÇÃO + ENTREGA, 18/09/2026.** Fecha o item "bateria do Lucas" de
`HELENA_EM_PRODUCAO_O_QUE_FALTA.md` — e corrige a premissa dele, porque a medição mostrou que o
risco do Lucas é de outra natureza, e maior.

---

## A premissa do documento, e por que ela não se sustenta

> *"Se a extração da Helena tinha os problemas que a bateria encontrou, não há razão para supor
> que a do Lucas esteja melhor."*

O raciocínio é bom e a conclusão está errada por um detalhe de arquitetura que ninguém tinha
olhado: **o Lucas não tem extração.**

O campo `intencao` dele é **entrada**, não saída de modelo. Ele chega no estado vindo do chamador,
é validado em `receive` contra uma lista fechada (`_VALID_INTENCOES`) e, fora dela, cai fechado em
`ambiguidade` — que roteia para humano. Não há prompt que leia texto livre e produza um campo
estruturado, que é exatamente o mecanismo que na Helena produziu os defeitos da bateria de 12-13/09.

O mesmo vale para o resto do roteamento: `Route`, `AdmissibilidadeCobranca` e
`RoteamentoEscalacao` são 100% derivados de DMN (ADR-0012). O modelo não escolhe nada.

**Uma bateria de extração contra o Lucas não teria achado nada, porque não há extração para
quebrar.** Repetir o formato da bateria da Helena aqui teria produzido um relatório verde sobre
uma superfície que não existe — e fechado o item com a sensação de que o Lucas foi verificado.

## Onde o risco dele mora de verdade

O Lucas chama o modelo em **três** lugares. Dois chegam ao beneficiário:

| Chamada | Quem lê | Cercado antes de 18/09 |
|---|---|---|
| `_build_message` (jornada informativa/lembrete) | o beneficiário | **nada** |
| `_build_escalation_ack` (o aviso depois da escalação) | o beneficiário | **nada** |
| `_build_dossier` (narrativa do encaminhamento) | um atendente humano | `decisao_cancelamento=None` |

A medição é de uma linha: `grep -c "motivo_de_recusa" src/maezo/agents/lucas/graph.py` devolvia
**0**, enquanto na Helena devolvia 3.

O que existia no lugar da cerca eram **proibições em prosa**, e elas são explícitas:

- `SYSTEM_PROMPT`: *"Voce NUNCA ameaca suspensao ou cancelamento, NUNCA comunica uma negativa"*
- `message_prompt()`: *"NUNCA comunique uma negativa, suspensao ou cancelamento — isso e sempre de
  um humano"*
- `escalation_ack_prompt()`: *"NUNCA revele um desfecho adverso (suspensao, cancelamento, negativa)
  — nenhuma decisao foi tomada ainda"*

**Essa é exatamente a forma do `response-v3` da Helena**, e ela foi medida não se sustentando: em
13/09/2026 o modelo passou por cima da proibição de negativa clínica **duas vezes na mesma
conversa**, com o raciocínio inteiro escrito no prompt. Esse achado é sobre a **forma** da
proteção, não sobre a agente.

## Por que a aposta do Lucas é maior, não menor

O que passava pela cerca ausente não é uma frase imprecisa. É **um beneficiário cujo contrato está
em análise sendo informado por um robô de que foi suspenso ou cancelado antes de qualquer humano
ter decidido.**

A decisão nasce numa User Task — `SP-OP-ESCALATION-001` → `SP-OP-CANCEL-001`, onde uma pessoa
escolhe RESCINDIR/MANTER/SUSPENDER. O lado do **humano** já tinha cerca estrutural
(`decisao_cancelamento` é sempre `None`, o dossiê nunca carrega o desfecho). O lado do
**beneficiário** — o único texto que a pessoa do outro lado lê — não tinha nenhuma.

E o momento é o pior possível: o ACK de escalação chega justamente a quem acabou de ter o caso
encaminhado por inadimplência, contestação ou pedido de cancelamento.

---

## O que foi entregue

### A cerca de saída (`prompts.py::motivo_de_recusa`, `recusa-lucas-v1`)

Função **pura** — texto, rota e fatos do turno, nada mais —, testável com os textos reais sem subir
grafo, engine nem modelo. Quatro grupos fechados, na ordem em que um texto que viola mais de um é
reportado (o mais grave primeiro):

1. **`desfecho_adverso`** — suspensão/cancelamento/rescisão/negativa **afirmados ou ameaçados**.
   Proibido em **toda** rota: não existe rota em que a decisão já exista.
2. **`promessa_de_capacidade`** — emitir 2ª via, dar baixa, parcelar, cancelar. Exige *escrever* no
   Tasy, e o TASY write DROP (ADR-0013) proíbe isso por decisão de arquitetura — não é
   funcionalidade que falta construir, é vedada. Proibido em toda rota.
3. **`valor_sem_fato`** — quantia em reais que não está nos fatos do turno. Hoje **nenhuma** está,
   porque o dicionário de fatos não tem campo de valor; a cerca é escrita como comparação com os
   fatos, e não como proibição cega de "R$", para que no dia em que a conciliação trouxer valor ela
   aceite aquele e continue recusando os outros.
4. **`promessa_de_humano`** — **rota-condicional**, e essa é a parte delicada: no ACK um processo
   foi mesmo aberto (`send_escalation_ack` só roda com `process_started is True`) e prometer é
   *obrigatório*; na jornada informativa, que nunca abre processo, a mesma frase é mentira.

**O que a cerca deixa passar, de propósito:** *"recebi seu pedido de cancelamento"* (a palavra é o
assunto legítimo de metade das conversas do Lucas) e *"você pode entrar em contato com a central"*
(orientação, não promessa). Por isso os padrões são sujeito+verbo e verbo+objeto, nunca a palavra
solta — uma cerca que barra o caminho certo acaba desligada.

### A aplicação (`graph.py::_cercar_saida`)

Ponto único compartilhado pelos dois textos que chegam ao WhatsApp. Numa recusa: o **padrão** vai
para o log (onde alguém depura), o **grupo** para o contador
`maezo_agent_resposta_recusada_total{agent_id="lucas",motivo,response_kind}` (onde alguém conta), e
o **texto recusado não vai para lugar nenhum** — é saída de modelo sobre a cobrança de uma pessoa.

**Não há segunda tentativa**, pelo mesmo motivo que na Helena: o mesmo prompt com os mesmos fatos
tende ao mesmo texto, e um laço de tentativas transformaria uma cerca num atraso. O beneficiário
recebe uma constante segura, e as duas constantes **passam na própria cerca** — seguras por
construção, não por sorte da redação. A informativa não promete humano nenhum, porque naquela rota
isso seria falso.

O desfecho do turno vira `resposta_recusada_na_saida` em vez de `resposta_informativa_enviada`:
chamar de "resposta enviada" afirmaria um atendimento que não aconteceu.

**O dossiê continua sem cerca, e isso é decisão.** Ele é lido por um atendente que precisa ver a
inadimplência e o pedido de cancelamento *nomeados* para decidir; aplicar ali a cerca do
beneficiário apagaria exatamente o que o humano tem de saber. Há um teste que registra isso como
escolha, para que não vire esquecimento.

### Duas derivas encontradas de passagem

- **O ACK de escalação não tinha versão de prompt.** O único texto do Lucas que chega ao
  beneficiário no caminho **adverso** era também o único sem número — olhando uma mensagem que
  vazasse, não havia como dizer qual redação a produziu. Agora é `escalation-ack-v1`.
- **O `agent.yaml` declarava dois dos cinco prompts.** `dossier-v1` existia desde sempre e nunca
  esteve lá. O Lucas não tinha a cerca que a Helena ganhou em 14/09 comparando `PROMPT_VERSIONS`
  com o yaml; agora tem (`tests/unit/agents/test_lucas_dono_declarado.py`).

---

## O que estes testes provam e o que eles não provam

24 testes em `tests/unit/agents/test_lucas_cerca_de_saida.py`. Suíte do Lucas: **1592 passando**.

O **pareamento** é o teste que importa: cada linha cita a proibição **literal** do prompt e o texto
que a viola, e verifica as duas coisas. Se alguém reescrever a proibição, ele fica vermelho antes
de a cerca ficar desalinhada em silêncio — que é o modo de falha de uma lista de padrões solta, e
que ninguém percebe até o dia em que ela deixa passar.

**Uma diferença de honestidade em relação à cerca da Helena, e ela precisa ficar registrada.** Os
padrões da Helena são a **transcrição de vazamentos medidos ao vivo**. Os do Lucas **não são**:
ninguém rodou uma bateria ao vivo contra ele, e os textos deste conjunto são derivados das
proibições que os próprios prompts dele escrevem, frase por frase. A cerca está justificada pelo
achado sobre a *forma* da proteção, não por defeito observado no Lucas.

**E o que nenhum destes testes prova é que o modelo obedece.** Isso só um turno ao vivo mostra — e
a razão de a cerca existir é precisamente que ele não obedece só porque pediram.

## O que continua pendente, e de quem é

| Item | De quem |
|---|---|
| Rodar uma bateria **ao vivo** contra o Lucas e medir a taxa de recusa real | engenharia, depende de um apply em dev |
| `owners.tecnico` com nome de pessoa (hoje `reports_to: atendimento@amh`, que é time) | dono do produto — mesmo item do `owners.clinico` da Helena |
| Alerta sobre `maezo_agent_resposta_recusada_total{agent_id="lucas"}` | engenharia, junto do apply do coletor |

O teste de dono está escrito e em `xfail(strict=False)`: ele fica verde no dia em que alguém
preencher o campo, em vez de o campo ser esquecido por não haver nada apontando para ele.
