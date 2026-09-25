"""B11: o `verificar` cobre todo digest que o portal executa, inclusive `staff.portal_image_digest`.

`scripts/ci/resolve_image_digest.sh` roda contra um `aws` falso: ele so' responde se o digest
existe no ECR (lista em KNOWN).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/ci/resolve_image_digest.sh"
APP = "sha256:" + "a" * 64
STAFF = "sha256:" + "c" * 64

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="POSIX bash fixture"
)


def _run(
    tmp_path: Path, tfvars: str, known: list[str], requested: str = ""
) -> tuple[int, dict[str, str], str]:
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "aws").write_text(
        '#!/bin/sh\nfor a in "$@"; do case "$a" in imageDigest=*) '
        'grep -qx "${a#imageDigest=}" "$KNOWN" && exit 0; exit 254 ;; esac; done\nexit 254\n'
    )
    (fake / "aws").chmod(0o755)
    (tmp_path / "known").write_text("".join(d + "\n" for d in known))
    (tmp_path / "portal.auto.tfvars").write_text(tfvars)
    out = tmp_path / "out"
    out.write_text("")
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            "PATH": f"{fake}:{os.environ['PATH']}",
            "KNOWN": str(tmp_path / "known"),
            "PORTAL_TFVARS": str(tmp_path / "portal.auto.tfvars"),
            "REQUESTED": requested,
            "ECR_REPOSITORY": "amh/maezo-operadora",
            "AWS_REGION": "sa-east-1",
            "REGISTRY": "reg",
            "GITHUB_OUTPUT": str(out),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return result.returncode, outputs, result.stdout + result.stderr


STAFF_TFVARS = f'''portal = {{
  image_digest = "{APP}"
  staff = {{
    # portal_image_digest = "sha256:{"d" * 64}"
    portal_image_digest = "{STAFF}"
  }}
}}
'''


def test_app_only_keeps_single_target(tmp_path: Path) -> None:
    rc, out, log = _run(tmp_path, f'portal = {{\n  image_digest = "{APP}"\n}}\n', [APP])
    assert rc == 0, log
    assert out["image_ref"] == f"reg/amh/maezo-operadora@{APP}"
    assert out["image_refs"] == out["image_ref"]


def test_staff_digest_is_verified_too(tmp_path: Path) -> None:
    rc, out, log = _run(tmp_path, STAFF_TFVARS, [APP, STAFF])
    assert rc == 0, log
    # `assinar` keeps the app digest as its single target; `verificar` gets both (comment ignored).
    assert out["digest"] == APP
    assert out["image_refs"] == f"reg/amh/maezo-operadora@{APP} reg/amh/maezo-operadora@{STAFF}"


def test_same_digest_for_app_and_staff_is_verified_once(tmp_path: Path) -> None:
    rc, out, log = _run(tmp_path, STAFF_TFVARS.replace(STAFF, APP), [APP])
    assert rc == 0, log
    assert out["image_refs"] == f"reg/amh/maezo-operadora@{APP}"


def test_missing_staff_digest_fails_closed(tmp_path: Path) -> None:
    rc, out, log = _run(tmp_path, STAFF_TFVARS, [APP])
    assert rc == 1
    assert STAFF in log and "image_refs" not in out


def test_explicit_request_ignores_tfvars(tmp_path: Path) -> None:
    rc, out, log = _run(tmp_path, STAFF_TFVARS, [APP], requested=APP)
    assert rc == 0, log
    assert out["image_refs"] == f"reg/amh/maezo-operadora@{APP}"
