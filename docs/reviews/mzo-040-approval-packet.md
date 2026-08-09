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
| `inicio_processo_regulatorio` | Inicia instância SP-OP-* — `contas.py:1055`/`:1061`, `adequacao.py:689`, `fraude.py:1005`/`:1011`, `inadimplencia.py:757`, `nip.py:562`; agente `mcp_cibseven/transport.py:560` | O start do processo | **Sim** (exceto o start por agente) |
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
  (`tools/mcp_cibseven/transport.py:560`), ao lado do fence de allowlist ADR-0016 já existente.
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
