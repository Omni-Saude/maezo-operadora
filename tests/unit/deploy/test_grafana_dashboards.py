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

REPARO REP-D12-02 (2026-09-05, VERIFY-D12-02 REVISE F1/F3) acrescenta duas cercas:

(4) **Painel que afirma espelhar um alerta bate com a expr do alerta, de fato.** Um painel
declara a intencao com o campo `maezo_mirrors_alert: "<AlertName>"` (adicionado ao JSON, ignorado
pelo Grafana — campo extra desconhecido). A cerca compara a `expr` do painel contra a `expr` do
`alert:` nomeado em `alert-rules.yml`, modulo APENAS: (a) diferencas de espaco em branco puras da
formatacao multi-linha do YAML block-scalar; (b) um UNICO par de parenteses externo redundante
envolvendo a expressao inteira (a convencao "grafico vs alerta" ja usada por 7 dos 8 paineis deste
PR: o alerta embrulha `(...) > limiar` e o painel arranca so o `> limiar`, nunca o `unless`/o
proprio corpo). Um `> N` que NAO esta no fim da string (por exemplo, gateando um `unless` no meio
da expressao, como `MaezoLifecycleJobFailed`) NAO e considerado "threshold final" — precisa
sobreviver identico nos dois lados. Encontrada e corrigida pelo REP-D12-02: o painel "Lifecycle
CronJob failures" tinha perdido o `> 0` que gate a metade esquerda do `unless`, alargando
silenciosamente quais series o grafico mostra (toda serie `kube_job_status_failed`, nao so as
genuinamente falhas) apesar de a propria descricao do painel afirmar "Mesma expressao".

(5) **Toda label usada em toda expr pertence ao label-set real da metrica.** Para uma metrica
`maezo_*` registrada em `metrics.py`, o label-set permitido e exatamente `labelnames=[...]`
daquele Counter/Histogram (mais `le`, implicito em toda serie `_bucket` de Histogram — nunca
declarado em `labelnames`, e sim injetado pelo `prometheus_client` na exposicao). Para uma metrica
de exporter (`kube_*`/`kafka_*`, nao registrada em `metrics.py`) o label-set permitido e o
conjunto padrao `job`/`instance`/`pod`/`namespace` (job/instance: injetados por TODO
`scrape_configs` de `prometheus.yml` — `job` implicito por `job_name`, `instance` explicito via o
`relabel_configs` do job `maezo-app`; pod/namespace: convencao padrao Kubernetes-SD/kube-state-
metrics, ainda nao exercida por este `prometheus.yml` de dev sem descoberta k8s, mas permitida
proativamente — nunca inventada por painel) UNIDO as labels que aquela MESMA metrica ja usa
literalmente em algum `expr`/corpo de `record:` de `alert-rules.yml` (o mesmo principio de
"ja vetada na arvore" usado para o inventario de NOMES de metrica acima — nunca uma label
inventada so para o dashboard). Uma label usada num seletor `metric{label=...}` e validada contra
o label-set daquela metrica especifica; uma label usada numa clausula `by(...)`/`on(...)`/
`ignoring(...)`/`without(...)` e validada contra a UNIAO dos label-sets de toda metrica citada na
mesma expr (aproximacao conservadora — nao tenta atribuir a clausula a um lado especifico de uma
divisao/`unless`). Superada a lacuna que VERIFY-D12-02 F3 mutou e confirmou NAO pega pela cerca
anterior (`sum by (nonexistent_label) (...)` sobre uma metrica real ficava verde).
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


# ---------------------------------------------------------------------------------------------
# REP-D12-02 (2026-09-05) — F1: painel que afirma espelhar um alerta bate com a expr do alerta.
# ---------------------------------------------------------------------------------------------


def _alert_exprs() -> dict[str, str]:
    """Nome do `alert:` -> texto bruto da sua `expr:` (block-scalar, com quebras de linha), lido
    de `alert-rules.yml` via YAML de verdade (nunca regex sobre o alerta em si — so' sobre
    metrics.py, onde nao ha parser estruturado equivalente)."""
    data = yaml.safe_load(_ALERT_RULES_YML.read_text(encoding="utf-8"))
    alerts: dict[str, str] = {}
    for group in data.get("groups", []):
        for rule in group.get("rules", []):
            if "alert" in rule:
                alerts[rule["alert"]] = rule["expr"]
    assert alerts, f"nenhum `alert:` encontrado em {_ALERT_RULES_YML} — regex/YAML quebrou?"
    return alerts


_TRAILING_THRESHOLD_RE = re.compile(r"\s*>\s*[0-9.]+\s*$")


def _normalize_promql_whitespace(expr: str) -> str:
    """Colapsa quebras de linha/indentacao do YAML block-scalar para uma comparacao estrutural —
    NUNCA remove espaco que mude o significado (so' colapsa runs de whitespace e o espaco colado
    a um parenteses, que o YAML multi-linha introduz e o PromQL ignora)."""
    text = re.sub(r"\s+", " ", expr).strip()
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return text


def _strip_one_redundant_outer_paren_pair(expr: str) -> str:
    """Remove UM par de parenteses externo se — e so' se — ele envolve a expressao INTEIRA (o
    parenteses de abertura so' fecha no ULTIMO caractere da string). Nunca remove um parenteses
    que faz parte de uma chamada de funcao (`rate(...)`) ou de um `unless on (...)` no meio da
    expressao — esses nao envolvem a expressao inteira, entao `depth` volta a 0 antes do fim."""
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        depth = 0
        wraps_whole_expr = True
        for index, char in enumerate(expr):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(expr) - 1:
                    wraps_whole_expr = False
                    break
        if not wraps_whole_expr:
            break
        expr = expr[1:-1].strip()
    return expr


def _canonicalize_promql(expr: str) -> str:
    """Forma canonica para comparar painel vs alerta: whitespace normalizado, um trailing
    `> <threshold>` removido SE ele estiver no fim literal da string (nunca um `> N` que apenas
    aparece no meio, como o que gate um `unless`), e um par de parenteses externo redundante
    removido."""
    text = _normalize_promql_whitespace(expr)
    text = _TRAILING_THRESHOLD_RE.sub("", text).strip()
    text = _strip_one_redundant_outer_paren_pair(text)
    return text


@pytest.mark.parametrize("path", _dashboard_files(), ids=lambda p: p.name)
def test_painel_que_afirma_espelhar_um_alerta_bate_com_a_expr_do_alerta(path: Path) -> None:
    """Todo painel que declara `maezo_mirrors_alert: "<AlertName>"` precisa ter
    `targets[0].expr` estruturalmente IGUAL a `expr` daquele `alert:` em alert-rules.yml, a menos
    apenas de um `> <threshold>` final (ver `_canonicalize_promql`) — nunca uma divergencia
    estrutural silenciosa por tras de uma descricao que afirma "mesma expressao"."""
    alerts = _alert_exprs()
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    for panel in _panels(dashboard):
        alert_name = panel.get("maezo_mirrors_alert")
        if not alert_name:
            continue
        assert alert_name in alerts, (
            f"{path.name}: painel {panel.get('title')!r} declara "
            f"maezo_mirrors_alert={alert_name!r}, mas nenhum `alert: {alert_name}` existe em "
            f"{_ALERT_RULES_YML}"
        )
        targets = panel.get("targets", [])
        assert targets, f"{path.name}: painel {panel.get('title')!r} sem targets"
        panel_expr = _canonicalize_promql(targets[0]["expr"])
        alert_expr = _canonicalize_promql(alerts[alert_name])
        assert panel_expr == alert_expr, (
            f"{path.name}: painel {panel.get('title')!r} declara maezo_mirrors_alert="
            f"{alert_name!r}, mas a expr nao bate (modulo um '> <threshold>' final):\n"
            f"  painel: {panel_expr!r}\n  alerta: {alert_expr!r}"
        )


# ---------------------------------------------------------------------------------------------
# REP-D12-02 (2026-09-05) — F3: toda label usada em toda expr pertence ao label-set real.
# ---------------------------------------------------------------------------------------------


def _metric_labelnames() -> tuple[dict[str, set[str]], set[str]]:
    """(nome_base -> set(labelnames declaradas), set(nomes base que sao Histogram)) — parseado
    de `metrics.py` por varredura de bloco balanceado ate o proximo `registry=self._registry`
    literal (nunca `[^)]*?`: a descricao de alguns instrumentos contem parenteses, o que quebraria
    uma classe de caracteres que exclui ')')."""
    source = _METRICS_PY.read_text(encoding="utf-8")
    block_re = re.compile(
        r'(Counter|Histogram)\(\s*\n?\s*"([a-z0-9_]+)".*?registry=self\._registry',
        re.DOTALL,
    )
    labelnames_by_metric: dict[str, set[str]] = {}
    histogram_bases: set[str] = set()
    for match in block_re.finditer(source):
        kind, name = match.group(1), match.group(2)
        block = match.group(0)
        labelnames_match = re.search(r"labelnames=\[([^\]]*)\]", block)
        labels = (
            set(re.findall(r'"([a-zA-Z0-9_]+)"', labelnames_match.group(1))) if labelnames_match else set()
        )
        labelnames_by_metric[name] = labels
        if kind == "Histogram":
            histogram_bases.add(name)
    assert labelnames_by_metric, f"nenhum Counter/Histogram parseado de {_METRICS_PY}"
    return labelnames_by_metric, histogram_bases


#: Labels que TODO scrape em `prometheus.yml` injeta por construcao do Prometheus, documentadas
#: contra os `scrape_configs` reais daquele arquivo — nunca inventadas: `job` e implicito por
#: `job_name` em TODO job; `instance` e setado explicitamente pelo `relabel_configs` do job
#: `maezo-app`. `pod`/`namespace` sao a convencao padrao Kubernetes-SD/kube-state-metrics para
#: series de exporter — este `prometheus.yml` de dev nao tem descoberta k8s (so' `static_configs`)
#: entao elas nao sao literalmente exercidas hoje, mas sao permitidas proativamente per o escopo
#: do reparo F3 (nunca fabricadas so' para um painel especifico).
_STANDARD_EXPORTER_LABELS: frozenset[str] = frozenset({"job", "instance", "pod", "namespace"})

_SELECTOR_RE = re.compile(r"(\b(?:maezo|kube|kafka)_[a-zA-Z0-9_]*\b)\s*\{([^}]*)\}")
_LABEL_KEY_RE = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:=~?|!~?=)")
_AGG_CLAUSE_RE = re.compile(r"\b(?:by|on|ignoring|without)\s*\(([^)]*)\)")


def _exporter_label_names_for_metric(metric: str) -> set[str]:
    """Labels literalmente usadas com `metric` dentro de algum seletor `metric{...}` de
    alert-rules.yml — o mesmo principio de "ja vetada na arvore" usado para o inventario de NOMES
    de metrica (`_exporter_metric_names_cited_in_alert_rules`), aplicado a labels."""
    source = _ALERT_RULES_YML.read_text(encoding="utf-8")
    labels: set[str] = set()
    for match in re.finditer(re.escape(metric) + r"\{([^}]*)\}", source):
        labels |= set(_LABEL_KEY_RE.findall(match.group(1)))
    return labels


def _allowed_labels_for_metric_token(
    token: str,
    labelnames_by_metric: dict[str, set[str]],
    histogram_bases: set[str],
) -> set[str]:
    """Label-set permitido para um token de metrica (ja confirmado pertencer ao inventario medido
    pela cerca de nomes acima): labelnames declaradas em metrics.py (mais `le` para uma serie
    `_bucket` de Histogram), OU — para uma metrica de exporter/recording-rule sem entrada em
    metrics.py — o conjunto padrao mais as labels ja vetadas em alert-rules.yml para essa metrica."""
    for suffix in _HISTOGRAM_SUFFIXES:
        if token.endswith(suffix):
            base = token[: -len(suffix)]
            if base in histogram_bases:
                labels = set(labelnames_by_metric[base])
                if suffix == "_bucket":
                    labels = labels | {"le"}
                return labels
    if token in labelnames_by_metric:
        return set(labelnames_by_metric[token])
    return _STANDARD_EXPORTER_LABELS | _exporter_label_names_for_metric(token)


def _label_problems_in_expr(
    expr: str,
    labelnames_by_metric: dict[str, set[str]],
    histogram_bases: set[str],
) -> list[str]:
    """Lista de descricoes de problema (vazia se a expr e limpa). Duas formas de uso de label:
    (1) presa a um seletor especifico `metrica{label=...}` — validada contra o label-set DAQUELA
    metrica; (2) numa clausula `by(...)/on(...)/ignoring(...)/without(...)` — validada contra a
    UNIAO dos label-sets de toda metrica citada na mesma expr (aproximacao conservadora: a cerca
    nao tenta resolver a qual lado de uma divisao/`unless` a clausula pertence)."""
    problems: list[str] = []
    referenced_tokens = set(_METRIC_TOKEN_RE.findall(expr))
    union_allowed: set[str] = set()
    for token in referenced_tokens:
        union_allowed |= _allowed_labels_for_metric_token(token, labelnames_by_metric, histogram_bases)

    for metric, body in _SELECTOR_RE.findall(expr):
        allowed = _allowed_labels_for_metric_token(metric, labelnames_by_metric, histogram_bases)
        used = set(_LABEL_KEY_RE.findall(body))
        bad = used - allowed
        if bad:
            problems.append(
                f"seletor {metric}{{...}}: label(is) {sorted(bad)} fora do label-set "
                f"permitido {sorted(allowed)}"
            )

    for clause in _AGG_CLAUSE_RE.findall(expr):
        labels = {token.strip() for token in clause.split(",") if token.strip()}
        bad = labels - union_allowed
        if bad:
            problems.append(
                f"clausula by/on/ignoring/without({clause}): label(is) {sorted(bad)} fora da "
                f"uniao permitida {sorted(union_allowed)}"
            )

    return problems


@pytest.mark.parametrize("path", _dashboard_files(), ids=lambda p: p.name)
def test_toda_label_usada_em_toda_expr_pertence_ao_label_set_real_da_metrica(path: Path) -> None:
    """Nucleo do reparo F3: uma expr pode citar so' metricas reais (a cerca acima ja garante isso)
    E AINDA fabricar uma label que aquela metrica nunca declarou. Esta cerca fecha essa lacuna
    (mutation-provada em VERIFY-D12-02 F3: `sum by (nonexistent_label) (rate(
    maezo_worker_error_count_total[5m]))` ficava verde antes desta cerca existir)."""
    labelnames_by_metric, histogram_bases = _metric_labelnames()
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    for expr in _exprs_in_dashboard(dashboard):
        problems = _label_problems_in_expr(expr, labelnames_by_metric, histogram_bases)
        assert not problems, f"{path.name}: expr {expr!r} usa label fabricada:\n" + "\n".join(problems)


def test_label_por_metrica_nao_esta_vazio_por_regex_quebrada() -> None:
    """Falha alto e claro se a extracao de labelnames por regex parar de encontrar nada — um
    dict vazio faria a cerca de labels acima passar trivialmente (falso positivo), o mesmo
    principio de `test_inventario_medido_nao_esta_vazio_por_regex_quebrada` acima."""
    labelnames_by_metric, histogram_bases = _metric_labelnames()
    assert labelnames_by_metric
    assert histogram_bases
    # Sanidade: algumas labelnames que sabemos existir na arvore neste base.
    assert labelnames_by_metric["maezo_worker_error_count_total"] == {"worker", "topic", "error_type"}
    assert labelnames_by_metric["maezo_agent_latency_seconds"] == set()
    assert "maezo_worker_execution_time_seconds" in histogram_bases
