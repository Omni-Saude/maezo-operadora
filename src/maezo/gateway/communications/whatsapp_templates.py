"""Registro FECHADO de templates WhatsApp (E-SF-13 / GTM-E-42): esquema, carga e validacao.

O que e'. O leitor unico de `spec/communications/whatsapp-templates.yaml`: finalidades (e se estao
ratificadas), templates (nome Meta, idioma, `meta_status`, `meta_template_ref`, `purpose_ref`,
`content_review_ref`, `publicity`, `variables[{pos, class}]`), o vocabulario FECHADO de classes de
variavel, os textos de permissao de contato publicados (aceite OS-05 / termo E-SF-62) e os tipos de
titular com oposicao executavel. O `OutboundGate` (`outbound_gate.py`) le' so' isto; o validador de CI
(`scripts/ci/check_whatsapp_templates.py`) chama `validar_registro` sobre o mesmo arquivo, entao o
esquema tem UMA implementacao.

Fechado por construcao:

* classe de variavel fora do vocabulario -> recusa (`VARIABLE_CLASS_UNKNOWN`);
* classe de variavel de saude (CPT, DS/DPP, CID, condicao, recusa por saude, utilizacao assistencial) ->
  recusa (`VARIABLE_CLASS_HEALTH`), mesmo que alguem a escreva no vocabulario;
* corpo de template com termo de saude -> recusa (`BODY_HEALTH_CONTENT`);
* `meta_status: approved` exige `meta_template_ref`, `content_review_ref`, corpo e `publicity` definidos;
* `ratified: true` exige `ratification_ref`.

O que este modulo NAO decide: a taxonomia final de classes (DPO; `vendor_notice.py` registra o mesmo
residuo) e qualquer ratificacao. O arquivo publicado tem ZERO finalidades ratificadas, ZERO templates
aprovados, ZERO textos de permissao e ZERO tipos de titular com oposicao executavel — o teste
`tests/unit/gateway/test_esf13_template_registry.py` fixa esse estado.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

SCHEMA: Final[str] = "maezo.whatsapp-templates.v1"

#: Caminho no checkout do repositorio (`<repo>/spec/communications/...`). A imagem de producao NAO
#: empacota `spec/communications` (`deploy/Dockerfile` copia so' subarvores nomeadas): la' o arquivo nao
#: existe e o registro carregado e' o VAZIO — que recusa todo envio proativo.
CAMINHO_PADRAO: Final[Path] = (
    Path(__file__).resolve().parents[4] / "spec" / "communications" / "whatsapp-templates.yaml"
)

META_STATUS: Final[frozenset[str]] = frozenset(
    {"not_submitted", "unverified", "submitted", "approved", "rejected", "paused"}
)
TIPOS_DE_CLASSE: Final[frozenset[str]] = frozenset({"nao_pessoal", "pessoal_contratual"})
TIPOS_DE_TITULAR: Final[frozenset[str]] = frozenset({"beneficiario", "lead", "prospect"})
CATEGORIAS_META: Final[frozenset[str]] = frozenset({"utility", "marketing", "authentication"})

#: Classes de variavel PROIBIDAS em qualquer mensagem (ADD-A9-CONSENT §4(b) "Cerca zero dado de saude";
#: GT-2). Nao e' o vocabulario aceito: e' a lista que nunca pode entrar nele.
CLASSES_DE_SAUDE: Final[frozenset[str]] = frozenset(
    {
        "cpt",
        "cobertura_parcial_temporaria",
        "agravo",
        "dps",
        "dps_status",
        "ds",
        "declaracao_saude",
        "dpp",
        "doenca_preexistente",
        "stage",
        "etapa_ds",
        "cid",
        "cid10",
        "condicao",
        "condicao_saude",
        "diagnostico",
        "recusa_motivo",
        "utilizacao_assistencial",
        "internacao",
        "sinistro",
        "guia",
        "autorizacao",
        "procedimento",
        "programa_cuidado",
    }
)

#: Termos de saude no TEXTO (corpo de template ou valor de variavel). Casam por palavra, sem acento e sem
#: caixa. Mais o padrao de CID-10 (letra + 2 digitos, subcategoria opcional).
TERMOS_DE_SAUDE: Final[tuple[str, ...]] = (
    "cpt",
    "cobertura parcial temporaria",
    "carencia de cpt",
    "agravo",
    "dps",
    "declaracao de saude",
    "dpp",
    "doenca preexistente",
    "doencas preexistentes",
    "recusa motivo",
    "cid",
    "diagnostico",
    "condicao de saude",
    "internacao",
    "sinistro",
    "utilizacao assistencial",
    "recusa por saude",
    "motivo de saude",
)
_CID10: Final[re.Pattern[str]] = re.compile(r"\b[A-TV-Z][0-9]{2}(?:\.[0-9A-Z]{1,2})?\b", re.IGNORECASE)
_NOME_META: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9_]{1,512}$")
_ID: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_.]*$")
_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"\{\{(\d+)\}\}")
_SHA256: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")

#: sha256 do texto do consentimento de ATENDIMENTO (`wa-consent-v2`, "nesta conversa"). COPIADO de
#: `maezo.platform.webhooks.whatsapp.acesso.SHA256_TEXTO_CONSENTIMENTO` para o gateway nao importar a
#: plataforma; `tests/unit/gateway/test_esf13_template_registry.py` fixa a igualdade. Esse texto nunca pode
#: ser publicado como permissao de contato proativo, sob nenhuma versao (A9 GT-3; RT-D4).
SHA256_TEXTO_CONSENTIMENTO_ATENDIMENTO: Final[str] = (
    "3f68f5bb01f8b95a10020b02aae49f43d2359f49fdc884a5af405fa4f4387a90"
)
PREFIXO_VERSAO_CONSENTIMENTO_ATENDIMENTO: Final[str] = "wa-consent"

#: Finalidades GENERICAS: um texto de permissao que so' nomeia isto nao nomeia finalidade (GT-3).
FINALIDADES_GENERICAS: Final[frozenset[str]] = frozenset({"comercial", "marketing", "contato", "geral"})


def _normalizar(texto: str) -> str:
    import unicodedata

    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower()


def contem_dado_de_saude(texto: str) -> bool:
    """`True` se o texto carrega termo de saude ou padrao de CID-10. Usado no corpo e nos valores."""
    if _CID10.search(texto):
        return True
    norm = re.sub(r"[=_\-:]", " ", _normalizar(texto))
    return any(re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", norm) for t in TERMOS_DE_SAUDE)


class RegistroInvalidoError(ValueError):
    """O registro nao passa no esquema. `problemas` sao tokens `CODIGO:onde` (nunca valores)."""

    def __init__(self, problemas: Sequence[str]) -> None:
        self.problemas = tuple(problemas)
        super().__init__("; ".join(self.problemas))


@dataclass(frozen=True, slots=True)
class ClasseDeVariavel:
    id: str
    tipo: str


@dataclass(frozen=True, slots=True)
class Variavel:
    pos: int
    classe: str


@dataclass(frozen=True, slots=True)
class Finalidade:
    id: str
    ratificada: bool
    ratification_ref: str | None


@dataclass(frozen=True, slots=True)
class TextoDePermissao:
    """Texto de permissao de contato PUBLICADO (aceite OS-05 / termo E-SF-62): versao, sha e finalidades."""

    versao: str
    sha256: str
    finalidades: tuple[str, ...]
    canal: str


@dataclass(frozen=True, slots=True)
class Template:
    id: str
    name: str
    language: str
    category: str
    meta_status: str
    meta_template_ref: str | None
    purpose_ref: str
    subject_kind: str
    content_review_ref: str | None
    publicity: bool | None
    body: str | None
    variables: tuple[Variavel, ...]

    @property
    def aprovado(self) -> bool:
        return (
            self.meta_status == "approved"
            and self.meta_template_ref is not None
            and self.content_review_ref is not None
        )


@dataclass(frozen=True, slots=True)
class RegistroDeTemplates:
    """O registro validado. O VAZIO (`RegistroDeTemplates()`) recusa tudo."""

    finalidades: Mapping[str, Finalidade] = field(default_factory=dict)
    templates: Mapping[str, Template] = field(default_factory=dict)
    classes: Mapping[str, ClasseDeVariavel] = field(default_factory=dict)
    textos_de_permissao: tuple[TextoDePermissao, ...] = ()
    oposicao_executavel_para: frozenset[str] = frozenset()
    taxonomia_ratificada: bool = False

    def finalidade_ratificada(self, finalidade: str) -> bool:
        f = self.finalidades.get(finalidade)
        return f is not None and f.ratificada and bool(f.ratification_ref)


_CAMPOS_RAIZ: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "versao",
        "variable_classes",
        "purposes",
        "contact_permission_texts",
        "opposition_executable_subject_kinds",
        "variable_class_taxonomy_ratified",
        "templates",
    }
)
_CAMPOS_TEMPLATE: Final[frozenset[str]] = frozenset(
    {
        "id",
        "name",
        "language",
        "category",
        "meta_status",
        "meta_template_ref",
        "purpose_ref",
        "subject_kind",
        "content_review_ref",
        "publicity",
        "body",
        "variables",
        "origem",
        "nota",
    }
)
_CAMPOS_TEMPLATE_OBRIGATORIOS: Final[frozenset[str]] = _CAMPOS_TEMPLATE - {"origem", "nota"}


def _texto_ou_nulo(valor: object) -> bool:
    return valor is None or (isinstance(valor, str) and bool(valor.strip()))


def _validar_classes(bruto: object, problemas: list[str]) -> dict[str, ClasseDeVariavel]:
    classes: dict[str, ClasseDeVariavel] = {}
    if not isinstance(bruto, list):
        problemas.append("ROOT_FIELD_INVALID:variable_classes")
        return classes
    for i, c in enumerate(bruto):
        if (
            not isinstance(c, dict)
            or not isinstance(c.get("id"), str)
            or c.get("tipo") not in TIPOS_DE_CLASSE
        ):
            problemas.append(f"VARIABLE_CLASS_INVALID:variable_classes[{i}]")
            continue
        cid = c["id"]
        if cid in CLASSES_DE_SAUDE or contem_dado_de_saude(cid.replace("_", " ")):
            problemas.append(f"VARIABLE_CLASS_HEALTH:variable_classes[{i}]")
            continue
        if cid in classes:
            problemas.append(f"DUPLICATE_ID:variable_classes[{i}]")
            continue
        classes[cid] = ClasseDeVariavel(id=cid, tipo=c["tipo"])
    return classes


def _validar_finalidades(bruto: object, problemas: list[str]) -> dict[str, Finalidade]:
    finalidades: dict[str, Finalidade] = {}
    if not isinstance(bruto, list):
        problemas.append("ROOT_FIELD_INVALID:purposes")
        return finalidades
    for i, p in enumerate(bruto):
        if (
            not isinstance(p, dict)
            or not isinstance(p.get("id"), str)
            or not _ID.match(p["id"])
            or not isinstance(p.get("ratified"), bool)
            or not _texto_ou_nulo(p.get("ratification_ref"))
        ):
            problemas.append(f"PURPOSE_INVALID:purposes[{i}]")
            continue
        if p["ratified"] and not p.get("ratification_ref"):
            problemas.append(f"PURPOSE_RATIFIED_WITHOUT_REF:purposes[{i}]")
            continue
        if p["id"] in finalidades:
            problemas.append(f"DUPLICATE_ID:purposes[{i}]")
            continue
        finalidades[p["id"]] = Finalidade(
            id=p["id"], ratificada=p["ratified"], ratification_ref=p.get("ratification_ref")
        )
    return finalidades


def _validar_textos(bruto: object, problemas: list[str]) -> tuple[TextoDePermissao, ...]:
    if not isinstance(bruto, list):
        problemas.append("ROOT_FIELD_INVALID:contact_permission_texts")
        return ()
    textos: list[TextoDePermissao] = []
    for i, t in enumerate(bruto):
        if (
            not isinstance(t, dict)
            or not isinstance(t.get("version"), str)
            or not isinstance(t.get("sha256"), str)
            or not _SHA256.match(t["sha256"])
            or not isinstance(t.get("purposes"), list)
            or not all(isinstance(x, str) and x for x in t["purposes"])
            or t.get("channel") != "whatsapp"
        ):
            problemas.append(f"PERMISSION_TEXT_INVALID:contact_permission_texts[{i}]")
            continue
        if (
            t["version"].startswith(PREFIXO_VERSAO_CONSENTIMENTO_ATENDIMENTO)
            or t["sha256"] == SHA256_TEXTO_CONSENTIMENTO_ATENDIMENTO
        ):
            problemas.append(f"PERMISSION_TEXT_IS_SERVICE_CONSENT:contact_permission_texts[{i}]")
            continue
        textos.append(
            TextoDePermissao(
                versao=t["version"], sha256=t["sha256"], finalidades=tuple(t["purposes"]), canal="whatsapp"
            )
        )
    return tuple(textos)


def _validar_variaveis(
    bruto: object, onde: str, classes: Mapping[str, ClasseDeVariavel], problemas: list[str]
) -> tuple[Variavel, ...]:
    if not isinstance(bruto, list):
        problemas.append(f"TEMPLATE_VARIABLES_INVALID:{onde}")
        return ()
    variaveis: list[Variavel] = []
    for j, v in enumerate(bruto):
        if not isinstance(v, dict) or set(v) != {"pos", "class"} or not isinstance(v["pos"], int):
            problemas.append(f"TEMPLATE_VARIABLES_INVALID:{onde}.variables[{j}]")
            continue
        classe = v["class"]
        if not isinstance(classe, str):
            problemas.append(f"TEMPLATE_VARIABLES_INVALID:{onde}.variables[{j}]")
            continue
        if classe in CLASSES_DE_SAUDE or contem_dado_de_saude(classe.replace("_", " ")):
            problemas.append(f"VARIABLE_CLASS_HEALTH:{onde}.variables[{j}]")
            continue
        if classe not in classes:
            problemas.append(f"VARIABLE_CLASS_UNKNOWN:{onde}.variables[{j}]")
            continue
        variaveis.append(Variavel(pos=v["pos"], classe=classe))
    if [v.pos for v in variaveis] != list(range(1, len(variaveis) + 1)):
        problemas.append(f"TEMPLATE_VARIABLES_NOT_CONTIGUOUS:{onde}")
    return tuple(variaveis)


def _validar_template(
    t: object,
    i: int,
    *,
    classes: Mapping[str, ClasseDeVariavel],
    finalidades: Mapping[str, Finalidade],
    problemas: list[str],
) -> Template | None:
    onde = f"templates[{i}]"
    if (
        not isinstance(t, dict)
        or not set(t) >= _CAMPOS_TEMPLATE_OBRIGATORIOS
        or not set(t) <= _CAMPOS_TEMPLATE
    ):
        problemas.append(f"TEMPLATE_FIELDS_INVALID:{onde}")
        return None
    antes = len(problemas)
    if not isinstance(t["id"], str) or not _ID.match(t["id"]):
        problemas.append(f"TEMPLATE_ID_INVALID:{onde}")
    if not isinstance(t["name"], str) or not _NOME_META.match(t["name"]):
        problemas.append(f"TEMPLATE_NAME_INVALID:{onde}")
    if t["language"] != "pt_BR":
        problemas.append(f"TEMPLATE_LANGUAGE_INVALID:{onde}")
    if t["category"] not in CATEGORIAS_META:
        problemas.append(f"TEMPLATE_CATEGORY_INVALID:{onde}")
    if t["meta_status"] not in META_STATUS:
        problemas.append(f"TEMPLATE_META_STATUS_INVALID:{onde}")
    if t["subject_kind"] not in TIPOS_DE_TITULAR:
        problemas.append(f"TEMPLATE_SUBJECT_KIND_INVALID:{onde}")
    if t["purpose_ref"] not in finalidades:
        problemas.append(f"TEMPLATE_PURPOSE_UNKNOWN:{onde}")
    for campo in ("meta_template_ref", "content_review_ref", "body"):
        if not _texto_ou_nulo(t[campo]):
            problemas.append(f"TEMPLATE_FIELD_INVALID:{onde}.{campo}")
    if t["publicity"] is not None and not isinstance(t["publicity"], bool):
        problemas.append(f"TEMPLATE_FIELD_INVALID:{onde}.publicity")
    variaveis = _validar_variaveis(t["variables"], onde, classes, problemas)
    body = t["body"]
    if isinstance(body, str):
        if contem_dado_de_saude(body):
            problemas.append(f"BODY_HEALTH_CONTENT:{onde}")
        posicoes = sorted({int(n) for n in _PLACEHOLDER.findall(body)})
        if posicoes != [v.pos for v in variaveis]:
            problemas.append(f"TEMPLATE_PLACEHOLDERS_MISMATCH:{onde}")
    elif variaveis:
        problemas.append(f"TEMPLATE_VARIABLES_WITHOUT_BODY:{onde}")
    if t["meta_status"] == "approved" and (
        t["meta_template_ref"] is None
        or t["content_review_ref"] is None
        or body is None
        or t["publicity"] is None
    ):
        problemas.append(f"TEMPLATE_APPROVED_INCOMPLETE:{onde}")
    if len(problemas) != antes:
        return None
    return Template(
        id=t["id"],
        name=t["name"],
        language=t["language"],
        category=t["category"],
        meta_status=t["meta_status"],
        meta_template_ref=t["meta_template_ref"],
        purpose_ref=t["purpose_ref"],
        subject_kind=t["subject_kind"],
        content_review_ref=t["content_review_ref"],
        publicity=t["publicity"],
        body=body,
        variables=variaveis,
    )


def validar_registro(dados: object) -> RegistroDeTemplates:
    """Valida o documento ja' parseado. Qualquer problema -> `RegistroInvalidoError` com TODOS os tokens."""
    problemas: list[str] = []
    if not isinstance(dados, dict):
        raise RegistroInvalidoError(["ROOT_NOT_MAPPING:raiz"])
    if set(dados) != _CAMPOS_RAIZ:
        problemas.append("ROOT_FIELDS_INVALID:raiz")
    if dados.get("schema") != SCHEMA:
        problemas.append("SCHEMA_INVALID:schema")
    classes = _validar_classes(dados.get("variable_classes"), problemas)
    finalidades = _validar_finalidades(dados.get("purposes"), problemas)
    textos = _validar_textos(dados.get("contact_permission_texts"), problemas)
    tipos = dados.get("opposition_executable_subject_kinds")
    if not isinstance(tipos, list) or not all(x in TIPOS_DE_TITULAR for x in tipos):
        problemas.append("ROOT_FIELD_INVALID:opposition_executable_subject_kinds")
        tipos = []
    taxonomia = dados.get("variable_class_taxonomy_ratified")
    if not isinstance(taxonomia, bool):
        problemas.append("ROOT_FIELD_INVALID:variable_class_taxonomy_ratified")
        taxonomia = False
    templates: dict[str, Template] = {}
    brutos = dados.get("templates")
    if not isinstance(brutos, list):
        problemas.append("ROOT_FIELD_INVALID:templates")
        brutos = []
    for i, t in enumerate(brutos):
        tpl = _validar_template(t, i, classes=classes, finalidades=finalidades, problemas=problemas)
        if tpl is None:
            continue
        if tpl.id in templates:
            problemas.append(f"DUPLICATE_ID:templates[{i}]")
            continue
        templates[tpl.id] = tpl
    if problemas:
        raise RegistroInvalidoError(problemas)
    return RegistroDeTemplates(
        finalidades=finalidades,
        templates=templates,
        classes=classes,
        textos_de_permissao=textos,
        oposicao_executavel_para=frozenset(tipos),
        taxonomia_ratificada=taxonomia,
    )


def ler_registro(caminho: Path) -> RegistroDeTemplates:
    """Le' e valida o arquivo. Ausente -> `FileNotFoundError` (quem chama decide; o gate usa o vazio)."""
    dados: Any = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    return validar_registro(dados)


def registro_de_producao(caminho: Path = CAMINHO_PADRAO) -> RegistroDeTemplates:
    """O registro que a producao usa: o arquivo, se existir; senao o VAZIO.

    Arquivo invalido -> levanta (o boot recusa; nunca cai no vazio por engano).
    """
    if not caminho.is_file():
        return RegistroDeTemplates()
    return ler_registro(caminho)
