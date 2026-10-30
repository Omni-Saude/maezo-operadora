"""Registro de legal-hold e o predicado que o consulta antes de qualquer eliminacao (R-044, F-062).

Origem: ADR-0020 item 6 (``docs/adr/0020-custody-chain.md``) — um pedido de eliminacao LGPD que
colide com um legal-hold ativo e **resolvido a favor do hold** durante o periodo regulatorio, e a
eliminacao e **diferida/reconciliada** ao fim do hold; o codigo **nao decide unilateralmente apagar
nem reter sob colisao — roteia a decisao a humano** (ADR-0018). A Opcao C de
``docs/compliance/ADR-0020-amendment-draft.md`` (registro de holds com reconciliacao diferida) e a
direcao que o dono registrou na issue #1081 (R-131/DEC-12 -> R-030, 2026-10-29): ratificar o
registro de legal-hold e o re-anchor do checkpoint assinado. A assinatura do juridico continua
necessaria (ADR-0007) — por isso tudo aqui e consumido apenas atras de uma bandeira DARK.

O que este modulo entrega:

- ``LegalHoldRegistry`` — contrato tipado e versionado (``schema_version: 1``) do registro, lido de
  um YAML mantido pelo juridico em ``MAEZO_LEGAL_HOLD_REGISTRY_PATH``. Sem caminho padrao, sem
  registro padrao, sem hold inventado.
- ``load_legal_hold_registry`` — carregador FAIL-CLOSED, no molde de ``legal_bases_matrix``: toda
  falha (caminho ausente, arquivo ausente/ilegivel, YAML invalido, esquema invalido, modelo nao
  ratificado) levanta ``LegalHoldRegistryUnavailableError`` com um ``reason`` preciso.
- ``evaluate_legal_hold`` — o PREDICADO. Nunca levanta; devolve ``LegalHoldDecision`` com um de tres
  desfechos: ``LIVRE`` (o registro carregou e nenhum hold ativo cobre o titular), ``RETIDO`` (ha hold
  ativo — eliminacao diferida) ou ``INDETERMINADO`` (registro indisponivel, titular nao
  identificado, ou erro inesperado — eliminacao diferida). So ``LIVRE`` deixa a eliminacao seguir
  para o PROXIMO gate; ``RETIDO`` e ``INDETERMINADO`` bloqueiam. Duvida nunca vira apagamento.

Nenhum prazo de retencao e decidido aqui: o predicado responde SO "ha hold ativo?". O piso de
retencao (DL-0018, ADR-0020 item 6 "5+ anos — DRAFT/verify") e a matriz de bases legais
(``legal_bases_matrix``) sao outros gates, de outros donos. Um hold esta ativo enquanto o registro
nao disser ``levantado_em`` (ou enquanto essa data for futura) — o fim do hold e um FATO que o
juridico escreve no registro, nunca um periodo calculado em codigo.

Escopo de um hold: ``tenant_id`` + ``titular_pseudo_id`` (o pseudonimo que o processo
SP-OP-LGPD-DSR-001 carrega). Outros escopos que o rascunho de emenda lista (caso, intervalo de
tempo, tenant inteiro — ``ADR-0020-amendment-draft.md`` §4 item 5) sao governanca ainda nao
ratificada (R-030): uma entrada com chave desconhecida torna o registro INVALIDO, o que bloqueia
toda eliminacao — nunca um escopo ignorado em silencio.

PHI (ADR-0006): o pseudonimo do titular so e comparado em memoria. Nenhum log, mensagem de erro ou
``registro()`` carrega o pseudonimo nem conteudo do arquivo; o ``detail`` de erro cita apenas o
caminho, o indice da entrada e o nome do campo. O ``registro()`` leva o sha256 dos bytes do
registro consultado — o "snapshot" que o ``hold_attestation`` do ADR-0029 §2 pede.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger(__name__)

#: Variavel de ambiente com o caminho do registro YAML. SEM fallback: ausente e motivo de recusa.
REGISTRY_PATH_ENV = "MAEZO_LEGAL_HOLD_REGISTRY_PATH"

#: Versao do contrato do registro. Qualquer outra versao e recusada (fail-closed).
SCHEMA_VERSION = 1

_TOP_LEVEL_KEYS = frozenset({"schema_version", "holds", "unratified"})
_HOLD_REQUIRED_KEYS = ("hold_id", "tenant_id", "titular_pseudo_id", "colocado_em")
_HOLD_KEYS = frozenset({*_HOLD_REQUIRED_KEYS, "levantado_em"})

#: Identificador de hold: token limitado, sem espacos (referencia de caso do juridico, nunca PHI).
_HOLD_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")

REASON_PATH_NOT_SET = "path_not_set"
REASON_FILE_NOT_FOUND = "file_not_found"
REASON_UNREADABLE = "unreadable"
REASON_INVALID_ENCODING = "invalid_encoding"
REASON_INVALID_YAML = "invalid_yaml"
REASON_INVALID_SCHEMA = "invalid_schema"
REASON_UNRATIFIED_TEMPLATE = "unratified_template"


class LegalHoldRegistryUnavailableError(RuntimeError):
    """Nao ha registro de legal-hold utilizavel. ``reason`` e um dos ``REASON_*``.

    ``detail`` cita caminho, indice e nome de campo — nunca valores (sem pseudonimo, sem PHI).
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"legal-hold registry unavailable ({reason}): {detail}")


@dataclass(frozen=True)
class LegalHold:
    """Uma entrada do registro: um hold colocado pelo juridico sobre um titular de um tenant."""

    hold_id: str
    tenant_id: str
    titular_pseudo_id: str = field(repr=False)
    colocado_em: date
    levantado_em: date | None = None

    def ativo_em(self, dia: date) -> bool:
        """Ativo enquanto nao levantado, ou enquanto a data de levantamento ainda nao chegou."""
        return self.levantado_em is None or self.levantado_em > dia


@dataclass(frozen=True)
class LegalHoldRegistry:
    """O registro carregado e validado. ``sha256`` identifica exatamente os bytes consultados."""

    holds: tuple[LegalHold, ...]
    sha256: str

    def holds_ativos(self, *, tenant_id: str, titular_pseudo_id: str, dia: date) -> tuple[LegalHold, ...]:
        """Holds ativos em ``dia`` que cobrem este titular deste tenant (correspondencia exata)."""
        return tuple(
            hold
            for hold in self.holds
            if hold.tenant_id == tenant_id
            and hold.titular_pseudo_id == titular_pseudo_id
            and hold.ativo_em(dia)
        )


class LegalHoldOutcome(StrEnum):
    """Desfecho do predicado. So ``LIVRE`` nao bloqueia a eliminacao."""

    LIVRE = "LIVRE"
    RETIDO = "RETIDO"
    INDETERMINADO = "INDETERMINADO"


MOTIVO_SEM_HOLD_ATIVO = "sem_hold_ativo"
MOTIVO_HOLD_ATIVO = "hold_ativo"
MOTIVO_TITULAR_NAO_IDENTIFICADO = "titular_nao_identificado"
MOTIVO_ERRO_INESPERADO = "erro_inesperado"
_MOTIVO_REGISTRO_INDISPONIVEL_PREFIXO = "registro_indisponivel_"


@dataclass(frozen=True)
class LegalHoldDecision:
    """Resultado do predicado, pronto para registro de auditoria (sem PHI)."""

    outcome: LegalHoldOutcome
    motivo: str
    hold_ids: tuple[str, ...] = ()
    registro_sha256: str | None = None

    @property
    def bloqueia_eliminacao(self) -> bool:
        """Fail-closed: tudo que nao e ``LIVRE`` bloqueia (hold ativo OU duvida)."""
        return self.outcome is not LegalHoldOutcome.LIVRE

    def registro(self) -> dict[str, Any]:
        """Registro auditavel da avaliacao: desfecho, motivo, ids de hold e digest do registro."""
        return {
            "legal_hold_outcome": self.outcome.value,
            "legal_hold_motivo": self.motivo,
            "legal_hold_ids": list(self.hold_ids),
            "legal_hold_registro_sha256": self.registro_sha256,
        }


def _fail(reason: str, detail: str) -> LegalHoldRegistryUnavailableError:
    logger.error("legal_hold_registry_unavailable", reason=reason, detail=detail)
    return LegalHoldRegistryUnavailableError(reason, detail)


def _as_date(value: object, where: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise _fail(REASON_INVALID_SCHEMA, f"{where} must be an ISO date (YYYY-MM-DD)")


def _non_empty_str(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise _fail(REASON_INVALID_SCHEMA, f"{where} must be a non-empty string without surrounding blanks")
    return value


def _parse_hold(raw: object, index: int, origin: Path) -> LegalHold:
    where = f"{origin}: holds[{index}]"
    if not isinstance(raw, dict):
        raise _fail(REASON_INVALID_SCHEMA, f"{where} is not a mapping")
    unknown = sorted(str(key) for key in raw if key not in _HOLD_KEYS)
    if unknown:
        # Escopo nao ratificado nunca e ignorado em silencio: registro invalido => bloqueia tudo.
        raise _fail(REASON_INVALID_SCHEMA, f"{where} has unknown field(s): {unknown}")
    missing = [key for key in _HOLD_REQUIRED_KEYS if key not in raw]
    if missing:
        raise _fail(REASON_INVALID_SCHEMA, f"{where} missing required field(s): {missing}")
    hold_id = _non_empty_str(raw["hold_id"], f"{where}.hold_id")
    if not _HOLD_ID_RE.fullmatch(hold_id):
        raise _fail(REASON_INVALID_SCHEMA, f"{where}.hold_id must be a bounded token [A-Za-z0-9._-]")
    colocado_em = _as_date(raw["colocado_em"], f"{where}.colocado_em")
    raw_levantado = raw.get("levantado_em")
    levantado_em = None if raw_levantado is None else _as_date(raw_levantado, f"{where}.levantado_em")
    if levantado_em is not None and levantado_em < colocado_em:
        raise _fail(REASON_INVALID_SCHEMA, f"{where}.levantado_em precedes colocado_em")
    return LegalHold(
        hold_id=hold_id,
        tenant_id=_non_empty_str(raw["tenant_id"], f"{where}.tenant_id"),
        titular_pseudo_id=_non_empty_str(raw["titular_pseudo_id"], f"{where}.titular_pseudo_id"),
        colocado_em=colocado_em,
        levantado_em=levantado_em,
    )


def load_legal_hold_registry(path: str | Path | None = None) -> LegalHoldRegistry:
    """Carrega e valida o registro de legal-hold. FAIL-CLOSED em todo modo de falha.

    Resolucao do caminho: ``path`` explicito, senao ``MAEZO_LEGAL_HOLD_REGISTRY_PATH``. Sem
    caminho padrao. Uma lista ``holds: []`` e valida (registro sem holds) — a AUSENCIA do registro
    nao e: ela levanta ``LegalHoldRegistryUnavailableError``.
    """
    raw_path = path if path is not None else os.environ.get(REGISTRY_PATH_ENV)
    if not raw_path:
        raise _fail(REASON_PATH_NOT_SET, f"{REGISTRY_PATH_ENV} is not set and no explicit path was provided")
    registry_path = Path(raw_path)
    if not registry_path.is_file():
        raise _fail(REASON_FILE_NOT_FOUND, f"not a regular file: {registry_path}")
    try:
        raw_bytes = registry_path.read_bytes()
    except OSError as exc:
        raise _fail(REASON_UNREADABLE, f"could not read {registry_path}: {type(exc).__name__}") from exc
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _fail(REASON_INVALID_ENCODING, f"{registry_path} is not valid UTF-8") from exc
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        # Sem a mensagem do parser: ela pode citar o trecho do arquivo (pseudonimos).
        raise _fail(REASON_INVALID_YAML, f"malformed YAML in {registry_path}") from exc
    if not isinstance(data, dict):
        raise _fail(REASON_INVALID_SCHEMA, f"{registry_path}: root must be a mapping")
    if data.get("unratified") is True:
        raise _fail(REASON_UNRATIFIED_TEMPLATE, f"{registry_path} is marked 'unratified: true'")
    unknown = sorted(str(key) for key in data if key not in _TOP_LEVEL_KEYS)
    if unknown:
        raise _fail(REASON_INVALID_SCHEMA, f"{registry_path}: unknown top-level field(s): {unknown}")
    version = data.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise _fail(REASON_INVALID_SCHEMA, f"{registry_path}: schema_version must be {SCHEMA_VERSION}")
    raw_holds = data.get("holds")
    if not isinstance(raw_holds, list):
        raise _fail(REASON_INVALID_SCHEMA, f"{registry_path}: 'holds' is missing or is not a list")
    holds = tuple(_parse_hold(raw, index, registry_path) for index, raw in enumerate(raw_holds))
    seen: set[str] = set()
    for index, hold in enumerate(holds):
        if hold.hold_id in seen:
            raise _fail(REASON_INVALID_SCHEMA, f"{registry_path}: duplicate hold_id at holds[{index}]")
        seen.add(hold.hold_id)
    digest = hashlib.sha256(raw_bytes).hexdigest()
    logger.info("legal_hold_registry_loaded", hold_count=len(holds), registro_sha256=digest)
    return LegalHoldRegistry(holds=holds, sha256=digest)


def evaluate_legal_hold(
    *,
    tenant_id: object,
    titular_pseudo_id: object,
    loader: Callable[[], LegalHoldRegistry] = load_legal_hold_registry,
    today: date | None = None,
) -> LegalHoldDecision:
    """O PREDICADO de legal-hold. Nunca levanta; na duvida, bloqueia (``INDETERMINADO``).

    Args:
        tenant_id: tenant do pedido. Ausente/nao-string/vazio => ``INDETERMINADO``.
        titular_pseudo_id: pseudonimo do titular. Ausente/nao-string/vazio => ``INDETERMINADO``.
        loader: fonte do registro (padrao: ``load_legal_hold_registry`` lido a cada chamada, para que
            um hold recem-colocado valha na proxima avaliacao).
        today: dia de referencia (padrao: hoje em UTC).
    """
    dia = today if today is not None else datetime.now(UTC).date()
    if (
        not isinstance(tenant_id, str)
        or not tenant_id.strip()
        or not isinstance(titular_pseudo_id, str)
        or not titular_pseudo_id.strip()
    ):
        decision = LegalHoldDecision(LegalHoldOutcome.INDETERMINADO, MOTIVO_TITULAR_NAO_IDENTIFICADO)
        return _logged(decision, tenant_id)
    try:
        registry = loader()
    except LegalHoldRegistryUnavailableError as exc:
        decision = LegalHoldDecision(
            LegalHoldOutcome.INDETERMINADO, f"{_MOTIVO_REGISTRO_INDISPONIVEL_PREFIXO}{exc.reason}"
        )
        return _logged(decision, tenant_id)
    except Exception as exc:  # fail-closed: qualquer falha do registro e duvida, nunca "livre".
        logger.error("legal_hold_registry_unexpected_error", error_type=type(exc).__name__)
        return _logged(LegalHoldDecision(LegalHoldOutcome.INDETERMINADO, MOTIVO_ERRO_INESPERADO), tenant_id)
    ativos = registry.holds_ativos(tenant_id=tenant_id, titular_pseudo_id=titular_pseudo_id, dia=dia)
    if ativos:
        decision = LegalHoldDecision(
            LegalHoldOutcome.RETIDO,
            MOTIVO_HOLD_ATIVO,
            hold_ids=tuple(hold.hold_id for hold in ativos),
            registro_sha256=registry.sha256,
        )
    else:
        decision = LegalHoldDecision(
            LegalHoldOutcome.LIVRE, MOTIVO_SEM_HOLD_ATIVO, registro_sha256=registry.sha256
        )
    return _logged(decision, tenant_id)


def _logged(decision: LegalHoldDecision, tenant_id: object) -> LegalHoldDecision:
    """Uma linha estruturada por avaliacao — tenant, desfecho, motivo, contagem; nunca o titular."""
    log = logger.warning if decision.bloqueia_eliminacao else logger.info
    log(
        "legal_hold_predicate_evaluated",
        tenant_id=tenant_id if isinstance(tenant_id, str) else None,
        outcome=decision.outcome.value,
        motivo=decision.motivo,
        hold_count=len(decision.hold_ids),
        registro_sha256=decision.registro_sha256,
    )
    return decision
