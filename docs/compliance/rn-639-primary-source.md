# RN 639/2025 (ANS) — Primary-Source Retrieval (T2.5 follow-up / DL-0028 contingency)

**Task:** T2.5 primary-source retrieval follow-up — V2-COMPLETION-PLAN, tier R1 (compliance-analyst).
**Purpose:** DL-0028 contingency resolution. T2.5's `rn-currency-review.md` reached only **secondary** sources
(gov.br/ans *avisos*, regulamentarsaude.com, m3bs.com.br, legismap) for the SIP-extinction finding and flagged it
`verify (SME)` / register row **R1**. DL-0028 requires **primary-source confirmation** of the norm text before the
finding may be treated as final. This document records the retrieval and, since the primary text **was** obtained,
quotes the operative articles verbatim and renders a per-claim verdict.
**Retrieval date:** 2026-07-17. **Repo HEAD at retrieval:** `6a3c64d1e738948da0c18301f71a3a11b6713b6d`.

---

## 0. Outcome (at a glance)

**RETRIEVED.** The full normative text of **Resolução Normativa ANS nº 639, de 04 de julho de 2025** was obtained
from the ANS's own official legislation database (raw-text endpoint), and independently re-fetched via a second tool
(`WebFetch`) returning byte-identical content.

| Claim held at verify-SME (from T2.5) | Verdict vs. **primary text** |
|---|---|
| **(a)** RN 639/2025 **revokes RN 551/2022** | **CONFIRMED** — ementa + Art. 1º |
| **(b)** SIP filing obligation **extinguished**; last mandatory filing **Q4/2025**; **in force ~02/03/2026** (∴ already extinct on 2026-07-17) | **CONFIRMED** — Art. 2º (após o envio do 4º trimestre de 2025) + Art. 3º (entra em vigor no dia 02 de março de 2026) |
| **(c)** the successor mechanism is **Monitoramento TISS** | **REFUTED as to the primary source** — the word "TISS" / "Monitoramento" and *any* successor mechanism are **absent** from RN 639's text. RN 639 only *revokes* RN 551/2022 and *removes* the SIP obligation; it names **no** substitute. The "Monitoramento TISS successor" statement rests solely on ANS **secondary** communications (avisos para operadoras), not on the norm. |

**DL-0028 evidentiary requirement (primary-source confirmation of the SIP-extinction finding): SATISFIED**
for the load-bearing facts (a) and (b). The ancillary characterization (c) is **not** established by the primary
norm and must be carried as a *secondary-source-only* assertion, not as text of RN 639.

---

## 1. Verbatim operative text

**Source (primary, authoritative — ANS official legislation database, raw text):**
`https://www.ans.gov.br/component/legislacao/?view=legislacao&task=textoLei&format=raw&id=NDY5Ng%3D%3D`
(the `id` is base64 of the ANS internal record number `4696`; `printf 4696 | base64` → `NDY5Ng==` → URL-encoded `NDY5Ng%3D%3D`.)

Retrieved 2026-07-17 via `curl` **and** independently re-fetched via `WebFetch` — both returned the identical text below.

> **RESOLUÇÃO NORMATIVA ANS Nº 639 DE 04 DE JULHO DE 2025**
>
> Revoga a Resolução Normativa ANS nº 551, de 11 de novembro de 2022, e desobriga as operadoras do envio do SIP.
>
> A DIRETORIA COLEGIADA DA AGÊNCIA NACIONAL DE SAÚDE SUPLEMENTAR - ANS, no uso das atribuições que lhe conferem o
> art. 4º, inciso XXXI, e o art. 10, incisos II e IV, ambos da Lei nº 9.961, de 28 de janeiro de 2000; e o art. 42,
> inciso IV, da Resolução Regimental nº 21, de 26 de janeiro de 2022, em reunião realizada em 04 de Julho de 2025,
> adota a seguinte Resolução Normativa e eu, Diretora-Presidente Interina, determino a sua publicação.
>
> **Art. 1º** Fica revogada a Resolução Normativa ANS nº 551, de 11 de novembro de 2022.
>
> **Art. 2º** As operadoras de planos de saúde ficam desobrigadas de enviar à ANS os arquivos com informações para o
> Sistema de Informações de Produtos - SIP após o envio do 4º trimestre de 2025.
>
> **Art. 3º** Esta Resolução entra em vigor no dia 02 de março de 2026.
>
> CARLA DE FIGUEIREDO SOARES
> DIRETORA-PRESIDENTE INTERINA
>
> *Este texto não substitui o texto normativo original e nem o de suas alterações, caso haja, publicados no Diário Oficial.*

RN 639/2025 is a **short, pure-revocation norm — three articles**. The block above is the complete operative text
(only the trailing "CORRELAÇÕES" cross-reference list — Lei nº 9.961/2000, RR nº 21/2022, and "A RN nº 639 revogou:
RN nº 551, de 2022" — is omitted; it is a database annotation, not enacted text).

---

## 2. Per-claim analysis against the verbatim text

### (a) "RN 639/2025 revokes RN 551/2022" — **CONFIRMED**
Ementa: *"Revoga a Resolução Normativa ANS nº 551, de 11 de novembro de 2022…"*; **Art. 1º**: *"Fica revogada a
Resolução Normativa ANS nº 551, de 11 de novembro de 2022."* Direct, unambiguous, single-target revocation.

### (b) "SIP obligation extinguished; last mandatory filing Q4/2025; in force ~02/03/2026; already extinct on 2026-07-17" — **CONFIRMED**
- **Extinction of the obligation + last mandatory filing:** **Art. 2º** — operators are *desobrigadas* from sending the
  SIP files *"após o envio do 4º trimestre de 2025"*, i.e. the **Q4/2025 filing is the last one owed**.
- **Vigência:** **Art. 3º** — *"Esta Resolução entra em vigor no dia 02 de março de 2026."* Matches the T2.5 "~02/03/2026"
  exactly (02 March 2026).
- **Status as of the environment date (2026-07-17):** RN 639 has been in force since 02/03/2026 and the Q4/2025 filing
  window has passed, so the **SIP filing obligation this repo automates (`SP-OP-ANS-SUBMIT-001` / `SP-OP-ANS-CRON-001`)
  is extinct today.** Confirmed.

### (c) "the successor mechanism is Monitoramento TISS" — **REFUTED as to the primary source**
The full text of RN 639 contains **no** occurrence of "Monitoramento", "TISS", "migra", or any successor/substitute
mechanism. The norm does exactly two things: revoke RN 551/2022 (Art. 1º) and lift the SIP-send obligation after
Q4/2025 (Art. 2º). It **does not** designate what, if anything, replaces SIP. The "successor = Monitoramento TISS"
statement in T2.5's `rn-currency-review.md` (§2.9, §4, register R1) therefore **cannot** be attributed to RN 639's
text; it derives only from ANS **secondary** communications (the `avisos-para-operadoras` pages). Treat (c) as a
secondary-source assertion pending its own primary basis (a distinct TISS/Monitoramento norm, not verified here).

---

## 3. Retrieval log — every attempt, in order (incl. failures)

| # | Route (charter method step) | Tool / URL | Outcome |
|---|---|---|---|
| 1 | Scoping searches | `WebSearch` ×6 (RN 639 / SIP / revoga 551 / date) | **Secondary only** — regulamentarsaude, m3bs, bvsms-search snippets, in.gov.br listing. Established: RN 639 **de 04/07/2025**, **pub. DOU 07/07/2025**, revokes RN 551/2022, desobriga SIP após Q4/2025, vigência 02/03/2026. Used **only** to bracket the date and pick source URLs — **not** quoted as primary. |
| 2 | §3 bvsms saudelegis (retry) | `WebFetch` `bvsms.saude.gov.br/bvs/saudelegis/ans/2025/` (dir) and `.../res_0639_07_07_2025.html` | **FAILED** — `read ECONNRESET` on every attempt (×4). `curl` to the same host → exit 56 *"Recv failure: Connection reset by peer"*. **Persistent server-side reset for this host in this environment**, not the transient hiccup the charter hypothesized. bvsms filename pattern independently confirmed via search (`res_0638_27_06_2025.html`, `res_0642_13_08_2025.html`) but the host will not serve this sandbox. |
| 3 | §1 DOU (in.gov.br) full text | `WebFetch` in.gov.br consulta/dou; `curl --http1.1` `in.gov.br/leiturajornal?data=07-07-2025&secao=do1`; `WebSearch allowed_domains=[in.gov.br]` | **FAILED** — WebFetch: `socket hang up` / HTTP2 `PROTOCOL_ERROR`; curl: `(52) Empty reply from server`; the in.gov.br domain search did **not** surface the RN 639 *materia* (only an unrelated 2016 homonym `RESOLUÇÃO Nº 639/2016` CONTRAN). DOU primary publication unreachable from this environment. |
| 4 | §2 ANS legacy portal — legislacao raw endpoint | `curl` then `WebFetch` `www.ans.gov.br/component/legislacao/?...&task=textoLei&format=raw&id=…` | **SUCCESS.** Endpoint returns 200 (same raw-text mechanism that worked for RN 561 in T2.5 verification). Found the `id` scheme is **chronological record-insertion order, unrelated to the RN number** (the base64 `id=NDY0Mw%3D%3D`=`4643` a search snippet labeled "RN 643" actually returns **RN 629**). Scanned the record range extracting each doc's real title; **`id=4696` (`NDY5Ng%3D%3D`) = RN 639/2025**. Full text pulled (§1). |
| 4b | Reproducibility / anti-self-certification | `WebFetch` the **same** URL `…format=raw&id=NDY5Ng%3D%3D` | **SUCCESS** — byte-identical text; `WebFetch` independently confirms Art. 1º/2º/3º and confirms **"Monitoramento TISS" appears nowhere**. The cited URL is re-fetchable by the R1 verifier via `WebFetch`. |
| 5 | §4 Web archives | `curl` `web.archive.org` reachability | Reachable (200). **Not needed** — route 4 yielded the authoritative text; per charter ("if one source yields the text… stop"), no snapshot was pulled. Noted as an available fallback if the ANS endpoint later regresses. |

**Hosts reachable from this sandbox (curl probe):** `example.com` 200, `www.gov.br/ans` 200, `www.ans.gov.br/component/legislacao …raw` **200**, `web.archive.org` 200. **Unreachable:** `bvsms.saude.gov.br` (conn reset), `in.gov.br` / `pesquisa.in.gov.br` (empty reply / HTTP2 protocol error).

---

## 4. Rule-3 discipline (no fabricated quotes/URLs)

- Every quoted string in §1 was **actually fetched** — first via `curl`, then re-verified via `WebFetch` against the
  same URL; the two agree verbatim. Nothing was reconstructed from the secondary-source summaries.
- The cited URL resolves live (HTTP 200) and is the ANS's own legislation database. Its own footer notes it does not
  substitute the DOU original; the DOU original (in.gov.br) was **unreachable from this environment** (route 3) — a
  limitation disclosed, not papered over. The ANS legislation-DB text is nonetheless a **primary/official** rendering
  (agency-published), sufficient for the DL-0028 evidentiary requirement, and its content is corroborated by (not
  copied from) the independent secondary sources.
- Claim (c) is **refuted rather than "probably true"**: the successor mechanism is simply **not in the norm**, and this
  document does not assert one.

---

## 5. Feed-back to T2.5 / DL-0028

1. **DL-0028 contingency:** the primary-source confirmation it required for the SIP-extinction finding is **now on
   file** (claims a + b confirmed from RN 639's text). The contingency's evidentiary gate is **met**.
2. **`rn-currency-review.md` register R1** ("SIP successor & re-scope … RN 639/2025 … exact Monitoramento-TISS
   obligation replacing it"): the **RN 639 half is confirmed primary**; the **"Monitoramento TISS successor" half
   remains secondary-source-only** and should be re-labelled accordingly (it is not in RN 639). Any downstream re-scope
   of `SP-OP-ANS-SUBMIT-001` / `SP-OP-ANS-CRON-001` may now rely on the confirmed extinction (in force 02/03/2026,
   last SIP = Q4/2025) but must source the *replacement obligation* from its own primary norm, not from RN 639.
3. **Correction to T2.5 wording:** T2.5 stated SIP ends "from the 1º trimestre de 2026" / "last mandatory filing
   Q4/2025". The primary text is precise: obligation lifted **após o envio do 4º trimestre de 2025** (Art. 2º) with
   **vigência 02/03/2026** (Art. 3º). "Last filing = Q4/2025" is exact; "extinct from Q1/2026" is consistent with the
   02/03/2026 in-force date.

---

*Retrieval performed 2026-07-17 against repo HEAD `6a3c64d`. Primary source: ANS legislation database, RN 639/2025
(`id=NDY5Ng%3D%3D`), fetched via curl and re-verified via WebFetch. No SME sign-off is claimed; this closes the
**primary-text** evidentiary gap only.*
