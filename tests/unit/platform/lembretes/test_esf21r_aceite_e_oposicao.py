"""E-SF-21r (E37, J68): criterio de selecao pelo aceite do termo (finalidade 2), oposicao e envio fechado.

- O criterio de selecao do lembrete e' o aceite do termo de contato com a finalidade 2 (cobranca), lido
  ANTES de qualquer fato de cobranca. Aceite ausente, revogado, sem a finalidade 2, sem o canal WhatsApp ou
  fonte fora do ar: ninguem e' selecionado e a cobranca nem e' lida.
- A oposicao pelo PARAR e' executavel so' com o gancho de entrada ligado e o store respondendo; revogar o
  aceite tem efeito na rodada seguinte.
- J68: o lembrete so' sai antes do vencimento e nunca e' notificacao de inadimplencia.
- Envio fechado: em producao nao ha' fonte de aceite, nem porta de oposicao, e o gate recusa.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from maezo.platform.lembretes.aceite import (
    FINALIDADE_COBRANCA,
    AceiteDeContato,
    permissao_do_aceite,
)
from maezo.platform.lembretes.config import ConfigJob
from maezo.platform.lembretes.job import Beneficiario, executar
from maezo.platform.lembretes.optout import OposicaoPeloParar, tratar_parar
from maezo.platform.lembretes.regras import escolher_competencia
from maezo.platform.lembretes.texto import CORPO_TEMPLATE_META, compor_lembrete
from tests.support.lembrete_fakes import (
    AGORA,
    CONV_A,
    TELEFONE_A,
    Cenario,
    FakeStore,
    allowlist,
    benef,
    competencia,
    contato,
    fatos,
)
from tests.unit.esf13_apoio import (
    SHA_ACEITE_SINTETICO,
    VERSAO_ACEITE_SINTETICO,
    OposicaoBeneficiarioSintetica,
    gate_ratificado,
)

REAL = ConfigJob(habilitado=True, dry_run=False, allowlist=allowlist(CONV_A), exigir_allowlist=True)


def _aceite(**kw: object) -> AceiteDeContato:
    base: dict[str, object] = {
        "prova_ref": "prova-sintetica-1",
        "texto_versao": VERSAO_ACEITE_SINTETICO,
        "texto_sha256": SHA_ACEITE_SINTETICO,
        "finalidades": frozenset({FINALIDADE_COBRANCA}),
        "canais": frozenset({"whatsapp", "email"}),
    }
    base.update(kw)
    return AceiteDeContato(**base)  # type: ignore[arg-type]


class _Aceites:
    """Fonte SINTETICA de aceites (nao existe em producao)."""

    def __init__(self, aceite: AceiteDeContato | None, *, falha: bool = False) -> None:
        self.aceite = aceite
        self.falha = falha
        self.lidos: list[str] = []

    async def de(self, beneficiario: Beneficiario) -> AceiteDeContato | None:
        self.lidos.append(beneficiario.conversation_id)
        if self.falha:
            raise RuntimeError("fonte de aceite fora")
        return self.aceite


def _cenario(aceites: _Aceites | None, cfg: ConfigJob = REAL) -> Cenario:
    c = Cenario(
        cfg=cfg,
        beneficiarios=[benef(CONV_A)],
        fatos_por_conversa={CONV_A: fatos()},
        contatos={CONV_A: contato(TELEFONE_A)},
        outbound=gate_ratificado(),
    )
    c.deps.oposicao_beneficiario = OposicaoBeneficiarioSintetica()
    c.deps.aceites = aceites
    return c


# --- criterio de selecao (E37) -------------------------------------------------------------


@pytest.mark.asyncio
async def test_criterio_de_selecao_e_o_aceite_da_finalidade_2() -> None:
    aceites = _Aceites(_aceite())
    c = _cenario(aceites)
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert resumo.contadores["enviado_texto"] == 1 and c.cliente.total == 1
    assert aceites.lidos == [CONV_A]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "aceites",
    [
        None,
        _Aceites(None),
        _Aceites(_aceite(revogado=True)),
        _Aceites(_aceite(finalidades=frozenset({7}))),
        _Aceites(_aceite(canais=frozenset({"email"}))),
        _Aceites(_aceite(prova_ref="")),
        _Aceites(_aceite(), falha=True),
    ],
    ids=[
        "sem-fonte(producao)",
        "sem-aceite",
        "revogado",
        "sem-finalidade-2",
        "sem-whatsapp",
        "sem-prova",
        "fonte-fora",
    ],
)
async def test_sem_aceite_da_finalidade_2_ninguem_e_selecionado(aceites: _Aceites | None) -> None:
    c = _cenario(aceites)
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert resumo.contadores["sem_aceite_finalidade_cobranca"] == 1
    assert c.cliente.total == 0 and c.store.reservas == {}
    # A cobranca da pessoa nem e' lida sem o aceite.
    assert c.fatos.consultadas == []


def test_a_permissao_do_aceite_e_a_de_contato_proativo() -> None:
    p = permissao_do_aceite(_aceite())
    assert p is not None
    assert (p.origem, p.escopo, p.canal) == ("aceite_contato", "contato_proativo", "whatsapp")
    assert p.texto_versao == VERSAO_ACEITE_SINTETICO and p.prova_ref == "prova-sintetica-1"


@pytest.mark.asyncio
async def test_revogacao_do_aceite_tem_efeito_na_rodada_seguinte() -> None:
    aceites = _Aceites(_aceite())
    c = _cenario(aceites, cfg=replace(REAL, dry_run=True))
    assert (await executar(c.deps, c.cfg, agora=AGORA)).contadores["enviaria"] == 1
    aceites.aceite = replace(aceites.aceite, revogado=True)  # type: ignore[arg-type]
    seguinte = await executar(c.deps, c.cfg, agora=AGORA)
    assert seguinte.contadores["sem_aceite_finalidade_cobranca"] == 1
    assert "enviaria" not in seguinte.contadores


# --- oposicao pelo PARAR --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oposicao_pelo_parar_so_e_executavel_com_o_gancho_ligado() -> None:
    store = FakeStore()
    assert await OposicaoPeloParar(store).executavel() is False
    assert await OposicaoPeloParar(store, gancho_de_entrada_ligado=True).executavel() is True


@pytest.mark.asyncio
async def test_oposicao_pelo_parar_com_store_fora_do_ar_recusa_a_rodada() -> None:
    class _StoreFora(FakeStore):
        async def optouts(self) -> frozenset[str]:
            raise RuntimeError("store fora")

    c = _cenario(_Aceites(_aceite()))
    c.deps.oposicao_beneficiario = OposicaoPeloParar(_StoreFora(), gancho_de_entrada_ligado=True)
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert resumo.contadores == {"recusado_BENEFICIARY_OPPOSITION_NOT_EXECUTABLE": 1}
    assert c.cliente.total == 0


@pytest.mark.asyncio
async def test_parar_tem_efeito_imediato_sobre_quem_tem_aceite() -> None:
    c = _cenario(_Aceites(_aceite()))
    c.deps.oposicao_beneficiario = OposicaoPeloParar(c.store, gancho_de_entrada_ligado=True)
    assert await tratar_parar("PARAR", conversation_id=CONV_A, store=c.store, agora=AGORA) is not None
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert resumo.contadores["optout"] == 1
    assert c.cliente.total == 0


# --- J68: so' antes do vencimento, nunca inadimplencia ----------------------------------------


@pytest.mark.parametrize(
    "c",
    [
        competencia(vencimento="2026-10-12"),  # ja' venceu (hoje = 2026-10-13)
        competencia(situacao="vencida", vencimento="2026-10-10"),
        competencia(situacao="paga"),
        competencia(situacao="cancelada"),
    ],
    ids=["vencimento-passado", "vencida", "paga", "cancelada"],
)
def test_lembrete_so_sai_antes_do_vencimento(c: object) -> None:
    from datetime import date

    escolhida, motivo = escolher_competencia(fatos(c), hoje=date(2026, 10, 13), dias_antecedencia=3)  # type: ignore[arg-type]
    assert escolhida is None and motivo == "sem_competencia_na_janela"


def test_lembrete_nunca_e_notificacao_de_inadimplencia() -> None:
    from datetime import date

    mensagem = compor_lembrete(competencia="2026-10", valor_total="320.25", vencimento=date(2026, 10, 16))
    textos = " ".join([CORPO_TEMPLATE_META, mensagem.texto, *mensagem.parametros_template]).casefold()
    for termo in ("inadimpl", "atraso", "atrasad", "vencida", "débito", "debito", "suspens", "cancelamento"):
        assert termo not in textos, termo
