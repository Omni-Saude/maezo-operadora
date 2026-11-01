"""Motor REAL: o reconciliador do MPTT P3 lendo terminais pela costura do PR #1167.

Contra o CIB Seven de verdade (`docker compose --profile core up`, ADR-0011 — nada de motor
simulado aqui), com duas definicoes AD HOC (nunca em `spec/`) cujos eventos finais tem os MESMOS
ids dos terminais de SP-OP-CRED-001/SP-OP-FRAUDE-001 — o que se prova e' a leitura do historico e a
projecao, nao o fluxo dos processos de producao (esses tem as suas suites em
`tests/integration/processes/`). Prova:

  1. terminal humano de descredenciamento -> linha DESCREDENCIADO com `data_efeito_iso`, por chave
     de negocio; reprocessar o mesmo terminal e' idempotente (`JA_REGISTRADO`);
  2. varredura por filtro de variavel `prestador_id` acha o credenciado (CREDENCIADO) e responde
     `NAO_ENCONTRADO` para o FRAUDE que nao existe;
  3. terminal sem delta (`End_VinculoMantido`) e instancia viva (`EM_CURSO`) e cancelada
     (`EXTERNALLY_TERMINATED`) nao gravam;
  4. terminal fora do contrato v1 e dois ciclos para o mesmo prestador (ambiguo) dao
     `INDISPONIVEL` sem gravar — nunca a primeira linha, nunca um status adivinhado;
  5. FRAUDE confirmado por humano para um prestador -> linha CONFIRMADA;
  6. motor inalcancavel -> `INDISPONIVEL` (falha fechada real, sem dublê).
O ledger e' o dublê em memoria com a semantica do store (a prova Postgres do append-only e da
idempotencia e' `tests/unit/platform/test_prestador_status_ledger_live_pg.py`).
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from maezo.platform.prestador_status.projecao import Dimensao, Origem
from maezo.platform.prestador_status.reconciliador import ReconciliadorStatusPrestador, Resultado
from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
from tests.unit.platform.test_prestador_status_reconciliador import LedgerMemoria

from .conftest import RUN_ID

pytestmark = pytest.mark.integration

_TENANT = "amh"


def _bpmn(process_key: str, rotas: dict[str, str], *, com_espera: bool) -> str:
    """start -> gateway(rota) -> evento final por rota; `espera` leva a uma User Task (instancia viva)."""
    fluxos = []
    fins: dict[str, list[str]] = {}
    for indice, (rota, fim) in enumerate(rotas.items()):
        fluxo = f"Flow_R{indice}"
        fluxos.append(
            f'<bpmn:sequenceFlow id="{fluxo}" sourceRef="GW_Rota" targetRef="{fim}">'
            f"<bpmn:conditionExpression xsi:type=\"bpmn:tFormalExpression\">${{rota == '{rota}'}}"
            "</bpmn:conditionExpression></bpmn:sequenceFlow>"
        )
        fins.setdefault(fim, []).append(fluxo)
    espera = ""
    if com_espera:
        fluxos.append(
            '<bpmn:sequenceFlow id="Flow_Espera" sourceRef="GW_Rota" targetRef="UT_Espera">'
            "<bpmn:conditionExpression xsi:type=\"bpmn:tFormalExpression\">${rota == 'espera'}"
            "</bpmn:conditionExpression></bpmn:sequenceFlow>"
        )
        primeiro_fim = next(iter(rotas.values()))
        fluxos.append(
            f'<bpmn:sequenceFlow id="Flow_EsperaFim" sourceRef="UT_Espera" targetRef="{primeiro_fim}"/>'
        )
        fins[primeiro_fim].append("Flow_EsperaFim")
        espera = (
            '<bpmn:userTask id="UT_Espera" name="espera"><bpmn:incoming>Flow_Espera</bpmn:incoming>'
            "<bpmn:outgoing>Flow_EsperaFim</bpmn:outgoing></bpmn:userTask>"
        )
    eventos = "".join(
        f'<bpmn:endEvent id="{fim}">'
        + "".join(f"<bpmn:incoming>{f}</bpmn:incoming>" for f in entradas)
        + "</bpmn:endEvent>"
        for fim, entradas in fins.items()
    )
    saidas = "".join(
        f"<bpmn:outgoing>{f}</bpmn:outgoing>" for f in [*(f"Flow_R{i}" for i in range(len(rotas)))]
    ) + ("<bpmn:outgoing>Flow_Espera</bpmn:outgoing>" if com_espera else "")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:camunda="http://camunda.org/schema/1.0/bpmn"
                  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                  id="Definitions_{process_key}" targetNamespace="http://maezo.health/test/mptt-p3">
  <bpmn:process id="{process_key}" isExecutable="true" camunda:historyTimeToLive="1">
    <bpmn:startEvent id="Start"><bpmn:outgoing>Flow_Start</bpmn:outgoing></bpmn:startEvent>
    <bpmn:sequenceFlow id="Flow_Start" sourceRef="Start" targetRef="GW_Rota"/>
    <bpmn:exclusiveGateway id="GW_Rota">
      <bpmn:incoming>Flow_Start</bpmn:incoming>{saidas}
    </bpmn:exclusiveGateway>
    {espera}
    {eventos}
    {"".join(fluxos)}
  </bpmn:process>
</bpmn:definitions>
"""


async def _deploy(client: httpx.AsyncClient, process_key: str, xml: str) -> None:
    resp = await client.post(
        "/deployment/create",
        data={
            "deployment-name": f"mptt-p3-{process_key}",
            "enable-duplicate-filtering": "true",
            "deploy-changed-only": "true",
            "tenant-id": _TENANT,
        },
        files={f"{process_key}.bpmn": (f"{process_key}.bpmn", xml, "text/xml")},
    )
    if resp.status_code >= 300:
        raise AssertionError(f"deployment failed: {resp.status_code} {resp.text}")


async def test_reconciliador_le_terminais_do_motor_real(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    cred_key = f"mptt_p3_cred_{RUN_ID}"
    fraude_key = f"mptt_p3_fraude_{RUN_ID}"
    await _deploy(
        engine_client,
        cred_key,
        _bpmn(
            cred_key,
            {
                "cred": "End_PrestadorCredenciado",
                "descred": "End_PrestadorDescredenciado",
                "mantido": "End_VinculoMantido",
                "novo": "End_TerminalForaDoContrato",
            },
            com_espera=True,
        ),
    )
    await _deploy(
        engine_client,
        fraude_key,
        _bpmn(
            fraude_key,
            {"confirmada": "End_FraudeConfirmadaHumano", "monitorar": "End_MonitorarSemAcao"},
            com_espera=False,
        ),
    )

    p_descred, p_cred, p_mantido, p_novo, p_vivo, p_cancel, p_duplo = (
        f"P{RUN_ID}{sufixo}" for sufixo in ("DE", "CR", "MA", "NO", "VI", "CA", "DU")
    )
    transport = CibSevenHttpTransport(engine_base_url, tenant_id=_TENANT)
    ledger = LedgerMemoria()
    rec = ReconciliadorStatusPrestador(
        transport=transport,
        ledger=ledger,
        tenant=_TENANT,
        processos={Origem.CRED: cred_key, Origem.FRAUDE: fraude_key},
    )
    try:

        async def iniciar(
            chave: str, prestador: str, rota: str, processo: str = cred_key, **extra: str
        ) -> str:
            variaveis = {"prestador_id": prestador, "rota": rota, **extra}
            instancia = await transport.start_process_instance(processo, chave, variaveis)
            return instancia.instance_id

        await iniciar(
            f"CRED-{_TENANT}-{p_descred}",
            p_descred,
            "descred",
            data_efeito_iso="2026-12-01",
            tipo_prestador="clinica",
        )
        await iniciar(f"CRED-{_TENANT}-{p_cred}", p_cred, "cred", data_efeito_iso="2026-11-01")
        await iniciar(f"CRED-{_TENANT}-{p_mantido}", p_mantido, "mantido")
        await iniciar(f"CRED-{_TENANT}-{p_novo}", p_novo, "novo")
        await iniciar(f"CRED-{_TENANT}-{p_vivo}", p_vivo, "espera")
        cancelada = await iniciar(f"CRED-{_TENANT}-{p_cancel}", p_cancel, "espera")
        await iniciar(f"CRED-{_TENANT}-{p_duplo}-c1", p_duplo, "cred")
        await iniciar(f"CRED-{_TENANT}-{p_duplo}-c2", p_duplo, "descred")
        await iniciar(
            f"FRAUDE-{_TENANT}-CASO{RUN_ID}",
            p_cred,
            "confirmada",
            processo=fraude_key,
            entidade_tipo="prestador",
        )
        cancel = await engine_client.delete(f"/process-instance/{cancelada}")
        assert cancel.status_code < 300, cancel.text

        # (1) descredenciamento por chave, idempotente.
        r = await rec.reconciliar_cred_por_chave(f"CRED-{_TENANT}-{p_descred}")
        assert r.resultado is Resultado.GRAVADO, r
        assert r.entrada is not None
        assert (r.entrada.dimensao, r.entrada.valor) == (Dimensao.SITUACAO, "DESCREDENCIADO")
        assert r.entrada.data_efeito == date(2026, 12, 1)
        assert r.entrada.tipo_prestador == "clinica"
        assert r.entrada.chave_evento.endswith(":End_PrestadorDescredenciado")
        again = await rec.reconciliar_cred_por_chave(f"CRED-{_TENANT}-{p_descred}")
        assert again.resultado is Resultado.JA_REGISTRADO

        # (2) varredura por filtro de variavel: CRED gravado; FRAUDE deste prestador ainda nao lido.
        res_cred, res_fraude = await rec.reconciliar_prestador(p_mantido)
        assert res_cred.resultado is Resultado.SEM_DELTA  # (3) End_VinculoMantido
        assert res_fraude.resultado is Resultado.NAO_ENCONTRADO
        res_cred, res_fraude = await rec.reconciliar_prestador(p_cred)
        assert res_cred.resultado is Resultado.GRAVADO and res_cred.entrada is not None
        assert res_cred.entrada.valor == "CREDENCIADO"
        # (5) o FRAUDE confirmado do mesmo prestador, achado pela varredura.
        assert res_fraude.resultado is Resultado.GRAVADO and res_fraude.entrada is not None
        assert (res_fraude.entrada.dimensao, res_fraude.entrada.valor) == (Dimensao.FRAUDE, "CONFIRMADA")
        assert (await rec.reconciliar_fraude_por_chave(f"FRAUDE-{_TENANT}-CASO{RUN_ID}")).resultado is (
            Resultado.JA_REGISTRADO
        )

        # (3) viva e cancelada nao gravam.
        assert (
            await rec.reconciliar_cred_por_chave(f"CRED-{_TENANT}-{p_vivo}")
        ).resultado is Resultado.EM_CURSO
        cancel_r = await rec.reconciliar_cred_por_chave(f"CRED-{_TENANT}-{p_cancel}")
        assert cancel_r.resultado is Resultado.SEM_DELTA and cancel_r.motivo == "encerrada_sem_terminal"

        # (4) fora do contrato e ambiguo: INDISPONIVEL, nada gravado.
        novo = await rec.reconciliar_cred_por_chave(f"CRED-{_TENANT}-{p_novo}")
        assert (novo.resultado, novo.motivo) == (Resultado.INDISPONIVEL, "terminal_fora_do_contrato_v1")
        duplo, _ = await rec.reconciliar_prestador(p_duplo)
        assert (duplo.resultado, duplo.motivo) == (
            Resultado.INDISPONIVEL,
            "historico:HistoricInstanceAmbiguousError",
        )

        assert sorted((e.prestador_id, e.valor) for e in ledger.linhas) == sorted(
            [(p_descred, "DESCREDENCIADO"), (p_cred, "CREDENCIADO"), (p_cred, "CONFIRMADA")]
        )
    finally:
        await transport.close()

    # (6) motor inalcancavel: falha fechada real.
    morto = CibSevenHttpTransport("http://127.0.0.1:9/engine-rest", tenant_id=_TENANT, timeout=2.0)
    try:
        rec_morto = ReconciliadorStatusPrestador(transport=morto, ledger=LedgerMemoria(), tenant=_TENANT)
        r = await rec_morto.reconciliar_cred_por_chave(f"CRED-{_TENANT}-{p_descred}")
        assert (r.resultado, r.motivo) == (Resultado.INDISPONIVEL, "historico:HistoryReadUnavailableError")
    finally:
        await morto.close()
