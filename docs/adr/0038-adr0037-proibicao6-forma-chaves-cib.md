# ADR-0038: Emenda a proibicao 6 do ADR-0037 — reconciliacao da FORMA das chaves CIB com a realidade implantada (business keys hifen-primeiro + process keys `SP-OP-*`) e correcao da auto-inconsistencia XRD-08 vs proibicao 6

**Status:** Proposed — **NAO RATIFICADO; nenhuma clausula deste ADR tem efeito.** A escolha entre a Opcao 1 e a Opcao 2 e ato do DONO DO REPOSITORIO (ver secao "Ratificacao"). · **Data:** 2026-08-09 · **Area:** Integracao / Fronteira de plataforma / Governanca de ADR

> **Enquadramento (leia antes do resto).** Este ADR e produzido para **ACELERAR** a decisao humana ja
> registrada como PENDENTE em **DL-0043** (`docs/decisions-log.md:10`) e em **PLANS.md §0.6**
> (`PLANS.md:190`) — **nao para substitui-la**. Ele nao decide: estrutura o espaco de decisao em
> EXATAMENTE DUAS opcoes mutuamente exclusivas, com uma recomendacao explicitamente **nao
> vinculante**, de modo que o ato do dono seja escolher uma palavra ("Opcao 1" ou "Opcao 2").
> Precedente direto: **ADR-0033/DL-0035** (ADR proposto para acelerar a decisao humana, com opcoes +
> recomendacao nao vinculante) e **ADR-0032/DL-0032** (um ADR corrige/emenda outro ADR aceito por ADR
> NOVO, nunca por edicao unilateral do arquivo do ADR aceito — `docs/adr/README.md:8`).
>
> **Escopo estritamente docs-only.** Este ADR **NAO** edita `src/`, `spec/`, `tests/`, `deploy/`,
> nenhum worker, nenhuma BPMN e nenhuma allowlist. Em particular, **nao toca**
> `src/maezo/tools/process_allowlist.py` nem nenhum compositor de business key. Alterar um literal de
> wire "por consistencia" enquanto o ADR que o governa ainda esta em disputa e exatamente o
> anti-padrao que DL-0043 registrou e proibiu ("um agente nao reescreve chaves de processo
> implantadas nem emenda um ADR aceito por conta propria").
>
> **Este ADR resolve APENAS a perna de FORMA de DL-0043 (pernas (a) e (b)).** A perna **(c)** —
> business keys que embutem identificadores crus de registro de fonte (`numero_guia_tiss`,
> `numero_contrato`, `prestador_id`) e, pior, `matricula_beneficiario`, que e item do proprio registro
> de PHI deste repo — e uma tensao com a **proibicao 5** (`0037:210-211`), e **NAO e resolvida nem
> mitigada por nenhuma das duas opcoes abaixo**. Ela permanece ABERTA e rastreada em DL-0043,
> exigindo avaliacao de privacidade propria (DPO/Legal/Security). **Escolher a Opcao 1 nao a fecha,
> nao a atenua e nao pode ser lido como aprovacao dela.**

## Contexto

### 1. O que a proibicao 6 do ADR-0037 congela

O ADR-0037 (Accepted, ratificado pelo dono em 2026-08-03, DL-0040 —
`docs/adr/0037-amh-compatibility-boundary-canonical-contracts.md:3`) fixa seis proibicoes imutaveis
da fronteira. A sexta diz, literalmente (`0037:212-213`):

> 6\. Business keys CIB opacas na forma `{company_tenant_ref}:{workflow_type}:{workflow_business_ref}`;
> process-definition keys começam com `maezo-payer-`.

Ou seja, a proibicao 6 carrega **duas exigencias de FORMA**: (i) business key composta por tres
componentes **unidos por dois-pontos**, com o tenant a frente; (ii) process-definition key com
**prefixo `maezo-payer-`**.

### 2. A FORMA implantada e outra (perna (a) de DL-0043) — business keys

Todos os compositores vivos produzem chaves **unidas por hifen e com o PREFIXO DE DOMINIO a frente**,
`{PREFIXO}-{tenant}-{ids...}` — nao `{tenant}:{tipo}:{ref}`. Verificado por leitura direta:

**Workers (`src/maezo/tools/workers/`):**

| Sitio | Literal |
|---|---|
| `ans_submit.py:740` | `f"ANSSUB-{tenant_id}-{report_type}-{competencia}"` |
| `contas.py:468` | `f"RECURSO-{tenant_id}-{numero_guia_tiss}-{glosa_id}"` |
| `contas.py:610` | `f"FRAUDE-{tenant_id}-{numero_caso}"` |
| `recurso.py:854` | `business_key.strip() or f"RECURSO-{tenant_id}-{numero_guia_tiss}-{glosa_id}"` |
| `inadimplencia.py:61` | `f"CANCEL-{tenant_id}-{contrato}"` (com `contrato = numero_contrato or matricula_beneficiario`, `inadimplencia.py:60`) |
| `fraude.py:757` | `f"CRED-{tenant_id}-{prestador_id}"` |
| `fraude.py:824` | `f"CANCEL-{tenant_id}-{numero_contrato}"` |

**Notification bridge — os SETE compositores** (`src/maezo/platform/notification_bridge.py`):
`_ans_cron_business_key` (`:176`, retorno `:184`), `_ans_nip_business_key` (`:187`, retorno `:198` —
`ANSSUB-{tenant_id}-nipfiling-{numero_nip_ans}`), `_recurso_business_key` (`:360`, retorno `:364`),
`_fraude_business_key` (`:367`, retorno `:370`), `_cred_business_key` (`:395`, retorno `:401`),
`_cancel_business_key` (`:404`, retorno `:407`), `_inadimplencia_business_key` (`:410`, retorno `:415`
— `INAD-{tenant_id}-{numero_contrato}`, prefixo deliberadamente DISTINTO de `CANCEL-` para o mesmo
contrato).

**Grafos de agente e delegacoes (`src/maezo/agents/`):** `beatriz/graph.py:202` (`FRAUDE-`),
`gustavo/graph.py:310-311` (`ANSSUB-`/`NIP-`), `andre/graph.py:469,477-478` (`ADEQ-`/`PAGTO-`),
`carolina/graph.py:296-297` (`CRED-`), `rafael/graph.py:169` (`AUTH-{tenant_id}-{numero_guia_tiss}`),
`marina/graph.py:325-332` (`RECURSO-`/`REEMB-`/`CONTAS-`), `fernando/graph.py:297` (mesmo fallback
`numero_contrato or matricula_beneficiario` de `inadimplencia.py`), `carolina/delegation.py:111-112`,
`andre/delegation.py:153-154,262,268`.

**A convencao esta DOCUMENTADA como contrato**, nao e acidente de codificacao:
`src/maezo/tools/mcp_cibseven/transport.py:14` descreve o start idempotente como "business-key dedup,
**ADR contract convention `{PREFIX}-{tenant}-{id}`**". A `<bpmn:documentation>` dos proprios contratos
de processo e citada como autoridade do esquema em `inadimplencia.py:52-58` (contrato
`SP-OP-INADIMPLENCIA-001.md`, "Business key (idempotencia) — coordenada com CANCEL-001", citado em
`notification_bridge.py:411-414`).

> **Correcao de precisao a DL-0043 (verificacao propria desta sessao, registrada por honestidade).**
> DL-0043 lista `auth.py` entre os compositores de business key. O literal em
> `src/maezo/tools/workers/auth.py:1261` — `f"AUTH-{tenant_id}-{guia}-{uuid.uuid4().hex[:8]}"` — **nao
> e uma business key**: e um `numero_autorizacao` TISS emitido (`auth.py:1267`, campo
> `numero_autorizacao` do log `auth_issued`). O compositor de business key da familia AUTH e
> `src/maezo/agents/rafael/graph.py:169`. A **forma** hifen-primeiro esta correta em ambos os casos e a
> conclusao de DL-0043 nao muda; apenas a atribuicao de sitio estava imprecisa.

### 3. A FORMA implantada e outra (perna (b) de DL-0043) — process-definition keys

- `src/maezo/tools/process_allowlist.py:60` congela o formato via regex:
  `^SP-OP-[A-Z]+(?:-[A-Z]+)*-[0-9]{3}$`.
- `process_allowlist.py:21-42` congela **15 chaves** `SP-OP-*` como `KNOWN_PROCESS_KEYS`
  (`:24-41`: `SP-OP-ESCALATION-001`, `SP-OP-LGPD-DSR-001`, `SP-OP-AUTH-001`, `SP-OP-CONTAS-001`,
  `SP-OP-RECURSO-001`, `SP-OP-NIP-001`, `SP-OP-ANS-SUBMIT-001`, `SP-OP-CANCEL-001`,
  `SP-OP-REEMBOLSO-001`, `SP-OP-INADIMPLENCIA-001`, `SP-OP-CRED-001`, `SP-OP-ADEQUACAO-001`,
  `SP-OP-FRAUDE-001`, `SP-OP-PROGRAMA-001`, `SP-OP-PAGTO-001`).
- O enforcement e **fail-closed e estrutural**: `process_allowlist.py:131-135` levanta
  `ProcessKeyNotAllowedError` (subclasse de `PermissionError`, `:63-76`) para qualquer chave que nao
  case a regex — ou seja, **uma chave `maezo-payer-*` seria hoje REJEITADA pelo proprio enforcement do
  ADR-0016**, antes de chegar ao engine.
- Os artefatos implantados concordam: `spec/processes/bpmn/*.bpmn` declara **20 `process id` distintos,
  todos `SP-OP-*`** (as 15 da allowlist + `SP-OP-ANS-CRON-001-DIOPS` / `-RN124SIP` / `-RN209` /
  `-RN388` / `-RN424TISS`). **Zero** com prefixo `maezo-payer-`.
- Varredura do repo: a string `maezo-payer` **nao ocorre em nenhum `.bpmn`, `.yaml`/`.yml` ou modulo
  `src/` de producao** — a UNICA ocorrencia de codigo e a constante
  `PAYER_PROCESS_DEFINITION_KEY_PREFIX: Final[str] = "maezo-payer-"` em
  `src/maezo/domain/integration/identity.py:145`, o modulo de valor do MZO-020, que implementa a forma
  do ADR (`identity.py:140` separador `":"`; `identity.py:466-496`
  `WorkflowBusinessRef.business_key` compondo
  `{company_tenant_ref}:{workflow_type}:{workflow_business_ref}`) e **nao tem nenhum chamador de
  producao** — so re-export em `src/maezo/domain/integration/__init__.py:10-22` e
  `src/maezo/domain/__init__.py:7`.

**Consequencia factual, sem exagero:** hoje **nenhuma** business key e **nenhuma** process-definition
key implantada satisfaz a FORMA da proibicao 6, e o MZO-020 entregou o objeto de valor que a satisfaz
sem que nenhum call site o consuma (migracao explicitamente DIFERIDA por DL-0043). As duas
especificacoes coexistem: uma governa o papel, a outra governa o engine.

### 4. O ADR-0037 e AUTO-INCONSISTENTE consigo mesmo quanto ao namespace

Dentro do MESMO documento, duas clausulas prescrevem formas **diferentes** para a mesma coisa:

- **XRD-08 (`0037:148`)**, sobre topologia de processo: "*engine, banco, credenciais, process keys e
  worker topics separados; **namespace de processo `maezo-payer/*`***".
- **Proibicao 6 (`0037:213`)**: "*process-definition keys começam com **`maezo-payer-`***".

`maezo-payer/*` (namespace com **barra**, sintaxe de glob) e `maezo-payer-` (prefixo com **hifen**)
nao sao a mesma regra: uma chave `maezo-payer-auth-001` satisfaz a proibicao 6 e **nao** casa
`maezo-payer/*`; uma chave `maezo-payer/auth-001` casa XRD-08 e **nao** comeca por `maezo-payer-`
seguido de conteudo no sentido de namespace. O ADR-0037, como escrito, e **impossivel de satisfazer
literalmente nas duas clausulas ao mesmo tempo**. Nenhuma implementacao pode ser julgada conforme
contra um alvo que se contradiz — e esta inconsistencia sobrevive a qualquer que seja a opcao
escolhida abaixo, portanto **as duas opcoes a corrigem**.

### 5. Por que isto e decisao do DONO e nao de um agente

- ADR-0037 e **Accepted por ratificacao humana explicita** (`0037:3`, DL-0040) e o proprio documento
  registra, no blockquote de `0037:5-12`, que "nenhum agente, orquestrador ou gatekeeper automatizado
  pode mover este Status".
- `docs/adr/README.md:8` fixa: "ADR aceito so muda por novo ADR com `Supersedes`". Emendar por ADR
  NOVO (o que este documento faz) e o caminho legitimo; editar `0037-*.md` no lugar nao e.
- DL-0043 (`docs/decisions-log.md:10`) fecha com a mesma regra: "um agente nao reescreve chaves de
  processo implantadas nem emenda um ADR aceito por conta propria", e classifica a reconciliacao como
  "PENDENTE do dono".

## Decisao

Este ADR apresenta **DUAS opcoes mutuamente exclusivas**. Nenhuma vigora ate a ratificacao do dono.

### Opcao 1 (RECOMENDADA) — emendar a proibicao 6: as formas implantadas passam a ser as formas canonicas Maezo (clausula de grandfathering), preservando integralmente os requisitos SEMANTICOS

A proibicao 6 do ADR-0037 passa a ler-se (o texto abaixo e a redacao proposta; so vigora se o dono
escolher a Opcao 1):

> **6. Business keys CIB e process-definition keys — FORMA canonica Maezo (emendada pelo ADR-0038).**
>
> **(6.1) Forma.** As business keys CIB do plano de acao Maezo tem a forma
> `{PREFIXO_DOMINIO}-{company_tenant_ref}-{workflow_business_ref...}`, unida por hifen, com o prefixo
> de dominio a frente — a convencao ja documentada em
> `src/maezo/tools/mcp_cibseven/transport.py:14` e realizada pelos compositores citados no §2 deste
> ADR-0038. As process-definition keys tem a forma `SP-OP-<DOMAIN>-<NNN>`, conforme a regex e o
> universo congelado de `src/maezo/tools/process_allowlist.py:60,21-42` (ADR-0016).
>
> **(6.2) Semantica — INALTERADA e integralmente exigivel.** Business keys permanecem **opacas**
> (nenhum significado de negocio derivavel por quem nao possui o mapeamento), **escopadas por tenant**
> (o `company_tenant_ref` e componente OBRIGATORIO — nenhuma key valida sem ele; isolamento
> cross-tenant continua estrutural), e **livres de PHI e de identificador cru de registro de fonte**
> (proibicao 5, `0037:210-211`, permanece **integralmente em vigor e NAO e emendada aqui**). O
> `workflow_type` da forma original nao desaparece: e o `{PREFIXO_DOMINIO}`, movido de posicao.
> Nenhuma garantia de opacidade, de tenancy ou de PHI e afrouxada por esta emenda — **so a ORDEM e o
> SEPARADOR dos componentes mudam**.
>
> **(6.3) Divida explicitamente NAO quitada por esta emenda.** As keys implantadas hoje **violam** a
> proibicao 5 em (6.2): `RECURSO-*` embute `numero_guia_tiss`/`glosa_id`, `CRED-*` embute
> `prestador_id`, `CANCEL-*`/`INAD-*` embutem `numero_contrato` e — no caminho de fallback de
> `src/maezo/tools/workers/inadimplencia.py:60-61` e `src/maezo/agents/fernando/graph.py:297` —
> `matricula_beneficiario`, que e item do registro de PHI deste repo
> (`src/maezo/tools/workers/phi_vars.py:52-59`, `PHI_PROCESS_VARS`, entrada em `:59`). Esta emenda
> **abencoa a FORMA e NAO abencoa esse conteudo**; a perna (c) de DL-0043 permanece ABERTA e exige
> avaliacao de privacidade propria (DPO/Legal/Security), fora do escopo deste ADR.
>
> **(6.4) Fronteira AMH.** A forma canonica AMH-owned de identidade portavel (`portable_subject_ref`,
> XRD-05, `0037:128-136`) e a forma das business keys internas do plano de acao Maezo sao eixos
> DISTINTOS e permanecem distintos. Nada nesta emenda autoriza um identificador Maezo a atravessar a
> fronteira para a AMH nem vice-versa; o contrato de wire (envelope congelado, `0037:193-201`) nao e
> tocado.

**E, na mesma tacada, a correcao da auto-inconsistencia do §4:** XRD-08 (`0037:148`) passa a ler
"namespace de processo do plano de acao payer: chaves `SP-OP-<DOMAIN>-<NNN>` (regex e universo em
`src/maezo/tools/process_allowlist.py`), isoladas da celula hospitalar" — eliminando as duas grafias
concorrentes (`maezo-payer/*` vs `maezo-payer-`) de uma vez. O requisito SUBSTANTIVO de XRD-08
(engine, banco, credenciais, process keys e worker topics **separados** entre celula hospitalar e
celula payer; nenhum compartilhamento de engine database ou de chaves de processo) permanece
**integral e inalterado** — o que muda e apenas a grafia do namespace, nunca a exigencia de
isolamento.

**Efeito no codigo se ratificada:** **NENHUM**. Nenhum worker, BPMN, allowlist ou chave de engine
muda. O objeto de valor do MZO-020 (`identity.py`) permanece valido para a fronteira AMH mas deixa de
ser o alvo de migracao das keys internas; o alinhamento do seu
`PAYER_PROCESS_DEFINITION_KEY_PREFIX`/separador com a forma emendada vira follow-up de codigo com
gatekeeper proprio, **nao autorizado por este ADR**.

### Opcao 2 — manter a forma congelada e agendar uma janela de migracao dual-read

O ADR-0037 fica como esta na FORMA (corrigida apenas a auto-inconsistencia do §4, que e defeito de
redacao sob qualquer opcao — nesta opcao, resolvida a favor da grafia da proibicao 6, `maezo-payer-`,
ficando XRD-08 alinhado a ela). Os call sites migram para
`{company_tenant_ref}:{workflow_type}:{workflow_business_ref}` e para process keys `maezo-payer-*`,
sob uma **janela de dual-read** com **alias legado explicito** e **sem rewrite destrutivo** — o
mecanismo que a clausula de rollback do MZO-020 preve, conforme registrado em DL-0043
(`docs/decisions-log.md:10`: "a propria clausula de rollback do MZO-020 preve alias legado explicito,
sem rewrite destrutivo"), e coerente com o principio ja adotado em XRD-05 para merges de identidade
("merges viram aliases AMH, **sem rewrite destrutivo de referencias de processo**", `0037:131`) e com
as janelas de dual-publish/dual-read de no minimo 30 dias que o proprio ADR-0037 aceita como
consequencia negativa (`0037:286-287`).

**Custo real desta opcao, declarado sem maquiagem:**

1. **Toca chaves de engine JA IMPLANTADAS.** Instancias vivas em CIB Seven carregam as keys atuais; a
   business key e o mecanismo de **dedupe idempotente do start**
   (`transport.py:14,206-230,238-259`) e de **correlacao de mensagem** (`transport.py:267-296`). Uma
   troca de forma sem dual-read parte a idempotencia e pode iniciar instancias duplicadas de processo
   regulatorio.
2. **Toca o enforcement fail-closed do ADR-0016.** `process_allowlist.py:60,131-135` REJEITA hoje
   qualquer chave fora de `SP-OP-<DOMAIN>-<NNN>`; migrar exige alterar a regex e o universo congelado
   de 15 chaves, alem de renomear os 20 `process id` em `spec/processes/bpmn/*.bpmn`, com redeploy
   coordenado — mudanca de superficie L0.
3. **Toca ~25 sitios de composicao** (7 workers, 7 no notification bridge, ~11 em grafos/delegacoes de
   agente, listados no §2) mais os contratos de processo em `docs/processes/` que documentam o esquema
   como autoridade.
4. **Nao resolve a perna (c) tambem.** Migrar a FORMA nao remove `numero_guia_tiss`,
   `numero_contrato`, `prestador_id` nem `matricula_beneficiario` do componente
   `workflow_business_ref` — a violacao da proibicao 5 sobrevive a mudanca de separador. A perna (c)
   precisa da sua propria decisao **independentemente da opcao escolhida aqui**.
5. **Requer decisao de janela e de aprovadores** (duracao, criterio de reconciliacao zero, ensaio de
   rollback, quem assina o redeploy) que este ADR nao pode tomar.

### Recomendacao (NAO VINCULANTE) — Opcao 1

Fundamentos verificados, nesta ordem:

1. **A divergencia e de FORMA, nao de seguranca.** As tres garantias que a proibicao 6 existe para
   produzir — opacidade, escopo de tenant, ausencia de PHI/ID cru — sao **ortogonais a ordem e ao
   separador dos componentes**. As formas vivas ja carregam o tenant como componente obrigatorio; a
   unica garantia genuinamente nao cumprida hoje (ausencia de ID cru/PHI) **nao e cumprida por
   nenhuma das duas formas** e e a perna (c), separada.
2. **A forma viva e mais antiga, mais amplamente realizada e ja documentada como contrato** — 20
   process ids em BPMN implantada, ~25 compositores, a convencao em `transport.py:14` e as
   `<bpmn:documentation>` dos contratos de processo. A forma do ADR tem **um** realizador
   (`identity.py`) e **zero chamadores de producao**.
3. **A Opcao 1 tem raio de explosao ZERO**; a Opcao 2 mexe em idempotencia de start e em correlacao de
   mensagem de processos regulatorios ANS/LGPD vivos, para um ganho de conformidade que a propria
   Opcao 2 nao entrega (item 4 acima).
4. **A proibicao 6 foi escrita na Wave 0** (MZO-000, 2026-08-03), antes de qualquer reconhecimento
   dos compositores implantados — DL-0043 registra que a divergencia so foi descoberta em 2026-08-05,
   quando o autor do MZO-020 foi implementar a clausula. E uma clausula escrita sem o fato, nao um
   requisito escolhido contra o fato.
5. **A auto-inconsistencia do §4 e evidencia de que a redacao de namespace do ADR-0037 nunca foi
   verificada contra a realidade** — duas clausulas do mesmo documento prescrevem grafias diferentes
   para uma coisa que, no repo, se chama `SP-OP-*`.

**Contra-argumento honesto (a favor da Opcao 2), para o dono pesar:** a proibicao 6 e uma **proibicao
imutavel de fronteira**, ratificada por ato humano ha seis dias; emenda-la seis dias depois porque a
implementacao diverge estabelece um precedente de "a realidade emenda o contrato" que e exatamente o
inverso do que este repo pratica (`docs/adr/README.md:7`: "toda decisao arquitetural relevante vira
ADR ANTES do codigo"). A defesa da Opcao 1 e que aqui a implementacao **precede** o ADR-0037 e nunca
foi contemplada por ele — mas quem entende que a forma congelada tem valor de compromisso cross-repo
com a AMH deve escolher a Opcao 2. Este ADR nao pode decidir isso.

## Ratificacao

`RATIFICATION: PENDING — <owner act required>`

**Ato humano UNICO que Aceita este ADR:** o **dono do repositorio** declara, em sessao, **"Opcao 1"**
ou **"Opcao 2"**. Esse ato, e somente ele, autoriza:

1. mover o **Status** deste ADR de `Proposed` para `Accepted (Opcao N)`;
2. registrar uma **linha DL** nova em `docs/decisions-log.md` nomeando o dono, a data e a opcao
   escolhida;
3. atualizar a linha 0038 em `docs/adr/README.md`.

**Restricoes de nao-fabricacao (vinculantes para qualquer agente que leia este arquivo):**

- Nenhum agente, orquestrador ou gatekeeper automatizado pode mover este Status — mesma regra que o
  proprio ADR-0037 impos a si (`0037:5-12`) e que DL-0043 reafirmou. A autoridade autonoma de
  ratificacao registrada em DL-0036 **nao se aplica** a este ADR.
- O marcador `RATIFICATION: PENDING` acima e **detectavel por maquina** e so pode ser substituido no
  mesmo commit que registra a linha DL da ratificacao humana. Nenhum nome, nenhuma data e nenhuma
  aprovacao estao pre-preenchidos neste documento, **por construcao**.
- Aceitar este ADR **nao** autoriza nenhuma mudanca de codigo. Sob a Opcao 1 nenhuma e necessaria;
  sob a Opcao 2, a migracao e um work package proprio, com plano de janela, aprovadores e gatekeeper
  independentes.
- Aceitar este ADR **nao** fecha, nao atenua e nao aprova a perna (c) de DL-0043 (proibicao 5 /
  PHI-e-ID-cru em keys). Essa continua sendo uma decisao humana separada, com aprovadores proprios.

## Relacao com ADRs existentes

**ADR-0037 — AMENDS (nao supersede).** Este ADR emenda a **proibicao 6** (`0037:212-213`) e a redacao
de namespace de **XRD-08** (`0037:148`), por ADR NOVO, conforme `docs/adr/README.md:8` e o precedente
ADR-0032 (corrige alegacao de ADR-0015 sem editar o arquivo do ADR-0015) e ADR-0027 (emenda a clausula
Kafka de ADR-0007 sem editar ADR-0007). **Nao edita**
`docs/adr/0037-amh-compatibility-boundary-canonical-contracts.md`. Todas as demais clausulas do
ADR-0037 — XRD-01..XRD-12 (exceto a redacao de namespace de XRD-08), catalogo canonico, envelope
congelado, proibicoes 1-5 — permanecem **integralmente vigentes e nao tocadas**. Em particular, a
**proibicao 5 nao e emendada**.

**ADR-0016 — nota (nao supersede, nao emenda).** O `process_allowlist.py` e o realizador do ADR-0016
(allowlist de `process_key` + invariante de pseudonimizacao). A Opcao 1 alinha o ADR-0037 ao que o
ADR-0016 ja enforca, **sem tocar** o ADR-0016 nem o arquivo. A Opcao 2, se escolhida, **exigiria**
alterar o realizador do ADR-0016 (regex `:60` + universo `:21-42`) — o que seria trabalho proprio, com
gatekeeper proprio, nunca consequencia automatica desta ratificacao.

**ADR-0006 / ADR-0035 / ADR-0036 — nota.** A perna (c) de DL-0043 (PHI em keys) vive no eixo destes
ADRs (duas zonas de PHI; pseudonimizador HMAC keyed; identidade conversa/thread/business-key keyed).
Este ADR **nao** avanca esse eixo e **nao** deve ser citado como se avancasse.

**Precedentes de processo seguidos:** ADR-0033/DL-0035 (ADR proposto para ACELERAR decisao humana, com
opcoes + recomendacao nao vinculante) e ADR-0032/DL-0032 (um ADR corrige outro; achado documentado sem
acao unilateral).

## Consequencias

**Positivas (uma vez ratificado, sob qualquer das duas opcoes):**
- A auto-inconsistencia `maezo-payer/*` vs `maezo-payer-` (§4) deixa de existir — hoje o ADR-0037 e
  literalmente insatisfazivel nas duas clausulas ao mesmo tempo, e nenhum gatekeeper pode julgar
  conformidade contra um alvo contraditorio.
- O achado aberto de DL-0043/PLANS §0.6 deixa de ser uma divergencia indefinida e passa a ser uma
  decisao com duas opcoes fechadas, custo declarado e um ato humano de uma palavra.
- Sob a Opcao 1: o repo para de carregar uma proibicao imutavel que **100%** da sua superficie
  implantada viola na forma — um estado que corroi o valor de sinal de todas as outras proibicoes.

**Negativas (aceitas):**
- **Sob a Opcao 1:** o compromisso de FORMA feito no ADR-0037 muda seis dias apos ratificacao humana;
  se algum artefato cross-repo AMH ja depender literalmente da forma `{a}:{b}:{c}` ou do prefixo
  `maezo-payer-`, essa dependencia quebra. **Este ADR NAO verificou o repo `amh-data-platform`** — ver
  "Limites de verificacao".
- **Sob a Opcao 1:** o objeto de valor do MZO-020 (`identity.py`) fica parcialmente desalinhado da
  forma canonica interna ate um follow-up de codigo proprio; ele continua correto para o eixo de
  identidade portavel AMH (XRD-05), que nao e tocado.
- **Sob a Opcao 2:** migracao com raio de explosao alto sobre idempotencia de start e correlacao de
  mensagem de processos ANS/LGPD vivos, por um ganho que nao inclui a perna (c).
- **Sob qualquer opcao:** a perna (c) (PHI/ID cru em keys) permanece ABERTA. Este ADR reduz o escopo
  do achado de DL-0043, **nao** o fecha.
- Este ADR e docs-only e nao entrega nenhuma verificacao de runtime; ele nao pode ser citado como
  evidencia de conformidade de nenhuma key.

## Limites de verificacao (declarados, nao inferidos)

1. **A clausula de rollback do MZO-020 nao e verificavel neste repo.** O mecanismo "dual-read com alias
   legado" citado na Opcao 2 esta registrado em **DL-0043** (`docs/decisions-log.md:10`), mas o texto
   original vive nos planos de execucao sob `docs/prompts/`, que e **gitignored** (`.gitignore:43`,
   conforme `0037:16-21`) e **nao existe nesta worktree**. A citacao da Opcao 2 e, portanto,
   **de segunda mao via DL-0043** — nao ha `file:line` primario a oferecer, e nenhum foi inventado.
2. **O lado AMH nao foi verificado.** Nao foi inspecionado se o repo `amh-data-platform` (ou o ADR-042
   que fecha XRG-1 do lado deles) referencia literalmente a forma `{a}:{b}:{c}` ou o prefixo
   `maezo-payer-`. Se referenciar, a Opcao 1 exige uma emenda companheira do lado AMH. **Isto e uma
   pergunta em aberto para o dono, nao um fato estabelecido por este ADR.**
3. **Nao foi estabelecido** que uma key portando `matricula_beneficiario` chegue hoje a uma linha de
   log — o mesmo limite honesto que DL-0043 ja declara. Os sitios de log de business key verificados
   (`src/maezo/tools/workers/recurso.py:737,813,903,961` — quatro `logger.warning(...,
   business_key=...)`) emitem keys `RECURSO-*`, que carregam
   `numero_guia_tiss`/`glosa_id` (ID cru de fonte — problema da proibicao 5 do mesmo jeito), nao itens
   de `PHI_PROCESS_VARS`. Isto pertence a perna (c) e nao e resolvido aqui.

## Supersedes

Amends ADR-0037 (proibicao 6, `:212-213`; redacao de namespace de XRD-08, `:148`) — **sem editar o
arquivo do ADR-0037** e **condicionado a ratificacao humana**. Nao supersede ADR-0037 nem nenhum outro
ADR. Nao emenda a proibicao 5 do ADR-0037. Nao toca ADR-0016, ADR-0006, ADR-0035 nem ADR-0036.
