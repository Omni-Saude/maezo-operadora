"""C2 pure replay sanitation UNIT tests; no schema/source/provider admission."""

from copy import deepcopy

import pytest

from maezo.adapters.amh.commercial import _strip_replay_actions


@pytest.mark.parametrize("expires_at", [None, "2100-01-01T00:00:00Z", "2000-01-01T00:00:00Z"])
def test_recovery_recursively_strips_hosted_links_with_any_claimed_expiry(expires_at: str | None) -> None:
    source = {
        "data": {
            "kind": "payment",
            "payment": {
                "attempt_ref": "original-attempt",
                "hosted_action": {
                    "action_ref": "action",
                    "url": "https://provider.invalid/token",
                    "expires_at": expires_at,
                },
            },
        },
        "nested": [{"mandate": {"hosted_action": {"url": "https://other.invalid/token"}}}],
    }
    retained = deepcopy(source)
    sanitized = _strip_replay_actions(source)
    assert sanitized["data"]["payment"] == {"attempt_ref": "original-attempt", "hosted_action": None}
    assert sanitized["nested"][0]["mandate"]["hosted_action"] is None
    assert source == retained
    assert "https://" not in str(sanitized)


def test_recovery_sanitation_is_bounded_before_projection() -> None:
    source: dict[str, object] = {}
    for _ in range(34):
        source = {"nested": source}
    with pytest.raises(ValueError):
        _strip_replay_actions(source)
