"""Monta os YAML da base a partir dos rascunhos aceitos (`rascunhos.jsonl` de `gerar.py`). Roda LOCAL.

    python scripts/base_saude_rascunhos/montar.py --rascunhos <pasta>/rascunhos.jsonl \
        --paginas <pasta>/paginas.json [--raiz spec/conhecimento/saude] [--relatorio <pasta>/montagem.json]

Para cada rascunho ACEITO (`desfecho` primeira/reescrita): escreve `<categoria>/<id>.yaml` no formato da base
com `revisao.status: rascunho_pendente_revisao_medica`, `escrito_por: claude-sonnet-5.5 (rascunho)`,
`aprovado_por: dono (18/10/2026, fase 5, dev)`, `revisar_ate` = coleta + 6 meses, `tipo_texto: proprio`,
`licenca: texto próprio da operadora` e as fontes de `assuntos.yaml` (URL conferida por `coletar_paginas.py`,
ou a REFERENCIA SEM LINK). Sinonimos: tira os que ja' sao de outro assunto da base (um sinonimo pertence a UM
id), os genericos demais (parte do corpo, "exame"...) e os repetidos entre os novos. Depois CARREGA a base
inteira e roda `problemas_de_politica`: arquivo que reprova e' APAGADO e listado no relatorio.
Nunca sobrescreve um arquivo que ja' existe na base.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comum import CATEGORIA_DO_PREFIXO  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
ESCRITO_POR = "claude-sonnet-5.5 (rascunho)"
AUTOR = "Claude (assistente de IA)"
APROVADO_POR = "dono (18/10/2026, fase 5, dev)"
STATUS = "rascunho_pendente_revisao_medica"
LICENCA = "texto próprio da operadora"
CABECALHO = (
    "# Base de conhecimento de saúde da Helena — RASCUNHO DA FASE 5 (dev).\n"
    "# Escrito pelo Claude Sonnet 5.5 a partir de prompt sintético (sem dado de beneficiário), aprovado pelo\n"
    "# dono em 18/10/2026 apenas para dev. REVISÃO MÉDICA PENDENTE (e jurídica) antes de qualquer uso em\n"
    "# produção. Texto informativo: não diagnostica, não indica conduta nem dose e não promete cobertura.\n"
)
#: Termos genericos demais para serem sinonimo de UM assunto (parte do corpo, palavra de categoria).
GENERICOS = frozenset(
    "dor dores exame exames vacina vacinas cirurgia operacao doenca remedio tireoide figado rim rins utero "
    "ovario ovarios pele sangue fezes urina coluna olho olhos ouvido intestino prostata mama seio pulmao "
    "coracao joelho osso ossos cabeca barriga estomago nariz garganta virus infeccao cancer tumor alergia "
    "hormonio gravidez menstruacao sono ansiedade depressao".split()
)
#: Sinonimo com cara de SINTOMA levaria a queixa ao texto de uma doenca (sintoma e' das perguntas de alerta).
_SINTOMA = re.compile(
    r"^(?:dor|dores|colica|febre(?! amarela)|tosse(?! comprida)|sangramento|cisto|cistos|falta de ar|coceira)\b|sintoma"
)
#: Sinonimos ESPECIFICOS que hoje moram num assunto GERAL e passam ao novo assunto especifico (o calendario do
#: adolescente tinha "vacina hpv"; agora existe `vac-hpv`). A linha sai do arquivo antigo.
TRANSFERIR: dict[str, str] = {"vacina hpv": "vac-hpv", "vacina contra hpv": "vac-hpv"}


def _tirar_sinonimo(raiz: Path, assunto_id: str, termo: str) -> None:
    """Tira a linha `- <termo>` dos sinonimos do arquivo de `assunto_id` (edicao de TEXTO: o arquivo antigo
    mantem cabecalho, ancoras e ordem)."""
    for caminho in raiz.glob(f"*/{assunto_id}.yaml"):
        texto = caminho.read_bytes().decode("utf-8")
        novo = re.sub(rf"(?m)^- {re.escape(termo)}\n", "", texto, count=1)
        caminho.write_bytes(novo.encode("utf-8"))


def _normalizar(texto: str) -> str:
    from maezo.conhecimento.base_saude import normalizar

    return normalizar(texto)


def _seis_meses(d: date) -> date:
    mes = d.month + 6
    ano = d.year + (mes - 1) // 12
    mes = (mes - 1) % 12 + 1
    return date(ano, mes, min(d.day, 28))


def documento(r: dict[str, Any], sinonimos: list[str], paginas: dict[str, Any], hoje: date) -> dict[str, Any]:
    fontes: list[dict[str, Any]] = []
    for f in r["fontes"]:
        coletado = (paginas.get(f["url"]) or {}).get("coletado_em") if f["url"] else None
        fontes.append(
            {
                "nome": f["nome"],
                "url": f["url"],
                "licenca": LICENCA,
                "coletado_em": date.fromisoformat(coletado) if coletado else hoje,
            }
        )
    leitura = next(
        (
            f["url"]
            for f in r["fontes"]
            if f["url"] and ("saude-de-a-a-z" in f["url"] or "/inca/" in f["url"] or "vacinacao" in f["url"])
        ),
        None,
    )
    doc: dict[str, Any] = {
        "id": r["id"],
        "categoria": r["categoria"],
        "titulo": r["titulo"],
        "sinonimos": sinonimos,
        "texto": r["texto_final"],
        "tipo_texto": "proprio",
        "fontes": fontes,
    }
    if leitura:
        doc["link_leitura"] = leitura
    doc["revisao"] = {
        "status": STATUS,
        "autor": AUTOR,
        "aprovado_por": APROVADO_POR,
        "revisao_medica": "pendente",
        "revisar_ate": _seis_meses(min(f["coletado_em"] for f in fontes)),
        "escrito_por": ESCRITO_POR,
    }
    return doc


def main(argv: list[str] | None = None) -> int:
    from maezo.conhecimento import base_saude as bs

    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--rascunhos", required=True, type=Path)
    p.add_argument("--paginas", required=True, type=Path)
    p.add_argument("--raiz", type=Path, default=_REPO / "spec" / "conhecimento" / "saude")
    p.add_argument("--relatorio", type=Path)
    p.add_argument("--hoje", default=date.today().isoformat())
    args = p.parse_args(argv)
    hoje = date.fromisoformat(args.hoje)
    registros = [json.loads(x) for x in args.rascunhos.read_text(encoding="utf-8").splitlines() if x.strip()]
    paginas = json.loads(args.paginas.read_text(encoding="utf-8"))
    base = bs.carregar_base(args.raiz)
    ocupados = dict(base.por_sinonimo)
    relatorio: dict[str, Any] = {
        "escritos": [],
        "reprovados_na_geracao": [],
        "removidos": [],
        "sinonimos_tirados": {},
    }
    escritos: list[Path] = []
    for r in registros:
        if r["desfecho"] == "reprovado" or not r.get("texto_final"):
            relatorio["reprovados_na_geracao"].append(
                {"id": r["id"], "violacoes": r.get("violacoes2") or r.get("violacoes1")}
            )
            continue
        if r["id"] in base.assuntos:
            relatorio["removidos"].append({"id": r["id"], "motivo": "ja' existe na base"})
            continue
        if not r["id"].startswith(next(p for p, c in CATEGORIA_DO_PREFIXO.items() if c == r["categoria"])):
            relatorio["removidos"].append({"id": r["id"], "motivo": "prefixo"})
            continue
        if _normalizar(r["titulo"]) in ocupados:
            relatorio["removidos"].append(
                {"id": r["id"], "motivo": f"titulo ja' e' de {ocupados[_normalizar(r['titulo'])]}"}
            )
            continue
        sinonimos: list[str] = []
        tirados: list[str] = []
        for s in [*r["sinonimos"], r["titulo"].lower()]:
            n = _normalizar(s)
            if TRANSFERIR.get(n) == r["id"] and n in ocupados and ocupados[n] != r["id"]:
                _tirar_sinonimo(args.raiz, ocupados[n], s)
                relatorio.setdefault("sinonimos_transferidos", []).append(
                    {"de": ocupados[n], "para": r["id"], "termo": s}
                )
                del ocupados[n]
            if _SINTOMA.search(n):
                tirados.append(s)
                continue
            if not n or len(n) < 2 or n in GENERICOS or (n in ocupados and ocupados[n] != r["id"]):
                tirados.append(s)
                continue
            if s not in sinonimos:
                sinonimos.append(s)
        if not sinonimos:
            relatorio["removidos"].append({"id": r["id"], "motivo": "nenhum sinonimo livre"})
            continue
        for s in sinonimos:
            ocupados[_normalizar(s)] = r["id"]
        ocupados[_normalizar(r["titulo"])] = r["id"]
        if tirados:
            relatorio["sinonimos_tirados"][r["id"]] = tirados
        caminho = args.raiz / r["categoria"] / f"{r['id']}.yaml"
        if caminho.exists():
            relatorio["removidos"].append({"id": r["id"], "motivo": "arquivo ja' existe"})
            continue
        corpo = yaml.safe_dump(
            documento(r, sinonimos, paginas, hoje), allow_unicode=True, sort_keys=False, width=10_000
        )
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes((CABECALHO + corpo).encode("utf-8"))  # LF sempre (tambem no Windows)
        escritos.append(caminho)
    # Confere a base INTEIRA: schema (carregador) e politica de cada novo; o que reprova sai.
    for _ in range(len(escritos) + 1):
        try:
            nova = bs.carregar_base(args.raiz)
        except bs.BaseSaudeInvalidaError as exc:
            culpado = next((c for c in escritos if any(c.name in e for e in exc.erros)), None)
            if culpado is None:
                raise
            relatorio["removidos"].append({"id": culpado.stem, "motivo": "; ".join(exc.erros)[:300]})
            culpado.unlink()
            escritos.remove(culpado)
            continue
        break
    for caminho in list(escritos):
        assunto = nova.assuntos[caminho.stem]
        problemas = bs.problemas_de_politica(assunto, hoje=hoje)
        if problemas:
            relatorio["removidos"].append({"id": caminho.stem, "motivo": "; ".join(problemas)})
            caminho.unlink()
            escritos.remove(caminho)
    relatorio["escritos"] = sorted(c.stem for c in escritos)
    saida = json.dumps(relatorio, ensure_ascii=False, indent=2, default=str)
    if args.relatorio:
        args.relatorio.write_text(saida, encoding="utf-8")
    print(
        f"{len(escritos)} escritos; reprovados na geracao: {len(relatorio['reprovados_na_geracao'])}; "
        f"removidos: {len(relatorio['removidos'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
