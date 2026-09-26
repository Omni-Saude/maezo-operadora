"""Passo `assemble`: `assemble` v2 (T1.3) -> digest do manifesto -> `verify` com os pins do "aprovador".

Tudo pela CLI da ferramenta (`python -m tools.staff_materials ...`), como a engenharia rodaria. O
arquivo de pins e escrito aqui no papel do aprovador de TESTE: no C1 os valores vem das fontes locais
medidas (banco, engine, generate), nunca do proprio pacote.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta

from maezo.portal.engine.profile import strict_loads

from .common import (
    APPROVER_OUT,
    ASSEMBLED,
    ENGINE_SCHEMA,
    MATERIALS,
    NATIVE_HOSTNAME,
    NATIVE_SCHEMA,
    ROOT,
    STATE,
    iso,
    jcs,
    now,
    save_state,
    sha256,
    state,
    step,
    write,
)
from .seed import ISSUER


def _tool(*args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("MAEZO_PORTAL_")}
    return subprocess.run([sys.executable, "-m", "tools.staff_materials", *args], capture_output=True, text=True, env=env)


def main() -> None:
    pins = state("pins")
    engine = state("engine")
    summary = state("materials")["summary"]
    version = "c1staff" + os.urandom(13).hex()
    inputs = dict(
        schema="staff-materials-assemble.v1", material_version_id=version, issuer=ISSUER,
        issued_at=iso(now() - timedelta(minutes=2)), valid_until=iso(now() + timedelta(days=5)),
        native_configuration_digest=engine["configuration_digest"], native_maximum_seconds="10",
        native_schema=NATIVE_SCHEMA, engine_schema=ENGINE_SCHEMA,
        session_lock_function_pin=pins["lock_function"],
        native_relation_pins={t: pins["relations"][t] for t in ("mzo_human_principal", "mzo_portal_read_membership")},
        revocation=dict(source_ref="c1-staff-revocation", revision="1", observed_at=iso(now() - timedelta(minutes=2)),
                        valid_until=iso(now() + timedelta(days=5)), revoked_fingerprints=[]),
    )
    write(ROOT / "assemble-input.json", jcs(inputs), 0o444)
    # Depois do passo `rotate`: o pacote da designacao r2 (mesmas chaves) com a prova dela.
    rotation = state("rotation") if (STATE / "rotation.json").exists() else None
    approver_dir = ROOT / "rotation" / "approver" if rotation else APPROVER_OUT
    extra = ["--designation", str(ROOT / "rotation" / "designation.json")] if rotation else []
    designation_digest = rotation["designation_digest"] if rotation else state("materials")["designation_digest"]
    assembled = _tool("assemble", "--materials", str(MATERIALS), "--spec", str(ROOT / "spec.json"),
                      "--approver", str(approver_dir), "--input", str(ROOT / "assemble-input.json"),
                      "--out", str(ASSEMBLED), *extra)
    if assembled.returncode != 0:
        step("assemble", False, f"assemble rc={assembled.returncode}: {assembled.stderr.strip()[-200:]}")
        raise SystemExit(1)
    digest = _tool("verify", "--manifest", str(ASSEMBLED / "public-manifest.json"), "--print-manifest-digest")
    manifest_sha = digest.stdout.strip().split("=")[-1]
    manifest = strict_loads((ASSEMBLED / "public-manifest.json").read_bytes())
    approver_pins = dict(
        capabilities="identity,staff_cases", tenant=manifest["scope"]["tenant"], issuer=ISSUER,
        staff_material_directory="/run/maezo-staff-materials/current", staff_material_version_id=version,
        staff_public_manifest_sha256=manifest_sha,
        staff_root_key_sha256=sha256((APPROVER_OUT / "installation-root.der").read_bytes()),
        staff_designation_sha256=designation_digest,
        staff_native_configuration_sha256=engine["configuration_digest"], staff_scope=summary["scope"],
        staff_native_origin="https://" + NATIVE_HOSTNAME,
        staff_native_server_spki_sha256=summary["native_server_spki_sha256"],
        staff_native_schema=NATIVE_SCHEMA, staff_engine_schema=ENGINE_SCHEMA,
        staff_read_key_sha256=summary["key_fingerprints"]["read_requester"],
        staff_witness_key_sha256=summary["key_fingerprints"]["identity_verifier"], staff_maximum_seconds=10,
    )
    write(ROOT / "approver-pins.json", json.dumps(approver_pins), 0o444)
    verified = _tool("verify", "--bundle", str(ASSEMBLED / "bundle.json"), "--pins", str(ROOT / "approver-pins.json"))
    # Negativo: 1 byte trocado no pacote tem que ser recusado pelo mesmo loader.
    raw = bytearray((ASSEMBLED / "bundle.json").read_bytes())
    raw[len(raw) // 2] ^= 0x01
    write(ROOT / "bundle-tampered.json", bytes(raw), 0o400)
    tampered = _tool("verify", "--bundle", str(ROOT / "bundle-tampered.json"), "--pins", str(ROOT / "approver-pins.json"))
    save_state("assemble", dict(version=version, manifest_sha256=manifest_sha, pins=approver_pins))
    step("assemble", verified.returncode == 0 and tampered.returncode != 0,
         f"assemble v2 ok (manifesto {manifest_sha[:12]}); verify com pins do aprovador rc={verified.returncode} "
         f"[{verified.stdout.strip() or verified.stderr.strip()}]; pacote com 1 byte trocado rc={tampered.returncode}")


if __name__ == "__main__":
    main()
