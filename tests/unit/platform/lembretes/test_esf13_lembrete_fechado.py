"""E-SF-13 + ADDENDUM-01 §7 (q) / RT-D4 — o lembrete proativo de cobranca esta' FECHADO por construcao.

1. A flag nasce desligada (e o dry-run ligado): afirmado aqui.
2. Mesmo com a flag ligada e envio REAL, o gate de PRODUCAO (o default de `Dependencias`) recusa com
   PURPOSE_NOT_RATIFIED antes de qualquer leitura: zero envio, zero leitura de acesso/cobranca/supressao.
3. O consentimento do acesso (`atendimento_whatsapp` / `wa-consent-v2`), que e' o que `leitura.py`
   seleciona, NUNCA habilita o envio: mesmo com um registro SINTETICO que ratifica tudo, a permissao que o
   job apresenta e' recusada `recontact_permission_unqualified`.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from maezo.gateway.amh_interop import CONSENT_SCOPE
from maezo.gateway.communications.outbound_gate import verificar_permissao
from maezo.platform.lembretes import composicao
from maezo.platform.lembretes.config import ConfigJob, LembreteSettings
from maezo.platform.lembretes.job import (
    ESCOPO_CONSENTIMENTO_ATENDIMENTO,
    FINALIDADE_LEMBRETE,
    executar,
    permissao_do_acesso,
    simular,
)
from tests.support.lembrete_fakes import AGORA, CONV_A, TELEFONE_A, Cenario, allowlist, benef, contato, fatos
from tests.unit.esf13_apoio import (
    OposicaoBeneficiarioSintetica,
    aceite_os05_sintetico,
    gate_ratificado,
    registro_ratificado,
)

REAL = ConfigJob(habilitado=True, dry_run=False, allowlist=allowlist(CONV_A), exigir_allowlist=True)


def _cenario(cfg: ConfigJob = REAL, *, oposicao: object = "sintetica", **kw: object) -> Cenario:
    """`oposicao="sintetica"` abre SO' a trava CD-25 para provar a camada seguinte (o gate); `None` e' o
    estado de producao."""
    base: dict[str, object] = {
        "beneficiarios": [benef(CONV_A)],
        "fatos_por_conversa": {CONV_A: fatos()},
        "contatos": {CONV_A: contato(TELEFONE_A)},
    }
    base.update(kw)
    c = Cenario(cfg=cfg, **base)  # type: ignore[arg-type]
    if oposicao == "sintetica":
        c.deps.oposicao_beneficiario = OposicaoBeneficiarioSintetica()
    else:
        c.deps.oposicao_beneficiario = oposicao  # type: ignore[assignment]
    return c


TRAVA = "recusado_BENEFICIARY_OPPOSITION_NOT_EXECUTABLE"


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    "porta",
    [None, OposicaoBeneficiarioSintetica(executavel=False), OposicaoBeneficiarioSintetica(falha=True)],
    ids=["sem-porta(producao)", "porta-nao-executavel", "porta-fora-do-ar"],
)
async def test_sem_oposicao_executavel_do_beneficiario_a_rodada_inteira_e_recusada(
    dry_run: bool, porta: object
) -> None:
    """CD-25 (GT-4, saida): flag LIGADA, gate SINTETICO ratificado e aceite sintetico — tudo o mais aberto —
    e mesmo assim a rodada e o simulador recusam, sem ler acesso, cobranca, supressao nem opt-out, e sem
    enviar."""
    c = _cenario(
        replace(REAL, dry_run=dry_run),
        oposicao=porta,
        outbound=gate_ratificado(),
        permissao_de=lambda _b: aceite_os05_sintetico(),
    )
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert resumo.contadores == {TRAVA: 1}
    sim = await simular(c.deps, c.cfg, conversation_id=CONV_A, agora=AGORA)
    assert sim.contadores == {TRAVA: 1}
    assert c.cliente.total == 0 and c.gate.embrulhados == 0 and c.store.reservas == {}
    assert c.acesso.chamadas == 0 and c.fatos.consultadas == [] and c.store.chamadas == 0


async def test_com_tudo_aberto_e_porta_sintetica_o_mecanismo_envia() -> None:
    """Controle positivo da trava: so' a porta (sintetica) muda e o mesmo cenario passa a enviar — a trava e'
    a causa da recusa acima, nao outra coisa."""
    c = _cenario(outbound=gate_ratificado(), permissao_de=lambda _b: aceite_os05_sintetico())
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert resumo.contadores["enviado_texto"] == 1 and c.cliente.total == 1


def test_nenhuma_porta_de_oposicao_do_beneficiario_existe_em_src() -> None:
    """GAP/DPO: o canal de oposicao do beneficiario nao e' decidido aqui. Nenhuma classe em `src/` implementa
    `executavel` e a composicao de producao nao preenche `oposicao_beneficiario`."""
    raiz = Path(composicao.__file__).resolve().parents[2]
    implementacoes = [
        arquivo
        for arquivo in raiz.rglob("*.py")
        for no in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8")))
        if isinstance(no, ast.AsyncFunctionDef | ast.FunctionDef)
        and no.name == "executavel"
        and arquivo.name != "job.py"
    ]
    assert implementacoes == []


def test_flag_do_lembrete_nasce_desligada_e_em_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in ("MAEZO_LEMBRETE_COBRANCA", "MAEZO_LEMBRETE_DRY_RUN", "MAEZO_LEMBRETE_EXIGIR_ALLOWLIST"):
        monkeypatch.delenv(nome, raising=False)
    s = LembreteSettings()
    assert s.habilitado is False and s.dry_run is True and s.exigir_allowlist is True
    cfg = ConfigJob()
    assert cfg.habilitado is False and cfg.dry_run is True


@pytest.mark.parametrize("dry_run", [False, True])
async def test_gate_de_producao_recusa_o_lembrete_antes_de_ler_qualquer_fonte(dry_run: bool) -> None:
    c = _cenario(replace(REAL, dry_run=dry_run))  # sem `outbound`: o gate de PRODUCAO
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert resumo.contadores == {"recusado_outbound_PURPOSE_NOT_RATIFIED": 1}
    assert c.cliente.total == 0 and c.gate.embrulhados == 0
    assert c.acesso.chamadas == 0 and c.fatos.consultadas == [] and c.store.reservas == {}


async def test_simulador_tambem_e_recusado_pelo_gate_de_producao() -> None:
    c = _cenario()
    resumo = await simular(c.deps, c.cfg, conversation_id=CONV_A, agora=AGORA)
    assert resumo.contadores == {"recusado_outbound_PURPOSE_NOT_RATIFIED": 1}
    assert c.cliente.total == 0 and c.store.chamadas == 0


async def test_wa_consent_v2_nunca_habilita_o_lembrete_nem_com_tudo_ratificado() -> None:
    """RT-D4: o registro SINTETICO abre finalidade, template, oposicao e taxonomia; a permissao que o job
    apresenta (a do acesso, `permissao_do_acesso`) continua recusada. Zero envio, zero reserva."""
    c = _cenario(outbound=gate_ratificado())  # `permissao_de` fica o default do job
    resumo = await executar(c.deps, c.cfg, agora=AGORA)
    assert resumo.contadores["recusado_outbound_recontact_permission_unqualified"] == 1
    assert "enviaria" not in resumo.contadores and not any(k.startswith("enviado") for k in resumo.contadores)
    assert c.cliente.total == 0 and c.store.reservas == {}
    sim = await simular(c.deps, c.cfg, conversation_id=CONV_A, agora=AGORA)
    assert sim.contadores["recusado_outbound_recontact_permission_unqualified"] == 1
    assert c.cliente.total == 0


def test_a_permissao_do_acesso_e_o_consentimento_de_atendimento() -> None:
    p = permissao_do_acesso(benef(CONV_A))
    assert ESCOPO_CONSENTIMENTO_ATENDIMENTO == CONSENT_SCOPE == p.escopo
    assert p.texto_versao.startswith("wa-consent")
    assert verificar_permissao(p, finalidade=FINALIDADE_LEMBRETE, registro=registro_ratificado()) is not None


def test_a_composicao_de_producao_nao_injeta_gate_nem_permissao() -> None:
    """A raiz de producao (`composicao.montar`) usa os DEFAULTS: o gate do registro publicado e a permissao
    do acesso. Injetar outro gate ali seria abrir o envio por codigo, nao por ratificacao."""
    arvore = ast.parse(inspect.getsource(composicao))
    chamadas = [
        n
        for n in ast.walk(arvore)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Dependencias"
    ]
    assert len(chamadas) == 1
    assert {k.arg for k in chamadas[0].keywords}.isdisjoint(
        {"outbound", "permissao_de", "oposicao_beneficiario"}
    )
    assert not Path(composicao.__file__).read_text(encoding="utf-8").count("esf13_apoio")


async def test_em_producao_a_trava_cd25_vem_antes_do_gate() -> None:
    """Estado real de producao (gate de producao + sem porta de oposicao): o motivo contado e' a trava CD-25,
    nao o fechamento do gate. Fixa a POSICAO da trava antes do gate."""
    c = _cenario(oposicao=None)
    assert (await executar(c.deps, c.cfg, agora=AGORA)).contadores == {TRAVA: 1}
    assert (await simular(c.deps, c.cfg, conversation_id=CONV_A, agora=AGORA)).contadores == {TRAVA: 1}
