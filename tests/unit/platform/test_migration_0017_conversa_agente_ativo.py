"""Testes estruturais da migration `0017_conversa_agente_ativo` (ADR-0062, plano §5).

Rodam sem Postgres. Nao substituem EXECUTAR a migration — essa prova e' a de
`tests/integration/platform/test_conversa_agente_ativo_live_pg.py` — mas fixam o que precisa
continuar verdadeiro sobre este DDL:

1. a cadeia e' linear: `0017` revisa `0016`, e so' ela;
2. PHI: nenhuma coluna de telefone, de texto ou de referencia de titular;
3. os CHECKs de §5 estao todos la' (prefixo do tenant, formato, vencimento, colunas do Lucas);
4. nenhum `:nome` no SQL que o `sa.text()` do `op.execute` leria como bind parameter;
5. `downgrade()` derruba exatamente a tabela que `upgrade()` criou, e o `REVOKE ... FROM PUBLIC`
   acompanha a criacao.
"""

from __future__ import annotations

import re
from pathlib import Path

import maezo

_VERSIONS_DIR = Path(maezo.__file__).parent / "platform" / "migrations" / "versions"
_MIGRATION = _VERSIONS_DIR / "0017_conversa_agente_ativo.py"
_SOURCE = _MIGRATION.read_text(encoding="utf-8")
_CODE = _SOURCE.split('"""', 2)[2]

_FORBIDDEN_COLUMNS = frozenset(
    {
        "telefone",
        "phone",
        "from_number",
        "message_body",
        "texto",
        "mensagem",
        "response_text",
        "resumo_contexto",
        "beneficiario_pseudo_id",
        "titular_pseudo_id",
        "cpf",
        "nome",
        "numero_boleto",
        "payload",
    }
)
_REQUIRED_COLUMNS = frozenset(
    {
        "tenant",
        "conversation_id",
        "agente_ativo",
        "revisao",
        "transicao_motivo",
        "lucas_cobranca_subtipo",
        "lucas_competencia",
        "ativo_desde",
        "ultimo_turno_em",
        "expira_em",
    }
)


def _create_table() -> str:
    casamento = re.search(r"CREATE TABLE conversa_agente_ativo \((.*?)\n        \)\n", _CODE, re.DOTALL)
    assert casamento, "CREATE TABLE conversa_agente_ativo nao encontrado"
    return casamento.group(1)


def _colunas() -> set[str]:
    return {
        m.group(1)
        for m in re.finditer(r"^\s{12}([a-z_]+) (?:text|bigint|timestamptz)\b", _create_table(), re.MULTILINE)
    }


def test_encadeia_em_0016_e_e_a_unica_que_revisa_a_0016() -> None:
    assert re.search(r'^revision: str = "0017"$', _SOURCE, re.MULTILINE)
    assert re.search(r'^down_revision: str \| None = "0016"$', _SOURCE, re.MULTILINE)
    revisam_0016 = [
        caminho.name
        for caminho in sorted(_VERSIONS_DIR.glob("0*.py"))
        if re.search(
            r'^down_revision: str \| None = "0016"$', caminho.read_text(encoding="utf-8"), re.MULTILINE
        )
    ]
    assert revisam_0016 == ["0017_conversa_agente_ativo.py"]


def test_as_colunas_sao_exatamente_as_do_contrato() -> None:
    assert _colunas() == _REQUIRED_COLUMNS


def test_nenhuma_coluna_de_telefone_texto_ou_titular() -> None:
    assert not _colunas() & _FORBIDDEN_COLUMNS


def test_os_checks_do_contrato_estao_todos_la() -> None:
    ddl = _create_table()
    assert "CHECK (tenant <> '')" in ddl
    assert "conversation_id ~ '^wa:[^:]+[:]hk1_[0-9a-f]+$'" in ddl
    assert "starts_with(conversation_id, 'wa:' || tenant || ':')" in ddl
    assert "agente_ativo IN ('helena', 'lucas')" in ddl
    assert "CHECK (expira_em > ultimo_turno_em)" in ddl
    assert r"lucas_competencia ~ '^\d{4}-(0[1-9]|1[0-2])$'" in ddl
    assert (
        "agente_ativo = 'lucas'" in ddl
        and "lucas_cobranca_subtipo IS NULL AND lucas_competencia IS NULL" in ddl
    )
    assert "PRIMARY KEY (tenant, conversation_id)" in ddl
    assert "revisao bigint NOT NULL DEFAULT 0" in ddl
    assert (
        "CREATE INDEX conversa_agente_ativo_ultimo_turno ON conversa_agente_ativo (ultimo_turno_em)" in _CODE
    )


def test_nenhum_bind_parameter_acidental_no_sql() -> None:
    """`op.execute(str)` passa por `sa.text()`, que leria `:nome` como bind e quebraria o DDL."""
    from sqlalchemy import text

    blocos = re.findall(r'op\.execute\(\s*r?("""|")(.*?)\1', _CODE, re.DOTALL)
    assert len(blocos) >= 4, "a cerca precisa enxergar o CREATE TABLE, o INDEX, o REVOKE e o DROP"
    assert any("CREATE TABLE" in sql for _, sql in blocos)
    for _, sql in blocos:
        assert text(sql)._bindparams == {}, sql[:80]
    # Nao-vacuidade: o mesmo detector acusa o ':hk1_' que a 0016 teve de evitar com '[:]'.
    assert text("SELECT '^wa:[^:]+:hk1_[0-9a-f]+$'")._bindparams != {}


def test_revoke_e_downgrade() -> None:
    assert 'op.execute("REVOKE ALL ON TABLE conversa_agente_ativo FROM PUBLIC")' in _CODE
    downgrade = _CODE.split("def downgrade() -> None:", 1)[1]
    assert re.findall(r"op\.execute\(\"([^\"]+)\"\)", downgrade) == ["DROP TABLE conversa_agente_ativo"]


def test_o_repo_continua_sem_rls_em_migration() -> None:
    """Plano §5: o isolamento e' o padrao vigente (schema por tenant + tenant na PK/WHERE/CHECK);
    RLS aqui seria mudanca transversal. Se alguem introduzir RLS, este teste pede a revisao do
    contrato em vez de deixar a tabela nova como unica excecao silenciosa."""
    for caminho in sorted(_VERSIONS_DIR.glob("0*.py")):
        assert "ENABLE ROW LEVEL SECURITY" not in caminho.read_text(encoding="utf-8"), caminho.name
