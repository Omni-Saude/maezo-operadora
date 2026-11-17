"""GTM-E-66 (E-SF-21) — gatilho de aniversário da revalidação de legitimidade coletiva.

Critérios de aceite: (1) linha nova, nunca UPDATE; (2) `would_block` só em shadow (registro,
nenhum efeito). Testes nomeados pelo plano: `test_revalidacao_aniversario_nunca_update` e
`test_revalidacao_sem_efeito_adverso` (PEP nega `contract_termination`, item hard L0).
"""

from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from maezo.gateway import commercial_legitimacy_anniversary as anniversary_module
from maezo.gateway import commercial_legitimacy_screen as screen_module
from maezo.gateway.commercial_legitimacy_anniversary import (
    ANNIVERSARY_ACTOR,
    LegitimacyAnniversaryTrigger,
    anniversary_revision,
)
from maezo.gateway.commercial_legitimacy_guard import GUARD_ACTOR, LegitimacyScreenShadow
from maezo.gateway.commercial_legitimacy_screen import LegitimacyScreenRecord, LegitimacyScreenStoreError
from maezo.gateway.pep import HARD_ACTIONS, Decision, build_pep
from tests.unit.gateway.test_commercial_legitimacy_enforcing import _enforcing
from tests.unit.gateway.test_commercial_legitimacy_enforcing import _execute as _execute_enforcing
from tests.unit.gateway.test_commercial_legitimacy_guard import (
    _criterion,
    _execute,
    _flag_off,
    _gateway,
    _receipt,
    _Store,
)
from tests.unit.test_commercial_gateway import NOW

REPO = Path(__file__).resolve().parents[3]
DUE = NOW - timedelta(days=1)


def _valid_head(**overrides: object) -> LegitimacyScreenRecord:
    fields: dict[str, object] = {
        "contract_kind": "empresario_individual",
        "criteria": (
            _criterion(criterion_id="documentos_art9", result="proven", norm_anchor="RN 557/2022, art. 9º"),
            _criterion(
                criterion_id="reconfirmacao_anual", result="proven", norm_anchor="RN 557/2022, art. 10"
            ),
        ),
        "status": "valid",
        "reconfirmation_due_at": DUE,
    }
    fields.update(overrides)
    return _receipt(**fields)


def _module_tree() -> ast.Module:
    return ast.parse(Path(anniversary_module.__file__).read_text(encoding="utf-8"))


def _code_without_docstrings() -> str:
    tree = _module_tree()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body[0].value.value = ""
    return ast.unparse(tree)


def _trigger(store: _Store, now: datetime = NOW) -> LegitimacyAnniversaryTrigger:
    return LegitimacyAnniversaryTrigger(store, tenant="tenant-a", clock=lambda: now)


@pytest.mark.asyncio
async def test_revalidacao_aniversario_nunca_update() -> None:
    head = _valid_head()
    store = _Store(head)
    saved = await _trigger(store).on_anniversary("entity-a")

    assert saved is not None
    # Linha nova da MESMA instância; a anterior fica byte-igual.
    assert (saved.screen_ref, saved.opportunity_ref) == (head.screen_ref, head.opportunity_ref)
    assert saved.screen_revision == head.screen_revision + 1
    assert store.rows[(head.tenant, head.screen_ref, head.screen_revision)] == head
    assert len(store.rows) == 2
    # Copia o recibo; não inventa critério, resultado, status nem vencimento.
    assert saved.criteria == head.criteria
    assert saved.status == head.status
    assert saved.reconfirmation_due_at == head.reconfirmation_due_at
    assert saved.created_by == ANNIVERSARY_ACTOR
    assert ANNIVERSARY_ACTOR not in {GUARD_ACTOR}
    assert saved.created_at == saved.updated_at == NOW

    # Reenvio: a cabeça já registra o aniversário — nenhuma terceira linha.
    assert await _trigger(store).on_anniversary("entity-a") is None
    assert len(store.rows) == 2 and store.save_calls == 1


def test_o_store_so_insere_e_nunca_atualiza() -> None:
    # A persistência do substrato é INSERT … ON CONFLICT DO NOTHING; nenhum UPDATE existe.
    sql = " ".join(
        str(getattr(screen_module, name)) for name in dir(screen_module) if name.endswith("_SQL")
    ).upper()
    assert "INSERT INTO PORTAL_LEGITIMACY_SCREENS" in sql
    assert "ON CONFLICT (TENANT, OPERATION, SCREEN_REF, SCREEN_REVISION) DO NOTHING" in sql
    assert re.search(r"\bUPDATE\b", sql) is None
    # No código do gatilho (fora das docstrings): nenhum UPDATE, nenhuma cópia mutante.
    assert re.search(r"\bUPDATE\b|model_copy|\.update\(", _code_without_docstrings()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "head",
    [
        _valid_head(reconfirmation_due_at=None),
        _valid_head(reconfirmation_due_at=NOW + timedelta(seconds=1)),
        _valid_head(would_block=True),
    ],
    ids=["sem_vencimento", "antes_do_vencimento", "cabeca_ja_bloquearia"],
)
async def test_fora_do_aniversario_nada_a_registrar(head: LegitimacyScreenRecord) -> None:
    store = _Store(head)
    assert await _trigger(store).on_anniversary("entity-a") is None
    assert store.save_calls == 0 and len(store.rows) == 1


@pytest.mark.asyncio
async def test_pj_sem_tela_nunca_fabrica_instancia() -> None:
    store = _Store()
    assert await _trigger(store).on_anniversary("entity-a") is None
    assert store.save_calls == 0 and store.rows == {}


@pytest.mark.asyncio
async def test_revalidacao_sem_efeito_adverso() -> None:
    # (2) would_block só em shadow: o gatilho só REGISTRA o que o enforcing decidiria.
    head = _valid_head(status="valid")
    store = _Store(head)
    saved = await _trigger(store).on_anniversary("entity-a")
    assert saved is not None and saved.would_block is True
    assert saved.status == "valid"  # não invalida o recibo
    assert saved.art39_signaled is False and saved.art39_anchor is None  # aniversário não é ingresso
    assert store.save_calls == 1  # a única escrita do gatilho

    # Nenhum efeito alcançável: o módulo só importa o substrato (sem gateway comercial,
    # comunicação, processo, worker ou efeito).
    tree = _module_tree()
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imported == {
        "__future__",
        "collections.abc",
        "datetime",
        "typing",
        "maezo.gateway.commercial_legitimacy_screen",
    }

    # Documentação do limite (o PEP é a cerca, não este módulo): rescisão é item hard L0.
    assert "contract_termination" in HARD_ACTIONS
    pep = build_pep()
    assert pep.evaluate("contract_termination", {"agent_id": ANNIVERSARY_ACTOR}) is Decision.DENY


def test_gatilho_nao_esta_montado_em_composicao_alguma() -> None:
    importers = [
        path.relative_to(REPO).as_posix()
        for path in (REPO / "src").rglob("*.py")
        if path.name != "commercial_legitimacy_anniversary.py"
        and "commercial_legitimacy_anniversary" in path.read_text(encoding="utf-8")
    ]
    assert importers == []


def test_relogio_ingenuo_recusa() -> None:
    with pytest.raises(ValueError):
        anniversary_revision(_valid_head(), now=NOW.replace(tzinfo=None))


@pytest.mark.asyncio
async def test_aniversario_nao_suprime_art39_do_guard() -> None:
    # A linha do aniversário carrega `would_block` sem o art. 39; um comando do caminho
    # coletivo depois dela ainda faz a postura shadow persistir o sinal do art. 39.
    store = _Store(_valid_head())
    assert await _trigger(store).on_anniversary("entity-a") is not None
    gateway, _, _ = _gateway(LegitimacyScreenShadow, store)
    await _execute(gateway)
    head = store.head("tenant-a", "entity-a")
    assert head is not None
    assert head.art39_signaled is True and head.created_by == GUARD_ACTOR
    assert head.screen_revision == 3


class _AnniversaryRace(_Store):
    """A cabeça é lida e, antes do save do leitor, OUTRO escritor grava a revisão N+1."""

    def __init__(self, head: LegitimacyScreenRecord, rival: LegitimacyScreenRecord) -> None:
        super().__init__(head)
        self._rival = rival

    async def latest_for_legal_entity(
        self, tenant: str, legal_entity_ref: str
    ) -> LegitimacyScreenRecord | None:
        found = await super().latest_for_legal_entity(tenant, legal_entity_ref)
        self._insert(self._rival)
        return found


@pytest.mark.asyncio
async def test_gatilho_trata_stale_revision_quando_perde_a_corrida() -> None:
    # A staff (ou o guard) gravou N+1 entre a leitura da cabeça e o save do gatilho: o
    # store recusa `revision_taken`, nada do gatilho é gravado e ele não reporta escrita.
    head = _valid_head()
    rival = _receipt(**{**head.model_dump(), "screen_revision": 2, "created_by": "staff-op21"})
    racing = _AnniversaryRace(head, rival)
    assert await _trigger(racing).on_anniversary("entity-a") is None
    assert racing.save_calls == 1
    assert racing.rows[(head.tenant, head.screen_ref, 2)] == rival
    assert len(racing.rows) == 2


@pytest.mark.asyncio
async def test_gatilho_propaga_falha_de_fonte() -> None:
    class _Down(_Store):
        async def save(self, record: LegitimacyScreenRecord) -> LegitimacyScreenRecord:
            raise LegitimacyScreenStoreError("source_unavailable")

    with pytest.raises(LegitimacyScreenStoreError) as exc:
        await _trigger(_Down(_valid_head())).on_anniversary("entity-a")
    assert exc.value.code == "source_unavailable"


def _anniversary_row(head: LegitimacyScreenRecord) -> LegitimacyScreenRecord:
    row = anniversary_revision(head, now=NOW)
    assert row is not None
    return row


@pytest.mark.asyncio
async def test_guarda_shadow_na_corrida_com_o_aniversario_nao_altera_a_resposta() -> None:
    # O guard lê N (vencido), o gatilho grava N+1 antes: o save do guard é `revision_taken`.
    # A shadow engole o defeito e a resposta comercial é byte-idêntica à de flag OFF.
    head = _valid_head()
    racing = _AnniversaryRace(head, _anniversary_row(head))
    shadow, _, _ = _gateway(LegitimacyScreenShadow, racing)
    off, _, _ = _flag_off()
    assert (await _execute(shadow)).wire_bytes == (await _execute(off)).wire_bytes
    assert racing.save_calls == 1
    assert racing.head("tenant-a", "entity-a").created_by == ANNIVERSARY_ACTOR  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_guarda_enforcing_na_corrida_com_o_aniversario_recusa_indisponivel() -> None:
    # Na enforcing a mesma corrida nunca vira passagem: recusa com o par fixo de
    # indisponibilidade, antes de qualquer passo de fonte.
    # O motivo (`source_unavailable` em vez de `legitimacy_unproven`) é o comportamento
    # atual da enforcing não montada, não contrato: o PR que montar a enforcing decide.
    head = _valid_head()
    racing = _AnniversaryRace(head, _anniversary_row(head))
    gateway, _, source = _enforcing(racing)
    result = await _execute_enforcing(gateway)
    assert (result.status, result.reason_code) == ("unavailable", "source_unavailable")
    assert "execute" not in source.calls


@pytest.mark.asyncio
async def test_guard_registra_sobre_cabeca_com_art39_sem_would_block() -> None:
    # Cabeça com art. 39 sem `would_block` (permitida pelo substrato) não é observação do
    # guard: a postura ainda grava a sua linha (só o par completo é idempotente).
    store = _Store(_valid_head(art39_signaled=True, art39_anchor="RN 557/2022, art. 39"))
    gateway, _, _ = _gateway(LegitimacyScreenShadow, store)
    await _execute(gateway)
    head = store.head("tenant-a", "entity-a")
    assert head is not None and head.screen_revision == 2 and head.created_by == GUARD_ACTOR
