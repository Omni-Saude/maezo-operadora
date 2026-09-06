# Canal de Teste (`testchannel`) — demo sem autenticação própria

**Decisão do dono (OWNER-DECISIONS-REGISTER `R-031`, Bold-Decision Review v2 CEO 2026-09-04,
status `APROVADO-APOS-REVISÃO-HUMANA`), citada verbatim:** *"Manter o SIM (Cockpit +
`testchannel` declarado demo sem autenticação própria) e DELETAR a espera em vez de datá-la
[...] `antes do 1º operador real` deixa de ser âncora e vira mera nota de revisão."*

Este diretório serve `https://maezo-teste-dev.austa.com.br/` — um formulário de lançamento +
visor de evidência de uma página só (`server.py`), e as páginas em `paginas/` (`exemplo.html`,
`autorizacao.html`). **Nenhum destes é a superfície ratificada do operador.** A superfície do
operador para as jornadas com User Task é o **Cockpit do CIB Seven**.

## Por que este canal NÃO é uma superfície de operador

- **Nenhuma autenticação própria.** O canal em si não implementa login, sessão ou identidade de
  usuário — a única barreira é a **Cloudflare Access** na frente (e-mail corporativo), que
  autentica a PESSOA, não o PAPEL (auditor, analista de glosa, médico-auditor). Uma vez atrás do
  Access, qualquer um alcança o proxy de `/engine/*` e o ingresso do Rafael em
  `/agente/v1/autorizacoes`.
- **Uso pretendido: demonstração e teste manual**, não operação de produção. `autorizacao.html`
  (535 linhas) monta uma página de dossiê/decisão real para `SP-OP-AUTH-001` com dados reais do
  data lake — materialmente útil para VER o que uma User Task carrega — mas sem o controle de
  acesso por papel, trilha de auditoria de decisão vinculada a um usuário nomeado, ou qualquer dos
  outros requisitos que uma superfície de operador ratificada exigiria.
- **Ver também:** `paginas/README.md` (por que as páginas são servidas aqui, mesma-origem, em vez
  de um token de serviço da Cloudflare); `server.py` (docstring do módulo — o aviso "nunca
  implantar" original foi substituído pela razão honesta atual: "ESTE CANAL NÃO TEM AUTENTICAÇÃO
  PRÓPRIA").

## O que isto desbloqueia

Fecha o gap `9.1` ("ausência de superfície humana") pela metade que cabe a este repositório:
`docs/review-queue.md` registra a decisão A+D do dono, e este arquivo é a nota exigida pela
`acao_seguinte` de R-031. Desbloqueia o restante de `WP-SUPERFICIE-HUMANA` (`11.1`, `9.2`, `9.6`,
`10.1`, `10.2`, `11.7`), condicionado ao mapa de personas de R-032 (`11.1`, memo M-20).
