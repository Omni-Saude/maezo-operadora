"""Fences for the two SME-session dossiers prepared under R-018/R-019 (payer vocabulary) and
R-163 (AUTH criteria ratification).

Both dossiers are PREPARATION artefacts: they carry EMPTY ratification fields and are explicitly
not mergeable into `spec/` as a decision. What makes them useful to a human reviewer is that every
claim points at a real artefact — so what rots silently is exactly what these fences catch:

  1. `PAYER-VOCAB-CORRESPONDENCE.md` / `payer-vocab-correspondence.yaml` — every row's
     `arquivo:linha` must resolve AND the resolved line must still contain the declared anchor
     token. A renamed element or a shifted line turns the table red instead of leaving the SME
     reading a citation that no longer says what the table claims.
  2. `AUTH-CRITERIA-RATIFICATION-DOSSIER.md` / `auth-criteria-ratification-dossier.yaml` — every
     table's binding `sha256` is RE-DERIVED here from the bytes on disk with the repo's own
     binding-digest function (`tests.support.dmn_first_hit.live_table_digest`, the one that
     computes `tabela_viva.sha256` for the shadow-candidate manifests). Editing a criteria table
     after the dossier was written breaks the fence, so a human ratification can never be bound to
     a text that has since changed.

Neither dossier may fill `ratificado`/`revisor`/`ratificado_em` — here or in the CODEOWNED
manifest `spec/processes/dmn/auth-criteria-ratification.yaml`, which the last test pins as
untouched (nothing ratified).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests.support.dmn_first_hit import live_table_digest

_REPO_ROOT = Path(__file__).parents[3]
_SME_DIR = _REPO_ROOT / "docs" / "sme-dispatch"

_VOCAB_YAML = _SME_DIR / "regulatorio" / "payer-vocab-correspondence.yaml"
_VOCAB_MD = _SME_DIR / "regulatorio" / "PAYER-VOCAB-CORRESPONDENCE.md"
_AUTH_YAML = _SME_DIR / "medico-auditor" / "auth-criteria-ratification-dossier.yaml"
_AUTH_MD = _SME_DIR / "medico-auditor" / "AUTH-CRITERIA-RATIFICATION-DOSSIER.md"
_MANIFEST = _REPO_ROOT / "spec" / "processes" / "dmn" / "auth-criteria-ratification.yaml"

_RATIFICATION_FIELDS = ("ratificado", "revisor", "ratificado_em")


def _load(path: Path) -> dict:
    assert path.is_file(), f"missing dossier: {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path}: root must be a mapping"
    return data


def _resolved_line(rel_path: str, line_no: int) -> str:
    target = _REPO_ROOT / rel_path
    assert target.is_file(), f"cited artefact does not exist: {rel_path}"
    lines = target.read_text(encoding="utf-8").splitlines()
    assert 1 <= line_no <= len(lines), f"{rel_path}:{line_no} is out of range (file has {len(lines)} lines)"
    return lines[line_no - 1]


# --------------------------------------------------------------------------------------------
# R-018 / R-019 — payer-vocabulary correspondence table
# --------------------------------------------------------------------------------------------


def test_vocab_table_has_the_twelve_contas_and_twentysix_recurso_rows() -> None:
    """Sanity: the fences below are not scanning a truncated table. The lexical inventory of
    `PERSPECTIVE-MATRIX.md` §5.1 is 12 CONTAS elements and ~25 RECURSO elements; the explicit
    enumeration closes at 26 for RECURSO (grouping difference, documented in the dossier §2)."""
    rows = _load(_VOCAB_YAML)["linhas"]
    contas = [r for r in rows if r["cadeia"] == "CONTAS"]
    recurso = [r for r in rows if r["cadeia"] == "RECURSO"]
    assert len(contas) == 12, [r["id"] for r in contas]
    assert len(recurso) == 26, [r["id"] for r in recurso]
    assert len({r["id"] for r in rows}) == len(rows), "duplicate row id"


def test_every_vocab_row_citation_resolves_and_still_carries_its_anchor() -> None:
    """THE FENCE. `arquivo:linha` must exist and the resolved line must still contain the anchor
    token the row claims is there — a moved or renamed element turns the table red."""
    broken: dict[str, str] = {}
    for row in _load(_VOCAB_YAML)["linhas"]:
        line = _resolved_line(row["arquivo"], row["linha"])
        if row["ancora"] not in line:
            broken[row["id"]] = (
                f"{row['arquivo']}:{row['linha']} no longer contains {row['ancora']!r} "
                f"(line is {line.strip()[:120]!r})"
            )
    assert not broken, broken


def test_every_vocab_row_is_marked_proposto_and_the_prohibition_is_stated() -> None:
    """R-018's condition of authorization: EVERY row is marked `PROPOSTO`, and the document states
    in so many words that no such row is mergeable into `spec/` as the decision."""
    data = _load(_VOCAB_YAML)
    assert {row["status"] for row in data["linhas"]} == {"PROPOSTO"}
    md = _VOCAB_MD.read_text(encoding="utf-8")
    assert "NENHUMA LINHA `PROPOSTO` E MERGEAVEL EM `spec/` COMO DECISAO." in md
    assert "MERGEAVEL EM `spec/`" in "\n".join(
        line for line in _VOCAB_YAML.read_text(encoding="utf-8").splitlines() if line.startswith("#")
    )


def test_vocab_ratification_fields_are_empty() -> None:
    """Preparation, never ratification: the three fields exist and are all empty."""
    ratificacao = _load(_VOCAB_YAML)["ratificacao"]
    assert set(ratificacao) == set(_RATIFICATION_FIELDS)
    assert all(ratificacao[field] is None for field in _RATIFICATION_FIELDS), ratificacao


def test_vocab_md_and_yaml_cite_the_same_lines() -> None:
    """The human-readable table and its machine twin cannot drift apart."""
    md = _VOCAB_MD.read_text(encoding="utf-8")
    for row in _load(_VOCAB_YAML)["linhas"]:
        citation = f"`{row['arquivo']}:{row['linha']}`"
        assert citation in md, f"{row['id']}: {citation} missing from {_VOCAB_MD.name}"


def test_the_proposto_labels_the_session_removes_still_exist_where_declared() -> None:
    """R-019's `agent_prep_task` asks for the EXACT list of `PROPOSTO` labels the session removes.
    The approved row cites `SP-OP-CONTAS-001.md:166` and `SP-OP-RECURSO-001.md:183,:189,:191`;
    the contracts were rewritten since, so the dossier locates them BY CONTENT. This pins the
    located lines."""
    labels = _load(_VOCAB_YAML)["rotulos_a_remover_na_sessao"]
    assert len(labels) == 4, labels
    md = _VOCAB_MD.read_text(encoding="utf-8")
    for label in labels:
        line = _resolved_line(label["arquivo"], label["linha"])
        assert label["ancora"] in line, (
            f"{label['arquivo']}:{label['linha']} no longer carries {label['ancora']!r}"
        )
        assert "PROPOSTO" in line.upper(), f"{label['arquivo']}:{label['linha']} is not a PROPOSTO label"
        assert f"`{label['arquivo']}:{label['linha']}`" in md


# --------------------------------------------------------------------------------------------
# R-163 — AUTH criteria ratification dossier
# --------------------------------------------------------------------------------------------


def test_auth_dossier_covers_every_source_declared_by_the_manifest() -> None:
    """The dossier must not silently cover a subset. The approved text says "4 tables", the
    original question says "5", `docs/review-queue.md` says "5 + populate the contractual one" —
    the manifest on disk declares 6 `fontes`, and the dossier covers all of them (the count
    divergence is itself an agenda item, dossier §0)."""
    manifest = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    declared = set(manifest["fontes"])
    covered = {table["id"] for table in _load(_AUTH_YAML)["tabelas"]}
    assert covered == declared, {"nao_cobertas": declared - covered, "extra": covered - declared}


def test_every_auth_binding_sha256_equals_the_digest_rederived_from_disk() -> None:
    """THE BINDING. Each table's declared digest must equal the sha256 of that file's bytes,
    re-derived here with the repo's own binding-digest function — the same one that computes
    `tabela_viva.sha256` for the shadow-candidate manifests. A criteria table edited after the
    dossier was written breaks this, so a ratification can never be bound to a stale text."""
    data = _load(_AUTH_YAML)
    for table in data["tabelas"]:
        path = _REPO_ROOT / table["arquivo"]
        assert path.is_file(), table["arquivo"]
        assert table["binding_sha256"] == live_table_digest(path), (
            f"{table['id']}: declared binding sha256 no longer describes {table['arquivo']}"
        )
    assert data["manifesto"]["sha256"] == live_table_digest(_MANIFEST)


def test_the_auth_bindings_are_seven_distinct_digests() -> None:
    """Anti-copy-paste: six tables plus the manifest must produce seven different digests. A
    repeated digest means a row was duplicated instead of computed."""
    data = _load(_AUTH_YAML)
    digests = [table["binding_sha256"] for table in data["tabelas"]]
    digests.append(data["manifesto"]["sha256"])
    assert len(set(digests)) == len(digests) == 7, digests
    assert all(re.fullmatch(r"[0-9a-f]{64}", d) for d in digests), digests


def test_auth_md_carries_the_same_digests_as_its_yaml_twin() -> None:
    """The dossier a human reads and the machine twin cannot disagree about what was signed."""
    md = _AUTH_MD.read_text(encoding="utf-8")
    data = _load(_AUTH_YAML)
    for table in data["tabelas"]:
        assert f"`{table['binding_sha256']}`" in md, table["id"]
    assert f"`{data['manifesto']['sha256']}`" in md


def test_auth_dossier_leaves_all_three_ratification_fields_empty_per_table() -> None:
    """No agent fills `ratificado`/`revisor`/`ratificado_em`. The dossier SHOWS the three fields
    per table precisely so the session can see what it is being asked to sign — all empty."""
    for table in _load(_AUTH_YAML)["tabelas"]:
        ratificacao = table["ratificacao"]
        assert set(ratificacao) == set(_RATIFICATION_FIELDS), table["id"]
        assert all(ratificacao[field] is None for field in _RATIFICATION_FIELDS), table["id"]


def test_auth_dossier_states_that_nothing_auto_approves_while_unratified() -> None:
    md = _AUTH_MD.read_text(encoding="utf-8")
    assert "NADA" in md and "auto-aprova" in md
    assert "`*_FONTE_NAO_RATIFICADA`" in md


def test_the_codeowned_manifest_is_still_completely_unratified() -> None:
    """The dossier is preparation: preparing it must not have flipped anything in the CODEOWNED
    manifest. Every source stays `ratificado: false` with `ratificado_em: null`."""
    manifest = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    for source_id, entry in manifest["fontes"].items():
        assert entry["ratificado"] is False, source_id
        assert entry["ratificado_em"] is None, source_id
