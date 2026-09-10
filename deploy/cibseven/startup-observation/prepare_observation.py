"""Pure opt-in compose construction. Never reads process environment or private logs."""

from __future__ import annotations

import copy
import re
from pathlib import Path

AREA = "/run/maezo-startup-observation"
INPUT = "/run/maezo-startup-observation-input"
DEFAULT_JAVA_OPTS = "-Xms128m -Xmx512m"
TMPFS = AREA + ":rw,nosuid,nodev,noexec,size=33554432,mode=0700,uid=1000,gid=1000"


def instrument(compose: dict, bundle: Path, project: str) -> dict:
    if not re.fullmatch(r"d7-[a-z0-9-]{8,64}", project):
        raise ValueError("OBSERVATION_REFUSED")
    if not bundle.is_absolute() or str(bundle) != str(bundle.resolve()) or bundle.is_symlink():
        raise ValueError("OBSERVATION_REFUSED")
    result = copy.deepcopy(compose)
    engine = result["services"]["engine"]
    if engine["environment"].get("JAVA_OPTS") != DEFAULT_JAVA_OPTS or engine.get("tmpfs"):
        raise ValueError("OBSERVATION_REFUSED")
    if any(v.get("target", "").startswith(AREA) for v in engine["volumes"]):
        raise ValueError("OBSERVATION_REFUSED")
    engine["environment"]["JAVA_OPTS"] += (
        " -XX:FlightRecorderOptions=repository=" + AREA + "/repository,maxchunksize=1m,stackdepth=128"
        " -javaagent:" + INPUT + "/observer.jar -Dmaezo.d7.project=" + project
    )
    engine["tmpfs"] = [TMPFS]
    for name in ("observer.jar", "startup.jfc"):
        engine["volumes"].append(
            {
                "type": "bind",
                "source": str(bundle / name),
                "target": INPUT + "/" + name,
                "read_only": True,
                "bind": {"create_host_path": False},
            }
        )
    return result
