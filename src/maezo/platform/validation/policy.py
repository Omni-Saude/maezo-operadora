"""Policy-artifact validation: autonomy levels (ADR-0008) + the PHI dispositions manifest.

Ported from the v1 donor repo's `platform/validation/autonomy.py` (frozen-set
gate), adapted to this repo's `Report`/`_loaders` and to the real shape of
`spec/policies/autonomy/` (`L0-core.yaml`, `_hard_frozen.yaml`,
`tenants-amh.yaml`).

Checks:
- schema: every entry under `actions:` in a `*-core.yaml` file has a `level`
  in {L0, L1, L2, L3};
- `hard: true` actions must be `level: L0` (ADR-0008: the immutable floor is
  always L0);
- immutability guardrail: `_hard_frozen.yaml` must match, item-for-item and
  level-for-level, the set of `hard: true` actions actually declared across
  the `*-core.yaml` files. Any drift — an item removed, added, or re-leveled
  in only one of the two places — is a compliance regression and FAILS the
  gate; it is never a warning;
- tenant override files (`tenants-*.yaml`) may never touch (add/modify) a
  hard-frozen action.

Second family, added by owner decision R-199: `spec/policies/phi/` — the
CODEOWNED manifest that receives the DPO's disposition over the PHI-shaped,
unlisted process-variable names. See the section header above
`load_phi_dispositions` for what it is, why its failure mode is `raise` rather
than the sibling privacy loader's "resolve to off", and why this module
deliberately does not import the completeness fence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._loaders import ParseError, load_yaml
from .result import Report

VALID_LEVELS = frozenset({"L0", "L1", "L2", "L3"})
FROZEN_FILENAME = "_hard_frozen.yaml"


def _as_dict(value: object) -> dict[str, object] | None:
    return value if isinstance(value, dict) else None


def load_frozen_hard(path: Path, report: Report) -> dict[str, str] | None:
    """Load `_hard_frozen.yaml` -> {action: level}. Returns None if invalid."""
    if not path.exists():
        report.error(path, f"frozen hard-item list {FROZEN_FILENAME} is missing (compliance guardrail)")
        return None
    try:
        data = _as_dict(load_yaml(path))
    except ParseError as exc:
        report.error(path, str(exc))
        return None
    if data is None:
        report.error(path, "root is not a mapping")
        return None
    items = data.get("hard_items")
    if not isinstance(items, list):
        report.error(path, "'hard_items' is missing or is not a list")
        return None
    frozen: dict[str, str] = {}
    for entry in items:
        entry_dict = _as_dict(entry)
        if entry_dict is None or "action" not in entry_dict:
            report.error(path, f"invalid hard_items entry: {entry!r}")
            continue
        frozen[str(entry_dict["action"])] = str(entry_dict.get("level", "L0"))
    return frozen


def validate_core_file(path: Path, report: Report) -> dict[str, str]:
    """Validate one core-layer file (`L*-core.yaml`); returns its {action: level} hard items."""
    hard_found: dict[str, str] = {}
    try:
        data = _as_dict(load_yaml(path))
    except ParseError as exc:
        report.error(path, str(exc))
        return hard_found
    if data is None:
        report.error(path, "root is not a mapping")
        return hard_found

    actions = _as_dict(data.get("actions"))
    if actions is None:
        report.error(path, "'actions' is missing or is not a mapping")
        return hard_found

    for name, spec in actions.items():
        spec_dict = _as_dict(spec)
        if spec_dict is None:
            report.error(path, f"action '{name}' is not a mapping")
            continue
        level = spec_dict.get("level")
        if level not in VALID_LEVELS:
            report.error(path, f"action '{name}' has invalid level: {level!r} (expected one of L0-L3)")
        if spec_dict.get("hard") is True:
            if level != "L0":
                report.error(path, f"hard action '{name}' must be level L0, not {level!r}")
            hard_found[str(name)] = str(level)
    return hard_found


def validate_tenant_file(path: Path, frozen: dict[str, str], report: Report) -> None:
    """Validate a tenant override file: it must never touch a hard-frozen action."""
    try:
        data = _as_dict(load_yaml(path))
    except ParseError as exc:
        report.error(path, str(exc))
        return
    if data is None:
        report.error(path, "root is not a mapping")
        return
    overrides = _as_dict(data.get("overrides")) or {}
    for name, spec in overrides.items():
        if name in frozen:
            report.error(
                path,
                f"tenant override touches hard-frozen action '{name}' — forbidden (ADR-0008)",
            )
            continue
        spec_dict = _as_dict(spec)
        if spec_dict is not None and "level" in spec_dict and spec_dict["level"] not in VALID_LEVELS:
            report.error(path, f"override '{name}' has invalid level: {spec_dict['level']!r}")


def reconcile_frozen(
    frozen: dict[str, str], hard_found: dict[str, str], frozen_path: Path, report: Report
) -> None:
    """Compare the frozen list against the hard items actually found in the core layers."""
    missing = set(frozen) - set(hard_found)  # frozen but gone from core => removed/downgraded
    extra = set(hard_found) - set(frozen)  # new hard item in core, not registered in the frozen list
    for action in sorted(missing):
        report.error(
            frozen_path,
            f"frozen hard item '{action}' is no longer present/hard in the core layers "
            "(removal or downgrade of an immutable rule — blocked)",
        )
    for action in sorted(extra):
        report.error(
            frozen_path,
            f"new hard item '{action}' in the core layers is not registered in {FROZEN_FILENAME} "
            "(add it to the frozen list in the same change)",
        )
    for action in sorted(set(frozen) & set(hard_found)):
        if frozen[action] != hard_found[action]:
            report.error(
                frozen_path,
                f"hard item '{action}': level {hard_found[action]!r} differs from frozen "
                f"{frozen[action]!r} (change to an immutable rule — blocked)",
            )


def validate_dir(root: Path, report: Report) -> None:
    """Validate the whole `policies/autonomy/` directory (core + tenants + frozen)."""
    if not root.exists() or not root.is_dir():
        report.error(root, "autonomy policy directory does not exist")
        return

    yaml_files = sorted(p for p in root.glob("*.yaml") if p.name != FROZEN_FILENAME)
    if not yaml_files:
        report.error(root, "no autonomy policy YAML files found")

    frozen_path = root / FROZEN_FILENAME
    frozen = load_frozen_hard(frozen_path, report)

    hard_found: dict[str, str] = {}
    core_files = [p for p in yaml_files if p.name.endswith("-core.yaml")]
    if not core_files:
        report.error(root, "no '*-core.yaml' autonomy policy file found")
    for path in core_files:
        hard_found.update(validate_core_file(path, report))

    if frozen is not None:
        for path in yaml_files:
            if path.name.startswith("tenants-"):
                validate_tenant_file(path, frozen, report)
        reconcile_frozen(frozen, hard_found, frozen_path, report)


# ---------------------------------------------------------------------------
# PHI dispositions manifest (`spec/policies/phi/` — decisao do dono R-199)
# ---------------------------------------------------------------------------
#
# The manifest is the CODEOWNED vehicle for the DPO's signature over the PHI-shaped,
# unlisted process-variable names the completeness fence records as OPEN QUESTIONS in
# `phi_completeness.DISPOSITIONS`. It ships with every disposition and every ratification
# field EMPTY, and the owner decision (R-199) inverted the ordering deliberately: the
# manifest lands BEFORE the signatures, so signing is a field fill that already produces
# gate effect instead of the START of a migration project.
#
# WHY THE FAILURE MODE HERE IS `raise`, NOT "resolve to off". The sibling privacy loader
# (`platform.privacy.phi_key_policy`) SWALLOWS every error and returns `OFF`, because that
# flag has a safe do-nothing state and lives on a worker's hot path — a raise there would
# turn a governance-config problem into a beneficiary's stalled case. This manifest has no
# `off`: it is read only by BUILD paths (`make validate-artifacts` and the completeness
# fence), where the safe direction is to REFUSE and go red. `validate_phi_dispositions_dir`
# converts the refusal into a blocking `Report` finding for the CI gate.
#
# THE DIRECTION OF THE IMPORT MATTERS. This module does NOT import `phi_completeness`: the
# code<->manifest closure lives on the fence's side, so `make validate-artifacts` still does
# not call the fence (`test_fence_is_not_wired_into_the_cli_yet` stays literally true).

PHI_DISPOSITIONS_DIRNAME = "phi"
PHI_DISPOSITIONS_FILENAME = "phi-dispositions-migration.yaml"

#: The `id:` the manifest must declare — the owner-decision id of the migration act (R-199).
PHI_DISPOSITIONS_ID = "PHI-DISPOSICOES-MIGRACAO-MANIFESTO"

#: `status:` literal that opens the manifest-level gate. Anything else is a draft.
PHI_RATIFIED_STATUS = "RATIFICADO"
PHI_DRAFT_STATUS = "DRAFT"

#: The CLOSED vocabulary a `disposicao` may carry. Engineering supplies the vocabulary (it is
#: schema, and it mirrors the two recommendation shapes the fence's table already uses); the DPO
#: supplies the VALUE. `None` — the state of every line today — means "not decided".
PHI_DISPOSICAO_LISTAR = "LISTAR_EM_CONJUNTO_PHI"
PHI_DISPOSICAO_NAO_PHI = "REGISTRAR_NAO_PHI"
PHI_DISPOSICAO_VOCABULARY = frozenset({PHI_DISPOSICAO_LISTAR, PHI_DISPOSICAO_NAO_PHI})

#: Template guard mirrored from `legal_bases_matrix` / `auth_criteria` / `phi_key_policy`: a root
#: `unratified: true` key makes the whole file REFUSED, so pointing a path at a placeholder by
#: mistake also fails closed.
PHI_TEMPLATE_MARKER = "unratified"


class PhiDispositionsError(Exception):
    """The dispositions manifest is missing, malformed, or internally inconsistent.

    Raised — never swallowed — because this manifest has no safe "do nothing" state: it is
    read only by build-time gates, where refusing is the fail-closed direction.
    """


@dataclass(frozen=True, slots=True)
class PhiDisposition:
    """One line of the manifest: a name, the DPO's disposition, and its accountability."""

    nome: str
    disposicao: str | None
    ratificado: bool
    revisor: str | None
    ratificado_em: str | None


@dataclass(frozen=True, slots=True)
class PhiDispositionsManifest:
    """The parsed, already-validated manifest. Constructing it IS the validation."""

    status: str
    ratificado: bool
    itens: tuple[PhiDisposition, ...]

    @property
    def nomes(self) -> frozenset[str]:
        """Every name the manifest carries a signature slot for."""
        return frozenset(item.nome for item in self.itens)

    @property
    def ratificadas(self) -> dict[str, str]:
        """`{nome: disposicao}` for the lines carrying a COMPLETE ratification.

        Empty today, and that emptiness is the honest recorded state: no disposition has been
        signed. A line with a partial ratification never appears here — the loader refuses the
        file before it could.
        """
        return {item.nome: item.disposicao for item in self.itens if item.ratificado and item.disposicao}


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_label(where: str, entry: dict[str, object]) -> None:
    """An unratified manifest/line must carry the pending-signature label, non-empty."""
    if not _nonblank(entry.get("rotulo")):
        raise PhiDispositionsError(
            f"{where}: nao ratificado e sem `rotulo` nao-vazio — enquanto nao ha assinatura o "
            "artefato tem de se declarar como recomendacao pendente, nunca como decisao"
        )


def _parse_ratification(where: str, block: object) -> tuple[bool, str | None, str | None]:
    """Parse one `ratificacao:` block. Partial ratification is REFUSED, never rounded up."""
    if not isinstance(block, dict):
        raise PhiDispositionsError(f"{where}: bloco `ratificacao` ausente ou nao e um mapeamento")
    flag = block.get("ratificado")
    if flag is not True and flag is not False:
        raise PhiDispositionsError(
            f"{where}: `ratificacao.ratificado` tem de ser o booleano literal true ou false "
            f"(recebido {flag!r}) — uma string 'true' ou um 1 nao contam como assinatura"
        )
    revisor = block.get("revisor")
    ratificado_em = block.get("ratificado_em")
    if flag is True and not (_nonblank(revisor) and _nonblank(ratificado_em)):
        raise PhiDispositionsError(
            f"{where}: ratificacao INCOMPLETA — `ratificado: true` exige `revisor` e "
            "`ratificado_em` nao-vazios (ADR-0007, prestacao de contas). Uma assinatura pela "
            "metade e uma edicao inacabada, nao uma ratificacao"
        )
    if flag is False and (revisor is not None or ratificado_em is not None):
        raise PhiDispositionsError(
            f"{where}: `ratificado: false` com `revisor`/`ratificado_em` preenchidos — estado "
            "ambiguo; os dois campos ficam nulos ate a assinatura existir"
        )
    return (
        flag,
        str(revisor) if _nonblank(revisor) else None,
        str(ratificado_em) if _nonblank(ratificado_em) else None,
    )


def _parse_disposition(index: int, raw: object) -> PhiDisposition:
    where = f"disposicoes[{index}]"
    entry = _as_dict(raw)
    if entry is None:
        raise PhiDispositionsError(f"{where}: nao e um mapeamento")
    nome = entry.get("nome")
    if not _nonblank(nome):
        raise PhiDispositionsError(f"{where}: `nome` ausente ou vazio")
    nome = str(nome).strip()
    where = f"disposicoes[{index}] ({nome})"

    disposicao = entry.get("disposicao")
    if disposicao is not None and disposicao not in PHI_DISPOSICAO_VOCABULARY:
        raise PhiDispositionsError(
            f"{where}: `disposicao` {disposicao!r} fora do vocabulario fechado "
            f"{sorted(PHI_DISPOSICAO_VOCABULARY)} — null significa 'nao decidido'"
        )

    ratificado, revisor, ratificado_em = _parse_ratification(where, entry.get("ratificacao"))
    if ratificado and disposicao is None:
        raise PhiDispositionsError(
            f"{where}: ratificada sem `disposicao` — assinar um campo vazio nao decide nada"
        )
    if not ratificado:
        if disposicao is not None:
            raise PhiDispositionsError(
                f"{where}: `disposicao` preenchida sem ratificacao. Um valor encostado aqui sem "
                "assinatura seria a engenharia decidindo pelo DPO; a recomendacao com evidencia "
                "vive em phi_completeness.DISPOSITIONS, nunca neste campo"
            )
        _require_label(where, entry)
    return PhiDisposition(
        nome=nome,
        disposicao=str(disposicao) if disposicao is not None else None,
        ratificado=ratificado,
        revisor=revisor,
        ratificado_em=ratificado_em,
    )


def load_phi_dispositions(path: Path) -> PhiDispositionsManifest:
    """Load and fully validate the PHI dispositions manifest, or raise `PhiDispositionsError`.

    Fail-closed on: a missing/unreadable/malformed file, a non-mapping root, the template
    marker `unratified: true`, a wrong `version`/`id`, an unknown `status`, a manifest that
    claims `RATIFICADO` without the three accountability fields, a duplicated name, a
    disposition outside the closed vocabulary, a partial or contradictory ratification, a
    ratified line with no disposition, an unratified line carrying a disposition value, and an
    unratified manifest/line with no pending-signature label. There is no lenient path.
    """
    if not path.is_file():
        raise PhiDispositionsError(f"{path}: manifesto de disposicoes PHI ausente")
    try:
        data = _as_dict(load_yaml(path))
    except ParseError as exc:
        raise PhiDispositionsError(f"{path}: {exc}") from exc
    if data is None:
        raise PhiDispositionsError(f"{path}: raiz nao e um mapeamento")
    if data.get(PHI_TEMPLATE_MARKER) is True:
        raise PhiDispositionsError(
            f"{path}: raiz `{PHI_TEMPLATE_MARKER}: true` — este e um template, nunca um manifesto"
        )
    if data.get("version") != 1:
        raise PhiDispositionsError(f"{path}: `version` tem de ser 1 (recebido {data.get('version')!r})")
    if data.get("id") != PHI_DISPOSITIONS_ID:
        raise PhiDispositionsError(
            f"{path}: `id` tem de ser {PHI_DISPOSITIONS_ID!r} (recebido {data.get('id')!r})"
        )
    if not _nonblank(data.get("pergunta_em")):
        raise PhiDispositionsError(
            f"{path}: `pergunta_em` ausente — o manifesto tem de apontar onde a pergunta de cada "
            "nome esta escrita por extenso"
        )

    status = data.get("status")
    if status not in (PHI_DRAFT_STATUS, PHI_RATIFIED_STATUS):
        raise PhiDispositionsError(
            f"{path}: `status` {status!r} desconhecido (esperado {PHI_DRAFT_STATUS} ou {PHI_RATIFIED_STATUS})"
        )
    ratificado, _, _ = _parse_ratification(f"{path}: manifesto", data.get("ratificacao"))
    if status == PHI_RATIFIED_STATUS and not ratificado:
        raise PhiDispositionsError(
            f"{path}: `status: {PHI_RATIFIED_STATUS}` com `ratificacao.ratificado` diferente de "
            "true — o status sozinho nao ratifica nada"
        )
    if status == PHI_DRAFT_STATUS and ratificado:
        raise PhiDispositionsError(
            f"{path}: `ratificacao.ratificado: true` sob `status: {PHI_DRAFT_STATUS}` — estado contraditorio"
        )
    if not ratificado:
        _require_label(f"{path}: manifesto", data)

    raw_items = data.get("disposicoes")
    if not isinstance(raw_items, list) or not raw_items:
        raise PhiDispositionsError(f"{path}: `disposicoes` ausente, vazia ou nao e uma lista")
    itens = tuple(_parse_disposition(i, raw) for i, raw in enumerate(raw_items))
    nomes = [item.nome for item in itens]
    duplicated = sorted({n for n in nomes if nomes.count(n) > 1})
    if duplicated:
        raise PhiDispositionsError(
            f"{path}: nome(s) duplicado(s) em `disposicoes`: {duplicated} — duas linhas de "
            "assinatura para a mesma pergunta"
        )
    return PhiDispositionsManifest(status=str(status), ratificado=ratificado, itens=itens)


def validate_phi_dispositions_dir(root: Path, report: Report) -> None:
    """Validate `spec/policies/phi/` for `make validate-artifacts` (fail-closed).

    Only the dispositions manifest is this validator's business. The directory's other
    artifact (`dossier-narrative-zone.yaml`) has its own loader and its own two-signature
    rule in `maezo.platform.privacy.dossier_zone`; claiming it here would duplicate a gate
    instead of adding one.
    """
    if not root.is_dir():
        report.error(root, "PHI policy directory does not exist")
        return
    path = root / PHI_DISPOSITIONS_FILENAME
    try:
        load_phi_dispositions(path)
    except PhiDispositionsError as exc:
        report.error(path, str(exc))
