#!/usr/bin/env python3
"""Reproduce the packaged Java registry from reviewed D7-A rows; never emit workload grants."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from maezo.gateway.engine_contracts import EngineOperation, canonical_json
from maezo.gateway.engine_schemas import SCHEMAS, start_read_schema, worker_lifecycle_schema


def registry() -> bytes:
    rows = []
    for row in SCHEMAS:
        rows.append(row)
        if row.operation is EngineOperation.START:
            rows.extend(
                start_read_schema(row, op)
                for op in (EngineOperation.READ_ACTIVE, EngineOperation.READ_HISTORY)
            )
        if row.topic:
            rows.extend(
                worker_lifecycle_schema(row, op)
                for op in (
                    EngineOperation.FETCH_LOCK,
                    EngineOperation.FAILURE,
                    EngineOperation.EXTEND_LOCK,
                    EngineOperation.UNLOCK,
                )
            )
    document = json.loads(json.dumps([asdict(row) for row in rows], default=lambda v: v.decode("utf-8")))
    return canonical_json({"protocol": "maezo.engine-schemas.v1", "schemas": document}) + b"\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = registry()
    if args.check:
        if args.path.read_bytes() != expected:
            raise SystemExit("D7 Java registry differs from reviewed Python schemas")
        print("47 exact D7-A registered rows: PASS")
    else:
        args.path.write_bytes(expected)
