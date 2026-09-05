"""Cerca sobre `deploy/observability/dashboards/*.json` (D12-02 / R-030).

DECIDIDO 2026-09-04 pelo dono (OWNER-DECISIONS-REGISTER R-030): versionar dashboards Grafana
como JSON agora, refinando o CONTEUDO quando `D12-01-a` (documento de SLO) existir. Esta cerca
protege a parte que NAO e refino de conteudo: (1) todo JSON de dashboard PARSEIA; (2) toda
query PromQL de todo painel referencia uma serie que de fato existe na arvore — um contador/
histograma registrado em `src/maezo/runtime/metrics.py`, uma recording rule `record:` de
`deploy/observability/alert-rules.yml`, ou uma serie de exporter (`kube_job_*`, `kafka_*`) ja
citada literalmente em algum `expr:` daquele arquivo (nunca uma metrica inventada apenas para o
dashboard); (3) todo dashboard declara `uid`/`title`/`schemaVersion`. Deriva o inventario de
metricas do PROPRIO codigo-fonte (regex sobre metrics.py + alert-rules.yml), nunca uma lista
hardcoded paralela que possa driftar do que `src/` de fato emite.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DASHBOARDS_DIR = _REPO_ROOT / "deploy" / "observability" / "dashboards"
_METRICS_PY = _REPO_ROOT / "src" / "maezo" / "runtime" / "metrics.py"
_ALERT_RULES_YML = _REPO_ROOT / "deploy" / "observability" / "alert-rules.yml"
_DOCKER_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_DASHBOARDS_PROVIDER_YAML = (
    _REPO_ROOT / "deploy" / "observability" / "grafana-provisioning" / "dashboards" / "dashboards.yaml"
)
_DATASOURCES_YAML = (
    _REPO_ROOT / "deploy" / "observability" / "grafana-provisioning" / "datasources" / "datasources.yaml"
)

#: prometheus_client suffixes a Histogram's registered base name with these when exposed —
#: `histogram_quantile(...)` / `rate(...)` always target the suffixed series, never the bare name.
_HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count")

#: PromQL/YAML tokens that share the `maezo_`/`kube_`/`kafka_` metric-name shape by pure
#: coincidence in this codebase — none exist; kept empty on purpose so a future false-positive
#: has one obvious place to add an entry with a comment, instead of silently loosening the regex.
_NON_METRIC_TOKENS: frozenset[str] = frozenset()


def _dashboard_files() -> list[Path]:
    files = sorted(_DASHBOARDS_DIR.glob("*.json"))
    assert files, f"nenhum dashboard JSON encontrado em {_DASHBOARDS_DIR}"
    return files


def _registered_metric_names() -> tuple[set[str], set[str]]:
    """Retorna (counters, histograms) — os NOMES BASE registrados em MetricsCollector.

    `Counter("maezo_x_total", ...)` expoe a serie exatamente como `maezo_x_total` (o nome ja
    termina em `_total`); `Histogram("maezo_y_seconds", ...)` expoe
    `maezo_y_seconds_bucket`/`_sum`/`_count` — o nome BASE, sem sufixo, e o que aparece aqui.
    """
    source = _METRICS_PY.read_text(encoding="utf-8")
    counters = set(re.findall(r'Counter\(\s*\n?\s*"([a-z0-9_]+)"', source))
    histograms = set(re.findall(r'Histogram\(\s*\n?\s*"([a-z0-9_]+)"', source))
    assert counters, f"nenhum Counter() encontrado em {_METRICS_PY} — regex quebrou?"
    assert histograms, f"nenhum Histogram() encontrado em {_METRICS_PY} — regex quebrou?"
    return counters, histograms


def _recording_rule_names() -> set[str]:
    """Nomes de toda `record:` em alert-rules.yml — series de recording rule sao GAUGEs/scalars
    diretos: nunca ganham sufixo `_bucket`/`_sum`/`_count`."""
    source = _ALERT_RULES_YML.read_text(encoding="utf-8")
    names = set(re.findall(r"^\s*-\s*record:\s*(\S+)\s*$", source, re.MULTILINE))
    assert names, f"nenhuma `record:` encontrada em {_ALERT_RULES_YML} — regex quebrou?"
    return names


def _exporter_metric_names_cited_in_alert_rules() -> set[str]:
    """Nomes de metrica de EXPORTER (kube-state-metrics, kafka_exporter) que ja aparecem
    literalmente em algum `expr:`/corpo de recording rule do proprio alert-rules.yml — a prova de
    que aquela serie ja foi vetada ali (nunca uma metrica de exporter inventada so para o
    dashboard)."""
    source = _ALERT_RULES_YML.read_text(encoding="utf-8")
    return set(re.findall(r"\b(?:kube|kafka)_[a-zA-Z0-9_]*\b", source))


def _allowed_metric_basenames() -> tuple[set[str], set[str]]:
    """(nomes_completos_permitidos, nomes_base_de_histogram) — o inventario medido inteiro."""
    counters, histograms = _registered_metric_names()
    recording_rules = _recording_rule_names()
    exporter_names = _exporter_metric_names_cited_in_alert_rules()
    full_names = counters | recording_rules | exporter_names
    return full_names, histograms


_METRIC_TOKEN_RE = re.compile(r"\b(?:maezo|kube|kafka)_[a-zA-Z0-9_]*\b")


def _panels(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    return dashboard.get("panels", [])


def _exprs_in_dashboard(dashboard: dict[str, Any]) -> list[str]:
    exprs: list[str] = []
    for panel in _panels(dashboard):
        for target in panel.get("targets", []):
            expr = target.get("expr")
            if expr:
                exprs.append(expr)
    return exprs


@pytest.mark.parametrize("path", _dashboard_files(), ids=lambda p: p.name)
def test_dashboard_json_parseia(path: Path) -> None:
    """Todo arquivo em deploy/observability/dashboards/*.json e JSON valido."""
    json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", _dashboard_files(), ids=lambda p: p.name)
def test_dashboard_tem_uid_title_schema_version(path: Path) -> None:
    """Todo dashboard declara uid/title/schemaVersion (D12-02 escopo minimo)."""
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(dashboard.get("uid"), str) and dashboard["uid"], f"{path.name}: uid ausente/vazio"
    assert isinstance(dashboard.get("title"), str) and dashboard["title"], f"{path.name}: title ausente/vazio"
    assert isinstance(dashboard.get("schemaVersion"), int) and dashboard["schemaVersion"] > 0, (
        f"{path.name}: schemaVersion ausente/invalido"
    )


def test_dashboard_uids_sao_unicos() -> None:
    """Dois dashboards com o mesmo uid colidem no provisioning do Grafana."""
    uids = [json.loads(p.read_text(encoding="utf-8"))["uid"] for p in _dashboard_files()]
    assert len(uids) == len(set(uids)), f"uids duplicados: {uids}"


@pytest.mark.parametrize("path", _dashboard_files(), ids=lambda p: p.name)
def test_dashboard_tem_pelo_menos_um_painel(path: Path) -> None:
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    assert _panels(dashboard), f"{path.name}: dashboard sem nenhum painel"


@pytest.mark.parametrize("path", _dashboard_files(), ids=lambda p: p.name)
def test_todo_painel_tem_pelo_menos_um_target_com_expr(path: Path) -> None:
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    for panel in _panels(dashboard):
        targets = panel.get("targets", [])
        assert targets, f"{path.name}: painel {panel.get('title')!r} sem targets"
        for target in targets:
            expr = target.get("expr")
            assert expr and expr.strip(), f"{path.name}: painel {panel.get('title')!r} com expr vazia"


@pytest.mark.parametrize("path", _dashboard_files(), ids=lambda p: p.name)
def test_toda_metrica_referenciada_pertence_ao_inventario_medido(path: Path) -> None:
    """O nucleo da cerca: cada nome de metrica citado em um `expr:` de painel precisa ser um
    Counter/Histogram registrado em metrics.py, uma recording rule de alert-rules.yml, ou uma
    serie de exporter ja citada em alert-rules.yml — nunca uma metrica fabricada so para o
    dashboard (ver docstring do modulo)."""
    full_names, histogram_bases = _allowed_metric_basenames()
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    for expr in _exprs_in_dashboard(dashboard):
        for token in _METRIC_TOKEN_RE.findall(expr):
            if token in _NON_METRIC_TOKENS:
                continue
            if token in full_names:
                continue
            stripped = None
            for suffix in _HISTOGRAM_SUFFIXES:
                if token.endswith(suffix):
                    stripped = token[: -len(suffix)]
                    break
            if stripped is not None and stripped in histogram_bases:
                continue
            pytest.fail(
                f"{path.name}: metrica {token!r} (na expr {expr!r}) nao pertence ao inventario "
                "medido (Counter/Histogram de metrics.py, `record:` de alert-rules.yml, ou "
                "serie de exporter ja citada em alert-rules.yml)"
            )


def test_inventario_medido_nao_esta_vazio_por_regex_quebrada() -> None:
    """Falha alto e claro se a extracao por regex do inventario parar de encontrar nada — uma
    lista vazia faria a cerca acima passar trivialmente (falso positivo)."""
    full_names, histogram_bases = _allowed_metric_basenames()
    assert full_names
    assert histogram_bases
    # Sanidade: alguns nomes que sabemos existir na arvore neste base.
    assert "maezo_agent_errors_total" in full_names
    assert "maezo_worker_execution_time_seconds" in histogram_bases
    assert "maezo_dead_letter_queue_size" in full_names


def test_dashboards_provider_yaml_aponta_para_o_path_montado_no_docker_compose() -> None:
    """`grafana-provisioning/dashboards/dashboards.yaml`.providers[].options.path precisa bater
    exatamente com o segundo alvo de mount do servico `grafana` em docker-compose.yml — do
    contrario o file provider nunca encontra os JSON (a lacuna que
    docs/runbooks/devops-stack.md documentava antes desta cerca existir)."""
    provider_config = yaml.safe_load(_DASHBOARDS_PROVIDER_YAML.read_text(encoding="utf-8"))
    providers = provider_config["providers"]
    assert providers, "dashboards.yaml sem nenhum provider"
    paths = {p["options"]["path"] for p in providers}

    compose_source = _DOCKER_COMPOSE.read_text(encoding="utf-8")
    mount_match = re.search(
        r"deploy/observability/dashboards:(/etc/grafana/provisioning/dashboards/\S+?):ro",
        compose_source,
    )
    assert mount_match, "docker-compose.yml: mount de deploy/observability/dashboards nao encontrado"
    mounted_path = mount_match.group(1)

    assert mounted_path in paths, (
        f"dashboards.yaml aponta para {sorted(paths)}, mas docker-compose.yml monta em "
        f"{mounted_path!r} — o file provider nunca vai encontrar os JSON"
    )


def test_datasource_uid_referenciado_pelos_paineis_bate_com_o_provisionado() -> None:
    """Todo painel usa `datasource.uid` — precisa ser exatamente o uid que
    grafana-provisioning/datasources/datasources.yaml provisiona, ou o painel fica sem fonte de
    dados no Grafana provisionado."""
    datasource_config = yaml.safe_load(_DATASOURCES_YAML.read_text(encoding="utf-8"))
    provisioned_uids = {ds["uid"] for ds in datasource_config["datasources"]}
    assert provisioned_uids, "datasources.yaml sem nenhum datasource"

    for path in _dashboard_files():
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        for panel in _panels(dashboard):
            ds = panel.get("datasource")
            assert ds is not None, f"{path.name}: painel {panel.get('title')!r} sem datasource"
            assert ds.get("uid") in provisioned_uids, (
                f"{path.name}: painel {panel.get('title')!r} referencia datasource uid "
                f"{ds.get('uid')!r}, nao provisionado ({sorted(provisioned_uids)})"
            )
