"""Cerca (R-110 / F-151): o SQL dinamico de `src/maezo` suprimido no bandit/semgrep foi triado.

O R-110 triou um a um os 85 sitios bandit B608 (85 de 85) e 50 dos 51 sitios semgrep
`avoid-sqlalchemy-text` da auditoria. Cada sitio triado ou foi parametrizado (o valor saiu do texto do
SQL e virou parametro ligado), ou recebeu `# nosec B608 # fonte: <X>` /
`# nosemgrep: avoid-sqlalchemy-text # fonte: <X>`, onde `<X>` nomeia a allowlist fechada (constante de
codigo) ou o validador ancorado de onde vem o IDENTIFICADOR interpolado. Valor externo nunca e'
interpolado: valor vai por `$n` / `:nome`.

RESIDUO DECLARADO (1 de 51): `gateway/communications/provider_notice.py:647` (`text(sql)`). O sitio e'
seguro (`self.schema` vem de `ProviderNoticeInstallation.schema_name`, `Identifier` ancorado
`^[a-z][a-z0-9_]{0,62}$`), mas fica SEM anotacao porque o arquivo e' molde congelado
(`FROZEN_MOLD_PATHS` em `tests/unit/gateway/communications/test_vendor_notice.py`, freeze PW1-C/PW1-D):
re-pin pendente do dono da cerca. O mesmo arquivo tem um segundo `text(f"...{self.schema}...")`
(`prepare_notice`) de mesma fonte, que a regra do registry nao reportou na auditoria; tambem pinado
abaixo como residuo conhecido. `RESIDUO_SEMGREP_CONGELADO` pina exatamente esses dois: um terceiro
`text()` dinamico sem anotacao em qualquer arquivo do conjunto da auditoria faz a cerca falhar.

Esta cerca garante, sem precisar de bandit/semgrep no CI, que:
  1. nenhuma supressao e' nua (sem regra) nem sem `# fonte: <X>` (o segundo `#` encerra a lista de
     testes que o bandit le, entao a justificativa nao vira nome de teste invalido);
  2. o inventario por arquivo e' pinado: uma supressao nova obriga a editar este arquivo (e a
     justificar no PR) — nunca entra calada;
  3. as EXPRESSOES interpoladas nas linhas com nosec sao pinadas por arquivo (`ast.unparse`): trocar
     um identificador triado por outra expressao numa linha ja' suprimida falha aqui;
  4. o residuo semgrep nao anotado e' exatamente o declarado acima.

As provas das allowlists por modulo ficam em `test_sql_dinamico_allowlists.py`.

Leitura por `tokenize` (so' comentarios reais): a palavra `nosec` em docstring nao conta.
"""

from __future__ import annotations

import ast
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
    "gateway/staff_cases/postgres.py": 1,
    "platform/integrations/amh_inbox.py": 1,
    "platform/webhooks/whatsapp/acesso_limite_cpf.py": 3,
    "platform/webhooks/whatsapp/acesso_store.py": 3,
    "platform/webhooks/whatsapp/roteamento.py": 4,
    "portal/api/commission_policies_postgres.py": 2,
}
NOSEMGREP_SQL_TEXT: Final[dict[str, int]] = {
    "gateway/capabilities/authority_postgres.py": 5,
    "gateway/commercial_legitimacy_screen.py": 2,
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
    "portal/admin/assignments.py": 1,
    "portal/api/commission_policies_postgres.py": 2,
}


# Expressoes interpoladas (via `ast.unparse`) nas linhas com `nosec B608`, por arquivo.
INTERPOLADOS_EM_NOSEC: Final[dict[str, frozenset[str]]] = {
    "gateway/audit_anchor_verify.py": frozenset(
        {
            "version_table",
        }
    ),
    "gateway/audit_postgres.py": frozenset(
        {
            "', '.join(_COLUMNS)",
            "_INSERT_PLACEHOLDERS",
        }
    ),
    "gateway/capabilities/authority_postgres.py": frozenset(
        {
            "refs",
            "self.schema",
        }
    ),
    "gateway/capabilities/durability/postgres.py": frozenset(
        {
            "column",
            "table",
        }
    ),
    "gateway/document_requests/production.py": frozenset(
        {
            "reader.table('mzo_auth_input_head')",
            "reader.table('mzo_auth_input_version')",
        }
    ),
    "gateway/documents/authority.py": frozenset(
        {
            "reader.table('mzo_auth_input_head')",
            "where['clause']",
        }
    ),
    "gateway/external_cases/publisher.py": frozenset(
        {
            "kind",
        }
    ),
    "gateway/human/decision_binding.py": frozenset(
        {
            "db.table('act_ru_identitylink')",
            "db.table('act_ru_task')",
            "db.table('mzo_human_decision_authority')",
            "db.table('mzo_human_decision_binding')",
            "db.table('mzo_human_decision_install_receipt')",
            "db.table('mzo_human_decision_qualification')",
            "db.table('mzo_human_evidence')",
            "db.table('mzo_human_principal')",
            "db.table('mzo_human_tenant')",
            "db.table('mzo_portal_read_publication_receipt')",
            "db.table('mzo_portal_read_resource')",
            "db.table('mzo_portal_read_revocation')",
            "names",
            "self._db.table('mzo_human_decision_install_receipt')",
            "self.table('act_ge_bytearray')",
            "self.table('act_re_decision_def')",
            "self.table('act_re_procdef')",
            "self.table('mzo_human_consumer_database')",
            "self.table('mzo_human_consumer_head')",
            "self.table('mzo_human_consumer_qualification')",
            "self.table('mzo_human_consumer_revoked')",
            "self.table('mzo_human_consumer_trust')",
            "self.table('mzo_human_decision_authority')",
            "self.table('mzo_human_tenant')",
            "slots",
            "suffix",
        }
    ),
    "gateway/human/task_publication_source.py": frozenset(
        {
            "self._act",
            "self._native",
        }
    ),
    "gateway/intake/native_authority.py": frozenset(
        {
            "self.table('mzo_auth_input_head')",
            "self.table('mzo_auth_input_version')",
            "self.table('mzo_auth_installation')",
            "self.table('mzo_auth_revoked_key')",
            "self.table('mzo_auth_trust')",
        }
    ),
    "gateway/intake/native_source_lifecycle.py": frozenset(
        {
            "self.table('portal_memberships')",
            "self.table('portal_sessions')",
        }
    ),
    "gateway/staff_cases/case_issuer_sources.py": frozenset(
        {
            "scope_where",
            "self.native_schema",
            "self.schema",
            "self.table",
            "self.where",
        }
    ),
    "gateway/staff_cases/postgres.py": frozenset(
        {
            "schema",
        }
    ),
    "platform/integrations/amh_inbox.py": frozenset(
        {
            "', '.join((f'${i}' for i in range(1, len(_INSERT_COLUMNS) + 1)))",
            "', '.join(_INSERT_COLUMNS)",
        }
    ),
    "platform/webhooks/whatsapp/acesso_limite_cpf.py": frozenset(
        {
            "TABELA",
        }
    ),
    "platform/webhooks/whatsapp/acesso_store.py": frozenset(
        {
            "TABELA",
            "_COLUNAS",
        }
    ),
    "platform/webhooks/whatsapp/roteamento.py": frozenset(
        {
            "TABELA",
            "_COLUNAS",
        }
    ),
    "portal/api/commission_policies_postgres.py": frozenset(
        {
            "_COLUMNS",
        }
    ),
}

# Arquivos dos 51 sitios semgrep da auditoria (raw/semgrep.json do FORENSIC-COMPLETENESS-2026-10).
ARQUIVOS_SEMGREP_AUDITORIA: Final[frozenset[str]] = frozenset(
    {*NOSEMGREP_SQL_TEXT, "gateway/communications/provider_notice.py"}
)

# Residuo declarado: `text()` dinamico sem anotacao, so' no molde congelado (ver docstring).
RESIDUO_SEMGREP_CONGELADO: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("gateway/communications/provider_notice.py", "text(sql)"),
        (
            "gateway/communications/provider_notice.py",
            "text(f'SELECT {self.schema}.prepare_notice(CAST(:scope AS jsonb),CAST(:intent AS jsonb),"
            ":command,:digest,:metadata,:audit,:until)')",
        ),
    }
)


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


def _folhas_dinamicas(no: ast.expr) -> list[str]:
    if isinstance(no, ast.BinOp) and isinstance(no.op, ast.Add):
        return _folhas_dinamicas(no.left) + _folhas_dinamicas(no.right)
    if isinstance(no, ast.JoinedStr):
        return [ast.unparse(v.value) for v in no.values if isinstance(v, ast.FormattedValue)]
    if isinstance(no, ast.Constant) and isinstance(no.value, str):
        return []
    return [ast.unparse(no)]


def _interpolados_em_nosec(path: Path) -> frozenset[str]:
    """Para cada linha com `nosec B608` triado, a maior expressao de string (f-string ou `+`) que a
    contem; devolve o que ela interpola. Linha sem expressao de string vira `<sem-expressao>`."""
    linhas = [linha for linha, c in _comentarios(path) if _NOSEC_TRIADO.search(c)]
    tree = ast.parse(path.read_bytes())
    candidatos = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.JoinedStr) or (isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add))
    ]
    achados: set[str] = set()
    for linha in linhas:
        contem = [n for n in candidatos if n.lineno <= linha <= (n.end_lineno or n.lineno)]
        if not contem:
            achados.add("<sem-expressao>")
            continue
        topo = max(
            contem, key=lambda n: ((n.end_lineno or 0) - n.lineno, (n.end_col_offset or 0) - n.col_offset)
        )
        achados.update(_folhas_dinamicas(topo))
    return frozenset(achados)


def test_expressoes_interpoladas_em_linhas_com_nosec_sao_pinadas() -> None:
    assert {f: _interpolados_em_nosec(_SRC / f) for f in NOSEC_B608} == INTERPOLADOS_EM_NOSEC


def test_a_cerca_de_expressoes_enxerga_troca_de_identificador(tmp_path: Path) -> None:
    """Nao-vacuidade (M7): mesma contagem de nosec, expressao trocada -> conjunto diferente."""
    original = tmp_path / "a.py"
    trocado = tmp_path / "b.py"
    original.write_text('Q = f"SELECT 1 FROM {TABELA} WHERE x = $1"  # nosec B608 # fonte: TABELA\n')
    trocado.write_text('Q = f"SELECT 1 FROM {pedido.tabela} WHERE x = $1"  # nosec B608 # fonte: TABELA\n')
    assert _interpolados_em_nosec(original) == frozenset({"TABELA"})
    assert _interpolados_em_nosec(trocado) == frozenset({"pedido.tabela"})


def _constantes_str_de_modulo(tree: ast.Module) -> set[str]:
    return {
        alvo.id
        for n in tree.body
        if isinstance(n, ast.Assign | ast.AnnAssign)
        and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, str)
        for alvo in (n.targets if isinstance(n, ast.Assign) else [n.target])
        if isinstance(alvo, ast.Name)
    }


def _text_dinamico_sem_anotacao(path: Path) -> set[str]:
    """`text(<nao-literal>)` sem `nosemgrep` triado na linha ou na anterior (constante str de modulo
    conta como literal, como na propagacao de constantes do semgrep)."""
    marcadas = {linha for linha, c in _comentarios(path) if _NOSEMGREP_TRIADO.search(c)}
    tree = ast.parse(path.read_bytes())
    constantes = _constantes_str_de_modulo(tree)
    achados: set[str] = set()
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "text" and n.args):
            continue
        arg = n.args[0]
        if isinstance(arg, ast.Constant) or (isinstance(arg, ast.Name) and arg.id in constantes):
            continue
        if n.lineno in marcadas or n.lineno - 1 in marcadas:
            continue
        achados.add(ast.unparse(n))
    return achados


def test_residuo_semgrep_e_exatamente_o_declarado_no_molde_congelado() -> None:
    residuo = {
        (f, expr) for f in ARQUIVOS_SEMGREP_AUDITORIA for expr in _text_dinamico_sem_anotacao(_SRC / f)
    }
    assert residuo == RESIDUO_SEMGREP_CONGELADO


def test_a_cerca_de_residuo_enxerga_text_dinamico_novo(tmp_path: Path) -> None:
    (tmp_path / "p.py").write_text(
        'C = "SELECT 1"\n'
        "a = text(C)\n"
        'b = text("SELECT 2")\n'
        "# nosemgrep: avoid-sqlalchemy-text # fonte: T\n"
        'c = text(f"SELECT {T}")\n'
        'd = text(f"SELECT {U}")\n',
        encoding="utf-8",
    )
    assert _text_dinamico_sem_anotacao(tmp_path / "p.py") == {"text(f'SELECT {U}')"}
