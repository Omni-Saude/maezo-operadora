"""Composicao K1 avaliada em SOMBRA e a guarda de membro isolado (DL-0173, `rco_sombra`).

O pin e montado do recorte REAL do K1 2.3.0 (`tests/unit/platform/rco_k1_2_3_recorte.py`: manifesto inteiro,
`vocabulario.json` e o dicionario 1.5.0) com o vocabulario da composicao (`ordem_restricao`,
`sinais_auditoria`, `adversas`, `adversa_exige_preenchidos`) como o leitor de producao o expoe. O motor e o
`FakeDmnTransport` (ou um transporte que levanta/dorme). Prova:

- `composicao_carencia_cpt` compoe com o PIOR caso (carencia `negar` com sinal, cpt `recomendar_negativa`),
  devolve `None`, nao muda os fatos, registra a composicao so com `modo=sombra` (DL-0187: o harness nao
  enfileira aviso de sombra) e audita cada membro com a auditoria do montador DELE e `composicaoK1`;
- uniao de entradas em conflito, composicao ausente, pin sem pacote ou ilegivel, prazo repartido e
  esgotado, erro de programacao dentro de `compor` e no avaliador: codigos fechados, nada sobe;
- a guarda: membro de grupo pedido sozinho nao e avaliado; a decisao de prazos de conta (`contas.py`) nao e
  membro no recorte real e segue avaliada;
- a sombra nao le o indicador de efeito do K1 (AST) e log/metrica so levam campos de uma lista de permissao.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import structlog

from maezo.platform import rco_ativacao as at
from maezo.platform import rco_composicao as cmp
from maezo.platform import rco_manifesto as k1
from maezo.tools.workers import _audit_ctx, contas, rco_pin, rco_sombra
from maezo.tools.workers import rco_montador as mt
from maezo.tools.workers._audit_ctx import (
    collect_dmn_versions,
    collect_rco_avaliacoes,
    collect_rco_composicoes,
)
from maezo.tools.workers.dmn_transport import (
    DmnEvaluationError,
    DmnHitPolicyViolationError,
    DmnVersion,
    FakeDmnTransport,
)
from tests.unit.platform import rco_k1_2_3_recorte as rec

GRUPO = rec.COMPOSICAO
CARENCIA, CPT = rec.ORDEM
PRAZOS = contas.DECISAO_RCO_PRAZOS_CONTA
TOPICO = "operadora.sintetico.composicao"
SINAL = "aviso_urgencia_carencia"
#: O pior caso: as duas decisoes adversas; `negar` e a mais restritiva e traz o sinal.
ROW_CARENCIA = {
    "decisao": "negar",
    "motivoTab38": "1001",
    "idRegra": "OMNI-GERAL-CAR-0001",
    "fila": "auditoria",
    "classeAdverso": "administrativo",
    "sinalAuditoria": SINAL,
}
ROW_CPT = {
    "decisao": "recomendar_negativa",
    "motivoTab38": "1002",
    "idRegra": "OMNI-GERAL-CAR-0004",
    "fila": "auditoria",
    "classeAdverso": "clinico",
}
#: Identificadores sinteticos do caso: nenhum pode chegar a log ou metrica.
GUIA = "GUIA-SINT-6161"
BENEFICIARIO = "BEN-SINT-8282"
PROIBIDOS = (GUIA, BENEFICIARIO, "2026-11-03")
CAMPOS_LOG = frozenset({"event", "log_level", "composicao", "decision_key", "desfecho", "topico", "erro"})


def _pin() -> rco_pin.RcoRulesPin:
    manifesto = rec.manifesto()
    vocab = rec.vocabulario()
    tabelas = {
        d.id: rco_pin.RcoPinnedTable(d.id, d.caminho_consumidor, d.sha256, regras=d.regras, decisao=d)
        for d in manifesto.decisoes.values()
    }
    return rco_pin.RcoRulesPin(
        estado=rco_pin.ESTADO_FIXADO,
        tables=MappingProxyType(tabelas),
        parametros_ativacao=vocab.parametros_ativacao,
        manifesto=manifesto,
        k1_dir=rec.RECORTE,
        ordem_restricao=vocab.ordem_restricao,
        sinais_auditoria=vocab.sinais_auditoria,
        adversas=vocab.adversas,
        adversa_exige_preenchidos=vocab.adversa_exige_preenchidos,
    )


def _pinar(monkeypatch: pytest.MonkeyPatch, pin: rco_pin.RcoRulesPin) -> rco_pin.RcoRulesPin:
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: pin)
    return pin


@pytest.fixture
def pin_2_3(monkeypatch: pytest.MonkeyPatch) -> rco_pin.RcoRulesPin:
    dicionario = rec.dicionario()
    monkeypatch.setattr(rco_sombra, "_dicionario", lambda _pin: dicionario)
    monkeypatch.setattr(rco_sombra, "_versao_k3_pinada", lambda: (1, 15))
    monkeypatch.delenv(at.CHAVE_EFEITO_K1, raising=False)
    return _pinar(monkeypatch, _pin())


@pytest.fixture
def metrica(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    contados: list[str] = []
    monkeypatch.setattr(rco_sombra, "record_rco_sombra", contados.append)
    return contados


@dataclasses.dataclass
class _Fatos:
    """`MontarFatos` que guarda o que entregou (para provar que nada foi mudado)."""

    entregues: list[mt.FatosDoCaso] = dataclasses.field(default_factory=list)

    def __call__(self, versao_k3: tuple[int, int]) -> mt.FatosDoCaso:
        fatos = mt.FatosDoCaso(
            guia={"data_atendimento": "2026-11-03", "numero_guia_tiss": GUIA, "beneficiario": BENEFICIARIO},
            versao_k3_pinada=versao_k3,
            carater_atendimento_guia="U",
        )
        self.entregues.append(fatos)
        return fatos


def _dmn_pior_caso() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register(CARENCIA, [dict(ROW_CARENCIA)])
    dmn.register(CPT, [dict(ROW_CPT)])
    return dmn


def _logs(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in logs if str(e["event"]).startswith("rco_sombra")]


def _assert_logs_permitidos(logs: list[dict[str, Any]]) -> None:
    for evento in logs:
        assert set(evento) <= CAMPOS_LOG, set(evento) - CAMPOS_LOG
        assert not any(p in repr(evento) for p in PROIBIDOS)


# --- o recorte real: o grupo e o vocabulario que a sombra le --------------------------------------------


def test_recorte_real_declara_o_grupo_e_o_vocabulario(pin_2_3: rco_pin.RcoRulesPin) -> None:
    grupo = cmp.composicao_de(pin_2_3, CARENCIA)
    assert grupo is not None and grupo.nome == GRUPO and grupo.decisoes == rec.ORDEM
    assert grupo.regra == k1.REGRA_MAIS_RESTRITIVA
    assert pin_2_3.ordem_restricao == rec.ORDEM_RESTRICAO_ACORDADA
    assert tuple(pin_2_3.sinais_auditoria) == rec.SINAIS_ACORDADOS
    assert pin_2_3.adversas and pin_2_3.adversa_exige_preenchidos


# --- composicao_carencia_cpt com o pior caso ---------------------------------------------------------------


def test_compoe_o_pior_caso_sem_efeito(
    pin_2_3: rco_pin.RcoRulesPin, metrica: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    resultados: list[cmp.ResultadoComposicao] = []
    compor = cmp.compor

    def espiao(*a: Any, **k: Any) -> cmp.ResultadoComposicao:
        resultado = compor(*a, **k)
        resultados.append(resultado)
        return resultado

    monkeypatch.setattr(cmp, "compor", espiao)
    registros: list[tuple[cmp.ResultadoComposicao, str]] = []
    registrar = _audit_ctx.registrar_composicao

    def espiao_registro(resultado: cmp.ResultadoComposicao, *, modo: str) -> None:
        registros.append((resultado, modo))
        registrar(resultado, modo=modo)

    monkeypatch.setattr(_audit_ctx, "registrar_composicao", espiao_registro)
    fatos = _Fatos()
    dmn = _dmn_pior_caso()
    with (
        collect_dmn_versions() as versoes,
        collect_rco_avaliacoes() as avaliacoes,
        collect_rco_composicoes() as composicoes,
        structlog.testing.capture_logs() as logs,
    ):
        retorno = rco_sombra.avaliar_composicao_em_sombra(dmn, GRUPO, fatos, topico=TOPICO)
    assert retorno is None
    assert metrica == [rco_sombra.COMPOSICAO_ESCOLHIDA]
    [resultado] = resultados
    assert (resultado.decisao_vencedora, resultado.sinais) == (CARENCIA, (SINAL,))
    assert resultado.linhas[0]["decisao"] == "negar"
    # os dois membros avaliados, na ordem do grupo, com a MESMA uniao de entradas
    assert [chave for chave, _ in dmn.calls] == list(rec.ORDEM)
    [(_, ent_carencia), (_, ent_cpt)] = dmn.calls
    assert ent_carencia == ent_cpt
    manifesto = rec.manifesto()
    assert set(ent_carencia) == set(manifesto.decisoes[CARENCIA].entradas) | set(
        manifesto.decisoes[CPT].entradas
    )
    # cada membro auditado com a auditoria do montador DELE, sempre em sombra
    for membro in rec.ORDEM:
        entrada = versoes[membro]
        assert entrada["modo"] == "sombra"
        assert entrada["origemAvaliacao"] == rco_sombra.ORIGEM_SOMBRA
        assert set(entrada["entradasNulas"]) <= set(manifesto.decisoes[membro].entradas)
        # DL-0187: a composicao e registrada em `modo=sombra`: o sinal e o resumo vao a auditoria
        assert entrada["sinaisAuditoria"] == [SINAL]
        assert entrada["composicaoK1"] == {
            "nome": GRUPO,
            "modo": "sombra",
            "desfecho": cmp.DESFECHO_ESCOLHIDO,
            "decisaoVencedora": CARENCIA,
            "motivo": None,
            "peloPadrao": False,
        }
        assert avaliacoes[membro].sombra is True and avaliacoes[membro].tem_ordinal is False
    # registrada UMA vez, sempre `modo=sombra` (o harness nao enfileira aviso de sombra; DL-0187)
    assert registros == [(resultado, "sombra")]
    assert {n: c.modo for n, c in composicoes.items()} == {GRUPO: "sombra"}
    # os fatos entregues nao mudaram (a uniao e copia; `compor` copia de novo)
    [entregue] = fatos.entregues
    assert entregue == _Fatos()((1, 15))
    [log] = _logs(logs)
    assert log == {
        "event": "rco_sombra_composicao",
        "log_level": "info",
        "composicao": GRUPO,
        "desfecho": rco_sombra.COMPOSICAO_ESCOLHIDA,
        "topico": TOPICO,
    }
    _assert_logs_permitidos(_logs(logs))


def test_sombra_da_composicao_nao_le_o_indicador(
    pin_2_3: rco_pin.RcoRulesPin, metrica: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def nao_leia() -> bool:
        raise AssertionError("a sombra leu o indicador do K1")

    monkeypatch.setattr(rco_pin, "efeito_k1_habilitado", nao_leia)
    monkeypatch.setattr(at, "efeito_k1_habilitado", nao_leia)
    with collect_dmn_versions() as versoes, collect_rco_avaliacoes():
        rco_sombra.avaliar_composicao_em_sombra(_dmn_pior_caso(), GRUPO, _Fatos())
    assert metrica == [rco_sombra.COMPOSICAO_ESCOLHIDA]
    assert {versoes[m]["modo"] for m in rec.ORDEM} == {"sombra"}


def test_grupo_em_revisao_humana_e_desfecho_fechado(pin_2_3: rco_pin.RcoRulesPin, metrica: list[str]) -> None:
    """Vencedora adversa sem `idRegra` (contrato §6): o grupo vai a revisao humana; so o codigo sai."""
    dmn = FakeDmnTransport()
    dmn.register(CARENCIA, [{**ROW_CARENCIA, "idRegra": ""}])
    dmn.register(CPT, [dict(ROW_CPT)])
    assert rco_sombra.avaliar_composicao_em_sombra(dmn, GRUPO, _Fatos()) is None
    assert metrica == [rco_sombra.COMPOSICAO_REVISAO_HUMANA]


# --- desfechos fechados sem avaliacao ---------------------------------------------------------------------


def test_uniao_em_conflito_nao_avalia(
    pin_2_3: rco_pin.RcoRulesPin, metrica: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    montar = mt.montar_entradas

    def divergente(decisao: k1.DecisaoK1, *a: Any) -> mt.EntradasMontadas:
        montadas = montar(decisao, *a)
        if decisao.id != CPT:
            return montadas
        return dataclasses.replace(montadas, variaveis={**montadas.variaveis, "dataAtendimento": "outra"})

    monkeypatch.setattr(mt, "montar_entradas", divergente)
    dmn = _dmn_pior_caso()
    with structlog.testing.capture_logs() as logs:
        assert rco_sombra.avaliar_composicao_em_sombra(dmn, GRUPO, _Fatos(), topico=TOPICO) is None
    assert dmn.calls == []
    assert metrica == [rco_sombra.ENTRADAS_EM_CONFLITO]
    _assert_logs_permitidos(_logs(logs))


#: O valor da MESMA entrada compartilhada (`dataAtendimento`) em carencia e em cpt.
_CONFLITOS = [
    (None, "2026-11-03"),
    ("2026-11-03", None),
    (True, 1),
    (1, True),
    (1, 1.0),
    ("2026-11-03", "2026-11-04"),
]
_UNIOES = [("2026-11-03", "2026-11-03"), (None, None), (True, True), (7, 7)]


def _com_valores(monkeypatch: pytest.MonkeyPatch, carencia: object, cpt: object) -> None:
    montar = mt.montar_entradas

    def fixo(decisao: k1.DecisaoK1, *a: Any) -> mt.EntradasMontadas:
        montadas = montar(decisao, *a)
        valor = carencia if decisao.id == CARENCIA else cpt
        return dataclasses.replace(montadas, variaveis={**montadas.variaveis, "dataAtendimento": valor})

    monkeypatch.setattr(mt, "montar_entradas", fixo)


@pytest.mark.parametrize(("carencia", "cpt"), _CONFLITOS)
def test_uniao_com_tipo_ou_valor_diferente_e_conflito(
    pin_2_3: rco_pin.RcoRulesPin,
    metrica: list[str],
    monkeypatch: pytest.MonkeyPatch,
    carencia: object,
    cpt: object,
) -> None:
    """`True == 1` e `1 == 1.0` em Python: a uniao exige o MESMO tipo; nulo contra valor tambem e conflito."""
    _com_valores(monkeypatch, carencia, cpt)
    dmn = _dmn_pior_caso()
    rco_sombra.avaliar_composicao_em_sombra(dmn, GRUPO, _Fatos())
    assert (dmn.calls, metrica) == ([], [rco_sombra.ENTRADAS_EM_CONFLITO])


@pytest.mark.parametrize(("carencia", "cpt"), _UNIOES)
def test_uniao_de_mesmo_tipo_e_valor_avalia(
    pin_2_3: rco_pin.RcoRulesPin,
    metrica: list[str],
    monkeypatch: pytest.MonkeyPatch,
    carencia: object,
    cpt: object,
) -> None:
    _com_valores(monkeypatch, carencia, cpt)
    dmn = _dmn_pior_caso()
    rco_sombra.avaliar_composicao_em_sombra(dmn, GRUPO, _Fatos())
    assert metrica == [rco_sombra.COMPOSICAO_ESCOLHIDA]
    assert [(k, type(v["dataAtendimento"]), v["dataAtendimento"]) for k, v in dmn.calls] == [
        (CARENCIA, type(carencia), carencia),
        (CPT, type(carencia), carencia),
    ]


def test_composicao_ausente_nao_avalia(pin_2_3: rco_pin.RcoRulesPin, metrica: list[str]) -> None:
    dmn = _dmn_pior_caso()
    fatos = _Fatos()
    rco_sombra.avaliar_composicao_em_sombra(dmn, "composicao_que_nao_existe", fatos)
    assert (dmn.calls, fatos.entregues, metrica) == ([], [], [rco_sombra.COMPOSICAO_AUSENTE])


def test_pin_real_fixado_avalia_a_composicao_em_sombra(metrica: list[str]) -> None:
    """O pin real do repositorio (K1 b8444f52, `fixado`): o grupo e avaliado inteiro, membro a membro na
    ordem de restricao, so em sombra (a porta devolve `None`)."""
    assert rco_pin.rco_rules_pin().estado == "fixado"
    dmn = _dmn_pior_caso()
    assert rco_sombra.avaliar_composicao_em_sombra(dmn, GRUPO, _Fatos()) is None  # type: ignore[func-returns-value]
    assert [chave for chave, _ in dmn.calls] == [CARENCIA, CPT]
    assert metrica == [rco_sombra.COMPOSICAO_ESCOLHIDA]


def test_pin_sem_pacote_e_pin_ilegivel(monkeypatch: pytest.MonkeyPatch, metrica: list[str]) -> None:
    dmn = _dmn_pior_caso()
    _pinar(monkeypatch, rco_pin.RcoRulesPin(estado="sem_pacote", tables=MappingProxyType({})))
    rco_sombra.avaliar_composicao_em_sombra(dmn, GRUPO, _Fatos())

    def ilegivel() -> rco_pin.RcoRulesPin:
        raise rco_pin.RcoRulesPinError("pin ilegivel")

    monkeypatch.setattr(rco_pin, "rco_rules_pin", ilegivel)
    rco_sombra.avaliar_composicao_em_sombra(dmn, GRUPO, _Fatos())
    rco_sombra.avaliar_composicao_em_sombra(None, GRUPO, _Fatos())
    assert dmn.calls == []
    assert metrica == [
        rco_sombra.SEM_PACOTE,
        rco_sombra.PIN_INDISPONIVEL,
        rco_sombra.SEM_TRANSPORTE,
    ]


def test_membro_sem_tabela_pinada_e_sem_pacote(
    pin_2_3: rco_pin.RcoRulesPin, metrica: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tabelas = {k: v for k, v in pin_2_3.tables.items() if k != CPT}
    _pinar(monkeypatch, dataclasses.replace(pin_2_3, tables=MappingProxyType(tabelas)))
    dmn = _dmn_pior_caso()
    rco_sombra.avaliar_composicao_em_sombra(dmn, GRUPO, _Fatos())
    assert (dmn.calls, metrica) == ([], [rco_sombra.SEM_PACOTE])


# --- prazo: UM total, repartido entre os membros -----------------------------------------------------------


class _Motor:
    """Transporte que dorme `dorme[decisao]` segundos ou levanta `exc` e guarda o que avaliou."""

    def __init__(self, dorme: dict[str, float] | None = None, exc: BaseException | None = None) -> None:
        self._dorme = dorme or {}
        self._exc = exc
        self.avaliadas: list[str] = []

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        self.avaliadas.append(decision_key)
        await asyncio.sleep(self._dorme.get(decision_key, 0.0))
        if self._exc is not None:
            raise self._exc
        row = ROW_CARENCIA if decision_key == CARENCIA else ROW_CPT
        return [dict(row)], DmnVersion(key=decision_key, id="x", version=1, deployment_id="d")

    async def close(self) -> None:
        return None


def test_prazo_e_repartido_entre_os_membros(
    pin_2_3: rco_pin.RcoRulesPin, metrica: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    prazos: list[float | None] = []
    evaluate_sync = rco_sombra.evaluate_sync

    def espiao(*a: Any, prazo_s: float | None = None, **k: Any) -> Any:
        prazos.append(prazo_s)
        return evaluate_sync(*a, prazo_s=prazo_s, **k)

    monkeypatch.setattr(rco_sombra, "evaluate_sync", espiao)
    rco_sombra.avaliar_composicao_em_sombra(_dmn_pior_caso(), GRUPO, _Fatos())
    assert prazos == [rco_sombra.PRAZO_SOMBRA_S / 2] * 2
    assert sum(p or 0.0 for p in prazos) == rco_sombra.PRAZO_SOMBRA_S
    assert metrica == [rco_sombra.COMPOSICAO_ESCOLHIDA]


@pytest.mark.parametrize("lento", [CARENCIA, CPT])
def test_prazo_esgotado_e_desfecho_fechado(
    pin_2_3: rco_pin.RcoRulesPin, metrica: list[str], monkeypatch: pytest.MonkeyPatch, lento: str
) -> None:
    monkeypatch.setattr(rco_sombra, "PRAZO_SOMBRA_S", 0.1)
    motor = _Motor(dorme={lento: 5.0})
    with structlog.testing.capture_logs() as logs:
        assert rco_sombra.avaliar_composicao_em_sombra(motor, GRUPO, _Fatos(), topico=TOPICO) is None
    assert motor.avaliadas == list(rec.ORDEM[: rec.ORDEM.index(lento) + 1])  # nada depois do esgotado
    assert metrica == [rco_sombra.PRAZO_ESGOTADO]
    assert _logs(logs)[-1]["desfecho"] == rco_sombra.PRAZO_ESGOTADO


def test_falha_esperada_do_motor_e_avaliacao_falhou(pin_2_3: rco_pin.RcoRulesPin, metrica: list[str]) -> None:
    motor = _Motor(exc=DmnEvaluationError("motor fora"))
    assert rco_sombra.avaliar_composicao_em_sombra(motor, GRUPO, _Fatos()) is None
    assert motor.avaliadas == [CARENCIA]
    assert metrica == [rco_sombra.AVALIACAO_FALHOU]


def test_hit_policy_violation_e_avaliacao_falhou(pin_2_3: rco_pin.RcoRulesPin, metrica: list[str]) -> None:
    motor = _Motor(exc=DmnHitPolicyViolationError(CARENCIA))
    assert rco_sombra.avaliar_composicao_em_sombra(motor, GRUPO, _Fatos()) is None
    assert motor.avaliadas == [CARENCIA]
    assert metrica == [rco_sombra.AVALIACAO_FALHOU]


def test_falha_do_motor_na_sombra_conta_a_composicao_com_modo_sombra(
    pin_2_3: rco_pin.RcoRulesPin, metrica: list[str]
) -> None:
    """M1: a revisao humana que `compor` conta numa SOMBRA leva `modo=sombra` no rotulo e no log: nunca se
    confunde com uma composicao de efeito (nada foi encaminhado a humano)."""
    from maezo.platform import observability

    registro = observability.get_metrics_collector().registry
    nome = "maezo_rco_composicao_revisao_humana_total"
    sombra = {"motivo": cmp.MOTIVO_AVALIADOR_FALHOU, "modo": cmp.MODO_SOMBRA}
    efeito = {"motivo": cmp.MOTIVO_AVALIADOR_FALHOU, "modo": cmp.MODO_EFEITO}
    antes_sombra = registro.get_sample_value(nome, sombra) or 0.0
    antes_efeito = registro.get_sample_value(nome, efeito) or 0.0
    with structlog.testing.capture_logs() as logs:
        rco_sombra.avaliar_composicao_em_sombra(_Motor(exc=DmnEvaluationError("motor fora")), GRUPO, _Fatos())
    assert metrica == [rco_sombra.AVALIACAO_FALHOU]
    assert registro.get_sample_value(nome, sombra) == antes_sombra + 1
    assert (registro.get_sample_value(nome, efeito) or 0.0) == antes_efeito
    [evento] = [e for e in logs if str(e["event"]).startswith("rco_composicao")]
    assert (evento["event"], evento["log_level"], evento["modo"]) == (
        "rco_composicao_avaliador_falhou",
        "warning",
        cmp.MODO_SOMBRA,
    )
    assert not any(p in repr(evento) for p in PROIBIDOS)


# --- erro de programacao: contado, logado so o tipo, nunca sobe --------------------------------------------


def test_runtime_error_dentro_de_compor(
    pin_2_3: rco_pin.RcoRulesPin, metrica: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def quebra(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(f"dado de caso {GUIA}")

    monkeypatch.setattr(cmp, "escolher", quebra)
    with structlog.testing.capture_logs() as logs:
        assert (
            rco_sombra.avaliar_composicao_em_sombra(_dmn_pior_caso(), GRUPO, _Fatos(), topico=TOPICO) is None
        )
    assert metrica == [rco_sombra.ERRO_DE_PROGRAMACAO]
    [log] = _logs(logs)
    assert log == {
        "event": "rco_sombra_composicao_erro_de_programacao",
        "log_level": "error",
        "composicao": GRUPO,
        "erro": "RuntimeError",
        "topico": TOPICO,
    }
    _assert_logs_permitidos(_logs(logs))


def test_erro_de_programacao_no_avaliador_nao_vira_revisao_silenciosa(
    pin_2_3: rco_pin.RcoRulesPin, metrica: list[str]
) -> None:
    """`compor` absorve QUALQUER excecao do avaliador como `avaliador_falhou`; a sombra a relanca e conta."""
    motor = _Motor(exc=AttributeError(f"bug {BENEFICIARIO}"))
    with structlog.testing.capture_logs() as logs:
        assert rco_sombra.avaliar_composicao_em_sombra(motor, GRUPO, _Fatos()) is None
    assert metrica == [rco_sombra.ERRO_DE_PROGRAMACAO]
    assert _logs(logs)[-1]["erro"] == "AttributeError"
    _assert_logs_permitidos(_logs(logs))


def test_avaliar_composicao_deixa_o_erro_de_programacao_subir(pin_2_3: rco_pin.RcoRulesPin) -> None:
    def quebra(_versao: tuple[int, int]) -> mt.FatosDoCaso:
        raise TypeError("bug")

    with pytest.raises(TypeError):
        rco_sombra.avaliar_composicao(_dmn_pior_caso(), GRUPO, quebra)


# --- a guarda: membro de grupo nunca avaliado sozinho ------------------------------------------------------


@pytest.mark.parametrize("membro", [CARENCIA, CPT])
def test_guarda_recusa_membro_avaliado_sozinho(
    pin_2_3: rco_pin.RcoRulesPin, metrica: list[str], membro: str
) -> None:
    dmn = _dmn_pior_caso()
    fatos = _Fatos()
    assert rco_sombra.avaliar(dmn, membro, fatos) == rco_sombra.MEMBRO_DE_COMPOSICAO
    rco_sombra.avaliar_em_sombra(dmn, membro, fatos)
    assert (dmn.calls, fatos.entregues) == ([], [])
    assert metrica == [rco_sombra.MEMBRO_DE_COMPOSICAO]


def test_decisao_do_intake_de_contas_nao_e_membro_e_segue_avaliada(
    pin_2_3: rco_pin.RcoRulesPin, metrica: list[str]
) -> None:
    """`contas.identify_glosa_entry` avalia `rco_geral_prazos_conta` sozinha: no K1 2.3.0 real ela nao
    esta em grupo nenhum, entao a guarda nao muda o desfecho dela."""
    assert not cmp.membro_de_composicao(pin_2_3, PRAZOS)
    assert all(PRAZOS not in c.decisoes for c in rec.manifesto().composicoes)
    dmn = FakeDmnTransport()
    dmn.register(PRAZOS, [{"decisao": "liberar", "idRegra": "", "motivoTab38": "", "fila": ""}])
    entrada = contas.GlosaInput(tenant_id="t", numero_lote_tiss="l", tipo_lote="internacao")
    with collect_dmn_versions(), collect_rco_avaliacoes():
        desfecho = rco_sombra.avaliar(dmn, PRAZOS, contas.fatos_prazo_conta_rco(entrada))
    assert desfecho == rco_sombra.AVALIADA
    assert [k for k, _ in dmn.calls] == [PRAZOS]


# --- vocabulario fechado e o indicador de efeito ----------------------------------------------------------


def test_novos_desfechos_estao_no_vocabulario_da_metrica() -> None:
    from maezo.platform.observability import RCO_SOMBRA_DESFECHOS

    novos = {
        rco_sombra.MEMBRO_DE_COMPOSICAO,
        rco_sombra.COMPOSICAO_AUSENTE,
        rco_sombra.ENTRADAS_EM_CONFLITO,
        rco_sombra.COMPOSICAO_ESCOLHIDA,
        rco_sombra.COMPOSICAO_REVISAO_HUMANA,
    }
    assert novos <= rco_sombra.DESFECHOS == RCO_SOMBRA_DESFECHOS


def test_sombra_nao_referencia_o_indicador_de_efeito() -> None:
    fonte = Path(rco_sombra.__file__).read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    proibidos = {"efeito_k1_habilitado", "CHAVE_EFEITO_K1", "MAEZO_RCO_EFEITO_K1", "rco_ativacao"}
    for no in ast.walk(arvore):
        nome = (
            no.id
            if isinstance(no, ast.Name)
            else no.attr
            if isinstance(no, ast.Attribute)
            else no.value
            if isinstance(no, ast.Constant) and isinstance(no.value, str)
            else None
        )
        assert nome not in proibidos, nome
        if isinstance(no, ast.ImportFrom | ast.Import):
            assert all(a.name not in proibidos for a in no.names)
            assert getattr(no, "module", None) != "maezo.platform.rco_ativacao"
    # a docstring do modulo cita o indicador so para dizer que NAO o le: nenhum NOME do codigo o referencia
    assert "MAEZO_RCO_EFEITO_K1" not in fonte.split('"""', 2)[2]
