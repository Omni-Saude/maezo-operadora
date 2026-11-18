"""E-SF-25 (EP-REG-12a, TB-E-11-a): banco de questionario da declaracao de saude, so' esqueleto e lint.

O que e': a forma FECHADA de um banco de perguntas (`spec/health/question-banks/*.yaml`) e o lint que
`make validate-artifacts` roda sobre ele. Referencia de comportamento: o contrato proposto de onboarding de
saude do portal (questionario com secoes, perguntas, conceito, opcoes e dependencia).

O que nao e':
  * nao e' banco real. O conteudo das perguntas e' do juridico e do medico auditor (aprovacao pergunta a
    pergunta). Aqui so' existe banco SINTETICO, com aprovacoes de prefixo sintetico e perguntas marcadas
    `[SINTETICO]`; o carregamento para uso (`carregar_para_uso`) recusa banco sintetico e banco cuja aprovacao
    nao resolve no registro de aprovacoes, que hoje e' VAZIO: nenhum banco carrega;
  * nao guarda resposta nem dado de saude: so' a estrutura das perguntas;
  * nao tem Agravo (decisao: Agravo nao e' construido). Orientacao que fale de Agravo e' recusada.

Regras (codigo da recusa entre parenteses):
  1. modo `declaration`: toda pergunta tem conceito `known_condition` (`DS_CONCEPT_OUT_OF_SCOPE`);
  2. aprovacao clinica e regulatoria: banco real so' carrega com as duas referencias resolvidas no registro
     injetado (`DS_APPROVAL_UNRESOLVED`); prefixo sintetico em banco real recusa (`DS_APPROVAL_SYNTHETIC`);
  3. orientacao (`guidanceText`) com as secoes obrigatorias de CPT e de consequencias da omissao
     (`DS_GUIDANCE_SECTION_MISSING`) e sem Agravo (`DS_AGRAVO_NOT_BUILT`);
  4. forma fechada: chaves exatas, referencias no formato, tipos e limites, opcoes coerentes com o tipo,
     dependencia so' de pergunta de escolha anterior e de opcao existente (`DS_SHAPE_INVALID`);
  5. banco sintetico: `synthetic: true`, aprovacoes com prefixo sintetico e toda pergunta marcada
     `[SINTETICO]` (`DS_SYNTHETIC_UNMARKED`); banco real com essa marca recusa (`DS_SYNTHETIC_IN_REAL_BANK`);
  6. texto com controle ou formatacao invisivel recusa (`DS_SHAPE_INVALID`), e a pasta so' aceita `.yaml` de
     topo.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any, Final

import yaml

from .result import Report

__all__ = [
    "APROVACOES_PUBLICADAS",
    "CONCEITOS",
    "MODOS",
    "PREFIXO_SINTETICO",
    "SECOES_OBRIGATORIAS",
    "BancoRecusadoError",
    "carregar_para_uso",
    "problemas_do_banco",
    "validate_dir",
]

SCHEMA: Final = "maezo.health.question-bank.v0"
MODOS: Final[frozenset[str]] = frozenset({"declaration", "care"})
CONCEITOS: Final[frozenset[str]] = frozenset(
    {"known_condition", "care_priority", "history", "treatment", "daily_life", "screening"}
)
CONCEITO_DA_DECLARACAO: Final = "known_condition"
TIPOS: Final[frozenset[str]] = frozenset({"choice", "text"})
PREFIXO_SINTETICO: Final = "SINTETICO-"
MARCA_SINTETICA: Final = "[SINTETICO]"
#: Cabecalhos obrigatorios da orientacao (estrutura; o texto de cada secao e' do juridico).
SECOES_OBRIGATORIAS: Final[tuple[str, ...]] = (
    "## Cobertura parcial temporaria (CPT)",
    "## Consequencias da omissao",
)
#: Registro de aprovacoes publicadas (clinica e regulatoria). VAZIO: nenhum banco real foi aprovado.
APROVACOES_PUBLICADAS: Final[frozenset[str]] = frozenset()

_REF: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,255}")
_AGRAVO: Final = re.compile(r"agravo", re.IGNORECASE)
_CHAVES_BANCO: Final = frozenset(
    {
        "schema",
        "ref",
        "version",
        "mode",
        "synthetic",
        "title",
        "clinicalApprovalRef",
        "regulatoryApprovalRef",
        "guidanceRef",
        "guidanceText",
        "sections",
    }
)
_CHAVES_SECAO: Final = frozenset({"ref", "title", "questions"})
_CHAVES_PERGUNTA: Final = frozenset(
    {"ref", "prompt", "help", "kind", "concept", "options", "required", "dependsOn"}
)
_CHAVES_OPCAO: Final = frozenset({"value", "label"})
_CHAVES_DEPENDENCIA: Final = frozenset({"questionRef", "value"})


class BancoRecusadoError(ValueError):
    """O banco nao pode ser usado. `problemas` traz os codigos com o lugar."""

    def __init__(self, problemas: list[str]) -> None:
        super().__init__("; ".join(problemas))
        self.problemas = problemas


def _ref(valor: object) -> bool:
    return isinstance(valor, str) and _REF.fullmatch(valor) is not None


def _caractere_permitido(c: str) -> bool:
    if c in "\t\n\r":
        return True
    # Controles (C0, DEL, C1) e formatacao invisivel (bidi, largura zero): recusados.
    return unicodedata.category(c) not in {"Cc", "Cf"}


def _texto(valor: object, maximo: int = 2000) -> bool:
    return isinstance(valor, str) and len(valor) <= maximo and all(_caractere_permitido(c) for c in valor)


def _membro(valor: object, conjunto: Collection[str]) -> bool:
    return isinstance(valor, str) and valor in conjunto


def _normal(texto: str) -> str:
    """Sem acento, minusculo, sem espaco no fim: para comparar cabecalho e procurar Agravo."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return sem_acento.casefold().rstrip()


def _forma(valor: object, chaves: frozenset[str]) -> bool:
    return isinstance(valor, dict) and set(valor) == chaves


def _perguntas(banco: Mapping[str, Any], problemas: list[str]) -> list[Mapping[str, Any]]:
    secoes = banco.get("sections")
    if not isinstance(secoes, list) or not 1 <= len(secoes) <= 20:
        problemas.append("DS_SHAPE_INVALID:sections")
        return []
    vistas: dict[str, Mapping[str, Any]] = {}
    refs_secao: set[str] = set()
    for i, secao in enumerate(secoes):
        onde = f"sections[{i}]"
        if not _forma(secao, _CHAVES_SECAO) or not _ref(secao["ref"]) or not _texto(secao["title"], 160):
            problemas.append(f"DS_SHAPE_INVALID:{onde}")
            continue
        if secao["ref"] in refs_secao:
            problemas.append(f"DS_SHAPE_INVALID:{onde}.ref")
        refs_secao.add(secao["ref"])
        perguntas = secao["questions"]
        if not isinstance(perguntas, list) or not 1 <= len(perguntas) <= 100:
            problemas.append(f"DS_SHAPE_INVALID:{onde}.questions")
            continue
        for j, pergunta in enumerate(perguntas):
            _pergunta(pergunta, f"{onde}.questions[{j}]", vistas, problemas)
    if len(vistas) > 200:
        problemas.append("DS_SHAPE_INVALID:questions")
    return list(vistas.values())


def _pergunta(p: object, onde: str, vistas: dict[str, Mapping[str, Any]], problemas: list[str]) -> None:
    if not (
        _forma(p, _CHAVES_PERGUNTA)
        and isinstance(p, dict)
        and _ref(p["ref"])
        and p["ref"] not in vistas
        and _texto(p["prompt"], 1000)
        and _texto(p["help"])
        and _membro(p["kind"], TIPOS)
        and type(p["required"]) is bool
    ):
        problemas.append(f"DS_SHAPE_INVALID:{onde}")
        return
    if not _membro(p["concept"], CONCEITOS):
        problemas.append(f"DS_SHAPE_INVALID:{onde}.concept")
        return
    opcoes = p["options"]
    valores: set[str] = set()
    if not isinstance(opcoes, list) or len(opcoes) > 30:
        problemas.append(f"DS_SHAPE_INVALID:{onde}.options")
        return
    for k, opcao in enumerate(opcoes):
        if not _forma(opcao, _CHAVES_OPCAO) or not _ref(opcao["value"]) or not _texto(opcao["label"], 160):
            problemas.append(f"DS_SHAPE_INVALID:{onde}.options[{k}]")
            return
        if opcao["value"] in valores:
            problemas.append(f"DS_SHAPE_INVALID:{onde}.options[{k}]")
            return
        valores.add(opcao["value"])
    if (p["kind"] == "choice" and len(valores) < 2) or (p["kind"] == "text" and valores):
        problemas.append(f"DS_SHAPE_INVALID:{onde}.options")
        return
    dep = p["dependsOn"]
    if dep is not None:
        pai_ref = dep.get("questionRef") if isinstance(dep, dict) else None
        pai = vistas.get(pai_ref) if isinstance(pai_ref, str) else None
        if (
            not _forma(dep, _CHAVES_DEPENDENCIA)
            or pai is None
            or pai["kind"] != "choice"
            or not _membro(dep["value"], {o["value"] for o in pai["options"]})
        ):
            problemas.append(f"DS_SHAPE_INVALID:{onde}.dependsOn")
            return
    vistas[p["ref"]] = p


def _textos_do_banco(banco: Mapping[str, Any], perguntas: list[Mapping[str, Any]]) -> list[str]:
    textos = [str(banco["title"]), str(banco["guidanceText"])]
    secoes = banco["sections"] if isinstance(banco["sections"], list) else []
    for secao in secoes:
        if isinstance(secao, dict):
            textos.append(str(secao.get("title", "")))
    for p in perguntas:
        textos += [str(p["prompt"]), str(p["help"]), *(str(o["label"]) for o in p["options"])]
    return textos


def problemas_do_banco(banco: object, *, aprovacoes: Collection[str] = APROVACOES_PUBLICADAS) -> list[str]:
    """Os codigos de recusa do banco (vazio = banco valido para o seu tipo, sintetico ou real)."""
    if not _forma(banco, _CHAVES_BANCO) or not isinstance(banco, dict):
        return ["DS_SHAPE_INVALID:bank"]
    problemas: list[str] = []
    if not (
        banco["schema"] == SCHEMA
        and _ref(banco["ref"])
        and _ref(banco["version"])
        and _membro(banco["mode"], MODOS)
        and type(banco["synthetic"]) is bool
        and _texto(banco["title"], 160)
        and _ref(banco["guidanceRef"])
        and _texto(banco["guidanceText"], 12000)
    ):
        problemas.append("DS_SHAPE_INVALID:bank")
        return problemas
    perguntas = _perguntas(banco, problemas)

    if banco["mode"] == "declaration":
        for p in perguntas:
            if p["concept"] != CONCEITO_DA_DECLARACAO:
                problemas.append(f"DS_CONCEPT_OUT_OF_SCOPE:{p['ref']}")

    texto = banco["guidanceText"]
    linhas = {_normal(linha) for linha in texto.splitlines()}
    for secao in SECOES_OBRIGATORIAS:
        if _normal(secao) not in linhas:
            problemas.append(f"DS_GUIDANCE_SECTION_MISSING:{secao}")
    if any(_AGRAVO.search(_normal(t)) for t in _textos_do_banco(banco, perguntas)):
        problemas.append("DS_AGRAVO_NOT_BUILT")

    refs = (banco["clinicalApprovalRef"], banco["regulatoryApprovalRef"])
    if banco["synthetic"]:
        if not all(_ref(r) and str(r).startswith(PREFIXO_SINTETICO) for r in refs) or not all(
            str(p["prompt"]).startswith(MARCA_SINTETICA) for p in perguntas
        ):
            problemas.append("DS_SYNTHETIC_UNMARKED")
    else:
        if any(str(p["prompt"]).startswith(MARCA_SINTETICA) for p in perguntas):
            problemas.append("DS_SYNTHETIC_IN_REAL_BANK")
        for nome, r in zip(("clinicalApprovalRef", "regulatoryApprovalRef"), refs, strict=True):
            if not _ref(r):
                problemas.append(f"DS_APPROVAL_UNRESOLVED:{nome}")
            elif str(r).startswith(PREFIXO_SINTETICO):
                problemas.append(f"DS_APPROVAL_SYNTHETIC:{nome}")
            elif r not in aprovacoes:
                problemas.append(f"DS_APPROVAL_UNRESOLVED:{nome}")
    return problemas


def carregar_para_uso(
    banco: object, *, aprovacoes: Collection[str] = APROVACOES_PUBLICADAS
) -> Mapping[str, Any]:
    """O banco pronto para uso, ou `BancoRecusadoError`. Banco sintetico nunca carrega para uso."""
    problemas = problemas_do_banco(banco, aprovacoes=aprovacoes)
    if isinstance(banco, dict) and banco.get("synthetic") is not False:
        problemas.append("DS_SYNTHETIC_NOT_LOADABLE")
    if problemas:
        raise BancoRecusadoError(problemas)
    return banco  # type: ignore[return-value]


def validate_dir(path: Path, report: Report) -> None:
    """Lint de `spec/health/question-banks/`: todo `.yaml` tem de ser um banco valido (sintetico ou real).

    Qualquer outro arquivo ou subpasta e' recusado: banco `.yml` ou aninhado nao escapa do lint."""
    for intruso in sorted(p for p in path.iterdir() if not (p.is_file() and p.suffix == ".yaml")):
        report.error(intruso, "DS_SHAPE_INVALID:not_a_bank_file (only top-level .yaml banks are allowed)")
    arquivos = sorted(path.glob("*.yaml"))
    if not arquivos:
        report.error(path, "no question bank .yaml files found")
    for arquivo in arquivos:
        try:
            banco = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            report.error(arquivo, f"DS_SHAPE_INVALID:unreadable ({type(exc).__name__})")
            continue
        for problema in problemas_do_banco(banco):
            report.error(arquivo, problema)
