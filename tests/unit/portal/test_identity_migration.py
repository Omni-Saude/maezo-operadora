"""Head fence propagated intact from 0010; no database simulation or relaxed merge graph."""

import re
from pathlib import Path

_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "src/maezo/platform/migrations/versions"


def test_0014_is_the_unique_head_of_a_linear_chain() -> None:
    """No fork: every revision claimed once, exactly one revision unreferenced as a parent.

    A forked chain is the failure mode where `alembic upgrade head` applies one branch while an
    operator believes it applied the other. Propagated from 0010 to the real 0014 head;
    integration with the concurrent 0011 must retain one connected linear chain.
    """
    revisions: dict[str, str | None] = {}
    for path in sorted(_VERSIONS_DIR.glob("[0-9]*.py")):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision: str = "([^"]+)"', text, re.MULTILINE)
        down = re.search(r'^down_revision: str \| None = (?:"([^"]+)"|None)', text, re.MULTILINE)
        assert rev is not None, f"{path.name} declares no revision"
        assert down is not None, f"{path.name} declares no down_revision"
        assert rev.group(1) not in revisions, f"duplicate revision id {rev.group(1)}"
        revisions[rev.group(1)] = down.group(1)

    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {"0014"}, f"expected 0014 to be the sole head, got {heads}"
    assert len(parents) == len(revisions) - 1, "a revision is claimed as parent by two children"

    assert parents <= set(revisions), "every predecessor must actually exist"
    visited = set()
    current = "0014"
    while current is not None:
        assert current not in visited, "migration cycle"
        visited.add(current)
        current = revisions[current]
    assert visited == set(revisions), "disconnected migration chain"
