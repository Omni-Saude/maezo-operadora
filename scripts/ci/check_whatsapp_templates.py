#!/usr/bin/env python3
"""Cerca de CI do registro de templates WhatsApp (E-SF-13 / GTM-E-42).

Por que
-------
`spec/communications/whatsapp-templates.yaml` e' o registro FECHADO que o `OutboundGate` le'. Uma entrada
fora do esquema (classe de variavel desconhecida, classe ou corpo com dado de saude, `approved` sem
referencia de revisao, finalidade ratificada sem referencia) tem de deixar o CI vermelho ANTES de chegar
ao gate. O esquema tem UMA implementacao: `maezo.gateway.communications.whatsapp_templates`; este script
so' a executa sobre o arquivo e imprime os tokens de problema (nunca valores).

Ligacao ao CI: `tests/unit/ci/test_check_whatsapp_templates.py` executa este script no lane normal de
testes unitarios (o arquivo publicado tem de passar; um registro quebrado tem de reprovar).

Uso: `.venv/bin/python scripts/ci/check_whatsapp_templates.py [--registro <arquivo.yaml>]`
(padrao: `spec/communications/whatsapp-templates.yaml`).
Saida 0 = valido; 1 = invalido (lista os problemas); 2 = arquivo ausente.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
if str(_RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "src"))

from maezo.gateway.communications.whatsapp_templates import (  # noqa: E402
    RegistroInvalidoError,
    ler_registro,
)

PADRAO = _RAIZ / "spec" / "communications" / "whatsapp-templates.yaml"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_whatsapp_templates")
    parser.add_argument("--registro", type=Path, default=PADRAO)
    args = parser.parse_args(argv)
    caminho: Path = args.registro
    if not caminho.is_file():
        print(f"check_whatsapp_templates: registro ausente: {caminho}", file=sys.stderr)
        return 2
    try:
        registro = ler_registro(caminho)
    except RegistroInvalidoError as exc:
        print("check_whatsapp_templates: REGISTRO INVALIDO", file=sys.stderr)
        for problema in exc.problemas:
            print(f"  - {problema}", file=sys.stderr)
        return 1
    aprovados = sum(1 for t in registro.templates.values() if t.aprovado)
    ratificadas = sum(1 for f in registro.finalidades.values() if f.ratificada)
    print(
        f"check_whatsapp_templates: OK — {len(registro.templates)} template(s), {aprovados} aprovado(s); "
        f"{len(registro.finalidades)} finalidade(s), {ratificadas} ratificada(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
