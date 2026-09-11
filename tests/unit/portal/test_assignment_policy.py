"""Closed staff policy predicates: source review is never inferred from unit fixtures."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from maezo.gateway.human.assignment_policy import AssignmentRule, RoleGroupRequirement, StaffMembership
from maezo.gateway.human.read_profile import parse_model, wire
from maezo.portal.contracts.models import MembershipBinding

NOW = datetime.now(UTC)


def member(ref="actor", *, roles=("reviewer",), groups=("team",)):
    return StaffMembership(
        principal_ref=ref,
        issuer="issuer",
        subject=ref,
        membership_revision=2**53 + 1,
        audience="staff",
        memberships=(MembershipBinding(membership_ref="membership", roles=roles, groups=groups),),
        subject_bindings=(),
        state="active",
        reviewed_until=NOW + timedelta(minutes=10),
    )


def rule(**changes):
    requirement = RoleGroupRequirement(roles=("reviewer",), groups=("team",))
    values = dict(
        operation="reassign",
        actor=requirement,
        target=requirement,
        ownership_states=("actor_assigned", "other_assigned", "unassigned"),
        target_relations=("actor", "current_assignee", "other"),
    )
    return AssignmentRule(**(values | changes))


@pytest.mark.parametrize(
    "assignee,target",
    [
        (None, "actor"),
        (None, "other"),
        ("actor", "actor"),
        ("owner", "actor"),
        ("owner", "owner"),
        ("owner", "other"),
    ],
)
def test_explicit_policy_allows_each_owned_unowned_self_current_relation(assignee, target):
    assert rule().permits(member(), member(target), assignee, ("team",), NOW)


def test_overlapping_actor_current_assignee_relations_are_conjunctive():
    assert not rule(target_relations=("actor",)).permits(member(), member(), "actor", ("team",), NOW)


def test_roles_and_groups_cannot_be_unioned_across_memberships():
    actor = member().model_copy(
        update={
            "memberships": (
                MembershipBinding(membership_ref="a", roles=("reviewer",), groups=("different",)),
                MembershipBinding(membership_ref="b", roles=("other",), groups=("team",)),
            )
        }
    )
    assert not rule().permits(actor, member("other"), None, ("team",), NOW)


def test_actual_engine_candidates_remain_a_separate_requirement():
    assert not rule().permits(member(), member("other"), None, ("different",), NOW)


@pytest.mark.parametrize("change", [{"state": "revoked"}, {"reviewed_until": NOW}])
def test_current_target_must_be_active_and_unexpired(change):
    assert not rule().permits(member(), member("other").model_copy(update=change), None, ("team",), NOW)


def test_lossless_source_membership_codec_rejects_private_numeric_token():
    value = wire(member())
    assert value["membership_revision"] == str(2**53 + 1)
    assert parse_model(StaffMembership, value) == member()
    value["membership_revision"] = 2**53 + 1
    with pytest.raises(ValueError):
        parse_model(StaffMembership, value)


def test_unreviewed_rule_shape_cannot_widen_claim():
    with pytest.raises(ValidationError):
        rule(operation="claim", target=None, target_relations=(), ownership_states=("other_assigned",))
