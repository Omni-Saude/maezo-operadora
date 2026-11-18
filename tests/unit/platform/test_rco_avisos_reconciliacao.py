"""DL-0186 (unidade 2b): reconciliacao SO LEITURA de `sinaisAuditoria` (auditoria) x `rco_aviso_auditoria`.

Sem banco: funcoes puras e uma fonte em memoria. O pacote e o recorte REAL do K1 2.3.0 (composicao
`composicao_carencia_cpt` = [rco_geral_carencia, rco_geral_cpt]; `aviso_urgencia_carencia` ->
`auditoria_medica` e `cadastro`). O Postgres real esta em `test_rco_avisos_reconciliacao_live_pg.py`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.platform import rco_avisos_reconciliacao as rr
from maezo.platform import rco_manifesto as k1
from maezo.platform.rco_avisos import chave_idempotencia
from maezo.tools.workers import _audit_ctx, harness
from tests.unit.platform import rco_k1_2_3_recorte as rec

SINAL = "aviso_urgencia_carencia"
DESDE = datetime(2026, 11, 18, tzinfo=UTC)
ATE = datetime(2026, 11, 19, tzinfo=UTC)


def _pacote() -> dict[str, Any]:
    return {
        "composicoes": rec.manifesto().composicoes,
        "avisos_do_pin": k1.carregar_avisos_k6(rec.manifesto(), rec.vocabulario(), k1_dir=rec.RECORTE),
    }


def _registro(task_id: str = "task-1", sinais: list[str] | None = None, **extra: Any) -> rr.RegistroAuditado:
    entrada = {"version": 1, "shaDmn": "sha256:x", "sinaisAuditoria": [SINAL] if sinais is None else sinais}
    return rr.RegistroAuditado(
        f"amh:{task_id}",
        {
            "rco_geral_carencia": dict(entrada),
            "rco_geral_cpt": dict(entrada),
            "auth_sla": {"version": 2},
            **extra,
        },
    )


def _chave(task_id: str, fila: str) -> str:
    return chave_idempotencia("amh", task_id, rec.COMPOSICAO, SINAL, fila)


def test_paridade_com_o_harness() -> None:
    assert rr.CHAVE_SINAIS == _audit_ctx.CHAVE_SINAIS_AUDITORIA
    assert rr.DECISAO_CONCLUSAO == harness.AUDIT_DECISION_COMPLETE


def test_esperado_e_uma_linha_por_fila_uma_vez_por_tarefa_e_composicao() -> None:
    esperados, divergencias = rr.esperados_da_auditoria([_registro()], tenant="amh", **_pacote())
    assert divergencias == []
    assert [(e.task_id, e.composicao, e.sinal, e.fila) for e in esperados] == [
        ("task-1", rec.COMPOSICAO, SINAL, "auditoria_medica"),
        ("task-1", rec.COMPOSICAO, SINAL, "cadastro"),
    ]
    assert esperados[0].chave_idempotencia == _chave("task-1", "auditoria_medica")


def test_tudo_no_outbox_nao_diverge() -> None:
    status = {_chave("task-1", "auditoria_medica"): "entregue", _chave("task-1", "cadastro"): "pendente"}
    rel = rr.reconciliar([_registro()], tenant="amh", status_no_outbox=status, **_pacote())
    assert rel.ok
    assert rel.como_dict() == {
        "registros": 1,
        "esperados": 2,
        "encontrados_por_status": {"entregue": 1, "pendente": 1},
        "divergencias_por_codigo": {},
        "divergencias": [],
    }


@pytest.mark.parametrize(
    ("lido", "esperado"),
    [("fila_sem_grupo", "fila_sem_grupo"), ("publicacao_falhou", "publicacao_falhou"), ("texto livre", "")],
)
def test_linha_falhou_diverge_com_o_codigo_fechado(lido: str, esperado: str) -> None:
    """DL-0186: o aviso que terminou `falhou` NAO saiu; aparece como `aviso_falhou` e a saida e 1."""
    chave = _chave("task-1", "cadastro")
    status = {_chave("task-1", "auditoria_medica"): "entregue", chave: "falhou"}
    rel = rr.reconciliar(
        [_registro()], tenant="amh", status_no_outbox=status, erros_no_outbox={chave: lido}, **_pacote()
    )
    assert not rel.ok
    assert rel.como_dict()["encontrados_por_status"] == {"entregue": 1, "falhou": 1}
    item = {
        "codigo": "aviso_falhou",
        "task_id": "task-1",
        "composicao": rec.COMPOSICAO,
        "sinal": SINAL,
        "fila": "cadastro",
        "chave_idempotencia": chave,
    }
    if esperado:
        item["ultimo_erro"] = esperado
    assert [d.como_dict() for d in rel.divergencias] == [item]


def test_aviso_ausente_e_reportado_so_com_ids_e_codigo() -> None:
    status = {_chave("task-1", "auditoria_medica"): "pendente"}
    rel = rr.reconciliar(
        [_registro(), _registro("task-2")], tenant="amh", status_no_outbox=status, **_pacote()
    )
    assert not rel.ok
    ausentes = [d.como_dict() for d in rel.divergencias]
    assert ausentes == [
        {
            "codigo": "ausente_no_outbox",
            "task_id": "task-1",
            "composicao": rec.COMPOSICAO,
            "sinal": SINAL,
            "fila": "cadastro",
            "chave_idempotencia": _chave("task-1", "cadastro"),
        },
        *(
            {
                "codigo": "ausente_no_outbox",
                "task_id": "task-2",
                "composicao": rec.COMPOSICAO,
                "sinal": SINAL,
                "fila": fila,
                "chave_idempotencia": _chave("task-2", fila),
            }
            for fila in ("auditoria_medica", "cadastro")
        ),
    ]
    assert rel.como_dict()["divergencias_por_codigo"] == {"ausente_no_outbox": 3}
    assert {d.codigo for d in rel.divergencias} <= rr.CODIGOS


def test_registro_sem_sinal_nao_espera_nada() -> None:
    esperados, divergencias = rr.esperados_da_auditoria(
        [_registro(sinais=[]), rr.RegistroAuditado("amh:task-9", {"auth_sla": {"version": 1}})],
        tenant="amh",
        **_pacote(),
    )
    assert esperados == [] and divergencias == []


def test_codigos_fechados_para_o_que_o_pin_de_agora_nao_explica() -> None:
    registros = [
        rr.RegistroAuditado("outro:task-1", {"rco_geral_carencia": {"sinaisAuditoria": [SINAL]}}),
        rr.RegistroAuditado("amh:refuse:task-1", {"rco_geral_carencia": {"sinaisAuditoria": [SINAL]}}),
        rr.RegistroAuditado("amh:task-3", {"rco_fora_do_pacote": {"sinaisAuditoria": [SINAL]}}),
        rr.RegistroAuditado("amh:task-4", {"rco_geral_carencia": {"sinaisAuditoria": ["sinal_antigo"]}}),
    ]
    esperados, divergencias = rr.esperados_da_auditoria(registros, tenant="amh", **_pacote())
    assert esperados == []
    assert [d.como_dict() for d in divergencias] == [
        {"codigo": "chave_auditoria_fora_da_forma"},
        {"codigo": "chave_auditoria_fora_da_forma"},
        {"codigo": "composicao_desconhecida", "task_id": "task-3", "decisao": "rco_fora_do_pacote"},
        {
            "codigo": "sinal_sem_aviso",
            "task_id": "task-4",
            "composicao": rec.COMPOSICAO,
            "sinal": "sinal_antigo",
        },
    ]


class FonteEmMemoria:
    def __init__(self, registros: list[rr.RegistroAuditado], status: dict[str, str]) -> None:
        self.registros = registros
        self.status = status
        self.consultadas: list[list[str]] = []

    async def ler_auditoria(self, desde: datetime, ate: datetime) -> list[rr.RegistroAuditado]:
        assert (desde, ate) == (DESDE, ATE)
        return self.registros

    async def status_no_outbox(self, chaves: Sequence[str]) -> dict[str, str]:
        self.consultadas.append(list(chaves))
        return {c: s for c, s in self.status.items() if c in chaves}

    async def erros_no_outbox(self, chaves: Sequence[str]) -> dict[str, str]:
        return {c: "fila_sem_grupo" for c, s in self.status.items() if c in chaves and s == "falhou"}


async def test_executar_le_so_as_chaves_esperadas() -> None:
    fonte = FonteEmMemoria(
        [_registro()], {_chave("task-1", "auditoria_medica"): "entregue", "outra": "pendente"}
    )
    rel = await rr.executar(fonte, tenant="amh", desde=DESDE, ate=ATE, **_pacote())
    assert fonte.consultadas == [[_chave("task-1", "auditoria_medica"), _chave("task-1", "cadastro")]]
    assert [d.fila for d in rel.divergencias] == ["cadastro"]


async def test_cli_recusa_janela_vazia_e_sem_banco(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert await rr.main(["--desde", "2026-11-19T00:00:00+00:00", "--ate", "2026-11-18T00:00:00+00:00"]) == 2
    assert json.loads(capsys.readouterr().out) == {"erro": "janela_vazia"}
    assert await rr.main(["--desde", "2026-11-18T00:00:00+00:00", "--ate", "2026-11-19T00:00:00+00:00"]) == 2
    assert json.loads(capsys.readouterr().out) == {"erro": "database_url_ausente"}
    with pytest.raises(SystemExit):
        rr.build_arg_parser().parse_args(
            ["--desde", "2026-11-18T00:00:00", "--ate", "2026-11-19T00:00:00+00:00"]
        )


async def test_cli_codigo_de_saida_pelo_relatorio(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace

    from maezo.tools.workers import rco_pin

    pacote = _pacote()
    monkeypatch.setenv("DATABASE_URL", "postgresql://nao-usado/db")
    monkeypatch.setenv("TENANT_ID", "amh")
    monkeypatch.setattr(
        rco_pin,
        "rco_rules_pin",
        lambda: SimpleNamespace(composicoes=pacote["composicoes"], avisos=pacote["avisos_do_pin"]),
    )
    fonte = FonteEmMemoria([_registro()], {})
    monkeypatch.setattr(rr, "PostgresFonteReconciliacao", lambda **_k: fonte)
    argv = ["--desde", "2026-11-18T00:00:00+00:00", "--ate", "2026-11-19T00:00:00+00:00"]
    assert await rr.main(argv) == 1
    saida = json.loads(capsys.readouterr().out)
    assert saida["divergencias_por_codigo"] == {"ausente_no_outbox": 2}
    fonte.status = {_chave("task-1", f): "entregue" for f in ("auditoria_medica", "cadastro")}
    assert await rr.main(argv) == 0
    capsys.readouterr()
    fonte.status[_chave("task-1", "cadastro")] = "falhou"  # DL-0186: linha esperada que nao saiu
    assert await rr.main(argv) == 1
    saida = json.loads(capsys.readouterr().out)
    assert saida["divergencias_por_codigo"] == {"aviso_falhou": 1}
    assert saida["divergencias"][0]["ultimo_erro"] == "fila_sem_grupo"


async def test_banco_fora_vira_codigo_so_com_o_tipo() -> None:
    async def conectar() -> Any:
        raise ConnectionRefusedError("detalhe do driver com host e senha")

    fonte = rr.PostgresFonteReconciliacao(dsn="postgresql://x/y", tenant="amh", conectar=conectar)
    with pytest.raises(rr.ReconciliacaoIndisponivelError) as exc:
        await fonte.ler_auditoria(DESDE, ATE)
    assert str(exc.value) == "reconciliacao indisponivel: ConnectionRefusedError"
