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
* classe de variavel declarada SEM validador de valor em `VALIDADORES_DE_CLASSE` e fora de
  `CLASSES_RESOLVIDAS_PELA_FONTE` -> recusa do registro inteiro (`VARIABLE_CLASS_NOT_CLOSED`): nao existe
  classe de texto livre. A garantia de "zero dado de saude em mensagem proativa" vem daqui (classe fechada +
  validador de VALOR ou resolucao pela fonte, aplicados pelo gate); o detector
  `contem_dado_de_saude` fica como defesa em profundidade, nao como garantia;
* `meta_status: approved` exige `meta_template_ref`, `content_review_ref`, corpo e `publicity` definidos;
* `ratified: true` exige `ratification_ref`.

O que este modulo NAO decide: a taxonomia final de classes (DPO; `vendor_notice.py` registra o mesmo
residuo) e qualquer ratificacao. O arquivo publicado tem ZERO finalidades ratificadas, ZERO templates
aprovados, ZERO textos de permissao e ZERO tipos de titular com oposicao executavel — o teste
`tests/unit/gateway/test_esf13_template_registry.py` fixa esse estado.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
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
    "gestacao",
    "gravidez",
    "cancer",
    "neoplasia",
    "hipertensao",
    "diabetes",
    "medicamento",
    "remedio",
)
#: CID-10: letra + separador opcional (espaco, ponto, hifen, barra, sublinhado) + 2 digitos + subcategoria
#: opcional (separador opcional + 1-2 alfanumericos). Roda sobre o texto sem acento, minusculo e com
#: espacos colapsados. Recusa MAIS de proposito (ex.: "J 45", "j-45", "J45.0", "j 45 9").
_CID10: Final[re.Pattern[str]] = re.compile(
    r"(?<![a-z0-9])[a-tv-z][ ._/\-]?[0-9]{2}(?:[ ._/\-]?[0-9a-z]{1,2})?(?![a-z0-9])"
)
#: CID-10 sobre o texto ORIGINAL (sem normalizacao): fronteira so' ASCII (lookaround, nao `\b`), para que
#: letra acentuada, travessao, seta, bullet e aspas nao escondam o codigo.
_CID10_MAIN: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9])[A-TV-Z][0-9]{2}(?:\.[0-9A-Z]{1,2})?(?![A-Za-z0-9])", re.IGNORECASE
)
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
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower()


def contem_dado_de_saude(texto: str) -> bool:
    """`True` se o texto carrega termo de saude ou padrao de CID-10. Usado no corpo e nos valores.

    DEFESA EM PROFUNDIDADE: a lista de termos nao e' completa e nao pretende ser. A garantia de "zero dado
    de saude" vem da classe fechada da variavel + validador de valor (`VALIDADORES_DE_CLASSE`).
    """
    norm = re.sub(r"\s+", " ", _normalizar(texto))
    if _CID10_MAIN.search(texto) or _CID10.search(norm):
        return True
    norm = re.sub(r"[=_\-:]", " ", norm)
    return any(re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", norm) for t in TERMOS_DE_SAUDE)


# --------------------------------------------------------------------------- classes FECHADAS de variavel
_MESES: Final[tuple[str, ...]] = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)
_RE_COMPETENCIA: Final[re.Pattern[str]] = re.compile(
    r"(?:\[TESTE\] )?(" + "|".join(_MESES) + r") de ([12][0-9]{3})"
)
_RE_VALOR_REAIS: Final[re.Pattern[str]] = re.compile(r"(?:0|[1-9][0-9]{0,8}),[0-9]{2}")
_RE_DIA_MES: Final[re.Pattern[str]] = re.compile(r"([0-9]{2})/([0-9]{2})")
_RE_DATA: Final[re.Pattern[str]] = re.compile(r"([0-9]{2})/([0-9]{2})/([0-9]{4})")
_RE_CONTAGEM: Final[re.Pattern[str]] = re.compile(r"0|[1-9][0-9]{0,8}")
_RE_LINK: Final[re.Pattern[str]] = re.compile(
    r"https://[A-Za-z0-9][A-Za-z0-9.\-]{0,100}(?:/[a-z]{1,2})?/[0-9a-f]{16,128}(?:\?[A-Za-z0-9._~\-=&%]{0,200})?"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,128}"
)
_RE_CENTAVOS: Final[re.Pattern[str]] = re.compile(r"R\$ (?:0|[1-9][0-9]{0,2}(?:\.[0-9]{3})*),[0-9]{2}")
_RE_PROTOCOLO: Final[re.Pattern[str]] = re.compile(r"[0-9]{4,20}(?:-[0-9]{1,10}){0,3}")


def _simples(valor: object, tamanho_max: int) -> bool:
    """Base de todo validador: `str` exata, sem espaco nas pontas, nao vazia, tamanho limitado."""
    return type(valor) is str and 0 < len(valor) <= tamanho_max and valor == valor.strip()


def _data_valida(dia: str, mes: str, ano: int) -> bool:
    try:
        date(ano, int(mes), int(dia))
    except ValueError:
        return False
    return True


def _v_competencia(valor: str) -> bool:
    return _simples(valor, 32) and _RE_COMPETENCIA.fullmatch(valor) is not None


def _v_valor_reais(valor: str) -> bool:
    return _simples(valor, 16) and _RE_VALOR_REAIS.fullmatch(valor) is not None


def _v_dia_mes(valor: str) -> bool:
    m = _RE_DIA_MES.fullmatch(valor) if _simples(valor, 5) else None
    return m is not None and _data_valida(m[1], m[2], 2000)  # 2000 e' bissexto: 29/02 vale


def _v_data(valor: str) -> bool:
    m = _RE_DATA.fullmatch(valor) if _simples(valor, 10) else None
    return m is not None and _data_valida(m[1], m[2], int(m[3]))


def _v_contagem(valor: str) -> bool:
    return _simples(valor, 9) and _RE_CONTAGEM.fullmatch(valor) is not None


def _v_link_opaco(valor: str) -> bool:
    return _simples(valor, 256) and _RE_LINK.fullmatch(valor) is not None


def _v_centavos(valor: str) -> bool:
    return _simples(valor, 20) and _RE_CENTAVOS.fullmatch(valor) is not None


def _v_protocolo(valor: str) -> bool:
    return _simples(valor, 64) and _RE_PROTOCOLO.fullmatch(valor) is not None


def validador_produto_catalogo(nomes: Collection[str] = frozenset()) -> Callable[[str], bool]:
    """Validador da classe `produto_catalogo`: so' vale nome que PERTENCE ao conjunto injetado.

    GAP: nao existe catalogo de produtos publicado. O padrao e' o conjunto VAZIO, que recusa tudo; quem
    publicar o catalogo injeta os nomes por PR que altere este modulo, com o catalogo publicado (o mapa
    `VALIDADORES_DE_CLASSE` e' somente leitura em tempo de execucao). Nunca aceita texto avulso.
    """
    permitidos = frozenset(nomes)

    def _valido(valor: str) -> bool:
        return _simples(valor, 120) and valor in permitidos

    return _valido


#: Classes cujo VALOR nao e' validado por formato: so' pode ser RESOLVIDO PELA FONTE (CD-94). `primeiro_nome`:
#: uma palavra latina nao distingue nome de pessoa de nome de medicamento, entao o valor so' vale se vier da
#: custodia do destinatario, pela referencia opaca do titular; valor informado pelo chamador e' recusado.
#: GAP: nao existe fonte de primeiro nome (a custodia guarda so' telefone) -> hoje todo modelo que use uma
#: classe daqui e' recusado pelo gate. A classe e' DECLARADA (nao e' "sem validador"), mas nunca valida valor.
CLASSES_RESOLVIDAS_PELA_FONTE: Final[frozenset[str]] = frozenset({"primeiro_nome"})

#: Classes FECHADAS de variavel e o validador de VALOR de cada uma. NAO existe classe de texto livre: classe
#: declarada sem entrada aqui (nem em `CLASSES_RESOLVIDAS_PELA_FONTE`) recusa o registro
#: (`VARIABLE_CLASS_NOT_CLOSED`). Os validadores abaixo checam so' FORMATO: as classes de data, valor,
#: contagem, protocolo e link aceitam so' digitos, separadores e hex, que nao codificam palavra; a resolucao
#: pela fonte delas e' linha propria, ainda nao feita. As seis primeiras sao as do
#: YAML publicado (formatos EXATOS do produtor de lembretes, `platform/lembretes/texto.py`; paridade fixada em
#: `tests/unit/gateway/test_cd87_classes_fechadas.py`); as quatro ultimas sao da decisao CD-87.
VALIDADORES_DE_CLASSE: Final[Mapping[str, Callable[[str], bool]]] = MappingProxyType(
    {
        "competencia_mes_ano": _v_competencia,  # `outubro de 2026` (modo de teste: prefixo `[TESTE] `)
        "valor_cobranca_reais": _v_valor_reais,  # `320,25` (sem `R$`, sem milhar)
        "data_vencimento_dia_mes": _v_dia_mes,  # `20/10`
        "contagem": _v_contagem,  # inteiro nao negativo em digitos
        # Formato nao especificado no YAML: assumida data DD/MM/AAAA (ajustar se a fonte publicar outro).
        "data_publicacao_fonte": _v_data,
        "link_opaco_jornada": _v_link_opaco,  # URL https opaca ou referencia, sem espaco nem texto
        "data": _v_data,  # DD/MM/AAAA valida
        "valor_centavos_formatado": _v_centavos,  # `R$ 1.234,56`
        "protocolo_ref": _v_protocolo,  # numerico com hifen, ex.: `2026-10-0001`
        "produto_catalogo": validador_produto_catalogo(),  # GAP: catalogo nao publicado -> recusa tudo
    }
)


def valor_valido_para_classe(
    classe: str,
    valor: object,
    validadores: Mapping[str, Callable[[str], bool]] = VALIDADORES_DE_CLASSE,
) -> bool:
    """`True` so' se a classe tem validador e o VALOR passa nele. Classe sem validador nunca valida; classe
    resolvida pela fonte (`primeiro_nome`) tambem nunca valida valor informado."""
    if classe in CLASSES_RESOLVIDAS_PELA_FONTE:
        return False  # o valor nao se valida por formato: so' a fonte o fornece
    validador = validadores.get(classe)
    return type(valor) is str and validador is not None and validador(valor)


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
        if cid not in VALIDADORES_DE_CLASSE and cid not in CLASSES_RESOLVIDAS_PELA_FONTE:
            problemas.append(f"VARIABLE_CLASS_NOT_CLOSED:variable_classes[{i}]")
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
        if classe not in VALIDADORES_DE_CLASSE and classe not in CLASSES_RESOLVIDAS_PELA_FONTE:
            problemas.append(f"VARIABLE_CLASS_NOT_CLOSED:{onde}.variables[{j}]")
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
