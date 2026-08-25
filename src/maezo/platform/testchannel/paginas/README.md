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

`exemplo.html` exercita as duas e serve de referência mínima.

## Como publicar uma página

Ponha o arquivo aqui, commite, e o próximo build da imagem o leva. Não há passo de
configuração: a rota lista o diretório em tempo de requisição.
