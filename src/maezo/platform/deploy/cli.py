"""Deployment CLI — `python -m maezo.platform.deploy` / `make deploy-artifacts`.

Two modes:
- (default) deploy: submits every `.bpmn`/`.dmn` file under
  `spec/processes/{bpmn,dmn}/` to the engine as one named deployment, then
  prints deployed-vs-skipped counts straight from the engine's response.
- `--list`: `GET /deployment` — shows what the engine currently holds,
  without deploying anything.

TARGET IS EXPLICIT (27/09/2026): a deploy names where the definitions live — `--tenant <id>`
(every BPMN + DMN owned by that tenant — what the tenant-scoped start route of
`CibSevenHttpTransport` resolves) and/or `--shared dmn|all` (tenant-less copies). With
neither, the CLI REFUSES (exit 2) instead of silently deploying the shared set that tenant-bound
starts cannot see. `--shared dmn` exists because the agent-side DMN
evaluation (`tools/workers/dmn_transport.py`, `tenant=None`) still reads SHARED decisions; shared
BPMN (`--shared all`) is for the local compose / integration engine only — in a live environment
a second, shared copy of a BPMN duplicates its timer start events and makes a message start
correlated without tenant ambiguous.

Fail-closed: any resolution error (missing `spec/processes/`, zero artifact
files) or engine rejection (non-2xx, transport failure, bad body) prints the
engine's verbatim error and exits non-zero. There is no warn-and-continue
path — see `engine_deploy.EngineDeployError`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .engine_deploy import (
    DEFAULT_DEPLOYMENT_NAME,
    DEFAULT_ENGINE_REST_URL,
    ENGINE_REST_URL_ENV,
    EngineDeployClient,
    EngineDeployError,
    collect_artifacts,
    resolve_spec_processes_dir,
)

#: `--shared` values: which artifacts get a tenant-less copy.
SHARED_CHOICES = ("dmn", "all")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="maezo-deploy",
        description="Deploy spec/processes/{bpmn,dmn} artifacts to the CIB Seven process engine.",
    )
    parser.add_argument(
        "--list",
        dest="list_mode",
        action="store_true",
        help="List current deployments on the engine (GET /deployment) instead of deploying.",
    )
    parser.add_argument(
        "--deployment-name",
        default=DEFAULT_DEPLOYMENT_NAME,
        help=(
            "Deployment name used for engine-side duplicate filtering "
            f"(default: {DEFAULT_DEPLOYMENT_NAME!r}). Keep this stable across runs — "
            "changing it defeats idempotency."
        ),
    )
    parser.add_argument(
        "--engine-url",
        default=None,
        help=(
            f"Engine REST base URL (default: ${ENGINE_REST_URL_ENV} if set, "
            f"else {DEFAULT_ENGINE_REST_URL!r})."
        ),
    )
    parser.add_argument(
        "--spec-dir",
        default=None,
        help=(
            "Explicitly override the resolved spec/ directory. This flag is the ONLY sanctioned "
            "override for this operator tool (Wave-1 Q-6): with it absent, resolution falls "
            "through to maezo.agents.resolve_spec_dir(), which REFUSES the ambient $MAEZO_SPEC_DIR "
            "variable outside an explicitly local runtime."
        ),
    )
    parser.add_argument(
        "--tenant",
        default=None,
        help=(
            "Deploy every BPMN + DMN as definitions OWNED by this tenant (tenant-id). Required "
            "unless --shared is given: a tenant-bound start only resolves definitions of its own "
            "tenant, and a tenant's business rule tasks only resolve decisions of that tenant."
        ),
    )
    parser.add_argument(
        "--shared",
        choices=SHARED_CHOICES,
        default=None,
        help=(
            "Also (or only) deploy tenant-less copies: 'dmn' = decisions only (the agent-side DMN "
            "evaluation reads shared decisions); 'all' = BPMN + DMN (local/integration engine "
            "only — never a live environment, see the module docstring)."
        ),
    )
    return parser


def _print_deployment_listing(deployments: list[dict[str, object]]) -> None:
    print(f"[deploy] {len(deployments)} deployment(s) on engine:")
    for d in deployments:
        print(f"  - id={d.get('id')} name={d.get('name')!r} time={d.get('deploymentTime')}")


def run_list(client: EngineDeployClient) -> int:
    """Handle `--list`: GET /deployment and print the listing.

    Returns:
        0 on success, 1 if the engine rejects the listing request.
    """
    try:
        deployments = client.list_deployments()
    except EngineDeployError as exc:
        print(f"[deploy] FAILED to list deployments from {client.base_url}: {exc}", file=sys.stderr)
        return 1
    _print_deployment_listing(deployments)
    return 0


def deployment_targets(
    artifacts: Sequence[Path], *, tenant: str | None, shared: str | None
) -> list[tuple[str | None, list[Path]]]:
    """The `(tenant_id, artifacts)` deployments to submit, tenant first; `[]` = no target named."""
    targets: list[tuple[str | None, list[Path]]] = []
    if tenant is not None:
        targets.append((tenant, list(artifacts)))
    if shared == "all":
        targets.append((None, list(artifacts)))
    elif shared == "dmn":
        dmn = [p for p in artifacts if p.suffix == ".dmn"]
        if dmn:
            targets.append((None, dmn))
    return targets


def run_deploy(
    client: EngineDeployClient,
    *,
    spec_dir: Path | None,
    deployment_name: str,
    tenant: str | None,
    shared: str | None,
) -> int:
    """Handle the default deploy action: resolve artifacts, submit, report.

    Returns:
        0 if the engine accepted every deployment, 1 on any resolution error
        or engine rejection (its error body is printed verbatim to stderr),
        2 when no target (`--tenant` / `--shared`) was named.
    """
    if tenant is None and shared is None:
        print(
            "[deploy] REFUSED: name the target — `--tenant <id>` (definitions owned by the tenant "
            "the agents start in) and/or `--shared dmn|all`. A deploy without a tenant is invisible "
            "to tenant-bound starts.",
            file=sys.stderr,
        )
        return 2
    if shared is not None and shared not in SHARED_CHOICES:
        print(f"[deploy] REFUSED: --shared must be one of {SHARED_CHOICES}", file=sys.stderr)
        return 2
    try:
        processes_dir = resolve_spec_processes_dir(spec_dir)
        artifacts = collect_artifacts(processes_dir)
    except (FileNotFoundError, EngineDeployError) as exc:
        print(f"[deploy] FAILED to resolve artifacts: {exc}", file=sys.stderr)
        return 1

    for target_tenant, target_artifacts in deployment_targets(artifacts, tenant=tenant, shared=shared):
        bpmn_count = sum(1 for p in target_artifacts if p.suffix == ".bpmn")
        dmn_count = sum(1 for p in target_artifacts if p.suffix == ".dmn")
        label = f"tenant {target_tenant!r}" if target_tenant is not None else "SHARED (no tenant)"
        print(
            f"[deploy] submitting {len(target_artifacts)} artifact(s) ({bpmn_count} BPMN, "
            f"{dmn_count} DMN) as deployment {deployment_name!r} for {label} to {client.base_url}"
        )

        try:
            outcome = client.deploy(target_artifacts, name=deployment_name, tenant_id=target_tenant)
        except EngineDeployError as exc:
            print(
                f"[deploy] FAILED — engine rejected the deployment for {label} (this is the real "
                f"BPMN/DMN validation; fix the artifact, do not retry blindly). Engine error:\n{exc}",
                file=sys.stderr,
            )
            return 1

        print(f"[deploy] OK — deployment id={outcome.deployment_id} name={outcome.name!r} ({label})")
        print(f"[deploy] deployed (new/changed): {outcome.deployed_count}")
        print(f"[deploy] skipped (duplicate, unchanged): {outcome.skipped_count}")
        if outcome.redeployed_resources:
            print("[deploy] redeployed resources:")
            for r in outcome.redeployed_resources:
                print(f"  - {r}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the deployment CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    spec_dir = Path(args.spec_dir).expanduser().resolve() if args.spec_dir else None

    with EngineDeployClient(base_url=args.engine_url) as client:
        if args.list_mode:
            return run_list(client)
        return run_deploy(
            client,
            spec_dir=spec_dir,
            deployment_name=args.deployment_name,
            tenant=args.tenant,
            shared=args.shared,
        )


if __name__ == "__main__":
    sys.exit(main())
