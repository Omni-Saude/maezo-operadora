"""PROG-COORD-37 (spec W2-MZO-CA b/c): as duas acoes L1 de rascunho do copiloto no L0-core.

`staff_assist_draft` e `retention_staff_assist_draft` entram em `spec/policies/autonomy/L0-core.yaml`
como L1, NAO hard, sem params. Este modulo prova, sobre o arquivo REAL (nunca uma copia escrita a
mao), que:

  1. as duas resolvem a L1, `hard=False`, `params={}` — no core puro e com o overlay real da AMH;
  2. o PEP as resolve a `REQUIRE_HUMAN` (L1: o agente prepara, o humano decide);
  3. "o mais restritivo vence" (`pep._apply_overlay`): um overlay de tenant que tente AFROUXAR
     qualquer uma para L2 ou L3 e' recusado (`PolicyError`), e um overlay que a ENDURECA para L0 e'
     aceito e segue `REQUIRE_HUMAN` (L0 nao-hard) — nunca `ALLOW`;
  4. nenhuma das duas e' hard: fora de `HARD_ACTIONS` e de `_hard_frozen.yaml`, e o validador de
     politicas (`platform/validation/policy.validate_dir`) segue sem erro no diretorio real.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from maezo.gateway.pep import HARD_ACTIONS, PEP, Decision, Level, PolicyError, build_pep, load_matrix
from maezo.platform.validation.policy import validate_dir
from maezo.platform.validation.result import Report

_AUTONOMY_DIR = Path(__file__).resolve().parents[3] / "spec" / "policies" / "autonomy"
_L0_CORE = _AUTONOMY_DIR / "L0-core.yaml"
_HARD_FROZEN = _AUTONOMY_DIR / "_hard_frozen.yaml"

_NOVAS: tuple[str, ...] = ("staff_assist_draft", "retention_staff_assist_draft")


def _real_core_in(tmp_path: Path) -> Path:
    """Copia o L0-core e o `_hard_frozen.yaml` REAIS, para o cross-check de hard rodar no load."""
    core = tmp_path / "L0-core.yaml"
    shutil.copyfile(_L0_CORE, core)
    shutil.copyfile(_HARD_FROZEN, tmp_path / "_hard_frozen.yaml")
    return core


def _overlay(tmp_path: Path, action: str, spec: dict[str, object]) -> Path:
    path = tmp_path / "tenants-t1.yaml"
    path.write_text(yaml.safe_dump({"tenant": "t1", "overrides": {action: spec}}), encoding="utf-8")
    return path


@pytest.mark.parametrize("action", _NOVAS)
def test_declarada_no_core_real_como_l1_nao_hard_sem_params(action: str) -> None:
    raw = yaml.safe_load(_L0_CORE.read_text(encoding="utf-8"))["actions"][action]
    assert raw == {"level": "L1"}


@pytest.mark.parametrize("tenant", ["core", "amh"])
@pytest.mark.parametrize("action", _NOVAS)
def test_resolve_l1_e_require_human(action: str, tenant: str) -> None:
    pep = build_pep(tenant=tenant)
    policy = pep.matrix.policy_for(action)
    assert policy is not None
    assert policy.level is Level.L1
    assert policy.hard is False
    assert policy.params == {}
    assert pep.evaluate(action) is Decision.REQUIRE_HUMAN


@pytest.mark.parametrize("looser", ["L2", "L3"])
@pytest.mark.parametrize("action", _NOVAS)
def test_overlay_nao_afrouxa(tmp_path: Path, action: str, looser: str) -> None:
    core = _real_core_in(tmp_path)
    with pytest.raises(PolicyError, match="LOOSEN"):
        load_matrix(core, tenant="t1", overlay_path=_overlay(tmp_path, action, {"level": looser}))


@pytest.mark.parametrize("action", _NOVAS)
def test_overlay_que_endurece_vence_e_segue_humano(tmp_path: Path, action: str) -> None:
    core = _real_core_in(tmp_path)
    matrix = load_matrix(core, tenant="t1", overlay_path=_overlay(tmp_path, action, {"level": "L0"}))
    policy = matrix.policy_for(action)
    assert policy is not None
    assert policy.level is Level.L0
    assert policy.hard is False
    assert PEP(matrix).evaluate(action) is Decision.REQUIRE_HUMAN


def test_nenhuma_e_hard_e_o_validador_segue_verde() -> None:
    hard_items = yaml.safe_load(_HARD_FROZEN.read_text(encoding="utf-8"))["hard_items"]
    frozen = {entry["action"] for entry in hard_items}
    for action in _NOVAS:
        assert action not in HARD_ACTIONS
        assert action not in frozen
    report = Report()
    validate_dir(_AUTONOMY_DIR, report)
    assert report.ok, report.findings
