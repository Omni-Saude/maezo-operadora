# ADR-0068: Errata factual de ADRs Accepted fora do alcance da ADR-0041 (0013, 0014, 0015, 0017) — achado F-114, item R-072

**Status:** **Proposed — DRAFT/verify.** Redigido por AGENTE no item R-072 do roadmap forense; um agente nao aceita ADR. `/docs/adr/` e dono-gated (`.github/CODEOWNERS`). · **Data:** 2026-10-31 · **Area:** Governanca de ADR / Documentacao

> **Enquadramento.** Esta ADR so registra fatos. Ela nao decide arquitetura nova, nao muda
> comportamento e nao edita nenhuma ADR Accepted. Cada secao diz o que a ADR afirma, o que existe na
> arvore em `main` = `2be4515f`, e o marcador. Tudo que mudaria uma decisao ficou de fora e esta na
> secao "O que NAO e errata factual".

## Contexto

O achado F-114 da auditoria forense (2026-10, baseline `f57f0821`) mostrou ADRs Accepted citando
arquivos e simbolos que nao existem, sem marcador. O item R-072 pede a errata "pelo mecanismo
ratificado". Esse mecanismo existe: `docs/adr/README.md` (regra 2: "ADR aceito so muda por novo ADR
com `Supersedes`"), a `## Convencao seguida` da ADR-0032 e a da ADR-0041. A ADR-0041 foi aceita pelo
dono em 2026-10-31 (DEC-27, issue #1084). Pela convencao, a correcao de uma ADR Accepted e uma ADR
nova. Nenhum arquivo Accepted e editado.

Inventario completo das erratas do F-114 e do F-052, com quem cobre cada uma:

| ADR | Afirmacao estale | Coberta por |
|---|---|---|
| 0005 | PEP "antes de TODO tool call efetivo" | ADR-0041 §2 |
| 0006 | "mapa de reidentificacao" | ADR-0041 §6 |
| 0008 | matriz em `src/maezo/policies/` | ADR-0041 §3 |
| 0012 | pipeline `contract_extraction` "portado" | ADR-0041 §7 |
| 0013 | simulador Tasy como deliverable permanente de CI (clausula 3) | ADR-0037 (supersede parcial, clausula 3) |
| 0013 | topicos `agents.events.whatsapp.*` (clausula 5) | **esta ADR, §1** |
| 0014 | `config/otel-collector.yaml`, `config/prometheus.yml`, `alert-rules.yaml` | **esta ADR, §2** |
| 0015 | `config/topic_registry.yaml` | ADR-0041 §5 |
| 0015 | `AgentCardRegistry` | **esta ADR, §3** |
| 0017 | `provision_tenant.py` e `tests/unit/platform/test_provision_tenant.py` | **esta ADR, §4** |
| 0024 | `PostgresDedupeStore` e drivers removidos | ADR-0041 §1 + ADR-0048 |
| 0025 | artefato de grep do PEP | ADR-0041 §8 |
| 0032 | "delegation/dispatcher/facts nao existem" | ADR-0041 §4 |

## Decisao

Registramos as quatro correcoes de fato abaixo. Nenhuma rejeita a decisao original.

### §1 — ADR-0013, clausula 5: topicos WhatsApp nunca declarados

- **A ADR afirma** (secao Decisao, item 5): os topicos `agents.events.whatsapp.message-received` e
  `agents.events.whatsapp.status` existem "para agentes".
- **Na arvore:** zero ocorrencias de `agents.events.whatsapp` em `src/`, `spec/`, `config/` e
  `deploy/`. O canal WhatsApp despacha em processo (`src/maezo/platform/webhooks/whatsapp/dispatch.py`).
  A ADR-0037 diz que esses topicos "NAO sao tocados" por ela.
- **Marcador:** `obsolete-section` para a lista dos dois topicos. Se o desenho deve voltar a ter esses
  topicos e decisao do dono (ver "O que NAO e errata factual").

### §2 — ADR-0014: caminhos dos arquivos de observabilidade

- **A ADR afirma:** OTel Collector em `config/otel-collector.yaml` (Decisao e Consequencias), scrape
  config em `config/prometheus.yml`, regras em `deploy/observability/alert-rules.yaml`.
- **Na arvore:** `config/` so contem `config/integrations/`. Os arquivos reais sao
  `deploy/observability/otel-collector.yaml` (mais `deploy/observability/otel-collector-ecs-dev.yaml`
  para o dev em ECS), `deploy/observability/prometheus.yml` e `deploy/observability/alert-rules.yml`
  (extensao `.yml`).
- **Marcador:** `amended-by` (so caminhos). A decisao AMP/AMG nao muda.

### §3 — ADR-0015: nome da classe do registry

- **A ADR afirma** (Dependencias injetadas): `AgentCardRegistry`.
- **Na arvore:** a classe e `A2ARegistry` (`src/maezo/a2a/registry.py:37`). O docstring do modulo diz
  que o nome do doador foi trocado de proposito (`src/maezo/a2a/registry.py:4-6`).
- **Marcador:** `amended-by` (so o nome). O caminho do modulo que a ADR cita (`src/maezo/a2a/registry.py`)
  esta certo.

### §4 — ADR-0017: injetor e testes negativos citados nao existem

- **A ADR afirma:** o gate `quality` roda testes NEGATIVOS de
  `tests/unit/platform/test_provision_tenant.py` que renderizam endpoint PHI sem `cidr` ou com
  `0.0.0.0/0` e confirmam o abort. E: `provision_tenant.py` injeta a `effectiveDefinition` merged.
- **Na arvore:** nenhum arquivo rastreado tem `provision_tenant` no nome. Os `fail` do chart existem
  (`deploy/helm/maezo-tenant/templates/_helpers.tpl:131-139` para PHI, `:151-160` para a Zona Geral,
  `:94-97` para a divergencia de zona). Os testes que existem renderizam os values commitados e
  conferem que nao ha `0.0.0.0/0` (`tests/unit/platform/test_networkpolicy.py:90`, `:137`, `:187`;
  `tests/unit/deploy/test_networkpolicy_egress_r061.py:155`). **Nenhum teste rastreado renderiza um
  endpoint PHI sem `cidr` e confere o abort.** Os testes negativos de render que existem cobrem o
  endpoint OTel (`tests/unit/deploy/test_networkpolicy_egress_r061.py:118`, `:124`).
- **Marcador:** `amended-by` (caminhos inexistentes). A falta do teste negativo e um defeito de
  cobertura, nao de texto. Escrever o teste e follow-up de codigo, fora desta ADR.

## O que NAO e errata factual (vai ao dono)

1. **Ponteiro in-loco nas ADRs corrigidas.** A ADR-0041 (Consequencias, Negativas) deixa ao dono
   trocar "nenhuma linha movida" por "ponteiro no topo de cada ADR". O mesmo vale aqui.
2. **ADR-0013, topicos WhatsApp.** Apagar os topicos do desenho, ou construi-los, muda a decisao.
3. **ADR-0017, injetor de `effectiveDefinition`.** Sem injetor rastreado, a validacao cruzada de zona
   so roda quando alguem monta a definicao a mao. Dizer quem injeta e decisao de desenho.

## Consequencias

**Positivas:** junto com a ADR-0041 e a ADR-0037, todo item do F-114 tem correcao de registro
rastreavel pelo indice, sem mover uma linha das ADRs Accepted.

**Negativas (aceitas):** como na ADR-0041, quem abre a ADR Accepted isolada, sem o indice, nao ve a
correcao. O repro `F-114_adrs_accepted_refs_inexistentes.sh` da auditoria confere a existencia de
arquivos e de simbolos na arvore. Uma errata documental nao o deixa verde.

## Supersedes

—. **Amends** ADR-0013 (clausula 5, so a lista de topicos WhatsApp), ADR-0014 (caminhos), ADR-0015
(nome da classe) e ADR-0017 (caminhos citados). Nao edita nenhum dos quatro arquivos. Nao supersede
nenhum. Depende de aceite do dono.
