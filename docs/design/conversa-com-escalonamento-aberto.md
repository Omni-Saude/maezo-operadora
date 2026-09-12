# Passo 4: a Helena reconhecer o caso que já está aberto

**Origem:** achado do PR #380 e §"Passo 4" de `CORRECAO_RESPOSTA_HELENA.md` (diretor, 12/09/2026).
**Estado:** proposta. **Uma decisão de mérito está em aberto e está marcada abaixo.**

Modelado em `docs/design/triage-suficiencia-coleta.md`: descreve o que construir e deixa
explícito o que ainda não foi decidido, em vez de escolher em código.

## O que sobrou depois dos passos 1 a 3

O achado original tinha duas metades:

1. a resposta **afirmava** que não havia sinais de alerta;
2. a Helena **não sabia** que aquela conversa já tinha um P1 aberto.

`response-v3` (PR #382) fechou a metade 1, e fechou em toda conversa, não só nas que têm caso
aberto. **Isso muda a natureza do passo 4.** Ele deixou de ser correção de segurança e passou a
ser melhoria de qualidade: a resposta já não é perigosa, mas continua ignorando um caso em
andamento, e isso é ruim para quem está do outro lado.

A consequência prática importa para a decisão de falha mais abaixo: **se o passo 4 falhar, o pior
resultado é a resposta de hoje**, que já não nega alerta. Não é mais um cenário de risco.

## O comportamento proposto

Antes de a rota informativa concluir o turno, saber se existe escalonamento **aberto** para
aquela conversa. Havendo, a resposta reconhece o caso em andamento em vez de tratar a mensagem
como se fosse a primeira.

A chave já existe e o grafo já a monta: `ESC-{tenant}-{conversation_id}`.

O que NÃO muda: a rota continua sendo `inform`, nenhum processo novo é aberto, e a DMN continua
sendo quem decide bandeira. A diferença está no texto e no contexto que ele recebe.

## De onde vem o fato — e é aqui que está a decisão

Há dois lugares onde "esta conversa tem caso aberto" pode ser lido, e eles têm custos bem
diferentes.

### Opção A — perguntar ao motor

Consultar `process-instance?businessKey=ESC-{tenant}-{conversation_id}` antes de responder.

- **Custo:** uma chamada de rede a mais em **todo** turno informativo.
- **Novo modo de falha:** o motor fora do ar passa a afetar um caminho que hoje não depende dele.
- É a fonte **autoritativa**: reflete inclusive um escalonamento encerrado por um humano no
  Cockpit entre um turno e outro.

### Opção B — ler o próprio estado persistido

O turno já roda com checkpoint (`checkpointed=True` no log do despacho), e `HelenaState` já tem
`escalation_business_key` e `escalation_started`. Se o estado do turno anterior sobrevive na
thread, o fato está em memória e **não custa chamada nenhuma**.

- **Custo:** zero.
- **Sem novo modo de falha.**
- **Não é autoritativo:** se um humano fechou o escalonamento no Cockpit, o checkpoint não sabe.

> **MEDIÇÃO QUE DECIDE, e que ainda não foi feita.** `new_helena_state` devolve apenas os cinco
> campos de entrada, e o LangGraph mescla isso no estado persistido da thread — então em tese os
> campos do turno anterior continuam lá. **Mas o log do turno 2 da reprodução de 12/09 mostrou
> `escalation_business_key=None`**, o que sugere que a carga não acontece como se suporia, ou que
> algum nó limpa o campo. Antes de escolher a opção B é preciso ler uma linha da tabela de
> checkpoint no Aurora e ver quais chaves estão gravadas para aquela thread. Enquanto isso não
> for medido, a opção B é uma hipótese, não um plano.

### Recomendação

Medir primeiro. Se o checkpoint carrega o campo, **opção B**: custo zero, sem novo modo de falha,
e o risco residual (escalonamento fechado no Cockpit sem a Helena saber) produz apenas uma frase
levemente desatualizada — não uma frase perigosa, porque `response-v3` já proibiu a negativa.

Se não carrega, **opção A com falha ABERTA**: motor indisponível não pode transformar um turno
informativo em falha técnica. Esta é a parte contraintuitiva e vale dizer por quê: em todo o resto
do grafo a falha é FECHADA (ADR-0028 §3), porque ali o que está em jogo é deixar de escalar uma
emergência. Aqui não: falhar é voltar ao texto de hoje, que já é seguro. Fechar a falha aqui
transformaria cada instabilidade do motor numa fila humana a mais, o que piora o atendimento sem
proteger ninguém.

## O que ainda não está decidido, e é de quem ratifica

**Qual texto a resposta usa quando há caso aberto.** Reconhecer o caso é uma afirmação sobre o
atendimento de alguém, e as duas formas plausíveis dizem coisas diferentes:

| Forma | O que comunica | Risco |
|---|---|---|
| "Seu caso já está com a nossa equipe e alguém vai falar com você" | tranquiliza, evita mensagem repetida | pode ser lido como "não faça mais nada" |
| "Já encaminhamos seu caso; se piorar, procure emergência" | mantém a orientação de alerta | repete a cada turno e pode soar alarmista |

Não escolhemos. **Vai junto com o material do médico auditor**, pela mesma razão que as 34 regras
vão: é uma frase dita a alguém em atendimento clínico.

## O que este passo não resolve

A Helena continua de um turno só em tudo o mais. Reconhecer um caso aberto não é memória de
conversa nem coleta: a segunda mensagem continua sendo classificada do zero. Os estados 1 e 2 da
jornada seguem dependendo do nó de coleta (desligado, esperando `triage_sufficiency`) e do elo
telefone → beneficiário (que não existe em nenhuma das duas repos).
