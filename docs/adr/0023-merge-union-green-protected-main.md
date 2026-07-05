# ADR-0023: Politica de merge — main protegido com required status checks + strict up-to-date; NAO merge queue

**Status:** Proposed · **Data:** 2026-07-04 · **Area:** DevOps / CI / Governanca de merge

## Contexto

O branch `main` esta **sem protecao**: `GET /repos/Omni-Saude/Maezo-Healthcare-Plan/branches/main/protection` responde `404 Branch not protected` e `GET .../rulesets` responde `[]`. Qualquer push que fique verde localmente entra em `main` sem gate de servidor.

A superficie de CI/CD sao dois workflows: `ci.yml` (9 jobs) e `cd.yml` (4 jobs) = 13 checks nominais. Tres fatos operacionais moldam a decisao:

1. **Merge-ref-staleness (a classe de defeito "pairwise-green != union-green").** O CI de um PR roda no **merge-ref do momento do push** e **NAO** re-roda quando `main` avanca antes do merge. Dois defeitos de uniao chegaram ao `main` exatamente assim — `#137x#144` e `#157x#155`, ambos no MESMO arquivo na mesma sessao (registrado em **DL-0023**). A licao operacional de DL-0023 foi *manual*: "re-validar lanes verificados-antigos contra `main` FRESCO antes de mergear quando `main` mudou no mesmo arquivo". Regra manual = falha por esquecimento; foi violada duas vezes.

2. **`concurrency` + nightly reescrevem veredito por-SHA.** `ci.yml:11-13` usa `group: CI-${{ github.ref }}` com `cancel-in-progress: true` — qualquer push a `main` **cancela** o run em voo da tip. E o cron noturno (`ci.yml:7-8`, 06:00 UTC) re-roda a suite sobre o **mesmo SHA** da tip e **substitui** os vereditos de check-run daquele SHA. Consequencia: um assert de "main verde" que consulte check-runs por SHA pode ler o veredito do **noturno** (ou de um run **cancelado**), nao o do **evento de push** que realmente validou a arvore. Qualquer guarda de union-green tem de **fixar o run do evento push**, nao os check-runs do SHA.

3. **O classifier de two-party-review estaciona PRs do proprio orquestrador (DL-0022).** PRs autorados+verificados por agentes do orquestrador ficam em READY-TO-MERGE ate merge humano ou regra de permissao. Portanto **nao** queremos empilhar um segundo gate de revisao humana dentro da branch protection — o gate de revisao ja existe fora dela.

Alem disso, duas praticas atuais dependem de **push direto no main**, que qualquer protecao efetiva bloqueia: (i) **hotfix stop-the-line** direto no `main` (precedentes DL-0007, DL-0023); (ii) **commits de estado** (HANDOFF*, `decisions-log.md`, `docs/handoffs/*`) por push direto do orquestrador (DL-0003). Proteger o `main` exige **substituir** os dois caminhos, nao apenas fecha-los.

**Alternativas consideradas** para matar a classe merge-ref-staleness:

- **(a) GitHub merge queue.** Serializa e re-testa cada PR contra a tip corrente antes de integrar. Exige `merge_group:` como trigger em **todo** workflow requerido — **nenhum existe** hoje (`grep -rn merge_group .github/` = zero) — mais um ruleset/branch-protection com "Require merge queue". **Disponibilidade (VERIFICADO, nao suposto):** merge queue em repo **privado** requer **GitHub Enterprise Cloud**. Este repo e privado (`gh api /repos/Omni-Saude/Maezo-Healthcare-Plan --jq .private` = `true`) e a org esta no plano **team** (`gh api /orgs/Omni-Saude --jq .plan.name` = `"team"`), **nao** Enterprise Cloud. Logo merge queue esta **indisponivel no plano atual** — nao e um "talvez".

- **(b) Required status checks + strict "branch up to date".** Branch protection classica exige que a branch do PR esteja **atualizada com a tip** antes de mergear; quando `main` avanca, todo PR aberto fica *out-of-date* e o GitHub bloqueia o merge ate o autor rebasear/atualizar — o que **re-dispara o CI no merge-ref FRESCO** que ja inclui a nova tip. Mata a classe merge-ref-staleness **por construcao**, com pecas minimas (uma chamada de API), sem `merge_group:` em lugar nenhum.

- **(c) Watch pos-merge + alarme de main-vermelho agendado.** Nao previne — apenas **detecta** depois que o defeito ja entrou. E o mais fraco isolado, mas e um bom **complemento** de (b).

## Decisao

1. **Adotamos (b): branch protection CLASSICA no `main` com `required_status_checks` + `strict: true`.** O endpoint e `PUT /repos/{owner}/{repo}/branches/main/protection` (payload exato no Apendice). Uma unica chamada idempotente cria/substitui a protecao. A cadencia pos-programa e baixa, entao serializar merges e um custo aceito — e, aqui, desejado.

2. **Contextos requeridos = exatamente estes 6 (strings iguais ao campo `name:` de cada job):**
   - `lint / type / unit` — job `quality` (`ci.yml:20`): lint + type + unit; roda em PR e push.
   - `validate-artifacts` — job `artifact-validation` (`ci.yml:54`): shape de BPMN/DMN/policy/agent defs.
   - `content-signoff-gate` — job `content-signoff-gate` (`ci.yml:75`): sign-off humano de artefato promovivel.
   - `terraform validate` — job `validate-terraform` (`ci.yml:97`).
   - `helm lint + egress NetworkPolicy CI` — job `validate-helm` (`ci.yml:129`): inclui os testes de egress PHI fail-closed (ADR-0006/ADR-0017).
   - `integration tests (real engine)` — job `integration` (`ci.yml:174`): a **lane de engine real** — a ancora de union-green.

   Os 6 ja sao reportados HOJE sob esses nomes exatos (todos rodam em PR e em push a `main`), entao nao dependem tecnicamente de nenhum rename. Ainda assim a protecao deve ser **aplicada apos o merge do PR WS-1** (que (a) corrige o nome truncado do job fhir-sync em `ci.yml:238` e (b) adiciona o workflow de alarme de main-vermelho), para que o rollout ocorra com o namespace de check-runs limpo e o alarme complementar ja vivo antes de `enforce_admins` fechar a valvula de push direto.

3. **Excluidos do conjunto requerido (com motivo de uma linha cada):**
   - `evals (agent-touching paths)` (`ci.yml:294`) — so-PR e com steps gated por paths-filter (quase-no-op em PR que nao toca agente) + depende de `LLM_GENERAL_API_KEY` (API externa/custo): **sinal, nao gate**.
   - `evals-nightly (full suite)` (`ci.yml:350`) — so-schedule; nunca presente num merge-ref de PR, logo **nao pode** ser gate de merge (ficaria eternamente "Expected").
   - `fhir-sync simulator smoke (ADR-0013 gap #33)` (`ci.yml:238`) — smoke de Kafka de 12 min, historicamente propenso a travar (run 27511267203); alem disso o nome so deixa de vir **truncado** (`fhir-sync simulator smoke (ADR-0013 gap` — o ` #33)` e comido como comentario YAML) depois do WS-1. **Sinal, nao gate.**
   - Jobs de CD — `build & push image`, `deploy -> staging`, `smoke tests -> staging`, `promote -> production (manual approval)` (`cd.yml`) — CD dispara em `push:[main]`/`workflow_dispatch`, **nao** em `pull_request`; esses checks **nunca** aparecem num PR, entao exigi-los travaria **todo** PR em "Expected". Sao entrega pos-merge, no-op sem `AWS_ENABLED`, e a promocao e manual.

4. **`strict: true` — semantica e substituicao da regra manual de DL-0023.** "Require branches to be up to date before merging": quando `main` avanca, o flag up-to-date de cada PR aberto expira e o GitHub bloqueia o merge ate o autor **rebasear/atualizar** a branch; isso re-roda o CI no merge-ref fresco que ja contem a nova tip, e o union-green passa a ser asserido **mecanicamente** sobre a arvore exata que virara `main`. Isso e precisamente a regra manual de DL-0023 tornada automatica — portanto a **clausula de revalidacao manual de DL-0023 fica SUPERSEDED** por esta protecao. Efeito colateral aceito: **merges serializam** (cada merge invalida o up-to-date dos demais PRs, que re-rodam antes de poder mergear).

5. **`enforce_admins: true`.** As regras valem tambem para admins — fecha o furo de push direto que os hotfixes DL-0007/DL-0023 usavam. Sem isto, a protecao e cosmetica para quem tem admin. O caminho de emergencia (decisao 9) preserva o stop-the-line de forma **auditavel**.

6. **`required_pull_request_reviews: null` (deliberado).** NAO exigimos revisao aprovadora via branch protection: o classifier de auto-mode (DL-0022) ja e a autoridade de revisao; duplicar isso na protecao so aprofundaria o estacionamento de PRs de agente. Com `required_status_checks` + `strict` + `enforce_admins`, todo commit que precise de checks e canalizado por branch/PR de qualquer modo (um push direto de commit sem checks verdes e rejeitado). Se um dia se quiser a UX explicita "Require a pull request before merging", a forma minima e `required_pull_request_reviews: {"required_approving_review_count": 0}` — opcional, nao adotado aqui.

7. **REJEITAMOS (a) merge queue — indisponivel no plano atual (fato verificado).** Repo privado + org no plano **team** = merge queue exige Enterprise Cloud, ausente aqui; e exigiria `merge_group:` em todos os workflows requeridos, que nao existe. Reavaliar **apenas** se a org migrar para Enterprise Cloud E a cadencia de merges subir a ponto de a serializacao de (b) doer.

8. **Adotamos (c) como COMPLEMENTO: alarme de main-vermelho agendado (entregue pelo WS-1), que NAO bloqueia — apenas alerta.** O contrato do alarme, ditado pelos fatos 1-2 do Contexto:
   - **Fixar o run do EVENTO PUSH** da tip de `main` (por run id de `push`), **nao** os check-runs por SHA — porque o noturno reescreve o veredito do SHA.
   - **Ignorar runs `cancelled`/`superseded`/`skipped`** — `cancel-in-progress: true` cancela o run em voo da tip a cada novo push; um run cancelado **nao** pagina.
   - So paginar quando o **ultimo run de push concluido e nao-cancelado** da tip corrente **falhou**.
   - `main`-vermelho bloqueia todos os merges (precedente stop-the-line); o alarme torna esse estado **audivel** em vez de descoberto no proximo PR.

9. **Caminho stop-the-line que substitui o hotfix por push direto (decisao 5 bloqueia push direto).** Emergencia = **levantar cirurgicamente `enforce_admins`** via `gh api` + **registrar uma linha DL**, nunca contornar em silencio:
   1. `gh api -X DELETE .../branches/main/protection/enforce_admins` (admins passam a poder bypassar);
   2. admin faz o push do hotfix direto no `main`;
   3. `gh api -X POST .../branches/main/protection/enforce_admins` (re-arma imediatamente);
   4. registrar `DL-00NN` (precedente DL-0007/DL-0023) com o SHA do hotfix e a janela.
   Isto mantem a **definicao de checks intacta** durante a janela (so a aplicabilidade a admin e suspensa) e deixa rastro auditavel. Fallback mais pesado, se for preciso tambem mexer nos contextos: `gh api -X DELETE .../branches/main/protection` e re-`PUT` da protecao completa (Apendice) — mesmo requisito de linha DL.

10. **Trade-off do push-direto de docs (DL-0003) — decisao final e do USUARIO (WS-6).** Sob a protecao, push direto de HANDOFF*/`decisions-log.md`/`docs/handoffs/*` tambem e bloqueado. Duas saidas:
    - **Opcao 1 (RECOMENDADA): commits de estado viram PRs**, como todo o resto. Custo: churn (um PR por update de estado) e cada um paga os 6 checks — incl. a lane de engine real (~5 min) mesmo para um diff so-markdown. Beneficio: uniforme, auditavel, **zero superficie de bypass**; classic protection basta (decisao 11).
    - **Opcao 2: lista de `bypass_actors`** para um ator/bot de estado. Custo: e uma **superficie de bypass permanente** e **nao e escopavel por path** — o ator bypassa **todas** as regras, nao so docs. Alem disso **exige rulesets** (classic protection nao tem `bypass_actors`), o que **inverte a decisao 11**.
    Recomendacao: Opcao 1. A escolha final e marcada como **decisao de usuario WS-6**.

11. **Classic branch protection vs rulesets — recomendamos CLASSIC.** `PUT /repos/{owner}/{repo}/branches/main/protection` cobre required checks + strict + enforce_admins numa unica chamada, e suficiente e bem entendida. Rulesets so se justificam se o WS-6 escolher a Opcao 2 (`bypass_actors` de docs) — unica condicao que inverte esta recomendacao.

## Consequencias

**Positivas:**
- Mata a classe merge-ref-staleness **por construcao**: `strict: true` forca CI no merge-ref fresco que contem a tip atual — o exato defeito de `#137x#144` e `#157x#155` deixa de ser possivel.
- Automatiza a regra manual de DL-0023 (que foi violada duas vezes): o GitHub, nao a memoria do orquestrador, faz cumprir o "revalidar contra main fresco".
- `enforce_admins: true` fecha o push direto para todos; o stop-the-line vira um gesto **auditavel** (lift cirurgico + linha DL) em vez de um push silencioso.
- Pecas minimas: uma chamada de API, sem `merge_group:` em workflow nenhum, sem dependencia de plano Enterprise; classic protection basta.
- Nao empilha um segundo gate de revisao humana (reviews=null) sobre o classifier de DL-0022.
- O alarme complementar torna `main`-vermelho **audivel** e, por fixar o run do evento push e ignorar cancelados, nao gera falso-alarme com o cancel-in-progress nem com o noturno.

**Negativas (aceitas):**
- **Merges serializam.** Cada merge invalida o up-to-date dos demais PRs abertos, que precisam rebasear + re-rodar CI antes de mergear. Aceito: a cadencia pos-programa e baixa; a serializacao e o proprio mecanismo que garante union-green.
- **Docs pagam CI cheio.** Se o WS-6 escolher a Opcao 1, um PR so-markdown dispara os 6 checks incl. a lane de engine real (~5 min). Aceito em troca de zero superficie de bypass.
- **Custo de rebase recai no autor do PR.** Quando `main` anda, o autor atualiza a branch (era antes um passo manual do orquestrador). Aceito: e o mesmo trabalho, agora imposto por servidor.
- **`evals-nightly` nao bloqueia merge** (so-schedule). Aceito: paginamos no vermelho noturno para pegar drift de prompt/modelo, mas **nunca** gateamos merge numa lane so-schedule e dependente de API de LLM (alerta, nao bloqueia).
- **Sem merge queue** enquanto a org estiver no plano team: nao ha re-teste especulativo em fila; a serializacao de (b) e o substituto de menor custo.

## Supersedes

— Operacionaliza e **torna mecanica** a clausula de revalidacao manual de **DL-0023** (que fica SUPERSEDED nessa parte). Interage com **DL-0003** (push direto de docs — vira PR ou bypass_actors, decisao de usuario WS-6), **DL-0007/DL-0023** (hotfix stop-the-line — agora via lift cirurgico de `enforce_admins` + linha DL) e **DL-0022** (classifier de two-party-review continua a autoridade de revisao; por isso reviews=null). Nao altera nenhum ADR de arquitetura.

## Apendice — comandos `gh api` (prontos para rodar)

Aplicar a protecao (idempotente — cria ou substitui). Rodar **apos o merge do WS-1**:

```bash
gh api -X PUT /repos/Omni-Saude/Maezo-Healthcare-Plan/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "lint / type / unit",
      "validate-artifacts",
      "content-signoff-gate",
      "terraform validate",
      "helm lint + egress NetworkPolicy CI",
      "integration tests (real engine)"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null
}
JSON
```

Verificar o estado aplicado:

```bash
gh api /repos/Omni-Saude/Maezo-Healthcare-Plan/branches/main/protection \
  --jq '{strict: .required_status_checks.strict, contexts: .required_status_checks.contexts, admins: .enforce_admins.enabled}'
```

Stop-the-line de emergencia (lift cirurgico de admin; SEMPRE seguido de linha DL):

```bash
# 1) suspende a aplicabilidade a admin
gh api -X DELETE /repos/Omni-Saude/Maezo-Healthcare-Plan/branches/main/protection/enforce_admins
# 2) admin faz o push do hotfix direto no main
# 3) re-arma imediatamente
gh api -X POST /repos/Omni-Saude/Maezo-Healthcare-Plan/branches/main/protection/enforce_admins
```

Fallback pesado (so se precisar mexer nos contextos): remover a protecao inteira e re-aplicar o `PUT` acima:

```bash
gh api -X DELETE /repos/Omni-Saude/Maezo-Healthcare-Plan/branches/main/protection
```

Fatos que fundamentam a rejeicao de merge queue (verificaveis):

```bash
gh api /repos/Omni-Saude/Maezo-Healthcare-Plan --jq .private   # => true  (repo privado)
gh api /orgs/Omni-Saude --jq .plan.name                        # => "team" (NAO Enterprise Cloud)
```

Opcional (hardening): fixar cada contexto ao app do GitHub Actions (`app_id: 15368`) trocando o array `contexts` por `checks`, ex.: `"checks": [{"context": "integration tests (real engine)", "app_id": 15368}, ...]` — impede que outro app satisfaca um contexto homonimo.
