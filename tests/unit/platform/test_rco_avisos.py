"""DL-0184 (unidade 2a): expansao dos avisos de `sinalAuditoria` pelas filas do K6, o outbox e o relay — sem
banco nem broker (um outbox em memoria com a semantica da 0036 e um produtor falso).

Os destinos vem do PIN: o recorte REAL do K1 2.3.0 do rco (`tests/fixtures/rco/k1-2.3.0-recorte/`) declara
`aviso_urgencia_carencia` -> `auditoria_medica` e `cadastro`. O Postgres real esta em
`test_rco_aviso_auditoria_live_pg.py`; o harness em `tests/unit/tools/workers/test_harness_rco_avisos.py`.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

import pytest

from maezo.platform import observability, rco_avisos, rco_avisos_relay
from maezo.platform import rco_manifesto as k1
from maezo.platform.rco_avisos import (
    AvisoInvalidoError,
    AvisoReivindicado,
    LinhaAviso,
    SinalSemAvisoError,
    avisos_para,
    chave_idempotencia,
    linhas_de_aviso,
    montar_payload,
)
from maezo.runtime.dependency_failures import PROGRAMMING_ERRORS
from tests.unit.platform import rco_k1_2_3_recorte as rec

SINAL_REAL = "aviso_urgencia_carencia"


def avisos_do_recorte() -> Mapping[str, k1.AvisoK6]:
    return k1.carregar_avisos_k6(rec.manifesto(), rec.vocabulario(), k1_dir=rec.RECORTE)


# --- fan-out puro -----------------------------------------------------------------------------------


def test_destinos_vem_do_pin_real_2_3_0() -> None:
    itens = avisos_para([SINAL_REAL], avisos_do_recorte())
    assert [(i.sinal, i.fila) for i in itens] == [
        (SINAL_REAL, "auditoria_medica"),
        (SINAL_REAL, "cadastro"),
    ]
    assert {i.prazo_parametro for i in itens} == {"OMNI-GERAL-CAR-0003:efeito.prazos.2"}


@pytest.mark.parametrize("sinais", [[], [""], ["", "  "]])
def test_sinal_vazio_nao_gera_nada(sinais: list[str]) -> None:
    assert avisos_para(sinais, avisos_do_recorte()) == ()


def test_sinal_desconhecido_e_erro_de_programacao() -> None:
    with pytest.raises(SinalSemAvisoError) as exc:
        avisos_para(["sinal_que_o_pin_recusaria"], avisos_do_recorte())
    assert isinstance(exc.value, PROGRAMMING_ERRORS)


def test_sinal_repetido_nao_duplica_destino() -> None:
    assert len(avisos_para([SINAL_REAL, SINAL_REAL], avisos_do_recorte())) == 2


def test_linhas_tem_chave_por_tarefa_composicao_sinal_e_fila() -> None:
    linhas = linhas_de_aviso(
        tenant="amh",
        task_id="task-1",
        composicoes=[(rec.COMPOSICAO, (SINAL_REAL,)), ("outra_composicao", ())],
        avisos_do_pin=avisos_do_recorte(),
    )
    assert [linha.fila for linha in linhas] == ["auditoria_medica", "cadastro"]
    assert linhas[0].chave_idempotencia == chave_idempotencia(
        "amh", "task-1", rec.COMPOSICAO, SINAL_REAL, "auditoria_medica"
    )
    assert len({linha.chave_idempotencia for linha in linhas}) == 2
    # a mesma tarefa da a mesma chave; outra tarefa, outra
    de_novo = linhas_de_aviso(
        tenant="amh",
        task_id="task-1",
        composicoes=[(rec.COMPOSICAO, (SINAL_REAL,))],
        avisos_do_pin=avisos_do_recorte(),
    )
    assert de_novo == linhas
    outra = linhas_de_aviso(
        tenant="amh",
        task_id="task-2",
        composicoes=[(rec.COMPOSICAO, (SINAL_REAL,))],
        avisos_do_pin=avisos_do_recorte(),
    )
    assert {x.chave_idempotencia for x in outra}.isdisjoint({x.chave_idempotencia for x in linhas})


@pytest.mark.parametrize(
    ("campo", "kwargs"),
    [
        ("tenant", {"tenant": "a m h"}),
        ("task_id", {"task_id": "task 1"}),
        ("composicao", {"composicoes": [("comp osicao", (SINAL_REAL,))]}),
    ],
)
def test_forma_dos_campos_conferida(campo: str, kwargs: dict[str, Any]) -> None:
    base: dict[str, Any] = {
        "tenant": "amh",
        "task_id": "task-1",
        "composicoes": [(rec.COMPOSICAO, (SINAL_REAL,))],
        "avisos_do_pin": avisos_do_recorte(),
    }
    base.update(kwargs)
    with pytest.raises(AvisoInvalidoError) as exc:
        linhas_de_aviso(**base)
    assert str(exc.value) == campo


def test_prazo_fora_da_forma_e_recusado() -> None:
    avisos = {SINAL_REAL: k1.AvisoK6(SINAL_REAL, ("cadastro",), "prazo com espaco")}
    with pytest.raises(AvisoInvalidoError):
        linhas_de_aviso(
            tenant="amh", task_id="t", composicoes=[(rec.COMPOSICAO, (SINAL_REAL,))], avisos_do_pin=avisos
        )


def test_payload_so_leva_ids_sinal_fila_e_prazo() -> None:
    (linha, _) = linhas_de_aviso(
        tenant="amh",
        task_id="task-1",
        composicoes=[(rec.COMPOSICAO, (SINAL_REAL,))],
        avisos_do_pin=avisos_do_recorte(),
    )
    assert montar_payload(linha) == {
        "type": "rco.aviso_auditoria",
        "tenant_id": "amh",
        "task_id": "task-1",
        "composicao": rec.COMPOSICAO,
        "sinal": SINAL_REAL,
        "fila": "auditoria_medica",
        "prazo_parametro": "OMNI-GERAL-CAR-0003:efeito.prazos.2",
        "chave_idempotencia": linha.chave_idempotencia,
    }


def test_desfechos_da_metrica_tem_paridade() -> None:
    assert rco_avisos.DESFECHOS == observability.RCO_AVISO_DESFECHOS


def test_metrica_rotulo_fechado(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.runtime.metrics import MetricsCollector

    coletor = MetricsCollector()
    monkeypatch.setattr(observability, "_get_metrics_collector", lambda: coletor)
    observability.record_rco_aviso("enfileirado", 2)
    observability.record_rco_aviso("qualquer_coisa")
    observability.record_rco_aviso("entregue", 0)
    assert coletor.rco_aviso_total.labels(desfecho="enfileirado")._value.get() == 2
    assert coletor.rco_aviso_total.labels(desfecho="outro")._value.get() == 1
    assert coletor.rco_aviso_total.labels(desfecho="entregue")._value.get() == 0


# --- outbox em memoria (semantica da 0036) e produtor falso -------------------------------------------


class OutboxEmMemoria:
    """O outbox com a semantica da 0036: unico por chave, lease, entregue/falhou, reprocessar."""

    def __init__(self, tenant: str = "amh") -> None:
        self.tenant = tenant
        self.linhas: dict[int, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self.falhar_enfileirar: BaseException | None = None

    async def enfileirar(self, linhas: Sequence[LinhaAviso]) -> int:
        if self.falhar_enfileirar is not None:
            raise self.falhar_enfileirar
        chaves = {r["linha"].chave_idempotencia for r in self.linhas.values()}
        novas = 0
        for linha in linhas:
            if linha.chave_idempotencia in chaves:
                continue
            chaves.add(linha.chave_idempotencia)
            self.linhas[next(self._ids)] = {
                "linha": linha,
                "status": "pendente",
                "tentativas": 0,
                "ultimo_erro": None,
                "por": None,
            }
            novas += 1
        return novas

    async def reivindicar(
        self, *, reivindicada_por: str, lote: int = 50, lease_s: float = 60.0
    ) -> list[AvisoReivindicado]:
        out = []
        for i, r in sorted(self.linhas.items()):
            if r["status"] == "pendente" and len(out) < lote:
                r.update(status="reivindicada", por=reivindicada_por)
                out.append(AvisoReivindicado(id=i, tentativas=r["tentativas"], linha=r["linha"]))
        return out

    def _meus(self, ids: Sequence[int], por: str) -> list[dict[str, Any]]:
        return [
            self.linhas[i]
            for i in ids
            if self.linhas[i]["status"] == "reivindicada" and self.linhas[i]["por"] == por
        ]

    async def marcar_entregue(self, ids: Sequence[int], *, reivindicada_por: str) -> int:
        rs = self._meus(ids, reivindicada_por)
        for r in rs:
            r.update(status="entregue", por=None, tentativas=r["tentativas"] + 1)
        return len(rs)

    async def marcar_falhou(self, ids: Sequence[int], *, reivindicada_por: str, erro: str) -> int:
        assert erro in rco_avisos.ERROS
        rs = self._meus(ids, reivindicada_por)
        for r in rs:
            r.update(status="falhou", por=None, ultimo_erro=erro, tentativas=r["tentativas"] + 1)
        return len(rs)

    async def liberar(self, ids: Sequence[int], *, reivindicada_por: str) -> int:
        rs = self._meus(ids, reivindicada_por)
        for r in rs:
            r.update(status="pendente", por=None)
        return len(rs)

    async def reprocessar_falhas(self) -> int:
        rs = [r for r in self.linhas.values() if r["status"] == "falhou"]
        for r in rs:
            r.update(status="pendente", ultimo_erro=None)
        return len(rs)

    def status(self) -> list[str]:
        return [r["status"] for _, r in sorted(self.linhas.items())]


class ProdutorFalso:
    """O `KafkaPublisher` da casa: grava o que publicaria; `falhas` = quantas publicacoes levantam."""

    def __init__(self, falhas: int = 0) -> None:
        self.publicadas: list[tuple[str, dict[str, Any], str | None, bool | None]] = []
        self.falhas = falhas

    async def publish(
        self, topic: str, value: dict[str, Any], *, key: str | None = None, best_effort: bool | None = None
    ) -> bool:
        if self.falhas > 0:
            self.falhas -= 1
            raise ConnectionError("broker fora com detalhe que nao pode vazar")
        self.publicadas.append((topic, dict(value), key, best_effort))
        return True


def _linhas(task_id: str = "task-1") -> tuple[LinhaAviso, ...]:
    return linhas_de_aviso(
        tenant="amh",
        task_id=task_id,
        composicoes=[(rec.COMPOSICAO, (SINAL_REAL,))],
        avisos_do_pin=avisos_do_recorte(),
    )


async def test_relay_publica_o_payload_exato_e_marca_entregue() -> None:
    outbox = OutboxEmMemoria()
    assert await outbox.enfileirar(_linhas()) == 2
    produtor = ProdutorFalso()
    rel = await rco_avisos_relay.drain_once(outbox, produtor, reivindicada_por="relay-1")
    assert (rel.reivindicadas, rel.entregues, rel.falhas, rel.erro) == (2, 2, 0, None)
    assert outbox.status() == ["entregue", "entregue"]
    assert [p[0] for p in produtor.publicadas] == ["operadora.notifications.internal"] * 2
    assert all(p[3] is False for p in produtor.publicadas)  # best_effort=False
    assert [p[1] for p in produtor.publicadas] == [montar_payload(x) for x in _linhas()]
    assert {p[2] for p in produtor.publicadas} == {"amh|task-1"}
    # nada mais a publicar
    assert (
        await rco_avisos_relay.drain_once(outbox, produtor, reivindicada_por="relay-1")
    ).reivindicadas == 0


async def test_falha_de_publicacao_marca_falhou_e_reprocessar_entrega() -> None:
    import structlog

    outbox = OutboxEmMemoria()
    await outbox.enfileirar(_linhas())
    produtor = ProdutorFalso(falhas=1)
    with structlog.testing.capture_logs() as logs:
        rel = await rco_avisos_relay.drain_once(outbox, produtor, reivindicada_por="relay-1")
    assert (rel.entregues, rel.falhas, rel.liberadas, rel.erro) == (0, 1, 1, "publicacao_falhou")
    assert outbox.status() == ["falhou", "pendente"]
    falhou = outbox.linhas[1]
    assert (falhou["tentativas"], falhou["ultimo_erro"]) == (1, "publicacao_falhou")
    assert outbox.linhas[2]["tentativas"] == 0  # o resto do lote nao conta tentativa
    assert "detalhe" not in str(logs)  # so o codigo fechado, nunca a mensagem do broker
    # a proxima varredura so entrega a que ficou pendente; a `falhou` espera o operador
    await rco_avisos_relay.drain_once(outbox, produtor, reivindicada_por="relay-1")
    assert outbox.status() == ["falhou", "entregue"]
    assert await rco_avisos_relay.reprocessar(outbox) == 1
    await rco_avisos_relay.drain_once(outbox, produtor, reivindicada_por="relay-1")
    assert outbox.status() == ["entregue", "entregue"]
    assert outbox.linhas[1]["tentativas"] == 2
    assert len(produtor.publicadas) == 2


async def test_publicacao_engolida_conta_como_falha() -> None:
    class Engolida(ProdutorFalso):
        async def publish(self, topic: str, value: dict[str, Any], **kw: Any) -> bool:
            return False

    outbox = OutboxEmMemoria()
    await outbox.enfileirar(_linhas())
    rel = await rco_avisos_relay.drain_once(outbox, Engolida(), reivindicada_por="r")
    assert rel.falhas == 1 and outbox.status()[0] == "falhou"


def test_cli_tem_once_e_reprocessar() -> None:
    args = rco_avisos_relay.build_arg_parser().parse_args(["--reprocessar"])
    assert args.reprocessar is True and args.once is False
    assert rco_avisos_relay.build_arg_parser().parse_args(["--once"]).once is True


def test_relay_recusa_subir_sem_banco_ou_broker() -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        rco_avisos_relay.build_relay(
            rco_avisos_relay.RelaySettings(DATABASE_URL=None, KAFKA_BOOTSTRAP_SERVERS="k:9092")
        )
    with pytest.raises(RuntimeError, match="KAFKA_BOOTSTRAP_SERVERS"):
        rco_avisos_relay.build_relay(
            rco_avisos_relay.RelaySettings(DATABASE_URL="postgresql://x/y", KAFKA_BOOTSTRAP_SERVERS=None)
        )


def test_avisos_do_pin_sao_imutaveis_no_recorte() -> None:
    assert isinstance(avisos_do_recorte(), MappingProxyType)


# --- CLI: codigos de saida, `--once` drena tudo, modo continuo sobrevive ao banco fora ----------------


class OutboxFora(OutboxEmMemoria):
    """Outbox cujo banco nao responde nas `falhas` primeiras reivindicacoes."""

    def __init__(self, falhas: int = 10**6) -> None:
        super().__init__()
        self.falhas = falhas
        self.reivindicacoes = 0

    async def reivindicar(self, **kw: Any) -> list[AvisoReivindicado]:
        self.reivindicacoes += 1
        if self.falhas > 0:
            self.falhas -= 1
            raise rco_avisos.OutboxAvisosIndisponivelError("rco_aviso_auditoria indisponivel: OSError")
        return await super().reivindicar(**kw)

    async def reprocessar_falhas(self) -> int:
        if self.falhas > 0:
            raise rco_avisos.OutboxAvisosIndisponivelError("rco_aviso_auditoria indisponivel: OSError")
        return await super().reprocessar_falhas()


def _args(*argv: str) -> Any:
    return rco_avisos_relay.build_arg_parser().parse_args(list(argv))


async def test_once_drena_ate_nao_sobrar_linha_e_sai_zero() -> None:
    outbox = OutboxEmMemoria()
    for i in range(5):
        await outbox.enfileirar(_linhas(f"task-{i}"))
    produtor = ProdutorFalso()
    codigo = await rco_avisos_relay.executar(_args("--once"), outbox, produtor, reivindicada_por="r", lote=3)
    assert codigo == 0
    assert outbox.status() == ["entregue"] * 10  # 4 lotes de ate 3, nao um so
    assert len(produtor.publicadas) == 10


async def test_once_com_falha_de_publicacao_sai_um() -> None:
    outbox = OutboxEmMemoria()
    await outbox.enfileirar(_linhas())
    assert (
        await rco_avisos_relay.executar(
            _args("--once"), outbox, ProdutorFalso(falhas=1), reivindicada_por="r"
        )
        == 1
    )
    assert outbox.status() == ["falhou", "pendente"]


async def test_once_respeita_o_limite_de_lotes() -> None:
    outbox = OutboxEmMemoria()
    for i in range(3):
        await outbox.enfileirar(_linhas(f"task-{i}"))
    codigo = await rco_avisos_relay.drenar(outbox, ProdutorFalso(), reivindicada_por="r", lote=1, max_lotes=2)
    assert codigo == 1 and outbox.status().count("pendente") == 4


async def test_reprocessar_limpa_o_erro_drena_e_sai_zero() -> None:
    outbox = OutboxEmMemoria()
    await outbox.enfileirar(_linhas())
    await rco_avisos_relay.drain_once(outbox, ProdutorFalso(falhas=1), reivindicada_por="r")
    assert outbox.linhas[1]["ultimo_erro"] == "publicacao_falhou"
    assert await rco_avisos_relay.reprocessar(outbox) == 1
    assert (outbox.linhas[1]["status"], outbox.linhas[1]["ultimo_erro"]) == ("pendente", None)
    outbox2 = OutboxEmMemoria()
    await outbox2.enfileirar(_linhas())
    await rco_avisos_relay.drain_once(outbox2, ProdutorFalso(falhas=1), reivindicada_por="r")
    assert (
        await rco_avisos_relay.executar(
            _args("--reprocessar"), outbox2, ProdutorFalso(), reivindicada_por="r"
        )
        == 0
    )
    assert outbox2.status() == ["entregue", "entregue"]
    outbox3 = OutboxEmMemoria()
    await outbox3.enfileirar(_linhas())
    await rco_avisos_relay.drain_once(outbox3, ProdutorFalso(falhas=1), reivindicada_por="r")
    assert (
        await rco_avisos_relay.executar(
            _args("--reprocessar"), outbox3, ProdutorFalso(falhas=1), reivindicada_por="r"
        )
        == 1
    )


@pytest.mark.parametrize("argv", [("--once",), ("--reprocessar",)])
async def test_banco_fora_sai_dois(argv: tuple[str, ...], monkeypatch: pytest.MonkeyPatch) -> None:
    contados: list[str] = []
    monkeypatch.setattr(rco_avisos_relay, "record_rco_aviso", lambda d, q=1: contados.append(d))
    assert (
        await rco_avisos_relay.executar(_args(*argv), OutboxFora(), ProdutorFalso(), reivindicada_por="r")
        == 2
    )
    assert contados == ["outbox_indisponivel"]


async def test_modo_continuo_sobrevive_ao_banco_fora_e_continua(monkeypatch: pytest.MonkeyPatch) -> None:
    contados: list[str] = []
    monkeypatch.setattr(rco_avisos_relay, "record_rco_aviso", lambda d, q=1: contados.append(d))
    outbox = OutboxFora(falhas=2)
    await OutboxEmMemoria.enfileirar(outbox, _linhas())
    produtor = ProdutorFalso()
    relatorios = await rco_avisos_relay.run_relay_loop(
        outbox, produtor, reivindicada_por="r", intervalo_s=0.0, max_varreduras=3
    )
    assert [r.erro for r in relatorios] == ["outbox_indisponivel", "outbox_indisponivel", None]
    assert outbox.status() == ["entregue", "entregue"]
    assert contados.count("outbox_indisponivel") == 2
