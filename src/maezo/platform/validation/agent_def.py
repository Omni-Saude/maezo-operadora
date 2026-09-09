"""Agent-definition artifact validation (`<agents-root>/<id>/agent.yaml`).

Reuses `maezo.agents.AgentLoader` / `AgentDefinition` (T0.3) as the schema
authority instead of re-implementing a parallel schema here — the runtime and
this CI gate must agree on what a valid `agent.yaml` looks like.

Additionally enforces what several `agent.yaml` files already document as a
hard requirement (see e.g. `spec/agents/andre/agent.yaml`'s `tools:` comment:
"validate-artifacts exige o dir mcp_<server>"): every `mcp-<server>.<action>`
tool id in an agent's allowlist must reference an MCP server that actually
exists in the repo (`src/maezo/tools/mcp_<server>/`) — an agent declaring a
tool for a server that was never built is a silent contract violation the
old stub never caught.

Since HEL-12 it ALSO enforces the spec-first rule the repo already states for
every other artifact family: every process key an agent declares
(`process_keys`, and `escalation.process` — the two places
`spec/agents/_template/agent.yaml` referenced the phantom
`SP-OP-TEMPLATE-001`) must exist BOTH as `docs/processes/contracts/<key>.md`
AND in `maezo.tools.process_allowlist.KNOWN_PROCESS_KEYS`. Before that cross-check
nothing validated process keys at artifact time — the only fence was
`gateway/effect_pep.py::EffectPolicy.allows_process_key`, which denies at RUNTIME
(fail-closed, but discovered on the first real start).

BOTH conditions are required, and they are not the same set: `SP-OP-ANS-CRON-001`
HAS a contract but is deliberately absent from `KNOWN_PROCESS_KEYS` (ADR-0016/AF-05
— timer-started, never agent-started), so declaring it in an `agent.yaml` is an error.

The `_template` directory (the contract template itself, with placeholder
values) is intentionally skipped by `validate_dir` — it is not a real agent, and
its `SP-OP-<AGENT>-001` token is a placeholder BY DESIGN. The derived agent is
covered instead: an UNSUBSTITUTED placeholder in any non-template `agent.yaml`
gets its own explicit error, so the skip can never be inherited by accident.

Since WP A2A-YAML-DISCLOSURE (CC-02 residual / NEW-B2 / NEW-B3 / NEW-B4) it ALSO enforces the
SHAPE of `a2a.handler_status`: five `agent.yaml` files described the A2A inbound-handler state
only as free-text prose above the `a2a:` block — some of it stale (denying a handler that had
since been built and wired), some of it simply absent — and no gate ever read a code comment.
A non-empty `a2a:` block must now carry `handler_status` from a closed vocabulary (`registrado` /
`pronto_sem_registro` / `ausente`), and, whenever a handler is disclosed to exist, the exact
`<module>::make_<id>_handler` symbol, which this validator resolves via `importlib` the same way
it already resolves `tools` entries against real `mcp_<server>/` directories. This validator only
enforces that SHAPE (closed vocabulary + self-consistent symbol); the deeper cross-check — that
the declared value actually matches `agents/<id>/delegation.py` and the composition root's live
registration — is `tests/unit/a2a/test_agent_card_handlers_parity.py`'s job, the same division of
labor this module already draws between "the process key has a contract" (here) and "the DMN
actually reaches it at runtime" (the effect PEP).
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import yaml

from maezo.agents import AgentLoader
from maezo.tools.process_allowlist import KNOWN_PROCESS_KEYS

from .result import Report

TEMPLATE_DIR_NAME = "_template"

#: `docs/processes/contracts/` relative to the repo root, resolved from this module's own
#: location (`<repo>/src/maezo/platform/validation/agent_def.py` -> 4 levels up is `<repo>`).
#: `validate_dir`/`validate_file` accept an explicit `contracts_root` override; this is only the
#: default so the existing 3-argument call sites keep working.
_CONTRACTS_SUBPATH = ("docs", "processes", "contracts")

#: An `agent.yaml` derived from `_template` that forgot to substitute the token. Reported with
#: its OWN message rather than the generic "no such contract" one, because the fix is different.
_PLACEHOLDER_MARKERS = ("<", ">")

#: The closed vocabulary CC-02's structured `a2a.handler_status` field is allowed to take
#: (WP A2A-YAML-DISCLOSURE). pt-BR tokens, matching the rest of this repo's spec vocabulary
#: (`triggers`, `dmn` outcomes, ...): `registrado` (a `make_<id>_handler` exists AND is a key of
#: a `handlers={...}` registration in `runtime.agent_runtime.a2a_composition`), `pronto_sem_registro`
#: (the handler exists but no composition root registers it — an OWNER decision, never a code
#: bug), `ausente` (no importable `make_<id>_handler` at all).
_HANDLER_STATUS_VALUES = frozenset({"registrado", "pronto_sem_registro", "ausente"})


def default_contracts_root() -> Path:
    """`<repo>/docs/processes/contracts` — the default universe of process contracts."""
    return Path(__file__).resolve().parents[4].joinpath(*_CONTRACTS_SUBPATH)


def known_contract_keys(contracts_root: Path) -> frozenset[str]:
    """Process keys that have a contract document on disk (`SP-OP-*.md` stems).

    A missing directory yields the EMPTY set, so every declared key then fails — fail-closed by
    construction. `validate_dir` additionally reports the missing root once, explicitly, so the
    cause is never hidden behind N identical per-agent errors.
    """
    if not contracts_root.is_dir():
        return frozenset()
    return frozenset(p.stem for p in contracts_root.glob("SP-OP-*.md"))


def known_mcp_servers(tools_root: Path) -> frozenset[str]:
    """MCP server names discoverable at `src/maezo/tools/mcp_<name>/`."""
    if not tools_root.exists():
        return frozenset()
    return frozenset(
        d.name[len("mcp_") :] for d in tools_root.iterdir() if d.is_dir() and d.name.startswith("mcp_")
    )


def _tool_server(tool: str) -> str | None:
    """`mcp-fhir.read_patient` -> `fhir`. None if `tool` isn't `mcp-<server>.<action>`."""
    if not tool.startswith("mcp-"):
        return None
    head = tool.split(".", 1)[0]  # mcp-fhir
    return head[len("mcp-") :] or None


def _validate_process_key(
    path: Path, key: str, field: str, contract_keys: frozenset[str], report: Report
) -> None:
    """One process key, cross-checked against BOTH the contracts on disk and the allowlist."""
    if any(marker in key for marker in _PLACEHOLDER_MARKERS):
        report.error(
            path,
            f"{field} '{key}' is an UNSUBSTITUTED placeholder inherited from "
            f"spec/agents/{TEMPLATE_DIR_NAME}/agent.yaml — replace it with this agent's real "
            "process key (which must exist in docs/processes/contracts/ and in KNOWN_PROCESS_KEYS)",
        )
        return
    if key not in contract_keys:
        report.error(
            path,
            f"{field} '{key}' has no process contract "
            f"(expected docs/processes/contracts/{key}.md to exist — spec-first: the contract "
            "comes before the code)",
        )
    if key not in KNOWN_PROCESS_KEYS:
        report.error(
            path,
            f"{field} '{key}' is not in KNOWN_PROCESS_KEYS "
            "(src/maezo/tools/process_allowlist.py, ADR-0016) — the effect PEP would deny every "
            "start of it at runtime",
        )


def _validate_a2a_handler_disclosure(path: Path, agent_id: str, a2a: dict[str, Any], report: Report) -> None:
    """CC-02/NEW-B2/NEW-B3/NEW-B4: `a2a.handler_status` must be a closed-vocabulary, self-
    consistent structured field rather than the free-text prose that used to deny, omit, or go
    stale on this exact fact. A non-empty `a2a:` block is REQUIRED to carry it; an agent that
    declares no `a2a:` block at all has nothing to disclose (this function is a no-op then).

    Only the SHAPE is enforced here (closed vocabulary, `handler_symbol` present iff a handler is
    disclosed, and the symbol names THIS agent's own module/factory pair). Whether the disclosed
    value matches the real `agents/<id>/delegation.py` + composition-root registration state is
    the parity fence's job (`tests/unit/a2a/test_agent_card_handlers_parity.py`), the same way
    `_validate_process_key` below checks a key has a contract without re-deriving whether the DMN
    that guards it is reachable.
    """
    if not a2a:
        return
    status = a2a.get("handler_status")
    if status not in _HANDLER_STATUS_VALUES:
        report.error(
            path,
            f"a2a.handler_status must be one of {sorted(_HANDLER_STATUS_VALUES)}, got {status!r} "
            "(CC-02: the A2A inbound-handler state must be a machine-checked field, not prose)",
        )
        return

    symbol = a2a.get("handler_symbol")
    if status == "ausente":
        if symbol:
            report.error(
                path,
                f"a2a.handler_status is 'ausente' but a2a.handler_symbol={symbol!r} is also set — "
                "an absent handler cannot also name one (claim-and-deny contradiction)",
            )
        return

    # registrado | pronto_sem_registro: a handler is disclosed to exist, so it must be named,
    # named correctly (THIS agent's own delegation module/factory — copy-paste from another
    # agent's block would otherwise pass silently), and actually resolve.
    if not symbol:
        report.error(
            path,
            f"a2a.handler_status is {status!r} but a2a.handler_symbol is missing — name the "
            f"`<module>::make_{agent_id}_handler` this status is disclosing",
        )
        return

    expected_symbol = f"maezo.agents.{agent_id}.delegation::make_{agent_id}_handler"
    if symbol != expected_symbol:
        report.error(
            path,
            f"a2a.handler_symbol must be {expected_symbol!r} for agent {agent_id!r}, got {symbol!r}",
        )
        return

    module_name, _, attr_name = str(symbol).partition("::")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        report.error(path, f"a2a.handler_symbol {symbol!r} names a module that does not import: {exc}")
        return
    if not callable(getattr(module, attr_name, None)):
        report.error(path, f"a2a.handler_symbol {symbol!r} does not resolve to a callable attribute")


def validate_file(
    path: Path,
    mcp_servers: frozenset[str],
    report: Report,
    contract_keys: frozenset[str] | None = None,
) -> None:
    """Validate one `agent.yaml` file, accumulating findings into `report`.

    `contract_keys` defaults to the keys discovered under `default_contracts_root()`.
    """
    try:
        definition = AgentLoader().load(path)
    except FileNotFoundError as exc:
        report.error(path, str(exc))
        return
    except ValueError as exc:
        report.error(path, str(exc))
        return
    except yaml.YAMLError as exc:
        report.error(path, f"malformed YAML: {exc}")
        return

    if not definition.tools:
        report.error(path, "'tools' must be a non-empty allowlist")

    for tool in definition.tools:
        server = _tool_server(tool)
        if server is None:
            report.error(path, f"tool '{tool}' does not follow the mcp-<server>.<action> pattern")
            continue
        if server not in mcp_servers:
            report.error(
                path,
                f"tool '{tool}' references an unknown MCP server "
                f"(expected src/maezo/tools/mcp_{server}/ to exist)",
            )

    # HEL-12 spec-first fence. ADDITIVE: it validates the keys an agent DECLARES. An agent that
    # declares NO `process_keys` at all is not caught here — that is the separate, OPEN,
    # owner-decision gap ANDRE-PROCESS-KEYS (andre grants `mcp-cibseven.start_process` for
    # SP-OP-PAGTO-001 with no `process_keys`, so the effect PEP denies it), and granting a key
    # widens effect authority, which is not a validator's call to make.
    keys = contract_keys if contract_keys is not None else known_contract_keys(default_contracts_root())
    for key in definition.process_keys:
        _validate_process_key(path, str(key), "process_keys entry", keys, report)
    escalation_process = definition.escalation.get("process")
    if isinstance(escalation_process, str) and escalation_process:
        _validate_process_key(path, escalation_process, "escalation.process", keys, report)
    # GUS-06: `escalation.secondary_process` is the SAME kind of reference, for a two-process
    # agent (lucas's own precedented convention — `escalation.secondary_process:
    # SP-OP-CANCEL-001`) that `escalation.process`'s single scalar slot has no room for. A
    # phantom key here was previously unvalidated at artifact time (only caught, if at all, at
    # runtime by the effect PEP).
    escalation_secondary_process = definition.escalation.get("secondary_process")
    if isinstance(escalation_secondary_process, str) and escalation_secondary_process:
        _validate_process_key(
            path, escalation_secondary_process, "escalation.secondary_process", keys, report
        )

    _validate_a2a_handler_disclosure(path, definition.id, definition.a2a, report)


def validate_dir(
    agents_root: Path,
    tools_root: Path,
    report: Report,
    contracts_root: Path | None = None,
) -> None:
    """Validate every `<agents_root>/<id>/agent.yaml` (skips `_template/`)."""
    if not agents_root.exists() or not agents_root.is_dir():
        report.error(agents_root, "agent-definitions directory does not exist")
        return

    resolved_contracts_root = contracts_root if contracts_root is not None else default_contracts_root()
    if not resolved_contracts_root.is_dir():
        report.error(
            resolved_contracts_root,
            "process-contracts directory does not exist — every agent process key would fail the "
            "spec-first cross-check (HEL-12); reported once here rather than N times per agent",
        )
    contract_keys = known_contract_keys(resolved_contracts_root)

    mcp_servers = known_mcp_servers(tools_root)
    agent_dirs = sorted(
        p
        for p in agents_root.iterdir()
        if p.is_dir() and p.name != TEMPLATE_DIR_NAME and (p / "agent.yaml").exists()
    )
    if not agent_dirs:
        report.error(agents_root, "no agent.yaml files found under any subdirectory")

    for agent_dir in agent_dirs:
        validate_file(agent_dir / "agent.yaml", mcp_servers, report, contract_keys)
