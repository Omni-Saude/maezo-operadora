"""CLI da ENGENHARIA: `generate`, `assemble`, `verify`, `lock-sql`, `native-secret`, `human-bundle` e
`login-secrets`.

Nao ha comando de raiz aqui (ver `approver.py`).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from maezo.gateway.external_cases.models import digest
from maezo.portal.engine.profile import canonicalize, strict_loads

from . import human_bundle
from .assemble import assemble, bundle_bytes, load_input, read_directories
from .generate import REPO, generate
from .lock_sql import render as render_lock_sql
from .login_secrets import write as write_login_secrets
from .native_secret import build as build_native_materials
from .native_secret import load_input as load_native_input
from .native_secret import write as write_native_materials
from .secure_io import PRIVATE, PUBLIC, MaterialError, new_private_directory, write_new
from .spec import load_spec
from .verify import load_pins, manifest_digest, verify_bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.staff_materials", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("generate", help="gera chaves, CAs, certificados, DSNs e o rascunho")
    make.add_argument("--spec", type=Path, required=True)
    make.add_argument("--out", type=Path, required=True, help="diretorio NOVO, fora do repositorio")
    pack = commands.add_parser("assemble", help="monta o pacote portal-staff-material.v2 (sem tocar a raiz)")
    pack.add_argument("--materials", type=Path, required=True, help="saida do generate")
    pack.add_argument("--spec", type=Path, required=True, help="o mesmo spec do generate")
    pack.add_argument(
        "--approver", type=Path, required=True, help="installation-root.der + installation-proof.json"
    )
    pack.add_argument("--input", type=Path, required=True, help="staff-materials-assemble.v1")
    pack.add_argument("--out", type=Path, required=True, help="diretorio NOVO, fora do repositorio")
    check = commands.add_parser("verify", help="confere um pacote com o loader do portal")
    check.add_argument("--bundle", type=Path)
    check.add_argument("--pins", type=Path, help="arquivo de pins escrito pelo aprovador")
    check.add_argument("--manifest", type=Path)
    check.add_argument("--print-manifest-digest", action="store_true")
    lock = commands.add_parser("lock-sql", help="renderiza deploy/sql/portal-identity-lock.sql.tmpl (D-D)")
    lock.add_argument("--tenant", required=True)
    lock.add_argument("--tenant-schema", required=True)
    lock.add_argument("--session-lock-login", required=True)
    lock.add_argument("--witness-login", required=True)
    native = commands.add_parser("native-secret", help="monta o segredo engine/native-materials da Onda 4")
    native.add_argument("--materials", type=Path, required=True, help="saida do generate")
    native.add_argument("--approver", type=Path, required=True, help="diretorio com installation-root.der")
    native.add_argument("--input", type=Path, required=True, help="staff-materials-native-secret.v1")
    native.add_argument("--out", type=Path, required=True, help="diretorio NOVO, fora do repositorio")
    native.add_argument("--human-keys", type=Path, help="public/human-keys-summary.json do human-bundle keys")
    human_bundle.add_parser(commands)
    logins = commands.add_parser(
        "login-secrets", help="senhas dos 4 logins nativos da Onda 3 (um JSON por login)"
    )
    logins.add_argument("--out", type=Path, required=True, help="diretorio NOVO, fora do repositorio")
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
        if args.command == "assemble":
            portal, approver_files, summary = read_directories(args.materials, args.approver)
            public, files = assemble(
                portal,
                approver_files,
                summary,
                load_spec(args.spec.read_bytes()),
                load_input(args.input.read_bytes()),
            )
            out = new_private_directory(args.out, forbidden=(REPO,))
            write_new(out / "bundle.json", bundle_bytes(public, files), PRIVATE)
            write_new(out / "public-manifest.json", canonicalize(public), PUBLIC)
            print(f"saida={out}")
            print(f"public_manifest_sha256={digest(public)} (confira com o verify ANTES de pinar)")
            return 0
        if args.command == "lock-sql":
            sys.stdout.write(
                render_lock_sql(
                    tenant=args.tenant,
                    tenant_schema=args.tenant_schema,
                    session_lock_login=args.session_lock_login,
                    witness_login=args.witness_login,
                )
            )
            return 0
        if args.command == "human-bundle":
            return human_bundle.run(args)
        if args.command == "native-secret":
            human = strict_loads(args.human_keys.read_bytes()) if args.human_keys else None
            files, public = build_native_materials(
                args.materials,
                (args.approver / "installation-root.der").read_bytes(),
                load_native_input(args.input.read_bytes(), human),
                human_client_ca=human["client_ca_pem"].encode("ascii") if human else None,
            )
            written = write_native_materials(args.out, files, public)
            # So digests publicos (fingerprints) vao para a saida; nenhum byte de `files` e impresso.
            print(f"saida={written.directory}")
            print(f"trust_configuration_digest={public['trust_configuration_digest']}")
            print(f"staff_native_configuration_digest={public['staff_native_configuration_digest']}")
            return 0
        if args.command == "login-secrets":
            written = write_login_secrets(args.out, forbidden=(REPO,))
            # So caminhos: nenhuma senha vai para a saida.
            print(f"saida={written.directory}")
            for path in written.files:
                print(f"segredo={path.name} (0400; suba com --secret-string file://{path})")
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
