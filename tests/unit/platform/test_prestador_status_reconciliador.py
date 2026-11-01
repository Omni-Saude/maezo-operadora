"""MPTT P3 — reconciliador: projecao deterministica e falha FECHADA (unidade, sem motor).

O historico vem do dublê rotulado `FakeCibSevenTransport` (mesmo seletor validado e mesmos erros
de nao-encontrado/ambiguo da costura do #1167); a prova contra o motor REAL esta em
`tests/integration/test_prestador_status_reconciliador_engine.py`. O ledger aqui e' um dublê em
memoria com a MESMA semantica de idempotencia do store Postgres (provada ao vivo em
`test_prestador_status_ledger_live_pg.py`).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from maezo.platform.prestador_status.ledger import (
    EntradaLedger,
    LedgerConflitoError,
    LedgerIndisponivelError,
    SituacaoLida,
    ler_situacao,
)
from maezo.platform.prestador_status.projecao import Dimensao, Origem
from maezo.platform.prestador_status.reconciliador import (
    NOMES_CRED,
    NOMES_FRAUDE,
    ReconciliadorStatusPrestador,
    Resultado,
)
from maezo.tools.mcp_cibseven.transport import (
    FakeCibSevenTransport,
    HistoricInstanceRecord,
    HistoryPayloadMalformedError,
    HistoryReadUnavailableError,
)
from maezo.tools.workers.phi_vars import PHI_FREE_TEXT_VARS, PHI_PROCESS_VARS

T = "amh"


class LedgerMemoria:
    """Dublê com a semantica do store: append-only, idempotente por chave, conflito LOUD."""

    def __init__(self, *, falhar: bool = False) -> None:
        self.linhas: list[EntradaLedger] = []
        self.falhar = falhar

    async def registrar(self, entrada: EntradaLedger) -> bool:
        if self.falhar:
            raise LedgerIndisponivelError("fora do ar")
        for existente in self.linhas:
            if existente.tenant == entrada.tenant and existente.chave_evento == entrada.chave_evento:
                if existente != entrada:
                    raise LedgerConflitoError(entrada.chave_evento)
                return False
        self.linhas.append(entrada)
        return True

    async def historico(self, tenant: str, prestador_id: str) -> tuple[EntradaLedger, ...]:
        if self.falhar:
            raise LedgerIndisponivelError("fora do ar")
        return tuple(e for e in self.linhas if e.tenant == tenant and e.prestador_id == prestador_id)

    async def prestadores(self, tenant: str) -> tuple[str, ...]:
        if self.falhar:
            raise LedgerIndisponivelError("fora do ar")
        return tuple(sorted({e.prestador_id for e in self.linhas if e.tenant == tenant}))


class TransporteQuebrado(FakeCibSevenTransport):
    def __init__(self, erro: Exception) -> None:
        super().__init__()
        self.erro = erro

    async def resolve_historic_instance(self, process_key: str, **_: Any) -> HistoricInstanceRecord:
        raise self.erro


def _cred(
    *,
    instance_id: str = "inst-c1",
    prestador: str = "P-001",
    fim: str | None = "End_PrestadorDescredenciado",
    estado: str = "COMPLETED",
    bk: str | None = None,
    **variaveis: Any,
) -> HistoricInstanceRecord:
    base: dict[str, Any] = {
        "prestador_id": prestador,
        "data_efeito_iso": "2026-12-01",
        "tipo_prestador": "clinica",
    }
    base.update(variaveis)
    return HistoricInstanceRecord(
        instance_id=instance_id,
        business_key=bk or f"CRED-{T}-{prestador}",
        process_key="SP-OP-CRED-001",
        state=estado,
        end_activity_id=fim,
        variables=base,
    )


def _fraude(
    *,
    instance_id: str = "inst-f1",
    prestador: str = "P-001",
    fim: str | None = "End_FraudeConfirmadaHumano",
    entidade: str = "prestador",
    caso: str = "CASO-9",
) -> HistoricInstanceRecord:
    return HistoricInstanceRecord(
        instance_id=instance_id,
        business_key=f"FRAUDE-{T}-{caso}",
        process_key="SP-OP-FRAUDE-001",
        state="COMPLETED",
        end_activity_id=fim,
        variables={"prestador_id": prestador, "entidade_tipo": entidade},
    )


def _rec(
    *registros: HistoricInstanceRecord, ledger: LedgerMemoria | None = None
) -> tuple[Any, LedgerMemoria]:
    transport = FakeCibSevenTransport()
    for r in registros:
        transport.seed_historic_instance_record(r)
    led = ledger or LedgerMemoria()
    return ReconciliadorStatusPrestador(transport=transport, ledger=led, tenant=T), led


# -- projecao feliz e idempotencia --------------------------------------------------------------


async def test_descredenciamento_humano_vira_delta_com_data_de_efeito() -> None:
    rec, led = _rec(_cred())
    r = await rec.reconciliar_cred_por_chave(f"CRED-{T}-P-001")
    assert r.resultado is Resultado.GRAVADO
    (linha,) = led.linhas
    assert (linha.origem, linha.dimensao, linha.valor) == (Origem.CRED, Dimensao.SITUACAO, "DESCREDENCIADO")
    assert linha.data_efeito == date(2026, 12, 1)
    assert linha.chave_evento == "CRED:inst-c1:End_PrestadorDescredenciado"
    assert linha.fonte_ref == "inst-c1" and linha.evidencia_ref == f"CRED-{T}-P-001"
    assert linha.tipo_prestador == "clinica"
    assert (await ler_situacao(led, T, "P-001")).situacao is SituacaoLida.DESCREDENCIADO


async def test_reprocessar_o_mesmo_terminal_e_idempotente() -> None:
    rec, led = _rec(_cred())
    assert (await rec.reconciliar_cred_por_chave(f"CRED-{T}-P-001")).resultado is Resultado.GRAVADO
    assert (await rec.reconciliar_cred_por_chave(f"CRED-{T}-P-001")).resultado is Resultado.JA_REGISTRADO
    (res_cred, _) = await rec.reconciliar_prestador("P-001")  # varredura cai na MESMA linha
    assert res_cred.resultado is Resultado.JA_REGISTRADO
    assert len(led.linhas) == 1


async def test_fraude_confirmada_do_prestador_vira_delta() -> None:
    rec, led = _rec(_fraude())
    r = await rec.reconciliar_fraude_por_chave(f"FRAUDE-{T}-CASO-9")
    assert r.resultado is Resultado.GRAVADO
    assert (led.linhas[0].dimensao, led.linhas[0].valor) == (Dimensao.FRAUDE, "CONFIRMADA")
    assert led.linhas[0].tipo_prestador is None  # FRAUDE nao traz atributo cadastral


async def test_caso_fraude_de_beneficiario_esta_fora_de_escopo() -> None:
    rec, led = _rec(_fraude(entidade="beneficiario"))
    r = await rec.reconciliar_fraude_por_chave(f"FRAUDE-{T}-CASO-9")
    assert r.resultado is Resultado.FORA_DE_ESCOPO
    assert led.linhas == []


@pytest.mark.parametrize(
    "fim", ["End_VinculoMantido", "End_CredenciamentoNegado", "End_DecredBloqueadoNaoHumano"]
)
async def test_terminal_sem_delta_nao_grava(fim: str) -> None:
    rec, led = _rec(_cred(fim=fim))
    assert (await rec.reconciliar_cred_por_chave(f"CRED-{T}-P-001")).resultado is Resultado.SEM_DELTA
    assert led.linhas == []


@pytest.mark.parametrize("estado", ["ACTIVE", "SUSPENDED"])
async def test_instancia_viva_e_em_curso_e_nao_grava(estado: str) -> None:
    rec, led = _rec(_cred(estado=estado, fim=None))
    assert (await rec.reconciliar_cred_por_chave(f"CRED-{T}-P-001")).resultado is Resultado.EM_CURSO
    assert led.linhas == []


async def test_instancia_cancelada_nao_produziu_terminal() -> None:
    rec, led = _rec(_cred(estado="EXTERNALLY_TERMINATED", fim=None))
    assert (await rec.reconciliar_cred_por_chave(f"CRED-{T}-P-001")).resultado is Resultado.SEM_DELTA
    assert led.linhas == []


async def test_nao_encontrado_so_quando_o_motor_responde_que_nao_ha() -> None:
    rec, led = _rec()
    assert (await rec.reconciliar_cred_por_chave(f"CRED-{T}-P-404")).resultado is Resultado.NAO_ENCONTRADO
    assert led.linhas == []


# -- falha FECHADA: nada gravado, sinal INDISPONIVEL --------------------------------------------


@pytest.mark.parametrize(
    "erro",
    [
        HistoryReadUnavailableError("motor fora"),
        HistoryPayloadMalformedError("resposta contraditoria"),
    ],
)
async def test_historico_ilegivel_nao_grava_e_sinaliza_indisponivel(erro: Exception) -> None:
    led = LedgerMemoria()
    rec = ReconciliadorStatusPrestador(transport=TransporteQuebrado(erro), ledger=led, tenant=T)
    r = await rec.reconciliar_cred_por_chave(f"CRED-{T}-P-001")
    assert r.resultado is Resultado.INDISPONIVEL
    assert r.motivo == f"historico:{type(erro).__name__}"
    assert led.linhas == []


async def test_ambiguidade_na_varredura_e_indisponivel_nunca_a_primeira_linha() -> None:
    rec, led = _rec(
        _cred(instance_id="a", bk=f"CRED-{T}-P-001-p1"), _cred(instance_id="b", bk=f"CRED-{T}-P-001-p2")
    )
    res_cred, _ = await rec.reconciliar_prestador("P-001")
    assert res_cred.resultado is Resultado.INDISPONIVEL
    assert res_cred.motivo == "historico:HistoricInstanceAmbiguousError"
    assert led.linhas == []


@pytest.mark.parametrize(
    ("registro", "motivo"),
    [
        (_cred(fim="End_TerminalNovoNoBpmn"), "terminal_fora_do_contrato_v1"),
        (_cred(fim="End_RiscoSlaNotificado"), "evento_final_nao_terminal"),
        (_cred(fim=None), "concluida_sem_evento_final"),
        (_cred(estado="ESTADO_MARCIANO"), "estado_desconhecido:ESTADO_MARCIANO"),
        (_cred(data_efeito_iso="01/12/2026"), "data_efeito_iso_malformada"),
        (_cred(data_efeito_iso="2026-13-40"), "data_efeito_iso_malformada"),
        (_cred(prestador_id=None), "prestador_id_ausente_ou_malformado"),
        (_cred(bk="CRED-outro-P-001"), "chave_de_outro_tenant_ou_processo"),
    ],
)
async def test_leitura_incoerente_e_recusada_sem_gravar(
    registro: HistoricInstanceRecord, motivo: str
) -> None:
    rec, led = _rec(registro)
    r = (
        await rec.reconciliar_cred_por_chave(registro.business_key)
        if registro.business_key.startswith(f"CRED-{T}-")
        else await rec.reconciliar_prestador("P-001")
    )
    resultado = r if not isinstance(r, tuple) else r[0]
    assert resultado.resultado is Resultado.INDISPONIVEL
    assert resultado.motivo == motivo
    assert led.linhas == []


async def test_ledger_fora_do_ar_e_indisponivel() -> None:
    rec, led = _rec(_cred(), ledger=LedgerMemoria(falhar=True))
    r = await rec.reconciliar_cred_por_chave(f"CRED-{T}-P-001")
    assert r.resultado is Resultado.INDISPONIVEL
    assert r.motivo == "ledger:LedgerIndisponivelError"
    assert (await ler_situacao(led, T, "P-001")).situacao is SituacaoLida.INDISPONIVEL


async def test_varredura_sem_universo_e_um_unico_indisponivel() -> None:
    rec, _ = _rec(ledger=LedgerMemoria(falhar=True))
    (r,) = await rec.varrer()
    assert r.resultado is Resultado.INDISPONIVEL and r.motivo == "ledger_indisponivel"


async def test_varredura_percorre_o_universo_do_ledger() -> None:
    rec, led = _rec(_cred(prestador="P-002", instance_id="c2"))
    await led.registrar(
        EntradaLedger(
            tenant=T,
            prestador_id="P-002",
            origem=Origem.BASELINE_AMH,
            dimensao=Dimensao.SITUACAO,
            valor="CREDENCIADO",
            chave_evento="BASELINE_AMH:" + "a" * 64 + ":P-002",
            fonte_ref="a" * 64,
            evidencia_ref="amh/rede/baseline-2026.pdf",
            versao_projecao="mptt-p3-projecao/v1",
        )
    )
    assert (await ler_situacao(led, T, "P-002")).situacao is SituacaoLida.CREDENCIADO
    resultados = await rec.varrer()
    assert [(r.origem, r.resultado) for r in resultados] == [
        (Origem.CRED, Resultado.GRAVADO),
        (Origem.FRAUDE, Resultado.NAO_ENCONTRADO),
    ]
    # O delta do CRED (descredenciado) vence a baseline na leitura: ordem de registro.
    assert (await ler_situacao(led, T, "P-002")).situacao is SituacaoLida.DESCREDENCIADO


# -- guardas de construcao/seletor --------------------------------------------------------------


async def test_chave_de_outro_tenant_e_erro_de_programacao() -> None:
    rec, _ = _rec()
    with pytest.raises(ValueError):
        await rec.reconciliar_cred_por_chave("CRED-outro-P-001")
    with pytest.raises(ValueError):
        await rec.reconciliar_fraude_por_chave(f"CRED-{T}-P-001")
    with pytest.raises(ValueError):
        await rec.reconciliar_prestador("P 001; drop")


def test_sem_a_costura_do_1167_o_reconciliador_nao_nasce() -> None:
    with pytest.raises(TypeError):
        ReconciliadorStatusPrestador(transport=object(), ledger=LedgerMemoria(), tenant=T)  # type: ignore[arg-type]


def test_nomes_lidos_sao_cadastrais_nunca_phi() -> None:
    for nome in (*NOMES_CRED, *NOMES_FRAUDE):
        assert nome not in PHI_PROCESS_VARS and nome not in PHI_FREE_TEXT_VARS
        assert not nome.startswith("dossie_")
    assert "beneficiario_pseudo_id" not in NOMES_FRAUDE
