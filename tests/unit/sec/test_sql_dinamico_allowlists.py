"""Provas das allowlists citadas pelas supressoes `nosec B608` / `nosemgrep` (R-110 / F-151).

Cada `-- <fonte>` de uma supressao em `src/maezo` aponta para uma allowlist fechada (constante de
codigo) ou para um validador ancorado de identificador. Este arquivo prova, por modulo, que:
  * a allowlist e' fechada e so' contem identificadores SQL simples; e
  * o validador recusa a carga de injecao (inclusive o `\\n` final que um `$` sem ancora aceitaria)
    ANTES de qualquer SQL ser montado.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Final

import pytest

# Identificador PostgreSQL simples, sem aspas: o unico formato que uma allowlist pode conter.
SIMPLES: Final[re.Pattern[str]] = re.compile(r"[a-z_][a-z0-9_]{0,62}")

_SRC: Final[Path] = Path(__file__).resolve().parents[3] / "src" / "maezo"

INJECOES: Final[tuple[str, ...]] = (
    'x"; DROP TABLE t; --',
    "x; DROP TABLE t",
    "x\n",
    "x OR 1=1",
    'x"."y',
    "",
    "X",
)


def _funcao(modulo: str, nome: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse((_SRC / modulo).read_bytes())
    achadas = [
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == nome
    ]
    assert len(achadas) == 1, (modulo, nome)
    return achadas[0]


def iteraveis_literais_dos_lacos(modulo: str, funcao: str, variavel: str) -> list[Any]:
    """Valores literais dos iteraveis de cada `for` da funcao que liga `variavel` (o nome interpolado).

    Resolve o iteravel quando ele e' um nome local atribuido.

    `ast.literal_eval` so' aceita literal puro: se alguem trocar a tupla por algo computado ou vindo
    de fora, a prova falha em vez de passar calada.
    """
    fn = _funcao(modulo, funcao)
    locais = {
        alvo.id: no.value
        for no in ast.walk(fn)
        if isinstance(no, ast.Assign)
        for alvo in no.targets
        if isinstance(alvo, ast.Name)
    }
    valores = []
    for no in ast.walk(fn):
        if isinstance(no, ast.For | ast.AsyncFor) and variavel in {
            n.id for n in ast.walk(no.target) if isinstance(n, ast.Name)
        }:
            it = no.iter
            if isinstance(it, ast.Name):
                it = locais[it.id]
            valores.append(ast.literal_eval(it))
    return valores


# ---------------------------------------------------------------------------------------------
# gateway/audit_postgres.py (INSERT_SQL) e gateway/audit_anchor_verify.py (version_sql)
# ---------------------------------------------------------------------------------------------


def test_audit_postgres_insert_sql_so_interpola_colunas_literais() -> None:
    from maezo.gateway import audit_postgres

    assert all(SIMPLES.fullmatch(col) for col in audit_postgres._COLUMNS)
    n = len(audit_postgres._COLUMNS)
    assert audit_postgres.INSERT_SQL == (
        "INSERT INTO audit_chain (timestamp, tenant_id, agent_id, agent_version, action, decision, "
        "input_hash, decision_basis, dmn_versions, model_id, prompt_version, record_hash, "
        "prev_record_hash) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, $11, $12, $13)"
    )
    assert n == 13


@pytest.mark.parametrize("carga", INJECOES)
def test_schema_for_tenant_recusa_injecao(carga: str) -> None:
    """`schema_for_tenant` e' a fonte do identificador em audit_anchor_verify.version_sql."""
    from maezo.gateway.audit_postgres import schema_for_tenant

    with pytest.raises(ValueError, match="not a valid schema identifier"):
        schema_for_tenant(carga)


@pytest.mark.asyncio
async def test_audit_anchor_verify_recusa_tenant_invalido_antes_de_conectar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maezo.gateway import audit_anchor_verify

    async def _nunca(*_a: object, **_k: object) -> None:
        raise AssertionError("conectou ao banco com tenant invalido")

    monkeypatch.setattr(audit_anchor_verify.asyncpg, "connect", _nunca)
    source = audit_anchor_verify.PostgresChainRecordSource("postgresql://u@h/db")
    with pytest.raises(ValueError, match="not a valid schema identifier"):
        await source.read_chain('amh"; DROP TABLE audit_chain; --')


# ---------------------------------------------------------------------------------------------
# gateway/capabilities/durability/postgres.py (observe_journey, recover)
# ---------------------------------------------------------------------------------------------


_DURABILITY: Final[str] = "gateway/capabilities/durability/postgres.py"


def test_durability_observe_journey_tabelas_e_colunas_sao_tupla_literal() -> None:
    assert iteraveis_literais_dos_lacos(_DURABILITY, "observe_journey", "table") == [
        (
            ("v21_capability_command", "command_ref"),
            ("v21_external_wait", "wait_ref"),
            ("v21_journal_outbox", "outbox_ref"),
        )
    ]


def test_durability_recover_entidades_sao_tupla_literal() -> None:
    (entidades,) = iteraveis_literais_dos_lacos(_DURABILITY, "recover", "table")
    assert [(tabela, coluna) for _, tabela, coluna, _ in entidades] == [
        ("v21_capability_command", "command_ref"),
        ("v21_journal_outbox", "outbox_ref"),
        ("v21_external_wait", "wait_ref"),
    ]
    assert all(SIMPLES.fullmatch(x) for _, t, c, _ in entidades for x in (t, c))


# ---------------------------------------------------------------------------------------------
# gateway/communications/admission.py e gateway/document_requests/admission.py (head + lock)
# ---------------------------------------------------------------------------------------------


class _ConexaoProibida:
    async def execute(self, *_a: object, **_k: object) -> None:
        raise AssertionError("SQL executado com modo de lock fora do mapa fechado")


def test_communications_head_sql_e_mapa_literal_fechado() -> None:
    from maezo.gateway.communications import admission

    base = (
        "SELECT revision,publication_ref,payload FROM portal_communication.authority_head "
        "WHERE tenant=:tenant AND environment=:environment AND access_digest=:access"
    )
    assert {
        "": base,
        " FOR SHARE": base + " FOR SHARE",
        " FOR UPDATE": base + " FOR UPDATE",
    } == admission._HEAD_SQL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lock", [" FOR SHARE; DROP TABLE t", "FOR UPDATE", " for update", " FOR NO KEY UPDATE"]
)
async def test_communications_head_recusa_lock_fora_do_mapa(lock: str) -> None:
    from maezo.gateway.communications import admission
    from maezo.gateway.external_cases.models import ExternalCaseError

    with pytest.raises(ExternalCaseError):
        await admission.head(_ConexaoProibida(), object(), lock=lock)  # type: ignore[arg-type]


def test_document_requests_head_sql_e_mapa_literal_fechado() -> None:
    from maezo.gateway.document_requests import admission

    base = (
        "SELECT revision,publication_ref,payload FROM portal_document_request.authority_head "
        "WHERE tenant=:tenant AND environment=:environment AND access_digest=:access"
    )
    assert {" FOR SHARE": base + " FOR SHARE", " FOR UPDATE": base + " FOR UPDATE"} == admission._HEAD_SQL


@pytest.mark.asyncio
@pytest.mark.parametrize("lock", ["", " FOR SHARE; DROP TABLE t", " for share", " FOR KEY SHARE"])
async def test_document_requests_head_recusa_lock_fora_do_mapa(lock: str) -> None:
    from maezo.gateway.document_requests import admission
    from maezo.gateway.external_cases.models import ExternalCaseError

    with pytest.raises(ExternalCaseError):
        await admission.head(_ConexaoProibida(), object(), lock=lock)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------
# Validadores ancorados de identificador (schema interpolado como `"<schema>".<objeto>`)
# ---------------------------------------------------------------------------------------------


def _recusa_todas(tipo: Any) -> None:
    from pydantic import TypeAdapter, ValidationError

    adaptador = TypeAdapter(tipo)
    for carga in INJECOES:
        with pytest.raises(ValidationError):
            adaptador.validate_python(carga)
    assert adaptador.validate_python("schema_ok") == "schema_ok"


def _campo_validado(modelo: Any, campo: str) -> None:
    """O campo do modelo carrega a mesma regex ancorada; recusa cada carga de `INJECOES`."""
    padroes = [getattr(m, "pattern", None) for m in modelo.model_fields[campo].metadata]
    assert any(p and p.startswith("^") and p.endswith("$") for p in padroes), padroes
    from typing import Annotated

    from pydantic import StringConstraints

    (padrao,) = [p for p in padroes if p]
    _recusa_todas(Annotated[str, StringConstraints(strict=True, pattern=padrao)])


def test_provider_notice_identifier_recusa_injecao() -> None:
    from maezo.gateway.communications import provider_notice

    _recusa_todas(provider_notice.Identifier)
    _campo_validado(provider_notice.ProviderNoticeInstallation, "schema_name")


# ---------------------------------------------------------------------------------------------
# gateway/commercial_legitimacy_screen.py (_SELECT_LATEST_TEMPLATE.format(axis=...))
# ---------------------------------------------------------------------------------------------


def argumentos_literais_de_format(modulo: str, template: str) -> list[dict[str, Any]]:
    """Kwargs de cada `<template>.format(...)` do modulo; `literal_eval` falha se nao for literal."""
    tree = ast.parse((_SRC / modulo).read_bytes())
    chamadas = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "format"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == template
    ]
    for c in chamadas:
        assert not c.args, "format posicional nao e' admitido"
    return [{k.arg: ast.literal_eval(k.value) for k in c.keywords} for c in chamadas]


def test_legitimacy_screen_axis_e_coluna_literal() -> None:
    assert argumentos_literais_de_format(
        "gateway/commercial_legitimacy_screen.py", "_SELECT_LATEST_TEMPLATE"
    ) == [{"axis": "opportunity_ref"}, {"axis": "legal_entity_ref"}]


# ---------------------------------------------------------------------------------------------
# gateway/documents/authority.py (`where["clause"]` no candidato de heads)
# ---------------------------------------------------------------------------------------------


def _so_literais(no: ast.expr) -> bool:
    if isinstance(no, ast.Constant):
        return isinstance(no.value, str)
    if isinstance(no, ast.BinOp) and isinstance(no.op, ast.Add):
        return _so_literais(no.left) and _so_literais(no.right)
    return False


def test_documents_authority_clausulas_sao_literais_de_codigo() -> None:
    """Toda `"clause"` passada a `_candidate_heads(_in)` e' texto literal; valores so' em `params`.

    O unico nome admitido e' o `clause` local de `_policy`, cujas atribuicoes (`=` e `+=`) tambem
    sao literais — a prova percorre cada uma.
    """
    tree = ast.parse((_SRC / "gateway/documents/authority.py").read_bytes())
    valores = [
        v
        for d in ast.walk(tree)
        if isinstance(d, ast.Dict)
        for k, v in zip(d.keys, d.values, strict=True)
        if isinstance(k, ast.Constant) and k.value == "clause"
    ]
    assert len(valores) == 4
    nomes = {v.id for v in valores if isinstance(v, ast.Name)}
    assert nomes == {"clause"}
    assert all(_so_literais(v) for v in valores if not isinstance(v, ast.Name))
    policy = _funcao("gateway/documents/authority.py", "_policy")
    atribuicoes = [
        n.value
        for n in ast.walk(policy)
        if isinstance(n, ast.Assign | ast.AugAssign)
        and any(
            isinstance(t, ast.Name) and t.id == "clause"
            for t in getattr(n, "targets", [getattr(n, "target", None)])
        )
    ]
    assert len(atribuicoes) == 2
    assert all(_so_literais(a) for a in atribuicoes)


# ---------------------------------------------------------------------------------------------
# NativeAuthReader.table() — fonte de gateway/document_requests/production.py, documents/authority.py
# e gateway/intake/native_authority.py
# ---------------------------------------------------------------------------------------------


def _leitor_falso(schema: str, nomes: tuple[str, ...]) -> Any:
    from types import SimpleNamespace

    relacoes = tuple(SimpleNamespace(name=n) for n in nomes)
    return SimpleNamespace(binding=SimpleNamespace(schema_name=schema, relations=relacoes))


def test_native_auth_reader_table_so_admite_relacoes_do_binding() -> None:
    from maezo.gateway.intake.native_authority import (
        AuthUnavailableError,
        NativeAuthReader,
        NativeDatabaseBinding,
    )

    leitor = _leitor_falso("auth_schema", ("mzo_auth_input_head", "mzo_auth_input_version"))
    assert NativeAuthReader.table(leitor, "mzo_auth_input_head") == '"auth_schema"."mzo_auth_input_head"'
    for nome in ("mzo_auth_trust", 'mzo_auth_input_head"; DROP TABLE t; --', "MZO_AUTH_INPUT_HEAD", ""):
        with pytest.raises(AuthUnavailableError):
            NativeAuthReader.table(leitor, nome)
    _campo_validado(NativeDatabaseBinding, "schema_name")


# ---------------------------------------------------------------------------------------------
# gateway/external_cases/publisher.py (`+ kind`) e gateway/external_cases/postgres.py (`+ operation`)
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["case(); DROP TABLE t; --", "authorization", "CASE", ""])
async def test_external_publisher_capture_recusa_kind_fora_de_kind(kind: str) -> None:
    from types import SimpleNamespace

    from maezo.gateway.external_cases.models import ExternalCaseError
    from maezo.gateway.external_cases.publisher import PostgresExternalCasePublicationSource

    class _Motor:
        async def connect(self) -> None:
            raise AssertionError("conectou com kind fora da allowlist")

    fonte = SimpleNamespace(engine=_Motor(), seconds=1.0)
    with pytest.raises(ExternalCaseError):
        await PostgresExternalCasePublicationSource.capture(fonte, kind, "pub", "claimant")  # type: ignore[arg-type]


def test_external_publisher_snapshot_so_nasce_em_capture_com_kind_literal() -> None:
    """`self.snapshot.kind` (persist/transition) so' vem do `ExternalSourceSnapshot` montado em capture()."""
    from typing import get_args

    from maezo.gateway.external_cases import publisher

    assert get_args(publisher.Kind) == ("case", "checkpoint")
    tree = ast.parse((_SRC / "gateway/external_cases/publisher.py").read_bytes())
    construcoes = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "ExternalSourceSnapshot"
    ]
    capture = _funcao("gateway/external_cases/publisher.py", "capture")
    assert len(construcoes) == 1
    assert capture.lineno <= construcoes[0].lineno <= (capture.end_lineno or 0)
    primeiro = construcoes[0].args[0]
    assert isinstance(primeiro, ast.Name) and primeiro.id == "kind"


def test_external_postgres_operation_e_um_de_tres_nomes_literais() -> None:
    tree = ast.parse((_SRC / "gateway/external_cases/postgres.py").read_bytes())
    atribuicoes = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "operation" for t in n.targets)
    ]
    assert len(atribuicoes) == 1

    def folhas(no: ast.expr) -> list[Any]:
        if isinstance(no, ast.IfExp):
            return folhas(no.body) + folhas(no.orelse)
        return [ast.literal_eval(no)]

    assert folhas(atribuicoes[0]) == [
        "accept_external_scope_checkpoint",
        "revoke_external_case_source",
        "accept_external_case_source",
    ]


# ---------------------------------------------------------------------------------------------
# gateway/human/decision_binding.py (`table()` em RELATIONS, `suffix`, `binding_columns`)
# ---------------------------------------------------------------------------------------------


def test_decision_binding_relations_e_table_fechados() -> None:
    from types import SimpleNamespace

    from maezo.gateway.human import decision_binding as m

    assert all(SIMPLES.fullmatch(r) for r in m.RELATIONS + m.NATIVE_COHORT_RELATIONS)
    _campo_validado(m.BindingDatabase, "schema_name")
    falso = SimpleNamespace(database=SimpleNamespace(schema_name="hb"), native_cohort_read=None)
    assert m.BindingConnection.table(falso, "mzo_human_tenant") == '"hb"."mzo_human_tenant"'
    for nome in ("mzo_human_consumer_head", 'mzo_human_tenant"; DROP TABLE t; --', "pg_authid", ""):
        with pytest.raises(m.BindingUnavailableError):
            m.BindingConnection.table(falso, nome)
    # A coorte nativa so' amplia a allowlist com NATIVE_COHORT_RELATIONS, nunca com nome livre.
    com_coorte = SimpleNamespace(database=falso.database, native_cohort_read=object())
    assert (
        m.BindingConnection.table(com_coorte, "mzo_human_consumer_head") == '"hb"."mzo_human_consumer_head"'
    )
    with pytest.raises(m.BindingUnavailableError):
        m.BindingConnection.table(com_coorte, "pg_authid")


def test_decision_binding_suffix_e_literal() -> None:
    fn = _funcao("gateway/human/decision_binding.py", "revision")
    (atrib,) = [
        n.value
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "suffix" for t in n.targets)
    ]
    assert isinstance(atrib, ast.IfExp)
    assert {ast.literal_eval(atrib.body), ast.literal_eval(atrib.orelse)} == {" FOR UPDATE", ""}


def test_decision_binding_colunas_do_insert_sao_kwargs_literais() -> None:
    fn = _funcao("gateway/human/decision_binding.py", "binding_columns")
    (ret,) = [n.value for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert isinstance(ret, ast.Call) and isinstance(ret.func, ast.Name) and ret.func.id == "dict"
    assert not ret.args and all(k.arg is not None for k in ret.keywords)
    colunas = [k.arg for k in ret.keywords]
    assert len(colunas) == 18 and all(SIMPLES.fullmatch(c or "") for c in colunas)


# ---------------------------------------------------------------------------------------------
# gateway/human/task_publication_source.py (`self._native` / `self._act` via `_schema()`)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("carga", INJECOES)
def test_task_publication_schema_recusa_injecao(carga: str) -> None:
    from maezo.gateway.human import task_publication_source as m
    from maezo.gateway.human.read_credentials import ReadRefusalError

    with pytest.raises(ReadRefusalError) as erro:
        m._schema(carga)
    assert erro.value.reason == "configuration_invalid"
    assert m._schema("maezo_native") == "maezo_native"


@pytest.mark.asyncio
async def test_task_publication_live_tasks_liga_o_limit() -> None:
    from maezo.gateway.human import task_publication_source as m

    capturado: dict[str, Any] = {}

    async def _rows(sql: str, **params: Any) -> list[Any]:
        capturado.update(sql=sql, params=params)
        return []

    fonte = m.NativeTaskSource.__new__(m.NativeTaskSource)
    fonte.tenant, fonte._act = "t", "cibseven"
    fonte._rows = _rows  # type: ignore[method-assign]
    assert await fonte.live_tasks(["k"]) == []
    assert capturado["sql"].endswith("LIMIT :limit") and capturado["params"]["limit"] == m.MAX_TASKS + 1


# ---------------------------------------------------------------------------------------------
# gateway/human/assignment_receipt.py e assignment_transport.py (`SET LOCAL search_path`)
# ---------------------------------------------------------------------------------------------


def _search_path_de_schema_for_tenant(modulo: str, funcao: str) -> None:
    """O `{schema}` do `SET LOCAL search_path` e' sempre `schema = schema_for_tenant(...)` local."""
    fn = _funcao(modulo, funcao)
    atribs = [
        n.value
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "schema" for t in n.targets)
    ]
    assert len(atribs) == 1
    chamada = atribs[0]
    assert isinstance(chamada, ast.Call) and isinstance(chamada.func, ast.Name)
    assert chamada.func.id == "schema_for_tenant"


def test_assignment_receipt_search_path_vem_de_schema_for_tenant() -> None:
    _search_path_de_schema_for_tenant("gateway/human/assignment_receipt.py", "_current_source")


def test_assignment_transport_search_path_vem_de_schema_for_tenant() -> None:
    _search_path_de_schema_for_tenant("gateway/human/assignment_transport.py", "_active")


# ---------------------------------------------------------------------------------------------
# gateway/human/{provider,vendor}_membership_administration_postgres.py
# ---------------------------------------------------------------------------------------------


def test_provider_membership_schema_e_identifier_ancorado() -> None:
    from maezo.gateway.human import provider_membership_administration_postgres as m

    _recusa_todas(m.Identifier)
    _campo_validado(m.AdministrationSourceDescriptor, "schema_name")


def test_vendor_membership_apply_change_e_constante_qualificada() -> None:
    from maezo.gateway.human import vendor_membership_administration_postgres as m

    assert m.APPLY_CHANGE == "portal_auth.apply_change"
    tree = ast.parse((_SRC / "gateway/human/vendor_membership_administration_postgres.py").read_bytes())
    atribs = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign | ast.AnnAssign)
        and any(
            isinstance(t, ast.Name) and t.id == "APPLY_CHANGE"
            for t in getattr(n, "targets", [getattr(n, "target", None)])
        )
    ]
    assert len(atribs) == 1 and isinstance(atribs[0].value, ast.Constant)


# ---------------------------------------------------------------------------------------------
# gateway/intake/native_authority.py (`self.table()` sobre `binding.relations`)
# ---------------------------------------------------------------------------------------------


def _binding_auth(nomes: tuple[str, ...]) -> Any:
    from types import SimpleNamespace

    relacoes = tuple(SimpleNamespace(name=n, schema_name="auth", owner="dono") for n in nomes)
    return SimpleNamespace(relations=relacoes, schema_name="auth", owner_role="dono", reader_role="leitor")


def test_native_auth_reader_so_aceita_as_cinco_relacoes_mzo_auth() -> None:
    from types import SimpleNamespace

    from maezo.gateway.intake.native_authority import AuthUnavailableError, NativeAuthReader

    motor = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    cinco = tuple(
        "mzo_auth_" + n for n in ("installation", "trust", "revoked_key", "input_head", "input_version")
    )
    leitor = NativeAuthReader(motor, _binding_auth(cinco))  # type: ignore[arg-type]
    assert leitor.table("mzo_auth_trust") == '"auth"."mzo_auth_trust"'
    for intrusa in ('x"; DROP TABLE t; --', "pg_authid", "mzo_auth_extra"):
        with pytest.raises(AuthUnavailableError):
            NativeAuthReader(motor, _binding_auth((*cinco[:4], intrusa)))  # type: ignore[arg-type]
        with pytest.raises(AuthUnavailableError):
            NativeAuthReader(motor, _binding_auth((*cinco, intrusa)))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------
# gateway/intake/native_source_lifecycle.py (`self.table()` em portal_sessions/portal_memberships)
# ---------------------------------------------------------------------------------------------


def test_auth_source_lifecycle_table_so_admite_dois_nomes() -> None:
    from types import SimpleNamespace

    from maezo.gateway.intake import native_source_lifecycle as m

    falso = SimpleNamespace(binding=SimpleNamespace(source_schema_name="portal_src"))
    assert m.PostgresAuthSourceLifecycle.table(falso, "portal_sessions") == '"portal_src"."portal_sessions"'
    assert (
        m.PostgresAuthSourceLifecycle.table(falso, "portal_memberships")
        == '"portal_src"."portal_memberships"'
    )
    for nome in ("portal_auth", 'portal_sessions"; DROP TABLE t; --', "PORTAL_SESSIONS", ""):
        with pytest.raises(m.AuthUnavailableError):
            m.PostgresAuthSourceLifecycle.table(falso, nome)
    _campo_validado(m.IdentitySourceBinding, "source_schema_name")


# ---------------------------------------------------------------------------------------------
# gateway/intake/recovery_store.py (`_CANDIDATES` + `boundary`)
# ---------------------------------------------------------------------------------------------


def test_recovery_store_candidates_e_boundary_sao_literais() -> None:
    from maezo.gateway.intake import recovery_store as m

    assert isinstance(m._CANDIDATES, str) and ":" in m._CANDIDATES
    fn = _funcao("gateway/intake/recovery_store.py", "scan")
    atribs = [
        n.value
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "boundary" for t in n.targets)
    ]
    assert len(atribs) == 2 and all(_so_literais(a) for a in atribs)
    tree = ast.parse((_SRC / "gateway/intake/recovery_store.py").read_bytes())
    (atrib,) = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_CANDIDATES" for t in n.targets)
    ]
    assert _so_literais(atrib.value)


# ---------------------------------------------------------------------------------------------
# gateway/staff_cases/case_issuer_sources.py (schema via SCHEMA/is_native_schema; where literal)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("carga", INJECOES)
def test_case_issuer_sources_recusam_schema_injetado_antes_de_io(carga: str) -> None:
    from types import SimpleNamespace

    from maezo.gateway.staff_cases import case_issuer_sources as m

    motor: Any = SimpleNamespace()
    with pytest.raises(m.CaseIssuerError):
        m.PostgresStaffGranteeSource(motor, tenant="t", schema=carga)
    with pytest.raises(m.CaseIssuerError):
        m.AuthClaimAnchor(SimpleNamespace(), motor, tenant="t", native_schema=carga)  # type: ignore[arg-type]
    with pytest.raises(m.CaseIssuerError):
        m.PostgresIssuerLedger(motor, scope=SimpleNamespace(), policy_ref="p", native_schema=carga)  # type: ignore[arg-type]


def test_case_issuer_ledger_where_e_literal() -> None:
    modulo = "gateway/staff_cases/case_issuer_sources.py"
    tree = ast.parse((_SRC / modulo).read_bytes())
    ledger = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PostgresIssuerLedger")
    wheres = [
        n.value
        for n in ast.walk(ledger)
        if isinstance(n, ast.Assign)
        and any(
            (isinstance(t, ast.Attribute) and t.attr == "where")
            or (isinstance(t, ast.Name) and t.id == "scope_where")
            for t in n.targets
        )
    ]
    assert len(wheres) == 2 and all(_so_literais(w) for w in wheres)
    tabela = [
        n.value
        for n in ast.walk(ledger)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "table" for t in n.targets)
    ]
    assert len(tabela) == 1 and isinstance(tabela[0], ast.JoinedStr)
    nomes = {
        v.value.id
        for v in tabela[0].values
        if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name)
    }
    assert nomes == {"native_schema"}


def test_case_issuer_timeout_vai_ligado() -> None:
    from maezo.gateway.staff_cases import case_issuer_sources as m

    assert m._SET_TIMEOUT == "SELECT set_config('statement_timeout', :timeout, true)"


# ---------------------------------------------------------------------------------------------
# gateway/staff_cases/postgres.py (`read_sql` com o schema pinado do manifesto)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("carga", INJECOES)
def test_is_native_schema_recusa_injecao(carga: str) -> None:
    """`is_native_schema` e' a fonte do schema de `NativeMembershipSource.read_sql` (ADR-0060 D3)."""
    from maezo.gateway.staff_cases.production_config import is_native_schema

    assert is_native_schema(carga) is False
    assert is_native_schema("maezo_native") is True


# ---------------------------------------------------------------------------------------------
# Constantes de modulo usadas como lista de colunas (INSERT com placeholders gerados)
# ---------------------------------------------------------------------------------------------


def atribuicao_literal_de_modulo(modulo: str, nome: str) -> Any:
    """Valor literal da UNICA atribuicao de topo a `nome` (falha se computado ou reatribuido)."""
    tree = ast.parse((_SRC / modulo).read_bytes())
    atribs = [
        n.value
        for n in tree.body
        if isinstance(n, ast.Assign | ast.AnnAssign)
        and n.value is not None
        and any(
            isinstance(t, ast.Name) and t.id == nome
            for t in getattr(n, "targets", [getattr(n, "target", None)])
        )
    ]
    assert len(atribs) == 1, (modulo, nome, len(atribs))
    return ast.literal_eval(atribs[0])


def test_amh_inbox_insert_so_interpola_colunas_literais() -> None:
    from maezo.platform.integrations import amh_inbox

    colunas = atribuicao_literal_de_modulo("platform/integrations/amh_inbox.py", "_INSERT_COLUMNS")
    assert colunas == amh_inbox._INSERT_COLUMNS and all(SIMPLES.fullmatch(c) for c in colunas)
    n = len(colunas)
    assert (
        f"INSERT INTO amh_inbox ({', '.join(colunas)}) "
        f"VALUES ({', '.join(f'${i}' for i in range(1, n + 1))}) "
        "ON CONFLICT (contract_manifest_digest, event_id) DO NOTHING "
        "RETURNING status, redelivery_count"
    ) == amh_inbox._INSERT_SQL
