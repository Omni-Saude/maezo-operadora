# Páginas servidas pelo Canal de Teste

Qualquer `.html`, `.css` ou `.js` colocado aqui é servido em
`https://maezo-teste-dev.austa.com.br/p/<arquivo>` — atrás do Cloudflare Access que já
protege o Canal.

## Por que isto existe em vez de um token de serviço da Cloudflare

Uma página que roda na máquina de alguém e precisa falar com o motor na AWS tem duas saídas:

1. **Token de serviço da Cloudflare Access** — a página autentica com um par
   `CF-Access-Client-Id` / `CF-Access-Client-Secret`. É o mecanismo próprio da Cloudflare
   para automação e é revogável. Mas é uma credencial que **contorna a autenticação humana**:
   quem a tiver entra sem passar por e-mail nenhum, e ela vive no arquivo da página.
2. **Servir a página aqui.** Ela passa a ser mesma-origem que o motor (`/engine`) e que o
   agente (`/agente`), então não precisa de credencial, não precisa de CORS, e continua atrás
   do Access — a mesma porta pela qual as pessoas já entram.

A 2 é melhor por eliminação: entrega o mesmo resultado sem criar credencial nova nem
superfície nova. O token continua sendo a resposta certa para automação que roda **fora** do
navegador de uma pessoa (um CI, um robô) — não é o caso aqui.

## O que a página pode chamar, mesma-origem

| Caminho | Vai para |
|---|---|
| `GET/POST /engine/...` | `engine-rest` do CIB Seven (motor BPMN/DMN) |
| `POST /agente/v1/autorizacoes` | ingresso do Rafael — **executa um turno** |
| `POST /receptor/simular` | receptor de webhook — **executa um turno da Helena** (só em dev) |

`exemplo.html` exercita as duas e serve de referência mínima.

## O que NÃO é mesma-origem: o Portal Maezo

`escalonamento.html` tem um painel **"O QUE O PORTAL VÊ"** que fala com o Portal Maezo, e o portal
vive em **outro domínio** (`https://portal-maezo-dev.austa.com.br`). Então ali não vale a regra
acima: a página chama o portal com `credentials: "include"` e o portal aceita a origem deste Canal
por **CORS com credenciais, apenas em dev**.

**A cerca deste lado** é o `server.py`: `PORTAL_PUBLIC_ORIGIN` só é aceita quando `MAEZO_ENV=dev`.
Fora de dev — ou sem `MAEZO_ENV` — ele **recusa** a variável, diz o motivo no log de boot e a
página recebe string vazia, entrando no estado "portal não configurado". É a cerca 3.3 do mandato
de 21/09/2026, e `tests/unit/platform/test_canal_portal_origin.py` reprova quem a afrouxar. A
página é demonstração declarada, sem autenticação própria: ligá-la ao portal não pode transformá-la
em porta de entrada do portal.

`window.PORTAL_PUBLIC_ORIGIN` é injetada por substituição de um marcador (`__PORTAL_PUBLIC_ORIGIN__`)
na hora de servir o `.html` — o arquivo no repositório continua sendo a única fonte de verdade da
página, e a origem chega validada como `https://<host>` puro, pela mesma regra que o portal aplica
ao próprio `public_origin`.

### O que o painel mede, e o que ele não mede

| Mostra | Vem de |
|---|---|
| quem está logado e seus **papéis** | `GET /api/v1/portal/session` |
| se a tarefa do turno **aparece na fila**, e em qual (`team`/`mine`) | `GET /api/v1/portal/tasks?queue=…` |
| **frescor** da projeção (atraso entre fonte e portal) | `freshness` da própria fila |
| **grupo** que a fila indica, **prazo** como o portal calcula, revisão, posse | `GET /api/v1/portal/tasks/{id}` |
| se algum **identificador vazou** no contexto (CPF, telefone, e-mail), destacado no valor | varredura de superfície do JSON devolvido |

Quatro limites, ditos na tela em vez de escondidos:

1. **A fila do portal não carrega `processInstanceId`.** A junção com o turno é pelo `task_id` que o
   motor devolveu. Se o portal indexar a tarefa por outro identificador, a leitura volta 404 e a
   página oferece as duas leituras possíveis (não é deste colaborador / a chave é outra) sem
   escolher uma.
2. **A sessão não traz grupo.** `SessionDTO` projeta `principal_ref`, público, papéis e expiração —
   grupo aparece só na tarefa, em `eligible_candidate_groups`.
3. **O prazo do portal não é o prazo que o processo cobra.** A User Task do SP-OP-ESCALATION-001 não
   define `camunda:dueDate`, então `engine_due_at` vem nulo; os 5 e os 30 minutos vivem em boundary
   timer, que a fila do portal não lê. A página mostra os dois lados e marca a divergência.
4. **Origem recusada e portal fora do ar são indistinguíveis no navegador.** O `fetch` rejeita sem
   status nos dois casos, e a tela diz exatamente isso.

### Os dois caminhos de conclusão

O bloco "Você é a pessoa da equipe" tem os três botões de sempre em **dois grupos**: *Concluir pelo
motor* (`POST /engine/task/{id}/complete`, a referência) e *Concluir pelo portal*
(`POST {portal}/api/v1/portal/tasks/{id}/completion`, o endpoint da Frente 2, atrás da flag
`MAEZO_PORTAL_DIRECT_COMPLETION`). Cada conclusão vira um cartão no **mesmo formato** — resultado
enviado, resposta HTTP, fim alcançado, encerramento e rastro a partir da tarefa humana — e a página
marca **DIVERGÊNCIA** quando os dois caminhos, com o mesmo resultado, produzem rastro, fim ou
encerramento diferentes. Duas coisas que a página separa de divergência, de propósito:

- **`emergencia_acionada` termina em `End_ResolvidoPorHumano`**, não em `End_SupervisorAlertado` — o
  gateway `GW_Resultado` só desvia `devolvido_agente`. `End_SupervisorAlertado` é o fim do ramo do
  timer de ciência, com o caso seguindo aberto.
- **"Aguardando worker" não é divergência.** Depois da User Task vem `ST_PublishResolved`, external
  task no tópico `operadora.events.publish`; sem worker o processo fica vivo sem chegar a fim algum.

O seletor de colaborador **orienta, não forja**: a identidade vive no cookie do portal, então os
botões "Entrar como…" abrem o login do portal (`/api/v1/portal/auth/login`) e, na volta, *Reler o
portal* relê a sessão. A sessão atual fica visível no topo do painel.

## Roteiro de teste conjunto (motor × portal)

1. Rodar `BAT.rodar()` (o `bateria-helena.js`, que **não** mudou e não sabe do portal). A bateria
   abre casos: P1 para `plantao-clinico`, P3 para `atendimento-humano`.
2. Entrar no portal como colaborador do **plantão clínico** e conferir no painel: os casos P1
   aparecem na fila, com prazo correndo (ciência em 5 minutos) e contexto pseudonimizado.
3. Entrar como colaborador do **atendimento humano**: os casos clínicos **não** aparecem (o painel
   diz "a tarefa não aparece para este colaborador", com o 404 do portal); os P3 aparecem.
4. Concluir **um caso pelo portal** e **um caso pelo motor**. Comparar no bloco de desfechos:
   desfecho, rastro de atividades e encerramento devem ser idênticos.
5. Voltar ao portal (*Reler o portal*) e confirmar que os dois casos saíram da fila.
6. Conferir no painel que os desfechos foram contabilizados.

**Se a fila do grupo vier vazia com casos abertos:** olhar a projeção de autoridade. O gateway
compara duas fontes independentes e *"o adaptador deve recusar fontes indisponíveis ou atrasadas"* —
projeção defasada devolve fila vazia **sem erro visível**. O bloco de frescor do painel mostra o
atraso entre a fonte e o portal, que é onde isso aparece.

## `/receptor/simular` — use um telefone NOVO em cada teste

A rota assina um envelope de WhatsApp no servidor e o entrega ao receptor, o que faz a
triagem da Helena rodar de verdade. Duas coisas a saber antes de usar:

**O telefone tem de estar na faixa `5511900000xxx`** — três dígitos no fim, nada além (999 números desde 22/09/2026; eram 99). É a
cerca que impede este canal de fabricar mensagem em nome de um número real, e ela é
verificada nas duas pontas (`^5511900000\d{3}$`), então a faixa como prefixo de um número
mais longo é recusada.

**Um telefone repetido NÃO abre um escalonamento novo.** A chave de negócio do processo é
derivada da conversa, que é derivada do telefone. Se aquele número já tem um escalonamento
ABERTO, o turno novo se prende ao que já existe em vez de abrir um segundo — que é o
comportamento correto (um beneficiário não gera duas filas), mas engana num teste: você manda
"dor no peito", recebe `200`, e a tela mostra um escalonamento antigo com a prioridade
daquele outro caso. Medido em 11/09/2026.

Então: **incremente os dois últimos dígitos a cada rodada**. Com um número virgem, a mensagem
*"estou com uma dor muito forte no peito e falta de ar"* produz, em poucos segundos,
`red_flag_clinico` / severidade grave / **P1** / fila `plantao-clinico`, prazos `PT5M` e
`PT30M`, a tarefa *"Assumir e tratar escalonamento"* esperando nessa fila e **dois relógios
armados** (5 e 30 minutos). Foi exatamente isso que `5511900000077` produziu na validação.

A rota só existe onde `CANAL_SIMULAR_RECEPTOR=1`, e `scripts/ci/check_canal_simular.py`
reprova quem a ligar fora de `dev-sa-east-1`.

## Como publicar uma página

Ponha o arquivo aqui, commite, e o próximo build da imagem o leva. Não há passo de
configuração: a rota lista o diretório em tempo de requisição.
