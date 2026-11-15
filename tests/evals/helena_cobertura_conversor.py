"""CONVERSOR do teste de uso do rco em golden evals da cobertura da Helena (DL-0167, HEL-6a).

Fontes (forma descrita pela ROOT a partir do rco `origin/main` 40b8df37; NENHUM conteudo real aqui):

- `validacao/teste-uso/fase-e/perguntas.yaml`: `perguntas: [{id, publico, pergunta}]`;
- `validacao/teste-uso/fase-e/gabarito.yaml`: `commit_referencia` e `gabarito: [{id, publico, dominio, tema,
  resposta_esperada, elementos_obrigatorios, fonte, regra_origem, secao_esperada, conferencia, armadilha,
  tipo_resposta?, o_que_seria_invencao}]`.

So' entra `publico: beneficiario` (a Helena atende o beneficiario). Cada caso vira um JSON
`EVL-HELENA-COB-<id>.json` (Tier A, deterministico), com a CAMADA de que depende (`camadas.yaml`: dominio ->
1 regra geral, 2 seu plano, 3 seu caso; dominio fora da tabela = `a_classificar`, que NAO conta como camada 1)
e o DESFECHO esperado:

- caso comum: `resposta` com todos os `elementos_obrigatorios` e nenhum trecho de `o_que_seria_invencao`;
- armadilha `nao_informa` / `publico_errado`: `transferir` (a Helena nao afirma, oferece uma pessoa e
  registra a lacuna); `depende_revisao_humana`: `transferir` ou a regra geral com a formula fixa (nao
  antecipa a decisao); `premissa_falsa`: `resposta` que corrige a premissa (os elementos dizem como).

Correcao (`avaliar`): correta = desfecho certo E (em `resposta`) todos os elementos presentes e nada de
invencao; INVENCAO = trecho de `o_que_seria_invencao` presente OU resposta cujo verificador de fatos nao
confere com as fontes. Corte (`corte`): >= 95% de acerto nos casos de camada 1 e ZERO invencao.

`o_que_seria_invencao` como TEXTO LIVRE nao e' verificavel por maquina: o conversor aceita lista de trechos
(o pedido ao rco) e, se vier texto, guarda como nota e conta so' o verificador de fatos.
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import yaml

FORMATO: Final[str] = "helena-cobertura-eval-v1"
Camada = Literal["1", "2", "3", "a_classificar"]
Desfecho = Literal["resposta", "transferir", "esclarecer", "aviso"]
#: Os desfechos ACEITOS por tipo de armadilha. `depende_revisao_humana`: transferir, ou a regra geral com a
#: formula fixa ("quem decide e' a analise do plano") — nao antecipar a decisao e' exatamente isso.
_TIPOS_DE_ARMADILHA: Final[dict[str, tuple[Desfecho, ...]]] = {
    "nao_informa": ("transferir",),
    "publico_errado": ("transferir",),
    "depende_revisao_humana": ("transferir", "resposta"),
    "premissa_falsa": ("resposta",),
}
_CAMPOS_DO_GABARITO: Final[frozenset[str]] = frozenset(
    {
        "id",
        "publico",
        "dominio",
        "tema",
        "resposta_esperada",
        "elementos_obrigatorios",
        "regra_origem",
        "armadilha",
    }
)
#: Acerto minimo nos casos de camada 1 e o teto de invencao (dono, Onda 1).
ACERTO_MINIMO: Final[float] = 0.95
INVENCAO_MAXIMA: Final[int] = 0


class FonteInvalidaError(ValueError):
    """As fontes do teste de uso nao estao na forma esperada (motivo curto; nunca o conteudo)."""


@dataclass(frozen=True)
class CasoDeCobertura:
    id: str
    pergunta: str
    camada: Camada
    dominio: str
    tema: str
    armadilha: bool
    tipo_resposta: str | None
    desfechos_aceitos: tuple[Desfecho, ...]
    regras_esperadas: tuple[str, ...]
    elementos_obrigatorios: tuple[str, ...]
    invencoes: tuple[str, ...]
    nota_de_invencao: str | None
    origem: Mapping[str, str] = field(default_factory=dict)

    def como_json(self) -> dict[str, Any]:
        dados = asdict(self)
        dados = {"formato": FORMATO, "agent": "helena", "eval_id": f"EVL-HELENA-COB-{self.id}", **dados}
        for chave in ("desfechos_aceitos", "regras_esperadas", "elementos_obrigatorios", "invencoes"):
            dados[chave] = list(dados[chave])
        dados["origem"] = dict(self.origem)
        return dados


def _yaml(caminho: Path) -> Any:
    try:
        return yaml.safe_load(caminho.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise FonteInvalidaError(f"{caminho.name}: ilegivel ({type(exc).__name__})") from None


def _textos(valor: object, *, campo: str, id_: str) -> tuple[str, ...]:
    if valor is None:
        return ()
    if not isinstance(valor, list) or not all(isinstance(v, str) and v.strip() for v in valor):
        raise FonteInvalidaError(f"{id_}: {campo} tem de ser lista de textos")
    return tuple(v.strip() for v in valor)


def carregar_camadas(caminho: Path) -> Mapping[str, Camada]:
    dados = _yaml(caminho)
    if not isinstance(dados, dict) or not isinstance(dados.get("camadas"), dict):
        raise FonteInvalidaError("camadas.yaml: mapeamento `camadas: {dominio: 1|2|3}`")
    saida: dict[str, Camada] = {}
    for dominio, camada in dados["camadas"].items():
        if str(camada) not in ("1", "2", "3"):
            raise FonteInvalidaError(f"camadas.yaml: {dominio} fora de 1, 2, 3")
        saida[str(dominio)] = str(camada)  # type: ignore[assignment]
    return saida


def converter(perguntas: Path, gabarito: Path, camadas: Mapping[str, Camada]) -> list[CasoDeCobertura]:
    """As perguntas do BENEFICIARIO com gabarito -> casos (ordem do arquivo de perguntas). Falha fechada."""
    bruto_p = _yaml(perguntas)
    bruto_g = _yaml(gabarito)
    if not isinstance(bruto_p, dict) or not isinstance(bruto_p.get("perguntas"), list):
        raise FonteInvalidaError("perguntas.yaml: `perguntas:` lista")
    if not isinstance(bruto_g, dict) or not isinstance(bruto_g.get("gabarito"), list):
        raise FonteInvalidaError("gabarito.yaml: `gabarito:` lista")
    commit = str(bruto_g.get("commit_referencia") or "")
    por_id: dict[str, dict[str, Any]] = {}
    for item in bruto_g["gabarito"]:
        if not isinstance(item, dict) or not set(item) >= _CAMPOS_DO_GABARITO:
            raise FonteInvalidaError("gabarito.yaml: item sem campo obrigatorio")
        if item["id"] in por_id:
            raise FonteInvalidaError(f"gabarito.yaml: id duplicado {item['id']}")
        por_id[str(item["id"])] = item
    casos: list[CasoDeCobertura] = []
    vistos: set[str] = set()
    for p in bruto_p["perguntas"]:
        if not isinstance(p, dict) or not {"id", "publico", "pergunta"} <= set(p):
            raise FonteInvalidaError("perguntas.yaml: item sem id, publico ou pergunta")
        id_ = str(p["id"])
        if id_ in vistos:
            raise FonteInvalidaError(f"perguntas.yaml: id duplicado {id_}")
        vistos.add(id_)
        if p["publico"] != "beneficiario":
            continue
        g = por_id.get(id_)
        if g is None:
            raise FonteInvalidaError(f"{id_}: pergunta sem gabarito")
        if g["publico"] != p["publico"]:
            raise FonteInvalidaError(f"{id_}: publico diverge entre pergunta e gabarito")
        armadilha = g["armadilha"] is True
        tipo = g.get("tipo_resposta")
        if armadilha and tipo not in _TIPOS_DE_ARMADILHA:
            raise FonteInvalidaError(f"{id_}: armadilha sem tipo_resposta conhecido")
        invencao = g.get("o_que_seria_invencao")
        casos.append(
            CasoDeCobertura(
                id=id_,
                pergunta=str(p["pergunta"]).strip(),
                camada=camadas.get(str(g["dominio"]), "a_classificar"),
                dominio=str(g["dominio"]),
                tema=str(g["tema"]),
                armadilha=armadilha,
                tipo_resposta=str(tipo) if armadilha else None,
                desfechos_aceitos=_TIPOS_DE_ARMADILHA[str(tipo)] if armadilha else ("resposta",),
                regras_esperadas=_textos(g["regra_origem"], campo="regra_origem", id_=id_),
                elementos_obrigatorios=_textos(
                    g["elementos_obrigatorios"], campo="elementos_obrigatorios", id_=id_
                ),
                invencoes=_textos(invencao, campo="o_que_seria_invencao", id_=id_)
                if isinstance(invencao, list)
                else (),
                nota_de_invencao=invencao.strip() if isinstance(invencao, str) and invencao.strip() else None,
                origem={"arquivo": "validacao/teste-uso/fase-e", "commit_referencia": commit},
            )
        )
    return casos


def _normal(texto: str) -> str:
    sem = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").casefold()
    return " ".join(sem.split())


@dataclass(frozen=True)
class Resultado:
    id: str
    camada: Camada
    correta: bool
    invencao: bool
    motivos: tuple[str, ...]


def avaliar(
    caso: CasoDeCobertura,
    *,
    desfecho: str,
    texto: str | None,
    regras_citadas: Sequence[str] = (),
    fatos_conferem: bool = True,
) -> Resultado:
    """O resultado de UM caso. `fatos_conferem` vem do verificador da camada (fatos x fontes)."""
    motivos: list[str] = []
    corpo = _normal(texto or "")
    invencao = desfecho == "resposta" and (
        not fatos_conferem or any(_normal(t) in corpo for t in caso.invencoes)
    )
    if invencao:
        motivos.append("invencao")
    if desfecho not in caso.desfechos_aceitos:
        motivos.append("desfecho")
    if desfecho == "resposta" and "resposta" in caso.desfechos_aceitos:
        if any(_normal(e) not in corpo for e in caso.elementos_obrigatorios):
            motivos.append("elemento_ausente")
        if caso.regras_esperadas and not set(regras_citadas) & set(caso.regras_esperadas):
            motivos.append("regra_errada")
    return Resultado(caso.id, caso.camada, not motivos, invencao, tuple(motivos))


@dataclass(frozen=True)
class Corte:
    casos_camada_1: int
    acertos_camada_1: int
    invencoes: int
    aprovado: bool

    @property
    def acerto(self) -> float:
        return self.acertos_camada_1 / self.casos_camada_1 if self.casos_camada_1 else 0.0


def corte(resultados: Sequence[Resultado]) -> Corte:
    """>= 95% de acerto na camada 1 e nenhuma invencao em NENHUM caso (invencao nao tem camada)."""
    camada_1 = [r for r in resultados if r.camada == "1"]
    acertos = sum(r.correta for r in camada_1)
    invencoes = sum(r.invencao for r in resultados)
    aprovado = bool(camada_1) and acertos / len(camada_1) >= ACERTO_MINIMO and invencoes <= INVENCAO_MAXIMA
    return Corte(len(camada_1), acertos, invencoes, aprovado)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--perguntas", type=Path, required=True)
    parser.add_argument("--gabarito", type=Path, required=True)
    parser.add_argument("--camadas", type=Path, required=True)
    parser.add_argument("--saida", type=Path, required=True)
    args = parser.parse_args(argv)
    casos = converter(args.perguntas, args.gabarito, carregar_camadas(args.camadas))
    args.saida.mkdir(parents=True, exist_ok=True)
    for caso in casos:
        destino = args.saida / f"EVL-HELENA-COB-{caso.id}.json"
        destino.write_text(
            json.dumps(caso.como_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"{len(casos)} casos de beneficiario convertidos em {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
