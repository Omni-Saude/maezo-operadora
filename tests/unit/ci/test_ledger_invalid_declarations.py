"""Synthetic Git/data adversaries; no fixture is authenticated execution evidence.

Every internally consistent fixture remains UNRESOLVED at the operational seam.
Original v1 test bodies are left unchanged and run alongside these controls.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from scripts.ci import check_evidence_ledger_hashes as v1
from scripts.ci import ledger_invalid_declarations as invalid

TEST = "tests/unit/test_claim.py"
HEADER = "| Task ID | Date | Author | Verifier | Commit | Evidence | Test hash | Status |\n"
OLD_TEST = b"def test_old():\n    assert True\n"
LOCK = b"synthetic-lock\n"
OLD_OUTPUT = f"{TEST}::test_old PASSED [100%]\n".encode()
CURRENT_HASH = "c" * 64


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def row(task: str, hashed: str, path: str = TEST, evidence: str = "synthetic") -> str:
    return f"| {task} | 2026-09-08 | A | V | source | {evidence} | sha256:{hashed} ({path}) | unverified |"


def marker(hashed: str) -> str:
    return (
        f"[ledger-supersedes:v2;kind=invalid-declaration;record=docs/evidence-corrections/{hashed}.json;"
        f"record_sha256={hashed}]"
    )


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@dataclass
class Fixture:
    root: Path
    source: str
    target: str
    record: dict[str, Any]
    correction: str = ""

    def write(self, path: str, data: bytes) -> None:
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    def publish(
        self,
        *,
        path: str = TEST,
        task: str = "CORRECTION",
        extra: str = "",
        target: str | None = None,
        template_suffix: str = "",
    ) -> None:
        correction = row(task, CURRENT_HASH, path, marker("0" * 64)) + template_suffix
        self.record["current"]["row_template_sha256"] = v1.invalid_declaration_template_sha256(correction)
        data = encoded(self.record)
        hashed = invalid.digest(data)
        directory = self.root / "docs/evidence-corrections"
        if directory.exists():
            for previous in directory.glob("*.json"):
                previous.unlink()
        self.write(f"docs/evidence-corrections/{hashed}.json", data)
        self.correction = row(task, CURRENT_HASH, path, marker(hashed)) + template_suffix
        self.write(
            "docs/evidence-ledger.md",
            (HEADER + (target or self.target) + "\n" + self.correction + "\n" + extra).encode(),
        )
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "synthetic correction", "--allow-empty")

    def plan(self) -> invalid.Plan:
        ledger = (self.root / "docs/evidence-ledger.md").read_text()
        ordinary = v1.build_supersession_plan(self.root, ledger)
        return invalid.build_plan(self.root, ledger, ordinary)


@pytest.fixture
def history(tmp_path: Path) -> Fixture:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Synthetic ledger tests")
    git(root, "config", "user.email", "synthetic@example.invalid")
    target = row("OLD", invalid.digest(OLD_TEST))
    fixture = Fixture(root, "", target, {})
    fixture.write(TEST, OLD_TEST)
    fixture.write("src/maezo/__init__.py", b"# Synthetic isolated archive package.\n")
    fixture.write("uv.lock", LOCK)
    old_ledger = (HEADER + target + "\n").encode()
    fixture.write("docs/evidence-ledger.md", old_ledger)
    git(root, "add", ".")
    git(root, "commit", "-qm", "synthetic history")
    fixture.source = git(root, "rev-parse", "HEAD")
    target_sha = invalid.digest(target.encode())
    prefix = f"docs/evidence-corrections/evidence/{target_sha}/"

    def asset(name: str, data: bytes) -> dict[str, str]:
        path = prefix + name
        fixture.write(path, data)
        return {"path": path, "sha256": invalid.digest(data)}

    record = {
        "schema": "maezo-ledger-invalid-declaration/v1",
        "reason": "declared-test-source-sha256",
        "target": {
            "task_id": "OLD",
            "row_sha256": target_sha,
            "declared_recipe_sha256": invalid.digest(OLD_TEST),
            "source_commit": fixture.source,
            "ledger_path": "docs/evidence-ledger.md",
            "ledger_sha256": invalid.digest(old_ledger),
            "test_path": TEST,
            "test_sha256": invalid.digest(OLD_TEST),
            "lock_sha256": invalid.digest(LOCK),
        },
        "historical_evidence": {
            "stdout": asset("stdout.txt", OLD_OUTPUT),
            "stderr": asset("stderr.txt", b""),
            "receipt": asset("receipt.json", b'{"synthetic_untrusted_receipt":true}'),
            "originals": [
                {
                    "role": "report",
                    "origin": "/documentary/not/opened/report",
                    "artifact": asset("report.txt", b"SYNTHETIC: no execution proof"),
                },
                {
                    "role": "manifest",
                    "origin": "https://documentary.invalid/no-fetch",
                    "artifact": asset("manifest.json", b"{}"),
                },
            ],
            "result_count": 1,
            "passed": 1,
            "failed": 0,
            "recipe_version": "fixed",
            "recipe_sha256": v1.compute_recipe_hash(v1.extract_result_lines(OLD_OUTPUT.decode()))[7:],
        },
        "current": {
            "task_id": "CORRECTION",
            "date": "2026-09-08",
            "path": TEST,
            "declared_recipe_sha256": CURRENT_HASH,
            "result_count": 2,
            "row_template_sha256": "0" * 64,
            "prior_correction": [],
        },
    }
    fixture.record = record
    fixture.write(TEST, b"def test_current():\n    assert True\n")
    fixture.publish()
    return fixture


def test_same_file_static_control_never_authenticates_receipt(
    history: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = history.plan()
    assert len(plan.relations) == 1
    result = invalid.selected_unresolved(history.root, plan, [plan.relations[0].correction], history.source)
    assert len(result) == 1
    assert result[0].historical_claim_verified is False
    assert result[0].status == "UNRESOLVED"
    assert result[0].reason == "IC_REPLAY_ADAPTER_UNREVIEWED"
    assert not isinstance(result[0], v1.RowVerification)
    assert v1.main(["--base", history.source], repo_root=history.root) == 1
    output = capsys.readouterr().out
    assert "0 historical verified" in output and "0 executions" in output
    assert "ACCEPTED_WITH_INVALID_HISTORY" not in output
    assert "OK historical" not in output


@pytest.mark.parametrize(
    "transform",
    [
        lambda text: text.replace("v2;", "v3;"),
        lambda text: text.replace("invalid-declaration", "waiver"),
        lambda text: text.replace("record=", "extra=yes;record="),
        lambda text: text.replace(";record_sha256=", "; record_sha256="),
        lambda text: text + text,
        lambda text: text.replace("record=", "record=" + text),
        lambda text: text.replace("v2;", "v2 ;"),
        lambda text: text.replace(".json;", ".json?x;"),
        lambda text: text.replace("/" + "a" * 64, "/" + "b" * 64),
    ],
)
def test_closed_marker_rejections(transform: Any) -> None:
    with pytest.raises(v1.SupersessionError):
        v1.select_rows([row("C", CURRENT_HASH, evidence=transform(marker("a" * 64)))])


@pytest.mark.parametrize(
    "line",
    [
        marker("a" * 64),
        "prose " + marker("a" * 64),
        row("C", CURRENT_HASH, evidence=marker("a" * 64)).replace("2026-09-08", "bad"),
        row("C", CURRENT_HASH, evidence=marker("a" * 64)).replace("2026-09-08", "2026-08-01"),
        row("C", CURRENT_HASH, evidence=marker("a" * 64)).replace("2026-09-08", "2026-99-99"),
        row("C", CURRENT_HASH, evidence=marker("a" * 64)).replace("sha256:", "sha512:"),
        row("C", CURRENT_HASH, evidence=marker("a" * 64)).replace(" | A |", " | extra | A |"),
        row("C", CURRENT_HASH, evidence=marker("a" * 64))[:-1],
    ],
)
def test_marker_cannot_downgrade_to_legacy_or_orphan(line: str) -> None:
    with pytest.raises(v1.SupersessionError):
        v1.select_rows([line])


def test_exact_frozen_v1_parser_refuses_v2(history: Fixture) -> None:
    repo = Path(__file__).resolve().parents[3]
    frozen = subprocess.run(
        [
            "git",
            "show",
            "7189bcb0b3532a48401bf86f376cf37e876adabf:scripts/ci/check_evidence_ledger_hashes.py",
        ],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    namespace: dict[str, Any] = {"__name__": "frozen_v1_probe", "__file__": str(repo / "scripts/ci/x.py")}
    import types

    module = types.ModuleType("frozen_v1_probe")
    module.__dict__.update(namespace)
    sys.modules[module.__name__] = module
    try:
        exec(compile(frozen, "frozen-v1", "exec"), module.__dict__)
        with pytest.raises(module.SupersessionError):
            module.select_rows([row("C", CURRENT_HASH, evidence=marker("a" * 64))])
        target = module.parse_row_line(history.target)
        correction = module.parse_row_line(row("CURRENT", CURRENT_HASH))
        claim = module.SupersessionClaim(
            "OLD",
            history.source,
            invalid.digest(history.target.encode()),
            invalid.digest(OLD_TEST),
            invalid.digest(LOCK),
        )
        result = module.verify_historical_row(
            history.root, module.SupersessionEdge(correction, target, claim), sys.executable
        )
        assert result.ok is False
        assert "historical hash mismatch" in result.message
        assert "refusing any HEAD fallback" in result.message
    finally:
        sys.modules.pop(module.__name__)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"a":1,"a":2}',
        b'{"a":1.0}',
        b'{"a":NaN}',
        b"\xff",
        b"[]",
        b'{"a":' + b"[" * 20 + b"0" + b"]" * 20 + b"}",
        b'{"a":"' + b"a" * 4097 + b'"}',
        b'{"a":[' + b"0," * 4097 + b"0]}",
        b" " * (invalid.MAX_JSON_BYTES + 1),
    ],
)
def test_bounded_closed_json(payload: bytes) -> None:
    with pytest.raises(v1.SupersessionError):
        invalid.bounded_json(payload)


@pytest.mark.parametrize(
    "group,key,value",
    [
        ("", "schema", "v2"),
        ("", "reason", "arbitrary-typo"),
        ("", "waiver", True),
        ("target", "source_commit", "HEAD"),
        ("target", "source_commit", "a" * 39),
        ("target", "source_commit", "A" * 40),
        ("target", "ledger_path", "other.md"),
        ("target", "test_path", "tests/../outside.py"),
        ("historical_evidence", "result_count", True),
        ("historical_evidence", "failed", 1),
        ("historical_evidence", "passed", 0),
        ("historical_evidence", "recipe_version", "legacy"),
        ("historical_evidence", "result_count", 0),
        ("historical_evidence", "result_count", 100001),
        ("current", "result_count", False),
        ("current", "prior_correction", None),
        ("current", "path", "tests/%2fsecret.py"),
        ("current", "path", "tests/./test_claim.py"),
    ],
)
def test_record_schema_rejects_unsupported_or_ambiguous_data(
    history: Fixture, group: str, key: str, value: Any
) -> None:
    data = copy.deepcopy(history.record)
    (data[group] if group else data)[key] = value
    with pytest.raises(v1.SupersessionError):
        invalid.parse_record(encoded(data))


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../outside",
        "tests/../x.py",
        "tests//x.py",
        "tests/./x.py",
        "tests/%2foutside.py",
        "-option",
        "tests/x\\y.py",
        "tests/x\x00.py",
        "tests/x\n.py",
    ],
)
def test_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(v1.SupersessionError):
        invalid.safe_path(path)


def test_icr1_unrelated_successful_current_file_rejected_before_receipt(history: Fixture) -> None:
    unrelated = "tests/unit/test_unrelated.py"
    history.write(unrelated, b"def test_easy():\n    assert True\n")
    history.record["current"]["path"] = unrelated
    history.publish(path=unrelated)
    # Make evidence inaccessible too: ordered path refusal must win without touching it.
    (history.root / history.record["historical_evidence"]["stdout"]["path"]).unlink()
    git(history.root, "add", ".")
    git(history.root, "commit", "-qm", "remove evidence")
    with pytest.raises(v1.SupersessionError, match="IC_R1_CURRENT_PATH_MISMATCH"):
        history.plan()


def test_icr1_target_record_path_checked_first(history: Fixture) -> None:
    history.record["target"]["test_path"] = "tests/unit/test_other.py"
    history.record["current"]["path"] = "tests/unit/test_another.py"
    history.publish()
    with pytest.raises(v1.SupersessionError, match="IC_R1_TARGET_PATH_BINDING"):
        history.plan()


@pytest.mark.parametrize("replacement", ["missing", "symlink", "submodule"])
def test_icr1_candidate_must_be_regular_same_file(history: Fixture, replacement: str) -> None:
    path = history.root / TEST
    path.unlink()
    if replacement == "symlink":
        path.symlink_to("/etc/passwd")
    history.publish()
    if replacement == "submodule":
        git(history.root, "update-index", "--add", "--cacheinfo", "160000", history.source, TEST)
        git(history.root, "commit", "-qm", "synthetic submodule")
    with pytest.raises(v1.SupersessionError, match="IC_R1_TARGET_PATH_UNAVAILABLE"):
        history.plan()


def test_icr1_cited_prior_row_scope_checked(history: Fixture) -> None:
    other = row("PRIOR", "b" * 64, "tests/unit/test_other.py")
    prior = copy.deepcopy(history.record["target"])
    prior.update(
        task_id="PRIOR", row_sha256=invalid.digest(other.encode()), test_path="tests/unit/test_other.py"
    )
    history.record["current"]["prior_correction"] = [prior]
    history.publish(extra=other + "\n")
    with pytest.raises(v1.SupersessionError, match="IC_R1_CITED_ROW_PATH_MISMATCH"):
        history.plan()


@pytest.mark.parametrize("key", ["ledger_sha256", "test_sha256", "lock_sha256", "declared_recipe_sha256"])
def test_source_metadata_cannot_override_git(history: Fixture, key: str) -> None:
    history.record["target"][key] = "f" * 64
    history.publish()
    with pytest.raises(v1.SupersessionError):
        history.plan()


def test_source_identity_requires_exact_current_occurrence(history: Fixture) -> None:
    history.publish(target=history.target.replace(" | A |", " | A  |"))
    with pytest.raises(v1.SupersessionError, match="IC_ROW_IDENTITY"):
        history.plan()


def test_linked_duplicate_is_ambiguous_unrelated_duplicate_id_is_allowed(history: Fixture) -> None:
    history.publish(extra=row("OLD", "b" * 64) + "\n")
    assert len(history.plan().relations) == 1
    history.publish(extra=history.target + "\n")
    with pytest.raises(v1.SupersessionError, match="IC_ROW_IDENTITY"):
        history.plan()


def test_record_reuse_and_duplicate_correction_rejected(history: Fixture) -> None:
    history.publish(extra=history.correction + "\n")
    with pytest.raises(v1.SupersessionError):
        history.plan()


@pytest.mark.parametrize(
    "links,old_links",
    [
        ([("a", "a")], []),
        ([("a", "b"), ("b", "a")], []),
        ([("a", "b"), ("b", "c"), ("c", "a")], []),
        ([("a", "b"), ("c", "b")], []),
        ([("a", "b"), ("a", "c")], []),
        ([("a", "b")], [("b", "c")]),
        ([("a", "b")], [("c", "a")]),
    ],
)
def test_graph_rejects_chains_cycles_and_collisions(links: Any, old_links: Any) -> None:
    with pytest.raises(v1.SupersessionError, match="IC_GRAPH_COLLISION"):
        invalid.assert_disjoint(links, old_links)


def test_disjoint_v1_chain_allowed() -> None:
    invalid.assert_disjoint([("a", "b")], [("c", "d"), ("d", "e")])


@pytest.mark.parametrize("source", ["tree", "missing", "nonancestor"])
def test_commit_identity_closed(history: Fixture, source: str) -> None:
    if source == "tree":
        value = git(history.root, "rev-parse", "HEAD^{tree}")
    elif source == "missing":
        value = "f" * 40
    else:
        value = git(history.root, "commit-tree", "HEAD^{tree}", "-m", "unrelated")
    history.record["target"]["source_commit"] = value
    history.publish()
    with pytest.raises(v1.SupersessionError):
        history.plan()


@pytest.mark.parametrize("kind", ["replacement", "graft", "shallow"])
def test_git_indirection_refused(history: Fixture, kind: str) -> None:
    if kind == "replacement":
        git(history.root, "replace", history.source, "HEAD")
    elif kind == "graft":
        (history.root / ".git/info/grafts").write_text(history.source + "\n")
    else:
        (history.root / ".git/shallow").write_text(history.source + "\n")
    with pytest.raises(v1.SupersessionError):
        history.plan()


@pytest.mark.parametrize("mode", ["alter", "remove", "untracked", "symlink", "hardlink"])
def test_evidence_requires_committed_regular_exact_bytes(history: Fixture, mode: str) -> None:
    asset = history.record["historical_evidence"]["stdout"]
    path = history.root / asset["path"]
    if mode == "alter":
        path.write_bytes(b"different\n")
    elif mode == "untracked":
        git(history.root, "rm", "--cached", asset["path"])
    else:
        path.unlink()
        if mode == "symlink":
            path.symlink_to("/etc/passwd")
        elif mode == "hardlink":
            path.hardlink_to(history.root / TEST)
    if mode != "untracked":
        git(history.root, "add", ".")
    git(history.root, "commit", "-qm", "tamper", "--allow-empty")
    with pytest.raises(v1.SupersessionError):
        history.plan()


def test_remove_marker_leaves_orphan_nonpassing(history: Fixture) -> None:
    ledger = history.root / "docs/evidence-ledger.md"
    ledger.write_text(HEADER + history.target + "\n")
    git(history.root, "add", ".")
    git(history.root, "commit", "-qm", "remove correction")
    with pytest.raises(v1.SupersessionError, match="IC_ORPHAN_EVIDENCE"):
        history.plan()


def test_current_nonmarker_byte_change_is_bound(history: Fixture) -> None:
    ledger = history.root / "docs/evidence-ledger.md"
    ledger.write_text(
        ledger.read_text().replace(history.correction, history.correction.replace("unverified", "verified"))
    )
    git(history.root, "add", ".")
    git(history.root, "commit", "-qm", "tamper correction prose")
    with pytest.raises(v1.SupersessionError, match="IC_CURRENT_BINDING"):
        history.plan()


@pytest.mark.parametrize("selected_endpoint", ["target", "correction"])
def test_either_selected_endpoint_closes_relation(history: Fixture, selected_endpoint: str) -> None:
    plan = history.plan()
    results = invalid.selected_unresolved(
        history.root, plan, [getattr(plan.relations[0], selected_endpoint)], None
    )
    assert len(results) == 1 and results[0].historical_claim_verified is False


@pytest.mark.parametrize("path_kind", ["receipt", "source", "record"])
def test_nonledger_diff_closes_relation(history: Fixture, path_kind: str) -> None:
    base = git(history.root, "rev-parse", "HEAD")
    if path_kind == "receipt":
        asset = history.record["historical_evidence"]["receipt"]
        data = b'{"still_untrusted":true}'
        history.write(asset["path"], data)
        asset["sha256"] = invalid.digest(data)
    elif path_kind == "source":
        history.write("src/example.py", b"VALUE = 1\n")
    else:
        history.record["current"]["result_count"] = 3
    history.publish()
    plan = history.plan()
    assert len(invalid.selected_unresolved(history.root, plan, [], base)) == 1


def test_unrelated_no_selection_control(history: Fixture) -> None:
    base = git(history.root, "rev-parse", "HEAD")
    history.write("notes.md", b"unrelated")
    git(history.root, "add", ".")
    git(history.root, "commit", "-qm", "unrelated")
    plan = history.plan()
    assert invalid.selected_unresolved(history.root, plan, [], base) == ()


def test_lock_mismatch_remains_unresolved(history: Fixture) -> None:
    history.write("uv.lock", b"different lock")
    history.publish()
    plan = history.plan()
    results = invalid.selected_unresolved(history.root, plan, [plan.relations[0].correction], None)
    assert results[0].reason == "IC_LOCK_MISMATCH"


def test_drift_between_plan_and_selection_refused(history: Fixture) -> None:
    plan = history.plan()
    history.write(TEST, b"changed after plan")
    with pytest.raises(v1.SupersessionError, match="IC_CANDIDATE_DIRTY"):
        invalid.selected_unresolved(history.root, plan, [plan.relations[0].correction], None)


@pytest.mark.parametrize(
    "output",
    [
        b"",
        OLD_OUTPUT + OLD_OUTPUT,
        OLD_OUTPUT.replace(b"PASSED", b"FAILED"),
        OLD_OUTPUT.replace(b"PASSED", b"SKIPPED"),
        OLD_OUTPUT.replace(b"PASSED", b"XFAILED"),
        OLD_OUTPUT + b"ERROR at teardown\n",
        OLD_OUTPUT + b"INTERRUPTED\n",
        OLD_OUTPUT.replace(TEST.encode(), b"tests/unit/test_other.py"),
    ],
)
def test_raw_inputs_refuse_incomplete_or_wrong_scope(output: bytes) -> None:
    expected = v1.compute_recipe_hash(v1.extract_result_lines(OLD_OUTPUT.decode()))[7:]
    with pytest.raises(v1.SupersessionError):
        invalid.validate_raw_streams(output, b"", TEST, expected, 1)


def test_raw_positive_control_does_not_create_a_verified_result() -> None:
    expected = v1.compute_recipe_hash(v1.extract_result_lines(OLD_OUTPUT.decode()))[7:]
    invalid.validate_raw_streams(OLD_OUTPUT, b"", TEST, expected, 1)


def test_exact_test_bodies_and_ledger_history_unchanged() -> None:
    root = Path(__file__).resolve().parents[3]
    for path in (
        "docs/evidence-ledger.md",
        "tests/unit/ci/test_check_evidence_ledger_hashes.py",
        "tests/unit/ci/test_check_evidence_ledger_supersession.py",
    ):
        original = subprocess.run(
            ["git", "show", "7189bcb0b3532a48401bf86f376cf37e876adabf:" + path],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        current = (root / path).read_bytes()
        if path == "docs/evidence-ledger.md":
            # Qualified union inserts one independently pinned R009 chunk between
            # the common prefix C and the exact ordered original suffix S.
            assert (
                invalid.digest(original) == "82e07720cf16a1c42fa54a9d89363f08466174c2a4ecb578f76287a14126ac8b"
            )

            def frozen(commit: str, relative: str = path) -> bytes:
                return subprocess.run(
                    ["git", "show", commit + ":" + relative], cwd=root, capture_output=True, check=True
                ).stdout

            common = frozen("f6346447233c7b640dd40d5a248c83c0372e4177")
            r009 = frozen("6f79ae51fd5390dc07df4cf094b49d3a49f25c17")
            assert len(common) == 1631512
            assert (
                invalid.digest(common) == "d96ee8f5fa97a5c13a06359895671e1434fda97df133ba06bed8b79f8245c3b0"
            )
            assert invalid.digest(r009) == "bdbd9ef7ae19150ee0e01a487f9aeae6a5d7589b1a4efd027420ec32bf6b2272"
            assert original.startswith(common) and r009.startswith(common)
            original_suffix = original[len(common) :]
            inserted = r009[len(common) :]
            assert len(original_suffix) == 51941 and len(original_suffix.splitlines()) == 27
            assert len(inserted) == 6793
            qualified = subprocess.run(
                ["git", "show", "5b70a6e9890c71a2f6a6851c89531eb8c89fd4c1:" + path],
                cwd=root,
                capture_output=True,
                check=True,
            ).stdout
            assert (
                invalid.digest(qualified)
                == "11055f392b6a622a4a5623e7af8c00f6478645bd1f503f42b8ac6a73a0e23218"
            )
            assert qualified.startswith(common + inserted + original_suffix)
            assert qualified.count(original_suffix) == 1
            assert len(qualified[len(common + inserted + original_suffix) :]) == 2652
            assert current.startswith(qualified)
        else:
            assert current == original
