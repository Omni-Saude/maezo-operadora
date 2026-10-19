"""Cancelamento a pedido PESSOA FISICA atras da chave `MAEZO_CANCEL_PF_V1` (DL-0111, SP-OP-CANCEL-001 v0.2.0).

Aprovado pelo dono em 15/10/2026 para uso em dev; revisao juridica/regulatoria, DPO e operacao antes de
producao. Prova, sem modelo, DMN real nem motor real (motor FALSO — nenhuma instancia real):

  * chave DESLIGADA = o Lucas de antes, byte a byte (mesmos textos, mesmas variaveis do processo, mesma
    chave), inclusive quando os fatos do cancelamento chegam na entrada;
  * chave LIGADA: titular PF verificado inicia SP-OP-CANCEL-001 com os fatos REAIS e recebe o protocolo;
    dependente nao inicia (o pedido e' do titular, oferta de pessoa); empresa -> RH; adesao ->
    administradora; identidade nao verificada nao confirma titularidade; pedido repetido -> o mesmo
    protocolo; o portao de dedup nao avaliavel PROPAGA e a falha de start cita SP-OP-CANCEL-001;
  * os fatos vem da AMH (TINA + billing-status) por uma derivacao PURA, sem campo novo;
  * os textos cabem na regua de tom e nunca dizem "cancelado" antes da execucao humana.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.lucas import cancelamento_pf as cpf
from maezo.agents.lucas.graph import (
    _CALLER_INPUT_FIELDS,
    TEXTO_CANCELAMENTO_ADMINISTRADORA,
    TEXTO_CANCELAMENTO_EMPRESA,
    TEXTO_CANCELAMENTO_ENCAMINHADO,
    TEXTO_CONFIRMA_CANCELAMENTO,
    LucasGraph,
    LucasState,
    new_lucas_state,
    ref_de_resposta,
)
from maezo.agents.lucas.politica_atraso import LimiaresDeAtraso
from maezo.ports.errors import PortResult
from maezo.ports.tina import ElegibilidadeView, VinculoPlano
from maezo.qa.simulador.checagens import frases_longas, palavras_proibidas, problemas_de_tom
from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    FakeCibSevenTransport,
    StartDedupGateUnavailableError,
)
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink

_REPO = Path(__file__).resolve().parents[3]
_CONVERSA = "wa:amh:hk1_cancelpf"


class _Inferencia:
    async def generate(self, prompt: str, **kw: Any) -> str:
        del prompt, kw
        return "Narrativa para o humano."


class _Envio:
    def __init__(self) -> None:
        self.textos: list[str] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del to_hash, idempotency_key
        self.textos.append(text)
        return {"ok": True}


def _fatos_pf() -> dict[str, Any]:
    return {
        "status_conciliado": True,
        "ciclos_sem_conciliacao": 0,
        "numero_boleto": "",
        "cnab_ref": "amh-billing:2026-10-15",
        "pagador_tipo": "pessoa_fisica",
    }


def _titular_verificado() -> dict[str, Any]:
    return {
        "cancelamento_titular": True,
        "cancelamento_tipo_plano": "individual",
        "cancelamento_vinculo_ativo": True,
        "cancelamento_identidade_verificada": True,
    }


def _estado(*, confirmacao: str | None = None, pendente: bool = False, **extra: Any) -> LucasState:
    entrada: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": _CONVERSA,
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-cancelpf",
        "to_hash": "hash-cancelpf",
        "intencao": "cancelamento",
        "tipo_solicitacao": "",
        "pedido_cancelamento": True,
        **extra,
    }
    if confirmacao is not None:
        entrada["confirmacao_cancelamento"] = confirmacao
    if pendente:
        entrada["resposta_anterior_ref"] = ref_de_resposta(TEXTO_CONFIRMA_CANCELAMENTO)
    return new_lucas_state(entrada)


def _confirmado(**extra: Any) -> LucasState:
    return _estado(confirmacao="confirma", pendente=True, **extra)


async def _turno(
    estado: LucasState,
    *,
    v1: bool | None,
    motor: FakeCibSevenTransport | None = None,
    sink: Any = None,
) -> tuple[dict[str, Any], _Envio, FakeCibSevenTransport]:
    envio = _Envio()
    motor = FakeCibSevenTransport() if motor is None else motor
    kwargs: dict[str, Any] = {} if v1 is None else {"cancelamento_pf_v1": v1}
    grafo = LucasGraph(
        inference=_Inferencia(),  # type: ignore[arg-type]
        dmn=DmnDraftLocal(),
        cibseven=motor,
        audit_sink=sink or FakeStartAuditSink(),
        whatsapp=envio,
        hoje=lambda: date(2026, 10, 15),
        limiares_atraso=LimiaresDeAtraso(),
        **kwargs,
    )
    final = dict(await grafo.compile_graph().compile().ainvoke(estado))
    return final, envio, motor


# =================================================================================================
# Chave DESLIGADA: o de antes, byte a byte
# =================================================================================================


_ESTADOS_DE_CANCELAMENTO = {
    "pergunta": lambda: _estado(**_fatos_pf()),
    "confirmado": lambda: _confirmado(**_fatos_pf()),
    "desistiu": lambda: _estado(confirmacao="nega", pendente=True, **_fatos_pf()),
    "empresa": lambda: _estado(**{**_fatos_pf(), "pagador_tipo": "pessoa_juridica"}),
    "administradora": lambda: _estado(**{**_fatos_pf(), "cobranca_administradora": True}),
    "pagador_desconhecido": lambda: _estado(),
    "confirmado_com_fatos_v1": lambda: _confirmado(**_fatos_pf(), **_titular_verificado()),
    "dependente_com_fatos_v1": lambda: _confirmado(
        **_fatos_pf(), **{**_titular_verificado(), "cancelamento_titular": False}
    ),
}


def _observavel(final: dict[str, Any], envio: _Envio, motor: FakeCibSevenTransport) -> dict[str, Any]:
    return {
        "textos": list(envio.textos),
        "route": final.get("route"),
        "desfecho": final.get("desfecho"),
        "process_started": final.get("process_started"),
        "business_key": final.get("business_key"),
        "variaveis": {k: dict(v) for k, v in motor._variables.items()},
    }


@pytest.mark.parametrize("caso", sorted(_ESTADOS_DE_CANCELAMENTO))
async def test_chave_desligada_e_byte_a_byte_o_lucas_de_antes(caso: str) -> None:
    """A chave desligada (explicita) e o grafo sem a chave (o de antes) produzem EXATAMENTE o mesmo:
    textos, rota, desfecho, start e variaveis do processo — mesmo com os fatos novos na entrada."""
    padrao = _observavel(*await _turno(_ESTADOS_DE_CANCELAMENTO[caso](), v1=None))
    desligada = _observavel(*await _turno(_ESTADOS_DE_CANCELAMENTO[caso](), v1=False))
    assert desligada == padrao


async def test_chave_desligada_mantem_os_fatos_false_e_o_texto_antigo() -> None:
    final, envio, motor = await _turno(_confirmado(**_fatos_pf(), **_titular_verificado()), v1=False)
    assert final["process_started"] is True
    variaveis = motor._variables[final["business_key"]]
    for campo in ("dentro_prazo", "titularidade_confirmada", "vinculo_ativo", "notificacao_previa_feita"):
        assert variaveis[campo] is False
    assert variaveis["tipo_plano"] == "individual"
    assert "aviso_beneficiario_whatsapp" not in variaveis and "conversation_id" not in variaveis
    assert envio.textos == [TEXTO_CANCELAMENTO_ENCAMINHADO]


async def test_chave_desligada_dependente_ainda_recebe_a_pergunta_de_antes() -> None:
    final, envio, motor = await _turno(
        _estado(**_fatos_pf(), **{**_titular_verificado(), "cancelamento_titular": False}), v1=False
    )
    assert envio.textos == [TEXTO_CONFIRMA_CANCELAMENTO] and not motor._history
    assert final["process_started"] is False


async def test_chave_desligada_portao_de_dedup_continua_virando_falha_de_start() -> None:
    final, envio, _ = await _turno(_confirmado(**_fatos_pf()), v1=False, sink=_SinkSemDedup())
    assert final.get("start_failed") is True and final["process_started"] is False
    assert envio.textos == []


# =================================================================================================
# Chave LIGADA
# =================================================================================================


async def test_titular_pf_verificado_inicia_com_os_fatos_reais_e_recebe_o_protocolo() -> None:
    final, envio, motor = await _turno(_confirmado(**_fatos_pf(), **_titular_verificado()), v1=True)
    assert final["process_started"] is True
    chave = final["business_key"]
    assert chave.startswith("CANCEL-amh-")
    instancia = await motor.find_active_instance(chave)
    assert instancia is not None and instancia.process_key == "SP-OP-CANCEL-001"
    v = motor._variables[chave]
    assert v["tipo_solicitacao"] == "pedido_beneficiario" and v["origem_solicitacao"] == "agente_lucas"
    assert v["tipo_plano"] == "individual"
    assert v["titularidade_confirmada"] is True
    assert v["vinculo_ativo"] is True
    assert v["dentro_prazo"] is True  # RN 412 — a confirmar pelo juridico
    assert v["notificacao_previa_feita"] is False
    assert v["aviso_beneficiario_whatsapp"] is True
    assert v["agent_id"] == "lucas" and v["conversation_id"] == _CONVERSA
    assert v["data_solicitacao_iso"] == "2026-10-15"
    # A identidade no motor continua o pseudonimo (nunca CPF, matricula crua ou telefone).
    assert v["matricula_beneficiario"] == "pseudo-cancelpf" == v["beneficiario_pseudo_id"]
    protocolo = cpf.protocolo_do_cancelamento(final["process_ref"]["instance_id"])
    assert envio.textos == [cpf.TEXTO_PEDIDO_RECEBIDO.format(protocolo=protocolo)]
    assert "cancelad" not in envio.textos[0].lower()


async def test_identidade_nao_verificada_nao_confirma_a_titularidade() -> None:
    fatos = {**_titular_verificado(), "cancelamento_identidade_verificada": False}
    final, _, motor = await _turno(_confirmado(**_fatos_pf(), **fatos), v1=True)
    assert motor._variables[final["business_key"]]["titularidade_confirmada"] is False


async def test_sem_fatos_da_amh_os_fatos_ficam_nao_afirmados_e_o_pedido_vai_a_analise_humana() -> None:
    """So' o pagador PF (sem TINA): titularidade/vinculo `false` e `tipo_plano` vazio -> catch-all da DMN."""
    final, _, motor = await _turno(_confirmado(**_fatos_pf()), v1=True)
    v = motor._variables[final["business_key"]]
    assert (v["titularidade_confirmada"], v["vinculo_ativo"], v["tipo_plano"]) == (False, False, "")


@pytest.mark.parametrize("dependente", ["titular_false", "nao_e_o_pagador"])
async def test_dependente_nao_abre_o_processo_e_recebe_a_oferta_de_pessoa(dependente: str) -> None:
    extra: dict[str, Any] = (
        {**_titular_verificado(), "cancelamento_titular": False}
        if dependente == "titular_false"
        else {"beneficiario_e_pagador": False}
    )
    for estado in (_estado(**_fatos_pf(), **extra), _confirmado(**_fatos_pf(), **extra)):
        final, envio, motor = await _turno(estado, v1=True)
        assert final["route"] == "respond_member" and final["process_started"] is False
        assert envio.textos == [cpf.TEXTO_DEPENDENTE] and not motor._history


@pytest.mark.parametrize(
    ("tipo_plano", "texto"),
    [
        ("coletivo_empresarial", TEXTO_CANCELAMENTO_EMPRESA),
        ("coletivo_adesao", TEXTO_CANCELAMENTO_ADMINISTRADORA),
    ],
)
async def test_coletivo_pelo_tipo_da_amh_responde_rh_ou_administradora(tipo_plano: str, texto: str) -> None:
    fatos = {"cancelamento_tipo_plano": tipo_plano, "cancelamento_titular": True}
    final, envio, motor = await _turno(_confirmado(**_fatos_pf(), **fatos), v1=True)
    assert final["process_started"] is False and not motor._history
    assert envio.textos == [texto]


@pytest.mark.parametrize(("pagador", "texto"), [("pessoa_juridica", TEXTO_CANCELAMENTO_EMPRESA)])
async def test_empresa_pelo_pagador_continua_rh(pagador: str, texto: str) -> None:
    final, envio, _ = await _turno(_estado(**{**_fatos_pf(), "pagador_tipo": pagador}), v1=True)
    assert envio.textos == [texto] and final["process_started"] is False


async def test_plano_individual_pela_amh_conta_como_pf_mesmo_sem_pagador() -> None:
    final, envio, _ = await _turno(_estado(cancelamento_tipo_plano="familiar"), v1=True)
    assert envio.textos == [TEXTO_CONFIRMA_CANCELAMENTO] and final["process_started"] is False


async def test_a_confirmacao_continua_unica_e_obrigatoria() -> None:
    final, envio, motor = await _turno(_estado(**_fatos_pf(), **_titular_verificado()), v1=True)
    assert envio.textos == [TEXTO_CONFIRMA_CANCELAMENTO] and not motor._history
    final, envio, motor = await _turno(
        _estado(confirmacao="confirma", **_fatos_pf(), **_titular_verificado()), v1=True
    )
    assert envio.textos == [TEXTO_CONFIRMA_CANCELAMENTO] and not motor._history


async def test_pedido_repetido_devolve_o_mesmo_protocolo() -> None:
    motor = FakeCibSevenTransport()
    primeiro, envio1, _ = await _turno(
        _confirmado(**_fatos_pf(), **_titular_verificado()), v1=True, motor=motor
    )
    segundo, envio2, _ = await _turno(
        _confirmado(**_fatos_pf(), **_titular_verificado()), v1=True, motor=motor
    )
    assert segundo["process_ref"]["already_existed"] is True
    protocolo = cpf.protocolo_do_cancelamento(primeiro["process_ref"]["instance_id"])
    assert segundo["process_ref"]["instance_id"] == primeiro["process_ref"]["instance_id"]
    assert envio2.textos == [cpf.TEXTO_PEDIDO_JA_REGISTRADO.format(protocolo=protocolo)]
    assert protocolo in envio1.textos[0]


class _SinkSemDedup:
    """Um sink que nao reporta a reivindicacao duravel: o portao de dedup NAO e' avaliavel."""

    async def emit_once(self, record: Any, *, dedup_key: str) -> str:
        del record, dedup_key
        return "hash"


async def test_portao_de_dedup_nao_avaliavel_propaga_com_a_chave_ligada() -> None:
    """Defeito conhecido da rodada 12: virava 'falhou ao iniciar'. E' defeito do controle: propaga alto."""
    with pytest.raises(StartDedupGateUnavailableError):
        await _turno(_confirmado(**_fatos_pf(), **_titular_verificado()), v1=True, sink=_SinkSemDedup())


class _MotorFora(FakeCibSevenTransport):
    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> Any:
        raise CibSevenError("motor fora (teste)")


async def test_falha_de_start_cita_o_processo_de_cancelamento() -> None:
    with capture_logs() as logs:
        final, envio, _ = await _turno(
            _confirmado(**_fatos_pf(), **_titular_verificado()), v1=True, motor=_MotorFora()
        )
    assert final.get("start_failed") is True and envio.textos == []
    falhas = [e for e in logs if e.get("event") == "agent_process_start_failed"]
    assert falhas and {e["process_key"] for e in falhas} == {"SP-OP-CANCEL-001"}


async def test_falha_de_start_com_a_chave_desligada_tambem_cita_o_processo_de_cancelamento() -> None:
    """DL-0130: desligada, o start tentado tambem e' o SP-OP-CANCEL-001 (`_start_cancelamento` nao depende
    da chave). O aviso de antes citava o SP-OP-ESCALATION-001, que nem foi tentado — era o defeito."""
    with capture_logs() as logs:
        final, envio, _ = await _turno(_confirmado(**_fatos_pf()), v1=False, motor=_MotorFora())
    assert final.get("iniciar_cancelamento") is True and final.get("start_failed") is True
    assert envio.textos == []
    falhas = [e for e in logs if e.get("event") == "agent_process_start_failed"]
    assert falhas and {e["process_key"] for e in falhas} == {"SP-OP-CANCEL-001"}


def test_os_fatos_novos_sao_entradas_classificadas_e_sem_texto() -> None:
    novos = {
        "cancelamento_titular",
        "cancelamento_tipo_plano",
        "cancelamento_vinculo_ativo",
        "cancelamento_identidade_verificada",
    }
    assert novos <= _CALLER_INPUT_FIELDS
    assert not [k for k in novos if re.search(r"body|texto|text|mensagem|conteudo|message|corpo|fala", k)]


# =================================================================================================
# Os fatos: derivacao pura do que a AMH entregou (contratos ja' pinados)
# =================================================================================================


def _vinculo(**kw: Any) -> VinculoPlano:
    base: dict[str, Any] = {
        "carteirinha_mascarada": "******4567",
        "plano": "Plano sintetico",
        "registro_ans_plano": None,
        "segmentacao": None,
        "acomodacao": None,
        "tipo_contratacao": "Individual/Familiar",
        "vigencia_inicio": "2020-01-01",
        "cancelamento": None,
        "atendimento_liberado": True,
        "situacao_vinculo": "Apto",
        "situacao_contrato": "Ativo",
        "titular": True,
        "relacao_dependencia": None,
        "titular_ref": None,
    }
    base.update(kw)
    return VinculoPlano(**base)


def _view(*vinculos: VinculoPlano, ativo: bool = True) -> ElegibilidadeView:
    return ElegibilidadeView(
        portable_subject_ref="ref-sintetica",
        ativo=ativo,
        vinculos=vinculos,
        fonte_atualizada_em="2026-10-15T00:00:00Z",
        campos_ausentes=frozenset(),
    )


def test_titular_individual_apto_e_verificado() -> None:
    fatos = cpf.derivar_fatos(_view(_vinculo()), identidade_verificada=True)
    assert fatos == cpf.FatosCancelamento(
        titular=True, tipo_plano="individual", vinculo_ativo=True, identidade_verificada=True
    )
    assert fatos.como_entrada_lucas() == {
        "cancelamento_identidade_verificada": True,
        "cancelamento_titular": True,
        "cancelamento_tipo_plano": "individual",
        "cancelamento_vinculo_ativo": True,
    }


@pytest.mark.parametrize(
    ("billing", "texto", "esperado"),
    [
        ("individual_familiar", "Coletivo empresarial", "individual"),  # o billing-status manda
        ("coletivo_empresarial", None, "coletivo_empresarial"),
        ("coletivo_adesao", None, "coletivo_adesao"),
        ("desconhecido", "Coletivo por adesão", "coletivo_adesao"),  # reserva: o texto do Tasy
        (None, "Coletivo Empresarial", "coletivo_empresarial"),
        (None, "Familiar", "familiar"),
        (None, "Plano X", None),
    ],
)
def test_tipo_do_plano(billing: str | None, texto: str | None, esperado: str | None) -> None:
    fatos = cpf.derivar_fatos(_view(_vinculo(tipo_contratacao=texto)), tipo_contratacao_billing=billing)
    assert fatos.tipo_plano == esperado


def test_dependente_e_vinculo_cancelado_e_situacao_nao_apta() -> None:
    assert cpf.derivar_fatos(_view(_vinculo(titular=False))).titular is False
    cancelado = cpf.derivar_fatos(_view(_vinculo(cancelamento="2026-01-31"), ativo=False))
    assert cancelado.vinculo_ativo is False and cancelado.titular is None
    assert cpf.derivar_fatos(_view(_vinculo(situacao_vinculo="Inadimplente"))).vinculo_ativo is False
    assert cpf.derivar_fatos(_view(_vinculo(situacao_vinculo=None))).vinculo_ativo is None


def test_dois_vinculos_ativos_que_divergem_nao_afirmam_nada() -> None:
    fatos = cpf.derivar_fatos(
        _view(_vinculo(), _vinculo(titular=False, tipo_contratacao="Coletivo empresarial"))
    )
    assert (fatos.titular, fatos.tipo_plano) == (None, None)
    assert "cancelamento_titular" not in fatos.como_entrada_lucas()


def test_sem_elegibilidade_so_o_tipo_do_billing() -> None:
    fatos = cpf.derivar_fatos(None, tipo_contratacao_billing="individual_familiar")
    assert fatos == cpf.FatosCancelamento(tipo_plano="individual")


class _Tina:
    def __init__(self, view: ElegibilidadeView | None, *, falha: bool = False) -> None:
        self.view = view
        self.falha = falha
        self.chamadas: list[dict[str, Any]] = []

    async def get_elegibilidade(self, ref: str, **kw: Any) -> PortResult[ElegibilidadeView]:
        self.chamadas.append({"ref": ref, **kw})
        if self.falha:
            raise TimeoutError("tina fora")
        assert self.view is not None
        return PortResult.ok(self.view)


class _Resolvedor:
    identidade_verificada = True

    async def portable_ref(self, pseudo_id: str, *, phone_hash: str | None) -> str | None:
        return "ref-sintetica" if pseudo_id else None


class _Base:
    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None:
        return "base-legal:execucao-de-contrato"


async def test_fonte_amh_le_a_elegibilidade_do_sujeito_verificado() -> None:
    tina = _Tina(_view(_vinculo()))
    fonte = cpf.FonteCancelamentoAmh(
        tina=tina, resolvedor=_Resolvedor(), consentimento=_Base(), purpose_of_use="p"
    )
    fatos = await fonte.fatos("pseudo", phone_hash=None, tipo_contratacao_billing="individual_familiar")
    assert isinstance(fatos, cpf.FatosCancelamento)
    assert fatos.identidade_verificada is True and fatos.titular is True
    assert tina.chamadas[0]["consent_decision_ref"] == "base-legal:execucao-de-contrato"


async def test_fonte_amh_fora_nunca_levanta() -> None:
    fonte = cpf.FonteCancelamentoAmh(
        tina=_Tina(None, falha=True), resolvedor=_Resolvedor(), consentimento=_Base(), purpose_of_use="p"
    )
    assert await fonte.fatos("pseudo", phone_hash=None) == cpf.FatosIndisponiveis("fonte_indisponivel")
    assert await fonte.fatos("", phone_hash=None) == cpf.FatosIndisponiveis("pseudo_id_ausente")


# =================================================================================================
# Protocolo e textos (aprovados pelo dono para dev em 15/10/2026; revisao juridica antes de producao)
# =================================================================================================


def test_o_script_de_operacao_deriva_o_mesmo_protocolo_e_nunca_rescinde() -> None:
    """`scripts/ops/cancel_pf_dev.py` repete a derivacao (a task da VPC nao tem o pacote): nunca diverge."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "cancel_pf_dev", _REPO / "scripts" / "ops" / "cancel_pf_dev.py"
    )
    assert spec is not None and spec.loader is not None
    ops = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops)
    for iid in ("3f0e9a8c-1111-2222-3333-444455556666", "simulador-1"):
        assert ops.protocolo(iid) == cpf.protocolo_do_cancelamento(iid)
    assert {"RESCINDIR", "SUSPENDER"}.isdisjoint(ops.DECISOES_DE_TESTE)


def test_protocolo_curto_estavel_e_sem_o_id() -> None:
    p = cpf.protocolo_do_cancelamento("3f0e9a8c-1111-2222-3333-444455556666")
    assert re.fullmatch(r"CAN-[0-9A-F]{4}-[0-9A-F]{4}", p) and cpf.protocolo_valido(p)
    assert p == cpf.protocolo_do_cancelamento("3f0e9a8c-1111-2222-3333-444455556666")
    assert p != cpf.protocolo_do_cancelamento("outra-instancia")
    assert "3f0e" not in p.lower()
    with pytest.raises(ValueError):
        cpf.protocolo_do_cancelamento("")


_P = "CAN-1A2B-3C4D"
_TEXTOS = {
    "recebido": cpf.TEXTO_PEDIDO_RECEBIDO.format(protocolo=_P),
    "ja_registrado": cpf.TEXTO_PEDIDO_JA_REGISTRADO.format(protocolo=_P),
    "dependente": cpf.TEXTO_DEPENDENTE,
    "executado": cpf.texto_do_retorno(cpf.RESULTADO_EXECUTADO, protocolo=_P, data_efeito_iso="2026-10-15"),
    "info": cpf.texto_do_retorno(cpf.RESULTADO_INFO_SOLICITADA, protocolo=_P),
    "mantido": cpf.texto_do_retorno(cpf.RESULTADO_MANTIDO, protocolo=_P),
}


@pytest.mark.parametrize("nome", sorted(_TEXTOS))
def test_textos_na_regua_de_tom(nome: str) -> None:
    texto = _TEXTOS[nome]
    assert frases_longas(texto) == [], "regua de tom: frase com mais de 20 palavras"
    assert problemas_de_tom(texto) == []


@pytest.mark.parametrize("nome", ["recebido", "ja_registrado", "dependente", "info", "mantido"])
def test_so_o_retorno_executado_diz_cancelado(nome: str) -> None:
    """'Cancelado' so' depois da execucao humana conferida no historico; nenhum outro texto o diz."""
    assert palavras_proibidas(_TEXTOS[nome]) == []
    assert "cancelado" not in _TEXTOS[nome].lower()


def test_o_retorno_executado_traz_a_data_e_o_protocolo() -> None:
    assert _TEXTOS["executado"].startswith("Seu plano foi cancelado a partir de 15/10/2026.")
    assert _P in _TEXTOS["executado"]


@pytest.mark.parametrize("data", [None, "", "15/10/2026", "2026-02-30", "20261015"])
def test_retorno_executado_sem_data_valida_nao_inventa(data: object) -> None:
    with pytest.raises(ValueError):
        cpf.texto_do_retorno(cpf.RESULTADO_EXECUTADO, protocolo=_P, data_efeito_iso=data)


def test_retorno_fora_do_contrato_ou_sem_protocolo_recusa() -> None:
    with pytest.raises(ValueError):
        cpf.texto_do_retorno("devolvido_agente", protocolo=_P)
    with pytest.raises(ValueError):
        cpf.texto_do_retorno(cpf.RESULTADO_MANTIDO, protocolo="CAN-x")


@pytest.mark.parametrize("nome", ["info", "mantido", "dependente"])
def test_oferta_de_pessoa_sem_prometer_ligacao(nome: str) -> None:
    texto = _TEXTOS[nome].lower()
    assert "pessoa" in texto or "equipe" in texto
    assert not re.search(r"vai (te )?(ligar|entrar em contato)|retornaremos", texto)


# =================================================================================================
# A chave: default desligado, ligada so' no dev pelo Terraform
# =================================================================================================


def test_a_chave_e_desligada_por_padrao() -> None:
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

    campo = WhatsAppWebhookSettings.model_fields["cancel_pf_v1"]
    assert campo.default is False
    assert "MAEZO_CANCEL_PF_V1" in str(campo.validation_alias)


def test_a_chave_so_e_ligada_no_dev_pelo_terraform() -> None:
    envs = _REPO / "deploy" / "aws-ecs" / "envs"
    dev = envs / "dev-sa-east-1"
    tfvars = (dev / "lucas.auto.tfvars").read_text(encoding="utf-8")
    assert re.search(r"^cancel_pf_v1 = true$", tfvars, re.MULTILINE)
    receptor = (dev / "service-webhook-receiver.tf").read_text(encoding="utf-8")
    assert '{ name = "MAEZO_CANCEL_PF_V1", value = tostring(var.cancel_pf_v1) }' in receptor
    variaveis = (dev / "variables.tf").read_text(encoding="utf-8")
    bloco = variaveis.split('variable "cancel_pf_v1"', 1)[1].split("\n}\n", 1)[0]
    assert "default     = false" in bloco
    assert 'var.lucas_fonte_cobranca == "amh"' in bloco
    for outro in envs.iterdir():
        if outro.is_dir() and outro.name != "dev-sa-east-1":
            for arquivo in outro.rglob("*.tf*"):
                assert "cancel_pf_v1" not in arquivo.read_text(encoding="utf-8"), arquivo


# =================================================================================================
# Recuo com a confirmacao pendente (defeito do roteiro `cancelamento_v1`, 15/10/2026)
# =================================================================================================


@pytest.mark.parametrize("fatos", ["titular", "sem_fatos_da_amh", "dependente", "empresa"])
async def test_desistencia_com_pendencia_limpa_e_diz_que_nada_foi_registrado(fatos: str) -> None:
    extra: dict[str, Any] = {
        "titular": _titular_verificado(),
        "sem_fatos_da_amh": {},
        "dependente": {**_titular_verificado(), "cancelamento_titular": False},
        "empresa": {"cancelamento_tipo_plano": "coletivo_empresarial"},
    }[fatos]
    estado = _estado(confirmacao="nega", pendente=True, **_fatos_pf(), **extra)
    final, envio, motor = await _turno(estado, v1=True)
    assert envio.textos == [cpf.TEXTO_PEDIDO_NAO_REGISTRADO]
    assert final["process_started"] is False and not motor._history  # nenhum processo, nenhum caso
    # A pendencia some: a memoria do turno deixa de ser a da pergunta (um "sim" depois nao confirma nada).
    assert final["resposta_memoria_ref"] != ref_de_resposta(TEXTO_CONFIRMA_CANCELAMENTO)


def test_texto_da_desistencia_cabe_na_regua_de_tom_e_nao_pressiona() -> None:
    texto = cpf.TEXTO_PEDIDO_NAO_REGISTRADO
    assert "nenhum pedido de cancelamento" in texto and "mudar de ideia" in texto
    assert not frases_longas(texto) and not palavras_proibidas(texto) and not problemas_de_tom(texto)
    for proibida in ("desconto", "promoc", "multa", "cancelado"):
        assert proibida not in texto.lower()


async def test_sim_sem_a_pergunta_pendente_nao_inicia_nada_depois_da_desistencia() -> None:
    """Depois da desistencia a pendencia some: "sim" SEM a pergunta pendente nao inicia nada."""
    final, envio, motor = await _turno(
        _estado(confirmacao="confirma", **_fatos_pf(), **_titular_verificado()), v1=True
    )
    assert envio.textos == [TEXTO_CONFIRMA_CANCELAMENTO] and not motor._history
    assert final["process_started"] is False


async def test_sim_quero_cancelar_com_pendencia_continua_iniciando_o_pedido() -> None:
    final, envio, _ = await _turno(_confirmado(**_fatos_pf(), **_titular_verificado()), v1=True)
    assert final["process_started"] is True
    assert envio.textos and envio.textos[0].startswith("Recebemos o seu pedido de cancelamento")


async def test_nao_sem_pendencia_segue_o_fluxo_normal_e_pergunta() -> None:
    final, envio, motor = await _turno(
        _estado(confirmacao="nega", **_fatos_pf(), **_titular_verificado()), v1=True
    )
    assert envio.textos == [TEXTO_CONFIRMA_CANCELAMENTO] and not motor._history
    assert final["process_started"] is False


@pytest.mark.parametrize("v1", [None, False])
async def test_chave_desligada_a_desistencia_mantem_o_texto_antigo(v1: bool | None) -> None:
    from maezo.agents.lucas.graph import TEXTO_CANCELAMENTO_DESISTIU

    _, envio, motor = await _turno(
        _estado(confirmacao="nega", pendente=True, **_fatos_pf(), **_titular_verificado()), v1=v1
    )
    assert envio.textos == [TEXTO_CANCELAMENTO_DESISTIU] and not motor._history
