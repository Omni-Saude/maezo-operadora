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

**Contexto verificado por comando (re-verificado no fact-check, base `54b0f698` = main `6ffb974`,
que já inclui #344 e #346 — ver `## Errata do fact-check` no fim deste documento).**
- `grep -n "make_.*_handler" src/maezo/runtime/agent_runtime/a2a_composition.py` (base `6ffb974`,
  hoje): **`make_andre_handler`, `make_carolina_handler`, `make_fernando_handler`,
  `make_rafael_handler` são importados/chamados — fernando ENTROU desde que este memo foi
  escrito** (era só andre/carolina/rafael na base `b9cdf245` original). Marina, beatriz, gustavo e
  valentina **têm** `src/maezo/agents/<agente>/delegation.py` com `make_<agente>_handler` pronto
  (`ls src/maezo/agents/*/delegation.py` lista os 9 módulos, um por agente exceto
  andre/carolina/rafael/fernando/helena que já têm caminho próprio), mas nenhum desses quatro é
  importado no composition root hoje.
- A cerca fleet-wide `tests/unit/a2a/test_agent_card_handlers_parity.py::_UNREGISTERED_HANDLERS`
  fixa, na base atual, `frozenset({"marina", "beatriz", "gustavo", "valentina"})` — **já são 4
  agentes, não mais 5** — fernando saiu do conjunto porque registro E call-site de origem
  chegaram juntos (abaixo).
- **PR #344 (`r5/train-3`, MESCLADA em `87b51a8e`, ancestral confirmado de `6ffb974`) já
  registrou `fernando`**: `_DOSSIER_EDGE_AGENT_IDS` agora é `("carolina", "andre", "fernando")`
  (`a2a_composition.py:134`) e `fernando_handler = make_fernando_handler(...)` é construído em
  `a2a_composition.py:906` — citando "owner decision R-081 (gap `FERNANDO-DELEGATION-CALL-SITE`,
  approved 2026-09-04)" no commit `35b5d7e5` ("fix(a2a): guarda RAF-02 em gustavo/marina/valentina
  e registro de fernando"). O call site de ORIGEM (metade que faltava na versão anterior deste
  memo) também chegou, no MESMO PR, por um commit seguinte (`e2deaf16`,
  "feat(inadimplencia): metade de ORIGEM de R-081"): `src/maezo/tools/workers/inadimplencia.py`'s
  `make_prepare_dossier_handler` agora delega de fato para `arrears.followup` via o handler raw-
  async — a linha `FERNANDO-DELEGATION-CALL-SITE` de `docs/evidence-ledger.md` registra as duas
  metades (registro + origem) e marca a de registro `⚠️ SUPERSEDIDO` apontando para a de origem.
- **A mesma PR #344 (commit `35b5d7e5`) também fechou o gap de guarda RAF-02 que este memo, na
  sua primeira versão, ainda descrevia como pendente numa PR aberta**: hoje,
  `src/maezo/agents/{gustavo,marina,valentina}/delegation.py` já levantam `StartProcessFailedError`
  quando `result.get("start_failed") is True` (mesma guarda que `rafael/delegation.py` já tinha),
  confirmado por leitura direta (`grep -n "StartProcessFailedError\|start_failed" src/maezo/agents/
  {gustavo,marina,valentina}/delegation.py`) e por uma cerca fleet-wide nova,
  `tests/unit/agents/test_start_failure_a2a_handlers.py::test_the_guarded_set_is_exactly_the_handlers_whose_graph_starts_a_process`,
  que reapura por AST o conjunto exato de handlers que devem ter essa guarda. **Isso fecha o P1 de
  segurança CC-01/RAF-02 (falso sucesso reportado ao A2A quando o engine recusa iniciar o
  processo) para gustavo/marina/valentina — mas NÃO os registra no composition root**: eles
  continuam com handler pronto e guardado, e sem nenhuma ligação em `a2a_composition.py` hoje.
  Beatriz e helena ficam fora dessa guarda POR ESTRUTURA (`beatriz/graph.py` não registra
  `start_process`; helena origina sem rodar o grafo) — não é uma lacuna, é o desenho.
- Cada um dos quatro `delegation.py` ainda não registrados documenta pré-condições de registro que
  a decisão do dono herdaria — ex.: beatriz precisa que o call site de origem carregue
  `evidencia_refs` por um canal capaz de lista ANTES de qualquer registro (hoje `payload_meta` é
  `Mapping[str, str]`), e `gather_evidence`/`assemble_dossier` compartilham um único `task_id`,
  então o 2º hop é sempre um replay do Guard 4, nunca uma segunda execução.

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

**Contexto verificado por comando (re-verificado no fact-check, base `54b0f698` = main `6ffb974`).**
- Na base ORIGINAL deste memo (`b9cdf245`), `deploy/observability/alert-rules.yml` tinha 3 grupos
  de `alert:` (SLA, crash-loop, dead-letter) + 1 grupo `maezo_lifecycle`; nenhuma regra lia
  `maezo_agent_desfecho_total`.
- **PR #341 (`r5/kpi-lag`) já está MESCLADA** (`mergedAt: 2026-09-05T19:15:45Z`, ancestral
  confirmado de `6ffb974` via #346) — na base atual, `deploy/observability/alert-rules.yml` **já
  tem** o grupo `maezo_agent_kpi_derived` com **3 `record:` (recording rules), zero `alert:`**
  (`grep -n "record:\|alert:" deploy/observability/alert-rules.yml`, linhas 274-301):
  `maezo_helena_resolution_rate`, `maezo_helena_escalation_rate` (ambas
  `resolvido_automatico`/`escalado_humano` sobre o total de helena) e `maezo_lucas_resolution_rate`
  (`resposta_informativa_enviada|lembrete_enviado` sobre o total de lucas). O próprio commit
  message de #341 diz explicitamente "nenhum alert/dashboard novo (D12-02 é escopo separado)".
  Como são `record:`, não `alert:`, **não precisam de `runbook_url`** —
  `scripts/ci/check_alert_runbook_urls.py:16` só gate `alert:` sem `annotations.runbook_url`.
  **A pergunta original deste item permanece aberta mesmo com #341 mesclada**: continua não
  existindo, hoje, nenhuma regra `alert:` sobre `maezo_agent_desfecho_total` — só as 3 `record:`
  acima, que agregam a taxa mas não disparam alarme.
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

**RESOLVIDO desde a escrita original deste memo — sem decisão pendente do dono.** Ver
`## Errata do fact-check` no fim deste documento para a evidência completa. Mantenho a seção
(em vez de apagá-la) para o rastro de auditoria: é assim que a lacuna foi descoberta, medida e
depois fechada dentro do mesmo ciclo.

**Decisão pedida (histórica, já superada pelos fatos).** Aprovar a correção do regex
`_RESULT_LINE_RE` (então `\S+::\S+`) para não descartar silenciosamente linhas PASSED/FAILED cujo
id de teste parametrizado contém espaço.

**Contexto verificado por comando — estado ORIGINAL (base `b9cdf245`, quando este item foi
escrito).**
- `grep -n "_RESULT_LINE_RE\s*=" scripts/ci/check_evidence_ledger_hashes.py`:
  `_RESULT_LINE_RE = re.compile(r"^(\S+::\S+ (?:PASSED|FAILED)(?:\s.*)?)$", re.MULTILINE)` — o lado
  direito do `::` exigia `\S+` (sem espaço), então um id como
  `test_x[RATIFICADO pelo DPO]` nunca casava e a linha inteira era descartada do hash.
- Medido na época: `pytest tests/unit/tools/workers/test_fraude.py -v --tb=no` →
  **148 linhas de resultado, 21 descartadas** pelo regex antigo (127 restavam). `test_programa.py`
  → **137 linhas, 15 descartadas** (122 restavam).
- Na época, a correção existia só como ref local `r5/tooling-fences` (`0d616803`/`505551b8`), sem
  PR e sem CI.

**Contexto verificado por comando — estado ATUAL (base `54b0f698` = main `6ffb974`, fact-check).**
- **A correção JÁ ESTÁ em `origin/main`.** `git merge-base --is-ancestor 0d616803 HEAD` → sim.
  Ela chegou pelo PR #346 (`r5/train-4`, "trem r5/train-4: integra 4 componentes verificados...
  cercas de tooling do ledger", MESCLADA `2026-09-05T21:08:52Z`), que integrou o componente
  `r5/tooling-fences` — o mesmo branch que este memo, na sua versão original, via só como ref
  local sem veículo de revisão.
- `grep -n "_RESULT_LINE_RE\s*=\|LEGACY_NODE_ID_RECIPE_CUTOFF_DATE" scripts/ci/check_evidence_ledger_hashes.py`
  hoje: `_RESULT_LINE_RE = re.compile(r"^(\S+::.+ (?:PASSED|FAILED))(?:\s.*)?$", re.MULTILINE)` —
  o lado direito do `::` agora é `.+` (aceita espaço); `_RESULT_LINE_RE_LEGACY` (a regex antiga,
  preservada verbatim) + `LEGACY_NODE_ID_RECIPE_CUTOFF_DATE = "2026-09-06"` cobrem, com fallback
  explícito (`recipe_version=legacy`, nunca silencioso), qualquer linha do ledger datada ANTES do
  corte — nenhum hash já declarado foi invalidado retroativamente.
- **Remedi eu mesma, hoje, a mesma suíte**: `test_fraude.py` → 148 linhas de resultado, **148
  casam com o regex NOVO** (0 descartadas; eram 21 descartadas com o regex antigo) — confirmado
  por script Python separado que aplica os dois regexes à mesma captura.
- `docs/evidence-ledger.md` já tem a linha `LEDGER-HASH-PARAM-IDS-WITH-SPACES` (autor
  `tooling-fences-implementer`, sonnet R2, branch `r5/tooling-fences`) descrevendo exatamente esta
  correção, mais a linha-irmã de divulgação `LEDGER-HASH-RECIPE-CHANGE-2026-09-05` (lista as 11
  linhas pré-existentes cujo hash recomputado mudou — nenhuma foi editada, todas continuam
  verificáveis pelo fallback legado) e a linha `LEDGER-ROW-CELL-COUNT` (gate-irmão novo,
  `make check-ledger-row-cell-count`, também já em `main`).

**Não há mais opções a decidir neste item** — a correção já passou pelo processo normal do
programa (commit, teste RED→GREEN, ledger, integração via trem, merge em `main`) sem necessidade
de uma decisão avulsa do dono. As três opções originais ("aguardar PR da irmã" / "pedir PR agora" /
"implementar de novo") ficam registradas acima só como contexto histórico de como a lacuna foi
tratada.

**Arquivos CODEOWNED envolvidos.** `scripts/ci/check_evidence_ledger_hashes.py` (`scripts/ci/` é
CODEOWNED) — já mesclado via PR normal do programa (#346), não uma pendência.

**O que já está pronto no código (símbolo).** `scripts/ci/check_evidence_ledger_hashes.py::{_RESULT_LINE_RE,
_RESULT_LINE_RE_LEGACY, LEGACY_NODE_ID_RECIPE_CUTOFF_DATE}` — em `main`, funcionando, testado.

**O que fica bloqueado até a decisão.** Nada — não há decisão pendente.

**Prazo sugerido.** Nenhum — item fechado.

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
     **Correção do fact-check (era "só rafael" na versão original; hoje são 7 dos 7 agentes que
     de fato iniciam processo via A2A).** Confirmado por `grep -c StartProcessFailedError
     src/maezo/agents/*/delegation.py`: `andre` e `carolina` (já a tinham antes deste ciclo),
     `rafael` (linhas 171-179, como o memo original já citava), e — chegados pelo PR #344
     (commit `35b5d7e5`, mesmo achado do item 1) — `gustavo`, `marina` e `valentina` também
     levantam `StartProcessFailedError` em vez de devolver `HandlerOutput` de sucesso quando
     `start_failed is True`. `fernando` chegou a um padrão equivalente pelo mesmo PR (metade de
     origem, commit `e2deaf16`). `beatriz` e `helena` ficam de fora POR ESTRUTURA — nenhuma das
     duas registra `start_process` no próprio grafo (beatriz nunca inicia processo pelo `graph.py`;
     helena origina sem rodar grafo) — não é uma lacuna, é o desenho; portanto a decisão 4 do ADR
     já está, na prática, cumprida para os 7 agentes aos quais ela se aplica (incluindo fernando).
     A cerca fleet-wide `tests/unit/agents/test_start_failure_a2a_handlers.py::test_the_guarded_set_is_exactly_the_handlers_whose_graph_starts_a_process`
     (nova, chegada pelo mesmo PR) reapura esse conjunto por AST, então uma regressão futura (um
     8º agente que inicie processo sem a guarda) quebraria essa cerca antes de chegar a produção.
  5. Adicionar `erro_inicio_processo` à enumeração de desfechos dos contratos `SP-OP-*` —
     **já feito em 10 contratos** (ver acima); os que faltam (se algum agente que inicia processo
     ainda não tiver a linha) ficariam pendentes de auditoria específica.

**Opções.** Aprovar as 5 decisões em bloco, parcialmente (ex.: só formalizar 1, 4 e 5, que já são
fato consumado na prática — correção do fact-check: a decisão 4 também já é fato consumado para
os 7 agentes aplicáveis, não só rafael — deixando 2/3 para depois), ou não aprovar (manter o
padrão como "boa prática não obrigatória").

**Arquivos CODEOWNED envolvidos.** `docs/adr/0045-...md`; se a decisão 3 escolher `scripts/ci/`,
também esse diretório + `Makefile`.

**O que já está pronto no código (símbolo).** `src/maezo/runtime/start_outcome.py::{route_after_start,
notify_start_failure, StartProcessFailedError, DESFECHO_ERRO_INICIO_PROCESSO}`;
`_template/graph.py`'s próprio esqueleto; `StartProcessFailedError` na fronteira A2A de
`{andre,carolina,rafael,fernando,gustavo,marina,valentina}/delegation.py` (correção do
fact-check — eram só rafael citado na versão original deste memo).

**O que fica bloqueado até a decisão.** A obrigatoriedade formal (decisão 1) e a política de
retry/alerta (decisão 2) — sem elas, um agente novo que copiar um `graph.py` antigo (não o
`_template` atual) ainda poderia reintroduzir o fail-open original.

**Prazo sugerido.** 2026-09-19 (mesma janela do item 10).

---

## 12. `solicitacao_humano` (Lucas) — desbloqueado só pela ratificação das DMNs DRAFT (LUC-03/LUC-04)

**Item novo, surgido na verificação do ciclo 2 — não estava na versão original deste memo.**
Achado de um work package irmão em andamento (`CONTRACT-DRIFT`, branch local
`fleet2/contract-drift`, base `6ffb974` — **ainda NÃO mesclado em `main`**, confirmado por
`git merge-base --is-ancestor 44b018f4 HEAD` → não), citado aqui porque a pergunta de governança
que ele expõe é real e não depende de aquele PR específico ser aprovado.

**Decisão pedida.** As duas tabelas DMN de roteamento de Lucas
(`spec/processes/dmn/lucas_billing_admissibility.dmn`,
`spec/processes/dmn/lucas_escalation_routing.dmn`) estão em `status: DRAFT — requires human review
(financeiro/PO)` desde antes deste ciclo (achado LUC-03 do relatório de auditoria), mas o grafo já
as usa como autoridade de roteamento em produção. Ratificar (ou não) essas duas tabelas é o que
libera — ou não — o valor `solicitacao_humano` a ser roteado corretamente quando um beneficiário
pede para falar com um humano.

**Contexto verificado por comando.**
- `grep -n status spec/processes/dmn/lucas_billing_admissibility.dmn
  spec/processes/dmn/lucas_escalation_routing.dmn` (base `6ffb974`, hoje): ambas seguem
  `v0.1.0 — status: DRAFT — requires human review (financeiro/PO)`.
- `grep -n solicitacao_humano src/maezo/agents/lucas/graph.py` → `MotivoCategoria = Literal["outro",
  "solicitacao_humano", "falha_tecnica"]` (linha 230) — o valor É um valor real e vivo do domínio
  compartilhado `SP-OP-ESCALATION-001` (helena o emite corretamente para o mesmo caso), mas
  `Intencao` (o campo de entrada do CALLER, não de Lucas) não tem nenhuma variante "beneficiário
  pede um humano" — então esse pedido é hoje misclassificado como `ambiguidade->outro`.
- O work package irmão investigou se dava para ligar isso sem tocar as DMNs DRAFT e concluiu que
  NÃO: o catch-all das DMNs absorveria um novo valor com segurança, mas a peça que falta
  (`Intencao`) é um campo de ENTRADA cujo domínio é produzido por um classificador upstream que
  nenhum contrato documenta hoje (`grep` por `intencao` em `docs/processes/contracts/*.md` — 0
  hits) — um segundo gap, não documentado, fora do escopo de qualquer WP atual.
- Dado o impasse, o WP irmão optou por só DIVULGAR o gap no código (comentário em
  `lucas/graph.py`, commit local `44b018f4`, "disclosa solicitacao_humano como owner-gated até
  ratificação"), citando a mesma ratificação LUC-03 já pedida pela auditoria — sem inventar um
  classificador nem remover o valor do domínio compartilhado.

**Opções.**
1. **Ratificar as duas DMNs como estão** (financeiro/PO assina `status: RATIFICADO`, sem mudança
   de lógica) — libera a base para depois, separadamente, desenhar o classificador de `Intencao`
   que faltaria para `solicitacao_humano` ser alcançável.
2. **Não ratificar agora** — Lucas continua operando com as DMNs DRAFT como autoridade de fato
   (mesma situação de hoje), e `solicitacao_humano` continua estruturalmente inalcançável.
3. **Ratificar E encomendar, no mesmo pacote, o desenho do classificador de `Intencao`** que falta
   para o valor ser de fato roteável — maior escopo, mas resolve as duas metades do gap de uma vez.

**Arquivos CODEOWNED envolvidos.** Nenhum dos três arquivos citados
(`spec/processes/dmn/lucas_billing_admissibility.dmn`,
`spec/processes/dmn/lucas_escalation_routing.dmn`, `src/maezo/agents/lucas/graph.py`) está em
`.github/CODEOWNERS` hoje.

**O que já está pronto no código (símbolo).** `src/maezo/agents/lucas/graph.py::MotivoCategoria`
(o valor `solicitacao_humano` já existe, tipado, documentado como intencionalmente inalcançável).

**O que fica bloqueado até a decisão.** Qualquer beneficiário que peça explicitamente falar com um
humano continua sendo roteado como `ambiguidade->outro` em vez de uma categoria própria, até a
ratificação (e o classificador de `Intencao`, se a opção 3 for escolhida).

**Prazo sugerido.** Mesma janela do LUC-03 original da auditoria — sem urgência adicional
introduzida por este memo; sugiro alinhar com o próximo ciclo de revisão financeiro/PO.

---

## 13. Persona de Carolina DRAFT + registro NPI fora da allowlist (CAR-08)

**Item novo, surgido na verificação do ciclo 2.** Achado P3 pré-existente do relatório de
auditoria (dimensão 10, capacidade), confirmado ainda válido nesta base.

**Decisão pedida.** Duas questões de produto distintas no mesmo agente: (a) assinar a persona de
Carolina (hoje DRAFT, pendente de PO); (b) decidir se/quando o MCP server do registro de
prestadores (NPI) deve existir, o que é pré-condição para trazer consulta ao registro para dentro
do `gather` de Carolina.

**Contexto verificado por comando.**
- `sed -n '1,2p' spec/agents/carolina/agent.yaml` (base `6ffb974`, hoje): `# Carolina — Analista de
  Credenciamento/rede (Phase 3 analogue of Marina/Rafael)` / `# PERSONA DRAFT (R-PERSONA-MAP — PO
  sign-off pendente).`
- `grep -n "NPI\|PORT-PENDING" spec/agents/carolina/agent.yaml` → linha 25: "memoria. NPI registry
  mcp e PORT-PENDING -> fica FORA da allowlist ate um no do grafo o [exercer]" — o MCP server do
  registro de prestadores **não existe em `src/` hoje**; não há nada para ligar na allowlist mesmo
  que a spec fosse reaberta agora.
- Este é um gap de evolução de capacidade, não um defeito de código: a identidade da persona é
  contratualmente DRAFT por decisão de produto pendente, e o servidor MCP do registro NPI está
  fora do repositório.

**Opções.**
1. **Assinar a persona agora** (PO ratifica nome/identidade de Carolina) independentemente do
   registro NPI — desacopla as duas decisões.
2. **Adiar ambas** — Carolina continua operando com persona DRAFT e sem consulta ao registro de
   prestadores; nenhum comportamento muda.
3. **Priorizar o MCP server do registro NPI no roadmap** (fora deste programa — depende de
   fornecedor/infra externa) antes de decidir a persona, já que o ganho de capacidade real
   (consultar o registro no `gather`) depende dele de qualquer forma.

**Arquivos CODEOWNED envolvidos.** Nenhum — `spec/agents/carolina/agent.yaml` não está em
`.github/CODEOWNERS`.

**O que já está pronto no código (símbolo).** Nada a "pronto" aqui — é puramente uma decisão de
produto/roadmap; o `agent.yaml` já documenta honestamente as duas pendências (comentários citados
acima).

**O que fica bloqueado até a decisão.** A identidade final de Carolina (nome/persona) e a consulta
ao registro de prestadores no fluxo de credenciamento.

**Prazo sugerido.** Sem prazo — depende de decisão de produto (persona) e de uma dependência
externa (MCP server NPI), nenhuma das duas com custo de atraso técnico mensurável hoje.

---

## 14. `.gitleaksignore` — duas entradas cuja condição de remoção declarada nunca vai se cumprir (REG-07)

**Item novo, surgido na verificação do ciclo 2.** Achado de um work package irmão já concluído
(`FENCE-PINS`, branch `fleet2/fence-pins`) que investigou — sem alterar — uma divulgação existente
no arquivo.

**Decisão pedida.** Duas entradas de `.gitleaksignore` (fingerprints `6615de0.../tests/evals/
test_dossier_adverse_evals.py` e `d3c5ac1.../.gitleaksignore`) trazem, em comentário, a condição
de remoção "remover esta entrada assim que a branch for mesclada e apagada". Essa condição já não
pode mais se cumprir do jeito que foi escrita — pedir ao dono que ratifique manter as duas
entradas permanentemente, ou que reescreva o comentário para não prometer uma remoção que nunca
vai acontecer.

**Contexto verificado por comando.**
- `git merge-base --is-ancestor 6615de0 HEAD` e `git merge-base --is-ancestor d3c5ac1 HEAD` (base
  `6ffb974`, hoje) → **ambos SIM** — os dois commits são ancestrais permanentes de `main`.
- `git ls-remote origin 'refs/heads/fleet/and02-metrics-egress-gate'` → vazio, a branch de origem
  **foi de fato apagada**, como o comentário previa.
- Mas `git log --format='%H %P' -1 75ed74f` (PR #319, `fleet/train-w3-lote3`, mesclado
  `2026-09-05T06:10:15Z`) mostra **dois pais** (`abb9d60b... 40ac69ca...`) — um merge commit
  GENUÍNO, não squash. Isso significa que os dois commits do fingerprint continuam para sempre
  como ancestrais de `main`, **mesmo com a branch original apagada** — a condição de remoção do
  comentário ("assim que a branch for mesclada e apagada") já se cumpriu pela metade (mesclada +
  apagada) mas os fingerprints continuam necessários porque o gitleaks escaneia todo o histórico,
  não só a ponta.
- O WP irmão `FENCE-PINS` já tinha investigado isso empiricamente (sem tocar o arquivo): rodar
  `gitleaks detect` sobre o histórico completo com as duas entradas removidas faz os dois
  fingerprints reaparecerem verbatim ("leaks found: 2") — confirmando que continuam necessários
  hoje, não são lixo esquecido.

**Opções.**
1. **Ratificar formalmente que as duas entradas ficam permanentes** — reescrever o comentário para
   não prometer uma remoção condicionada a "branch apagada" (condição que, para um merge de
   2-pais, nunca isola os commits de `main`), documentando a razão real (merge genuíno, não
   squash).
2. **Reescrever a história do commit `6615de0`** (única edição local de histórico, fora das regras
   de operação normais do programa que proíbem rebase/reset/amend em branches de trabalho) —
   eliminaria a necessidade do fingerprint, mas é uma operação sensível sobre histórico já
   publicado.
3. **Deixar como está** — as entradas continuam funcionando (o achado não é uma falha de segurança,
   é uma imprecisão de comentário), só o texto da condição de remoção fica descasado da realidade.

**Arquivos CODEOWNED envolvidos.** Nenhum — `.gitleaksignore` não está listado em
`.github/CODEOWNERS` (diferente de `.gitleaks.toml`, que é CODEOWNED).

**O que já está pronto no código (símbolo).** As duas entradas já existem e já funcionam
(`.gitleaksignore`, linhas citadas nos comentários "AND-02" do arquivo); nada precisa ser
construído — a pergunta é só sobre o texto do comentário e a expectativa que ele cria.

**O que fica bloqueado até a decisão.** Nada tecnicamente — é uma correção de precisão
documental, sem efeito em nenhum gate.

**Prazo sugerido.** Baixa urgência — sugiro agrupar com a próxima revisão de `.gitleaks.toml`/
`.gitleaksignore`, sem data própria.

---

## 15. Prosa vs. campo estruturado para escopo de skill/contrato — estender o precedente do `a2a.handler_status`?

**Item novo, surgido na verificação do ciclo 2.** Não é um achado de defeito — é uma pergunta de
padrão de governança que um work package irmão já resolveu para UM caso e que pode valer a pena
repetir para outros dois.

**Decisão pedida.** Um work package irmão (`A2A-YAML-DISCLOSURE`, branch local
`fleet2/a2a-yaml-disclosure`, base `6ffb974` — **ainda NÃO mesclado em `main`**, confirmado por
`git merge-base --is-ancestor 981d2e8b HEAD` → não, e por `grep -n handler_status
spec/agents/*/agent.yaml` na base atual → 0 hits) substituiu, para o status do handler A2A de cada
agente, uma frase de prosa (que podia negar ou omitir um handler já registrado, sem nenhum
validador conferir) por um campo estruturado obrigatório e validado,
`a2a.handler_status: {ausente|pronto_sem_registro|registrado}` + `handler_symbol`, cruzado por
cerca contra a verdade real do código (`delegation.py` + registro em `a2a_composition.py`). A
pergunta para o dono: vale a pena pedir o mesmo tratamento para outras duas divulgações que hoje
também são só prosa/comentário, não campo validado?

**Contexto verificado por comando.**
- Candidato 1 — **`docs/processes/contracts/*.md`**: as seções "Variáveis de proveniência do
  agente" (CC-13, ver item 5 deste memo) são texto em markdown, sem parser/validador que confira
  se o agente realmente escreve exatamente essas chaves (a fence que existe,
  `test_contract_provenance_parity.py`, roda no lado do TESTE, não como um campo do próprio
  contrato).
- Candidato 2 — **`spec/agents/*/agent.yaml`'s `a2a.skills`** (LUC-13/LUC-14, achado do mesmo WP
  `CONTRACT-DRIFT` citado no item 12): o escopo real de `collection_nudge`/`boleto_2via` de Lucas
  hoje só é divulgado por um COMENTÁRIO inline no yaml (`spec/agents/lucas/agent.yaml`), pinado
  por uma cerca nova (`tests/unit/platform/test_lucas_skill_scope_disclosures.py`) que lê o
  comentário como string — não um campo estruturado que um schema valide. Comentário YAML não é
  lido por nenhum carregador (`AgentDefinition`), então essa divulgação é invisível para qualquer
  código que não seja o teste específico que a pina.
- O padrão do candidato 1/2 é estruturalmente o MESMO que motivou `a2a.handler_status`: uma
  afirmação em prosa que pode divergir silenciosamente do comportamento real, corrigida ali por um
  campo tipado + cerca cruzada, não por mais um comentário.

**Opções.**
1. **Estender o padrão de campo estruturado** para escopo de skill (`a2a.skills` ganha um
   `scope:` tipado por skill, ex. `reativo`/`fim-a-fim`/`informativo`) e para as tabelas de
   proveniência dos contratos (um bloco YAML/front-matter em vez de só uma tabela markdown) —
   maior consistência entre WPs, mas mais schema/validador para manter.
2. **Manter comentário/prosa para esses dois casos** — aceitar que são divulgações de menor risco
   (P2/P3, não um P1 de segurança como o handler A2A) e que uma cerca de teste já pina o texto,
   mesmo sem ser um campo de schema.
3. **Padronizar caso a caso** — só migrar para campo estruturado quando um novo WP tocar aquele
   arquivo de qualquer forma (não abrir um WP só para a migração de formato).

**Arquivos CODEOWNED envolvidos.** Nenhum dos arquivos citados (`docs/processes/contracts/*.md`,
`spec/agents/*/agent.yaml`) está em `.github/CODEOWNERS` hoje.

**O que já está pronto no código (símbolo).** O PRECEDENTE já existe (não neste repositório
`main` ainda, mas como WP irmão pronto): `src/maezo/platform/validation/agent_def.py::
_validate_a2a_handler_disclosure` + `_HANDLER_STATUS_VALUES`, e a cerca
`tests/unit/a2a/test_agent_card_handlers_parity.py::test_declared_handler_status_matches_the_delegation_and_registration_truth`.

**O que fica bloqueado até a decisão.** Nada tecnicamente hoje — é uma pergunta de padrão a
resolver antes que mais WPs pinem divulgações por comentário/prosa em vez de campo estruturado.

**Prazo sugerido.** Sem prazo — sugiro decidir junto com a revisão do PR do `A2A-YAML-DISCLOSURE`
quando ele for aberto, para que o padrão (se aprovado) já nasça no mesmo formato.

---

## Lista de decisões

| id | Pergunta em uma frase | Opção recomendada pela engenharia (quando houver) | Prazo |
|---|---|---|---|
| 1 | Registrar marina/beatriz/gustavo/valentina (e `care.enroll`) no composition root A2A, e atualizar `_UNREGISTERED_HANDLERS`? | Sem recomendação — decisão de exposição de superfície, não técnica | 2026-09-19 |
| 2 | Criar `alert:` sobre `maezo_agent_desfecho_total` com `runbook_url`? | Opção 2 — alertar sobre a taxa de `erro_inicio_processo` (une com item 11) | 2026-09-19 |
| 3 | Classificar `mcp-memory.read_write` no catálogo `effect_classes.py` (arquivo do brief estava errado)? | Sem recomendação — acoplar a ADR-0043 | 2026-09-19 |
| 4 | ~~Aprovar o veículo de PR para o fix de `check_evidence_ledger_hashes.py`?~~ **RESOLVIDO** — já mesclado em `main` via PR #346, nenhuma decisão pendente | N/A — já feito | fechado |
| 5 | Criar gate de CI nomeado para CC-13 (ADR-P-002, ainda não redigida)? | Baixa urgência — a proteção via pytest geral já roda | 2026-09-19 |
| 6 | Criar gate de CI nomeado para CC-12 + aceitar `task_kind="batch"` em call sites reais? | Acoplar à ratificação de ADR-0044 | 2026-09-19 |
| 7 | Corrigir o mismatch `CAROLINA-FHIR-TOOL-DECLARED-MISMATCH` antes de promover `leitura_phi_clinica`? | Opção 1 — corrigir o `agent.yaml` para `mcp-fhir.read_patient` | 2026-09-19 |
| 8 | Agendar MZO-050+/MZO-080 (adapter de `PopulationFeatureClient`)? | Sem recomendação — depende de data externa de WB.4 | sem prazo |
| 9 | `scheduling` fica L3, cai a L2, ou outro nível — e agendamento direto entra na Fase 1? | Sem recomendação (ADR-0046 é deliberadamente sem recomendação) | sem prazo |
| 10 | Ratificar ADR-0042–0048? | Ratificar 0047/0048 primeiro (só formalização); 0042/0043/0044/0045 têm decisões de mérito reais | 0047/0048: 2026-09-12; demais: 2026-09-19 |
| 11 | Elevar o padrão fail-notify (CC-01) a requisito do `_template` (5 sub-decisões de ADR-0045)? | Aprovar decisões 1, 4 e 5 (já fato consumado — correção do fact-check: 4 também já vale para 7 agentes, não só rafael); 2/3 pedem escolha explícita | 2026-09-19 |
| 12 | Ratificar `lucas_billing_admissibility.dmn`/`lucas_escalation_routing.dmn` (LUC-03), o que também desbloqueia `solicitacao_humano` (LUC-04)? | Sem recomendação — decisão financeiro/PO | sem prazo próprio (alinhar com LUC-03) |
| 13 | Assinar a persona de Carolina (R-PERSONA-MAP) e/ou priorizar o MCP server do registro NPI? | Sem recomendação — decisão de produto/roadmap | sem prazo |
| 14 | Ratificar as duas entradas permanentes de `.gitleaksignore` (REG-07) ou reescrever o comentário de condição de remoção? | Sem recomendação — opções + consequências no item 14 | sem prazo |
| 15 | Estender o padrão `a2a.handler_status` (campo estruturado) para escopo de skill e proveniência de contrato? | Sem recomendação — pergunta de padrão de governança | sem prazo |

---

## Errata do fact-check (main `6ffb974`)

**Autor:** `owner-memo-factchecker` (agente, R2) — agente DIFERENTE de quem escreveu a versão
original deste memo. **Base do fact-check:** worktree
`/Users/familia/code/maezo-fleet2-wt/owner-memos`, branch `fleet2/owner-memos`, tip `54b0f698`
(merge do memo original, commit `1a69e7b6`, escrito sobre `b9cdf245`, com `origin/main` em
`6ffb974` — que já inclui os PRs #344 e #346, ambos mesclados DEPOIS que o memo original foi
escrito). Todo comando abaixo foi executado de novo, nesta base, por mim — nenhuma reafirmação
sem reexecução. Método: para cada uma das 11 seções originais + a tabela de fechamento, re-rodei
os comandos citados e comandos adicionais que a alegação exigia; classifico cada bloco como
VERIFIED (segue verdadeiro, sem edição), STALE (verdadeiro na época, hoje desatualizado — editei
no lugar) ou WRONG (a alegação nunca foi correta — não encontrei nenhum caso WRONG novo além dos
dois que a própria versão original já tinha se corrigido, itens 2 e 3, que reverifiquei como
ainda corretos).

| # | Item do memo | Veredito | Comando(s) chave | O que mudou / evidência |
|---|---|---|---|---|
| 1 | A2A — registro de fernando + guardas RAF-02 | **STALE → corrigido** | `grep -n "make_.*_handler" src/maezo/runtime/agent_runtime/a2a_composition.py`; `sed -n '90p' tests/unit/a2a/test_agent_card_handlers_parity.py`; `grep -n StartProcessFailedError src/maezo/agents/{gustavo,marina,valentina}/delegation.py` | PR #344 (mesclado `87b51a8e`, ancestral de `6ffb974`) registrou `fernando` (`a2a_composition.py:93,134,906`) e o call site de origem (`e2deaf16`); `_UNREGISTERED_HANDLERS` já é `{marina,beatriz,gustavo,valentina}` (4, não 5) na base atual — o memo original ainda descrevia isso como uma PR aberta (#342) prevendo o resultado. Guardas RAF-02 em gustavo/marina/valentina TAMBÉM já landaram no mesmo PR (commit `35b5d7e5`) — o memo original via isso como pendente. |
| 2 | Alert rule sobre `maezo_agent_desfecho_total` — status de PR #341 | **STALE → corrigido** | `gh pr view 341 --json state,mergedAt`; `grep -n "record:\|alert:" deploy/observability/alert-rules.yml` | PR #341 (`r5/kpi-lag`) estava "aberta" no memo original; hoje está MESCLADA (`mergedAt: 2026-09-05T19:15:45Z`, ancestral de `6ffb974` via #346) e as 3 `record:` já estão em `alert-rules.yml` linhas 274-301. A pergunta de fundo (nenhuma regra `alert:` sobre o desfecho) continua aberta — só o status da PR-fonte mudou. Correção de rótulo "VAL-08" do memo original: RE-VERIFICADA, continua correta (`grep -n VAL-08` no relatório de auditoria confirma que é um achado diferente, sobre o grafo de Valentina não rodar em produção). |
| 3 | `mcp-memory.read_write` — arquivo correto é `effect_classes.py`, não `process_allowlist.py` | **VERIFIED** | `sed -n '1,60p' src/maezo/gateway/effect_classes.py` (linha 38, "KNOWN GAP, DISCLOSED"); `find . -iname process_allowlist.yaml` (0 hits); `ls src/maezo/policies/` (não existe) | Nenhuma mudança — a correção de arquivo que o memo original já tinha feito continua válida byte a byte nesta base. |
| 4 | `check_evidence_ledger_hashes.py` — regex de ids com espaço | **STALE → corrigido (item inteiro reescrito)** | `git merge-base --is-ancestor 0d616803 HEAD` (SIM); `grep -n "_RESULT_LINE_RE\s*=" scripts/ci/check_evidence_ledger_hashes.py`; medição própria: 148/148 linhas de `test_fraude.py` casam com o regex novo (eram 127/148 com o antigo) | O memo original descrevia a correção como existindo só numa ref local sem PR (`r5/tooling-fences`, `0d616803`). Ela chegou a `main` pelo PR #346 (`r5/train-4`, mesclado `2026-09-05T21:08:52Z`) — a "decisão pedida" original (qual veículo de PR usar) está resolvida: o veículo normal do programa já resolveu. Reescrevi a seção inteira mantendo o histórico para rastro de auditoria e marcando RESOLVIDO. `docs/evidence-ledger.md` já tem as linhas `LEDGER-HASH-PARAM-IDS-WITH-SPACES` + `LEDGER-HASH-RECIPE-CHANGE-2026-09-05` + `LEDGER-ROW-CELL-COUNT` documentando a correção e as 11 linhas pré-existentes cujo hash recomputado mudou (nenhuma editada, todas com fallback `LEGACY_NODE_ID_RECIPE_CUTOFF_DATE`). |
| 5 | CC-13 sem gate de CI nomeado | **VERIFIED** | `uv run python -m pytest tests/unit/agents/test_contract_provenance_parity.py -q` → 9 passed; `grep -n "run: make" .github/workflows/ci.yml` (nenhum passo nomeado para este teste, ainda) | Sem mudanças. `.github/workflows/ci.yml` ganhou passos nomeados novos (`check-plans-counts`, `check-ledger-row-cell-count`, do PR #346) mas nenhum deles é para CC-13. |
| 6 | CC-12 sem gate de CI nomeado, `task_kind="batch"` | **VERIFIED** | `uv run python -m pytest tests/unit/agents/test_llm_calls_declare_task_kind.py -q` → 26 passed; `grep -n MODEL_TASK_KINDS src/maezo/runtime/inference/__init__.py` (linha 282, catálogo de 3 inalterado) | Sem mudanças. |
| 7 | `CAROLINA-FHIR-TOOL-DECLARED-MISMATCH` | **VERIFIED** | Reproduzi o probe ao vivo: `decide_effect(...)` para carolina → `False False TOOL_NAO_DECLARADA`; para andre → `False False MANIFESTO_NAO_RATIFICADO` | Idêntico ao que o memo original documentou; `carolina/graph.py:512` continua chamando `read_patient`, `agent.yaml:20` continua declarando `read_patient_summary`. |
| 8 | `PopulationFeatureClient` (WB.4) | **VERIFIED** | `grep -n "class PopulationFeatureClient" src/maezo/agents/andre/graph.py` (linha 426); `grep -n "BLOCKED(external WB.4)" src/maezo/runtime/agent_runtime/a2a_composition.py` | Sem mudanças. |
| 9 | HEL-11 / ADR-0046 | **VERIFIED** | `cat docs/adr/0046-memo-agendamento-direto-helena.md`; `grep -n scheduling spec/policies/autonomy/L0-core.yaml` → `L3` | Sem mudanças. |
| 10 | Ratificação ADR-0042–0048 | **VERIFIED** | `for f in docs/adr/004[2-8]*.md; do grep -m1 "^\*\*Status" "$f"; done` (todas `DRAFT/verify` ou `MEMO`); `DEFAULT_MIN_K_ANONYMITY` = 1 (`andre/graph.py:343`); PRs #320/#335 confirmados ancestrais de `6ffb974` | Sem mudanças de status; todos os fatos numéricos/símbolos re-derivados batem. |
| 11 | CC-01 → requisito do `_template` | **STALE → corrigido (decisão 4)** | `grep -c StartProcessFailedError src/maezo/agents/*/delegation.py` | O memo original citava só `rafael` para a decisão 4 (fronteira A2A). Hoje: `andre`, `carolina`, `rafael`, `fernando`, `gustavo`, `marina`, `valentina` (7 agentes) já levantam `StartProcessFailedError`; `beatriz`/`helena` ficam de fora por estrutura (não iniciam processo pelo grafo). Atualizei a seção e a linha 11 da tabela de fechamento. |
| 12–15 | Itens novos (não existiam no memo original) | **NOVO** | ver seções 12–15 acima | Ver evidência em cada seção; 12/13/14 citam achados de work packages irmãos (dois ainda não mesclados em `main` — 12/15 citam branches locais `fleet2/contract-drift`/`fleet2/a2a-yaml-disclosure`, confirmado por `git merge-base --is-ancestor <sha> HEAD` → não, em ambos os casos; 14 cita um WP já concluído, `fleet2/fence-pins`, cujo achado (REG-07) foi reproduzido de forma independente aqui via `git log --format='%H %P' -1 75ed74f` — dois pais, merge genuíno). |

**Gates re-executados nesta base (fact-check, sem alterar código/spec/testes — só prosa em
`docs/reviews/`):**
```
$ env -u VIRTUAL_ENV make validate-artifacts
[validate] OK — 0 errors, 0 notice(s).
```
`docs/reviews/` não é escaneado por nenhum gate de CI (confirmado: nenhum `check-*` do Makefile
cita esse diretório) — o resultado acima é idêntico ao que já valia antes desta edição, como
esperado para uma mudança que toca só um arquivo markdown fora do escopo de qualquer validador.

Este errata não altera a autoria das 11 seções originais — cada correção fica embutida no lugar
(marcada inline como "correção do fact-check" onde relevante) para que quem já leu a v1 do memo
veja exatamente o que mudou sem precisar comparar diffs.
