# ADR-0035: PHI pseudonymizer — keyed HMAC-SHA256, prod fail-closed, dev deterministic [t6-hmac]

**Status:** Accepted (2026-07-26) · **Data:** 2026-07-26 · **Area:** Seguranca (PHI/LGPD)

> Amends ADR-0006 (PHI two-zones); does not supersede it. Authored by an R1 builder;
> R1-verified (author ≠ verifier) pelo gatekeeper em 2026-07-26 — verdict PASS (reversibilidade
> fechada, matriz fail-closed com mutation-RED, side-effect do RUNTIME_MODE no Helm liberado) —
> movendo o status para Accepted. Fold-back LOW aplicado na mesma verificacao: chave
> whitespace-only e tratada como AUSENTE (normalizacao unica antes do branch — prod falha
> fechado tambem para `"   "`/`"\t\n"`; um sufixo de newline numa chave real e canonicalizado).

## Contexto

`gateway/pseudonymizer.py::Pseudonymizer.pseudonymize` tokenizava campos PHI (`cpf`, `nome`,
`telefone`, `email`) com **SHA-256 SEM CHAVE** (`hashlib.sha256(str(value))`). Para um CPF —
~10^9 valores validos apos os digitos verificadores — um digest sem chave e **trivialmente
reversivel** por tabela pre-computada (rainbow/brute-force). Um pseudonimo assim NAO satisfaz a
pseudonimizacao da LGPD: o mapeamento tem de ser inviavel de reverter sem um segredo.

O segredo ja existia no ambiente: `PHI_HMAC_KEY` estava declarado em
`runtime/worker_runtime/settings.py` e `runtime/agent_runtime/settings.py`, sincronizado do cofre
pela ExternalSecret `phi-hmac-key` — mas **nao era consumido em lugar nenhum** (grep-proven). A
variante keyed (`_surrogate()` com `hmac.new(key, msg, sha256)` + `InMemorySurrogateStore`)
existiu apenas no commit greenfield e foi perdida na simplificacao do arquivo atual — uma
regressao. A intencao documentada (`.env.example`, `docs/Tarefas_Pendentes.md`) e explicita:
`Pseudonymizer.from_vault` keyed, **fail-closed** ("Sem a chave HMAC ... levanta erro — nunca usa
default"), e "vazia em dev/CI (o runtime deriva uma chave determinista por tenant — NAO secreta)".

Havia ainda um descasamento de deploy: `PHI_HMAC_KEY` era injetada nos Deployments agent-runtime e
worker-daemon — mas **nenhum dos dois constroi um Pseudonymizer** (agent-runtime e scaffold
health-only; o worker usa a redacao one-way `tools/workers/phi_vars.redact_phi_vars`, sem
Pseudonymizer). O unico daemon que realmente pseudonimiza e o **webhook-receiver** (dispatch da
Helena, `webhooks/service.py` -> `whatsapp/dispatch.py`), que nao recebia nem a chave nem o modo.

## Decisao

1. **Keyed HMAC-SHA256.** `Pseudonymizer` passa a produzir `hmac.new(key, str(value).encode(),
   sha256).hexdigest()`. Determinismo por-chave preserva a correlacao; a irreversibilidade agora
   depende do segredo (impossivel reverter sem `PHI_HMAC_KEY`).

2. **Fabrica com politica fail-closed** — `Pseudonymizer.from_settings(phi_hmac_key, production,
   tenant_id)`, espelhando os precedentes mais fortes do repo (a trava `DATABASE_URL` do
   webhook-receiver; `RefusingAnsGatewayTransport`; `inference.py` "no silent fallback"):
   - chave presente -> HMAC com a chave real do cofre;
   - **producao + chave ausente/vazia/whitespace-only -> `PseudonymizerKeyMissingError`
     (fail-closed)**: a chave e normalizada (strip) UMA vez antes do branch — whitespace nunca e
     material de chave; um pod de
     producao NUNCA pode cair num pseudonimo determinista/reversivel;
   - dev/CI + chave ausente -> **chave DEV determinista por-tenant, NAO secreta** + WARNING alto
     (convencao ".env vazia em dev = determinista"). Mesmo o fallback dev e HMAC (nunca SHA-256
     puro), entao a reversibilidade fica fechada em todos os modos — em dev, apenas "reversivel por
     quem conhece a chave dev publica", aceitavel para dados locais nao-PHI.

3. **Discriminador de modo.** Reusa o `runtime_mode` que o webhook-receiver ja carrega (T4b):
   `!= "local"` e producao (Helm injeta `RUNTIME_MODE: "kubernetes"`). Mesmo criterio que
   `agent_runtime_mode`.

4. **Wiring.** O unico site de construcao em caminho de producao (`webhooks/service.py`) passa a
   usar `from_settings`; `WhatsAppWebhookSettings` ganha `phi_hmac_key`; o Deployment
   webhook-receiver passa a injetar `PHI_HMAC_KEY` (mesma ExternalSecret `maezo-phi-hmac`) e
   `RUNTIME_MODE` (que tambem arma corretamente a trava do checkpointer T4b, antes sem modo no
   deploy). `LogScrubber` aceita um Pseudonymizer keyed injetado (default dev-safe).

5. **Continuidade — cutover limpo.** NAO existe store de pseudonimos persistido no v2 ("no
   persistent, reversible phone-number vault exists in v2 yet" — `dispatch.py`, `helena/lucas
   adapters`). Os tokens sao correlacao one-way efemera; pre-prod greenfield, sem PHI real. A troca
   unkeyed->keyed muda todos os tokens, mas nada os persiste atraves do cutover — troca limpa,
   sem quebra de continuidade a reconciliar.

## Consequencias

**Positivas:** pseudonimos PHI passam a ser irreversiveis sem o segredo do cofre (LGPD-grade); o
gap de deploy (chave no daemon errado) e corrigido; o fail-closed impede que um pod de producao
sem chave sirva silenciosamente com a fraqueza reversivel; a trava de modo do checkpointer T4b
tambem passa a ser efetiva no deploy.

**Negativas (aceitas):** provisionar a `PHI_HMAC_KEY` real no cofre continua sendo passo manual
§6.2 (BLOCKED) — ate la, um webhook-receiver em `RUNTIME_MODE=kubernetes` sem a chave se recusa a
despachar (`/webhook` 501), por design. Todos os tokens de pseudonimo mudam (aceitavel: sem store
persistido, sem PHI real).

## Supersedes

— (amends ADR-0006).
