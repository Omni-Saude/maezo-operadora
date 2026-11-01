"""Cerca (R-110 / F-151): todo SQL dinamico de `src/maezo` suprimido no bandit/semgrep foi triado.

O R-110 triou um a um os 85 sitios bandit B608 e os 51 semgrep `avoid-sqlalchemy-text`. Cada sitio
ou foi parametrizado (o valor saiu do texto do SQL e virou parametro ligado), ou recebeu
`# nosec B608 # fonte: <X>` / `# nosemgrep: avoid-sqlalchemy-text # fonte: <X>`, onde `<X>` nomeia
a allowlist fechada (constante de codigo) ou o validador ancorado de onde vem o IDENTIFICADOR
interpolado. Valor externo nunca e' interpolado: valor vai por `$n` / `:nome`.

Esta cerca garante, sem precisar de bandit/semgrep no CI, que:
  1. nenhuma supressao e' nua (sem regra) nem sem `# fonte: <X>` (o segundo `#` encerra a lista de
     testes que o bandit le, entao a justificativa nao vira nome de teste invalido);
  2. o inventario por arquivo e' pinado: uma supressao nova obriga a editar este arquivo (e a
     justificar no PR) — nunca entra calada.

As provas das allowlists por modulo ficam em `test_sql_dinamico_allowlists.py`.

Leitura por `tokenize` (so' comentarios reais): a palavra `nosec` em docstring nao conta.
"""

from __future__ import annotations

import io
import re
import tokenize
from collections import Counter
from pathlib import Path
from typing import Final

_SRC: Final[Path] = Path(__file__).resolve().parents[3] / "src" / "maezo"

_NOSEC: Final[re.Pattern[str]] = re.compile(r"#\s*nosec\b(?P<resto>.*)$")
_NOSEC_TRIADO: Final[re.Pattern[str]] = re.compile(r"#\s*nosec B608 # fonte: \S.*$")
_NOSEMGREP: Final[re.Pattern[str]] = re.compile(r"#\s*nosemgrep\b(?P<resto>.*)$")
_NOSEMGREP_TRIADO: Final[re.Pattern[str]] = re.compile(r"#\s*nosemgrep: avoid-sqlalchemy-text # fonte: \S.*$")

# Inventario pinado: arquivo (relativo a src/maezo) -> numero de supressoes triadas.
NOSEC_B608: Final[dict[str, int]] = {
    "gateway/audit_anchor_verify.py": 1,
    "gateway/audit_postgres.py": 1,
    "gateway/capabilities/authority_postgres.py": 4,
    "gateway/capabilities/durability/postgres.py": 2,
    "gateway/document_requests/production.py": 2,
    "gateway/documents/authority.py": 1,
    "gateway/external_cases/publisher.py": 1,
    "gateway/human/decision_binding.py": 22,
    "gateway/human/task_publication_source.py": 4,
    "gateway/intake/native_authority.py": 6,
    "gateway/intake/native_source_lifecycle.py": 10,
    "gateway/staff_cases/case_issuer_sources.py": 7,
}
NOSEMGREP_SQL_TEXT: Final[dict[str, int]] = {
    "gateway/capabilities/authority_postgres.py": 5,
    "gateway/commercial_legitimacy_screen.py": 2,
    "gateway/communications/provider_notice.py": 1,
    "gateway/document_requests/production.py": 2,
    "gateway/documents/authority.py": 1,
    "gateway/external_cases/postgres.py": 1,
    "gateway/external_cases/publisher.py": 3,
    "gateway/human/assignment_receipt.py": 1,
    "gateway/human/assignment_transport.py": 1,
    "gateway/human/provider_membership_administration_postgres.py": 1,
    "gateway/human/vendor_membership_administration_postgres.py": 1,
    "gateway/intake/native_authority.py": 6,
    "gateway/intake/native_source_lifecycle.py": 10,
    "gateway/intake/recovery_store.py": 2,
    "gateway/staff_cases/case_issuer_sources.py": 7,
}


def _comentarios(path: Path) -> list[tuple[int, str]]:
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
    return [(tok.start[0], tok.string) for tok in tokens if tok.type == tokenize.COMMENT]


def _varre(root: Path) -> tuple[Counter[str], Counter[str], list[str]]:
    nosec: Counter[str] = Counter()
    nosemgrep: Counter[str] = Counter()
    defeitos: list[str] = []
    for path in sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts):
        rel = str(path.relative_to(root))
        for linha, comentario in _comentarios(path):
            if _NOSEC.search(comentario):
                if _NOSEC_TRIADO.search(comentario):
                    nosec[rel] += 1
                else:
                    defeitos.append(f"{rel}:{linha}: nosec sem 'B608 # fonte: <X>'")
            if _NOSEMGREP.search(comentario):
                if _NOSEMGREP_TRIADO.search(comentario):
                    nosemgrep[rel] += 1
                else:
                    defeitos.append(f"{rel}:{linha}: nosemgrep sem 'avoid-sqlalchemy-text # fonte: <X>'")
    return nosec, nosemgrep, defeitos


def test_toda_supressao_sql_em_src_nomeia_regra_e_fonte() -> None:
    _, _, defeitos = _varre(_SRC)
    assert defeitos == []


def test_inventario_de_supressoes_b608_e_pinado() -> None:
    nosec, _, _ = _varre(_SRC)
    assert dict(nosec) == NOSEC_B608


def test_inventario_de_supressoes_semgrep_e_pinado() -> None:
    _, nosemgrep, _ = _varre(_SRC)
    assert dict(nosemgrep) == NOSEMGREP_SQL_TEXT


def test_a_cerca_enxerga_supressao_nua_e_triada(tmp_path: Path) -> None:
    """Nao-vacuidade: a mesma varredura conta as triadas e acusa as nuas, ignorando docstrings."""
    (tmp_path / "plantado.py").write_text(
        '"""docstring citando # nosec B608 nao conta."""\n'
        'A = f"SELECT 1 FROM {T}"  # nosec B608 # fonte: T constante\n'
        'B = f"SELECT 1 FROM {T}"  # nosec\n'
        'C = f"SELECT 1 FROM {T}"  # nosec B608\n'
        "# nosemgrep: avoid-sqlalchemy-text # fonte: T constante\n"
        "# nosemgrep\n",
        encoding="utf-8",
    )
    nosec, nosemgrep, defeitos = _varre(tmp_path)
    assert dict(nosec) == {"plantado.py": 1}
    assert dict(nosemgrep) == {"plantado.py": 1}
    assert defeitos == [
        "plantado.py:3: nosec sem 'B608 # fonte: <X>'",
        "plantado.py:4: nosec sem 'B608 # fonte: <X>'",
        "plantado.py:6: nosemgrep sem 'avoid-sqlalchemy-text # fonte: <X>'",
    ]
