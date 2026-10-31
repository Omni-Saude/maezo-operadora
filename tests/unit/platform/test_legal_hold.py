"""R-044 (F-062, ADR-0020 item 6): registro de legal-hold + predicado fail-closed.

Cobre: (1) carregador fail-closed em todo modo de falha; (2) predicado com tres desfechos, onde
so LIVRE nao bloqueia; (3) guarda negativa de PHI (ADR-0006): o pseudonimo do titular nunca vai
para log, mensagem de erro ou registro de auditoria. Fixtures 100% sinteticas.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest
import yaml
from structlog.testing import capture_logs

from maezo.platform.lifecycle import legal_hold as lh
from maezo.platform.lifecycle.legal_hold import (
    REASON_FILE_NOT_FOUND,
    REASON_INVALID_ENCODING,
    REASON_INVALID_SCHEMA,
    REASON_INVALID_YAML,
    REASON_PATH_NOT_SET,
    REASON_UNRATIFIED_TEMPLATE,
    REASON_UNREADABLE,
    REGISTRY_PATH_ENV,
    LegalHoldDecision,
    LegalHoldOutcome,
    LegalHoldRegistry,
    LegalHoldRegistryUnavailableError,
    evaluate_legal_hold,
    load_legal_hold_registry,
)

_PSEUDO = "pseudo-sintetico-7f3a9c"  # pseudonimo sintetico, marcador de vazamento nos testes
_HOJE = date(2026, 10, 30)

_REGISTRO = f"""
schema_version: 1
holds:
  - hold_id: LH-2026-0001
    tenant_id: tenant-a
    titular_pseudo_id: {_PSEUDO}
    colocado_em: 2026-09-01
  - hold_id: LH-2026-0002
    tenant_id: tenant-a
    titular_pseudo_id: pseudo-levantado
    colocado_em: 2026-01-01
    levantado_em: 2026-06-30
  - hold_id: LH-2026-0003
    tenant_id: tenant-a
    titular_pseudo_id: pseudo-levanta-no-futuro
    colocado_em: 2026-01-01
    levantado_em: 2026-12-31
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "legal-holds.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _loader(path: Path):  # type: ignore[no-untyped-def]
    return lambda: load_legal_hold_registry(path)


# ---------------------------------------------------------------------------------------------
# 1. Carregador
# ---------------------------------------------------------------------------------------------


def test_carrega_registro_valido_com_digest_dos_bytes(tmp_path: Path) -> None:
    path = _write(tmp_path, _REGISTRO)
    registry = load_legal_hold_registry(path)
    assert [h.hold_id for h in registry.holds] == ["LH-2026-0001", "LH-2026-0002", "LH-2026-0003"]
    assert registry.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_lista_vazia_de_holds_e_um_registro_valido(tmp_path: Path) -> None:
    registry = load_legal_hold_registry(_write(tmp_path, "schema_version: 1\nholds: []\n"))
    assert registry.holds == ()


def test_env_resolve_o_caminho(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REGISTRY_PATH_ENV, str(_write(tmp_path, _REGISTRO)))
    assert len(load_legal_hold_registry().holds) == 3


def test_sem_caminho_recusa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REGISTRY_PATH_ENV, raising=False)
    with pytest.raises(LegalHoldRegistryUnavailableError) as exc:
        load_legal_hold_registry()
    assert exc.value.reason == REASON_PATH_NOT_SET


def test_arquivo_ausente_recusa(tmp_path: Path) -> None:
    with pytest.raises(LegalHoldRegistryUnavailableError) as exc:
        load_legal_hold_registry(tmp_path / "nao-existe.yaml")
    assert exc.value.reason == REASON_FILE_NOT_FOUND


def test_codificacao_invalida_recusa(tmp_path: Path) -> None:
    path = tmp_path / "r.yaml"
    path.write_bytes("schema_version: 1\nholds: []\n# jur\xeddico\n".encode("cp1252"))
    with pytest.raises(LegalHoldRegistryUnavailableError) as exc:
        load_legal_hold_registry(path)
    assert exc.value.reason == REASON_INVALID_ENCODING


def test_yaml_malformado_recusa_sem_ecoar_o_conteudo(tmp_path: Path) -> None:
    path = _write(tmp_path, f"schema_version: 1\nholds: [\n  {{titular_pseudo_id: {_PSEUDO}\n")
    with pytest.raises(LegalHoldRegistryUnavailableError) as exc:
        load_legal_hold_registry(path)
    assert exc.value.reason == REASON_INVALID_YAML
    assert _PSEUDO not in str(exc.value)


def test_modelo_nao_ratificado_recusa(tmp_path: Path) -> None:
    path = _write(tmp_path, "unratified: true\nschema_version: 1\nholds: []\n")
    with pytest.raises(LegalHoldRegistryUnavailableError) as exc:
        load_legal_hold_registry(path)
    assert exc.value.reason == REASON_UNRATIFIED_TEMPLATE


_HOLD_OK = "hold_id: LH-1\n    tenant_id: t\n    titular_pseudo_id: p\n    colocado_em: 2026-01-01"


@pytest.mark.parametrize(
    "texto",
    [
        "- 1\n- 2\n",  # raiz nao e mapeamento
        "holds: []\n",  # sem schema_version
        "schema_version: 2\nholds: []\n",  # versao desconhecida
        "schema_version: true\nholds: []\n",  # bool nao e versao
        "schema_version: '1'\nholds: []\n",  # string nao e versao
        "schema_version: 1\n",  # holds ausente
        "schema_version: 1\nholds: {}\n",  # holds nao e lista
        "schema_version: 1\nescopo_global: true\nholds: []\n",  # chave de topo desconhecida
        "schema_version: 1\nholds:\n  - nao-e-mapa\n",
        f"schema_version: 1\nholds:\n  - {_HOLD_OK}\n    escopo: caso\n",  # escopo nao ratificado
        "schema_version: 1\nholds:\n  - hold_id: LH-1\n    tenant_id: t\n    colocado_em: 2026-01-01\n",
        f"schema_version: 1\nholds:\n  - {_HOLD_OK.replace('LH-1', 'LH 1')}\n",  # hold_id com espaco
        f"schema_version: 1\nholds:\n  - {_HOLD_OK.replace('tenant_id: t', 'tenant_id: ""')}\n",
        f"schema_version: 1\nholds:\n  - {_HOLD_OK.replace('pseudo_id: p', 'pseudo_id: 7')}\n",
        f"schema_version: 1\nholds:\n  - {_HOLD_OK.replace('2026-01-01', 'ontem')}\n",
        f"schema_version: 1\nholds:\n  - {_HOLD_OK}\n    levantado_em: 2025-12-31\n",  # antes de colocado
        f"schema_version: 1\nholds:\n  - {_HOLD_OK}\n  - {_HOLD_OK}\n",  # hold_id duplicado
    ],
)
def test_esquema_invalido_recusa(tmp_path: Path, texto: str) -> None:
    with pytest.raises(LegalHoldRegistryUnavailableError) as exc:
        load_legal_hold_registry(_write(tmp_path, texto))
    assert exc.value.reason == REASON_INVALID_SCHEMA


# ---------------------------------------------------------------------------------------------
# 2. Predicado
# ---------------------------------------------------------------------------------------------


def test_hold_ativo_retem(tmp_path: Path) -> None:
    path = _write(tmp_path, _REGISTRO)
    decision = evaluate_legal_hold(
        tenant_id="tenant-a", titular_pseudo_id=_PSEUDO, loader=_loader(path), today=_HOJE
    )
    assert decision.outcome is LegalHoldOutcome.RETIDO
    assert decision.bloqueia_eliminacao is True
    assert decision.hold_ids == ("LH-2026-0001",)
    assert decision.registro_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_hold_com_levantamento_futuro_ainda_retem(tmp_path: Path) -> None:
    decision = evaluate_legal_hold(
        tenant_id="tenant-a",
        titular_pseudo_id="pseudo-levanta-no-futuro",
        loader=_loader(_write(tmp_path, _REGISTRO)),
        today=_HOJE,
    )
    assert decision.outcome is LegalHoldOutcome.RETIDO


def test_hold_levantado_no_dia_libera(tmp_path: Path) -> None:
    decision = evaluate_legal_hold(
        tenant_id="tenant-a",
        titular_pseudo_id="pseudo-levantado",
        loader=_loader(_write(tmp_path, _REGISTRO)),
        today=date(2026, 6, 30),
    )
    assert decision.outcome is LegalHoldOutcome.LIVRE
    assert decision.bloqueia_eliminacao is False


def test_sem_hold_para_o_titular_libera_so_para_o_proximo_gate(tmp_path: Path) -> None:
    decision = evaluate_legal_hold(
        tenant_id="tenant-a",
        titular_pseudo_id="outro-pseudo",
        loader=_loader(_write(tmp_path, _REGISTRO)),
        today=_HOJE,
    )
    assert decision.outcome is LegalHoldOutcome.LIVRE
    assert decision.motivo == "sem_hold_ativo"


def test_hold_de_outro_tenant_nao_cobre(tmp_path: Path) -> None:
    decision = evaluate_legal_hold(
        tenant_id="tenant-b",
        titular_pseudo_id=_PSEUDO,
        loader=_loader(_write(tmp_path, _REGISTRO)),
        today=_HOJE,
    )
    assert decision.outcome is LegalHoldOutcome.LIVRE


def test_registro_indisponivel_e_indeterminado_e_bloqueia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REGISTRY_PATH_ENV, raising=False)
    decision = evaluate_legal_hold(tenant_id="tenant-a", titular_pseudo_id=_PSEUDO)
    assert decision.outcome is LegalHoldOutcome.INDETERMINADO
    assert decision.motivo == "registro_indisponivel_path_not_set"
    assert decision.bloqueia_eliminacao is True


def test_registro_invalido_e_indeterminado(tmp_path: Path) -> None:
    path = _write(tmp_path, "schema_version: 1\nholds:\n  - hold_id: LH-1\n    escopo: caso\n")
    decision = evaluate_legal_hold(tenant_id="tenant-a", titular_pseudo_id=_PSEUDO, loader=_loader(path))
    assert decision.outcome is LegalHoldOutcome.INDETERMINADO
    assert decision.motivo == "registro_indisponivel_invalid_schema"


def test_erro_inesperado_do_registro_e_indeterminado_nunca_livre() -> None:
    def _explode() -> LegalHoldRegistry:
        raise KeyError(_PSEUDO)

    with capture_logs() as logs:
        decision = evaluate_legal_hold(tenant_id="tenant-a", titular_pseudo_id=_PSEUDO, loader=_explode)
    assert decision.outcome is LegalHoldOutcome.INDETERMINADO
    assert decision.motivo == "erro_inesperado"
    assert _PSEUDO not in repr(logs)


@pytest.mark.parametrize(
    ("tenant", "titular"),
    [
        ("tenant-a", None),
        ("tenant-a", ""),
        ("tenant-a", "   "),
        ("tenant-a", 123),
        (None, _PSEUDO),
        ("", _PSEUDO),
    ],
)
def test_titular_ou_tenant_nao_identificado_e_indeterminado_sem_consultar(
    tenant: object, titular: object
) -> None:
    def _nunca() -> LegalHoldRegistry:
        raise AssertionError("o registro nao deve ser consultado sem titular/tenant")

    decision = evaluate_legal_hold(tenant_id=tenant, titular_pseudo_id=titular, loader=_nunca)
    assert decision.outcome is LegalHoldOutcome.INDETERMINADO
    assert decision.motivo == "titular_nao_identificado"


@pytest.mark.parametrize("outcome", list(LegalHoldOutcome))
def test_so_livre_nao_bloqueia(outcome: LegalHoldOutcome) -> None:
    decision = LegalHoldDecision(outcome, "x")
    assert decision.bloqueia_eliminacao is (outcome is not LegalHoldOutcome.LIVRE)


def test_predicado_nao_inventa_prazo_de_retencao() -> None:
    """Nenhum periodo de retencao vive neste modulo: o fim do hold e um FATO do registro."""
    source = Path(lh.__file__).read_text(encoding="utf-8")
    assert "timedelta" not in source
    assert "relativedelta" not in source


# ---------------------------------------------------------------------------------------------
# 3. Guarda negativa de PHI (ADR-0006)
# ---------------------------------------------------------------------------------------------


def test_pseudonimo_nunca_vai_para_log_nem_para_o_registro(tmp_path: Path) -> None:
    path = _write(tmp_path, _REGISTRO)
    with capture_logs() as logs:
        retido = evaluate_legal_hold(
            tenant_id="tenant-a", titular_pseudo_id=_PSEUDO, loader=_loader(path), today=_HOJE
        )
        livre = evaluate_legal_hold(
            tenant_id="tenant-a", titular_pseudo_id="outro-pseudo", loader=_loader(path), today=_HOJE
        )
    blob = repr(logs) + repr(retido.registro()) + repr(livre.registro()) + repr(retido) + repr(livre)
    for leak in (_PSEUDO, "outro-pseudo", "pseudo-levantado"):
        assert leak not in blob
    assert any(entry["event"] == "legal_hold_predicate_evaluated" for entry in logs)
    assert retido.registro() == {
        "legal_hold_outcome": "RETIDO",
        "legal_hold_motivo": "hold_ativo",
        "legal_hold_ids": ["LH-2026-0001"],
        "legal_hold_registro_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def test_repr_do_hold_nao_exibe_o_pseudonimo(tmp_path: Path) -> None:
    registry = load_legal_hold_registry(_write(tmp_path, _REGISTRO))
    assert _PSEUDO not in repr(registry)


# ---------------------------------------------------------------------------------------------
# 4. Revisao independente do #1122 (B1-B5): endurecimento do carregador
# ---------------------------------------------------------------------------------------------

_HOLD_VALIDO = (
    "  - hold_id: LH-1\n    tenant_id: tenant-a\n"
    f"    titular_pseudo_id: {_PSEUDO}\n    colocado_em: 2026-01-01\n"
)


def _assert_recusa(tmp_path: Path, texto: str, reason: str) -> None:
    path = _write(tmp_path, texto)
    with pytest.raises(LegalHoldRegistryUnavailableError) as exc:
        load_legal_hold_registry(path)
    assert exc.value.reason == reason
    decision = evaluate_legal_hold(tenant_id="tenant-a", titular_pseudo_id=_PSEUDO, loader=_loader(path))
    assert decision.outcome is LegalHoldOutcome.INDETERMINADO


@pytest.mark.parametrize(
    "texto",
    [
        # B1: `holds` duplicado — o segundo (vazio) apagaria o hold em silencio.
        f"schema_version: 1\nholds:\n{_HOLD_VALIDO}holds: []\n",
        # B1: `titular_pseudo_id` duplicado dentro do hold.
        f"schema_version: 1\nholds:\n{_HOLD_VALIDO}    titular_pseudo_id: outro\n",
        # B1: ancora + alias.
        "schema_version: 1\nholds:\n  - &a {hold_id: LH-1, tenant_id: tenant-a, "
        f"titular_pseudo_id: {_PSEUDO}, colocado_em: 2026-01-01}}\n  - *a\n",
        # B1: tag python arbitraria nunca e construida.
        "schema_version: 1\nholds: !!python/object/apply:os.system ['true']\n",
    ],
    ids=["holds_duplicado", "titular_duplicado", "alias", "tag_python"],
)
def test_yaml_estrito_recusa_duplicata_alias_e_tag(tmp_path: Path, texto: str) -> None:
    if "!!python" not in texto:
        # YAML bem-formado: o `safe_load` padrao aceitaria — quem recusa e o carregador estrito.
        yaml.safe_load(texto)
    _assert_recusa(tmp_path, texto, REASON_INVALID_YAML)


def test_ancora_sem_alias_tambem_e_recusada(tmp_path: Path) -> None:
    _assert_recusa(tmp_path, "schema_version: &v 1\nholds: []\n", REASON_INVALID_YAML)


@pytest.mark.parametrize("valor", ["'true'", "1", "null", "sim"])
def test_unratified_com_qualquer_valor_que_nao_false_recusa(tmp_path: Path, valor: str) -> None:
    """B2: so o booleano `false` explicito nao marca o registro como nao-ratificado."""
    _assert_recusa(
        tmp_path, f"unratified: {valor}\nschema_version: 1\nholds: []\n", REASON_UNRATIFIED_TEMPLATE
    )


def test_unratified_false_explicito_e_aceito(tmp_path: Path) -> None:
    registry = load_legal_hold_registry(_write(tmp_path, "unratified: false\nschema_version: 1\nholds: []\n"))
    assert registry.holds == ()


@pytest.mark.parametrize(
    "texto",
    [
        f"schema_version: 1\n{_PSEUDO}: x\nholds: []\n",
        f"schema_version: 1\nholds:\n{_HOLD_VALIDO}    {_PSEUDO}: x\n",
    ],
    ids=["topo", "dentro_do_hold"],
)
def test_chave_desconhecida_nao_vaza_o_nome(tmp_path: Path, texto: str) -> None:
    """B3: a mensagem cita a CONTAGEM de chaves desconhecidas, nunca os nomes (podem ser PHI)."""
    path = _write(tmp_path, texto)
    with capture_logs() as logs, pytest.raises(LegalHoldRegistryUnavailableError) as exc:
        load_legal_hold_registry(path)
    assert exc.value.reason == REASON_INVALID_SCHEMA
    assert _PSEUDO not in str(exc.value)
    assert _PSEUDO not in repr(logs)


def test_symlink_para_registro_valido_e_recusado(tmp_path: Path) -> None:
    """B4: O_NOFOLLOW — um symlink nunca e seguido, mesmo para um registro valido."""
    alvo = _write(tmp_path, "schema_version: 1\nholds: []\n")
    link = tmp_path / "link.yaml"
    link.symlink_to(alvo)
    with pytest.raises(LegalHoldRegistryUnavailableError) as exc:
        load_legal_hold_registry(link)
    assert exc.value.reason == REASON_UNREADABLE
    decision = evaluate_legal_hold(tenant_id="tenant-a", titular_pseudo_id=_PSEUDO, loader=_loader(link))
    assert decision.outcome is LegalHoldOutcome.INDETERMINADO


def test_diretorio_nao_e_arquivo_regular(tmp_path: Path) -> None:
    with pytest.raises(LegalHoldRegistryUnavailableError) as exc:
        load_legal_hold_registry(tmp_path)
    assert exc.value.reason == REASON_FILE_NOT_FOUND


def test_registro_acima_do_limite_tecnico_e_recusado(tmp_path: Path) -> None:
    """B4: leitura limitada — um byte alem de `_MAX_REGISTRY_BYTES` recusa."""
    path = tmp_path / "grande.yaml"
    cabecalho = b"schema_version: 1\nholds: []\n"
    path.write_bytes(cabecalho + b"#" * (lh._MAX_REGISTRY_BYTES + 1 - len(cabecalho)))
    assert path.stat().st_size == lh._MAX_REGISTRY_BYTES + 1
    with pytest.raises(LegalHoldRegistryUnavailableError) as exc:
        load_legal_hold_registry(path)
    assert exc.value.reason == REASON_UNREADABLE
    decision = evaluate_legal_hold(tenant_id="tenant-a", titular_pseudo_id=_PSEUDO, loader=_loader(path))
    assert decision.outcome is LegalHoldOutcome.INDETERMINADO


def test_registro_exatamente_no_limite_e_aceito(tmp_path: Path) -> None:
    path = tmp_path / "limite.yaml"
    cabecalho = b"schema_version: 1\nholds: []\n"
    path.write_bytes(cabecalho + b"#" * (lh._MAX_REGISTRY_BYTES - len(cabecalho)))
    assert load_legal_hold_registry(path).holds == ()


@pytest.mark.parametrize(
    ("tenant", "titular"),
    [("tenant-a", f"{_PSEUDO} "), ("tenant-a", f" {_PSEUDO}"), ("tenant-a ", _PSEUDO)],
)
def test_entrada_com_espaco_nas_bordas_e_indeterminada(tmp_path: Path, tenant: str, titular: str) -> None:
    """B5: `'<pseudo> '` nao pode escapar do hold de `'<pseudo>'` por correspondencia exata."""
    decision = evaluate_legal_hold(
        tenant_id=tenant,
        titular_pseudo_id=titular,
        loader=_loader(_write(tmp_path, _REGISTRO)),
        today=_HOJE,
    )
    assert decision.outcome is LegalHoldOutcome.INDETERMINADO
    assert decision.motivo == "titular_nao_identificado"
