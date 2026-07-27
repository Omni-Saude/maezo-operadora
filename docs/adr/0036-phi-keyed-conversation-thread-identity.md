# ADR-0036: Identidade conversa/thread/business-key com HMAC keyed (fecha reversibilidade residual do ADR-0035) [t9-phi-conversation-id]

**Status:** Accepted (2026-07-26) · **Data:** 2026-07-26 · **Area:** Seguranca (PHI/LGPD)

> Estende o ADR-0035 (pseudonimizador keyed) e o ADR-0006 (PHI duas zonas); nao os supersede.
> ADR-0035 tornou keyed os CAMPOS PHI (`cpf`/`nome`/`telefone`/`email`) no site de construcao do
> `Pseudonymizer` — mas a IDENTIDADE que efetivamente egressa (conversation_id -> checkpoint
> thread_id -> business-key CIB Seven -> logs INFO) continuava derivada por um caminho separado e
> AINDA SEM CHAVE. Este ADR fecha essa reversibilidade residual.

## Contexto

`platform/webhooks/whatsapp/security.py::hash_phone` produzia `sha256(f"{tenant}:{phone}")` —
**SEM CHAVE**. Esse digest virava `conversation_id = "wa:{tenant}:{hash}"`, e o T4b passou a:

1. **persistir** o `conversation_id` como `thread_id` nas tabelas duraveis de checkpoint do
   langgraph (`checkpoints`/`checkpoint_blobs`/`checkpoint_writes` — Postgres), e
2. usa-lo como **business key** do motor CIB Seven (`ESC-{tenant}-{conversation_id}`,
   visivel no Cockpit), alem dos logs INFO (`helena_dispatch_turn_started/completed`).

`telefone` e PHI. O keyspace de celular BR (~6,7e9 apos DDD+9) e **forcavel por tabela
pre-computada**, e o prefixo `wa:{tenant}:` e texto plano — exatamente o ataque que o ADR-0035
existe para fechar. O HMAC keyed do ADR-0035 estava aplicado APENAS ao `beneficiario_pseudo_id`
(`hmac(key, sha256(tenant:phone))`); o identificador que realmente sai (identidade
conversa/thread/business-key) permanecia SEM CHAVE.

A trava de PHI do checkpoint (`runtime/checkpoint.py::assert_phi_safe_thread_id`) so rejeitava um
id **crua-numerico** (`^\+?\d{6,}$`). Um hash sha256 e um HMAC-sha256 sao **byte-a-byte
indistinguiveis** (ambos 64 chars hex), entao "nao e numero cru" AINDA aceitava o esquema
reversivel `wa:{tenant}:{sha256}` como thread_id.

## Decisao

1. **`hash_phone` keyed (raiz).** `hash_phone(phone, tenant, pseudonymizer)` passa a derivar a
   identidade pelo MESMO `Pseudonymizer` keyed do ADR-0035 (`hmac(PHI_HMAC_KEY, "tenant:phone")`),
   marcando o digest com o prefixo de esquema `hk1_`. O caminho sha256 sem chave foi **removido**
   — nao existe mais fallback. O `tenant:` dentro do input do HMAC preserva distincao por-tenant
   sob uma chave de cofre unica; o `hk1_` no output permite a trava exigir a forma keyed.

2. **Fail-closed HERDADO, nao reimplementado.** O `pseudonymizer` injetado ja e construido por
   `Pseudonymizer.from_settings` no composition root (`webhooks/service.py`), que **levanta**
   `PseudonymizerKeyMissingError` em `runtime_mode` de producao quando a chave e
   ausente/vazia/whitespace. Logo `hash_phone` NAO tem caminho para emitir identidade sem chave em
   producao; em dev/CI usa a chave DEV determinista nao-secreta (warning alto) — sempre HMAC,
   nunca sha256 puro.

3. **Trava do thread-id exige forma keyed.** `assert_phi_safe_thread_id` passa a EXIGIR o token
   keyed `hk1_<64-hex>` no id (via `.search`, aceitando tanto `wa:{tenant}:hk1_{hmac}` quanto
   `ESC-{tenant}-...` que o envolve). Como keyed e unkeyed sao indistinguiveis pela forma, exigir
   o marcador e o UNICO jeito de rejeitar o esquema legado reversivel `wa:{tenant}:{sha256}` de
   nunca mais entrar como thread_id (defesa em profundidade contra regressao — a garantia real de
   irreversibilidade vem da derivacao keyed do item 1).

4. **Fold-in LOW — `LogScrubber` fail-closed.** Removido o default de construtor
   `LogScrubber(pseudonymizer=None)` que caia numa chave DEV nao-secreta MESMO EM PRODUCAO
   (fail-OPEN latente). Agora o `pseudonymizer` e obrigatorio e ha `LogScrubber.from_settings`
   herdando a politica fail-closed do ADR-0035. Latente hoje (nao esta ligado ao unico
   `structlog.configure` em `platform/observability.py`), fechado preventivamente.

## Continuidade — cutover limpo (verificado)

NAO existe store de pseudonimo persistido (mapa reversivel hash->telefone) no v2
(`agents/helena/adapters.py`, `agents/lucas/adapters.py`: "no persistent, reversible hash->phone
vault exists in v2 yet"). As tabelas `agent_*` das migracoes 0001/0006 estao **retiradas/mortas**
(nenhum src escreve nelas). A unica persistencia viva da identidade e (a) as tabelas de checkpoint
upstream do langgraph (estado de trabalho efemero, chaveado por thread_id) e (b) as business keys
do motor CIB Seven — ambas guardam o pseudonimo como chave OPACA, nunca como mapeamento reversivel.
A troca unkeyed->keyed muda todos os thread_ids/business-keys, mas nada precisa de reconciliacao de
cofre. Uma conversa WhatsApp em voo no meio da migracao recebe um thread_id NOVO (começa um turno
fresco) — aceitavel: pre-prod greenfield, e a `PHI_HMAC_KEY` de producao ainda NAO esta
provisionada (§6.2 BLOCKED), entao um webhook-receiver em `RUNTIME_MODE=kubernetes` hoje ja se
RECUSA a despachar (agora tanto pelo pseudonimizador quanto por `hash_phone`), logo nao ha dado
unkeyed real de producao a orfanizar.

## Consequencias

**Positivas:** a identidade que egressa (conversa/thread/business-key/logs) passa a ser
irreversivel sem o segredo do cofre (LGPD-grade), fechando o vetor reversivel que o T4b passou a
PERSISTIR; a trava do checkpoint deixa de aceitar o hash unkeyed legado; o fail-open latente do
`LogScrubber` e fechado.

**Negativas (aceitas):** provisionar a `PHI_HMAC_KEY` real no cofre continua sendo passo manual
§6.2 (BLOCKED) — ate la, prod se recusa a despachar (por design). Todos os thread_ids/business-keys
mudam de forma (`hk1_`); qualquer estado de checkpoint pre-existente com o esquema antigo fica
orfão (aceitavel: efemero, sem PHI real). Bump do sufixo do marcador (`hk1_`) sera necessario em
qualquer rotacao de esquema futura.

## Supersedes

— (estende ADR-0035 e ADR-0006).
