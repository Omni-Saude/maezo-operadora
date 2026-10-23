#!/usr/bin/env python3
"""Publica o pacote de comportamento de um agente e move o ponteiro de um ambiente (ADR-0067 fase 3).

    python scripts/agentes/publicar_pacote.py --agente helena --ambiente dev --versao <commit> \\
        --uri s3://<bucket>/agentes            # publica e aponta
    python scripts/agentes/publicar_pacote.py --agente helena --seco   # so' monta e valida

1. Monta `spec/agents/<agente>/pacote/` (JSON canonico, sha256 dos bytes).
2. Valida com o proprio agente (`maezo.agents.<agente>.pacote.validar`): o que o agente recusaria em runtime
   e' recusado aqui, antes de qualquer escrita.
3. Grava `<agente>/pacotes/<sha256>.json` (imutavel: se ja' existe, confere que e' igual) e so' entao
   `<agente>/<ambiente>/ativo.json`. O receptor passa a usar a versao nova em ate' 60 s, sem deploy.

Voltar uma versao = rodar de novo num commit anterior (o pacote ja' existe; so' o ponteiro muda).
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from maezo.runtime import pacote_do_agente as pda

_RAIZ = Path(__file__).resolve().parents[2]


def montar_e_validar(agente: str, versao: str) -> tuple[bytes, pda.PacoteDoAgente]:
    conteudo, pacote = pda.montar(agente, versao, _RAIZ / "spec" / "agents" / agente / "pacote")
    try:
        modulo = importlib.import_module(f"maezo.agents.{agente}.pacote")
    except ModuleNotFoundError:
        raise SystemExit(f"o agente {agente!r} nao tem validador (maezo.agents.{agente}.pacote)") from None
    modulo.validar(pacote)
    return conteudo, pacote


def publicar(conteudo: bytes, pacote: pda.PacoteDoAgente, *, uri: str, ambiente: str) -> None:
    import boto3  # type: ignore[import-untyped]

    bucket, _, prefixo = uri.removeprefix("s3://").partition("/")
    base = f"{prefixo.strip('/')}/{pacote.agente}" if prefixo.strip("/") else pacote.agente
    s3 = boto3.client("s3")
    chave_pacote = f"{base}/pacotes/{pacote.sha256}.json"
    try:
        existente = s3.get_object(Bucket=bucket, Key=chave_pacote)["Body"].read()
    except s3.exceptions.NoSuchKey:
        existente = None
    if existente is None:
        s3.put_object(Bucket=bucket, Key=chave_pacote, Body=conteudo, ContentType="application/json")
    elif existente != conteudo:
        raise SystemExit(f"{chave_pacote} ja' existe com outro conteudo — pacote imutavel, recusado")
    ponteiro = {
        "sha256": pacote.sha256,
        "versao": pacote.versao,
        "publicado_em": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    s3.put_object(
        Bucket=bucket,
        Key=f"{base}/{ambiente}/ativo.json",
        Body=json.dumps(ponteiro).encode("utf-8"),
        ContentType="application/json",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--agente", required=True)
    p.add_argument("--versao", default="local")
    p.add_argument("--ambiente", default="dev")
    p.add_argument("--uri", default="")
    p.add_argument("--seco", action="store_true", help="so' monta e valida, sem escrever")
    args = p.parse_args(argv)
    conteudo, pacote = montar_e_validar(args.agente, args.versao)
    print(f"[pacote] {pacote.agente} versao={pacote.versao} sha256={pacote.sha256}")
    print(f"[pacote] arquivos={len(pacote.arquivos)}")
    if args.seco:
        return 0
    if not args.uri.startswith("s3://"):
        raise SystemExit("--uri s3://bucket/prefixo e' obrigatorio para publicar")
    publicar(conteudo, pacote, uri=args.uri, ambiente=args.ambiente)
    print(f"[pacote] publicado e apontado: {args.ambiente} -> {pacote.sha256}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
