"""Zona da narrativa do dossiê — interruptor ratificado por humano, fail-closed.

A única chamada de LLM do fluxo de autorização (`agents/rafael/graph.py`,
`_build_dossier`) precisa decidir se a narrativa do dossiê é dado da **zona PHI**
ou dado pseudonimizado servível pela **zona geral**. Essa pergunta é jurídica e
clínica, não técnica: o envelope A2A traz só referências e pseudo-ids, mas o prompt
leva os fatos do caso e o `payload_ref` é o recurso FHIR do beneficiário.

Este módulo NÃO responde a pergunta. Ele lê a resposta de um artefato que o DPO e o
médico auditor ratificam (`spec/policies/phi/dossier-narrative-zone.yaml`), e na
ausência de ratificação devolve **PHI** — o comportamento de hoje.

Mesmo formato do interruptor do inbox AMH
(`platform/integrations/amh_inbox.load_inbox_ratification`): artefato de dado,
recusa em qualquer desvio, e digest que amarra a aprovação aos bytes revisados.

Por que digest: sem ele, uma ratificação feita sobre um prompt se estenderia
silenciosamente a qualquer prompt futuro. Com ele, editar o grafo do Rafael devolve
a narrativa à zona PHI até alguém revisar e re-assinar. É deliberadamente
conservador — e é a mesma escolha que o artefato do DBA fez com a DDL.
"""

from __future__ import annotations

import datetime
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import structlog
import yaml

logger = structlog.get_logger(__name__)

#: Variável que sobrepõe a descoberta de caminho (mesma convenção do inbox AMH).
RATIFICATION_PATH_ENV: Final[str] = "MAEZO_DOSSIER_ZONE_RATIFICATION"

#: Declaração de AMBIENTE SOMENTE-SINTÉTICO. NÃO é uma ratificação, e o nome é longo
#: e feio de propósito: quem a liga não pode alegar que não sabia o que estava ligando.
#:
#: O que ela afirma: "neste ambiente não existe dado de paciente real, portanto a
#: narrativa do dossiê pode ser servida pela zona geral". Isso é uma afirmação sobre o
#: AMBIENTE, verificável por quem opera — diferente da ratificação, que é uma afirmação
#: sobre a NATUREZA DO DADO e exige DPO e médico auditor.
#:
#: Declaração do dono deste ambiente, registrada em 18/08/2026, textual:
#:   "trocar o provedor de inferência do Rafael do simulador para o Bedrock e desligar
#:    a exigência de zona de saúde apenas nesse serviço — o que só é aceitável porque
#:    vamos usar caso fictício."
#:
#: O código NÃO pode verificar que o caso é fictício. Nenhum código pode. Por isso esta
#: chave não substitui a ratificação em nenhum ambiente que veja dado real, e por isso
#: ela grita em WARNING a cada boot com o nome dela dentro da mensagem.
SYNTHETIC_ONLY_ENV: Final[str] = "MAEZO_DOSSIER_NARRATIVE_GENERAL_ZONE_SYNTHETIC_ONLY"

#: Caminho relativo do artefato, tanto no checkout quanto ao lado do pacote.
_ARTIFACT_RELPATH: Final[Path] = Path("spec/policies/phi/dossier-narrative-zone.yaml")

#: Arquivo cujos bytes a ratificação carimba: o que constrói o prompt enviado.
_GRAPH_RELPATH: Final[Path] = Path("src/maezo/agents/rafael/graph.py")

#: Campos obrigatórios. Faltar um é DRAFT, não "quase ratificado".
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "ratificado",
    "zona_declarada",
    "dpo_review",
    "medico_auditor_review",
    "ratificado_por",
    "data_ratificacao",
    "graph_sha256",
    "fundamentacao",
)

#: Marcadores que denunciam um artefato preenchido pela metade.
_PLACEHOLDER_MARKERS: Final[tuple[str, ...]] = ("PENDING", "TODO", "XXX", "REPLACE")

#: Razões estáveis — operadores e testes casam com estes códigos, nunca com prosa.
REASON_FILE_NOT_FOUND: Final[str] = "ratification_file_not_found"
REASON_UNREADABLE: Final[str] = "ratification_unreadable"
REASON_INVALID_SCHEMA: Final[str] = "ratification_invalid_schema"
REASON_PLACEHOLDER: Final[str] = "ratification_has_placeholder"
REASON_NOT_RATIFIED: Final[str] = "ratification_not_ratified"
REASON_ZONE_NOT_GENERAL: Final[str] = "zona_declarada_not_geral"
REASON_REVIEW_MISSING: Final[str] = "review_not_approved"
REASON_DIGEST_MISMATCH: Final[str] = "graph_sha256_mismatch"

#: Motivo distinto para a zona geral por ambiente sintético — nunca confundido com
#: ratificação nas consultas de log.
REASON_SYNTHETIC_ONLY: Final[str] = "ambiente_declarado_somente_sintetico"


class DossierZoneNotRatifiedError(RuntimeError):
    """A narrativa não foi ratificada como zona geral. Carrega a razão estável."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class DossierZoneRatification:
    """Ratificação válida — só existe se TUDO conferiu."""

    zona_declarada: str
    ratificado_por: str
    data_ratificacao: str
    graph_sha256: str
    fundamentacao: str


def _candidate_paths() -> tuple[Path, ...]:
    """Override explícito, depois o checkout, depois a cópia ao lado do pacote."""
    override = os.environ.get(RATIFICATION_PATH_ENV)
    candidates: list[Path] = []
    if override and override.strip():
        candidates.append(Path(override.strip()))
    # Checkout: .../src/maezo/platform/privacy/dossier_zone.py -> raiz do repo
    repo_root = Path(__file__).resolve().parents[4]
    candidates.append(repo_root / _ARTIFACT_RELPATH)
    # Wheel: o force-include entrega em maezo/spec/policies/phi/
    pkg_root = Path(__file__).resolve().parents[2]  # .../maezo
    candidates.append(pkg_root / "spec" / "policies" / "phi" / _ARTIFACT_RELPATH.name)
    return tuple(candidates)


def resolve_ratification_path() -> Path:
    """Primeiro candidato que existe; se nenhum existir, o último (para a mensagem)."""
    candidates = _candidate_paths()
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]


def _looks_like_placeholder(value: str) -> str | None:
    upper = value.upper()
    for marker in _PLACEHOLDER_MARKERS:
        if marker in upper:
            return marker
    return None


def _resolve_graph_path() -> Path | None:
    repo_root = Path(__file__).resolve().parents[4]
    checkout = repo_root / _GRAPH_RELPATH
    if checkout.is_file():
        return checkout
    pkg = Path(__file__).resolve().parents[2] / "agents" / "rafael" / "graph.py"
    return pkg if pkg.is_file() else None


def load_ratification(path: str | Path | None = None) -> DossierZoneRatification:
    """Carrega e valida o artefato. FALHA FECHADO em todo desvio.

    Raises:
        DossierZoneNotRatifiedError: sempre, em qualquer modo de falha.
    """
    artifact = Path(path) if path is not None else resolve_ratification_path()
    if not artifact.is_file():
        raise DossierZoneNotRatifiedError(
            REASON_FILE_NOT_FOUND, f"artefato de ratificação não encontrado em {artifact}"
        )

    try:
        raw: Any = yaml.safe_load(artifact.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — qualquer falha de leitura é DRAFT
        raise DossierZoneNotRatifiedError(REASON_UNREADABLE, f"{artifact}: {exc}") from exc

    if not isinstance(raw, dict):
        raise DossierZoneNotRatifiedError(REASON_INVALID_SCHEMA, f"{artifact}: raiz deve ser um mapeamento")

    missing = [f for f in REQUIRED_FIELDS if f not in raw]
    if missing:
        raise DossierZoneNotRatifiedError(
            REASON_INVALID_SCHEMA, f"{artifact}: campo(s) ausente(s): {missing}"
        )

    # O booleano vem antes da varredura de placeholder para que um DRAFT puro
    # reporte `not_ratified` — a razão que o leitor espera — em vez de tropeçar
    # primeiro num placeholder do próprio DRAFT.
    if raw["ratificado"] is not True:
        raise DossierZoneNotRatifiedError(
            REASON_NOT_RATIFIED,
            f"{artifact}: ratificado é {raw['ratificado']!r}, não o booleano true — "
            "a narrativa do dossiê permanece na zona PHI",
        )

    valores: dict[str, str] = {}
    for campo in REQUIRED_FIELDS:
        if campo == "ratificado":
            continue
        valor = raw[campo]
        # O YAML converte `2026-08-18` sem aspas num objeto `date`. Recusar isso com
        # um erro de tipo puniria o ratificador pelo gesto MAIS natural e ensinaria
        # o time a desconfiar do portao. Coagimos para ISO e seguimos — o que
        # importa e' o conteudo, nao a citacao.
        if isinstance(valor, (datetime.date, datetime.datetime)):
            valor = valor.isoformat()
        if not isinstance(valor, str) or not valor.strip():
            raise DossierZoneNotRatifiedError(
                REASON_INVALID_SCHEMA,
                f"{artifact}: {campo} deve ser texto não vazio, veio {type(valor).__name__}",
            )
        marcador = _looks_like_placeholder(valor)
        if marcador is not None:
            raise DossierZoneNotRatifiedError(
                REASON_PLACEHOLDER,
                f"{artifact}: {campo} ainda carrega o placeholder {marcador!r} — "
                "uma ratificação pela metade não é ratificação",
            )
        valores[campo] = valor.strip()

    if valores["zona_declarada"].upper() != "GERAL":
        raise DossierZoneNotRatifiedError(
            REASON_ZONE_NOT_GENERAL,
            f"{artifact}: zona_declarada é {valores['zona_declarada']!r}, não 'GERAL'",
        )

    for campo in ("dpo_review", "medico_auditor_review"):
        if valores[campo].upper() != "APPROVED":
            raise DossierZoneNotRatifiedError(
                REASON_REVIEW_MISSING,
                f"{artifact}: {campo} é {valores[campo]!r}, não 'APPROVED' — "
                "a pergunta é jurídica E clínica, e as duas revisões são obrigatórias",
            )

    grafo = _resolve_graph_path()
    if grafo is None:
        raise DossierZoneNotRatifiedError(
            REASON_DIGEST_MISMATCH, "não foi possível localizar graph.py para conferir o digest"
        )
    digest = hashlib.sha256(grafo.read_bytes()).hexdigest()
    if digest != valores["graph_sha256"].lower():
        raise DossierZoneNotRatifiedError(
            REASON_DIGEST_MISMATCH,
            f"{grafo} tem sha256 {digest}, a ratificação carimbou "
            f"{valores['graph_sha256']} — o prompt mudou depois da aprovação; "
            "reveja e re-assine",
        )

    return DossierZoneRatification(
        zona_declarada=valores["zona_declarada"].upper(),
        ratificado_por=valores["ratificado_por"],
        data_ratificacao=valores["data_ratificacao"],
        graph_sha256=valores["graph_sha256"].lower(),
        fundamentacao=valores["fundamentacao"],
    )


def ambiente_declarado_somente_sintetico() -> bool:
    """`True` se o operador declarou que este ambiente não vê dado real.

    Aceita apenas `1`, `true`, `yes` (sem distinção de caixa). Qualquer outro valor —
    inclusive `0`, vazio ou lixo — é `False`: uma declaração desta natureza não pode
    ser ligada por acidente de digitação.
    """
    valor = os.environ.get(SYNTHETIC_ONLY_ENV, "").strip().lower()
    return valor in {"1", "true", "yes"}


def dossier_narrative_requires_phi_zone() -> bool:
    """`True` = a narrativa é PHI (comportamento padrão). NUNCA levanta exceção.

    Duas vias levam a `False`, e elas afirmam coisas diferentes:

    1. `SYNTHETIC_ONLY_ENV` — o operador declara que o AMBIENTE não vê dado real.
       Verificada primeiro, porque é a única que serve a um ambiente de teste sem
       envolver DPO e corpo clínico.
    2. A ratificação assinada — DPO e médico auditor declaram que o DADO
       (pseudonimizado) pertence à zona geral. É a via de produção.


    O chamador está no meio da construção do dossiê e precisa de um booleano, não
    de um erro. Toda falha vira `True`: a ausência de ratificação mantém a narrativa
    dentro da zona PHI, que é o lado seguro.

    A decisão é registrada em log nos dois casos — quem lê o log de um boot sabe sob
    qual regime o agente está operando, sem inferir de um booleano solto.
    """
    if ambiente_declarado_somente_sintetico():
        # WARNING, não INFO: quem lê o log de boot precisa TROPEÇAR nisto. E a mensagem
        # carrega o nome da variável para que a pergunta "quem ligou isso?" tenha
        # resposta imediata na task definition.
        logger.warning(
            "dossier_narrative_zone_geral_por_ambiente_sintetico",
            motivo=REASON_SYNTHETIC_ONLY,
            zona="geral",
            variavel=SYNTHETIC_ONLY_ENV,
            mensagem=(
                "a narrativa do dossie sera servida pela ZONA GERAL porque o operador "
                "declarou que este ambiente NAO contem dado de paciente real. Isto NAO "
                "e' uma ratificacao de DPO/medico auditor: se dado real passar por aqui, "
                "houve transferencia internacional de dado de saude sem base. Desligue a "
                "variavel antes de qualquer carga real."
            ),
        )
        return False

    try:
        ratificacao = load_ratification()
    except DossierZoneNotRatifiedError as exc:
        logger.info(
            "dossier_narrative_zone_phi",
            motivo=exc.reason,
            detalhe=exc.detail,
            zona="phi",
        )
        return True

    logger.warning(
        "dossier_narrative_zone_geral_ratificada",
        zona="geral",
        ratificado_por=ratificacao.ratificado_por,
        data_ratificacao=ratificacao.data_ratificacao,
        graph_sha256=ratificacao.graph_sha256,
        mensagem=(
            "a narrativa do dossiê será servida pela ZONA GERAL por ratificação "
            "explícita do DPO e do médico auditor — transferência internacional "
            "aceita para este conteúdo"
        ),
    )
    return False
