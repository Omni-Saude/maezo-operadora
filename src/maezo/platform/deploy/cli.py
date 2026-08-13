"""Deployment CLI — `python -m maezo.platform.deploy` / `make deploy-artifacts`.

Two modes:
- (default) deploy: submits every `.bpmn`/`.dmn` file under
  `spec/processes/{bpmn,dmn}/` to the engine as one named deployment, then
  prints deployed-vs-skipped counts straight from the engine's response.
- `--list`: `GET /deployment` — shows what the engine currently holds,
  without deploying anything.

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


def run_deploy(client: EngineDeployClient, *, spec_dir: Path | None, deployment_name: str) -> int:
    """Handle the default deploy action: resolve artifacts, submit, report.

    Returns:
        0 if the engine accepted the deployment, 1 on any resolution error
        or engine rejection (its error body is printed verbatim to stderr).
    """
    try:
        processes_dir = resolve_spec_processes_dir(spec_dir)
        artifacts = collect_artifacts(processes_dir)
    except (FileNotFoundError, EngineDeployError) as exc:
        print(f"[deploy] FAILED to resolve artifacts: {exc}", file=sys.stderr)
        return 1

    bpmn_count = sum(1 for p in artifacts if p.suffix == ".bpmn")
    dmn_count = sum(1 for p in artifacts if p.suffix == ".dmn")
    print(
        f"[deploy] submitting {len(artifacts)} artifact(s) ({bpmn_count} BPMN, {dmn_count} DMN) "
        f"as deployment {deployment_name!r} to {client.base_url}"
    )

    try:
        outcome = client.deploy(artifacts, name=deployment_name)
    except EngineDeployError as exc:
        print(
            f"[deploy] FAILED — engine rejected the deployment (this is the real BPMN/DMN "
            f"validation; fix the artifact, do not retry blindly). Engine error:\n{exc}",
            file=sys.stderr,
        )
        return 1

    print(f"[deploy] OK — deployment id={outcome.deployment_id} name={outcome.name!r}")
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
        return run_deploy(client, spec_dir=spec_dir, deployment_name=args.deployment_name)


if __name__ == "__main__":
    sys.exit(main())
