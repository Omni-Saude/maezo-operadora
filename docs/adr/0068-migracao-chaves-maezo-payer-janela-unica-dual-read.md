# ADR-0068: Migração das identidades de processo para `maezo-payer-*` com referência opaca — janela única de leitura dupla (DEC-18 b + R-101 + F-058)

**Status:** Proposed — DESENHO para revisão do dono (`/docs/adr/` é CODEOWNERS). Nenhuma mudança de
código, BPMN, DMN, allowlist, deploy ou chave de engine é autorizada por este documento. A
implementação acontece depois, numa única janela de migração, com plano, aprovadores e gatekeeper
próprios, como pedem o ADR-0038 (Opção 2) e o DL-0157. As cinco perguntas abertas no fim são do dono
(e do DPO na Q1/Q3). Nenhum bloco de aprovação foi preenchido (ADR-0007).
**Data:** 2026-10-31 · **Área:** Integração / Dados / Privacidade / Fronteira de plataforma

> **Numeração.** Outros pacotes da mesma onda também abriram rascunhos como `0068` em paralelo. A
> numeração precisa ser contígua (`tests/unit/docs/test_contagens_canonicas.py`), então quem fizer
> merge depois renumera. Renumerar é mecânico: nome do arquivo, este título, a linha do
> `docs/adr/README.md`, as contagens de `AGENTS.md`/`README.md`/`PLANS.md` e as referências nas seções
> "PROPOSTA (aguarda janela)" dos contratos.

## Contexto

### As três decisões do dono que este ADR junta

Decisões de 2026-10-31, registradas pelo orquestrador a partir da resposta do dono aos issues #1081–#1085:

1. **DEC-18 (b).** Renomear as process keys para o namespace `maezo-payer-*` que a proibição 6 do
   ADR-0037 exige (`docs/adr/0037-amh-compatibility-boundary-canonical-contracts.md:212-213`), em vez de emendar o
   ADR-0037. Isso executa a Opção 2 do ADR-0038, já escolhida pelo dono em 2026-10-29 (DL-0157,
   `docs/decisions-log.md:10`; registro datado em `docs/adr/0038-adr0037-proibicao6-forma-chaves-cib.md:22-41`):
   manter a forma congelada e migrar os call sites para
   `{company_tenant_ref}:{workflow_type}:{workflow_business_ref}` e process keys `maezo-payer-*`,
   "sob uma janela de dual-read com alias legado explícito e sem rewrite destrutivo"
   (`docs/adr/0038-adr0037-proibicao6-forma-chaves-cib.md:312-326`).
2. **R-101.** As duas formas da chave CONTAS ganham prefixos de forma distintos, `-L-` (lote) e `-G-`
   (guia+conta), e `numero_guia_tiss` com `-` é recusado (fail-closed). O compositor único é
   `src/maezo/domain/chave_de_negocio.py::contas_business_key`, e o resíduo que ele declara hoje está
   em `src/maezo/domain/chave_de_negocio.py:85-90`.
3. **F-058.** As referências de business key ficam opacas na saída: pseudônimo HMAC com chave por
   tenant, partindo do modo `scrub_only` (`spec/policies/privacy/phi-business-key-remediation.yaml:77-90`)
   e de `src/maezo/platform/privacy/key_scrubber.py`. Isso inclui o `_business_key` dentro dos
   payloads de `agents.events.*` (`src/maezo/tools/workers/events.py:317-320`). Na rodada 2 o DPO
   aceitou o risco até o MENOR entre 2027-01-26 e a primeira publicação em produção com dado real,
   e ratifica `scrub_only` quando a chave HMAC e a janela de drenagem existirem.

O dono decidiu as três **na mesma janela de migração com leitura dupla, antes do go-live**
(análise `MARINA-activation-and-WHATSAPP-dedup.md` §A.2 e `DPO-S3-rawkey-DEC11.md` §B, opção B4, em
`docs/prompts/fcp-protocol-2026-10-30/analyses/`, que é gitignored). O motivo: as três mexem na MESMA
identidade. Fazer em separado seria abrir duas ou três janelas de leitura dupla sobre as mesmas
chaves.

**DL-0043, perna (c)** ("um agente não reescreve chaves de processo implantadas", `docs/decisions-log.md:147`)
fica respeitada. A troca de chave não é ad hoc: é uma migração decidida pelo dono, com plano (este
documento), sem reescrever instância viva e com alias legado explícito.

### Fatos verificados nesta árvore (origin/main `2be4515f`)

**Process keys.**
- `spec/processes/bpmn/*.bpmn` declara **20** `process id`, todos `SP-OP-*`:
  - as 15 de `src/maezo/tools/process_allowlist.py::KNOWN_PROCESS_KEYS`;
  - `SP-OP-SUPP-001`;
  - quatro variantes de timer de `SP-OP-ANS-CRON-001` (`-DIOPS`, `-RN209`, `-RN388`, `-RN424TISS`).
- A variante `-RN124SIP`, citada pelo ADR-0038, não existe mais.
- A regex do allowlist (`src/maezo/tools/process_allowlist.py:63`) rejeitaria hoje qualquer
  `maezo-payer-*`.

**Composição da forma alvo, que já existe sem chamador.**
- `src/maezo/domain/integration/identity.py::WorkflowBusinessRef` compõe
  `{company_tenant_ref}:{workflow_type}:{workflow_business_ref}`.
- `src/maezo/domain/integration/identity.py::payer_process_definition_key` compõe `maezo-payer-{workflow_type}`.
- O componente aceita só `[A-Za-z0-9._-]` (`src/maezo/domain/integration/identity.py::PERMITTED_CHARACTERS`),
  com tamanho 2–128.
- Recusa, **por desenho**, um valor cujo formato compacto tenha 11 ou 15 dígitos ("CPF-like"/"CNS-like",
  `src/maezo/domain/integration/identity.py:182-207`). Ou seja, um `numero_guia_tiss` de 15 dígitos
  em claro seria recusado como componente. A forma do ADR-0037 foi feita para carregar referência
  opaca, não ID cru.

**Compositores de business key: cerca de 30 sítios.**
- Agentes: Rafael, Marina, Beatriz, Carolina, Gustavo, Helena, Lucas, Fernando, André e Valentina.
- Workers: `contas.py`, `recurso.py`, `fraude.py`, `adequacao.py`, `ans_submit.py` e `base.py`.
- A ponte de notificações: `src/maezo/platform/notification_bridge.py:274-568`.
- O motor de portal em Java: `src/maezo/portal/engine/java/src/main/java/br/com/maezo/human/AuthBusinessKeys.java`,
  com vetores de paridade em `src/maezo/portal/engine/java/src/test/resources/auth-business-key-vectors.json`.

**Leituras que dependem da business key E da process key.**
- `src/maezo/tools/mcp_cibseven/transport.py::CibSevenHttpTransport.find_active_instance` consulta por
  `businessKey`.
- `src/maezo/tools/mcp_cibseven/transport.py::CibSevenHttpTransport.find_any_instance` consulta o
  histórico e filtra por `processDefinitionKey` no cliente.
- A reivindicação durável é
  `src/maezo/tools/mcp_cibseven/transport.py::start_dedup_key` = `{tenant}:start:{process_key}:{business_key}`.
  Ela fica na tabela `audit_emit_dedup`, sem coluna de postura.
- O predicado de retenção dos claims `PERMANENT` é um `LIKE` sobre o segmento da process key
  (`src/maezo/tools/mcp_cibseven/transport.py:1503-1514`).
- A postura de cada processo vem de
  `src/maezo/tools/mcp_cibseven/transport.py::_START_DEDUP_POLICY` (PAGTO `PERMANENT`; CANCEL e AUTH
  `EXCLUSIVE`; os demais `NON_STRICT`), mais a promoção escura de CONTAS em
  `src/maezo/tools/mcp_cibseven/transport.py::_FLAGGED_START_DEDUP_PROMOTIONS`.

**Saída (F-058).**
- A chave de mensagem Kafka passa por `src/maezo/platform/privacy/key_scrubber.py::egress_message_key`,
  que só pseudonimiza CANCEL/INAD, e só com a política ratificada
  (`src/maezo/platform/privacy/key_scrubber.py::PSEUDONYMIZED_MESSAGE_KEY_FAMILIES`).
- O `_business_key` cru vai no VALOR de toda mensagem de `agents.events.*`.
- O fallback de partição monta chaves com `numero_guia_tiss`/`numero_contrato`/`prestador_id` crus
  (`src/maezo/platform/integrations/partition_key.py:127-139`).
- O agent-resume:
  - lê `_business_key` e exige o prefixo `CANCEL-{tenant}-` (DL-0111,
    `src/maezo/platform/integrations/agent_resume.py:324-335`);
  - compara a chave do evento com a chave do histórico do motor
    (`src/maezo/platform/integrations/agent_resume.py:387`);
  - reconhece ESC por recomposição (`src/maezo/domain/conversa/caso_clinico.py::chave_e_da_conversa`).

**Consumidores que discriminam pela process key.**
- O plano de autorizações do motor seguro: `src/maezo/platform/engine_bootstrap/secured_plan.py:327-341`
  (recurso `PROCESS_DEFINITION` com id = process key).
- No Java: `src/maezo/portal/engine/java/src/main/java/br/com/maezo/workload/NativeAdmissionV2.java:86-89`.
- Os bindings de formulário do portal: `src/maezo/portal/engine/decision.py::_BINDINGS` e
  `src/maezo/portal/contracts/models.py`, com 46 literais.
- As fontes de publicação de programa (`src/maezo/platform/privacy/program_publication.py::PROGRAM_PROCESS_KEY`).
- As fontes de fato de timer ANS (`src/maezo/tools/workers/events.py:175`).
- O mapa de PEP `src/maezo/gateway/effect_pep.py`, com 15 literais.
- Os `process_keys` de `spec/agents/*/agent.yaml`.
- A lista `admitidos` de `deploy/aws-ecs/envs/dev-sa-east-1/portal-admissao.json`.

**Tamanho medido.**
- 149 arquivos de `src/` citam `SP-OP-*`; cerca de 60 deles têm literal entre aspas usado em runtime.
  O resto é docstring.
- 333 arquivos de `tests/`.
- 25 de `deploy/`.
- 33 goldens de `tests/evals/` com chaves no formato atual.
- 46 arquivos de `tests/integration/`.

## Decisão (proposta)

### 1. Gramática alvo

**1.1 Process key.** É `maezo-payer-{workflow_type}`, composta só por
`src/maezo/domain/integration/identity.py::payer_process_definition_key`.

- `workflow_type` é o sufixo da chave atual sem `SP-OP-`, em minúsculas, com a versão: `SP-OP-AUTH-001`
  vira `maezo-payer-auth-001`, e `SP-OP-ANS-CRON-001-DIOPS` vira `maezo-payer-ans-cron-001-diops` (Q4).
- O nome do arquivo `.bpmn` NÃO muda. `engine_deploy` implanta por conteúdo e nome de deployment, não
  por nome de arquivo, e renomear arquivo mexeria em censos e citações sem ganho.

**1.2 Business key.** É `{company_tenant_ref}:{workflow_type}:{workflow_business_ref}`, composta só por
`src/maezo/domain/integration/identity.py::WorkflowBusinessRef`.

- `company_tenant_ref` é o `tenant_id` atual (por exemplo, `amh`).
- O `:` está fora do charset. Por isso a chave tem exatamente dois separadores por construção, e a
  posse se decide por recomposição. Isso fecha o resíduo de fronteira de componente que o ADR-0038
  registrou (`docs/adr/0038-adr0037-proibicao6-forma-chaves-cib.md:234-272`).

**1.3 Referência canônica em claro (`ref_claro`).** Cada processo declara uma ou mais FORMAS. Cada forma
é uma etiqueta de uma letra seguida dos componentes da chave atual, nesta ordem. A etiqueta resolve
a colisão entre formas do mesmo processo, que é a lição de R-101 generalizada.

- A entrada do HMAC é a codificação netstring de `(versão "bk1", workflow_type, etiqueta, componente...)`.
  Ela é injetiva por construção e não depende de nenhum componente estar livre de `-`.
- A única recusa nova é a decidida pelo dono em R-101: `numero_guia_tiss` com `-` na forma `G` de
  CONTAS. Ela vale nas duas gramáticas durante a janela, porque a chave legada continua sendo composta
  na leitura dupla.

**1.4 `workflow_business_ref` no motor.** É uma de duas coisas, conforme a Q1 (recomendação (c)):

- **opaca:** `hk1_<HMAC-SHA256 em hex>` (68 caracteres), quando a `ref_claro` carrega ID cru de fonte
  ou dado pessoal, ou não cabe no charset;
- **em claro:** a própria `ref_claro`, quando ela não tem ID cru de fonte, não tem dado pessoal e cabe
  no charset.

**1.5 Forma de saída.** Vale para chave Kafka, `_business_key` em payload, log, métrica, trace e
metadado de DLQ/quarentena. É SEMPRE `{tenant}:{workflow_type}:hk1_<HMAC(ref_claro)>`, para toda
família, sem exceção.

- Sob a Q1 (c) ou (b), a saída das famílias opacas é a própria chave do motor, e não há transformação
  na saída.
- Sob a Q1 (a), a saída é calculada por uma função única.
- O HMAC usa o `src/maezo/gateway/pseudonymizer.py::Pseudonymizer` com chave por tenant
  (`PHI_HMAC_KEY`, a mesma fonte de `src/maezo/platform/privacy/key_scrubber.py::egress_pseudonymizer`),
  com separação de domínio pelo prefixo `bk1` da entrada (Q3).

### 2. Tabela por processo

Colunas: process key atual → nova; business key atual (formas) → nova (forma no motor sob a recomendação
Q1-c); classificação de dado pessoal (do `DPO-S3-rawkey-DEC11.md` §B, a confirmar pelo DPO); forma de
saída. Abreviações: `{t}` = tenant; `H(...)` = `hk1_` + HMAC por tenant da netstring da `ref_claro`.

| # | Process key atual → nova | Business key atual | Nova (motor, Q1-c) | Dado pessoal / ID cru | Saída |
|---|---|---|---|---|---|
| 1 | `SP-OP-AUTH-001` → `maezo-payer-auth-001` | `AUTH-{t}-{numero_guia_tiss}` | `{t}:auth-001:H(G,guia)` | Sim, pseudônimo; saúde por inferência; ID cru de fonte | `{t}:auth-001:H(...)` |
| 2 | `SP-OP-CONTAS-001` → `maezo-payer-contas-001` | `CONTAS-{t}-{lote}` ou `CONTAS-{t}-{guia}-{conta}` | `{t}:contas-001:H(L,lote)` ou `H(G,guia,conta)`; `ref_claro` `L-{lote}` ou `G-{guia}-{conta}`, guia sem `-` (R-101) | L: ID cru de fonte, em geral não pessoal; G: pessoal | `{t}:contas-001:H(...)` |
| 3 | `SP-OP-RECURSO-001` → `maezo-payer-recurso-001` | `RECURSO-{t}-{guia}-{glosa_id}` | `{t}:recurso-001:H(G,guia,glosa)` | Sim, pseudônimo | idem |
| 4 | `SP-OP-PAGTO-001` → `maezo-payer-pagto-001` | `PAGTO-{t}-{ordem}`; `PAGTO-{t}-{lote}-{prestador}`; `PAGTO-{t}-{guia}-{glosa}` | `H(O,ordem)`; `H(L,lote,prestador)`; `H(G,guia,glosa)` | G: pessoal; L: pessoal só se o prestador for PF; O: ID cru | idem |
| 5 | `SP-OP-CANCEL-001` → `maezo-payer-cancel-001` | `CANCEL-{t}-{numero_contrato \| matricula \| beneficiario_pseudo_id}` | `H(C,contrato)`; `H(M,matricula)`; `H(P,pseudo)` | Sim; matrícula é PHI (`PHI_PROCESS_VARS`) | idem |
| 6 | `SP-OP-INADIMPLENCIA-001` → `maezo-payer-inadimplencia-001` | `INAD-{t}-{numero_contrato \| matricula}` | `H(C,contrato)`; a forma M deixa de ser cunhada (F-124 a: exigir contrato e recusar o start) | Sim | idem |
| 7 | `SP-OP-CRED-001` → `maezo-payer-cred-001` | `CRED-{t}-{prestador}`; `CRED-{t}-{prestador}-{protocolo_cred}` | `H(P,prestador)`; `H(R,prestador,protocolo)` | Pessoal se PF; ID cru | idem |
| 8 | `SP-OP-FRAUDE-001` → `maezo-payer-fraude-001` | `FRAUDE-{t}-{numero_caso}` | `H(F,caso)` | Sensibilidade reputacional | idem |
| 9 | `SP-OP-NIP-001` → `maezo-payer-nip-001` | `NIP-{t}-{numero_nip_ans}` | `H(N,nip)` | Sim (reclamação de beneficiário) | idem |
| 10 | `SP-OP-ANS-SUBMIT-001` → `maezo-payer-ans-submit-001` | `ANSSUB-{t}-{report_type}-{competencia}`; `ANSSUB-{t}-nipfiling-{numero_nip_ans}` | em claro `R-{report_type}-{competencia}`; `H(N,nip)` | R: não; N: sim | idem (R também sai como `H(...)`) |
| 11 | `SP-OP-ESCALATION-001` → `maezo-payer-escalation-001` | `ESC-{t}-{conversation_id}[-clin]`; `ESC-{t}-sla-auth-{guia}`; `ESC-{t}-sla-recurso-...` | `H(V,conv)`; `H(K,conv)`; `H(S,auth,guia)`; `H(S,recurso,...)` | Já pseudônimo (`hk1_` na conversa), mas `conversation_id` contém `:`, fora do charset; a forma S carrega guia crua | idem |
| 12 | `SP-OP-LGPD-DSR-001` → `maezo-payer-lgpd-dsr-001` | `DSR-{t}-{titular_pseudo_id}-{tipo}-{data}` | em claro `D-{pseudo}-{tipo}-{data}`, se couber no charset; senão `H(...)` | Já pseudônimo | idem |
| 13 | `SP-OP-PROGRAMA-001` → `maezo-payer-programa-001` | `PROG-{t}-{programa_id}-{beneficiario_pseudo_id}-{ciclo}` | em claro `B-{programa}-{pseudo}-{ciclo}`, se couber; senão `H(...)` | Já pseudônimo | idem |
| 14 | `SP-OP-REEMBOLSO-001` → `maezo-payer-reembolso-001` | `REEMB-{t}-{protocolo_reembolso}`; alternativa `REEMB-{t}-{numero_guia_tiss}` | `H(P,protocolo)`; `H(G,guia)` | Pseudônimo ligado a beneficiário | idem |
| 15 | `SP-OP-ADEQUACAO-001` → `maezo-payer-adequacao-001` | `ADEQ-{t}-{regiao}-{especialidade}[-{ciclo}]` | em claro `A-{regiao}-{especialidade}[-{ciclo}]` | Não | idem |
| 16 | `SP-OP-SUPP-001` → `maezo-payer-supp-001` | `SUPP-{t}-{suppression_ref}` (digest) | em claro `X-{suppression_ref}`, se couber; senão `H(...)` | Já opaco | idem |
| 17–20 | `SP-OP-ANS-CRON-001-{DIOPS,RN209,RN388,RN424TISS}` → `maezo-payer-ans-cron-001-{diops,rn209,rn388,rn424tiss}` | sem business key (start por timer) | — | — | — |

Notas da tabela:

- **Forma em claro com `-`.** Quando uma `ref_claro` em claro tem `-` entre componentes de tamanho
  variável (linhas 10, 12, 13 e 15), a injetividade é garantida porque a forma em claro só é usada
  quando TODO componente, inclusive o final, está livre de `-`. Assim a aridade é fixada pela contagem
  de `-`, e a forma opcional `[-{ciclo}]` de ADEQ não colide com uma `especialidade` contendo `-`.
  Isso é verificado na composição. Se falhar, a
  chave cai na forma opaca: nunca recusa por isso, nunca colide.
- **O que não muda.** As linhas 17–20 seguem fora do allowlist de start por agente, porque são
  disparadas por timer. É a mesma postura de hoje, e o achado colateral do ADR-0038
  (`docs/adr/0038-adr0037-proibicao6-forma-chaves-cib.md:507-525`) fica como está.
- **Universo do allowlist.** O universo de start por agente continua com 15 chaves, agora
  `maezo-payer-*`, e a regex passa a `^maezo-payer-[a-z]+(?:-[a-z]+)*-[0-9]{3}\Z`.

### 3. A janela única: leitura dupla e escrita dupla

**Configuração.** Uma única configuração enumerada, `MAEZO_CHAVES_JANELA`, nas settings de cada
runtime, abaixo da âncora W7 de `src/maezo/runtime/worker_runtime/settings.py:147`. Ela tem quatro
fases. O padrão é `legado`, byte-idêntico ao comportamento de hoje (flag default-off). A virada é ato
de dado do dono (ROLL), nunca de agente.

| Fase | Inícios (escrita) | Leituras de dedup e busca | Saída (Kafka, log, payload) |
|---|---|---|---|
| `legado` (padrão) | process key `SP-OP-*` + chave legada | só legada | como hoje |
| `leitura_dupla` | legada | **alvo, depois legada** | como hoje |
| `escrita_alvo` | **`maezo-payer-*` + chave alvo** | alvo, depois legada | **opaca** (`{t}:{wt}:hk1_…`) |
| `so_alvo` | alvo | só alvo, mais o alias legado (abaixo) | opaca |

**Por que existe `leitura_dupla` antes de `escrita_alvo`.** A troca de imagem é rolante. Se uma réplica
nova começasse a gravar na forma alvo enquanto uma réplica velha ainda só lê a legada, a velha não
veria a instância nova e iniciaria uma duplicata. Por isso toda réplica precisa estar em
`leitura_dupla` antes de qualquer uma estar em `escrita_alvo`. É a mesma ordem leitor-antes-de-escritor
dos protocolos de troca de esquema.

**O que a leitura dupla cobre.** Cada item é um ponto de mudança da implementação.

1. **Busca de instância ativa.** `src/maezo/tools/mcp_cibseven/transport.py::start_process_idempotent`
   chama `find_active_instance` com a chave alvo e, se não achar nada, com a chave legada.
   - Achar qualquer uma devolve `ALREADY_ACTIVE` com aquela instância.
   - A chave legada é calculada pelo MESMO compositor, que nas fases de leitura dupla devolve o par
     `(alvo, legada)`. Nunca há um segundo compositor.
2. **Histórico (`find_any_instance`).** O filtro de `processDefinitionKey` no cliente passa a aceitar o
   PAR `{SP-OP-X, maezo-payer-x}` da família. Sem isso, o gate `PERMANENT` de PAGTO leria "nunca
   iniciado" para um pagamento legado já concluído e liberaria um pagamento duplicado.
3. **Reivindicações duráveis (`audit_emit_dedup`).**
   - **Exclusão mútua entre fases (ordem obrigatória).** Em toda fase ≠ `legado`, o gate estrito
     PRIMEIRO insere o claim da própria forma (`emit_once_status`) e SÓ DEPOIS lê o claim da outra
     forma. Se qualquer um dos dois já existir, trata como acerto de dedup.
     - Se os dois racers se virem mutuamente, ambos recusam fail-closed (`StrictDedupUnresolvable`).
       Nunca os dois iniciam.
     - A ordem inversa (ler e depois inserir) é proibida.
     - O item 2 vale também para o probe de histórico: `find_active_instance` e `find_any_instance`
       rodam para a business key alvo E para a legada do par, cada uma aceitando o par de process
       keys.
     - Teste de integração obrigatório: dois starts concorrentes de PAGTO, um em `leitura_dupla` e
       outro em `escrita_alvo`, terminam com no máximo uma instância.
   - **Alias legado explícito, sem reescrita:** um job da janela INSERE uma linha de alias com a
     `dedup_key` alvo para cada claim `PERMANENT` ou `EXCLUSIVE` vivo. A linha legada nunca é apagada
     nem alterada.
   - O predicado de retenção dos claims `PERMANENT` ganha a cláusula do segmento
     `maezo-payer-pagto-001`.
   - Ordem dos horizontes: retenção do claim ≤ retenção do histórico, como já documentado em
     `src/maezo/tools/mcp_cibseven/transport.py:1515-1522`.
4. **Política de postura.** `_START_DEDUP_POLICY` e `_FLAGGED_START_DEDUP_PROMOTIONS` ganham as chaves
   alvo com a MESMA postura da legada. O teste de completude do mapa contra `KNOWN_PROCESS_KEYS`
   cobre as duas durante a janela.
5. **Correlação de mensagem.** A instância é resolvida por leitura dupla e depois correlacionada por
   `processInstanceId`, não por `businessKey`, para não correlacionar duas vezes nem correlacionar
   com a instância errada. O ponto de mudança é
   `src/maezo/tools/mcp_cibseven/transport.py::CibSevenHttpTransport.correlate_message` e quem o chama.
6. **Guarda cruzada INAD→CANCEL.** `src/maezo/tools/workers/inadimplencia.py::_query_ja_em_rescisao_cancel`
   já faz leitura dupla de formas por
   `src/maezo/tools/workers/base.py::contract_business_key_forms`. Ganha as formas alvo, mantendo a
   recusa fail-closed se a consulta falhar.
7. **agent-resume (DL-0111 e ESC).**
   - A âncora do cancelamento passa a aceitar `CANCEL-{t}-` OU `{t}:cancel-001:`.
   - A conferência do ESC passa a recompor as duas formas (a de `chave_e_da_conversa` e a opaca).
   - A comparação com o histórico do motor (`src/maezo/platform/integrations/agent_resume.py:387`)
     compara formas equivalentes. Sob a Q1-c, chave do motor e chave da saída só coincidem nas
     famílias opacas. Nas famílias em claro (ANSSUB `R`, ADEQ, LGPD-DSR, PROGRAMA, SUPP) e sob a Q1-a,
     a comparação é contra `saida(chave_do_motor)`.
8. **Varredura por prefixo.** `src/maezo/gateway/staff_cases/case_issuer_sources.py:377` (`ESC-{t}-sla-auth-`)
   e o simulador de QA (`src/maezo/qa/simulador/composicao.py:213`) procuram por PREFIXO de chave.
   Com referência opaca isso não funciona. Esses sítios passam a consultar por variável de processo
   (o motor aceita consulta por variável) antes da fase `escrita_alvo`.
9. **Autorizações do motor seguro e do portal.** Durante a janela, o plano de autorizações
   (`src/maezo/platform/engine_bootstrap/secured_plan.py:327-341`, `spec/provisioning/d7d-stage1-v1.json`)
   e o Java (`NativeAdmissionV2.java:86-89`) concedem a permissão para as DUAS process keys.
   - Os bindings de formulário do portal (`src/maezo/portal/engine/decision.py::_BINDINGS`,
     `src/maezo/portal/contracts/models.py`, `src/maezo/portal/web/src/taskReadBindings.ts`) aceitam
     as duas.
   - Uma tarefa humana de instância legada continua concluível pelo portal até o fim da janela.
   - **Cercas por process key (falham abertas se esquecidas).** `ClassifiedConsumerFence.java:62`,
     `ConsumerLineage.java` (o mapa de fontes e `classified`) e `ClassifiedDecision.java` passam a
     reconhecer o PAR `{SP-OP-X, maezo-payer-x}` de AUTH, PAGTO e ESCALATION durante toda a janela.
     - O par é resolvido pelo mesmo mapa único do módulo de domínio.
     - Uma execução `maezo-payer-*` NUNCA pode cair fora da classificação.
     - Teste negativo obrigatório: correlação e conclusão de external task classificada numa instância
       `maezo-payer-pagto-001` são recusadas exatamente como em `SP-OP-PAGTO-001`.
10. **Chave do motor recebida (engine key first).** `src/maezo/agents/andre/keys.py::pagto_business_key`
    (e, por ele, `src/maezo/agents/andre/graph.py::_business_key` e
    `src/maezo/agents/andre/delegation.py::pagto_task_id`) honra a `business_key` recebida VERBATIM
    quando ela pertence a este tenant em QUALQUER das duas formas da janela: prefixo legado
    `PAGTO-{tenant}-` OU prefixo alvo `{tenant}:pagto-001:`.
    - Nunca re-deriva a partir de campos quando existe uma chave recebida válida.
    - Sem isso, um pagamento da forma `L` com ordem no escopo deriva `H(O,ordem)`, erra a própria
      instância, e o gate `PERMANENT` libera um segundo pagamento.
    - Teste negativo obrigatório: instância alvo `H(L,…)` concluída + reentrega com
      `ordem_pagamento_id` no escopo → `ALREADY_COMPLETED`, sem start.

**Resíduo declarado da janela.** Na leitura dupla, a chave LEGADA mantém as colisões de hoje (por
exemplo, PAGTO `ordem="L-P"` contra `lote=L, prestador=P`; CRED, CANCEL e o ESC `-clin`). O efeito
possível é um falso `ALREADY_*`: uma recusa adversa, não uma duplicata, igual ao comportamento atual.
Só a forma alvo é injetiva. O resíduo some em `so_alvo`.

**Kafka: drenagem das partições.** A chave de mensagem muda para TODA família na fase `escrita_alvo`.
O hash continua determinístico, mas a partição muda, e mensagens em voo antes e depois da virada
podem ficar em partições diferentes.

- Procedimento, antes do go-live:
  1. Pausar o worker de `operadora.events.publish`. As external tasks esperam no motor; não se perde
     nada.
  2. Esperar lag zero em todos os grupos consumidores: a ponte e a caixa de notificações em
     `operadora.notifications.internal`, e o agent-resume em `agents.events.process_completed`.
  3. Virar para `escrita_alvo`.
  4. Retomar o worker.
- Os consumidores aceitam as duas formas de `_business_key` durante toda a janela (item 7).
- O fallback de partição por campos crus
  (`src/maezo/platform/integrations/partition_key.py::partition_key_for_task`) passa a usar a forma
  de saída opaca.
- O espelho em `operadora.notifications.internal` deixa de carregar `_business_key` cru: a entrada
  revogável em `src/maezo/platform/integrations/events_kafka_producer.py::_POLICY_REVOCABLE_ALLOWLIST_KEYS`
  passa a levar a forma opaca.
- Os controles compensatórios do DPO (ACL, retenção curta explícita, sem auto-create fora de dev, sem
  chave crua em log) são pré-requisitos do go-live, mas estão FORA desta janela.

### 4. Virada (cutover) e reversão (rollback)

**Pré-condições, todas com evidência no PR da janela:**
- (i) `PHI_HMAC_KEY` provisionada por tenant (`spec/policies/privacy/phi-business-key-remediation.yaml:202-223`),
  também no plugin Java do motor (Q3);
- (ii) código da janela mergeado ESCURO (`legado`), com a suíte local CIB Seven verde duas vezes no
  mesmo motor;
- (iii) BPMN com os ids novos implantados ao lado dos antigos. O motor mantém as definições antigas, e
  as instâncias em voo seguem na definição em que nasceram;
- (iv) entradas `aguardando_admissao` no `deploy/aws-ecs/envs/dev-sa-east-1/portal-admissao.json`
  (CODEOWNERS `/deploy/`);
- (v) consulta de reconciliação que lista, por ambiente, as instâncias ativas por process key legada.

**Passos:**
1. `legado` → `leitura_dupla` em todas as réplicas. Conferir pela métrica de fase por réplica que
   nenhuma ficou para trás.
2. Rodar o job de alias dos claims estritos e conferir as contagens: alias = claims vivos.
3. Pausar o publicador, esperar lag zero, virar `leitura_dupla` → `escrita_alvo`, retomar.
4. Smoke em sintético:
   - um start por família cai em `maezo-payer-*`;
   - um reenvio da mesma solicitação devolve `ALREADY_ACTIVE`;
   - uma solicitação cuja instância é legada devolve a instância legada (sem duplicata);
   - nenhum payload ou log tem ID cru.
5. Esperar o critério de saída (Q5). Em seguida, `escrita_alvo` → `so_alvo`.
6. Num PR posterior, com gatekeeper próprio: remover o código de leitura legada, os literais `SP-OP-*`
   do runtime, as autorizações e bindings duplicados, e a permissão legada do allowlist. As
   definições antigas no motor só saem quando não houver instância nem histórico exigido.

**Reversão:**
- `escrita_alvo` → `leitura_dupla` é SEMPRE segura: instâncias novas continuam visíveis pela leitura
  dupla, e os inícios voltam a ser legados.
- `leitura_dupla` → `legado` só é segura com:
  - ZERO instâncias na forma alvo, ativas OU no histórico; e
  - ZERO claims `audit_emit_dedup` com segmento `maezo-payer-*`.
  As duas contagens vêm da consulta de reconciliação e vão no PR da reversão. Se já houve qualquer
  start na forma alvo, a reversão para em `leitura_dupla` para sempre, até a remoção da janela. O modo
  `legado` não enxerga instância, histórico nem claim alvo, e um pagamento alvo concluído seria
  liberado de novo.
- `so_alvo` → `escrita_alvo` é segura.
- A reversão de Kafka repete a drenagem.
- Nenhuma reversão apaga linha de alias nem reescreve chave.

### 5. O que muda no código (lista de arquivos da implementação)

Um módulo novo de domínio, folha e sem dependência de plataforma, ao lado de `maezo.domain.chave_de_negocio`,
reúne em UM lugar:

- o mapa `SP-OP-*` ↔ `maezo-payer-*`;
- as formas e etiquetas por processo;
- o cálculo da `ref_claro`;
- a composição pelo `WorkflowBusinessRef`;
- o par `(alvo, legada)` da leitura dupla.

O HMAC é injetado como porta, para o domínio não importar a plataforma (ADR-0067). Todo sítio abaixo
passa a importar desse módulo, e nenhum outro sítio monta chave.

- **Allowlist e transporte:** `src/maezo/tools/process_allowlist.py`, `src/maezo/tools/mcp_cibseven/transport.py`,
  `src/maezo/gateway/effect_pep.py`, `src/maezo/gateway/engine_schemas.py`.
- **Compositores:**
  - `src/maezo/domain/chave_de_negocio.py` (R-101: etiquetas L/G e recusa de `-` na guia);
  - `src/maezo/tools/process_business_keys.py`;
  - `src/maezo/tools/workers/base.py`, `contas.py`, `recurso.py`, `fraude.py`, `adequacao.py`,
    `ans_submit.py` e `inadimplencia.py`, sob `src/maezo/tools/workers/`;
  - `src/maezo/platform/notification_bridge.py`;
  - `src/maezo/domain/conversa/caso_clinico.py`;
  - `src/maezo/agents/andre/keys.py`;
  - `src/maezo/agents/andre/graph.py` (`PROCESS_KEY_PAGTO`, único sítio de start de PAGTO) e
    `src/maezo/agents/andre/delegation.py` (`pagto_task_id`, chave de idempotência A2A);
  - os grafos e delegações de `rafael`, `marina`, `beatriz`, `carolina`, `gustavo`, `helena`,
    `lucas`, `fernando` e `valentina`, sob `src/maezo/agents/`;
  - `src/maezo/runtime/agent_runtime/ingress.py`;
  - `src/maezo/gateway/intake/native_composition.py`, `src/maezo/gateway/intake/native_dispatch.py`.
- **Saída e privacidade:**
  - `src/maezo/platform/privacy/key_scrubber.py` (a função única de saída; as famílias pseudonimizadas
    passam a ser TODAS);
  - `src/maezo/platform/privacy/phi_key_policy.py`;
  - `src/maezo/tools/workers/events.py` (`_business_key` opaco; fontes de timer ANS);
  - `src/maezo/platform/integrations/partition_key.py`;
  - `src/maezo/platform/integrations/events_kafka_producer.py`;
  - `src/maezo/platform/integrations/agent_resume.py`;
  - `src/maezo/platform/integrations/notifications_inbox.py`;
  - `src/maezo/platform/privacy/program_publication.py`;
  - `src/maezo/platform/lifecycle/legal_hold.py` (escopo por chave).
- **Varreduras por prefixo:** `src/maezo/gateway/staff_cases/case_issuer_sources.py`,
  `src/maezo/qa/simulador/composicao.py`, `src/maezo/qa/simulador/limpeza.py`,
  `src/maezo/platform/testchannel/resultados.py`.
- **Motor e portal:**
  - `src/maezo/platform/engine_bootstrap/secured_plan.py`, `spec/provisioning/d7d-stage1-v1.json`;
  - `src/maezo/portal/engine/decision.py`, `src/maezo/portal/contracts/models.py`;
  - Java: `AuthBusinessKeys.java`, `NativeAdmissionV2.java`, `StaffEscalation.java`,
    `NativeCaseIdentityReader.java` e os vetores de paridade;
  - Java, cercas e bindings por process key: `PortalReadCommand.java`, `ClassifiedConsumerFence.java`,
    `ConsumerLineage.java`, `ClassifiedDecision.java`, `AuthRuntime.java`,
    `AuthDocumentProducerContext.java`, `RuntimeDefinitionObservation.java`, e os JSON
    `engine-schemas-v1.json` e `provider-native-observation-schema-v1.json`;
  - Python, recusas por process key ou prefixo de chave: `src/maezo/gateway/human/escalation_context.py`,
    `src/maezo/gateway/human/auth_profile.py`, `src/maezo/gateway/denial_notices/producer.py`,
    `src/maezo/gateway/document_requests/completion.py`, `src/maezo/runtime/worker_runtime/document_requests.py`
    e `src/maezo/runtime/intake_dispatch/service.py` (`_business_key_anomaly`, que marcaria a chave alvo
    como `foreign_business_key`);
  - diagnóstico: `src/maezo/platform/engine_bootstrap/bootstrap.py:442-445` (`startswith("SP-OP")`)
    listaria as definições novas como "NÃO-SP-OP";
  - web: `taskReadBindings.ts`, `taskLabels.ts`, `openapi.json` e `src/maezo/portal/web/src/generated/api.ts`.
- **Artefatos:**
  - os 20 `process id` em `spec/processes/bpmn/*.bpmn` (os comentários de chave, como o de CONTAS,
    também mudam);
  - os `process_keys` de `spec/agents/*/agent.yaml`;
  - `deploy/aws-ecs/envs/dev-sa-east-1/portal-admissao.json`, `deploy/observability/alert-rules.yml`
    e os `tfvars`/Helm que citam process key.
  - `docs/processes/New_BPMN/*_v3.bpmn` NÃO é tocado.
- **Fora da lista: menção em docstring ou prompt.** A troca acontece no mesmo PR da janela, mas sem
  efeito de runtime. As DMNs só citam `SP-OP-*` em texto descritivo.

### 6. Impacto em testes e goldens

- **Testes que fixam a forma atual.** Cerca de 170 arquivos de `tests/` têm literal de chave entre
  aspas e 333 citam `SP-OP-*`. Entre eles:
  `tests/unit/agents/test_marina_dedup_key.py`, `tests/unit/tools/test_auth_business_key.py`,
  `tests/unit/tools/test_auth_business_key_parity.py`, `tests/unit/tools/test_process_allowlist.py`,
  `tests/unit/platform/test_agent_def_process_keys.py` e `tests/unit/spec/test_sp_op_contas_001_artefatos.py`.
  - O caminho é parametrizar pela fase: o mesmo teste roda em `legado` com a expectativa de hoje e em
    `escrita_alvo` com a forma alvo.
  - Os testes de `legado` são o teste de regressão byte-idêntico do padrão desligado.
- **Goldens.** 33 arquivos em `tests/evals/` têm chave no formato atual (por exemplo, os goldens da
  Marina; `EVL-MARINA-01` usa o lote `LOTE-EVL-MARINA-01`, que já mostra a colisão de `-`). Eles
  precisam regenerar, com diff revisado.
- **Integração.** 46 arquivos de `tests/integration/` citam `SP-OP-*`. A suíte local CIB Seven roda
  duas vezes no mesmo motor, em cada fase, com `docker compose down -v` entre provas de motor
  persistente. Ela inclui o caso "instância legada viva + solicitação nova": nenhuma duplicata, em
  cada uma das três posturas.
- **Censos e pins que se movem.**
  - `tests/unit/platform/test_engine_deploy.py`, `test_engine_deploy_cli.py`, `test_validation_perspective.py`
    e `tests/unit/spec/test_shadow_candidates_common.py` (só se algum arquivo de spec for adicionado;
    renomear id não adiciona arquivo);
  - os pins de completude de PHI (`tests/unit/platform/test_validation_phi_completeness.py`);
  - os vetores de paridade Java↔Python.
- **Testes negativos obrigatórios, que acompanham o pacote da janela** (guarda de PHI, protocolo item 6):
  1. nenhuma forma de saída contém ID cru: propriedade sobre todas as famílias, com valores sintéticos
     de 11 e 15 dígitos;
  2. a chave de outro tenant nunca é aceita (recomposição, não split);
  3. CONTAS com `-` na guia é recusado nas duas gramáticas;
  4. em `escrita_alvo`, um start com process key legada é recusado pelo allowlist;
  5. a ausência da `PHI_HMAC_KEY` com fase ≠ `legado` falha fechado (sem fallback para claro);
  6. o PERMANENT de PAGTO recusa a forma alvo quando só existe o pagamento legado concluído;
  7. agent-resume recusa uma âncora de CANCEL fora das duas formas.
  8. dois starts concorrentes de PAGTO em fases diferentes (`leitura_dupla` × `escrita_alvo`) terminam com no máximo uma instância (ordem insere-depois-lê, item 3 da §3);
  9. correlação e conclusão classificadas em `maezo-payer-pagto-001` são recusadas como em `SP-OP-PAGTO-001` (cercas Java, item 9 da §3);
  10. uma reentrega de PAGTO com chave alvo recebida e `ordem_pagamento_id` no escopo devolve `ALREADY_COMPLETED` (engine key first, item 10 da §3).
  - Essas guardas não mexem em alçada, CADE, RN 659 ou RN 518. Nenhum item `hard` da matriz de
    autonomia é tocado.

### 7. Emendas de contrato

Cada contrato afetado em `docs/processes/contracts/` recebe, NESTE PR, uma seção final marcada
**"PROPOSTA (aguarda janela)"**. Ela não altera a semântica vigente: a seção "Business key" de cada
contrato continua sendo a regra até a fase `escrita_alvo`.

- A exceção é `docs/processes/contracts/SP-OP-ESCALATION-001.md`, que está no registro de
  congelamento da plataforma comercial
  (`docs/delivery/commercial-platform/SOURCE-FREEZE-C6-4-MAEZO-R4.json`, `source_paths[4]`). A
  proposta dele vive só na linha 11 da tabela acima, e a emenda entra no PR da janela.
- O contrato `SP-OP-ANS-CRON-001.md` recebe a proposta das quatro variantes.

## Relação com ADRs e decisões existentes

- **ADR-0037:** não é editado. Este ADR implementa a forma da proibição 6 e, sob a Q1-c, também a
  proibição 5 nas chaves (`docs/adr/0037-amh-compatibility-boundary-canonical-contracts.md:210-213`).
- **ADR-0038 / DL-0157:** este é o "work package próprio, com plano de janela" que o registro de
  2026-10-29 exige. A auto-inconsistência XRD-08 já foi resolvida pela grafia da proibição 6.
- **DL-0043:** as pernas (a) e (b) fecham na implementação. A perna (c) fecha sob a Q1-c e fica
  PARCIAL sob a Q1-a.
- **ADR-0016:** o universo do allowlist muda de grafia (15 → 15), a regex muda, e as variantes de
  timer seguem fora.
- **D-02** (`docs/plans/maezo-completion-program.md:131`) diz que pontes novas entram "como NOVOS
  process-keys SP-OP-*". Com DEC-18 (b), esse literal fica desatualizado e pede uma errata datada no
  plano. A errata é ato de registro do dono e NÃO é feita aqui.
- **ADR-0050:** precedente de alias legado por RECUSA (`AUTHI-`). Aqui o alias legado é por LEITURA
  durante a janela, e por recusa depois de `so_alvo`.
- **DL-0111:** a âncora do cancelamento muda de forma (item 7 da §3).
- **DL-0046:** a postura de CONTAS fica inalterada; a promoção escura continua escura.
- **`phi-business-key-remediation.yaml`:** continua DRAFT. A ratificação de `scrub_only` é do DPO.
  Este desenho é o que torna `janela_drenagem` cumprível.

## Consequências

**Positivas:**
- Uma janela em vez de três, sobre a mesma identidade.
- A chave passa a ter tenancy estrutural: `:` fora do charset, e posse por recomposição.
- A saída fica opaca para TODA família, não só CANCEL/INAD.
- O resíduo de colisão de CONTAS fecha (R-101), e PAGTO, CRED e CANCEL deixam de depender de `-`
  para separar formas.
- Nenhuma reescrita destrutiva: a leitura dupla e o alias são só inserção.

**Negativas (aceitas):**
- O raio de explosão é alto: cerca de 60 arquivos de runtime, Java, web, BPMN e deploy, mais cerca de
  170 testes e 33 goldens.
- O start ganha uma segunda consulta ao motor durante a janela (latência: contar no SLI p95 de R-086).
- Sob a Q1-c, o Cockpit deixa de mostrar o número da guia na chave. A busca passa a ser por variável
  de processo.
- A rotação da chave HMAC vira uma nova janela (Q3).
- `/deploy/`, `.github/` e `scripts/ci/` são CODEOWNERS e exigem revisão humana no PR da janela.

## Perguntas abertas (dono; Q1 e Q3 também DPO)

**Q1 — `workflow_business_ref` no motor: opaca ou em claro?**
- (a) Em claro no motor, opaca só na saída. É a leitura literal da decisão F-058 ("opaca na saída").
  Mas:
  - a proibição 5 continua violada no `BUSINESS_KEY_` do motor e na tabela de dedup;
  - o `WorkflowBusinessRef` recusaria guias de 11 ou 15 dígitos, o que obrigaria a afrouxar a guarda
    XRD-05;
  - o agent-resume precisaria transformar antes de comparar.
- (b) Opaca para TODA família.
- (c) Opaca no motor para as famílias com ID cru de fonte ou dado pessoal; em claro para as que não
  têm. A saída é sempre opaca.
- **Recomendação: (c).** Satisfaz "opaca na saída" com folga (a chave do motor JÁ é a forma de saída),
  cumpre a proibição 5 sem afrouxar o MZO-020 e mantém legível o que não é pessoal (ANSSUB de
  relatório, ADEQ).

**Q2 — Instâncias legadas em voo.**
- (a) Drenar naturalmente com leitura dupla até zero.
- (b) Migrar instâncias para as definições novas pela API de migração do motor. A business key não
  muda, então a leitura dupla continua necessária.
- (c) Antes do go-live, só há dado sintético: reconciliar, encerrar as instâncias sintéticas com
  registro, e manter leitura dupla e alias dos claims estritos como rede.
- **Recomendação: (c)**, com (a) para qualquer ambiente que já tenha instância de valor. Sem
  reescrita de business key em nenhum caso.

**Q3 — Custódia e rotação da chave HMAC.**
- (a) A mesma `PHI_HMAC_KEY` por tenant do `Pseudonymizer`, com separação de domínio pelo prefixo
  `bk1`.
- (b) Uma chave dedicada só para business key.
- Em qualquer caso, a rotação NUNCA é silenciosa: uma versão nova (`hk2_`) só entra por outra janela
  de leitura dupla. O plugin Java do motor precisa receber a chave (segredo novo no motor).
- **Recomendação: (a)**, com o prefixo de versão gravado na chave e a rotação como janela registrada.

**Q4 — Versão na process key.**
- (a) Manter: `maezo-payer-auth-001`. Fica 1:1 com contratos e arquivos, segue o exemplo do ADR-0038 e
  a regex do allowlist continua análoga.
- (b) Remover: `maezo-payer-auth`.
- **Recomendação: (a).**

**Q5 — Critério de saída da janela.**
- (a) Prazo fixo de 30 dias. O ADR-0037 aceita 30 dias para contratos de fio AMH, mas as business keys
  não cruzam essa fronteira: o ADR-0038 verificou zero ocorrência na superfície AMH pinada
  (`docs/adr/0038-adr0037-proibicao6-forma-chaves-cib.md:552-560`).
- (b) Por evidência:
  - zero instância ativa legada em todo ambiente (consulta de reconciliação);
  - alias = claims estritos vivos;
  - lag zero na virada;
  - smoke verde.
- **Recomendação: (b)**, com piso de 7 dias em `escrita_alvo` antes de `so_alvo`, para cobrir
  reentregas e timers curtos.

## Supersedes

—. Implementa o ADR-0038, Opção 2 (DL-0157). Não edita o ADR-0037 nem nenhum ADR aceito.
