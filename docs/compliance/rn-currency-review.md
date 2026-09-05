# RN Regulatory-Currency Review — DRAFT (pending SME confirmation)

**Status:** `DRAFT — pending SME confirmation (regulatório / jurídico)` — analyst-side findings, **not** a finalized regulatory position.
**Task:** T2.5 (draft phase) — V2-COMPLETION-PLAN §3, tier R1 (compliance-analyst).
**Access date for all web sources:** 2026-07-16 (`currentDate`).
**Scope of inventory:** every `RN` citation in `docs/processes/` (incl. `contracts/`), `spec/`, and `src/`.
**What this document does NOT do:** it does not edit the contracts, DMNs, BPMNs, or code. Redlines below are **proposed** (quoted diff blocks), to be applied by SMEs after sign-off. SME dispatch packages (`docs/sme-dispatch/regulatorio/`, `docs/sme-dispatch/juridico/`, PR #27, branch `t0.6-sme-dispatch`) pose the RN-currency questions; **this report is the analyst-side answer sheet those SMEs should receive alongside the packages.**
**Aditamento 2026-09-04 (R-171):** §1 desta tabela foi expandida em `docs/sme-dispatch/regulatorio/RN-CURRENCY-MATRIX.md` (+ `rn-currency-matrix.yaml`) para uma matriz linha-a-linha (norma × arquivo:linha × contratos afetados), preparada para a sessão única e datada de regulatório + jurídico que ratifica este relatório — sem alterar nenhum veredito aqui.

> **Method & fail-closed discipline.** Each distinct RN was checked via WebSearch/WebFetch against authoritative sources only: `bvsms.saude.gov.br` (Ministério da Saúde — *Saúde Legis*, official mirror of the DOU text), `www.gov.br/ans` and `www.ans.gov.br` (ANS official), and `www.in.gov.br` (Imprensa Nacional — DOU). No RN number, subject, URL, or supersession below was invented. Where a fact rests on a single search summary of an authoritative page rather than a direct page fetch, it is flagged **verify (SME)**. Two ANS pages were fetched directly and are marked **[fetched]**. Anything the tools could not resolve is marked **cannot-verify — requires SME**, never "assumed current."

---

## 0. Verdict counts (at a glance)

| Verdict | Count (distinct RN citations) |
|---|---|
| **superseded / revogada** (cited RN no longer in force) | **11** — RN 124/2006, 162/2007, 186/2009, 195/2009, 209/2009, 259/2011, 305/2012, 388/2015, 395/2016, 412/2016, 567/2018¹ |
| **vigente but MISATTRIBUTED subject and/or WRONG YEAR** | **4** — RN 424/2017 (subj), 567/2022 (year+subj)¹, 388 (year), 305 (year) |
| **current — correctly cited** | **6** — RN 465/2021, 473/2021, 501/2022, 566/2022, 593/2023², 643/2025 |
| **cannot-verify — requires SME** | see §5 register (SIP successor exact norm; RN 593 day-basis; DIOPS governing norm; reembolso 30-day norm) |

¹ RN 567 is a single citation that is **both** wrong-year (2018 → 2022) **and** subject-misattributed (non-hospital, not hospital). Counted once under each lens.
² RN 593/2023 is *correctly in force and correctly used for inadimplência*, but the **claim that it "consolida/substitui RN 412/2016" is false** (see §2.7).

---

## 1. Executive summary — citation → verdict → source

Representative `file:line` shown; full citation loci in §3 redlines. "Current norm" = the norm actually in force for the subject as of 2026-07-16.

| Cited RN (repo) | Repo's claimed subject | Verified subject | **Verdict** | Current norm | Authoritative source (access 2026-07-16) |
|---|---|---|---|---|---|
| **RN 124/2006** — `SP-OP-ANS-SUBMIT-001.md:6` | "SIP — Sistema de Informações de Produtos" | **Aplicação de penalidades** por infrações à legislação de planos | **superseded + MISATTRIBUTED** | **RN 489/2022** (penalidades). *SIP itself* → RN 551/2022, **obligation ending Q1/2026** by RN 639/2025 → Monitoramento TISS | ans.gov.br `component/legislacao` id=Nzkw (RN124); bvsms `res0489_31_03_2022.html`; gov.br `avisos-para-operadoras/ans-revoga-obrigatoriedade-de-envio-do-sip-a-partir-de-2026` |
| **RN 162/2007** — `spec/.../carencia_check.dmn:25,96,107` | CPT / DLP / art. 11 Lei 9.656 | Carta de Orientação ao Beneficiário, DLP, CPT, DS | **superseded** | **RN 558/2022** (in force 01/02/2023) | bvsms `res0162_17_10_2007.html`; bvsms `res0558_30_12_2022.html` |
| **RN 186/2009** — `docs/review-queue.md:87` | Portabilidade de carências | Portabilidade de carências | **superseded** | **RN 438/2018** | bvsms `res0438_05_12_2018.html`; in.gov.br id=53493318 (ementa: "revoga a RN 186/2009") |
| **RN 195/2009** — `spec/.../carencia_check.dmn:22,174,185,202` | Contratação / carências eletivas | Classificação e **contratação** de planos (individ./coletivo) | **superseded** | **RN 557/2022** | bvsms `res0195_14_07_2009.html`; bvsms `res0557_30_12_2022.html` |
| **RN 209/2009** — `SP-OP-ANS-SUBMIT-001.md:6` | "utilização de serviços" | **Recursos Próprios Mínimos / Provisões Técnicas** (garantias financeiras / capital) | **superseded + MISATTRIBUTED** | **RN 451/2020** (capital regulatório) | bvsms `res0451_12_03_2020.html`; in.gov.br 247535376 (ementa: "revoga a RN 209/2009") |
| **RN 259/2011** — `SP-OP-AUTH-001.md:5,101`; `ADEQUACAO-001.md:5…`; `REEMBOLSO-001.md:5`; `auth_sla.dmn:10,42` | Garantia de atendimento; tempos/distâncias máximas | Garantia de atendimento (prazos máximos) | **superseded** | **RN 566/2022** (prazos **em dias úteis**) | bvsms `res0566_02_01_2023.html`; **gov.br `consumidor/prazos-maximos-de-atendimento` [fetched]** — header "Prazos máximos de atendimento (em dias úteis)" |
| **RN 305/2012** — `SP-OP-CONTAS-001.md:5,184` | Padrão TISS / Componente de Comunicação | Padrão TISS | **superseded** | **RN 501/2022** (revogou 305/2012 + 341/2013) | bvsms `res0501_01_04_2022.html`; legisweb 245930 (RN305, 09/10/2012) |
| **RN 305/2016** — `SP-OP-RECURSO-001.md:6,196` | TISS / glosa-recurso | *(RN 305 is 2012, not 2016)* | **WRONG YEAR + superseded** | **RN 501/2022** | as above |
| **RN 388/2015** — `SP-OP-ANS-SUBMIT-001.md:6` | "indicadores/transparência" | **Ações fiscalizatórias / NIP** | **superseded + MISATTRIBUTED** | **RN 483/2022** | in.gov.br id=33345888 (RN388, 25/11/2015); bvsms `res0483_31_03_2022.html` |
| **RN 388/2016** — `SP-OP-NIP-001.md:5,141,225,226`; `nip_sla.dmn` | NIP — prazos de resposta | NIP *(but norm is 388/**2015**, now revoked)* | **WRONG YEAR + superseded** | **RN 483/2022** (NIP resp.: 5 d.ú. assistencial / 10 d.ú. não-assist.) | bvsms `res0483_31_03_2022.html`; gov.br `.../nip/orientacao-nip...pdf` |
| **RN 395/2016** — `SP-OP-AUTH-001.md:5,102,105`; `auth_sla.dmn:11,50` | Resposta / negativa por escrito; 5 d.ú. | Regras de atendimento / prazos de resposta / negativa | **superseded** | **RN 623/2024** (in force 01/07/2025) | **gov.br `operadoras/atendimento-ao-beneficiario-diretrizes-da-rn-no-623-2024...` [fetched]**; bvsms `res0623_19_12_2024.html` |
| **RN 412/2016** — `SP-OP-CANCEL-001.md:5,19,236` | Cancelamento a pedido; *"593 substitui 412"* | Cancelamento a pedido / exclusão de beneficiário | **superseded (by RN 561, NOT 593)** | **RN 561/2022** (in force 01/02/2023) | bvsms `res0561_30_12_2022.html`; ans.gov.br id=MzMyNA%3D%3D (RN412) |
| **RN 424/2017** — `SP-OP-AUTH-001.md:5` ✓; `ANS-SUBMIT-001.md:6` ✗; `CONTAS-001.md:5,146` ✗; `RECURSO-001.md:6,179` ~ | "junta médica" ✓; "padrão TISS/monitoramento" ✗; "recurso/análise de conta" ✗ | **Junta médica ou odontológica** (divergência técnico-assistencial) | **vigente; CORRECT for junta médica; MISATTRIBUTED elsewhere** | RN 424/2017 (junta); **TISS/glosa → RN 501/2022** | bvsms `res0424_27_06_2017.html`; gov.br `faq_junta_medica_2021-v2.pdf` |
| **RN 465/2021** — `dut_rol_coverage.dmn:23,34`; `carencia_check.dmn:18` | Rol de Procedimentos | Rol de Procedimentos e Eventos em Saúde | **current — correct** (base norm, many amendments) | RN 465/2021 (+ 473, 624, 625, 643…) | ans.gov.br id=NDAzMw%3D%3D; gov.br `Anexo_I_Rol_2021RN_465.2021_RN643.2025.pdf` |
| **RN 473/2021** — `dut_rol_coverage.dmn:23`; `dut_criteria_terapias_especiais.dmn:21` | Emenda ao Rol | Altera RN 465/2021 (cirurgia antiglaucomatosa) | **current — correct** | RN 473/2021 | bvsms `res0473_08_11_2021.html` |
| **RN 501/2022** — `SP-OP-RECURSO-001.md:6,196` | Padrão TISS / glosa-recurso | Padrão TISS (revogou 305/2012 + 341/2013) | **current — correct** | RN 501/2022 | bvsms `res0501_01_04_2022.html` |
| **RN 566/2022** — `SP-OP-ADEQUACAO-001.md:111,148…`; `SP-OP-CRED-001.md:5,61` | Dimensionamento / garantias de rede | **Garantia de atendimento (prazos máximos)** — successor of RN 259 | **current — correct** (note it *is* the 259 successor; keep, retire 259) | RN 566/2022 | bvsms `res0566_02_01_2023.html`; gov.br prazos page **[fetched]** |
| **RN 567 "/2018"** — `SP-OP-CRED-001.md:5…`; `SP-OP-FRAUDE-001.md:5` | "descredenciamento de prestador **hospitalar**" | **Substituição de prestadores NÃO hospitalares** | **WRONG YEAR (2022) + subject partial-MISATTRIBUTION**; norm itself **vigente** | **RN 567/2022** (não-hospitalar); **hospital → RN 585/2023** (uncited, §4) | bvsms `res0567_30_12_2022.html`; cbr `RN-ANS-de-2022-no-567_Substituicao-de-prestadores-de-servicos-nao-hospitalares.pdf` |
| **RN 593/2023** — `INADIMPLENCIA-001.md:5…`; `CANCEL-001.md:5,31`; `FRAUDE-001.md:5` | Cancelamento/rescisão + inadimplência; *"consolida 412"* | **Notificação por inadimplência** (pré-requisito p/ suspensão/rescisão) | **current — correct for inadimplência; the "consolida/substitui 412" claim is FALSE** | RN 593/2023 (inadimplência); cancelamento a pedido → RN 561/2022 | bvsms `res0593_20_12_2023.html`; gov.br `Perguntas_Frequentes_FAQ__RN_593.pdf` |
| **RN 643/2025** — `dut_rol_coverage.dmn:24,34`; `dut_criteria_terapias_especiais.dmn:21` | Emenda ao Rol | Altera RN 465/2021 (IMRT canal anal; in force 01/09/2025) | **current — correct** | RN 643/2025 | bvsms `res_0643_13_08_2025.html`; gov.br Anexo I Rol RN643 |

**Highest-impact supersessions (ranked):**
1. **`SP-OP-ANS-SUBMIT-001` / `SP-OP-ANS-CRON-001` are almost entirely miscited** — of the 4 RNs in the gatilho (124, 209, 388, 424), **three are subject-misattributed** and **all three of those are revoked** (124→489/2022, 209→451/2020, 388→483/2022); the 4th (424) is junta-médica, not TISS. Moreover **the SIP obligation these processes automate is being extinguished from Q1/2026** (RN 639/2025 → Monitoramento TISS). This is the single most affected contract and should be re-scoped, not just re-cited.
2. **RN 259/2011 → RN 566/2022** across the whole authorization/adequação/reembolso family — the *current* norm (566) is already cited in ADEQUACAO/CRED but the stale 259 co-exists throughout; prazos are **dias úteis** (see §2, §SLA).
3. **RN 395/2016 → RN 623/2024** (in force 01/07/2025) — the written-denial / response-deadline norm underpinning `SP-OP-AUTH-001` and `auth_sla.dmn` changed; RN 623 tightens negativa-por-escrito fundamentação.
4. **RN 412/2016 → RN 561/2022** (not RN 593) — corrects a false consolidation claim in `SP-OP-CANCEL-001`.
5. **RN 388/2015 (NIP) → RN 483/2022** — NIP is the most schedule-critical timer family (Phase 2); wrong year (2016) and revoked norm cited.

---

## 2. Per-RN verified findings (evidence detail)

### 2.1 RN 259/2011 → **RN 566/2022** (garantia de atendimento; prazos em DIAS ÚTEIS)
- RN 259/2011 (garantia de atendimento dos beneficiários) was **revoked by RN 566/2022** (pub. DOU 02/01/2023). RN 566 keeps most of 259's structure and re-states the maximum times.
- **Day basis — verified [fetched]:** gov.br `.../consumidor/prazos-maximos-de-atendimento` renders the table header **"Prazos máximos de atendimento (em dias úteis)"**. Examples: consulta básica (pediatria, clínica médica, cirurgia geral, GO) **7 dias úteis**; demais especialidades **14 d.ú.**; fono/nutri/psico/TO/fisio **10 d.ú.**; diagnóstico laboratorial **3 d.ú.**; demais SADT ambulatorial **10 d.ú.**; PAC (alta complexidade) **21 d.ú.**; urgência/emergência **imediato**. Counted "da data da demanda … até sua efetiva realização."
- Note: the consumer page text still *names* RN 259 in a legacy passage — the ANS page is itself partially stale — but the deadlines and the **dias-úteis** basis are the ones in force; the governing norm is RN 566/2022. **verify (SME):** confirm no later partial revocation of RN 566 (RN 623/2024 does **not** revoke it — they coexist; see §2.11).

### 2.2 RN 388 (NIP) → **RN 483/2022**
- Two problems in the repo: (a) `SP-OP-ANS-SUBMIT-001.md:6` cites **RN 388/2015** as "indicadores/transparência"; (b) `SP-OP-NIP-001.md`/`nip_sla.dmn` cite **RN 388/2016** as NIP. The **correct** identity is **RN 388, de 25/11/2015**, which "estabelece os procedimentos adotados pela ANS para estruturação e realização de suas ações fiscalizatórias" — i.e., **fiscalização + NIP**, not "indicadores." The 2016 year is wrong.
- **RN 388/2015 was revoked by RN 483, de 29/03/2022** (in force 31/03/2022). RN 483 now governs NIP + fiscalização. NIP response prazos under RN 483: **5 dias úteis (assistencial)**, **10 dias úteis (não-assistencial)**; operator uploads response within **10 dias úteis** of notification. These match the repo's `P5D`/`P10D` numeric magnitudes (the *value* survives; the *citation* and *day-basis note* do not).

### 2.3 RN 395/2016 → **RN 623/2024** (in force 01/07/2025)
- RN 395/2016 (regras de atendimento ao beneficiário; resposta / negativa por escrito) was **replaced by RN 623/2024** — verified **[fetched]** gov.br diretrizes page: RN 623 "defines the new rules for the service provided by health plan operators … whether assistential or non-assistential … in all types of contracting," effective **01/07/2025**, principles of transparency/traceability/resolutividade. Corroborating searches: RN 623 "substituiu a RN 395/2016."
- RN 623 response-handling relevant to `auth_sla.dmn`: não-assistencial **7 dias**; urgência/emergência imediato / **5 dias úteis**; alta complexidade e internação eletiva **até 10 dias úteis**; **negativa reduzida a termo com fundamentação explícita e linguagem clara**; reanálise → ouvidoria **7 dias úteis**. **verify (SME):** exact per-class figures and day-basis in the RN 623 text before any timer change.

### 2.4 RN 412/2016 → **RN 561/2022** (NOT RN 593)
- RN 412/2016 (solicitação de cancelamento a pedido / exclusão) was **revoked by RN 561/2022** (in force 01/02/2023). RN 561 = "pedido de cancelamento tem efeito imediato a partir da ciência da operadora; irretratável."
- **Therefore `SP-OP-CANCEL-001`'s premise that "RN 593/2023 consolida/substitui RN 412/2016" is incorrect.** RN 593 is a *different* subject (inadimplência notification, §2.7). The open question logged in `INADIMPLENCIA-001.md:210` ("RN 593 supersede/consolida RN 412/2016?") is now **answerable: NO** — 412's successor is 561/2022; 593 governs inadimplência.

### 2.5 RN 305/2012 (TISS) → **RN 501/2022**
- RN 305 is **de 09/10/2012** (the `RECURSO-001` "305/2016" year is wrong). It was **fully revoked by RN 501/2022** (which also revoked RN 341/2013). The TISS Componente de Comunicação now lives in RN 501/2022 — a norm the repo *already cites correctly* in `RECURSO-001`, so `CONTAS-001`'s "RN 305/2012" and `RECURSO-001`'s "RN 305/2016" should both resolve to RN 501/2022.

### 2.6 RN 424/2017 — vigente, but misattributed outside AUTH
- RN 424/2017 = **junta médica ou odontológica** para dirimir divergência técnico-assistencial (formação por 3 profissionais; parecer do desempatador vinculante). **Vigente.**
- **Correct** in `SP-OP-AUTH-001` (junta médica). **Misattributed** in `SP-OP-ANS-SUBMIT-001.md:6` ("padrão TISS / monitoramento" — that is RN 501/2022 + Monitoramento TISS, not 424) and in `SP-OP-CONTAS-001.md:5,146` ("prazos de análise/recurso de conta" — glosa/recurso is TISS/RN 501, not junta médica). In `RECURSO-001` the *junta* facet is fine but "prazo recursal de glosa" is TISS.

### 2.7 RN 593/2023 — vigente & correctly used for inadimplência; consolidation claim false
- RN 593, de 19/12/2023: **notificação por inadimplência** como pré-requisito para exclusão/suspensão/rescisão unilateral por iniciativa da operadora. Requisitos: mínimo **2 mensalidades não pagas** (consecutivas ou não) em 12 meses; **notificação até o 50º dia** de inadimplência; exclusão/rescisão só **10 dias após a notificação** e se o débito persistir.
- It **revokes only** §§1º–3º art.4º e §§1º–2º art.8º da RN 527/2022, §8º art.9º da RN 522/2022, e o par. único art.12 da RN 523/2022, e **cancela a Súmula Normativa 28/2015**. It does **not** revoke RN 412/2016, RN 561/2022, or Lei 9.656 art. 13 (it disciplines the art.-13 notification).
- **Conclusion:** the repo's use of RN 593 for the inadimplência purge/notification window (`INADIMPLENCIA-001`, and the suspension-by-inadimplência terminal in `CANCEL-001`) is **correct**. Only the "consolida 412" framing and the RN 412 citation for *cancelamento a pedido* are wrong.
- **verify (SME):** RN 593's "50º dia" / "10 dias" — the repo encodes these as ISO calendar durations; confirm dias corridos vs úteis and the counting anchor (see §SLA).

### 2.8 RN 567 — year 2018 → **2022**; non-hospital, not hospital
- **RN 567, de 16/12/2022** (pub. 30/12/2022): **substituição de prestadores de serviços de atenção à saúde NÃO hospitalares** (equivalente + comunicação 30 dias). Revogou RN 365/2014 + IN 56/2014.
- Repo issues: `SP-OP-FRAUDE-001.md:5` cites "RN 567/**2018**" (wrong year). `SP-OP-CRED-001` describes RN 567 as "descredenciamento de prestador **hospitalar**" — but hospital network change is **RN 585/2023** (§4). RN 567 = non-hospital. `SP-OP-CRED-001` explicitly models **hospital** descredenciamento (`End_PrestadorDescredenciado`, "hospital/prestador com beneficiários vinculados"), so it needs **both** RN 585/2023 (hospital) and RN 567/2022 (non-hospital), each for the right prestador type.

### 2.9 RN 124/2006 — penalties, not SIP; revoked by RN 489/2022; SIP itself extinguished 2026
- **RN 124/2006 = "aplicação de penalidades para as infrações à legislação dos planos"** — **revoked by RN 489/2022.** It is **not** the SIP norm; `SP-OP-ANS-SUBMIT-001.md:6`'s "RN 124/2006 (SIP)" is a **misattribution**.
- The **SIP (Sistema de Informações de Produtos)** obligation was governed by **RN 551/2022**; **RN 639/2025 revokes RN 551/2022 and extinguishes the SIP submission obligation from the 1º trimestre de 2026**, migrating assistencial data to **Monitoramento TISS** (in force 02/03/2026). **As the environment date is 2026-07-16, the SIP filing this contract automates is already discontinued.** → **High-impact; requires SME (regulatório).** The exact successor norm number (RN 639/2025) rests on an authoritative gov.br aviso summary — **verify (SME)** the norm number and the precise Monitoramento-TISS obligation that replaces SIP.

### 2.10 Older carência/portabilidade norms
- **RN 162/2007** (Carta de Orientação, DLP, CPT, DS) → **revoked by RN 558/2022** (01/02/2023).
- **RN 186/2009** (portabilidade de carências) → **revoked by RN 438/2018** (ementa explicit).
- **RN 195/2009** (contratação de planos) → **revoked by RN 557/2022**. **verify (SME):** confirm 557/2022 is the full successor and whether carência prazos moved elsewhere.
- **RN 209/2009** (Recursos Próprios Mínimos / Provisões Técnicas — *financial*, not "utilização") → **revoked by RN 451/2020** (capital regulatório).

### 2.11 RN 465/2021 Rol chain — current & correct
- **RN 465/2021** (Rol) **vigente**; **RN 473/2021** and **RN 643/2025** are genuine amendments (verified). The chain `465 + 473 + 643` is **valid but non-exhaustive** — ANS's own Anexo I lists further amendments (e.g., RN 624/2024, 625/2024, 599/2024, 632/2025, 642/2025). Not an error; note for completeness. **RN 623/2024 does NOT touch the Rol** (it is atendimento rules) and does **not** revoke RN 566/2022 — 465 (cobertura), 566 (prazos de realização) and 623 (atendimento/resposta) are three complementary, co-existing norms.

---

## 3. Proposed redlines (QUOTED — **not applied**; for SME to action after sign-off)

> Format: current repo text (❌) → proposed (✅). All proposals remain **DRAFT/verify** until SME sign-off. Contracts/DMNs are **forbidden edits for this task**; these are the answer sheet, not a patch.

### 3.1 `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md:6` (highest impact)
```
❌ RN 124/2006 (SIP …), RN 209/2009 (utilizacao de servicos …),
   RN 388/2015 (indicadores/transparencia …), RN 424/2017 (padrao TISS / monitoramento …), DIOPS …
✅ SIP → obrigação EXTINTA a partir do 1º tri/2026 (RN 639/2025 revoga RN 551/2022;
   dados assistenciais migram para Monitoramento TISS) — RE-ESCOPAR este processo. [verify SME]
   Penalidades (o que a RN 124/2006 realmente tratava) → RN 489/2022 (não é gatilho de envio).
   Garantias financeiras / capital (o que a RN 209/2009 tratava) → RN 451/2020; DIOPS = canal
   econômico-financeiro [verify SME: norma DIOPS vigente].
   Fiscalização / NIP (RN 388/2015) → RN 483/2022 (não é envio periódico — é canal NIP).
   Padrão TISS / Monitoramento (não RN 424) → RN 501/2022 + Monitoramento TISS.
   RN 424/2017 = junta médica — REMOVER deste contrato.
```
> **Note:** this is not a citation swap — the *set of report types* and their legal basis must be re-derived with regulatório; SIP's extinction changes the process's reason to exist.

### 3.2 `SP-OP-ANS-CRON-001.md:89` and `SP-OP-ANS-SUBMIT-001.md:183,206`
```
❌ RN 124/209/388/424 (…timeCycle reais por report_type…)
✅ Substituir por Monitoramento TISS / RN 501/2022 + RN 451/2020 (econ-fin) conforme §3.1;
   SIP encerrado 2026 (RN 639/2025). [verify SME]
```

### 3.3 `SP-OP-NIP-001.md:5,141,225,226,269` and `spec/processes/dmn/nip_sla.dmn`
```
❌ RN 388/2016 (NIP …) — prazos ~5 d.ú. assistencial / ~10 d.ú. não-assistencial
✅ RN 483/2022 (NIP; revogou RN 388/2015). Prazos confirmados: 5 dias ÚTEIS assistencial,
   10 dias ÚTEIS não-assistencial; upload da resposta em 10 dias úteis. Ajustar `fonte_regulatoria`
   nas rules de nip_sla.dmn de "RN 388/2016" → "RN 483/2022". Valores P5D/P10D permanecem
   como aproximação conservadora até resolver calendário útil (ver §SLA).
```

### 3.4 `SP-OP-AUTH-001.md:5,101,102,105` and `spec/processes/dmn/auth_sla.dmn:10-11,42,50`
```
❌ RN 259/2011 (garantia de atendimento …) ; RN 395/2016 (resposta em ate 5 dias uteis) ;
   RN 424/2017 (junta medica)
✅ RN 566/2022 (garantia de atendimento / prazos de REALIZAÇÃO — dias úteis; revogou RN 259/2011).
   RN 623/2024 (regras de ATENDIMENTO / resposta / negativa por escrito — em vigor 01/07/2025;
   revogou RN 395/2016). RN 424/2017 (junta médica) — MANTER (correto).
   auth_sla.dmn: em r2 trocar "RN 259 garantia 21 dias uteis…" → "RN 566/2022…";
   em r3 trocar "RN 395/2016 — resposta em ate 5 dias uteis" → "RN 623/2024 …". [verify SME: figuras exatas]
```

### 3.5 `SP-OP-ADEQUACAO-001.md:5,9,43,84,93,113,129,130,148,161,164,179,226,272,274,277` and `REEMBOLSO-001.md:5,197`
```
❌ RN 259/2011 (tempos e distancias maximas …) ; "RN 259/566" ; thresholds RN 259
✅ RN 566/2022 como fonte PRIMÁRIA (revogou RN 259/2011); RN 566 já citada — retirar/anotar
   RN 259 como "revogada por RN 566/2022". Tempos/distâncias e prazos em DIAS ÚTEIS.
   REEMBOLSO: prazo de reembolso "~30 dias" — [cannot-verify: norma específica; requires SME].
```

### 3.6 `SP-OP-CANCEL-001.md:5,19,236` and `SP-OP-INADIMPLENCIA-001.md:5,207,212,228`
```
❌ RN 593/2023 (… consolida/substitui RN 412/2016) ; RN 412/2016 (cancelamento a pedido)
❌ OQ: "RN 593 supersede/consolida RN 412/2016?"
✅ Cancelamento A PEDIDO do beneficiário → RN 561/2022 (revogou RN 412/2016; efeito imediato/irretratável).
   Suspensão/rescisão por INADIMPLÊNCIA + notificação → RN 593/2023 (MANTER; correto).
   Remover a afirmação "593 consolida 412" — são assuntos distintos.
   Fechar a OQ: RN 593 NÃO consolida a 412; sucessora da 412 é a RN 561/2022. [verify SME p/ ratificar]
```

### 3.7 `SP-OP-CRED-001.md:5,28,62,79,80,100,159,192` and `SP-OP-FRAUDE-001.md:5,207`
```
❌ RN 567 (… descredenciamento de prestador HOSPITALAR …) ; "RN 567/2018"
✅ RN 585/2023 → alterações na rede HOSPITALAR (substituição de entidade hospitalar,
   redimensionamento por redução, portabilidade 180 dias, comunicação 30 dias, CNES) — CITAR (uncited).
   RN 567/2022 → substituição de prestadores NÃO hospitalares (corrigir ano 2018→2022).
   RN 566/2022 (dimensionamento/rede) — MANTER. Mapear obrigações por tipo_prestador
   (hospital=585/2023 vs não-hospitalar=567/2022). [verify SME]
```

### 3.8 `SP-OP-CONTAS-001.md:5,146,184` and `SP-OP-RECURSO-001.md:6,54,153,179,196`
```
❌ RN 305/2012 (TISS) ; RN 305/2016 ; RN 424/2017 (recurso/analise de conta)
✅ TISS/glosa/recurso de conta → RN 501/2022 (revogou RN 305/2012; corrigir "305/2016").
   RN 424/2017 só se aplica à JUNTA MÉDICA (divergência técnico-assistencial), não a prazo de
   recurso de glosa — substituir por RN 501/2022 nas linhas de prazo de conta. [verify SME]
```

### 3.9 `spec/processes/dmn/carencia_check.dmn:18,22,25,53,96,107,174,185,202` and `docs/review-queue.md:87`
```
❌ RN 195/2009 ; RN 162/2007 ; RN 186/2009 ; RN 259/2011
✅ RN 195/2009 → RN 557/2022 (contratação). RN 162/2007 → RN 558/2022 (CPT/DLP/Carta Orientação).
   RN 186/2009 → RN 438/2018 (portabilidade de carências). RN 259/2011 → RN 566/2022.
   RN 465/2021 — MANTER. [verify SME: prazos de carência exatos permanecem "sintéticos/DRAFT"]
```

---

## 4. RNs applicable but **uncited** (should be added — with verified subject + relevance)

| RN | Verified subject | Status | Why it is relevant to Maezo | Source |
|---|---|---|---|---|
| **RN 483/2022** | Ações fiscalizatórias + **NIP** (revogou RN 388/2015) | vigente | The **current NIP norm**; `SP-OP-NIP-001` + `nip_sla.dmn` cite the revoked RN 388. | bvsms `res0483_31_03_2022.html` |
| **RN 561/2022** | **Cancelamento a pedido** / exclusão (revogou RN 412/2016) | vigente (01/02/2023) | Successor of the RN 412 that `SP-OP-CANCEL-001` cites for cancelamento a pedido. | bvsms `res0561_30_12_2022.html` |
| **RN 585/2023** | Alterações na **rede HOSPITALAR** (substituição de entidade hospitalar, redimensionamento por redução, portabilidade 180d, comunicação 30d, CNES) | vigente (in force 01/09/2024; regras de substituição 31/12/2024) | `SP-OP-CRED-001` **models hospital descredenciamento** but cites only RN 567 (non-hospital). RN 585/2023 is the governing hospital-network norm. | bvsms `res0585_24_08_2023.html`; gov.br `FAQ_RN_585_2023.pdf`; gov.br noticia "novas regras para alteração de rede hospitalar" |
| **RN 623/2024** | Regras de **atendimento ao beneficiário** / resposta / negativa por escrito (revogou RN 395/2016) | vigente (01/07/2025) | Governs the written-denial + response-deadline logic in `SP-OP-AUTH-001` / `auth_sla.dmn` that currently cite RN 395/2016. | gov.br diretrizes page **[fetched]**; bvsms `res0623_19_12_2024.html` |
| **RN 566/2022** | Garantia de atendimento — prazos máximos (revogou RN 259/2011) | vigente | Already cited in ADEQUACAO/CRED but **absent** from AUTH/REEMBOLSO/auth_sla, which still cite RN 259. | bvsms `res0566_02_01_2023.html`; gov.br prazos **[fetched]** |
| **RN 558/2022** | CPT / DLP / Carta de Orientação (revogou RN 162/2007) | vigente | `carencia_check.dmn` CPT logic cites revoked RN 162/2007. | bvsms `res0558_30_12_2022.html` |
| **RN 557/2022** | Contratação de planos (revogou RN 195/2009) | vigente | `carencia_check.dmn` cites revoked RN 195/2009. | bvsms `res0557_30_12_2022.html` |
| **RN 438/2018** | Portabilidade de carências (revogou RN 186/2009) | vigente | `review-queue.md` portabilidade item cites revoked RN 186/2009. | bvsms `res0438_05_12_2018.html` |
| **RN 451/2020** | Capital regulatório / provisões técnicas (revogou RN 209/2009) | vigente | Correct basis for the econ-fin submissions ANS-SUBMIT miscites as RN 209. | bvsms `res0451_12_03_2020.html` |
| **RN 489/2022** | Aplicação de penalidades (revogou RN 124/2006) | vigente | Correct identity of what RN 124/2006 was (penalidades), for the record. | bvsms `res0489_31_03_2022.html` |
| **RN 639/2025** | Extingue a obrigação de envio ao **SIP** (revoga RN 551/2022) a partir do 1º tri/2026 → Monitoramento TISS | vigente (02/03/2026) | **Re-scopes `SP-OP-ANS-SUBMIT-001`/`ANS-CRON-001`** — the SIP filing is discontinued. | gov.br `avisos-para-operadoras/ans-revoga-obrigatoriedade-de-envio-do-sip-a-partir-de-2026` |

> The plan flagged **RN 585 and RN 623 as "possibly applicable but absent"** — **both confirmed applicable and both should be cited** (585 → hospital descredenciamento in CRED; 623 → atendimento/resposta in AUTH). The plan's hypothesis that **RN 388 → RN 483/2022** is **confirmed**; **RN 259 → RN 566/2022** is **confirmed**; **RN 412 → RN 593** is **rejected** (412 → **RN 561/2022**; 593 is a distinct inadimplência norm).

---

## 5. Explicit "requires SME confirmation" register (blocked — external)

| # | Item | Why it needs SME | Owner |
|---|---|---|---|
| R1 | **SIP successor & re-scope of ANS-SUBMIT/ANS-CRON** — RN 639/2025 extinguishing SIP from Q1/2026; exact Monitoramento-TISS obligation replacing it | Single-source (gov.br aviso summary); high-impact re-scope, not just re-cite | regulatório |
| R2 | **DIOPS governing norm** (cited generically in ANS-SUBMIT) | Not an RN; current DIOPS norm not verified in this pass | regulatório |
| R3 | **Reembolso prazo (~30 dias)** in `SP-OP-REEMBOLSO-001` — which current norm sets it | Not resolved to a specific vigente RN | regulatório + jurídico |
| R4 | **RN 593 day-basis** — "50º dia" / "10 dias" as dias corridos vs úteis, and counting anchor | Repo encodes as ISO calendar durations; exact wording not fetched | jurídico |
| R5 | **RN 623/2024 exact per-class response figures** and day-basis for `auth_sla.dmn` | Confirmed norm & general figures; exact table needs the RN text | regulatório |
| R6 | **RN 557/2022 as full successor of RN 195/2009** and where carência prazos now live | Verified via search summary only | regulatório |
| R7 | **Ratify RN 412 → RN 561/2022 (not 593)** and closing of the INADIMPLENCIA-001 OQ | Legal ratification of the supersession chain | jurídico |
| R8 | **RN 585/2023 in-force date** (01/09/2024 vs 31/12/2024 for substitution rules) | Sources give two dates for different articles | regulatório |

**Cannot-verify (tools could not resolve authoritatively this pass):** R2 (DIOPS norm), R3 (reembolso norm). All other rows are *verified-but-need-sign-off*, not unverifiable.

---

## SLA day-basis question (for jurídico / regulatório)

**Ambiguity as encoded.** `spec/processes/dmn/auth_sla.dmn:12-14` (and `nip_sla.dmn`, and the NIP/AUTH contract SLA tables) state: *"prazos regulatorios sao em dias UTEIS; ISO 8601 usa dias corridos — valores abaixo sao aproximacao conservadora (menor ou igual ao prazo legal)."* Every SLA prazo is therefore stored as an **ISO 8601 duration (dias corridos / calendar days)** — `P2H`, `P3D`, `P5D`, `P7D`, `P10D` — while the underlying regulatory prazo is in **dias úteis (business days)**. The worker is expected to "resolve calendário útil" later.

**What the current norms actually specify (verified 2026-07-16):**
- **Atendimento / realização (RN 566/2022):** **dias úteis** — gov.br table header literally "em dias úteis" **[fetched]** (7 / 10 / 14 / 21 d.ú.; urgência imediato).
- **Resposta / atendimento ao beneficiário (RN 623/2024, successor of RN 395/2016):** **dias úteis** for the assistencial response classes (5 / 10 d.ú.); não-assistencial 7 dias — day-basis of the não-assistencial 7 **verify (SME)**.
- **NIP (RN 483/2022, successor of RN 388/2015):** **dias úteis** — 5 (assistencial) / 10 (não-assistencial); upload em 10 d.ú.
- **Inadimplência (RN 593/2023):** "até o 50º dia" and "10 dias após a notificação" — **day-basis not explicitly fetched**; these read as **dias corridos** in practice but must be confirmed (R4).

**Operational impact of the two readings.**
- Encoding a **dias-úteis** legal prazo as **dias-corridos** ISO (as today) makes the timer **fire earlier** than the legal deadline whenever the window spans weekends/holidays (e.g., a 5-business-day prazo = up to 7+ calendar days). For *attendance/NIP* this is **conservative for compliance** (the operadora is nudged to act before the legal breach) but produces **false SLA-breach escalations** and extra human load, and — critically — is **not the same instant the regulator will measure**, so any externally-reported metric or ANS-facing deadline computed from the ISO value will be **wrong (too early)**.
- For **inadimplência (RN 593)**, if the real basis is **dias corridos**, the current ISO encoding is **correct** for that family — meaning the repo is **mixing bases across processes**, which is the actual latent bug: one calendar convention cannot be right for all SLA classes.

**The exact question jurídico/regulatório must answer (per SLA class):**
> For each SLA class below, (a) does the **current** governing ANS norm count the prazo in **dias úteis** or **dias corridos**, and (b) from **which anchor event** is it counted?
> 1. **Autorização / realização** — RN 566/2022 (realização) + RN 623/2024 (resposta): confirmed **dias úteis**; from *data da demanda* (566) / *data da solicitação* (623)? — ratify.
> 2. **NIP** — RN 483/2022: **dias úteis** (5/10); from *data de recebimento da NIP* (as `nip_sla.dmn` anchors) — ratify.
> 3. **Inadimplência** — RN 593/2023: **dias corridos or úteis** for the 50º-dia notification and the 10-dia purge window, and counted from *não-pagamento* vs *notificação*?
> 4. **Reembolso** — the ~30-dia prazo (R3): which norm, and which basis?
>
> Given the confirmed **dias-úteis** basis for classes 1–2, must the engine **resolve a business-day calendar in the worker** (per `CONTRIBUTING`: prazo lives in DMN/timer, útil-calendar resolution in worker) **before any production timer**, replacing the current conservative ISO calendar-day approximation? And is the **urgência "2h" operationalization** (`auth_sla.dmn` r1: `PT2H` for a Lei 9.656 art. 35-C "imediato") an acceptable internal SLA, or must it be re-expressed?

---

## Cross-links & provenance

- **SME dispatch:** `docs/sme-dispatch/regulatorio/` and `docs/sme-dispatch/juridico/` (branch `t0.6-sme-dispatch`, PR #27) raise the RN-currency and SLA-day-basis questions. **This report is the analyst-side answer sheet** to accompany those packages: §1 answers "is each RN current?", §3 gives the proposed redlines, §5 lists what still needs the SME, and the SLA section frames the day-basis question verbatim.
- **Evidence ledger:** `docs/evidence-ledger.md` does **not** exist on `origin/main` at draft time → no row appended; **backfill noted** here for whoever creates the ledger (suggested row: `T2.5 (draft) | RN currency review | implemented — unverified`).
- **Forbidden-edit compliance:** no file under `docs/processes/contracts/**`, `spec/**`, or `docs/sme-dispatch/**` was modified. The only repo write for T2.5 is this file.

## Source URLs (all accessed 2026-07-16; **[fetched]** = direct WebFetch, else authoritative page via WebSearch)

- RN 259/2011: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2011/res0259_17_06_2011.html`
- RN 566/2022: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2023/res0566_02_01_2023.html` · **[fetched]** `https://www.gov.br/ans/pt-br/assuntos/consumidor/prazos-maximos-de-atendimento`
- RN 388/2015: `https://www.in.gov.br/materia/-/asset_publisher/Kujrw0TZC2Mb/content/id/33345888` · `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2015/res0388_25_11_2015.html`
- RN 483/2022: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0483_31_03_2022.html`
- RN 395/2016 → 623/2024: **[fetched]** `https://www.gov.br/ans/pt-br/assuntos/operadoras/atendimento-ao-beneficiario-diretrizes-da-rn-no-623-2024-para-operadoras` · `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2024/res0623_19_12_2024.html` · `https://www.gov.br/ans/pt-br/arquivos/assuntos/espaco-da-operadora-de-plano-de-saude/atendimento-ao-beneficiario/FAQ_RN_623__060625.pdf`
- RN 412/2016 → 561/2022: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0561_30_12_2022.html` · `https://www.ans.gov.br/component/legislacao/?view=legislacao&task=TextoLei&format=raw&id=MzMyNA%3D%3D`
- RN 305/2012 → 501/2022: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0501_01_04_2022.html`
- RN 424/2017: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2017/res0424_27_06_2017.html` · `https://www.gov.br/ans/pt-br/arquivos/canais-de-atendimento/canais-de-atendimento-ao-consumidor-1/faq_junta_medica_2021-v2.pdf`
- RN 593/2023: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2023/res0593_20_12_2023.html` · `https://www.gov.br/ans/pt-br/assuntos/consumidor/Perguntas_Frequentes_FAQ__RN_593.pdf`
- RN 567/2022: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0567_30_12_2022.html`
- RN 585/2023: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2023/res0585_24_08_2023.html` · `https://www.gov.br/ans/pt-br/assuntos/operadoras/registro-e-manutencao-de-produtos/registro-e-manutencao-de-operadoras-e-produtos/alteracao-de-produtos-1/FAQ_RN_585_2023.pdf`
- RN 465/2021 · 473/2021 · 643/2025: `https://www.ans.gov.br/component/legislacao/?view=legislacao&task=TextoLei&format=raw&id=NDAzMw%3D%3D` · `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2021/res0473_08_11_2021.html` · `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2025/res_0643_13_08_2025.html`
- RN 124/2006 → 489/2022: `https://www.ans.gov.br/component/legislacao/?view=legislacao&task=TextoLei&format=raw&id=Nzkw` · `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0489_31_03_2022.html`
- SIP → RN 639/2025: `https://www.gov.br/ans/pt-br/assuntos/operadoras/avisos-para-operadoras/ans-revoga-obrigatoriedade-de-envio-do-sip-a-partir-de-2026` · `https://www.gov.br/ans/pt-br/assuntos/operadoras/avisos-para-operadoras/ans-comunica-fim-da-obrigatoriedade-de-envio-de-dados-ao-sip-a-partir-de-2026`
- RN 209/2009 → 451/2020: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2020/res0451_12_03_2020.html`
- RN 195/2009 → 557/2022: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2009/res0195_14_07_2009.html` · `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0557_30_12_2022.html`
- RN 186/2009 → 438/2018: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2018/res0438_05_12_2018.html` · `https://www.in.gov.br/materia/-/asset_publisher/Kujrw0TZC2Mb/content/id/53493318`
- RN 162/2007 → 558/2022: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2007/res0162_17_10_2007.html` · `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0558_30_12_2022.html`

---
*End of DRAFT. No regulatory position herein is final; every verdict is an analyst draft pending SME (regulatório/jurídico) confirmation. Fail-closed: items marked cannot-verify are NOT to be treated as current.*
