# ADR-0069: Emenda ao ADR-0023 — o ruleset 20775655 e o mecanismo real de protecao do `main`; strict desligado; bypass so por break-glass auditado

**Status:** Proposed — redigido por AGENTE a partir da decisao do dono DEC-19 (2026-10-31); ratificacao pela revisao independente por ADR (DEC-27) PENDENTE; a mudanca do ruleset e ato administrativo do security-team/dono, NAO executado por agente
**Data:** 2026-10-31
**Area:** DevOps / CI / Governanca de merge

## Contexto

O ADR-0023 (Accepted, 2026-07-05) foi escrito no repositorio v1 — o Apendice dele aponta para `Omni-Saude/Maezo-Healthcare-Plan` — e decidiu: protecao CLASSICA do `main` (decisoes 1 e 11), exatamente 6 contextos requeridos (`lint / type / unit`, `validate-artifacts`, `content-signoff-gate`, `terraform validate`, `helm lint + egress NetworkPolicy CI`, `integration tests (real engine)`; decisao 2), `strict: true` (decisao 4), `enforce_admins: true` (decisao 5), revisao aprovadora nao exigida (decisao 6), merge queue rejeitado (decisao 7), alarme de main-vermelho como complemento (decisao 8), stop-the-line por lift cirurgico de `enforce_admins` + linha DL (decisao 9) e commits de estado por PR (decisao 10).

A auditoria forense de 2026-10 (achados F-093 e F-050; itens de roadmap R-038 e R-039; GOV-0.10) mediu que o GitHub real diverge do texto. Estado lido AO VIVO em 2026-10-31 neste repo (`Omni-Saude/maezo-operadora`), so com chamadas de leitura:

| Aspecto | ADR-0023 diz | Realidade (`gh api`, 2026-10-31) |
|---|---|---|
| Mecanismo | protecao classica (`PUT .../branches/main/protection`) | `GET .../branches/main/protection` -> `404 Branch not protected`; o `main` e protegido pelo ruleset `20775655` (`main-protection`, `enforcement: active`, criado 2026-08-12, atualizado 2026-10-24) |
| Contextos requeridos | os 6 da decisao 2 | 6 outros: `lint / type / unit`, `gitleaks / secrets`, `validate-artifacts`, `release-capability-floor (audit §5)`, `ai-attribution-gate`, `flip-path-review-gate` |
| Branch atualizada | `strict: true` | `strict_required_status_checks_policy: false` |
| Admins | `enforce_admins: true` | `bypass_actors: [{actor_id: 195689062 (rodaquino-OMNI), actor_type: User, bypass_mode: always}]` |
| Revisao | nenhuma regra de PR | regra `pull_request` com `required_approving_review_count: 0` e `require_code_owner_review: false` |
| Demais regras | — | `deletion` e `non_fast_forward` |

Onde cada contexto requerido nasce hoje (workflows em `.github/workflows/`): `lint / type / unit` (`ci.yml:190`), `validate-artifacts` (`ci.yml:714`), `ai-attribution-gate` (`ci.yml:1027`), `release-capability-floor (audit §5)` (`ci.yml:1106`), `gitleaks / secrets` (`security.yml:447`) e `flip-path-review-gate` (`flip-path-review-gate.yml:102`, script `scripts/ci/check_flip_path_review.py`). O `flip-path-review-gate` e o mecanismo que de fato exige a revisao de um dono qualificado nos caminhos do CODEOWNERS, porque `require_code_owner_review` esta desligado no ruleset. Os quatro contextos do ADR-0023 que NAO sao requeridos continuam rodando como sinais de CI: `content-signoff-gate` (`ci.yml:1057`), `terraform validate` (`ci.yml:1250`), `helm lint + egress NetworkPolicy CI` (`ci.yml:1382`) e `integration tests (real engine)` (`ci.yml:1981`).

Dois fatos do Contexto do ADR-0023 tambem mudaram: (i) o repositorio hoje e PUBLICO (`gh api repos/Omni-Saude/maezo-operadora --jq .visibility` -> `"public"`), e a premissa "repo privado" da decisao 7 nao vale mais; (ii) o `.github/workflows/branch-protection-check.yml` so fica verde com um RULESET ativo (protecao classica sozinha o deixa vermelho), o que ja inverte a recomendacao "classic" da decisao 11.

## Decisao

Decisao do dono em 2026-10-31 (DEC-19, resposta ao issue de papel #1085 do security-team: "manter strict=false; restringir o bypass a break-glass auditado"), mais a emenda do texto a realidade:

1. **O mecanismo e o ruleset `20775655`, nao a protecao classica.** Substitui as decisoes 1 e 11 do ADR-0023. E o que esta ativo e o unico formato que o `branch-protection-check` aceita.

2. **Os contextos requeridos sao os 6 que o ruleset exige hoje:** `lint / type / unit`, `gitleaks / secrets`, `validate-artifacts`, `release-capability-floor (audit §5)`, `ai-attribution-gate` e `flip-path-review-gate`. Substitui a lista da decisao 2. `content-signoff-gate`, `terraform validate`, `helm lint + egress NetworkPolicy CI` e `integration tests (real engine)` passam a constar como SINAL, nao gate (somam-se aos excluidos da decisao 3). Requerer de novo qualquer um deles exige nova emenda.

3. **`strict` fica DESLIGADO** (`strict_required_status_checks_policy: false`) por orcamento de minutos de CI (decisao do dono): com strict ligado, cada merge invalidaria todos os PRs abertos e re-rodaria a suite completa de cada um. Substitui a decisao 4. **Consequencia que esta emenda declara em vez de esconder:** a classe "pairwise-green != union-green" do ADR-0023 NAO fica morta por construcao. A clausula de revalidacao manual do DL-0023, que o ADR-0023 dava por SUPERSEDED, VOLTA A VALER como regra operacional: quando `main` mudou nos mesmos arquivos depois do ultimo CI verde de um PR, atualizar a branch (`git merge origin/main`, sem rebase nem force-push) e esperar o CI verde de novo antes do merge. O alarme de main-vermelho (decisao 8, `.github/workflows/red-main-alarm.yml`) segue como rede de deteccao.

4. **Sem bypass permanente; bypass so por break-glass auditado.** Substitui as decisoes 5 e 9 (o equivalente de `enforce_admins` num ruleset e a lista `bypass_actors`). Configuracao-alvo: `bypass_actors: []`. Procedimento de break-glass, sempre nesta ordem:
   1. registrar ANTES o motivo (issue ou linha DL) — main vermelho bloqueando todos os merges, incidente de seguranca, ou outro motivo nomeado;
   2. um admin do repositorio inclui UM ator em `bypass_actors` com `bypass_mode: pull_request` (bypass so pelo merge de um PR — nunca push direto, nunca `always`);
   3. o PR do hotfix e mergeado;
   4. o mesmo admin volta `bypass_actors` para `[]` imediatamente;
   5. registrar uma linha DL com o numero do PR, o SHA do merge, a janela (abertura e fechamento) e o ator — o historico do ruleset e o audit log da organizacao sao o rastro independente.

5. **Mantidas sem alteracao:** a decisao 6 em espirito (nenhuma aprovacao obrigatoria pelo ruleset — a regra `pull_request` com 0 aprovacoes so obriga o caminho por PR; a revisao de dono nos caminhos do CODEOWNERS e exigida pelo `flip-path-review-gate`), a decisao 7 (sem merge queue: nenhum workflow tem gatilho `merge_group:`; a premissa "repo privado" caiu, e reavaliar o merge queue e decisao do dono, nao desta emenda), a decisao 8 (alarme) e a decisao 10 (commits de estado por PR — e o que ja acontece).

### Proposta de mudanca do ruleset (ato administrativo — NAO executado por agente)

A unica diferenca entre o ruleset vivo e a configuracao-alvo e `bypass_actors`. Quem executa: admin do repositorio (security-team ou dono), depois da ratificacao desta emenda. Os comandos derivam o corpo do estado vivo e trocam SO `bypass_actors`, para nao reescrever regras a mao:

```bash
R=repos/Omni-Saude/maezo-operadora/rulesets/20775655

# 0) estado antes (guardar a saida no registro do ato)
gh api "$R" --jq '{strict: (.rules[]|select(.type=="required_status_checks")|.parameters.strict_required_status_checks_policy), contexts: [.rules[]|select(.type=="required_status_checks")|.parameters.required_status_checks[].context], bypass: .bypass_actors}'

# 1) configuracao-alvo: sem bypass permanente (regras, condicoes e enforcement inalterados)
gh api "$R" | jq '{name, target, enforcement, conditions, rules, bypass_actors: []}' \
  | gh api -X PUT "$R" --input -

# 2) verificacao: espera bypass [] , strict false e os 6 contextos
gh api "$R" --jq '{strict: (.rules[]|select(.type=="required_status_checks")|.parameters.strict_required_status_checks_policy), n_contexts: ([.rules[]|select(.type=="required_status_checks")|.parameters.required_status_checks[]]|length), bypass: .bypass_actors}'
```

Break-glass (passos 2 e 4 do item 4 da Decisao):

```bash
# abrir: um ator, so via PR
gh api "$R" | jq '{name, target, enforcement, conditions, rules, bypass_actors: [{actor_id: 195689062, actor_type: "User", bypass_mode: "pull_request"}]}' \
  | gh api -X PUT "$R" --input -
# fechar: imediatamente depois do merge
gh api "$R" | jq '{name, target, enforcement, conditions, rules, bypass_actors: []}' \
  | gh api -X PUT "$R" --input -
```

O DoD do R-039 (`length` dos contextos requeridos entre 6 e 9) ja passa hoje com os 6 contextos; o que falta do R-039, pela decisao do dono, e so o passo 1 acima.

## Consequencias

**Positivas:**
- O texto de governanca de merge volta a descrever o GitHub real; um auditor que ler o ADR encontra o ruleset que esta ativo.
- Sem bypass permanente: nenhum ator passa por cima dos 6 contextos fora de uma janela registrada, e a janela so abre pelo caminho de PR.
- O custo de minutos de CI fica limitado: um merge nao re-dispara a suite completa de todos os PRs abertos.

**Negativas (aceitas):**
- **Union-green nao e garantido pelo servidor.** Com strict desligado, dois PRs verdes isoladamente podem quebrar o `main` juntos. Mitigacao: a regra manual do DL-0023 (item 3) e o alarme de main-vermelho. Risco aceito pelo dono por orcamento de CI.
- **A lane de engine real nao e gate de merge.** `integration tests (real engine)` nao e requerido; um defeito que so aparece contra o engine pode chegar ao `main` e ser pego pelo alarme ou pelo CI do proximo push. Mitigacao: prova em engine local antes do push, conforme o protocolo de implementacao.
- **`content-signoff-gate`, `terraform validate` e `helm lint` nao bloqueiam merge.** Viram sinal; um vermelho neles precisa de leitura humana.
- **Break-glass custa minutos de admin** (duas chamadas + registro) em vez de um push direto. Aceito: e o preco do rastro auditavel.
- **Ate o passo 1 da proposta ser executado, o bypass `always` do dono continua ativo.** Esta emenda nao muda o servidor.

## Supersedes

— (Emenda; nao supersede o ADR-0023 inteiro.) **Amends ADR-0023:** substitui as decisoes 1, 2, 4, 5, 9 e 11; amplia a decisao 3 (mais quatro contextos viram sinal); mantem as decisoes 6 (em espirito), 7, 8 e 10. Pela convencao do repositorio (ADR-0040, ADR-0041), o arquivo do ADR-0023 NAO e editado. Interage com o DL-0023 (a revalidacao manual volta a valer enquanto strict estiver desligado) e com o DL-0003 (commits de estado seguem por PR). Achado relacionado fora desta emenda: F-050 (o gate de golden datasets do ADR-0009 §4 nao e contexto requerido) continua aberto.
