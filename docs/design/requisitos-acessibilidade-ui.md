# Requisitos de acessibilidade — superfícies humanas do Maezo (GAP 10.1)

**Status:** DRAFT — requer revisão humana (acessibilidade/jurídico) antes de virar critério de
aceite vinculante ou ADR · **Autor:** a11y-requirements-author (R2, gap-closure) · **Data:**
2026-09-03
**Gatilho:** `GAP-REGISTER.yaml` id `10.1` — "Nenhum requisito de acessibilidade registrado em
spec/ADR/checklist/CI para a UI que será construída; nenhum dos 39 ADRs trata de UI/a11y."
Reprodução re-checada nesta sessão: `grep -rinE '\ba11y\b|acessibilidade|wcag|screen.?reader|libras' docs/adr/`
→ **0 hits** (41 arquivos sob `docs/adr/`, dos quais 39 são ADRs numerados 0001–0039).

**Este documento NÃO é um ADR.** `docs/adr/` é gated ao dono do produto/CODEOWNERS (fora do
escopo desta sessão) — promover este requisito a ADR formal é o follow-up explícito da §7.
Este documento também **não decide** nenhuma das perguntas de produto listadas na §5 — essas são
propriedade de `WP-SUPERFICIE-HUMANA-DECISAO` (gaps 9.1/9.6/10.2/11.1) e ficam citadas como
abertas, nunca respondidas aqui.

---

## 1. Escopo — o que existe hoje e o que estes requisitos cobrem

### 1.1 Inventário verificado nesta sessão

| Superfície | Existe hoje? | Evidência |
|---|---|---|
| Canal beneficiário (WhatsApp/Helena) | SIM — texto puro, sem UI renderizada | `src/maezo/agents/helena/graph.py` (docstring do módulo: "Free-text WhatsApp message content"); `src/maezo/platform/webhooks/whatsapp/dispatch.py:85` (`msg.get("type") != "text"` → mensagens de áudio/imagem/localização são **descartadas**, nunca processadas) |
| Superfície de decisão humana (operador/auditor abre um dossiê de Rafael/Marina/Carolina) | NÃO CONSTRUÍDA | achado 9.1 (D9) / 11.3 (D11) — decisão de produto de `WP-SUPERFICIE-HUMANA-DECISAO`, citada aqui como alvo destes requisitos quando for construída, não decidida |
| `src/maezo/platform/testchannel/paginas/{exemplo,autorizacao}.html` | SIM — 2 páginas HTML | ferramenta de QA/dev interna, atrás de Cloudflare Access em nuvem (`testchannel/server.py`'s próprio docstring); **isenta** do escopo vinculante desta seção 2 — ver §1.3 |
| Tasklist do CIB Seven (interface de terceiro que a User Task humana pode usar hoje) | Ferramenta de terceiro, fora do controle deste repositório | não auditável/alterável por este time — citada apenas para registrar que **não é** a superfície-alvo destes requisitos |

A metade "0 menções a a11y em ADR" do achado 10.1 permanece exata (grep acima). A metade "0
artefatos de UI" está **enfraquecida** pelas 2 páginas de `testchannel/` (achado 9.1) — este
documento nomeia essa exceção explicitamente em vez de repetir a alegação factualmente
imprecisa.

### 1.2 Superfícies-alvo destes requisitos

Estes requisitos se aplicam, no presente ou como baseline para quando forem construídas, a:

1. **A conversa WhatsApp com Helena** (e, quando existirem, Lucas/Fernando — ver
   `docs/audits/maezo-deep-audit/` gap 11.7, `WP-SUPERFICIE-HUMANA-EXEC`) — um canal
   conversacional, não uma UI renderizada; a aplicabilidade do WCAG aqui é qualificada pela §4.
2. **A futura superfície de decisão humana** (operador/auditor/médico-auditor que abre um
   dossiê) — quando `WP-SUPERFICIE-HUMANA-DECISAO` (9.1/11.1) decidir construí-la, ela nasce
   sob a baseline WCAG 2.2 AA da §2, não como um requisito a acrescentar depois.
3. **Qualquer futuro painel do beneficiário** (app, portal web, PWA) — mesma baseline.

### 1.3 Isenção explícita — `testchannel/`

`src/maezo/platform/testchannel/` é uma ferramenta de teste/QA de uso interno, com audiência
técnica cativa (desenvolvedores e testadores, nunca o beneficiário ou o operador de produção —
`server.py`'s próprio docstring: "ESTE CANAL NÃO TEM AUTENTICAÇÃO PRÓPRIA... só é aceitável atrás
de uma fronteira de identidade"). Ela está **isenta** do caráter vinculante das seções 2 e 6 —
recomenda-se apenas, como boa prática (`SHOULD`, não `MUST`), rótulos de formulário associados e
contraste mínimo, sem gate de CI.

---

## 2. Baseline normativo: WCAG 2.2 nível AA

**Nível escolhido: AA** (não A, não AAA). Justificativa: A é insuficiente para um domínio de
saúde regulado (RN 424/2017 e a Lei 9.656/1998 pressupõem que o beneficiário CONSEGUE entender e
agir sobre uma comunicação da operadora); AAA inclui critérios que a própria especificação marca
como nem sempre aplicáveis a todo conteúdo (ex.: 3.1.5 Reading Level) — tratados aqui como
**aspiracionais**, citados na tabela mas não como critério de aceite obrigatório.

Fonte: W3C, *Web Content Accessibility Guidelines (WCAG) 2.2*, W3C Recommendation, 5 de outubro
de 2023 — <https://www.w3.org/TR/WCAG22/> (conteúdo confirmado por busca ao vivo nesta sessão;
os números/títulos/níveis abaixo foram lidos diretamente dessa fonte, não de memória).

### 2.1 Perceptível (Perceivable)

| SC | Título | Nível | Por que se aplica aqui |
|---|---|---|---|
| 1.1.1 | Non-text Content | A | Qualquer ícone/emoji/imagem usado numa futura UI (status de autorização, selo de urgência) precisa de alternativa textual — o dossiê de Rafael/Marina/Carolina não pode comunicar `GLOSAR`/`DEFERIR`/`INDEFERIR` só por cor/ícone |
| 1.3.1 | Info and Relationships | A | Tabelas de dossiê (ex.: os campos de `_DOSSIER_SUMMARY_KEYS` que os workers já montam) precisam de estrutura semântica (`<table>`/`<label>`), não só alinhamento visual |
| 1.3.5 | Identify Input Purpose | AA | Formulários da User Task humana (`UT_AnaliseMedicoAuditor`, `UT_TratarEscalonamento`) devem declarar o propósito de cada campo, habilitando preenchimento assistido |
| 1.4.1 | Use of Color | A | O catálogo de prioridades (`P1`/`P2`/`P3`, `grave`/`moderada`/`leve` — `spec/agents/helena/agent.yaml`, contrato SP-OP-ESCALATION-001) nunca pode depender só de cor para distinguir severidade |
| 1.4.3 | Contrast (Minimum) | AA | Contraste mínimo 4.5:1 (texto normal) / 3:1 (texto grande) em qualquer UI renderizada |
| 1.4.4 | Resize Text | AA | Texto deve reescalar até 200% sem perda de conteúdo/funcionalidade — relevante para o público idoso/baixa-visão de um plano de saúde |
| 1.4.10 | Reflow | AA | Sem rolagem horizontal em 320px CSS de largura — a maioria dos beneficiários acessa via celular |
| 1.4.11 | Non-text Contrast | AA | Contraste ≥3:1 em componentes de UI e indicadores gráficos de estado (ex.: badge de SLA) |
| 4.1.3 (cruza com Robust, listado aqui por afinidade de conteúdo dinâmico) | Status Messages | AA | Mudanças de estado assíncronas (SLA estourado, escalonamento iniciado) devem ser anunciadas a tecnologia assistiva sem exigir foco manual |

### 2.2 Operável (Operable)

| SC | Título | Nível | Por que se aplica aqui |
|---|---|---|---|
| 2.1.1 | Keyboard | A | Toda a superfície de decisão humana deve ser 100% operável por teclado — um auditor médico sob pressão de SLA não pode depender do mouse |
| 2.1.2 | No Keyboard Trap | A | Nenhum modal/dossiê pode prender o foco de teclado |
| 2.2.1 | Timing Adjustable | A | Se a UI expuser um cronômetro de SLA (ack/resolução, contrato SP-OP-ESCALATION-001/SP-OP-AUTH-001), o usuário deve poder estendê-lo/desativá-lo — nunca uma ação adversa automática por timeout de UI (coerente com o invariante HITL no-denial já vigente no motor) |
| 2.4.3 | Focus Order | A | Ordem de foco lógica em formulários de decisão adversa (ex.: os campos obrigatórios de `UT_AnaliseMedicoAuditor` ao negar) |
| 2.4.6 | Headings and Labels | AA | Cabeçalhos/labels descritivos, não genéricos ("Dossiê", "Decisão") |
| 2.4.7 | Focus Visible | AA | Indicador de foco visível em todo elemento interativo |
| 2.4.11 | Focus Not Obscured (Minimum) | AA (novo na 2.2) | O foco atual nunca pode ficar totalmente escondido atrás de um cabeçalho/painel fixo |
| 2.5.3 | Label in Name | A | O nome acessível de um botão deve conter o texto visível (relevante se a UI usar botões de decisão como "Negar"/"Deferir") |
| 2.5.8 | Target Size (Minimum) | AA (novo na 2.2) | Alvos de toque ≥24×24px CSS — relevante tanto para uma futura UI web quanto para qualquer botão de resposta rápida do WhatsApp Business API (ver §4) |

### 2.3 Compreensível (Understandable)

| SC | Título | Nível | Por que se aplica aqui |
|---|---|---|---|
| 3.1.1 | Language of Page | A | `lang="pt-BR"` declarado — toda a plataforma é pt-BR |
| 3.1.5 | Reading Level | AAA (aspiracional, não gate) | Citado porque o público de um plano de saúde inclui baixa-literacia; ver §2.4 sobre o cruzamento com o gap 10.3 (clareza dos goldens da Helena) |
| 3.2.1 | On Focus | A | Focar um campo não pode disparar submissão/navegação inesperada |
| 3.3.1 | Error Identification | A | Erro de formulário (ex.: campo obrigatório de fundamentação clínica ausente, `ERR_*_NOT_HUMAN`) deve ser identificado em texto, não só cor |
| 3.3.2 | Labels or Instructions | A | Todo campo de entrada tem rótulo/instrução |
| 3.3.3 | Error Suggestion | AA | Quando possível, sugerir a correção do erro |
| 3.3.7 | Redundant Entry | A (novo na 2.2) | Um operador que já preencheu um dado antes na mesma sessão/fluxo não deve ser obrigado a redigitá-lo |
| 3.3.8 | Accessible Authentication (Minimum) | AA (novo na 2.2) | Qualquer login futuro da superfície de decisão humana não pode depender exclusivamente de teste cognitivo (ex.: só CAPTCHA de memorização) |

### 2.4 Robusto (Robust)

| SC | Título | Nível | Por que se aplica aqui |
|---|---|---|---|
| 4.1.2 | Name, Role, Value | A | Componentes customizados (badges de prioridade, indicadores de SLA) precisam expor role/estado a tecnologia assistiva, não só estilo visual |
| 4.1.3 | Status Messages | AA | Repetido aqui por ser Robust na especificação (ver nota na tabela §2.1) — cobre também notificações assíncronas de escalonamento na UI |

> **Nota de honestidade metodológica:** a tabela acima é um SUBCONJUNTO selecionado dos ~50
> critérios A/AA do WCAG 2.2, escolhido por relevância aos artefatos que este repositório já
> declara (dossiês, formulários de User Task, canal conversacional). Ela não substitui uma
> auditoria de conformidade completa contra os ~50 critérios A/AA — essa auditoria só é possível
> depois que a UI existir (§6 define os critérios de aceite/como testar).

---

## 3. Âncoras regulatórias brasileiras — **todas DRAFT/verify**

Nenhuma das três âncoras abaixo foi confirmada contra o texto vigente ou contra parecer
jurídico — `docs/compliance/` não contém nenhum documento sobre acessibilidade (verificado
nesta sessão: `grep -rli "acessibilidade\|wcag\|13.146\|eMAG\|17060" docs/compliance/` retorna
apenas `lgpd-topic-reconciliation.md`, que não trata do tema). Mantidas como **DRAFT/verify**
por instrução expressa do escopo desta sessão — não removível sem revisão jurídica.

- **Lei 13.146/2015 (Lei Brasileira de Inclusão da Pessoa com Deficiência — LBI)** — **DRAFT/verify**.
  Estabelece o direito à acessibilidade digital de forma geral; a aplicabilidade direta e o
  alcance de suas disposições sobre TIC/comunicação a uma operadora de plano de saúde PRIVADA
  (em oposição a órgão público) e os artigos exatos aplicáveis precisam de confirmação
  jurídico-regulatória antes de qualquer citação vinculante.
- **eMAG (Modelo de Acessibilidade em Governo Eletrônico)** — **DRAFT/verify**. É um modelo
  historicamente alinhado ao WCAG, mas dirigido a serviços de governo eletrônico — se e como ele
  se estende a uma operadora privada de saúde é uma questão aberta, não respondida aqui.
- **ABNT NBR 17060** — **DRAFT/verify**. Citada por instrução do escopo desta sessão como âncora
  a verificar; este documento não confirma o número, o título exato nem o escopo desta norma —
  cabe ao revisor jurídico/de acessibilidade confirmar contra o texto ABNT vigente antes de
  qualquer promoção a critério vinculante.

Nenhuma das três âncoras acima é usada neste documento para definir um critério de aceite (§6) —
os critérios de aceite usam exclusivamente o WCAG 2.2 AA (§2), que foi verificado ao vivo contra
a fonte W3C nesta sessão. As três âncoras ficam registradas como **contexto regulatório a
confirmar**, e uma linha correspondente foi adicionada a `docs/review-queue.md`.

---

## 4. Restrições do canal WhatsApp-first / Helena texto-somente

O WCAG 2.2 foi escrito para páginas/aplicações web — a conversa com Helena não é uma página, é
uma troca de mensagens de texto dentro do cliente WhatsApp (Meta), cuja própria acessibilidade
(leitor de tela, zoom, contraste do app) está **fora do controle deste repositório**. As
implicações práticas, hoje verificadas no código:

1. **Somente texto, hoje.** `dispatch.py:85` descarta silenciosamente (loga, não processa)
   qualquer mensagem que não seja `type == "text"` — áudio, imagem, localização. Um beneficiário
   que só consegue gravar uma nota de voz (baixa visão severa, baixa alfabetização, dificuldade
   motora para digitar) **não recebe nenhuma resposta ao seu áudio** — nem um pedido para
   reenviar em texto. Isto é uma lacuna de acessibilidade REAL, hoje, não hipotética — worth
   citar mesmo sem uma correção aqui, porque a correção (transcrição de áudio, ou uma resposta
   automática pedindo texto) é uma decisão de produto/custo (novo provedor de transcrição,
   política de retenção de áudio) fora do escopo `agent-executable` desta sessão.
2. **Sem alternativa a texto.** O gap 10.2 do registro de auditoria (`GAP-REGISTER.yaml` id
   `10.2`, `human-only`, `WP-SUPERFICIE-HUMANA-DECISAO`) é exatamente esta pergunta — Libras,
   TTS/áudio de saída, ou roteamento humano prioritário para quem não consegue usar texto. **Este
   documento não decide 10.2** — ele cita a lacuna (equivalente ao princípio "Perceivable" do
   WCAG aplicado a um canal conversacional) e aponta para a decisão pendente.
3. **Clareza textual como substituto parcial de "Perceivable/Understandable" num canal sem
   layout.** Como não há elementos visuais, os critérios mais relevantes deste canal são os de
   compreensão textual (3.3.1 Error Identification, 3.1.5 Reading Level-como-aspiração) —
   redação curta, sem jargão médico/administrativo, é o principal veículo de acessibilidade
   aqui. Isto se conecta ao gap 10.3 (`WP-EVALS`, "vocabulário de triagem sem eval de
   clareza/legibilidade nos goldens da Helena") — este documento NÃO implementa esse eval (é
   `WP-EVALS`), mas registra que a métrica de clareza textual, quando existir, é a forma real de
   medir "Understandable" neste canal, e recomenda que `WP-EVALS` cite este documento ao definir
   os critérios do eval de clareza.
4. **Alvos de toque em respostas rápidas** — se o WhatsApp Business API de botões/listas
   interativos vier a ser usado (hoje não é — `dispatch.py` só lê `msg.get("text")`), 2.5.8
   Target Size (Minimum) e 2.5.3 Label in Name (§2.2) se aplicam a esses componentes.

---

## 5. Decisões de produto citadas, não tomadas aqui

Por regra do work package (`WP-SUPERFICIE-HUMANA-DECISAO` é `human-only`), este documento cita e
não decide:

- **10.2** — caminho alternativo a texto para Helena (Libras/TTS/áudio/roteamento humano
  prioritário). Aberto.
- **9.6** — canal secundário além de WhatsApp/Meta (web, e-mail, telefone). Aberto; se
  decidido, a superfície resultante herda a baseline WCAG 2.2 AA da §2 por definição, sem
  precisar de um novo documento de requisitos.
- **9.1 / 11.1** — se/como a superfície de decisão humana (operador/auditor) é construída, e o
  mapa de personas que a sustenta. Aberto; a §1.2/§2 desta documentação já se aplicam a essa
  superfície no dia em que ela nascer.

---

## 6. Critérios de aceite e como testar

| # | Critério de aceite | Como testar |
|---|---|---|
| AC-1 | Toda página/tela nova da superfície de decisão humana passa scan automatizado (axe-core ou pa11y) com zero violações `critical`/`serious` contra a regra WCAG 2.2 AA | `pa11y-ci`/`@axe-core/cli` no pipeline de build da UI (a integrar quando a UI existir — não há UI para rodar hoje) |
| AC-2 | Navegação 100% por teclado (sem mouse) em qualquer fluxo de decisão adversa | Roteiro manual de QA: completar `UT_AnaliseMedicoAuditor`/`UT_TratarEscalonamento` só com Tab/Enter/Espaço/setas |
| AC-3 | Contraste mínimo 4.5:1 (texto)/3:1 (UI) em todo componente | Verificação automatizada (axe-core cobre; contraste também revisável manualmente com um verificador de contraste) |
| AC-4 | Smoke test com leitor de tela (NVDA ou VoiceOver) consegue completar o fluxo de decisão adversa ponta a ponta | Roteiro manual de QA com um leitor de tela real, não simulado |
| AC-5 | Nenhum critério de aceite depende só de cor para transmitir severidade/estado (`P1`/`P2`/`P3`, `grave`/`moderada`/`leve`) | Revisão de design + grep de uso de cor isolada nos componentes de status |
| AC-6 | Toda mensagem de status assíncrona (SLA estourado, escalonamento iniciado) é anunciada a tecnologia assistiva (região `aria-live` ou equivalente) | Smoke test com leitor de tela cobrindo uma atualização assíncrona |
| AC-7 | O texto das respostas de Helena (quando a métrica de clareza do gap 10.3 existir, `WP-EVALS`) atinge o limiar de legibilidade que aquele eval define | Delegado a `WP-EVALS` — este documento só define QUE a métrica deve existir e O QUE ela mede (clareza textual como veículo de acessibilidade num canal sem layout, §4.3) |
| AC-8 | Uma mensagem de áudio/imagem recebida por Helena nunca é silenciosamente descartada sem QUALQUER resposta ao beneficiário (hoje: descartada, `dispatch.py:85`) | Depende da decisão 10.2 (§5) — sem uma decisão de produto sobre o que fazer com mídia não-texto, não há um "correto" a testar; citado aqui como lacuna, não fechado |

AC-1 a AC-6 aplicam-se no dia em que a superfície de decisão humana existir — nenhum é
executável hoje porque não há UI para escanear (§1.1). AC-7/AC-8 estão explicitamente marcados
como dependentes de outro work package/decisão, não fechados por este documento.

---

## 7. Follow-ups e o que fica gated

1. **Promoção a ADR** — `docs/adr/` é `owner-gated` (fora do alcance desta sessão: CODEOWNERS +
   revisão do dono do produto). Este documento é o INSUMO para um futuro
   `ADR-00XX-requisitos-acessibilidade-ui.md`; a promoção em si é um ato do dono, não desta
   sessão.
2. **Confirmação jurídica das 3 âncoras da §3** — jurídico/regulatório + especialista em
   acessibilidade devem confirmar aplicabilidade/texto vigente antes de qualquer citação virar
   vinculante. Linha adicionada a `docs/review-queue.md`.
3. **Decisões de produto da §5** — `WP-SUPERFICIE-HUMANA-DECISAO` (9.1/9.6/10.2/11.1),
   `human-only`.
4. **Eval de clareza/legibilidade (gap 10.3)** — `WP-EVALS`; este documento define o requisito
   qualitativo (§4.3/AC-7), não a implementação.
5. **Ferramentas de CI de a11y (axe-core/pa11y)** — a integrar ao pipeline quando a primeira UI
   real existir; não há `Makefile`/CI a alterar hoje porque não há artefato de UI para escanear
   (edições a `Makefile`/`.github/` são, de toda forma, fora do escopo desta sessão).

---

## 8. Referências

- W3C, *Web Content Accessibility Guidelines (WCAG) 2.2*, Recommendation 2023-10-05 —
  <https://www.w3.org/TR/WCAG22/> (verificado ao vivo nesta sessão para os números/títulos/níveis
  citados nas tabelas da §2).
- `GAP-REGISTER.yaml`/`GAP-REGISTER.md` (docs worktree, read-only) — gaps `10.1`, `10.2`, `9.6`,
  `9.1`, `10.3`, `11.1`.
- `docs/audits/maezo-deep-audit/reports/domain-09-ux-ui.md` (achado 9.1),
  `docs/audits/maezo-deep-audit/reports/domain-10-accessibility.md` (achado 10.1/10.2/10.3) —
  checkout principal, read-only, `docs/audits/` é gitignored (não versionado nesta worktree).
- `src/maezo/agents/helena/graph.py`, `src/maezo/platform/webhooks/whatsapp/dispatch.py`,
  `src/maezo/platform/testchannel/server.py` — evidência de código citada nas §1/§4.
