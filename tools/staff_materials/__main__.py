"""CLI da ENGENHARIA: `generate` e `verify`. Nao ha comando de raiz aqui (ver `approver.py`)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .generate import generate
from .secure_io import MaterialError
from .spec import load_spec
from .verify import load_pins, manifest_digest, verify_bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.staff_materials", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("generate", help="gera chaves, CAs, certificados, DSNs e o rascunho")
    make.add_argument("--spec", type=Path, required=True)
    make.add_argument("--out", type=Path, required=True, help="diretorio NOVO, fora do repositorio")
    check = commands.add_parser("verify", help="confere um pacote com o loader do portal")
    check.add_argument("--bundle", type=Path)
    check.add_argument("--pins", type=Path, help="arquivo de pins escrito pelo aprovador")
    check.add_argument("--manifest", type=Path)
    check.add_argument("--print-manifest-digest", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "generate":
            result = generate(load_spec(args.spec.read_bytes()), args.out)
            summary = result.summary
            print(f"saida={result.directory}")
            print(f"designation_sha256={summary['designation_sha256']} (rascunho, SEM assinatura)")
            print(f"native_server_spki_sha256={summary['native_server_spki_sha256']}")
            for role, value in summary["key_fingerprints"].items():
                print(f"key_sha256[{role}]={value}")
            print(f"continuity_commitment={summary['continuity']['commitment']}")
            print("falta do aprovador: installation-root.der e installation-proof.json")
            return 0
        if args.print_manifest_digest:
            if args.manifest is None:
                raise MaterialError("--print-manifest-digest exige --manifest")
            print(f"public_manifest_sha256={manifest_digest(args.manifest.read_bytes())}")
            return 0
        if args.bundle is None or args.pins is None:
            raise MaterialError("verify exige --bundle e --pins (ou --manifest --print-manifest-digest)")
        manifest = verify_bundle(args.bundle.read_bytes(), load_pins(args.pins.read_bytes()))
        print(f"aceito pelo loader: material_version_id={manifest.material_version_id}")
        return 0
    except MaterialError as failure:
        print(f"recusado: {failure}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
