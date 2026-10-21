"""Contrato do ROTEADOR de habilidades da Helena (ADR-0066, DL-0134).

A fonte única é `spec/agents/helena/roteador.yaml`. Este módulo só CARREGA e VALIDA o contrato (forma, ordem
das travas, dono único de cada pendência); quem usa é o roteador (F1 em sombra, F2 ativo na allowlist). Um
contrato inválido reprova o carregamento inteiro — nada de contrato pela metade.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

#: As habilidades do desenho aprovado (ADR-0066), nesta ordem. Habilidade nova entra no contrato E aqui.
HABILIDADES: Final[tuple[str, ...]] = (
    "cobertura",
    "plano",
    "saude",
    "sintoma",
    "equipe",
    "pedidos",
    "cobranca",
    "conversa",
)
#: A saída do roteador quando não dá para decidir entre duas habilidades (vira a pergunta "X ou Y?").
DUVIDA: Final[str] = "duvida"
#: As travas de entrada, SEMPRE nesta ordem (ADR-0066 §1).
TRAVAS_DE_ENTRADA: Final[tuple[str, ...]] = (
    "consentimento_e_identificacao",
    "alarme_pela_tabela",
    "risco_psicossocial",
    "dados_pessoais",
)
TRAVAS_DE_SAIDA: Final[tuple[str, ...]] = (
    "cobertura_sem_promessa",
    "sem_dose_nem_diagnostico",
    "sem_link",
    "caso_so_com_sim",
    "nome_so_do_proprio_titular",
)
CONFIANCAS: Final[tuple[str, ...]] = ("alta", "media", "baixa")
MODOS: Final[tuple[str, ...]] = ("desligado", "sombra", "ativo")


class ContratoInvalidoError(ValueError):
    """O contrato do roteador não passou na validação."""


@dataclass(frozen=True)
class Habilidade:
    id: str
    titulo: str
    #: Uma linha para o prompt ENXUTO do roteador (roteador-v3).
    resumo: str
    descricao: str
    quando_usar: tuple[str, ...]
    quando_nao_usar: tuple[str, ...]
    exemplos: tuple[str, ...]
    pendencias: tuple[str, ...]
    processo: str | None


@dataclass(frozen=True)
class ContratoDoRoteador:
    versao: str
    habilidades: tuple[Habilidade, ...]
    travas_de_entrada: tuple[str, ...]
    travas_de_saida: tuple[str, ...]
    maximo_de_mensagens: int
    prazo_s: float
    texto_da_duvida: str

    def habilidade(self, habilidade_id: str) -> Habilidade:
        for h in self.habilidades:
            if h.id == habilidade_id:
                return h
        raise KeyError(habilidade_id)

    def dona_da_pendencia(self, codigo: str | None) -> str | None:
        """A habilidade dona de uma pergunta pendente (pelo prefixo do código), ou `None`."""
        if not codigo:
            return None
        for h in self.habilidades:
            if any(codigo.startswith(p) for p in h.pendencias):
                return h.id
        return None


def caminho_padrao() -> Path:
    from maezo.agents import resolve_spec_dir  # import tardio: evita ciclo no import do pacote

    return resolve_spec_dir() / "agents" / "helena" / "roteador.yaml"


def _textos(bruto: Any, campo: str, onde: str, *, minimo: int = 1) -> tuple[str, ...]:
    if not isinstance(bruto, Sequence) or isinstance(bruto, str):
        raise ContratoInvalidoError(f"{onde}: '{campo}' tem de ser uma lista")
    itens = tuple(str(x).strip() for x in bruto)
    if len(itens) < minimo or any(not x for x in itens):
        raise ContratoInvalidoError(f"{onde}: '{campo}' precisa de pelo menos {minimo} item(ns) não vazio(s)")
    return itens


def _habilidade(bruto: Mapping[str, Any]) -> Habilidade:
    hid = str(bruto.get("id") or "")
    onde = f"habilidade {hid or '?'}"
    for campo in ("titulo", "resumo", "descricao"):
        if not str(bruto.get(campo) or "").strip():
            raise ContratoInvalidoError(f"{onde}: '{campo}' vazio")
    pendencias = bruto.get("pendencias") or []
    return Habilidade(
        id=hid,
        titulo=str(bruto["titulo"]).strip(),
        resumo=str(bruto["resumo"]).strip(),
        descricao=str(bruto["descricao"]).strip(),
        quando_usar=_textos(bruto.get("quando_usar"), "quando_usar", onde),
        quando_nao_usar=_textos(bruto.get("quando_nao_usar"), "quando_nao_usar", onde),
        exemplos=_textos(bruto.get("exemplos"), "exemplos", onde, minimo=3),
        pendencias=_textos(pendencias, "pendencias", onde, minimo=0),
        processo=(str(bruto["processo"]) if bruto.get("processo") else None),
    )


def validar(bruto: Mapping[str, Any]) -> ContratoDoRoteador:
    """Valida o contrato já lido do YAML. Qualquer desvio reprova tudo."""
    chave = bruto.get("chave") or {}
    if tuple(chave.get("valores") or ()) != MODOS or chave.get("padrao") != "desligado":
        raise ContratoInvalidoError(
            "chave: valores têm de ser desligado|sombra|ativo, com padrão 'desligado'"
        )
    if chave.get("ativo_so_na_allowlist") is not True:
        raise ContratoInvalidoError("chave: o modo ativo vale só na allowlist")
    modelo = bruto.get("modelo") or {}
    if modelo.get("zona") != "phi_regional_sa_east_1":
        raise ContratoInvalidoError("modelo: o roteador roda só na zona PHI regional (ADR-0006/0017)")
    habilidades = tuple(_habilidade(h) for h in (bruto.get("habilidades") or ()))
    if tuple(h.id for h in habilidades) != HABILIDADES:
        raise ContratoInvalidoError(f"habilidades: esperado {HABILIDADES}")
    entrada = tuple(str(t.get("id")) for t in (bruto.get("travas_de_entrada") or ()))
    if entrada != TRAVAS_DE_ENTRADA:
        raise ContratoInvalidoError(f"travas_de_entrada: esperado {TRAVAS_DE_ENTRADA}, nesta ordem")
    saida = tuple(str(t.get("id")) for t in (bruto.get("travas_de_saida") or ()))
    if saida != TRAVAS_DE_SAIDA:
        raise ContratoInvalidoError(f"travas_de_saida: esperado {TRAVAS_DE_SAIDA}")
    prefixos = [p for h in habilidades for p in h.pendencias]
    for p in prefixos:
        donos = [o for o in prefixos if o != p and (o.startswith(p) or p.startswith(o))]
        if prefixos.count(p) > 1 or donos:
            raise ContratoInvalidoError(f"pendencias: o prefixo {p!r} tem mais de um dono")
    saida_cfg = bruto.get("saida") or {}
    texto = str(saida_cfg.get("texto_da_duvida") or "")
    if "{x}" not in texto or "{y}" not in texto:
        raise ContratoInvalidoError("saida: texto_da_duvida precisa de {x} e {y}")
    maximo = int(((bruto.get("entrada") or {}).get("mensagens") or {}).get("maximo") or 0)
    if not 2 <= maximo <= 12:
        raise ContratoInvalidoError("entrada: mensagens.maximo entre 2 e 12")
    return ContratoDoRoteador(
        versao=str(bruto.get("versao") or ""),
        habilidades=habilidades,
        travas_de_entrada=entrada,
        travas_de_saida=saida,
        maximo_de_mensagens=maximo,
        prazo_s=float(modelo.get("prazo_s") or 0),
        texto_da_duvida=texto,
    )


def carregar(caminho: Path | None = None) -> ContratoDoRoteador:
    with (caminho or caminho_padrao()).open(encoding="utf-8") as f:
        bruto = yaml.safe_load(f)
    if not isinstance(bruto, Mapping):
        raise ContratoInvalidoError("o contrato tem de ser um mapa YAML")
    return validar(bruto)


__all__ = [
    "CONFIANCAS",
    "DUVIDA",
    "HABILIDADES",
    "MODOS",
    "TRAVAS_DE_ENTRADA",
    "TRAVAS_DE_SAIDA",
    "ContratoDoRoteador",
    "ContratoInvalidoError",
    "Habilidade",
    "caminho_padrao",
    "carregar",
    "validar",
]
