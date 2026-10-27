# Proveniência dos commits provider T1.7/Q2/C1 sem token `(#NNN)` — GOV-0.6 (F-E13)

**Status:** registro executado (nota de proveniência; NÃO reescreve histórico) · **Data:** 2026-10-27 ·
**Base auditada:** `origin/main` @ `edd7910b` · **Fecha:** GOV-0.6 do plano
`docs/plans/maezo-completion-program.md` (tabela W0), finding **F-E13** do bundle
JOURNEYS-COMPLETENESS-2026-10 (`FINDINGS.md` linha 29; `TRACEABILITY.md` §0 `:37-40`).

> **O que esta nota é:** o registro da proveniência que o token ausente deixou implícita — para cada
> commit do F-E13, o PR de aterrissagem, a missão de origem e a prova reproduzível. **O que não é:**
> reparo de história (nenhum rebase/filter/reword; os subjects permanecem como estão).

## 1. Contexto e causa-raiz

O método de citação da casa (TRACEABILITY §0, passo 1) parte de `git log --grep '<token>'` sobre
subjects de squash `(#NNN)`. Os commits do substrato provider de leitura nativa (set/2026) foram
aterrissados por **merge commit** (não squash) — a estratégia de merge da era — de modo que o número
do PR nunca entrou no subject do commit de trabalho. O código está em `main` e verificado; o que
faltava era o registro commit→PR (F-E13: "implementation record incomplete for the provider engine
substrate"). A proveniência é recuperável deterministicamente da própria história de merges + API do
GitHub; é exatamente o que esta nota fixa.

## 2. Método (reproduzível)

Para cada `SHA`:

1. Confirmação de ancestralidade: `git merge-base --is-ancestor <SHA> origin/main` (esperado exit 0).
2. Aterrissagem: caminhar `git log --merges --ancestry-path <SHA>..origin/main` e tomar o merge mais
   antigo cujo **2º parent** contém o SHA (o merge do PR que trouxe o branch; merges internos entre
   branches de trabalho são intermediários e ficam registrados como tal quando for o caso).
3. Verificação externa: `gh pr view <N> --repo Omni-Saude/maezo-operadora --json
   number,title,state,mergedAt,mergeCommit,headRefName`.
4. Símbolo sobrevivente: caminho `file` em `origin/main` citado por coluna própria.

## 3. Núcleo F-E13 — os 6 commits apontados pelo TRACEABILITY §0

| SHA | Assunto (data, autor) | PR de aterrissagem (estado) | Merge sha | Papel na missão provider | Símbolo sobrevivente em `main` |
|---|---|---|---|---|---|
| `20331847` | `feat(portal-engine): provedor Q2 instalado recortado ao staff (T1.7a)` (2026-09-23 16:36, Leonardo Silva) | **#481** — `feat/t1-7a-read-provider`, MERGED 2026-09-23T20:42Z | `d88eaaf8` | **T1.7a** do plano `docs/plans/portal-autoridade-nativa-dev.md` §3.1: módulo Maven novo `read-provider` com admissão `portal-read-admission.v1` (`maezo_native.mzo_portal_read_admission`), acquire/requireCurrent/continuity, fail-closed; registro `META-INF/services` do `PortalReadTrust.Providers` (ADR-0060, citado no corpo do commit) | `src/maezo/portal/engine/read-provider/src/main/resources/META-INF/services/br.com.maezo.human.PortalReadTrust$Providers`; `src/maezo/portal/engine/read-provider/pom.xml` |
| `139fcee1` | `fix(portal-engine): fecha o pin da admissao Q2 e ancora a raiz no boot (review #481)` (2026-09-23 17:27, Leonardo Silva) | **#481** — mesmo branch/PR (commit de resposta à revisão de segurança do próprio #481, como o subject diz) | `d88eaaf8` | Fechamento dos achados da revisão do #481: pin por lista fechada (`PIN_SQL`), linha pública única por revisão, `search_path=pg_catalog` (CVE-2018-1058), leitura por `tableoid`, CI `postgres:17` por digest | `src/maezo/portal/engine/read-provider/src/main/java/br/com/maezo/human/readprovider/InstalledReadProviders.java` (símbolo `PIN_SQL`) |
| `0749aae1` | `feat(portal-engine): provedor Q2 qualifica as publicacoes do escopo staff (T1.7b)` (2026-09-23 17:45, Leonardo Silva) | **#487** — `feat/t1-7b-read-provider-publicacoes`, MERGED 2026-09-23T22:19Z | `4ba7d7d3` | **T1.7b** §3.1 do plano provider: `qualifyPublication` lê a linha viva de `portal_memberships` (verify-full, read-only); vetor JCS compartilhado Java×Python; Onda 8 recusa `resource` | `tests/fixtures/portal_read/jcs-membership-vector.json`; `tests/unit/portal/test_jcs_membership_vector.py` |
| `48968769` | `H1: provedor Q2 verifica classificacao e identidade de tarefa humana, publica resource e le staff_current_task` (2026-09-25 15:34, Leonardo Silva) | **#538** — `feat/h1-q2-tarefas-humanas`, MERGED 2026-09-25T19:58Z | `71e383eb` | **H1** do pacote humano (§Ordem do plano provider): o provedor Q2 passa a verificar classificação/identidade de tarefa humana, publicar `resource` e ler `staff_current_task` | `src/maezo/portal/engine/read-provider/src/test/java/br/com/maezo/human/HumanTaskReadJarIT.java`; `tools/staff_materials/approver.py` |
| `e4f637d0` | `Corrige F1/F2/F4 do C1: designacao sem FOR UPDATE, separacao E04 sem D7, DataSource do provedor no boot` (2026-09-23 22:19, Leonardo Silva) | **#505** — `feat/t1-11-chave-auth` ("T1.11: chave AUTH por número da guia TISS + fixture sintética do C1"), MERGED 2026-09-24T04:36Z | `f6ade036` | Reparos **F1/F2/F4 do C1** (checkpoint de integração do plano provider): lock consultivo no lugar de `FOR UPDATE`, degradação E04 condicionada a D7, DataSource do provedor no boot; aterrissou junto da T1.11 no mesmo PR | `src/maezo/portal/engine/read-provider/src/main/java/br/com/maezo/human/readprovider/InstalledReadProviders.java` |
| `c92392d0` | `C1: fecha as ressalvas da revisao de seguranca (lock da designacao, tenant sem hifen, descriptor root, designacao assinada, colacl portal_read, decimal limitado)` (2026-09-24 00:06, Leonardo Silva) | **#505** — mesmo PR (branch `fix/c1-ressalvas` absorbido na linhagem `t1-11`/`c1-java` antes do merge) | `f6ade036` | Fechamento das ressalvas de segurança do **C1**: designação assinada, tenant sem hífen, descriptor root, `colacl portal_read`, decimal limitado | `tests/unit/deploy/test_portal_image_assembly.py` |

Merges internos intermediários (não são PRs; registrados para reprodutibilidade do passo 2):
`20331847`/`139fcee1` → `d9a8e5b2` (main → `feat/t1-7a-read-provider`); `0749aae1` → `99613824`
(main → `feat/t1-7b-…`) e `acf3484e` (`feat/t1-7b-…` → `fix/followups-t17b-t18b`, aterrissado pelo #491);
`e4f637d0` → `f87232b3` (`fix/c1-python` → `fix/c1-java`); `c92392d0` → `2427a40b`
(`fix/c1-ressalvas` → linhagem `t1-11`).

## 4. Linhagem satélite — recorte delimitado (NÃO exaustivo)

Mesmo método; mesmo padrão de causa-raiz (merge commit, não squash). **Esta tabela NÃO esgota os
commits provider sem token `(#NNN)` do intervalo 2026-09-23…25 da missão
`docs/plans/portal-autoridade-nativa-dev.md`**: ficam fora desta nota, todos ancestrais de `main`,
entre outros — família C1-checkpoint: `81cd6485`, `8fe536be`, `83c96824`, `db7621ce`, `1f219b58`,
`b7f2826f`, `2e004ade`; família Q2/T1.4b/T1.5: `2f459d90`, `377329ed`, `74209e0e`; família
T1.11-chave-AUTH: `d2af661d`; grupo H5/Onda-8/job-T1.5: `21547259`, `c525513f`, `c6d87c59`,
`fb2c3bd8`, `6d3a269b`, `20f8426c`, `8229e2ca`, `bd468edf`, `41f3acd4`, `c562f270`, `56b8f4e9`.
A extensão da tabela a esses commits é nota própria; GOV-0.6 fica executado NESTE recorte.

| SHA | Assunto | PR de aterrissagem (estado) | Merge sha | Papel |
|---|---|---|---|---|
| `0fc7e30a` | `docs(adr): ADR-0060 schema nativo maezo_native (D-C2) + desenho da T1.7` | **#479** — `adr/maezo-native-schema`, MERGED | `096986ef` | O próprio ADR-0060 + desenho §3.1 que a T1.7a implementa |
| `e976988f` | `Corrige F6: sign-admission confere o shape fechado do portal-read-admission.v1 da T1.7a…` | **#505**, MERGED | `f6ade036` | F6 do C1 sobre o harness do `sign-admission` |
| `c0e0626d` | `fix(portal-engine): ressalvas de seguranca T1.7b/T1.8b` | **#491** — `fix/followups-t17b-t18b`, MERGED | `cf5ebc0a` | Follow-ups de segurança T1.7b/T1.8b |
| `da08a9b1` | `chore(gitleaks): fingerprint do cabecalho PEM de teste no commit antigo da T1.7b` | **#487**, MERGED | `4ba7d7d3` | Fingerprint gitleaks do cabeçalho PEM introduzido pela T1.7b |
| `37901dbc` | `fix(engine): instala o provedor Q2 no Dockerfile.human e verifica o digest staff do portal` | **#520** — `build/engine-provider-jar`, MERGED | `8cdc0032` | Item **B1/B11** do plano provider (JAR do provedor Q2 no `Dockerfile.human`; `verificar` confere `staff.portal_image_digest`) |
| `1ee4c320` | `H2-H4: fonte real de tarefas no job T1.5, grant com tarefas correntes e fim dos contornos D11-D13 do C1` | **#540** — `feat/h2-h4-tarefas-fonte-real`, MERGED | `25ca4718` | **H2–H4** do pacote humano |
| `98bef1ec` | `ops(staff): task-read - prova da fila humana no plano Q2 com o cliente de producao (sem o BFF)` | **#543** — `ops/onda8-human`, MERGED | `5307bd1b` | **Onda 8** (fila humana no Q2; task-facts/task-read) |

## 5. Provas (executadas 2026-10-27, base `origin/main` @ `edd7910b`)

Ancestralidade dos 13 commits (núcleo + satélites), todos **exit 0**:

```text
$ for sha in 20331847 139fcee1 0749aae1 48968769 e4f637d0 c92392d0 \
             0fc7e30a e976988f c0e0626d da08a9b1 37901dbc 1ee4c320 98bef1ec; do
    git merge-base --is-ancestor ${sha} origin/main; echo "${sha} ancestor-exit=$?"; done
20331847 ancestor-exit=0
139fcee1 ancestor-exit=0
0749aae1 ancestor-exit=0
48968769 ancestor-exit=0
e4f637d0 ancestor-exit=0
c92392d0 ancestor-exit=0
0fc7e30a ancestor-exit=0
e976988f ancestor-exit=0
c0e0626d ancestor-exit=0
da08a9b1 ancestor-exit=0
37901dbc ancestor-exit=0
1ee4c320 ancestor-exit=0
98bef1ec ancestor-exit=0
```

GitHub API (estado dos PRs de aterrissagem, todos **MERGED**):

```text
$ gh pr view <N> --repo Omni-Saude/maezo-operadora --json number,title,state,mergedAt,headRefName,mergeCommit
#481 | feat/t1-7a-read-provider          | d88eaaf8 | feat(portal-engine): provedor Q2 instalado recortado ao staff (T1.7a)            | 2026-09-23T20:42:23Z
#487 | feat/t1-7b-read-provider-publicacoes | 4ba7d7d3 | feat(portal-engine): provedor Q2 qualifica as publicacoes do escopo staff     | 2026-09-23T22:19:02Z
#505 | feat/t1-11-chave-auth              | f6ade036 | T1.11: chave AUTH por numero da guia TISS + fixture sintetica do C1              | 2026-09-24T04:36:21Z
#538 | feat/h1-q2-tarefas-humanas         | 71e383eb | H1 (D-N): provedor Q2 verifica tarefa humana (classificacao, identidade, resource) | 2026-09-25T19:58:52Z
#479 | adr/maezo-native-schema            | 096986ef | ADR-0060: schema nativo maezo_native (D-C2) + desenho da T1.7                    |
#491 | fix/followups-t17b-t18b            | cf5ebc0a | fix(portal-engine): ressalvas de seguranca T1.7b/T1.8b                           |
#520 | build/engine-provider-jar          | 8cdc0032 | fix(engine): provedor Q2 no Dockerfile.human e verificação do digest staff do portal |
#540 | feat/h2-h4-tarefas-fonte-real      | 25ca4718 | H2-H4 (D-N): fonte real de tarefas, grant com tarefas correntes, fim dos contornos D11-D13 |
#543 | ops/onda8-human                    | 5307bd1b | ops/infra(dev): Onda 8 - fila humana no Q2 (H5 infra, D14, task-facts/task-read) + portal human (desligado) |
```

## 6. Vocabulário dos tokens (definição rastreada)

- **Q2** — a "autoridade Q2" do engine: o `PortalReadPlugin`/`staffLease` que o `/cases` staff
  depende (`docs/plans/portal-autoridade-nativa-dev.md:15,52`; ADR-0060 "admissão Q2 (T1.7)").
- **T1.7 / T1.7a / T1.7b** — tarefa do plano provider §3.1: implementação
  `PortalReadTrust.Providers` (`:556`), dividida em T1.7a (módulo `read-provider`, admissão e
  chaves, `:558`) e T1.7b (qualificação das publicações do escopo staff + vetor JCS, `:559`).
- **C1** — checkpoint de integração do plano provider, thread principal (`:629`, `:782`).
- **H1…H5** — pacote humano do plano provider (ordem de builders §Ordem, `:445`).
- **T1.11** — tarefa "chave AUTH por número da guia TISS" (`:394`).

## 7. Limites

1. Nenhum commit foi reescrito, rebaseado ou rewordeado; nenhum token foi adicionado a histórico.
2. A lacuna apontada pelo F-E13 era de **registro**, não de implementação (maturidade já
   "implemented (code on main)"); esta nota fecha o registro.
3. Commits futuros continuam sob a convenção vigente de squash com `(#NNN)`; esta nota não cria
   exceção nem precedente para aterrissagens por merge commit.
