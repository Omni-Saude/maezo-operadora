"""E-SF-13 / GTM-E-41 — OutboundGate: fonte unica das verificacoes de envio proativo (estado FECHADO).

Guardas (nomes do plano/adendo):
- `test_outbound_refuses_without_ratified_purpose` (registro vazio/publicado => 100%
  PURPOSE_NOT_RATIFIED, 0 POST);
- `test_outbound_refuses_suppressed_lead`;
- `test_outbound_message_with_health_data_is_refused` (GT-2, ADD-A9-CONSENT §6);
- `test_prospect_without_acceptance_gets_no_whatsapp` (GT-3, re-especificado: aceite OS-05 versionado COM
  finalidades nomeadas e' aceito; `wa-consent-v2` nunca vale).
O registro ratificado e o aceite usados aqui sao SINTETICOS (`tests/unit/esf13_apoio.py`).
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from maezo.gateway.communications import outbound_gate as og
from maezo.gateway.communications.outbound_gate import (
    AutorizacaoDeEnvio,
    EnvioProativoRecusadoError,
    Motivo,
    OutboundGate,
    PedidoDeEnvio,
    PermissaoDeContato,
    autorizacao_valida,
    verificar_permissao,
)
from maezo.gateway.communications.whatsapp_templates import (
    RegistroDeTemplates,
    Variavel,
    registro_de_producao,
)
from maezo.platform.webhooks.whatsapp.acesso import SHA256_TEXTO_CONSENTIMENTO, VERSAO_TEXTO_CONSENTIMENTO
from tests.unit.esf13_apoio import (
    FINALIDADE_LEAD,
    TEMPLATE_LEAD,
    ReservaEmMemoria,
    SupressaoFixa,
    TransporteGravador,
    aceite_os05_sintetico,
    gate_ratificado,
    registro_ratificado,
)

_RAIZ = Path(__file__).resolve().parents[3]
TELEFONE = "5511977771234"
PROSPECT = "prospect-ref-0001"
CANAL = "contact-ref-0001"


def _pedido_lead(**kw: Any) -> PedidoDeEnvio:
    base: dict[str, Any] = {
        "template_id": TEMPLATE_LEAD,
        "finalidade": FINALIDADE_LEAD,
        "tipo_titular": "lead",
        "titular_ref": PROSPECT,
        "canal_ref": CANAL,
        "variaveis": ("1",),
        "permissao": aceite_os05_sintetico(),
        "chave_idempotencia": "chave-1",
    }
    base.update(kw)
    return PedidoDeEnvio(**base)


def _pedido_lembrete(**kw: Any) -> PedidoDeEnvio:
    base: dict[str, Any] = {
        "template_id": "lembrete_mensalidade_v1",
        "finalidade": "cobranca_lembrete_pre_vencimento",
        "tipo_titular": "beneficiario",
        "titular_ref": "ref-benef-1",
        "canal_ref": "wa:amh:hk1_aaaa0001",
        "variaveis": ("outubro de 2026", "320,25", "16/10"),
        "permissao": aceite_os05_sintetico(),
        "chave_idempotencia": "chave-l",
    }
    base.update(kw)
    return PedidoDeEnvio(**base)


async def _enviar(gate: OutboundGate, pedido: PedidoDeEnvio, *, telefone: str | None = TELEFONE) -> Any:
    reserva = ReservaEmMemoria()
    transporte = TransporteGravador()

    async def destinatario() -> str | None:
        return telefone

    r = await gate.enviar(pedido, destinatario=destinatario, reserva=reserva, transporte=transporte)
    return r, reserva, transporte


# ----------------------------------------------------------------------------- G-41 (1): registro vazio
@pytest.mark.parametrize(
    "registro", [RegistroDeTemplates(), registro_de_producao()], ids=["vazio", "publicado"]
)
async def test_outbound_refuses_without_ratified_purpose(registro: RegistroDeTemplates) -> None:
    """Todo pedido — de qualquer template publicado, ou desconhecido, com permissao valida ou nao — recusa
    PURPOSE_NOT_RATIFIED e nenhum POST acontece."""
    gate = OutboundGate(registro, supressao=SupressaoFixa())
    publicados = registro_de_producao().templates.values()
    pedidos = [
        _pedido_lead(),
        _pedido_lembrete(),
        *(
            _pedido_lembrete(template_id=t.id, finalidade=t.purpose_ref, tipo_titular=t.subject_kind)
            for t in publicados
        ),
    ]
    posts = 0
    for pedido in pedidos:
        r, reserva, transporte = await _enviar(gate, pedido)
        assert r.motivo is Motivo.PURPOSE_NOT_RATIFIED and r.postado is False
        assert reserva.reservas == 0
        posts += len(transporte.posts)
    assert posts == 0
    assert gate.contagens == {"PURPOSE_NOT_RATIFIED": len(pedidos)}


def test_imagem_sem_o_arquivo_carrega_o_registro_vazio(tmp_path: Path) -> None:
    vazio = registro_de_producao(tmp_path / "nao-existe.yaml")
    assert vazio == RegistroDeTemplates()
    assert not vazio.finalidade_ratificada("cobranca_lembrete_pre_vencimento")


def test_gate_de_producao_nao_tem_atalho() -> None:
    """Nenhum parametro do gate pula verificacao: o construtor so' recebe registro e fonte de supressao."""
    assert list(inspect.signature(OutboundGate.__init__).parameters) == ["self", "registro", "supressao"]
    assert list(inspect.signature(OutboundGate.de_producao).parameters) == ["supressao"]


# ----------------------------------------------------------------------------- G-41 (2): fixture ratificada
async def test_fixture_ratificada_gera_uma_reserva_e_um_post_e_a_mesma_chave_zero_post() -> None:
    gate = gate_ratificado()
    reserva = ReservaEmMemoria()
    transporte = TransporteGravador()

    async def destinatario() -> str | None:
        return TELEFONE

    r1 = await gate.enviar(_pedido_lead(), destinatario=destinatario, reserva=reserva, transporte=transporte)
    assert r1.motivo is None and r1.postado is True
    assert reserva.reservas == 1 and len(transporte.posts) == 1
    ((tel, aut),) = transporte.posts
    assert tel == TELEFONE and autorizacao_valida(aut) and aut.template_name == TEMPLATE_LEAD
    r2 = await gate.enviar(_pedido_lead(), destinatario=destinatario, reserva=reserva, transporte=transporte)
    assert r2.motivo is Motivo.DUPLICATE and r2.postado is False
    assert reserva.reservas == 1 and len(transporte.posts) == 1


async def test_falha_do_transporte_libera_a_reserva() -> None:
    gate = gate_ratificado()
    reserva = ReservaEmMemoria()

    async def destinatario() -> str | None:
        return TELEFONE

    async def quebrado(_t: str, _a: AutorizacaoDeEnvio) -> dict[str, object]:
        raise RuntimeError("meta fora")

    with pytest.raises(RuntimeError):
        await gate.enviar(_pedido_lead(), destinatario=destinatario, reserva=reserva, transporte=quebrado)
    assert reserva.chaves == set()


async def test_contato_nao_resolvido_recusa_sem_reserva_nem_post() -> None:
    r, reserva, transporte = await _enviar(gate_ratificado(), _pedido_lead(), telefone=None)
    assert r.motivo is Motivo.RECIPIENT_UNRESOLVED
    assert reserva.reservas == 0 and transporte.posts == []


# ----------------------------------------------------------------------------- supressao
@pytest.mark.parametrize("par", [(PROSPECT, "outro-canal"), ("outro-ref", CANAL)])
async def test_outbound_refuses_suppressed_lead(par: tuple[str, str]) -> None:
    supressao = SupressaoFixa(frozenset({par}))
    r, reserva, transporte = await _enviar(gate_ratificado(supressao=supressao), _pedido_lead())
    assert r.motivo is Motivo.SUPPRESSED and r.postado is False
    assert reserva.reservas == 0 and transporte.posts == []
    assert supressao.chamadas == 1


async def test_fonte_de_supressao_fora_ou_ausente_nunca_envia() -> None:
    r, _, transporte = await _enviar(gate_ratificado(supressao=SupressaoFixa(falha=True)), _pedido_lead())
    assert r.motivo is Motivo.SUPPRESSION_SOURCE_UNAVAILABLE and transporte.posts == []
    sem_fonte = OutboundGate(registro_ratificado(), supressao=None)
    r2, _, transporte2 = await _enviar(sem_fonte, _pedido_lead())
    assert r2.motivo is Motivo.SUPPRESSION_SOURCE_UNAVAILABLE and transporte2.posts == []


# ----------------------------------------------------------------------------- GT-2: dado de saude
@pytest.mark.parametrize(
    "valor",
    [
        "stage=dpp_pending",
        "J45.0",
        "recusa_motivo=saude",
        "sua CPT termina em 10/12",
        "utilização assistencial",
        "j45.0",  # CID em minusculas tambem e' recusado
    ],
)
async def test_outbound_message_with_health_data_is_refused(valor: str) -> None:
    """GT-2: valor de variavel com dado de saude => PHI_IN_COMMERCIAL_INPUT; zero chamada ao transporte e
    nenhuma reserva (o "outbox" do gate)."""
    r, reserva, transporte = await _enviar(gate_ratificado(), _pedido_lead(variaveis=(valor,)))
    assert r.motivo is Motivo.PHI_IN_COMMERCIAL_INPUT and r.postado is False
    assert transporte.posts == [] and reserva.reservas == 0


async def test_template_de_boas_vindas_com_lembrete_de_cpt_e_recusado_no_gate() -> None:
    """GT-2, template: mesmo que um corpo com CPT escapasse do validador, o gate recusa (defesa em
    profundidade). O registro e' montado SEM o validador, de proposito."""
    base = registro_ratificado()
    tpl = base.templates[TEMPLATE_LEAD]
    ruim = replace(tpl, body="Bem-vindo! Lembrete: sua CPT vale ate {{1}}.")
    registro = replace(base, templates={**base.templates, TEMPLATE_LEAD: ruim})
    r, _, transporte = await _enviar(OutboundGate(registro, supressao=SupressaoFixa()), _pedido_lead())
    assert r.motivo is Motivo.PHI_IN_COMMERCIAL_INPUT and transporte.posts == []


async def test_classe_de_variavel_fora_do_vocabulario_no_gate_e_recusada() -> None:
    base = registro_ratificado()
    tpl = replace(base.templates[TEMPLATE_LEAD], variables=(Variavel(pos=1, classe="dps_status"),))
    registro = replace(base, templates={**base.templates, TEMPLATE_LEAD: tpl})
    r, _, transporte = await _enviar(OutboundGate(registro, supressao=SupressaoFixa()), _pedido_lead())
    assert r.motivo is Motivo.PHI_IN_COMMERCIAL_INPUT and transporte.posts == []


# ----------------------------------------------------------------------------- GT-3: permissao
def _wa_consent_v2() -> PermissaoDeContato:
    return PermissaoDeContato(
        origem="consentimento_atendimento",
        escopo="atendimento_whatsapp",
        texto_versao=VERSAO_TEXTO_CONSENTIMENTO,
        texto_sha256=SHA256_TEXTO_CONSENTIMENTO,
        prova_ref="consent-ref-1",
    )


def test_aceite_os05_versionado_com_finalidades_nomeadas_e_aceito() -> None:
    """GT-3 re-especificado: o aceite OS-05 com texto versionado que NOMEIA a finalidade e' aceito."""
    registro = registro_ratificado()
    assert verificar_permissao(aceite_os05_sintetico(), finalidade=FINALIDADE_LEAD, registro=registro) is None


@pytest.mark.parametrize(
    ("permissao", "motivo"),
    [
        (None, Motivo.RECONTACT_PERMISSION_ABSENT),  # (a) sem registro de aceite
        (_wa_consent_v2(), Motivo.RECONTACT_PERMISSION_UNQUALIFIED),  # (b) wa-consent-v2
        (
            aceite_os05_sintetico(escopo="atendimento_whatsapp"),
            Motivo.RECONTACT_PERMISSION_UNQUALIFIED,
        ),  # (b') escopo alheio mesmo com texto publicado
        (aceite_os05_sintetico(escopo="analytics"), Motivo.RECONTACT_PERMISSION_UNQUALIFIED),
        (
            aceite_os05_sintetico(origem="inferida_prospect_save"),
            Motivo.RECONTACT_PERMISSION_UNQUALIFIED,
        ),  # (c) inferida do save
        (aceite_os05_sintetico(texto_sha256="0" * 64), Motivo.RECONTACT_PERMISSION_UNQUALIFIED),  # (d)
        (aceite_os05_sintetico(ultimo_evento="revoked"), Motivo.RECONTACT_PERMISSION_ABSENT),  # (e)
        (aceite_os05_sintetico(prova_ref=None), Motivo.RECONTACT_PERMISSION_UNQUALIFIED),  # sem prova
        (
            aceite_os05_sintetico(texto_versao="wa-consent-v2"),
            Motivo.RECONTACT_PERMISSION_UNQUALIFIED,
        ),  # versao do consentimento de atendimento, mesmo com escopo/origem de aceite
    ],
)
async def test_prospect_without_acceptance_gets_no_whatsapp(
    permissao: PermissaoDeContato | None, motivo: Motivo
) -> None:
    r, reserva, transporte = await _enviar(gate_ratificado(), _pedido_lead(permissao=permissao))
    assert r.motivo is motivo and r.postado is False
    assert transporte.posts == [] and reserva.reservas == 0


def test_texto_que_so_nomeia_finalidade_generica_nao_qualifica() -> None:
    doc_textos = [
        {
            "version": "os05-sintetico-v1",
            "sha256": aceite_os05_sintetico().texto_sha256,
            "purposes": ["comercial"],
            "channel": "whatsapp",
        }
    ]
    registro = registro_ratificado(contact_permission_texts=doc_textos)
    assert (
        verificar_permissao(aceite_os05_sintetico(), finalidade=FINALIDADE_LEAD, registro=registro)
        is Motivo.RECONTACT_PERMISSION_UNQUALIFIED
    )


def test_finalidade_nao_nomeada_no_texto_nao_qualifica() -> None:
    assert (
        verificar_permissao(
            aceite_os05_sintetico(), finalidade="outra_finalidade", registro=registro_ratificado()
        )
        is Motivo.RECONTACT_PERMISSION_UNQUALIFIED
    )


def test_wa_consent_v2_nunca_vale_para_contato_proativo_nem_com_registro_aberto() -> None:
    """A recusa e' por construcao, em duas camadas: (1) o validador do registro recusa publicar o texto
    `wa-consent-v2`; (2) mesmo num registro montado SEM o validador que o publique, o gate recusa."""
    from maezo.gateway.communications.whatsapp_templates import RegistroInvalidoError, TextoDePermissao

    with pytest.raises(RegistroInvalidoError):
        registro_ratificado(
            contact_permission_texts=[
                {
                    "version": VERSAO_TEXTO_CONSENTIMENTO,
                    "sha256": SHA256_TEXTO_CONSENTIMENTO,
                    "purposes": [FINALIDADE_LEAD],
                    "channel": "whatsapp",
                }
            ]
        )
    registro = replace(
        registro_ratificado(),
        textos_de_permissao=(
            TextoDePermissao(
                versao=VERSAO_TEXTO_CONSENTIMENTO,
                sha256=SHA256_TEXTO_CONSENTIMENTO,
                finalidades=(FINALIDADE_LEAD,),
                canal="whatsapp",
            ),
        ),
    )
    for permissao in (
        _wa_consent_v2(),
        replace(_wa_consent_v2(), origem="aceite_contato", escopo="x"),
        replace(_wa_consent_v2(), origem="aceite_contato", escopo="contato_proativo"),
    ):
        assert (
            verificar_permissao(permissao, finalidade=FINALIDADE_LEAD, registro=registro)
            is Motivo.RECONTACT_PERMISSION_UNQUALIFIED
        )


def test_sha_do_wa_consent_sob_outra_versao_nao_qualifica_no_gate() -> None:
    """F1: mesmo que um registro montado SEM o validador publicasse o texto `wa-consent-v2` sob outra
    versao, o gate recusa pelo sha256 do texto."""
    from maezo.gateway.communications.whatsapp_templates import TextoDePermissao

    base = registro_ratificado()
    registro = replace(
        base,
        textos_de_permissao=(
            TextoDePermissao(
                versao="os05-disfarcado-v9",
                sha256=SHA256_TEXTO_CONSENTIMENTO,
                finalidades=(FINALIDADE_LEAD,),
                canal="whatsapp",
            ),
        ),
    )
    disfarcado = aceite_os05_sintetico(
        texto_versao="os05-disfarcado-v9", texto_sha256=SHA256_TEXTO_CONSENTIMENTO
    )
    assert (
        verificar_permissao(disfarcado, finalidade=FINALIDADE_LEAD, registro=registro)
        is Motivo.RECONTACT_PERMISSION_UNQUALIFIED
    )


def test_em_producao_nenhum_aceite_os05_esta_registrado() -> None:
    """O texto de admissao/aceite nao existe (GAP-CONSENT-ADMISSAO; E-SF-62 so' minuta): o registro
    publicado nao tem texto de permissao, e o aceite sintetico e' recusado contra ele."""
    publicado = registro_de_producao()
    assert publicado.textos_de_permissao == ()
    assert (
        verificar_permissao(aceite_os05_sintetico(), finalidade=FINALIDADE_LEAD, registro=publicado)
        is Motivo.RECONTACT_PERMISSION_UNQUALIFIED
    )


# ----------------------------------------------------------------------------- titular, template, classe
async def test_beneficiario_sem_oposicao_executavel_e_recusado() -> None:
    gate = gate_ratificado(opposition_executable_subject_kinds=["lead"])
    r, _, transporte = await _enviar(gate, _pedido_lembrete())
    assert r.motivo is Motivo.SUBJECT_KIND_CLOSED and transporte.posts == []


async def test_template_nao_aprovado_e_recusado() -> None:
    base = registro_ratificado()
    registro = replace(
        base,
        templates={
            **base.templates,
            TEMPLATE_LEAD: replace(base.templates[TEMPLATE_LEAD], meta_status="submitted"),
        },
    )
    r, _, transporte = await _enviar(OutboundGate(registro, supressao=SupressaoFixa()), _pedido_lead())
    assert r.motivo is Motivo.TEMPLATE_NOT_APPROVED and transporte.posts == []


async def test_template_de_outra_finalidade_ou_titular_e_recusado() -> None:
    gate = gate_ratificado()
    r, _, _ = await _enviar(gate, _pedido_lead(template_id="lembrete_mensalidade_v1"))
    assert r.motivo is Motivo.TEMPLATE_PURPOSE_MISMATCH
    r2, _, _ = await _enviar(gate, _pedido_lead(template_id="nao_existe"))
    assert r2.motivo is Motivo.TEMPLATE_NOT_REGISTERED


async def test_classe_pessoal_sem_taxonomia_ratificada_e_recusada() -> None:
    gate = gate_ratificado(variable_class_taxonomy_ratified=False)
    r, _, transporte = await _enviar(gate, _pedido_lembrete())
    assert r.motivo is Motivo.VARIABLE_CLASS_PENDING_DPO and transporte.posts == []


# ----------------------------------------------------------------------------- G-41 (3): contagens, sem PII
async def test_contagens_por_motivo_e_log_sem_telefone_nem_referencia() -> None:
    gate = gate_ratificado(supressao=SupressaoFixa(frozenset({(PROSPECT, "x")})))
    with structlog.testing.capture_logs() as logs:
        await _enviar(gate, _pedido_lead())
        await _enviar(gate, _pedido_lead(permissao=None))
        await _enviar(gate, _pedido_lead(variaveis=("J45.0",)))
    assert gate.contagens == {
        "SUPPRESSED": 1,
        "recontact_permission_absent": 1,
        "PHI_IN_COMMERCIAL_INPUT": 1,
    }
    bruto = repr(logs)
    for proibido in (TELEFONE, PROSPECT, CANAL, "J45", "chave-1", "prova-sintetica"):
        assert proibido not in bruto
    assert {e["event"] for e in logs} == {"outbound_gate_recusa"}
    assert "chave-1" not in repr(_pedido_lead()) and PROSPECT not in repr(_pedido_lead())


# ----------------------------------------------------------------------------- transporte e 1:1 inbound
def _server() -> Any:
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer, WhatsAppSettings

    return WhatsAppServer(
        settings=WhatsAppSettings(whatsapp_token="t", whatsapp_verify_token="v", phone_number_id="123")
    )


def _httpx_mock() -> Any:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"messages": [{"id": "wamid.t"}]})
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=resp)
    return client


async def _autorizacao_valida() -> AutorizacaoDeEnvio:
    d = await gate_ratificado().avaliar(_pedido_lead())
    assert d.autorizacao is not None and autorizacao_valida(d.autorizacao)
    return d.autorizacao


@pytest.mark.parametrize(
    "fabricar",
    [
        lambda _a: None,
        lambda _a: "lembrete_mensalidade_v1",
        lambda _a: AutorizacaoDeEnvio(
            template_name="lembrete_mensalidade_v1",
            language="pt_BR",
            parametros=("a",),
            finalidade="x",
            chave_idempotencia="k",
            _selo="f" * 64,
        ),
        lambda a: replace(a, parametros=("outro",)),
        lambda a: replace(a, finalidade="x"),
        lambda a: replace(a, chave_idempotencia="k2"),
        lambda a: replace(a, template_name="outro_template"),
    ],
    ids=["nenhuma", "nome-solto", "forjada", "parametros", "finalidade", "chave", "template"],
)
async def test_send_template_sem_autorizacao_do_gate_recusa_antes_do_post(fabricar: Any) -> None:
    """F3: o selo cobre TODOS os campos; qualquer alteracao de uma autorizacao valida recusa."""
    autorizacao = fabricar(await _autorizacao_valida())
    client = _httpx_mock()
    with patch("httpx.AsyncClient", return_value=client), pytest.raises(EnvioProativoRecusadoError) as exc:
        await _server().send_template("5511999999999", autorizacao)
    assert exc.value.motivo is Motivo.OUTBOUND_NOT_AUTHORIZED
    client.post.assert_not_awaited()


async def test_mesma_autorizacao_nao_gera_segundo_post() -> None:
    """F4 (replay): a chave de dedup e' a da autorizacao; reapresentar a mesma autorizacao = 1 POST so'."""
    from maezo.tools.mcp_whatsapp.server import (
        WhatsAppIdempotencyUnsupportedError,
        WhatsAppServer,
        WhatsAppSettings,
    )
    from tests.support.dedup_fakes import FakeDedupRegistry

    assert "idempotency_key" not in inspect.signature(WhatsAppServer.send_template).parameters
    aut = await _autorizacao_valida()
    server = WhatsAppServer(
        settings=WhatsAppSettings(whatsapp_token="t", whatsapp_verify_token="v", phone_number_id="123"),
        dedup=FakeDedupRegistry(),
    )
    client = _httpx_mock()
    with patch("httpx.AsyncClient", return_value=client):
        primeiro = await server.send_template("5511999999999", aut)
        segundo = await server.send_template("5511999999999", aut)
    assert "messages" in primeiro
    assert segundo == {"suppressed_duplicate": True, "idempotency_key": aut.chave_idempotencia}
    client.post.assert_awaited_once()
    sem_registro = _httpx_mock()
    with (
        patch("httpx.AsyncClient", return_value=sem_registro),
        pytest.raises(WhatsAppIdempotencyUnsupportedError),
    ):
        await _server().send_template("5511999999999", aut)
    sem_registro.post.assert_not_awaited()


#: Chamadores de `.send_message(` em `src/maezo`, todos REATIVOS (resposta a mensagem recebida, pelo gate
#: de efeito do principal) ou CERCADOS pelo OutboundGate:
#: - dispatch.py: resposta da Helena/Lucas no turno inbound (`_ScopedWhatsAppSender`);
#: - agents/lucas/adapters.py: adaptador do remetente do Lucas (turno inbound, chave do wamid);
#: - tools/mcp_whatsapp/sender.py: adaptador `WhatsAppSender` dos agentes (seam cercado, turno inbound);
#: - platform/lembretes/envio.py: texto livre do lembrete, so' com `AutorizacaoDeEnvio` (exigida antes).
_CHAMADORES_SEND_MESSAGE = frozenset(
    {
        "platform/webhooks/whatsapp/dispatch.py",
        "agents/lucas/adapters.py",
        "tools/mcp_whatsapp/sender.py",
        "platform/lembretes/envio.py",
    }
)


def test_send_message_so_tem_chamadores_reativos_ou_cercados() -> None:
    """F5: um novo chamador de `send_message` (envio de texto livre) e' um caminho proativo potencial que
    pularia o gate; tem de entrar aqui de forma consciente, com justificativa."""
    raiz = _RAIZ / "src" / "maezo"
    achados: set[str] = set()
    for arquivo in raiz.rglob("*.py"):
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if (
                isinstance(no, ast.Call)
                and isinstance(no.func, ast.Attribute)
                and no.func.attr == "send_message"
            ):
                achados.add(arquivo.relative_to(raiz).as_posix())
    assert achados == _CHAMADORES_SEND_MESSAGE


async def test_resposta_1a1_inbound_nao_passa_pelo_gate() -> None:
    """G-41 (4): o caminho de resposta (`send_message`, chamado por `dispatch.py`) segue sem autorizacao."""
    from maezo.tools.mcp_whatsapp.server import WhatsAppServer

    assert "autorizacao" not in inspect.signature(WhatsAppServer.send_message).parameters
    client = _httpx_mock()
    with patch("httpx.AsyncClient", return_value=client):
        await _server().send_message("5511999999999", "Ola")
    client.post.assert_awaited_once()
    dispatch = (_RAIZ / "src/maezo/platform/webhooks/whatsapp/dispatch.py").read_text(encoding="utf-8")
    importados = {
        n.module
        for n in ast.walk(ast.parse(dispatch))
        if isinstance(n, ast.ImportFrom) and n.module is not None
    }
    assert not any("outbound_gate" in m for m in importados)


def test_o_gate_nao_importa_sdk_nem_transporte() -> None:
    fonte = Path(og.__file__).read_text(encoding="utf-8")
    for proibido in ("httpx", "anthropic", "boto3", "mcp_whatsapp"):
        assert f"import {proibido}" not in fonte and f"from {proibido}" not in fonte
