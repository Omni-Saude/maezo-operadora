"""Verificador das FONTES da base de conhecimento de saude da Helena (ADR-0064, DL-0118).

O QUE FAZ. Carrega a base (`spec/conhecimento/saude`, mesmo carregador e mesmas regras do CI) e, para cada
assunto:
  1. lista o que PASSOU (ou vai passar em `--dias` dias) do `revisao.revisar_ate` — o ciclo e' de 6 meses;
  2. baixa cada `fontes[].url` (e o `link_leitura`) e diz se a pagina responde (HTTP 2xx);
  3. quando a fonte tem `sha256_trecho`: confere (a) que o `texto` do arquivo ainda e' o trecho coletado
     (sha256 do texto normalizado == `sha256_trecho`) e (b) que a pagina AINDA contem o trecho (texto
     normalizado da pagina, sem tags). Falhar em (a) = o arquivo mudou sem nova coleta; falhar em (b) = a
     fonte mudou (revisar a citacao).
Imprime uma linha por achado e um resumo. NUNCA altera arquivo nenhum.

NAO RODA NO CI (faz rede e depende de sites de terceiros): rode a mao ou num job agendado do dev, por
exemplo antes de cada ciclo de revisao. Uso:

    python scripts/ops/base_saude_verificar_fontes.py [--raiz spec/conhecimento/saude] [--dias 30]
                                                      [--sem-rede] [--falhar]

  --sem-rede  so' as datas de revisao (nao baixa nada);
  --falhar    sai com 1 quando houver qualquer achado (para um job agendado alertar).

Como gerar o `sha256_trecho` de um texto novo: `--hash "texto literal"` imprime o valor a gravar no YAML
(sha256 do texto NORMALIZADO: minusculas, sem acento, espacos colapsados). Runbook:
`docs/runbooks/helena-base-saude.md`.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

_RAIZ_DO_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_RAIZ_DO_REPO / "src"))

from maezo.conhecimento import base_saude as b  # noqa: E402

_TIMEOUT_S = 20
_AGENTE = "maezo-base-saude-verificador/1.0 (+revisao de fontes; contato: operadora)"
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)


def sha256_do_trecho(texto: str) -> str:
    """O valor de `sha256_trecho`: sha256 do texto NORMALIZADO (o mesmo `normalizar` da busca)."""
    return hashlib.sha256(b.normalizar(texto).encode("utf-8")).hexdigest()


def texto_da_pagina(conteudo_html: str) -> str:
    """O texto legivel de uma pagina HTML, normalizado (sem script/style, sem tags, entidades resolvidas)."""
    sem_script = _SCRIPT.sub(" ", conteudo_html)
    return b.normalizar(html.unescape(_TAG.sub(" ", sem_script)))


@dataclass(frozen=True)
class Achado:
    assunto: str
    tipo: str
    detalhe: str

    def linha(self) -> str:
        return f"[{self.tipo}] {self.assunto}: {self.detalhe}"


def achados_de_revisao(base: b.BaseSaude, *, hoje: date, dias: int) -> list[Achado]:
    limite = hoje + timedelta(days=dias)
    achados = []
    for assunto in sorted(base.assuntos.values(), key=lambda a: a.id):
        ate = assunto.revisao.revisar_ate
        if ate < hoje:
            achados.append(Achado(assunto.id, "revisao_vencida", f"revisar_ate {ate.isoformat()}"))
        elif ate <= limite:
            achados.append(Achado(assunto.id, "revisao_proxima", f"revisar_ate {ate.isoformat()}"))
    return achados


def _baixar(url: str) -> tuple[int | None, str]:
    pedido = urllib.request.Request(url, headers={"User-Agent": _AGENTE})  # noqa: S310 (https conferido no schema)
    try:
        with urllib.request.urlopen(pedido, timeout=_TIMEOUT_S) as resposta:  # noqa: S310
            corpo = resposta.read(3_000_000).decode("utf-8", errors="replace")
            return int(resposta.status), corpo
    except urllib.error.HTTPError as erro:
        return int(erro.code), ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, ""


def achados_de_fonte(base: b.BaseSaude, baixar: object = _baixar) -> list[Achado]:
    achados: list[Achado] = []
    cache: dict[str, tuple[int | None, str]] = {}
    for assunto in sorted(base.assuntos.values(), key=lambda a: a.id):
        urls = [f.url for f in assunto.fontes] + ([assunto.link_leitura] if assunto.link_leitura else [])
        for url in dict.fromkeys(urls):
            if url not in cache:
                cache[url] = baixar(url)  # type: ignore[operator]
            status, _ = cache[url]
            if status is None or not 200 <= status < 300:
                achados.append(Achado(assunto.id, "fonte_fora_do_ar", f"{url} -> {status or 'sem resposta'}"))
        for fonte in assunto.fontes:
            if fonte.sha256_trecho is None:
                continue
            if sha256_do_trecho(assunto.texto) != fonte.sha256_trecho:
                achados.append(
                    Achado(
                        assunto.id, "texto_diverge_do_coletado", "o texto do arquivo nao e' o trecho coletado"
                    )
                )
            status, corpo = cache.get(fonte.url, (None, ""))
            if (
                status is not None
                and 200 <= status < 300
                and b.normalizar(assunto.texto) not in texto_da_pagina(corpo)
            ):
                achados.append(Achado(assunto.id, "fonte_mudou", f"o trecho nao esta mais em {fonte.url}"))
    return achados


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--raiz", type=Path, default=_RAIZ_DO_REPO / "spec" / "conhecimento" / "saude")
    p.add_argument("--dias", type=int, default=30, help="aviso de revisao que vence nos proximos N dias")
    p.add_argument("--sem-rede", action="store_true", help="so' as datas de revisao")
    p.add_argument("--falhar", action="store_true", help="sai com 1 se houver achado")
    p.add_argument("--hash", metavar="TEXTO", help="imprime o sha256_trecho de TEXTO e sai")
    args = p.parse_args(argv)
    if args.hash is not None:
        print(sha256_do_trecho(args.hash))
        return 0
    base = b.carregar_base(args.raiz)
    hoje = datetime.now(UTC).date()
    achados = achados_de_revisao(base, hoje=hoje, dias=args.dias)
    if not args.sem_rede:
        achados += achados_de_fonte(base)
    for achado in achados:
        print(achado.linha())
    print(f"resumo: {len(base)} assunto(s), {len(achados)} achado(s), hoje={hoje.isoformat()}")
    return 1 if (args.falhar and achados) else 0


if __name__ == "__main__":
    sys.exit(main())
