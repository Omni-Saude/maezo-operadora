"""Proveniencia das tabelas K1 do rco na trilha de auditoria (ADR-0075 secao (f), MZO-5a).

O QUE ISTO FAZ. Para uma decisao DMN que vem do pacote K1 do rco (arquivo em
`spec/processes/dmn/rco/`, id de decisao prefixado `rco_`), a entrada de `AuditRecord.dmn_versions`
ganha, alem das chaves do motor (`version`, `id`, `deploymentId`), ate tres campos:

- `shaDmn`: o sha256 do arquivo `.dmn`, lido do pin `config/integrations/rco/rules.lock.json`
  (secao `artifacts` ou `exemplos_sinteticos`). NUNCA do motor: o ADR manda atestar o hash que o
  pin fixou no deploy. Este leitor ainda re-hasheia o arquivo de bordo e recusa divergencia, para
  que o sha gravado seja o dos bytes que o deploy publica (`platform/deploy/engine_deploy.py`, nome
  de recurso `rco/<arquivo>.dmn`).
- `versaoRegra`: do manifesto K1 que o pin fixa (`manifest_pin`). Enquanto `estado` e `sem_pacote`
  nao ha manifesto, entao o campo fica AUSENTE (nunca `null`, nunca inventado).
- `idRegra`: da SAIDA `idRegra` da linha casada (o ADR diz "da saida da tabela"). So entra quando a
  avaliacao devolveu exatamente uma linha com essa saida nao nula; sem ela fica AUSENTE. Valor fora
  de `^[A-Za-z0-9._-]{1,64}$` (ex.: eco de variavel de entrada, PHI) levanta `RcoRulesPinError` e
  nunca e gravado. O exemplo sintetico de hoje nao tem essa saida.

Para tabela Maezo (id sem o prefixo `rco_`) nada muda: nenhuma chave nova, e o pin nem e lido.

FAIL-CLOSED. Uma decisao `rco_` sem entrada no pin (ou com pin ilegivel, sha divergente, arquivo
ausente, estado desconhecido) levanta `RcoRulesPinError` — antes de avaliar, em
`dmn_transport.evaluate_sync`, e de novo ao montar a entrada de auditoria. Nunca se grava um
`shaDmn` fabricado nem se omite o campo para uma tabela do rco. O erro carrega `.code`/`.message`
(convencao da casa), entao `FunctionWorker.execute()` (`base.py::reclassify_coded_exception`) o
converte em `ValueError` -> `failure(retries=0)`: incidente visivel, sem retry (repetir nao muda o
pin).

ESTADO `fixado` AINDA NAO E LIDO. Nele `versaoRegra` vem do manifesto K1, cujo formato o rco ainda
nao publicou. Ler `fixado` sem saber de onde tirar `versaoRegra` gravaria auditoria incompleta, entao
o carregador recusa o estado ate a Onda 2 implementar a leitura do manifesto (e so as decisoes
`rco_` param; as tabelas Maezo nao dependem disto).

CARGA E CACHE. So biblioteca padrao, sem rede. O pin e lido na primeira decisao `rco_` do processo
e guardado (`functools.lru_cache`); trocar o pin exige novo deploy, como trocar a tabela. Carga
preguicosa de proposito: um worker que so consulta tabelas Maezo nao pode deixar de subir por causa
do pin do rco.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Final

from maezo.agents import resolve_spec_dir
from maezo.platform.xml_safe import parse_xml_without_dtd

#: Prefixo obrigatorio do id de decisao do K1 (ADR-0075 secao (b); `platform/validation/dmn.py`).
RCO_DECISION_PREFIX: Final[str] = "rco_"
#: Pin relativo a raiz do repo — o mesmo valor de `scripts/ci/verify_rco_rules_pin.py::DEFAULT_LOCK_PATH`.
RCO_RULES_PIN_RELATIVE_PATH: Final[str] = "config/integrations/rco/rules.lock.json"
#: Pasta do pacote relativa a raiz do repo — o `lock_format.rules_dir` exigido pelo gate.
RCO_RULES_DIR: Final[str] = "spec/processes/dmn/rco"
LOCK_NAME: Final[str] = "rco-rules-lock"
ESTADO_SEM_PACOTE: Final[str] = "sem_pacote"
ESTADO_FIXADO: Final[str] = "fixado"

#: Nome da saida da tabela K1 que identifica a regra casada (ADR-0075 (f): "idRegra: da saida").
SAIDA_ID_REGRA: Final[str] = "idRegra"

ERR_DMN_RCO_PIN: Final[str] = "ERR_DMN_RCO_PIN"

_SHA256: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
#: Forma aceita de `idRegra`: identificador curto, nunca texto livre. Uma saida FEEL pode ecoar uma
#: variavel de entrada (dado de caso/PHI); fora deste padrao o valor NUNCA e gravado (fail-closed).
_ID_REGRA: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_DMN_NS: Final[str] = "{https://www.omg.org/spec/DMN/20191111/MODEL/}"


class RcoRulesPinError(Exception):
    """A proveniencia de uma tabela do rco nao pode ser atestada pelo pin — PERMANENTE.

    Deliberadamente `Exception` com `.code`/`.message` (nao `RuntimeError`): repetir a tarefa nao
    conserta o pin, entao `reclassify_coded_exception` leva a `failure(retries=0)`. A mensagem cita
    so caminhos e ids de decisao do repositorio — nenhum dado de caso.
    """

    def __init__(self, message: str) -> None:
        self.code = ERR_DMN_RCO_PIN
        self.message = message
        super().__init__(f"{self.code}: {message}")


@dataclass(frozen=True, slots=True)
class RcoPinnedTable:
    """Uma decisao do K1 e o que o pin atesta sobre o arquivo dela."""

    decision_key: str
    path: str  # relativo a raiz do repo, como escrito no pin
    sha_dmn: str
    versao_regra: str | None  # None enquanto nao ha manifesto (estado `sem_pacote`)

    def audit_fields(self) -> dict[str, str]:
        """Campos do pin para a entrada de `dmn_versions`; `versaoRegra` so quando existe."""
        fields = {"shaDmn": self.sha_dmn}
        if self.versao_regra is not None:
            fields["versaoRegra"] = self.versao_regra
        return fields


@dataclass(frozen=True, slots=True)
class RcoRulesPin:
    """O pin carregado: estado e as decisoes `rco_` que ele cobre."""

    estado: str
    tables: Mapping[str, RcoPinnedTable]


def is_rco_decision(decision_key: str) -> bool:
    return decision_key.startswith(RCO_DECISION_PREFIX)


def _entries(lock: Mapping[str, Any], section: str) -> list[tuple[PurePosixPath, str]]:
    raw = lock.get(section)
    if not isinstance(raw, list):
        raise RcoRulesPinError(f"pin: secao {section!r} ausente ou nao e lista")
    out: list[tuple[PurePosixPath, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise RcoRulesPinError(f"pin: entrada de {section!r} nao e objeto")
        path, digest = entry.get("path"), entry.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str) or not _SHA256.match(digest):
            raise RcoRulesPinError(f"pin: entrada de {section!r} sem 'path' ou sem 'sha256' de 64 hex")
        rel = PurePosixPath(path)
        if rel.parent != PurePosixPath(RCO_RULES_DIR) or rel.suffix != ".dmn":
            raise RcoRulesPinError(f"pin: {path!r} nao e um .dmn direto em {RCO_RULES_DIR}")
        out.append((rel, digest))
    return out


def _decision_ids(raw: bytes, rel: PurePosixPath) -> list[str]:
    try:
        root = parse_xml_without_dtd(raw)
    except Exception as exc:  # ParseError / DtdRefusedError: arquivo do pacote ilegivel
        raise RcoRulesPinError(f"pin: {rel} nao e DMN legivel ({type(exc).__name__})") from exc
    ids = [str(el.get("id") or "") for el in root.iter(f"{_DMN_NS}decision")]
    if not ids or any(not is_rco_decision(i) for i in ids):
        raise RcoRulesPinError(f"pin: {rel} tem de declarar decisoes com prefixo {RCO_DECISION_PREFIX!r}")
    return ids


def load_rco_rules_pin(lock_path: Path, *, rules_dir: Path) -> RcoRulesPin:
    """Le o pin e mapeia cada decisao `rco_` ao sha256 do seu arquivo. Fail-closed em tudo.

    `rules_dir` e a pasta de bordo de `spec/processes/dmn/rco/` (checkout ou wheel). Cada arquivo
    listado e re-hasheado: o sha gravado na auditoria tem de ser o dos bytes que o deploy publica.
    """
    try:
        lock = json.loads(lock_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise RcoRulesPinError(f"pin ilegivel em {lock_path} ({type(exc).__name__})") from exc
    if not isinstance(lock, dict):
        raise RcoRulesPinError("pin: raiz nao e objeto")
    fmt = lock.get("lock_format")
    if not isinstance(fmt, dict) or fmt.get("name") != LOCK_NAME or fmt.get("rules_dir") != RCO_RULES_DIR:
        raise RcoRulesPinError(f"pin: lock_format exige name={LOCK_NAME!r} e rules_dir={RCO_RULES_DIR!r}")

    estado = lock.get("estado")
    if estado == ESTADO_FIXADO:
        # Onda 2: `versaoRegra` vem do manifesto K1 (`manifest_pin`), cujo formato o rco ainda nao
        # publicou. Sem essa leitura a auditoria sairia sem `versaoRegra` para regra real.
        raise RcoRulesPinError(
            "pin em estado 'fixado': a leitura de versaoRegra do manifesto K1 (manifest_pin) ainda nao "
            "existe neste leitor (Onda 2); decisoes rco_ ficam recusadas ate la"
        )
    if estado != ESTADO_SEM_PACOTE:
        raise RcoRulesPinError(f"pin: estado desconhecido {estado!r}")
    if lock.get("manifest_pin") is not None or _entries(lock, "artifacts"):
        raise RcoRulesPinError("pin: estado 'sem_pacote' exige manifest_pin = null e artifacts = []")

    tables: dict[str, RcoPinnedTable] = {}
    for rel, digest in _entries(lock, "exemplos_sinteticos"):
        try:
            raw = (rules_dir / rel.name).read_bytes()
        except OSError as exc:
            raise RcoRulesPinError(f"pin: arquivo listado ausente ou ilegivel: {rel}") from exc
        if hashlib.sha256(raw).hexdigest() != digest:
            raise RcoRulesPinError(f"pin: sha256 de {rel} diverge do pin")
        for decision_key in _decision_ids(raw, rel):
            if decision_key in tables:
                raise RcoRulesPinError(f"pin: decisao {decision_key!r} declarada em mais de um arquivo")
            tables[decision_key] = RcoPinnedTable(decision_key, str(rel), digest, versao_regra=None)
    return RcoRulesPin(estado=estado, tables=MappingProxyType(tables))


def _default_lock_path() -> Path:
    """Checkout do repo, depois a copia ao lado do pacote (force-include do pyproject)."""
    parents = Path(__file__).resolve().parents
    candidates = [parents[4] / RCO_RULES_PIN_RELATIVE_PATH, parents[2] / RCO_RULES_PIN_RELATIVE_PATH]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RcoRulesPinError(f"pin nao encontrado; tentados: {[str(c) for c in candidates]}")


@lru_cache(maxsize=1)
def rco_rules_pin() -> RcoRulesPin:
    """O pin de bordo, carregado uma vez por processo (falha nao e cacheada: tenta de novo)."""
    try:
        rules_dir = resolve_spec_dir() / "processes" / "dmn" / "rco"
    except Exception as exc:  # FileNotFoundError / SpecDirOverrideRefusedError
        raise RcoRulesPinError(f"pasta do pacote rco nao resolvida ({type(exc).__name__})") from exc
    return load_rco_rules_pin(_default_lock_path(), rules_dir=rules_dir)


def rco_pinned_table(decision_key: str) -> RcoPinnedTable | None:
    """`None` para tabela Maezo (o pin nem e lido); a entrada do pin para `rco_`, ou levanta."""
    if not is_rco_decision(decision_key):
        return None
    pinned = rco_rules_pin().tables.get(decision_key)
    if pinned is None:
        raise RcoRulesPinError(
            f"decisao {decision_key!r} tem prefixo rco_ mas nao esta no pin {RCO_RULES_PIN_RELATIVE_PATH}; "
            "sem shaDmn atestado ela nao e avaliada nem auditada"
        )
    return pinned


def rco_audit_fields(decision_key: str, row: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Campos extras da entrada de `dmn_versions`: `{}` para Maezo; `shaDmn` (+ opcionais) para rco."""
    pinned = rco_pinned_table(decision_key)
    if pinned is None:
        return {}
    fields = pinned.audit_fields()
    id_regra = row.get(SAIDA_ID_REGRA) if row is not None else None
    if id_regra is None:
        return fields
    if not isinstance(id_regra, str) or _ID_REGRA.fullmatch(id_regra) is None:
        # O valor nao entra nem na mensagem: pode ser dado de caso ecoado pela tabela.
        raise RcoRulesPinError(
            f"decisao {decision_key!r}: saida {SAIDA_ID_REGRA!r} fora do padrao {_ID_REGRA.pattern!r}; "
            "valor nao gravado na auditoria"
        )
    fields["idRegra"] = id_regra
    return fields


__all__ = [
    "ERR_DMN_RCO_PIN",
    "RCO_DECISION_PREFIX",
    "RCO_RULES_DIR",
    "RCO_RULES_PIN_RELATIVE_PATH",
    "RcoPinnedTable",
    "RcoRulesPin",
    "RcoRulesPinError",
    "is_rco_decision",
    "load_rco_rules_pin",
    "rco_audit_fields",
    "rco_pinned_table",
    "rco_rules_pin",
]
