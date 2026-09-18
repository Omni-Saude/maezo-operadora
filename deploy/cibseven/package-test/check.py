#!/usr/bin/env python3
"""Run only the real-image package tests; caller owns lock, services and teardown."""

import argparse
import json
import os
import time
from pathlib import Path

import httpx
import pytest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads((args.fixture / "test-env.json").read_text())
    os.environ.update(settings)
    # HTTP is used only to observe that deployment has reached its servlet; it cannot
    # authenticate, even on the success path. All plugin-ready assertions use mTLS.
    deadline = time.monotonic() + 180
    with httpx.Client(trust_env=False, timeout=2, follow_redirects=False) as client:
        while True:
            try:
                response = client.post(
                    settings["MAEZO_HUMAN_PACKAGE_HTTP_URL"] + "/maezo-human/v1/commands", content=b"{}"
                )
                if response.status_code == 403:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Tomcat servlet did not become reachable within 180 seconds")
            time.sleep(1)
    return pytest.main(
        [
            "tests/integration/test_portal_engine_package.py",
            "-q",
            "-ra",
            "--junitxml=" + str(args.junit.resolve()),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
