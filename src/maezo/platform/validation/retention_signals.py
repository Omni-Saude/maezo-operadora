"""Cerca de sinais de retencao (E-SF-RT-01): catalogo FECHADO `ret.*` e recusa de sinal clinico.

Origem
------
Registro RT-09 do adendo de retencao do plano do funil (decisao do coordenador sob o mandato
OS-07) e decisao CD-07 da execucao: entregar a allowlist e a cerca LOCAL; nenhum emissor, nenhum
processo de reajuste. O dado mora em `spec/policies/retention-signals/allowlist.yaml` (DRAFT).

O que este modulo faz
---------------------
1. Le a allowlist (sem cache; toda chamada de `load_catalog` rele e revalida o arquivo).
2. Recusa, com codigo tipado (`RetentionSignalRefusal`), todo gatilho/segmento de retencao que:
   seja de familia proibida (autorizacao, guia, sinistro, reembolso, programa de cuidado,
   CPT/DLP, DS, internacao, uso do plano, leitura de mensagem) ou tenha forma de PHI
   (`ERR_SINAL_RETENCAO_PROIBIDO`); esteja fora do catalogo (`..._FORA_DO_CATALOGO`); ou esteja
   no catalogo sem o uso pedido (`..._USO_NAO_PERMITIDO`).
3. Recusa todo payload de evento `ret.*` que carregue campo clinico (`ERR_EVENTO_RET_CAMPO_CLINICO`),
   campo fora do esquema fechado do evento, ou valor nao escalar. Chave de medicao de funil
   cai no esquema fechado (`ERR_EVENTO_RET_CAMPO_NAO_DECLARADO`).

O que este modulo NAO faz
-------------------------
* Nao emite evento. Medicao (A9) nao e' executavel; a emissao ao lago e' de E-SF-35r. O leitor
  exige `emissao: nenhuma` e `emissor: nenhum` em todo evento.
* Nao decide o que e' PHI. "Campo clinico" vem das fontes que ja' existem, sem lista nova:
  `phi_completeness.classify` (nomes PHI listados + heuristica de forma) e a taxonomia C1-C6 de
  `gateway.vendor_suppression` (so' C5 e C6 sao aceitas num campo de evento de retencao).
* Nao ratifica nada: `status` so' aceita DRAFT.
* Nao usa `resolve_spec_dir()`: nenhum runtime empacotado le esta allowlist na onda 1 (a cerca de
  empacotamento de `tests/unit/ci/test_spec_packaging_fence.py` seguiria exigindo force-include).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, final

import yaml

from maezo.gateway.vendor_suppression import PROHIBITED_FIELD_CLASSES, FieldClass
from maezo.platform.validation.phi_completeness import CLEAN, classify, matches_phi_shape, segments

#: Caminho relativo a raiz do repositorio.
ALLOWLIST_RELPATH: Final = "spec/policies/retention-signals/allowlist.yaml"
SCHEMA: Final = "maezo.retention-signals.allowlist.v1"
#: Nada ratificado: so' DRAFT carrega. Aceitar outro status e' mudanca de codigo, revisada como codigo.
STATUS_ACEITOS: Final[frozenset[str]] = frozenset({"DRAFT"})
#: Portao A9 fechado por construcao: o unico valor aceito.
EMISSAO_FECHADA: Final = "nenhuma"
EMISSOR_NENHUM: Final = "nenhum"
#: Classes C1-C6 aceitas num campo de evento de retencao: so' comportamental e contratual-administrativo.
ALLOWED_FIELD_CLASSES: Final[frozenset[FieldClass]] = frozenset(
    {FieldClass.C5_COMPORTAMENTAL, FieldClass.C6_CONTRATUAL_ADMINISTRATIVO}
)
if ALLOWED_FIELD_CLASSES & PROHIBITED_FIELD_CLASSES:  # pragma: no cover - recusa no import
    raise RuntimeError("retention_signals: classe permitida colide com PROHIBITED_FIELD_CLASSES")

#: Envelope LOCAL do evento de processo (nao e' o esquema do lago); o YAML so' declara a classe. Para o lago
#: (E-SF-35r) vale OS-04: so' nome, horario e resultado; numero_contrato e tenant_id NAO seguem.
ENVELOPE_CAMPOS: Final[frozenset[str]] = frozenset({"evento", "versao_catalogo", "tenant_id", "ocorrido_em"})
GRUPOS: Final[frozenset[str]] = frozenset({"renovacao_reajuste", "saida", "cobranca_reuso", "onboarding"})
ORIGENS: Final[frozenset[str]] = frozenset({"processo", "fonte"})
MODO_SEGMENTO: Final = "segmento"
MODO_RADICAL: Final = "radical"

_ROOT_KEYS: Final = frozenset(
    {
        "schema",
        "id",
        "versao",
        "status",
        "decisao_origem",
        "emissao",
        "campos_envelope",
        "eventos",
        "familias_proibidas",
    }
)
_EVENT_KEYS: Final = frozenset({"nome", "grupo", "origem", "usos", "emissor", "campos"})
_FIELD_KEYS: Final = frozenset({"nome", "classe"})
_FAMILY_KEYS: Final = frozenset({"id", "tokens", "evidencia"})
_TOKEN_KEYS: Final = frozenset({"token", "modo"})
_EVENT_NAME: Final = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_FIELD_NAME: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_TOKEN: Final = re.compile(r"^[a-z]+$")


class RetentionUse(StrEnum):
    """Para que um seletor de retencao usa um evento do catalogo."""

    GATILHO = "gatilho"
    SEGMENTO = "segmento"


@final
class RetentionSignalRefusal(StrEnum):
    """Vocabulario fechado de recusa da cerca de retencao."""

    SINAL_PROIBIDO = "ERR_SINAL_RETENCAO_PROIBIDO"
    FORA_DO_CATALOGO = "ERR_SINAL_RETENCAO_FORA_DO_CATALOGO"
    USO_NAO_PERMITIDO = "ERR_SINAL_RETENCAO_USO_NAO_PERMITIDO"
    SELETOR_INVALIDO = "ERR_SELETOR_RETENCAO_INVALIDO"
    CAMPO_CLINICO = "ERR_EVENTO_RET_CAMPO_CLINICO"
    CAMPO_NAO_DECLARADO = "ERR_EVENTO_RET_CAMPO_NAO_DECLARADO"
    CAMPO_AUSENTE = "ERR_EVENTO_RET_CAMPO_AUSENTE"
    VALOR_NAO_ESCALAR = "ERR_EVENTO_RET_VALOR_NAO_ESCALAR"
    ENVELOPE_INCOERENTE = "ERR_EVENTO_RET_ENVELOPE_INCOERENTE"
    ALLOWLIST_INVALIDA = "ERR_ALLOWLIST_RET_INVALIDA"


class RetentionSignalError(PermissionError):
    """Recusa tipada. `detalhe` leva so' nome de evento/campo, nunca valor de payload."""

    def __init__(self, reason: RetentionSignalRefusal, detalhe: str = "") -> None:
        super().__init__(f"{reason.value}: {detalhe}" if detalhe else reason.value)
        self.reason: RetentionSignalRefusal = reason
        self.detalhe: str = detalhe


@dataclass(frozen=True, slots=True)
class CatalogField:
    nome: str
    classe: FieldClass


@dataclass(frozen=True, slots=True)
class CatalogEvent:
    nome: str
    grupo: str
    origem: str
    usos: frozenset[RetentionUse]
    campos: tuple[CatalogField, ...]


@dataclass(frozen=True, slots=True)
class FamilyToken:
    token: str
    modo: str  # MODO_SEGMENTO | MODO_RADICAL


@dataclass(frozen=True, slots=True)
class ProhibitedFamily:
    id: str
    tokens: tuple[FamilyToken, ...]


@dataclass(frozen=True, slots=True)
class RetentionSelector:
    """Declaracao de um seletor de retencao. Nenhum existe na onda 1."""

    selector_id: str
    gatilhos: tuple[str, ...]
    segmentos: tuple[str, ...] = ()
    variaveis: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetentionSignalCatalog:
    versao: str
    status: str
    envelope: tuple[CatalogField, ...]
    eventos: Mapping[str, CatalogEvent]
    familias: tuple[ProhibitedFamily, ...]

    def prohibited_family(self, nome: str) -> str | None:
        """Id da familia proibida que algum segmento de `nome` casa, ou None."""
        for segmento in segments(nome):
            for familia in self.familias:
                for tok in familia.tokens:
                    if tok.modo == MODO_SEGMENTO and segmento == tok.token:
                        return familia.id
                    if tok.modo == MODO_RADICAL and segmento.startswith(tok.token):
                        return familia.id
        return None

    def is_prohibited_signal(self, nome: str) -> bool:
        return self.prohibited_family(nome) is not None or matches_phi_shape(nome)

    def is_clinical_field(self, nome: str) -> bool:
        return classify(nome) != CLEAN or self.prohibited_family(nome) is not None

    def envelope_names(self) -> frozenset[str]:
        return frozenset(c.nome for c in self.envelope)


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


class _UniqueLoader(yaml.SafeLoader):
    """Chave duplicada num mapa e' recusa (molde `platform/privacy/population_policy.py`)."""


def _mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if type(key) is not str or key in result:
            raise RetentionSignalError(RetentionSignalRefusal.ALLOWLIST_INVALIDA, "chave duplicada")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _invalida(detalhe: str) -> RetentionSignalError:
    return RetentionSignalError(RetentionSignalRefusal.ALLOWLIST_INVALIDA, detalhe)


def _closed_map(value: object, keys: frozenset[str], onde: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise _invalida(f"{onde}: chaves devem ser exatamente {sorted(keys)}")
    return value


def _text(value: object, onde: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalida(f"{onde}: texto ausente")
    return value


def default_allowlist_path() -> Path:
    """`<repo>/spec/policies/retention-signals/allowlist.yaml` (arquivo a 4 niveis da raiz)."""
    return Path(__file__).resolve().parents[4] / ALLOWLIST_RELPATH


def _parse_family(raw: object, i: int) -> ProhibitedFamily:
    item = _closed_map(raw, _FAMILY_KEYS, f"familias_proibidas[{i}]")
    fid = _text(item["id"], f"familias_proibidas[{i}].id")
    _text(item["evidencia"], f"familias_proibidas[{i}].evidencia")
    tokens_raw = item["tokens"]
    if not isinstance(tokens_raw, list) or not tokens_raw:
        raise _invalida(f"familia {fid}: tokens ausentes")
    tokens: list[FamilyToken] = []
    for t in tokens_raw:
        tk = _closed_map(t, _TOKEN_KEYS, f"familia {fid}: token")
        token, modo = tk["token"], tk["modo"]
        if not isinstance(token, str) or not _TOKEN.match(token):
            raise _invalida(f"familia {fid}: token fora de [a-z]+")
        if modo not in (MODO_SEGMENTO, MODO_RADICAL):
            raise _invalida(f"familia {fid}: modo deve ser segmento|radical")
        tokens.append(FamilyToken(token, str(modo)))
    return ProhibitedFamily(fid, tuple(tokens))


def _parse_field(raw: object, onde: str, familias: tuple[ProhibitedFamily, ...]) -> CatalogField:
    item = _closed_map(raw, _FIELD_KEYS, onde)
    nome = item["nome"]
    if not isinstance(nome, str) or not _FIELD_NAME.match(nome):
        raise _invalida(f"{onde}: nome de campo invalido")
    probe = RetentionSignalCatalog("", "", (), MappingProxyType({}), familias)
    if probe.is_clinical_field(nome):
        raise RetentionSignalError(RetentionSignalRefusal.CAMPO_CLINICO, nome)
    try:
        classe = FieldClass(item["classe"])
    except ValueError:
        raise _invalida(f"{onde}: classe fora de C1-C6") from None
    if classe in PROHIBITED_FIELD_CLASSES:
        raise RetentionSignalError(RetentionSignalRefusal.CAMPO_CLINICO, nome)
    if classe not in ALLOWED_FIELD_CLASSES:
        raise _invalida(f"{onde}: classe {classe.value} nao aceita (so' C5, C6)")
    return CatalogField(nome, classe)


def catalog_from_mapping(data: object) -> RetentionSignalCatalog:
    """Valida a estrutura inteira e devolve o catalogo; qualquer desvio e' recusa tipada."""
    root = _closed_map(data, _ROOT_KEYS, "raiz")
    if root["schema"] != SCHEMA:
        raise _invalida("schema")
    _text(root["id"], "id")
    versao = _text(root["versao"], "versao")
    status = root["status"]
    if status not in STATUS_ACEITOS:
        raise _invalida(f"status deve ser um de {sorted(STATUS_ACEITOS)}")
    if root["emissao"] != EMISSAO_FECHADA:
        raise _invalida("emissao: o unico valor aceito e' 'nenhuma' (A9 nao executavel; CD-07)")
    origem = root["decisao_origem"]
    if (
        not isinstance(origem, dict)
        or not origem
        or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in origem.items())
    ):
        raise _invalida("decisao_origem")

    fam_raw = root["familias_proibidas"]
    if not isinstance(fam_raw, list) or not fam_raw:
        raise _invalida("familias_proibidas vazia")
    familias = tuple(_parse_family(f, i) for i, f in enumerate(fam_raw))
    if len({f.id for f in familias}) != len(familias):
        raise _invalida("familias_proibidas: id duplicado")

    env_raw = root["campos_envelope"]
    if not isinstance(env_raw, list) or not env_raw:
        raise _invalida("campos_envelope vazio")
    envelope = tuple(_parse_field(c, "campos_envelope", familias) for c in env_raw)
    env_names = {c.nome for c in envelope}
    if len(env_names) != len(envelope) or env_names != ENVELOPE_CAMPOS:
        raise _invalida(f"campos_envelope deve ser exatamente {sorted(ENVELOPE_CAMPOS)}")

    ev_raw = root["eventos"]
    if not isinstance(ev_raw, list) or not ev_raw:
        raise _invalida("eventos vazio")
    probe = RetentionSignalCatalog(versao, str(status), envelope, MappingProxyType({}), familias)
    eventos: dict[str, CatalogEvent] = {}
    for i, raw in enumerate(ev_raw):
        item = _closed_map(raw, _EVENT_KEYS, f"eventos[{i}]")
        nome = item["nome"]
        if not isinstance(nome, str) or not _EVENT_NAME.match(nome):
            raise _invalida(f"eventos[{i}]: nome invalido")
        if probe.is_prohibited_signal(nome):
            raise RetentionSignalError(RetentionSignalRefusal.SINAL_PROIBIDO, nome)
        if nome in eventos:
            raise _invalida(f"evento duplicado: {nome}")
        if item["grupo"] not in GRUPOS or item["origem"] not in ORIGENS:
            raise _invalida(f"{nome}: grupo/origem fora do vocabulario")
        if item["emissor"] != EMISSOR_NENHUM:
            raise _invalida(f"{nome}: emissor deve ser 'nenhum' (CD-07)")
        usos_raw = item["usos"]
        if not isinstance(usos_raw, list) or len(set(usos_raw)) != len(usos_raw):
            raise _invalida(f"{nome}: usos")
        try:
            usos = frozenset(RetentionUse(u) for u in usos_raw)
        except ValueError:
            raise _invalida(f"{nome}: uso fora de gatilho|segmento") from None
        campos_raw = item["campos"]
        if not isinstance(campos_raw, list):
            raise _invalida(f"{nome}: campos")
        campos = tuple(_parse_field(c, f"{nome}.campos", familias) for c in campos_raw)
        nomes_campos = [c.nome for c in campos]
        if len(set(nomes_campos)) != len(nomes_campos) or env_names & set(nomes_campos):
            raise _invalida(f"{nome}: campo duplicado ou repetindo o envelope")
        eventos[nome] = CatalogEvent(nome, str(item["grupo"]), str(item["origem"]), usos, campos)

    return RetentionSignalCatalog(versao, str(status), envelope, MappingProxyType(eventos), familias)


def load_catalog(path: Path | None = None) -> RetentionSignalCatalog:
    """Le e valida a allowlist; `path=None` usa `default_allowlist_path()`. Sem cache."""
    alvo = path if path is not None else default_allowlist_path()
    try:
        texto = alvo.read_text(encoding="utf-8")
    except OSError as exc:
        raise _invalida(f"leitura ({type(exc).__name__})") from None
    try:
        data = yaml.load(texto, Loader=_UniqueLoader)
    except yaml.YAMLError as exc:
        raise _invalida(f"yaml ({type(exc).__name__})") from None
    return catalog_from_mapping(data)


# ---------------------------------------------------------------------------
# Cerca
# ---------------------------------------------------------------------------


def validate_retention_event_name(
    nome: str, catalog: RetentionSignalCatalog, *, uso: RetentionUse | None = None
) -> CatalogEvent:
    """Devolve o evento do catalogo ou recusa. Ordem: proibido > fora do catalogo > uso."""
    if not isinstance(nome, str):
        raise RetentionSignalError(RetentionSignalRefusal.FORA_DO_CATALOGO, "nome nao textual")
    if catalog.is_prohibited_signal(nome):
        raise RetentionSignalError(RetentionSignalRefusal.SINAL_PROIBIDO, nome)
    evento = catalog.eventos.get(nome)
    if evento is None:
        raise RetentionSignalError(RetentionSignalRefusal.FORA_DO_CATALOGO, nome)
    if uso is not None and uso not in evento.usos:
        raise RetentionSignalError(RetentionSignalRefusal.USO_NAO_PERMITIDO, f"{nome} como {uso.value}")
    return evento


def validate_retention_selector(selector: RetentionSelector, catalog: RetentionSignalCatalog) -> None:
    """Todo gatilho/segmento no catalogo com o uso certo; toda variavel nao clinica e declarada."""
    if not selector.selector_id.strip() or not selector.gatilhos:
        raise RetentionSignalError(RetentionSignalRefusal.SELETOR_INVALIDO, "id ou gatilhos ausentes")
    referenciados: list[CatalogEvent] = []
    for nome in selector.gatilhos:
        referenciados.append(validate_retention_event_name(nome, catalog, uso=RetentionUse.GATILHO))
    for nome in selector.segmentos:
        referenciados.append(validate_retention_event_name(nome, catalog, uso=RetentionUse.SEGMENTO))
    declaradas = set(catalog.envelope_names())
    for ev in referenciados:
        declaradas.update(c.nome for c in ev.campos)
    for var in selector.variaveis:
        if catalog.is_clinical_field(var):
            raise RetentionSignalError(RetentionSignalRefusal.CAMPO_CLINICO, var)
        if var not in declaradas:
            raise RetentionSignalError(RetentionSignalRefusal.CAMPO_NAO_DECLARADO, var)


def validate_retention_event_payload(
    evento: str, payload: Mapping[str, object], catalog: RetentionSignalCatalog
) -> None:
    """Esquema FECHADO do evento. Ordem: nome > campo clinico > esquema > valores."""
    ev = validate_retention_event_name(evento, catalog)
    if not isinstance(payload, Mapping) or not all(isinstance(k, str) for k in payload):
        raise RetentionSignalError(RetentionSignalRefusal.ENVELOPE_INCOERENTE, "payload nao e' objeto")
    for chave in payload:
        if catalog.is_clinical_field(chave):
            raise RetentionSignalError(RetentionSignalRefusal.CAMPO_CLINICO, chave)
    esperado = catalog.envelope_names() | {c.nome for c in ev.campos}
    extras = sorted(set(payload) - esperado)
    if extras:
        raise RetentionSignalError(RetentionSignalRefusal.CAMPO_NAO_DECLARADO, extras[0])
    faltando = sorted(esperado - set(payload))
    if faltando:
        raise RetentionSignalError(RetentionSignalRefusal.CAMPO_AUSENTE, faltando[0])
    for chave, valor in payload.items():
        if isinstance(valor, float) or not isinstance(valor, str | int):
            raise RetentionSignalError(RetentionSignalRefusal.VALOR_NAO_ESCALAR, chave)
    if payload["evento"] != evento or payload["versao_catalogo"] != catalog.versao:
        raise RetentionSignalError(RetentionSignalRefusal.ENVELOPE_INCOERENTE, "evento/versao_catalogo")
