"""Cenarios sinteticos do caminho AUTH (SP-OP-AUTH-001) para carga, p95 e drills de failover.

Fonte unica dos cenarios usados por tres consumidores (R-086, achado F-120):

- ``tests/load/locustfile_auth.py`` — teste de carga Locust (execucao manual, stack local);
- ``tests/integration/perf/test_auth_p95.py`` — teste de p95 do AUTH (lane ``integration``);
- ``tests/integration/resilience/test_auth_worker_failover.py`` e
  ``tests/load/chaos_engine_restart.py`` — failover de worker e de engine.

STDLIB ONLY de proposito: o locustfile roda sob ``uv run --with locust==...`` (gevent faz
monkey-patch do processo) e importa este modulo como irmao; nada aqui pode puxar httpx/maezo.

O que e' "AUTH" aqui (escopo declarado, ver NEEDS-HUMAN no README): os trechos AUTOMATICOS e
sincronos do processo de autorizacao previa que rodam no engine — o start de
``SP-OP-AUTH-001`` ate' o primeiro estado de espera e as quatro avaliacoes DMN que o processo e o
worker ``operadora.auth.validate_auto_criteria`` fazem engine-side (ADR-0028). As User Tasks
humanas (medico auditor, coordenacao, junta) ficam FORA por construcao: negativa e' decisao
humana (L0 hard, contrato SP-OP-AUTH-001 "Invariante L0 hard") e nao tem meta de latencia.

Dados 100% sinteticos: nenhum CPF, nome, carteirinha ou dado clinico. Os unicos identificadores
sao um tenant descartavel ``r086perf_<hex>`` e uma guia ``SINTR086<hex>``.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

#: Meta de latencia do AUTH. Unica fonte encontrada na spec: PLANS.md:923, criterio de
#: Verificacao do M13 ("Latencia p95 < 200ms para AUTH"). docs/observability/SLO.md NAO tem SLI
#: de AUTH (o unico p95 de la e' o DRAFT de 5 s do worker, SLO.md:168). Limite ESTRITO (<).
P95_BOUND_MS = 200.0
P95_BOUND_SOURCE = "PLANS.md:923"

#: Hosts aceitos. Qualquer outro host e' recusado ANTES da primeira requisicao: carga e chaos
#: nunca podem apontar para um ambiente compartilhado/remoto.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

AUTH_PROCESS_KEY = "SP-OP-AUTH-001"

#: Artefatos que viajam juntos num deploy por tenant: o BPMN e as DMNs que ele (e o worker de
#: criterios) avaliam. Um businessRuleTask de um processo de tenant resolve a DMN SO' no mesmo
#: tenant (engine_deploy.py, docstring de EngineDeployClient.deploy).
AUTH_ARTIFACTS = (
    "bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn",
    "dmn/auth_admissibility.dmn",
    "dmn/auth_sla.dmn",
    "dmn/auth_auto_approval.dmn",
    "dmn/auth_criteria_contratual.dmn",
)

#: Topico da primeira external task do processo (ST_PublishReceived, BPMN:95-96).
AUTH_FIRST_TOPIC = "operadora.events.publish"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def auth_artifact_paths(spec_processes_dir: Path | None = None) -> list[Path]:
    base = spec_processes_dir or repo_root() / "spec" / "processes"
    paths = [base / rel for rel in AUTH_ARTIFACTS]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"artefatos AUTH ausentes: {missing}")
    return paths


def assert_local_url(url: str) -> str:
    """Devolve ``url`` se o host for local; senao ``ValueError`` (fail-closed).

    Compara o HOSTNAME parseado, nunca prefixo de string: ``http://localhost.evil.example`` e
    ``http://127.0.0.1@evil.example`` sao recusados.
    """
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise ValueError(f"esquema nao suportado para alvo de carga: {parts.scheme!r}")
    if parts.hostname not in LOCAL_HOSTS:
        raise ValueError(
            f"alvo de carga/chaos precisa ser local ({sorted(LOCAL_HOSTS)}); recebido: {parts.hostname!r}"
        )
    return url


def synthetic_tenant() -> str:
    """Tenant descartavel; casa ``engine_deploy._TENANT_RE`` e nao tem ``-`` (business key AUTH)."""
    return f"r086perf_{uuid.uuid4().hex[:12]}"


def synthetic_guia() -> str:
    return f"SINTR086{uuid.uuid4().hex[:16]}"


def auth_business_key(tenant_id: str, numero_guia_tiss: str) -> str:
    """Espelho stdlib da forma contratual ``AUTH-{tenant}-{guia}`` (SP-OP-AUTH-001).

    O teste de integracao confere este espelho contra o compositor real
    ``maezo.tools.process_business_keys.auth_business_key``; o locustfile usa o espelho porque
    nao importa ``maezo`` sob gevent.
    """
    return f"AUTH-{tenant_id}-{numero_guia_tiss}"


#: Variaveis sinteticas das quatro DMNs do AUTH (nomes = inputExpression de cada tabela).
DMN_SCENARIOS: dict[str, dict[str, object]] = {
    "auth_admissibility": {
        "requer_autorizacao": True,
        "documentacao_completa": True,
        "beneficiario_ativo": True,
        "carencia_cumprida": True,
    },
    "auth_sla": {"carater_atendimento": "eletivo", "categoria_procedimento": "consulta"},
    "auth_auto_approval": {
        "auto_criteria_verificado": True,
        "criterio_tecnico_ok": True,
        "criterio_financeiro_ok": True,
        "criterio_regulatorio_ok": True,
        "criterio_contratual_ok": True,
        "carater_atendimento": "eletivo",
    },
    "auth_criteria_contratual": {"tenant_id": "r086perf", "categoria_procedimento": "consulta"},
}


def start_variables(*, requer_autorizacao: bool = True) -> dict[str, object]:
    """Variaveis de start sinteticas (as quatro de admissibilidade + carater/categoria)."""
    return {
        "requer_autorizacao": requer_autorizacao,
        "documentacao_completa": True,
        "beneficiario_ativo": True,
        "carencia_cumprida": True,
        "carater_atendimento": "eletivo",
        "categoria_procedimento": "consulta",
    }


def camunda_variables(variables: dict[str, object]) -> dict[str, dict[str, object]]:
    """Forma REST do engine para tipos escalares (bool antes de int: bool e' subclasse de int)."""
    out: dict[str, dict[str, object]] = {}
    for name, value in variables.items():
        if isinstance(value, bool):
            kind = "Boolean"
        elif isinstance(value, int):
            kind = "Long"
        elif isinstance(value, str):
            kind = "String"
        else:
            raise TypeError(f"variavel sintetica {name!r} com tipo nao escalar: {type(value).__name__}")
        out[name] = {"value": value, "type": kind}
    return out


def nearest_rank_percentile(samples_ms: Sequence[float], pct: float) -> float:
    """Percentil pelo metodo nearest-rank (sem interpolacao): o menor valor com >= pct% abaixo."""
    if not samples_ms:
        raise ValueError("sem amostras")
    if not 0 < pct <= 100:
        raise ValueError(f"percentil fora de (0, 100]: {pct}")
    ordered = sorted(samples_ms)
    rank = math.ceil(pct / 100 * len(ordered))
    return ordered[max(rank, 1) - 1]
