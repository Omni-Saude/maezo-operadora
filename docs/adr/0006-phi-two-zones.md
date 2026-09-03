# ADR-0006: PHI em duas zonas; pseudonimizacao no gateway

**Status:** Accepted (2026-07-05) · **Data:** 2026-06-12 · **Area:** LGPD/Seguranca

## Contexto
LGPD exige minimizacao; PHI em APIs externas de LLM cria risco. Mas agentes de back-office
precisam do dado integral.

## Decisao
- **Zona Geral** (Helena, Lucas, Fernando, Gustavo): PHI pseudonimizado no Tool Gateway ANTES do
  contexto LLM; mapa de reidentificacao so no gateway; modelos cloud com DPA + regiao.
- **Zona PHI/Financeira** (Rafael, Marina, Beatriz, Valentina, Carolina, Andre): PHI integral somente
  em modelo on-prem ou endpoint contratado com residencia BR + zero-retention; **NetworkPolicy K8s
  impede egress fora da allowlist** (garantia de rede, nao de prompt).
- Logs/traces passam pelo mesmo pseudonimizador. Memoria semantica armazena derivados minimizados.

## Consequencias
**Positivas:** minimizacao sem cegar agentes; todo PHI que tocou LLM externo tem registro.
**Negativas (aceitas):** leve degradacao de UX na Zona Geral; custo de endpoint dedicado.

## Supersedes
—

---

## Emenda 2026-09-03 — nao existe "mapa de reidentificacao": a pseudonimizacao virou HMAC irreversivel (GAP AF-17)

**Status:** Proposto (amendment) — DRAFT/verify · **Data:** 2026-09-03 · **Autor:** `adr-reconciler` (R1, AGENTE)
**Marcadores:** `amended-by`: ADR-0035 e ADR-0036 + esta Emenda 2026-09-03 (WP-ADR-RECONCILIACAO,
GAP AF-17) · `obsolete-section`: a clausula "mapa de reidentificacao so no gateway" em `:11`.
**Base de verificacao:** worktree em `71dd4da`.

> APPEND-ONLY. Nenhuma linha do texto original acima foi alterada ou removida. Um AGENTE nao ratifica
> nada: enquanto este bloco carregar `DRAFT/verify`, ele e um fato reconciliado com o codigo, nao uma
> decisao ratificada. Assinatura humana pendente (`.github/CODEOWNERS:61`).

### 1. O que a ADR afirma

`:11` (Decisao, Zona Geral): "... mapa de reidentificacao so no gateway ...". A frase pressupoe um mapa
REVERSIVEL, guardado no gateway, capaz de levar do pseudonimo de volta ao dado original.

### 2. O que e verdade hoje

**Esse mapa nao existe — e a ausencia dele e a decisao, nao o defeito.**

- `src/maezo/platform/webhooks/whatsapp/dispatch.py:24-31`: "no persistent, reversible phone-number vault
  exists in v2 (ADR-0006 general-zone pseudonymization is one-way, `gateway/pseudonymizer.py`)"; o
  `conversation_id` e `wa:{tenant}:hk1_{phone_hash}` com `phone_hash` = HMAC-SHA256 **com chave**
  (ADR-0035), "irreversible without `PHI_HMAC_KEY`, NOT a reversible bare sha256", e o
  `beneficiario_pseudo_id` e uma SEGUNDA derivacao com chave sobre esse mesmo hash.
- `src/maezo/agents/helena/adapters.py:10-14`: o grafo da Helena "only ever has a hash, never a raw
  number — ADR-0006", e "no persistent, reversible hash->phone vault exists in v2 yet".
- A evolucao esta ratificada por ADR posterior, nao improvisada:
  `docs/adr/0035-phi-pseudonymizer-keyed-hmac.md:5` ("Amends ADR-0006 (PHI two-zones); does not supersede
  it") e `:38` (a irreversibilidade por chave); ADR-0036 estende a mesma decisao para
  `conversation_id`/thread-id/business-key, exigindo o marcador keyed `hk1_`.
- **A substancia implementada e MAIS FORTE que a desenhada:** irreversivel-sem-chave supera
  reversivel-com-mapa do ponto de vista de LGPD/minimizacao. O que ADR-0035/0036 nao fizeram foi voltar
  aqui e marcar a clausula do mapa como morta — e isso que esta emenda faz.

### 3. Consequencia

1. `:11` e `obsolete-section` **apenas quanto ao "mapa de reidentificacao"**. Todo o resto da Decisao
   (duas zonas, pseudonimizacao ANTES do contexto LLM, NetworkPolicy de egress na Zona PHI, logs/traces
   pelo mesmo pseudonimizador) permanece vigente.
2. **Nao ha reidentificacao no produto — nem no gateway.** A unica reversao existente e pontual e
   efemera: o resolvedor por-turno do proprio dispatch, que devolve o numero cru capturado na requisicao
   inbound corrente (`dispatch.py:31-32`, `_ScopedWhatsAppSender`). Isso nao e um mapa persistido.
3. **Consequencia LGPD explicita:** um pedido de titular (DSR) que dependa de reidentificar um pseudonimo
   a partir do repositorio **falha fechado por desenho**, e nao por lacuna de implementacao. Qualquer
   requisito futuro de reidentificacao exige ADR propria (armazenamento seguro do mapa, custodia da
   chave, autorizacao), nao a releitura de `:11`.
4. Nenhum comportamento de runtime muda com esta emenda: ela e documental.
