# MZO-040 — `ActionExecutionGateway`: pacote de evidência para aprovação

**Estado:** o gateway está CONSTRUÍDO e LIGADO EM SOMBRA. Nada está aprovado. Nada é bloqueado.
**Aprovações exigidas:** **Médica**, **ANS**, **Security** — as três ABERTAS (`PLANS.md` §0.6;
DL-0042 descarregou o portão DPO/Legal do MZO-020 e diz em termos que **não alcança** estas três).
**Artefato que os aprovadores tocam:** `spec/policies/autonomy/action-approvals.yaml`
— **CODEOWNERS-LISTED** (`@rodrigotaquino @Omni-Saude/security`), **não CODEOWNERS-gated**: a
proteção server-side do `main` **não está ativa hoje** (branch protection 404 + rulesets vazios,
verificado contra o remoto), então a entrada CODEOWNERS **pede** um revisor de segurança sem
conseguir **exigi-lo**. Esse achado do dono já está registrado na row `mzo-000` do
`docs/evidence-ledger.md` (restaurar a proteção é decisão do dono, não deste wave). Ver §6,
resíduo 2.

> **Nenhum bloco de aprovação abaixo pode ser preenchido por um agente.** Todos os campos estão
> como `PENDENTE` e todos os `aprovado` estão `false`. Isso não é um placeholder aguardando
> limpeza — é o estado honesto de três portões humanos abertos. Uma aprovação forjada é um evento
> de conformidade, não um bug.

---

## 1. Por que este pacote existe

A ADR-0034 ratificou o **descope** do chokepoint PEP por-chamada, com **uma** precondição de
revisita: "a amostragem L2 só volta a ser root-cause-correct SE e QUANDO o v2 introduzir um
chokepoint de dispatch PEP por-tool-call em runtime" (`docs/adr/0034-...:78-85`).

A cláusula **XRD-09** da ADR-0037 é o veículo de ratificação que essa precondição exige, e declara
a própria condição de efetividade: "só produz efeito com a aceitação deste ADR + **evidência
MZO-040 entregue**" (`docs/adr/0037-...:151-157`, `:241-244`).

Isso cria um laço: os aprovadores não podem aprovar sem evidência, e não há evidência sem
construir. Este wave desfaz o laço na única direção segura — **construir o gateway inerte**. Ele
avalia cada chamada no chokepoint, emite telemetria estruturada sem PHI, e **nunca altera
execução**. Os aprovadores ratificam contra o que o sistema em execução realmente faz, não contra
uma descrição de projeto.

**Ratificar é uma mudança de DADOS.** Sem mudança de Python, sem redeploy do gateway. É
exatamente por isso que o dado precisa do revisor que o caminho de código teria — o mesmo
raciocínio que o `.github/CODEOWNERS` já registra para o manifesto do GAP-AUTH-4.

---

## 2. O que cada classe de ação faz HOJE, e o que a aplicação vai negar

Dez classes. Cada uma tem referente real de runtime (`file:line`), verificado por
`tests/unit/sec/test_action_execution_fence.py::test_every_class_cites_a_runtime_referent_that_exists`.

| Classe | O que faz hoje (`file:line`) | O que a aplicação nega | Evidência de sombra? |
|---|---|---|---|
| `autorizacao_emissao` | Emite autorização prévia — `tools/workers/auth.py:1125` (`operadora.auth.issue_authorization`) | A emissão da autorização; o pedido vai a incidente → humano | **Sim** |
| `negativa_notificacao` | Notifica negativa já decidida por humano — `auth.py:1369`, `reembolso.py:813` | O envio da notificação (não a decisão, que é L0 hard e nasce em User Task) | **Sim** |
| `submissao_regulatoria_ans` | Envio/retransmissão ANS, resposta NIP, recurso — `ans_submit.py:900`/`:911`, `nip.py:552`, `recurso.py:1090` | A submissão oficial ao canal ANS | **Sim** |
| `pagamento_emissao` | Libera pagamento a prestador/beneficiário — `pagto.py:896`/`:898`, `reembolso.py:808` | A liberação do pagamento | **Sim** |
| `vinculo_contratual_mudanca` | Suspensão, cancelamento, descredenciamento — `inadimplencia.py:753`, `cancel.py:731`/`:737`, `credenciamento.py:770` | A mudança de vínculo | **Sim** |
| `acusacao_fraude_registro` | Registra acusação / encaminha ao jurídico — `fraude.py:999`/`:1002` | O registro e o encaminhamento | **Sim** |
| `comunicacao_beneficiario` | Contato proativo, resposta DSR LGPD, pedido de prova — `programa.py:673`, `lgpd.py:765`/`:763`; WhatsApp `mcp_whatsapp/server.py:111` | O contato externo com o titular | **Sim** (exceto o WhatsApp direto) |
| `inicio_processo_regulatorio` | Inicia instância SP-OP-* — `contas.py:1055`/`:1061`, `adequacao.py:689`, `fraude.py:1005`/`:1011`, `inadimplencia.py:757`, `nip.py:562`; agente `mcp_cibseven/transport.py:1052` (`start_process_idempotent`) | O start do processo | **Sim** (exceto o start por agente) |
| `leitura_phi_clinica` | Lê dado clínico FHIR — `mcp_fhir/server.py:88`/`:118` | A leitura de PHI | **NÃO** — superfície não choked |
| `delegacao_a2a` | Delega agente→agente — `a2a/dispatcher.py:277` | A delegação | **NÃO** — superfície não choked |

### O que "negar" significa concretamente

Sob `modo: enforcing`, um DENY faz o chokepoint: (a) **não executar o handler** — o efeito é
pré-empetido, não desfeito; (b) emitir uma linha de auditoria `REFUSED` na cadeia ADR-0007 com
`guard_code=ERR_ACTION_GATEWAY_NOT_HUMAN`; (c) reportar falha ao engine com `retries=0` → incidente
**sempre visível a humano** (ADR-0008: guards nunca são retentados; ADR-0030 §4). A requisição não
prossegue **e não desaparece**.

---

## 3. O risco que cada domínio está atestando

Cada classe exige **os três** domínios. Isso vem de `PLANS.md` §0.6 e do DL-0042, que enunciam o
portão como Médica + ANS + Security sem diferenciação por classe. **Estreitar** o conjunto exigido
de uma classe (p.ex. decidir que pagamento não precisa de atestação médica) é decisão de governança
para um HUMANO em PR revisada — um agente nunca a faz — e o loader **não a aceita nem como dado**:
ele exige `set(dominios_exigidos) == {medica, ans, seguranca}` EXATAMENTE. Lista vazia
(`DOMINIOS_EXIGIDOS_VAZIO`), subconjunto `[medica]` e repetição que finge cardinalidade
`[medica, medica, medica]` (ambos `DOMINIOS_INCOMPLETOS`) e domínio fora do conjunto congelado
(`DOMINIO_DESCONHECIDO`) **negam**, cada um com razão distinta. Isso é no-op para o arquivo
implantado, que declara os três em todas as classes.

- **Médica** atesta que, para cada classe, o gate por-chamada é o lugar CERTO para interromper um
  efeito assistencial, e que interrompê-lo (incidente → auditor médico) é clinicamente mais seguro
  do que deixá-lo passar. Atenção específica a `autorizacao_emissao` e `negativa_notificacao`: um
  DENY nessas classes **atrasa cuidado**. O risco atestado é de PRAZO, não de decisão.
- **ANS** atesta que interromper `submissao_regulatoria_ans` e `inicio_processo_regulatorio` não
  cria dano de PRAZO REGULATÓRIO pior do que o gap que fecha — precisamente a distinção que o
  DL-0038 já fez ao manter `ans_submit.notify_regulatorio` como best-effort por desenho.
- **Security** atesta a postura fail-closed do loader, a ausência de PHI na telemetria, o
  CODEOWNERS do manifesto, e os resíduos declarados na §6.

---

## 4. O ato exato de ratificação

Quatro passos, todos no mesmo arquivo, todos em uma PR que **deve** ser revisada pelo CODEOWNER de
segurança — "deve" como disciplina de processo, **não** como bloqueio server-side (ver o cabeçalho
e o resíduo 2 da §6):

1. **Cada aprovador preenche o SEU bloco**, por classe que atesta:
   ```yaml
   aprovado:      true                    # o literal booleano true — nada mais conta
   aprovador:     "<nome, papel>"         # ADR-0007: QUEM
   data:          "YYYY-MM-DD"            # QUANDO
   evidencia_ref: "<o que foi revisado>"  # CONTRA O QUÊ — janela de telemetria, relatório, ata
   ```
   O literal `PENDENTE` é o placeholder detectável por máquina: um campo deixado como `PENDENTE`
   (ou vazio, ou nulo) significa NÃO aprovado, **mesmo com `aprovado: true` ao lado**. Preenchimento
   parcial lê-se como edição inacabada, nunca como ratificação.
2. **Completar `mapeamento_topicos`** para todo tópico que deva ser gated — ver §5, é a parte mais
   fácil de esquecer e a de maior consequência.
3. **`status: RATIFICADO`** (o literal exato; `ratificado`, `RATIFICADO ` com espaço, etc. não contam).
4. **`modo: enforcing`**.

Os passos 3 e 4 **são** o interruptor. Nenhum dos dois sozinho abre nada: `status: RATIFICADO` com
os blocos ainda em branco aprova zero classes
(`test_flipping_status_alone_still_requires_real_approvals`), e `modo: enforcing` sobre um arquivo
DRAFT **nega tudo** — direção deliberadamente segura.

**Efetivação:** o manifesto é cacheado por processo (`lru_cache`, o mesmo trade-off de
`auth_criteria.criteria_sources` e `ceilings._load_matrix_cached`). Uma ratificação passa a valer
**no próximo restart do daemon**, não ao vivo. Registrado aqui para que ninguém espere um flip
quente.

### Duas armadilhas afiadas — leia antes de virar o modo

1. **`Decision.allow` sem `Decision.enforced` é uma cilada de integração.** `allow` é o veredito
   *em termos de enforcement*; `enforced` diz se ele **vale**. Todo call site novo tem de testar
   **os dois** (`if decision is not None and decision.enforced and not decision.allow:` — a forma
   usada em `WorkerHarness._handle`). Um call site que olhe só `allow` começa a **bloquear em
   sombra** — ou seja, o wave inteiro deixa de ser inerte no momento em que alguém liga a segunda
   superfície da §7. É uma costura afiada por desenho (o par `allow`/`enforced` é o que torna a
   sombra provável), e é por isso que ela está documentada aqui e não apenas no docstring.
2. **Um erro de digitação em `modo` desliga o portão em silêncio.** `enforcing ` (espaço à
   direita), `Enforcing`, `enforce` ou qualquer não-literal resolve para `unresolved`, que **nunca
   aplica**. A direção é deliberadamente segura — nada bloqueia por acidente — mas a consequência
   é que **uma ratificação malsucedida é indistinguível, de fora, de um deployment de sombra
   saudável**. Depois de virar o modo, **verifique na telemetria**: `mode` tem de ler `enforcing`
   e o nome do evento tem de ser `action_execution_gateway_enforced` (em sombra é
   `action_execution_gateway_shadow`). O mesmo vale para `status`: só o literal exato
   `RATIFICADO` conta.

---

## 5. AVISO OPERACIONAL — o mapa de tópicos está deliberadamente incompleto

`mapeamento_topicos` cobre hoje **26 tópicos** dos **105** declarados em `spec/processes/bpmn/**`
(contagem verificada nesta sessão; os 26 são exatamente os `superficies[].choked: true`).
Só estão mapeados aqueles cujo efeito externo é inequívoco a partir do próprio worker. Os demais
— cálculo, triagem, leitura de fatos, notificações internas de SLA — **não** estão classificados,
e classificá-los é ato humano, não inferência de agente.

**Sob `modo: enforcing`, um tópico NÃO mapeado é DENY** (`ACAO_NAO_MAPEADA`). É o que a XRD-09
manda ("ação/política desconhecida ... negam"), e significa que **virar o modo com o mapa
incompleto interrompe todo tópico não classificado**. Isso é intencional: torna a completude do
mapa uma precondição explícita do ato de ratificação, e não um follow-up.

A telemetria de sombra é exatamente a ferramenta para isso: cada tópico não mapeado emite uma
linha `ACAO_NAO_MAPEADA` por dispatch, contra tráfego real. **Essa é a lista de trabalho.**

---

## 6. Resíduos declarados (Security deve ler esta seção antes de assinar)

1. **`MAEZO_ACTION_APPROVALS_PATH` — o override de deployment troca o registro governado.** Uma
   variável de ambiente aponta o loader para QUALQUER arquivo. Isso é **mais forte** do que o
   resíduo de remoção abaixo: não degrada, **substitui** — um manifesto que nenhum CODEOWNER viu
   pode declarar o que quiser, sem uma única edição no arquivo listado no CODEOWNERS.
   **FECHADO NESTE REPARO, na perna que importa:** um manifesto vindo do override e lendo
   `modo: enforcing` resolve para `shadow_override` e **nunca** aplica, a menos que o operador
   também exporte `MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT=1` (o literal exato) — que é
   o caminho legítimo de **rollout encenado**. Assim, "trocar o manifesto" e "ligar o enforcement"
   passam a ser **dois atos deliberados e auditáveis em separado**, nunca um. Todo load vindo do
   override emite uma linha `error` (`action_approvals_manifest_path_overridden`) com o caminho
   resolvido e se o enforcement foi permitido; e um ALLOW lido de um manifesto override sem o
   flag carrega o token distinto `OVERRIDE_NAO_ENFORCAVEL`, para não ser confundido com uma
   aprovação governada. **O QUE PERMANECE ABERTO:** quem controla o ambiente do processo ainda
   pode setar as DUAS variáveis. Isso é irredutível em código — é uma fronteira de deployment
   (quem edita o Deployment/ConfigMap), e o remédio é de plataforma: restringir quem altera env do
   worker e alertar sobre essas duas linhas de log. **Decisão do dono/Security.** Os testes NÃO
   usam o override (usam o parâmetro `path` explícito), então ele permanece uma superfície
   exclusivamente de deployment.
2. **CODEOWNERS aqui é LISTAGEM, não portão.** O `main` deste repositório **não tem proteção
   server-side** (branch protection 404, rulesets vazios — verificado contra o remoto; achado do
   dono já registrado na row `mzo-000` do `docs/evidence-ledger.md`). Logo a entrada CODEOWNERS do
   manifesto **pede** revisor de segurança e **não consegue exigi-lo**: um push direto em `main`
   pode ratificar sem revisão. O que de fato bloqueia hoje é (a) o **fence test**
   (`tests/unit/sec/test_action_execution_fence.py`, executado por `.github/workflows/ci.yml:61`),
   que quebra se o arquivo for apagado, alterado para não-DRAFT ou tiver bloco preenchido; e (b)
   as regras fail-closed do próprio loader. Restaurar a proteção do `main` (prescrição da ADR-0023)
   é **decisão do dono** e não foi feita aqui.
3. **Degradação por remoção do arquivo em runtime.** O loader falha fechado num manifesto ausente
   retornando `modo: unresolved`, que **não aplica**. Antes do flip isso é idêntico a hoje; DEPOIS
   do flip, apagar o arquivo implantado reverteria silenciosamente para sombra. No repositório o
   buraco está fechado por `test_shipped_manifest_exists_and_is_wellformed`: **é o FENCE TEST que
   quebra o CI** ao apagar o arquivo (`.github/workflows/ci.yml:61` roda `pytest tests/`) — **não**
   o CODEOWNERS, que aqui só pede um revisor (resíduo 2). O caso runtime-only (editar o ConfigMap
   implantado) **permanece aberto**. Remédio nomeado e NÃO construído aqui: um check de readiness
   em `runtime/worker_runtime/service.py` que recusa o boot quando o manifesto está inutilizável
   ("um PEP que não consegue gatear não deve subir", `gateway/pep.py`). Não foi construído porque
   muda comportamento de boot, e este wave é provadamente inerte. **Decisão do dono/Security.**
4. **Erro interno do gateway degrada para sombra, não para negação.** `evaluate` é total (pura,
   sobre dados congelados já validados) e o loader nunca levanta, então este caminho exige um bug
   de programação. Quando ocorre, `evaluate_worker_task` fixa o VEREDITO em DENY e resolve o modo
   a partir do manifesto já cacheado — então não falha aberto sob `enforcing`. Se nem isso for
   possível, o modo resolve para `unresolved` e o dispatch segue, com log `error` alto. Julgamento
   deliberado: travar toda tarefa externa por um bug do gateway é pior, para uma operadora sob
   prazo ANS, do que uma degradação ruidosa.
5. **Duas classes sem evidência de sombra.** `leitura_phi_clinica` e `delegacao_a2a` estão
   declaradas porque a XRD-09 as nomeia e a nota da ADR-0037 sobre a ADR-0022 exige que o
   `mcp_fhir` roteie por este gateway — mas **nenhuma das duas passa pelo chokepoint atual**.
   Aprová-las hoje seria aprovar sem evidência. Plano de wiring na §7.
6. **Escopo NÃO implementado da XRD-09.** A cláusula pede "política + consent + teto de autonomia +
   audit-before-effect + regras de revisão humana". Este wave entrega **apenas** o eixo de
   aprovação de classe de ação. Consent, teto (`CeilingResolver`) e a composição com o PEP **não**
   estão implementados aqui; o parâmetro `context` de `evaluate()` é o seam que eles usarão. Isto
   está declarado para que ninguém leia o gateway como cobertura completa da XRD-09.
7. **A telemetria não carrega business key** — deliberado, e o contraste explícito com o achado do
   DL-0043 (business keys SÃO logadas em `recurso.py`, e o `LogScrubber` não está ligado). A linha
   `action_execution_gateway_shadow` (ou `..._enforced`, quando a decisão de fato bloqueia) carrega
   apenas `topic`, `action_class`, `decision`, `reason`, `mode`, `tenant` — todos tokens limitados
   e não-PHI.
8. **`tenant` hifenizado colapsa para `INVALIDO` na telemetria — documentado, NÃO alterado.** A
   dimensão `tenant` passa pelo mesmo regex de token limitado do resto da linha
   (`^[A-Za-z][A-Za-z0-9_]{0,39}$`, espelhado de `harness._ENUM_TOKEN_RE`), e o hífen **não está
   nele**. Um deployment cujo tenant id seja `omni-saude` emite `tenant=INVALIDO` em **toda** linha
   do gateway: a telemetria continua não-PHI e continua correta sobre a DECISÃO, mas **não pode ser
   fatiada por tenant** — e, se vários tenants forem hifenizados, todos colapsam no mesmo valor.
   Alargar o regex é decisão de PHI (o que passa a poder viajar em claro num campo de log), não de
   formatação, então o comportamento está **fixado em teste**
   (`test_a_hyphenated_tenant_collapses_to_invalido_in_telemetry`) e divulgado aqui em vez de ser
   mudado por um agente. **Decisão do dono/Security** se o rollout de sombra precisar do corte por
   tenant.
9. **O que a prova de inércia NÃO cobre.** A igualdade da §9 compara `completed` / `failures` /
   `bpmn_errors` / `unlocked` do transporte e `(action, decision, details, dmn_versions)` de cada
   linha de auditoria. Fora do escopo, por omissão consciente: (a) **`transport.extended`** — as
   extensões de lock não entram na comparação (o caminho choked destes fixtures não as exercita,
   mas um dispatch lento em produção as exercita); (b) **as chaves de dedup/idempotência** dos
   registros de auditoria — comparam-se os campos citados, não o hash nem a chave de emissão
   exactly-once; (c) **tempo**. O gateway é inerte em termos de RESULTADO, **não** em termos de
   LATÊNCIA. Medido nesta árvore (`timeit`, 20 000 dispatches, tópico mapeado): **~4 µs** só a
   decisão e **~31 µs por dispatch** com a cadeia real de processors do structlog renderizando —
   ou seja, o custo é dominado pela **linha de log**, não pela avaliação (o GK mediu ~46 µs na
   máquina dele; mesma ordem de grandeza, mesma conclusão). Além disso, no PRIMEIRO dispatch de
   cada processo há um `read_text` **síncrono** do manifesto **dentro do laço de dispatch** — o
   `lru_cache` só paga a partir do segundo. Nada disso altera um resultado observável; tudo isso
   pode aparecer num percentil de latência ou num teste sensível a tempo, e por isso está
   declarado.
10. **Merge keys YAML (`<<: *anchor`) não são suportados pelo loader — MINOR do GK W5, corrigido
    só na telemetria.** `_RefusingDuplicatesLoader.construct_mapping` (`gateway/action_execution.py`)
    varre as chaves brutas do nó ANTES de `flatten_mapping` — o passo do `SafeConstructor` do
    PyYAML que resolve `<<` — rodar, então um merge key bate no mesmo `ConstructorError` ("no
    constructor for this tag") de uma tag genuinamente desconhecida — mas o documento em si é YAML
    bem-formado, não malformado. `_parse` agora reconhece esse caso especificamente (pela tag
    `tag:yaml.org,2002:merge` no `.problem` da exceção) e devolve a razão DEDICADA
    `merge_key_unsupported`, nunca mais o `invalid_yaml` genérico — para que um futuro ratificador
    que tente usar âncoras no manifesto receba uma mensagem honesta ("inline os valores") em vez de
    caçar um erro de sintaxe que não existe. Comportamento fail-closed **inalterado**: `<<` continua
    recusado, `_EMPTY_APPROVALS` continua o resultado; só o token de razão na linha de log
    `action_approvals_manifest_unavailable` ficou mais específico. Ampliar o loader para de fato
    RESOLVER `<<` continua fora de escopo desta correção — mesma postura da nota de SCOPE na
    docstring de `_RefusingDuplicatesLoader` (a classe de achado do `auth_criteria` também não foi
    estendida aqui).
    **Precisão do discriminador (V3 GK REVISE):** o corte (`_parse`, `gateway/action_execution.py:406`)
    é um teste de SUBSTRING sobre `exc.problem` (`_MERGE_KEY_TAG in exc.problem`) — narrowed by tag
    substring, não comprovadamente exclusivo do uso de `<<`. Um valor explicitamente marcado
    `!!merge` (nunca usado como chave `<<`, logo nunca um merge de fato) carrega a MESMA tag
    `tag:yaml.org,2002:merge` e também recebe `merge_key_unsupported` — an explicitly merge-tagged
    value also reports this reason; fail-closed identical in both cases (nada observável regride:
    `degraded`/`unresolved`/nada aprovado nos dois casos). Isto é lacuna de PRECISÃO DOCUMENTAL (a
    afirmação anterior de que o token "ficou preciso" superestimava o corte), não comportamental —
    fixado em teste dedicado (`test_a_merge_tagged_value_also_gets_merge_key_unsupported`,
    `tests/unit/gateway/test_action_execution_gateway.py`), documentando a colisão, não a
    corrigindo.

---

## 7. Plano de wiring para as superfícies ainda não choked

Não construído neste wave (controle de escopo: o diff de runtime foi mantido mínimo e em forma de
seam). Cada item abaixo é um work package próprio:

- **`leitura_phi_clinica`** — `FhirServer.read_resource`/`search_resources`
  (`tools/mcp_fhir/server.py:88`/`:118`). Chamar `evaluate_worker_task`-equivalente no topo de cada
  método. É a superfície que a nota da ADR-0037 sobre a ADR-0022 nomeia explicitamente.
- **`delegacao_a2a`** — `A2ADispatcher.delegate` (`a2a/dispatcher.py:277`), junto ao
  `_audit_delegation` que já existe ali.
- **`comunicacao_beneficiario` (perna WhatsApp)** — `WhatsAppServer.send_message`
  (`tools/mcp_whatsapp/server.py:111`).
- **`inicio_processo_regulatorio` (perna agente)** — `start_process_idempotent`
  (`tools/mcp_cibseven/transport.py:1052`), ao lado do fence de allowlist ADR-0016 já existente.
- **Chamada de tool do grafo do agente** — **não existe chokepoint por-chamada** hoje: os agentes
  chamam os transports diretamente dentro dos nós do grafo (`runtime/harness.py:166-179`), que é
  precisamente o fato 2 da ADR-0034. Um gate genérico de tool-dispatch exigiria introduzir esse
  chokepoint primeiro — trabalho de arquitetura, não de wiring.

---

## 8. Blocos de aprovação

Os blocos vivem em `spec/policies/autonomy/action-approvals.yaml`, não aqui — para que exista **um
único** artefato ratificável e nenhuma cópia possa divergir dele. Este documento é a evidência; o
YAML é o registro.

| Domínio | Aprovador | Data | Referência de evidência | Estado |
|---|---|---|---|---|
| Médica | PENDENTE | PENDENTE | PENDENTE | **ABERTO** |
| ANS | PENDENTE | PENDENTE | PENDENTE | **ABERTO** |
| Security | PENDENTE | PENDENTE | PENDENTE | **ABERTO** |

---

## 9. Verificação (o que foi realmente executado)

Números do **build W5 + reparo pós-gatekeeper** (2026-08-09, rodados nesta árvore):

- `uv run python -m pytest tests/unit -q` — **5111 passed, 41 skipped**, medido nesta árvore após o
  reparo. O build W5 reportava 5081 → **+30 testes** no reparo (contagem conferida uma a uma).
- `uv run ruff format --check src tests` — 409 arquivos, limpo. `uv run ruff check src tests` — limpo.
- `uv run mypy src` — **Success: no issues found in 179 source files** (strict).
- `make validate-artifacts` — **OK, 0 errors**.
- `make check-bpmn-error-allowlist` — **PASS** (3 warnings dead-model pré-existentes, inalterados).
- `make check-start-process-fence` — **PASS** (179 arquivos).
- **NÃO provado em engine ao vivo** por nenhum dos autores.

**Não-vacuidade das cercas novas do reparo**, provada por 4 mutantes dirigidos (revertidos): remover
a igualdade de conjunto de `dominios_exigidos` → 6 falhas; remover a recusa de enforcement do
override → 10 falhas; fixar o nome do evento em `..._shadow` → 1 falha; voltar a `yaml.safe_load` →
3 falhas.

**Prova de inércia** (`tests/unit/gateway/test_action_execution_gateway.py`): o caminho choked é
executado com o gateway vivo e com ele **removido do caminho**, e os resultados observáveis —
`complete`/`failure`/`bpmnError`/`unlock` do transporte, mais `(action, decision, details,
dmn_versions)` de cada linha de auditoria — são comparados por igualdade. Um segundo teste inspeciona
a decisão diretamente (DENY, não aplicada), porque um gateway inerte e um gateway ausente são
indistinguíveis do transporte, e sem isso a prova de inércia passaria também se o wiring tivesse
virado no-op. **O que essa igualdade NÃO cobre está no resíduo 9 da §6** (`transport.extended`,
chaves de dedup, tempo).

**Prova de alcançabilidade por DADO** (`test_enforcement_is_reachable_from_data_alone_and_refuses_real_tasks`):
a afirmação central deste pacote — "ratificar é uma mudança de DADOS e nada mais" — é provada a
partir do dado, não de um veredito injetado. O único insumo é o ARQUIVO de manifesto
(`status: RATIFICADO` + `modo: enforcing`, blocos ainda não assinados), o loader real o interpreta,
o `evaluate` real decide, e **duas tarefas reais em dois tópicos diferentes** são recusadas com a
forma de guard prescrita (handler nunca executa, `retries=0`, linha de auditoria `REFUSED` com
`guard_code=ERR_ACTION_GATEWAY_NOT_HUMAN`). O controle correspondente
(`test_the_same_data_in_shadow_leaves_both_tasks_untouched`) usa o manifesto **idêntico** com
`modo: shadow` e vê as duas tarefas completarem — isolando o único campo que carrega o interruptor.

---

## 10. Onda 1 — Pacote de evidência de sombra (§9.2 do design; estrutura preparada, evidência de telemetria PENDENTE de deployment)

**O que esta seção é.** Estende este pacote conforme `docs/design/wave1-effect-chokepoint.md` §9.2
("Phase 1 — the shadow evidence packet", `:781-800`), agora que a Onda 1 Fase 0 — o chokepoint
por-chamada INERTE: núcleo de decisão (`gateway/effect_pep.py`), catálogo fechado
(`gateway/effect_classes.py`), registry + sete seams (`gateway/tool_registry.py`,
`gateway/seams/*.py`) e cerca CI estática (`scripts/ci/check_effect_chokepoint_fence.py`) — está
construída e mergeada em `main` (`29763e7`). Ela prepara a ESTRUTURA que o §9.2 pede, per classe de
ação, separando explicitamente o que é DERIVÁVEL ESTATICAMENTE hoje (contra a árvore e a suíte de
testes desta sessão) do que só existe depois de um deployment real em modo `shadow` observando
tráfego de produção.

**O que esta seção NÃO é.** Não é um bloco de aprovação, não contém linguagem de atestação e não
recomenda nada a ninguém — a §8 deste documento continua sendo o único lugar onde um `aprovado` pode
virar `true`, e nenhum campo lá foi tocado por esta seção. Onde o conteúdo depende de telemetria que
ainda não existe — porque não há hoje nenhum deployment rodando em modo shadow —, o campo carrega o
placeholder honesto abaixo, verbatim, e nunca um número, uma janela ou uma alegação de tráfego
inventados:

> **SEM EVIDÊNCIA DE SOMBRA — requer deployment em modo shadow; preenchido por observação, nunca
> por agente.**

Sete subseções, uma por item do design §9.2, fechando com a mecânica de virada (§9.4) e o que
transforma cada PENDENTE em evidência real.

### Item 1 do design §9.2 — Janela de observação e volume

Placeholder por classe (contagem de linhas por `decision` × `reason` × `tenant`):

| Classe | Rung | Janela de observação | Volume (WOULD_ALLOW / WOULD_DENY por camada) |
| --- | --- | --- | --- |
| avaliacao_dmn | C0 | PENDENTE¹ | PENDENTE¹ |
| consulta_processo | C0 | PENDENTE¹ | PENDENTE¹ |
| comunicacao_beneficiario | C1 | PENDENTE¹ | PENDENTE¹ |
| leitura_phi_clinica | C2 | PENDENTE¹ | PENDENTE¹ |
| leitura_populacional | C2 | PENDENTE¹ (sem cliente injetado — ver Item 6) | PENDENTE¹ |
| inferencia_llm | C2 | PENDENTE¹ | PENDENTE¹ |
| inicio_processo_regulatorio | C3 | PENDENTE¹ (perna agente deliberadamente não-choked — ver Item 6) | PENDENTE¹ |
| correlacao_processo | C3 | PENDENTE¹ | PENDENTE¹ |
| delegacao_a2a | C3 | PENDENTE¹ | PENDENTE¹ |
| autorizacao_emissao | C4 | PENDENTE¹ | PENDENTE¹ |
| negativa_notificacao | C4 | PENDENTE¹ | PENDENTE¹ |
| submissao_regulatoria_ans | C4 | PENDENTE¹ | PENDENTE¹ |
| pagamento_emissao | C4 | PENDENTE¹ | PENDENTE¹ |
| vinculo_contratual_mudanca | C4 | PENDENTE¹ | PENDENTE¹ |
| acusacao_fraude_registro | C4 | PENDENTE¹ | PENDENTE¹ |

¹ SEM EVIDÊNCIA DE SOMBRA — requer deployment em modo shadow; preenchido por observação, nunca por
agente.

**O que É estático hoje: os nomes de evento e o conjunto exato de campos que um operador vai
agregar.**

Perna de worker (`gateway/action_execution.py`) — um evento por dispatch, emitido por
`_log_decision` (`:1130-1143`), chamado de dentro de `evaluate_worker_task` (`:1076-1127`):

- Evento: `action_execution_gateway_shadow` (não-enforçante) ou `action_execution_gateway_enforced`
  (enforçante) — literais `EVENT_SHADOW` / `EVENT_ENFORCED` (`action_execution.py:1072-1073`).
- Campos: `topic`, `action_class`, `decision` (`WOULD_ALLOW`/`WOULD_DENY`), `reason`, `mode`,
  `enforcement`, `tenant` — todos tokens limitados, ou o fallback (`TOPICO_INVALIDO`/`NAO_MAPEADA`/
  `INVALIDO`) quando o valor bruto falha o regex.

Perna de agente/seam (`gateway/effect_pep.py::log_effect_decision`, `:928-953`) — os MESMOS dois
nomes de evento, reaproveitados deliberadamente ("a second event family would fragment exactly the
evidence §9.2 asks the approvers to read", `effect_pep.py:934-936`). Campo set PINADO em ambas as
direções por `tests/unit/gateway/seams/test_seam_proofs.py::
test_every_gated_call_emits_exactly_one_bounded_shadow_line` (`:489-512`), contra a tabela
`_EXPECTED_TELEMETRY_FIELDS` (`:467-485`): `event`, `log_level`, `topic`, `operation`, `action_ref`,
`principal`, `action_class`, `decision`, `reason`, `layer`, `denial_shape`, `mode`, `enforcement`,
`phi_zone`, `tenant` — 15 campos, exatamente um por chamada gateada (o mesmo teste prova a contagem
exata, não só o conjunto).

Um operador agrega por `decision` × `reason` × `tenant` (a chave que o item 1 pede); a perna de
agente acrescenta `operation`/`layer`/`denial_shape`/`principal`/`phi_zone` como dimensões NOVAS,
nunca substituindo as antigas — a mesma disciplina do #222 (campo pinado nos dois sentidos).

### Item 2 do design §9.2 — A lista would-deny

Placeholder — quais chamadas reais teriam sido bloqueadas, e em qual camada:

> SEM EVIDÊNCIA DE SOMBRA — requer deployment em modo shadow; preenchido por observação, nunca por
> agente. Nenhuma lista real pode existir hoje: a Onda 1 Fase 0 aterrissou em `29763e7` e este
> pacote é escrito na mesma sessão — não há histórico de tráfego contra o chokepoint de agente.

**A taxonomia de camadas que um aprovador vai ler É estática hoje** — re-derivada de `EffectLayer`
(`gateway/effect_pep.py:201-210`) e do vocabulário de razão fechado (`:213-229`, mais o passthrough
de `action_execution.py:187-204` na camada L5):

| Camada | O que testa | Razões (tokens fechados) |
| --- | --- | --- |
| `L_ENTRADA` | a própria chamada/gateway (falha atribuível a nenhuma camada específica) | `CHAMADA_INVALIDA`, `GATEWAY_ERRO_INTERNO` |
| `L0_CATALOGO` | a operação existe no catálogo fechado (`effect_classes.OPERATIONS`) | `OPERACAO_DESCONHECIDA` (perna agente) / `ACAO_NAO_MAPEADA` (perna worker, `action_execution.py:190`) |
| `L1_CAPACIDADE` | o agente declarou a tool / a `process_key` (`agent.yaml`) | `CAPACIDADE_INDISPONIVEL`, `TOOL_NAO_DECLARADA`, `PROCESS_KEY_NAO_PERMITIDA` |
| `L2_AUTONOMIA` | a matriz de autonomia (`pep.PEP.evaluate`) | `VOCABULARIO_PENDENTE`, `POLITICA_INDISPONIVEL`, `AUTONOMIA_NEGADA`, `HUMANO_REQUERIDO` |
| `L3_TETO` | teto de valor (`CeilingResolver`) — INERTE hoje, nenhuma operação catalogada declara teto | `TETO_EXCEDIDO` |
| `L4_CONSENTIMENTO` | consentimento (`ConsentDecisionSource`) — INERTE hoje, nenhuma classe exige (Q-5) | `CONSENTIMENTO_AUSENTE` |
| `L5_RATIFICACAO` | o gate humano MZO-040 (`ActionExecutionGateway.evaluate`, inalterado) | `MANIFESTO_INDISPONIVEL`, `MANIFESTO_NAO_RATIFICADO`, `ACAO_NAO_DECLARADA`, `DOMINIOS_EXIGIDOS_VAZIO`, `DOMINIO_DESCONHECIDO`, `DOMINIOS_INCOMPLETOS`, `APROVACAO_PENDENTE`, `APROVACAO_INCOMPLETA`, `OVERRIDE_NAO_ENFORCAVEL` |

A distinção crucial que o design exige de um aprovador (`effect_pep.py:102-105`): **`APROVACAO_
PENDENTE` (L5) significa "nenhum humano assinou esta classe ainda"; `TOOL_NAO_DECLARADA` (L1)
significa "este agente nunca foi permitido a fazer isto"**. Hoje, com as 15 classes todas `aprovado:
false`, toda chamada real que chegasse à L5 pararia em `APROVACAO_PENDENTE` — mas a lista would-deny
do item 2 é sobre em qual camada CADA chamada individual pararia (algumas nunca chegam à L5 porque
uma camada anterior já nega — p.ex. um agente sem a tool declarada nega em L1), e isso só se sabe
observando tráfego real.

### Item 3 do design §9.2 — Censo de refs não-mapeadas

**Estático, derivado nesta sessão contra este worktree.** Script (reproduzível por qualquer revisor
no mesmo commit, usando as mesmas peças que `tests/unit/tools/workers/test_bootstrap_registration.py`
já exercita):

```python
from maezo.tools.workers.bootstrap import register_all_workers
from maezo.tools.workers.harness import FakeKafkaPublisher, FakeWorkerTransport, WorkerHarness
import yaml

harness = WorkerHarness(FakeWorkerTransport(), worker_id="census-probe")
register_all_workers(harness, kafka=FakeKafkaPublisher())
registered = set(harness.registered_topics)

manifest = yaml.safe_load(open("spec/policies/autonomy/action-approvals.yaml"))
mapped = set(manifest["mapeamento_topicos"].keys())

unmapped = sorted(registered - mapped)
```

Resultado (**re-derivado 2026-09-03**, ADR-0040 PR-3+PR-4): **124 tópicos registrados**
(`WorkerHarness.registered_topics`, via os 17 bootstraps de
`tools/workers/bootstrap.py::register_all_workers`), **30 mapeados** (`mapeamento_topicos`), **0
mapeados-mas-não-registrados** (nenhum drift entre o manifesto e o registry vivo), **94 tópicos
registrados sem entrada em `mapeamento_topicos`**.

> **Por que 98 → 94, sem que nenhum tópico tenha deixado de existir por acidente.** A correção de
> perspectiva das cadeias CONTAS/RECURSO (ADR-0040, **Proposed — não ratificada**) trocou o
> vocabulário de seis superfícies e **classificou** as novas em `mapeamento_topicos`: saíram da
> lista de trabalho `operadora.recurso.registrar_indeferimento`,
> `operadora.recurso.comunicar_resposta`, `operadora.recurso.handoff_pagamento`,
> `operadora.contas.registrar_glosa`, `operadora.contas.emitir_demonstrativo` e
> `operadora.contas.handoff_pagamento`. Foram REMOVIDOS do registry — porque os atos que nomeavam
> eram do **recorrente**, não do pagador — `operadora.recurso.submit_appeal`,
> `operadora.recurso.track_status`, `operadora.recurso.reconcile_payment`,
> `operadora.recurso.register_desistencia`, `operadora.contas.start_recurso` e
> `operadora.contas.reconcile_payment`. **Nada aqui ratifica MZO-040**: `status: DRAFT`,
> `modo: shadow` e todos os blocos `aprovacoes` permanecem intocados.

A cláusula que rege esses 94 já existe no próprio manifesto, no ponto que este pacote cita sem
tocar (`action-approvals.yaml:563-566`):

> "DELIBERADAMENTE INCOMPLETO. Só estão mapeados os tópicos cujo efeito externo é inequívoco a
> partir do próprio worker... Os ~80 tópicos restantes... NÃO estão mapeados, e classificá-los é
> decisão humana, não inferência de agente."

O número real hoje (94) é maior que a estimativa de prosa "~80" que o próprio manifesto carrega —
cresceu porque mais workers foram registrados desde que aquela frase foi escrita. Isto não é uma
contradição a corrigir: a frase nunca prometeu um número exato, e o script acima é a fonte da
verdade reproduzível, não a prosa.

**A lista de trabalho** (94 tópicos, agrupados por domínio; cada um hoje classificado sob
`enforcement_padrao_nao_mapeado: shadow`, portanto já emitindo uma linha `ACAO_NAO_MAPEADA` por
dispatch assim que um deployment shadow rodar contra tráfego real):

| Domínio | Contagem | Tópicos (sufixo, sem o prefixo do domínio) |
| --- | --- | --- |
| `operadora.adequacao.*` | 7 | calculate_gap, measure_coverage, notify_rede, notify_sla_risk, prepare_remediation_dossier, register_fallback_commitment, update_monitoring_plan |
| `operadora.ans_cron.*` | 2 | check_calendar, trigger_submissions |
| `operadora.auth.*` | 5 | analyze_request, convene_junta, notify_sla_risk, request_documents, validate_auto_criteria |
| `operadora.cancel.*` | 5 | confirm_maintained_decision, notify_sla_risk, prepare_dossier, request_notification, resolve_facts |
| `operadora.contas.*` | 7 | analyze_reason, calculate_impact, devolver_conta, identify_glosa, notify_sla_risk, prepare_triage_dossier, publish |
| `operadora.cred.*` | 8 | check_network_criteria, check_prior_notice, notify_doc_pendente, notify_sla_risk, prepare_dossier, register_cred_denial, register_credenciamento, verify_credentials |
| `operadora.escalation.*` | 2 | notify_supervisor, notify_team |
| `operadora.events.*` | 1 | publish |
| `operadora.fraude.*` | 7 | assemble_dossier, gather_evidence, intake, notify_sla_risk, publish_completed, score_indicators, seal_custody_bundle |
| `operadora.inadimplencia.*` | 6 | assess_status, calculate_purge, check_prior_notice, notify_sla_risk, prepare_dossier, resolve_facts |
| `operadora.lgpd.*` | 6 | execute_erasure, execute_export, execute_rectification, notify_sla_risk, publish_completed, verify_identity |
| `operadora.nip.*` | 3 | instruct_dossier, notify_deadline_risk, publish_completed |
| `operadora.pagto.*` | 7 | assess_admissibility, calculate_facts, notify_sla_risk, prepare_approval_dossier, publish_completed, register_payment_refusal, validate_payment_data |
| `operadora.programa.*` | 7 | build_care_plan, check_consent, monitor_programa, notify_sla_risk, register_program_discharge, stop_processing, stratify_risk |
| `operadora.recurso.*` | 9 | analyze_request, assess_eligibility, escalate_ans_timeout, escalate_to_junta, notify_sla_risk, prepare_dossier, publish_completed, request_documents, validate_recurso |
| `operadora.reembolso.*` | 7 | analyze_request, calculate_amount, check_coverage, check_prazo, notify_sla_risk, publish_completed, request_documents |
| `regulatorio.anssubmit.*` | 5 | assemble, notify_regulatorio, publish_completed, track_protocol, validate |
| **Total** | **94** | |

**Os 12 tópicos vivos de `operadora.recurso.*` depois de ADR-0040** (os 9 acima + os 3 que passaram
a ser mapeados): `analyze_request`, `assess_eligibility`, `comunicar_resposta`,
`escalate_ans_timeout`, `escalate_to_junta`, `handoff_pagamento`, `notify_sla_risk`,
`prepare_dossier`, `publish_completed`, `registrar_indeferimento`, `request_documents`,
`validate_recurso`. Os 11 vivos de `operadora.contas.*`: os 7 acima + `registrar_glosa`,
`emitir_demonstrativo`, `handoff_pagamento`, `start_fraude`.

**Um segundo censo, menor e já FECHADO por construção** (não é trabalho pendente; citado para
completude): a perna `mapeamento_acoes` (agent-side) tem `16/16` operações do catálogo
`effect_classes.OPERATIONS` mapeadas — 0 não-mapeadas (`action-approvals.yaml:627-651`, verificado
`len(mapeamento_acoes) == len(effect_classes.OPERATIONS) == 16` nesta sessão). O catálogo fechado e
o mapa de roteamento agente nasceram juntos nesta Onda, então não há um segundo backlog de
classificação do lado do agente. O único gap conhecido do lado do agente é `mcp-memory.read_write`
— declarado por todo `agent.yaml` mas sem operação catalogada (disclosed no próprio docstring de
`effect_classes.py`, "KNOWN GAP", `:38-47`; testado por
`tests/unit/gateway/test_effect_enforcement.py::
test_the_memory_tool_gap_is_recorded_not_silently_catalogued`) — a mesma classe de decisão humana
que os 94 tópicos acima, não um achado novo desta seção.

### Item 4 do design §9.2 — Forma de recusa declarada por classe + ponteiro para o teste de mutação

**Totalmente estático hoje.** As 15 classes, pinadas byte-a-byte por um único teste de dicionário
(não um loop por linha, que perderia uma classe removida em silêncio):
`tests/unit/gateway/test_effect_enforcement.py::
test_every_class_carries_the_exact_rung_and_denial_shape_design_6_1_assigns` (`:862-880`) — nota de
re-derivação: o brief de execução desta PR sugeriu `test_effect_pep.py` como o arquivo; re-derivado
contra a árvore, o teste vive em `test_effect_enforcement.py` (`test_effect_pep.py` existe e cobre a
escada L-0..L-5 em si, não a tabela §6.1 classe→rung→forma).

| Classe | Rung | Forma de recusa declarada | Prova ao vivo per-classe (seam-level) |
| --- | --- | --- | --- |
| avaliacao_dmn | C0 | `ROTA_DMN_INDISPONIVEL` | `test_seam_proofs.py::test_dmn_denial_lands_on_the_nodes_declared_dmn_unavailable_path` (`:563-584`) |
| consulta_processo | C0 | `LEITURA_INCONCLUSIVA` | `test_seam_proofs.py::test_engine_denial_raises_and_is_never_readable_as_no_active_instance` (`:596-617`, parametrizado ×4: `find_active_instance`/`find_any_instance`/`get_process_status`/`correlate_message`) |
| comunicacao_beneficiario | C1 | `ESCALONAMENTO_HUMANO` | `test_seam_proofs.py::test_the_remaining_seams_refuse_in_their_declared_shape[whatsapp]` (`:620-648`) |
| leitura_phi_clinica | C2 | `LACUNA_DECLARADA` | idem, `[fhir]` |
| leitura_populacional | C2 | `LACUNA_DECLARADA` | idem, `[population]` |
| inferencia_llm | C2 | `ROTA_LLM_INDISPONIVEL` | idem, `[inference]` |
| inicio_processo_regulatorio | C3 | `INCIDENTE_FALHA_FECHADA` | perna agente deliberadamente não-choked (Item 6); mecanismo genérico da perna worker abaixo |
| correlacao_processo | C3 | `INCIDENTE_FALHA_FECHADA` | `test_engine_denial_raises_...` acima (mesma parametrização, `correlate_message`) |
| delegacao_a2a | C3 | `DEGRADACAO_SEM_DOSSIE` | idem, `[a2a]` |
| autorizacao_emissao | C4 | `INCIDENTE_FALHA_FECHADA` | sem seam de agente (tópico-de-worker-só); mecanismo genérico abaixo |
| negativa_notificacao | C4 | `INCIDENTE_FALHA_FECHADA` | idem |
| submissao_regulatoria_ans | C4 | `INCIDENTE_FALHA_FECHADA` | idem |
| pagamento_emissao | C4 | `INCIDENTE_FALHA_FECHADA` | idem |
| vinculo_contratual_mudanca | C4 | `INCIDENTE_FALHA_FECHADA` | idem |
| acusacao_fraude_registro | C4 | `INCIDENTE_FALHA_FECHADA` | idem |

**Prova do mecanismo genérico da perna worker** (usada por `inicio_processo_regulatorio` e pelas 6
classes C4, todas mapeadas só por tópico de external-task):
`tests/unit/gateway/test_action_execution_gateway.py::
test_enforcement_is_reachable_from_data_alone_and_refuses_real_tasks` (`:1015-1059`) — prova o
MECANISMO (manifesto ratificado+enforcing como ARQUIVO real → handler nunca roda, `retries=0`,
linha de auditoria `REFUSED` com `guard_code=ERR_ACTION_GATEWAY_NOT_HUMAN`) contra dois tópicos
SINTÉTICOS, não contra os tópicos reais dessas 7 classes especificamente. **Divulgado, não
escondido:** não existe hoje um teste de mutação POR CLASSE, usando o tópico REAL de
`autorizacao_emissao`/etc., que prove a forma de recusa daquela classe especificamente — a prova é
do mecanismo compartilhado (`evaluate_worker_task` / `_log_decision` / `WorkerHarness._handle`), que
é o MESMO código para as 26 classes mapeadas; uma prova por tópico adicional acrescentaria cobertura
de ASSERÇÃO POR CLASSE, não cobertura de código nova. Fechar esse gap fino (adicionar os 6 tópicos
reais à parametrização existente) é trabalho barato e agent-executável, mas está fora do escopo
ADD-ONLY desta PR (o gap vive em código de teste já existente, não neste documento).

**Provas de forma de recusa adicionais**, estruturais e cobrindo as 15 classes de uma vez:
`test_seam_proofs.py::test_no_payload_prompt_recipient_or_patient_id_ever_reaches_a_telemetry_line`
(`:515-532`, I-3 na linha de telemetria) e `test_a_denial_message_carries_only_bounded_tokens`
(`:651-663`, I-3 na mensagem de exceção que os grafos interpolam em notas de lacuna).

### Item 5 do design §9.2 — Prova de defesa-em-profundidade C4

**Lado A — os guards L0-hard existem e são testados INDEPENDENTEMENTE do PEP. PROVADO hoje.** Três
exemplos concretos, re-derivados — nenhum dos três arquivos de teste abaixo importa `effect_pep` ou
`ActionExecutionGateway` (contagem `grep -c` = 0 nos três): os guards não apenas "ainda recusam com
o PEP neutralizado", eles nunca sabem que o PEP existe.

1. `ERR_ANS_SUBMIT_NOT_HUMAN` (guard `_require_human_approval`, `tools/workers/ans_submit.py:344-360`;
   exceção `AnsSubmitNotHumanError`, `:101-114`) — cobre `submissao_regulatoria_ans`. Provado
   independente em `tests/unit/tools/workers/test_ans_submit.py::
   test_guard_fires_before_gateway_even_with_refusing` (`:689-699` — o "gateway" no nome é o
   TRANSPORTE ANS, `RefusingAnsGatewayTransport`, não o PEP/`ActionExecutionGateway`; o teste prova
   que o guard dispara ANTES de qualquer transporte ser consultado) e
   `test_ans_submit_not_human_is_permission_error` (`:790-792`).
2. `ERR_FRAUD_ACCUSATION_NOT_HUMAN` (`tools/workers/fraude.py:47`) — cobre `acusacao_fraude_registro`.
   Provado em ~13 testes de `tests/unit/tools/workers/test_fraude.py`
   (p.ex. `test_fraud_accusation_guard_rejects_arquivar`, `:494-510`), nenhum referenciando o PEP.
3. `ERR_DENIAL_NOT_HUMAN` (`tools/workers/base.py:32`; levantado em `tools/workers/auth.py:1228,
   :1242` dentro de `SendDenialNoticeWorker`) — cobre `negativa_notificacao`. Provado em
   `tests/unit/tools/workers/test_auth_denial_guard.py::
   test_send_denial_notice_guard_prevents_automatic_denial` (`:144-165`), idem.

Um quarto exemplo, fora das 6 classes C4 mas da MESMA família: `ERR_FALLBACK_COMMITMENT_NOT_HUMAN`
(`tools/workers/adequacao.py:44`, cobrindo `operadora.adequacao.register_fallback_commitment`, um
dos 94 não-mapeados do Item 3), provado em
`tests/unit/tools/workers/test_adequacao.py::test_fallback_commitment_rejects_wrong_decisao`
(`:1146-1157`), citado porque a mesma disciplina de independência se estende a toda a família
`ERR_*_NOT_HUMAN`, não só às 6 classes C4.

**Lado B — a prova DE-DOIS-LADOS por classe (negado→recusa E PEP-neutralizado→guard-ainda-recusa)
é uma PRECONDIÇÃO DE VIRADA, per design §6.1 C4, e está PENDENTE.** Duas razões concretas:

- A prova de paridade gated-vs-neutralized que a Onda 1 Fase 0 entregou
  (`test_seam_proofs.py::test_parity_gated_vs_neutralized_under_the_shipped_manifest`, `:420-439`,
  e as demais provas (A)/(D) do arquivo) cobre os SETE seams de agente (`fhir`, `whatsapp`, `dmn`,
  `cibseven`, `inference`, `population`, `a2a`). NENHUMA das 6 classes C4 tem seam de agente — são
  worker-topic-only por desenho (`effect_classes.py:204-206`: "registered so the denial-shape
  contract... covers every class the manifest declares, not only the ones an agent seam can
  reach"). Não existe hoje um "PEP-neutralized" para comparar nessas 6, porque não existe um
  "PEP-live" em nível de seam para elas em primeiro lugar — a única perna que as toca é
  `evaluate_worker_task` / `WorkerHarness._handle`, que já é o MZO-040 original, inalterado por
  esta Onda.
- O Lado A prova que os guards recusam SEM o PEP. Ele não prova, para cada uma das 6 classes C4
  especificamente, que uma NEGAÇÃO do PEP (sob um manifesto de teste que efetivamente enforça
  aquela classe) produz a `INCIDENTE_FALHA_FECHADA` declarada usando o TÓPICO REAL daquela classe —
  essa é exatamente a lacuna fina já divulgada no Item 4.

Portanto: **defesa-em-profundidade C4 = Lado A feito, Lado B pendente**, e o design é explícito que
essa combinação é intencional até a classe se aproximar da rampa: a prova de-dois-lados per-classe
é uma precondição de virada per §6.1 C4, marcada pendente até o rung C4 se aproximar do ato de
virada (§9.4).

### Item 6 do design §9.2 — Resíduos divulgados, nomeados

Estático, sem depender de telemetria — nove resíduos, cada um já vivo no código ou no manifesto,
reunidos e re-derivados linha-a-linha nesta sessão:

1. **C-B2 — cliente cru local-ao-nó evade a asserção de seam.** Um autor determinado ainda pode
   construir um cliente `httpx` novo dentro do corpo de um nó e pular o registry inteiramente.
   Compensação em duas camadas: (a) a cerca AST §8.1
   (`scripts/ci/check_effect_chokepoint_fence.py`, `FORBIDDEN_CONSTRUCTION_NAMES`) rejeita a
   construção NOMEADA das classes cruas fora do registry; (b) a asserção de boot
   `tool_registry.effect_seams_gated` (`:479-509`) pega qualquer coisa que de fato chegue a um dep
   map de composição — mas nenhuma das duas fecha a indireção (item 5 abaixo). Disclosed pela
   própria design (§4, C-B2) e re-confirmado pelo docstring da cerca.
2. **A-10 — `lru_cache` faz o rollback mais lento que o incidente.** `action_approvals`
   (`action_execution.py:964-972`) cacheia por processo via `_load_cached`
   (`@lru_cache(maxsize=8)`, `:959-961`). Uma ratificação — ou um rollback — só vale no próximo
   restart do daemon. Q-7 (design §10) permanece em aberto: aceitar "rollback = restart" ou
   construir uma releitura TTL fail-closed. Nenhuma das duas foi decidida ou construída nesta Onda.
3. **XRD-10/MZO-060 — janela de atomicidade claim-vs-start.** `start_process_idempotent`
   (`tools/mcp_cibseven/transport.py:1069`) escreve o claim ADR-0007 ANTES do start do engine, mas
   os dois não compartilham transação — a janela é real e DIVULGADA, não fechada, na própria
   docstring da função (`RESIDUAL, recorded not hidden`, `:1129-1133`, re-derivado nesta sessão — a
   citação do design original, `:1112-1117`, ficou stale porque a função cresceu; esta seção corrige
   a citação só para seu próprio uso, sem tocar nenhuma linha existente deste pacote).
4. **`MAEZO_SPEC_DIR` — pin PROVISÓRIO, pendente Q-6.** O pin (`action_execution.py:719-733`,
   ligado por `spec_dir_sourced`/`spec_dir_override` em `:905-916`) implementa a forma FRACA: um
   manifesto resolvido via `MAEZO_SPEC_DIR` em runtime de produção é AVALIADO mas nunca ENFORÇA
   sozinho (`mode = MODE_SHADOW_OVERRIDE`, espelhando o override de path já existente) — nunca
   refuse-to-load. A própria docstring do pin nomeia isso "PROVISIONAL... Q-6 may STRENGTHEN it to
   refuse-to-load... belongs to the owner" (`:726-733`).
5. **Limite de indireção do AST (§8.1), divulgado no próprio docstring da cerca.**
   `scripts/ci/check_effect_chokepoint_fence.py:75-83`: a cerca casa NOMES de classe na AST, então
   `_C = InferenceProvider; _C()` ou `getattr(mod, "InferenceProvider")()` constroem a classe
   fenced sem o nome aparecer no call site — o MESMO limite que `check_start_process_fence.py` já
   carrega, pela mesma razão (uma varredura AST não é uma sandbox). O que de fato cobre isso é a
   metade de RUNTIME de I-11 — `tool_registry.effect_seams_gated`, cuja consequência está pinada nas
   quatro raízes de composição por `tests/unit/gateway/seams/test_boot_assertion_wiring.py`.
6. **Padrão `Stub*` omitido do §8.4 por decisão, não por descuido.**
   `check_effect_chokepoint_fence.py:419-427`: o design §8.4 nomeia exatamente `Fake*`/`*Mock*`/
   `Noop*`; o único `Stub*` existente no repo (`StubWorker`,
   `tests/unit/tools/workers/test_worker_registry.py:23`) vive em `tests/`, que esta cerca nunca
   varre — então incluir `Stub*` fencaria zero nomes reais hoje. Um FUTURO `Stub*` de produção não
   seria pego pela cerca ESTÁTICA, mas seria pego em RUNTIME pela mesma `effect_seams_gated` (item 5
   acima).
7. **`consulta_processo` — duas pré-condições de virada, já na própria `descricao` do manifesto**
   (`action-approvals.yaml:453-469`, não tocado por esta PR): (a) resíduo pós-claim —
   `find_active_instance` está gated e é chamada em `transport.py:1182`, DEPOIS de o claim durável
   já ter sido escrito em `:1152-1157` (`gateway/seams/cibseven.py:29-35` documenta o mesmo resíduo
   do lado do seam); numa família ESTRITA, uma negativa ali deixaria um claim sem instância e sem a
   linha `cibseven_start_claim_orphaned` (que só cobre o ramo `start_process_instance`); (b)
   enforcement é POR CLASSE, não por principal — virar `consulta_processo` derruba toda leitura de
   engine dos daemons `worker_runtime`/`notifications_bridge`, que não têm `agent.yaml` e portanto
   negam em L1 com `CAPACIDADE_INDISPONIVEL`. Nenhuma das duas é inferência de agente; ambas exigem
   decisão humana antes da 1ª virada C0.
8. **Duas superfícies `choked: false`, por decisão e não por omissão.** `leitura_populacional`
   segue `choked: false` (`action-approvals.yaml:509-528`) porque o cliente de lago é PORT-PENDING
   (WB.4) — nada é injetado, então nada é observável; virar isso hoje seria alegar evidência
   inexistente (design I-10). `inicio_processo_regulatorio`'s perna agente
   (`action-approvals.yaml:350-356`) segue `choked: false` porque gatear
   `start_process_instance` exigiria mexer nas entranhas de `start_process_idempotent`, proibido
   por design §5.8/I-6 — divulgado em `gateway/seams/cibseven.py:10-26`.
9. **A nota RUNTIME_MODE de ambas-vazias (do dono).** `action_execution.py:524-544` —
   `_is_production_runtime()` lê AMBOS `RUNTIME_MODE`/`AGENT_RUNTIME_MODE` por disjunção (mais
   estrito que o first-set-wins do `key_scrubber`), mas se NENHUMA das duas está declarada, o
   processo lê como `local` e o pin do item 4 acima NÃO arma (`:539-541`: "NOTHING declared reads
   as local, so a production deployment that injects neither variable does not arm the pin... An
   empty string is treated as undeclared"). Residual UNCHANGED, disclosed pelo próprio autor do
   código — decisão de deployment (garantir que todo pod injeta uma das duas variáveis), não de
   código.

### Item 7 do design §9.2 — Pré-condição de governança

**Verbatim-fiel, sem paráfrase que amoleça.** `PLANS.md` §0.8 exige "rulesets/branch protection
**ATIVOS ANTES** do aceite da ratificação" (`PLANS.md:297-298`), "porque o manifesto
`action-approvals.yaml` só é confiável com enforcement server-side" (`:298-299`). A ausência está
registrada na row `mzo-000` de `docs/evidence-ledger.md` (`:169`) e no cabeçalho do próprio
manifesto que este pacote governa (`action-approvals.yaml:51-57`): CODEOWNERS aqui é LISTAGEM, não
portão — `main` não tem proteção server-side ativa.

**A forma executável desta precondição já existe e está RED hoje.**
`.github/workflows/branch-protection-check.yml` (workflow inteiro, adicionado na Onda 0 do
Hardening, achado W6) é o check executável. Verificado nesta sessão via
`gh run list --workflow=branch-protection-check.yml`: as três execuções mais recentes contra `main`
— incluindo a disparada pelo push do próprio merge que traz a Onda 1 Fase 0 (`29763e7`) — são
`completed failure` (runs `31551602014` em `2026-08-12T00:50:31Z`, `31494041279`, `31458638208`).
RED confirmado ao vivo nesta sessão, não apenas por prosa herdada.

**A ação é do dono, não de agente:** ligar uma RULESET (não proteção clássica — o próprio comentário
do workflow explica por que proteção clássica sozinha nunca satisfaz este check) em `main`, exigindo
PR + status checks obrigatórios. Até então, a ratificação de qualquer classe — mesmo com os três
blocos preenchidos — continua sendo, em termos de integridade de processo, um push direto que
ninguém foi OBRIGADO a revisar.

### Mecânica de virada (design §9.4) e o que transforma cada PENDENTE em evidência

Resumo, dado-apenas, humano — nenhum passo abaixo é agent-executável:

1. Cada aprovador preenche o SEU bloco por classe (`aprovado`/`aprovador`/`data`/`evidencia_ref` —
   nunca `PENDENTE`).
2. `status: RATIFICADO` (uma vez, na primeira virada).
3. `modo: enforcing` (uma vez, na primeira virada — é o teto, não o interruptor por classe).
4. `acoes.<classe>.enforcement: enforcing` — o interruptor POR CLASSE, um rung de cada vez, C0 →
   C4.
5. Verificar contra telemetria: o nome do evento tem de virar `..._enforced` e `mode`/`enforcement`
   têm de ler `enforcing` — uma virada malsucedida parece, de fora, um deployment de sombra
   saudável (a mesma armadilha da §4 deste documento).
6. Soak pela janela acordada antes de avançar de rung.
7. Ato terminal: `enforcement_padrao_nao_mapeado: enforcing`, restaurando o XRD-09 literalmente.

**O que transforma cada placeholder desta seção em evidência real:** um deployment em modo
`shadow` rodando contra tráfego de produção pela janela de observação que Médica/ANS/Security
concordarem ser suficiente, e — separadamente — o "vai" do dono para a prova ao vivo do formato de
incidente (design §9.3, `inicio_processo_regulatorio` num CIB Seven local), o único item da Fase 2
que continua agent-executável assim que autorizado. Nenhum dos dois está em curso nesta sessão;
esta seção é a estrutura que os recebe quando existirem, nunca uma antecipação deles.
