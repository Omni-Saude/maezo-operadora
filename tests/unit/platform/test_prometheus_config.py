"""Unit tests for Prometheus config and alert rules validation.

TDD London School — validates deploy/observability/ configs.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _repo_root() -> Path:
    """Return the repository root directory."""
    return Path(__file__).resolve().parents[3]


def test_prometheus_config_exists() -> None:
    """prometheus.yml must exist in deploy/observability/."""
    config_path = _repo_root() / "deploy" / "observability" / "prometheus.yml"
    assert config_path.exists(), f"Missing: {config_path}"
    assert config_path.is_file(), f"Not a file: {config_path}"


def test_prometheus_config_valid_yaml() -> None:
    """prometheus.yml must be valid YAML."""
    config_path = _repo_root() / "deploy" / "observability" / "prometheus.yml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    assert config is not None, "prometheus.yml is empty or invalid YAML"
    assert isinstance(config, dict), "prometheus.yml must be a YAML mapping"


def test_prometheus_has_global_config() -> None:
    """prometheus.yml must have global.scrape_interval."""
    config_path = _repo_root() / "deploy" / "observability" / "prometheus.yml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    assert "global" in config, "Missing 'global' section"
    assert "scrape_interval" in config["global"], "Missing scrape_interval"


def test_prometheus_has_scrape_configs() -> None:
    """prometheus.yml must have scrape_configs with at least one job."""
    config_path = _repo_root() / "deploy" / "observability" / "prometheus.yml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    assert "scrape_configs" in config, "Missing 'scrape_configs'"
    assert len(config["scrape_configs"]) > 0, "scrape_configs must have at least one job"


def test_prometheus_scrapes_maezo_metrics() -> None:
    """prometheus.yml must have a scrape job for maezo metrics."""
    config_path = _repo_root() / "deploy" / "observability" / "prometheus.yml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    job_names = [job.get("job_name", "") for job in config["scrape_configs"]]
    assert any("maezo" in name for name in job_names), f"No maezo scrape job found in: {job_names}"


def test_alert_rules_exists() -> None:
    """alert-rules.yml must exist in deploy/observability/."""
    rules_path = _repo_root() / "deploy" / "observability" / "alert-rules.yml"
    assert rules_path.exists(), f"Missing: {rules_path}"
    assert rules_path.is_file(), f"Not a file: {rules_path}"


def test_alert_rules_valid_yaml() -> None:
    """alert-rules.yml must be valid YAML."""
    rules_path = _repo_root() / "deploy" / "observability" / "alert-rules.yml"
    with open(rules_path) as f:
        rules = yaml.safe_load(f)
    assert rules is not None, "alert-rules.yml is empty or invalid YAML"


def test_alert_rules_has_groups() -> None:
    """alert-rules.yml must have groups with at least one alert rule group."""
    rules_path = _repo_root() / "deploy" / "observability" / "alert-rules.yml"
    with open(rules_path) as f:
        rules = yaml.safe_load(f)

    assert "groups" in rules, "Missing 'groups' section"
    assert len(rules["groups"]) > 0, "At least one alert rule group required"


def test_alert_rules_cover_sla_breach() -> None:
    """alert-rules.yml must include an SLA breach alert."""
    rules_path = _repo_root() / "deploy" / "observability" / "alert-rules.yml"
    with open(rules_path) as f:
        rules = yaml.safe_load(f)

    alert_names: list[str] = []
    for group in rules["groups"]:
        for rule in group.get("rules", []):
            alert_names.append(rule.get("alert", ""))

    assert any("SLA" in name or "sla" in name.lower() for name in alert_names), (
        f"No SLA breach alert found in: {alert_names}"
    )


def test_alert_rules_cover_crash_loop() -> None:
    """alert-rules.yml must include a crash-loop alert."""
    rules_path = _repo_root() / "deploy" / "observability" / "alert-rules.yml"
    with open(rules_path) as f:
        rules = yaml.safe_load(f)

    alert_names: list[str] = []
    for group in rules["groups"]:
        for rule in group.get("rules", []):
            alert_names.append(rule.get("alert", ""))

    has_crash = any("crash" in name.lower() or "CrashLoop" in name for name in alert_names)
    assert has_crash, f"No crash-loop alert found in: {alert_names}"


def test_alert_rules_cover_dead_letter() -> None:
    """alert-rules.yml must include a dead-letter queue alert."""
    rules_path = _repo_root() / "deploy" / "observability" / "alert-rules.yml"
    with open(rules_path) as f:
        rules = yaml.safe_load(f)

    alert_names: list[str] = []
    for group in rules["groups"]:
        for rule in group.get("rules", []):
            alert_names.append(rule.get("alert", ""))

    has_dlq = any("dead" in name.lower() or "DeadLetter" in name or "DLQ" in name for name in alert_names)
    assert has_dlq, f"No dead-letter alert found in: {alert_names}"
