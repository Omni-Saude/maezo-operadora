# ⚠️ HUMAN-GATED PR — Phase-3 process-key allowlist (DO NOT auto-merge with W-E)

> **STOP.** This change makes six Phase-3 governance processes **start-enabled** on the engine.
> Under **ADR-0016** (fail-closed `process_key` allowlist) adding a key to the sanctioned
> universe **requires a separate, human-reviewed PR with CODEOWNERS approval**. It must **NOT**
> ride along with, or be auto-merged as part of, Wave E (W-E) or any automated/orchestrator merge.

## What this change does

Adds the six Phase-3 (operadora) process keys to the sanctioned universe so that
`start_compliance_process` (via `mcp_cibseven`) is structurally permitted to start them.

| process_key                | Processo                                                      |
| -------------------------- | ------------------------------------------------------------ |
| `SP-OP-FRAUDE-001`         | Investigação de Fraude / Cadeia de Custódia                  |
| `SP-OP-CRED-001`           | Credenciamento / Descredenciamento de Prestadores            |
| `SP-OP-PAGTO-001`          | Pagamentos por Alçada (high-value payment)                   |
| `SP-OP-PROGRAMA-001`       | Programas de Cuidado                                          |
| `SP-OP-INADIMPLENCIA-001`  | Suspensão / Rescisão por Inadimplência                       |
| `SP-OP-ADEQUACAO-001`      | Adequação Geográfica de Rede (RN 259)                        |

Files changed (already applied in this branch, ready to be cherry-picked into the gated PR):

- `src/maezo/tools/process_allowlist.py` — 6 keys added to `KNOWN_PROCESS_KEYS`
  (which equals `DEFAULT_ALLOWED_PROCESS_KEYS`, so they become default-allowed).
- `src/maezo/policies/process_allowlist.yaml` — same 6 keys added to the `amh` tenant overlay
  (kept in sync with the frozen default; `test_shipped_config_loads_and_matches_default` asserts
  the shipped YAML matches the default set).

## Why it MUST be a separate, human-reviewed PR

- **ADR-0016 (Consequências):** *"Adicionar um novo processo de governança exige PR (alteração de
  `KNOWN_PROCESS_KEYS`) — revisão humana obrigatória."* The allowlist is **fail-closed**: a key
  outside `KNOWN_PROCESS_KEYS` raises `ProcessKeyNotAllowedError` before any engine effect. The
  whole point of the control is that **widening the universe is a deliberate human act**, not a
  side effect of a feature wave.
- **CODEOWNERS:** `src/maezo/policies/` is owned by `@rodrigotaquino` — that owner must explicitly
  approve the policy/allowlist change.
- **Security boundary:** these keys gate the ability to *start governance processes* (incl.
  fraud investigation, payments by alçada, contract termination). Letting them become
  start-enabled via an automated merge would erode the audited, deliberate-widening guarantee
  that ADR-0016 exists to provide.

## Required reviewers / approvals

- [ ] CODEOWNER of `src/maezo/policies/` (`@rodrigotaquino`) — **required**
- [ ] Security review (process-key allowlist is a fail-closed security control, ADR-0016)
- [ ] Confirm each Phase-3 BPMN/DMN + autonomy matrix is itself ready (these keys only control
      *starting*; they do not validate the process content — that is the `validate-artifacts` /
      sign-off gate).

## Merge discipline

- Do **NOT** squash/auto-merge this into W-E.
- Open as its own PR titled e.g. `policy(ADR-0016): sanction Phase-3 process keys (human-gated)`.
- Land **only** after the CODEOWNER + security sign-off above.

## Verification run in this branch

- `pytest tests/unit/sec/test_process_allowlist.py` — passes (default set == known universe;
  shipped YAML matches default; fail-closed rejection of unknown/forged keys intact).
- `make validate-artifacts` — passes (note: the artifact validator covers
  `processes`/`policies/autonomy`/`agents`; it does **not** itself validate
  `process_allowlist.yaml`, so the `process_allowlist` unit tests are the structural gate for
  this file).
