"""O que `coletar_paginas.py`, `gerar.py` e `montar.py` compartilham: a lista de assuntos e as fontes.

Nada aqui chama rede.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PASTA = Path(__file__).resolve().parent
ASSUNTOS = PASTA / "assuntos.yaml"

#: As paginas de apoio de exame/procedimento/cirurgia e de vacina (as mesmas dos rascunhos de 17/10/2026).
URL_ROL_ANS = "https://www.gov.br/ans/pt-br/acesso-a-informacao/participacao-da-sociedade/atualizacao-do-rol-de-procedimentos"
NOME_ROL_ANS = "ANS — Rol de Procedimentos e Eventos em Saúde (atualização do Rol)"
URL_CALENDARIO = "https://www.gov.br/saude/pt-br/vacinacao/calendario"
NOME_CALENDARIO = "Ministério da Saúde — Calendário Nacional de Vacinação"
NOME_SEM_LINK = (
    "Sem página leiga oficial na lista fechada (FONTES.md) — texto próprio, conferir na revisão médica"
)
#: O fecho FIXO dos assuntos de saude mental (o mesmo dos rascunhos de depressao/ansiedade).
FECHO_SAUDE_MENTAL = (
    "Se você estiver em sofrimento, pode ligar 188 (CVV), a qualquer hora. "
    "Também posso chamar alguém da nossa equipe para conversar com você."
)
CATEGORIA_DO_PREFIXO = {
    "con-": "condicao",
    "exa-": "exame",
    "pro-": "procedimento",
    "cir-": "cirurgia",
    "vac-": "vacina",
    "med-": "medicamento",
}


@dataclass(frozen=True)
class Fonte:
    nome: str
    url: str | None


@dataclass(frozen=True)
class AssuntoNovo:
    id: str
    titulo: str
    grupo: str
    categoria: str
    #: As fontes na ORDEM do arquivo (a primeira de responder e' a creditada).
    fontes: tuple[Fonte, ...]
    #: A pagina cujo texto vai ao modelo (o trecho), se houver.
    pagina: str | None
    termos: tuple[str, ...] = ()
    saude_mental: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


def titulo_da_pagina(texto: str) -> str:
    """O titulo da pagina do gov.br: a primeira linha do miolo que nao e' o rotulo "Info"."""
    for linha in texto.splitlines():
        t = linha.strip()
        if t and t.lower() != "info":
            return t[:80]
    return ""


def nome_da_fonte(url: str, titulo: str) -> str:
    """O nome da fonte com o titulo da PAGINA (nao o do assunto): "Saude de A a Z — Herpes (Cobreiro)"."""
    if "/inca/" in url:
        return f"INCA — {titulo}"
    return f"Saúde de A a Z — {titulo} (Ministério da Saúde)"


def carregar_assuntos(caminho: Path = ASSUNTOS, titulos: dict[str, str] | None = None) -> list[AssuntoNovo]:
    """Os assuntos de `assuntos.yaml`. `titulos` (url -> titulo da pagina, de `paginas.json`) da' o nome
    certo de cada fonte; sem ele, o slug da URL."""
    titulos = titulos or {}
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    az, inca = dados["az"], dados["inca"]
    saida: list[AssuntoNovo] = []
    for item in dados["assuntos"]:
        aid, titulo, fonte = item["id"], item["titulo"], item.get("fonte")
        categoria = next(c for p, c in CATEGORIA_DO_PREFIXO.items() if aid.startswith(p))
        fontes: list[Fonte] = []
        pagina: str | None = None
        if fonte is None:
            fontes.append(Fonte(NOME_SEM_LINK, None))
        elif fonte == "ans":
            fontes.append(Fonte(NOME_ROL_ANS, URL_ROL_ANS))
        elif fonte == "calendario":
            pagina = URL_CALENDARIO
        elif str(fonte).startswith("inca:"):
            # A aba "Versao para populacao" (a pagina do tipo so' carrega as abas por script).
            pagina = f"{inca}/{str(fonte)[5:]}/versao-para-populacao"
        else:
            pagina = f"{az}/{fonte}"
        if pagina is not None and pagina != URL_CALENDARIO:
            slug = pagina.removesuffix("/versao-para-populacao").rsplit("/", 1)[-1]
            rotulo = titulos.get(pagina) or slug.replace("-", " ").capitalize()
            fontes.append(Fonte(nome_da_fonte(pagina, rotulo), pagina))
        if categoria == "vacina":
            fontes.append(Fonte(NOME_CALENDARIO, URL_CALENDARIO))
        if categoria == "cirurgia" and fonte != "ans":
            fontes.append(Fonte(NOME_ROL_ANS, URL_ROL_ANS))
        saida.append(
            AssuntoNovo(
                id=aid,
                titulo=titulo,
                grupo=item["grupo"],
                categoria=categoria,
                fontes=tuple(fontes),
                pagina=pagina,
                termos=tuple(item.get("termos") or ()),
                saude_mental=bool(item.get("saude_mental")),
            )
        )
    ids = [a.id for a in saida]
    if len(ids) != len(set(ids)):
        raise ValueError("id repetido em assuntos.yaml")
    return saida


def titulos_das_paginas(paginas: dict[str, Any]) -> dict[str, str]:
    return {url: titulo_da_pagina(str(p.get("texto") or "")) for url, p in paginas.items()}


def urls_para_conferir(assuntos: list[AssuntoNovo]) -> list[str]:
    return list(dict.fromkeys(f.url for a in assuntos for f in a.fontes if f.url))
