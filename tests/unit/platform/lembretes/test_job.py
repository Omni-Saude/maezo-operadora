"""O job do lembrete: selecao ponta a ponta com dubles (dry-run, desligado, allowlist, janela, simulador)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
import structlog

from maezo.agents.lucas.fonte_cobranca import Indisponivel
from maezo.gateway.recipient_custody import RecipientRecord
from maezo.platform.lembretes.config import ConfigJob
from maezo.platform.lembretes.envio import DestinoForaDaAllowlistError
from maezo.platform.lembretes.job import executar, simular
from maezo.platform.lembretes.texto import compor_lembrete
from tests.support.lembrete_fakes import (
    AGORA,
    CONV_A,
    CONV_B,
    CONV_C,
    TELEFONE_A,
    TELEFONE_B,
    TELEFONE_C,
    Cenario,
    FakeCliente,
    allowlist,
    benef,
    competencia,
    contato,
    fatos,
)

TEXTO_A = (
    "Sua mensalidade de outubro de 2026 (R$ 320,25) vence em 16/10. "
    "Para a 2ª via, acesse o aplicativo Austa Clínicas, o portal do plano ou a central de atendimento. "
    "Para não receber lembretes, responda PARAR."
)

REAL = ConfigJob(
    habilitado=True, dry_run=False, allowlist=allowlist(CONV_A, CONV_B, CONV_C), template_nome=""
)


def _cenario(cfg: ConfigJob = REAL, **kw: object) -> Cenario:
    base: dict[str, object] = {
        "beneficiarios": [benef(CONV_A)],
        "fatos_por_conversa": {CONV_A: fatos()},
        "contatos": {CONV_A: contato(TELEFONE_A)},
    }
    base.update(kw)
    return Cenario(cfg=cfg, **base)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- desligado / dry-run
async def test_desligado_por_padrao_nada_e_tocado() -> None:
    c = _cenario(ConfigJob())  # defaults: desligado, dry-run
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert resumo.modo == "desligado" and resumo.contadores["desligado"] == 1
    assert c.cliente.total == 0
    assert c.acesso.chamadas == 0 and c.fatos.consultadas == [] and c.store.chamadas == 0


async def test_desligado_mesmo_com_dry_run_falso_e_allowlist_nao_envia() -> None:
    c = _cenario(replace(REAL, habilitado=False))
    await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 0 and c.acesso.chamadas == 0


async def test_simulador_desligado_nao_envia_nada() -> None:
    c = _cenario(replace(REAL, habilitado=False))
    resumo = await simular(c.deps, c.cfg, conversation_id=CONV_A, agora=AGORA)
    assert resumo.contadores["desligado"] == 1
    assert c.cliente.total == 0


async def test_dry_run_so_conta_e_nao_grava_nem_envia() -> None:
    c = _cenario(replace(REAL, dry_run=True))
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert resumo.modo == "dry_run"
    assert resumo.contadores["enviaria"] == 1 and resumo.contadores["candidatos"] == 1
    assert c.cliente.total == 0
    assert c.store.reservas == {}
    assert c.gate.embrulhados == 0


async def test_dry_run_vale_tambem_fora_do_horario_e_sem_allowlist() -> None:
    c = _cenario(ConfigJob(habilitado=True, dry_run=True))
    resumo = await executar(c.deps, c.cfg, agora=datetime(2026, 10, 14, 2, 0, tzinfo=UTC))
    assert resumo.contadores["enviaria"] == 1 and c.cliente.total == 0
    assert resumo.dentro_do_horario is False


async def test_logs_so_tem_contagens_nunca_telefone_conversa_ou_valor() -> None:
    c = _cenario()
    with structlog.testing.capture_logs() as logs:
        await executar(c.deps, c.cfg, agora=AGORA)
    assert logs
    bruto = repr(logs)
    for proibido in (TELEFONE_A, CONV_A, "hk1_", "320", "ref-", "consent-", "[TESTE]"):
        assert proibido not in bruto
    resumo = next(e for e in logs if e["event"] == "lembrete_cobranca_resumo")
    assert set(resumo["contagens"]) <= {"candidatos", "enviado_texto"}
    assert all(isinstance(v, int) for v in resumo["contagens"].values())


# ----------------------------------------------------------------------------- envio real
async def test_envia_texto_exato_dentro_da_janela_e_registra_a_reserva() -> None:
    c = _cenario()
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.texto == [(TELEFONE_A, TEXTO_A)]
    assert c.cliente.template == []
    assert resumo.contadores["enviado_texto"] == 1
    assert c.store.reservas == {(CONV_A, "2026-10"): "texto"}
    assert c.gate.embrulhados == 1  # o envio passou pelo gate do principal
    assert c.cliente.chaves == [f"k:{len(f'lembrete:{CONV_A}:2026-10')}"]


async def test_idempotente_segunda_rodada_nao_reenvia() -> None:
    c = _cenario()
    await executar(c.deps, c.cfg, agora=AGORA)
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 1
    assert resumo.contadores["ja_lembrado"] == 1


async def test_competencia_nova_gera_novo_lembrete() -> None:
    c = _cenario()
    await executar(c.deps, c.cfg, agora=AGORA)
    c.fatos.por_conversa[CONV_A] = fatos(competencia("2026-11", vencimento="2026-10-15"))
    await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 2


@pytest.mark.parametrize(
    ("f", "chave"),
    [
        (fatos(pagador="pessoa_juridica"), "pagador_nao_e_pessoa_fisica"),
        (fatos(pagador="desconhecido", administradora=True), "cobranca_da_administradora"),
        (fatos(competencia(situacao="paga")), "sem_competencia_na_janela"),
        (fatos(competencia(vencimento="2026-10-20")), "sem_competencia_na_janela"),
    ],
)
async def test_quem_nao_recebe(f: object, chave: str) -> None:
    c = _cenario(fatos_por_conversa={CONV_A: f})
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 0 and resumo.contadores[chave] == 1
    assert c.store.reservas == {}


async def test_fonte_indisponivel_nao_inventa_fato() -> None:
    c = _cenario(fatos_por_conversa={CONV_A: Indisponivel("fonte_indisponivel")})
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 0 and resumo.contadores["fonte_indisponivel_fonte_indisponivel"] == 1


async def test_optout_nao_recebe() -> None:
    c = _cenario(optouts=[CONV_A])
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 0 and resumo.contadores["optout"] == 1
    assert c.fatos.consultadas == []  # nem a cobranca dele e' lida


@pytest.mark.parametrize("par", [("ref-0001", "qualquer-canal"), ("outra-ref", CONV_A)])
async def test_suprimido_pelo_registro_sp_op_supp_001_nao_recebe(par: tuple[str, str]) -> None:
    c = _cenario(suprimidos=[par])
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 0 and resumo.contadores["suprimido"] == 1


async def test_fonte_de_supressao_fora_nao_envia_nada() -> None:
    c = _cenario(supressao_falha=True)
    with pytest.raises(RuntimeError):
        await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 0


@pytest.mark.parametrize(
    ("utc", "envia"),
    [
        (datetime(2026, 10, 13, 10, 59, tzinfo=UTC), False),
        (datetime(2026, 10, 13, 11, 0, tzinfo=UTC), True),
        (datetime(2026, 10, 13, 22, 59, tzinfo=UTC), True),
        (datetime(2026, 10, 13, 23, 0, tzinfo=UTC), False),
    ],
)
async def test_horario_de_silencio_no_envio_real(utc: datetime, envia: bool) -> None:
    # o contato fala "agora" (dentro da janela de 24 h) relativo a `utc`
    c = _cenario(contatos={CONV_A: RecipientRecord(phone=TELEFONE_A, last_inbound_at=utc)})
    resumo = await executar(c.deps, c.cfg, agora=utc)
    assert (c.cliente.total == 1) is envia
    if not envia:
        assert resumo.contadores["fora_do_horario"] == 1
        assert c.acesso.chamadas == 0


# ----------------------------------------------------------------------------- allowlist
async def test_allowlist_so_os_listados_recebem_e_os_outros_nem_sao_consultados() -> None:
    cfg = replace(REAL, allowlist=allowlist(CONV_A))
    c = _cenario(
        cfg,
        beneficiarios=[benef(CONV_A), benef(CONV_B), benef(CONV_C)],
        fatos_por_conversa={CONV_A: fatos(), CONV_B: fatos(), CONV_C: fatos()},
        contatos={
            CONV_A: contato(TELEFONE_A),
            CONV_B: contato(TELEFONE_B),
            CONV_C: contato(TELEFONE_C),
        },
    )
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert [t for t, _ in c.cliente.texto] == [TELEFONE_A]
    assert resumo.contadores["fora_da_allowlist"] == 2
    assert c.fatos.consultadas == [CONV_A]


async def test_allowlist_vazia_nao_envia_a_ninguem() -> None:
    c = _cenario(replace(REAL, allowlist=allowlist()))
    await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 0


async def test_envio_real_sem_allowlist_e_recusado_quando_exigida() -> None:
    c = _cenario(replace(REAL, allowlist=None, exigir_allowlist=True))
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert resumo.contadores["recusado_sem_allowlist"] == 1
    assert c.cliente.total == 0 and c.acesso.chamadas == 0


async def test_sem_allowlist_e_sem_exigencia_envia_a_todos_os_elegiveis() -> None:
    cfg = replace(REAL, allowlist=None, exigir_allowlist=False)
    c = _cenario(cfg)
    await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 1


async def test_o_enviador_recusa_conversa_fora_da_allowlist_mesmo_chamado_direto() -> None:
    c = _cenario(replace(REAL, allowlist=allowlist(CONV_A)))
    m = compor_lembrete(competencia="2026-10", valor_total="1.00", vencimento=AGORA.date())
    with pytest.raises(DestinoForaDaAllowlistError):
        await c.deps.enviador.enviar(
            conversation_id=CONV_B, raw_to=TELEFONE_B, mensagem=m, via_template=False, chave="k"
        )
    assert c.cliente.total == 0 and c.gate.embrulhados == 0


async def test_o_enviador_recusa_conversa_de_outro_tenant() -> None:
    c = _cenario(replace(REAL, allowlist=None))
    m = compor_lembrete(competencia="2026-10", valor_total="1.00", vencimento=AGORA.date())
    with pytest.raises(ValueError, match="outro tenant"):
        await c.deps.enviador.enviar(
            conversation_id="wa:outro:hk1_aaaa0001",
            raw_to=TELEFONE_A,
            mensagem=m,
            via_template=False,
            chave="k",
        )
    assert c.cliente.total == 0


# ----------------------------------------------------------------------------- janela de 24 h / template
async def test_fora_da_janela_usa_template_com_as_tres_variaveis() -> None:
    cfg = replace(REAL, template_nome="lembrete_mensalidade_v1")
    c = _cenario(cfg, contatos={CONV_A: contato(TELEFONE_A, horas=30)})
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.texto == []
    assert c.cliente.template == [
        (TELEFONE_A, "lembrete_mensalidade_v1", ["outubro de 2026", "320,25", "16/10"], "pt_BR")
    ]
    assert resumo.contadores["enviado_template"] == 1
    assert c.store.reservas == {(CONV_A, "2026-10"): "template"}


async def test_fora_da_janela_sem_template_nao_envia_nem_reserva() -> None:
    c = _cenario(contatos={CONV_A: contato(TELEFONE_A, horas=30)})
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 0 and c.store.reservas == {}
    assert resumo.contadores["fora_da_janela_sem_template"] == 1


async def test_sem_telefone_na_custodia_nao_envia() -> None:
    c = _cenario(contatos={})
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 0 and resumo.contadores["sem_contato"] == 1


# ----------------------------------------------------------------------------- limite, falha, suprimido
async def test_limite_global_por_execucao_e_pausa_entre_envios() -> None:
    cfg = replace(REAL, limite_por_execucao=2, pausa_entre_envios_s=1.5)
    c = _cenario(
        cfg,
        beneficiarios=[benef(CONV_A), benef(CONV_B), benef(CONV_C)],
        fatos_por_conversa={CONV_A: fatos(), CONV_B: fatos(), CONV_C: fatos()},
        contatos={
            CONV_A: contato(TELEFONE_A),
            CONV_B: contato(TELEFONE_B),
            CONV_C: contato(TELEFONE_C),
        },
    )
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 2
    assert resumo.contadores["limite_por_execucao"] == 1
    assert c.pausas == [1.5, 1.5]


async def test_falha_de_envio_libera_a_reserva_e_a_proxima_rodada_tenta_de_novo() -> None:
    c = _cenario(cliente=FakeCliente(falhar=1))
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert resumo.contadores["falha_envio"] == 1 and c.store.reservas == {}
    await executar(c.deps, c.cfg, agora=AGORA)
    assert c.cliente.total == 1 and len(c.store.reservas) == 1


async def test_envio_suprimido_pelo_cliente_nao_conta_como_enviado() -> None:
    c = _cenario(cliente=FakeCliente(ack={"suppressed_synthetic": True}))
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert resumo.contadores["suprimido_pelo_cliente"] == 1
    assert "enviado_texto" not in resumo.contadores


# ----------------------------------------------------------------------------- simulador
async def test_simulador_envia_teste_sintetico_so_a_conversa_da_allowlist() -> None:
    cfg = replace(REAL, allowlist=allowlist(CONV_A))
    c = _cenario(cfg)
    resumo = await simular(c.deps, c.cfg, conversation_id=CONV_A, agora=AGORA)
    assert resumo.modo == "simulacao" and resumo.contadores["enviado_texto"] == 1
    ((para, texto),) = c.cliente.texto
    assert para == TELEFONE_A
    assert texto.startswith("[TESTE] Sua mensalidade de outubro de 2026 (R$ 123,45) vence em 16/10.")
    assert texto.endswith("Para não receber lembretes, responda PARAR.")
    assert c.fatos.consultadas == []  # fatos sinteticos: a fonte de cobranca nao e' lida
    assert c.store.reservas == {}  # pode repetir


async def test_simulador_recusa_conversa_fora_da_allowlist_e_sem_allowlist() -> None:
    c = _cenario(replace(REAL, allowlist=allowlist(CONV_A)), contatos={CONV_B: contato(TELEFONE_B)})
    r = await simular(c.deps, c.cfg, conversation_id=CONV_B, agora=AGORA)
    assert r.contadores["recusado_fora_da_allowlist"] == 1 and c.cliente.total == 0
    c2 = _cenario(replace(REAL, allowlist=None))
    r2 = await simular(c2.deps, c2.cfg, conversation_id=CONV_A, agora=AGORA)
    assert r2.contadores["recusado_fora_da_allowlist"] == 1 and c2.cliente.total == 0


async def test_simulador_em_dry_run_so_conta() -> None:
    c = _cenario(replace(REAL, dry_run=True))
    r = await simular(c.deps, c.cfg, conversation_id=CONV_A, agora=AGORA)
    assert r.contadores["simularia"] == 1 and c.cliente.total == 0


async def test_simulador_respeita_optout_e_horario_e_janela() -> None:
    c = _cenario(optouts=[CONV_A])
    assert (await simular(c.deps, c.cfg, conversation_id=CONV_A, agora=AGORA)).contadores["optout"] == 1
    noite = datetime(2026, 10, 14, 2, 0, tzinfo=UTC)
    c2 = _cenario()
    assert (await simular(c2.deps, c2.cfg, conversation_id=CONV_A, agora=noite)).contadores[
        "fora_do_horario"
    ] == 1
    assert c2.cliente.total == 0
    c3 = _cenario(replace(REAL, template_nome="t"), contatos={CONV_A: contato(TELEFONE_A, horas=48)})
    await simular(c3.deps, c3.cfg, conversation_id=CONV_A, agora=noite, ignorar_horario=True)
    assert c3.cliente.template[0][2][0] == "[TESTE] outubro de 2026"
