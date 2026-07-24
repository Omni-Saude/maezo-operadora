# ADR-0032: ADR-0015's "runtime completo" claim describes the v1 donor's PR #17, not v2 — amends ADR-0015 [T3.4]

**Status:** Accepted (ratificado pelo orquestrador 2026-07-24, autoridade autônoma vigente; DL-0032) · **Data:** 2026-07-24 · **Area:** Comunicacao / A2A

Owner: docs-agent (R2). Scope: status-correction only — no `src/`/`tests/`/`spec/`/evidence-ledger
change. Does not edit ADR-0015's original text (history is history); this is a new, separate ADR
that amends it, per the convention below.

## Contexto

ADR-0015 ("Runtime de delegacao A2A: Agent Card registry, envelope e anti-loop estrutural",
**Status: Accepted**, Data: 2026-06-13) states, at `docs/adr/0015-a2a-delegation-runtime.md:14`:

> "A PR #17 implementou o runtime completo em `src/maezo/a2a/`. Este ADR formaliza as decisões
> concretas feitas nessa implementação."

...and its `## Decisao` section (lines 34-97) describes, as if present in this repo, an
`AgentCardRegistry` (`registry.py`), an immutable `DelegationEnvelope` with four structural
anti-loop guards (`delegation.py`), a `DelegationDispatcher` as "the single entry point for all
delegation" (`dispatcher.py`), and three Kafka fact topics validated by `validate-artifacts`
(`facts.py`).

**This describes the v1 donor repo's (`Maezo-Healthcare-Plan`) implementation, not this repo
(v2)'s.** Re-verified against this tree 2026-07-24 (`main` = `1318e1b`, matching the branch point
of this correction):

- **File inventory.** `src/maezo/a2a/` contains exactly four files: `__init__.py`, `anti_loop.py`,
  `card.py`, `registry.py`. `git log --follow --oneline -- src/maezo/a2a/` shows a single commit
  ever touching this directory: `7e76006` ("feat(M5): Agent Framework + A2A + MCP Servers").
  `git log --all --diff-filter=A --name-only -- '**/dispatcher.py' '**/facts.py' '**/delegation.py'
  '**/idempotency.py'` returns **zero hits across every branch in this repo's history** — these
  four files ADR-0015 §Decisao describes have never existed in this repo.
- **Zero production call sites.** The classes that do exist — `AgentCard` (`card.py`), `A2ARegistry`
  (`registry.py` — note: not `AgentCardRegistry`, the name ADR-0015:71 uses for the injected
  dependency), and `AntiLoopGuard`/`CyclicDelegationError`/`MaxDepthExceededError`
  (`anti_loop.py`) — have no call site anywhere in `src/` outside `src/maezo/a2a/` itself. The only
  matches for `AgentCard`/`A2ARegistry`/`AntiLoopGuard` outside the package are disclosure comments
  in three agent graphs' module docstrings: `src/maezo/agents/rafael/graph.py:48-50` ("No
  cross-agent A2A delegation ... is wired in this build: v2's `a2a/` package has no
  `DelegationEnvelope`/`DelegationDispatcher` yet (only `AgentCard`/`A2ARegistry`/`AntiLoopGuard`
  exist)"), `src/maezo/agents/beatriz/graph.py:89-90`, and `src/maezo/agents/marina/graph.py:115`
  — each independently disclosing the identical gap.
- **`PLANS.md:43`** (ground-truth corrected 2026-07-23, commit `723dc7f`) already tracks this:
  "M5 Agent Framework + MCP + A2A | ◑ | 10 grafos de agente reais (B6 fechado). **A2A dispatcher +
  card-signing NÃO feitos** (backlog P3)." `PLANS.md:58` places the dispatcher port in Sprint P3,
  alongside T3.2/T3.3/T3.4, "quase não iniciado."
- **`docs/decisions-log.md:32` (DL-0009, 2026-06-13, contemporaneous with ADR-0015's authoring)**
  already records what ADR-0015 was actually grounded in: "ADR-0015 formaliza runtime A2A **de
  PR #17** (complementa ADR-0003)" — i.e. the ADR formalizes PR #17's decisions without
  distinguishing donor from v2. v2's own `a2a/` package traces to a different, later, thinner
  commit (`7e76006`, "M5"), not to a "PR #17" in this repo's history.

**Consequences of the gap:**

1. **T-F (A2A delegation audit)** — `docs/design/audit-emit-path-wiring.md:387-388`: "Agent-runtime
   effect chokepoint. Audit process-start + A2A delegation (the second effect surface, §2.1 site 5),
   reusing `emit_once`" (restated `:627-628`) — is **BLOCKED-ON this substrate**: there is no
   dispatcher to instrument, because there is no dispatcher.
2. **T-G (`docs/design/audit-emit-path-wiring.md:389-391`, restated `:629`: "Identity hardening
   (ADR-0007 signed identity). Service-account cert / signed Agent Card for `agent_id`... Orthogonal,
   larger")** splits into two independently-gapped halves:
   - The **signed-Agent-Card half is BLOCKED-ON this same substrate** — `docs/reports/
     T2.4-a2a-agent-card-signing-gap.md` (2026-07-17) reached the identical conclusion: v2's
     `AgentCard` (`card.py`) is "a plain, non-frozen class" with no `signature` field, no
     `CardSigner`, no verifying chokepoint, and restoring it "requires... a dispatcher... [that]
     does not exist in v2 yet" (report §"Why this is not trivially self-contained", item 4).
   - The **service-account/cert identity half is a DESIGN-GAP in its own right**, independent of
     the dispatcher: ADR-0007 states the requirement in one clause —
     `docs/adr/0007-agent-identity-audit-non-repudiation.md:10`: "Identidade de servico por
     agente+tenant (service account + certificado; Agent Card assinado)" — with no elaboration
     anywhere of the issuance mechanism (who mints the cert? per-tenant? per-agent-version?
     rotation?) or the verification chokepoint (where is it checked — at dispatch, at audit-emit,
     at the PEP?). This needs its own ADR; it is out of scope here.

## Decisao

This ADR does not change ADR-0015's original text — the runtime design it describes (Agent Card
derivation from `AgentDefinition`, tenant-scoped registry, structural anti-loop guards on an
immutable envelope, an idempotent single-entry-point dispatcher, PHI-free Kafka facts) remains a
valid *target design* and reference for whichever task eventually ports it. What this ADR corrects
is the **status claim**: read ADR-0015's "A PR #17 implementou o runtime completo em
`src/maezo/a2a/`" as describing the **donor repo's** PR #17, not this repo's state, at every point
in this repo's history up to and including `main = 1318e1b` (2026-07-24). Concretely:

- ADR-0015 §"Agent Card registry", §"Envelope de delegação", §"Dispatcher", and §"Fatos Kafka"
  describe `registry.py`/`delegation.py`/`dispatcher.py`/`facts.py` as if all four exist in v2.
  Only `registry.py` (plus `card.py` and `anti_loop.py`, not named as separate files in ADR-0015)
  exist here; `delegation.py`, `dispatcher.py`, and `facts.py` do not.
- The classes that do exist in v2 (`AgentCard`, `A2ARegistry`, `AntiLoopGuard`) are unwired: zero
  production call sites, confirmed above.
- Porting the dispatcher/envelope/facts runtime (byte-faithful or adapted from the donor) remains
  tracked as backlog P3 (`PLANS.md:43,58`) and is a hard prerequisite for T-F and for the
  signed-Agent-Card half of T-G.
- The service-account/cert identity half of T-G is explicitly **not** resolved by this amendment;
  it requires its own ADR (issuance mechanism + verification chokepoint), since ADR-0007
  (`:10`) states the requirement in a single clause with no further elaboration anywhere in this
  repo.

## Consequencias

**Positivas:**
- Documentation now matches the tree: a reader of ADR-0015 who cross-references this amendment will
  not conclude the A2A dispatcher exists or is wired into any agent.
- T-F and the signed-Card half of T-G are now traceably BLOCKED-ON the dispatcher port (P3 backlog)
  instead of silently assumed available.
- The service-account/cert identity gap in ADR-0007 is now flagged as needing its own ADR, rather
  than remaining an unexamined single clause.

**Negativas (aceitas):**
- ADR-0015 remains formally `Status: Accepted` in its own file (this amendment does not flip it to
  Superseded, since the design itself is not rejected, only its "already implemented" framing) —
  any reader of `docs/adr/0015-a2a-delegation-runtime.md` in isolation, without also finding this
  ADR, can still be misled. Mitigated by: this ADR's entry in `docs/adr/README.md`'s table
  (adjacent to 0015) and in `docs/decisions-log.md`.
- No new implementation lands here — T-F, T-G's signed-Card half, and the ADR-0007 identity-design
  gap all remain open; this ADR only makes their blocked status honest and traceable.

## Convencao seguida

Per `docs/adr/README.md:8` ("ADR aceito so muda por novo ADR com `Supersedes`") and the precedent
of ADR-0027 (`docs/adr/0027-audit-transport-postgres-first.md`, which amends ADR-0007's Kafka-
transport clause via a **new** ADR, without editing ADR-0007's file, and records `Supersedes:
"Amends ADR-0007 ... does not supersede it"`): this repo's convention for correcting an *Accepted*
ADR is a new ADR that amends it, not an in-place edit. (The in-place `> Amended ...` blockquote
seen in `docs/adr/0030-worker-error-semantics-bpmn-boundary.md:10-22` is a different case — ADR-0030
was still `Status: Proposed`, pre-ratification, when it was amended; it is not a precedent for
editing an already-`Accepted` ADR.) This ADR follows the ADR-0027 pattern accordingly: ADR-0015's
file is untouched; this document is the correction of record.

## Supersedes

Amends ADR-0015 (status/implementation claim only — the `## Decisao` design itself is not
rejected and remains the target for the pending A2A-dispatcher port). Does not supersede it. No
other ADR affected.
