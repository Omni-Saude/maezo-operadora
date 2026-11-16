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

FONTE UNICA (HEL-6b): os golden passam a vir do `avaliacoes.yaml` DENTRO do pacote K2 pinado
(`converter_k2`), com as regras de cada avaliacao fixadas pelo mesmo commit e versao; o lock separado do
teste de uso foi cancelado. `converter` fica so' para as fontes sinteticas do resultado fixado. Gerar golden
de pacote NAO ratificado exige rodar com `MAEZO_ENV=dev` e `--aceitar-nao-ratificado` (o portao
`aceite_de_nao_ratificado` le' o ambiente ele mesmo; o CLI nunca o define).

PROXIMO PASSO (fora daqui): o corte de 95% sobre o K2 REAL entra quando o pin real chegar, com o pacote em
`config/integrations/rco/k2/` e o pin lido de `config/integrations/rco/k2.lock.json` por
`manual.pin_do_lock`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import yaml

from maezo.agents.helena.habilidades.cobertura.manual import (
    AvaliacaoDoManual,
    ManualK2,
    PacoteK2InvalidoError,
    PinDoK2,
    aceite_de_nao_ratificado,
    carregar,
)

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
#: `comportamento` da avaliacao do K2 -> `tipo_resposta` (os desfechos saem de `_TIPOS_DE_ARMADILHA`).
_COMPORTAMENTO_PARA_TIPO: Final[dict[str, str]] = {
    "corrige_premissa": "premissa_falsa",
    "declara_ausencia": "nao_informa",
    "identifica_publico": "publico_errado",
}
#: Dominio de avaliacao sem regra, ou com regras de dominios diferentes: nunca conta como camada 1.
A_CLASSIFICAR: Final[str] = "a_classificar"
ARQUIVO_K2: Final[str] = "k2/avaliacoes.yaml"
#: O id vira nome de arquivo (`EVL-HELENA-COB-<id>.json`): so' estes caracteres.
_ID_VALIDO: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]+$")
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


def _id_valido(id_: str) -> str:
    if not _ID_VALIDO.fullmatch(id_):
        raise FonteInvalidaError("id fora do padrao [A-Za-z0-9._-]")
    return id_


def carregar_camadas(caminho: Path) -> Mapping[str, Camada]:
    dados = _yaml(caminho)
    if not isinstance(dados, dict) or not isinstance(dados.get("camadas"), dict):
        raise FonteInvalidaError("camadas.yaml: mapeamento `camadas: {dominio: 1|2|3}`")
    saida: dict[str, Camada] = {}
    for dominio, camada in dados["camadas"].items():
        if str(dominio) == A_CLASSIFICAR:
            raise FonteInvalidaError(f"camadas.yaml: {A_CLASSIFICAR} e' reservado")
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
        id_ = _id_valido(str(p["id"]))
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


def _dominio_camada_tema(
    manual: ManualK2, av: AvaliacaoDoManual, camadas: Mapping[str, Camada]
) -> tuple[str, Camada, str]:
    """Dominio comum das regras de origem (senao `a_classificar`), a CAMADA comum (camada por camada: dominios
    diferentes na mesma camada ficam nela; vazio ou camadas diferentes -> `a_classificar`) e o titulo da
    primeira regra (ou ""). Dominio do pacote fora de `camadas.yaml` falha fechado (so' o codigo)."""
    regras = []
    for id_ in av.regra_origem:
        regra = manual.regras.get(id_)
        if regra is None:
            raise FonteInvalidaError(f"{av.id}: regra_origem fora do pacote")
        if regra.dominio not in camadas:
            raise FonteInvalidaError(f"dominio fora do camadas.yaml: {regra.dominio}")
        regras.append(regra)
    dominios = {r.dominio for r in regras}
    niveis = {camadas[r.dominio] for r in regras}
    dominio = dominios.pop() if len(dominios) == 1 else A_CLASSIFICAR
    camada: Camada = niveis.pop() if len(niveis) == 1 else "a_classificar"
    return dominio, camada, regras[0].titulo if regras else ""


def _caso_k2(manual: ManualK2, av: AvaliacaoDoManual, camadas: Mapping[str, Camada]) -> CasoDeCobertura:
    _id_valido(av.id)
    dominio, camada, tema = _dominio_camada_tema(manual, av, camadas)
    if av.comportamento is not None and av.comportamento not in _COMPORTAMENTO_PARA_TIPO:
        raise FonteInvalidaError(f"{av.id}: comportamento desconhecido")
    tipo = _COMPORTAMENTO_PARA_TIPO.get(av.comportamento) if av.comportamento is not None else None
    return CasoDeCobertura(
        id=av.id,
        pergunta=av.pergunta,
        camada=camada,
        dominio=dominio,
        tema=tema,
        armadilha=tipo is not None,
        tipo_resposta=tipo,
        desfechos_aceitos=_TIPOS_DE_ARMADILHA[tipo] if tipo is not None else ("resposta",),
        regras_esperadas=tuple(av.regra_origem),
        elementos_obrigatorios=tuple(av.elementos_obrigatorios),
        invencoes=tuple(av.o_que_seria_invencao),
        nota_de_invencao=None,
        origem={"arquivo": ARQUIVO_K2, "commit_referencia": manual.commit_rco, "versao": manual.versao},
    )


def converter_k2(manual: ManualK2, camadas: Mapping[str, Camada]) -> list[CasoDeCobertura]:
    """As avaliacoes do pacote K2 (ja' verificado por `carregar`) -> casos, na ordem do `avaliacoes.yaml`.
    O leitor ja' garante publico `beneficiario`, ids unicos e regras de origem existentes; aqui falham
    fechado o id fora do padrao de nome de arquivo e o dominio fora de `camadas.yaml`."""
    return [_caso_k2(manual, av, camadas) for av in manual.avaliacoes]


def como_texto(caso: CasoDeCobertura) -> str:
    """O conteudo EXATO do JSON versionado de um caso (o mesmo dump para as duas fontes)."""
    return json.dumps(caso.como_json(), ensure_ascii=False, indent=2) + "\n"


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
    """O resultado de UM caso. `fatos_conferem` vem do verificador da camada (fatos x fontes).

    Vale para QUALQUER desfecho (um `transferir` tambem pode inventar): trecho de invencao no texto, ou fatos
    que nao conferem com texto nao vazio (em `resposta`, sempre), e' invencao. Os elementos obrigatorios
    (decisao da ROOT) valem em `resposta` e no desfecho que e' o UNICO aceito (`nao_informa`/
    `publico_errado`: o `transferir` os cobra, estrito); onde ha' mais de um aceito
    (`depende_revisao_humana`), o outro desfecho so' responde por desfecho e invencao. A regra citada so' se
    cobra em `resposta`."""
    motivos: list[str] = []
    corpo = _normal(texto or "")
    invencao = any(_normal(t) in corpo for t in caso.invencoes) or (
        not fatos_conferem and (desfecho == "resposta" or bool(corpo))
    )
    if invencao:
        motivos.append("invencao")
    if desfecho not in caso.desfechos_aceitos:
        motivos.append("desfecho")
    else:
        cobra_elementos = desfecho == "resposta" or caso.desfechos_aceitos == (desfecho,)
        if cobra_elementos and any(_normal(e) not in corpo for e in caso.elementos_obrigatorios):
            motivos.append("elemento_ausente")
        if (
            desfecho == "resposta"
            and caso.regras_esperadas
            and not set(regras_citadas) & set(caso.regras_esperadas)
        ):
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


def corte(resultados: Sequence[Resultado], *, minimo_camada_1: int = 1) -> Corte:
    """>= 95% de acerto na camada 1, ao menos `minimo_camada_1` casos de camada 1 e nenhuma invencao em
    NENHUM caso (invencao nao tem camada)."""
    camada_1 = [r for r in resultados if r.camada == "1"]
    acertos = sum(r.correta for r in camada_1)
    invencoes = sum(r.invencao for r in resultados)
    aprovado = (
        len(camada_1) >= max(minimo_camada_1, 1)
        and acertos / len(camada_1) >= ACERTO_MINIMO
        and invencoes <= INVENCAO_MAXIMA
    )
    return Corte(len(camada_1), acertos, invencoes, aprovado)


def _argumentos(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    fonte = parser.add_mutually_exclusive_group(required=True)
    fonte.add_argument("--perguntas", type=Path, help="teste de uso sintetico (com --gabarito)")
    fonte.add_argument("--k2", type=Path, help="diretorio do pacote K2 (com --pin-versao e --pin-sha256)")
    parser.add_argument("--gabarito", type=Path)
    parser.add_argument("--pin-versao")
    parser.add_argument("--pin-sha256", help="sha256 dos bytes do manifesto.json (64 hex, sem prefixo)")
    parser.add_argument("--pin-commit", help="commit do rco (40 hex), conferido com o do manifesto")
    parser.add_argument(
        "--aceitar-nao-ratificado",
        action="store_true",
        help="pacote nao ratificado: so' vale com MAEZO_ENV=dev no ambiente (o CLI nao o define)",
    )
    parser.add_argument("--camadas", type=Path, required=True)
    parser.add_argument("--saida", type=Path, required=True)
    args = parser.parse_args(argv)
    so_k2 = (args.pin_versao, args.pin_sha256, args.pin_commit)
    if args.perguntas is not None and (
        args.gabarito is None or any(v is not None for v in so_k2) or args.aceitar_nao_ratificado
    ):
        parser.error("--perguntas exige --gabarito e nao aceita --pin-*/--aceitar-nao-ratificado")
    if args.k2 is not None and (args.gabarito is not None or not args.pin_versao or not args.pin_sha256):
        parser.error("--k2 exige --pin-versao e --pin-sha256 e nao aceita --gabarito")
    return args


def _casos_da_fonte(args: argparse.Namespace) -> list[CasoDeCobertura]:
    camadas = carregar_camadas(args.camadas)
    if args.k2 is None:
        return converter(args.perguntas, args.gabarito, camadas)
    pin = PinDoK2(versao=args.pin_versao, sha256_manifesto=args.pin_sha256, commit_rco=args.pin_commit)
    aceite = aceite_de_nao_ratificado(configurado=args.aceitar_nao_ratificado)
    return converter_k2(carregar(args.k2, pin=pin, aceite=aceite), camadas)


def main(argv: Sequence[str] | None = None) -> int:
    args = _argumentos(argv)
    try:
        casos = _casos_da_fonte(args)
    except PacoteK2InvalidoError as exc:
        print(f"pacote K2 recusado: {exc.motivo}", file=sys.stderr)
        return 2
    except FonteInvalidaError as exc:
        print(f"fonte invalida: {exc}", file=sys.stderr)
        return 2
    args.saida.mkdir(parents=True, exist_ok=True)
    # A saida e' o conjunto INTEIRO da fonte: golden de avaliacao que saiu da fonte nao fica para tras.
    for antigo in args.saida.glob("EVL-HELENA-COB-*.json"):
        antigo.unlink()
    for caso in casos:
        (args.saida / f"EVL-HELENA-COB-{caso.id}.json").write_text(como_texto(caso), encoding="utf-8")
    print(f"{len(casos)} casos de beneficiario convertidos em {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
