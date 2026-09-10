"""Executable prepare/publish administration; no secret or grant CLI arguments."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from maezo.gateway.external_cases.models import now_utc, timestamp
from maezo.gateway.human.read_profile import digest, parse_model
from maezo.portal.engine.profile import canonicalize

from .census_plan import PreparedCensus, compile_plan, protected_read, read_plan, seal
from .census_production import CensusProduction, load_configuration, production
from .census_source import NativePublicationReceipt, correlate
from .models import StaffCaseError


def verify_plan(plan: PreparedCensus, runtime: CensusProduction) -> None:
    config = runtime.config
    now = runtime.current()
    if plan.dependency_digest != digest(config.dependencies.wire()):
        raise StaffCaseError("conflict")
    cut = runtime.source.read(
        canonicalize(plan.cut.wire()), config.dependencies.cut_digest, now=now, current=False
    )
    runtime.require_cut(cut)
    if {p.source_ref for p in cut.source_positions} != set(runtime.clients):
        raise StaffCaseError("denied")
    value = plan.wire()
    value.pop("proof")
    at = timestamp(plan.proof.issued_at)
    if at > now:
        raise StaffCaseError("invalid")
    runtime.authority.proof(
        plan.proof, value, role="case_issuer", purpose="scope_complete", source_ref=cut.source_ref, now=at
    )


async def publish_plan(
    plan: PreparedCensus, runtime: CensusProduction
) -> tuple[NativePublicationReceipt, ...]:
    verify_plan(plan, runtime)
    results: list[NativePublicationReceipt] = []
    for publication in plan.publications:
        runtime.current()
        client = runtime.clients[publication.source_ref]
        # The client authenticates the original native receipt after current TLS/
        # importer checks; any exception stops without sending the next message.
        result = await client.publish(canonicalize(publication.wire()))
        receipt = parse_model(NativePublicationReceipt, result)
        correlate(publication, receipt)
        runtime.current()
        results.append(receipt)
    return tuple(results)


async def run(command: str, path: Path) -> None:
    config = load_configuration(path)
    async with production(config, prepare=command == "prepare") as runtime:
        if command == "prepare":
            assert runtime.resolver is not None and runtime.runtime is not None and runtime.secret is not None
            cut = runtime.source.read(
                protected_read(
                    Path(config.source_cut.path),
                    expected=config.source_cut.sha256,
                    maximum=config.maximum_source_bytes,
                ),
                config.dependencies.cut_digest,
                now=runtime.current(),
            )
            runtime.require_cut(cut)
            if {p.source_ref for p in cut.source_positions} != set(runtime.clients):
                raise StaffCaseError("denied")
            session = await runtime.resolver.resolve(runtime.secret)
            async with runtime.runtime.sessions.acquire(runtime.secret, session.principal) as lease:
                lease.current(now_utc())
                witness = await runtime.runtime.witnesses.observe(session.principal, lease.valid_until)
                plan = compile_plan(
                    cut,
                    principal=session.principal,
                    witness=witness,
                    signer_for=runtime.signer_for,
                    authority=runtime.authority,
                    dependency_digest=digest(config.dependencies.wire()),
                    now=runtime.current(),
                    session_until=lease.valid_until,
                )
                lease.current(now_utc())
                seal(plan, Path(config.output_plan_path), maximum=config.maximum_plan_bytes)
                lease.current(now_utc())
            # A failure here leaves exact sealed bytes, but reports no successful
            # preparation. No remote effect exists until a separate publish call.
            final = await runtime.resolver.resolve(runtime.secret)
            if final.principal != session.principal:
                raise StaffCaseError("conflict")
            runtime.current()
        elif command == "publish":
            if config.input_plan is None:
                raise StaffCaseError("invalid")
            plan = read_plan(
                protected_read(
                    Path(config.input_plan.path),
                    expected=config.input_plan.sha256,
                    maximum=config.maximum_plan_bytes,
                )
            )
            await publish_plan(plan, runtime)
        else:
            raise StaffCaseError("invalid")


def main() -> None:
    parser = argparse.ArgumentParser(description="Protected staff census administration")
    parser.add_argument("command", choices=("prepare", "publish"))
    parser.add_argument("--config", required=True, type=Path)
    arguments = parser.parse_args()
    failed = False
    try:
        asyncio.run(run(arguments.command, arguments.config))
    except (Exception, KeyboardInterrupt):
        # No exception chaining, credential URLs, signed source records or public
        # private-material hashes escape the production command boundary.
        failed = True
    if failed:
        print("staff_census_unavailable")
        raise SystemExit(1)
    print("staff_census_command_completed")


if __name__ == "__main__":
    main()
