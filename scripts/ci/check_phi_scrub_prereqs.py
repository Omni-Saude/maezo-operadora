#!/usr/bin/env python3
"""CI gate: `scrub_only`/`pseudo_keys` cannot be ratified while its prerequisites are unmet
(R-009, owner decision on gap D7-01 — OWNER-DECISIONS-REGISTER row R-009,
`Bold-Decision Review v2 CEO 2026-09-04`).

Purpose
-------
`spec/policies/privacy/phi-business-key-remediation.yaml` frames — but does not itself enforce —
two prerequisites of the `scrub_only` remediation mode (inherited by `pseudo_keys`, which is
`scrub_only` PLUS more): the manifest's own `pre_requisitos_scrub_only` block says so in prose,
and prose does not block a merge. The owner's ratified answer to R-009 is explicit: "converter as
duas pre-condicoes de prosa em cerca de CI no MESMO PR" — turn the two prose prerequisites into a
red CI gate before any PR can take the manifest out of `DRAFT` with an unmet one. This script is
that gate.

THE TWO TRACKED PREREQUISITES (code-frozen, see `TRACKED_PREREQUISITE_IDS` below):

  1. ``phi_hmac_key_provisionado`` — the real `PHI_HMAC_KEY` secret VALUE is provisioned (not
     merely wired) in the real overlays. Unmet today: the env-var/secret-reference wiring exists
     in `deploy/helm/maezo-tenant/templates/deployment-{agent-runtime,worker-daemon,
     webhook-receiver}.yaml` and `deploy/aws-ecs/envs/dev-sa-east-1/service-{agents,worker}.tf`,
     but `deploy/helm/maezo-tenant/templates/externalsecret.yaml:170` itself records populating
     the real key as a manual, still-open step. Without it, a ratified `scrub_only` makes every
     composition root report NOT READY at boot (`src/maezo/platform/observability.py
     ::_build_key_scrubber` raises `PseudonymizerKeyMissingError`, caught by
     `bootstrap_observability`).

  2. ``janela_drenagem_cancel_inad`` — a scheduled drain window for the CANCEL/INAD Kafka topics,
     because `scrub_only` changes the Kafka MESSAGE KEY (hence the partition) for those two
     families.

Both prerequisites are read from the manifest's own `pre_requisitos_scrub_only` block — never
recomputed from the codebase by this script — so ratifying the mode is still a DATA act (fill in
`evidencia_provisionamento` / `janela_drenagem.inicio+fim` and flip `atendido: true`), exactly the
posture `phi_key_policy.py`'s docstring already commits to for the manifest as a whole.

Fail-closed contract
---------------------
- `status: DRAFT` (or any other non-fully-ratified state — partial `ratificacao`, unknown `modo`,
  `unratified: true`, duplicate top-level key, ...) -> PASS, exit 0. Nothing is in force; there is
  nothing to gate. Delegated to `maezo.platform.privacy.phi_key_policy.load_phi_key_policy` — the
  SAME loader the runtime uses to resolve `modo` — so this gate can never disagree with the
  runtime about whether the manifest is actually ratified.
- Fully ratified with `modo: off` -> PASS, exit 0. The owner explicitly chose to stay off; no
  scrub-only prerequisite applies.
- Fully ratified with `modo: scrub_only` or `modo: pseudo_keys` -> the `pre_requisitos_scrub_only`
  block is READ and EVERY tracked item must be present, `atendido: true` (an exact Python `bool`,
  never a truthy string), AND carry non-blank required evidence:
    * `phi_hmac_key_provisionado`  requires non-blank `evidencia_provisionamento`.
    * `janela_drenagem_cancel_inad` requires non-blank `janela_drenagem.inicio` AND
      `janela_drenagem.fim`.
  Any miss -> FAIL, printing every unmet prerequisite (never just the first).
- The block is ABSENT, not a list, missing a tracked id, carries an unknown extra id, or any item
  has a malformed shape (missing key, `atendido` not a bool, `janela_drenagem` not a mapping when
  present, ...) -> FAIL. A block that cannot be read cleanly must never read as "satisfied".

This is a MERGE gate, not a signature: it never writes `status`/`ratificacao.*`, and passing it
does not ratify anything — see the manifest's own `floor_note` (R-009 dossier) and
`docs/evidence-ledger.md` row `R-009`.

Non-vacuity
-----------
`self_check()` drives `evaluate()` over synthetic manifests (in-memory dicts, never touching the
shipped file) and asserts it can FAIL every one of the four ways above AND PASS both the DRAFT and
the all-met-RATIFICADO cases, before any real measurement is trusted — same posture as
`check_deviation_expiry.py`/`generate_release_floor.py`.

Usage
-----
    python scripts/ci/check_phi_scrub_prereqs.py                       # CI gate, real manifest
    python scripts/ci/check_phi_scrub_prereqs.py --manifest PATH       # against another manifest
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path("spec/policies/privacy/phi-business-key-remediation.yaml")

_BLOCK_KEY = "pre_requisitos_scrub_only"

#: THE TRACKED SET, code-frozen — mirrors `check_deviation_expiry.py::TRACKED_DEVIATIONS`. Which
#: prerequisites this gate enforces is a governance fact the owner ratified (R-009); a manifest
#: that could silently drop one of its own required ids by editing the YAML would be a record
#: that grades its own homework. Adding/removing a tracked id is therefore a code change under
#: `scripts/ci/` review, never a data-only edit.
TRACKED_PREREQUISITE_IDS: tuple[str, ...] = (
    "phi_hmac_key_provisionado",
    "janela_drenagem_cancel_inad",
)

LEVEL_OK: str = "OK"
LEVEL_FAIL: str = "FAIL"


@dataclass(frozen=True, slots=True)
class Finding:
    """One verdict, either about the manifest as a whole or about one tracked prerequisite."""

    level: str
    prereq_id: str
    headline: str
    detail: str = ""

    def render(self) -> str:
        body = f"[{self.level}] {self.prereq_id}: {self.headline}"
        return f"{body}\n{self.detail}" if self.detail else body


def _is_nonblank_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _evaluate_item(item: dict[str, Any], prereq_id: str) -> Finding:
    """Evaluate one already-located `pre_requisitos_scrub_only` list entry. Never raises.

    `item` is guaranteed to be a `dict` by the caller (`evaluate`'s block-parsing loop reports a
    non-mapping entry as its own "(bloco)" finding and never hands it to this function) — there is
    deliberately no second `isinstance` guard here for that shape: a check nothing can ever drive
    to its FAIL branch cannot be proven by a red mutation, and an unprovable check is exactly the
    kind of decoration BRIEF-COMMON's "no fabricated success" rule exists to catch.
    """
    atendido = item.get("atendido")
    if not isinstance(atendido, bool):
        return Finding(
            level=LEVEL_FAIL,
            prereq_id=prereq_id,
            headline=f"campo `atendido` desconhecido/malformado: {atendido!r} (esperado true/false booleano)",
            detail=(
                '      Um `atendido` que não é um bool exato (string "true", 1, ausente, ...) '
                "não pode ser lido como satisfeito — a cerca falha fechada."
            ),
        )

    if prereq_id == "phi_hmac_key_provisionado":
        evidencia = item.get("evidencia_provisionamento")
        if not atendido:
            return Finding(
                level=LEVEL_FAIL,
                prereq_id=prereq_id,
                headline="PHI_HMAC_KEY ainda não provisionado nos overlays reais",
                detail=(
                    f"      onde: {item.get('onde', '(ausente)')}\n"
                    f"      efeito se ratificado sem isto: "
                    f"{item.get('efeito_se_ratificado_sem_isto', '(ausente)')}"
                ),
            )
        if not _is_nonblank_str(evidencia):
            return Finding(
                level=LEVEL_FAIL,
                prereq_id=prereq_id,
                headline=(
                    "`atendido: true` mas `evidencia_provisionamento` está vazio — "
                    "satisfeito sem prova não conta"
                ),
            )
        return Finding(level=LEVEL_OK, prereq_id=prereq_id, headline=f"provisionado — {evidencia}")

    if prereq_id == "janela_drenagem_cancel_inad":
        janela = item.get("janela_drenagem")
        if not isinstance(janela, dict):
            return Finding(
                level=LEVEL_FAIL,
                prereq_id=prereq_id,
                headline="`janela_drenagem` ausente ou não é um mapeamento",
            )
        inicio, fim = janela.get("inicio"), janela.get("fim")
        if not atendido:
            return Finding(
                level=LEVEL_FAIL,
                prereq_id=prereq_id,
                headline="janela de drenagem CANCEL/INAD ainda não registrada",
                detail=(
                    f"      onde: {item.get('onde', '(ausente)')}\n"
                    f"      efeito se ratificado sem isto: "
                    f"{item.get('efeito_se_ratificado_sem_isto', '(ausente)')}"
                ),
            )
        if not (_is_nonblank_str(inicio) and _is_nonblank_str(fim)):
            return Finding(
                level=LEVEL_FAIL,
                prereq_id=prereq_id,
                headline=(
                    "`atendido: true` mas `janela_drenagem.inicio`/`fim` não estão ambos "
                    f"preenchidos (inicio={inicio!r}, fim={fim!r})"
                ),
            )
        return Finding(level=LEVEL_OK, prereq_id=prereq_id, headline=f"janela registrada: {inicio} -> {fim}")

    # Unreachable given TRACKED_PREREQUISITE_IDS is the only source of prereq_id values passed
    # here, but fail closed rather than silently pass an id this function does not know how to
    # grade.
    return Finding(
        level=LEVEL_FAIL,
        prereq_id=prereq_id,
        headline="id rastreado sem regra de avaliação (defeito da cerca)",
    )


def evaluate(data: dict[str, Any]) -> list[Finding]:
    """The whole comparator, as a pure function over an already-parsed manifest mapping.

    Args:
        data: the manifest's top-level YAML mapping (`yaml.safe_load` output). No I/O, no
            environment — every finding is derived from this dict alone.
    """
    modo = data.get("modo")
    # `modo: "off"` (quoted, the shipped form) and the YAML-1.1 bare-`off` -> `False` trap both
    # mean OFF; neither needs the prerequisites block. Anything else that ISN'T exactly
    # "scrub_only"/"pseudo_keys" is some other unresolved state that `phi_key_policy.py` already
    # demotes to OFF at the loader level — callers of `evaluate` only reach here after confirming
    # ratification, so by the time this runs `modo` is one of the three known tokens.
    if modo not in ("scrub_only", "pseudo_keys"):
        return [
            Finding(
                level=LEVEL_OK,
                prereq_id="(manifesto)",
                headline=f"modo={modo!r} — nenhum pré-requisito de scrub_only se aplica",
            )
        ]

    block = data.get(_BLOCK_KEY)
    if not isinstance(block, list):
        return [
            Finding(
                level=LEVEL_FAIL,
                prereq_id="(bloco)",
                headline=(
                    f"`{_BLOCK_KEY}` ausente ou não é uma lista — `modo: {modo}` ratificado sem "
                    "como provar os pré-requisitos"
                ),
            )
        ]

    by_id: dict[str, dict[str, Any]] = {}
    duplicate_ids: list[str] = []
    malformed_entries: list[Any] = []
    for entry in block:
        if not isinstance(entry, dict):
            malformed_entries.append(entry)
            continue
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            malformed_entries.append(entry)
            continue
        if entry_id in by_id:
            duplicate_ids.append(entry_id)
        by_id[entry_id] = entry

    findings: list[Finding] = []
    for entry in malformed_entries:
        findings.append(
            Finding(
                level=LEVEL_FAIL,
                prereq_id="(bloco)",
                headline=(
                    f"entrada malformada em `{_BLOCK_KEY}` — não é um mapeamento com `id` "
                    f"string não-vazio: {entry!r}"
                ),
            )
        )
    if duplicate_ids:
        findings.append(
            Finding(
                level=LEVEL_FAIL,
                prereq_id="(bloco)",
                headline=f"id(s) duplicado(s) em `{_BLOCK_KEY}`: {sorted(set(duplicate_ids))}",
            )
        )

    unknown_ids = sorted(set(by_id) - set(TRACKED_PREREQUISITE_IDS))
    if unknown_ids:
        findings.append(
            Finding(
                level=LEVEL_FAIL,
                prereq_id="(bloco)",
                headline=(
                    f"id(s) não rastreado(s) em `{_BLOCK_KEY}`: {unknown_ids} — adicionar um "
                    "pré-requisito novo exige atualizar TRACKED_PREREQUISITE_IDS neste script, em "
                    "PR revisado (scripts/ci/ é CODEOWNED), não apenas editar o YAML"
                ),
            )
        )

    for prereq_id in TRACKED_PREREQUISITE_IDS:
        if prereq_id not in by_id:
            findings.append(
                Finding(
                    level=LEVEL_FAIL,
                    prereq_id=prereq_id,
                    headline=f"pré-requisito ausente do bloco `{_BLOCK_KEY}`",
                )
            )
            continue
        findings.append(_evaluate_item(by_id[prereq_id], prereq_id))

    return findings


def exit_code_for(findings: list[Finding]) -> int:
    """1 iff anything failed."""
    return 1 if any(f.level == LEVEL_FAIL for f in findings) else 0


# ---------------------------------------------------------------------------------------------
# Non-vacuity self-check — prove the comparator can go red AND green before trusting it.
# ---------------------------------------------------------------------------------------------

SELF_CHECK_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("draft", LEVEL_OK),
    ("ratificado_modo_off", LEVEL_OK),
    ("ratificado_scrub_only_tudo_atendido", LEVEL_OK),
    ("ratificado_scrub_only_hmac_nao_atendido", LEVEL_FAIL),
    ("ratificado_scrub_only_janela_nao_atendida", LEVEL_FAIL),
    ("ratificado_scrub_only_atendido_sem_evidencia", LEVEL_FAIL),
    ("ratificado_scrub_only_bloco_ausente", LEVEL_FAIL),
    ("ratificado_scrub_only_atendido_malformado", LEVEL_FAIL),
    ("ratificado_pseudo_keys_herda_prerequisitos", LEVEL_FAIL),
)

#: `ratificacao` block shared by every "fully ratified" self-check fixture — the three
#: accountability fields `_is_ratified` (in `phi_key_policy.py`) requires, synthetic but complete.
_RATIFICACAO_COMPLETA: dict[str, Any] = {
    "ratificado": True,
    "revisor": "self-check",
    "ratificado_em": "2026-09-06",
}

_MET_HMAC_ITEM: dict[str, Any] = {
    "id": "phi_hmac_key_provisionado",
    "atendido": True,
    "evidencia_provisionamento": "AWS Secrets Manager arn:...:phi-hmac-key populado 2026-09-06, verificado por dpo-ratifier",
}
_MET_JANELA_ITEM: dict[str, Any] = {
    "id": "janela_drenagem_cancel_inad",
    "atendido": True,
    "janela_drenagem": {"inicio": "2026-10-01T02:00:00-03:00", "fim": "2026-10-01T04:00:00-03:00"},
}
_UNMET_HMAC_ITEM: dict[str, Any] = {
    "id": "phi_hmac_key_provisionado",
    "atendido": False,
    "evidencia_provisionamento": "",
}
_UNMET_JANELA_ITEM: dict[str, Any] = {
    "id": "janela_drenagem_cancel_inad",
    "atendido": False,
    "janela_drenagem": {"inicio": None, "fim": None},
}


def build_self_check_cases() -> tuple[tuple[str, dict[str, Any]], ...]:
    """Materialize `SELF_CHECK_SCENARIOS` into `(name, manifest dict)`. Raises on drift.

    Every "ratificado_*" fixture carries a COMPLETE `ratificacao` block: `run_gate` resolves
    ratification through the real `load_phi_key_policy`, which — correctly — treats an
    incomplete `ratificacao` exactly like `DRAFT` (nothing in force). A self-check fixture with
    `status: RATIFICADO` and no `ratificacao` block would therefore silently exercise the SAME
    "not ratified" branch as the `draft` scenario, teaching this self-check nothing about the
    RATIFICADO branch it is named for.
    """
    manifests: dict[str, dict[str, Any]] = {
        "draft": {"status": "DRAFT", "modo": "scrub_only"},
        "ratificado_modo_off": {"status": "RATIFICADO", "modo": "off", "ratificacao": _RATIFICACAO_COMPLETA},
        "ratificado_scrub_only_tudo_atendido": {
            "status": "RATIFICADO",
            "modo": "scrub_only",
            "ratificacao": _RATIFICACAO_COMPLETA,
            _BLOCK_KEY: [_MET_HMAC_ITEM, _MET_JANELA_ITEM],
        },
        "ratificado_scrub_only_hmac_nao_atendido": {
            "status": "RATIFICADO",
            "modo": "scrub_only",
            "ratificacao": _RATIFICACAO_COMPLETA,
            _BLOCK_KEY: [_UNMET_HMAC_ITEM, _MET_JANELA_ITEM],
        },
        "ratificado_scrub_only_janela_nao_atendida": {
            "status": "RATIFICADO",
            "modo": "scrub_only",
            "ratificacao": _RATIFICACAO_COMPLETA,
            _BLOCK_KEY: [_MET_HMAC_ITEM, _UNMET_JANELA_ITEM],
        },
        "ratificado_scrub_only_atendido_sem_evidencia": {
            "status": "RATIFICADO",
            "modo": "scrub_only",
            "ratificacao": _RATIFICACAO_COMPLETA,
            _BLOCK_KEY: [
                {"id": "phi_hmac_key_provisionado", "atendido": True, "evidencia_provisionamento": "   "},
                _MET_JANELA_ITEM,
            ],
        },
        "ratificado_scrub_only_bloco_ausente": {
            "status": "RATIFICADO",
            "modo": "scrub_only",
            "ratificacao": _RATIFICACAO_COMPLETA,
        },
        "ratificado_scrub_only_atendido_malformado": {
            "status": "RATIFICADO",
            "modo": "scrub_only",
            "ratificacao": _RATIFICACAO_COMPLETA,
            _BLOCK_KEY: [
                {"id": "phi_hmac_key_provisionado", "atendido": "true", "evidencia_provisionamento": "x"},
                _MET_JANELA_ITEM,
            ],
        },
        "ratificado_pseudo_keys_herda_prerequisitos": {
            "status": "RATIFICADO",
            "modo": "pseudo_keys",
            "ratificacao": _RATIFICACAO_COMPLETA,
            _BLOCK_KEY: [_UNMET_HMAC_ITEM, _MET_JANELA_ITEM],
        },
    }
    declared = [name for name, _ in SELF_CHECK_SCENARIOS]
    if sorted(manifests) != sorted(declared):
        raise RuntimeError(
            f"self-check scenarios drifted: SELF_CHECK_SCENARIOS declares {sorted(declared)} "
            f"but build_self_check_cases builds {sorted(manifests)}"
        )
    return tuple((name, manifests[name]) for name, _ in SELF_CHECK_SCENARIOS)


def self_check() -> list[str]:
    """Drive `run_gate` over synthetic manifests written to temp files. Empty == healthy.

    Goes through `run_gate` (loader + evaluate together), not `evaluate` alone, so the self-check
    proves the SAME pipeline `main()` runs, including the real ratification gate — never a
    shortcut that could pass while the wired pipeline is broken.
    """
    problems: list[str] = []
    expected_by_name = dict(SELF_CHECK_SCENARIOS)
    with tempfile.TemporaryDirectory(prefix="phi-scrub-prereqs-self-check-") as tmp_dir:
        for name, manifest in build_self_check_cases():
            tmp_path = Path(tmp_dir) / f"{name}.yaml"
            tmp_path.write_text(yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")
            findings = run_gate(tmp_path)
            actual = LEVEL_FAIL if any(f.level == LEVEL_FAIL for f in findings) else LEVEL_OK
            expected = expected_by_name[name]
            if actual != expected:
                problems.append(
                    f"self-check '{name}': esperava {expected}, veio {actual} ({[f.render() for f in findings]})"
                )
    return problems


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------


def _load_manifest(manifest_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read + parse the manifest. Returns (data, None) or (None, error message). Never raises."""
    if not manifest_path.is_file():
        return None, f"no readable manifest file at {manifest_path}"
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"could not read {manifest_path}: {exc}"
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        return None, f"malformed YAML in {manifest_path}: {exc}"
    if not isinstance(data, dict):
        return None, f"{manifest_path}: root must be a mapping"
    return data, None


def run_gate(manifest_path: Path) -> list[Finding]:
    """The full pipeline `main()` runs against a real path: load -> ratification check -> evaluate.

    Factored out so `self_check()` drives the EXACT SAME code path `main()` does (including the
    canonical `load_phi_key_policy` ratification gate), never a re-derived shortcut that could
    drift from what production actually checks. Never raises.
    """
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from maezo.platform.privacy.phi_key_policy import load_phi_key_policy  # noqa: PLC0415

    policy = load_phi_key_policy(manifest_path)
    if not policy.ratificado:
        return [
            Finding(
                level=LEVEL_OK,
                prereq_id="(manifesto)",
                headline=f"manifesto não está ratificado (declarado={policy.declarado.value}); nada em vigor",
            )
        ]

    data, error = _load_manifest(manifest_path)
    if error is not None or data is None:
        return [
            Finding(
                level=LEVEL_FAIL,
                prereq_id="(manifesto)",
                headline=f"manifesto ratificado mas ilegível para a cerca ({error})",
            )
        ]
    return evaluate(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help=f"manifesto a verificar (default: {DEFAULT_MANIFEST})",
    )
    args = parser.parse_args(argv)

    problems = self_check()
    if problems:
        print("[phi-scrub-prereqs] FAIL: o auto-teste de não-vacuidade não passou —", file=sys.stderr)
        print("  um comparador que não se prova capaz de reprovar não vale como prova.", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = REPO_ROOT / manifest_path

    # Ratification is resolved through the SAME loader the runtime uses — never re-derived here —
    # so this gate can never disagree with `phi_key_policy.py` about whether the manifest is
    # actually in force. `sys.path` is shimmed exactly like `check_deviation_expiry.py` does, so
    # this script has no import-time dependency on the package being installed as a wheel.
    try:
        findings = run_gate(manifest_path)
    except Exception as exc:  # noqa: BLE001 - an unimportable loader is itself a fail-closed finding
        print(f"[phi-scrub-prereqs] FAIL: não consegui avaliar o manifesto ({exc})", file=sys.stderr)
        return 1

    code = exit_code_for(findings)
    stream = sys.stderr if code else sys.stdout
    print(f"[phi-scrub-prereqs] {manifest_path}", file=stream)
    for finding in findings:
        print(f"  {finding.render()}", file=stream)

    if code:
        print(
            "\n[phi-scrub-prereqs] FAIL: pré-requisito(s) de `scrub_only`/`pseudo_keys` não "
            "atendido(s) (R-009, owner decision D7-01). Preencher o item em "
            "`pre_requisitos_scrub_only` com evidência real e `atendido: true` é um ato de DADOS, "
            "no MESMO PR de ratificação — esta cerca nunca assina `status`/`ratificacao.*` por "
            "ninguém.",
            file=sys.stderr,
        )
    else:
        print("[phi-scrub-prereqs] PASS")
    return code


if __name__ == "__main__":
    sys.exit(main())
