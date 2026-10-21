"""Baixa as paginas OFICIAIS citadas em `assuntos.yaml`, confere que respondem (HTTP 200) e grava o texto
principal de cada uma em `paginas.json` (entrada de `gerar.py`). Roda no NOTEBOOK (precisa de internet; a
tarefa na AWS roda em subrede privada) e so' le paginas publicas do gov.br.

    python -I scripts/base_saude_rascunhos/coletar_paginas.py --saida <pasta>/paginas.json

O texto vai ao modelo como TRECHO de apoio (para o rascunho nao errar o fato); a resposta da Helena nunca e'
o texto da pagina (o rascunho e' texto proprio, com credito "Texto da operadora com base em ...").
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comum import carregar_assuntos, urls_para_conferir  # noqa: E402

_LIMITE = 20000


def baixar(url: str) -> tuple[int | None, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (maezo-base-saude)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return int(r.status), r.geturl(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as erro:
        return int(erro.code), url, ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, url, ""


def texto_principal(pagina: str) -> str:
    """O conteudo entre `#main-content` e `#viewlet-below-content` (o miolo do Plone do gov.br)."""
    i = pagina.find('id="main-content"')
    j = pagina.find('id="viewlet-below-content"', max(i, 0))
    miolo = pagina[max(i, 0) : j if j > 0 else None]
    miolo = re.sub(r"(?is)<(script|style|nav)[^>]*>.*?</\1>", " ", miolo)
    miolo = re.sub(r"(?i)<(?:br|/p|/li|/h[1-6]|/div)[^>]*>", "\n", miolo)
    t = html.unescape(re.sub(r"<[^>]+>", " ", miolo))
    t = re.sub(r"[ \t\xa0]+", " ", t)
    t = re.sub(r"\n\s*", "\n", t).strip()
    return t.removeprefix('id="main-content">').strip()[:_LIMITE]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--saida", required=True, type=Path)
    args = p.parse_args(argv)
    assuntos = carregar_assuntos()
    hoje = datetime.now(UTC).date().isoformat()
    paginas: dict[str, dict[str, object]] = {}
    falhas = 0
    for url in urls_para_conferir(assuntos):
        status, final, corpo = baixar(url)
        texto = texto_principal(corpo) if corpo else ""
        paginas[url] = {"status": status, "final": final, "coletado_em": hoje, "texto": texto}
        ok = status == 200 and bool(texto)
        falhas += 0 if ok else 1
        print(f"{status} {len(texto):>6} {url}")
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(paginas, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(paginas)} paginas, {falhas} sem resposta/sem texto -> {args.saida}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
