# Memos de decisão do dono — ciclo 2 do fleet hardening (2026-09-05)

**Autor:** `owner-memo-drafter` (agente, R2). Este documento NÃO é uma decisão nem propõe uma
vinculante — é o pacote de fatos verificados + opções que o dono precisa para decidir. Onde a
engenharia tem uma recomendação, ela está marcada como **NÃO VINCULANTE**.
**Base verificada:** `origin/main` `b9cdf245` (worktree `/Users/familia/code/maezo-fleet2-wt/owner-memos`,
branch `fleet2/owner-memos`). Todo comando abaixo foi executado contra esse commit; o log completo
de comandos+saída está no relatório em inglês desta mesma entrega
(`$S/phase4/REPORT-W-OWNER.md`).
**`docs/reviews/` é CODEOWNED** (`.github/CODEOWNERS:276`, `@rodaquino-OMNI @Omni-Saude/security-team`)
— este arquivo, por si só, já torna a PR que o carrega owner-review por construção.

---

## 1. Registro dos handlers A2A em `a2a_composition.py` (marina/beatriz/gustavo/valentina) + `care.enroll`

**Decisão pedida.** Registrar (ou manter não-registrados) os handlers A2A de marina, beatriz,
gustavo e valentina no composition root — incluindo o task type `care.enroll` de valentina — e,
se registrar algum, atualizar a cerca fleet-wide que hoje fixa exatamente esses quatro como
não-registrados.

**Contexto verificado por comando.**
- `grep -n "make_.*_handler" src/maezo/runtime/agent_runtime/a2a_composition.py` (base `b9cdf245`):
  só `make_andre_handler`, `make_carolina_handler`, `make_rafael_handler` são importados/chamados.
  Marina, beatriz, gustavo e valentina **têm** `src/maezo/agents/<agente>/delegation.py` com
  `make_<agente>_handler` pronto (`ls src/maezo/agents/*/delegation.py` lista os 9 módulos, um por
  agente exceto andre/carolina/rafael/fernando/helena que já têm caminho próprio), mas nenhum dos
  quatro é importado no composition root.
- A cerca fleet-wide `tests/unit/a2a/test_agent_card_handlers_parity.py::_UNREGISTERED_HANDLERS`
  fixa, nesta base, `frozenset({"fernando", "marina", "beatriz", "gustavo", "valentina"})` — 5
  agentes, não 4 — como o conjunto exato e executável do gap `FERNANDO-DELEGATION-CALL-SITE`
  ("o registro em `a2a_composition` e o call site de origem são decisão do dono").
- **A PR irmã aberta #342 (`r5/raf02-guards`, branch ainda não mesclada) já registra `fernando`** —
  confirmado por `gh pr diff 342 -- ...a2a_composition.py`: adiciona
  `from maezo.agents.fernando.delegation import make_fernando_handler`, estende
  `_DOSSIER_EDGE_AGENT_IDS` para `("carolina", "andre", "fernando")` e monta
  `fernando_handler = make_fernando_handler(...)`, citando "owner decision R-081 (gap
  `FERNANDO-DELEGATION-CALL-SITE`, approved 2026-09-04: 'SIM — ligar o call site... e registrar
  make_fernando_handler'". A mesma PR edita `_UNREGISTERED_HANDLERS` para
  `frozenset({"marina", "beatriz", "gustavo", "valentina"})` — **fernando sai do conjunto** porque
  registro E call-site de origem (`tools/workers/inadimplencia.py::make_prepare_dossier_handler`)
  chegaram juntos. **Não toquei nem citei conteúdo de #342 além deste diff público — branch
  pertence à sessão irmã.**
- #342 também acrescenta um guard (RAF-02) aos `delegation.py` de gustavo/marina/valentina, mas
  **não os registra** — eles continuam com handler pronto e sem ligação nenhuma no composition
  root após #342.
- Cada um dos quatro `delegation.py` restantes documenta pré-condições de registro que a decisão
  do dono herdaria — ex.: beatriz precisa que o call site de origem carregue `evidencia_refs` por
  um canal capaz de lista ANTES de qualquer registro (hoje `payload_meta` é `Mapping[str, str]`),
  e `gather_evidence`/`assemble_dossier` compartilham um único `task_id`, então o 2º hop é sempre
  um replay do Guard 4, nunca uma segunda execução.

**Opções.**
1. **Manter tudo como está** (nenhum dos quatro registrado). Consequência: `glosa.analyze`,
   `recurso.analyze`, `reembolso.analyze` (marina), `fraude.investigate` (beatriz), `nip.instruct`
   (gustavo) e `care.stratify`/`care.enroll` (valentina) continuam inacessíveis por A2A — os
   workers correspondentes seguem operando standalone, sem o enriquecimento por delegação.
2. **Registrar um subconjunto agora** (espelhando a decisão R-081 que já liberou fernando),
   condicionado às pré-condições específicas de cada agente documentadas em cada `delegation.py`
   (ex.: valentina/gustavo podem estar mais prontos que beatriz, cujo 2º hop tem uma limitação de
   chave estrutural não resolvida). Consequência: cada registro é uma decisão separada, cada uma
   exige atualizar `_UNREGISTERED_HANDLERS` na mesma PR (a cerca falha se o registro e o registro
   documentado divergirem).
3. **Registrar os quatro de uma vez.** Consequência: maior superfície A2A ligada simultaneamente;
   mais fácil de auditar em um único memo de decisão, mas concentra o risco (ex.: se a limitação de
   chave de beatriz não for resolvida antes, o 2º hop de fraude fica exposto como replay silencioso
   do 1º).

**Arquivos CODEOWNED envolvidos.** Nenhum diretamente — `a2a_composition.py`, os `delegation.py` e
`tests/unit/a2a/test_agent_card_handlers_parity.py` não estão em `.github/CODEOWNERS`. A decisão em
si é owner-gated por convenção do programa (gap `FERNANDO-DELEGATION-CALL-SITE`), não por
CODEOWNERS técnico.

**O que já está pronto no código (símbolo).** `src/maezo/agents/{marina,beatriz,gustavo,valentina}/delegation.py::make_<agente>_handler`
— compilam o grafo real via `graph.build(config)`, mesmo contrato fail-closed de qualquer outro
chamador; testes próprios (`tests/unit/agents/test_{marina,gustavo,valentina}_delegation.py`)
passam isoladamente.

**O que fica bloqueado até a decisão.** Qualquer delegação A2A viva para marina/beatriz/gustavo/
valentina; os gaps declarados `GAP_BEATRIZ_A2A_NAO_LIGADO` (`tools/workers/fraude.py`) e
`GAP_ENROLL_A2A_NAO_LIGADO` (`tools/workers/programa.py`) continuam sendo emitidos como lacuna
honesta em vez de coleta/montagem reais.

**Prazo sugerido.** 2026-09-19 (mesmo ciclo dos demais itens de governança A2A; alinhar com a
decisão que já liberou fernando).

---

## 2. Alert rule sobre `maezo_agent_desfecho_total` em `deploy/observability/alert-rules.yml`

**Decisão pedida.** Aprovar (ou não) uma nova regra `alert:` sobre `maezo_agent_desfecho_total`
(CC-09), com o `runbook_url` que `make check-alert-runbook-urls` exige para toda regra `alert:`.

**Correção de rótulo, INFO.** O brief desta tarefa rotulou este item "VAL-08"; **verifiquei e
VAL-08 no relatório de auditoria (`$S/snap/maezo-agent-fleet-audit-20260904.md:1786,1948,2151`) é
outro achado** ("nenhum code path de produção executa o grafo de Valentina", dimensão 10, P1) —
não tem relação com `maezo_agent_desfecho_total` nem com regra de alerta. Não encontrei, em
`GAP-REGISTER.yaml` nem no relatório, nenhum id que nomeie especificamente "alerta sobre
`maezo_agent_desfecho_total`" — o fundamento real deste item é o próprio docstring de
`record_agent_desfecho` (abaixo). Reporto a divergência em vez de inventar um id.

**Contexto verificado por comando.**
- `cat deploy/observability/alert-rules.yml` na base `b9cdf245`: 3 grupos de `alert:` (SLA,
  crash-loop, dead-letter) + 1 grupo `maezo_lifecycle`; **nenhuma regra lê
  `maezo_agent_desfecho_total`** hoje.
- `gh pr diff 341 -- deploy/observability/alert-rules.yml` (PR irmã aberta `r5/kpi-lag`, não
  mesclada): acrescenta o grupo `maezo_agent_kpi_derived` com **3 `record:` (recording rules), zero
  `alert:`** — `maezo_helena_resolution_rate`, `maezo_helena_escalation_rate` (ambas
  `resolvido_automatico`/`escalado_humano` sobre o total de helena) e `maezo_lucas_resolution_rate`
  (`resposta_informativa_enviada|lembrete_enviado` sobre o total de lucas). O próprio commit
  message de #341 diz explicitamente "nenhum alert/dashboard novo (D12-02 é escopo separado)".
  Como são `record:`, não `alert:`, **não precisam de `runbook_url`** —
  `scripts/ci/check_alert_runbook_urls.py:16` só gate `alert:` sem `annotations.runbook_url`.
- `src/maezo/platform/observability.py::record_agent_desfecho` documenta um invariante `==0`
  esperado por design: `desfecho` num conjunto adverso (ex.: `false_denial_rate` de
  rafael/marina/gustavo/valentina/lucas/fernando, `false_decredentialing_rate` de carolina,
  `zero_auto_accusation`/`false_accusation_rate` de beatriz) "MUST have a count of ZERO em
  `sum(maezo_agent_desfecho_total{agent_id="<x>", desfecho=~"negativa_.*|rescisao_.*|
  descredenciamento_.*|acusacao_.*"})`" — citado literalmente do docstring.
- **Verificação adversarial (achado novo, INFO):** `src/maezo/runtime/turn_telemetry.py::_DESFECHO_VOCAB`
  é o vocabulário FECHADO real de cada agente. Li os 10 conjuntos por inteiro — **nenhum token
  literal de nenhum agente casa com o regex `negativa_.*|rescisao_.*|descredenciamento_.*|
  acusacao_.*`** (ex.: carolina usa `analise_descredenciamento`, não `descredenciamento_*`; beatriz
  usa `dossie_instruido`/`instrucao_incompleta`, nunca um token `acusacao_*`). Ou seja: **a query
  citada no próprio docstring de `record_agent_desfecho` é hoje VÁCUA contra o vocabulário real** —
  ela nunca dispararia, não porque o invariante seja violado, mas porque a proteção real é
  ESTRUTURAL (a topologia do grafo de beatriz não tem aresta condicional para uma acusação
  automática — `zero_auto_accusation` é L0 estrutural, não um filtro de rótulo) e não pelo
  vocabulário de `desfecho`. Uma regra de alerta calcada literalmente nesse regex seria uma
  proteção cosmética, não funcional.
- Em contrapartida, `erro_inicio_processo` (`_DESFECHO_ERRO_INICIO_PROCESSO`) **aparece de fato**
  no vocabulário de 8 dos 10 agentes (todo agente que inicia processo) — é o desfecho do padrão
  fail-notify do item 11/ADR-0045.

**Opções.**
1. **Não criar alerta agora**, manter #341 como está (só recording rules, D12-02 decide alertas
   depois). Consequência: nenhuma regra nova, nenhum `runbook_url` a escrever agora.
2. **Alerta sobre a taxa de `erro_inicio_processo`** — `sum by (agent_id)
   (rate(maezo_agent_desfecho_total{desfecho="erro_inicio_processo"}[5m])) > 0`, com
   `runbook_url` novo. Consequência: dá sinal observável exatamente ao padrão fail-notify do item
   11 (ADR-0045) — union natural entre os dois itens deste memo. Requer um runbook novo em
   `docs/runbooks/alerts/`.
3. **Alerta sobre o invariante adverso ==0**, mas reescrito para nomear os tokens REAIS por agente
   (não o regex vácuo do docstring) — ex. uma regra por agente com o token adverso real dele.
   Consequência: mais preciso, mas 6+ regras separadas (uma por agente com invariante hard) em vez
   de uma regra genérica; e corrige o docstring de `record_agent_desfecho` para não citar uma query
   que nunca casa.

**Recomendação de engenharia (NÃO VINCULANTE):** opção 2 — é a que tem sinal real hoje, cobre um
P1 confirmado (CC-01/ADR-0045) e sua ausência (fail-open silencioso) é exatamente o problema que
motivou o ADR. Opção 3 é o alvo correto de longo prazo, mas exige primeiro corrigir o docstring do
código (fora do escopo deste memo).

**Arquivos CODEOWNED envolvidos.** `deploy/observability/alert-rules.yml` (`deploy/**` é CODEOWNED
por política de programa, conforme #341 já disclosed em sua própria linha de ledger).

**O que já está pronto no código (símbolo).** `src/maezo/platform/observability.py::record_agent_desfecho`,
`src/maezo/runtime/turn_telemetry.py::emit_turn_desfecho`, contador `maezo_agent_desfecho_total`
— todos emitindo em produção (CC-09, VERIFIED-FIXED).

**O que fica bloqueado até a decisão.** Nenhuma regra `alert:` nova sobre desfecho até o dono
escolher a forma; `MaezoAgentCrashLoop` (já existente) cobre erro técnico agregado, não
especificamente falha de start.

**Prazo sugerido.** 2026-09-19.

---

## 3. `mcp-memory.read_write` fora do catálogo de efeitos — arquivo do brief está errado

**Correção factual, não é um detalhe menor.** O brief desta tarefa apontou
`src/maezo/tools/process_allowlist.py` / `process_allowlist.yaml` como o arquivo deste item.
**Verifiquei e nenhum dos dois cobre o assunto:**
- `src/maezo/tools/process_allowlist.py` (lido por inteiro, 164 linhas) é o allowlist de
  **process keys BPMN** (`KNOWN_PROCESS_KEYS`, ADR-0016) — `SP-OP-AUTH-001` etc. Não menciona
  `mcp-memory` nem qualquer tool MCP; `grep -n mcp-memory` nele: 0 hits.
- `src/maezo/tools/process_allowlist.yaml` **não existe** (`find . -iname process_allowlist.yaml`:
  0 hits).
- `.github/CODEOWNERS:75` cita `/src/maezo/policies/process_allowlist.yaml` como caminho
  CODEOWNED — mas **o próprio CODEOWNERS já documenta, num comentário de auditoria de
  2026-08-13, que esse arquivo (e todo o diretório `/src/maezo/policies/`) NUNCA EXISTIU**
  ("`git log --all --diff-filter=A` vazio para ambos... Mantidas como gates PRE-POSICIONADOS...
  NÃO as leia como cobertura de hoje"). Confirmei: `ls src/maezo/policies/` → não existe.

**O arquivo real do achado é `src/maezo/gateway/effect_classes.py`** — o catálogo fechado de
operações do chokepoint de efeito. Corrijo o item para esse arquivo.

**Decisão pedida.** Classificar (ou decidir não classificar ainda) uma `action_class` para
`mcp-memory.read_write` no catálogo `OPERATIONS` de `effect_classes.py`.

**Contexto verificado por comando.**
- `sed -n '1,60p' src/maezo/gateway/effect_classes.py`: o docstring do módulo tem uma seção
  "KNOWN GAP, DISCLOSED" que diz literalmente: "`mcp-memory.read_write` é declarado por 11 de 11
  `spec/agents/*/agent.yaml` e mapeia para o `read_write_memory` ratificado (L3), mas a tabela de
  rungs do design §6.1 não declara NENHUMA action class para memória... um seam de memória roteado
  pelo chokepoint faria DENY com `OPERACAO_DESCONHECIDA` — fail-closed, e hoje inerte porque nada
  aplica".
- `grep -n read_write_memory spec/policies/autonomy/L0-core.yaml` → `read_write_memory: { level: L3 }`
  — a ação já é ratificada na matriz de autonomia; falta só a classe de efeito no catálogo.
- `scripts/ci/check_effect_chokepoint_fence.py:924-1036` já trata isso como exceção divulgada e
  explícita: `_ITEM2_DISCLOSED_TOOL_ID_EXCEPTIONS = frozenset({"mcp-memory.read_write"})` — a cerca
  §8.5 item 2 (todo id `mcp-<server>.<action>` declarado resolve a uma operação do catálogo) **já
  está verde hoje porque essa exceção existe**, não porque o gap foi fechado.

**Opções.**
1. **Manter a exceção divulgada** (nenhuma classe nova). Consequência: memória nunca passa pelo
   chokepoint de efeito enquanto ninguém decidir a classe; continua fail-closed e inerte —
   consistente com CC-07 (memória declarada em 10/10 `agent.yaml`, exercida em 0/10 grafos) e com
   ADR-0043's metade episódica ainda aberta.
2. **Classificar agora** uma `action_class` nova (ex. `memoria_leitura_escrita`) com denial shape
   e L-2/L-4 flags análogos às demais classes L3. Consequência: remove a exceção de
   `check_effect_chokepoint_fence.py`; memória passa a ser roteável pelo PEP — mas só tem
   consequência prática quando (e se) `MemoryServer` ganhar um adaptador real (ADR-0043, metade
   episódica).
3. **Classificar como decisão formal de NÃO CATALOGAR** (ligado à Parte 2 de ADR-0043: "aposentar"
   em vez de "ativar"), documentando que a exceção divulgada é permanente até segunda ordem, não
   temporária.

**Arquivos CODEOWNED envolvidos.** Nenhum dos três arquivos reais (`effect_classes.py`,
`check_effect_chokepoint_fence.py`, `spec/agents/*/agent.yaml`) está em `.github/CODEOWNERS`.

**O que já está pronto no código (símbolo).** `src/maezo/gateway/effect_classes.py::OPERATIONS`,
`::CATALOGUED_TOOL_IDS`; `scripts/ci/check_effect_chokepoint_fence.py::_ITEM2_DISCLOSED_TOOL_ID_EXCEPTIONS`.

**O que fica bloqueado até a decisão.** Qualquer seam de memória que um dia passe pelo chokepoint
de efeito continuaria negado por `OPERACAO_DESCONHECIDA` até uma classe existir — hoje isso é
inerte (nada chama o chokepoint com essa operação).

**Prazo sugerido.** Acoplar à ratificação de ADR-0043 (item 10) — mesma janela, 2026-09-19.

---

## 4. `check_evidence_ledger_hashes.py` — ids parametrizados com espaço somem do hash

**Decisão pedida.** Aprovar a correção do regex `_RESULT_LINE_RE` (hoje `\S+::\S+`) para não
descartar silenciosamente linhas PASSED/FAILED cujo id de teste parametrizado contém espaço.

**Contexto verificado por comando.**
- `grep -n "_RESULT_LINE_RE\s*=" scripts/ci/check_evidence_ledger_hashes.py` (base `b9cdf245`):
  `_RESULT_LINE_RE = re.compile(r"^(\S+::\S+ (?:PASSED|FAILED)(?:\s.*)?)$", re.MULTILINE)` — o lado
  direito do `::` exige `\S+` (sem espaço), então um id como
  `test_x[RATIFICADO pelo DPO]` nunca casa e a linha inteira é descartada do hash.
- Medi eu mesma, na base `b9cdf245` (não herdado do brief): `pytest tests/unit/tools/workers/test_fraude.py -v --tb=no`
  → **148 linhas de resultado, 21 descartadas** pelo regex atual (127 restam) — bate com o "21/148"
  do brief. `test_programa.py` → **137 linhas, 15 descartadas** (122 restam) — o brief citava
  "15/135"; a diferença (137 vs 135) é esperada por 2 testes terem sido adicionados depois que o
  brief foi escrito — reporto como INFO, não como discrepância de causa.
- **A sessão irmã já corrigiu isso**, mas **não em PR aberta** — verifiquei
  `gh pr list --search "0d61680\|tooling-fences" --state all`: **0 resultados**. O commit existe
  como ref local `refs/heads/r5/tooling-fences` (`0d616803`, "quatro cercas de tooling do
  ledger/PLANS.md", com reparo subsequente `505551b8`, mesclado num branch de integração local
  `r5/train-4` em `fb944424`) — **nenhum desses três commits está em `origin/main`**
  (`git merge-base --is-ancestor 0d616803 origin/main` → não). Ou seja: a correção existe em
  código, em algum lugar do disco compartilhado deste programa, mas **não passou por PR nem por
  CI** até este momento. Cito a mensagem do commit: troca a regex para `\S+::.+` e mantém
  `_RESULT_LINE_RE_LEGACY` + `LEGACY_NODE_ID_RECIPE_CUTOFF_DATE = "2026-09-06"` para não invalidar
  retroativamente hashes já registrados.

**Opções.**
1. **Aguardar a PR da sessão irmã** (quando/se ela abrir `r5/tooling-fences` como PR) e revisar ali.
2. **Pedir que a correção seja aberta como PR agora**, dado que o achado é real e mensurável nesta
   base (`b9cdf245`) e a correção já existe pronta em disco — só falta o veículo de revisão.
3. **Implementar independentemente** (outro agente/sessão), correndo o risco de duplicar o trabalho
   já feito em `r5/tooling-fences` caso ele apareça como PR depois.

**Arquivos CODEOWNED envolvidos.** `scripts/ci/check_evidence_ledger_hashes.py` (`scripts/ci/` é
CODEOWNED).

**O que já está pronto no código (símbolo).** Nada em `origin/main`; a correção existe apenas na
ref local `r5/tooling-fences` (`0d616803`/`505551b8`), não em nenhum PR.

**O que fica bloqueado até a decisão.** `make check-ledger-hashes` continua com um "legacy row
skipped" cego para toda linha cujo hash-alvo tem id parametrizado com espaço — inclusive linhas já
registradas por `test_fraude.py`/`test_programa.py` neste mesmo ciclo.

**Prazo sugerido.** 2026-09-12 (curto — a correção já existe, só falta abrir o veículo de PR).

---

## 5. CC-13 como gate de CI nomeado (ADR-P-002)

**Decisão pedida.** Autorizar a criação de um checker dedicado em `scripts/ci/` + alvo de
`Makefile` + passo em `.github/workflows/ci.yml` para a paridade de proveniência de contrato
(CC-13/ADR-P-002), hoje coberta apenas como parte do lote geral de testes unitários.

**Contexto verificado por comando.**
- `docs/evidence-ledger.md` linha CC-13 (lida por inteiro): a própria linha registra, no fim,
  "**ADR-P-002 e qualquer alvo de `Makefile`/`scripts/ci/` — explicitamente FORA DO ESCOPO
  (owner-gated) por instrução do brief, não criados**".
- `env -u VIRTUAL_ENV uv run python -m pytest tests/unit/agents/test_contract_provenance_parity.py -q`
  → **9 passed** nesta base (o teste existe e passa; VERIFIED-FIXED confirmado independentemente
  pela auditoria de fase 1, `ASSURANCE-F1-C1.md`).
- `grep -n "run: make\|run: uv run" .github/workflows/ci.yml`: o job `quality` roda
  `uv run pytest tests/ -q --cov=src/maezo ... --cov-fail-under=85` — um ÚNICO comando que engole
  todos os ~10600 testes, incluindo `test_contract_provenance_parity.py`. **Não existe, hoje, um
  passo NOMEADO no workflow que rode ou reporte esse teste isoladamente** — ao contrário de
  `check-bpmn-error-allowlist`, `check-start-process-fence` etc., que são passos próprios e
  visíveis nos logs do CI, com nome legível na lista de checks do PR.
- Consequência prática dessa diferença: se `test_contract_provenance_parity.py` falhar, o sinal no
  PR é "quality / lint / type / unit" vermelho — sem indicar que foi especificamente a paridade de
  proveniência que quebrou, ao contrário dos outros L0-hard invariants do programa.

**Opções.**
1. **Manter como está** (dentro do lote geral de pytest, sem gate nomeado). Consequência: o teste
   já roda e já bloqueia merge se falhar (via coverage/unit gate) — a proteção EXISTE, só não é
   nomeada/visível separadamente.
2. **Criar `scripts/ci/check_contract_provenance_parity.py`** que roda especificamente esse teste
   (ou reimplementa a checagem fora do pytest) + alvo `make check-contract-provenance` + passo
   dedicado no workflow. Consequência: paridade com os outros L0-hard gates, mais um arquivo
   CODEOWNED (`scripts/ci/`) e uma linha a mais no `Makefile`/CODEOWNERS.
3. **Não criar um script novo, só nomear o passo de CI** (ex. `pytest tests/unit/agents/test_contract_provenance_parity.py`
   como um step próprio no workflow, antes ou além do lote geral). Consequência: visibilidade sem
   duplicar infraestrutura de gate.

**Arquivos CODEOWNED envolvidos (se opção 2).** `scripts/ci/` (arquivo novo), `Makefile`,
`.github/workflows/ci.yml` — todos CODEOWNED.

**O que já está pronto no código (símbolo).** `tests/unit/agents/test_contract_provenance_parity.py::_CASES`
(9 pares agente/contrato), VERIFIED-FIXED.

**O que fica bloqueado até a decisão.** Nada tecnicamente — a proteção já roda. O que fica em
aberto é só a visibilidade/nomeação do gate.

**Prazo sugerido.** 2026-09-19 (baixa urgência — proteção já ativa).

---

## 6. CC-12 como gate de CI nomeado (ADR-0044, `task_kind`)

**Decisão pedida.** Mesma pergunta do item 5, para a cerca de `task_kind` obrigatório em toda
chamada `.generate()` de agente (CC-12).

**Contexto verificado por comando.**
- `env -u VIRTUAL_ENV uv run python -m pytest tests/unit/agents/test_llm_calls_declare_task_kind.py -q`
  → passa nesta base (VERIFIED-FIXED, confirmado pela auditoria de fase 1 com sondas de mutação:
  remover o `task_kind=` de uma chamada real produz `1 failed`; trocar `task_kind="reasoning"` por
  `task_kind="batch"` também produz `1 failed` — "task_kind='batch' fora de ['reasoning',
  'task_default']" — porque **ADR-0044 ainda não foi ratificada**, então o vocabulário efetivamente
  aceito pela cerca hoje é um subconjunto do catálogo que o código já suporta).
- `grep -n MODEL_TASK_KINDS src/maezo/runtime/inference/__init__.py` → `MODEL_TASK_KINDS: Final[frozenset[str]]
  = frozenset({"task_default", "reasoning", "batch"})` — o catálogo de 3 já existe em código e é
  fail-closed na construção do provider (`_validated_model_tiers`); **"batch" já está implementado,
  mas nenhum call site real de agente o usa hoje** (por isso a cerca RED ao testar).
- Mesma situação estrutural do item 5: `test_llm_calls_declare_task_kind.py` roda dentro do lote
  geral do job `quality`, sem passo nomeado próprio.

**Opções.** Idênticas em estrutura ao item 5 (manter dentro do lote / criar gate dedicado / nomear
passo sem novo script), com a diferença de que aqui há uma SEGUNDA decisão acoplada: **ratificar
ADR-0044 também decide se `task_kind="batch"` passa a ser aceito em call sites reais** — hoje a
cerca recusaria um agente real que o usasse, mesmo o valor já existindo no catálogo.

**Arquivos CODEOWNED envolvidos (se gate dedicado).** `scripts/ci/`, `Makefile`,
`.github/workflows/ci.yml`.

**O que já está pronto no código (símbolo).** `tests/unit/agents/test_llm_calls_declare_task_kind.py`;
`src/maezo/runtime/inference/__init__.py::MODEL_TASK_KINDS`.

**O que fica bloqueado até a decisão.** Visibilidade do gate (igual item 5) + uso de
`task_kind="batch"` em qualquer grafo real (aguarda ratificação de ADR-0044).

**Prazo sugerido.** Acoplar à ratificação de ADR-0044 (item 10), 2026-09-19.

---

## 7. Catálogo `effect_classes.py` — ids órfãos e `CAROLINA-FHIR-TOOL-DECLARED-MISMATCH`

**Decisão pedida.** Duas questões de governança distintas no mesmo arquivo:
(a) classificar `mcp-memory.read_write` (mesmo assunto do item 3, mesmo arquivo);
(b) decidir o que fazer com o mismatch `carolina` declara `mcp-fhir.read_patient_summary` mas
invoca `read_patient` — hoje é um would-DENY em shadow, e passaria a DENY real quando
`leitura_phi_clinica` sair de shadow para enforcing.

**Contexto verificado por comando.**
- Achado `CAROLINA-FHIR-TOOL-DECLARED-MISMATCH`, reproduzido pelo relatório de fase 1
  (`ASSURANCE-F1-C2.md`, bloco CC-03(d)) via probe direto do PEP:
  `env -u VIRTUAL_ENV uv run python -c "from maezo.gateway.effect_pep import decide_effect; ...
  decide_effect(tenant='amh', principal='carolina', operation='fhir.read_patient', ...)"` →
  `False False TOOL_NAO_DECLARADA`. Fatos: `spec/agents/carolina/agent.yaml:20` declara
  `mcp-fhir.read_patient_summary`; `gateway/tool_registry.py::_FHIR_ADAPTER_BY_AGENT["carolina"] =
  "read_patient"`; `carolina/graph.py:512` chama `self._fhir.read_patient(summary_ref)` — o id
  declarado e o adaptador invocado divergem.
- O mesmo probe para `andre` (que declara corretamente `mcp-fhir.read_patient`) devolve `False
  False MANIFESTO_NAO_RATIFICADO` — o manifesto de autonomia inteiro está em `status: DRAFT`/`mode:
  shadow`, com `review_by: 2027-02-09` (`spec/policies/autonomy/action-approvals.yaml:414`). Ambos
  os casos são **would-DENY em shadow hoje**, sem efeito em produção — mas o mismatch de carolina
  seria um DENY REAL no dia em que `leitura_phi_clinica` sair de shadow.
- **Achado adjacente, INFO:** o gap `9.5` do `GAP-REGISTER.yaml` (drift de nomes de tool) foi
  fechado como `CLOSED (091b926) / NO-LONGER-REPRODUCES`, mas essa correção foi **só de
  docstring** (`build_fhir_seam` passou a documentar o drift) — não resolve a consequência
  funcional que `decide_effect()` ainda produz hoje. Isso não é meu achado original; a auditoria de
  fase 1 já registrou como pendência a ser sinalizada a quem for dono do gap 9.5.

**Opções (para o mismatch de carolina, item b).**
1. **Corrigir o `agent.yaml`** para declarar `mcp-fhir.read_patient` (igual andre) — alinha
   declaração ao código, sem tocar `graph.py`.
2. **Corrigir o código** para chamar o adaptador `read_patient_summary` (se esse for o
   comportamento pretendido) — mudança de comportamento, exige revisão de qual leitura é
   semanticamente correta para o fluxo de carolina.
3. **Não corrigir agora**, mas registrar a decisão de manter `leitura_phi_clinica` em shadow até
   este e outros mismatches serem sanados — evita que a promoção para enforcing produza um DENY
   real por um erro de nomenclatura, não por uma decisão de negócio.

**Arquivos CODEOWNED envolvidos.** `spec/agents/carolina/agent.yaml` não está em CODEOWNERS;
`spec/policies/autonomy/` (a matriz e o manifesto de ratificação) **é** CODEOWNED
(`.github/CODEOWNERS`, linha citada acima).

**O que já está pronto no código (símbolo).** `src/maezo/gateway/effect_pep.py::decide_effect`;
`src/maezo/gateway/effect_classes.py::{OPERATIONS, ACTION_CLASSES}`; ambos comportando-se
exatamente como o design prevê (fail-closed, shadow).

**O que fica bloqueado até a decisão.** A promoção de `leitura_phi_clinica` de shadow para
enforcing (fora do escopo deste memo, mas dependente desta correção) herdaria o DENY de carolina
se o mismatch não for sanado antes.

**Prazo sugerido.** 2026-09-19 (mesma janela do item 3, mesmo arquivo).

---

## 8. `PopulationFeatureClient` (WB.4, bloqueado externo)

**Decisão pedida.** Nenhuma decisão de engenharia pendente aqui — o item é informativo: o que já
existe, o que falta, e quem decide o próximo passo (que é externo ao programa).

**Contexto verificado por comando.**
- `grep -n "class PopulationFeatureClient" src/maezo/agents/andre/graph.py` → Protocol definido em
  `graph.py:426-434`, com duas operações (`actuarial_risk`, `population_metrics`), retornando
  SEMPRE um `CohortAggregate` k-anon + `dataset_ref` — nunca um `fhir_patient_id` resolvível.
- `grep -rn "BLOCKED.*external WB.4\|PORT-PENDING WB.4" src/maezo/`: **nenhum cliente concreto
  existe em `src/`** — `population=None` é uma configuração suportada e testada
  (`a2a_composition.py:775`: "there is no concrete `PopulationFeatureClient` in `src/`"); o
  composition root passa `population` explicitamente como `None` com o comentário
  "BLOCKED(external WB.4) — see this function's docstring. Explicit, never omitted."
- Já existe um **port formal e mais rico** para o mesmo conceito:
  `src/maezo/ports/population_features.py::PopulationFeaturePort` — mirror do contrato pinado
  publicado (`config/integrations/amh/contracts.lock.json`), com `KAnonymityPolicy` (piso
  comprometido vs. piso aplicado), `ConsentFilter` (com `consent_snapshot_at`) e supressão
  in-band por célula. **O próprio docstring do port diz que ele NÃO liga o módulo do andre**:
  "Wiring an adapter that satisfies both this port and `PopulationFeatureClient` is **MZO-050+/
  MZO-080 scope** and remains gated. This work package does NOT touch André's module."
- `gateway/seams/population.py::GatedPopulationFeatureClient` já existe e é TESTADO, mas "SHIPS
  UNWIRED, AND THE MANIFEST SAYS SO" (comentário literal do arquivo) — a wrapper está pronta para
  o dia em que WB.4 chegar, mas hoje não decora nada real.
- Achado da auditoria (AND-03, dim 1/8): mesmo se WB.4 existisse hoje, o composition root
  constrói o handler de andre **sem `population` nem `fhir`** — então nenhum dossiê de andre
  carregaria agregados k-anon em produção "a menos que alguém passe o client neste call site"
  (achado independente do bloqueio externo).

**O que existe.** Protocol (`andre/graph.py`), port mais rico e pinado (`ports/population_features.py`),
wrapper de PEP pronta e testada (`gateway/seams/population.py`), allowlist de features fechada
proposta em ADR-0042.

**O que falta.** (1) O cliente concreto de `mcp-datalake` (WB.4 — dependência externa, fora do
programa); (2) o adapter que satisfaça SIMULTANEAMENTE `PopulationFeaturePort` (o port novo) e
`PopulationFeatureClient` (o Protocol do andre) — MZO-050+/MZO-080, gated; (3) passar
`population=<client>` no call site de `a2a_composition.py:838` (isso já não depende de WB.4 para
ser corrigido — é uma lacuna de fiação separada, AND-03).

**Quem decide.** WB.4 (o cliente de `mcp-datalake`) é uma dependência externa — decisão de
roadmap/fornecedor, não deste programa. MZO-050+/MZO-080 (o adapter) é trabalho de engenharia já
descrito, mas apontado como fora do escopo de qualquer WP atual — precisa ser agendado. O item (3)
(fiação do call site) é o único puramente interno e não bloqueado por nada externo.

**Prazo sugerido.** Sem prazo — depende de WB.4 (externo). Recomendo que o dono só agende
MZO-050+/MZO-080 quando houver data de WB.4.

---

## 9. HEL-11 — agendamento direto (ADR-0046, memo já existe)

**Decisão pedida.** Nenhuma recomendação de engenharia — só a pergunta e as opções, seguindo a
mesma disciplina que `docs/adr/0046-memo-agendamento-direto-helena.md` já aplicou.

**Contexto verificado por comando.** `cat docs/adr/0046-memo-agendamento-direto-helena.md` (arquivo
já existe, `Status: Proposed — MEMO. Decisão PENDENTE DO DONO. SEM RECOMENDAÇÃO DE ENGENHARIA`).
O documento já registra a colisão central: o relatório de auditoria propõe "agendamento passível
de automação **L2**", mas `grep -n scheduling spec/policies/autonomy/L0-core.yaml` mostra que a
matriz viva já classifica `scheduling` em **L3** — a proposta da auditoria, se aplicada
literalmente, seria um **rebaixamento** de autonomia, não uma habilitação. Este item do memo
apenas resume e encaminha o documento já pronto; não duplico o texto.

**As três perguntas já registradas em ADR-0046 (citação literal):**
1. `scheduling` permanece **L3**, é rebaixada a **L2**, ou outro nível?
2. Agendamento direto entra no escopo de Fase 1, ou o handoff humano atual (já construído e
   funcional — `HelenaGraph.schedule` → `_start_escalation` → SP-OP-ESCALATION-001) permanece?
3. Se entrar: autoriza `SP-OP-AGENDAMENTO-001` novo (contrato+BPMN+DMN) e a inclusão de uma chave
   nova em `KNOWN_PROCESS_KEYS` (`src/maezo/tools/process_allowlist.py` — dono-gated +
   security-team)?

**Arquivos CODEOWNED envolvidos.** `docs/adr/` (o próprio ADR-0046); se a resposta for "sim" à
pergunta 3, também `src/maezo/tools/process_allowlist.py` e, se o nível de autonomia for
revisitado, `spec/policies/autonomy/L0-core.yaml` — ambos CODEOWNED com `@Omni-Saude/security-team`.

**O que já está pronto no código (símbolo).** `src/maezo/agents/helena/graph.py::HelenaGraph.schedule`
roteando para `::_start_escalation` — o handoff humano real já funciona, HEL-11 não deixa o
beneficiário sem resposta hoje.

**O que fica bloqueado até a decisão.** Toda a capacidade de agendamento direto (contrato, BPMN,
DMN de elegibilidade `triage_elegibilidade_agendamento`, chave em `KNOWN_PROCESS_KEYS`). "Nenhuma
linha de código, spec ou política deve ser escrita a partir [de ADR-0046] antes" da resposta — cito
o próprio ADR.

**Prazo sugerido.** Sem recomendação de prazo — é uma decisão de produto/política de autonomia,
não uma pendência técnica com custo de atraso mensurável hoje (o handoff atual não regride
enquanto a decisão não vier).

---

## 10. Ratificação de ADR-0042–0048

**Decisão pedida.** Ratificar (ou não, ou parcialmente) cada uma das 7 ADRs do programa de
remediação do fleet audit. Todas, hoje, estão `Proposed — DRAFT/verify` ou `MEMO`
(nenhuma ratificada) — confirmado por `grep -m1 "^\*\*Status" docs/adr/004[2-8]*.md` em cada uma
das 7.

| ADR | Decisão que o dono toma | O que a ratificação libera | Já implementado, divulgado |
|---|---|---|---|
| **0042** — k-anonimato do egresso de agregados | Piso numérico (`min_k_anonymity`, opção A=5 recomendada pela auditoria, B por finalidade, C manter 1) + allowlist fechada de features de métrica | Promove `AND-02`'s `DEFAULT_MIN_K_ANONYMITY` de constante de código (hoje **`1`**, verificado em `andre/graph.py:343`) para parâmetro de política versionado e fail-closed | `andre/graph.py::{_metric_value_is_admissible, _scrub_aggregate, DEFAULT_MIN_K_ANONYMITY}` já existe e já FUNCIONA com k=1 — a ratificação muda o VALOR/mecanismo de config, não cria o gate |
| **0043** — memória episódica/semântica | Só a metade EPISÓDICA (`MemoryServer`/`mcp-memory.read_write`, AF-11/DU-01-a) segue em aberto | Fecharia CC-07 por completo (hoje só a metade semântica está resolvida) + destrava a classificação de `mcp-memory.read_write` no catálogo (item 3) | **Metade SEMÂNTICA já decidida e implementada**: R-005/R-006 (owner, 2026-09-04) escolheu remover pgvector; migração `0009_drop_pgvector.py` já existe e **já está mesclada em `main`** (PR #320, `abb9d60`, ancestral confirmado de `b9cdf245`) |
| **0044** — `task_kind` obrigatório | Ratificar o catálogo `{task_default, reasoning, batch}` como requisito de CI, não só como valor de código já existente | Habilita a cerca CC-12 a aceitar `task_kind="batch"` em call sites reais (hoje recusaria, ver item 6) | `MODEL_TASK_KINDS` (`runtime/inference/__init__.py:282`) já tem os 3 valores; `_validated_model_tiers` já fail-closa na construção |
| **0045** — fail-notify canônico de `start_process` | 5 decisões explícitas: (1) elevar o padrão a requisito do `_template`; (2) política de retry/alerta (opções A/B/C); (3) forma da cerca (`scripts/ci/` dono-gated vs. teste em `tests/`); (4) correção da fronteira A2A (raise tipado vs. campo `success`); (5) adicionar `erro_inicio_processo` à enumeração de desfechos dos contratos `SP-OP-*` | Promove o padrão CC-01 de "já implementado" para "requisito do contrato canônico" — ver item 11 | **Decisões 1 e 5 já materializadas na prática**: `_template/graph.py` já implementa o padrão; **10 contratos `SP-OP-*.md` já declaram `erro_inicio_processo`** (`grep -rl erro_inicio_processo docs/processes/contracts/*.md` → 10 arquivos). **Decisão 4 (fronteira A2A) já implementada para rafael**: `rafael/delegation.py` levanta `StartProcessFailedError` tipado em vez de devolver sucesso falso (RAF-02). Decisões 2 e 3 seguem abertas |
| **0047** — emenda ADR-0002 §3 suspensa | Assinatura formal da emenda (a decisão de mérito, R-005/R-006, já foi tomada pelo dono e citada literalmente no próprio ADR-0047) | Formaliza por escrito que ADR-0002 §3 (camada semântica) fica suspensa até haver consumidor | **Já implementado**: migração `0009_drop_pgvector.py` mesclada (mesmo PR #320 acima); falta só a assinatura do documento |
| **0048** — `driver_idempotency` reancorada para dedup de `wamid` | Assinatura formal (decisão de mérito R-073 já aprovada, citada literalmente no ADR) | Formaliza a tabela `driver_idempotency` como registro de dedup do canal WhatsApp (chave = HMAC do `wamid`, nunca o `wamid` cru) | **Já implementado**: migração `0010_webhook_wamid_dedup.py` **já mesclada em `main`** (PR #335, `9496faa`, ancestral confirmado de `b9cdf245`) |

**Correção ao brief, INFO.** O brief desta tarefa listou "CC-13 checker" entre os itens que a
ratificação de 0042–0048 libera. **Verifiquei e nenhuma das 7 ADRs menciona um checker de CI para
CC-13** — `grep -l CC-13 docs/adr/004[2-8]*.md` só acha `0045`, e lá a menção é apenas "o trabalho
de proveniência CC-13 já materializado... combina com" o padrão fail-notify, não uma promessa de
gate de CI. O checker de CC-13 é assunto do item 5 deste memo (ADR-P-002, que **ainda não foi
redigida** — `ls docs/adr | grep P-002`: 0 hits, é só uma referência no relatório de auditoria) —
não depende da ratificação de nenhuma das 0042–0048.

**Arquivos CODEOWNED envolvidos.** `docs/adr/` (as 7 ADRs); cada ratificação individual pode
destravar outros CODEOWNED específicos listados nos itens 1–9 acima.

**Prazo sugerido.** 2026-09-19 para todas (permite revisão conjunta); 0047/0048 podem ser
assinadas antes (2026-09-12) por já não terem decisão de mérito pendente, só formalização.

---

## 11. CC-01 → requisito do contrato canônico do `_template` (ADR-0045)

**Decisão pedida.** As mesmas 5 decisões explícitas listadas na tabela do item 10 para ADR-0045,
detalhadas aqui com o que muda em `spec/agents/_template/agent.yaml` e
`docs/processes/contracts/`.

**Contexto verificado por comando.**
- `grep -n "notify_start_failure\|erro_inicio_processo" src/maezo/agents/_template/graph.py`: o
  `_template` **já implementa** o esqueleto `receive -> start_process -> {notify_start_failure |
  complete}` (linha 186), importando `route_after_start`/`notify_start_failure` de
  `maezo.runtime.start_outcome` — "o ponto mais baixo, de onde os 9 agentes e este template
  importam" (comentário do próprio arquivo). **O código do `_template` já está correto** —
  o que falta é a ADR elevar isso de "padrão que o template feliz coincidiu de ter" para
  "requisito obrigatório documentado".
- `cat spec/agents/_template/agent.yaml`: **não há hoje nenhuma menção a `erro_inicio_processo`**
  neste arquivo — ele declara `kpis`, `escalation`, `a2a` etc., mas não tem uma seção de
  "desfechos obrigatórios" que force um agente novo a herdar o padrão fail-notify por contrato
  declarativo (só por herança de código, se o autor copiar `_template/graph.py` como base).
- `grep -rl erro_inicio_processo docs/processes/contracts/*.md` → **10 de ~15 contratos SP-OP-\*
  já declaram** `erro_inicio_processo` na enumeração de desfechos (AUTH, CONTAS, RECURSO, CRED,
  NIP, ANS-SUBMIT, PROGRAMA, INADIMPLENCIA, ESCALATION, PAGTO) — este trabalho spec-first, em boa
  parte, **já aconteceu**, combinado com CC-13 (seção "Variáveis de proveniência do agente").
- As 5 decisões do próprio ADR-0045 (citadas literalmente, seção "O que é decisão do DONO"):
  1. Elevar o padrão fail-notify a requisito do contrato canônico — hoje é "pode ter", ratificar
     torna "PRECISA ter" aresta condicional + `notify_start_failure` para todo agente que inicia
     processo.
  2. Política de retry/alerta (opção A, B ou C da Parte 3 do ADR — não reproduzo aqui o texto
     completo do ADR, só sinalizo que a escolha está em aberto).
  3. Forma da cerca que valida o padrão fleet-wide: `scripts/ci/` + alvo de `Makefile`
     (dono-gated, `.github/CODEOWNERS`) ou teste unitário em `tests/` (não dono-gated).
  4. Correção da fronteira A2A: raise tipado do handler vs. campo `success` em `HandlerOutput`.
     **Já implementada para rafael** — `rafael/delegation.py` levanta `StartProcessFailedError`
     (verificado por leitura direta do código, linhas 171-179) em vez de devolver
     `HandlerOutput` de sucesso quando `start_failed is True`; não verifiquei se os outros 7
     agentes com A2A real seguem o mesmo padrão (fora do escopo deste memo).
  5. Adicionar `erro_inicio_processo` à enumeração de desfechos dos contratos `SP-OP-*` —
     **já feito em 10 contratos** (ver acima); os que faltam (se algum agente que inicia processo
     ainda não tiver a linha) ficariam pendentes de auditoria específica.

**Opções.** Aprovar as 5 decisões em bloco, parcialmente (ex.: só formalizar 1 e 5, que já são
fato consumado na prática, deixando 2/3/4 para depois), ou não aprovar (manter o padrão como
"boa prática não obrigatória").

**Arquivos CODEOWNED envolvidos.** `docs/adr/0045-...md`; se a decisão 3 escolher `scripts/ci/`,
também esse diretório + `Makefile`.

**O que já está pronto no código (símbolo).** `src/maezo/runtime/start_outcome.py::{route_after_start,
notify_start_failure, StartProcessFailedError, DESFECHO_ERRO_INICIO_PROCESSO}`;
`_template/graph.py`'s próprio esqueleto; `rafael/delegation.py`'s `StartProcessFailedError` na
fronteira A2A.

**O que fica bloqueado até a decisão.** A obrigatoriedade formal (decisão 1) e a política de
retry/alerta (decisão 2) — sem elas, um agente novo que copiar um `graph.py` antigo (não o
`_template` atual) ainda poderia reintroduzir o fail-open original.

**Prazo sugerido.** 2026-09-19 (mesma janela do item 10).

---

## Lista de decisões

| id | Pergunta em uma frase | Opção recomendada pela engenharia (quando houver) | Prazo |
|---|---|---|---|
| 1 | Registrar marina/beatriz/gustavo/valentina (e `care.enroll`) no composition root A2A, e atualizar `_UNREGISTERED_HANDLERS`? | Sem recomendação — decisão de exposição de superfície, não técnica | 2026-09-19 |
| 2 | Criar `alert:` sobre `maezo_agent_desfecho_total` com `runbook_url`? | Opção 2 — alertar sobre a taxa de `erro_inicio_processo` (une com item 11) | 2026-09-19 |
| 3 | Classificar `mcp-memory.read_write` no catálogo `effect_classes.py` (arquivo do brief estava errado)? | Sem recomendação — acoplar a ADR-0043 | 2026-09-19 |
| 4 | Aprovar o veículo de PR para o fix já pronto de `check_evidence_ledger_hashes.py` (regex de ids com espaço)? | Sim — abrir a PR agora, a correção já existe em disco | 2026-09-12 |
| 5 | Criar gate de CI nomeado para CC-13 (ADR-P-002, ainda não redigida)? | Baixa urgência — a proteção via pytest geral já roda | 2026-09-19 |
| 6 | Criar gate de CI nomeado para CC-12 + aceitar `task_kind="batch"` em call sites reais? | Acoplar à ratificação de ADR-0044 | 2026-09-19 |
| 7 | Corrigir o mismatch `CAROLINA-FHIR-TOOL-DECLARED-MISMATCH` antes de promover `leitura_phi_clinica`? | Opção 1 — corrigir o `agent.yaml` para `mcp-fhir.read_patient` | 2026-09-19 |
| 8 | Agendar MZO-050+/MZO-080 (adapter de `PopulationFeatureClient`)? | Sem recomendação — depende de data externa de WB.4 | sem prazo |
| 9 | `scheduling` fica L3, cai a L2, ou outro nível — e agendamento direto entra na Fase 1? | Sem recomendação (ADR-0046 é deliberadamente sem recomendação) | sem prazo |
| 10 | Ratificar ADR-0042–0048? | Ratificar 0047/0048 primeiro (só formalização); 0042/0043/0044/0045 têm decisões de mérito reais | 0047/0048: 2026-09-12; demais: 2026-09-19 |
| 11 | Elevar o padrão fail-notify (CC-01) a requisito do `_template` (5 sub-decisões de ADR-0045)? | Aprovar decisões 1 e 5 (já fato consumado); 2/3/4 pedem escolha explícita | 2026-09-19 |
