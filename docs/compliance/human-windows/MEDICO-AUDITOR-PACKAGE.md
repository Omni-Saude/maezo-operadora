# PACOTE MÉDICO-AUDITOR — ratificação auth-criteria, 4 DMNs de triage e o desvio C2 (muro 2027-02-09)

**Missão:** W5 CLNT 5.6 — pacote de evidência para a janela humana do médico-auditor (`docs/plans/maezo-completion-program.md` §W5 5.6 e §HDR).
**Autor:** engenheiro de pacotes (agente). **Data:** 2026-10-24. **Base:** `main` = `dc544a0e` (#965), checkout limpo de `origin/main`.
**Natureza:** INFORMATIVO. Este pacote **não decide, não ratifica e não aprova nada** (ADR-0007; cerca §0(4) do plano). Nenhum campo de revisor/data/decisão está preenchido aqui.
**Consumidor:** médico-auditor clínico (e, na pendência C2, o papel "Diretor(a) Médico(a)" declarado no desvio — `spec/policies/autonomy/action-approvals.yaml:466`).
**Regra de leitura:** toda afirmação de fato traz `arquivo:linha`; contagens somadas no fim (§6).

---

## 0. Resumo executivo — as três pendências do médico-auditor

O §HDR do plano registra três pendências humanas para este papel (plano `:139`):

| # | Pendência | Estado hoje | Prazo de verdade externa |
|---|---|---|---|
| A | Ratificar as **6 fontes** do portão auth-criteria (`spec/processes/dmn/auth-criteria-ratification.yaml`) | **NADA ratificado** — as 6 fontes `ratificado: false` (`auth-criteria-ratification.yaml:54-62`, `:46` do loader `src/maezo/tools/workers/auth_criteria.py`) | sem data própria; é pré-condição de qualquer rota automática de autorização (SP-OP-AUTH-001) |
| B | Revisar as **4 DMNs de triage red-flag** (conteúdo clínico DRAFT) | 4 tabelas `SHAPE FINAL / CONTEUDO CLINICO DRAFT` (`docs/review-queue.md:10-13`) | sem data própria; bloqueia qualquer deploy do caminho de triagem |
| C | Revisão do desvio **C2 `leitura_phi_clinica`** (sombra) | `enforcement: shadow`, 3 blocos de aprovação `PENDENTE` (`action-approvals.yaml:444`, `:493-495`) | **2027-01-26** fim da janela de evidências · **2027-02-09** muro + revisão C2 (plano `:17`; `review_by`/`checkpoint` em `action-approvals.yaml:469`,`:473`) |

Custo do silêncio, em uma linha cada: (A) toda autorização continua indo a análise humana para sempre, com a sombra acumulando além da janela de evidências; (B) o conteúdo clínico de triagem segue SINTÉTICO e sem valor probatório; (C) a partir de 2027-02-09 **todo PR do repositório fica VERMELHO** no gate obrigatório `validate-artifacts` enquanto o desvio continuar em sombra (`scripts/ci/check_deviation_expiry.py`; detalhes em §3.4). Detalhes e formato de resposta em §1–§5.

---

## 1. Pendência A — ratificar as 6 fontes do portão auth-criteria (GAP-AUTH-4, resíduo de DADOS)

### 1.1 Como o portão funciona (por que a ratificação é um ato só seu)

`operadora.auth.validate_auto_criteria` (`src/maezo/tools/workers/auth.py`) computa quatro critérios — tecnico / financeiro / regulatorio / contratual — e alimenta `auth_auto_approval.dmn`. Três dos quatro têm sua regra em DMNs cujo conteúdo clínico/regulatório/contratual é **SINTÉTICO e DRAFT**. A regra do portão (comentário de `auth-criteria-ratification.yaml:13-24`, espelhada no loader `auth_criteria.py:11-13`):

> "A criterion may contribute a PASS only if its rule source is RATIFIED here. A DRAFT / unlisted / malformed source yields `false` with a `*_FONTE_NAO_RATIFICADA` token, REGARDLESS of what the table computes."

Consequências que o portão já impõe hoje (fatos, não opinião):

- Com as três fontes de critério não ratificadas (tecnico/regulatorio/contratual saem `false`
  com `*_FONTE_NAO_RATIFICADA` — `auth-criteria-ratification.yaml:56-62`), **nada se
  auto-aprova**: todo pedido vai a análise humana. O teto financeiro NÃO é cadeado hoje:
  `spec/policies/autonomy/tenants-amh.yaml` fixa `authorization_approval.max_value_brl: 500`
  (D-07 FECHADA em 25/08/2026 para esta classe; o 0 com D-07 aberta é de
  `reembolso_auto_approval`), e o critério financeiro já passa sozinho até esse teto — o gate é
  conjuntivo, logo a não-ratificação das fontes é hoje O ÚNICO cadeado
  (`docs/sme-dispatch/medico-auditor/auth-criteria-ratification-dossier.yaml:39-43`, pinado por
  `tests/unit/docs/test_sme_dispatch_dossiers.py::test_the_stated_ceiling_matches_the_live_tenant_policy`;
  o comentário "valor 0 / D-07 aberta" em `auth-criteria-ratification.yaml:37-45` está STALE).
- A tabela continua sendo AVALIADA e o que ELA teria decidido fica gravado em `auto_criteria_shadow` (variável delimitada, sem PHI) — é a sua **evidência de sombra**: você ratifica contra desfecho real, não contra regra abstrata (`auth-criteria-ratification.yaml:21-24`; `auth.py:416`, `:971`).
- Ratificar é **mudança de DADOS**: `ratificado: true` (booleano literal) + `revisor` não vazio + `ratificado_em` "YYYY-MM-DD", por fonte. Qualquer coisa faltando/malformada ⇒ NÃO ratificado, fail-closed (`auth-criteria-ratification.yaml:26-35`; `_entry_is_ratified` em `auth_criteria.py:217-240`). O loader nunca assume ratificação por omissão e qualquer erro de carga do manifesto vira "nada ratificado" (`auth-criteria-ratification.yaml:32-35`).
- Pinado em runtime por `tests/unit/sec/test_auto_approval_criteria_fence.py` e `tests/unit/tools/workers/test_auth_auto_criteria.py`.

### 1.2 As 6 fontes que faltam ratificar (contagem: 6 = 6)

| # | Fonte (`decisao_id`) | Onde | O que a tabela decide hoje (SINTÉTICO) | Revisor(es) já declarados no manifesto |
|---|---|---|---|---|
| 1 | `dut_rol_coverage` | `auth-criteria-ratification.yaml:64-71`; tabela `spec/processes/dmn/dut_rol_coverage.dmn` (linha da fila: `docs/review-queue.md:105`) | Mapeamento TUSS→ROL/DUT com códigos TUSS sintéticos; flags `no_rol`/`requer_dut`; catch-all fail-safe | "medico auditor + juridico/regulatorio" |
| 2 | `dut_criteria_bariatrica` | `auth-criteria-ratification.yaml:73-79`; tabela `spec/processes/dmn/dut_criteria_bariatrica.dmn` (`docs/review-queue.md:106`) | Limiares clínicos sintéticos (IMC 35/40, tentativas prévias, avaliação multidisciplinar); conferir DUT ANS vigente + CFM 2.131/2015 | "medico auditor + cirurgiao bariatrico + juridico/regulatorio" |
| 3 | `dut_criteria_oncologia_pet_ct` | `auth-criteria-ratification.yaml:81-87`; tabela `spec/processes/dmn/dut_criteria_oncologia_pet_ct.dmn` (`docs/review-queue.md:107`) | Finalidades e lista de neoplasias elegíveis sintéticas; indicações neurológicas/cardiográficas NÃO cobertas (DUT separada, SME adicional) | "medico auditor + oncologista/nucleo-medicina + juridico/regulatorio" |
| 4 | `dut_criteria_terapias_especiais` | `auth-criteria-ratification.yaml:89-95`; tabela `spec/processes/dmn/dut_criteria_terapias_especiais.dmn` (`docs/review-queue.md:108`) | Modalidades e limites de sessões/mês sintéticos; conferir DUT ANS + Lei 12.764/2012 + Lei 14.254/2021 + RN 465/2021 | "medico auditor + neuropediatra/psiquiatra infantil + juridico/regulatorio" |
| 5 | `carencia_check` | `auth-criteria-ratification.yaml:97-105`; tabela `spec/processes/dmn/carencia_check.dmn` (`docs/review-queue.md:109`) | TODOS os prazos sintéticos (urgência 1d, parto 300d, eletivo 180d, CPT 730d); portabilidade RN 186/2009 não implementada | "juridico/regulatorio + medico auditor" |
| 6 | `auth_criteria_contratual` | `auth-criteria-ratification.yaml:107-116`; tabela `spec/processes/dmn/auth_criteria_contratual.dmn` (`docs/review-queue.md:21`) | Costura VAZIA e honesta: 1 regra catch-all → `false`/`SEM_REGRA_RATIFICADA`. Ratificar aqui só abre algo DEPOIS de um SME popular a tabela com regras reais de plano/produto/contrato/KPI (`docs/review-queue.md:21`) | "juridico/contratos + financas + medico auditor (criterios de KPI assistencial)" |

Três notas de leitura antes de decidir:

1. **O critério FINANCEIRO não está nesta lista de propósito**: sua fonte não é DMN, é a matriz de autonomia (`spec/policies/autonomy/L0-core.yaml` + `tenants-amh.yaml`, `authorization_approval.max_value_brl`) via `CeilingResolver`; o comentário do manifesto que diz "valor atual 0 / D-07 aberta" está STALE — o valor VIVO do tenant AMH é 500 com D-07 FECHADA em 25/08/2026 para `authorization_approval` (`spec/policies/autonomy/tenants-amh.yaml`; o 0/D-07-aberta vale para `reembolso_auto_approval`). Ratificar as 6 fontes acima não altera o teto.
2. **Bloqueio prévio à ratificação do contratual**: a seção executável `criterios_nao_cobertos:` (`auth-criteria-ratification.yaml:203-208`) declara `rede_credenciada` (fato contratual sem fonte de credenciamento legível por máquina no repositório) e SUPRIME a ratificação de `auth_criteria_contratual` mesmo que os três campos estejam preenchidos — o loader lê `bloqueia_ratificacao_de` e devolve `CONTRATUAL_FONTE_NAO_RATIFICADA` + log `auth_criteria_ratification_suppressed` (`auth-criteria-ratification.yaml:155-171`; `auth.py` — pinado em `tests/unit/tools/workers/test_auth_auto_criteria.py`). A resolução é mudança de DADOS no mesmo arquivo: popular a fonte e remover a entrada no mesmo commit.
3. **Roteamento DUT deliberadamente incompleto**: `mapeamento_dut_criteria` só declara as duas correspondências inequívocas (`auth-criteria-ratification.yaml:141-143`); um `dut_ref` com `requer_dut=true` sem entrada resolve FECHADO (`TECNICO_PROCEDIMENTO_NAO_MAPEADO`). As DUTs pendentes de SME estão listadas em `auth-criteria-ratification.yaml:135-139` (DUT-TERAPIA-OCUPACIONAL-001, DUT-FONOAUDIOLOGIA-001, DUT-PSICOTERAPIA-001, DUT-ONCOLOGIA-QUIMIO-001, DUT-ONCOLOGIA-RT-001, DUT-CINTILOGRAFIA-OSSEA-001, DUT-OPME-*, DUT-ALTA-COMPLEXIDADE-ANALISE-HUMANA) — completar o mapa é parte da mesma janela e também é mudança de DADOS.

**Insumo pronto:** o dossiê por tabela já existe em `docs/sme-dispatch/medico-auditor/AUTH-CRITERIA-RATIFICATION-DOSSIER.md` (+ `.yaml`), com "conteúdo atual → fonte normativa/clínica a conferir → o que muda ao ratificar" e o sha256 dos bytes de cada tabela em disco (`docs/review-queue.md:1632`, gap `AUTH-CRITERIA-RATIFICACAO-MANIFESTO`, R-163). Linhas da fila relativas: `docs/review-queue.md:17-24` (fluxo SP-OP-AUTH-001, formulário `UT_AnaliseMedicoAuditor` GAP-AUTH-2, `auth_admissibility.dmn`, `auth_auto_approval.dmn`, `auth_criteria_contratual.dmn`, manifesto, GAP-AUTH-4, `auth_sla.dmn`) e `:105-109` (as 5 DMNs de conteúdo DUT/carencia).

### 1.3 O que muda ao ratificar × impacto do silêncio

- **Ao ratificar uma fonte:** o critério dela passa a contribuir PASS real quando a tabela computa PASS; a rota automática de `SP-OP-AUTH-001` passa a existir de verdade — dentro do teto L2 e das quatro conjunções (`auth_auto_approval.dmn` v0.2.0, política de combinação a ratificar em `docs/review-queue.md:20`). Nenhum deploy de Python; nenhum flip de flag.
- **Silêncio:** a rota automática segue inerte (todo pedido humano) — seguro, porém com custo operacional permanente; a sombra `auto_criteria_shadow` segue acumulando além da janela de evidências C2 (2027-01-26, §3), e o resíduo de DADOS do GAP-AUTH-4 permanece aberto na fila (`docs/review-queue.md:23`).

---

## 2. Pendência B — as 4 DMNs de triage red-flag (conteúdo clínico DRAFT)

Consumidas pela Helena via `mcp-dmn.evaluate` — "o LLM NUNCA decide red flag; classifica sintomas em códigos normalizados e ESTA tabela decide" (`triage_redflag_adult.dmn:8-12`). Seleção da tabela por população: mapa `_DMN_BY_POPULATION` em `src/maezo/agents/helena/graph.py:1604-1609`, usado em `travas_entrada.py:123`; `red_flag=true`/`ESCALATE_*` dispara escalonamento imediato com `motivo_categoria=red_flag_clinico` e severidade derivada da prioridade (`travas_entrada.py:107-117`).

Contagem: **4 tabelas = 4 linhas da fila** (`docs/review-queue.md:10-13`).

| # | Tabela (caminho) | O que decide | Estado declarado |
|---|---|---|---|
| 1 | `spec/processes/dmn/triage_redflag_adult.dmn` (v0.1.0) | Lista de sintomas, limiares de intensidade/idade, prioridades P1/P2, regra fail-safe (`review-queue.md:10`; cabeçalho `:8-12`) | `SHAPE FINAL; CONTEUDO CLINICO: DRAFT — requer validacao de medico (diretriz de triagem) antes de qualquer deploy` |
| 2 | `spec/processes/dmn/triage_redflag_pediatric.dmn` (v0.2.0) | Conteúdo clínico pediátrico (limiar febre <3 meses, desidratação etc.). v0.2.0 (DL-0101, 14/10/2026): regras neonatais r9-r13 ratificadas PELO DONO em `spec/processes/dmn/triage-redflag-pediatric-neonatal-ratification.yaml` — **não é ratificação médica** (`review-queue.md:11`; cabeçalho `:9-11`) | `SHAPE FINAL; CONTEUDO CLINICO: DRAFT — requer validacao de pediatra`; revisão do pediatra/auditor recomendada |
| 3 | `spec/processes/dmn/triage_redflag_gestante.dmn` (v0.1.0) | Conteúdo clínico obstétrico (pré-eclâmpsia ≥20s, MF reduzidos ≥26s etc.; campo populacional `idade_gestacional_semanas`) (`review-queue.md:12`; cabeçalho `:8-11`) | `SHAPE FINAL; CONTEUDO CLINICO: DRAFT — requer validacao de obstetra` |
| 4 | `spec/processes/dmn/triage_redflag_mental_health.dmn` (v0.1.0) | Conteúdo de saúde mental + postura conservadora (`risco_imediato` default TRUE salvo falso explícito — coerção corrigida em `travas_entrada.py:130-140`) (`review-queue.md:13`; cabeçalho `:8-12`) | `SHAPE FINAL; CONTEUDO CLINICO: DRAFT — requer validacao de psiquiatra/psicologo` |

Leituras que a ratificação precisa disposicionar (achados já registrados, nenhum corrigido):

- **Candidatos-sombra M-7** (`docs/review-queue.md:642`): para gestante e pediatria, os candidatos NÃO propõem mudança de regra nenhuma — provam por exaustão que a tabela já emite o veredito mais severo possível sob FIRST-hit; os defeitos são **lacunas de banda** (ACHADO-P1: febre só é red flag em `idade_meses < 3` — nenhum regra de 3 meses em diante; ACHADO-P2: `intensidade="desconhecida"` nunca levanta bandeira; ACHADO-G1: perda de líquido/contrações param em `IG < 37` — a termo não há regra; ACHADO-G2: pré-eclâmpsia só a partir de 20 semanas; ACHADO-G3: movimentos fetais só a partir de 26 semanas). Ratificar aqui = o clínico REGISTRA que leu e disposicionou os achados; fechar qualquer um deles é edição DIRETA da tabela viva, com conteúdo que só ele pode escrever. Cada candidato pinado por teste vetorial contra a tabela viva.
- **Binding sha256 (#221):** ratificar exige que o `tabela_viva.sha256` de cada manifesto ainda bata com os bytes da tabela no disco — editar a tabela invalida a ratificação pendente/concedida até re-revisão (`docs/review-queue.md:642`).
- **Pergunta de DPO acoplada:** `sintoma_codigo` é input das 4 tabelas (`triage_redflag_adult.dmn:26`, `gestante:19`, `pediatric:22`, `mental_health:22`) e está em pergunta aberta DPO + médico auditor + arquitetura (`docs/review-queue.md:976`) — a revisão clínica não responde por ela, mas as duas janelas compartilham os mesmos revisores.
- **Estado de implantação (fato):** as tabelas SÃO avaliadas no grafo da Helena (`travas_entrada.py:134-136`), mas o nó de coleta da Frente 2.2 segue desligado (`coleta_enabled=False` default — `src/maezo/agents/helena/graph.py:388`, `src/maezo/platform/webhooks/whatsapp/dispatch.py:946`); a tabela de suficiência `triage_sufficiency.dmn` **não está implantada** (`docs/review-queue.md:969`). Nenhum conteúdo clínico novo entra em vigor por ratificar a leitura — o que entra em vigor é o registro de que um clínico validou o conteúdo.
- **Impacto do silêncio:** o conteúdo clínico permanece SINTÉTICO e a fila segue com 4 linhas DRAFT — violando o alvo do programa "0 linhas DRAFT sem revisor nomeado" para estas linhas (revisor está nomeado como PAPEL, falta o humano) e impedindo qualquer promoção do caminho de triagem.

---

## 3. Pendência C — o desvio C2 `leitura_phi_clinica` (sombra; muro 2027-02-09)

### 3.1 O que é C2

C2 é a classe de efeito `leitura_phi_clinica` — leitura de dado clínico PHI via FHIR — registrada como **desvio Q-10** legível por máquina em `spec/policies/autonomy/action-approvals.yaml:443-495` (comentário do desvio `:446-464`; "declarar um prazo não é aprovar", `:450-451`). Estado:

- `enforcement: shadow` (`:444`) — nada enforça; cada decisão produz evidência de sombra via `GatedFhirReader` (`src/maezo/gateway/seams/fhir.py:44`), que embrulha as duas superfícies do leaf `FhirServer` (`src/maezo/tools/mcp_fhir/server.py:88` `read_resource`, `:118` `search_resources` — `action-approvals.yaml:479-490`).
- **24 ações** mapeadas para a classe (`mapeamento_acoes`, `action-approvals.yaml:773-796` — de `agente.fhir.read_patient` a `agente.amh.resolve_by_document`).
- Três domínios exigidos para qualquer ALLOW futuro — `dominios_exigidos: [medica, ans, seguranca]` (`:491`) — e os três blocos de aprovação `PENDENTE` (`:493-495`). O papel que este pacote interessa: **medica**.
- Papel-dono do desvio: `owner_role: "Diretor(a) Médico(a) (interim, deadline-enforcement only: dono)"` (`:466`), ratificado pelo dono em 2026-08-13 (`:467-468`).

### 3.2 O calendário (verdade externa)

| Data | O que é | Fonte |
|---|---|---|
| **2027-01-26** | Fim da janela de evidências C2 (planificado: 14 dias antes do muro) | plano `:17`; `scripts/ci/check_deviation_expiry.py:86` (`WARNING_WINDOW_DAYS = 14`) |
| **2027-02-09** | Muro shadow + revisão C2 — `review_by: "2027-02-09"` e `checkpoint: "2027-02-09"` do desvio C2 | plano `:17`; `action-approvals.yaml:469`, `:473` (renovação do dono 2026-09-20, PR fix/d7-exception-renewal-2027; F-P04 emendado GOV-0.2, plano `:36`) |

O que expira em 2027-02-09 (as cercas same-date, todas numa única revisão do dono — memo MEMOS 2026-09-19 opção B, `action-approvals.yaml:151-153`):

1. O desvio C2 `leitura_phi_clinica` em sombra (`review_by` `:469`).
2. O desvio raiz Q-2 do mesmo arquivo (`expires: "2027-02-09"`, `:154`; owner "Security/crypto R1 reviewer (interim: dono)", `:148`).
3. As cercas irmãs do mesmo dia: catálogo D7 de xfails (`tests/unit/ci/test_check_d7_xfail_expiry.py:10`) e ciclo de vida expected-fail-until (`tests/unit/ci/test_check_lifecycle_expected_fail_expiry.py:338-342`).

### 3.3 Como a evidência de sombra é produzida (o que você revisa)

- O gate grava o que cada decisão teria feito, sem sancionar — a evidência existe para ser revisada contra dado real antes do muro (`action-approvals.yaml:12-15`, `:479-481`).
- A mesma mecânica de sombra alimenta a Pendência A: `auto_criteria_shadow` guarda o que as DMNs de critério TERIAM decidido (`auth.py:416`, `:971`; `auth-criteria-ratification.yaml:21-24`) — uma única janela de leitura serve às duas pendências.
- O dashboard de desvios shadow é item W7 7.5 do programa ("muro 2027-02-09; só de dados rastreados" — plano `:105`).

### 3.4 O que acontece na data (mecânica do gate — não é escolha de ninguém)

`scripts/ci/check_deviation_expiry.py` (cabeado no job `artifact-validation`, check obrigatório `validate-artifacts` — `action-approvals.yaml:127-129`):

- **VERMELHO em TODO PR a partir de `expires`/`review_by`** enquanto o valor continuar `shadow` (`:129`); VERMELHO também se o bloco sumir ou vier malformado com sombra (fail-closed: apagar o prazo ≠ não ter prazo, `:130-131`).
- **AVISO alto (sem reprovar) nos 14 dias que antecedem** — ou seja, de 2027-01-26 a 2027-02-09 (`:132`; janela de evidências fecha junto).
- **ISENTO quando o valor virar `enforcing`** — o desvio acabou e vira histórico (`:133`).
- **Não existe renovação silenciosa**: sair do vermelho exige ato humano — virar `enforcing` (ato terminal do rollout) OU o dono re-ratificar um desvio NOVO E DATADO, PR de dados sob CODEOWNERS (`:134-137`; o arquivo é `@rodaquino-OMNI @Omni-Saude/security-team` — `.github/CODEOWNERS:128`).
- O gate lê pelo loader (`ActionApprovals.deviations`, `src/maezo/gateway/action_execution.py`), nunca por YAML cru (`:139-140`).

### 3.5 Decisões que a janela C2 pede (nenhuma tomada por este pacote)

1. **Disposição do desvio**: virar `enforcing` (exigiria as aprovações medica+ans+seguranca da classe — `:493-495` — e a ordem de rollout C0→C4 do design §9.4), ou re-ratificar desvio novo e datado, ou manter e pagar o vermelho do CI. O plano do programa já declara que **nenhum flip shadow→enforcing entra neste programa** (plano `:146` — "Fora de escopo (→ ROLL)").
2. **Preenchimento (opcional, ato seu) do bloco `medica`** da classe: `aprovado: true` + `aprovador` + `data` + `evidencia_ref` (janela de telemetria de sombra revisada) — formato em `action-approvals.yaml:31-45`. Um arquivo `status: DRAFT` NUNCA produz ALLOW em modo nenhum (`:27-29`), então preencher o bloco é registro de atestado, não efeito.
3. **Impacto do silêncio:** a partir de 2027-02-09, cada PR do repositório falha no check obrigatório até que o dono re-ratifique ou flippe — custo organizacional total, independente deste papel; para o médico-auditor, a evidência de sombra acumulada antes de 2027-01-26 deixa de ter janela de leitura formal.

---

## 4. Formato de resposta esperado (decisão assinável — você preenche, agente não preenche)

Cada pendência vira um PR de DADOS (nunca código), sob CODEOWNERS, com quem decide como autor/revisor humano:

**A — Ratificação auth-criteria** — em `spec/processes/dmn/auth-criteria-ratification.yaml`, por fonte (bloco de `fontes:`):

```yaml
  <decisao_id>:
    ratificado: true                  # booleano literal true — nada mais conta
    revisor: "<nome, registro, papel>"  # não vazio (ADR-0007)
    ratificado_em: "AAAA-MM-DD"       # não vazio
```

(± remoção da entrada de `criterios_nao_cobertos.rede_credenciada` no mesmo commit, se for o caso — §1.2 nota 2; ± completamento de `mapeamento_dut_criteria` — §1.2 nota 3.)

**B — DMNs de triage** — uma linha por tabela em `docs/review-queue.md` movendo o status de `DRAFT — requires human review` para a disposição real (ratificado / recusado / condicionado), PR com assinatura do revisor humano; qualquer edição de conteúdo clínico é edição direta da tabela viva (quebra o binding sha256 do manifesto-sombra e exige re-revisão — §2). Se o contrato da casa for usado, o protocolo de signoff é o de `docs/sme-dispatch/README.md` §"Signoff artifact spec" (`docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml`).

**C — Desvio C2** — OU re-ratificação datada do bloco `deviation` (PR de dados, CODEOWNERS `:128`), OU disposição de enforcing com os três blocos de aprovação preenchidos (formato `action-approvals.yaml:31-45`), OU deferral datado registrado pelo dono. O bloco declara: "NENHUM AGENTE PODE EDITAR ESTE BLOCO" (`:463`).

---

## 5. Impacto do silêncio — resumo

| Pendência | Silêncio custa |
|---|---|
| A (auth-criteria) | rota automática de autorização permanentemente inerte; resíduo de DADOS do GAP-AUTH-4 aberto; sombra acumulando além da janela |
| B (4 DMNs) | 4 linhas DRAFT vivas; conteúdo clínico sintético sem valor probatório; caminho de triagem sem promoção |
| C (C2) | VERMELHO em todo PR do repositório a partir de 2027-02-09 até ato do dono; janela de evidências morre em 2027-01-26 |

## 6. Contagens conferidas

- **6 fontes** no manifesto (`fontes:` — §1.2; loader `auth_criteria.py:46` declara "all six sources") = 6 listadas. ✓
- **4 DMNs de triage** (`docs/review-queue.md:10-13`) = 4 tabelas em `spec/processes/dmn/`. ✓
- **24 ações** mapeadas para `leitura_phi_clinica` (`action-approvals.yaml:773-796`) — contagem por varredura do bloco `mapeamento_acoes`. ✓
- **3 domínios** de aprovação da classe (`medica/ans/seguranca`, `:491`) com 3 blocos `PENDENTE` (`:493-495`). ✓
- **2 cercas same-date adicionais** (D7 xfail; lifecycle expected-fail-until) além do desvio C2 e do desvio raiz Q-2. ✓
