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

## `/receptor/simular` — use um telefone NOVO em cada teste

A rota assina um envelope de WhatsApp no servidor e o entrega ao receptor, o que faz a
triagem da Helena rodar de verdade. Duas coisas a saber antes de usar:

**O telefone tem de estar na faixa `55119000000xx`** — dois dígitos no fim, nada além. É a
cerca que impede este canal de fabricar mensagem em nome de um número real, e ela é
verificada nas duas pontas (`^55119000000\d{2}$`), então a faixa como prefixo de um número
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
