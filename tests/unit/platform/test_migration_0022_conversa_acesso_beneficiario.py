"""Testes estruturais da migration `0022_conversa_acesso_beneficiario` (DL-0083). Rodam sem Postgres."""

from __future__ import annotations

import re
from pathlib import Path

import maezo

_VERSIONS_DIR = Path(maezo.__file__).parent / "platform" / "migrations" / "versions"
_MIGRATION = _VERSIONS_DIR / "0022_conversa_acesso_beneficiario.py"
_SOURCE = _MIGRATION.read_text(encoding="utf-8")
_CODE = _SOURCE.split('"""', 2)[2]

_FORBIDDEN_COLUMNS = frozenset(
    {
        "telefone", "phone", "from_number", "message_body", "texto", "mensagem", "response_text",
        "beneficiario_pseudo_id", "cpf", "nome", "nascimento", "data_nascimento", "dob", "hash",
        "verification_hash", "payload",
    }
)  # fmt: skip
_REQUIRED_COLUMNS = frozenset(
    {
        "tenant", "conversation_id", "estado", "tentativas", "texto_versao", "texto_sha256", "consentido_em",
        "revogado_em", "portable_subject_ref", "consent_ref", "consentimento_pendente_gravacao",
        "revogacao_pendente", "verificado_em", "expira_em", "bloqueado_ate", "ultima_mensagem_em",
        "regravacoes_falhas",
    }
)  # fmt: skip


def _create_table() -> str:
    casamento = re.search(
        r"CREATE TABLE conversa_acesso_beneficiario \((.*?)\n        \)\n", _CODE, re.DOTALL
    )
    assert casamento
    return casamento.group(1)


def _colunas() -> set[str]:
    return {
        m.group(1)
        for m in re.finditer(
            r"^\s{12}([a-z_0-9]+) (?:text|smallint|timestamptz|boolean)\b", _create_table(), re.MULTILINE
        )
    }


def test_encadeia_em_0021_e_e_a_unica_que_revisa_a_0021() -> None:
    assert re.search(r'^revision: str = "0022"$', _SOURCE, re.MULTILINE)
    assert re.search(r'^down_revision: str \| None = "0021"$', _SOURCE, re.MULTILINE)
    revisam = [
        p.name
        for p in sorted(_VERSIONS_DIR.glob("0*.py"))
        if re.search(r'^down_revision: str \| None = "0021"$', p.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert revisam == ["0022_conversa_acesso_beneficiario.py"]


def test_colunas_exatas_e_nenhuma_de_dado_pessoal() -> None:
    assert _colunas() == _REQUIRED_COLUMNS
    assert not _colunas() & _FORBIDDEN_COLUMNS


def test_estados_da_tabela_sao_os_da_maquina() -> None:
    from maezo.platform.webhooks.whatsapp.acesso import ESTADOS

    trecho = _create_table().split("estado text NOT NULL CHECK (estado IN (")[1].split("))")[0]
    assert set(re.findall(r"'([a-z_]+)'", trecho)) == set(ESTADOS)


def test_checks_e_restricoes() -> None:
    ddl = _create_table()
    assert "conversation_id ~ '^wa:[^:]+[:]hk1_[0-9a-f]+$'" in ddl
    assert "starts_with(conversation_id, 'wa:' || tenant || ':')" in ddl
    assert "tentativas smallint NOT NULL DEFAULT 0 CHECK (tentativas BETWEEN 0 AND 3)" in ddl
    assert "PRIMARY KEY (tenant, conversation_id)" in ddl
    assert "conversa_acesso_verificado_completo" in ddl and "conversa_acesso_pendencia_com_ref" in ddl


def test_nenhum_bind_parameter_acidental_no_sql() -> None:
    from sqlalchemy import text

    blocos = re.findall(r'op\.execute\(\s*r?("""|")(.*?)\1', _CODE, re.DOTALL)
    assert any("CREATE TABLE" in sql for _, sql in blocos)
    for _, sql in blocos:
        assert text(sql)._bindparams == {}, sql[:80]


def test_revoke_e_downgrade_recusa_tabela_populada() -> None:
    """Revisao do #700 (P1-c): o downgrade NUNCA apaga consentimento vivo nem `revogacao_pendente` (um REVOGAR
    que o lago ainda nao recebeu). Com a tabela populada ele RECUSA, antes do DROP (padrao da 0021)."""
    assert 'op.execute("REVOKE ALL ON TABLE conversa_acesso_beneficiario FROM PUBLIC")' in _CODE
    downgrade = _CODE.split("def downgrade() -> None:", 1)[1]
    trava = downgrade.index("IF EXISTS (SELECT 1 FROM conversa_acesso_beneficiario) THEN")
    recusa = downgrade.index("RAISE EXCEPTION 'populated conversa_acesso_beneficiario")
    drop = downgrade.index('op.execute("DROP TABLE conversa_acesso_beneficiario")')
    assert trava < recusa < drop
    assert re.findall(r"op\.execute\(\"([^\"]+)\"\)", downgrade) == [
        "DROP TABLE conversa_acesso_beneficiario"
    ]


def test_regravacoes_tem_teto_igual_ao_do_codigo() -> None:
    from maezo.platform.webhooks.whatsapp.acesso import MAX_REGRAVACOES

    esperado = "regravacoes_falhas smallint NOT NULL DEFAULT 0 CHECK (regravacoes_falhas BETWEEN 0 AND {})"
    assert esperado.format(MAX_REGRAVACOES) in _create_table()
