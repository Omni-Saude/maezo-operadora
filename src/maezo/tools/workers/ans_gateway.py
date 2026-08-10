"""ANS gateway transport — protocol-issuance seam for SP-OP-ANS-SUBMIT-001 (T2.6-1, DL-0029a).

This module replaces the repo's canonical fabrication: `ans_submit.transmit_to_ans` used to mint
an official-looking ANS protocol from `sha256(time.time_ns())` (`ans_submit.py:220`) — a made-up
regulatory protocol number presented as if a real filing had happened. DL-0029 effect (a) makes
removing that path MANDATORY "independente da direção" (`docs/decisions-log.md`, DL-0029), and
`docs/design/T2.6-ans-submission-rescope.md` §2.A specifies the replacement: an explicit ANS
gateway behind an interface, following the repo's established transport-triple pattern
(`dmn_transport.DmnTransport`, `mcp_cibseven.transport.CibSevenTransport` — Protocol + real + fake).

The triple (design §2.A):

1. **`RefusingAnsGatewayTransport`** — the **PRODUCTION DEFAULT until real ANS credentials exist**.
   REFUSES to issue a protocol: raises `AnsGatewayUnavailableError` (fail-closed), so the process
   routes to a human/incident and NEVER fabricates a protocol. In production configuration the
   gateway therefore returns no protocol at all until `RealAnsGatewayTransport` is wired. This is
   the safe default — `resolve_ans_gateway(None)` selects it, so an *unwired* seam refuses rather
   than falling through to the mock (the mock is unreachable in prod by construction).

2. **`LabeledMockAnsGatewayTransport`** — DEV/TEST ONLY. Returns an UNMISTAKABLY-SYNTHETIC protocol
   `MOCK-ANS-NAO-VINCULATIVO-{business_key}` (no `ANSPROTO-` prefix — nothing that reads as an
   official ANS protocol), plus explicit `synthetic=True` / `vinculativo=False` flags. It is
   **deterministic by business key** (`ANSSUB-{tenant}-{report_type}-{competencia}`) — no `time_ns`,
   no randomness — which subsumes the T-H determinism requirement AND fixes the latent retry
   idempotency bug (BPMN `:461` "protocolo_ans determinista por business_key"): a retransmit for the
   same business key yields the identical synthetic protocol. It is ALSO the only transport that can
   produce a **NACK** (`requested_outcome=ANS_OUTCOME_NACK`) — the dev/test-only capability that
   makes BPMN `BE_SubmitNack`/`SUB_RetryEnvio` a live path rather than dead model. Production cannot
   reach it: `resolve_ans_gateway(None)` selects the refusing transport, and the two
   production-shaped transports raise before they ever read `requested_outcome`.

3. **`RealAnsGatewayTransport`** — the future real ANS webservice integration point. **Blocked
   external** (no ANS credentials/endpoint — Plan §7, issue #16; contract SP-OP-ANS-SUBMIT-001.md).
   `submit` fails-closed (raises `AnsGatewayUnavailableError`) until a real endpoint + credential
   exist. Blocked ≠ done — this is a documented stub, never a fabrication.

Error classification (ADR-0026 §5 / `tools/workers/base.py`): `AnsGatewayUnavailableError` carries
`.code`/`.message` and is NOT a `_HARNESS_CLASSIFIED` type, so `FunctionWorker.execute` reclassifies
it to `ValueError` → `failure(retries=0)` — an engine-guaranteed, human-visible incident, NEVER
engine-retried. Deliberately NOT a `RuntimeError` (which would be transient/engine-retried):
retrying cannot conjure ANS credentials, so a refusal must reach a human immediately (same
fail-closed reasoning as `DmnNoResultError`).

This module is a SEAM (like `dmn_transport`/`cibseven_engine`), not a BPMN worker handler — it
registers no workers and is threaded into `ans_submit`'s entry functions via the `**seams`
mechanism. It is therefore listed in the worker-purity test's `_INFRA_MODULES`, not scanned as a
domain handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)

#: Unmistakably-synthetic protocol prefix for the labeled mock (design §2.A) — NOT `ANSPROTO-`
#: (nothing that reads as an official ANS protocol). Loud enough that a synthetic protocol can
#: never be mistaken for a binding regulatory one.
MOCK_ANS_PROTOCOL_PREFIX = "MOCK-ANS-NAO-VINCULATIVO-"

#: The two `AnsProtocol.status_envio` values this seam knows how to produce. `ENVIADO` is the
#: accepted filing; `NACK` is the ANS refusal that BPMN `BE_SubmitNack` catches as
#: `ERR_ANS_PROTOCOLO_NACK` (contract SP-OP-ANS-SUBMIT-001.md §Codigos de erro). Any OTHER status
#: is rejected fail-closed by `ans_submit.transmit_to_ans` — never silently treated as a filing.
ANS_OUTCOME_ENVIADO = "enviado"
ANS_OUTCOME_NACK = "nack"

#: The labeled mock's NACK reason. Deliberately NOT an ANS reason code/phrase — the real ANS
#: NACK vocabulary is an unresolved external dependency (contract §Pendencias: "Semantica precisa
#: de ACK/NACK da ANS ... AWS-blocked, issue #16"), so inventing one here would be a fabrication.
#: This string is self-labelling as a mock artifact, exactly like `MOCK_ANS_PROTOCOL_PREFIX`.
MOCK_ANS_NACK_MOTIVO = "MOCK-ANS-NACK-NAO-VINCULATIVO"


class AnsGatewayUnavailableError(Exception):
    """Fail-closed refusal to issue an ANS protocol — the gateway has no real transport/credentials.

    Raised by `RefusingAnsGatewayTransport` (prod default until creds exist) and
    `RealAnsGatewayTransport` (creds-blocked stub). NEVER a fabricated protocol.

    Carries `.code`/`.message` (this package's coded-exception convention — mirrors
    `DmnNoResultError`/`PagtoError`) so `FunctionWorker.execute` (`tools/workers/base.py:284-294`)
    reclassifies it into `ValueError`, which the harness maps to `failure(retries=0)` — an
    engine-guaranteed, human-visible incident. Deliberately NOT a `RuntimeError`/`OSError` (the
    transient, engine-retried family): retrying cannot provision ANS credentials, so a refusal is
    a DETERMINISTIC condition that must reach a human immediately, never be retried.
    """

    def __init__(self, detail: str = "") -> None:
        self.code = "ERR_ANS_GATEWAY_UNAVAILABLE"
        self.message = detail or (
            "ANS gateway refuses to issue a protocol — no real ANS transport/credentials configured "
            "(fail-closed; never fabricates a protocol)"
        )
        super().__init__(f"{self.code}: {self.message}")


@dataclass(frozen=True, slots=True)
class AnsProtocol:
    """The result of an ANS protocol issuance.

    `synthetic`/`vinculativo` make a non-binding (mock) protocol self-describing at the process
    boundary — a real ANS protocol would be `synthetic=False, vinculativo=True`. `RefusingAns`/
    `RealAns` never construct one (they raise), so an `AnsProtocol` only ever exists for a genuinely
    issued (or explicitly-synthetic) filing.

    `status_envio` is either `ANS_OUTCOME_ENVIADO` (accepted) or `ANS_OUTCOME_NACK` (refused by
    ANS). `nack_motivo` is populated ONLY on a NACK and carries the refusal reason through to the
    `ERR_ANS_PROTOCOLO_NACK` error MESSAGE — `WorkerBpmnError`'s variables channel (t9-nack-vars)
    admits only allowlisted, bounded tokens, so free-text prose like a refusal reason still travels
    in the message and nowhere else (see `ans_submit.transmit_to_ans`). On the RETRANSMISSION leg
    `nack_motivo` IS a process variable (contract SP-OP-ANS-SUBMIT-001.md:75 "preenchido apenas em
    retransmissao"), written there by `ans_submit.retransmit_to_ans`'s normal completion.
    """

    protocolo_ans: str
    status_envio: str
    synthetic: bool
    vinculativo: bool
    nack_motivo: str = ""


@runtime_checkable
class AnsGatewayTransport(Protocol):
    """ANS protocol-issuance seam (design §2.A) — mirrors the `DmnTransport`/`CibSevenTransport`
    Protocol-shape. One method, injectable for unit tests (`LabeledMockAnsGatewayTransport`) and
    fail-closed by default in production (`RefusingAnsGatewayTransport`).

    `submit` is SYNCHRONOUS (unlike the DMN/engine transports): there is no real network today
    (the real endpoint is creds-blocked), and the worker boundary that calls it
    (`ans_submit.transmit_to_ans`) is itself synchronous — no `asyncio` bridge is needed until
    `RealAnsGatewayTransport` is wired to an actual webservice.

    `requested_outcome` — DEV/TEST ONLY, and the ONLY implementation that may honor it is
    `LabeledMockAnsGatewayTransport`. It exists so a NACK (`ANS_OUTCOME_NACK`) is DETERMINISTICALLY
    INDUCIBLE under test, which is what makes BPMN `BE_SubmitNack` -> `SUB_RetryEnvio` a live path
    instead of dead model. A REAL transport MUST IGNORE it: the ANS response is the only authority
    on whether a filing was accepted, and a caller that could *ask* for an outcome could fabricate
    one. Both production-shaped implementations here (`RefusingAnsGatewayTransport`,
    `RealAnsGatewayTransport`) raise unconditionally BEFORE reading it, so no production-reachable
    code path can be steered by it — see `resolve_ans_gateway` for why the mock is unreachable in
    production at all.
    """

    def submit(
        self,
        *,
        business_key: str,
        report_type: str,
        competencia: str,
        dataset_ref: str,
        revisor_id: str,
        requested_outcome: str = "",
    ) -> AnsProtocol: ...


class RefusingAnsGatewayTransport:
    """PRODUCTION DEFAULT (design §2.A): REFUSES to issue a protocol — no ANS credentials exist yet.

    `submit` raises `AnsGatewayUnavailableError` (fail-closed) → the harness routes it to a
    human-visible incident (`retries=0`). It NEVER returns a protocol, so the production path
    genuinely issues nothing until `RealAnsGatewayTransport` is wired — the anti-fabrication
    guarantee. This is what `resolve_ans_gateway(None)` selects, so an unwired seam refuses rather
    than fabricating.

    `requested_outcome` is accepted for Protocol conformance and DELIBERATELY IGNORED — the refusal
    is unconditional and happens before it is read, so a NACK (or any other outcome) can never be
    induced on the production default.
    """

    def submit(
        self,
        *,
        business_key: str,
        report_type: str,
        competencia: str,
        dataset_ref: str,
        revisor_id: str,
        requested_outcome: str = "",
    ) -> AnsProtocol:
        del requested_outcome  # IGNORED BY CONTRACT — production refuses regardless (see docstring)
        logger.warning(
            "ans_gateway.refusing.no_credentials",
            business_key=business_key,
            report_type=report_type,
            competencia=competencia,
        )
        raise AnsGatewayUnavailableError(
            "RefusingAnsGatewayTransport is the production default until real ANS credentials/"
            "endpoint are provisioned — it fails closed and issues no protocol (never fabricates); "
            f"business_key={business_key!r}"
        )


class LabeledMockAnsGatewayTransport:
    """DEV/TEST ONLY (design §2.A): returns a DETERMINISTIC, UNMISTAKABLY-SYNTHETIC protocol.

    `MOCK-ANS-NAO-VINCULATIVO-{business_key}` with `synthetic=True`/`vinculativo=False` — no
    `ANSPROTO-` prefix, so it can never be mistaken for a real ANS protocol. Deterministic purely
    by `business_key` (no `time_ns`, no randomness), so two `submit` calls for the same business key
    return the identical protocol — this is both the T-H determinism requirement and the fix for the
    latent retry idempotency bug (BPMN `:461`).

    NEVER selected by production wiring — `resolve_ans_gateway(None)` selects the refusing transport,
    so the mock is reachable only by an EXPLICIT dev/test injection (mirrors `FakeDmnTransport`'s
    prod-fenced posture).

    **NACK capability (the ONE implementation that has it).** `requested_outcome=ANS_OUTCOME_NACK`
    makes `submit` return a refused filing (`status_envio="nack"`, `nack_motivo=
    MOCK_ANS_NACK_MOTIVO`) instead of an accepted one, which is how `ans_submit.transmit_to_ans`
    is driven to raise `ERR_ANS_PROTOCOLO_NACK` and light up BPMN `BE_SubmitNack` -> `SUB_RetryEnvio`
    under test. Every other value (including the default `""`) yields the accepted outcome, so this
    is purely additive to the pre-existing behavior. The NACK is still an UNMISTAKABLY-SYNTHETIC
    result: the protocol keeps the `MOCK-ANS-NAO-VINCULATIVO-` prefix and `synthetic=True`/
    `vinculativo=False`, and the motivo is a self-labelling mock string, never an ANS reason code.
    """

    def submit(
        self,
        *,
        business_key: str,
        report_type: str,
        competencia: str,
        dataset_ref: str,
        revisor_id: str,
        requested_outcome: str = "",
    ) -> AnsProtocol:
        nacked = requested_outcome.strip().lower() == ANS_OUTCOME_NACK
        logger.info(
            "ans_gateway.labeled_mock.issue_synthetic",
            business_key=business_key,
            report_type=report_type,
            competencia=competencia,
            revisor_id=revisor_id,
            outcome=ANS_OUTCOME_NACK if nacked else ANS_OUTCOME_ENVIADO,
        )
        return AnsProtocol(
            protocolo_ans=f"{MOCK_ANS_PROTOCOL_PREFIX}{business_key}",
            status_envio=ANS_OUTCOME_NACK if nacked else ANS_OUTCOME_ENVIADO,
            synthetic=True,
            vinculativo=False,
            nack_motivo=MOCK_ANS_NACK_MOTIVO if nacked else "",
        )


class RealAnsGatewayTransport:
    """Real ANS webservice integration point (design §2.A) — BLOCKED EXTERNAL, creds-blocked stub.

    No ANS credentials/endpoint exist (Plan §7, issue #16; contract SP-OP-ANS-SUBMIT-001.md). Until
    a real endpoint + credential are provisioned, `submit` fails closed (raises
    `AnsGatewayUnavailableError`) — a documented future integration point, NEVER a fabrication.
    Blocked ≠ done: this class exists so the real transport has a named home, not so it can ship.

    `requested_outcome` is accepted for Protocol conformance and MUST STAY IGNORED even once a real
    endpoint is wired: only the ANS response may decide `enviado` vs `nack`. Honoring a caller's
    requested outcome here would let the process fabricate a filing result — the exact failure mode
    this whole seam exists to prevent (DL-0029).
    """

    def __init__(self, base_url: str | None = None, *, auth_token: str | None = None) -> None:
        # Accepted for the eventual real wiring; unused today (no endpoint is contacted).
        self._base_url = base_url
        self._auth_token = auth_token

    def submit(
        self,
        *,
        business_key: str,
        report_type: str,
        competencia: str,
        dataset_ref: str,
        revisor_id: str,
        requested_outcome: str = "",
    ) -> AnsProtocol:
        del requested_outcome  # IGNORED BY CONTRACT — only ANS decides the outcome (see docstring)
        raise AnsGatewayUnavailableError(
            "RealAnsGatewayTransport is not provisioned — the real ANS webservice endpoint/"
            "credentials do not exist yet (Plan §7, issue #16). Blocked ≠ done; this is the "
            f"future integration point, never a fabricated protocol; business_key={business_key!r}"
        )


def resolve_ans_gateway(ans_gateway: AnsGatewayTransport | None) -> AnsGatewayTransport:
    """Fail-closed default selection (design §2.A): an unwired seam resolves to the REFUSING
    transport (the production default), NEVER the mock.

    This is the load-bearing "the mock is unreachable in prod by construction" guarantee: the
    production composition root (`register_all_workers` → `register_ans_submit_workers`) injects no
    `ans_gateway`, so `submit_entry` receives `None` and resolves it HERE to
    `RefusingAnsGatewayTransport` — production refuses. Dev/test/integration explicitly inject
    `LabeledMockAnsGatewayTransport`.
    """
    if ans_gateway is None:
        return RefusingAnsGatewayTransport()
    return ans_gateway
