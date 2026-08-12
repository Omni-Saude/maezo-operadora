# ADR-0039: A2A Delegation-Envelope Signing — Canonical Digest, Key Rotation, Fail-Closed Verification

**Status:** **Accepted** (2026-08-12) — ratificado pelo DONO do repositorio, que respondeu as sete
Perguntas abertas na mesma data (ver `## Decisoes do dono (2026-08-12)` logo abaixo). A tabela de
aprovadores POR PAPEL segue PENDENTE e continua vedada a agentes: a ratificacao do dono registra a
DECISAO, e nao substitui a revisao nomeada de Security/crypto (R1). · **Data:** 2026-08-11 (redigido)
/ 2026-08-12 (aceito) · **Area:** Seguranca / A2A / Auditoria

| Role | Approver | Date |
|---|---|---|
| Security/crypto reviewer (R1) | _(pending)_ | _(pending)_ |
| Orchestrator (architecture ratification) | _(pending)_ | _(pending)_ |

> ℹ️ **NOTA (leg E1, esta revisao — r5): o corpo de design abaixo ESTA emendado.** A partir desta
> revisao, §4.1 inclui `payload_meta_hash` e `task_type` no digest v2; §4.2/§4.3/§4.4 trazem a
> cadencia de rotacao ordinaria de 7 dias (com a idade maxima de assinatura e o piso de retencao de
> `a2a_idempotency` derivados dela), a janela de graca do epoch anterior, e a custodia de chave
> por-tenant. Esta ADR deixa de estar "aceita-mas-nao-emendada" — ver `## Historico de revisoes`,
> linha r5, para o resumo da emenda.

## Decisoes do dono (2026-08-12)

Respostas as sete `## Perguntas abertas (humano decide)` (cabecalho em `:866`, secao ate `:919`, logo
antes de `## Relacao com ADRs existentes` em `:920`), uma a uma, na ordem em que a ADR as fez. Transcritas do
dono; nenhuma delas e inferencia de agente.

| # | Pergunta | Decisao |
| --- | --- | --- |
| 1 | `payload_meta` no digest | **ADOTAR `payload_meta_hash`**: um campo, `"payload_meta_hash": sha256(canonical(envelope.payload_meta))`, com a canonicalizacao exata do §4.1. Fecha o MAJOR M1 (TUSS, CID-10, `valor_estimado_brl`, `prestador_id`, `valor_pagamento_cents` seguiam nao-assinados). |
| 2 | `task_type` no digest | **INCLUIR** — fecha o segundo residual divulgado do §4.1. |
| 3 | Cadencia de rotacao / idade maxima de assinatura | **Rotacao ordinaria de chave roda por 7 DIAS.** Donde a idade maxima de assinatura no verificador (§4.3.2) e, por §4.3.3, o **piso de retencao** de `a2a_idempotency` vira numero computavel — o DBA/MZO-060 escolhe o valor contra esse piso, nao contra uma dependencia aberta. |
| 4 | Custodia de chave | **Mesmo seam vault/KMS de `MAEZO_A2A_CARD_SIGNING_KEY`** (`a2a/assembly.py:45`, lido em `:67`) guarda as chaves no dia a dia, e o **keyset e POR TENANT**, nao repo-wide — assinatura de envelope tambem por tenant. Blast radius permanece o dos **quatro builders por-requisicao** enumerados na propria Pergunta 4 (`:895-909`). |
| 5 | Graca no bump de epoch | **SIM** — `replay_epoch` tolera uma janela curta de graca para o epoch anterior, para evitar rejeicao em massa de envelopes em voo. A semantica de graca-zero para incidente segue tendo de ser enunciavel. |
| 6 | `MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES` | **CONFIRMADO** (nome e forma). Dev-local apenas, fail-closed fora de local pelo `is_production_runtime_mode` ja compartilhado. |
| 7 | Default ausente de `AGENT_RUNTIME_MODE` | **MUDAR o default de `AgentRuntimeSettings`** (`runtime/agent_runtime/settings.py:38`, hoje `Field(default="local", alias="AGENT_RUNTIME_MODE")`) para fail-closed. E a licao "default de chave ausente falha FECHADO" aplicada ao residual que enfraquecia a propria alegacao fail-closed desta ADR na borda Helena→Rafael. |

**Consequencia imediata:** a implementacao da assinatura de envelope — deliberadamente nao construida
na Onda 3 porque construir contra um digest nao-ratificado e construir sobre areia — esta
DESBLOQUEADA. Criterio de aceite ja escrito e auto-verificavel: por §4.8 (:772), os dois testes de
negativo honesto que hoje afirmam que mutar `payload_meta`/`task_type` deixa o digest **byte-identico**
devem VIRAR assercoes positivas (mutacao ⇒ digest muda ⇒ envelope rejeitado). Se passarem sem virar,
a mudanca de digest nao aconteceu, qualquer que seja o resto do verde.

Owner: envelope-adr-author (R2), leg 1/4 of Train C (Onda 3 — A2A distribution-grade),
`wave3-a2a-distribution`, created at `origin/main` = `29763e7`. Scope: docs-only — this ADR designs
envelope signing; it does **not** implement it and does **not** touch `src/`, `spec/`, `tests/`,
`deploy/`, or the effect-chokepoint fence allowlist. Legs 2-4 of this train build against the
constraints fixed here.

## Contexto

### 1. What exists today — two DIFFERENT signing surfaces, only one built

A2A already has cryptographic signing, but it covers the **Agent Card** (per-agent identity),
never the **delegation envelope** (the per-message instruction). Keeping the two distinct is
load-bearing for the rest of this ADR:

- **Agent Card signing (built, T-G, `t2.4-a2a-w1-card-signing` → merged)** —
  `AgentCard.signing_payload()` (`src/maezo/a2a/card.py:101-124`) canonicalizes the Card's
  identity/capability fields (`json.dumps(payload, sort_keys=True, separators=(",", ":"))`,
  UTF-8-encoded) and `CardSigner` (`src/maezo/a2a/signing.py:35-114`) HMAC-SHA256s that byte string
  under a single injected key, tagging the result `f"{scheme}:{hexdigest}"` with
  `SIGNATURE_SCHEME = "v1"` embedded in the signed payload itself (`card.py:50`). `A2ARegistry`
  (`src/maezo/a2a/registry.py:37`, class definition) takes an **optional** `verifier: CardSigner |
  None = None` (`:54`) — when absent, Cards are admitted regardless of signature state. The MODULE
  docstring states the posture ("`verifier=None` (default) preserves / Phase-0/dev behavior
  (unsigned Cards are accepted): verification is gated on the PRESENCE of the / key",
  `registry.py:14-16`); the phrase "Phase-0/dev fail-safe" itself is `__init__`'s docstring, not the
  class docstring (`registry.py:60-61`, "Cards are admitted regardless of signature state
  (Phase-0/dev / fail-safe)"). `register()` (`registry.py:66-76`) only calls
  `self._verifier.require_valid(card)` (`:74`) when a verifier was injected. Production composition (`src/maezo/runtime/agent_runtime/a2a_composition.py`) closes
  this gap with a fail-closed gate, `_require_signer_or_fail_closed` (`:136-189`) — see §3 below.
- **Delegation-envelope signing (does NOT exist — this ADR's subject).**
  `DelegationEnvelope` (`src/maezo/a2a/delegation.py:98-99`, `@dataclass(frozen=True, slots=True)` /
  `class DelegationEnvelope:`) carries `task_id, task_type, origin, target, tenant,
  delegation_chain, budget, payload_ref, max_hops, deadline, payload_meta` (`:113-123`) — **no
  signature field of any kind**. Nothing in `root()` (`:150-187`) or `extend()` (`:189-226`) signs
  anything; the four ADR-0003 anti-loop guards (acyclic, `max_hops=3`, budget, `task_id`
  idempotency) are structural (raise-on-construction), not cryptographic. A forged or
  in-transit-tampered envelope that still satisfies those four structural guards passes unnoticed —
  there is no mechanism today that would catch it.

The two surfaces answer different questions: the Card answers "is this really Rafael's registered
capability set, signed by whoever holds the trust key?"; the envelope this ADR designs answers "is
this *specific* delegation — this `task_id`, this `budget`, this `deadline`, this `payload_ref` —
exactly what the signer authorized, unaltered?" Card signing does not imply envelope signing, and
neither today implies the other.

### 2. The gaps this ADR closes are already named in the code, not invented here

- **In-memory idempotency does not survive a restart.** `DelegationDispatcher`
  (`src/maezo/a2a/dispatcher.py:247`, class definition) documents its own limit
  (`:252-257`): `idempotency=None` (the default) backs Guard 4 with an in-memory
  `_inflight` dict that "does NOT survive replica restarts (re-delegation)". The durable
  alternative, `PostgresIdempotencyStore` (`src/maezo/a2a/idempotency.py:218-311`), exists and is
  fully implemented, but is wired **only when `database_url` is truthy** at both composition roots:
  `src/maezo/runtime/agent_runtime/a2a_composition.py:266` (`PostgresIdempotencyStore(dsn=...) if
  settings.database_url else None`) and `:369` (`... if database_url else None`). Neither site
  checks `runtime_mode` — unlike the Card-signing key gate (§3 below), **there is no fail-closed
  check today that refuses to compose a dispatcher with in-memory-only idempotency in a production
  runtime.** An operator who forgets `DATABASE_URL` in production gets silent, non-durable Guard 4
  with no raise and no warning log. This ADR's mandate in §Decisão 4.6 is exactly closing that gap.
- **Facts are observability, not audit — `_NoopKafkaProducer` is a named, intentional stand-in.**
  `class _NoopKafkaProducer` (`a2a_composition.py:192-201`) is constructed at both composition roots
  (`:264`, `:368`, `kafka_producer or _NoopKafkaProducer()`) whenever no real producer is injected.
  Its own docstring is explicit: "Facts are observability, never the T-F audit surface (that rides
  the real `PostgresAuditSink`...), so a dropped fact never compromises the delegation-audit proof."
  This is a **declared, by-name exception** in the effect-chokepoint fence's §8.4 allowlist
  (`scripts/ci/check_effect_chokepoint_fence.py:401-412`, entry at `:406`:
  `("runtime/agent_runtime/a2a_composition.py", "_NoopKafkaProducer")`) — the fence's
  `_is_test_double_name` (`:415-429`) would otherwise flag any `Noop*`/`Fake*`/`*Mock*` name
  **defined in, or imported into,** a composition-root module. That — a `class` statement or an
  `import`, never a *construction* — is precisely what trips the rule: the gate collects
  `ast.ClassDef` names into `test_double_defs` (`:611-615`) and `ast.ImportFrom`/`ast.Import` names
  into `test_double_imports` (`:618-633`, `:634-638`), and `_is_test_double_name` is reached from
  exactly those three AST branches (`:614`, `:632`, `:637`) — no `ast.Call` path feeds it. This one
  is allowlisted **by name with rationale**, never by loosening the pattern (the allowlist is
  applied to imports at `:822` and to definitions at `:835`). §Decisão 4.6 below treats replacing `_NoopKafkaProducer` with a
  transactional outbox as a consequence for legs 2-3, and is explicit about what that means for this
  allowlist entry.
- **`RUNTIME_MODE`/`AGENT_RUNTIME_MODE` fail-closed discriminator — already used, already
  asymmetric.** `a2a_composition.py:104-128` is the block this ADR's verification-gate design
  reuses: `_LOCAL_RUNTIME_MODE = "local"` (`:104`), `ALLOW_UNSIGNED_CARDS_ENV_VAR` (`:111`),
  `WORKER_RUNTIME_MODE_ENV_VAR = "RUNTIME_MODE"` (`:123`), and
  `worker_runtime_mode_from_env()` (`:126-128`): `os.environ.get(WORKER_RUNTIME_MODE_ENV_VAR,
  "").strip() or "production"` — **absent → `"production"` (fail-closed)**. The
  agent-runtime edge instead reads `AgentRuntimeSettings.agent_runtime_mode`
  (`src/maezo/runtime/agent_runtime/settings.py:38`,
  `Field(default="local", alias="AGENT_RUNTIME_MODE")`) — **absent → `"local"` (fail-OPEN
  default)**. Both converge on `"production"` when the variable is *present but explicitly set to
  the empty string* (pydantic assigns the literal `""`, then `"" != "local"` → production; the
  worker path's own `.strip() or "production"` gives the same result for `""`) — that convergence
  is the "both-empty-string residual" this ADR does **not** touch. The asymmetry that *does* matter
  is the **absent-variable** case: the same two-runtime-mode idiom defaults permissively on one
  composition root and restrictively on the other. This is a **pre-existing, owner-level
  inconsistency**, not introduced by this ADR and not this ADR's to fix — §Decisão 4.4 states
  precisely how the envelope-verification gate inherits it (does not resolve it).
- **The gated seam pins the dispatcher's public surface to exactly `{delegate}`.**
  `GatedDelegationDispatcher` (`src/maezo/gateway/seams/a2a.py:54-62`) subclasses
  `DelegationDispatcher` rather than structurally decorating it (module docstring `:9-30` explains
  why: the class is spelled as a concrete annotation in eight call sites, so a `Protocol`
  introduction would be real churn for a wave whose whole claim is invisibility). The docstring
  states the safety argument explicitly (`:23-26`): "`delegate` is the ONLY public method of
  `DelegationDispatcher`... `test_delegation_dispatcher_public_surface_is_only_delegate` asserts
  exactly that, so the day someone adds a second public method the gate fails loudly instead of
  silently inheriting an ungated path." That test exists and is where it is claimed:
  `tests/unit/gateway/seams/test_seam_proofs.py:809`. §Decisão 4.4 below is explicit that envelope
  verification must be **internal** (a `_`-prefixed helper called from inside `delegate` itself,
  never a new public method) for exactly this reason.
- **The seam gate runs BEFORE the dispatcher, and is not the "first check" this ADR means.**
  `GatedDelegationDispatcher.delegate` calls `await gate(self._seam, _OP_DELEGATE)`
  (`gateway/seams/a2a.py:60`) *before* `await self._inner.delegate(envelope)` (`:61`). `gate()`
  itself emits a shadow line (`_emit`, `gateway/seams/_base.py:378` — "Emit the ONE shadow line for
  this call. Never raises onto the effect path", `_base.py:264`) and then calls
  `_pre_effect_audit` (`_base.py:381`), the `audita_antes` durable-write seam. That pre-effect audit
  is **inert today, and only because** every class declares `audita_antes: bool = False`
  (`gateway/effect_classes.py:136`) and `delegacao_a2a` does not override it
  (`effect_classes.py:199-203`) — `_pre_effect_audit` returns at its first branch
  (`_base.py:316-318`, `if spec is None or not spec.audita_antes: return`). §Decisão 4.4's "first
  check" is therefore scoped to **inside the dispatcher**, never a claim that nothing at all
  precedes it: if `delegacao_a2a` ever flips `audita_antes=True`, a durable record is written by the
  seam before envelope verification has run at all.

### 3. Composition-root precedent this ADR's verification gate mirrors

`_require_signer_or_fail_closed` (`a2a_composition.py:136-189`) is the existing fail-closed gate for
Card signing, and its three-way structure is the template §Decisão 4.4 reuses for envelopes: key
present → sign/verify; key absent + production runtime → `RuntimeError` at composition (no
dispatcher is ever returned); key absent + non-production + **explicit**
`MAEZO_A2A_ALLOW_UNSIGNED_CARDS=1` opt-out → proceed unsigned, loudly warned (`logger.warning`,
`:178-188`); key absent + non-production + no opt-out → also raise. The function is already
generalized across both composition roots (`build_auth_delegation_dispatcher`,
`build_dossier_delegation_dispatcher`) via an injected `runtime_mode` parameter (`:156-160`
docstring), so the envelope-verification gate this ADR proposes (§4.4) can be built as a structural
sibling at the same two call sites without a new composition-root shape.

### 4. Non-interaction with the AMH boundary (ADR-0037)

ADR-0037 fixes the Maezo↔AMH contract boundary: Kafka work-item/consent/outcome topics, the
`config/integrations/amh/contracts.lock.json` pin, and the `ActionExecutionGateway` chokepoint for
external effects crossing into the AMH data plane. A2A delegation is **intra-Maezo**
agent-to-agent communication inside the payer's own runtime (Helena→Rafael, worker→Carolina/Andre)
— it never crosses into the AMH data plane, never touches a `cdc.*`/`amh.*` topic, and is not part
of ADR-0037's canonical envelope baseline (which is a *different* envelope — the AMH CDC
work-item/consent/outcome wire format, ADR-0037 "Baseline congelado" §, itself coincidentally
naming a `payload_hash` field for that unrelated wire). **One line, as instructed: no interaction —
envelope signing is entirely internal to the Maezo A2A runtime and does not touch, extend, or
constrain any AMH-boundary artifact.**

## Decisao

This ADR designs delegation-envelope signing. It ratifies the **design**; legs 2-4 of this train
build it. No code changes accompany this ADR.

### 4.1 Canonical digest — field set and canonicalization

**Digest v2 (esta revisao, r5) — emendado por §Decisoes do dono 1-2 (2026-08-12).** O payload
assinado cobre exatamente o conjunto de campos mandatado
`{tenant, task_id, task_type, origin, target, payload_hash, payload_meta_hash, deadline, budget,
delegation_chain}`, mapeado para um objeto JSON canonico:

```
{
  "tenant": envelope.tenant,
  "task_id": envelope.task_id,
  "task_type": envelope.task_type,
  "origin": envelope.origin,
  "target": envelope.target,
  "payload_hash": sha256(envelope.payload_ref.encode("utf-8")).hexdigest(),
  "payload_meta_hash": sha256(canonical(envelope.payload_meta)).hexdigest(),
  "deadline": envelope.deadline.astimezone(UTC).isoformat() if envelope.deadline else None,
  "budget": {"tokens": budget.tokens, "time_ms": budget.time_ms, "cost_per_hop": budget.cost_per_hop},
  "delegation_chain": list(envelope.delegation_chain),
  "scheme": <signature scheme tag, see §4.2>,
  "key_id": <signing key identifier, see §4.2>,
  "replay_epoch": <trust epoch, see §4.2>,
}
```

`canonical(...)` above is not a new recipe — it is exactly the canonicalization this ADR already
fixes for the outer digest, restated in full immediately below: `json.dumps(value, sort_keys=True,
separators=(",", ":"))` (no `default=`), UTF-8-encoded. `envelope.payload_meta` is
`Mapping[str, str]` (`delegation.py:123`), so it is JSON-native by construction and needs no
coercion before hashing.

**v1 -> v2 delta, stated exactly.** v1 (r1-r3, Proposed status) mandated only
`{tenant, task_id, origin, target, payload_hash, deadline, budget, delegation_chain}` and carried
`payload_meta` and `task_type` as *disclosed residuals* — real, evidenced gaps the "Disclosed
residuals" discussion below argues at length, but left for a human to close. **v2 closes both, per
the owner's 2026-08-12 decision** (§Decisoes do dono, table rows 1-2): `payload_meta_hash` is added
(binding the instruction content — TUSS/CID-10/`valor_estimado_brl` on Helena's edge, the
`prestador_id` CIB business key on Carolina's edge, `valor_pagamento_cents` on Andre's edge —
without persisting or disclosing a byte of it), and `task_type` is added directly (binding the
Card-capability/routing-significant field argued for below). `max_hops` is **not** added — the
owner's seven decisions did not touch it, and the reasoning below for why it stays a disclosure
rather than a signed field is unchanged. The "Disclosed residuals" discussion that follows is
retained as the evidentiary record the decision was made against; only its accept/reject framing for
`payload_meta`/`task_type` is superseded by this delta.

**Canonicalization: reuse the repo's existing convention — precisely, not by rounding two related
recipes together.** The repo already has TWO close-but-not-identical "canonical JSON" recipes, and
this ADR is explicit about which one it adopts. `AgentCard.signing_payload()`
(`card.py:124`, `json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")`) and the
standalone `hash_input()` helper (`audit.py:51-61`, `json.dumps(payload, sort_keys=True,
separators=(",", ":"), default=str)`, "SHA-256 of a canonical (sorted-key) JSON encoding") both use
**compact separators** (`,`/`:`, no whitespace). `AuditRecord._compute_hash()`'s own preimage
(`audit.py:260-284`) uses `json.dumps({...}, sort_keys=True, default=str)` — sorted keys, but
**Python's default (spaced) separators**, since it passes no `separators=` argument at all. These
two recipes produce different bytes for the same content and are NOT interchangeable; this ADR
adopts the **compact-separator variant** (`sort_keys=True, separators=(",", ":")`), matching
`card.signing_payload()` and `hash_input()` — the more directly relevant precedent, since it lives in
the same `a2a` package `signing_payload()` already signs, and compact separators leave no whitespace
ambiguity for a re-encoder to exploit (§ malleability argument below). Adopting a *third*,
differently-shaped canonicalization for envelopes (e.g. a literal
RFC 8785 JCS implementation, or a length-prefixed field-concatenation scheme) would buy no
additional guarantee for this field set — every value here is a string, a bounded int, `None`, or a
tuple/list of strings; there are no floats, no non-ASCII-normalization edge cases, and no nested
maps deep enough for RFC 8785's marginal guarantees (canonical float formatting, deep Unicode
normalization) to matter. `sort_keys=True` + compact separators is sufficient to make the byte
string a pure function of content, independent of dict/set iteration order — the same argument
`card.py:106-109`'s own docstring makes for the Card.

**`default=str` is NOT part of this recipe — deliberately, and this is the third axis on which the
two existing recipes differ.** The digest's `json.dumps` passes `sort_keys=True` and
`separators=(",", ":")` and **nothing else**: no `default=`. It therefore raises `TypeError` on any
value that is not JSON-native, which is the wanted behaviour — a non-JSON-native value reaching the
signing payload is a producer bug, and a fail-loud `TypeError` at signing time is strictly better
than a silently-coerced string that the verifier's own coercion may or may not reproduce. This
follows `card.py:124` (`json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")`
— no `default=`) and deliberately does **not** follow `hash_input()`
(`gateway/audit.py:60`, `json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)`)
or `AuditRecord._compute_hash()` (`gateway/audit.py:282`, `default=str,`). The difference is not
theoretical, and `deadline` is exactly where it bites: for
`datetime(2026, 8, 11, 12, 0, tzinfo=UTC)`, §4.1's `.isoformat()` mapping yields
`{"deadline":"2026-08-11T12:00:00+00:00"}` while `default=str` on the raw object yields
`{"deadline":"2026-08-11 12:00:00+00:00"}` — a `T` versus a space, two different byte strings, two
different MACs for the same instant. Pinning the conversion explicitly in the payload map (§4.1's
`envelope.deadline.astimezone(UTC).isoformat()`) and refusing `default=` is what keeps the preimage
a single, stated function of the envelope rather than a function of whichever `str()` a future
Python or a future field type happens to produce. **Why ambiguity = malleability, stated
precisely:** without `sort_keys` + fixed separators, two semantically-identical payloads (same
field values, different key order, or `", "` vs `","` whitespace) would serialize to different byte
strings and produce different MACs — an attacker who can influence serialization (e.g. relaying
through a JSON re-encoder) could then present a *re-encoded* copy of a validly-signed envelope with
a MAC that no longer matches, turning a legitimate signature verification into a denial-of-service
false-negative, or, worse, if a verifier naively re-canonicalizes before checking, opening a gap
where two byte-distinct-but-field-equal payloads are treated as interchangeable when they should
not be (classic JSON-canonicalization signature malleability). Fixing the canonicalization function
once, structurally, removes the ambiguity entirely rather than requiring every producer/consumer
pair to agree out-of-band.

**Why `payload_hash`, not `payload_ref` directly, in the digest.** The envelope's actual field is
`payload_ref` (a FHIR/pseudonymized reference string, never raw PHI — enforced by
`_looks_like_phi()`, `delegation.py:257-264`). Hashing it before inclusion (a) bounds the signed
payload's size and shape regardless of how long a future reference scheme's strings get, (b) mirrors
the ALREADY-established naming convention `payload_hash` from ADR-0037's canonical envelope
baseline (a coincidental but reassuring naming precedent for "a hash-of-payload field belongs in an
envelope's frozen field set"), and (c) keeps the digest's preimage shape stable if the "payload"
concept ever grows beyond a bare reference string (only the hash's *input* changes; the outer digest
schema does not). This is a design choice, not a requirement forced by any existing code — flagged
explicitly as a decision, open to legs 2-4 revisiting if they find a reason `payload_ref` should be
signed directly instead.

**Disclosed residuals (v1 status, r1-r3) — RESOLVED for two of three by §Decisoes do dono 1-2.** As
originally argued (r1-r3, reproduced below as the evidentiary record): `payload_meta`, `task_type`
and `max_hops` were excluded from the v1 mandated field set, two of the three being real gaps and
`payload_meta` the larger one. **As of this revision, `payload_meta` (via its hash) and `task_type`
are INCLUDED in digest v2 above — closed, not merely disclosed.** `max_hops` remains excluded; the
reasoning below for it is unchanged and still current. The v1 field set
(`{tenant, task_id, origin, target, payload_hash, deadline, budget, delegation_chain}`) is retained
below only as the historical baseline the argument that follows was made against.

**`payload_meta` — the most consequential omission, ranked at or above `task_type`.** On today's
four real edges `payload_meta` is not incidental metadata; it is the delegation's actual
instruction content, and every business decision the target makes is driven from it:

- Helena→Rafael: `_build_payload_meta` (`agents/helena/delegation.py:93-111`) carries the
  procedure code `"codigo_procedimento_tuss"` (`:99`), the diagnosis `"cid10"` (`:102`), and
  `meta["valor_estimado_brl"] = str(case_meta["valor_estimado_brl"])` (`:107-108`) — clinical
  coding and money, on the authorization path.
- worker→Carolina: `state_from_envelope` reads `meta = dict(envelope.payload_meta)` (`:211`) and
  `prestador_id = str(meta.get("prestador_id", "")).strip()` (`:212`), failing closed when blank
  (`:213-218`); its own docstring states why that matters — "Carolina's graph derives the
  idempotent business key from it, and her `start_process` node has no degenerate-key
  short-circuit" (`agents/carolina/delegation.py:207-209`). A tampered `prestador_id` therefore
  redirects the CIB business key itself.
- worker→Andre: `meta["valor_pagamento_cents"] = str(valor)` (`agents/andre/delegation.py:361`)
  is read back as `raw["valor_pagamento_cents"] = int(str(meta["valor_pagamento_cents"]))`
  (`:537-539`), and the builder's own comment names the blast radius — "`1234.99` reaching this
  builder became `1234` and Andre's faixa/alcada routing ran on a value the process never had —
  silently, on the money path" (`:349-354`). That is band/authority-level routing on money,
  driven entirely by an unsigned field.

The exclusion cannot be justified by the dispatcher's PHI precedent, and this ADR withdraws that
argument rather than restating it. The dispatcher's audit `details` payload does exclude
`envelope.payload_meta` (`dispatcher.py:451-456`, "`details` deliberately EXCLUDES
`envelope.payload_meta` — the one envelope field NOT covered by the `_looks_like_phi` guard on
`payload_ref`") — but that decision is about **not persisting or disclosing** those bytes into a
durable audit row. A digest binds `sha256(canonical(payload_meta))`: a 64-hex value that persists
nothing, discloses nothing, and is not reversible to the meta it covers. The PHI rationale
therefore does not transfer to the signing question at all; the two are different decisions about
different artifacts, and treating them as one call applied twice was a false equivalence.

**`payload_hash` does not cover for this, and on three of the four edges it covers almost
nothing.** `payload_ref` is `f"process://{task_id}"` at `agents/carolina/delegation.py:141`,
`agents/andre/delegation.py:204`, and `agents/andre/delegation.py:372` — so on those edges
`payload_hash` is a pure function of `task_id`, which the digest already binds as its own field. It
adds no independent binding there. Only Helena's edge passes a distinct reference
(`payload_ref=coverage_ref`, `agents/helena/delegation.py:88`). The signed digest as mandated thus
binds *who, where, when and how much budget*, and — on three of four edges — nothing whatsoever
about *what is being asked*.

**`task_type` — a real gap, of the same kind, one rank below.** `task_type` determines which Agent
Card capability gate the delegation is checked against (`A2ARegistry.lookup(...).accepts(task_type)`,
`dispatcher.py:388`) and which handler branch a target routes into (see
`a2a_composition.py:88-98`'s comment on Andre's shared `analytics.population` type
disambiguated by *origin*, not `task_type`, into different flows — meaning a tampered `task_type`
on an otherwise-validly-signed envelope could, depending on the target's routing logic, redirect a
delegation to a different semantic outcome without invalidating the signature). It ranks below
`payload_meta` only because it is still cross-checked at admission against the target's Card, while
`payload_meta` is cross-checked against nothing.

**`max_hops` — low marginal risk, but not for the reason previously stated.** `max_hops` is **not**
a repo-wide constant baked into every envelope: `MAX_HOPS: int = 3` (`delegation.py:42`) is only the
*default* of a sender-supplied `root()` keyword (`max_hops: int = MAX_HOPS`, `delegation.py:161`),
bounded solely by `if self.max_hops < 1: raise DelegationError("max_hops must be >= 1")`
(`delegation.py:130-131`) — a caller may pass `max_hops=99`. The honest reason its omission carries
little marginal risk is different and narrower: (a) no builder in `src/` supplies it today — the
only `max_hops=` keyword argument anywhere under `src/` is `delegation.py:184`, `root()` forwarding
its own parameter into the constructor; the three occurrences in
`agents/{helena,carolina}/delegation.py` (`helena:22`, `helena:78`, `carolina:131`) are all
docstring prose, and `agents/andre/delegation.py` has none at all — so it is always the default in
practice; and (b) the guard it parameterizes is evaluated **on the sender's side at construction**,
inside `root()`/`extend()`. `_validate()` (`dispatcher.py:376-400`) re-checks `expired()`, the
registry lookup, `accepts(task_type)` and handler presence, but never re-derives a hop bound, so
altering `max_hops` in transit changes nothing any dispatcher-side check consults. The residual,
stated plainly: a *forging* sender can already choose `max_hops` freely, signature or not, so
binding it would not remove that freedom — which is why it stays a disclosure rather than a
recommendation.

**r1-r3 status (superseded above).** This ADR originally followed the mandated field set as given,
widening it only on a human's call; both `payload_meta` and `task_type` were carried to §Perguntas
abertas as a Proposed-status option, not a decision. **The human call has been made: the owner
ratified `payload_meta_hash` and `task_type` on 2026-08-12** (§Decisoes do dono, table rows 1-2) —
the digest v2 field set at the top of this section is the resulting mandated set, not a proposal.
`max_hops` was not part of that ratification and remains a disclosure, per the reasoning above.

### 4.2 Signature metadata — key-id, algorithm version, replay epoch

Three new fields, bound INTO the signed digest itself (§4.1's payload already includes them) so
that none of them can be swapped post-signing without invalidating the MAC — the same reasoning
`card.py:122` gives for embedding `"scheme": SIGNATURE_SCHEME` inside the Card's own signed payload
(an unbound `key_id`/`scheme`/`epoch` would let an attacker present a validly-MAC'd payload under a
*different* claimed key/scheme/epoch than the one that actually produced it — a
key-confusion/downgrade attack, the JSON-signing analogue of JWT's "alg:none" class of bugs):

- **`scheme`** — an algorithm+version tag, e.g. `"hmac-sha256-envelope-v1"` (distinct string space
  from the Card's `SIGNATURE_SCHEME = "v1"`, §4.1's note on the two surfaces being independent) so a
  future migration (asymmetric signing, a different digest algorithm) is representable without
  breaking the field's shape — mirrors `card.py:48-50`'s own rationale for `SIGNATURE_SCHEME`
  verbatim.
- **`key_id`** — identifies *which* trusted key produced the MAC. Card signing today has exactly one
  active key (`MAEZO_A2A_CARD_SIGNING_KEY`, `assembly.py:45`, a scalar, no rotation story). Envelope
  signing needs **more than one concurrently-trusted key** to rotate without an outage: the verifier
  resolves `key_id` against a small trusted-keyset (`dict[key_id, bytes]`), not a single scalar, and
  signing always uses the CURRENT/active `key_id`. A stale `key_id` (retired but not yet purged from
  the trusted set) is only accepted inside its explicit rotation grace window (below); a `key_id`
  the verifier has never heard of is rejected unconditionally.
- **`replay_epoch`** — a monotonically-increasing integer, distinct from ordinary key rotation.
  Ordinary scheduled rotation adds a new `key_id` under the **same** epoch (smooth overlap, both
  keys valid). An epoch bump is a **hard cutover**, reserved for suspected-compromise / incident
  response: every envelope claiming a prior epoch is rejected immediately regardless of whether its
  `key_id` is otherwise still in the trusted set — the blunt, fast instrument for "we do not trust
  *anything* signed before this moment," independent of which specific key produced it. This is
  deliberately a coarser, cheaper mechanism than per-key revocation: bumping the epoch does not
  require enumerating or purging every possibly-compromised key.

**Cadencia de rotacao — decidida (§Decisoes do dono, table row 3, 2026-08-12): 7 dias.** Rotacao
ordinaria de chave publica um novo `key_id` a cada 7 dias; a chave anterior permanece no trusted set
por uma janela de graca de **7 dias** apos ser suplantada — a mesma duracao do ciclo, para que
qualquer envelope assinado no instante imediatamente anterior a uma rotacao ainda encontre sua
`key_id` no trusted set pelo tempo que a idade maxima de assinatura (§4.3, item 2, derivada da mesma
cadencia) permitir que ele continue em voo. §4.3 abaixo deriva a idade maxima de assinatura e o piso
de retencao de `a2a_idempotency` diretamente desse numero.

**Janela de graca do epoch anterior — decidida (§Decisoes do dono, table row 5): SIM, curta e
tolerada.** Ao contrario da rotacao ordinaria, um bump de `replay_epoch` continua reservado a
incidente (suspeita de comprometimento); a decisao do dono autoriza uma janela de graca **curta** —
mais curta que a janela de rotacao ordinaria de 7 dias — durante a qual assinaturas do epoch anterior
ainda sao aceitas, evitando rejeicao em massa de envelopes em voo no momento do bump. A duracao exata
dessa janela e um parametro operacional escolhido por quem executa o bump no momento do incidente,
nao um numero fixo nesta ADR — a decisao do dono confirma apenas que ela PODE ser nao-zero.
**Semantica quando a graca e ZERO (bump duro, sem tolerancia):** todo envelope reivindicando o epoch
anterior e rejeitado imediatamente a partir do instante do bump, independentemente de seu `key_id`
ainda estar no trusted set — nenhum envelope em voo sobrevive; qualquer trabalho em andamento
assinado sob o epoch anterior deve ser re-emitido (re-assinado sob o epoch corrente) para prosseguir.
Essa e a semantica que ja valia implicitamente antes da decisao do dono (o "instrumento contundente"
descrito acima); a decisao de 2026-08-12 apenas AUTORIZA o operador a escolher uma graca curta em vez
de zero, sem alterar o que graca-zero significa.

### 4.3 Key rotation and revocation

Ordinary rotation: publish a new `key_id`→key entry into the trusted set, start signing new
envelopes under it, retain the *previous* `key_id` in the trusted set for a **grace window bounded
by the longest `deadline` horizon actually in use** — because `DelegationEnvelope.deadline`
upper-bounds how long any single envelope can remain "in flight" before `expired()`
(`delegation.py:144-148`) independently rejects it, the rotation grace window need never exceed that
horizon: once every envelope that could have been signed under the old key has either completed or
expired on its own, the old `key_id` can be purged with zero risk of rejecting a still-legitimate
in-flight delegation. **Concretized as of 2026-08-12 (§Decisoes do dono, table row 3):** the owner
fixed ordinary rotation at a 7-day cadence (§4.2), so this grace window is now a stated number — 7
days — rather than a bound left to be worked out from `deadline` alone; item 2 below derives the
independent max-signature-age bound from that same number and shows how the two interact.

**That bound is vacuous on today's envelopes, and this ADR fixes it with an explicit constraint
rather than assuming it away.** `deadline` is optional (`deadline: datetime | None = None`,
`delegation.py:122`) and `expired()` returns `False` outright when it is unset (`delegation.py:146-147`,
`if self.deadline is None: / return False`). **Zero of the four `root()` call sites in `src/` pass a
`deadline=`** — `agents/helena/delegation.py:81-90`, `agents/carolina/delegation.py:134-143`,
`agents/andre/delegation.py:197-206` and `agents/andre/delegation.py:365-374`; grepping `deadline`
across those three modules returns no occurrences at all. So on every envelope this system builds
today, `deadline is None`. Three consequences follow, and legs 2-4 inherit all three as binding
constraints:

1. **A `deadline`-derived grace window is unbounded as written.** "The longest `deadline` horizon
   actually in use" is undefined when no envelope carries one, so a retired `key_id` would have no
   principled purge date. **Constraint: a signed envelope MUST carry a non-`None` `deadline`.** The
   verification step rejects a signed envelope whose `deadline` is `None` — signing is what makes
   the field load-bearing, so this is a signing-path requirement, not a change to the envelope's
   general contract. **This obliges legs 2-4 to update all four builders** (`helena:81-90`,
   `carolina:134-143`, `andre:197-206`, `andre:365-374`) to pass an explicit `deadline=`; a leg that
   ships signing without touching those four builders ships a gate that rejects every real envelope,
   or a grace window that means nothing.
2. **A max-signature-age bound, independent of `deadline`, is ALSO required** — belt to the
   `deadline` suspenders. `deadline` is chosen by the sender and is inside the digest, so a
   legitimately-signed envelope with a far-future `deadline` would extend its own key's grace window
   arbitrarily. Legs 2-4 fix a verifier-side maximum signature age (a policy constant, not a sender
   input) and reject any signature older than it regardless of `deadline`. **Value, derived
   (§Decisoes do dono, table row 3, 2026-08-12): max signature age = 7 days.** Derivation: the owner
   fixed the ordinary rotation cadence at 7 days (§4.2), and §4.2's rotation grace window is set to
   that same 7 days so a `key_id` remains trusted for exactly one full rotation cycle after being
   superseded. A signature older than that cycle is, by construction, one whose signing `key_id` may
   already have been purged — so capping acceptance at the cycle length (7 days) keeps the
   independent age bound consistent with the keyset's own retention, rather than an arbitrarily
   different number that could accept a signature whose key is already gone, or reject one whose key
   is still valid. The purge date for a retired `key_id` is then `max(longest deadline horizon in
   use, max signature age)` = `max(longest deadline horizon in use, 7 days)` — and since every real
   `deadline` on today's four edges is operationally far shorter than 7 days (hours, not days, once
   legs 2-4 implement constraint 1), max signature age dominates: **the purge date is 7 days after a
   `key_id` is superseded**, a bound that holds even if a builder regresses to `deadline=None`.
3. **Anti-replay leans on durable idempotency, which makes idempotency a RETENTION requirement, not
   only a durability one.** With no expiry on the envelope, the only thing that stops a captured,
   validly-signed envelope from being replayed indefinitely is Guard 4 remembering its `task_id`.
   **Retention of the durable idempotency rows MUST dominate the signature-validity window**: a row
   purged while its envelope's signature is still acceptable re-opens the replay it was preventing.
   Today `a2a_idempotency` has no expiry column and no purge path at all
   (`platform/migrations/versions/0003_a2a_idempotency.py:32-45` — `PRIMARY KEY (task_id, tenant)`
   at `:43`, no `expires_at`; contrast `driver_idempotency` in the same migration, which does have
   `expires_at timestamptz NOT NULL` at `:64` and an expiry index at `:70-71`), so the constraint on
   legs 2-4 is a *prohibition*: do not introduce a retention/purge policy for `a2a_idempotency`
   shorter than the max-signature-age of item 2 without changing that bound in the same step.
   **Floor, computed (§Decisoes do dono, table row 3): `a2a_idempotency` retention MUST be >= 7
   days.** Derivation chain, each step following from the one before: (i) ordinary rotation cadence
   = 7 days (owner decision, §4.2); (ii) rotation grace window = 7 days, set equal to the cadence so
   a `key_id` stays trusted for one full cycle past being superseded (§4.2); (iii) max signature age
   = 7 days, derived above in item 2 as that same cycle length, since a signature the verifier would
   still accept can be at most one rotation cycle old; (iv) by this item's own dominance constraint
   (retention >= max signature age), the idempotency retention floor = max signature age = **7
   days**. **This ADR computes the floor; it does not choose the actual retention window.**
   MZO-060/DBA picks the real `a2a_idempotency` retention/purge value against this floor (>= 7 days)
   — a number now grounded in a stated derivation rather than an open dependency.

**A related asymmetry the constraint above does not cover.** The durable store keys its claim on
`(task_id, tenant)` (`claim_or_get(*, tenant, task_id)`, `dispatcher.py:305`; `PRIMARY KEY
(task_id, tenant)`, `0003_a2a_idempotency.py:43`), but the in-memory fallback keys on `task_id`
**alone** — `self._inflight: dict[str, _InflightEntry]` (`dispatcher.py:274`), claimed via
`self._entry_for(envelope.task_id)` (`dispatcher.py:285`, `:312-318`). The two paths therefore do
not have the same replay semantics across tenants. That is one more reason §4.6 makes durable
idempotency mandatory outside dev-local rather than treating the in-memory path as a merely-slower
equivalent.

Tying rotation to a bound the envelope enforces remains the right shape — but it is only a real
bound once constraint 1 above is implemented.

Revocation (suspected key compromise): remove the compromised `key_id` from the trusted set
immediately AND bump `replay_epoch` in the same action — removing the `key_id` alone is
insufficient if the compromise is severe enough that the attacker could forge envelopes under a
*still-trusted* `key_id` from the same epoch; the epoch bump is the belt to the `key_id` removal's
suspenders, covering the case where the specific compromised key is not precisely known.

### 4.4 Verification points — where, and the fail-closed gate

**Signing** happens once, at envelope construction time — mirroring
`AgentCard.from_definition(..., signer=signer)`'s pattern (`card.py:140-185`) of signing at the
exact point identity is fixed. **Envelope construction is NOT a composition root, and saying "by
whichever composition root holds the signing key" would misstate the blast radius.** Cards are
built once at composition; envelopes are built **per request**, deep inside the agent packages:
`build_auth_analysis_envelope` (`agents/helena/delegation.py:63`),
`build_cred_dossier_envelope` (`agents/carolina/delegation.py:115`),
`build_adequacao_dossier_envelope` (`agents/andre/delegation.py:169`) and
`build_pagto_dossier_envelope` (`agents/andre/delegation.py:278`). Three of the four are reached
from worker handlers via the lazy-imported `delegate_*_dossier` wrappers
(`tools/workers/credenciamento.py:634,:637`, `tools/workers/adequacao.py:785,:788`,
`tools/workers/pagto.py:886,:892`). So the signing key — or a signer seam that holds it — has to
reach four per-request builders in two packages, not two composition functions. That is the real
key-custody blast radius, and it is carried into §Perguntas abertas 4.

**Key custody — decided (§Decisoes do dono, table row 4, 2026-08-12): same vault/KMS seam as Card
signing, keyset PER-TENANT.** The owner resolved this ADR's largest open custody question directly:
envelope-signing keys are provisioned through the identical vault/KMS delivery seam that already
provisions `MAEZO_A2A_CARD_SIGNING_KEY` (`src/maezo/a2a/assembly.py:45` — the env-var constant,
`CARD_SIGNING_KEY_ENV_VAR` — read at `:67` by `card_signing_key_from_env()`; never hard-coded, never
logged, whitespace-stripped before use) — but, unlike the Card-signing key (repo-wide, a single
scalar, no per-tenant scoping), **the envelope-signing keyset is scoped PER TENANT**: each tenant
resolves its own `dict[key_id, bytes]` trusted set, never a single repo-wide dict shared across
tenants. This matches the tenant-scoping precedent `A2ARegistry` already applies (ADR-0004,
`registry.py:8-10`, cited in §4.5's Cross-tenant-replay row) and is the sharper posture given the
blast radius: a leaked repo-wide envelope key would let an attacker forge envelopes across every
tenant at once, a strictly worse outcome than a leaked Card key. The precise per-tenant resolution
mechanism (a per-tenant env var naming scheme, a single structured secret enumerating all tenant
keys, or a vault/KMS path templated on `tenant`) is an implementation decision for legs 2-4 — this
ADR fixes only that it MUST reuse the vault/KMS delivery seam (mirroring
`card_signing_key_from_env`/`CardSigner`'s existing posture: absent -> `None`, dev fail-safe only
with an explicit opt-out; present-but-malformed -> raise, never silently downgrade) and MUST be
scoped per-tenant, never repo-wide. Whatever seam is built must reach all **four** per-request
builders named above (`agents/helena/delegation.py:63`, `agents/carolina/delegation.py:115`,
`agents/andre/delegation.py:169` and `:278`) **plus the worker package**
(`tools/workers/credenciamento.py`, `tools/workers/adequacao.py`, `tools/workers/pagto.py`) that
lazily imports three of them, since that is where the per-request signing call must actually be
threaded through at dispatch time, not merely at the two composition roots.

**Migration path — fail-closed, any compatibility shim explicitly time-boxed, never silent.** No
envelope-signing key exists today (§Contexto point 1: envelope signing "does NOT exist"), so there
is no live single-key deployment to migrate off of — but the Card-signing precedent (single
repo-wide key) is exactly the anti-pattern the owner's per-tenant decision rules out for envelopes,
and legs 2-4 must not default into it. If operational rollout requires a transitional shared key
while per-tenant keys are provisioned tenant-by-tenant in the vault/KMS, that shared-key
compatibility shim MUST: (a) be named explicitly and distinctly from the per-tenant keyset — never
silently reused as a `"default"` tenant entry that quietly outlives its purpose; (b) carry an
explicit sunset date/milestone recorded in this ADR's revision history or a follow-up amendment — an
untimed shim is exactly the kind of silent default this ADR's fail-closed posture exists to prevent;
and (c) be fail-closed once the time-box expires — composition refuses to build a dispatcher for any
tenant lacking a provisioned per-tenant key past that date (mirroring
`_require_signer_or_fail_closed`'s raise-at-composition posture, §Contexto point 3), rather than
silently falling back to the shared key indefinitely.

The precise call shape (a `signer=` parameter threaded through `root()`/`extend()`, or a standalone
`sign_envelope(envelope, signer) -> DelegationEnvelope` step applied immediately after) is an
implementation decision for legs 2-4; this ADR fixes only that signing must happen before the
envelope crosses the dispatcher boundary and must cover exactly §4.1's canonical payload.

**Latent trap for whichever leg adds a `signature` field: `extend()` propagates by
`dataclasses.replace`.** `extend()` ends in `return replace(self, ...)`
(`delegation.py:217-226`), overriding only `task_id`, `task_type`, `target`, `delegation_chain`,
`budget`, `payload_ref` and `payload_meta`. Every other field is **carried over verbatim** — so a
`signature` added to `DelegationEnvelope` would be silently inherited by a sub-envelope whose
`task_id`, `target` and `delegation_chain` (and `budget`) have all changed, i.e. a sub-envelope
carrying a MAC computed over a *different* digest. Under §4.1's field set that signature cannot
verify, so the failure mode is a confusing rejection rather than a forgery — but it is exactly the
kind of silent propagation that becomes a forgery the moment someone "fixes" the rejection by
loosening the check. **Constraint: legs 2-4 must make `extend()` either drop the signature
explicitly (returning an unsigned sub-envelope that must be re-signed) or re-sign in place; it must
never inherit it.** This is cheap to get right now and expensive later: `extend()` has **zero
callers in `src/`** today — every call site is under `tests/unit/a2a/` (13 in total, across
`test_delegation.py`, `fakes.py` and `test_a2a_edge_live_pg.py`) — so the constraint costs nothing
to honour and there is no production behaviour to preserve.

**Verification** happens inside `DelegationDispatcher`, as the **first check inside the dispatcher**
— and the placement is pinned, not left to legs 2-4, because the obvious alternatives defeat the
goal. It runs in `delegate` itself (`dispatcher.py:277`), **ahead of the durable-vs-in-memory branch
at `dispatcher.py:279-281`** (`if self._idempotency is not None: / return await
self._delegate_durable(...) / return await self._delegate_inflight(...)`), and therefore ahead of
both idempotency claims and ahead of `_validate()` (`:376-400`, registry/Card lookup, `task_type`
acceptance, expiry).

**`_execute` is explicitly NOT a sanctioned location**, and neither is either `_delegate_*` helper.
Both claim the idempotency store *before* they call `_execute`: `_delegate_durable` does
`stored = await store.claim_or_get(...)` at `:305` and only then `result = await
self._execute(envelope)` at `:308`; `_delegate_inflight` does `entry = await
self._entry_for(envelope.task_id)` at `:285` and only then `result = await self._execute(envelope)`
at `:291`. Verifying at `_execute` would therefore let a forged envelope claim a `task_id` of the
attacker's choosing before the signature is ever checked — the precise poisoning this ADR's
ordering exists to prevent. Placing the check in `delegate` ahead of `:279-281` means a forged
envelope never touches either idempotency path, never triggers a registry lookup, and never reaches
the audit emission in `_execute`.

**"First" is scoped to the dispatcher, and this ADR does not overclaim it.** The seam gate runs
earlier: `GatedDelegationDispatcher.delegate` calls `gate(...)` (`gateway/seams/a2a.py:60`) before
`self._inner.delegate(envelope)` (`:61`), and `gate()` emits a shadow line (`_base.py:378`) and
invokes the `audita_antes` pre-effect audit hook (`_base.py:381`). That hook is inert **only**
because `audita_antes` defaults to `False` (`gateway/effect_classes.py:136`) and `delegacao_a2a`
does not override it (`effect_classes.py:199-203`), so `_pre_effect_audit` returns at its first
branch (`_base.py:316-318`). The honest statement is therefore: verification is the first check
inside the dispatcher, and nothing durable precedes it *today* — but if `delegacao_a2a` ever
declares `audita_antes=True`, the seam writes a record for an envelope whose signature has not yet
been examined. Whoever flips that flag owns re-deciding this ordering. A failed verification returns
a structured
`DelegationResult.rejected(...)` (never a raise — preserving the dispatcher's existing "never
raised to the caller" contract, `dispatcher.py:16`'s own module docstring; `DelegationResult`
itself is documented "never a raise out of the dispatcher", `dispatcher.py:154`), via a **new**
`RejectionReason` member
(e.g. `SIGNATURE_INVALID`) that legs 2-4 add to the existing enum (`dispatcher.py:136-141`) — new
vocabulary this ADR authorizes but does not itself add (no code changes here).

**This new check must be `_`-prefixed / internal, never a new public method** — §Contexto point 4
above is the reason restated as a hard constraint: `GatedDelegationDispatcher`
(`gateway/seams/a2a.py:54-62`) only forwards `delegate`, and
`test_delegation_dispatcher_public_surface_is_only_delegate`
(`tests/unit/gateway/seams/test_seam_proofs.py:809`) fails the build the moment
`DelegationDispatcher` grows a second public method. Verification logic belongs entirely inside
`delegate`'s own body — a `_verify_or_reject(envelope)`-shaped private helper called from
`delegate` before the `:279-281` branch, per the placement pinned above. **If legs 2-4 need to
expose verification as a
standalone public entry point for any reason (e.g. a pre-flight check callable before `delegate`),
the `GatedDelegationDispatcher` wrapper MUST be updated in the SAME change** to forward it through
the `delegacao_a2a` gate — otherwise a new public method is a structurally ungated hole in the
action-execution-gateway chokepoint (ADR-0037 XRD-09), silently bypassing the seam whose entire job
is to be the *only* place `delegate` — or now potentially a sibling method — is reachable from.

**Fail-closed outside dev-local, reusing the existing discriminator — and inheriting its known
asymmetry.** The verification gate mirrors `_require_signer_or_fail_closed`
(`a2a_composition.py:136-189`) exactly, as a structural sibling at both composition roots: trusted
keyset present → verify; absent + production runtime → raise at composition (no dispatcher without
a verifier is ever returned); absent + non-production + an explicit, new
`MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES`-shaped opt-out → proceed unverified, loudly warned; absent +
non-production + no opt-out → also raise. Because this gate is built as a sibling at the SAME two
call sites, it inherits whichever runtime-mode discriminator that site already uses: the
Helena→Rafael edge (`build_auth_delegation_dispatcher`) reads `settings.agent_runtime_mode`
(`AGENT_RUNTIME_MODE`, **absent → `"local"`, permissive-by-default**); the worker dossier edge
(`build_dossier_delegation_dispatcher`) reads `worker_runtime_mode_from_env()` (`RUNTIME_MODE`,
**absent → `"production"`, restrictive-by-default**). This ADR does **not** resolve that asymmetry
— it is a pre-existing, owner-level inconsistency (§Contexto point 2) — and states plainly that the
"fail-closed outside dev-local" guarantee this ADR mandates is, at the Helena→Rafael edge,
**only as strong as `AGENT_RUNTIME_MODE` actually being set** in every real deployment; an
unset `AGENT_RUNTIME_MODE` in a genuinely-production agent-runtime pod would silently qualify as
"local" for BOTH the existing Card-signing gate and this ADR's new envelope-verification gate.
Fixing that default is out of scope here; legs 2-4 inherit it as a known residual, not a
newly-introduced one.

**`MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES` — name and shape CONFIRMED (§Decisoes do dono, table row
6, 2026-08-12).** The opt-out env var named above is not a proposal legs 2-4 may rename; the owner
confirmed it verbatim. Its posture is dev-local only, fail-closed everywhere else, and it reuses the
SAME shared discriminator this gate already reuses — `is_production_runtime_mode`
(`a2a_composition.py:139-160`, extracted in Onda 3/Train C from `_require_signer_or_fail_closed`
precisely so no fail-closed gate in this module can drift into a second, subtly different answer to
"is this production?"). No second discriminator is introduced for the envelope-verification gate:
`MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES` is honored only when `is_production_runtime_mode(runtime_mode)`
is `False`, exactly like `MAEZO_A2A_ALLOW_UNSIGNED_CARDS` today (§Contexto point 3). This ADR's
required amendments named this confirmation as belonging to §4.6; the text it confirms in fact lives
here in §4.4 (§4.6 covers durable idempotency and the transactional outbox, an unrelated pair of
constraints) — placed at its actual textual home rather than forced into an unrelated section.

### 4.5 Threat table

| Attack | Defense |
|---|---|
| **Tamper** (any signed field altered in transit or in-process before reaching the dispatcher) | HMAC-SHA256 over the canonical digest (§4.1) fails; the dispatcher's verification step (§4.4, first check in `delegate`, ahead of `dispatcher.py:279-281`) rejects before idempotency claim, audit, or routing. **Scope, stated exactly:** this row covers the SIGNED fields only. `payload_meta` and `task_type` are outside the mandated digest, so tampering with them is **not** defended here — see §4.1's disclosed residuals, where `payload_meta` (which carries TUSS/CID-10/`valor_estimado_brl`, the CRED business key, and the pagto faixa/alcada value) is ranked the more severe of the two. |
| **Cross-tenant replay** (an envelope captured for one tenant re-presented against another) | `tenant` is inside the signed digest — retagging it invalidates the MAC. Defense-in-depth: `A2ARegistry` is tenant-scoped by construction (ADR-0004, `registry.py:8-10`) and `PostgresIdempotencyStore`'s primary key is `(task_id, tenant)` (`idempotency.py:38`), so even a same-`tenant`-field replay against a different registry/store partition finds no matching Card/claim. |
| **Expiry bypass** (`deadline` stripped or extended after signing) | `deadline` is inside the signed digest — altering it invalidates the MAC. Independently, `DelegationEnvelope.expired()` (`delegation.py:144-148`) is still checked by `_validate()` even for a validly-signed, correctly-dated envelope — two independent checks, neither a substitute for the other. **This row is VACUOUS on today's envelopes and becomes real only via §4.3's constraint 1:** all four `root()` call sites omit `deadline=`, so the signed value is `"deadline": None`, and `expired()` returns `False` unconditionally (`delegation.py:146-147`). Binding `None` binds nothing there is anything to bypass. The defense is only in force once signed envelopes are required to carry a non-`None` `deadline` (§4.3.1) **and** the verifier enforces a max signature age independent of it (§4.3.2). |
| **Stale key** (an envelope signed under a retired or compromised key) | `key_id` and `replay_epoch` are inside the signed digest (§4.2) and separately checked against the CURRENT trusted keyset — a retired `key_id` fails outside its rotation grace window (§4.3); an epoch bump (§4.2) hard-rejects every prior-epoch signature regardless of `key_id` validity. |
| **Altered `payload_hash`** (`payload_ref` swapped for a different FHIR/pseudonymized reference after signing) | `payload_hash` is inside the signed digest — swapping `payload_ref` changes the hash, invalidating the MAC. Binds the specific PHI-safe reference to the exact authorized envelope; combined with the existing `_looks_like_phi()` guard (`delegation.py:257-264`), which independently rejects raw-PHI-shaped references at construction. |
| **Crash-before-complete** (dispatcher/replica crashes after admission but before the handler's effect completes) | **Signature verification alone does not defend this** — it is orthogonal. The actual defenses are DURABLE idempotency (Guard 4, `PostgresIdempotencyStore`, already implemented but only conditionally wired — §4.6) and a TRANSACTIONAL OUTBOX replacing `_NoopKafkaProducer`'s fire-and-forget emission — **both planned in this train, legs 2-3, not delivered by this ADR.** Referenced forward honestly: this ADR mandates them as consequences (§4.6); it does not build them. |

### 4.6 Consequences for legs 2-3: durable idempotency + transactional outbox are MANDATORY design constraints

**Durable idempotency MANDATORY outside dev-local.** Today, `PostgresIdempotencyStore` wiring is
gated purely on `database_url` truthiness (`a2a_composition.py:266`, `:369`) — with **no
runtime-mode check at all**, unlike the Card-signing gate. This is a real, currently-existing gap:
a production runtime with `DATABASE_URL` unset today silently falls back to in-memory-only Guard 4
with no raise, no warning. This ADR's design constraint for legs 2-3: build a
`_require_durable_idempotency_or_fail_closed`-shaped gate, structurally mirroring
`_require_signer_or_fail_closed`, at BOTH composition roots — absent `database_url` + production
runtime → raise at composition; absent + non-production + explicit opt-out → proceed in-memory,
loudly warned; absent + non-production + no opt-out → raise. This closes the exact gap §Contexto
point 2 names, and is a precondition for the "crash-before-complete" defense in §4.5's threat table
to actually hold outside dev-local.

**Transactional outbox replacing `_NoopKafkaProducer`'s facts — with an explicit fence-allowlist
consequence.** `_NoopKafkaProducer` is a **declared, by-name exception**
(`check_effect_chokepoint_fence.py:401-412`, entry `:406`) in the effect-chokepoint fence's §8.4
allowlist — the design doc's own instruction
(`docs/design/wave1-effect-chokepoint.md:746-748`, inside §8.4 at `:741-748`) is "exception to
declare explicitly, not silently: `_NoopKafkaProducer` ... allowlist it by / name with the
rationale, never by pattern."
If legs 2-3 replace `_NoopKafkaProducer` with a real, transactional-outbox-backed producer at
BOTH construction sites (`a2a_composition.py:264`, `:368`), the fence's declared exception entry
becomes unused **once no definition or import of that name remains in that module** — and should
then be **removed, not widened**. The retirement trigger is the `class _NoopKafkaProducer`
statement at `a2a_composition.py:192` disappearing (or, equivalently, the name no longer being
imported into a composition-root module), **not** the disappearance of its call sites: §8.4 fires
on `ast.ClassDef` (`check_effect_chokepoint_fence.py:611-615`) and on
`ast.ImportFrom`/`ast.Import` (`:618-633`, `:634-638`), never on `ast.Call`, and applies the
allowlist to imports at `:822` and to definitions at `:835`. A leg that swaps both constructions to
a real producer but leaves the class body in place therefore leaves the allowlist entry **still
live and still required** — removing it in that state would turn a green gate red. Removal is
consistent with `_is_test_double_name`'s own comment (`:416-427`) that this gate never
loosens its `Fake*`/`*Mock*`/`Noop*` pattern match on its own authority. If a *new* named
local/dev-only fallback double is introduced in the replacement's place, it must be added to
`DECLARED_TEST_DOUBLE_EXCEPTIONS` (`:401`) **by name, with rationale, in the same change** — never
by pattern, and never silently. This ADR does not modify that allowlist; it records the constraint
for whichever leg does the replacement.

### 4.7 mTLS / service identity — DEFERRED

**Deferred, explicitly, until a remote transport exists.** The design doc for the existing A2A build
states this outright and unchanged across multiple waves: "Remote cross-pod A2A transport
(queue_ref/endpoint, HTTP/mTLS) remains explicitly out of scope"
(`docs/design/A2A-dispatcher-card-signing.md:320`). Today's built edges — Helena→Rafael (assembled
at the composition root, `_EDGE_AGENT_IDS = ("helena", "rafael")`, `a2a_composition.py:86`, and
driven by the W3 live-Postgres suite rather than by any `src/` caller —
`tests/unit/a2a/test_a2a_edge_live_pg.py:1`, "LIVE-Postgres proof: the Helena->Rafael A2A
delegation edge's 'T-F becomes real' claim (W3)") and the three worker→Carolina/Andre dossier edges
(reached from real worker code: `tools/workers/credenciamento.py:637`,
`tools/workers/adequacao.py:788`, `tools/workers/pagto.py:892`) — are all **Option A: in-process
co-located** (`a2a_composition.py:1-10` module docstring). There is no network hop, no serialization
boundary, and therefore no transport-authenticity question for mTLS to answer yet. Envelope signing as
designed here is transport-independent (it protects the envelope's *content*, not the channel it
travels over) and is valuable even in-process today (defense against a compromised/buggy in-process
caller constructing a spoofed envelope via direct dataclass construction outside `root()`/`extend()`
— nothing in the current code prevents that — plus strengthening non-repudiation for the audit
chain, ADR-0007), but it does **not** substitute for mTLS once a real remote transport exists.
**Trigger condition, stated explicitly for whoever eventually builds a remote transport:** mTLS
(or an equivalent transport-authenticity mechanism) becomes REQUIRED design work the moment any A2A
edge crosses a process/pod boundary over a network (i.e., the moment Option A's "in-process
co-located" premise stops holding for any edge) — this is not a recommendation, it is the condition
under which "explicitly out of scope" stops applying and a NEW ADR (or an amendment to this one)
must cover it before that transport ships.

### 4.8 Acceptance criteria — the proof obligations legs 2-4 must discharge

A threat table is a claim, not evidence. This ADR is only ratifiable-in-retrospect if legs 2-4 ship
proof that each claimed defense is actually load-bearing, and the repo already has the precedent for
what "proof" means here: the effect-chokepoint fence's own §8.5, titled **"Non-vacuity and
completeness assertions (the gate must prove it is doing work)"**
(`docs/design/wave1-effect-chokepoint.md:750`), implemented as a per-rule counter — "Every rule
carries a non-vacuity counter: a rule that never fires because nothing in the real tree / exercises
its allowlisted path is exactly the kind of gate this repo has been bitten by before"
(`scripts/ci/check_effect_chokepoint_fence.py:28-29`), materialized as `counters[...]` increments at
`:724`, `:751`, `:765`, `:780`, `:793`, `:806`, `:823`, `:836` and printed at `:666-668`/`:1149-1151`.
Envelope signing gets the same treatment. Three obligations, all on leg 4:

1. **Red-control attack test per threat-table row.** Every row of §4.5 gains at least one test that
   *performs the attack* and asserts the rejection — not a test that merely asserts a happy-path
   verification succeeds. Rows and their attacks: **Tamper** (mutate a signed field post-signing,
   assert `SIGNATURE_INVALID`); **Cross-tenant replay** (retag `tenant`, assert rejection);
   **Expiry bypass** (extend `deadline` post-signing, assert rejection — and, per §4.3.1, a test
   that a signed envelope with `deadline=None` is refused, which is what stops this row from
   staying vacuous); **Stale key** (sign under a retired `key_id` outside its grace window, and
   separately under a prior `replay_epoch`, assert rejection in both); **Altered `payload_hash`**
   (swap `payload_ref` post-signing, assert rejection). The **Crash-before-complete** row is
   explicitly NOT a signature test — its proof is a durable-idempotency replay test plus an outbox
   test, per §4.6, and it must be labelled as such rather than counted as signature evidence.
2. **Each red control must be a real control — the test must FAIL when the defense is neutered.**
   For every test in obligation 1, the leg demonstrates that removing or short-circuiting the
   specific defense (skipping the verification call, dropping the field from the digest, widening
   the keyset to accept any `key_id`, ignoring the epoch) makes that test fail. A test that still
   passes with the defense removed is proving nothing and does not count toward this section. This
   is the same standard `_is_test_double_name`'s own census applies to itself
   (`check_effect_chokepoint_fence.py:416-427`): a rule that fences zero real names is disclosed as
   such rather than counted as coverage.
3. **Non-vacuity of the digest itself.** A test asserts that the canonical payload actually contains
   every field §4.1 lists (so a field silently dropped from the preimage is caught), and — because
   §4.1 discloses `payload_meta`/`task_type` as residuals — a test asserts the **converse** too:
   that mutating `payload_meta` or `task_type` alone does **not** change the digest today. That
   negative test is the honest, executable form of the disclosed residual; if a human later ratifies
   `payload_meta_hash` (§Perguntas abertas 1), that test flips to a positive assertion in the same
   change, and the flip is the evidence the residual was closed.

None of these obligations are discharged by this ADR. They are the acceptance criteria against
which leg 4 is reviewed.

## Consequencias

**Positivas:**
- Closes a real, currently-open gap: a forged or tampered delegation envelope that satisfies the
  four existing structural anti-loop guards passes today with no cryptographic check at all — this
  ADR gives the dispatcher a fail-closed, tamper-evident admission gate over the mandated field set
  (`{tenant, task_id, origin, target, payload_hash, deadline, budget, delegation_chain}`) — see
  §Negativas for what that set does not cover.
- Reuses, rather than reinvents, three separate existing precedents (Card-signing HMAC/canonical-JSON
  pattern, the `_require_signer_or_fail_closed` composition-root gate shape, and the audit chain's
  own `sort_keys`+compact-separator canonicalization) — minimizing the number of NEW cryptographic
  primitives this repo has to reason about and review.
- Ties key-rotation grace windows to a bound the envelope already models (`deadline`) rather than
  inventing a separate rotation-specific TTL policy — and, having found that bound unpopulated on
  every real envelope today, converts it into explicit, checkable constraints on legs 2-4
  (§4.3.1/§4.3.2/§4.3.3) instead of relying on it silently.
- Turns the §4.5 threat table into acceptance criteria rather than assertions: §4.8 requires a
  red-control attack test per row, each proven to fail when its own defense is neutered.
- Makes the crash-before-complete gap's real fix (durable idempotency + transactional outbox)
  MANDATORY design constraints for legs 2-3, closing a durability gap that exists independently of
  signing (§4.6) and was previously silent.

**Negativas (aceitas):**
- New cryptographic surface: a compromised HMAC key (or, later, an asymmetric key) is a new risk the
  pure structural-guard design did not carry; key custody is explicitly a human/infra decision this
  ADR does not make (§Perguntas abertas).
- Inherits, rather than fixes, the `AGENT_RUNTIME_MODE`/`RUNTIME_MODE` absent-variable asymmetry
  (§4.4) — the fail-closed guarantee this ADR mandates is only as strong as that pre-existing
  discriminator at each composition root.
- **`payload_meta` is excluded from the mandated digest field set — the largest accepted gap in this
  design, ranked at or above `task_type`.** On today's edges `payload_meta` *is* the instruction:
  TUSS code, CID-10 and `valor_estimado_brl` on Helena's authorization path
  (`agents/helena/delegation.py:99,:102,:107-108`); the `prestador_id` from which Carolina's graph
  derives the idempotent CIB business key (`agents/carolina/delegation.py:211-218`, docstring
  `:207-209`); and `valor_pagamento_cents`, which drives Andre's faixa/alcada routing on the money
  path (`agents/andre/delegation.py:361`, read back at `:537-539`, comment `:349-354`). A signed
  envelope therefore attests who/where/when/how-much-budget while leaving *what is being asked*
  unbound — and `payload_hash` does not compensate, since `payload_ref` is `f"process://{task_id}"`
  on three of the four edges (`carolina:141`, `andre:204`, `andre:372`), making that field a
  restatement of `task_id`. Accepted only because the digest field set came from the program brief;
  §Perguntas abertas 1 is where a human closes or ratifies it.
- `task_type` is excluded from the mandated digest field set despite being routing-significant
  (§4.1's disclosed residual) — a real, named gap left for human resolution, not closed by this ADR.
  It ranks below `payload_meta` because it is at least cross-checked at admission against the
  target's Card (`dispatcher.py:388`), whereas `payload_meta` is cross-checked against nothing.
- The `deadline`-derived rotation grace window (§4.3) is **not** inherited from working code: no
  envelope in `src/` carries a `deadline` today, so the bound only exists once legs 2-4 implement
  §4.3's constraints and update all four builders. Until then the "Expiry bypass" defense in §4.5 is
  vacuous, and anti-replay rests entirely on durable-idempotency retention (§4.3.3) — a retention
  obligation this ADR creates and does not itself enforce.
- No implementation ships with this ADR; legs 2-4 carry real build cost (new envelope fields, a new
  `RejectionReason` member, a keyset injection seam, two new fail-closed composition-root gates, a
  transactional outbox) before any of the threat-table defenses in §4.5 are real.

## Perguntas abertas (humano decide)

> ✅ **AS SETE FORAM RESPONDIDAS PELO DONO EM 2026-08-12** — ver `## Decisoes do dono (2026-08-12)`
> (`:21`). O texto abaixo fica como estava, deliberadamente: e o enunciado original de cada pergunta,
> com a evidencia que a motivou, e e contra ele que as respostas devem ser lidas. Nao ha pergunta
> aberta remanescente nesta ADR; o que resta e a EMENDA do corpo de design (perna E1 do trem
> seguinte) e a revisao nomeada de Security/crypto, que a ratificacao do dono nao substitui.

1. **`payload_meta` in the digest — the first question, ahead of `task_type`.** §4.1 ranks
   `payload_meta`'s exclusion at or above `task_type`'s, on evidence: it carries the TUSS code,
   CID-10 and `valor_estimado_brl` (`agents/helena/delegation.py:99,:102,:107-108`), the
   `prestador_id` that Carolina's graph turns into the idempotent CIB business key
   (`agents/carolina/delegation.py:211-218`), and `valor_pagamento_cents`, which drives Andre's
   faixa/alcada routing on the money path (`agents/andre/delegation.py:361,:537-539`). Since
   `payload_ref` is `f"process://{task_id}"` on three of the four edges, `payload_hash` binds
   essentially nothing beyond a field the digest already carries. The **option** on the table — a
   recommendation, not a decision, because the mandated field set came from the program brief — is
   to add one field, `"payload_meta_hash": sha256(canonical(envelope.payload_meta))`, using §4.1's
   exact canonicalization. It binds the content while persisting and disclosing nothing, so the
   dispatcher's PHI-driven exclusion of `payload_meta` from audit `details`
   (`dispatcher.py:451-456`) is not an argument against it. **Decide: adopt `payload_meta_hash`,
   or record an explicit rationale for leaving the instruction content unsigned.**
2. **`task_type` in the digest** — include it (closing §4.1's second disclosed residual) or provide
   an explicit rationale for leaving it out beyond "it was not in the original mandate"?
3. **Rotation cadence and the max signature age** — how often should ordinary key rotation actually
   run (days? weeks?)? Note that §4.3 no longer lets `deadline` answer this alone: because no
   envelope carries a `deadline` today, §4.3.2 requires a verifier-side **maximum signature age** as
   an independent bound, and that value is a policy number a human sets. It also sets the floor for
   `a2a_idempotency` retention (§4.3.3), so it cannot be chosen without the DBA/retention owner.
4. **Key custody — and note the blast radius is four per-request builders, not two composition
   roots.** Who holds the signing key(s) day-to-day (same vault/KMS seam as
   `MAEZO_A2A_CARD_SIGNING_KEY`, or a separate secret), and is the keyset per-tenant or repo-wide
   (today's Card-signing key is repo-wide, single-key, no per-tenant scoping — should envelope
   signing diverge from that precedent, given a cross-tenant HMAC key leak would let an attacker
   forge envelopes across every tenant, arguably a sharper blast radius than a leaked Card key)?
   The custody problem is materially wider than Card signing's: a Card is signed once at composition,
   but envelopes are built **per request** in `build_auth_analysis_envelope`
   (`agents/helena/delegation.py:63`), `build_cred_dossier_envelope`
   (`agents/carolina/delegation.py:115`), `build_adequacao_dossier_envelope`
   (`agents/andre/delegation.py:169`) and `build_pagto_dossier_envelope`
   (`agents/andre/delegation.py:278`) — three of them reached from worker handlers
   (`tools/workers/credenciamento.py:637`, `tools/workers/adequacao.py:788`,
   `tools/workers/pagto.py:892`). Whatever custody mechanism is chosen must reach all four, in two
   agent packages plus the worker package that lazily imports them.
5. **Epoch-bump grace window** — should a `replay_epoch` bump triggered by a suspected-compromise
   incident ever tolerate a short grace window for the prior epoch (to avoid mass in-flight
   rejection), or must an incident-triggered bump always be zero-grace (§4.2 leaves this
   unspecified)?
6. **`MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES` naming/shape** — confirm the proposed opt-out env var
   name (§4.4) does not collide with any planned naming convention outside this ADR's visibility.
7. **`AGENT_RUNTIME_MODE` absent-default fix** — out of scope for this ADR (§Contexto point 2,
   §4.4), but flagged again here since it directly weakens this ADR's own fail-closed claim at the
   Helena→Rafael edge; a human owns the decision to change `AgentRuntimeSettings`'s default.

## Relacao com ADRs existentes

**ADR-0003 — extends, does not supersede.** ADR-0003 named the four anti-loop guards structurally;
this ADR adds a fifth, cryptographic guard (envelope-signature validity) operating alongside them,
not replacing any of the four.

**ADR-0007 — extends, does not supersede.** ADR-0007's non-repudiation clause ("Agent Card
assinado") is satisfied today by Card signing alone (§Contexto point 1). This ADR extends
non-repudiation from "who is Rafael" (the Card) to "exactly what was delegated to Rafael, unaltered"
(the envelope) — the same accountability goal, applied to the message rather than the identity.

**ADR-0015 — extends, does not supersede.** ADR-0015 formalized the envelope/dispatcher/registry
runtime as built in PR #17 and explicitly deferred a durable idempotency store to "Phase 1+"
(`0015-a2a-delegation-runtime.md:93-96`) — already delivered (`PostgresIdempotencyStore`) but only
conditionally wired (§4.6). This ADR adds the signature guard ADR-0015 never specified and tightens
the idempotency-wiring gap ADR-0015 flagged but left open.

**ADR-0033 — complementary, no dependency either direction.** ADR-0033 (Proposed, non-binding)
covers the OTHER half of T-G: verifiable **process/service** identity (a JWT or cert binding
`agent_id`/`tenant`/`agent_version` to the actual running process), explicitly orthogonal to Card
signing (`0033-tg-service-account-cert-identity.md:221-224`, "T-F... e ORTOGONAL a este ADR"). This
ADR's `key_id`/keyset design is independent of ADR-0033's eventual mechanism; if ADR-0033 is later
ratified, the process-identity token it designs COULD become a future source for envelope
`key_id` binding, but that is a future integration, not a dependency of this ADR.

**ADR-0037 — no interaction (§Contexto point 4).** One line, as required: A2A delegation is
intra-Maezo and never crosses the AMH boundary; this ADR touches nothing ADR-0037 governs.

## Supersedes

None. Amends/extends ADR-0003 (adds a fifth guard), ADR-0007 (extends non-repudiation from identity
to message), and ADR-0015 (specifies the signature guard it left unspecified; tightens the
idempotency-wiring gap it flagged). Does not supersede any ADR. Complementary to ADR-0033 (no
dependency either direction). Does not interact with ADR-0037.

## Historico de revisoes

| Rev | Data | Mudanca |
|---|---|---|
| r1 | 2026-08-11 | Redacao inicial (Onda 3 / Train C leg 1). Status Proposed. |
| r2 | 2026-08-11 | Revisao do gatekeeper de Train C leg 1 (zero-trust, pre-ratificacao) — dez achados fechados, sem mudanca de status. Substantivos: (M1) `payload_meta` reclassificado como residual de severidade igual ou maior que `task_type`, com a evidencia dos quatro edges; retirada a equivalencia falsa com a exclusao PHI do dispatcher; `payload_meta_hash` registrado como OPCAO para o humano (§4.1, §4.5, §Negativas, §Perguntas abertas 1). (M2) a janela de graca de rotacao nao se sustentava — `deadline` e `None` em todos os envelopes reais; §4.3 passa a exigir `deadline` nao-`None` em envelope ASSINADO, idade maxima de assinatura independente, e retencao de idempotencia dominando a validade da assinatura. (M3) gatilho de aposentadoria da excecao §8.4 corrigido: `ast.ClassDef`/`ast.Import`, nunca construcao (§Contexto 2, §4.6). (M4) verificacao fixada em `delegate` antes do ramo `dispatcher.py:279-281`; `_execute` removido dos locais sancionados. (m5) `default=str` proibido na canonicalizacao, com contraexemplo `datetime`. (m6) custodia de chave alcanca quatro builders por requisicao; armadilha do `dataclasses.replace` em `extend()`. (m7) `max_hops` e sim fornecido pelo remetente — razao corrigida. (m8) "primeira checagem" escopada ao dispatcher (o seam gate roda antes). (m9) tres citacoes corrigidas (`registry.py`, `_is_test_double_name`, design doc §8.4). (m10) novo §4.8 com obrigacoes de prova por linha da tabela de ameacas, no precedente §8.5 "the gate must prove it is doing work". |
| r3 | 2026-08-11 | Delta do mesmo gatekeeper sobre r2, dois residuos de uma linha. (R1) §4.4 enumerava tres call sites de `extend()` sob `tests/unit/a2a/`; a contagem real e 13 (`test_delegation.py` 11, `fakes.py` 1, `test_a2a_edge_live_pg.py` 1) — enumeracao trocada pelo total medido; a metade "zero callers em `src/`" segue confirmada. (R2) o primeiro bullet de §Positivas ainda dizia "the exact fields that matter (tenant, identity, payload reference, deadline, budget, chain)" — a frase que M1 retirou — e contradizia §Negativas; agora nomeia o conjunto MANDATADO e remete a §Negativas para o que ele nao cobre. Polimento: §4.7 dizia "ONLY built edge" seguido de quatro edges; reescrito para "built edges" distinguindo Helena→Rafael (montado, exercitado pela suite W3, sem caller em `src/`) das tres bordas worker→Carolina/Andre (alcancadas por codigo de worker real). |
| r4 | 2026-08-12 | **Status Proposed → Accepted, por instrucao do DONO do repositorio.** As sete Perguntas abertas foram respondidas na mesma data e transcritas na nova secao `## Decisoes do dono (2026-08-12)` (`:23`): adotar `payload_meta_hash` · incluir `task_type` no digest · rotacao ordinaria de 7 dias (donde a idade maxima de assinatura e o piso de retencao de `a2a_idempotency`) · custodia no mesmo seam vault/KMS do Card com keyset POR TENANT · janela curta de graca para o epoch anterior · `MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES` confirmado · mudar o default de `AgentRuntimeSettings` para fail-closed. **A tabela de aprovadores por PAPEL segue `_(pending)_` e nao foi tocada** — a ratificacao do dono registra a decisao e nao substitui a revisao nomeada de Security/crypto (R1); nenhum agente preencheu, nem pode preencher, aquelas celulas. **O corpo de design NAO foi emendado nesta revisao** e segue sendo o texto pre-decisao (§4.1 ainda sem `payload_meta_hash`/`task_type`; §4.2/§4.3/§4.4 ainda sem os 7 dias, a graca de epoch e o keyset por-tenant) — divulgado em bloco de aviso no topo, com a emenda designada a perna E1 do trem seguinte (`docs/prompts/13-08-26_signing-unlock.md`) sob cadeia zero-trust. Docs-only; zero linha de `src/`, `spec/`, `tests/` ou `deploy/` tocada. |
| r5 | 2026-08-12 | **Emenda do corpo de design (leg E1), sobre a base do dono (`d87d85e` + `feefb9c`, r4).** Por instrucao do dono (`## Decisoes do dono (2026-08-12)`), o corpo de design pre-decisao foi emendado para refletir as sete decisoes: §4.1 digest v2 adiciona `payload_meta_hash` (canonicalizacao inalterada: `sort_keys=True`, `separators=(",",":")`, sem `default=`, UTF-8, `deadline.astimezone(UTC).isoformat()`) e `task_type`, com o delta v1→v2 explicitado; §4.2 fixa a cadencia de rotacao ordinaria em 7 dias e autoriza uma janela de graca curta para o epoch anterior, com a semantica de graca-zero enunciada; §4.3 deriva a idade maxima de assinatura (7 dias, igual ao ciclo de rotacao) e o **piso de retencao de `a2a_idempotency` (>= 7 dias, computado)** — a escolha do valor real segue sendo do MZO-060/DBA, acima desse piso; §4.4 fixa a custodia de chave no mesmo seam vault/KMS de `MAEZO_A2A_CARD_SIGNING_KEY`, keyset POR TENANT alcancando os quatro builders por-requisicao mais o pacote worker, com caminho de migracao fail-closed e qualquer shim de compatibilidade explicitamente delimitado no tempo; e confirma o nome/forma de `MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES` (§Decisoes do dono, table row 6), reusando o `is_production_runtime_mode` ja compartilhado (essa confirmacao vive em §4.4, nao em §4.6 — §4.6 e sobre idempotencia duravel/outbox, um par de constraints nao relacionado). O bloco de aviso "aceita-mas-nao-emendada" no topo foi retirado e substituido por uma nota curta de que o corpo abaixo e o texto pos-decisao. **Nao emendou** `## Decisoes do dono`, o `**Status:**`, a tabela de aprovadores (segue `_(pending)_`) nem o texto da r4 — apenas os NUMEROS de auto-citacao dentro dessas secoes (`:23`, `:746`→cabecalho de Perguntas abertas, `:799`→fim da secao, `:800`→Relacao com ADRs, `:775-789`→Pergunta 4, `:652`→§4.8) foram re-derivados por CONTEUDO onde o deslocamento das insercoes acima os tornou incorretos, seguindo a mesma disciplina que o proprio commit `feefb9c` aplicou a si mesmo. §4.5 (tabela de ameacas), §4.8 (obrigacao 3) e §Negativas ainda descrevem `payload_meta`/`task_type` como nao-cobertos/excluidos — texto agora desatualizado pela emenda do §4.1, mas fora do escopo desta perna (nao tocado; ver relatorio do agente). Docs-only; zero linha de `src/`, `spec/`, `tests/` ou `deploy/` tocada. |
