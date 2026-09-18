"""Unit tests for `maezo.platform.integrations.partition_key` (GAP-SC-04-a, audit D5 / gvr-d05).

Proves the four properties the ordering fix rests on:
  1. DETERMINISM + PER-ENTITY CO-LOCATION — two events about the same entity always derive the
     same key (the property that makes a >1-partition topic safe to consume from >1 replica).
  2. THE CHAIN'S ORDER — business key beats family anchors beats `{tenant}|{process_instance}`;
     an entity whose payload gains/loses an incidental field does not migrate partitions.
  3. PHI REFUSAL — no anchor field name is a `PHI_PROCESS_VARS` name, enforced at IMPORT, and the
     import-time guard is itself exercised (not merely asserted to exist).
  4. HONEST ABSENCE — nothing derivable returns `None` rather than a plausible-looking key; the
     producer's fail-closed gate is what turns that into a refusal.
"""

from __future__ import annotations

import importlib

import pytest

from maezo.platform.integrations import partition_key as pk
from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS

_CONTAS_TOPIC = "agents.events.contas.completed"
_FRAUDE_TOPIC = "agents.events.fraude.completed"
_CRED_TOPIC = "agents.events.cred.completed"
_CANCEL_TOPIC = "agents.events.cancel.completed"
_INAD_TOPIC = "agents.events.inadimplencia.completed"
_RECURSO_TOPIC = "agents.events.recurso.sla_breached"
_ANSSUBMIT_TOPIC = "agents.events.anssubmit.completed"
_AUTH_TOPIC = "agents.events.auth.completed"  # family with NO declared anchor group


class _FakeTask:
    """Minimal `ExternalTask` shape (`partition_key_for_task` reads it structurally)."""

    def __init__(
        self,
        *,
        business_key: str | None = "",
        process_instance_id: str = "",
        variables: dict[str, str] | None = None,
    ) -> None:
        self.business_key = business_key
        self.process_instance_id = process_instance_id
        self.topic = "operadora.events.publish"
        self.variables = variables if variables is not None else {}


# ---------------------------------------------------------------------------
# topic_family
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        (pk.NOTIFICATIONS_TOPIC, pk.NOTIFICATIONS_FAMILY),
        (_CONTAS_TOPIC, "contas"),
        (_FRAUDE_TOPIC, "fraude"),
        (_CRED_TOPIC, "cred"),
        (_CANCEL_TOPIC, "cancel"),
        (_INAD_TOPIC, "inadimplencia"),
        (_RECURSO_TOPIC, "recurso"),
        (_ANSSUBMIT_TOPIC, "anssubmit"),
        (_AUTH_TOPIC, "auth"),
        # 3-segment `agents.events.process_completed` is NOT the 4-segment domain shape.
        ("agents.events.process_completed", ""),
        ("operadora.escalation.notify_team", ""),
        ("", ""),
    ],
)
def test_topic_family_resolution(topic: str, expected: str) -> None:
    assert pk.topic_family(topic) == expected


# ---------------------------------------------------------------------------
# (3) PHI refusal — the invariant that makes this table safe to extend
# ---------------------------------------------------------------------------


def test_no_anchor_field_is_a_phi_process_var() -> None:
    """A Kafka message key rides the broker's own partition metadata and is visible to every
    consumer group — a PHI-named anchor here would be a worse leak than a payload field."""
    assert pk.anchor_fields() & PHI_PROCESS_VARS == frozenset()


def test_anchor_fields_never_include_matricula_beneficiario() -> None:
    """Named explicitly (not only via the set intersection above) because `matricula_beneficiario`
    is the ONE PHI name this platform is documented to mint into business keys today (DL-0043 leg
    (c), `phi_key_policy.py`'s module docstring) — the exact field a future editor would be
    tempted to reach for when a CANCEL/INAD payload carries no `numero_contrato`."""
    assert "matricula_beneficiario" not in pk.anchor_fields()


def test_import_guard_refuses_a_phi_named_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard is EXERCISED, not merely present: inject a PHI-named anchor and re-run it.

    Goes RED if `_assert_anchors_phi_free` is ever softened into a log line — the failure mode a
    "we have a check for that" comment cannot distinguish from a real gate.
    """
    poisoned = dict(pk.ENTITY_ANCHORS)
    poisoned["cancel"] = ("matricula_beneficiario",)
    monkeypatch.setattr(pk, "ENTITY_ANCHORS", poisoned)
    with pytest.raises(RuntimeError, match="PHI-named fields in ENTITY_ANCHORS"):
        pk._assert_anchors_phi_free()


def test_module_imports_cleanly_with_the_real_table() -> None:
    """The other half of the guard proof: the SHIPPED table passes it (a guard that fails on
    everything proves nothing)."""
    importlib.reload(pk)
    assert pk.anchor_fields()


# ---------------------------------------------------------------------------
# (2) The chain — arm by arm, table-driven per family
# ---------------------------------------------------------------------------


def test_business_key_wins_over_every_other_arm() -> None:
    """Arm (2) beats the family anchors: the engine business key IS the entity identity every
    SP-OP-* contract declares, and it is what `start_process_idempotent` dedups on."""
    derived = pk.derive_partition_key(
        _CONTAS_TOPIC,
        {
            "_business_key": "RECURSO-amh-GUIA-1-GLOSA-1",
            "_process_instance_id": "pi-1",
            "tenant_id": "amh",
            "numero_guia_tiss": "GUIA-1",
            "glosa_id": "GLOSA-1",
        },
    )
    assert derived == "RECURSO-amh-GUIA-1-GLOSA-1"


@pytest.mark.parametrize(
    ("topic", "payload", "expected"),
    [
        (
            _CONTAS_TOPIC,
            {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1", "glosa_id": "GLOSA-1"},
            "amh|GUIA-1|GLOSA-1",
        ),
        (
            _RECURSO_TOPIC,
            {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1", "glosa_id": "GLOSA-1"},
            "amh|GUIA-1|GLOSA-1",
        ),
        (_FRAUDE_TOPIC, {"tenant_id": "amh", "numero_caso": "CASO-1"}, "amh|CASO-1"),
        (_CRED_TOPIC, {"tenant_id": "amh", "prestador_id": "PREST-1"}, "amh|PREST-1"),
        (_CANCEL_TOPIC, {"tenant_id": "amh", "numero_contrato": "CTR-1"}, "amh|CTR-1"),
        (_INAD_TOPIC, {"tenant_id": "amh", "numero_contrato": "CTR-1"}, "amh|CTR-1"),
        (
            _ANSSUBMIT_TOPIC,
            {"tenant_id": "amh", "report_type": "RN_124_SIP", "competencia": "2026-08"},
            "amh|RN_124_SIP|2026-08",
        ),
    ],
)
def test_family_anchor_derivation(topic: str, payload: dict[str, str], expected: str) -> None:
    """Arm (3), one row per family whose anchor group is copied from an existing business-key
    derivation in `notification_bridge.py` (cited inline in `ENTITY_ANCHORS`)."""
    assert pk.derive_partition_key(topic, payload) == expected


def test_family_with_no_declared_anchor_falls_through_to_process_instance() -> None:
    """`auth` has NO anchor group — deliberately, rather than a guessed one. It must fall to arm
    (4), never to a partial/invented key."""
    assert (
        pk.derive_partition_key(
            _AUTH_TOPIC, {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1", "_process_instance_id": "pi-9"}
        )
        == "amh|pi-9"
    )


def test_incomplete_anchor_group_falls_through_rather_than_deriving_a_partial_key() -> None:
    """A CONTAS payload with `numero_guia_tiss` but no `glosa_id` must NOT derive `amh|GUIA-1`:
    a partial key would co-locate every glosa of one guia AND diverge from the full-anchor key
    for the same entity — two partitions for one entity, the defect this module closes."""
    assert (
        pk.derive_partition_key(
            _CONTAS_TOPIC, {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1", "_process_instance_id": "pi-2"}
        )
        == "amh|pi-2"
    )


def test_process_instance_fallback_requires_nothing_but_the_instance_id() -> None:
    assert pk.derive_partition_key(_AUTH_TOPIC, {"_process_instance_id": "pi-3"}) == "pi-3"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tenant_id": "amh"},
        {"tenant_id": "amh", "_business_key": "   "},
        {"tenant_id": "amh", "_process_instance_id": None},
        {"tenant_id": None, "_process_instance_id": ""},
    ],
)
def test_nothing_derivable_returns_none(payload: dict[str, object]) -> None:
    """(4) HONEST ABSENCE: `None`, never a plausible-looking key built out of whatever was there."""
    assert pk.derive_partition_key(_CONTAS_TOPIC, payload) is None


def test_explicit_none_anchor_is_treated_as_absent_not_as_the_string_none() -> None:
    """`notification_bridge._non_blank`'s own finding, re-proven here: `str(None) == "None"` would
    mint `amh|None|None` — a key that LOOKS usable and silently co-locates every anchor-less event
    of a tenant on one partition."""
    key = pk.derive_partition_key(
        _CONTAS_TOPIC,
        {"tenant_id": "amh", "numero_guia_tiss": None, "glosa_id": None, "_process_instance_id": "pi-4"},
    )
    assert key == "amh|pi-4"


# ---------------------------------------------------------------------------
# (1) The ordering property the whole slice exists for
# ---------------------------------------------------------------------------


def test_two_events_about_the_same_entity_derive_the_same_key() -> None:
    """The ordering property, stated directly: same entity -> same key -> same partition -> the
    broker's own per-partition FIFO preserves their relative order for any number of consumers."""
    received = pk.derive_partition_key(
        _CONTAS_TOPIC,
        {"tenant_id": "amh", "numero_guia_tiss": "GUIA-7", "glosa_id": "GLOSA-7", "fase": "recebido"},
    )
    completed = pk.derive_partition_key(
        _CONTAS_TOPIC,
        {
            "tenant_id": "amh",
            "numero_guia_tiss": "GUIA-7",
            "glosa_id": "GLOSA-7",
            "desfecho": "encaminhada_recurso",
            "numero_lote_tiss": "LOTE-7",
        },
    )
    assert received == completed == "amh|GUIA-7|GLOSA-7"


def test_two_events_of_the_same_process_instance_derive_the_same_key() -> None:
    """Same property one arm down: a family with no anchor group still co-locates every event of
    one process instance."""
    first = pk.derive_partition_key(_AUTH_TOPIC, {"tenant_id": "amh", "_process_instance_id": "pi-8"})
    second = pk.derive_partition_key(
        _AUTH_TOPIC, {"tenant_id": "amh", "_process_instance_id": "pi-8", "fase": "concluido"}
    )
    assert first == second == "amh|pi-8"


def test_different_entities_of_one_tenant_derive_different_keys() -> None:
    """The complement: co-location must not degenerate into "one tenant, one partition" (which
    would make the >1-replica scale-out pointless)."""
    a = pk.derive_partition_key(_FRAUDE_TOPIC, {"tenant_id": "amh", "numero_caso": "CASO-A"})
    b = pk.derive_partition_key(_FRAUDE_TOPIC, {"tenant_id": "amh", "numero_caso": "CASO-B"})
    assert a != b


def test_same_entity_id_under_different_tenants_derives_different_keys() -> None:
    """Tenant is a structural prefix (mirrors `notification_bridge._anchored`'s own rule) so two
    tenants' identically-numbered cases never share a key."""
    a = pk.derive_partition_key(_FRAUDE_TOPIC, {"tenant_id": "amh", "numero_caso": "CASO-1"})
    b = pk.derive_partition_key(_FRAUDE_TOPIC, {"tenant_id": "acme", "numero_caso": "CASO-1"})
    assert a != b


# ---------------------------------------------------------------------------
# partition_key_for_task — the worker call sites' seam
# ---------------------------------------------------------------------------


def test_task_business_key_is_used_verbatim_under_the_shipped_policy() -> None:
    """Under the shipped DL-0043 `off` policy `egress_message_key` returns the key untouched —
    so every pre-GAP-SC-04-a call site (`key=task.business_key or None`) keeps its exact key."""
    task = _FakeTask(business_key="RECURSO-amh-GUIA-1-GLOSA-1", process_instance_id="pi-1")
    assert (
        pk.partition_key_for_task(task, pk.NOTIFICATIONS_TOPIC, {"tenant_id": "amh"})
        == "RECURSO-amh-GUIA-1-GLOSA-1"
    )


def test_blank_task_business_key_falls_to_the_payload_anchors_not_to_none() -> None:
    """THE DEFECT, stated as a test: `task.business_key or None` returned `None` here — an unkeyed,
    round-robin publish. It now derives a stable per-entity key."""
    task = _FakeTask(business_key="", process_instance_id="pi-5", variables={"tenant_id": "amh"})
    key = pk.partition_key_for_task(
        task, _CONTAS_TOPIC, {"tenant_id": "amh", "numero_guia_tiss": "GUIA-2", "glosa_id": "GLOSA-2"}
    )
    assert key == "amh|GUIA-2|GLOSA-2"


def test_blank_business_key_and_no_anchors_uses_the_task_process_instance() -> None:
    task = _FakeTask(business_key=None, process_instance_id="pi-6", variables={"tenant_id": "amh"})
    assert pk.partition_key_for_task(task, _AUTH_TOPIC, {"severity": "grave"}) == "amh|pi-6"


def test_tenant_is_read_from_the_payload_when_the_task_variables_lack_it() -> None:
    task = _FakeTask(business_key="", process_instance_id="pi-7", variables={})
    assert pk.partition_key_for_task(task, _AUTH_TOPIC, {"tenant_id": "acme"}) == "acme|pi-7"


def test_task_without_business_key_or_process_instance_yields_none() -> None:
    """A real external task always carries a process-instance id, so this is unreachable in
    production — kept honest rather than asserted away, and it is what the producer's fail-closed
    gate then refuses."""
    task = _FakeTask(business_key="", process_instance_id="", variables={"tenant_id": "amh"})
    assert pk.partition_key_for_task(task, _AUTH_TOPIC, {"tenant_id": "amh"}) is None


def test_partition_key_for_task_without_a_payload_still_uses_the_task_identity() -> None:
    task = _FakeTask(business_key="", process_instance_id="pi-10", variables={"tenant_id": "amh"})
    assert pk.partition_key_for_task(task, _AUTH_TOPIC) == "amh|pi-10"


# ---------------------------------------------------------------------------
# Arm (3) on the SHARED bridge topic — the family comes from the payload's `type`.
#
# The module originally claimed arm (3) was reached "in practice" by the worker NOTIFICATION dicts
# published to `operadora.notifications.internal`. It was not: that topic's `topic_family` is
# `"notifications"`, which has no anchor group, so 16 of the 17 migrated call sites could only ever
# reach arm (1) or arm (4) — per-INSTANCE ordering, not per-entity. `anchor_family` closes that by
# reading the `{familia}.{acao}` discriminator every worker notification already stamps.
# ---------------------------------------------------------------------------


_RECURSO_NOTIFICATION = {
    "type": "recurso.notify_sla_risk",  # workers/recurso.py:688
    "tenant_id": "amh",
    "numero_guia_tiss": "GUIA-1",
    "glosa_id": "GLOSA-9",
}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"type": "recurso.notify_sla_risk"}, "recurso"),
        ({"type": "anssubmit.retransmit"}, "anssubmit"),
        ({"type": "lgpd.send_response"}, "lgpd"),  # no anchor group -> falls through, honestly
        ({"type": "agents.events.contas.completed"}, "agents"),  # prefix-only, never a match here
        ({"type": "semponto"}, "semponto"),
        ({"type": "   "}, ""),
        ({"type": None}, ""),
        ({}, ""),
    ],
)
def test_notification_family_reads_the_type_prefix(payload: dict[str, object], expected: str) -> None:
    assert pk.notification_family(payload) == expected


def test_anchor_family_uses_the_payload_type_only_on_the_shared_notifications_topic() -> None:
    """The topic is the authority everywhere else. A `type` field on an `agents.events.*` message
    must never re-route that message's key away from the family its own topic declares."""
    assert pk.anchor_family(pk.NOTIFICATIONS_TOPIC, _RECURSO_NOTIFICATION) == "recurso"
    assert pk.anchor_family(_CONTAS_TOPIC, _RECURSO_NOTIFICATION) == "contas"
    assert pk.anchor_family(_CONTAS_TOPIC, {}) == "contas"


def test_notifications_topic_anchors_fire_for_a_recurso_notification() -> None:
    """The behavioural fix: a notification about one guia/glosa now gets a per-ENTITY key on the
    shared topic instead of `{tenant}|{process_instance_id}`."""
    assert pk.derive_partition_key(pk.NOTIFICATIONS_TOPIC, _RECURSO_NOTIFICATION) == "amh|GUIA-1|GLOSA-9"


def test_two_instances_notifying_about_one_guia_share_a_partition_key() -> None:
    """What MAJOR-3 reported as broken, pinned. Two DIFFERENT process instances with blank business
    keys, notifying about the SAME guia/glosa on the shared topic, used to derive `amh|pi-A` and
    `amh|pi-B` — different partitions for one entity."""
    task_a = _FakeTask(business_key="", process_instance_id="pi-A", variables={"tenant_id": "amh"})
    task_b = _FakeTask(business_key="", process_instance_id="pi-B", variables={"tenant_id": "amh"})

    key_a = pk.partition_key_for_task(task_a, pk.NOTIFICATIONS_TOPIC, dict(_RECURSO_NOTIFICATION))
    key_b = pk.partition_key_for_task(task_b, pk.NOTIFICATIONS_TOPIC, dict(_RECURSO_NOTIFICATION))

    assert key_a == key_b == "amh|GUIA-1|GLOSA-9"


def test_a_contas_and_a_recurso_notification_about_one_guia_co_partition() -> None:
    """Cross-family co-partitioning on the SHARED topic — the property the notifications channel
    needs, because both families' events about one guia/glosa arrive at the same bridge."""
    contas = {
        "type": "contas.glosa_confirmada",
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-1",
        "glosa_id": "GLOSA-9",
    }

    assert pk.derive_partition_key(pk.NOTIFICATIONS_TOPIC, contas) == pk.derive_partition_key(
        pk.NOTIFICATIONS_TOPIC, _RECURSO_NOTIFICATION
    )


def test_anssubmit_notification_anchors_on_report_type_and_competencia() -> None:
    """`workers/ans_submit.py:809,1125` — both notification dicts carry the full ANSSUB group."""
    notification = {
        "type": "anssubmit.notify_regulatorio",
        "tenant_id": "amh",
        "report_type": "DIOPS",
        "competencia": "2026-08",
    }
    assert pk.derive_partition_key(pk.NOTIFICATIONS_TOPIC, notification) == "amh|DIOPS|2026-08"


@pytest.mark.parametrize("instance", ["timer-a", "timer-b"])
def test_ans_cron_fact_uses_calendar_anchors_across_timer_instances(instance: str) -> None:
    # SP-OP-ANS-CRON-001 publishes ans.cron_due, not anssubmit.*. Calendar facts
    # must use the already contracted tenant/report/period identity without a case key.
    payload = {
        "type": "ans.cron_due",
        "tenant_id": "amh",
        "report_type": "DIOPS",
        "competencia": "2026-08",
        "_business_key": "",
        "_process_instance_id": instance,
    }
    assert pk.derive_partition_key(pk.NOTIFICATIONS_TOPIC, payload) == "amh|DIOPS|2026-08"
    for field in ("tenant_id", "report_type", "competencia"):
        assert (
            pk.derive_partition_key(pk.NOTIFICATIONS_TOPIC, dict(payload, **{field: ""}))
            != "amh|DIOPS|2026-08"
        )
    assert (
        pk.derive_partition_key(pk.NOTIFICATIONS_TOPIC, dict(payload, type="ans.unknown"))
        == f"amh|{instance}"
    )
    assert pk.derive_partition_key(_AUTH_TOPIC, payload) == f"amh|{instance}"


def test_a_family_with_no_anchor_group_still_falls_to_the_process_instance() -> None:
    """`lgpd`/`escalation`/`programa`/`adequacao` have no verified per-entity business-key
    derivation in this repo to copy, so they are deliberately absent from the table and order
    per-INSTANCE. Stated as a test so the limitation is a fact, not a footnote."""
    notification = {
        "type": "lgpd.send_response",
        "tenant_id": "amh",
        "_process_instance_id": "pi-77",
    }
    assert pk.derive_partition_key(pk.NOTIFICATIONS_TOPIC, notification) == "amh|pi-77"


def test_notifications_anchors_never_outrank_the_business_key() -> None:
    """Arm (2) still wins on the shared topic — the family resolution changes arm (3) only."""
    payload = dict(_RECURSO_NOTIFICATION) | {"_business_key": "RECURSO-amh-GUIA-1-GLOSA-9"}
    assert pk.derive_partition_key(pk.NOTIFICATIONS_TOPIC, payload) == "RECURSO-amh-GUIA-1-GLOSA-9"


def test_an_incomplete_anchor_group_on_the_shared_topic_falls_through() -> None:
    """One group per family, never a chain: a `recurso` notification missing `numero_guia_tiss`
    (`workers/recurso.py:914,977` build exactly that shape) falls to arm (4) rather than minting a
    partial key that would split one entity across two partitions."""
    task = _FakeTask(business_key="", process_instance_id="pi-8", variables={"tenant_id": "amh"})
    partial = {"type": "recurso.submit_appeal", "tenant_id": "amh", "glosa_id": "GLOSA-9"}
    assert pk.partition_key_for_task(task, pk.NOTIFICATIONS_TOPIC, partial) == "amh|pi-8"
