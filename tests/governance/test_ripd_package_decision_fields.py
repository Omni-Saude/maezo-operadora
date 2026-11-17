"""TB-E-12-c — o pacote RIPD não tem campo de decisão preenchido.

Origem: plano de funil SALES-FUNNEL-DECISIONS-PLAN-2026-11 (IMPLEMENTATION-PLAN.md bloco
E-SF-32 / seção E; DEC-TRACKB.md TB-E-12-c). Critério de aceite: "todo campo de decisão fica em
branco para o DPO, sem assinatura de agente (ADR-0007)". Guarda nomeada:
``test_ripd_package_has_no_agent_filled_decision_fields`` — alvo: lint de docs/compliance;
violação injetada: campo "Base legal escolhida" preenchido ou assinatura preenchida.

A decisão de base legal (LGPD art. 7º/11) e a assinatura do RIPD são do DPO/encarregado; um
agente nunca as preenche. O repositório não sabe QUEM fez um commit de modo confiável, então a
guarda é mais estrita que o texto do plano: no pacote RIPD rastreado, campo de decisão ou de
assinatura NÃO tem valor, venha de quem vier. O ato do DPO, quando existir, é registrado fora do
pacote (registro de ratificação), e quem o incorporar ao pacote revisa esta guarda junto.

Escopo (declarado): arquivos ``docs/compliance/**`` rastreados cujo nome contém "ripd" (hoje,
só ``docs/compliance/ripd-kickoff.md``). Esta guarda não edita o conteúdo dele.

Formas reconhecidas de campo:
- linha chave-valor: ``Rótulo: valor`` / ``**Rótulo:** valor`` / ``- **Rótulo**: valor``;
- tabela markdown: coluna cujo cabeçalho é um rótulo de decisão — toda célula do corpo vazia.

Valor vazio = só espaços/sublinhados/traços/pontos/colchetes, ou "(em branco)", "(vazio)",
"(a preencher pelo DPO)", "pendente". Falha: ``ripd_decision_field_filled``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOKEN = "ripd_decision_field_filled"

DECISION_LABELS: tuple[str, ...] = (
    "base legal escolhida",
    "base legal adotada",
    "base legal definida",
    "decisão do dpo",
    "decisão do encarregado",
    "parecer do dpo",
    "assinatura",
    "assinado por",
    "assinatura do dpo",
    "assinatura do encarregado",
    "ratificado por",
    "ratificação",
    "data da ratificação",
    "aprovado por",
    "chosen legal basis",
    "legal basis chosen",
    "dpo decision",
    "dpo sign-off",
    "signature",
    "signed by",
    "approved by",
    "ratified by",
)

_LABEL_ALT = "|".join(re.escape(label) for label in sorted(DECISION_LABELS, key=len, reverse=True))
_KV = re.compile(
    rf"^\s*(?:[-*]\s+)?(?:\*\*|__)?(?P<label>{_LABEL_ALT})(?:\*\*|__)?\s*:\s*(?:\*\*|__)?(?P<value>.*)$",
    re.IGNORECASE,
)
_EMPTY = re.compile(
    r"^[\s_\-—–.*\[\]()`]*$|^\(?\s*(?:em branco|vazio|a preencher pelo (?:dpo|encarregado)|pendente)\s*\)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Filled:
    path: str
    line_no: int
    label: str
    value: str


def _is_empty(value: str) -> bool:
    return bool(_EMPTY.match(value.strip()))


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _header_label(cell: str) -> str:
    return re.sub(r"[*_`]", "", cell).strip().rstrip(":").strip().lower()


def find_filled_decision_fields(rel: str, text: str) -> list[Filled]:
    lines = text.splitlines()
    found: list[Filled] = []
    for i, line in enumerate(lines, start=1):
        m = _KV.match(line)
        if m and not line.lstrip().startswith("|") and not _is_empty(m.group("value")):
            found.append(Filled(rel, i, m.group("label"), m.group("value").strip()))
    # Tabelas: cabeçalho, linha separadora, corpo.
    i = 0
    while i < len(lines) - 1:
        head, sep = lines[i], lines[i + 1]
        if head.lstrip().startswith("|") and re.match(r"^\s*\|?\s*:?-{3,}", sep):
            header = [_header_label(c) for c in _cells(head)]
            cols = [k for k, h in enumerate(header) if h in DECISION_LABELS]
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                row = _cells(lines[j])
                for k in cols:
                    if k < len(row) and not _is_empty(row[k]):
                        found.append(Filled(rel, j + 1, header[k], row[k]))
                j += 1
            i = j
        else:
            i += 1
    return found


def _ripd_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "docs/compliance"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return sorted(p for p in out.split("\0") if p.endswith(".md") and "ripd" in Path(p).name.lower())


def test_ripd_package_has_no_agent_filled_decision_fields() -> None:
    files = _ripd_files()
    assert files, "nenhum pacote RIPD rastreado em docs/compliance (escopo vazio = falha)"
    assert "docs/compliance/ripd-kickoff.md" in files
    found = [f for rel in files for f in find_filled_decision_fields(rel, (REPO / rel).read_text("utf-8"))]
    assert not found, "\n".join(f"{TOKEN}: {f.path}:{f.line_no}: {f.label} = {f.value!r}" for f in found)


# --------------------------------------------------------------------------- casos injetados

_BLANK_PACKAGE = """# RIPD — fluxo DS (insumo)

**Base legal escolhida:** ____________
**Assinatura do DPO:**
- **Data da ratificação**: (em branco)

| Fluxo | Base legal candidata (DPO confirma) | Base legal escolhida | Assinatura |
|---|---|---|---|
| F-DS | Art. 11, II, a (candidata) | | ___ |
"""

_ROW = "| F-DS | Art. 11, II, a (candidata) | | ___ |"


def test_blank_package_passes() -> None:
    assert find_filled_decision_fields("docs/compliance/ripd-ds.md", _BLANK_PACKAGE) == []


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("**Base legal escolhida:** ____________", "**Base legal escolhida:** Art. 11, II, a"),
        ("**Assinatura do DPO:**", "**Assinatura do DPO:** Claude (agente)"),
        ("- **Data da ratificação**: (em branco)", "- **Data da ratificação**: 2026-11-16"),
        (_ROW, "| F-DS | Art. 11, II, a (candidata) | Art. 11, II, a | ___ |"),
        (_ROW, "| F-DS | Art. 11, II, a (candidata) | | agente |"),
    ],
)
def test_filled_decision_field_is_refused(tmp_path: Path, old: str, new: str) -> None:
    assert old in _BLANK_PACKAGE
    target = tmp_path / "docs" / "compliance" / "ripd-ds.md"
    target.parent.mkdir(parents=True)
    target.write_text(_BLANK_PACKAGE.replace(old, new), encoding="utf-8")
    found = find_filled_decision_fields("docs/compliance/ripd-ds.md", target.read_text("utf-8"))
    assert len(found) == 1, found


def test_candidate_basis_column_is_not_a_decision_field() -> None:
    """ "Candidate LGPD basis (DPO to confirm)" (ripd-kickoff §2) é insumo, não decisão."""
    text = "| Flow | Candidate LGPD basis (DPO to confirm) |\n|---|---|\n| F1 | Art. 11 — hypothesis TBD |\n"
    assert find_filled_decision_fields("docs/compliance/ripd-kickoff.md", text) == []
