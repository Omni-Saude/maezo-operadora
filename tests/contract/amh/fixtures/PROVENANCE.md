# Provenance — vendored AMH contract-test fixtures (MZO-010 / XRG-3)

**Source repo:** `Omni-Saude/amh-data-platform`
**`amh_commit_sha`:** `09a0a282e69f49aa9c6944b25afb35eee65fcc9c`
**Source path:** `schemas/contracts/maezo/v1/fixtures/`
**Evidence:** `XRG2-AMH-DEV-GHA-30991849241` (see `config/integrations/amh/contracts.lock.json`)

These 10 files (`README.md` + 9 `*.json`) are the AMH-published, **synthetic, non-PHI** conformance
test vectors for the canonical AMH×Maezo v1 contract. They are copied here byte-for-byte from the
AMH repository at the pinned `amh_commit_sha` so that the Maezo consumer contract tests can run
hermetically, with no network access.

## They are pinned bytes, not an editable contract source

The contract itself — the Avro schemas, the OpenAPI documents and the contract manifest — is
AMH-owned and is **never** copied into this repository (ADR-0037 XRD-04, "Proibições imutáveis" #4).
Maezo holds only the immutable pin in `config/integrations/amh/contracts.lock.json`. These fixtures
are vendored *test vectors*, and they are pinned exactly like every other artifact: each file has a
`sha256` recorded in the lock's `fixtures[]` block.

**Every byte in this directory is digest-gated by the test suite and by CI.**
`scripts/ci/verify_amh_contract_pin.py` recomputes the sha256 of each file listed in the lock's
`fixtures[]` and compares it to the pinned digest; it also refuses any file in this directory that
the lock does not list (this `PROVENANCE.md` is the sole exemption). A local edit to any fixture —
even one byte, even a trailing newline — makes the gate fail closed and the change cannot merge.
`tests/contract/amh/test_contract_pin.py` recomputes the same digests independently.

## Changing anything here

You cannot. A fixture changes only when AMH publishes a new contract manifest (a new XRG-2 event)
and a Maezo steward performs a new independent XRG-3 verification, re-vendoring the new bytes and
updating both the digests and the provenance block of the lock in the same change. Adjusting a
digest so a red gate turns green is a contract breach, not a fix.
