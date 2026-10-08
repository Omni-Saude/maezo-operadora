"""Acesso do beneficiario (DL-0083): os achados da revisao do PR #700, um teste (ou mais) por achado.

P0 tentativas/bloqueio sobrevivem a REVOGAR/ACEITO; ALTO telefone desconhecido nao entra pelo documento e
telefone compartilhado so' vale contra os candidatos; MEDIO "confere, nao resolve"; P1-b pessoa diferente
volta ao consentimento; P2 memoria apagada; 9 prazo proprio e teto da regravacao com alarme.
"""

from __future__ import annotations

from datetime import date

import pytest
import structlog

from maezo.platform.webhooks.whatsapp import acesso as ac
from maezo.platform.webhooks.whatsapp import pre_roteamento
from tests.unit.platform.webhooks.whatsapp.test_acesso import (
    CPF,
    NASC,
    NASC_TXT,
    OUTRA_REF,
    OUTRO_CPF,
    REF,
    TENANT_AMH,
    Ambiente,
)

NASC_DEPENDENTE = date(2010, 1, 1)
NASC_DEPENDENTE_TXT = "01/01/2010"


@pytest.fixture
def amb() -> Ambiente:
    return Ambiente()


# --------------------------------------------------------------------------- P0: REVOGAR/ACEITO nao zeram
async def test_revogar_e_aceito_nao_zeram_as_tentativas(amb: Ambiente) -> None:
    """2 erros -> REVOGAR -> ACEITO -> 1 erro = BLOQUEADO. Antes, REVOGAR zerava o contador (bypass)."""
    await amb.ate_cpf()
    await amb.msg(OUTRO_CPF)
    await amb.msg("123")
    d = await amb.msg("REVOGAR")
    assert d.novo.estado == ac.REVOGADO and d.novo.tentativas == 2
    d = await amb.msg("ACEITO")
    assert d.novo.estado == ac.AGUARDANDO_CPF and d.novo.tentativas == 2
    d = await amb.msg(OUTRO_CPF)
    assert d.escalar and d.novo.estado == ac.BLOQUEADO_HUMANO and d.respostas == (ac.BLOQUEADO,)


async def test_bloqueio_sobrevive_a_revogar_e_aceito_e_so_o_prazo_libera(amb: Ambiente) -> None:
    await amb.ate_cpf()
    for _ in range(3):
        await amb.msg("123")
    d = await amb.msg("REVOGAR")
    assert d.novo.estado == ac.REVOGADO and d.novo.bloqueado_ate is not None and d.novo.tentativas == 3
    d = await amb.msg("ACEITO")
    assert d.novo.estado == ac.BLOQUEADO_HUMANO and d.respostas == (ac.BLOQUEADO_CURTO,) and not d.escalar
    assert d.novo.consentido_em is not None  # o consentimento vale; o bloqueio tambem
    assert (await amb.msg(CPF)).respostas == (ac.BLOQUEADO_CURTO,) and amb.verif.chamadas == []
    amb.relogio.avancar(hours=25)
    d = await amb.msg("oi")
    assert d.novo.estado == ac.AGUARDANDO_CPF and d.novo.tentativas == 0 and d.respostas == (ac.PEDIDO_CPF,)


async def test_prazo_do_bloqueio_vencido_em_revogado_zera_o_contador(amb: Ambiente) -> None:
    await amb.ate_cpf()
    for _ in range(3):
        await amb.msg("123")
    await amb.msg("REVOGAR")
    amb.relogio.avancar(hours=25)
    d = await amb.msg("ACEITO")
    assert d.novo.estado == ac.AGUARDANDO_CPF and d.novo.tentativas == 0 and d.novo.bloqueado_ate is None


# --------------------------------------------------------------------------- ALTO: telefone desconhecido
@pytest.mark.parametrize("cpf", [CPF, OUTRO_CPF, "123"])
async def test_telefone_desconhecido_nao_entra_pelo_documento(amb: Ambiente, cpf: str) -> None:
    """Sem candidato: texto neutro + atendente, IGUAL para CPF cadastrado, nao cadastrado ou invalido; a AMH
    nem e' perguntada (nada revela se o CPF existe)."""
    amb.verif.cadastrar_cpf(CPF, REF)
    amb.resolvedor.desfecho = "nenhum"
    await amb.ate_cpf()
    d = await amb.msg(cpf)
    assert d.respostas == (ac.TELEFONE_NAO_IDENTIFICADO,) and d.escalar and not d.prosseguir
    assert d.novo.estado == ac.BLOQUEADO_HUMANO and d.novo.tentativas == 0
    assert amb.verif.chamadas == [] and amb.consent.chamadas == []
    assert "CPF" not in ac.TELEFONE_NAO_IDENTIFICADO
    d = await amb.msg(NASC_TXT)
    assert d.respostas == (ac.BLOQUEADO_CURTO,) and not d.escalar


async def test_telefone_com_candidatos_demais_e_tratado_como_desconhecido(amb: Ambiente) -> None:
    amb.resolvedor.desfecho = "multiplos"
    amb.resolvedor.multiplos = tuple(f"ref-{i}" for i in range(ac.MAX_CANDIDATOS + 1))
    await amb.ate_cpf()
    d = await amb.msg(CPF)
    assert d.respostas == (ac.TELEFONE_NAO_IDENTIFICADO,) and d.escalar and amb.verif.chamadas == []


async def test_amh_fora_no_passo_do_telefone_nao_conta_tentativa(amb: Ambiente) -> None:
    amb.resolvedor.desfecho = "indisponivel"
    await amb.ate_cpf()
    d = await amb.msg(CPF)
    assert d.respostas == (ac.INDISPONIVEL,) and d.novo.estado == ac.AGUARDANDO_CPF and d.novo.tentativas == 0
    assert amb.verif.chamadas == []


# --------------------------------------------------------------------------- ALTO/MEDIO: compartilhado
async def test_compartilhado_documento_de_quem_nao_e_candidato_nao_vale(amb: Ambiente) -> None:
    """O CPF+nascimento e' de uma pessoa REAL do cadastro, mas que nao e' candidata deste telefone."""
    amb.resolvedor.desfecho = "multiplos"
    amb.verif.cadastrar_cpf_nasc(CPF, NASC, "ref-de-outra-familia")
    await amb.ate_cpf()
    await amb.msg(CPF)
    d = await amb.msg(NASC_TXT)
    assert (
        d.novo.estado != ac.VERIFICADO and d.respostas == (ac.FALHA_CONFERENCIA,) and d.novo.tentativas == 1
    )
    assert {c[2] for c in amb.verif.chamadas} == {REF, OUTRA_REF}


async def test_compartilhado_dois_candidatos_conferindo_nao_verifica_ninguem(amb: Ambiente) -> None:
    amb.resolvedor.desfecho = "multiplos"
    amb.verif.cadastrar_cpf_nasc(CPF, NASC, REF)
    amb.verif.cadastrar_cpf_nasc(CPF, NASC, OUTRA_REF)
    await amb.ate_cpf()
    await amb.msg(CPF)
    d = await amb.msg(NASC_TXT)
    assert d.novo.estado != ac.VERIFICADO and d.respostas == (ac.FALHA_CONFERENCIA,)


async def test_unico_confere_so_contra_a_ref_do_telefone(amb: Ambiente) -> None:
    amb.verif.cadastrar_cpf(CPF, OUTRA_REF)  # o CPF existe, mas e' de outra pessoa
    await amb.ate_cpf()
    d = await amb.msg(CPF)
    assert d.respostas == (ac.FALHA_CONFERENCIA,) and [c[2] for c in amb.verif.chamadas] == [REF]


# --------------------------------------------------------------------------- P1-b e P2: a pessoa mudou
async def _verificar_compartilhado(amb: Ambiente, quem: str) -> ac.DecisaoAcesso:
    await amb.msg(CPF)
    return await amb.msg(NASC_TXT if quem == REF else NASC_DEPENDENTE_TXT)


async def test_pessoa_diferente_volta_ao_consentimento_e_apaga_a_memoria(amb: Ambiente) -> None:
    amb.resolvedor.desfecho = "multiplos"
    amb.verif.cadastrar_cpf_nasc(CPF, NASC, REF)
    amb.verif.cadastrar_cpf_nasc(CPF, NASC_DEPENDENTE, OUTRA_REF)
    await amb.ate_cpf()
    d = await _verificar_compartilhado(amb, REF)
    assert d.novo.portable_subject_ref == REF and len(amb.consent.chamadas) == 1
    assert not d.esquecer_memoria
    amb.relogio.avancar(hours=25)  # a verificacao vence; agora quem escreve e' o dependente
    assert (await amb.msg("oi")).respostas == (ac.PEDIDO_CPF,)
    d = await _verificar_compartilhado(amb, OUTRA_REF)
    assert d.novo.estado == ac.SEM_CONSENTIMENTO and d.respostas == (ac.TEXTO_CONSENTIMENTO,)
    assert d.esquecer_memoria and d.nova_verificacao and not d.prosseguir
    assert d.novo.consentido_em is None and d.novo.portable_subject_ref is None and d.novo.consent_ref is None
    assert (
        len(amb.consent.chamadas) == 1
    )  # NENHUM consentimento em nome do dependente com o ACEITO do titular
    # O dependente aceita por si e se verifica: AGORA o consentimento e' dele.
    amb.relogio.avancar(minutes=1)
    assert (await amb.msg("ACEITO")).novo.estado == ac.AGUARDANDO_CPF
    d = await _verificar_compartilhado(amb, OUTRA_REF)
    assert (
        d.novo.estado == ac.VERIFICADO and d.novo.portable_subject_ref == OUTRA_REF and not d.esquecer_memoria
    )
    ultimo = amb.consent.chamadas[-1]
    assert ultimo["ref"] == OUTRA_REF and ultimo["decision"] == "granted"
    assert ultimo["decided_at"] == "2026-10-09T13:01:00Z"  # o ACEITO do dependente, nao o do titular


async def test_mesma_pessoa_reverificada_nao_apaga_a_memoria(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    amb.relogio.avancar(hours=25)
    await amb.msg("oi")
    d = await amb.msg(CPF)
    assert d.novo.estado == ac.VERIFICADO and not d.esquecer_memoria


async def test_revogar_apaga_a_memoria_da_conversa(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    assert (await amb.msg("REVOGAR")).esquecer_memoria


# --------------------------------------------------------------------------- 9: prazo proprio e teto
async def test_gravacao_do_consentimento_usa_prazo_proprio(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    assert amb.consent.chamadas[0]["timeout_seconds"] == ac.PRAZO_CONSENTIMENTO_S == 20.0


def test_prazo_do_consentimento_fora_da_faixa_recusa(amb: Ambiente) -> None:
    with pytest.raises(ValueError, match="prazo_consentimento_s"):
        ac.AcessoBeneficiario(
            store=amb.store, verification=amb.verif, consents=amb.consent, resolvedor=amb.resolvedor,
            hash_telefone=lambda n: "a" * 64, hasher=amb.hasher, lexicos=pre_roteamento.carregar(),
            amh_tenant=TENANT_AMH, hash_scheme="amh-subject-verify-v1", purpose_of_use="x",
            prazo_consentimento_s=31,
        )  # fmt: skip


async def test_regravacao_tem_teto_e_alarme_unico(amb: Ambiente) -> None:
    amb.consent.falhar = True
    d = await amb.verificar_pelo_telefone()
    assert d.novo.regravacoes_falhas == 1 and d.novo.consentimento_pendente_gravacao
    with structlog.testing.capture_logs() as logs:
        for _ in range(ac.MAX_REGRAVACOES + 3):
            d = await amb.msg("oi")
            assert d.prosseguir  # a falha da gravacao nunca nega o acesso
    assert len(amb.consent.chamadas) == ac.MAX_REGRAVACOES  # parou de tentar no teto
    assert d.novo.regravacoes_falhas == ac.MAX_REGRAVACOES and d.novo.consentimento_pendente_gravacao
    alarmes = [e for e in logs if e["event"] == "acesso_consentimento_regravacao_esgotada"]
    assert len(alarmes) == 1 and alarmes[0]["log_level"] == "error" and alarmes[0]["alarme"] is True


async def test_revogacao_pendente_no_teto_escala_uma_vez_e_nao_prende_em_indisponivel(amb: Ambiente) -> None:
    """Re-revisao do #700 (1): com a revogacao pendente o titular recebe o texto FIXO de "recebida, em
    processamento" (nunca INDISPONIVEL), os agentes seguem bloqueados, e no TETO de regravacoes abre-se UM
    escalonamento humano com o motivo operacional `revogacao_consentimento_pendente`."""
    await amb.verificar_pelo_telefone()
    amb.consent.falhar = True
    revogou = await amb.msg("REVOGAR")
    revogado_em = revogou.novo.revogado_em
    escalonamentos = []
    for texto in ("ACEITO", "oi", "ACEITO", "quero ajuda", "ACEITO", "oi", "ACEITO"):
        amb.relogio.avancar(minutes=7)
        d = await amb.msg(texto)
        assert d.respostas == (ac.REVOGACAO_EM_PROCESSAMENTO,) and not d.prosseguir
        assert d.novo.estado == ac.REVOGADO and d.novo.revogacao_pendente
        if d.escalar:
            escalonamentos.append(d.motivo_escalonamento)
    assert escalonamentos == [ac.ESCALONAMENTO_REVOGACAO_PENDENTE]
    revogacoes = [c for c in amb.consent.chamadas if c["decision"] == "revoked"]
    assert len(revogacoes) == ac.MAX_REGRAVACOES
    # Toda regravacao com o `revogado_em` ORIGINAL (a AMH #212 aceita decided_at no passado).
    assert {c["decided_at"] for c in revogacoes} == {"2026-10-08T12:00:00Z"} and revogado_em is not None
    assert len({c["idempotency_key"] for c in revogacoes}) == 1


async def test_revogar_de_novo_com_pendencia_mantem_o_revogado_em_original(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    amb.consent.falhar = True
    original = (await amb.msg("REVOGAR")).novo.revogado_em
    amb.relogio.avancar(hours=1)
    d = await amb.msg("REVOGAR")
    assert d.novo.revogado_em == original and d.novo.revogacao_pendente and d.esquecer_memoria
    assert {c["decided_at"] for c in amb.consent.chamadas if c["decision"] == "revoked"} == {
        "2026-10-08T12:00:00Z"
    }


async def test_runbook_atendente_zera_o_contador_e_a_proxima_mensagem_conclui(amb: Ambiente) -> None:
    """O passo do runbook: com a AMH de volta, o atendente zera `regravacoes_falhas`; a proxima mensagem do
    titular regrava `revoked` com o `revogado_em` original e a conversa volta ao consentimento normal."""
    from dataclasses import replace

    await amb.verificar_pelo_telefone()
    amb.consent.falhar = True
    await amb.msg("REVOGAR")
    for _ in range(ac.MAX_REGRAVACOES):
        await amb.msg("oi")
    [linha] = amb.store._linhas.values()
    assert linha.regravacoes_falhas == ac.MAX_REGRAVACOES
    amb.consent.falhar = False
    amb.relogio.avancar(days=2)
    await amb.store.gravar(replace(linha, regravacoes_falhas=0))  # UPDATE ... SET regravacoes_falhas = 0
    d = await amb.msg("ACEITO")
    assert not d.novo.revogacao_pendente and d.novo.estado == ac.AGUARDANDO_CPF
    assert amb.consent.chamadas[-1]["decision"] == "revoked"
    assert amb.consent.chamadas[-1]["decided_at"] == "2026-10-08T12:00:00Z"


async def test_sucesso_na_regravacao_zera_o_contador(amb: Ambiente) -> None:
    amb.consent.falhar = True
    await amb.verificar_pelo_telefone()
    await amb.msg("oi")
    amb.consent.falhar = False
    d = await amb.msg("oi")
    assert not d.novo.consentimento_pendente_gravacao and d.novo.regravacoes_falhas == 0


# --------------------------------------------------------------------------- re-revisao (2): janela de 24 h
async def test_janela_de_24h_nao_e_zerada_por_sucesso_em_telefone_compartilhado(amb: Ambiente) -> None:
    """REVOGAR -> ACEITO -> 2 erros -> acerto PROPRIO -> repete: o acerto zera as tentativas seguidas, mas
    NAO a janela; na 7a falha da janela, bloqueio ate' o fim dela + atendente."""
    amb.verif.cadastrar_cpf(CPF, REF)
    await amb.ate_cpf()
    assert (await amb.msg(CPF)).novo.estado == ac.VERIFICADO
    for ciclo in range(3):
        await amb.msg("REVOGAR")
        await amb.msg("ACEITO")
        for _ in range(2):
            d = await amb.msg(OUTRO_CPF)
            assert d.novo.estado == ac.AGUARDANDO_CPF and not d.escalar, ciclo
        d = await amb.msg(CPF)  # o proprio acerto
        assert d.novo.estado == ac.VERIFICADO and d.novo.tentativas == 0
        assert d.novo.falhas_janela == 2 * (ciclo + 1)
        amb.relogio.avancar(minutes=30)
    await amb.msg("REVOGAR")
    await amb.msg("ACEITO")
    d = await amb.msg(OUTRO_CPF)
    assert d.escalar and d.novo.estado == ac.BLOQUEADO_HUMANO and d.respostas == (ac.BLOQUEADO,)
    assert d.novo.bloqueado_ate == d.novo.janela_inicio + ac.JANELA_FALHAS  # ate' o FIM da janela
    assert d.novo.falhas_janela == ac.MAX_FALHAS_JANELA + 1
    assert (await amb.msg(CPF)).respostas == (ac.BLOQUEADO_CURTO,)


async def test_janela_vencida_recomeca_a_contagem(amb: Ambiente) -> None:
    amb.verif.cadastrar_cpf(CPF, REF)
    await amb.ate_cpf()
    await amb.msg(OUTRO_CPF)
    await amb.msg(OUTRO_CPF)
    assert (await amb.msg(CPF)).novo.falhas_janela == 2
    amb.relogio.avancar(hours=25)
    await amb.msg("oi")  # verificacao vencida: volta ao CPF
    d = await amb.msg(OUTRO_CPF)
    assert d.novo.falhas_janela == 1 and d.novo.janela_inicio == amb.relogio.agora


async def test_tres_falhas_seguidas_continuam_bloqueando_dentro_da_janela(amb: Ambiente) -> None:
    await amb.ate_cpf()
    for _ in range(2):
        assert not (await amb.msg("123")).escalar
    d = await amb.msg("123")
    assert d.escalar and d.novo.estado == ac.BLOQUEADO_HUMANO and d.novo.falhas_janela == 3
