# W7 7.4 — Pin do tag CIB Seven: registro de verificação

**Data:** 2026-10-27 · **Pacote:** W7 7.4 (`docs/plans/maezo-completion-program.md:114`) · **Tipo:** registro por verificação (docs-only) · **Base auditada:** `origin/main` @ `88844a57` (worktree da branch `w7-74-cib-tag-pin`)

## Veredito

**PIN COMPLETO E CONSISTENTE — sem drift de valor.** A imagem de plataforma CIB Seven está pinada em **2.1.0** em todos os sítios Docker (compose de dev = tag; todos os Dockerfiles de build = tag+digest; ECS prod = digest de imagem ECR própria de repo IMMUTABLE); a dependência Maven do engine está em **2.2.0** em todos os sítios pom. A aparente divergência "2.2.0 (pom) × 2.1.0 (compose)" **não é inconsistência**: são artefatos de classes distintas (§2). Nenhum sítio sem pin foi encontrado. Este pacote NÃO altera nenhum pin: re-pin é decisão de governança do dono (bump de dependência é proibido ao autor; ver §5).

## 1. Tabela sítio → valor → prova (imagem de plataforma, Docker)

| Sítio | Valor | Prova (arquivo:linha) |
|---|---|---|
| Stack de dev (`docker compose --profile core`) | `cibseven/cibseven:2.1.0` (tag, **sem** digest — único sítio tag-only, ver §4) | `docker-compose.yml:60` — comentário in-line "governanca (2.1.3 sem imagem publicada — DL-0006)" |
| Imagem própria do engine (sem showcase invoice) | `cibseven/cibseven:2.1.0@sha256:20a2135bbc634924838b6a067ac27172bb95d68ddec035e01b40d2b9e95a1169` | `deploy/cibseven/Dockerfile:28` (racional do TAG→TAG+DIGEST em `:22-27`, fold de 19/09/2026, pacote trivy/IaC D2) |
| Variante secured (v1) | `cibseven/cibseven:tomcat-2.1.0@sha256:20a2135b…` (MESMO index digest) | `deploy/cibseven/Dockerfile.secured:7` |
| Variante secured (v2) | idem | `deploy/cibseven/Dockerfile.secured-v2:7` |
| Variante human (staff composition) | idem | `deploy/cibseven/Dockerfile.human:31` |
| Produção (ECS Fargate, dev-sa-east-1) | digest sha256 **obrigatório** da imagem ECR `amh/cibseven-maezo` (validação `^sha256:[0-9a-f]{64}$`, sem default, "sem tag mutavel ou digest inventado") | `deploy/aws-ecs/envs/dev-sa-east-1/service-cibseven.tf:19-27`; repo ECR `image_tag_mutability = "IMMUTABLE"` (desde 19/09/2026) em `deploy/aws-ecs/envs/dev-sa-east-1/ecs.tf:65-78` |
| Célula local C1 | consome imagens DERIVADAS buildadas dos Dockerfiles pinados (`${C1_IMG}-legacy:local` / `-engine:local`), não a imagem upstream direta | `deploy/c1-local/compose.yaml:37-42,52-56` |
| Runner de integração de engine | `declared_image: "cibseven/cibseven:2.1.0"` gravado no provenance do run | `scripts/dev/run_engine_integration.py:1518` |
| Runner de migração histórica | `IMAGES["cibseven"] = "cibseven/cibseven:2.1.0"` + asserção de proveniência `declared_image == IMAGES["cibseven"]` | `scripts/dev/run_historical_migration_recipe.py:90` e `:1228` |
| Cerca de regressão (teste) | `assert "FROM cibseven/cibseven:2.1.0" in dockerfile` | `tests/unit/deploy/test_canonical_group_whitelist.py:172` |
| Selo vendor da imagem deployada | 25 entradas `lib/cibseven-*-2.1.0.jar` (incl. `cibseven-engine-2.1.0.jar` → `412dc73c…`), **zero** entrada 2.2.0 — amarra a imagem buildada à linha 2.1.0 | `src/maezo/portal/engine/java/src/main/resources/secured-startup-vendor.sha256` |

## 2. A distinção jar × imagem (por que 2.2.0 no pom NÃO conflita com 2.1.0 no compose)

| Artefato | Coordenada/valor | Sítios | Papel |
|---|---|---|---|
| **Imagem de plataforma** (distribuição Docker Hub do CIB Seven; embute o engine jar vendorado `lib/cibseven-*-2.1.0.jar`) | `2.1.0`, digest `sha256:20a2135b…` | §1 | O que RODA em dev/ECS. Pin de governança DL-0006 |
| **Jar Maven `org.cibseven.bpm:cibseven-engine`** (API de compilação/teste do comando humano) | `2.2.0` | `src/maezo/portal/engine/java/pom.xml:7` (provided), `:9` (`cibseven-engine-rest-core-jakarta:2.2.0`, provided), `:41` (provided, profile `deployed-runtime-compatibility`); `src/maezo/portal/engine/read-provider/pom.xml:21` (test) | O que COMPILA/testa o `human-command-engine` fora da imagem. Entrou por bump automatizado do Dependabot 2.1.0→2.2.0 (`e1cdbae0`, PRs #651/#838 — ecossistema `maven` vigia os únicos 2 poms rastreados, `.github/dependabot.yml:71-92`) |

A versão da API de compilação (2.2.0) é independente da versão vendorada dentro da imagem (2.1.0): a compatibilidade do jar construído contra o engine REAL 2.1.0 é exercida pela lane de integração com engine de pé (`run_engine_integration.py:1518` grava a imagem declarada no provenance; `run_historical_migration_recipe.py:1228` falha se o `declared_image` divergir do declarado). O selo `secured-startup-vendor.sha256` fecha o lado imagem.

## 3. Prova viva do pin (Registry API do Docker Hub, consultada 2026-10-27)

| Tag upstream | Digest (index) | Último push |
|---|---|---|
| `2.1.0` | `sha256:20a2135bbc634924838b6a067ac27172bb95d68ddec035e01b40d2b9e95a1169` | 2025-11-07 |
| `tomcat-2.1.0` | **idem** `20a2135b…` | 2025-11-07 |
| `2.2.0` | `sha256:cf362b2de0ffb710defd5d4e17231e23221d20662d7a3a23cac5a994f3172892` | **2026-05-31** |
| `latest`, `tomcat` | **idem** `cf362b2d…` (= 2.2.0) | 2026-05-31 |
| `2.1.3` | **NUNCA publicado** (a linha 2.1.x só tem 2.1.0 + SNAPSHOT) | — |

Leituras: (i) o digest pinado nos 4 Dockerfiles (`20a2135b…`) é exatamente o digest corrente das tags `2.1.0`/`tomcat-2.1.0` — o pin está válido e a tag não se moveu desde 2025-11-07; (ii) a igualdade `2.1.0 == tomcat-2.1.0` afirmada em `deploy/cibseven/Dockerfile:23-25` foi re-confirmada ao vivo; (iii) **`latest` upstream já aponta 2.2.0** — qualquer sítio sem pin sofreria drift silencioso; os pins existentes são o que impede isso.

## 4. Observações de consistência (nenhuma é drift de valor)

1. **Compose de dev é tag-only** (`docker-compose.yml:60`): o único sítio da imagem sem digest. Já registrado como menor em `docs/Tarefas_Pendentes.md:658-659` ("pinar por digest ou espelhar"). Os caminhos de build/deploy (tudo que produz artefato) são tag+digest; o compose é consumido só pelo stack local/CI.
2. **Staleness lexical em `src/maezo/portal/engine/java/pom.xml:37-41`:** o comentário do profile `deployed-runtime-compatibility` diz que as versões provided "match secured-startup-vendor.sha256", mas o perfil declara `cibseven-engine 2.2.0` e o selo lista jars 2.1.0 (a imagem pinada não mudou; o pom foi bumpado pelo Dependabot). Compatibilidade real continua provada pela lane de integração (§2). Edição de pom está fora deste pacote (docs-only; bump proibido).
3. **Drift de linha em citações antigas:** o compromisso R-110 cita `docker-compose.yml:52` — a linha do serviço `cibseven` hoje é `:60` (o arquivo cresceu). O valor pinado não mudou.
4. **ADR-0049** (linha do índice, `docs/adr/README.md:61`) menciona "comando Java/CIB 2.1.0" — coerente com a imagem de plataforma; ADRs são artefatos congelados, não editados aqui.

## 5. Pendências nomeadas (ato do OWNER — nenhuma executável por agente)

| # | Pendência | Estado | Prova |
|---|---|---|---|
| 1 | **Revisita do DL-0006** ("revisitar quando publicarem"): a condição de revisita AGORA EXISTE — `cibseven/cibseven:2.2.0` publicado 2026-05-31 (§3). Re-pin da imagem (compose+Dockerfiles+selo vendor) é decisão de governança + bump de dependência | ABERTA — insumo fresco registrado neste doc | `docs/decisions-log.md:182`; §3 acima |
| 2 | **HX-15** (risco de drift de conformidade do motor; P2, `owner-decision`, UNREPRODUCED) + **R-110**: criar vigilância automatizada Renovate/Dependabot apontando aos 3 sítios de pin — **prazo 2027-03-04**. Hoje: `.github/dependabot.yml` não tem entrada cibseven-specific (ecossistema `docker` só no diretório `/`; `maven` só nos 2 poms) e não existe `renovate.json` | ABERTA | `docs/compliance/GAP-REGISTER.yaml:2234-2252`; `docs/Tarefas_Pendentes.md:1142`; `.github/dependabot.yml` |
| 3 | **ADR-0038** — emenda à proibição 6 do ADR-0037 (forma das chaves CIB: business keys hifen-primeiro × `{tenant}:{tipo}:{ref}`; process keys `SP-OP-*` × `maezo-payer-`): **Proposed — NÃO RATIFICADO; nenhuma cláusula tem efeito**; escolha Opção 1 × Opção 2 = ato do dono (pendência DL-0043/PLANS §0.6). Vinculada a este pacote apenas como co-citada no mandato 7.4; nada aqui a afeta | ABERTA — owner | `docs/adr/0038-adr0037-proibicao6-forma-chaves-cib.md:3`; `docs/adr/README.md:50`; proibição 6 original em `docs/adr/0037-amh-compatibility-boundary-canonical-contracts.md:212-213` (ADR-0037 Accepted, ratificado DL-0040) |

## 6. Escopo não-tocado

A evidência do rebuild v29 do programa provider N2 (Dockerfile.sealed-parent, selo estável `n2fix-r17`) vive em worktrees e em bundles de auditoria gitignored da frota provider (`docs/audits/...`, elisão declarada — fora da árvore rastreada) — **não é prova desta verificação** (aqui valem só artefatos rastreados em `origin/main`); o pacote 7.4 não a consome nem a altera.

## 7. Método

Auditado por leitura direta de `origin/main` @ `88844a57` (varredura `grep -rniE 'cibseven|cib-seven|cib_seven' . --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=target --exclude-dir=.venv --exclude='*.jar' --exclude='*.lock' --exclude='*.png' | wc -l` sobre a árvore de checkout de `88844a57` (worktree novo, detached) — 5332 linhas brutas, segmentadas por imagem × pom × docs × scripts; todos os sítios de valor listados em §1-2 conferidos um a um) + consulta ao Registry API público do Docker Hub em 2026-10-27 (§3). Nenhuma edição de pom, compose, Dockerfile ou ADR. Pacote fecha a linha 7.4 do plano como EXECUTADO-com-apontamento.
