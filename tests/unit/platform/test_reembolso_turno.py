"""Turno do reembolso no WhatsApp (DL-0157/DL-0158): coleta, arquivo, abertura do processo, CAS."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from maezo.domain.reembolso import pedido as rp
from maezo.gateway.reembolso_anexos import AnexoGuardado
from maezo.platform.webhooks.whatsapp.reembolso_store import (
    ColetaReembolsoError,
    MemoriaColetaReembolsoStore,
)
from maezo.platform.webhooks.whatsapp.reembolso_turno import ReembolsoTurno
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.mcp_whatsapp.media import MidiaBaixada, MidiaRecusada, mime_aceito
from maezo.tools.mcp_whatsapp.server import WhatsAppMediaClient
from tests.support.audit_fakes import FakeStartAuditSink

CONV = "wa:amh:hk1_abc123"
PSEUDO = "benef-1"
AGORA = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)


class _Relogio:
    def __init__(self) -> None:
        self.t = AGORA

    def __call__(self) -> datetime:
        return self.t


class _Custodia:
    def __init__(self) -> None:
        self.guardados: list[tuple[str, str, int]] = []

    async def guardar_anexo(self, *, conversation_id: str, mime: str, conteudo: bytes) -> AnexoGuardado:
        self.guardados.append((conversation_id, mime, len(conteudo)))
        return AnexoGuardado(f"doc-{len(self.guardados)}", "a" * 64)


class _Baixador:
    def __init__(self, *, recusa: str | None = None) -> None:
        self.recusa = recusa

    async def baixar(self, media_id: str) -> MidiaBaixada:
        if self.recusa:
            raise MidiaRecusada(self.recusa)  # type: ignore[arg-type]
        return MidiaBaixada(conteudo=b"%PDF-1.4 teste", mime="application/pdf")


class _CibSevenCaido(FakeCibSevenTransport):
    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        raise CibSevenError("motor fora")


class _Gravador(FakeCibSevenTransport):
    def __init__(self) -> None:
        super().__init__()
        self.starts: list[tuple[str, str, dict[str, Any]]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.starts.append((process_key, business_key, dict(variables)))
        return await super().start_process_instance(process_key, business_key, variables)


def _turno(
    *,
    cib: FakeCibSevenTransport | None = None,
    store: MemoriaColetaReembolsoStore | None = None,
    baixador: _Baixador | None = None,
    relogio: _Relogio | None = None,
) -> tuple[ReembolsoTurno, MemoriaColetaReembolsoStore, _Custodia]:
    store = store or MemoriaColetaReembolsoStore()
    custodia = _Custodia()
    turno = ReembolsoTurno(
        tenant_id="amh",
        store=store,
        cibseven=cib or _Gravador(),
        audit_sink=FakeStartAuditSink(),
        custodia=custodia,
        baixador=baixador or _Baixador(),
        agora=relogio or _Relogio(),
    )
    return turno, store, custodia


async def _texto(turno: ReembolsoTurno, texto: str, *, emergencia: bool = False) -> Any:
    return await turno.tratar_texto(
        conversation_id=CONV, texto=texto, beneficiario_pseudo_id=PSEUDO, sinal_de_emergencia=emergencia
    )


async def _ate_a_nota(turno: ReembolsoTurno) -> None:
    for t in ("quero um reembolso", "sim", "titular", "1", "1", "25/10/2026", "150,00"):
        assert await _texto(turno, t) is not None


@pytest.mark.asyncio
async def test_mensagem_de_outro_assunto_nao_e_tratada() -> None:
    turno, store, _ = _turno()
    assert await _texto(turno, "qual o valor da minha mensalidade?") is None
    assert await store.ler(CONV) is None


@pytest.mark.asyncio
async def test_emergencia_vence_e_a_coleta_fica_guardada() -> None:
    turno, store, _ = _turno()
    await _texto(turno, "quero um reembolso")
    assert await _texto(turno, "estou com dor no peito", emergencia=True) is None
    assert (await store.ler(CONV)).pedido.etapa == "tem_nota"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_fluxo_completo_abre_o_processo_com_chave_e_variaveis_do_contrato() -> None:
    cib = _Gravador()
    turno, store, custodia = _turno(cib=cib)
    await _ate_a_nota(turno)
    r = await turno.tratar_arquivo(conversation_id=CONV, media_id="m1", mime_declarado="application/pdf")
    assert r is not None and r.evento == "arquivo_recebido" and custodia.guardados
    r = await _texto(turno, "sim")  # terminou
    assert "Confira" in r.texto
    r = await _texto(turno, "sim")  # confirma
    assert r.evento == "aberto" and rp.protocolo_valido(r.protocolo)
    assert len(cib.starts) == 1
    chave_do_processo, business_key, variaveis = cib.starts[0]
    assert chave_do_processo == "SP-OP-REEMBOLSO-001"
    assert business_key == f"REEMB-amh-{r.protocolo}"
    assert variaveis["aviso_beneficiario_whatsapp"] is True and variaveis["agent_id"] == "lucas"
    assert variaveis["valor_solicitado_cents"] == 15000
    assert (await store.ler(CONV)).protocolo == r.protocolo  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_pedido_repetido_reencontra_o_mesmo_protocolo() -> None:
    cib = _Gravador()
    turno, store, _ = _turno(cib=cib)
    await _ate_a_nota(turno)
    await turno.tratar_arquivo(conversation_id=CONV, media_id="m1", mime_declarado="application/pdf")
    await _texto(turno, "sim")
    primeiro = await _texto(turno, "sim")
    repetido = await _texto(turno, "quero saber do meu reembolso")
    assert repetido.evento == "ja_registrado" and repetido.protocolo == primeiro.protocolo
    assert len(cib.starts) == 1


@pytest.mark.asyncio
async def test_motor_fora_nao_avanca_a_coleta_e_diz_a_verdade() -> None:
    turno, store, _ = _turno(cib=_CibSevenCaido())
    await _ate_a_nota(turno)
    await turno.tratar_arquivo(conversation_id=CONV, media_id="m1", mime_declarado="application/pdf")
    await _texto(turno, "sim")
    r = await _texto(turno, "sim")
    assert r.evento == "start_falhou" and "Nada foi enviado" in r.texto
    assert (await store.ler(CONV)).pedido.etapa == "confirma"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_arquivo_sem_coleta_ativa_nao_e_tratado() -> None:
    turno, _, custodia = _turno()
    assert await turno.tratar_arquivo(conversation_id=CONV, media_id="m1", mime_declarado="image/png") is None
    assert custodia.guardados == []


@pytest.mark.asyncio
async def test_arquivo_fora_da_etapa_de_documentos_nao_e_guardado() -> None:
    turno, _, custodia = _turno()
    await _texto(turno, "quero um reembolso")
    r = await turno.tratar_arquivo(conversation_id=CONV, media_id="m1", mime_declarado="image/png")
    assert r is not None and r.evento == "arquivo_fora_de_hora" and custodia.guardados == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("motivo", "trecho"),
    [("mime", "não é aceito"), ("tamanho", "não é aceito"), ("indisponivel", "Tente enviar")],
)
async def test_arquivo_recusado_pela_midia_nao_e_guardado(motivo: str, trecho: str) -> None:
    turno, _, custodia = _turno(baixador=_Baixador(recusa=motivo))
    await _ate_a_nota(turno)
    r = await turno.tratar_arquivo(conversation_id=CONV, media_id="m1", mime_declarado="image/png")
    assert r is not None and trecho in r.texto and custodia.guardados == []


@pytest.mark.asyncio
async def test_coleta_vencida_recomeca_do_zero() -> None:
    relogio = _Relogio()
    turno, store, _ = _turno(relogio=relogio)
    await _texto(turno, "quero um reembolso")
    await _texto(turno, "sim")
    relogio.t += timedelta(hours=25)
    assert await _texto(turno, "titular") is None  # vencida: so' reembolso recomeca
    r = await _texto(turno, "quero reembolso")
    assert r.evento == "iniciado"
    assert (await store.ler(CONV)).pedido.etapa == "tem_nota"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_novo_pedido_depois_de_aberto_comeca_outra_coleta() -> None:
    turno, store, _ = _turno()
    await _ate_a_nota(turno)
    await turno.tratar_arquivo(conversation_id=CONV, media_id="m1", mime_declarado="application/pdf")
    await _texto(turno, "sim")
    await _texto(turno, "sim")
    r = await _texto(turno, "quero fazer outro reembolso")
    assert r.evento == "iniciado"
    assert (await store.ler(CONV)).pedido.etapa == "tem_nota"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------------------------
# Store em memoria: o CAS e o isolamento do tenant
# ---------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cas_recusa_escrita_com_revisao_velha() -> None:
    store = MemoriaColetaReembolsoStore()
    pedido = rp.iniciar().pedido
    kw: dict[str, Any] = {"protocolo": None, "agora": AGORA, "expira_em": AGORA + timedelta(hours=1)}
    assert await store.gravar(CONV, pedido, revisao_esperada=None, **kw)
    assert not await store.gravar(CONV, pedido, revisao_esperada=None, **kw)  # ja existe
    assert await store.gravar(CONV, pedido, revisao_esperada=0, **kw)
    assert not await store.gravar(CONV, pedido, revisao_esperada=0, **kw)  # revisao velha


@pytest.mark.asyncio
async def test_store_recusa_conversa_de_outro_tenant_e_protocolo_fora_do_aberto() -> None:
    store = MemoriaColetaReembolsoStore()
    with pytest.raises(ColetaReembolsoError):
        await store.ler("wa:outro:hk1_abc")
    with pytest.raises(ColetaReembolsoError):
        await store.gravar(
            CONV,
            rp.iniciar().pedido,
            protocolo="REE-AB12-CD34",
            agora=AGORA,
            expira_em=AGORA + timedelta(hours=1),
            revisao_esperada=None,
        )


# ---------------------------------------------------------------------------------------------
# Download de midia (httpx.MockTransport: o transporte HTTP, nao o motor)
# ---------------------------------------------------------------------------------------------
def _cliente(handler: Any, *, max_bytes: int = 1024) -> WhatsAppMediaClient:
    return WhatsAppMediaClient(
        base_url="https://graph.example/v18.0",
        token="tok",
        max_bytes=max_bytes,
        transport=httpx.MockTransport(handler),
    )


def _meta(**extra: Any) -> dict[str, Any]:
    return {"url": "https://cdn.example/file", "mime_type": "application/pdf", "file_size": 10, **extra}


@pytest.mark.asyncio
async def test_baixa_o_arquivo_em_duas_chamadas_com_o_bearer() -> None:
    vistos: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append((str(request.url), request.headers["authorization"]))
        if request.url.host == "graph.example":
            return httpx.Response(200, json=_meta())
        return httpx.Response(200, content=b"%PDF conteudo")

    midia = await _cliente(handler).baixar("abc123")
    assert midia.conteudo == b"%PDF conteudo" and midia.mime == "application/pdf"
    assert [h for _, h in vistos] == ["Bearer tok", "Bearer tok"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("meta", "corpo", "motivo"),
    [
        (_meta(mime_type="video/mp4"), b"x", "mime"),
        (_meta(file_size=10_000), b"x", "tamanho"),
        (_meta(), b"x" * 2000, "tamanho"),  # o declarado mentiu: o real estoura
        (_meta(url="http://inseguro.example/f"), b"x", "indisponivel"),
        (_meta(), b"", "indisponivel"),
    ],
)
async def test_recusas_da_midia_tem_codigo_fechado(meta: dict[str, Any], corpo: bytes, motivo: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.example":
            return httpx.Response(200, json=meta)
        return httpx.Response(200, content=corpo)

    with pytest.raises(MidiaRecusada) as erro:
        await _cliente(handler).baixar("abc123")
    assert erro.value.motivo == motivo


@pytest.mark.asyncio
async def test_falha_http_nao_vaza_id_nem_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(MidiaRecusada) as erro:
        await _cliente(handler).baixar("abc123")
    assert erro.value.motivo == "indisponivel"
    assert "abc123" not in repr(erro.value) and "graph.example" not in repr(erro.value)
    assert erro.value.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("media_id", ["", "../etc/passwd", "a b", "x?y=1"])
async def test_id_de_midia_malformado_e_recusado_sem_chamar_a_rede(media_id: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - nao deve ser chamado
        raise AssertionError("nenhuma chamada de rede era esperada")

    with pytest.raises(MidiaRecusada) as erro:
        await _cliente(handler).baixar(media_id)
    assert erro.value.motivo == "id_invalido"


def test_token_nunca_aparece_no_repr_e_mime_com_parametro_vale() -> None:
    assert "tok" not in repr(_cliente(lambda r: httpx.Response(200)))
    assert mime_aceito("image/jpeg; charset=binary") and not mime_aceito("text/html")
    with pytest.raises(ValueError):
        WhatsAppMediaClient(base_url="https://x", token="")


# ---------------------------------------------------------------------------------------------
# Resposta a um pedido de informacao da equipe (DL-0159)
# ---------------------------------------------------------------------------------------------
from maezo.tools.mcp_cibseven.transport import HistoricProcessVariables  # noqa: E402


async def _pedido_aberto(
    cib: _Gravador,
) -> tuple[ReembolsoTurno, MemoriaColetaReembolsoStore, _Custodia, str]:
    turno, store, custodia = _turno(cib=cib)
    await _ate_a_nota(turno)
    await turno.tratar_arquivo(conversation_id=CONV, media_id="m1", mime_declarado="application/pdf")
    await _texto(turno, "sim")
    aberto = await _texto(turno, "sim")
    return turno, store, custodia, aberto.protocolo


async def _esperando(cib: _Gravador, protocolo: str, *, aguardando: bool = True) -> str:
    instancia = await cib.find_active_instance(f"REEMB-amh-{protocolo}")
    assert instancia is not None
    cib.seed_historic_variables(
        HistoricProcessVariables(
            instance_id=instancia.instance_id,
            business_key=f"REEMB-amh-{protocolo}",
            process_key="SP-OP-REEMBOLSO-001",
            state="ACTIVE",
            variables={"aguardando_resposta": aguardando},
        )
    )
    return instancia.instance_id


@pytest.mark.asyncio
async def test_sem_pendencia_a_mensagem_segue_o_fluxo_de_sempre() -> None:
    cib = _Gravador()
    turno, store, _, protocolo = await _pedido_aberto(cib)
    await _esperando(cib, protocolo, aguardando=False)
    assert await _texto(turno, "boa tarde, tudo bem?") is None
    assert await turno.tratar_arquivo(conversation_id=CONV, media_id="m9", mime_declarado="image/png") is None
    assert (await store.ler(CONV)).pedido.etapa == "aberto"  # type: ignore[union-attr]
    assert cib.correlate_calls == []


@pytest.mark.asyncio
async def test_resposta_com_texto_e_arquivos_vai_numa_correlacao_so() -> None:
    cib = _Gravador()
    turno, store, custodia, protocolo = await _pedido_aberto(cib)
    await _esperando(cib, protocolo)
    r = await _texto(turno, "O atendimento foi de urgencia, CPF 123.456.789-01")
    assert r.evento == "resposta_iniciada"
    r = await turno.tratar_arquivo(conversation_id=CONV, media_id="m2", mime_declarado="application/pdf")
    assert r is not None and r.evento == "resposta_arquivo"
    r = await turno.tratar_arquivo(conversation_id=CONV, media_id="m3", mime_declarado="application/pdf")
    assert r is not None and r.evento == "resposta_arquivo"
    assert cib.correlate_calls == []  # nada vai antes do "terminei"
    r = await _texto(turno, "terminei")
    assert r.evento == "resposta_enviada" and protocolo in r.texto
    (chamada,) = cib.correlate_calls
    assert chamada["message_name"] == "msg.reembolso.docs_received"
    assert chamada["business_key"] == f"REEMB-amh-{protocolo}"
    variaveis = chamada["variables"]
    assert "123.456.789-01" not in str(variaveis["resposta_beneficiario"])
    assert variaveis["aguardando_resposta"] is False
    assert [d["tipo"] for d in variaveis["documentos_adicionais_refs"]] == ["complemento", "complemento"]
    linha = await store.ler(CONV)
    assert linha is not None and linha.pedido.etapa == "aberto" and linha.pedido.resposta_texto == ""
    assert linha.protocolo == protocolo


@pytest.mark.asyncio
async def test_terminei_sem_nada_nao_correlaciona() -> None:
    cib = _Gravador()
    turno, _, _, protocolo = await _pedido_aberto(cib)
    await _esperando(cib, protocolo)
    r = await _texto(turno, "terminei")
    assert r.evento == "resposta_vazia" and cib.correlate_calls == []


@pytest.mark.asyncio
async def test_resposta_longa_demais_pede_para_resumir() -> None:
    cib = _Gravador()
    turno, store, _, protocolo = await _pedido_aberto(cib)
    await _esperando(cib, protocolo)
    r = await _texto(turno, "x " * 800)
    assert r.evento == "resposta_longa"
    assert (await store.ler(CONV)).pedido.etapa == "aberto"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_motor_fora_na_entrega_nada_se_perde() -> None:
    class _CorrelacaoCaida(_Gravador):
        async def correlate_message(self, *a: Any, **k: Any) -> None:
            raise CibSevenError("motor fora")

    cib = _CorrelacaoCaida()
    turno, store, _, protocolo = await _pedido_aberto(cib)
    await _esperando(cib, protocolo)
    await _texto(turno, "segue a informacao")
    r = await _texto(turno, "terminei")
    assert r.evento == "resposta_falhou"
    linha = await store.ler(CONV)
    assert linha is not None and linha.pedido.etapa == "resposta" and linha.pedido.resposta_texto


@pytest.mark.asyncio
async def test_pendencia_que_acabou_antes_do_terminei_nao_correlaciona() -> None:
    cib = _Gravador()
    turno, store, _, protocolo = await _pedido_aberto(cib)
    await _esperando(cib, protocolo)
    await _texto(turno, "segue a informacao")
    await _esperando(cib, protocolo, aguardando=False)  # a equipe seguiu sem a resposta
    r = await _texto(turno, "terminei")
    assert r.evento == "resposta_sem_pendencia" and cib.correlate_calls == []
    assert (await store.ler(CONV)).pedido.etapa == "aberto"  # type: ignore[union-attr]
