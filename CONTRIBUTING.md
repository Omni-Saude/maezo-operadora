# CONTRIBUTING — Guia de Desenvolvimento

## Princípios (lições do repo hospitalar — ADR-0011)

1. **Árvore única.** Todo código em `src/maezo/`. Sem cópias, sem `.archive/`, sem `_old`, sem `_bkp` no main — histórico é papel do git.
2. **ADR antes de código.** Decisão arquitetural sem ADR não entra. Use `docs/adr/template.md`.
3. **Sem mock de engine em integração.** Testes `integration` rodam contra CIB Seven/Postgres reais. O repo hospitalar teve 508 testes "passando" enquanto 91 não-conformidades BPMN dormiam.
4. **Artefato é código.** BPMN, DMN, `agent.yaml` e policies passam por `make validate-artifacts` (blocker de CI).
5. **Nenhum SDK de LLM fora de `runtime/inference.py`. Nenhuma credencial fora do gateway.**

## Como adicionar um agente

1. Copie `src/maezo/agents/_template/` → `src/maezo/agents/<id>/` (código: `graph.py` + `__init__.py`).
2. Copie `spec/agents/_template/agent.yaml` → `spec/agents/<id>/agent.yaml` (única fonte — T0.3/B14, sem cópia em `src/`) e preencha: zona de segurança (ADR-0006), allowlist de tools, KPIs, gestor humano, rota de escalonamento. **Todo agente tem gestor humano e rota de escalonamento — sem exceção.**
3. Implemente `graph.py:build` — nós curtos e idempotentes (checkpoint é entre nós).
4. Adicione ações novas à matriz `policies/autonomy/` via PR separado (revisão de compliance obrigatória — CODEOWNERS).
5. Crie golden dataset em `tests/evals/golden/<id>/` ANTES do go-live (mínimo definido por fase).
6. Dashboards: a "ficha de funcionário" do agente (KPIs do `agent.yaml`) é parte da entrega.

## Como adicionar uma tool (MCP server)

1. Novo pacote em `src/maezo/tools/mcp_<nome>/`. Clientes externos maduros podem ser **portados** do repo hospitalar (`shared/integrations/`) — copiar, adaptar, re-testar; nunca importar entre repos.
2. Toda tool declara: ação correspondente na matriz de autonomia, escopo de PHI dos argumentos/retorno (para o pseudonimizador) e schema tipado.
3. Registre no Tool Gateway. Agente nenhum chama a tool sem passar pelo PEP.

## Como adicionar um processo SP-OP

1. Justifique o gatilho regulatório no PR (SLA legal, HITL mandatório, auditoria, multi-ator legal). Sem gatilho → é jornada de agente (AGJ), não BPMN.
2. Modele em `spec/processes/bpmn/` seguindo `SP-OP-{AREA}-{NNN}_{Titulo}.bpmn`; tópicos `{dominio}.{contexto}.{acao}`.
3. Ações L0/L1 exigem User Task com candidate group humano + timer + escalation.
4. Atualize `docs/processes/catalog.md` e `config/topic_registry.yaml`.
5. **Perspectiva: o dono do processo é a operadora (pagador).** Ela RECEBE guia, lote e recurso; ela EMITE demonstrativo, autorização, negativa, glosa e resposta de recurso. Um elemento que interpõe recurso, aguarda a resposta da operadora ou concilia dinheiro recebido descreve o prestador, não o pagador — é inversão (ADR-0040).

### Onde vive a referência histórica (convenção, não marcador)

O portão de regressão de vocabulário de perspectiva (ADR-0040 D7 — `src/maezo/platform/validation/perspective.py`, chamado de dentro de `make validate-artifacts`) lê duas superfícies de formas diferentes:

- **Tier A** — `spec/processes/bpmn/*.bpmn` e `spec/processes/dmn/*.dmn` em **texto bruto, inclusive comentários XML**;
- **Tier B** — os YAML de spec (`spec/processes/dmn/*.yaml`, `spec/agents/*/agent.yaml`, `spec/policies/autonomy/*.yaml`) pelos **nós parseados**; comentário não é nó, e por isso não é lido.

A assimetria é **por formato e universal**, nunca um favor por arquivo. A consequência prática para quem edita spec: **narrativa de deleção não entra em comentário XML de BPMN/DMN.** Registrar ali «este elemento roteava para <valor do recorrente>» reintroduz exatamente o vocabulário que a deleção tirou e derruba o build. Ela vai para um destes dois lugares:

1. a **narrativa de docs** — contrato e test-spec em `docs/processes/`, `docs/review-queue.md`, `docs/evidence-ledger.md`, o ADR, os documentos de auditoria/redesenho — que é onde se procura *por que* um artefato mudou; ou
2. um **comentário do YAML** do manifesto de spec, cuja função é justamente citar o que o artefato *dizia* (`spec/agents/marina/agent.yaml` é o exemplo vivo: o KPI deletado sobrevive só no comentário que registra a deleção).

**Não existe marcador de dispensa** (`<!-- historico: … -->`, `<!-- HISTORICAL-REFERENCE -->`) e ele não deve ser criado: um marcador desses é o allowlist por-arquivo que ADR-0040 D7 recusa pelo nome («Não há allowlist de exceções, nem por arquivo nem por bloco `historico:`»). Criá-lo é decisão do dono, por emenda ao próprio ADR — nunca uma edição da fence. O que um elemento BPMN/DMN já foi está no histórico do git e nos docs acima. Testes que fixam isso: `tests/unit/platform/test_validation_perspective.py::TestNoExceptionMechanism`.

## Convenções

- Python 3.12, `ruff` + `mypy --strict`. Async por padrão.
- Commits: conventional commits (`feat(helena): ...`, `adr: ...`).
- PR pequeno > PR épico. Um agente/tool/processo por PR.
- Datas/prazos regulatórios: sempre em DMN ou BPMN timer — nunca hard-coded em Python.

## Fluxo de release

`main` protegido → CI completo (lint, type, unit, artifact-validation, integration, evals) → tag → imagem única `maezo-agent` + manifests por tenant. Promoção de prompt/modelo segue o mesmo fluxo de release de código (eval gate, ADR-0009).

## Ciclo de trabalho: autoria, publicação, integração e aposentadoria

ROOT é o único responsável pela fila de integração, conflitos em arquivos compartilhados, pushes da branch de entrega e merges. Um coordenador auxiliar pode reconciliar preservação em paralelo; não disputa essas operações. Revisores permanecem independentes. Não criar outro tracker: fila e exceções em `PLANS.md`, decisões em `RUNBOOK.md`, estado de retomada em `CHECKPOINT.md`; inventários detalhados são evidência referenciada por esses artefatos.

### Abrir e trabalhar em um pacote

- Antes de criar branch/worktree, procurar o pacote e seu sucessor na fila existente. Reutilizar um checkout limpo e liberado quando compatível com o papel e o baseline; não criar um novo por resposta, consulta ou revisão estática. Um revisor pode ler o checkout congelado do autor sem modificá-lo. Testes com efeitos locais ou requisitos de isolamento justificam outro checkout.
- Registrar pacote, responsável, papel, paths de autoria, baseline, branch/worktree, próximo gate e condição de liberação. Cada worktree retido exige motivo concreto e responsável; idade ou nome não demonstram abandono.
- Antes de qualquer mutação, conferir `pwd`, `git rev-parse --show-toplevel`, branch, HEAD e `git status --short --branch`. Operar com workdir explícito. Divergência interrompe aquela mutação até reconciliação; nunca mover o ROOT sujo para o candidato.
- Manter até quatro pacotes de implementação, dois aprovados aguardando integração e um candidato em validação ampla. Ao atingir a fila de dois aprovados, priorizar composição e bloqueios de entrega antes de abrir trabalho dependente. Checkouts históricos retidos não contam como implementação ativa, mas não autorizam expansão silenciosa: qualquer novo checkout precisa de propósito e condição de saída.

### Comitar, publicar e revisar

- Criar commits coesos nos limites naturais da entrega e antes de handoff/encerramento. Se trabalho ativo ficar mais de uma hora sem checkpoint, ROOT exige um checkpoint na próxima transição segura: commit dos paths de autoria quando publicáveis, ou preservação local verificada e motivo explícito. Um commit WIP não equivale a aprovação.
- Publicar a branch e abrir PR draft assim que existir um incremento coeso, após verificação de paths e segredos. Não esperar todas as jornadas ou o programa inteiro. Evidência privada, PHI, credenciais e conteúdo deliberadamente local não entram em Git. Separar os estados **preservado localmente**, **publicado**, **aprovado** e **integrado em main**.
- Depois do push, verificar que o ref remoto aponta para o SHA enviado e registrar PR/HEAD. Um comando de push iniciado ou uma branch local não provam publicação. Evitar force-push/rebase de histórico já compartilhado; resolver atualizações por commits explícitos, preservando os SHAs revisados.
- Congelar a revisão em commit/tree, paths e dependências exatos. Preferir ler os objetos Git para comparação histórica; não manter um diretório completo apenas para representar um commit. Quando uma ferramenta exige um path ou ambiente específico, preservar esse pin até substituição qualificada.
- Antes de compor ou fazer merge, atualizar refs sem prune e verificar `origin/main`. Se ele não for ancestral do candidato, integrar sua atualização em checkout de entrega, resolver conflitos com rastreabilidade e revisar/testar os seams alterados. Se já for ancestral, registrar zero atraso; não executar merge/rebase sem necessidade.
- Abrir o próximo PR a partir do main atualizado ou declarar explicitamente uma dependência de PR. Não ampliar indefinidamente um PR congelado: novos recursos ficam no próximo pacote; apenas bloqueios indispensáveis à entrega entram no candidato atual.
- Executar verificações focais durante autoria/reparo; reutilizar resultados imutáveis no escopo válido. Executar CI completo aplicável no candidato, sem repetir cargas pesadas idênticas sem nova hipótese ou mudança. Não reduzir gates para acomodar o cronograma. Aprovações de owner exigidas continuam vinculadas ao HEAD elegível.

### Fechar a retenção, além de inventariar

Cada pacote termina com uma disposição explícita: integrado, substituído por sucessor identificado, pendente de integração ou retido por dependência concreta. O registro aponta o SHA/PR que preserva o código, a evidência externa necessária, o consumidor/pin ainda ativo e o evento que libera o checkout. “Preservar” sem responsável e condição de liberação não encerra a manutenção.

Após cada merge, executar integralmente a seção seguinte. Entre merges, reconciliar por mudanças desde o último inventário, sem confundir um inventário incremental com a verificação completa exigida para remoção. Trabalhar em lotes pequenos de candidatos; cada candidato precisa de verificação atual de atividade, sujeira, conteúdo ignorado exclusivo e contenção antes de remover. Uma recusa de remoção volta à fila com o motivo, sem contornar a proteção.

Commits e evidência preservados não exigem necessariamente o checkout materializado. Liberar primeiro pins de ferramentas e ambientes com sucessores verificados; somente depois usar a remoção conservadora prevista abaixo. Não mover bytes privados para Git para obter uma árvore “limpa”. Não usar prazo de expiração como autorização de descarte.

ROOT revisa a fila a cada integração ou handoff: pacotes publicados mas não integrados, trabalho ainda apenas local, aprovados aguardando composição e retenções cuja condição já mudou. Medir tempo até publicação/main e retenções sem disposição, não o número bruto de diretórios como produtividade. Os limites de fila são controles operacionais, não uma garantia automatizada de ausência de perdas.

## Manutenção obrigatória após merge

Depois de cada merge e da verificação no `main` atualizado, o responsável pelo merge executa este ciclo antes de encerrar o pacote. Uma autorização já dada para merge e manutenção cobre o ciclo; não crie uma aprovação adicional. Uma dispensa explícita de revisão humana não dispensa validação técnica nem a verificação independente por agente distinto do autor exigida pelo plano.

1. Atualize referências sem poda automática (`git fetch --all --no-prune`) e prove o SHA de `main`/`origin/main` com `git rev-parse main origin/main`. Inventarie `git worktree list --porcelain`, refs locais/remotas com `git for-each-ref`, heads remotos exatos com `git ls-remote --heads origin`, e PRs abertos com `gh pr list --state open`. Não mova um checkout sujo para outra revisão.
2. Para cada candidato, registre caminho, branch e SHA; rode `git status --short --branch --untracked-files=all`, inspecione arquivos ignorados com valor exclusivo, e identifique processos, sessões, locks e ferramentas que mantêm o worktree ou seus arquivos como entrada. Worktree com WIP, PR aberto, sujeira rastreada ou não rastreada, evidência ignorada única, tooling pinado ou processo ativo fica preservado.
3. Antes de remover algo, arquive a evidência de recuperação: commits exclusivos, hashes e localização de arquivos não rastreados/ignorados relevantes, vínculo com PR e resultado dos gates. Recupere órfãos em branch isolada; não sobrescreva `main`, não adicione segredo/PHI ao Git e não remova entradas ainda consumidas por ferramentas.
4. Prove contenção com `git merge-base --is-ancestor <tip> origin/main`. Em squash merge, a falha de ancestralidade é esperada e nunca autoriza remoção automática: registre a recuperação semântica explícita contra o SHA do squash (PR, diff/conteúdo e validação relevante) e preserve a branch até essa prova estar arquivada.
5. Remova somente worktree limpo, inativo e já preservado, usando `git worktree remove <caminho>` sem `--force`; depois use apenas `git branch -d <branch>`. Se qualquer comando recusar, preserve e investigue — nunca use `git branch -D`, `git reset`, `git clean` ou poda em massa.
6. Delete uma branch remota somente quando a tarefa já autorizar essa deleção, o ref exato ainda apontar para o SHA inspecionado (`git ls-remote --heads origin refs/heads/<branch>`), esse SHA for ancestral do `origin/main` atualizado e `gh pr list --state open --head <branch>` não retornar PR. Use o lease exato (`git push --force-with-lease=refs/heads/<branch>:<sha> origin :refs/heads/<branch>`); divergência mantém a branch.
7. Ao terminar, atualize referências novamente com `git fetch --all --no-prune` e verifique o SHA e a árvore de `main`, os heads sobreviventes, a lista de worktrees, PRs abertos e toda sujeira restante. Registre inventário, preservações, recuperações, remoções, comandos e resultados nos artefatos existentes `PLANS.md`, `RUNBOOK.md` e `CHECKPOINT.md`; não crie outro tracker.
