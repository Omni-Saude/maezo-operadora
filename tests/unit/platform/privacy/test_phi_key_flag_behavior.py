"""`modo: off` is byte-identical to the pre-flag behaviour (test (a)), and the modes really work.

The golden table below is the LITERAL pre-change output of the f-strings this change replaced
(`f"CANCEL-{tenant}-{numero_contrato or matricula}"` and friends), written out by hand rather
than computed, so the test cannot pass by accidentally re-deriving the same bug. Every touched
mint/egress path is compared against it under `modo: off`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maezo.agents.fernando.graph import _business_key as fernando_business_key
from maezo.platform.integrations.events_kafka_producer import (
    MIRROR_PAYLOAD_ALLOWLIST,
    _effective_mirror_allowlist,
    scrub_mirror_payload,
)
from maezo.platform.notification_bridge import _cancel_business_key as bridge_cancel_key
from maezo.platform.notification_bridge import (
    _inadimplencia_business_key as bridge_inad_key,
)
from maezo.platform.observability import _build_key_scrubber
from maezo.platform.privacy.key_scrubber import egress_message_key
from maezo.tools.workers.fraude import _cancel_business_key as fraude_cancel_key
from maezo.tools.workers.fraude import _inadimplencia_business_key as fraude_inad_key
from maezo.tools.workers.inadimplencia import _cancel_business_key as inad_cancel_key
from tests.support.privacy_policy import phi_key_mode

_TENANT = "amh"
_CONTRATO = "C-123"
_MATRICULA = "mat-99"
_PSEUDO = "hk1_deadbeef"


# ---------------------------------------------------------------------------
# (a) modo off => zero behaviour change, on every touched path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("numero_contrato", "matricula", "expected"),
    [
        (_CONTRATO, _MATRICULA, f"CANCEL-{_TENANT}-{_CONTRATO}"),
        (_CONTRATO, "", f"CANCEL-{_TENANT}-{_CONTRATO}"),
        ("", _MATRICULA, f"CANCEL-{_TENANT}-{_MATRICULA}"),  # the PHI-bearing fallback
        ("", "", f"CANCEL-{_TENANT}-"),  # degenerate, but preserved exactly
    ],
)
def test_modo_off_cancel_key_matches_the_pre_change_golden(
    tmp_path: Path, numero_contrato: str, matricula: str, expected: str
) -> None:
    with phi_key_mode(tmp_path, "off"):
        assert inad_cancel_key(_TENANT, numero_contrato, matricula) == expected


def test_modo_off_cancel_key_ignores_a_present_pseudo_id(tmp_path: Path) -> None:
    """A `beneficiario_pseudo_id` in the variables must change NOTHING until ratification."""
    with phi_key_mode(tmp_path, "off"):
        assert inad_cancel_key(_TENANT, "", _MATRICULA, _PSEUDO) == f"CANCEL-{_TENANT}-{_MATRICULA}"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"tenant_id": _TENANT, "numero_contrato": _CONTRATO}, f"INAD-{_TENANT}-{_CONTRATO}"),
        ({"tenant_id": _TENANT, "matricula_beneficiario": _MATRICULA}, f"INAD-{_TENANT}-{_MATRICULA}"),
        (
            {"tenant_id": _TENANT, "numero_contrato": "", "matricula_beneficiario": _MATRICULA},
            f"INAD-{_TENANT}-{_MATRICULA}",
        ),
        ({}, "INAD--"),
    ],
)
def test_modo_off_fernando_key_matches_the_pre_change_golden(
    tmp_path: Path, state: dict[str, str], expected: str
) -> None:
    with phi_key_mode(tmp_path, "off"):
        assert fernando_business_key(state) == expected  # type: ignore[arg-type]


def test_modo_off_fraude_and_bridge_composers_unchanged(tmp_path: Path) -> None:
    """The two no-fallback composers were only re-pointed at the shared formatter."""
    with phi_key_mode(tmp_path, "off"):
        assert fraude_cancel_key(_TENANT, _CONTRATO) == f"CANCEL-{_TENANT}-{_CONTRATO}"
        assert fraude_inad_key(_TENANT, _CONTRATO) == f"INAD-{_TENANT}-{_CONTRATO}"
        assert bridge_cancel_key(_TENANT, _CONTRATO) == f"CANCEL-{_TENANT}-{_CONTRATO}"
        assert bridge_inad_key(_TENANT, _CONTRATO) == f"INAD-{_TENANT}-{_CONTRATO}"


def test_all_three_cancel_composers_agree_when_a_contract_number_exists(tmp_path: Path) -> None:
    """BK convergence: with a `numero_contrato` present, every composer mints the SAME key.

    This is the property whose ABSENCE (when only a matricula exists) is defect B-2.
    """
    with phi_key_mode(tmp_path, "off"):
        assert (
            inad_cancel_key(_TENANT, _CONTRATO, _MATRICULA)
            == fraude_cancel_key(_TENANT, _CONTRATO)
            == bridge_cancel_key(_TENANT, _CONTRATO)
        )


def test_modo_off_mirror_allowlist_is_untouched(tmp_path: Path) -> None:
    with phi_key_mode(tmp_path, "off"):
        assert _effective_mirror_allowlist() == MIRROR_PAYLOAD_ALLOWLIST
        payload = {"_business_key": f"CANCEL-{_TENANT}-{_MATRICULA}", "tenant_id": _TENANT}
        assert scrub_mirror_payload(payload) == payload


def test_modo_off_kafka_message_key_is_untouched(tmp_path: Path) -> None:
    with phi_key_mode(tmp_path, "off"):
        key = f"CANCEL-{_TENANT}-{_MATRICULA}"
        assert egress_message_key(key) == key


def test_modo_off_installs_no_structlog_processor(tmp_path: Path) -> None:
    """`setup_observability` must build the exact same processor chain it built before."""
    with phi_key_mode(tmp_path, "off"):
        assert _build_key_scrubber() is None


# ---------------------------------------------------------------------------
# scrub_only / pseudo_keys — the modes are not decorative
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("modo", ["scrub_only", "pseudo_keys"])
def test_scrubbing_modes_revoke_the_business_key_from_the_mirror(tmp_path: Path, modo: str) -> None:
    with phi_key_mode(tmp_path, modo):
        assert "_business_key" not in _effective_mirror_allowlist()
        kept = scrub_mirror_payload({"_business_key": f"CANCEL-{_TENANT}-{_MATRICULA}", "tenant_id": _TENANT})
    assert kept == {"tenant_id": _TENANT}
    assert _MATRICULA not in repr(kept)


@pytest.mark.parametrize("modo", ["scrub_only", "pseudo_keys"])
def test_scrubbing_modes_install_the_structlog_processor(
    tmp_path: Path, modo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from maezo.platform.privacy.key_scrubber import BusinessKeyScrubber

    monkeypatch.setenv("RUNTIME_MODE", "local")
    with phi_key_mode(tmp_path, modo):
        assert isinstance(_build_key_scrubber(), BusinessKeyScrubber)


def test_pseudo_keys_mints_the_pseudo_id_instead_of_the_matricula(tmp_path: Path) -> None:
    """The remediation itself: the valentina precedent applied to CANCEL/INAD."""
    with phi_key_mode(tmp_path, "pseudo_keys"):
        assert inad_cancel_key(_TENANT, "", _MATRICULA, _PSEUDO) == f"CANCEL-{_TENANT}-{_PSEUDO}"
        assert (
            fernando_business_key(  # type: ignore[arg-type]
                {
                    "tenant_id": _TENANT,
                    "matricula_beneficiario": _MATRICULA,
                    "beneficiario_pseudo_id": _PSEUDO,
                }
            )
            == f"INAD-{_TENANT}-{_PSEUDO}"
        )


def test_pseudo_keys_still_prefers_the_contract_number(tmp_path: Path) -> None:
    """A contract number is not a person's identifier — it stays the anchor in every mode."""
    with phi_key_mode(tmp_path, "pseudo_keys"):
        assert inad_cancel_key(_TENANT, _CONTRATO, _MATRICULA, _PSEUDO) == f"CANCEL-{_TENANT}-{_CONTRATO}"


def test_pseudo_keys_falls_back_rather_than_minting_a_degenerate_key(tmp_path: Path) -> None:
    """An absent `beneficiario_pseudo_id` must never produce `CANCEL-{tenant}-`.

    The pseudo id is not universally populated (it is not in `_HANDOFF_CARRY_KEYS`, and only some
    upstreams set it), so `pseudo_keys` has to degrade to the legacy anchor rather than mint an
    empty key — which would collide across every beneficiary in the tenant.
    """
    with phi_key_mode(tmp_path, "pseudo_keys"):
        assert inad_cancel_key(_TENANT, "", _MATRICULA, "") == f"CANCEL-{_TENANT}-{_MATRICULA}"
        assert (
            fernando_business_key({"tenant_id": _TENANT, "matricula_beneficiario": _MATRICULA})  # type: ignore[arg-type]
            == f"INAD-{_TENANT}-{_MATRICULA}"
        )


# ---------------------------------------------------------------------------
# Shadow telemetry — the owner's evidence, and it must carry no content
# ---------------------------------------------------------------------------


def _mint_counter_samples() -> list[tuple[dict[str, str], float]]:
    from maezo.platform.observability import get_metrics_collector

    metric = get_metrics_collector().phi_business_key_mint
    return [
        (dict(sample.labels), sample.value)
        for family in metric.collect()
        for sample in family.samples
        if sample.name.endswith("_total")
    ]


def _sample_value(labels: dict[str, str]) -> float:
    for sample_labels, value in _mint_counter_samples():
        if sample_labels == labels:
            return value
    return 0.0


def test_shadow_counter_counts_matricula_mints_while_modo_is_off(tmp_path: Path) -> None:
    """THE evidence the owner ratifies against: how many keys today carry a raw matricula."""
    labels = {"family": "CANCEL", "modo": "off", "anchor": "matricula"}
    with phi_key_mode(tmp_path, "off"):
        before = _sample_value(labels)
        inad_cancel_key(_TENANT, "", _MATRICULA)
        inad_cancel_key(_TENANT, "", "outra-matricula")
        after = _sample_value(labels)
    assert after - before == 2


def test_shadow_counter_distinguishes_the_safe_anchor(tmp_path: Path) -> None:
    labels = {"family": "CANCEL", "modo": "off", "anchor": "contrato"}
    with phi_key_mode(tmp_path, "off"):
        before = _sample_value(labels)
        inad_cancel_key(_TENANT, _CONTRATO, _MATRICULA)
        after = _sample_value(labels)
    assert after - before == 1


def test_shadow_counter_records_the_pseudo_anchor_once_ratified(tmp_path: Path) -> None:
    labels = {"family": "CANCEL", "modo": "pseudo_keys", "anchor": "pseudo"}
    with phi_key_mode(tmp_path, "pseudo_keys"):
        before = _sample_value(labels)
        inad_cancel_key(_TENANT, "", _MATRICULA, _PSEUDO)
        after = _sample_value(labels)
    assert after - before == 1


def test_the_four_repointed_composers_now_feed_the_shadow_counter(tmp_path: Path) -> None:
    """COUNTER COMPLETENESS: `fraude` and the bridge used to bypass the counter entirely.

    Both minted through the bare formatter `base.contract_business_key`, so every CANCEL started
    by the fraude handoff and by the bridge's FRAUDE->CANCEL rule was missing from
    `anchor="contrato"`. The observability docstring nonetheless called
    `mint_contract_business_key` "the single composer every CANCEL/INAD key is minted through".
    Four mints, four increments — and the output is unchanged (asserted alongside, so a future
    "fix" cannot buy the counter at the cost of a different key).
    """
    cancel = {"family": "CANCEL", "modo": "off", "anchor": "contrato"}
    inad = {"family": "INAD", "modo": "off", "anchor": "contrato"}
    with phi_key_mode(tmp_path, "off"):
        cancel_before, inad_before = _sample_value(cancel), _sample_value(inad)
        assert fraude_cancel_key(_TENANT, _CONTRATO) == f"CANCEL-{_TENANT}-{_CONTRATO}"
        assert bridge_cancel_key(_TENANT, _CONTRATO) == f"CANCEL-{_TENANT}-{_CONTRATO}"
        assert fraude_inad_key(_TENANT, _CONTRATO) == f"INAD-{_TENANT}-{_CONTRATO}"
        assert bridge_inad_key(_TENANT, _CONTRATO) == f"INAD-{_TENANT}-{_CONTRATO}"
        assert _sample_value(cancel) - cancel_before == 2
        assert _sample_value(inad) - inad_before == 2


def test_no_cancel_inad_key_is_minted_outside_the_shared_mint_composer() -> None:
    """The claim "the single composer every CANCEL/INAD key is minted through", made checkable.

    `base.contract_business_key` is the bare formatter — it mints without counting. Exactly two
    call sites in `src/` may use it, both inside `base.py` itself: `mint_contract_business_key`
    (which counts) and `contract_business_key_forms` (which QUERIES for pre-existing instances
    and must NOT count — inflating the mint series would corrupt the owner's evidence). Any other
    module reaching for it is a silently uncounted mint, which is precisely how the
    `anchor="contrato"` series came to under-count before this repair.
    """
    import ast

    src = Path(__file__).resolve().parents[4] / "src" / "maezo"
    offenders: list[str] = []
    for path in sorted(src.rglob("*.py")):
        if path.name == "base.py" and path.parent.name == "workers":
            continue  # the composer's own module — the two sanctioned call sites live here
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            # The bridge aliases the import, so match the resolved tail rather than the literal.
            if name is not None and name.endswith("contract_business_key"):
                if name.startswith(("mint_", "_shared_mint_")):
                    continue
                offenders.append(f"{path}:{node.lineno}  {name}(...)")
    assert not offenders, (
        "CANCEL/INAD key minted through the bare formatter instead of "
        "`mint_contract_business_key` — this mint would never reach the DL-0043 shadow counter:\n"
        + "\n".join(offenders)
    )


def test_shadow_counter_labels_are_a_closed_vocabulary_with_no_content(tmp_path: Path) -> None:
    """A per-instance label here would recreate, in Prometheus, the very leak being measured."""
    with phi_key_mode(tmp_path, "off"):
        inad_cancel_key(_TENANT, _CONTRATO, _MATRICULA)
        inad_cancel_key(_TENANT, "", _MATRICULA)
        samples = _mint_counter_samples()
    assert samples, "the shadow counter emitted nothing"
    for labels, _value in samples:
        assert set(labels) == {"family", "modo", "anchor"}
        assert labels["family"] in {"CANCEL", "INAD"}
        assert labels["modo"] in {"off", "scrub_only", "pseudo_keys"}
        assert labels["anchor"] in {"contrato", "matricula", "pseudo"}
        rendered = repr(labels)
        assert _MATRICULA not in rendered
        assert _CONTRATO not in rendered
        assert _TENANT not in rendered
